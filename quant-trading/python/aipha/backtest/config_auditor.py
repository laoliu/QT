"""回测配置审计器。

扫描 YAML 配置文件，检测常见的 Alpha 真实性反模式：
- 单因子权重过高（size beta）
- 滑点设置不切实际（< 5bps）
- 缺少风控配置
- 起始日期硬编码等

与回测引擎解耦：只读取 YAML → 输出审计报告。

用法::

    findings = audit_config(config_dict)
    for f in findings:
        print(f"{f.severity}: {f.message}")

    # 批量扫描
    report = audit_config_directory("configs/backtest/")
    print(report.summary())
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ============================================================================
# 审计发现
# ============================================================================


@dataclass(frozen=True, slots=True)
class AuditFinding:
    """单条审计发现。

    Attributes:
        severity: 严重等级 ("error" | "warning" | "info")
        category: 类别 (factor_exposure | slippage | risk | general)
        message: 描述
        config_path: 来源配置文件路径（可选）
        field_path: 问题字段路径（如 "strategy.parameters.weight_small_cap"）
        value: 当前值
        suggestion: 建议值或修复方法
    """

    severity: str
    category: str
    message: str
    config_path: str = ""
    field_path: str = ""
    value: Any = None
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
        }
        if self.config_path:
            d["config_path"] = self.config_path
        if self.field_path:
            d["field_path"] = self.field_path
        if self.value is not None:
            d["value"] = self.value
        if self.suggestion:
            d["suggestion"] = self.suggestion
        return d


@dataclass
class ConfigAuditReport:
    """批量配置审计报告。

    Attributes:
        findings: 所有审计发现
        configs_scanned: 扫描的配置数
    """

    findings: list[AuditFinding] = field(default_factory=list)
    configs_scanned: int = 0

    @property
    def error_count(self) -> int:
        """错误级别发现数。"""
        return sum(1 for f in self.findings if f.severity == "error")

    @property
    def warning_count(self) -> int:
        """警告级别发现数。"""
        return sum(1 for f in self.findings if f.severity == "warning")

    def summary(self) -> str:
        """返回审计摘要字符串。"""
        return (
            f"扫描 {self.configs_scanned} 个配置: "
            f"{self.error_count} errors, "
            f"{self.warning_count} warnings, "
            f"{len(self.findings)} total findings"
        )

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "summary": self.summary(),
            "configs_scanned": self.configs_scanned,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "findings": [f.to_dict() for f in self.findings],
        }


# ============================================================================
# 审计规则
# ============================================================================

# 因子权重 key → (YAML 路径模式, 描述)
_FACTOR_WEIGHT_KEYS = {
    "weight_small_cap": "小市值因子",
    "weight_size": "市值因子",
    "weight_momentum": "动量因子",
    "weight_volatility": "波动率因子",
    "weight_quality": "质量因子",
    "weight_value": "价值因子",
    "weight_turnover": "换手率因子",
}

# 单因子权重超过此值 → error
_MAX_SINGLE_FACTOR_WEIGHT = 0.40

# 滑点最低合理值（bps）
_MIN_REALISTIC_SLIPPAGE = 5.0

# 推荐 A 股最低滑点
_RECOMMENDED_SLIPPAGE = {
    "000300.SH": 10.0,  # 沪深300 → 10bps
    "000905.SH": 15.0,  # 中证500 → 15bps
    "000852.SH": 25.0,  # 中证1000 → 25bps
}


def _get_nested(d: dict, *keys: str, default: Any = None) -> Any:
    """安全地从嵌套 dict 取值。"""
    current = d
    for k in keys:
        if isinstance(current, dict) and k in current:
            current = current[k]
        else:
            return default
    return current


# ============================================================================
# 审计函数
# ============================================================================


def audit_config(
    config: dict[str, Any],
    *,
    config_path: str = "",
) -> list[AuditFinding]:
    """审计单个回测配置 dict，返回发现列表。

    Args:
        config: YAML/JSON 配置（原始 dict）
        config_path: 配置文件路径（用于报告）
    """
    findings: list[AuditFinding] = []

    # --- 1) 因子权重检查 ---
    params = _get_nested(config, "strategy", "parameters", default={})
    if not isinstance(params, dict):
        params = _get_nested(config, "strategy", "params", default={})
    if isinstance(params, dict):
        for key, desc in _FACTOR_WEIGHT_KEYS.items():
            weight = params.get(key)
            if weight is not None and isinstance(weight, (int, float)):
                if weight > _MAX_SINGLE_FACTOR_WEIGHT:
                    findings.append(AuditFinding(
                        severity="error",
                        category="factor_exposure",
                        message=(
                            f"{desc}({key})权重 {weight:.0%} "
                            f"超过上限 {_MAX_SINGLE_FACTOR_WEIGHT:.0%}，"
                            f"策略可能只是在做 beta 暴露"
                        ),
                        config_path=config_path,
                        field_path=f"strategy.parameters.{key}",
                        value=weight,
                        suggestion=f"建议降至 ≤ {_MAX_SINGLE_FACTOR_WEIGHT:.0%}",
                    ))

    # --- 2) 滑点检查 ---
    slippage_bps = _get_slippage_bps(config)
    if slippage_bps is not None:
        if slippage_bps < _MIN_REALISTIC_SLIPPAGE:
            findings.append(AuditFinding(
                severity="error",
                category="slippage",
                message=(
                    f"滑点 {slippage_bps:.1f}bps 低于 A 股最低合理值 "
                    f"{_MIN_REALISTIC_SLIPPAGE:.1f}bps"
                ),
                config_path=config_path,
                field_path="slippage/cost.slippage_bps",
                value=slippage_bps,
                suggestion=f"至少设为 {_MIN_REALISTIC_SLIPPAGE:.0f}bps",
            ))

        # 按基准指数推荐更合理的滑点
        base_index = _get_base_index(config)
        if base_index and base_index in _RECOMMENDED_SLIPPAGE:
            recommended = _RECOMMENDED_SLIPPAGE[base_index]
            if slippage_bps < recommended:
                findings.append(AuditFinding(
                    severity="warning",
                    category="slippage",
                    message=(
                        f"基准 {base_index} 推荐滑点 ≥ {recommended:.0f}bps，"
                        f"当前 {slippage_bps:.1f}bps 偏低"
                    ),
                    config_path=config_path,
                    field_path="slippage/cost.slippage_bps",
                    value=slippage_bps,
                    suggestion=f"建议 ≥ {recommended:.0f}bps",
                ))

    # --- 3) 风控配置检查 ---
    risk = config.get("risk", {})
    risk_constraints = config.get("risk_constraints", {})
    has_risk = isinstance(risk, dict) and risk.get("enabled", True)
    has_risk_constraints = isinstance(risk_constraints, dict) and risk_constraints

    if not has_risk and not has_risk_constraints:
        findings.append(AuditFinding(
            severity="warning",
            category="risk",
            message="未配置风控约束（risk/risk_constraints 均缺失）",
            config_path=config_path,
        ))

    # 止损检查
    stop_loss = (
        _get_nested(config, "risk", "stop_loss_pct")
        or _get_nested(config, "strategy", "parameters", "stop_loss_pct")
        or _get_nested(config, "strategy", "params", "stop_loss_pct")
    )
    if stop_loss is None or (isinstance(stop_loss, (int, float)) and stop_loss >= 0.50):
        findings.append(AuditFinding(
            severity="warning",
            category="risk",
            message="未设置有效止损或止损 ≥ 50%（形同虚设）",
            config_path=config_path,
            field_path="risk.stop_loss_pct",
            value=stop_loss,
            suggestion="建议设置 10%~25% 止损",
        ))

    # --- 4) 策略名存在性 ---
    strategy_name = _get_nested(config, "strategy", "name")
    if not strategy_name:
        strategy_name = _get_nested(config, "strategy", "type")
    if not strategy_name:
        findings.append(AuditFinding(
            severity="error",
            category="general",
            message="缺少 strategy.name",
            config_path=config_path,
        ))

    return findings


def audit_config_directory(
    directory: str | Path,
    *,
    pattern: str = "*.yml",
) -> ConfigAuditReport:
    """批量扫描目录下所有配置文件。

    Args:
        directory: 配置目录
        pattern: glob 模式

    Returns:
        ConfigAuditReport
    """
    directory = Path(directory)
    report = ConfigAuditReport()

    if not directory.is_dir():
        logger.warning("配置目录不存在: %s", directory)
        return report

    for path in sorted(directory.glob(pattern)):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
            if not isinstance(raw, dict):
                continue
            findings = audit_config(raw, config_path=str(path))
            report.findings.extend(findings)
            report.configs_scanned += 1
        except Exception as e:
            logger.warning("解析配置失败 %s: %s", path, e)
            report.findings.append(AuditFinding(
                severity="error",
                category="general",
                message=f"YAML 解析失败: {e}",
                config_path=str(path),
            ))
            report.configs_scanned += 1

    return report


# ============================================================================
# 辅助函数
# ============================================================================


def _get_slippage_bps(config: dict[str, Any]) -> float | None:
    """从多种配置格式中提取滑点（bps）。"""
    # 格式 1: slippage_model.bps
    bps = _get_nested(config, "slippage_model", "bps")
    if bps is not None:
        return float(bps)

    # 格式 2: slippage.bps
    bps = _get_nested(config, "slippage", "bps")
    if bps is not None:
        return float(bps)

    # 格式 3: cost.slippage_bps
    bps = _get_nested(config, "cost", "slippage_bps")
    if bps is not None:
        return float(bps)

    return None


def _get_base_index(config: dict[str, Any]) -> str | None:
    """提取基准指数代码。"""
    # 格式 1: market.instruments.index
    idx = _get_nested(config, "market", "instruments", "index")
    if idx:
        return str(idx)

    # 格式 2: universe.base_index
    idx = _get_nested(config, "universe", "base_index")
    if idx:
        return str(idx)

    # 格式 3: data.base_index
    idx = _get_nested(config, "data", "base_index")
    if idx:
        return str(idx)

    return None
