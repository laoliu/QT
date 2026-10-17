# Canonical A-share research pipeline

`ResearchPipeline` is the single low-frequency research entry point. It accepts
a frozen market/feature panel, applies the row-wise historical universe filter,
creates forward labels, trains the configured walk-forward model, replays the
monthly portfolio at next-session open, and writes an immutable run folder.
For live TuShare research, call `ResearchPipeline.run_tushare(...)`; it builds
the snapshot from the equity/PIT adapters, historical name-change ST status,
same-day adjustment factors, limit prices, and (when available) the benchmark
index bars before training.

The Python bridge accepts `train`, `train_tushare`, `predict`, `evaluate`,
`export`, and `list_models`. `train_tushare` materializes a frozen PIT
snapshot through the configured TuShare adapters before training. Historical
Parquet inputs require a sibling metadata file named
`<stem>.metadata.json` (for example `daily.metadata.json`) with:

```json
{
  "provider": "tushare_pit",
  "data_snapshot_id": "tushare:20240101-20251231:sha256-...",
  "feature_version": "cn_equity_v1",
  "start_date": "2024-01-02",
  "end_date": "2025-12-31"
}
```

The panel must include daily OHLCV, `is_index_member`, `is_st`,
`is_suspended`, and `listing_days`; membership and status fields must be
point-in-time values.
Financial features must already have been joined by announcement date. The
`visible_financial_snapshots` helper provides the same announce-date cutoff for
long-form financial snapshots. A sidecar with another provider, mismatched
feature version, or mismatched date range is rejected.

Runs write `run_manifest.json`, `report.json`, a version-checked model folder,
`oos_predictions.parquet` and its metadata sidecar, `portfolio_returns.parquet`,
`trades.json`, `target_positions.json`, and one validated paper execution plan
per rebalance date. Reports include the walk-forward PBO proxy plus formal
CSCV PBO when enough OOS quintile observations exist, long-short diagnostics,
benchmark comparison, and causal style exposures when the snapshot contains
the required columns. The portfolio replay models board lots, cash,
configurable fees/slippage, daily turnover, T+1, suspension, and
limit-up/limit-down execution blocks. It is a research/paper accounting engine;
it does not connect to a broker or submit orders.

Legacy model folders without `model_manifest.json` are rejected. Use
`migrate_legacy_model` only with an explicit feature version, column order,
snapshot identifier, and operator attestation. QMT consumption is available
through the versioned execution-plan adapter and requires an explicit live
gate; direct research artifacts are never passed to a broker.
