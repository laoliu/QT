---
name: backtest-research
description: "运行或解读单策略、组合、高级成交模型的历史回测，对比收益回撤与交易费用时使用。"
allowed-tools: ["backtest","aipha_backtest","portfolio_backtest","risk_analysis","strategy_validation"]
metadata: {"display_name":"策略回测与结果解读","category":"策略研究","quant-trading":{"emoji":"📊","requires":{"plugins":["quant-trading"]}}}
---

# 策略回测与结果解读
先 backtest(action=list) 确认实际策略 ID，然后确认代码、区间、初始资金和参数再 backtest(action=run)。组合比较用 portfolio_backtest(action=compare)；需要 A 股 T+1、费用和成交模型时按真实工具参数使用 aipha_backtest(action=run)。
报告策略版本/引擎（若可得）、收益、最大回撤、交易次数、费用/滑点、数据区间和基准。不同引擎结果不视为等价。
strategy_validation(action=quality_gates) 需要真实样本量、交易次数、夏普、回撤；PBO 需要多策略收益矩阵。walk_forward 只在提供真实收益矩阵时评价训练选优后的样本外表现；只返回切分方案时标为“仅方案，未验证”，也不是重新训练模型。
不把历史最优结果当作未来保证；缺少成本、样本外或 PIT 证据时逐项标明。
