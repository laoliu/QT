"""可转债三低策略（V3 框架集成版）

经过 3 年回测验证（2022-2024）的最优参数策略：
- 溢价率上限：40%（核心发现：从 50% → 40%，年化提升约 7%pt）
- 持仓数量：10 只
- 调仓周期：3 天
- 止损阈值：10%
- 无极低价过滤（保留低价有收益）
- 三因子等权归一化评分：价格 + 溢价率 + 转股价值

回测基线（V3 脚本验证）：
- 年化收益：12.07%
- 总收益：38.85%
- 最大回撤：15.78%
- 卡玛比率：0.76
- 分年度：2022 +5.49%, 2023 +4.83%, 2024 +25.56%

策略通过框架 BacktestEngine 运行时使用 Next-Bar-Open 成交模型，
结果与 V3 脚本（同 bar close 成交）会有细微差异，属于预期行为。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Optional

from aipha.domain.models import Side
from aipha.strategy.base import IntentType, OrderIntent, Strategy, StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationStrategy, CBRotationConfig, CandidateInfo
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ============================================================================
# 配置
# ============================================================================


@dataclass
class TripleLowConfig:
    """三低策略配置（V3 验证版）

    所有参数均来自 3 年回测验证结果，可通过 YAML 配置覆盖。
    """

    # --- 持仓配置 ---
    top_k: int = 10  # 持仓数量
    rebalance_days: int = 3  # 轮动天数

    # --- 筛选条件 ---
    max_price: float = 130.0  # 最高转债价格
    min_price: float = 0.0  # 最低转债价格（0 = 不过滤，V3 验证不过滤更优）
    max_premium: float = 40.0  # 最高溢价率（%），核心参数
    min_issue_size: float = 0.0  # 最小发行规模（万元，静态字段，无前视偏差）
    min_turnover: float = 20.0  # 最小成交额（万元）
    min_days_to_maturity: int = 30  # 最小距到期天数

    # --- 三因子权重（归一化评分）---
    price_weight: float = 0.33  # 价格因子权重
    premium_weight: float = 0.33  # 溢价率因子权重
    cb_value_weight: float = 0.34  # 转股价值因子权重

    # --- 小规模加分（大 V 经验；验证影响不大，保留可配置）---
    # 使用 issue_size（发行规模，静态字段）替代 remain_size，消除前视偏差
    low_size_threshold: float = 30000.0  # 小规模阈值（万元，3 亿）
    low_size_bonus: float = 0.1  # 小规模评分折扣

    # --- 风控 ---
    stop_loss_pct: float = 0.10  # 单只止损比例
    force_redeem_threshold: float = 130.0  # 强赎预警转股价值阈值
    force_redeem_days: int = 8  # 连续多少天触发剔除

    # --- 排除条件 ---
    exclude_called: bool = True  # 排除已公告强赎
    exclude_st: bool = True  # 排除 ST 正股

    # --- 换手控制 ---
    buffer_k: int = 5  # 持仓缓冲：已持有的标的排名在 top_k + buffer_k 内则保留

    # --- 交易 ---
    lot_size: int = 10  # 可转债 10 张一手
    equal_weight: bool = True  # 等权配置


# ============================================================================
# 评分
# ============================================================================


@dataclass
class TripleLowScore:
    """三低评分结果

    采用 min-max 归一化评分，得分越低越好。
    """

    ts_code: str
    name: str
    price: float
    premium_rate: float  # 溢价率（%）
    cb_value: float  # 转股价值
    issue_size: float  # 发行规模（万元，静态字段，无前视偏差）

    # 归一化因子得分
    price_score: float = 0.0
    premium_score: float = 0.0
    cb_value_score: float = 0.0

    # 综合得分
    total_score: float = 0.0


def calculate_normalized_scores(
    bonds: list[dict],
    price_weight: float = 0.33,
    premium_weight: float = 0.33,
    cb_value_weight: float = 0.34,
    low_size_threshold: float = 30000.0,
    low_size_bonus: float = 0.1,
) -> list[TripleLowScore]:
    """V3 归一化评分算法

    对筛选后的转债池做 min-max 归一化，加权求和。
    得分越低越好（低价、低溢价、低转股价值 → 得分低）。

    Args:
        bonds: 转债数据列表，每项需含 ts_code, name, price, premium_rate, cb_value, issue_size
        price_weight: 价格因子权重
        premium_weight: 溢价率因子权重
        cb_value_weight: 转股价值因子权重
        low_size_threshold: 低余额阈值（万元）
        low_size_bonus: 低余额评分折扣比例

    Returns:
        按得分升序排列的 TripleLowScore 列表
    """
    if not bonds:
        return []

    prices = [b["price"] for b in bonds]
    premiums = [b["premium_rate"] for b in bonds]
    cb_values = [b["cb_value"] for b in bonds]

    p_min, p_max = min(prices), max(prices)
    pr_min, pr_max = min(premiums), max(premiums)
    cv_min, cv_max = min(cb_values), max(cb_values)

    scores: list[TripleLowScore] = []

    for b in bonds:
        # Min-max 归一化（避免除零加 0.01）
        p_norm = (b["price"] - p_min) / (p_max - p_min + 0.01)
        pr_norm = (b["premium_rate"] - pr_min) / (pr_max - pr_min + 0.01)
        cv_norm = (b["cb_value"] - cv_min) / (cv_max - cv_min + 0.01)

        total = (
            price_weight * p_norm
            + premium_weight * pr_norm
            + cb_value_weight * cv_norm
        )

        # 小规模加分（使用 issue_size 静态字段，无前视偏差）
        if low_size_bonus > 0:
            issue_size = b.get("issue_size", 50000.0)
            if issue_size < low_size_threshold:
                total *= 1 - low_size_bonus

        scores.append(
            TripleLowScore(
                ts_code=b["ts_code"],
                name=b.get("name", b["ts_code"]),
                price=b["price"],
                premium_rate=b["premium_rate"],
                cb_value=b["cb_value"],
                issue_size=b.get("issue_size", 0),
                price_score=p_norm,
                premium_score=pr_norm,
                cb_value_score=cv_norm,
                total_score=total,
            )
        )

    scores.sort(key=lambda x: x.total_score)
    return scores


# ============================================================================
# 策略
# ============================================================================


@StrategyRegistry().register("triple_low")
class TripleLowStrategy(CBRotationStrategy):
    """可转债三低策略（V3 框架集成版）

    继承 CBRotationStrategy 基类，实现三低评分逻辑。
    覆写 on_bar 保持 V3 验证版的完整行为。

    核心逻辑：
    1. 风控过滤（价格、溢价、成交、规模、ST、临期、强赎）
    2. 归一化三因子评分（价格 + 溢价 + 转股价值）
    3. 选择综合得分最低的 top_k 只
    4. 定期轮动 + 个股止损

    数据通过 Bar.metadata 获取可转债特有字段：
    - cb_value: 转股价值
    - cb_over_rate: 转股溢价率（%）
    - issue_size: 发行规模（万元，静态字段）
    - amount: 成交额（万元）
    - stk_short_name: 正股简称
    - maturity_date: 到期日期（YYYYMMDD 字符串）
    - is_called: 是否已公告强赎
    """

    name = "triple_low"

    @property
    def config(self) -> TripleLowConfig:
        """返回策略配置（满足基类抽象属性要求）。"""
        return self._tl_config

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """对候选池打分（满足基类抽象方法要求）。

        注意：TripleLowStrategy 覆写了 on_bar，实际评分在 _filter_bonds + calculate_normalized_scores 中完成。
        此方法仅作为基类接口合规实现。
        """
        bonds = []
        for c in candidates:
            meta = {}
            if hasattr(c, 'metadata') and c.metadata:
                meta = c.metadata
            bonds.append({
                "ts_code": c.symbol if hasattr(c, 'symbol') else str(c),
                "name": meta.get("name", ""),
                "price": c.close if hasattr(c, 'close') else 0,
                "premium_rate": meta.get("cb_over_rate", 0),
                "cb_value": meta.get("cb_value", 0),
                "issue_size": meta.get("issue_size", 0),
            })

        scores = calculate_normalized_scores(
            bonds=bonds,
            price_weight=self._tl_config.price_weight,
            premium_weight=self._tl_config.premium_weight,
            cb_value_weight=self._tl_config.cb_value_weight,
            low_size_threshold=self._tl_config.low_size_threshold,
            low_size_bonus=self._tl_config.low_size_bonus,
        )
        return [s.ts_code for s in scores[:self._tl_config.top_k]]

    def __init__(
        self,
        config: TripleLowConfig | None = None,
        **kwargs,
    ):
        super().__init__()
        self._tl_config = config or TripleLowConfig()

        # 允许 kwargs 覆盖配置字段
        for key, value in kwargs.items():
            if hasattr(self._tl_config, key):
                setattr(self._tl_config, key, value)

        # 状态
        self._bar_count = 0
        self._last_rebalance_bar: int | None = None  # 上次调仓时的 bar 计数
        self._last_rebalance_date: date | None = None
        self._current_targets: set[str] = set()
        self._entry_prices: dict[str, float] = {}  # 止损用
        self._stop_loss_codes: set[str] = set()  # 本轮止损过的标的
        self._force_redeem_counter: dict[str, int] = {}  # 强赎预警计数

    def on_start(self) -> None:
        """策略启动时记录配置参数。"""
        logger.info(
            f"三低策略启动 | top_k={self.config.top_k} | "
            f"rebalance={self.config.rebalance_days}d | "
            f"max_premium={self.config.max_premium}% | "
            f"weights={self.config.price_weight}/{self.config.premium_weight}/"
            f"{self.config.cb_value_weight}"
        )

    def on_bar(
        self,
        bars: dict[str, "Bar"],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        """处理新一根 K 线，执行止损检查、强赎预警与定期调仓。"""
        self._bar_count += 1
        current_date = context.current_time.date()
        intents: list[OrderIntent] = []

        # 1. 止损检查（每日执行，不限于调仓日）
        stop_intents = self._check_stop_loss(bars, context)
        intents.extend(stop_intents)

        # 2. 更新强赎预警计数
        self._update_force_redeem_counter(bars)

        # 3. 判断是否调仓
        if not self._should_rebalance(current_date):
            return intents

        # 4. 风控过滤
        filtered_bonds = self._filter_bonds(bars, current_date)

        if not filtered_bonds:
            logger.warning(f"[{current_date}] 风控过滤后无符合条件的转债")
            return intents

        # 5. 归一化评分
        scores = calculate_normalized_scores(
            bonds=filtered_bonds,
            price_weight=self.config.price_weight,
            premium_weight=self.config.premium_weight,
            cb_value_weight=self.config.cb_value_weight,
            low_size_threshold=self.config.low_size_threshold,
            low_size_bonus=self.config.low_size_bonus,
        )

        # 6. 选择目标持仓（带持仓缓冲减少换手）
        target_symbols = self._select_targets(scores, context.positions)

        # 7. 生成轮动指令
        rebalance_intents = self._generate_rebalance_intents(
            target_symbols=target_symbols,
            scores={s.ts_code: s for s in scores},
            current_positions=context.positions,
            cash=context.cash,
            bars=bars,
            nav=context.nav,
        )
        intents.extend(rebalance_intents)

        self._last_rebalance_bar = self._bar_count
        self._last_rebalance_date = current_date
        self._stop_loss_codes.clear()  # 新一轮清空止损标记

        if rebalance_intents:
            logger.info(
                f"三低轮动 | {current_date} | "
                f"可选: {len(filtered_bonds)} | 目标: {len(target_symbols)} | "
                f"卖: {sum(1 for i in rebalance_intents if i.side == Side.SELL)} | "
                f"买: {sum(1 for i in rebalance_intents if i.side == Side.BUY)}"
            )

        return intents

    # ------------------------------------------------------------------
    # 止损
    # ------------------------------------------------------------------

    def _check_stop_loss(
        self,
        bars: dict[str, "Bar"],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        """逐只检查止损"""
        intents: list[OrderIntent] = []
        for symbol, qty in context.positions.items():
            if qty <= 0 or symbol in self._stop_loss_codes:
                continue
            entry_price = self._entry_prices.get(symbol)
            if entry_price is None or entry_price <= 0:
                continue
            bar = bars.get(symbol)
            if bar is None:
                continue
            loss_pct = (entry_price - bar.close) / entry_price
            if loss_pct >= self.config.stop_loss_pct:
                intents.append(
                    OrderIntent(
                        symbol=symbol,
                        side=Side.SELL,
                        qty=qty,
                        intent_type=IntentType.ORDER,
                        reason=f"止损 跌幅={loss_pct:.1%}",
                    )
                )
                self._stop_loss_codes.add(symbol)
                logger.info(f"[止损] {symbol} 跌幅 {loss_pct:.1%}")
        return intents

    # ------------------------------------------------------------------
    # 强赎预警
    # ------------------------------------------------------------------

    def _update_force_redeem_counter(self, bars: dict[str, "Bar"]) -> None:
        """更新强赎连续计数"""
        for symbol, bar in bars.items():
            meta = bar.metadata if bar.metadata is not None else {}
            cb_value = meta.get("cb_value")
            if cb_value is None:
                continue
            try:
                cb_val = float(cb_value)
            except (TypeError, ValueError):
                continue
            if cb_val >= self.config.force_redeem_threshold:
                self._force_redeem_counter[symbol] = (
                    self._force_redeem_counter.get(symbol, 0) + 1
                )
            else:
                self._force_redeem_counter[symbol] = 0

    # ------------------------------------------------------------------
    # 调仓判断
    # ------------------------------------------------------------------

    def _should_rebalance(self, current_date: date) -> bool:
        """基于交易日计数判断是否调仓（与 V3 脚本一致）"""
        if self._last_rebalance_bar is None:
            # 首次调仓需等待 rebalance_days 个交易日
            return self._bar_count >= self.config.rebalance_days
        return (self._bar_count - self._last_rebalance_bar) >= self.config.rebalance_days

    # ------------------------------------------------------------------
    # 风控过滤
    # ------------------------------------------------------------------

    def _filter_bonds(
        self,
        bars: dict[str, "Bar"],
        current_date: date,
    ) -> list[dict]:
        """从 bars 中提取可转债数据并做风控过滤

        Returns:
            通过过滤的转债列表，每项为 dict
        """
        result: list[dict] = []

        for symbol, bar in bars.items():
            meta = bar.metadata if bar.metadata is not None else {}
            if not meta:
                continue

            price = bar.close
            premium_rate = meta.get("cb_over_rate")
            cb_value = meta.get("cb_value")
            issue_size = meta.get("issue_size")
            amount = meta.get("amount")
            stk_short_name = meta.get("stk_short_name", "")
            maturity_date_str = meta.get("maturity_date", "")
            is_called = meta.get("is_called", False)

            # 跳过缺失关键字段
            if premium_rate is None or cb_value is None:
                continue

            try:
                premium_rate = float(premium_rate)
                cb_value = float(cb_value)
            except (TypeError, ValueError):
                continue

            # --- 筛选条件 ---

            # 价格区间
            if price <= 0:
                continue
            if self.config.min_price > 0 and price < self.config.min_price:
                continue
            if price > self.config.max_price:
                continue

            # 溢价率上限
            if premium_rate > self.config.max_premium:
                continue
            if premium_rate < -50:  # 异常值
                continue

            # 成交额
            if amount is not None:
                try:
                    if float(amount) < self.config.min_turnover:
                        continue
                except (TypeError, ValueError):
                    pass

            # 发行规模（静态字段，无前视偏差）
            if issue_size is not None:
                try:
                    iss = float(issue_size)
                    if iss < self.config.min_issue_size:
                        continue
                except (TypeError, ValueError):
                    pass

            # ST / 退市
            if self.config.exclude_st and stk_short_name:
                name_upper = str(stk_short_name).upper()
                if "ST" in name_upper or "退" in stk_short_name or "*" in stk_short_name:
                    continue

            # 临期
            if maturity_date_str and self.config.min_days_to_maturity > 0:
                try:
                    mat_date = datetime.strptime(str(maturity_date_str)[:8], "%Y%m%d").date()
                    min_maturity = current_date + timedelta(
                        days=self.config.min_days_to_maturity
                    )
                    if mat_date <= min_maturity:
                        continue
                except (ValueError, TypeError):
                    pass

            # 已强赎
            if self.config.exclude_called and is_called:
                continue

            # 强赎预警
            if (
                self._force_redeem_counter.get(symbol, 0)
                >= self.config.force_redeem_days
            ):
                continue

            # 通过所有过滤
            result.append(
                {
                    "ts_code": symbol,
                    "name": meta.get("bond_short_name", symbol),
                    "price": price,
                    "premium_rate": premium_rate,
                    "cb_value": cb_value,
                    "issue_size": float(issue_size) if issue_size is not None else 50000.0,
                }
            )

        return result

    # ------------------------------------------------------------------
    # 目标选择
    # ------------------------------------------------------------------

    def _select_targets(
        self,
        scores: list[TripleLowScore],
        current_positions: dict[str, int] | None = None,
    ) -> set[str]:
        """选择目标持仓，带持仓缓冲以减少不必要换手

        持仓缓冲逻辑（hysteresis）：
        - 已持有标的只要排名在 top_k + buffer_k 范围内就保留
        - 新标的从 top_k 排名中补充剩余空位
        - 如保留的已持有标的数 > top_k，裁掉排名最差的
        """
        held = set()
        if current_positions:
            held = {s for s, q in current_positions.items() if q > 0}

        # 全部排名（scores 已按 total_score 升序）
        ranked_codes = [s.ts_code for s in scores]
        buffer_k = self.config.buffer_k
        keep_zone = set(ranked_codes[: self.config.top_k + buffer_k])

        # 保留还在 keep_zone 内且未止损的已持有标的
        retained = {
            s for s in held
            if s in keep_zone and s not in self._stop_loss_codes
        }

        # 如果保留过多，裁掉排名最差的
        if len(retained) > self.config.top_k:
            retained_ranked = sorted(
                retained,
                key=lambda s: ranked_codes.index(s) if s in ranked_codes else float("inf"),
            )
            retained = set(retained_ranked[: self.config.top_k])

        # 从 top 排名中补充剩余空位
        targets = set(retained)
        for s in scores:
            if len(targets) >= self.config.top_k:
                break
            if s.ts_code not in targets and s.ts_code not in self._stop_loss_codes:
                targets.add(s.ts_code)

        self._current_targets = targets
        return targets

    # ------------------------------------------------------------------
    # 轮动指令
    # ------------------------------------------------------------------

    def _generate_rebalance_intents(
        self,
        target_symbols: set[str],
        scores: dict[str, TripleLowScore],
        current_positions: dict[str, int],
        cash: float,
        bars: dict[str, "Bar"],
        nav: float,
    ) -> list[OrderIntent]:
        intents: list[OrderIntent] = []

        # 1. 卖出不在目标池的持仓
        for symbol, qty in current_positions.items():
            if symbol not in target_symbols and qty > 0:
                score = scores.get(symbol)
                reason = "三低轮出"
                if score:
                    reason += f" (score={score.total_score:.3f})"
                intents.append(
                    OrderIntent(
                        symbol=symbol,
                        side=Side.SELL,
                        qty=qty,
                        intent_type=IntentType.ORDER,
                        reason=reason,
                    )
                )
                # 清除入场价
                self._entry_prices.pop(symbol, None)

        # 2. 需要买入的标的（不在当前持仓中的目标）
        symbols_to_buy = target_symbols - set(current_positions.keys())
        if not symbols_to_buy:
            return intents

        # 3. 等权配置：以 NAV / 全部目标数 计算每个仓位目标值
        #    与 V3 脚本逻辑对齐：target_value = NAV / len(target_codes)
        #    按得分升序排列以保证确定性（得分低=更优先买入）
        if self.config.equal_weight and symbols_to_buy:
            per_position_value = nav / len(target_symbols)
            sorted_to_buy = sorted(
                symbols_to_buy,
                key=lambda s: scores[s].total_score if s in scores else float("inf"),
            )

            for symbol in sorted_to_buy:
                bar = bars.get(symbol)
                if bar is None:
                    continue

                qty = (
                    int(per_position_value / bar.close / self.config.lot_size)
                    * self.config.lot_size
                )

                if qty >= self.config.lot_size:
                    score = scores.get(symbol)
                    reason = "三低轮入"
                    if score:
                        reason += (
                            f" (score={score.total_score:.3f}"
                            f" p={score.price:.1f}"
                            f" prem={score.premium_rate:.1f}%"
                            f" cv={score.cb_value:.1f})"
                        )
                    intents.append(
                        OrderIntent(
                            symbol=symbol,
                            side=Side.BUY,
                            qty=qty,
                            intent_type=IntentType.ORDER,
                            reason=reason,
                        )
                    )
                    # 入场价先设估计值，on_fill 回调会用实际成交价覆盖
                    self._entry_prices[symbol] = bar.close

        return intents

    # ------------------------------------------------------------------
    # 成交回调
    # ------------------------------------------------------------------

    def on_fill(self, symbol: str, side: "Side", qty: int, price: float) -> None:
        """引擎成交回调 — 用实际成交价更新止损入场价"""
        from aipha.domain.models import Side as _Side

        if side == _Side.BUY:
            self._entry_prices[symbol] = price
        elif side == _Side.SELL:
            self._entry_prices.pop(symbol, None)

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def get_current_targets(self) -> set[str]:
        """返回当前目标持仓标的集合的副本。"""
        return self._current_targets.copy()
