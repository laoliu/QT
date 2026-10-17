#!/usr/bin/env python3
"""V2 ML 选股策略 —— 模拟盘每日推进（EOD）

职责只有两件：
1. **打分**：把当日特征面板喂给已训练好的模型（可多模型集成），取 Top-K
2. **记账**：按 Top-K 调仓，更新现金/持仓/NAV，落盘状态

明确**不做**的事：

- **不构造特征**。规范入口只接受已物化且带 TuShare PIT sidecar 的特征
  快照。历史面板仍可通过 ``--legacy-compat --add-fundamentals`` 显式回放。
- **不拉数据**。当日面板由 ``--panel`` 传入（盘前 ``scripts/ops/data_preheat.py``
  灌缓存、训练/特征流水线产出面板）。这个脚本不去猜数据从哪来。

## 状态文件

``data/paper_state/v2_ml/state.json``，与 ``scripts/ops/state_stale_checker.py``
扫的是同一个位置、同一个文件名 —— 引擎哪天没跑，陈旧检查就能看见。
``--execute`` 时还会往同目录追加一行 ``nav_history.jsonl``，让
``scripts/ops/s4_validator.py`` 能把这个组合纳入 S4 门禁统计；只写在
state.json 里的 NAV 那些工具是看不到的。

状态读坏时**直接报错，不回退到初始资金**：静默重置等于把亏损洗掉，账面会
凭空多出一笔钱，比崩掉难查得多。

## 默认 dry-run

按 ``scripts/ops/__init__.py`` 的约定，会改持久状态的操作默认只预演，要真
写盘必须显式 ``--execute``。

用法::

    python scripts/ops/v2_ml_paper_daily.py --panel output/panel_20260910.parquet \\
        --model-dir models/ml_csi500 --date 20260910           # 预演
    python scripts/ops/v2_ml_paper_daily.py --panel ... --model-dir ... --execute
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from aipha.utils.paths import DATA_DIR  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# 组合 id：目录名同时被 state_stale_checker / s4_validator 当作组合标识
PORTFOLIO_ID = "v2_ml"
_STATE_DIR = DATA_DIR / "paper_state" / PORTFOLIO_ID
_STATE_FILENAME = "state.json"
_NAV_HISTORY_FILENAME = "nav_history.jsonl"

INITIAL_CAPITAL = 1_000_000.0
DEFAULT_TOP_K = 20

# 买入时预留的现金比例：费用按成交额算，等权分配到分算完再扣费容易差几十块
# 导致最后一只买不进去。留 0.5% 比事后回退一手更省事。
_CASH_RESERVE_PCT = 0.005

# 训练时用到的 daily_basic 原始列（与 scripts/train_ml_factor.py:737 同源，
# 改这里之前先确认训练侧也改了，否则特征列会对不上）
_FUNDAMENTAL_RAW_COLS = (
    "pe_ttm", "pb", "total_mv", "circ_mv",
    "turnover_rate", "dv_ratio", "dv_ttm",
)


# ──────────────────────────── 状态 ────────────────────────────
@dataclass
class PaperState:
    """模拟盘账本。

    ``positions`` 单条形状::

        {"code": "000001.SZ", "shares": 1000,
         "avg_cost": 10.0, "entry_date": "20260401"}

    ``entry_date`` 不是备注，是 T+1 判定的依据（见 :func:`execute_rebalance`）。
    """

    cash: float = INITIAL_CAPITAL
    positions: list[dict[str, Any]] = field(default_factory=list)
    nav_history: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    last_run_date: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PaperState:
        return cls(
            cash=float(raw.get("cash", INITIAL_CAPITAL)),
            positions=list(raw.get("positions") or []),
            nav_history=list(raw.get("nav_history") or []),
            trades=list(raw.get("trades") or []),
            last_run_date=raw.get("last_run_date"),
        )


def load_state() -> PaperState:
    """读状态文件；不存在则返回初始状态（现金 = INITIAL_CAPITAL）。

    Raises:
        RuntimeError: 文件存在但读不出来 —— 不会退化成初始资金，见模块 docstring。
    """
    path = Path(_STATE_DIR) / _STATE_FILENAME
    if not path.is_file():
        logger.info("状态文件不存在，按初始资金 %.0f 开户: %s", INITIAL_CAPITAL, path)
        return PaperState()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"状态文件损坏，拒绝按初始资金继续（会把已实现盈亏抹掉）: "
            f"{path}: {exc}"
        ) from exc

    state = PaperState.from_dict(raw)
    logger.info(
        "状态已加载: 现金 %.2f, 持仓 %d, NAV 记录 %d 条 (last_run=%s)",
        state.cash, len(state.positions), len(state.nav_history),
        state.last_run_date,
    )
    return state


def save_state(state: PaperState) -> Path:
    """原子写状态文件（先写 .tmp 再 rename），返回落盘路径。

    原子写不是洁癖：写到一半被 kill 会留下半个 JSON，下次 load_state 直接
    抛错，等于账本丢了。
    """
    state_dir = Path(_STATE_DIR)
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / _STATE_FILENAME

    payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(suffix=".json.tmp", dir=str(state_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    logger.info("状态已保存: %s", path)
    return path


# ──────────────────────────── NAV ────────────────────────────
def _update_nav(
    state: PaperState,
    prices: dict[str, float],
    date: str,
) -> float:
    """计算并追加当日 NAV = 现金 + Σ 持仓市值。

    某只持仓当天取不到价（停牌/退市/数据缺口）时按 ``avg_cost`` 估值并告警。
    不能按 0 算 —— 那会让一次数据缺口看起来像血本无归，触发一堆假警报；
    也不能跳过该持仓，那等于凭空少一块资产。按成本估值至少是有界的偏差，
    且日志里能查到是哪几只。
    """
    position_value = 0.0
    priced_from_cost: list[str] = []

    for pos in state.positions:
        code = pos["code"]
        shares = float(pos.get("shares", 0) or 0)
        px = _clean_price(prices.get(code))
        if px is None:
            px = float(pos.get("avg_cost", 0) or 0)
            priced_from_cost.append(code)
        position_value += shares * px

    nav = float(state.cash) + position_value
    record: dict[str, Any] = {
        "date": date,
        "nav": round(nav, 2),
        "cash": round(float(state.cash), 2),
        "position_value": round(position_value, 2),
        "n_positions": len(state.positions),
    }
    if priced_from_cost:
        record["priced_from_cost"] = priced_from_cost
        logger.warning(
            "%s 有 %d 只持仓无当日价格，按成本估值（NAV 已失真）: %s",
            date, len(priced_from_cost), ", ".join(priced_from_cost),
        )

    state.nav_history.append(record)
    logger.info(
        "%s NAV=%.2f (现金 %.2f + 持仓 %.2f, %d 只)",
        date, nav, state.cash, position_value, len(state.positions),
    )
    return nav


def _clean_price(px: Any) -> float | None:
    """价格清洗：None/NaN/<=0 一律当「没有价格」。"""
    if px is None:
        return None
    try:
        val = float(px)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(val) or val <= 0:
        return None
    return val


# ──────────────────────────── 调仓 ────────────────────────────
def execute_rebalance(
    state: PaperState,
    target_codes: list[str],
    prices: dict[str, float],
    date: str,
    *,
    asset_type: str = "cn_equity",
) -> list[dict[str, Any]]:
    """按目标持仓调仓：卖掉不在目标里的，买入目标里还没有的。

    有意**不重新配平已持有的仓位**。「已经拿着、目标里还在」的票一股不动，
    只处理进出。每天把权重拉回等权会产生大量小单，A 股 5 元最低佣金下这些
    小单的成本占比极高，模拟盘里看不出来、实盘里全是摩擦。

    约束（都是真约束，不是为了好看）：

    - **T+1**：``entry_date == date`` 的持仓当天不能卖（A 股当日买入次日才可卖）。
    - **整手**：按 ``FeeParams.lot_size``（A 股 100 股）向下取整，不足一手不买。
    - **无价不动**：当天取不到价格的票既不卖也不买。拿一个猜的价格记账，
      会让 NAV 和后续的 S4 门禁建立在虚构成交上。
    - **费用**：走 ``aipha.execution.trading_ops.calc_trade_cost``（§13.2 唯一
      实现），不在这里手算佣金/印花税。

    Args:
        state: 会被就地修改（现金、持仓、trades）
        target_codes: 目标持仓代码（重复项会去重，顺序即优先级）
        prices: 当日价格 ``{code: price}``
        date: ``YYYYMMDD``
        asset_type: 费率口径，默认 A 股

    Returns:
        本次实际成交的流水（同时已追加到 ``state.trades``）
    """
    from aipha.execution.trading_ops import calc_trade_cost, get_fee_params

    fee_params = get_fee_params(asset_type)
    lot = max(int(fee_params.lot_size or 1), 1)

    targets = list(dict.fromkeys(target_codes or []))
    target_set = set(targets)
    held = {p["code"] for p in state.positions}
    executed: list[dict[str, Any]] = []

    # --- 1. 卖出：持有但不在目标里 ---
    kept: list[dict[str, Any]] = []
    for pos in state.positions:
        code = pos["code"]
        if code in target_set:
            kept.append(pos)
            continue

        if str(pos.get("entry_date") or "") == str(date):
            logger.info("%s T+1 限制，今日买入当日不可卖，继续持有", code)
            kept.append(pos)
            continue

        px = _clean_price(prices.get(code))
        if px is None:
            logger.warning("%s 无当日价格，跳过卖出（继续持有）", code)
            kept.append(pos)
            continue

        shares = float(pos.get("shares", 0) or 0)
        if shares <= 0:
            logger.warning("%s 持仓股数 %s，直接剔除", code, shares)
            continue

        amount = shares * px
        fee = calc_trade_cost(amount, "sell", fee_params, trade_date=date)
        state.cash = float(state.cash) + amount - fee
        trade = _make_trade(date, code, "sell", shares, px, amount, fee, "not_in_target")
        executed.append(trade)

    state.positions = kept

    # --- 2. 买入：目标里还没有的 ---
    buy_codes = [
        c for c in targets
        if c not in held and _clean_price(prices.get(c)) is not None
    ]
    skipped = [c for c in targets if c not in held and c not in buy_codes]
    if skipped:
        logger.warning("%d 只目标无当日价格，跳过买入: %s", len(skipped), ", ".join(skipped))

    if buy_codes:
        budget_total = float(state.cash) * (1.0 - _CASH_RESERVE_PCT)
        budget_each = budget_total / len(buy_codes)
        for code in buy_codes:
            px = _clean_price(prices.get(code))
            if px is None:                      # 上面筛过，防御性
                continue
            shares = math.floor(budget_each / (px * lot)) * lot
            while shares > 0:
                amount = shares * px
                fee = calc_trade_cost(amount, "buy", fee_params, trade_date=date)
                if amount + fee <= float(state.cash):
                    break
                shares -= lot                   # 差一点点就少买一手，别透支
            if shares <= 0:
                logger.info(
                    "%s 预算 %.2f 不足一手（价 %.2f × %d 股），跳过",
                    code, budget_each, px, lot,
                )
                continue

            amount = shares * px
            fee = calc_trade_cost(amount, "buy", fee_params, trade_date=date)
            state.cash = float(state.cash) - amount - fee
            state.positions.append({
                "code": code,
                "shares": int(shares),
                "avg_cost": round(px, 4),
                "entry_date": date,
            })
            executed.append(
                _make_trade(date, code, "buy", shares, px, amount, fee, "new_target")
            )

    state.trades.extend(executed)
    logger.info(
        "%s 调仓完成: 卖 %d 笔 / 买 %d 笔, 现金 %.2f, 持仓 %d 只",
        date,
        sum(1 for t in executed if t["side"] == "sell"),
        sum(1 for t in executed if t["side"] == "buy"),
        state.cash, len(state.positions),
    )
    return executed


def _make_trade(
    date: str, code: str, side: str, shares: float,
    price: float, amount: float, fee: float, reason: str,
) -> dict[str, Any]:
    return {
        "date": date,
        "code": code,
        "side": side,
        "shares": int(shares),
        "price": round(price, 4),
        "amount": round(amount, 2),
        "fee": round(fee, 2),
        "reason": reason,
    }


# ──────────────────────────── 特征 ────────────────────────────
def add_fundamental_factors(df: pd.DataFrame, *, allow_legacy: bool = False) -> pd.DataFrame:
    """在面板上补基本面衍生因子（截面 rank / 变化率 / EP、BP 等）。

    实现直接复用训练脚本的 ``_add_fundamental_factors``，**不在推理端另写一
    份**：两份实现只要有一处口径不同（rank vs z-score、clip 边界、ffill 天
    数），模型上线看到的分布就和训练时不一样了。

    要求 ``df`` 含 ``datetime`` / ``symbol`` 两列（截面与时序分组依据）。
    原始基本面列一个都没有时原样返回，只记一条警告 —— 面板缺列是上游数据
    问题，在这里补零会把「没数据」伪装成「数据是 0」。

    Returns:
        同一个 DataFrame（就地新增列后返回，便于链式调用）
    """
    if not allow_legacy:
        raise ValueError(
            "规范 Paper 入口不再从 train_ml_factor 生成特征；"
            "请传入已物化的 PIT feature snapshot，或显式使用 --legacy-compat 回放旧模型"
        )

    from scripts.train_ml_factor import _add_fundamental_factors

    missing_keys = [c for c in ("datetime", "symbol") if c not in df.columns]
    if missing_keys:
        raise ValueError(f"面板缺少分组列 {missing_keys}，无法计算截面因子")

    cols = [c for c in _FUNDAMENTAL_RAW_COLS if c in df.columns]
    if not cols:
        logger.warning(
            "面板不含任何基本面原始列 %s，跳过基本面因子",
            list(_FUNDAMENTAL_RAW_COLS),
        )
        return df

    added = _add_fundamental_factors(df, cols)
    logger.info("基本面因子: 原始列 %d → 新增 %d 列", len(cols), added)
    return df


# ──────────────────────────── 打分 ────────────────────────────
def ensemble_predict(
    df: pd.DataFrame,
    model_paths: list[str | Path],
    *,
    method: str = "rank_average",
    feature_version: str = "cn_equity_v1",
    allow_legacy: bool = False,
) -> pd.Series:
    """多模型集成打分，返回 ``index = df.index`` 的分数 Series（越大越好）。

    规范模式只接受带 ``model_manifest.json`` 的 canonical selector。旧模型
    目录和 ``ensemble_meta.pkl`` 组合模型必须显式传 ``allow_legacy=True``，
    以免历史实验模型悄悄进入默认 Paper 运行链。

    兼容模式支持两种历史目录布局：

    - 含 ``ensemble_meta.pkl`` → 按 :class:`aipha.models.ensemble.MultiModelEnsemble`
      整体加载（它自己会处理子模型与 stacking 层）
    - 否则 → 当作单个 :class:`aipha.models.ml_stock_selector.MLStockSelector`
      目录，多个目录之间再做截面 rank 平均

    默认 ``rank_average``：各模型预测值的量纲往往不可比（树模型输出的是
    label 尺度、线性模型是另一套），直接平均等于给尺度大的模型加权。截面
    rank 只用序，跨模型才是可加的。

    Args:
        df: 当日特征面板（列名需与训练时一致）
        model_paths: 模型目录，至少一个
        method: ``rank_average``（默认）或 ``mean``（已知量纲一致时）

    Raises:
        ValueError: 没给模型目录、或某个目录不存在
    """
    if not model_paths:
        raise ValueError("ensemble_predict 至少需要一个模型目录")

    paths = [Path(p) for p in model_paths]
    for p in paths:
        if not p.exists():
            raise ValueError(f"模型目录不存在: {p}")

    preds: list[pd.Series] = []
    for p in paths:
        manifest = p / "model_manifest.json"
        if manifest.is_file():
            from aipha.research import load_verified_selector

            sel = load_verified_selector(
                p,
                expected_feature_version=feature_version,
            )
            scores = sel.predict(df)
        elif not allow_legacy:
            raise ValueError(
                f"规范 Paper 入口拒绝无 model_manifest.json 的旧模型: {p}。"
                "如需历史回放，请显式传 --legacy-compat"
            )
        elif (p / "ensemble_meta.pkl").is_file():
            from aipha.models.ensemble import EnsembleConfig, MultiModelEnsemble

            ens = MultiModelEnsemble(EnsembleConfig())
            ens.load(p)
            scores = ens.predict(df)
        else:
            from aipha.models.ml_stock_selector import (
                MLSelectorConfig,
                MLStockSelector,
            )

            sel = MLStockSelector(MLSelectorConfig())
            sel.load(p)
            scores = sel.predict(df)
        preds.append(pd.Series(np.asarray(scores), index=df.index, dtype=float))
        logger.info("模型 %s 打分完成: %d 条", p.name, len(preds[-1]))

    if len(preds) == 1:
        return preds[0].rename("score")

    if method == "mean":
        combined = sum(preds) / len(preds)
    else:
        combined = sum(s.rank(pct=True) for s in preds) / len(preds)
    return combined.rename("score")


def select_top_k(
    scores: pd.Series,
    df: pd.DataFrame,
    top_k: int,
    *,
    symbol_col: str = "symbol",
) -> list[str]:
    """取分数最高的 top_k 个 symbol（去重，保持分数降序）。"""
    if symbol_col not in df.columns:
        raise ValueError(f"面板缺少 {symbol_col} 列，无法映射分数到标的")
    ranked = (
        pd.DataFrame({"symbol": df[symbol_col].values, "score": scores.values})
        .dropna(subset=["score"])
        .sort_values("score", ascending=False)
    )
    picked = list(dict.fromkeys(ranked["symbol"].tolist()))[:max(int(top_k), 0)]
    logger.info("Top-%d 选出 %d 只: %s", top_k, len(picked), ", ".join(picked))
    return picked


# ──────────────────────────── CLI ────────────────────────────
def _latest_slice(panel: pd.DataFrame, date: str | None) -> tuple[pd.DataFrame, str]:
    """取面板中指定日（默认最后一日）的截面。"""
    if "datetime" not in panel.columns:
        raise ValueError("面板缺少 datetime 列")
    dt = pd.to_datetime(panel["datetime"])
    if date:
        want = pd.to_datetime(str(date).replace("-", ""), format="%Y%m%d")
        sub = panel[dt == want]
        if sub.empty:
            raise ValueError(f"面板中没有 {date} 的数据（最后一日 {dt.max().date()}）")
        return sub, want.strftime("%Y%m%d")
    last = dt.max()
    return panel[dt == last], last.strftime("%Y%m%d")


def _load_feature_panel(
    panel_path: Path,
    *,
    feature_version: str,
    allow_legacy: bool = False,
) -> pd.DataFrame:
    """Load a canonical PIT snapshot, or an explicitly opted-in legacy panel."""
    if not panel_path.is_file():
        raise FileNotFoundError(f"feature panel not found: {panel_path}")
    if not allow_legacy:
        if panel_path.suffix.lower() != ".parquet":
            raise ValueError("规范 Paper 入口只接受带 TuShare PIT sidecar 的 Parquet 面板")
        from aipha.research.snapshots import verify_attested_parquet

        metadata = verify_attested_parquet(
            panel_path,
            require_artifact_hash=True,
            verify_content_hash=True,
            require_source_signature=True,
        )
        if metadata.get("provider") != "tushare_pit":
            raise ValueError("规范 Paper 面板必须来自 tushare_pit snapshot builder")
        if metadata.get("feature_version") != feature_version:
            raise ValueError(
                f"feature version mismatch: snapshot={metadata.get('feature_version')!r}, "
                f"requested={feature_version!r}"
            )

    panel = (
        pd.read_parquet(panel_path) if panel_path.suffix.lower() == ".parquet"
        else pd.read_csv(panel_path)
    )
    if not allow_legacy:
        required = {"trade_date", "symbol", "feature_version", "is_index_member", "is_st", "listing_days"}
        missing = required - set(panel.columns)
        if missing:
            raise ValueError(f"TuShare PIT feature snapshot missing columns: {', '.join(sorted(missing))}")
        versions = panel["feature_version"].dropna().astype(str).unique().tolist()
        if versions != [feature_version]:
            raise ValueError(f"row feature_version mismatch: {versions!r}")
        if "price_adjustment" not in panel or not panel["price_adjustment"].eq("point_in_time_factor").all():
            raise ValueError("TuShare PIT snapshot must use point_in_time_factor adjusted prices")
        panel = panel.copy()
        panel["datetime"] = pd.to_datetime(panel["trade_date"], errors="raise")
    return panel


def _append_nav_history(state_dir: Path, record: dict[str, Any]) -> None:
    """追加一行 nav_history.jsonl —— s4_validator / nav_monitor 读的是这个文件。"""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / _NAV_HISTORY_FILENAME
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    """CLI 入口，返回进程退出码。"""
    parser = argparse.ArgumentParser(description="V2 ML 策略模拟盘每日推进")
    parser.add_argument(
        "--panel", required=True,
        help="当日特征面板（parquet/csv），需含 datetime/symbol + 训练时的特征列",
    )
    parser.add_argument(
        "--model-dir", action="append", default=[],
        help="模型目录，可重复传以做集成",
    )
    parser.add_argument("--date", default=None, help="交易日 YYYYMMDD，默认取面板最后一日")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument(
        "--add-fundamentals", action="store_true",
        help="仅兼容模式：面板里只有原始 daily_basic 列时补旧基本面因子",
    )
    parser.add_argument(
        "--feature-version", default="cn_equity_v1",
        help="规范模型 manifest 要求的 feature_version",
    )
    parser.add_argument(
        "--legacy-compat", action="store_true",
        help="显式启用旧模型/旧特征函数，仅用于历史回放，不属于默认研究路径",
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="真正写状态文件（默认只预演，不落盘）",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = parser.parse_args(argv)

    if not args.model_dir:
        logger.error("必须至少指定一个 --model-dir，拒绝在没有模型的情况下调仓")
        return 2

    panel_path = Path(args.panel)
    try:
        panel = _load_feature_panel(
            panel_path,
            feature_version=args.feature_version,
            allow_legacy=args.legacy_compat,
        )
    except Exception as exc:
        logger.error("特征面板校验失败: %s", exc)
        return 2
    if args.add_fundamentals:
        panel = add_fundamental_factors(panel, allow_legacy=args.legacy_compat)

    today, trade_date = _latest_slice(panel, args.date)
    logger.info("推理截面: %s, %d 只标的", trade_date, len(today))

    scores = ensemble_predict(
        today,
        args.model_dir,
        feature_version=args.feature_version,
        allow_legacy=args.legacy_compat,
    )
    targets = select_top_k(scores, today, args.top_k)

    prices = {}
    if "close" in today.columns:
        prices = {
            str(row["symbol"]): row["close"]
            for _, row in today[["symbol", "close"]].iterrows()
        }
    else:
        logger.error("面板缺少 close 列，无法按当日价格记账")
        return 2

    state = load_state()
    # 已持仓的票如果不在当日截面里（停牌），价格取不到 —— execute_rebalance
    # 会跳过它们而不是瞎猜，这里不额外补价。
    trades = execute_rebalance(state, targets, prices, trade_date)
    nav = _update_nav(state, prices, trade_date)
    state.last_run_date = trade_date

    result = {
        "date": trade_date,
        "portfolio_id": PORTFOLIO_ID,
        "targets": targets,
        "n_trades": len(trades),
        "nav": round(nav, 2),
        "cash": round(float(state.cash), 2),
        "positions": [p["code"] for p in state.positions],
        "executed": args.execute,
    }

    if args.execute:
        save_state(state)
        _append_nav_history(Path(_STATE_DIR), state.nav_history[-1])
    else:
        logger.warning("dry-run：以上调仓与 NAV 均未落盘（加 --execute 才写）")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
