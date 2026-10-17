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
`export`, `execute_paper`, and `list_models`. `train_tushare` materializes a frozen PIT
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

TuShare sidecars written by the canonical snapshot builder also include the
generator version, logical content SHA-256 and Parquet artifact SHA-256. A
TuShare input with a missing or mismatched artifact hash is rejected by the
bridge; a provider string alone is not treated as proof of provenance.

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

Forward-return labels use the next eligible session's open as entry and the
close after the configured holding horizon as exit. The canonical signal is
formed after the close, so `label.skip_days` must be at least one; this keeps
the training target aligned with the backtest's next-open execution.

The canonical portfolio policy also applies `portfolio.max_industry_weight`
(default 20%) when the frozen panel contains an `industry` column. Selection
walks the ranked list and skips names that would exceed the cap; when the panel
has no industry field, the cap is not inferred and the report marks industry
exposure as unavailable. `PaperGateway.submit_target_plan` consumes the
generated paper plan, rejects live plans, applies the independent position and
total-weight risk gate, and carries plan/run/model/signal provenance into every
submitted order. The `execute_paper` bridge action additionally requires
explicit `confirm=true`; it never selects QMT.

The `export` action writes one execution-plan JSON file per rebalance date in
`<output stem>.execution-plans/`. Its `executionPlanPath` points to the latest
rebalance for Paper review and `executionPlanPaths` lists the full history.
Pass a single dated plan path to `execute_paper`; historical rebalances must
not be submitted together as if they were current orders.

Legacy model folders without `model_manifest.json` are rejected. Use
`migrate_legacy_model` only with an explicit feature version, column order,
snapshot identifier, and operator attestation. QMT consumption is available
through the versioned execution-plan adapter and requires an explicit live
gate. The generic HTTP and polling bridge `place_order` methods are disabled:
they accept a single raw order and cannot establish membership in a validated
target plan. Use the plan adapter, which validates the full plan and risk
limits before it translates targets into broker orders.
The local Node-to-Python single-order QMT bridge also rejects orders when the
profile requires plans, even if callers provide matching-looking provenance
strings; `run_id`, `model_id`, and `signal_id` alone do not prove plan membership.

The old `configs/sample_ml_factor.yaml` shape can be converted with
`python scripts/migrate_research_config.py`. The command keeps the source
unchanged, emits warnings for dropped legacy fields, and always writes a
TuShare-PIT/paper configuration.

The historical cache-backed trainer is opt-in: `python scripts/train_ml_factor.py`
and the CSI1000/CSI2000 rolling retrainers refuse to train unless passed
`--legacy-compat`. For new offline runs, use
`python scripts/research_retrain.py --data-path <attested-snapshot.parquet>`;
for online TuShare snapshot construction, use the `ml_ranking` bridge's
`train_tushare` action. The old `--check-only` rolling-retrainer mode remains
available for read-only drift and coverage checks.

Legacy SQLite history can be audited and migrated without replacing the old
database. Use `python scripts/migrate_sqlite.py --audit OLD.db NEW.db` for a
read-only comparison, `--conflicts` to export keyed row conflicts, or
`--migrate OLD.db NEW.db` to publish a verified copy. Migration retains the
old file and fences its future writes; an existing destination is never
overwritten.

The same canonical bridge is available over the authenticated Node HTTP API
at `POST /quant/api/research/ml-ranking` (and `GET` for `list_models`).
Mutating actions require an `Idempotency-Key`; `execute_paper` additionally
requires `confirm: true` and accepts only a `mode=paper` execution plan. Each
request appends an audit record under `<dataDir>/research/ml-ranking-audit.jsonl`.
The route rejects `mode=live` and `allowLive=true` before invoking Python, so
it cannot be used as a QMT order shortcut.

Runtime Paper scorers follow the same boundary. Configure
`feature_snapshot_path` (optionally with a `{trade_date}` placeholder) and
`feature_version` to consume an attested PIT snapshot. The old cache-backed
`train_ml_factor` scorer is disabled unless a historical Paper configuration
explicitly sets `legacy_ml_compat: true`; that flag is a migration marker and
does not make the legacy model provenance-safe.
