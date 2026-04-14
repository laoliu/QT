"""状态管理器 — JSON 持久化策略运行状态"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class PositionState:
    symbol: str
    quantity: int
    avg_cost: float = 0.0
    frozen: int = 0
    last_update: str = ""


@dataclass
class AccountState:
    cash: float = 1_000_000.0
    initial_cash: float = 1_000_000.0
    positions: list[PositionState] = field(default_factory=list)
    nav: float = 0.0
    trading_day: str = ""
    strategy_name: str = ""

    @property
    def market_value(self) -> float:
        return sum(p.quantity * p.avg_cost for p in self.positions)


@dataclass
class StateSnapshot:
    account: AccountState
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


class StateManager:
    """状态管理器 — JSON 文件持久化"""

    def __init__(self, state_dir: str | Path = "state", max_backups: int = 5):
        self._dir = Path(state_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._max_backups = max_backups
        self._state_file = self._dir / "current_state.json"
        self._backup_dir = self._dir / "backups"
        self._backup_dir.mkdir(exist_ok=True)

    def save(self, snapshot: StateSnapshot) -> Path:
        """原子写入状态快照"""
        data = self._to_dict(snapshot)
        tmp = self._state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._state_file)
        self._create_backup(data, snapshot.timestamp)
        logger.info("状态已保存: %s", self._state_file)
        return self._state_file

    def load(self) -> Optional[StateSnapshot]:
        if not self._state_file.exists():
            return None
        data = json.loads(self._state_file.read_text(encoding="utf-8"))
        return self._from_dict(data)

    def save_account(self, account: AccountState, **metadata) -> Path:
        snap = StateSnapshot(account=account, metadata=metadata)
        return self.save(snap)

    def _create_backup(self, data: dict, ts: str) -> None:
        safe_ts = ts.replace(":", "-").replace(".", "-")[:19]
        backup = self._backup_dir / f"state_{safe_ts}.json"
        backup.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._prune_backups()

    def _prune_backups(self) -> None:
        backups = sorted(self._backup_dir.glob("state_*.json"))
        while len(backups) > self._max_backups:
            oldest = backups.pop(0)
            oldest.unlink(missing_ok=True)

    def list_backups(self) -> list[Path]:
        return sorted(self._backup_dir.glob("state_*.json"))

    def restore_from_backup(self, backup_path: str | Path) -> StateSnapshot:
        p = Path(backup_path)
        if not p.exists():
            raise FileNotFoundError(f"备份不存在: {p}")
        data = json.loads(p.read_text(encoding="utf-8"))
        snap = self._from_dict(data)
        self.save(snap)
        logger.info("从备份恢复: %s", p.name)
        return snap

    @staticmethod
    def _to_dict(snap: StateSnapshot) -> dict:
        return {
            "timestamp": snap.timestamp,
            "metadata": snap.metadata,
            "account": {
                "cash": snap.account.cash,
                "initial_cash": snap.account.initial_cash,
                "nav": snap.account.nav,
                "trading_day": snap.account.trading_day,
                "strategy_name": snap.account.strategy_name,
                "positions": [asdict(p) for p in snap.account.positions],
            },
        }

    @staticmethod
    def _from_dict(data: dict) -> StateSnapshot:
        acct_data = data.get("account", {})
        positions = [PositionState(**p) for p in acct_data.get("positions", [])]
        account = AccountState(
            cash=acct_data.get("cash", 0),
            initial_cash=acct_data.get("initial_cash", 0),
            positions=positions,
            nav=acct_data.get("nav", 0),
            trading_day=acct_data.get("trading_day", ""),
            strategy_name=acct_data.get("strategy_name", ""),
        )
        return StateSnapshot(
            account=account,
            metadata=data.get("metadata", {}),
            timestamp=data.get("timestamp", ""),
        )
