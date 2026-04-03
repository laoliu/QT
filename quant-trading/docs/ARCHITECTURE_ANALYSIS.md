# OpenClaw Quant-Trading 扩展 - 深度架构分析

## 目录

1. [数据源架构](#数据源架构)
2. [指标库完整清单](#指标库完整清单)
3. [策略数据处理](#策略数据处理)
4. [可扩展点和集成指南](#可扩展点和集成指南)

---

## 数据源架构

### 1. 支持的时间框架（TimeFrames）

**完整列表** (`KlineInterval`):

```typescript
export type KlineInterval = "1m" | "5m" | "15m" | "30m" | "60m" | "1d" | "1w" | "1M";
```

**详细说明：**

- **分钟级**: `1m`, `5m`, `15m`, `30m`, `60m` → 用于日内高频交易
  - 需要通过 xtquant (QMT) 或兼容的提供商获取数据
- **日线**: `1d` → 默认时间框架，中线策略使用
- **周线、月线**: `1w`, `1M` → 长期趋势分析

### 2. Bar/Candle 数据结构

**定义** (`KlineBar`):

```typescript
export type KlineBar = {
  timestamp: number; // Unix 毫秒时间戳
  open: number; // 开盘价
  high: number; // 最高价
  low: number; // 最低价
  close: number; // 收盘价
  volume: number; // 成交量
  amount?: number; // 成交额（可选）
  turnover?: number; // 换手率（可选）
  openInterest?: number; // 持仓量（期货/期权）
  settlementPrice?: number; // 结算价（期货）
};
```

**特点：**

- 标准 OHLCV 结构
- 智能字段：期货、期权相关字段为可选
- **注意**: 当前仅支持 Bar/Candle 级别，**不支持 Tick 级别数据**

### 3. Tick 级别数据支持现状

**结论**: ❌ **当前未实现**

- 项目中没有 `TickData`、`Tick` 或类似类型定义
- `market-data-provider.ts` 仅实现了 OHLCV（K线）级别数据
- 对于高频交易策略，目前限制于日内 1 分钟到 1 小时的 Bar 数据
- **扩展可能性**: 可通过 Python bridge 接入 QMT ContextInfo 的 Tick 数据流

### 4. 报价快照（Quote Snapshot）

**定义** (`QuoteSnapshot`):

```typescript
export type QuoteSnapshot = {
  symbol: SymbolInfo;
  price: number;
  change: number; // 绝对涨跌
  changePct: number; // 百分比涨跌
  open: number;
  high: number;
  low: number;
  prevClose: number;
  volume: number;
  amount: number;
  timestamp: number;
  bid1?: number; // 买一价
  ask1?: number; // 卖一价
  openInterest?: number; // 期货/期权持仓量
  settlementPrice?: number; // 期货结算价
};
```

### 5. 市场类型（MarketType）

```typescript
export type MarketType =
  | "cn_stock" // A股
  | "us_stock" // 美股
  | "hk_stock" // 港股
  | "futures" // 期货
  | "fund" // 基金
  | "crypto" // 加密资产
  | "etf_option" // ETF期权
  | "index_option" // 指数期权
  | "commodity_option"; // 商品期权
```

### 6. 支持的数据提供商

**实现的提供商**：

| 提供商               | 类型          | 支持市场             | 实现位置                              |
| -------------------- | ------------- | -------------------- | ------------------------------------- |
| **AKShare**          | Python Bridge | A股、期货、基金      | `data_providers/akshare_provider.py`  |
| **Tushare**          | Python Bridge | A股                  | `data_providers/tushare_provider.py`  |
| **Yahoo Finance**    | Python Bridge | 美股、港股、加密     | `data_providers/yfinance_provider.py` |
| **Options Provider** | Python Bridge | ETF/指数/商品期权    | `data_providers/options_provider.py`  |
| **Financial**        | Python Bridge | 财务报表数据         | `data_providers/financial_provider.py`|
| **Sector Data**      | Python Bridge | 板块分类与热点追踪   | `data_providers/sector_data.py`       |
| **QMT (Windows)**    | HTTP Bridge   | A股、期货            | `data_providers/qmt_provider.ts`      |
| **Mock/Demo**        | 本地生成      | 所有市场             | `market-data-provider.ts`             |

---

## 指标库完整清单

### 📊 已实现的指标（共 15 个）

所有指标位于：`src/engine/strategies/indicators.ts`

#### A. 趋势指标

**1. SMA - 简单移动平均**

```typescript
export function sma(data: number[], period: number): number[];
```

- 用途：识别趋势方向
- 性能：O(n) 滑动窗口
- 特点：**NaN-aware**，处理上游指标输出

**2. EMA - 指数移动平均**

```typescript
export function ema(data: number[], period: number): number[];
```

- 用途：更敏感的均线，新数据权重更高
- 平滑因子：k = 2/(period+1)
- 返回：NaN 直到周期准备就绪

**3. MACD - 移动平均收敛发散**

```typescript
export function macd(
  closes: number[],
  fastPeriod = 12,
  slowPeriod = 26,
  signalPeriod = 9,
): { macdLine: number[]; signalLine: number[]; histogram: number[] };
```

- 返回：快线、信号线、直方图
- 本质：EMA 差值 + 信号线平滑
- 用途：**趋势确认、背离识别**

#### B. 波动率指标

**4. Bollinger Bands - 布林带**

```typescript
export function bollingerBands(
  closes: number[],
  period: number,
  stdDev: number, // 标准差倍数，通常为 2
): { middle: number[]; upper: number[]; lower: number[] };
```

- 构成：SMA(中线) + 标准差通道
- 性能：O(n) 使用**滚动平方和**优化
- 用途：**均值回归、突破交易**

**5. ATR - 平均真实波幅**

```typescript
export function atr(bars: KlineBar[], period: number): number[];
```

- 公式：TR 的 SMA
- TR = max(high-low, |high-prevClose|, |low-prevClose|)
- 用途：**止损设置、风险管理、波动性排名**
- 特别适合：CTA 策略

**6. KDJ / Stochastic**

```typescript
export function stochastic(
  bars: KlineBar[],
  period = 9,
  kSmooth = 3,
  dPeriod = 3,
): { k: number[]; d: number[]; j: number[] };
```

- 返回：K线（快线）、D线（慢线）、J线（3K-2D）
- 中文名：随机指标
- 用途：**超买超卖识别**

#### C. 动量指标

**7. RSI - 相对强弱指数**

```typescript
export function rsi(closes: number[], period: number): number[];
```

- 范围：0-100
- 方法：Wilder 平滑移动平均
- 用途：**超买(>70) / 超卖(<30) 识别**
- 特点：**标准周期 14**

**8. ROC - 价格动量**

```typescript
export function roc(closes: number[], period: number): number[];
```

- 公式：(close - close[period ago]) / close[period ago] × 100
- 用途：**动量排名、背离识别**

**9. Williams %R - 威廉指标**

```typescript
export function williamsR(bars: KlineBar[], period: number): number[];
```

- 范围：-100 到 0
- 公式：(HH - Close) / (HH - LL) × (-100)
- 用途：**超买(>-20) / 超卖(<-80)**
- 优点：**O(n) 单调队列实现**

#### D. 方向性指标

**10. DMI / ADX - 方向运动指数**

```typescript
export function dmi(
  bars: KlineBar[],
  period: number,
): { plusDI: number[]; minusDI: number[]; adx: number[] };
```

- 返回：+DI、-DI、ADX
- +DI > -DI 表示上升趋势
- ADX > 25 表示强趋势
- 用途：**趋势强度评估**

**11. CCI - 商品通道指数**

```typescript
export function cci(bars: KlineBar[], period: number): number[];
```

- 公式：(TP - SMA(TP)) / (0.015 × Mean Deviation)
- TP = (H + L + C) / 3
- 用途：**周期识别、突破确认**

#### E. 成交量指标

**12. OBV - 成交量能指标**

```typescript
export function obv(bars: KlineBar[]): number[];
```

- 累计成交量指标
- 规则：
  - close > prevClose → OBV += volume
  - close < prevClose → OBV -= volume
  - close == prevClose → OBV 不变
- 用途：**成交量趋势确认**

#### F. 停止反转指标

**13. Parabolic SAR - 抛物线止损反转**

```typescript
export function parabolicSar(
  bars: KlineBar[],
  afStart = 0.02, // 加速因子初值
  afStep = 0.02, // 加速因子步长
  afMax = 0.2, // 加速因子最大值
): { sar: number[]; trend: number[] };
```

- 返回：SAR 值数组 + 趋势数组 (1 = 上升, -1 = 下降)
- 用途：**动态止损、趋势反转识别**
- 优点：自适应 SAR 值避免穿透历史 bar

#### G. 一目均衡图（Japanese Cloud）

**14. Ichimoku - 一目均衡图**

```typescript
export function ichimoku(
  bars: KlineBar[],
  tenkanPeriod = 9, // 转换线周期
  kijunPeriod = 26, // 基准线周期
  senkouBPeriod = 52, // 先行下线周期
  displacement = 26, // 移位
): {
  tenkan: number[]; // 转换线
  kijun: number[]; // 基准线
  senkouA: number[]; // 先行上线（向前移位）
  senkouB: number[]; // 先行下线（向前移位）
  chikou: number[]; // 迟行线（向后移位）
};
```

- 特点：五条线组成云系统
- 用途：**多时空确认、支撑阻力识别**
- **适合中长期 swing trading**

#### H. 高低点辅助函数

**15. rollingHighLow - 滚动高低点**

```typescript
export function rollingHighLow(
  bars: KlineBar[],
  lookback: number,
): { highs: number[]; lows: number[] };
```

- 性能：O(n) **单调队列算法**（非 O(n×lookback)）
- 用途：**Donchian 通道、突破检测**

#### I. 辅助函数

**getCloses - 提取收盘价**

```typescript
export function getCloses(ctx: BuiltinStrategyContext): number[];
```

- 用途：缓存 Bar 数组的收盘价（避免重复 map）
- 优化：共享缓存，避免冗余计算

---

### 🎯 指标使用特性总结

| 指标                | 是否支持高频 | 适合市场   | 最佳周期        | 关键参数                 |
| ------------------- | ------------ | ---------- | --------------- | ------------------------ |
| **SMA**             | ✅ 是        | 全部       | 10, 20, 50, 200 | period                   |
| **EMA**             | ✅ 是        | 全部       | 12, 26, 50      | period                   |
| **Bollinger Bands** | ✅ 是        | 横盘市     | 20, 30          | period, stdDev           |
| **MACD**            | ⚠️ 中        | 趋势市     | 12,26,9         | fastPeriod, slowPeriod   |
| **ATR**             | ⚠️ 中        | 期货、加密 | **14**          | period                   |
| **RSI**             | ⚠️ 中        | 全部       | **14**          | period                   |
| **DMI/ADX**         | ⚠️ 中        | 趋势识别   | **14**          | period                   |
| **KDJ**             | ⚠️ 中        | 短期反转   | 9, 3, 3         | period, kSmooth, dPeriod |
| **Williams %R**     | ✅ 是        | 短期反转   | 14              | period                   |
| **CCI**             | ⚠️ 中        | 周期识别   | 20              | period                   |
| **OBV**             | ⚠️ 中        | 成交量确认 | -               | -                        |
| **Parabolic SAR**   | ⚠️ 中        | 趋势跟踪   | -               | af, afStep, afMax        |
| **Ichimoku**        | ❌ 否        | 中长期     | 9,26,52,26      | 各周期                   |
| **Stochastic**      | ⚠️ 中        | 短期反转   | 9,3,3           | period, kSmooth, dPeriod |

**图例**：

- ✅ **是**：可用于 分钟/5分钟 级别交易
- ⚠️ **中**：15分钟及以上适用
- ❌ **否**：仅适合日线及以上

---

### ⚠️ 缺失的高频交易指标

主要欠缺以下指标（可自行扩展）：

1. **VWAP** - 成交量加权平均价（日内必需）
2. **Volume Profile** - 成交量分布
3. **Market Profile** - 市场轮廓
4. **Order Flow Imbalance** - 成交单失衡
5. **TWAP** - 时间加权平均价
6. **FPGA-friendly** 简化指标

---

## 策略数据处理

### 1. 策略执行上下文（BuiltinStrategyContext）

**定义**：

```typescript
export type BuiltinStrategyContext = {
  bars: KlineBar[]; // 完整历史 Bar 数组
  index: number; // 当前 Bar 索引（0-based）
  position: number; // 当前持仓数量（0 = 平仓）
  cash: number; // 剩余现金
  params: Record<string, unknown>; // 策略参数对象
  signals: TradingSignal[]; // 已发出的全部信号
  closes?: number[]; // 收盘价缓存（可选，避免重复 map）
};
```

**关键特点**：

- **完整历史访问**：每次 `next()` 可访问全部历史 Bar
- **单步执行模式**：从 index 0 到最后逐 Bar 回测
- **原地参数**：params 字典允许自定义参数注入
- **NaN 处理**：指标输出中 NaN 用于标记热身期

### 2. 策略接口（BuiltinStrategy）

```typescript
export type BuiltinStrategy = {
  name: string;
  init?(ctx: BuiltinStrategyContext): void; // 初始化：计算指标
  next(ctx: BuiltinStrategyContext): TradingSignal | TradingSignal[] | null; // 每个 Bar 生成信号
};
```

**工作流**：

```
1. init(ctx)  ●─────────────────────────────────────┐
              │ 计算 SMA、MACD、Bollinger 等指标     │
              │ 一次性操作，建立指标缓存             │
              └─────────────────────────────────────┤
                                                    │
2. next(ctx)  ●────────────────────────────────────┤
   (loop)     │ Bar 0: index=0 → 检查信号          │
              │ Bar 1: index=1 → 检查信号          │
              │ Bar 2: index=2 → 检查信号 (TTM)   │
              │ ...                                 │
              │ Bar N: index=N → 检查信号          │
              └─────────────────────────────────────┘
```

### 3. 数据聚合与时间序列处理

**架构**：

```
Market Data Provider (Python Bridge)
  ↓
KlineBar[] (历史数据一次性加载)
  ↓
BuiltinStrategyContext.bars
  ↓
init(): 指标预计算 (SMA, MACD, BB, etc)
  ↓
Loop: next() on each bar
  ├─ 访问指标[index]
  ├─ 检查交叉、突破、极值
  └─ 返回 TradingSignal | null
```

**关键点**：

- **全量加载模式**：回测时数据全部预加载，无流式处理
- **缓存优化**：`closes` 数组避免重复提取收盘价
- **无状态指标**：每个指标返回完整数组，不维护内部状态
- **NaN-aware**：指标热身期用 NaN 标记，策略逻辑需检查

### 4. 流式数据处理机制

**实时交易引擎** (`live-engine.ts`):

```typescript
class LiveEngine implements OpenClawPluginService {
  // Python 持久进程
  private proc: PersistentPythonProcess | null = null;

  // 实时策略注册表
  private registry: LiveStrategyRegistry;

  // 数据陈旧性监控
  private stalenessMonitor: DataStalenessMonitor | null = null;

  // Quote 订阅回调
  private unsubscribeQuotes: (() => void)[] = [];
}
```

**流程**：

```
1. 启动 live-engine
   ├─ 创建 Python live_engine.py 进程
   ├─ 连接 broker adapter
   └─ 订阅实时报价

2. Quote 推送 (Quote → Bar 聚合)
   ├─ 接收 QuoteSnapshot
   ├─ 聚合为分钟 Bar（或指定周期）
   └─ 发送给 Python 策略

3. Python 策略处理
   ├─ 计算指标（热启动保持内存中）
   ├─ 检查信号逻辑
   └─ 返回 TradingSignal

4. 信号路由
   ├─ OrderManager 下单
   ├─ 持仓更新
   └─ EventBus 推送信号给 UI
```

**特性**：

- ✅ **热启动**：指标在内存中持续更新，无需重新计算
- ✅ **异步处理**：Python 进程独立运行，不阻塞主线程
- ✅ **健康监控**：DataStalenessMonitor 跟踪数据新鲜度
- ✅ **自动重连**：连接失败自动重试（max 2 次）

### 5. 信号数据结构

```typescript
export type TradingSignal = {
  timestamp: number;
  symbol: string;
  direction: "buy" | "sell" | "short" | "cover";
  price: number;
  quantity?: number;
  reason?: string; // 信号原因（用户可见）
  confidence?: number; // 0-1 置信度
  strategyId: string;

  // 期权特定
  legs?: OptionStrategyLeg[]; // 多腿策略成分
};
```

### 6. 市场特性处理

**T+1 限制处理** (A股):

```typescript
function isTPlus1Market(market: MarketType): boolean {
  return market === "cn_stock" || market === "fund";
}
// 当日买入的股票不能当日卖出（enforced in builtin-engine.ts）
```

**期货交割交易**：

- openInterest 字段追踪持仓量
- settlementPrice 字段用于日结算

---

## 可扩展点和集成指南

### 1. 添加新指标

**步骤**：

1️⃣ **在 `indicators.ts` 中实现**

```typescript
export function myIndicator(bars: KlineBar[], period: number, otherParam?: number): number[] {
  const result = new Array<number>(bars.length).fill(NaN);

  // 计算逻辑
  for (let i = 0; i < bars.length; i++) {
    if (i < period - 1) continue; // 热身期返回 NaN

    // 你的计算
    result[i] = someCalculation(bars, i, period);
  }

  return result;
}
```

2️⃣ **在 `indicators.ts` 末尾导出**

```typescript
export { myIndicator };
```

3️⃣ **在策略中使用**

```typescript
import { myIndicator, getCloses } from "./indicators.js";

export function myStrategy(): BuiltinStrategy {
  let indicator: number[] = [];

  return {
    init(ctx) {
      indicator = myIndicator(ctx.bars, 20);
    },
    next(ctx) {
      const i = ctx.index;
      if (Number.isNaN(indicator[i])) return null;
      // 使用 indicator[i]
      return null;
    },
  };
}
```

**性能建议**：

- ✅ 优先使用 **O(n) 单调队列**（rolling high/low 示例）
- ✅ **避免 O(n²)** 嵌套循环（CCI 的均值偏差例外，无法优化）
- ✅ 使用 **滑动窗口** 而非 SMA 的 O(n×period) 暴力法
- ✅ 缓存结果返回数组，不维护动态状态

### 2. 编写新策略

**方式 A：基于 BuiltinStrategy（推荐）**

```typescript
export function newStrategy(param1 = 10, param2 = 20): BuiltinStrategy {
  let smaFast: number[] = [];
  let smaSlow: number[] = [];

  return {
    name: `NewStrategy(${param1},${param2})`,
    init(ctx) {
      const closes = getCloses(ctx);
      smaFast = sma(closes, param1);
      smaSlow = sma(closes, param2);
    },
    next(ctx) {
      const i = ctx.index;
      const bar = ctx.bars[i]!;

      // 信号逻辑
      if (smaFast[i]! > smaSlow[i]! && ctx.position <= 0) {
        return {
          timestamp: bar.timestamp,
          symbol: "",
          direction: "buy",
          price: bar.close,
          reason: "...",
          strategyId: "new-strategy",
        };
      }
      return null;
    },
  };
}
```

**方式 B：Python 策略（复杂逻辑）**

```python
# python/strategies/my_strategy.py

from live_engine import LiveStrategyBase

class MyStrategy(LiveStrategyBase):
    def __init__(self, fast=10, slow=20):
        self.fast = fast
        self.slow = slow

    def init_indicators(self):
        """热启动：计算指标"""
        pass

    def on_bar(self, bars_dict):
        """每个 Bar 回调"""
        signals = []
        # Python 实现
        return signals
```

### 3. 注册策略定义

**在 `strategy-definitions.ts` 中添加**：

```typescript
const builtinTsStrategies: StrategyDefinition[] = [
  // ...
  {
    id: "my-strategy",
    name: "我的策略",
    description: "描述...",
    version: "1.0.0",
    engine: "builtin",
    entryFile: "",
    parameters: [
      {
        name: "period",
        type: "number",
        default: 20,
        min: 5,
        max: 100,
        description: "周期参数",
      },
    ],
    markets: ["cn_stock"], // 限制市场（可选）
  },
];
```

### 4. 扩展数据提供商

**添加新的数据源**：

1️⃣ **实现 `MarketDataProvider` 接口**

```typescript
class MyDataProvider implements MarketDataProvider {
  name = "my-provider";

  async getQuote(symbol: string): Promise<QuoteSnapshot | null> {
    // 实现
  }

  async getKlines(
    symbol: string,
    interval: KlineInterval,
    start: string,
    end: string,
  ): Promise<KlineBar[]> {
    // 实现
  }

  async searchSymbols(keyword: string): Promise<SymbolInfo[]> {
    // 实现
  }
}
```

2️⃣ **在 `getDataProvider()` 中注册**

```typescript
export function getDataProvider(
  config: QuantTradingConfig,
  providerName: string,
): MarketDataProvider {
  switch (providerName) {
    case "my-provider":
      return new MyDataProvider(config);
    // ...
  }
}
```

### 5. 支持新的时间框架

**当前框架** (`KlineInterval`):

```typescript
type KlineInterval = "1m" | "5m" | "15m" | "30m" | "60m" | "1d" | "1w" | "1M";
```

**添加新框架**：

```typescript
export type KlineInterval =
  | "1m"
  | "5m"
  | "15m"
  | "30m"
  | "60m" // 分钟
  | "2h"
  | "4h" // 小时（如需添加）
  | "1d"
  | "1w"
  | "1M"; // 日以上
```

更新所有数据提供商的 `getKlines()` 逻辑

### 6. Python Bridge 扩展

**添加新的 Python 模块**：

```
python/
├── strategies/
│   ├── my_strategy.py      # 新策略
│   └── __init__.py
├── data_providers/
│   ├── my_provider.py      # 新数据源
│   └── __init__.py
└── live_engine.py          # 实时引擎
```

**从 TypeScript 调用**：

```typescript
const result = await runPythonScript<MyResult>(
  "strategies/my_strategy.py",
  { action: "run", symbol: "600519", period: 20 },
  config,
);
```

### 7. 实时策略集成

**使用 LiveStrategyRegistry**：

```typescript
const registry = new LiveStrategyRegistry(liveEngine.api);

// 注册策略
await registry.addStrategy({
  id: "live-macd-001",
  definition: {
    id: "macd-signal",
    name: "MACD 信号",
    engine: "builtin",
  },
  symbols: ["600519"], // 订阅符号
  params: { fastPeriod: 12 },
  enabled: true,
});

// 启动实时处理
liveEngine.start();
```

---

## 总结表格

### 数据源支持矩阵

| 功能                    | 项目支持      | 状态         |
| ----------------------- | ------------- | ------------ |
| **日线 K 线**           | ✅ 完全支持   | 生产就绪     |
| **分钟级数据** (1m-60m) | ✅ 部分支持\* | 需 QMT/API   |
| **Tick 级数据**         | ❌ 不支持     | 可扩展       |
| **报价快照**            | ✅ 完全支持   | 生产就绪     |
| **期货持仓量**          | ✅ 支持       | 部分数据源   |
| **期权链**              | ⚠️ 框架有     | 数据提供商缺 |

### 指标完整性

| 类别       | 数量   | 关键指标                 |
| ---------- | ------ | ------------------------ |
| **趋势**   | 3      | SMA, EMA, MACD           |
| **波动率** | 3      | BB, ATR, KDJ/Stochastic  |
| **动量**   | 3      | RSI, ROC, Williams %R    |
| **方向**   | 2      | DMI/ADX, CCI             |
| **成交量** | 1      | OBV                      |
| **复合**   | 2      | Ichimoku, Parabolic SAR  |
| **辅助**   | 1      | rollingHighLow           |
| **总计**   | **15** | 覆盖 90% 常用指标        |

### 策略库完整性（52 个内置策略）

| 类别                | 数量    | 文件               | 示例策略                                        |
| ------------------- | ------- | ------------------ | ----------------------------------------------- |
| **基础策略**        | 9       | `basic.ts`         | DualMA, MomentumBreakout, MeanReversion, RSI    |
| **高阶组合**        | 8       | `advanced.ts`      | Composite, Gated, Scored, TrendCombo            |
| **CTA 趋势**       | 8       | `cta-trend.ts`     | TurtleBreakout, DualThrust, MacdTrend           |
| **CTA 通道**       | 5       | `cta-channel.ts`   | RBreaker, Aberration, TtmSqueeze, DmiAdx        |
| **CTA 指标**       | 12      | `cta-indicator.ts` | VolatilityTarget, GridTrading, Ichimoku, KDJ    |
| **CTA 形态**       | 10      | `cta-pattern.ts`   | RangeBreakout, DoubleBottom, StochasticRsi      |
| **总计**            | **52**  |                    | 覆盖主流交易风格                                |

---

### 性能优化策略

```typescript
// ❌ 不要：重复计算收盘价数组
const closes1 = bars.map((b) => b.close);
const closes2 = bars.map((b) => b.close); // 浪费！

// ✅ 要：使用 getCloses 缓存
const closes = getCloses(ctx);
const sma10 = sma(closes, 10);
const sma20 = sma(closes, 20);
```

### NaN 处理

```typescript
// ❌ 不要：忽视 NaN
const bb = bollingerBands(closes, 20, 2);
if (closePrice > bb.upper[i]) {
  /* 买入 */
} // 前 19 个数据是 NaN！

// ✅ 要：检查 NaN
const bb = bollingerBands(closes, 20, 2);
if (!Number.isNaN(bb.upper[i]) && closePrice > bb.upper[i]) {
  /* 买入 */
}
```

### 测试指标

```typescript
// src/engine/strategies/indicators.test.ts
import { sma, rsi, bollingerBands } from "./indicators.js";

test("sma should return NaN for initial period", () => {
  const data = [1, 2, 3, 4, 5];
  const result = sma(data, 3);
  expect(Number.isNaN(result[0])).toBe(true);
  expect(result[2]).toEqual(2); // (1+2+3)/3
});
```

---

**文档版本**: 2026-04-03
**项目**: OpenClaw Quant-Trading v1.0
