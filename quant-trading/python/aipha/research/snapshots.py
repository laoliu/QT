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
SNAPSHOT_GENERATOR_VERSION = "aipha.research.snapshots/1"
SNAPSHOT_SIGNING_KEY_ENV = "AIPHA_PIT_SNAPSHOT_KEY"
_SIGNED_SOURCE_FIELDS = (
    "provider", "data_snapshot_id", "feature_version", "start_date", "end_date",
    "index_code", "benchmark_code", "adjust", "snapshot_schema_version",
    "generator_version", "snapshot_content_sha256", "row_count", "column_count",
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
    normalized = frame.copy()
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
    payload = normalized.to_json(
        orient="split", date_format="iso", double_precision=15,
        force_ascii=False, default_handler=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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


@dataclass(frozen=True, slots=True)
class TushareSnapshot:
    panel: pd.DataFrame
    metadata: dict[str, Any]

    def write(self, path: str | Path) -> tuple[Path, Path]:
        target, sidecar, attested = write_attested_parquet(
            self.panel, path, self.metadata, artifact_type="tushare_pit_snapshot",
        )
        return target, sidecar


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
    if factor.isna().any() or (factor <= 0).any():
        raise ValueError("adj_factor must be positive and non-missing")
    for column in ("open", "high", "low", "close"):
        out[f"{column}_raw"] = pd.to_numeric(out[column], errors="raise")
        out[column] = out[column].astype(float) * factor
    out["price_adjustment"] = "point_in_time_factor"
    return out


def assemble_tushare_snapshot(
    market: pd.DataFrame,
    *,
    daily_basic: pd.DataFrame | None,
    stock_basic: pd.DataFrame,
    index_snapshots: dict[str, list[str]],
    st_status: pd.DataFrame | None = None,
    limit_prices: pd.DataFrame | None = None,
    financial_snapshots: pd.DataFrame | None = None,
    spec: SnapshotSpec,
) -> TushareSnapshot:
    """Normalize provider outputs into the pipeline's Market/Feature panel.

    ``st_status`` is required because TuShare ``stock_basic`` is current-state
    metadata and cannot prove historical ST status. Its rows must contain
    ``trade_date``, ``ts_code`` and ``is_st``.
    """
    required_market = {"ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"}
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
    out = out.merge(stock_basic[["ts_code", "list_date"]], on="ts_code", how="left", validate="many_to_one")
    out["listing_days"] = (
        pd.to_datetime(out["trade_date"]) - pd.to_datetime(out["list_date"], format="%Y%m%d", errors="coerce")
    ).dt.days
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
    out["adj_factor"] = pd.to_numeric(out.get("adj_factor", 1.0), errors="coerce")
    if out["adj_factor"].isna().any():
        raise ValueError("TuShare adj_factor is required for every market row")
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
    }
    metadata = attest_snapshot_metadata(panel, metadata)
    panel.attrs["snapshot_metadata"] = metadata
    return TushareSnapshot(panel=panel, metadata=metadata)


def build_cn_equity_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Create a compact, causal low-frequency feature set from frozen rows."""
    required = {"trade_date", "symbol", "close", "volume", "amount"}
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(f"feature input missing columns: {', '.join(sorted(missing))}")
    out = panel.copy()
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
    out["feature_set"] = "cn_equity_v1"
    return out


def join_pit_financials(panel: pd.DataFrame, snapshots: pd.DataFrame) -> pd.DataFrame:
    """As-of join long-form financial facts using announcement dates only."""
    required = {"symbol", "report_date", "announce_date", "metric_name", "value"}
    missing = required - set(snapshots.columns)
    if missing:
        raise ValueError(f"PIT financial snapshot missing columns: {', '.join(sorted(missing))}")
    facts = snapshots.copy()
    # merge_asof requires identical datetime units.  Pandas 2/3 can infer
    # different units from Python ``date`` objects versus frame timestamps;
    # normalize both sides explicitly before the causal join.
    facts["announce_date"] = pd.to_datetime(facts["announce_date"]).astype("datetime64[ns]")
    facts["report_date"] = pd.to_datetime(facts["report_date"]).astype("datetime64[ns]")
    if (facts["announce_date"] < facts["report_date"]).any():
        raise ValueError("PIT announce_date cannot precede report_date")
    facts = facts.sort_values(["symbol", "metric_name", "announce_date"])
    result = panel.copy()
    result["_asof_date"] = pd.to_datetime(result["trade_date"]).astype("datetime64[ns]")
    for metric, metric_rows in facts.groupby("metric_name", sort=True):
        metric_rows = metric_rows.rename(columns={"value": str(metric)})
        metric_rows = metric_rows[["symbol", "announce_date", str(metric)]].drop_duplicates(
            ["symbol", "announce_date"], keep="last",
        )
        merged_parts: list[pd.DataFrame] = []
        for symbol, rows in result.groupby("symbol", sort=False):
            left = rows.sort_values("_asof_date")
            right = metric_rows[metric_rows["symbol"] == symbol].sort_values("announce_date")
            if right.empty:
                left[str(metric)] = pd.NA
            else:
                left = pd.merge_asof(
                    left, right.drop(columns=["symbol"]),
                    left_on="_asof_date", right_on="announce_date", direction="backward",
                )
                # The announcement key is only a join aid.  Drop it after
                # every metric so the next metric cannot create duplicate
                # announce_date columns in the evolving panel.
                left = left.drop(columns=["announce_date"], errors="ignore")
            merged_parts.append(left)
        result = pd.concat(merged_parts).sort_index()
    return result.drop(columns=["_asof_date"], errors="ignore")


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
    return pd.DataFrame(rows, columns=["symbol", "report_date", "announce_date", "metric_name", "value"])


def _historical_membership(
    trade_dates: pd.Series,
    symbols: pd.Series,
    snapshots: dict[str, list[str]],
) -> pd.Series:
    if not snapshots:
        raise ValueError("historical index snapshots are required")
    normalized = {pd.Timestamp(key).strftime("%Y%m%d"): set(values) for key, values in snapshots.items()}
    keys = sorted(normalized)
    result: list[bool] = []
    for raw_date, symbol in zip(trade_dates, symbols):
        day = pd.Timestamp(raw_date).strftime("%Y%m%d")
        prior = [key for key in keys if key <= day]
        result.append(bool(prior and symbol in normalized[prior[-1]]))
    return pd.Series(result, index=trade_dates.index, dtype=bool)


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

        pit_provider = TuSharePITProvider(equity_provider)
    market = equity_provider.load_daily_bars_range(spec.start_date, spec.end_date, use_cache=use_cache)
    basic = equity_provider.load_daily_basic_range(spec.start_date, spec.end_date, use_cache=use_cache)
    stock = equity_provider.load_stock_basic(use_cache=use_cache)
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
    snapshots = equity_provider.load_index_members_snapshots(spec.index_code, start_year, end_year, use_cache=use_cache)
    return assemble_tushare_snapshot(
        market, daily_basic=basic, stock_basic=stock,
        index_snapshots=snapshots, st_status=st_status,
        limit_prices=limit_prices, financial_snapshots=financial_snapshots, spec=spec,
    )


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
    rows: list[dict[str, Any]] = []
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
        for raw_date in pd.to_datetime(group["trade_date"]).dt.date:
            is_st = False
            if not history.empty:
                day = pd.Timestamp(raw_date).date()
                for record in history.itertuples(index=False):
                    name = str(getattr(record, "name", "")).upper()
                    announced = getattr(record, "ann_date", pd.NaT)
                    start = getattr(record, "start_date", pd.NaT)
                    end = getattr(record, "end_date", pd.NaT)
                    if "ST" not in name and "退市" not in name:
                        continue
                    # A historical effective interval alone does not prove
                    # that the status was public at the time.  Exclude the
                    # record until TuShare's announcement date is visible.
                    if pd.isna(announced) or day < pd.Timestamp(announced).date():
                        continue
                    if pd.notna(start) and day < pd.Timestamp(start).date():
                        continue
                    if pd.notna(end) and day > pd.Timestamp(end).date():
                        continue
                    is_st = True
                    break
            rows.append({"ts_code": str(symbol), "trade_date": raw_date, "is_st": is_st})
    return pd.DataFrame(rows, columns=["ts_code", "trade_date", "is_st"])
