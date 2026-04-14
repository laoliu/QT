"""§13 架构守护测试

扫描式自动检测违反架构约束的问题，从机制上杜绝以下类别的 bug 再次发生：
- §13.1 资产类型预设——单一来源机制（MarketRules 默认值一致性）
- §13.2 单一实现原则（禁止重复实现 Sharpe/rank_normalize 等）
- §5.2.1 费率参数三层覆盖（YAML 配置与权威源对齐）
- §4.3 回测-仿真一致性契约（滑点/费率默认值一致）

每个测试失败即意味着发现了违反架构约束的问题，必须修复后方可合并。
"""

from __future__ import annotations

import ast
import glob
import importlib
import os
import re
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src" / "aipha"
CONFIGS_ROOT = PROJECT_ROOT / "configs"


# ── §13.1 MarketRules 默认值一致性 ──────────────────────────────


class TestMarketRulesConsistency:
    """验证所有配置层的默认值与 MarketRules 权威源一致"""

    def test_cost_config_matches_cn_equity(self):
        """CostConfig 默认值必须与 MarketRulesCNEquity 一致"""
        from aipha.backtest.config_models import CostConfig
        from aipha.market.rules_cn_equity import MarketRulesCNEquity

        rules = MarketRulesCNEquity()
        cfg = CostConfig()

        assert cfg.commission_rate == float(rules.commission_rate), (
            f"CostConfig.commission_rate={cfg.commission_rate} "
            f"!= MarketRulesCNEquity={float(rules.commission_rate)}"
        )
        assert cfg.commission_min == float(rules.commission_min), (
            f"CostConfig.commission_min={cfg.commission_min} "
            f"!= MarketRulesCNEquity={float(rules.commission_min)}"
        )
        assert cfg.stamp_tax_rate == float(rules.stamp_tax_rate), (
            f"CostConfig.stamp_tax_rate={cfg.stamp_tax_rate} "
            f"!= MarketRulesCNEquity={float(rules.stamp_tax_rate)}"
        )
        assert cfg.transfer_fee_rate == float(rules.transfer_fee_rate), (
            f"CostConfig.transfer_fee_rate={cfg.transfer_fee_rate} "
            f"!= MarketRulesCNEquity={float(rules.transfer_fee_rate)}"
        )

    def test_backtest_config_matches_cn_equity(self):
        """BacktestConfig 默认值必须与 MarketRulesCNEquity 一致"""
        from aipha.backtest.engine import BacktestConfig
        from aipha.market.rules_cn_equity import MarketRulesCNEquity

        rules = MarketRulesCNEquity()
        cfg = BacktestConfig()

        assert cfg.commission_rate == float(rules.commission_rate)
        assert cfg.commission_min == float(rules.commission_min)
        assert cfg.stamp_tax_rate == float(rules.stamp_tax_rate)
        assert cfg.transfer_fee_rate == float(rules.transfer_fee_rate)
        assert cfg.lot_size == rules.lot_size
        assert cfg.t_plus_n == rules.t_plus

    def test_paper_engine_matches_cb_rules(self):
        """PaperEngineConfig 默认值必须与 MarketRulesConvertibleBond 一致"""
        from aipha.market.rules_convertible_bond import (
            MarketRulesConvertibleBond,
        )
        from aipha.runner.paper_engine import PaperEngineConfig

        rules = MarketRulesConvertibleBond()
        cfg = PaperEngineConfig()

        assert cfg.commission_rate == float(rules.commission_rate), (
            f"PaperEngineConfig.commission_rate={cfg.commission_rate} "
            f"!= CB authority={float(rules.commission_rate)}"
        )
        assert cfg.commission_min == float(rules.commission_min), (
            f"PaperEngineConfig.commission_min={cfg.commission_min} "
            f"!= CB authority={float(rules.commission_min)}"
        )

    def test_paper_gateway_cb_matches_rules(self):
        """PaperTradingGateway.from_cb_mode 默认值必须与 CB 权威源一致"""
        import inspect

        from aipha.execution.paper_gateway import PaperTradingGateway
        from aipha.market.rules_convertible_bond import (
            MarketRulesConvertibleBond,
        )

        rules = MarketRulesConvertibleBond()
        sig = inspect.signature(PaperTradingGateway.from_cb_mode)

        cr = sig.parameters["commission_rate"].default
        cm = sig.parameters["commission_min"].default

        assert cr == float(rules.commission_rate), (
            f"from_cb_mode commission_rate={cr} "
            f"!= CB authority={float(rules.commission_rate)}"
        )
        assert cm == float(rules.commission_min), (
            f"from_cb_mode commission_min={cm} "
            f"!= CB authority={float(rules.commission_min)}"
        )

    def test_market_rules_have_slippage_bps_default(self):
        """§13.1: MarketRules 必须包含 slippage_bps_default 权威字段"""
        from aipha.market.rules_cn_equity import MarketRulesCNEquity
        from aipha.market.rules_convertible_bond import (
            MarketRulesConvertibleBond,
        )

        cn = MarketRulesCNEquity()
        cb = MarketRulesConvertibleBond()

        assert hasattr(cn, "slippage_bps_default"), (
            "MarketRulesCNEquity 缺少 slippage_bps_default 字段"
        )
        assert hasattr(cb, "slippage_bps_default"), (
            "MarketRulesConvertibleBond 缺少 slippage_bps_default 字段"
        )
        assert cn.slippage_bps_default == 10.0, (
            f"A股滑点权威值应为 10.0，实际为 {cn.slippage_bps_default}"
        )
        assert cb.slippage_bps_default == 5.0, (
            f"CB滑点权威值应为 5.0，实际为 {cb.slippage_bps_default}"
        )

    def test_runner_config_has_transfer_fee_rate(self):
        """§13.1: RunnerConfig/PortfolioConfig 必须包含 transfer_fee_rate"""
        from aipha.runner.live_runner import RunnerConfig
        from aipha.runner.multi_strategy import PortfolioConfig

        rc = RunnerConfig()
        pc = PortfolioConfig()

        assert hasattr(rc, "transfer_fee_rate"), (
            "RunnerConfig 缺少 transfer_fee_rate 字段"
        )
        assert hasattr(pc, "transfer_fee_rate"), (
            "PortfolioConfig 缺少 transfer_fee_rate 字段"
        )
        assert rc.transfer_fee_rate == Decimal("0.00001")
        assert pc.transfer_fee_rate == Decimal("0.00001")

    def test_order_validator_default_commission_rate(self):
        """§13.1: order_validator 默认佣金率应为 A 股权威值 0.0000854（万0.854）"""
        import inspect

        from aipha.market.order_validator import OrderValidator

        sig = inspect.signature(OrderValidator.validate_buy_funds)
        cr = sig.parameters["commission_rate"].default
        assert cr == Decimal("0.0000854"), (
            f"validate_buy_funds commission_rate 默认值"
            f"={cr}，应为 A 股权威值 0.0000854"
        )

    def test_multi_strategy_yaml_fallback_matches_cb(self):
        """multi_strategy create_from_yaml CB fallback commission_min 必须为 5"""
        import inspect

        from aipha.runner.multi_strategy import MultiStrategyRunner

        # 读取源码检查 commission_min fallback
        source = inspect.getsource(MultiStrategyRunner.create_from_yaml)
        # 确保 commission_min fallback 为 "5"（CB 最低佣金5元）
        assert '"commission_min", "5")' in source, (
            "create_from_yaml commission_min fallback 应为 5（CB 最低佣金5元）"
        )


# ── §13.2 单一实现原则 ─────────────────────────────────────────


class TestSingleImplementation:
    """扫描代码确保无重复实现"""

    def test_no_duplicate_rank_normalize(self):
        """除允许位置外，不得存在 def rank_normalize 定义（§13.2）"""
        # 允许位置
        allowed = {
            "rank_utils.py",         # §13.2 唯一共享实现
            "equity_rotation_base.py",  # A 股轮动专用（不同 API）
        }
        violations = []
        for py in SRC_ROOT.rglob("*.py"):
            if py.name in allowed:
                continue
            try:
                text = py.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            # 查找 def rank_normalize 定义
            for m in re.finditer(
                r"^\s*def\s+(rank_normalize\w*)\s*\(",
                text,
                re.MULTILINE,
            ):
                rel = py.relative_to(PROJECT_ROOT)
                violations.append(f"{rel}: {m.group(1)}")

        assert not violations, (
            f"§13.2 违规：以下文件包含 rank_normalize 重复实现：\n"
            + "\n".join(violations)
        )

    def test_sharpe_no_inline_sqrt252(self):
        """检测绕过 metrics.py 的内联 Sharpe 计算（§13.2）"""
        # 允许位置：metrics.py 自身、vectorized.py（薄封装）
        # technical.py: 年化波动率计算（std*sqrt(252)），非 Sharpe
        allowed = {"metrics.py", "vectorized.py", "test_vectorized.py",
                   "test_metrics.py", "test_architectural_guards.py",
                   "technical.py"}
        # 扫描模式: .std() 或 np.std 附近出现 sqrt(252) 或 sqrt(trading_days)
        pattern = re.compile(
            r"\.std\(.*\).*sqrt\s*\(\s*252|"
            r"sqrt\s*\(\s*252.*\.std\(",
            re.DOTALL,
        )
        violations = []
        for py in SRC_ROOT.rglob("*.py"):
            if py.name in allowed:
                continue
            try:
                text = py.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if "compute_sharpe" in text or "import.*metrics" in text:
                # 已正确 import，跳过
                continue
            for m in pattern.finditer(text):
                rel = py.relative_to(PROJECT_ROOT)
                line_no = text[:m.start()].count("\n") + 1
                violations.append(f"{rel}:{line_no}")

        assert not violations, (
            f"§13.2 违规：以下文件可能内联了 Sharpe 计算"
            f"（应使用 metrics.compute_sharpe_ratio）：\n"
            + "\n".join(violations)
        )

    def test_risk_free_rate_consistency(self):
        """所有代码中 risk_free_rate 默认值必须为 0.02（§13.1）"""
        pattern = re.compile(
            r"risk_free[_\s]*rate[^=]*=\s*(0\.0\d+)",
            re.IGNORECASE,
        )
        violations = []
        for py in SRC_ROOT.rglob("*.py"):
            try:
                text = py.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for m in pattern.finditer(text):
                val = float(m.group(1))
                if abs(val - 0.02) > 1e-6:
                    rel = py.relative_to(PROJECT_ROOT)
                    line_no = text[:m.start()].count("\n") + 1
                    violations.append(
                        f"{rel}:{line_no} = {val} (should be 0.02)"
                    )

        assert not violations, (
            f"§13.1 risk_free_rate 不一致：\n"
            + "\n".join(violations)
        )


# ── YAML 配置全量验证 ──────────────────────────────────────────


class TestYAMLConfigIntegrity:
    """验证所有 YAML 配置文件完整性"""

    def test_all_yaml_parseable(self):
        """所有 YAML 配置文件必须可解析"""
        yaml_dirs = [
            CONFIGS_ROOT / "backtest",
            CONFIGS_ROOT / "cb_strategies",
            CONFIGS_ROOT / "capital_sweep",
            CONFIGS_ROOT / "examples",
            CONFIGS_ROOT / "paper",
        ]
        errors = []
        count = 0
        for d in yaml_dirs:
            if not d.exists():
                continue
            for yml in d.rglob("*.yml"):
                count += 1
                try:
                    with open(yml, encoding="utf-8") as f:
                        yaml.safe_load(f)
                except Exception as e:
                    rel = yml.relative_to(PROJECT_ROOT)
                    errors.append(f"{rel}: {e}")

        assert count > 0, "未找到任何 YAML 配置文件"
        assert not errors, (
            f"以下 YAML 文件解析失败：\n" + "\n".join(errors)
        )

    def test_no_zero_slippage(self):
        """禁止零滑点配置（§质量闸门 min_realistic=5.0）"""
        violations = []
        for yml in (CONFIGS_ROOT / "backtest").rglob("*.yml"):
            try:
                with open(yml, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if not data:
                    continue
            except Exception:
                continue
            # 检查 cost.slippage_bps
            cost = data.get("cost", {})
            if isinstance(cost, dict):
                bps = cost.get("slippage_bps")
                if bps is not None and float(bps) < 1.0:
                    rel = yml.relative_to(PROJECT_ROOT)
                    violations.append(f"{rel}: slippage_bps={bps}")

        assert not violations, (
            f"以下配置零/极低滑点（应 ≥5.0 bps）：\n"
            + "\n".join(violations)
        )

    def test_cb_yaml_commission_rate(self):
        """CB 策略 YAML commission_rate 必须与权威源一致（0.0000854，万0.854）"""
        violations = []
        for yml in (CONFIGS_ROOT / "cb_strategies").rglob("*.yml"):
            try:
                with open(yml, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if not data:
                    continue
            except Exception:
                continue
            cost = data.get("cost", {})
            if isinstance(cost, dict):
                cr = cost.get("commission_rate")
                if cr is not None and abs(float(cr) - 0.0000854) > 1e-8:
                    rel = yml.relative_to(PROJECT_ROOT)
                    violations.append(
                        f"{rel}: commission_rate={cr} (CB authority=0.0000854)"
                    )

        assert not violations, (
            f"CB YAML commission_rate 偏离权威源：\n"
            + "\n".join(violations)
        )

    def test_a_stock_yaml_transfer_fee(self):
        """A 股 YAML transfer_fee_rate 必须与权威源一致（0.00001）"""
        violations = []
        for yml in (CONFIGS_ROOT / "backtest").rglob("*.yml"):
            try:
                with open(yml, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if not data:
                    continue
            except Exception:
                continue
            cost = data.get("cost", {})
            if isinstance(cost, dict):
                tf = cost.get("transfer_fee_rate")
                if tf is not None and abs(float(tf) - 0.00001) > 1e-9:
                    # 排除 CB 配置（无过户费）
                    if float(tf) > 0:
                        rel = yml.relative_to(PROJECT_ROOT)
                        violations.append(
                            f"{rel}: transfer_fee_rate={tf} "
                            f"(authority=0.00001)"
                        )

        assert not violations, (
            f"A 股 YAML transfer_fee_rate 偏离权威源：\n"
            + "\n".join(violations)
        )

    def test_cb_yaml_commission_min_five(self):
        """CB 策略 YAML commission_min 必须为 5（CB 最低佣金5元）"""
        violations = []
        for yml in (CONFIGS_ROOT / "cb_strategies").rglob("*.yml"):
            try:
                with open(yml, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if not data:
                    continue
            except Exception:
                continue
            cost = data.get("cost", {})
            if isinstance(cost, dict):
                cm = cost.get("commission_min")
                if cm is not None and float(cm) != 5.0:
                    rel = yml.relative_to(PROJECT_ROOT)
                    violations.append(
                        f"{rel}: commission_min={cm} (CB authority=5)"
                    )

        assert not violations, (
            f"CB YAML commission_min 应为5（CB 最低佣金5元）：\n"
            + "\n".join(violations)
        )


# ── 策略模块完整性 ─────────────────────────────────────────────


class TestStrategyIntegrity:
    """验证策略模块可导入、注册表完整"""

    def test_all_strategy_files_importable(self):
        """src/aipha/strategy/ 下所有 .py 模块必须可导入"""
        strategy_dir = SRC_ROOT / "strategy"
        if not strategy_dir.exists():
            pytest.skip("strategy directory not found")

        errors = []
        count = 0
        for py in sorted(strategy_dir.glob("*.py")):
            if py.name.startswith("_"):
                continue
            count += 1
            module_name = f"aipha.strategy.{py.stem}"
            try:
                importlib.import_module(module_name)
            except Exception as e:
                errors.append(f"{module_name}: {type(e).__name__}: {e}")

        assert count >= 30, f"策略模块数量过少: {count}"
        assert not errors, (
            f"以下策略模块导入失败：\n" + "\n".join(errors)
        )

    def test_replay_cost_module_importable(self):
        """§13.2: replay_cost 共享模块必须可导入并提供正确接口"""
        from aipha.utils.replay_cost import (
            calc_buy_cost,
            calc_sell_cost,
            calc_slippage_impact,
        )

        # 基本功能测试：A 股买入费用
        cost = calc_buy_cost(
            price=10.0, shares=100, date="2024-06-01",
            asset_type="cn_equity",
        )
        assert cost > 0, "买入费用必须 > 0"

        # CB 买入费用（无过户费、无印花税、佣金更低）
        cost_cb = calc_buy_cost(
            price=100.0, shares=10, date="2024-06-01",
            asset_type="convertible_bond",
        )
        assert cost_cb > 0, "CB 买入费用必须 > 0"

    def test_cb_provider_suspension_not_hardcoded(self):
        """§13.3: CB provider 不得将 is_suspended 硬编码为 False"""
        import inspect
        from aipha.data.convertible_bond_provider import (
            ConvertibleBondProvider,
        )

        source = inspect.getsource(ConvertibleBondProvider)
        # 确保不存在 is_suspended=False 硬编码
        assert "is_suspended=False" not in source, (
            "convertible_bond_provider.py 中存在 is_suspended=False "
            "硬编码（§13.3 违规），应根据 vol/open 动态判断"
        )


# ── P0-A Guard: slippage_bps 默认值必须 ≥ 5.0 ──────────────────


class TestSlippageBpsAuthority:
    """确保所有模块的 slippage_bps 默认值不低于 CB 权威源 5.0"""

    def test_paper_gateway_slippage_bps(self):
        import inspect
        from aipha.execution.paper_gateway import PaperTradingGateway
        sig = inspect.signature(PaperTradingGateway.__init__)
        default = sig.parameters["slippage_bps"].default
        assert default >= 5.0, f"slippage_bps={default} < 5.0 (§13.1)"

    def test_paper_engine_config_slippage_bps(self):
        from aipha.runner.paper_engine import PaperEngineConfig
        cfg = PaperEngineConfig()
        assert cfg.slippage_bps >= 5.0

    def test_live_runner_config_slippage_bps(self):
        from aipha.runner.live_runner import RunnerConfig
        cfg = RunnerConfig()
        assert cfg.slippage_bps >= 5.0

    def test_multi_strategy_config_slippage_bps(self):
        from aipha.runner.multi_strategy import PortfolioConfig
        cfg = PortfolioConfig()
        assert cfg.slippage_bps >= 5.0


# ── P0-B/C Guard: config_parser risk 配置完整性 ─────────────────


class TestConfigParserRisk:
    """确保 risk_control 别名和 RiskConfig 字段全部被解析"""

    def test_risk_control_alias_handled(self):
        """risk_control: 必须被识别为 risk: 的别名"""
        from aipha.backtest.config_parser import _normalize_config
        raw = {"risk_control": {"max_drawdown": 0.15}}
        out = _normalize_config(raw)
        assert "risk" in out, "risk_control: 未被转换为 risk:"
        assert out["risk"]["max_drawdown"] == 0.15

    def test_risk_config_parses_concentration(self):
        """concentration_enabled 必须从 YAML 正确解析到 RiskConfig"""
        from aipha.backtest.config_parser import parse_config
        minimal = {
            "data": {"asset_type": "convertible_bond",
                     "start_date": "2020-01-01", "end_date": "2020-12-31"},
            "strategy": {"name": "double_low_percentile"},
            "risk": {"concentration_enabled": True},
        }
        cfg = parse_config(minimal)
        assert cfg.risk.concentration_enabled is True

    def test_risk_config_parses_industry_neutral(self):
        """industry_neutral_enabled 必须正确解析（之前完全丢失）"""
        from aipha.backtest.config_parser import parse_config
        minimal = {
            "data": {"asset_type": "convertible_bond",
                     "start_date": "2020-01-01", "end_date": "2020-12-31"},
            "strategy": {"name": "double_low_percentile"},
            "risk": {"industry_neutral_enabled": True,
                     "industry_max_deviation": 0.10},
        }
        cfg = parse_config(minimal)
        assert cfg.risk.industry_neutral_enabled is True
        assert cfg.risk.industry_max_deviation == 0.10


# ── P0-D Guard: paper_gateway 涨跌停检查 ────────────────────────


class TestPaperGatewayPriceLimit:
    """确保 PaperTradingGateway 在涨跌停时拒绝成交"""

    def test_try_fill_has_limit_check(self):
        """_try_fill_order 必须包含 is_limit_up/down 检查"""
        import inspect
        from aipha.execution.paper_gateway import PaperTradingGateway
        source = inspect.getsource(PaperTradingGateway._try_fill_order)
        assert "is_limit_up" in source, (
            "_try_fill_order 缺少 is_limit_up 检查 (§4.3/§13.4)"
        )
        assert "is_limit_down" in source, (
            "_try_fill_order 缺少 is_limit_down 检查 (§4.3/§13.4)"
        )


# ── P0-E Guard: Sharpe ddof=1 一致性 ───────────────────────────


class TestSharpeConsistency:
    """确保 src/ 中不存在 ddof=0 的 Sharpe 计算"""

    def test_archiver_sharpe_uses_sample_std(self):
        """archiver.py 必须使用 ddof=1（样本标准差）"""
        import inspect
        from aipha.reporting.archiver import ExperimentArchiver
        source = inspect.getsource(ExperimentArchiver._extract_metrics)
        assert "(len(returns) - 1)" in source, (
            "archiver._compute_metrics 仍使用 ddof=0 (§13.2)"
        )


# ── P0-G Guard: CB prev_close 不得静默跳过 ─────────────────────


class TestCBPrevCloseWarning:
    """确保 CB 涨跌停检查在 prev_close=None 时记录 warning"""

    def test_precheck_logs_when_prev_close_none(self):
        import inspect
        from aipha.market.rules_convertible_bond import (
            MarketRulesConvertibleBond,
        )
        source = inspect.getsource(
            MarketRulesConvertibleBond.precheck_order
        )
        assert "logger.warning" in source, (
            "precheck_order 未在 prev_close=None 时记录 warning (§13.3)"
        )


class TestAStockPrevCloseWarning:
    """确保 A 股涨跌停检查在 prev_close=None 时记录 warning（§13.3）"""

    def test_precheck_logs_when_prev_close_none(self):
        import inspect
        from aipha.market.rules_cn_equity import MarketRulesCNEquity
        source = inspect.getsource(MarketRulesCNEquity.precheck_order)
        assert "logger.warning" in source, (
            "A股 precheck_order 未在 prev_close=None 时记录 warning (§13.3)"
        )

    def test_precheck_returns_warning_when_prev_close_none(self):
        from datetime import datetime
        from aipha.domain.models import Bar, Order, OrderType, Side
        from aipha.market.rules_cn_equity import MarketRulesCNEquity
        rules = MarketRulesCNEquity()
        ts = datetime(2024, 1, 2, 9, 30)
        bar = Bar(
            symbol="000001.SZ", start_ts=ts, end_ts=ts,
            open=10.0, high=11.0, low=9.0,
            close=10.5, volume=1000, prev_close=None,
        )
        order = Order(
            order_id="test_001", symbol="000001.SZ",
            side=Side.BUY, qty=100,
            order_type=OrderType.LIMIT, t_submit=ts,
            limit_price=999.0,
        )
        decision = rules.precheck_order(order=order, bar=bar)
        assert decision.allowed, "prev_close=None 时应放行但标记未校验"
        assert decision.warning is not None, (
            "A股 prev_close=None 时 decision.warning 不应为 None (§13.3)"
        )


# ── P0-F Guard: A 股 YAML commission_rate 正确性 ──────────────


class TestAStockYamlCommissionRate:
    """确保 A 股 YAML 配置的 commission_rate 不低于权威值"""

    def test_a_stock_yamls_commission_rate(self):
        """含 stamp_tax_rate 且 lot_size=100 的 YAML 必须用 A 股费率"""
        from aipha.market.rules_cn_equity import MarketRulesCNEquity
        authority = float(MarketRulesCNEquity().commission_rate)

        bt_dir = CONFIGS_ROOT / "backtest"
        if not bt_dir.exists():
            pytest.skip("configs/backtest 不存在")

        violations = []
        for yml in bt_dir.glob("*.yml"):
            try:
                with open(yml, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if not data or not isinstance(data, dict):
                    continue
                cost = data.get("cost", {})
                if not isinstance(cost, dict):
                    continue
                cr = cost.get("commission_rate")
                st = cost.get("stamp_tax_rate")
                # 有印花税 → A 股配置
                if st and float(st) > 0 and cr and float(cr) < authority * 0.9:
                    violations.append(
                        f"{yml.name}: commission_rate={cr} < {authority}"
                    )
            except Exception:
                continue
        assert not violations, (
            f"A 股 YAML commission_rate 低于权威值 {authority}:\n"
            + "\n".join(violations)
        )


# ── R95 新增: 参数统一性审计守护 ─────────────────────────────────


class TestR95ParameterUniformity:
    """R95审计发现的参数不一致问题的回归守护测试"""

    def test_config_parser_slippage_from_defaults(self):
        """config_parser 解析 CB 时 slippage_bps 必须从 _defaults 获取(=5.0)"""
        from aipha.backtest.config_parser import parse_config
        minimal_cb = {
            "data": {"asset_type": "convertible_bond",
                     "start_date": "2020-01-01", "end_date": "2020-12-31"},
            "strategy": {"name": "double_low_percentile"},
        }
        cfg = parse_config(minimal_cb)
        assert cfg.cost.slippage_bps == 5.0, (
            f"CB 未指定 slippage_bps 时应默认 5.0, 实际={cfg.cost.slippage_bps}"
        )

    def test_config_parser_slippage_equity_default(self):
        """config_parser 解析 A股 时 slippage_bps 默认 10.0"""
        from aipha.backtest.config_parser import parse_config
        minimal_eq = {
            "data": {"start_date": "2020-01-01", "end_date": "2020-12-31"},
            "strategy": {"name": "buy_and_hold"},
        }
        cfg = parse_config(minimal_eq)
        assert cfg.cost.slippage_bps == 10.0, (
            f"A股 未指定 slippage_bps 时应默认 10.0, 实际={cfg.cost.slippage_bps}"
        )

    def test_replay_cost_impact_coeff_matches_fee_config(self):
        """replay_cost 默认 impact_coeff 必须与 fee_config 一致"""
        import inspect
        from aipha.utils.replay_cost import calc_slippage_impact
        sig = inspect.signature(calc_slippage_impact)
        default = sig.parameters["impact_coeff"].default
        assert default == 10.0, (
            f"replay_cost impact_coeff 默认值={default}, 应为 10.0 "
            f"(与 fee_config.IMPACT_COEFF 对齐)"
        )

    def test_a_stock_yaml_commission_not_too_high(self):
        """A股 YAML commission_rate 不应高于权威值 20% (万3 > 万2.5)"""
        from aipha.market.rules_cn_equity import MarketRulesCNEquity
        authority = float(MarketRulesCNEquity().commission_rate)
        bt_dir = CONFIGS_ROOT / "backtest"
        if not bt_dir.exists():
            pytest.skip("configs/backtest 不存在")
        violations = []
        for yml in bt_dir.glob("*.yml"):
            try:
                with open(yml, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if not data or not isinstance(data, dict):
                    continue
                cost = data.get("cost", {})
                if not isinstance(cost, dict):
                    continue
                cr = cost.get("commission_rate")
                st = cost.get("stamp_tax_rate")
                if st and float(st) > 0 and cr and float(cr) > authority * 1.2:
                    violations.append(
                        f"{yml.name}: commission_rate={cr} > {authority*1.2}"
                    )
            except Exception:
                continue
        assert not violations, (
            f"A股 YAML commission_rate 超过权威值 20%:\n"
            + "\n".join(violations)
        )

    def test_cb_scripts_commission_min_five(self):
        """scripts/ 中 CB 回测脚本应使用 commission_min=5.0（CB 最低佣金5元）"""
        scripts_dir = PROJECT_ROOT / "scripts"
        violations = []
        for py in scripts_dir.glob("*.py"):
            if "archive" in str(py):
                continue
            try:
                text = py.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            # 查找 commission_min=0.1 或 commission_min=0.0 (旧错误值)
            for i, line in enumerate(text.splitlines(), 1):
                if "commission_min=0.1" in line or "commission_min=0.0" in line or "min_fee=0.1" in line:
                    stripped = line.lstrip()
                    if stripped.startswith("#"):
                        continue
                    violations.append(f"{py.name}:{i}: {stripped[:60]}")
        assert not violations, (
            f"CB脚本 commission_min 应为5.0（CB 最低佣金5元）:\n"
            + "\n".join(violations)
        )

    def test_runner_defaults_asset_self_consistent(self):
        """Runner 配置的默认值必须属于同一资产类型"""
        from aipha.runner.live_runner import RunnerConfig
        from aipha.runner.multi_strategy import PortfolioConfig

        # RunnerConfig: 费率=A股, slippage 也应该=A股
        rc = RunnerConfig()
        if float(rc.commission_rate) == 0.0000854:  # A股费率（万0.854）
            assert rc.slippage_bps == 10.0, (
                f"RunnerConfig 费率=A股 但 slippage_bps={rc.slippage_bps} "
                f"!=10.0(A股权威值), 资产类型默认值不自洽"
            )

        # PortfolioConfig: 费率=A股, slippage 也应该=A股
        pc = PortfolioConfig()
        if float(pc.commission_rate) == 0.0000854:
            assert pc.slippage_bps == 10.0, (
                f"PortfolioConfig 费率=A股 但 slippage_bps={pc.slippage_bps} "
                f"!=10.0(A股权威值), 资产类型默认值不自洽"
            )

    def test_no_independent_fee_model_in_scripts(self):
        """scripts/ 下不应定义 FeeModel 类 (§13.2)"""
        scripts_dir = PROJECT_ROOT / "scripts"
        violations = []
        for py in scripts_dir.rglob("*.py"):
            if "archive" in str(py):
                continue
            try:
                text = py.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for m in re.finditer(
                r"^\s*class\s+(FeeModel\w+)", text, re.MULTILINE
            ):
                rel = py.relative_to(PROJECT_ROOT)
                violations.append(f"{rel}: {m.group(1)}")
        assert not violations, (
            f"§13.2违规: scripts/下存在独立FeeModel类:\n"
            + "\n".join(violations)
        )


# ── R96 新增: 全面参数合规守护（解决反复审计根因）──────────────────


class TestR96ComprehensiveParameterGuard:
    """R96 全面参数合规守护。

    根因分析：历经 R92-R95 四轮审计仍有遗漏，原因是：
    1. 审计靠人工 grep，遗漏率高
    2. 无编译期守护阻止不合规值进入代码
    3. 模板文件错误导致新配置继承错误
    本测试类从机制上杜绝这些问题。
    """

    # --- 权威值基准 ---
    A_STOCK_CR = 0.0000854  # §13.1 MarketRulesCNEquity.commission_rate（万0.854）
    A_STOCK_SB = 10.0      # §13.1 MarketRulesCNEquity.slippage_bps_default
    CB_CR = 0.0000854       # §13.1 MarketRulesConvertibleBond.commission_rate（万0.854）
    CB_CM = 0.1            # §13.1 MarketRulesConvertibleBond.commission_min
    CB_SB = 5.0            # §13.1 MarketRulesConvertibleBond.slippage_bps_default

    def _is_a_stock_yaml(self, data: dict) -> bool:
        """Heuristic: A-stock if stamp_tax > 0, lot_size=100, or t_plus_n=1."""
        cost = data.get("cost", {})
        if not isinstance(cost, dict):
            return False
        st = cost.get("stamp_tax_rate")
        ls = data.get("lot_size")
        tp = data.get("t_plus_n")
        at = data.get("data", {}).get("asset_type", "")
        if at == "stock" or at == "cn_equity":
            return True
        if st and float(st) > 0:
            return True
        if ls and int(ls) == 100 and (tp is None or int(tp) == 1):
            return True
        return False

    def _is_cb_yaml(self, data: dict) -> bool:
        """Heuristic: CB if lot_size=10, t_plus_n=0, or asset_type=convertible_bond."""
        at = data.get("data", {}).get("asset_type", "")
        if "convertible" in at or "cb" in at:
            return True
        ls = data.get("lot_size")
        tp = data.get("t_plus_n")
        st = data.get("cost", {}).get("stamp_tax_rate")
        if ls and int(ls) == 10 and tp is not None and int(tp) == 0:
            return True
        return False

    def test_examples_yaml_a_stock_commission_rate(self):
        """examples/ 下 A 股 YAML 的 commission_rate 必须 >= 5e-05（万0.5）。

        根因：示例 YAML 曾用错误佣金率做 A 股回测，
        导致所有从模板复制的新配置继承错误佣金率。
        当前账户费率为万0.854 (8.5e-05)，下限设为万0.5 (5e-05) 防止极端低值。
        """
        examples_dir = CONFIGS_ROOT / "backtest" / "examples"
        if not examples_dir.exists():
            pytest.skip("examples dir missing")
        violations = []
        for yml in examples_dir.glob("*.yml"):
            try:
                with open(yml, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                cost = data.get("cost", {})
                if not isinstance(cost, dict):
                    continue
                cr = cost.get("commission_rate")
                if cr is None:
                    continue
                cr = float(cr)
                # A 股判定: stamp_tax > 0 or lot_size=100 or t_plus_n=1
                if self._is_a_stock_yaml(data) and cr < 5e-05:
                    violations.append(
                        f"{yml.name}: commission_rate={cr} < 5e-05 (A股下限)"
                    )
            except Exception:
                continue
        assert not violations, (
            "examples/ A股模板 commission_rate 低于下限:\n"
            + "\n".join(violations)
        )

    def test_examples_yaml_a_stock_slippage_bps(self):
        """examples/ A 股 YAML slippage_bps 必须 >= 8.0。

        根因：示例曾使用 2.0/3.0/5.0 bps 做 A 股回测（权威值 10.0），
        导致回测严重低估滑点成本。
        """
        examples_dir = CONFIGS_ROOT / "backtest" / "examples"
        if not examples_dir.exists():
            pytest.skip("examples dir missing")
        violations = []
        for yml in examples_dir.glob("*.yml"):
            try:
                with open(yml, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                cost = data.get("cost", {})
                if not isinstance(cost, dict):
                    continue
                sb = cost.get("slippage_bps")
                if sb is None:
                    continue
                sb = float(sb)
                if self._is_a_stock_yaml(data) and sb < 8.0:
                    violations.append(
                        f"{yml.name}: slippage_bps={sb} < 8.0 (A股§13.1=10)"
                    )
            except Exception:
                continue
        assert not violations, (
            "examples/ A股 slippage_bps 低于下限:\n"
            + "\n".join(violations)
        )

    def test_paper_equity_yaml_slippage_minimum(self):
        """paper/ A 股配置 slippage_bps 必须 >= 8.0。

        根因：equity_multifactor.yml 和 equity_pead.yml 曾使用 5.0 bps。
        """
        paper_dir = CONFIGS_ROOT / "paper"
        if not paper_dir.exists():
            pytest.skip("paper dir missing")
        violations = []
        for yml in paper_dir.glob("equity_*.yml"):
            try:
                with open(yml, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                # 递归查找 slippage_bps in nested dicts
                def find_slippage(d, path=""):
                    if isinstance(d, dict):
                        for k, v in d.items():
                            if k == "slippage_bps" and v is not None:
                                sb = float(v)
                                if sb < 8.0:
                                    violations.append(
                                        f"{yml.name}{path}.{k}={sb}"
                                    )
                            elif isinstance(v, dict):
                                find_slippage(v, f"{path}.{k}")
                find_slippage(data)
            except Exception:
                continue
        assert not violations, (
            "paper/ A股配置 slippage_bps 低于 8.0:\n"
            + "\n".join(violations)
        )

    def test_all_backtest_yaml_slippage_floor(self):
        """所有回测 YAML 的 slippage_bps 必须 >= CB 权威值 5.0。

        允许刻意保守（>5.0），禁止乐观偏差（<5.0）。
        此测试覆盖 configs/backtest/**/*.yml。
        """
        bt_dir = CONFIGS_ROOT / "backtest"
        if not bt_dir.exists():
            pytest.skip("backtest dir missing")
        violations = []
        for yml in bt_dir.rglob("*.yml"):
            try:
                with open(yml, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                cost = data.get("cost", {})
                if not isinstance(cost, dict):
                    continue
                sb = cost.get("slippage_bps")
                if sb is None:
                    continue
                sb = float(sb)
                if sb < self.CB_SB:
                    rel = yml.relative_to(bt_dir)
                    violations.append(f"{rel}: slippage_bps={sb} < {self.CB_SB}")
            except Exception:
                continue
        assert not violations, (
            f"回测 YAML slippage_bps 低于 CB 最小值 {self.CB_SB}:\n"
            + "\n".join(violations)
        )

    def test_sweep_scripts_a_stock_params(self):
        """sweep_12y_*.py 中 A 股参数必须使用合理值。

        根因：6 个 sweep 脚本曾用 CB 佣金率 + A 股其余参数的混搭组合。
        当前账户佣金率为万0.854 (8.5e-05)，下限设为万0.5 (5e-05)。
        """
        scripts_dir = PROJECT_ROOT / "scripts"
        violations = []
        for py in scripts_dir.glob("sweep_12y_*.py"):
            try:
                text = py.read_text(encoding="utf-8")
            except Exception:
                continue
            # Check commission rate (only in commission dict context)
            for m in re.finditer(
                r'"commission"\s*:\s*\{[^}]*"rate"\s*:\s*([\d.e-]+)',
                text,
            ):
                val = float(m.group(1))
                if val < 5e-05:
                    violations.append(
                        f"{py.name}: commission rate={val} "
                        f"< 5e-05 (A股下限)"
                    )
            # Check slippage bps
            for m in re.finditer(r'"bps"\s*:\s*([\d.]+)', text):
                val = float(m.group(1))
                if val < 8.0:
                    violations.append(
                        f"{py.name}: slippage bps={val} "
                        f"< 8.0 (A股§13.1=10)"
                    )
        assert not violations, (
            "sweep脚本 A股参数不合规:\n"
            + "\n".join(violations)
        )

    def test_fee_config_slippage_matches_authority(self):
        """fee_config.SLIPPAGE_SPREAD_BPS 作为 linear_impact 基础分量须 >= 1.0 bps。

        NOTE: fee_config.SLIPPAGE_SPREAD_BPS 是线性冲击模型的 base spread 分量,
        不等同于 MarketRulesCNEquity.slippage_bps_default (10.0, 固定滑点模式)。
        实际总滑点 = base_bps + impact_coeff * sqrt(participation) >= base_bps,
        在 CSI2000 微盘股中通常 4~7 bps。(R115 审查修正)
        """
        fc_path = PROJECT_ROOT / "scripts" / "fee_config.py"
        if not fc_path.exists():
            pytest.skip("scripts/fee_config.py not found")
        text = fc_path.read_text(encoding="utf-8")
        m = re.search(
            r'SLIPPAGE_SPREAD_BPS.*_env_float\([^,]+,\s*([\d.]+)\)',
            text,
        )
        if not m:
            pytest.skip("SLIPPAGE_SPREAD_BPS pattern not found")
        sb = float(m.group(1))
        # base spread 下限: 1.0 bps (线性冲击模型, 非固定滑点)
        MIN_BASE_SPREAD = 1.0
        assert sb >= MIN_BASE_SPREAD, (
            f"fee_config.SLIPPAGE_SPREAD_BPS={sb} "
            f"< linear_impact base spread 下限 {MIN_BASE_SPREAD}"
        )

    def test_paper_yaml_slippage_minimum_floor(self):
        """所有 paper/ YAML slippage_bps >= CB 最小值 5.0。"""
        paper_dir = CONFIGS_ROOT / "paper"
        if not paper_dir.exists():
            pytest.skip("paper dir missing")
        violations = []
        for yml in paper_dir.glob("*.yml"):
            try:
                with open(yml, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}

                def scan(d, path=""):
                    if isinstance(d, dict):
                        for k, v in d.items():
                            if k == "slippage_bps" and v is not None:
                                if float(v) < self.CB_SB:
                                    violations.append(
                                        f"{yml.name}{path}.{k}={v}"
                                    )
                            elif isinstance(v, dict):
                                scan(v, f"{path}.{k}")
                scan(data)
            except Exception:
                continue
        assert not violations, (
            f"paper/ YAML slippage_bps < {self.CB_SB}:\n"
            + "\n".join(violations)
        )

    def test_readme_transfer_fee_rate(self):
        """README.md transfer_fee_rate <= §13.1 权威值。"""
        readme = PROJECT_ROOT / "README.md"
        if not readme.exists():
            pytest.skip("README not found")
        text = readme.read_text(encoding="utf-8")
        for m in re.finditer(
            r"transfer_fee_rate:\s*([\d.e-]+)", text
        ):
            val = float(m.group(1))
            assert val <= 0.00001, (
                f"README transfer_fee_rate={val} "
                f"> §13.1 A股权威值 0.00001"
            )

    def test_archive_commission_min_five_cb(self):
        """archive/ CB 脚本 commission_min 应为 5（CB 最低佣金5元）。"""
        archive_dir = PROJECT_ROOT / "scripts" / "archive"
        if not archive_dir.exists():
            pytest.skip("archive dir missing")
        violations = []
        for py in archive_dir.glob("*.py"):
            try:
                text = py.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                if re.search(
                    r"commission_min.*=\s*0\.1\b"
                    r"|commission_min.*=\s*0\.0[^1-9]"
                    r"|min_fee=0\.1\b"
                    r"|min_fee=0\.0[^1-9]",
                    line,
                ):
                    violations.append(f"{py.name}:{i}")
        assert not violations, (
            "archive/ CB commission_min 应为5:\n"
            + "\n".join(violations)
        )

    def test_test_configs_astock_commission(self):
        """tests/scripts/ A 股测试配置 commission_rate >= 5e-05（万0.5 下限）。"""
        test_dir = PROJECT_ROOT / "tests" / "scripts"
        if not test_dir.exists():
            pytest.skip("tests/scripts not found")
        violations = []
        for py in test_dir.glob("*.py"):
            try:
                text = py.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if '"stock"' not in text and "'stock'" not in text:
                continue
            for m in re.finditer(
                r'"commission"\s*:\s*\{[^}]*"rate"\s*:\s*([\d.e-]+)',
                text,
            ):
                val = float(m.group(1))
                if val < 5e-05:
                    violations.append(
                        f"{py.name}: rate={val}"
                    )
        assert not violations, (
            "测试配置 A 股佣金率过低:\n"
            + "\n".join(violations)
        )

    def test_cb_strategy_yaml_min_commission(self):
        """CB 策略配置 min_commission >= 5（§13.1 MarketRulesConvertibleBond — CB最低佣金5元）。"""
        cb_dir = PROJECT_ROOT / "configs" / "cb_strategies"
        if not cb_dir.exists():
            pytest.skip("configs/cb_strategies not found")
        violations = []
        for yml in cb_dir.rglob("*.yml"):
            try:
                text = yml.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                m = re.search(r"min_commission:\s*([\d.e+-]+)", stripped)
                if m:
                    val = float(m.group(1))
                    if val < 5.0:
                        rel = yml.relative_to(PROJECT_ROOT)
                        violations.append(f"{rel}:{i} min_commission={val}")
        assert not violations, (
            "CB min_commission < 5.0 (§13.1 — CB最低佣金5元):\n"
            + "\n".join(violations)
        )

    def test_astock_backtest_yaml_commission_rate(self):
        """A 股回测配置 commission.rate >= 5e-05（万0.5 下限）。"""
        bt_dir = PROJECT_ROOT / "configs" / "backtest"
        if not bt_dir.exists():
            pytest.skip("configs/backtest not found")
        violations = []
        for yml in bt_dir.rglob("*.yml"):
            # Skip diagnostic zero-cost configs (intentionally zero fees)
            if "zerocost" in yml.name:
                continue
            try:
                text = yml.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            # Only check A-stock configs
            is_astock = (
                "asset_type: stock" in text
                or bool(re.search(r"lot_size:\s*100\b", text))
            )
            if not is_astock:
                continue
            # Match commission.rate specifically (inside commission block)
            m = re.search(
                r"commission:\s*\n\s+(?:min_fee:\s*[\d.]+\s*\n\s+)?"
                r"rate:\s*([\d.e+-]+)",
                text,
            )
            if m:
                val = float(m.group(1))
                if val < 5e-05:
                    rel = yml.relative_to(PROJECT_ROOT)
                    violations.append(f"{rel}: commission.rate={val}")
        assert not violations, (
            "A-stock YAML commission.rate < 5e-05:\n"
            + "\n".join(violations)
        )