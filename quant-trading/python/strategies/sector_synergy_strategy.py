# coding: utf-8
"""
sector_synergy_strategy — 板块协同大单追涨策略 (QMT tick驱动)

借鉴自板块协同大单追涨思路，融入 openclaw quant-trading 体系。
与 qmt_lianban_monitor（封板后确认）互补，本策略在封板前的 6%~9% 区间介入。

买入条件（全部同时满足）：
  1. 涨幅在 6%~9% 之间（vs 昨收）
  2. 板块协同：该股票所属板块有 ≥2 只涨停股
  3. 实际换手率 5%~20%（当日成交量 / 自由流通股本）
  4. 攻击斜率 > 60° (5 分钟价格斜率角度)
  5. 大单净额 > 5000万（单 tick ≥ 100万元计入主力大单）
  6. 涨幅 8%~9% 区间出现万手以上主动买入大单（扫单信号）

卖出规则（对已持仓股票）：
  1. 盈利 ≥ 10%（vs 成本价）→ 全部卖出
  2. 盈利 ≥ 5%（vs 成本价）→ 卖出一半（每只仅一次）
  3. 亏损 ≥ 3%（vs 成本价）→ 全部卖出

架构设计：
  - 使用 engines/tick_flow.TickFlowTracker 做 tick 级大单追踪
  - 使用 engines/attack_slope.AttackSlopeCalculator 做攻击斜率
  - 板块涨停数据支持两种模式：
    a) "file" 模式：读外部 hot_sectors.json（兼容同事的辅助程序）
    b) "api" 模式：通过 engines/market_sentiment 在线获取
  - 信号输出到 OpenClaw webhook（与 qmt_lianban_monitor 共用通道）
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any

# 延迟导入引擎（QMT 环境下引擎文件需在同目录或 sys.path 中）
_tick_flow_mod = None
_slope_mod = None


def _lazy_import():
    global _tick_flow_mod, _slope_mod
    if _tick_flow_mod is None:
        try:
            from engines.tick_flow import TickFlowTracker  # noqa: F811
            from engines.attack_slope import AttackSlopeCalculator  # noqa: F811
            _tick_flow_mod = True
            _slope_mod = True
        except ImportError:
            # Fallback: try relative import (running from strategies/)
            import sys
            parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if parent not in sys.path:
                sys.path.insert(0, parent)
            from engines.tick_flow import TickFlowTracker  # noqa: F811
            from engines.attack_slope import AttackSlopeCalculator  # noqa: F811
            _tick_flow_mod = True
            _slope_mod = True


# ── Configuration ────────────────────────────────────────────────

STRATEGY_ID = "sector-synergy-chase"

# 买入条件参数
CHANGE_LOW = 0.06            # 涨幅下限 6%
CHANGE_HIGH = 0.09           # 涨幅上限 9%
SECTOR_LIMIT_MIN = 2         # 板块最少涨停数
TURNOVER_LOW = 0.05          # 换手率下限 5%
TURNOVER_HIGH = 0.20         # 换手率上限 20%
SLOPE_ANGLE_MIN = 60         # 攻击斜率最小角度 (度)
SLOPE_SCALE = 300            # 斜率缩放系数
SLOPE_WINDOW_SEC = 300       # 斜率计算窗口 (秒), 5分钟
NET_AMOUNT_MIN = 5e7         # 大单净额阈值 5000万
LARGE_ORDER_THRESHOLD = 1e6  # 大单定义: 单 tick ≥ 100万
SWEEP_VOLUME = 1_000_000     # 扫单: 单 tick ≥ 100万股 (1万手)
SWEEP_CHANGE_LOW = 0.08      # 扫单涨幅下限 8%
SWEEP_CHANGE_HIGH = 0.09     # 扫单涨幅上限 9%
BUY_AMOUNT = 300_000         # 每只股票买入金额 (Ԫ)

# 卖出参数
SELL_PROFIT_FULL = 0.10      # 盈利 ≥ 10% 全卖
SELL_PROFIT_HALF = 0.05      # 盈利 ≥ 5% 卖半仓
SELL_LOSS_FULL = -0.03       # 亏损 ≥ 3% 全卖

# 板块数据源
SECTOR_DATA_MODE = "file"    # "file" | "api"
HOT_SECTORS_FILE = "D:\\hot_sectors.json"
SECTOR_CACHE_SEC = 10        # 板块数据缓存时间 (秒)

# QMT 下单
AUTO_ORDER = False
ORDER_ACCOUNT_ID = ""

# 信号输出 (复用 qmt_lianban_monitor 的 webhook)
SIGNAL_MODE = "http"         # "http" | "file"
_OPENCLAW_HOST = os.environ.get("OPENCLAW_HOST", "127.0.0.1")
_APP_PORT = os.environ.get("APP_PORT", "18789")
_OPENCLAW_BASE = f"http://{_OPENCLAW_HOST}:{_APP_PORT}"
OPENCLAW_WEBHOOK_URL = f"{_OPENCLAW_BASE}/quant/api/signal"
OPENCLAW_WEBHOOK_SECRET = os.environ.get("OPENCLAW_SIGNAL_SECRET", "qmt-signal-secret-2026")


# ── Per-Stock State ──────────────────────────────────────────────

@dataclass
class StockState:
    """Per-stock strategy state for position and daily tracking."""

    holding_vol: int = 0
    cost_price: float = 0.0
    pre_close: float = 0.0
    bought_today: bool = False
    half_sold: bool = False
    last_date: str = ""


# ── Strategy State ───────────────────────────────────────────────

class StrategyState:
    """Global strategy state container."""

    def __init__(self):
        self.inited = False
        self.stock_list: list[str] = []      # 监控标的
        self.stock_states: dict[str, StockState] = {}
        self.free_float_map: dict[str, float] = {}    # stock → 自由流通股本(股)
        self.sector_map: dict[str, str] = {}           # stock → 行业板块名
        self.daily_cache: dict[str, Any] = {}          # stock → 日线数据

        # Engines (initialized after lazy import)
        self.tick_tracker: Any = None
        self.slope_calc: Any = None

        # Sector cache
        self._hot_sectors_data: dict = {}
        self._hot_sectors_time: float = 0


# Singleton state
_S = StrategyState()


# ── Signal Output ────────────────────────────────────────────────

def _emit_signal(symbol: str, direction: str, price: float, reason: str,
                 confidence: float = 0.8) -> None:
    signal = {
        "symbol": symbol,
        "direction": direction,
        "price": round(price, 4),
        "timestamp": int(time.time() * 1000),
        "strategyId": STRATEGY_ID,
        "confidence": confidence,
        "reason": reason,
    }
    if SIGNAL_MODE == "http":
        _post_signal_http(signal)
    else:
        _write_signal_file(signal)


def _post_signal_http(signal: dict) -> None:
    import urllib.request
    body = json.dumps(signal, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if OPENCLAW_WEBHOOK_SECRET:
        headers["X-Signal-Secret"] = OPENCLAW_WEBHOOK_SECRET
    req = urllib.request.Request(
        OPENCLAW_WEBHOOK_URL, data=body, headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"[{STRATEGY_ID}] Webhook OK: {resp.read().decode()[:120]}")
    except Exception as e:
        print(f"[{STRATEGY_ID}] Webhook 失败: {e}")


def _write_signal_file(signal: dict) -> None:
    path = os.path.join(
        os.path.expanduser("~"), ".openclaw", "quant-trading",
        "sector-synergy-signals.json",
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    signals: list = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            signals = json.load(f)
    except Exception:
        pass
    signals.append(signal)
    signals = signals[-500:]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(signals, f, ensure_ascii=True, indent=2)


# ── Sector Data ──────────────────────────────────────────────────

def _read_hot_sectors() -> dict:
    """Read sector limit-up data with caching.

    In "file" mode: reads from HOT_SECTORS_FILE (compatible with
    external limit_up_scanner.py output).

    In "api" mode: uses market_sentiment engine to fetch live data
    and build per-sector counts from the limit-up pool.

    Returns:
        {sector_name: {"count": N, "stocks": [...]}, ...}
    """
    now = time.time()
    if now - _S._hot_sectors_time < SECTOR_CACHE_SEC and _S._hot_sectors_data:
        return _S._hot_sectors_data.get("hot_sectors", {})

    if SECTOR_DATA_MODE == "file":
        return _read_hot_sectors_file()
    else:
        return _read_hot_sectors_api()


def _read_hot_sectors_file() -> dict:
    """Read hot sectors from JSON file (external scanner output)."""
    if not os.path.exists(HOT_SECTORS_FILE):
        return {}
    try:
        with open(HOT_SECTORS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _S._hot_sectors_data = data
        _S._hot_sectors_time = time.time()
        return data.get("hot_sectors", {})
    except Exception:
        return _S._hot_sectors_data.get("hot_sectors", {})


def _read_hot_sectors_api() -> dict:
    """Build hot sectors from market_sentiment limit-up pool."""
    try:
        from engines.market_sentiment import lianban_ladder
        result = lianban_ladder()
        if result.get("error"):
            return {}

        # Build per-sector counts from ZT pool
        sectors: dict[str, dict] = {}
        # stocks_by_level includes all ZT stocks with lian_ban_count >= 2
        # but we also need first-board stocks — use total count instead
        # Rebuild from the raw pool if available
        from engines.lianban import fetch_zt_pool
        pool = fetch_zt_pool()
        for stock in pool.get("stocks", []):
            industry = stock.get("industry", "")
            if not industry:
                continue
            if industry not in sectors:
                sectors[industry] = {"count": 0, "stocks": []}
            sectors[industry]["count"] += 1
            sectors[industry]["stocks"].append(
                stock.get("code", "") or stock.get("name", "")
            )

        _S._hot_sectors_data = {"hot_sectors": sectors}
        _S._hot_sectors_time = time.time()
        return sectors
    except Exception as e:
        print(f"[{STRATEGY_ID}] API 获取板块数据失败: {e}")
        return {}


# ── Helpers ──────────────────────────────────────────────────────

def _calc_volume(price: float, amount: float) -> int:
    """Calculate buy volume (rounded down to 100 shares)."""
    if price <= 0:
        return 0
    return int(amount / price / 100) * 100


def _limit_up_ratio(code: str) -> float:
    """Determine limit-up ratio by code prefix."""
    c = str(code).split(".")[0]
    if c.startswith("688"):
        return 0.20
    if c.startswith("30"):
        return 0.20
    if c.startswith("4") or (c.startswith("8") and not c.startswith("688")):
        return 0.30
    return 0.10


def _time_to_seconds(time_str: str) -> float:
    """Convert HHMMSS string to seconds since midnight."""
    try:
        hh = int(time_str[:2])
        mm = int(time_str[2:4])
        ss = int(time_str[4:6]) if len(time_str) >= 6 else 0
        return hh * 3600 + mm * 60 + ss
    except (ValueError, IndexError):
        return 0


# ── QMT Strategy Callbacks ──────────────────────────────────────
# These functions are called by QMT when running as a tick-driven strategy.

def init(C):
    """QMT 策略初始化。读取预选股，初始化引擎。"""
    if _S.inited:
        return

    _lazy_import()
    from engines.tick_flow import TickFlowTracker
    from engines.attack_slope import AttackSlopeCalculator

    # Initialize engines
    _S.tick_tracker = TickFlowTracker(
        large_order_threshold=LARGE_ORDER_THRESHOLD,
        sweep_volume_threshold=SWEEP_VOLUME,
        sweep_change_low=SWEEP_CHANGE_LOW,
        sweep_change_high=SWEEP_CHANGE_HIGH,
    )
    _S.slope_calc = AttackSlopeCalculator(
        window_sec=SLOPE_WINDOW_SEC,
        scale_factor=SLOPE_SCALE,
    )

    # Read preselected stocks from Excel
    try:
        import pandas as pd
    except ImportError:
        print(f"[{STRATEGY_ID}] pandas 未安装，无法读取 Excel")
        _S.inited = True
        return

    excel_file = os.environ.get("PRESELECT_EXCEL", "D:\\preselect_stocks.xlsx")
    try:
        df = pd.read_excel(excel_file, engine="openpyxl")
        print(f"[{STRATEGY_ID}] 读取 Excel 成功, {len(df)} 行")
    except Exception as e:
        print(f"[{STRATEGY_ID}] 读取 Excel 失败: {e}")
        _S.inited = True
        return

    if "stock_code" not in df.columns:
        print(f"[{STRATEGY_ID}] Excel 缺少 stock_code 列")
        _S.inited = True
        return

    _S.stock_list = [str(x).strip() for x in df["stock_code"] if str(x).strip()]
    if not _S.stock_list:
        print(f"[{STRATEGY_ID}] 无有效股票代码")
        _S.inited = True
        return

    # Read optional free_float_shares column
    if "free_float_shares" in df.columns:
        for _, row in df.iterrows():
            stock = str(row["stock_code"]).strip()
            try:
                shares = float(row["free_float_shares"])
                if shares > 0:
                    _S.free_float_map[stock] = shares
            except (ValueError, TypeError):
                pass

    # Read optional sector column
    if "sector" in df.columns:
        for _, row in df.iterrows():
            stock = str(row["stock_code"]).strip()
            sector = str(row.get("sector", "")).strip()
            if sector and sector != "nan":
                _S.sector_map[stock] = sector

    # Try to fill missing data from QMT instrument details
    for stock in _S.stock_list:
        if stock not in _S.free_float_map:
            try:
                detail = C.get_instrument_detail(stock)
                if detail:
                    for fname in ("FloatVolume", "FloatVolumn", "float_volume"):
                        fv = float(getattr(detail, fname, 0) or
                                   (detail.get(fname, 0) if isinstance(detail, dict) else 0))
                        if fv > 0:
                            _S.free_float_map[stock] = fv
                            break
            except Exception:
                pass

        if stock not in _S.sector_map:
            try:
                detail = C.get_instrument_detail(stock)
                if detail:
                    for fname in ("IndustryName", "ThirdIndustryName",
                                  "SecondIndustryName", "industry"):
                        val = (getattr(detail, fname, "") if not isinstance(detail, dict)
                               else detail.get(fname, ""))
                        if val and str(val).strip() not in ("", "nan"):
                            _S.sector_map[stock] = str(val).strip()
                            break
            except Exception:
                pass

    # Subscribe and cache daily data
    C.set_universe(_S.stock_list)
    for stock in _S.stock_list:
        _S.stock_states[stock] = StockState()
        try:
            download_history_data(stock, "1d", "20250101", "")  # noqa: F821 (QMT global)
        except Exception:
            pass
        try:
            daily = C.get_market_data_ex(
                ["close"], [stock], period="1d",
                start_time="20250101", end_time="", count=-1,
                dividend_type="none", subscribe=False,
            )
            if stock in daily and len(daily[stock]) > 0:
                _S.daily_cache[stock] = daily[stock]
        except Exception:
            pass

    _S.inited = True
    print(f"[{STRATEGY_ID}] 初始化完成: {len(_S.stock_list)} 只预选股, "
          f"流通股本 {len(_S.free_float_map)} ֻ, 板块 {len(_S.sector_map)} ֻ")


def handlebar(C):
    """QMT tick 回调：每个 tick 检查买卖条件。"""
    if not _S.stock_list:
        return

    bar_timetag = C.get_bar_timetag(C.barpos)
    bar_datetime = timetag_to_datetime(bar_timetag, "%Y%m%d%H%M%S")  # noqa: F821
    bar_date = bar_datetime[:8]
    bar_time = bar_datetime[8:]
    bar_hhmm = bar_time[:4]

    # Only run during continuous trading session (9:30 ~ 14:57)
    if bar_hhmm < "0930" or bar_hhmm > "1457":
        return

    bar_seconds = _time_to_seconds(bar_time)

    # Batch fetch tick data
    try:
        fields = ["lastPrice", "volume", "amount", "bidPrice1", "askPrice1"]
        all_tick = C.get_market_data_ex(
            fields, _S.stock_list, period="tick",
            start_time=bar_date + "091500", end_time=bar_datetime,
            count=1, subscribe=False,
        )
    except Exception:
        return

    # Read sector data (cached)
    hot_sectors = _read_hot_sectors()

    # Batch fetch positions
    pos_map = _get_position_map()

    for stock in _S.stock_list:
        state = _S.stock_states[stock]

        # ── Daily reset ──
        if bar_date != state.last_date:
            state.last_date = bar_date
            state.bought_today = False
            state.half_sold = False
            _S.tick_tracker.reset(stock, bar_date)
            _S.slope_calc.reset(stock)
            state.pre_close = _get_pre_close(stock, bar_date)
            if state.pre_close > 0:
                print(f"[{bar_date}] [{STRATEGY_ID}] {stock} 昨收 {state.pre_close:.2f}")

        # ── Sync position ──
        _apply_position(stock, state, pos_map)

        # ── Parse tick data ──
        tick_info = _parse_tick(all_tick, stock)
        if tick_info is None:
            continue

        cur_price = tick_info["lastPrice"]
        cur_volume = tick_info["volume"]
        cur_amount = tick_info["amount"]
        bid_price = tick_info.get("bidPrice1", 0.0)
        ask_price = tick_info.get("askPrice1", 0.0)

        if cur_price <= 0:
            continue

        # ── Record to engines ──
        _S.slope_calc.record(stock, bar_seconds, cur_price)

        flow_result = _S.tick_tracker.update(
            stock, cur_price, cur_volume, cur_amount,
            state.pre_close, bid_price, ask_price, bar_date,
        )

        # ── Check sell (priority over buy) ──
        if _check_sell(C, stock, state, cur_price, bar_datetime):
            continue

        # ── Check buy conditions ──
        if state.bought_today or state.pre_close <= 0:
            continue

        if flow_result is None:
            continue

        # Condition 1: Change 6%~9%
        change = flow_result.change_pct
        if change < CHANGE_LOW or change > CHANGE_HIGH:
            continue

        # Condition 2: Sector synergy (>= N limit-up stocks in same sector)
        sector = _S.sector_map.get(stock, "")
        if sector and hot_sectors:
            sector_info = hot_sectors.get(sector)
            if not sector_info or sector_info.get("count", 0) < SECTOR_LIMIT_MIN:
                continue

        # Condition 3: Turnover 5%~20%
        free_float = _S.free_float_map.get(stock, 0)
        if free_float > 0:
            turnover = cur_volume / free_float
            if turnover < TURNOVER_LOW or turnover > TURNOVER_HIGH:
                continue

        # Condition 4: Attack slope > 60°
        slope_angle = _S.slope_calc.get_angle(stock, bar_seconds)
        if slope_angle < SLOPE_ANGLE_MIN:
            continue

        # Condition 5: Main force net > 50M
        if flow_result.main_net < NET_AMOUNT_MIN:
            continue

        # Condition 6: Sweep detected (large aggressive buy in 8%~9% zone)
        if not flow_result.sweep_triggered:
            continue

        # ── All 6 conditions met → BUY ──
        buy_vol = _calc_volume(cur_price, BUY_AMOUNT)
        if buy_vol <= 0:
            continue

        # Order at limit-up price to ensure fill
        ratio = _limit_up_ratio(stock)
        order_price = round(state.pre_close * (1 + ratio), 2)

        # Emit signal
        sector_info = hot_sectors.get(sector, {}) if sector else {}
        sector_count = sector_info.get("count", 0)
        turnover_pct = (cur_volume / free_float * 100) if free_float > 0 else 0
        reason = (
            f"板块协同买入: 涨幅{change*100:.1f}% | "
            f"板块[{sector}]涨停{sector_count}ֻ | "
            f"换手{turnover_pct:.1f}% | "
            f"斜率{slope_angle:.0f}° | "
            f"大单净额{flow_result.main_net/1e4:.0f}万"
        )
        _emit_signal(stock, "buy", cur_price, reason, confidence=0.85)

        # Auto order via QMT
        if AUTO_ORDER and ORDER_ACCOUNT_ID:
            passorder(23, 1101, ORDER_ACCOUNT_ID, stock,  # noqa: F821 (QMT global)
                      11, order_price, float(buy_vol), "", 2, C)

        # Update state
        state.bought_today = True
        old_vol = state.holding_vol
        old_cost = state.cost_price
        if old_vol + buy_vol > 0:
            state.cost_price = (old_cost * old_vol + cur_price * buy_vol) / (old_vol + buy_vol)
        else:
            state.cost_price = cur_price
        state.holding_vol += buy_vol

        print(f"[{bar_datetime}] [{STRATEGY_ID}] ★买入★ {stock} | {reason} | "
              f"{buy_vol}股 @{cur_price:.2f}")

    # Periodic summary
    if C.barpos % 200 == 0:
        holding = [s for s in _S.stock_list if _S.stock_states[s].holding_vol > 0]
        if holding:
            print(f"[{bar_datetime}] [{STRATEGY_ID}] 持仓: {holding}")


# ── Sell Logic ───────────────────────────────────────────────────

def _check_sell(C, stock: str, state: StockState, cur_price: float,
                bar_datetime: str) -> bool:
    """Check sell conditions. Returns True if full sell executed."""
    if state.holding_vol <= 0 or state.cost_price <= 0:
        return False

    profit = (cur_price - state.cost_price) / state.cost_price

    # Rule 1: Profit >= 10% → sell all
    if profit >= SELL_PROFIT_FULL:
        _sell_all(C, stock, state, cur_price, bar_datetime,
                  f"盈利{profit*100:.1f}%≥10%止盈")
        return True

    # Rule 3: Loss >= 3% → sell all (priority over half-sell)
    if profit <= SELL_LOSS_FULL:
        _sell_all(C, stock, state, cur_price, bar_datetime,
                  f"亏损{profit*100:.1f}%≥3%止损")
        return True

    # Rule 2: Profit >= 5% → sell half (once)
    if not state.half_sold and profit >= SELL_PROFIT_HALF:
        half_vol = (state.holding_vol // 200) * 100
        if half_vol >= 100:
            if AUTO_ORDER and ORDER_ACCOUNT_ID:
                passorder(24, 1101, ORDER_ACCOUNT_ID, stock,  # noqa: F821
                          5, -1.0, float(half_vol), "", 2, C)
            _emit_signal(stock, "sell_half", cur_price,
                         f"盈利{profit*100:.1f}%≥5%卖半仓", 0.8)
            state.holding_vol -= half_vol
            state.half_sold = True
            print(f"[{bar_datetime}] [{STRATEGY_ID}] {stock} 卖半仓 "
                  f"{half_vol}股 @{cur_price:.2f}, 剩余{state.holding_vol}股")
    return False


def _sell_all(C, stock: str, state: StockState, price: float,
              bar_datetime: str, reason: str) -> None:
    """Execute full position sell."""
    vol = state.holding_vol
    if vol <= 0:
        return

    if AUTO_ORDER and ORDER_ACCOUNT_ID:
        passorder(24, 1101, ORDER_ACCOUNT_ID, stock,  # noqa: F821
                  5, -1.0, float(vol), "", 2, C)

    profit_pct = ((price - state.cost_price) / state.cost_price * 100
                  if state.cost_price > 0 else 0)
    _emit_signal(stock, "sell", price, reason, 0.9)
    print(f"[{bar_datetime}] [{STRATEGY_ID}] 【{reason}】{stock} "
          f"全卖 {vol}股 @{price:.2f}, 盈亏{profit_pct:.1f}%")

    state.holding_vol = 0
    state.cost_price = 0.0
    state.half_sold = False


# ── Data Helpers ─────────────────────────────────────────────────

def _get_pre_close(stock: str, bar_date: str) -> float:
    """Get previous close from daily cache."""
    if stock not in _S.daily_cache:
        return 0.0
    df = _S.daily_cache[stock]
    for i in range(len(df) - 1, -1, -1):
        idx = str(df.index[i])[:8]
        if idx < bar_date:
            c = float(df.iloc[i]["close"])
            if c > 0:
                return c
    return 0.0


def _parse_tick(all_tick: dict, stock: str) -> dict | None:
    """Parse tick data from QMT batch response."""
    if stock not in all_tick or len(all_tick[stock]) < 1:
        return None

    data = all_tick[stock]
    row = data.iloc[-1] if hasattr(data, "iloc") else data
    cols = list(data.columns) if hasattr(data, "columns") else []

    info: dict[str, Any] = {}

    # lastPrice
    for key in ("lastPrice", "close"):
        if key in cols:
            info["lastPrice"] = float(row[key])
            break
    if "lastPrice" not in info:
        return None

    # volume (cumulative, 股)
    for key in ("volume", "vol"):
        if key in cols:
            info["volume"] = int(float(row[key]))
            break
    info.setdefault("volume", 0)

    # amount (cumulative, Ԫ)
    info["amount"] = float(row["amount"]) if "amount" in cols else 0.0

    # bidPrice1
    for key in ("bidPrice1", "bid1", "bidprice1", "b1"):
        if key in cols:
            info["bidPrice1"] = float(row[key])
            break
    info.setdefault("bidPrice1", 0.0)

    # askPrice1
    for key in ("askPrice1", "ask1", "askprice1", "a1"):
        if key in cols:
            info["askPrice1"] = float(row[key])
            break
    info.setdefault("askPrice1", 0.0)

    return info


def _get_position_map() -> dict:
    """Batch fetch all positions from QMT."""
    try:
        positions = get_trade_detail_data(  # noqa: F821 (QMT global)
            ORDER_ACCOUNT_ID, "stock", "position",
        )
        return {
            f"{pos.m_strInstrumentID}.{pos.m_strExchangeID}": pos
            for pos in positions
        }
    except Exception:
        return {}


def _apply_position(stock: str, state: StockState, pos_map: dict) -> None:
    """Sync position from QMT to strategy state."""
    if stock in pos_map:
        pos = pos_map[stock]
        state.holding_vol = pos.m_nVolume
        if pos.m_dOpenPrice > 0:
            state.cost_price = pos.m_dOpenPrice
    else:
        state.holding_vol = 0
        state.cost_price = 0.0
        state.half_sold = False
