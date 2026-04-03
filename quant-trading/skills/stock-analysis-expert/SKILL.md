---
name: stock-analysis-expert
description: >-
  专业A股投资分析框架。当用户提到股票分析、个股研判、行业研究、
  投资策略、技术面分析、基本面分析、选股推荐、持仓诊断、
  板块轮动、资金流向分析、价值投资、趋势交易时使用此skill。
  涵盖完整的研报级个股分析、板块分析、组合管理、风控模型。
metadata:
  openclaw:
    emoji: "📊"
    always: true
    requires:
      plugins: ["quant-trading"]
---

# 专业股票分析框架

## 一、个股深度研究模板

### 1.1 技术分析维度

使用以下工具组合进行技术面判断：

```
分析目标                     → 工具调用
────────────────────────────────────────
趋势方向判断                 → market_data(action=indicators) 查看 MA5/10/20/60
超买超卖判断                 → RSI > 70 超买, RSI < 30 超卖
动能变化                     → MACD 金叉/死叉 + 柱状图方向
波动区间                     → 布林带宽窄 + 价格位置
成交量配合                   → market_data(action=klines) 量价关系
支撑/压力位                  → 前高前低 + 均线位置 + 布林带上下轨
```

**技术面综合判断框架**：

- **多头排列**（MA5>MA10>MA20>MA60）+ 放量 → 强势上涨趋势
- **空头排列**（MA5<MA10<MA20<MA60）+ 缩量 → 弱势下跌趋势
- **MACD零轴上方金叉** → 强势确认信号
- **RSI背离** → 反转预警信号

### 1.2 基本面分析维度

```
分析目标                     → 工具调用
────────────────────────────────────────
盈利能力                     → market_data(action=fundamentals) → ROE/毛利率/净利率
估值水平                     → value_investing → PE/PB/PEG + 行业对比
成长性                       → earnings_calendar → 营收/净利增速趋势
财务健康                     → cfo_assistant → 资产负债率/现金流/商誉
估值模型                     → financial_calculator → DCF/DDM 估算内在价值
```

**估值判断标准**：
| 指标 | 低估 | 合理 | 高估 |
|------|------|------|------|
| PE（相对行业） | < 行业均值0.7x | 0.7x~1.3x | > 1.3x |
| PB（ROE调整后） | PB/ROE < 0.1 | 0.1~0.2 | > 0.2 |
| PEG | < 0.5 | 0.5~1.0 | > 1.5 |

### 1.3 资金面分析维度

```
分析目标                     → 工具调用
────────────────────────────────────────
主力资金动向                 → fund_flow(action=stock, symbol=xxx)
北向资金持仓                 → institutional_flow(action=fund_holdings)
机构增减仓                   → institutional_flow(action=holding_changes)
大宗交易信号                 → institutional_flow(action=block_trades)
主力吸筹/出货判断            → chip_accumulation
```

### 1.4 事件催化维度

```
分析目标                     → 工具调用
────────────────────────────────────────
近期财报预期                 → earnings_calendar(action=upcoming)
分析师评级变化               → earnings_calendar(action=analyst_ratings)
个股新闻事件                 → news_summary(action=stock_news)
市场情绪                     → social_sentiment(action=stock_sentiment)
```

---

## 二、板块与行业分析模板

### 2.1 板块轮动分析

```
1. sector_analysis              → 板块涨跌幅排名 + 资金流入排名
2. fund_flow(action=rank)       → 板块级别资金净流入 TOP/BOTTOM
3. sector_synergy               → 板块联动效应 + 产业链传导
4. lianban_analysis             → 连板梯队 → 辨识市场主线题材
5. dragon_tiger                 → 龙虎榜 → 游资主攻方向
```

### 2.2 主线题材判断逻辑

- 连板股数量 ≥ 3 + 板块资金持续流入 → **强主线**
- 有龙虎榜知名游资参与 + 涨停板数递增 → **主线加速**
- 龙头股断板 + 板块资金转流出 → **主线退潮**

---

## 三、选股策略框架

### 3.1 不同风格的选股路径

**价值型选股**：

```
stock_screen(factors=pe<15,roe>15,peg<1) → value_investing 估值验证 → backtest 回测验证
```

**成长型选股**：

```
stock_screen(factors=revenue_growth>30,profit_growth>30) → earnings_calendar 财报验证 → backtest 回测
```

**趋势型选股**：

```
stock_screen(factors=rps_20>90,volume_ratio>1.5) → market_data(indicators) 技术确认 → strategy_router 策略匹配
```

**事件驱动型**：

```
news_summary(action=market_news) → social_sentiment(action=hot_stocks) → chip_accumulation 筹码验证
```

### 3.2 AI智能选股（一键推荐）

当用户没有明确偏好时，直接使用：

```
stock_recommend(count=5)  → 自动完成全市场扫描 + 多策略回测 + 排名（经典模式）
```

#### 前瞻选股（发现未来潜力股）

当用户说"推荐未来可能涨的"、"不要已经涨上来的"、"找低位蓄势股"时：

```
stock_recommend(mode=predictive, horizon=all) → 量价低位筛选 + 主力吸筹 + 机构行为 + 综合评分
```

- `horizon=short` — 短期（1-10日）：底部放量反转、动量触发临近
- `horizon=mid` — 中期（10-60日）：主力建仓、板块轮动受益
- `horizon=long` — 长期（60日+）：价值成长、机构持续增持
- 评分维度：主力吸筹(30%) + 量价信号(20%) + 机构动向(20%) + 底背离(15%) + 底部反转(15%)

---

## 四、风险管理框架

### 4.1 持仓风险评估

```
risk_analysis → VaR(95%) / 最大回撤 / 夏普比率 / 波动率
portfolio     → 当前持仓 + 仓位集中度
correlation_analysis → 持仓标的间相关性（避免过度集中）
```

### 4.2 风控标准

| 指标           | 警告线 | 止损线 |
| -------------- | ------ | ------ |
| 单只个股仓位   | > 20%  | > 30%  |
| 单一行业集中度 | > 30%  | > 50%  |
| 组合最大回撤   | > 10%  | > 15%  |
| 日VaR(95%)     | > 3%   | > 5%   |

**仓位管理工具**：

- **position_sizing** (action=compute) — 根据风控标准自动计算仓位
  - method=risk_pct — 单笔风险不超过总资金N% (仓位 = 资金×N% / (入场价-止损价))
  - method=kelly — 凯利公式优化仓位
  - method=atr_vol — ATR波动率仓位控制

### 4.3 止盈止损建议

- 短线（1-5日）：止损 3-5%，止盈 8-15%
- 中线（1-3月）：止损 8-10%，止盈 20-30%
- 长线（>6月）：止损 15%，动态止盈（跌破20日均线）

**自动化风控工具组合**：

- **conditional_order** — 止损(stop_loss) / 止盈(take_profit) / ATR移动止损(trailing_atr) / 均线止损(indicator_stop) / 分批止盈(staged_tp) / 移动止盈(trailing_tp)
- **breakout_monitor** (add_eod_rule) — 收盘跌破N日均线自动清仓/减半
- **dca_schedule** — 定投降低平均成本

---

## 五、常用跨工具工作流

### 5.1 晨间选股流程

```
1. macro_data(action=calendar)      → 今日重要经济事件
2. news_summary(action=market_news) → 隔夜重大新闻
3. fund_flow(action=northbound)     → 北向资金动向
4. sector_analysis                  → 板块预判
5. stock_recommend(count=3)         → AI 选股推荐（经典模式）
6. stock_recommend(mode=predictive) → 前瞻选股（寻找低位蓄势潜力股）
```

### 5.2 个股买入决策流程

```
1. 技术面 → market_data(indicators) 确认趋势
2. 基本面 → value_investing 确认估值合理
3. 资金面 → fund_flow + chip_accumulation 确认主力动向
4. 策略验证 → strategy_router → backtest 回测
5. 风控 → risk_analysis 评估加仓后组合风险
6. 仓位 → position_sizing(method=risk_pct) 计算买入股数
→ 综合评分 ≥ 3/5 维度看多 → 建议买入（附风险提示）
```

### 5.2b 突破跟踪自动化流程

```
1. 确定关键位 → market_data(action=klines) 查看前高/支撑位
2. 设置突破监控 → breakout_monitor(action=add_setup)
   • trigger_price = 前高 + 缓冲
   • volume_min_ratio = 1.5 (放量确认)
   • hold_checks = 10 (保持约 5 分钟)
   • time_windows = "09:45-10:30,14:00-15:00"
   • enable_pullback = true (可选: 等回踩再入场)
3. 仓位计算 → position_sizing(method=risk_pct, risk_pct=1)
4. 设置止损 → conditional_order(add_trailing_atr)
5. 设置止盈 → conditional_order(add_staged_tp, tiers: +8%/-50%, +12%再减)
6. 收盘检查 → breakout_monitor(add_eod_rule, ma_period=10)
```

### 5.3 持仓复盘流程

```
1. portfolio                           → 当前持仓概况
2. 逐只: market_data(indicators)       → 技术面变化
3. performance_attribution             → 归因分析
4. risk_analysis                       → 当前组合风险
5. portfolio_rebalance(action=plan)    → 检查是否需要再平衡
6. trade_journal                       → 记录复盘笔记
```

### 5.4 周度策略评审

```
1. strategy_health                     → 活跃策略健康度
2. strategy_explorer                   → 本周策略表现对比
3. backtest(action=run, period=最近1周) → 策略回测验证
4. trading_signal(action=hitrate)      → 信号准确率追踪
```

---

## 六、回答规范

1. **数据为王** — 所有观点必须基于工具返回的真实数据，不编造数据
2. **表格呈现** — 关键对比数据用表格展示（估值对比、板块排名等）
3. **多维度** — 至少从技术面+基本面+资金面三个维度分析
4. **风险提示** — 每次投资建议结尾必须附带风险提示
5. **时间维度** — 区分短线/中线/长线给出不同建议
6. **可操作性** — 给出明确的价格区间、仓位建议、止盈止损位
