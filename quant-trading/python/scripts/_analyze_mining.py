"""Analyze factor field diversity for expert review."""
import json
import re
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
factors = json.loads(
    (_PROJECT_ROOT / "output" / "auto_mining_v6" / "independent_factors.json")
    .read_text(encoding="utf-8")
)

field_freq: dict[str, int] = {}
for f in factors:
    expr = f.get("expression", "")
    fields = set(re.findall(r"\$(\w+)", expr))
    f_str = ",".join(sorted(fields))
    print(f"{f['name']:25s} score={f['composite_score']:.3f} "
          f"ICIR={f.get('kb_icir',0):+.3f} "
          f"WF={f.get('walk_forward',{}).get('oos_icir',0):+.3f} "
          f"mono={f.get('quantile',{}).get('monotonicity',0):+.2f} "
          f"TO={f.get('turnover',{}).get('avg_turnover',0):.2f} "
          f"fields=[{f_str}]")
    for fd in fields:
        field_freq[fd] = field_freq.get(fd, 0) + 1

print("\n--- Field frequency ---")
for k, v in sorted(field_freq.items(), key=lambda x: -x[1]):
    print(f"  {k:20s}: {v}")
