# Checkpoint: Operational Reliability Hardening — scoped and decided, no code changes needed (2026-08-07)

Not one of the numbered phases — like "wire real adapters" before Phase 6, "pipeline wiring" after
Phase 7, or the grid regime filter after Phase 8, a separate effort motivated by findings from
prior work, scoped here for the first time. Named explicitly in Phase 9's own Design section
(item 4) and re-flagged as still-open by `docs/DEMO_TO_LIVE_READINESS_CHECKLIST.md`'s rows 8–9.

**Decided the same day this was proposed, both explicitly**: Item 1 — do nothing yet (still cheap
at today's scale, revisit once Step 7's real call volume is known). Item 2 — leave as-is,
explicitly re-affirmed (avoids building auto-reconnect on top of Phase 7's still-unresolved
in-flight-order gap). Neither decision required or produced any code change — see "Decision"
section below. This is a real, recorded outcome, not an unstarted proposal left hanging; re-open
either item's other options if Step 7 or Live pilot scoping later surfaces a concrete reason to.

## Motivation

Phase 9's own Design section named two decisions this project had already flagged and
deliberately deferred "until sustained live operation is actually proposed" — and named Phase 9
itself as that trigger, without actually revisiting either decision while building Steps 1–6. The
readiness checklist (Step 6) now formally re-flags both as NOT DECIDED, and Step 7 (the actual
sustained forward-test run) is the next thing that would concretely need whatever's decided here.

Two independent, unrelated decisions, bundled under one "operational reliability" heading only
because both were deferred together, not because they share a mechanism:

1. `StateStore.all_open()`/`all_records()`'s O(N) full-directory scan cost.
2. The pipeline loop's "one long-lived connection, no reconnect, a drop is fatal" v1 decision.

## Item 1: `StateStore`'s O(N) scan cost

**Current real numbers** (checked today, not guessed): 134 ticket files on disk, of which 47 are
still marked `OPEN` even though the account is currently fully flat (0 real positions/orders) —
the already-known, already-accepted "stale local status" property, not new. Previously benchmarked
at ~1.3 ms/ticket-file, flat across scale (1.4s at 1000 tickets, 6.2s at 5000) — at 134 tickets
today that's roughly 170ms per full scan, genuinely cheap still. Growth rate: today's Step 5 smoke
tests alone added ~38 new tickets in under an hour of live activity.

**A real change since this cost was last assessed**: Step 5's kill-switch wiring now calls
`state_store.all_records()` (a full-directory scan, same cost profile as `all_open()`) once per
loop iteration — not just on order submit/cancel/close like `all_open()`'s existing call sites.
This adds a new, previously-nonexistent full-scan call on the "nothing happened this cycle" path,
not just the order-submitting path — a real, if still small today, change to the cost profile this
item should account for.

**Options**:
- **Do nothing yet.** Still genuinely cheap at today's scale (~170ms). Revisit again once a real
  call volume from an actual sustained run (Step 7) is known, rather than guessing ahead of it.
- **Archive terminal-status tickets.** Move `CLOSED`/`CANCELLED` records out of the active
  `var/order_state/` directory into a separate archive subdirectory once they transition.
  `all_open()`'s hot path (called on every real order action) would then scan only currently-open
  tickets — the number that actually matters for guard-check latency during live trading, not
  total historical volume. `all_records()` (the kill-switch's input) would need to scan both
  directories, so it doesn't get cheaper by this alone — but `all_open()` is the more frequently-
  called, more latency-sensitive path.
- **In-session caching**: previously considered and explicitly rejected (`docs/PIPELINE_WIRING_CHECKPOINT.md`)
  — real risk of feeding stale reads to the exposure-cap/duplicate-order guards, this codebase's
  highest-severity failure class. Not re-proposing this.
- **A per-magic or per-status index file**: adds a second source of truth that could drift from
  the real per-ticket files it's meant to summarize — a correctness risk for a cost problem that
  isn't urgent yet.

**A separate, related-but-distinct question surfaced by the same numbers**: the 47 stale `OPEN`
records aren't a performance problem, they're a bookkeeping-accuracy one — already flagged and
explicitly deferred multiple times (Steps 25/29 of the pipeline-wiring effort: "not fixing it now
... the only real fix options either violate this project's explicit 'never mutate local state
automatically' principle or duplicate the `all_open()` cost decision already deferred above").
Whether to revisit that call now, alongside this item, or keep it explicitly separate, is its own
open point below.

## Item 2: connection-drop-is-fatal, no reconnect

**Current design** (`scripts/run_demo_execution_pipeline_loop.py`, decision 4 in its own
docstring): one `demo_execution_session()` for the whole run; a dropped connection propagates as
an exception, the loop stops, a human decides whether to relaunch. Deliberately v1-only, never
revisited since.

**This is entangled with an already-known, already-deferred, unresolved risk** — Phase 7's
disconnect-testing effort (Stage 3 Part 3, `AGENTS.md`) found every disconnect scenario *except
one* provably safe: **"a real order reaches the broker but the response is lost to the same
disconnect"** — `McpOrderExecutor` only writes local state *after* its MCP call returns, so an
in-flight order whose response never arrives leaves no local record of an attempt at all. That
gap was explicitly left open, not built or tested, with this exact reasoning: *"Revisit if/when
extended or less-supervised live operation is actually proposed."* A reconnect-and-resume
capability is arguably that trigger — building auto-reconnect without addressing what happens to
an in-flight order across the exact disconnect that would trigger a reconnect attempt would be
building on a foundation this project has already identified as shaky, not closing the gap.

**Options, increasing in scope/risk**:
1. **Leave as-is, explicitly re-affirmed as an accepted risk for now.** No code change. A
   documented decision, not silence — the readiness checklist's row 8 gets a real answer either
   way.
2. **Detect + clean exit only.** Recognize a connection-drop distinctly (already possible — Phase
   7 confirmed the real exception shapes), log it clearly as "connection lost, stopping" rather
   than a generic cycle-error message, but still just stop — no auto-reconnect, no auto-resume.
   Improves operator clarity without touching the in-flight-order gap at all.
3. **Auto-reconnect, no resume of in-flight state.** On a detected drop, attempt a bounded number
   of reconnects (fresh `require_demo_account_kind()` gate each time, matching every other
   connection-establishment in this project), then either continue the loop from its next
   scheduled cycle or give up and stop after N failures. Still doesn't resolve the in-flight-order
   gap — a reconnect immediately after a drop could still land on top of an order whose real
   outcome is unknown.
4. **Auto-reconnect with in-flight reconciliation.** Requires solving Phase 7's deferred gap
   first: after reconnecting, positively determine whether any order submitted just before the
   drop actually reached the broker (e.g. a live position/order snapshot diffed against local
   state, or a deal-history check) before resuming normal cycling. The most complete answer, and
   the most new, safety-critical logic — comparable in risk to Step 5's own kill-switch build.

## Proposed steps, smallest/lowest-risk first (once approved)

| Step | Scope | Key risk |
|---|---|---|
| 1 | Decide Item 1's direction (do nothing / archive) and whether to bundle the stale-`OPEN`-records question in or keep it separate | Archiving touches every `StateStore` consumer's assumptions about what `all_open()`/`all_records()` return — needs care, not just a file move |
| 2 | If archiving: implement + unit test, no live call | Must not change `all_open()`'s return value for any currently-passing scenario — regression risk against the existing at-scale sweep tests |
| 3 | Decide Item 2's option (1–4 above) | The single biggest risk-shape decision in this whole item — option 4 is a materially different scope than 1–3 |
| 4 | If option 2/3/4: implement + unit/integration tested against mocks only, no live call | Same "prove it against a real dropped pipe" discipline Phase 7 already used (`tests/integration/test_pipeline_loop_disconnect.py`'s pattern) |
| 5 | Live verification of whatever was built, its own explicit go-ahead | Same live-testing-pause discipline as every prior live-adjacent step |

## Decision (2026-08-07)

**Item 1: do nothing yet.** Explicit user choice, matching the recommended option above. Reasoning
restated for the record: ~170ms/scan at today's 134-ticket scale is genuinely cheap, and archiving
would be guessing ahead of real evidence Step 7 hasn't produced yet. `docs/DEMO_TO_LIVE_READINESS_CHECKLIST.md`
row 9 updated to reflect this as a real, recorded decision rather than an open question.

**Item 2: leave as-is, explicitly re-affirmed.** Explicit user choice, matching the recommended
option above. No code change to `scripts/run_demo_execution_pipeline_loop.py`'s connection model.
Reasoning restated for the record: Options 2–4 all risk building new operational logic on top of
Phase 7's still-unresolved "order reaches the broker but the response is lost to the disconnect"
gap — Option 1 doesn't add that risk, and documents the decision rather than leaving it silently
open. `docs/DEMO_TO_LIVE_READINESS_CHECKLIST.md` row 8 updated to reflect this as a real, recorded
decision rather than an open question.

Both decisions are revisitable — nothing here is permanent. The natural trigger to reopen either
is Step 7 (the sustained forward-test run) or Live pilot scoping surfacing a concrete need neither
decision anticipated.

```
pytest -q -> unaffected (pure documentation, no src/tests/ changed)
```

**Files changed this entry**: this checkpoint doc, `docs/DEMO_TO_LIVE_READINESS_CHECKLIST.md`
(rows 8-9 updated), `AGENTS.md`.

## Explicitly not in this doc

- Doesn't itself change any production code — a proposal only, matching Phase 9's own opening
  convention.
- Doesn't resolve or attempt Phase 7's in-flight-order gap unless Item 2's Option 4 is
  specifically chosen — Options 1–3 leave it exactly as open as it is today, honestly, not
  silently.
- Doesn't authorize Step 7 (the sustained forward-test run) or Live pilot — this remains its own
  input to those, not a substitute for scoping them separately.
