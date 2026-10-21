---
name: market-review
description: "用户要求晨报、盘前准备、收盘复盘、大盘概览或当日市场情绪与重要事件时使用。"
allowed-tools: ["macro_data","news_summary","sector_analysis","fund_flow","social_sentiment","lianban_analysis","market_data"]
metadata: {"display_name":"市场晨报与收盘复盘","category":"市场观察","quant-trading":{"emoji":"📊","requires":{"plugins":["quant-trading"]}}}
---

# 市场晨报与收盘复盘
确定晨间/盘后、交易日和市场；非交易日引用最近交易日并注明。
先 news_summary(action=market_news)、macro_data(action=calendar) 获取事件，再 sector_analysis(action=ranking)、fund_flow(action=northbound)、social_sentiment(action=hot_stocks) 交叉观察。短线情绪需要时用 lianban_analysis(action=scan)。
热点股票或热门板块问题在下结论前，必须完成三项资金核对：fund_flow(action=sector) 查询行业资金，fund_flow(action=northbound) 确认北向披露状态，对准备重点分析的前 1 至 3 只候选股调用 fund_flow(action=stock)。有明确股票时先用 market_data(action=quote) 确认代码及交易所，不要只给人气榜就结束。
sector_analysis(action=hot) 可提供当日涨幅领先板块；按 rankingBasis 解读，缺少换手率、上涨家数时不能声称综合热度已确认。某项工具失败时保留已经成功的结果，逐项列出已查、未披露、接口失败和未查询，不能混写成“没查”。
个股资金按返回的最新交易日和近几日趋势解读，若仅有上一交易日数据，明确标为历史资金，不能与盘中价格混称“今日实时流入”。板块资金与板块涨幅按名称和分类核对；概念板块不可冒用同名/近似行业资金。
北向实时净流入在 2024-08-19 后不再披露。availability=not_disclosed 或 realtime.available=false 时说明“已查询，但实时净流入未披露”；接口零值不是净流入为零。rawRealtime 仅供审计；historyType=market_fund_flow 是全市场主力资金，绝不能当作北向历史或替代外资流向。
输出市场事实、领涨/承压板块、事件传导、下一交易日观察清单。盘前未知的收盘值不能预填。资金披露日期可能晚于行情日期，逐项标注；热度和连板强度不等于可实现收益。
