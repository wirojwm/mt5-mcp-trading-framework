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

## Status

**Step 1 done.** Locked parameter set documented and verified against the current codebase (see
"Step 1 — locked parameter set: done" above) — the single source of truth for the rest of this
effort. No code changed, no live/demo call made. **Next smallest step: Step 2** — build the
loss-based kill-switch guard (unit tested against synthetic P&L data only, not yet wired into the
live loop). Needs its own explicit go-ahead before any code is written, same standing rule as
always, though Step 2 itself makes no live call either.
