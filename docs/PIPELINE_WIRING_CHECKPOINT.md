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

## Step 7 — first real (non-smoke-test) live RUNNER submission

`scripts/run_demo_execution_pipeline_cycle.py` run live with `STRATEGY="RUNNER"`
(`magic=72101`, the real registered "runner" strategy identity — not the Step 6 smoke test's
throwaway `79999`). **Result: PASSED, submitted and verified, left open by design.**

- Signal: SHORT → SELL MARKET (live MACD sign at run time).
- Ticket `171621825`: requested `side=SELL, volume=0.01, price=62554.5, sl=62572.03,
  tp=62519.44`. Retcode `10009` (done), `executed_price=62552.54`, deal `99727187`,
  `verified=True` — `McpOrderExecutor`'s internal check confirmed both position presence and
  exact SL/TP match (attempt 1/3).
- Local record: `strategy='runner'`, `status='OPEN'`.

Per this script's designed behavior (see its module docstring and the Goal section above), **no
cleanup was performed** — asked the user explicitly what to do with it. **Decision: leave it
open.** Unlike Step 3's GRID orders (which the user chose to cancel), ticket `171621825` stays
live on the demo account, `magic=72101`, `strategy='runner'`, to be picked up and managed by a
later cycle/reconciliation — consistent with this whole effort's designed behavior for the real
pipeline-wiring script (not the smoke test's disposable, self-cleaning pattern).

**Files changed this step**: this checkpoint doc only (no code changes — this step only ran the
already-committed script).

## Step 8 — second real GRID submission via pipeline_cycle.py

`scripts/run_demo_execution_pipeline_cycle.py` run live again with `STRATEGY="GRID"`
(`magic=71101`), with ticket `171621825` (Step 7's open runner position) still live on the
account at the time — no conflict, since grid/runner use disjoint magics and grid's guards are
scoped per-magic. **Result: PASSED, both sides submitted and verified, left open by design.**

- Ticket `171621926`: BUY_LIMIT BTCUSD 0.01 lot @ 62535.88 — retcode `10009` (done),
  `verified=True` (confirmed present via `get_pending_orders_with_magic`, attempt 1/3).
- Ticket `171621927`: SELL_LIMIT BTCUSD 0.01 lot @ 62562.22 — retcode `10009` (done),
  `verified=True`, same confirmation.
- Both `sl=0.0, tp=0.0` — the same known, documented, unfixed grid gap (LIMIT orders aren't
  hard-validated for SL/TP, so this doesn't block submission; see "Remaining risks").
- Both local records: `strategy='grid'`, `status='OPEN'`.

Per this script's designed behavior, no cleanup was performed — asked the user explicitly what
to do with both. **Decision: leave them open**, consistent with Step 7's decision for the
runner position — both pending orders stay live on the demo account, `magic=71101`, to be
picked up by a later cycle/reconciliation rather than closed now.

**Files changed this step**: this checkpoint doc only (no code changes — this step only ran the
already-committed script).

## Step 9 — closing all 3 open items: account had moved since Step 8

User asked to close all three open items. First attempt, a new one-off script
(`scripts/run_demo_execution_close_pipeline_open_items.py`) written against the state as
reported in Step 8, **correctly aborted before touching anything**: a fresh live check showed
the account had moved on since that report —

- Ticket `171621825` (runner SELL position) was **already absent** from live positions — most
  likely closed automatically by the broker hitting its own SL (`62572.03`) or TP (`62519.44`).
  This is itself a good live proof point: the SL/TP fix isn't just passing validation, it's
  real, broker-monitored protection that actually triggers. Local `StateStore` had no way to
  learn about this on its own (it only updates via an explicit `record_closed()`/`record_cancelled()`
  call from `McpOrderExecutor`, never automatically) — the local record was stale, still `OPEN`.
- Ticket `171621926` (grid BUY_LIMIT) had **filled** into a real, live BUY position — needing
  `close_position()`, not `cancel()`.
- Ticket `171621927` (grid SELL_LIMIT) was unchanged, still a live pending order.

Asked the user how to proceed given the changed state. **Decision**: close the now-filled
position, cancel the still-pending order, and reconcile the stale local record for the
already-gone position (mark it `CLOSED` locally, no MCP call needed since there was nothing
left on the broker side to act on).

Script rewritten against the confirmed-current state (re-verified live immediately before
acting, not trusting the Step 8 report) and re-run. **Result: PASSED, all three resolved.**

- Ticket `171621926`: `close_position()` — retcode `10009` (done), `executed_price=62494.58`,
  deal `99727478`, `verified=True`, confirmed absent afterward. Local status → `CLOSED`.
- Ticket `171621927`: `cancel()` — retcode `10009` (done), `verified=True`, confirmed absent
  afterward. Local status → `CANCELLED`.
- Ticket `171621825`: `StateStore.record_closed()` only (no MCP call) — local status → `CLOSED`,
  reason recorded as "confirmed absent from live positions — closed outside this process, most
  likely via broker-side SL/TP execution".

Final state: 0 live positions, 0 live pending orders on BTCUSD. Account is fully clean. `pytest -q`
still 327 passed, architecture tests still 13 passed (no test/production code changed this step).

**Files changed this step**: `scripts/run_demo_execution_close_pipeline_open_items.py` (new,
rewritten once mid-step against the corrected live state), this checkpoint doc.

## Step 10 — fix: grid's LIMIT orders now get a real SL/TP

Design chosen after research (not the only option, but the one that reuses the most existing
code and best matches this project's own conventions): research found a real asymmetry with the
runner fix. `domain/models.py`'s `GridLevels` already had a `tp_price` field, computed by
`strategy/grid.py`'s `compute_grid_levels()` — but it was **completely dropped**:
`trade_intent/grid.py` never reads it, and `pipeline/grid_cycle.py` never referenced
`levels.tp_price` after computing it. Grid's TP distance already existed and was already fully
tested (`tests/unit/test_strategy_grid.py`), just never wired into an actual order. Grid's
stop-loss side, by contrast, had **no precedent at all** — no field, no legacy formula, no
docstring mention either way (unlike runner's docstring, which explicitly confirmed its legacy
never had SL/TP — grid's docstring is simply silent on the question). No existing test locked
in `sl=0.0`/`tp=0.0` as correct, so nothing needed to break to fix this.

Also confirmed: unlike runner's MARKET path (place naked, then a mandatory separate
`modify_position` call to attach SL/TP), grid's LIMIT path sends `stop_loss`/`take_profit`
**directly** to the broker in the same `place_pending_order` call (confirmed by reading the
vendored `metatrader_client` source) — no second call needed, and no mandatory-non-zero
validation exists for it in `McpOrderExecutor` either, which is exactly why this shipped silently
unprotected for two live runs (Step 2, Step 8) without ever erroring.

**User chose the SL design**: a new, independent ATR-based multiplier
(`GridStrategyConfig.sl_atr_mult`, default `2.0`), mirroring the exact pattern already built for
runner (`compute_stop_distances()`'s independent `sl_atr_mult`/`tp_atr_mult`) — both strategies
now share the same "ATR × configurable multiplier" convention for their stop, while grid's
existing, already-tested `tp_price` formula (`step_mult*1.2`) is left completely untouched.

**Implemented**:
- `domain/models.py`: `GridLevels` gains `sl_price: float`.
- `strategy/grid.py`: `GridStrategyConfig` gains `sl_atr_mult: float = 2.0` — new,
  project-original, no legacy value (documented as such), deliberately independent of
  `step_mult` so it can never make the existing `tp_price` formula stale.
  `compute_grid_levels()` computes `sl_price` alongside `tp_price`, same fallback shape
  (`atr<=0` → `min_step_points*point`; else → `max(min_step_points*point, atr*sl_atr_mult)`).
- `pipeline/grid_cycle.py`: mirrors `runner_cycle.py`'s already-established pattern (compute
  sl/tp from a levels-derived distance anchored to the intent's reference price, pass as
  `sl=`/`tp=` kwargs into `build_order_plan()`, not a post-hoc mutation) — BUY:
  `sl=reference_price-levels.sl_price, tp=reference_price+levels.tp_price`; SELL: mirrored.
  `run_runner_cycle()` untouched (already fixed, Step 5).

**Tests**: `tests/unit/test_strategy_grid.py` (+3 — `sl_price`'s ATR-scaled value, floor
fallback, and independence from `step_mult`/coupling only to `sl_atr_mult`).
`tests/integration/test_grid_dry_run_pipeline.py` (+1 —
`test_both_sides_produce_protected_orders_with_correct_sl_tp_ordering`, asserting `sl>0`, `tp>0`,
and the same BUY/SELL ordering `McpOrderExecutor` would enforce for MARKET — the direct
regression proof, failing before this fix and passing after).

```
pytest -q                        -> 330 passed (327 previously + 3 new)
pytest tests/test_architecture.py -q -> 13 passed
```

**Not yet live-verified.** No live call was made this step, matching the runner fix's own
two-step "fix then separately-approved live verification" precedent — a follow-up live GRID run
(smoke test or the real `scripts/run_demo_execution_pipeline_cycle.py`) is a separate, explicit
next action.

**Files changed this step**: `src/mt5_mcp_trading/domain/models.py`,
`src/mt5_mcp_trading/strategy/grid.py`, `src/mt5_mcp_trading/pipeline/grid_cycle.py`,
`tests/unit/test_strategy_grid.py`, `tests/integration/test_grid_dry_run_pipeline.py`,
`AGENTS.md`, this checkpoint doc.

## Remaining risks / not done

- Grid's LIMIT-orders-unprotected gap (Step 4/Step 8) is **fixed** (Step 10, above) but **not
  yet live-verified** — the fix has only been proven against `DryRunExecutor`/tests so far, the
  same gap runner's original bug hid behind before its own live verification (Steps 6-7).
- Account is currently clean (0 open items from this effort, as of Step 9) — a real,
  live-confirmed reminder that account state can move between a report and a follow-up action
  (order fills, broker-side SL/TP triggers), so any future cleanup script must re-verify live
  immediately before acting, never trust an earlier report as still current.
- Still no internal scheduler/loop — every cycle requires a separate, manual, human-approved
  invocation. Whether/when to build a bounded autonomous loop (the option not chosen when this
  effort started) is undecided.
- The `all_open()` O(N)-per-`McpOrderExecutor`-action cost flagged in
  `docs/PHASE7_REGRESSION_FAILURE_TESTING_CHECKPOINT.md` remains unaddressed — still not a real
  problem at current ticket volumes.
- No live run has yet exercised a `GridCycleError` (partial-failure) path for real, nor a
  `STRATEGY="RUNNER"` FLAT/rejected/no-submission outcome via the real pipeline-wiring script.

## Exact next smallest task

Not started — account is clean. Ask the user whether to live-verify Step 10's grid SL/TP fix
next (smoke test or the real pipeline-wiring script), design the bounded-autonomous-loop option,
or something else. Stopping here per this project's standard "explain, implement, report, stop
for approval" workflow.
