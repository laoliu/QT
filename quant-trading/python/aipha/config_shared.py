"""共享配置子模型 — RiskConfig / CostConfig 的规范导入路径。

§4.3.2 统一配置体系规则要求 RiskConfig 和 CostConfig 定义在唯一来源模块中。
当前实际定义在 ``aipha.backtest.config_models``（历史原因），本模块提供
统一的规范导入路径，供新代码使用。

**新代码应从此模块导入**::

    from aipha.config_shared import CostConfig, RiskConfig

**迁移计划**：
  - Phase 1 (当前): 此模块为 re-export 层，实际定义保留在 config_models.py
  - Phase 2 (未来): 将定义迁移至此模块，config_models.py 反向 re-export（向后兼容）

此规划符合 §4.3.2 要求，避免一次性修改 130+ import 带来的风险。
"""

from __future__ import annotations

# 规范导入路径 — 新代码请从此处 import
# Phase C: 实际定义保留在 config_models.py, 本模块统一 re-export
from aipha.backtest.config_models import (  # noqa: F401
    CostConfig,
    FillModelConfig,
    RiskConfig,
)

__all__ = ["CostConfig", "FillModelConfig", "RiskConfig"]
