"""Batch add missing fields to Config dataclasses."""
import re
from pathlib import Path

base = Path(r"D:\work\QT\quant-trading\python\aipha\strategy")

# For each config class, define what fields to ADD (field_name: default_value_str)
additions = {
    "dividend_defensive.py": {
        "class": "DividendDefensiveConfig",
        "fields": [
            "weight_accruals: float = 0.0",
            "weight_earnings_stability: float = 0.0",
            "weight_piotroski: float = 0.0",
        ],
    },
    "dividend_quality.py": {
        "class": "DividendQualityConfig",
        "fields": [
            "weight_dividend: float = 0.25",
            "weight_fcfy: float = 0.25",
            "weight_roe: float = 0.20",
            "weight_vol: float = 0.15",
            "weight_ep: float = 0.15",
            "min_dividend: float = 0.5",
        ],
    },
    "earnings_momentum.py": {
        "class": "EarningsMomentumConfig",
        "fields": [
            "min_earnings_growth: float = 10.0",
            "min_revenue_growth: float = 5.0",
            "weight_earnings_accel: float = 0.30",
            "weight_revenue_growth: float = 0.20",
            "weight_sue: float = 0.20",
            "weight_momentum: float = 0.15",
            "weight_quality: float = 0.10",
            "weight_volatility: float = 0.05",
            "momentum_lookback: int = 20",
            "volatility_lookback: int = 60",
        ],
    },
    "ml_factor_strategy.py": {
        "class": "MLFactorConfig",
        "fields": [
            "model_objective: str = 'regression'",
            "train_years: int = 3",
            "forward_days: int = 5",
        ],
    },
    "multifactor_enhance.py": {
        "class": "MultiFactorEnhanceConfig",
        "fields": [
            "max_single_pct: float = 0.05",
            "piotroski_min_dims: int = 5",
        ],
    },
    "pead_event.py": {
        "class": "PEADConfig",
        "fields": [
            "weight_sue: float = 0.45",
            "weight_earnings_growth: float = 0.20",
            "weight_quality: float = 0.15",
            "weight_momentum: float = 0.10",
            "weight_volatility: float = 0.10",
            "momentum_lookback: int = 20",
            "volatility_lookback: int = 60",
        ],
    },
    "sector_momentum.py": {
        "class": "SectorMomentumConfig",
        "fields": [
            "weight_momentum: float = 0.30",
            "weight_volatility: float = 0.25",
            "weight_value: float = 0.20",
            "weight_size: float = 0.10",
        ],
    },
    "v19_hgro.py": {
        "class": "V19HgroConfig",
        "fields": [
            "vol_scaling_enabled: bool = False",
            "vol_target: float = 0.20",
            "momentum_skip_recent: int = 20",
            "max_pb: float = 5.0",
        ],
    },
    "v22_bmf.py": {
        "class": "V22BmfConfig",
        "fields": [
            "drawdown_timing_enabled: bool = False",
            "dd_reduce_threshold: float = 0.20",
            "dd_restore_threshold: float = 0.10",
            "dd_min_scale: float = 0.50",
            "max_industry_pct: float = 0.20",
            "max_pb: float = 8.0",
        ],
    },
    "v23_sbal.py": {
        "class": "V23SbalConfig",
        "fields": [
            "drawdown_timing_enabled: bool = True",
            "max_industry_pct: float = 0.15",
            "min_quadrant_stocks: int = 3",
            "dd_level1_threshold: float = 0.10",
            "dd_level2_threshold: float = 0.20",
            "dd_restore_threshold: float = 0.05",
            "dd_level1_scale: float = 0.70",
            "dd_level2_scale: float = 0.40",
        ],
    },
    "value_quality.py": {
        "class": "ValueQualityConfig",
        "fields": [
            "weight_value: float = 0.30",
            "weight_quality: float = 0.30",
            "weight_momentum: float = 0.20",
            "weight_volatility: float = 0.10",
            "weight_size: float = 0.10",
            "weight_piotroski: float = 0.0",
            "weight_valuation_pct: float = 0.0",
            "weight_accruals: float = 0.0",
            "piotroski_min_dims: int = 5",
            "momentum_lookback: int = 60",
            "volatility_lookback: int = 60",
        ],
    },
}

changed = 0
for fname, info in additions.items():
    fpath = base / fname
    content = fpath.read_text(encoding="utf-8")
    cls_name = info["class"]
    new_fields = info["fields"]
    
    # Find the end of the config class definition
    # Pattern: @dataclass\nclass ConfigName(...):  <fields...>
    # We need to find the last field line and add new fields after it
    
    lines = content.split("\n")
    # Find the class line
    class_line_idx = None
    for i, line in enumerate(lines):
        if f"class {cls_name}" in line:
            class_line_idx = i
            break
    
    if class_line_idx is None:
        print(f"  SKIP (class not found): {fname} - {cls_name}")
        continue
    
    # Find the last field line (indented line with : and = that belongs to this class)
    last_field_idx = class_line_idx
    indent = None
    for i in range(class_line_idx + 1, len(lines)):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Check if still in class body (indented)
        if line and not line[0].isspace():
            break
        if ":" in stripped and "=" in stripped and not stripped.startswith("def "):
            last_field_idx = i
            indent = len(line) - len(line.lstrip())
    
    if indent is None:
        indent = 4
    
    # Detect if fields are on same line with semicolons
    last_line = lines[last_field_idx]
    if ";" in last_line:
        # Multi-field on one line - append to same line style
        addition_str = "; ".join(new_fields)
        lines[last_field_idx] = last_line.rstrip() + "; " + addition_str
    else:
        # Standard multi-line - insert after last field
        new_lines = [" " * indent + f for f in new_fields]
        for j, nl in enumerate(new_lines):
            lines.insert(last_field_idx + 1 + j, nl)
    
    content = "\n".join(lines)
    fpath.write_text(content, encoding="utf-8")
    changed += 1
    print(f"  ADDED to {fname}/{cls_name}: {[f.split(':')[0] for f in new_fields]}")

print(f"\nTotal modified: {changed}/{len(additions)}")
