# Live stop controls and research modules

## Emergency stop API

The Python API registers these endpoints under its existing JWT authentication:

- `GET /api/v1/live/engine/status`
- `POST /api/v1/live/engine/stop`, body: `{"reason": "operator halt"}`

Both require an `ops`, `risk`, `risk_approver`, or `admin` account. A successful
stop atomically publishes `aipha.utils.paths.KILL_SWITCH_PATH`, recording the
operator, reason and UTC activation time. The stop remains active after API or
engine restarts. This API does not expose resume, clear or engine-start commands.

The response distinguishes the persistent switch from runner notification:

- `kill_switch_active`: the shared file currently exists.
- `runner_attached`: an in-process runner was explicitly attached.
- `runner_notified`: that runner's `emergency_stop()` returned successfully.
- `broker_cancellation_confirmed`: true only when participating gateway workers
  acknowledge this request and fresh broker queries show no outstanding orders.
  Missing, stale, failed or unresolved workers prevent confirmation.
- `error`: a runner notification failure, if any; the file remains active.
- `stop_state`: `confirmed` only when `broker_cancellation_confirmed` is true;
  otherwise `unconfirmed`. HTTP success means the persistent kill switch was
  published, not that a broker has confirmed all cancellations.

The stop endpoint returns HTTP 200 only for `stop_state=confirmed`. If the
kill-switch file is safely published but no broker receipt arrives before the
wait window (including an explicit `wait_seconds=0`), it returns HTTP 503 with
the result under `data`; `kill_switch_active` remains true and the response
contains worker/error details for operator follow-up. A 503 therefore does not
mean the stop request was discarded: it means cancellation is not yet proven.

A runner hosted in the API process may be attached with
`get_engine_manager().attach(runner)`. Attaching while the switch is active first
invokes the runner's emergency stop. No broker connection is created by the API.
Separate engine processes must use the same project root / shared switch file;
QMT and non-dry-run Easytrader gateways run background stop listeners and publish
request-specific receipts through the shared filesystem. No market event is needed
to trigger cancellation. This is not a remote-host RPC service.

LiveRunner checks the switch at order submission, including urgent Tick orders.
LiveEngineEOD checks it before connecting and before each order in a batch.
Use `wait_seconds` (0 to 10) in the stop body to wait for receipts; the default
is 2 seconds, while explicitly passing 0 requests asynchronous publication.
Cancellation acceptance alone is not confirmation: a subsequent broker query
must show no open orders. Unresponsive or unregistered workers require operator
investigation.
An unwritable switch returns HTTP 503; a local attached runner is still asked to
stop. Clearing the file remains a manual operation after the cause is resolved.

## Research module recovery

- Tick domain models, strategy context and a separate strategy registry are
  implemented. TickBacktestEngine now uses the shared Fill-based fee and slippage
  APIs. Unsettled holdings remain visible but cannot be sold before settlement.
- Tick quote volume and amount are cumulative session counters. Feature windows
  and aggregation use differences and reset at session boundaries.
- Supported Tick fill modes are `bbo`, `immediate` and `next_tick`. Delayed orders
  require a strictly later quote for the same symbol; equal timestamps cannot
  fill them. Limit orders wait for an acceptable quote. Pending DAY orders expire
  at session end and are counted in `expired_orders`.
- `aipha.factors.register_all` delegates to the canonical builtin registry. It
  registers implemented factors rather than importing nonexistent classes.
- Auction and sector screeners reuse the repository's existing engine/provider
  implementations; they no longer prepend the wrong directory to `sys.path`.
- eToro additionally collects public monthly/yearly percentage return history
  and current aggregated positions. Simulated history is excluded and malformed
  numeric/provenance fields are rejected. Portfolio instrument IDs and original
  `Invested`, `NetProfit`, `Value` fields remain unconverted: these are not share
  quantities or independently verified portfolio weights. Evidence stores both
  API URLs and parsed holdings. Unavailable holdings remain `None`, distinct
  from a verified empty portfolio. Evidence stays PARTIAL; no CAGR or total return
  is invented from `maxReturn` or incomplete monthly history.
- Darwinex additionally collects the public DARWIN page's cumulative return,
  annualized return and displayed holding-period label after verifying that the
  rendered DARWIN identity matches the requested code. It does not infer maximum
  drawdown or holdings from the page. The remaining four library collectors
  still collect identified public profile metadata only. Their platform-specific
  performance data remains unfinished. A metadata refresh preserves existing
  performance data.
- ZuluTrade additionally collects public leader-search statistics, including
  ROI, annualized ROI, drawdown, followers and a timeframe Sharpe value. The
  requested numeric provider ID must be present in the response before any data
  is accepted; an unrelated top leader is never substituted. The endpoint does
  not expose verified holdings, so holdings remain absent.

Automated tests use temporary switch paths, mocked gateways and local fixtures;
they submit no live orders or external requests. A separate public eToro live
check on 2026-10-17 returned 154 monthly observations and 41 aggregated positions
for `jaynemesis`; a Darwinex live check returned the THA page's 834.21%
cumulative and 20.47% annualized return. These confirm current endpoint
compatibility for those accounts, not every platform or future availability.
The ZuluTrade public search endpoint also returned provider `430525` with
performance statistics; provider-specific detail requests returned 401, so the
adapter intentionally uses only the public search response and does not claim
private trade or holdings data.
JoinQuant redirected to login, BigQuant redirected to its home page, and the
Xueqiu NAV probe returned HTTP 400.

## Backtest database migration

The default index is `python/output/backtest_index.db`. API and backtests use
the same resolver: `AIPHA_DB_PATH`, then `AIPHA_OUTPUT_DIR/backtest_index.db`,
then the project default. Relative overrides are anchored to the Python project.
The old `quant-trading/output/backtest_index.db` is not selected by default. A
temporary fallback requires the explicit `AIPHA_ALLOW_LEGACY_DB=1` opt-in and
only applies while the new default file is absent.

`aipha.backtest.db_migration.migrate_database(source, destination)` copies a
consistent SQLite snapshot including committed WAL data, checks integrity and
schema/row fingerprints, and publishes without replacing an existing target.
The source is retained with write-fence triggers. Existing clients using the old
path must restart with the new path; stale writes fail explicitly. Explicit
environment overrides pointing to the old path must also be updated.

If publication fails after fencing, rerun the migration with the same arguments
while the destination is absent. The source stays readable. Never remove fences
or merge divergent database files to work around a migration failure.

The read-only `audit_databases(source, destination)` helper reports schema,
integrity, source-only/destination-only primary keys and same-key conflicts
before any recovery decision. On 2026-10-17, both project databases passed
`quick_check` and had matching schemas, but the new index had 30 backtest rows
versus 28 in the fenced legacy index, with 2 destination-only rows and 25
same-key conflicts. Replacing or merging the current index is therefore not
automated; stop API/backtest writers, review the conflicting run records, then
back up both databases before any operator-directed reconciliation.
