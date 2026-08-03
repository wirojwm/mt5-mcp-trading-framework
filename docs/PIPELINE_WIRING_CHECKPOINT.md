# Checkpoint: pipeline wiring (post-Phase 7)

Handoff doc for continuing this effort in a new session. Read `AGENTS.md` first for overall
project context. "Pipeline wiring" is not one of this project's numbered phases (0–7) — per
`AGENTS.md` and both the Phase 6/7 checkpoint docs, it has always been called out as a separate,
later, explicitly-approved effort: actually running `run_grid_cycle`/`run_runner_cycle` against
the real, order-submitting `McpOrderExecutor`, rather than `DryRunExecutor` or a hand-built
`OrderPlan` (every real `McpOrderExecutor` call before this used a hand-built plan — the three
`scripts/run_demo_execution_*_smoke_test.py` scripts).

## Goal

User chose "human-approved per cycle" as the run mode: one script invocation runs exactly one
cycle against the real executor, reports what happened, and requires a human to re-invoke it
for the next cycle — no internal scheduler/loop yet. Key departure from every prior smoke-test
script: a real cycle's result is the actual, intended strategy decision, meant to persist and be
managed by later cycles/reconciliation — no self-cleanup is built into the cycle script itself.

## Step 1 — first script, not yet run

`scripts/run_demo_execution_pipeline_cycle.py` written, mirroring
`scripts/run_live_dry_run_pipeline.py`'s exact config (`SYMBOL="BTCUSD"`, `TIMEFRAME="M1"`,
`BARS_COUNT=100`, `GRID_MAGIC=71101`, `RUNNER_MAGIC=72101`, default `GridStrategyConfig()`/
`RunnerStrategyConfig()`/`MoneyConfig()`, `ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06)`)
with only the executor swapped from `DryRunExecutor` to a real one via
`execution/composition.py`'s `demo_execution_session()`. A `STRATEGY` constant (`"GRID"`/
`"RUNNER"`) scopes each run to exactly one pipeline function, never both together. Committed
without being run live (`29b728f`).

## Step 2 — first live run

Run 2026-08-03, `STRATEGY="GRID"`, against the real demo account. **Both sides submitted and
verified:**

- Ticket `171621248`: BUY_LIMIT BTCUSD 0.01 lot @ 62498.75 — retcode 10009 (done), verified
  present via `get_pending_orders_with_magic`.
- Ticket `171621249`: SELL_LIMIT BTCUSD 0.01 lot @ 62592.09 — retcode 10009 (done), verified
  present.

Both recorded locally as `OPEN`/`strategy="grid"`/`magic=71101` in `var/order_state/`. Two
already-known quirks observed, not new problems:
- This script's own "before/after" visibility print, which queries
  `account.get_orders(symbol=SYMBOL, magic=magic)`, showed 0 live orders for `magic=71101` even
  right after both were placed — MT5 is confirmed to always report `magic=0` on orders this
  project places (`docs/mcp_tool_classification.md` item 7), so a magic-filtered query always
  shows 0 regardless of whether the ticket is genuinely present. `McpOrderExecutor`'s own
  internal verification (`_verify_present()`) is unaffected — it queries `get_orders()`
  unfiltered by magic, which is why it correctly confirmed both tickets.
- `require_demo_account informational check failed: trade_mode='REAL'` printed several times —
  the same known-inverted `account_type` field (`docs/mcp_tool_classification.md` gap 3),
  informational only; the real hard gate (`mt5_account_kind='DEMO'`, checked by
  `require_demo_account_kind()`) passed and is what actually matters.

Both LIMIT orders had `sl=0.0, tp=0.0` in their `OrderPlan` — `GridStrategyConfig()`'s default
behavior via `build_order_plan()`, unrelated to this script; not investigated further here since
this step's goal was proving the wiring, not tuning the grid strategy's sl/tp choices.

By design, nothing was cleaned up after this run — both orders were left live on the account.

## Step 3 — explicit cancellation of both orders

User approved cancelling both resulting tickets. New one-off script,
`scripts/run_demo_execution_cancel_pipeline_cycle_orders.py`, hardcoded to exactly
`TICKETS = (171621248, 171621249)` — not a general-purpose cancel tool. Requirements (all met,
confirmed by the actual run output, not assumed):
- Verified both tickets present in live pending orders (`account.get_orders(symbol=SYMBOL)`,
  deliberately unfiltered by magic — see the quirk above) before attempting anything; would have
  aborted with no cancel attempted on either ticket if either had been missing.
- Exactly one `cancel_pending_order` attempt per ticket via `McpOrderExecutor.cancel()`, no
  retry of either; one ticket's cancel raising would not have blocked the other (wrapped
  independently in `try`/`except`) — not exercised this run since both succeeded on the first
  attempt.
- No new order placed anywhere in this script.

**Run 2026-08-03, result: both cancelled successfully.**
- Ticket `171621248`: retcode 10009 (done), `verified=True`, "confirmed absent from live
  orders".
- Ticket `171621249`: retcode 10009 (done), `verified=True`, "confirmed absent from live
  orders".

Final state: 0 live pending orders on BTCUSD (all magics); both local records transitioned to
`CANCELLED` (`closed_reason="cancel confirmed via McpOrderExecutor.cancel()"`). Account is clean.

## Remaining risks / not done

- Only `STRATEGY="GRID"` has been run live once. `STRATEGY="RUNNER"` has never been exercised
  against the real executor.
- Still no internal scheduler/loop — every cycle requires a separate, manual, human-approved
  invocation. Whether/when to build a bounded autonomous loop (the option not chosen when this
  effort started) is undecided.
- The `all_open()` O(N)-per-`McpOrderExecutor`-action cost flagged in
  `docs/PHASE7_REGRESSION_FAILURE_TESTING_CHECKPOINT.md` remains unaddressed — still not a real
  problem at current ticket volumes.
- No live run has yet exercised a `GridCycleError` (partial-failure) path for real, nor a
  `STRATEGY="RUNNER"` FLAT/rejected/no-submission outcome.

## Exact next smallest task

Not started — ask the user whether to run `STRATEGY="RUNNER"` live next, run another `"GRID"`
cycle, design the bounded-autonomous-loop option, or something else. Stopping here per this
project's standard "explain, implement, report, stop for approval" workflow.
