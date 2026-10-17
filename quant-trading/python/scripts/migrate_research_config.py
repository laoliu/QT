"""Convert a legacy ML-factor YAML into the canonical PIT research config.

The source file is never modified.  AKShare/generic backtest inputs are
reported as warnings and the generated file always requires a TuShare PIT
snapshot, so a migration cannot silently reintroduce a look-ahead-prone data
path.

Example::

    python scripts/migrate_research_config.py \
      configs/sample_ml_factor.yaml \
      aipha/configs/research/migrated_ml.yaml
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aipha.research.config import migrate_legacy_ml_config_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        output, warnings = migrate_legacy_ml_config_file(args.source, args.destination)
    except (OSError, ValueError) as exc:
        print(f"migration_failed={type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"written={output}")
    for warning in warnings:
        print(f"warning={warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
