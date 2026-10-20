---
name: convertible-bond-research
description: "分析可转债价格、转股溢价、双低、Delta 分区、转债因子或筛选转债时使用。"
allowed-tools: ["cb_analysis","market_data"]
metadata: {"display_name":"可转债研究","category":"股票研究","quant-trading":{"emoji":"📊","requires":{"plugins":["quant-trading"]}}}
---

# 可转债研究
cb_analysis(action=list_bonds) 确认转债范围，cb_analysis(action=metrics) 查看指标，cb_analysis(action=factors) 查看因子，cb_analysis(action=select) 筛选；cb_analysis(action=delta_zone) 观察股债属性。
核对正股/转债身份、交易日、数据源及赎回/回售信息是否具备。列出价格、转股溢价、策略分数、流动性和缺失风险项。缺少赎回公告时不能判定没有强赎风险；股票 T+1 与转债 T+0 规则须区分。
