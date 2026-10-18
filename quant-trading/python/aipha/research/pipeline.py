"""Canonical A-share low-frequency research pipeline.

The pipeline is intentionally a thin orchestration layer around the existing
PIT providers, Alpha/feature implementations and LightGBM selector.  It gives
those components one stable contract while leaving legacy strategies usable.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .contracts import TargetPosition, price_limit_flags, validate_market_frame
from .execution_plan import build_execution_plan
from .evaluation import benchmark_diagnostics, feature_missing_rates, json_safe_metrics
from aipha.utils.metrics import compute_annualized_volatility


@dataclass(frozen=True, slots=True)
class ResearchConfig:
    """Default research policy for A-share low-frequency strategies."""

    data_provider: str = "tushare_pit"
    adjust: str = "point_in_time"
    feature_version: str = "cn_equity_v1"
    universe: str = "csi500"
    min_listing_days: int = 252
    rebalance: str = "monthly"
    train_years: int = 3
    test_months: int = 1
    forward_days: int = 20
    label_skip_days: int = 1
    purge_days: int = 20
    top_k: int = 30
    max_position_weight: float = 0.05
    max_industry_weight: float | None = 0.20
    max_daily_turnover: float = 0.30
    model_type: str = "lightgbm"
    objective: str = "lambdarank"
    initial_capital: float = 1_000_000.0
    n_estimators: int = 300
    commission_rate: float = 8.54e-05
    commission_min: float = 5.0
    stamp_tax_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_bps: float = 15.0
    feature_groups: tuple[str, ...] = (
        "value", "quality", "momentum", "volatility", "liquidity", "technical",
    )

    def __post_init__(self) -> None:
        from aipha.universe.historical import validate_listing_threshold

        validate_listing_threshold(self.min_listing_days)
        if self.model_type not in {"lightgbm", "elasticnet"}:
            raise ValueError("canonical research pipeline supports model_type=lightgbm or elasticnet")
        if self.adjust != "point_in_time":
            raise ValueError("canonical research pipeline requires adjust=point_in_time")
        if self.rebalance != "monthly":
            raise ValueError("canonical research pipeline currently uses monthly rebalance")
        if self.train_years < 1 or self.test_months < 1 or self.n_estimators < 1:
            raise ValueError("train_years and test_months must be positive")
        if self.forward_days < 1 or self.purge_days < self.forward_days:
            raise ValueError("purge_days must be >= forward_days")
        if self.label_skip_days < 1:
            raise ValueError("label_skip_days must be >= 1 because signals are generated after the close")
        if not 0 < self.max_position_weight <= 1:
            raise ValueError("max_position_weight must be in (0, 1]")
        if self.max_industry_weight is not None and not 0 < self.max_industry_weight <= 1:
            raise ValueError("max_industry_weight must be None or in (0, 1]")
        if not 0 < self.max_daily_turnover <= 2:
            raise ValueError("max_daily_turnover must be in (0, 2]")

    def model_config_hash(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, ensure_ascii=False, default=list)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_forward_labels(
    frame: pd.DataFrame,
    *,
    horizon_days: int = 20,
    skip_days: int = 1,
    price_column: str = "close",
    entry_column: str = "open",
) -> pd.DataFrame:
    """Create forward labels aligned to next-session-open execution.

    The entry is ``entry_column`` at T+skip_days on the panel's shared trading
    calendar; the exit is ``price_column`` horizon_days trading intervals
    after entry. Missing or untradeable entry/exit bars invalidate the label
    rather than moving the holding period to a later symbol observation. For the
    canonical after-close signal, ``skip_days=1`` therefore measures
    next-open to the close after 20 trading intervals, matching the model
    selector's purge horizon.
    The input is never mutated.
    """
    if horizon_days < 1 or skip_days < 0:
        raise ValueError("horizon_days must be positive and skip_days non-negative")
    missing = {price_column, entry_column} - set(frame.columns)
    if missing:
        raise ValueError(f"missing label price columns: {', '.join(sorted(missing))}")
    out = frame.copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"], errors="raise")
    out = out.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    if out.duplicated(["trade_date", "symbol"]).any():
        raise ValueError("label frame contains duplicate trade_date/symbol rows")
    calendar = pd.DatetimeIndex(out["trade_date"].unique()).sort_values()
    day_numbers = calendar.get_indexer(out["trade_date"])
    entry_blocked, _ = price_limit_flags(out, price_column=entry_column)
    _, exit_blocked = price_limit_flags(out, price_column=price_column)
    suspended = out.get("is_suspended", pd.Series(False, index=out.index)).fillna(True).astype(bool)
    prices = pd.DataFrame({
        "entry": pd.to_numeric(out[entry_column], errors="coerce").where(~(entry_blocked | suspended)),
        "exit": pd.to_numeric(out[price_column], errors="coerce").where(~(exit_blocked | suspended)),
    })
    prices.index = pd.MultiIndex.from_frame(out[["trade_date", "symbol"]])

    def at_offset(column: str, offset: int) -> np.ndarray:
        indices = day_numbers + offset
        target_dates = np.full(len(out), np.datetime64("NaT"), dtype="datetime64[ns]")
        valid = indices < len(calendar)
        target_dates[valid] = calendar.to_numpy()[indices[valid]]
        keys = pd.MultiIndex.from_arrays([target_dates, out["symbol"]])
        return prices[column].reindex(keys).to_numpy(dtype=float)

    base = at_offset("entry", skip_days)
    future = at_offset("exit", skip_days + horizon_days)
    valid = np.isfinite(base) & (base > 0) & np.isfinite(future) & (future > 0)
    out["fwd_return"] = np.divide(future, base, out=np.full(len(out), np.nan), where=valid) - 1.0
    out = out.dropna(subset=["fwd_return"]).copy()
    out["fwd_return_rank"] = out.groupby("trade_date")["fwd_return"].rank(
        method="average", pct=True,
    )
    out["trade_date"] = out["trade_date"].dt.date
    return out


def build_target_positions(
    predictions: pd.DataFrame,
    *,
    top_k: int = 30,
    max_position_weight: float = 0.05,
    max_industry_weight: float | None = None,
    industry_column: str = "industry",
    run_id: str = "",
    model_id: str = "",
) -> list[TargetPosition]:
    """Turn one or more prediction dates into capped equal-weight targets."""
    required = {"trade_date", "symbol", "score"}
    missing = required - set(predictions.columns)
    if missing:
        raise ValueError(f"predictions missing columns: {', '.join(sorted(missing))}")
    if top_k < 1 or max_position_weight <= 0:
        raise ValueError("top_k and max_position_weight must be positive")
    normalized = predictions.copy()
    normalized["trade_date"] = pd.to_datetime(normalized["trade_date"]).dt.date
    period = pd.to_datetime(normalized["trade_date"].astype(str)).dt.to_period("M")
    last_dates = normalized.assign(_period=period).groupby("_period")["trade_date"].max()
    normalized = normalized[normalized["trade_date"].isin(set(last_dates))]
    rows: list[TargetPosition] = []
    for raw_date, group in normalized.groupby("trade_date", sort=True):
        ranked = group.dropna(subset=["score"]).sort_values(
            ["score", "symbol"], ascending=[False, True],
        )
        if ranked.empty:
            continue
        weight = min(max_position_weight, 1.0 / min(top_k, len(ranked)))
        cap_enabled = max_industry_weight is not None and industry_column in ranked.columns
        selected: list[Any] = []
        industry_weights: dict[str, float] = {}
        for row in ranked.itertuples(index=False):
            if len(selected) >= top_k:
                break
            industry = str(getattr(row, industry_column, "__UNKNOWN__")).strip() or "__UNKNOWN__"
            if cap_enabled and industry_weights.get(industry, 0.0) + weight > float(max_industry_weight) + 1e-12:
                continue
            selected.append(row)
            if cap_enabled:
                industry_weights[industry] = industry_weights.get(industry, 0.0) + weight
        for rank, row in enumerate(selected, start=1):
            rows.append(TargetPosition(
                rebalance_date=pd.Timestamp(raw_date).date(),
                symbol=str(row.symbol),
                target_weight=float(weight),
                reason=f"ml_rank_{rank}",
                run_id=run_id,
                model_id=model_id,
                signal_id=f"{run_id or model_id or 'research'}:{pd.Timestamp(raw_date).date().isoformat()}:{row.symbol}",
            ))
    return rows


class ResearchPipeline:
    """One canonical train/evaluate/export entry point."""

    def __init__(self, config: ResearchConfig | None = None) -> None:
        self.config = config or ResearchConfig()
        self.run_id = uuid.uuid4().hex

    def prepare_labels(self, market: pd.DataFrame) -> pd.DataFrame:
        checked = validate_market_frame(market)
        return build_forward_labels(
            checked,
            horizon_days=self.config.forward_days,
            skip_days=self.config.label_skip_days,
        )

    def run_tushare(
        self,
        equity_provider: Any,
        output_root: str | Path,
        *,
        start_date: str,
        end_date: str,
        index_code: str = "000905.SH",
        benchmark_code: str | None = None,
        pit_provider: Any | None = None,
        st_status: pd.DataFrame | None = None,
        limit_prices: pd.DataFrame | None = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """Build a frozen TuShare PIT snapshot and execute the canonical run.

        This is the production entry point for historical research.  Provider
        objects are injected so tests can use deterministic adapters; no
        AKShare fallback or broker connection is possible from this method.
        Financial histories are joined by announcement date when a
        ``TuSharePITProvider`` is supplied, while historical ST status is
        materialized from name-change intervals when omitted.
        """
        from .snapshots import SnapshotSpec, build_tushare_snapshot

        if pit_provider is None:
            from aipha.data.tushare_pit_provider import TuSharePITProvider

            pit_provider = TuSharePITProvider(equity_provider)

        spec = SnapshotSpec(
            start_date=start_date,
            end_date=end_date,
            index_code=index_code,
            benchmark_code=benchmark_code or index_code,
            feature_version=self.config.feature_version,
            adjust=self.config.adjust,
        )
        snapshot = build_tushare_snapshot(
            equity_provider,
            spec,
            st_status=st_status,
            limit_prices=limit_prices,
            pit_provider=pit_provider,
            use_cache=use_cache,
        )
        return self.run(
            snapshot.panel,
            output_root,
            data_snapshot_id=snapshot.metadata["data_snapshot_id"],
        )

    def train_oos(self, features: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any], Any]:
        """Train the canonical LightGBM selector and return OOS predictions."""
        from aipha.models.ml_stock_selector import MLSelectorConfig, MLStockSelector

        required = {"symbol"}
        if not required.issubset(features.columns) or not ({"datetime", "trade_date"} & set(features.columns)):
            raise ValueError("features must contain a date column (datetime or trade_date) and symbol")
        labelled = features.copy()
        if "datetime" not in labelled.columns:
            labelled["datetime"] = pd.to_datetime(labelled["trade_date"])
        if "fwd_return_rank" not in labelled.columns:
            market = labelled.drop(columns=["datetime"], errors="ignore")
            labelled = self.prepare_labels(market)
            labelled["datetime"] = pd.to_datetime(labelled["trade_date"])
        # Form labels on the complete market tape before checking eligibility
        # at the signal date. Removing a later ST/suspended/non-member row
        # first would silently move its earlier signal's entry/exit forward.
        labelled = _filter_historical_universe(labelled, min_listing_days=self.config.min_listing_days)
        if "fwd_return" in labelled:
            labelled["fwd_return_rank"] = labelled.groupby("datetime")["fwd_return"].rank(
                method="average", pct=True,
            )
        excluded = {
            "datetime", "trade_date", "symbol", "open", "high", "low", "close",
            "volume", "amount", "vol", "pre_close", "change", "pct_chg",
            "adj_factor", "is_suspended", "is_limit_up", "is_limit_down",
            "up_limit", "down_limit",
            "benchmark_close", "benchmark_return",
            "is_index_member", "is_st", "listing_days",
            "fwd_return", "fwd_return_rank", "fwd_return_raw", "label",
            "feature_version", "feature_set", "industry", "sector", "sw_industry",
        }
        feature_cols = [
            column for column in labelled.columns
            if column not in excluded and not column.endswith("_raw")
            and labelled[column].dtype.kind in ("f", "i", "u")
        ]
        if not feature_cols:
            raise ValueError("no numeric feature columns found after excluding labels and market metadata")
        selector_config = MLSelectorConfig(
            model_type=self.config.model_type,
            objective=self.config.objective,
            train_years=self.config.train_years,
            predict_months=self.config.test_months,
            forward_days=self.config.forward_days,
            label_skip_days=self.config.label_skip_days,
            purge_days=self.config.purge_days,
            top_k=self.config.top_k,
            feature_cols=feature_cols,
            n_estimators=self.config.n_estimators,
        )
        selector = MLStockSelector(selector_config)
        oos = selector.walk_forward_train(labelled)
        summary = json_safe_metrics(selector.get_wf_summary())
        summary.update({
            "run_id": self.run_id,
            "feature_version": self.config.feature_version,
            "model_config_hash": self.config.model_config_hash(),
            "model_type": self.config.model_type,
        })
        return oos, summary, selector

    def run(
        self,
        features: pd.DataFrame,
        output_root: str | Path,
        *,
        data_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        """Run OOS training/evaluation and persist an immutable run directory.

        ``features`` must contain a point-in-time feature panel plus daily OHLCV.
        It can be read from Parquet, so tests and offline research never need a
        network connection or broker account.
        """
        source = features.copy()
        if "trade_date" not in source and "datetime" in source:
            source = source.rename(columns={"datetime": "trade_date"})
        source = validate_market_frame(source)
        input_start = pd.Timestamp(source["trade_date"].min()).date().isoformat()
        input_end = pd.Timestamp(source["trade_date"].max()).date().isoformat()
        source["datetime"] = pd.to_datetime(source["trade_date"])
        if _filter_historical_universe(source, min_listing_days=self.config.min_listing_days).empty:
            raise ValueError("historical universe filter removed all rows")
        # Preserve only explicitly versioned inputs when the caller supplies it.
        if "feature_version" in source:
            versions = source["feature_version"].dropna().astype(str).unique()
            if len(versions) != 1 or versions[0] != self.config.feature_version:
                raise ValueError(
                    f"expected feature_version={self.config.feature_version!r}; got {versions.tolist()}"
                )
        else:
            raise ValueError("versioned research input requires a feature_version column")

        metadata = _load_snapshot_metadata(features)
        if metadata["provider"] != self.config.data_provider:
            raise ValueError(
                f"snapshot provider must be {self.config.data_provider!r}; got {metadata['provider']!r}"
            )
        if metadata["feature_version"] != self.config.feature_version:
            raise ValueError("snapshot metadata feature_version does not match research config")
        if pd.Timestamp(metadata["start_date"]).date().isoformat() != input_start or pd.Timestamp(metadata["end_date"]).date().isoformat() != input_end:
            raise ValueError("snapshot metadata date range does not match the input panel")
        if data_snapshot_id is not None and data_snapshot_id != metadata["data_snapshot_id"]:
            raise ValueError("data_snapshot_id does not match frozen snapshot metadata")
        data_snapshot_id = metadata["data_snapshot_id"]

        oos, wf_summary, selector = self.train_oos(source)
        if oos.empty:
            raise ValueError("walk-forward training produced no out-of-sample predictions")
        oos = oos.rename(columns={"datetime": "trade_date", "pred_score": "score"})
        oos["trade_date"] = pd.to_datetime(oos["trade_date"]).dt.date
        oos["model_id"] = self.run_id
        oos["feature_version"] = self.config.feature_version
        # Selector predictions deliberately contain only scores/labels.
        # Carry the signal-date classification into the portfolio boundary;
        # otherwise a cap configured in YAML silently becomes ineffective.
        industry_columns = [c for c in ("industry", "sector", "sw_industry") if c in source]
        if industry_columns:
            classifications = source[["trade_date", "symbol", *industry_columns]].copy()
            oos = oos.drop(columns=industry_columns, errors="ignore").merge(
                classifications, on=["trade_date", "symbol"], how="left", validate="one_to_one",
            )
            if "industry" not in oos:
                oos["industry"] = oos[industry_columns[0]]
        targets = build_target_positions(
            oos,
            top_k=self.config.top_k,
            max_position_weight=self.config.max_position_weight,
            max_industry_weight=self.config.max_industry_weight,
            run_id=self.run_id,
            model_id=self.run_id,
        )
        from .backtest import run_portfolio_backtest

        backtest = run_portfolio_backtest(source, oos, targets, self.config)
        feature_coverage = feature_missing_rates(
            _filter_historical_universe(source, min_listing_days=self.config.min_listing_days),
            selector.config.feature_cols,
        )
        folds = wf_summary.get("folds", [])
        if not folds or any(not row.get("train_start") or not row.get("train_end") for row in folds):
            raise ValueError("walk-forward training must report actual training dates for every fold")
        training_period = {
            "scope": "union_of_walk_forward_training_windows",
            "start": min(pd.Timestamp(row["train_start"]).date() for row in folds).isoformat(),
            "end": max(pd.Timestamp(row["train_end"]).date() for row in folds).isoformat(),
            "latest_fold_start": pd.Timestamp(folds[-1]["train_start"]).date().isoformat(),
            "latest_fold_end": pd.Timestamp(folds[-1]["train_end"]).date().isoformat(),
        }
        metrics = _evaluate_predictions(oos, targets, self.config)
        metrics.update(backtest["metrics"])
        from .overfitting import portfolio_selection_cscv

        cscv_returns, cscv_analysis = portfolio_selection_cscv(
            source, oos, self.config, baseline_returns=backtest["daily_returns"],
        )
        cscv_method = cscv_analysis["method"] if cscv_analysis["available"] else f"unavailable_{cscv_analysis['reason']}"
        metrics.update({
            "pbo": cscv_analysis["pbo"], "pbo_method": cscv_method,
            "selection_pbo": cscv_analysis["pbo"], "selection_pbo_method": cscv_method,
        })
        metrics["pbo_proxy"] = _pbo_proxy(wf_summary)
        evaluation_diagnostics = _build_evaluation_diagnostics(
            source, oos, targets, self.config, portfolio_returns=backtest["daily_returns"],
        )
        run_root = Path(output_root) / self.run_id
        run_root.mkdir(parents=True, exist_ok=False)
        from .snapshots import write_attested_parquet

        input_path, _, input_metadata = write_attested_parquet(
            source,
            run_root / "input_snapshot.parquet",
            metadata,
            artifact_type="research_input_snapshot",
        )
        selector.save(run_root / "model")
        _write_model_manifest(
            run_root / "model",
            model_type=self.config.model_type,
            feature_version=self.config.feature_version,
            feature_cols=selector.config.feature_cols,
            model_config_hash=self.config.model_config_hash(),
        )
        oos_metadata = {
            "provider": "aipha_research",
            "data_snapshot_id": data_snapshot_id,
            "feature_version": self.config.feature_version,
            "start_date": str(oos["trade_date"].min()),
            "end_date": str(oos["trade_date"].max()),
            "parent_snapshot_content_sha256": metadata.get("snapshot_content_sha256", ""),
        }
        _, _, oos_artifact_metadata = write_attested_parquet(
            oos,
            run_root / "oos_predictions.parquet",
            oos_metadata,
            artifact_type="oos_predictions",
        )
        _, _, cscv_metadata = write_attested_parquet(
            cscv_returns.reset_index(), run_root / "cscv_returns.parquet",
            {**oos_metadata, "candidate_ids": list(cscv_returns.columns),
             "start_date": str(cscv_returns.index.min().date()) if len(cscv_returns) else None,
             "end_date": str(cscv_returns.index.max().date()) if len(cscv_returns) else None,
             "parent_oos_content_sha256": oos_artifact_metadata["snapshot_content_sha256"],
             "model_config_hash": self.config.model_config_hash()},
            artifact_type="cscv_candidate_net_returns",
        )
        cscv_analysis.update({
            "run_id": self.run_id, "model_id": self.run_id, "data_snapshot_id": data_snapshot_id,
            "candidate_returns_content_sha256": cscv_metadata["snapshot_content_sha256"],
        })
        (run_root / "cscv_analysis.json").write_text(
            json.dumps(cscv_analysis, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8",
        )
        pd.DataFrame(backtest["daily_returns"]).to_parquet(
            run_root / "portfolio_returns.parquet", index=False,
        )
        pd.DataFrame(backtest["trades"]).to_json(
            run_root / "trades.json", orient="records", date_format="iso", force_ascii=False, indent=2,
        )
        (run_root / "corporate_actions.json").write_text(
            json.dumps(backtest["corporate_actions"], ensure_ascii=False, indent=2), encoding="utf-8",
        )
        (run_root / "target_positions.json").write_text(
            json.dumps([position.to_wire() for position in targets], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        execution_plans = []
        rebalance_dates = sorted({position.rebalance_date for position in targets})
        for rebalance_date in rebalance_dates:
            group = [position for position in targets if position.rebalance_date == rebalance_date]
            plan = build_execution_plan(group, mode="paper", run_id=self.run_id, model_id=self.run_id)
            plan_path = run_root / "execution_plans" / f"{rebalance_date.isoformat()}.json"
            plan.write(plan_path)
            execution_plans.append(str(plan_path.relative_to(run_root)))
        report = {
            "run_id": self.run_id,
            "strategy_id": "aipha-ml-ranking",
            "strategy_version": "1.0.0",
            "engine": "aipha",
            "data_as_of": max(oos["trade_date"]).isoformat(),
            "model_type": self.config.model_type,
            "feature_version": self.config.feature_version,
            "data_snapshot_id": data_snapshot_id,
            "config": asdict(self.config),
            "walk_forward": wf_summary,
            "training_period": training_period,
            "metrics": metrics,
            "n_oos_predictions": int(len(oos)),
            "n_target_positions": len(targets),
            "execution_plans": execution_plans,
            "feature_missing_rate": feature_coverage["mean"],
            "feature_missing_rates": feature_coverage,
            "feature_missing_rate_scope": "historical_universe_model_columns",
            "evaluation_window": backtest["evaluation_window"],
            "snapshot_metadata": metadata,
            "accounting": backtest["accounting"],
            "final_positions": backtest["final_positions"],
            "artifact_attestation": {
                "input_snapshot": input_metadata,
                "oos_predictions": oos_artifact_metadata,
                "cscv_returns": cscv_metadata,
            },
            "diagnostics": {
                "pbo_proxy": metrics["pbo_proxy"],
                "pbo": metrics.get("pbo"),
                "pbo_method": metrics.get("pbo_method", "unavailable"),
                "selection_pbo": metrics.get("selection_pbo"),
                "selection_pbo_method": metrics.get("selection_pbo_method", "unavailable"),
                "selection_pbo_candidates": metrics.get("selection_pbo_candidates", []),
                "is_oos_decay": wf_summary.get("is_oos_decay_per_fold", []),
                **evaluation_diagnostics,
            },
            "n_trades": len(backtest["trades"]),
            "n_unexecutable": backtest["n_unexecutable"],
            "input_snapshot_path": "input_snapshot.parquet",
            "portfolio_returns_path": "portfolio_returns.parquet",
            "trades_path": "trades.json",
            "corporate_actions_path": "corporate_actions.json",
            "cscv_returns_path": "cscv_returns.parquet",
            "cscv_analysis_path": "cscv_analysis.json",
            "pbo_analysis": {key: value for key, value in cscv_analysis.items() if key != "result"},
            "run_envelope": {
                "schemaVersion": "1.0",
                "runId": self.run_id,
                "strategyId": "aipha-ml-ranking",
                "strategyVersion": "1.0.0",
                "engine": "aipha",
                "dataAsOf": max(oos["trade_date"]).isoformat(),
                "signalFormat": "target_position",
                "signals": [],
                "targetPositions": [position.to_wire() for position in targets],
            },
        }
        report = json_safe_metrics(report)
        (run_root / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str, allow_nan=False), encoding="utf-8",
        )
        self.export_manifest(
            run_root,
            train_start=date.fromisoformat(training_period["start"]),
            train_end=date.fromisoformat(training_period["end"]),
            oos_start=min(oos["trade_date"]),
            oos_end=max(oos["trade_date"]),
            data_snapshot_id=data_snapshot_id,
            metrics=metrics,
            artifact_attestation={
                "input_snapshot": input_metadata,
                "oos_predictions": oos_artifact_metadata,
                "cscv_returns": cscv_metadata,
            },
            evaluation_window=backtest["evaluation_window"],
            training_period=training_period,
        )
        return {"run_id": self.run_id, "artifact_dir": str(run_root), **report}

    def export_manifest(
        self,
        output_dir: str | Path,
        *,
        train_start: date,
        train_end: date,
        oos_start: date,
        oos_end: date,
        data_snapshot_id: str | None = None,
        metrics: dict[str, float] | None = None,
        artifact_attestation: dict[str, Any] | None = None,
        evaluation_window: dict[str, Any] | None = None,
        training_period: dict[str, Any] | None = None,
    ) -> Path:
        """Write an immutable run manifest next to model/report artifacts."""
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        manifest = {
            "run_id": self.run_id,
            "data_provider": self.config.data_provider,
            "model_type": self.config.model_type,
            "data_snapshot_id": data_snapshot_id or f"{self.config.data_provider}:{train_start}:{oos_end}",
            "feature_version": self.config.feature_version,
            "model_config_hash": self.config.model_config_hash(),
            "train_start": train_start.isoformat(),
            "train_end": train_end.isoformat(),
            "oos_start": oos_start.isoformat(),
            "oos_end": oos_end.isoformat(),
            "metrics": metrics or {},
            "config": asdict(self.config),
            "model_artifact": "model/",
            "predictions": "oos_predictions.parquet",
            "portfolio_returns": "portfolio_returns.parquet",
            "targets": "target_positions.json",
            "execution_plans": "execution_plans/",
            "cscv_returns": "cscv_returns.parquet",
            "cscv_analysis": "cscv_analysis.json",
            "artifact_attestation": artifact_attestation or {},
            "evaluation_window": evaluation_window or {},
            "training_period": training_period or {},
        }
        path = root / "run_manifest.json"
        path.write_text(json.dumps(json_safe_metrics(manifest), ensure_ascii=False, indent=2, default=str, allow_nan=False), encoding="utf-8")
        return path


def _evaluate_predictions(
    predictions: pd.DataFrame,
    targets: list[TargetPosition],
    config: ResearchConfig,
) -> dict[str, Any]:
    """Compute OOS cross-sectional metrics and an explicit turnover-cost estimate."""
    from scipy.stats import spearmanr

    daily_ic: list[float] = []
    top_returns: list[float] = []
    bottom_returns: list[float] = []
    quintile_returns: dict[int, list[float]] = {index: [] for index in range(1, 6)}
    top_k_grid = _top_k_candidate_grid(config.top_k)
    for _, group in predictions.groupby("trade_date", sort=True):
        valid = group.dropna(subset=["score", "fwd_return"])
        if len(valid) < 3:
            continue
        rho = spearmanr(valid["score"], valid["fwd_return"]).statistic
        if np.isfinite(rho):
            daily_ic.append(float(rho))
        ranked = valid.sort_values("score", ascending=False)
        top_returns.append(float(ranked.head(config.top_k)["fwd_return"].mean()))
        bottom_returns.append(float(ranked.tail(config.top_k)["fwd_return"].mean()))
        ranked = ranked.copy()
        ranked["quintile"] = pd.qcut(
            ranked["score"].rank(method="first", ascending=False),
            q=5, labels=False, duplicates="drop",
        )
        for bucket in range(5):
            values = ranked.loc[ranked["quintile"] == bucket, "fwd_return"]
            if not values.empty:
                quintile_returns[5 - bucket].append(float(values.mean()))
    target_frame = pd.DataFrame([asdict(p) for p in targets])
    turnover_cost = 0.0
    if not target_frame.empty:
        # Estimate portfolio turnover between monthly target snapshots. The
        # cost model applies commission, transfer fee and slippage to both
        # sides, with stamp tax on sells; final exchange behavior remains in
        # the event-driven backtest/execution layer.
        weight_maps = [
            dict(zip(g["symbol"], g["target_weight"]))
            for _, g in target_frame.groupby("rebalance_date", sort=True)
        ]
        previous: dict[str, float] = {}
        turnover_costs: list[float] = []
        for current in weight_maps:
            symbols = set(previous) | set(current)
            gross = 0.0
            for symbol in symbols:
                delta = current.get(symbol, 0.0) - previous.get(symbol, 0.0)
                rate = config.commission_rate + config.transfer_fee_rate + config.slippage_bps / 10_000
                if delta < 0:
                    rate += config.stamp_tax_rate
                gross += abs(delta) * rate
            turnover_costs.append(gross)
            previous = current
        turnover_cost = float(np.mean(turnover_costs)) if turnover_costs else 0.0
    top_mean = float(np.mean(top_returns)) if top_returns else 0.0
    bottom_mean = float(np.mean(bottom_returns)) if bottom_returns else 0.0
    ic_mean = float(np.mean(daily_ic)) if daily_ic else 0.0
    ic_std = float(np.std(daily_ic, ddof=1)) if len(daily_ic) > 1 else 0.0
    return {
        "rank_ic": ic_mean,
        "icir": ic_mean / ic_std if ic_std > 0 else 0.0,
        "top_k_mean_forward_return": top_mean,
        "bottom_k_mean_forward_return": bottom_mean,
        "top_bottom_spread": top_mean - bottom_mean,
        "quintile_1_mean_forward_return": float(np.mean(quintile_returns[1])) if quintile_returns[1] else 0.0,
        "quintile_5_mean_forward_return": float(np.mean(quintile_returns[5])) if quintile_returns[5] else 0.0,
        "estimated_turnover_cost": turnover_cost,
        "top_k_mean_forward_return_net_estimated_cost": top_mean - turnover_cost,
        # Overlapping forward labels are useful descriptive diagnostics, but
        # cannot substitute for a matrix of realized daily portfolio returns.
        "pbo": None,
        "pbo_method": "unavailable_requires_portfolio_replay",
        "selection_pbo": None,
        "selection_pbo_method": "unavailable_requires_portfolio_replay",
        "selection_pbo_candidates": list(top_k_grid),
    }


def _top_k_candidate_grid(top_k: int) -> tuple[int, ...]:
    """Return the fixed Top-K candidates used by the selection PBO test."""
    base = max(1, int(top_k))
    candidates = {max(5, base // 2), base, base * 2, base * 4}
    return tuple(sorted(candidates))


def _build_evaluation_diagnostics(
    source: pd.DataFrame,
    predictions: pd.DataFrame,
    targets: list[TargetPosition],
    config: ResearchConfig,
    *,
    portfolio_returns: list[dict[str, Any]] | pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Build benchmark, long-short, and point-in-time exposure diagnostics."""
    diagnostics: dict[str, Any] = {
        "benchmark": {"available": False},
        "long_short": {"available": False},
        "exposures": {"available": False},
        "style_exposures": {"available": False},
    }
    diagnostics["benchmark"] = benchmark_diagnostics(source, predictions, portfolio_returns)

    long_short: list[float] = []
    long_short_net: list[float] = []
    long_short_costs: list[float] = []
    long_weights: dict[str, float] = {}
    short_weights: dict[str, float] = {}
    target_dates = sorted({pd.Timestamp(target.rebalance_date).normalize() for target in targets})
    prediction_dates = pd.to_datetime(predictions["trade_date"], errors="coerce").dt.normalize()
    for signal_date in target_dates:
        group = predictions.loc[prediction_dates == signal_date]
        valid = group.dropna(subset=["score", "fwd_return"])
        if len(valid) < 2:
            continue
        ranked = valid.sort_values("score", ascending=False)
        top_rows = ranked.head(config.top_k)
        bottom_rows = ranked.tail(config.top_k)
        top = top_rows["fwd_return"].mean()
        bottom = bottom_rows["fwd_return"].mean()
        if pd.notna(top) and pd.notna(bottom):
            gross_spread = float(top - bottom)
            long_symbols = top_rows["symbol"].astype(str).tolist()
            short_symbols = bottom_rows["symbol"].astype(str).tolist()
            new_long = {symbol: 1.0 / len(long_symbols) for symbol in long_symbols}
            new_short = {symbol: 1.0 / len(short_symbols) for symbol in short_symbols}
            cost = 0.0
            # Each leg is normalized to 100% notional, so this is a 2x gross
            # long-short diagnostic. Charge entry/exit turnover on both legs;
            # the short leg is a statistical comparator, not an A-share
            # shortability claim.
            for old, new, short_leg in (
                (long_weights, new_long, False), (short_weights, new_short, True),
            ):
                for symbol in old.keys() | new.keys():
                    delta = new.get(symbol, 0.0) - old.get(symbol, 0.0)
                    if not delta:
                        continue
                    rate = config.commission_rate + config.transfer_fee_rate + config.slippage_bps / 10_000
                    is_sale = (delta > 0) if short_leg else (delta < 0)
                    if is_sale:
                        rate += config.stamp_tax_rate
                    cost += abs(delta) * rate
            long_short.append(gross_spread)
            long_short_costs.append(cost)
            long_short_net.append(gross_spread - cost)
            long_weights, short_weights = new_long, new_short
    if long_short:
        ls = pd.Series(long_short, dtype=float)
        net = pd.Series(long_short_net, dtype=float)
        ls_nav = (1.0 + net).cumprod()
        diagnostics["long_short"] = {
            "available": True,
            "periods": int(len(ls)),
            "rebalance_frequency": "monthly",
            "gross_exposure": 2.0,
            "mean_gross_return": float(ls.mean()),
            "mean_net_return": float(net.mean()),
            "annualized_return": float((1.0 + net.mean()) ** 12 - 1.0),
            "volatility": compute_annualized_volatility(
                net.to_numpy(), trading_days=12,
            ),
            "sharpe": float(
                net.mean() / net.std(ddof=1) * np.sqrt(12)
                if len(net) > 1 and net.std(ddof=1) > 0 else 0.0
            ),
            "max_drawdown": float((ls_nav / ls_nav.cummax() - 1.0).min()),
            "estimated_cost": float(sum(long_short_costs)),
            "mean_period_cost": float(np.mean(long_short_costs)),
            "positive_period_rate": float((net > 0).mean()),
        }

    target_frame = pd.DataFrame([asdict(item) for item in targets])
    style_columns = [
        column for column in (
            "log_market_cap", "factor_pe_ttm", "factor_pb", "momentum_20",
            "volatility_20", "average_turnover_value_20",
        ) if column in source.columns
    ]
    if not target_frame.empty and style_columns:
        source_dates = pd.to_datetime(source["trade_date"])
        style_rows: list[dict[str, Any]] = []
        for _, group in target_frame.groupby("rebalance_date", sort=True):
            signal_date = pd.Timestamp(group["rebalance_date"].iloc[0])
            visible = source.loc[(source_dates <= signal_date) & source["symbol"].isin(group["symbol"])]
            latest = visible.sort_values("trade_date").drop_duplicates("symbol", keep="last")
            weights = group.set_index("symbol")["target_weight"]
            total_weight = float(weights.sum())
            if latest.empty or total_weight <= 0:
                continue
            exposure: dict[str, float] = {}
            for column in style_columns:
                values = pd.to_numeric(latest.set_index("symbol")[column], errors="coerce")
                aligned = values.reindex(weights.index).dropna()
                if not aligned.empty:
                    exposure[column] = float((aligned * weights.reindex(aligned.index)).sum() / total_weight)
            if exposure:
                style_rows.append({"rebalance_date": signal_date.date().isoformat(), "weighted_means": exposure})
        if style_rows:
            diagnostics["style_exposures"] = {
                "available": True,
                "columns": style_columns,
                "by_rebalance": style_rows,
            }
    industry_col = next((c for c in ("industry", "sector", "sw_industry") if c in source.columns), None)
    if not target_frame.empty and industry_col:
        source_dates = pd.to_datetime(source["trade_date"])
        rows: list[dict[str, Any]] = []
        for _, group in target_frame.groupby("rebalance_date", sort=True):
            signal_date = pd.Timestamp(group["rebalance_date"].iloc[0])
            visible = source.loc[(source_dates <= signal_date) & source["symbol"].isin(group["symbol"])]
            latest = visible.sort_values("trade_date").drop_duplicates("symbol", keep="last")
            weights = group.set_index("symbol")["target_weight"]
            exposure: dict[str, float] = {}
            for row in latest.itertuples(index=False):
                industry = str(getattr(row, industry_col, ""))
                if not industry:
                    continue
                exposure[industry] = exposure.get(industry, 0.0) + float(weights.get(row.symbol, 0.0))
            if exposure:
                rows.append({"rebalance_date": signal_date.date().isoformat(), "weights": exposure})
        if rows:
            diagnostics["exposures"] = {
                "available": True,
                "industry_column": industry_col,
                "by_rebalance": rows,
            }
    return diagnostics


def _load_snapshot_metadata(frame: pd.DataFrame) -> dict[str, Any]:
    metadata = frame.attrs.get("snapshot_metadata")
    if not isinstance(metadata, dict):
        raise ValueError("frozen snapshot metadata is required (Parquet sidecar <file>.metadata.json)")
    required = {"provider", "data_snapshot_id", "feature_version", "start_date", "end_date"}
    missing = required - set(metadata)
    if missing:
        raise ValueError(f"snapshot metadata missing fields: {', '.join(sorted(missing))}")
    normalized: dict[str, Any] = dict(metadata)
    for key in required:
        normalized[key] = str(metadata[key]).strip()
    if any(not normalized[key] for key in required):
        raise ValueError("snapshot metadata values must be non-empty")
    if normalized["provider"] == "tushare_pit":
        from .snapshots import verify_snapshot_signature

        verify_snapshot_signature(metadata, required=True)
        # A provider label is not provenance. Direct callers of ResearchPipeline
        # must carry the same logical-content attestation that Parquet callers
        # receive from verify_attested_parquet(). This catches a changed frame
        # even when it was supplied in memory and therefore had no file hash.
        content_hash = str(metadata.get("snapshot_content_sha256", "")).strip()
        if not content_hash:
            raise ValueError("tushare_pit snapshot metadata requires snapshot_content_sha256")
        from .snapshots import frame_content_sha256

        if frame_content_sha256(frame) != content_hash:
            raise ValueError("snapshot_content_sha256 does not match the input panel")
        from .snapshots import SNAPSHOT_GENERATOR_VERSION, SNAPSHOT_SCHEMA_VERSION

        if metadata.get("generator_version") != SNAPSHOT_GENERATOR_VERSION:
            raise ValueError("tushare_pit snapshot was not produced by the canonical snapshot builder")
        if metadata.get("snapshot_schema_version") != SNAPSHOT_SCHEMA_VERSION:
            raise ValueError("unsupported TuShare PIT snapshot schema version")
        if "feature_version" not in frame:
            raise ValueError("TuShare PIT panel must carry a row-level feature_version")
        versions = frame["feature_version"].dropna().astype(str).unique()
        if len(versions) != 1 or versions[0] != normalized["feature_version"]:
            raise ValueError("TuShare PIT row feature_version does not match snapshot metadata")
        adjustment = frame.get("price_adjustment")
        if adjustment is None or not adjustment.astype(str).eq("point_in_time_factor").all():
            raise ValueError("TuShare PIT panel must use same-day point-in-time adjustment factors")
    return normalized


def _pbo_proxy(wf_summary: dict[str, Any]) -> float:
    """Conservative fold-level overfit proxy; not a CSCV PBO estimate."""
    folds = wf_summary.get("folds", []) if isinstance(wf_summary, dict) else []
    comparable = [
        row for row in folds
        if row.get("is_mean_ic") is not None and row.get("mean_ic") is not None
    ]
    if not comparable:
        return 0.0
    failures = sum(float(row["is_mean_ic"]) > 0 and float(row["mean_ic"]) <= 0 for row in comparable)
    return float(failures / len(comparable))


def _filter_historical_universe(frame: pd.DataFrame, *, min_listing_days: int = 252) -> pd.DataFrame:
    from aipha.universe.historical import filter_historical_universe

    return filter_historical_universe(frame, min_listing_days=min_listing_days)


def _write_model_manifest(
    model_dir: Path,
    *,
    model_type: str,
    feature_version: str,
    feature_cols: list[str],
    model_config_hash: str,
) -> Path:
    """Write the schema guard consumed before a saved model is loaded."""
    manifest = {
        "schema_version": 1,
        "model_type": model_type,
        "feature_version": feature_version,
        "feature_columns": list(feature_cols),
        "model_config_hash": model_config_hash,
    }
    path = model_dir / "model_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
