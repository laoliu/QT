"""Canonical names and TuShare identifiers for supported historical universes."""
from __future__ import annotations

INDEX_CODES = {
    "csi300": "000300.SH", "csi500": "000905.SH",
    "csi1000": "000852.SH", "csi2000": "932000.CSI",
}
_ALIASES = {"hs300": "csi300", "zz500": "csi500", "zz1000": "csi1000", "zz2000": "csi2000"}


def normalize_universe(value: str) -> str:
    name = str(value).strip().lower()
    name = _ALIASES.get(name, name)
    if name not in INDEX_CODES:
        raise ValueError("canonical universe must be CSI300, CSI500, CSI1000 or CSI2000")
    return name


def universe_index_code(value: str) -> str:
    return INDEX_CODES[normalize_universe(value)]


def validate_snapshot_universe(metadata: dict, universe: str) -> None:
    expected = universe_index_code(universe)
    actual = str(metadata.get("index_code", "")).strip().upper()
    if actual != expected:
        raise ValueError(f"snapshot universe mismatch: index_code={actual!r}, expected {expected} for {universe}")
