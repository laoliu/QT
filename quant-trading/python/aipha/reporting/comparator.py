"""实验对比分析器

支持多次回测实验的对比，生成对比报告和叠加曲线数据。

使用示例::

    from aipha.reporting.comparator import ExperimentComparator

    comparator = ExperimentComparator(base_dir="results")
    report = comparator.compare(
        experiments=[
            ("triple_low", "20260210_120000_v3_nbo"),
            ("triple_low", "20260210_130000_v3_sbc"),
        ],
        metrics=["annual_return", "max_drawdown", "sharpe_ratio"],
    )
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ExperimentComparator:
    """实验对比分析器

    从归档目录加载多个实验，进行指标对比和曲线叠加分析。

    Args:
        base_dir: 归档根目录，默认 "results"
    """

    def __init__(self, base_dir: str = "results"):
        self.base_dir = Path(base_dir)

    def compare(
        self,
        experiments: list[tuple[str, str]],
        metrics: list[str] | None = None,
    ) -> dict:
        """对比多个实验

        Args:
            experiments: [(strategy_name, run_id), ...] 列表
            metrics: 要对比的指标名列表（默认全部核心指标）

        Returns:
            对比报告字典，包含 experiments_info, metrics_table,
            nav_overlay, yearly_comparison
        """
        if metrics is None:
            metrics = [
                "total_return",
                "annual_return",
                "max_drawdown",
                "sharpe_ratio",
                "calmar_ratio",
                "total_fees",
            ]

        experiments_info: list[dict] = []
        nav_data: list[dict] = []
        yearly_data: list[dict] = []

        for strategy, run_id in experiments:
            run_dir = self.base_dir / strategy / run_id
            if not run_dir.exists():
                logger.warning(f"实验不存在, 跳过: {strategy}/{run_id}")
                continue

            # 加载元信息
            meta_path = run_dir / "meta.json"
            if not meta_path.exists():
                logger.warning(f"元信息缺失, 跳过: {strategy}/{run_id}")
                continue

            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            exp_metrics = meta.get("metrics", {})

            # 实验信息
            info = {
                "strategy": strategy,
                "run_id": run_id,
                "tag": meta.get("tag", ""),
                "fill_model": meta.get("fill_model", ""),
                "start_date": meta.get("start_date", ""),
                "end_date": meta.get("end_date", ""),
                "initial_capital": meta.get("initial_capital", 0),
                "final_nav": meta.get("final_nav", 0),
                "total_trades": meta.get("total_trades", 0),
                "git_commit": meta.get("git_commit", ""),
                "archived_at": meta.get("archived_at", ""),
            }

            # 提取选定指标
            for m in metrics:
                info[m] = exp_metrics.get(m, None)

            experiments_info.append(info)

            # NAV 序列
            nav_path = run_dir / "nav_series.csv"
            if nav_path.exists():
                nav_series = self._load_nav_csv(nav_path)
                nav_data.append({
                    "label": f"{strategy}/{run_id}" if not meta.get("tag") else meta["tag"],
                    "series": nav_series,
                })

            # 分年度收益
            yearly_returns = exp_metrics.get("yearly_returns", {})
            yearly_data.append({
                "label": meta.get("tag", run_id),
                "yearly_returns": yearly_returns,
            })

        # 构建指标对比表
        metrics_table = self._build_metrics_table(experiments_info, metrics)

        # 构建 NAV 叠加数据（归一化到初始 1.0）
        nav_overlay = self._build_nav_overlay(nav_data)

        # 构建分年度对比
        yearly_comparison = self._build_yearly_comparison(yearly_data)

        # 计算优胜者
        best_by = self._determine_best(experiments_info, metrics)

        return {
            "generated_at": datetime.now().isoformat(),
            "num_experiments": len(experiments_info),
            "experiments": experiments_info,
            "metrics_table": metrics_table,
            "nav_overlay": nav_overlay,
            "yearly_comparison": yearly_comparison,
            "best_by": best_by,
        }

    def generate_text_report(self, comparison: dict) -> str:
        """生成文本格式的对比报告

        Args:
            comparison: compare() 的返回值

        Returns:
            格式化的文本报告
        """
        lines: list[str] = []
        lines.append("=" * 80)
        lines.append("实验对比报告")
        lines.append(f"生成时间: {comparison['generated_at']}")
        lines.append(f"对比实验数: {comparison['num_experiments']}")
        lines.append("=" * 80)

        # 实验概况
        lines.append("\n--- 实验概况 ---")
        for i, exp in enumerate(comparison["experiments"], 1):
            lines.append(
                f"\n[{i}] {exp.get('tag') or exp['run_id']} "
                f"({exp['strategy']})"
            )
            lines.append(f"    成交模型: {exp.get('fill_model', 'N/A')}")
            lines.append(f"    Git: {exp.get('git_commit', 'N/A')}")
            lines.append(f"    归档: {exp.get('archived_at', 'N/A')}")

        # 指标对比表
        lines.append("\n--- 核心指标对比 ---")
        table = comparison.get("metrics_table", {})
        if table:
            header = table.get("header", [])
            rows = table.get("rows", [])

            # 格式化列宽
            col_widths = [max(len(str(h)), 14) for h in header]
            for row in rows:
                for j, cell in enumerate(row):
                    col_widths[j] = max(col_widths[j], len(str(cell)))

            # 表头
            line = "  ".join(str(h).ljust(w) for h, w in zip(header, col_widths))
            lines.append(line)
            lines.append("-" * len(line))

            # 数据行
            for row in rows:
                line = "  ".join(
                    str(c).ljust(w) for c, w in zip(row, col_widths)
                )
                lines.append(line)

        # 优胜者
        best = comparison.get("best_by", {})
        if best:
            lines.append("\n--- 各指标优胜 ---")
            for metric, winner in best.items():
                lines.append(f"  {metric}: {winner}")

        # 分年度
        yearly = comparison.get("yearly_comparison", {})
        if yearly:
            lines.append("\n--- 分年度收益对比 ---")
            years = sorted(yearly.keys())
            labels = [
                exp.get("tag") or exp["run_id"]
                for exp in comparison["experiments"]
            ]
            header_line = "年份".ljust(8) + "  ".join(
                label[:16].ljust(16) for label in labels
            )
            lines.append(header_line)
            lines.append("-" * len(header_line))
            for year in years:
                vals = yearly[year]
                vals_str = "  ".join(
                    f"{v*100:+.2f}%".ljust(16) if v is not None else "N/A".ljust(16)
                    for v in vals
                )
                lines.append(f"{year}    {vals_str}")

        lines.append("\n" + "=" * 80)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _build_metrics_table(
        self,
        experiments: list[dict],
        metrics: list[str],
    ) -> dict:
        """构建指标对比表"""
        header = ["指标"] + [
            e.get("tag") or e["run_id"] for e in experiments
        ]
        rows: list[list] = []

        format_map = {
            "total_return": lambda v: f"{v * 100:.2f}%" if v is not None else "N/A",
            "annual_return": lambda v: f"{v * 100:.2f}%" if v is not None else "N/A",
            "max_drawdown": lambda v: f"{v * 100:.2f}%" if v is not None else "N/A",
            "sharpe_ratio": lambda v: f"{v:.4f}" if v is not None else "N/A",
            "calmar_ratio": lambda v: f"{v:.4f}" if v is not None else "N/A",
            "total_fees": lambda v: f"{v:,.2f}" if v is not None else "N/A",
        }

        metric_labels = {
            "total_return": "总收益率",
            "annual_return": "年化收益率",
            "max_drawdown": "最大回撤",
            "sharpe_ratio": "夏普比率",
            "calmar_ratio": "卡玛比率",
            "total_fees": "总手续费",
        }

        for m in metrics:
            label = metric_labels.get(m, m)
            fmt = format_map.get(m, lambda v: f"{v}" if v is not None else "N/A")
            row = [label] + [fmt(e.get(m)) for e in experiments]
            rows.append(row)

        return {"header": header, "rows": rows}

    def _build_nav_overlay(self, nav_data: list[dict]) -> dict:
        """构建 NAV 叠加数据（归一化到 1.0）"""
        overlay: dict[str, list[dict]] = {}

        for entry in nav_data:
            label = entry["label"]
            series = entry["series"]
            if not series:
                continue

            # 归一化
            first_nav = float(series[0]["nav"]) if series else 1.0
            normalized: list[dict] = []
            for point in series:
                nav_val = float(point["nav"])
                normalized.append({
                    "date": point["date"],
                    "nav_normalized": round(nav_val / first_nav, 6) if first_nav > 0 else 0,
                    "nav_raw": nav_val,
                })
            overlay[label] = normalized

        return overlay

    def _build_yearly_comparison(self, yearly_data: list[dict]) -> dict:
        """构建分年度对比"""
        # 收集所有年份
        all_years: set[str] = set()
        for entry in yearly_data:
            all_years.update(entry.get("yearly_returns", {}).keys())

        result: dict[str, list] = {}
        for year in sorted(all_years):
            vals: list[float | None] = []
            for entry in yearly_data:
                yr = entry.get("yearly_returns", {})
                vals.append(yr.get(year))
            result[year] = vals

        return result

    def _determine_best(
        self,
        experiments: list[dict],
        metrics: list[str],
    ) -> dict[str, str]:
        """判断各指标的优胜者"""
        # 指标方向：True = 越大越好，False = 越小越好
        higher_better = {
            "total_return": True,
            "annual_return": True,
            "sharpe_ratio": True,
            "calmar_ratio": True,
        }
        lower_better = {
            "max_drawdown": True,
            "total_fees": True,
        }

        best: dict[str, str] = {}
        for m in metrics:
            vals = [(e.get("tag") or e["run_id"], e.get(m)) for e in experiments]
            vals = [(label, v) for label, v in vals if v is not None]
            if not vals:
                continue

            if m in higher_better:
                winner = max(vals, key=lambda x: x[1])
            elif m in lower_better:
                winner = min(vals, key=lambda x: x[1])
            else:
                winner = max(vals, key=lambda x: x[1])

            best[m] = winner[0]

        return best

    @staticmethod
    def _load_nav_csv(path: Path) -> list[dict]:
        """加载 NAV CSV"""
        rows: list[dict] = []
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(dict(row))
        return rows
