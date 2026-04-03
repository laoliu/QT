# @openclaw/quant-trading

量化交易 OpenClaw 扩展插件 — 股票、期货、基金、期权的推荐、管理、买卖、策略回测。

## Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│  OpenClaw Gateway (Node.js / TypeScript)                          │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │  quant-trading extension                                    │  │
│  │                                                             │  │
│  │  AI Tools:          Commands:        Services:              │  │
│  │  • market_data      /quote           DataSyncService        │  │
│  │  • stock_screen     /screen          SignalMonitorService   │  │
│  │  • backtest         /backtest        PnlMonitorService      │  │
│  │  • portfolio        /portfolio       OptionsMonitorService  │  │
│  │  • trading_signal   /signal                                 │  │
│  │  • risk_analysis                     HTTP:                  │  │
│  │  • options_data     /期权链           /quant/dashboard       │  │
│  │  • options_strategy /期权行情         /quant/api/portfolio   │  │
│  │  • options_trading  /希腊值                                  │  │
│  │                                                             │  │
│  │  Engine Layer:                                              │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │  │
│  │  │ Builtin (TS) │  │  Backtrader  │  │    VNPy      │     │  │
│  │  │  轻量回测    │  │  (Python)    │  │  (Python)    │     │  │
│  │  └──────────────┘  └──────────────┘  └──────────────┘     │  │
│  │                                                             │  │
│  │  ┌──────────────┐  ┌───────────────────────────┐           │  │
│  │  │ Greeks Engine │  │ Options Strategy Engine   │           │  │
│  │  │  BSM + BAW   │  │ (payoff/scenario/compare) │           │  │
│  │  └──────────────┘  └───────────────────────────┘           │  │
│  │                                                             │  │
│  │  Data Layer:                                                │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐                 │  │
│  │  │ AKShare  │  │ Tushare  │  │  Demo    │                 │  │
│  │  │ (Python) │  │ (Python) │  │  (内置)   │                 │  │
│  │  └──────────┘  └──────────┘  └──────────┘                 │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                                                                   │
│  Channels: Discord / Telegram / Web / WeChat / ...               │
└───────────────────────────────────────────────────────────────────┘
```

## Features

### AI Tools (通过对话调用)

| Tool               | Description                                     |
| ------------------ | ----------------------------------------------- |
| `market_data`      | 获取实时行情、历史K线、搜索标的                 |
| `stock_screen`     | 条件筛选（PE、PB、ROE、市值等）                 |
| `backtest`         | 策略回测（内置引擎 + backtrader）               |
| `portfolio`        | 模拟组合管理（买入、卖出、持仓查看）            |
| `trading_signal`   | 查询策略生成的交易信号                          |
| `risk_analysis`    | 风险分析（VaR、回撤、夏普、波动率）             |
| `options_data`     | 期权链、合约行情、IV曲面、希腊值计算            |
| `options_strategy` | 期权策略推荐、损益分析、情景模拟、策略对比      |
| `options_trading`  | 期权下单（买开/卖开/买平/卖平）、持仓、风控摘要 |

### Chat Commands (快捷命令)

```
/quote 600519          — 快速查看茅台行情
/screen pe<20 roe>15   — 简单条件筛选
/backtest dual-ma-crossover 600519 — 快速回测
/portfolio             — 查看持仓
/signal                — 查看最近信号
/期权链 510050          — 查看50ETF期权T型报价
/期权行情 10005646      — 查看期权合约实时行情与Greeks
/希腊值 3.0 call 30    — BSM计算Greeks（行权价 类型 剩余天数）
```

### CLI Commands

```bash
openclaw quant backtest dual-ma-crossover -s 2024-01-01 -e 2025-01-01 --symbols 600519
openclaw quant data fetch --symbol 600519
openclaw quant portfolio show
```

### Built-in Strategies (内置策略)

1. **双均线交叉** (`dual-ma-crossover`) — 快慢均线金叉买入，死叉卖出
2. **动量突破** (`momentum-breakout`) — N日高点突破买入
3. **均值回归** (`mean-reversion`) — 布林带上下轨交易

### Backtrader Integration (Python 引擎)

支持将 backtrader Python 策略直接接入，实现如截图中所述的 **年化476%，回撤6.61%** 等高级策略回测。

### Options (期权交易)

支持中国市场全品种期权：

| 市场类型 | 示例标的                         | 交易所               |
| -------- | -------------------------------- | -------------------- |
| ETF期权  | 50ETF、300ETF、500ETF、创业板ETF | 上交所/深交所        |
| 股指期权 | 沪深300、中证1000、上证50        | 中金所               |
| 商品期权 | 豆粕、白糖、铜、黄金、铁矿等     | 大商所/郑商所/上期所 |

**核心功能：**

- **期权链 T型报价** — 按到期日展示认购/认沽合约，含隐含波动率、Greeks
- **BSM/BAW 定价引擎** — Black-Scholes-Merton (欧式) + Barone-Adesi-Whaley (美式) 精确定价
- **希腊值计算** — Delta、Gamma、Theta、Vega、Rho 完整计算
- **IV曲面 / 波动率微笑** — 可视化隐含波动率期限结构
- **20+ 内置策略模板** — 买入看涨/看跌、牛市/熊市价差、跨式/宽跨式、蝶式、铁鹰等
- **策略推荐** — 根据市场观点和风险偏好智能推荐最优策略
- **损益分析** — 到期损益曲线、盈亏平衡点计算
- **情景模拟** — 标的价格×波动率×时间的三维情景网格
- **策略对比** — 多策略并排对比分析
- **风控引擎** — 裸空头限制、Delta/Gamma/Vega暴露限制、临近到期保护
- **期权监控** — 后台自动监控到期、Greeks突破、实值空头指派风险

**配置期权参数（可选）：**

```json
{
  "plugins": {
    "entries": {
      "quant-trading": {
        "config": {
          "optionsConfig": {
            "riskFreeRate": 0.02,
            "dividendYield": 0,
            "monitor": {
              "expiryWarningDays": 7,
              "maxDelta": 50,
              "maxGamma": 10,
              "maxVega": 20
            }
          },
          "tradingAccount": {
            "riskLimits": {
              "maxNakedShort": 0,
              "maxDeltaExposure": 500,
              "maxGammaExposure": 100,
              "maxVegaExposure": 10000,
              "nearExpiryDays": 3
            }
          }
        }
      }
    }
  }
}
```

## Setup

### 1. Enable the plugin

在 `openclaw.json` (或 temp-config.json) 中添加:

```json
{
  "plugins": {
    "entries": {
      "quant-trading": {
        "enabled": true,
        "config": {
          "defaultMarket": "cn_stock",
          "backtestEngine": "builtin",
          "dataProviders": {
            "akshare": { "enabled": true }
          }
        }
      }
    }
  }
}
```

### 2. Install Python dependencies (for backtrader/akshare)

```bash
pip install -r extensions/quant-trading/python/requirements.txt
```

### 3. (Optional) Configure Tushare

```json
{
  "dataProviders": {
    "tushare": {
      "enabled": true,
      "token": "your-tushare-token"
    }
  }
}
```

## Usage Examples

### Via Chat (Discord/Telegram/Web)

```
User: 帮我查看茅台最近的行情
AI:   [调用 market_data tool] 贵州茅台 (600519) 当前价格 ¥1,850.00 ...

User: 用双均线策略回测沪深300，过去一年
AI:   [调用 backtest tool] 回测结果：年化收益 12.3%，最大回撤 8.5% ...

User: 筛选PE小于20、ROE大于15的A股
AI:   [调用 stock_screen tool] 找到以下符合条件的股票 ...

User: 帮我模拟买入100股茅台
AI:   [调用 portfolio tool] 已买入 600519 100股 @ ¥1,850.00

User: 分析我的组合风险
AI:   [调用 risk_analysis tool] VaR(95%): ¥5,230 ...

User: 查看50ETF期权链
AI:   [调用 options_data tool] 50ETF购/沽 T型报价 ...

User: 帮我推荐一个看涨期权策略，标的50ETF
AI:   [调用 options_strategy tool] 推荐: 牛市看涨价差 ...

User: 买入开仓1张50ETF购3月3100
AI:   [调用 options_trading tool] 已提交买入开仓 10005646 x1 ...

User: 查看期权持仓风险摘要
AI:   [调用 options_trading tool] Delta ±5.5, Gamma 0.12, Vega 0.45 ...
```

### Via CLI

```bash
# Run a backtest
openclaw quant backtest dual-ma-crossover --symbols 600519 -s 2024-01-01 -e 2025-12-31

# Fetch market data
openclaw quant data fetch --symbol 000300

# View portfolio
openclaw quant portfolio show
```

## 跨机器迁移 (Export / Import)

提供一键导出/导入脚本，方便把配置、运行时数据、Python 策略代码迁移到另一台机器。

### 打包内容

| 目录             | 说明                                                                                                |
| ---------------- | --------------------------------------------------------------------------------------------------- |
| `config/`        | `openclaw.json` 插件配置（含 quant-trading 段）                                                     |
| `data/`          | `~/.openclaw/quant-trading/` 运行时数据（signals、stock-list、portfolio、orders …）                 |
| `cache/`         | `~/.openclaw/extensions/quant-trading/cache/` SQLite 缓存数据库（stock_data.db、financial_data.db） |
| `openclaw-home/` | `~/.openclaw/` 核心目录：workspace 人设、identity 设备密钥、cron 定时任务、feishu 状态              |
| `python/`        | Python 策略/选股/引擎/数据源代码                                                                    |
| `meta/`          | 插件元信息 (`openclaw.plugin.json`, `package.json`) + TypeScript 源码                               |

### 导出

```bash
# 默认输出到 ~/quant-trading-bundle-<日期>.tar.gz
./extensions/quant-trading/scripts/export-bundle.sh

# 指定输出路径
./extensions/quant-trading/scripts/export-bundle.sh /path/to/my-bundle.tar.gz
```

导出脚本会自动收集以下内容：

1. `config-custom/openclaw.json` 和 `~/.openclaw/openclaw.json` 配置文件
2. `~/.openclaw/quant-trading/` 下的所有运行时数据文件
3. `~/.openclaw/extensions/quant-trading/cache/` 下的 SQLite 缓存数据库（导出前自动执行 WAL checkpoint 保证数据完整性）
4. `~/.openclaw/workspace/` AI 人设文件（IDENTITY.md、USER.md、SOUL.md、TOOLS.md 等）
5. `~/.openclaw/identity/` 设备身份密钥
6. `~/.openclaw/cron/` 定时任务配置
7. `~/.openclaw/feishu/` 飞书去重状态
8. `extensions/quant-trading/python/` 下全部 Python 源码（排除 `__pycache__`）
9. 插件元信息和 TypeScript 源码

> 如果通过 `dataDir` 配置了自定义数据目录，可以设置环境变量 `QUANT_DATA_DIR` 指定：
>
> ```bash
> QUANT_DATA_DIR=/my/custom/data ./extensions/quant-trading/scripts/export-bundle.sh
> ```

### 导入

把 bundle 文件传到目标机器后：

```bash
# 完整导入（配置 + 数据 + Python + 元信息）
./extensions/quant-trading/scripts/import-bundle.sh quant-trading-bundle-*.tar.gz

# 只恢复配置
./extensions/quant-trading/scripts/import-bundle.sh bundle.tar.gz --config-only

# 只恢复运行时数据
./extensions/quant-trading/scripts/import-bundle.sh bundle.tar.gz --data-only

# 只恢复 Python 策略代码
./extensions/quant-trading/scripts/import-bundle.sh bundle.tar.gz --python-only

# 预览模式（不实际写入）
./extensions/quant-trading/scripts/import-bundle.sh bundle.tar.gz --dry-run

# 跳过确认提示
./extensions/quant-trading/scripts/import-bundle.sh bundle.tar.gz --force

# 不备份现有文件（默认会自动备份为 *.bak-<时间戳>）
./extensions/quant-trading/scripts/import-bundle.sh bundle.tar.gz --no-backup
```

### 导入后步骤

导入完成后，在目标机器上执行：

```bash
# 1. 检查配置（API 密钥、Webhook Secret 等可能需要更新）
cat config-custom/openclaw.json | grep -A5 quant-trading

# 2. 安装 Python 依赖
cd extensions/quant-trading/python && pip install -r requirements.txt

# 3. 安装 Node 依赖 & 构建
pnpm install && pnpm build
```

> **注意**：`qmt_dual_ma_5m.py` 中的 `OPENCLAW_WEBHOOK_URL` 包含源机器 IP，
> 需要修改为目标机器的地址。

## File Structure

```
extensions/quant-trading/
├── index.ts                        # Plugin entry point
├── package.json
├── openclaw.plugin.json            # Plugin metadata & config schema
├── README.md
├── src/
│   ├── types.ts                    # Core type definitions
│   ├── bridge/
│   │   └── python-bridge.ts        # Node→Python subprocess bridge
│   ├── data/
│   │   └── market-data-provider.ts # Data provider abstraction (含期权数据)
│   ├── engine/
│   │   ├── builtin-engine.ts       # Lightweight TS backtest engine
│   │   ├── backtrader-engine.ts    # Backtrader Python bridge
│   │   ├── strategy-registry.ts    # Strategy management
│   │   ├── live-engine.ts          # Live trading engine
│   │   └── strategy-definitions.ts # Built-in strategy definitions (含期权策略)
│   ├── tools/
│   │   ├── market-data.ts          # AI tool: market data
│   │   ├── backtest.ts             # AI tool: backtesting
│   │   ├── portfolio.ts            # AI tool: portfolio mgmt
│   │   ├── stock-screen.ts         # AI tool: stock screening
│   │   ├── trading-signal.ts       # AI tool: signal queries
│   │   ├── risk-analysis.ts        # AI tool: risk metrics
│   │   ├── options-data.ts         # AI tool: 期权数据 (链/行情/IV/Greeks)
│   │   ├── options-strategy.ts     # AI tool: 期权策略 (推荐/损益/情景)
│   │   └── options-trading.ts      # AI tool: 期权交易 (下单/持仓/风控)
│   ├── broker/
│   │   ├── adapter.ts              # Broker adapter interface
│   │   ├── paper-broker.ts         # Paper trading broker (支持期权)
│   │   └── qmt-broker.ts           # QMT real broker (支持期权)
│   ├── commands/
│   │   └── index.ts                # Chat commands (/quote, /期权链, etc.)
│   ├── services/
│   │   ├── data-sync.ts            # Background data sync
│   │   ├── signal-monitor.ts       # Background signal monitoring
│   │   ├── pnl-monitor.ts          # P&L monitoring
│   │   ├── risk-gate.ts            # Pre-trade risk checks (含期权规则)
│   │   ├── options-monitor.ts      # Background 期权风控 (到期/Greeks/指派)
│   │   └── order-manager.ts        # Order lifecycle management
│   ├── cli/
│   │   ├── backtest-cli.ts         # CLI: openclaw quant backtest
│   │   ├── data-cli.ts             # CLI: openclaw quant data
│   │   └── portfolio-cli.ts        # CLI: openclaw quant portfolio
│   └── http/
│       ├── dashboard.ts            # Web dashboard endpoint
│       └── portfolio-api.ts        # REST API for portfolio
├── scripts/
│   ├── export-bundle.sh            # 导出配置+数据+代码为迁移包
│   └── import-bundle.sh            # 从迁移包导入配置+数据+代码
└── python/
    ├── requirements.txt            # Python dependencies
    ├── engines/
    │   ├── backtrader_engine.py    # Backtrader strategy runner
    │   ├── greeks_engine.py        # BSM/BAW 定价 + Greeks 计算
    │   └── option_strategy_engine.py  # 期权策略推荐/损益/情景/对比
    ├── brokers/
    │   └── qmt_broker.py           # QMT broker Python bridge
    └── data_providers/
        ├── akshare_provider.py     # AKShare data provider
        ├── tushare_provider.py     # Tushare data provider
        └── options_provider.py     # 期权数据 provider (AKShare)
```
