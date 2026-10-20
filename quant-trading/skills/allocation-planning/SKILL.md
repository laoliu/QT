---
name: allocation-planning
description: "计算买入数量、风险预算、凯利或 ATR 仓位、组合目标权重以及调仓计划时使用。"
allowed-tools: ["portfolio","market_data","position_sizing","portfolio_optimizer","portfolio_rebalance","risk_analysis","correlation_analysis"]
metadata: {"display_name":"仓位计算与调仓规划","category":"组合管理","quant-trading":{"emoji":"📊","requires":{"plugins":["quant-trading"]}}}
---

# 仓位计算与调仓规划
先 portfolio(action=summary)、portfolio(action=show) 和 market_data(action=quote) 确认资产、持仓和价格，核对权益是否真实可得。
position_sizing(action=compute) 计算数量，risk_pct/pct 等比例参数采用 0—1；若权益未知不使用默认资金冒充真实资产。position_sizing(action=config) 仅查看规则。
portfolio_optimizer(action=optimize) 需要同频、同日期、同币种且已对齐的真实收益率；没有收益矩阵先获取数据或说明不足，不能编造序列。
portfolio_rebalance(action=plan) 生成计划，不执行。输出目标权重、现金保留、预计买卖数量、成本与失效条件，并明确只是计划。
