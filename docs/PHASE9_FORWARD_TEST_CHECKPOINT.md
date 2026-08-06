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

## Status

**Steps 1–3 done** (locked parameter set; loss-based kill-switch guard, built and unit tested;
kill-switch wired into `pipeline/loop_control.py`'s `should_stop()`, proven end-to-end against a
real multi-cycle run with `DryRunExecutor`/mocks, deliberately not wired into the live script's
own call sites yet). 458 passed total, architecture tests still pass, no live/demo call made in
any of Steps 1–3. **Step 4 IN PROGRESS**: research only (see above) — real `get_deals` CSV shape,
field list, and the `position_id`-as-join-key confirmed by reading source; nothing built yet, no
file created or modified in `src/`/`tests/` this entry, no live/demo call made. **Exact next
smallest step**: build `StateStore.all_closed()` first (the smallest, purely-local piece, no
adapter/live dependency at all), then the `Deal` domain model and
`mt5_adapter/mcp_deal_history.py` reader (unit-tested against a stub `McpClient`, same pattern as
`test_mt5_adapter_mcp_market_data.py` — no live call), then the pure
`monitoring/live_performance.py` join/computation logic (unit-tested against synthetic
`StateStore` records + synthetic `Deal` objects — no live call), and only after all of that is
built and tested, the read-only monitor script itself — whose one real `get_deals` call remains
its own explicit go-ahead moment, not assumed pre-approved. Live-loop wiring (actually connecting
this to `scripts/run_demo_execution_pipeline_loop.py`'s `should_stop()` call sites) remains
deferred regardless — that was never Step 4's job, and stays out of scope until its own
explicitly-approved step.
