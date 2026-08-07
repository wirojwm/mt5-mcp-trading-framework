# Checkpoint: Phase 9 (demo forward test, performance monitoring, drawdown/risk gates) — proposal, not yet started

One of this project's originally-numbered phases (`AGENTS.md`'s "Forward phases (named, not yet
scoped)"), scoped here for the first time. Per `AGENTS.md`'s required workflow ("explain the goal,
list files to create/change, identify risks and assumptions — before editing... stop and wait for
explicit approval before starting the next phase"). **Nothing in this doc has been built. This is
the proposal, for review before any code.**

## Motivation

Phase 8's tuning/validation work is functionally complete (edge metric decided, cost sensitivity
measured, parameters tuned and walk-forward validated, runner's `sl_atr_mult=3.0`/`tp_atr_mult=6.0`
adopted as the production default and live-verified). The grid regime filter follow-on effort
(`docs/GRID_REGIME_FILTER_CHECKPOINT.md`), motivated by Phase 8 Step 7's regime-analysis finding,
is now closed as a negative result — neither candidate it produced survived out-of-sample
validation.

**Both strategies still show negative expectancy on held-out data** — grid −0.311 R (filter-off
baseline), runner −0.100 R (with the adopted `sl_atr_mult=3.0` default). Phase 9 must be framed
honestly against that backdrop: **this is not a "run it forward and hope it's profitable"
exercise.** Its purpose is two distinct, useful things regardless of whether either strategy is
ever adopted for real money:
1. Prove the *operational* machinery (locked parameters, a real loss-based circuit breaker,
   performance monitoring, sustained-run reliability) actually works under real, longer-duration
   demo conditions — something never yet tested at this project's largest scale so far (the
   longest run to date, Step 30 of the pipeline-wiring effort, was 3 of 12 bounded cycles before a
   known retcode-10016 recurrence stopped it; every prior run has been short, human-launched, and
   bounded to ~12 cycles / 90 minutes).
2. Measure whether real forward performance is even in the neighborhood of the backtested numbers
   — a distinct question from "is the edge positive," and one pure backtesting can never answer
   (real fill behavior, real slippage accumulation over time, real broker quirks like the
   recurring retcode-10016 bug, none of which a bar-replay engine can fully reproduce).

Separately, `AGENTS.md`'s Live pilot entry already flags a **hard blocker**: no daily-loss-limit or
kill-switch code exists anywhere in this codebase (`risk/__init__.py` documents this gap
explicitly; `pipeline/loop_control.py`'s `LoopLimits`/`should_stop()` bounds cycles and wall-clock
time only, never realized loss). That phase cannot begin until this is written and tested — Phase
9 is where that gets built, since a sustained forward test is the first context that actually
needs it.

## Design

Five components, each independently useful, in increasing order of risk:

1. **A locked parameter set** — one documented, frozen combination of
   `GridStrategyConfig`/`RunnerStrategyConfig`/`ExposureCaps`, not edited during the test window
   without its own new, explicitly-approved effort. Proposed: freeze current production defaults
   exactly as they stand today (see "Open design points" below) — Phase 9 tests operational
   behavior around already-tuned parameters, it does not re-tune them.

2. **A real daily-loss/drawdown kill-switch** — a new risk gate (e.g.
   `risk/daily_loss_guard.py`, same independent/composable shape as `symbol_guards.py`/
   `portfolio_guards.py`, folded into `combine.py` alongside the existing guards so no guard is
   ever skippable by another passing) that halts new submissions once realized loss over some
   window breaches a configured threshold. This is a genuinely new risk category — every existing
   guard (`check_exposure_cap`, `check_duplicate_order`, `check_position_limit`) bounds *position
   size*, never *realized P&L* — so it needs its own design, not a variant of an existing guard.

3. **A live performance/drawdown monitor** — reuses `backtest/metrics.py`'s existing
   `expectancy_r()`/`max_drawdown_r()`/`has_minimum_sample()` (already pure, already tested)
   fed from real `StateStore` records plus a live closed-trade read, instead of a backtest
   ledger, so a running forward test's real R-multiple expectancy/drawdown can be checked at any
   point without guessing or waiting for a manual reconciliation.

4. **Operational reliability hardening for sustained runs** — revisit two decisions this project
   has already flagged and deliberately deferred until "sustained live operation is actually
   proposed" (which Phase 9 now is):
   - `StateStore.all_open()`'s O(N) full-directory scan on every guard check (quantified in the
     pipeline-wiring effort: ~1.3 ms/ticket-file, negligible today, a real cost at larger scale or
     longer duration).
   - The pipeline loop's "one long-lived connection, no reconnect, a drop is fatal" decision
     (explicitly v1-only; Stage 3 Part 3 of the Phase 7 disconnect-testing effort already found
     every *other* disconnect scenario provably safe — this would only be about whether to
     *recover* from one automatically, not about safety).

5. **Demo-to-live readiness criteria** — an explicit, objective checklist (a documentation
   artifact, not code) that must be satisfied before the Live pilot phase can even be scoped:
   e.g. a minimum forward-test duration/trade count, the kill-switch proven to trigger correctly
   at least once against a real run, zero unresolved `OPEN_UNPROTECTED` incidents left behind, and
   the backtest-vs-forward expectancy drift characterized (not necessarily positive — just
   understood, honestly).

## Open design points, decided provisionally here — flag if you'd rather choose differently

1. **Loss unit for the kill-switch**: proposed **raw account-currency $, not R-multiples or %
   equity** — the simplest, most legible unit a human can directly cross-check against the MT5
   terminal UI in real time, and consistent with how `ExposureCaps` is already expressed in raw
   lots rather than R.
2. **Kill-switch scope**: proposed to **stop the whole loop**, mirroring
   `pipeline/loop_control.py`'s existing "stop immediately, no partial-tolerance" philosophy
   (decision 3 in `run_demo_execution_pipeline_loop.py`'s own docstring) — a loss-limit breach is
   at least as serious as an unhandled cycle error, which already stops the loop outright. Not
   proposing a new intermediate "block new orders only, keep managing existing ones" state for v1.
3. **Initial threshold value**: proposed to derive a conservative starting number from already-
   measured backtested drawdown figures (grid held-out max drawdown 14.240 R, runner held-out
   62.999 R, both at the fixed 0.01-lot sizing every script already uses) converted to a dollar
   figure via that same sizing — then treat it as adjustable, not a final answer. Exact conversion
   math to be worked out in Step 2, not fixed here.
4. **Locked parameter set contents**: proposed to be exactly current production defaults —
   `GridStrategyConfig(step_mult=0.4, sl_atr_mult=2.0, atr_period=14, center_ema_period=50,
   min_step_points=10.0, max_entry_efficiency_ratio=None)`,
   `RunnerStrategyConfig(fast=12, slow=26, sl_atr_mult=3.0, tp_atr_mult=6.0, atr_period=14,
   min_stop_distance_points=10.0, max_concurrent_positions=1)`,
   `ExposureCaps(max_open_lots=0.06, budget_max_lots=0.06)`, fixed `0.01` lot sizing — i.e. exactly
   what `run_demo_execution_pipeline_loop.py` already runs today, with no changes. Phase 9 adds a
   kill-switch and monitoring around this, it does not re-tune it.
5. **Duration/scope of the actual forward-test run**: deliberately **not decided in this doc** —
   the single biggest live-adjacent open question here, to be scoped as its own explicitly-approved
   step (Step 7 below) only after Steps 1–6 give real evidence to size it against, and only with a
   fresh go-ahead per this project's standing live-testing-pause rule.

## Proposed steps, smallest/lowest-risk first

| Step | Scope | Entry criteria | Exit criteria | Key risk |
|---|---|---|---|---|
| 1 | Document the locked parameter set (open point 4) as a single source of truth; no code | This doc reviewed/approved | A committed doc/table of frozen params, referenced by later steps | None — pure documentation |
| 2 | Build the loss-based kill-switch (`risk/daily_loss_guard.py` or similar), unit tested against synthetic P&L data; **not wired into the live loop yet** | Step 1 done | New guard + tests merged; not yet reachable from any live script | Threshold choice is initially a judgment call — flagged as adjustable, not final |
| 3 | Wire the kill-switch into the loop (`pipeline/loop_control.py`'s `should_stop()` or equivalent), unit + integration tested against mocks/`DryRunExecutor` only | Step 2 done | Loop correctly stops on a real (mocked/injected) breach; every existing stop condition (stop-file/max_cycles/max_runtime) still works, proven by test, not assumption | Must not weaken or reorder any existing stop condition |
| 4 | Live performance/drawdown monitor script, read-only, computed from real `StateStore` + one live account read | Step 1 done (can run in parallel with 2–3) | A script reporting real R-multiple expectancy/drawdown for whatever's currently in `StateStore`, zero order/trading calls | Must not misattribute another magic's trades — reuse the existing magic-recovery fix (`state_store` cross-reference), not `get_positions(magic=...)`'s known-broken client-side filter |
| 5 | A single, short, explicitly-approved live smoke test proving the wired kill-switch actually halts a REAL loop run when triggered (a deliberately tiny, easily-crossed threshold, not the real production one) | Steps 2–4 done, fresh live-testing go-ahead (standing pause rule) | One real observed trigger event, loop halted, verified via live re-read, zero leftover unmanaged risk | The one live/order-adjacent step in this list — its own separate go-ahead, same discipline as every past live step |
| 6 | Define demo-to-live readiness criteria (documentation only, informed by Steps 1–5's real findings) | Step 5 done | An explicit, objective checklist doc | None — pure decision/documentation |
| 7 (only if scoped later) | The actual sustained forward-test run itself, monitored per Step 4, bounded by Step 2–3's kill-switch | Step 6 done, its own separate explicit go-ahead | An honest report: real forward performance vs. backtested expectations, any incidents, readiness-criteria scorecard | The longest-duration live-adjacent exposure this project would ever run; its own explicitly-scoped/approved effort, not automatic |

## Explicitly not in this effort

- No change to grid/runner strategy logic, signal generation, or SL/TP formulas — Phase 8 already
  tuned and validated those; Phase 9 tests operational behavior around already-locked parameters.
- Does not itself constitute or authorize the Live pilot phase. Step 6's readiness criteria are an
  input to a future, separately-scoped Live pilot proposal, not an automatic green light — real
  money is never at stake anywhere in this effort.
- No margin guards or spread filters (also flagged missing in `risk/__init__.py`) — explicitly out
  of scope here. The daily-loss kill-switch is this phase's only new risk gate; revisit the others
  if and when Live pilot scoping finds them necessary.
- Step 7 (the actual sustained forward-test run) is not started or pre-authorized by this doc
  alone — its own explicitly-approved step, same discipline as every phase before it.
- Does not change anything about live-testing being paused. Steps 1–4 are pure code/docs and touch
  no real demo call; Step 5 and Step 7 are the only steps that do, and each needs its own fresh
  go-ahead per this project's standing rule.

## Step 1 — locked parameter set: done

Pure documentation, no code changed, no live/demo call of any kind. Per open design point 4,
locked to exactly today's production defaults — verified by reading each source file directly
(not assumed from this doc's own earlier draft), so this table is the single source of truth for
every later Phase 9 step, superseding open design point 4's proposal text above if the two ever
appear to differ.

**Strategy parameters** (`src/mt5_mcp_trading/strategy/grid.py`,
`src/mt5_mcp_trading/strategy/runner.py` — both dataclass defaults, unmodified by this step):

| `GridStrategyConfig` field | Locked value |
|---|---|
| `atr_period` | `14` |
| `center_ema_period` | `50` |
| `center_mode` | `"ema"` |
| `step_mult` | `0.4` |
| `min_step_points` | `10.0` |
| `sl_atr_mult` | `2.0` |
| `max_entry_efficiency_ratio` | `None` (regime filter off — see `docs/GRID_REGIME_FILTER_CHECKPOINT.md`, closed as a negative result) |
| `efficiency_ratio_period` | `14` |

| `RunnerStrategyConfig` field | Locked value |
|---|---|
| `fast` | `12` |
| `slow` | `26` |
| `min_bars_floor` | `30` |
| `atr_period` | `14` |
| `sl_atr_mult` | `3.0` |
| `tp_atr_mult` | `6.0` |
| `min_stop_distance_points` | `10.0` |
| `max_concurrent_positions` | `1` |

**Sizing, risk, and run parameters** (`src/mt5_mcp_trading/sizing/money.py`'s `MoneyConfig`
default, `src/mt5_mcp_trading/risk/portfolio_guards.py`'s `ExposureCaps`, and
`scripts/run_demo_execution_pipeline_loop.py`'s own module-level constants — the exact script
this project has already run live multiple times, confirmed by reading it directly):

| Parameter | Locked value | Source |
|---|---|---|
| Lot sizing | `MoneyConfig()` default → `lot_size_mode="fixed"`, `fixed_lot=0.01` | `sizing/money.py` |
| `ExposureCaps.max_open_lots` | `0.06` | `run_demo_execution_pipeline_loop.py` |
| `ExposureCaps.budget_max_lots` | `0.06` | `run_demo_execution_pipeline_loop.py` |
| Symbol | `BTCUSD` | `run_demo_execution_pipeline_loop.py` |
| Timeframe | `M1` | `run_demo_execution_pipeline_loop.py` |
| Grid magic | `71101` | `run_demo_execution_pipeline_loop.py` (registered in `state/strategy_registry.py`) |
| Runner magic | `72101` | `run_demo_execution_pipeline_loop.py` |
| Cycle interval | `300.0` s (5 min) | `run_demo_execution_pipeline_loop.py` |

**`MAX_CYCLES`/`MAX_RUNTIME_MINUTES` (currently `12`/`90.0` in the same script) are deliberately
NOT locked here** — those are the loop's own time/count safety ceiling, not a strategy or sizing
parameter, and Step 7 (the actual forward-test run) will need its own, likely much larger, values
sized to whatever duration gets separately approved at that point. Locking them now would be
guessing ahead of evidence this effort doesn't have yet.

Nothing above changes any file — this step exists purely to freeze-and-cite what's already true in
the codebase today, so Steps 2–7 have one unambiguous reference point rather than each re-deriving
it. If any of these values ever change for a real (separately-approved) reason before Step 7 runs,
this table must be updated in the same commit, not left stale.

```
pytest -q -> unaffected (no code changed this entry)
```

**Files changed this entry**: this checkpoint doc only.

## Step 2 — loss-based kill-switch guard: built, unit tested only, NOT wired anywhere

New `src/mt5_mcp_trading/risk/daily_loss_guard.py` — same independent/composable shape as
`portfolio_guards.py`/`symbol_guards.py` (returns `RiskDecision`, `combine()`-compatible), pure
(no adapter imports; `tests/test_architecture.py`'s `PURE_PACKAGES` check covers `risk/`
automatically and still passes). No legacy precedent — genuinely new, project-original code,
closing the hard blocker `AGENTS.md`'s Live pilot entry has flagged since before Phase 9 existed.

**Two pieces, matching the checkpoint's Design section exactly**:
- `DailyLossLimitConfig(max_daily_loss: Optional[float] = None, reset_hour_utc: int = 0)` — `$`
  unit (open design point 1), `None` means off, matching every other Optional guard field in this
  codebase.
- `check_daily_loss_limit(realized_pnl_since_reset, limit) -> RiskDecision` — **TRIGGER**: rejects
  once `realized_pnl_since_reset` is a genuine loss (`< 0`) whose magnitude is **at or beyond**
  `max_daily_loss`. Deliberately at-or-beyond, not strictly-beyond like `check_exposure_cap()`'s
  "exactly at cap is not a violation" convention — this is a safety-critical, stop-loss-shaped
  gate (reaching the limit exactly must trip it), documented explicitly in the module docstring so
  the difference from the existing convention reads as a deliberate choice, not an inconsistency.
- `daily_reset_boundary(now, reset_hour_utc=0) -> datetime` — **RESET**: one canonical, tested
  definition of "start of the current loss-tracking window" (most recent `reset_hour_utc:00 UTC`
  at or before `now`), so Step 3's live wiring and any future monitoring script agree on the same
  boundary rather than each reimplementing "start of day" independently. Requires a
  timezone-aware `now` (rejects naive datetimes) and converts non-UTC timezones correctly.

**A real bug caught by the tests, not shipped**: the first implementation used
`pnl <= -max_daily_loss` as the trigger condition. At `max_daily_loss=0.0` (the most conservative
possible setting) this misfired on an exact `$0.00` breakeven — `0.0 <= -0.0` is `True` in
floating point, so a session with literally no loss at all would have tripped the kill-switch.
Fixed by requiring a genuine loss (`realized_pnl_since_reset < 0`) before comparing magnitude —
`test_zero_max_daily_loss_still_approves_breakeven_or_profit` failed against the original
implementation and passes now, the same "prove the fix actually fixes it" discipline this project
has used throughout (e.g. the grid regime filter's normalized-SL/TP regression test).

**18 new unit tests** (`tests/unit/test_risk_daily_loss_guard.py`), synthetic P&L values and
hand-constructed datetimes only — no adapter, no MCP/MT5 call, no order of any kind: the trigger
boundary (profit / just-under / exactly-at / over the limit), the zero-threshold edge case above,
invalid config (`max_daily_loss < 0`) rejected, the reset-boundary function's own boundary cases
(exactly at the reset hour, one second before it, a non-UTC timezone converted, naive-datetime and
invalid-hour rejection), and one explicit `combine()` interop test proving this guard's
`RiskDecision` composes with an existing guard's (`check_exposure_cap`) exactly like any other —
without touching `pipeline/grid_cycle.py`, `pipeline/runner_cycle.py`, or
`pipeline/loop_control.py` at all.

**Confirmed NOT wired anywhere**: `grep -rl "daily_loss_guard"` across the whole repo returns only
the new module and its own test file — no pipeline script, `loop_control.py`, or live script
references it yet, exactly as scoped. Wiring is Step 3, a separate, later, explicitly-approved
step.

```
pytest -q                        -> 446 passed (428 previously + 18 new)
pytest tests/test_architecture.py -q -> 13 passed
```

No order, no live/demo call of any kind this entry.

**Risks / open items carried forward, not resolved by this step**:
- The actual dollar threshold for a real forward test is still undecided (open design point 3) —
  Step 2 built the mechanism, not the number.
- This guard cannot compute `realized_pnl_since_reset` itself (architecturally, `risk/` may never
  touch an adapter) — Step 3/4 must supply that number from a real `StateStore`/account read, and
  getting that computation right (correct magic filtering, no double-counting, correct window) is
  real remaining work, not a formality.
- `reset_hour_utc`'s default (`0`, UTC midnight) is a placeholder, not yet reviewed against any
  operational preference (e.g. aligning to broker's own server-day rollover).

**Files changed this entry**: `src/mt5_mcp_trading/risk/daily_loss_guard.py` (new),
`tests/unit/test_risk_daily_loss_guard.py` (new, +18), this checkpoint doc.

## Step 3 — wire the kill-switch into loop_control.py: done, NOT wired into the live script

**`pipeline/loop_control.py`'s `should_stop()`** gained a new optional
`daily_loss_decision: Optional[RiskDecision] = None` parameter. Precedence (documented in the
updated docstring): stop-file first (unchanged — an explicit human request always wins), then a
daily-loss-limit breach (new — a real-money safety stop, so it outranks the administrative
`max_cycles`/`max_runtime_seconds` ceilings, though never the stop-file above it), then
`max_cycles`, then `max_runtime_seconds`. `daily_loss_decision` defaults to `None`, so every
pre-Step-3 caller is byte-for-bit unaffected unless it explicitly opts in — same convention as
every other Optional guard field in this codebase.

**Deliberately did NOT touch `scripts/run_demo_execution_pipeline_loop.py` itself.** That script
has two real `should_stop()` call sites (`main()`'s per-cycle check, `_wait_for_next_cycle()`'s
during-wait check) — neither was changed, and neither passes `daily_loss_decision`, so the real
script's live behavior is completely unaffected by this step. This was a deliberate scope
decision, not an oversight: the script has no real source for `realized_pnl_since_reset` yet
(flagged as a Step 2 "risk carried forward"), and wiring the call site now would force either a
fake/stub P&L value (misleading) or building the real `StateStore`-based P&L aggregation out of
the step table's own order (that's Step 4's job — "live performance/drawdown monitor... computed
from real `StateStore` + one live account read", the natural place to build the same computation
the kill-switch will eventually need). Wiring the actual live script's call sites to a real
decision is deferred to whichever of Step 4/5 first has a real number to supply.

**Unit tests** (`tests/unit/test_pipeline_loop_control.py`, +8): the existing 10 tests are
unmodified and still pass unchanged (backward-compatibility proof); new tests cover
`daily_loss_decision=None`/omitted (no behavior change), an approved decision (never stops),
a breach (stops, with the reason text included), precedence against stop-file/`max_cycles`/
`max_runtime` in both directions, and one test feeding the REAL
`risk.daily_loss_guard.check_daily_loss_limit()` output straight into `should_stop()` rather than
a hand-built `RiskDecision`, proving actual interop, not just type compatibility.

**Integration test** (new `tests/integration/test_pipeline_loop_daily_loss_stop.py`, +4): proves
the wiring actually halts a real multi-cycle run driven against the live script's own
`_run_one_cycle()` — not just that the pure function returns the right string in isolation. Reuses
`test_pipeline_loop_disconnect.py`'s already-proven harness (`_load_loop_module()`,
`_market_data()`, `_account()`, `_runner_bars()`, `_PoisonExecutor`) rather than duplicating it —
same `DryRunExecutor`/mock-only shape, no `McpClient`, no subprocess, no MT5, no credentials, no
`.env`, no live/trading call anywhere. A new `_drive_loop()` test helper mirrors `main()`'s real
while-loop shape (`should_stop()` checked BEFORE each cycle, cycle counter incremented only after
that check passes) with `daily_loss_decision` now wired in — a test-only harness proving what the
real script's future wiring would do, not new production code. Four cases: no breach (all cycles
run), a mid-run breach (cycles before it run for real against `DryRunExecutor`, a `_PoisonExecutor`
proves the next cycle's executor is never touched), a breach on the very first check (zero cycles
run), and the real `check_daily_loss_limit()` feeding a realistic per-cycle P&L sequence into the
stop decision end-to-end.

**A real fixture bug caught along the way, not shipped**: the integration test's first draft used
`_grid_bars()` (16 bars) for all cases, which made `run_runner_cycle()` raise
(`ValueError: runner_signal requires at least 31 bars`) on every single cycle regardless of any
daily-loss logic — every test "passed" for the wrong reason (`_run_one_cycle()` returning `False`
from the runner-side exception, not from a loss-limit breach). Caught by checking actual
`cycles_run` counts against expectations rather than trusting green tests at face value; fixed by
switching to `_runner_bars()` (40 bars, already used by `test_pipeline_loop_disconnect.py`'s own
runner-focused tests for the same reason).

```
pytest -q                        -> 458 passed (446 previously + 8 loop_control + 4 integration)
pytest tests/test_architecture.py -q -> 13 passed
```

No order, no live/demo call of any kind this entry. `git status` confirms
`scripts/run_demo_execution_pipeline_loop.py` was not touched.

**Risks / open items carried forward, not resolved by this step**:
- The real script still cannot actually stop on a loss breach — only the decision *mechanism* is
  wired and proven; the live script's own call sites remain unmodified by design (see above).
- `realized_pnl_since_reset` still has no real computation anywhere — Step 4 is where that gets
  built, and only once it exists does wiring the live script's call sites become honest to do.
- The real dollar threshold and `reset_hour_utc` value remain undecided (carried forward from
  Step 2).

**Files changed this entry**: `src/mt5_mcp_trading/pipeline/loop_control.py` (modified),
`tests/unit/test_pipeline_loop_control.py` (modified, +8),
`tests/integration/test_pipeline_loop_daily_loss_stop.py` (new, +4), this checkpoint doc.

## Step 4 — IN PROGRESS (research only, no code written, stopped for a lunch break)

Read-only research into how to get real closed-trade P&L, confirmed by reading source (this
project's own established practice), not guessed. **No file was created or modified this entry —
research only.** Findings, so the next session doesn't have to re-derive them:

- The `get_deals` MCP tool (already classified `READ_ONLY` in `mcp_adapter/metatrader_tools.py`,
  never yet called anywhere in this codebase) returns **CSV text** (`metatrader_mcp/server.py`:
  `df.to_csv()`), the same shape `get_positions_with_magic`/`get_pending_orders_with_magic`
  already return and already parse via `mt5_adapter/metatrader_parsing.py`'s
  `parse_dataframe_csv()` — that existing helper should be reused as-is, not reinvented.
- Real per-deal fields, confirmed via `metatrader_client/client_history.py`'s own docstring:
  `ticket`, `time`, `type`, `entry` (0=in, 1=out, 2=inout), `symbol`, `volume`, `price`, `profit`,
  `commission`, `swap`, `fee`, `magic`, `position_id`, `order`, `comment`.
- **`position_id` is the join key back to `LocalOrderRecord.ticket`** — confirmed, not assumed:
  `metatrader_client/order/close_position.py` already treats the ticket this project's
  `McpOrderExecutor.close_position(ticket)` passes as a `position_id` (`position_id = int(id)`),
  so `StateStore`'s own `ticket` concept already IS MT5's `position_id` elsewhere in this
  codebase; a deal's `position_id` should match it directly.
- **`deal.magic`'s reliability is UNCONFIRMED, must not be trusted directly** — this project has
  already confirmed `magic=0` on every position it places (the reason
  `get_positions_with_magic`/the magic-recovery `state_store` cross-reference fix exist at all);
  whether the same broker/terminal quirk also affects deal history has never been tested. Plan:
  attribute every deal to a strategy via `StateStore`'s locally-recorded magic (matched by
  `position_id`/ticket), exactly like the existing magic-recovery fix does for
  positions/orders — never via `deal.magic` directly. This is precisely the checkpoint's own
  standing caution ("must not misattribute another magic's trades... reuse the existing
  magic-recovery fix").
- **A real (harmless, third-party) bug found along the way, not fixed** (it's vendored,
  out-of-repo code, and doesn't affect this project): `metatrader_client/client_history.py`'s
  `MT5History.get_deals()` **class method** has a dead early return —
  `return f"from_date: ${from_date}, ..."` sits BEFORE its real `return get_deals(...)` call,
  so calling `client.history.get_deals()` directly would silently return a debug string, never
  real data. Irrelevant here because the actual MCP tool calls
  `client.history.get_deals_as_dataframe()` instead, which correctly delegates to the real
  module-level `get_deals()` function via a different import path — confirmed by reading both
  files, not assumed safe. Flagged so nothing in this project ever calls the broken class method
  directly if a future `mt5_adapter` addition is tempted to use `metatrader_client` more
  directly.

**Planned shape (not yet built)**: a new `Deal` domain model (`domain/models.py`, matching
`PositionState`/`OrderState`'s style); `StateStore.all_closed()` (mirrors `all_open()`, filters
`status == "CLOSED"` — today only `all_open()` exists, and `record_closed()` itself never even
receives a P&L value, confirmed by reading `state/store.py` fully); a new
`mt5_adapter/mcp_deal_history.py` reader (reusing `parse_dataframe_csv()`, same defensive
`int(float(row[...]))`-style coercion as `mcp_account.py`); a new pure
`monitoring/live_performance.py` that joins `StateStore` closed records to real deals by
`position_id`, sums `profit+commission+swap+fee` per position for real $ P&L, derives
`price_close` from the OUT-entry deal's `price`, and constructs genuine
`backtest.ledger.ClosedTrade` instances (`r_multiple` computed with the exact same
`signed_pnl / risk` formula the backtest engine already uses, so live and backtested R-multiples
stay comparable) — feeding straight into `backtest/metrics.py`'s existing, already-tested
`expectancy_r()`/`max_drawdown_r()`/`has_minimum_sample()` unchanged, per the checkpoint's own
Design section. Note: for live data, `ClosedTrade.notional_pnl` would hold a REAL, broker-confirmed
dollar figure (unlike the backtest engine's own explicitly-uncalibrated use of that same field) —
must be documented clearly wherever this is built, not left as a silent mismatch with that field's
existing docstring. A `realized_pnl_since(deals, since, position_ids)` function (filtered by a
caller-supplied, `StateStore`-derived set of trusted position IDs, never by `deal.magic`) is the
natural, final piece feeding Step 2's kill-switch its still-missing real input. Finally, a
read-only monitor script following this project's established one-real-call pattern (like the
`SymbolInfo`/historical-data probes) — `demo_execution_session()`, never touching `executor`.

**Stopped here deliberately, for a lunch break, per explicit instruction** — mid-research, before
any file was written, so there is nothing to roll back and nothing partially built. **No live/demo
call was made at any point in this research** (everything above came from reading already-installed
package source on disk, never from calling the MCP server). Next session picks up by actually
building the pieces above, still entirely offline/unit-tested first (matching Steps 2–3's own
discipline) — the "one live account read" the original Step 4 proposal describes remains its own
explicit-go-ahead moment, not assumed to be pre-approved by this research.

**`StateStore.all_closed()` built and unit tested** (`src/mt5_mcp_trading/state/store.py`) — mirrors
`all_open()` exactly: one filter over `_load_all()`, same full-directory `StateLoadError`
hard-stop-on-corruption guarantee. Filters `status == "CLOSED"` only — `CANCELLED` is deliberately
excluded (an order that never filled has no closed-trade P&L for the live performance monitor to
join against real deals; `OPEN`/`OPEN_UNPROTECTED` are excluded because they're still `all_open()`'s
domain). No adapter import, no live/demo call — purely local, same as every other `StateStore`
method. 6 new assertions added across existing tests in `tests/unit/test_state_store.py` (cold
start returns `()`; `record_closed` populates it and excludes it from `all_open()`; `record_cancelled`
confirms it stays excluded from `all_closed()`; `OPEN_UNPROTECTED` confirms the same; the
multi-ticket independence test and the 200-ticket at-scale sweep both now check `all_closed()`
alongside `all_open()`; the corrupted-file test confirms `all_closed()` hard-stops on a bad ticket
file exactly like `all_open()` does) — no new test functions, since every scenario `all_closed()`
needs already had an `all_open()`-checking test in place to extend.

```
pytest -q                        -> 458 passed (unchanged total -- new assertions in existing tests, no new test functions)
pytest tests/test_architecture.py -q -> 13 passed
```

No order, no live/demo call of any kind this entry. `git status` confirms nothing outside
`src/mt5_mcp_trading/state/store.py`, `tests/unit/test_state_store.py`, and this checkpoint doc
was touched.

**Files changed this entry**: `src/mt5_mcp_trading/state/store.py` (modified, +`all_closed()`),
`tests/unit/test_state_store.py` (modified, +6 assertions in existing tests), this checkpoint doc.

**`Deal` domain model built and unit tested** (`src/mt5_mcp_trading/domain/models.py`) — same
frozen-dataclass style as `PositionState`/`OrderState`, in the pure `domain/` package (no adapter
import; `tests/test_architecture.py`'s `PURE_PACKAGES` check covers it automatically and still
passes). Carries the full real field set confirmed by reading
`metatrader_client/client_history.py`'s own docstring in Step 4's research (`ticket`, `order`,
`position_id`, `time`, `type`, `entry`, `symbol`, `volume`, `price`, `profit`, `commission`,
`swap`, `fee`, `magic`, `comment`) — nothing invented, nothing dropped. Two fields deliberately
kept less specific than a first pass might reach for, both explained in the class docstring so the
reasoning travels with the code: `type` stays a raw `str` rather than narrowed to `Side`, since a
deal can represent a non-trade event (e.g. a balance operation) and no live capture has yet
confirmed the full value set; `entry` stays a plain `int` rather than a `Literal[0, 1, 2]`, since
MT5's real `ENUM_DEAL_ENTRY` additionally defines `3=out_by` (a hedging-account concept,
unconfirmed either way against this project's netting-mode demo account). The docstring also
carries the two load-bearing caveats from Step 4's research forward onto the type itself, not just
the checkpoint doc: `position_id` is the confirmed join key back to `LocalOrderRecord.ticket`;
`magic` is present on the wire but its reliability is UNCONFIRMED and must never be trusted
directly for attribution — callers must join through `StateStore` by `position_id` instead, same
as the existing magic-recovery fix does for positions/orders.

**3 new tests** (`tests/unit/test_domain_models.py`): frozen-immutability (matching every other
model in that file), the `position_id`-as-join-key relationship stated as an executable assertion
rather than only a comment, and a full-field round-trip proving every field from the real
`get_deals` shape survives construction unchanged.

```
pytest -q                        -> 461 passed (458 previously + 3 new Deal tests)
pytest tests/test_architecture.py -q -> 13 passed
```

No order, no live/demo call of any kind this entry. `git status` confirms nothing outside
`src/mt5_mcp_trading/domain/models.py`, `tests/unit/test_domain_models.py`, and this checkpoint
doc was touched by this increment.

**Files changed this entry**: `src/mt5_mcp_trading/domain/models.py` (modified, +`Deal`),
`tests/unit/test_domain_models.py` (modified, +3 tests), this checkpoint doc.

**`mt5_adapter/mcp_deal_history.py` reader built and unit tested** — `McpDealHistoryReader.get_deals()`,
same shape as `McpMarketDataSource`/`McpAccountReader`: only ever calls through `McpClient`
(`get_deals` already classified `READ_ONLY`, no local server extension needed this time — unlike
`get_positions_with_magic`/`get_pending_orders_with_magic`, `get_deals` already exists upstream),
reuses `parse_dataframe_csv()` unchanged, same defensive `int(float(row[...]))` coercion as
`mcp_account.py`.

**Two real wire-format quirks found by tracing past the docstring into the vendored
`metatrader_client.history` package's actual source, not guessed — this correction landed BEFORE
committing anything, since it changes a field the previous `Deal` entry above had gotten wrong**:
- **`type` is a raw MT5 `ENUM_DEAL_TYPE` int, not a string.** `metatrader_client/history/get_deals.py`
  calls `deal._asdict()` directly on the raw MT5 `TradeDeal` namedtuple returned by
  `mt5.history_deals_get()` — no string conversion happens anywhere in the call chain. Confirmed
  against `client_history.py`'s own local `DealType(Enum)` (`BUY=0`, `SELL=1`, `BALANCE=2`, …).
  **`Deal.type` corrected from `str` to `int`** before this entry's tests were written (the
  previous entry's 3 tests and their `_deal()` helper were updated from string literals like
  `"DEAL_TYPE_SELL"` to the real int codes) — caught by reading one level deeper than Step 4's
  original research had gone, not by a live call.
- **`time` is a genuine UTC instant but arrives as a naive datetime string, unlike every other
  timestamp this codebase parses.** `metatrader_client/history/get_deals_as_dataframe.py` converts
  MT5's raw epoch-seconds `time` field via `pd.to_datetime(df['time'], unit='s')` — unambiguously
  UTC by definition (Unix epoch), but pandas attaches no `tzinfo`, and the CSV serialization
  carries no offset suffix (unlike candles/positions/orders/price, which all carry an explicit
  `"Z"` or `"+00:00"` — see `metatrader_parsing.py`'s module docstring). `parse_iso_datetime()`
  would silently return a naive `datetime` here; the reader's new `_parse_deal_time()` helper
  explicitly attaches `tzinfo=timezone.utc` when the parsed value comes back naive, rather than
  trust the string to say so — avoiding a domain-layer value that would silently disagree with
  every other tz-aware datetime elsewhere in this codebase.
- A third, smaller finding along the way: `get_deals_as_dataframe()`'s `set_index("time")` call
  means the CSV's leading column is named `"time"` (not blank, unlike candles/positions CSVs) —
  harmless in practice since `parse_dataframe_csv()` matches by header name regardless, but the
  test fixture below matches this exact shape rather than the blank-leading-column shape used
  elsewhere, so a future reader touching this tool isn't misled by copying the wrong sample.

Both corrections are documented directly on `Deal`'s docstring in `domain/models.py`, not only
here, so the reasoning travels with the type itself.

**9 new tests** (`tests/unit/test_mt5_adapter_mcp_deal_history.py`, same `_StubMcpClient`
re-implementing `ToolRegistry.authorize_call()` pattern as the market-data tests): real-field
parsing (including `type`/`entry` staying raw ints), the UTC-attachment behavior proven directly
(`deal.time.isoformat() == "2026-08-04T10:15:32+00:00"`), extra real MT5 columns
(`time_msc`/`reason`/`external_id`) ignored without raising, empty history returns `[]`, malformed
CSV (missing column / non-numeric field) raises `KeyError`/`ValueError`, conditional argument
passing (`None` args omitted entirely rather than sent as explicit nulls, matching
`McpAccountReader`'s existing convention), and the same `READ_ONLY`-classification enforcement
test every other reader has.

```
pytest -q                        -> 470 passed (461 previously + 9 new)
pytest tests/test_architecture.py -q -> 13 passed
```

No order, no live/demo call of any kind this entry — everything above came from reading
already-installed package source on disk, same as Step 4's original research.

**Files changed this entry**: `src/mt5_mcp_trading/mt5_adapter/mcp_deal_history.py` (new),
`tests/unit/test_mt5_adapter_mcp_deal_history.py` (new, +9), this checkpoint doc.

**`monitoring/live_performance.py` built and unit tested** — the pure join/computation layer the
Design section scoped: `build_closed_trades(closed_records, deals) -> LiveTradeJoinResult` joins
`StateStore.all_closed()` records to real `Deal`s by `position_id` only (never `deal.magic`),
constructs genuine `backtest.ledger.ClosedTrade` instances so they feed
`backtest/metrics.py`'s existing `expectancy_r()`/`max_drawdown_r()`/`has_minimum_sample()`
completely unchanged, and `realized_pnl_since(deals, since, trusted_position_ids) -> float` — the
still-missing real input `risk/daily_loss_guard.check_daily_loss_limit()`'s
`realized_pnl_since_reset` parameter has needed since Step 2. No adapter import, no MCP/MT5 call.

**Design decisions made building it, each because a real edge case forced a choice, not
guessed ahead of one**:
- `entry==2` ("inout", a netting reversal in one fill) counts as BOTH an opening and closing leg
  for volume-weighting — the one fill genuinely did both.
- `price_open` prefers the volume-weighted price of matched IN-entry deals; falls back to the
  local record's `executed_price` only when no IN deal is present (e.g. the position opened
  before the caller's `get_deals` date window). If neither is available, the record is skipped,
  never fabricated.
- A record with no matching OUT-entry deal at all (never actually closed on the broker side, or
  outside the queried window) is skipped, not treated as an error — a caller-supplied date range
  that doesn't reach far enough back shows up as a skip reason, not a crash.
- A record whose `requested_sl` equals its resolved `price_open` (risk-per-trade zero, e.g. a
  `manual_adoption` record, which defaults `requested_sl=0.0`) is skipped rather than raising —
  deliberately NOT reusing `BacktestLedger.close_position()` for this reason: that method raises
  on exactly this condition because the backtest engine guarantees non-zero SL by construction,
  a guarantee live data does not share.
- **`notional_pnl` on trades built here holds a REAL, broker-confirmed dollar figure**
  (`profit+commission+swap+fee` summed across every matched deal) — the checkpoint's own
  Step 4 planning note flagged this as a deliberate divergence from `backtest/ledger.py`'s
  explicitly-uncalibrated use of the same field; now built and documented directly on this
  module, not left as a silent mismatch.
- **`close_reason` is a fixed, honest `"closed"`** for every trade built here — distinguishing a
  real SL exit from a real TP exit would need MT5's own `ENUM_DEAL_REASON` (a raw `reason` field
  on the wire, visible in the deal-history reader's own test fixture but not modeled on `Deal`,
  since Step 4's research scope only covered `client_history.py`'s documented field list). Not
  resolved here — flagged as a real, open gap rather than guessed at with a fabricated SL/TP
  label. Doesn't block expectancy/drawdown (neither metric reads `close_reason`), but a future
  caller wanting a genuine SL-vs-TP breakdown will need `Deal.reason` added first.

**18 new tests** (`tests/unit/test_monitoring_live_performance.py`): BUY/SELL R-multiple
computation, a direct cross-check against `BacktestLedger.close_position()`'s own formula on
matching inputs (proving the "same formula" claim rather than only asserting it), real-dollar
`notional_pnl` summation across both legs, `deal.magic` deliberately ignored in favor of the
record's own magic, the `executed_price` fallback, the `inout`-counts-as-both-legs case, deals
for an unrelated position ignored, every skip path (no OUT deal / no open-price source / zero
risk), multiple records resolving independently, and `realized_pnl_since()`'s own filtering
(time cutoff, untrusted `position_id`, magic-agnostic summation, naive-`since` rejection).

```
pytest -q                        -> 488 passed (470 previously + 18 new)
pytest tests/test_architecture.py -q -> 13 passed
```

No order, no live/demo call of any kind this entry.

**Files changed this entry**: `src/mt5_mcp_trading/monitoring/live_performance.py` (new),
`tests/unit/test_monitoring_live_performance.py` (new, +18), this checkpoint doc.

**`scripts/run_demo_execution_live_performance_monitor.py` written, then live-verified — run for
real against the demo connection, per explicit go-ahead.** Same
READ-ONLY shape as `run_demo_execution_historical_data_probe.py`: goes through
`demo_execution_session()`, unpacks `executor` and immediately discards it (never referenced),
only calls `get_deals` (`McpDealHistoryReader`, `READ_ONLY`-classified). Uses the REAL production
`StateStore` path (`var/order_state`) deliberately — the whole point is reporting on this
project's actual recorded history, unlike the historical-data probe's throwaway path.

**Design choices made writing it**:
- `get_deals`'s `from_date` is derived from the earliest `submitted_at` among the real closed
  `StateStore` records themselves (midnight UTC of that day), not a hardcoded guess or the
  tool's own 30-day default — guarantees the query window covers every locally closed record
  without assuming how far back this project's real history goes. Anything the query still
  misses (e.g. a deal purged from the terminal's own local history) surfaces honestly as a skip
  in `build_closed_trades()`'s report, never silently dropped.
- Reports grid and runner separately by magic (`71101`/`72101`, per the Step 1 locked-parameter
  table), never blended — matching `backtest/metrics.py`'s own established convention, and flags
  any matched trade carrying a magic outside that set for visibility rather than silently
  excluding it.
- Also prints realized P&L since the most recent UTC daily-reset boundary
  (`risk.daily_loss_guard.daily_reset_boundary()`) via `realized_pnl_since()` — the real number
  Step 2's kill-switch has been missing since it was built. **Display only**: this script does
  NOT call `check_daily_loss_limit()` or feed anything into `pipeline/loop_control.py` — wiring
  the kill-switch into a live decision remains its own, separate, later, explicitly-approved
  step, unchanged from Step 3's own scoping.

**Verified without running it**: `py_compile` and a direct `importlib` module load (no `main()`
call, so no session/connection attempted) both succeed — every import resolves, `STRATEGIES`/
`STATE_PATH` construct correctly. Full suite re-run for regression safety: still 488 passed
(unchanged, since nothing under `src/`/`tests/` was touched this entry), architecture tests
still 13 passed.

**Written entry: deliberately not executed at the time it was built.** `git status` at that point
confirmed only this one new file plus the checkpoint doc had changed; no live/demo call of any
kind was made writing it. Running it required its own explicit go-ahead, per this checkpoint's
own Step 4 entry point and the project's standing live-testing-pause rule.

**Files changed this entry**: `scripts/run_demo_execution_live_performance_monitor.py` (new),
this checkpoint doc.

## Live run: first real Phase 9 forward-test read (2026-08-06, explicit go-ahead given)

Ran for real against the demo connection. One real `get_deals` call, read-only throughout — no
order, no `executor` reference, connection closed normally afterward. `require_demo_account`'s
informational check logged its already-known, already-documented warning
(`trade_mode='REAL'` — the confirmed `mcp_account.py` inversion bug) and was correctly ignored,
same as every prior real run in this project; the actual safety gate
(`require_demo_account_kind`, `MT5_ACCOUNT_KIND=DEMO`) passed before any connection was even
opened.

**Real results** — 39 locally closed `StateStore` records found; `from_date` derived as
`2026-08-03` (the earliest of those records' `submitted_at`); 162 real deals returned; 37 records
matched to a genuine `ClosedTrade`, 2 skipped:

| Strategy (magic) | Trades | `expectancy_r` | `max_drawdown_r` | 30-trade minimum met? |
|---|---|---|---|---|
| grid (71101) | 19 | −0.825 R | 16.302 R | no |
| runner (72101) | 13 | −0.418 R | 10.214 R | no |

Both negative, directionally consistent with Phase 8's backtested findings for both strategies —
but neither has reached the 30-trade minimum sample this project's own acceptance bar requires
(`has_minimum_sample()`), so this is a first honest read, not a validated result one way or the
other. Realized P&L since the most recent UTC daily-reset boundary
(`2026-08-06T00:00:00+00:00`) was `+0.00` (nothing closed today at the moment of the run).

**Two things the report surfaced, not diagnosed further this entry (flagged, not investigated,
per instruction to hold here)**:
- **5 matched trades carry a magic outside `{71101, 72101}`** — not attributed to either strategy,
  listed for visibility only, not counted in the table above. Likely earlier-phase smoke-test
  magics (e.g. `79999`, see Phase 8's `runner_sltp_smoke_test` runs) rather than a new issue, but
  not confirmed here.
- **2 local records skipped** (tickets `171648990`, `171649461`) — no matching OUT-entry deal
  found in the returned deal history, so no real close price could be determined for either.
  Could be pruned MT5-side deal history, a `from_date` edge case, or a genuine local/real
  mismatch — not root-caused this entry, reported honestly via `build_closed_trades()`'s skip
  mechanism exactly as designed (never fabricated, never silently dropped).

No production code changed this entry — a live run of already-built, already-tested code, not a
new capability.

**Files changed this entry**: this checkpoint doc only.

## Follow-up: the 2 skipped tickets, root-caused (2026-08-06, explicit go-ahead given)

`scripts/run_demo_execution_check_two_skipped_grid_tickets.py` (new, one-off diagnostic — not
part of Phase 9's scoped deliverables) confirmed the hypothesis directly: one real, read-only
`get_orders` call (MT5's pending-order HISTORY tool, distinct from `get_deals` — already
`READ_ONLY`-classified, no `executor` reference) shows both tickets at `state='2'`
(`OrderState.CANCELED` per `client_history.py`'s own enum), with `volume_current` equal to
`volume_initial` (`0.01 == 0.01`) — confirming zero volume was ever filled, not even partially.

Both were ordinary grid LIMIT orders cancelled while still pending (price never reached the
level, later superseded) — never real trades. `build_closed_trades()`'s skip was correct
behavior, not a bug: a cancelled-unfilled order genuinely has no deal to join against, since MT5
only records a deal for an actual execution.

One loose thread noted, not chased further (outside what was asked): the broker's own
`time_done` for both (`2026-08-04 08:18:42`/`43` UTC) is ~1.5 hours after the local records'
`closed_at` (`06:53:44` UTC) — a real gap between when MT5 actually cancelled the orders and
when a later reconciliation script happened to notice and record it locally. Not a correctness
problem (the cancellation is real either way), just a timing note.

**Broader, structural finding this surfaces**: this project's local `"CLOSED"` status is
overloaded — it's written identically by ~10 reconciliation scripts
(`run_demo_execution_cleanup_loop_run.py` and siblings) for BOTH "filled then closed" and
"cancelled/expired without ever filling," using the same generic
`"confirmed absent from live positions/orders..."` reason text that admits the ambiguity in its
own wording. The project already has the correct distinction available in one place
(`run_demo_execution_reconcile_manual_cancel_5_grid_orders.py` uses `record_cancelled()`
correctly for a known-unfilled case) — it just wasn't applied consistently every time historical
reconciliation happened. Not fixed here (retroactively relabeling old records wasn't asked for,
and `build_closed_trades()` already handles the ambiguity correctly today, at the cost of a skip
instead of a `CANCELLED`-aware exclusion) — flagged for awareness, not acted on.

No `StateStore`/production code changed by this follow-up (skip logic already handled this
correctly) — only the new one-off diagnostic script and this checkpoint doc.

**Files changed this entry**: `scripts/run_demo_execution_check_two_skipped_grid_tickets.py`
(new), this checkpoint doc.

## Follow-up: the 5 unrecognized-magic trades, identified (2026-08-06, local records only)

No live call needed — the 5 local `StateStore` records already self-document. Grepping
`var/order_state/*.json` for `magic` outside `{71101, 72101}` finds exactly 5 files, matching
the live run's count exactly:

| Ticket | Comment | Submitted |
|---|---|---|
| `171617865` | `phase6_step6_market_smoke_test` | 2026-08-03 |
| `171618036` | `phase6_step6_market_smoke_test` | 2026-08-03 |
| `171618202` | `phase6_step7_market_sell_smoke_test` | 2026-08-03 |
| `171621792` | `runner` | 2026-08-03 |
| `171702598` | `runner` | 2026-08-05 |

All five carry `magic=79999` and `strategy="unknown_magic_79999"` — flagged as non-strategy
trades at the time they were originally recorded, not a new discovery. The first three are
Phase 6 Steps 6–7's live MARKET-order verification; the last two match
`scripts/run_demo_execution_runner_sltp_smoke_test.py` runs — tickets `171621792` and
`171702598` are both named explicitly in `AGENTS.md`'s own history (Phase 8's runner SL/TP
validation and its closing smoke test), independently confirming the identification.

**Not a data problem.** These are deliberate, one-off smoke-test trades, not real strategy
output — correctly excluded from grid/runner's performance numbers by the monitor's
magic-based split. Including them would have polluted the strategy metrics with trades no
strategy signal ever produced. No code change needed or made.

**Files changed this entry**: this checkpoint doc only.

## Step 5 — live kill-switch wiring + smoke test: WIRING + TESTS BUILT, NOT LIVE-RUN

Per the Proposed Steps table above: *"A single, short, explicitly-approved live smoke test
proving the wired kill-switch actually halts a REAL loop run when triggered (a deliberately tiny,
easily-crossed threshold, not the real production one)."* Entry criteria (Steps 2–4 done) are
now met. This is the one step in the whole plan flagged live/order-adjacent — its own separate
go-ahead, same discipline as every past live step in this project. **Status as of end of this
session (2026-08-06): the proposal below was approved, and its code-only half (wiring + tests,
"Sequencing" bullet (a)) is now built. The live half (bullet (b), actually running the wired
script) has NOT happened and was NOT authorized — a separate, later, explicit go-ahead per this
project's standing pause rule.**

### Goal

Two things, in order:
1. Wire the real live script (`scripts/run_demo_execution_pipeline_loop.py`) to a REAL
   `daily_loss_decision`, computed from a real `get_deals` read each cycle — closing the gap
   Step 3 deliberately left open ("the real script still cannot actually stop on a loss
   breach... wiring the actual live script's call sites to a real decision is deferred to
   whichever of Step 4/5 first has a real number to supply"). Step 4 built that real number
   (`realized_pnl_since()`); Step 5 is where it gets connected.
2. Prove it, live: run the wired script with a deliberately tiny, easily-crossed threshold and
   observe one real trigger — the loop actually halts, verified via a live re-read, zero
   leftover unmanaged risk.

### What's needed, and why it's smaller than it first looks

`realized_pnl_since(deals, since, trusted_position_ids)` (Step 4) only needs `deals` and a
`trusted_position_ids` set — NOT the full `build_closed_trades()` join. So the live wiring per
cycle is:
```
trusted_ids = {r.ticket for r in state_store.all_closed()}
reset_boundary = daily_reset_boundary(datetime.now(timezone.utc), reset_hour_utc=RESET_HOUR_UTC)
deals = await deal_reader.get_deals(from_date=reset_boundary.strftime("%Y-%m-%d"))
pnl = realized_pnl_since(deals, since=reset_boundary, trusted_position_ids=trusted_ids)
decision = check_daily_loss_limit(pnl, DailyLossLimitConfig(max_daily_loss=MAX_DAILY_LOSS, reset_hour_utc=RESET_HOUR_UTC))
```
then pass `decision` as `daily_loss_decision` into both of `should_stop()`'s existing real call
sites (`main()`'s top-of-loop check, `_wait_for_next_cycle()`'s during-wait check — confirmed by
reading the current script, lines ~184 and ~228). `from_date` narrows to just today's reset
window here (unlike the one-shot monitor script's "earliest ever closed record" — the kill-switch
only ever cares about the current window), so this is cheap: one extra real `get_deals` call per
5-minute cycle, not per guard check.

### Open design points — flag if you'd rather choose differently

1. **`MAX_DAILY_LOSS` for the smoke test**: a placeholder, deliberately tiny value, NOT the real
   production threshold (open design point 3 upstream is still undecided and stays undecided by
   this step). Candidate: derive it from what's already near-certain rather than guessed — every
   closed trade pays real spread/commission, a small guaranteed negative contribution to
   `realized_pnl_since_reset` even on a directionally-winning trade. A threshold set just below
   one trade's typical commission magnitude would trip reliably on the first real closed trade of
   the smoke-test run, regardless of win/loss direction — bounding the live exposure to
   "however long until one trade closes," not "however long until a real loss occurs" (which is
   unpredictable in timing). Needs a concrete number before this step can actually run; not fixed
   here.
2. **`RESET_HOUR_UTC`**: still just Step 2's placeholder default (`0`), never reviewed against an
   operational preference (carried forward, unresolved since Step 2).
3. **Per-cycle `get_deals` cost/reliability**: new real MCP traffic this loop didn't make before.
   Cheap in isolation (one call per 5-minute cycle), but if `get_deals` itself ever raises
   (network hiccup, timeout), does the cycle correctly fail closed (treat as "can't confirm
   safety, stop") or fail open (skip the check, keep running)? Not decided here — needs an
   explicit choice before wiring, since this is exactly the kind of guard-computation failure
   mode `AGENTS.md`'s "never silently trust" discipline cares about.
4. **Smoke-test duration/scope**: should reuse the loop's own existing `MAX_CYCLES`/
   `MAX_RUNTIME_MINUTES` ceilings as a backstop (unchanged), sized small — this is a smoke test
   proving ONE trigger, not Step 7's sustained run.

### Files to create/change (once approved — no code yet)

- `scripts/run_demo_execution_pipeline_loop.py`: add `MAX_DAILY_LOSS`/`RESET_HOUR_UTC` module
  constants (mirroring the existing `MAX_CYCLES`/`MAX_RUNTIME_MINUTES` pattern); wire the real
  `daily_loss_decision` computation into both `should_stop()` call sites; construct
  `McpDealHistoryReader` alongside the existing `McpMarketDataSource`.
- Possibly a small new pure/testable helper (rather than inlining the computation in `main()`) —
  e.g. `pipeline/loop_control.py` or a new function near it — so the per-cycle computation above
  can be unit-tested against a stub, not only proven by reading `main()`.
- New integration test extending `tests/integration/test_pipeline_loop_daily_loss_stop.py`'s
  pattern (or a new file) against the REAL script's own `main()`/call sites this time (loaded via
  `importlib`, same harness `test_pipeline_loop_disconnect.py` already established) — proving the
  wiring doesn't regress stop-file/`max_cycles`/`max_runtime` precedence in the real script, not
  just in the mock harness Step 3 already proved this in isolation.

### Sequencing — two separate go-aheads, not one

Matching how every other live-adjacent step in this project has worked: (a) build the wiring +
tests above with **no live call at all** (same discipline as Steps 2–3) — this alone can be done
under a code-only go-ahead; then (b) actually RUNNING the wired script against the real demo
connection is its own, separate, later go-ahead — building the wiring does not imply permission
to run it, per this project's standing live-testing-pause rule (a fresh go-ahead is required
each time, not assumed carried forward from an earlier one in the same session).

### Exit criteria (from the Proposed Steps table, restated)

One real observed trigger event, loop halted, verified via a live re-read, zero leftover
unmanaged risk left behind. **Not yet met — the live half of this step has not run.**

### Built this session (code + tests only, no live call)

- `monitoring/live_performance.py`: new `compute_daily_loss_decision(deals,
  trusted_position_ids, now, config) -> RiskDecision` — combines `realized_pnl_since()` with
  `risk.daily_loss_guard.check_daily_loss_limit()`, still pure (no adapter access; callers fetch
  `deals` themselves). 6 new unit tests in `tests/unit/test_monitoring_live_performance.py`
  (within-limit approval, at-limit breach, deals before the reset boundary excluded, untrusted
  `position_id`s excluded, off-by-default always approves, naive-`now` rejected).
- `scripts/run_demo_execution_pipeline_loop.py`: new `MAX_DAILY_LOSS`/`RESET_HOUR_UTC`/
  `DAILY_LOSS_CONFIG` module constants (`MAX_DAILY_LOSS` defaults to `None` — kill-switch present
  but inert, per open design point 1 staying deliberately undecided); two new functions —
  `_compute_daily_loss_decision()` (the real computation, CAN raise, short-circuits to zero real
  calls when the threshold is unset) and `_daily_loss_decision_for_cycle()` (never raises — FAILS
  CLOSED on a computation error, resolving open design point 3 above: a safety-critical gate that
  can't confirm safety stops, it doesn't proceed on blind trust); wired into both real
  `should_stop()` call sites, the SAME decision computed once per loop iteration and reused for
  both the pre-cycle and during-wait checks (one real `get_deals` call per iteration, not per
  guard check, resolving open design point 3's cost question too).
- New `tests/integration/test_pipeline_loop_daily_loss_wiring.py` (10 tests) — against the REAL
  script's own functions (loaded via `importlib`, same harness as
  `test_pipeline_loop_disconnect.py`) via a stub `McpClient`, not a re-implemented mock harness:
  off-by-default makes no real call, within-limit/breach/untracked-ticket computation, a real
  `get_deals` failure raises from `_compute_daily_loss_decision()` but is caught and fails closed
  by `_daily_loss_decision_for_cycle()`, and three end-to-end driver tests proving a real breach,
  a real approval, and a MID-RUN computation failure (succeeds on cycle 1's check, fails on
  cycle 2's) each correctly stop or continue a real multi-cycle `DryRunExecutor` run.

```
pytest -q                        -> 504 passed (488 previously + 16 new)
pytest tests/test_architecture.py -q -> 13 passed
```

No order, no live/demo call of any kind building this. `MAX_DAILY_LOSS=None` means today's real
script behavior is otherwise unaffected — the kill-switch is wired and computed every cycle
(when enabled) but cannot trip until its threshold is deliberately set for an explicitly-approved
live run.

**Files changed this entry**: `src/mt5_mcp_trading/monitoring/live_performance.py` (modified,
+`compute_daily_loss_decision()`), `scripts/run_demo_execution_pipeline_loop.py` (modified, real
kill-switch wiring), `tests/unit/test_monitoring_live_performance.py` (modified, +6),
`tests/integration/test_pipeline_loop_daily_loss_wiring.py` (new, +10), this checkpoint doc,
`AGENTS.md`.

### Live smoke test run (2026-08-07, explicit go-ahead given) — kill-switch did NOT trip; a real gap found, not fixed

Open design points resolved first: `MAX_DAILY_LOSS=0.01` (trips on the first net realized loss of
any size — sidesteps needing real commission-magnitude data, which isn't recorded anywhere in this
repo), `RESET_HOUR_UTC=0` (kept Step 2's default). Both wired into
`scripts/run_demo_execution_pipeline_loop.py`'s existing constants.

**Ran live, for real, for the full 12 cycles / ~56 minutes** (`mode=DEMO_EXECUTION`,
`trading_enabled=True`, `mt5_account_kind='DEMO'` confirmed) — stopped by `max cycles (12) reached`,
**not** by the kill-switch. `_daily_loss_decision_for_cycle()` never raised (no fail-closed path
exercised) and never rejected — every cycle's `get_deals` call returned **zero deals**, so
`realized_pnl_since_reset` was `0.0` throughout and `check_daily_loss_limit()` had nothing to react
to. **This is not evidence that no losses occurred** — real evidence says otherwise (see below) —
so Step 5's exit criteria ("one real observed trigger event, loop halted") is **NOT met**, and not
because the threshold was wrong.

**Real trades did close during the run, confirmed independently of `get_deals`**: 4 separate real
runner (magic=72101) MARKET positions were opened across the run (tickets `171809336` cycle 1,
`171809814` cycle 6, `171809876` cycle 7, `171809948` cycle 8) despite
`max_concurrent_positions=1` — only possible if the previous one had genuinely closed by the time
the next cycle's `check_position_limit()` ran (that guard reads real live positions cross-referenced
against `StateStore`-trusted tickets, the same magic-recovery-fix pattern already proven correct
elsewhere in this project — not the broken raw magic filter, so its "0 open" reads are trustworthy).
A post-run read-only diagnostic (`scripts/run_demo_execution_check_step5_smoke_test_state.py`, new,
no `executor` reference) confirms the account's real current open positions are only 4 total —
`171809948` (the run's last runner position) plus 3 grid SELL LIMIT orders that filled into real
positions (`171811195`, `171809947`, `171809984`) — `171809336`/`171809814`/`171809876` are absent,
meaning all three genuinely closed (SL/TP, matching this project's established pattern throughout).

**Yet `get_deals(from_date='2026-08-07')` queried fresh, in a brand-new session, well after the run
ended, still returns zero deals** — including zero for any of those three confirmed-closed
tickets. Root-caused precisely with a follow-up probe
(`scripts/run_demo_execution_probe_get_deals_gap.py`, new, read-only, no `executor` reference):
querying with an explicit `to_date='2026-08-08'` (next calendar day) finds all 6 deals for the 3
target tickets immediately. Comparing their reported `Deal.time` against the SAME tickets' true UTC
fill time (independently known from the run's own log, generated via `datetime.now(timezone.utc)`)
found an **exact, repeated +3:00:00 offset**, confirmed three separate times (each within 2 seconds
of precision):

| Ticket | True UTC fill time (run's own log) | `Deal.time` reported | Offset |
|---|---|---|---|
| 171809336 | 01:49:02 UTC | 04:49:00 | +3:00:00 |
| 171809814 | 02:14:11 UTC | 05:14:09 | +3:00:00 |
| 171809876 | 02:19:14 UTC | 05:19:12 | +3:00:00 |

**This is not a sync-lag gap — it's a real, previously-shipped labeling bug.** `Deal.time` is not
actually UTC; it's the broker's server time (UTC+3), and `mt5_adapter/mcp_deal_history.py`'s
`_parse_deal_time()` mislabels it as UTC (`tzinfo=timezone.utc` attached directly, no offset
correction). Step 4's own docstring claim ("time is a genuine UTC instant... converted from MT5's
epoch-seconds field") was reasoned from the conversion code path, never cross-checked against a
real known-true-UTC event until this probe — the first time this project has had independent
ground truth (the run's own log timestamps) to check a `Deal.time` value against. Every deal
`McpDealHistoryReader` has ever returned (including the 2026-08-06 monitor run's 162 deals) carries
this same +3h mislabel. The Aug 6 run's expectancy/drawdown numbers are unaffected (multi-day
windows, time-of-day-independent math), but any window-boundary-sensitive computation is —
`_compute_daily_loss_decision()`'s `to_date=None -> datetime.now()` default is exactly that: a
3-hour mislabel is enough to push genuinely-recent deals outside the comparison window, which is
the precise mechanism behind this run's zero-deals-every-cycle result. **Not fixed this entry** —
flagged with a confirmed, precise root cause rather than a guess, ready for its own scoped fix.

**Practical consequence**: as currently wired, `MAX_DAILY_LOSS` is effectively inert for real-time
monitoring regardless of its value — `realized_pnl_since_reset` reads `0.0` against real, closed,
real-money-adjacent losses whenever the true window boundary falls within roughly 3 hours of "now"
(exactly the smoke test's own scenario). A safety-relevant finding about the kill-switch's real
reliability, surfaced by the smoke test exactly as a smoke test should — not a wasted run.

**Real current account state left behind** (no cleanup, matching this script's established design):
4 open positions (`171809948` runner SELL, `171811195`/`171809947`/`171809984` grid SELL — all
protected, SL/TP confirmed at submission) + 2 pending grid orders (`171809875` SELL LIMIT,
`171811194` BUY LIMIT) — total exposure exactly at `max_open_lots=0.06`. Nothing unprotected;
"leftover" here means open, not unmanaged. Separately noted, not new: local
`StateStore.all_open()` now shows 71 tickets marked `OPEN` (up from the 39/57 previously quantified)
— the same already-accepted, not-fixed staleness property (Steps 25/29's "not fixing it now"
decision), now larger in scale; still harmless per the same reasoning (`determine_posture()`/
`MANAGE_ONLY` gate/magic-recovery fix all read live state fresh, never trust a stale local
`status` field for a safety decision).

**Not decided in this entry**: how to fix `_parse_deal_time()`'s UTC mislabeling (hardcoding +3h is
fragile — broker server offsets can shift with daylight saving depending on the specific broker's
convention; a robust fix should derive the real offset rather than assume this session's measured
value holds forever), whether to manage (close/cancel or leave) the 4 open positions + 2 pending
orders, and whether the already-large stale-`StateStore` count changes the prior "not fixing it
now" call. Each is its own next decision, not assumed here.

```
pytest -q -> unaffected (no src/ changed this entry — MAX_DAILY_LOSS/RESET_HOUR_UTC are constant
             value changes already covered by existing tests; new scripts are one-off diagnostics,
             not part of the test suite, matching every other one-off diagnostic in this project)
```

**Files changed this entry**: `scripts/run_demo_execution_pipeline_loop.py` (modified,
`MAX_DAILY_LOSS`/`RESET_HOUR_UTC` set for the live run),
`scripts/run_demo_execution_check_step5_smoke_test_state.py` (new, read-only diagnostic),
`scripts/run_demo_execution_probe_get_deals_gap.py` (new, read-only diagnostic, root-caused the
`Deal.time` UTC mislabel), this checkpoint doc.

### `Deal.time` offset fix: built, unit + integration tested, NOT re-run live

User's choice from the two flagged options (hardcode +3h vs. derive live vs. leave unfixed): derive
the real offset live each session, not hardcode a value that could go stale (e.g. across a daylight-
saving boundary).

**`monitoring/live_performance.infer_deal_time_offset(closed_records, deals) -> Optional[timedelta]`**
(new, pure): derives the real correction from data already available every session, no new adapter
call needed. For each `CLOSED` **MARKET**-order `LocalOrderRecord` (LIMIT orders deliberately
excluded — a LIMIT fill can happen long after `submitted_at`, corrupting the comparison; a MARKET
fill is essentially simultaneous with `submitted_at`, which is real, locally-recorded true UTC —
see `mcp_order_executor.py`), diffs its own IN-entry `Deal.time` (mislabeled) against
`record.submitted_at` (true UTC). Takes the **median** across every available reference (robust to
one outlier) and rounds to the nearest **15 minutes** (real broker server offsets are always round
numbers, never an arbitrary number of minutes — filters ordinary network/processing-latency noise).
Returns `None` when no MARKET-order reference exists yet (e.g. a fresh `StateStore`, or a run that
has only ever placed LIMIT orders) — callers decide their own fallback; this function never
fabricates a value it can't derive.

**`realized_pnl_since()`/`compute_daily_loss_decision()`** both gained an optional
`deal_time_offset: timedelta = timedelta(0)` parameter (default preserves every existing caller's
exact prior behavior — same Optional-defaults-to-off convention as every other guard field in this
codebase), subtracted from `deal.time` before comparing against `since`, so the reset-boundary
filter compares against each deal's real true-UTC instant, not its mislabeled one.

**`scripts/run_demo_execution_pipeline_loop.py`'s `_compute_daily_loss_decision()`** now does two
things differently, both necessary — confirmed by working through the actual failure mechanism, not
guessed:
1. **Fetches with a full-day margin on both ends** (`from_date = boundary - 1 day`,
   `to_date = now + 1 day`) instead of `from_date=boundary` only with no `to_date` — this is what
   actually caused the 2026-08-07 run to find zero deals every cycle (the vendored client's
   `to_date=None -> datetime.now()` default, combined with the mislabel, produced a window that
   excluded the run's own recent closes). A generous, fixed margin sidesteps needing to know the
   exact offset just to fetch safely — no real deal is missed regardless of the mislabel's
   direction or magnitude.
2. **Infers the real offset from `state_store.all_closed()` + the fetched deals** via
   `infer_deal_time_offset()`, falling back to `timedelta(0)` (no correction) when no MARKET-order
   reference is available yet, and passes it into `compute_daily_loss_decision()` — this is what
   makes the reset-boundary *filter* itself correct, not just the fetch window wide enough.

**9 new tests**: 5 unit tests for `infer_deal_time_offset()` (no records, LIMIT-order exclusion,
basic derivation, 15-minute rounding, median-ignores-an-outlier), 2 unit tests for
`realized_pnl_since()`/`compute_daily_loss_decision()`'s new `deal_time_offset` parameter (the
`compute_daily_loss_decision` one directly reproduces the confirmed bug's sign: a loss that
genuinely happened before today's reset boundary must not be misattributed to today just because
the +3h mislabel pushes its apparent timestamp past the boundary — wrongly counted as a breach
(`approved=False`) without correction, correctly excluded (`approved=True`) once corrected), and 1 new
integration test proving the real script's call site now asks for a full-day margin on each end
(replacing the narrow `from_date=boundary`-only call that caused the live failure). All 5 pre-
existing integration tests in `test_pipeline_loop_daily_loss_wiring.py` pass completely unchanged
(their stub `_submit_closed_record()` always used `order_type="LIMIT"`, so `infer_deal_time_offset`
correctly returns `None` for them and the `timedelta(0)` fallback preserves their exact original
expected behavior — confirms the fix is additive, not a silent behavior change for already-tested
paths).

```
pytest -q                        -> 513 passed (504 previously + 9 new)
pytest tests/test_architecture.py -q -> 13 passed
```

No order, no live/demo call of any kind building or testing this fix — purely a code fix informed
by the read-only diagnostics above. **Not re-run live this entry** — Step 5's own "two separate
go-aheads" rule applies again: this is new, safety-relevant code touching the kill-switch's real
computation, so re-attempting the live smoke test needs its own fresh, explicit go-ahead, not
assumed carried forward from building the fix.

**Files changed this entry**: `src/mt5_mcp_trading/monitoring/live_performance.py` (modified,
+`infer_deal_time_offset()`, +`deal_time_offset` param on 2 functions),
`scripts/run_demo_execution_pipeline_loop.py` (modified, wide-margin fetch + offset correction),
`tests/unit/test_monitoring_live_performance.py` (modified, +8),
`tests/integration/test_pipeline_loop_daily_loss_wiring.py` (modified, +1), this checkpoint doc.

### Re-run #2: abrupt kill (unexplained), which surfaced a SECOND real gap, fixed — then Step 5's exit criteria finally met (2026-08-07, explicit go-aheads given throughout)

**Re-run #2** (same `MAX_DAILY_LOSS=0.01`, with the `Deal.time` fix in place): ran live, submitted
real orders through cycle 11 (~50 minutes), then was **killed abruptly** — no "Stop requested"/
"Done." log line, not a clean shutdown via the script's own stop-file/`KeyboardInterrupt` paths.
Cause unknown; the user had no context either. Checked immediately, read-only: **no orphan Python
process** (`tasklist` found none), and the real account was safe — 1 open runner position
(`171814431`, SL/TP-protected) + 4 pending grid orders, 0.05 lots total, under the 0.06 cap.
Nothing corrupted, nothing unprotected. If this recurs on a future run, worth investigating then;
not chased further here per the user's own call.

**A second, deeper, independent gap found by checking whether the live re-run's own real close was
actually detected** (`scripts/run_demo_execution_check_rerun_kill_switch_correctness.py`, new,
read-only): `realized_pnl_since()` computed `0.0` despite a real position from this exact run
having closed. Root cause: `_compute_daily_loss_decision()` sourced its trusted ticket set from
`state_store.all_closed()` — but `record_closed()` is **only ever called by
`McpOrderExecutor.close_position()`**, confirmed by `grep`, never by anything that notices a
broker-side SL/TP close on its own. Since the overwhelming majority of real closes throughout this
entire project's history have happened via broker-side SL/TP (not an explicit `close_position()`
call), `all_closed()` essentially never contains a same-session close — the kill-switch, even with
the `Deal.time` fix, could only ever react to a loss this project explicitly closed itself. This is
independent of the first bug and would have kept the kill-switch silently inert on its own.

**Fixed**: new `StateStore.all_records()` (`state/store.py`) — every locally recorded ticket
regardless of status, unlike `all_open()`/`all_closed()`'s status filters. Same
"never trust a stale local status field for a safety decision" discipline `determine_posture()`/
the `MANAGE_ONLY` gate/the magic-recovery fix already apply elsewhere — the deal data itself
(an OUT-entry deal existing) is what actually proves a close, not the local `status` field.
`_compute_daily_loss_decision()` now sources both `trusted_ids` and `infer_deal_time_offset()`'s
input from `all_records()` instead of `all_closed()`. **3 new assertions in existing `StateStore`
tests** (cold-start, multi-ticket independence, the 200-ticket at-scale sweep, and the corrupted-
file hard-stop test) — no new test functions, matching every scenario already having an
`all_open()`/`all_closed()`-checking test in place to extend, same pattern Step 4 used adding
`all_closed()` itself.

```
pytest -q                        -> 513 passed (unchanged count -- new assertions in existing tests)
pytest tests/test_architecture.py -q -> 13 passed
```

**Verified directly against real live data before attempting a third live run**
(`scripts/run_demo_execution_verify_kill_switch_fix.py`, new, read-only — calls the REAL wired
`_compute_daily_loss_decision()` via `importlib`, not a hand-rolled mirror): `approved=False`,
`realized_pnl_since_reset=-10.41` breaching `max_daily_loss=0.01` — both fixes together correctly
compute today's real accumulated loss for the first time. Flagged to the user before the third
attempt: today's real P&L was already in breach, so launching the loop would trip the kill-switch
**before cycle 1 even starts** — a different shape than "runs a few cycles, then trips", but still
a real, live-verified trigger with zero incremental risk (no new cycle would run at all). User
chose to proceed on that basis.

**Third live attempt: Step 5's exit criteria finally met.** Ran with the same `MAX_DAILY_LOSS=0.01`
— real log: `Stopping before cycle 1: daily loss limit breached
(realized_pnl_since_reset=-10.41 breaches max_daily_loss=0.01)`, then `Done. 0 cycle(s) run.`, clean
disconnect. **One real observed trigger event, loop halted, verified via a live re-read (the
`get_deals` call driving the decision), zero leftover unmanaged risk (0 cycles run, nothing new
submitted).** This is the exit criteria the Proposed Steps table specified verbatim. Leftover
exposure on the account is unchanged from re-run #2's abrupt-kill state (1 open runner position +
4 pending grid orders, all already reported, all protected) — not touched by this entry, since 0
cycles ran.

No order, no live/demo call of any kind while building either fix — only the explicitly-approved
live re-run and verification calls themselves touched the demo account, and all were read-only or
(for the loop itself) correctly refused to submit anything.

**Files changed this entry**: `src/mt5_mcp_trading/state/store.py` (modified, +`all_records()`),
`tests/unit/test_state_store.py` (modified, +3 assertions in existing tests),
`scripts/run_demo_execution_pipeline_loop.py` (modified, sources `all_records()` instead of
`all_closed()`), `scripts/run_demo_execution_check_rerun_kill_switch_correctness.py` (new, read-
only diagnostic), `scripts/run_demo_execution_verify_kill_switch_fix.py` (new, read-only
diagnostic), this checkpoint doc.

### Leftover reconciliation (2026-08-07, user-requested)

`scripts/run_demo_execution_reconcile_20260807_leftovers.py` (new, one-off): found 38 local
records from today's three live attempts still marked `OPEN`/`OPEN_UNPROTECTED`. Fresh live reads
(`get_positions`/`get_orders`/`get_deals`, all read-only) found real current state had already
moved on since the last check — 0 open positions (the last runner position, `171814431`, had since
closed via its own broker-side SL/TP), 3 pending grid orders still genuinely live
(`171809875`/`171812719`/`171812840`, all within the `0.06`-lot cap, left untouched). The other 35
tickets were reconciled purely locally (`record_closed()`/`record_cancelled()`, no MCP call): all
35 had a matching real `Deal`, so all reconciled to `CLOSED` (0 needed `CANCELLED` — every grid
LIMIT order from today either filled and later closed, or is still genuinely pending; none were
simply cancelled unfilled). `closed_at` for each uses the real OUT-entry deal's time, corrected by
a freshly re-derived `infer_deal_time_offset()` (`3:00:00` — consistent with the earlier confirmed
+3h finding, a good cross-check that the fix holds) rather than storing the raw mislabeled
timestamp into local history. Verified after: `var/order_state` now shows exactly 3 `OPEN` + 35
`CLOSED` + 0 `CANCELLED` for today's tickets, matching the script's own reported counts exactly.

```
pytest -q -> unaffected (var/order_state is git-ignored local data, no src/tests/ changed)
```

**Files changed this entry**: `scripts/run_demo_execution_reconcile_20260807_leftovers.py` (new),
`var/order_state/*.json` (35 records transitioned to `CLOSED`, local data only, not tracked by
git), this checkpoint doc.

### Closed the 3 remaining pending grid orders (2026-08-07, user-requested)

`scripts/run_demo_execution_cancel_20260807_remaining_grid_orders.py` (new, one-off, mirrors
`run_demo_execution_cancel_pipeline_cycle_orders.py`'s exact shape): cancelled the last 3 genuinely
live pending grid LIMIT orders left after reconciliation (`171809875`, `171812719`, `171812840`).
All three verified present before, cancelled with exactly one attempt each (no retry), all three
retcode `10009`, all three verified absent from live pending orders afterward.
`McpOrderExecutor.cancel()` wrote local state itself (`status="CANCELLED"`) — no manual
reconciliation needed. **Account is now fully clean: 0 open positions, 0 pending orders.**

```
pytest -q -> unaffected (no src/tests/ changed this entry)
```

**Files changed this entry**: `scripts/run_demo_execution_cancel_20260807_remaining_grid_orders.py`
(new), `var/order_state/*.json` (3 records transitioned to `CANCELLED`, local data only, not
tracked by git), this checkpoint doc.

## Step 6 — demo-to-live readiness checklist: done (2026-08-07)

Pure documentation, no code, no live/demo call of any kind. New
`docs/DEMO_TO_LIVE_READINESS_CHECKLIST.md` — 11 criteria, each traced to a specific real finding
from this project's own history (not generic best-practice boilerplate): the kill-switch's
still-placeholder threshold, the 30-trade minimum sample not yet reached, Step 7 not yet run, the
connection-model and `all_open()`-cost decisions still deferred, the retcode-10016 pattern, and the
credential-exposure fix already made. Each row states its current status as of today plus its
evidence source, so the table is checkable, not just aspirational.

**One explicit design decision made scoping it, per the user's own call**: the checklist does
**not** gate Live pilot scoping on demonstrated positive edge — it requires real forward
expectancy/drawdown to be measured and honestly reported (row 3/4), but a negative result doesn't
itself block scoping; matches Phase 9's own stated framing that this was never a
"run-it-forward-and-hope-it's-profitable" exercise. Whether to proceed despite a negative edge is
left as an explicit, informed decision for the future, separately-scoped Live pilot proposal
itself.

**Currently, most rows are NOT MET** — that's expected and honest, not a problem: this checklist
defines what "ready" looks like, it doesn't claim readiness now. Step 7 (the actual sustained
forward-test run) hasn't happened yet, and the kill-switch threshold is still Step 5's smoke-test
placeholder.

```
pytest -q -> unaffected (pure documentation, no src/tests/ changed)
```

**Files changed this entry**: `docs/DEMO_TO_LIVE_READINESS_CHECKLIST.md` (new), this checkpoint
doc, `AGENTS.md`.

## Status

**Steps 1–3 done** (locked parameter set; loss-based kill-switch guard, built and unit tested;
kill-switch wired into `pipeline/loop_control.py`'s `should_stop()`, proven end-to-end against a
real multi-cycle run with `DryRunExecutor`/mocks, deliberately not wired into the live script's
own call sites yet). 458 passed total, architecture tests still pass, no live/demo call made in
any of Steps 1–3. **Step 4 IN PROGRESS**: research done (real `get_deals` CSV shape, field list,
and the `position_id`-as-join-key confirmed by reading source, later extended with two wire-format
corrections — `type` is a raw int not a string, `time` needs explicit UTC attachment);
`StateStore.all_closed()`, the `Deal` domain model, `mt5_adapter/mcp_deal_history.py`'s reader,
`monitoring/live_performance.py`'s join/computation logic, and
`scripts/run_demo_execution_live_performance_monitor.py` are all now built, unit tested, and (the
script) **live-verified** — run for real against the demo connection with explicit go-ahead
(2026-08-06), one real read-only `get_deals` call, no order of any kind. **Real first result**:
grid 19 trades/−0.825 R expectancy/16.302 R drawdown, runner 13 trades/−0.418 R
expectancy/10.214 R drawdown, both directionally consistent with Phase 8's backtested findings
but neither past the 30-trade minimum-sample bar yet — a first honest read, not a validated one.
488 passed total, architecture tests still pass, no live/demo call made building any of Step 4's
code (only the deliberate, explicitly-approved live calls in the two follow-ups below). **Both
open items the live run surfaced are now resolved**: the 2 skipped tickets (`171648990`,
`171649461`) were root-caused via one real `get_orders` call — both cancelled grid LIMIT orders,
never filled, correctly skipped; the 5 unrecognized-magic trades were identified from local
records alone (no live call needed) — all `magic=79999` Phase 6/8 smoke tests, correctly excluded
from grid/runner's numbers, not a data problem. One structural, unfixed finding surfaced along the
way: this project's local `"CLOSED"` status conflates "filled then closed" with
"cancelled/expired unfilled" across several reconciliation scripts — flagged, not retroactively
fixed. **Known open gap, not blocking**: `close_reason` on live-built `ClosedTrade`s is a fixed
`"closed"` — a genuine SL-vs-TP breakdown would need `Deal.reason` (MT5's `ENUM_DEAL_REASON`),
not modeled yet since it wasn't in `client_history.py`'s documented field list; doesn't affect
`expectancy_r()`/`max_drawdown_r()`, which never read that field. **This closes out Step 4's
originally-scoped work AND both of its live-run follow-ups.** **Step 5's code-only half is now
BUILT** (2026-08-06, explicit go-ahead for the wiring, NOT for a live run): `should_stop()` is
wired into `scripts/run_demo_execution_pipeline_loop.py`'s two real call sites via a real,
fail-closed `_daily_loss_decision_for_cycle()`; `MAX_DAILY_LOSS` defaults to `None` (kill-switch
present but inert) since the real smoke-test threshold remains an open design point. 504 passed
total, architecture tests still pass, no live/demo call anywhere in building this.

**Step 5 is now DONE, live-verified, exit criteria met (2026-08-07).** Open design points
resolved: `MAX_DAILY_LOSS=0.01` (trips on the first net realized loss of any size — sidesteps
needing real commission-magnitude data), `RESET_HOUR_UTC=0` (kept Step 2's default). First live
attempt ran the full 12 cycles without tripping — not because nothing went wrong, but because of a
real, previously-unknown bug: `Deal.time` is broker server time (confirmed UTC+3) mislabeled as
UTC, root-caused precisely (sub-2-second precision, three independent cross-checks) via a read-only
follow-up probe. Fixed with a live-derived (not hardcoded) offset — `infer_deal_time_offset()` —
plus a wide-margin `get_deals` fetch window. A second live re-run surfaced a SECOND, independent
gap: the trusted-ticket set was sourced from `StateStore.all_closed()`, which only reflects tickets
this project explicitly closed itself (`record_closed()`'s only caller) — never a broker-side SL/TP
close, which is how the overwhelming majority of real closes happen. Fixed via new
`StateStore.all_records()` (every locally recorded ticket regardless of status), matching this
project's existing "never trust a stale local status field for a safety decision" discipline. Both
fixes verified directly against real live data (`realized_pnl_since_reset=-10.41`, correctly
breaching the threshold) before a third live attempt, which **met Step 5's exit criteria verbatim**:
one real observed trigger event (`Stopping before cycle 1: daily loss limit breached...`), loop
halted, verified via a live re-read, zero leftover unmanaged risk. 513 passed total (+9 across both
fixes), architecture tests still pass. Full session detail, including the abrupt (unexplained, not
chased further per the user's own call) kill of the second live attempt, in this doc's own Step 5
entries above. Leftover exposure from Step 5's live attempts has since been fully resolved
(same-day follow-ups, user-requested): 35 stale local records reconciled to `CLOSED` (no MCP
call), and the 3 remaining genuinely-live pending grid orders explicitly cancelled (retcode
`10009` each, verified absent). **Account is fully clean: 0 open positions, 0 pending orders.**

**Step 6 is now DONE (2026-08-07)**: `docs/DEMO_TO_LIVE_READINESS_CHECKLIST.md` — an 11-row,
evidence-traced checklist (not generic boilerplate) covering the kill-switch's still-placeholder
threshold, the not-yet-reached 30-trade minimum sample, Step 7 not yet run, the deferred
connection-model/`all_open()`-cost decisions, the retcode-10016 pattern, and the credential fix
already made. Explicitly does **not** gate Live pilot scoping on positive edge, per the user's own
call — requires the forward-vs-backtest drift to be honestly measured and reported either way, not
hidden or required positive. Most rows currently read NOT MET, which is the honest, expected state
before Step 7 has ever run — this checklist defines readiness, it doesn't claim it.

**Not done**: the unscoped "operational reliability hardening" design item (the connection-model
and `StateStore.all_open()`-cost decisions the checklist itself flags as still open), Step 7 (the
actual sustained forward-test run), and the Live pilot proposal itself — each its own separate,
later, explicit go-ahead/scoping, per this checklist's own stated boundaries. **Exact next smallest
step, whenever resumed**: pick one of those three to scope next, informed by the readiness
checklist's own NOT MET rows rather than guessed at fresh.
