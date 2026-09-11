"""Heartbeat Checker v2 — 状态机 + 调度感知 + Grace Period.

核心改进（对比 v1）：
- 交易日历感知：非交易日自动跳过检查
- 调度频率感知：从配置推导 Period，不再固定 6h
- Grace Period：Late ≠ Down，超时分两阶段（Late→Grace→Down）
- 因果抑制：上游 DOWN 时抑制下游告警
- 组件级粒度：runner 内部各组合状态独立追踪
- 执行挂死检测：Start 后无 End 超过 max_execution → HUNG
- 恢复通知：DOWN→UP 时发送 recovery 消息

Usage::

    from aipha.monitor.heartbeat_checker_v2 import run_heartbeat_check_v2
    result = run_heartbeat_check_v2()
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from enum import Enum
from pathlib import Path
from typing import Any
from aipha.utils.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)


class CheckState(str, Enum):
    """心跳检查状态."""

    UP = "UP"
    LATE = "LATE"
    DOWN = "DOWN"
    HUNG = "HUNG"
    MISSING = "MISSING"
    NEVER_SUCCEEDED = "NEVER_SUCCEEDED"
    PAUSED = "PAUSED"


@dataclass
class CheckResult:
    """单个任务的检查结果."""

    name: str
    state: CheckState
    message: str
    alert_level: str = "WARNING"
    age_minutes: float = 0.0
    threshold_minutes: float = 0.0
    components: dict[str, Any] = field(default_factory=dict)
    suppressed: bool = False
    suppression_reason: str = ""
    last_success_ts: str | None = None
    is_recovery: bool = False


@dataclass
class HeartbeatCheckSummary:
    """全部检查结果汇总."""

    results: list[CheckResult] = field(default_factory=list)
    check_time: str = ""
    is_trading_day: bool = True
    skipped_non_trading: bool = False

    @property
    def critical_count(self) -> int:
        return sum(
            1 for r in self.results
            if r.state in (CheckState.DOWN, CheckState.HUNG, CheckState.MISSING)
            and not r.suppressed
        )

    @property
    def warning_count(self) -> int:
        return sum(
            1 for r in self.results
            if r.state == CheckState.LATE and not r.suppressed
        )

    @property
    def healthy(self) -> bool:
        return self.critical_count == 0


@dataclass
class CheckConfig:
    """单个任务的检查配置."""

    name: str
    schedule: str  # cron 表达式
    period_minutes: int
    grace_time_minutes: int = 30
    max_execution_minutes: int = 60
    alert_level: str = "CRITICAL"
    inhibits: list[str] = field(default_factory=list)
    per_component: bool = False
    non_trading_day_skip: bool = True
    watchdog: bool = False


@dataclass
class HeartbeatPolicy:
    """心跳策略配置."""

    checks: dict[str, CheckConfig] = field(default_factory=dict)
    dedup_window_seconds: int = 3600
    group_wait_seconds: int = 60
    recovery_notify: bool = True
    inhibition_rules: list[dict[str, Any]] = field(default_factory=list)


def load_heartbeat_policy(policy_path: Path | None = None) -> HeartbeatPolicy:
    """加载心跳策略配置.

    优先从 configs/paper/heartbeat_policy.yml 加载，
    文件不存在时使用内置默认策略。
    """
    if policy_path is None:
        # 自动探测路径
        candidates = [
            Path("/app/configs/paper/heartbeat_policy.yml"),
            PROJECT_ROOT / "configs" / "paper" / "heartbeat_policy.yml",
        ]
        for c in candidates:
            if c.exists():
                policy_path = c
                break

    if policy_path and policy_path.exists():
        try:
            import yaml  # noqa: PLC0415

            raw = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
            return _parse_policy(raw)
        except Exception as e:
            logger.warning("Failed to load heartbeat policy from %s: %s", policy_path, e)

    # 内置默认策略
    return _default_policy()


def _parse_policy(raw: dict) -> HeartbeatPolicy:
    """解析 YAML 配置为 HeartbeatPolicy."""
    checks = {}
    defaults = raw.get("defaults", {})
    for name, cfg in raw.get("checks", {}).items():
        checks[name] = CheckConfig(
            name=name,
            schedule=cfg.get("schedule", ""),
            period_minutes=cfg.get("period_minutes", 1440),
            grace_time_minutes=cfg.get(
                "grace_time_minutes", defaults.get("grace_time_minutes", 30)
            ),
            max_execution_minutes=cfg.get(
                "max_execution_minutes", defaults.get("max_execution_minutes", 60)
            ),
            alert_level=cfg.get("alert_level", "CRITICAL"),
            inhibits=cfg.get("inhibits", []),
            per_component=cfg.get("per_component", False),
            non_trading_day_skip=cfg.get(
                "non_trading_day_skip", defaults.get("non_trading_day_skip", True)
            ),
            watchdog=cfg.get("watchdog", False),
        )

    routing = raw.get("alert_routing", {})
    return HeartbeatPolicy(
        checks=checks,
        dedup_window_seconds=routing.get("dedup_window_seconds", 3600),
        group_wait_seconds=routing.get("group_wait_seconds", 60),
        recovery_notify=routing.get("recovery_notify", True),
        inhibition_rules=raw.get("inhibition_rules", []),
    )


def _default_policy() -> HeartbeatPolicy:
    """内置默认策略（无配置文件时的 fallback）."""
    return HeartbeatPolicy(
        checks={
            "daily_runner": CheckConfig(
                name="daily_runner",
                schedule="0 19 * * 1-5",
                period_minutes=1440,
                grace_time_minutes=30,
                max_execution_minutes=45,
                alert_level="CRITICAL",
                per_component=True,
            ),
            "data_preheat": CheckConfig(
                name="data_preheat",
                schedule="0 17 * * 1-5",
                period_minutes=1440,
                grace_time_minutes=60,
                max_execution_minutes=90,
                alert_level="CRITICAL",
                inhibits=["daily_runner", "live_eod"],
            ),
            "live_eod": CheckConfig(
                name="live_eod",
                schedule="0 21 * * 1-5",
                period_minutes=1440,
                grace_time_minutes=30,
                max_execution_minutes=30,
                alert_level="CRITICAL",
                per_component=True,
            ),
        },
        inhibition_rules=[
            {
                "source": "data_preheat",
                "targets": ["daily_runner", "live_eod"],
                "reason": "上游数据未就绪导致下游无法执行",
            }
        ],
    )


def _is_trading_day_check(d: date | None = None) -> bool:
    """判断是否为交易日.

    尝试使用 CNStockCalendar，失败时降级为简单周末判断。
    """
    if d is None:
        d = date.today()

    try:
        from aipha.data.calendar import CNStockCalendar  # noqa: PLC0415

        cal = CNStockCalendar()
        return cal.is_trading_day(d)
    except Exception:
        # 降级：排除周末
        return d.weekday() < 5


def _calc_trading_age_minutes(
    last_ts: datetime, now: datetime
) -> float:
    """计算交易日感知的时间间隔（分钟）.

    规则：
    - 同交易日或相邻交易日：直接计算实际时间差
    - 跨非交易日（周末/假期）：仅计算交易日数 × 24h
      避免周末后首个交易日虚假超时
    """
    last_date = last_ts.date()
    now_date = now.date()

    if last_date == now_date:
        # 同一天 — 直接差
        return (now - last_ts).total_seconds() / 60

    # 计算之间的交易日数
    trading_days = 0
    current = last_date + timedelta(days=1)
    while current <= now_date:
        if _is_trading_day_check(current):
            trading_days += 1
        current += timedelta(days=1)

    if trading_days <= 1:
        # 相邻交易日 — 直接实际差（允许跨周末不超时）
        # 但上限为 1个交易日的 period（1440 分钟）
        actual_mins = (now - last_ts).total_seconds() / 60
        # 如果跨了非交易日，有效时间按 1 个交易日计
        if trading_days == 1 and (now_date - last_date).days > 1:
            return min(actual_mins, 1440.0)
        return actual_mins
    else:
        # 多个交易日间隔
        return trading_days * 24 * 60


def _check_single_task(
    check_cfg: CheckConfig,
    heartbeat_dir: Path,
    now: datetime,
) -> CheckResult:
    """检查单个任务的心跳状态.

    状态判定逻辑：
    1. 文件不存在 → MISSING
    2. state=running 且超 max_execution → HUNG
    3. 从未成功 → NEVER_SUCCEEDED
    4. 成功且在 period 内 → UP
    5. 成功超 period 但在 grace 内 → LATE
    6. 超 period + grace → DOWN
    """
    hb_path = heartbeat_dir / f"{check_cfg.name}.json"

    if not hb_path.exists():
        return CheckResult(
            name=check_cfg.name,
            state=CheckState.MISSING,
            message=f"任务 {check_cfg.name} 无心跳文件，可能从未运行",
            alert_level=check_cfg.alert_level,
        )

    try:
        hb = json.loads(hb_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return CheckResult(
            name=check_cfg.name,
            state=CheckState.DOWN,
            message=f"心跳文件解析失败: {e}",
            alert_level=check_cfg.alert_level,
        )

    # Check for HUNG state (running too long)
    if hb.get("state") == "running":
        start_ts_str = hb.get("start_ts")
        if start_ts_str:
            try:
                start_dt = datetime.fromisoformat(start_ts_str)
                running_mins = (now - start_dt).total_seconds() / 60
                if running_mins > check_cfg.max_execution_minutes:
                    return CheckResult(
                        name=check_cfg.name,
                        state=CheckState.HUNG,
                        message=(
                            f"任务已运行 {running_mins:.0f} 分钟，"
                            f"超过 {check_cfg.max_execution_minutes} 分钟上限"
                        ),
                        alert_level=check_cfg.alert_level,
                        age_minutes=running_mins,
                        threshold_minutes=float(check_cfg.max_execution_minutes),
                    )
            except (ValueError, TypeError):
                pass
        # Still running within limits
        return CheckResult(
            name=check_cfg.name,
            state=CheckState.UP,
            message="任务执行中",
        )

    # Check success/fail state freshness
    end_ts_str = hb.get("end_ts")
    if not end_ts_str:
        return CheckResult(
            name=check_cfg.name,
            state=CheckState.NEVER_SUCCEEDED,
            message="任务从未成功完成（无 end_ts）",
            alert_level=check_cfg.alert_level,
        )

    try:
        last_success = datetime.fromisoformat(end_ts_str)
    except (ValueError, TypeError):
        return CheckResult(
            name=check_cfg.name,
            state=CheckState.DOWN,
            message=f"end_ts 解析失败: {end_ts_str}",
            alert_level=check_cfg.alert_level,
        )

    # If last run was a failure, check if it's stale
    if hb.get("state") == "failed":
        age_minutes = _calc_trading_age_minutes(last_success, now)
        return CheckResult(
            name=check_cfg.name,
            state=CheckState.DOWN,
            message=(
                f"最近一次运行失败: {hb.get('error', 'unknown')}\n"
                f"失败时间: {end_ts_str}"
            ),
            alert_level=check_cfg.alert_level,
            age_minutes=age_minutes,
            last_success_ts=end_ts_str,
            components=hb.get("components", {}),
        )

    # Normal success — check freshness
    age_minutes = _calc_trading_age_minutes(last_success, now)
    period = float(check_cfg.period_minutes)
    grace = float(check_cfg.grace_time_minutes)

    if age_minutes <= period:
        return CheckResult(
            name=check_cfg.name,
            state=CheckState.UP,
            message="正常",
            age_minutes=age_minutes,
            last_success_ts=end_ts_str,
            components=hb.get("components", {}),
        )
    elif age_minutes <= period + grace:
        return CheckResult(
            name=check_cfg.name,
            state=CheckState.LATE,
            message=(
                f"心跳延迟 {age_minutes - period:.0f} 分钟"
                f"（Grace 期内，{grace - (age_minutes - period):.0f}min 后告警）"
            ),
            alert_level="WARNING",
            age_minutes=age_minutes,
            threshold_minutes=period + grace,
            last_success_ts=end_ts_str,
            components=hb.get("components", {}),
        )
    else:
        return CheckResult(
            name=check_cfg.name,
            state=CheckState.DOWN,
            message=(
                f"心跳超时: 上次成功 {end_ts_str} "
                f"({age_minutes:.0f}min 前 > {period + grace:.0f}min 阈值)"
            ),
            alert_level=check_cfg.alert_level,
            age_minutes=age_minutes,
            threshold_minutes=period + grace,
            last_success_ts=end_ts_str,
            components=hb.get("components", {}),
        )


def _apply_inhibition(
    results: list[CheckResult],
    rules: list[dict[str, Any]],
) -> list[CheckResult]:
    """应用因果抑制规则.

    当上游任务 DOWN/HUNG 时，抑制下游告警，避免级联虚假告警。
    抑制的告警仍记录日志，只是不推送飞书。
    """
    down_tasks = {
        r.name for r in results
        if r.state in (CheckState.DOWN, CheckState.HUNG, CheckState.MISSING)
    }

    for rule in rules:
        source = rule.get("source", "")
        targets = rule.get("targets", [])
        reason = rule.get("reason", "上游失败")

        if source in down_tasks:
            for r in results:
                if r.name in targets and r.state in (
                    CheckState.DOWN, CheckState.HUNG, CheckState.LATE
                ):
                    r.suppressed = True
                    r.suppression_reason = f"被 {source} 抑制: {reason}"
                    logger.info(
                        "Inhibited alert: %s (%s) — %s",
                        r.name, r.state.value, r.suppression_reason,
                    )

    return results


# 历史状态文件：记录上次检查时各任务状态，用于检测恢复
_STATE_HISTORY_FILE = "heartbeat_check_state.json"


def _detect_recoveries(
    results: list[CheckResult],
    state_file: Path,
) -> list[CheckResult]:
    """检测从 DOWN → UP 的恢复，标记 is_recovery=True.

    通过比较当前状态与上次保存的状态来确定恢复。
    """
    # 读取上次状态
    prev_states: dict[str, str] = {}
    if state_file.exists():
        try:
            prev_states = json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    # 标记恢复
    recoveries = []
    for r in results:
        prev_state = prev_states.get(r.name)
        if prev_state in ("DOWN", "HUNG", "MISSING") and r.state == CheckState.UP:
            r.is_recovery = True
            recoveries.append(r)
            logger.info("Recovery detected: %s (was %s → now UP)", r.name, prev_state)

    # 保存当前状态
    current_states = {r.name: r.state.value for r in results}
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps(current_states, indent=2), encoding="utf-8"
        )
    except OSError as e:
        logger.warning("Failed to save check state: %s", e)

    return recoveries


def run_heartbeat_check_v2(
    policy_path: Path | None = None,
    heartbeat_dir: Path | None = None,
) -> HeartbeatCheckSummary:
    """执行 v2 心跳检查主流程.

    Returns:
        HeartbeatCheckSummary 包含所有检查结果
    """
    now = datetime.now().astimezone()
    today = now.date()

    # 加载策略配置
    policy = load_heartbeat_policy(policy_path)

    # 确定心跳目录
    if heartbeat_dir is None:
        candidates = [
            Path("/app/logs/heartbeat"),
            PROJECT_ROOT / "logs" / "heartbeat",
        ]
        for c in candidates:
            if c.exists():
                heartbeat_dir = c
                break
        if heartbeat_dir is None:
            heartbeat_dir = candidates[-1]

    # 非交易日判断
    is_trading = _is_trading_day_check(today)
    if not is_trading:
        # 非交易日：仅检查不跳过的任务
        trading_checks = {
            name: cfg for name, cfg in policy.checks.items()
            if not cfg.non_trading_day_skip
        }
        if not trading_checks:
            logger.info("Non-trading day, all checks skipped")
            return HeartbeatCheckSummary(
                check_time=now.isoformat(),
                is_trading_day=False,
                skipped_non_trading=True,
            )
        active_checks = trading_checks
    else:
        active_checks = policy.checks

    # 逐任务检查
    results: list[CheckResult] = []
    for name, check_cfg in active_checks.items():
        result = _check_single_task(check_cfg, heartbeat_dir, now)
        results.append(result)

    # 应用因果抑制
    results = _apply_inhibition(results, policy.inhibition_rules)

    # 检测恢复
    state_file = heartbeat_dir / _STATE_HISTORY_FILE
    _detect_recoveries(results, state_file)

    summary = HeartbeatCheckSummary(
        results=results,
        check_time=now.isoformat(),
        is_trading_day=is_trading,
    )

    logger.info(
        "Heartbeat check complete: %d checks, %d critical, %d warning, %d recovery",
        len(results),
        summary.critical_count,
        summary.warning_count,
        sum(1 for r in results if r.is_recovery),
    )

    return summary
