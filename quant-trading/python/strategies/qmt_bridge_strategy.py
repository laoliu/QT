# coding: utf-8
"""
qmt_bridge_strategy.py -- QMT 策略包装器，加载后自动启动数据桥 HTTP 服务
======================================================================

用法：
  1. 在 miniQMT 中新建一个策略，把本文件加载进去
  2. 运行策略后，自动在后台线程启动 HTTP 服务（端口 18888）
  3. Mac 可通过 POST http://<windows-ip>:18888/klines 拉取 K 线数据

原理：
  miniQMT 会用内置 Python 3.6 执行本策略。
  init() 回调里启动 qmt_data_bridge 的 HTTP 服务后台线程。
  handlebar() 里不做任何交易操作，只做心跳日志。

  这样就绕过了"没有独立 python.exe"的问题 -- QMT 内部 Python 3.6
  可以 import xtquant，HTTP 服务在后台线程运行。

注意：
  - 确保 qmt_data_bridge.py 在同目录下（或 QMT Python 可搜索到的路径）
  - 本策略不做交易，纯粹为了启动数据桥
  - 可以和 qmt_dual_ma_5m.py 同时运行（各自独立策略）
"""

import os
import sys
import time

# 确保当前目录在 sys.path 中，以便 import qmt_data_bridge
_this_dir = os.path.dirname(os.path.abspath(__file__))
if _this_dir not in sys.path:
    sys.path.insert(0, _this_dir)

# 数据桥 HTTP 服务端口（与 qmt_data_bridge.py 中一致）
BRIDGE_PORT = int(os.environ.get("SERVE_PORT", "18888"))
BRIDGE_HOST = "0.0.0.0"

# 全局标记，防止重复启动
_bridge_started = False


def init(context):
    """
    QMT 策略初始化回调。
    启动数据桥 HTTP 服务后台线程。
    """
    global _bridge_started

    if _bridge_started:
        print("[bridge-strategy] 数据桥已在运行中，跳过重复启动")
        return

    print("[bridge-strategy] ========================================")
    print("[bridge-strategy] 数据桥策略启动")
    print("[bridge-strategy] HTTP 服务端口: %d" % BRIDGE_PORT)
    print("[bridge-strategy] ========================================")

    try:
        import qmt_data_bridge
    except ImportError as e:
        print("[bridge-strategy] 错误: 无法导入 qmt_data_bridge: %s" % e)
        print("[bridge-strategy] 请确保 qmt_data_bridge.py 在同一目录下")
        return

    # 设置端口
    qmt_data_bridge.SERVE_PORT = BRIDGE_PORT
    qmt_data_bridge.SERVE_HOST = BRIDGE_HOST

    # 尝试自动发现 QMT 路径并初始化 xtquant
    qmt_data_bridge._xtquant_userdata = qmt_data_bridge._setup_xtquant_path()
    if qmt_data_bridge._xtquant_userdata:
        print("[bridge-strategy] QMT userdata: %s" % qmt_data_bridge._xtquant_userdata)

    # 在后台线程启动 HTTP 服务
    import threading
    t = threading.Thread(
        target=qmt_data_bridge.run_serve_mode,
        kwargs={"host": BRIDGE_HOST, "port": BRIDGE_PORT},
        name="qmt-data-bridge-http",
    )
    t.daemon = True
    t.start()

    _bridge_started = True
    print("[bridge-strategy] HTTP 服务线程已启动 (daemon)")
    print("[bridge-strategy] Mac 请访问: http://<windows-ip>:%d/health" % BRIDGE_PORT)


def handlebar(context):
    """
    每根 K 线回调。数据桥策略不做交易，仅做心跳。
    """
    pass
