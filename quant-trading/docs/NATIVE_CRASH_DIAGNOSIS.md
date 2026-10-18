# Windows 科学计算原生崩溃诊断

日期：2026-10-18。更新前已确认本机存在处理器硬件错误；用户现已更新 BIOS，全核复测结果见文末。具体病因及长期修复效果仍未确定，也没有证明每一次崩溃都来自同一原因。

## 直接证据

本机为 Intel i9-13900KF、MSI PRO Z790-P WIFI (MS-7E06)，BIOS A.50，发布日期 2023-03-23。Windows System 日志多次出现 WHEA-Logger Event 19：`Processor Core / Corrected Machine Check / Internal parity error`，涉及 APIC 16、25，例如上海时间 18:54:25、20:42:07、21:34:07。

通过逐逻辑 CPU 绑定执行 CPUID 读取 APIC，映射到 Windows 逻辑 CPU 4、7；Windows CPU Set 信息确认均为 P 核。APIC 编号不能直接当作 Windows CPU 编号。原始事件、XML、映射分别在 `G:/qtdata/research/native_diagnostics/whea_events.json`、`cpu_apic_map.json`、`cpu_sets.json`。

外部 Windows Debug API 调试器采集到二次机会 `0xc0000005`，故障点分别落在 Python DLL、NumPy；NumPy 一次故障位于 `ret`，寄存器/栈地址异常，采集的执行页是可执行只读映像，代码与磁盘内容相符。最小组合压力脚本是 DataFrame 内容哈希加 NumPy `vstack/mean`，未做模型训练；纯 NumPy 压力循环没有复现。研究进程模块清单未加载 XtQuant。另有历史 XtQuant MSVCP140.dll 故障记录，不能据此归因本次研究崩溃。

22:01 重新读取当日完整的命名 Python Application Error 1000 与 WHEA 19 处理器事件，得到 25 次 Python 错误、6 次处理器校验错误；此前的十条应用日志截取并不完整。21:34:06.815 的全局 NumPy 崩溃与 21:34:07.694 的 APIC16/MCABank0 WHEA 相差 **0.879 秒**。其 PID `0x2d14` 即十进制 11540，与 `hash_stress_global_v2/exception.json` 捕获到 `ret`/异常栈的 PID 一致。21:34:13.562、21:34:32.888 的 Python 3.13.11 对照进程又出现 NumPy 访问冲突。这不是仅凭两个任意时间段的故障类别相似作出的判断。

25 次错误中，距最近 WHEA 一秒内为 1 次、一分钟内为 3 次；其余没有这种近时关联。事件写入可能滞后且日志不涵盖调试器截获的全部退出，不能据此计算真实故障概率，或把近时关联当成逐条因果证明。所有六条 WHEA 均为 MCA bank0、ErrorType12，APIC16/17、24/25 涉及相邻 SMT 线程；没有通过再压测故障 P 核去验证。原始完整查询与带哈希摘要见 [时间关联证据](evidence/native_correlation_20261018.json)。

## 对照实验

| 条件 | 结果 | 能说明什么 |
| --- | --- | --- |
| 全局 Python 3.13.4 科学栈，研究测试重复 5 次 | 4 次通过、1 次原生访问冲突 | 间歇性，单次通过不证明稳定 |
| 全局环境仅替换 NumPy 为 2.2.6，5 次 | 4 次通过、1 次原生访问冲突 | 降级 NumPy 未消除故障 |
| 全局环境仅替换 pandas 为 2.3.3，5 次 | 5 次通过 | 有限观察，不足以认定 pandas 根因 |
| 仅解释器改为官方签名 Python 3.13.11，科学栈不变，5 次 | 2 次通过、3 次原生访问冲突 | 升级解释器未消除故障 |
| 固定独立科学栈真实 Paper 预演，不限制核心 | 也出现 `0xc0000005` | 原先隔离依赖通过不构成稳定性保证 |
| 全局科学栈仅绑定 E 核 16—19，研究测试重复 3 次 | 每次 344 passed、5 skipped，退出 0 | 有限的进程级绕行证据，未证明硬件恢复 |
| 固定科学栈绑定 E 核，真实 TuShare 输入预演 | 494 只股票推理，Paper preview 退出 0 | 绕行可支持当前验证，仍须连续观察 |

官方 Python 3.13.11 可执行文件 Authenticode 为 Valid / Python Software Foundation。首次缺少项目路径的解释器实验只产生收集错误，未计入上表。调试器改变时序后也曾连续通过，不能当修复。

## 官方固件信息核对

2026-10-18 读取该型号的[微星中国支持页](https://www.msi.cn/Motherboard/PRO-Z790-P-WIFI/support)及其页面实际调用的[BIOS 列表接口](https://www.msi.cn/api/v1/product/support/panel?product=PRO-Z790-P-WIFI&type=bios)，返回产品标题严格为 `PRO Z790-P WIFI`：

| 官网条目 | 发布日期 | 官方说明中相关内容 |
| --- | --- | --- |
| 7E06vA5 | 2023-03-24 | CPU Microcode 更新；与本机 A.50 同版本分支，本机 SMBIOS 日期早一天 |
| 7E06vAF | 2024-11-05 | CPU Microcode 0x12B |
| 7E06vAH | 2025-06-25 | Intel uCode 0x12F |
| 7E06vAK | 2026-08-28 | 本次接口中最新的非 Beta 条目；内存兼容性、ME 16.1.42.2872 |

首次核对只读取网页和版本元数据；随后准备了官方 BIOS 文件，用户自行完成更新，更新后的实测版本见文末。没有将 AK 说明中未写出的具体微码版本猜成 0x12F。全局站返回 403，而中国站动态页面及其真实接口可读；Intel 旧新闻/社区地址重定向到通用列表，没有获得相应正文，未把这些页面作为病因结论的依据。

另以只读 .NET 注册表接口保存 Windows 易失 `HARDWARE/DESCRIPTION/System/CentralProcessor/*/Update Revision`。CPU0 为四字节 `13-01-00-00`，按小端解码为 `0x113`；其余记录保留原始字节，不强行把不同长度的编码统一解释。这是 Windows 启动后的硬件注册信息，未加载驱动直接读取 MSR。它与旧 BIOS 和缺少后续更新的判断相符，不证明某个缺失微码就是全部崩溃的唯一原因，也不证明硬件已经劣化。

原始官网响应、页面组件/API 路径和注册表快照保存在 `G:/qtdata/research/native_diagnostics/official_sources`，可核对 [后续证据摘要](evidence/paper_followup_20261018.json)。主机维护时应依据当时官网、准确型号和厂商步骤选择版本，不能将这次网页快照当永久的最新版本。

以上跨解释器/依赖失败、故障地址变化和 WHEA CPU 错误共同支持平台不稳定的优先诊断。具体是固件/微码、供电/超频设置、CPU 劣化还是其他硬件条件，单靠这些日志不能区分；也不能完全排除另有软件缺陷。没有把 NumPy/Python 某版本当作已证实根因，没有修改全局依赖或用忽略异常掩盖故障。

## 更新前处理与边界

更新前仅将独立研究 Paper 进程临时绑定逻辑 CPU 16、17、18、19，并保留原生退出码、每日回执与新增 WHEA 观察。当时既有交易服务未改亲和性、未重启，BIOS/电压/内存设置未改。

主机维护及固件应用由用户操作。若确认厂商推荐默认配置与适当固件后仍报错，应交由硬件支持检查。刷 BIOS、调整电压、重启和硬件更换不属于自动代码修复。E 核通过不能扩大为 QMT/live 环境已稳定的结论。

可复查全部实验汇总、原生上下文和日志于 `G:/qtdata/research/native_diagnostics`。小型带哈希摘要在 [evidence/paper_operations_20261018.json](evidence/paper_operations_20261018.json)，大型原始诊断和机器转储不入 Git。

## BIOS 更新后复测

用户自行更新 BIOS 并重启后，2026-10-18 23:13 读取到 BIOS **A.K0**、SMBIOS 构建日期 2026-08-19；官网包的发布日期为 2026-08-28，两者含义不同。Windows 记录的 CPU0 微码原始字节由 `13-01-00-00` 变为 `37-01-00-00`，按小端解码由 **0x113 升为 0x137**。仍是注册表观察，并非直接读取 MSR。用户明确只确认更新 BIOS；Intel 默认设置、XMP、降压及独立 ME 更新均未确认，不能将这些条件写成已经完成。

本次验证没有修改全局 Python/科学栈，也没有限制到 E 核；环境探针及真实预演进程均确认进程/系统亲和性为 `0xffffffff`。全局环境为 Python 3.13.4、NumPy 2.4.1、pandas 3.0.1、SciPy 1.17.1、LightGBM 4.6.0、scikit-learn 1.8.0、PyArrow 23.0.1。

| 更新后负载 | 实测结果 |
| --- | --- |
| 原全局科学栈 `tests/research` 重复 5 次 | 每次 **360 passed、5 skipped、2 warnings**，全部退出 0 |
| 原 `hash_stress.py`，DataFrame 内容哈希加 NumPy `vstack/mean` | 完整 **1000 轮**退出 0，每十轮输出的哈希均一致；更新前该脚本约 30 轮曾崩溃 |
| 固定独立科学栈、真实签名的 2026-10-16 输入、不限核心 | Paper CLI 退出 0；重新生成 494 只股票预测，与原 E 核预演逐项完全相同；targets=0、未建账本、未连接 broker |

真实预演使用独立输出目录，旧快照及结果未覆盖。预演后的外部证据收集首次因默认 GBK 读取 UTF-8 中文 JSON 而退出 1；CLI 本身已正常完成。修正显式 UTF-8 后重新收集并比较已生成预测，没有重复运行负载，也没有把收集器错误计作原生崩溃。

从本次开机 **23:07:38.500** 到 **23:21:02**（上海时间）查询 System/WHEA-Logger 及 Application/Error 1000：**新增 WHEA 为 0，命名 Python 应用崩溃为 0**。查询成功，旧日志保留。这一短观察区间及有限负载支持“BIOS 更新后当前未复现”，不证明唯一病因、长期稳定、CPU 无物理退化或 QMT/live 已验收。

独立 Paper 任务已恢复并实际触发，退出 0、状态 `not_started`，下次为 2026-10-19 18:30；暂保留逻辑 CPU 16—19 限制，五个真实交易日仍待观察。18888、18889、18891 未监听，未启动原交易服务。详见 [更新后证据摘要](evidence/post_bios_20261018.json)；原始日志及独立预演位于 `G:/qtdata/research/native_diagnostics/post_bios_20261018`。
