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

## Step 11 — first live GRID run of the SL/TP fix: found a real bug in the anchor price

`scripts/run_demo_execution_pipeline_cycle.py` run live with `STRATEGY="GRID"`. **Result: SELL
side succeeded and verified; BUY side rejected — a genuine bug in Step 10's fix, not a broker
quirk, with no live impact from the rejection itself.**

- SELL: ticket `171622543`, `price=62499.26, sl=62558.89, tp=62484.95` — correct ordering
  (`tp<price<sl`), retcode `10009`, `verified=True`. Left open on the account by this script's
  design.
- BUY: `price=62287.56, sl=62415.79, tp=62489.72` — `sl` is **above** `price`, backwards for a
  BUY. Rejected client-side (`"Stop loss must be less than price"`, retcode `-1`, no ticket,
  `success=False`) before any request reached the broker — no invalid order was ever placed.

**Root cause**: Step 10's `grid_cycle.py` fix computed `sl`/`tp` relative to
`intent.reference_price` (`levels.buy_price`/`levels.sell_price`, i.e. `center ± step_price`,
computed from bars alone) rather than the final, broker-normalized `plan.price` returned by
`build_order_plan()`/`normalize_limit_price()`. The Step 10 plan explicitly assumed this
difference would be "negligible relative to ATR-scaled distances" — **that assumption was
wrong**: `normalize_limit_price()` pushed this BUY's actual entry down by over 160 points to
satisfy the broker's minimum-distance gap from the current market, but the SL/TP were still
anchored to the old, un-pushed reference price, inverting the SL relative to the real entry.
Neither of Step 10's new tests caught this because `tests/integration/test_grid_dry_run_pipeline.py`'s
fixture keeps bid/ask deliberately close to `center`, so normalization never needs to push far
enough to expose the divergence — a gap in what those tests actually cover, not proof they were
wrong for what they did cover.

**Not fixed yet.** Needs: recomputing `sl`/`tp` relative to the actual `plan.price` (after
`build_order_plan()` returns, not before) instead of `intent.reference_price`, plus a new
regression test that deliberately forces `normalize_limit_price()` to push the price away from
the naive `center ± step_price` level (unlike the existing fixture, which never does).

**Files changed this step**: this checkpoint doc only (no code changes yet — this step only ran
the already-committed code and found the bug).

## Step 12 — fix: anchor grid's SL/TP to the actual normalized entry price

User approved the recommended fix. `pipeline/grid_cycle.py` now calls `build_order_plan()`
first (as before Step 10, no `sl=`/`tp=` kwargs), then computes `sl`/`tp` from the returned
`plan.price` (the real, broker-normalized entry `normalize_limit_price()` decided on) instead of
`intent.reference_price`, and attaches them via `dataclasses.replace(plan, sl=sl, tp=tp)` before
`executor.submit()`. `strategy/grid.py`/`domain/models.py` (Step 10's `sl_price`/`tp_price`
computation itself) are untouched — only the anchor point for applying them changed.

**New regression test**, `tests/integration/test_grid_dry_run_pipeline.py`'s
`test_sl_tp_ordering_holds_even_when_normalize_limit_price_pushes_the_entry_far`: sets bid/ask
far below the fixture's `center` (~63010 vs. bid=62000/ask=62002), deliberately forcing
`normalize_limit_price()` to push the BUY_LIMIT price down substantially (confirmed via an
explicit `abs(buy_plan.price - levels.buy_price) > 100` assertion, so the test can't pass by
accident without actually exercising the push) — then asserts the same `sl<price<tp`/
`tp<price<sl` invariants as the existing regression test. **Verified this test actually catches
the bug**, not just theoretically: ran it against the pre-fix code (`git stash` on
`grid_cycle.py` alone) and confirmed it fails with the exact live-observed shape (`sl=62949.3`
above `price=61999.8`); passes again with the fix restored.

```
pytest -q                        -> 331 passed (330 previously + 1 new)
pytest tests/test_architecture.py -q -> 13 passed
```

**Decision on the open position from Step 11**: ticket `171622543` (SELL, `magic=71101`,
`strategy='grid'`, correct SL/TP, unaffected by the bug) — user chose to leave it open, same as
every other open-position decision this effort.

**Not yet re-verified live.** The fix is proven against a dry-run test that specifically
reproduces the failure mode, but has not yet been re-run against the real `McpOrderExecutor` —
a follow-up live GRID run (ideally one that would have triggered the same normalization push,
though that depends on live market conditions, not something this script controls) is a
separate, explicit next action.

**Files changed this step**: `src/mt5_mcp_trading/pipeline/grid_cycle.py`,
`tests/integration/test_grid_dry_run_pipeline.py`, `AGENTS.md`, this checkpoint doc.

## Step 13 — live re-verification of Step 12's anchor-price fix

`scripts/run_demo_execution_pipeline_cycle.py` run live again with `STRATEGY="GRID"` (ticket
`171622543` from Step 11 still open at the time, unaffected). **Result: PASSED, both sides
submitted and verified with correct SL/TP ordering.**

- BUY: ticket `171622789`, `price=62276.52, sl=62192.69, tp=62296.64` —
  `sl < price < tp` ✓. Retcode `10009`, `verified=True`.
- SELL: ticket `171622791`, `price=62443.94, sl=62527.77, tp=62423.82` —
  `tp < price < sl` ✓. Retcode `10009`, `verified=True`.

Both sides now get correctly-ordered, non-zero SL/TP sent directly to the broker — confirms
Step 12's fix live, not just against the dry-run regression test. Per this script's design, no
cleanup was performed; account now holds 3 open grid items (`171622543` from Step 11 plus these
two).

**Files changed this step**: this checkpoint doc only (no code changes — this step only ran the
already-committed script).

## Step 14 — bounded autonomous loop: designed and implemented, not yet run live

The option deliberately not chosen when this effort started ("human-approved per cycle" was
picked instead) — a script with its own internal loop, approved once at launch, that runs
multiple cycles against the real `McpOrderExecutor` without a human approving each individual
one. This is the biggest step this effort has taken: every order-submitting action to date has
had its own separate approval; this changes that for whatever happens *during* a run, while the
initial launch remains the one explicit approval point.

Research confirmed this is genuinely greenfield — no daily-shutdown/kill-switch/circuit-breaker
concept exists anywhere in this codebase (`risk/__init__.py` explicitly: these "do not exist in
the legacy project... would be new functionality, not a migration"), every existing risk guard
is a stateless per-call snapshot check with zero cross-call memory, `_current_posture()` has no
history across cycles, logging is console-only with no file handler anywhere, and no
signal-handling pattern exists in this repo at all.

**Four structural decisions**, all made explicitly before any code was written:
1. **Strategy scope**: both `run_grid_cycle()` and `run_runner_cycle()` every cycle,
   sequentially — one raising never blocks the other from being attempted that same cycle
   (mirrors `GridCycleError`'s own per-side isolation, one level up).
2. **Stop mechanism**: a stop-file (`var/STOP_PIPELINE_LOOP`) checked before every cycle and
   polled every 5s during the inter-cycle wait, plus clean `Ctrl+C` handling.
3. **Error handling across cycles**: stop the loop immediately after any cycle in which either
   strategy raised — no error-tolerance/retry in this first version.
4. **Connection model**: one long-lived `demo_execution_session()` for the whole run; a dropped
   connection is fatal (caught by decision 3, no reconnect logic built).

**Implemented**:
- `src/mt5_mcp_trading/pipeline/loop_control.py` (new): the one piece of genuinely new decision
  logic (`should_stop()`, `LoopLimits`), kept pure and separately testable rather than buried in
  the script — precedence: stop-file, then max cycles, then max runtime.
- `scripts/run_demo_execution_pipeline_loop.py` (new): thin orchestration shell, structured like
  `run_demo_execution_pipeline_cycle.py` (same `SYMBOL`/`GRID_MAGIC`/`RUNNER_MAGIC`/`CAPS`).
  Conservative first-run defaults: `CYCLE_INTERVAL_SECONDS=300` (5 min), `MAX_CYCLES=12`,
  `MAX_RUNTIME_MINUTES=90` — at most ~1 hour of actual cycling under a 90-minute hard ceiling.
  `ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06)` (unchanged, reused) still bounds
  standing exposure per symbol regardless of cycle count. No cleanup of submitted orders/
  positions, ever — same "a real cycle's result persists" design as the single-shot script.
  Adds a per-run file log under `var/logs/` (in addition to console output) so an unattended run
  leaves a durable record — additive only, `monitoring/logging_setup.py` itself untouched.

**Tests**: `tests/unit/test_pipeline_loop_control.py` (new, +10) — `should_stop()`'s three
conditions individually, precedence order, boundary (`>=` not `==`), and the "nothing applies"
case. Pure, no live connection needed.

```
pytest -q                        -> 341 passed (331 previously + 10 new)
pytest tests/test_architecture.py -q -> 13 passed
```

**Not yet run live.** This is the largest-blast-radius script in the project so far — running it
is a separate, explicit next action requiring its own go-ahead after review, exactly like every
other live action in this effort.

**Files changed this step**: `src/mt5_mcp_trading/pipeline/loop_control.py` (new),
`scripts/run_demo_execution_pipeline_loop.py` (new),
`tests/unit/test_pipeline_loop_control.py` (new), `AGENTS.md`, this checkpoint doc.

## Step 15 — first live loop run: apparent hang (false alarm), real credential exposure found, 3 fixes made

First live run of the bounded autonomous loop. Launched in the background; appeared to stall
mid-cycle-1 (no new console output for several checks). Investigated before assuming anything.

**Finding 1 — no hang occurred.** Comparing the buffered "stuck" output against the full log
captured after the process exited: the last visible tool-call log before the apparent stall was
at a given timestamp, the next appeared **exactly 300 seconds later** — matching
`CYCLE_INTERVAL_SECONDS` exactly. Cross-checked against `StateStore`: cycle 1's runner order was
submitted, confirmed, and SL/TP-verified under a second after the cycle started. Root cause:
Python fully block-buffers `print()` output when stdout isn't a TTY (true for a
backgrounded/redirected run) — the `logging`-module `WARNING` lines appeared live (their
handler flushes per record), but this script's `print()`-based cycle headers/results did not,
making a process that had already finished and moved into its normal inter-cycle sleep look
stuck mid-call. No MCP/tool call ever failed to return.

**Finding 2 — real credential exposure, unrelated to the hang investigation but found while
diagnosing it.** Checking whether the loop's process was still alive (`Get-CimInstance
Win32_Process ... CommandLine`) revealed `scripts/metatrader_mcp_extended_server.py`'s full
command line, including the plaintext demo account password, passed via
`scripts/run_metatrader_mcp_stdio.py:101-106`'s `--password` argv construction — visible to any
process/user on the machine via ordinary, unprivileged process listing. Contradicted that
script's own docstring claim of never exposing credentials "on any command line visible outside
this process."

**Stopped the loop safely**: created the stop-file; the loop detected it during its inter-cycle
wait and exited cleanly (exit code 0) within its normal ~5s poll interval — no forced kill
needed. Confirmed via process listing that no related processes remained.

**Cleanup**: independently re-verified live state (separate read-only connection) before
touching anything — state had moved again since the hang investigation: 4 of the loop's 6
tickets (`171623173`, `171623174`, `171623175`, `171623293`) had already closed on their own via
their own SL/TP before cleanup even started (live evidence the SL/TP fixes provide real,
triggering protection, not just validation-passing values); only `171623291` (pending) and
`171623294` (open position) were still genuinely live. New one-off script,
`scripts/run_demo_execution_cleanup_loop_run.py`, re-verified each of the 6 tickets individually
immediately before acting: cancelled `171623291` (retcode 10009), closed `171623294` (retcode
10009), reconciled the other 4 locally (`StateStore.record_closed()`, no MCP call — nothing left
on the broker side to act on). All 6 resolved; account left with only the 2 tickets pre-existing
before this loop run (`171622543`/`171622791` — untouched, out of scope; `171622789` also
disappeared from live state during this window, noted but not acted on, out of scope).

**Three fixes implemented** (code only — no live call made to verify any of them; that remains a
separate, explicit next action):

1. **Credential exposure** (Finding 2): `metatrader_mcp_extended_server.py` now falls back to
   `MT5_DEMO_LOGIN`/`MT5_DEMO_PASSWORD`/`MT5_DEMO_SERVER` from its environment (already inherited
   from the parent process — `subprocess.run()` inherits by default) when `--login`/`--password`/
   `--server` aren't given via argv; `run_metatrader_mcp_stdio.py` no longer puts them on argv at
   all. The child's command line now only ever shows `--transport`/`--path` — never credentials.
2. **MCP call timeout** (a real, separate gap the hang investigation surfaced — no call anywhere
   in this codebase had ever had a timeout): `McpClient.call_tool()` now wraps the underlying
   session call in `asyncio.wait_for(timeout=DEFAULT_CALL_TIMEOUT_SECONDS=30.0)`, raising a new
   `McpCallTimeoutError` on expiry. Centralized in the one place every tool call already passes
   through — every caller benefits, no changes needed elsewhere (the loop's existing "stop on any
   error" handling already covers this new exception type correctly).
3. **stdout buffering** (Finding 1): `run_demo_execution_pipeline_loop.py`'s own status
   reporting now goes entirely through the `logging` module (`get_logger(...)`) instead of
   `print()` — fixes the lag (logging handlers flush per record) and, as a side effect, also
   fixes the file log missing this script's own output (it previously only captured
   `logging`-routed records).

**Tests**: `tests/unit/test_mcp_client.py` (new, +5) — `McpClient`'s first-ever dedicated unit
tests (everything else about it requires a real subprocess; only the new timeout wrapping is
practical to test in isolation, via a fake session object bypassing `__aenter__`): normal
completion, timeout-raises-`McpCallTimeoutError`-with-correct-attributes, registry
authorization still gates before the session is ever touched, and the pre-existing "used outside
`async with`" guard.

```
pytest -q                        -> 346 passed (341 previously + 5 new)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed this step**: `src/mt5_mcp_trading/mcp_adapter/client.py`,
`scripts/metatrader_mcp_extended_server.py`, `scripts/run_metatrader_mcp_stdio.py`,
`scripts/run_demo_execution_pipeline_loop.py`,
`scripts/run_demo_execution_cleanup_loop_run.py` (new), `tests/unit/test_mcp_client.py` (new),
this checkpoint doc.

## Remaining risks / not done

- Grid's SL/TP anchor-price bug (Step 11) is **fixed** (Step 12) and **live-verified** (Step 13,
  both BUY and SELL confirmed with correct ordering against the real `McpOrderExecutor`).
- **2 open items remain on the account, both pre-existing (not from the loop run)**: ticket
  `171622543` (SELL, Step 11) and `171622791` (SELL, Step 13) — user's standing decision to leave
  open. `171622789` (Step 13) disappeared from live state during Step 15's window; not
  investigated further, out of scope for that step.
- Account state can move between a report and a follow-up action (order fills, broker-side
  SL/TP triggers, confirmed repeatedly now — Step 9, and again in Step 15) — any future
  cleanup/diagnostic script must re-verify live immediately before acting, never trust an
  earlier report as still current.
- **The bounded autonomous loop has now run live twice** (Step 15, Step 17). Step 17 ran to its
  full designed `MAX_CYCLES=12` completion — the first time that stop path (vs. the stop-file
  path only, in Step 15) has been observed live. No reconnect-on-drop logic and no
  error-tolerance-across-cycles exist by design (v1) — the new MCP call timeout (Step 15's fix
  #2) means a truly stuck call now becomes a clean, bounded failure instead of an unbounded
  wait, but a dropped connection is still simply fatal to the run, by design.
- The three Step 15 fixes (credential passing, MCP call timeout, stdout buffering) are **now
  live-verified** (Step 17) — all three held up correctly across a full 12-cycle, ~56-minute
  unattended run.
- The `all_open()` O(N)-per-`McpOrderExecutor`-action cost flagged in
  `docs/PHASE7_REGRESSION_FAILURE_TESTING_CHECKPOINT.md` remains unaddressed — more relevant now
  that a loop could call it far more often than any single-shot script has so far, though still
  not a real problem at current ticket volumes/cycle counts.
- No live run has yet exercised a `GridCycleError` (partial-failure) path for real, nor a
  `STRATEGY="RUNNER"` FLAT/rejected/no-submission outcome via the real pipeline-wiring script.
- **CONFIRMED (post-Step-17 investigation): `ExposureCaps`/duplicate-order guard are both
  effectively no-ops in real live cycles.** Root cause found by reading code, not guessed:
  `risk/portfolio_guards.py`'s `check_exposure_cap()` itself is implemented correctly (checks
  *projected* total -- open + pending + proposed -- against the caps, per its own docstring's
  documented strengthening over the legacy vol_all-only check; not the bug). The bug is one layer
  up -- `pipeline/grid_cycle.py:86-89` and `pipeline/runner_cycle.py:57-60` compute
  `open_lots`/`pending_lots` via `account.get_positions(symbol, magic=magic)`/
  `account.get_orders(symbol, magic=magic)`, and `mt5_adapter/mcp_account.py:94-95,112-113`
  filters those client-side on `p.magic == magic`. Per this project's own already-documented
  finding (`docs/mcp_tool_classification.md` item 7, referenced repeatedly elsewhere), MT5
  always reports `magic=0` on every position/order this project's own executor places -- never
  the real intended magic -- so that client-side filter silently excludes ALL of this project's
  own real positions/orders, every call. `open_lots`/`pending_lots` are therefore always `0.0` in
  real live cycles regardless of actual standing exposure, which is exactly what let Step 17
  submit 12 straight grid cycles past a 0.06-lot cap unblocked. `grid_cycle.py:102-104`'s
  `check_duplicate_order()` is fed that same magic-filtered, always-empty `pending_orders` list,
  so it is equally blind to grid's own previously-placed pending orders in real live cycles --
  directly touching an `AGENTS.md` safety rule ("Never bypass ... duplicate-order ... guards").
  No test caught this because `MockAccountReader` filters on whatever `magic` a test fixture set
  (a normal, correct value) and never reproduces MT5's real `magic=0` quirk. Two candidate fixes
  were considered: drop the magic filter and rely on local `StateStore` for "is this ticket
  mine," or filter by `comment` prefix instead of `magic`. **Fixed in Step 18** (code only, not
  yet live-verified) via the `StateStore` approach -- see Step 18 for the full change.
- **New from Step 17**: local `StateStore` is now stale for the 27 (of 36) Step 17 tickets that
  closed via their own broker-side SL/TP without an explicit `close()`/`cancel()` call — same
  well-documented existing pattern (`StateStore` only updates via an explicit call, never
  automatically), not fixed here. Harmless, matches the `171622789` precedent already noted below.

## Step 16 — end-of-day safe stop, final confirmation

User called time at end of workday and asked for a final safe-stop confirmation, no further
investigation or new work. Verified fresh, independently, read-only:

- **No processes running** — the loop and every helper script from Step 15 had already exited;
  confirmed via process listing (`tasklist`), nothing to stop.
- **Live state re-checked**: exactly 2 items remain, both pre-existing from before Step 15's
  loop run — position ticket `171622791` (SELL, filled from Step 13's pending order) and pending
  order `171622543` (Step 11). **None of the 6 tickets Step 15's loop run created are present**
  — the Step 15 cleanup fully held; nothing unexpected. `171622789` (Step 13) remains absent
  from live state (noted already in Step 15, still out of scope — not this run's ticket, not
  investigated further, per "stop expanding" instruction).
- `pytest -q` → 346 passed (unchanged since Step 15's commit `e812867`). Architecture tests → 13
  passed.
- `git status`/`git diff` at session start: clean, nothing pending — Step 15's commit already
  captured everything. This step's only change is this addendum.

No code changed this step. No live order-affecting action taken — read-only verification only.

## Step 17 — second live loop run: ran to full completion (12/12 cycles), Step 15's three fixes confirmed live

User approved resuming live testing (per Step 16's exact next step). Before launch, found and
removed a stale artifact from Step 15's shutdown: `var/STOP_PIPELINE_LOOP` (an empty sentinel
file) was still present from when the previous run was stopped — left in place, the loop would
have detected it and exited immediately on its first check, doing nothing. Removed after
confirming its contents were empty (just the stop sentinel, not meaningful state).

Launched `scripts/run_demo_execution_pipeline_loop.py` live in the background with the exact,
unchanged Step 15/14 config (`SYMBOL="BTCUSD"`, `GRID_MAGIC=71101`, `RUNNER_MAGIC=72101`,
`ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06)`, `CYCLE_INTERVAL_SECONDS=300`,
`MAX_CYCLES=12`, `MAX_RUNTIME_MINUTES=90`) — confirmed with the user before launch, matching this
project's practice of a fresh explicit go-ahead for every live action.

**Result: ran to its full designed completion, 12/12 cycles, zero errors.** Stopped naturally via
`should_stop()`'s max-cycles condition (`"Stop requested during inter-cycle wait: max cycles (12)
reached"`), not the stop-file — the first time this stop path has been observed live (Step 15
only exercised the stop-file path). Disconnected cleanly afterward.

- **Every one of the 12 cycles' submissions succeeded**: retcode `10009`, `verified=True`, for
  both `run_grid_cycle()` (BUY_LIMIT + SELL_LIMIT each cycle, magic `71101`) and
  `run_runner_cycle()` (one MARKET order each cycle, side following the live MACD signal, magic
  `72101`) — **36 tickets total** (24 grid, 12 runner), all left open by the script's designed
  no-cleanup behavior.
- **Step 15's three fixes all held up live, across a full ~56-minute unattended run** (not just
  the 2-cycle partial run Step 15 itself achieved): no credential exposure observed (process
  command line never carried `--login`/`--password`), no `McpCallTimeoutError` raised (no call
  ever hung), and console/file log output stayed live throughout — confirmed directly by checking
  in on the running process mid-loop and seeing exactly the expected quiet 5-minute inter-cycle
  gaps, never an unexplained stall.
- **New one-off read-only script**, `scripts/run_demo_execution_loop_run_status_check.py`:
  queries live positions/orders for `BTCUSD` (all magics, unfiltered — same reason as every prior
  script, MT5 always reports `magic=0`), cross-references against all 36 of this run's tickets
  plus the 3 tickets pre-existing from before this run, and reports each as `OPEN position`,
  `PENDING order`, or absent. Places, modifies, and closes nothing — read-only only.
- **Status check result: 27 of 36 tickets already resolved on their own**, before the check even
  ran — closed via their own broker-side SL/TP (grid SELL_LIMITs that filled then closed, runner
  MARKET positions that closed), the same live-triggering-protection evidence Step 15 first
  surfaced, now confirmed at much larger scale. **9 tickets remain live**: 7 pending grid
  BUY_LIMIT orders (`171644242`, `171644332`, `171644504`, `171644689`, `171645070`, `171645204`,
  `171645569`) and 2 open positions (`171645570`, grid SELL; `171645571`, runner BUY).
- **Incidental finding, out of scope**: all 3 tickets pre-existing from before this run
  (`171622543`, `171622789`, `171622791` — confirmed still live as recently as Step 16) were also
  found absent during this check — closed via their own SL/TP sometime since Step 16, not
  investigated further, consistent with this project's repeated "stop expanding scope"
  convention.
- **User decision: leave all 9 remaining live tickets open** — no cleanup performed this step,
  consistent with this project's standing default for real (non-smoke-test) cycles.

```
pytest -q                        -> 346 passed (unchanged since Step 15's commit e812867)
pytest tests/test_architecture.py -q -> 13 passed
```

No production code changed this step (no fix was needed — this step only ran already-committed
code and verified it). **Files changed this step**:
`scripts/run_demo_execution_loop_run_status_check.py` (new), this checkpoint doc.

## Step 18 — magic-filter bug fixed (code only, not yet live-verified)

User approved fixing the root cause confirmed at the end of Step 17 (`ExposureCaps`/duplicate-
order guard both blind due to MT5 always reporting `magic=0`). No live call made this step —
code and tests only, read-only verification of git/process state before starting (latest commit
`8e909c8`, working tree clean, no live process or stop-file present).

**Fix**: `run_grid_cycle()`/`run_runner_cycle()` (`pipeline/grid_cycle.py`,
`pipeline/runner_cycle.py`) now accept an optional `state_store: Optional[StateStore] = None`
parameter, chosen over the doc's other candidate (filtering by `comment` prefix) because
`LocalOrderRecord.magic` already holds the intended value recorded locally at submission time —
no new field or convention needed. Behavior:
- `state_store is None` (the default): **unchanged** from every prior step — calls
  `account.get_positions(symbol, magic=magic)`/`get_orders(symbol, magic=magic)` exactly as
  before. Every mock/dry-run caller (both integration test files,
  `scripts/run_live_dry_run_pipeline.py`) is unaffected by this fix and needed no changes.
- `state_store` supplied: reads `account.get_positions(symbol=symbol)`/`get_orders(symbol=symbol)`
  **unfiltered** (the broker's own `magic` field is never trusted for this project's own tickets),
  then intersects the returned tickets against `{r.ticket for r in state_store.all_open() if
  r.magic == magic}` — recovering "is this mine" from local state instead of the broker's
  always-`0` echo. `open_lots`/`pending_lots`/`check_duplicate_order()`'s input list are all
  computed from that intersection; no change to `check_exposure_cap()`/`check_duplicate_order()`
  themselves, which were already confirmed correct in Step 17's root-cause analysis.

**Wired into all three real-executor call sites** (the only places the `magic=0` quirk actually
bites): `scripts/run_demo_execution_pipeline_cycle.py` (both `run_grid_cycle`/`run_runner_cycle`
calls), `scripts/run_demo_execution_pipeline_loop.py` (`state_store` threaded through
`_run_one_cycle`), `scripts/run_demo_execution_runner_sltp_smoke_test.py` — each already had
`state_store` in scope from `demo_execution_session()`'s existing return tuple, just not passed
through before now.

**+2 regression tests**, the first to reproduce MT5's `magic=0` quirk in a mock at all (previously
impossible to catch, per Step 17's root-cause note): one per pipeline function
(`tests/integration/test_grid_dry_run_pipeline.py::test_state_store_recovers_duplicate_order_visibility_despite_broker_magic_zero`,
`tests/integration/test_runner_dry_run_pipeline.py::test_state_store_recovers_exposure_visibility_despite_broker_magic_zero`).
Each seeds a live position/order with `magic=0` (as real MT5 reports) plus a matching
`LocalOrderRecord` carrying the true magic, and asserts *both* directions in one test: without
`state_store` the guard is still blind (documents the fallback as intentional, not an oversight),
with `state_store` the guard correctly blocks. `pytest -q` → 348 passed (was 346 before this
step); `pytest tests/test_architecture.py -q` → 13 passed, unchanged.

**AGENTS.md updated** with the same summary (commit `2c7b42b`).

**Not yet live-verified**: this fix has only been exercised against mocks. Whether it correctly
discriminates real, populated, differently-magic-tagged live data on the demo account —
including the connected account's own pre-existing tickets, and any residual staleness from
`StateStore` records that went stale via broker-side SL/TP closes (Step 17's separate, still-open
finding) — has not been observed live. Requires its own explicit approval before any live run,
same standing rule as every step since Step 15.

```
pytest -q                        -> 348 passed (was 346 before this step)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed this step**: `src/mt5_mcp_trading/pipeline/grid_cycle.py`,
`src/mt5_mcp_trading/pipeline/runner_cycle.py`, `scripts/run_demo_execution_pipeline_cycle.py`,
`scripts/run_demo_execution_pipeline_loop.py`,
`scripts/run_demo_execution_runner_sltp_smoke_test.py`,
`tests/integration/test_grid_dry_run_pipeline.py`,
`tests/integration/test_runner_dry_run_pipeline.py`, `AGENTS.md`, this checkpoint doc. Committed
as `2c7b42b`.

## Exact next smallest task

**Live testing remains paused — do not resume without explicit approval**, same standing rule as
every step before this one. What's left, roughly in priority order:
1. Live-verify Step 18's fix: confirm the `state_store`-based magic recovery actually
   discriminates correctly against the demo account's real, populated, differently-magic-tagged
   data (grid `71101` vs runner `72101` vs the account's other live tickets) — the exact gap this
   whole effort exists to close. Requires explicit approval before any live call.
2. Decide what to do with the 9 tickets Step 17 left open (7 pending grid BUY_LIMIT orders, 2
   open positions) — currently no action planned, left for a later session/cycle.
3. Read `risk/portfolio_guards.py` to confirm whether `ExposureCaps` is meant to bound pending
   LIMIT-order exposure across cycles, or only open-position exposure — Step 17 observed 12
   consecutive grid cycles submit without any visible cap-driven rejection; now explained by the
   magic-filter bug (Step 18), but worth confirming the guard's own intent reading its code
   directly rather than inferring it from the bug alone.
4. Lower priority, pre-existing: the `all_open()` per-action cost question, and stale local
   `StateStore` records for tickets closed via broker-side SL/TP without an explicit
   `close()`/`cancel()` call (27 from Step 17, plus `171622789` from Step 13) — harmless,
   `local_only` in any future `reconcile()` call, not blocking. Step 18's fix makes `StateStore`
   staleness marginally more load-bearing than before (it's now also read for magic recovery, not
   just reconciliation), worth keeping in mind if this becomes a real problem at scale.

**Continuation prompt for a new session**: "Read AGENTS.md and
docs/PIPELINE_WIRING_CHECKPOINT.md (Step 18 is the most recent entry), confirm git status is
clean at the latest commit, then ask me what to do next — do not run anything live without my
explicit go-ahead first."
