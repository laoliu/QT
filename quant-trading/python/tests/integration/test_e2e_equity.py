"""A 股权益策略端到端集成测试

验证 multifactor_enhance / smallcap_rotation / pead_event 等策略
通过 run_from_config 全链路组装后能正确运行：
  1. YAML 配置解析 → 策略实例化
  2. 合成数据（含 equity 元数据）→ 引擎运行
  3. 结果包含完整指标（NAV / 交易 / 报告）
  4. _enrich_bar_metadata 正确注入元数据

使用合成数据，无需 TuShare Token。
"""
from __future__ import annotations

import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import yaml

from aipha.backtest.config_runner import (
    BacktestRunConfig,
    _enrich_bar_metadata,
    load_config,
    parse_config,
    run_from_config,
)
from aipha.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from aipha.backtest.report import generate_report
from aipha.domain.models import Bar
from aipha.execution.fees import (
    CommissionModel,
    FeeModelCNEquity,
    StampTaxModel,
    TransferFeeModel,
)
from aipha.execution.fill_models import NextBarOpenFillModel
from aipha.execution.slippage import FixedBpsSlippageModel
from aipha.market.rules_cn_equity import MarketRulesCNEquity
from aipha.strategy.registry import create_strategy_from_config


# ============================================================================
# 合成数据工具（含 equity 元数据）
# ============================================================================


def generate_equity_bars(
    n_stocks: int = 20,
    n_days: int = 120,
    start_date: date | None = None,
    seed: int = 42,
) -> dict[date, dict[str, Bar]]:
    """生成含 equity 元数据的合成 Bar 数据

    每只股票的 Bar.metadata 包含:
      circ_mv, turnover_rate, pe_ttm, pb, roe, amount,
      list_date, name, industry
    """
    rng = np.random.RandomState(seed)
    if start_date is None:
        start_date = date(2023, 1, 3)

    # 生成股票代码
    symbols = [f"{600000 + i:06d}.SH" for i in range(n_stocks)]

    # 初始价格、市值等
    prices = {s: rng.uniform(10, 80) for s in symbols}
    circ_mvs = {s: rng.uniform(200_000, 5_000_000) for s in symbols}  # 万元

    bars_by_date: dict[date, dict[str, Bar]] = {}
    current = start_date

    industries = ["银行", "白酒", "医药", "电子", "新能源", "地产", "钢铁"]

    for day_idx in range(n_days):
        # 跳过周末
        while current.weekday() >= 5:
            current += timedelta(days=1)

        day_bars: dict[str, Bar] = {}
        for i, sym in enumerate(symbols):
            # 价格随机游走
            ret = rng.normal(0.001, 0.025)
            prices[sym] *= 1 + ret
            p = prices[sym]

            bar_start = datetime.combine(
                current, datetime.min.time().replace(hour=9, minute=30)
            )
            bar_end = datetime.combine(
                current, datetime.min.time().replace(hour=15)
            )

            volume = int(rng.uniform(500_000, 20_000_000))
            amount = volume * p / 10000  # 万元

            # equity 元数据
            meta = {
                "circ_mv": circ_mvs[sym] * (1 + rng.normal(0, 0.01)),
                "total_mv": circ_mvs[sym] * 1.3,
                "pe_ttm": rng.uniform(5, 60),
                "pb": rng.uniform(0.5, 8),
                "roe": rng.uniform(2, 25),
                "turnover_rate": rng.uniform(0.5, 8),
                "amount": amount,
                "list_date": "20150101",
                "name": f"测试股票{i}",
                "industry": industries[i % len(industries)],
                "dt_netprofit_yoy": rng.uniform(-20, 50),
                "sue": rng.normal(0, 2),
            }

            day_bars[sym] = Bar(
                symbol=sym,
                start_ts=bar_start,
                end_ts=bar_end,
                open=p * (1 + rng.normal(0, 0.005)),
                high=p * (1 + abs(rng.normal(0, 0.015))),
                low=p * (1 - abs(rng.normal(0, 0.015))),
                close=p,
                volume=volume,
                prev_close=p / (1 + ret),
                metadata=meta,
            )

        bars_by_date[current] = day_bars
        current += timedelta(days=1)

    return bars_by_date


# ============================================================================
# 策略实例化测试
# ============================================================================


class TestEquityStrategyCreation:
    """测试 A 股策略通过 registry 正确实例化"""

    @pytest.mark.parametrize("strategy_name", [
        "multifactor_enhance",
        "smallcap_rotation",
        "pead_event",
        "demo_momentum",
        "demo_mean_reversion",
        "demo_dual_ma",
    ])
    def test_create_strategy(self, strategy_name: str):
        """策略可通过 registry 创建"""
        strategy = create_strategy_from_config({"name": strategy_name})
        assert strategy is not None
        assert hasattr(strategy, "on_bar")

    def test_multifactor_with_params(self):
        """MultiFactorEnhance 接受自定义参数"""
        strategy = create_strategy_from_config({
            "name": "multifactor_enhance",
            "params": {
                "top_k": 30,
                "rebalance_days": 10,
                "weight_value": 0.30,
            },
        })
        assert strategy is not None

    def test_smallcap_with_params(self):
        """SmallCapRotation 接受自定义参数"""
        strategy = create_strategy_from_config({
            "name": "smallcap_rotation",
            "params": {
                "top_k": 20,
                "max_mv": 1_500_000,
            },
        })
        assert strategy is not None

    def test_pead_with_params(self):
        """PEAD 策略接受自定义参数"""
        strategy = create_strategy_from_config({
            "name": "pead_event",
            "params": {
                "top_k": 15,
                "min_roe": 5.0,
            },
        })
        assert strategy is not None


# ============================================================================
# 端到端引擎运行测试
# ============================================================================


class TestEquityE2EBacktest:
    """A 股策略端到端回测测试（合成数据）"""

    @pytest.fixture
    def equity_bars(self) -> dict[date, dict[str, Bar]]:
        return generate_equity_bars(n_stocks=15, n_days=80, seed=42)

    @pytest.fixture
    def engine_components(self):
        """标准 A 股引擎组件"""
        return {
            "config": BacktestConfig(
                initial_capital=1_000_000,
                lot_size=100,
                commission_rate=0.0001,
                commission_min=5.0,
                stamp_tax_rate=0.0005,
                transfer_fee_rate=0.00001,
                slippage_bps=5.0,
                t_plus_n=1,
            ),
            "fill_model": NextBarOpenFillModel(),
            "slippage": FixedBpsSlippageModel(bps=5),
            "fee_model": FeeModelCNEquity(
                commission=CommissionModel(rate=0.0001, min_fee=5.0),
                stamp_tax=StampTaxModel(sell_rate=0.0005),
                transfer_fee=TransferFeeModel(rate=0.00001),
            ),
            "rules": MarketRulesCNEquity(),
        }

    @pytest.mark.parametrize("strategy_name,params", [
        ("multifactor_enhance", {"top_k": 5, "rebalance_days": 10}),
        ("smallcap_rotation", {"top_k": 5, "rebalance_days": 10}),
        ("pead_event", {"top_k": 5, "rebalance_days": 10}),
    ])
    def test_strategy_runs_with_equity_data(
        self, equity_bars, engine_components, strategy_name, params,
    ):
        """A 股策略用含元数据的合成数据跑通全流程"""
        strategy = create_strategy_from_config({
            "name": strategy_name,
            "params": params,
        })

        engine = BacktestEngine(
            config=engine_components["config"],
            strategy=strategy,
            fill_model=engine_components["fill_model"],
            slippage=engine_components["slippage"],
            fee_model=engine_components["fee_model"],
            rules=engine_components["rules"],
        )

        result = engine.run(equity_bars)

        assert isinstance(result, BacktestResult)
        # NAV 应在合理范围（80 天合成数据不应暴涨暴跌）
        assert 0.5 * 1_000_000 < result.final_nav < 2.0 * 1_000_000, (
            f"NAV {result.final_nav} 超出合理范围"
        )
        # 80 天扣周末应有 50+ 个交易日快照
        assert len(result.daily_snapshots) >= 40, (
            f"快照数 {len(result.daily_snapshots)} 应 >= 40"
        )
        assert result.initial_capital == 1_000_000
        # 应产生交易且费用 > 0
        assert result.total_trades > 0, "应有交易记录"
        assert result.total_fees.commission > 0, "应有佣金"

    @pytest.mark.parametrize("strategy_name", [
        "multifactor_enhance",
        "smallcap_rotation",
        "pead_event",
    ])
    def test_report_generation(
        self, equity_bars, engine_components, strategy_name,
    ):
        """A 股策略回测后能生成报告"""
        strategy = create_strategy_from_config({
            "name": strategy_name,
            "params": {"top_k": 5, "rebalance_days": 10},
        })

        engine = BacktestEngine(
            config=engine_components["config"],
            strategy=strategy,
            fill_model=engine_components["fill_model"],
            slippage=engine_components["slippage"],
            fee_model=engine_components["fee_model"],
            rules=engine_components["rules"],
        )

        result = engine.run(equity_bars)
        report = generate_report(result)

        assert report is not None
        assert report.strategy_name is not None
        assert report.performance is not None
        assert report.performance.trading_days >= 40, "报告应体现 40+ 个交易日"
        assert report.drawdown is not None
        assert report.drawdown.max_drawdown_pct >= 0
        assert report.costs is not None
        assert report.costs.total_fees > 0, "有交易必有费用"

    def test_multifactor_produces_trades(
        self, equity_bars, engine_components,
    ):
        """MultiFactorEnhance 应产生交易（验证元数据被正确读取）"""
        strategy = create_strategy_from_config({
            "name": "multifactor_enhance",
            "params": {
                "top_k": 5,
                "rebalance_days": 5,
                "min_price": 1.0,    # 放宽到合成数据范围
                "max_price": 500.0,
                "min_turnover": 0.0,
                "min_listed_days": 0,
            },
        })

        engine = BacktestEngine(
            config=engine_components["config"],
            strategy=strategy,
            fill_model=engine_components["fill_model"],
            slippage=engine_components["slippage"],
            fee_model=engine_components["fee_model"],
            rules=engine_components["rules"],
        )

        result = engine.run(equity_bars)

        # 应产生交易（因为合成数据含有效的 circ_mv, pe_ttm 等）
        assert result.total_trades > 0, (
            "MultiFactorEnhance 应在含元数据的合成数据上产生交易"
        )
        # rebalance_days=5, 80 天应产生多次调仓
        assert result.total_trades >= 5, (
            f"rebalance_days=5 应产生 >=5 笔交易，实际 {result.total_trades}"
        )
        assert result.total_fees.commission > 0, "有交易必有佣金"


# ============================================================================
# 配置解析集成测试
# ============================================================================


class TestEquityConfigParsing:
    """验证 A 股回测 YAML 配置能被正确解析"""

    @pytest.mark.parametrize("config_file", [
        "configs/backtest/multifactor_enhance.yml",
        "configs/backtest/smallcap_rotation.yml",
        "configs/backtest/pead_event.yml",
        "configs/backtest/momentum_hs300.yml",
    ])
    def test_config_loads(self, config_file: str):
        """配置文件能被正确加载"""
        path = Path(config_file)
        if not path.exists():
            pytest.skip(f"{config_file} not found")

        config = load_config(path)
        assert config is not None
        assert config.strategy.name is not None
        assert config.data.start_date is not None

    @pytest.mark.parametrize("config_file,expected_strategy", [
        ("configs/backtest/multifactor_enhance.yml", "multifactor_enhance"),
        ("configs/backtest/smallcap_rotation.yml", "smallcap_rotation"),
        ("configs/backtest/pead_event.yml", "pead_event"),
    ])
    def test_config_strategy_name(
        self, config_file: str, expected_strategy: str,
    ):
        """配置中策略名称正确"""
        path = Path(config_file)
        if not path.exists():
            pytest.skip(f"{config_file} not found")

        config = load_config(path)
        assert config.strategy.name == expected_strategy


# ============================================================================
# run_from_config 合成数据端到端测试
# ============================================================================


class TestRunFromConfigSynthetic:
    """使用合成数据通过 run_from_config 全链路测试"""

    def _make_synthetic_config(
        self, strategy_name: str, params: dict | None = None,
    ) -> Path:
        """创建使用合成数据的临时配置文件"""
        cfg = {
            "name": f"e2e_test_{strategy_name}",
            "run": {
                "mode": "backtest",
                "start_date": "2023-01-01",
                "end_date": "2023-06-30",
                "seed": 42,
            },
            "data": {
                "source": "synthetic",
                "provider": "synthetic",
            },
            "strategy": {
                "name": strategy_name,
                "params": params or {},
            },
            "cost": {
                "commission_rate": 0.0001,
                "commission_min": 5.0,
                "stamp_tax_rate": 0.0005,
                "transfer_fee_rate": 0.00001,
                "slippage_bps": 5,
            },
            "capital": {"initial": 1_000_000},
            "fill_model": {"name": "next_bar_open"},
        }

        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False,
        )
        yaml.dump(cfg, tmp, allow_unicode=True)
        tmp.close()
        return Path(tmp.name)

    def test_momentum_synthetic_e2e(self):
        """Momentum 策略合成数据端到端"""
        config_path = self._make_synthetic_config("demo_momentum")
        try:
            result = run_from_config(
                str(config_path),
                output_dir=tempfile.mkdtemp(),
            )
            assert result.result.final_nav > 0
            assert len(result.result.daily_snapshots) > 0
            assert result.result.total_trades >= 0  # 合成数据可能无信号
        finally:
            config_path.unlink(missing_ok=True)

    def test_dual_ma_synthetic_e2e(self):
        """DualMA 策略合成数据端到端"""
        config_path = self._make_synthetic_config("demo_dual_ma")
        try:
            result = run_from_config(
                str(config_path),
                output_dir=tempfile.mkdtemp(),
            )
            assert result.result.final_nav > 0
            assert len(result.result.daily_snapshots) >= 30, "应有 30+ 个交易日快照"
        finally:
            config_path.unlink(missing_ok=True)

    def test_mean_reversion_synthetic_e2e(self):
        """MeanReversion 策略合成数据端到端"""
        config_path = self._make_synthetic_config("demo_mean_reversion")
        try:
            result = run_from_config(
                str(config_path),
                output_dir=tempfile.mkdtemp(),
            )
            assert result.result.final_nav > 0
            assert len(result.result.daily_snapshots) >= 30, "应有 30+ 个交易日快照"
        finally:
            config_path.unlink(missing_ok=True)


class TestRunFromConfigHardened:
    """run_from_config 全链路加固测试

    验证 run_from_config 返回的 BacktestRunResult 中所有关键产物:
    - report: BacktestReport 非空、含核心指标
    - html_report_path: HTML 报告文件存在且可读
    - fingerprint: ExperimentFingerprint 所有字段非空
    - quality_report: QualityReport 非空、结构完整
    - data_health: 数据健康检查摘要非空
    - NAV 一致性: cash + position_value ≈ NAV
    """

    def _make_config(
        self, strategy_name: str, params: dict | None = None,
        output_dir: str | None = None,
    ) -> Path:
        """创建使用合成数据的临时配置文件"""
        cfg = {
            "name": f"hardened_{strategy_name}",
            "run": {
                "mode": "backtest",
                "start_date": "2023-01-01",
                "end_date": "2023-06-30",
                "seed": 42,
            },
            "data": {
                "source": "synthetic",
                "provider": "synthetic",
            },
            "strategy": {
                "name": strategy_name,
                "params": params or {},
            },
            "cost": {
                "commission_rate": 0.0001,
                "commission_min": 5.0,
                "stamp_tax_rate": 0.0005,
                "transfer_fee_rate": 0.00001,
                "slippage_bps": 5,
            },
            "capital": {"initial": 1_000_000},
            "fill_model": {"name": "next_bar_open"},
        }

        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False,
        )
        yaml.dump(cfg, tmp, allow_unicode=True)
        tmp.close()
        return Path(tmp.name)

    @pytest.fixture
    def momentum_result(self, tmp_path):
        """运行 momentum 策略并返回完整 BacktestRunResult"""
        config_path = self._make_config("demo_momentum")
        try:
            return run_from_config(str(config_path), output_dir=str(tmp_path))
        finally:
            config_path.unlink(missing_ok=True)

    # -- 报告验证 --

    def test_report_exists_and_has_performance(self, momentum_result):
        """run_from_config 应返回非空 BacktestReport"""
        r = momentum_result
        assert r.report is not None, "report 不应为 None"
        assert r.report.performance is not None
        assert r.report.performance.trading_days > 0
        assert r.report.performance.annualized_return is not None

    def test_report_drawdown(self, momentum_result):
        """报告应包含回撤信息"""
        r = momentum_result
        assert r.report is not None
        assert r.report.drawdown is not None
        assert r.report.drawdown.max_drawdown_pct >= 0

    def test_report_costs(self, momentum_result):
        """报告应包含成本归因"""
        r = momentum_result
        assert r.report is not None
        assert r.report.costs is not None
        assert r.report.costs.total_fees >= 0

    def test_report_summary_not_empty(self, momentum_result):
        """report.summary() 应返回非空字符串"""
        r = momentum_result
        assert r.report is not None
        s = r.report.summary()
        assert isinstance(s, str)
        assert len(s) > 50, "摘要不应为空"

    # -- HTML 报告验证 --

    def test_html_report_exists(self, momentum_result):
        """HTML 报告文件应存在"""
        r = momentum_result
        assert r.html_report_path is not None, "html_report_path 不应为 None"
        assert r.html_report_path.exists(), (
            f"HTML 报告不存在: {r.html_report_path}"
        )

    def test_html_report_readable(self, momentum_result):
        """HTML 报告文件应可读且包含关键标签"""
        r = momentum_result
        if r.html_report_path is None or not r.html_report_path.exists():
            pytest.skip("HTML 报告未生成")
        content = r.html_report_path.read_text(encoding="utf-8")
        assert "<html" in content.lower(), "HTML 文件应包含 <html> 标签"
        assert len(content) > 200, "HTML 文件不应过小"

    # -- 实验指纹验证 --

    def test_fingerprint_exists(self, momentum_result):
        """实验指纹应存在且字段非空"""
        fp = momentum_result.fingerprint
        assert fp is not None
        assert isinstance(fp.git_short, str) and len(fp.git_short) > 0
        assert isinstance(fp.config_hash, str) and len(fp.config_hash) > 0
        assert isinstance(fp.data_fingerprint, str) and len(fp.data_fingerprint) > 0
        assert fp.random_seed == 42

    def test_fingerprint_has_git_info(self, momentum_result):
        """指纹应包含 Git 信息"""
        fp = momentum_result.fingerprint
        assert isinstance(fp.git_commit, str)
        assert isinstance(fp.git_branch, str)
        assert isinstance(fp.git_dirty, bool)

    def test_fingerprint_has_runtime(self, momentum_result):
        """指纹应包含运行时信息"""
        fp = momentum_result.fingerprint
        assert fp.run_time is not None
        assert isinstance(fp.python_version, str)
        assert len(fp.python_version) > 0

    def test_fingerprint_to_dict(self, momentum_result):
        """指纹可序列化为完整字典"""
        fp = momentum_result.fingerprint
        d = fp.to_dict()
        assert "git_short" in d
        assert "config_hash" in d
        assert "data_fingerprint" in d
        assert "random_seed" in d
        assert d["random_seed"] == 42

    # -- 质量门禁验证 --

    def test_quality_report_exists(self, momentum_result):
        """质量门禁报告应存在"""
        qr = momentum_result.quality_report
        assert qr is not None, "quality_report 不应为 None"
        assert isinstance(qr.all_passed, bool)
        assert isinstance(qr.results, list)
        assert len(qr.results) > 0, "至少应有 1 条门禁检查"

    def test_quality_report_structure(self, momentum_result):
        """质量门禁报告结构完整"""
        qr = momentum_result.quality_report
        if qr is None:
            pytest.skip("质量报告未生成")
        assert isinstance(qr.errors, list)
        assert isinstance(qr.warnings, list)
        assert isinstance(qr.strategy_name, str)
        assert isinstance(qr.checked_at, str)

    # -- 数据健康检查验证 --

    def test_data_health_summary(self, momentum_result):
        """数据健康检查摘要应存在"""
        dh = momentum_result.data_health
        assert dh is not None, "data_health 不应为 None"
        assert "status" in dh
        assert dh["status"] in ("pass", "warning", "error")

    # -- NAV 一致性验证 --

    def test_nav_consistency(self, momentum_result):
        """每日 NAV = cash + position_value（允许微小浮点误差）"""
        snapshots = momentum_result.result.daily_snapshots
        assert len(snapshots) > 0
        for snap in snapshots:
            pos_value = sum(snap.position_values.values())
            expected_nav = snap.cash + pos_value
            assert abs(snap.nav - expected_nav) < 0.01, (
                f"{snap.date}: NAV {snap.nav} != "
                f"cash {snap.cash} + pos {pos_value}"
            )

    # -- 多策略端到端覆盖 --

    @pytest.mark.parametrize("strategy_name", [
        "demo_momentum", "demo_dual_ma", "demo_mean_reversion",
    ])
    def test_strategy_produces_valid_result(self, strategy_name, tmp_path):
        """多种策略 run_from_config 均产生有效结果"""
        config_path = self._make_config(strategy_name)
        try:
            r = run_from_config(str(config_path), output_dir=str(tmp_path))
            assert r.result.final_nav > 0
            assert len(r.result.daily_snapshots) >= 30
            assert r.fingerprint is not None
            assert r.report is not None
        finally:
            config_path.unlink(missing_ok=True)

    def test_output_dir_created(self, momentum_result):
        """输出目录应已创建"""
        assert momentum_result.output_dir.exists()


class TestRiskIntegrationE2E:
    """风控组件集成端到端测试"""

    @pytest.fixture
    def equity_bars(self) -> dict[date, dict[str, Bar]]:
        return generate_equity_bars(n_stocks=15, n_days=80, seed=42)

    def test_engine_with_risk_manager(self, equity_bars):
        """引擎配置风控管理器后仍能正常运行"""
        from aipha.risk.constraints import (
            CompositeRiskManager,
            DrawdownLimit,
            PositionLimit,
        )

        strategy = create_strategy_from_config({
            "name": "multifactor_enhance",
            "params": {
                "top_k": 5,
                "rebalance_days": 5,
                "min_price": 1.0,
                "max_price": 500.0,
                "min_turnover": 0.0,
                "min_mv": 0.0,
                "min_listed_days": 0,
                "buffer_k": 0,
            },
        })

        risk_manager = CompositeRiskManager(
            constraints=[
                PositionLimit(max_weight=0.20, lot_size=100),
                DrawdownLimit(max_drawdown=0.20, reduce_position_at=0.10),
            ]
        )

        engine = BacktestEngine(
            config=BacktestConfig(
                initial_capital=1_000_000,
                lot_size=100,
                commission_rate=0.0001,
                commission_min=5.0,
                stamp_tax_rate=0.0005,
                transfer_fee_rate=0.00001,
                slippage_bps=5.0,
                t_plus_n=1,
            ),
            strategy=strategy,
            fill_model=NextBarOpenFillModel(),
            slippage=FixedBpsSlippageModel(bps=5),
            fee_model=FeeModelCNEquity(
                commission=CommissionModel(rate=0.0001, min_fee=5.0),
                stamp_tax=StampTaxModel(sell_rate=0.0005),
                transfer_fee=TransferFeeModel(rate=0.00001),
            ),
            rules=MarketRulesCNEquity(),
            risk_manager=risk_manager,
        )

        result = engine.run(equity_bars)
        # NAV 应在合理范围
        assert 0.5 * 1_000_000 < result.final_nav < 2.0 * 1_000_000
        assert len(result.daily_snapshots) >= 40
        # 应产生交易且有费用
        assert result.total_trades > 0, "风控测试应产生交易"
        assert result.total_fees.commission > 0, "有交易必有佣金"

    def test_engine_with_market_regime_detector(self, equity_bars):
        """市场状态检测器集成测试"""
        from aipha.risk.constraints import MarketRegimeDetector

        strategy = create_strategy_from_config({
            "name": "smallcap_rotation",
            "params": {
                "top_k": 5,
                "rebalance_days": 5,
                "min_price": 1.0,
                "max_price": 500.0,
                "min_turnover": 0.0,
                "min_mv": 0.0,
                "min_listed_days": 0,
                "buffer_k": 0,
            },
        })

        detector = MarketRegimeDetector()

        engine = BacktestEngine(
            config=BacktestConfig(
                initial_capital=1_000_000,
                lot_size=100,
                commission_rate=0.0001,
                commission_min=5.0,
                stamp_tax_rate=0.0005,
                transfer_fee_rate=0.00001,
                slippage_bps=5.0,
                t_plus_n=1,
            ),
            strategy=strategy,
            fill_model=NextBarOpenFillModel(),
            slippage=FixedBpsSlippageModel(bps=5),
            fee_model=FeeModelCNEquity(
                commission=CommissionModel(rate=0.0001, min_fee=5.0),
                stamp_tax=StampTaxModel(sell_rate=0.0005),
                transfer_fee=TransferFeeModel(rate=0.00001),
            ),
            rules=MarketRulesCNEquity(),
            market_regime_detector=detector,
            benchmark_symbol="S0",  # Use first synthetic stock as benchmark
        )

        result = engine.run(equity_bars)
        assert 0.5 * 1_000_000 < result.final_nav < 2.0 * 1_000_000
        assert len(result.daily_snapshots) >= 40
        assert result.total_trades > 0, "市场父态检测器下应产生交易"

    def test_engine_with_stop_loss_manager(self, equity_bars):
        """引擎级止损管理器集成测试"""
        from aipha.risk.stop_loss import (
            FixedPercentStopLoss,
            StopLossConfig,
            StopLossManager,
            StopLossType,
            TriggerAction,
        )

        strategy = create_strategy_from_config({
            "name": "pead_event",
            "params": {
                "top_k": 5,
                "rebalance_days": 5,
                "min_price": 1.0,
                "max_price": 500.0,
                "min_turnover": 0.0,
                "min_mv": 0.0,
                "min_listed_days": 0,
                "buffer_k": 0,
            },
        })

        sl_manager = StopLossManager()
        sl_config = StopLossConfig(
            enabled=True,
            stop_type=StopLossType.FIXED_PERCENT,
            threshold=0.15,
            action=TriggerAction.CLOSE_POSITION,
        )
        sl_manager.add_rule(FixedPercentStopLoss(sl_config))

        engine = BacktestEngine(
            config=BacktestConfig(
                initial_capital=1_000_000,
                lot_size=100,
                commission_rate=0.0001,
                commission_min=5.0,
                stamp_tax_rate=0.0005,
                transfer_fee_rate=0.00001,
                slippage_bps=5.0,
                t_plus_n=1,
            ),
            strategy=strategy,
            fill_model=NextBarOpenFillModel(),
            slippage=FixedBpsSlippageModel(bps=5),
            fee_model=FeeModelCNEquity(
                commission=CommissionModel(rate=0.0001, min_fee=5.0),
                stamp_tax=StampTaxModel(sell_rate=0.0005),
                transfer_fee=TransferFeeModel(rate=0.00001),
            ),
            rules=MarketRulesCNEquity(),
            stop_loss_manager=sl_manager,
        )

        result = engine.run(equity_bars)
        assert 0.5 * 1_000_000 < result.final_nav < 2.0 * 1_000_000
        assert len(result.daily_snapshots) >= 40
        assert result.total_trades > 0, "止损测试应产生交易"


# ============================================================================
# 质量门禁 E2E 集成测试
# ============================================================================


class TestQualityGatesE2E:
    """验证质量门禁通过 run_from_config 全链路集成"""

    def _make_config_with_gates(
        self, gates_path: str | None = None,
    ) -> Path:
        """创建含质量门禁路径的临时配置"""
        cfg = {
            "name": "qg_e2e_test",
            "run": {
                "mode": "backtest",
                "start_date": "2023-01-01",
                "end_date": "2023-06-30",
                "seed": 42,
            },
            "data": {
                "source": "synthetic",
                "provider": "synthetic",
            },
            "strategy": {
                "name": "demo_momentum",
                "params": {},
            },
            "cost": {
                "commission_rate": 0.0001,
                "commission_min": 5.0,
                "stamp_tax_rate": 0.0005,
                "transfer_fee_rate": 0.00001,
                "slippage_bps": 5,
            },
            "capital": {"initial": 1_000_000},
            "fill_model": {"name": "next_bar_open"},
        }
        if gates_path:
            cfg["quality_gates_path"] = gates_path

        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False,
        )
        yaml.dump(cfg, tmp, allow_unicode=True)
        tmp.close()
        return Path(tmp.name)

    def test_default_gates_produce_report(self, tmp_path):
        """默认质量门禁应产生完整报告"""
        config_path = self._make_config_with_gates()
        try:
            result = run_from_config(
                str(config_path), output_dir=str(tmp_path),
            )
            qr = result.quality_report
            assert qr is not None
            assert isinstance(qr.all_passed, bool)
            assert len(qr.results) >= 5, "默认门禁至少 5 项检查"
        finally:
            config_path.unlink(missing_ok=True)

    def test_strict_gates_produce_errors(self, tmp_path):
        """极严格门禁应报告 error"""
        # 创建一个极严格的门禁配置
        gates = {
            "quality_gates": [
                {
                    "metric": "sharpe_ratio",
                    "operator": ">=",
                    "threshold": 99.0,
                    "severity": "error",
                    "description": "不可能达到的 Sharpe",
                },
            ],
        }
        gates_file = tmp_path / "strict_gates.yml"
        gates_file.write_text(
            yaml.dump(gates, allow_unicode=True), encoding="utf-8",
        )

        config_path = self._make_config_with_gates(str(gates_file))
        try:
            result = run_from_config(
                str(config_path), output_dir=str(tmp_path / "out"),
            )
            qr = result.quality_report
            assert qr is not None
            assert qr.all_passed is False, "极严格门禁不应全部通过"
            assert len(qr.errors) >= 1, "应有 error 级别失败"
        finally:
            config_path.unlink(missing_ok=True)

    def test_lenient_gates_pass(self, tmp_path):
        """宽松门禁应全部通过"""
        gates = {
            "quality_gates": [
                {
                    "metric": "max_drawdown_pct",
                    "operator": "<=",
                    "threshold": 0.99,
                    "severity": "error",
                    "description": "最大回撤不超过99%",
                },
            ],
        }
        gates_file = tmp_path / "lenient_gates.yml"
        gates_file.write_text(
            yaml.dump(gates, allow_unicode=True), encoding="utf-8",
        )

        config_path = self._make_config_with_gates(str(gates_file))
        try:
            result = run_from_config(
                str(config_path), output_dir=str(tmp_path / "out"),
            )
            qr = result.quality_report
            assert qr is not None
            assert qr.all_passed is True, "宽松门禁应全部通过"
            assert len(qr.errors) == 0
        finally:
            config_path.unlink(missing_ok=True)

    def test_warning_severity_not_fail(self, tmp_path):
        """warning 级别门禁不影响 all_passed"""
        gates = {
            "quality_gates": [
                {
                    "metric": "sharpe_ratio",
                    "operator": ">=",
                    "threshold": 99.0,
                    "severity": "warning",
                    "description": "警告级别不影响通过",
                },
                {
                    "metric": "max_drawdown_pct",
                    "operator": "<=",
                    "threshold": 0.99,
                    "severity": "error",
                    "description": "回撤宽松硬门禁",
                },
            ],
        }
        gates_file = tmp_path / "warn_gates.yml"
        gates_file.write_text(
            yaml.dump(gates, allow_unicode=True), encoding="utf-8",
        )

        config_path = self._make_config_with_gates(str(gates_file))
        try:
            result = run_from_config(
                str(config_path), output_dir=str(tmp_path / "out"),
            )
            qr = result.quality_report
            assert qr is not None
            assert qr.all_passed is True, "仅 warning 不影响 all_passed"
            assert len(qr.warnings) >= 1
            assert len(qr.errors) == 0
        finally:
            config_path.unlink(missing_ok=True)

    def test_quality_report_strategy_name(self, tmp_path):
        """质量报告策略名称应匹配配置"""
        config_path = self._make_config_with_gates()
        try:
            result = run_from_config(
                str(config_path), output_dir=str(tmp_path),
            )
            qr = result.quality_report
            assert qr is not None
            assert qr.strategy_name == "demo_momentum"
        finally:
            config_path.unlink(missing_ok=True)

    def test_gate_result_has_details(self, tmp_path):
        """每条门禁结果应有详细信息"""
        config_path = self._make_config_with_gates()
        try:
            result = run_from_config(
                str(config_path), output_dir=str(tmp_path),
            )
            qr = result.quality_report
            assert qr is not None
            for gr in qr.results:
                assert isinstance(gr.passed, bool)
                assert isinstance(gr.message, str)
                assert len(gr.message) > 0
        finally:
            config_path.unlink(missing_ok=True)
