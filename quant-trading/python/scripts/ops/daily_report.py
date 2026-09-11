#!/usr/bin/env python3
"""每日组合报告生成器（B4 / R88）

把各组合散落的三份落盘数据汇成一份日报：

| 数据源 | 谁写的 | 说明 |
|---|---|---|
| ``live_metrics.json`` | ``aipha/monitor/metrics_collector.py`` | 实时引擎的盯市指标（NAV/回撤/Sharpe），**最可信** |
| ``nav_snapshot.json`` | EOD 盯市快照 | 按当日收盘价重估的 NAV |
| ``state.json`` / ``paper_state.json`` | 各引擎的账本 | 现金 + 持仓（成本价） |

## 优先级：metrics > nav_snapshot > state 成本价

这个顺序不是随便排的。``state.json`` 里的 ``avg_cost`` 是**成本**，不是市
价 —— 用它算出来的「NAV」等于假设所有持仓的浮动盈亏都是 0。所以它只是
兜底口径，报告里会用 ``nav.is_mark_to_market=False`` 明确标出来，markdown
里也带脚注。把成本口径的数字和盯市口径的数字混在一张表里而不加区分，是
「模拟盘看着不错、实盘对不上」的经典来源。

## 两种 state 格式

- StateManager 格式：``{"account": {"cash", "frozen_cash", "positions"}, "meta": {...}}``
- R106/R107 扁平格式：``{"profile_name", "initial_capital", "cash", "positions"}``，
  持仓字段是 ``code`` / ``shares`` 而不是 ``symbol`` / ``qty``

:func:`_normalize_state` 把两种都拍平成一种，其余函数只认归一化后的形状。

用法::

    python scripts/ops/daily_report.py                      # 扫全部组合
    python scripts/ops/daily_report.py --portfolio r106_attack --json
    python scripts/ops/daily_report.py --markdown-out output/reports --notify
"""

from __future__ import annotations

import argparse
import json
import logging
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
# 与 MetricsConfig.output_dir 默认值一致（aipha/monitor/metrics_collector.py:39）
DEFAULT_METRICS_DIR = DATA_DIR / "metrics"

# 账本文件名优先级：与 aipha/api/trading/paper_state_reader.py:35、
# aipha/governance/paper_ops_health.py:145、scripts/ops/state_stale_checker.py
# 保持同一顺序。EOD 组合写 state.json、实时引擎写 paper_state.json；日报是
# 收盘后的口径，所以 EOD 的账本优先。
STATE_FILENAMES = ("state.json", "paper_state.json")
METRICS_FILENAME = "live_metrics.json"
NAV_SNAPSHOT_FILENAME = "nav_snapshot.json"
NAV_HISTORY_FILENAME = "nav_history.jsonl"


# ──────────────────────── 读盘 ────────────────────────
def load_state_file(portfolio_dir: str | Path) -> dict[str, Any] | None:
    """读一个组合目录下的账本，找不到或读不出来返回 ``None``。"""
    d = Path(portfolio_dir)
    for name in STATE_FILENAMES:
        path = d / name
        if not path.is_file():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # 不抛：一个组合的账本坏了不该让整份日报生成不出来，
            # 但必须留下 ERROR，否则这个组合会安静地从报告里消失。
            logger.error("账本读取失败 %s: %s", path, exc)
            return None
    return None


def load_metrics_file(
    portfolio_id: str,
    metrics_dir: str | Path = DEFAULT_METRICS_DIR,
) -> dict[str, Any] | None:
    """读组合的 ``live_metrics.json``：先找 ``<metrics_dir>/<组合>/``，再退全局。

    退全局是为了兼容单组合部署（只跑一个引擎时 metrics 直接写在
    ``data/metrics/live_metrics.json``）。多组合部署下这份全局文件属于谁并
    不确定，所以子目录永远优先。
    """
    root = Path(metrics_dir)
    for path in (root / portfolio_id / METRICS_FILENAME, root / METRICS_FILENAME):
        if not path.is_file():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.error("metrics 读取失败 %s: %s", path, exc)
            return None
    return None


def load_nav_snapshot(portfolio_dir: str | Path) -> dict[str, Any] | None:
    """读组合目录下的 ``nav_snapshot.json``（EOD 盯市快照），没有则 ``None``。"""
    path = Path(portfolio_dir) / NAV_SNAPSHOT_FILENAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.error("nav_snapshot 读取失败 %s: %s", path, exc)
        return None


# ──────────────────────── 归一化 ────────────────────────
def _normalize_state(
    state: dict[str, Any] | None,
) -> tuple[float, float, list[dict[str, Any]], str | None]:
    """把两种 state 格式拍平。

    Returns:
        ``(cash, initial_capital, positions, strategy_name)``。
        ``positions`` 每条保证含 ``symbol`` / ``qty`` / ``avg_cost``，
        原有字段保留。``initial_capital`` 缺失时为 ``0.0``（调用方需要自己
        判断能不能算收益率 —— 拿 0 去除会得出无穷大的「收益」）。
    """
    if not state:
        return 0.0, 0.0, [], None

    account = state.get("account")
    if isinstance(account, dict):
        # StateManager 格式
        meta = state.get("meta") or {}
        # 冻结现金仍是账户里的钱（只是被挂单占住），不计入会让 NAV 少一块
        cash = _as_float(account.get("cash")) + _as_float(account.get("frozen_cash"))
        initial = _as_float(
            meta.get("initial_capital", state.get("initial_capital", 0.0))
        )
        raw_positions = account.get("positions") or []
        name = meta.get("strategy_name") or state.get("profile_name")
    else:
        # R106/R107 扁平格式
        cash = _as_float(state.get("cash"))
        initial = _as_float(state.get("initial_capital"))
        raw_positions = state.get("positions") or []
        name = state.get("profile_name") or state.get("strategy_name")

    positions: list[dict[str, Any]] = []
    for raw in raw_positions:
        if not isinstance(raw, dict):
            logger.warning("跳过异常持仓记录: %r", raw)
            continue
        pos = dict(raw)
        pos["symbol"] = raw.get("symbol") or raw.get("code") or ""
        qty = raw.get("qty", raw.get("shares", raw.get("volume", 0)))
        pos["qty"] = _as_float(qty)
        pos["avg_cost"] = _as_float(raw.get("avg_cost", raw.get("cost", 0.0)))
        positions.append(pos)

    return cash, initial, positions, name


def _as_float(val: Any, default: float = 0.0) -> float:
    try:
        out = float(val)
    except (TypeError, ValueError):
        return default
    return out


def compute_cost_basis_value(state: dict[str, Any] | None) -> float:
    """成本口径的账户价值 = 现金 + Σ 股数 × 成本价。

    **不是盯市价值**：浮动盈亏在这个口径下恒为 0。只在既没有 metrics 也没有
    nav_snapshot 时兜底使用。
    """
    cash, _initial, positions, _name = _normalize_state(state)
    return cash + sum(p["qty"] * p["avg_cost"] for p in positions)


# ──────────────────────── 组装 ────────────────────────
def build_daily_report(
    portfolio_id: str,
    state: dict[str, Any] | None,
    metrics: dict[str, Any] | None,
    report_date: str,
    *,
    nav_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把一个组合的三份数据组装成一条日报记录。

    NAV 取值优先级 metrics > nav_snapshot > state 成本价（见模块 docstring）。
    持仓/现金**始终**取自 state —— metrics 只报口径指标，不报明细。

    Returns:
        ``{"portfolio_id", "report_date", "generated_at", "strategy_name",
        "nav", "risk", "trading", "cash", "n_positions", "positions",
        "data_sources"}``。``risk`` / ``trading`` 只有 metrics 在时才有值。
    """
    cash, initial, positions, name = _normalize_state(state)
    sources: list[str] = []

    nav_current = 0.0
    nav_initial = initial
    nav_peak: float | None = None
    total_return_pct: float | None = None
    daily_return_pct: float | None = None
    risk: dict[str, Any] | None = None
    trading: dict[str, Any] | None = None
    mark_to_market = False

    m_nav = (metrics or {}).get("nav") or {}
    if metrics and m_nav.get("current") is not None:
        sources.append("live_metrics")
        nav_current = _as_float(m_nav.get("current"))
        nav_initial = _as_float(m_nav.get("initial"), initial) or initial
        nav_peak = _as_float(m_nav.get("peak")) or None
        total_return_pct = _opt_float(m_nav.get("total_return_pct"))
        daily_return_pct = _opt_float(m_nav.get("daily_return_pct"))
        risk = metrics.get("risk")
        trading = metrics.get("trading")
        name = metrics.get("strategy_name") or name
        mark_to_market = True
    elif nav_snapshot and nav_snapshot.get("nav") is not None:
        sources.append("nav_snapshot")
        nav_current = _as_float(nav_snapshot.get("nav"))
        nav_initial = _as_float(nav_snapshot.get("initial_capital"), initial) or initial
        total_return_pct = _opt_float(nav_snapshot.get("total_return_pct"))
        mark_to_market = True
    elif state:
        sources.append("state_cost_basis")
        nav_current = compute_cost_basis_value(state)
    else:
        sources.append("none")
        logger.warning("%s 三份数据都没有，报告为空壳", portfolio_id)

    if state and "state_cost_basis" not in sources:
        sources.append("state_positions")

    if total_return_pct is None and nav_initial > 0 and nav_current > 0:
        total_return_pct = (nav_current / nav_initial - 1) * 100

    return {
        "portfolio_id": portfolio_id,
        "report_date": report_date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "strategy_name": name,
        "nav": {
            "current": nav_current,
            "initial": nav_initial,
            "peak": nav_peak,
            "total_return_pct": total_return_pct,
            "daily_return_pct": daily_return_pct,
            # False = 成本口径，浮动盈亏未计入，见模块 docstring
            "is_mark_to_market": mark_to_market,
        },
        "risk": risk,
        "trading": trading,
        "cash": cash,
        "n_positions": len(positions),
        "positions": positions,
        "data_sources": sources,
    }


def _opt_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ──────────────────────── 落盘 ────────────────────────
def append_nav_history(portfolio_dir: str | Path, report: dict[str, Any]) -> Path | None:
    """把日报的 NAV 追加一行到 ``nav_history.jsonl``，返回文件路径。

    ``nav <= 0`` 时**什么都不写**并返回 ``None``：这条线是
    ``scripts/ops/s4_validator.py`` 判 S4 门禁的原始数据，而它把 ``nav <= 0``
    当作账本写坏、会**打断连续天数**。数据缺口写一个 0 进去，等于把三个月
    的模拟盘记录从中间截断，比不写严重得多。

    同一天重复跑会追加多行 —— 这是刻意的：纯追加是 O(1) 且崩溃安全的，
    而 ``s4_validator`` / ``nav_monitor`` 读取时按日期去重（保留最后一条）。
    为了消掉一个下游已经处理的重复，去改写整份账本，风险换来的收益是负的。
    """
    nav = _as_float((report.get("nav") or {}).get("current"))
    if nav <= 0:
        logger.warning(
            "%s NAV=%.2f，不写 nav_history（避免打断 S4 连续天数）",
            report.get("portfolio_id", "?"), nav,
        )
        return None

    d = Path(portfolio_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / NAV_HISTORY_FILENAME
    record = {
        "date": report.get("report_date"),
        "nav": nav,
        "total_return_pct": (report.get("nav") or {}).get("total_return_pct"),
        "generated_at": report.get("generated_at"),
        "source": ",".join(report.get("data_sources") or []),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def format_markdown_report(reports: list[dict[str, Any]]) -> str:
    """把多个组合的日报渲染成一张 markdown 表。"""
    if not reports:
        return "# Aipha-Q 每日组合报告\n\n无可用组合数据。\n"

    date = reports[0].get("report_date", "")
    lines = [
        f"# Aipha-Q 每日组合报告 — {date}",
        "",
        f"组合数: {len(reports)}  |  生成时间: "
        f"{datetime.now().isoformat(timespec='seconds')}",
        "",
        "| 组合 | NAV | 初始 | 累计收益 | 当日 | 现金 | 持仓 | 回撤 | 口径 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]

    has_cost_basis = False
    for r in sorted(reports, key=lambda x: str(x.get("portfolio_id"))):
        nav = r.get("nav") or {}
        risk = r.get("risk") or {}
        mtm = bool(nav.get("is_mark_to_market"))
        has_cost_basis = has_cost_basis or not mtm
        lines.append(
            "| {pid} | {cur} | {init} | {ret} | {day} | {cash} | {npos} | {dd} | {src} |".format(
                pid=r.get("portfolio_id", "?"),
                cur=f"{_as_float(nav.get('current')):,.2f}",
                init=f"{_as_float(nav.get('initial')):,.2f}",
                ret=_pct(nav.get("total_return_pct")),
                day=_pct(nav.get("daily_return_pct")),
                cash=f"{_as_float(r.get('cash')):,.2f}",
                npos=r.get("n_positions", 0),
                dd=_pct(risk.get("current_drawdown_pct"), signed=False),
                src="盯市" if mtm else "**成本价**",
            )
        )

    if has_cost_basis:
        lines += [
            "",
            "> 标注「成本价」的组合既没有 live_metrics 也没有 nav_snapshot，"
            "NAV 按持仓成本估算，**浮动盈亏未计入**，不可与盯市口径直接比较。",
        ]
    return "\n".join(lines) + "\n"


def _pct(val: Any, *, signed: bool = True) -> str:
    f = _opt_float(val)
    if f is None:
        return "—"
    return f"{f:+.2f}%" if signed else f"{f:.2f}%"


# ──────────────────────── 编排 ────────────────────────
def generate_daily_reports(
    state_dir: str | Path = DEFAULT_STATE_DIR,
    metrics_dir: str | Path = DEFAULT_METRICS_DIR,
    *,
    portfolio: str | None = None,
    report_date: str | None = None,
    write_json: bool = True,
    write_nav_history: bool = True,
) -> list[dict[str, Any]]:
    """扫描 ``state_dir`` 下的组合并生成日报。

    每个组合会：
    1. 读账本 / metrics / nav_snapshot → :func:`build_daily_report`
    2. 追加 ``nav_history.jsonl``（写在**组合的 state 目录**里，
       ``s4_validator`` 就是从那里读的）
    3. 写 ``<metrics_dir>/<组合>/daily_report_<日期>.json``

    没有任何数据源的目录会被跳过（空目录不该在日报里占一行 0）。

    Args:
        state_dir: 组合状态根目录，其下每个子目录是一个组合
        metrics_dir: metrics 根目录，也是 JSON 报告的输出位置
        portfolio: 只处理这一个组合
        report_date: 报告日期，默认今天（``YYYY-MM-DD``）
        write_json: 是否落盘每组合 JSON
        write_nav_history: 是否追加 nav_history.jsonl
    """
    root = Path(state_dir)
    m_root = Path(metrics_dir)
    date = report_date or datetime.now().strftime("%Y-%m-%d")

    if not root.is_dir():
        logger.warning("状态根目录不存在: %s", root)
        return []

    dirs = sorted(p for p in root.iterdir() if p.is_dir())
    if portfolio:
        dirs = [p for p in dirs if p.name == portfolio]
        if not dirs:
            logger.warning("未找到组合 %s（在 %s 下）", portfolio, root)

    reports: list[dict[str, Any]] = []
    for d in dirs:
        pid = d.name
        state = load_state_file(d)
        metrics = load_metrics_file(pid, m_root)
        snapshot = load_nav_snapshot(d)
        if state is None and metrics is None and snapshot is None:
            logger.info("%s 无任何数据源，跳过", pid)
            continue

        report = build_daily_report(
            pid, state, metrics, date, nav_snapshot=snapshot,
        )
        reports.append(report)

        if write_nav_history:
            append_nav_history(d, report)
        if write_json:
            out_dir = m_root / pid
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"daily_report_{date.replace('-', '')}.json"
            out_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            logger.info("%s 报告已写入 %s", pid, out_path)

    logger.info("生成 %d 份组合日报（日期 %s）", len(reports), date)
    return reports


def _send_alert(title: str, body: str) -> None:
    """发汇总告警 —— 走通用飞书通道。

    刻意**不用** ``send_trade_daily_report``：那是各 paper 引擎推自己当日
    成交日报的入口（§13.14），引擎收盘后已经推过一次。这个脚本推的是跨组合
    汇总，用同一个通道会让每天多出一轮重复卡片，真正的告警就被淹了。
    """
    try:
        from aipha.monitor.feishu_alert import send_feishu_alert

        send_feishu_alert(title, body, level="INFO")
    except Exception as exc:  # noqa: BLE001 - 告警失败不影响报告已落盘的事实
        logger.warning("飞书推送失败: %s", exc)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口，返回进程退出码。"""
    parser = argparse.ArgumentParser(description="每日组合报告生成器")
    parser.add_argument(
        "--state-dir", default=str(DEFAULT_STATE_DIR), help="组合状态根目录",
    )
    parser.add_argument(
        "--metrics-dir", default=str(DEFAULT_METRICS_DIR),
        help="metrics 根目录（也是 JSON 报告输出位置）",
    )
    parser.add_argument("--portfolio", default=None, help="只处理指定组合")
    parser.add_argument("--date", default=None, help="报告日期 YYYY-MM-DD，默认今天")
    parser.add_argument(
        "--markdown-out", default=None,
        help="markdown 报告输出目录；不给则只打印到 stdout（避免测试/临时"
             "运行往仓库里写文件）",
    )
    parser.add_argument(
        "--no-nav-history", action="store_true",
        help="不追加 nav_history.jsonl（补跑历史日期时用，避免污染 S4 序列）",
    )
    parser.add_argument("--notify", action="store_true", help="推送汇总到飞书")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出全部报告")
    args = parser.parse_args(argv)

    reports = generate_daily_reports(
        state_dir=args.state_dir,
        metrics_dir=args.metrics_dir,
        portfolio=args.portfolio,
        report_date=args.date,
        write_nav_history=not args.no_nav_history,
    )

    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))

    markdown = format_markdown_report(reports)
    if args.markdown_out:
        out_dir = Path(args.markdown_out)
        out_dir.mkdir(parents=True, exist_ok=True)
        date = (reports[0]["report_date"] if reports
                else (args.date or datetime.now().strftime("%Y-%m-%d")))
        md_path = out_dir / f"daily_report_{date.replace('-', '')}.md"
        md_path.write_text(markdown, encoding="utf-8")
        logger.info("markdown 报告已写入 %s", md_path)
    elif not args.json:
        print(markdown)

    if not reports:
        # 0 而不是 1：没有组合在跑不是这个脚本的错误。「组合本该在跑却没跑」
        # 由 state_stale_checker 负责判定，两件事不要混在一个退出码里。
        logger.warning("没有可报告的组合: %s", args.state_dir)
        return 0

    if args.notify:
        _send_alert(f"每日组合报告（{len(reports)} 个组合）", markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
