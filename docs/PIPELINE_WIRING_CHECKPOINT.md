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

## Step 4 — first live RUNNER run: found a real pipeline/executor gap, no live impact

`STRATEGY` flipped to `"RUNNER"` and run live, 2026-08-03. **Result: `run_runner_cycle()` raised
`InvalidOrderPlanError`, no order reached the broker.**

Root cause, confirmed by reading the code, not guessed: `order_planning/plan.py`'s
`build_order_plan()` takes `sl`/`tp` as optional parameters defaulting to `0.0`/`0.0`
(`plan.py:46-47`). Neither `pipeline/grid_cycle.py:110` nor `pipeline/runner_cycle.py:69` ever
passes them — both call sites omit `sl`/`tp` entirely, so every `OrderPlan` this pipeline layer
has ever produced carries `sl=0.0, tp=0.0`, always. This was invisible until now because every
prior real/near-real exercise of these functions used either `DryRunExecutor`
(`run_live_dry_run_pipeline.py`) or a `MockOrderExecutor` in tests — neither validates SL/TP at
all. The real `McpOrderExecutor._validate_market_sl_tp()` does, and only for MARKET orders
(mandatory non-zero `sl`/`tp` before any MCP call is made, so nothing unsafe ever reaches the
broker) — this is why Step 2's GRID run (LIMIT orders only) went through fine despite the same
`sl=0.0, tp=0.0` values, while this RUNNER run (MARKET orders) was refused outright.

**No live impact**: `_validate_market_sl_tp()` raises before `_submit_market()` makes any MCP
call, confirmed by this run's own before/after prints — 0 positions, 0 pending orders, 0 local
records for `magic=72101`, identical before and after. Nothing was submitted anywhere.

**Not fixed yet, flagged for a decision**: `run_runner_cycle()` (and, less urgently since
LIMIT isn't hard-validated, `run_grid_cycle()`) has no way to ever succeed against the real
executor's MARKET path until something upstream computes real `sl`/`tp` values and threads them
through to `build_order_plan()`. This is a genuine pipeline-wiring gap this step's whole purpose
was to surface — not a bug in `McpOrderExecutor` (its mandatory-SL/TP-for-MARKET rule is an
intentional Phase 6 safety decision, working exactly as designed) and not something either dry-
run test suite could have caught (neither exercises real validation).

## Step 5 — fix: runner's MARKET orders now get a real SL/TP

Design chosen (not the only option, but the one that reuses the most existing code): a new
pure function, `strategy/runner.py`'s `compute_stop_distances(bars, point, config) ->
(sl_distance, tp_distance)`, reusing `features/atr.py`'s existing, strategy-agnostic `atr()`
helper — the same one `strategy/grid.py` already uses for its own step/tp sizing, so no new ATR
logic was written. Falls back to a fixed points floor (mirroring
`strategy/grid.py`'s `compute_grid_levels()` fallback) when ATR can't be computed.

**Explicitly a new, project-original design, not a legacy port**: confirmed by reading
`strategy/runner.py`'s own module docstring that the legacy `ema_crossover_core_multi.py`
runner never attached SL/TP to its MARKET orders at all — there was no formula to preserve.
`RunnerStrategyConfig` gained 4 new fields, all fresh defaults (not derived from any legacy
value): `atr_period=14` (matches `GridStrategyConfig`'s default), `sl_atr_mult=1.5`,
`tp_atr_mult=3.0` (2:1 reward:risk), `min_stop_distance_points=10.0` (matches
`GridStrategyConfig.min_step_points`'s floor convention).

`pipeline/runner_cycle.py` now computes `sl`/`tp` from `compute_stop_distances()` and the same
BUY/SELL reference-price logic `build_order_plan()`'s own MARKET fallback already uses, and
passes them into `build_order_plan(..., sl=sl, tp=tp)` — previously omitted entirely, always
defaulting to `0.0`/`0.0`.

**Not in scope for this fix** (deliberately): `run_grid_cycle()`'s parallel LIMIT-orders-
unprotected question remains open, untouched. Wiring the computed distance into
`sizing/money.py`'s `MoneyConfig.stop_distance_points` (today a static, caller-supplied value
only consumed by the unrelated `"risk_percent"` lot-sizing mode) was considered and explicitly
deferred — broader than "stop `InvalidOrderPlanError` from being raised."

**Tests** (all passing, full suite unaffected): `tests/unit/test_strategy_runner.py` (+2 —
`compute_stop_distances()`'s ATR-available and floor-fallback paths).
`tests/integration/test_runner_dry_run_pipeline.py` (+2 —
`test_long_signal_produces_a_protected_market_order`/`test_short_signal_produces_a_protected_market_order`,
asserting `sl>0`, `tp>0`, and the exact BUY (`sl<price<tp`)/SELL (`tp<price<sl`) ordering
`McpOrderExecutor._validate_market_sl_tp()` enforces — these are the direct regression proof for
the exact live-discovered bug: they fail before this fix, pass after, no live call needed).

```
pytest -q                        -> 327 passed (323 previously + 4 new)
pytest tests/test_architecture.py -q -> 13 passed
```

**Not yet re-run live.** `STRATEGY="RUNNER"` has never actually completed a submission against
the real `McpOrderExecutor` — only failed once (Step 4) and been fixed since (this step).
Re-running it live to prove the fix for real is a separate, explicit next action.

**Files changed this step**: `src/mt5_mcp_trading/strategy/runner.py`,
`src/mt5_mcp_trading/pipeline/runner_cycle.py`, `tests/unit/test_strategy_runner.py`,
`tests/integration/test_runner_dry_run_pipeline.py`, `AGENTS.md`, this checkpoint doc.

## Step 6 — live verification of Step 5's fix

New one-off, self-cleaning smoke test (unlike `scripts/run_demo_execution_pipeline_cycle.py`,
which deliberately does not clean up): `scripts/run_demo_execution_runner_sltp_smoke_test.py`.
Calls the real, fixed `run_runner_cycle()` (not a hand-built `OrderPlan`) against the real
`McpOrderExecutor`, using `SMOKE_TEST_MAGIC=79999` (the Phase 6 convention, distinct from the
"real" `72101` used by the pipeline-wiring script) and the symbol's live `volume_min`. Verifies
a local-state leftover guard before running, asserts non-zero/correctly-ordered SL/TP on the
resulting plan, independently re-reads the live position's actual SL/TP after opening (not just
`ExecutionResult.verified`), and cleans up with one `close_position()` call on full success only
— mirroring every prior Phase 6 smoke test's "prove it round-trips, then leave the account
clean" pattern.

**Run 2026-08-03, result: PASSED, full round trip confirmed live.**

- Signal: SHORT → SELL MARKET (`runner_signal()`'s live MACD sign at run time — not
  controllable in advance).
- Ticket `171621792`: requested `side=SELL, volume=0.01, price=62564.91, sl=62585.4,
  tp=62523.94` (the fixed code's ATR-based `compute_stop_distances()` output).
- Submit: retcode `10009` (done), `executed_price=62565.92`, deal `99727152`, `verified=True`
  (`McpOrderExecutor`'s internal check confirmed both position presence and exact SL/TP match,
  attempt 1/3).
- **Independent re-read** (`account.get_positions()`, separate from the internal verification
  above): live `sl=62585.4, tp=62523.94` — exact match to requested. This is the direct,
  live-confirmed proof that Step 5's fix produces a real, broker-attached SL/TP, not just a
  locally-computed value that happens to satisfy validation.
- Cleanup: `close_position()` — retcode `10009` (done), `executed_price=62583.86`, deal
  `99727153`, `verified=True`, confirmed absent from live positions afterward.

Final state: 0 live positions on BTCUSD (all magics); local record transitioned to `CLOSED`.
Account is clean. `pytest -q` still 327 passed, architecture tests still 13 passed (unaffected
by a live run, as expected — no test changes this step, only a new one-off script).

**Files changed this step**: `scripts/run_demo_execution_runner_sltp_smoke_test.py` (new), this
checkpoint doc.

## Remaining risks / not done

- `run_grid_cycle()`'s LIMIT orders still carry `sl=0.0, tp=0.0` today (same underlying gap as
  Step 4, never hard-validated for LIMIT so never blocked) — worth a decision on whether grid's
  pending orders are supposed to be protected at placement too, separately from Step 5's fix.
- `STRATEGY="GRID"` has been run live once (Step 2, then cleaned up Step 3); `STRATEGY="RUNNER"`
  has now been proven live end-to-end via the dedicated smoke test (Step 6), but
  `scripts/run_demo_execution_pipeline_cycle.py` itself (the "real", non-cleaning
  pipeline-wiring script, magic=72101) has still never completed a `STRATEGY="RUNNER"`
  submission — only the Step 4 failure and this step's separate smoke test have exercised the
  runner MARKET path live so far.
- Still no internal scheduler/loop — every cycle requires a separate, manual, human-approved
  invocation. Whether/when to build a bounded autonomous loop (the option not chosen when this
  effort started) is undecided.
- The `all_open()` O(N)-per-`McpOrderExecutor`-action cost flagged in
  `docs/PHASE7_REGRESSION_FAILURE_TESTING_CHECKPOINT.md` remains unaddressed — still not a real
  problem at current ticket volumes.
- No live run has yet exercised a `GridCycleError` (partial-failure) path for real, nor a
  `STRATEGY="RUNNER"` FLAT/rejected/no-submission outcome.

## Exact next smallest task

Not started — ask the user whether to run `STRATEGY="RUNNER"` live via the actual, non-cleaning
`scripts/run_demo_execution_pipeline_cycle.py` next (the smoke test in Step 6 only proved the
fix in isolation, magic=79999), address the grid LIMIT-orders-unprotected question, design the
bounded-autonomous-loop option, or something else. Stopping here per this project's standard
"explain, implement, report, stop for approval" workflow.
