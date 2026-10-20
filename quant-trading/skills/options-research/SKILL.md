---
name: options-research
description: "查询期权链、波动率、希腊值，比较期权组合、收益图、情景变化或定价方法时使用。"
allowed-tools: ["options_data","options_strategy","stochastic_pricing","market_data"]
metadata: {"display_name":"期权策略与情景分析","category":"衍生品研究","quant-trading":{"emoji":"📊","requires":{"plugins":["quant-trading"]}}}
---

# 期权策略与情景分析
确认标的、合约、到期日、行权价和报价时间。options_data(action=contracts)、options_data(action=chain)、options_data(action=quote) 获取合约信息；options_data(action=iv_surface) 查看波动率。
options_strategy(action=list) 查看模板，然后 options_strategy(action=payoff)、options_strategy(action=scenario)、options_strategy(action=compare) 分析策略。
数值计算使用 stochastic_pricing，engine 与 action 必须匹配：monte_carlo 配 price_european/price_asian/price_barrier/greeks；binomial_tree 配 price/convergence；delta_hedge 配 simulate/ensemble/frequency；var 配 parametric/historical/monte_carlo/stress_test/backtest；vol_models 配 sabr_calibrate/svi_calibrate/dupire 等。
区分到期收益和当前估值，披露权利金、合约乘数、费用、波动率和流动性假设。只有工具返回并且条件满足时才给出最大损失；裸卖空不能描述为风险有限。
