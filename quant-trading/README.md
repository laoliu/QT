# @openclaw/quant-trading

量化交易 OpenClaw 扩展插件 — 股票、期货、基金、期权的推荐、管理、买卖、策略回测。

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  OpenClaw Gateway (Node.js / TypeScript)                                │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  quant-trading extension (46 AI Tools · 60+ Services · 52 策略)   │  │
│  │                                                                   │  │
│  │  AI Tools:               Commands:         Services:              │  │
│  │  • market_data           /quote             DataSyncService       │  │
│  │  • stock_screen          /screen            SignalMonitor         │  │
│  │  • backtest              /backtest          PnlMonitor            │  │
│  │  • portfolio             /portfolio         OptionsMonitor        │  │
│  │  • trading_signal        /signal            LiveEngine            │  │
│  │  • risk_analysis         /期权链             TickIngressService    │  │
│  │  • fund_flow             /期权行情           TickStrategyService   │  │
│  │  • sector_analysis       /希腊值             PriceAlertService     │  │
│  │  • dragon_tiger                             TaskScheduler         │  │
│  │  • lianban_analysis      CLI:               ReceiverService       │  │
│  │  • options_data          quant backtest      BreakoutMonitor      │  │
│  │  • options_strategy      quant data          + 45 more...         │  │
│  │  • options_trading       quant portfolio                          │  │
│  │  • live_trading          quant live         HTTP:                  │  │
│  │  • strategy_router                          /quant/dashboard      │  │
│  │  • chip_accumulation     Broker:            /quant/api/portfolio  │  │
│  │  • institutional_flow    QMT (real)         /quant/admin/*        │  │
│  │  • + 29 more...          Paper (sim)        /quant/data-bridge    │  │
│  │                                                                   │  │
│  │  Engine Layer:                                                    │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐            │  │
│  │  │ Builtin (TS) │  │  Backtrader  │  │ Live Engine  │            │  │
│  │  │ 52 内置策略   │  │  (Python)    │  │ (实盘交易)    │            │  │
│  │  └──────────────┘  └──────────────┘  └──────────────┘            │  │
│  │                                                                   │  │
│  │  ┌──────────────┐  ┌───────────────────────────┐                 │  │
│  │  │ Greeks Engine │  │ Options Strategy Engine   │                 │  │
│  │  │  BSM + BAW   │  │ (payoff/scenario/compare) │                 │  │
│  │  └──────────────┘  └───────────────────────────┘                 │  │
│  │                                                                   │  │
│  │  Data Layer:                                                      │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐            │  │
│  │  │ AKShare  │ │ Tushare  │ │ YFinance │ │  Demo    │            │  │
│  │  │ (Python) │ │ (Python) │ │ (Python) │ │  (内置)   │            │  │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘            │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  Channels: Discord / Telegram / Web / WeChat / ...                     │
└─────────────────────────────────────────────────────────────────────────┘
```

## Features

### AI Tools (通过对话调用，共 46 个)

#### 核心分析

| Tool                     | Description                                     |
| ------------------------ | ----------------------------------------------- |
| `market_data`            | 获取实时行情、历史K线、搜索标的                 |
| `stock_screen`           | 条件筛选（PE、PB、ROE、市值等）                 |
| `stock_recommend`        | 智能选股推荐                                    |
| `backtest`               | 策略回测（内置引擎 + backtrader）               |
| `portfolio`              | 模拟组合管理（买入、卖出、持仓查看）            |
| `portfolio_backtest`     | 组合级回测                                      |
| `portfolio_rebalance`    | 组合再平衡                                      |
| `trading_signal`         | 查询策略生成的交易信号                          |
| `risk_analysis`          | 风险分析（VaR、回撤、夏普、波动率）             |
| `data_analysis`          | 通用数据分析                                    |
| `data_cache`             | 数据缓存管理                                    |

#### 策略管理

| Tool                     | Description                                     |
| ------------------------ | ----------------------------------------------- |
| `strategy_router`        | 智能策略路由与推荐                              |
| `strategy_composer`      | 策略组合与编排                                  |
| `strategy_optimizer`     | 策略参数优化                                    |
| `strategy_explorer`      | 策略探索与发现                                  |
| `strategy_health`        | 策略运行健康度监控                              |
| `custom_strategy`        | 自定义策略创建                                  |

#### 交易执行

| Tool                     | Description                                     |
| ------------------------ | ----------------------------------------------- |
| `live_trading`           | 实盘交易（QMT/Paper broker）                    |
| `conditional_order`      | 条件单（触价/追踪止损）                         |
| `dca_schedule`           | 定投计划管理                                    |
| `breakout_monitor`       | 突破形态监控                                    |
| `position_sizing`        | 仓位管理与计算                                  |
| `price_alert`            | 价格预警                                        |
| `scheduled_task`         | 定时任务管理                                    |

#### 期权交易

| Tool                     | Description                                     |
| ------------------------ | ----------------------------------------------- |
| `options_data`           | 期权链、合约行情、IV曲面、希腊值计算            |
| `options_strategy`       | 期权策略推荐、损益分析、情景模拟、策略对比      |
| `options_trading`        | 期权下单（买开/卖开/买平/卖平）、持仓、风控摘要 |
| `stochastic_pricing`     | 随机定价与蒙特卡洛风险模拟                      |
| `futures_straddle`       | 期货套利策略                                    |

#### 市场研判

| Tool                     | Description                                     |
| ------------------------ | ----------------------------------------------- |
| `sector_analysis`        | 板块分析与热点追踪                              |
| `fund_flow`              | 资金流向分析                                    |
| `dragon_tiger`           | 龙虎榜数据分析                                  |
| `lianban_analysis`       | 连板股分析                                      |
| `chip_accumulation`      | 主力吸筹检测                                    |
| `institutional_flow`     | 机构持仓变动                                    |
| `correlation_analysis`   | 标的相关性分析                                  |
| `macro_data`             | 宏观经济数据                                    |
| `earnings_calendar`      | 财报日历                                        |
| `social_sentiment`       | 舆情分析                                        |
| `news_summary`           | 新闻摘要                                        |
| `polymarket_odds`        | 预测市场赔率                                    |

#### 投资分析

| Tool                     | Description                                     |
| ------------------------ | ----------------------------------------------- |
| `value_investing`        | 价值投资分析                                    |
| `tech_moat`              | 技术壁垒评估                                    |
| `cfo_assistant`          | CFO财务分析助手                                 |
| `financial_calculator`   | 估值计算器                                      |
| `performance_attribution`| 绩效归因分析                                    |

#### 个人管理

| Tool                     | Description                                     |
| ------------------------ | ----------------------------------------------- |
| `watchlist`              | 自选股管理                                      |
| `favorites`              | 股票收藏                                        |
| `trade_journal`          | 交易笔记                                        |

#### 交易辅助

| Tool                     | Description                                     |
| ------------------------ | ----------------------------------------------- |
| `stock_compare`          | 个股多维度对比（技术面+基本面+涨跌幅对比）      |
| `dividend_tracker`       | 分红/股息追踪（历史分红、除权日、股息率排行）    |
| `trade_cost_calculator`  | A股交易成本计算（佣金、印花税、过户费、保本价）  |
| `market_calendar`        | 交易日历（休市、除权日、IPO、解禁、周概览）      |
| `position_diagnosis`     | 持仓诊断/健康检查（评级+信号+操作建议）          |

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
openclaw quant live status
```

### Built-in Strategies (52 个内置策略)

#### 基础策略（9 个）

| 策略 ID               | 名称         | 核心指标               |
| --------------------- | ------------ | ---------------------- |
| `dual-ma-crossover`   | 双均线交叉   | SMA 金叉/死叉          |
| `momentum-breakout`   | 动量突破     | N日最高价突破          |
| `mean-reversion`      | 均值回归     | 布林带上下轨           |
| `rsi-filter`          | RSI 过滤     | RSI 超买超卖           |
| `rsi-enhanced`        | RSI 增强     | RSI + SMA 确认         |
| `macd-confirmation`   | MACD 确认    | MACD 交叉              |
| `macd-divergence`     | MACD 背离    | MACD 顶底背离          |
| `volume-confirmation` | 量价确认     | OBV + SMA              |
| `t0-day-trading`      | T+0 日内     | 多指标日内交易         |

#### 高阶组合策略（8 个）

Composite、Gated、Scored、TrendCombo、EnhancedComposite、RsiMacd、T0Enhanced、MarketRegime

#### CTA 策略（35 个）

- **趋势类** (8)：TurtleBreakout、DualThrust、EMA带状跟踪、MacdTrend、Keltner通道 ...
- **通道类** (5)：RBreaker、Aberration、TtmSqueeze、DmiAdx、ParabolicSar
- **指标类** (12)：VolatilityTarget、GridTrading、Ichimoku、KDJ、MeanReversionZScore ...
- **形态类** (10)：RangeBreakout、DoubleBottom、StochasticRsi、BollingerBandwidth ...

> 详细策略清单见 [docs/INDICATORS_MATRIX.md](docs/INDICATORS_MATRIX.md)

### Backtrader Integration (Python 引擎)

支持将 backtrader Python 策略直接接入。另有 24 个 Python 实盘策略（含连板、网格、配对交易、QMT 数据桥等）。

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
pip install -r python/requirements.txt
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
quant-trading/
├── index.ts                        # Plugin entry point (46 tools registered)
├── package.json
├── openclaw.plugin.json            # Plugin metadata & config schema
├── README.md
├── docs/
│   ├── ARCHITECTURE_ANALYSIS.md    # 深度架构分析
│   ├── ARCHITECTURE_DIAGRAMS.md    # 11 张 Mermaid 架构图
│   ├── INDICATORS_MATRIX.md        # 指标应用矩阵 & 52 策略完整清单
│   └── FAVORITES_UI_GUIDE.md       # 收藏功能 UI 指南
├── src/
│   ├── types.ts                    # Core type definitions
│   ├── config-validator.ts         # Plugin config validation
│   ├── bridge/
│   │   ├── python-bridge.ts        # Node→Python subprocess bridge
│   │   ├── persistent-python.ts    # Persistent Python process
│   │   └── windows-bridge-client.ts # Windows QMT bridge
│   ├── data/
│   │   └── market-data-provider.ts # Data provider abstraction
│   ├── engine/
│   │   ├── builtin-engine.ts       # Lightweight TS backtest engine
│   │   ├── backtrader-engine.ts    # Backtrader Python bridge
│   │   ├── live-engine.ts          # Live trading engine
│   │   ├── strategy-registry.ts    # Strategy management
│   │   ├── strategy-definitions.ts # 52 built-in strategy definitions
│   │   ├── dynamic-composer.ts     # Dynamic strategy composition
│   │   ├── rule-strategy.ts        # Rule-based strategy builder
│   │   ├── tick-aggregator.ts      # Tick → Bar aggregation
│   │   ├── tick-feature-store.ts   # Tick feature extraction
│   │   └── strategies/             # Indicator & strategy implementations
│   │       ├── indicators.ts       # 15 technical indicators
│   │       ├── basic.ts            # 9 basic strategies
│   │       ├── advanced.ts         # 8 advanced strategies
│   │       ├── cta-trend.ts        # 8 CTA trend strategies
│   │       ├── cta-channel.ts      # 5 CTA channel strategies
│   │       ├── cta-indicator.ts    # 12 CTA indicator strategies
│   │       └── cta-pattern.ts      # 10 CTA pattern strategies
│   ├── tools/                      # 46 AI-callable tool implementations
│   │   ├── market-data.ts          # 行情数据
│   │   ├── stock-screen.ts         # 选股筛选
│   │   ├── backtest.ts             # 策略回测
│   │   ├── portfolio.ts            # 投资组合
│   │   ├── live-trading.ts         # 实盘交易
│   │   ├── options-data.ts         # 期权数据
│   │   ├── fund-flow.ts            # 资金流向
│   │   ├── sector-analysis.ts      # 板块分析
│   │   ├── dragon-tiger.ts         # 龙虎榜
│   │   ├── strategy-router.ts      # 策略路由
│   │   └── ... (36 more tools)
│   ├── broker/
│   │   ├── adapter.ts              # Broker adapter interface
│   │   ├── paper-broker.ts         # Paper trading broker
│   │   ├── qmt-broker.ts           # QMT real broker
│   │   └── remote-qmt-broker.ts    # Remote QMT via HTTP
│   ├── commands/
│   │   ├── commands.ts             # Chat commands (/quote, /期权链, etc.)
│   │   └── categories.ts           # Command categories
│   ├── services/                   # 60+ background services
│   │   ├── data-sync.ts            # 数据同步
│   │   ├── signal-monitor.ts       # 信号监控
│   │   ├── pnl-monitor.ts          # 盈亏监控
│   │   ├── options-monitor.ts      # 期权风控
│   │   ├── order-manager.ts        # 订单管理
│   │   ├── risk-gate.ts            # 交易前风控
│   │   ├── tick-ingress-service.ts  # Tick 数据接入
│   │   ├── tick-strategy-service.ts # Tick 策略执行
│   │   ├── task-scheduler.ts       # 定时任务调度
│   │   ├── event-bus.ts            # 事件总线
│   │   ├── data-warehouse.ts       # 数据仓库
│   │   ├── sqlite-storage.ts       # SQLite 持久化
│   │   └── ... (50+ more services)
│   ├── cli/
│   │   ├── backtest-cli.ts         # openclaw quant backtest
│   │   ├── data-cli.ts             # openclaw quant data
│   │   ├── portfolio-cli.ts        # openclaw quant portfolio
│   │   └── live-cli.ts             # openclaw quant live
│   ├── http/
│   │   ├── routes.ts               # Route registration
│   │   ├── admin-routes.ts         # Admin API endpoints
│   │   ├── dashboard.ts            # Web dashboard
│   │   ├── portfolio-api.ts        # Portfolio REST API
│   │   ├── data-bridge-api.ts      # Data bridge API
│   │   ├── trading-bridge-api.ts   # Trading bridge API
│   │   └── auth-middleware.ts      # Authentication
│   ├── e2e/                        # End-to-end tests
│   └── utils/
│       ├── async-mutex.ts          # Async mutex
│       └── number-utils.ts         # Number utilities
├── scripts/
│   ├── export-bundle.sh            # 导出配置+数据+代码为迁移包
│   └── import-bundle.sh            # 从迁移包导入配置+数据+代码
└── python/
    ├── requirements.txt            # Python dependencies
    ├── engines/                    # 48 Python 分析引擎
    │   ├── backtrader_engine.py    # Backtrader strategy runner
    │   ├── greeks_engine.py        # BSM/BAW 定价 + Greeks 计算
    │   ├── option_strategy_engine.py # 期权策略推荐/损益/情景/对比
    │   ├── screener_engine.py      # 选股引擎
    │   ├── fund_flow.py            # 资金流向分析
    │   ├── dragon_tiger.py         # 龙虎榜分析
    │   ├── sector_analysis.py      # 板块分析
    │   ├── live_engine.py          # 实盘策略引擎
    │   ├── monte_carlo_engine.py   # 蒙特卡洛模拟
    │   └── ... (39 more engines)
    ├── strategies/                 # 24 Python 实盘策略
    │   ├── BRIDGE.py               # QMT 策略桥接
    │   ├── RECEIVER.py             # 信号接收器
    │   ├── turtle_strategy.py      # 海龟策略
    │   ├── grid_strategy.py        # 网格策略
    │   ├── pair_trading_strategy.py # 配对交易
    │   └── ... (19 more strategies)
    ├── brokers/
    │   └── qmt_broker.py           # QMT broker bridge
    ├── data_providers/
    │   ├── akshare_provider.py     # AKShare (A股/期货/基金)
    │   ├── tushare_provider.py     # Tushare (A股)
    │   ├── yfinance_provider.py    # Yahoo Finance (美股/港股)
    │   ├── options_provider.py     # 期权数据
    │   ├── financial_provider.py   # 财务报表数据
    │   ├── sector_data.py          # 板块分类数据
    │   └── provider_factory.py     # 数据源工厂
    └── screeners/                  # 选股脚本
```

## Documentation

| 文档 | 说明 |
| ---- | ---- |
| [docs/ARCHITECTURE_ANALYSIS.md](docs/ARCHITECTURE_ANALYSIS.md) | 深度架构分析：数据源、15 个指标、策略处理、扩展指南 |
| [docs/ARCHITECTURE_DIAGRAMS.md](docs/ARCHITECTURE_DIAGRAMS.md) | 11 张 Mermaid 架构图：插件机制、时序、状态机、排障 |
| [docs/INDICATORS_MATRIX.md](docs/INDICATORS_MATRIX.md) | 指标应用矩阵、52 个策略完整清单、参数速查 |
| [docs/FAVORITES_UI_GUIDE.md](docs/FAVORITES_UI_GUIDE.md) | 收藏功能 UI 指南 |
