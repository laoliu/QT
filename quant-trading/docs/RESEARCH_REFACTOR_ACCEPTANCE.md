# A 股低频 ML 重构验收与迁移状态

核对日期：2026-10-18。本文区分代码能力、离线验收和部署迁移，不能用其中一项替代另一项。旧任务描述中的六项缺口不再代表当前代码状态。

## 原方案逐项核对

| 原方案 | 当前代码与证据 | 尚未完成或证据边界 |
| --- | --- | --- |
| 1. 唯一研究核心与数据契约 | `python/aipha/research/pipeline.py` 编排快照、标签、Walk-Forward、OOS、组合回测、报告和目标计划；`contracts.py` 定义统一记录。复用 `data`、`universe`、`features`、`models`、`validation`、`execution`。 | 实际编排和报告实现集中于 `research`，并非原方案示意目录的逐文件搬迁；旧兼容模块保留。 |
| 2. ML 入口收敛 | `src/tools/ml-ranking.ts`、`python/aipha/engines/aipha_ml_engine.py` 使用规范入口，默认只公开 LightGBM LambdaRank 和 ElasticNet；旧训练脚本、模型和策略要求显式兼容开关。7 个实验模型及旧独立 LightGBM Ranker 实现已迁入 `aipha/experiments/models`，旧子模块保留名称别名供历史导入和 pickle 解析。独立 Ranker 不进入默认包导出，旧包级名称惰性读取并发出弃用提示；构造和原生 Booster 加载分别要求调用方显式 `legacy_compat=True`。`test_experimental_model_boundary.py` 禁止默认路径导入实验模块；`test_legacy_ranker_boundary.py` 在独立进程读取旧限定名 pickle 和原生 Booster，验证预测及源文件字节不变。 | 模块隔离不等于存量任务或模型产物已完成规范迁移；无 manifest 的历史模型仍被规范 loader 拒绝。旧 Ranker 的自然日切分和旧标签仅供显式回放，不构成规范防前视验收。缺少可选 Torch/PyG 时，对应模型兼容测试跳过。 |
| 3. TuShare PIT 与防前视 | `data/tushare_pit_provider.py` 采用实际/声明公告日较晚值，保留修订；`research/snapshots.py` 生成历史成分、ST、上市交易日、当日复权、财务特征和签名日历。`test_financial_revisions.py`、`test_market_calendar.py`、`test_listing_sessions.py` 验证时点和完整性。 | 本地签名证明产物在签名后未被篡改，不证明上游供应商提供了全部历史修订；真实 TuShare 数据覆盖率仍需部署验证。 |
| 4. 标签与月度调仓 | 默认研究 YAML 为日频、月度、收盘信号、次日开盘、三年训练、一月预测、20 日标签和 purge。`test_execution_timing.py`、`test_research_pipeline.py` 验证交易日边界、无效标签和训练/测试隔离。 | 集成 fixture 缩短训练窗口以控制测试时间，不替代真实三年窗口研究。 |
| 5. 唯一组合回测 | `research/backtest.py` 处理原始价格、手数、现金、T+1、涨跌停、停牌、换手、费用、滑点、基准与公司事件；组合构建约束单票和行业。`research/style.py` 将配置的风格 z-score 预算用于目标权重、导出、CSCV 和 Paper 计划复核。相关证据见 `test_style_constraints.py`、`test_portfolio_cscv.py`、`test_corporate_action_accounting.py`、`test_execution_timing.py`、`test_research_pipeline.py`。 | 风格预算默认未开启，约束目标组合；受执行限制及价格变化影响的实际持仓不保证始终满足预算，尚无 broker 实盘侧持续风格风控。缺失行业数据时明确报告不可用；不推断历史行业。完整持有期红利税、配股、零碎送股仍未实现。 |
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
- 采用非恒定市值特征和显式风格预算，逐月验证报告中的目标风格暴露，导出的计划继续通过 Paper 的独立复核。
- 签名输入快照、OOS、组合回测、现金分红事件、报告和按日 Paper 计划实际生成。
- 同一计划可以进入规范 SQLite Paper 账本；默认预演不创建账本，信号收盘只接受计划，下一交易日按原始开盘价成交并保留 plan/run/model/signal 标识。
- 重新打开账本可以只读重算成交，数据库字节保持不变。

价格样本是确定性的，RankIC 可能不可定义，报告必须输出 JSON `null`。另一个随机截面端到端测试负责验证非退化的每折 IC、模型加载、基准、暴露、多空诊断和可复现 CSCV；不能用本测试声称策略有预测收益。

## 迁移与上线的实际状态

2026-10-18 运行 `python scripts/audit_ml_migration.py`：`configs/paper` 下共 36 份配置，16 份 `not_applicable`，20 份 `legacy_compat`，0 份 `canonical`。这是配置库存统计，不表示 20 个任务当前都在运行，也不表示它们的旧模型文件都存在。

进一步核对这些配置引用的 9 个不同模型目录，当前 Python 项目路径下全部不存在；ML 组件均未声明规范快照路径。因此这些配置暂时没有可供实际迁移/推理核对的输入。不能生成空模型目录、附加新 manifest 或只删除兼容标志来宣称迁移完成。

真实迁移应依次进行：选择需保留的旧任务，定位原始 DB/模型，核对可信训练与特征证据，必要时通过规范 TuShare 链路重训，配置独立的新 Paper 账本，验证实际推理与计划，再运行只读迁移审计。不能只删 `legacy_ml_compat` 标志。

审计区分两类契约：`canonical` 校验 ML 组件的签名快照、模型 manifest 与文件哈希；`canonical_paper` 使用启动器共享的 profile 解析器，校验签名快照、当日计划、已有账本全部回执和重放，再执行内存记账预演。两者均不反序列化模型或证明推理成功；后者明确返回 `model_provenance_verified=false`、`inference_exercised=false`、`committed=false`。没有当日计划的新空账本可通过现金估值预演，不能算作模型迁移完成。`--strict` 只接受上述两种已校验的消费者契约，拒绝 legacy、混合、畸形配置和共享账本路径，不代表真实逐日运行验收。

路径相对 Python 项目根目录解析；快照模板必须传 `--trade-date`，固定路径默认使用已验证的快照结束日，快照必须恰好结束于审计日。新版 profile 声明即纳入审计，畸形或嵌套声明不能被归为 `not_applicable`。`test_audit_paper_profile.py` 覆盖这些边界，并检查审计前后文件字节不变、不启动进程、不读取模型。

进一步沿数据面板核对，实际原始数据位于 `G:/qtdata/cache/equity_tushare`，含 178 个月日线及大量旧财务缓存；用户目录的 SQLite 行情缓存另有 131 个品种。这些数据可作为补齐规范输入的基础，不能继续描述为没有历史行情。配套复权、每日指标、交易日历及新版 PIT 财务仍有缺口，详见 [DATA_CACHE_ACCEPTANCE.md](DATA_CACHE_ACCEPTANCE.md)。规范 `train_tushare` 的默认缓存已接到统一路径解析，避免再次仅查项目内空目录。

普通终端进程没有 TuShare token 或快照签名密钥，但工作区 `.env` 中确有 token，应用启动器会加载并解析配置中的环境变量引用。使用解析后的实际凭据查询 TuShare SSE 交易日历成功；早先直接提交配置引用文本导致的 token 错误不能代表真实凭据状态。完整历史日历补全随后受到上游访问限制，仍无可直接训练的已签名规范快照。签名密钥应由部署环境提供，测试密钥仅用于本地 fixture。尚无证据证明规范 Paper 已连续真实交易日运行；历史一次性回放不计作逐日运行记录。

仓库 `config.json` 和本机 `config.local.json` 的交易字段在本次核对时均为 paper/broker paper、确认开启、执行计划必需、自动启动关闭。本机覆盖文件未纳入 Git；该结论是本机时点检查，其他部署必须检查各自覆盖配置。

QMT 只读实机证据及其边界见 [QMT_ACCEPTANCE.md](QMT_ACCEPTANCE.md)。真实下单、撤单、成交回报和连续稳定性没有验收；原方案首期明确不以真实下单为完成条件。

## 可复现检查命令

在 `quant-trading/python` 运行：

```powershell
python -m pytest tests/research tests/runner tests/ops tests/data/test_pit_financial.py tests/data/test_tushare_pit_provider.py tests/data/test_financial_revisions.py tests/data/test_parquet_data_updater.py tests/models/test_ml_stock_selector.py tests/models/test_lgbm_ranker.py tests/models/test_feature_groups.py tests/models/test_wf_stats.py tests/models/test_gru_ranker.py tests/models/test_gat_ranker.py tests/models/test_double_ensemble.py tests/models/test_ensemble.py tests/backtest/test_db_migration.py tests/backtest/test_migrate_sqlite_cli.py tests/scripts/test_ops.py tests/scripts/test_research_retrain.py tests/scripts/test_research_cache_paths.py tests/scripts/test_audit_ml_migration.py tests/scripts/test_audit_paper_profile.py tests/scripts/test_paper_launcher.py tests/execution/test_qmt_bridge_contract.py tests/execution/test_paper_gateway.py -q --basetemp=.pytest-tmp-goal --tb=short
python scripts/audit_ml_migration.py
```

在 `quant-trading` 运行：

```powershell
npm.cmd run typecheck
npm.cmd exec -- vitest run src/http/pages/admin/local-data.test.ts src/http/admin-routes.test.ts src/http/ml-research-api.test.ts src/config-validator.test.ts src/broker/qmt-broker.test.ts src/broker/remote-qmt-broker.test.ts src/services/order-manager.test.ts src/services/order-manager-paper.integration.test.ts
```

本次 Python 检查结果（包含独立 Ranker 隔离、旧模型跨进程回放及上述研究链路）：**1129 passed、8 skipped、44 warnings**。Ranker 及实验导入边界另经 **33 passed、4 skipped** 定向检查；缓存及路径此前 **18 项测试**通过。TypeScript 最近一次类型检查及以上 8 个文件的 **152 项测试**通过，本轮没有 TypeScript 改动；页面脚本测试验证失败响应、旧包装、空缓存及网络错误不会显示假成功。这是上述选定范围的回归结果，并非全仓库所有测试或上线验收。

Linux 原生独占发布和 Docker cron 尚未在本机执行。8 项跳过包含 Windows 符号链接权限和缺少可选 Torch/PyG 的模块/参数测试；测试结果不能扩展成这些平台或可选模型的实际运行验收。
