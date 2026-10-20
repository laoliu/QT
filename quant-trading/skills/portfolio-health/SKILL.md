---
name: portfolio-health
description: "体检当前持仓、检查集中度、组合回撤、相关性、事件风险或持仓风险建议时使用。"
allowed-tools: ["portfolio","risk_analysis","correlation_analysis","market_data","earnings_calendar"]
metadata: {"display_name":"持仓体检与组合风控","category":"组合管理","quant-trading":{"emoji":"📊","requires":{"plugins":["quant-trading"]}}}
---

# 持仓体检与组合风控
先 portfolio(action=show) 和 portfolio(action=summary) 确认账户与持仓；工具返回模拟/实盘身份时明确展示，不用界面账户视图推断执行模式。
用 risk_analysis(action=portfolio_risk)、risk_analysis(action=position_risk)、correlation_analysis(action=matrix) 观察集中和共振风险；earnings_calendar(action=upcoming) 检查事件。
给出每项风险、对应证据、受影响仓位与观察/调整建议。空账户应如实说明，不虚构持仓。缺少成交历史或收益序列时，VaR、回撤等指标标为不可评估。仓位计算和调仓规划可配合相应辅助 Skill，不能声称已减仓。
