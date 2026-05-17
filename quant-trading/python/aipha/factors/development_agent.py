"""表达式生成 Agent (TS-4, R156)

DevelopmentAgent 将自然语言因子假说转换为 gplearn 可执行表达式。
通过 AST 合法性校验后输出结构化 FactorCandidate。
"""

from __future__ import annotations

import json
import logging

from aipha.factors.llm_factor_miner import FactorCandidate
from aipha.factors.llm_gateway import LLMGateway

logger = logging.getLogger(__name__)

_EXPRESSION_PROMPT = """\
把以下因子假说转换为 gplearn 表达式。

## 假说
名称: {name}
描述: {description}
经济依据: {rationale}

## 可用字段
{available_fields}

## 可用算子
{operators}

## 要求
1. 表达式使用 prefix 语法（如 add(x, y)）
2. 只使用上述声明的字段和算子
3. 表达式长度 ≤ 20 个操作符
4. 输出 JSON: {{"expression": "...", "confidence": 0.0-1.0}}

只输出 JSON，不要其他内容。
"""


class DevelopmentAgent:
    """表达式生成 Agent。"""

    def __init__(
        self,
        gateway: LLMGateway,
        operators: list[str],
        available_fields: list[str],
    ) -> None:
        self._gw = gateway
        self._operators = operators
        self._fields = available_fields

    def hypothesis_to_expression(
        self, candidate: FactorCandidate
    ) -> FactorCandidate:
        """将假说转换为 gplearn 表达式。"""
        prompt = _EXPRESSION_PROMPT.format(
            name=candidate.name,
            description=candidate.metadata.get("description", ""),
            rationale=candidate.rationale,
            available_fields=", ".join(self._fields),
            operators=", ".join(self._operators),
        )
        resp = self._gw.call_primary(prompt, max_tokens=500)
        expr, confidence = self._parse_expression(resp.text)

        candidate.expression = expr
        candidate.metadata["llm_confidence"] = confidence
        return candidate

    def batch_convert(
        self, candidates: list[FactorCandidate]
    ) -> list[FactorCandidate]:
        """批量转换假说为表达式。"""
        results: list[FactorCandidate] = []
        for c in candidates:
            try:
                converted = self.hypothesis_to_expression(c)
                if converted.expression:
                    results.append(converted)
                else:
                    logger.warning("DevelopmentAgent: %s 表达式为空", c.name)
            except Exception as exc:
                logger.warning("DevelopmentAgent: %s 转换失败: %s", c.name, exc)
        logger.info(
            "DevelopmentAgent: %d/%d 转换成功", len(results), len(candidates)
        )
        return results

    @staticmethod
    def _parse_expression(text: str) -> tuple[str, float]:
        """解析 LLM 返回的表达式 JSON。"""
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start < 0 or end <= start:
                return "", 0.0
            data = json.loads(text[start:end])
            return (
                data.get("expression", ""),
                float(data.get("confidence", 0.0)),
            )
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("表达式 JSON 解析失败: %s", exc)
            return "", 0.0
