#!/usr/bin/env python3
"""模拟盘 NAV 跳变监控（cron 每日盘后）

读各策略的模拟盘状态，把**当前 NAV 与上一条记录的 NAV** 相比，超过阈值
就告警。无人值守跑模拟盘时，NAV 一夜之间跳 5% 以上通常不是行情，而是：

- 状态文件被并发写坏 / 只写了一半
- 持仓数量或价格单位错了一位（万元 vs 元）
- 重启后仓位没恢复，NAV 按纯现金重算

这些都是**账本层面**的问题，越早发现越好 —— 等到下单量按错误 NAV 算出来
就已经是真金白银了。

用法：
    python scripts/ops/nav_monitor.py
    python scripts/ops/nav_monitor.py --paper-state data/paper_state --threshold 0.03
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aipha.utils.paths import DATA_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_PAPER_STATE_DIR = DATA_DIR / "paper_state"
DEFAULT_THRESHOLD = 0.05

STATE_FILENAME = "state.json"
NAV_HISTORY_FILENAME = "nav_history.jsonl"


@dataclass
class NavCheckResult:
    """单个策略的 NAV 偏差检查结果。"""

    strategy_name: str = ""
    current_nav: float = 0.0
    baseline_nav: float = 0.0
    deviation_pct: float = 0.0
    threshold: float = DEFAULT_THRESHOLD
    passed: bool = True
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "current_nav": round(self.current_nav, 2),
            "baseline_nav": round(self.baseline_nav, 2),
            "deviation_pct": round(self.deviation_pct, 6),
            "threshold": self.threshold,
            "passed": self.passed,
            "message": self.message,
        }


def load_paper_nav(state_dir: str | Path) -> dict[str, Any] | None:
    """读一个策略目录的最新 NAV。

    优先 ``state.json``（引擎的权威状态），没有则退回
    ``nav_history.jsonl`` 的最后一行。两者都没有 / 读不出数字时返回 None
    —— **不返回 0.0**：把「读不到」和「净值归零」混成同一个值，会让
    监控要么天天误报要么永远不报。

    Returns:
        ``{"nav": float, "strategy_name": str, "date": str, "source": str,
        "positions": dict, "history": list[float]}``，或 None
    """
    d = Path(state_dir)
    if not d.is_dir():
        return None

    history = _load_nav_history(d / NAV_HISTORY_FILENAME)

    state_path = d / STATE_FILENAME
    if state_path.is_file():
        data = _read_json(state_path)
        if data is not None:
            nav = _coerce_nav(data)
            if nav is not None:
                return {
                    "nav": nav,
                    "strategy_name": str(
                        data.get("strategy_name") or d.name
                    ),
                    "date": str(data.get("date") or ""),
                    "source": STATE_FILENAME,
                    "positions": data.get("positions") or {},
                    "initial_capital": _as_float(
                        data.get("initial_capital")
                    ),
                    "history": history,
                }

    if history:
        return {
            "nav": history[-1],
            "strategy_name": d.name,
            "date": "",
            "source": NAV_HISTORY_FILENAME,
            "positions": {},
            "initial_capital": None,
            "history": history,
        }

    return None


def check_nav_deviation(
    current_nav: float,
    baseline_nav: float,
    threshold: float = DEFAULT_THRESHOLD,
    strategy_name: str = "",
) -> NavCheckResult:
    """比较当前 NAV 与基准 NAV，返回检查结果。

    ``deviation_pct`` 是**绝对值**：涨得离谱和跌得离谱一样可疑
    （账本错位通常两个方向都会出现）。

    基准非正数时判定为未通过：这种情况下偏差比例没有定义，
    而「基准 NAV ≤ 0」本身就是状态文件坏了的信号。
    """
    result = NavCheckResult(
        strategy_name=strategy_name,
        current_nav=float(current_nav),
        baseline_nav=float(baseline_nav),
        threshold=threshold,
    )

    if baseline_nav <= 0:
        # 不编造偏差数字：留 0.0 并在 message 里说明原因，
        # 免得报表里出现 inf / nan 之类没法序列化也没法读的值。
        result.passed = False
        result.message = f"基准 NAV 非正数（{baseline_nav}），无法计算偏差"
        return result

    result.deviation_pct = abs(current_nav - baseline_nav) / baseline_nav
    result.passed = result.deviation_pct <= threshold
    if not result.passed:
        result.message = (
            f"NAV 偏差 {result.deviation_pct:.2%} 超过阈值 {threshold:.2%}"
            f"（{baseline_nav:.2f} → {current_nav:.2f}）"
        )
    return result


def run_nav_check(
    paper_state_dir: str | Path = DEFAULT_PAPER_STATE_DIR,
    threshold: float = DEFAULT_THRESHOLD,
    book_dir: str | Path | None = None,
) -> list[NavCheckResult]:
    """遍历模拟盘状态目录，对每个策略做一次 NAV 偏差检查。

    目录不存在或没有任何可读状态时返回 ``[]`` —— 空列表表示
    「没什么可检查」，与「检查了且有问题」区分开。
    """
    root = Path(paper_state_dir)
    # 既支持指向父目录（每个子目录一个策略），也支持直接指向策略目录
    candidates: list[Path] = [root] if root.is_dir() else []
    if root.is_dir():
        candidates.extend(sorted(p for p in root.iterdir() if p.is_dir()))

    results: list[NavCheckResult] = []
    seen: set[str] = set()
    for d in candidates:
        info = load_paper_nav(d)
        if info is None:
            continue
        name = str(info["strategy_name"])
        if name in seen:
            continue
        seen.add(name)

        baseline = _pick_baseline(info)
        if baseline is None:
            logger.info("%s: 只有一条 NAV 记录，无基准可比，跳过", name)
            continue

        res = check_nav_deviation(
            info["nav"], baseline, threshold=threshold, strategy_name=name,
        )
        if res.passed:
            logger.info(
                "%s: NAV %.2f，偏差 %.2%%（阈值 %.2%%）",
                name, res.current_nav, res.deviation_pct * 100,
                threshold * 100,
            )
        else:
            logger.warning("%s: %s", name, res.message)
        results.append(res)
    if book_dir is not None:
        from aipha.research.paper_monitor import inspect_paper_books

        for row in inspect_paper_books(book_dir):
            name, history = row["portfolio_id"], row["nav_history"]
            if not history:
                results.append(NavCheckResult(strategy_name=name, threshold=threshold, passed=False, message=row["message"]))
            elif len(history) < 2:
                results.append(NavCheckResult(strategy_name=name, current_nav=history[-1]["nav"], threshold=threshold,
                                              passed=False, message="规范账本仅有一条 NAV，尚无日间比较基准"))
            elif any(not isinstance(item["nav"], (int, float)) or not math.isfinite(item["nav"]) or item["nav"] <= 0
                     for item in history[-2:]):
                results.append(NavCheckResult(strategy_name=name, threshold=threshold, passed=False, message="规范账本包含无效 NAV"))
            else:
                results.append(check_nav_deviation(history[-1]["nav"], history[-2]["nav"], threshold=threshold, strategy_name=name))
    return results


def _pick_baseline(info: dict[str, Any]) -> float | None:
    """选基准 NAV：优先上一条历史记录，否则初始资金。"""
    history = info.get("history") or []
    if len(history) >= 2:
        return float(history[-2])
    # state.json 的 NAV 与 history 最后一条可能是同一天，此时用倒数第一条
    if len(history) == 1 and info.get("source") == STATE_FILENAME:
        return float(history[-1])
    initial = info.get("initial_capital")
    if initial:
        return float(initial)
    return None


def _load_nav_history(path: Path) -> list[float]:
    """读 nav_history.jsonl 的 nav 列。坏行跳过，不让一行脏数据废掉整个监控。"""
    if not path.is_file():
        return []
    navs: list[float] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.warning("读取 %s 失败: %s", path, exc)
        return []
    for lineno, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("%s:%d 不是合法 JSON，跳过", path.name, lineno)
            continue
        nav = _coerce_nav(row)
        if nav is not None:
            navs.append(nav)
    return navs


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("读取 %s 失败: %s", path, exc)
        return None
    return data if isinstance(data, dict) else None


# NAV 在不同写入方那里叫不同名字（引擎 state 用 total_value，
# nav_history 用 nav），这里统一按优先级取第一个能转成数字的。
_NAV_KEYS = ("total_value", "nav", "total_nav", "equity")


def _coerce_nav(data: dict[str, Any]) -> float | None:
    for key in _NAV_KEYS:
        if key in data:
            value = _as_float(data[key])
            if value is not None:
                return value
    return None


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
    except Exception as exc:  # noqa: BLE001 - 告警失败不影响检查结论
        logger.warning("飞书告警失败: %s", exc)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口，返回进程退出码（有策略未通过则 1）。"""
    parser = argparse.ArgumentParser(description="模拟盘 NAV 跳变监控")
    parser.add_argument(
        "--paper-state", default=str(DEFAULT_PAPER_STATE_DIR),
        help="模拟盘状态目录",
    )
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_THRESHOLD,
        help="NAV 日间偏差阈值（默认 0.05）",
    )
    parser.add_argument("--book-dir", default=str(DATA_DIR / "research/paper"), help="规范 Paper SQLite 目录或单个账本")
    parser.add_argument(
        "--notify", action="store_true", help="有异常时发送飞书告警",
    )
    parser.add_argument(
        "--json", action="store_true", help="以 JSON 输出结果",
    )
    args = parser.parse_args(argv)

    results = run_nav_check(args.paper_state, threshold=args.threshold, book_dir=args.book_dir)

    if args.json:
        print(json.dumps(
            [r.to_dict() for r in results], ensure_ascii=False, indent=2,
        ))

    if not results:
        logger.info("没有可检查的模拟盘状态")
        return 0

    failed = [r for r in results if not r.passed]
    if not failed:
        logger.info("全部 %d 个策略 NAV 正常", len(results))
        return 0

    if args.notify:
        body = "\n".join(f"- {r.strategy_name}: {r.message}" for r in failed)
        _send_alert(f"模拟盘 NAV 异常（{len(failed)} 个策略）", body)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
