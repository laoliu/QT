# Windows 科学计算原生崩溃诊断

日期：2026-10-18。已确认本机存在处理器硬件错误，Python 原生崩溃的诊断应优先转向平台稳定性；尚未完成硬件修复，也没有证明每一次崩溃都来自同一原因。

## 直接证据

本机为 Intel i9-13900KF、MSI PRO Z790-P WIFI (MS-7E06)，BIOS A.50，发布日期 2023-03-23。Windows System 日志多次出现 WHEA-Logger Event 19：`Processor Core / Corrected Machine Check / Internal parity error`，涉及 APIC 16、25，例如上海时间 18:54:25、20:42:07、21:34:07。

通过逐逻辑 CPU 绑定执行 CPUID 读取 APIC，映射到 Windows 逻辑 CPU 4、7；Windows CPU Set 信息确认均为 P 核。APIC 编号不能直接当作 Windows CPU 编号。原始事件、XML、映射分别在 `G:/qtdata/research/native_diagnostics/whea_events.json`、`cpu_apic_map.json`、`cpu_sets.json`。

外部 Windows Debug API 调试器采集到二次机会 `0xc0000005`，故障点分别落在 Python DLL、NumPy；NumPy 一次故障位于 `ret`，寄存器/栈地址异常，采集的执行页是可执行只读映像，代码与磁盘内容相符。最小组合压力脚本是 DataFrame 内容哈希加 NumPy `vstack/mean`，未做模型训练；纯 NumPy 压力循环没有复现。研究进程模块清单未加载 XtQuant。另有历史 XtQuant MSVCP140.dll 故障记录，不能据此归因本次研究崩溃。

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

只读取网页和版本元数据，没有下载或应用 BIOS/ME 固件。没有将 AK 说明中未写出的具体微码版本猜成 0x12F。全局站返回 403，而中国站动态页面及其真实接口可读；Intel 旧新闻/社区地址重定向到通用列表，没有获得相应正文，未把这些页面作为病因结论的依据。

另以只读 .NET 注册表接口保存 Windows 易失 `HARDWARE/DESCRIPTION/System/CentralProcessor/*/Update Revision`。CPU0 为四字节 `13-01-00-00`，按小端解码为 `0x113`；其余记录保留原始字节，不强行把不同长度的编码统一解释。这是 Windows 启动后的硬件注册信息，未加载驱动直接读取 MSR。它与旧 BIOS 和缺少后续更新的判断相符，不证明某个缺失微码就是全部崩溃的唯一原因，也不证明硬件已经劣化。

原始官网响应、页面组件/API 路径和注册表快照保存在 `G:/qtdata/research/native_diagnostics/official_sources`，可核对 [后续证据摘要](evidence/paper_followup_20261018.json)。主机维护时应依据当时官网、准确型号和厂商步骤选择版本，不能将这次网页快照当永久的最新版本。

以上跨解释器/依赖失败、故障地址变化和 WHEA CPU 错误共同支持平台不稳定的优先诊断。具体是固件/微码、供电/超频设置、CPU 劣化还是其他硬件条件，单靠这些日志不能区分；也不能完全排除另有软件缺陷。没有把 NumPy/Python 某版本当作已证实根因，没有修改全局依赖或用忽略异常掩盖故障。

## 当前处理与边界

仅将独立研究 Paper 进程临时绑定逻辑 CPU 16、17、18、19，并继续保留原生退出码、每日回执与新增 WHEA 观察。既有交易服务未改亲和性、未重启，BIOS/电压/内存设置未改。

主机负责人需要按准确主板型号核对厂商 BIOS/微码和官方稳定性建议，在安排维护停机后处理，并重新验证硬件稳定性；若默认配置与适当固件下仍报错，应交由硬件支持检查。刷 BIOS、调整电压、重启和硬件更换不属于此次自动代码修复。E 核通过不能扩大为 QMT/live 环境已稳定的结论。

可复查全部实验汇总、原生上下文和日志于 `G:/qtdata/research/native_diagnostics`。小型带哈希摘要在 [evidence/paper_operations_20261018.json](evidence/paper_operations_20261018.json)，大型原始诊断和机器转储不入 Git。
