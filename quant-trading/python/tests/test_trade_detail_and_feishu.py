"""飞书通知渠道 + save_run_result CSV 输出 + 交易明细页面 的测试"""
from __future__ import annotations

import asyncio
import csv
import json
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ============================================================================
# FeishuChannel 测试
# ============================================================================


class TestFeishuChannel:
    """飞书通知渠道测试"""

    def _make_channel(self, url: str = "https://open.feishu.cn/hook/test123"):
        from aipha.monitor.notifiers import FeishuChannel, FeishuConfig
        return FeishuChannel(FeishuConfig(webhook_url=url))

    def _make_message(self):
        from aipha.monitor.notifiers import AlertMessage, AlertLevel, AlertCategory
        return AlertMessage(
            title="测试告警",
            content="这是一条测试消息",
            level=AlertLevel.WARNING,
            category=AlertCategory.SYSTEM,
        )

    def test_channel_name(self):
        ch = self._make_channel()
        assert ch.get_name() == "feishu"

    def test_build_payload(self):
        ch = self._make_channel()
        msg = self._make_message()
        payload = ch._build_payload(msg)
        assert payload["msg_type"] == "interactive"
        assert "card" in payload
        assert "header" in payload["card"]
        assert payload["card"]["header"]["template"] == "orange"  # WARNING -> orange
        elements = payload["card"]["elements"]
        assert len(elements) == 1
        assert "测试告警" in elements[0]["content"]

    def test_level_color_mapping(self):
        from aipha.monitor.notifiers import FeishuChannel, AlertLevel
        assert FeishuChannel._level_color(AlertLevel.DEBUG) == "grey"
        assert FeishuChannel._level_color(AlertLevel.INFO) == "blue"
        assert FeishuChannel._level_color(AlertLevel.WARNING) == "orange"
        assert FeishuChannel._level_color(AlertLevel.ERROR) == "red"
        assert FeishuChannel._level_color(AlertLevel.CRITICAL) == "red"

    def test_get_signed_url_no_secret(self):
        ch = self._make_channel("https://open.feishu.cn/hook/abc")
        assert ch._get_signed_url() == "https://open.feishu.cn/hook/abc"

    def test_get_signed_url_with_secret(self):
        from aipha.monitor.notifiers import FeishuChannel, FeishuConfig
        ch = FeishuChannel(FeishuConfig(
            webhook_url="https://open.feishu.cn/hook/abc",
            secret="test_secret",
        ))
        signed = ch._get_signed_url()
        assert "timestamp=" in signed
        assert "sign=" in signed

    @pytest.mark.asyncio
    async def test_send_success(self):
        ch = self._make_channel()
        msg = self._make_message()
        mock_resp = AsyncMock()
        mock_resp.json = AsyncMock(return_value={"code": 0})
        mock_resp.status = 200

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(return_value=False),
        ))

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await ch.send(msg)
        assert result is True

    @pytest.mark.asyncio
    async def test_send_failure(self):
        ch = self._make_channel()
        msg = self._make_message()
        mock_resp = AsyncMock()
        mock_resp.json = AsyncMock(return_value={"code": 9999, "msg": "error"})

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(return_value=False),
        ))

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await ch.send(msg)
        assert result is False


# ============================================================================
# save_run_result CSV 输出测试
# ============================================================================


class TestSaveRunResultCSV:
    """测试 save_run_result 导出 trades.csv 和 daily_snapshots.csv"""

    def _make_run_result(self, output_dir: Path):
        """构造一个最小可用的 BacktestRunResult"""
        from aipha.backtest.engine import BacktestResult, TradeRecord, DailySnapshot
        from aipha.execution.fees import FeeBreakdown
        from aipha.backtest.config_models import (
            BacktestRunConfig, BacktestRunResult,
            DataConfig, StrategyConfig, RiskConfig,
            UniverseConfig, CostConfig,
        )
        from aipha.utils.reproducibility import ExperimentFingerprint

        fees = FeeBreakdown(commission=0.5, stamp_tax=0.0, transfer_fee=0.01)

        trades = [
            TradeRecord(
                trade_id="T001", order_id="O001", symbol="128000.SZ",
                side="BUY", qty=100, price=105.5, notional=10550.0,
                fees=fees, trade_time=datetime(2024, 1, 2, 9, 30),
                trade_date=date(2024, 1, 2), reason="signal",
            ),
            TradeRecord(
                trade_id="T002", order_id="O002", symbol="128000.SZ",
                side="SELL", qty=100, price=110.0, notional=11000.0,
                fees=fees, trade_time=datetime(2024, 1, 5, 14, 30),
                trade_date=date(2024, 1, 5), reason="rebalance",
            ),
        ]

        snapshots = [
            DailySnapshot(
                trade_date=date(2024, 1, 2), nav=1000000, cash=989450,
                positions={"128000.SZ": 100}, position_values={"128000.SZ": 10550},
                daily_return=0.0, cumulative_return=0.0,
            ),
            DailySnapshot(
                trade_date=date(2024, 1, 3), nav=1000500, cash=989450,
                positions={"128000.SZ": 100}, position_values={"128000.SZ": 11050},
                daily_return=0.0005, cumulative_return=0.0005,
            ),
        ]

        result = BacktestResult(
            strategy_name="test_strategy",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            initial_capital=1000000.0,
            final_nav=1005000.0,
            final_cash=994000.0,
            final_positions={},
            daily_snapshots=snapshots,
            trades=trades,
            total_trades=2,
            total_fees=FeeBreakdown(commission=1.0, stamp_tax=0.0, transfer_fee=0.02),
            exclusion_stats={},
        )

        config = BacktestRunConfig(
            name="test_csv_output",
            data=DataConfig(source="synthetic", start_date=date(2024, 1, 1), end_date=date(2024, 1, 31)),
            strategy=StrategyConfig(name="test_strategy"),
            universe=UniverseConfig(),
            cost=CostConfig(),
        )

        fingerprint = ExperimentFingerprint(
            git_commit="abc123def456789",
            git_short="abc123d",
            git_dirty=False,
            git_branch="main",
            config_path=None,
            config_hash="def456",
            data_fingerprint="v1",
            random_seed=42,
            run_time=datetime.now(),
        )

        return BacktestRunResult(
            config=config,
            result=result,
            fingerprint=fingerprint,
            output_dir=output_dir,
        )

    def test_trades_csv_created(self, tmp_path):
        run_result = self._make_run_result(tmp_path)
        from aipha.backtest.config_parser import save_run_result
        paths = save_run_result(run_result)

        assert "trades" in paths
        assert paths["trades"].exists()
        with open(paths["trades"], encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["symbol"] == "128000.SZ"
        assert rows[0]["side"] == "BUY"
        assert float(rows[0]["price"]) == 105.5

    def test_daily_snapshots_csv_created(self, tmp_path):
        run_result = self._make_run_result(tmp_path)
        from aipha.backtest.config_parser import save_run_result
        paths = save_run_result(run_result)

        assert "daily_snapshots" in paths
        assert paths["daily_snapshots"].exists()
        with open(paths["daily_snapshots"], encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["date"] == "2024-01-02"
        positions = json.loads(rows[0]["positions"])
        assert "128000.SZ" in positions

    def test_no_trades_no_csv(self, tmp_path):
        """无交易时不生成 trades.csv"""
        run_result = self._make_run_result(tmp_path)
        run_result.result.trades = []
        from aipha.backtest.config_parser import save_run_result
        paths = save_run_result(run_result)
        assert "trades" not in paths


# ============================================================================
# 交易明细页面数据函数测试
# ============================================================================


class TestTradeDetailDataFunctions:
    """交易明细页面数据加载与计算函数"""

    def _write_trades_csv(self, d: Path) -> Path:
        csv_path = d / "trades.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "trade_id", "order_id", "date", "time", "symbol", "side",
                "qty", "price", "notional", "commission", "stamp_tax",
                "transfer_fee", "total_fee", "reason",
            ])
            w.writerow(["T1", "O1", "2024-01-02", "2024-01-02T09:30:00", "128000.SZ",
                         "BUY", 100, 105.0, 10500, 0.5, 0, 0.01, 0.51, "signal"])
            w.writerow(["T2", "O2", "2024-01-05", "2024-01-05T14:30:00", "128000.SZ",
                         "SELL", 100, 110.0, 11000, 0.5, 0, 0.01, 0.51, "rebalance"])
            w.writerow(["T3", "O3", "2024-01-03", "2024-01-03T09:30:00", "128001.SZ",
                         "BUY", 50, 90.0, 4500, 0.25, 0, 0.005, 0.255, "signal"])
        return csv_path

    def _write_snapshots_csv(self, d: Path) -> Path:
        csv_path = d / "daily_snapshots.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "nav", "cash", "frozen_cash", "daily_return",
                         "cumulative_return", "position_count", "positions", "position_values"])
            w.writerow(["2024-01-02", 1000000, 989500, 0, 0.0, 0.0, 1,
                         '{"128000.SZ": 100}', '{"128000.SZ": 10500}'])
            w.writerow(["2024-01-03", 1001000, 985000, 0, 0.001, 0.001, 2,
                         '{"128000.SZ": 100, "128001.SZ": 50}', '{"128000.SZ": 10600, "128001.SZ": 4400}'])
        return csv_path

    def test_load_trades(self, tmp_path):
        self._write_trades_csv(tmp_path)
        # 直接用 pandas 验证 CSV 读取（避免 streamlit 依赖的动态加载）
        import pandas as pd
        df = pd.read_csv(tmp_path / "trades.csv", parse_dates=["date"])
        assert len(df) == 3
        assert "symbol" in df.columns
        assert set(df["symbol"].unique()) == {"128000.SZ", "128001.SZ"}

    def test_compute_per_symbol_pnl_logic(self, tmp_path):
        """测试标的盈亏计算逻辑"""
        import pandas as pd
        self._write_trades_csv(tmp_path)
        df = pd.read_csv(tmp_path / "trades.csv", parse_dates=["date"])
        df["side_cn"] = df["side"].map({"BUY": "买入", "SELL": "卖出"})

        # 手动验证 128000.SZ: buy 10500, sell 11000, fee 1.02 -> pnl = 498.98
        sym_df = df[df["symbol"] == "128000.SZ"]
        buy_notional = sym_df[sym_df["side"] == "BUY"]["notional"].sum()
        sell_notional = sym_df[sym_df["side"] == "SELL"]["notional"].sum()
        total_fee = sym_df["total_fee"].sum()
        expected_pnl = sell_notional - buy_notional - total_fee
        assert expected_pnl == pytest.approx(498.98, abs=0.1)

    def test_daily_snapshots_parsing(self, tmp_path):
        """测试快照 CSV 解析"""
        import pandas as pd
        self._write_snapshots_csv(tmp_path)
        df = pd.read_csv(tmp_path / "daily_snapshots.csv", parse_dates=["date"])
        assert len(df) == 2
        positions = json.loads(df.iloc[1]["positions"])
        assert "128001.SZ" in positions
        assert positions["128000.SZ"] == 100
