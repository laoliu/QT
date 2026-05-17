"""CB GP 挖掘因子策略

使用 GP 遗传编程挖掘出的量价因子对 CB 进行评分和轮动。

设计思路:
- 加载预挖掘的因子表达式（JSON 文件）
- 每个调仓日对候选标的应用 GP 因子计算
- 多因子等权融合排名 → 选出 top_k
- 接入标准 CBRotationStrategy 框架

与现有架构关系:
- 继承 CBRotationStrategy（预过滤/调仓/止损逻辑复用）
- 因子表达式由 mining/cb_gp_miner.py 产出
- 回测通过 config_runner.py 唯一入口执行
"""
from __future__ import annotations

import json
import logging
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)

# 滚动窗口大小（需 20 日 MA + 缓冲）
_HISTORY_LEN = 25

# ALL_CB_FEATURES 索引映射（与 mining/cb_gp_miner.py 一致）
_FEATURE_NAMES = [
    "close", "open", "high", "low",
    "ret_1d", "ret_5d", "ret_20d",
    "vol_ratio_5d", "vol_ratio_20d",
    "price_ma5_ratio", "price_ma20_ratio",
    "high_low_range", "close_open_range",
    "cb_over_rate", "cb_value",
    "premium_chg_5d", "turnover_rate", "amount_rank",
]


# ── 配置 ──


@dataclass
class GPMinedCBConfig(CBRotationConfig):
    """GP 挖掘因子策略配置."""

    top_k: int = 15
    rebalance_days: int = 5
    max_price: float = 150.0
    min_turnover: float = 50.0

    # GP 因子文件路径（mining 输出的 JSON）
    factor_file: str = ""

    # 内联因子表达式（优先于 factor_file）
    factor_expressions: list[str] = field(default_factory=list)

    # 回退: 若 GP 因子无法计算则使用双低排名
    fallback_to_double_low: bool = True

    # 评分模式: "equal" 等权, "ic_weighted" IC 加权
    scoring_mode: str = "equal"


# ── 策略 ──


class CBGPMinedStrategy(CBRotationStrategy):
    """CB GP 挖掘因子轮动策略.

    从 JSON 文件或内联配置加载 GP 因子表达式，对候选标的
    计算因子值并等权融合排名，选出 top_k。

    使用滚动 bar 历史计算真实特征值（ret, vol_ratio, MA 等），
    而非占位符，确保训练-推理行为一致。
    """

    name = "cb_gp_mined"

    def __init__(self, config: GPMinedCBConfig | None = None, **kwargs: Any):
        super().__init__()
        self._config = config or self._build_config(kwargs)
        self._gp_factors_loaded = False
        self._factor_exprs: list[str] = []
        self._factor_directions: list[float] = []  # IC 方向: +1 正 IC, -1 负 IC
        self._factor_ic_abs: list[float] = []  # |IC| 绝对值, 用于加权

    @property
    def config(self) -> GPMinedCBConfig:
        """当前策略配置对象。"""
        return self._config

    def _on_start_extra(self) -> None:
        """初始化滚动 bar 历史缓冲."""
        self._bar_history: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=_HISTORY_LEN),
        )
        self._cur_features: dict[str, dict[str, float]] = {}

    def _on_bar_update_state(
        self, bars: dict[str, "Bar"], context: StrategyContext,
    ) -> None:
        """每 bar 追加历史数据，用于滚动特征计算."""
        for symbol, bar in bars.items():
            meta = bar.metadata or {}
            self._bar_history[symbol].append({
                "close": bar.close,
                "open": bar.open if hasattr(bar, "open") else bar.close,
                "high": bar.high if hasattr(bar, "high") else bar.close,
                "low": bar.low if hasattr(bar, "low") else bar.close,
                "vol": meta.get("vol", 0.0),
                "amount": meta.get("amount", 0.0),
                "cb_over_rate": meta.get("cb_over_rate"),
                "cb_value": meta.get("cb_value"),
                "turnover_rate": meta.get("turnover_rate", 0.0),
            })

    @staticmethod
    def _build_config(params: dict[str, Any]) -> GPMinedCBConfig:
        p = params
        conf = GPMinedCBConfig(
            top_k=p.get("top_k", 15),
            rebalance_days=p.get("rebalance_days", 5),
            max_price=p.get("max_price", 150.0),
            min_turnover=p.get("min_turnover", 50.0),
            factor_file=p.get("factor_file", ""),
            factor_expressions=p.get("factor_expressions", []),
            fallback_to_double_low=p.get("fallback_to_double_low", True),
            stop_loss_pct=p.get("stop_loss_pct", 0.15),
            buffer_k=p.get("buffer_k", 2),
            scoring_mode=p.get("scoring_mode", "equal"),
        )
        return conf

    def _load_factors(self) -> list[str]:
        """加载 GP 因子表达式及其 IC 方向.

        IC 方向用于确定排名方向：
        - 正 IC → 因子值越高 → 未来收益越高 → 正向排名
        - 负 IC → 因子值越高 → 未来收益越低 → 反向排名
        """
        if self._gp_factors_loaded:
            return self._factor_exprs

        cfg: GPMinedCBConfig = self._config  # type: ignore[assignment]

        # 优先使用内联表达式（无 IC 方向信息, 默认正向）
        if cfg.factor_expressions:
            self._factor_exprs = list(cfg.factor_expressions)
            self._factor_directions = [1.0] * len(self._factor_exprs)
            self._factor_ic_abs = [1.0] * len(self._factor_exprs)
            self._gp_factors_loaded = True
            return self._factor_exprs

        # 从 JSON 文件加载（含 IC 方向）
        if cfg.factor_file:
            path = Path(cfg.factor_file)
            if path.exists():
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                factors = data.get("factors", [])
                for fac in factors:
                    if not fac.get("is_significant", False):
                        continue
                    self._factor_exprs.append(fac["expression"])
                    # IC 方向: 正 IC → +1, 负 IC → -1
                    ic = fac.get("ic_mean", 0.0)
                    self._factor_directions.append(
                        1.0 if ic >= 0 else -1.0,
                    )
                    self._factor_ic_abs.append(abs(ic) if ic else 0.01)
                logger.info(
                    "Loaded %d GP factors from %s (pos_ic=%d, neg_ic=%d)",
                    len(self._factor_exprs), path,
                    sum(1 for d in self._factor_directions if d > 0),
                    sum(1 for d in self._factor_directions if d < 0),
                )
            else:
                logger.warning("Factor file not found: %s", path)

        self._gp_factors_loaded = True
        return self._factor_exprs

    def _compute_features_for_symbol(self, symbol: str) -> dict[str, float]:
        """从滚动 bar 历史计算真实特征值.

        返回 {feature_name: value} 字典，对应 ALL_CB_FEATURES 的 18 个特征。
        """
        hist = list(self._bar_history.get(symbol, []))
        if not hist:
            return {}
        cur = hist[-1]
        close = cur["close"] or 100.0
        opn = cur.get("open", close)
        high = cur.get("high", close)
        low = cur.get("low", close)
        vol = cur.get("vol", 0.0)
        cb_over = cur.get("cb_over_rate") or 0.0
        cb_val = cur.get("cb_value") or close

        closes = [h["close"] for h in hist if h["close"]]
        vols = [h.get("vol", 0.0) for h in hist]
        premiums = [h.get("cb_over_rate") for h in hist]

        n = len(closes)
        # 收益率
        ret_1d = (closes[-1] / closes[-2] - 1.0) if n >= 2 else 0.0
        ret_5d = (closes[-1] / closes[-6] - 1.0) if n >= 6 else 0.0
        ret_20d = (closes[-1] / closes[-21] - 1.0) if n >= 21 else 0.0

        # 成交量比率
        def _safe_mean(arr: list, k: int) -> float:
            sl = arr[-k:] if len(arr) >= k else arr
            m = sum(sl) / max(len(sl), 1)
            return m if m > 1e-8 else 1e-8

        vol_ma5 = _safe_mean(vols, 5)
        vol_ma20 = _safe_mean(vols, 20)
        vol_ratio_5d = vol / vol_ma5 if vol_ma5 > 1e-8 else 1.0
        vol_ratio_20d = vol / vol_ma20 if vol_ma20 > 1e-8 else 1.0

        # 价格均线比
        close_ma5 = _safe_mean(closes, 5)
        close_ma20 = _safe_mean(closes, 20)
        price_ma5_ratio = close / close_ma5 if close_ma5 > 1e-8 else 1.0
        price_ma20_ratio = close / close_ma20 if close_ma20 > 1e-8 else 1.0

        # 波幅
        high_low_range = (high - low) / close if close > 1e-8 else 0.02
        close_open_range = (close - opn) / opn if opn > 1e-8 else 0.0

        # 溢价率 5 日变化
        valid_p = [p for p in premiums if p is not None]
        premium_chg_5d = (
            (valid_p[-1] - valid_p[-6]) if len(valid_p) >= 6 else 0.0
        )

        return {
            "close": close, "open": opn, "high": high, "low": low,
            "ret_1d": ret_1d, "ret_5d": ret_5d, "ret_20d": ret_20d,
            "vol_ratio_5d": vol_ratio_5d, "vol_ratio_20d": vol_ratio_20d,
            "price_ma5_ratio": price_ma5_ratio,
            "price_ma20_ratio": price_ma20_ratio,
            "high_low_range": high_low_range,
            "close_open_range": close_open_range,
            "cb_over_rate": cb_over, "cb_value": cb_val,
            "premium_chg_5d": premium_chg_5d,
            "turnover_rate": cur.get("turnover_rate", 0.0),
            "amount_rank": 0.5,  # 截面排名在 _score_candidates 中计算
        }

    def _eval_gp_expression(
        self, expr: str, c: CandidateInfo,
    ) -> float:
        """使用真实特征值评估 GP 表达式.

        特征从滚动 bar 历史计算，与 GP 训练时的特征一致。
        """
        feats = self._cur_features.get(c.symbol)
        if not feats:
            feats = self._compute_features_for_symbol(c.symbol)

        # 构建 X0-X17 变量映射 + 命名特征映射
        variables = {}
        for idx, fname in enumerate(_FEATURE_NAMES):
            val = feats.get(fname, 0.0)
            variables[f"X{idx}"] = val
            variables[fname] = val

        try:
            result = self._safe_eval_gp(expr, variables)
            if not math.isfinite(result):
                return 0.0
            return result
        except Exception:
            return 0.0

    @staticmethod
    def _safe_eval_gp(expr: str, variables: dict[str, float]) -> float:
        """安全解析 gplearn 表达式.

        gplearn 输出格式: 'add(X0, mul(X1, X2))' 或 'sub(X0, div(X1, X2))'
        """
        # 与 gplearn 内部保护运算保持一致:
        # gplearn div: |b| < 0.001 → 1.0
        # gplearn sqrt: sqrt(abs(a))
        # gplearn log: |a| < 0.001 → 0.0, else log(abs(a))
        safe_funcs = {
            "add": lambda a, b: a + b,
            "sub": lambda a, b: a - b,
            "mul": lambda a, b: a * b,
            "div": lambda a, b: a / b if abs(b) > 1e-3 else 1.0,
            "sqrt": lambda a: math.sqrt(abs(a)),
            "log": lambda a: math.log(abs(a)) if abs(a) > 1e-3 else 0.0,
            "abs": lambda a: abs(a),
            "neg": lambda a: -a,
            "inv": lambda a: 1.0 / a if abs(a) > 1e-3 else 0.0,
            "max": lambda a, b: max(a, b),
            "min": lambda a, b: min(a, b),
        }

        # 替换变量
        s = expr
        for var_name, var_val in sorted(
            variables.items(), key=lambda x: -len(x[0]),
        ):
            s = s.replace(var_name, str(var_val))

        # 安全 eval
        try:
            return float(eval(s, {"__builtins__": {}}, safe_funcs))  # noqa: S307
        except Exception:
            return 0.0

    def _rank_normalize_features(
        self, candidates: list[CandidateInfo],
    ) -> None:
        """截面排名归一化: 将所有特征转换为 [0,1] 百分位排名.

        与 mining/cb_gp_miner.py::_cross_section_rank() 保持一致:
        GP 表达式在训练时基于 rank-normalized 特征学习,
        推理时必须使用相同的预处理, 否则表达式输出值无意义。
        """
        from scipy.stats import rankdata

        n = len(candidates)
        if n < 2:
            return

        symbols = [c.symbol for c in candidates]
        for fname in _FEATURE_NAMES:
            values = np.array([
                self._cur_features.get(s, {}).get(fname, 0.0)
                for s in symbols
            ])
            if np.std(values) < 1e-10:
                ranked = np.full(n, 0.5)
            else:
                # argsort().argsort() / (n-1) → [0, 1], 与 mining 一致
                ranked = values.argsort().argsort().astype(np.float64)
                ranked /= (n - 1)
            for i, s in enumerate(symbols):
                if s in self._cur_features:
                    self._cur_features[s][fname] = ranked[i]

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """对候选标的应用 GP 因子评分.

        1. 为所有候选计算真实特征（从滚动历史）
        2. 截面计算 amount_rank
        3. 截面排名归一化（与 GP 训练预处理一致）
        4. 多因子等权融合排名 → top_k
        """
        cfg: GPMinedCBConfig = self._config  # type: ignore[assignment]
        factor_exprs = self._load_factors()

        if not factor_exprs:
            if cfg.fallback_to_double_low:
                logger.debug("No GP factors, fallback to double-low")
                return self._double_low_fallback(candidates, cfg.top_k)
            return []

        # 1. 计算所有候选的真实特征
        self._cur_features = {}
        amounts = []
        for c in candidates:
            feats = self._compute_features_for_symbol(c.symbol)
            self._cur_features[c.symbol] = feats
            amounts.append(feats.get("close", 0.0) * c.amount)

        # 2. 截面 amount_rank（百分位排名，先于全局 rank 归一化）
        if amounts:
            from scipy.stats import rankdata
            ranks = rankdata(amounts) / len(amounts)
            for i, c in enumerate(candidates):
                if c.symbol in self._cur_features:
                    self._cur_features[c.symbol]["amount_rank"] = ranks[i]

        # 3. 截面排名归一化（与 GP 训练预处理一致）
        #    GP 表达式在 rank-normalized [0,1] 空间中训练,
        #    推理时必须相同预处理, 否则表达式输出值无意义。
        self._rank_normalize_features(candidates)

        # 4. 计算因子值矩阵
        n_cands = len(candidates)
        n_factors = len(factor_exprs)
        scores_matrix = np.zeros((n_cands, n_factors))

        for j, expr in enumerate(factor_exprs):
            for i, c in enumerate(candidates):
                scores_matrix[i, j] = self._eval_gp_expression(expr, c)

        # 截面排名归一化（因子输出 → 排名融合）
        # IC 方向修正: 负 IC 因子反转排名 (1 - rank)
        rank_matrix = np.zeros_like(scores_matrix)
        directions = self._factor_directions
        for j in range(n_factors):
            col = scores_matrix[:, j]
            if np.std(col) > 1e-10:
                raw_rank = rankdata(col) / n_cands
                # 负 IC 因子: 因子值越低 → 未来收益越高 → 反转排名
                if j < len(directions) and directions[j] < 0:
                    rank_matrix[:, j] = 1.0 - raw_rank
                else:
                    rank_matrix[:, j] = raw_rank
            else:
                rank_matrix[:, j] = 0.5

        # IC 加权或等权融合
        if cfg.scoring_mode == "ic_weighted" and self._factor_ic_abs:
            weights = np.array(self._factor_ic_abs[:n_factors])
            w_sum = weights.sum()
            if w_sum > 1e-10:
                weights = weights / w_sum
            else:
                weights = np.ones(n_factors) / n_factors
            final_scores = rank_matrix @ weights
        else:
            final_scores = rank_matrix.mean(axis=1)
        ranked_idx = np.argsort(-final_scores)
        return [candidates[i].symbol for i in ranked_idx[: cfg.top_k]]

    @staticmethod
    def _double_low_fallback(
        candidates: list[CandidateInfo], top_k: int,
    ) -> list[str]:
        """双低排名回退."""
        def _dl(c: CandidateInfo) -> float:
            premium = c.cb_over_rate if c.cb_over_rate is not None else 100.0
            return c.close + premium
        scored = sorted(candidates, key=_dl)
        return [c.symbol for c in scored[:top_k]]


# ── 注册 ──
StrategyRegistry().register("cb_gp_mined")(CBGPMinedStrategy)
