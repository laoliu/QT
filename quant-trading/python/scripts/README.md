# 量化交易数据运维指南

## 架构概览

```
Mac (stock_detail.py 等)  → pull →  Windows (qmt_data_bridge.py :18888)
         ↓                                    ↓
   refresh_klines.py                   xtquant (QMT内置)
         ↓
  ~/.openclaw/extensions/quant-trading/cache/stock_data.db
```

Mac 直接向 Windows HTTP 服务拉取数据，无需 Windows 知道 Mac 地址。

---

## 1. Windows 端：启动数据桥

qmt_data_bridge.py 在 Windows 上启动 HTTP 服务，Mac 主动拉取数据和执行交易。

### 方式 A：qmt_data_bridge.py（推荐）

```cmd
:: 设置 QMT 安装路径（自动查找 xtquant）
set QMT_PATH=D:\国金证券QMT交易端

:: 仅数据
python qmt_data_bridge.py

:: 数据 + 交易
python qmt_data_bridge.py --account YOUR_ACCOUNT_ID

:: 自定义端口
python qmt_data_bridge.py --port 18888 --host 0.0.0.0
```

API 端点：

| 方法 | 路径            | 说明                         |
| ---- | --------------- | ---------------------------- |
| GET  | /health         | 健康检查                     |
| GET  | /trading/events | 获取交易事件（成交回调等）   |
| POST | /klines         | 批量 K 线                    |
| POST | /quote          | 实时行情                     |
| POST | /trading        | 交易指令（下单/撤单/查询等） |

### 方式 B：BRIDGE.py（QMT 策略内嵌模式）

仅限在 QMT 策略模式（ContextInfo）中运行，通过 RECEIVER.py 推送数据。
一般情况下不需要使用此模式。

---

## 2. Mac 端：批量刷新 K 线数据

`refresh_klines.py` 检查所有 A 股 K 线数据的新鲜度，拉取过期数据。

**数据源自动选择：**

- 优先使用 Bridge（快速，批量模式，~100只/批）
- Bridge 不可达时自动切换 akshare（较慢，逐只拉取，无需 Windows）

```bash
cd extensions/quant-trading/python

# 全量刷新（~5200只，Bridge约1分钟，akshare约30分钟）
python3 scripts/refresh_klines.py

# 限制数量（测试用）
python3 scripts/refresh_klines.py --max 50

# 后台运行
PYTHONUNBUFFERED=1 python3 scripts/refresh_klines.py 2>&1 | tee /tmp/refresh_klines.log &
```

**查看缓存状态：**

```bash
# 总股票数 / 最新日期 / 最早日期
sqlite3 ~/.openclaw/extensions/quant-trading/cache/stock_data.db \
  "SELECT COUNT(DISTINCT symbol), MAX(trade_date), MIN(trade_date) FROM kline_data WHERE market='cn_stock';"

# 最近2天内有数据的股票数
sqlite3 ~/.openclaw/extensions/quant-trading/cache/stock_data.db \
  "SELECT COUNT(DISTINCT symbol) FROM kline_data WHERE market='cn_stock' AND trade_date >= date('now', '-2 days');"
```

---

## 3. 环境变量

| 变量                 | 默认值                   | 说明                           |
| -------------------- | ------------------------ | ------------------------------ |
| `WINDOWS_XTDATA_URL` | `http://127.0.0.1:18888` | Windows 桥地址（Mac 端配置）   |
| `QMT_PATH`           | 自动搜索                 | QMT 安装路径（Windows 端配置） |
| `MINI_QMT_PATH`      | 自动搜索                 | miniQMT userdata 路径          |
| `TRADING_ACCOUNT_ID` | 无                       | 交易账户 ID                    |

---

## 4. 常用排查

```bash
# 检查 18888 端口占用
lsof -i :18888

# 检查 RECEIVER 进程
ps aux | grep RECEIVER | grep -v grep

# 检查 Bridge 连通性
curl -s --connect-timeout 3 http://127.0.0.1:18888/health

# 查看 K 线缓存 DB 大小
du -h ~/.openclaw/extensions/quant-trading/cache/stock_data.db
```
