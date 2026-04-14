"""
StateManager 状态持久化管理器测试

测试覆盖：
- 状态保存/加载往返
- Gateway 状态提取与恢复
- 增量保存（hash 去重）
- 备份清理
- 空状态/损坏文件处理
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aipha.runner.state_manager import (
    AccountState,
    PositionState,
    StateManager,
    StateSnapshot,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_mock_gateway(
    cash: float = 100_000.0,
    frozen_cash: float = 0.0,
    positions: dict | None = None,
    trade_date: date | None = None,
) -> MagicMock:
    """创建测试用 mock gateway"""
    gw = MagicMock()
    gw._cash = Decimal(str(cash))
    gw._frozen_cash = Decimal(str(frozen_cash))
    gw._positions = positions or {}
    gw._current_trade_date = trade_date
    return gw


# ─────────────────────────────────────────────────────────────────────────────
# StateSnapshot 测试
# ─────────────────────────────────────────────────────────────────────────────

class TestStateSnapshot:
    """快照序列化/反序列化"""

    def test_roundtrip(self):
        """往返序列化一致"""
        positions = [
            PositionState(symbol="127045", qty=100, avg_cost=105.5),
            PositionState(symbol="128054", qty=200, avg_cost=88.3, today_bought=50),
        ]
        account = AccountState(
            cash=95_000.0,
            frozen_cash=5_000.0,
            positions=positions,
            peak_nav=1_010_000.0,
            current_trade_date="2024-06-15",
        )
        snapshot = StateSnapshot(
            account=account,
            meta={"strategy_name": "test", "version": "1.0"},
        )

        data = snapshot.to_dict()
        restored = StateSnapshot.from_dict(data)

        assert restored.account.cash == 95_000.0
        assert restored.account.frozen_cash == 5_000.0
        assert len(restored.account.positions) == 2
        assert restored.account.positions[0].symbol == "127045"
        assert restored.account.positions[0].qty == 100
        assert restored.account.positions[0].avg_cost == 105.5
        assert restored.account.positions[1].today_bought == 50
        assert restored.account.peak_nav == 1_010_000.0
        assert restored.account.current_trade_date == "2024-06-15"
        assert restored.meta["strategy_name"] == "test"

    def test_empty_positions(self):
        """空持仓序列化"""
        account = AccountState(cash=100_000.0, frozen_cash=0.0)
        snapshot = StateSnapshot(account=account)
        data = snapshot.to_dict()
        restored = StateSnapshot.from_dict(data)

        assert restored.account.cash == 100_000.0
        assert len(restored.account.positions) == 0


# ─────────────────────────────────────────────────────────────────────────────
# StateManager 测试
# ─────────────────────────────────────────────────────────────────────────────

class TestStateManager:
    """管理器测试"""

    def test_save_and_load(self, tmp_path: Path):
        """基本保存/加载"""
        manager = StateManager(state_dir=tmp_path)
        gateway = _make_mock_gateway(
            cash=95_000.0,
            positions={
                "127045": {"qty": 100, "avg_cost": Decimal("105.5"), "today_bought": 0, "frozen": 0},
            },
            trade_date=date(2024, 6, 15),
        )

        # 保存
        result = manager.save_from_gateway(gateway, strategy_name="test_strat")
        assert result is True
        assert manager.state_path.exists()

        # 加载
        snapshot = manager.load()
        assert snapshot is not None
        assert snapshot.account.cash == 95_000.0
        assert len(snapshot.account.positions) == 1
        assert snapshot.account.positions[0].symbol == "127045"
        assert snapshot.account.positions[0].qty == 100
        assert snapshot.meta["strategy_name"] == "test_strat"

    def test_incremental_save_skips_unchanged(self, tmp_path: Path):
        """状态未变化时跳过保存"""
        manager = StateManager(state_dir=tmp_path)
        gateway = _make_mock_gateway(cash=100_000.0)

        # 第一次保存
        assert manager.save_from_gateway(gateway) is True
        # 第二次相同状态
        assert manager.save_from_gateway(gateway) is False

    def test_save_after_state_change(self, tmp_path: Path):
        """状态变化后重新保存"""
        manager = StateManager(state_dir=tmp_path)
        gateway = _make_mock_gateway(cash=100_000.0)

        assert manager.save_from_gateway(gateway) is True

        # 修改现金
        gateway._cash = Decimal("90000")
        assert manager.save_from_gateway(gateway) is True

    def test_restore_to_gateway(self, tmp_path: Path):
        """状态恢复到 Gateway"""
        manager = StateManager(state_dir=tmp_path)

        snapshot = StateSnapshot(
            account=AccountState(
                cash=85_000.0,
                frozen_cash=5_000.0,
                positions=[
                    PositionState(
                        symbol="127045", qty=100, avg_cost=105.5,
                        today_bought=50, frozen=20,
                    ),
                ],
                current_trade_date="2024-06-15",
            ),
        )

        gateway = _make_mock_gateway()
        manager.restore_to_gateway(gateway, snapshot)

        assert gateway._cash == Decimal("85000.0")
        assert gateway._frozen_cash == Decimal("5000.0")
        assert "127045" in gateway._positions
        assert gateway._positions["127045"]["qty"] == 100
        assert gateway._positions["127045"]["avg_cost"] == Decimal("105.5")
        assert gateway._current_trade_date == date(2024, 6, 15)

    def test_load_nonexistent(self, tmp_path: Path):
        """加载不存在的文件返回 None"""
        manager = StateManager(state_dir=tmp_path)
        assert manager.load() is None

    def test_load_corrupted_file(self, tmp_path: Path):
        """损坏的文件返回 None"""
        manager = StateManager(state_dir=tmp_path)
        state_path = tmp_path / "paper_state.json"
        state_path.write_text("not valid json {{{")

        assert manager.load() is None

    def test_backup_creation(self, tmp_path: Path):
        """保存时创建备份"""
        manager = StateManager(state_dir=tmp_path)
        gateway = _make_mock_gateway(cash=100_000.0)

        # 第一次保存
        manager.save_from_gateway(gateway)

        # 修改并第二次保存（应创建备份）
        gateway._cash = Decimal("90000")
        manager.save_from_gateway(gateway)

        backups = list(tmp_path.glob("*.bak"))
        assert len(backups) >= 1

    def test_backup_cleanup(self, tmp_path: Path):
        """备份数量限制"""
        manager = StateManager(state_dir=tmp_path, max_backups=2)
        gateway = _make_mock_gateway()

        # 创建 5 次保存（4 次备份）
        for i in range(5):
            gateway._cash = Decimal(str(100_000 - i * 1000))
            manager._last_hash = None  # 强制每次都保存
            manager.save_from_gateway(gateway)

        backups = list(tmp_path.glob("*.bak"))
        assert len(backups) <= 2

    def test_full_roundtrip_gateway(self, tmp_path: Path):
        """完整 Gateway → 保存 → 加载 → 恢复 往返"""
        manager = StateManager(state_dir=tmp_path)

        # 原始 Gateway
        original_gw = _make_mock_gateway(
            cash=75_000.0,
            frozen_cash=10_000.0,
            positions={
                "127045": {"qty": 200, "avg_cost": Decimal("105.0"), "today_bought": 100, "frozen": 50},
                "128054": {"qty": 300, "avg_cost": Decimal("88.5"), "today_bought": 0, "frozen": 0},
            },
            trade_date=date(2024, 6, 15),
        )

        # 保存
        manager.save_from_gateway(original_gw, strategy_name="full_test")

        # 加载
        snapshot = manager.load()
        assert snapshot is not None

        # 恢复到新 Gateway
        new_gw = _make_mock_gateway()
        manager.restore_to_gateway(new_gw, snapshot)

        # 验证一致性
        assert float(new_gw._cash) == 75_000.0
        assert float(new_gw._frozen_cash) == 10_000.0
        assert len(new_gw._positions) == 2
        assert new_gw._positions["127045"]["qty"] == 200
        assert new_gw._positions["128054"]["qty"] == 300
        assert new_gw._current_trade_date == date(2024, 6, 15)
