---
name: market-review
description: "用户要求晨报、盘前准备、收盘复盘、大盘概览或当日市场情绪与重要事件时使用。"
allowed-tools: ["macro_data","news_summary","sector_analysis","fund_flow","social_sentiment","lianban_analysis"]
metadata: {"display_name":"市场晨报与收盘复盘","category":"市场观察","quant-trading":{"emoji":"📊","requires":{"plugins":["quant-trading"]}}}
---

# 市场晨报与收盘复盘
确定晨间/盘后、交易日和市场；非交易日引用最近交易日并注明。
先 news_summary(action=market_news)、macro_data(action=calendar) 获取事件，再 sector_analysis(action=ranking)、fund_flow(action=northbound)、social_sentiment(action=hot_stocks) 交叉观察。短线情绪需要时用 lianban_analysis(action=scan)。
输出市场事实、领涨/承压板块、事件传导、下一交易日观察清单。盘前未知的收盘值不能预填。资金披露日期可能晚于行情日期，逐项标注；热度和连板强度不等于可实现收益。
