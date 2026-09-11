"""Batch fix: Update old-style __init__ to accept config= parameter."""
import re
from pathlib import Path

base_dir = Path(r"D:\work\QT\quant-trading\python\aipha\strategy")

# Map of file -> (ClassName, ConfigClass)
fixes = {
    "buyback_signal.py": ("BuybackSignalStrategy", "BuybackSignalConfig"),
    "dividend_quality.py": ("DividendQualityStrategy", "DividendQualityConfig"),
    "earnings_momentum.py": ("EarningsMomentumStrategy", "EarningsMomentumConfig"),
    "garp_select.py": ("GARPSelectStrategy", "GARPConfig"),
    "high_delivery_expect.py": ("HighDeliveryExpectStrategy", "HighDeliveryConfig"),
    "insider_buying.py": ("InsiderBuyingStrategy", "InsiderBuyingConfig"),
    "momentum_acceleration.py": ("MomentumAccelerationStrategy", "MomentumAccelConfig"),
    "northbound_enhanced.py": ("NorthboundEnhancedStrategy", "NorthboundConfig"),
    "pead_event.py": ("PEADStrategy", "PEADConfig"),
    "sector_rotation.py": ("SectorRotationStrategy", "SectorRotationConfig"),
    "small_cap_quality.py": ("SmallCapQualityStrategy", "SmallCapQualityConfig"),
    "stable_compounder.py": ("StableCompounderStrategy", "StableCompounderConfig"),
    "turnaround_play.py": ("TurnaroundPlayStrategy", "TurnaroundConfig"),
    "value_quality.py": ("ValueQualityStrategy", "ValueQualityConfig"),
    "vol_managed_value.py": ("VolManagedValueStrategy", "VolManagedValueConfig"),
}

changed = 0
for fname, (cls_name, cfg_name) in fixes.items():
    fpath = base_dir / fname
    content = fpath.read_text(encoding="utf-8")

    # Replace: def __init__(self) -> None:\n        super().__init__()\n        self._config = CfgClass()
    old = f"    def __init__(self) -> None:\n        super().__init__()\n        self._config = {cfg_name}()"
    new = (
        f"    def __init__(self, config: {cfg_name} | None = None, **kwargs: object) -> None:\n"
        f"        super().__init__(config=config or {cfg_name}(), **kwargs)"
    )

    if old in content:
        content = content.replace(old, new)
        fpath.write_text(content, encoding="utf-8")
        changed += 1
        print(f"  FIXED: {fname}")
    else:
        print(f"  SKIP (pattern not found): {fname}")

print(f"\nTotal fixed: {changed}/15")
