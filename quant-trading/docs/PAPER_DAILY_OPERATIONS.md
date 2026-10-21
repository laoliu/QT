# CSI500 逐日 Paper 运营

部署日期：2026-10-21。因 2026-10-20 代理超时导致中间交易日漏跑，已按用户确认将观察期重新起算为 **5 个真实交易日**，首日为 2026-10-21；旧账本保留在 `G:/qtdata/research/operations/csi500/paper.sqlite3`，新账本使用独立目录 `G:/qtdata/research/operations/csi500-20261021`。尚未取得真实逐日运营回执，不能提前宣布验收完成。历史预演不计天数。

## 部署与日常行为

独立部署配置在 `G:/qtdata/research/operations/csi500/deployment.yaml`，只允许 `mode: paper`；当前 `start_date=2026-10-21`、`output_root=G:/qtdata/research/operations/csi500-20261021`。旧配置副本和切换核验保存在 `G:/qtdata/research/operations/csi500/recovery_proposal_20261021/`。固定使用真实研究 `f69fada02c684d7697ae287ae176b53f` 的模型，manifest SHA-256 为 `f41ee827b38b45ff42e4966d19be4a79bb38657cddd2dfff933a519a4531a547`；配置哈希、特征版本/列策略、历史股票池和训练截止时间均校验。升级模型必须显式变更部署。

Windows 任务 `QT-Research-Paper-CSI500` 每天 18:30、19:30、20:30 启动。重复调用按同日冻结快照、预测与计划核对并幂等提交；同一部署使用 OS 文件锁。任务禁止并发，失败间隔 20 分钟重试两次，单次上限两小时。非交易日跳过，18:00 前不取数；漏掉首日或中间交易日则拒绝历史补记，需明确调整起点/对账。

任务使用当前用户的 Interactive、Limited 身份，**电脑必须开机且该用户已登录**，锁屏可以运行，注销后不能运行。S4U 注册曾被 Windows 拒绝访问，改为用户级 Interactive 后注册和手动触发成功；并未获得无人登录运行的保证。没有重启或修改原交易服务。

未到首日、未到窗口和非交易日均写入明确的终止状态与 `latest.json`，不会把正常跳过留在“检查日历”阶段。提交前再次读取时钟；若准备过程跨到下一自然日，或时钟倒退，则拒绝提交，避免产生前一日补记。CLI 始终读取真实时钟；可注入时钟只用于离线测试，不计运营天数。

本机科学计算临时只使用逻辑 CPU 16、17、18、19，亲和性仅作用于新 Paper 进程；原因和限制见 [原生崩溃诊断](NATIVE_CRASH_DIAGNOSIS.md)。固定环境为 `G:/qtdata/research/validation-env-py313/Scripts/python.exe`。这不是硬件修复，也不是其他机器通用的 CPU 编号。

2026-10-20 调度失败由桌面代理 `127.0.0.1:10808` 读超时引起；直连探测恢复后，按用户确认将正式观察起点切换至 2026-10-21。旧账本 SHA-256 为 `58d717550af441c297b5bbb84a09d6411cdb0a9051a9c8f5b572edb99757608f`，切换后保持不变；切换没有联系 broker 或 QMT。

每天刷新 TuShare 历史资料与当前日有界 PIT 财务查询，复用原始月缓存；日期迭代严格使用完整 SSE 日历，不回退到工作日或其他行情源。覆盖原始缓存前按文件哈希归档。财务冲突仍按指标隔离为缺失；仅复用已审阅且原值/证据匹配的公司事件校正，新增矛盾拒绝处理。

QT 公共 Python 网络层对外部 `requests` 和 `urllib` 链接统一先按当前代理配置请求（包括 Windows 注册表中的系统代理）；代理连接失败或超时时，用相同参数直连重试一次，并在当前进程记住该主机直连。研究 CLI 额外记录 `tushare_proxy_fallback` 事件。回环、局域网、QMT 和 Receiver 地址保持直连；不修改系统代理、全局 Git 配置或其他服务。若直连仍失败，继续由调用方既有限流/重试机制处理。调度日志保留 Python 原始错误信息和真实退出码，不附加 PowerShell `NativeCommandError` 调用栈。网络恢复不能补回已错过的正式运营日；旧账本和验收证据仍保留，恢复前需明确调整起点或对账。

每日生成签名快照、固定模型截面预测及排名。实际月末收盘才接收计划，后续开市按原始开盘推进 Paper 账本；该入口不加载 broker。10 月 21—27 日若均正常开市且按时运行，可验证五日补数/推理/账本连续性；由于未到月末且新账本初始为空，这一周预计没有持仓或成交，不能扩展成带仓运营或真实成交验收。月末到次日成交已有独立历史 Paper 和离线测试证据。

## 运行与暂停

在仓库根目录，用以下命令复现本机任务注册；脚本只允许复用相同入口的任务名，不覆盖其他动作：

```powershell
./scripts/install-research-paper.ps1 `
  -Deployment 'G:/qtdata/research/operations/csi500/deployment.yaml' `
  -Python 'G:/qtdata/research/validation-env-py313/Scripts/python.exe' `
  -LogRoot 'G:/qtdata/research/operations/csi500/scheduler' `
  -CpuAffinity '16,17,18,19'
Get-ScheduledTaskInfo -TaskName 'QT-Research-Paper-CSI500'
```

创建 `G:/qtdata/research/operations/csi500/STOP` 可阻止后续准备/提交；准备结束、提交前再次检查。它不是中断已开始 SQLite 事务的即时按钮。暂停调度可使用 `Disable-ScheduledTask -TaskName 'QT-Research-Paper-CSI500'`。不要删除账本来掩盖漏跑。

`scripts/run-research-paper.ps1` 记录 Python 进程的真实退出码，包括不能被 Python 异常处理捕获的原生崩溃。`scheduler/latest_process_result.json` 和每次独立日志用于进程诊断；`progress.json` 记录阶段/PID，不等同成功回执；`sessions/YYYY-MM-DD/operation.json` 与 `paper.sqlite3` 用于账本核验。`latest_failure.json` 是最后一次失败，不会因之后成功自动消失，必须比较时间。包含大量审计元数据的回执不应直接完整打印。

`research_paper_daily.py --preflight-date YYYY-MM-DD` 仅在独立 `preflight/` 中预演，与 `--execute` 同用会拒绝；日常必须由实际当天盘后产生回执。凭据从本机 `.env` 读取，不复制入配置或报告。

## 验收与后续检查

2026-10-21 起的新部署等待首个盘后正式回执；旧部署的 2026-10-20 失败不计入新观察期。当前代码重新读取签名的真实 2026-10-16 输入，校验模型、推理并 Paper 预演退出 0，覆盖 494 只股票；非月末目标数为 0，未创建账本。结果在独立 `code_validation/preflight/`，旧预演产物保留。

本轮全部 `tests/research`：**356 passed、5 skipped、2 warnings**，固定科学栈、仅 E 核、退出码 0。涵盖三日 fixture 月末到下次开盘成交、同日幂等、漏跑拒绝、准备中 kill switch、预测变更拒绝、部署模型哈希、严格日历、原始缓存归档、ST 历史分页，以及 PowerShell 捕获普通/原生退出码。5 项跳过为既有 Windows 符号链接权限和可选依赖测试。首次执行因系统默认 pytest 临时目录拒绝访问而在 fixture 阶段失败，改用独立 G 盘测试临时目录后执行通过；未修改系统目录权限。日志为 `G:/qtdata/research/native_diagnostics/operations_final_regression_v2.log`，不代表全仓库回归。

后续时钟/跳过状态修正：只重跑受影响的三个 `test_daily_*` 文件，**21 passed**，包括跨午夜、收盘窗口前和同日倒拨均不能提交，以及正常同日晚到的调用可以提交。Windows 任务再次实际触发退出 0，进度已明确为 `not_started`。此增量结果与上述完整研究回归分别记录，不把新增代码计作已经再次跑完全部 356 项。

通过 `aipha.research.paper_monitor.inspect_paper_book(..., required_days=5)` 只读重放并核对同日盘后提交、连续交易日、NAV 和新鲜度；回放/补记不计。默认新鲜度为 24 小时，长休市需要显式解释监控阈值，不能伪造新回执。

已创建本聊天的每日 22:40 自动检查 `csi500-paper`，仅在失败、需用户行动或五日完成时通知；无实质变化保持静默。该检查依赖 Codex 本机可执行环境可用，不替代 Windows 任务。五日通过后暂停检查；不自动开启 live。全部实时运行产物保存在 G 盘，代码和小型验收证据入库，不提交凭据、模型或大型快照。
