"""因子研究报告模块测试"""

from datetime import date, timedelta
import tempfile
from pathlib import Path
import numpy as np
import pytest

from aipha.factors.research_report import (
    PeriodAnalysis,
    FactorResearchReport,
    FactorResearchReporter,
    generate_factor_report,
)
from aipha.factors.analysis import ICAnalysisResult


class TestPeriodAnalysis:
    """PeriodAnalysis测试"""

    def test_basic_creation(self):
        """测试基本创建"""
        analysis = PeriodAnalysis(
            period_type="yearly",
            periods=["2021", "2022", "2023"],
            ic_by_period={"2021": 0.03, "2022": 0.02, "2023": 0.04},
            return_by_period={"2021": 0.10, "2022": -0.05, "2023": 0.15},
        )

        assert analysis.period_type == "yearly"
        assert len(analysis.periods) == 3
        assert analysis.ic_by_period["2022"] == 0.02


class TestFactorResearchReport:
    """FactorResearchReport测试"""

    def _create_sample_report(self) -> FactorResearchReport:
        """创建示例报告"""
        ic_analysis = ICAnalysisResult(
            ic_series={date(2023, 1, 1): 0.05},
            ic_mean=0.05,
            ic_std=0.08,
            ir=0.625,
            ic_positive_rate=0.6,
            t_stat=2.5,
            p_value=0.01,
            ic_abs_mean=0.06,
        )

        return FactorResearchReport(
            factor_name="TestMomentum",
            analysis_period=(date(2023, 1, 1), date(2023, 12, 31)),
            sample_size=10000,
            ic_analysis=ic_analysis,
            decay_analysis=None,
            quantile_analysis=None,
            yearly_analysis=None,
            monthly_analysis=None,
            factor_stats={
                "均值": 0.0,
                "标准差": 1.0,
                "最小值": -3.5,
                "最大值": 4.2,
            },
        )

    def test_summary(self):
        """测试摘要生成"""
        report = self._create_sample_report()
        summary = report.summary()

        assert "因子研究报告" in summary
        assert "TestMomentum" in summary
        assert "IC分析" in summary
        assert "0.05" in summary  # IC均值
        assert "2023" in summary

    def test_to_dict(self):
        """测试转换为字典"""
        report = self._create_sample_report()
        result = report.to_dict()

        assert result["factor_name"] == "TestMomentum"
        assert result["sample_size"] == 10000
        assert result["ic_analysis"]["ic_mean"] == 0.05
        assert result["ic_analysis"]["ir"] == 0.625

    def test_to_html(self):
        """测试HTML生成"""
        report = self._create_sample_report()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "report.html"
            report.to_html(output_path)

            assert output_path.exists()
            content = output_path.read_text(encoding="utf-8")
            assert "TestMomentum" in content
            assert "IC均值" in content
            assert "html" in content.lower()


class TestFactorResearchReporter:
    """FactorResearchReporter测试"""

    def _generate_test_data(
        self,
        n_days: int = 100,
        n_symbols: int = 50,
        factor_ic: float = 0.05,
        seed: int = 42,
    ) -> tuple[dict, dict]:
        """生成测试数据"""
        rng = np.random.default_rng(seed)
        start_date = date(2023, 1, 1)

        factor_values = {}
        forward_returns = {}

        symbols = [f"S{i:03d}" for i in range(n_symbols)]

        for d in range(n_days):
            current_date = start_date + timedelta(days=d * 2)  # 跳过周末

            # 因子值：标准正态分布
            factors = {s: rng.standard_normal() for s in symbols}
            factor_values[current_date] = factors

            # 未来收益：与因子值有一定相关性
            returns = {}
            for s in symbols:
                # 收益 = IC * 因子 + 噪声
                noise = rng.standard_normal() * 0.05
                returns[s] = factor_ic * factors[s] + noise

            forward_returns[current_date] = returns

        return factor_values, forward_returns

    def test_basic_report_generation(self):
        """测试基本报告生成"""
        factor_values, forward_returns = self._generate_test_data()

        reporter = FactorResearchReporter(factor_values, forward_returns)
        report = reporter.generate_report("TestFactor")

        assert isinstance(report, FactorResearchReport)
        assert report.factor_name == "TestFactor"
        assert report.sample_size > 0
        assert report.ic_analysis is not None

    def test_with_decay_analysis(self):
        """测试包含IC衰减"""
        factor_values, forward_returns = self._generate_test_data()

        reporter = FactorResearchReporter(factor_values, forward_returns)
        report = reporter.generate_report("TestFactor", include_decay=True)

        assert report.decay_analysis is not None
        assert len(report.decay_analysis.ic_by_period) > 0

    def test_with_quantile_analysis(self):
        """测试包含分组收益"""
        factor_values, forward_returns = self._generate_test_data()

        reporter = FactorResearchReporter(factor_values, forward_returns)
        report = reporter.generate_report("TestFactor", include_quantile=True, n_quantiles=5)

        assert report.quantile_analysis is not None
        assert report.quantile_analysis.num_quantiles == 5

    def test_with_yearly_analysis(self):
        """测试包含年度分析"""
        # 生成跨年数据
        factor_values, forward_returns = self._generate_test_data(n_days=400)

        reporter = FactorResearchReporter(factor_values, forward_returns)
        report = reporter.generate_report("TestFactor", include_yearly=True)

        assert report.yearly_analysis is not None
        assert len(report.yearly_analysis.ic_by_period) > 0

    def test_factor_stats(self):
        """测试因子统计"""
        factor_values, forward_returns = self._generate_test_data()

        reporter = FactorResearchReporter(factor_values, forward_returns)
        report = reporter.generate_report("TestFactor")

        stats = report.factor_stats
        assert "均值" in stats
        assert "标准差" in stats
        assert "偏度" in stats
        assert "峰度" in stats

    def test_spearman_vs_pearson(self):
        """测试不同相关系数方法"""
        factor_values, forward_returns = self._generate_test_data()

        reporter_spearman = FactorResearchReporter(factor_values, forward_returns, method="spearman")
        reporter_pearson = FactorResearchReporter(factor_values, forward_returns, method="pearson")

        report_sp = reporter_spearman.generate_report("Test")
        report_pe = reporter_pearson.generate_report("Test")

        # 两种方法应该得到相近的结果
        assert isinstance(report_sp.ic_analysis.ic_mean, float)
        assert isinstance(report_pe.ic_analysis.ic_mean, float)

    def test_empty_data(self):
        """测试空数据"""
        reporter = FactorResearchReporter({}, {})
        report = reporter.generate_report("Empty")

        assert report.sample_size == 0

    def test_html_with_full_report(self):
        """测试完整报告的HTML"""
        factor_values, forward_returns = self._generate_test_data()

        reporter = FactorResearchReporter(factor_values, forward_returns)
        report = reporter.generate_report(
            "FullFactor",
            include_decay=True,
            include_quantile=True,
            include_yearly=True,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "full_report.html"
            report.to_html(output_path)

            content = output_path.read_text(encoding="utf-8")
            assert "IC衰减分析" in content
            assert "分组收益" in content


class TestConvenienceFunction:
    """便捷函数测试"""

    def test_generate_factor_report(self):
        """测试generate_factor_report"""
        rng = np.random.default_rng(42)
        start_date = date(2023, 1, 1)

        factor_values = {}
        forward_returns = {}

        for d in range(50):
            current_date = start_date + timedelta(days=d)
            factor_values[current_date] = {f"S{i}": rng.standard_normal() for i in range(20)}
            forward_returns[current_date] = {f"S{i}": rng.standard_normal() * 0.02 for i in range(20)}

        report = generate_factor_report(factor_values, forward_returns, "QuickTest")

        assert isinstance(report, FactorResearchReport)
        assert report.factor_name == "QuickTest"

    def test_generate_with_output(self):
        """测试生成并输出HTML"""
        rng = np.random.default_rng(42)
        start_date = date(2023, 1, 1)

        factor_values = {}
        forward_returns = {}

        for d in range(50):
            current_date = start_date + timedelta(days=d)
            factor_values[current_date] = {f"S{i}": rng.standard_normal() for i in range(20)}
            forward_returns[current_date] = {f"S{i}": rng.standard_normal() * 0.02 for i in range(20)}

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "quick_report.html"
            report = generate_factor_report(factor_values, forward_returns, "QuickTest", output_path)

            assert output_path.exists()
            assert report.factor_name == "QuickTest"


class TestReportConclusion:
    """结论生成测试"""

    def test_strong_factor_conclusion(self):
        """测试强因子结论"""
        ic_analysis = ICAnalysisResult(
            ic_series={date(2023, 1, 1): 0.05},
            ic_mean=0.05,
            ic_std=0.06,
            ir=0.83,
            ic_positive_rate=0.7,
            t_stat=4.0,
            p_value=0.001,
            ic_abs_mean=0.05,
        )

        report = FactorResearchReport(
            factor_name="StrongFactor",
            analysis_period=(date(2023, 1, 1), date(2023, 12, 31)),
            sample_size=5000,
            ic_analysis=ic_analysis,
            decay_analysis=None,
            quantile_analysis=None,
            yearly_analysis=None,
            monthly_analysis=None,
            factor_stats={},
        )

        html = report._generate_html()
        conclusion = report._generate_conclusion()

        assert "预测能力" in conclusion
        assert "稳定性良好" in conclusion
        assert "显著" in conclusion

    def test_weak_factor_conclusion(self):
        """测试弱因子结论"""
        ic_analysis = ICAnalysisResult(
            ic_series={date(2023, 1, 1): 0.01},
            ic_mean=0.01,
            ic_std=0.10,
            ir=0.10,
            ic_positive_rate=0.5,
            t_stat=0.5,
            p_value=0.6,
            ic_abs_mean=0.05,
        )

        report = FactorResearchReport(
            factor_name="WeakFactor",
            analysis_period=(date(2023, 1, 1), date(2023, 12, 31)),
            sample_size=5000,
            ic_analysis=ic_analysis,
            decay_analysis=None,
            quantile_analysis=None,
            yearly_analysis=None,
            monthly_analysis=None,
            factor_stats={},
        )

        conclusion = report._generate_conclusion()

        assert "弱" in conclusion or "较差" in conclusion
        assert "不显著" in conclusion or "谨慎" in conclusion
