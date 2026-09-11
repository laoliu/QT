"""用自己的交易记录校准追高上限 ``max_chase_pct``

``RecommendationConfig.max_chase_pct`` 默认 0.03、BEAR 乘数默认 0.5，
都是先验值。本脚本从 Paper/Live 的交易记录反推它们该是多少。

用法::

    python scripts/calibrate_chase_cap.py --state state/paper_combo.json

    # 用整段持有收益而非首日表现作为判据
    python scripts/calibrate_chase_cap.py --state state/paper_combo.json \\
        --outcome forward_total_bps --forward-days 10

    # 分市场状态校准乘数（需要一份 日期→状态 的映射）
    python scripts/calibrate_chase_cap.py --state state/paper_combo.json \\
        --regime-file state/regime_history.json

    # 旧记录没有 signal_price 时，从历史日线重建后再校准
    python scripts/calibrate_chase_cap.py --state state/paper_combo.json \\
        --backfill-signal-price

数据要求（缺一不可）:
    1. ``signal_price`` — 推荐/信号当时的参考价。Paper 与 Live 引擎
       自 2026-09 起随成交一并落盘；更早的记录没有这个字段，默认会被跳过，
       可用 ``--backfill-signal-price`` 从历史日线重建（见下）。
    2. 成交价 — 交易记录里的 ``price``。
    3. 成交后的日收盘价 —— 本脚本按 ``--forward-days`` 自行拉取。

样本不足 30 笔时脚本不会给新数字，只报还差多少 —— 拿 n<30 去改 cap
与凭直觉改没有区别。

关于重建（``--backfill-signal-price``）
    引擎记的 ``signal_price`` 就是**信号日收盘价**：
    ``generate_order_intents`` 取的 ``prices`` 来自 ``fetch_prices``，
    按代码前缀分派到 daily / cb_daily / fund_daily 的 ``close``。
    所以重建不是估算 —— 本脚本调用同一个 ``fetch_prices``，
    对同一个信号日取同一个字段，拿到的是同一个数。两处例外：

    - 交易记录里带 ``signal_date`` 时按它取价，结果精确（除非 TuShare
      事后修订了历史数据）。
    - 没有 ``signal_date`` 只能按「T 日出信号、T+1 开盘成交」倒推成交日的
      前一交易日。挂单遇停牌/涨停会在 ``pending_orders`` 里多压几天
      （见 ``_MAX_PENDING_AGE_DAYS``），这种延迟成交倒推出的信号日是错的。
      报告会分别给出精确与倒推的笔数。

    重建值一律标记 ``signal_price_source="reconstructed"``，输出与 JSON
    里都与原生记录分开计数。默认不重建 —— 拿重建数据去改真实风控参数
    之前，你该先知道有多少笔是重建的。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# aipha 包在 python/ 下，不在 python/src/ —— 直接跑脚本时需要这一行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aipha.advisory.chase_calibration import (  # noqa: E402
    MIN_SAMPLE_FOR_CALIBRATION,
    OUTCOME_METRICS,
    calibrate_chase_cap,
    calibrate_regime_multipliers,
    format_chase_calibration,
    format_regime_calibration,
)
from aipha.advisory.shortfall import ExecutionRecord  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_FORWARD_DAYS = 5

SOURCE_RECORDED = "recorded"
SOURCE_RECONSTRUCTED = "reconstructed"

# 重建取价时的重试参数 —— 这是离线分析，不必像盘中那样死等 30×2s。
# 某个信号日缺数据就跳过该笔，不该让整个脚本挂在那里。
_BACKFILL_RETRIES = 2
_BACKFILL_INTERVAL = 0.5


# ── 交易记录读取 ──────────────────────────────────────────────


def load_trades(path: Path) -> list[dict[str, Any]]:
    """从状态文件读出交易记录，兼容各版本字段名。

    接受 ``{"trades": [...]}`` 或裸列表。历史上 code/ts_code、
    date/trade_date 两种命名都出现过。
    """
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    trades = raw.get("trades", []) if isinstance(raw, dict) else raw
    if not isinstance(trades, list):
        raise ValueError(f"{path}: 未找到交易记录列表")

    out: list[dict[str, Any]] = []
    for t in trades:
        if not isinstance(t, dict):
            continue
        out.append({
            "code": t.get("code") or t.get("ts_code") or "",
            "side": str(t.get("side", "")).lower(),
            "price": t.get("price"),
            "shares": int(t.get("shares", 0) or 0),
            "cost": float(t.get("cost", 0) or 0),
            "date": str(t.get("date") or t.get("trade_date") or ""),
            "signal_price": t.get("signal_price"),
            "signal_date": str(t.get("signal_date", "") or ""),
            "component": t.get("component", ""),
            "status": t.get("status", ""),
            # 原生记录 / 重建 / 无。回填后才有值，用于把两类样本分开计数。
            "signal_price_source": (
                SOURCE_RECORDED if _positive(t.get("signal_price")) else ""
            ),
        })
    return out


def summarize_coverage(trades: list[dict[str, Any]]) -> dict[str, int]:
    """统计有多少笔能用 —— 先把「数据够不够」讲清楚。

    ``recorded`` / ``reconstructed`` 把原生落盘与事后重建的样本分开：
    两者可信度不同，混在一个总数里看不出来。
    """
    buys = [t for t in trades if t["side"] == "buy"]
    with_signal = [t for t in buys if _positive(t.get("signal_price"))]
    with_price = [t for t in with_signal if _positive(t.get("price"))]
    return {
        "total": len(trades),
        "buys": len(buys),
        "with_signal_price": len(with_signal),
        "usable": len(with_price),
        "recorded": sum(
            1 for t in with_price
            if t.get("signal_price_source") != SOURCE_RECONSTRUCTED
        ),
        "reconstructed": sum(
            1 for t in with_price
            if t.get("signal_price_source") == SOURCE_RECONSTRUCTED
        ),
    }


def _positive(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


# ── signal_price 重建 ────────────────────────────────────────


@dataclass
class BackfillStats:
    """重建结果 —— 补上了几笔、剩下的为什么补不上。"""

    considered: int = 0          # 缺 signal_price 的买入笔数
    filled: int = 0              # 成功重建
    exact_date: int = 0          # 其中信号日来自记录（精确）
    inferred_date: int = 0       # 其中信号日按 T+1 模型倒推（近似）
    skipped: dict[str, int] = field(default_factory=dict)
    dates_fetched: int = 0       # 实际取了几个信号日的行情

    def to_dict(self) -> dict[str, Any]:
        return {
            "considered": self.considered,
            "filled": self.filled,
            "exact_date": self.exact_date,
            "inferred_date": self.inferred_date,
            "skipped": dict(self.skipped),
            "dates_fetched": self.dates_fetched,
        }

    def _skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def backfill_signal_prices(
    gw: Any,
    trades: list[dict[str, Any]],
    *,
    retries: int = _BACKFILL_RETRIES,
    interval: float = _BACKFILL_INTERVAL,
) -> BackfillStats:
    """为缺 signal_price 的买入记录重建该字段（原地修改 trades）。

    引擎记的 signal_price 就是信号日收盘价，取自 ``fetch_prices``
    （按代码前缀分派 daily / cb_daily / fund_daily 的 close）。这里调用
    **同一个函数**、同一个字段，所以重建值与当初落盘的会是同一个数 ——
    不是近似估计。唯一的近似出现在信号日本身：

    - 记录里带 ``signal_date`` → 直接用，精确。
    - 没有 → 按「T 出信号、T+1 开盘成交」倒推成交日前一交易日。挂单遇
      停牌/涨停会在 pending_orders 里多压几天，这类延迟成交倒推会偏早。

    已有 signal_price 的记录一概不动 —— 原生落盘值是基准，不能被重建覆盖。
    只处理买入：追高校准只用买入样本（见 :func:`build_records`）。
    """
    stats = BackfillStats()

    need: list[dict[str, Any]] = []
    for t in trades:
        if t["side"] != "buy":
            continue
        if _positive(t.get("signal_price")):
            continue  # 原生记录优先，不覆盖
        need.append(t)

    stats.considered = len(need)
    if not need:
        return stats

    from aipha.data.calendar import CNStockCalendar
    from aipha.execution.trading_ops import fetch_prices

    cal = CNStockCalendar()

    # 先定信号日，再按信号日分组批量取价
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in need:
        recorded = str(t.get("signal_date") or "")
        if len(recorded) == 8 and recorded.isdigit():
            t["_signal_date_exact"] = True
            by_date[recorded].append(t)
            continue

        fill_date = t.get("date") or ""
        try:
            fill_day = _to_date(fill_date)
        except (ValueError, IndexError, TypeError):
            stats._skip("成交日缺失或无法解析")
            continue
        try:
            signal_day = cal.get_prev_trading_day(fill_day)
        except Exception as exc:
            logger.debug("倒推信号日失败 %s: %s", fill_date, exc)
            stats._skip("无法倒推信号日（交易日历）")
            continue
        t["_signal_date_exact"] = False
        by_date[signal_day.strftime("%Y%m%d")].append(t)

    for signal_date in sorted(by_date):
        group = by_date[signal_date]
        codes = sorted({t["code"] for t in group if t["code"]})
        if not codes:
            for t in group:
                stats._skip("标的代码缺失")
            continue

        try:
            prices = fetch_prices(
                gw, codes, signal_date,
                retries=retries, interval=interval,
            )
            stats.dates_fetched += 1
        except Exception as exc:
            # 单个信号日取不到不该拖垮整次重建
            logger.warning("信号日 %s 行情获取失败: %s", signal_date, exc)
            for t in group:
                stats._skip("信号日行情获取失败")
            continue

        for t in group:
            px = prices.get(t["code"])
            if not _positive(px):
                stats._skip("信号日无该标的收盘价（停牌/未上市/接口未覆盖）")
                continue
            t["signal_price"] = float(px)
            t["signal_date"] = signal_date
            t["signal_price_source"] = SOURCE_RECONSTRUCTED
            stats.filled += 1
            if t.pop("_signal_date_exact", False):
                stats.exact_date += 1
            else:
                stats.inferred_date += 1

    for t in need:
        t.pop("_signal_date_exact", None)

    return stats


def format_backfill(stats: BackfillStats) -> str:
    """重建结果的文字报告。"""
    if not stats.considered:
        return "signal_price 重建: 无需重建（所有买入记录都带原生 signal_price）"

    lines = [
        "signal_price 重建",
        "─" * 46,
        f"缺 signal_price 的买入   {stats.considered} 笔",
        f"成功重建                 {stats.filled} 笔"
        f"（信号日精确 {stats.exact_date} / 倒推 {stats.inferred_date}）",
    ]
    if stats.skipped:
        lines.append("未能重建:")
        for reason, count in sorted(
            stats.skipped.items(), key=lambda kv: -kv[1],
        ):
            lines.append(f"  {count:>4} 笔  {reason}")
    if stats.inferred_date:
        lines += [
            "",
            f"注意: {stats.inferred_date} 笔的信号日是按「T 出信号、T+1 开盘"
            "成交」倒推的。",
            "      挂单遇停牌/涨停会多压几天再成交，这类记录倒推出的信号日偏早，",
            "      算出的追高幅度会混入额外几天的行情。这部分结论请当作参考值。",
        ]
    return "\n".join(lines)


# ── 前瞻价格 ──────────────────────────────────────────────────


def fetch_forward_prices(
    trades: list[dict[str, Any]],
    forward_days: int,
) -> dict[tuple[str, str], list[float]]:
    """拉取每笔成交之后 ``forward_days`` 个交易日的收盘价。

    一次性按日期区间批量加载，避免逐笔请求。
    返回 (code, trade_date) → 收盘价列表。
    """
    dates = sorted({t["date"] for t in trades if t["date"]})
    if not dates:
        return {}

    from datetime import timedelta

    from aipha.data.calendar import CNStockCalendar
    from aipha.data.tushare_equity_provider import TuShareEquityProvider

    cal = CNStockCalendar()
    provider = TuShareEquityProvider()

    start = dates[0]
    # 末笔成交还需要 forward_days 个交易日，日历日按 2 倍粗放取够
    end_dt = _to_date(dates[-1]) + timedelta(days=max(forward_days * 2, 10))
    end = end_dt.strftime("%Y%m%d")

    logger.info("拉取日线 %s → %s ...", start, end)
    bars = provider.load_daily_bars_range(start, end)
    if bars is None or bars.empty:
        logger.error("日线数据为空，无法计算成交后表现")
        return {}

    # (code, date) → close
    close_by: dict[str, dict[str, float]] = defaultdict(dict)
    for row in bars.itertuples(index=False):
        code = getattr(row, "ts_code", None)
        td = str(getattr(row, "trade_date", ""))
        close = getattr(row, "close", None)
        if code and td and _positive(close):
            close_by[code][td] = float(close)

    trading_days = [
        d.strftime("%Y%m%d")
        for d in cal.get_trading_days(_to_date(start), end_dt)
    ]
    day_index = {d: i for i, d in enumerate(trading_days)}

    out: dict[tuple[str, str], list[float]] = {}
    for t in trades:
        code, td = t["code"], t["date"]
        idx = day_index.get(td)
        if code not in close_by or idx is None:
            continue
        window = trading_days[idx + 1: idx + 1 + forward_days]
        prices = [close_by[code][d] for d in window if d in close_by[code]]
        if prices:
            out[(code, td)] = prices
    return out


def _to_date(yyyymmdd: str):
    from datetime import date

    return date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]))


# ── 记录构建 ──────────────────────────────────────────────────


def build_records(
    trades: list[dict[str, Any]],
    forward: dict[tuple[str, str], list[float]],
) -> list[ExecutionRecord]:
    """把交易记录转成 ExecutionRecord。

    ``arrival_price`` 取成交价 —— Paper/Live 都是「T 日出信号、T+1 开盘成交」
    的两阶段模型，可下单价与成交价是同一个数。因此 ``delay_bps``
    （信号价→可下单价）承载全部追高幅度，``execution_bps`` 恒为 0。
    """
    records: list[ExecutionRecord] = []
    for t in trades:
        if t["side"] != "buy":
            continue
        if not (_positive(t.get("signal_price")) and _positive(t.get("price"))):
            continue
        fill = float(t["price"])
        records.append(ExecutionRecord(
            ts_code=t["code"],
            signal_price=float(t["signal_price"]),
            arrival_price=fill,
            fill_price=fill,
            qty=t["shares"],
            fees=t["cost"],
            side="buy",
            forward_prices=forward.get((t["code"], t["date"]), []),
        ))
    return records


def warn_if_degenerate(records: list[ExecutionRecord]) -> None:
    """信号价与成交价完全相同 —— 测出来的追高必然是 0，说明数据不对。

    Live 路径把限价单价格当成交价记账，而限价就等于信号价；
    真实成交价要从 QMT 回报 (``traded_price``) 取。
    """
    if not records:
        return
    zero = sum(
        1 for r in records
        if r.fill_price is not None
        and abs(float(r.fill_price) - r.signal_price) < 1e-9
    )
    if zero == len(records):
        logger.warning(
            "全部 %d 笔的成交价与信号价完全相同 —— 追高幅度恒为 0，"
            "无法校准。这通常说明记录的是委托价而非成交价"
            "（Live 路径的限价单）。请改用 QMT 成交回报里的 traded_price。",
            zero,
        )
    elif zero > len(records) * 0.5:
        logger.warning(
            "%d/%d 笔的成交价等于信号价，测出的追高分布会偏向 0。",
            zero, len(records),
        )


def split_by_regime(
    trades: list[dict[str, Any]],
    forward: dict[tuple[str, str], list[float]],
    regime_path: Path,
) -> dict[str, list[ExecutionRecord]]:
    """按 日期→市场状态 映射切分记录。

    映射文件为 ``{"20250103": "BULL", ...}``，或
    ``{"regimes": {"20250103": "bull"}}``。状态名大小写不敏感。
    """
    from aipha.advisory.recommendation_engine import normalize_regime

    with open(regime_path, encoding="utf-8") as f:
        raw = json.load(f)
    mapping = raw.get("regimes", raw) if isinstance(raw, dict) else raw
    if not isinstance(mapping, dict):
        # 不能退化成空映射 —— 那会让「文件格式错」看起来像「全部日期未覆盖」
        raise ValueError(
            f"{regime_path}: 需要 日期→状态 的字典，"
            f'如 {{"20250106": "BULL"}}，收到 {type(mapping).__name__}',
        )

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unmapped = 0
    for t in trades:
        key = normalize_regime(mapping.get(t["date"], ""))
        if key is None:
            unmapped += 1
            continue
        grouped[key].append(t)
    if unmapped:
        logger.warning("%d 笔交易日期在状态映射中缺失，已跳过", unmapped)

    return {k: build_records(v, forward) for k, v in grouped.items()}


# ── 入口 ──────────────────────────────────────────────────────


def _build_gateway() -> Any:
    """构造取价用的 TuShare gateway（仅重建路径需要）。"""
    import os

    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN 未配置，无法重建 signal_price")

    from aipha.data.tushare_gateway import TuShareGateway

    return TuShareGateway(token=token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="用历史交易记录校准 max_chase_pct",
    )
    parser.add_argument("--state", required=True, type=Path,
                        help="Paper/Live 状态文件 (JSON)")
    parser.add_argument("--current-cap", type=float, default=0.03,
                        help="当前 max_chase_pct，用于对比 (默认 0.03)")
    parser.add_argument("--forward-days", type=int, default=DEFAULT_FORWARD_DAYS,
                        help=f"成交后观察的交易日数 (默认 {DEFAULT_FORWARD_DAYS})")
    parser.add_argument("--outcome", choices=OUTCOME_METRICS,
                        default="first_forward_bps",
                        help="判据指标: 首日表现(默认) 或 整段持有")
    parser.add_argument("--alpha", type=float, default=0.05,
                        help="显著性水平 (默认 0.05)")
    parser.add_argument("--regime-file", type=Path,
                        help="日期→市场状态映射，用于校准分状态乘数")
    parser.add_argument("--backfill-signal-price", action="store_true",
                        help="旧记录缺 signal_price 时，从历史日线重建"
                             "（信号日收盘价，与引擎同一取价口径）")
    parser.add_argument("--json", type=Path, dest="json_out",
                        help="把结果写成 JSON")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if not args.state.exists():
        logger.error("状态文件不存在: %s", args.state)
        return 2

    trades = load_trades(args.state)

    backfill: BackfillStats | None = None
    if args.backfill_signal_price:
        try:
            gw = _build_gateway()
        except RuntimeError as exc:
            logger.error("%s", exc)
            return 2
        backfill = backfill_signal_prices(gw, trades)
        print(format_backfill(backfill))
        print()

    coverage = summarize_coverage(trades)
    print(
        f"交易记录 {coverage['total']} 笔 → 买入 {coverage['buys']} 笔 → "
        f"带信号价 {coverage['with_signal_price']} 笔 → "
        f"可用 {coverage['usable']} 笔"
        f"（原生 {coverage['recorded']} / 重建 {coverage['reconstructed']}）"
    )
    if coverage["buys"] and not coverage["with_signal_price"]:
        print(
            "\n没有任何买入记录带 signal_price —— 这些是旧格式的记录。\n"
            "Paper/Live 引擎现已随成交落盘该字段，继续运行即可积累；\n"
            f"攒到 {MIN_SAMPLE_FOR_CALIBRATION} 笔后再跑本脚本。\n"
            "不想等的话加 --backfill-signal-price，从历史日线重建信号价\n"
            "（信号日收盘价，与引擎同一取价口径）。"
            if not args.backfill_signal_price else
            "\n重建后仍没有可用的 signal_price —— 上面的「未能重建」列出了原因。\n"
            "常见情形是本地缺对应信号日的行情，或标的类型未被取价接口覆盖。"
        )
        return 1

    if coverage["reconstructed"]:
        print(
            f"\n⚠ {coverage['reconstructed']}/{coverage['usable']} 笔的信号价"
            "是事后重建的，不是当初落盘的。\n"
            "  改 max_chase_pct 之前请留意这一点；重建口径见模块 docstring。"
        )

    forward = fetch_forward_prices(trades, args.forward_days)
    records = build_records(trades, forward)
    warn_if_degenerate(records)

    cal = calibrate_chase_cap(
        records,
        current_max_chase_pct=args.current_cap,
        outcome_metric=args.outcome,
        alpha=args.alpha,
    )
    print()
    print(format_chase_calibration(cal))

    regime_result = None
    if args.regime_file:
        regime_result = calibrate_regime_multipliers(
            split_by_regime(trades, forward, args.regime_file),
            current_max_chase_pct=args.current_cap,
            outcome_metric=args.outcome,
            alpha=args.alpha,
        )
        print()
        print(format_regime_calibration(regime_result))

    if args.json_out:
        payload: dict[str, Any] = {
            "coverage": coverage,
            "forward_days": args.forward_days,
            "calibration": cal.to_dict(),
            # 下游读到这个标记就知道结论里混了重建样本
            "uses_reconstructed_signal_price": bool(coverage["reconstructed"]),
        }
        if backfill is not None:
            payload["backfill"] = backfill.to_dict()
        if regime_result is not None:
            payload["regime"] = regime_result.to_dict()
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\n已写出 {args.json_out}")

    return 0 if cal.sample_sufficient else 1


if __name__ == "__main__":
    raise SystemExit(main())
