---
name: stock-selection
description: "筛选股票、找候选股、按风格或基本面选股、推荐观察名单并匹配策略时使用。"
allowed-tools: ["stock_screen","stock_recommend","strategy_router","market_data","trading_signal","watchlist","favorites"]
metadata: {"display_name":"智能选股与候选池研究","category":"股票研究","quant-trading":{"emoji":"📊","requires":{"plugins":["quant-trading"]}}}
---

# 智能选股与候选池研究
确定市场、股票池、风格和持有周期。自选范围先 watchlist(action=list) 或 favorites(action=list)。
先 stock_screen(action=list_screeners) 了解可用筛选器，再 stock_screen(action=run_screener)；codes 参数如使用，须是 JSON 数组字符串。也可使用 stock_recommend(mode=classic) 或 predictive，但保留其部分成功和降级提示。
筛选完成后再将真实返回代码传给 strategy_router(action=profile_route_backtest)，symbols 是逗号分隔字符串，不与尚未完成的筛选并行。trading_signal(action=recent) 仅核查已有信号。
输出股票池、入选依据、策略/回测状态、风险和数据日期。筛选得分不代表收益概率，候选名单不等于次日可买或可成交。聊天不保存推荐追踪或筛选业务记录。
