"""QMT 双券商配置模型。

敏感信息 (account_id, password) 通过环境变量注入，
YAML 中仅包含非敏感字段。见 §8.1 SO-3 约束。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class QMTBrokerConfig:
    """单个券商的 QMT 配置。"""
    broker_name: str = ""
    session_id: int = 100
    account_type: str = "STOCK"  # STOCK / CREDIT
    userdata_path: str = ""
    # 以下从环境变量读取
    account_id_env: str = ""  # env var name for account_id

    @property
    def account_id(self) -> str:
        return os.environ.get(self.account_id_env, "")

    @property
    def userdata_path_resolved(self) -> str:
        return os.path.expandvars(self.userdata_path)


@dataclass
class RoutingRule:
    """券商路由规则。"""
    pattern: str = "*"  # 标的匹配 (glob or regex)
    asset_type: str = "*"  # cn_equity / convertible_bond / *
    strategy: str = "*"  # 策略名匹配
    broker: str = ""  # 路由到哪个 broker_name


@dataclass
class QMTDualBrokerConfig:
    """双券商配置。"""
    brokers: list[QMTBrokerConfig] = field(default_factory=list)
    default_broker: str = ""
    routing_rules: list[RoutingRule] = field(default_factory=list)

    def get_broker(self, name: str) -> QMTBrokerConfig | None:
        for b in self.brokers:
            if b.broker_name == name:
                return b
        return None

    def route(self, ts_code: str, asset_type: str = "*", strategy: str = "*") -> str:
        """根据规则路由到券商。"""
        for rule in self.routing_rules:
            if rule.asset_type != "*" and rule.asset_type != asset_type:
                continue
            if rule.strategy != "*" and rule.strategy != strategy:
                continue
            if rule.pattern == "*" or ts_code.startswith(rule.pattern):
                return rule.broker
        return self.default_broker

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QMTDualBrokerConfig:
        brokers = [
            QMTBrokerConfig(**b) for b in data.get("brokers", [])
        ]
        rules = [
            RoutingRule(**r) for r in data.get("routing_rules", [])
        ]
        return cls(
            brokers=brokers,
            default_broker=data.get("default_broker", ""),
            routing_rules=rules,
        )
