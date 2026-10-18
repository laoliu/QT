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
tape. Its signed metadata records the calendar range, content hash and explicit
`listing_calendar.open_sessions`. The v4 builder checks that market dates
cover every open session in the **requested** range, including its edges.
Canonical training, prediction and runtime Paper readers check the same
signed session list; whole-market missing days fail before labeling or fitting.
Individual missing symbol bars still invalidate their own labels/orders.
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

`features.groups` selects the actual model inputs through the versioned
`cn_equity_feature_groups/2` catalog. Only registered factors in enabled groups
enter training, in stable column order. Raw financial fields, unknown numeric
columns, prices, status flags and labels are excluded by default. TuShare
snapshot construction computes the existing Alpha158 implementation from PIT
OHLCV for the technical group. Register additional causal columns explicitly:

```yaml
features:
  version: cn_equity_v1
  groups: [quality, momentum]
  column_groups:
    my_quality_alpha: quality
```

Custom mappings cannot reclassify standard factors or register reserved
price/status/label columns. A configuration with no available registered
columns fails. Partially unavailable groups are disclosed under
`walk_forward.feature_selection.unavailable_groups`; per-column missing rates
remain in the report, including entirely null registered columns.

Model manifest schema v2 binds the training universe, listing-session
threshold, enabled groups, custom column mappings, feature policy version,
ordered feature columns, model/config/snapshot identifiers, and hashes of
`meta.pkl` and every `fold_*` artifact. Prediction and canonical Paper check
their policy against this manifest before deserialization. Missing input
columns fail rather than silently shrinking the feature set. File hashes
detect changed or exchanged bytes; they are not a signature proving model
origin. OOS and bridge rankings include model ID, feature version and a
deterministic date-wise rank (ties sorted by symbol).

Financial features must already have been joined by announcement date. The
`visible_financial_snapshots` helper provides the same announce-date cutoff for
long-form financial snapshots. A sidecar with another provider, mismatched
feature version, or mismatched date range is rejected.

The v4 snapshot generator retains the v3 conservative availability date
`max(ann_date, f_ann_date)` for statements when an actual publication date is
present; a null actual date retains the declared `ann_date`. Invalid actual
dates fail instead of being treated as earlier availability. Statement inputs
are consolidated cumulative filings (`report_type=1`) and their consolidated
restatements (`4`); parent-only and single-quarter forms are excluded.
PIT queries choose the latest visible report period, then its latest visible
revision. A late correction of an older period remains in the history but
does not replace a newer report's current value. The long-form feature join
applies this rule independently per metric, without changing market row
identity or exposing financial join keys as model features. Conflicting
values with the same report/announcement identity fail rather than depend
on API row order.

Financial provider caches use separate `*_pit_v2_*_<start>_<end>.parquet`
files. They retain all distinct source rows and isolate requested date
ranges, preserving old caches unchanged. Statement API responses must include
`f_ann_date` even when its values are null. TuShare indicator histories expose
`ann_date`; changed values sharing the same report/publication identity are
retained and rejected as ambiguous. Missing historical revisions cannot be
reconstructed or assigned a new availability date from download time. These
checks do not establish that TuShare supplied every historical version.
Snapshots from generator v1/v2/v3 must be rebuilt; their files are not deleted.
Adding an inferred weekday list or relabeling an old sidecar does not prove
the original market tape covered the exchange calendar.
The feature policy is also advanced to v2 so a model fitted under the older
financial timing policy cannot silently score the corrected features.
Models trained with those earlier timing errors require retraining; attaching
new metadata alone cannot repair the values used during their training.

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
market dates, verified against signed source open sessions on canonical
inputs), with entry at D+skip_days open and exit at
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

The bridge's `execute_paper` response is a durable **plan-acceptance receipt**
from a short-lived gateway. It does not advance a continuous daily cash,
position or fill ledger. For canonical daily Paper accounting, use:

```powershell
python scripts/ops/v2_ml_paper_daily.py --research-config aipha/configs/research/a_stock_ml.yaml --panel snapshot.parquet --plan-path plan.json --book-path data/research/paper/v2_ml.sqlite3 --json
python scripts/ops/v2_ml_paper_daily.py --research-config aipha/configs/research/a_stock_ml.yaml --panel snapshot.parquet --plan-path plan.json --book-path data/research/paper/v2_ml.sqlite3 --execute --json
python scripts/ops/v2_ml_paper_daily.py --research-config aipha/configs/research/a_stock_ml.yaml --panel next_snapshot.parquet --book-path data/research/paper/v2_ml.sqlite3 --execute --json
python scripts/ops/v2_ml_paper_daily.py --book-path data/research/paper/v2_ml.sqlite3 --replay --json
```

The first command previews on an in-memory database copy and creates no book.
Only `--execute` commits an observed close. A new book opens in cash at that
close; it does not invent historical positions from the training tape.
An optional same-close paper plan is queued for the next exchange session's
open. Each month accepts at most one plan. Calls without a plan still execute
an earlier pending plan and mark existing positions. Trading blocks consume
that session's attempt; fills are not silently deferred to a later price.
The full signed market tape must retain ST, suspended and departed constituents
for valuation and exits; the signal universe only gates new positive targets.

SQLite transactions protect cash/position history, accepted plans and receipts
together. The book freezes the research config and past market observations,
requires snapshots to advance and overlap the previous close, rejects changed
corporate event terms or events arriving after a committed record close, and
returns the prior receipt for an identical same-day request. Conflicting
same-day requests fail. A whole-market missing session cannot shift a queued
fill to a later day's open: the new snapshot and the full accumulated book
must match its signed exchange session list. Rolling snapshots must retain
calendar coverage through the book's opening date; historical calendar
revisions that insert/remove committed sessions require reconciliation.

Daily accounting replays the accumulated tape and target plans with the same
portfolio engine used in research, including raw-price lot sizing, turnover,
trading restrictions, costs and supported corporate events. Receipts include
NAV, cash, final holdings, new fills, cost/block diagnostics and a replay hash;
fills carry plan/run/model/signal provenance. Read-only `--replay` recomputes
the ledger, verifies its latest receipt and checks the saved source-signed
calendar. It requires the deployment's snapshot-verification key. This replay trades storage and
CPU for one accounting implementation; it is intended for low-frequency
books. Offline continuity fixtures do not establish real-market daily
operation or account reconciliation.

Books created before the signed-session requirement remain untouched. To
inspect one under its old accounting assumptions, use `--replay
--allow-unverified-calendar`; the response marks calendar verification
unavailable. This flag permits no append or trading. To upgrade a book, first
rebuild a canonical snapshot covering its complete frozen history and ending
at its last committed close, then preview and explicitly publish a new copy:

```powershell
python scripts/migrate_paper_calendar.py old.sqlite3 new.sqlite3 --snapshot rebuilt.parquet
python scripts/migrate_paper_calendar.py old.sqlite3 new.sqlite3 --snapshot rebuilt.parquet --execute
```

The upgrade verifies the old replay hash, market identities/prices, corporate
events and every open session. It changes only calendar/source evidence in
the copied latest receipt; the old file and existing destination files are
never overwritten. A book that skipped a session or used different prices
fails upgrade and needs explicit accounting reconciliation. Subsequent daily
calls must select the new `--book-path`; no scheduler is redirected implicitly.

Canonical receipts record `committed_at` in UTC only after a commit is
requested; new-close previews contain no committed timestamp and identical retries
retain the original one. `read_paper_book_evidence` reads the verified replay
and receipts from one SQLite read transaction, checking each receipt's saved
NAV, cash and date against the replay. Monitoring never rewrites the book.

The existing ops commands now include `data/research/paper/*.sqlite3` by
default, or accept `--book-dir` for a separate directory or a single book:

```powershell
python scripts/ops/state_stale_checker.py --book-dir data/research/paper --json
python scripts/ops/nav_monitor.py --book-dir data/research/paper --json
python scripts/ops/s4_validator.py --book-dir data/research/paper --required 60 --json
python scripts/ops/daily_ops.py --task health --book-dir data/research/paper --json
```

Canonical entries use `research/<book stem>` IDs to distinguish them from
legacy JSON portfolios. NAV checks compare replayed close values; S4 counts
only persisted receipts committed on their signal date after 15:00 Shanghai
time, using exchange sessions for continuity. A skipped daily commit resets
the streak even when a later snapshot fills in that day's market bars.
Historical backfills, before-close/future timestamps and old receipts without
timestamps do not count. A copied/touched database does not refresh the
last committed timestamp. Corrupt books and missing verification keys report
failures rather than disappearing from the check. Freshness uses elapsed
hours (`--threshold-hours`, default 24); explicitly extend the threshold for
long exchange closures. A stale book cannot pass canonical S4.

These are local accounting and commit-time observations. They do not attest
independent wall-clock authenticity, market-data delivery, broker fills or
real-market continuous operation. Replaying historical snapshots in one
session cannot supply 60 daily operational observations. Calendar upgrades
preserve original timestamps and never invent missing ones.

Old `data/paper_state/v2_ml/state.json` files are preserved separately. Only
`--legacy-compat --model-dir ...` uses the historical model-scoring/close-price
accounting and its `nav_history.jsonl` monitoring outputs. The canonical book
does not silently import those positions or overwrite the legacy monitor feed;
the ops readers inspect its SQLite evidence directly.

Legacy model folders without `model_manifest.json`, and schema v1 manifests,
are rejected. Retrain with the canonical pipeline or explicitly migrate a
trusted legacy model. Migration requires the operator to supply the feature
version/order, training universe, listing threshold, groups/custom mappings,
model/config/snapshot identifiers and attest those declarations. It validates
the copied selector before publishing a new directory; it neither overwrites
the destination nor modifies the source. It cannot establish PIT provenance
for historical training data by itself. For example:

```powershell
python scripts/migrate_legacy_model.py OLD NEW --feature-version cn_equity_v1 --feature-columns momentum_20 volatility_20 --model-type lightgbm --data-snapshot-id VERIFIED_SNAPSHOT --universe CSI500 --min-listing-days 252 --feature-groups momentum volatility --model-id ATTESTED_MODEL --model-config-hash VERIFIED_CONFIG_HASH --acknowledge-legacy
```

QMT consumption is available
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
unchanged, emits warnings for dropped legacy fields, validates the generated
TuShare-PIT/paper configuration before publication, and refuses an existing
destination (including the source path). Unsupported policies fail without
publishing a partial configuration.

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
Each ML component must declare its own snapshot, universe and model paths;
one component cannot supply another's provenance or compatibility flag.
Canonical status requires an actual signed current-version snapshot, its
exchange calendar, compatible model schema/policy, artifact hashes, and
numeric feature columns. The audit does not unpickle models or run inference:
`verification_level=contracts_and_hashes` and `inference_exercised=false`
describe this boundary. Inference and fitted fold checks still run at model
load time. Snapshot templates require `--trade-date YYYY-MM-DD`; fixed paths
default to their signed end date. Missing signing keys fail verification.
Adding `--strict` makes CI fail for legacy, mixed, unmigrated or malformed
configs, and for missing/empty directories. An explicit legacy marker is
inventory evidence only and cannot pass this migration gate.

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
Canonical scorers also accept `feature_groups` and `feature_column_groups`,
using the same defaults as `ResearchConfig`. The daily Paper CLI accepts
`--research-config` to enforce the research universe, listing threshold,
feature version, portfolio and accounting policy against its target plan.
Scoring and Top-K selection take place in the canonical model/export path.
Omission uses `aipha/configs/research/a_stock_ml.yaml`. The canonical daily CLI
rejects model, Top-K and feature-version overrides; regenerate the source
model/plan through the research pipeline when changing those policies.
