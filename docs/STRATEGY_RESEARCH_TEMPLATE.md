# Strategy Research Template

Copy this file to a new, strategy-specific document (e.g.
`docs/research/<STRATEGY_ID>_RESEARCH.md`) — one instance per strategy/instrument/timeframe
combination under research. Fill in every section as work happens; do not backfill sections
after the fact to look tidier than the actual research process was. Leave a section explicitly
marked `Not yet done` rather than deleting it, so the document always reflects real status.

This template is strategy-agnostic. It carries no assumptions about market, timeframe, or
strategy family — every field is meant to be filled in fresh for whatever is actually being
researched. See `docs/STRATEGY_VALIDATION_FRAMEWORK.md` for the full reasoning behind each
section; this file is the fill-in companion, not a replacement for reading that framework first.

---

## 1. Identification

| Field | Value |
|---|---|
| Strategy ID | `<short, unique, stable identifier — e.g. SYM-TF-CLASS-NNN>` |
| Strategy name | `<human-readable name>` |
| Instrument(s) | `<exact ticker(s)/symbol(s)>` |
| Timeframe | `<signal bar interval>` |
| Strategy class | `<directional / mean-reversion / breakout / arbitrage / carry / market-making / other>` |
| Owner / researcher | `<name>` |
| Status | `<DRAFT / ACTIVE RESEARCH / KEEP / REDESIGN / ABANDONED / FORWARD-TESTING / LIVE PILOT>` |
| Date started | `<date>` |
| Date last updated | `<date>` |

---

## 2. Hypothesis (Framework Section A)

| Field | Value |
|---|---|
| Economic / statistical hypothesis | `<the mechanism, not just the pattern>` |
| Expected source of edge | `<information / structural / behavioral / risk-premium / other>` |
| Entry logic | `<exact, mechanical, reproducible from this description alone>` |
| Exit logic | `<exact, including tie-break/overlap handling if relevant>` |
| Holding-period expectation | `<order of magnitude>` |
| Risk/reward structure | `<e.g. fixed R:R bracket, and its own breakeven math>` |
| Expected market regime | `<trending / ranging / high-vol / low-vol / regime-agnostic>` |
| Invalidation conditions | `<decided BEFORE looking at results>` |

**Ladder check** (Framework Section A) — state explicitly which rung the current evidence
actually supports, and don't let a strong claim at one rung imply the next without its own test:

- [ ] Statistical structure demonstrated
- [ ] Trading signal defined (mechanical, reproducible)
- [ ] Trading edge demonstrated (beats baselines, pre-cost)
- [ ] Profitable strategy demonstrated (survives real costs, out-of-sample)

---

## 3. Data windows and baselines (Framework Section B)

| Window | Date range | Bar count | Used for |
|---|---|---|---|
| Train | | | |
| Validation | | | |
| Held-out | | | |
| Forward/live | | | |

- Chronological split? `<yes — describe boundaries, or note if using chronological-thirds
  convention for an instrument/timeframe with no live-trading history>`
- Pre-registration: `<link/paste the exact horizons, seeds, thresholds fixed before running, and
  where they're printed/checkable in the experiment's own output>`
- Multiple-comparison correction: `<method, total test count, corrected threshold>`
- Sample-size floor: `<minimum trade/observation count required before treating a result as a
  claim>`

**Baselines run** (check all that apply, and record where each is implemented/reused from):

- [ ] Random-direction baseline — seeds: `<list>`, run count: `<N>`
- [ ] Unconditional / no-signal baseline
- [ ] Buy-and-hold
- [ ] Fixed-rule baseline (always-long / always-short / other)

---

## 4. Experiments log

One row per experiment. Add rows as research proceeds — never overwrite a prior row's result.

| # | Date | Hypothesis tested | Design (pre-registered) | Window(s) | Result | Classification |
|---|---|---|---|---|---|---|
| 1 | | | | | | |
| 2 | | | | | | |

---

## 5. Metrics (Framework Section C)

Report per window, per experiment/candidate — never blended across strategies or across windows.

| Metric | Train | Validation | Held-out | Forward/live |
|---|---|---|---|---|
| Trade count (min-sample met?) | | | | |
| Expectancy (R) | | | | |
| Expectancy ($, informational) | | | | |
| Win rate | | | | |
| Profit factor | | | | |
| Max drawdown (R) | | | | |
| Sharpe/Sortino (if used, state lens) | | | | |
| Turnover / trade frequency | | | | |
| Avg / median holding time | | | | |
| Exposure (concurrent open risk) | | | | |
| Transaction-cost drag (zero-cost vs. real-cost delta) | | | | |
| Spread model used | | | | |
| Slippage assumption | | | | |
| Rejection / skipped-entry rate + reason | | | | |

---

## 6. Backtest results

`<Summary narrative — what the pre-cost, in-sample/train picture looks like, with a pointer to
the full metrics table above rather than repeating it here.>`

## 7. Held-out results

`<Summary narrative — the single, one-time-use held-out read per candidate. State explicitly
whether this window has already been used for this candidate before (it should not have been).>`

## 8. Demo / forward results

`<Summary narrative — real (paper/demo) trade data, once available. Link to the per-run log if
this strategy has entered Framework Section I's locked forward-test series.>`

---

## 9. Live-vs-backtest divergence investigation (Framework Section D)

Only fill in if live/forward results disagree with backtest expectations enough to warrant
investigation. Otherwise mark `Not applicable — no divergence observed` and skip to Section 10.

**Candidate cause checklist** — mark each considered, not just the one that turned out true:

- [ ] Backtest-model defect
- [ ] Execution/friction effect
- [ ] Market-regime change
- [ ] Sampling/window mismatch
- [ ] Design-level mechanism
- [ ] Genuine lack of edge

| Sub-investigation | Hypothesis | Controlled experiment | Evidence | Conclusion |
|---|---|---|---|---|
| | | | | |

**Root cause identified**: `<state plainly, or "not yet isolated — see next smallest experiment
in Section 15">`

---

## 10. Execution issues (Framework Section F)

| Issue | Description | Root cause | Fix / status |
|---|---|---|---|
| | | | |

Relevant checks performed (mark all that were actually verified for this strategy, not assumed):

- [ ] Order lifecycle verified against actual broker state, not just return values
- [ ] Retcode/error-code handling enumerated and tested
- [ ] SL/TP (or equivalent) attachment mandatory and verified
- [ ] Position-protection failure state defined and monitored
- [ ] Reconciliation by exact identifier only, tested
- [ ] Unattributed-real-state handling verified (triggers restricted posture)
- [ ] Orphan/background process check performed before session start
- [ ] Stop-file / safe shutdown tested
- [ ] Restart/recovery behavior documented (including known gaps)
- [ ] Idempotency tested (repeated cycle does not double-submit)
- [ ] Broker-generated artifact handling verified, if applicable
- [ ] Stale-state reconciliation run, findings recorded

## 11. Safety issues (Framework Section H)

| Guard | Present? | Tested? | Notes |
|---|---|---|---|
| Max exposure / max open lots | | | |
| Per-symbol exposure limit | | | |
| Position concurrency limit | | | |
| Mandatory SL/TP | | | |
| Daily loss limit / kill-switch | | | |
| Margin guard | | | |
| Spread/slippage filter | | | |
| Session/news filter (if relevant) | | | |
| Shutdown/rollback procedure | | | |
| Manual approval gates enumerated | | | |

## 12. Root-cause findings summary

`<One-paragraph plain-language summary of what was actually learned, for someone who reads only
this section. Link back to Section 4/9 for full detail — do not duplicate it here.>`

---

## 13. Decision (Framework Section E)

**Decision**: `<KEEP FOR FURTHER RESEARCH / REDESIGN / ABANDON>`

**Edge-strength classification**: `<robust economic edge / weak-inconclusive edge / statistical
structure present, costs erase it / no usable edge / negative edge>`

**Evidence supporting this decision**: `<the specific result(s) this decision rests on — point to
experiment numbers in Section 4, not a general impression>`

**If REDESIGN**: what specific mechanism is being changed, and why there's a concrete reason to
expect it to matter (not just "try a different parameter"):
`<...>`

**If ABANDON**: why further experiments aren't warranted (e.g. a stronger version of the same
idea already failed the relevant bar):
`<...>`

---

## 14. Locked forward-test configuration (Framework Section I)

Only fill in once a KEEP (or successful REDESIGN) decision leads to a locked forward test.

| Field | Value |
|---|---|
| Locked parameter set | `<exact config, with a version/commit reference>` |
| Lock date | |
| No-further-tuning commitment confirmed | `<yes/no>` |
| Cadence | `<e.g. bounded runs spaced across sessions/days>` |
| Per-run acceptance criteria | `<link or restate>` |

**Per-run log**:

| Run ID | Date | Cycles completed | Accepted/Rejected | Expectancy (R) | Max DD (R) | Win rate | Profit factor | Trade count | Incidents |
|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | |

---

## 15. Live-readiness checklist (Framework Section J)

| Gate | Status | Evidence |
|---|---|---|
| 1. Execution readiness | | |
| 2. Strategy profitability readiness | | |
| 3. Operational readiness | | |
| 4. Risk readiness | | |
| 5. Broker/instrument readiness | | |

**Instrument-specific sub-checklist** (gate 5):

- [ ] Dedicated research pass exists for this instrument specifically (not inherited from another)
- [ ] Real broker constraints (min stop distance, spread, volume step) read live and verified
      compatible
- [ ] Volatility-to-price structure verified appropriate for this strategy
- [ ] Any rejected alternative instrument(s) explicitly recorded with reason

---

## 16. Remaining risks

`<Known gaps, deferred issues, explicitly-accepted limitations — anything a future reader should
know before trusting this document's conclusions.>`

## 17. Next smallest experiment

`<The single next step, sized as small as possible while still producing a real decision. Not a
list of everything that could eventually be done — just the next one.>`

**Requires approval for**: `<none / read-only live call / demo order-affecting action / other>`

---

## 18. Decision log

Append-only. One row per decision point, in chronological order — never edit a past row, add a
new one if a decision changes.

| Date | Decision | Reasoning (one line) | Made by |
|---|---|---|---|
| | | | |
