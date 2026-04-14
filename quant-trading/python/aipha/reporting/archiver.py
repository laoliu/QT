"""实验归档系统

提供回测结果的自动归档、版本管理、对比分析功能。

归档目录结构::

    results/
    ├── registry.json          # 全局实验索引
    └── {strategy_name}/
        └── {timestamp}_{tag}/
            ├── meta.json      # 实验元信息（指纹、配置摘要、指标）
            ├── config.yml     # 完整回测配置
            ├── nav_series.csv # NAV 时间序列
            ├── trades.csv     # 交易记录
            └── summary.json   # 汇总报告

使用示例::

    from aipha.reporting.archiver import ExperimentArchiver

    archiver = ExperimentArchiver(base_dir="results")
    run_id = archiver.archive(result, config_dict, tag="v3_same_bar_close")
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


def _json_serial(obj: Any) -> Any:
    """JSON 序列化辅助：处理 date/datetime 等类型"""
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    raise TypeError(f"Type {type(obj)} not serializable")


def _get_git_commit() -> str:
    """获取当前 Git commit hash"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _hash_config(config: dict) -> str:
    """计算配置字典的 hash"""
    raw = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


class ExperimentArchiver:
    """实验归档器

    将 BacktestResult 及关联配置持久化到文件系统，
    并维护全局实验注册表 (registry.json)。

    Args:
        base_dir: 归档根目录，默认 "results"
    """

    REGISTRY_FILE = "registry.json"

    def __init__(self, base_dir: str = "results"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def archive(
        self,
        result: Any,  # BacktestResult
        config: dict,
        tag: str = "",
        notes: str = "",
    ) -> str:
        """归档一次回测结果

        Args:
            result: BacktestResult 实例
            config: 完整配置字典
            tag: 实验标签（如 "v3_same_bar_close"）
            notes: 备注信息

        Returns:
            run_id: 归档目录名（唯一标识）
        """
        # 生成 run_id
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        tag_part = f"_{tag}" if tag else ""
        run_id = f"{timestamp}{tag_part}"

        strategy_name = getattr(result, "strategy_name", config.get("name", "unknown"))
        run_dir = self.base_dir / strategy_name / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        # 1. 保存元信息
        meta = self._build_meta(result, config, tag, notes, run_id, strategy_name)
        self._save_json(run_dir / "meta.json", meta)

        # 2. 保存完整配置
        self._save_yaml(run_dir / "config.yml", config)

        # 3. 保存 NAV 时间序列
        self._save_nav_series(run_dir / "nav_series.csv", result)

        # 4. 保存交易记录
        self._save_trades(run_dir / "trades.csv", result)

        # 5. 保存汇总报告
        summary = self._build_summary(result, meta)
        self._save_json(run_dir / "summary.json", summary)

        # 6. 更新全局注册表
        self._update_registry(meta)

        logger.info(f"实验归档完成: {run_dir}")
        return run_id

    def list_experiments(
        self,
        strategy: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """列出已归档的实验

        Args:
            strategy: 策略筛选（可选）
            limit: 最大返回数

        Returns:
            实验列表，按时间倒序
        """
        registry = self._load_registry()
        experiments = registry.get("experiments", [])

        if strategy:
            experiments = [
                e for e in experiments
                if e.get("strategy_name") == strategy
            ]

        # 按时间倒序
        experiments.sort(key=lambda x: x.get("archived_at", ""), reverse=True)
        return experiments[:limit]

    def load_experiment(self, strategy: str, run_id: str) -> dict:
        """加载某次实验的完整数据

        Args:
            strategy: 策略名称
            run_id: 归档目录名

        Returns:
            包含 meta, config, nav_series, summary 的字典
        """
        run_dir = self.base_dir / strategy / run_id
        if not run_dir.exists():
            raise FileNotFoundError(f"实验不存在: {run_dir}")

        data: dict[str, Any] = {}

        meta_path = run_dir / "meta.json"
        if meta_path.exists():
            data["meta"] = json.loads(meta_path.read_text(encoding="utf-8"))

        config_path = run_dir / "config.yml"
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                data["config"] = yaml.safe_load(f)

        nav_path = run_dir / "nav_series.csv"
        if nav_path.exists():
            data["nav_series"] = self._load_csv(nav_path)

        summary_path = run_dir / "summary.json"
        if summary_path.exists():
            data["summary"] = json.loads(summary_path.read_text(encoding="utf-8"))

        return data

    def get_nav_series(self, strategy: str, run_id: str) -> list[dict]:
        """获取某次实验的 NAV 时间序列

        Returns:
           [{"date": "2022-01-04", "nav": 500000.0, "daily_return": 0.0}, ...]
        """
        run_dir = self.base_dir / strategy / run_id
        nav_path = run_dir / "nav_series.csv"
        if not nav_path.exists():
            return []
        return self._load_csv(nav_path)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _build_meta(
        self,
        result: Any,
        config: dict,
        tag: str,
        notes: str,
        run_id: str,
        strategy_name: str,
    ) -> dict:
        """构建实验元信息"""
        # 计算核心指标
        metrics = self._extract_metrics(result)

        return {
            "run_id": run_id,
            "strategy_name": strategy_name,
            "tag": tag,
            "notes": notes,
            "archived_at": datetime.now().isoformat(),
            "git_commit": _get_git_commit(),
            "config_hash": _hash_config(config),
            "start_date": str(result.start_date),
            "end_date": str(result.end_date),
            "initial_capital": result.initial_capital,
            "final_nav": result.final_nav,
            "total_trades": result.total_trades,
            "trading_days": len(result.daily_snapshots),
            "metrics": metrics,
            "fill_model": result.config.get("fill_model_name", "unknown"),
        }

    def _build_summary(self, result: Any, meta: dict) -> dict:
        """构建汇总报告"""
        return {
            "run_id": meta["run_id"],
            "strategy_name": meta["strategy_name"],
            "tag": meta["tag"],
            "archived_at": meta["archived_at"],
            "period": f"{meta['start_date']} ~ {meta['end_date']}",
            "initial_capital": meta["initial_capital"],
            "final_nav": meta["final_nav"],
            "metrics": meta["metrics"],
            "total_trades": meta["total_trades"],
            "trading_days": meta["trading_days"],
            "fill_model": meta["fill_model"],
            "git_commit": meta["git_commit"],
            "config_hash": meta["config_hash"],
        }

    def _extract_metrics(self, result: Any) -> dict:
        """从 BacktestResult 提取核心指标"""
        snapshots = result.daily_snapshots
        if not snapshots:
            return {}

        initial = result.initial_capital
        final = result.final_nav
        total_return = (final - initial) / initial if initial > 0 else 0

        n_days = len(snapshots)
        n_years = n_days / 252.0

        annual_return = (
            (1 + total_return) ** (1 / n_years) - 1
            if n_years > 0 and total_return > -1
            else total_return
        )

        # 最大回撤
        peak = initial
        max_dd = 0.0
        for snap in snapshots:
            if snap.nav > peak:
                peak = snap.nav
            dd = (peak - snap.nav) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        # 夏普比率 — §13.2: ddof=1 (sample std)，与 metrics.compute_sharpe_ratio 一致
        returns = [s.daily_return for s in snapshots]
        if len(returns) > 2:
            avg_ret = sum(returns) / len(returns)
            std_ret = (sum((r - avg_ret) ** 2 for r in returns) / (len(returns) - 1)) ** 0.5
            sharpe = (
                (avg_ret * 252 - 0.02) / (std_ret * 252**0.5)
                if std_ret > 0
                else 0
            )
        else:
            sharpe = 0

        calmar = annual_return / max_dd if max_dd > 0 else 0

        # 分年度
        yearly_returns = self._calc_yearly_returns(snapshots, initial)

        return {
            "total_return": round(total_return, 6),
            "annual_return": round(annual_return, 6),
            "max_drawdown": round(max_dd, 6),
            "sharpe_ratio": round(sharpe, 4),
            "calmar_ratio": round(calmar, 4),
            "total_fees": round(result.total_fees.total, 2),
            "yearly_returns": yearly_returns,
        }

    def _calc_yearly_returns(
        self, snapshots: list, initial: float
    ) -> dict[str, float]:
        """计算分年度收益率"""
        yearly: dict[str, list] = {}
        for snap in snapshots:
            year = str(snap.trade_date.year)
            if year not in yearly:
                yearly[year] = []
            yearly[year].append(snap)

        result: dict[str, float] = {}
        prev_nav = initial
        for year in sorted(yearly.keys()):
            year_end_nav = yearly[year][-1].nav
            year_ret = (
                (year_end_nav - prev_nav) / prev_nav if prev_nav > 0 else 0
            )
            result[year] = round(year_ret, 6)
            prev_nav = year_end_nav

        return result

    def _save_nav_series(self, path: Path, result: Any) -> None:
        """保存 NAV 时间序列到 CSV"""
        snapshots = result.daily_snapshots
        if not snapshots:
            return

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "date", "nav", "cash", "daily_return", "cumulative_return",
            ])
            for snap in snapshots:
                writer.writerow([
                    snap.trade_date.isoformat(),
                    round(snap.nav, 2),
                    round(snap.cash, 2),
                    round(snap.daily_return, 8),
                    round(snap.cumulative_return, 8),
                ])

    def _save_trades(self, path: Path, result: Any) -> None:
        """保存交易记录到 CSV"""
        trades = result.trades
        if not trades:
            return

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "trade_id", "order_id", "date", "time",
                "symbol", "side", "qty", "price", "notional",
                "commission", "stamp_tax", "transfer_fee", "total_fee",
            ])
            for t in trades:
                writer.writerow([
                    t.trade_id,
                    t.order_id,
                    t.trade_date.isoformat(),
                    t.trade_time.isoformat(),
                    t.symbol,
                    t.side.value if hasattr(t.side, "value") else str(t.side),
                    t.qty,
                    round(t.price, 4),
                    round(t.notional, 2),
                    round(t.fees.commission, 4),
                    round(t.fees.stamp_tax, 4),
                    round(t.fees.transfer_fee, 4),
                    round(t.fees.total, 4),
                ])

    def _update_registry(self, meta: dict) -> None:
        """更新全局注册表"""
        registry = self._load_registry()
        experiments = registry.setdefault("experiments", [])

        # 添加精简版元信息
        entry = {
            "run_id": meta["run_id"],
            "strategy_name": meta["strategy_name"],
            "tag": meta["tag"],
            "archived_at": meta["archived_at"],
            "git_commit": meta["git_commit"],
            "start_date": meta["start_date"],
            "end_date": meta["end_date"],
            "metrics": {
                "annual_return": meta["metrics"].get("annual_return", 0),
                "max_drawdown": meta["metrics"].get("max_drawdown", 0),
                "sharpe_ratio": meta["metrics"].get("sharpe_ratio", 0),
                "calmar_ratio": meta["metrics"].get("calmar_ratio", 0),
            },
        }
        experiments.append(entry)
        registry["last_updated"] = datetime.now().isoformat()
        registry["total_experiments"] = len(experiments)

        self._save_json(self.base_dir / self.REGISTRY_FILE, registry)

    def _load_registry(self) -> dict:
        """加载注册表"""
        path = self.base_dir / self.REGISTRY_FILE
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {"experiments": [], "total_experiments": 0}

    @staticmethod
    def _save_json(path: Path, data: dict) -> None:
        """保存 JSON"""
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=_json_serial),
            encoding="utf-8",
        )

    @staticmethod
    def _save_yaml(path: Path, data: dict) -> None:
        """保存 YAML"""
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    @staticmethod
    def _load_csv(path: Path) -> list[dict]:
        """加载 CSV 为字典列表"""
        rows: list[dict] = []
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(dict(row))
        return rows
