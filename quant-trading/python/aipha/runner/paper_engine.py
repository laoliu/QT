"""模拟盘引擎 — 纸上交易运行器

AlphQ 原版 1620 行，深度依赖 AKShare/实时流/CB Universe 等特有模块。
此处保留核心框架 & 配置体系，去除无法在 QT 中直接运行的外部依赖。
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal as signal_mod
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


# ── 配置 ─────────────────────────────────────────────────────

@dataclass
class BondSymbolConfig:
    code: str
    weight: float = 1.0
    duration_bucket: str = ""


@dataclass
class PillarConfig:
    name: str
    weight: float = 1.0
    market_type: str = "equity"
    static_symbols: list[str] = field(default_factory=list)
    bond_symbols: list[BondSymbolConfig] = field(default_factory=list)
    strategy_name: str = ""
    model_dir: str = ""
    sub_strategies: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass
class PaperEngineConfig:
    # 基本
    strategy_name: str = ""
    strategy_params: dict = field(default_factory=dict)
    symbols: list[str] = field(default_factory=list)
    pillar_configs: list[PillarConfig] = field(default_factory=list)
    portfolio_weights: dict[str, float] = field(default_factory=dict)
    market_type: str = "equity"
    # 轮询
    poll_interval: float = 5.0
    bar_interval_sec: int = 60
    # 资金
    initial_capital: float = 1_000_000.0
    lot_size: int = 100
    t_plus_n: int = 1
    # 费用
    commission_rate: float = 0.0003
    commission_min: float = 5.0
    stamp_tax_rate: float = 0.001
    # 滑点
    slippage_bps: float = 5.0
    slippage_model_name: str = "fixed"
    slippage_params: dict = field(default_factory=dict)
    # 成交
    fill_model_name: str = "full"
    fill_volume_constraint: float = 0.1
    # 存储
    state_dir: str = "data/paper_state"
    audit_log_dir: str = "logs/paper_audit"
    # 监控阈值
    alert_max_drawdown: float = 0.15
    alert_daily_loss: float = 0.05
    alert_data_stale_sec: float = 300.0
    alert_inactivity_min: float = 30.0
    # ML risk
    ml_vol_target: float | None = None
    ml_mdd_delever_threshold: float | None = None
    ml_sharpe_downweight_threshold: float | None = None
    # CB pillar
    cb_pillar_downweight: float | None = None
    # Barra
    barra_monitor_enabled: bool = False
    # Metrics
    metrics_enabled: bool = False
    metrics_port: int = 9090

    def create_slippage_model(self) -> Any:
        """工厂创建滑点模型"""
        from aipha.execution.slippage import (
            FixedBpsSlippageModel,
            LinearImpactSlippageModel,
            SquareRootImpactSlippageModel,
        )
        if self.slippage_model_name == "linear":
            return LinearImpactSlippageModel(**self.slippage_params)
        elif self.slippage_model_name == "sqrt":
            return SquareRootImpactSlippageModel(**self.slippage_params)
        return FixedBpsSlippageModel(bps=self.slippage_bps)

    @classmethod
    def from_yaml(cls, path: Path) -> PaperEngineConfig:
        if not path.exists():
            raise FileNotFoundError(f"Config not found: {path}")
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        pillars = []
        for p in raw.get("pillars", []):
            bonds = [BondSymbolConfig(**b) for b in p.get("bond_symbols", [])]
            pillars.append(PillarConfig(
                name=p.get("name", ""),
                weight=p.get("weight", 1.0),
                market_type=p.get("market_type", "equity"),
                static_symbols=p.get("symbols", []),
                bond_symbols=bonds,
                strategy_name=p.get("strategy", ""),
                model_dir=p.get("model_dir", ""),
                sub_strategies=p.get("sub_strategies", []),
                raw=p,
            ))

        kwargs: dict[str, Any] = {}
        # map flat keys
        field_names = {f.name for f in cls.__dataclass_fields__.values()}
        for k, v in raw.items():
            if k in field_names and k not in ("pillar_configs",):
                kwargs[k] = v
        kwargs["pillar_configs"] = pillars
        kwargs.setdefault("strategy_params", raw.get("strategy_params", {}))
        kwargs.setdefault("symbols", raw.get("symbols", []))
        return cls(**{k: v for k, v in kwargs.items() if k in field_names})


# ── 引擎 ─────────────────────────────────────────────────────

class PaperEngine:
    """模拟盘运行引擎

    实际接入数据源和交易对在上层通过 LiveRunner 管理。
    此类提供:
    - 配置解析
    - 事件循环（市场日程、行情轮询、盘后结算）
    - 状态持久化 / 恢复
    - 监控 / 告警集成
    """

    def __init__(
        self,
        config: PaperEngineConfig,
        strategy: Any = None,
        *,
        calendar: Any = None,
    ):
        self.config = config
        self.strategy = strategy
        self.calendar = calendar
        self._running = False
        self._state_dir = Path(config.state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._runner: Any = None
        self._monitor: Any = None
        self._metrics: Any = None
        logger.info("PaperEngine 初始化: strategy=%s symbols=%d pillar=%d",
                     config.strategy_name, len(config.symbols), len(config.pillar_configs))

    @property
    def runner(self) -> Any:
        return self._runner

    @property
    def monitor(self) -> Any:
        return self._monitor

    async def run(self) -> None:
        """主事件循环 — 连接数据源 → 订阅 → 轮询 → 盘后结算"""
        self._running = True
        self._register_signal_handlers()
        self._load_state()
        logger.info("PaperEngine 启动")
        try:
            while self._running:
                await asyncio.sleep(self.config.poll_interval)
        except asyncio.CancelledError:
            pass
        finally:
            await self._shutdown()

    async def stop(self) -> None:
        self._running = False

    def _register_signal_handlers(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal_mod.SIGINT, signal_mod.SIGTERM):
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))
        except (NotImplementedError, AttributeError):
            pass  # Windows

    async def _shutdown(self) -> None:
        self._save_state()
        logger.info("PaperEngine 已停止")

    def _save_state(self) -> None:
        path = self._state_dir / "engine_state.json"
        import json
        state = {
            "strategy_name": self.config.strategy_name,
            "timestamp": datetime.now().isoformat(),
        }
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_state(self) -> None:
        path = self._state_dir / "engine_state.json"
        if path.exists():
            import json
            state = json.loads(path.read_text(encoding="utf-8"))
            logger.info("恢复状态: %s", state.get("timestamp", "?"))


def run_paper_trading(config_path: Path) -> None:
    """CLI 入口: 加载 YAML 配置并启动模拟盘"""
    config = PaperEngineConfig.from_yaml(config_path)
    engine = PaperEngine(config)
    asyncio.run(engine.run())
