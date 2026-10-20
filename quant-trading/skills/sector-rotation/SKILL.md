---
name: sector-rotation
description: "研究行业板块强弱、热点切换、轮动、板块动量或涨停协同时使用。"
allowed-tools: ["sector_analysis","sector_synergy","fund_flow","market_data","news_summary"]
metadata: {"display_name":"板块轮动研究","category":"市场观察","quant-trading":{"emoji":"📊","requires":{"plugins":["quant-trading"]}}}
---

# 板块轮动研究
sector_analysis(action=ranking) 确定候选，再用 sector_analysis(action=rotation) 和 sector_analysis(action=momentum) 检查持续性；sector_analysis(action=detail) 查看成员。
fund_flow(action=sector) 验证资金，sector_synergy(action=scan) 或 sector_synergy(action=analyze) 检查协同；news_summary(action=events) 核对催化。
给出板块对比、强弱变化、成分股广度、资金确认与失效条件。板块概念可能重叠，不能把同一股票重复当作独立分散配置。追涨信号只作研究，不触发订单。
