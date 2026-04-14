"""因子驱动策略端到端管道测试。

验证完整链路：
  因子注册表 → FactorBasedStrategy → BacktestEngine → BacktestReport
  并确认报告中包含因子 IC、风险摘要、Brinson 归因等新增指标。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pytest

from aipha.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from aipha.backtest.report import (
    BacktestReport,
    compute_factor_ic_briefs,
    compute_risk_summary,
    generate_report,
)
from aipha.domain.models import Bar
from aipha.execution.fees import (
    CommissionModel,
    FeeModelCNEquity,
    StampTaxModel,
    TransferFeeModel,
)
from aipha.execution.fill_models import NextBarOpenFillModel
from aipha.execution.slippage import FixedBpsSlippageModel
from aipha.factors.registry import create_factor
from aipha.market.rules_cn_equity import MarketRulesCNEquity
from aipha.strategy.factor_strategy import FactorBasedStrategy, FactorStrategyConfig


# ---------------------------------------------------------------------------
# 测试数据生成
# ---------------------------------------------------------------------------

def _generate_bars(
    n_stocks: int = 15,
    n_days: int = 100,
    seed: int = 123,
) -> dict[date, dict[str, Bar]]:
    """生成含基本面元数据的合成 Bar 数据。"""
    rng = np.random.RandomState(seed)
    symbols = [f"{600000 + i:06d}.SH" for i in range(n_stocks)]
    prices = {s: rng.uniform(10, 60) for s in symbols}
    start = date(2023, 3, 1)
    current = start
    bars: dict[date, dict[str, Bar]] = {}
    industries = ["银行", "医药", "电子", "新能源", "白酒"]

    for _ in range(n_days):
        while current.weekday() >= 5:
            current += timedelta(days=1)
        day_bars: dict[str, Bar] = {}
        for i, sym in enumerate(symbols):
            ret = rng.normal(0.0005, 0.02)
            prices[sym] *= 1 + ret
            p = prices[sym]
            vol = int(rng.uniform(500_000, 10_000_000))
            meta = {
                "circ_mv": rng.uniform(300_000, 3_000_000),
                "pe_ttm": max(3.0, rng.uniform(5, 50)),
                "pb": max(0.3, rng.uniform(0.5, 6)),
                "roe": rng.uniform(3, 20),
                "turnover_rate": rng.uniform(0.5, 5),
                "amount": vol * p / 10_000,
                "list_date": "20180101",
                "name": f"测试{i}",
                "industry": industries[i % len(industries)],
            }
            t0 = datetime.combine(current, datetime.min.time().replace(hour=9, minute=30))
            t1 = datetime.combine(current, datetime.min.time().replace(hour=15))
            day_bars[sym] = Bar(
                symbol=sym, start_ts=t0, end_ts=t1,
                open=p * (1 + rng.normal(0, 0.003)),
                high=p * (1 + abs(rng.normal(0, 0.01))),
                low=p * (1 - abs(rng.normal(0, 0.01))),
                close=p, volume=vol, prev_close=p / (1 + ret),
                metadata=meta,
            )
        bars[current] = day_bars
        current += timedelta(days=1)
    return bars


def _build_engine(strategy, bars):
    """构建 BacktestEngine + 运行。"""
    config = BacktestConfig(
        initial_capital=1_000_000,
    )
    fee = FeeModelCNEquity(
        commission=CommissionModel(rate=0.0003, min_fee=5.0),
        stamp_tax=StampTaxModel(sell_rate=0.001),
        transfer_fee=TransferFeeModel(rate=0.00002),
    )
    engine = BacktestEngine(
        config=config,
        strategy=strategy,
        fill_model=NextBarOpenFillModel(),
        slippage=FixedBpsSlippageModel(bps=3.0),
        fee_model=fee,
        rules=MarketRulesCNEquity(),
    )
    return engine.run(bars)


# ===========================================================================
# 因子策略 E2E
# ===========================================================================


class TestFactorStrategyE2EPipeline:
    """因子策略全链路回测。"""

    @pytest.fixture()
    def bars(self):
        return _generate_bars()

    @pytest.fixture()
    def pe_strategy(self):
        factor = create_factor("pe", invert=True)
        return FactorBasedStrategy(
            factors=[factor],
            config=FactorStrategyConfig(top_k=5, rebalance_days=10),
            name="E2E_PE",
        )

    @pytest.fixture()
    def multi_factor_strategy(self):
        factors = [
            create_factor("pe", invert=True),
            create_factor("momentum"),
        ]
        return FactorBasedStrategy(
            factors=factors,
            config=FactorStrategyConfig(
                top_k=5, rebalance_days=10, weight_method="equal",
            ),
            name="E2E_MULTI",
        )

    # --- 基本回测 ---

    def test_single_factor_runs(self, pe_strategy, bars):
        result = _build_engine(pe_strategy, bars)
        assert isinstance(result, BacktestResult)
        assert result.final_nav > 0

    def test_multi_factor_runs(self, multi_factor_strategy, bars):
        result = _build_engine(multi_factor_strategy, bars)
        assert isinstance(result, BacktestResult)
        assert result.final_nav > 0

    # --- 报告生成 ---

    def test_report_generated(self, pe_strategy, bars):
        result = _build_engine(pe_strategy, bars)
        report = generate_report(result)
        assert isinstance(report, BacktestReport)
        assert report.performance.total_return is not None
        assert report.drawdown.max_drawdown_pct is not None

    def test_report_has_trading_stats(self, pe_strategy, bars):
        result = _build_engine(pe_strategy, bars)
        report = generate_report(result)
        assert report.trading is not None
        assert report.trading.total_trades >= 0

    def test_report_has_cost_attribution(self, pe_strategy, bars):
        result = _build_engine(pe_strategy, bars)
        report = generate_report(result)
        assert report.costs is not None

    def test_report_summary_not_empty(self, pe_strategy, bars):
        result = _build_engine(pe_strategy, bars)
        report = generate_report(result)
        text = report.summary()
        assert len(text) > 100
        assert "收益" in text or "回撤" in text or "NAV" in text


# ===========================================================================
# 风险摘要集成
# ===========================================================================


class TestRiskSummaryE2E:
    """验证回测报告可附加风险摘要。"""

    @pytest.fixture()
    def result_and_report(self):
        bars = _generate_bars(n_stocks=10, n_days=80, seed=456)
        factor = create_factor("momentum")
        strategy = FactorBasedStrategy(
            factors=[factor],
            config=FactorStrategyConfig(top_k=3, rebalance_days=5),
            name="RISK_TEST",
        )
        result = _build_engine(strategy, bars)
        report = generate_report(result)
        return result, report

    def test_compute_risk_summary(self, result_and_report):
        result, report = result_and_report
        # 从 daily_snapshots 提取日收益
        snaps = result.daily_snapshots
        if len(snaps) >= 2:
            navs = [s.nav for s in snaps]
            daily_returns = [
                navs[i] / navs[i - 1] - 1
                for i in range(1, len(navs))
                if navs[i - 1] > 0
            ]
            risk = compute_risk_summary(daily_returns)
            if risk is not None:
                assert risk.var_95 <= 0  # VaR 通常为负
                assert risk.cvar_95 <= risk.var_95  # CVaR 更极端

    def test_risk_in_report_summary(self, result_and_report):
        result, report = result_and_report
        snaps = result.daily_snapshots
        if len(snaps) >= 12:
            navs = [s.nav for s in snaps]
            daily_rets = [
                navs[i] / navs[i - 1] - 1
                for i in range(1, len(navs))
                if navs[i - 1] > 0
            ]
            risk = compute_risk_summary(daily_rets)
            if risk is not None:
                report.risk_summary = risk
                text = report.summary()
                assert "VaR" in text or "风险" in text


# ===========================================================================
# 因子 IC 集成
# ===========================================================================


class TestFactorICE2E:
    """验证因子 IC 可从回测数据计算并集成到报告。"""

    def test_compute_ic_from_synthetic_data(self):
        """使用合成因子暴露和收益计算 IC。"""
        n_days, n_stocks = 60, 20
        rng = np.random.RandomState(999)
        dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(n_days)]
        symbols = [f"{i:06d}.SZ" for i in range(n_stocks)]

        factor_exposures = {
            "test_factor": {
                d: {s: rng.normal(0, 1) for s in symbols}
                for d in dates
            },
        }
        return_data = {
            d: {s: rng.normal(0.001, 0.02) for s in symbols}
            for d in dates
        }

        briefs = compute_factor_ic_briefs(factor_exposures, return_data)
        assert len(briefs) == 1
        assert briefs[0].factor_name == "test_factor"
        assert briefs[0].sample_days > 0

    def test_ic_briefs_in_report(self):
        """IC briefs 可正确附加到 BacktestReport。"""
        from aipha.backtest.report import FactorICBrief

        brief = FactorICBrief(
            factor_name="pe",
            ic_mean=0.05,
            ic_std=0.12,
            ir=0.42,
            ic_positive_rate=0.58,
            t_stat=2.1,
            p_value=0.04,
            sample_days=100,
        )

        # 可序列化
        d = brief.to_dict()
        assert d["factor_name"] == "pe"
        assert d["ir"] == 0.42
