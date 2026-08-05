# Checkpoint: Phase 8 — strategy research, edge validation, parameter tuning

Handoff doc for continuing this phase in a new session. Read `AGENTS.md` first for overall
project context, specifically the "Forward phases" section — Phase 8 is not one of this
project's original numbered phases (0–7); it's the user's own forward framing, formalized here
per the roadmap review that preceded this doc. Read `docs/PIPELINE_WIRING_CHECKPOINT.md`'s
final entries too: Phase 8 was explicitly gated on Phase 7's live/MCP-adjacent failure-testing
gap being closed or accepted, which it now is (Stage 3 Parts 1–2 live-verified, Part 3
explicitly accepted as an open risk).

## Goal

Unlike phases 0–7 (phases of *building* this codebase), Phase 8 is a phase of *running and
tuning* the strategy once built: does the grid/runner combination, as currently implemented,
have a real, positive, cost-net edge — and if any of its parameters should change, which ones
and to what, validated on data the tuning process never saw. No live trading is involved. This
phase is 100% research against historical market data; nothing here changes what a real cycle
does unless a later, separately-approved step decides to act on a finding.

## Scoping pass (before any code written) — read this before continuing

Grounded in the actual codebase, not designed from the phase name alone. Read
`strategy/grid.py`, `strategy/runner.py`, `strategy/guard.py`, `sizing/money.py`,
`risk/portfolio_guards.py`, `mt5_adapter/mcp_market_data.py`, and `domain/models.py`, and
grepped the whole `pipeline/`/`scripts/` tree for any existing backtest/tuning infrastructure,
before writing anything below.

**Tunable parameter surface today**: `GridStrategyConfig` (6 fields: `atr_period`,
`center_ema_period`, `center_mode`, `step_mult`, `min_step_points`, `sl_atr_mult`),
`RunnerStrategyConfig` (7 fields: `fast`, `slow`, `min_bars_floor`, `atr_period`, `sl_atr_mult`,
`tp_atr_mult`, `min_stop_distance_points`), `MoneyConfig` (7 fields, 3 lot-sizing modes:
`fixed`/`atr_scale`/`risk_percent`), `ExposureCaps` (`max_open_lots`, `budget_max_lots`). Several
current defaults are placeholders by their own module docstrings' admission — grid's
`sl_atr_mult=2.0` and every SL/TP field on `RunnerStrategyConfig` are documented as
"new, project-original, no legacy value, open to tuning," not validated choices.

**Guard (`strategy/guard.py`, `evaluate_guard()`) is unwired — confirmed by grep, and
DECIDED OUT OF SCOPE for this phase.** It's a real, ported, unit-tested EMA-based
partial-close/flatten exit rule, but it is never called anywhere in `pipeline/` or any live
script — every real cycle this project has ever run, including every live loop run, has
operated with no active exit-management logic beyond the static SL/TP set at order placement.
**User's explicit decision**: Phase 8 tunes and validates grid/runner exactly as they run
today, with no exit-guard, rather than wiring the guard in first. Reasoning this implies (not
independently re-litigated here, just recorded so a future session doesn't re-open it by
accident): wiring the guard into the pipeline is itself a pipeline-completeness change, closer
in kind to the "pipeline wiring" effort than to tuning already-deployed behavior — folding it in
here would mean Phase 8 spends its early budget on a wiring task instead of research, and would
make every backtest result depend on a not-yet-live-verified new pipeline behavior. Revisit
whether to wire the guard as a *separate*, explicitly-scoped effort — before or after Phase 8,
either order is legitimate, but not silently inside it.

**No backtest, tuning, or walk-forward infrastructure exists anywhere in this codebase.** Fully
greenfield — confirmed, not assumed.

**No cached historical market data exists anywhere in this repo.** Every prior real
market-data read in this project's history (dry-run pipeline, every live cycle) has been a live
pull for immediate use, never persisted.

**No Phase 0 legacy-audit document survives** with recorded legacy strategy performance —
`AGENTS.md` notes Phase 0 "produced no code, by nature." Phase 8 establishes a new performance
baseline; it does not validate against a known legacy track record, because none exists in
writing.

**Cost data available today**: `MarketBar`/`SymbolInfo` both carry `spread` (points). No
commission/swap field exists anywhere in `domain/models.py`. Whether one is obtainable via any
MCP tool (e.g. a deal-history tool) is an open research question — not yet checked, first item
of Step 3 below.

## Open questions, decided or still open

1. **Guard wiring — DECIDED**: out of scope for Phase 8 (see above).
2. **What "edge" means, concretely — DECIDED (Step 1)**: **per-trade expectancy in R-multiples,
   net of transaction costs, computed separately per strategy** (grid and runner each must
   independently clear the bar — never blended, since blending could hide one strategy's losing
   edge behind the other's positive one). R = the strategy's own actual per-trade risk distance
   at the time of that trade (`|entry_price − sl_price|`, using the real, already-computed SL each
   strategy attaches — grid's ATR-scaled `sl_price`, runner's `compute_stop_distances()` output —
   not a fixed nominal value); a trade's result is expressed as its P&L divided by R, and expected
   R is chosen over profit factor/Sharpe/CAGR specifically because it normalizes across grid's and
   runner's very different stop widths, which none of those alternatives do (recorded reasoning,
   not just the conclusion, in case a later session needs to revisit this). **Required companion
   metric, not merely informative**: max drawdown, expressed in the same R units (peak-to-trough
   equity decline) — expectancy alone says nothing about the ride, and a strategy with positive
   expectancy but a catastrophic drawdown path is not validated by this metric alone. **Required
   minimum sample size**: no "edge validated" claim (positive or negative) is made below **30
   trades per strategy** in the tested window — expectancy from fewer trades is statistically too
   noisy to trust either way; 50+ preferred before treating a positive result as reasonably
   reliable, not just a hard floor at 30. Cost inputs (spread now, commission/swap if Step 3 finds
   them available) feed directly into each trade's P&L before it's divided by R — this metric's
   definition doesn't change once Step 3 resolves question 4 below, only its cost inputs get more
   complete.
3. **Data source — OPEN, but direction is clear**: a local historical-bar cache, seeded by one
   read-only live pull per symbol/timeframe (same risk category as the MCP disconnect effort's
   read-only calls — no order, no `executor`, no credential exposure beyond what any other real
   script already does). How much real history the demo terminal actually retains for BTCUSD is
   unknown until checked — this is Step 2 below, deliberately sequenced before any engine design.
4. **Cost-model completeness — OPEN**: is commission/swap available anywhere, or is spread-only
   the honest ceiling of what this project can model? Step 3 below.

## Proposed sub-steps, smallest/lowest-risk first

Numbered as steps within this doc (matching every other checkpoint doc's convention), not as
sub-phases with their own numbers — Phase 8 is one phase, tracked here.

| Step | Scope | Entry criteria | Exit criteria | Key risk |
|---|---|---|---|---|
| 1 | Decide the edge metric (question 2) | This doc reviewed | A single, written target metric | None — pure decision — **DONE**, see question 2 above |
| 2 | Historical data acquisition + local cache | Step 1 done | Reusable, tested loader; real data actually pulled and cached for ≥1 symbol/timeframe, live-verified once | MT5 may retain less history than hoped — discover this **first**, before designing anything around an assumed depth |
| 3 | Cost-model research (question 4) + pure backtest/replay engine, reusing existing strategy/risk/order_planning code untouched | Step 2 has real data | Deterministic trade log + equity curve from real historical data, reviewed before being treated as meaningful | **Look-ahead bias** — the single most common, most dangerous backtest-engine bug class; needs explicit fill-logic tests proving no future bar ever influences a past decision, not just informal review |
| 4 | Transaction-cost / stress modeling | Step 3 engine trusted | Sensitivity table (expectancy at 1x/2x/5x observed spread) | Cost model may be incomplete per question 4's answer |
| 5 | Parameter tuning | Steps 3–4 done | A justified parameter set (or explicit "no change needed"), sweep results archived, not just a black-box "best" number | **Overfitting to one historical window** — the single biggest intellectual risk of this whole phase; requires a train/test split, never tuning against the same window used for Step 6 |
| 6 | Walk-forward / out-of-sample validation | Step 5 produces a candidate | Honest report of the *locked* candidate's performance on a disjoint, later window never used in Step 5 — a disappointing result here is a valid, useful phase outcome, not a failed phase | None beyond Step 5's — this step exists specifically to catch Step 5 overfitting |
| 7 (optional) | Regime analysis | Only if Steps 5–6 show regime-dependence worth investigating | Informs whether a regime filter is worth a future, separate proposal | Lowest priority — may be dropped entirely if 5–6 already show robustness across the tested window |

## Explicitly not in Phase 8

- No live/demo order execution changes of any kind.
- No production strategy-code change unless Steps 5–6 justify one — and even then, making that
  change is its own separate, explicitly-approved step, not automatic.
- Does not wire `evaluate_guard()` into the pipeline (see decision above).
- Does not touch Phase 9 or live-pilot readiness criteria.
- Does not require or assume any particular symbol beyond what's already traded (`BTCUSD`) —
  expanding to `EURUSD`/`XAUUSD` (named in the live-pilot framing) is Phase 9/live-pilot
  territory, not decided or needed here.

## Exact next smallest task

**Step 1 is done** (edge metric decided, see question 2 above). Step 2's very first action is
next: one read-only live pull to find out how much real `BTCUSD` history this demo connection
can actually retrieve. That number determines whether Steps 3–7 are viable as scoped above or
need adjusting — worth learning before designing the backtest engine around a guess.

**Live testing remains paused for anything order-related — this entire phase is research-only
and read-only by design, but any real MCP call (including a plain historical-data pull) still
needs its own explicit go-ahead, same standing rule as every prior step in this project.**

**Continuation prompt for a new session**: "Read AGENTS.md and
docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md (Step 1 is done — edge metric decided: per-trade
expectancy in R-multiples, per strategy, with a max-drawdown companion and a 30-trade minimum
sample floor), confirm git status is clean at the latest commit, then ask me what to do next —
Step 2's first action (a read-only live pull to discover real BTCUSD history depth) is the
smallest next step, but still needs its own explicit go-ahead before any live call."
