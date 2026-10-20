---
name: factor-ml-research
description: "分析因子值、已有模型的股票排名、样本外 RankIC/ICIR 或模型评估时使用。训练和执行计划不属于本聊天能力。"
allowed-tools: ["factor_analysis","ml_ranking","market_data","research_operations"]
metadata: {"display_name":"因子研究与 ML 排序","category":"策略研究","quant-trading":{"emoji":"📊","requires":{"plugins":["quant-trading"]}}}
---

# 因子研究与 ML 排序
factor_analysis(action=list) 查询因子，再 factor_analysis(action=compute) 计算；必须提供该因子所需的真实 fundamentals/returns，缺失不能当作零分或有效排名。
ml_ranking(action=list_models) 了解模型类型，ml_ranking(action=predict) 对已有模型和冻结特征快照排序，ml_ranking(action=evaluate) 评估已有预测。模型、配置与数据路径仅使用用户明确指定或工具给出的已知路径，不猜测。
核对 model_id、feature_version、data_snapshot_id、样本区间、PIT 状态、成本和 OOS 指标。缺少历史公告可见性不能升级为 PIT 验证通过。
research_operations(action=predictions) 查询固定部署已有预测。聊天不训练模型、不导出计划、不执行 Paper；相关需求说明应使用研究工作流入口。
