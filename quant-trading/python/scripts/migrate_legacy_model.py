"""Attach an explicit manifest to a copied legacy ML selector.

Example::

    python scripts/migrate_legacy_model.py legacy_model migrated_model \
      --feature-version cn_equity_v1 \
      --feature-columns momentum_20 volatility_20 \
      --model-type lightgbm --data-snapshot-id snapshot-2024 \
      --universe CSI500 --min-listing-days 252 \
      --feature-groups momentum volatility --model-id legacy-attested-1 \
      --model-config-hash operator-verified-config-hash \
      --acknowledge-legacy
"""
from __future__ import annotations

import argparse
import json

from aipha.research.migration import migrate_legacy_model


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("destination")
    parser.add_argument("--feature-version", required=True)
    parser.add_argument("--feature-columns", nargs="+", required=True)
    parser.add_argument("--model-type", required=True)
    parser.add_argument("--data-snapshot-id", required=True)
    parser.add_argument("--universe", required=True)
    parser.add_argument("--min-listing-days", type=int, required=True)
    parser.add_argument("--feature-groups", nargs="+", required=True)
    parser.add_argument("--column-groups-json", default="{}", help="显式自定义列到分组的 JSON 映射")
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--model-config-hash", required=True)
    parser.add_argument("--acknowledge-legacy", action="store_true")
    args = parser.parse_args()
    output = migrate_legacy_model(
        args.source,
        args.destination,
        feature_version=args.feature_version,
        feature_columns=args.feature_columns,
        model_type=args.model_type,
        data_snapshot_id=args.data_snapshot_id,
        acknowledge_legacy=args.acknowledge_legacy,
        universe=args.universe,
        min_listing_days=args.min_listing_days,
        feature_groups=args.feature_groups,
        feature_column_groups=json.loads(args.column_groups_json),
        model_id=args.model_id,
        model_config_hash=args.model_config_hash,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
