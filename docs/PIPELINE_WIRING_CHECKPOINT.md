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
same standing rule as every step since Step 15. **Live-verified in Step 19** — see below.

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

## Step 19 — Step 18's fix live-verified against real, populated, differently-magic-tagged data

User approved live-verifying Step 18's fix (this step's exact predecessor task). Account state at
the start: 0 live positions/orders on BTCUSD (everything from Step 17 had already closed since
the last session) — read-only-confirmed via a new script,
`scripts/run_demo_execution_magic_filter_fix_verification.py`, which computes the OLD
(broker-side `magic`-filtered) and NEW (Step 18's `state_store`-recovered) results side by side
for both real magics, without ever calling `submit()`/`cancel()`/`close_position()`. An empty
account can't prove discrimination, so real data was needed first.

**GRID cycle (`scripts/run_demo_execution_pipeline_cycle.py`, `STRATEGY="GRID"`) — fully
succeeded**: BUY ticket `171647522` @ 63723.92 and SELL ticket `171647525` @ 63750.01, both
retcode `10009`, both `verified=True`, both protected (LIMIT orders carry SL/TP at placement).

**RUNNER cycle (same script, `STRATEGY="RUNNER"`) — hit the designed failure path, not a new
bug**: the MARKET order itself placed fine (SELL ticket `171647565` @ 63737.56, retcode `10009`),
but the mandatory SL/TP attach (`modify_position`) was rejected — retcode `10016` ("Invalid
stops"), while the tool's own message text claimed success (`'Modify position 171647565 success,
SL at 63753.5, TP at 63705.59, current price 0.0'`) — the exact same live-confirmed
retcode-trust bug already documented for ticket `171617865` (Phase 6 Step 6), recurring here for
the first time since. Correctly raised `SlTpAttachmentFailedError`, left the position
`OPEN_UNPROTECTED`, attempted no automatic retry or close, per the established design.

**Recovery (user-approved, separate action)**: a new narrowly-scoped one-off script,
`scripts/run_demo_execution_close_unprotected_runner_position.py` (same re-verify/abort-if-
mismatched/one-attempt/verify-after pattern as
`scripts/run_demo_execution_close_pipeline_open_items.py`), closed ticket `171647565` —
retcode `10009`, `verified=True`, confirmed absent afterward, local state transitioned to
`CLOSED`.

**The actual verification, re-run after the close** — `scripts/run_demo_execution_magic_filter_fix_verification.py` against the resulting real state (0 positions, 2 live grid pending orders,
both broker-reported `magic=0` as always):

| | OLD (`account.get_positions/get_orders(symbol, magic=magic)`) | NEW (Step 18 fix via `state_store`) |
|---|---|---|
| grid (`magic=71101`) | 0 orders — blind, confirms the bug is still real | **2 orders, `pending_lots=0.02`** — correctly recovers `171647522`/`171647525` |
| runner (`magic=72101`) | 0 orders | **0 orders** — correctly does NOT misattribute grid's tickets, despite runner having 12 historical local records of its own |

This is exactly the bar this whole effort was written against (see Step 17's root-cause note and
Step 18's "not yet live-verified" caveat): the fix recovers real exposure for the magic that
actually owns it, and does not leak it to the other, against real broker data carrying the real
`magic=0` quirk — not a mock. **Step 18's fix is now live-verified.**

**User decision: leave the 2 grid tickets open** (`171647522` BUY, `171647525` SELL) — same
standing default as every prior real (non-smoke-test) cycle, left for a later session/cycle.
`scripts/run_demo_execution_pipeline_cycle.py`'s `STRATEGY` constant was left at `"RUNNER"` (the
last value exercised this step) — reviewed before every run regardless, not a safety-relevant
state.

```
pytest -q                        -> 348 passed (unchanged since Step 18)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed this step**: `scripts/run_demo_execution_magic_filter_fix_verification.py`
(new), `scripts/run_demo_execution_close_unprotected_runner_position.py` (new),
`scripts/run_demo_execution_pipeline_cycle.py` (`STRATEGY` flipped to `"RUNNER"`), this
checkpoint doc, `AGENTS.md`.

## Step 20 — Step 19's 2 grid tickets resolved, account clean

User asked to decide what to do with the 2 grid tickets Step 19 left open (`171647522` BUY,
`171647525` SELL). Re-checked live first, not trusting Step 19's report (state is known to move
between a report and any follow-up action, per this project's own repeated precedent) — via
`scripts/run_demo_execution_magic_filter_fix_verification.py`, re-run read-only: `171647522` was
already **absent** from both live positions and live pending orders (most likely filled then
closed via its own broker-side SL/TP, the same pattern observed repeatedly throughout this
project, e.g. Step 17's 27-of-36), while `171647525` was still a live pending order.

**Decision**: resolve both now rather than leave either open. Reasoning: these existed only as
residue from Step 19's fix-verification cycles, not a real, ongoing strategy decision — no
monitoring loop is running to manage them, so an unmonitored live order serves no purpose and the
cleanest state is to close everything out, matching this project's established end-of-session
"safe stop" pattern (Step 16).

**Action**: new one-off script, `scripts/run_demo_execution_close_step19_grid_tickets.py`, same
re-verify/abort-if-mismatched/one-attempt/verify-after pattern as
`scripts/run_demo_execution_close_pipeline_open_items.py`:
- `171647522`: reconciled via `StateStore.record_closed()` directly (no MCP call — nothing left
  on the broker side to act on). Local state now `CLOSED`.
- `171647525`: cancelled via `McpOrderExecutor.cancel()` — retcode `10009`, `verified=True`,
  confirmed absent afterward. Local state now `CANCELLED`.

**Result: PASSED, both tickets resolved.** Account is now clean: 0 live positions, 0 live pending
orders on BTCUSD.

```
pytest -q                        -> 348 passed (unchanged since Step 18)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed this step**: `scripts/run_demo_execution_close_step19_grid_tickets.py` (new),
this checkpoint doc, `AGENTS.md`.

## Step 21 — confirmed ExposureCaps' own intent: pending orders were always meant to count

User asked to resolve the one open question left from Step 17: whether `ExposureCaps` is meant
to bound pending LIMIT-order exposure across cycles, or only open-position exposure. Read-only —
no code or live state changed this step, `risk/portfolio_guards.py` read directly rather than
inferring intent from the magic-filter bug.

**Answer: pending orders were always meant to count, by explicit design, not by omission.** The
module's own docstring documents the ported legacy formula directly: `vol_all = current open +
pending lots for the symbol/magic`, and `check_exposure_cap()`'s own computation matches it
exactly — `projected_total = open_lots + pending_lots + proposed_volume`, checked against both
`max_open_lots` and `budget_max_lots`. The docstring also documents a deliberate *strengthening*
already made over the legacy version: the legacy check compared only *current* `vol_all` before
seeding a new order (so it could jump from under-cap to over-cap in one order); this project's
version adds `proposed_volume` into the projected total specifically to close that gap. Neither
of these was new information requiring a code change — `check_exposure_cap()` itself was already
confirmed correct in Step 17's original root-cause analysis; this step only confirms the guard's
*own stated intent* matches that implementation, closing the "not yet explained" gap Step 17 left
open.

**Conclusion**: Step 17's 12 straight grid cycles blowing past a 0.06-lot cap was never a gap in
`check_exposure_cap()`'s design — it was always meant to, and does, treat pending orders as real
exposure. It happened purely because the magic-filter bug (fixed and live-verified in Steps
18-19) fed it `open_lots=0.0`/`pending_lots=0.0` regardless of real state, so the guard never
received real numbers to check against in the first place. With that fix live-verified, the
exposure cap should now genuinely protect pending-order exposure too, not just open-position
exposure — nothing further needed here; item 1 of Step 20's open-question list is resolved.

```
pytest -q                        -> 348 passed (unchanged -- no code changed this step)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed this step**: this checkpoint doc, `AGENTS.md` only — no production code, no
scripts, no live call.

## Step 22 — quantified the all_open() per-call cost with a real benchmark

User asked to resolve the other open item from Step 21's list: the `all_open()` per-action cost
question, previously flagged (Phase 7) as "not a real problem at current ticket volumes/cycle
counts" but never actually measured, and noted in Step 18/20 as "marginally more load-bearing
than before" now that `run_grid_cycle()`/`run_runner_cycle()` also call it once per cycle (in
addition to the existing one call per `McpOrderExecutor.submit()`/`cancel()`/`close_position()`
via `_current_posture()`). Read-only research, no code changed — replaced speculation with a real
benchmark, matching this project's own established practice (Phase 7 benchmarked the O(N²) write
fix the same way rather than assuming the fix worked).

**Method**: timed `StateStore.all_open()` directly (no live MCP call involved) — once against the
real `var/order_state` directory as it exists today (57 ticket files, the accumulated residue of
every live step this project has run), and once synthetically at larger scales (100/500/1000/
2000/5000 tickets) in a throwaway temp directory, averaged over repeated calls.

```
REAL var/order_state (57 files): 23.7 ms/call
 100 tickets: 136.7 ms/call
 500 tickets: 635.3 ms/call
1000 tickets: 1394.2 ms/call
2000 tickets: 2658.7 ms/call
5000 tickets: 6244.6 ms/call
```

**Confirms the O(N) documented in `state/store.py`'s module docstring, but with a bigger constant
factor than expected** — roughly ~1.3 ms/ticket-file, almost certainly dominated by per-file
open/read/JSON-parse syscall overhead (Windows filesystem `open()` calls are relatively costly
compared to POSIX). This is notably worse than Phase 7's own write-side benchmark (~3 ms/write
flat, independent of ticket count, because each write only touches its own file) — `all_open()`
is fundamentally different: it touches *every* file, every call, by design, and nothing in this
codebase ever removes or archives a `CLOSED`/`CANCELLED` ticket's file, so the directory only
grows over the life of the project.

**At today's scale (57 files, ~24 ms/call), this remains genuinely negligible** — even a full
real cycle with both strategies submitting now triggers `all_open()` up to 4-5 times (1 each from
`run_grid_cycle()`/`run_runner_cycle()`, plus one per `McpOrderExecutor` action), which is
~100-120 ms of total overhead against a 300-second cycle interval (Step 15/17's loop config) —
nowhere close to mattering. **The growth trajectory is the real finding**: at 1000-2000 tickets
(plausible after weeks of sustained daily loop runs — a single 12-cycle Step 17 run alone created
36 tickets in about an hour), a single `all_open()` call costs 1.4-2.7 seconds, and a real cycle
now makes 4-5 such calls, meaning several seconds of `all_open()` overhead per cycle with no
upper bound in sight — a genuine, evidence-backed problem for sustained/long-term live use, not
merely a theoretical one. Still not blocking for near-term bounded test runs at current ticket
counts.

**Not fixed this step** — a code fix (e.g., caching the loaded set within a single cycle/session
instead of re-reading from disk on every call, or splitting "records I need to check my own
magic's tickets" from "every ticket ever," or periodically archiving resolved tickets out of the
live directory) is a real design decision affecting `StateStore`'s public contract and every
caller listed in `state/store.py`'s own docstring, and deserves its own explicit scoping and
approval rather than being folded into a documentation step. Flagged as the clear next candidate
if/when this project moves toward sustained (not just bounded-test) live operation.

```
pytest -q                        -> 348 passed (unchanged -- no production code changed this step)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed this step**: this checkpoint doc, `AGENTS.md` only — no production code, no
scripts, no live call. (The benchmark itself was run ad hoc via the Python REPL against a
throwaway temp directory and the real `var/order_state` directory, read-only; not saved as a
script since it's a one-time measurement, not a repeatable tool.)

## Step 23 — decided: not fixing all_open()'s cost now

User asked to decide (not just quantify) whether to fix `all_open()`'s per-call cost. Read-only —
no code or live state changed this step, a decision-and-rationale entry only, same shape as Step
16's "safe stop" and Step 21's guard-intent confirmation (neither changed code either).

**Decision: not now.** Reasoning:

1. **Doesn't block anything currently in scope.** The system has only ever run bounded test loops
   (Step 17: 12 cycles, ~56 minutes, 36 tickets). Sustained live operation — the only scenario
   where Step 22's numbers actually bite (roughly 500+ tickets before it's noticeable) — hasn't
   been proposed or approved as a next phase. Nothing on the "exact next smallest task" list
   depends on this being fixed first.
2. **The candidate fixes aren't low-risk, unlike Phase 7's O(N²) write fix.** That fix (one file
   per ticket instead of one big file) was a clean, unambiguous format change — zero behavior
   change for any caller. The three candidates noted in Step 22 are all more invasive:
   - **In-session caching** — the most obvious option, but risks feeding stale reads to the
     exposure-cap and duplicate-order guards, exactly the failure class `AGENTS.md` treats as
     highest-severity ("Never bypass ... duplicate-order ... guards"). A caching bug here
     wouldn't just be slow, it would be *wrong* — the worst possible category for this codebase.
   - **A per-magic secondary index** — a new file/data structure, more moving parts, more surface
     for a subtle bug.
   - **Archiving resolved tickets** — needs a real answer for whether reconciliation or anything
     else still needs to read archived records, which nobody has worked out.
   None of these is a "smallest safe fix"; each is real design work deserving its own scoping, not
   something to bolt on opportunistically onto a documentation step.
3. **Matches this project's own established practice**: don't design for hypothetical future
   requirements, fix things when proven necessary, not before. Step 22 did the "prove it" half
   (real benchmark); this step is the "not necessary yet" half of that same discipline.

**Revisit when**: sustained (not bounded-test) live operation is actually proposed as a next
phase — at that point, pick the fix design deliberately, informed by real usage patterns from
that decision (cycle interval, expected run duration), rather than guessing now.

```
pytest -q                        -> 348 passed (unchanged -- no code changed this step)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed this step**: this checkpoint doc, `AGENTS.md` only — no production code, no
scripts, no live call.

## Step 24 — stale StateStore records: traced (not assumed) harmless, decided not to fix now

User asked to resolve the last item carried forward since Phase 7: whether stale local
`StateStore` records (tickets closed live via their own broker-side SL/TP, never reconciled by
an explicit `close()`/`cancel()` call) are actually harmless, or just assumed to be. Read-only —
no code or live state changed this step; every actual consumer of `ReconciliationReport.local_only`
was traced directly, not re-asserted from memory.

**Traced, not assumed**:
1. `determine_posture()` (`state/policy.py`) never reads `report.local_only` at all — only
   `report.unknown_real` (a *live* ticket with no local record) can produce `MANAGE_ONLY`/
   `BLOCKED`. A stale local record for a ticket that's actually gone cannot degrade posture.
2. `cancel()`/`close_position()`'s `MANAGE_ONLY` gate (`mcp_order_executor.py:454,498`) treats
   `local_only` tickets the same as `matched` — both are "attributable, allowed to act on."
   Attempting to cancel/close an already-gone stale ticket would just fail cleanly at the broker
   (nothing there to act on), never silently do the wrong thing.
3. Steps 18-19's magic-recovery fix (`pipeline/grid_cycle.py`/`runner_cycle.py`) intersects
   `state_store.all_open()`'s tickets with a live, unfiltered read every time — a stale local
   record can never survive that intersection, since the live read is always the final
   authority. Staleness cannot produce phantom exposure in the fix this project just spent
   Steps 18-21 proving correct.

**Quantified today's actual staleness**: of 57 total local ticket files, `all_open()` currently
returns 39 as `OPEN` — but the account is confirmed completely flat (0 live positions/orders,
per Step 20's cleanup). **100% of the currently-"open" local records are stale.** This isn't new
or surprising given this project's own repeated observation (Steps 15, 17) that tickets close
live via their own SL/TP far more often than they're explicitly closed by this project's code —
but it had never actually been measured before.

**Decision: not fixing this now either**, same reasoning class as Step 23:
- Confirmed genuinely harmless for correctness by tracing every actual consumer, not by
  re-asserting the existing "harmless" note.
- The only two ways to "fix" it are (a) automatic reconciliation — writing local state without
  an explicit, human-reviewed call, which goes directly against this project's own repeatedly-
  stated design principle (`state/store.py`'s docstring: "never implicitly trusted"; updates
  happen "only via an explicit call, never automatically") — or (b) manual pruning, which is
  really just a partial, premature attempt at reducing `all_open()`'s file count, the exact cost
  question Step 23 already decided to defer. Fixing this now would either violate an established
  principle or duplicate a decision already made.
- Existing precedent already handles this correctly on demand: one-off, explicitly-approved
  cleanup scripts (Step 17's `run_demo_execution_close_pipeline_open_items.py`, Step 20's
  `run_demo_execution_close_step19_grid_tickets.py`) re-verify live state and reconcile stale
  records via `record_closed()` right before acting — the right threat model for a state layer
  this project has deliberately kept human-gated.

```
pytest -q                        -> 348 passed (unchanged -- no code changed this step)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed this step**: this checkpoint doc, `AGENTS.md` only — no production code, no
scripts, no live call.

## Step 25 — third live loop run: exposure cap confirmed binding for the first time, safe midday stop

User approved a fresh bounded autonomous loop run (Step 24's identified next live milestone), to
confirm the exposure cap and duplicate-order guard now actually bind in a real multi-cycle run —
the exact scenario Step 17 exposed as broken, fixed in Steps 18-19, live-verified only in
isolation (Step 19) until now. Pre-flight, all confirmed fresh: working tree clean, no stale
`var/STOP_PIPELINE_LOOP`, loop config unchanged from Step 15/17 (`SYMBOL="BTCUSD"`,
`GRID_MAGIC=71101`, `RUNNER_MAGIC=72101`, `ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06)`,
5-minute cycle interval, 12-cycle/90-minute ceiling), account confirmed flat (0 live
positions/orders) immediately before launch.

Launched `scripts/run_demo_execution_pipeline_loop.py` live in the background. Mid-run, the user
requested a safe midday stop ahead of a lunch break: no new cycle after the one in progress, no
new orders beyond it, stop via the project's normal stop-file mechanism, then report state —
explicitly not a rushed kill, and explicitly not touching anything beyond the loop's own designed
no-cleanup behavior.

**Result: ran 6 of 12 cycles, then stopped cleanly and exactly as designed** — `touch
var/STOP_PIPELINE_LOOP` created during cycle 6's inter-cycle wait; the loop's poll (every
`POLL_INTERVAL_SECONDS=5s` during the wait) picked it up at 12:09:01, logged `"Stop requested
during inter-cycle wait: stop file present"`, disconnected cleanly, and exited — `"Done. 6
cycle(s) run. No cleanup performed."` No cycle 7 was ever started; nothing was submitted after
cycle 6. Zero `ERROR`-level log lines, no tracebacks, no exceptions, across the entire run (`grep`-
confirmed, not assumed).

**The exposure cap bound for the first time in this project's history, live, in cycles 5 and 6**:
```
[GRID] BTCUSD BUY rejected (portfolio.max_open_lots): open=0, pending=0.05, proposed=0.01,
  projected_total=0.06 exceeds max_open_lots=0.06
[GRID] BTCUSD SELL rejected (portfolio.max_open_lots): open=0, pending=0.05, proposed=0.01,
  projected_total=0.06 exceeds max_open_lots=0.06
```
(cycle 5, and again structurally identically in cycle 6 with `open=0.01, pending=0.04`). This is
the exact scenario Step 17 exposed as silently broken (12 straight grid cycles blew past this
same 0.06 cap unblocked) — now genuinely enforced, live, not a mock. Closes the loop on the
entire magic-filter investigation's original goal.

**Cycle-by-cycle results**:

| Cycle | Grid (magic 71101) | Runner (magic 72101) |
|---|---|---|
| 1 | BUY `171648990`@63784.29, SELL `171648991`@63957.24 — both retcode 10009 | BUY MARKET `171648992`@63957.10, SL/TP attached, retcode 10009 |
| 2 | BUY `171649324`@63822.06, SELL `171649325`@64138.03 — both retcode 10009 | BUY MARKET `171649326`@64141.73, SL/TP attached, retcode 10009 |
| 3 | BUY `171649422`@63859.65, SELL `171649423`@64072.50 — both retcode 10009 | BUY MARKET `171649424`@64068.23, SL/TP attached, retcode 10009 |
| 4 | BUY `171649460`@63893.48, SELL `171649461`@64053.69 — both retcode 10009 | BUY MARKET `171649463`@64053.54, SL/TP attached, retcode 10009 |
| 5 | **Both sides rejected** — `portfolio.max_open_lots` | BUY MARKET `171649552`@63925.44, SL/TP attached, retcode 10009 |
| 6 | **Both sides rejected** — `portfolio.max_open_lots` | SELL MARKET `171649631`@63870.10, SL/TP attached, retcode 10009 |

14 tickets submitted total (8 grid, 6 runner), every one retcode 10009/`verified=True`, every
runner MARKET order's SL/TP attach succeeded on the first attempt (no recurrence of Step 19's
retcode-10016 quirk this run). Runner never signaled FLAT — a directional signal existed every
single cycle.

**Live state re-checked immediately after the stop** (`scripts/
run_demo_execution_magic_filter_fix_verification.py`, read-only): of the 14 tickets this run
created, **8 had already resolved on their own** before the check even ran — closed via their own
broker-side SL/TP (grid SELLs `171648991`/`171649325`/`171649423`; runner positions `171648992`/
`171649326`/`171649424`/`171649463`/`171649552`) — the same live-triggering-protection evidence
this project has observed repeatedly (Steps 15, 17), now at a smaller but still real scale.
**6 tickets remain live, all correctly protected with real SL/TP**:
- 2 open positions: `171649460` (grid BUY, filled from its own pending order) and `171649631`
  (runner SELL).
- 4 pending orders: `171648990`, `171649324`, `171649422` (grid BUY), `171649461` (grid SELL).

**No cleanup performed** — matches the loop's own designed no-cleanup behavior exactly (`AGENTS.md`
safety rule: don't act beyond what's designed/approved). All 6 remaining tickets are protected
(real SL/TP attached at submission), a safe state to leave over a break.

```
pytest -q                        -> 348 passed (unchanged -- no code changed this step)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed this step**: this checkpoint doc, `AGENTS.md` only — no production code, no new
scripts. `var/STOP_PIPELINE_LOOP` and `var/logs/pipeline_loop_20260804T044217Z.log` were created
by this step but are git-ignored (`var/`, `logs/` in `.gitignore`) — never committed.

**Known gotcha for the next launch**: `var/STOP_PIPELINE_LOOP` is still present on disk (not
removed this step, since removal wasn't part of what was asked and leaving it is itself safe — a
future launch would just see it and exit immediately, a safe no-op, not a hazard). It must be
deleted before the loop can run again; otherwise it will connect, log the stop-file, and exit on
its very first check without doing anything. Same gotcha Step 17 hit and documented from Step 15's
shutdown.

## Step 26 — Step 25's 6 leftover tickets: checked live, reconciled

New session. Read `AGENTS.md` and this doc, confirmed working tree clean at `e0f3899` (Step 25's
commit), confirmed no live process running, confirmed `var/STOP_PIPELINE_LOOP` was already absent
(no deletion needed). User asked to check the live state of the 6 tickets Step 25 left open, then
to reconcile the stale local records.

**Live check** (new read-only script, `scripts/run_demo_execution_step25_ticket_status_check.py`,
same pattern as Step 17's status-check script): all 6 tickets (`171649460` grid BUY position,
`171649631` runner SELL position, `171648990`/`171649324`/`171649422` grid BUY pending,
`171649461` grid SELL pending) were already **absent** from both live positions and live pending
orders — 0/6 still live, account fully flat (0 positions, 0 pending orders on BTCUSD, all magics).
Same broker-side-SL/TP-closes-things-on-its-own pattern this project has observed repeatedly
(Steps 9, 15, 17, 20). Local `StateStore` still showed all 6 as `status='OPEN'` (stale, per Step
24's already-traced-harmless finding).

**Reconciliation** (new one-off script,
`scripts/run_demo_execution_step25_reconcile_leftover_tickets.py`, same shape as Step 20's
close-tickets script): re-verified all 6 live-absent immediately before acting (not trusting the
status-check script's report, per this project's standing "state can move between a report and an
action" precedent), then called `StateStore.record_closed()` directly for each — no MCP call for
any of them, since nothing remained on the broker side to act on. **Result: PASSED, all 6
reconciled to `CLOSED`.**

Account confirmed clean: 0 live positions, 0 live pending orders on BTCUSD (all magics).

```
pytest -q                        -> 348 passed (unchanged -- no production code changed this step)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed this step**: `scripts/run_demo_execution_step25_ticket_status_check.py` (new),
`scripts/run_demo_execution_step25_reconcile_leftover_tickets.py` (new), this checkpoint doc,
`AGENTS.md`. No production code changed. No MCP order-affecting call made (`record_closed()` is
local-state-only).

## Step 27 — fourth live loop run: retcode-10016 bug recurred cycle 1, unprotected position recovered

Same new session as Step 26, after the lunch break. User approved relaunching the loop with
unchanged config (`SYMBOL="BTCUSD"`, `GRID_MAGIC=71101`, `RUNNER_MAGIC=72101`,
`ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06)`, 5-min cycle interval, 12-cycle/90-minute
ceilings). Pre-flight: working tree clean, no live process, no stop-file, account confirmed flat
via a fresh read-only check immediately before launch.

**Result: cycle 1 hit the recurring retcode-trust bug; loop correctly stopped itself.**

- **Grid (magic 71101) — both sides succeeded, protected**: ticket `171651878` (BUY_LIMIT @
  63586.21, sl=63573.04, tp=63589.37) and `171651879` (SELL_LIMIT @ 63648.07, sl=63661.24,
  tp=63644.91), both retcode `10009`, `verified=True`.
- **Runner (magic 72101) — SL/TP attach rejected, left `OPEN_UNPROTECTED`**: MARKET SELL placed
  (ticket `171651880`, retcode `10009`), but `modify_position` was rejected — retcode `10016`
  ("Invalid stops") — while the tool's own message text claimed success (`'Modify position
  171651880 success, SL at 63596.23, TP at 63566.61, current price 0.0'`). The exact same
  live-confirmed retcode-trust bug as `171617865` (Phase 6 Step 6) and `171647565` (Step 19),
  recurring a third time. Correctly left `OPEN_UNPROTECTED`, no automatic retry or close
  attempted. `run_runner_cycle()` raised `SlTpAttachmentFailedError`, and the loop stopped itself
  immediately after cycle 1 per its no-error-tolerance design — no cycle 2 was ever attempted.

**Recovery (user-approved, separate action)**: re-confirmed `171651880` still live and unprotected
(`sl=0.0, tp=0.0`) via a fresh read-only check, then a new narrowly-scoped one-off script,
`scripts/run_demo_execution_close_unprotected_runner_position_171651880.py` (same
re-verify/abort-if-mismatched/one-attempt/verify-after pattern as Step 19's equivalent recovery
script), closed it — retcode `10009`, `verified=True`, confirmed absent afterward, local state
`CLOSED`.

**User decision on the 2 remaining grid tickets**: leave both open (`171651878`, `171651879`),
same standing default as every prior real (non-smoke-test) cycle — no cleanup performed on them.

Final state: 0 live positions, 2 live protected pending grid orders on BTCUSD. Loop remains
stopped; no relaunch attempted this step.

```
pytest -q                        -> 348 passed (unchanged -- no production code changed this step)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed this step**:
`scripts/run_demo_execution_close_unprotected_runner_position_171651880.py` (new), this checkpoint
doc, `AGENTS.md`. No production code changed.

**Follow-up, same session**: a later read-only check found `171651878` (grid BUY_LIMIT) already
absent from live positions/orders — most likely filled then closed via its own broker-side SL/TP,
same pattern as ever. `171651879` (grid SELL_LIMIT) was still confirmed live. New one-off script,
`scripts/run_demo_execution_reconcile_171651878.py` (same re-verify-then-`record_closed()`-only
pattern as Step 26's reconciliation script), reconciled `171651878` to `CLOSED` locally — no MCP
call, `171651879` deliberately left untouched (still genuinely live, not stale). Final state: 0
live positions, 1 live protected pending grid order (`171651879`) on BTCUSD.

**Second follow-up, after Step 28**: a later read-only check found `171651879` also now absent
from live positions/orders — same pattern, closed via its own broker-side SL/TP. New one-off
script, `scripts/run_demo_execution_reconcile_171651879.py` (identical pattern), reconciled it to
`CLOSED` locally — no MCP call. Every ticket from Step 27's loop run is now resolved and
reconciled. Final state: 0 live positions, 0 live pending orders on BTCUSD.

## Step 28 — retcode-10016 / "current price 0.0" watch item: root-caused, closed, no fix needed

Same new session as Steps 26-27. User asked to investigate the recurring retcode-10016 pattern
(`171617865` Phase 6 Step 6, `171647565` Step 19, `171651880` Step 27) — read-only research only,
no code change, no live MCP call.

**Root cause of the misleading message, traced to the exact third-party source line**: the
vendored `metatrader_client` package, `metatrader_client/order/send_order.py:272-277` (installed
at `.venv/Lib/site-packages/metatrader_client/order/send_order.py`), the `SLTP`-action branch:

```python
response = mt5.order_send(request)
error_code, error_description = mt5.last_error()
if error_code < 0:
    return {"success": False, ...}
return {"success": True, "message": "Order sent successfully", "data": response}
```

**Why `response["success"]` (and therefore the wrapping `modify_position()`'s own "success"
message) is not trustworthy**: `mt5.last_error()` is the MetaTrader5 *Python package's own
terminal/API-level* error code — it reflects whether the request was well-formed and transmitted
to the terminal, not whether the broker actually accepted the trade. The broker's real decision
lives in `response.retcode` (e.g. `10016`), which this function never inspects at all. So the
library reports `success=True` whenever the call itself went through cleanly, regardless of what
the broker decided — this is "Known Issues item 7", now confirmed at its exact source rather than
inferred from live symptoms.

**The "current price 0.0" text is a red herring, not a clue**: `modify_position.py:70` builds its
success message from `response['data'].price`. For an `SLTP`-action MT5 response (a pure
stop-modification, no trade executed), MT5 never populates a meaningful execution price on that
field — it is structurally `0.0` on *every* SLTP response, success or failure alike. It appeared
in all 3 recorded failures simply because it appears in all SLTP responses; it carries no
diagnostic information about why any particular rejection happened, and does not indicate a stale
or failed price lookup.

**Our executor already handles this correctly — no code change required**: `mcp_order_executor.py`
never trusts the tool's own `success`/`error` field for any call, this one included. It parses the
raw `retcode` directly out of `response.data` via `metatrader_retcodes.parse_trade_response()`,
and additionally requires a fresh, independent live re-read of the position's actual SL/TP to
agree before ever treating an attach as confirmed (see this module's own docstring, points 4 and
"Success is never taken on retcode alone" under `_submit_market()`). All 3 recorded occurrences
were caught correctly, every single time, and the position was correctly left `OPEN_UNPROTECTED`
with no automatic retry or close — this is the existing safeguard working exactly as designed, not
a live gap.

**Retcode `10016` itself is a separate, already-anticipated broker-side rejection — not the trust
bug, and not newly explained by it**: both recent failures (`171647565`'s 15.97 and `171651880`'s
9.87 price-unit stop distances — ~1600/~987 points at BTCUSD's real `point=0.01`)
used real ATR-based distances from `compute_stop_distances()`, nowhere near Phase 6 Step 6's
original too-tight 1.2-unit distance that caused its own separate, already-fixed rejection. 2
rejections out of ~9 real MARKET-order SL/TP attaches since the ATR fix (Step 5) is consistent with
BTCUSD's real minimum-stop-distance (`stops_level`/`freeze_level`) being variable and occasionally
exceeding even a normal ATR-based distance — exactly the scenario this module's own docstring
already anticipated and deliberately chose not to pre-validate locally (`reliable SymbolInfo isn't
available through the current MCP connection path`; see the module docstring's "Broker-side
minimum stop-distance ... is deliberately NOT pre-validated locally"). Nothing found this step
contradicts that decision — if anything, it confirms the existing retcode-trust workaround is
sufficient and was the right call.

**Conclusion: watch item closed, no fix needed.** No production code changed, no live call made.

```
pytest -q                        -> 348 passed (unchanged -- no production code changed this step)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed this step**: this checkpoint doc, `AGENTS.md` only — no production code, no
scripts, no live call.

## Step 29 — fifth live loop run: retcode-10016 recurred a fourth time, recovered and cleaned up

Same session as Step 28, later. User approved relaunching the loop with unchanged config
(`SYMBOL="BTCUSD"`, `GRID_MAGIC=71101`, `RUNNER_MAGIC=72101`,
`ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06)`, 5-min cycle interval, 12-cycle/90-minute
ceilings). Pre-flight: no stop-file, no live process.

**Result: ran 4 of 12 cycles, then stopped itself on another retcode-10016 SL/TP-attach
rejection.**

- Cycles 1-3: both grid sides and the runner MARKET order succeeded every cycle — 6 grid tickets
  (`171652730`/`171652731`, `171652797`/`171652799`, `171652844`/`171652845`) and 3 runner
  positions (`171652732`, `171652801`, `171652846`), all retcode `10009`, all runner SL/TP
  attaches confirmed on the first attempt.
- Cycle 4: grid succeeded again (`171653004`/`171653005`), but runner's MARKET order
  (`171653006`, BUY) hit the same recurring bug root-caused in Step 28 — `modify_position`
  rejected with retcode `10016`, tool message falsely claimed success. Correctly left
  `OPEN_UNPROTECTED`, no auto-remediation; `run_runner_cycle()` raised `SlTpAttachmentFailedError`,
  and the loop stopped itself immediately per its no-error-tolerance design. No cycle 5 attempted.

**Recovery (user-approved, separate action)**: re-confirmed `171653006` still live and unprotected
via a fresh read-only check, then a new one-off script,
`scripts/run_demo_execution_close_unprotected_runner_position_171653006.py` (same
re-verify/abort-if-mismatched/one-attempt/verify-after pattern as every prior recovery script for
this bug), closed it — retcode `10009`, `verified=True`, confirmed absent afterward.

**Cleanup of self-resolved tickets, in two passes** (state moved between checks, consistent with
this project's own repeated precedent — Step 9, Step 15, and now again here):
- First read-only check (right after the `171653006` close) found 4 of the remaining tickets
  already absent from live state: `171652731`, `171652799` (grid SELL, filled then closed via
  their own SL/TP) and `171652801`, `171652846` (runner BUY, closed via their own SL/TP). New
  one-off script, `scripts/run_demo_execution_reconcile_fifth_loop_run_leftovers.py`, reconciled
  all 4 to `CLOSED` locally — no MCP calls.
- That same check incidentally revealed 2 more tickets (`171653005`, `171652845`, both grid SELL
  positions confirmed live moments earlier) had *also* since closed — noticed but out of scope for
  that script, reported separately. User approved reconciling those too; a second new one-off
  script, `scripts/run_demo_execution_reconcile_171653005_171652845.py`, confirmed both absent and
  reconciled them to `CLOSED` — no MCP calls.

**Final state**: 1 live protected position (`171652732`, runner BUY, sl=63579.86/tp=63684.98) and
4 live protected pending grid orders (`171652730`, `171652797`, `171652844`, `171653004`) remain —
left open, no cleanup performed on them, matching this project's standing default for real
(non-smoke-test) cycles. All 6 self-resolved tickets from this run are now reconciled.

```
pytest -q                        -> 348 passed (unchanged -- no production code changed this step)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed this step**:
`scripts/run_demo_execution_close_unprotected_runner_position_171653006.py`,
`scripts/run_demo_execution_reconcile_fifth_loop_run_leftovers.py`,
`scripts/run_demo_execution_reconcile_171653005_171652845.py` (all new), this checkpoint doc,
`AGENTS.md`. No production code changed.

**Follow-up, later session**: user asked for a full read-only reconciliation of the 5 remaining
tickets before deciding what to do with them (ticket/symbol/side/volume/SL/TP/magic/local-state
ownership for each, plus an explanation of the retcode-10016 failure that stopped cycle 4). New
one-off script, `scripts/run_demo_execution_reconcile_step29_remaining_tickets.py`, found all 5
had *also* since self-resolved (absent from live positions/orders, most likely triggered via
their own broker-side SL/TP) — state had moved again since Step 29's report, the same
repeatedly-observed pattern. User approved reconciling them; a second new one-off script,
`scripts/run_demo_execution_reconcile_step29_final_5_tickets.py`, re-verified all 5 absent and
reconciled them to `CLOSED` locally — no MCP calls. **Every ticket from Step 29's loop run is now
resolved.** Final state: 0 live positions, 0 live pending orders on BTCUSD.

```
pytest -q                        -> 348 passed (unchanged -- no production code changed this step)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed this follow-up**:
`scripts/run_demo_execution_reconcile_step29_remaining_tickets.py`,
`scripts/run_demo_execution_reconcile_step29_final_5_tickets.py` (both new), this checkpoint doc,
`AGENTS.md`. No production code changed.

## Exact next smallest task

**Live testing remains paused — do not resume without explicit approval**, same standing rule as
every step before this one.
1. Account is fully clean (0 live positions/orders) — every ticket from Step 29's loop run,
   including the 5 initially left open, has self-resolved via broker-side SL/TP and is now
   reconciled locally. Nothing left over to decide on.
2. The retcode-10016 bug (closed as a watch item in Step 28) recurred a fourth time in Step 29
   (`171653006`) — consistent with Step 28's own conclusion that this is an expected-possible,
   already-handled broker-side rejection, not a code defect. Still no action needed; the existing
   retcode-trust + fresh-live-read workaround caught it correctly again.
3. With the exposure cap confirmed binding live (Step 25) and all residue reconciled through this
   follow-up, the magic-filter investigation that began at Step 17 remains fully closed. Nothing
   further planned here unless something new surfaces.
4. No loop is running and no stop-file is present — a future loop relaunch needs no pre-cleanup
   step right now, but should still check for `var/STOP_PIPELINE_LOOP` first (the gotcha several
   prior steps have hit) since any future stop would leave it behind again.

**Continuation prompt for a new session**: "Read AGENTS.md and
docs/PIPELINE_WIRING_CHECKPOINT.md (Step 29 is the most recent entry), confirm git status is
clean at the latest commit, confirm no live process is running, confirm/delete
`var/STOP_PIPELINE_LOOP` before any loop relaunch, then ask me what to do next — do not run
anything live without my explicit go-ahead first."

## Step 30 — sixth live loop run: end-to-end unattended run, retcode-10016 recurred a fifth time

User approved a full end-to-end run of the bounded autonomous loop with explicit instructions not
to pause for routine interim status checks, only to stop on the loop's own designed conditions
(cycle/runtime limit, error, unprotected position, exposure violation, unknown ownership) and
report a single final summary. Unchanged config (`SYMBOL="BTCUSD"`, `GRID_MAGIC=71101`,
`RUNNER_MAGIC=72101`, `ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06)`, 5-min cycle
interval, 12-cycle/90-minute ceilings). Pre-flight: working tree clean, no stop-file, no live
process, account confirmed flat immediately before launch.

**Result: ran 3 of 12 cycles, then stopped itself on cycle 3's runner leg — the fifth recurrence
of the retcode-10016 SL/TP-attach bug (Step 28's root-caused, already-understood watch item).**

- Cycles 1-2: both grid sides and the runner MARKET order succeeded every cycle — 4 grid tickets
  (`171654091`/`171654092`, `171654190`/`171654191`) and 2 runner SELL positions (`171654093`,
  `171654192`), all retcode `10009`, all SL/TP correctly attached and verified.
- Cycle 3: grid succeeded again (`171654322`/`171654323`), but runner's MARKET SELL order
  (`171654324`) had its mandatory SL/TP attach rejected — retcode `10016`, tool message falsely
  claimed success (`'Modify position 171654324 success, SL at 63510.24, TP at 63459.19, current
  price 0.0'`). Correctly left `OPEN_UNPROTECTED`, no automatic retry or close attempted;
  `run_runner_cycle()` raised `SlTpAttachmentFailedError`, and the loop stopped itself immediately
  per its no-error-tolerance design. No cycle 4 attempted. No exposure-cap violation, no unknown-
  ownership case, no other error — this was the only stop condition triggered.

**Per the approved test design, `171654324` was NOT auto-closed** — recovery for an unprotected
position has always been a separate, explicitly-approved action in this project (never automated,
per `AGENTS.md`'s safety rules and every prior recovery script), and the run's instructions
described stopping on this condition, not auto-remediating it. It remains live and unprotected,
awaiting a separate approval decision.

**Verification and reconciliation** (read-only + local-state-only, one new script,
`scripts/run_demo_execution_reconcile_step30_run.py`): a fresh live read found 3 of the 8
protected tickets already absent (`171654091`, `171654093`, `171654192` — most likely closed via
their own broker-side SL/TP) and reconciled them to `CLOSED` locally, no MCP calls. The remaining
5 protected tickets (`171654092`, `171654190`, `171654191`, `171654322`, `171654323`) are still
live and were left untouched, matching this project's standing "no cleanup" design for real
cycle results. `171654324` (unprotected) was confirmed still live and was not touched.

**Final live state**: 1 unprotected position (`171654324`), 5 protected pending grid orders
(`171654092`, `171654190`, `171654191`, `171654322`, `171654323`). No pre-existing tickets were
touched (there were none — account was confirmed flat before launch).

```
pytest -q                        -> 348 passed (unchanged -- no production code changed this step)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed this step**: `scripts/run_demo_execution_reconcile_step30_run.py` (new), this
checkpoint doc, `AGENTS.md`. No production code changed.

**Recovery, same session**: user approved closing `171654324`. New one-off script,
`scripts/run_demo_execution_close_unprotected_runner_position_171654324.py` (same
re-verify/abort-if-mismatched/one-attempt/verify-after pattern as every prior recovery script for
this bug), closed it — retcode `10009`, `verified=True`, confirmed absent afterward, local state
`CLOSED`. Account now holds 0 unprotected positions.

```
pytest -q                        -> 348 passed (unchanged -- no production code changed this step)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed this recovery**:
`scripts/run_demo_execution_close_unprotected_runner_position_171654324.py` (new), this checkpoint
doc, `AGENTS.md`. No production code changed.

## Exact next smallest task

**Live testing remains paused — do not resume without explicit approval**, same standing rule as
every step before this one.
1. The unprotected-position risk from Step 30 is resolved — `171654324` closed and verified
   absent. Nothing urgent outstanding.
2. 5 protected pending grid orders remain live (`171654092`, `171654190`, `171654191`,
   `171654322`, `171654323`) — left open per this project's standing default, no urgency, all
   carry real SL/TP.
3. The retcode-10016 bug (closed as a watch item in Step 28) has now recurred a fifth time
   (`171654324`) — still consistent with Step 28's conclusion (expected-possible broker-side
   stops_level rejection, not a code defect, already correctly handled by the existing
   retcode-trust + fresh-live-read workaround). No new action needed on the bug itself.
4. No loop is running and no stop-file is present — a future loop relaunch needs no pre-cleanup
   step right now, but should still check for `var/STOP_PIPELINE_LOOP` first (the gotcha several
   prior steps have hit) since any future stop would leave it behind again.

**Continuation prompt for a new session**: "Read AGENTS.md and
docs/PIPELINE_WIRING_CHECKPOINT.md (Step 30 is the most recent entry), confirm git status is
clean at the latest commit, confirm no live process is running, confirm/delete
`var/STOP_PIPELINE_LOOP` before any loop relaunch, then ask me what to do next — do not run
anything live without my explicit go-ahead first. Note: ticket 171654324 is a real, unprotected
live position awaiting an explicit recovery decision."

## Step 31 — end-of-day safe stop, final confirmation

Same session as Step 30. User approved closing `171654324` (see Step 30's recovery entry above —
retcode `10009`, verified absent). User then manually cancelled all 5 remaining pending grid
orders (`171654092`, `171654190`, `171654191`, `171654322`, `171654323`) directly in the MT5
terminal, outside this project's code, and asked for a pure read-only reconciliation first (no
writes), then approved reconciling the resulting stale local records, then called time on the
session and asked for a final safe-stop close-out — no new task, phase, live test, loop, or
MT5/MCP action.

**Reconciliation of the manual cancellation**: a pure read-only check confirmed all 5 tickets
absent from live positions/orders but still `OPEN` locally (no `cancel()` call was ever made by
this project for any of them, since the user cancelled them directly). New one-off script,
`scripts/run_demo_execution_reconcile_manual_cancel_5_grid_orders.py`, re-verified all 5
live-absent immediately before acting, then reconciled each via `StateStore.record_cancelled()`
(the semantically correct terminal status for a cancelled pending order, as opposed to
`record_closed()` for a filled-then-closed position) — no MCP calls. All 5 confirmed `CANCELLED`.

**Final safe-stop confirmation, independently re-verified**:
- **No processes running** — confirmed via process listing, no Python process of any kind active
  (loop, MCP server, or otherwise).
- **No stop-file present** — `var/STOP_PIPELINE_LOOP` absent (the loop was never running this
  session to begin with; it exited on its own after Step 30's cycle 3, and Step 30's relaunch
  was never followed by another launch).
- **Live state re-checked fresh**: 0 live positions, 0 live pending orders on BTCUSD (all magics)
  — ground truth confirmed via `scripts/run_demo_execution_magic_filter_fix_verification.py`,
  immediately after the reconciliation above, not trusting the reconciliation script's own report
  alone.
- **Local state fully reconciled**: every ticket touched this session (Step 26 through this step)
  now has a terminal status (`CLOSED` or `CANCELLED`) matching its real, confirmed-absent live
  state. No open local records remain for anything this session created.

```
pytest -q                        -> 348 passed (unchanged since Step 30 -- no production code
                                     changed this session)
pytest tests/test_architecture.py -q -> 13 passed
```

**No code changed this step** — safe-stop confirmation and end-of-day reconciliation only.

**Files changed this step**:
`scripts/run_demo_execution_readonly_check_5_grid_orders.py`,
`scripts/run_demo_execution_reconcile_manual_cancel_5_grid_orders.py` (both new), this checkpoint
doc, `AGENTS.md`. No production code changed.

## Session summary (Steps 26-31): today's work

**Features and fixes completed**: none new this session — all production code (grid/runner SL/TP,
the magic-filter fix, the loop itself) was already complete and committed before today. This
session was entirely live-testing, verification, cleanup, and one root-cause investigation.

**Demo tests performed**: three more bounded-autonomous-loop live runs (the project's 4th, 5th,
and 6th total), each on the real demo account, each user-approved individually:
- **Step 27** (4th run): 1 cycle completed before the retcode-10016 bug recurred (3rd time
  overall) on `171651880`; recovered on approval; both grid tickets from that cycle later
  self-resolved and were reconciled.
- **Step 29** (5th run): 4 cycles completed before the retcode-10016 bug recurred (4th time) on
  `171653006`; recovered on approval; all 6 remaining tickets from that run later self-resolved
  and were reconciled across two follow-up checks.
- **Step 30** (6th run): a full end-to-end unattended run (no interim status pings, per explicit
  instruction) — 3 cycles completed before the retcode-10016 bug recurred (5th time) on
  `171654324`; recovered on approval. The 5 remaining protected tickets were left open per
  standing design, later manually cancelled by the user directly in MT5, and reconciled locally
  this step (Step 31).

**Important findings**:
- **Step 28**: root-caused the recurring retcode-10016 "false success" message to its exact
  source — the vendored `metatrader_client` package's `send_order()` SLTP branch
  (`.venv/Lib/site-packages/metatrader_client/order/send_order.py:272-277`), which determines
  `success` from `mt5.last_error()` (a terminal/API-level code) instead of the broker's real
  `response.retcode`. The "current price 0.0" text in the message is a red herring — structurally
  `0.0` on every SLTP response, not diagnostic. Confirmed this project's own executor already
  handles it correctly (parses retcode directly, requires a fresh live re-read before trusting an
  attach) — watch item closed as a known, no-fix-needed, already-mitigated broker-side rejection
  class, not a code defect.
- The retcode-10016 bug recurred 3 more times today (Steps 27, 29, 30) after being closed as a
  watch item in Step 28 — every single occurrence was caught correctly by the existing safeguard,
  with zero silent failures and zero unmanaged risk left behind after recovery. This is now
  evidence, not just theory, that the mitigation holds under repeated real-world recurrence.
- Every "leftover ticket" check this session found live state had moved further than the last
  report by the time a follow-up ran — the same "state moves between a report and an action"
  pattern this project has observed since Step 9, now reconfirmed at even higher frequency (grid
  LIMIT orders and runner MARKET positions both routinely self-resolve via their own broker-side
  SL/TP within minutes).

**Errors or safety stops encountered**: 3 (Steps 27, 29, 30), all the same already-understood
retcode-10016 SL/TP-attach rejection, all handled exactly as designed — position correctly left
`OPEN_UNPROTECTED`, loop correctly stopped itself, no automatic retry or close ever attempted, no
other error type occurred (no exposure-cap violation, no unknown-ownership case, no dropped
connection).

**Final MT5 and repository state**: account fully clean — 0 live positions, 0 live pending orders
on BTCUSD (all magics), independently re-verified this step. Every ticket created or touched today
is reconciled to a terminal local status matching live reality. Repository: working tree clean,
all changes committed and pushed to `origin/master`, no uncommitted work.

## Remaining roadmap

- **Phase 7 (regression and failure testing)**: closed by user decision (see Phase 7's own
  checkpoint doc), but one slice was explicitly never done and remains open if resumed: **no
  live/MCP-adjacent failure testing** (e.g. actually killing the MCP subprocess mid-call, forcing
  a dropped connection) — everything in Phase 7 itself was pure/mock-based. The pipeline-wiring
  effort's own loop runs have since observed *organic* connection stability (no drop has ever
  occurred across 6 live runs), but a *deliberate* forced-disconnect test, proving the loop's
  documented "a dropped connection is fatal, no reconnect logic" behavior actually behaves as
  described, has still never been exercised.
- **Phase 8 (not yet started, not yet scoped in this repo)**: strategy research and demo
  performance tuning. No formal definition exists yet in `AGENTS.md`'s phase list (which currently
  only defines phases 0-7) — this would be new scope, not a continuation of an existing checkpoint
  doc. Likely candidates based on what today's runs surfaced: tuning `sl_atr_mult`/`tp_atr_mult`
  for both strategies against real observed fill/SL-TP-trigger behavior (today's data shows most
  positions self-resolve within single-digit minutes — worth understanding whether that reflects
  genuine signal quality or just current market volatility), and revisiting the grid step-size/TP
  formula now that real fill data exists across 6 live runs.
- **Phase 9 (not yet started, not yet scoped in this repo)**: locked demo forward testing and a
  live-readiness gate. Also undefined in `AGENTS.md` today. Conceptually the natural endpoint of
  this whole pipeline-wiring effort — a longer, less-supervised demo run (days, not single
  bounded-loop sessions) with an explicit, objective gate (e.g. N consecutive clean cycles, a
  maximum acceptable retcode-10016 recurrence rate, a stability/uptime bar) before ever
  considering anything beyond `DEMO_EXECUTION` mode. Nothing about this project's `no LIVE mode
  exists in this codebase` boundary (`AGENTS.md`, "Execution modes") is being questioned or
  proposed to change — this phase would still be entirely on the demo account.

Both Phase 8 and Phase 9 are the user's own forward framing from this instruction, not yet written
into `AGENTS.md`'s phase list or given their own checkpoint docs — that formalization is itself a
future task, not done here (per "Do not begin tomorrow's work today").

## Tomorrow's smallest safe task

Recommended order, smallest/lowest-risk first:
1. **Nothing urgent is outstanding** — account is clean, all state reconciled, no open recovery
   items. Tomorrow can start from a genuinely clean slate.
2. If continuing the live-testing thread: the next smallest live action is another bounded loop
   run (7th total) — no new decision needed, same conservative config, same established recovery
   pattern if retcode-10016 recurs again. Needs fresh explicit approval, per this project's
   standing rule (a prior day's approval never carries over).
3. If shifting toward hardening instead: the deliberate live/MCP-adjacent failure test flagged
   above (forced MCP subprocess kill mid-cycle) is the smallest well-scoped Phase-7-adjacent gap
   remaining — pure engineering, no live order risk beyond what any other loop run already
   carries, and it directly tests a documented-but-never-proven behavior (fatal-on-disconnect).
4. If shifting toward Phase 8/9 definition: the smallest safe first step is purely
   documentation — formalizing what Phase 8 and Phase 9 actually mean in `AGENTS.md`'s phase list
   (today's roadmap section above is a first draft, not a committed definition) — before any
   strategy-tuning code or extended forward-test script is written.

No single one of these is prescribed — this is a menu for tomorrow's first explicit decision, not
a plan already put in motion.

## Step 32 — MCP disconnect/timeout testing, Stage 1 + Stage 2 (mock/stub-only, no live call)

New session, roadmap-review-driven. Before any of this, confirmed fresh: latest commit `df8db05`
(Step 31), working tree clean, no live process, no stop-file, 348 tests passing. A full roadmap
review (Phase 0-9 + live-pilot) found Phase 7's live/MCP-adjacent failure testing — flagged as
never done since Step 14/Phase 7's own checkpoint — was the correct smallest task to close before
considering Phase 8, since every phase-8 candidate sits on top of an execution/reconciliation
layer whose real disconnect behavior had only ever been *inferred* (six loop runs that happened
not to drop), never *forced and observed*. User approved scoping it in three stages (see AGENTS.md
"Forward phases") and then explicitly approved building Stage 1 + Stage 2 only, with five required
behaviors to prove and Stage 3 (real subprocess/MT5) held for separate approval.

**Stage 1 — `tests/integration/test_mcp_client_disconnect.py` + `tests/integration/_stub_mcp_server.py`
(new)**: a throwaway stub stdio MCP server (`FastMCP`, one `sleep_forever` tool, no MT5/dotenv/
credential import anywhere) is spawned via the real `McpClient` exactly as production does, then
hard-killed (`os.kill`, which TerminateProcess()s unconditionally on Windows) while a call is
genuinely in flight. Before writing any assertion, ran an exploratory throwaway script (not
committed) to observe real behavior first rather than guess it — found `McpClient.call_tool()`
raises `mcp.shared.exceptions.McpError('Connection closed')` in ~0.02s, confirmed via
`McpError.__mro__` to be a plain `Exception` subclass, not a `BaseExceptionGroup`/
`asyncio.CancelledError` (a real, specifically-considered risk with anyio task-group-based
transports — a disconnect propagating as a bare cancellation would silently defeat every
`except Exception` in this codebase). Three tests, all passing: the disconnect surfaces well
under the 30s `McpCallTimeoutError` bound and as a plain catchable `Exception`, `McpClient.__aexit__`
cleanup after the kill doesn't hang, and a second `call_tool()` on the same now-dead session also
fails fast rather than hanging.

**Stage 2 — `tests/integration/test_pipeline_loop_disconnect.py` (new)**: loads
`scripts/run_demo_execution_pipeline_loop.py` via `importlib.util.spec_from_file_location()` so
its `_run_one_cycle()` can be called directly without ever executing that script's `main()` (where
`load_dotenv()`/`load_settings()`/`demo_execution_session()` live — confirmed by reading the
script that everything credential/connection-adjacent is inside `main()`'s `if __name__ ==
"__main__":` guard, never triggered by `exec_module()`). Injects the exact real exception found in
Stage 1 (`McpError(ErrorData(code=0, message="Connection closed"))`, not a generic stand-in) via a
`_DisconnectingExecutor` wrapping `DryRunExecutor`, against mock market data/account. Four tests,
all passing: a grid-side disconnect and a runner-side disconnect both make `_run_one_cycle()`
return `False` rather than raise past its own boundary; a pre-existing `StateStore` record survives
a failed cycle completely unchanged (no corruption, no partial/leaked record for the failed
attempt); and driving `main()`'s own exact two-line stop check (`if not ok: break`) against two
real sequential `_run_one_cycle()` calls proves a second cycle's executor (a `_PoisonExecutor` that
raises `AssertionError` if ever touched) is never invoked once the first cycle fails.

**Finding: no code fix was needed.** The existing blanket `except Exception` in
`_run_one_cycle()` already handles the real disconnect exception shape correctly — Stage 1/2
answer "is the documented fatal-on-disconnect behavior actually true" with a verified yes, for the
mock/stub-reachable slice of that question, not new functionality.

**Explicitly still open at the end of Stage 1/2**:
1. No real disconnect has been forced against the actual demo-connected subprocess (only a
   throwaway stub with no MT5 involved) — Stage 1/2 prove `McpClient`'s and `_run_one_cycle()`'s
   *mechanics* are sound, not that a real production disconnect looks identical (plausible, since
   nothing here is metatrader-mcp-server-specific, but not yet observed).
2. The 30s `McpCallTimeoutError` path had never fired for real — only unit-tested against a fake
   session (`tests/unit/test_mcp_client.py`, pre-existing) and, in Stage 1's disconnect test,
   superseded by a much faster `McpError` before the timeout could ever matter. **Closed by Part
   1, below, same session.**
3. The "ambiguous in-flight" case is still entirely untested and can only ever be resolved live:
   `McpOrderExecutor.submit()` calls `state_store.record_submission()` only *after* its MCP call
   returns (`mcp_order_executor.py:233-262` LIMIT, `:287-315` MARKET) — so a call that raises
   before returning is never recorded locally, meaning a real disconnect at the exact moment the
   broker accepts an order but the response is lost would leave a genuinely unknown ticket with no
   local trace at all. No mock can manufacture this; it requires a real broker. Still open.

```
pytest -q                        -> 355 passed (348 previously + 7 new)
pytest tests/test_architecture.py -q -> 13 passed
```

No live/MCP/MT5 call was made anywhere in this step. No credentials or `.env` were read. No
production code changed (no fix was needed).

**Files changed this step**: `tests/integration/_stub_mcp_server.py` (new),
`tests/integration/test_mcp_client_disconnect.py` (new),
`tests/integration/test_pipeline_loop_disconnect.py` (new), `AGENTS.md`, this checkpoint doc.

## Step 32 addendum — Stage 3 scoped, Part 1 built (still mock/stub-only, no live call)

Same session. Before scoping Stage 3, re-read `scripts/run_metatrader_mcp_stdio.py` and found a
real difference from Stage 1's stub worth flagging before any live-adjacent design: the real MCP
server is not a single process. The wrapper (`run_metatrader_mcp_stdio.py`, what `McpClient`
actually spawns) launches `metatrader_mcp_extended_server.py` as a *nested* child via
`subprocess.run()`, not `exec`. Stage 1's stub was a single flat process, so killing it the way
Stage 1 did would, against the real server, risk orphaning the MT5-connected grandchild — left
running invisibly, holding a real connection, not reaped by anything. Any live-adjacent Stage 3
test has to tree-kill (`taskkill /F /T /PID`, not a plain `os.kill`) and verify nothing is left
behind, or it would create exactly the kind of untracked live state this whole project works to
avoid.

Scoped Stage 3 into three parts of very different risk (not one): **Part 1** (real 30s timeout
firing — turns out this needs no live component at all, just the existing stub sleeping longer
than the timeout instead of being killed); **Part 2** (real subprocess/process-tree disconnect,
read-only call only — needs real demo credentials to launch the real wrapper, zero order risk);
**Part 3** (the "ambiguous in-flight order" case — needs a real order, highest risk, recommended
to defer/decide separately rather than bundle into a single approval). User approved building
Part 1 only.

**Part 1 — `tests/integration/test_mcp_client_disconnect.py`, +1 test**: reused Stage 1's stub
unchanged, added `test_a_real_slow_call_raises_mcp_call_timeout_error_via_a_real_pipe`. The stub
process is told to `sleep_forever(seconds=5.0)` and is **never killed** — the client is
constructed with a short `call_timeout_seconds=1.0` so the test stays fast while still exercising
`McpClient`'s real `asyncio.wait_for()` against a real subprocess/pipe (not the unit test's fake
in-memory session, which has no pipe at all). **Result: PASSED.** `McpCallTimeoutError` raised
with the correct `tool_name`/`timeout_seconds`, well before the stub's own 5s sleep would have
completed on its own (confirms the timeout — not the tool finishing — is what ended the call), and
cleanup of the still-alive, mid-sleep child (`__aexit__`) completed without hanging — a scenario
none of Stage 1's other tests exercised, since every one of those killed the process outright.

This closes item 2 of the "still open" list above. Items 1 and 3 remain open — Part 2 and Part 3
of Stage 3, both live-adjacent, neither started, both need their own separate explicit approval.

```
pytest -q                        -> 356 passed (355 previously + 1 new)
pytest tests/test_architecture.py -q -> 13 passed
```

No live/MCP/MT5 call was made. No credentials or `.env` were read (Part 1 stayed on the same
stub as Stage 1). No production code changed.

**Files changed this addendum**: `tests/integration/test_mcp_client_disconnect.py` (modified,
+1 test), `AGENTS.md`, this checkpoint doc.

## Step 32 addendum 2 — Stage 3 Part 2 scoped and written, deliberately NOT run

Same session. User approved scoping Stage 3 Part 2 (real subprocess/process-tree disconnect,
read-only call only), then approved building the script but explicitly held back running it —
two separate approvals, matching this project's own established practice of writing vs. running a
real script being two distinct decision points.

**Design decisions made while scoping, before writing anything**:
- Goes through the real `demo_execution_session()` (not a bespoke `McpClient` hookup) — the
  actual composition root every live script uses, including `require_demo_account_kind()`'s hard
  gate. `trading_enabled=True` is unavoidable (a property of `mode=DEMO_EXECUTION`, not an
  independent flag — `config/settings.py`) but poses no incremental risk: no `TRADING`-classified
  tool is ever called, `executor`/`state_store` from the session tuple are unpacked and
  immediately discarded (`del executor, state_store`), and `ToolRegistry` still gates any
  trading-tool call regardless.
- The real server is two nested processes, not one: `run_metatrader_mcp_stdio.py` (what
  `McpClient` actually spawns) launches `metatrader_mcp_extended_server.py` as a child via
  `subprocess.run()` with *inherited*, not proxied, stdio. Killing only the outer wrapper is not
  guaranteed to sever the pipe or take the grandchild with it — found while re-reading the
  wrapper script during scoping, not assumed. Design: tree-kill (`taskkill /F /T /PID`) as the
  primary mechanism, followed by an explicit re-scan confirming both PIDs are gone, with a direct
  fallback kill of either individually if the tree-kill somehow missed one.
- Belongs in `scripts/`, not `tests/`: Stage 1/2 are `pytest` tests specifically because they're
  side-effect-free — a routine `pytest -q` (run constantly throughout this whole session as a
  safety check) must never silently dial into the real demo account. Confirmed `pyproject.toml`'s
  `testpaths = ["tests"]` already excludes `scripts/` from collection, so this can't regress that
  guarantee even by accident.

**Built**: `scripts/run_demo_execution_mcp_disconnect_smoke_test.py`. Five steps: (1) one baseline
`get_account_info` call, to prove the real connection genuinely works before anything is broken;
(2) identify the script's own newly-spawned wrapper process by diffing a `Get-CimInstance
Win32_Process` snapshot taken immediately before/after connecting (never a raw command-line
substring match alone -- see the concrete finding below); (3) race a second call against a
tree-kill (best-effort, not guaranteed -- a real account read is typically sub-second, unlike
Stage 1's fully controllable stub sleep; if the call completes anyway, that's informational, not
a failure); (4) re-verify process cleanup, force-killing individually if either PID somehow
survived the tree-kill; (5) the one deterministic assertion -- a call made against the
now-confirmed-dead connection must fail the same clean way Stage 1 found (`McpError`, fast, a
plain `Exception`, not `BaseExceptionGroup`), not hang. A `finally` block in `main()` re-kills
anything still matching this run's own recorded PIDs regardless of how the script exits, as a
last safety net.

**Concrete finding while building, not just a theoretical concern**: tested the process-finding
helper (`_find_processes()`) in isolation (read-only `Get-CimInstance` query, no MT5, no kill) and
found it falsely matched the *test shell itself* -- the inline `python -c "..."` command used to
run the test happened to contain the literal string `"run_metatrader_mcp_stdio.py"` inside its own
source text (because that string was being passed as an argument to the function under test),
which a raw substring match against `CommandLine` matched. This directly validates the
before/after diffing design (the false positive would already have been in the "before" snapshot,
so it would never appear as "new") rather than proving it unnecessary. Added one further
strengthening after finding this: the diffed wrapper process's command line must also contain the
exact resolved Python executable path (`str(PYTHON)`) used to launch it, a second independent
fact under this script's own control, reducing the residual risk of a coincidental new unrelated
process matching by substring alone.

**Verified, read-only, no live call**: the script compiles (`py_compile`), imports cleanly via
the same `importlib` technique Stage 2 used (module-level code is only constant/function
definitions -- confirmed nothing runs at import time), and `_find_processes()` itself was
exercised standalone (Win32_Process queries only, no kill, no MT5) to confirm the PowerShell/JSON
parsing actually works, not just that it looks right. `pytest -q` still 356 passed (this script
lives in `scripts/`, outside `testpaths`, so it was never collected).

**Deliberately not run yet at the end of this addendum.** Writing it made no live call; running it
is a separate action, same standing rule as every other real script in this project's history, and
needed its own explicit go-ahead. **Run in the next addendum, below.**

**Files changed this addendum**: `scripts/run_demo_execution_mcp_disconnect_smoke_test.py` (new,
not yet run), `AGENTS.md`, this checkpoint doc.

## Step 32 addendum 3 — Stage 3 Part 2 live-verified: real disconnect confirmed, venv-stub root cause found and fixed

Same session. User approved running the script. Pre-flight: working tree clean except the new
script, no stray processes, no stop-file.

**Run 1: aborted safely.** Baseline `get_account_info` succeeded (`balance=9982.39`, `trade_mode`
field-inversion quirk present as always documented). The diff found **2 new wrapper processes and
2 new extended-server processes** instead of 1 of each — the script's own safety check correctly
refused to guess and aborted before touching anything, exactly as designed. Read-only re-check
immediately after: all 4 processes had already exited on their own; nothing to clean up. Considered
possible causes (a concurrent connection from this project's own Claude Code integration, per
`mcp_adapter/client.py`'s own docstring noting it uses the same wrapper) but found no supporting
evidence in this project's or the user's global Claude config.

**Run 2 (after user asked to run again without changes): identical outcome.** Same 2-and-2 shape.
Two identical results in a row pointed at something systematic, not a one-off race, so user asked
for full command-line diagnostics before a third blind retry.

**Diagnostic logging added** (`_format_procs()`, logged unconditionally for baseline/diff every
run, embedded directly in abort exception messages, not just PIDs) — **Run 3 revealed the real
cause immediately**: the two "new wrapper" PIDs were not independent — one's `ParentProcessId` was
the other. Full chain: wrapper stub (`.venv\Scripts\python.exe run_metatrader_mcp_stdio.py`) →
its child, the real wrapper (`miniconda3\python.exe run_metatrader_mcp_stdio.py`, identical
args) → extended-server stub (`.venv\Scripts\python.exe metatrader_mcp_extended_server.py`) →
its child, the real extended server (`miniconda3\python.exe`, identical args). **Root-caused, not
guessed**: `.venv/Scripts/python.exe` is a ~235KB CPython venv launcher stub (confirmed via file
size, and `.venv/pyvenv.cfg`: `home = C:\Users\PC\miniconda3`, `executable =
C:\Users\PC\miniconda3\python.exe`) that spawns the base interpreter as a genuine child OS process
rather than exec'ing in place — a structural property of this machine's Python install, happening
identically at every level of the spawn chain (both the wrapper and the extended server), not a
second connection from anywhere.

**Fixed**: replaced the "exactly 1 new PID per marker" validation with a single-connected-tree
validation — collect all new wrapper + extended-server PIDs together, find the process(es) whose
`ParentProcessId` is NOT itself in that combined set (the root(s)), require exactly 1 root, require
that root to be a wrapper-marker match whose command line contains the exact Python executable
path used, require at least one extended-server descendant exists. A genuinely unrelated second
connection would still produce a second, disconnected root and still correctly abort — this only
stops treating the *same machine's own venv-stub doubling* as ambiguous. Tree-kill now always
targets the single root (`taskkill /F /T` already recurses through however many re-exec layers
exist beneath it); cleanup verification and the `finally` safety net both now track the whole
confirmed tree (`results["_new_tree_pids_for_cleanup"]`, replacing the old two-key wrapper/server
split, which no longer maps cleanly onto a variable-depth tree).

**Run 4 (after the fix): PASSED, full success.**
- Baseline call OK.
- Tree correctly identified: 4 PIDs (`268 → 4256 → 412 → 7880`), single root confirmed, root's
  command line matched, one extended-server descendant confirmed.
- Root tree-killed (`taskkill /F /T /PID 268`).
- Cleanup re-scan: **0 orphans** — all 4 PIDs confirmed gone, re-verified again independently
  read-only immediately after the script exited.
- Step 3's mid-flight race did **not** land this run (`raced_call_outcome: "completed before the
  kill could reach it (timing)"`) — the documented best-effort limitation (a real account read is
  typically sub-second, unlike Stage 1's fully controllable stub sleep), informational, not a
  failure.
- **Step 5's deterministic assertion succeeded**: a call made against the now-confirmed-dead
  connection raised `anyio.ClosedResourceError` in 0.00s — confirmed via
  `post_kill_call_is_exception_subclass: True` / `post_kill_call_is_exception_group: False` — a
  plain `Exception` subtype, not `BaseExceptionGroup`/`CancelledError`, not hanging. This is a
  *different concrete exception class* than Stage 1's stub finding
  (`mcp.shared.exceptions.McpError`) — worth recording precisely rather than assuming they'd
  match: both are equally safe by the property that actually matters (clean `Exception` subtype,
  fast, never escaping as `BaseException`), and nothing anywhere in this codebase's failure
  handling checks for a specific exception class, only `except Exception` — so this doesn't change
  any existing behavior, it just replaces an assumption with a direct observation.

No order, no symbol, no `executor` call was made at any point across all four runs — fully
read-only throughout, exactly as designed. `pytest -q` unaffected (356 passed; this script is
outside `testpaths`).

```
pytest -q                        -> 356 passed (unchanged -- script lives outside testpaths)
pytest tests/test_architecture.py -q -> 13 passed
```

**This closes Stage 3 Part 2.** Only Stage 3 Part 3 (the "ambiguous in-flight order" case) remains
open in the whole MCP disconnect/timeout testing effort, and it needs a real order to test —
recommended, same as originally scoped, to decide separately rather than bundle into a single
approval.

**Files changed this addendum**: `scripts/run_demo_execution_mcp_disconnect_smoke_test.py`
(modified — tree-based validation, diagnostic logging), `AGENTS.md`, this checkpoint doc.

## Exact next smallest task

**Live testing remains paused — do not resume without explicit approval**, same standing rule as
every step before this one. Account is fully clean, nothing outstanding.

1. **Stage 3 Parts 1 and 2 are both done and live-verified** (Part 1: real 30s timeout, mock/stub-
   only; Part 2: real disconnect against the actual demo-connected subprocess, process-tree
   cleanup confirmed clean). Only **Part 3** remains — the "ambiguous in-flight order" case, which
   needs a real order and is the highest-risk piece of this whole effort; recommended to decide
   separately, with a deliberately minimal-risk design (e.g. a far-from-market, unfillable LIMIT
   order) if ever pursued, rather than treated as a default next step.
2. Otherwise, per the roadmap review, Phase 8 (strategy research/tuning) remains explicitly not
   started, pending either Stage 3 Part 3 or an explicit decision to accept the remaining gap as an
   open risk and proceed anyway.

**Continuation prompt for a new session**: "Read AGENTS.md and
docs/PIPELINE_WIRING_CHECKPOINT.md (Step 32 addendum 3 is the most recent entry), confirm git
status is clean at the latest commit, confirm no live process is running, confirm the demo
account is clean (no positions, no pending orders), confirm live testing is still paused, then
ask me what to do next — do not run anything live without my explicit go-ahead first. Stage 3
Parts 1 and 2 (real 30s timeout; real subprocess/process-tree disconnect) are both done and
live-verified. Only Part 3 (ambiguous in-flight order, needs a real order) remains, scoped but not
started."
