# Quant Trading Quant-Trading - 指标应用矩阵

## 指标速查表

### 按交易风格分类

#### 📈 趋势跟踪（Trend Following）

| 指标              | 周期     | 优势                   | 使用例                             |
| ----------------- | -------- | ---------------------- | ---------------------------------- |
| **SMA**           | 50, 200  | 趋势识别清晰，容易实现 | `dualMaCrossover`                  |
| **EMA**           | 12, 26   | 对近期价格敏感         | `MACD`, `ctaMacdTrend`             |
| **MACD**          | 12,26,9  | 双重确认（线+直方图）  | `macdConfirmation`, `ctaMacdTrend` |
| **Parabolic SAR** | 0.02步长 | 自适应跟踪止损         | `ctaTrendFollowing`, `ctaParabolicSar` |

#### 🔄 均值回归（Mean Reversion）

| 指标                | 周期   | 用途                  | 使用例                                  |
| ------------------- | ------ | --------------------- | --------------------------------------- |
| **Bollinger Bands** | 20, 2σ | 上下轨反向交易        | `meanReversion`, `ctaBollingerBreakout` |
| **RSI**             | 14     | 超买(>70)/超卖(<30)   | `rsiFilter`, `rsiEnhanced`              |
| **Williams %R**     | 14     | 超买(>-20)/超卖(<-80) | `ctaWilliamsR`                          |
| **CCI**             | 20     | 周期顶底识别          | `ctaCciDivergence`                      |
| **Z-Score**         | 20     | 统计极端识别          | `ctaMeanReversionZScore`                |

#### 🎯 突破动量（Breakout Momentum）

| 指标               | 参数 | 特点          | 使用例                                    |
| ------------------ | ---- | ------------- | ----------------------------------------- |
| **rollingHighLow** | 20   | N日最高价突破 | `momentumBreakout`, `ctaDonchianMidReversion` |
| **破裂检测**       | 动态 | Donchian通道  | `ctaTurtleBreakout`, `ctaRangeBreakout`   |
| **ATR**            | 14   | 波动率确认    | `ctaDualThrust`, `ctaAtrTrailing`         |

#### 📊 综合确认（Multi-Signal）

| 指标组合              | 用途           | 策略                                          |
| --------------------- | -------------- | --------------------------------------------- |
| **SMA + RSI**         | 趋势+动量确认  | `rsiEnhanced`, `rsiMacdCombined`              |
| **MACD + BB**         | 趋势+范围确认  | `enhancedCompositeStrategy`, `compositeStrategy` |
| **DMI + ATR**         | 方向+波动度    | `ctaDmiAdx`                                   |
| **Stochastic + RSI**  | 双维度超买超卖 | `ctaStochasticRsi`                            |
| **Multi-MA**          | 多均线收敛     | `ctaMultiMaConvergence`                       |

---

## 🎨 时间框架适用性矩阵

```
             1m   5m  15m  30m   1h   1d   1w   1M
SMA          ✓    ✓    ✓    ✓    ✓    ✓    ✓    ✓
EMA          ✓    ✓    ✓    ✓    ✓    ✓    ✓    ✓
Bollinger    ✓    ✓    ✓    ✓    ✓    ✓    ✓    △
MACD         △    △    ✓    ✓    ✓    ✓    ✓    ✓
RSI          △    △    ✓    ✓    ✓    ✓    ✓    △
ATR          △    △    ✓    ✓    ✓    ✓    ✓    ✓
DMI/ADX      △    △    ✓    ✓    ✓    ✓    ✓    ✓
KDJ          △    △    ✓    ✓    ✓    △    ✗    ✗
Williams%R   ✓    ✓    ✓    ✓    ✓    △    ✗    ✗
Ichimoku     ✗    ✗    △    △    △    ✓    ✓    ✓
CCI          ✓    ✓    ✓    △    △    △    △    ✗
OBV          △    △    ✓    ✓    ✓    ✓    ✓    △
Parabolic    △    △    ✓    ✓    ✓    ✓    ✓    ✓

✓  - 适合      △  - 可用      ✗  - 不推荐
```

---

## 📍 按市场类型应用矩阵

```
             A股  美股  期货  加密  期权
SMA          ✓    ✓    ✓    ✓    △
EMA          ✓    ✓    ✓    ✓    △
Bollinger    ✓    ✓    ✓    ✓    ✓
MACD         ✓    ✓    ✓    ✓    △
RSI          ✓    ✓    ✓    ✓    △
ATR          △    △    ✓✓   ✓    ✓
DMI/ADX      △    △    ✓    △    △
KDJ          ✓    △    △    △    △
Williams%R   △    △    △    △    △
Ichimoku     △    △    △    △    ✗
CCI          △    △    △    △    △
OBV          ✓    ✓    △    △    △
Parabolic    ✓    ✓    ✓✓   ✓    △

✓✓ - 必需      ✓  - 推荐      △  - 可用
```

---

## 🧮 指标计算复杂度

| 指标               | 时间复杂度 | 空间复杂度 | 说明           |
| ------------------ | ---------- | ---------- | -------------- |
| **SMA**            | O(n)       | O(n)       | 滑动窗口       |
| **EMA**            | O(n)       | O(n)       | 递推算法       |
| **Bollinger**      | O(n)       | O(n)       | 基于 SMA+ 方差 |
| **MACD**           | O(n)       | O(n)       | EMA(3次)       |
| **RSI**            | O(n)       | O(n)       | Wilder 平滑    |
| **ATR**            | O(n)       | O(n)       | EMA(TR)        |
| **DMI/ADX**        | O(n)       | O(n)       | Wilder 平滑    |
| **CCI**            | O(n²)     | O(n)       | 需要均值偏差   |
| **OBV**            | O(n)       | O(n)       | 单次遍历       |
| **Williams %R**    | O(n)       | O(n)       | 单调队列       |
| **ROC**            | O(n)       | O(n)       | 单次遍历       |
| **Ichimoku**       | O(n)       | O(n)       | 多个滑动窗口   |
| **Stochastic**     | O(n)       | O(n)       | KDJ 架构       |
| **Parabolic SAR**  | O(n)       | O(n)       | 状态机         |
| **rollingHighLow** | O(n)       | O(n)       | 单调队列       |

---

## 🔗 指标组合推荐

### 头寸入场

#### Trend Following（高概率入场）

```
确认思路: 趋势 + 动量 + 波动度
┌─────────────────────────────┐
│ 1. EMA(26) > EMA(12)       │ ← 大趋势向上
│ 2. MACD 直方图 > 0         │ ← 加速确认
│ 3. ATR 增长 + Vol 增加     │ ← 动量确认
└─────────────────────────────┘
推荐策略: ctaMacdTrend, ctaTrendFollowing
```

#### Mean Reversion（极限反弹）

```
确认思路: 极值 + 均值线接近 + 相反方向
┌─────────────────────────────┐
│ 1. RSI < 30 (或 > 70)      │ ← 极端读数
│ 2. 价格触及 BB 下轨        │ ← 统计极端
│ 3. 收盘价 < SMA(20)        │ ← 下方买入
└─────────────────────────────┘
推荐策略: meanReversion, enhancedCompositeStrategy, ctaMeanReversionZScore
```

#### Breakout（突破突进）

```
确认思路: 高点突破 + 成交量 + 风险限制
┌─────────────────────────────┐
│ 1. Close > HH(20)          │ ← 20日高点
│ 2. Volume > Avg Volume     │ ← 成交量加大
│ 3. ATR 作为止损距离        │ ← N×ATR 止损
└─────────────────────────────┘
推荐策略: momentumBreakout, ctaTurtleBreakout, ctaRangeBreakout
```

### 头寸退出

#### 趋势反转确认

```
SMA 死叉:        快线下穿慢线
MACD 背离:       收盘创新高，MACD 不创新高
Parabolic SAR:  SAR 反转信号
Chandelier:     ctaChandelierExit ATR 跟踪止损
```

#### 极值回收

```
RSI > 75:        超买退出
Price > BB Up:   布林上轨获利
Ichimoku 出云:   跌出云层退出
```

---

## 🎯 策略参数速查

### 基础参数配置

#### 短期交易（Swing, Day Trade）

```typescript
{
  fastMA: 10,          // 快线周期
  slowMA: 30,          // 慢线周期
  rrPeriod: 14,        // RSI 周期
  rrThreshold: 30,     // RSI 阈值
  bbPeriod: 10,        // 布林带周期（短）
  atrPeriod: 10,
  stop: 2.5 * ATR      // 2.5倍 ATR 止损
}
```

#### 中期交易（Swing）

```typescript
{
  fastMA: 12,
  slowMA: 26,
  macdSignal: 9,
  bbPeriod: 20,
  rrPeriod: 14,
  atrPeriod: 14,
  stop: 2.0 * ATR
}
```

#### 长期交易（Position）

```typescript
{
  fastMA: 50,
  slowMA: 200,
  bbPeriod: 30,
  rrPeriod: 14,
  atrPeriod: 21,
  stop: 1.5 * ATR
}
```

#### CTA 专用

```typescript
{
  trendWindow: 20,     // Donchian 通道
  afStart: 0.02,       // SAR 加速因子
  afMax: 0.2,
  volatilityPeriod: 14 // ATR 周期
}
```

---

## 📈 指标热身周期（Warmup Period）

| 指标           | 最少准备 bars | 推荐准备 | 说明                 |
| -------------- | ------------- | -------- | -------------------- |
| **SMA(20)**    | 20            | 30       | period bars          |
| **EMA(12)**    | 12            | 20       | period bars          |
| **RSI(14)**    | 14            | 30       | 需包含 up/down cycle |
| **MACD**       | 26            | 50       | 取决于 slowPeriod    |
| **BB(20, 2σ)** | 20            | 30       | 需要完整周期         |
| **Ichimoku**   | 52            | 100      | senkouBPeriod 最大   |
| **ATR(14)**    | 14            | 20       | 需要 TR 计算         |
| **DMI/ADX**    | 2×14=28       | 50       | ADX 需要二次平滑     |

---

## ⚠️ 常见坑点

### 1. NaN 处理不当

```typescript
// ❌ 错误
const rsiVals = rsi(closes, 14);
if (rsiVals[i] > 70) {
  /* ... */
} // 前 14 个数据是 NaN!

// ✅ 正确
const rsiVals = rsi(closes, 14);
if (!Number.isNaN(rsiVals[i]) && rsiVals[i] > 70) {
  /* ... */
}

// 或使用索引检查
if (i >= 14 && rsiVals[i] > 70) {
  /* ... */
}
```

### 2. T+1 限制

```typescript
// ❌ A股当日不能卖
buy at 10:00 → wanted to sell at 11:00  // 不可行

// ✅ 改为次日卖
buy at day1 EOD → sell at day2 EOD
```

### 3. 参数过优化（Overfitting）

```typescript
// ❌ 极端参数
BB((period = 5), (stdDev = 4)); // 太敏感
RSI((period = 3)); // 噪音太多

// ✅ 经过验证的参数
BB((period = 20), (stdDev = 2)); // 标准
RSI((period = 14)); // Wilder 默认
MACD(12, 26, 9); // 标准配置
```

### 4. 指标延迟（Lagging）

```typescript
// ❌ 使用昨日指标
const i = ctx.index;
const signal = smaValues[i - 1]; // 落后 1 bar!

// ✅ 使用当日指标
const signal = smaValues[i]; // 即时
// 但要知道：指标本身就有延迟（SMA 落后趋势）
```

---

## 🚀 性能优化检查清单

- [ ] 使用 `getCloses()` 缓存收盘价，避免 `bars.map()`
- [ ] 指标预计算在 `init()` 中完成
- [ ] 检查所有指标数组访问时的 NaN 条件
- [ ] 特殊指标（CCI）的 O(n²) 开销是否可接受
- [ ] `rollingHighLow` 等已用单调队列优化
- [ ] 参数热身期是否足够（最少 slowPeriod 或 senkouBPeriod）
- [ ] 是否有重复计算的指标
- [ ] 市场限制（T+1、持仓限制）是否已考虑

---

## 📚 完整策略清单

### 基础策略（9 个） — `basic.ts`

| 策略 ID | 名称 | 核心指标 |
| ------- | ---- | -------- |
| `dualMaCrossover` | 双均线交叉 | SMA |
| `momentumBreakout` | 动量突破 | rollingHighLow |
| `meanReversion` | 均值回归 | BB |
| `rsiFilter` | RSI 过滤 | RSI |
| `rsiEnhanced` | RSI 增强 | RSI + SMA |
| `macdConfirmation` | MACD 确认 | MACD |
| `macdDivergence` | MACD 背离 | MACD |
| `volumeConfirmation` | 量价确认 | OBV + SMA |
| `t0DayTrading` | T+0 日内 | 多指标 |

### 高阶组合策略（8 个） — `advanced.ts`

| 策略 ID | 名称 | 核心特点 |
| ------- | ---- | -------- |
| `compositeStrategy` | 多信号投票 | ≥2/3 共识 |
| `gatedStrategy` | 门控策略 | BB 门 + 趋势确认 |
| `trendComboStrategy` | 趋势组合 | OR 逻辑 |
| `scoredStrategy` | 评分策略 | 加权仓位 |
| `enhancedCompositeStrategy` | 增强组合 | 多门控 + 风控 |
| `rsiMacdCombined` | RSI+MACD | 多条件 |
| `t0EnhancedStrategy` | 增强日内 | T+0 增强 |
| `detectMarketRegime` | 市场状态 | 波动率检测 |

### CTA 趋势策略（8 个） — `cta-trend.ts`

| 策略 ID | 名称 | 核心指标 |
| ------- | ---- | -------- |
| `ctaTrendFollowing` | EMA 带状跟踪 | EMA Ribbon |
| `ctaDualThrust` | Dual Thrust | ATR 突破 |
| `spreadArbitrage` | 套利策略 | 价差 |
| `ctaTurtleBreakout` | 海龟策略 | Donchian |
| `ctaMacdTrend` | MACD 趋势 | MACD |
| `ctaRsiReversal` | RSI 反转 | RSI |
| `ctaBollingerBreakout` | BB 突破 | BB |
| `ctaKeltnerChannel` | Keltner 通道 | EMA + ATR |

### CTA 通道策略（5 个） — `cta-channel.ts`

| 策略 ID | 名称 | 核心指标 |
| ------- | ---- | -------- |
| `ctaRBreaker` | R-Breaker | 支撑阻力 |
| `ctaAberration` | Aberration | BB |
| `ctaTtmSqueeze` | TTM Squeeze | BB + Keltner |
| `ctaDmiAdx` | DMI/ADX | DMI |
| `ctaParabolicSar` | SAR 跟踪 | Parabolic SAR |

### CTA 指标策略（12 个） — `cta-indicator.ts`

| 策略 ID | 名称 | 核心指标 |
| ------- | ---- | -------- |
| `ctaVolatilityTarget` | 波动率目标 | ATR |
| `ctaMomentumRotation` | 动量轮动 | ROC |
| `ctaWilliamsR` | Williams %R | Williams %R |
| `ctaObvTrend` | OBV 趋势 | OBV |
| `ctaIchimoku` | 一目均衡图 | Ichimoku |
| `ctaKdj` | KDJ 随机 | Stochastic |
| `ctaMeanReversionZScore` | Z-Score 回归 | Z-Score |
| `ctaGridTrading` | 网格交易 | 价格网格 |
| `ctaMultiMaConvergence` | 多均线收敛 | Multi-SMA |
| `ctaDonchianMidReversion` | Donchian 中线 | rollingHighLow |
| `ctaPivotPoint` | 枢轴点 | Pivot |
| `ctaChandelierExit` | 吊灯止损 | ATR |

### CTA 形态策略（10 个） — `cta-pattern.ts`

| 策略 ID | 名称 | 核心指标 |
| ------- | ---- | -------- |
| `ctaRangeBreakout` | 日内突破 | 区间突破 |
| `ctaVwapReversion` | VWAP 回归 | VWAP |
| `ctaDoubleBottom` | 双底形态 | 形态识别 |
| `ctaRocMomentum` | ROC 动量 | ROC |
| `ctaCciDivergence` | CCI 背离 | CCI |
| `ctaEmaCrossover` | EMA 交叉 | EMA |
| `ctaAtrTrailing` | ATR 跟踪 | ATR |
| `ctaStochasticRsi` | 随机RSI | Stochastic + RSI |
| `ctaMacdHistogramReversal` | MACD柱反转 | MACD |
| `ctaBollingerBandwidth` | BB带宽挤压 | BB Bandwidth |

---

## 📚 参考代码片段

### 完整的三信号策略

```typescript
export function tripleSignalStrategy(): BuiltinStrategy {
  let closes: number[] = [];
  let smaFast: number[] = [];
  let smaSlow: number[] = [];
  let rsiVals: number[] = [];
  let bbResult: ReturnType<typeof bollingerBands>;

  return {
    name: "TripleSignal (SMA+RSI+BB)",
    init(ctx) {
      closes = getCloses(ctx);
      smaFast = sma(closes, 10);
      smaSlow = sma(closes, 30);
      rsiVals = rsi(closes, 14);
      bbResult = bollingerBands(closes, 20, 2);
    },
    next(ctx) {
      const i = ctx.index;
      if (i < 30) return null; // 热身期

      const bar = ctx.bars[i]!;
      let buyScore = 0;
      let sellScore = 0;

      // 信号 1: SMA 交叉
      if (smaFast[i]! > smaSlow[i]! && smaFast[i - 1]! <= smaSlow[i - 1]!) {
        buyScore++;
      }
      if (smaFast[i]! < smaSlow[i]! && smaFast[i - 1]! >= smaSlow[i - 1]!) {
        sellScore++;
      }

      // 信号 2: RSI 极值
      if (rsiVals[i]! > 70) {
        sellScore++;
      }
      if (rsiVals[i]! < 30) {
        buyScore++;
      }

      // 信号 3: BB 极值
      if (bar.close < bbResult.lower[i]!) {
        buyScore++;
      }
      if (bar.close > bbResult.upper[i]!) {
        sellScore++;
      }

      // 多数表决
      if (buyScore >= 2 && ctx.position <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: `三信号买入 (SMA+RSI+BB): ${buyScore}/3`,
          strategyId: "triple-signal",
          confidence: buyScore / 3,
        };
      }

      if (sellScore >= 2 && ctx.position > 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "sell",
          price: bar.close,
          reason: `三信号卖出 (SMA+RSI+BB): ${sellScore}/3`,
          strategyId: "triple-signal",
          confidence: sellScore / 3,
        };
      }

      return null;
    },
  };
}
```

---

**文档版本**: 2026-04-03
**覆盖指标**: 15 个核心指标
**内置策略**: 52 个（9 基础 + 8 高阶 + 8 CTA趋势 + 5 CTA通道 + 12 CTA指标 + 10 CTA形态）
