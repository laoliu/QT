from __future__ import annotations

import csv
import json
from pathlib import Path

from aipha.backtest.engine import DailySnapshot

NAV_ARTIFACT_NAMES = ("daily_snapshots.csv", "daily_nav.csv")
REQUIRED_NAV_COLUMNS = {"date", "nav"}


class NavArtifactError(RuntimeError):
    pass


def validate_nav_artifact(path: str | Path, expected_rows: int | None = None) -> int:
    path = Path(path)
    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing = REQUIRED_NAV_COLUMNS - columns
        if missing:
            raise NavArtifactError(f"NAV artifact missing columns {sorted(missing)}: {path}")
        row_count = 0
        for row_count, row in enumerate(reader, start=1):
            if row_count == 1:
                float(str(row["nav"]).strip())
                if not str(row["date"]).strip():
                    raise NavArtifactError(f"NAV artifact has empty date: {path}")
        if row_count == 0:
            raise NavArtifactError(f"NAV artifact has no rows: {path}")
    if expected_rows is not None and row_count != expected_rows:
        raise NavArtifactError(
            f"NAV artifact row mismatch: expected {expected_rows}, got {row_count}: {path}"
        )
    return row_count


def resolve_nav_artifact(path_or_dir: str | Path) -> Path:
    path = Path(path_or_dir)
    if path.is_file():
        validate_nav_artifact(path)
        return path
    for name in NAV_ARTIFACT_NAMES:
        candidate = path / name
        if candidate.exists():
            validate_nav_artifact(candidate)
            return candidate
    raise FileNotFoundError(f"No valid NAV artifact found in {path}")


def load_nav_series(path_or_dir: str | Path, series_name: str | None = None):
    import pandas as pd

    path = resolve_nav_artifact(path_or_dir)
    df = pd.read_csv(path, usecols=["date", "nav"])
    df["date"] = pd.to_datetime(df["date"], errors="raise")
    if df["nav"].isna().any():
        raise NavArtifactError(f"NAV artifact contains null nav values: {path}")
    series = df.sort_values("date").drop_duplicates("date").set_index("date")["nav"]
    series.name = series_name
    return series


def write_daily_snapshots_csv(snapshots: list[DailySnapshot], path: str | Path) -> int:
    path = Path(path)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "date", "nav", "cash", "frozen_cash", "daily_return",
            "cumulative_return", "position_count", "positions", "position_values",
        ])
        for snapshot in snapshots:
            writer.writerow([
                snapshot.trade_date.isoformat(), round(snapshot.nav, 2), round(snapshot.cash, 2),
                round(snapshot.frozen_cash, 2), round(snapshot.daily_return, 6),
                round(snapshot.cumulative_return, 6), len(snapshot.positions),
                json.dumps(snapshot.positions, ensure_ascii=False),
                json.dumps({k: round(v, 2) for k, v in snapshot.position_values.items()}, ensure_ascii=False),
            ])
    return validate_nav_artifact(path, expected_rows=len(snapshots))