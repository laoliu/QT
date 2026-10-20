---
name: trade-review
description: "分析历史交易盈亏、策略贡献、推荐胜率、交易笔记或按日周月复盘时使用。"
allowed-tools: ["portfolio","performance_attribution","recommend_tracker","trade_journal","trading_signal"]
metadata: {"display_name":"交易复盘与绩效归因","category":"组合管理","quant-trading":{"emoji":"📊","requires":{"plugins":["quant-trading"]}}}
---

# 交易复盘与绩效归因
确定期间和账户范围。portfolio(action=live_trades) 或 portfolio(action=daily_pnl) 查看账户记录，再 performance_attribution(action=full_report)、performance_attribution(action=by_strategy)、performance_attribution(action=by_symbol) 分解收益。
推荐复盘用 recommend_tracker(action=list)、recommend_tracker(action=stats)、recommend_tracker(action=review)，并披露记录最后更新时间，不刷新/结算推荐状态。
笔记用 trade_journal(action=recent) 或 trade_journal(action=search)；信号用 trading_signal(action=hit_rate)。
不同工具可能来自不同交易记录库，不把推荐模拟收益、Paper 收益和真实账户成交收益混成一份总收益。缺失账户来源或净值口径时明确说明；不写入/删除笔记。
