#!/usr/bin/env python
"""
数据健康检查脚本

用于检查数据质量问题并生成HTML报告，包括：
- 缺失率检查（按symbol/日期维度）
- 异常跳变检测（日内涨跌幅>阈值）
- 停牌/涨跌停标记完整性
- 重复/乱序交易日识别

示例用法:
    python scripts/check_data_health.py \\
        --cache-dir data/cache \\
        --symbols 000001.SZ,600000.SH \\
        --start 2024-01-01 \\
        --end 2024-12-31 \\
        --output reports/data_health.html
"""

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


@dataclass
class HealthCheckResult:
    """单项健康检查结果"""

    check_name: str
    status: str  # "pass", "warning", "error"
    details: Dict[str, Any]
    issues: List[Dict[str, Any]]


class DataHealthChecker:
    """数据健康检查器"""

    def __init__(
        self,
        cache_dir: str,
        symbols: List[str],
        start_date: str,
        end_date: str,
        missing_rate_threshold: float = 0.1,
        jump_threshold: float = 0.30,
    ):
        """
        初始化健康检查器

        Args:
            cache_dir: 缓存目录路径
            symbols: 股票代码列表
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            missing_rate_threshold: 缺失率告警阈值 (0.1 = 10%)
            jump_threshold: 异常跳变阈值 (0.30 = 30%)
        """
        self.cache_dir = Path(cache_dir)
        self.symbols = symbols
        self.start_date = start_date
        self.end_date = end_date
        self.missing_rate_threshold = missing_rate_threshold
        self.jump_threshold = jump_threshold

        # 结果存储
        self.results: List[HealthCheckResult] = []
        self.summary: Dict[str, Any] = {}

    def load_data(self, symbol: str, freq: str = "1min") -> Optional[pd.DataFrame]:
        """
        加载单个股票的缓存数据

        Args:
            symbol: 股票代码
            freq: 数据频率

        Returns:
            DataFrame 或 None（如果文件不存在）
        """
        cache_file = (
            self.cache_dir
            / f"{symbol}_{self.start_date}_{self.end_date}_{freq}_qfq.parquet"
        )

        if not cache_file.exists():
            return None

        df = pd.read_parquet(cache_file)
        df["trade_time"] = pd.to_datetime(df["trade_time"])
        return df.sort_values("trade_time")

    def check_missing_rate(self) -> HealthCheckResult:
        """
        检查数据缺失率

        Returns:
            HealthCheckResult 包含缺失率统计
        """
        issues = []
        missing_stats = {}

        for symbol in self.symbols:
            df = self.load_data(symbol)
            if df is None:
                issues.append(
                    {
                        "symbol": symbol,
                        "issue": "缓存文件不存在",
                        "severity": "error",
                    }
                )
                missing_stats[symbol] = 1.0  # 100% missing
                continue

            # 计算预期bar数（简化版：假设每交易日240 bars）
            expected_days = (
                pd.to_datetime(self.end_date) - pd.to_datetime(self.start_date)
            ).days + 1
            expected_bars = expected_days * 240  # 1min频率

            actual_bars = len(df)
            missing_rate = max(0, 1 - actual_bars / expected_bars)
            missing_stats[symbol] = missing_rate

            if missing_rate > self.missing_rate_threshold:
                issues.append(
                    {
                        "symbol": symbol,
                        "issue": f"缺失率 {missing_rate:.2%} 超过阈值 {self.missing_rate_threshold:.2%}",
                        "severity": "warning" if missing_rate < 0.3 else "error",
                        "expected": expected_bars,
                        "actual": actual_bars,
                        "missing_rate": missing_rate,
                    }
                )

        status = "pass"
        if any(issue["severity"] == "error" for issue in issues):
            status = "error"
        elif any(issue["severity"] == "warning" for issue in issues):
            status = "warning"

        return HealthCheckResult(
            check_name="missing_rate",
            status=status,
            details={"missing_stats": missing_stats, "threshold": self.missing_rate_threshold},
            issues=issues,
        )

    def check_price_jumps(self) -> HealthCheckResult:
        """
        检查异常价格跳变（日内涨跌幅超过阈值）

        Returns:
            HealthCheckResult 包含跳变记录
        """
        issues = []
        jump_stats = {}

        for symbol in self.symbols:
            df = self.load_data(symbol)
            if df is None:
                continue

            # 计算bar间价格变化率
            df["price_change"] = df["close"].pct_change().abs()

            # 找出异常跳变
            jumps = df[df["price_change"] > self.jump_threshold]
            jump_count = len(jumps)
            jump_stats[symbol] = jump_count

            if jump_count > 0:
                for _, row in jumps.head(10).iterrows():  # 最多报告10条
                    issues.append(
                        {
                            "symbol": symbol,
                            "issue": f"价格跳变 {row['price_change']:.2%}",
                            "severity": "warning",
                            "trade_time": str(row["trade_time"]),
                            "close": float(row["close"]),
                            "change": float(row["price_change"]),
                        }
                    )

        status = "pass" if len(issues) == 0 else "warning"

        return HealthCheckResult(
            check_name="price_jumps",
            status=status,
            details={"jump_stats": jump_stats, "threshold": self.jump_threshold},
            issues=issues,
        )

    def check_halt_flags(self) -> HealthCheckResult:
        """
        检查停牌/涨跌停标记完整性

        Returns:
            HealthCheckResult 包含标记缺失情况
        """
        issues = []
        flag_stats = {}

        for symbol in self.symbols:
            df = self.load_data(symbol)
            if df is None:
                continue

            # 检查是否有 halt/limit_up/limit_down 字段
            missing_cols = []
            for col in ["halt", "limit_up", "limit_down"]:
                if col not in df.columns:
                    missing_cols.append(col)

            flag_stats[symbol] = {
                "has_halt": "halt" in df.columns,
                "has_limit_up": "limit_up" in df.columns,
                "has_limit_down": "limit_down" in df.columns,
            }

            if missing_cols:
                issues.append(
                    {
                        "symbol": symbol,
                        "issue": f"缺少字段: {', '.join(missing_cols)}",
                        "severity": "warning",
                        "missing_columns": missing_cols,
                    }
                )

        status = "pass" if len(issues) == 0 else "warning"

        return HealthCheckResult(
            check_name="halt_flags",
            status=status,
            details={"flag_stats": flag_stats},
            issues=issues,
        )

    def check_duplicates(self) -> HealthCheckResult:
        """
        检查重复交易时间

        Returns:
            HealthCheckResult 包含重复记录
        """
        issues = []
        dup_stats = {}

        for symbol in self.symbols:
            df = self.load_data(symbol)
            if df is None:
                continue

            duplicates = df[df.duplicated(subset=["trade_time"], keep=False)]
            dup_count = len(duplicates)
            dup_stats[symbol] = dup_count

            if dup_count > 0:
                dup_times = duplicates["trade_time"].unique()
                issues.append(
                    {
                        "symbol": symbol,
                        "issue": f"发现 {dup_count} 条重复记录",
                        "severity": "error",
                        "duplicate_count": dup_count,
                        "sample_times": [str(t) for t in dup_times[:5]],
                    }
                )

        status = "pass" if len(issues) == 0 else "error"

        return HealthCheckResult(
            check_name="duplicates",
            status=status,
            details={"dup_stats": dup_stats},
            issues=issues,
        )

    def check_order(self) -> HealthCheckResult:
        """
        检查交易时间顺序

        Returns:
            HealthCheckResult 包含乱序记录
        """
        issues = []
        order_stats = {}

        for symbol in self.symbols:
            df = self.load_data(symbol)
            if df is None:
                continue

            # 检查是否单调递增
            time_diffs = df["trade_time"].diff()
            out_of_order = time_diffs[time_diffs < pd.Timedelta(0)]
            disorder_count = len(out_of_order)
            order_stats[symbol] = disorder_count

            if disorder_count > 0:
                issues.append(
                    {
                        "symbol": symbol,
                        "issue": f"发现 {disorder_count} 处乱序",
                        "severity": "error",
                        "disorder_count": disorder_count,
                    }
                )

        status = "pass" if len(issues) == 0 else "error"

        return HealthCheckResult(
            check_name="order",
            status=status,
            details={"order_stats": order_stats},
            issues=issues,
        )

    def run_all_checks(self) -> List[HealthCheckResult]:
        """
        运行所有健康检查

        Returns:
            检查结果列表
        """
        print("开始数据健康检查...")
        print(f"- 缓存目录: {self.cache_dir}")
        print(f"- 股票数量: {len(self.symbols)}")
        print(f"- 日期范围: {self.start_date} ~ {self.end_date}")
        print()

        self.results = [
            self.check_missing_rate(),
            self.check_price_jumps(),
            self.check_halt_flags(),
            self.check_duplicates(),
            self.check_order(),
        ]

        # 生成总结
        total_issues = sum(len(r.issues) for r in self.results)
        error_count = sum(1 for r in self.results if r.status == "error")
        warning_count = sum(1 for r in self.results if r.status == "warning")
        pass_count = sum(1 for r in self.results if r.status == "pass")

        self.summary = {
            "total_checks": len(self.results),
            "pass_count": pass_count,
            "warning_count": warning_count,
            "error_count": error_count,
            "total_issues": total_issues,
            "check_time": datetime.now().isoformat(),
        }

        print(f"[OK] 检查完成: {pass_count} pass, {warning_count} warning, {error_count} error")
        print(f"   共发现 {total_issues} 个问题")
        print()

        return self.results

    def generate_html_report(self, output_path: str):
        """
        生成HTML格式的健康检查报告

        Args:
            output_path: 输出HTML文件路径
        """
        from jinja2 import Template

        html_template = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>数据健康检查报告</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; 
               background: #f5f7fa; color: #333; line-height: 1.6; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                  color: white; padding: 30px; border-radius: 10px; margin-bottom: 30px; }
        .header h1 { font-size: 28px; margin-bottom: 10px; }
        .header p { opacity: 0.9; font-size: 14px; }
        .summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); 
                   gap: 20px; margin-bottom: 30px; }
        .summary-card { background: white; padding: 20px; border-radius: 8px; 
                        box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
        .summary-card h3 { font-size: 14px; color: #666; margin-bottom: 10px; }
        .summary-card .value { font-size: 32px; font-weight: bold; }
        .summary-card.pass .value { color: #52c41a; }
        .summary-card.warning .value { color: #faad14; }
        .summary-card.error .value { color: #f5222d; }
        .check-section { background: white; padding: 25px; border-radius: 8px; 
                         box-shadow: 0 2px 8px rgba(0,0,0,0.1); margin-bottom: 20px; }
        .check-header { display: flex; justify-content: space-between; align-items: center; 
                        margin-bottom: 15px; padding-bottom: 15px; border-bottom: 2px solid #f0f0f0; }
        .check-header h2 { font-size: 20px; }
        .badge { padding: 4px 12px; border-radius: 4px; font-size: 12px; font-weight: bold; }
        .badge.pass { background: #f6ffed; color: #52c41a; border: 1px solid #b7eb8f; }
        .badge.warning { background: #fffbe6; color: #faad14; border: 1px solid #ffe58f; }
        .badge.error { background: #fff1f0; color: #f5222d; border: 1px solid #ffccc7; }
        .issue-list { list-style: none; }
        .issue-item { padding: 12px; margin-bottom: 8px; border-radius: 6px; 
                      border-left: 4px solid #ddd; background: #fafafa; }
        .issue-item.warning { border-left-color: #faad14; background: #fffbe6; }
        .issue-item.error { border-left-color: #f5222d; background: #fff1f0; }
        .issue-item strong { color: #000; }
        .no-issues { text-align: center; padding: 30px; color: #52c41a; font-size: 16px; }
        .footer { text-align: center; padding: 20px; color: #999; font-size: 12px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>[DATA] 数据健康检查报告</h1>
            <p>生成时间: {{ summary.check_time }} | 检查项: {{ summary.total_checks }}</p>
        </div>

        <div class="summary">
            <div class="summary-card pass">
                <h3>通过</h3>
                <div class="value">{{ summary.pass_count }}</div>
            </div>
            <div class="summary-card warning">
                <h3>警告</h3>
                <div class="value">{{ summary.warning_count }}</div>
            </div>
            <div class="summary-card error">
                <h3>错误</h3>
                <div class="value">{{ summary.error_count }}</div>
            </div>
            <div class="summary-card">
                <h3>总问题数</h3>
                <div class="value">{{ summary.total_issues }}</div>
            </div>
        </div>

        {% for result in results %}
        <div class="check-section">
            <div class="check-header">
                <h2>{{ result.check_name | title }}</h2>
                <span class="badge {{ result.status }}">{{ result.status | upper }}</span>
            </div>
            
            {% if result.issues %}
            <ul class="issue-list">
                {% for issue in result.issues %}
                <li class="issue-item {{ issue.severity }}">
                    <strong>{{ issue.symbol }}</strong>: {{ issue.issue }}
                </li>
                {% endfor %}
            </ul>
            {% else %}
            <div class="no-issues">✓ 未发现问题</div>
            {% endif %}
        </div>
        {% endfor %}

        <div class="footer">
            Aipha-Q Data Health Check Report | Generated by scripts/check_data_health.py
        </div>
    </div>
</body>
</html>
        """

        template = Template(html_template)
        html_content = template.render(
            summary=self.summary,
            results=[
                {
                    "check_name": r.check_name,
                    "status": r.status,
                    "issues": r.issues,
                }
                for r in self.results
            ],
        )

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(html_content, encoding="utf-8")

        print(f"[OK] HTML报告已生成: {output_path}")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="数据健康检查工具")
    parser.add_argument("--cache-dir", required=True, help="缓存目录路径")
    parser.add_argument(
        "--symbols", required=True, help="股票代码列表 (逗号分隔)"
    )
    parser.add_argument("--start", required=True, help="开始日期 (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="结束日期 (YYYY-MM-DD)")
    parser.add_argument(
        "--output",
        default="reports/data_health.html",
        help="输出HTML文件路径",
    )
    parser.add_argument(
        "--missing-threshold",
        type=float,
        default=0.1,
        help="缺失率告警阈值 (default: 0.1)",
    )
    parser.add_argument(
        "--jump-threshold",
        type=float,
        default=0.30,
        help="价格跳变告警阈值 (default: 0.30)",
    )

    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]

    checker = DataHealthChecker(
        cache_dir=args.cache_dir,
        symbols=symbols,
        start_date=args.start,
        end_date=args.end,
        missing_rate_threshold=args.missing_threshold,
        jump_threshold=args.jump_threshold,
    )

    checker.run_all_checks()
    checker.generate_html_report(args.output)


if __name__ == "__main__":
    main()
