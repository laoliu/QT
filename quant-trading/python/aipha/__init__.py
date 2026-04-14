"""Aipha-Q 量化交易核心库 — 移植自 AlphQ 工程。

提供：
- domain: 领域模型 (Bar, Order, Fill, Side)
- data: 数据提供者协议 + 交易日历 + 缓存
- config: YAML 配置驱动体系
- factors: 因子库 (Alpha101/158, 价值/质量/动量/波动)
- strategy: 策略抽象基类 + 注册表
- backtest: 事件驱动回测引擎
- risk: 风控约束框架
- models: ML 模型 (LightGBM/GRU/GAT/MASTER/Ensemble)
- portfolio: 组合优化 (CVXPY)
- validation: 统计验证工具 (DSR/PBO/HLZ)
"""

__version__ = "0.1.0"
