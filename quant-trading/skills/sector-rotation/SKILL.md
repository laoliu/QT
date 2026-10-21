---
name: sector-rotation
description: "研究行业板块强弱、热点切换、轮动、板块动量或涨停协同时使用。"
allowed-tools: ["sector_analysis","sector_synergy","fund_flow","market_data","news_summary"]
metadata: {"display_name":"板块轮动研究","category":"市场观察","quant-trading":{"emoji":"📊","requires":{"plugins":["quant-trading"]}}}
---

# 板块轮动研究
sector_analysis(action=ranking) 确定候选，再用 sector_analysis(action=rotation) 和 sector_analysis(action=momentum) 检查持续性；sector_analysis(action=detail) 查看成员。
fund_flow(action=sector) 验证资金，sector_synergy(action=scan) 或 sector_synergy(action=analyze) 检查协同；news_summary(action=events) 核对催化。
热点股票/板块分析必须查询 fund_flow(action=sector)、fund_flow(action=northbound)，并为重点介绍的前 1 至 3 只股票查询 fund_flow(action=stock)，分别列出资金日期与查询状态。北向 availability=not_disclosed 表示已查但未披露，不能将接口零值或全市场主力资金当成北向净流入。
sector_analysis(action=hot) 的 rankingBasis 会说明热度依据；缺失换手率、上涨家数、主力资金时不得补成零或虚构综合分。rotation/momentum 的 scope 是已分析样本范围，历史失败不阻止使用 ranking/hot 的当日结果。按真实板块分类核对资金；行业资金不可冒充概念资金。
给出板块对比、强弱变化、成分股广度、资金确认与失效条件。板块概念可能重叠，不能把同一股票重复当作独立分散配置。追涨信号只作研究，不触发订单。
