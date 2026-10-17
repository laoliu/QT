# Canonical A-share research pipeline

`ResearchPipeline` is the single low-frequency research entry point. It accepts
a frozen market/feature panel, applies the row-wise historical universe filter,
creates forward labels, trains the configured walk-forward model, replays the
monthly portfolio at next-session open, and writes an immutable run folder.

The Python bridge accepts `train`, `predict`, `evaluate`, `export`, and
`list_models`. Historical Parquet inputs require a sibling metadata file named
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
`trades.json`, and `target_positions.json`. The portfolio replay models board
lots, cash, configurable fees/slippage, daily turnover, T+1, suspension, and
limit-up/limit-down execution blocks. It is a research/paper accounting engine;
it does not connect to a broker or submit orders.
