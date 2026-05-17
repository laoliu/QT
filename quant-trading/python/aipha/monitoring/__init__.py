"""研究阶段信号质量监控模块 (Research Signal Quality)

§2.1: 研究阶段使用的因子衰减/拥挤度/重训练触发监控。
与 aipha.monitor (生产运行时告警) 区分。

组件:
- HalflifeDecayMonitor: DecayMonitor + AR(1) 半衰期估算
- AdvancedCrowdingMonitor: 四指标拥挤度 (FVS/换手集中度/Pairwise相关)

快速导入::

    from aipha.monitoring import HalflifeDecayMonitor, AdvancedCrowdingMonitor
"""

from aipha.monitoring.factor_decay_monitor import (
    HalflifeDecayMonitor,
    HalflifeSnapshot,
)
from aipha.monitoring.crowding_monitor import (
    AdvancedCrowdingMonitor,
    CrowdingMonitorConfig,
    CrowdingMetrics,
)
from aipha.monitoring.retrain_trigger import (
    RetrainTrigger,
    RetrainDecision,
    RetrainReason,
)
from aipha.monitoring.combo_health_checker import (
    ComboHealthChecker,
    HealthReport,
    HealthCheckItem,
)
from aipha.monitoring.psi_calculator import (
    PSICalculator,
    DriftReport,
    FeaturePSI,
)
from aipha.monitoring.downward_revision_tracker import (
    DownwardRevisionTracker,
    DownwardRevisionStatus,
)
from aipha.monitoring.ic_threshold_monitor import (
    ICThresholdMonitor,
    ICCheckResult,
)

__all__ = [
    "HalflifeDecayMonitor",
    "HalflifeSnapshot",
    "AdvancedCrowdingMonitor",
    "CrowdingMonitorConfig",
    "CrowdingMetrics",
    "RetrainTrigger",
    "RetrainDecision",
    "RetrainReason",
    "ComboHealthChecker",
    "HealthReport",
    "HealthCheckItem",
    "PSICalculator",
    "DriftReport",
    "FeaturePSI",
    "DownwardRevisionTracker",
    "DownwardRevisionStatus",
    "ICThresholdMonitor",
    "ICCheckResult",
]
