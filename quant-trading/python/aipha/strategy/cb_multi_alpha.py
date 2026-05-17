"""可转债多Alpha动态因子融合策略

Alpha 来源（5 个独立信号源组合）：
1. 小规模因子 — issue_size 越小，促转股动力越强
2. 短期动量因子 — N 日涨幅（捕捉趋势延续与资金惯性）
3. 放量突破因子 — surge_ratio（成交额/MA，机构关注或事件催化）
4. 低溢价因子 — cb_over_rate 越低，股性越强
5. 低价因子 — 低价转债面值回归效应

与 multi_factor 的区别：
- multi_factor 全部是静态截面因子（无动量/放量）
- cb_multi_alpha 融合了**动态时序因子**（momentum + surge），
  这两个因子在单独使用时分别贡献 ~24% 和 ~23% 年化

设计要点：
- 每个因子独立排名归一化后加权合并
- 支持配置权重微调与因子开关
- 使用 deque 维护价格/成交额历史（与 cb_momentum / turnover_surge 相同模式）
- max_price 建议 150（sweep 显示 150 >> 120）
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class CBMultiAlphaConfig(CBRotationConfig):
    """多Alpha动态因子融合配置。"""

    # --- 持仓 ---
    top_k: int = 8
    rebalance_days: int = 1
    buffer_k: int = 3

    # --- 筛选 ---
    max_price: float = 150.0
    max_issue_size: float = 20000.0   # 2 亿
    min_issue_size: float = 0.0
    min_turnover: float = 10.0
    min_days_to_maturity: int = 60
    exclude_st: bool = True
    exclude_called: bool = True
    max_premium: float = 80.0         # 溢价率上限（宽松，靠权重控制）

    # --- 动态因子参数 ---
    momentum_lookback: int = 10       # 动量回望天数
    surge_lookback: int = 20          # 放量回望天数
    warmup_bars: int = 25             # 预热期（不交易）

    # --- 因子权重 ---
    w_size: float = 0.30              # 小规模
    w_momentum: float = 0.20          # 动量
    w_surge: float = 0.20             # 放量
    w_premium: float = 0.15           # 低溢价
    w_price: float = 0.15             # 低价

    # --- 风控 ---
    stop_loss_pct: float = 0.12
    min_positions: int = 3

    lot_size: int = 10


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("cb_multi_alpha")
class CBMultiAlphaStrategy(CBRotationStrategy):
    """可转债多Alpha动态因子融合策略。

    组合 5 个独立 alpha 信号源（含 2 个动态时序信号），
    通过加权排名融合选出持仓。
    """

    name = "cb_multi_alpha"

    def __init__(
        self,
        config: CBMultiAlphaConfig | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__()
        self._config = config or CBMultiAlphaConfig(**kwargs)
        self._price_history: dict[str, deque[float]] = {}
        self._amount_history: dict[str, deque[float]] = {}

    @property
    def config(self) -> CBMultiAlphaConfig:
        return self._config

    # ------------------------------------------------------------------
    # 生命周期钩子
    # ------------------------------------------------------------------

    def _on_start_extra(self) -> None:
        self._price_history.clear()
        self._amount_history.clear()

    def _on_bar_update_state(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> None:
        """每 bar 追加收盘价和成交额到历史队列。"""
        cfg = self._config
        price_maxlen = cfg.momentum_lookback + 5
        amount_maxlen = cfg.surge_lookback + 5

        for symbol, bar in bars.items():
            # 价格历史
            if symbol not in self._price_history:
                self._price_history[symbol] = deque(maxlen=price_maxlen)
            self._price_history[symbol].append(bar.close)

            # 成交额历史
            amt = (bar.metadata or {}).get("amount", 0)
            if symbol not in self._amount_history:
                self._amount_history[symbol] = deque(maxlen=amount_maxlen)
            self._amount_history[symbol].append(amt)

    def _should_rebalance(self) -> bool:
        """预热期后按 rebalance_days 轮动。"""
        cfg = self._config
        effective = self._bar_count - cfg.warmup_bars
        if effective < 1:
            return False
        return (effective - 1) % cfg.rebalance_days == 0

    # ------------------------------------------------------------------
    # 筛选
    # ------------------------------------------------------------------

    def _extra_filter(
        self,
        candidate: CandidateInfo,
        context: StrategyContext,
    ) -> bool:
        """溢价率上限 + 需要足够的历史数据。"""
        cfg = self._config

        # 溢价率过滤
        if candidate.cb_over_rate is not None and candidate.cb_over_rate > cfg.max_premium:
            return False

        # 需要价格历史（用于动量）
        price_hist = self._price_history.get(candidate.symbol)
        if not price_hist or len(price_hist) < cfg.momentum_lookback + 1:
            return False

        # 需要成交额历史（用于放量）
        amount_hist = self._amount_history.get(candidate.symbol)
        if not amount_hist or len(amount_hist) < cfg.surge_lookback + 1:
            return False

        return True

    # ------------------------------------------------------------------
    # 打分
    # ------------------------------------------------------------------

    def _score_candidates(
        self,
        candidates: list[CandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """五因子排名归一化，加权合并，取综合评分最低的 top_k。"""
        cfg = self._config
        n = len(candidates)
        if n == 0:
            return []
        if n == 1:
            return [candidates[0].symbol]

        # --- 计算每个候选的因子值 ---
        factor_data: list[dict] = []
        for c in candidates:
            # 动量：momentum_lookback 天收益率
            price_hist = self._price_history[c.symbol]
            past_price = price_hist[-(cfg.momentum_lookback + 1)]
            momentum = (c.close - past_price) / past_price if past_price > 0 else 0.0

            # 放量比：当日成交额 / MA(成交额, surge_lookback)
            amount_hist = self._amount_history[c.symbol]
            # 排除当日（最后一条），取前 surge_lookback 条的均值
            history_amounts = list(amount_hist)[-(cfg.surge_lookback + 1):-1]
            avg_amt = sum(history_amounts) / len(history_amounts) if history_amounts else 1.0
            surge_ratio = c.amount / avg_amt if avg_amt > 0 else 0.0

            factor_data.append({
                "symbol": c.symbol,
                "size": c.issue_size,        # 越小越优（升序）
                "momentum": momentum,        # 越大越优（降序）
                "surge": surge_ratio,        # 越大越优（降序）
                "premium": c.cb_over_rate if c.cb_over_rate is not None else 50.0,  # 越小越优（升序）
                "price": c.close,            # 越小越优（升序）
            })

        # --- 排名归一化 §13.2 委托给共享实现 ---
        from aipha.utils.rank_utils import rank_normalize_list

        def _rank_field(data_list: list[dict], key: str, ascending: bool = True) -> None:
            """将 factor_data 中的 key 字段替换为 0~1 排名。"""
            raw_values = [d[key] for d in data_list]
            ranks = rank_normalize_list(raw_values, ascending=ascending, handle_ties=False)
            for i, r in enumerate(ranks):
                data_list[i][f"{key}_rank"] = r

        # 升序因子（值越小 → rank 越低 → 越优）
        _rank_field(factor_data, "size", ascending=True)
        _rank_field(factor_data, "premium", ascending=True)
        _rank_field(factor_data, "price", ascending=True)

        # 降序因子（值越大 → rank 越低 → 越优）
        _rank_field(factor_data, "momentum", ascending=False)
        _rank_field(factor_data, "surge", ascending=False)

        # --- 加权合并 ---
        w_total = cfg.w_size + cfg.w_momentum + cfg.w_surge + cfg.w_premium + cfg.w_price
        for d in factor_data:
            d["score"] = (
                cfg.w_size * d["size_rank"]
                + cfg.w_momentum * d["momentum_rank"]
                + cfg.w_surge * d["surge_rank"]
                + cfg.w_premium * d["premium_rank"]
                + cfg.w_price * d["price_rank"]
            ) / w_total

        # 按 score 升序（越低越优）
        factor_data.sort(key=lambda x: x["score"])

        top = cfg.top_k + cfg.buffer_k
        result = [d["symbol"] for d in factor_data[:top]]

        if logger.isEnabledFor(logging.DEBUG) and factor_data:
            best = factor_data[0]
            logger.debug(
                f"[{self.name}] 最优: {best['symbol']} "
                f"score={best['score']:.4f} "
                f"size_r={best.get('size_rank', 0):.2f} "
                f"mom_r={best.get('momentum_rank', 0):.2f} "
                f"surge_r={best.get('surge_rank', 0):.2f} "
                f"prem_r={best.get('premium_rank', 0):.2f} "
                f"price_r={best.get('price_rank', 0):.2f}"
            )

        return result
