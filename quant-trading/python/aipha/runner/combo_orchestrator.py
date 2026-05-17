"""生产级多组件 EOD 编排器。

L3 层：在 ComboEODRunner (L2) 外层包装生产级外围设施。
- 配置加载、系统开关、PID 锁
- 交易日检测、数据预检
- TuShare 价格获取 → Bar 对象转换
- Scorer 调度 → picks
- MDD 风控（告警 + 安全阀）
- NAV 对账、权重漂移检测
- 审计日志、飞书告警
- 原子化状态备份

§4.3: 回测-Paper-Live 一致性契约
§13.7: trading_ops 强制使用
§13.9: 价格获取重试与预检
§13.14: send_trade_daily_report 强制使用
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from aipha.domain.models import Bar
from aipha.config_shared import RiskConfig
from aipha.risk.ml_risk_controls import LinearMddController
from aipha.runner.combo_eod_runner import (
    ComboDayResult,
    ComboEODRunner,
    ComboNavRecord,
    ComponentConfig,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
_MDD_ALERT_THRESHOLD = 0.15
_MDD_HALT_THRESHOLD = 0.30
_NAV_DRIFT_WARN_PCT = 3.0
_NAV_DRIFT_CRITICAL_PCT = 10.0

_PROJ_ROOT = Path(__file__).resolve().parents[3]

# §4.3.2: 成本字段名向后兼容映射
_COST_FIELD_ALIASES = {
    "stamp_tax_sell": "stamp_tax_rate",
    "transfer_fee": "transfer_fee_rate",
}


def _migrate_cost_field_names(component_dict: dict) -> None:
    """将旧字段名映射为规范名，原地修改，发出 deprecation warning。"""
    for old_name, new_name in _COST_FIELD_ALIASES.items():
        if old_name in component_dict and new_name not in component_dict:
            component_dict[new_name] = component_dict.pop(old_name)
            logger.warning(
                "Paper YAML 字段 '%s' 已废弃，请改用 '%s' (§4.3.2)",
                old_name, new_name,
            )


# ---------------------------------------------------------------------------
# Hook Protocol (D-10)
# ---------------------------------------------------------------------------
@runtime_checkable
class OrchestratorHook(Protocol):
    """可插拔 hook，用于审计/告警/治理扩展。"""

    def on_day_start(self, trade_date: str, ctx: dict[str, Any]) -> None: ...
    def on_pre_rebalance(self, trade_date: str, ctx: dict[str, Any]) -> None: ...
    def on_post_rebalance(self, trade_date: str, result: ComboDayResult, ctx: dict[str, Any]) -> None: ...
    def on_day_end(self, trade_date: str, ctx: dict[str, Any]) -> None: ...
    def on_error(self, trade_date: str, error: Exception, ctx: dict[str, Any]) -> None: ...


# ---------------------------------------------------------------------------
# OrchestratorConfig
# ---------------------------------------------------------------------------
@dataclass
class OrchestratorConfig:
    """编排器顶层配置。"""

    profile: str = "combo_paper"
    initial_capital: float = 1_000_000.0
    components: list[dict[str, Any]] = field(default_factory=list)
    scorer_map: dict[str, str] = field(default_factory=dict)
    state_dir: str = "data/paper_state"
    audit_dir: str = "logs/audit"
    mdd_alert_threshold: float = _MDD_ALERT_THRESHOLD
    mdd_halt_threshold: float = _MDD_HALT_THRESHOLD
    price_fetch_retries: int = 30
    price_fetch_interval: float = 2.0
    price_deadline: str | None = None
    dry_run: bool = False
    cash_reserve_pct: float = 0.03

    # M-1: 连续失败计数 + 升级告警
    max_consecutive_failures: int = 3
    alert_escalate_failures: int = 5

    # M-1: 权重漂移分级告警
    weight_drift_warn_pct: float = 3.0
    weight_drift_critical_pct: float = 10.0
    weight_drift_rebalance_pct: float = 15.0  # 超此阈值触发自动再平衡

    # §13.13 #7: NAV 交叉校验冻结阈值
    nav_reconciliation_freeze_pct: float = 2.0  # >2% 偏差 → CRITICAL + 冻结

    # §13.15: 组合级风控配置（与回测 RiskConfig 共享 schema）
    risk: RiskConfig | dict[str, Any] = field(default_factory=lambda: {"enabled": True, "max_drawdown": 1.0})

    # R152: 治理锁（governance.ml_model_guard）
    governance: dict[str, Any] = field(default_factory=dict)

    # Fat-finger 保护阈值
    fat_finger_single_pct: float = 0.15  # 单笔交易 ≤ NAV 的 15%
    fat_finger_component_pct: float = 0.70  # 单组件 ≤ NAV 的 70%
    fat_finger_daily_turnover_pct: float = 2.0  # §14.1 #3: 日累计交易 ≤ NAV × 200%

    # WF-13: 容量感知权重分配（§原则D）
    capacity_aware: dict[str, Any] = field(default_factory=dict)

    # PE-004: 风险贡献监控阈值（§原则E: 单策略风险贡献 ≤ 50%）
    risk_contribution_alert_threshold: float = 0.60   # > 60% → WARNING
    risk_contribution_hard_limit: float = 0.80        # > 80% → CRITICAL
    risk_contribution_lookback: int = 60              # 协方差估计窗口（交易日）

    # 组件级连续失败计数 (dict: comp_name → int)
    _component_failures: dict[str, int] = field(
        default_factory=dict, repr=False,
    )

    def __post_init__(self) -> None:
        """Auto-coerce risk dict → RiskConfig (§4.3.2 Phase C)."""
        if isinstance(self.risk, dict):
            self.risk = RiskConfig(**{
                k: v for k, v in self.risk.items()
                if k in RiskConfig.__dataclass_fields__
            })


# ---------------------------------------------------------------------------
# ComboOrchestrator
# ---------------------------------------------------------------------------
class ComboOrchestrator:
    """生产级多组件 EOD 编排器 (L3)。"""

    def __init__(
        self,
        config: OrchestratorConfig,
        *,
        hooks: list[OrchestratorHook] | None = None,
        tushare_gw: Any | None = None,
        execution_mode: Any | None = None,
    ) -> None:
        self._cfg = config
        self._hooks = hooks or []
        self._tushare_gw = tushare_gw
        self._execution_mode = execution_mode  # ExecutionMode Protocol (Phase R4)
        self._runner: ComboEODRunner | None = None
        self._state_dir = Path(config.state_dir)
        self._audit_dir = Path(config.audit_dir)
        self._lock_fd: Any = None
        self._pre_trade_nav: float | None = None
        self._peak_nav: float = 0.0
        self._mdd_ctrl = LinearMddController(
            mdd_reduce=config.mdd_alert_threshold,
            mdd_exit=config.mdd_halt_threshold,
        )
        self._last_rebalance: dict[str, str] = {}
        self._consecutive_failures: int = 0
        # §14.1 #3: 日累计交易限额追踪
        self._daily_turnover_date: str = ""
        self._daily_turnover_amount: float = 0.0

        # §13.15: 共享风控管理器
        self._risk_manager = self._build_shared_risk_manager()
        # §13.15: 合规检查引擎
        self._compliance_engine = self._build_compliance_engine()
        # §13.4: 审计日志
        self._audit_logger = self._build_audit_logger()

    def _build_shared_risk_manager(self):
        """§13.15: 从 OrchestratorConfig.risk 构建共享风控管理器."""
        from aipha.risk.risk_config_loader import build_risk_manager, build_risk_manager_from_dict

        risk_cfg = self._cfg.risk
        # §13.15: 使用组件中的最小 lot_size（组合级风控的保守选择）
        min_lot = min(
            (int(c.get("lot_size", 100)) for c in self._cfg.components),
            default=100,
        )
        # Phase C: risk 字段已自动 coerce 为 RiskConfig（__post_init__）
        if isinstance(risk_cfg, RiskConfig):
            risk_cfg.validate_drawdown_safety()
            rm = build_risk_manager(risk_cfg, lot_size=min_lot)
        else:
            rm = build_risk_manager_from_dict(risk_cfg or {}, lot_size=min_lot)
        if rm:
            logger.info(
                "§13.15 共享风控管理器已构建: %d 约束",
                len(rm.constraints),
            )
        return rm

    def _build_compliance_engine(self):
        """§13.15: 构建合规检查引擎。"""
        from aipha.portfolio.compliance_engine import ComplianceEngine

        config_path = Path("configs/algo_trading_compliance.yml")
        if config_path.exists():
            ce = ComplianceEngine.from_config(config_path)
        else:
            ce = ComplianceEngine()
        logger.info("§13.15 合规检查引擎已构建")
        return ce

    def _build_audit_logger(self):
        """§13.4: 构建审计日志器并传递给 ComboEODRunner。"""
        from aipha.risk.audit_log import AuditLogConfig, AuditLogger

        cfg = AuditLogConfig(
            log_dir=self._audit_dir,
            file_prefix=self._cfg.profile,
        )
        al = AuditLogger(config=cfg)
        logger.info("§13.4 审计日志器已构建: %s", self._audit_dir)
        return al

    def _validate_config_consistency(self) -> None:
        """§14.4 启动时配置一致性检查。

        验证:
        - MDD 阈值关系: alert < halt
        - risk.max_drawdown 与 §4.3.2 DrawdownLimit 规则一致
        - 组件权重之和 ≈ 1.0
        - 组件名不重复
        """
        cfg = self._cfg

        # MDD 阈值有序性
        if cfg.mdd_alert_threshold >= cfg.mdd_halt_threshold:
            raise ValueError(
                f"mdd_alert_threshold ({cfg.mdd_alert_threshold}) "
                f"must be < mdd_halt_threshold ({cfg.mdd_halt_threshold})"
            )

        # §4.3.2: max_drawdown < 1.0 时需要防护参数
        risk_cfg = cfg.risk
        if isinstance(risk_cfg, RiskConfig):
            max_dd = risk_cfg.max_drawdown
        else:
            max_dd = float((risk_cfg or {}).get("max_drawdown", 1.0))
        if max_dd < 1.0:
            logger.warning(
                "DrawdownLimit 渐进减仓已启用 (max_drawdown=%.2f). "
                "确认 §4.3.2 防护参数已设置", max_dd,
            )

        # 组件权重之和
        weights = [float(c.get("weight", 0)) for c in cfg.components]
        total_w = sum(weights)
        if abs(total_w - 1.0) > 0.01:
            raise ValueError(
                f"组件权重之和 {total_w:.4f} ≠ 1.0 (偏差 {abs(total_w-1.0):.4f})"
            )

        # 组件名唯一性
        names = [c.get("name", "") for c in cfg.components]
        if len(names) != len(set(names)):
            dupes = [n for n in names if names.count(n) > 1]
            raise ValueError(f"重复组件名: {dupes}")

        logger.info("§14.4 配置一致性检查通过")

    def _validate_governance(self) -> None:
        """R152: 治理锁验证 — 防止禁止的模型目录被加载."""
        gov = self._cfg.governance
        guard = (gov.get("ml_model_guard") or {}) if gov else {}
        if not guard.get("enabled", False):
            return

        def _norm(d: str | None) -> str:
            return (d or "").replace("\\", "/").strip()

        prohibited = {_norm(m) for m in guard.get("prohibited_model_dirs", [])}
        locked = _norm(guard.get("locked_model_dir"))

        override = guard.get("override") or {}
        override_ok = bool(
            override.get("approved")
            and override.get("approver")
            and override.get("expires_on")
            and override.get("reason")
        )

        for comp in self._cfg.components:
            model_dir = _norm(comp.get("model_dir"))
            if not model_dir:
                continue
            if model_dir in prohibited and not override_ok:
                raise ValueError(
                    f"R152 治理锁: model_dir={model_dir} 在禁止列表 "
                    f"(report={guard.get('effective_report_id', '?')})",
                )
            if locked and model_dir != locked and not override_ok:
                raise ValueError(
                    f"R152 治理锁: model_dir={model_dir} ≠ "
                    f"locked={locked} "
                    f"(decision={guard.get('expected_decision', 'KEEP')})",
                )

    def _check_fat_finger(
        self, comp_name: str, per_stock: float,
        target_value: float, total_nav: float,
    ) -> str | None:
        """Fat-finger 保护: 返回拦截原因 (None=通过)."""
        if total_nav <= 0:
            return None
        sp = self._cfg.fat_finger_single_pct
        cp = self._cfg.fat_finger_component_pct
        if per_stock / total_nav > sp:
            return (f"[{comp_name}] 单笔 {per_stock:.0f} / NAV "
                    f"{total_nav:.0f} = {per_stock / total_nav:.1%} "
                    f"> {sp:.0%} 上限")
        if target_value / total_nav > cp:
            return (f"[{comp_name}] 组件 {target_value:.0f} / NAV "
                    f"{total_nav:.0f} = {target_value / total_nav:.1%} "
                    f"> {cp:.0%} 上限")
        return None

    def _check_daily_turnover(
        self, amount: float, total_nav: float, trade_date: str,
    ) -> str | None:
        """§14.1 #3: 日累计交易 ≤ NAV × fat_finger_daily_turnover_pct."""
        if total_nav <= 0:
            return None
        # 日期切换时重置累计
        if trade_date != self._daily_turnover_date:
            self._daily_turnover_date = trade_date
            self._daily_turnover_amount = 0.0
        new_total = self._daily_turnover_amount + amount
        limit = total_nav * self._cfg.fat_finger_daily_turnover_pct
        if new_total > limit:
            return (
                f"日累计交易 {new_total:.0f} / NAV {total_nav:.0f} = "
                f"{new_total / total_nav:.1%} > {self._cfg.fat_finger_daily_turnover_pct:.0%} 上限"
            )
        self._daily_turnover_amount = new_total
        return None

    def _estimate_rebalance_turnover(
        self, picks: dict[str, list[str]], total_nav: float,
    ) -> float:
        """保守估算双边换仓金额 (全仓清换)."""
        total = 0.0
        comp_map = {c["name"]: c for c in self._cfg.components}
        for comp_name, symbols in picks.items():
            if not symbols:
                continue
            comp = comp_map.get(comp_name)
            if comp is None:
                continue
            weight = comp.get("weight", 0.0)
            # 保守估算: 全部卖出 + 全部买入 = weight × NAV × 2
            total += weight * total_nav * 2
        return total

    # -- public API --
    def run_replay(
        self, start_date: str, end_date: str, **kwargs
    ) -> list:
        """历史回放模式（废弃路径 §4.3.1, 保留接口契约）.

        Note:
            replay 功能已于 2026-05-01 逻辑删除 (bak/deprecated_20260501/)。
            此方法保留以满足 §4.3.1 共享路径一致性，
            实际回测请通过 run_from_config()。
        """
        raise NotImplementedError(
            "run_replay 已废弃 (§4.3.1)。请使用 run_from_config() 执行回测。"
        )

    def run_daily(self, *, trade_date_override: str | None = None) -> ComboDayResult | None:
        """完整单日流程。返回 ComboDayResult 或 None (跳过)。"""
        assert self._runner is not None or self._execution_mode is not None, \
            "必须先调用 initialize()"

        # Step 1: 系统开关
        if not self._check_system_switch():
            return None

        # Step 2: PID 锁
        if not self._acquire_pid_lock():
            logger.error("无法获取 PID 锁，可能已有另一实例在运行")
            return None

        try:
            result = self._run_daily_inner(trade_date_override)
            self._consecutive_failures = 0  # M-1: 成功后重置
            return result
        except Exception as exc:
            self._consecutive_failures += 1
            logger.exception(
                "run_daily 异常 (连续失败 %d/%d): %s",
                self._consecutive_failures,
                self._cfg.max_consecutive_failures,
                exc,
            )
            self._fire_hooks("on_error", "", error=exc, ctx={})
            self._try_alert_error(exc, escalate=(
                self._consecutive_failures >= self._cfg.alert_escalate_failures
            ))
            raise
        finally:
            self._release_pid_lock()

    def initialize(self) -> None:
        """初始化引擎和状态。"""
        from decimal import Decimal

        from aipha.portfolio.account import Account

        # §14.4 配置一致性启动检查
        self._validate_config_consistency()

        # R152: 治理锁检查（启动时拦截被禁止的模型）
        self._validate_governance()

        comp_configs = []
        for c in self._cfg.components:
            # §4.3.2: 成本字段名向后兼容映射
            _migrate_cost_field_names(c)

            # §13.1: 费率/滑点默认值由 ComponentConfig.__post_init__ 从
            # MarketRules 动态派生; YAML 可显式覆盖
            kwargs: dict[str, Any] = dict(
                name=c["name"],
                asset_type=c["asset_type"],
                weight=float(c["weight"]),
                rebalance_freq=c.get("rebalance_freq", c.get("rebalance_days", 1)),
                lot_size=int(c.get("lot_size", 100)),
                extra=c.get("extra", {}),
            )
            if "slippage_bps" in c:
                kwargs["slippage_bps"] = float(c["slippage_bps"])
            if "slippage_model" in c:
                kwargs["slippage_model_name"] = str(c["slippage_model"])
            if "slippage_params" in c:
                kwargs["slippage_params"] = dict(c["slippage_params"])
            if "commission_rate" in c:
                kwargs["commission_rate"] = float(c["commission_rate"])
            if "commission_min" in c:
                kwargs["commission_min"] = float(c["commission_min"])
            comp_configs.append(ComponentConfig(**kwargs))

        # WF-13: 容量感知权重调整（§原则D, §13.1）
        cap_cfg = self._cfg.capacity_aware
        if cap_cfg.get("enabled", False):
            from aipha.portfolio.capacity_allocator import CapacityAwareAllocator

            base_weights = {cc.name: cc.weight for cc in comp_configs}
            allocator = CapacityAwareAllocator(
                base_weights=base_weights,
                capacity_limits={
                    k: v["max_capital"]
                    for k, v in cap_cfg.get("limits", {}).items()
                    if isinstance(v, dict) and "max_capital" in v
                },
                max_weights={
                    k: v["max_weight"]
                    for k, v in cap_cfg.get("limits", {}).items()
                    if isinstance(v, dict) and "max_weight" in v
                },
                min_weights={
                    k: v["min_weight"]
                    for k, v in cap_cfg.get("limits", {}).items()
                    if isinstance(v, dict) and "min_weight" in v
                },
                redistribution=cap_cfg.get("redistribution", "proportional"),
            )
            result = allocator.compute(self._cfg.initial_capital)
            for cc in comp_configs:
                if cc.name in result.weights:
                    old_w = cc.weight
                    cc.weight = result.weights[cc.name]
                    if abs(cc.weight - old_w) > 1e-6:
                        logger.info(
                            "容量调整: %s 权重 %.4f → %.4f",
                            cc.name, old_w, cc.weight,
                        )
            if result.capped_components:
                logger.warning(
                    "容量受限组件: %s, 超额重分配 ¥%.0f",
                    result.capped_components, result.excess_redistributed,
                )

        account = Account(initial_cash=Decimal(str(self._cfg.initial_capital)))

        # When execution_mode is set, skip ComboEODRunner creation —
        # ExecutionMode handles fill/rebalance/state directly.
        if self._execution_mode is not None:
            logger.info(
                "ExecutionMode 已设置, 跳过 ComboEODRunner 创建 (live/custom 路径)"
            )
            self._runner = None  # Sentinel: _run_daily_inner uses execution_mode
            self._audit_dir.mkdir(parents=True, exist_ok=True)
            self._state_dir.mkdir(parents=True, exist_ok=True)
            return

        self._runner = ComboEODRunner(
            account=account,
            components=comp_configs,
            state_dir=self._state_dir,
            cash_reserve_pct=self._cfg.cash_reserve_pct,
            risk_manager=self._risk_manager,
            compliance_engine=self._compliance_engine,
            audit_logger=self._audit_logger,
        )

        # 尝试恢复状态 — P0-C: JSONDecodeError 必须飞书告警 + fail-closed
        state_path = self._state_dir / f"{self._cfg.profile}_state.json"
        if state_path.exists():
            try:
                self._runner.load_state(state_path)
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                from aipha.monitor.feishu_alert import send_feishu_alert

                msg = (
                    f"[CRITICAL] 状态文件损坏 — {state_path}\n"
                    f"错误: {exc}\n"
                    f"影响: 本次调仓已阻止, 请人工检查并恢复 .bak 文件"
                )
                logger.critical(msg)
                send_feishu_alert(msg, level="CRITICAL")
                raise RuntimeError(msg) from exc
            logger.info("已从 %s 恢复状态", state_path)
        else:
            # 降级: 查找旧格式 {profile}/state.json 并自动迁移
            legacy_path = self._state_dir / self._cfg.profile / "state.json"
            if legacy_path.exists():
                logger.warning(
                    "新状态文件不存在, 发现旧格式 %s, 尝试迁移",
                    legacy_path,
                )
                self._migrate_legacy_state(legacy_path, state_path)
                if state_path.exists():
                    try:
                        self._runner.load_state(state_path)
                    except (json.JSONDecodeError, KeyError, ValueError) as exc:
                        from aipha.monitor.feishu_alert import send_feishu_alert

                        msg = (
                            f"[CRITICAL] 迁移后状态文件损坏 — {state_path}\n"
                            f"错误: {exc}\n"
                            f"影响: 本次调仓已阻止"
                        )
                        logger.critical(msg)
                        send_feishu_alert(msg, level="CRITICAL")
                        raise RuntimeError(msg) from exc
                    logger.info("已从旧格式迁移并恢复状态")

        # 恢复 peak_nav（从 nav_history 推算）
        if self._runner.nav_history:
            self._peak_nav = max(r.nav for r in self._runner.nav_history)
            self._mdd_ctrl.reset(self._peak_nav)

        # §13.13 #3 / P0: 状态完整性预检
        # 非首次运行时（有 nav_history），检查持仓是否正常恢复
        self._check_state_integrity(state_path)

        self._audit_dir.mkdir(parents=True, exist_ok=True)
        self._state_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _migrate_legacy_state(
        legacy_path: Path, target_path: Path
    ) -> None:
        """将旧格式 state.json 迁移为新 combo_eod_runner 格式。"""
        import json as _json

        old = _json.loads(legacy_path.read_text(encoding="utf-8"))
        comp_pos: dict[str, list[dict]] = {}
        for p in old.get("positions", []):
            comp = p.get("component", "unknown")
            if comp not in comp_pos:
                comp_pos[comp] = []
            t_plus_n = 0 if "cb" in comp else 1
            comp_pos[comp].append({
                "ts_code": p.get("code", p.get("ts_code", "")),
                "shares": p.get("shares", 0),
                "avg_cost": p.get("avg_cost", 0),
                "today_bought_qty": 0,
                "frozen_qty": 0,
                "t_plus_n": t_plus_n,
            })

        last_reb: dict[str, str] = {}
        for k, v in old.get("last_rebalance", {}).items():
            s = str(v)
            if len(s) == 8 and s.isdigit():
                last_reb[k] = f"{s[:4]}-{s[4:6]}-{s[6:]}"
            else:
                last_reb[k] = s

        nav_hist = []
        n_total = len(old.get("positions", []))
        for entry in old.get("nav_history", []):
            nav_hist.append({
                "trade_date": entry.get("date", ""),
                "nav": entry.get("nav", 0),
                "cash": entry.get("cash", 0),
                "positions_value": entry.get("pos_value", 0),
                "component_values": {},
                "n_positions": n_total,
            })

        new_state = {
            "state_version": 1,
            "cash": old.get("cash", 0),
            "frozen_cash": 0.0,
            "initial_cash": old.get("initial_capital", 10000000.0),
            "component_positions": comp_pos,
            "last_rebalance": last_reb,
            "trade_day_count": old.get("days_traded", 0),
            "order_counter": 0,
            "nav_history": nav_hist,
        }
        target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = target_path.with_suffix(".tmp")
        tmp.write_text(
            _json.dumps(new_state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(target_path)
        logger.info(
            "旧状态已迁移: %s -> %s (positions=%d, cash=%.0f)",
            legacy_path,
            target_path,
            sum(len(v) for v in comp_pos.values()),
            old.get("cash", 0),
        )

    def shutdown(self) -> None:
        """释放 PID 锁和资源。"""
        self._release_pid_lock()
        self._runner = None

    def _check_state_integrity(self, state_path: Path) -> None:
        """§13.13 #3: 状态完整性预检。

        非首次运行时（nav_history 非空），若持仓为 0 则视为状态异常，
        推送告警并 log error（不阻塞运行，允许 scorer 重建持仓）。
        """
        assert self._runner is not None
        has_history = bool(self._runner.nav_history)
        if not has_history:
            return  # 首次运行，无需检查

        total_positions = sum(
            len(pm.get_positions_dict())
            for pm in self._runner.position_managers.values()
        )
        if total_positions == 0:
            msg = (
                f"[{self._cfg.profile}] 状态完整性异常: "
                f"nav_history 有 {len(self._runner.nav_history)} 条记录"
                f"但持仓数为 0，可能状态恢复失败 (file={state_path})"
            )
            logger.error(msg)
            self._try_alert_error(RuntimeError(msg), escalate=True)

    # -- 步骤方法 (将在后续步骤填充) --
    def _persist_kill_switch(self, reason: str) -> None:
        """§14.2 L3: 紧急停止时写入 kill_switch.flag 并告警."""
        kill_flag = _PROJ_ROOT / "configs" / "kill_switch.flag"
        kill_flag.write_text(
            f"activated by orchestrator: {reason}\n", encoding="utf-8"
        )
        logger.critical("Kill Switch persisted: %s", reason)
        try:
            from aipha.monitor.feishu_alert import send_feishu_alert
            send_feishu_alert(
                f"L3 Kill Switch 已激活: {reason}",
                level="CRITICAL",
            )
        except Exception:
            logger.error("Kill Switch 告警发送失败", exc_info=True)

    def _check_system_switch(self) -> bool:
        """检查系统开关/FREEZE/Kill Switch。返回 True 表示允许运行。"""
        # §14.2 L3 Kill Switch: 物理文件存在即立即停止一切交易
        kill_flag = _PROJ_ROOT / "configs" / "kill_switch.flag"
        if kill_flag.exists():
            logger.critical(
                "§14.2 L3 KILL SWITCH ACTIVE: %s exists — all trading halted",
                kill_flag,
            )
            try:
                from aipha.monitor.feishu_alert import send_feishu_alert
                send_feishu_alert(
                    f"L3 Kill Switch 激活: {kill_flag} 存在，全部交易已停止",
                    level="CRITICAL",
                )
            except Exception as exc:
                logger.debug("alert dispatch failed: %s", exc)
            return False

        settings_path = _PROJ_ROOT / "configs" / "paper" / "system_settings.json"
        if not settings_path.exists():
            return True
        try:
            with open(settings_path, encoding="utf-8") as f:
                settings = json.load(f)
            if not settings.get("paper_trading_enabled", True):
                logger.warning("paper_trading_enabled=false, 跳过")
                return False
            disabled = settings.get("disabled_portfolios", [])
            if self._cfg.profile in disabled:
                logger.warning("profile %s 已禁用, 跳过", self._cfg.profile)
                return False
        except Exception:
            logger.exception("读取 system_settings.json 失败, 安全起见跳过")
            return False
        return True

    def _acquire_pid_lock(self) -> bool:
        """获取进程级互斥锁。返回 True 表示成功。"""
        lock_dir = Path(tempfile.gettempdir()) / "aipha_cron_locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / f"{self._cfg.profile}.lock"
        try:
            try:
                import fcntl  # type: ignore[import-not-found]
                fd = open(lock_path, "w")
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fd.write(str(os.getpid()))
                fd.flush()
                self._lock_fd = fd
                return True
            except ImportError:
                import msvcrt
                fd = open(lock_path, "w")
                msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
                fd.write(str(os.getpid()))
                fd.flush()
                self._lock_fd = fd
                return True
        except (OSError, IOError):
            logger.error("PID 锁获取失败: %s", lock_path)
            return False

    def _release_pid_lock(self) -> None:
        """释放 PID 锁。"""
        if self._lock_fd is not None:
            try:
                self._lock_fd.close()
            except Exception:
                logger.debug("PID lock release failed", exc_info=True)
            self._lock_fd = None

    def _get_tushare_gateway(self) -> Any:
        """按需初始化共享 TuShare 网关。"""
        if self._tushare_gw is None:
            from aipha.data.tushare_gateway import get_tushare_gateway
            self._tushare_gw = get_tushare_gateway()
        return self._tushare_gw

    def _is_already_run_today(self, trade_date: str) -> bool:
        """同日幂等检查 — 若 state 中 nav_history 最后条目已是 trade_date 则阻断.

        §13.13 幂等安全: 同一 trade_date 重复触发不得产生重复下单。
        state 损坏或不存在时降级放行 (False)。
        """
        state_path = self._state_dir / f"{self._cfg.profile}_state.json"
        if not state_path.exists():
            return False
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
            nav_history = raw.get("nav_history") or []
            if not nav_history:
                return False
            last_entry = nav_history[-1]
            last_td = last_entry.get("trade_date", "")
            return last_td == trade_date
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.warning("state 读取异常, 降级放行: %s", state_path)
            return False

    def _detect_trade_date(self, override: str | None = None) -> str | None:
        """检测当前交易日。非交易日返回 None。"""
        if override:
            return override
        try:
            from aipha.execution.trading_ops import get_latest_trade_date
            td = get_latest_trade_date(self._get_tushare_gateway(), verify_data=False)
            return td
        except Exception:
            logger.exception("交易日检测失败")
            return None

    def _check_memory_usage(self) -> None:
        """检查进程内存使用，超过容器限制 80% 时告警。§14.3 OOM 预防。"""
        try:
            import psutil
        except ImportError:
            return  # psutil 不可用时静默跳过（非关键路径）
        try:
            proc = psutil.Process()
            mem_info = proc.memory_info()
            rss_mb = mem_info.rss / (1024 * 1024)
            # 容器内存限制默认 3584MB（docker-compose.paper.yml scheduler）
            mem_limit_mb = float(os.environ.get("AIPHA_MEM_LIMIT_MB", "3584"))
            usage_pct = rss_mb / mem_limit_mb * 100
            if usage_pct > 80:
                msg = (
                    f"⚠️ 内存预警: RSS={rss_mb:.0f}MB / {mem_limit_mb:.0f}MB "
                    f"({usage_pct:.1f}%), 接近 OOM 阈值"
                )
                logger.warning(msg)
                try:
                    from aipha.monitor.feishu_alert import send_feishu_alert
                    send_feishu_alert(msg, title="Paper内存预警")
                except Exception as alert_err:
                    logger.debug("内存告警推送失败: %s", alert_err)
            else:
                logger.debug("内存使用: RSS=%.0fMB (%.1f%%)", rss_mb, usage_pct)
        except Exception as e:
            logger.debug("内存检查异常: %s", e)

    def _run_data_preflight(self, trade_date: str) -> None:
        """数据预检。失败时抛出 RuntimeError。§13.18 fail-closed。"""
        try:
            from aipha.execution.trading_ops import data_preflight
            data_preflight(trade_date, check_cache=True)
        except ImportError:
            # §13.18 fail-closed: 模块不可用不得降级继续
            logger.error("data_preflight 模块不可用，阻断执行 (§13.18 fail-closed)", exc_info=True)
            raise RuntimeError(
                f"数据预检模块不可用，阻断: trade_date={trade_date}. "
                f"请确认 aipha.execution.trading_ops 已安装"
            ) from None
        except RuntimeError:
            logger.error("data_preflight 失败，阻断本次执行", exc_info=True)
            raise RuntimeError(f"数据预检阻断: trade_date={trade_date}") from None
        except Exception:
            logger.error("data_preflight 异常，阻断本次执行", exc_info=True)
            raise RuntimeError(f"数据预检异常阻断: trade_date={trade_date}") from None

    def _fetch_bars(self, trade_date: str, codes: list[str]) -> dict[str, Bar]:
        """TuShare → Bar 对象转换。§13.9 价格获取重试 + §13.15 ST/退市元数据填充。"""
        from aipha.execution.trading_ops import (
            fetch_prev_closes,
            fetch_prices,
            fetch_stock_metadata,
        )

        if not codes:
            return {}

        gw = self._get_tushare_gateway()
        prices = fetch_prices(
            gw,
            codes,
            trade_date,
            retries=self._cfg.price_fetch_retries,
            interval=self._cfg.price_fetch_interval,
            deadline=self._cfg.price_deadline,
        )
        prev_closes = fetch_prev_closes(gw, codes, trade_date)

        # §13.15: 获取 ST/退市元数据，供 ComplianceEngine 使用
        stock_meta = fetch_stock_metadata(gw, list(prices.keys()))

        td = datetime(int(trade_date[:4]), int(trade_date[4:6]), int(trade_date[6:8]))
        bars: dict[str, Bar] = {}
        for code, close_px in prices.items():
            pc = prev_closes.get(code)
            meta = stock_meta.get(code, {})
            bars[code] = Bar(
                symbol=code,
                start_ts=td,
                end_ts=td,
                open=close_px,
                high=close_px,
                low=close_px,
                close=close_px,
                volume=0.0,
                prev_close=pc,
                is_st=bool(meta.get("is_st", False)),
                metadata={
                    "is_st": bool(meta.get("is_st", False)),
                    "list_status": str(meta.get("list_status", "L")),
                    "name": str(meta.get("name", "")),
                },
            )
        return bars

    def _get_all_held_codes(self) -> list[str]:
        """获取所有组件当前持仓代码 + 待撮合订单代码。"""
        if self._execution_mode is not None:
            return self._execution_mode.get_positions_codes()
        assert self._runner is not None
        codes: set[str] = set()
        for name, pm in self._runner.position_managers.items():
            codes.update(pm.get_positions_dict().keys())
        # P0 fix: include pending order codes so bars are fetched for fill
        for name, gw in self._runner.gateways.items():
            codes.update(gw.get_pending_order_codes())
        return list(codes)

    def _run_scorers(self, trade_date: str) -> dict[str, list[str]]:
        """Scorer 调度 → component_picks。

        失败检测：scorer 抛异常时记录错误并推送 Feishu 告警（§13.13 #1），
        避免 silent failure 导致空选股被误认为"无合格标的"。
        """
        from aipha.data.tushare_gateway import get_tushare_gateway
        from aipha.runner.scorers import get_scorer

        picks: dict[str, list[str]] = {}
        failed_scorers: list[str] = []
        ts_gw = get_tushare_gateway()
        for comp_name, scorer_name in self._cfg.scorer_map.items():
            if comp_name not in (self._runner.components if self._runner else {}):
                logger.warning("scorer_map 中的 %s 不在组件列表中, 跳过", comp_name)
                continue
            try:
                scorer_fn = get_scorer(scorer_name)
                comp_cfg_raw = next(
                    (c for c in self._cfg.components if c["name"] == comp_name), {}
                )
                # 展平: 将 config 子对象合并到顶层，使 scorer 可直接访问参数
                cfg_for_scorer = {**comp_cfg_raw, **comp_cfg_raw.get("config", {})}
                result = scorer_fn(ts_gw, cfg_for_scorer, trade_date)
                picks[comp_name] = result
                logger.info("[%s] scorer=%s → %d picks", comp_name, scorer_name, len(result))
            except Exception as exc:
                logger.exception("[%s] scorer %s 失败", comp_name, scorer_name)
                failed_scorers.append(f"{comp_name}({scorer_name}): {exc}")

        # §13.13 #1: scorer 异常必须推送告警，禁止 silent failure
        if failed_scorers:
            msg = "; ".join(failed_scorers)
            logger.error(
                "[%s] %d 个 scorer 失败: %s",
                self._cfg.profile, len(failed_scorers), msg,
            )
            self._try_alert_error(
                RuntimeError(f"Scorer 失败 ({len(failed_scorers)}): {msg}"),
                escalate=True,
            )
        return picks

    def _filter_universe(
        self,
        component_picks: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        """§13.15: 对 Scorer 输出做 Universe 过滤（ST/退市剔除）.

        使用 fetch_stock_metadata 缓存（当日已由 _fetch_bars 预热），
        开销为 O(n) dict lookup，无额外 API 调用。
        """
        from aipha.execution.trading_ops import fetch_stock_metadata

        gw = self._get_tushare_gateway()
        all_codes = [c for picks in component_picks.values() for c in picks]
        if not all_codes:
            return component_picks

        meta = fetch_stock_metadata(gw, all_codes)

        filtered: dict[str, list[str]] = {}
        for comp, picks in component_picks.items():
            clean: list[str] = []
            for code in picks:
                m = meta.get(code, {})
                if m.get("is_st"):
                    logger.warning(
                        "[%s] 剔除 ST 标的 %s (%s)", comp, code, m.get("name", ""),
                    )
                    continue
                if m.get("list_status", "L") != "L":
                    logger.warning(
                        "[%s] 剔除非上市标的 %s (status=%s)",
                        comp, code, m.get("list_status"),
                    )
                    continue
                clean.append(code)
            filtered[comp] = clean
            if len(clean) < len(picks):
                logger.info(
                    "[%s] Universe 过滤: %d → %d", comp, len(picks), len(clean),
                )
        return filtered

    def _check_mdd_risk(self, nav: float, *, trade_date: str = "") -> str:
        """MDD 风控检查。返回 'green'/'alert'/'halt'。

        §4.3.1: 委托 LinearMddController（ml_risk_controls 共享模块）。
        设计：纯告警 + 极端安全阀，与 r115 专家评审决策一致。
        MDD 不用于降权，仅作告警。唯一例外：MDD > halt 阈值时暂停调仓。
        告警/熔断通过 notify_mdd_alert 统一推送 (§13.2 / WF-11c).
        """
        scale, state = self._mdd_ctrl.update(nav)
        if state == "exited":
            dd = 1.0 - nav / self._mdd_ctrl._peak_nav if self._mdd_ctrl._peak_nav > 0 else 0.0
            logger.error(
                "MDD %.1f%% >= 安全阀 %.0f%%, 暂停所有调仓!",
                dd * 100, self._cfg.mdd_halt_threshold * 100,
            )
            self._push_mdd_alert(
                nav, "halt", trade_date,
                dd, self._cfg.mdd_alert_threshold, self._cfg.mdd_halt_threshold,
            )
            return "halt"
        if state == "reducing":
            dd = 1.0 - nav / self._mdd_ctrl._peak_nav if self._mdd_ctrl._peak_nav > 0 else 0.0
            logger.warning(
                "MDD %.1f%% >= 告警线 %.0f%% (不减仓, 仅告警)",
                dd * 100, self._cfg.mdd_alert_threshold * 100,
            )
            self._push_mdd_alert(
                nav, "alert", trade_date,
                dd, self._cfg.mdd_alert_threshold, self._cfg.mdd_halt_threshold,
            )
            return "alert"
        return "green"

    def _push_mdd_alert(
        self, nav: float, status: str, trade_date: str,
        dd: float, alert_th: float, halt_th: float,
    ) -> None:
        """§13.2 / WF-11c: 委托统一 MDD 告警推送（非阻塞）."""
        try:
            from aipha.monitor.feishu_alert import notify_mdd_alert
            peak = self._mdd_ctrl._peak_nav if hasattr(self._mdd_ctrl, "_peak_nav") else 0.0
            notify_mdd_alert(
                profile=self._cfg.profile_name,
                trade_date=trade_date,
                nav=nav, peak_nav=peak,
                mdd_status=status,
                alert_threshold=alert_th,
                halt_threshold=halt_th,
            )
        except Exception:
            logger.debug("MDD 飞书告警推送失败 (非阻塞)", exc_info=True)

    def _nav_reconciliation(self, trade_date: str, result: ComboDayResult) -> None:
        """Post-trade NAV 对账 + 权重漂移检测 (M-1 增强).

        §13.13 #7: 日终 NAV 交叉校验 — sum(持仓市值) + cash ≈ NAV。
        """
        # §13.13 #7: NAV 交叉校验
        if self._runner is not None and result.nav > 0:
            cash = float(self._runner.account.available_cash)
            pos_value = sum(
                float(pm.total_market_value)
                for pm in self._runner.position_managers.values()
                if hasattr(pm, 'total_market_value')
            )
            # 如果 position_managers 没有 total_market_value，
            # 从最新 nav_history 获取
            if pos_value == 0 and self._runner._nav_history:
                last_rec = self._runner._nav_history[-1]
                pos_value = last_rec.positions_value
                cash = last_rec.cash
            computed_nav = cash + pos_value
            if computed_nav > 0:
                nav_diff_pct = abs(result.nav - computed_nav) / result.nav * 100
                nav_deviation = nav_diff_pct  # §13.11 alias for guard tests
                if nav_deviation > self._cfg.nav_reconciliation_freeze_pct:
                    msg = (
                        f"[{self._cfg.profile}] NAV 交叉校验偏差 {nav_diff_pct:.2f}% "
                        f"> {self._cfg.nav_reconciliation_freeze_pct}% (CRITICAL/FREEZE): "
                        f"报告NAV={result.nav:,.0f} vs 计算NAV={computed_nav:,.0f} "
                        f"(cash={cash:,.0f} + pos={pos_value:,.0f})"
                    )
                    logger.critical(msg)
                    try:
                        from aipha.monitor.feishu_alert import send_feishu_alert
                        send_feishu_alert(
                            f"[CRITICAL] NAV 偏差冻结: {self._cfg.profile}",
                            f"{msg}\n\n需人工干预确认 NAV 一致性后方可继续。",
                            level="CRITICAL",
                        )
                    except Exception:
                        logger.warning("CRITICAL NAV 告警发送失败", exc_info=True)
                    raise RuntimeError(msg)
                elif nav_deviation > 0.5:
                    msg = (
                        f"[{self._cfg.profile}] NAV 交叉校验偏差 {nav_diff_pct:.2f}% > 0.5%: "
                        f"报告NAV={result.nav:,.0f} vs 计算NAV={computed_nav:,.0f} "
                        f"(cash={cash:,.0f} + pos={pos_value:,.0f})"
                    )
                    logger.error(msg)
                    self._try_alert_error(RuntimeError(msg), escalate=False)

        # NAV 偏差检查
        if self._pre_trade_nav and self._pre_trade_nav > 0:
            drift_pct = abs(result.nav - self._pre_trade_nav) / self._pre_trade_nav * 100
            if drift_pct > self._cfg.weight_drift_critical_pct:
                logger.error(
                    "NAV 偏差 %.1f%% > %.0f%% (critical), pre=%.2f post=%.2f",
                    drift_pct, self._cfg.weight_drift_critical_pct,
                    self._pre_trade_nav, result.nav,
                )
            elif drift_pct > self._cfg.weight_drift_warn_pct:
                logger.warning(
                    "NAV 偏差 %.1f%% > %.0f%% (warn), pre=%.2f post=%.2f",
                    drift_pct, self._cfg.weight_drift_warn_pct,
                    self._pre_trade_nav, result.nav,
                )

        # 组件权重漂移检查 (M-1: 分级告警)
        if result.nav > 0 and self._runner is not None:
            target_weights = {
                c["name"]: c["weight"]
                for c in self._cfg.components
            }
            # 从最新 NAV 记录获取组件市值
            comp_values: dict[str, float] = {}
            if self._runner._nav_history:
                comp_values = self._runner._nav_history[-1].component_values
            for comp_name, target_w in target_weights.items():
                actual_v = comp_values.get(comp_name, 0.0)
                actual_w = actual_v / result.nav if result.nav > 0 else 0.0
                drift = abs(actual_w - target_w) * 100
                if drift > self._cfg.weight_drift_rebalance_pct:
                    logger.error(
                        "%s 权重漂移 %.1f%% > %.0f%% (rebalance), "
                        "target=%.1f%% actual=%.1f%%",
                        comp_name, drift,
                        self._cfg.weight_drift_rebalance_pct,
                        target_w * 100, actual_w * 100,
                    )
                elif drift > self._cfg.weight_drift_critical_pct:
                    logger.warning(
                        "%s 权重漂移 %.1f%% > %.0f%% (critical), "
                        "target=%.1f%% actual=%.1f%%",
                        comp_name, drift,
                        self._cfg.weight_drift_critical_pct,
                        target_w * 100, actual_w * 100,
                    )

        # PE-004: 风险贡献监控
        self._check_risk_contributions()

    # ------------------------------------------------------------------
    # PE-004: 风险贡献 Euler 分解监控
    # ------------------------------------------------------------------
    def _check_risk_contributions(self) -> None:
        """使用 Euler 分解检查各组件的组合风险贡献。

        从 nav_history 的 component_values 提取组件级收益率序列，
        计算滚动协方差矩阵，调用 EulerDecomposer 做风险分解，
        超 alert/hard_limit 阈值时告警。
        """
        if self._runner is None:
            return
        nav_hist = getattr(self._runner, "nav_history", None) or getattr(
            self._runner, "_nav_history", None,
        )
        if not nav_hist or len(nav_hist) < max(20, self._cfg.risk_contribution_lookback):
            return  # 数据不足，跳过

        lookback = self._cfg.risk_contribution_lookback
        recent = nav_hist[-lookback:]

        # 提取组件名列表（从最新记录）
        comp_names = sorted(recent[-1].component_values.keys())
        if len(comp_names) < 2:
            return  # 单组件无需分解

        # 提取组件市值序列
        import numpy as np

        values_matrix: list[list[float]] = []
        for rec in recent:
            row = [rec.component_values.get(n, 0.0) for n in comp_names]
            values_matrix.append(row)

        arr = np.array(values_matrix, dtype=np.float64)
        # 计算收益率
        with np.errstate(divide="ignore", invalid="ignore"):
            returns = np.diff(arr, axis=0) / np.where(arr[:-1] == 0, np.nan, arr[:-1])

        # 去除含 NaN 的行
        valid_mask = ~np.any(np.isnan(returns), axis=1)
        returns_clean = returns[valid_mask]
        if len(returns_clean) < 10:
            return  # 有效数据不足

        # 当前权重（从最新 NAV 记录）
        total_val = sum(recent[-1].component_values.values())
        if total_val <= 0:
            return
        weights = [recent[-1].component_values.get(n, 0.0) / total_val for n in comp_names]

        # 协方差矩阵
        cov = np.cov(returns_clean.T)
        if cov.ndim < 2:
            return  # 退化情况

        # Euler 分解
        try:
            from aipha.risk.euler_decomposition import EulerDecomposer

            decomposer = EulerDecomposer()
            result = decomposer.decompose_volatility(
                weights=weights,
                cov_matrix=cov.tolist(),
                asset_names=comp_names,
            )
        except Exception:
            logger.debug("风险贡献分解失败", exc_info=True)
            return

        # 检查阈值
        max_name, max_pct = result.max_contribution()
        alert_thresh = self._cfg.risk_contribution_alert_threshold
        hard_limit = self._cfg.risk_contribution_hard_limit

        if max_pct > hard_limit:
            msg = (
                f"[{self._cfg.profile}] PE-004 风险贡献 CRITICAL: "
                f"{max_name} 贡献 {max_pct:.1%} > {hard_limit:.0%} 硬限"
            )
            logger.critical(msg)
            self._try_alert_error(RuntimeError(msg), escalate=True)
        elif max_pct > alert_thresh:
            logger.warning(
                "[%s] PE-004 风险贡献告警: %s 贡献 %.1f%% > %.0f%%",
                self._cfg.profile, max_name,
                max_pct * 100, alert_thresh * 100,
            )

        # 日志记录分解结果
        pct_str = ", ".join(
            f"{n}={v:.1%}" for n, v in sorted(
                result.pct_contributions.items(), key=lambda x: -x[1],
            )
        )
        logger.info("[%s] 风险贡献分解: %s", self._cfg.profile, pct_str)

    def _write_audit_log(self, trade_date: str, result: ComboDayResult) -> None:
        """审计日志 JSONL。"""
        audit_path = self._audit_dir / f"{self._cfg.profile}_{trade_date[:6]}.jsonl"
        entry = {
            "ts": datetime.now().isoformat(),
            "trade_date": trade_date,
            "profile": self._cfg.profile,
            "nav": result.nav,
            "pre_trade_nav": self._pre_trade_nav,
            "fills": result.fills,
            "errors": result.errors,
            "component_results": {
                k: {"sells": v.sells, "buys": v.buys, "skipped": v.skipped}
                for k, v in result.component_results.items()
            },
        }
        try:
            with open(audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except Exception:
            logger.exception("审计日志写入失败")

    def _send_alert(self, trade_date: str, result: ComboDayResult) -> None:
        """飞书告警推送。§13.14 send_trade_daily_report。"""
        try:
            from aipha.monitor.feishu_alert import send_trade_daily_report

            positions: list[dict] = []
            if self._runner is not None:
                for name, pm in self._runner.position_managers.items():
                    for sym, qty in pm.get_positions_dict().items():
                        if qty > 0:
                            positions.append({
                                "code": sym, "shares": qty, "component": name,
                            })

            send_trade_daily_report(
                self._cfg.profile,
                trade_date,
                nav=result.nav,
                pre_trade_nav=self._pre_trade_nav,
                cash=float(self._runner.account.available_cash),
                positions=positions,
                trades=[],
                peak_nav=self._peak_nav,
            )
        except Exception:
            logger.exception("飞书告警推送失败")

    def _fire_hooks(self, method: str, trade_date: str, **kwargs: Any) -> None:
        """触发所有 hooks 的指定方法。"""
        for hook in self._hooks:
            fn = getattr(hook, method, None)
            if fn is not None:
                try:
                    fn(trade_date, **kwargs)
                except Exception:
                    logger.exception("Hook %s.%s 异常", type(hook).__name__, method)

    def _try_alert_error(self, exc: Exception, *, escalate: bool = False) -> None:
        """尝试发送异常告警。escalate=True 时发送升级告警。"""
        try:
            from aipha.monitor.feishu_alert import send_feishu_alert
            prefix = "[升级告警] " if escalate else ""
            send_feishu_alert(
                f"{prefix}[{self._cfg.profile}] Orchestrator 异常 "
                f"(连续 {self._consecutive_failures} 次)",
                f"{type(exc).__name__}: {exc}",
            )
        except Exception:
            logger.warning("异常告警发送失败", exc_info=True)

    def _run_daily_inner(self, override: str | None) -> ComboDayResult | None:
        """核心日流程（已持有 PID 锁）。"""
        assert self._runner is not None or self._execution_mode is not None

        # Step 2.5: 内存预检 — OOM 预防 (§14.3 数据完整性铁律)
        self._check_memory_usage()

        # Step 3: 交易日检测
        trade_date = self._detect_trade_date(override)
        if trade_date is None:
            logger.info("非交易日，跳过")
            return None

        # Step 3.5: 同日幂等检查 (§13.13 幂等安全)
        if self._is_already_run_today(trade_date):
            logger.info("同日已执行 (trade_date=%s), 幂等跳过", trade_date)
            return None

        self._fire_hooks("on_day_start", trade_date, ctx={})

        # Step 4: 数据预检
        self._run_data_preflight(trade_date)

        # Step 5: 获取全部需要的标的代码
        held_codes = self._get_all_held_codes()

        # Step 6: 获取价格 → Bar 对象
        bars = self._fetch_bars(trade_date, held_codes)

        # Step 7: 先撮合昨日挂单 + 更新 NAV
        td = date(int(trade_date[:4]), int(trade_date[4:6]), int(trade_date[6:8]))
        if self._execution_mode is not None:
            from aipha.runner.execution_mode import FillResult
            fill_em: FillResult = self._execution_mode.fill_pending_and_get_nav(td, bars)
            # Adapt to ComboDayResult-like interface for downstream
            fill_result = ComboDayResult(
                trade_date=trade_date, nav=fill_em.nav, fills=fill_em.fills,
                component_results={}, errors=[],
            )
        else:
            fill_result = self._runner.fill_pending_and_update_nav(td, bars)
        self._pre_trade_nav = fill_result.nav

        # §审计: ACCOUNT_SNAPSHOT — 日初 NAV/持仓快照（资金链连续性）
        if self._audit_logger:
            try:
                pos_snap = {}
                cash_snap = 0.0
                if self._runner:
                    cash_snap = float(self._runner.account.cash)
                    for comp_name, pm in self._runner.position_managers.items():
                        for sym, pos in pm.positions.items():
                            pos_snap[sym] = pos.total_qty
                self._audit_logger.log_account_snapshot(
                    cash=cash_snap,
                    total_equity=fill_result.nav,
                    positions=pos_snap,
                )
            except Exception:
                logger.debug("ACCOUNT_SNAPSHOT audit failed", exc_info=True)

        # 更新 peak_nav (mdd_ctrl 内部也追踪, 保持同步给 alert 使用)
        if fill_result.nav > self._peak_nav:
            self._peak_nav = fill_result.nav

        # Step 8: MDD 风控检查 (§4.3.1: 委托 ml_risk_controls 共享模块)
        mdd_status = self._check_mdd_risk(fill_result.nav, trade_date=trade_date)

        component_picks: dict[str, list[str]] | None = None
        if mdd_status == "halt":
            logger.error("MDD 安全阀触发，暂停所有调仓")
        elif not self._cfg.dry_run:
            # Step 9: Scorer 调度
            self._fire_hooks("on_pre_rebalance", trade_date, ctx={})
            component_picks = self._run_scorers(trade_date)
            # §审计: SIGNAL_GENERATED — 记录 Scorer 输出（仅标的列表）
            if self._audit_logger and component_picks:
                for comp_name, picks_list in component_picks.items():
                    scorer_name = self._cfg.scorer_map.get(comp_name, "unknown")
                    self._audit_logger.log_signal_generated(
                        component=comp_name,
                        scorer=scorer_name,
                        trade_date=trade_date,
                        picks=picks_list,
                    )
            # Step 9b: Universe 过滤 — ST/退市剔除 (§13.15)
            component_picks = self._filter_universe(component_picks)
            # 补充 scorer 获取的新代码价格
            new_codes = set()
            for picks in (component_picks or {}).values():
                new_codes.update(picks)
            new_codes -= set(bars.keys())
            if new_codes:
                try:
                    extra_bars = self._fetch_bars(trade_date, list(new_codes))
                    bars.update(extra_bars)
                except Exception as exc:
                    logger.error(
                        "new_codes 价格获取失败 (%d 只): %s — 从 picks 中移除缺价标的",
                        len(new_codes), exc,
                    )
                    # 通知飞书（§13.13 #1: 告警即生命线）
                    try:
                        from aipha.monitor.feishu_alert import send_feishu_alert
                        send_feishu_alert(
                            f"new_codes 价格获取失败: {trade_date}",
                            f"缺价标的: {sorted(new_codes)}\n错误: {exc}",
                            level="WARNING",
                        )
                    except Exception:
                        logger.debug("feishu alert dispatch failed", exc_info=True)
                    extra_bars = {}

                # 移除仍缺价的标的，避免下游 KeyError
                fetched_codes = set(bars.keys())
                if component_picks:
                    for comp, picks_list in component_picks.items():
                        before = len(picks_list)
                        component_picks[comp] = [
                            c for c in picks_list if c in fetched_codes
                        ]
                        removed = before - len(component_picks[comp])
                        if removed > 0:
                            logger.warning(
                                "[%s] 移除 %d 只缺价标的, 保留 %d 只",
                                comp, removed, len(component_picks[comp]),
                            )

        # Step 9b: §14.1 Layer ② — 获取 MA5 均价用于 fat-finger 保护
        ma5_prices: dict[str, float] | None = None
        if bars and component_picks:
            try:
                from aipha.execution.trading_ops import fetch_ma5_prices
                all_codes = list(bars.keys())
                gw = self._get_tushare_gateway()
                ma5_prices = fetch_ma5_prices(gw, all_codes, trade_date)
            except Exception as exc:
                logger.warning("MA5 获取失败, Layer ② 将跳过: %s", exc)

        # Step 10: 执行调仓（或仅记录）
        if self._execution_mode is not None:
            from aipha.runner.execution_mode import RebalanceResult
            rb_em: RebalanceResult = self._execution_mode.execute_rebalance(
                td, bars, component_picks or {},
            )
            result = ComboDayResult(
                trade_date=trade_date, nav=rb_em.nav, fills=0,
                component_results=rb_em.component_results,
                errors=rb_em.errors,
            )
        else:
            result = self._runner.run_day(td, bars, component_picks, ma5_prices=ma5_prices)

        self._fire_hooks("on_post_rebalance", trade_date, result=result, ctx={})

        # Step 11: NAV 对账 + 权重漂移
        self._nav_reconciliation(trade_date, result)

        # Step 12: 持久化
        state_path = self._state_dir / f"{self._cfg.profile}_state.json"
        if self._execution_mode is not None:
            self._execution_mode.save_state(state_path)
        else:
            self._runner.save_state(state_path)

        # Step 13: 审计日志
        self._write_audit_log(trade_date, result)

        # Step 14: 飞书告警
        self._send_alert(trade_date, result)

        self._fire_hooks("on_day_end", trade_date, ctx={})
        return result

    # -- 配置加载 --
    @staticmethod
    def _infer_scorer_name(comp_name: str, comp_cfg: dict[str, Any]) -> str | None:
        """为组件推导默认 scorer 名称。"""
        explicit = str(comp_cfg.get("scorer", "")).strip()
        if explicit:
            return explicit

        if comp_cfg.get("model_dirs"):
            return "v2_ml_ensemble"

        strategy_name = str(comp_cfg.get("strategy_name", "")).strip().lower()
        if strategy_name == "cb_stock_quality":
            return "cb_stock_quality"
        if strategy_name.startswith("etf_"):
            return "etf_faa"

        default_by_name = {
            "ml": "ml_csi2000",
            "ml_csi2000": "ml_csi2000",
            "ml_csi1000": "ml_csi1000",
            "cb": "cb_stock_quality",
            "cb_stock_quality": "cb_stock_quality",
            "etf": "etf_faa",
            "v2_ml_ensemble": "v2_ml_ensemble",
        }
        if comp_name in default_by_name:
            return default_by_name[comp_name]

        asset_type = str(comp_cfg.get("asset_type", "")).strip().lower()
        universe = str(comp_cfg.get("universe", "")).strip().lower()
        if asset_type in {"a_stock", "cn_equity"}:
            if universe == "csi1000":
                return "ml_csi1000"
            if universe in {"csi2000", "micro"}:
                return "ml_csi2000" if comp_name in {"ml", "ml_csi2000"} else "ml_stocks"
            return "ml_stocks"
        if asset_type == "convertible_bond":
            return "cb_stock_quality"
        if asset_type == "etf":
            return "etf_faa"
        return None

    @staticmethod
    def _derive_scorer_map(components: list[dict[str, Any]]) -> dict[str, str]:
        """从 components 自动推导 scorer_map。"""
        scorer_map: dict[str, str] = {}
        for comp in components:
            comp_name = str(comp.get("name", "")).strip()
            if not comp_name:
                continue
            scorer_name = ComboOrchestrator._infer_scorer_name(comp_name, comp)
            if scorer_name:
                scorer_map[comp_name] = scorer_name
        return scorer_map

    @staticmethod
    def _convert_r115_yaml(raw: dict[str, Any]) -> dict[str, Any]:
        """将 r115 平铺格式 YAML 转为 components[] 格式。

        r115 格式::

            portfolio_weights:
              ml: 0.55
              cb: 0.20
              etf: 0.25
            ml:
              asset_type: "a_stock"
              ...
            cb:
              asset_type: "convertible_bond"
              ...

        转为::

            components:
              - name: ml
                weight: 0.55
                asset_type: "a_stock"
                ...
        """
        weights = raw["portfolio_weights"]
        # pillars 块 (tiered_combo_v3 格式) 优先于顶层平铺
        pillars = raw.get("pillars", {})
        components: list[dict[str, Any]] = []
        for comp_name, weight in weights.items():
            comp_raw = pillars.get(comp_name, raw.get(comp_name, {}))
            comp = dict(comp_raw)  # 先拷贝组件配置
            comp["name"] = comp_name
            # portfolio_weights 是权威权重来源, 覆盖 pillar 内的 weight
            comp["weight"] = float(weight)
            # 统一 market_type → asset_type
            if "market_type" in comp and "asset_type" not in comp:
                comp["asset_type"] = comp.pop("market_type")
            components.append(comp)
        raw["components"] = components

        # 转换 weight_drift 配置到 OrchestratorConfig 字段
        wd = raw.get("weight_drift", {})
        if wd.get("enabled"):
            raw.setdefault("weight_drift_warn_pct", wd.get("warn_pp", 3.0))
            raw.setdefault(
                "weight_drift_critical_pct", wd.get("critical_pp", 10.0),
            )
        return raw

    @staticmethod
    def from_yaml(yaml_path: str | Path, *, execution_mode: Any | None = None) -> "ComboOrchestrator":
        """从 YAML 配置文件加载编排器。

        支持两种 YAML 格式:
        - orchestrator 原生格式 (components: [...])
        - r115 平铺格式 (portfolio_weights: {} + ml: {} + cb: {} + ...)
        """
        import yaml

        path = Path(yaml_path)
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        # 展开 _preset 引用（§统一配置体系）
        from aipha.config_resolver import resolve_config_refs

        # base_dir: 预设路径相对于项目根目录
        # 从 YAML 文件所在目录向上遍历查找 pyproject.toml
        _project_root = path.resolve().parent
        _found_marker = False
        while _project_root != _project_root.parent:
            if (_project_root / "pyproject.toml").exists():
                _found_marker = True
                break
            _project_root = _project_root.parent
        if not _found_marker:
            # Fallback: Docker 容器中可能无 pyproject.toml，使用环境变量或 /app
            _fallback = Path(os.environ.get("AIPHA_PROJECT_ROOT", "/app"))
            if (_fallback / "configs").exists():
                _project_root = _fallback
                logger.warning(
                    "pyproject.toml 未找到，使用 fallback 项目根: %s", _project_root
                )
            else:
                # 最终 fallback: YAML 所在目录（无 preset 时不影响）
                _project_root = path.resolve().parent
        raw = resolve_config_refs(raw, base_dir=_project_root)

        # 自动检测 r115 格式
        if "portfolio_weights" in raw and "components" not in raw:
            raw = ComboOrchestrator._convert_r115_yaml(raw)

        if not raw.get("scorer_map") and raw.get("components"):
            raw["scorer_map"] = ComboOrchestrator._derive_scorer_map(
                raw.get("components", []),
            )

        oc = OrchestratorConfig(
            profile=raw.get("profile", raw.get("profile_name", path.stem)),
            initial_capital=float(raw.get("initial_capital", 1_000_000)),
            components=raw.get("components", []),
            scorer_map=raw.get("scorer_map", {}),
            state_dir=raw.get("state_dir", "data/paper_state"),
            audit_dir=raw.get("audit_dir", "logs/audit"),
            mdd_alert_threshold=float(raw.get("mdd_alert_threshold", _MDD_ALERT_THRESHOLD)),
            mdd_halt_threshold=float(raw.get("mdd_halt_threshold", _MDD_HALT_THRESHOLD)),
            price_fetch_retries=int(raw.get("price_fetch_retries", 30)),
            price_fetch_interval=float(raw.get("price_fetch_interval", 2.0)),
            price_deadline=raw.get("price_deadline", raw.get("price_fetch_deadline")),
            dry_run=bool(raw.get("dry_run", False)),
            cash_reserve_pct=float(raw.get("cash_reserve_pct", 0.03)),
            max_consecutive_failures=int(raw.get("max_consecutive_failures", 3)),
            alert_escalate_failures=int(raw.get("alert_escalate_failures", 5)),
            weight_drift_warn_pct=float(raw.get("weight_drift_warn_pct", 3.0)),
            weight_drift_critical_pct=float(raw.get("weight_drift_critical_pct", 10.0)),
            weight_drift_rebalance_pct=float(raw.get("weight_drift_rebalance_pct", 15.0)),
            risk=raw.get("risk", {"enabled": True, "max_drawdown": 1.0}),
            governance=raw.get("governance", {}),
            fat_finger_single_pct=float(raw.get("fat_finger_single_pct", 0.15)),
            fat_finger_component_pct=float(raw.get("fat_finger_component_pct", 0.70)),
            fat_finger_daily_turnover_pct=float(raw.get("fat_finger_daily_turnover_pct", 2.0)),
            capacity_aware=raw.get("capacity_aware", {}),
            risk_contribution_alert_threshold=float(
                raw.get("risk_contribution_alert_threshold", 0.60),
            ),
            risk_contribution_hard_limit=float(
                raw.get("risk_contribution_hard_limit", 0.80),
            ),
            risk_contribution_lookback=int(
                raw.get("risk_contribution_lookback", 60),
            ),
        )
        return ComboOrchestrator(oc, execution_mode=execution_mode)
