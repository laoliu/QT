"""残差动量因子测试

测试覆盖：
- OLS 回归正确性
- 正常流：有效收益率序列 → 残差动量值
- skip_recent：跳过最近 N 天
- t-stat 标准化模式
- 数据不足/缺失场景
- NaN 过滤
- 边界：全相同收益率
- 因子名称自动生成
"""

import math
from datetime import date

import pytest

from aipha.factors.base import FactorData, FactorResult
from aipha.factors.residual_momentum import ResidualMomentumFactor, _ols_residuals


# ============================================================================
# OLS 回归单元测试
# ============================================================================


class TestOLSResiduals:
    """OLS 回归辅助函数测试"""

    def test_perfect_linear(self):
        """完美线性关系 → 残差全为 0"""
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [2.0, 4.0, 6.0, 8.0, 10.0]  # y = 2x
        residuals, alpha, beta = _ols_residuals(y, x)
        assert beta == pytest.approx(2.0, abs=1e-6)
        assert alpha == pytest.approx(0.0, abs=1e-6)
        for r in residuals:
            assert r == pytest.approx(0.0, abs=1e-8)

    def test_with_intercept(self):
        """有截距的线性关系"""
        x = [1.0, 2.0, 3.0, 4.0]
        y = [3.0, 5.0, 7.0, 9.0]  # y = 1 + 2x
        residuals, alpha, beta = _ols_residuals(y, x)
        assert beta == pytest.approx(2.0, abs=1e-6)
        assert alpha == pytest.approx(1.0, abs=1e-6)

    def test_constant_x(self):
        """x 全部相同 → beta = 0"""
        x = [1.0, 1.0, 1.0, 1.0]
        y = [2.0, 4.0, 6.0, 8.0]
        residuals, alpha, beta = _ols_residuals(y, x)
        assert beta == pytest.approx(0.0)

    def test_too_few_points(self):
        """不足 3 个点 → 返回原 y"""
        x = [1.0, 2.0]
        y = [3.0, 4.0]
        residuals, alpha, beta = _ols_residuals(y, x)
        assert residuals == y
        assert alpha == 0.0
        assert beta == 0.0

    def test_length_mismatch(self):
        """长度不匹配 → 返回原 y"""
        x = [1.0, 2.0, 3.0]
        y = [1.0, 2.0]
        residuals, alpha, beta = _ols_residuals(y, x)
        assert residuals == y

    def test_nonzero_residuals(self):
        """有噪声的数据 → 残差之和约 0"""
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [2.1, 3.9, 6.2, 7.8, 10.1]
        residuals, alpha, beta = _ols_residuals(y, x)
        assert sum(residuals) == pytest.approx(0.0, abs=1e-10)


# ============================================================================
# ResidualMomentumFactor 测试
# ============================================================================


class TestResidualMomentumFactor:
    """残差动量因子测试"""

    def _make_data(
        self,
        stock_returns: list[float],
        market_returns: list[float],
        symbol: str = "000001.SZ",
    ) -> FactorData:
        return FactorData(
            symbol=symbol,
            trade_date=date(2025, 1, 10),
            returns=stock_returns,
            metadata={"market_returns": market_returns},
        )

    def test_basic_computation(self):
        """正常计算：60 日残差动量"""
        # 构造 70 日数据（allowance for skip_recent=5）
        n = 70
        mkt = [0.001 * (i % 5 - 2) for i in range(n)]
        # 个股 = 1.2 * market + 0.0002 alpha + noise
        stk = [1.2 * m + 0.0002 for m in mkt]

        factor = ResidualMomentumFactor(lookback_days=60, skip_recent=5)
        result = factor.compute(self._make_data(stk, mkt))

        assert not math.isnan(result.value)
        assert result.metadata["beta"] == pytest.approx(1.2, abs=0.01)
        assert result.metadata["alpha"] == pytest.approx(0.0002, abs=0.001)
        assert result.metadata["n_observations"] == 60

    def test_skip_recent_zero(self):
        """skip_recent=0 时使用最近的数据"""
        n = 65
        mkt = [0.002 * (i % 3 - 1) for i in range(n)]
        stk = [0.8 * m + 0.001 for m in mkt]

        factor = ResidualMomentumFactor(lookback_days=60, skip_recent=0)
        result = factor.compute(self._make_data(stk, mkt))

        assert not math.isnan(result.value)
        assert result.metadata["n_observations"] == 60

    def test_tstat_mode(self):
        """t-stat 标准化模式"""
        n = 70
        mkt = [0.001] * n
        stk = [0.001 + 0.0005 * (1 if i % 2 == 0 else -1) for i in range(n)]

        factor = ResidualMomentumFactor(
            lookback_days=60, skip_recent=5, use_tstat=True
        )
        result = factor.compute(self._make_data(stk, mkt))

        assert not math.isnan(result.value)
        assert "residual_sum" in result.metadata

    def test_no_returns(self):
        """无收益率序列 → invalid"""
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 10),
            metadata={"market_returns": [0.01] * 70},
        )
        factor = ResidualMomentumFactor(lookback_days=60, skip_recent=5)
        result = factor.compute(data)
        assert math.isnan(result.value)
        assert result.metadata.get("reason") == "no_returns"

    def test_no_market_returns(self):
        """无市场收益率 → invalid"""
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 10),
            returns=[0.01] * 70,
        )
        factor = ResidualMomentumFactor(lookback_days=60, skip_recent=5)
        result = factor.compute(data)
        assert math.isnan(result.value)
        assert result.metadata.get("reason") == "no_market_returns"

    def test_insufficient_data(self):
        """数据不足 → invalid"""
        factor = ResidualMomentumFactor(lookback_days=60, skip_recent=5)
        result = factor.compute(self._make_data([0.01] * 30, [0.01] * 30))
        assert math.isnan(result.value)
        assert result.metadata.get("reason") == "insufficient_data"

    def test_nan_filtering(self):
        """NaN 值被过滤"""
        n = 70
        mkt = [0.001] * n
        stk = [0.002] * n
        # 插入一些 NaN
        stk[5] = float("nan")
        stk[10] = float("nan")
        mkt[15] = float("nan")

        factor = ResidualMomentumFactor(lookback_days=60, skip_recent=5)
        result = factor.compute(self._make_data(stk, mkt))
        # 应该仍然成功，但 n_observations < 60
        assert not math.isnan(result.value)
        assert result.metadata["n_observations"] < 60

    def test_all_nan_returns(self):
        """全 NaN → too_few_valid_returns"""
        n = 70
        factor = ResidualMomentumFactor(lookback_days=60, skip_recent=5)
        result = factor.compute(
            self._make_data([float("nan")] * n, [0.01] * n)
        )
        assert math.isnan(result.value)
        assert result.metadata.get("reason") == "too_few_valid_returns"

    def test_name_auto_generation(self):
        """因子名称自动生成"""
        f1 = ResidualMomentumFactor(lookback_days=60, skip_recent=5)
        assert f1.name == "RES_MOM_60_5"

        f2 = ResidualMomentumFactor(lookback_days=120, skip_recent=0)
        assert f2.name == "RES_MOM_120"

        f3 = ResidualMomentumFactor(
            lookback_days=60, skip_recent=5, use_tstat=True
        )
        assert f3.name == "RES_MOM_60_5_tstat"

        f4 = ResidualMomentumFactor(lookback_days=60, name="custom_name")
        assert f4.name == "custom_name"

    def test_zero_market_returns(self):
        """市场收益率全为 0 → beta ≈ 0"""
        n = 70
        factor = ResidualMomentumFactor(lookback_days=60, skip_recent=5)
        result = factor.compute(
            self._make_data([0.001] * n, [0.0] * n)
        )
        assert not math.isnan(result.value)
        assert result.metadata["beta"] == pytest.approx(0.0)

    def test_constant_stock_returns(self):
        """个股收益率全相同"""
        n = 70
        mkt = [0.001 * (i % 3 - 1) for i in range(n)]
        stk = [0.005] * n

        factor = ResidualMomentumFactor(lookback_days=60, skip_recent=5)
        result = factor.compute(self._make_data(stk, mkt))
        assert not math.isnan(result.value)
