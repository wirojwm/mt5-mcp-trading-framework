# Strategy Validation Framework

A strategy-agnostic lifecycle for taking a trading idea from hypothesis to (possibly) a small
real-money pilot, extracted from the discipline this repository actually practiced across its
grid/runner BTCUSD work, the XAUUSD replication effort, and the session-seasonality research
thread. **This document does not assume any particular market, instrument, timeframe, or
strategy family.** Every concrete figure, config name, or file path below is drawn from this
repository and is marked as an example — copy the *process*, not the numbers.

Use alongside `docs/STRATEGY_RESEARCH_TEMPLATE.md`, the fill-in companion to this framework: one
template instance per strategy/instrument/timeframe combination under research.

**The discipline this framework exists to protect, stated once up front because every section
below is an application of it:**
- Evidence before adoption — no config, threshold, or parameter goes into a "current best"
  state on the strength of one favorable result.
- Fail closed on ambiguous state — anything the system cannot positively attribute to itself
  (a broker position, a statistical result, a live/backtest match) is treated as unsafe/unproven
  until proven otherwise, never assumed benign to keep things moving.
- Explicit approval before any live/demo order-affecting action — automation does not grant
  itself permission to escalate scope.
- No parameter adoption from one favorable backtest — every candidate needs an independent,
  not-used-for-tuning window before it counts as evidence.
- No live pilot without both execution readiness AND strategy-edge readiness — a working system
  is not the same thing as a system worth trusting with money.

---

## A. Strategy hypothesis and edge definition

Every strategy under research must have a written answer to each of the following before any
code is built. If a field can't be filled in, that's a sign the idea isn't ready for research
yet, not a formality to skip.

| Field | What it must state |
|---|---|
| Market / instrument | The specific tradeable symbol(s) — not "crypto" or "FX", the actual ticker(s) |
| Timeframe | The bar interval the signal is computed on (may differ from the execution cadence) |
| Strategy class | Directional / mean-reversion / breakout / arbitrage / carry / market-making / other — named explicitly |
| Economic or statistical hypothesis | *Why* this should work — a mechanism, not just a pattern ("liquidity thins in session X, causing overshoot" is a hypothesis; "the line goes up after three red bars" is a pattern in search of one) |
| Expected source of edge | Information advantage, structural/liquidity advantage, behavioral bias, risk premium, etc. |
| Entry logic | Exact, mechanical, reproducible from the written description alone |
| Exit logic | Same — including what happens on ambiguous same-bar SL/TP overlap, if relevant |
| Holding-period expectation | Order of magnitude (seconds, bars, hours, days) — sets what "trading-relevant horizon" means for this strategy |
| Risk/reward structure | The R-multiple or equivalent shape (e.g. fixed 2:1 bracket) and its own breakeven math |
| Expected market regime | Trending / ranging / high-vol / low-vol / regime-agnostic — stated up front, not inferred after seeing results |
| Invalidation conditions | What observation(s) would make you conclude the hypothesis is wrong, decided *before* looking at results |

### Distinguish these four things explicitly — they are not synonyms

Conflating these is the single most common way research self-deceives. Every result must be
labeled with which of these four it actually demonstrates:

1. **Statistical structure** — a measurable, non-random property of the price series itself
   (serial correlation, variance-ratio departure, excess/deficit of sign reversals, a seasonal
   pattern). Says nothing about whether it's tradeable.
2. **Trading signal** — a mechanical rule that converts structure (or any other input) into a
   directional/sizing decision. A signal can exist with zero underlying structure (e.g. a coin
   flip is a valid "signal", just an uninformative one).
3. **Trading edge** — a signal whose *pre-cost* expectancy beats an appropriate baseline
   (random-direction, unconditional, buy-and-hold) by a margin that survives basic scrutiny
   (sample size, cross-window consistency). Structure is necessary but not sufficient for edge;
   a signal can also show apparent edge on one window by chance without any real structure behind
   it (this is what pre-registration and cross-window testing exist to catch).
4. **Profitable strategy after costs** — edge that survives spread, slippage, commission, and
   any execution friction, measured directly (not estimated), on data not used to find the edge.

**Statistical significance alone is not sufficient evidence of economic edge.** A result can be
statistically significant (a real, non-random departure from chance) and still be economically
worthless — either because its magnitude is too small to trade profitably, or because it
represents structure with no valid trading signal built on top of it yet, or because a valid
signal built on it still loses to transaction costs.

> **Example (this repo).** BTCUSD M15/H1 showed a real, Bonferroni-significant, cross-window-
> consistent 1-bar mean-reversion *statistical structure* (variance-ratio and runs-test evidence).
> A mechanical *signal* was built on it ("fade the last bar, hold 1 bar"). That signal's pre-cost
> expectancy did not clearly beat baselines in most windows, and its real-cost expectancy was
> negative in **6 of 6** tested window×timeframe cells — real structure, a well-defined signal,
> but never a profitable strategy after costs. All three rungs of the ladder were checked
> independently and explicitly, not assumed to follow from the first.

---

## B. Research design

### Windows

- **Train**: the window a parameter, threshold, or rule is actually chosen/fit on.
- **Validation**: an independent window used to check a candidate before it's treated as
  "found" — may be revisited during iterative design, but each revisit counts as a new use.
- **Held-out**: touched exactly once per candidate, after the candidate is fully fixed. Once a
  held-out window has been read for a given candidate, that window is no longer held-out for
  that candidate — a re-test after seeing the result is not a held-out test, it's tuning.
- **Forward**: real (paper, demo, or live) data generated *after* the strategy was locked —
  cannot be contaminated by construction, since it didn't exist at design time.

**Chronological splitting only.** Time-series data must never be shuffled or randomly split —
windows are contiguous calendar/bar ranges, oldest-first. Where no natural "live" boundary
exists for an instrument/timeframe (never traded live, or traded under a different config), use
**chronological thirds** (early / middle / recent) of the available history as train/validation/
recent, rather than inventing an arbitrary split after seeing the data.

**Avoiding lookahead bias**: the mechanism that determines "what the strategy could see at time
t" must be structurally incapable of returning anything after t — not just conventionally
avoided by careful coding. Prefer a single, narrowly-scoped, directly-testable chokepoint over
scattered discipline.

> **Example (this repo).** `ReplayCursor.visible_bars()` is the *only* method any backtest
> strategy call goes through to see market data, and it structurally cannot return bars past its
> own cursor position — one small function, unit-tested directly for this property, rather than
> trusting every call site to "remember" not to peek ahead.

**Avoiding leakage**: any input to the signal (including regime filters, volatility estimates,
or symbol metadata) must be computable from information genuinely available at decision time —
watch especially for indicators computed over a window that includes the bar being decided on.

**Avoiding parameter mining**: a threshold search is not evidence of edge, it's evidence that a
threshold *exists* that fits the training data — expected even under pure noise. Every
parameter search must be followed by an independent held-out check before the parameter is
trusted, and **a rejected candidate does not license an unlimited number of further search
attempts on the same question** — treat repeated failed searches as evidence against a stable
answer existing at all, not as a reason to keep searching.

> **Example (this repo).** A regime-filter threshold search on grid found `0.2`, rejected on
> held-out data. A second, independent search found `0.013`, also rejected on held-out data
> (drawdown roughly tripled despite expectancy direction holding). The result was accepted as a
> **negative finding** — "two independent, differently-shaped candidates both failing
> out-of-sample is itself informative: evidence against a stable, generalizable threshold
> existing for this combination at all" — rather than treated as license for a third sweep.

**Pre-specifying experiments**: horizons, seeds, thresholds, and window boundaries are fixed and
written down *before* the experiment runs, and printed by the experiment's own output so the
pre-registration is checkable after the fact, not just claimed in a commit message.

**Avoiding repeated tuning on held-out data**: exactly one look per candidate, as above. If a
held-out result forces a redesign, the redesigned candidate needs its *own*, still-untouched
held-out data — reusing the same window is not independent evidence anymore.

**Multiple-comparison risk**: when running more than one statistical test (multiple horizons,
multiple windows, multiple metrics), correct the significance threshold for the total number of
tests (Bonferroni or an equivalent), and compute/print that count and corrected threshold *before*
reporting which cells are significant.

**Sample-size requirements**: fix a minimum trade/observation count before treating any
expectancy, win-rate, or similar figure as a claim, not just a preliminary read. Report whether
that floor was met alongside every such figure, not just the figure itself.

**Baseline comparisons**: every signal needs at least one appropriate baseline run under
identical cost/risk/sizing conditions, not compared against zero or against intuition:

- **Random-direction baseline** — same entry cadence, same risk/sizing, same cost model, only
  the direction call replaced by a seeded coin flip. Run enough pre-registered seeds to report a
  distribution (mean, spread, range), not a single random run.
- **Unconditional / no-signal baseline** — what happens with no filter/condition applied at all,
  when the strategy under test is itself a *conditional* variant of something simpler.
- **Buy-and-hold** — where the instrument and holding-period expectation make it a meaningful
  comparison (a 1-bar-hold strategy doesn't need this; a multi-day directional strategy does).
- **Fixed-rule baseline** (e.g. always-long, always-short) — necessary whenever the strategy's
  own hypothesis is directional, so "beats random" can be distinguished from "was just long/short
  a market that trended that whole period."

---

## C. Backtest validation

### Required metrics, computed per strategy, never blended across strategies

- Trade count (and whether the pre-registered minimum-sample floor was met)
- Expectancy — in R-multiples (risk-normalized, the primary unit) and in money terms
  (informational, since $ conversion usually needs contract-size/tick-value data most backtest
  engines don't model precisely)
- Win rate
- Profit factor
- Maximum drawdown (in R-multiples, peak-to-trough of the cumulative R curve)
- Sharpe / Sortino — where position-level R-multiples aren't the natural unit (e.g.
  portfolio-level or multi-strategy allocation questions); not always the right lens for a
  single-strategy R-multiple analysis, but state explicitly which lens is being used and why
- Turnover / trade frequency
- Average (and median) holding time
- Exposure — concurrent open risk over time, not just at trade entry
- Transaction-cost drag — the *difference* between a zero-cost and real-cost run of the identical
  candidate, reported directly rather than estimated from a formula
- Spread — modeled using the instrument's own real historical spread where available, not a
  flat assumption
- Slippage assumptions — stated explicitly if not directly measured
- Rejection / skipped-entry behavior — how often and why the strategy's own guards (position
  limits, exposure caps, broker constraints) blocked a signal from ever becoming a trade; a
  strategy that looks fine on paper but is silently blocked most of the time in practice is not
  the strategy that will actually run

> **Example (this repo).** A wide stop-loss floor combined with a single-position concurrency
> cap caused one position to sit open, unresolved, for most of a 13-day backtest window — the
> strategy wasn't performing badly, it was barely trading at all, and that fact only surfaced by
> explicitly checking rejection counts, not by reading the expectancy number alone.

### Require independent windows, not one best period

A single backtest window, however long, is one draw from history. Report the same metrics across
at least train/held-out (and forward/live once available) windows, and treat inconsistency
across windows as information, not noise to average away. A strategy whose headline metric comes
from its best window only has not been validated — it's been found.

---

## D. Root-cause research when backtest and live (or two windows) diverge

When live results and backtested expectations disagree — or two supposedly-comparable windows
disagree with each other — classify the candidate cause(s) before touching any parameter.
Multiple causes can be true simultaneously; the goal of this section is to isolate which ones
actually are, not to guess and fix the first plausible-sounding one.

### The six candidate categories

1. **Backtest-model defect** — the simulation itself is wrong (a bug in fill logic, cost
   modeling, or a mismatch between what was actually configured and what was actually tested).
2. **Execution/friction effect** — real broker behavior (rejections, spread, slippage, stop
   distance floors, concurrency limits) that the backtest didn't model or under-modeled.
3. **Market-regime change** — the underlying market genuinely behaves differently across the
   windows being compared, independent of any modeling error.
4. **Sampling/window mismatch** — the "live" and "backtest" samples aren't actually comparable
   (different configs blended into one sample, different calendar windows, different sample
   sizes/power).
5. **Design-level mechanism** — a structural interaction in the strategy's own design (not a bug)
   that produces the observed behavior — e.g. two individually-reasonable guards combining to
   silently starve the strategy of trades.
6. **Genuine lack of edge** — after ruling out 1-5, the signal simply doesn't predict anything
   better than chance.

### Questions worth testing explicitly, not assuming

- Is a concurrency/position-limit setting bottlenecking opportunity, and if relaxed, does
  per-trade quality actually change (or only volume)?
- Is a stop/target distance wide enough that positions rarely resolve, blocking later signals
  (holding-time blocking)?
- Does execution timing (fill at bar close vs. next bar, market vs. limit) match what the
  backtest assumed?
- What candle-close/timing assumptions does the backtest make, and do they match how the real
  pipeline actually evaluates cycles (e.g. cycle cadence vs. bar timeframe)?
- Are stop-distance constraints (broker minimums, ATR floors) realistic and actually reachable
  at the instrument's real price/volatility?
- Is the spread/slippage model realistic, and does it match what the strategy actually paid live?
- Are transaction costs (commission, swap, financing) present live but absent from the backtest?
- Is the sample contaminated by broker rejection behavior (orders attempted but never filled,
  silently absent from a "trades" count)?
- Is the live sample continuous, or a series of disjoint, differently-configured runs blended
  together as if they were one sample?
- Do the two windows being compared actually represent the same regime, or has the underlying
  market genuinely shifted?

### The required loop

**Hypothesis → controlled experiment → evidence → decision.** Each candidate cause gets its own
narrowly-scoped experiment designed to isolate it (holding everything else fixed), not a single
combined re-run that changes several things at once. **Do not allow immediate parameter tuning
as the default response to a poor result** — tuning without first isolating cause conflates
"we found a number that worked on this data" with "we understand why it worked," and reproduces
exactly the parameter-mining risk Section B exists to prevent.

> **Example (this repo).** A live sample that looked negative on its face turned out to blend
> three structurally different configurations across the period it spanned, with only 2 of the
> live trades reflecting the actually-deployed configuration — a sampling/window mismatch (cause
> 4), not evidence about the deployed config's edge at all. Separately, re-running the deployed
> config against its own live window in the backtest reproduced a genuine zero-trade result,
> ruling out "wrong window" as an explanation and pointing at a design-level mechanism (cause 5:
> a wide floor interacting with a concurrency cap) instead.

---

## E. Strategy decision gates

Every research pass ends in exactly one of three outcomes, chosen on evidence, not on effort
already invested:

- **KEEP FOR FURTHER RESEARCH** — a specific, named next experiment exists and the current
  evidence justifies running it (e.g. confirmed statistical structure not yet tested for
  cost-survival). Not a default when results are merely ambiguous.
- **REDESIGN** — a specific, identified mechanism (not the whole thesis) is responsible for
  underperformance, and there's a concrete, testable reason to believe fixing that mechanism
  (not searching for a better parameter of the same mechanism) would change the outcome.
- **ABANDON** — genuine lack of edge, or a stronger/cleaner version of the same idea already
  failed the relevant bar and a weaker version is very unlikely to pass it. Abandoning without
  running every conceivable follow-up test is correct once the marginal experiment's likely
  answer is already known.

### Edge-strength vocabulary, used consistently across all research docs for this project

- **Robust economic edge**: consistent (same sign, beats all relevant baselines) across every
  independently tested window, and survives realistic transaction costs. A single good window
  never promotes a candidate to this tier.
- **Weak / inconclusive edge**: some support, but not full cross-window consistency, or beats
  baselines only narrowly/inconsistently — worth a specific next experiment, not yet a basis for
  any adoption decision.
- **Statistical structure present, costs erase it**: a real, confirmed non-random pattern exists,
  but the mechanical trading rule built on it does not survive realistic costs. Distinct from "no
  usable edge" — the structure is real, the tradeable version of it currently is not.
- **No usable edge**: the signal is statistically indistinguishable from an appropriate baseline
  in every independent window tested.
- **Negative edge**: stronger than "no usable edge" — a large, well-powered sample shows
  consistently unprofitable results (profit factor persistently below 1, negative expectancy
  across independent windows), not just an absence of proof of profitability.

> **Example (this repo).** A weaker, not-fully-Bonferroni-robust version of a structure whose
> *stronger, fully-confirmed* counterpart had already failed real-cost testing (negative
> expectancy in every tested cell) was judged not worth its own full cost-test build: "would very
> likely just reconfirm that failure at real build/run cost, for a close-to-foregone conclusion."
> Documented as the reasoning for the decision, not skipped silently.

---

## F. Execution framework validation

**Strategy profitability and execution-system reliability are separate questions, validated
separately.** A system that reliably and safely executes a strategy with no edge is a completed
execution-readiness milestone and a still-open profitability question, not a contradiction.

Cover, independent of what strategy is running:

- **Order lifecycle**: submit → verify against actual broker state (a position/order lookup),
  never trust the immediate call's return value alone as proof of what happened.
- **Broker response validation**: exactly one code path decides success/failure, and it should
  be based on the authoritative signal your API actually provides (which may not be the most
  obvious field) — a single shared function, not judgment calls scattered across call sites.
- **Retcode / error-code handling**: enumerate the response codes your broker/API can return,
  what each means, and how each is handled (retry, fail loudly, fail silently-and-log) —
  explicitly, not "whatever wasn't tested yet falls through to a generic exception."
- **SL/TP attachment** (or the equivalent protective-order mechanism for the asset class):
  mandatory before a position is considered safely open; understand and handle broker-side
  minimum-distance constraints, which can differ enormously by instrument.
- **Position protection**: define and monitor for an explicit "unprotected position" failure
  state — a position with no working stop/target is a named, tracked incident, not a silent
  possibility.
- **Reconciliation**: cross-check local records against the broker's live state by **exact
  identifier only** (ticket/order ID) — never infer ownership by matching symbol, side, price, or
  timing, however tempting a "probably ours" match looks. Guessing ownership defeats the purpose
  of reconciliation.
- **Local state vs. broker state**: define the reconciliation outcome categories explicitly
  (e.g. matched / local-only / unattributed-real) and what action each triggers.
- **Unattributed-real-state behavior**: any broker-side position/order with no matching local
  record must trigger a conservative posture change, not be silently ignored or silently adopted.
- **A restricted "manage existing, don't open new" safety posture**: a system state that refuses
  to touch anything it cannot attribute to itself, entered automatically whenever unattributed
  state is found — the core fail-closed mechanism this whole section protects.
- **Orphan / background process checks**: verify no stray process from a prior run is still
  submitting orders before starting a new session.
- **Stop-file / safe shutdown**: an external, out-of-band way to halt a running session (a file
  the process polls for) that takes priority over every other stop condition.
- **Restart / recovery**: what happens to in-flight state (orders submitted but whose response
  was lost, e.g. to a connection drop) — a known gap to explicitly acknowledge if not solved, not
  to silently assume away.
- **Idempotency**: re-running the same cycle/step twice must not double-submit orders or corrupt
  state — test this directly (chain two real cycle calls back to back), don't just assume it.
- **Broker-generated artifacts**: some brokers surface their own auto-generated closing
  orders/deals as transient live tickets — distinguish these from genuinely foreign state via a
  strict evidence conjunction (multiple independent, hard-to-fake matching signals), and fail
  closed (treat as unattributed) on any single missing piece of evidence, never on a partial or
  "probably fine" match.
- **Stale-state reconciliation**: periodically (and after any outage/gap) reconcile the full
  local record set against a live broker read, and report exactly what was found/fixed (counts by
  category), not just "cleaned up."
- **Exact ownership verification / manual adoption**: if a human needs to bring an
  externally-opened position under system management, require an exact match (ticket, symbol,
  side, volume) against a fresh live read, record it honestly as manually adopted (not
  retroactively as if the system had opened it), and never backfill fields (like an order
  response code) that never actually existed.

**Preserve this rule exactly: ambiguous or foreign live state → fail closed → restricted posture
or stop.** Do not weaken ownership-verification rules merely to keep a test or a demo run going —
a fail-closed trip during testing is the mechanism working, not an obstacle to route around.

---

## G. Staged demo-execution progression

A strategy-agnostic shape for building execution confidence incrementally, each stage gated on
the previous one passing and on its own explicit approval:

1. **Targeted smoke tests** — the smallest possible live/demo action that exercises one specific
   mechanism (e.g. submit-then-cancel a single order), never a full strategy cycle on the first
   live touch.
2. **Bounded live/demo cycles** — a small, fixed number of real cycles (not open-ended), with
   tight caps on runtime and loss, to observe real behavior at low stakes.
3. **Failure-injection tests** — deliberately induced failures (broker rejection, partial
   response, simulated disconnect) run first against mocks/doubles (no live call needed), only
   escalated to real conditions if a specific gap can't be tested otherwise.
4. **Kill-switch verification** — prove the safety-stop mechanism trips correctly against a
   *real, observed* trigger condition at least once, not just unit-tested in isolation.
5. **Uninterrupted reliability run** — a longer, continuous session at real (or near-real) scale,
   specifically to observe operational behavior over time (memory/state growth, connection
   stability, cumulative drift) rather than to prove anything about profitability.
6. **Post-run reconciliation** — a full local-vs-broker state reconciliation immediately after
   any live/demo session, reporting exactly what was found.
7. **Performance monitoring** — a repeatable, read-only report (expectancy, drawdown, win rate,
   profit factor, slippage, and whatever this project's Section C metrics apply) run against the
   session's real trade data.

**A long, uninterrupted run proves operational durability, not profitability.** Do not let a
clean, incident-free reliability run be read as evidence of edge — it answers "did the system
survive," a completely different question from "did the strategy make money," and both should be
reported, never conflated.

---

## H. Risk and safety gates

- **Max exposure / max open lots (or equivalent notional/contract cap)**
- **Per-symbol exposure limits**
- **Position concurrency limits** — and be aware these interact with holding-time (see Section D)
  in ways that can silently bottleneck a strategy without ever showing up as a "loss"
- **Mandatory stop-loss / take-profit** (or equivalent) on every position — no naked exposure
- **Daily loss limit / kill-switch** — get the trigger semantics exactly right (at-or-beyond vs.
  strictly-beyond a threshold is a real, consequential design choice, not a rounding detail — a
  breakeven-exactly edge case is a classic place for an off-by-a-sign bug to hide)
- **Margin guard** — do not assume this exists just because other risk guards do; verify directly
- **Spread / slippage filter** — refuse to trade when real-time cost conditions are abnormal
- **Session / news filters** — where relevant to the instrument (not universal — a 24/7 asset and
  a scheduled-hours instrument need different treatment)
- **Shutdown / rollback procedures** — both a controlled stop and a way to unwind/flatten if
  needed
- **Manual/human approval requirements** — explicitly enumerated: which actions require a human
  in the loop, and which do not

**Safety guards must be independently validated from strategy edge.** A project can (and should)
be able to say "our execution/risk framework is fully validated" while separately and honestly
saying "we have not yet found a strategy with edge to run inside it" — these are not the same
milestone, and neither should be allowed to stand in for the other.

---

## I. Locked Demo Forward Test

### Entry criteria (all required, not a majority)

- Execution framework operationally validated (Sections F/G/H all substantively complete)
- Strategy has credible, out-of-sample-confirmed economic edge (Section E: at least
  "weak/inconclusive" with a clear next step, ideally stronger — never started on "no usable
  edge" or "negative edge")
- Parameters locked — the exact configuration under test is fixed and recorded before the first
  run, not adjustable mid-series
- No further tuning during the test — a forward test that gets its parameters adjusted partway
  through is no longer measuring what it claims to measure
- A demo-to-live-style readiness checklist (Section J) is sufficiently complete for what this
  specific test needs to prove

### What to collect, every run

Trades, PnL, expectancy, win rate, profit factor, drawdown, slippage, spread, rejected orders,
skipped signals, safety-stop trips, reconciliation errors/findings, uptime and restart events.
Track cross-run as a running log (one row per run: id, date, cycles completed,
accepted/rejected, expectancy, drawdown, win rate, profit factor, trade count, incidents) so
patterns across runs are visible, not just each run in isolation.

### Cadence

Repeated, bounded runs spaced across different sessions/days — deliberately not back-to-back, so
the series samples different real market conditions rather than one long contiguous window
dressed up as several runs.

### Exit / failure criteria

- Define per-run acceptance criteria explicitly (a checklist, evaluated the same way every time)
  before the series starts.
- **Pause trigger**: any rejected run pauses the whole series until root-caused and fixed — a
  rejected run is never simply discarded and replaced with the next one.
- **"Enough runs" is a judgment call, not a formula** — but bias toward more runs, not fewer,
  using whatever sample-size floor Section B established as a reference point; a single-digit run
  count is preliminary regardless of what it shows.

---

## J. Demo-to-live readiness

Separate readiness into five gates, and require every gate relevant to the pilot's scope before
proceeding — do not let strength in one gate compensate for weakness in another:

1. **Execution readiness** — Section F validated.
2. **Strategy profitability readiness** — Section E/I: credible, cost-inclusive, out-of-sample
   edge, not merely "the system runs without crashing."
3. **Operational readiness** — Section G/H: reliability, safety guards, monitoring all proven.
4. **Risk readiness** — every applicable Section H gate present and tested, including the ones
   that may be genuinely absent (margin guard, spread filter) rather than assumed present.
5. **Broker/instrument readiness** — the specific symbol has itself been researched, not assumed
   to inherit another symbol's properties. At minimum: (a) a dedicated research pass exists for
   this instrument specifically, (b) real broker constraints (minimum stop distance, spread,
   volume step) are read live and shown compatible with the strategy's own assumptions, (c) the
   instrument's volatility-to-price character is structurally appropriate for the strategy (not
   merely "a plausible-sounding alternative"), (d) if choosing between candidate instruments, the
   rejected one(s) are explicitly recorded with a reason, not silently dropped.

**Execution readiness being complete does not imply strategy readiness, and vice versa — always
report and gate on both separately**, exactly as this project did: execution/operational
readiness (this section's gates 1/3/4) can be, and was, closed out completely while strategy
profitability readiness (gate 2) remained open and unresolved. Treat that as the expected, normal
shape of a rigorous project, not an inconsistency to paper over.

> **Example (this repo).** The EURUSD-vs-XAUUSD symbol decision framework used exactly the
> four-criteria shape in gate 5 above: broker-constraint compatibility and volatility-structure
> similarity were satisfied for the candidate instrument, but the dedicated-research-pass
> criterion (gate 5a) was explicitly called out as unmet and blocking, "the *decision itself*
> still needs" that research — not something the other three criteria could substitute for.

---

## K. Live Pilot

**A live pilot is a validation exercise, not a production deployment.** Its purpose is to test
whether real-money conditions (real fills, real psychology, real broker behavior at stake) change
anything the demo phase couldn't reveal — not to "go live" as a milestone for its own sake.

Require:

- Explicit human approval, not automated escalation from any prior stage
- One or a small, fixed number of instruments — never the full universe the strategy could
  theoretically trade
- Minimum lot / smallest tradeable size
- Small initial capital, sized deliberately (not "whatever's convenient")
- A hard daily loss limit, tested and proven to trip (Section H)
- An exposure cap
- Explicit shutdown rules and rollback conditions, decided before the pilot starts
- Active monitoring — a live pilot is not a "start and check back later" activity
- No automatic scaling — any increase in size, instrument count, or duration is its own new,
  separately-approved decision, never a default continuation

**No live pilot should begin merely because the software works.** A fully execution-ready system
running a strategy with no demonstrated edge is not ready for a live pilot — it is ready to keep
searching for edge, or to have an explicit, separately-reasoned conversation about running the
pilot anyway for a stated non-edge purpose (e.g. pure operational validation under real
conditions), which is a deliberate exception to name out loud, never a default.

---

## L. Research-loop discipline

The reusable loop, applicable to every individual experiment inside any of the sections above:

```
Observation
  -> Hypothesis
  -> Pre-specified experiment
  -> Controlled test
  -> Evidence
  -> Decision
  -> Documentation
  -> Commit
  -> Next smallest experiment
```

- **Smallest safe next step.** Each turn of the loop should be the smallest experiment that
  produces a real decision, not a large batch of speculative work run ahead of approval.
- **No unnecessary live tests.** Prefer offline/backtest/mock evidence wherever it can answer the
  question; reserve live/demo calls for what genuinely requires them, and keep those as narrowly
  scoped and clearly justified as possible (read-only where a read-only call suffices).
- **Do not repeat a successful acceptance test without a specific evidence gap.** Passing a gate
  once is evidence; re-running an already-passed gate "just to be sure" without a stated reason
  wastes real-world risk/cost for no new information.
- **Separate research questions from implementation.** A new statistical or empirical question
  should, wherever possible, be answerable via an additive, standalone script/harness rather than
  a change to production strategy/execution/pipeline code — keeps the blast radius of "just
  testing an idea" strictly separate from "changing what actually runs."
- **Separate infrastructure readiness from edge discovery**, as in Section J — track them as
  genuinely independent tracks of work with independent status, not as two views of one project
  health number.

> **Example (this repo).** A fixed-holding-period cost test was built as "a wrapper around the
> existing engine... not a change to backtest/engine.py/pipeline/strategy/execution" specifically
> so a new research question could be answered without touching anything the live pipeline
> actually depends on.

---

## M. Multi-machine (or multi-environment) workflow

Where research/development happens across more than one machine or environment, establish this
split explicitly and keep it consistent:

- **Source code, documentation, and tests**: version-controlled, portable, assumed identical
  across environments.
- **Credentials and environment-specific configuration** (API keys, account details, local
  paths): environment-local, never committed, re-supplied per environment.
- **Runtime/execution state** (local order/position records, session state): environment-local
  and **not assumed to transfer** through version control, even if it theoretically could be
  copied — treat any such transfer as a deliberate, explicit action, not an implicit sync.

**Distinguish two genuinely different kinds of "blocked" when planning work across environments**:
1. **Environment-specific blocker** — depends on state/data that only exists in one particular
   environment (e.g. runtime records generated by a session that ran there). No amount of
   authorization from elsewhere fixes this; it requires either running the work in that
   environment or deliberately moving the data.
2. **Live-call blocker** — blocked only because live/order-affecting calls are out of scope for
   *this specific session*, not because of anything environment-specific. Fully doable in the
   same environment once that constraint lifts.

Conflating these two produces wrong plans (waiting to "get back to the right machine" for
something that was actually just waiting on approval, or vice versa).

- **Never silently adopt foreign runtime state because local records are missing.** A missing
  local record after an environment switch is exactly the ambiguous-state case Section F's
  fail-closed rule covers — treat it identically, including the same narrow, exact-match manual
  adoption workflow if adoption is genuinely warranted.
- **Perform a fresh, read-only pre-flight check after any environment change**, before assuming
  anything about current live/demo state — read the real state, don't infer it from what was
  last known in the other environment.

---

## N. Reusable phase model

A generalized phase structure, derived from — but not identical to — how this repository's own
work actually unfolded. Renumbered and reworded here to be strategy-agnostic; adjust names for
your own project's vocabulary, but preserve the ordering and the gates between phases (later
phases should not start before earlier ones' exit criteria are met).

For each phase: objective, entry criteria, work, evidence/tests required, exit criteria, common
failure modes, whether live/demo activity is allowed, and human-approval requirements.

### Phase 0 — Problem / strategy hypothesis
- **Objective**: produce a complete Section A hypothesis definition.
- **Entry**: none (starting point).
- **Work**: market/instrument selection, hypothesis writing, literature/prior-art review.
- **Evidence required**: a filled-in hypothesis (Section A table), not yet any code.
- **Exit**: hypothesis is specific and falsifiable enough to design an experiment against.
- **Common failure modes**: a "pattern" mistaken for a hypothesis (no mechanism stated); scope
  too vague to falsify.
- **Live/demo allowed**: no.
- **Approval**: none needed yet.

### Phase 1 — Data and research environment
- **Objective**: reliable, cached, offline-reproducible historical data and a correctness-tested
  backtest/simulation engine.
- **Entry**: Phase 0 complete.
- **Work**: data source integration (read-only), local caching, engine scaffolding (fill logic,
  cost modeling, look-ahead-bias prevention).
- **Evidence required**: unit tests proving the look-ahead-bias chokepoint (Section B) actually
  can't leak; a documented cost model.
- **Exit**: engine can replay historical data deterministically and offline.
- **Common failure modes**: cost model that quietly diverges from real broker behavior; caching
  bugs that silently drop or duplicate bars.
- **Live/demo allowed**: read-only only, for initial data pulls.
- **Approval**: read-only data pulls typically low-friction; still worth explicit sign-off per
  this project's own convention of confirming even read-only live calls each session.

### Phase 2 — Baseline strategy implementation
- **Objective**: a mechanical, testable implementation of the Phase 0 signal, with no execution
  wiring yet.
- **Entry**: Phase 1 complete.
- **Work**: signal/entry/exit logic as pure, testable functions.
- **Evidence required**: unit tests of the signal logic in isolation.
- **Exit**: signal can be run against Phase 1's engine.
- **Common failure modes**: signal logic entangled with execution/order-placement code, making it
  untestable in isolation.
- **Live/demo allowed**: no.
- **Approval**: none needed.

### Phase 3 — Backtest correctness and validation
- **Objective**: Section C's metrics, computed correctly, across Section B's windows.
- **Entry**: Phase 2 complete.
- **Work**: run the baseline strategy plus all Section B baselines across train/held-out windows.
- **Evidence required**: full Section C metric table per window; baseline comparison.
- **Exit**: an honest Section E classification exists (even if it's "no usable edge" — that's a
  valid, informative exit, not a blocker).
- **Common failure modes**: parameter mining disguised as "validation"; comparing against no
  baseline at all.
- **Live/demo allowed**: no.
- **Approval**: none needed for the research itself.

### Phase 4 — Execution integration
- **Objective**: wire the strategy to a real (or realistic dry-run) execution path, with strategy
  logic still fully decoupled from broker/adapter code.
- **Entry**: Phase 3 shows at least a candidate worth building execution for (KEEP or REDESIGN,
  not ABANDON).
- **Work**: order lifecycle (Section F), architectural boundary enforcement (strategy code cannot
  import execution/adapter code directly).
- **Evidence required**: architecture tests proving the boundary holds; dry-run tests with no
  live call.
- **Exit**: a dry-run pipeline runs end-to-end against a simulated or mocked broker.
- **Common failure modes**: strategy code reaching directly into execution "just this once" for
  convenience — the boundary must be enforced structurally, not just by convention.
- **Live/demo allowed**: no.
- **Approval**: none needed for dry-run/mock work.

### Phase 5 — Risk / state / safety controls
- **Objective**: Section H's guards and Section F's state/reconciliation machinery, built and
  unit-tested before any live call.
- **Entry**: Phase 4 complete.
- **Work**: exposure caps, position limits, mandatory SL/TP, local state store, reconciliation
  logic, fail-closed posture mechanism.
- **Evidence required**: unit tests of every guard, including edge cases (exact-threshold
  breaches, zero/empty states).
- **Exit**: every Section H/F item that applies to this project is implemented and tested, or
  explicitly flagged as a known, deliberate gap.
- **Common failure modes**: an off-by-one or sign error at an exact threshold boundary (classic:
  breakeven-exactly cases); guards that assume "this will never happen" instead of testing it.
- **Live/demo allowed**: no.
- **Approval**: none needed for offline work.

### Phase 6 — Controlled demo execution
- **Objective**: Section G's staged progression, steps 1-4, each with its own live/demo smoke
  test.
- **Entry**: Phase 5 complete.
- **Work**: incremental live/demo actions, smallest first (Section G).
- **Evidence required**: a real, observed success at each step before the next is attempted.
- **Exit**: the execution path has been proven against a real (demo) broker connection at small,
  controlled scale.
- **Common failure modes**: skipping straight to full-cycle live testing without the smallest
  smoke test first; treating "it compiled/ran without an exception" as proof instead of verifying
  actual resulting state.
- **Live/demo allowed**: yes, demo/paper account only, narrowly scoped per step.
- **Approval**: explicit, per step — each step is its own go-ahead, not a blanket authorization.

### Phase 7 — Failure / regression / reliability validation
- **Objective**: Section G's failure-injection and reliability steps; also where operational
  scaling issues (state-store performance, connection-drop handling) get found and fixed or
  explicitly deferred.
- **Entry**: Phase 6 complete.
- **Work**: failure-injection tests (prefer mocks first), reliability/performance checks under
  realistic scale.
- **Evidence required**: documented failure modes and their fixes (or documented, reasoned
  decisions to leave a known gap unresolved for now).
- **Exit**: known failure modes are handled or explicitly, reasonedly deferred — not silently
  unaddressed.
- **Common failure modes**: performance issues that only appear at realistic scale (e.g. an
  algorithm that's fine at small N and quadratic at real N) — test at the scale you actually
  expect, not a token amount.
- **Live/demo allowed**: for reliability runs specifically, yes, at the smallest scale that still
  produces a meaningful test.
- **Approval**: explicit for any live/demo component.

### Phase 8 — Strategy research, edge validation, and redesign
- **Objective**: Sections D and E's full discipline — this is where most of a project's real
  research time is spent, and where the KEEP/REDESIGN/ABANDON decision gets made (and potentially
  remade after redesign, iteratively).
- **Entry**: Phase 3's initial backtest classification, whatever it was (this phase is where
  deeper investigation happens, especially after live/forward data starts disagreeing with
  backtest, or after Phase 3's own result is inconclusive).
- **Work**: root-cause investigation, alternative-signal search, cost-inclusive testing, always
  under Section B's discipline (pre-registration, held-out data used once, baselines).
- **Evidence required**: every experiment documented with hypothesis, design, result, and
  classification before the next one starts.
- **Exit**: a stable KEEP/REDESIGN/ABANDON decision with evidence behind it. ABANDON is a valid,
  complete exit for this phase — a research thread can close permanently here.
- **Common failure modes**: parameter mining; treating a statistically significant but
  economically tiny result as if it settled the question; re-testing held-out data after seeing
  it once already.
- **Live/demo allowed**: read-only only, and only where offline data genuinely can't answer the
  question.
- **Approval**: explicit per new experiment, especially any live-call component.

### Phase 9 — Locked demo forward validation and live-readiness
- **Objective**: Section I's locked forward-test series, and Section J's five-gate readiness
  assessment.
- **Entry**: Phase 8 produced at least a "weak/inconclusive" edge classification or stronger — an
  ABANDON exit from Phase 8 means this phase does not start.
- **Work**: repeated, bounded, parameter-locked forward runs; readiness-checklist maintenance.
- **Evidence required**: the full per-run metric collection from Section I, across enough runs
  that a single-digit count isn't being treated as conclusive.
- **Exit**: all five Section J gates satisfied, or an explicit decision that some subset is
  sufficient for a deliberately scoped-down pilot purpose.
- **Common failure modes**: adjusting parameters mid-series; treating operational
  success (no crashes, no incidents) as if it were evidence of edge; resuming a paused series
  without new edge evidence, on schedule/inertia alone.
- **Live/demo allowed**: demo/paper account, bounded runs, per Section I's cadence guidance.
- **Approval**: explicit per run, or per a pre-approved bounded series — never open-ended.

### Phase 10 — Small real-money pilot
- **Objective**: Section K's live pilot.
- **Entry**: Phase 9's five gates all satisfied (or an explicit, separately-reasoned exception
  documented and approved).
- **Work**: small, monitored, capped real-money trading.
- **Evidence required**: the same metric set as Phase 9, now on real capital, plus anything
  specific to real-money conditions (real fill quality, real psychological/operational pressure).
- **Exit**: either a decision to scale (its own new, separately-approved phase) or a decision to
  stop/return to research.
- **Common failure modes**: starting a pilot because the system is *capable* of it rather than
  because both readiness tracks (Section J) are actually satisfied; silent scope creep (more
  instruments, more size) without a fresh approval.
- **Live/demo allowed**: yes — this is the first phase where real capital is at risk.
- **Approval**: explicit, human, informed — the single highest-friction approval gate in this
  model, deliberately.

### Phase 11 — Production monitoring / controlled scaling
- **Objective**: sustained operation with ongoing performance/risk monitoring, and any scaling
  decisions made as their own explicit, evidence-gated steps.
- **Entry**: Phase 10 produced a decision to continue/scale.
- **Work**: continuous monitoring against the same metrics used throughout, incident response,
  periodic re-validation that live performance hasn't silently diverged from what justified the
  pilot.
- **Evidence required**: ongoing, not a one-time check — the same discipline that got the
  strategy here doesn't stop applying once real money is flowing.
- **Exit**: not a fixed exit — this phase is where a mature strategy lives, subject to the same
  standing rule as everywhere else: any material change (new instrument, new size, new market
  regime) re-triggers the relevant earlier phases rather than being assumed to inherit prior
  approval.
- **Common failure modes**: treating "it's been fine so far" as permanent proof rather than
  ongoing evidence that needs to keep arriving; scaling decisions made without re-running the
  readiness gates at the new scale.
- **Live/demo allowed**: yes.
- **Approval**: explicit for any scaling step; ongoing monitoring itself should have a defined
  owner and response process, not be assumed to run itself.
