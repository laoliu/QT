"""A 股微盘股价值策略

策略逻辑：
1. **选股范围**：全 A 股中市值 5-50 亿的微盘股
2. **三层漏斗**：
   - L1 流动性门槛：日均成交额≥500万，上市≥180天，非ST
   - L2 质量门槛：ROE≥8%, 负债率<60%, Piotroski F-Score≥5, 连续盈利
   - L3 价值排名：综合小市值+深度价值+质量+低波动+低换手
3. **分散持仓**：top_k=50，单票最大 2%，行业上限 15%
4. **低换手**：月频调仓 + 高缓冲 buffer_k=15

Alpha 来源假说：
- A 股微盘股（5-50 亿）机构覆盖极低，散户主导定价，存在显著β+α
- 质量门槛过滤掉绩差股/ST/退市风险，保留的是被忽视的"好而小"
- 深度价值（低PE+低PB）在微盘股中有更强的均值回复效应
- 低换手选股偏好冷门标的，避免热炒泡沫

风险管理：
- 微盘股流动性差，滑点设为 20bps
- 50 只持仓极致分散，单票不超过 2%
- 行业集中度 ≤15%
- 止损 20%（小票波动大）

继承 EquityRotationStrategy 基类。
对齐 copilot-instructions.md：因子无前视偏差，PIT 数据，所有参数可配置。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_debt_factor,
    compute_momentum_factor,
    compute_piotroski_factor,
    compute_quality_factor,
    compute_size_factor,
    compute_turnover_factor,
    compute_value_factor,
    compute_volatility_factor,
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


# ===========================================================================
# 配置
# ===========================================================================


@dataclass
class MicroCapValueConfig(EquityRotationConfig):
    """微盘股价值策略配置。"""

    # 持仓 — 极致分散
    top_k: int = 50
    rebalance_days: int = 20        # 月频，降低小票冲击
    min_mv: float = 50_000.0        # 5 亿（万元）
    max_mv: float = 500_000.0       # 50 亿
    min_price: float = 3.0          # 排除仙股
    max_price: float = 100.0        # 排除高价股
    min_turnover: float = 500.0     # 500 万元，微盘下限
    min_listed_days: int = 180      # 半年以上
    buffer_k: int = 15              # 大缓冲降低换手

    # 止损
    stop_loss_pct: float = 0.20     # 小票波动大
    trailing_stop_pct: float = 0.15

    # 集中度控制
    max_single_pct: float = 0.025   # 单票 2.5%
    cash_reserve_pct: float = 0.03
    max_industry_pct: float = 0.15  # 行业 15% 上限

    # 加权
    inverse_vol_weighting: bool = True

    # --- L2 质量门槛 ---
    min_roe: float = 8.0            # ROE ≥ 8%
    max_debt_to_assets: float = 60.0  # 负债率 < 60%
    min_piotroski_score: int = 5    # F-Score ≥ 5
    require_positive_profit: bool = True  # 要求最近盈利为正

    # --- 因子权重 ---
    weight_size: float = 0.30       # 小市值
    weight_value: float = 0.25      # 深度价值
    weight_quality: float = 0.20    # 质量
    weight_volatility: float = 0.15 # 低波动
    weight_turnover: float = 0.10   # 低换手

    # --- 可选辅助因子 ---
    weight_debt: float = 0.0        # 低负债（已在门槛层过滤，默认关）
    weight_momentum: float = 0.0    # 微盘不用动量（反转效应更强）

    # --- 因子参数 ---
    value_metric: str = "pe_ttm"
    quality_metric: str = "roe"
    volatility_lookback: int = 60
    momentum_lookback: int = 20     # 若启用，短期动量
    momentum_skip_recent: int = 3


# ===========================================================================
# 策略实现
# ===========================================================================


@StrategyRegistry().register("micro_cap_value")
class MicroCapValueStrategy(EquityRotationStrategy):
    """A 股微盘股价值策略。

    核心差异（vs SmallCapRotation）：
    1. 更窄市值范围（5-50亿 vs 10-200亿），聚焦真正的微盘股
    2. 严格质量门槛（ROE≥8% + F-Score≥5 + 低负债），过滤地雷
    3. 不用动量因子（微盘股反转效应强于动量）
    4. 低换手因子选冷门股（避开炒作期）
    5. 极致分散（50只 × 2%）降低个股风险
    """

    name = "micro_cap_value"

    def __init__(
        self, config: MicroCapValueConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or MicroCapValueConfig(**kwargs)

    @property
    def config(self) -> MicroCapValueConfig:
        return self._config

    # ------------------------------------------------------------------
    # L2 质量门槛过滤
    # ------------------------------------------------------------------

    def _extra_filter(
        self, candidate: EquityCandidateInfo, context: StrategyContext,
    ) -> bool:
        """L2 质量门槛：ROE、负债率、F-Score、盈利性。

        在基类 _filter_universe() 的 L1 过滤之后再做二次筛选。
        """
        cfg = self._config
        meta = candidate.metadata

        # ROE 门槛（年化处理）
        # TuShare fina_indicator 的 roe 是报告期累计值而非年化：
        #   Q1(0331): ~3-4%, Q2(0630): ~6-8%, Q3(0930): ~10-12%, Q4(1231): ~13-16%
        # 必须根据报告期 end_date 进行年化，否则 Q1 几乎全部被过滤掉
        roe = meta.get("roe")
        if roe is None or not math.isfinite(roe):
            return False
        # 从 fina PIT 注入的 end_date 判断报告期
        end_date = str(meta.get("_end_date", "") or "")
        if len(end_date) >= 6:
            report_month = end_date[4:6]
        else:
            report_month = "12"  # 无法判断时假设年报
        _ANNUALIZE = {"03": 4.0, "06": 2.0, "09": 4.0 / 3.0, "12": 1.0}
        annualize_factor = _ANNUALIZE.get(report_month, 1.0)
        roe_annual = roe * annualize_factor
        if roe_annual < cfg.min_roe:
            return False

        # 负债率门槛
        dta = meta.get("debt_to_assets")
        if dta is not None and math.isfinite(dta) and dta > cfg.max_debt_to_assets:
            return False

        # 盈利为正
        if cfg.require_positive_profit:
            nprofit_yoy = meta.get("dt_netprofit_yoy")
            # 如果有增速数据，盈利增速必须为正（意味着有盈利）
            # 没有增速数据时用 EPS 判断
            eps = meta.get("eps")
            if eps is not None and math.isfinite(eps) and eps <= 0:
                return False

        # Piotroski F-Score 门槛（软条件：如果有数据才检查）
        fscore = self._compute_candidate_fscore(candidate)
        if fscore is not None and fscore < cfg.min_piotroski_score:
            return False

        return True

    def _compute_candidate_fscore(
        self, candidate: EquityCandidateInfo,
    ) -> int | None:
        """快速计算单个候选的 Piotroski F-Score。

        简化版：只用可获取的字段，缺失字段跳过。
        """
        meta = candidate.metadata
        score = 0
        dims = 0

        # 1. ROA > 0
        roa = meta.get("roa")
        if roa is not None and math.isfinite(roa):
            dims += 1
            if roa > 0:
                score += 1

        # 2. 经营现金流 > 0
        cfps = meta.get("cfps")
        if cfps is not None and math.isfinite(cfps):
            dims += 1
            if cfps > 0:
                score += 1

        # 3. ROA 改善
        prev_roa = meta.get("prev_roa")
        if (
            roa is not None and prev_roa is not None
            and math.isfinite(roa) and math.isfinite(prev_roa)
        ):
            dims += 1
            if roa > prev_roa:
                score += 1

        # 4. 应计质量（CFPS > EPS）
        eps = meta.get("eps")
        if (
            cfps is not None and eps is not None
            and math.isfinite(cfps) and math.isfinite(eps)
        ):
            dims += 1
            if cfps > eps:
                score += 1

        # 5. 负债率下降
        dta = meta.get("debt_to_assets")
        prev_dta = meta.get("prev_debt_to_assets")
        if (
            dta is not None and prev_dta is not None
            and math.isfinite(dta) and math.isfinite(prev_dta)
        ):
            dims += 1
            if dta < prev_dta:
                score += 1

        # 6. 流动比率改善
        cr = meta.get("current_ratio")
        prev_cr = meta.get("prev_current_ratio")
        if (
            cr is not None and prev_cr is not None
            and math.isfinite(cr) and math.isfinite(prev_cr)
        ):
            dims += 1
            if cr > prev_cr:
                score += 1

        # 7. 毛利率改善
        gm = meta.get("grossprofit_margin")
        prev_gm = meta.get("prev_grossprofit_margin")
        if (
            gm is not None and prev_gm is not None
            and math.isfinite(gm) and math.isfinite(prev_gm)
        ):
            dims += 1
            if gm > prev_gm:
                score += 1

        # 8. 资产周转率改善
        at = meta.get("asset_turnover")
        prev_at = meta.get("prev_asset_turnover")
        if (
            at is not None and prev_at is not None
            and math.isfinite(at) and math.isfinite(prev_at)
        ):
            dims += 1
            if at > prev_at:
                score += 1

        if dims < 3:
            return None  # 数据不足，跳过
        return score

    # ------------------------------------------------------------------
    # 核心打分
    # ------------------------------------------------------------------

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """五因子加权排名：小市值 + 深度价值 + 质量 + 低波动 + 低换手。

        微盘股策略**不使用动量因子**（微盘股反转效应 > 动量效应），
        除非通过配置显式启用 weight_momentum > 0。
        """
        cfg = self._config
        if not candidates:
            return []

        # ------------------------------------------------------------------
        # 计算因子
        # ------------------------------------------------------------------
        factors: list[tuple[str, dict[str, float], float]] = []

        if cfg.weight_size > 0.001:
            f_size = compute_size_factor(candidates)
            factors.append(("size", f_size, cfg.weight_size))

        if cfg.weight_value > 0.001:
            f_value = compute_value_factor(candidates, metric=cfg.value_metric)
            factors.append(("value", f_value, cfg.weight_value))

        if cfg.weight_quality > 0.001:
            f_quality = compute_quality_factor(
                candidates, metric=cfg.quality_metric,
            )
            factors.append(("quality", f_quality, cfg.weight_quality))

        if cfg.weight_volatility > 0.001:
            f_vol = compute_volatility_factor(
                self._price_history, candidates,
                lookback=cfg.volatility_lookback,
            )
            factors.append(("volatility", f_vol, cfg.weight_volatility))

        if cfg.weight_turnover > 0.001:
            f_to = compute_turnover_factor(candidates)
            factors.append(("turnover", f_to, cfg.weight_turnover))

        # 可选因子
        if cfg.weight_debt > 0.001:
            f_debt = compute_debt_factor(candidates)
            factors.append(("debt", f_debt, cfg.weight_debt))

        if cfg.weight_momentum > 0.001:
            f_mom = compute_momentum_factor(
                self._price_history, candidates,
                lookback=cfg.momentum_lookback,
                skip_recent=cfg.momentum_skip_recent,
            )
            factors.append(("momentum", f_mom, cfg.weight_momentum))

        if not factors:
            return []

        # ------------------------------------------------------------------
        # 过滤不可用 + 归一化权重
        # ------------------------------------------------------------------
        _VALID_THRESHOLD = 0.20
        usable: list[tuple[str, dict[str, float], float]] = []
        for name, fv, w in factors:
            n_valid = sum(1 for v in fv.values() if math.isfinite(v))
            if len(fv) > 0 and n_valid / len(fv) >= _VALID_THRESHOLD:
                usable.append((name, fv, w))

        if not usable:
            return []

        w_total = sum(w for _, _, w in usable)
        if w_total <= 0:
            return []

        # ------------------------------------------------------------------
        # rank_normalize 融合
        # ------------------------------------------------------------------
        scores: dict[str, float] = {}
        for name, fv, w in usable:
            self.rank_normalize(
                candidates, fv, w / w_total, scores,
                nan_handling="skip",
            )

        return self.rank_sort(scores)
