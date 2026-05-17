"""共享日级工具函数 — Paper/Live 引擎公共层.

从 paper_engine_eod.py 提取的共享工具，打破 live→paper 耦合。
Phase R1 of paper_live_unification_refactoring_v2.md.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger("daily_utils")

_PROJ = Path(__file__).resolve().parents[3]  # src/aipha/runner → Aipha-Q
_CN_TZ = ZoneInfo("Asia/Shanghai")
_STATE_ROOT = _PROJ / "data" / "paper_state"
_AUDIT_DIR = _PROJ / "logs" / "audit"
_MAX_BACKUPS = 20
_MAX_NAV_HISTORY = 1000


# ── 导入共享交易层 ──
from aipha.execution.trading_ops import (
    calc_nav as _ops_calc_nav,
)
from aipha.execution.state_manager import save_state_json_atomic
from aipha.risk.ml_risk_controls import MddAlertChecker


# =========================================================================
# §14.4 #9: 盘中部署阻断
# =========================================================================
def is_cn_trading_hours(dt: datetime | None = None) -> bool:
    """判断给定时刻是否在 A 股交易时段 (09:15-15:30, 工作日).

    Args:
        dt: 带时区的 datetime，默认取当前北京时间。

    Returns:
        True 表示正在交易时段，应阻止部署。
    """
    if dt is None:
        dt = datetime.now(_CN_TZ)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=_CN_TZ)
    # 周末不交易
    if dt.weekday() >= 5:
        return False
    t = dt.time()
    from datetime import time as _time
    # 09:15 ~ 15:30 为交易时段
    return _time(9, 15) <= t <= _time(15, 30)


def assert_not_trading_hours(action: str = "deploy") -> None:
    """§14.4 #9: 交易时段阻止部署，违规则抛出 RuntimeError."""
    if is_cn_trading_hours():
        raise RuntimeError(
            f"§14.4 #9 违规: 禁止在交易时段执行 {action} "
            f"(当前北京时间: {datetime.now(_CN_TZ).strftime('%H:%M')})"
        )


# =========================================================================
# Data structures — 统一状态模型
# =========================================================================
@dataclass
class ComboState:
    """组合状态 — 所有 paper/live 组合共用."""

    profile_name: str
    initial_capital: float
    cash: float
    positions: list[dict] = field(default_factory=list)
    nav_history: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    last_rebalance: dict = field(default_factory=dict)
    last_update: str = ""
    days_traded: int = 0
    vol_scale: float = 1.0
    peak_nav: float = 0.0
    state_version: int = 3
    consecutive_failures: dict = field(default_factory=dict)
    pending_orders: list[dict] = field(default_factory=list)


# =========================================================================
# Config loading
# =========================================================================
def load_config(path: str | Path) -> dict:
    """加载 YAML 配置."""
    import yaml

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _state_dir(profile: str) -> Path:
    return _STATE_ROOT / profile


# =========================================================================
# State validation + persistence
# =========================================================================
def _validate_state(state: ComboState) -> None:
    """P0-3: 状态完整性校验."""
    errors: list[str] = []
    if state.cash < -1.0:
        errors.append(f"cash 为负: {state.cash:.2f}")
    for i, p in enumerate(state.positions):
        if p.get("shares", 0) <= 0:
            errors.append(f"positions[{i}] shares<=0: {p}")
        if p.get("avg_cost", 0) <= 0:
            errors.append(f"positions[{i}] avg_cost<=0: {p}")
        if not p.get("code"):
            errors.append(f"positions[{i}] 缺少 code")
    if state.peak_nav < 0:
        errors.append(f"peak_nav 为负: {state.peak_nav}")
    if state.initial_capital <= 0:
        errors.append(f"initial_capital<=0: {state.initial_capital}")
    if errors:
        msg = "; ".join(errors)
        logger.error("State 完整性校验失败: %s", msg)
        _send_alert(
            "[PaperEngine] State 数据异常!",
            f"问题: {msg}\n请立即检查 data/paper_state/",
            level="CRITICAL",
        )
        raise ValueError(f"State 完整性校验失败: {msg}")


def load_state(profile: str, initial_capital: float) -> ComboState:
    """加载或创建状态."""
    fp = _state_dir(profile) / "state.json"
    if fp.exists():
        try:
            raw = json.loads(fp.read_text(encoding="utf-8"))
            state = ComboState(**{
                k: v for k, v in raw.items()
                if k in ComboState.__dataclass_fields__
            })
            if state.peak_nav <= 0 and state.nav_history:
                state.peak_nav = max(
                    h.get("nav", 0) for h in state.nav_history
                )
            _validate_state(state)
            return state
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.error("State 损坏: %s, 尝试备份恢复", exc)
            _send_alert(
                f"[PaperEngine] State 文件损坏: {profile}",
                f"异常: {exc}\n文件: {fp}\n正在尝试从备份恢复...",
                level="CRITICAL",
            )
            backups = sorted(
                _state_dir(profile).glob("state.json.*.bak"),
                reverse=True,
            )
            for bak in backups[:10]:
                try:
                    raw = json.loads(bak.read_text(encoding="utf-8"))
                    state = ComboState(**{
                        k: v for k, v in raw.items()
                        if k in ComboState.__dataclass_fields__
                    })
                    logger.warning("从备份恢复: %s", bak.name)
                    _send_alert(
                        f"[PaperEngine] State 从备份恢复: {profile}",
                        f"主文件损坏, 已从 {bak.name} 恢复。请检查数据完整性。",
                        level="WARNING",
                    )
                    return state
                except Exception:  # silent-pass-exempt: 尝试下一个备份文件
                    continue
            _send_alert(
                f"[PaperEngine] State 损坏且全部备份失败: {profile}",
                f"文件: {fp}\n已尝试 {len(backups)} 个备份均无效。需人工干预！",
                level="CRITICAL",
            )
            raise RuntimeError(f"State 损坏且备份恢复失败: {fp}")
    return ComboState(
        profile_name=profile,
        initial_capital=initial_capital,
        cash=initial_capital,
        peak_nav=initial_capital,
    )


def save_state(state: ComboState) -> None:
    """原子化保存状态."""
    d = _state_dir(state.profile_name)
    d.mkdir(parents=True, exist_ok=True)
    fp = d / "state.json"
    if state.nav_history and len(state.nav_history) > _MAX_NAV_HISTORY:
        state.nav_history = state.nav_history[-_MAX_NAV_HISTORY:]
    state.last_update = datetime.now(tz=_CN_TZ).isoformat()
    data = asdict(state)
    save_state_json_atomic(fp, data)
    # 保留最近 N 个备份
    baks = sorted(d.glob("state.json.*.bak"), reverse=True)
    for old in baks[_MAX_BACKUPS:]:
        old.unlink(missing_ok=True)


# =========================================================================
# Live state — 独立于 paper_state
# =========================================================================
_LIVE_STATE_ROOT = _PROJ / "data" / "live_state"


def load_live_state(profile: str, initial_capital: float) -> ComboState:
    """加载实盘状态 (独立于 paper_state, 存放在 data/live_state/).

    迁移自 live_engine_eod.py。§13.16 废弃清理。
    """
    fp = _LIVE_STATE_ROOT / profile / "state.json"
    if fp.exists():
        try:
            raw = json.loads(fp.read_text(encoding="utf-8"))
            return ComboState(**{
                k: v for k, v in raw.items()
                if k in ComboState.__dataclass_fields__
            })
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.error("Live state 损坏: %s", exc)
            raise RuntimeError(f"Live state 损坏: {fp}") from exc
    return ComboState(
        profile_name=profile,
        initial_capital=initial_capital,
        cash=initial_capital,
        peak_nav=initial_capital,
    )


# =========================================================================
# 告警 — 统一飞书通道
# =========================================================================
def _send_alert(title: str, body: str, *, level: str = "WARNING") -> None:
    """发送告警 — 统一飞书通道."""
    try:
        from aipha.monitor.feishu_alert import send_feishu_alert
        send_feishu_alert(title, body, level=level)
    except Exception as exc:
        logger.warning("飞书告警失败: %s", exc)


def _notify_trades(
    profile: str,
    trade_date: str,
    state: Any,
    pre_trade_nav: float,
    final_nav: float,
) -> None:
    """推送当日交易摘要到飞书 — 委托共享入口."""
    today_trades = [
        t for t in state.trades if t.get("date") == trade_date
    ]
    if not today_trades:
        return
    try:
        from aipha.monitor.feishu_alert import send_trade_daily_report
        send_trade_daily_report(
            profile, trade_date,
            nav=final_nav,
            pre_trade_nav=pre_trade_nav,
            cash=state.cash,
            positions=state.positions,
            trades=state.trades,
            peak_nav=getattr(state, "peak_nav", 0.0),
        )
    except Exception as exc:
        logger.error("交易推送失败: %s — 交易已执行但通知未送达!", exc)


# =========================================================================
# 交易日检查 + 数据预检
# =========================================================================
def _is_trading_day(gw: Any) -> bool:
    """检查今天是否为交易日 (宽容 2 天).

    §13.18 fail-closed: 日历查询失败时返回 False（阻断交易），禁止静默放行。
    本地缓存日历可作为降级数据源。
    """
    today = datetime.now(tz=_CN_TZ).strftime("%Y%m%d")
    start = (datetime.now(tz=_CN_TZ) - timedelta(days=10)).strftime("%Y%m%d")
    try:
        cal = gw.call("trade_cal", start_date=start, end_date=today,
                       exchange="SSE")
        if cal is not None and not cal.empty:
            opens = cal[cal["is_open"] == 1]["cal_date"].tolist()
            if today in opens:
                return True
            if opens:
                latest_td = max(opens)
                diff = (
                    datetime.now(tz=_CN_TZ)
                    - datetime.strptime(latest_td, "%Y%m%d").replace(
                        tzinfo=_CN_TZ)
                ).days
                return diff <= 2
            # 日历返回但无 open 日 → 今天不是交易日
            return False
    except Exception as exc:
        logger.error(
            "§13.18 交易日历查询失败: %s — fail-closed, 阻断交易", exc
        )
        try:
            from aipha.monitor.feishu_alert import send_feishu_alert
            send_feishu_alert(
                f"交易日历查询失败(fail-closed): {exc}",
                level="WARNING",
            )
        except Exception:  # silent-pass-exempt: 主错误已 log+fail-closed, 告警失败不阻塞
            pass
    # §13.18: 查询失败或无数据 → 禁止交易（fail-closed）
    return False


def _data_preflight(gw: Any) -> bool:
    """数据预检 — 验证 TuShare API 可用.

    §13.18 fail-closed: 异常时返回 False（阻断），禁止静默放行。
    """
    try:
        df = gw.call("trade_cal", exchange="SSE",
                      start_date="20260101", end_date="20260131")
        return df is not None and not df.empty
    except Exception as exc:
        logger.error("数据预检失败: %s — 阻断后续操作 (§13.18 fail-closed)", exc)
        return False


# =========================================================================
# NAV + MDD
# =========================================================================
def _update_nav(
    state: ComboState, prices: dict, trade_date: str,
) -> float:
    """更新 NAV, 记录历史."""
    result = _ops_calc_nav(state.cash, state.positions, prices, fallback="avg_cost")
    nav = result.nav
    state.peak_nav = max(state.peak_nav, nav)
    state.days_traded += 1

    state.nav_history.append({
        "date": trade_date,
        "nav": round(nav, 2),
        "cash": round(state.cash, 2),
        "pos_value": round(result.position_value, 2),
        "n_positions": len(state.positions),
    })
    return nav


def reconcile_nav(
    state: ComboState, prices: dict[str, float], trade_date: str,
) -> bool:
    """§14.3 #8: NAV 日终交叉校验。

    检查 sum(持仓市值) + cash ≈ NAV，偏差 > 0.5% 触发 CRITICAL 告警。

    Returns:
        True if NAV is consistent, False if deviation detected.
    """
    result = _ops_calc_nav(state.cash, state.positions, prices, fallback="avg_cost")
    computed_nav = result.nav
    last_nav = state.nav_history[-1]["nav"] if state.nav_history else computed_nav
    if last_nav <= 0:
        return True
    deviation = abs(computed_nav - last_nav) / last_nav
    if deviation > 0.005:
        msg = (
            f"§14.3 NAV reconciliation FAILED on {trade_date}: "
            f"computed={computed_nav:.2f} vs recorded={last_nav:.2f} "
            f"(deviation={deviation:.2%})"
        )
        logger.critical(msg)
        try:
            from aipha.monitor.feishu_alert import send_feishu_alert
            send_feishu_alert(msg, level="CRITICAL")
        except Exception as exc:
            logger.debug("alert dispatch failed: %s", exc)
        return False
    return True


def _check_mdd(
    state: ComboState, nav: float, cfg: dict,
    *, trade_date: str = "",
) -> str:
    """MDD 检查 — 纯告警 + 安全阀 (不降权, 与回测一致).

    委托 MddAlertChecker 共享实现 (§4.3.1 / §13.8).
    告警/熔断时通过 notify_mdd_alert 统一推送 (§13.2 / WF-11c).
    Returns: "green" / "alert" / "halt"
    """
    checker = MddAlertChecker(
        alert_threshold=cfg.get("mdd_alert_threshold", 0.15),
        halt_threshold=cfg.get("mdd_halt_threshold", 0.30),
    )
    result = checker.check(nav, state.peak_nav)
    if result in ("alert", "halt"):
        dd = 1.0 - nav / state.peak_nav if state.peak_nav > 0 else 0.0
        if result == "alert":
            logger.warning("MDD 告警: %.2f%% (阈值 %.0f%%)",
                            dd * 100, checker.alert_threshold * 100)
        else:
            logger.error("MDD 熔断: %.2f%% (阈值 %.0f%%)",
                          dd * 100, checker.halt_threshold * 100)
        # §13.2 / WF-11c: 统一推送飞书 MDD 告警
        try:
            from aipha.monitor.feishu_alert import notify_mdd_alert
            notify_mdd_alert(
                profile=state.profile_name,
                trade_date=trade_date or "",
                nav=nav,
                peak_nav=state.peak_nav,
                mdd_status=result,
                alert_threshold=checker.alert_threshold,
                halt_threshold=checker.halt_threshold,
            )
        except Exception:
            logger.error("MDD 飞书告警推送失败 — 风控事件可能未通知!", exc_info=True)
    return result


# =========================================================================
# 审计日志
# =========================================================================
def _write_audit_log(state: ComboState, trade_date: str) -> None:
    """写入 JSONL 审计日志."""
    d = _AUDIT_DIR
    d.mkdir(parents=True, exist_ok=True)
    fp = d / f"audit_{trade_date}.jsonl"
    record = {
        "timestamp": datetime.now(tz=_CN_TZ).isoformat(),
        "profile": state.profile_name,
        "trade_date": trade_date,
        "cash": round(state.cash, 2),
        "n_positions": len(state.positions),
        "peak_nav": round(state.peak_nav, 2),
        "nav": (state.nav_history[-1]["nav"]
                if state.nav_history else 0),
    }
    with open(fp, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# =========================================================================
# PID 锁
# =========================================================================
def _acquire_pid_lock(profile: str) -> Any:
    """进程级互斥锁."""
    import tempfile
    lock_dir = Path(tempfile.gettempdir()) / "aipha_cron_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{profile}.lock"
    try:
        import fcntl
        fd = open(lock_path, "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(os.getpid()))
        fd.flush()
        return fd
    except ImportError:
        import msvcrt
        fd = open(lock_path, "w")
        try:
            msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
            fd.write(str(os.getpid()))
            fd.flush()
            return fd
        except OSError:
            fd.close()
            raise
    except OSError:
        raise RuntimeError(
            f"另一个 {profile} 实例正在运行 (lock: {lock_path})"
        )


# =========================================================================
# 组件解析
# =========================================================================
_RESERVED_KEYS = {
    "profile_name", "description", "initial_capital",
    "portfolio_weights", "price_fetch_deadline",
    "mdd_alert_threshold", "mdd_halt_threshold",
}


def _resolve_components(
    cfg: dict, weights: dict[str, float],
) -> list[tuple[str, dict]]:
    """按权重键从 YAML 顶层提取组件配置, 按权重降序排列."""
    result: list[tuple[str, dict]] = []
    for name in weights:
        comp_cfg = cfg.get(name)
        if comp_cfg is None:
            logger.warning("权重中有 %s 但 YAML 无对应配置段, 跳过", name)
            continue
        if not isinstance(comp_cfg, dict):
            logger.warning("组件 %s 配置不是 dict, 跳过", name)
            continue
        result.append((name, comp_cfg))
    # 按权重降序 —— 大权重组件先调仓
    result.sort(key=lambda x: weights.get(x[0], 0), reverse=True)
    return result
