"""Tech moat evidence chain analysis.

Scores a company's technological moat through a 4-layer evidence
chain: announcement signals, verification metrics, competitor
adoption, and supply chain verification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["TechMoatAnalyzer", "MoatResult"]

# Default signal keywords per layer
_DEFAULT_SIGNALS: Dict[str, List[str]] = {
    "announcement": [
        "专利", "发明", "技术突破", "自主研发", "首创",
        "国产替代", "国际领先", "卡脖子", "芯片", "光刻",
    ],
    "verification": [
        "市场份额", "产能", "良率", "认证", "资质",
        "ISO", "量产", "产线", "出货量", "营收增长",
    ],
    "competitor_adoption": [
        "供应商", "合作", "定点", "中标", "入围",
        "战略合作", "联合开发", "授权", "客户导入",
    ],
    "supply_chain": [
        "供应链", "产业链", "上下游", "核心客户",
        "国际大厂", "苹果", "华为", "特斯拉", "比亚迪",
        "三星", "台积电", "英伟达",
    ],
}

_LAYER_MAX: Dict[str, float] = {
    "announcement": 2.0,
    "verification": 3.0,
    "competitor_adoption": 4.0,
    "supply_chain": 5.0,
}


@dataclass
class MoatResult:
    """Result of tech moat analysis."""

    score: float = 0.0
    grade: str = "很弱"
    layers_satisfied: int = 0
    layer_scores: Dict[str, float] = field(default_factory=dict)
    evidence: Dict[str, List[str]] = field(default_factory=dict)
    symbol: str = ""
    company_name: str = ""


class TechMoatAnalyzer:
    """Analyze tech moat through multi-layer evidence chain.

    Parameters
    ----------
    signals_config : dict or None
        Custom keyword configuration per layer.
    """

    def __init__(
        self, signals_config: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        self._signals = signals_config or _DEFAULT_SIGNALS

    def analyze(
        self,
        news_titles: List[str],
        symbol: str = "",
        company_name: str = "",
    ) -> MoatResult:
        """Analyze news/announcement titles for tech moat evidence.

        Parameters
        ----------
        news_titles : list of str
            Announcement and news headlines to scan.
        """
        evidence: Dict[str, List[str]] = {}
        layer_scores: Dict[str, float] = {}

        for layer, keywords in self._signals.items():
            hits: List[str] = []
            for title in news_titles:
                for kw in keywords:
                    if kw in title and title not in hits:
                        hits.append(title)
                        break
            evidence[layer] = hits
            max_score = _LAYER_MAX.get(layer, 2.0)
            if not hits:
                layer_scores[layer] = 0.0
            elif len(hits) == 1:
                layer_scores[layer] = max_score * 0.5
            elif len(hits) == 2:
                layer_scores[layer] = max_score * 0.75
            else:
                layer_scores[layer] = max_score

        layers_satisfied = sum(1 for s in layer_scores.values() if s > 0)
        total = sum(layer_scores.values())

        # bonus for breadth
        if layers_satisfied >= 3:
            total += 0.5

        total = min(14.0, total)  # max possible ~14
        # normalize to 0-10 scale
        score = total * 10.0 / 14.0

        grade = self._grade(score)

        return MoatResult(
            score=round(score, 2),
            grade=grade,
            layers_satisfied=layers_satisfied,
            layer_scores={k: round(v, 2) for k, v in layer_scores.items()},
            evidence=evidence,
            symbol=symbol,
            company_name=company_name,
        )

    @staticmethod
    def _grade(score: float) -> str:
        if score >= 8.0:
            return "极强"
        if score >= 6.0:
            return "较强"
        if score >= 4.0:
            return "中等"
        if score >= 2.0:
            return "较弱"
        return "很弱"
