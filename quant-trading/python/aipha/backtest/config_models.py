"""Backtest configuration data models.

All dataclass definitions used by the config-driven backtest runner.
Extracted from config_runner.py for modularity.

Classes:
    DataConfig: 数据源配置
    UniverseConfig: 股票池配置
    StrategyConfig: 策略配置
    CostConfig: 成本配置
    FillModelConfig: 成交模型配置
    RiskConfig: 风控配置
    BacktestRunConfig: 完整回测运行配置
    BacktestRunResult: 回测运行结果
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from aipha.backtest.engine import BacktestResult
from aipha.backtest.quality_gates import QualityReport
from aipha.backtest.report import BacktestReport
from aipha.utils.reproducibility import ExperimentFingerprint


# ============================================================================
# 配置数据类
# ============================================================================


@dataclass
class DataConfig:
    """数据配置

    Attributes:
        source: 数据源名称 (tushare, local, synthetic)
        start_date: 开始日期
        end_date: 结束日期
        symbols: 股票列表（可选，用于限定）
        frequency: 数据频率 (1d, 1m, 等)
        adjust: 复权方式 (qfq, hfq, none)
        cache_dir: 缓存目录（可选）
    """

    source: str
    start_date: date
    end_date: date
    symbols: list[str] | None = None
    frequency: str = "1d"
    adjust: str = "qfq"
    cache_dir: str | None = None


@dataclass
class UniverseConfig:
    """股票池配置

    Attributes:
        policy: 股票池策略名称
        exclude_st: 是否剔除 ST
        exclude_suspended: 是否剔除停牌
        exclude_limit: 是否剔除涨跌停
        exclude_list_path: 手动剔除名单路径
        min_listing_days: 最小上市天数
        base_index: 基准指数代码（用于限定成分股范围）
    """

    policy: str = "default"
    exclude_st: bool = True
    exclude_suspended: bool = True
    exclude_limit: bool = True
    exclude_list_path: str | None = None
    min_listing_days: int = 60
    base_index: str | None = None


@dataclass
class StrategyConfig:
    """策略配置

    Attributes:
        name: 策略名称/类型
        params: 策略参数字典
    """

    name: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class CostConfig:
    """成本配置

    Attributes:
        commission_rate: 佣金率
        commission_min: 最低佣金
        stamp_tax_rate: 印花税率（仅卖出）
        transfer_fee_rate: 过户费率
        slippage_bps: 滑点（基点），当 slippage_model="fixed" 时使用
        slippage_model: 滑点模型名称 ("fixed" | "linear_impact" | "square_root_impact")
        slippage_params: 非 fixed 模型的额外参数
    """

    commission_rate: float = 8.54e-05  # §13.1 → MarketRulesCNEquity 万0.854
    commission_min: float = 5.0  # §13.1 → MarketRulesCNEquity
    stamp_tax_rate: float = 0.0005  # §13.1 → MarketRulesCNEquity
    transfer_fee_rate: float = 0.00001  # §13.1 → MarketRulesCNEquity
    stamp_tax_auto_schedule: bool = True  # 是否根据历史时段自动调整印花税率
    slippage_bps: float = 10.0  # A 股默认 10bps（大盘最低合理值）
    slippage_model: str = "fixed"  # "fixed" | "linear_impact" | "square_root_impact"
    slippage_params: dict[str, Any] = field(default_factory=dict)


@dataclass
class FillModelConfig:
    """成交模型配置

    Attributes:
        name: 成交模型名称 (next_bar_open, next_bar_close, vwap, 等)
        volume_constraint: 成交量参与约束配置（可选）
    """

    name: str = "next_bar_open"
    volume_constraint: dict[str, Any] | None = None  # {max_participation_rate, lot_size}


@dataclass
class RiskConfig:
    """风控配置

    从 YAML ``risk_constraints`` 部分解析，驱动
    ``CompositeRiskManager`` / ``StopLossManager`` / ``AuditLogger`` /
    ``MarketRegimeDetector`` 的构建。

    Attributes:
        enabled: 是否启用风控（默认 True）
        max_position_weight: 单票最大权重 → PositionLimit
        max_drawdown: 最大回撤阈值 → DrawdownLimit
            ⚠️ 对集中持仓策略（top_k ≤ 10），默认 0.20 会触发反馈循环
            （reduce_position_at=0.10 在正常波动中持续缩仓，导致策略失效）。
            可转债集中策略建议设为 0.50。详见 docs/known_limitations.md #13。
        max_sector_weight: 行业最大权重 → SectorLimit（None = 不启用）
        max_daily_turnover: 日最大换手率 → TurnoverLimit（None = 不启用）
        daily_loss_limit: 日亏损限制 → CircuitBreaker daily_loss
        stop_loss_pct: 固定止损百分比 → FixedPercentStopLoss（None = 不启用）
        trailing_stop_pct: 追踪止损百分比 → TrailingStopLoss（None = 不启用）
        circuit_breaker_enabled: 是否启用熔断器
        circuit_breaker_drawdown: 熔断回撤阈值
        enable_audit_log: 是否启用审计日志
        market_regime_enabled: 是否启用市场状态检测
        benchmark_symbol: 基准指数代码（市场状态检测用）
    """

    enabled: bool = True
    max_position_weight: float = 0.10  # CB k≤10 策略需调大（如 0.25）
    max_drawdown: float = 0.20  # CB 集中策略需调大（如 0.50），否则触发反馈循环
    drawdown_scale_factor_floor: float = 0.0  # 线性减仓最小缩放比例（CB 轮转策略建议 0.3~0.5）
    drawdown_allow_rotation: bool = False  # 有持仓时允许跳过减仓区间缩减（CB 轮转策略推荐开启）
    drawdown_peak_reset_days: int = 0  # 全仓现金 N 天后重置 peak_nav（0 = 禁用，A股建议 20~30）
    max_sector_weight: float | None = None
    max_daily_turnover: float | None = None
    daily_loss_limit: float | None = None
    stop_loss_pct: float | None = None
    trailing_stop_pct: float | None = None
    circuit_breaker_enabled: bool = False
    circuit_breaker_drawdown: float = 0.10
    enable_audit_log: bool = True
    market_regime_enabled: bool = False
    market_regime_bear_scale: float = 0.50
    market_regime_neutral_scale: float = 0.80
    benchmark_symbol: str = ""
    # 集中度限制
    concentration_enabled: bool = False
    concentration_max_top_n_weight: float = 0.50
    concentration_top_n: int = 5
    concentration_min_positions: int = 10
    # 流动性约束
    liquidity_enabled: bool = False
    liquidity_max_participation_rate: float = 0.10
    # 风格暴露限制
    style_exposure_enabled: bool = False
    style_exposure_limits: dict = field(default_factory=lambda: {
        "size": 0.30,      # 市值风格暴露上限
        "value": 0.30,     # 价值风格暴露上限
        "momentum": 0.30,  # 动量风格暴露上限
    })
    # 强赎暴露限制（可转债）
    force_redeem_enabled: bool = False
    force_redeem_max_weight: float = 0.30
    force_redeem_cb_value_threshold: float = 125.0
    # 行业中性约束
    industry_neutral_enabled: bool = False
    industry_max_deviation: float = 0.05
    # 降级管理器
    degradation_enabled: bool = False
    degradation_auto_recover: bool = True
    degradation_recovery_cooldown: float = 300.0
    degradation_position_scale_warning: float = 0.5
    degradation_position_scale_degraded: float = 0.0
    # 额外配置（position_sizing 等扩展字段，通过 YAML 透传）
    extra: dict = field(default_factory=dict)


@dataclass
class PortfolioConfig:
    """组合优化器配置

    Attributes:
        method: 优化方法名称。
            支持: equal (等权), market_cap (市值加权), risk_parity (风险平价),
            min_variance (最小方差), max_sharpe (最大夏普), mean_variance (均值方差),
            black_litterman (BL 模型)
        params: 优化器额外参数（传递给 create_optimizer()）
    """

    method: str = "equal"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class BacktestRunConfig:
    """完整回测配置

    Attributes:
        name: 回测名称
        description: 描述
        data: 数据配置
        universe: 股票池配置
        strategy: 策略配置
        cost: 成本配置
        initial_capital: 初始资金
        lot_size: 最小交易单位
        t_plus_n: T+N 规则
        random_seed: 随机种子（可选）
        output_dir: 输出目录
        portfolio: 组合优化器配置（可选）
    """

    name: str
    data: DataConfig
    universe: UniverseConfig
    strategy: StrategyConfig
    cost: CostConfig
    fill_model: FillModelConfig = field(default_factory=FillModelConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    portfolio: PortfolioConfig = field(default_factory=PortfolioConfig)
    description: str = ""
    initial_capital: float = 1_000_000.0
    lot_size: int = 100  # §13.1 → MarketRulesCNEquity
    t_plus_n: int = 1  # §13.1 → MarketRulesCNEquity
    random_seed: int | None = None
    output_dir: str = "output"
    quality_gates_path: str | None = None


@dataclass
class BacktestRunResult:
    """回测运行结果

    Attributes:
        config: 使用的配置
        result: 回测结果
        fingerprint: 实验指纹
        output_dir: 输出目录
        report: 回测分析报告（可选）
        html_report_path: HTML 报告文件路径（可选）
        data_health: 数据健康检查摘要（可选）
        quality_report: 质量门禁检查报告（可选）
    """

    config: BacktestRunConfig
    result: BacktestResult
    fingerprint: ExperimentFingerprint
    output_dir: Path
    report: BacktestReport | None = None
    html_report_path: Path | None = None
    data_health: dict | None = None
    quality_report: QualityReport | None = None

    @property
    def summary_path(self) -> Path:
        """标准 summary.json 路径。"""
        return self.output_dir / "summary.json"

    def to_dict(self) -> dict[str, Any]:
        """返回轻量级、可序列化的结果摘要。

        该方法主要用于脚本层兼容，避免将 ``BacktestRunResult``
        误当作普通 ``dict`` 使用时出现静默失败或属性错误。
        """
        result_dict: dict[str, Any] = {
            "config_name": self.config.name,
            "strategy_name": getattr(self.result, "strategy_name", self.config.strategy.name),
            "output_dir": str(self.output_dir),
            "summary_path": str(self.summary_path),
            "html_report_path": str(self.html_report_path) if self.html_report_path else None,
            "initial_capital": float(getattr(self.result, "initial_capital", 0.0) or 0.0),
            "final_nav": float(getattr(self.result, "final_nav", 0.0) or 0.0),
            "total_trades": int(getattr(self.result, "total_trades", 0) or 0),
        }

        if self.report is not None:
            result_dict.update(
                {
                    "annualized_return": getattr(self.report.performance, "annualized_return", None),
                    "sharpe_ratio": getattr(self.report.performance, "sharpe_ratio", None),
                    "calmar_ratio": getattr(self.report.performance, "calmar_ratio", None),
                    "max_drawdown_pct": getattr(self.report.drawdown, "max_drawdown_pct", None),
                    "win_rate": getattr(self.report.trading, "win_rate", None),
                    "avg_positions": getattr(self.report.trading, "avg_positions", None),
                }
            )

        if self.data_health is not None:
            result_dict["data_health_status"] = self.data_health.get("status")

        if self.quality_report is not None:
            result_dict["quality_all_passed"] = bool(self.quality_report.all_passed)

        return result_dict

    def items(self):
        """兼容旧脚本的字典式遍历接口。"""
        return self.to_dict().items()

    def keys(self):
        """兼容旧脚本的字典式键访问。"""
        return self.to_dict().keys()

    def values(self):
        """兼容旧脚本的字典式值访问。"""
        return self.to_dict().values()

    def get(self, key: str, default: Any = None) -> Any:
        """兼容旧脚本的 ``dict.get()`` 访问。"""
        return self.to_dict().get(key, default)
