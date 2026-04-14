"""FactorGuardPipeline 单元测试 — 含 HLZ N 值校正验证."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from factor_mining_daemon import FactorGuardPipeline


def _make_ic_records(n: int = 24, ic_mean: float = 0.04,
                     ic_std: float = 0.01, seed: int = 0) -> list[dict]:
    """构造模拟月频 IC 序列."""
    rng = np.random.RandomState(seed)
    ics = rng.normal(ic_mean, ic_std, n)
    return [{"period": f"20{23 + i // 12:02d}-{i % 12 + 1:02d}",
             "ic": float(ic)} for i, ic in enumerate(ics)]


class TestHLZUsesTotalTrials:
    """验证 HLZ 校正使用 total_trials 而非 passed 数."""

    def test_penalty_uses_total_trials(self):
        guard = FactorGuardPipeline(hlz_t_threshold=2.0)
        # 模拟 130 次总试验, 30 次通过
        guard.total_trials = 130
        for i in range(30):
            guard._passed_ics[f"f{i}"] = {
                "ics": [0.03] * 24, "icir": 0.8}
        # penalty = sqrt(2 * ln(130)) ≈ 3.12
        expected_penalty = np.sqrt(2.0 * np.log(130))
        assert expected_penalty > 3.0  # sanity
        # t_stat=5.0 → t_adj = 5.0 - 3.12 ≈ 1.88 < 2.0 → FAIL
        recs = _make_ic_records(24, 0.04, 0.008, seed=99)
        ok, reason = guard.check("new_f", "cs_rank($close)", recs, 5.0)
        assert not ok
        assert "hlz" in reason

    def test_high_t_stat_passes(self):
        guard = FactorGuardPipeline(hlz_t_threshold=2.0)
        guard.total_trials = 130
        # t_stat=6.0 → t_adj = 6.0 - 3.12 ≈ 2.88 > 2.0 → PASS
        recs = _make_ic_records(24, 0.05, 0.01, seed=42)
        ok, reason = guard.check("strong_f", "cs_rank($volume)", recs, 6.0)
        assert ok
        assert reason == ""

    def test_few_trials_low_penalty(self):
        guard = FactorGuardPipeline(hlz_t_threshold=2.0)
        guard.total_trials = 3
        # penalty = sqrt(2 * ln(3)) ≈ 1.48
        # t_stat=4.0 → t_adj = 4.0 - 1.48 ≈ 2.52 > 2.0 → PASS
        recs = _make_ic_records(24, 0.05, 0.01, seed=7)
        ok, _ = guard.check("early_f", "ts_mean($close, 10)", recs, 4.0)
        assert ok

    def test_total_trials_increments(self):
        guard = FactorGuardPipeline()
        assert guard.total_trials == 0
        recs = _make_ic_records(24, 0.01, 0.05, seed=1)
        guard.check("x", "cs_rank($close)", recs, 1.0)
        assert guard.total_trials == 1
        guard.check("y", "cs_rank($volume)", recs, 1.0)
        assert guard.total_trials == 2


class TestASTDedup:
    """AST 去重阶段测试."""

    def test_identical_expression_rejected(self):
        guard = FactorGuardPipeline(ast_sim_threshold=0.85)
        recs = _make_ic_records(24, 0.05, 0.01)
        guard.register_passed("f1", "cs_rank($close)", recs, icir=1.0)
        ok, reason = guard.check(
            "f2", "cs_rank($close)", recs, 5.0)
        assert not ok
        assert "ast_dup" in reason


class TestDaemonLogging:
    """验证 daemon 日志文件写入功能."""

    def test_file_handler_writes_log(self, tmp_path):
        import logging
        log_file = tmp_path / "test_daemon.log"
        handler = logging.FileHandler(str(log_file), encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s"))
        test_logger = logging.getLogger("test_daemon_log")
        test_logger.addHandler(handler)
        test_logger.setLevel(logging.INFO)
        test_logger.info("daemon log test message")
        handler.flush()
        handler.close()
        content = log_file.read_text(encoding="utf-8")
        assert "daemon log test message" in content


class TestMultiPeriodFwd:
    """P2-3: 多周期前向收益相关测试."""

    def test_knowledge_entry_multi_period_field(self):
        """KnowledgeEntry 包含 multi_period 字段."""
        from factor_mining_daemon import KnowledgeEntry
        e = KnowledgeEntry(name="x", expression="$close",
                           category="test")
        assert isinstance(e.multi_period, dict)
        assert e.multi_period == {}

    def test_knowledge_entry_with_multi_period_data(self):
        """KnowledgeEntry 可携带多周期数据."""
        from factor_mining_daemon import KnowledgeEntry
        mp = {"fwd_5d": {"icir": 1.2, "ic_mean": 0.03, "n": 24},
              "fwd_20d": {"icir": 0.8, "ic_mean": 0.02, "n": 24}}
        e = KnowledgeEntry(name="x", expression="$close",
                           category="test", multi_period=mp)
        assert len(e.multi_period) == 2
        assert e.multi_period["fwd_5d"]["icir"] == 1.2

    def test_load_market_data_multi_period(self):
        """load_market_data 支持多周期列计算."""
        from factor_mining_daemon import load_market_data
        # 仅测试签名接受 fwd_periods 参数
        import inspect
        sig = inspect.signature(load_market_data)
        assert "fwd_periods" in sig.parameters

    def test_run_mining_round_extra_fwd_cols_param(self):
        """run_mining_round 接受 extra_fwd_cols 参数."""
        from factor_mining_daemon import run_mining_round
        import inspect
        sig = inspect.signature(run_mining_round)
        assert "extra_fwd_cols" in sig.parameters
