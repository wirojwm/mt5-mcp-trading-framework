# Checkpoint: Live Pilot Preparation — remaining Phase 9 work classified, offline items completed (2026-08-11, home machine)

Home-machine session, continuing after run #10 (work machine) and this same day's earlier
operational-reliability re-affirmation (`docs/OPERATIONAL_RELIABILITY_HARDENING_CHECKPOINT.md`,
commit `3ad85fc`). Triggered by a concrete constraint: this machine's `var/order_state` is
machine-local and gitignored by design (never synced from the work machine), and no network
path/mapped drive to the work machine exists — so nothing that depends on run #10's real local
`StateStore` records can be completed here tonight. This entry inventories everything Phase 9 still
needs, separates what genuinely needs that missing data from what doesn't, and completes every item
that doesn't. **No MCP/MT5 call of any kind was made producing this entry. No order was placed,
modified, cancelled, or closed. Step 7 was not relaunched.**

## Two distinct kinds of "blocked", not one

This entry uses three buckets per the session's own request — **A** (completed fully on this
machine tonight), **B** (prepared here, needs data that only exists elsewhere for final
validation), **C** (must wait for the work machine specifically) — but two materially different
things get lumped into "needs data from elsewhere" if not separated explicitly:

1. **Work-machine-only blocker**: anything that reads local `StateStore` (`var/order_state`) —
   that data was produced on, and only exists on, the work machine. No amount of authorization
   fixes this from here; it requires either running the work there, or physically moving the data
   (see the cross-machine workflow proposal below).
2. **Live-call blocker**: anything that needs a fresh read from the real MT5/broker connection
   (e.g. `get_symbol_info` for a not-yet-researched symbol). This is **not** work-machine-specific
   — it's blocked tonight only because this session's own instructions bar any live MCP/MT5 call.
   It is fully doable on *this* machine once that constraint lifts.

Items below are tagged with which kind applies, where relevant.

## Phase 9 remaining task inventory

### Stage A — Complete Step 7 acceptance (`docs/PHASE9_FORWARD_TEST_CHECKPOINT.md`, roadmap A)

| Item | Status | Bucket |
|---|---|---|
| 1. Human decision on run #10's leftover exposure | **Already resolved** (2026-08-11, home machine) — manual close, account verified flat | done |
| 2. Re-run `run_demo_execution_live_performance_monitor.py` against run #10's real trades | **Done (2026-08-13, work machine)** — real read: grid 90 trades / −0.566 R / 51.332 R drawdown / 36.7% win rate / 0.199 profit factor; runner 28 trades / −0.958 R / 31.586 R drawdown / 14.3% win rate / 0.182 profit factor | done |
| 3. Update readiness checklist rows 3–7 | **All 7 rows now MET or PARTIAL with reasoning** — rows 5–7 done prior session; rows 3–4 updated 2026-08-13 with item 2's real numbers | done |
| 4. Reconcile the 130-record stale `StateStore` backlog | **Done (2026-08-13, work machine)** — bulk classify-by-deal-history pass (`scripts/run_demo_execution_reconcile_20260813_backlog.py`): 136 stale records found (grew from 130 since 2026-08-11), 0 still live, 115 reconciled to `CLOSED`, 21 to `CANCELLED`. Re-verified: 0 stale `OPEN`/`OPEN_UNPROTECTED` records remain | done |
| 5. Decide whether a real kill-switch trigger at Step 7 scale ($50 threshold) is required for acceptance | **Decided tonight** — see "Kill-switch smoke test" section below | **A**, done |

### Stage B — Operational reliability hardening

**Already closed** (prior session tonight, commit `3ad85fc`) — not remaining work.

### Stage C — Demo-to-live readiness criteria

All 11 rows now have a final status except rows 3–4 (blocked, same as Stage A item 2/3). Nothing
further to do here until those two rows have real numbers — **C**.

### Stage D — Locked demo forward testing

Formalized into an actual plan tonight (see "Locked demo forward-test plan" below) — the plan
itself is **A**, done. Executing it (repeated live Step-7-scale runs) is inherently live trading
activity, out of scope for this or any session without its own explicit go-ahead — not blocked by
data location, blocked by this project's own live-testing-pause discipline.

### Live Pilot Preparation framework (`docs/PHASE9_FORWARD_TEST_CHECKPOINT.md`'s existing section)

| Sub-item | Status | Bucket |
|---|---|---|
| Symbol decision (EURUSD vs XAUUSD) | Formalized into explicit criteria tonight (below); the *decision itself* still needs live `get_symbol_info` research | criteria **A**; real validation is a **live-call blocker**, not work-machine-specific |
| Minimum-lot / exposure framework | Framework designed tonight (below); real values need live symbol data | **A** design; **live-call blocker** for real numbers |
| Initial deposit derivation | Methodology specified tonight (mirrors how `MAX_DAILY_LOSS=50` was derived) | **A** |
| Daily loss / shutdown / rollback rules | Formalized tonight (below) | **A** |
| Human-approval gates | Enumerated explicitly tonight (below) | **A** |

## Code completed tonight (category A, no fabricated data, no live call)

Extends the live-performance report's actual capability so that when it *is* run against real
data, its output already matches the spec below. All new code is pure functions over
already-existing data shapes (`ClosedTrade`, `LocalOrderRecord`) — no adapter, no MCP import, no
execution-path change (`mcp_order_executor.py`, `grid.py`, `runner.py` untouched).

- **`backtest/metrics.py`**: added `win_rate()` and `profit_factor()`, same R-multiple convention
  as `expectancy_r()`/`max_drawdown_r()`. 7 new unit tests (synthetic `ClosedTrade` fixtures, same
  pattern the existing tests already use).
- **`monitoring/live_performance.py`**: added `compute_slippage()` — signed requested-vs-executed
  price slippage per `system_owned` `LocalOrderRecord` (positive = unfavorable), skipping
  `manual_adoption` records and anything missing a comparable price rather than fabricating a
  value. 8 new unit tests.
- **`scripts/run_demo_execution_live_performance_monitor.py`**: now prints `win_rate`,
  `profit_factor`, and an average MARKET-order slippage line per strategy/session. Still
  read-only, still no `executor` reference — same risk category as before, unchanged.

```
pytest -q -> 556 passed (was 541; +15 new tests, all offline/synthetic-data unit tests)
```

## Live-performance report specification

The metrics this session was asked to ensure are collectable, and their real status after
verifying against the actual code (not assumed):

| Metric | Status | Source |
|---|---|---|
| PnL | **Implemented** (`notional_pnl`, real broker $ once joined via `build_closed_trades()`) | `monitoring/live_performance.py` |
| Drawdown | **Implemented**, R-multiple units (`max_drawdown_r`) — this project's authoritative unit, not $ | `backtest/metrics.py` |
| Expectancy | **Implemented** (`expectancy_r`) | `backtest/metrics.py` |
| Win rate | **Implemented tonight** (`win_rate`) | `backtest/metrics.py` |
| Profit factor | **Implemented tonight** (`profit_factor`) | `backtest/metrics.py` |
| Trade count | **Implemented** (`len(trades)`, `has_minimum_sample`) | `backtest/metrics.py` |
| Slippage | **Implemented tonight** (`compute_slippage`) — requested vs executed price, MARKET orders meaningful | `monitoring/live_performance.py` |
| Rejected orders | **Not implemented — genuine data gap, verified by reading `mcp_order_executor.py`**: a rejected submission raises `OrderSubmissionError` and is *deliberately never written to `StateStore`* (`record_submission()` is only called after a confirmed-done retcode — see that file's own module docstring, line ~74). Today it exists only as a transient `logger.warning` line, not queryable after the fact. | none — see proposal below |
| Safety stops (kill-switch trips) | **Not implemented — same gap.** `pipeline/loop_control.should_stop()` and `risk/daily_loss_guard.check_daily_loss_limit()` are both pure decision functions with no persistence; a trip is only visible in that session's own text log while it's running. | none — see proposal below |
| Reconciliation errors | **Not implemented — same gap.** `state/reconcile.reconcile()` is pure/stateless; every `run_demo_execution_reconcile_*.py` script prints its `ReconciliationReport` to console and exits — nothing persists across runs today. | none — see proposal below |

**Why the last three weren't built tonight, not just specified**: closing them for real requires
adding a persistence path somewhere in execution-adjacent code (`mcp_order_executor.py`,
`loop_control.py`, or a reconciliation script) — a materially bigger, riskier change than a pure
reporting function, and exactly the kind of "half-finished implementation under time pressure" this
project's own conventions warn against. **Proposed minimal shape, not built**: a single
append-only JSONL event log (`var/events.jsonl`, same machine-local/gitignored treatment as
`var/order_state`) with one line per rejection/kill-switch-trip/reconciliation-run, each carrying
enough to answer "how many, when, why" without needing to replay full session logs. This is its
own separately-scoped, explicitly-approved effort — flagged here, not started.

## Acceptance / rejection criteria (Step 7 forward-test sessions, not the strategy's edge)

Per `docs/DEMO_TO_LIVE_READINESS_CHECKLIST.md`'s own explicit, standing decision, this project does
**not** gate on positive backtested/forward edge — a negative or unproven expectancy is a valid,
reportable outcome, not a failure. Acceptance criteria below are about the *session's own
operational integrity*, not its P&L sign:

**A Step 7-scale session counts as ACCEPTED (operationally) when:**
- It reaches its own stop condition cleanly — cycle limit, time limit, or an explicit stop-file —
  never an unhandled exception or an abrupt process kill.
- Zero unresolved `OPEN_UNPROTECTED` records remain at session end.
- Zero `unknown_real` reconciliation trips occur without being explained and resolved before the
  next cycle.
- A live-performance report is produced afterward (even reporting a negative result) and the
  readiness checklist is updated in the same session/commit — an unreported session is not a
  complete one, regardless of what happened live.

**A session counts as REJECTED / needs investigation before being trusted as evidence when:**
- It ends via an unhandled exception or unexplained process death (as runs #2, #4, and #6 did,
  historically — each root-caused and fixed before being recounted as evidence).
- It leaves an `OPEN_UNPROTECTED` position unresolved past the session's own end.
- A kill-switch trip occurs and is not reviewed before any further code-initiated action (carried
  forward, unweakened, from the Live Pilot Preparation framework's own stated rule).

Run #10 meets every ACCEPTED criterion above per what's already documented in `AGENTS.md` — this
is a restatement of a bar it already clears, not a new hurdle it must re-clear.

## Locked demo forward-test plan (Stage D, formalized)

**Status update, 2026-08-13 end-of-day**: deliberately PAUSED, not just unstarted. Stage A/B/C
(execution readiness) closed the same day, but the same session's Phase 8 continuation
investigation (`docs/RUNNER_LIVE_VS_BACKTEST_DIVERGENCE_CHECKPOINT.md`) found both current
production strategies still show confirmed negative edge (grid: 169 trades, −0.683 R; runner:
statistically indistinguishable from random-direction chance, classification C).

**Update, 2026-08-14**: the M15/H1 mean-reversion lead named below as "the current best
candidate, not yet tested" has since been tested and closed negative — Experiment 5
(`docs/RUNNER_LIVE_VS_BACKTEST_DIVERGENCE_CHECKPOINT.md`) found real-cost expectancy negative in
all 6 tested window x timeframe cells, no exception, for both M15 and H1. A parallel, separately
scoped research pass on XAUUSD (`docs/XAUUSD_SIGNAL_EDGE_CHECKPOINT.md`, satisfying this
document's own criterion 1) reached the same negative conclusion there too. **There is currently
no open candidate strategy/instrument combination with credible, cost-surviving edge** — both
signal-search lines this project has run are closed. Building a "locked parameter" track record
now would lock in a strategy already shown to lack edge — Stage D should not resume until either
a new strategy/instrument combination has credible, out-of-sample-confirmed positive edge, or an
explicit, separate decision is made to proceed despite negative edge for a different stated
reason (e.g. as a pure execution/operations exercise, not a profit expectation). This is a
standing pause, same discipline as [[project_live_testing_paused]] — revisit only with a fresh,
explicit go-ahead citing new edge evidence or an explicit reason to proceed without it.

Phase 9's own stated longer-horizon purpose is a real track record, not one data point. Proposed
structure for when further live sessions are separately authorized:

- **Cadence**: repeated Step-7-scale (30-cycle / 180-minute-cap) sessions, spaced across different
  sessions/days — deliberately not back-to-back, so sessions sample different real market
  conditions rather than one contiguous window that happens to look like several runs.
- **Per-run record**: this checkpoint doc's acceptance criteria (above) applied per run, plus that
  run's live-performance report (once rows 3–4's tooling can actually run against real data).
- **Cross-run tracking**: a running log of `{run id, date, cycles completed, accepted/rejected,
  expectancy_r, max_drawdown_r, win_rate, profit_factor, trade count, incidents}` — not built
  tonight (depends on real per-run data this machine doesn't have), but this is the shape the
  Stage A tooling above already produces per-run, so aggregating it later is a formatting exercise,
  not new engineering.
- **Pause trigger**: any REJECTED run (per the criteria above) pauses the series until root-caused
  and fixed — exactly the discipline every prior incident in this project has already followed,
  formalized here as a standing rule rather than an ad hoc response each time.
- **"Enough runs" is a judgment call, not a formula** (the roadmap doc's own words) — as a
  reference point only, this project's own `MIN_TRADES_FOR_A_CLAIM=30` statistical floor suggests
  erring toward more runs, not fewer, before treating any forward-vs-backtest comparison as
  settled; a single-digit run count should be treated as preliminary regardless of what it shows.

## Live Pilot Preparation framework — formalized

### EURUSD vs XAUUSD — explicit decision criteria (not just a recommendation)

The existing framework already recommends XAUUSD with a caveat (structural similarity to BTCUSD's
regime). Formalized as criteria the actual decision must satisfy before either symbol is approved:

1. A dedicated research pass (Phase-8-equivalent: real backtest + at least one live-verified
   session) exists for the chosen symbol specifically — BTCUSD's tuned parameters transferring by
   assumption is explicitly disqualifying.
2. The symbol's real broker minimum-stop-distance, spread, and point value are read live
   (`get_symbol_info`) and shown compatible with `min_stop_distance_fraction_of_price=0.01`
   without producing a distorted risk:reward ratio (the concrete failure mode already identified
   for EURUSD: pip-level minimums under this fraction floor would set absurdly wide "minimum"
   stops).
3. The symbol's typical daily ATR-to-price ratio is structurally close to BTCUSD's regime (high
   absolute volatility relative to a lot's $ value), not merely "a different, plausible-sounding
   instrument."
4. Whichever symbol is chosen, the other is explicitly rejected with a recorded reason — not
   silently dropped.

**Status**: criteria defined; satisfying criterion 2 needs a live `get_symbol_info` call — a
live-call blocker, not a work-machine blocker, doable on this machine once authorized.

**Criteria 2 and 3 data gathered (2026-08-13, work machine, explicit go-ahead)** — new read-only
script `scripts/run_demo_execution_xauusd_symbol_research.py` (`get_symbols`, `get_symbol_info`,
`get_candles_latest` M1/D1 for both XAUUSD and BTCUSD, all `READ_ONLY`-classified, no `executor`
reference, no order of any kind). First resolved the real broker symbol name via
`get_symbols(group="*XAU*")` — this broker (ThinkMarkets-Demo) exposes it as literally `XAUUSD`,
no quirk to work around. Real, same-session, directly-comparable read:

| | XAUUSD | BTCUSD (this project's only live-validated regime) |
|---|---|---|
| price | $4,408.47 | $63,524.85 |
| spread | 19 points (~0.0043% of price) | 1,632 points (~0.0257% of price) |
| broker `trade_stops_level` | 1 point = 0.0002% of price | 10 points = 0.0002% of price |
| M1 ATR(14) | 0.0209% of price | 0.0267% of price |
| D1 ATR(14) | 2.0682% of price | 1.8294% of price |
| `min_stop_distance_fraction_of_price=0.01` design floor vs. broker minimum | floor dominates (deliberate design choice, not a collision) | floor dominates (same, already proven live) |
| `volume_min`/`volume_max`/`volume_step` | 0.01 / 10.0 / 0.01 | 0.01 / 5.0 / 0.01 |

**Reading**: criterion 2 is satisfied — XAUUSD's broker-side minimum stop distance is negligible
(0.0002% of price, identical in relative terms to BTCUSD's), so the 1%-of-price design floor stays
the deliberate binding constraint on both symbols, exactly the situation already proven live for
BTCUSD, not the EURUSD failure mode (pip-level minimum colliding with/dominating the floor).
Criterion 3 is satisfied — XAUUSD's M1 and D1 ATR-to-price ratios are structurally close to
BTCUSD's (M1: 0.021% vs 0.027%; D1: 2.07% vs 1.83%), a genuine "high absolute volatility relative
to price" match, not a superficial resemblance. XAUUSD's spread is also proportionally tighter
than BTCUSD's on this broker (~0.004% vs ~0.026% of price), a cost-efficiency point in its favor,
not one of the four decision criteria itself.

**Not satisfied by this read**: criterion 1 (a dedicated Phase-8-equivalent backtest +
live-verified session specifically for XAUUSD — none exists yet, still needed before any real
sizing/parameter is trusted) and criterion 4 (an explicit recorded decision rejecting EURUSD,
which is a human call, not something this data-gathering script should pre-empt). This entry is
evidence toward the decision, not the decision itself — no symbol has been approved by this
session.

### Minimum-lot and exposure framework

- **Procedure, not a number**: at Live Pilot scoping time, read the chosen symbol's real
  `get_symbol_info().volume_min` live — never assume it matches BTCUSD's or any other symbol's
  minimum.
- **Initial position size = broker minimum**, unconditionally, for the pilot's first session(s) —
  same "start at the floor" discipline this project has used for every new capability so far
  (Phase 6's first MARKET order, Phase 7's first LIMIT order, Step 7's own "modest step-up" cycle
  count).
- **Exposure cap re-derivation**: `ExposureCaps`' existing mechanism is reused structurally, but
  its lot-based ceiling (BTCUSD's `0.06 lots`) must be re-derived from the new symbol's own $
  risk-per-trade at the broker minimum, times the same number of concurrent grid+runner positions
  this project already caps at today — not copied as a raw number.

### Daily loss / shutdown / rollback rules

- **Daily loss limit derivation**: same method already used for BTCUSD's `MAX_DAILY_LOSS=50.0` —
  real $ risk-per-trade at the new symbol's minimum lot, scaled by a held-out backtested/forward
  max-drawdown-in-R figure for that symbol specifically (not reused from BTCUSD/Phase 8).
- **Shutdown**: reuse the proven stop-file + `Ctrl+C` mechanisms unchanged; the kill-switch
  (`check_daily_loss_limit`) must trip and halt the loop exactly as today, with no auto-resume.
- **Rollback**: if a Live Pilot session needs to be aborted mid-flight, the rollback sequence is:
  (1) stop-file / `Ctrl+C` to halt the loop cleanly, (2) a human-reviewed decision on any open
  exposure — close now or let it resolve naturally, same discipline as every demo run to date,
  (3) revert `MT5_ACCOUNT_KIND`/execution mode back to `DEMO_EXECUTION` before any further
  automated session, (4) if a code change is implicated, `git revert` it rather than hotfixing
  live. None of this is new mechanism — it's the existing demo discipline, restated so Live Pilot
  doesn't have to invent it under pressure later.

### Explicit human-approval gates before real-money execution

Carried forward from the existing framework, made into an explicit numbered sequence:

1. Approval to begin the symbol-specific research pass (still demo/live-read only, no real
   money).
2. Approval to write any real-order-submitting `LIVE`-mode code (does not exist in this codebase
   today — `config/settings.py` has no `LIVE` value).
3. Approval of that code's first-ever run, at any scale.
4. Approval before **every individual session's launch** thereafter — no standing authorization,
   ever, matching `DEMO_EXECUTION`'s existing discipline exactly.
5. Approval before any position-size increase beyond the initial broker-minimum floor.

## Kill-switch smoke test — necessity review

**Classification: RECOMMENDED, not REQUIRED.**

- Readiness checklist row 1 (deliberate, non-placeholder threshold) is MET — `MAX_DAILY_LOSS=50.0`
  was a real, derived choice, not a smoke-test leftover.
- Readiness checklist row 2 (kill-switch proven to trip on a real, live-observed breach) is
  **already MET**, from Step 5 (2026-08-07) — a real live run hit the mechanism and halted
  correctly. Nothing in the checklist requires re-proving the trip specifically at today's `$50`
  value; the mechanism (constant compared via simple arithmetic) is exactly the kind of change
  very unlikely to silently break between a `$0.01` proof and a `$50.0` one.
- The roadmap's own text explicitly allows "accepted as unproven at Step 7 scale" as a legitimate
  closure path for this specific question — it was never framed as a hard blocker.
- **Why RECOMMENDED anyway**: it's cheap (1–2 cycles, minutes), and it would be the first time the
  kill-switch trips inside the *actual* `run_demo_execution_pipeline_loop.py` production path at
  Step 7 scale rather than Step 5's earlier wiring — real confidence before Live Pilot scoping ever
  begins, at negligible cost.
- **Not run tonight** — it is itself a live-order-adjacent action (a real, if tiny, live session)
  and needs its own explicit go-ahead, same as every prior live step in this project. Not part of
  this entry's scope.

## Cross-machine StateStore workflow — proposal only, not implemented

**Problem**: `var/order_state` is deliberately gitignored — one authoritative writer per machine,
no merge/ownership-conflict risk across concurrent or interleaved sessions. That correctness
property is exactly what makes tonight's rows 3–4/backlog work impossible from here. A safe
cross-machine workflow needs to add visibility without weakening that property.

**Design principles for a future, separately-approved effort:**

1. **One-way export only, never sync or merge.** The producing machine's `StateStore` stays the
   sole write-authoritative copy of its own tickets, permanently.
2. **A dedicated export script** (not built tonight): reads `state_store.all_records()`
   (read-only) and serializes to one portable file (JSON/JSONL) carrying a schema version,
   generated-at timestamp, and source-machine identifier. Purely additive — never touches
   `var/order_state` itself.
3. **Import is read-only and clearly labeled.** The receiving machine's reporting tools (e.g. the
   live-performance monitor) would gain an optional `--snapshot <path>` mode that reads an
   imported file *instead of* local `var/order_state` — never merges it in, never writes it back.
   Any report built this way must print its own provenance ("SNAPSHOT from <machine> as of
   <timestamp> — not this machine's live state") so it's never mistaken for real-time local truth.
4. **Never feeds ownership/safety decisions.** Reconciliation, `ExposureCaps`, duplicate-order
   guards, and the kill-switch's `trusted_position_ids` must keep reading only the *local*
   machine's own live MT5 account plus its own local `StateStore` — an imported snapshot must
   never influence what orders get submitted, cancelled, or modified anywhere. This is the load-
   bearing safeguard: visibility is the only thing this workflow is for.
5. **Transport stays manual and out-of-band, never git.** USB drive, remote-desktop file copy, or
   a private (non-git) sync folder — explicitly not this repository. Git history is permanent and
   effectively public; `StateStore` records carry real trade tickets/timestamps/comments that
   don't belong there, and a committed snapshot invites being mistaken for current truth long
   after it's stale.

Not implemented, not scoped further than this proposal — a future, separately-approved effort per
this project's standing discipline.

## Files changed this entry

`src/mt5_mcp_trading/backtest/metrics.py`, `src/mt5_mcp_trading/monitoring/live_performance.py`,
`scripts/run_demo_execution_live_performance_monitor.py`, `tests/unit/test_backtest_metrics.py`,
`tests/unit/test_monitoring_live_performance.py`, this checkpoint doc, `AGENTS.md`.

```
pytest -q -> 556 passed
```
