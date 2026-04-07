"""
Quantitative trading strategy library — 52+ built-in strategies.

All strategies extend the BaseStrategy class (which extends bt.Strategy)
and are auto-discovered by the strategy registry via *_strategy.py naming.

══════════════════════════════════════════════════════════════════
  Category           ID                    Name
══════════════════════════════════════════════════════════════════

  ── 基础 / CTA 趋势 (Trend Following) ─────────────────────────
  trend               macd                  MACD 振荡器
  trend               dual-ma               双均线交叉
  trend               ema-triple            三重EMA交叉
  trend               adx-trend             ADX 趋势强度
  trend               supertrend            超级趋势
  trend               ichimoku              一目均衡表
  trend               parabolic-sar         抛物线SAR
  trend               dmi                   方向运动DMI
  trend               aroon                 阿隆指标
  trend               turtle                海龟交易法
  trend               trix                  三重指数TRIX
  trend               adaptive-ma           自适应均线KAMA

  ── 通道 (Channel) ─────────────────────────────────────────────
  channel             donchian              唐奇安通道
  channel             keltner               肯特纳通道
  channel             price-channel         价格通道突破
  channel             envelope              均线包络线
  channel             atr-channel           ATR波动通道

  ── 指标 / 振荡器 (Oscillator / Indicator) ─────────────────────
  oscillator          rsi                   RSI 超买超卖
  oscillator          kdj                   KDJ 随机指标
  oscillator          bollinger             布林带均值回归
  oscillator          mean-reversion-cn     A股均值回归 (BIAS)
  oscillator          williams-r            威廉指标
  oscillator          cci                   顺势指标 CCI
  oscillator          obv                   能量潮 OBV
  oscillator          stoch-rsi             随机RSI
  oscillator          mfi                   资金流量 MFI
  oscillator          vwap                  成交量加权均价
  oscillator          dpo                   去趋势震荡 DPO
  oscillator          roc                   变动率 ROC
  oscillator          ultimate-oscillator   终极震荡指标
  oscillator          chaikin               蔡金资金流
  oscillator          fisher-transform      费雪变换

  ── 形态 (Pattern) ─────────────────────────────────────────────
  pattern             engulfing             吞没形态
  pattern             doji-star             十字星反转
  pattern             hammer                锤子线/上吊线
  pattern             double-bottom         双底双顶
  pattern             head-shoulders        头肩形态
  pattern             gap                   跳空缺口
  pattern             three-soldiers        三兵三鸦
  pattern             breakout-volume       放量突破

  ── 高阶 / 复合 (Composite / Advanced) ─────────────────────────
  composite           multi-factor          多因子选股
  composite           momentum-rotation     动量轮动
  composite           volatility-breakout   波动率突破
  composite           swing-trading         波段交易
  composite           ma-ribbon             均线带
  composite           elder-triple          三重滤网
  composite           heikin-ashi           平均K线
  composite           auction               竞价多条件退出
  composite           dragon-return         龙回头（龙头回调）
  composite           dragon-master         龙头猎手

  ── 套利 / 区间 (Arbitrage / Range) ────────────────────────────
  arbitrage           pair-trading          配对交易 / 统计套利
  range               grid                  网格交易

  ── QMT tick-driven (non-bt) ──────────────────────────────────
  -                   qmt_lianban_monitor   连板接力封板确认监控
  -                   sector_synergy        板块协同追涨
  -                   lianban_relay         连板接力

══════════════════════════════════════════════════════════════════
Total: 52 auto-discovered bt-based strategies + QMT strategies
"""
