"""可转债低波动均值回归策略 (CB Low Volatility Mean Reversion)

Alpha 来源（经济学理论基础）：
1. **低波动异象** (Ang et al. 2006, Baker et al. 2011):
   低波动资产提供更好的风险调整收益。在CB市场，低波动转债有更强的
   债底保护，且post-2022 ±20% 涨跌停限制削弱了高波动品种的超额收益。

2. **有界均值回归** (post-2022 特定):
   2022-08-01 新规引入 ±20% 涨跌停后，价格运动受到反射边界约束。
   短期走弱的CB（5-10日负收益）有更强的回归均值倾向，因为：
   - 价格限制创造的 reflecting barrier 加速均值回归
   - 低价区间有债底(100元面值)支撑
   - 过度悲观修正提供超额收益

3. **YTM carry 保护**:
   接近面值的CB提供类债券收益(YTM proxy = 100/close)，即使股性
   失效也有正收益底线。

4. **正股质量底线** (ROE filter):
   确保正股不是垃圾公司(违约风险低)，ROE > 0 作为最低质量门槛。

与现有策略的差异化（用于低相关组合）：
- small_size_v2: 规模因子 → 本策略不用规模
- double_low_plus_v4: 低价+低溢价 → 本策略加入波动率+反转
- cb_comprehensive_v2: 混合动量 → 本策略是反动量(逆向)
- cb_stock_quality_v3: 正股质量+价格+溢价 → 本策略加入波动率+反转

因子矩阵（4因子）：
  1. **CB低波动率** (20d realized vol, inv): weight=0.30 — 低波=低风险
  2. **短期反转** (10d return, inv):        weight=0.30 — 超卖=机会
  3. **债底距离** (100/close proxy):         weight=0.25 — 接近面值=安全
  4. **溢价率** (premium, inv):              weight=0.15 — 低溢价=弹性

预期表现:
- 全周期 AR 可能低于动量策略(15-25%)，但 post-2022 应当更稳定
- 与现有策略相关性目标 < 0.50
- MDD 目标 < 15%（低波动选股的天然优势）
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
    days_since_listed,
)
from aipha.strategy.cb_ultra import _rank_scores
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class CBLowVolReversionConfig(CBRotationConfig):
    """CB低波动均值回归策略配置。

    支持灵活的因子组合：低波动率、短期反转、债底距离、溢价率、正股ROE。
    任何因子的权重设为0即可禁用。
    """

    # --- 持仓与调仓 ---
    top_k: int = 15
    rebalance_days: int = 5       # 周频
    max_price: float = 130.0      # 聚焦债性转债（低价）
    min_turnover: float = 20.0    # 万元最低成交额
    min_days_to_maturity: int = 30
    exclude_st: bool = True
    stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.0
    lot_size: int = 10
    buffer_k: int = 5

    # --- 过滤条件 ---
    max_premium: float = 50.0     # 溢价率上限(%)
    use_age_filter: bool = True
    min_age_days: int = 5

    # --- 因子权重（任何权重=0表示禁用该因子）---
    weight_low_vol: float = 0.30      # CB低波动率(inv)
    weight_reversal: float = 0.0      # 短期反转(inv)，默认关闭
    weight_bond_floor: float = 0.25   # 债底距离(100/close)
    weight_premium: float = 0.15      # 溢价率(inv)
    weight_stock_roe: float = 0.20    # 正股ROE（需use_stock_bridge=True）
    weight_issue_size: float = 0.10   # 发行规模(小规模好)

    # --- 因子参数 ---
    vol_lookback: int = 20            # 波动率回看天数
    reversal_lookback: int = 10       # 反转回看天数

    # --- 正股数据(可选) ---
    use_stock_bridge: bool = True     # 默认启用正股数据


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("cb_low_vol_reversion")
class CBLowVolReversionStrategy(CBRotationStrategy):
    """可转债低波动均值回归策略。

    灵活6因子加权评分（权重=0的因子自动禁用）:
      w_vol     * rank(realized_vol, ↑)    → 低波好
      w_rev     * rank(return_Nd, ↑)       → 超卖好(可选)
      w_floor   * rank(100/close, ↓)       → 近面值好
      w_premium * rank(premium, ↑)         → 低溢价好
      w_roe     * rank(stock_roe, ↓)       → 高ROE好
      w_size    * rank(issue_size, ↑)      → 小规模好
    """

    name = "cb_low_vol_reversion"

    def __init__(
        self,
        config: CBLowVolReversionConfig | None = None,
        stock_bridge: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self._config = config or CBLowVolReversionConfig(**kwargs)
        self._bridge = stock_bridge
        # 价格历史 {symbol: deque[float]}
        self._price_history: dict[str, deque] = {}
        # 正股基本面缓存 {stk_code: fundamentals}
        self._current_fundamentals: dict[str, Any] = {}

    @property
    def config(self) -> CBLowVolReversionConfig:
        """返回策略配置。"""
        return self._config

    # --- 生命周期 ---

    def _on_start_extra(self) -> None:
        """初始化价格历史和正股桥接。"""
        self._price_history.clear()
        self._current_fundamentals.clear()
        # 延迟创建 bridge
        if self._bridge is None and self._config.use_stock_bridge:
            try:
                from aipha.data.cb_stock_bridge import CBStockBridge
                self._bridge = CBStockBridge()
            except Exception as e:
                logger.warning(f"CBStockBridge 初始化失败: {e}，降级为纯 CB 因子模式")

    def _on_bar_update_state(
        self, bars: dict[str, "Bar"], context: StrategyContext,
    ) -> None:
        """每bar更新价格历史 + 正股基本面。"""
        cfg = self._config
        maxlen = max(cfg.vol_lookback, cfg.reversal_lookback) + 5
        self._current_fundamentals.clear()

        query_date = (
            context.current_time.date()
            if hasattr(context.current_time, "date")
            else context.current_time
        )

        for symbol, bar in bars.items():
            if bar.close <= 0:
                continue
            if symbol not in self._price_history:
                self._price_history[symbol] = deque(maxlen=maxlen)
            self._price_history[symbol].append(bar.close)

            # 获取正股基本面
            meta = bar.metadata or {}
            stk_code = meta.get("stk_code")
            if (
                self._bridge is not None
                and stk_code
                and stk_code not in self._current_fundamentals
            ):
                try:
                    fund = self._bridge.get_fundamentals(stk_code, query_date)
                    self._current_fundamentals[stk_code] = fund
                except Exception:
                    pass

    # --- 过滤 ---

    def _extra_filter(
        self, candidate: CandidateInfo, context: StrategyContext,
    ) -> bool:
        """溢价率上限 + 次新过滤 + 足够价格历史。"""
        cfg = self._config

        # 溢价率过滤
        if candidate.cb_over_rate is not None:
            if candidate.cb_over_rate > cfg.max_premium:
                return False

        # 次新过滤
        if cfg.use_age_filter and candidate.list_date:
            age = days_since_listed(candidate.list_date, context.current_time)
            if age < cfg.min_age_days:
                return False

        # 需要足够历史来计算波动率
        hist = self._price_history.get(candidate.symbol)
        min_hist = min(cfg.vol_lookback, cfg.reversal_lookback)
        if hist is None or len(hist) < min_hist:
            return False

        return True

    # --- 核心评分 ---

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """灵活6因子排名加权评分（权重=0的因子自动跳过）。

        score = w_vol   * rank(realized_vol, ↑)    → 低波好
              + w_rev   * rank(return_Nd, ↑)        → 超卖好(可选)
              + w_floor * rank(100/close, ↓)        → 近面值好
              + w_prem  * rank(premium, ↑)          → 低溢价好
              + w_roe   * rank(stock_roe, ↓)        → 高ROE好
              + w_size  * rank(issue_size, ↑)       → 小规模好
        """
        cfg = self._config
        if not candidates:
            return []
        n = len(candidates)
        if n == 1:
            return [candidates[0].symbol]

        # --- 提取因子原始值 ---
        vols: list[float] = []
        reversals: list[float] = []
        bond_floors: list[float] = []
        premiums: list[float] = []
        roes: list[float] = []
        sizes: list[float] = []

        for c in candidates:
            # 1. CB 波动率
            vol = self._compute_volatility(c.symbol, cfg.vol_lookback)
            vols.append(vol if vol is not None else 999.0)

            # 2. 短期反转 (可选)
            if cfg.weight_reversal > 0:
                ret = self._compute_return(c.symbol, cfg.reversal_lookback)
                reversals.append(ret if ret is not None else 0.0)

            # 3. 债底距离
            bond_floors.append(100.0 / c.close if c.close > 0 else 0.0)

            # 4. 溢价率
            premiums.append(
                c.cb_over_rate if c.cb_over_rate is not None else 50.0
            )

            # 5. 正股ROE
            stk_code = (c.metadata or {}).get("stk_code", "")
            fund = self._current_fundamentals.get(stk_code)
            if fund is not None and hasattr(fund, "roe") and fund.roe is not None:
                roes.append(fund.roe)
            else:
                roes.append(0.0)

            # 6. 发行规模
            sizes.append(c.issue_size)

        # --- 排名归一化 ---
        rank_vol = _rank_scores(vols, ascending=True)           # 低波好
        rank_floor = _rank_scores(bond_floors, ascending=False)  # 高=近面值好
        rank_prem = _rank_scores(premiums, ascending=True)      # 低溢价好
        rank_roe = _rank_scores(roes, ascending=False)          # 高ROE好
        rank_size = _rank_scores(sizes, ascending=True)         # 小规模好
        rank_rev = (
            _rank_scores(reversals, ascending=True)             # 低收益好
            if cfg.weight_reversal > 0 else [0.0] * n
        )

        # --- 权重归一化 ---
        total_w = (
            cfg.weight_low_vol
            + cfg.weight_reversal
            + cfg.weight_bond_floor
            + cfg.weight_premium
            + cfg.weight_stock_roe
            + cfg.weight_issue_size
        )
        if total_w <= 0:
            total_w = 1.0

        # --- 综合打分 ---
        scored: list[tuple[str, float]] = []
        for i, c in enumerate(candidates):
            score = (
                (cfg.weight_low_vol / total_w) * rank_vol[i]
                + (cfg.weight_reversal / total_w) * rank_rev[i]
                + (cfg.weight_bond_floor / total_w) * rank_floor[i]
                + (cfg.weight_premium / total_w) * rank_prem[i]
                + (cfg.weight_stock_roe / total_w) * rank_roe[i]
                + (cfg.weight_issue_size / total_w) * rank_size[i]
            )
            scored.append((c.symbol, score))

        scored.sort(key=lambda x: x[1])
        return [s for s, _ in scored[: cfg.top_k]]

    # --- 辅助方法 ---

    def _compute_volatility(
        self, symbol: str, lookback: int,
    ) -> float | None:
        """计算N日实现波动率（年化）。"""
        hist = self._price_history.get(symbol)
        if hist is None or len(hist) < lookback:
            return None

        prices = list(hist)[-lookback:]
        returns = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 0:
                returns.append(prices[i] / prices[i - 1] - 1.0)

        if len(returns) < 5:
            return None

        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
        daily_vol = variance ** 0.5
        return daily_vol * (TRADING_DAYS_PER_YEAR ** 0.5)  # 年化

    def _compute_return(
        self, symbol: str, lookback: int,
    ) -> float | None:
        """计算N日累计收益率。"""
        hist = self._price_history.get(symbol)
        if hist is None or len(hist) < lookback + 1:
            return None

        prices = list(hist)
        current = prices[-1]
        past = prices[-(lookback + 1)]

        if past <= 0:
            return None
        return current / past - 1.0
