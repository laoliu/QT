"""TuShare PIT snapshot assembly for the canonical research pipeline.

The builder deliberately receives provider objects from the caller. It never
falls back to AKShare and it keeps raw prices plus a point-in-time adjusted
price derived from the factor available on the same trade date.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np


# These values are part of the on-disk provenance contract.  They are kept
# here rather than in a caller so that every TuShare snapshot writer uses the
# same attestation format.
SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_GENERATOR_VERSION = "aipha.research.snapshots/4"
SNAPSHOT_SIGNING_KEY_ENV = "AIPHA_PIT_SNAPSHOT_KEY"
_SIGNED_SOURCE_FIELDS = (
    "provider", "data_snapshot_id", "feature_version", "start_date", "end_date",
    "index_code", "benchmark_code", "adjust", "snapshot_schema_version",
    "generator_version", "snapshot_content_sha256", "row_count", "column_count",
    "listing_calendar",
)


def _snapshot_signing_key(key: str | bytes | None = None) -> bytes | None:
    raw = key if key is not None else os.environ.get(SNAPSHOT_SIGNING_KEY_ENV)
    if raw is None:
        return None
    value = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
    if len(value) < 32:
        raise ValueError(f"{SNAPSHOT_SIGNING_KEY_ENV} must contain at least 32 bytes")
    return value


def _signature_payload(metadata: dict[str, Any]) -> bytes:
    payload = {key: metadata.get(key) for key in _SIGNED_SOURCE_FIELDS}
    # Optional extension keeps old signatures verifiable while authenticating
    # the complete quality disclosure on newly generated snapshots.
    for field in ("financial_data_quality", "universe_history", "corporate_action_policy",
                  "snapshot_lineage", "corporate_action_unit_corrections"):
        if field in metadata:
            payload[field] = metadata[field]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_snapshot_metadata(
    metadata: dict[str, Any], *, key: str | bytes | None = None,
) -> dict[str, Any]:
    """Authenticate PIT provenance with a deployment-held HMAC key.

    File/content hashes detect accidental or post-write changes. This
    signature adds a separate trust anchor so a caller cannot create a new
    sidecar that simply claims ``provider=tushare_pit``.
    """
    if metadata.get("provider") != "tushare_pit":
        return dict(metadata)
    secret = _snapshot_signing_key(key)
    if secret is None:
        raise ValueError(f"{SNAPSHOT_SIGNING_KEY_ENV} is required to sign a TuShare PIT snapshot")
    missing = [
        field for field in (
            "provider", "data_snapshot_id", "feature_version", "start_date",
            "end_date", "snapshot_schema_version", "generator_version",
            "snapshot_content_sha256", "row_count", "column_count",
        ) if metadata.get(field) is None
    ]
    if missing:
        raise ValueError(f"cannot sign PIT snapshot; metadata missing: {', '.join(missing)}")
    result = dict(metadata)
    result["source_signature_algorithm"] = "hmac-sha256"
    result["source_signature"] = hmac.new(secret, _signature_payload(result), hashlib.sha256).hexdigest()
    return result


def verify_snapshot_signature(
    metadata: dict[str, Any], *, key: str | bytes | None = None, required: bool = True,
) -> None:
    """Verify the source HMAC; required mode fails closed on missing key/signature."""
    if metadata.get("provider") != "tushare_pit":
        return
    signature = str(metadata.get("source_signature", "")).strip()
    if not signature:
        if required:
            raise ValueError("TuShare PIT snapshot is missing its source_signature")
        return
    if metadata.get("source_signature_algorithm") != "hmac-sha256":
        raise ValueError("unsupported TuShare PIT source signature algorithm")
    secret = _snapshot_signing_key(key)
    if secret is None:
        if required:
            raise ValueError(f"{SNAPSHOT_SIGNING_KEY_ENV} is required to verify a TuShare PIT snapshot")
        return
    expected = hmac.new(secret, _signature_payload(metadata), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("TuShare PIT source signature verification failed")


def frame_content_sha256(frame: pd.DataFrame) -> str:
    """Return a deterministic digest for a normalized tabular snapshot.

    The digest is deliberately independent of Parquet engine metadata.  It is
    used to detect a changed row/column value after a snapshot has been
    materialized, while ``artifact_sha256`` below detects byte-level file
    tampering.
    """
    normalized = frame.copy(deep=False)
    normalized.attrs = {}  # Provenance is signed separately, not tabular content.
    for column in normalized.columns:
        if pd.api.types.is_datetime64_any_dtype(normalized[column]):
            normalized[column] = normalized[column].dt.strftime("%Y-%m-%dT%H:%M:%S.%f")
    # Stable column and row order makes the digest reproducible across
    # providers that return the same data in a different order.
    normalized = normalized.reindex(sorted(normalized.columns), axis=1)
    sort_columns = [c for c in ("trade_date", "symbol", "ts_code") if c in normalized.columns]
    if sort_columns:
        normalized = normalized.sort_values(sort_columns, kind="mergesort")
    normalized = normalized.reset_index(drop=True)
    options = dict(date_format="iso", double_precision=15, force_ascii=False, default_handler=str)
    # Stream precisely the legacy orient=split byte format. Large real panels
    # otherwise build a multi-GB native JSON buffer and another UTF-8 copy.
    # Keeping identical bytes preserves existing snapshot signatures.
    digest = hashlib.sha256()
    header = normalized.iloc[:0].to_json(orient="split", **options).split(',"index":', 1)[0]
    digest.update((header + ',"index":[').encode("utf-8"))
    chunk_size = 2048
    for start in range(0, len(normalized), chunk_size):
        if start:
            digest.update(b",")
        digest.update(",".join(str(i) for i in range(start, min(start + chunk_size, len(normalized)))).encode("ascii"))
    digest.update(b'],"data":[')
    for start in range(0, len(normalized), chunk_size):
        if start:
            digest.update(b",")
        payload = normalized.iloc[start:start + chunk_size].to_json(orient="values", **options)
        digest.update(payload[1:-1].encode("utf-8"))
    digest.update(b"]}")
    return digest.hexdigest()


def attest_snapshot_metadata(
    frame: pd.DataFrame,
    metadata: dict[str, Any],
    *,
    signing_key: str | bytes | None = None,
) -> dict[str, Any]:
    """Add generator/schema/content provenance to snapshot metadata."""
    result = dict(metadata)
    result.update({
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "generator_version": SNAPSHOT_GENERATOR_VERSION,
        "snapshot_content_sha256": frame_content_sha256(frame),
        "row_count": int(len(frame)),
        "column_count": int(len(frame.columns)),
    })
    if result.get("provider") == "tushare_pit" and _snapshot_signing_key(signing_key) is not None:
        result = sign_snapshot_metadata(result, key=signing_key)
    return result


def write_attested_parquet(
    frame: pd.DataFrame,
    path: str | Path,
    metadata: dict[str, Any],
    *,
    artifact_type: str = "snapshot",
    signing_key: str | bytes | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    """Write a Parquet artifact and a sidecar containing verifiable hashes."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(target, index=False)
    sidecar = target.with_suffix(".metadata.json")
    sidecar_metadata = attest_snapshot_metadata(frame, metadata, signing_key=signing_key)
    sidecar_metadata.update({
        "artifact_type": artifact_type,
        "artifact_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    })
    if sidecar_metadata.get("provider") == "tushare_pit":
        sidecar_metadata = sign_snapshot_metadata(sidecar_metadata, key=signing_key)
    sidecar.write_text(
        json.dumps(sidecar_metadata, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return target, sidecar, sidecar_metadata


def verify_attested_parquet(
    path: str | Path,
    *,
    require_artifact_hash: bool = False,
    verify_content_hash: bool = True,
    require_source_signature: bool = False,
    signing_key: str | bytes | None = None,
) -> dict[str, Any]:
    """Verify a Parquet sidecar and return its metadata.

    A provider name alone is not trusted.  Canonical PIT callers must carry a
    file hash; when present, the logical content hash is checked as well.
    """
    target = Path(path).expanduser().resolve()
    sidecar = target.with_suffix(".metadata.json")
    if not sidecar.is_file():
        raise ValueError(f"snapshot metadata sidecar required: {sidecar}")
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("snapshot metadata sidecar must contain a JSON object")
    artifact_hash = str(metadata.get("artifact_sha256", "")).strip()
    if require_artifact_hash and not artifact_hash:
        raise ValueError("attested snapshot sidecar requires artifact_sha256")
    if artifact_hash:
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != artifact_hash:
            raise ValueError("snapshot artifact_sha256 does not match Parquet contents")
    if verify_content_hash and metadata.get("snapshot_content_sha256"):
        frame = pd.read_parquet(target)
        actual_content = frame_content_sha256(frame)
        if actual_content != str(metadata["snapshot_content_sha256"]):
            raise ValueError("snapshot_content_sha256 does not match Parquet rows")
    verify_snapshot_signature(
        metadata, key=signing_key, required=require_source_signature,
    )
    return metadata


@dataclass(frozen=True, slots=True)
class SnapshotSpec:
    start_date: str
    end_date: str
    index_code: str = "000905.SH"
    benchmark_code: str = "000905.SH"
    feature_version: str = "cn_equity_v1"
    adjust: str = "point_in_time"
    financial_conflict_policy: str = "raise"


@dataclass(frozen=True, slots=True)
class TushareSnapshot:
    panel: pd.DataFrame
    metadata: dict[str, Any]

    def write(self, path: str | Path) -> tuple[Path, Path]:
        target, sidecar, attested = write_attested_parquet(
            self.panel, path, self.metadata, artifact_type="tushare_pit_snapshot",
        )
        return target, sidecar


def correct_snapshot_action_units(
    source: TushareSnapshot, corrections: list[dict], evidence: dict[str, bytes], *,
    factor_rounding_unit: float = 0.,
) -> TushareSnapshot:
    """Derive a new signed snapshot from verified, explicitly reviewed terms.

    No feature, raw price, financial fact or source event is overwritten in
    the parent. The optional factor quantum declares the vendor's published
    maximum quantum; finer decimal values use the next encoding precision.
    This is not a fitted price tolerance.
    """
    from .corporate_actions import apply_action_unit_corrections, attach_corporate_actions, read_corporate_actions

    metadata = source.metadata
    if metadata.get("provider") != "tushare_pit":
        raise ValueError("unit corrections require an authenticated TuShare PIT parent")
    verify_snapshot_signature(metadata, required=True)
    if frame_content_sha256(source.panel) != metadata["snapshot_content_sha256"]:
        raise ValueError("parent snapshot content hash mismatch")
    if not corrections or metadata.get("corporate_action_unit_corrections"):
        raise ValueError("provide corrections once against the original source snapshot")
    if not np.isfinite(factor_rounding_unit) or not 0 <= factor_rounding_unit <= .001:
        raise ValueError("factor rounding unit must be between zero and 0.001")
    panel = source.panel.copy(deep=False)
    panel.attrs = {}
    if factor_rounding_unit:
        factors = pd.to_numeric(panel.adj_factor, errors="coerce").to_numpy()
        if not (np.isfinite(factors) & (factors > factor_rounding_unit / 2)).all():
            raise ValueError("source factors must be positive and finite")
        quantum = np.zeros(len(panel), dtype=float)
        for decimal_places in range(6):
            candidate = factor_rounding_unit / 10 ** decimal_places
            aligned = np.isclose(factors / candidate, np.round(factors / candidate), rtol=0, atol=1e-6)
            quantum[(quantum == 0) & aligned] = candidate
        panel["adj_factor_rounding_unit"] = quantum
    actions, audit = apply_action_unit_corrections(
        read_corporate_actions(panel), corrections, evidence, as_of=metadata["end_date"],
    )
    panel = attach_corporate_actions(panel, actions)
    result = {k: v for k, v in metadata.items() if k not in {
        "source_signature", "source_signature_algorithm", "artifact_sha256", "artifact_type",
    }}
    result["snapshot_lineage"] = {
        "parent_data_snapshot_id": metadata["data_snapshot_id"],
        "parent_snapshot_content_sha256": metadata["snapshot_content_sha256"],
        "parent_artifact_sha256": metadata.get("artifact_sha256"),
        "operation": "reviewed_issuer_action_unit_correction",
    }
    result["corporate_action_unit_corrections"] = audit
    result["corporate_action_policy"] = {**metadata.get("corporate_action_policy", {}),
        "unit_correction": "event_specific_dated_issuer_terms",
        "reference_cash": "issuer_diluted_reference_separate_from_holder_entitlement",
        "adj_factor_rounding_unit": factor_rounding_unit,
        "adj_factor_rounding_rule": "coarsest_encoding_decimal_quantum_up_to_declared_maximum_otherwise_exact",
    }
    result = attest_snapshot_metadata(panel, result)
    result["data_snapshot_id"] = f"{metadata['data_snapshot_id']}:unit_corrected:{result['snapshot_content_sha256'][:24]}"
    result = sign_snapshot_metadata(result)
    panel.attrs["snapshot_metadata"] = result
    return TushareSnapshot(panel, result)


def point_in_time_adjust_prices(frame: pd.DataFrame) -> pd.DataFrame:
    """Scale raw OHLC by the factor known on that row.

    ``close * adj_factor`` is equivalent for return calculation to qfq prices
    up to a symbol-specific constant. It avoids looking up a future anchor
    factor, so historical feature and label calculations remain point in time.
    """
    required = {"adj_factor", "open", "high", "low", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"market frame missing adjustment columns: {', '.join(sorted(missing))}")
    out = frame.copy()
    factor = pd.to_numeric(out["adj_factor"], errors="coerce")
    if factor.isna().any() or not np.isfinite(factor.to_numpy(dtype=float)).all() or (factor <= 0).any():
        raise ValueError("adj_factor must be positive, finite and non-missing")
    out["adj_factor"] = factor
    for column in ("open", "high", "low", "close"):
        out[f"{column}_raw"] = pd.to_numeric(out[column], errors="raise")
        with np.errstate(over="ignore", invalid="ignore"):
            adjusted = out[f"{column}_raw"].astype(float) * factor
        if not np.isfinite(adjusted.to_numpy(dtype=float)).all():
            raise ValueError(f"point-in-time adjusted {column} must be finite")
        out[column] = adjusted
    out["price_adjustment"] = "point_in_time_factor"
    return out


def validate_market_calendar(
    frame: pd.DataFrame, metadata: dict[str, Any], *, start_date=None, end_date=None,
) -> list[str]:
    """Match market dates to explicit source calendar sessions, never weekdays.

    The caller verifies the metadata signature before trusting its calendar.
    Explicit bounds support replaying a book that opens inside the source
    calendar's longer history. Symbol-specific missing bars remain the
    responsibility of label/execution eligibility.
    """
    calendar = metadata.get("listing_calendar")
    if not isinstance(calendar, dict) or not isinstance(calendar.get("open_sessions"), list):
        raise ValueError("snapshot requires explicit open_sessions from trade_cal; rebuild legacy snapshots")
    if (calendar.get("provider"), calendar.get("endpoint"), calendar.get("exchange")) != ("tushare", "trade_cal", "SSE"):
        raise ValueError("snapshot calendar must come from TuShare SSE trade_cal")
    def iso_day(value):
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError("calendar dates must use YYYY-MM-DD")
        return value
    try:
        first, last = iso_day(calendar["start_date"]), iso_day(calendar["end_date"])
        sessions = [iso_day(value) for value in calendar["open_sessions"]]
        start = iso_day(str(start_date if start_date is not None else metadata["start_date"]))
        end = iso_day(str(end_date if end_date is not None else metadata["end_date"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid snapshot calendar dates or range") from exc
    if first > start or end > last or start > end:
        raise ValueError("snapshot calendar does not cover the required market range")
    if not sessions or sessions != sorted(set(sessions)) or sessions[0] < first or sessions[-1] > last:
        raise ValueError("snapshot open_sessions must be sorted, unique and within calendar coverage")
    expected = [day for day in sessions if start <= day <= end]
    dates = pd.to_datetime(frame["trade_date"].astype(str), errors="raise")
    if dates.isna().any() or not dates.eq(dates.dt.normalize()).all():
        raise ValueError("market calendar requires valid daily observation dates")
    actual = sorted(set(dates.dt.strftime("%Y-%m-%d")))
    if not expected or actual != expected:
        missing = sorted(set(expected) - set(actual))
        unexpected = sorted(set(actual) - set(expected))
        raise ValueError(f"market dates do not match trade_cal open_sessions: missing={missing[:5]}, unexpected={unexpected[:5]}")
    return expected


def assemble_tushare_snapshot(
    market: pd.DataFrame,
    *,
    daily_basic: pd.DataFrame | None,
    stock_basic: pd.DataFrame,
    index_snapshots: dict[str, list[str]],
    st_status: pd.DataFrame | None = None,
    limit_prices: pd.DataFrame | None = None,
    financial_snapshots: pd.DataFrame | None = None,
    industry_history: pd.DataFrame | None = None,
    dividend_history: pd.DataFrame | None = None,
    trade_calendar: pd.DataFrame | None = None,
    spec: SnapshotSpec,
) -> TushareSnapshot:
    """Normalize provider outputs into the pipeline's Market/Feature panel.

    ``st_status`` is required because TuShare ``stock_basic`` is current-state
    metadata and cannot prove historical ST status. Its rows must contain
    ``trade_date``, ``ts_code`` and ``is_st``.
    """
    required_market = {"ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount", "adj_factor"}
    missing = required_market - set(market.columns)
    if missing:
        raise ValueError(f"TuShare market data missing columns: {', '.join(sorted(missing))}")
    stock_required = {"ts_code", "list_date"}
    missing = stock_required - set(stock_basic.columns)
    if missing:
        raise ValueError(f"TuShare stock_basic missing columns: {', '.join(sorted(missing))}")
    if st_status is None:
        raise ValueError("historical st_status snapshot is required; current stock_basic is not PIT-safe")
    if limit_prices is None or limit_prices.empty:
        raise ValueError("historical limit-price snapshot is required for execution eligibility")
    status_required = {"trade_date", "ts_code", "is_st"}
    missing = status_required - set(st_status.columns)
    if missing:
        raise ValueError(f"ST status snapshot missing columns: {', '.join(sorted(missing))}")

    out = market.copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.strftime("%Y%m%d")
    listing_columns = [column for column in (
        "ts_code", "list_date", "list_date_source", "list_date_observation_date",
    ) if column in stock_basic]
    out = out.merge(stock_basic[listing_columns], on="ts_code", how="left", validate="many_to_one")
    from aipha.universe.historical import LISTING_DAYS_BASIS, listing_session_counts, normalize_trade_calendar

    out["listing_days"] = listing_session_counts(out["trade_date"], out["list_date"], trade_calendar)
    out["listing_days_basis"] = LISTING_DAYS_BASIS
    listing_calendar = normalize_trade_calendar(
        trade_calendar, min(pd.to_datetime(out["list_date"].astype(str)).min(), pd.Timestamp(spec.start_date)),
        pd.Timestamp(spec.end_date),
    )[["cal_date", "is_open"]]
    calendar_metadata = {
        "provider": "tushare", "endpoint": "trade_cal", "exchange": "SSE",
        "start_date": listing_calendar.cal_date.min().date().isoformat(),
        "end_date": listing_calendar.cal_date.max().date().isoformat(),
        "content_sha256": frame_content_sha256(listing_calendar),
        "listing_days_basis": LISTING_DAYS_BASIS,
        "count_includes_listing_session": True,
        "open_sessions": listing_calendar.loc[listing_calendar.is_open.eq(1), "cal_date"].dt.strftime("%Y-%m-%d").tolist(),
    }
    # Validate the requested boundaries too: a missing first/last session
    # must not shrink the declared range and escape gap detection.
    validate_market_calendar(out, {"listing_calendar": calendar_metadata,
                                   "start_date": pd.Timestamp(spec.start_date).date().isoformat(),
                                   "end_date": pd.Timestamp(spec.end_date).date().isoformat()})
    status = st_status[["trade_date", "ts_code", "is_st"]].copy()
    status["trade_date"] = pd.to_datetime(status["trade_date"]).dt.strftime("%Y%m%d")
    out = out.merge(status, on=["trade_date", "ts_code"], how="left", validate="one_to_one")
    if out["is_st"].isna().any():
        raise ValueError("historical ST status is incomplete for the market panel")
    out["is_suspended"] = out["vol"].fillna(0).eq(0)

    out["is_limit_up"] = False
    out["is_limit_down"] = False
    if limit_prices is not None and not limit_prices.empty:
        required_limits = {"ts_code", "trade_date", "up_limit", "down_limit"}
        missing = required_limits - set(limit_prices.columns)
        if missing:
            raise ValueError(f"limit price snapshot missing columns: {', '.join(sorted(missing))}")
        limits = limit_prices[list(required_limits)].copy()
        limits["trade_date"] = pd.to_datetime(limits["trade_date"]).dt.strftime("%Y%m%d")
        out = out.merge(limits, on=["ts_code", "trade_date"], how="left", validate="one_to_one")
        out["is_limit_up"] = out["up_limit"].notna() & (out["close"] >= out["up_limit"] - 0.01)
        out["is_limit_down"] = out["down_limit"].notna() & (out["close"] <= out["down_limit"] + 0.01)
        if out[["up_limit", "down_limit"]].isna().any().any():
            raise ValueError("historical limit-price snapshot is incomplete for the market panel")

    basic = daily_basic.copy() if daily_basic is not None else pd.DataFrame()
    if not basic.empty:
        out = out.merge(basic, on=["ts_code", "trade_date"], how="left", suffixes=("", "_basic"))
    out["is_index_member"] = _historical_membership(out["trade_date"], out["ts_code"], index_snapshots)
    if industry_history is not None and not industry_history.empty:
        out["industry"] = _historical_industry(out["trade_date"], out["ts_code"], industry_history)
    out = point_in_time_adjust_prices(out)
    out = out.rename(columns={"ts_code": "symbol", "vol": "volume"})
    columns = [
        "trade_date", "symbol", "open", "high", "low", "close", "volume", "amount", "adj_factor",
        "is_suspended", "is_limit_up", "is_limit_down", "is_index_member", "is_st", "listing_days",
    ]
    for column in columns:
        if column not in out:
            raise ValueError(f"normalized snapshot missing {column}")
    panel = out[columns + [column for column in out.columns if column not in columns]].copy()
    if financial_snapshots is not None and not financial_snapshots.empty:
        panel = join_pit_financials(panel, financial_snapshots)
    panel = build_cn_equity_features(panel)
    if dividend_history is not None:
        from .corporate_actions import attach_corporate_actions, normalize_tushare_dividends

        panel = attach_corporate_actions(panel, normalize_tushare_dividends(
            dividend_history, start_date=spec.start_date, end_date=spec.end_date,
        ))
    panel["feature_version"] = spec.feature_version
    digest = hashlib.sha256(pd.util.hash_pandas_object(panel.sort_values(["trade_date", "symbol"]), index=False).values.tobytes()).hexdigest()
    metadata = {
        "provider": "tushare_pit",
        "data_snapshot_id": f"tushare_pit:{spec.start_date}:{spec.end_date}:{digest[:24]}",
        "feature_version": spec.feature_version,
        "start_date": pd.to_datetime(panel["trade_date"].min()).strftime("%Y-%m-%d"),
        "end_date": pd.to_datetime(panel["trade_date"].max()).strftime("%Y-%m-%d"),
        "index_code": spec.index_code,
        "benchmark_code": spec.benchmark_code,
        "adjust": spec.adjust,
        "listing_calendar": calendar_metadata,
        "corporate_action_policy": {
            "initial_holdings": "cash_only",
            "event_scope": "ex_date_on_or_after_start_and_announced_by_end",
            "null_distribution_leg": "absent_only_if_other_leg_positive_and_no_settlement_date",
            "duplicate_terms": "deduplicate_identical_report_periods_sum_distinct_periods_with_matching_settlement",
        },
    }
    metadata = attest_snapshot_metadata(panel, metadata)
    panel.attrs["snapshot_metadata"] = metadata
    return TushareSnapshot(panel=panel, metadata=metadata)


def build_cn_equity_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Create causal low-frequency and Alpha158 features from frozen rows."""
    required = {"trade_date", "symbol", "close", "volume", "amount"}
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(f"feature input missing columns: {', '.join(sorted(missing))}")
    out = panel.copy()
    # Preserve the PIT provider's public field names while mapping their
    # canonical factor aliases. Both remain null for quarantined events.
    for canonical, source in (("grossprofit_margin", "gross_margin"), ("revenue_yoy", "or_yoy")):
        if canonical not in out and source in out:
            out[canonical] = out[source]
    out["trade_date"] = pd.to_datetime(out["trade_date"], errors="raise")
    out = out.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    grouped = out.groupby("symbol", sort=False, group_keys=False)
    returns = grouped["close"].pct_change(fill_method=None)
    for window in (5, 20, 60, 120):
        out[f"momentum_{window}"] = grouped["close"].transform(
            lambda values, n=window: values / values.shift(n) - 1.0,
        )
    out["reversal_5"] = -out["momentum_5"]
    out["volatility_20"] = returns.groupby(out["symbol"], sort=False).transform(
        lambda values: values.rolling(20, min_periods=15).std(),
    )
    out["average_turnover_value_20"] = grouped["amount"].transform(
        lambda values: values.rolling(20, min_periods=15).mean(),
    )
    out["log_market_cap"] = np.log1p(
        pd.to_numeric(out.get("circ_mv", pd.Series(index=out.index, dtype=float)), errors="coerce").clip(lower=0),
    )
    # Valuation and quality columns remain null until their PIT facts are
    # announced. No forward fill is done here: consumers may only fill within
    # an explicit PIT/as-of materialization step.
    for column in ("pe_ttm", "pb", "dv_ttm", "roe", "roa", "grossprofit_margin", "revenue_yoy"):
        if column in out:
            out[f"factor_{column}"] = pd.to_numeric(out[column], errors="coerce")
    if {"open", "high", "low"} <= set(out):
        from aipha.factors.qlib_alpha import Alpha158

        calculator = Alpha158()
        technical = calculator.compute(out.assign(datetime=out["trade_date"]))
        out = out.drop(columns=calculator.factor_names, errors="ignore").merge(
            technical[["trade_date", "symbol", *calculator.factor_names]].replace([np.inf, -np.inf], np.nan),
            on=["trade_date", "symbol"], how="left", validate="one_to_one", sort=False,
        )
    out["feature_set"] = "cn_equity_v1"
    return out


def join_pit_financials(panel: pd.DataFrame, snapshots: pd.DataFrame) -> pd.DataFrame:
    """Use the newest visible report period and its latest announced revision."""
    required = {"symbol", "report_date", "announce_date", "metric_name", "value"}
    missing = required - set(snapshots.columns)
    if missing:
        raise ValueError(f"PIT financial snapshot missing columns: {', '.join(sorted(missing))}")
    if not {"trade_date", "symbol"} <= set(panel):
        raise ValueError("PIT market panel requires trade_date and symbol")
    facts = snapshots.copy()
    # Pandas 2/3 can infer different datetime units; merge_asof requires ns.
    for column in ("announce_date", "report_date"):
        facts[column] = pd.to_datetime(facts[column], errors="raise").astype("datetime64[ns]")
    if facts[["symbol", "metric_name", "announce_date", "report_date"]].isna().any().any():
        raise ValueError("PIT financial identity and dates cannot be missing")
    if (facts["announce_date"] < facts["report_date"]).any():
        raise ValueError("PIT announce_date cannot precede report_date")
    identity = ["symbol", "metric_name", "report_date", "announce_date"]
    if (facts.groupby(identity)["value"].nunique(dropna=False) > 1).any():
        raise ValueError("conflicting PIT financial values share report and announcement date")
    facts = facts.drop_duplicates(identity).sort_values(["symbol", "metric_name", "announce_date", "report_date"])
    metrics = facts.metric_name.unique().tolist()
    reserved = {"announce_date", "report_date", "_asof_date", "_pit_row_id"}
    if any(not isinstance(metric, str) or not metric.strip() or metric in panel or metric in reserved for metric in metrics):
        raise ValueError("PIT financial metric names must be non-empty and cannot overwrite panel columns")
    if reserved & set(panel):
        raise ValueError("PIT market panel contains reserved financial join columns")
    result = panel.copy()
    result["_asof_date"] = pd.to_datetime(result["trade_date"], errors="raise").astype("datetime64[ns]")
    if result["_asof_date"].isna().any():
        raise ValueError("PIT market trade_date cannot be missing")
    result["_pit_row_id"] = np.arange(len(result))
    # Join thin date/identity tables globally by symbol instead of copying the
    # growing wide market frame once per symbol and metric.
    left = result[["symbol", "_asof_date", "_pit_row_id"]].sort_values("_asof_date", kind="stable")
    left["symbol"] = left.symbol.astype(object)
    for metric, metric_rows in facts.groupby("metric_name", sort=True):
        ordered = metric_rows.sort_values(["symbol", "announce_date", "report_date"], kind="stable")
        # A late revision of an older period cannot replace a newer report.
        newest = ordered.groupby("symbol", sort=False).report_date.cummax()
        events = ordered.loc[ordered.report_date.eq(newest)].drop_duplicates(["symbol", "announce_date"], keep="last")
        right = events[["symbol", "announce_date", "value"]].sort_values("announce_date", kind="stable")
        right["symbol"] = right.symbol.astype(object)
        merged = pd.merge_asof(left, right, by="symbol", left_on="_asof_date", right_on="announce_date", direction="backward")
        result[metric] = merged.sort_values("_pit_row_id", kind="stable")["value"].to_numpy()
    result = result.drop(columns=["_asof_date", "_pit_row_id"])
    result.index = panel.index
    return result


def financial_snapshots_from_provider(
    pit_provider: Any,
    symbols: list[str],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    statement_types: tuple[str, ...] = ("income", "balance", "cashflow"),
) -> pd.DataFrame:
    """Materialize PIT indicators and statement histories into the long form.

    ``get_statement_history`` is optional for lightweight fixture providers,
    but the production TuShare provider implements it for income, balance and
    cashflow.  Statement rows retain their ``announce_date`` so the subsequent
    as-of join can never expose a report before it was published.
    """
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        history = pit_provider.get_indicator_history(symbol)
        for indicator in history:
            for metric, value in indicator.indicators.items():
                rows.append({
                    "symbol": symbol,
                    "report_date": indicator.report_date.to_date(),
                    "announce_date": indicator.announce_date,
                    "metric_name": metric,
                    "value": None if value is None else float(value),
                })
        statement_loader = getattr(pit_provider, "get_statement_history", None)
        if callable(statement_loader):
            # Include facts announced before the market window.  The as-of
            # join needs the last previously published statement to seed the
            # first feature rows; starting at the first trade date would make
            # those rows look like missing fundamentals until the next filing.
            history_start = date.min
            history_end = end_date or date.max
            for statement_type in statement_types:
                statements = statement_loader(
                    symbol, history_start, history_end, statement_type,
                )
                for statement in statements or ():
                    for metric, value in statement.data.items():
                        rows.append({
                            "symbol": symbol,
                            "report_date": statement.report_date.to_date(),
                            "announce_date": statement.announce_date,
                            "metric_name": metric,
                            "value": None if value is None else float(value),
                        })
    result = pd.DataFrame(rows, columns=["symbol", "report_date", "announce_date", "metric_name", "value"])
    if end_date is not None and not result.empty:
        result = result.loc[pd.to_datetime(result.announce_date).dt.date <= end_date].reset_index(drop=True)
    return result


def _historical_membership(
    trade_dates: pd.Series,
    symbols: pd.Series,
    snapshots: dict[str, list[str]],
) -> pd.Series:
    if not snapshots:
        raise ValueError("historical index snapshots are required")
    normalized = {pd.Timestamp(key).strftime("%Y%m%d"): set(values) for key, values in snapshots.items()}
    keys = sorted(normalized)
    days = pd.to_datetime(trade_dates, format="mixed").dt.strftime("%Y%m%d").to_numpy()
    codes = symbols.to_numpy()
    visible = np.searchsorted(np.asarray(keys), days, side="right") - 1
    result = np.zeros(len(days), dtype=bool)
    for index in np.unique(visible):
        if index >= 0:
            positions = np.flatnonzero(visible == index)
            result[positions] = pd.Series(codes[positions]).isin(normalized[keys[index]]).to_numpy()
    return pd.Series(result, index=trade_dates.index, dtype=bool)


def _historical_industry(
    trade_dates: pd.Series,
    symbols: pd.Series,
    history: pd.DataFrame,
) -> pd.Series:
    """Map industry membership by its historical effective interval.

    Current ``stock_basic.industry`` or today's SW member list is not a valid
    substitute. A missing interval stays null; ambiguous simultaneous L1
    classifications fail closed instead of choosing one arbitrarily.
    """
    required = {"ts_code", "industry_name", "in_date", "out_date"}
    missing = required - set(history.columns)
    if missing:
        raise ValueError(f"historical industry data missing columns: {', '.join(sorted(missing))}")
    intervals = history[list(required)].copy()
    intervals["ts_code"] = intervals["ts_code"].astype(str)
    intervals["industry_name"] = intervals["industry_name"].astype(str).str.strip()
    intervals["in_date"] = pd.to_datetime(intervals["in_date"], format="%Y%m%d", errors="coerce")
    intervals["out_date"] = pd.to_datetime(intervals["out_date"], format="%Y%m%d", errors="coerce")
    if intervals["in_date"].isna().any():
        raise ValueError("historical industry intervals require valid in_date values")
    if (intervals["out_date"].notna() & (intervals["out_date"] < intervals["in_date"])).any():
        raise ValueError("historical industry out_date cannot precede in_date")
    by_symbol = {symbol: group for symbol, group in intervals.groupby("ts_code", sort=False)}
    labels = np.full(len(trade_dates), None, dtype=object)
    days = pd.to_datetime(trade_dates, format="mixed", errors="raise").astype("datetime64[ns]").to_numpy()
    observations = pd.DataFrame({"symbol": symbols.astype(str).to_numpy()})
    for symbol, positions in observations.groupby("symbol", sort=False).indices.items():
        candidates = by_symbol.get(symbol)
        if candidates is None:
            continue
        observed = days[positions]
        for record in candidates.itertuples(index=False):
            active = observed >= record.in_date.to_datetime64()
            if pd.notna(record.out_date):
                active &= observed <= record.out_date.to_datetime64()
            selected = positions[active]
            previous = labels[selected]
            conflict = pd.notna(previous) & (previous != record.industry_name)
            if conflict.any():
                day = pd.Timestamp(days[selected[np.flatnonzero(conflict)[0]]]).date()
                raise ValueError(f"ambiguous historical industry assignment for {symbol} on {day}")
            labels[selected] = record.industry_name
    return pd.Series(labels, index=trade_dates.index, dtype="string")


def build_tushare_snapshot(
    equity_provider: Any,
    spec: SnapshotSpec,
    *,
    st_status: pd.DataFrame | None = None,
    limit_prices: pd.DataFrame | None = None,
    pit_provider: Any | None = None,
    use_cache: bool = True,
) -> TushareSnapshot:
    """Fetch and assemble a frozen snapshot through existing TuShare adapters."""
    if pit_provider is None and hasattr(equity_provider, "api"):
        from aipha.data.tushare_pit_provider import TuSharePITProvider

        pit_provider = TuSharePITProvider(equity_provider, conflict_policy=spec.financial_conflict_policy)
    market = equity_provider.load_daily_bars_range(spec.start_date, spec.end_date, use_cache=use_cache)
    membership_loader = getattr(equity_provider, "load_index_membership_history", None)
    snapshots = None
    if callable(membership_loader):
        snapshots = membership_loader(spec.index_code, spec.start_date, spec.end_date, use_cache=use_cache)
        candidates = {symbol for members in snapshots.values() for symbol in members}
        # Retain every date for every historical candidate, including dates
        # outside its membership. Labels and liquidation still need those bars.
        market = market.loc[market.ts_code.isin(candidates)].copy()
        if market.empty:
            raise ValueError("historical index candidates have no market observations")
    basic = equity_provider.load_daily_basic_range(spec.start_date, spec.end_date, use_cache=use_cache)
    listing_loader = getattr(equity_provider, "load_historical_listing_dates", None)
    stock = (listing_loader(market, use_cache=use_cache) if callable(listing_loader)
             else equity_provider.load_stock_basic(use_cache=use_cache))
    calendar_loader = getattr(equity_provider, "load_trade_calendar", None)
    if not callable(calendar_loader):
        raise ValueError("TuShare equity provider must implement load_trade_calendar")
    listings = stock.set_index("ts_code")["list_date"].reindex(market["ts_code"].unique())
    if listings.isna().any() or listings.empty:
        raise ValueError("listing dates are required for every market symbol")
    first_listing = pd.to_datetime(listings.astype(str), errors="raise").min().strftime("%Y%m%d")
    trade_calendar = calendar_loader(first_listing, spec.end_date, use_cache=use_cache)
    factors = equity_provider.load_adj_factor_bulk(spec.start_date, spec.end_date, use_cache=use_cache)
    market = market.merge(factors[["ts_code", "trade_date", "adj_factor"]], on=["ts_code", "trade_date"], how="left", validate="one_to_one")
    if limit_prices is None:
        limit_loader = getattr(equity_provider, "load_limit_prices_range", None)
        if limit_loader is None:
            raise ValueError("TuShare equity provider does not implement load_limit_prices_range")
        limit_prices = limit_loader(spec.start_date, spec.end_date, use_cache=use_cache)
    benchmark_loader = getattr(equity_provider, "load_index_bars_range", None)
    if benchmark_loader is not None:
        benchmark = benchmark_loader(
            spec.benchmark_code, spec.start_date, spec.end_date, use_cache=use_cache,
        )
        if benchmark is not None and not benchmark.empty:
            benchmark = benchmark[["trade_date", "close"]].copy()
            benchmark["trade_date"] = pd.to_datetime(benchmark["trade_date"]).dt.strftime("%Y%m%d")
            benchmark = benchmark.rename(columns={"close": "benchmark_close"})
            market = market.merge(benchmark, on="trade_date", how="left", validate="many_to_one")
    if st_status is None:
        st_status = historical_st_status_from_provider(
            equity_provider, market, use_cache=use_cache,
        )
    financial_snapshots = None
    if pit_provider is not None:
        financial_snapshots = financial_snapshots_from_provider(
            pit_provider, sorted(market["ts_code"].dropna().astype(str).unique()),
            start_date=date.fromisoformat(f"{spec.start_date[:4]}-{spec.start_date[4:6]}-{spec.start_date[6:8]}"),
            end_date=date.fromisoformat(f"{spec.end_date[:4]}-{spec.end_date[4:6]}-{spec.end_date[6:8]}"),
        )
    start_year = int(spec.start_date[:4])
    end_year = int(spec.end_date[:4])
    if snapshots is None:
        snapshots = equity_provider.load_index_members_snapshots(spec.index_code, start_year, end_year, use_cache=use_cache)
    industry_loader = getattr(equity_provider, "load_sw_industry_history", None)
    industry_history = industry_loader(level="L1", use_cache=use_cache) if callable(industry_loader) else None
    dividend_loader = getattr(equity_provider, "load_dividend_history", None)
    dividend_history = dividend_loader(
        sorted(market["ts_code"].dropna().astype(str).unique()), use_cache=use_cache,
    ) if callable(dividend_loader) else None
    result = assemble_tushare_snapshot(
        market, daily_basic=basic, stock_basic=stock,
        index_snapshots=snapshots, st_status=st_status,
        limit_prices=limit_prices, financial_snapshots=financial_snapshots,
        industry_history=industry_history, spec=spec,
        dividend_history=dividend_history,
        trade_calendar=trade_calendar,
    )
    quality_loader = getattr(pit_provider, "get_data_quality_report", None)
    if callable(membership_loader):
        result.metadata["universe_history"] = {
            "provider": "tushare", "endpoint": "index_weight", "observation_frequency": "monthly",
            "membership_policy": "last_observed_weights_asof_trade_date",
            "observed_dates": sorted(snapshots), "historical_candidates": len(candidates),
            "retains_nonmember_dates_for_labels_and_liquidation": True,
        }
    if callable(quality_loader):
        result.metadata["financial_data_quality"] = quality_loader(end_date=pd.Timestamp(spec.end_date).date())
    if callable(quality_loader) or callable(membership_loader):
        result.metadata.update(attest_snapshot_metadata(result.panel, result.metadata))
        result.panel.attrs["snapshot_metadata"] = result.metadata
    return result


def historical_st_status_from_provider(
    equity_provider: Any,
    market: pd.DataFrame,
    *,
    use_cache: bool = True,
) -> pd.DataFrame:
    """Materialize a row-level historical ST flag from ``namechange``.

    ``stock_basic.name`` is current metadata and therefore cannot be used for
    a historical backtest.  TuShare's name-change intervals are the source of
    truth available through the equity adapter; this helper expands them only
    over the dates present in the frozen market snapshot.
    """
    required = {"ts_code", "trade_date"}
    missing = required - set(market.columns)
    if missing:
        raise ValueError(f"market missing columns for historical ST status: {', '.join(sorted(missing))}")
    parts: list[pd.DataFrame] = []
    for symbol, group in market.groupby("ts_code", sort=False):
        history = equity_provider.load_namechange(ts_code=str(symbol), use_cache=use_cache)
        if history is None:
            history = pd.DataFrame()
        if not history.empty:
            required_history = {"name", "start_date", "end_date", "ann_date"}
            missing_history = required_history - set(history.columns)
            if missing_history:
                raise ValueError(
                    "TuShare namechange history is not PIT-complete; missing "
                    + ", ".join(sorted(missing_history))
                )
        days = pd.to_datetime(group["trade_date"]).astype("datetime64[ns]")
        observed = days.to_numpy()
        flags = np.zeros(len(group), dtype=bool)
        for record in history.itertuples(index=False):
            name = str(getattr(record, "name", "")).upper()
            announced = getattr(record, "ann_date", pd.NaT)
            start = getattr(record, "start_date", pd.NaT)
            end = getattr(record, "end_date", pd.NaT)
            if ("ST" not in name and "退市" not in name) or pd.isna(announced):
                continue
            active = observed >= pd.Timestamp(announced).to_datetime64()
            if pd.notna(start):
                active &= observed >= pd.Timestamp(start).to_datetime64()
            if pd.notna(end):
                active &= observed <= pd.Timestamp(end).to_datetime64()
            flags |= active
        parts.append(pd.DataFrame({"ts_code": str(symbol), "trade_date": days.dt.date.to_numpy(), "is_st": flags}))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["ts_code", "trade_date", "is_st"])
