# AI 聊天 Skill 与查询工具

项目 Skill 来自 `quant-trading/skills/*/SKILL.md`，不扫描 Codex 全局 Skill。
当前有 16 个 Skill，聊天工具策略覆盖 47 个工具。工具是否可用以运行时实际注册结果为准。

## Skill 目录

| ID | 显示名称 |
| --- | --- |
| financial-data-hub | 金融数据导航 |
| stock-analysis-expert | 个股综合研究 |
| market-review | 市场晨报与收盘复盘 |
| capital-flow | 资金流与主力行为 |
| sector-rotation | 板块轮动研究 |
| fundamental-valuation | 价值投资与财报分析 |
| portfolio-health | 持仓体检与组合风控 |
| stock-selection | 智能选股与候选池研究 |
| backtest-research | 策略回测与结果解读 |
| strategy-research | 策略探索与参数优化 |
| allocation-planning | 仓位计算与调仓规划 |
| trade-review | 交易复盘与绩效归因 |
| factor-ml-research | 因子研究与 ML 排序 |
| convertible-bond-research | 可转债研究 |
| options-research | 期权策略与情景分析 |
| research-diagnostics | 数据质量与研究运行诊断 |

## 发现与选择

`name` 保持英文 ID，`description` 用于自动选择。`metadata` 使用 JSON 兼容的行内 YAML，包含 `display_name` 和 `category`；`allowed-tools` 是 JSON 兼容的工具名数组。加载器支持 BOM、普通字符串与折叠/多行 description，保留旧格式 Skill 的兼容读取。

列表查询和每次发送都会重新读取 Skill 文件。新增文档后重新打开聊天页即可更新目录，无须为文档变更重启交易服务。Skill 所需工具缺失时，API 返回 `available=false` 与 `unavailableTools`，界面禁用该项，自动选择排除该项。

自动选择参考当前问题及最近六条已完成消息，最多选一个主 Skill 与两个辅助 Skill；话题变化时重新选择。手动下拉选择或消息前缀 `@skill-id 问题` 跳过自动选择。消息仍以 Skill ID 保存，界面用中文显示主/辅助角色。工具定义与执行范围取所选 Skill 的工具并集，通用模式使用已审核的查询工具。

## 工具权限与数据口径

`src/services/chat-tool-policy.ts` 同时裁剪模型参数定义并在执行前校验具体 action；新工具和新 action 不会自动进入聊天。

- 开放查询、计算、历史回测、已有模型预测/评估和调仓计划。
- 不开放交易提交/撤销、训练、模型计划导出、调仓执行、缓存清理、配置修改、自选股/笔记修改及推荐状态刷新。
- 聊天选股、推荐和回测关闭业务历史写入；策略探索强制不保存，并在完成或异常后释放临时策略。底层数据查询仍可能填充行情缓存，聊天消息和调用轨迹仍正常保存。
- 聊天仓位计算要求真实账户权益，账户不可用时不能回退成虚构的十万元。
- `research_operations` 复用固定研究部署的查询函数与参数校验，不接收可执行文件或任意部署路径，不启动 Windows 任务。查询不到证据应报告不可用，不能推断为正常。

现有两个大 Skill 已替换为真实接口工作流。旧的 `market_data/indicators` 等失效 action、未注册的分红/诊断工具名称不再被引用；相关研究用现有财报、行情、风险工具完成。`equity_screening`、`aipha_monitor` 的占位路径不作为已完成分析接入聊天；选股使用实际筛选管线，运行诊断使用已有证据投影。

因子计算显式接收真实财务字段和收益序列；缺字段、非有限值、样本不足返回缺失状态，不生成零值排名。

策略验证桥接已按当前统计库接口接通：

- `quality_gates` 要求真实夏普、回撤、交易次数、观测数量，列明实际执行和未执行的检验；不再使用虚构波动率/年数，也不承诺未实现的 HLZ 检验。
- `pbo` 要求多策略收益矩阵，报告固定随机种子的抽样 CSCV 估计方法。
- `walk_forward` 有收益矩阵时按训练段选策略，再评价隔离后的测试段；这是固定候选收益序列的选择验证，不是模型重训。只有数据长度时返回 `split_plan`、`validated=false`，与真实评价区分。

## 验证入口

在 `quant-trading` 中运行 TypeScript 类型检查及 AI 聊天、Skill 目录/动作契约、权限、业务写入隔离、管理页脚本和研究查询的 Vitest 用例。

在 `quant-trading/python` 中运行 `python -m pytest tests/test_chat_research_contracts.py -q`，覆盖真实统计输入、PBO、Walk-Forward 样本隔离、环境变量桥接和因子缺失值。

目录契约测试实例化实际工具定义，逐项检查每份 Skill 的工具名和 action 示例；测试不连接真实行情、QMT 或外部大模型。数据源在线可用性、真实模型预测质量和未来投资收益不由这些测试证明。

2026-10-20 验收：TypeScript 类型检查通过；12 个相关 Vitest 文件的 95 项测试通过，Python 契约测试 11 项通过，16 份 Skill 通过标准文档校验。运行中的 `18891` Skills API 返回 16 项且全部 `available=true`；管理页响应包含中文分组、用途说明和主/辅助 Skill 展示。保留了工作区原有改动，未执行 Git 提交或推送。
