# Demo-to-Live Readiness Checklist

Phase 9 Step 5 (`docs/PHASE9_FORWARD_TEST_CHECKPOINT.md`) — pure documentation, no code, no live
call of any kind in producing this. Per the Phase 9 proposal's own Design section item 5 and
Proposed Steps table: *"Define demo-to-live readiness criteria... an explicit, objective checklist
that must be satisfied before the Live pilot phase can even be scoped."*

## What this is, and isn't

This is an **input** to a future, separately-scoped Live pilot proposal — it does **not** itself
constitute or authorize that phase, and satisfying every row below is not an automatic green
light. Real money is never at stake anywhere in this checklist or in producing it. Scoping Live
pilot itself remains its own explicitly-approved effort, same discipline as every phase in this
project, informed by this checklist rather than replaced by it.

**Explicit decision made scoping this checklist**: it does **not** gate on demonstrated positive
edge. Phase 9's own motivation section is explicit that this whole effort "is not a 'run it
forward and hope it's profitable' exercise" — both strategies show negative backtested
expectancy, and that's the honest starting point, not something this checklist pretends away.
Real forward expectancy/drawdown must be *measured and reported* (row 3 below), but a negative
result does not itself block Live pilot from being scoped — whether to proceed despite a negative
edge (e.g. to gather more data, or under a materially different sizing/risk posture) is an
explicit, informed decision for that future, separate proposal to make, not a hard gate here.

## Checklist

| # | Criterion | Current status (as of 2026-08-07) | Evidence / source |
|---|---|---|---|
| 1 | Kill-switch (`MAX_DAILY_LOSS`/`RESET_HOUR_UTC`) set to a deliberately-chosen production threshold, not a smoke-test placeholder | **MET** — `MAX_DAILY_LOSS=50.0` set during Step 7 scoping (2026-08-07), derived from real $ risk-per-trade scaled by Phase 8's held-out backtested drawdown figures; still a judgment call, adjustable | `scripts/run_demo_execution_pipeline_loop.py`, Phase 9 Step 7 entry |
| 2 | Kill-switch proven to trip correctly against a real, live-observed breach at least once | **MET** — Step 5's third live attempt: `Stopping before cycle 1: daily loss limit breached (realized_pnl_since_reset=-10.41 breaches max_daily_loss=0.01)`, loop halted, verified via live re-read | `docs/PHASE9_FORWARD_TEST_CHECKPOINT.md`, Step 5 entries |
| 3 | Both strategies independently reach the 30-trade minimum sample (Phase 8 Step 1's own bar) on **live**, not backtested, data, with real expectancy/drawdown reported either way | **PARTIAL** — real read (2026-08-07, after fixing the monitor's own `get_deals` window bug): grid 46 trades (MET) / −0.557 R / 26.425 R drawdown; runner 21 trades (NOT MET) / −0.672 R / 18.895 R drawdown | `docs/PHASE9_FORWARD_TEST_CHECKPOINT.md`, Step 7 entry |
| 4 | Backtest-vs-forward expectancy drift characterized for both strategies (not required positive — just honestly understood) | **MET for grid, PARTIAL for runner** — grid now at a stable minimum sample, directionally consistent with Phase 8's backtested findings; runner not yet at minimum sample | same as row 3 |
| 5 | Step 7 (the actual sustained forward-test run, bounded by the kill-switch, monitored per the live-performance monitor) completed at least once | **MET (2026-08-11)** — run #10 (work machine) completed a full unbroken 1→30 cycle run, ~146 minutes, zero `MANAGE_ONLY`, zero `unknown_real`, zero retcode-`10016`, zero kill-switch trigger, zero unhandled exceptions. Directly observed, not a judgment call. Note: this is the operational-completion criterion only — rows 3-4's live sample-size/expectancy evidence is separate and still open (see below) | `AGENTS.md`, run #10 entry; `docs/PHASE9_FORWARD_TEST_CHECKPOINT.md` |
| 6 | Zero unresolved `OPEN_UNPROTECTED` incidents left behind by that sustained run | **MET (2026-08-11)** — run #10 ended with zero unprotected exposure (1 protected runner position + 5 pending grid orders, 0.06 lots — all protected or pending, none unprotected). Directly observed | `AGENTS.md`, run #10 entry |
| 7 | Zero unmanaged leftover risk (open positions/orders) at the end of that sustained run, or an explicit, reviewed decision to leave protected exposure open | **MET (2026-08-11)** — satisfied by documented judgment, consistent with this row's own accepted pattern: run #10 left protected exposure open by the pipeline loop's documented no-cleanup design (same as every prior run), then that exposure was fully closed via an explicit manual close on the home machine the same day, user-confirmed; account verified flat (0 positions, 0 pending orders) by a fresh read-only pre-flight | `AGENTS.md`, run #10 and home-machine entries |
| 8 | "One long-lived connection, no reconnect, a drop is fatal" (the pipeline loop's v1 connection-model decision) explicitly revisited: either accepted as a documented risk for whatever Live pilot's scope turns out to be, or fixed first | **MET — explicitly re-affirmed, decided 2026-08-07**: leave as-is, no code change, to avoid building auto-reconnect on top of Phase 7's still-unresolved in-flight-order gap | `docs/OPERATIONAL_RELIABILITY_HARDENING_CHECKPOINT.md`, "Decision" section |
| 9 | `StateStore.all_open()`'s O(N) full-directory-scan cost explicitly revisited for whatever call volume Live pilot's scope implies | **MET — explicitly revisited, decided 2026-08-07**: do nothing yet (still ~170ms at today's 134-ticket scale); revisit once Step 7's real call volume is known | `docs/OPERATIONAL_RELIABILITY_HARDENING_CHECKPOINT.md`, "Decision" section |
| 10 | The recurring retcode-`10016` pattern (5 live occurrences to date) explicitly listed as a known, root-caused, correctly-handled quirk in whatever Live pilot proposal follows — not silently omitted just because it's already understood | **MET as a finding, not yet carried forward** — root-caused (Step 28), every occurrence caught correctly, zero unmanaged risk resulted; must be explicitly restated in the Live pilot proposal itself, not assumed still known | `docs/PIPELINE_WIRING_CHECKPOINT.md`, Step 28 |
| 11 | No credential/security regression vs. the argv-exposure fix already made (demo account password no longer passed via process command line) | **MET, revisit only if new code touches credential handling** — fixed in the pipeline-wiring effort, unchanged since | `docs/PIPELINE_WIRING_CHECKPOINT.md` |

## How to use this

Re-run the already-built `scripts/run_demo_execution_live_performance_monitor.py` (read-only) to
refresh rows 3–4's real numbers at any time without needing a new sustained run — currently blocked
on any machine other than the one holding the real `var/order_state` records the script reads from
(machine-local, gitignored by design). Rows 5–7 needed Step 7 to have actually happened; run #10
(2026-08-11) satisfied that and all three are now MET. Rows 1, 8, 9 are explicit decisions someone
has to make, not facts that resolve themselves by waiting. This table should be updated in place
(not left stale)
whenever any row's status genuinely changes — the same "if any of these values ever change... this
table must be updated in the same commit" discipline Phase 9 Step 1's locked-parameter table
already established.
