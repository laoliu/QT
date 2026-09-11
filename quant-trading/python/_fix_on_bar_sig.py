"""Fix _on_bar_update_state signatures to match base class: (self, bars, context)."""
import re
from pathlib import Path

base = Path(r"D:\work\QT\quant-trading\python\aipha\strategy")

# Files with wrong signature: (self, ctx: StrategyContext) -> None:
wrong_sig_files = [
    "breadth_controlled.py", "cb_adaptive_factor.py", "cb_dl_enhanced_5f.py",
    "cb_heat_momentum.py", "cb_ivol_value.py", "cb_low_vol_reversion.py",
    "cb_multi_alpha.py", "cb_multi_layer.py", "cb_regime_rotation.py",
    "cb_reversal_small.py", "cb_small_size_gated.py", "cb_vol_squeeze.py",
    "size_style_rotation.py", "trend_quality_managed.py", "vol_breakout.py",
    "value_growth_switch.py", "turnover_surge.py",
]

changed = 0
for fname in wrong_sig_files:
    fpath = base / fname
    if not fpath.exists():
        print(f"  SKIP (not found): {fname}")
        continue
    content = fpath.read_text(encoding="utf-8")
    
    # Fix signature: (self, ctx: StrategyContext) -> (self, bars, context)
    old_sig = "def _on_bar_update_state(self, ctx: StrategyContext) -> None:"
    new_sig = 'def _on_bar_update_state(self, bars: dict[str, "Bar"], context: StrategyContext) -> None:'
    
    if old_sig not in content:
        print(f"  SKIP (signature not found): {fname}")
        continue
    
    content = content.replace(old_sig, new_sig)
    
    # Fix the internal "bars = getattr(ctx, ...)" pattern
    # Replace: bars = getattr(ctx, "bars", {})
    content = content.replace('bars = getattr(ctx, "bars", {})', '# bars provided as argument')
    content = content.replace("bars = getattr(ctx, 'bars', {})", '# bars provided as argument')
    
    # Replace remaining references to 'ctx' with 'context' in the method body
    # We need to be careful - only replace within the _on_bar_update_state method
    lines = content.split("\n")
    in_method = False
    method_indent = 0
    for i, line in enumerate(lines):
        if '_on_bar_update_state' in line and 'def ' in line:
            in_method = True
            method_indent = len(line) - len(line.lstrip()) + 4
            continue
        if in_method:
            stripped = line.strip()
            if stripped and not line.startswith(' ' * method_indent) and not stripped.startswith('#'):
                if not line.startswith(' ' * (method_indent - 4) + ' '):
                    in_method = False
                    continue
            if in_method and 'ctx' in line:
                # Only replace 'ctx' that refers to the old parameter
                lines[i] = line.replace('ctx.', 'context.').replace('ctx,', 'context,').replace('ctx)', 'context)')
    
    content = "\n".join(lines)
    fpath.write_text(content, encoding="utf-8")
    changed += 1
    print(f"  FIXED: {fname}")

print(f"\nTotal fixed: {changed}/{len(wrong_sig_files)}")
