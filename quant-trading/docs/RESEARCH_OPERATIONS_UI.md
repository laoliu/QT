# A 股研究 / Paper 运营页面

主站地址：`http://127.0.0.1:18891/quant/admin/research`。首页、管理面板顶部和投资规划顶部均有入口。主站进程需要重新加载代码才能注册新路由；这不是重启 QMT 桥接的理由。

2026-10-19 已经用户确认重启主站并验证页面/API/导航生效。注意本机 `18889` 接收器当前由 Node 主站作为子进程管理：现有启动脚本的整个进程树重启会同时重启接收器，本次产生了这个超出批准范围的副作用并已向用户说明。后续不能把该脚本当作“保证仅影响 18891”的重启方法；必须先核对进程依赖。18888 桥接与 MiniQMT 在本次操作中 PID 未改变。

页面只允许读取证据，不启动训练、补数、Windows 任务或交易服务，也没有提交计划、下单或撤单控件。API 为 `GET /quant/api/research/operations`，沿用主站认证、CORS 和限流；写方法返回 405。页面默认 30 秒刷新，后端缓存 30 秒、合并并发读取，刷新失败时明确标识保留的旧结果。

## 能看到什么

- 状态：固定模型 ID、manifest 哈希匹配、特征版本、研究快照截止日、OOS 评估截止日、独立 Paper 提交日、Windows 任务状态及下次时间、最近进程退出码、STOP 和读取失败告警。
- 过程：补数/特征快照、快照校验、固定模型校验、特征截面筛选、预测、目标持仓、记账；展示尝试 ID、PID、阶段开始/结束和耗时，以及最近脱敏日志。旧记录没有的耗时显示“未记录”。
- 历史研究：RankIC、ICIR、收益/回撤/Sharpe、净值和回撤曲线、成本归因、不可执行比例、逐折 Walk-Forward、IS/OOS 衰减、暴露、逐特征缺失率、最新历史目标持仓、历史 OOS 排名和研究摘要下载。
- 目标持仓中的股票代码可点击，在新标签页打开现有个股详情；完整保留 `.SH`、`.SZ`、`.BJ` 后缀。历史目标和每日 Paper 目标使用相同入口，详情为现有行情详情，不冒充信号日历史行情。
- 历史和每日 Paper 目标均展示股票名称，按完整交易所代码读取部署 `cache_dir` 中的 `stock_basic.parquet`，不发起在线查询。名称为当前缓存的展示资料，可能与历史信号日名称不同；未命中显示“名称未缓存”。读取失败不影响目标权重和代码，不修改研究产物或执行计划。
- 真实 Paper：调用 `inspect_paper_book` 校验连续天数及回执新鲜度；通过原有只读 SQLite 重放读取净资产、现金、成本、持仓和成交。每日回执可点开，不能把 `sessions` 目录中的预测文件当作成功记账。

历史组合可能在最后一个 OOS 预测日之后继续估值。页面分别显示快照覆盖、OOS 评估和组合估值截止日；Paper 净值与历史净值分别绘制。Paper 尚无正式账本时不创建数据库，也不把历史预演计入天数；首日已经过去仍没有正式账本时提示需要明确新起点。

## 证据与读取环境

默认部署：`G:/qtdata/research/operations/csi500/deployment.yaml`。研究结果由该部署的 `model_path` 定位到所属 run；不扫描账户数据库或自动选择其他研究结果。服务端可用以下环境变量调整本机安装位置（不接受浏览器提供文件路径）：

| 变量 | Windows 默认值 |
| --- | --- |
| `QT_RESEARCH_DEPLOYMENT` | `G:/qtdata/research/operations/csi500/deployment.yaml` |
| `QT_RESEARCH_VIEW_PYTHON` | `G:/qtdata/research/validation-env-py313/Scripts/python.exe` |
| `QT_RESEARCH_VIEW_CPU_AFFINITY` | `16,17,18,19`，只限制读取证据的子进程；其他主机须按实际 CPU 调整 |
| `QT_RESEARCH_TASK_NAME` | `QT-Research-Paper-CSI500` |

Python 读取器在加载科学计算依赖前设置进程亲和性；从部署配置中的本机 `.env` 加载验签所需环境并脱敏。它不反序列化模型、不构建快照、不请求在线数据、不导入 broker。只投影所需字段，不返回凭据、签名、原始回执内的完整日历/财务元数据。日志仅允许来自部署的 `scheduler` 子目录，且有字节数/行数限制。

后续真实任务会写入 `progress.json`（本次尝试阶段计时）及追加的 `phase_events.jsonl`。这些是过程观测，不能代替 `operation.json` 与账本中的正式回执。历史失败会继续保留并注明时间，恢复成功不删除历史失败证据。

预测接口支持 `section=predictions&scope=research|paper&date=YYYY-MM-DD&offset=0&limit=50`，单页上限 100。Paper 仅查看正式 `sessions` 路径，不读取 `preflight`。

## 独立只读预览

如果主站暂不能重启，可以在仓库根目录运行：

```powershell
node --import tsx scripts/serve-research-operations.ts
```

访问 `http://127.0.0.1:18993/quant/admin/research`。这个预览服务器只监听本机，不启动主站或 QMT；关闭启动它的终端或 Ctrl+C 即停止。它不是注册为常驻服务的主站替代品。

## 验证

HTTP 测试覆盖写操作拒绝、路径和日期/分页校验、并发合并/缓存、调度缺失、错误脱敏和最终浏览器脚本语法。Python 使用本地 fixture 验证无账本零写入、预演排除、完整连续天数、回执篡改拒绝、目标契约转换、预测日期分页、UTF-16 日志脱敏及阶段计时。实际浏览器使用当前部署核对指标与排名；真实 Paper 回执要等正式盘后运营产生，不能通过 UI 测试提前宣布五日通过。
