# coding: utf-8
"""
qmt_dual_ma_5m.py   QMT 5
==============================================


  - 5 K (EMA12)(EMA26)
  -     
  -  ~/data/signals.json
  - Quant Trading  +  +


  1.  QuanTX miniQMT 
  2.   
  3.  "  " 
  4.  5 
  5. /

 signals.json
  {
    "symbol": "600519",          # 
    "direction": "buy"|"sell",
    "price": 1888.00,            # 
    "timestamp": 1706000000000,  # ms
    "strategyId": "qmt-dual-ma-5m",
    "confidence": 0.8,
    "reason": "EMA12EMA26 "
  }

Quant Trading signals.json
  Windows: C:\\Users\\<>\\data\\signals.json
  macOS:   ~/data/signals.json
"""

import json
import os
import time
import math

# 
#  
# 

STRATEGY_ID   = "qmt-dual-ma-5m"       #  Quant Trading
FAST_PERIOD   = 12                      #  EMA 
SLOW_PERIOD   = 26                      #  EMA 
SIGNAL_COOLDOWN_SECS = 300              # 
CONFIDENCE    = 0.8                     # 0-1 Quant Trading

# .
# SCREEN_MODE="auto"     _screen_stocks()  xtdata 
# SCREEN_MODE="remote"   Mac  GET /quant/api/stocklist
# SCREEN_MODE="manual"   STOCK_LIST 
SCREEN_MODE = "remote"

# ——  SCREEN_MODE="manual" ——
STOCK_LIST = [
    "600519.SH",   # 
    "000858.SZ",   # 
    "600036.SH",   # 
    "000333.SZ",   # 
    "002594.SZ",   # 
]

# ——  SCREEN_MODE="auto" ——
SCREEN_UNIVERSE   = "沪深A股"   # xtdata.get_stock_list_in_sector() 
SCREEN_TOP_N      = 20          # N
SCREEN_MIN_PRICE  = 5.0         # （）
SCREEN_MAX_PRICE  = 500.0       # （ST / ）
SCREEN_MIN_VOLUME = 50_000_000  # 5000（/）
SCREEN_DAYS       = 5           # N  K
SCREEN_MA_PERIOD  = 20          # N  MA

#   
#   "file":  QMT  Quant Trading  signals.json
#   "http": QMT  WindowsQuant Trading  Mac/ HTTP
SIGNAL_MODE = "http"   #  "file" 

#  HTTP SIGNAL_MODE="http" 
# Mac  IP + 端口，通过环境变量 QUANT_TRADING_HOST / APP_PORT 配置
# 支持 Tailscale / 局域网 / VPN 等多种网络拓扑
_QUANT_TRADING_HOST = os.environ.get("QUANT_TRADING_HOST", "127.0.0.1")
_APP_PORT = os.environ.get("APP_PORT", "18891")
_QUANT_TRADING_BASE = f"http://{_QUANT_TRADING_HOST}:{_APP_PORT}"

QUANT_TRADING_WEBHOOK_URL    = f"{_QUANT_TRADING_BASE}/quant/api/signal"
QUANT_TRADING_WEBHOOK_SECRET = "qmt-signal-secret-2026"  #  quant-trading.json signalWebhook.secret
QUANT_TRADING_TIMEOUT_SECS  = 5               # HTTP

# Mac  GET /quant/api/stocklist   SCREEN_MODE="remote" 
QUANT_TRADING_STOCKLIST_URL = f"{_QUANT_TRADING_BASE}/quant/api/stocklist"

# Quant Trading signals.json Windows
SIGNALS_PATH = os.path.join(os.path.expanduser("~"), "data", "signals.json")
MAX_SIGNALS  = 500   # signals.json 

# 
#  Quant Trading
# 

def _load_signals():
    """ signals.json
    
     WindowsGBK/GB2312 UTF-8 
    """
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb2312"):
        try:
            with open(SIGNALS_PATH, "r", encoding=enc) as f:
                return json.load(f)
        except UnicodeDecodeError:
            continue
        except Exception:
            return []
    return []


def _build_signal_payload(symbol: str, direction: str, price: float, reason: str) -> dict:
    """Construct the canonical signal dict used by both transport modes."""
    return {
        "symbol":     symbol,
        "direction":  direction,       # "buy" | "sell"
        "price":      round(price, 4),
        "timestamp":  int(time.time() * 1000),
        "strategyId": STRATEGY_ID,
        "confidence": CONFIDENCE,
        "reason":     reason,
    }


def _post_signal_http(signal: dict):
    """
     HTTP POST  Quant Trading

    Windows QMT  Mac Quant Trading  VPN Tailscale
     18789  Quant Trading
    """
    import urllib.request
    import urllib.error

    body = json.dumps(signal, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if QUANT_TRADING_WEBHOOK_SECRET:
        headers["X-Signal-Secret"] = QUANT_TRADING_WEBHOOK_SECRET

    req = urllib.request.Request(
        QUANT_TRADING_WEBHOOK_URL,
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=QUANT_TRADING_TIMEOUT_SECS) as resp:
            resp_body = resp.read().decode("utf-8")
            print(f"[{STRATEGY_ID}] Webhook OK: {resp_body[:120]}")
    except urllib.error.HTTPError as e:
        print(f"[{STRATEGY_ID}] Webhook HTTP  {e.code}: {e.read().decode('utf-8')[:200]}")
    except Exception as e:
        print(f"[{STRATEGY_ID}] Webhook : {e}")


def _write_signal(symbol: str, direction: str, price: float, reason: str):
    """
     Quant Trading

    SIGNAL_MODE="file" :  signals.json
    SIGNAL_MODE="http" : HTTP POST  Quant Trading
    """
    signal = _build_signal_payload(symbol, direction, price, reason)

    if SIGNAL_MODE == "http":
        _post_signal_http(signal)
        return

    #  file  
    os.makedirs(os.path.dirname(SIGNALS_PATH), exist_ok=True)
    signals = _load_signals()
    signals.append(signal)
    #  MAX_SIGNALS 
    signals = signals[-MAX_SIGNALS:]
    # ensure_ascii=True  ASCII Windows GBK/UTF-8 
    with open(SIGNALS_PATH, "w", encoding="utf-8") as f:
        json.dump(signals, f, ensure_ascii=True, indent=2)


def _last_signal_time(symbol: str, direction: str) -> float:
    """ 0"""
  
    try:
        signals = _load_signals()
        matches = [
            s["timestamp"] / 1000.0
            for s in signals
            if s.get("symbol") == symbol
            and s.get("direction") == direction
            and s.get("strategyId") == STRATEGY_ID
        ]
        return max(matches) if matches else 0.0
    except Exception:
        return 0.0


# 
#  
# 

def _screen_stocks(context) -> list:
    """
     SCREEN_UNIVERSE  SCREEN_TOP_N 

     (xtdata):
    1.  SCREEN_MIN_PRICE ~ SCREEN_MAX_PRICE
    2.  SCREEN_MIN_VOLUME 
    3.  SCREEN_DAYS  / ()
    4.  MA(SCREEN_MA_PERIOD)
    5.   SCREEN_DAYS  N 

     [] context._stock_list
    """
    try:
        import xtdata  # miniQMT 

        all_codes = xtdata.get_stock_list_in_sector(SCREEN_UNIVERSE)
        if not all_codes:
            print(f"[{STRATEGY_ID}]  '{SCREEN_UNIVERSE}' ")
            return STOCK_LIST[:]

        count_needed = max(SCREEN_MA_PERIOD, SCREEN_DAYS) + 5
        fields = ["close", "volume"]
        bar_data = xtdata.get_market_data_ex(
            fields,
            stock_code=all_codes,
            period="1d",
            start_time="",
            end_time="",
            count=count_needed,
            dividend_type="front_ratio",
        )

        candidates = []
        for code, df in bar_data.items():
            try:
                if df is None or len(df) < SCREEN_MA_PERIOD + 1:
                    continue
                closes  = list(df["close"])
                volumes = list(df["volume"])

                latest_close  = closes[-1]
                latest_volume = volumes[-1]

                #  1: 
                if not (SCREEN_MIN_PRICE <= latest_close <= SCREEN_MAX_PRICE):
                    continue
                #  2: 
                if latest_volume < SCREEN_MIN_VOLUME:
                    continue
                #  3: MA (>MA )
                ma = sum(closes[-SCREEN_MA_PERIOD:]) / SCREEN_MA_PERIOD
                if latest_close < ma:
                    continue
                #  4: N 
                base_close = closes[-SCREEN_DAYS - 1]
                if base_close <= 0:
                    continue
                gain = (latest_close - base_close) / base_close

                candidates.append((code, gain, latest_volume))
            except Exception:
                continue

        if not candidates:
            print(f"[{STRATEGY_ID}]  STOCK_LIST")
            return STOCK_LIST[:]

        #  N
        candidates.sort(key=lambda x: x[1], reverse=True)
        selected = [c[0] for c in candidates[:SCREEN_TOP_N]]
        print(f"[{STRATEGY_ID}]  {len(selected)} : {selected}")
        return selected

    except ImportError:
        #  xtdata  STOCK_LIST
        print(f"[{STRATEGY_ID}] xtdata  STOCK_LIST")
        return STOCK_LIST[:]
    except Exception as e:
        print(f"[{STRATEGY_ID}]  {e}  STOCK_LIST")
        return STOCK_LIST[:]


# 
#  EMA  
# 

def _calc_ema(closes: list, period: int) -> list:
    """ closes  EMA  period-1  None"""
  
    if len(closes) < period:
        return [None] * len(closes)
    k = 2.0 / (period + 1)
    emas = [None] * (period - 1)
    seed = sum(closes[:period]) / period
    emas.append(seed)
    for price in closes[period:]:
        emas.append(price * k + emas[-1] * (1 - k))
    return emas


# 
#   QMT  
# 

def _fetch_remote_stocklist() -> list:
    """
     Mac  GET QUANT_TRADING_STOCKLIST_URL

    Mac  qmt_screener_mac.py  POST /quant/api/stocklist
     Windows QMT  before_trading_start  
     STOCK_LIST
    """
    import urllib.request, urllib.error

    try:
        req = urllib.request.Request(QUANT_TRADING_STOCKLIST_URL, method="GET")
        with urllib.request.urlopen(req, timeout=QUANT_TRADING_TIMEOUT_SECS) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
            stocks = data.get("data", [])
            if isinstance(stocks, list) and stocks:
                print(f"[{STRATEGY_ID}]  {len(stocks)} : {stocks[:5]}...")
                return stocks
            print(f"[{STRATEGY_ID}]  STOCK_LIST")
            return STOCK_LIST[:]
    except Exception as e:
        print(f"[{STRATEGY_ID}]  {e}  STOCK_LIST")
        return STOCK_LIST[:]


def _refresh_stock_list(context):
    """
     subscribe_quote
     init()  before_trading_start() 
    """
    if SCREEN_MODE == "remote":
        new_list = _fetch_remote_stocklist()
    elif SCREEN_MODE == "auto":
        new_list = _screen_stocks(context)
    else:
        new_list = STOCK_LIST[:]

    context._stock_list = new_list

    for code in new_list:
        context.subscribe_quote(code, "5m", "front_ratio")

    print(f"[{STRATEGY_ID}] : {new_list}")


def init(context):
    """
    
    
    """
    #  bar K
    context._prev_cross = {}   # { "600519": "golden" | "death" | None }

    #  /
    _refresh_stock_list(context)


def before_trading_start(context, data):
    """
     QMT  before_trading_start  
     context._stock_list
    """
    _refresh_stock_list(context)


def handlebar(context):
    """
    5K
    / Quant Trading
    """
    now_ts = time.time()
    stock_list = getattr(context, "_stock_list", STOCK_LIST)

    for qmt_code in stock_list:
        # Quant Trading
        pure_code = qmt_code.split(".")[0]

        #   K  
        # get_market_data_ex  dict{ code: DataFrame }
        #  xtquant (get_market_data  deprecated)
        try:
            bar_data = context.get_market_data_ex(
                ["close", "open", "high", "low", "volume"],
                stock_code=[qmt_code],
                period="5m",
                start_time="",
                end_time="",
                count=SLOW_PERIOD + 5,
                dividend_type="front_ratio",
            )
            df = bar_data.get(qmt_code)
            if df is None or len(df) == 0:
                print(f"[{STRATEGY_ID}]  {qmt_code} ")
                continue
            closes = list(df["close"])
        except Exception as e:
            print(f"[{STRATEGY_ID}]  {qmt_code} : {e}")
            continue

        if len(closes) < SLOW_PERIOD + 1:
            # 
            continue

        #   EMA 
        fast_ema = _calc_ema(closes, FAST_PERIOD)
        slow_ema = _calc_ema(closes, SLOW_PERIOD)

        #  K  EMA 
        f_prev, f_curr = fast_ema[-2], fast_ema[-1]
        s_prev, s_curr = slow_ema[-2], slow_ema[-1]

        if None in (f_prev, f_curr, s_prev, s_curr):
            continue

        current_price = closes[-1]
        prev_cross    = context._prev_cross.get(pure_code)

        #  
        if f_prev <= s_prev and f_curr > s_curr:
            if prev_cross != "golden":
                context._prev_cross[pure_code] = "golden"

                # 
                last_t = _last_signal_time(pure_code, "buy")
                if now_ts - last_t >= SIGNAL_COOLDOWN_SECS:
                    reason = (
                        f"EMA{FAST_PERIOD}({f_curr:.3f}) crossed above"
                        f" EMA{SLOW_PERIOD}({s_curr:.3f}) golden cross"
                    )
                    _write_signal(pure_code, "buy", current_price, reason)
                    print(f"[{STRATEGY_ID}]   {pure_code} @ {current_price:.2f}  {reason}")

        #  
        elif f_prev >= s_prev and f_curr < s_curr:
            if prev_cross != "death":
                context._prev_cross[pure_code] = "death"

                last_t = _last_signal_time(pure_code, "sell")
                if now_ts - last_t >= SIGNAL_COOLDOWN_SECS:
                    reason = (
                        f"EMA{FAST_PERIOD}({f_curr:.3f}) crossed below"
                        f" EMA{SLOW_PERIOD}({s_curr:.3f}) death cross"
                    )
                    _write_signal(pure_code, "sell", current_price, reason)
                    print(f"[{STRATEGY_ID}]   {pure_code} @ {current_price:.2f}  {reason}")


# 
#  
# 

if __name__ == "__main__":
    """
    
      python qmt_dual_ma_5m.py [http]

       SIGNAL_MODE 
     "http"     HTTP  SIGNAL_MODE 
    """
    import sys

    mode_override = sys.argv[1] if len(sys.argv) > 1 else None

    if mode_override == "http":
        print("==   HTTP  ==")
        print(f"Webhook URL   : {QUANT_TRADING_WEBHOOK_URL}")
        print(f"Webhook Secret: {'(set)' if QUANT_TRADING_WEBHOOK_SECRET else '(none)'}")
        payload = _build_signal_payload("600519", "buy", 1888.88, " HTTP")
        print(f"\n payload: {json.dumps(payload, ensure_ascii=False, indent=2)}\n")
        _post_signal_http(payload)
    else:
        print("==   file  ==")
        print(f"signals.json : {SIGNALS_PATH}")
        _write_signal("600519", "buy", 1888.88, "")
        print(" Quant Trading ")
        sigs = _load_signals()
        print(f" signals.json  {len(sigs)} ")
        print(json.dumps(sigs[-1], ensure_ascii=False, indent=2))

    print("\n:  QMT ")
