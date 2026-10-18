# Canonical A-share research pipeline

`ResearchPipeline` is the single low-frequency research entry point. It accepts
a frozen market/feature panel, applies the row-wise historical universe filter,
creates forward labels, trains the configured walk-forward model, replays the
monthly portfolio at next-session open, and writes an immutable run folder.
Labels are calculated on the complete frozen market tape, then rows are
filtered by signal-date universe eligibility. Rows that become ST, suspended,
or leave the index remain in the saved tape for fixed-horizon label prices
and portfolio valuation/exits. Historical industry classifications are joined
back onto OOS scores by date/symbol before portfolio constraints are applied.
For live TuShare research, call `ResearchPipeline.run_tushare(...)`; it builds
the snapshot from the equity/PIT adapters, historical name-change ST status,
same-day adjustment factors, dated Shenwan industry membership intervals,
limit prices, and (when available) the benchmark index bars before training.

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
`listing_days` counts exchange open sessions from the listing session through
the signal close, inclusive. It must carry `listing_days_basis=exchange_open_sessions`;
snapshots using natural days or an undeclared basis must be rebuilt. The v2
TuShare builder requests a complete SSE `trade_cal` history from the earliest
issuer listing through the snapshot end, retaining closed dates to detect
coverage gaps. It never estimates age from weekdays or the truncated market
tape. Its signed metadata records the calendar range and content hash.
`universe.min_listing_days` (integer, at least 252) applies to training,
feature-missingness reporting and prediction; canonical Paper scorers accept
the same `min_listing_days` parameter. Existing snapshots are preserved; the
old v1 builder's age semantics cannot be silently reused for canonical runs.

The research YAML controls the model and historical universe in every entry
point. Omitting bridge `modelType` retains `model.type`; only an explicit
`lgbm`/`linear` override changes it. `train_tushare` resolves `universe.base_index`
to the matching TuShare code (CSI300/500/1000/2000), with the same index as its
default benchmark. An explicit conflicting `indexCode` is rejected before
data access. Offline runs, prediction and canonical Paper scoring reject
snapshots with an absent or mismatched index identity. Prediction also checks
the source signature, generator, row feature version and complete date range
before loading a model.

Financial features must already have been joined by announcement date. The
`visible_financial_snapshots` helper provides the same announce-date cutoff for
long-form financial snapshots. A sidecar with another provider, mismatched
feature version, or mismatched date range is rejected.

TuShare PIT sidecars are HMAC authenticated with the deployment-held
`AIPHA_PIT_SNAPSHOT_KEY`; canonical ML and Paper readers fail closed when the
signature is missing or invalid. The key is never stored in the repository.

TuShare sidecars written by the canonical snapshot builder also include the
generator version, logical content SHA-256 and Parquet artifact SHA-256. A
TuShare input with a missing or mismatched artifact hash is rejected by the
bridge; a provider string alone is not treated as proof of provenance.

Runs write `run_manifest.json`, `report.json`, a version-checked model folder,
`oos_predictions.parquet` and its metadata sidecar, `portfolio_returns.parquet`,
`trades.json`, `corporate_actions.json`, `target_positions.json`, and one validated paper execution plan
per rebalance date. Reports include the walk-forward PBO proxy plus formal
CSCV PBO over a fixed Top-K grid when enough OOS observations exist.
`pbo` and the compatibility alias `selection_pbo` now refer to this same
portfolio-size selection diagnostic (`cscv_net_top_k_portfolios`). Quintile
forward-return means remain descriptive metrics; overlapping forward labels
are never used as the CSCV return observations. It also includes long-short diagnostics,
benchmark comparison, and causal style exposures when the snapshot contains
the required columns. The portfolio replay models board lots, cash,
configurable fees/slippage, daily turnover, T+1, suspension, and
limit-up/limit-down execution blocks. It is a research/paper accounting engine;
it does not connect to a broker or submit orders.

Performance observation dates start at the first OOS prediction, not at the
beginning of the training tape. The first NAV row anchors signal-date close;
only later close-to-close intervals enter annualization, volatility and
Sharpe. Cash waiting for a monthly rebalance remains part of OOS performance.
Holdings continue marking through the frozen tape's end; `evaluation_window`
separately records the signal OOS range, actual return interval count and
valuation sessions after the final prediction. Benchmark headline and
relative metrics use exactly those same return intervals. Missing benchmark
sessions mark the comparison unavailable rather than compressing the dates.

Reports measure feature missingness only over the model manifest's input
columns within the historical signal universe, with a rate per feature.
Non-finite feature values count as missing. Fold reports retain actual train
dates and purge boundaries; the manifest's aggregate training range is the
union of those training windows, with the latest fold identified separately.
Undefined statistical diagnostics are JSON `null`, never `NaN`/`Infinity`.

CSCV candidates use the same monthly targets, industry/position limits,
turnover, raw-price accounting, trading restrictions and costs as the main
replay. Their daily net returns are saved in `cscv_returns.parquet` with a
hash sidecar binding the parent OOS predictions. `cscv_analysis.json` records
the candidate grid, metric configuration, partitions, IS selections and OOS
ranks, so the statistic can be reproduced without training again. The
analyzer retains remainder observations and uses ascending OOS average ranks
divided by N+1; IS ties share selection probability equally. Insufficient
candidate coverage, short samples and identical candidate returns produce
an unavailable diagnostic. This measures Top-K selection on the frozen model
scores; it does not estimate unrecorded model/factor search. The standalone
prediction-only `evaluate` action requires a portfolio replay for formal PBO.

Forward-return labels use the panel's shared trading calendar (the union of
market dates), with entry at D+skip_days open and exit at
D+skip_days+horizon_days close. Missing or suspended entry/exit bars and
directionally blocked limit prices invalidate the label; they never move its
holding period to a later observation of that symbol. The canonical signal is
formed after the close, so `label.skip_days` must be at least one; this keeps
the training target aligned with the backtest's next-open execution.

When price bands are present, replay eligibility compares raw opening prices
against the day's unadjusted limits, independently of the later closing
limit flags. Legacy local panels without bands retain their supplied flags.
Buy sizing includes minimum commission and rechecks total fees before cash is
debited. Replay uses raw currency prices for board-lot sizing, fees and
valuation; model features and labels retain their point-in-time adjusted
series. Changing a constant adjustment anchor cannot change executed lots.

The TuShare builder freezes implemented dividend/bonus-share terms in
`corporate_actions_json`, covered by the snapshot content hash and signature.
Entitlement is determined by shares held at record-date close. At ex-date,
cash dividends and bonus shares become receivables, included in equity but
unavailable for trading until pay-date/share-list-date. An absent symbol bar
on ex-date does not discard the event. The report exposes receivables, actual
cash/share delivery, final positions and its **gross dividend** tax policy;
holding-period dividend withholding is not modeled. Fractional bonus-share
allocation, overlapping unlisted bonus entitlements, and rights issues require explicit additional terms and are not
inferred from adjustment factors. Missing actions or factor changes that do
not reconcile with declared events cause a held-position replay to fail.
This is not yet a complete live-account tax/reconciliation implementation.

The canonical TuShare snapshot maps Shenwan L1 membership using the historical
`in_date`/`out_date` interval; it does not reuse today's industry mapping for
past dates. The portfolio policy applies `portfolio.max_industry_weight`
(default 20%) when the frozen panel contains an `industry` column. Selection
walks the ranked list and skips names that would exceed the cap; when a local
fixture or external snapshot has no dated industry field, the cap is not
inferred and the report marks industry exposure as unavailable.
Execution plan schema v2 binds `plan_id` to the complete normalized plan,
including mode, weights, reasons and risk flags; older schema v1 plans are
rejected and must be re-exported from their source run. `PaperGateway.submit_target_plan`
consumes the generated paper plan, rejects live plans, applies the independent
position and total-weight risk gate, and carries plan/run/model/signal
provenance into every submitted order. The `execute_paper` bridge action additionally requires
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

The historical `ml_factor` and `ml_predict` strategy classes also require
`legacy_ml_compat: true` whenever a model path or precomputed OOS prediction
file is configured. Their feature paths are retained for reproducible old
backtests; the canonical `aipha-ml-ranking` strategy remains the default.

The shared `MLStockSelector` also rejects experimental model types by default.
Historical callers must set `allow_experimental_models=True` at construction
and again on `load(...)`; a flag persisted inside an old model cannot authorize
loading it into a new runtime. The canonical manifest loader accepts only
LightGBM/ElasticNet and checks the stored model type against its manifest.

Use `python scripts/audit_ml_migration.py --config-dir configs/paper` for a
read-only inventory of canonical, explicitly legacy, and non-ML Paper configs.
Adding `--strict` makes CI fail while any applicable ML config lacks a
canonical snapshot/model manifest or an explicit legacy marker.

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
