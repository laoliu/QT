# 统一策略契约

QT 允许策略继续使用 TypeScript、Backtrader、vn.py 或 AIPHA，但策略的身份和输出格式由 `strategies/catalog.json` 统一登记。

每个目录条目至少声明：

- 稳定的 `id`、实现引擎和源码位置；
- 支持的市场与 K 线周期；
- 数据需求（包括 PIT 财务数据和历史成分股）；
- 输出是 `trading_signal`、`target_position` 还是 `order_intent`；
- `primary`、`compatibility`、`experimental` 或 `live_only` 生命周期。

`GET /quant/api/strategy-catalog` 返回 TS 注册表和 Python Bridge 合并后的目录。旧策略的运行逻辑保持不变，但会由注册表补齐缺失的契约字段。

## 结果边界

回测入口会为每次运行生成 `runId`，并在 `BacktestResult.runEnvelope` 中返回：

```text
schemaVersion, runId, strategyId, strategyVersion,
engine, dataAsOf, signalFormat, signals
```

每个信号带有 `runId`、`strategyVersion` 和 `dataTimestamp`。AIPHA 研究路径输出相同版本的目标持仓结构，字段为 `rebalanceDate`、`targetWeight`、`riskFlags`、`runId`、`modelId` 和 `signalId`。

## 默认研究路径

`aipha-ml-ranking` 是 A 股低频多因子研究的唯一默认策略。历史数据必须来自带元数据旁车文件的冻结 TuShare PIT 快照；训练、OOS 预测、组合回测和目标持仓导出均由 `python/aipha/research` 完成。TS 负责调度和 UI，Paper/QMT 只消费目标持仓或执行计划。

Backtrader、vn.py 和既有 TS 技术策略继续作为兼容或实验路径，待结果与统一契约验证后再逐步合并或退役。
