"""假说生成 Agent (TS-4, R156)

ResearchAgent 使用 LLM 生成具有经济学依据的因子假说。
每个假说包含：名称、描述、经济理论依据、预期 IC。

配合 FactorKnowledgeBase 实现闭环反馈（避免重复失败模式）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from aipha.factors.llm_factor_miner import (
    CandidateSource,
    FactorCandidate,
    FactorKnowledgeBase,
)
from aipha.factors.llm_gateway import LLMGateway

logger = logging.getLogger(__name__)

_HYPOTHESIS_PROMPT = """\
你是一位资深量化研究员，目标是为中国 A 股市场生成具有经济学依据的因子假说。

## 可用字段
{available_fields}

## 主题
{theme}

## 知识库（已有因子、失败模式）
{kb_context}

## 要求
生成 {n} 个新因子假说，每个假说以 JSON 对象输出，包含：
- "name": snake_case 因子名
- "description": 一句话描述
- "rationale": 经济学依据
- "expected_ic": [下限, 上限]
- "risk": 潜在风险

以 JSON 数组格式输出，不要输出其他内容。

## 禁止
- 纯语法变异（换括号/换顺序）
- 没有经济解释的因子
- 与已有因子名称完全相同
"""


class ResearchAgent:
    """假说生成 Agent。"""

    def __init__(
        self,
        gateway: LLMGateway,
        kb: FactorKnowledgeBase,
        available_fields: list[str],
    ) -> None:
        self._gw = gateway
        self._kb = kb
        self._fields = available_fields

    def generate_hypothesis_batch(
        self,
        n: int = 20,
        theme: str | None = None,
    ) -> list[FactorCandidate]:
        """生成 n 个因子假说。"""
        prompt = _HYPOTHESIS_PROMPT.format(
            available_fields=", ".join(self._fields),
            theme=theme or "通用（盈利/成长/质量/动量/估值）",
            kb_context=self._kb.format_for_prompt(max_examples=10),
            n=n,
        )
        resp = self._gw.call_primary(prompt, max_tokens=3000)
        return self._parse_hypotheses(resp.text)

    def _parse_hypotheses(self, text: str) -> list[FactorCandidate]:
        """解析 LLM 返回的假说 JSON 数组。"""
        candidates: list[FactorCandidate] = []
        try:
            # 尝试提取 JSON 数组
            start = text.find("[")
            end = text.rfind("]") + 1
            if start < 0 or end <= start:
                logger.warning("LLM 响应中未找到 JSON 数组")
                return []
            items = json.loads(text[start:end])
        except json.JSONDecodeError as exc:
            logger.warning("LLM 响应 JSON 解析失败: %s", exc)
            return []

        for item in items:
            if not isinstance(item, dict) or "name" not in item:
                continue
            candidates.append(FactorCandidate(
                name=item["name"],
                expression="",  # 由 DevelopmentAgent 填充
                source=CandidateSource.LLM_API,
                rationale=item.get("rationale", ""),
                metadata={
                    "description": item.get("description", ""),
                    "expected_ic": item.get("expected_ic", []),
                    "risk": item.get("risk", ""),
                    "theme": item.get("theme", ""),
                },
            ))
        logger.info("ResearchAgent 生成 %d 个假说", len(candidates))
        return candidates
