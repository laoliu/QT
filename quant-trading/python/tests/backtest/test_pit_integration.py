"""PIT 注入层集成测试

测试覆盖：
- PIT lookup 在真实数据流中的边界场景
- 跨季度报告切换
- 财务数据与策略打分的端到端集成
- 多股票、多报告期的交叉验证
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from aipha.backtest.config_runner import _enrich_bar_metadata, _pit_lookup
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo
from aipha.strategy.pead_event import PEADConfig, PEADStrategy


# ===========================================================================
# 辅助工具
# ===========================================================================


def _make_fina_record(ann_date: date, roe: float = 10.0, eps: float = 1.0,
                      dt_netprofit_yoy: float = 15.0, **extra):
    """创建一条 PIT 财务记录"""
    data = {"roe": roe, "eps": eps, "dt_netprofit_yoy": dt_netprofit_yoy}
    data.update(extra)
    return (ann_date, data)


def _make_context(dt=None, nav=1_000_000.0):
    return StrategyContext(
        current_time=dt or datetime(2024, 6, 1, 15, 0),
        positions={},
        cash=nav,
        nav=nav,
    )


# ===========================================================================
# TestPitLookupIntegration — PIT 查找在多记录流中的行为
# ===========================================================================


class TestPitLookupIntegration:
    """测试 PIT 查找在多季度报告流中的行为"""

    def _build_quarterly_records(self):
        """构建典型的 4 季度报告发布时间线"""
        return [
            _make_fina_record(date(2024, 4, 28), roe=10.0, eps=0.5),   # Q1
            _make_fina_record(date(2024, 8, 25), roe=12.0, eps=1.2),   # Q2 (半年报)
            _make_fina_record(date(2024, 10, 30), roe=11.0, eps=0.9),  # Q3
            _make_fina_record(date(2025, 3, 31), roe=15.0, eps=2.0),   # Q4 (年报)
        ]

    def test_before_first_announcement(self):
        """首份报告发布前查不到数据"""
        records = self._build_quarterly_records()
        result = _pit_lookup(records, date(2024, 1, 15))
        assert result is None

    def test_exact_announcement_date(self):
        """恰好在公告日查到该报告"""
        records = self._build_quarterly_records()
        result = _pit_lookup(records, date(2024, 4, 28))
        assert result is not None
        assert result["roe"] == 10.0

    def test_between_q1_q2(self):
        """Q1 与 Q2 公告之间 → 返回 Q1 数据"""
        records = self._build_quarterly_records()
        result = _pit_lookup(records, date(2024, 6, 15))
        assert result["roe"] == 10.0
        assert result["eps"] == 0.5

    def test_after_q2(self):
        """Q2 公告后 → 返回 Q2 (最新可见)"""
        records = self._build_quarterly_records()
        result = _pit_lookup(records, date(2024, 9, 10))
        assert result["roe"] == 12.0

    def test_after_all_announcements(self):
        """全部公告之后 → 返回最后一条"""
        records = self._build_quarterly_records()
        result = _pit_lookup(records, date(2025, 12, 31))
        assert result["roe"] == 15.0
        assert result["eps"] == 2.0

    def test_same_day_multiple_records_takes_last(self):
        """同一天发布多份报告 → 取排序在后的那一份"""
        records = [
            _make_fina_record(date(2024, 4, 28), roe=9.0),   # 年报修正前
            _make_fina_record(date(2024, 4, 28), roe=10.0),  # 年报修正后
        ]
        result = _pit_lookup(records, date(2024, 4, 28))
        # 排序后 records[1] 是最后一个 ann_date <= trade_date 的记录
        assert result["roe"] == 10.0


# ===========================================================================
# TestEnrichBarMetadataIntegration — 完整 bar metadata 注入流
# ===========================================================================


class TestEnrichBarMetadataIntegration:
    """测试 _enrich_bar_metadata 完整注入流（含多个数据源）"""

    def _build_fina_pit_map(self):
        """构建多股票 PIT 映射"""
        return {
            "000001.SZ": [
                _make_fina_record(date(2024, 4, 15), roe=12.0, eps=1.5,
                                  dt_netprofit_yoy=20.0, bps=8.5),
                _make_fina_record(date(2024, 8, 20), roe=14.0, eps=2.0,
                                  dt_netprofit_yoy=25.0, bps=9.0),
            ],
            "600036.SH": [
                _make_fina_record(date(2024, 3, 30), roe=18.0, eps=3.0,
                                  dt_netprofit_yoy=10.0, bps=15.0),
            ],
        }

    def test_all_fina_fields_injected(self):
        """所有 fina 字段通过 PIT 注入到 meta dict"""
        meta: dict = {}
        fina_pit_map = self._build_fina_pit_map()

        _enrich_bar_metadata(
            meta, "000001.SZ", date(2024, 6, 15),
            stock_basic_df=None, daily_basic_map={},
            fina_pit_map=fina_pit_map,
        )

        # fina PIT 注入（2024-06-15 在 Q1 报告(4/15)后 → roe=12.0）
        assert meta.get("roe") == 12.0
        assert meta.get("eps") == 1.5
        assert meta.get("dt_netprofit_yoy") == 20.0
        assert meta.get("bps") == 8.5

    def test_fina_updates_after_new_report(self):
        """新报告发布后自动切换到最新数据"""
        fina_pit_map = self._build_fina_pit_map()

        # 6 月 → Q1 数据
        meta1: dict = {}
        _enrich_bar_metadata(meta1, "000001.SZ", date(2024, 6, 15),
                             stock_basic_df=None, daily_basic_map={},
                             fina_pit_map=fina_pit_map)
        assert meta1.get("roe") == 12.0

        # 9 月 → Q2 数据（8月20日发布）
        meta2: dict = {}
        _enrich_bar_metadata(meta2, "000001.SZ", date(2024, 9, 10),
                             stock_basic_df=None, daily_basic_map={},
                             fina_pit_map=fina_pit_map)
        assert meta2.get("roe") == 14.0

    def test_no_fina_for_unlisted_symbol(self):
        """fina_pit_map 中无该标的 → 不注入财务数据"""
        fina_pit_map = self._build_fina_pit_map()
        meta: dict = {}
        _enrich_bar_metadata(meta, "999999.SZ", date(2024, 6, 15),
                             stock_basic_df=None, daily_basic_map={},
                             fina_pit_map=fina_pit_map)
        assert meta.get("roe") is None

    def test_fina_does_not_overwrite_existing_values(self):
        """meta 中已有 roe → fina 不覆盖（setdefault）"""
        fina_pit_map = self._build_fina_pit_map()
        meta: dict = {"roe": 99.0}
        _enrich_bar_metadata(meta, "000001.SZ", date(2024, 6, 15),
                             stock_basic_df=None, daily_basic_map={},
                             fina_pit_map=fina_pit_map)
        assert meta.get("roe") == 99.0


# ===========================================================================
# TestEndToEndPitStrategy — 策略层端到端 PIT 数据验证
# ===========================================================================


class TestEndToEndPitStrategy:
    """验证 PIT 财务数据正确地流入策略打分"""

    def test_pead_score_changes_with_pit_data(self):
        """有 PIT 数据 vs 无数据 → SUE 打分结果不同"""
        strategy = PEADStrategy(
            config=PEADConfig(
                top_k=5, buffer_k=0,
                weight_sue=0.5,
                weight_quality=0.3,
                weight_earnings_growth=0.2,
                weight_momentum=0.0,
                weight_volatility=0.0,
                min_sue=0.0, min_roe=0.0,
            )
        )
        strategy.on_start()
        ctx = _make_context()

        # 候选 1：有完整 PIT 数据
        c_rich = EquityCandidateInfo(
            symbol="RICH",
            close=20.0,
            metadata={"sue": 3.0, "roe": 15.0, "dt_netprofit_yoy": 30.0},
            circ_mv=500_000.0,
            turnover_rate=3.0,
            amount=5_000.0,
            list_date="20200101",
        )
        # 候选 2：无数据
        c_empty = EquityCandidateInfo(
            symbol="EMPTY",
            close=20.0,
            metadata={},
            circ_mv=500_000.0,
            turnover_rate=3.0,
            amount=5_000.0,
            list_date="20200101",
        )
        result = strategy._score_candidates([c_rich, c_empty], ctx)
        assert result[0] == "RICH"  # 有数据的排前

    def test_pit_data_prevents_forward_looking(self):
        """PIT 对齐防止前视偏差：6月不应看到 8 月发布的 Q2 数据"""
        fina_pit_map = {
            "000001.SZ": [
                _make_fina_record(date(2024, 4, 28), roe=10.0),  # Q1
                _make_fina_record(date(2024, 8, 25), roe=20.0),  # Q2
            ],
        }
        # 6月 → 只能看到 Q1
        meta_june: dict = {}
        _enrich_bar_metadata(meta_june, "000001.SZ", date(2024, 6, 15),
                             stock_basic_df=None, daily_basic_map={},
                             fina_pit_map=fina_pit_map)
        assert meta_june.get("roe") == 10.0

        # 9月 → 可以看到 Q2
        meta_sept: dict = {}
        _enrich_bar_metadata(meta_sept, "000001.SZ", date(2024, 9, 15),
                             stock_basic_df=None, daily_basic_map={},
                             fina_pit_map=fina_pit_map)
        assert meta_sept.get("roe") == 20.0

    def test_multiple_stocks_different_pit_timelines(self):
        """不同股票有不同的报告时间线，互不干扰"""
        fina_pit_map = {
            "A.SZ": [_make_fina_record(date(2024, 4, 1), roe=8.0)],
            "B.SZ": [_make_fina_record(date(2024, 6, 1), roe=15.0)],
        }

        # 5月：A 已发布报告，B 尚未发布
        meta_a: dict = {}
        _enrich_bar_metadata(meta_a, "A.SZ", date(2024, 5, 15),
                             stock_basic_df=None, daily_basic_map={},
                             fina_pit_map=fina_pit_map)
        assert meta_a.get("roe") == 8.0

        meta_b: dict = {}
        _enrich_bar_metadata(meta_b, "B.SZ", date(2024, 5, 15),
                             stock_basic_df=None, daily_basic_map={},
                             fina_pit_map=fina_pit_map)
        assert meta_b.get("roe") is None  # B 的报告尚未发布


# ===========================================================================
# TestPEADPostFixValidation — 验证 PEAD PIT 修复后的端到端行为
# ===========================================================================


class TestPEADPostFixValidation:
    """PEAD 策略 PIT 修复后的端到端验证"""

    def _make_candidates_with_pit(self):
        """构造带完整 PIT 财务数据的候选集"""
        return [
            EquityCandidateInfo(
                symbol="HIGH_SUE",
                close=25.0,
                metadata={"sue": 5.0, "roe": 18.0, "dt_netprofit_yoy": 40.0,
                          "eps": 2.5, "eps_yoy_prev": 1.5, "eps_std": 0.2},
                circ_mv=300_000.0,
                turnover_rate=4.0,
                amount=8_000.0,
                list_date="20190101",
            ),
            EquityCandidateInfo(
                symbol="MED_SUE",
                close=15.0,
                metadata={"sue": 2.0, "roe": 12.0, "dt_netprofit_yoy": 15.0},
                circ_mv=400_000.0,
                turnover_rate=3.0,
                amount=5_000.0,
                list_date="20190101",
            ),
            EquityCandidateInfo(
                symbol="NO_SUE",
                close=10.0,
                metadata={"roe": 8.0, "dt_netprofit_yoy": 5.0},
                circ_mv=500_000.0,
                turnover_rate=2.0,
                amount=3_000.0,
                list_date="20190101",
            ),
            EquityCandidateInfo(
                symbol="NEG_SUE",
                close=12.0,
                metadata={"sue": -1.0, "roe": 10.0, "dt_netprofit_yoy": -5.0},
                circ_mv=350_000.0,
                turnover_rate=3.5,
                amount=4_000.0,
                list_date="20190101",
            ),
        ]

    def test_sue_drives_ranking(self):
        """SUE 因子权重最大 (0.45)，高 SUE 标的应排在前面"""
        strategy = PEADStrategy(PEADConfig(min_sue=-999, min_roe=0))
        strategy.on_start()
        ctx = _make_context()
        cands = self._make_candidates_with_pit()
        result = strategy._score_candidates(cands, ctx)
        assert result[0] == "HIGH_SUE"
        assert result[1] == "MED_SUE"

    def test_extra_filter_blocks_low_roe(self):
        """低 ROE 标的应被 _extra_filter 拦截"""
        strategy = PEADStrategy(PEADConfig(min_roe=10.0))
        cand = EquityCandidateInfo(
            symbol="LOW_ROE",
            close=10.0,
            metadata={"roe": 5.0, "sue": 3.0},
            circ_mv=300_000.0,
            turnover_rate=3.0,
            amount=5_000.0,
            list_date="20190101",
        )
        ctx = _make_context()
        assert not strategy._extra_filter(cand, ctx)

    def test_extra_filter_blocks_negative_sue(self):
        """负 SUE 标的应被 _extra_filter 拦截（min_sue 默认 0）"""
        strategy = PEADStrategy(PEADConfig())
        cand = EquityCandidateInfo(
            symbol="NEG",
            close=10.0,
            metadata={"roe": 15.0, "sue": -0.5},
            circ_mv=300_000.0,
            turnover_rate=3.0,
            amount=5_000.0,
            list_date="20190101",
        )
        ctx = _make_context()
        assert not strategy._extra_filter(cand, ctx)

    def test_five_factor_all_contribute(self):
        """5 个因子 (SUE/growth/quality/momentum/volatility) 全部参与打分"""
        strategy = PEADStrategy(PEADConfig(min_sue=-999, min_roe=0))
        strategy.on_start()
        ctx = _make_context()
        cfg = strategy.config
        # 权重和应为 1.0
        total_weight = (cfg.weight_sue + cfg.weight_earnings_growth +
                        cfg.weight_quality + cfg.weight_momentum +
                        cfg.weight_volatility)
        assert abs(total_weight - 1.0) < 1e-6

    def test_pit_enriched_metadata_flows_to_scoring(self):
        """PIT 注入的 roe/eps/growth/sue 字段在 scoring 中实际被使用。

        ``sue`` 必须显式给出：生产路径上它由
        ``data_loader._compute_sue_fields``（data_loader.py:1474）写进同一个
        ``fina_row``，再被 ``_enrich_bar_metadata`` 整体拷进 metadata。
        本用例手工拼 fina_pit_map，跳过了那一步，所以要自己补上 ——
        否则测的是"没有 SUE 的 PEAD"，而 PEAD 现在会（正确地）拒绝打分。
        """
        fina_pit_map = {
            "STAR.SZ": [
                _make_fina_record(date(2024, 4, 28), roe=25.0, eps=3.0,
                                  dt_netprofit_yoy=50.0, sue=2.0),
            ],
        }
        meta: dict = {}
        _enrich_bar_metadata(
            meta, "STAR.SZ", date(2024, 6, 1),
            stock_basic_df=None, daily_basic_map={},
            fina_pit_map=fina_pit_map,
        )
        # 验证 PIT 注入的字段
        assert meta["roe"] == 25.0
        assert meta["eps"] == 3.0
        assert meta["dt_netprofit_yoy"] == 50.0
        assert meta["sue"] == 2.0

        # 这些字段会被 PEAD 打分使用
        strategy = PEADStrategy(PEADConfig(min_sue=-999, min_roe=0))
        strategy.on_start()
        cand = EquityCandidateInfo(
            symbol="STAR.SZ",
            close=20.0,
            metadata=meta,  # 使用 PIT 注入后的 metadata
            circ_mv=400_000.0,
            turnover_rate=3.0,
            amount=5_000.0,
            list_date="20190101",
        )
        ctx = _make_context()
        result = strategy._score_candidates([cand], ctx)
        assert len(result) == 1
        assert result[0] == "STAR.SZ"

        # 反过来：PIT 里没有 SUE 时，同一只票不进名单。
        # 这是 PEAD 的契约（见 tests/strategy/test_pead_event.py），
        # 在集成层再钉一遍，防止哪天 _enrich_bar_metadata 丢了 sue 字段
        # 却因为 roe/动量还在、名单照常产出而无人察觉。
        meta_no_sue = {k: v for k, v in meta.items() if k != "sue"}
        cand_no_sue = EquityCandidateInfo(
            symbol="STAR.SZ",
            close=20.0,
            metadata=meta_no_sue,
            circ_mv=400_000.0,
            turnover_rate=3.0,
            amount=5_000.0,
            list_date="20190101",
        )
        assert strategy._score_candidates([cand_no_sue], ctx) == []
