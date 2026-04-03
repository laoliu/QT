"""
Quantitative trading strategy library.

All strategies extend the BaseStrategy class (which extends bt.Strategy)
and are auto-discovered by the strategy registry.

Available strategies:
  - macd_strategy:           MACD 振荡器（趋势跟踪）
  - rsi_strategy:            RSI 超买超卖（振荡器）
  - kdj_strategy:            KDJ 随机指标（振荡器）
  - turtle_strategy:         海龟交易法（趋势跟踪）
  - pair_trading_strategy:   配对交易 / 统计套利
  - grid_strategy:           网格交易（区间震荡）
  - auction_strategy:        竞价多条件退出（短线复合）
  - dragon_return_strategy:  龙回头（龙头回调二次启动）

QMT tick-driven strategies (not bt-based):
  - qmt_lianban_monitor:        连板接力封板确认监控
  - sector_synergy_strategy:    板块协同+大单净额+攻击斜率追涨（封板前介入）
"""
