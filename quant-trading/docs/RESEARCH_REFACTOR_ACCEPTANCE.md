# A 股低频 ML 重构验收与迁移状态

核对日期：2026-10-18。本文区分代码能力、离线验收和部署迁移，不能用其中一项替代另一项。旧任务描述中的六项缺口不再代表当前代码状态。

## 原方案逐项核对

| 原方案 | 当前代码与证据 | 尚未完成或证据边界 |
| --- | --- | --- |
| 1. 唯一研究核心与数据契约 | `python/aipha/research/pipeline.py` 编排快照、标签、Walk-Forward、OOS、组合回测、报告和目标计划；`contracts.py` 定义统一记录。复用 `data`、`universe`、`features`、`models`、`validation`、`execution`。 | 实际编排和报告实现集中于 `research`，并非原方案示意目录的逐文件搬迁；旧兼容模块保留。 |
| 2. ML 入口收敛 | `src/tools/ml-ranking.ts`、`python/aipha/engines/aipha_ml_engine.py` 使用规范入口，默认只公开 LightGBM LambdaRank 和 ElasticNet；旧训练脚本、模型和策略要求显式兼容开关。`test_ml_screener_compatibility.py`、`test_research_completion.py` 验证边界。 | GRU/GAT/Ensemble 等文件仍在原目录，以显式开关隔离，尚未物理迁入独立实验目录；不能据此宣称旧模块已全部删除或迁移。 |
| 3. TuShare PIT 与防前视 | `data/tushare_pit_provider.py` 采用实际/声明公告日较晚值，保留修订；`research/snapshots.py` 生成历史成分、ST、上市交易日、当日复权、财务特征和签名日历。`test_financial_revisions.py`、`test_market_calendar.py`、`test_listing_sessions.py` 验证时点和完整性。 | 本地签名证明产物在签名后未被篡改，不证明上游供应商提供了全部历史修订；真实 TuShare 数据覆盖率仍需部署验证。 |
| 4. 标签与月度调仓 | 默认研究 YAML 为日频、月度、收盘信号、次日开盘、三年训练、一月预测、20 日标签和 purge。`test_execution_timing.py`、`test_research_pipeline.py` 验证交易日边界、无效标签和训练/测试隔离。 | 集成 fixture 缩短训练窗口以控制测试时间，不替代真实三年窗口研究。 |
| 5. 唯一组合回测 | `research/backtest.py` 处理原始价格、手数、现金、T+1、涨跌停、停牌、换手、费用、滑点、基准与公司事件；组合构建约束单票和行业。相关证据见 `test_corporate_action_accounting.py`、`test_execution_timing.py`、`test_research_pipeline.py`。 | **风格暴露目前是报告诊断，尚未成为组合构建的硬约束。** 缺失行业数据时明确报告不可用；不推断历史行业。完整持有期红利税、配股、零碎送股仍未实现。 |
| 6. 不可变模型与研究报告 | `research/artifacts.py` 校验 schema、特征顺序/版本/策略和模型字节；pipeline 写入输入快照、每折模型、OOS、收益、交易、报告和计划。`test_end_to_end_oos_run_writes_reproducible_artifacts` 重新加载模型并重算 CSCV 结果。 | CSCV/PBO 只度量已记录的固定 Top-K 候选组合选择，不覆盖未记录的模型/因子搜索；样本不足或不可识别的统计量返回不可用。 |
| 7. 执行隔离与迁移工具 | `research/execution_plan.py`、`paper_book.py`、QMT plan adapter 和 Node 安全配置限制执行模式、风险及来源；原始 QMT 单订单入口被拒绝。SQLite、模型、配置和 Paper 日历均有显式迁移工具。 | 工具通过 fixture 不等于存量文件已迁移。20 份历史 ML Paper 配置仍是 legacy；实际旧研究 DB/模型尚未逐项迁移核对。原文件不自动覆盖或删除。 |
| 8. 统一配置和入口 | `python/aipha/configs/research/a_stock_ml.yaml` 是研究策略配置；规范 Paper profile 只引用它并配置部署路径。历史 YAML 通过显式兼容入口保留。`test_config_identity.py`、`test_paper_launcher.py` 检查配置传递、默认预演及路径。 | 示例 profile 没有被自动加入启动清单；不能把安装示例当成任务部署完成。 |

## 财务 PIT 到 Paper 的完整离线证据

`python/tests/research/test_research_completion.py::test_tushare_fixture_runs_to_paper_plan` 使用 300 个本地交易日、6 只股票和真实 `TuSharePITProvider`，不访问在线供应商、真实账户或 QMT。

该测试证明：

- 原始财务行无序、有同报告期修订、新报告期、较晚的旧报告更正和快照结束后的公告。
- 公告前 `factor_roe` 为空，公告后才能使用；修订前后分段检查所有股票的因子值。
- 更正旧报告不会覆盖已可见的新报告，未来公告不会进入历史特征。
- 利润表等待 `max(ann_date, f_ann_date)` 才可见，修订也等待实际公布日。
- 真正训练的 LightGBM manifest 包含质量因子，原始财务字段/公告键不被默认选为模型输入；报告给出确切缺失率和因子分组。
- 签名输入快照、OOS、组合回测、现金分红事件、报告和按日 Paper 计划实际生成。
- 同一计划可以进入规范 SQLite Paper 账本；默认预演不创建账本，信号收盘只接受计划，下一交易日按原始开盘价成交并保留 plan/run/model/signal 标识。
- 重新打开账本可以只读重算成交，数据库字节保持不变。

价格样本是确定性的，RankIC 可能不可定义，报告必须输出 JSON `null`。另一个随机截面端到端测试负责验证非退化的每折 IC、模型加载、基准、暴露、多空诊断和可复现 CSCV；不能用本测试声称策略有预测收益。

## 迁移与上线的实际状态

2026-10-18 运行 `python scripts/audit_ml_migration.py`：`configs/paper` 下共 36 份配置，16 份 `not_applicable`，20 份 `legacy_compat`，0 份 `canonical`。这是配置库存统计，不表示 20 个任务当前都在运行，也不表示它们的旧模型文件都存在。

真实迁移应依次进行：选择需保留的旧任务，定位原始 DB/模型，核对可信训练与特征证据，必要时通过规范 TuShare 链路重训，配置独立的新 Paper 账本，验证实际推理与计划，再运行只读迁移审计。只有移除兼容依赖且满足快照/模型契约的任务才能通过 `--strict`；不能只删 `legacy_ml_compat` 标志。

当前进程没有配置生产 TuShare token 或快照签名密钥，无法据此运行真实数据训练。密钥应由部署环境提供，测试密钥仅用于本地 fixture。尚无证据证明规范 Paper 已连续真实交易日运行；历史一次性回放不计作逐日运行记录。

仓库 `config.json` 和本机 `config.local.json` 的交易字段在本次核对时均为 paper/broker paper、确认开启、执行计划必需、自动启动关闭。本机覆盖文件未纳入 Git；该结论是本机时点检查，其他部署必须检查各自覆盖配置。

QMT 只读实机证据及其边界见 [QMT_ACCEPTANCE.md](QMT_ACCEPTANCE.md)。真实下单、撤单、成交回报和连续稳定性没有验收；原方案首期明确不以真实下单为完成条件。

## 可复现检查命令

在 `quant-trading/python` 运行：

```powershell
python -m pytest tests/research tests/runner tests/ops tests/data/test_pit_financial.py tests/data/test_tushare_pit_provider.py tests/data/test_financial_revisions.py tests/models/test_ml_stock_selector.py tests/models/test_lgbm_ranker.py tests/models/test_feature_groups.py tests/models/test_wf_stats.py tests/backtest/test_db_migration.py tests/backtest/test_migrate_sqlite_cli.py tests/scripts/test_ops.py tests/scripts/test_research_retrain.py tests/scripts/test_audit_ml_migration.py tests/scripts/test_paper_launcher.py tests/execution/test_qmt_bridge_contract.py tests/execution/test_paper_gateway.py -q --basetemp=.pytest-tmp-goal --tb=short
python scripts/audit_ml_migration.py
```

在 `quant-trading` 运行：

```powershell
npm.cmd run typecheck
npm.cmd exec -- vitest run src/http/ml-research-api.test.ts src/config-validator.test.ts src/broker/qmt-broker.test.ts src/broker/remote-qmt-broker.test.ts src/services/order-manager.test.ts src/services/order-manager-paper.integration.test.ts
```

本次以上检查结果：Python **1025 passed、2 skipped**；TypeScript 类型检查通过，6 个测试文件、**140 passed**。这是上述选定范围的回归结果，并非全仓库所有测试或上线验收。

Linux 原生独占发布和 Docker cron 尚未在本机执行；Windows 无符号链接创建权限时，对应测试会跳过。测试结果不能扩展成这些平台的实际运行验收。
