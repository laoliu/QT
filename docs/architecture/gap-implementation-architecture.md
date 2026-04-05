# 股票交易系统 — 缺失情景架构设计文档

> 项目：1999_ndhy/QT（量化交易 OpenClaw 插件）
> 版本：v1.0
> 日期：2026-04-05
> 关联需求文档：`docs/requirements/requirements.md`

---

## 〇、使用情景覆盖总结

### 已满足（20 项）

S1-S20：实时行情、基本面分析、多因子选股、策略回测、模拟交易、QMT实盘、风控、条件单、定投、期权、板块分析、连板分析、主力资金、消息面事件、价格预警、交易日志、定时报告、组合管理、Web仪表盘、自选股管理。

### 本期待实现（7 项，P0 + P1）

| ID | 情景 | 优先级 | 对应需求 |
|----|------|--------|---------|
| G1 | 多账户管理 | P0 | FR-1 |
| G8 | 批量下单/篮子交易 | P0 | FR-3 |
| G2 | 富途券商实盘 | P0 | FR-2 |
| G3 | 交易绩效归因报告 | P1 | FR-4 |
| G4 | 资金曲线交互式分析 | P1 | FR-5 |
| G11 | 数据导出 | P1 | FR-7 |
| G12 | 策略实盘vs回测对比 | P1 | FR-8 |

---

## 一、模块边界与职责划分

### 1.1 新增模块总览

```
quant-trading/src/
├── account/                         # [新增] 多账户管理层
│   ├── account-manager.ts           # 账户注册/切换/聚合
│   ├── account-context.ts           # 当前账户上下文（线程安全）
│   └── account-store.ts             # 账户配置持久化（加密凭证）
│
├── broker/
│   └── futu-broker.ts               # [新增] 富途 OpenD 适配器
│
├── services/
│   ├── basket-order.ts              # [新增] 篮子交易服务
│   ├── performance-report.ts        # [新增] 绩效报告生成器
│   ├── data-export.ts               # [新增] 数据导出服务
│   └── live-backtest-compare.ts     # [新增] 实盘vs回测对比
│
├── http/
│   ├── account-routes.ts            # [新增] 多账户 API
│   ├── basket-routes.ts             # [新增] 篮子交易 API
│   ├── report-routes.ts             # [新增] 绩效报告 API
│   ├── export-routes.ts             # [新增] 导出 API
│   └── equity-chart-page.ts         # [新增] 交互式资金曲线页面
│
├── tools/
│   ├── account-switch.ts            # [新增] AI 工具：切换账户
│   ├── basket-order.ts              # [新增] AI 工具：篮子下单
│   ├── performance-report.ts        # [新增] AI 工具：绩效报告
│   └── data-export.ts               # [新增] AI 工具：数据导出
│
└── commands/
    └── basket.ts                    # [新增] /basket 聊天命令
```

### 1.2 各模块职责

#### AccountManager（多账户管理）
- **职责**：管理多个交易账户的生命周期（注册、连接、断开、切换）
- **边界**：不参与具体下单逻辑，仅提供当前活跃账户的 Broker 实例
- **核心原则**：账户间数据完全隔离（持仓、订单、资金、风控参数）

#### AccountContext（账户上下文）
- **职责**：维护"当前操作账户"状态，所有下游模块通过此获取活跃 Broker
- **边界**：仅做路由分发，不持有业务逻辑

#### FutuBrokerAdapter（富途券商）
- **职责**：实现 `IBrokerAdapter`，通过 Futu OpenD 网关完成下单/查询/订阅
- **边界**：仅负责协议转换，不包含风控逻辑（风控由 RiskGate 统一处理）

#### BasketOrderService（篮子交易）
- **职责**：将多标的订单拆分为独立子单，逐一过风控，汇总执行状态
- **边界**：依赖 OrderManager 提交子单，不直接操作 Broker

#### PerformanceReportService（绩效报告）
- **职责**：聚合交易记录、持仓快照、基准数据，生成结构化报告（HTML）
- **边界**：只读模块，不修改任何交易数据

#### DataExportService（数据导出）
- **职责**：将持仓/订单/回测/选股等数据序列化为 CSV/Excel
- **边界**：纯数据转换层，不含业务逻辑

#### LiveBacktestCompareService（实盘vs回测对比）
- **职责**：对齐同策略的实盘执行记录与回测结果，计算偏离度
- **边界**：只读分析模块

---

## 二、接口设计

### 2.1 多账户管理接口

```typescript
// account/account-manager.ts

interface AccountConfig {
  accountId: string;               // 唯一标识 "paper-default" | "futu-main" | "qmt-001"
  label: string;                   // 显示名称 "模拟账户" | "富途港股" | "QMT实盘"
  broker: BrokerType;              // "paper" | "futu" | "qmt" | "remote-qmt" | "ib"
  market: MarketType[];            // 支持的市场
  mode: "paper" | "live";
  riskLimits?: RiskLimitsConfig;   // 账户级风控参数（覆盖全局默认值）
  positionSizing?: PositionSizingConfig;
  brokerConfig: FutuBrokerConfig | QMTBrokerConfig | IBBrokerConfig | {};
}

interface AccountState {
  accountId: string;
  connected: boolean;
  lastSyncAt?: string;
  account?: BrokerAccount;         // 最新资金快照
  positionCount: number;
  pendingOrderCount: number;
}

interface AggregateView {
  totalAssets: number;
  totalCash: number;
  totalMarketValue: number;
  totalProfit: number;
  accounts: AccountState[];
}

class AccountManager {
  // 生命周期
  register(config: AccountConfig): void;
  unregister(accountId: string): void;
  connect(accountId: string): Promise<void>;
  disconnect(accountId: string): Promise<void>;
  connectAll(): Promise<void>;

  // 查询
  listAccounts(): AccountConfig[];
  getState(accountId: string): AccountState;
  getAggregate(): Promise<AggregateView>;

  // 获取活跃Broker
  getBroker(accountId: string): IBrokerAdapter;
  getOrderManager(accountId: string): OrderManager;
  getRiskGate(accountId: string): RiskGate;
}
```

```typescript
// account/account-context.ts

class AccountContext {
  // 当前活跃账户（所有 AI 工具/命令/API 默认操作此账户）
  get current(): string;           // accountId
  switch(accountId: string, confirm?: boolean): Promise<void>;

  // 便捷访问（代理到 AccountManager）
  get broker(): IBrokerAdapter;
  get orderManager(): OrderManager;
  get riskGate(): RiskGate;
}
```

### 2.2 富途券商适配器接口

```typescript
// broker/futu-broker.ts

class FutuBrokerAdapter implements IBrokerAdapter {
  brokerType: "futu";
  connected: boolean;

  constructor(config: FutuBrokerConfig);

  // IBrokerAdapter 完整实现
  connect(): Promise<void>;           // 连接 OpenD + 解锁交易
  disconnect(): Promise<void>;
  placeOrder(req: OrderRequest): Promise<Order>;
  cancelOrder(orderId: string): Promise<void>;
  modifyOrder(orderId: string, update: ModifyOrderRequest): Promise<void>;
  queryOrder(orderId: string): Promise<Order>;
  queryOrders(filter?: OrderFilter): Promise<Order[]>;
  queryPositions(): Promise<LivePosition[]>;
  queryAccount(): Promise<BrokerAccount>;
  subscribeQuote(symbols: string[], cb: QuoteCallback): () => void;
  subscribeFills(cb: FillCallback): () => void;
  subscribeOrderStatus(cb: OrderStatusCallback): () => void;
  isMarketOpen(market?: MarketType): boolean;
  ping(): Promise<boolean>;

  // 内部：自动重连机制
  private reconnect(): Promise<void>;    // 30s内重试3次
  private reconnectAttempts: number;
  private maxReconnectAttempts: 3;
}
```

**协议层**：通过 `futu-api`（Node.js Protobuf 客户端）或 Python Bridge 调用 Futu OpenD。推荐 Python Bridge 路线（复用已有 `python-bridge.ts` 基础设施）。

### 2.3 篮子交易接口

```typescript
// services/basket-order.ts

interface BasketItem {
  symbol: string;
  side: OrderSide;
  // 三种模式三选一
  quantity?: number;               // 按股数
  amount?: number;                 // 按金额（自动算股数，A股向下取100整数倍）
  weight?: number;                 // 按权重（需配合 totalAmount）
}

interface BasketOrderRequest {
  basketId?: string;               // 可选自定义ID
  items: BasketItem[];
  totalAmount?: number;            // 按权重模式的总金额
  mode: "quantity" | "amount" | "weight";
  accountId?: string;              // 默认用当前活跃账户
  dryRun?: boolean;                // true=仅预览，不提交
}

interface BasketPreview {
  basketId: string;
  items: BasketPreviewItem[];
  totalEstimatedAmount: number;
  totalEstimatedCommission: number;
  warnings: string[];              // 停牌/资金不足等
}

interface BasketPreviewItem {
  symbol: string;
  name?: string;
  side: OrderSide;
  quantity: number;                // 计算后的实际股数
  estimatedPrice: number;
  estimatedAmount: number;
  estimatedCommission: number;
  riskCheckPassed: boolean;
  riskRejectReason?: string;
}

interface BasketOrderResult {
  basketId: string;
  status: "all_filled" | "partial" | "all_rejected" | "pending";
  items: BasketOrderItemResult[];
  summary: { submitted: number; filled: number; rejected: number; pending: number };
}

interface BasketOrderItemResult {
  symbol: string;
  orderId?: string;
  status: OrderStatus | "skipped";
  reason?: string;                 // 跳过/拒绝原因
}

class BasketOrderService {
  preview(req: BasketOrderRequest): Promise<BasketPreview>;
  submit(req: BasketOrderRequest): Promise<BasketOrderResult>;
  cancel(basketId: string): Promise<void>;           // 撤销未成交子单
  getStatus(basketId: string): Promise<BasketOrderResult>;
  listBaskets(filter?: { accountId?: string }): Promise<BasketOrderResult[]>;
}
```

### 2.4 绩效报告接口

```typescript
// services/performance-report.ts

interface ReportRequest {
  accountId?: string;
  dateFrom: string;                // "2026-01-01"
  dateTo: string;
  benchmark?: string;              // "000300.SH" 沪深300
  groupBy?: "day" | "week" | "month" | "quarter" | "year";
  includeTradeDetail?: boolean;
}

interface PerformanceReport {
  meta: { accountId: string; dateFrom: string; dateTo: string; generatedAt: string };

  // 核心指标
  summary: {
    totalReturn: number;
    annualizedReturn: number;
    maxDrawdown: number;
    maxDrawdownDuration: number;   // 天
    sharpeRatio: number;
    sortinoRatio: number;
    calmarRatio: number;
    winRate: number;
    profitFactor: number;
    avgHoldingDays: number;
    turnoverRate: number;
    maxSingleProfit: number;
    maxSingleLoss: number;
    totalTrades: number;
    totalCommission: number;
  };

  // 时间维度收益
  periodReturns: { period: string; return_pct: number; pnl: number; trades: number }[];

  // 归因分解
  attribution: {
    bySector: { sector: string; contribution: number; weight: number }[];
    bySymbol: { symbol: string; name: string; contribution: number; trades: number }[];
    byStrategy: { strategyId: string; contribution: number; trades: number }[];
  };

  // 曲线数据
  equityCurve: { date: string; equity: number }[];
  drawdownCurve: { date: string; drawdown: number }[];
  benchmarkCurve?: { date: string; value: number }[];
}

class PerformanceReportService {
  generate(req: ReportRequest): Promise<PerformanceReport>;
  renderHtml(report: PerformanceReport): string;
  exportPdf?(report: PerformanceReport): Promise<Buffer>;
}
```

### 2.5 数据导出接口

```typescript
// services/data-export.ts

type ExportTarget =
  | { type: "positions"; accountId?: string }
  | { type: "orders"; accountId?: string; dateFrom?: string; dateTo?: string }
  | { type: "trades"; accountId?: string; dateFrom?: string; dateTo?: string }
  | { type: "backtest"; backtestId: string }
  | { type: "screen"; screenId: string }
  | { type: "equity"; accountId?: string; dateFrom?: string; dateTo?: string };

type ExportFormat = "csv" | "xlsx";

interface ExportResult {
  filename: string;
  format: ExportFormat;
  path: string;                    // 本地文件路径
  rowCount: number;
  size: number;                    // bytes
}

class DataExportService {
  export(target: ExportTarget, format: ExportFormat): Promise<ExportResult>;
}
```

### 2.6 实盘 vs 回测对比接口

```typescript
// services/live-backtest-compare.ts

interface CompareRequest {
  strategyId: string;
  dateFrom: string;
  dateTo: string;
  accountId?: string;
  backtestId?: string;             // 可选指定某次回测，否则自动运行回测
}

interface CompareResult {
  strategyId: string;
  period: { from: string; to: string };

  live: {
    totalReturn: number;
    sharpeRatio: number;
    maxDrawdown: number;
    avgSlippage: number;           // 滑点 bps
    avgSignalDelay: number;        // 信号延迟 ms
    equityCurve: { date: string; equity: number }[];
  };

  backtest: {
    totalReturn: number;
    sharpeRatio: number;
    maxDrawdown: number;
    equityCurve: { date: string; equity: number }[];
  };

  deviation: {
    returnDeviation: number;       // 实盘收益 / 回测收益 - 1
    curveCorrelation: number;      // 曲线相关系数
    slippageCost: number;          // 滑点造成的收益损耗
    decayAlertTriggered: boolean;  // 是否触发衰减告警
  };
}

interface DecayAlertConfig {
  returnDeviationThreshold: number;    // 默认 0.20 (20%)
  perStrategyOverrides?: Record<string, number>;
}

class LiveBacktestCompareService {
  compare(req: CompareRequest): Promise<CompareResult>;
  getDecayAlerts(): DecayAlert[];
}
```

### 2.7 HTTP API 新增端点

```
# 多账户
GET    /quant/api/accounts                    # 列出所有账户
POST   /quant/api/accounts                    # 注册新账户
DELETE /quant/api/accounts/:id                # 注销账户
POST   /quant/api/accounts/:id/connect        # 连接账户
POST   /quant/api/accounts/:id/disconnect     # 断开账户
POST   /quant/api/accounts/switch             # 切换活跃账户（需确认）
GET    /quant/api/accounts/aggregate           # 聚合视图
GET    /quant/api/accounts/:id/positions       # 指定账户持仓
GET    /quant/api/accounts/:id/orders          # 指定账户订单

# 篮子交易
POST   /quant/api/basket/preview               # 篮子预览
POST   /quant/api/basket/submit                # 提交篮子单
DELETE /quant/api/basket/:id                    # 撤销篮子
GET    /quant/api/basket/:id                    # 篮子状态
GET    /quant/api/baskets                       # 篮子历史

# 绩效报告
POST   /quant/api/report/performance            # 生成绩效报告（JSON）
GET    /quant/api/report/performance/html        # 绩效报告（HTML页面）

# 交互式资金曲线
GET    /quant/pages/equity-chart                 # 交互式资金曲线页面

# 数据导出
POST   /quant/api/export                        # 导出数据
GET    /quant/api/export/:filename               # 下载导出文件

# 实盘 vs 回测对比
POST   /quant/api/compare/live-vs-backtest       # 对比分析
GET    /quant/api/compare/decay-alerts           # 衰减告警列表
```

### 2.8 AI 工具新增

| 工具名 | 描述 | 关键参数 |
|--------|------|---------|
| `account_switch` | 切换当前操作账户 | accountId |
| `account_list` | 查看所有账户状态和聚合视图 | — |
| `basket_order` | 篮子下单（预览+执行） | items[], mode, totalAmount |
| `performance_report` | 生成绩效报告 | dateFrom, dateTo, benchmark |
| `data_export` | 导出数据为CSV/Excel | target, format |
| `live_vs_backtest` | 实盘与回测对比分析 | strategyId, dateFrom, dateTo |

---

## 三、数据模型变更

### 3.1 配置模型扩展

```typescript
// types.ts 变更

// 原有（单账户）
interface QuantTradingConfig {
  tradingAccount?: TradingAccountConfig;
  // ...
}

// 新增（多账户）— 向后兼容
interface QuantTradingConfig {
  tradingAccount?: TradingAccountConfig;        // 保留：默认账户，兼容旧配置
  accounts?: AccountConfig[];                    // 新增：多账户列表
  activeAccountId?: string;                      // 新增：当前活跃账户ID
  // ...
}
```

**向后兼容策略**：如果 `accounts` 为空但 `tradingAccount` 存在，自动迁移为单元素 `accounts` 数组。

### 3.2 持久化文件变更

现有单文件改为按账户隔离的目录结构：

```
~/.openclaw/quant-trading/
├── accounts.json                              # 账户注册表（加密凭证）
├── accounts/
│   ├── paper-default/
│   │   ├── portfolio.json
│   │   ├── orders.json
│   │   ├── trades.json
│   │   ├── equity-history.json
│   │   └── signals.json
│   ├── futu-main/
│   │   ├── orders.json
│   │   ├── trades.json
│   │   ├── equity-history.json
│   │   └── signals.json
│   └── qmt-001/
│       └── ...
├── baskets/                                   # 篮子单记录
│   └── basket-{id}.json
├── reports/                                   # 生成的报告
│   └── report-{timestamp}.html
└── exports/                                   # 导出文件（临时，24h自动清理）
    └── positions-2026-04-05.csv
```

**数据迁移**：首次启动检测到旧文件（`orders-paper.json` 等），自动迁移到 `accounts/paper-default/` 目录。

### 3.3 篮子单数据模型

```typescript
interface BasketOrder {
  basketId: string;
  accountId: string;
  createdAt: string;
  mode: "quantity" | "amount" | "weight";
  totalAmount?: number;
  status: "preview" | "submitted" | "partial" | "completed" | "cancelled";
  items: {
    symbol: string;
    side: OrderSide;
    requestedQty?: number;
    requestedAmount?: number;
    requestedWeight?: number;
    actualQty: number;
    orderId?: string;
    orderStatus: OrderStatus | "skipped";
    reason?: string;
  }[];
}
```

### 3.4 凭证加密

```typescript
// account/account-store.ts

// accounts.json 中 brokerConfig 的敏感字段加密存储
// 加密方式：AES-256-GCM，密钥派生自机器指纹 + 可选用户密码
interface EncryptedAccountConfig extends Omit<AccountConfig, "brokerConfig"> {
  brokerConfig: string;            // 加密后的 JSON 字符串
  encryptionVersion: 1;
}
```

---

## 四、依赖关系

### 4.1 模块依赖图

```
                         ┌─────────────────┐
                         │   AI Tools /     │
                         │   Commands /     │
                         │   HTTP Routes    │
                         └────────┬────────┘
                                  │
                                  ▼
                    ┌─────────────────────────┐
                    │    AccountContext        │  ← 所有业务入口通过此获取当前账户
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │    AccountManager        │  ← 管理多账户生命周期
                    └──┬──────┬──────┬────────┘
                       │      │      │
            ┌──────────┘      │      └──────────┐
            ▼                 ▼                  ▼
     ┌──────────┐    ┌──────────────┐    ┌──────────┐
     │PaperBroker│   │FutuBroker    │    │QMTBroker  │
     │(已有)      │   │(新增)        │    │(已有)      │
     └──────────┘    └──────────────┘    └──────────┘
            │                 │                  │
            └────────┬────────┘──────────────────┘
                     ▼
          ┌─────────────────────┐
          │  OrderManager       │  ← 每个账户独立实例
          │  (per account)      │
          └─────────┬───────────┘
                    │
          ┌─────────┤──────────────────────────────┐
          ▼         ▼                              ▼
   ┌──────────┐ ┌──────────────┐          ┌──────────────┐
   │ RiskGate  │ │BasketOrder   │          │Conditional   │
   │(per acct) │ │Service       │          │Order/DCA     │
   └──────────┘ └──────────────┘          └──────────────┘
                    │
                    ▼
       ┌──────────────────────────────────────────┐
       │           只读/分析层                      │
       ├──────────────┬──────────────┬─────────────┤
       │ Performance  │ DataExport   │ LiveBacktest │
       │ Report       │ Service      │ Compare      │
       └──────────────┴──────────────┴─────────────┘
```

### 4.2 新增 npm 依赖

| 包名 | 用途 | 必要性 |
|------|------|--------|
| `futu-api` | Futu OpenD Node.js SDK | FR-2（或用 Python Bridge 替代） |
| `xlsx` (sheetjs) | Excel 文件生成 | FR-7 |
| `echarts` / `chart.js` | 交互式图表（资金曲线页面） | FR-5（前端CDN引入，无需npm） |

### 4.3 模块间约束

| 约束 | 说明 |
|------|------|
| AccountManager → IBrokerAdapter | 每个账户创建独立的 Broker 实例 |
| AccountManager → OrderManager | 每个账户创建独立的 OrderManager 实例 |
| AccountManager → RiskGate | 每个账户独立风控参数和状态 |
| BasketOrderService → AccountContext | 通过当前上下文获取目标账户 |
| BasketOrderService → OrderManager | 逐笔提交子单（不绕过风控） |
| PerformanceReportService → EquityHistory | 读取账户级资金快照 |
| PerformanceReportService → TradeRecord[] | 读取账户级交易记录 |
| LiveBacktestCompareService → LiveEngine | 读取实盘策略执行记录 |
| LiveBacktestCompareService → BuiltinEngine | 需要时自动运行回测 |
| DataExportService → 各 Store | 只读访问所有数据存储 |

---

## 五、实现顺序与并行条件

### Phase 1：基础设施（约 1 周）

```
┌────────────────────────────────────────────────┐
│ 1a. AccountManager + AccountContext + Store     │ ← 关键路径
│     - 多账户配置模型                              │
│     - 账户注册/连接/切换                          │
│     - 旧数据迁移逻辑                             │
│     - 凭证加密存储                               │
│     - 聚合视图                                   │
│     - HTTP API + AI 工具                         │
├────────────────────────────────────────────────┤
│ 1b. DataExportService（可与 1a 并行）            │ ← 无账户依赖也能工作
│     - CSV 导出（UTF-8 BOM）                      │
│     - Excel 导出                                 │
│     - HTTP 下载端点                              │
│     - AI 工具                                    │
└────────────────────────────────────────────────┘
```

**并行条件**：1b 在单账户模式下即可独立开发和测试。1a 完成后补充 `accountId` 参数即可。

### Phase 2：交易能力（约 1.5 周，依赖 Phase 1a）

```
┌────────────────────────────────────────────────┐
│ 2a. FutuBrokerAdapter                          │
│     - OpenD 连接管理                             │
│     - 协议转换（OrderRequest ↔ Futu 协议）       │
│     - 自动重连                                   │
│     - 订单/持仓/账户查询                          │
│     - 实时推送订阅                               │
├────────────────────────────────────────────────┤
│ 2b. BasketOrderService（可与 2a 并行）           │
│     - 篮子预览（量/额/权重三种模式）              │
│     - 子单拆分 + 逐笔风控                        │
│     - 篮子状态追踪                               │
│     - 撤销未成交子单                              │
│     - 与组合再平衡联动                            │
│     - HTTP API + AI 工具                         │
└────────────────────────────────────────────────┘
```

**并行条件**：2a（富途适配器）和 2b（篮子交易）可完全并行开发。2a 需要 Futu OpenD 环境；2b 可用 PaperBroker 测试。

### Phase 3：分析能力（约 1.5 周，依赖 Phase 1a）

```
┌────────────────────────────────────────────────────────┐
│ 3a. PerformanceReportService                           │
│     - 核心指标计算（收益/回撤/夏普/胜率等）               │
│     - 时间维度聚合（日/周/月/季/年）                      │
│     - 行业/个股/策略归因分解                              │
│     - HTML 报告模板渲染                                  │
│     - HTTP API + AI 工具                                │
├────────────────────────────────────────────────────────┤
│ 3b. 资金曲线交互式页面（可与 3a 并行）                    │
│     - ECharts 可缩放资金曲线                             │
│     - 关键事件标注（大额交易/止损/回撤峰谷）              │
│     - 基准叠加对比                                      │
│     - 回撤统计表                                        │
│     - 时间范围筛选                                      │
├────────────────────────────────────────────────────────┤
│ 3c. LiveBacktestCompareService（可与 3a/3b 并行）       │
│     - 实盘执行记录提取                                   │
│     - 自动对齐回测（取交集时间段）                        │
│     - 偏离度/滑点/衰减计算                               │
│     - 衰减告警（阈值可配置，默认20%）                     │
└────────────────────────────────────────────────────────┘
```

**并行条件**：3a、3b、3c 三个模块互不依赖，可完全并行。均依赖 Phase 1a 的账户级数据隔离。

### 关键里程碑

| 里程碑 | 完成标志 | 前置条件 |
|--------|---------|---------|
| M1 | 多账户可切换、旧数据已迁移 | — |
| M2 | CSV/Excel 导出可用 | — |
| M3 | 富途可连接下单 | M1 |
| M4 | 篮子单可预览+执行 | M1 |
| M5 | 绩效报告可生成HTML | M1 |
| M6 | 交互式资金曲线可查看 | M1 |
| M7 | 实盘vs回测对比可运行 | M1 |
| M8 | 全部功能集成测试通过 | M3-M7 |

### 开放问题决策建议

| 问题 | 建议 |
|------|------|
| Q1 多账户存储方案 | **多文件隔离**（每账户独立目录），原因：隔离性好、迁移简单、不担心单文件性能 |
| Q3 篮子单撤销 | **支持撤销未成交子单**，已纳入接口设计 |
| Q4 绩效报告模板 | **固定格式 + 可选参数**（如 groupBy、benchmark），不做自定义模板 |
| Q5 定时导出 | **本期不做**，可通过定时报告 + 导出工具组合实现 |
| Q6 衰减告警阈值 | **默认 20%，支持按策略覆盖**，趋势策略可放宽至 30% |
| Q7 存储迁移到SQLite | **本期保持 JSON 文件**，按账户隔离后单文件数据量可控 |
| Q8 多券商并行 | **支持**，AccountManager 天然支持多账户同时在线 |

---

## 附录 A：现有模块影响分析

以下现有模块需要适配多账户：

| 模块 | 变更类型 | 变更内容 |
|------|---------|---------|
| `index.ts` | 修改 | 初始化 AccountManager，注册新工具/路由/服务 |
| `types.ts` | 扩展 | 新增 AccountConfig、BasketOrder 等类型 |
| `order-manager.ts` | 修改 | 数据路径从全局改为按账户目录 |
| `live-engine.ts` | 修改 | 通过 AccountContext 获取 Broker（而非直接创建） |
| `risk-gate.ts` | 修改 | 支持按账户加载不同风控参数 |
| `equity-history.ts` | 修改 | 数据路径按账户隔离 |
| `routes.ts` | 扩展 | 注册新路由，RouteDeps 增加 AccountManager |
| `dashboard.ts` | 扩展 | Dashboard 展示多账户聚合/切换 |
| `portfolio_rebalance` 工具 | 扩展 | 再平衡结果可直接生成 BasketOrderRequest |

## 附录 B：安全考虑

| 风险 | 缓解措施 |
|------|---------|
| 误切账户导致错误下单 | 切换账户需显式确认（NFR-8） |
| 凭证泄露 | AES-256-GCM 加密存储，不在日志中打印敏感信息 |
| 篮子单资金竞争 | 提交前锁定并校验总可用资金，原子扣减 |
| 多账户数据串扰 | 每账户独立文件目录 + 运行时独立实例 |
| 导出文件信息泄露 | 导出文件 24h 自动清理，路径不可遍历 |
