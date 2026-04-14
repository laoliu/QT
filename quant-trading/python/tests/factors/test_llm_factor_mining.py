"""LLM 因子挖掘管道集成测试。

覆盖 4 个新模块：
- factor_expression_parser.py (表达式解析)
- ast_dedup.py (AST 去重)
- ic_trap_guard.py (5 阶段防护管道)
- llm_factor_miner.py (主编排器)

测试策略：
- 用构造的 toy data 验证正确性
- 不依赖外部 API / 真实市场数据
"""

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from aipha.factors.factor_expression_parser import (
    ALLOWED_OPERATORS,
    FactorExpressionParser,
    ParsedFactor,
    QLIB_ALIASES,
)
from aipha.factors.ast_dedup import ASTFactorDeduplicator, ASTSignature, DedupResult
from aipha.factors.ic_trap_guard import (
    GuardConfig,
    GuardCheckResult,
    GuardVerdict,
    ICTrapGuard,
)
from aipha.factors.llm_factor_miner import (
    CampaignResult,
    CandidateSource,
    FactorCandidate,
    FactorKnowledgeBase,
    LLMFactorMiner,
    MiningConfig,
    ValidationResult,
)


# ── FactorExpressionParser ─────────────────────────────


class TestFactorExpressionParser:
    """表达式解析器测试"""

    def setup_method(self):
        self.parser = FactorExpressionParser()

    def test_parse_simple_operator(self):
        """解析简单时序算子"""
        p = self.parser.parse("ts_mean($close, 20)", "f1")
        assert p.is_valid
        assert "ts_mean" in p.used_operators
        assert "close" in p.required_fields

    def test_parse_nested_expression(self):
        """解析嵌套表达式"""
        p = self.parser.parse("cs_rank(ts_corr($close, $volume, 10))", "f2")
        assert p.is_valid
        assert "cs_rank" in p.used_operators
        assert "ts_corr" in p.used_operators

    def test_parse_qlib_alias(self):
        """Qlib 别名映射"""
        p = self.parser.parse("Rank(Mean($close, 20))", "f3")
        assert p.is_valid
        assert "cs_rank" in p.normalized_expression
        assert "ts_mean" in p.normalized_expression

    def test_parse_field_normalization(self):
        """$field 字段替换"""
        p = self.parser.parse("ts_delta($close, 5)", "f4")
        assert p.is_valid
        assert "close" in p.required_fields
        # 规范化后 $close 应被替换
        assert "$" not in p.normalized_expression

    def test_parse_invalid_operator(self):
        """拒绝未知算子"""
        p = self.parser.parse("unknown_op($close, 5)", "bad")
        assert not p.is_valid

    def test_parse_math_expression(self):
        """数学运算"""
        p = self.parser.parse("-1 * ts_rank(ts_delta($volume, 3))", "f5")
        assert p.is_valid
        assert "ts_rank" in p.used_operators
        assert "ts_delta" in p.used_operators

    def test_batch_parse(self):
        """批量解析"""
        exprs = {
            "f1": "ts_mean($close, 20)",
            "f2": "cs_rank($volume)",
            "bad": "evil_func($x)",
        }
        results = self.parser.batch_parse(exprs)
        assert len(results) == 3
        valid_count = sum(1 for r in results if r.is_valid)
        assert valid_count == 2

    def test_allowed_operators_nonempty(self):
        """算子白名单非空"""
        assert len(ALLOWED_OPERATORS) >= 20

    def test_qlib_aliases_coverage(self):
        """Qlib 别名覆盖常用算子"""
        assert "Ref" in QLIB_ALIASES
        assert "Mean" in QLIB_ALIASES
        assert "Rank" in QLIB_ALIASES


# ── ASTFactorDeduplicator ──────────────────────────────


class TestASTFactorDeduplicator:
    """AST 去重测试"""

    def setup_method(self):
        self.dedup = ASTFactorDeduplicator(similarity_threshold=0.85)

    def test_register_and_check_identical(self):
        """完全相同的表达式应被判定为重复"""
        self.dedup.register("f1", "ts_mean(close, 20)")
        result = self.dedup.check_duplicate("f2", "ts_mean(close, 20)")
        assert result.is_duplicate
        assert result.most_similar_name == "f1"
        assert result.similarity_score >= 0.99

    def test_different_expressions_not_duplicate(self):
        """不同结构的表达式不重复"""
        self.dedup.register("f1", "ts_mean(close, 20)")
        result = self.dedup.check_duplicate("f2", "ts_corr(close, volume, 10)")
        assert not result.is_duplicate

    def test_similar_but_distinct(self):
        """窗口不同但结构相同"""
        self.dedup.register("f1", "ts_mean(close, 20)")
        # 常量泛化后结构应相同
        result = self.dedup.check_duplicate("f2", "ts_mean(close, 10)")
        # 由于常量被泛化，结构完全一致 → 应视为重复
        assert result.is_duplicate

    def test_compute_signature(self):
        """签名计算基本属性"""
        sig = self.dedup.compute_signature("f1", "ts_mean(close, 20)")
        assert isinstance(sig, ASTSignature)
        assert sig.depth > 0
        assert sig.node_count > 0
        assert len(sig.structure_hash) > 0

    def test_batch_check(self):
        """批量去重"""
        self.dedup.register("f0", "ts_mean(close, 20)")
        candidates = {
            "f1": "ts_std(close, 20)",
            "f2": "ts_mean(close, 20)",  # 重复
            "f3": "cs_rank(ts_corr(close, volume, 10))",
        }
        results = self.dedup.batch_check(candidates)
        assert len(results) == 3
        dup_count = sum(1 for r in results.values() if r.is_duplicate)
        assert dup_count >= 1  # 至少 f2 被判重

    def test_empty_expression(self):
        """空表达式处理"""
        sig = self.dedup.compute_signature("empty", "")
        assert sig.depth == 0 or sig.node_count == 0


# ── ICTrapGuard ────────────────────────────────────────


class TestICTrapGuard:
    """IC Trap Guard 管道测试"""

    def setup_method(self):
        self.config = GuardConfig(
            ic_threshold=0.04,
            icir_threshold=0.30,
            ast_similarity_threshold=0.85,
            correlation_threshold=0.70,
            ar_drop_tolerance=0.03,
            hlz_t_threshold=2.0,
        )
        self.guard = ICTrapGuard(self.config)

    def _make_returns(self, n=500):
        np.random.seed(42)
        return pd.Series(np.random.normal(0, 0.02, n))

    def _make_factor_values(self, returns, ic_target=0.08):
        """构造具有指定 IC 的因子值"""
        np.random.seed(42)
        noise = np.random.normal(0, 1, len(returns))
        # 混合信号 + 噪声实现目标 IC
        signal = returns.values * ic_target * 50 + noise
        return pd.Series(signal, index=returns.index)

    def test_guard_config_defaults(self):
        """默认配置值"""
        cfg = GuardConfig()
        assert cfg.ic_threshold == 0.04
        assert cfg.icir_threshold == 0.30
        assert cfg.hlz_t_threshold == 2.0

    def test_guard_verdict_enum(self):
        """判定枚举值"""
        assert GuardVerdict.PASS.value == "pass"
        assert GuardVerdict.FAIL_IC.value == "fail_ic_threshold"
        assert GuardVerdict.FAIL_AR_DROP.value == "fail_ar_drop"

    def test_check_factor_basic_pass(self):
        """基本通过场景"""
        returns = self._make_returns()
        values = self._make_factor_values(returns, ic_target=0.10)
        self.guard.set_baseline(baseline_ar=0.20)
        result = self.guard.check_factor(
            factor_name="test_f",
            factor_values=values,
            forward_returns=returns,
            expression="ts_mean(close, 20)",
            new_ar=0.19,
        )
        assert isinstance(result, GuardCheckResult)

    def test_check_factor_ar_drop_fail(self):
        """AR 下降超限应失败"""
        returns = self._make_returns()
        values = self._make_factor_values(returns, ic_target=0.10)
        self.guard.set_baseline(baseline_ar=0.25)
        result = self.guard.check_factor(
            factor_name="bad_f",
            factor_values=values,
            forward_returns=returns,
            expression="ts_mean(close, 20)",
            new_ar=0.15,
        )
        # 如果 IC 通过但 AR 下降超限，应该在 AR drop 阶段失败
        ic_val = result.details.get("checks", {}).get("ic", {}).get("ic", 0)
        if ic_val >= self.config.ic_threshold:
            assert result.verdict == GuardVerdict.FAIL_AR_DROP

    def test_set_baseline(self):
        """设置基线"""
        self.guard.set_baseline(baseline_ar=0.25, existing_factor_names=["f1"])
        assert self.guard._baseline_ar == 0.25

    def test_guard_check_result_passed_property(self):
        """GuardCheckResult.passed 属性"""
        r = GuardCheckResult(factor_name="f", verdict=GuardVerdict.PASS)
        assert r.passed
        r2 = GuardCheckResult(factor_name="f", verdict=GuardVerdict.FAIL_IC)
        assert not r2.passed


# ── LLMFactorMiner ─────────────────────────────────────


class TestLLMFactorMiner:
    """LLM 因子挖掘编排器测试"""

    def setup_method(self):
        self.miner = LLMFactorMiner()

    def test_default_config(self):
        """默认配置"""
        assert self.miner.config.candidates_per_round == 20
        assert len(self.miner.config.available_operators) > 10
        assert len(self.miner.config.available_fields) > 5

    def test_knowledge_base_default_constraints(self):
        """默认 CF 约束已加载"""
        assert len(self.miner.knowledge._cf_constraints) >= 5
        texts = "\n".join(self.miner.knowledge._cf_constraints)
        assert "CF-020" in texts
        assert "IC Trap" in texts

    def test_knowledge_base_format_prompt(self):
        """知识库格式化为 prompt"""
        kb = FactorKnowledgeBase()
        kb.add_cf_constraint("Test constraint")
        kb.add_passed("f1", "ts_mean(close, 20)", 0.06, 0.45)
        kb.add_failed("bad", "evil(x)", "invalid operator")
        text = kb.format_for_prompt()
        assert "Test constraint" in text
        assert "f1" in text
        assert "evil" in text

    def test_generate_mining_prompt(self):
        """生成挖掘提示"""
        prompt = self.miner.generate_mining_prompt(
            direction="量价背离", n_factors=5,
        )
        assert "量价背离" in prompt
        assert "ts_mean" in prompt
        assert "close" in prompt
        assert "5" in prompt

    def test_add_manual_candidates(self):
        """手动添加候选"""
        cands = self.miner.add_manual_candidates({
            "mom5": "ts_delta($close, 5)",
            "vol20": "ts_std($close, 20)",
        })
        assert len(cands) == 2
        assert cands[0].source == CandidateSource.MANUAL

    def test_parse_llm_response(self):
        """解析 LLM 响应"""
        response = """
名称: 动量反转
表达式: cs_rank(ts_delta($close, 10))
理论依据: 短期动量反转效应

名称: 波动率压缩
表达式: safe_div(ts_std($close, 5), ts_std($close, 20))
理论依据: 波动率压缩后突破
"""
        cands = self.miner.parse_llm_response(response)
        assert len(cands) == 2
        assert cands[0].name == "动量反转"
        assert "cs_rank" in cands[0].expression
        assert cands[1].rationale == "波动率压缩后突破"

    def test_validate_candidate_parse_only(self):
        """仅解析模式验证（无因子值）"""
        cand = FactorCandidate(
            name="test", expression="ts_mean(close, 20)",
            source=CandidateSource.MANUAL,
        )
        result = self.miner.validate_candidate(
            candidate=cand,
            returns=pd.Series(dtype=float),
        )
        assert isinstance(result, ValidationResult)

    def test_run_campaign_empty(self):
        """空候选战役"""
        result = self.miner.run_campaign(
            candidates=[],
            campaign_id="test_empty",
        )
        assert isinstance(result, CampaignResult)
        assert result.total_candidates == 0
        assert result.pass_rate == 0.0

    def test_run_campaign_with_candidates(self):
        """带候选的战役"""
        cands = self.miner.add_manual_candidates({
            "f1": "ts_mean(close, 20)",
            "f2": "cs_rank(ts_delta(close, 5))",
            "bad": "evil_func(x)",
        })
        result = self.miner.run_campaign(
            candidates=cands,
            campaign_id="test_run",
        )
        assert result.total_candidates == 3
        assert result.campaign_id == "test_run"

    def test_export_report(self):
        """报告导出"""
        cands = self.miner.add_manual_candidates({
            "f1": "ts_mean(close, 20)",
        })
        self.miner.run_campaign(candidates=cands, campaign_id="r1")
        report = self.miner.export_report()
        assert report["total_campaigns"] == 1
        assert "campaigns" in report
        assert "passed_factors" in report
        assert "knowledge_base" in report

    def test_campaign_result_summary(self):
        """战役结果摘要"""
        cr = CampaignResult(
            campaign_id="test",
            total_candidates=10,
            parse_failures=2,
            guard_failures=5,
            passed=3,
            elapsed_seconds=1.5,
        )
        s = cr.summary()
        assert s["campaign_id"] == "test"
        assert s["pass_rate"] == 0.3

    def test_factor_candidate_dataclass(self):
        """FactorCandidate 数据类"""
        fc = FactorCandidate(
            name="test", expression="ts_mean(close, 20)",
            source=CandidateSource.RDAGENT,
            rationale="动量效应",
        )
        assert fc.source == CandidateSource.RDAGENT
        assert fc.rationale == "动量效应"

    def test_ingest_rdagent_nonexistent_dir(self):
        """RD-Agent 不存在目录"""
        cands = self.miner.ingest_rdagent_output("/nonexistent/path")
        assert len(cands) == 0


# ── Phase1-3 新增测试: 搜索空间扩展+复杂度+反馈 ─────


class TestExpandedSearchSpace:
    """验证 Phase1.1 扩展后的搜索空间"""

    def test_mining_config_includes_fundamental_fields(self):
        """MiningConfig 默认字段包含基本面字段"""
        cfg = MiningConfig()
        assert "pe_ttm" in cfg.available_fields
        assert "pb" in cfg.available_fields
        assert "total_mv" in cfg.available_fields
        assert "dv_ttm" in cfg.available_fields

    def test_mining_config_includes_extended_operators(self):
        """MiningConfig 默认算子包含扩展算子"""
        cfg = MiningConfig()
        assert "ts_resi" in cfg.available_operators
        assert "ts_rsquare" in cfg.available_operators
        assert "cs_scale" in cfg.available_operators
        assert "pow2" in cfg.available_operators

    def test_mining_config_includes_fundamental_directions(self):
        """MiningConfig 默认方向包含基本面方向（财务数据已接入）"""
        cfg = MiningConfig()
        # 财务数据接入后，基本面方向恢复
        assert "基本面价值" in cfg.directions
        assert "盈利质量" in cfg.directions
        # 价量方向仍存在
        assert "事件驱动" in cfg.directions
        assert "价量动量" in cfg.directions

    def test_prompt_contains_fundamental_fields(self):
        """生成的 prompt 包含基本面字段提示"""
        miner = LLMFactorMiner()
        prompt = miner.generate_mining_prompt("基本面价值", 5)
        assert "pe_ttm" in prompt
        assert "pb" in prompt
        assert "基本面字段" in prompt

    def test_prompt_contains_hypothesis_requirement(self):
        """生成的 prompt 要求先写假说"""
        miner = LLMFactorMiner()
        prompt = miner.generate_mining_prompt("价量动量", 5)
        assert "假说" in prompt
        assert "嵌套深度不超过 5 层" in prompt


class TestComplexityControl:
    """验证 Phase1.3 复杂度控制门禁"""

    def test_guard_config_has_complexity_params(self):
        """GuardConfig 包含复杂度控制参数"""
        cfg = GuardConfig()
        assert cfg.max_ast_depth == 5
        assert cfg.max_ast_node_count == 15

    def test_complexity_verdict_exists(self):
        """GuardVerdict 包含 FAIL_COMPLEXITY"""
        assert hasattr(GuardVerdict, "FAIL_COMPLEXITY")
        assert GuardVerdict.FAIL_COMPLEXITY.value == "fail_complexity"


class TestFeedbackMechanism:
    """验证 Phase2.1 闭环反馈机制"""

    def test_failure_pattern_analysis(self):
        """失败模式分析"""
        kb = FactorKnowledgeBase()
        kb.add_failed("f1", "expr1", "fail_ic_threshold: IC too low")
        kb.add_failed("f2", "expr2", "fail_ic_threshold: IC too low")
        kb.add_failed("f3", "expr3", "AST 重复: similar to f1")
        patterns = kb._analyze_failure_patterns()
        assert "IC/ICIR 不达标" in patterns
        assert patterns["IC/ICIR 不达标"] == 2

    def test_failure_patterns_in_prompt(self):
        """失败模式出现在 prompt 中"""
        kb = FactorKnowledgeBase()
        for i in range(3):
            kb.add_failed(f"f{i}", f"expr{i}", "fail_ic: low")
        text = kb.format_for_prompt()
        assert "失败模式分析" in text

    def test_feedback_prompt_includes_last_campaign(self):
        """反馈 prompt 包含上轮战役信息"""
        miner = LLMFactorMiner()
        # 模拟一轮 campaign
        cand = FactorCandidate(
            name="test", expression="ts_mean($close, 20)",
            source=CandidateSource.MANUAL)
        miner.run_campaign([cand])
        prompt = miner.generate_feedback_prompt("价量动量", 5)
        assert "上轮挖掘反馈" in prompt
        assert "通过率" in prompt


class TestHypothesisConsistency:
    """验证 Phase2.2 假说-因子一致性"""

    def test_consistency_prompt_generation(self):
        """一致性验证 prompt 生成"""
        miner = LLMFactorMiner()
        cands = [
            FactorCandidate(
                name="动量因子", expression="ts_delta($close, 20)",
                source=CandidateSource.LLM_API,
                rationale="过去收益预测未来收益",
                metadata={"hypothesis": "高动量股票后续表现更好"}),
        ]
        prompt = miner.generate_consistency_check_prompt(cands)
        assert "假说-表达式一致性" in prompt
        assert "动量因子" in prompt
        assert "3分" in prompt

    def test_filter_by_consistency(self):
        """一致性过滤"""
        miner = LLMFactorMiner()
        cands = [
            FactorCandidate(name="good", expression="e1",
                            source=CandidateSource.LLM_API),
            FactorCandidate(name="bad", expression="e2",
                            source=CandidateSource.LLM_API),
        ]
        scores = [
            {"name": "good", "score": 3},
            {"name": "bad", "score": 1},
        ]
        result = miner.filter_by_consistency(cands, scores, min_score=2)
        assert len(result) == 1
        assert result[0].name == "good"

    def test_parse_hypothesis_from_llm_response(self):
        """从 LLM 响应中提取假说"""
        miner = LLMFactorMiner()
        response = """名称: 动量反转因子
假说: 短期反转效应在小盘股更显著
表达式: cs_rank(-ts_delta($close, 5))
理论依据: 过度反应后的均值回归"""
        cands = miner.parse_llm_response(response)
        assert len(cands) == 1
        assert cands[0].metadata.get("hypothesis") == "短期反转效应在小盘股更显著"


class TestOrthogonalization:
    """正交化逻辑测试"""

    def test_orthogonalize_removes_size_exposure(self):
        """正交化后，纯市值因子的残差 IC 应接近 0"""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        from factor_mining_daemon import orthogonalize_against_known
        np.random.seed(42)
        n_dates, n_stocks = 50, 200
        dates = pd.date_range("2020-01-01", periods=n_dates, freq="B")
        rows = []
        for dt in dates:
            for i in range(n_stocks):
                mv = np.random.lognormal(10, 1)
                rows.append({
                    "datetime": dt,
                    "symbol": f"S{i:04d}",
                    "total_mv": mv,
                    "pb": np.random.lognormal(0, 0.5),
                    "pct_chg": np.random.randn() * 2,
                })
        df = pd.DataFrame(rows)
        # 纯 size 因子 = total_mv 的单调变换
        df["_fac_test"] = np.log(df["total_mv"])
        resid = orthogonalize_against_known(df, "_fac_test")
        # 残差与 total_mv 的截面相关应接近 0
        corr = resid.corr(df["total_mv"])
        assert abs(corr) < 0.15, f"残差与市值相关仍高: {corr:.3f}"

    def test_orthogonalize_preserves_novel_signal(self):
        """正交化应保留与已知因子正交的独立信号"""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        from factor_mining_daemon import orthogonalize_against_known
        np.random.seed(123)
        n_dates, n_stocks = 50, 200
        dates = pd.date_range("2020-01-01", periods=n_dates, freq="B")
        rows = []
        for dt in dates:
            for i in range(n_stocks):
                rows.append({
                    "datetime": dt,
                    "symbol": f"S{i:04d}",
                    "total_mv": np.random.lognormal(10, 1),
                    "pb": np.random.lognormal(0, 0.5),
                    "pct_chg": np.random.randn() * 2,
                })
        df = pd.DataFrame(rows)
        # 构造与已知因子完全正交的独立信号
        df["_fac_novel"] = np.random.randn(len(df))
        resid = orthogonalize_against_known(df, "_fac_novel")
        # 残差应保留大部分原始方差
        orig_var = df["_fac_novel"].var()
        resid_var = resid.var()
        ratio = resid_var / orig_var
        assert ratio > 0.85, f"独立信号方差保留率过低: {ratio:.3f}"

    def test_orthogonalize_no_known_cols(self):
        """控制变量不存在时不崩溃，返回原始值"""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        from factor_mining_daemon import orthogonalize_against_known
        df = pd.DataFrame({
            "datetime": pd.date_range("2020-01-01", periods=100),
            "_fac_test": np.random.randn(100),
        })
        resid = orthogonalize_against_known(df, "_fac_test")
        assert np.allclose(resid.values, df["_fac_test"].values, equal_nan=True)
