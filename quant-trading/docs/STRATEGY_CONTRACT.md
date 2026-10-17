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

QMT 登录并显示交易连接后，可运行 `python scripts/qmt-readonly-probe.py` 完成只读实机验收；探测只查询账户、持仓和历史订单，然后断开连接。
