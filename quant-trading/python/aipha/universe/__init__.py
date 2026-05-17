"""
Universe 模块

股票池策略管理：定义可交易标的集合的过滤规则。
"""

from aipha.universe.exclusions import (
    DelistedExclusionRule,
    ForceRedeemExclusionRule,
    IndexMemberExclusionRule,
    ManualExclusionRule,
    NearMaturityExclusionRule,
    STExclusionRule,
    SuspendedExclusionRule,
    load_manual_exclusion_from_config,
)
from aipha.universe.policy import (
    CompositeUniversePolicy,
    ExclusionReason,
    ExclusionRecord,
    ExclusionRule,
    FilterResult,
    UniversePolicy,
)

__all__ = [
    # Policy
    "UniversePolicy",
    "CompositeUniversePolicy",
    "ExclusionRule",
    "ExclusionReason",
    "ExclusionRecord",
    "FilterResult",
    # Exclusion Rules
    "STExclusionRule",
    "DelistedExclusionRule",
    "SuspendedExclusionRule",
    "ManualExclusionRule",
    "ForceRedeemExclusionRule",
    "IndexMemberExclusionRule",
    "NearMaturityExclusionRule",
    # Config Loading
    "load_manual_exclusion_from_config",
]
