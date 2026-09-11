#!/usr/bin/env python3
"""因子衰减 / 特征漂移周报（cron 每日或每周）

``scripts/daily_scheduler.py`` 每个交易日把当天的监控告警写进
``logs/audit/daily_monitor_{YYYYMMDD}.jsonl``。单日看不出趋势 —— 一个因子
今天 IC 掉一点很正常，连着两周掉才是衰减。本脚本把最近 N 天的审计日志汇总
成一份 JSON 报告，回答两个问题：

1. 哪些因子在反复告警（``factor_decay`` / ``drift_detection`` 的 warning+）
2. 哪些特征的 PSI 已经越过显著漂移线（默认 0.2，与
   ``aipha/models/drift_monitor.py:31`` 的 ``PSI_WARNING`` 同口径）

## 退出码为什么只看 PSI

``main`` 只在**有 PSI 告警**时返回 1。因子衰减是常态：单因子 IC 衰减本来就
是量化的日常，每次都非零退出会让 cron 天天报警，真事故就被淹了。而 PSI
越线意味着模型**输入分布**已经偏离训练集 —— 此时模型的预测不再有统计
保证，属于必须有人看一眼的情况。

## 日志的两种形状

``daily_scheduler`` 实际写的是**每天一行汇总**，当天的告警嵌在
``alerts`` 数组里（``daily_scheduler.py:485-499``）；早期版本 / 其他写入方
则是一行一条告警。``load_daily_monitor_alerts`` 两种都吃：见到 ``alerts``
数组就展开成扁平告警，否则整行当一条告警。

用法::

    python scripts/ops/factor_decay_report.py --lookback-days 14
    python scripts/ops/factor_decay_report.py --psi-threshold 0.25 --notify
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from aipha.utils.paths import LOGS_DIR, PROJECT_ROOT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_AUDIT_DIR = LOGS_DIR / "audit"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output" / "reports"
REPORT_FILENAME = "factor_decay_report.json"

AUDIT_FILE_PREFIX = "daily_monitor_"
AUDIT_DATE_FORMAT = "%Y%m%d"

DEFAULT_LOOKBACK_DAYS = 7

# 显著漂移阈值，与 aipha/models/drift_monitor.py 的 PSI_WARNING 保持一致。
# 这里不 import 那个常量：本脚本要能在只挂了 scripts/ 的运维容器里跑，
# 不该为了一个数字把整个 models 依赖链拉进来。数值变更时两处同步。
DEFAULT_PSI_THRESHOLD = 0.2

# 计入「衰减类」告警的任务名。cancel_rate / retrain_trigger 不算衰减，
# 它们各有自己的看板，混进来只会稀释这份报告。
_DECAY_TASKS = frozenset({
    "factor_decay",
    "drift_detection",
    "halflife_decay",
})

_ALERT_SEVERITIES = frozenset({"warning", "error", "critical"})

# 明细最多留多少条 —— 报告是给人看的，几千条明细只会让人不看
_MAX_DETAILS = 50


def load_daily_monitor_alerts(
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    audit_dir: str | Path | None = None,
    today: datetime | None = None,
) -> list[dict[str, Any]]:
    """读取最近 ``lookback_days`` 天的审计告警（已展开成扁平记录）。

    时间取自**文件名里的日期**而不是 mtime：审计日志会被打包 / 同步，
    mtime 会变成搬运时间，按 mtime 筛会把老日志当新的读进来。
    文件名解析不出日期的一律跳过并告警。

    坏行（非法 JSON）跳过，不让一行脏数据废掉整份报告。
    """
    d = Path(audit_dir) if audit_dir is not None else DEFAULT_AUDIT_DIR
    if not d.is_dir():
        logger.info("审计目录不存在: %s", d)
        return []

    ref = (today or datetime.now()).date()
    records: list[dict[str, Any]] = []

    for f in sorted(d.glob(f"{AUDIT_FILE_PREFIX}*.jsonl")):
        stamp = f.stem[len(AUDIT_FILE_PREFIX):]
        try:
            file_date = datetime.strptime(stamp, AUDIT_DATE_FORMAT).date()
        except ValueError:
            logger.warning("跳过无法解析日期的审计文件: %s", f.name)
            continue
        if (ref - file_date).days > lookback_days:
            continue
        records.extend(_read_audit_file(f))

    return records


def _read_audit_file(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.warning("读取 %s 失败: %s", path, exc)
        return []

    out: list[dict[str, Any]] = []
    for lineno, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("%s:%d 不是合法 JSON，跳过", path.name, lineno)
            continue
        if not isinstance(row, dict):
            continue

        nested = row.get("alerts")
        if isinstance(nested, list):
            # 每日汇总行：展开内部告警，汇总本身不再作为一条记录
            for item in nested:
                if not isinstance(item, dict):
                    continue
                alert = dict(item)
                alert.setdefault("date", row.get("date", ""))
                alert.setdefault("_source_file", path.name)
                out.append(alert)
            continue

        row.setdefault("_source_file", path.name)
        out.append(row)
    return out


def extract_factor_decay_alerts(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """筛出衰减类的 warning+ 告警。

    汇总行（没有 ``task`` 字段，只有 ``tasks_run`` / ``scheduler_event``）
    会被跳过 —— 它描述的是「今天跑了哪些任务」，不是一条告警，
    统计进来会让告警数虚高一倍。
    """
    alerts: list[dict[str, Any]] = []
    for r in records:
        task = r.get("task")
        if not task:
            continue
        if task not in _DECAY_TASKS:
            continue
        if str(r.get("severity", "")).lower() not in _ALERT_SEVERITIES:
            continue
        alerts.append(r)
    return alerts


def extract_psi_alerts(
    records: list[dict[str, Any]],
    threshold: float = DEFAULT_PSI_THRESHOLD,
) -> list[dict[str, Any]]:
    """筛出 PSI 达到阈值的记录（``psi >= threshold``）。

    只看 ``psi`` 数值，不看 severity：severity 是写入方按当时阈值打的标，
    而这份报告允许调用方用自己的阈值重新审视同一批数据。
    """
    alerts: list[dict[str, Any]] = []
    for r in records:
        psi = _as_float(r.get("psi"))
        if psi is None:
            continue
        if psi >= threshold:
            alerts.append(r)
    return alerts


def generate_decay_report(
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    output_dir: str | Path | None = None,
    audit_dir: str | Path | None = None,
    psi_threshold: float = DEFAULT_PSI_THRESHOLD,
) -> dict[str, Any]:
    """汇总最近 N 天的衰减/漂移告警，写 JSON 报告并返回摘要。

    审计目录为空时同样产出报告（各项计数为 0）—— 「这段时间没有告警」
    本身就是一条需要留痕的结论，不能和「报告没生成」混在一起。
    """
    out_dir = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
    aud_dir = Path(audit_dir) if audit_dir is not None else DEFAULT_AUDIT_DIR

    records = load_daily_monitor_alerts(
        lookback_days=lookback_days, audit_dir=aud_dir,
    )
    decay = extract_factor_decay_alerts(records)
    psi = extract_psi_alerts(records, threshold=psi_threshold)

    factor_counter = Counter(
        str(a.get("factor") or a.get("factor_name") or "unknown")
        for a in decay
        if a.get("factor") or a.get("factor_name")
    )
    feature_counter = Counter(
        str(a.get("feature") or a.get("feature_name") or "unknown")
        for a in psi
        if a.get("feature") or a.get("feature_name")
    )

    report: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "lookback_days": lookback_days,
        "audit_dir": str(aud_dir),
        "psi_threshold": psi_threshold,
        "records_scanned": len(records),
        "factor_decay_alerts": len(decay),
        "psi_alerts": len(psi),
        "top_factors": factor_counter.most_common(10),
        "top_features": feature_counter.most_common(10),
        "max_psi": max((_as_float(a.get("psi")) or 0.0 for a in psi), default=0.0),
        "decay_details": decay[:_MAX_DETAILS],
        "psi_details": psi[:_MAX_DETAILS],
    }

    report_path = out_dir / REPORT_FILENAME
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        report["report_path"] = str(report_path)
        logger.info(
            "报告已写出: %s（衰减告警 %d，PSI 告警 %d）",
            report_path, len(decay), len(psi),
        )
    except OSError as exc:
        # 写不出文件不该让调用方拿不到结论 —— 摘要照常返回，只是标记失败
        logger.error("写报告失败 %s: %s", report_path, exc)
        report["report_path"] = None
        report["write_error"] = str(exc)

    return report


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _send_alert(title: str, body: str) -> None:
    """发送告警 —— 统一飞书通道（与 aipha/runner/daily_utils.py 同口径）。"""
    try:
        from aipha.monitor.feishu_alert import send_feishu_alert

        send_feishu_alert(title, body, level="WARNING")
    except Exception as exc:  # noqa: BLE001 - 告警失败不影响报告产出
        logger.warning("飞书告警失败: %s", exc)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口，返回进程退出码（有 PSI 告警则 1，理由见模块 docstring）。"""
    parser = argparse.ArgumentParser(description="因子衰减 / 特征漂移周报")
    parser.add_argument(
        "--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS,
        help=f"回看天数（默认 {DEFAULT_LOOKBACK_DAYS}）",
    )
    parser.add_argument(
        "--output-dir", default=None, help="报告输出目录",
    )
    parser.add_argument(
        "--audit-dir", default=None, help="审计日志目录",
    )
    parser.add_argument(
        "--psi-threshold", type=float, default=DEFAULT_PSI_THRESHOLD,
        help=f"PSI 告警阈值（默认 {DEFAULT_PSI_THRESHOLD}）",
    )
    parser.add_argument(
        "--notify", action="store_true", help="有 PSI 告警时发送飞书告警",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出摘要")
    args = parser.parse_args(argv)

    report = generate_decay_report(
        lookback_days=args.lookback_days,
        output_dir=args.output_dir,
        audit_dir=args.audit_dir,
        psi_threshold=args.psi_threshold,
    )

    if args.json:
        summary = {k: v for k, v in report.items()
                   if k not in ("decay_details", "psi_details")}
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

    psi_count = int(report["psi_alerts"])
    decay_count = int(report["factor_decay_alerts"])
    logger.info(
        "最近 %d 天: 衰减告警 %d 条，PSI 告警 %d 条",
        args.lookback_days, decay_count, psi_count,
    )

    if psi_count == 0:
        return 0

    if args.notify:
        body = "\n".join(
            f"- {a.get('feature', '?')}: PSI={a.get('psi')}"
            for a in report["psi_details"][:20]
        )
        _send_alert(f"特征漂移告警（{psi_count} 条）", body)
    logger.warning(
        "存在 PSI 告警（最大 %.4f），模型输入分布已偏离训练集",
        float(report["max_psi"]),
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
