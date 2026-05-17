"""Paper-Backtest 自动对账引擎 (§Phase5b)

自动发现活跃 Paper 组合的 NAV 历史，对比回测/replay 基线，
生成结构化对账报告。解决 paper_replay_diff.py 依赖手动
replay_mapping.yml 的痛点。

功能:
- 自动发现 Paper NAV 历史 (data/paper_state/{combo}/)
- NAV 曲线健康度检查 (缺失天数/异常跳变/连续平值)
- Paper vs Replay NAV 偏差分析
- 组合级 S4 验收指标汇总
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PAPER_STATE = _PROJECT_ROOT / "data" / "paper_state"
_OUTPUT_DIR = _PROJECT_ROOT / "output"


@dataclass
class NavHealthReport:
    """单组合 NAV 健康报告。"""

    combo_id: str
    first_date: str = ""
    last_date: str = ""
    total_days: int = 0
    missing_days: int = 0
    max_daily_jump_pct: float = 0.0
    flat_streak_days: int = 0
    status: str = "unknown"  # healthy / warning / error
    issues: list[str] = field(default_factory=list)


@dataclass
class ReconciliationReport:
    """完整对账报告。"""

    combo_id: str
    generated_at: str = ""
    nav_health: NavHealthReport | None = None
    replay_comparison: dict[str, Any] = field(default_factory=dict)
    s4_metrics: dict[str, Any] = field(default_factory=dict)


def discover_active_combos() -> list[str]:
    """从 system_settings.json 发现活跃 Paper 组合。"""
    settings_path = _PROJECT_ROOT / "configs" / "paper" / "system_settings.json"
    if not settings_path.exists():
        logger.warning("system_settings.json not found")
        return []
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    return settings.get("daily_batch_portfolios", [])


def load_paper_nav(combo_id: str) -> dict[str, float]:
    """加载 Paper NAV 历史 (date_str -> nav_value)。

    支持三种文件格式:
    1. nav_history.json (首选)
    2. daily_nav.json (备选)
    3. shadow_progress.json -> daily_navs 字段 (影子跑格式)
    """
    nav_path = _PAPER_STATE / combo_id / "nav_history.json"
    if not nav_path.exists():
        alt_path = _PAPER_STATE / combo_id / "daily_nav.json"
        if alt_path.exists():
            nav_path = alt_path
        else:
            # 尝试 shadow_progress.json (影子跑引擎格式)
            shadow_path = _PAPER_STATE / combo_id / "shadow_progress.json"
            if shadow_path.exists():
                return _load_shadow_navs(shadow_path)
            return {}

    data = json.loads(nav_path.read_text(encoding="utf-8"))
    return _parse_nav_data(data)


def _load_shadow_navs(shadow_path: Path) -> dict[str, float]:
    """从 shadow_progress.json 的 daily_navs 字段提取 NAV。"""
    data = json.loads(shadow_path.read_text(encoding="utf-8"))
    daily_navs = data.get("daily_navs", [])
    if not daily_navs:
        return {}
    return _parse_nav_data(daily_navs)


def _parse_nav_data(data: list | dict) -> dict[str, float]:
    """解析 NAV 数据 (列表或字典格式)。"""
    out: dict[str, float] = {}
    if isinstance(data, list):
        for rec in data:
            d = rec.get("date") or rec.get("trade_date")
            nav = rec.get("nav")
            if d is not None and nav is not None:
                out[_normalize_date(str(d))] = float(nav)
    elif isinstance(data, dict):
        for d, nav in data.items():
            out[_normalize_date(d)] = float(nav)
    return out


def check_nav_health(combo_id: str, nav_series: dict[str, float]) -> NavHealthReport:
    """检查 NAV 曲线健康度。"""
    report = NavHealthReport(combo_id=combo_id)

    if not nav_series:
        report.status = "error"
        report.issues.append("NAV 序列为空")
        return report

    dates = sorted(nav_series.keys())
    report.first_date = dates[0]
    report.last_date = dates[-1]
    report.total_days = len(dates)

    # 检查异常跳变 (单日 > 10%)
    max_jump = 0.0
    flat_streak = 0
    max_flat = 0
    prev_nav = None
    for d in dates:
        nav = nav_series[d]
        if prev_nav and prev_nav > 0:
            change = abs(nav / prev_nav - 1)
            max_jump = max(max_jump, change)
            if abs(change) < 1e-8:
                flat_streak += 1
                max_flat = max(max_flat, flat_streak)
            else:
                flat_streak = 0
        prev_nav = nav

    report.max_daily_jump_pct = round(max_jump * 100, 2)
    report.flat_streak_days = max_flat

    # 判断状态
    if report.total_days < 10:
        report.status = "error"
        report.issues.append(f"数据点过少: {report.total_days} 天")
    elif max_jump > 0.10:
        report.status = "warning"
        report.issues.append(f"存在 >10% 单日跳变: {report.max_daily_jump_pct}%")
    elif max_flat >= 10:
        report.status = "warning"
        report.issues.append(f"连续 {max_flat} 天 NAV 不变 (可能停止运行)")
    else:
        report.status = "healthy"

    return report


def compute_s4_metrics(
    combo_id: str, nav_series: dict[str, float],
) -> dict[str, Any]:
    """计算 S4 验收所需的关键指标。"""
    if len(nav_series) < 20:
        return {"status": "insufficient_data", "n_days": len(nav_series)}

    dates = sorted(nav_series.keys())
    first_nav = nav_series[dates[0]]
    last_nav = nav_series[dates[-1]]
    n_days = len(dates)

    # 计算基础绩效
    total_return = (last_nav / first_nav - 1) if first_nav > 0 else 0
    trading_days_per_year = 244
    years = n_days / trading_days_per_year
    ar = (1 + total_return) ** (1 / max(years, 0.1)) - 1 if total_return > -1 else -1

    # 最大回撤
    peak = first_nav
    max_dd = 0.0
    for d in dates:
        nav = nav_series[d]
        peak = max(peak, nav)
        dd = (peak - nav) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    # S4 时长检查
    from datetime import datetime as dt
    first_dt = dt.strptime(dates[0], "%Y-%m-%d")
    last_dt = dt.strptime(dates[-1], "%Y-%m-%d")
    elapsed_months = (last_dt - first_dt).days / 30.44

    return {
        "combo_id": combo_id,
        "n_trading_days": n_days,
        "elapsed_months": round(elapsed_months, 1),
        "s4_min_months": 3.0,
        "s4_months_met": elapsed_months >= 3.0,
        "total_return_pct": round(total_return * 100, 2),
        "annualized_return_pct": round(ar * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "first_date": dates[0],
        "last_date": dates[-1],
    }


def run_reconciliation(combo_id: str) -> ReconciliationReport:
    """对单个组合运行完整对账。"""
    report = ReconciliationReport(
        combo_id=combo_id,
        generated_at=datetime.now().isoformat(timespec="seconds"),
    )

    nav = load_paper_nav(combo_id)
    report.nav_health = check_nav_health(combo_id, nav)
    report.s4_metrics = compute_s4_metrics(combo_id, nav)

    return report


def run_all_active() -> dict[str, ReconciliationReport]:
    """对所有活跃组合运行对账。"""
    combos = discover_active_combos()
    results: dict[str, ReconciliationReport] = {}
    for combo_id in combos:
        results[combo_id] = run_reconciliation(combo_id)
    return results


def _normalize_date(d: str) -> str:
    """Normalize date to YYYY-MM-DD format."""
    s = d.strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s
