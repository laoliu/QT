"""因子分析模块测试"""

from datetime import date, timedelta

import numpy as np
import pytest

from aipha.factors.analysis import (
    FactorAnalyzer,
    FactorAnalysisReport,
    FactorDecayResult,
    ICAnalysisResult,
    QuantileReturnResult,
    analyze_factor,
)


def generate_test_data(
    num_days: int = 60,
    num_symbols: int = 20,
    ic_target: float = 0.05,
    seed: int = 42,
) -> tuple[dict[date, dict[str, float]], dict[date, dict[str, float]]]:
    """生成测试数据

    Args:
        num_days: 天数
        num_symbols: 标的数量
        ic_target: 目标IC（用于生成相关的因子和收益）
        seed: 随机种子

    Returns:
        (factor_data, return_data)
    """
    np.random.seed(seed)
    symbols = [f"SYM{i:03d}" for i in range(num_symbols)]
    start_date = date(2024, 1, 1)

    factor_data: dict[date, dict[str, float]] = {}
    return_data: dict[date, dict[str, float]] = {}

    for day in range(num_days):
        current_date = start_date + timedelta(days=day)
        if current_date.weekday() >= 5:  # 跳过周末
            continue

        # 生成因子值
        factors = np.random.randn(num_symbols)

        # 生成与因子相关的收益（加入噪声）
        noise = np.random.randn(num_symbols)
        returns = ic_target * factors + np.sqrt(1 - ic_target ** 2) * noise

        factor_data[current_date] = {s: f for s, f in zip(symbols, factors)}
        return_data[current_date] = {s: r for s, r in zip(symbols, returns)}

    return factor_data, return_data


class TestICAnalysis:
    """IC分析测试"""

    def test_compute_ic_basic(self):
        """测试基本IC计算"""
        factor_data, return_data = generate_test_data(ic_target=0.1)
        analyzer = FactorAnalyzer(factor_data, return_data, "test_factor")

        result = analyzer.compute_ic()

        assert isinstance(result, ICAnalysisResult)
        assert len(result.ic_series) > 0
        # 目标IC=0.1，实际IC应该接近
        assert -0.3 < result.ic_mean < 0.3

    def test_ic_with_high_correlation(self):
        """测试高相关因子"""
        factor_data, return_data = generate_test_data(ic_target=0.5)
        analyzer = FactorAnalyzer(factor_data, return_data, "high_ic_factor")

        result = analyzer.compute_ic()

        # 高相关因子应该有较高的绝对IC均值
        assert result.ic_abs_mean > 0.05

    def test_ic_with_zero_correlation(self):
        """测试零相关因子"""
        factor_data, return_data = generate_test_data(ic_target=0.0)
        analyzer = FactorAnalyzer(factor_data, return_data, "zero_ic_factor")

        result = analyzer.compute_ic()

        # 零相关因子IC应接近0
        assert abs(result.ic_mean) < 0.2

    def test_ic_positive_rate(self):
        """测试IC为正比例"""
        factor_data, return_data = generate_test_data(ic_target=0.2)
        analyzer = FactorAnalyzer(factor_data, return_data)

        result = analyzer.compute_ic()

        # 正IC因子应该有较高的正比例
        assert 0 <= result.ic_positive_rate <= 1

    def test_ir_calculation(self):
        """测试IR计算"""
        factor_data, return_data = generate_test_data()
        analyzer = FactorAnalyzer(factor_data, return_data)

        result = analyzer.compute_ic()

        # IR = IC均值 / IC标准差
        if result.ic_std > 0:
            expected_ir = result.ic_mean / result.ic_std
            assert abs(result.ir - expected_ir) < 1e-10

    def test_t_stat_and_pvalue(self):
        """测试t统计量和p值"""
        factor_data, return_data = generate_test_data(ic_target=0.3)
        analyzer = FactorAnalyzer(factor_data, return_data)

        result = analyzer.compute_ic()

        # 检查t统计量和p值的合理性
        assert result.t_stat != 0 or result.ic_mean == 0
        assert 0 <= result.p_value <= 1

    def test_spearman_vs_pearson(self):
        """测试Spearman vs Pearson方法"""
        factor_data, return_data = generate_test_data()
        analyzer = FactorAnalyzer(factor_data, return_data)

        spearman_result = analyzer.compute_ic(method="spearman")
        pearson_result = analyzer.compute_ic(method="pearson")

        # 两种方法都应该产生结果
        assert len(spearman_result.ic_series) > 0
        assert len(pearson_result.ic_series) > 0


class TestFactorDecay:
    """因子衰减分析测试"""

    def test_compute_decay(self):
        """测试因子衰减计算"""
        factor_data, return_data = generate_test_data()
        analyzer = FactorAnalyzer(factor_data, return_data)

        result = analyzer.compute_decay()

        assert isinstance(result, FactorDecayResult)
        assert len(result.holding_periods) > 0
        assert result.optimal_period in result.holding_periods

    def test_custom_holding_periods(self):
        """测试自定义持有期"""
        factor_data, return_data = generate_test_data()
        analyzer = FactorAnalyzer(factor_data, return_data)

        periods = [1, 5, 10]
        result = analyzer.compute_decay(holding_periods=periods)

        assert result.holding_periods == periods
        assert len(result.ic_by_period) == len(periods)
        assert len(result.ir_by_period) == len(periods)

    def test_decay_trend(self):
        """测试衰减趋势"""
        factor_data, return_data = generate_test_data(ic_target=0.2)
        analyzer = FactorAnalyzer(factor_data, return_data)

        result = analyzer.compute_decay(holding_periods=[1, 5, 10, 20])

        # 短期IC通常比长期更稳定（取决于数据）
        assert len(result.ic_by_period) == 4


class TestQuantileReturns:
    """分位数收益分析测试"""

    def test_compute_quantile_returns(self):
        """测试分位数收益计算"""
        factor_data, return_data = generate_test_data(ic_target=0.3)
        analyzer = FactorAnalyzer(factor_data, return_data)

        result = analyzer.compute_quantile_returns(num_quantiles=5)

        assert isinstance(result, QuantileReturnResult)
        assert result.num_quantiles == 5
        assert len(result.quantile_returns) == 5

    def test_long_short_return(self):
        """测试多空收益"""
        factor_data, return_data = generate_test_data(ic_target=0.3)
        analyzer = FactorAnalyzer(factor_data, return_data)

        result = analyzer.compute_quantile_returns()

        # 多空收益 = Q1 - Q5
        expected = result.quantile_returns[1] - result.quantile_returns[5]
        assert abs(result.long_short_return - expected) < 1e-10

    def test_monotonicity(self):
        """测试单调性"""
        factor_data, return_data = generate_test_data(ic_target=0.4)
        analyzer = FactorAnalyzer(factor_data, return_data)

        result = analyzer.compute_quantile_returns()

        # 单调性在0-1之间
        assert 0 <= result.monotonicity <= 1

    def test_different_quantile_counts(self):
        """测试不同分位数数量"""
        factor_data, return_data = generate_test_data()
        analyzer = FactorAnalyzer(factor_data, return_data)

        for n in [3, 5, 10]:
            result = analyzer.compute_quantile_returns(num_quantiles=n)
            assert result.num_quantiles == n
            assert len(result.quantile_returns) == n


class TestFactorAnalysisReport:
    """因子分析报告测试"""

    def test_generate_report(self):
        """测试生成报告"""
        factor_data, return_data = generate_test_data()
        analyzer = FactorAnalyzer(factor_data, return_data, "momentum")

        report = analyzer.generate_report()

        assert isinstance(report, FactorAnalysisReport)
        assert report.factor_name == "momentum"
        assert report.ic_analysis is not None
        assert report.decay_analysis is not None
        assert report.quantile_analysis is not None

    def test_report_summary(self):
        """测试报告摘要"""
        factor_data, return_data = generate_test_data()
        analyzer = FactorAnalyzer(factor_data, return_data, "test_factor")

        report = analyzer.generate_report()
        summary = report.summary()

        assert "test_factor" in summary
        assert "IC" in summary
        assert "IR" in summary

    def test_report_without_decay(self):
        """测试不含衰减分析的报告"""
        factor_data, return_data = generate_test_data()
        analyzer = FactorAnalyzer(factor_data, return_data)

        report = analyzer.generate_report(include_decay=False)

        assert report.decay_analysis is None

    def test_report_without_quantile(self):
        """测试不含分位数分析的报告"""
        factor_data, return_data = generate_test_data()
        analyzer = FactorAnalyzer(factor_data, return_data)

        report = analyzer.generate_report(include_quantile=False)

        assert report.quantile_analysis is None


class TestConvenienceFunction:
    """便捷函数测试"""

    def test_analyze_factor(self):
        """测试analyze_factor函数"""
        factor_data, return_data = generate_test_data()

        report = analyze_factor(factor_data, return_data, "my_factor")

        assert isinstance(report, FactorAnalysisReport)
        assert report.factor_name == "my_factor"


class TestEdgeCases:
    """边界情况测试"""

    def test_empty_data(self):
        """测试空数据"""
        analyzer = FactorAnalyzer({}, {}, "empty")

        result = analyzer.compute_ic()
        assert result.ic_mean == 0.0
        assert result.ir == 0.0

    def test_single_day(self):
        """测试单日数据"""
        d = date(2024, 1, 1)
        factor_data = {d: {"A": 1.0, "B": 2.0}}
        return_data = {d: {"A": 0.01, "B": 0.02}}

        analyzer = FactorAnalyzer(factor_data, return_data)
        result = analyzer.compute_ic()

        # 单日数据可能没有足够样本
        assert isinstance(result, ICAnalysisResult)

    def test_few_symbols(self):
        """测试少量标的"""
        d = date(2024, 1, 1)
        factor_data = {d: {"A": 1.0, "B": 2.0, "C": 3.0}}
        return_data = {d: {"A": 0.01, "B": 0.02, "C": 0.03}}

        analyzer = FactorAnalyzer(factor_data, return_data)
        result = analyzer.compute_ic()

        # 应该能处理少量标的
        assert isinstance(result, ICAnalysisResult)

    def test_mismatched_symbols(self):
        """测试标的不匹配"""
        d = date(2024, 1, 1)
        factor_data = {d: {"A": 1.0, "B": 2.0}}
        return_data = {d: {"C": 0.01, "D": 0.02}}

        analyzer = FactorAnalyzer(factor_data, return_data)
        result = analyzer.compute_ic()

        # 没有共同标的，应该返回空结果
        assert result.ic_mean == 0.0


# ============================================================================
# 自动化脚本函数测试 (scripts/factor_ic_decay.py)
# ============================================================================


class TestFactorICDecayScript:
    """测试 factor_ic_decay.py 的核心函数"""

    def test_analyze_single_factor_basic(self):
        """基本因子分析返回正确报告"""
        from scripts.factor_ic_decay import analyze_single_factor
        factor_data, return_data = generate_test_data(num_days=60, ic_target=0.1)
        report = analyze_single_factor("test_factor", factor_data, return_data)
        assert report is not None
        assert report.factor_name == "test_factor"
        assert report.ic_analysis is not None
        assert report.decay_analysis is not None
        assert report.quantile_analysis is not None

    def test_analyze_single_factor_empty_data(self):
        """空数据返回 None"""
        from scripts.factor_ic_decay import analyze_single_factor
        report = analyze_single_factor("empty", {}, {})
        assert report is None

    def test_analyze_single_factor_insufficient_dates(self):
        """不够 10 天时返回 None"""
        from scripts.factor_ic_decay import analyze_single_factor
        d = date(2024, 1, 1)
        factor_data = {d: {"A": 1.0}}
        return_data = {d: {"A": 0.01}}
        report = analyze_single_factor("short", factor_data, return_data)
        assert report is None

    def test_analyze_all_factors(self):
        """批量分析多因子"""
        from scripts.factor_ic_decay import analyze_all_factors
        # 无数据目录 → 全部返回 None → 空结果
        results = analyze_all_factors(["sue", "roe"])
        assert isinstance(results, dict)
        # 无离线数据 → 空
        assert len(results) == 0

    def test_report_to_dict(self):
        """报告转字典可序列化"""
        from scripts.factor_ic_decay import report_to_dict
        factor_data, return_data = generate_test_data(num_days=60)
        analyzer = FactorAnalyzer(factor_data, return_data, "test")
        report = analyzer.generate_report()
        d = report_to_dict(report)
        assert isinstance(d, dict)
        assert d["factor_name"] == "test"
        assert "ic" in d
        assert "decay" in d
        assert "quantile" in d
        # 验证可 JSON 序列化
        import json
        json_str = json.dumps(d, ensure_ascii=False)
        assert len(json_str) > 0

    def test_save_json_report(self, tmp_path):
        """JSON 报告保存到文件"""
        from scripts.factor_ic_decay import save_json_report
        import json
        factor_data, return_data = generate_test_data(num_days=60)
        analyzer = FactorAnalyzer(factor_data, return_data, "test")
        report = analyzer.generate_report()
        output = tmp_path / "report.json"
        save_json_report({"test": report}, str(output))
        assert output.exists()
        with open(output) as f:
            data = json.load(f)
        assert "test" in data

    def test_print_summary(self):
        """文本摘要不崩溃"""
        from scripts.factor_ic_decay import print_summary
        factor_data, return_data = generate_test_data(num_days=60)
        analyzer = FactorAnalyzer(factor_data, return_data, "momentum_20d")
        report = analyzer.generate_report()
        summary = print_summary({"momentum_20d": report})
        assert "momentum_20d" in summary
        assert "IC均值" in summary

    def test_registered_factors_not_empty(self):
        """已注册因子列表非空"""
        from scripts.factor_ic_decay import REGISTERED_FACTORS
        assert len(REGISTERED_FACTORS) >= 10

    def test_custom_holding_periods(self):
        """自定义持有期传递到分析"""
        from scripts.factor_ic_decay import analyze_single_factor
        factor_data, return_data = generate_test_data(num_days=60)
        report = analyze_single_factor(
            "test", factor_data, return_data,
            holding_periods=[1, 5, 10],
        )
        assert report is not None
        assert report.decay_analysis is not None
        assert report.decay_analysis.holding_periods == [1, 5, 10]
