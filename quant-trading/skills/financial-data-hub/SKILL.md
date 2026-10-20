---
name: financial-data-hub
description: "查询金融数据来源、可查询字段、行情与历史数据时使用。复杂研究优先交给对应专用 Skill。"
allowed-tools: ["market_data","macro_data","earnings_calendar","news_summary","data_cache"]
metadata: {"display_name":"金融数据导航","category":"通用","quant-trading":{"emoji":"📊","requires":{"plugins":["quant-trading"]}}}
---

# 金融数据导航
根据问题选择最小必要查询，避免一次加载无关分析。
- 大盘概览先查询三大指数：market_data(action=quote, market=index)，上证指数 000001.SH、深证成指 399001.SZ、创业板指 399006.SZ。指数名称搜索使用 action=search, market=index。000001/cn_stock 是平安银行（000001.SZ），不能用裸代码猜成上证指数。
- 行情结果中的 dataTime/dataDate 是数据源时间，retrievedAt 只是查询时间。dataTime 为空时明确标注“行情时间未知”，不能声称为今天盘中表现；开盘前区分最近快照与今天交易，不因尚未开盘就断言指数不可查询。
- 价格、历史 K 线、证券搜索、基本面分别使用 market_data(action=quote)、market_data(action=klines)、market_data(action=search)、market_data(action=fundamentals)。先确认股票代码和市场，A 股明确 SH/SZ；不要将同号指数当股票。
- 宏观数据先 macro_data(action=indicators)，再 macro_data(action=history)；事件日程用 macro_data(action=calendar) 或 earnings_calendar(action=upcoming)。
- 市场/个股新闻用 news_summary(action=market_news) 或 news_summary(action=stock_news)。
- 缓存覆盖查询 data_cache(action=stats)，该结果主要是 SQLite 统计，不代表全部外部 Parquet 或 PIT 覆盖。
回答列出数据时间、来源、市场、复权/周期（工具有返回时）和缺失字段。工具没有 indicators/cash_flow action；不要按旧接口调用，也不要把未知数据补成零。
