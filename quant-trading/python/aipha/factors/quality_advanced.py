"""高阶质量因子模块

提供 Piotroski F-Score、应计盈余质量、负债质量、盈利稳定性等
高阶复合质量因子。与 ``quality.py`` 中的基础质量因子互补。

包含：
- PiotroskiFScoreFactor: Piotroski F-Score（9 维度质量复合评分）
- AccrualsQualityFactor: 应计盈余质量 — (NI - CFO) / TotalAssets
- DebtQualityFactor: 负债质量复合 — 流动比率 + 速动比率 + 资产负债率
- EarningsStabilityFactor: 盈利稳定性 — ROE 变异系数的负值

数据依赖：
- FactorData.metadata 中的财务指标字段（降级路径）
- PITFinancialProvider（PIT 路径，可选）

设计原则：
- 值越大 = 质量越高 = 越优
- 缺失值返回 NaN + invalid 标记
- 支持 PIT 和 metadata 双路径
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Optional

from aipha.factors.base import Factor, FactorData, FactorResult


def _safe_float(val: object) -> Optional[float]:
    """安全转换为 float，无效值返回 None。"""
    if val is None:
        return None
    try:
        f = float(val)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Piotroski F-Score
# ---------------------------------------------------------------------------


@dataclass
class PiotroskiFScoreFactor(Factor):
    """Piotroski F-Score 因子

    经典的 9 维度二值质量评分（0-9 分），由 Joseph Piotroski (2000)
    在 "Value Investing: The Use of Historical Financial Statement
    Information to Separate Winners from Losers" 中提出。

    **9 个维度**（每个通过 = 1 分，不通过 = 0 分）：

    盈利能力 (4 分):
        1. ROA > 0
        2. CFO > 0
        3. ΔROA > 0（ROA 同比提升）
        4. CFO > NI（应计盈余质量）

    杠杆/流动性 (3 分):
        5. Δ负债率 < 0（杠杆降低）
        6. Δ流动比率 > 0（短期偿债改善）
        7. 无新增股份发行

    运营效率 (2 分):
        8. Δ毛利率 > 0（毛利率提升）
        9. Δ资产周转率 > 0（资产使用效率提升）

    所需 metadata 字段：
        - ``roa``: 当期 ROA
        - ``roa_prev``: 上期 ROA
        - ``cfps``: 每股经营现金流
        - ``eps``: 每股收益
        - ``debt_to_assets``: 资产负债率
        - ``debt_to_assets_prev``: 上期资产负债率
        - ``current_ratio``: 流动比率
        - ``current_ratio_prev``: 上期流动比率
        - ``total_share``: 当期总股本
        - ``total_share_prev``: 上期总股本
        - ``grossprofit_margin``: 毛利率
        - ``grossprofit_margin_prev``: 上期毛利率
        - ``asset_turnover``: 资产周转率（可选，fallback revenue/total_assets）
        - ``asset_turnover_prev``: 上期资产周转率

    Attributes:
        min_score: 最低分过滤（低于此值返回 NaN）
    """

    min_score: Optional[int] = None

    @property
    def name(self) -> str:
        """因子名称: ``PIOTROSKI_F``。"""
        return "PIOTROSKI_F"

    def compute(self, data: FactorData) -> FactorResult:
        """计算 Piotroski F-Score。

        Args:
            data: 因子输入数据

        Returns:
            FactorResult，value 为 0-9 整数分数；数据不足时 value=NaN。
        """
        m = data.metadata
        score = 0
        details: dict[str, int] = {}
        computable = 0

        # --- 盈利能力 (4 项) ---

        # F1: ROA > 0
        roa = _safe_float(m.get("roa"))
        if roa is not None:
            computable += 1
            s = 1 if roa > 0 else 0
            score += s
            details["f1_roa_positive"] = s

        # F2: CFO > 0
        cfps = _safe_float(m.get("cfps"))
        if cfps is not None:
            computable += 1
            s = 1 if cfps > 0 else 0
            score += s
            details["f2_cfo_positive"] = s

        # F3: ΔROA > 0
        roa_prev = _safe_float(m.get("roa_prev"))
        if roa is not None and roa_prev is not None:
            computable += 1
            s = 1 if roa > roa_prev else 0
            score += s
            details["f3_roa_improving"] = s

        # F4: CFO > NI (accruals quality)
        eps = _safe_float(m.get("eps"))
        if cfps is not None and eps is not None:
            computable += 1
            s = 1 if cfps > eps else 0
            score += s
            details["f4_cfo_gt_ni"] = s

        # --- 杠杆/流动性 (3 项) ---

        # F5: 负债率下降
        debt = _safe_float(m.get("debt_to_assets"))
        debt_prev = _safe_float(m.get("debt_to_assets_prev"))
        if debt is not None and debt_prev is not None:
            computable += 1
            s = 1 if debt < debt_prev else 0
            score += s
            details["f5_leverage_down"] = s

        # F6: 流动比率提升
        cr = _safe_float(m.get("current_ratio"))
        cr_prev = _safe_float(m.get("current_ratio_prev"))
        if cr is not None and cr_prev is not None:
            computable += 1
            s = 1 if cr > cr_prev else 0
            score += s
            details["f6_current_ratio_up"] = s

        # F7: 无新增发行
        shares = _safe_float(m.get("total_share"))
        shares_prev = _safe_float(m.get("total_share_prev"))
        if shares is not None and shares_prev is not None:
            computable += 1
            s = 1 if shares <= shares_prev else 0
            score += s
            details["f7_no_dilution"] = s

        # --- 运营效率 (2 项) ---

        # F8: 毛利率提升
        gm = _safe_float(
            m.get("grossprofit_margin") or m.get("gross_margin")
        )
        gm_prev = _safe_float(
            m.get("grossprofit_margin_prev") or m.get("gross_margin_prev")
        )
        if gm is not None and gm_prev is not None:
            computable += 1
            s = 1 if gm > gm_prev else 0
            score += s
            details["f8_margin_improving"] = s

        # F9: 资产周转率提升
        at = _safe_float(m.get("asset_turnover"))
        at_prev = _safe_float(m.get("asset_turnover_prev"))
        if at is not None and at_prev is not None:
            computable += 1
            s = 1 if at > at_prev else 0
            score += s
            details["f9_turnover_improving"] = s

        # 至少需要 5 个维度有数据
        if computable < 5:
            return self._invalid(
                data, f"可计算维度仅 {computable}/9，不足最低 5 个"
            )

        if self.min_score is not None and score < self.min_score:
            return self._invalid(
                data, f"F-Score {score} < 下限 {self.min_score}"
            )

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float(score),
            metadata={
                "score": score,
                "computable_dimensions": computable,
                "details": details,
            },
        )

    def _invalid(self, data: FactorData, reason: str) -> FactorResult:
        """返回无效因子结果。"""
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )


# ---------------------------------------------------------------------------
# 应计盈余质量
# ---------------------------------------------------------------------------


@dataclass
class AccrualsQualityFactor(Factor):
    """应计盈余质量因子

    Accruals = (Net Income - Cash from Operations) / Total Assets

    该因子度量盈利中"非现金"部分占比。高应计盈余通常与盈余操纵、
    未来盈利下滑正相关（Sloan 1996）。

    **因子方向取负**：应计盈余越低（即现金流质量越高），因子值越大。

    所需 metadata 字段：
        - ``n_income``: 归母净利润（或 ``eps`` × ``total_share``）
        - ``n_cashflow_act``: 经营活动现金流净额（或 ``cfps`` × ``total_share``）
        - ``total_assets``: 总资产

    备选降级字段：
        - ``eps``: 每股收益
        - ``cfps``: 每股经营现金流

    Attributes:
        use_per_share: 是否使用每股数据（eps/cfps）替代绝对值
    """

    use_per_share: bool = True

    @property
    def name(self) -> str:
        """因子名称: ``ACCRUALS_QUALITY``。"""
        return "ACCRUALS_QUALITY"

    def compute(self, data: FactorData) -> FactorResult:
        """计算应计盈余质量因子值。

        Args:
            data: 因子输入数据

        Returns:
            FactorResult，value 为负应计盈余比率（越高越好）；
            数据缺失时 value=NaN。
        """
        m = data.metadata

        # 优先使用绝对值口径
        ni = _safe_float(m.get("n_income") or m.get("n_income_attr_p"))
        cfo = _safe_float(m.get("n_cashflow_act"))
        ta = _safe_float(m.get("total_assets"))

        if ni is not None and cfo is not None and ta is not None and ta > 0:
            accruals = (ni - cfo) / ta
            return FactorResult(
                symbol=data.symbol,
                trade_date=data.trade_date,
                factor_name=self.name,
                value=-accruals,  # 取负：低应计 = 高质量
                metadata={"accruals_ratio": accruals, "method": "absolute"},
            )

        # 降级：每股数据
        if self.use_per_share:
            eps = _safe_float(m.get("eps"))
            cfps = _safe_float(m.get("cfps"))
            if eps is not None and cfps is not None:
                accruals_ps = eps - cfps
                return FactorResult(
                    symbol=data.symbol,
                    trade_date=data.trade_date,
                    factor_name=self.name,
                    value=-accruals_ps,  # 取负
                    metadata={
                        "accruals_per_share": accruals_ps,
                        "method": "per_share",
                    },
                )

        return self._invalid(data, "缺少净利润/现金流/总资产数据")

    def _invalid(self, data: FactorData, reason: str) -> FactorResult:
        """返回无效因子结果。"""
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )


# ---------------------------------------------------------------------------
# 负债质量因子
# ---------------------------------------------------------------------------


@dataclass
class DebtQualityFactor(Factor):
    """负债质量复合因子

    综合考量公司的偿债能力与杠杆水平：
    - 流动比率（正向）
    - 速动比率（正向）
    - 资产负债率（负向）

    三项指标做 rank normalize 后加权组合。

    所需 metadata 字段：
        - ``current_ratio``: 流动比率
        - ``quick_ratio``: 速动比率
        - ``debt_to_assets``: 资产负债率（%）

    Attributes:
        w_current: 流动比率权重
        w_quick: 速动比率权重
        w_debt: 资产负债率权重（取负向）
    """

    w_current: float = 0.35
    w_quick: float = 0.35
    w_debt: float = 0.30

    @property
    def name(self) -> str:
        """因子名称: ``DEBT_QUALITY``。"""
        return "DEBT_QUALITY"

    def compute(self, data: FactorData) -> FactorResult:
        """计算负债质量复合因子值。

        单标的计算：返回各维度的加权组合原始值。
        截面排名应通过 ``normalize()`` 进行。

        Args:
            data: 因子输入数据

        Returns:
            FactorResult，value 为加权组合值；数据不足时 value=NaN。
        """
        m = data.metadata
        components: dict[str, float] = {}
        total_weight = 0.0
        weighted_sum = 0.0

        # 流动比率（正向）
        cr = _safe_float(m.get("current_ratio"))
        if cr is not None:
            components["current_ratio"] = cr
            # Cap at 10 to avoid outliers dominating
            capped = min(cr, 10.0)
            weighted_sum += self.w_current * capped
            total_weight += self.w_current

        # 速动比率（正向）
        qr = _safe_float(m.get("quick_ratio"))
        if qr is not None:
            components["quick_ratio"] = qr
            capped = min(qr, 10.0)
            weighted_sum += self.w_quick * capped
            total_weight += self.w_quick

        # 资产负债率（负向，低更好）
        dta = _safe_float(m.get("debt_to_assets"))
        if dta is not None:
            components["debt_to_assets"] = dta
            # 转换：100 - debt_to_assets，使值越大越好
            if 0 <= dta <= 100:
                weighted_sum += self.w_debt * (100.0 - dta) / 100.0 * 10.0
                total_weight += self.w_debt

        if total_weight == 0:
            return self._invalid(data, "缺少流动比率/速动比率/负债率数据")

        value = weighted_sum / total_weight

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=value,
            metadata={"components": components},
        )

    def _invalid(self, data: FactorData, reason: str) -> FactorResult:
        """返回无效因子结果。"""
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )


# ---------------------------------------------------------------------------
# 盈利稳定性因子
# ---------------------------------------------------------------------------


@dataclass
class EarningsStabilityFactor(Factor):
    """盈利稳定性因子

    使用 ROE 或 ROA 的历史变异系数（CV）的负值。
    盈利越稳定（CV 越低），因子值越大。

    所需 metadata 字段：
        - ``roe_history``: ROE 历史列表（按时间正序，至少 3 期）

    或者：
        - ``roe_std``: ROE 标准差
        - ``roe_mean``: ROE 均值

    Attributes:
        min_periods: 最少需要的历史期数
        metric: 使用的盈利指标（``roe`` 或 ``roa``）
        history_field: metadata 中历史列表键名
    """

    min_periods: int = 3
    metric: str = "roe"
    history_field: str = "roe_history"

    @property
    def name(self) -> str:
        """因子名称，格式为 ``EARNINGS_STABILITY_ROE``。"""
        return f"EARNINGS_STABILITY_{self.metric.upper()}"

    def compute(self, data: FactorData) -> FactorResult:
        """计算盈利稳定性因子值。

        Args:
            data: 因子输入数据

        Returns:
            FactorResult，value 为 -CV（越大越稳定）；数据不足时 value=NaN。
        """
        m = data.metadata

        # 方式 1：直接使用历史列表
        history = m.get(self.history_field)
        if history and isinstance(history, (list, tuple)):
            valid = []
            for v in history:
                fv = _safe_float(v)
                if fv is not None:
                    valid.append(fv)

            if len(valid) >= self.min_periods:
                return self._compute_from_series(data, valid)

        # 方式 2：使用预计算的 std + mean
        std_key = f"{self.metric}_std"
        mean_key = f"{self.metric}_mean"
        roe_std = _safe_float(m.get(std_key))
        roe_mean = _safe_float(m.get(mean_key))

        if roe_std is not None and roe_mean is not None:
            if roe_mean == 0:
                return self._invalid(data, f"{self.metric} 均值为 0，无法计算 CV")
            cv = roe_std / abs(roe_mean)
            return FactorResult(
                symbol=data.symbol,
                trade_date=data.trade_date,
                factor_name=self.name,
                value=-cv,
                metadata={"cv": cv, "std": roe_std, "mean": roe_mean},
            )

        return self._invalid(
            data, f"缺少 {self.history_field} 或 {self.metric}_std+mean"
        )

    def _compute_from_series(
        self, data: FactorData, series: list[float]
    ) -> FactorResult:
        """从历史序列计算 CV。"""
        mean = statistics.mean(series)
        if mean == 0:
            return self._invalid(data, f"{self.metric} 历史均值为 0")

        stdev = statistics.stdev(series) if len(series) >= 2 else 0.0
        cv = stdev / abs(mean)

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=-cv,  # 负 CV：越稳定值越大
            metadata={
                "cv": cv,
                "std": stdev,
                "mean": mean,
                "periods": len(series),
            },
        )

    def _invalid(self, data: FactorData, reason: str) -> FactorResult:
        """返回无效因子结果。"""
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )
