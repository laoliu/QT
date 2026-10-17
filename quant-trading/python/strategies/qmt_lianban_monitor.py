# coding: utf-8
"""
qmt_lianban_monitor.py — QMT 连板接力实盘监控 (二进三)
======================================================

运行在 Windows miniQMT 内，作为策略文件加载。
功能:
  1. 每日开盘前从 Mac 获取二板候选列表 (screener结果)
  2. 盘中订阅候选股 1 分钟 K 线
  3. 实时监控封板状态 (是否触及涨停价、封板稳定性)
  4. 满足条件时通过 HTTP webhook 推送信号到 Mac Quant Trading
  5. (可选) 通过 xttrader 直接自动下单

信号逻辑:
  - 候选股触及涨停价 → 标记为"触板"
  - 维持封板超 N 分钟且未开板 → 触发"封板确认"信号
  - 封板确认 + 量能配合 → 推送买入信号 / 自动下单
  - 次日监控: 继续封板 → hold; 跌停/大跌 → 紧急卖出

配置:
  - SCREEN_MODE: "remote" (从Mac获取候选) / "manual" (手动指定)
  - SIGNAL_MODE: "http" (推送到Quant Trading) / "file" (写信号文件)
  - AUTO_ORDER:  True (通过xttrader自动下单) / False (仅信号)
"""

import json
import math
import os
import sys
import time
from datetime import datetime

# Add parent dir to path for engine imports
_ENGINES_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ENGINES_PARENT not in sys.path:
    sys.path.insert(0, _ENGINES_PARENT)

try:
    from engines.tick_flow import TickFlowTracker
    from engines.attack_slope import AttackSlopeCalculator
    _HAS_ENGINES = True
except ImportError:
    _HAS_ENGINES = False

# ── Configuration ────────────────────────────────────────────────

STRATEGY_ID = "qmt-lianban-relay"

# 候选股来源
SCREEN_MODE = "remote"  # "remote" | "manual"
MANUAL_STOCK_LIST = [
    # "600519.SH",
]

# 信号输出模式
SIGNAL_MODE = "http"  # "http" | "file"

# 自动下单 (需要 xttrader)
AUTO_ORDER = False
ORDER_ACCOUNT_ID = ""  # QMT 资金账号
ORDER_AMOUNT_PER_STOCK = 100000  # 每只股票最大买入金额 (Ԫ)
ORDER_PRICE_TYPE = 11  # 11=限价 (涨停价挂单), 5=市价

# 封板确认参数
SEAL_CONFIRM_MINUTES = 3         # 持续封板 N 分钟 → 确认
SEAL_RECHECK_INTERVAL = 60       # ÿ 60 秒重新检查封板状态
MAX_OPEN_COUNT = 2               # 盘中开板超 N 次 → 放弃
COOLDOWN_AFTER_SIGNAL = 600      # 同一股票信号间隔 (秒)

# 止损/止盈 (次日监控)
STOP_LOSS_PCT = -0.05            # 亏损 5% 止损
EMERGENCY_SELL_AT_LIMIT_DOWN = True  # 跌停紧急卖出

# Mac Quant Trading 连接
_QUANT_TRADING_HOST = os.environ.get("QUANT_TRADING_HOST", "127.0.0.1")
_APP_PORT = os.environ.get("APP_PORT", "18891")
_QUANT_TRADING_BASE = f"http://{_QUANT_TRADING_HOST}:{_APP_PORT}"
QUANT_TRADING_WEBHOOK_URL = f"{_QUANT_TRADING_BASE}/quant/api/signal"
QUANT_TRADING_WEBHOOK_SECRET = os.environ.get("QUANT_TRADING_SIGNAL_SECRET", "qmt-signal-secret-2026")
QUANT_TRADING_TIMEOUT_SECS = 5

# 候选列表 API (Mac 端 screener 结果)
QUANT_TRADING_CANDIDATES_URL = f"{_QUANT_TRADING_BASE}/quant/api/stocklist"

# 信号文件路径
SIGNALS_PATH = os.path.join(
    os.path.expanduser("~"), "data", "signals.json",
)
MAX_SIGNALS = 500

# ── Helpers ──────────────────────────────────────────────────────


def _safe_float(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _qmt_code(symbol: str) -> str:
    """Convert pure code to QMT format (600519 → 600519.SH)."""
    code = str(symbol).split(".")[0]
    if code.startswith("6") or code.startswith("5") or code.startswith("688"):
        return f"{code}.SH"
    if code.startswith("0") or code.startswith("3"):
        return f"{code}.SZ"
    if code.startswith("4") or code.startswith("8"):
        return f"{code}.BJ"
    return f"{code}.SH"


def _pure_code(qmt_code: str) -> str:
    """600519.SH → 600519"""
    return qmt_code.split(".")[0]


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


def _limit_up_price(prev_close: float, code: str) -> float:
    """Calculate limit-up price."""
    ratio = _limit_up_ratio(code)
    return round(prev_close * (1 + ratio), 2)


def _limit_down_price(prev_close: float, code: str) -> float:
    """Calculate limit-down price."""
    ratio = _limit_up_ratio(code)
    return round(prev_close * (1 - ratio), 2)


# ── Signal Output ────────────────────────────────────────────────


def _build_signal(symbol, direction, price, reason, confidence=0.8):
    return {
        "symbol": symbol,
        "direction": direction,
        "price": round(price, 4),
        "timestamp": int(time.time() * 1000),
        "strategyId": STRATEGY_ID,
        "confidence": confidence,
        "reason": reason,
    }


def _post_signal_http(signal):
    import urllib.request
    import urllib.error

    body = json.dumps(signal, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if QUANT_TRADING_WEBHOOK_SECRET:
        headers["X-Signal-Secret"] = QUANT_TRADING_WEBHOOK_SECRET
    req = urllib.request.Request(
        QUANT_TRADING_WEBHOOK_URL, data=body, headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=QUANT_TRADING_TIMEOUT_SECS) as resp:
            print(f"[{STRATEGY_ID}] Webhook OK: {resp.read().decode()[:120]}")
    except Exception as e:
        print(f"[{STRATEGY_ID}] Webhook 失败: {e}")


def _write_signal_file(signal):
    os.makedirs(os.path.dirname(SIGNALS_PATH), exist_ok=True)
    signals = []
    try:
        with open(SIGNALS_PATH, "r", encoding="utf-8") as f:
            signals = json.load(f)
    except Exception:
        pass
    signals.append(signal)
    signals = signals[-MAX_SIGNALS:]
    with open(SIGNALS_PATH, "w", encoding="utf-8") as f:
        json.dump(signals, f, ensure_ascii=True, indent=2)


def _emit_signal(symbol, direction, price, reason, confidence=0.8):
    signal = _build_signal(symbol, direction, price, reason, confidence)
    if SIGNAL_MODE == "http":
        _post_signal_http(signal)
    else:
        _write_signal_file(signal)
    return signal


# ── Remote Candidate Fetching ────────────────────────────────────


def _fetch_candidates_from_mac() -> list:
    """Fetch relay candidates from Mac Quant Trading screener.

    Expects the Mac screener to have run and posted results to
    /quant/api/stocklist with metadata including lian_ban_count.
    """
    import urllib.request
    import urllib.error

    try:
        req = urllib.request.Request(QUANT_TRADING_CANDIDATES_URL, method="GET")
        with urllib.request.urlopen(req, timeout=QUANT_TRADING_TIMEOUT_SECS) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
            stocks = data.get("data", [])
            if isinstance(stocks, list) and stocks:
                print(f"[{STRATEGY_ID}] 获取候选 {len(stocks)} ֻ: {stocks[:5]}...")
                return stocks
    except Exception as e:
        print(f"[{STRATEGY_ID}] 获取候选失败: {e}")
    return []


# ── Auto-Order via xttrader ──────────────────────────────────────


def _try_auto_order(context, qmt_code, price, direction="buy"):
    """Place order via xttrader if AUTO_ORDER is enabled."""
    if not AUTO_ORDER or not ORDER_ACCOUNT_ID:
        return

    try:
        from xtquant.xttrader import XtQuantTrader, StockAccount

        trader = getattr(context, "_trader", None)
        if not trader:
            print(f"[{STRATEGY_ID}] xttrader 未初始化")
            return

        account = StockAccount(ORDER_ACCOUNT_ID, "STOCK")

        if direction == "buy":
            volume = int(ORDER_AMOUNT_PER_STOCK / price)
            volume = (volume // 100) * 100  # Round to lot
            if volume < 100:
                print(f"[{STRATEGY_ID}] 买入量不足一手 (价格 {price:.2f})")
                return
            order_id = trader.order_stock(
                account, qmt_code, 23, volume,
                ORDER_PRICE_TYPE, price,
                STRATEGY_ID, f"lianban-{_pure_code(qmt_code)}",
            )
            print(f"[{STRATEGY_ID}] 买入下单: {qmt_code} {volume}股 @ {price:.2f}, 委托号 {order_id}")

        elif direction == "sell":
            # Sell all held shares
            positions = trader.query_stock_positions(account)
            for pos in positions:
                if pos.stock_code == qmt_code and pos.can_use_volume > 0:
                    order_id = trader.order_stock(
                        account, qmt_code, 24, pos.can_use_volume,
                        ORDER_PRICE_TYPE, price,
                        STRATEGY_ID, f"lianban-sell-{_pure_code(qmt_code)}",
                    )
                    print(
                        f"[{STRATEGY_ID}] 卖出下单: {qmt_code} "
                        f"{pos.can_use_volume}股 @ {price:.2f}, 委托号 {order_id}"
                    )
    except ImportError:
        print(f"[{STRATEGY_ID}] xttrader 不可用，跳过自动下单")
    except Exception as e:
        print(f"[{STRATEGY_ID}] 下单失败: {e}")


# ── QMT Strategy Callbacks ──────────────────────────────────────


def init(context):
    """QMT 策略初始化。

    1. 获取候选列表
    2. 订阅 1 分钟 K 线
    3. 初始化状态跟踪
    """
    context._stock_list = []
    context._prev_close = {}       # {code: prev_close}
    context._board_state = {}      # {code: {"sealed": bool, "seal_start": ts, "open_count": int}}
    context._signal_time = {}      # {code: last_signal_ts}
    context._held_stocks = set()   # Stocks we bought (for next-day monitoring)
    context._entry_price = {}      # {code: entry_price}

    # Tick-level engines (borrowed from sector-synergy approach)
    if _HAS_ENGINES:
        context._tick_tracker = TickFlowTracker()
        context._slope_calc = AttackSlopeCalculator()
        print(f"[{STRATEGY_ID}] tick_flow + attack_slope 引擎已加载")
    else:
        context._tick_tracker = None
        context._slope_calc = None

    # Initialize xttrader if auto-order enabled
    if AUTO_ORDER and ORDER_ACCOUNT_ID:
        try:
            from xtquant.xttrader import XtQuantTrader
            trader = XtQuantTrader("", 12345)
            trader.start()
            trader.connect()
            context._trader = trader
            print(f"[{STRATEGY_ID}] xttrader 已连接")
        except Exception as e:
            print(f"[{STRATEGY_ID}] xttrader 连接失败: {e}")
            context._trader = None

    _refresh_candidates(context)


def before_trading_start(context, data):
    """每日开盘前刷新候选列表和状态。"""
    # Reset daily state
    context._board_state = {}

    _refresh_candidates(context)

    # Fetch prev_close for all stocks
    for qmt_code in context._stock_list:
        try:
            bar_data = context.get_market_data(
                ["close"], stock_code=[qmt_code],
                period="1d", count=2,
            )
            if bar_data is not None and len(bar_data) >= 2:
                closes = list(bar_data["close"])
                context._prev_close[_pure_code(qmt_code)] = closes[-2]
        except Exception:
            pass


def _refresh_candidates(context):
    """Refresh candidate stock list and subscribe to 1-min K-lines."""
    if SCREEN_MODE == "remote":
        raw = _fetch_candidates_from_mac()
        # raw may be list of codes or list of dicts
        codes = []
        for item in raw:
            if isinstance(item, str):
                codes.append(item)
            elif isinstance(item, dict):
                codes.append(item.get("code", item.get("stock_id", "")))
        codes = [c for c in codes if c]
    else:
        codes = [_pure_code(c) for c in MANUAL_STOCK_LIST]

    # Also keep held stocks for exit monitoring
    all_codes = list(set(codes) | context._held_stocks)

    qmt_codes = [_qmt_code(c) for c in all_codes]
    context._stock_list = qmt_codes

    for qmt_code in qmt_codes:
        context.subscribe_quote(qmt_code, "1m", "front_ratio")

    print(f"[{STRATEGY_ID}] 监控列表: {[_pure_code(c) for c in qmt_codes]}")


def handlebar(context):
    """每根 1 分钟 K 线回调。

    核心监控逻辑:
    1. 检查当前价是否触及涨停价
    2. 维护封板状态 (sealed / opened)
    3. 封板确认达标 → 推送信号 / 下单
    4. 持仓股止损/止盈监控
    """
    now_ts = time.time()
    stock_list = getattr(context, "_stock_list", [])

    for qmt_code in stock_list:
        code = _pure_code(qmt_code)

        # Get latest 1-min bar
        try:
            bar_data = context.get_market_data_ex(
                [qmt_code],
                ["open", "high", "low", "close", "volume"],
                "1m", "", "", 3, "front_ratio", True, False,
            )
            df = bar_data.get(qmt_code)
            if df is None or len(df) == 0:
                continue
            latest_close = float(df["close"].iloc[-1])
            latest_high = float(df["high"].iloc[-1])
            latest_low = float(df["low"].iloc[-1])
            latest_volume = float(df["volume"].iloc[-1])
        except Exception as e:
            continue

        prev_close = context._prev_close.get(code, 0)
        if prev_close <= 0:
            continue

        zt_price = _limit_up_price(prev_close, code)
        dt_price = _limit_down_price(prev_close, code)

        # ── Held stock monitoring (exit) ──
        if code in context._held_stocks:
            entry = context._entry_price.get(code, 0)

            # Exit 1: Limit-down emergency
            if EMERGENCY_SELL_AT_LIMIT_DOWN and latest_close <= dt_price:
                _emit_signal(code, "sell", latest_close, "跌停紧急卖出", 0.95)
                _try_auto_order(context, qmt_code, latest_close, "sell")
                context._held_stocks.discard(code)
                print(f"[{STRATEGY_ID}] 跌停卖出 {code} @ {latest_close:.2f}")
                continue

            # Exit 2: Stop loss
            if entry > 0:
                loss_pct = (latest_close - entry) / entry
                if loss_pct < STOP_LOSS_PCT:
                    _emit_signal(
                        code, "sell", latest_close,
                        f"止损: 亏损{loss_pct*100:.1f}%", 0.9,
                    )
                    _try_auto_order(context, qmt_code, latest_close, "sell")
                    context._held_stocks.discard(code)
                    print(f"[{STRATEGY_ID}] 止损 {code} @ {latest_close:.2f} ({loss_pct*100:.1f}%)")
                    continue

            # Exit 3: Still limit-up → let it ride
            if latest_close >= zt_price:
                continue  # Great, still sealed

        # ── New candidate monitoring (entry) ──
        if code in context._held_stocks:
            continue  # Already holding, skip entry logic

        state = context._board_state.get(code, {
            "sealed": False,
            "seal_start": 0,
            "open_count": 0,
            "last_check": 0,
        })

        # Check if price hit limit-up
        at_limit = latest_close >= zt_price

        if at_limit:
            if not state["sealed"]:
                # Just reached limit-up
                state["sealed"] = True
                state["seal_start"] = now_ts
                print(f"[{STRATEGY_ID}] {code} 触板! 涨停价 {zt_price:.2f}")
            else:
                # Still sealed — check duration
                seal_duration = now_ts - state["seal_start"]
                if (
                    seal_duration >= SEAL_CONFIRM_MINUTES * 60
                    and state["open_count"] <= MAX_OPEN_COUNT
                ):
                    # ── Seal confirmed → emit signal ──
                    last_signal = context._signal_time.get(code, 0)
                    if now_ts - last_signal >= COOLDOWN_AFTER_SIGNAL:
                        reason = (
                            f"封板确认: 持续{seal_duration/60:.0f}分钟, "
                            f"开板{state['open_count']}次, "
                            f"涨停价{zt_price:.2f}"
                        )
                        confidence = 0.85
                        if state["open_count"] == 0:
                            confidence = 0.9
                        if seal_duration >= 10 * 60:
                            confidence = min(confidence + 0.05, 0.95)

                        # Boost confidence with tick-flow analysis
                        if context._tick_tracker:
                            main_net = context._tick_tracker.get_main_net(code)
                            if main_net > 5e7:  # 大单净额 > 5000万
                                confidence = min(confidence + 0.03, 0.98)
                                reason += f", 大单净额{main_net/1e4:.0f}万"
                            if context._tick_tracker.is_sweep_triggered(code):
                                confidence = min(confidence + 0.02, 0.98)
                                reason += ", 有扫单信号"

                        _emit_signal(code, "buy", zt_price, reason, confidence)
                        context._signal_time[code] = now_ts

                        # Auto order
                        _try_auto_order(context, qmt_code, zt_price, "buy")

                        # Track as held
                        context._held_stocks.add(code)
                        context._entry_price[code] = zt_price

                        print(f"[{STRATEGY_ID}] ✅ 封板确认买入 {code} @ {zt_price:.2f}")
        else:
            if state.get("sealed"):
                # Board opened!
                state["sealed"] = False
                state["open_count"] = state.get("open_count", 0) + 1
                print(
                    f"[{STRATEGY_ID}] {code} 开板! 第{state['open_count']}次 "
                    f"(最大{MAX_OPEN_COUNT}次)"
                )
                if state["open_count"] > MAX_OPEN_COUNT:
                    print(f"[{STRATEGY_ID}] {code} 开板过多，放弃")

        context._board_state[code] = state
