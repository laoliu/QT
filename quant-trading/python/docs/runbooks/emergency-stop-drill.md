# Emergency Stop Drill

This runbook verifies the cross-process emergency stop without placing a new
order. It requires a paper account or an explicitly approved live-account
window and an operator who can inspect the broker client.

## Preconditions

- Start the QMT or Easytrader gateway with the intended account connected.
- Confirm the gateway stop worker is online:

  ```text
  GET /api/v1/live/engine/status
  ```

- Confirm `kill_switch_active=false` before the drill. A stale switch must be
  cleared only through the normal manual recovery procedure; never delete it
  while an account may still have open orders.
- Submit one harmless, operator-approved paper limit order that remains open.
  Do not use a market order and do not use an unapproved live order.

## Drill

Send an authenticated request with a non-empty reason:

```text
POST /api/v1/live/engine/stop
Content-Type: application/json

{"reason":"approved emergency-stop drill","wait_seconds":5}
```

Expected successful response:

- HTTP `200`
- `data.stop_state == "confirmed"`
- `data.broker_cancellation_confirmed == true`
- Every worker in `data.workers` has the requested `request_id` and no
  `pending_orders`.

Expected fail-closed response when any broker worker cannot confirm:

- HTTP `503`
- `data.kill_switch_active == true`
- `data.stop_state == "unconfirmed"`
- The response identifies the worker error or remaining orders.

HTTP `503` is not a successful drill. Keep the kill switch active, inspect the
broker client and worker status, and cancel/verify the remaining orders using
the broker's own UI or approved operations procedure.

## Broker-side verification

1. Refresh the account's open-order list in the broker client.
2. Verify the drill order and every other open order are absent or explicitly
   canceled. A local gateway order state is not sufficient evidence.
3. Query the API status again and retain the response with the broker-side
   screenshot or audit record.
4. Verify a new submission is rejected while the kill-switch file remains
   active.

## Recovery

Recovery is manual and requires risk approval. Do not remove the kill-switch
file merely because the API returned `200`; first confirm the broker has no
unresolved orders and the account owner approves resumption. After recovery,
run the normal connection and reconciliation checks before enabling trading.

## Evidence to retain

- Timestamp, operator and reason.
- Status response before and after the stop.
- Stop response, including all worker records.
- Broker-side open-order verification.
- Any error, remaining order ID or manual cancellation record.

The test suite uses fake broker workers only. Passing those tests verifies the
fail-closed protocol, not connectivity to a real brokerage account.
