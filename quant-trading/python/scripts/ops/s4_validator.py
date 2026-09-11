#!/usr/bin/env python3
"""S4（模拟交易）连续运行天数验证

S4→S5（模拟盘→实盘）的门禁之一是「有足够长的、连续可信的模拟盘记录」。
``aipha/governance/paper_backtest_reconciliation.py:201`` 把这条量化成
``s4_min_months = 3.0``（约 60 个交易日）。本脚本从各组合的
``data/paper_state/<组合>/nav_history.jsonl`` 出发，回答：

- 有多少天的 NAV 记录是**可信**的（NAV > 0、日间变动不离谱）
- 这些天是否**连续**（中间没有长时间断档）
- 距离 ``required_days`` 还差多少

## 为什么按「连续」判定通过

``passed`` 取的是 ``consecutive_valid_days >= required_days``，而不是累计
天数。累计 60 天但中间断了三周的记录，说明引擎在这三周里没跑 —— 那段时间
的市场这个策略根本没经历过，用「累计」放行等于用不存在的历史给实盘背书。
这是两种口径里更保守的一种，S4 门禁应当保守。

## NAV 可信性判定

- ``nav <= 0`` 或非数字 → 无效（账本写坏 / 只写了一半）
- 与**上一条记录**相比日间变动超过 ``max_daily_change``（默认 20%）→ 无效。
  比较对象是上一条记录而不是上一条**有效**记录：NAV 跳到 1.5 之后再跳回
  1.02，两次跳变都不可信，级联标记才是对的 —— 只标第一次会让「跳上去
  再跳回来」的坏账本看起来只坏了一天。
- 上一条 NAV 本身 ``<= 0`` 时跳过变动检查（比例没有定义），不编造数字。

## 断档阈值

``max_gap_days`` 默认 10 个**自然日**：A 股最长休市（春节 / 国庆）约
8~10 天，阈值再小就会每年被假期重置一次连续计数，S4 永远攒不满。超过 10
天没有记录意味着引擎停了一周以上，此时重新起算是应该的。

用法::

    python scripts/ops/s4_validator.py
    python scripts/ops/s4_validator.py --required 60 --portfolio r115_combo
    python scripts/ops/s4_validator.py --json --notify
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from aipha.utils.paths import DATA_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_STATE_DIR = DATA_DIR / "paper_state"
NAV_HISTORY_FILENAME = "nav_history.jsonl"

# 与 paper_backtest_reconciliation 的 s4_min_months=3.0 对齐：
# 3 个月 × 244/12 ≈ 61 个交易日，取 60。
DEFAULT_REQUIRED_DAYS = 60

DEFAULT_MAX_DAILY_CHANGE = 0.20
DEFAULT_MAX_GAP_DAYS = 10

_DATE_FORMATS = ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d")


@dataclass
class S4ValidationResult:
    """单个组合的 S4 验证结果。"""

    portfolio_id: str = ""
    total_valid_days: int = 0
    consecutive_valid_days: int = 0
    longest_streak: int = 0
    first_valid_date: str = ""
    last_valid_date: str = ""
    required_days: int = DEFAULT_REQUIRED_DAYS
    passed: bool = False
    detail: str = ""
    invalid_days: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "PASSED" if self.passed else "IN PROGRESS"

    def to_dict(self) -> dict[str, Any]:
        return {
            "portfolio_id": self.portfolio_id,
            "status": self.status,
            "passed": self.passed,
            "total_valid_days": self.total_valid_days,
            "consecutive_valid_days": self.consecutive_valid_days,
            "longest_streak": self.longest_streak,
            "first_valid_date": self.first_valid_date,
            "last_valid_date": self.last_valid_date,
            "required_days": self.required_days,
            "detail": self.detail,
            "invalid_days": self.invalid_days,
            "gaps": self.gaps,
        }


def load_nav_history(portfolio_dir: str | Path) -> list[dict[str, Any]]:
    """读一个组合的 ``nav_history.jsonl``。

    文件缺失返回 ``[]``；坏行跳过并告警，不让一行脏数据废掉整次验证。
    """
    path = Path(portfolio_dir) / NAV_HISTORY_FILENAME
    if not path.is_file():
        return []

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.warning("读取 %s 失败: %s", path, exc)
        return []

    records: list[dict[str, Any]] = []
    for lineno, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("%s:%d 不是合法 JSON，跳过", path.name, lineno)
            continue
        if isinstance(row, dict):
            records.append(row)
    return records


def validate_s4(
    records: list[dict[str, Any]],
    portfolio_id: str = "",
    required_days: int = DEFAULT_REQUIRED_DAYS,
    max_daily_change: float = DEFAULT_MAX_DAILY_CHANGE,
    max_gap_days: int = DEFAULT_MAX_GAP_DAYS,
) -> S4ValidationResult:
    """验证一组 NAV 记录是否满足 S4 连续运行要求。

    Args:
        records: ``[{"date": "2025-01-01", "nav": 1.0}, ...]``，顺序无所谓
        portfolio_id: 组合名（仅用于输出）
        required_days: 要求的连续有效天数
        max_daily_change: 日间 NAV 变动上限（超出即判该日无效）
        max_gap_days: 允许的最大断档自然日数（超出即重置连续计数）

    Returns:
        ``S4ValidationResult``。无记录时 ``passed=False``、各计数为 0 ——
        「没有记录」不等于「通过」。
    """
    result = S4ValidationResult(
        portfolio_id=portfolio_id, required_days=required_days,
    )

    ordered = _dedup_and_sort(records, result)
    if not ordered:
        result.detail = "NO DATA: 没有可用的 NAV 记录"
        return result

    prev_nav: float | None = None
    prev_date: datetime | None = None
    streak = 0

    for date_str, dt, row in ordered:
        nav = _as_float(row.get("nav"))
        reason = _invalid_reason(nav, prev_nav, max_daily_change)

        if reason is not None:
            result.invalid_days.append(
                {"date": date_str, "nav": row.get("nav"), "reason": reason}
            )
            # 无效日不计入有效天数，也不接续连续计数
            streak = 0
        else:
            # 断档检查：只在两个有效日之间做，否则「无效日 + 断档」会被
            # 记两笔，同一件事在报告里出现两次。
            if prev_date is not None:
                gap_days = (dt - prev_date).days
                if gap_days > max_gap_days:
                    result.gaps.append({
                        "from": prev_date.strftime("%Y-%m-%d"),
                        "to": date_str,
                        "days": gap_days,
                    })
                    streak = 0
            streak += 1
            result.total_valid_days += 1
            result.longest_streak = max(result.longest_streak, streak)
            if not result.first_valid_date:
                result.first_valid_date = date_str
            result.last_valid_date = date_str
            prev_date = dt

        # prev_nav 跟着**每一条**记录走（含无效条），见模块 docstring
        if nav is not None:
            prev_nav = nav

    result.consecutive_valid_days = streak
    result.passed = streak >= required_days

    pct = (streak / required_days * 100) if required_days > 0 else 100.0
    result.detail = (
        f"{'PASSED' if result.passed else 'IN PROGRESS'}: "
        f"{streak}/{required_days} ({pct:.0f}%)"
    )
    if result.invalid_days:
        result.detail += f"，无效 {len(result.invalid_days)} 天"
    if result.gaps:
        result.detail += f"，断档 {len(result.gaps)} 次"
    return result


def _dedup_and_sort(
    records: list[dict[str, Any]],
    result: S4ValidationResult,
) -> list[tuple[str, datetime, dict[str, Any]]]:
    """按日期去重（同日保留最后一条）并升序排序。

    同一天出现两条通常是当天重跑了一次引擎 —— 后写的那条才是最终账本，
    所以保留最后一条而不是第一条。
    """
    by_date: dict[str, tuple[datetime, dict[str, Any]]] = {}
    for row in records:
        raw = row.get("date") or row.get("trade_date")
        dt = _parse_date(raw)
        if dt is None:
            result.invalid_days.append({
                "date": str(raw), "nav": row.get("nav"),
                "reason": f"日期无法解析: {raw!r}",
            })
            continue
        key = dt.strftime("%Y-%m-%d")
        by_date[key] = (dt, row)  # 同日后来者覆盖

    return [
        (key, by_date[key][0], by_date[key][1]) for key in sorted(by_date)
    ]


def _invalid_reason(
    nav: float | None,
    prev_nav: float | None,
    max_daily_change: float,
) -> str | None:
    """返回该日无效的原因；有效时返回 None。"""
    if nav is None:
        return "NAV 不是数字"
    if nav <= 0:
        return f"NAV 非正数: {nav}"
    if prev_nav is not None and prev_nav > 0:
        change = nav / prev_nav - 1
        if abs(change) > max_daily_change:
            return (
                f"change={change:+.2%} 超过阈值 "
                f"{max_daily_change:.0%}（{prev_nav:.4f} → {nav:.4f}）"
            )
    return None


def check_all_portfolios(
    state_dir: str | Path = DEFAULT_STATE_DIR,
    required_days: int = DEFAULT_REQUIRED_DAYS,
    portfolio_filter: str | None = None,
    max_daily_change: float = DEFAULT_MAX_DAILY_CHANGE,
    max_gap_days: int = DEFAULT_MAX_GAP_DAYS,
) -> list[S4ValidationResult]:
    """扫描状态目录，逐个组合做 S4 验证。

    没有 ``nav_history.jsonl`` 的子目录**也会**出一条结果（``NO DATA``）：
    这种目录通常是引擎建了目录却从未成功写过账本，静默跳过会让它在
    S4 看板上凭空消失。
    """
    root = Path(state_dir)
    if not root.is_dir():
        logger.info("状态目录不存在: %s", root)
        return []

    results: list[S4ValidationResult] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if portfolio_filter and d.name != portfolio_filter:
            continue
        results.append(validate_s4(
            load_nav_history(d),
            portfolio_id=d.name,
            required_days=required_days,
            max_daily_change=max_daily_change,
            max_gap_days=max_gap_days,
        ))
    return results


def format_s4_summary(results: list[S4ValidationResult]) -> str:
    """把验证结果排成 Markdown 摘要（飞书 / 报告直接可用）。"""
    if not results:
        return (
            "# S4 验证摘要\n\n"
            "No portfolios found —— 状态目录下没有可验证的组合。\n"
        )

    passed = [r for r in results if r.passed]
    lines = [
        "# S4 验证摘要",
        "",
        f"生成时间: {datetime.now().isoformat(timespec='seconds')}",
        f"通过 {len(passed)}/{len(results)} 个组合",
        "",
        "| 组合 | 状态 | 连续/要求 | 累计有效 | 最长连续 | 区间 | 详情 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        span = (
            f"{r.first_valid_date} ~ {r.last_valid_date}"
            if r.first_valid_date else "-"
        )
        lines.append(
            f"| {r.portfolio_id} | {r.status} "
            f"| {r.consecutive_valid_days}/{r.required_days} "
            f"| {r.total_valid_days} | {r.longest_streak} | {span} "
            f"| {r.detail} |"
        )

    blockers = [r for r in results if not r.passed]
    if blockers:
        lines += ["", "## 未通过的组合", ""]
        for r in blockers:
            lines.append(f"- **{r.portfolio_id}**: {r.detail}")
            for bad in r.invalid_days[:5]:
                lines.append(f"  - 无效日 {bad['date']}: {bad['reason']}")
            for gap in r.gaps[:5]:
                lines.append(
                    f"  - 断档 {gap['from']} → {gap['to']}（{gap['days']} 天）"
                )
    return "\n".join(lines) + "\n"


def send_s4_notification(results: list[S4ValidationResult]) -> bool:
    """把摘要推到飞书。通道不可用时返回 False，不抛异常。

    告警通道失败**不能**改变验证结论 —— 发不出去是运维问题，
    而这个脚本的结论要照常写进日志和退出码。
    """
    try:
        from aipha.monitor.feishu_alert import send_feishu_alert
    except Exception as exc:  # noqa: BLE001 - 无告警依赖时也要能跑
        logger.warning("飞书告警模块不可用: %s", exc)
        return False

    blockers = [r for r in results if not r.passed]
    title = (
        f"S4 验证: {len(results) - len(blockers)}/{len(results)} 通过"
    )
    try:
        return bool(send_feishu_alert(
            title,
            format_s4_summary(results),
            level="WARNING" if blockers else "INFO",
        ))
    except Exception as exc:  # noqa: BLE001
        logger.warning("飞书告警失败: %s", exc)
        return False


def _parse_date(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    """CLI 入口，返回进程退出码（任一组合未通过则 1）。"""
    parser = argparse.ArgumentParser(description="S4 连续运行天数验证")
    parser.add_argument(
        "--state-dir", default=str(DEFAULT_STATE_DIR), help="模拟盘状态根目录",
    )
    parser.add_argument(
        "--required", type=int, default=DEFAULT_REQUIRED_DAYS,
        help=f"要求的连续有效天数（默认 {DEFAULT_REQUIRED_DAYS}）",
    )
    parser.add_argument(
        "--portfolio", default=None, help="只验证指定组合",
    )
    parser.add_argument(
        "--max-daily-change", type=float, default=DEFAULT_MAX_DAILY_CHANGE,
        help=f"日间 NAV 变动上限（默认 {DEFAULT_MAX_DAILY_CHANGE}）",
    )
    parser.add_argument(
        "--max-gap-days", type=int, default=DEFAULT_MAX_GAP_DAYS,
        help=f"允许的最大断档自然日（默认 {DEFAULT_MAX_GAP_DAYS}）",
    )
    parser.add_argument(
        "--notify", action="store_true", help="把摘要推送到飞书",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = parser.parse_args(argv)

    results = check_all_portfolios(
        state_dir=args.state_dir,
        required_days=args.required,
        portfolio_filter=args.portfolio,
        max_daily_change=args.max_daily_change,
        max_gap_days=args.max_gap_days,
    )

    if args.json:
        print(json.dumps(
            [r.to_dict() for r in results], ensure_ascii=False, indent=2,
        ))
    else:
        print(format_s4_summary(results))

    if not results:
        # 没有组合可验证 ≠ S4 通过；退出码给 0 是因为这属于配置/挂载问题，
        # 不是「某个组合没达标」，日志里明确写出来供人排查。
        logger.warning("没有可验证的组合: %s", args.state_dir)
        return 0

    if args.notify:
        send_s4_notification(results)

    blockers = [r for r in results if not r.passed]
    for r in results:
        (logger.info if r.passed else logger.warning)(
            "%s: %s", r.portfolio_id, r.detail,
        )
    if blockers:
        logger.warning("%d/%d 个组合未达 S4 要求", len(blockers), len(results))
        return 1
    logger.info("全部 %d 个组合满足 S4 要求", len(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
