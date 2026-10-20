---
name: strategy-research
description: "探索策略组合、搜索参数、比较候选策略或分析信号有效性时使用。"
allowed-tools: ["backtest","strategy_explorer","strategy_optimizer","strategy_router","strategy_validation","trading_signal"]
metadata: {"display_name":"策略探索与参数优化","category":"策略研究","quant-trading":{"emoji":"📊","requires":{"plugins":["quant-trading"]}}}
---

# 策略探索与参数优化
确认标的、样本区间、候选数量、优化目标和成本。backtest(action=list) 获取可用策略。
strategy_explorer(action=explore) 在聊天中仅探索，不保存最佳组合；strategy_explorer(action=recommend) 查询已有组合。strategy_optimizer(action=optimize) 搜索参数，strategy_optimizer(action=validate_signals) 查看信号表现；不修改组合权重配置。
先划分研究/验证时间，再查看样本内与样本外结果，记录试验次数。strategy_validation(action=pbo) 必须使用多个候选的真实收益矩阵；没有足够数据不能给“过拟合检验通过”。
输出候选对比、选择依据、成本敏感性和未验证项。探索生成的临时组合 ID 不代表已经持久化；若要再次回测，使用可用策略 ID 和明确参数。
