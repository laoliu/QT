"""A 股多因子基线策略（C-1）

知识库 G1 首要行动：A 股多因子基线。四个正交因子等权合成：
  1. EP（盈利收益率）     — 1/PE_TTM，知识库 A1.1 价值因子，月频 IC 2-4%
  2. ROE 质量            — 最新季度 ROE，知识库 A1.2 质量因子，月频 IC 1-3%
  3. MOM_12_1            — 过去 12 月去掉近 1 月动量，知识库 A2.1，月频 IC 1-3%
  4. IVOL 反转           — 特异波动率反向信号，知识库 A2.3，月频 IC 2-4%

成功标准（知识库 1.2 硬性门槛）：
  - 年化收益 ≥ 20%，最大回撤 ≤ 30%，夏普 ≥ 0.8
  - Walk-Forward 验证：DSR 显著 + PBO < 0.50 + OOS/IS 夏普衰减 ≤ 50%

回测配置：configs/backtest/a_stock_multifactor_baseline_10y.yml
  - 范围：2015-01-05 ~ 当前（≥10 年）
  - UniversePolicy：排除 ST/退市/停牌，全 A 股 30-500 亿市值
  - 调仓频率：每 20 个交易日
  - 持仓：top_k=30 等权

对齐 copilot-instructions.md §4.3：
  - 策略层仅输出 OrderIntent，不操作资金/持仓
  - T+1，涨跌停检测，手数=100 股
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.alpha_factors_p0 import compute_amihud_illiquidity_factor
from aipha.factors.equity_factors import (
    compute_ep_factor,
    compute_ivol_factor,
    compute_momentum_factor,
    compute_quality_factor,
)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class AStockMultiFactorConfig(EquityRotationConfig):
    """A 股多因子基线策略配置（知识库 G1）。

    四因子等权，各因子权重之和应 = 1.0（run-time 不强制校验，由策略控制）。
    """

    # ── 因子权重 ──────────────────────────────────────────────────────────
    weight_ep: float = 0.25           # EP 盈利收益率权重
    weight_roe: float = 0.25          # ROE 质量权重
    weight_mom: float = 0.25          # MOM_12_1 动量权重
    weight_ivol: float = 0.25         # IVOL 特异波动率反转权重

    # ── 动量参数 ─────────────────────────────────────────────────────────
    mom_lookback: int = 250           # MOM_12_1：约 12 月（250 交易日）
    mom_skip_recent: int = 20         # 跳过近 1 月（约 20 交易日）

    # ── IVOL 参数 ────────────────────────────────────────────────────────
    ivol_lookback: int = 60           # IVOL 计算窗口（60 交易日）
    # ── Amihud 非流动性因子 (P1.1) ──────────────────────────────────
    weight_amihud: float = 0.0        # 默认 0.0 保持向后兼容，设为>0 即启用因子
    amihud_window: int = 20           # Amihud 计算窗口（20 交易日）
    # ── 质量/价值指标 ────────────────────────────────────────────────────
    ep_pe_metric: str = "pe_ttm"      # EP 使用的估值指标
    roe_metric: str = "roe"           # ROE 质量指标

    def __post_init__(self) -> None:
        """同步子类因子参数到基类历史窗口控制参数。

        基类 ``_update_history()`` 使用 ``momentum_lookback`` / ``volatility_lookback``
        决定 ``_price_history`` 保留长度:
            max_len = max(momentum_lookback, volatility_lookback) + 5

        子类 MOM_12_1 实际需要 ``mom_lookback + mom_skip_recent + 1`` 条历史，
        所以 ``momentum_lookback`` 必须 ≥ ``mom_lookback + mom_skip_recent``。
        """
        # MOM_12_1: need = mom_lookback + mom_skip_recent + 1
        # 基类 max_len = momentum_lookback + BUFFER(5)
        # → momentum_lookback 必须 ≥ mom_lookback + mom_skip_recent
        self.momentum_lookback = max(
            self.mom_lookback + self.mom_skip_recent,
            self.momentum_lookback,
        )
        self.volatility_lookback = max(self.ivol_lookback, self.volatility_lookback)


# ---------------------------------------------------------------------------
# 策略
# ---------------------------------------------------------------------------

@StrategyRegistry().register("a_stock_multifactor")
class AStockMultiFactorStrategy(EquityRotationStrategy):
    """A 股多因子基线策略（知识库 G1 四因子等权合成）。

    打分流程：
    1. 基类 ``_filter_candidates()`` 过滤（价格/市值/ST/停牌/上市天数）
    2. 四因子 rank_normalize → 等权加和
    3. 选 top_k，等权持仓
    """

    def __init__(self, config: AStockMultiFactorConfig | None = None, **kwargs) -> None:
        if config is None and kwargs:
            # 从扁平参数字典构建 config（config_runner 路径）
            valid = {
                k: v for k, v in kwargs.items()
                if k in AStockMultiFactorConfig.__dataclass_fields__
            }
            config = AStockMultiFactorConfig(**valid)
        super().__init__()
        self._cfg = config or AStockMultiFactorConfig()

    @property
    def config(self) -> AStockMultiFactorConfig:
        return self._cfg

    @classmethod
    def from_params(cls, **params) -> "AStockMultiFactorStrategy":
        """从参数字典创建策略（供 config_runner 使用）。"""
        cfg = AStockMultiFactorConfig(**{
            k: v for k, v in params.items()
            if k in AStockMultiFactorConfig.__dataclass_fields__
        })
        return cls(cfg)

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """四因子等权打分：EP + ROE + MOM_12_1 + IVOL_反转。"""
        if not candidates:
            return []
        cfg = self._cfg
        scores: dict[str, float] = {c.symbol: 0.0 for c in candidates}

        # ── 因子 1：EP（盈利收益率）────────────────────────────────────
        if cfg.weight_ep > 0:
            ep_vals = compute_ep_factor(candidates, metric=cfg.ep_pe_metric)
            self.rank_normalize(candidates, ep_vals, weight=cfg.weight_ep,
                                scores=scores)

        # ── 因子 2：ROE 质量 ────────────────────────────────────────────
        if cfg.weight_roe > 0:
            roe_vals = compute_quality_factor(candidates, metric=cfg.roe_metric)
            self.rank_normalize(candidates, roe_vals, weight=cfg.weight_roe,
                                scores=scores)

        # ── 因子 3：MOM_12_1 ────────────────────────────────────────────
        if cfg.weight_mom > 0:
            mom_vals = compute_momentum_factor(
                self._price_history,
                candidates,
                lookback=cfg.mom_lookback,
                skip_recent=cfg.mom_skip_recent,
                invert=False,
            )
            self.rank_normalize(candidates, mom_vals, weight=cfg.weight_mom,
                                scores=scores)

        # ── 因子 4：IVOL 反转（低 IVOL 优先） ───────────────────────────
        if cfg.weight_ivol > 0:
            ivol_vals = compute_ivol_factor(
                self._price_history,
                candidates,
                market_returns=None,   # 无市场数据时退化为总波动率
                lookback=cfg.ivol_lookback,
            )
            self.rank_normalize(candidates, ivol_vals, weight=cfg.weight_ivol,
                                scores=scores)
        # ── 因子 5：Amihud 非流动性 (P1.1, 默认禁用) ──────────────────────
        if cfg.weight_amihud > 0:
            amihud_vals = compute_amihud_illiquidity_factor(
                candidates, window=cfg.amihud_window,
            )
            # Amihud 高 = 非流动性高 = 预期收益高 → 高值高分（rank_normalize 默认 reverse=True）
            self.rank_normalize(candidates, amihud_vals, weight=cfg.weight_amihud,
                                scores=scores)
        return self.rank_sort(scores)

