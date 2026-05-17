"""Regime 检测器 — 支持规则投票与 HMM 两种模式。

根据 CB 市场特征（波动率、溢价率中位数、成交额）检测市场状态。
规则模式复用 MarketRegimeDetector（risk/constraints.py），
HMM 模式使用 hmmlearn GaussianHMM 进行无监督状态推断。

理论基础：
- Hamilton (1989) Regime-Switching Models
- Moskowitz, Ooi, Pedersen (2012) — 时间序列动量与 regime 识别
- 光大证券 RSRS 技术择时

避免前视偏差：
- HMM 仅用 T-1 及之前数据拟合 + 预测
- 滚动窗口拟合，每日前推

与现有架构关系：
- 复用 MarketRegimeDetector（规则投票模式）
- 输出 MarketRegime 枚举直接对接 RegimeAdaptiveAllocator
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

import numpy as np

from aipha.portfolio.regime_weight import MarketRegime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

class DetectorMode(str, Enum):
    """检测器模式。"""
    RULE = "rule"   # 基于规则投票（默认，无外部依赖）
    HMM = "hmm"     # 基于 HMM 无监督识别（需 hmmlearn）


@dataclass
class RegimeDetectorConfig:
    """Regime 检测器配置。

    Attributes:
        mode: 检测模式
        hmm_n_states: HMM 隐状态数（2 或 3）
        hmm_lookback: HMM 拟合滚动窗口（交易日）
        vol_window: 波动率计算窗口
        momentum_window: 动量回看窗口
        high_vol_quantile: 高波动分位数阈值
        feature_names: 使用的特征列表
        # 规则模式阈值（可配化，避免硬编码）
        momentum_bull_threshold: 动量牛市阈值
        momentum_bear_threshold: 动量熊市阈值
        volume_bull_ratio: 成交额牛市比阈值
        volume_bear_ratio: 成交额熊市比阈值
        # 溢价率信号阈值
        premium_low_threshold: 低溢价阈值 (%) — 低于此值偏牛
        premium_high_threshold: 高溢价阈值 (%) — 高于此值偏熊
        premium_lookback: 溢价率变化回看窗口
        use_premium_signal: 是否启用溢价率信号
    """
    mode: DetectorMode = DetectorMode.RULE
    hmm_n_states: int = 3
    hmm_lookback: int = 252
    vol_window: int = 20
    momentum_window: int = 60
    high_vol_quantile: float = 0.75
    feature_names: list[str] = field(
        default_factory=lambda: ["volatility", "momentum", "volume_ratio"]
    )
    # 规则模式阈值（针对 CB 市场校准）
    momentum_bull_threshold: float = 0.03   # CB 市场动量偏弱，3% 即偏牛
    momentum_bear_threshold: float = -0.05  # -5% 即偏熊 (60日)
    volume_bull_ratio: float = 1.15   # 成交额放量 15%
    volume_bear_ratio: float = 0.85   # 成交额缩量 15%
    # 溢价率信号
    premium_low_threshold: float = 20.0   # 中位溢价 <20% → 偏牛 (债性强，面值保护)
    premium_high_threshold: float = 45.0  # 中位溢价 >45% → 偏熊 (股性强，随正股跌)
    premium_lookback: int = 20
    use_premium_signal: bool = True
    # 投票逻辑
    use_score_voting: bool = True  # True=评分制(+1/0/-1), False=多数投票


# ---------------------------------------------------------------------------
# Protocol：特征提供者
# ---------------------------------------------------------------------------

class RegimeFeatureProvider(Protocol):
    """提供 regime 检测所需特征的协议。"""

    def get_features(self, lookback: int) -> np.ndarray:
        """返回 shape=(lookback, n_features) 的特征矩阵。"""
        ...


# ---------------------------------------------------------------------------
# 核心检测器
# ---------------------------------------------------------------------------

class RegimeDetector:
    """统一 Regime 检测器。

    Usage:
        >>> cfg = RegimeDetectorConfig(mode=DetectorMode.RULE)
        >>> detector = RegimeDetector(cfg)
        >>> detector.update(index_close=2150.0, volume=500.0)
        >>> regime = detector.detect()
    """

    def __init__(self, config: RegimeDetectorConfig | None = None) -> None:
        self._config = config or RegimeDetectorConfig()
        # 历史数据缓存
        self._closes: list[float] = []
        self._volumes: list[float] = []
        self._premiums: list[float] = []
        self._max_len = max(
            self._config.hmm_lookback,
            self._config.vol_window,
            self._config.momentum_window,
        ) + 50
        # HMM 模型（延迟初始化）
        self._hmm_model: Any = None
        self._hmm_available = False
        if self._config.mode == DetectorMode.HMM:
            self._init_hmm()

    def _init_hmm(self) -> None:
        """延迟初始化 HMM 模型。"""
        try:
            from hmmlearn.hmm import GaussianHMM  # type: ignore[import-untyped]
            self._hmm_model = GaussianHMM(
                n_components=self._config.hmm_n_states,
                covariance_type="full",
                n_iter=100,
                random_state=42,
            )
            self._hmm_available = True
            logger.info("HMM regime detector initialized (n_states=%d)", self._config.hmm_n_states)
        except ImportError:
            logger.warning("hmmlearn 不可用，降级到规则模式")
            self._hmm_available = False

    def update(
        self,
        index_close: float,
        volume: float = 0.0,
        premium_median: float = 0.0,
    ) -> None:
        """每日更新数据。"""
        self._closes.append(index_close)
        self._volumes.append(volume)
        self._premiums.append(premium_median)
        # 裁剪历史
        if len(self._closes) > self._max_len:
            self._closes = self._closes[-self._max_len:]
            self._volumes = self._volumes[-self._max_len:]
            self._premiums = self._premiums[-self._max_len:]

    def detect(self) -> MarketRegime:
        """检测当前市场状态。"""
        if self._config.mode == DetectorMode.HMM and self._hmm_available:
            return self._detect_hmm()
        return self._detect_rule()

    def is_high_volatility(self) -> bool:
        """是否高波动。"""
        vol = self._current_volatility()
        if len(self._closes) < self._config.hmm_lookback:
            return vol > 0.03  # fallback 固定阈值
        # 用历史分位数判断
        n = self._config.vol_window
        vols: list[float] = []
        for i in range(n + 1, len(self._closes)):
            prices = self._closes[i - n:i + 1]
            rets = [(prices[j] - prices[j - 1]) / prices[j - 1]
                    for j in range(1, len(prices)) if prices[j - 1] > 0]
            if len(rets) >= 2:
                vols.append(float(np.std(rets)))
        if not vols:
            return vol > 0.03
        threshold = float(np.quantile(vols, self._config.high_vol_quantile))
        return vol > threshold

    def _detect_rule(self) -> MarketRegime:
        """规则投票检测 — 最多 4 信号投票。"""
        cfg = self._config
        signals: list[str] = []
        # 信号 1: 动量
        mom = self._current_momentum()
        if mom > cfg.momentum_bull_threshold:
            signals.append("BULL")
        elif mom < cfg.momentum_bear_threshold:
            signals.append("BEAR")
        else:
            signals.append("NEUTRAL")
        # 信号 2: 波动率
        if self.is_high_volatility():
            signals.append("BEAR")
        else:
            signals.append("NEUTRAL")
        # 信号 3: 成交额趋势（近 10 日 vs 近 30 日均值）
        if len(self._volumes) >= 30:
            v_recent = [v for v in self._volumes[-10:] if v > 0]
            v_longer = [v for v in self._volumes[-30:] if v > 0]
            if v_recent and v_longer:
                recent = np.mean(v_recent)
                longer = np.mean(v_longer)
                if longer > 0 and recent / longer > cfg.volume_bull_ratio:
                    signals.append("BULL")
                elif longer > 0 and recent / longer < cfg.volume_bear_ratio:
                    signals.append("BEAR")
                else:
                    signals.append("NEUTRAL")
            else:
                signals.append("NEUTRAL")
        else:
            signals.append("NEUTRAL")
        # 信号 4: 溢价率信号（CB 特有）
        if cfg.use_premium_signal and len(self._premiums) >= cfg.premium_lookback:
            valid_prems = [p for p in self._premiums[-cfg.premium_lookback:]
                          if p != 0.0 and not np.isnan(p)]
            if valid_prems:
                current_prem = np.median(valid_prems[-5:]) if len(valid_prems) >= 5 else valid_prems[-1]
                if current_prem < cfg.premium_low_threshold:
                    signals.append("BULL")   # 低溢价=债性强=安全
                elif current_prem > cfg.premium_high_threshold:
                    signals.append("BEAR")   # 高溢价=股性强=风险
                else:
                    signals.append("NEUTRAL")
        # 投票/评分: 使用评分制 (+1=BULL, 0=NEUTRAL, -1=BEAR)
        if cfg.use_score_voting:
            score = 0
            for s in signals:
                if s == "BULL":
                    score += 1
                elif s == "BEAR":
                    score -= 1
            if score > 0:
                return MarketRegime.BULL
            elif score < 0:
                return MarketRegime.BEAR
            return MarketRegime.NEUTRAL
        # 传统多数投票
        bull_cnt = signals.count("BULL")
        bear_cnt = signals.count("BEAR")
        threshold = len(signals) // 2 + 1
        if bull_cnt >= threshold:
            return MarketRegime.BULL
        elif bear_cnt >= threshold:
            return MarketRegime.BEAR
        return MarketRegime.NEUTRAL

    def _detect_hmm(self) -> MarketRegime:
        """HMM 检测。"""
        if not self._hmm_available or self._hmm_model is None:
            return self._detect_rule()
        features = self._build_features()
        if features.shape[0] < 30:
            return self._detect_rule()
        try:
            self._hmm_model.fit(features)
            states = self._hmm_model.predict(features)
            current_state = int(states[-1])
            # 根据各状态均值的动量特征排序分配 regime
            means = self._hmm_model.means_
            # 动量特征在第 1 列
            mom_col = min(1, means.shape[1] - 1)
            state_order = np.argsort(means[:, mom_col])  # 从低到高
            regime_map = {}
            n_states = self._config.hmm_n_states
            if n_states == 2:
                regime_map[int(state_order[0])] = MarketRegime.BEAR
                regime_map[int(state_order[1])] = MarketRegime.BULL
            else:  # 3-state
                regime_map[int(state_order[0])] = MarketRegime.BEAR
                regime_map[int(state_order[1])] = MarketRegime.NEUTRAL
                regime_map[int(state_order[-1])] = MarketRegime.BULL
            return regime_map.get(current_state, MarketRegime.NEUTRAL)
        except Exception:
            logger.warning("HMM fit/predict failed, fallback to rule-based")
            return self._detect_rule()

    def _build_features(self) -> np.ndarray:
        """从历史数据构建特征矩阵 (T, n_features)。"""
        n = min(len(self._closes) - 1, self._config.hmm_lookback)
        if n < 10:
            return np.empty((0, 4))
        prices = self._closes[-(n + 1):]
        # 特征 1: 日收益率
        rets = np.diff(prices) / np.array(prices[:-1])
        # 特征 2: 滚动波动率 (vol_window)
        vw = self._config.vol_window
        vols = np.array([
            np.std(rets[max(0, i - vw):i], ddof=1) if i >= vw
            else (np.std(rets[:i + 1], ddof=1) if i + 1 >= 2 else 0.0)
            for i in range(len(rets))
        ])
        # 特征 3: 成交额变化率
        if len(self._volumes) > n:
            vol_slice = self._volumes[-(n):]
        else:
            vol_slice = self._volumes[-len(rets):]
        vol_arr = np.array(vol_slice[-len(rets):], dtype=float)
        if len(vol_arr) < len(rets):
            vol_arr = np.pad(vol_arr, (len(rets) - len(vol_arr), 0), constant_values=0)
        vol_mean = np.mean(vol_arr) if np.mean(vol_arr) > 0 else 1.0
        vol_ratio = vol_arr / vol_mean
        # 特征 4: 溢价率水平 (归一化到 0-1 范围)
        if len(self._premiums) > n:
            prem_slice = self._premiums[-(n):]
        else:
            prem_slice = self._premiums[-len(rets):]
        prem_arr = np.array(prem_slice[-len(rets):], dtype=float)
        if len(prem_arr) < len(rets):
            prem_arr = np.pad(prem_arr, (len(rets) - len(prem_arr), 0),
                             constant_values=30.0)
        prem_norm = prem_arr / 100.0  # 归一化: 30% → 0.3
        features = np.column_stack([rets, vols, vol_ratio, prem_norm])
        # 替换 NaN/Inf
        features = np.nan_to_num(features, nan=0.0, posinf=0.0, neginf=0.0)
        return features

    def _current_volatility(self) -> float:
        """计算当前波动率（日收益率标准差）。"""
        n = self._config.vol_window
        if len(self._closes) < n + 1:
            return 0.0
        prices = self._closes[-(n + 1):]
        rets = [(prices[i] - prices[i - 1]) / prices[i - 1]
                for i in range(1, len(prices)) if prices[i - 1] > 0]
        if len(rets) < 2:
            return 0.0
        arr = np.array(rets)
        return float(np.std(arr, ddof=1))

    def _current_momentum(self) -> float:
        """计算当前动量（回看期收益率）。"""
        n = self._config.momentum_window
        if len(self._closes) < n + 1:
            return 0.0
        old = self._closes[-(n + 1)]
        new = self._closes[-1]
        return (new - old) / old if old > 0 else 0.0

    def get_state_history(self) -> list[tuple[int, str]]:
        """获取历史 regime 标注。"""
        return []

    def reset(self) -> None:
        """重置内部状态。"""
        self._closes.clear()
        self._volumes.clear()
        self._premiums.clear()
        self._hmm_model = None
