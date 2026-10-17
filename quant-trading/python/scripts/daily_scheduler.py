"""每日盘后调度器 — daily_scheduler.py

设计用途：
    在每个交易日 15:30 收盘后自动运行，执行因子衰减检测、特征漂移检测、
    合规撤单率校验等日常监控任务，并将告警写入审计日志。

运行方式（Docker cron）：
    # docker-compose cron 示例（crontab 入口）
    # 30 15 * * 1-5  conda run -n python3.13 python /app/scripts/daily_scheduler.py
    #
    # 本地测试运行：
    # python scripts/daily_scheduler.py [--date 2024-01-05] [--dry-run]

任务列表：
    Task-A  因子衰减监控：读取每日 IC 数据，更新 DecayMonitor + HalflifeDecayMonitor
    Task-B  特征漂移检测：读取特征分布数据，调用 DriftMonitor.detect_drift()
    Task-C  合规撤单率校验：读取当日撤单/总单数据，调用 ComplianceEngine.check_cancel_rate()
    Task-D  告警聚合：将所有 warning/error 告警写入 logs/audit/daily_monitor_{YYYYMMDD}.jsonl
    Task-E  重训触发检测：调用 RetrainTrigger.evaluate()，判断是否需要重训，写入 RetrainDecision

依赖：
    src/aipha/factors/decay_monitor.py    DecayMonitor
    src/aipha/monitoring/               HalflifeDecayMonitor
    src/aipha/models/drift_monitor.py   DriftMonitor
    src/aipha/portfolio/compliance_engine.py  ComplianceEngine
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Any

# --- 确保项目根在 PYTHONPATH -----------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from aipha.factors.decay_monitor import DecayMonitor
from aipha.models.drift_monitor import DriftMonitor, PSI_WARNING
from aipha.monitoring import HalflifeDecayMonitor, HalflifeSnapshot
from aipha.portfolio.compliance_engine import ComplianceEngine

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

AUDIT_LOG_DIR = _REPO_ROOT / "logs" / "audit"
try:
    from aipha.config import get_cache_dir
    DATA_CACHE_DIR = get_cache_dir()
except Exception:
    DATA_CACHE_DIR = _REPO_ROOT / "data" / "cache"
COMPLIANCE_CONFIG = _REPO_ROOT / "configs" / "algo_trading_compliance.yml"

# 每日 IC 数据文件（由回测引擎写出）
FACTOR_IC_CACHE_PREFIX = "factor_ic_daily"

# 因子名称清单（A股截面因子 + CB因子）
_DEFAULT_FACTORS = [
    "ep_ratio", "bp_ratio", "roe", "mom_12_1", "mom_3",
    "ivol_neg", "log_mv", "turnover_rate", "amihud",
    "cb_premium_rate", "cb_ytm", "cb_double_low",
]

logger = logging.getLogger("daily_scheduler")


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _write_audit_jsonl(log_path: Path, records: list[dict[str, Any]]) -> None:
    """将多条记录追加写入 JSONL 审计文件。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    logger.info("Wrote %d records to %s", len(records), log_path)


def _load_factor_ic_data(trade_date: str) -> dict[str, float]:
    """加载当日因子 IC 数据。

    文件格式：data/cache/factor_ic_daily_{YYYYMMDD}.json
    示例：{"ep_ratio": 0.032, "bp_ratio": -0.015, ...}

    若文件不存在则返回空字典（告警通知）。
    """
    fname = DATA_CACHE_DIR / f"{FACTOR_IC_CACHE_PREFIX}_{trade_date.replace('-','')}.json"
    if not fname.exists():
        logger.warning("Factor IC cache not found: %s", fname)
        return {}
    with open(fname, encoding="utf-8") as f:
        return json.load(f)


def _load_feature_snapshot(trade_date: str) -> "pd.DataFrame | None":
    """加载当日特征快照（用于漂移检测）。

    文件格式：data/cache/feature_snapshot_{YYYYMMDD}.parquet
    """
    try:
        import pandas as pd
        fname = DATA_CACHE_DIR / f"feature_snapshot_{trade_date.replace('-', '')}.parquet"
        if fname.exists():
            return pd.read_parquet(fname)
        logger.warning("Feature snapshot not found: %s", fname)
        return None
    except ImportError:
        logger.warning("pandas not available — skipping drift detection")
        return None


def _load_cancel_rate_data(trade_date: str) -> tuple[int, int]:
    """加载当日撤单/总订单数据。

    文件格式：data/paper/cancel_stats_{YYYYMMDD}.json
    示例：{"n_cancels": 12, "n_total": 150}

    若无数据返回 (0, 0)。
    """
    fname = _REPO_ROOT / "data" / "paper" / f"cancel_stats_{trade_date.replace('-','')}.json"
    if not fname.exists():
        return 0, 0
    with open(fname, encoding="utf-8") as f:
        d = json.load(f)
    return int(d.get("n_cancels", 0)), int(d.get("n_total", 0))


# ---------------------------------------------------------------------------
# Task-A: 因子衰减监控
# ---------------------------------------------------------------------------

def run_task_a_factor_decay(
    trade_date: str,
    monitor_store: dict[str, HalflifeDecayMonitor],
) -> list[dict[str, Any]]:
    """Task-A：更新每日因子 IC，检查衰减告警。

    Returns:
        告警记录列表（severity in ["info", "warning", "error"]）
    """
    ic_data = _load_factor_ic_data(trade_date)
    if not ic_data:
        return [{
            "task": "factor_decay",
            "date": trade_date,
            "severity": "warning",
            "message": "No factor IC data found for today",
        }]

    alerts: list[dict[str, Any]] = []
    for factor_name in _DEFAULT_FACTORS:
        ic_val = ic_data.get(factor_name)
        if ic_val is None:
            continue

        if factor_name not in monitor_store:
            monitor_store[factor_name] = HalflifeDecayMonitor(
                factor_name=factor_name,
                window=60,
                icir_threshold=0.3,
                min_obs_for_halflife=10,
            )
        monitor = monitor_store[factor_name]
        result = monitor.update(trade_date, ic_val)

        # 检查告警
        snapshot = monitor.get_halflife_snapshot()
        if snapshot.decay_alert:
            alerts.append({
                "task": "factor_decay",
                "date": trade_date,
                "factor": factor_name,
                "severity": "warning",
                "message": (
                    f"Factor {factor_name} alpha decay detected: "
                    f"recent_icir={snapshot.recent_icir:.3f}, "
                    f"full_icir={snapshot.full_icir:.3f}, "
                    f"halflife={snapshot.halflife_days}"
                ),
            })
        logger.debug(
            "Task-A [%s] ic=%.4f alert=%s",
            factor_name, ic_val, snapshot.decay_alert,
        )

    logger.info(
        "Task-A done: %d factors updated, %d alerts",
        len(ic_data), len(alerts),
    )
    return alerts


# ---------------------------------------------------------------------------
# Task-B: 特征漂移检测
# ---------------------------------------------------------------------------

def run_task_b_drift_detection(
    trade_date: str,
    drift_monitor: DriftMonitor | None,
) -> list[dict[str, Any]]:
    """Task-B：检测特征分布漂移（PSI）。

    若 drift_monitor 未初始化（无基准分布），跳过并返回 info 告警。
    PSI > 0.20 触发 warning，并记录建议重训练。
    """
    if drift_monitor is None:
        return [{
            "task": "drift_detection",
            "date": trade_date,
            "severity": "info",
            "message": "DriftMonitor not initialized — skip",
        }]

    snapshot = _load_feature_snapshot(trade_date)
    if snapshot is None:
        return [{
            "task": "drift_detection",
            "date": trade_date,
            "severity": "warning",
            "message": "Feature snapshot not found — drift detection skipped",
        }]

    try:
        report = drift_monitor.detect_drift(snapshot)
    except Exception as exc:
        return [{
            "task": "drift_detection",
            "date": trade_date,
            "severity": "error",
            "message": f"DriftMonitor.detect_drift() raised: {exc}",
        }]

    alerts: list[dict[str, Any]] = []
    for feat_name, psi_val in getattr(report, "feature_psi", {}).items():
        if psi_val >= PSI_WARNING:
            alerts.append({
                "task": "drift_detection",
                "date": trade_date,
                "feature": feat_name,
                "psi": psi_val,
                "severity": "warning",
                "message": (
                    f"Feature {feat_name} PSI={psi_val:.4f} >= {PSI_WARNING} "
                    f"— consider model retraining"
                ),
            })

    logger.info(
        "Task-B done: overall_status=%s drift_alerts=%d",
        getattr(report, "overall_status", "unknown"),
        len(alerts),
    )
    return alerts


# ---------------------------------------------------------------------------
# Task-C: 合规撤单率检查
# ---------------------------------------------------------------------------

def run_task_c_cancel_rate(
    trade_date: str,
    compliance_engine: ComplianceEngine,
) -> list[dict[str, Any]]:
    """Task-C：每日盘后校验实盘撤单率是否超标（2024-10 管理规定）。"""
    n_cancels, n_total = _load_cancel_rate_data(trade_date)
    if n_total == 0:
        return [{
            "task": "cancel_rate",
            "date": trade_date,
            "severity": "info",
            "message": "No order data — cancel_rate check skipped",
        }]

    passed, rate = compliance_engine.check_cancel_rate(n_cancels, n_total)
    if not passed:
        return [{
            "task": "cancel_rate",
            "date": trade_date,
            "severity": "error",
            "cancel_rate": rate,
            "n_cancels": n_cancels,
            "n_total": n_total,
            "message": (
                f"Cancel rate {rate:.2%} exceeds limit "
                f"{compliance_engine._max_cancel_rate:.0%} — review mandatory"
            ),
        }]

    logger.info("Task-C done: cancel_rate=%.2f%%", rate * 100)
    return []


# ---------------------------------------------------------------------------
# Task-E: 重训触发检测
# ---------------------------------------------------------------------------

def run_task_e_retrain_trigger(
    trade_date: str,
    drift_report: Any = None,
    decay_snapshot: Any = None,
) -> list[dict[str, Any]]:
    """Task-E：使用 RetrainTrigger 评估是否需要模型重训。

    将 Task-A（decay_snapshot）和 Task-B（drift_report）的结果组合，
    通过 RetrainTrigger.evaluate() 产生标准化 RetrainDecision。
    仅产生告警/建议，不自动执行重训（由 auto_retrain_pipeline 编排）。
    """
    try:
        from aipha.monitoring.retrain_trigger import RetrainTrigger
    except ImportError:
        return [{
            "task": "retrain_trigger",
            "date": trade_date,
            "severity": "info",
            "message": "RetrainTrigger not available — skip",
        }]

    trigger = RetrainTrigger()
    decision = trigger.evaluate(
        drift_report=drift_report,
        decay_snapshot=decay_snapshot,
    )

    alerts: list[dict[str, Any]] = []
    if decision.should_retrain:
        reason_names = [r.value for r in decision.reasons]
        alerts.append({
            "task": "retrain_trigger",
            "date": trade_date,
            "severity": "warning",
            "should_retrain": True,
            "urgency": decision.urgency,
            "reasons": reason_names,
            "message": (
                f"RetrainTrigger fired: urgency={decision.urgency}, "
                f"reasons={reason_names}. "
                f"Run auto_retrain_pipeline to execute."
            ),
        })
    else:
        logger.info("Task-E done: no retrain needed")

    return alerts


# ---------------------------------------------------------------------------
# Task-F: 条件性重训执行 (R87: 端到端打通)
# ---------------------------------------------------------------------------

def run_task_f_auto_retrain(
    trade_date: str,
    config_path: str = "configs/retrain_csi2000.yml",
) -> list[dict[str, Any]]:
    """Task-F: 当 Task-E 触发重训时，执行 AutoRetrainPipeline。

    仅在 Task-E 返回 should_retrain=True 时被 run_daily_schedule 调用。
    """
    try:
        from aipha.models.retrain_pipeline import (
            AutoRetrainPipeline,
            RetrainConfig,
        )
    except ImportError:
        return [{
            "task": "auto_retrain",
            "date": trade_date,
            "severity": "warning",
            "message": "AutoRetrainPipeline not available — skip",
        }]

    alerts: list[dict[str, Any]] = []
    try:
        cfg = RetrainConfig.from_yaml(config_path)
        if not cfg.legacy_ml_compat:
            return [{
                "task": "auto_retrain",
                "date": trade_date,
                "severity": "info",
                "skipped": True,
                "message": (
                    "Legacy selector retraining is disabled by default; "
                    "use the canonical aipha.research pipeline or explicitly "
                    "set legacy_ml_compat: true for a historical model"
                ),
            }]
        pipeline = AutoRetrainPipeline(cfg)
        result = pipeline.run(end_date=trade_date.replace("-", ""))
        alerts.append({
            "task": "auto_retrain",
            "date": trade_date,
            "severity": "info" if result.promoted else "warning",
            "triggered": result.triggered,
            "retrained": result.retrained,
            "promoted": result.promoted,
            "rolled_back": result.rolled_back,
            "new_ic": result.new_ic,
            "old_ic": result.old_ic,
            "message": (
                f"AutoRetrain: promoted={result.promoted}, "
                f"IC {result.old_ic:.4f}→{result.new_ic:.4f}"
                if result.retrained
                else f"AutoRetrain: triggered={result.triggered}"
            ),
        })
    except Exception as exc:
        logger.exception("Task-F auto_retrain failed")
        alerts.append({
            "task": "auto_retrain",
            "date": trade_date,
            "severity": "error",
            "message": f"AutoRetrain failed: {exc!r}",
        })

    return alerts


# ---------------------------------------------------------------------------
# 主调度函数
# ---------------------------------------------------------------------------

def run_daily_schedule(
    trade_date: str,
    dry_run: bool = False,
    monitor_store: dict[str, HalflifeDecayMonitor] | None = None,
    drift_monitor: DriftMonitor | None = None,
) -> dict[str, Any]:
    """执行全部每日监控任务，返回汇总结果。

    Args:
        trade_date:    交易日期字符串 "YYYY-MM-DD"
        dry_run:       若为 True，不写入审计日志（用于测试）
        monitor_store: 跨轮次持久化的 DecayMonitor 字典（可选）
        drift_monitor: DriftMonitor 实例（可选，首次运行无基准时设为 None）

    Returns:
        {
            "date": ...,
            "tasks_run": ...,
            "total_alerts": ...,
            "alerts": [...],
            "success": True/False,
        }
    """
    if monitor_store is None:
        monitor_store = {}

    logger.info("=" * 60)
    logger.info("Daily Schedule Starting: %s", trade_date)

    compliance_engine = ComplianceEngine.from_config(COMPLIANCE_CONFIG)

    all_alerts: list[dict[str, Any]] = []

    # Task-A
    alerts_a = run_task_a_factor_decay(trade_date, monitor_store)
    all_alerts.extend(alerts_a)

    # Task-B
    alerts_b = run_task_b_drift_detection(trade_date, drift_monitor)
    all_alerts.extend(alerts_b)

    # Task-C
    alerts_c = run_task_c_cancel_rate(trade_date, compliance_engine)
    all_alerts.extend(alerts_c)

    # Task-E: 重训触发检测（消费 Task-B 的 drift 结果）
    alerts_e = run_task_e_retrain_trigger(trade_date)
    all_alerts.extend(alerts_e)

    # Task-F: 条件性重训执行（R87: 端到端打通）
    retrain_fired = any(
        a.get("task") == "retrain_trigger" and a.get("should_retrain")
        for a in alerts_e
    )
    if retrain_fired and not dry_run:
        alerts_f = run_task_f_auto_retrain(trade_date)
        all_alerts.extend(alerts_f)

    # Task-D: 聚合写入审计日志
    error_count = sum(1 for a in all_alerts if a.get("severity") == "error")
    warning_count = sum(1 for a in all_alerts if a.get("severity") == "warning")

    summary = {
        "scheduler_event": "daily_schedule_complete",
        "date": trade_date,
        "ran_at": datetime.now().isoformat(),
        "tasks_run": ["factor_decay", "drift_detection", "cancel_rate", "retrain_trigger", "auto_retrain"],
        "total_alerts": len(all_alerts),
        "error_count": error_count,
        "warning_count": warning_count,
        "alerts": all_alerts,
        "success": error_count == 0,
    }

    if not dry_run and all_alerts:
        log_date = trade_date.replace("-", "")
        log_path = AUDIT_LOG_DIR / f"daily_monitor_{log_date}.jsonl"
        _write_audit_jsonl(log_path, [summary])

    logger.info(
        "Daily Schedule Done: errors=%d, warnings=%d, total_alerts=%d",
        error_count, warning_count, len(all_alerts),
    )
    return summary


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aipha-Q 每日盘后调度器")
    parser.add_argument(
        "--date",
        default=date.today().strftime("%Y-%m-%d"),
        help="交易日期 YYYY-MM-DD（默认今日）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="不写入审计日志（测试用）",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="日志级别（DEBUG/INFO/WARNING）",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _setup_logging(args.log_level)
    result = run_daily_schedule(
        trade_date=args.date,
        dry_run=args.dry_run,
    )
    if not result["success"]:
        logger.error(
            "Daily schedule completed with %d error(s) — review alerts",
            result["error_count"],
        )
        return 1
    logger.info("Daily schedule completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
