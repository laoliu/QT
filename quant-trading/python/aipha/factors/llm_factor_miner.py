"""LLM 因子挖掘编排器 — 整合 RD-Agent/LLM 输出与 IC Trap Guard 验证管道。

核心职责：
1. 从 RD-Agent fin_factor 输出目录导入因子候选
2. 解析/规范化因子表达式（支持 Qlib/Alpha101 格式）
3. 通过 5 阶段 IC Trap Guard 管道验证因子
4. 管理知识库注入（FactorEngine 思路: CF 结论 + IC 历史）
5. 多阶段提示生成（CogAlpha 风格 + MCTS 子树回避）
6. 挖掘战役管理与统计报告

架构位置：
    LLM / RD-Agent
        ↓ (factor expressions)
    FactorExpressionParser  →  AST Dedup  →  IC Trap Guard
        ↓ (validated factors)
    FactorRegistry / ML Pipeline

参考：
- RD-Agent (NeurIPS 2025) fin_factor 模式
- FactorEngine (arXiv:2603.16365) 知识注入 + 经验库
- CogAlpha (arXiv:2511.18850) 多阶段提示
- LLM+MCTS (arXiv:2505.11122) 频繁子树回避
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import yaml

from aipha.factors.ast_dedup import ASTFactorDeduplicator
from aipha.factors.factor_expression_parser import FactorExpressionParser, ParsedFactor
from aipha.factors.ic_trap_guard import GuardCheckResult, GuardConfig, GuardVerdict, ICTrapGuard

logger = logging.getLogger(__name__)

# ── 字段注册表加载（单一来源: configs/mining/field_registry.yml）────

_REGISTRY_PATH = Path(__file__).resolve().parents[3] / "configs" / "mining" / "field_registry.yml"
_registry_cache: dict | None = None


def load_field_registry(path: Path | str | None = None) -> dict:
    """加载字段注册表 YAML，带内存缓存。"""
    global _registry_cache
    if _registry_cache is not None and path is None:
        return _registry_cache
    p = Path(path) if path else _REGISTRY_PATH
    if not p.exists():
        logger.warning("字段注册表不存在: %s，使用内置默认值", p)
        return {}
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if path is None:
        _registry_cache = data
    return data


def _fields_from_registry(reg: dict) -> list[str]:
    """从注册表提取扁平化字段列表。"""
    fields_section = reg.get("fields", {})
    result: list[str] = []
    for group in fields_section.values():
        if isinstance(group, list):
            result.extend(group)
    return result


def _operators_from_registry(reg: dict) -> list[str]:
    """从注册表提取扁平化算子列表。"""
    ops_section = reg.get("operators", {})
    result: list[str] = []
    for group in ops_section.values():
        if isinstance(group, list):
            result.extend(group)
    return result


# ── 数据类 ────────────────────────────────────────────────


class CandidateSource(Enum):
    """因子候选来源"""
    RDAGENT = "rdagent"
    LLM_API = "llm_api"
    MANUAL = "manual"
    GP_MINING = "gp_mining"


@dataclass
class FactorCandidate:
    """因子候选"""
    name: str
    expression: str
    source: CandidateSource
    rationale: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class ValidationResult:
    """单个因子的验证结果"""
    candidate: FactorCandidate
    parsed: Optional[ParsedFactor] = None
    guard_result: Optional[GuardCheckResult] = None
    ic_value: float = 0.0
    icir_value: float = 0.0
    passed: bool = False
    failure_reason: str = ""


@dataclass
class CampaignResult:
    """一轮挖掘战役结果"""
    campaign_id: str
    total_candidates: int = 0
    parse_failures: int = 0
    guard_failures: int = 0
    passed: int = 0
    results: list[ValidationResult] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def pass_rate(self) -> float:
        return self.passed / max(self.total_candidates, 1)

    def summary(self) -> dict:
        return {
            "campaign_id": self.campaign_id,
            "total": self.total_candidates,
            "parsed": self.total_candidates - self.parse_failures,
            "passed_guard": self.passed,
            "pass_rate": round(self.pass_rate, 3),
            "elapsed_s": round(self.elapsed_seconds, 1),
        }


@dataclass
class MiningConfig:
    """挖掘配置

    默认值从 configs/mining/field_registry.yml 加载（单一来源）。
    若 YAML 不存在则回退到内置 fallback。
    """
    # IC Trap Guard 配置 — 从注册表加载统一阈值
    guard: GuardConfig = field(default_factory=GuardConfig.from_registry)
    # AST 去重阈值
    ast_similarity_threshold: float = 0.85
    # 可用算子列表（传给 LLM prompt）— 从注册表加载
    available_operators: list[str] = field(default_factory=lambda: (
        _operators_from_registry(load_field_registry())
        or [
            "ts_delay", "ts_delta", "ts_sum", "ts_mean", "ts_std",
            "ts_min", "ts_max", "ts_rank", "ts_corr", "ts_cov",
            "ts_slope", "ts_decay_linear", "ts_resi", "ts_rsquare",
            "ts_argmax", "ts_argmin", "ts_product", "ts_log", "ts_abs",
            "ts_sign", "cs_rank", "cs_zscore", "cs_scale",
            "safe_div", "pow1", "pow2", "quesval",
        ]
    ))
    # 可用字段 — 从注册表加载（统一用 turnover_rate）
    available_fields: list[str] = field(default_factory=lambda: (
        _fields_from_registry(load_field_registry())
        or [
            "open", "high", "low", "close", "pre_close",
            "volume", "amount", "turnover_rate", "pct_chg",
            "pe_ttm", "pb", "total_mv", "circ_mv", "dv_ratio", "dv_ttm",
        ]
    ))
    # 挖掘方向提示 — 从注册表加载
    directions: list[str] = field(default_factory=lambda: (
        load_field_registry().get("directions", None)
        or [
            "价量动量", "波动率均值回复", "流动性异常", "量价背离",
            "价值-动量交互", "规模-流动性交互", "事件驱动",
        ]
    ))
    # 每轮候选数上限
    candidates_per_round: int = 20
    # RD-Agent 输出目录
    rdagent_output_dir: str = ""


# ── 知识库（FactorEngine 思路）──────────────────────────


class FactorKnowledgeBase:
    """因子挖掘经验知识库 — FactorEngine 风格的轨迹知识注入。

    存储：
    - 已验证因子（通过 IC Trap Guard 的因子表达式 + IC/ICIR）
    - 失败因子（被拒因子 + 拒绝原因）  → 避免重复探索
    - CF 结论（项目已确认的定量结论）  → 先验约束注入
    """

    def __init__(self) -> None:
        self._passed: list[dict] = []
        self._failed: list[dict] = []
        self._cf_constraints: list[str] = []

    def add_passed(self, name: str, expr: str, ic: float, icir: float) -> None:
        self._passed.append({
            "name": name, "expression": expr,
            "ic": round(ic, 4), "icir": round(icir, 4),
        })

    def add_failed(self, name: str, expr: str, reason: str) -> None:
        self._failed.append({
            "name": name, "expression": expr, "reason": reason,
        })

    def add_cf_constraint(self, text: str) -> None:
        self._cf_constraints.append(text)

    def load_cf_constraints_default(self) -> None:
        """加载 Aipha-Q 项目 BINDING 结论作为先验约束。"""
        defaults = [
            "CF-020: 新增因子可能提升全截面IC但降低Top-K组合AR(IC Trap)",
            "CF-022: 因子同质化(相关>0.7)会稀释Top-K选股浓度",
            "CF-026: LightGBM 对>200特征过拟合, 建议≤150有效特征",
            "CF-028: 动量因子在A股有效但需控制换手率",
            "CF-034: 波动率因子在CSI1000/2000有负向预测力",
            "CF-035: 基本面因子(pe_ttm/pb)在小盘股IC更高,大盘股需额外交互",
            "CF-036: 价量-基本面交互因子比纯价量因子相关性更低,分散化更好",
            "CF-037: 因子嵌套深度>5或AST节点>15的复杂因子易过拟合",
        ]
        for d in defaults:
            self._cf_constraints.append(d)

    def format_for_prompt(self, max_examples: int = 5) -> str:
        """将知识库格式化为 LLM prompt 片段。"""
        parts: list[str] = []
        # 先验约束
        if self._cf_constraints:
            parts.append("## 项目先验约束（必须遵守）")
            for c in self._cf_constraints:
                parts.append(f"- {c}")
        # 成功案例
        if self._passed:
            parts.append("\n## 已验证高质量因子（参考风格）")
            for p in self._passed[-max_examples:]:
                parts.append(f"- {p['name']}: `{p['expression']}` IC={p['ic']}")
        # 失败案例
        if self._failed:
            parts.append("\n## 已拒绝因子（避免类似构造）")
            for f in self._failed[-max_examples:]:
                parts.append(f"- {f['name']}: `{f['expression']}` 原因={f['reason']}")
        # 失败模式分析（闭环反馈）
        failure_analysis = self._analyze_failure_patterns()
        if failure_analysis:
            parts.append("\n## 失败模式分析（请避免以下模式）")
            for pattern, count in failure_analysis.items():
                parts.append(f"- {pattern}: 已出现 {count} 次")
        return "\n".join(parts)

    def _analyze_failure_patterns(self) -> dict[str, int]:
        """分析失败因子的分布模式，用于闭环反馈。"""
        patterns: dict[str, int] = {}
        for f in self._failed:
            reason = f.get("reason", "")
            if "fail_ic" in reason:
                patterns["IC/ICIR 不达标"] = patterns.get("IC/ICIR 不达标", 0) + 1
            elif "fail_ast" in reason or "AST 重复" in reason:
                patterns["与已有因子结构重复"] = patterns.get("与已有因子结构重复", 0) + 1
            elif "fail_corr" in reason:
                patterns["与已有因子数值高度相关"] = patterns.get("与已有因子数值高度相关", 0) + 1
            elif "fail_hlz" in reason:
                patterns["未通过HLZ多重检验校正"] = patterns.get("未通过HLZ多重检验校正", 0) + 1
            elif "fail_ar" in reason:
                patterns["触发IC Trap(AR下降)"] = patterns.get("触发IC Trap(AR下降)", 0) + 1
            elif "fail_complexity" in reason:
                patterns["表达式过于复杂"] = patterns.get("表达式过于复杂", 0) + 1
            elif "解析失败" in reason:
                patterns["表达式语法错误"] = patterns.get("表达式语法错误", 0) + 1
            else:
                patterns["其他原因"] = patterns.get("其他原因", 0) + 1
        return {k: v for k, v in sorted(
            patterns.items(), key=lambda x: -x[1]) if v >= 2}


# ── 主编排器 ────────────────────────────────────────────


class LLMFactorMiner:
    """LLM 因子挖掘编排器。

    整合 RD-Agent 输出导入、LLM 多阶段 Prompt 生成、
    IC Trap Guard 验证管道，实现端到端因子挖掘闭环。
    """

    def __init__(self, config: Optional[MiningConfig] = None) -> None:
        self.config = config or MiningConfig()
        self.parser = FactorExpressionParser()
        self.deduplicator = ASTFactorDeduplicator(
            similarity_threshold=self.config.ast_similarity_threshold,
        )
        self.guard = ICTrapGuard(self.config.guard)
        self.knowledge = FactorKnowledgeBase()
        self.knowledge.load_cf_constraints_default()
        self._campaigns: list[CampaignResult] = []

    # ------------------------------------------------------------------
    # 候选导入
    # ------------------------------------------------------------------

    def ingest_rdagent_output(self, output_dir: Optional[str] = None) -> list[FactorCandidate]:
        """从 RD-Agent fin_factor 输出目录读取因子候选。

        RD-Agent 输出结构:
            output_dir/
              factor_<N>/
                factor.py        (因子代码, 含 expression)
                result.pkl/.csv  (因子值)
        """
        target = output_dir or self.config.rdagent_output_dir
        if not target or not Path(target).exists():
            logger.warning("RD-Agent 输出目录不存在或未指定: %s", target)
            return []

        candidates: list[FactorCandidate] = []
        base = Path(target)
        for factor_dir in sorted(base.iterdir()):
            if not factor_dir.is_dir():
                continue
            factor_py = factor_dir / "factor.py"
            if not factor_py.exists():
                continue
            expr = self._extract_expression_from_rdagent(factor_py)
            if expr:
                candidates.append(FactorCandidate(
                    name=factor_dir.name,
                    expression=expr,
                    source=CandidateSource.RDAGENT,
                    metadata={"dir": str(factor_dir)},
                ))
        logger.info("从 RD-Agent 导入 %d 个因子候选", len(candidates))
        return candidates

    def add_manual_candidates(
        self, expressions: dict[str, str],
    ) -> list[FactorCandidate]:
        """手动添加因子候选。expressions: {name: expression}"""
        return [
            FactorCandidate(name=n, expression=e, source=CandidateSource.MANUAL)
            for n, e in expressions.items()
        ]

    @staticmethod
    def _extract_expression_from_rdagent(factor_py: Path) -> str:
        """从 RD-Agent 的 factor.py 提取因子表达式字符串。"""
        text = factor_py.read_text(encoding="utf-8", errors="ignore")
        # 匹配常见模式: expression = "..." 或 EXPRESSION = "..."
        import re
        m = re.search(
            r'(?:expression|EXPRESSION|formula)\s*=\s*["\'](.+?)["\']',
            text,
        )
        if m:
            return m.group(1)
        # 备选: 查找 def calculate 中的注释
        m2 = re.search(r'#\s*formula:\s*(.+)', text)
        return m2.group(1).strip() if m2 else ""

    # ------------------------------------------------------------------
    # Prompt 生成（CogAlpha 多阶段 + MCTS 子树回避）
    # ------------------------------------------------------------------

    def generate_mining_prompt(
        self,
        direction: str = "价量动量",
        n_factors: int = 10,
    ) -> str:
        """生成 LLM 因子挖掘提示（可直接传给 DeepSeek/GPT-4）。"""
        ops = ", ".join(self.config.available_operators)
        fields = ", ".join(self.config.available_fields)
        kb_section = self.knowledge.format_for_prompt()

        # 动态构建字段列表（从注册表加载，不再硬编码）
        reg = load_field_registry()
        fields_section = reg.get("fields", {})
        price_fields = fields_section.get("price", [])
        vol_fields = fields_section.get("volume", [])
        daily_fields = fields_section.get("daily_indicator", [])
        funda_fields = fields_section.get("fundamentals", [])

        price_vol_str = ", ".join(price_fields + vol_fields + daily_fields)
        funda_str = ", ".join(funda_fields) if funda_fields else "(待接入)"

        prompt = f"""你是一位量化因子研究专家。请为 A 股/可转债市场设计 {n_factors} 个新的阿尔法因子。

## 挖掘方向
{direction}

## 可用算子（必须且仅使用以下算子）
{ops}

## 可用字段（用 $field 引用）
**价量指标字段**: {price_vol_str}
**基本面字段**: {funda_str}

## 输出格式（每个因子）
```
名称: <4-8字描述>
假说: <1句经济学假说，说明因子为何有效>
表达式: <使用上述算子和字段的公式>
理论依据: <1-2句解释假说的经济学逻辑>
```

## 要求
1. 每个因子必须有清晰的先验经济学解释（禁止纯数据挖掘）
2. 先写假说，再设计表达式——因子必须与假说一致
3. 避免与下方已有因子结构相似（AST 相似度需 < 0.85）
4. 优先使用 cs_rank 包裹以增强截面可比性
5. 窗口参数建议: 5/10/20/60，避免极端值
6. 时序算子的第二参数是窗口长度（整数）
7. 因子表达式嵌套深度不超过 5 层，AST 节点数不超过 15
8. 鼓励混合价量与基本面字段构建交互因子

{kb_section}
"""
        return prompt.strip()

    def parse_llm_response(self, response_text: str) -> list[FactorCandidate]:
        """解析 LLM 返回的因子列表文本。"""
        import re
        candidates: list[FactorCandidate] = []
        # 按 "名称:" 分块
        blocks = re.split(r'\n(?=名称[:：])', response_text)
        for block in blocks:
            name_m = re.search(r'名称[:：]\s*(.+)', block)
            expr_m = re.search(r'表达式[:：]\s*(.+)', block)
            reason_m = re.search(r'理论依据[:：]\s*(.+)', block)
            hypo_m = re.search(r'假说[:：]\s*(.+)', block)
            if name_m and expr_m:
                hypothesis = hypo_m.group(1).strip() if hypo_m else ""
                rationale = reason_m.group(1).strip() if reason_m else ""
                candidates.append(FactorCandidate(
                    name=name_m.group(1).strip(),
                    expression=expr_m.group(1).strip().strip('`'),
                    source=CandidateSource.LLM_API,
                    rationale=rationale,
                    metadata={"hypothesis": hypothesis} if hypothesis else {},
                ))
        logger.info("从 LLM 响应解析出 %d 个因子候选", len(candidates))
        return candidates

    def generate_feedback_prompt(
        self,
        direction: str = "价量动量",
        n_factors: int = 10,
    ) -> str:
        """生成带闭环反馈的挖掘提示（含上轮失败分析）。

        与 generate_mining_prompt 的区别：
        - 自动注入上一轮 campaign 的通过率和失败原因分布
        - 根据失败模式调整引导策略
        """
        base_prompt = self.generate_mining_prompt(direction, n_factors)

        # 注入上轮 campaign 反馈
        if self._campaigns:
            last = self._campaigns[-1]
            feedback_parts = [
                "\n## 上轮挖掘反馈（请据此调整策略）",
                f"- 上轮通过率: {last.passed}/{last.total_candidates} "
                f"({last.pass_rate*100:.0f}%)",
            ]
            # 统计上轮各阶段失败数
            fail_reasons: dict[str, int] = {}
            for vr in last.results:
                if not vr.passed and vr.failure_reason:
                    key = vr.failure_reason.split(":")[0]
                    fail_reasons[key] = fail_reasons.get(key, 0) + 1
            if fail_reasons:
                feedback_parts.append("- 失败原因分布:")
                for reason, cnt in sorted(
                    fail_reasons.items(), key=lambda x: -x[1],
                ):
                    feedback_parts.append(f"  - {reason}: {cnt} 个")
            # 自适应引导
            total_failed = last.total_candidates - last.passed
            if total_failed > 0:
                parse_ratio = last.parse_failures / total_failed
                if parse_ratio > 0.3:
                    feedback_parts.append(
                        "- **建议**: 上轮解析失败率高，请严格使用给定算子语法"
                    )
                guard_ratio = last.guard_failures / total_failed
                if guard_ratio > 0.5:
                    feedback_parts.append(
                        "- **建议**: 上轮因子质量不足，请增加创新性，"
                        "尝试不同字段组合和更深层的经济学假说"
                    )
            base_prompt += "\n" + "\n".join(feedback_parts)
        return base_prompt

    def generate_consistency_check_prompt(
        self, candidates: list[FactorCandidate],
    ) -> str:
        """生成假说-因子一致性验证提示（AlphaAgent 风格）。

        将候选因子及其假说/表达式交给 LLM 进行一致性打分。
        返回可直接发给 LLM 的验证 prompt，LLM 返回 JSON 评分。
        """
        items: list[str] = []
        for i, c in enumerate(candidates):
            hypo = c.metadata.get("hypothesis", c.rationale)
            items.append(
                f"{i+1}. 名称: {c.name}\n"
                f"   假说: {hypo}\n"
                f"   表达式: {c.expression}"
            )
        factors_block = "\n".join(items)

        return f"""你是量化因子审查专家。请验证以下因子的"假说-表达式一致性"。

对每个因子，判断其表达式是否真正在度量假说所声称的经济现象。

## 待审查因子
{factors_block}

## 评分标准
- 3分: 表达式精确捕捉了假说描述的经济现象
- 2分: 表达式大致相关但有偏差（如假说说动量但表达式更偏波动率）
- 1分: 表达式与假说几乎无关，属于数据挖掘

## 输出格式（JSON 数组）
```json
[
  {{"name": "因子名", "score": 3, "reason": "一致性说明"}},
  ...
]
```

只输出 JSON，不要其他文字。"""

    def filter_by_consistency(
        self,
        candidates: list[FactorCandidate],
        scores: list[dict],
        min_score: int = 2,
    ) -> list[FactorCandidate]:
        """根据一致性评分过滤候选因子。

        Args:
            candidates: 原始候选列表
            scores: LLM 返回的评分列表 [{"name": ..., "score": N}]
            min_score: 最低通过分数（默认 2）
        """
        score_map = {s["name"]: s.get("score", 0) for s in scores}
        passed = []
        for c in candidates:
            sc = score_map.get(c.name, 0)
            if sc >= min_score:
                c.metadata["consistency_score"] = sc
                passed.append(c)
            else:
                reason = score_map.get(c.name, "未评分")
                logger.info(
                    "一致性过滤: %s 得分=%s < %d, 剔除",
                    c.name, sc, min_score,
                )
                self.knowledge.add_failed(
                    c.name, c.expression,
                    f"假说一致性不足: score={sc}",
                )
        logger.info(
            "一致性过滤: %d/%d 通过 (min_score=%d)",
            len(passed), len(candidates), min_score,
        )
        return passed

    # ------------------------------------------------------------------
    # 验证管道
    # ------------------------------------------------------------------

    def validate_candidate(
        self,
        candidate: FactorCandidate,
        returns: pd.Series,
        existing_factor_df: Optional[pd.DataFrame] = None,
        new_ar: Optional[float] = None,
        factor_values: Optional[pd.Series] = None,
    ) -> ValidationResult:
        """对单个因子候选执行完整 5 阶段验证。

        Parameters
        ----------
        candidate : 因子候选
        returns : 资产收益率序列（用于 IC 计算）
        existing_factor_df : 已有因子值 DataFrame（用于相关性去重）
        new_ar : 加入新因子后的 Top-K 组合 AR（AR Drop 检查）
        factor_values : 因子值序列（若为 None 则尝试执行表达式）
        """
        result = ValidationResult(candidate=candidate)

        # Stage 0: 表达式解析
        parsed = self.parser.parse(candidate.expression, candidate.name)
        result.parsed = parsed
        if not parsed.is_valid:
            result.failure_reason = f"解析失败: {parsed.error_message}"
            self.knowledge.add_failed(
                candidate.name, candidate.expression, result.failure_reason,
            )
            return result

        # Stage 1-5: IC Trap Guard 管道
        if factor_values is not None:
            guard_result = self.guard.check_factor(
                factor_name=candidate.name,
                factor_values=factor_values,
                forward_returns=returns,
                expression=candidate.expression,
                new_ar=new_ar,
                existing_factor_values=existing_factor_df,
            )
            result.guard_result = guard_result
            ic_checks = guard_result.details.get("checks", {}).get("ic", {})
            result.ic_value = ic_checks.get("ic_mean", 0.0)
            result.icir_value = ic_checks.get("icir", 0.0)

            if guard_result.passed:
                result.passed = True
                self.knowledge.add_passed(
                    candidate.name, candidate.expression,
                    result.ic_value, result.icir_value,
                )
            else:
                result.failure_reason = (
                    f"{guard_result.verdict.value}: "
                    f"{guard_result.details.get('reason', '')}"
                )
                self.knowledge.add_failed(
                    candidate.name, candidate.expression,
                    result.failure_reason,
                )
        else:
            # 无因子值时仅做解析 + AST 去重
            dedup = self.deduplicator.check_duplicate(
                candidate.name, candidate.expression,
            )
            if dedup.is_duplicate:
                result.failure_reason = (
                    f"AST 重复: 与 {dedup.most_similar_name} "
                    f"相似度 {dedup.similarity_score:.2f}"
                )
                self.knowledge.add_failed(
                    candidate.name, candidate.expression,
                    result.failure_reason,
                )
            else:
                result.passed = True
                self.deduplicator.register(
                    candidate.name, candidate.expression,
                )
                self.knowledge.add_passed(
                    candidate.name, candidate.expression, 0.0, 0.0,
                )

        return result

    # ------------------------------------------------------------------
    # 战役管理
    # ------------------------------------------------------------------

    def run_campaign(
        self,
        candidates: list[FactorCandidate],
        returns: Optional[pd.Series] = None,
        existing_factor_df: Optional[pd.DataFrame] = None,
        campaign_id: Optional[str] = None,
    ) -> CampaignResult:
        """运行一轮挖掘验证战役。"""
        cid = campaign_id or f"campaign_{int(time.time())}"
        result = CampaignResult(campaign_id=cid, total_candidates=len(candidates))
        t0 = time.time()

        for cand in candidates:
            vr = self.validate_candidate(
                candidate=cand,
                returns=returns if returns is not None else pd.Series(dtype=float),
                existing_factor_df=existing_factor_df,
            )
            result.results.append(vr)
            if vr.parsed and not vr.parsed.is_valid:
                result.parse_failures += 1
            elif not vr.passed:
                result.guard_failures += 1
            else:
                result.passed += 1

        result.elapsed_seconds = time.time() - t0
        self._campaigns.append(result)
        logger.info(
            "战役 %s 完成: %d/%d 通过 (%.1f%%), 耗时 %.1fs",
            cid, result.passed, result.total_candidates,
            result.pass_rate * 100, result.elapsed_seconds,
        )
        return result

    # ------------------------------------------------------------------
    # 报告与导出
    # ------------------------------------------------------------------

    def get_passed_factors(self) -> list[dict]:
        """获取所有通过验证的因子列表。"""
        passed = []
        for camp in self._campaigns:
            for vr in camp.results:
                if vr.passed and vr.parsed:
                    passed.append({
                        "name": vr.candidate.name,
                        "expression": vr.candidate.expression,
                        "source": vr.candidate.source.value,
                        "rationale": vr.candidate.rationale,
                        "ic": round(vr.ic_value, 4),
                        "icir": round(vr.icir_value, 4),
                    })
        return passed

    def export_report(self, output_path: Optional[str] = None) -> dict:
        """导出挖掘报告 JSON。"""
        report = {
            "total_campaigns": len(self._campaigns),
            "total_candidates": sum(c.total_candidates for c in self._campaigns),
            "total_passed": sum(c.passed for c in self._campaigns),
            "campaigns": [c.summary() for c in self._campaigns],
            "passed_factors": self.get_passed_factors(),
            "knowledge_base": {
                "passed_count": len(self.knowledge._passed),
                "failed_count": len(self.knowledge._failed),
                "cf_constraints": len(self.knowledge._cf_constraints),
            },
        }
        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            logger.info("挖掘报告已导出: %s", output_path)
        return report
