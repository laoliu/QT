---
name: stock-analysis-expert
description: "对某只股票做综合分析、研判趋势或投资逻辑时使用。专门的选股、财报、持仓问题由对应 Skill 处理。"
allowed-tools: ["market_data","value_investing","cfo_assistant","fund_flow","chip_accumulation","earnings_calendar","news_summary","tech_moat"]
metadata: {"display_name":"个股综合研究","category":"股票研究","quant-trading":{"emoji":"📊","requires":{"plugins":["quant-trading"]}}}
---

# 个股综合研究
先确认代码、市场、分析截止日期和持有周期。
1. 用 market_data(action=quote) 和 market_data(action=klines) 获取价格与量价序列，先给行情事实。技术指标仅在已有数据足够且可以可靠计算时给出，否则说明未计算；不存在 market_data 的 indicators action。
2. market_data(action=fundamentals)、value_investing(action=comprehensive)、cfo_assistant(action=red_flags) 查看估值、盈利和财务问题。历史研究要求说明公告可见时间；现时基本面不自动具有 PIT 语义。
3. fund_flow(action=stock)、chip_accumulation(action=detect) 验证资金迹象；news_summary(action=stock_news)、earnings_calendar(action=stock_forecast) 补充事件与预期。
4. 技术壁垒问题可调用 tech_moat(action=analyze)，区分新闻叙事、证据和已验证业绩。
按“事实与日期、支持逻辑、反面证据、情景与观察条件”组织回答。分析建议不是委托单；不以固定打分阈值自动推导买入，不编造支撑价、目标价或缺失指标。
