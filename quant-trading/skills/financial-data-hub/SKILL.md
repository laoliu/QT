---
name: financial-data-hub
description: "Comprehensive financial data decision guide for the quant-trading extension. Helps the agent decide which data tool to use based on the user's question: 12 core data tools (market_data, macro_data, earnings_calendar, etc.) plus 30+ specialized analysis tools (stock_screen, stock_recommend, chip_accumulation, backtest, strategy_composer, strategy_explorer, strategy_router, strategy_optimizer, strategy_health, lianban_analysis, live_trading, portfolio, trading_signal, conditional_order, trade_journal, watchlist, favorites, price_alert, options_data, options_strategy, options_trading, risk_analysis, stochastic_pricing, performance_attribution, value_investing, financial_calculator, fund_flow, sector_synergy, dragon_tiger, sector_analysis, news_summary, polymarket_odds, cfo_assistant, data_analysis, data_cache, scheduled_task, macro_data, earnings_calendar, institutional_flow, social_sentiment, correlation_analysis, breakout_monitor, position_sizing, dca_schedule, portfolio_rebalance) for backtesting, strategy optimization, options trading, real-time trading, position management, and risk management."
metadata: { "quant-trading": { "emoji": "📊", "requires": { "plugins": ["quant-trading"] } } }
---

# Financial Data Hub — Agent Decision Guide

Use this guide to pick the right data tool for each user question.

## Tool Selection Decision Tree

### 🔍 Core Market Data

```
User wants...                   → Use this tool
─────────────────────────────────────────────────────────
Real-time price / chart         → market_data (action=quote/klines)
Historical K-line data          → market_data (action=klines)
Technical indicators (MA/MACD)  → market_data (action=indicators)
Fundamentals (PE/PB/ROE)        → market_data (action=fundamentals)
Dividend & cash flow data       → market_data (action=cash_flow)
```

### 📊 Macro & Economic

```
GDP / CPI / PMI / rates         → macro_data (action=history)
Macro cycle / regime            → macro_data (action=regime)
Economic calendar               → macro_data (action=calendar)
Interest rates & yields         → macro_data (action=rates)
Inflation & growth trends       → macro_data (action=trends)
```

### 📈 Earnings & Analyst

```
Upcoming earnings dates         → earnings_calendar (action=upcoming)
Analyst consensus EPS           → earnings_calendar (action=stock_forecast)
Beat/miss track record          → earnings_calendar (action=surprise_history)
Analyst ratings (buy/hold/sell) → earnings_calendar (action=analyst_ratings)
Analyst price targets           → earnings_calendar (action=price_targets)
```

### 🏛️ Institutional & Smart Money

```
Fund/institutional holders      → institutional_flow (action=fund_holdings)
Q/Q holdings change             → institutional_flow (action=holding_changes)
Block trades (大宗交易)         → institutional_flow (action=block_trades)
Insider buy/sell                → institutional_flow (action=insider_trades)
Smart money conviction signal   → institutional_flow (action=smart_money_signal)
Institutional accumulation      → chip_accumulation
```

### 👥 Sentiment & Social

```
Hot/trending stocks             → social_sentiment (action=hot_stocks)
Stock community sentiment       → social_sentiment (action=stock_sentiment)
Sentiment trend history         → social_sentiment (action=sentiment_history)
Retail attention spikes         → social_sentiment (action=retail_flow)
```

### 💰 Capital Flow & Resources

```
Capital flow ranking            → fund_flow (action=rank)
Individual stock capital flow   → fund_flow (action=stock)
Northbound flow (港通)          → fund_flow (action=northbound)
Dragon tiger board              → dragon_tiger
Sectoral capital rotation       → sector_synergy
```

### 📰 News & Information

```
Financial news headlines        → news_summary (action=market_news)
Stock-specific news             → news_summary (action=stock_news)
News sentiment scoring          → news_summary (action=sentiment)
Prediction market odds          → polymarket_odds
```

### 🔎 Stock Selection & Screening

```
Multi-factor stock screening    → stock_screen
AI-driven stock recommendations → stock_recommend (mode=classic: 回测验证, mode=predictive: 前瞻预测)
Predictive stock picks          → stock_recommend (mode=predictive, horizon=short/mid/long)
Correlation analysis (600000 vs SPX) → correlation_analysis
Sector rotation analysis        → sector_analysis
Chip accumulation tracking      → chip_accumulation
Lianban (连板) trend chase      → lianban_analysis
Technology moat analysis        → tech_moat
```

### 📉 Backtesting & Strategy Validation

```
Backtest a strategy (MA 20/50)  → backtest
Strategy performance vs. benchmark → backtest (with bench=CSI300)
Compose multiple sub-strategies → strategy_composer
Explore strategy parameter space → strategy_explorer
Route to best-performing strategy → strategy_router
Optimize strategy parameters    → strategy_optimizer
Monitor strategy health/drawdown → strategy_health
Futures spread & arbitrage      → futures_straddle
```

### 💼 Live Trading & Execution

```
View current portfolio positions → portfolio
Send buy/sell orders             → live_trading
Generate trading signals         → trading_signal
Set conditional orders (止损/止盈) → conditional_order
Record trade journal entries     → trade_journal
Manage watchlists                → watchlist
Price alerts (condition-based)   → price_alert
```

### 🎯 Position & Breakout Automation

```
Compound breakout entry (price+volume+hold) → breakout_monitor (action=add_setup)
EOD MA auto-exit rule              → breakout_monitor (action=add_eod_rule)
Compute position size (risk-based) → position_sizing (action=compute)
Set up DCA recurring buy plan      → dca_schedule (action=create)
Rebalance portfolio to targets     → portfolio_rebalance (action=plan/execute)
```

### 📋 Options Analysis

```
Option chain data               → options_data
Implied volatility / Greeks     → options_data (action=greeks)
Strategy recommendations        → options_strategy
Execute options orders          → options_trading
Stochastic pricing / MC sim     → stochastic_pricing
```

### 📊 Risk & Performance

```
VaR / max drawdown / Sharpe     → risk_analysis
Performance attribution         → performance_attribution
Valuation multiples (Graham/PE) → value_investing
Valuation calculator (DCF/PB)   → financial_calculator
Monte Carlo pricing             → stochastic_pricing
```

### 🎯 Tools & Utilities

```
Ad-hoc data analysis (correlation, regression) → data_analysis
Cache management / data refresh → data_cache
Schedule delayed tasks / retries → scheduled_task
CFO-level statement analysis    → cfo_assistant
```

### 🔧 Trading Assistance

```
Compare 2-5 stocks side-by-side     → stock_compare (action=compare/valuation/technical/performance)
Dividend history & yield ranking     → dividend_tracker (action=history/upcoming/yield_rank/analysis)
Transaction cost & breakeven price   → trade_cost_calculator (action=cost/round_trip/breakeven/compare)
Trading calendar & market events     → market_calendar (action=trading_days/holidays/events/weekly)
Position health check & diagnosis    → position_diagnosis (action=diagnose/single/risk_check/action_plan)
```

## Cross-Tool Workflows

### 📋 Before Making a Trade Recommendation

1. **macro_data** (action=regime) — Check macro cycle (recovery/overheating/stagflation/recession)
2. **earnings_calendar** (action=upcoming) — Flag any imminent earnings for the stock
3. **institutional_flow** (action=smart_money_signal) — Check institutional conviction
4. **social_sentiment** (action=stock_sentiment) — Check if retail is overheated
5. **market_data** (action=quote + klines with indicators) — Technical picture
6. **price_alert** or **conditional_order** — Set stop-loss and take-profit levels

### 🔬 For a Comprehensive Stock Analysis

1. **market_data** (action=fundamentals) — PE/PB/ROE/cash flow
2. **earnings_calendar** (action=stock_forecast + analyst_ratings) — Analyst consensus & distribution
3. **institutional_flow** (action=holding_changes) — Q/Q institutional trend
4. **social_sentiment** (action=stock_sentiment) — Community heat & sentiment
5. **news_summary** (action=stock_news) — Recent headlines & events
6. **risk_analysis** — Volatility & drawdown profile
7. **performance_attribution** — Historical performance drivers

### 📈 Weekly Market Review

1. **macro_data** (action=regime) — Current macro backdrop
2. **fund_flow** (action=northbound) — Northbound capital trend
3. **social_sentiment** (action=hot_stocks) — What retail is watching today
4. **news_summary** (action=market_news) — Key market headlines
5. **sector_analysis** — Sector rotation perspective
6. **sector_synergy** — Hot sector chain analysis

### 🎯 Strategy Backtesting & Optimization Workflow

1. **backtest** — Test initial strategy on historical data
2. **strategy_optimizer** — Find optimal parameters (MA period, RSI threshold, etc.)
3. **strategy_health** — Check Sharpe, max drawdown, win rate
4. **strategy_composer** — Combine multiple sub-strategies
5. **strategy_explorer** — Visualize parameter sensitivity
6. **position_sizing** — Calculate position size (risk_pct=1%, kelly, ATR volatility)
7. **live_trading** — Deploy to live market with position sizing

### 🚀 New Strategy Research Workflow

1. **stock_screen** — Identify candidate stocks matching your criteria
2. **correlation_analysis** — Ensure stocks are not highly correlated
3. **backtest** — Validate strategy on screened universe
4. **strategy_router** — Route to best-performing variant
5. **watchlist** — Add promising candidates to watchlist
6. **price_alert** — Monitor for entry/exit conditions

### 💼 Active Portfolio Management

1. **portfolio** — View all current positions, P&L, allocation
2. **trading_signal** — Get AI-generated trading signals
3. **portfolio_rebalance** (action=plan) — Compute rebalancing delta vs target weights
4. **portfolio_rebalance** (action=execute) — Execute rebalancing (sells first, then buys)
5. **dca_schedule** — Set up recurring buy plans (daily/weekly/monthly)
6. **trade_journal** — Log all trades for post-analysis
7. **performance_attribution** — Understand what drove returns
8. **scheduled_task** — Set up automated position adjustments

### 📊 Options Trading Workflow

1. **options_data** (action=chain + greeks) — Browse option chain, check IV & Greeks
2. **options_strategy** — Get strategy recommendations (call spread, strangle, etc.)
3. **stochastic_pricing** — Model payoff under different scenarios
4. **risk_analysis** — Check max loss, breakeven points
5. **options_trading** — Execute the strategy
6. **conditional_order** — Set automated exit points

### 🎪 Hot Stock & Trend-Chasing Workflow

1. **social_sentiment** (action=hot_stocks) — See today's trending stocks
2. **lianban_analysis** — Track consecutive limit-ups (连板)
3. **dragon_tiger** — Check if on dragon tiger board
4. **institutional_flow** (action=smart_money_signal) — Are institutions buying?
5. **technical indicators** via **market_data** — Check entry/exit signals
6. **breakout_monitor** — Set compound breakout entry (price + volume + hold confirmation + time window)
7. **price_alert** — Set alerts for volume spikes or breakouts

### 🔄 Automated Breakout Entry Workflow

1. **market_data** (action=klines) — Identify recent high / support level
2. **breakout_monitor** (action=add_setup) — Set trigger price + volume ratio (1.5×) + 5-min hold + time windows
3. **position_sizing** (action=compute, method=risk_pct) — Calculate qty for 1% risk
4. **conditional_order** (add_trailing_atr) — Set ATR trailing stop after fill
5. **conditional_order** (add_staged_tp) — Set staged take-profit (+8% / +12%)
6. **breakout_monitor** (action=add_eod_rule) — Auto-exit if close below 10-day MA

### 🔮 Predictive Stock Recommendation Workflow

1. **stock_recommend** (mode=predictive, horizon=all) — 前瞻选股：量价低位筛选 → 主力吸筹分析 → 机构行为 → 综合评分
2. Scoring breakdown: 主力吸筹(30%) + 量价信号(20%) + 机构动向(20%) + 底背离(15%) + 底部反转(15%)
3. Automatic horizon classification: short(底部放量反转), mid(主力建仓期), long(价值成长布局)
4. **chip_accumulation** (action=scan) — Deep dive on top picks: verify accumulation pattern
5. **institutional_flow** (action=smart_money_signal) — Verify institutional positioning
6. **social_sentiment** (action=stock_sentiment) — Check heat level (avoid overheated)
7. **position_sizing** (action=compute) — Size position for recommended horizon

### 🏛️ Value Investing Research

1. **market_data** (action=fundamentals) — Get current PE, PB, ROE ratios
2. **value_investing** — Calculate intrinsic value using Graham's method
3. **financial_calculator** — DCF valuation & sensitivity analysis
4. **earnings_calendar** (action=forecast) — Project future earnings growth
5. **institutional_flow** (action=fund_holdings) — Check if smart money is accumulating
6. **backtest** — Test value investing factor on historical data

### 🌍 Macro-Driven Strategy Workflow

1. **macro_data** (action=calendar) — Scan upcoming economic data releases
2. **macro_data** (action=regime) — Identify current macro regime
3. **sector_analysis** — Which sectors benefit from current regime?
4. **stock_screen** — Find stocks in winning sectors
5. **trend_analysis** — Check technical trends align with macro
6. **backtest** — Validate macro-driven strategy
7. **scheduled_task** — Rebalance when macro regime changes

### 📊 Stock Comparison & Selection Workflow

1. **stock_screen** — Screen candidates by fundamental/technical factors
2. **stock_compare** (action=compare) — Compare top candidates side-by-side (PE/PB/ROE/margins)
3. **stock_compare** (action=performance) — Compare historical returns & drawdowns
4. **dividend_tracker** (action=analysis) — Compare dividend history for income preference
5. **trade_cost_calculator** (action=breakeven) — Calculate breakeven for position sizing
6. **position_sizing** — Determine allocation

### 🏥 Daily Position Health Check Workflow

1. **position_diagnosis** (action=diagnose) — Full portfolio health scan (A/B/C/D grades)
2. **position_diagnosis** (action=risk_check) — Check concentration & stop-loss proximity
3. **position_diagnosis** (action=action_plan) — Get hold/watch/reduce/exit action list
4. **market_calendar** (action=events) — Check upcoming events affecting held stocks
5. **trade_cost_calculator** (action=round_trip) — Calculate cost for planned trades
6. **conditional_order** — Set/update stop-loss for positions needing attention

### 💰 Dividend Income Strategy Workflow

1. **dividend_tracker** (action=yield_rank) — Find highest dividend yield stocks
2. **dividend_tracker** (action=analysis) — Analyze consistency & trend of dividends
3. **stock_compare** (action=valuation) — Compare valuations of top dividend stocks
4. **market_calendar** (action=events, event_types="dividend") — Upcoming ex-dividend dates
5. **trade_cost_calculator** (action=round_trip) — Ensure dividend exceeds trading costs
6. **portfolio** — Execute buy orders

## Chat Commands Quick Reference

### Core Data Commands

| Command                           | Tool              | Description                        |
| --------------------------------- | ----------------- | ---------------------------------- |
| `/行情 600000` or `/quote 600000` | market_data       | Real-time quote & K-lines          |
| `/基本面 600000`                  | market_data       | PE/PB/ROE and fundamentals         |
| `/k线 600000 1d`                  | market_data       | Historical K-line data with period |
| `/宏观` or `/宏观 regime`         | macro_data        | Macro regime or economic data      |
| `/宏观日历`                       | macro_data        | Economic calendar                  |
| `/财报日历`                       | earnings_calendar | Upcoming earnings dates            |
| `/分析师 600519`                  | earnings_calendar | Analyst ratings & consensus        |

### Smart Money & Institutional

| Command                           | Tool               | Description                       |
| --------------------------------- | ------------------ | --------------------------------- |
| `/机构持仓 600519`                | institutional_flow | Fund holdings for a stock         |
| `/大宗交易` or `/大宗交易 600519` | institutional_flow | Block trade details               |
| `/内幕交易 600519`                | institutional_flow | Insider buy/sell activity         |
| `/吸筹 600519` or `/chip`         | chip_accumulation  | Smart money accumulation analysis |

### Sentiment & Social

| Command                  | Tool             | Description                     |
| ------------------------ | ---------------- | ------------------------------- |
| `/舆情 600519`           | social_sentiment | Stock sentiment analysis        |
| `/热股` or `/hot_stocks` | social_sentiment | Today's trending stocks ranking |
| `/舆情趋势 600519`       | social_sentiment | Sentiment change over time      |

### Capital Flow & Sectors

| Command                      | Tool            | Description                       |
| ---------------------------- | --------------- | --------------------------------- |
| `/资金` or `/fund_flow`      | fund_flow       | Market-wide capital flow ranking  |
| `/股票资金 600519`           | fund_flow       | Individual stock capital flow     |
| `/港通`                      | fund_flow       | Northbound (港通) flow trend      |
| `/龙虎榜` or `/dragon_tiger` | dragon_tiger    | Dragon tiger board hottest stocks |
| `/板块`                      | sector_analysis | Sector ranking & analysis         |
| `/板块协同`                  | sector_synergy  | Sector synergy chain analysis     |

### News & Information

| Command                   | Tool            | Description                       |
| ------------------------- | --------------- | --------------------------------- |
| `/新闻` or `/market_news` | news_summary    | Latest financial market news      |
| `/新闻 600519`            | news_summary    | Stock-specific news               |
| `/预测市场`               | polymarket_odds | Prediction market odds for events |

### Stock Screening & Selection

| Command                 | Tool                 | Description                                   |
| ----------------------- | -------------------- | --------------------------------------------- |
| `/选股 [criteria]`      | stock_screen         | Multi-factor stock screening                  |
| `/推荐 [criteria]`      | stock_recommend      | AI stock recommendations (classic/predictive) |
| `/相关性 600000 vs SPX` | correlation_analysis | Correlation between two symbols               |
| `/连板`                 | lianban_analysis     | Lianban (连板) tracking                       |
| `/技术壁垒 600519`      | tech_moat            | Technology moat analysis                      |

### Strategy & Backtest

| Command         | Tool               | Description                        |
| --------------- | ------------------ | ---------------------------------- |
| `/回测 MA20,50` | backtest           | Backtest a simple strategy         |
| `/策略优化`     | strategy_optimizer | Find optimal strategy parameters   |
| `/策略组合`     | strategy_composer  | Combine multiple strategies        |
| `/策略健康`     | strategy_health    | Monitor strategy health indicators |
| `/期货套利`     | futures_straddle   | Futures spread & arbitrage         |
| `/策略路由`     | strategy_router    | Route to best-performing strategy  |

### Live Trading & Portfolio

| Command                  | Tool                | Description                       |
| ------------------------ | ------------------- | --------------------------------- |
| `/持仓` or `/portfolio`  | portfolio           | View all positions & P&L          |
| `/交易信号` or `/signal` | trading_signal      | AI-generated trading signals      |
| `/订单 BUY 600000 100`   | live_trading        | Place buy order                   |
| `/自选股`                | watchlist           | Manage watchlist                  |
| `/预警 [condition]`      | price_alert         | Set price alert                   |
| `/条件单`                | conditional_order   | Set conditional order (止损/止盈) |
| `/交易笔记`              | trade_journal       | Record trade analysis             |
| `/突破监控`              | breakout_monitor    | Set compound breakout setup       |
| `/仓位计算`              | position_sizing     | Calculate position size for risk  |
| `/定投`                  | dca_schedule        | Create DCA recurring buy plan     |
| `/再平衡`                | portfolio_rebalance | Rebalance portfolio to targets    |

### Comparison & Assistance

| Command                             | Tool                  | Description                        |
| ----------------------------------- | --------------------- | ---------------------------------- |
| `/对比 600519,000858`               | stock_compare         | Side-by-side stock comparison      |
| `/分红 600519`                      | dividend_tracker      | Dividend history & analysis        |
| `/股息排行`                         | dividend_tracker      | Dividend yield ranking             |
| `/费用 50 1000` or `/成本`          | trade_cost_calculator | Calculate trade costs & breakeven  |
| `/保本价 50 1000`                   | trade_cost_calculator | Calculate breakeven sell price     |
| `/日历` or `/交易日`                | market_calendar       | Trading calendar & events          |
| `/诊断` or `/体检`                  | position_diagnosis    | Portfolio health check             |
| `/操作建议`                         | position_diagnosis    | Action plan (hold/reduce/exit)     |

### Options & Risk

| Command                        | Tool               | Description                       |
| ------------------------------ | ------------------ | --------------------------------- |
| `/期权链 600519`               | options_data       | Option chain for underlying stock |
| `/期权策略 600519`             | options_strategy   | Strategy recommendations          |
| `/期权交易 CALL 600519 看涨`   | options_trading    | Execute options order             |
| `/风险 600519` or `/risk`      | risk_analysis      | VaR, drawdown, Sharpe analysis    |
| `/估值 600519` or `/valuation` | value_investing    | Graham & Buffett valuation        |
| `/随机定价`                    | stochastic_pricing | MC simulation & payoff analysis   |

### Tools & Utilities

| Command            | Tool                    | Description                      |
| ------------------ | ----------------------- | -------------------------------- |
| `/分析 [query]`    | data_analysis           | Custom data analysis & export    |
| `/缓存 refresh`    | data_cache              | Refresh data cache               |
| `/定时任务 [spec]` | scheduled_task          | Schedule delayed retry / recheck |
| `/财报分析 600519` | cfo_assistant           | CFO-level financial analysis     |
| `/绩效分析`        | performance_attribution | Performance attribution analysis |

## Data Freshness Guidelines

| Data Source                | Recommended Refresh | Cache TTL      | Tool(s)                                |
| -------------------------- | ------------------- | -------------- | -------------------------------------- |
| **Real-time Quotes**       | Live / 1 second     | 5 seconds      | market_data                            |
| **K-line Data**            | 1 minute            | 5 minutes      | market_data                            |
| **Macro Indicators**       | Daily               | 1 hour         | macro_data                             |
| **Economic Calendar**      | Daily               | 2 hours        | macro_data                             |
| **Earnings Calendar**      | Daily               | 1 hour         | earnings_calendar                      |
| **Analyst Ratings**        | Daily               | 24 hours       | earnings_calendar                      |
| **Institutional Holdings** | Weekly (Q data)     | 24 hours       | institutional_flow                     |
| **Block Trades**           | Daily               | 30 minutes     | institutional_flow                     |
| **Insider Trades**         | Daily               | 1 hour         | institutional_flow                     |
| **Smart Money Signal**     | Daily               | 2 hours        | institutional_flow + chip_accumulation |
| **Sentiment Scores**       | During market hours | 30 minutes     | social_sentiment                       |
| **Hot Stocks Ranking**     | During market hours | **15 minutes** | social_sentiment                       |
| **News Headlines**         | Real-time           | 5 minutes      | news_summary                           |
| **Capital Flow Ranking**   | During market hours | 15 minutes     | fund_flow                              |
| **Northbound Flow**        | Daily               | 1 hour         | fund_flow                              |
| **Dragon Tiger Board**     | Daily (after close) | 24 hours       | dragon_tiger                           |
| **Sector Analysis**        | Daily               | 1 hour         | sector_analysis + sector_synergy       |
| **Option Chain IV**        | Real-time           | 1 minute       | options_data                           |
| **Option Greeks**          | Real-time           | 1 minute       | options_data                           |

> **Note**: The agent will automatically cache results and skip redundant data fetches within the TTL window. You can force a refresh with /cache refresh if data is stale.

---

## 🎯 Tool Categories by Use Case

### 🔎 I want to find promising stocks

**Best tools**: stock_screen → chip_accumulation → institutional_flow → social_sentiment

Steps:

1. Use **stock_screen** to filter by factors (PE < 15, ROE > 15%, growth > 20%)
2. Check **chip_accumulation** to see if institutions are accumulating
3. Verify with **institutional_flow** (smart_money_signal) to confirm conviction
4. Monitor **social_sentiment** to avoid over-extended retail euphoria

### 📈 I want to build & test a trading strategy

**Best tools**: backtest → strategy_optimizer → strategy_health → strategy_composer → custom_strategy

Steps:

1. Choose from 30+ builtin strategies (moving average, RSI, MACD, Bollinger, etc.) or compose a custom blend
2. **custom_strategy** — 自定义规则策略：用声明式 DSL 定义买入/卖出条件（指标+比较+AND/OR），无需编写代码
   - 支持指标: sma, ema, rsi, macd, bollinger, atr, cci, roc, obv, price, volume
   - 支持条件: above, below, crosses_above(上穿), crosses_below(下穿), between(区间)
   - 支持逻辑: all(AND), any(OR)
   - 例: "SMA5上穿SMA20 且 RSI<30 买入，RSI>70 卖出"
3. **strategy_composer** to combine multiple strategies with weighted voting/unanimous methods
4. **backtest** on historical data (past 3 years)
5. **strategy_optimizer** to find optimal parameters
6. **strategy_health** to check Sharpe ratio, max drawdown
7. **position_sizing** (method=risk_pct/kelly/atr_vol) — Calculate optimal position size before deployment
8. Deploy via **live_trading** with optional `confirmation_bars` for breakout validation
9. Set **conditional_order** (止损/止盈/ATR跟踪/均线止损/分批止盈/移动止盈) for auto risk management
10. **breakout_monitor** for compound entry automation (price+volume+hold+time window+pullback)
11. **price_alert** with `order_action` for condition-triggered auto-orders
12. **dca_schedule** for cost-averaging entry across multiple dates

### 💼 I want to manage an active portfolio

**Best tools**: portfolio → trading_signal → price_alert → trade_journal

Steps:

1. Check **portfolio** for current positions & P&L
2. Get **trading_signal** for suggested actions
3. Set **price_alert** for rebalancing triggers
4. Log every trade in **trade_journal**
5. Review **performance_attribution** weekly

### 🏆 I want to do comprehensive stock analysis

**Best tools**: market_data → earnings_calendar → institutional_flow → risk_analysis

Steps:

1. **market_data** (fundamentals) — PE/PB ratios, quality
2. **earnings_calendar** — Consensus EPS, analyst ratings
3. **institutional_flow** — Are smart money buying/selling?
4. **macro_data** — Is the macro environment supportive?
5. **risk_analysis** — What's the volatility & drawdown risk?

### 🎰 I want to trade options

**Best tools**: options_data → options_strategy → stochastic_pricing → options_trading

Steps:

1. **options_data** — View option chain, IV, Greeks
2. **options_strategy** — Get recommendations (call spread, strangle)
3. **stochastic_pricing** — MC simulation of payoff
4. **risk_analysis** — Max loss, breakeven analysis
5. **options_trading** — Execute

---

## 📊 Tool Inventory (52 Tools)

| Category                     | Count  | Tools                                                                                                                                   |
| ---------------------------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| **Core Market Data**         | 5      | market_data, macro_data, earnings_calendar, institutional_flow, social_sentiment                                                        |
| **Stock Selection**          | 4      | stock_screen, stock_recommend, correlation_analysis, chip_accumulation                                                                  |
| **Strategy & Backtest**      | 8      | backtest, strategy_composer, strategy_explorer, strategy_router, strategy_optimizer, strategy_health, lianban_analysis, custom_strategy |
| **Live Trading**             | 7      | live_trading, portfolio, trading_signal, conditional_order, trade_journal, watchlist, price_alert                                       |
| **Position & Automation**    | 4      | breakout_monitor, position_sizing, dca_schedule, portfolio_rebalance                                                                    |
| **Options**                  | 3      | options_data, options_strategy, options_trading                                                                                         |
| **Risk & Valuation**         | 5      | risk_analysis, stochastic_pricing, performance_attribution, value_investing, financial_calculator                                       |
| **Capital Flow & Sentiment** | 4      | fund_flow, sector_synergy, dragon_tiger, sector_analysis                                                                                |
| **News & Market Intel**      | 3      | news_summary, polymarket_odds, cfo_assistant                                                                                            |
| **Cross-cutting**            | 4      | data_analysis, data_cache, scheduled_task, tech_moat                                                                                    |
| **Trading Assistance**       | 5      | stock_compare, dividend_tracker, trade_cost_calculator, market_calendar, position_diagnosis                                              |
| **TOTAL**                    | **52** | —                                                                                                                                       |

---

## 💡 Pro Tips

### ✅ Do This

- **Chain tools together** for better insights — Don't just use market_data; combine with institutional_flow + social_sentiment
- **Backtest before you trade** — Always validate your strategy on historical data first
- **Set price alerts** — Automate your monitoring with price_alert and conditional_order
- **Review trades regularly** — Use trade_journal + performance_attribution weekly
- **Check macro first** — Start with macro_data (regime) before picking individual stocks

### ❌ Don't Do This

- **Chase headlines alone** — social_sentiment + news_summary are inputs, not signals
- **Ignore risk** — Always check risk_analysis before entering a trade
- **Over-optimize** — strategy_optimizer can overfit; validate on out-of-sample data
- **All-in on one stock** — Use correlation_analysis to diversify
- **Ignore smart money** — institutional_flow + chip_accumulation are leading indicators

---

## 🔗 Integration Notes

All tools are callable directly via:

- **Chat**: Natural language questions (agent auto-routes to correct tool)
- **Quick commands**: Slash commands like `/热股`, `/回测`, `/持仓`
- **Direct API**: Call from other services via gateway methods

The agent uses financial-data-hub as a **decision guide** — your question → tool selection → execution.
