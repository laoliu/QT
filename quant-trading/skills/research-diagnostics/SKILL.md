---
name: research-diagnostics
description: "查询缓存覆盖、数据缺口、研究/Paper 运行进度、预测证据日期、调度状态或策略健康时使用。"
allowed-tools: ["data_cache","research_operations","strategy_health","market_data"]
metadata: {"display_name":"数据质量与研究运行诊断","category":"数据与运行","quant-trading":{"emoji":"📊","requires":{"plugins":["quant-trading"]}}}
---

# 数据质量与研究运行诊断
data_cache(action=stats) 查询 SQLite 缓存统计，不代表外部 Parquet 全部覆盖。market_data(action=klines) 按指定标的/区间抽查数据；不要为诊断清缓存。
research_operations(action=overview) 查询固定部署的 research/paper 概览和调度状态，research_operations(action=predictions) 查看分页预测；使用 scope 区分研究与 Paper，不启动任务。
strategy_health(action=check_all) 或 strategy_health(action=check) 查询已有策略追踪数据。无记录不等于健康，调度任务状态与研究结果成功分开判断。
输出观察时间、证据所属交易日、当前阶段、错误/缺口和下一步建议。未接入真实 IC/持仓数据的监控占位接口不能作为因子健康结论；不启动训练、补数据、重启服务或交易。
