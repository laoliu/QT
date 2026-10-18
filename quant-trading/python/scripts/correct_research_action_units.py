"""Derive an attested snapshot using explicit, reviewed issuer event terms.

Requires AIPHA_PIT_SNAPSHOT_KEY in the process environment. Evidence files are
UTF-8 documents named <sha256>.txt; correction JSON is a list of event records.
The original snapshot and existing output files are never overwritten.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aipha.research.snapshots import (
    TushareSnapshot, correct_snapshot_action_units, verify_attested_parquet,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--corrections", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--factor-rounding-unit", type=float, default=0.)
    args = parser.parse_args()
    if args.output.exists() or args.output.with_suffix(".metadata.json").exists():
        raise FileExistsError("choose a new immutable output snapshot path")
    records = json.loads(args.corrections.read_text(encoding="utf-8"))
    if not isinstance(records, list) or not records:
        raise ValueError("corrections must be a non-empty list")
    evidence = {}
    for record in records:
        digest = record["evidence_sha256"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid evidence_sha256")
        evidence[digest] = (args.evidence_dir / f"{digest}.txt").read_bytes()
    # Verify the file and source signature here; the derivation verifies the
    # logical row hash once after loading the input panel.
    metadata = verify_attested_parquet(args.snapshot, require_artifact_hash=True,
                                      verify_content_hash=False, require_source_signature=True)
    result = correct_snapshot_action_units(
        TushareSnapshot(pd.read_parquet(args.snapshot), metadata), records, evidence,
        factor_rounding_unit=args.factor_rounding_unit,
    )
    result.write(args.output)
    print(json.dumps({"snapshot_path": str(args.output), "data_snapshot_id": result.metadata["data_snapshot_id"],
                      "corrected_events": len(records)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
