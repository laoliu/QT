# 统一策略契约

QT 允许策略继续使用 TypeScript、Backtrader、vn.py 或 AIPHA，但策略的身份和输出格式由 `strategies/catalog.json` 统一登记。

每个目录条目至少声明：

- 稳定的 `id`、实现引擎和源码位置；
- 支持的市场与 K 线周期；
- 数据需求（包括 PIT 财务数据和历史成分股）；
- 输出是 `trading_signal`、`target_position` 还是 `order_intent`；
- `primary`、`compatibility`、`experimental` 或 `live_only` 生命周期。

`GET /quant/api/strategy-catalog` 返回 TS 注册表和 Python Bridge 合并后的目录。旧策略的迁移状态也登记在目录中：

- `migrated_to_builtin`：保留旧 ID 兼容调用，但参数映射后只进入统一 builtin 实现；旧 Backtrader/vn.py 引擎入口会被拒绝。
- `retired`：没有可验证的统一等价物，运行时拒绝新回测，并从统一运行目录隐藏。

这样每个兼容策略都有明确的迁移结果，不再依赖“Python 包是否恰好安装”来决定实际执行路径。

62 个兼容目录条目中，18 个迁移到 builtin，44 个正式退役。另有 8 个与原生 TS 策略同名的 Python 实现，通过 `retiredImplementations` 单独登记退役原因与源码位置；这些 ID 继续代表原生 TS 实现，不能据此声称与旧 Python 行为等价。Python 的运行入口同样读取共享目录，拒绝已迁移、退役或属于 builtin 的 ID；原 Python 类只作为审计和对照资料保留。

以下 18 个入口已按保留的 Python 源码补齐信号规则，旧 ID 的运行版本更新为 `2.0.0`，以区别此前不完整的别名映射：

| 旧 ID | 统一实现 | 保留的行为 |
| --- | --- | --- |
| `ema-cross` | `ema-cross-long-only` | SMA 初始化的 EMA、交叉记忆、只开多和平多 |
| `donchian-breakout` | `donchian-long-only` | 独立入场和退出周期、排除当前 K 线的通道 |
| `kdj` | `kdj-long-only` | K/D 的 SMA 平滑、J 极值退出和半仓入场 |
| `bollinger` | `bollinger-squeeze-reversion` | 总体标准差、带宽挤压过滤和中轨回升退出 |
| `macd` | `macd-long-only` | SMA 初始化的 EMA、MACD 信号线交叉，仅做多 |
| `rsi` | `rsi-long-only` | 超卖入场、超买退出，不反手开空 |
| `adx-trend` | `adx-long-only` | Wilder 平滑、DI 交叉、ADX 入场过滤及衰减退出 |
| `dmi` | `dmi-long-only` | DI 交叉，不额外添加 ADX 过滤 |
| `dema-cross` | `dema-long-only` | 双重 EMA 的预热与交叉 |
| `aroon` | `aroon-long-only` | 上下阈值及阈值穿越 |
| `trix` | `trix-long-only` | 三重 EMA 预热、SMA 信号线交叉 |
| `envelope` | `envelope-long-only` | 均线包络、中轨回升退出 |
| `price-channel-breakout` | `price-channel-long-only` | 独立退出周期、可选成交量确认 |
| `mfi` | `mfi-long-only` | 典型价格相等时计入负资金流 |
| `roc` | `roc-long-only` | ROC 零轴穿越 |
| `adaptive-ma` | `kama-long-only` | KAMA 初始化、自适应平滑及上行入场 |
| `multi-tf-ma` | `triple-ma-long-only` | 三均线首次多头排列、短中均线退出 |
| `supertrend` | `supertrend-python-long-only` | 原 Python 的带宽重置与方向翻转规则 |

原生 Supertrend 保留为 `supertrend-ts`，避免覆盖旧 Python 的同名规则。

`python scripts/generate_legacy_signal_fixtures.py` 使用原 Backtrader 类生成 36 个默认和自定义参数场景。行情包括趋势翻转、挤压、量能变化和非对称高低价冲击，每个场景必须产生信号。`legacy-compatible.test.ts` 逐条核对信号方向、所在 K 线及入场现金比例，并验证历史信号不受后续数据影响、对照源码哈希未变化、所有声明迁移的入口均被覆盖。该验证隔离了成交、费用和 T+1 规则，只证明这些场景下的信号一致，不能证明成交收益完全等价。

`turtle` 的 ATR 风险仓位、止损和金字塔加仓不能用简化突破策略代替；`obv` 引用了标准 Backtrader 不提供的 OBV 指标；两个旧 vn.py 入口存在持仓未同步、通道包含当前 K 线等问题。这四个入口已正式退役，原生替代策略保持独立身份。

## 结果边界

回测入口会为每次运行生成 `runId`，并在 `BacktestResult.runEnvelope` 中返回：

```text
schemaVersion, runId, strategyId, strategyVersion,
engine, dataAsOf, signalFormat, signals
```

每个信号带有 `runId`、`strategyVersion` 和 `dataTimestamp`。AIPHA 研究路径输出相同版本的目标持仓结构，字段为 `rebalanceDate`、`targetWeight`、`riskFlags`、`runId`、`modelId` 和 `signalId`。

## 默认研究路径

`aipha-ml-ranking` 是 A 股低频多因子研究的唯一默认策略。历史数据必须来自带元数据旁车文件的冻结 TuShare PIT 快照；训练、OOS 预测、组合回测和目标持仓导出均由 `python/aipha/research` 完成。TS 负责调度和 UI，Paper/QMT 只消费目标持仓或执行计划。

Paper 和 QMT 的订单持久化都按 broker 模式分文件保存（`orders-paper.json` / `orders-live.json`），订单和已完成交易记录保留 `runId`、`signalId`、`strategyVersion`、`dataTimestamp`，进程重启后可按同一契约回放和查询。QMT 实盘配置还要求订单来自带 provenance 的执行计划；没有统一元数据的订单会在桥接层被拒绝。

QMT 的只读实机验收从仓库根目录执行：

```powershell
# miniQMT 可用时：原生 xtquant 查询路径
python scripts/qmt-readonly-probe.py

# miniQMT 不可用时：完整 QMT 客户端运行 BRIDGE.py，RECEIVER 和网关均启动
python scripts/qmt-readonly-probe.py --receiver-url http://127.0.0.1:18889 --timeout 20
```

两种方式均要求配置 `TRADING_ACCOUNT_ID`（环境变量或仓库根目录 `.env`）。RECEIVER 模式先检查 `/health` 中的 `push-receiver` 和 BRIDGE 在线状态，再通过 `/quant/api/trading-request`、`/quant/api/trading-response` 请求真实 QMT 账户、持仓和委托；不使用可能来自本地缓存的 `/account`、`/positions`、`/orders`。账号必须与 BRIDGE 配置一致。探测只允许连接和这三类查询，不发送下单、撤单或策略执行请求；RECEIVER 模式保留共享 BRIDGE 连接和订阅，miniQMT 模式结束后断开独立连接。

QMT 的委托查询可能只返回当日数据，不能代替本地历史订单持久化及重启回放验收。空持仓或空委托列表属于合法查询结果，不能证明成交链路已验收。`scripts/test_qmt_readonly_probe.py` 使用本地假服务验证协议与错误处理，其测试通过也不能作为真实 QMT 验收证据。

2026-10-18 的实机只读验收记录见 [QMT_ACCEPTANCE.md](QMT_ACCEPTANCE.md)。BRIDGE v24 修复 QMT 手动停止后复用线程引起的启动误判；更新源码后应看到 `Data Bridge v24` 和 `Starting synchronous polling loop`。
