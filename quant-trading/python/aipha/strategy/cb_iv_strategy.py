"""CB 隐含波动率(IV)轮动策略。

基于 IV 因子择时选债：低 IVP = 隐含波动率处于历史低位 = 买入信号。
融合 IV 因子（IVP/IVPR/IV溢价率）与传统 CB 因子（价格/溢价率）。

因子架构（3 层）：
Layer 1 — IV 核心因子：
  1. **IVP** (IV Percentile): 低 IVP → IV 处历史低位 → 期权被低估（权重 0.30）
  2. **IVPR** (IV Rank): 低 IVPR → 同义辅助（权重 0.15）
  3. **IV Premium** (IV-HV)/HV: 低/负 → IV 低于 HV → 被低估（权重 0.15）
Layer 2 — CB 本体因子（安全垫）：
  4. **价格** (close): 低价 → 债底保护（权重 0.20）
  5. **溢价率** (cb_over_rate): 低溢价 → 股性弹性（权重 0.15）
Layer 3 — 弱因子：
  6. **规模** (issue_size): 小规模 → 定价偏差（权重 0.05）

理论基础（§11.1 cb_iv_option_pricing_research.md）：
- 中信证券 2024-11: IV 极端低位信号，IVP < 20% 后 20 日超额 2-4%
- BSM 反解 σ_impl 作为 CB 期权定价偏差度量
- IV 均值回归特性（vol clustering + mean reversion）

数据依赖：
- CB 日线: close, cb_value, conv_price, maturity_date (全部来自 bar.metadata)
- 无外部数据下载需求（正股价格通过 cb_value 反算）

Usage::

    配置文件: configs/cb_strategies/cb_iv_factor_8y.yml
    strategy.name: "cb_iv_factor"
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import numpy as np

from aipha.factors.cb_iv_factor import CBIVCalculator
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from aipha.domain.models import Bar
    from aipha.strategy.base import StrategyContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class CBIVConfig(CBRotationConfig):
    """CB IV 策略配置。"""

    top_k: int = 10
    rebalance_days: int = 5
    max_price: float = 150.0
    max_premium: float = 80.0
    min_turnover: float = 20.0
    min_days_to_maturity: int = 60
    stop_loss_pct: float = 0.20
    lot_size: int = 10

    # 次新过滤
    use_age_filter: bool = True
    min_age_days: int = 3

    # IV 因子参数
    iv_lookback: int = 120  # IVP/IVPR 回看天数
    hv_lookback: int = 20   # 历史波动率计算窗口

    # 因子权重
    weight_ivp: float = 0.30         # 低 IVP = 好（ascending）
    weight_ivpr: float = 0.15        # 低 IVPR = 好（ascending）
    weight_iv_premium: float = 0.15  # 低 IV premium = 好（ascending）
    weight_price: float = 0.20       # 低价 = 好（ascending）
    weight_premium: float = 0.15     # 低溢价 = 好（ascending）
    weight_issue_size: float = 0.05  # 小规模 = 好（ascending）


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("cb_iv_factor")
class CBIVStrategy(CBRotationStrategy):
    """CB 隐含波动率轮动策略。"""

    name = "cb_iv_factor"

    def __init__(self, config: CBIVConfig | None = None, **kwargs: Any) -> None:
        super().__init__()
        self._config = config or CBIVConfig(**kwargs)
        self._calculator = CBIVCalculator()
        # IV 历史 {ts_code: deque[float]}
        self._iv_history: dict[str, deque] = {}
        # 正股日收益率历史 {ts_code: deque[float]} 用于 HV
        self._stock_return_history: dict[str, deque] = {}
        # 上一日正股价格 {ts_code: float}
        self._prev_stock_prices: dict[str, float] = {}

    @property
    def config(self) -> CBIVConfig:
        return self._config

    def _on_start_extra(self) -> None:
        """重置 IV 状态。"""
        self._iv_history.clear()
        self._stock_return_history.clear()
        self._prev_stock_prices.clear()

    def _on_bar_update_state(
        self, bars: dict[str, "Bar"], context: "StrategyContext"
    ) -> None:
        """每日更新 IV 序列和正股收益率历史。"""
        for symbol, bar in bars.items():
            if bar.close <= 0 or bar.volume <= 0:
                continue

            meta = bar.metadata or {}
            conv_price = meta.get("conv_price")
            cb_value = meta.get("cb_value")
            maturity_str = meta.get("maturity_date", "")

            if not conv_price or conv_price <= 0:
                continue
            if not maturity_str:
                continue

            # 计算剩余期限
            try:
                mat_date = datetime.strptime(str(maturity_str)[:8], "%Y%m%d")
                today = context.current_time if hasattr(context, "current_time") else datetime.now()
                if hasattr(today, "date"):
                    today = datetime.combine(today.date() if hasattr(today, "date") else today, datetime.min.time()) if not isinstance(today, datetime) else today
                years = (mat_date - today).days / 365.0
            except (ValueError, TypeError):
                continue

            if years < 0.1:
                continue

            # 正股价格（从 cb_value 反算）
            stock_price = 0.0
            if cb_value and cb_value > 0 and conv_price > 0:
                stock_price = cb_value * conv_price / 100.0
            if stock_price <= 0:
                continue

            # 计算 IV
            iv = self._calculator.compute_iv(
                market_price=bar.close,
                stock_price=stock_price,
                conversion_price=conv_price,
                years_to_maturity=years,
            )

            # 存储 IV 历史
            if iv is not None:
                if symbol not in self._iv_history:
                    self._iv_history[symbol] = deque(maxlen=self._config.iv_lookback)
                self._iv_history[symbol].append(iv)

            # 正股收益率（用于 HV）
            prev_price = self._prev_stock_prices.get(symbol, 0.0)
            if prev_price > 0 and stock_price > 0:
                ret = math.log(stock_price / prev_price)
                if symbol not in self._stock_return_history:
                    self._stock_return_history[symbol] = deque(
                        maxlen=self._config.hv_lookback
                    )
                self._stock_return_history[symbol].append(ret)
            self._prev_stock_prices[symbol] = stock_price

    def _score_candidates(
        self,
        candidates: list[CandidateInfo],
        context: "StrategyContext",
    ) -> list[str]:
        """基于 IV 因子排名加权打分，返回 top_k symbols。"""
        if not candidates:
            return []

        cfg = self._config
        rows: list[dict[str, Any]] = []

        for c in candidates:
            symbol = c.symbol
            iv_hist = self._iv_history.get(symbol)

            # 当前 IV
            current_iv = iv_hist[-1] if iv_hist and len(iv_hist) > 0 else None
            if current_iv is None:
                continue

            # IVP / IVPR
            hist_arr = np.array(iv_hist) if iv_hist else np.array([])
            ivp = self._calculator.compute_ivp(current_iv, hist_arr)
            ivpr = self._calculator.compute_ivpr(current_iv, hist_arr)

            # HV (正股历史波动率)
            ret_hist = self._stock_return_history.get(symbol)
            if ret_hist and len(ret_hist) >= 5:
                hv = float(np.std(list(ret_hist))) * math.sqrt(244)
            else:
                hv = 0.3  # 默认 30%

            # IV premium
            iv_premium = self._calculator.compute_iv_premium(current_iv, hv)

            # CB 本体因子
            premium = c.cb_over_rate if c.cb_over_rate is not None else 50.0
            issue_size = c.issue_size if c.issue_size < float("inf") else 30.0

            rows.append({
                "symbol": symbol,
                "ivp": ivp,
                "ivpr": ivpr,
                "iv_premium": iv_premium,
                "close": c.close,
                "premium": premium,
                "issue_size": issue_size,
            })

        if not rows:
            return []

        # 排名加权打分
        n = len(rows)
        if n == 1:
            return [rows[0]["symbol"]]

        # 各因子排名归一化 [0, 1]（ascending: 低值=低rank=好）
        factors = ["ivp", "ivpr", "iv_premium", "close", "premium", "issue_size"]
        weights = [
            cfg.weight_ivp, cfg.weight_ivpr, cfg.weight_iv_premium,
            cfg.weight_price, cfg.weight_premium, cfg.weight_issue_size,
        ]

        # 所有因子都是 ascending（低值优先）
        for factor in factors:
            vals = [r[factor] for r in rows]
            sorted_idx = sorted(range(n), key=lambda i: vals[i])
            for rank, idx in enumerate(sorted_idx):
                rows[idx][f"{factor}_rank"] = rank / max(n - 1, 1)

        # 加权综合得分
        for r in rows:
            r["score"] = sum(
                w * r[f"{f}_rank"] for f, w in zip(factors, weights)
            )

        # 按得分排序（低分=好）
        rows.sort(key=lambda r: r["score"])

        top_k = min(cfg.top_k, n)
        return [r["symbol"] for r in rows[:top_k]]
