"""
Quantitative trading strategy library.

All strategies extend the BaseStrategy class (which extends bt.Strategy)
and are auto-discovered by the strategy registry.

=== CTA趋势策略 (Trend Following) ===
  - macd_strategy:             MACD 振荡器（趋势跟踪）
  - ema_cross_strategy:        EMA 均线交叉（双均线系统）
  - adx_trend_strategy:        ADX 趋势强度（方向确认）
  - parabolic_sar_strategy:    抛物线SAR（跟踪止损）
  - ichimoku_strategy:         一目均衡表（云图趋势）
  - supertrend_strategy:       超级趋势（ATR跟踪）
  - dema_strategy:             DEMA 双重指数均线
  - tema_strategy:             TEMA 三重指数均线
  - aroon_strategy:            阿隆指标（趋势起始）
  - vortex_strategy:           漩涡指标（趋势方向）
  - cci_strategy:              CCI 顺势指标
  - trix_strategy:             TRIX 三重指数平滑
  - momentum_strategy:         动量策略
  - dmi_strategy:              DMI 趋向指标
  - turtle_strategy:           海龟交易法（经典趋势）

=== CTA通道策略 (Channel/Band) ===
  - bollinger_strategy:        布林带均值回归
  - keltner_channel_strategy:  肯特纳通道
  - donchian_breakout_strategy: 唐奇安通道突破
  - atr_channel_strategy:      ATR通道突破
  - envelope_strategy:         百分比包络线
  - price_channel_breakout_strategy: 价格通道突破

=== CTA指标策略 (Indicator-based) ===
  - rsi_strategy:              RSI 超买超卖
  - kdj_strategy:              KDJ 随机指标
  - stochastic_strategy:       随机振荡器
  - williams_r_strategy:       威廉指标
  - obv_strategy:              OBV 能量潮
  - mfi_strategy:              MFI 资金流量
  - cmf_strategy:              蔡金资金流
  - adl_strategy:              ADL 聚散指标
  - roc_strategy:              ROC 变化率
  - vwap_strategy:             VWAP 成交量加权均价

=== CTA形态策略 (Pattern Recognition) ===
  - engulfing_strategy:        吞没形态
  - hammer_strategy:           锤子线/射击之星
  - doji_strategy:             十字星形态
  - morning_star_strategy:     启明星/黄昏星
  - three_soldiers_strategy:   三兵三鸦
  - harami_strategy:           孕线形态

=== 基础组合策略 (Basic Composite) ===
  - macd_rsi_combo_strategy:      MACD+RSI 组合
  - bollinger_rsi_combo_strategy: 布林带+RSI 组合
  - ma_volume_combo_strategy:     均线+量能 组合
  - kdj_macd_combo_strategy:      KDJ+MACD 组合

=== 高阶组合策略 (Advanced Composite) ===
  - ichimoku_adx_combo_strategy:    一目均衡表+ADX 组合
  - multi_timeframe_ma_strategy:    多周期均线
  - volatility_breakout_strategy:   波动率突破
  - adaptive_ma_strategy:           自适应均线 (KAMA)
  - regime_switch_strategy:         趋势+均值回归切换
  - dual_thrust_strategy:           Dual Thrust 双推力
  - r_breaker_strategy:             R-Breaker 突破反转
  - pivot_point_strategy:           枢轴点策略
  - multi_factor_strategy:          多因子策略
  - mean_reversion_cn_strategy:     A股均值回归

=== 特色策略 (Specialty) ===
  - grid_strategy:                网格交易
  - pair_trading_strategy:        配对交易/统计套利
  - auction_strategy:             竞价多条件退出（短线复合）
  - dragon_return_strategy:       龙回头（龙头回调二次启动）
  - dragon_master_strategy:       龙头战法
  - lianban_relay_strategy:       连板接力

QMT tick-driven strategies (not bt-based):
  - qmt_lianban_monitor:          连板接力封板确认监控
  - sector_synergy_strategy:      板块协同+大单净额+攻击斜率追涨
"""
