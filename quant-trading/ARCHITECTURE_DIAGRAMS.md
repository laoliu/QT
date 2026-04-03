# quant-trading 架构图谱

本文件汇总了 quant-trading 扩展在 OpenClaw 插件体系中的完整架构视图，包含机制、时序、生命周期、排障和运维等多个维度，可配合 [ARCHITECTURE_ANALYSIS.md](./ARCHITECTURE_ANALYSIS.md) 一同阅读。

---

## 总览地图（阅读索引）

> 按 **机制 → 数据流 → 运维 → 部署** 四层分组，推荐按箭头方向阅读。

```mermaid
flowchart TB
  classDef mechanism fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
  classDef flow fill:#dcfce7,stroke:#16a34a,color:#14532d
  classDef ops fill:#fef9c3,stroke:#ca8a04,color:#713f12
  classDef deploy fill:#f3e8ff,stroke:#9333ea,color:#581c87

  subgraph Layer1[机制层 — 插件是怎么工作的]
    D1([1. OpenClaw 扩展机制全景图\n插件发现→加载→注册→消费]):::mechanism
    D7([7. 插件缓存与热加载关系图\n三层缓存命中与失效规则]):::mechanism
  end

  subgraph Layer2[数据流层 — 一次请求是怎么跑的]
    D2([2. 用户请求到工具执行时序图\n端到端请求链路]):::flow
    D3([3. quant-trading 内部子系统架构图\nTools/Services/Storage/Bridge]):::flow
    D5([5. Tick 实时数据到策略执行\ningress→feature→strategy→order]):::flow
  end

  subgraph Layer3[运维层 — 出了问题怎么办]
    D4([4. 生命周期状态机\nDiscovered→Running→Degraded→Stopped]):::ops
    D6([6. 工具调用失败排障决策树\n配置/数据/Python/交易/Tick 分层定位]):::ops
    D8([8. 工具调用错误传播路径图\n错误如何到达用户+可观测挂载点]):::ops
  end

  subgraph Layer4[部署层 — 上线后的运行态]
    D9([9. Gateway 冷启动关键路径图\n启动到各服务就绪的完整时序]):::deploy
    D10([10. 多通道并发一致性图\n共享状态域与保护策略]):::deploy
    D11([11. 容量与背压控制图\n限流→缓冲→可观测→运维闭环]):::deploy
  end

  Layer1 --理解基础后--> Layer2
  Layer2 --理解数据流后--> Layer3
  Layer3 --掌握排障后--> Layer4

  D1 -. 参考.-> D7
  D2 -. 参考.-> D5
  D4 -. 参考.-> D8
  D9 -. 参考.-> D10
  D10 -. 参考.-> D11
```

---

## 目录

1. [OpenClaw 扩展机制全景图](#1-openclaw-扩展机制全景图)
2. [用户请求到工具执行时序图](#2-用户请求到工具执行时序图)
3. [quant-trading 内部子系统架构图](#3-quant-trading-内部子系统架构图)
4. [生命周期状态机](#4-生命周期状态机)
5. [Tick 实时数据到策略执行的数据流](#5-tick-实时数据到策略执行的数据流)
6. [工具调用失败排障决策树](#6-工具调用失败排障决策树)
7. [插件缓存与热加载关系图](#7-插件缓存与热加载关系图)
8. [工具调用错误传播路径图](#8-工具调用错误传播路径图)
9. [Gateway 冷启动关键路径图](#9-gateway-冷启动关键路径图)
10. [多通道并发一致性图](#10-多通道并发一致性图)
11. [容量与背压控制图](#11-容量与背压控制图)

---

## 1. OpenClaw 扩展机制全景图

> 描述：插件从发现、加载、注册到运行时消费的完整链路。

```mermaid
flowchart TB
  U[用户消息\nDiscord/Telegram/Web/CLI] --> G[Gateway Server]
  G --> GP[loadGatewayPlugins]
  GP --> L[loadOpenClawPlugins]

  subgraph D[插件发现与解析]
    R[resolvePluginSourceRoots\nworkspace/global/bundled/config] --> DISC[discoverOpenClawPlugins]
    DISC --> MR[loadPluginManifestRegistry]
    MR --> CAND[候选插件 + manifest 元数据]
  end

  L --> D
  CAND --> JI[jiti 动态加载插件入口\nalias: openclaw/plugin-sdk]
  JI --> REG[createPluginRegistry + createApi]

  subgraph EXT[quant-trading 插件 register(api)]
    T[registerTool\nmarket_data/backtest/portfolio/...]
    CMD[registerCommand\n聊天快捷命令]
    CLI[registerCli\nquant 子命令]
    SVC[registerService\nLiveEngine/DataSync/Monitor/...]
    HTTP[registerHttpRoute\n由 registerQuantRoutes/registerAdminRoutes 挂载]
    HK[api.on before_prompt_build\n注入量化工作流系统提示]
  end

  REG --> EXT

  subgraph PR[PluginRegistry]
    P1[plugins]
    P2[tools]
    P3[channels]
    P4[gatewayHandlers]
    P5[httpRoutes]
    P6[services]
    P7[commands]
    P8[hooks]
  end

  EXT --> PR
  PR --> ACT[setActivePluginRegistry\n全局活动注册表]

  ACT --> RT1[Agent 工具执行路径\n从 registry.tools 调用]
  ACT --> RT2[Channel 查找路径\ngetChannelPlugin/listChannelPlugins]
  ACT --> RT3[Gateway 方法扩展\n合并 plugin gatewayMethods]
  ACT --> RT4[HTTP 路由分发\n插件路由进入网关]
  ACT --> RT5[生命周期服务\n网关启动/停止时管理]

  RT1 --> OUT[返回响应到网关]
  RT2 --> OUT
  RT3 --> OUT
  RT4 --> OUT
  RT5 --> OUT
  OUT --> U
```

---

## 2. 用户请求到工具执行时序图

> 描述：一条真实请求从 Channel 进入，经 Agent 调用 quant 工具，最后返回用户的完整时序。

```mermaid
sequenceDiagram
  autonumber
  participant User as 用户
  participant Ch as Channel(Discord/Web/CLI)
  participant Gw as Gateway
  participant Pl as Plugin Loader
  participant Reg as Active PluginRegistry
  participant Agent as Agent Runtime
  participant QT as quant-trading Tool
  participant Py as Python Bridge
  participant Svc as Plugin Services

  User->>Ch: 发送请求(例如: 帮我回测策略)
  Ch->>Gw: 转发标准化请求

  alt 首次请求或缓存失效
    Gw->>Pl: loadGatewayPlugins()
    Pl->>Pl: loadOpenClawPlugins()
    Pl->>Pl: discoverOpenClawPlugins + manifest registry
    Pl->>Pl: jiti import quant-trading/index.ts
    Pl->>Pl: 执行 register(api)
    Pl->>Reg: setActivePluginRegistry(registry)
  else 已有活动注册表
    Gw->>Reg: 直接读取已激活 registry
  end

  Gw->>Agent: 发起 agent/chat 流程
  Agent->>Reg: 解析可用工具与命令
  Reg-->>Agent: 返回 quant-trading tools 元数据

  Agent->>QT: 调用具体工具(如 backtest/market_data)

  alt 工具需要 Python 能力
    QT->>Py: runPythonScript(...)
    Py-->>QT: 返回结构化结果
  end

  opt 工具/服务产生事件
    QT->>Svc: 写入事件/告警/状态
    Svc-->>Gw: SSE/日志/状态更新
  end

  QT-->>Agent: 工具结果(JSON/文本)
  Agent-->>Gw: 组装最终回复
  Gw-->>Ch: 下发响应
  Ch-->>User: 展示结果
```

---

## 3. quant-trading 内部子系统架构图

> 描述：register(api) 内部各层的依赖关系：Tools / Services / Storage / Bridge。

```mermaid
flowchart LR
  subgraph Core[OpenClaw 插件容器]
    API[OpenClawPluginApi]
    REG[Plugin Registry Entries]
  end

  subgraph QT[quant-trading register(api)]
    CFG[Config & Validation]
    TOOLREG[Tool Registration]
    CMDREG[Command & CLI Registration]
    SVCREG[Service Registration]
    ROUTEREG[HTTP Routes Registration]
    HOOKREG[Prompt Hook Registration]
  end

  API --> QT
  QT --> REG

  subgraph Tools[AI Tools 层]
    T1[market_data / stock_screen]
    T2[backtest / strategy_*]
    T3[portfolio / live_trading / conditional_order]
    T4[options_* / risk_analysis]
    T5[fund_flow / sector_* / sentiment]
    T6[data_cache / scheduled_task / price_alert]
  end

  subgraph Services[后台服务层]
    S1[LiveEngine]
    S2[DataSyncService]
    S3[SignalMonitor / PnLMonitor]
    S4[OptionsMonitor]
    S5[TickIngressService]
    S6[TickStrategyService]
    S7[PriceAlertService]
    S8[TaskScheduler]
    S9[ReceiverService]
  end

  subgraph Storage[状态与存储]
    ST1[AlertStore]
    ST2[BacktestStore]
    ST3[ScanHistoryStore]
    ST4[OptimizationStore]
    ST5[EquityHistory]
    ST6[TickHistoryStore]
    ST7[InMemoryStorage / DataWarehouse]
    ST8[TradeJournal / Strategy Stores]
    EB[EventBus]
    LOG[LogStore]
  end

  subgraph Bridge[外部能力桥接]
    PY[Python Bridge\nbacktrader/akshare/tushare]
    BRK[Broker Bridge\nQMT/Paper]
    FEED[Market Data Providers]
  end

  Tools --> Services
  Tools --> Storage
  Tools --> Bridge
  Services --> Storage
  Services --> Bridge
  Storage --> ROUTEOUT[Admin & Quant HTTP APIs]
  EB --> ROUTEOUT
  LOG --> ROUTEOUT

  ROUTEREG --> ROUTEOUT
  TOOLREG --> Tools
  CMDREG --> CmdOut[Chat Commands + quant CLI]
  SVCREG --> Services
  HOOKREG --> PromptOut[before_prompt_build System Context]
```

---

## 4. 生命周期状态机

> 描述：从插件发现到 gateway 停止的完整生命周期状态转移，含 Running/Paused/Degraded 路径。

```mermaid
stateDiagram-v2
  [*] --> Discovered: 插件发现
  Discovered --> Loaded: jiti import + register(api)
  Loaded --> Registered: tools/commands/services/routes/hooks 已登记
  Registered --> StartingServices: Gateway 启动触发 service.start
  StartingServices --> Running: LiveEngine/Monitor/Alert/Tick 等进入运行态

  Running --> Running: 处理工具调用\n处理HTTP请求\n事件总线广播

  Running --> Paused: live_engine pause / 风控触发
  Paused --> Running: resume

  Running --> Degraded: 数据源异常/桥接异常/健康探针失败
  Degraded --> Running: 恢复连接与状态

  Running --> Stopping: Gateway 停止或插件卸载
  Degraded --> Stopping
  Paused --> Stopping

  Stopping --> Flushing: store flush + 资源释放
  Flushing --> Stopped: service.stop 完成
  Stopped --> [*]
```

---

## 5. Tick 实时数据到策略执行的数据流

> 描述：Tick 从数据源进入，经过 ingress → feature → strategy → order 的完整链路。

```mermaid
sequenceDiagram
  autonumber
  participant Src as Tick Source\n(QMT Bridge/Receiver)
  participant Ingress as TickIngressService
  participant Feature as TickFeatureStore
  participant Hist as TickHistoryStore
  participant Strat as TickStrategyService
  participant Engine as LiveEngine.OrderManager
  participant Alert as AlertStore/EventBus
  participant API as Admin/Quant HTTP

  Src->>Ingress: 推送 tick/bar
  Ingress->>Hist: appendTick / appendBar
  Ingress->>Feature: recordTick -> features
  Feature-->>Ingress: 特征向量
  Ingress->>Hist: appendFeature
  Ingress->>Strat: 广播 tick features

  Strat->>Strat: 策略条件判定/信号生成
  alt 满足交易条件
    Strat->>Engine: submit(order)
    Engine-->>Strat: order result
    Strat->>Alert: 发送信号/订单事件
  else 不满足条件
    Strat->>Alert: 记录观察事件(可选)
  end

  Alert-->>API: SSE/查询接口可见
  Hist-->>API: 回放与状态查询
  Feature-->>API: 特征查询(调试/监控)
```

---

## 6. 工具调用失败排障决策树

> 描述：从"工具失败/无结果"出发，分层定位配置/数据/Python/交易/Tick 五类根因。

```mermaid
flowchart TD
  A[用户反馈: 工具失败/无结果] --> B{是否有明确错误信息?}
  B -- 无 --> C[先看 gateway/plugin 日志\n以及 /quant/admin/logs]
  B -- 有 --> D[按错误类别分流]

  C --> D

  D --> E{配置类错误?\nmissing key/invalid config}
  E -- 是 --> E1[检查插件配置校验输出\nvalidateConfig + pluginConfig]
  E1 --> E2[修复配置后重启/热加载]
  E -- 否 --> F{数据源错误?\nquote/klines/provider timeout}

  F -- 是 --> F1[检查 market data provider 连通性]
  F1 --> F2[确认 symbol/market 映射]
  F2 --> F3[检查 DataWarehouse/缓存命中]
  F -- 否 --> G{Python bridge 错误?\nscript fail/import fail}

  G -- 是 --> G1[检查 runPythonScript 参数与脚本路径]
  G1 --> G2[验证 Python 环境和依赖]
  G2 --> G3[单独运行对应脚本动作]
  G -- 否 --> H{交易执行链路错误?\norder reject/engine not running}

  H -- 是 --> H1[检查 LiveEngine 状态 running/paused/degraded]
  H1 --> H2[检查 broker bridge/QMT 连接]
  H2 --> H3[查看 orderManager submit 返回 reason]
  H -- 否 --> I{Tick/策略信号链路错误?}

  I -- 是 --> I1[检查 TickIngress source 和订阅同步]
  I1 --> I2[检查 TickFeatureStore 是否产生特征]
  I2 --> I3[检查 TickStrategyService 触发条件]
  I -- 否 --> J[进入通用路径: 打开调试日志 + 最小重现]

  E2 --> K[回归验证: 复测同一请求]
  F3 --> K
  G3 --> K
  H3 --> K
  I3 --> K
  J --> K

  K --> L{是否恢复?}
  L -- 是 --> M[关闭事件并记录根因]
  L -- 否 --> N[升级排障: 捕获请求参数/调用栈/依赖状态]
```

---

## 7. 插件缓存与热加载关系图

> 描述：三层缓存（Discovery / Manifest / Registry）的命中条件、失效规则与关键安全约束。

```mermaid
flowchart LR
  subgraph Inputs[输入维度]
    I1[workspaceDir]
    I2[plugins.loadPaths]
    I3[plugins.allow/deny/entries]
    I4[plugins.installs]
    I5[onlyPluginIds/setup-only mode]
    I6[env knobs\nOPENCLAW_PLUGIN_*_CACHE_MS]
  end

  Inputs --> DK[Discovery Cache Key]
  Inputs --> MK[Manifest Cache Key]
  Inputs --> RK[Registry Cache Key]

  DK --> DC[Discovery Cache\n~1s 默认窗口]
  MK --> MC[Manifest Registry Cache\n~1s 默认窗口]
  RK --> RC[Plugin Registry Cache\nLRU, max 128]

  subgraph LoadFlow[加载流程]
    L1[discoverOpenClawPlugins]
    L2[loadPluginManifestRegistry]
    L3[loadOpenClawPlugins]
    L4[createPluginRegistry/createApi]
    L5[jiti import + register(api)]
    L6[setActivePluginRegistry]
  end

  DC --> L1
  MC --> L2
  RC --> L3

  L1 --> L2 --> L3 --> L4 --> L5 --> L6

  L3 --> C1{cache 命中?}
  C1 -- 是 --> C2[直接返回 registry\n并可激活为 active]
  C1 -- 否 --> C3[执行完整发现/导入/注册]

  C3 --> C4[写入 registry cache]
  C4 --> L6

  subgraph Notes[关键规则]
    N1[activate:false 必须 cache:false\n避免命令注册状态不一致]
    N2[clearPluginCommands 仅在 shouldActivate 时执行]
    N3[allowlist 为空且有非 bundled 插件\n会发安全告警]
  end

  L3 --> Notes
```

---

## 8. 工具调用错误传播路径图

> 描述：工具失败时错误如何从 Provider/Python/Broker 层传播到用户，以及可观测性挂载点。

```mermaid
sequenceDiagram
  autonumber
  participant User as 用户
  participant Gw as Gateway
  participant Agent as Agent Runtime
  participant Tool as quant-trading Tool
  participant Dep as Provider/Python/Broker
  participant Log as Logger + LogStore
  participant Alert as AlertStore/EventBus

  User->>Gw: 发起请求
  Gw->>Agent: 进入 agent/chat 流程
  Agent->>Tool: execute(toolArgs)

  alt 下游依赖成功
    Tool->>Dep: 调用外部能力
    Dep-->>Tool: success payload
    Tool-->>Agent: 标准结果(content/details)
    Agent-->>Gw: 组装回复
    Gw-->>User: 返回成功
  else 下游依赖失败/超时/异常
    Tool->>Dep: 调用外部能力
    Dep-->>Tool: throw/error code
    Tool->>Log: 记录错误上下文
    opt 触发告警
      Tool->>Alert: add(alert) + emit(event)
    end
    Tool-->>Agent: 返回失败信息/降级结果
    Agent-->>Gw: 失败响应(可带重试建议)
    Gw-->>User: 返回可理解错误
  end

  opt 后台可观测性
    Log-->>Gw: /quant/admin/logs 可查询
    Alert-->>Gw: SSE/状态接口可见
  end
```

---

## 9. Gateway 冷启动关键路径图

> 描述：从进程启动到 quant-trading 所有服务就绪、RPC/HTTP 对外可用的完整时序。

```mermaid
sequenceDiagram
  autonumber
  participant Boot as Process Boot
  participant Cfg as Config Loader
  participant G as Gateway Startup
  participant PL as Plugin Loader
  participant Disc as Discovery+Manifest
  participant J as jiti Import
  participant Q as quant-trading register
  participant S as Services Start
  participant Ready as RPC/HTTP Ready

  Boot->>Cfg: loadConfig()
  Cfg-->>Boot: cfg
  Boot->>G: initialize gateway server
  G->>PL: loadGatewayPlugins(cfg, workspaceDir)

  PL->>Disc: discoverOpenClawPlugins()
  Disc-->>PL: plugin candidates
  PL->>Disc: loadPluginManifestRegistry()
  Disc-->>PL: manifest records

  loop 每个启用插件
    PL->>J: jiti import(entry)
    J-->>PL: plugin module
    PL->>Q: register(api)
    Q-->>PL: tools/commands/services/routes/hooks registered
  end

  PL-->>G: active PluginRegistry
  G->>S: start registered services

  par 后台服务并行启动
    S->>S: DataSync / SignalMonitor / PnLMonitor
  and 交易服务
    S->>S: LiveEngine / OptionsMonitor
  and 实时链路
    S->>S: TickIngress / TickStrategy / Receiver
  end

  S-->>G: startup complete
  G->>Ready: expose RPC + HTTP routes
  Ready-->>Boot: gateway ready for requests
```

---

## 10. 多通道并发一致性图

> 描述：多个 Channel 并发请求如何共享 quant-trading 状态域，以及一致性保护策略。

```mermaid
flowchart TB
  subgraph Inputs[并发输入源]
    C1[Discord]
    C2[Telegram]
    C3[Web Chat]
    C4[CLI]
    C5[Scheduled Tasks]
  end

  C1 --> GW[Gateway Request Dispatcher]
  C2 --> GW
  C3 --> GW
  C4 --> GW
  C5 --> GW

  GW --> AG[Agent Runtime + Tool Invocation]

  subgraph Shared[quant-trading 共享状态域]
    E[LiveEngine / OrderManager]
    A[AlertStore]
    B[BacktestStore]
    H[EquityHistory]
    T[TickHistoryStore]
    F[TickFeatureStore]
    W[DataWarehouse + InMemoryStorage]
    EV[EventBus]
    L[LogStore]
  end

  AG --> E
  AG --> A
  AG --> B
  AG --> H
  AG --> T
  AG --> F
  AG --> W
  AG --> EV
  AG --> L

  subgraph Guards[一致性保护策略]
    G1[单例 Service 实例\n由 plugin register 统一创建]
    G2[Gateway 事件循环串行执行\n避免跨线程写冲突]
    G3[Store 层限长/环形缓冲\n防止无限增长]
    G4[Service stop 时 flush\n减少崩溃丢失]
    G5[命令/工具共用同一引擎句柄\n避免多引擎状态分裂]
  end

  Shared --> Out1[HTTP Admin 查询]
  EV --> Out2[SSE/状态推送]
  L --> Out1
  A --> Out2

  Guards -.约束.-> Shared
```

---

## 11. 容量与背压控制图

> 描述：从入口限流到各存储缓冲、可观测接口，再到运维动作的完整闭环。

```mermaid
flowchart LR
  Req[请求流量\nchat/tools/http/scheduler] --> Q1{入口限流?}
  Q1 -- 是 --> RL[RateLimiter\nwindow + maxRequests]
  Q1 -- 否 --> Exec
  RL --> Exec[工具执行层]

  Exec --> DQ{数据读取密集?}
  DQ -- 是 --> WH[DataWarehouse\n并发上限 + 缓存优先]
  DQ -- 否 --> TR

  Exec --> TR{交易执行密集?}
  TR -- 是 --> OM[OrderManager\n顺序提交 + broker 反馈]
  TR -- 否 --> EV

  Exec --> EV[EventBus]
  EV --> SSE[SSE Clients]

  subgraph Buffers[缓冲与保留策略]
    B1[EventBus maxRecent]
    B2[LogStore ring buffer]
    B3[AlertStore maxAlerts + TTL]
    B4[TaskScheduler maxConcurrent + maxTasks]
    B5[TickHistory max recent per symbol/series]
  end

  WH --> Buffers
  OM --> Buffers
  EV --> Buffers

  Buffers --> Obs[可观测接口\n/admin routes + logs + status]

  Obs --> Ops[运维动作\n降采样/缩短窗口/暂停引擎/恢复]
  Ops --> Req
```

---

_本文件由 GitHub Copilot 辅助生成，基于 2026-03-26 版本代码分析。_
