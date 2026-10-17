/**
 * Generate a standalone Python strategy script for MiniQMT.
 *
 * The generated script:
 * - Connects to MiniQMT xtquant
 * - Waits for call auction / market open
 * - Places all specified limit orders
 * - Monitors fills until market close
 * - Works both as a standalone script and as a QMT strategy (init/handlebar)
 */

export interface ScheduledOrder {
  stockCode: string;
  name: string;
  volume: number;
  price: number;
  orderType?: number; // 23=buy (default)
  priceType?: number; // 11=limit (default)
  remark?: string;
}

export function generateQmtStrategyScript(
  orders: ScheduledOrder[],
  opts: {
    strategyName?: string;
    miniQmtPath?: string;
    accountId?: string;
    accountType?: string;
  } = {},
): { content: string; filename: string } {
  const ts = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 15).replace(/\./g, "_");
  const name = opts.strategyName ?? "quant-trading_scheduled";
  const filename = `${name}_${ts}.py`;

  const orderData = JSON.stringify(
    orders.map((o) => ({
      stock_code: o.stockCode,
      name: o.name,
      volume: o.volume,
      price: o.price,
      order_type: o.orderType ?? 23,
      price_type: o.priceType ?? 11,
      remark: o.remark ?? "",
    })),
    null,
    2,
  );

  const miniPath = (opts.miniQmtPath ?? "d:\\\\st_lc\\\\userdata_mini").replace(/\\/g, "\\\\");
  const accountId = opts.accountId ?? "YOUR_ACCOUNT_ID";
  const accountType = opts.accountType ?? "STOCK";

  const content = `# coding: utf-8
"""
Quant Trading 预埋策略 - 自动生成于 ${ts}
策略名称: ${name}

使用方法:
  方式1 (推荐): 在 MiniQMT 中加载此策略文件并运行
  方式2: python ${filename}

功能:
  - 连接 MiniQMT
  - 在集合竞价/开盘时自动提交限价买单
  - 监控成交状态并输出日志
  - 全部成交或收盘后自动退出

注意: 请确保 MiniQMT (XtMiniQmt.exe) 已启动
"""

import os
import sys
import time
import json

ORDERS = ${orderData}

MINI_QMT_PATH = os.environ.get("MINI_QMT_PATH", r"${miniPath}")
ACCOUNT_ID = os.environ.get("TRADING_ACCOUNT_ID", "${accountId}")
ACCOUNT_TYPE = os.environ.get("TRADING_ACCOUNT_TYPE", "${accountType}")
STRATEGY_NAME = "${name}"


def _log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def _is_trading_time():
    """Check if within A-share trading hours (9:25-11:30, 13:00-15:00)."""
    now = time.localtime()
    h, m = now.tm_hour, now.tm_min
    t = h * 100 + m
    return (925 <= t <= 1130) or (1300 <= t <= 1500)


def _is_pre_open():
    """Check if in pre-open period (9:15-9:25) for call auction."""
    now = time.localtime()
    h, m = now.tm_hour, now.tm_min
    t = h * 100 + m
    return 915 <= t <= 925


def _wait_for_market():
    """Wait until pre-open/trading time."""
    while True:
        if _is_pre_open() or _is_trading_time():
            return
        now = time.localtime()
        t = now.tm_hour * 100 + now.tm_min
        if t >= 1500:
            _log("已收盘 (15:00+)，策略退出")
            sys.exit(0)
        _log("等待开盘… 当前时间 %02d:%02d" % (now.tm_hour, now.tm_min))
        time.sleep(30)


class StrategyCallback:
    def on_disconnected(self):
        _log("WARNING: 与 MiniQMT 断开连接")

    def on_stock_order(self, order):
        status_map = {0: "待报", 1: "待报", 2: "已报", 3: "已报待撤",
                      4: "部成待撤", 5: "部撤", 6: "已撤", 7: "部成",
                      8: "已成", 9: "废单", 10: "已报待改"}
        status_str = status_map.get(order.order_status, str(order.order_status))
        _log("订单状态: %s %s 数量:%d 已成:%d 状态:%s %s" % (
            order.stock_code, "买" if order.order_type == 23 else "卖",
            order.order_volume, order.traded_volume, status_str,
            order.strategy_error_msg or ""))

    def on_stock_trade(self, trade):
        _log("成交: %s 价格:%.3f 数量:%d 金额:%.2f" % (
            trade.stock_code, trade.traded_price,
            trade.traded_volume, trade.traded_price * trade.traded_volume))

    def on_order_error(self, order_error):
        _log("ERROR 订单错误: %s %s" % (
            order_error.stock_code, order_error.error_msg or "unknown"))


def run_strategy():
    try:
        from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
        from xtquant.xttype import StockAccount
    except ImportError:
        _log("ERROR: xtquant 未安装，请确保在 MiniQMT 环境中运行")
        sys.exit(1)

    _log("=" * 50)
    _log("Quant Trading 预埋策略启动")
    _log("待下单: %d 笔" % len(ORDERS))
    for o in ORDERS:
        _log("  %s %s 数量:%d 限价:%.3f" % (
            o["stock_code"], o.get("name", ""), o["volume"], o["price"]))
    _log("=" * 50)

    # Connect to MiniQMT
    _log("连接 MiniQMT: %s" % MINI_QMT_PATH)
    trader = XtQuantTrader(MINI_QMT_PATH, int(time.time()) % 10000)
    trader.register_callback(StrategyCallback())
    trader.start()

    for _ in range(20):
        if trader.connect() == 0:
            break
        time.sleep(0.5)
    else:
        _log("ERROR: 无法连接 MiniQMT，请确认 XtMiniQmt.exe 已启动")
        sys.exit(1)

    account = StockAccount(ACCOUNT_ID, ACCOUNT_TYPE)
    result = trader.subscribe(account)
    if result != 0:
        _log("ERROR: 订阅账户失败, code=%d" % result)
        sys.exit(1)
    _log("已连接, 账户: %s" % ACCOUNT_ID)

    # Wait for market open
    _wait_for_market()

    # Place orders
    submitted = {}
    for o in ORDERS:
        stock_code = o["stock_code"]
        volume = int(o["volume"])
        price = float(o["price"])
        order_type = int(o.get("order_type", 23))
        price_type = int(o.get("price_type", 11))
        remark = o.get("remark", "")

        _log("下单: %s 数量:%d 限价:%.3f" % (stock_code, volume, price))
        try:
            order_id = trader.order_stock(
                account, stock_code, order_type, volume,
                price_type, price, STRATEGY_NAME, remark)
            if order_id < 0:
                _log("ERROR: 下单失败 %s, QMT返回 %d" % (stock_code, order_id))
            else:
                submitted[stock_code] = order_id
                _log("已提交: %s => order_id=%s" % (stock_code, order_id))
        except Exception as e:
            _log("ERROR: 下单异常 %s: %s" % (stock_code, str(e)))
        time.sleep(0.3)

    _log("下单完成: 成功 %d / %d 笔" % (len(submitted), len(ORDERS)))

    # Monitor until market close
    _log("开始监控成交状态…")
    while _is_trading_time() or _is_pre_open():
        time.sleep(10)

    _log("收盘，策略结束")
    trader.stop()


# ── QMT 策略回调入口 (在 QMT 中加载时使用) ──

def init(context):
    """QMT strategy init callback."""
    _log("Quant Trading 预埋策略 init (QMT内部模式)")
    import threading
    t = threading.Thread(target=run_strategy, name="quant-trading-scheduled", daemon=True)
    t.start()

def handlebar(context):
    pass


if __name__ == "__main__":
    run_strategy()
`;

  return { content, filename };
}
