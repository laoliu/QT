"""可转债双低五因子增强策略 (Double-Low 5-Factor Enhanced)

灵感来源：国金证券 2025 年《可转债量化择时、轮动、选券策略》——双低五因子增强。
据报告：双低五因子增强 AR>24%, ICIR=36.7%, 周胜率67%。

Alpha 原理：
    经典双低（价格+溢价率）在 2022 年后表现衰减（转股溢价率中枢上移），
    通过叠加时序维度的 **DL 历史分位** + **隐含波动率代理** + **短期反转动量**
    实现更稳定的超额收益。

因子矩阵（5 因子）：
1. **双低值 (double_low)**: price + premium_rate(%)
   → 经典双低信号，越低越优
2. **DL 历史分位 (dl_percentile)**: 当前 DL 值在过去 120 日的百分位
   → 越低说明当前 DL 处于历史低位，安全边际更高
3. **短期反转 (short_reversal)**: 过去 20 日收益率
   → 负收益越大 → 超跌反弹空间越大（反向因子）
4. **已实现波动率分位 (rv_percentile)**: 近期 RV 在历史的分位
   → 低波动 → 期权价值被低估 → 波动扩张空间大
5. **信用评级 (rating)**: 高评级优先
   → 排除低资质"雷券"[国金: 2024年信用冲击后此因子重要性提升]

参考：
- 国金证券 2025: AR>24%, MDD 降低 3%, SR/Calmar +0.7
- 区别于现有 double_low_percentile: 添加时序分位 + 波动率 + 反转
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
    _RATING_ORDER,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


@dataclass
class CBDLEnhanced5FConfig(CBRotationConfig):
    """双低五因子增强配置。"""

    top_k: int = 20
    rebalance_days: int = 5
    max_price: float = 135.0
    min_turnover: float = 20.0          # 万元
    min_issue_size: float = 0.0         # 万元
    max_issue_size: float = float("inf")  # 万元
    min_days_to_maturity: int = 30
    exclude_st: bool = True
    stop_loss_pct: float = 0.10
    buffer_k: int = 2
    force_redeem_threshold: float = 130.0
    force_redeem_days: int = 8
    exclude_called: bool = True

    # --- 信用筛选 ---
    min_rating: str = ""
    exclude_unrated: bool = False

    # --- 溢价率上限 ---
    max_premium: float = 40.0

    # --- 历史窗口 ---
    dl_percentile_window: int = 120     # DL 百分位回看天数
    reversal_window: int = 20           # 短期反转回看天数
    rv_window: int = 20                 # 已实现波动率窗口
    rv_percentile_window: int = 120     # RV 百分位回看天数
    min_history: int = 20               # 最少历史数据量

    # --- 溢价率上限 ---
    max_premium: float = 40.0

    # --- 双低参数 ---
    premium_weight_in_dl: float = 1.0   # DL = close + premium_rate * weight

    # --- 五因子权重（自动归一化）---
    w_double_low: float = 0.70          # 双低值（主驱动）
    w_dl_percentile: float = 0.0        # DL 历史分位（诊断显示轻微负面）
    w_reversal: float = 0.30            # 动量（已翻转为正向）
    w_rv_percentile: float = 0.0        # RV 分位（诊断显示负面）
    w_rating: float = 0.0               # 评级（诊断显示负面）


# ---------------------------------------------------------------------------
# 策略
# ---------------------------------------------------------------------------


@StrategyRegistry().register("cb_dl_enhanced_5f")
class CBDLEnhanced5FStrategy(CBRotationStrategy):
    """双低五因子增强策略。

    在经典双低基础上叠加 3 个 Alpha 因子：
    - DL 时序百分位（历史相对估值）
    - 短期反转（超跌反弹）
    - 已实现波动率分位（波动压缩/期权价值）
    """

    name = "cb_dl_enhanced_5f"

    def __init__(self, config: CBDLEnhanced5FConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or CBDLEnhanced5FConfig(**kwargs)

        # 历史数据 {symbol: deque[值]}
        self._price_history: dict[str, deque] = {}
        self._dl_history: dict[str, deque] = {}
        self._return_history: dict[str, deque] = {}

    @property
    def config(self) -> CBDLEnhanced5FConfig:
        return self._config

    def _on_start_extra(self) -> None:
        self._price_history.clear()
        self._dl_history.clear()
        self._return_history.clear()

    def _extra_filter(
        self, candidate: CandidateInfo, context: StrategyContext,
    ) -> bool:
        """溢价率上限过滤。"""
        cfg = self._config
        if candidate.cb_over_rate is not None:
            if candidate.cb_over_rate > cfg.max_premium:
                return False
        return True

    def _on_bar_update_state(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> None:
        """每 bar 更新价格、DL、收益率历史。"""
        cfg = self._config
        maxlen_dl = cfg.dl_percentile_window + 10
        maxlen_rv = cfg.rv_percentile_window + cfg.rv_window + 10
        maxlen_ret = max(cfg.reversal_window, cfg.rv_window) + 10

        for symbol, bar in bars.items():
            if bar.close <= 0:
                continue

            # --- 价格历史 ---
            if symbol not in self._price_history:
                self._price_history[symbol] = deque(maxlen=maxlen_rv)
            prev_close = (
                self._price_history[symbol][-1]
                if self._price_history[symbol]
                else bar.close
            )
            self._price_history[symbol].append(bar.close)

            # --- 日收益率 ---
            ret = (bar.close - prev_close) / prev_close if prev_close > 0 else 0.0
            if symbol not in self._return_history:
                self._return_history[symbol] = deque(maxlen=maxlen_ret)
            self._return_history[symbol].append(ret)

            # --- DL 值历史 ---
            meta = bar.metadata or {}
            premium = meta.get("cb_over_rate")
            if premium is not None:
                dl = bar.close + premium * cfg.premium_weight_in_dl
                if symbol not in self._dl_history:
                    self._dl_history[symbol] = deque(maxlen=maxlen_dl)
                self._dl_history[symbol].append(dl)

    def _should_rebalance(self) -> bool:
        cfg = self._config
        if self._bar_count < cfg.min_history:
            return False
        return (self._bar_count - 1) % cfg.rebalance_days == 0

    def _compute_dl_percentile(self, symbol: str) -> float | None:
        """当前 DL 值在过去 N 日的百分位（0~1, 越低越便宜）。"""
        hist = self._dl_history.get(symbol)
        if hist is None or len(hist) < 20:
            return None
        current = hist[-1]
        vals = list(hist)
        below = sum(1 for v in vals if v <= current)
        return below / len(vals)

    def _compute_reversal(self, symbol: str) -> float | None:
        """过去 N 日累积收益率。

        诊断发现: CB 市场存在短期动量效应，而非反转。
        正收益越大 = 动量越强 → 走势延续。
        """
        cfg = self._config
        rets = self._return_history.get(symbol)
        if rets is None or len(rets) < cfg.reversal_window:
            return None
        recent = list(rets)[-cfg.reversal_window:]
        cum = 1.0
        for r in recent:
            cum *= (1.0 + r)
        return cum - 1.0

    def _compute_rv_percentile(self, symbol: str) -> float | None:
        """近期已实现波动率在历史的百分位（0~1, 低=波动压缩）。"""
        cfg = self._config
        rets = self._return_history.get(symbol)
        if rets is None or len(rets) < cfg.rv_percentile_window:
            return None

        all_rets = list(rets)

        # 计算所有可能的 rv_window 窗口的 RV
        rv_values: list[float] = []
        for i in range(cfg.rv_window, len(all_rets) + 1):
            window = all_rets[i - cfg.rv_window: i]
            mean_r = sum(window) / len(window)
            var = sum((r - mean_r) ** 2 for r in window) / len(window)
            rv_values.append(math.sqrt(var) if var > 0 else 0.0)

        if len(rv_values) < 5:
            return None

        current_rv = rv_values[-1]
        below = sum(1 for v in rv_values if v <= current_rv)
        return below / len(rv_values)

    def _score_candidates(
        self,
        candidates: list[CandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """五因子排名打分。"""
        cfg = self._config
        n = len(candidates)
        if n == 0:
            return []

        # --- 采集数据 ---
        data: list[dict] = []
        for c in candidates:
            premium = c.cb_over_rate if c.cb_over_rate is not None else 50.0
            dl_val = c.close + premium * cfg.premium_weight_in_dl
            dl_pct = self._compute_dl_percentile(c.symbol)
            reversal = self._compute_reversal(c.symbol)
            rv_pct = self._compute_rv_percentile(c.symbol)
            rating_num = _RATING_ORDER.get(c.rating, 30)

            # 若缺失关键因子，使用中位数替代
            data.append({
                "symbol": c.symbol,
                "double_low": dl_val,
                "dl_percentile": dl_pct if dl_pct is not None else 0.5,
                "reversal": reversal if reversal is not None else 0.0,
                "rv_percentile": rv_pct if rv_pct is not None else 0.5,
                "rating": rating_num,
            })

        n = len(data)
        if n < 2:
            return [d["symbol"] for d in data[:cfg.top_k]]

        # --- 排名归一化 — §13.2 委托共享模块 ---
        from aipha.utils.rank_utils import rank_normalize_list

        # 反向因子: 越小越好 (ascending=True → 小值 = 低排名分)
        for key in ("double_low", "dl_percentile", "rv_percentile"):
            values = [d[key] for d in data]
            scores = rank_normalize_list(values, ascending=True, handle_ties=True)
            for d, s in zip(data, scores):
                d[f"{key}_rank"] = s

        # 正向因子: 越大越好 (ascending=False → 大值 = 低排名分)
        for key in ("reversal", "rating"):
            values = [d[key] for d in data]
            scores = rank_normalize_list(values, ascending=False, handle_ties=True)
            for d, s in zip(data, scores):
                d[f"{key}_rank"] = s

        # --- 加权综合得分 ---
        total_w = (
            cfg.w_double_low + cfg.w_dl_percentile
            + cfg.w_reversal + cfg.w_rv_percentile + cfg.w_rating
        )
        if total_w < 1e-8:
            total_w = 1.0

        for d in data:
            d["score"] = (
                cfg.w_double_low * d["double_low_rank"]
                + cfg.w_dl_percentile * d["dl_percentile_rank"]
                + cfg.w_reversal * d["reversal_rank"]
                + cfg.w_rv_percentile * d["rv_percentile_rank"]
                + cfg.w_rating * d["rating_rank"]
            ) / total_w

        data.sort(key=lambda x: x["score"])
        return [d["symbol"] for d in data[:cfg.top_k]]
