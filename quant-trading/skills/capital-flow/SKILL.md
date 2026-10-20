---
name: capital-flow
description: "分析主力资金、北向资金、龙虎榜、机构增减持、筹码吸筹或资金与价格背离时使用。"
allowed-tools: ["market_data","fund_flow","dragon_tiger","institutional_flow","chip_accumulation"]
metadata: {"display_name":"资金流与主力行为","category":"市场观察","quant-trading":{"emoji":"📊","requires":{"plugins":["quant-trading"]}}}
---

# 资金流与主力行为
先 market_data(action=quote) 确认标的。fund_flow(action=stock) 查看个股资金，fund_flow(action=rank) 查看排行；板块和北向用对应 sector、northbound action。
结合 dragon_tiger(action=stock)、institutional_flow(action=holding_changes)、institutional_flow(action=block_trades)、chip_accumulation(action=detail) 分析。
按日期对齐价格、净流入、机构披露期；区分实时估计、大单划分与正式披露。列出持续性和背离证据，缺乏连续样本时不声称主力建仓完成。
