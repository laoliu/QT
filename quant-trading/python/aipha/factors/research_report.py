"""
因子研究报告模块

整合因子分析能力，生成完整的因子研究报告：
- IC/IR分析
- 因子衰减（IC Decay）
- 分组收益
- 周期分析（年度/月度）
- HTML/文本报告输出

使用示例：
    reporter = FactorResearchReporter(factor_data, return_data)
    report = reporter.generate_report("Momentum")
    report.to_html("momentum_research.html")
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

from aipha.factors.analysis import (
    FactorAnalyzer,
    FactorDecayResult,
    ICAnalysisResult,
    QuantileReturnResult,
)


@dataclass
class PeriodAnalysis:
    """周期分析结果"""

    period_type: Literal["yearly", "monthly"]
    periods: list[str]
    ic_by_period: dict[str, float]
    return_by_period: dict[str, float]


@dataclass
class FactorResearchReport:
    """因子研究报告"""

    factor_name: str
    analysis_period: tuple[date, date]
    sample_size: int

    # IC分析
    ic_analysis: ICAnalysisResult

    # IC衰减
    decay_analysis: FactorDecayResult | None

    # 分组收益
    quantile_analysis: QuantileReturnResult | None

    # 周期分析
    yearly_analysis: PeriodAnalysis | None
    monthly_analysis: PeriodAnalysis | None

    # 因子统计
    factor_stats: dict[str, float]

    def summary(self) -> str:
        """生成文本摘要"""
        lines = [
            "=" * 70,
            f"因子研究报告: {self.factor_name}",
            "=" * 70,
            "",
            f"分析期间: {self.analysis_period[0]} ~ {self.analysis_period[1]}",
            f"样本数量: {self.sample_size:,}",
            "",
            "【IC分析】",
            f"  IC均值: {self.ic_analysis.ic_mean:.4f}",
            f"  IC标准差: {self.ic_analysis.ic_std:.4f}",
            f"  IR (信息比率): {self.ic_analysis.ir:.4f}",
            f"  IC > 0 比例: {self.ic_analysis.ic_positive_rate:.2%}",
            f"  t统计量: {self.ic_analysis.t_stat:.4f}",
            f"  p值: {self.ic_analysis.p_value:.4f}",
            "",
        ]

        # IC衰减
        if self.decay_analysis:
            lines.extend([
                "【IC衰减分析】",
            ])
            for period, ic in self.decay_analysis.ic_by_period.items():
                ir = self.decay_analysis.ir_by_period.get(period, 0)
                lines.append(f"  {period}期: IC={ic:.4f}, IR={ir:.4f}")
            lines.append("")

        # 分组收益
        if self.quantile_analysis:
            lines.extend([
                "【分组收益】",
                f"  分组数: {self.quantile_analysis.num_quantiles}",
            ])
            for q, ret in self.quantile_analysis.quantile_returns.items():
                lines.append(f"  Q{q}: {ret:.2%}")
            lines.extend([
                f"  多空收益: {self.quantile_analysis.long_short_return:.2%}",
                f"  单调性: {self.quantile_analysis.monotonicity:.4f}",
                "",
            ])

        # 因子统计
        if self.factor_stats:
            lines.extend([
                "【因子统计】",
            ])
            for key, value in self.factor_stats.items():
                if isinstance(value, float):
                    lines.append(f"  {key}: {value:.4f}")
                else:
                    lines.append(f"  {key}: {value}")
            lines.append("")

        # 年度IC
        if self.yearly_analysis:
            lines.extend([
                "【年度IC】",
            ])
            for year, ic in sorted(self.yearly_analysis.ic_by_period.items()):
                lines.append(f"  {year}: IC={ic:.4f}")
            lines.append("")

        lines.extend(["=" * 70])
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        result = {
            "factor_name": self.factor_name,
            "analysis_period": {
                "start": str(self.analysis_period[0]),
                "end": str(self.analysis_period[1]),
            },
            "sample_size": self.sample_size,
            "ic_analysis": {
                "ic_mean": self.ic_analysis.ic_mean,
                "ic_std": self.ic_analysis.ic_std,
                "ir": self.ic_analysis.ir,
                "ic_positive_rate": self.ic_analysis.ic_positive_rate,
                "t_stat": self.ic_analysis.t_stat,
                "p_value": self.ic_analysis.p_value,
            },
            "factor_stats": self.factor_stats,
        }

        if self.decay_analysis:
            result["decay_analysis"] = {
                "ic_by_period": self.decay_analysis.ic_by_period,
                "ir_by_period": self.decay_analysis.ir_by_period,
            }

        if self.quantile_analysis:
            result["quantile_analysis"] = {
                "n_quantiles": self.quantile_analysis.num_quantiles,
                "quantile_returns": self.quantile_analysis.quantile_returns,
                "long_short_return": self.quantile_analysis.long_short_return,
                "monotonicity": self.quantile_analysis.monotonicity,
            }

        if self.yearly_analysis:
            result["yearly_analysis"] = {
                "ic_by_period": self.yearly_analysis.ic_by_period,
                "return_by_period": self.yearly_analysis.return_by_period,
            }

        return result

    def to_html(self, output_path: str | Path) -> None:
        """生成HTML报告"""
        html_content = self._generate_html()
        Path(output_path).write_text(html_content, encoding="utf-8")

    def _generate_html(self) -> str:
        """生成HTML内容"""
        ic = self.ic_analysis

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>因子研究报告 - {self.factor_name}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }}
        .container {{ background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        h1 {{ color: #333; border-bottom: 3px solid #2196F3; padding-bottom: 10px; }}
        h2 {{ color: #555; margin-top: 30px; }}
        .summary-box {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin: 20px 0; }}
        .metric-card {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 8px; text-align: center; }}
        .metric-card.positive {{ background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%); }}
        .metric-card.negative {{ background: linear-gradient(135deg, #eb3349 0%, #f45c43 100%); }}
        .metric-value {{ font-size: 2em; font-weight: bold; }}
        .metric-label {{ font-size: 0.9em; opacity: 0.9; margin-top: 5px; }}
        table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background: #f8f9fa; color: #333; }}
        tr:hover {{ background: #f5f5f5; }}
        .positive {{ color: #28a745; }}
        .negative {{ color: #dc3545; }}
        .section {{ margin: 30px 0; }}
        .bar {{ height: 20px; background: #e0e0e0; border-radius: 4px; overflow: hidden; }}
        .bar-fill {{ height: 100%; background: #2196F3; transition: width 0.3s; }}
        .footer {{ text-align: center; color: #888; margin-top: 40px; padding-top: 20px; border-top: 1px solid #eee; }}
    </style>
</head>
<body>
<div class="container">
    <h1>因子研究报告: {self.factor_name}</h1>

    <p>分析期间: {self.analysis_period[0]} ~ {self.analysis_period[1]} | 样本量: {self.sample_size:,}</p>

    <div class="summary-box">
        <div class="metric-card {'positive' if ic.ic_mean > 0 else 'negative'}">
            <div class="metric-value">{ic.ic_mean:.4f}</div>
            <div class="metric-label">IC均值</div>
        </div>
        <div class="metric-card">
            <div class="metric-value">{ic.ir:.2f}</div>
            <div class="metric-label">IR (信息比率)</div>
        </div>
        <div class="metric-card {'positive' if ic.ic_positive_rate > 0.5 else 'negative'}">
            <div class="metric-value">{ic.ic_positive_rate:.1%}</div>
            <div class="metric-label">IC正向比例</div>
        </div>
        <div class="metric-card">
            <div class="metric-value">{ic.t_stat:.2f}</div>
            <div class="metric-label">t统计量</div>
        </div>
    </div>

    <div class="section">
        <h2>IC分析详情</h2>
        <table>
            <tr><th>指标</th><th>值</th><th>说明</th></tr>
            <tr><td>IC均值</td><td>{ic.ic_mean:.4f}</td><td>因子与未来收益的平均相关性</td></tr>
            <tr><td>IC标准差</td><td>{ic.ic_std:.4f}</td><td>IC的波动程度</td></tr>
            <tr><td>IR</td><td>{ic.ir:.4f}</td><td>IC均值/IC标准差，衡量因子稳定性</td></tr>
            <tr><td>IC绝对值均值</td><td>{ic.ic_abs_mean:.4f}</td><td>不考虑方向的因子强度</td></tr>
            <tr><td>IC正向比例</td><td>{ic.ic_positive_rate:.2%}</td><td>IC > 0 的时期占比</td></tr>
            <tr><td>t统计量</td><td>{ic.t_stat:.4f}</td><td>IC显著性检验</td></tr>
            <tr><td>p值</td><td>{ic.p_value:.4f}</td><td>{'显著' if ic.p_value < 0.05 else '不显著'} (α=0.05)</td></tr>
        </table>
    </div>
"""

        # IC衰减分析
        if self.decay_analysis:
            html += """
    <div class="section">
        <h2>IC衰减分析</h2>
        <table>
            <tr><th>持有期</th><th>IC</th><th>IR</th></tr>
"""
            for period in sorted(self.decay_analysis.ic_by_period.keys()):
                ic_val = self.decay_analysis.ic_by_period[period]
                ir_val = self.decay_analysis.ir_by_period.get(period, 0)
                html += f"            <tr><td>{period}期</td><td>{ic_val:.4f}</td><td>{ir_val:.4f}</td></tr>\n"
            html += "        </table>\n    </div>\n"

        # 分组收益
        if self.quantile_analysis:
            qa = self.quantile_analysis
            html += """
    <div class="section">
        <h2>分组收益分析</h2>
        <table>
            <tr><th>分组</th><th>收益</th><th>相对基准</th></tr>
"""
            avg_ret = sum(qa.quantile_returns.values()) / len(qa.quantile_returns)
            for q in sorted(qa.quantile_returns.keys()):
                ret = qa.quantile_returns[q]
                rel = "正" if ret > avg_ret else "负" if ret < avg_ret else "中"
                css_class = "positive" if ret > avg_ret else "negative" if ret < avg_ret else ""
                html += f'            <tr><td>Q{q}</td><td class="{css_class}">{ret:.2%}</td><td>{rel}</td></tr>\n'

            html += f"""        </table>
        <p><strong>多空收益:</strong> <span class="{'positive' if qa.long_short_return > 0 else 'negative'}">{qa.long_short_return:.2%}</span></p>
        <p><strong>单调性:</strong> {qa.monotonicity:.4f} (1.0表示完美单调递增)</p>
    </div>
"""

        # 年度IC
        if self.yearly_analysis:
            html += """
    <div class="section">
        <h2>年度IC分析</h2>
        <table>
            <tr><th>年度</th><th>IC</th><th>表现</th></tr>
"""
            for year, ic_val in sorted(self.yearly_analysis.ic_by_period.items()):
                css_class = "positive" if ic_val > 0 else "negative"
                html += f'            <tr><td>{year}</td><td class="{css_class}">{ic_val:.4f}</td><td>{"有效" if abs(ic_val) > 0.02 else "弱"}</td></tr>\n'
            html += "        </table>\n    </div>\n"

        # 因子统计
        if self.factor_stats:
            html += """
    <div class="section">
        <h2>因子分布统计</h2>
        <table>
            <tr><th>统计量</th><th>值</th></tr>
"""
            for key, value in self.factor_stats.items():
                if isinstance(value, float):
                    html += f"            <tr><td>{key}</td><td>{value:.4f}</td></tr>\n"
                else:
                    html += f"            <tr><td>{key}</td><td>{value}</td></tr>\n"
            html += "        </table>\n    </div>\n"

        # 结论与建议
        conclusion = self._generate_conclusion()
        html += f"""
    <div class="section">
        <h2>结论与建议</h2>
        <p>{conclusion}</p>
    </div>

    <div class="footer">
        <p>Aipha-Q 因子研究系统 | 生成时间: {date.today()}</p>
    </div>
</div>
</body>
</html>
"""
        return html

    def _generate_conclusion(self) -> str:
        """生成结论"""
        ic = self.ic_analysis
        conclusions = []

        # IC有效性
        if abs(ic.ic_mean) > 0.03:
            conclusions.append(f"该因子IC均值为{ic.ic_mean:.4f}，具有{'正向' if ic.ic_mean > 0 else '负向'}预测能力。")
        else:
            conclusions.append(f"该因子IC均值为{ic.ic_mean:.4f}，预测能力较弱。")

        # 稳定性
        if ic.ir > 0.5:
            conclusions.append(f"IR={ic.ir:.2f}，因子稳定性良好。")
        elif ic.ir > 0.3:
            conclusions.append(f"IR={ic.ir:.2f}，因子稳定性一般。")
        else:
            conclusions.append(f"IR={ic.ir:.2f}，因子稳定性较差。")

        # 显著性
        if ic.p_value < 0.01:
            conclusions.append("统计检验高度显著（p<0.01）。")
        elif ic.p_value < 0.05:
            conclusions.append("统计检验显著（p<0.05）。")
        else:
            conclusions.append("统计检验不显著，需谨慎使用。")

        # 分组收益
        if self.quantile_analysis:
            qa = self.quantile_analysis
            if qa.long_short_return > 0.05:
                conclusions.append(f"多空组合年化收益{qa.long_short_return:.2%}，具有实际交易价值。")
            if qa.monotonicity > 0.8:
                conclusions.append("分组收益单调性良好，因子区分度高。")

        return " ".join(conclusions)


class FactorResearchReporter:
    """因子研究报告生成器"""

    def __init__(
        self,
        factor_values: dict[date, dict[str, float]],
        forward_returns: dict[date, dict[str, float]],
        method: Literal["spearman", "pearson"] = "spearman",
    ):
        """初始化报告生成器

        Args:
            factor_values: 因子值 {date: {symbol: value}}
            forward_returns: 未来收益 {date: {symbol: return}}
            method: 相关性计算方法
        """
        self.factor_values = factor_values
        self.forward_returns = forward_returns
        self.method = method
        self.analyzer = FactorAnalyzer(factor_values, forward_returns)

    def generate_report(
        self,
        factor_name: str,
        include_decay: bool = True,
        include_quantile: bool = True,
        include_yearly: bool = True,
        decay_periods: list[int] | None = None,
        n_quantiles: int = 5,
    ) -> FactorResearchReport:
        """生成因子研究报告

        Args:
            factor_name: 因子名称
            include_decay: 是否包含IC衰减分析
            include_quantile: 是否包含分组收益分析
            include_yearly: 是否包含年度分析
            decay_periods: IC衰减分析的持有期列表
            n_quantiles: 分组数量

        Returns:
            因子研究报告
        """
        # IC分析
        ic_analysis = self.analyzer.compute_ic(method=self.method)

        # IC衰减
        decay_analysis = None
        if include_decay:
            periods = decay_periods or [1, 5, 10, 20]
            decay_analysis = self.analyzer.compute_decay(periods, method=self.method)

        # 分组收益
        quantile_analysis = None
        if include_quantile:
            quantile_analysis = self.analyzer.compute_quantile_returns(n_quantiles)

        # 年度分析
        yearly_analysis = None
        if include_yearly and len(self.factor_values) > 0:
            yearly_analysis = self._compute_yearly_analysis()

        # 因子统计
        factor_stats = self._compute_factor_stats()

        # 分析期间
        dates = sorted(self.factor_values.keys())
        analysis_period = (dates[0], dates[-1]) if dates else (date.today(), date.today())

        # 样本量
        sample_size = sum(len(v) for v in self.factor_values.values())

        return FactorResearchReport(
            factor_name=factor_name,
            analysis_period=analysis_period,
            sample_size=sample_size,
            ic_analysis=ic_analysis,
            decay_analysis=decay_analysis,
            quantile_analysis=quantile_analysis,
            yearly_analysis=yearly_analysis,
            monthly_analysis=None,  # 可扩展
            factor_stats=factor_stats,
        )

    def _compute_yearly_analysis(self) -> PeriodAnalysis | None:
        """计算年度分析"""
        dates = sorted(self.factor_values.keys())
        if not dates:
            return None

        # 按年分组
        by_year: dict[str, list[date]] = {}
        for d in dates:
            year = str(d.year)
            if year not in by_year:
                by_year[year] = []
            by_year[year].append(d)

        ic_by_period = {}
        return_by_period = {}

        for year, year_dates in by_year.items():
            # 该年度的因子和收益
            factor_subset = {d: self.factor_values[d] for d in year_dates if d in self.factor_values}
            return_subset = {d: self.forward_returns[d] for d in year_dates if d in self.forward_returns}

            if factor_subset and return_subset:
                try:
                    yearly_analyzer = FactorAnalyzer(factor_subset, return_subset)
                    yearly_ic = yearly_analyzer.compute_ic(method=self.method)
                    ic_by_period[year] = yearly_ic.ic_mean
                except Exception:
                    ic_by_period[year] = 0.0

            # 平均收益
            if return_subset:
                all_rets = []
                for day_rets in return_subset.values():
                    all_rets.extend(day_rets.values())
                return_by_period[year] = float(np.mean(all_rets)) if all_rets else 0.0

        return PeriodAnalysis(
            period_type="yearly",
            periods=list(by_year.keys()),
            ic_by_period=ic_by_period,
            return_by_period=return_by_period,
        )

    def _compute_factor_stats(self) -> dict[str, float]:
        """计算因子分布统计"""
        all_values = []
        for day_values in self.factor_values.values():
            all_values.extend(day_values.values())

        if not all_values:
            return {}

        arr = np.array(all_values)

        return {
            "均值": float(np.mean(arr)),
            "标准差": float(np.std(arr)),
            "最小值": float(np.min(arr)),
            "25%分位": float(np.percentile(arr, 25)),
            "中位数": float(np.median(arr)),
            "75%分位": float(np.percentile(arr, 75)),
            "最大值": float(np.max(arr)),
            "偏度": float(self._skewness(arr)),
            "峰度": float(self._kurtosis(arr)),
        }

    def _skewness(self, arr: NDArray) -> float:
        """计算偏度"""
        mean = np.mean(arr)
        std = np.std(arr)
        if std == 0:
            return 0.0
        return float(np.mean(((arr - mean) / std) ** 3))

    def _kurtosis(self, arr: NDArray) -> float:
        """计算峰度"""
        mean = np.mean(arr)
        std = np.std(arr)
        if std == 0:
            return 0.0
        return float(np.mean(((arr - mean) / std) ** 4) - 3)


def generate_factor_report(
    factor_values: dict[date, dict[str, float]],
    forward_returns: dict[date, dict[str, float]],
    factor_name: str,
    output_path: str | Path | None = None,
) -> FactorResearchReport:
    """便捷函数：生成因子研究报告

    Args:
        factor_values: 因子值
        forward_returns: 未来收益
        factor_name: 因子名称
        output_path: HTML输出路径（可选）

    Returns:
        因子研究报告
    """
    reporter = FactorResearchReporter(factor_values, forward_returns)
    report = reporter.generate_report(factor_name)

    if output_path:
        report.to_html(output_path)

    return report
