# CSI500 真实研究与独立 Paper 验收

核对日期：2026-10-18。首期真实研究闭环已经跑通；策略表现不支持实盘。机器可读摘要见 [evidence/research_20261018.json](evidence/research_20261018.json)。本记录不代表连续运营或真实 QMT 成交验收。

## 输入与审计口径

- 原始缓存：`G:/qtdata/cache/equity_tushare`。数据源为 TuShare PIT；行情、历史成分、名称变更、上市资料、交易日历、行业、基准与财务输入已经补齐本轮区间。
- 签名快照：`G:/qtdata/research/snapshots/csi500_20230101_20261016_issuer_units_v2.parquet`，730,711 行、814 个历史候选、270 列；实际行情日期 2023-01-03—2026-10-16。原快照及中间版本保留。
- 用户确认的财务口径为逐指标隔离：5,167 个冲突指标事件置缺失，涉及 754 个代码；其他指标和股票保留，不猜测修订顺序。全部事件进入签名元数据及报告。
- 核对发行人公告后，逐事件校正 26 笔公司事件，包括九号公司基础股票/CDR 单位、差异化分红/送转的摊薄除权参考值，以及源端重复合计。现金/送股权益与除权参考量分开；不从复权因子反推分红，不对单一代码全历史乘固定系数。
- 公告全文通过东方财富的发行人公告镜像取得，明确排除 AI 摘要；保留公告日、链接、全文哈希、摘录及原始响应。该镜像用于事件审阅，不替代 TuShare 行情和财务主源。本地签名不证明供应商历史修订已绝对完整。
- 复权因子的已声明精度上限为 0.001；按原值编码精度计算有界舍入区间，并在持仓除权日独立核对 `pre_close`。错误条款、缺失证据或无法解释的因子变化仍拒绝回测。

原始快照文件 SHA-256：`865aab0e854f41b2d216d7f665137bee1e8d174c6b5a0ec3571a696591894e05`。

校正快照文件 SHA-256：`18f4318e1171c3735ac49ef0c67fd9243de3a74071b01d8ca054ebb5fcc61049`；内容 SHA-256：`b0688f86a092f9ca253ae0a3d770404caad14242e54cfaf309d51113a16b9037`。校正谱系和事件审计均受签名保护。

## 真实训练、OOS 与组合回测

运行 ID：`f69fada02c684d7697ae287ae176b53f`。完整产物在 `G:/qtdata/research/runs/f69fada02c684d7697ae287ae176b53f`，含输入快照、7 折模型、manifest、OOS、组合收益、交易、公司事件、CSCV、报告、目标持仓及执行计划。

使用默认 CSI500 规范配置：三年训练、一个月预测、LightGBM LambdaRank 300 轮、月末调仓、次日开盘、Top-30、单票上限 5%、行业上限 20%、单日换手上限 30%。三年窗口按现有训练常量 244 日/年，即 732 个训练交易日；每折实际 purge 为 25 个开市日。标签持有 20 日、跳过 1 日。没有为通过验收缩短训练或挑选获利样本。

模型实际使用 173 个特征，覆盖六组，特征策略版本 `cn_equity_feature_groups/3`；平均缺失率 1.18146%。产生 67,736 条 OOS 预测、210 条目标持仓、7 份实际月末计划、329 笔交易及 274 次因换手/手数约束未执行记录。OOS 信号为 2026-02-24—2026-09-09；估值至 2026-10-16，157 个收益观测，含 21 个交易日的估值尾部。标签截止后的估值尾部不产生新信号。

| 指标 | 实际结果 |
| --- | ---: |
| RankIC / ICIR | 0.04107 / 0.24387 |
| 区间净收益 | -12.50% |
| 同期基准 / 收益差 | -19.30% / +6.81 个百分点 |
| 年化收益 / 波动率 | -19.29% / 12.96% |
| Sharpe / 最大回撤 | -1.588 / -14.21% |
| 总成本（含滑点） | 3,590.50 元 |
| 实际入账现金分红（持有期税前） | 8,671.892 元 |
| 固定 Top-K 组合选择 PBO | 0.80567 |

年化指标沿用当前组合引擎的 252 个交易日口径，报告中显式记录；它与训练窗口的 244 日/年约定用途不同。CSCV 对预先固定的 `[15, 30, 60, 120]` 四个 Top-K 候选、16 个分区的 12,870 种组合全部枚举。PBO 仅度量该候选集合的组合选择，不代表所有因子/模型搜索的过拟合概率。行业/风格暴露、分层收益和统计多空诊断已输出；统计多空不等于 A 股可直接卖空策略。

独立复现重新加载 7 折模型，逐条重算全部 OOS 分数，最大误差为 0；另核对 RankIC、训练/隔离/测试不重叠、7 个实际月末计划、权重上限、329 笔交易来源及原快照文件哈希。证据：下述暂存目录中的 `real_research_reproduction.json`。

## 实际推理与 Paper

重新加载已保存模型，对 2026-10-15/16 的 988 行实际输入推理，两日各 494 个不同有限分数，特征版本/顺序及模型来源校验通过；非月末导出目标为 0。

独立账本：`G:/qtdata/research/acceptance/f69fada02c684d7697ae287ae176b53f_verified_v2/historical_paper.sqlite3`。2026-02-27 收盘只接收计划，2026-03-02 实际原始开盘成交 25 笔，再推进一日，共 3 份回执；全部成交带 run/model/signal/plan 标识。只读重放和 profile 审计前后账本字节一致，外部独立 profile 审计为 `canonical_paper: 1`。没有访问 broker。

本机仓库配置与 ignored 覆盖配置均核对为 paper/broker paper、确认开启、执行计划必需、自动启动关闭。20 份历史 ML 配置仍是兼容库存；用户确认没有旧模型/研究数据库，未伪造迁移。独立验收 profile 没有加入常驻启动清单。

## 验证与剩余范围

所有原始响应、逐事件公告证据、校正 JSON、研究日志及复现脚本保存在 `G:/qtdata/cache/equity_tushare/migration_inputs/20261018T090159Z`；入口包括 `resume_canonical_research.py`、`reproduce_saved_oos.py`、`verify_real_paper.py`。摘要记录相关文件哈希。大型数据、模型、账本及凭据不提交 Git。

本轮选定大回归：**1239 passed、8 skipped、44 warnings，64.21 秒，退出码 0**。范围为全部 `tests/research`、`tests/runner`、`tests/ops`，以及重构验收主文档所列数据/PIT、ML、迁移脚本及执行契约测试；另包括 `tests/data/test_tushare_equity_provider.py`。8 项跳过涉及 Windows 符号链接和可选 Torch/PyG；不等于全仓库或所有可选引擎验收。本轮自有代码没有 TypeScript 改动。

验证在新建独立环境 `G:/qtdata/research/validation-env-py313` 中完成，Python 3.13.4、NumPy 2.2.6、pandas 2.3.3、SciPy 1.15.3、LightGBM 4.6.0、scikit-learn 1.6.1、PyArrow 20.0.0。完整依赖已固定在 `python/requirements-research-validation.txt`，可在新的独立 venv 中用 `pip install -r requirements-research-validation.txt` 重建。完整命令与输出保存在暂存目录的 `regression_command.json`、`isolated_regression_v2.log`；跨环境 OOS 复现见 `real_research_reproduction_isolated.json`。

本机全局环境（同 Python，NumPy 2.4.1/pandas 3.0.1/SciPy 1.17.1/scikit-learn 1.8.0/PyArrow 23.0.1）的大回归多次出现 Windows 原生访问冲突，位置包括 NumPy `vstack` 与 LightGBM；Python debug allocator 下仍可复现。独立环境的相同范围曾通过，且全部真实 OOS 分数一致，但后续真实逐日预演中独立环境也出现原生崩溃，不能将隔离依赖当成修复。新增 WHEA 处理器内部校验错误、跨版本对照与 E 核临时绕行证据见 [诊断记录](NATIVE_CRASH_DIAGNOSIS.md)。没有覆盖全局依赖或重启交易服务。

连续交易日自动补数/推理/Paper 任务已部署，首日 2026-10-19，五日真实运营验收仍待完成，见 [运营说明](PAPER_DAILY_OPERATIONS.md)。真实 QMT 下单/撤单/成交回报、Linux/Docker 部署仍未验收。完整持有期红利税、配股及零碎送股处理仍有限制，风格约束默认未启用。原方案首期不以真实下单为完成条件；该研究的负收益和高 PBO 不构成开启 live 的依据。
