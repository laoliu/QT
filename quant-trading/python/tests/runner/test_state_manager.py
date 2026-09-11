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

from datetime import date
from decimal import Decimal
from pathlib import Path


from aipha.runner.state_manager import (
    AccountState,
    PositionState,
    StateManager,
    StateSnapshot,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

class _FakePaperGateway:
    """PaperGateway 的最小替身（§13.16）。

    这里刻意**不用** MagicMock：state_manager 用 ``_is_paper_gateway``
    标记来识别模拟盘网关，动机正是防止裸 MagicMock 的 auto-attribute
    冒充出一个 ``save_state`` / ``restore_state`` 来（§13.16）。用 Mock
    写这些测试的话，走的是「不支持的网关类型」分支 —— 测试看着在测
    持久化，实际测的是空状态。

    因此替身必须真的实现公开接口，内部存储沿用 ``_cash`` / ``_positions``
    等私有字段，方便断言。
    """

    _is_paper_gateway = True

    def __init__(
        self,
        cash: float = 100_000.0,
        frozen_cash: float = 0.0,
        positions: dict | None = None,
        trade_date: date | None = None,
    ) -> None:
        self._cash = Decimal(str(cash))
        self._frozen_cash = Decimal(str(frozen_cash))
        self._positions: dict = dict(positions or {})
        self._current_trade_date = trade_date
        self._orders: dict = {}
        self._active_orders: list = []

    # -- §13.16 公开接口 ------------------------------------------------
    def save_state(self) -> dict:
        """与 PaperGateway.save_state 相同的键名/口径。"""
        return {
            "cash": float(self._cash),
            "frozen_cash": float(self._frozen_cash),
            "positions": {
                symbol: {
                    "total_qty": p.get("qty", 0),
                    "avg_cost": float(p.get("avg_cost", 0)),
                    "today_bought_qty": p.get("today_bought", 0),
                    "frozen_qty": p.get("frozen", 0),
                }
                for symbol, p in self._positions.items()
            },
            "latest_prices": {},
        }

    def restore_state(self, state: dict) -> None:
        self._cash = Decimal(str(state.get("cash", 0)))
        self._frozen_cash = Decimal(str(state.get("frozen_cash", 0)))
        self._positions = {
            symbol: {
                "qty": p.get("total_qty", 0),
                "avg_cost": Decimal(str(p.get("avg_cost", 0))),
                "today_bought": p.get("today_bought_qty", 0),
                "frozen": p.get("frozen_qty", 0),
            }
            for symbol, p in state.get("positions", {}).items()
        }

    def get_active_orders(self) -> list:
        return list(self._active_orders)


def _make_mock_gateway(
    cash: float = 100_000.0,
    frozen_cash: float = 0.0,
    positions: dict | None = None,
    trade_date: date | None = None,
) -> _FakePaperGateway:
    """创建测试用 PaperGateway 替身"""
    return _FakePaperGateway(
        cash=cash,
        frozen_cash=frozen_cash,
        positions=positions,
        trade_date=trade_date,
    )


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
        # T+1 相关字段（今日买入/冻结）必须一起回来，否则可卖量算错
        assert gateway._positions["127045"]["today_bought"] == 50
        assert gateway._positions["127045"]["frozen"] == 20
        # 交易日不在 §13.16 的 restore_state 契约里（PaperGateway 没有
        # _current_trade_date，日期状态在 Position.last_trade_date 上）。
        # 这个缺口由 TestTradeDateNotPersisted 单独记录。

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
        # T+1 字段同样要往返
        assert new_gw._positions["127045"]["today_bought"] == 100
        assert new_gw._positions["127045"]["frozen"] == 50
        assert new_gw._positions["128054"]["today_bought"] == 0
        # 交易日不往返 —— 见 TestTradeDateNotPersisted


# ─────────────────────────────────────────────────────────────────────────────
# 已知缺口：交易日/last_trade_date 不参与持久化
# ─────────────────────────────────────────────────────────────────────────────

class TestTradeDateNotPersisted:
    """记录一个真实缺口：快照不保存「持仓上次结算到哪一天」。

    链路：``PaperGateway.save_state()`` 只写 total_qty / avg_cost /
    today_bought_qty / frozen_qty / t_plus_n，没有 ``Position.last_trade_date``；
    ``StateManager`` 的 ``PositionState`` 也没有对应字段。于是恢复出来的
    Position 一律是 ``last_trade_date=None``。

    而 ``Position.day_end_unlock`` 的判断是::

        if self.last_trade_date is None or new_trade_date > self.last_trade_date:
            self.today_bought_qty = 0

    ``None`` 被当成「可以解锁」。后果分两种：

    * 跨日重启 —— 正常，本该解锁；
    * **当日重启** —— T 日买入 100 股（today_bought_qty=100），存快照，
      同一天进程重启，恢复后 last_trade_date=None，接着 day_end/
      set_trade_date 传入同一个 T 日，条件里 ``None`` 命中，
      today_bought_qty 被清零 → 这 100 股当天就变成可卖，**违反 T+1**。

    影响面：模拟盘/回测的可卖量偏乐观（实盘由券商侧强制 T+1，不受此影响）。
    修法需要给快照加字段，涉及 SCHEMA_VERSION 与已落盘状态文件的迁移，
    因此单独留给人决策，不在「修测试」范围内顺手改掉。
    """

    def test_gateway_save_state_omits_last_trade_date(self) -> None:
        """守住现状：PaperGateway.save_state() 的持仓字段里没有日期

        这条不是在认可现状，而是让「缺口被补上」这件事必须显式改测试，
        不会无声无息地漂走。
        """
        import inspect

        from aipha.execution.paper_gateway import PaperGateway

        source = inspect.getsource(PaperGateway.save_state)
        assert "last_trade_date" not in source, (
            "PaperGateway.save_state 已开始持久化 last_trade_date —— "
            "缺口已修复，请同步更新 StateManager.PositionState "
            "与本测试（见类文档）"
        )

    def test_position_state_has_no_date_field(self) -> None:
        """守住现状：PositionState 没有承载日期的字段"""
        import dataclasses

        from aipha.runner.state_manager import PositionState

        names = {f.name for f in dataclasses.fields(PositionState)}
        assert "last_trade_date" not in names, (
            "PositionState 已新增 last_trade_date —— 请同步 "
            "save_from_gateway / restore_to_gateway 并更新本测试"
        )

    def test_same_day_restart_unlocks_today_bought(self) -> None:
        """复现后果：当日重启后，T 日买入被当天解锁（T+1 被绕过）"""
        from datetime import date as _date

        from aipha.portfolio.position import Position

        trade_day = _date(2024, 6, 17)

        # 重启前：T 日买入 100 股，仍在 T+1 锁定中
        before = Position(
            symbol="600000.SH", total_qty=100,
            avg_cost=Decimal("10.0"), today_bought_qty=100,
        )
        before.last_trade_date = trade_day
        before.day_end_unlock(new_trade_date=trade_day)  # 同日，不应解锁
        assert before.today_bought_qty == 100, "同一交易日不应解锁"

        # 重启后：last_trade_date 丢失（快照没存），其余字段照原样恢复
        after = Position(
            symbol="600000.SH", total_qty=100,
            avg_cost=Decimal("10.0"), today_bought_qty=100,
        )
        assert after.last_trade_date is None, "恢复后日期状态确实丢了"
        after.day_end_unlock(new_trade_date=trade_day)  # 同一个 T 日

        assert after.today_bought_qty == 0, (
            "当前实现下同日重启会解锁 today_bought —— 若此断言失败，"
            "说明缺口已修复，请删掉本测试并更新类文档"
        )
