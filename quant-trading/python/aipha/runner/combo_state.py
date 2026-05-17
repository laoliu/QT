"""统一的 ComboState 格式 (v4) + 多格式迁移支持。

解决 M-2: r115/v2/ComboEODRunner/paper_engine_eod 四套状态格式不兼容问题。
字段命名遵循 §13.12 规范: ts_code / shares / trade_date / YYYYMMDD。

支持自动检测并迁移以下格式:
- Format A (r115/r116, state_version=2/3): flat positions + code 字段
- Format B (v2_ensemble): profile 键 + 无 state_version
- Format C (ComboEODRunner): component_positions dict + ts_code
- Format D (legacy PaperEngine): account + meta 嵌套
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STATE_VERSION = 4


@dataclass
class UnifiedComboState:
    """统一组合状态 v4。"""

    state_version: int = STATE_VERSION
    profile_name: str = ""
    initial_capital: float = 0.0
    cash: float = 0.0
    frozen_cash: float = 0.0
    component_positions: dict[str, list[dict[str, Any]]] = field(
        default_factory=dict,
    )
    nav_history: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    last_rebalance: dict[str, str] = field(default_factory=dict)
    last_update: str = ""
    trade_day_count: int = 0
    vol_scale: float = 1.0
    peak_nav: float = 0.0
    consecutive_failures: dict[str, int] = field(default_factory=dict)
    order_counter: int = 0
    pending_orders: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict。"""
        return {
            "state_version": self.state_version,
            "profile_name": self.profile_name,
            "initial_capital": self.initial_capital,
            "cash": self.cash,
            "frozen_cash": self.frozen_cash,
            "component_positions": self.component_positions,
            "nav_history": self.nav_history,
            "trades": self.trades,
            "last_rebalance": self.last_rebalance,
            "last_update": self.last_update,
            "trade_day_count": self.trade_day_count,
            "vol_scale": self.vol_scale,
            "peak_nav": self.peak_nav,
            "consecutive_failures": self.consecutive_failures,
            "order_counter": self.order_counter,
            "pending_orders": self.pending_orders,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> UnifiedComboState:
        """从 dict 反序列化 (仅接受 v4 格式)。"""
        return cls(
            state_version=raw.get("state_version", STATE_VERSION),
            profile_name=raw.get("profile_name", ""),
            initial_capital=float(raw.get("initial_capital", 0)),
            cash=float(raw.get("cash", 0)),
            frozen_cash=float(raw.get("frozen_cash", 0)),
            component_positions=raw.get("component_positions", {}),
            nav_history=raw.get("nav_history", []),
            trades=raw.get("trades", []),
            last_rebalance=raw.get("last_rebalance", {}),
            last_update=raw.get("last_update", ""),
            trade_day_count=int(raw.get("trade_day_count", 0)),
            vol_scale=float(raw.get("vol_scale", 1.0)),
            peak_nav=float(raw.get("peak_nav", 0)),
            consecutive_failures=raw.get("consecutive_failures", {}),
            order_counter=int(raw.get("order_counter", 0)),
            pending_orders=raw.get("pending_orders", []),
        )


def detect_format(raw: dict[str, Any]) -> str:
    """检测状态格式类型: 'v4' / 'runner' / 'r115' / 'v2' / 'legacy'。"""
    if raw.get("state_version") == STATE_VERSION:
        return "v4"
    if "account" in raw and "meta" in raw:
        return "legacy"
    if "component_positions" in raw:
        return "runner"
    if "profile" in raw and "profile_name" not in raw:
        return "v2"
    # Default: r115 family (has profile_name or state_version ≤ 3)
    return "r115"


def migrate_state(raw: dict[str, Any]) -> dict[str, Any]:
    """将任意旧格式转换为 v4 统一格式。"""
    fmt = detect_format(raw)
    if fmt == "v4":
        return raw
    logger.info("检测到 %s 格式, 自动迁移至 v4", fmt)
    migrators = {
        "r115": _migrate_from_r115,
        "v2": _migrate_from_v2,
        "runner": _migrate_from_runner,
        "legacy": _migrate_from_legacy,
    }
    return migrators[fmt](raw)


def load_combo_state(path: Path | str) -> UnifiedComboState:
    """加载状态文件, 自动检测格式并迁移。"""
    path = Path(path)
    if not path.exists():
        return UnifiedComboState()
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    migrated = migrate_state(raw)
    return UnifiedComboState.from_dict(migrated)


def save_combo_state(state: UnifiedComboState, path: Path | str) -> None:
    """原子化保存状态 (先写 .tmp 再 rename)。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    state.last_update = datetime.now().isoformat()
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
    # 备份前一版本
    if path.exists():
        bak = path.with_suffix(".bak")
        shutil.copy2(path, bak)
    tmp.replace(path)


# -- 内部迁移函数 --

def _migrate_from_r115(raw: dict[str, Any]) -> dict[str, Any]:
    """Format A (r115/r116): flat positions + code → v4。"""
    # 按 component 分组持仓
    comp_pos: dict[str, list[dict[str, Any]]] = {}
    for pos in raw.get("positions", []):
        comp = pos.get("component", "unknown")
        entry = {
            "ts_code": pos.get("code", pos.get("ts_code", "")),
            "shares": pos.get("shares", 0),
            "avg_cost": pos.get("avg_cost", 0.0),
        }
        if "last_price" in pos:
            entry["last_price"] = pos["last_price"]
        if "buy_date" in pos:
            entry["buy_date"] = _normalize_date(pos["buy_date"])
        comp_pos.setdefault(comp, []).append(entry)

    # 迁移交易记录
    trades = []
    for t in raw.get("trades", []):
        trades.append({
            "ts_code": t.get("code", t.get("ts_code", "")),
            "side": t.get("side", ""),
            "price": t.get("price", 0),
            "shares": t.get("shares", 0),
            "amount": t.get("amount", 0),
            "cost": t.get("cost", 0),
            "component": t.get("component", ""),
            "trade_date": _normalize_date(t.get("date", "")),
        })

    # 迁移 NAV 历史
    nav_hist = []
    for n in raw.get("nav_history", []):
        nav_hist.append({
            "trade_date": _normalize_date(n.get("date", "")),
            "nav": n.get("nav", 0),
            "cash": n.get("cash", 0),
            "positions_value": n.get("pos_value", 0),
        })

    # 标准化 last_rebalance
    lr = raw.get("last_rebalance", {})
    if isinstance(lr, str):
        lr = {"default": _normalize_date(lr)}
    else:
        lr = {k: _normalize_date(v) for k, v in lr.items()}

    return {
        "state_version": STATE_VERSION,
        "profile_name": raw.get("profile_name", ""),
        "initial_capital": float(raw.get("initial_capital", 0)),
        "cash": float(raw.get("cash", 0)),
        "frozen_cash": 0.0,
        "component_positions": comp_pos,
        "nav_history": nav_hist,
        "trades": trades,
        "last_rebalance": lr,
        "last_update": raw.get("last_update", ""),
        "trade_day_count": int(raw.get("days_traded", 0)),
        "vol_scale": float(raw.get("vol_scale", 1.0)),
        "peak_nav": float(raw.get("peak_nav", 0)),
        "consecutive_failures": raw.get("consecutive_failures", {}),
        "order_counter": 0,
        "pending_orders": raw.get("pending_orders", []),
    }


def _migrate_from_v2(raw: dict[str, Any]) -> dict[str, Any]:
    """Format B (v2_ensemble) → v4。"""
    # v2 与 r115 持仓结构相似, 但少了 component/amount 等字段
    comp_pos: dict[str, list[dict[str, Any]]] = {}
    for pos in raw.get("positions", []):
        comp = pos.get("component", "default")
        entry = {
            "ts_code": pos.get("code", pos.get("ts_code", "")),
            "shares": pos.get("shares", 0),
            "avg_cost": pos.get("avg_cost", 0.0),
        }
        if "last_price" in pos:
            entry["last_price"] = pos["last_price"]
        if "entry_date" in pos:
            entry["buy_date"] = _normalize_date(pos["entry_date"])
        elif "buy_date" in pos:
            entry["buy_date"] = _normalize_date(pos["buy_date"])
        comp_pos.setdefault(comp, []).append(entry)

    trades = []
    for t in raw.get("trades", []):
        trades.append({
            "ts_code": t.get("code", t.get("ts_code", "")),
            "side": t.get("side", ""),
            "price": t.get("price", 0),
            "shares": t.get("shares", 0),
            "amount": t.get("amount", 0),
            "cost": t.get("cost", 0),
            "component": t.get("component", ""),
            "trade_date": _normalize_date(t.get("date", "")),
        })

    nav_hist = []
    for n in raw.get("nav_history", []):
        nav_hist.append({
            "trade_date": _normalize_date(n.get("date", "")),
            "nav": n.get("nav", 0),
            "cash": n.get("cash", 0),
            "positions_value": n.get("pos_value", 0),
            "n_positions": n.get("n_positions", 0),
        })

    lr = raw.get("last_rebalance", {})
    if isinstance(lr, str):
        lr = {"default": _normalize_date(lr)}
    else:
        lr = {k: _normalize_date(v) for k, v in lr.items()}

    return {
        "state_version": STATE_VERSION,
        "profile_name": raw.get("profile", ""),
        "initial_capital": 0.0,
        "cash": float(raw.get("cash", 0)),
        "frozen_cash": 0.0,
        "component_positions": comp_pos,
        "nav_history": nav_hist,
        "trades": trades,
        "last_rebalance": lr,
        "last_update": raw.get("last_update", ""),
        "trade_day_count": int(raw.get("days_since_rebalance", 0)),
        "vol_scale": 1.0,
        "peak_nav": 0.0,
        "consecutive_failures": {},
        "order_counter": 0,
        "pending_orders": [],
    }


def _migrate_from_runner(raw: dict[str, Any]) -> dict[str, Any]:
    """Format C (ComboEODRunner) → v4。"""
    # Runner 已用 ts_code + component_positions dict, 接近 v4
    comp_pos = {}
    for comp, positions in raw.get("component_positions", {}).items():
        entries = []
        for pos in positions:
            entry = {
                "ts_code": pos.get("ts_code", ""),
                "shares": pos.get("shares", 0),
                "avg_cost": pos.get("avg_cost", 0.0),
            }
            for k in ("today_bought_qty", "frozen_qty", "t_plus_n"):
                if k in pos:
                    entry[k] = pos[k]
            entries.append(entry)
        comp_pos[comp] = entries

    # NAV history: trade_date 可能是 YYYY-MM-DD, 统一到 YYYYMMDD
    nav_hist = []
    for n in raw.get("nav_history", []):
        nav_hist.append({
            "trade_date": _normalize_date(n.get("trade_date", "")),
            "nav": n.get("nav", 0),
            "cash": n.get("cash", 0),
            "positions_value": n.get("positions_value", 0),
            "component_values": n.get("component_values", {}),
            "n_positions": n.get("n_positions", 0),
        })

    lr = raw.get("last_rebalance", {})
    lr = {k: _normalize_date(v) for k, v in lr.items()}

    return {
        "state_version": STATE_VERSION,
        "profile_name": raw.get("profile_name", ""),
        "initial_capital": float(raw.get("initial_cash", 0)),
        "cash": float(raw.get("cash", 0)),
        "frozen_cash": float(raw.get("frozen_cash", 0)),
        "component_positions": comp_pos,
        "nav_history": nav_hist,
        "trades": [],
        "last_rebalance": lr,
        "last_update": "",
        "trade_day_count": int(raw.get("trade_day_count", 0)),
        "vol_scale": 1.0,
        "peak_nav": 0.0,
        "consecutive_failures": {},
        "order_counter": int(raw.get("order_counter", 0)),
        "pending_orders": [],
    }


def _migrate_from_legacy(raw: dict[str, Any]) -> dict[str, Any]:
    """Format D (legacy PaperEngine) → v4。"""
    account = raw.get("account", {})
    meta = raw.get("meta", {})
    positions = account.get("positions", [])

    comp_pos: dict[str, list[dict[str, Any]]] = {}
    for pos in positions:
        entry = {
            "ts_code": pos.get("ts_code", pos.get("code", "")),
            "shares": pos.get("shares", 0),
            "avg_cost": pos.get("avg_cost", 0.0),
        }
        comp_pos.setdefault("default", []).append(entry)

    return {
        "state_version": STATE_VERSION,
        "profile_name": meta.get("strategy_name", ""),
        "initial_capital": 0.0,
        "cash": float(account.get("cash", 0)),
        "frozen_cash": float(account.get("frozen_cash", 0)),
        "component_positions": comp_pos,
        "nav_history": [],
        "trades": [],
        "last_rebalance": {},
        "last_update": meta.get("saved_at", ""),
        "trade_day_count": 0,
        "vol_scale": 1.0,
        "peak_nav": float(account.get("peak_nav", 0) or 0),
        "consecutive_failures": {},
        "order_counter": 0,
        "pending_orders": [],
    }


def _normalize_date(d: str) -> str:
    """统一日期格式为 YYYYMMDD。"""
    if not d:
        return ""
    # YYYY-MM-DD → YYYYMMDD
    if "-" in d and len(d) >= 10:
        return d[:10].replace("-", "")
    # 已经是 YYYYMMDD
    if len(d) == 8 and d.isdigit():
        return d
    # ISO datetime → 取前 10 字符
    if "T" in d:
        return d[:10].replace("-", "")
    return d
