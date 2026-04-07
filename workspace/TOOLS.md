# TOOLS.md - 工具使用备忘

Skills 定义工具的能力，这个文件记录你的环境特定的使用细节和技巧。

---

## 已安装插件：quant-trading

拥有 50+ AI 工具，覆盖行情、选股、回测、交易、期权、风控全链条。**不要**尝试用外部搜索/抓取来获取财务数据，直接调用内置工具。

---

## 一、行情与基本面

| 工具 | 用途 |
|------|------|
| `market_data` | 实时行情、K线（含技术指标）、基本面（PE/PB/ROE）、证券搜索 |
| `macro_data` | GDP、CPI、PMI、利率、经济周期研判 |
| `news_summary` | 财经新闻聚合与情绪分析 |
| `earnings_calendar` | 财报日期、分析师评级、业绩预告 |

## 二、选股与发现

| 工具 | 用途 |
|------|------|
| `stock_screen` | 多因子筛选（F-Score/CAN SLIM/魔法公式/技术面/量价/PEG） |
| `stock_recommend` | AI 智能选股推荐 |
| `sector_analysis` | 板块排名、行业详细指标 |
| `favorites` / `watchlist` | 自选股与关注列表管理 |

### stock_screen 调用要点

```
# F-Score 筛选指定股票（A股代码不加.SZ/.SH后缀）
stock_screen(action="run_screener", screenerId="fscore", market="cn_stock", codes='["600519","000858"]')

# 沪深300 魔法公式筛选
stock_screen(action="run_screener", screenerId="magic_formula", market="cn_stock", universe="hs300", limit=20)

# 列出所有策略
stock_screen(action="list_screeners")
```

**可用策略：**

| screenerId | 策略 | 说明 |
|------------|------|------|
| `fscore` | F-Score | Piotroski 9项价值质量打分 |
| `canslim` | CAN SLIM | 成长动量（需K线数据） |
| `magic_formula` | 魔法公式 | Greenblatt 盈利+估值双排名 |
| `technical` | 技术面 | RSI/MACD/均线（需K线数据） |
| `volume_price` | 量价异动 | 放量突破（需K线数据） |
| `peg` | PEG/GARP | 合理成长策略 |

**股票池 (universe)：**

| universe | 指数 | 成分股数 |
|----------|------|----------|
| `sz50` | 上证50 | ~50 |
| `hs300` | 沪深300 | ~300 |
| `zz100` | 中证100 | ~100 |
| `zz500` | 中证500 | ~500 |
| `zz1000` | 中证1000 | ~1000 |
| `cyb` | 创业板指 | ~100 |
| `kc50` | 科创50 | ~50 |

不传 universe 也不传 codes → 全市场筛选（A股约5000只，较慢）。

## 三、回测与策略

| 工具 | 用途 |
|------|------|
| `backtest` | 单/多标的策略回测 |
| `portfolio_backtest` | 组合级回测（含再平衡） |
| `strategy_composer` | 策略组合构建 |
| `strategy_optimizer` | 参数优化 |
| `strategy_explorer` | 策略性能分析 |
| `strategy_router` | 标的→最优策略路由 |
| `strategy_health` | 策略健康度监控 |
| `custom_strategy` | 自定义规则策略 |
| `correlation_analysis` | 资产相关性分析 |

## 四、持仓与交易

| 工具 | 用途 |
|------|------|
| `portfolio` | 持仓管理（查看/买卖/跟踪） |
| `position_sizing` | 仓位计算（基于风险） |
| `portfolio_rebalance` | 组合再平衡 |
| `dca_schedule` | 定投计划管理 |
| `trade_journal` | 交易日志与复盘笔记 |
| `live_trading` | 实盘交易（下单/持仓/暂停/恢复） |
| `conditional_order` | 条件单（止损/止盈/追踪止损） |
| `price_alert` | 价格预警 + 可选自动下单 |

## 五、技术分析与信号

| 工具 | 用途 |
|------|------|
| `trading_signal` | 策略信号查询（近期/按标的/按策略/命中率） |
| `lianban_analysis` | 连板分析 |
| `dragon_tiger` | 龙虎榜分析 |
| `sector_synergy` | 板块协同追涨 |
| `breakout_monitor` | 突破形态监控 |
| `chip_accumulation` | 主力吸筹扫描 |

## 六、资金面与情绪

| 工具 | 用途 |
|------|------|
| `fund_flow` | 资金流向（机构/散户） |
| `institutional_flow` | 基金持仓 + 内部交易监控 |
| `social_sentiment` | 社区热股与社交情绪 |

## 七、期权

| 工具 | 用途 |
|------|------|
| `options_data` | 期权链、行情、IV曲面 |
| `options_strategy` | 期权策略推荐（损益图/Greeks） |
| `options_trading` | 期权下单与持仓管理 |
| `stochastic_pricing` | 蒙特卡洛/二叉树定价 |

## 八、风控与绩效

| 工具 | 用途 |
|------|------|
| `risk_analysis` | VaR、最大回撤、夏普、Sortino |
| `performance_attribution` | 交易级归因（价格/板块/时机） |
| `financial_calculator` | DCF/PE/PB/PEG估值计算 |
| `value_investing` | Graham/Buffett 估值框架 |

## 九、辅助工具

| 工具 | 用途 |
|------|------|
| `data_cache` | 缓存管理（统计/清理） |
| `data_analysis` | 统计分析、回归、相关性 |
| `scheduled_task` | 定时任务调度 |
| `cfo_assistant` | CFO级财报深度分析 |
| `tech_moat` | 技术护城河分析 |

## 十、交易辅助

| 工具 | 用途 |
|------|------|
| `stock_compare` | 个股多维度对比（技术+基本面+涨跌幅并排对比） |
| `dividend_tracker` | 分红追踪（历史分红、除权日、股息率排行） |
| `trade_cost_calculator` | 交易成本计算（佣金/印花税/过户费/保本价） |
| `market_calendar` | 交易日历（休市日/除权日/IPO/解禁/周概览） |
| `position_diagnosis` | 持仓诊断（健康评级A-D + 风险扫描 + 操作建议） |

### stock_compare 使用要点

```
# 多维度对比
stock_compare(action="compare", symbols="600519,000858,000333")

# 估值对比
stock_compare(action="valuation", symbols="600519,000858")

# 区间涨跌幅对比
stock_compare(action="performance", symbols="600519,000858,000333", period="3m")
```

### trade_cost_calculator 使用要点

```
# 买入费用计算
trade_cost_calculator(action="cost", price=50, quantity=1000, side="buy")

# 买卖全程费用
trade_cost_calculator(action="round_trip", price=50, quantity=1000, sell_price=55)

# 保本价计算
trade_cost_calculator(action="breakeven", price=50, quantity=1000)
```

### position_diagnosis 使用要点

```
# 全持仓诊断
position_diagnosis(action="diagnose")

# 操作建议清单
position_diagnosis(action="action_plan")

# 单只持仓深度诊断
position_diagnosis(action="single", symbol="600519")
```

---

## 常用快捷命令

| 命令 | 功能 |
|------|------|
| `/选股` or `/screen-qmt` | 分类选股 |
| `/真龙` or `/dragon` | 龙头战法选股 |
| `/简报` | 晨间简报 |
| `/收盘` | 收盘复盘 |
| `/日志` | 交易日志 |
| `/周报` / `/月报` | 周/月度绩效报告 |
| `/板块` | 板块分析 |
| `/资金` | 资金流向 |
| `/龙虎榜` | 龙虎榜 |
| `/预警` | 设置预警 |
| `/期权链` / `/希腊值` | 期权相关 |
| `/对比` | 个股对比 |
| `/分红` | 分红追踪 |
| `/费用` / `/成本` | 交易成本计算 |
| `/日历` | 交易日历 |
| `/诊断` / `/体检` | 持仓诊断 |

---

## 重要提醒

- **数据来源已内置**：A股用 akshare（新浪财经），美股用 yfinance，无需外部搜索 API
- **A股代码不加后缀**：`600519` 而非 `600519.SH`
- **CN K线受限**：受代理限制，A股 technical/volume_price/canslim 的K线可能获取失败，F-Score/magic_formula/peg 不受影响
