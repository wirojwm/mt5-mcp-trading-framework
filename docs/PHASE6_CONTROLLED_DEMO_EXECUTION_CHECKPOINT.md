# Checkpoint: Phase 6 — controlled demo execution

Handoff doc for continuing this phase in a new session. Read `AGENTS.md` and `README.md` first
for overall project context; `docs/MCP_ADAPTER_WIRING_CHECKPOINT.md` for the full history of
wiring the real `mt5_adapter`/`mcp_adapter` implementations this phase builds on (that effort
is fully complete and closed out — do not confuse the two documents).

## Goal

Phase 6 is the first phase where a real (demo-account) order could ever be placed. Everything
before this — including the entire "wire real adapters" effort — was read-only or
`DryRunExecutor` (which never calls MCP/MT5 at all). This checkpoint covers: planning (via
Claude Code's plan mode, approved by the user) and implementing a standalone, thoroughly
unit-tested `McpOrderExecutor` for LIMIT-order submit/cancel only. **No live execution has
happened yet.** The plan deliberately does *not* wire this executor into
`run_grid_cycle`/`run_runner_cycle` — that's an explicitly separate, later, phase-gated step.

Full approved plan: `C:\Users\Wiroj\.claude\plans\moonlit-swinging-koala.md` (outside the repo,
local to the machine this was planned on — the summary below is self-contained, don't assume
that file is reachable in a future session).

## Two blocking safety problems found during planning (research, not guessed)

1. **`require_demo_account()`'s `trade_mode` field is unreliable.** `McpAccountReader` is
   live-confirmed (see `docs/MCP_ADAPTER_WIRING_CHECKPOINT.md`) to report `trade_mode='REAL'`
   for the actual connected *demo* account, because of the documented upstream `account_type`
   inversion bug. A hard `require_demo_account()` gate would therefore always refuse to trade
   on the genuinely-demo account in use. **Resolved**: added a second, independent, env-sourced
   hard gate, `require_demo_account_kind()`, reading the same `MT5_ACCOUNT_KIND` value
   `scripts/run_metatrader_mcp_stdio.py` already refuses to launch the subprocess without.
   `require_demo_account()` itself is kept, unchanged, as informational-only (logs a warning,
   never blocks) — see `mt5_adapter/safety.py`'s module docstring for the full reasoning.

2. **`metatrader_client`'s own `send_order()` silently drops `magic`.** Confirmed by tracing
   the full call chain (`metatrader_mcp/server.py` → `metatrader_client/client_order.py` →
   `metatrader_client/order/*.py` → `metatrader_client/order/send_order.py` → raw
   `MetaTrader5.order_send()`). Not a missing-tool gap like `SymbolInfo` was — the bug lives
   inside the third-party library's deepest order-construction function itself, so there's no
   already-correct capability one layer down to expose instead, unlike the `get_symbol_info`/
   `get_positions_with_magic` fixes. Every real order this system places lands in MT5 with
   `magic=0`, `comment="MCP"` (forced), hardcoded `deviation=20` (market orders; pending orders
   respect a caller value but no wrapper exposes one), broker-auto-selected `filling_mode`
   (any caller value ignored), no `expiry` (never reaches MT5 despite being supported deep in
   `send_order()`), and market orders can't carry SL/TP at placement at all (dropped in
   `MT5Order.place_market_order`, need a follow-up `modify_position` call). **Worst finding**:
   success/failure is determined by `mt5.last_error()` (terminal-level), never
   `response.retcode` (the broker's actual decision) — a genuinely rejected trade (requote,
   invalid stops, no money, market closed, etc.) can report `error: False`. Bulk-action tools
   (`close_all_*`, `cancel_all_*`) also swallow individual failures, always reporting success.
   Fully documented in `docs/mcp_tool_classification.md`, Known Issues item 7.

   **User's decision, deliberately NOT re-opened**: do not write new order-*placement* code to
   fix magic-forwarding at the source (would be the single highest-risk code this project could
   contain). Instead: populate the `state/` package with local, persistent ticket→intent
   records, reconciled against real MT5 state **by ticket only, never by symbol/timing**, with
   an explicit safety posture (`NORMAL`/`MANAGE_ONLY`/`BLOCKED`) that refuses new submissions —
   or even management actions on unattributed tickets — whenever local state can't be trusted.
   The retcode-trust bug has no such workaround: `metatrader_retcodes.py` handles it correctly
   from the first line, unconditionally.

## Work completed (Steps 0–3 of the approved plan; no live call in any of them)

- **Step 0** — `config/settings.py`: `Settings.mt5_account_kind: Optional[str]`, read from
  `MT5_ACCOUNT_KIND`. `mt5_adapter/safety.py`: `require_demo_account_kind()` (new, hard gate)
  alongside the now-clearly-documented informational `require_demo_account()`. `.env.example`
  updated. `docs/mcp_tool_classification.md` Known Issues item 7 added (the trading-tool bugs
  above), explicitly marked **deferred, not fixed**.
- **Step 1** — `state/` package populated: `models.py` (`LocalOrderRecord` — captures order
  intent, request parameters, execution response, ticket, symbol, side, volume, timestamps,
  strategy identifier, exactly as required; `ReconciliationReport`), `store.py` (`StateStore`
  — atomic JSON persistence at a caller-supplied path, cold-start-safe, raises
  `StateLoadError` rather than silently treating a corrupt file as empty), `reconcile.py`
  (pure, ticket-only cross-check — proven by a same-ticket-different-symbol test that it never
  matches on anything else), `policy.py` (`ExecutionPosture`: `NORMAL`/`MANAGE_ONLY`/`BLOCKED`,
  `determine_posture()`).
- **Step 2** — `mt5_adapter/metatrader_retcodes.py` (`parse_trade_response()` — reads `data`,
  requires a `retcode` key, **never** reads the tool's own `error`/`message` for success;
  `data: None` — a pre-flight rejection before `MT5.order_send()` was ever called — is
  represented as `retcode=None`, distinct from a real unfavorable retcode).
  `state/strategy_registry.py` (`strategy_name_for_magic()` — explicit `{71101: "grid", 72101:
  "runner"}` map, unmapped magics return a loud `"unknown_magic_<n>"`, never a guess).
  `mt5_adapter/mcp_order_executor.py` (`McpOrderExecutor` — `submit()` for `LIMIT` orders only
  in this step, `cancel()`; `close_position()` and `MARKET` orders explicitly raise
  `NotImplementedError`, deferred to later steps). Re-read `send_order.py`/
  `place_pending_order.py`/`cancel_pending_order.py` directly before writing any of this, per
  the plan — confirmed `place_pending_order`'s client wrapper takes only
  `type/symbol/volume/price/stop_loss/take_profit`, nothing else, and both wrappers' response
  shape is `{"error": bool, "message": str, "data": <raw OrderSendResult-shaped dict> | None}`.
- **Step 3** — `execution/composition.py` (`demo_execution_session()` — the one place
  `trading_enabled=True` is ever constructed outside a test; gates on `settings.mode ==
  DEMO_EXECUTION`, `settings.trading_enabled`, and `require_demo_account_kind()`, in that
  order, all **before** constructing `ToolRegistry`/`McpClient`/spawning anything).

```
pytest -q                        -> 276 passed (250 previously + 26 new across Steps 0-3)
pytest tests/test_architecture.py -q -> 13 passed
```

Legacy project confirmed untouched throughout. No live MCP call was made at any point in
planning or in Steps 0–3. No trading tool had been invoked as of this point. `.env`/
credentials were not read.

## Step 4 — first live call, with explicit approval: ran, failed safely, root cause fixed

Ran `scripts/run_demo_execution_smoke_test.py` live. **No order was placed.** Two things
happened, both handled correctly:

1. **MT5 rejected the order with retcode `10027` ("AutoTrading disabled by client")** — a
   terminal-side setting (the AutoTrading toggle in the MetaTrader 5 desktop application) that
   must be enabled before any program can place any order, completely unrelated to this
   project's code. This is an environment/operator prerequisite, not a bug — cannot be resolved
   from code; someone has to click the AutoTrading button in the actual terminal window.
2. **The genuine, previously-flagged uncertainty about `data`'s shape was real, and caught
   safely.** `data` did not arrive as a dict — it arrived as a **positional JSON array**
   matching MT5's documented `OrderSendResult` field order exactly:
   `[10027, 0, 0, 0.0, 0.0, 0.0, 0.0, "AutoTrading disabled by client", 0, 0, [...]]` ≡
   `retcode, deal, order, volume, price, bid, ask, comment, request_id, retcode_external,
   request`. `parse_trade_response()` correctly refused to guess and raised
   `MalformedTradeResponseError` — no state was written, nothing was misinterpreted as
   success. Exactly the "build from documented shape, verify live, correct if wrong" pattern
   already used for `SymbolInfo`/`get_positions_with_magic`.

**Fixed**: `mt5_adapter/metatrader_retcodes.py` now accepts both a positional list (the
confirmed real shape — maps it onto the documented field names) and a dict (kept as a
fallback in case a future package version changes this), rejecting anything else exactly as
before. `tests/unit/test_mt5_adapter_mcp_order_executor.py`'s fixtures updated to the real
list shape; the retcode-trust regression test now uses this real captured response (`data`
verbatim, `message` reconstructed from `place_pending_order.py`'s confirmed f-string, not
independently captured but read directly from source, not guessed) instead of a synthetic
dict. Added a dedicated `test_parse_trade_response_extracts_retcode_from_a_positional_list`
proving the positional field mapping itself, not just that some rejection is detected.

```
pytest -q                        -> 277 passed (276 previously + 1 new)
pytest tests/test_architecture.py -q -> 13 passed
```

Legacy project confirmed untouched before and after. No order was ever placed, modified, or
closed — the only outcome of this live call was a clean rejection and a safe parse failure,
both now understood and (for the parse failure) fixed.

## Step 4 retry #1 — still AutoTrading-disabled, but the fix confirmed correct

Re-ran the smoke test with explicit approval, before the user had changed anything in the
terminal. Result: **same retcode `10027`**, but this time `parse_trade_response()` parsed it
cleanly — correct `ExecutionResult(success=False, retcode=10027, ticket=None, verified=False,
...)`, no exception, no state written, script exited safely printing "No ticket was returned --
nothing to cancel." Confirms the list-shape fix from the previous attempt is correct on a
second real response, independent of the AutoTrading question. Legacy project confirmed
untouched.

## Step 4 retry #2 — SUCCESS: first order this project has ever placed and closed for real

User confirmed, before this retry: the terminal's AutoTrading toolbar button is enabled, Tools
→ Options → Expert Advisors → "Allow algorithmic trading" is checked, and this is the same
ThinkMarkets Demo terminal `MT5_PATH` points to. Explicit instructions for this specific
retry: demo account only, minimum lot, one attempt, no automatic retry, report the exact
retcode and MT5 state, stop immediately after. All honored.

**Submit**: retcode `10009` (`TRADE_RETCODE_DONE`). Real order placed: ticket `171604513`,
BTCUSD LIMIT BUY, `0.01` lots (live `volume_min`) @ `57073.69` (~10% below the live bid at the
time, chosen so it could never fill during the script's runtime). `success=True`,
`verified=True` — confirmed present via a fresh `get_pending_orders_with_magic` read
immediately after. MT5 itself reported `magic=0` on the real ticket, exactly as documented
(gap 7); the local `state/order_state.json` record correctly captured the *intended*
`magic=79999` and, since `79999` isn't in `strategy_registry.py`'s known map, correctly fell
back to the loud `strategy='unknown_magic_79999'` label rather than guessing.

**Cancel**: retcode `10009` (`TRADE_RETCODE_DONE`). `success=True`, `verified=True` —
confirmed absent via a fresh live read immediately after. Local record transitioned
`OPEN → CANCELLED` with `closed_at`/`closed_reason` populated.

No position or pending order was left on the account. This closes out the one previously-open
item from Steps 0–3 ("the success/verification/state-recording path has never been observed
live") — it now has. Legacy project confirmed untouched before and after. No code was changed
by this retry; `var/order_state.json` (gitignored) is the only file that changed on disk.

## Step 5 — close_position(), implemented and unit-tested; live attempt #1 correctly refused

Approved scope, explicitly bounded by the user: `close_position()` only. No MARKET-order
testing. No pipeline wiring. Inspect and document the close path before writing code. Tests
before any live call.

**Research** (read `metatrader_client/order/close_position.py`/`client_order.py` directly, not
assumed): `close_position(id)` takes only a ticket, **no volume parameter anywhere in the
stack** — it always closes a position's full size; there is no partial-close capability to
call even if wanted. It looks up the position first (a real read), then sends a DEAL-action
`order_send()` with `"position": ticket` set explicitly — the same detail the legacy project's
own comments flagged as important for hedging accounts (omitting it can open a new position
instead of closing the existing one); this implementation already gets it right. Response
reuses the exact same `OrderSendResult`-shaped payload already confirmed live in Step 4 (a
positional list) — no new parsing work needed in `metatrader_retcodes.py`.

**Implemented**: `McpOrderExecutor.close_position(ticket, volume=None)` in
`mt5_adapter/mcp_order_executor.py` — same gating pattern as `submit()`/`cancel()`
(`require_demo_account_kind` hard gate, informational `require_demo_account`, fresh posture
check, `BLOCKED`/`MANAGE_ONLY` enforcement identical to `cancel()`'s unattributed-ticket
refusal). If a caller passes an explicit `volume` that doesn't match the position's live full
size, raises `NotImplementedError` *before* any MCP call — never silently closes the whole
position when a different amount was asked for. On a confirmed-done retcode, calls
`state_store.record_closed(...)` and verifies by re-reading live positions, matching by ticket
only.

**Tests**: `tests/unit/test_mt5_adapter_mcp_order_executor.py` — 9 new tests (full-close
success with state recording, explicit-volume-matches-full-size allowed, partial-volume
request rejected before any MCP call, retcode-trust regression (a live-plausible "Market
closed" `10018` rejection reported cleanly, not as success), an "invalid position ID" case
with no retcode at all, verification-fails-when-still-present, the demo-account hard gate, and
both `MANAGE_ONLY` cases (allowed for a matched ticket, refused for an unattributed one)).
Registry-classification test extended to cover `close_position` too.

```
pytest -q                        -> 286 passed (277 previously + 9 new)
pytest tests/test_architecture.py -q -> 13 passed
```

New script `scripts/run_demo_execution_close_smoke_test.py` — deliberately does NOT open a
position itself (per the approved scope); requires the user to open exactly one minimum-lot
position on `TARGET_SYMBOL` manually first, lists live positions and refuses to proceed unless
exactly one match exists, warns (doesn't silently proceed past) if its volume isn't the
symbol's live `volume_min`, performs exactly one `close_position()` call, and stops.

**Live attempt #1 — correctly refused, no MT5 action taken.** User manually opened one BTCUSD
position (ticket `171604527`, `BUY`, `0.01` lots — confirmed via a live read before the close
attempt). The script called `close_position(171604527, volume=0.01)`, and `McpOrderExecutor`
refused it **before any RPC was sent** — `state/order_state.json` had no record for this
ticket (it was opened directly in the terminal, not through `McpOrderExecutor.submit()`), so
reconciliation correctly classified it `unknown_real` → `ExecutionPosture.MANAGE_ONLY`, which
by design refuses to touch any ticket it can't attribute to a local record. **This is the
safety mechanism working exactly as designed, not a bug** — but it means the manual-position
approach chosen for Step 5 has a real consequence: a position opened outside
`McpOrderExecutor` has no path to being closed by it without some explicit attribution step.
The position remains open and untouched; no MT5 state changed.

**Proposed, not yet done, awaiting approval**: write one local state record for ticket
`171604527` based on the human-verified detail already confirmed (ticket/symbol/side/volume
read live and stated by the user) — not a guess, an explicit attribution — then a fresh,
separately-approved single close attempt. The "one attempt, no automatic retry" instruction
for attempt #1 was fully honored (no MCP trading-tool call was made at all); a second attempt
requires new, separate approval, not a continuation of the first.

Legacy project confirmed untouched. No code was changed by this live attempt.
`git status` at this point: `mcp_order_executor.py` and its test file modified,
`run_demo_execution_close_smoke_test.py` new — none of this step's code committed yet.

## Step 5, manual-adoption workflow — SUCCESS: real position closed for real, honestly recorded

User approved an explicit, narrowly-scoped manual-adoption workflow for this one ticket only,
with hard requirements: never fabricate the position as system-originated; record it as an
explicit, clearly-marked external adoption; require an exact match on ticket/symbol/side/volume
against a fresh live read before adopting; abort without sending any close request on any
mismatch; one close attempt, no retry; report request/retcode/execution result/state
before-and-after; never commit real ticket data.

**Schema change** (`state/models.py`, `state/store.py`): added `LocalOrderRecord.origin:
Literal["system_owned", "manual_adoption"]` and made `retcode: Optional[int]` (was `int`) --
`manual_adoption` records genuinely have no retcode, since no submission ever happened to
receive one from; representing that as `None` rather than a fabricated/sentinel value is the
honest choice. `StateStore.record_submission()` now always sets `origin="system_owned"`
internally (no call-site changes needed anywhere it's already used). New
`StateStore.record_manual_adoption(ticket, symbol, side, volume, price_open, magic,
adopted_at, note)` sets `strategy="manual_adoption"`, `order_type="EXTERNAL_POSITION"`,
`retcode=None`, and populates `requested_*`/`executed_*` fields from values *independently
observed* via a live read (never a system request). `magic` is recorded as the REAL observed
value (confirmed `0`, per the already-documented upstream bug), never invented. Reading a
state file written before "origin" existed (Step 4's file) defaults it to `"system_owned"` --
the correct historical fact, not a guess, and avoids treating the pre-existing file as
corrupted. 3 new tests in `test_state_store.py` (system-owned default, manual-adoption honesty
checks, backward-compatible read of a pre-origin file); 1 existing `test_state_reconcile.py`
fixture updated for the new required field.

```
pytest -q                        -> 289 passed (286 previously + 3 new)
pytest tests/test_architecture.py -q -> 13 passed
```

`scripts/run_demo_execution_close_smoke_test.py` rewritten: requires an EXACT match against
hardcoded expected values (`EXPECTED_TICKET=171604527`, `EXPECTED_SYMBOL="BTCUSD"`,
`EXPECTED_SIDE="BUY"`, `EXPECTED_VOLUME=0.01`) on a fresh live read; aborts before any close
request on any mismatch or if the ticket isn't found; the expected account server
(`ThinkMarkets-Demo`) is explicitly documented as human-stated, not machine-verifiable over
this MCP server (no tool exposes login/server) -- `MT5_ACCOUNT_KIND=DEMO` remains the actual
trust boundary, same as every other live step this session.

**Live result — SUCCESS**:
- **Before**: one live position, ticket `171604527`, BTCUSD, BUY, `0.01` lots, `price_open=63440.91`,
  `profit=-0.73`, MT5-reported `magic=0` (as expected, per the known bug). Exact match
  confirmed against all four expected values.
- **Adoption recorded**: `origin='manual_adoption'`, `retcode=None`, `strategy='manual_adoption'`,
  full audit note stating this was manually opened and explicitly adopted, not system-originated.
- **Exact request**: `close_position(ticket=171604527, volume=0.01)`.
- **ExecutionResult**: `success=True`, `retcode=10009` (`TRADE_RETCODE_DONE`), `ticket=171604527`,
  `deal=99712724`, `executed_price=63368.26`, `verified=True`, `verification_details='ticket=171604527
  confirmed absent from live positions'`.
- **Local state after**: `status='CLOSED'`, `closed_reason='close confirmed via
  McpOrderExecutor.close_position()'`, `origin` still `'manual_adoption'` (never silently
  rewritten to `'system_owned'`).
- **Live positions on BTCUSD after**: `0`. No new position was opened at any point.

One attempt, no retry, exactly as instructed. Legacy project confirmed untouched before and
after. This is the first time `close_position()` has closed a real position, and the first
time this project's local state has honestly represented a human-originated action rather than
either fabricating or refusing to record it at all.

## Step 6 — planning, then implementation (6a-6d), no live call yet

**Planning approved.** Read `metatrader_client`/`metatrader_mcp` source directly (not assumed)
to confirm: `place_market_order` MCP tool signature is `(symbol, volume, type)` only — no
`sl`/`tp`/`magic`/`comment`/`deviation` at that layer at all, so a MARKET order always opens
completely naked. `modify_position(id, stop_loss, take_profit)` is the only path to attach
SL/TP afterward, and reuses the exact same `OrderSendResult`-shaped response already handled
by `metatrader_retcodes.parse_trade_response()` — no new parsing code needed. Full detail now
also in `docs/mcp_tool_classification.md`, Known Issues item 7 addendum.

User decisions locking scope:
1. SL/TP pre-validation: reject missing/zero/wrong-side SL/TP locally before any MCP call;
   do NOT attempt broker-side minimum-distance (`stops_level`/`freeze_level`) validation, since
   reliable `SymbolInfo` isn't available through the current MCP connection path — a `10016`
   rejection from `modify_position` is handled as a normal, expected-possible, trusted-retcode
   failure, not a bug.
2. Failure scope: if SL/TP attachment fails, mark and block only that exact ticket
   (`status="OPEN_UNPROTECTED"`), never the whole executor. No automatic retry, no automatic
   close. Other tickets/symbols continue operating normally. The only recovery path is an
   explicitly-approved action (typically `close_position()`) on that one ticket.

**Implemented (6a-6d), all mocked, zero live calls**:
- `domain/models.py`: `PositionState` gained `sl`/`tp` fields (default `0.0`, MT5's own
  no-stop convention).
- `mt5_adapter/mcp_account.py`: `get_positions()` now parses the `stop_loss`/`take_profit`
  columns `get_positions_with_magic` already returned but nothing read before now.
- `state/models.py`: `OrderRecordStatus` gained `"OPEN_UNPROTECTED"` — a real status value, not
  a separate boolean, so a partial/stale write can never look identical to a normally-protected
  `OPEN` record.
- `state/store.py`: `record_submission()` gained a `status` parameter (default `"OPEN"`,
  existing LIMIT call sites unchanged); new `mark_sl_tp_attached()` transitions
  `OPEN_UNPROTECTED` → `OPEN`; `all_open()` now includes `OPEN_UNPROTECTED` so reconciliation
  still recognizes the ticket as locally known (never `unknown_real`, so it never forces the
  whole executor into `MANAGE_ONLY` — this is the mechanism that satisfies decision 2 above
  without touching `state/policy.py` or `state/reconcile.py` at all).
- `mt5_adapter/mcp_order_executor.py`: `submit()` now dispatches LIMIT vs MARKET (existing
  LIMIT logic moved into `_submit_limit()`, unchanged). New `_submit_market()`: pre-flight
  `_validate_market_sl_tp()` (raises `InvalidOrderPlanError`, no MCP call yet) → one
  `place_market_order` call → on confirmed-done retcode, `record_submission(...,
  status="OPEN_UNPROTECTED")` **before** attempting attachment → `_verify_position_present()`
  (fresh live read) → exactly one `modify_position` call (any exception during the call itself
  is caught and re-raised as `SlTpAttachmentFailedError`, same as a rejected retcode) →
  `_verify_sl_tp_attached()` (fresh live read must agree with the retcode, not just trust it)
  → `mark_sl_tp_attached()` only if both agree. New `SlTpAttachmentFailedError` (carries
  ticket/order_plan/reason/retcode) and `InvalidOrderPlanError` exception classes.
- Tests: `tests/unit/test_mt5_adapter_mcp_account.py` (+1, non-zero sl/tp parsing proof).
  `tests/unit/test_mt5_adapter_mcp_order_executor.py` (+9): full happy path (place + attach +
  live-verify, status transitions to `OPEN`), missing/zero SL-TP rejected pre-flight,
  wrong-side SL/TP rejected pre-flight, place-rejected (no modify attempted), attach-rejected
  (marks `OPEN_UNPROTECTED`, exactly one attempt, no auto-close/cancel), attach-call-raises
  (proves state written before the attempt, the crash-mid-sequence scenario), attach
  retcode-done-but-live-read-disagrees (retcode alone is never sufficient), an unrelated LIMIT
  submission still succeeds despite another ticket being `OPEN_UNPROTECTED`, and an explicit
  `close_position()` still works normally on an `OPEN_UNPROTECTED` ticket (the one allowed
  recovery path). One existing test renamed/repointed:
  `test_submit_only_supports_limit_orders_in_this_step` → `test_submit_rejects_unsupported_order_types`
  (now asserts against a genuinely-unsupported `order_type`, since MARKET is no longer one).

```
pytest -q                        -> 299 passed (289 previously + 10 new)
pytest tests/test_architecture.py -q -> 13 passed
```

**Not done**: the live smoke test (a dedicated script mirroring
`run_demo_execution_close_smoke_test.py`'s pattern, minimum lot, one attempt) — this is its own
separate step requiring its own separate explicit approval, not started. No MARKET order has
ever been placed live; `_submit_market()` has never made a real MCP call.

**Remaining risks / open items**:
- SL/TP minimum-distance from price (`stops_level`/`freeze_level`) is not pre-validated
  locally — a live `10016` rejection is expected-possible and handled, but not yet observed for
  real. First live attempt should deliberately choose SL/TP comfortably far from price to avoid
  conflating "the attach-failure path works" with "the distance itself was the problem."
  Confirmed against a captured fixture in tests, not yet against a real broker response.
- `ticket = int(response.raw_data["order"])` for a MARKET fill is assumed to equal the
  resulting position's ticket (standard MT5 behavior on a hedging-style account, consistent
  with how Step 4 already treats the same field for LIMIT orders) — not yet independently
  live-confirmed for the DEAL/MARKET case specifically. The live smoke test should verify this
  by checking the returned ticket appears in `get_positions_with_magic` immediately after.
  `_verify_position_present()` will catch it (reports `verified=False`) if this assumption is
  ever wrong, rather than silently trusting it.
- `_SL_TP_TOLERANCE = 1e-6` in `mcp_order_executor.py` guards against float-serialization noise
  only, not real broker rounding to the symbol's price precision — may need widening once a
  live `modify_position` response's actual rounding behavior is observed.
- Pipeline wiring (`run_grid_cycle`/`run_runner_cycle`) remains explicitly out of scope for
  this whole phase's current plan, unaffected by this step.

## Step 6 — live smoke-test script built, then run (attach failed, ticket left OPEN_UNPROTECTED)

`scripts/run_demo_execution_market_smoke_test.py` added, mirroring
`run_demo_execution_close_smoke_test.py`'s pattern. Syntax- and import-checked (module-level
import succeeds under Python 3.12, `main()` never invoked) -- **no live call has been made**.

What it does, in order:
1. Constructs `Settings` with `mode=DEMO_EXECUTION` explicitly in code (same reasoning as every
   prior smoke test — doesn't depend on `.env`'s `MT5_MCP_MODE`).
2. **Abort check**: reads live positions for `SYMBOL="BTCUSD"` filtered to
   `magic=SMOKE_TEST_MAGIC (79999)` — if any already exist (e.g. an `OPEN_UNPROTECTED` leftover
   from a previous incomplete run), aborts before submitting anything rather than compounding
   exposure.
3. Reads a live tick and `SymbolInfo`, computes SL/TP as
   `tick.ask ± (stops_level + freeze_level + 2) * point * 10` (the same minimum-gap formula
   `order_planning/limit_price.py` uses for LIMIT prices, reused here with a 10x safety
   multiplier since this account's exact SL/TP-rejection boundary has never been observed
   live — chosen to clear it comfortably rather than spend this run's one attempt discovering
   the edge). Volume is the symbol's live `volume_min`.
4. Exactly one `executor.submit(order_plan)` call — internally exactly one
   `place_market_order` call, then (only if that succeeds) exactly one `modify_position` call,
   per `_submit_market()`'s existing no-retry design.
5. If `SlTpAttachmentFailedError` is raised: reports ticket/reason/retcode, **does not attempt
   any cleanup**, and stops — per the approved design, recovery from a failed attach requires
   its own separate, explicitly-approved action, not automated remediation in the same run.
6. If `submit()` returns `success=False` (place itself rejected): nothing was opened, nothing
   to clean up, stops.
7. Only on full success (position opened AND SL/TP confirmed attached via a fresh live read):
   prints the result, then performs this script's own designed cleanup — one
   `close_position()` call, mirroring how Step 4 always cancels its own successfully-placed
   test order. Prints local state and live positions before and after the close.

**New tests added this session** (state-layer, direct — the executor-level MARKET tests were
already added in the prior "Step 6 — planning, then implementation" session): `mark_sl_tp_attached()`
transitions `OPEN_UNPROTECTED` → `OPEN` without touching `closed_at`/`closed_reason`;
`record_submission(status="OPEN_UNPROTECTED")` round-trips and is included in `all_open()`;
`mark_sl_tp_attached()` on an unknown ticket logs and does not raise (mirrors the existing
`record_cancelled`/`record_closed` unknown-ticket test).

```
pytest -q                        -> 302 passed (299 previously + 3 new)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed this session**: `scripts/run_demo_execution_market_smoke_test.py` (new),
`tests/unit/test_state_store.py` (+3), this checkpoint doc.

**Remaining risks** (in addition to the three logged in the prior session's entry above, all
still open, none newly resolved by this session — no live call was made):
- The `GAP_SAFETY_MULTIPLIER = 10` choice is a reasoned guess, not yet validated against a real
  broker response for this account/symbol. If the live attempt's `modify_position` still gets
  rejected (`10016`), that's expected-possible per this step's design (see the prior session's
  entry) and will surface exactly as `SlTpAttachmentFailedError` — not a script bug, but worth
  knowing before running: it means the position stays open and unprotected until a separately
  approved recovery action.
- The abort-on-existing-magic check (step 2 above) only looks at `SYMBOL="BTCUSD"` with
  `magic=SMOKE_TEST_MAGIC` — it would not catch a leftover from a *different* symbol if this
  script were ever repurposed. Not a concern for this specific run, since BTCUSD is the only
  symbol every prior Phase 6 live step has used.

**Live result — 2026-08-03, one attempt, exactly as designed**:

Preflight (all confirmed before running): required `.env` vars present (values never
displayed); `MT5_PATH` pointed at "TF Global Markets MetaTrader 5 Terminal" -- user confirmed
this is ThinkMarkets' legal entity name, correct terminal; `MT5_ACCOUNT_KIND=DEMO`; terminal
open with AutoTrading enabled (user-confirmed, unverifiable by any tool on this MCP server).

- **`place_market_order`**: retcode `10009` (`TRADE_RETCODE_DONE`). Real position opened:
  ticket `171617865`, BTCUSD BUY, `0.01` lots (live `volume_min`) @ `62880.2`.
- **`modify_position` (SL/TP attach)**: retcode `10016` ("Invalid stops") -- **rejected**. The
  tool's own message string said `"...success, SL at 62879.0, TP at 62881.4, current price
  0.0"` despite the real retcode being a rejection -- a **live-confirmed instance of the known
  retcode-trust bug (Known Issues item 7), now confirmed for `modify_position` specifically**
  (previously only observed for place/cancel/close). `parse_trade_response()` correctly ignored
  the misleading message and read the real retcode; `SlTpAttachmentFailedError` was raised
  exactly as designed.
- **Local state**: `record_submission(..., status="OPEN_UNPROTECTED")` had already been written
  before the attach attempt (confirmed by reading `var/order_state.json` after the run --
  `retcode=10009`, `requested_sl=62879.0`, `requested_tp=62881.4`, `strategy=
  "unknown_magic_79999"`, `status="OPEN_UNPROTECTED"`). No automatic retry or close was
  attempted -- the script stopped immediately after printing ticket/reason/retcode, exactly as
  designed.
- **Post-run read-only verification** (a fresh, separate read, no mutating call): ticket
  `171617865` is present among live BTCUSD positions with `sl=0.0, tp=0.0` -- confirmed
  genuinely open and unprotected, matching the local record exactly.

**Root cause of the attach failure**: `GAP_SAFETY_MULTIPLIER=10` was far too small for BTCUSD.
`stops_level=10` points x `point=0.01` x 10 = a $1.20 offset on a ~$62,880 price (~0.002%) --
evidently still inside the broker's real minimum distance. A points-based formula scales badly
for a high-priced instrument like this; needs a much larger multiplier or a percentage-of-price
approach before any retry.

**New bug found, not previously known**: the script's own pre-submission "abort if a leftover
position already exists" check filters live positions by `magic=SMOKE_TEST_MAGIC` -- but MT5 is
confirmed (again, live, just now) to always report `magic=0` on positions this project places,
regardless of what was requested. That abort check would therefore silently pass (report zero)
even with a genuine `OPEN_UNPROTECTED` leftover still open, exactly the scenario this run just
created. **Must be fixed to check local `StateStore` instead of live MT5 `magic`** before this
script is safely re-run.

**Recovery — SUCCESS, same session, user approved closing the position**:

A fresh live read (no magic filter, matching Step 5's discipline) re-confirmed an exact match
on ticket/symbol/side/volume before sending anything. Local state (`status="OPEN_UNPROTECTED"`)
was printed before, for the record.

- **Request**: `close_position(ticket=171617865)`.
- **ExecutionResult**: `success=True`, `retcode=10009` (`TRADE_RETCODE_DONE`),
  `ticket=171617865`, `deal=99723684`, `executed_price=62893.9`, `verified=True`,
  `verification_details='ticket=171617865 confirmed absent from live positions'`.
- **Local state after**: `status='CLOSED'`, `closed_reason='close confirmed via
  McpOrderExecutor.close_position()'`, `origin` still `'system_owned'` (unchanged).
- **Live positions after**: ticket `171617865` confirmed absent. Zero open positions/exposure
  remaining from this step.

One attempt, no retry, exactly as instructed. The account is clean: nothing is currently open
from Phase 6 Step 6's work.

```
pytest -q                        -> 302 passed (unchanged by this live run -- no code changed)
```

**Exact live-test procedure** (as actually executed, kept for reference/repeatability):
1. Confirm required `.env` vars present, `MT5_PATH`/`MT5_ACCOUNT_KIND` correct, terminal open
   with AutoTrading enabled -- all via explicit human confirmation, values never displayed.
2. Run `.venv/Scripts/python.exe scripts/run_demo_execution_market_smoke_test.py` once.
3. Read the full output.
4. If `SlTpAttachmentFailedError` is raised: stop, report the ticket, treat it as needing its
   own separate recovery approval -- do not re-run this script or attempt any close/retry
   without a fresh, explicit go-ahead for that specific ticket. (This is what happened.)
5. If it succeeds end-to-end: confirm zero positions remain for that ticket before considering
   the step done.
6. Report retcode/ticket/verified for submit (and close, if reached), plus local state
   before/after, in this checkpoint.

## Step 6 — smoke-test script's two known bugs fixed (no re-run yet)

Both issues found by the live run above are fixed in
`scripts/run_demo_execution_market_smoke_test.py`:

1. **Leftover-detection bug**: the pre-submission abort check now reads
   `state_store.all_open()` filtered to `magic == SMOKE_TEST_MAGIC`, instead of a live
   `account.get_positions(symbol=SYMBOL, magic=SMOKE_TEST_MAGIC)` call — which could never
   actually detect a leftover, since MT5 is confirmed (live, this same session) to always
   report `magic=0` on positions this project places. The live-position read is kept for
   visibility/audit but no longer filtered by magic and no longer the abort gate. The "after"
   printout was fixed the same way for consistency, plus now explicitly checks whether the
   just-closed ticket is still present rather than relying on a magic-filtered count.
2. **Undersized SL/TP margin**: `offset` is now `max(gap * GAP_SAFETY_MULTIPLIER,
   reference_price * MIN_SL_TP_FRACTION_OF_PRICE)` — a new `MIN_SL_TP_FRACTION_OF_PRICE = 0.01`
   (1% of price) floor, since the live run showed the previous 10x-gap-only offset ($1.20 on
   ~$62,880 BTCUSD, ~0.002%) was rejected with retcode `10016`. The gap-multiplier term is kept
   as the floor for low-priced instruments where it could dominate instead.

No unit tests exist for this script (consistent with `run_demo_execution_smoke_test.py`/
`run_demo_execution_close_smoke_test.py`, neither of which have dedicated tests either — all
three are live-only, driven by `demo_execution_session()` and real `.env`/subprocess wiring
that isn't meaningfully mockable at the script level). Verified instead via `ast.parse()`
(syntax) and a module-level `runpy` import check (all imports resolve, `main()` never
invoked) — same verification used when the script was first written.

```
pytest -q                        -> 302 passed (unchanged -- no production code touched)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed this session**: `scripts/run_demo_execution_market_smoke_test.py` only.

## Step 6 — re-run: SUCCESS end-to-end, first full live proof of the MARKET path

**Live result — 2026-08-03, one attempt, both fixes confirmed working**:

Preflight leftover check: `state_store.all_open()` filtered to `magic=79999` → 0 records.
Correctly passed (account was clean from the prior recovery).

- **`place_market_order`**: retcode `10009` (`TRADE_RETCODE_DONE`). Real position opened:
  ticket `171618036`, BTCUSD BUY, `0.01` lots @ `62881.66`.
- **Computed SL/TP** (with the fixed formula): `gap_offset=1.2` (10x gap, the old
  insufficient value) vs `price_fraction_offset=628.82` (1% of `62881.63`) — the 1% floor
  correctly dominated. `sl=62252.81`, `tp=63510.45`.
- **`modify_position` (SL/TP attach)**: retcode `10009` — **succeeded this time**. Live read
  confirmed `sl=62252.81`/`tp=63510.45` matching exactly on the first verification attempt.
- **`submit()` result**: `success=True`, `verified=True`. Local state transitioned
  `OPEN_UNPROTECTED` → `OPEN` (confirmed: "state before close" printout already shows
  `status='OPEN'`, proving `mark_sl_tp_attached()` fired correctly).
- **Cleanup `close_position()`**: retcode `10009`, `success=True`, `verified=True`,
  `executed_price=62866.66`, confirmed absent from live positions immediately after.
- **Local state after**: `status='CLOSED'`.
- **Final live check**: 0 positions on BTCUSD, ticket `171618036` confirmed absent.

**This closes out Step 6's live-proof requirement.** Every branch of `_submit_market()`
(place, mandatory SL/TP attach with live verification, and the designed cleanup close) is now
live-proven end-to-end on the demo account, not just unit-tested — the same bar Step 4 met for
LIMIT orders and Step 5 met for `close_position()`. The account is clean: nothing open from
this step.

```
pytest -q -> 302 passed (unchanged by this live run -- no code changed)
```

**Remaining risk, unchanged**: `MIN_SL_TP_FRACTION_OF_PRICE=0.01` worked on this one live
attempt but is still a generous guess, not a confirmed broker minimum — the true minimum
distance for BTCUSD on this broker remains unpublished and unqueryable via this MCP server.
Fine as-is for this smoke test's purposes; worth revisiting if this margin is ever reused
for real strategy logic rather than a self-closing smoke test.

## Step 7 — MARKET SELL-side: mocked coverage added, live script updated, not yet run

**Note on numbering**: this "Step 7" is a Phase-6-internal sub-step (continuing the Steps 0-6
sequence tracked in this doc), NOT the project's overall numbered "Phase 7 (regression and
failure testing)" from `AGENTS.md`'s phase list — those are two different sequences that happen
to share a number. Don't confuse them; Phase 7 proper has not started.

**Gap found**: Step 6 live-proved `_submit_market()` for `side="BUY"` only. Neither
`_validate_market_sl_tp()`'s SELL branch (`tp < price < sl` check) nor the full SELL flow
through `_submit_market()` had ANY test coverage, mocked or live, until this step — a real gap
against this project's own rule that every new adapter capability needs passing- and
failure-case tests before being considered done.

**Implemented (mocked only, zero live calls)**:
- `tests/unit/test_mt5_adapter_mcp_order_executor.py`: `SUCCESS_MARKET_SELL_PLACE_JSON`/
  `SUCCESS_MODIFY_POSITION_SELL_JSON` fixtures (mirror the existing BUY fixtures exactly, sides
  swapped). Two new tests: `test_submit_market_sell_success_places_and_attaches_protection`
  (full SELL happy path — place, attach, live-verify, state transitions `OPEN_UNPROTECTED` →
  `OPEN`, mirrors the BUY happy-path test) and
  `test_submit_market_rejects_wrong_side_sl_tp_for_sell_before_any_mcp_call` (SELL with sl/tp
  in BUY order must be rejected pre-flight, no MCP call — exercises the previously-untested
  `else:` branch of `_validate_market_sl_tp()`).
- `scripts/run_demo_execution_market_smoke_test.py` generalized: new `SIDE = "SELL"` constant
  (set for this step; flip back to `"BUY"` to re-exercise that side). `reference_price` now
  `tick.ask` for BUY / `tick.bid` for SELL; `sl`/`tp` placement around `reference_price` now
  flips direction by side, matching `_validate_market_sl_tp()`'s requirement exactly (BUY:
  `sl < price < tp`; SELL: `tp < price < sl`). `comment` now
  `f"phase6_step7_market_{SIDE.lower()}_smoke_test"`. Everything else (leftover-check via
  `StateStore`, the 1%-of-price SL/TP floor, one-attempt/no-retry, no auto-cleanup on
  `SlTpAttachmentFailedError`, cleanup-close only on full success) is unchanged from Step 6's
  already-fixed version. Syntax/import-checked; `main()` never invoked.

```
pytest -q                        -> 304 passed (302 previously + 2 new)
pytest tests/test_architecture.py -q -> 13 passed
```

**Files changed this step**: `tests/unit/test_mt5_adapter_mcp_order_executor.py`,
`scripts/run_demo_execution_market_smoke_test.py`, this checkpoint doc.

**Live result — 2026-08-03, one attempt, SUCCESS on the first try**:

Preflight leftover check: `state_store.all_open()` filtered to `magic=79999` → 0 records.
Correctly passed (account was clean from Step 6).

- **`place_market_order`** (`type="SELL"`): retcode `10009` (`TRADE_RETCODE_DONE`). Real
  position opened: ticket `171618202`, BTCUSD SELL, `0.01` lots @ `62771.0`.
- **Computed SL/TP**: `reference_price=62771.0` (live `bid`, correct for SELL — BUY used
  `ask`), `offset=627.71` (the 1%-of-price floor dominated again). Placement correctly flipped
  vs. BUY: `sl=63398.71` (**above** price), `tp=62143.29` (**below** price), matching
  `_validate_market_sl_tp()`'s SELL requirement (`tp < price < sl`) exactly.
- **`modify_position` (SL/TP attach)**: retcode `10009` — succeeded. Live read confirmed
  `sl=63398.71`/`tp=62143.29` matching exactly on the first verification attempt. Unlike Step
  6's first BUY attempt, no attach failure occurred here — the 1%-of-price margin (already
  fixed after Step 6's BUY failure) comfortably covered SELL too, on the first try.
- **`submit()` result**: `success=True`, `verified=True`. Local state transitioned
  `OPEN_UNPROTECTED` → `OPEN`.
- **Cleanup `close_position()`**: retcode `10009`, `success=True`, `verified=True`,
  `executed_price=62797.17`, confirmed absent from live positions immediately after.
- **Final live check**: 0 positions on BTCUSD, ticket `171618202` confirmed absent.

**This closes out Step 7.** Both MARKET sides (BUY: Step 6; SELL: this step) are now live-proven
end-to-end — placement, mandatory SL/TP attach with live verification, and cleanup close.
Account is clean, nothing open from this work.

```
pytest -q -> 304 passed (unchanged by this live run -- no code changed)
```

**Continuation prompt for the next session**:
> Phase 6 Steps 6 and 7 are both complete and fully live-proven: MARKET orders on both BUY
> (Step 6, tickets `171617865`/`171618036`) and SELL (Step 7, ticket `171618202`) sides, with
> mandatory SL/TP attach observed both failing (Step 6's first BUY attempt, cleanly recovered)
> and succeeding (every attempt since). Read this checkpoint's "Step 6"/"Step 7" sections in
> order and `mt5_adapter/mcp_order_executor.py`'s module docstring for full context. The account
> is currently clean. Per "Incomplete / explicitly deferred" below, the next not-yet-started
> items are: wiring `McpOrderExecutor` into `run_grid_cycle`/`run_runner_cycle` for autonomous
> trading (a separate, later-approved effort), or the project's overall "Phase 7" (regression
> and failure testing, not to be confused with this doc's Step 7) — ask the user which they want
> before starting either. Do not make any live MCP/MT5 call, and do not begin pipeline wiring or
> a new phase, without explicit approval.

## Incomplete / explicitly deferred — do NOT treat as done

- ~~No order has ever been placed, and `McpOrderExecutor`'s success path has never been
  observed live~~ — **resolved**: Step 4 retry #2 placed and cancelled a real order (ticket
  `171604513`), retcode `10009` both ways, `verified=True` both ways. Every branch of
  `submit()`/`cancel()` for LIMIT orders is now live-proven, not just unit-tested.
- `close_position()` is implemented and unit-tested but **not yet live-proven** — live attempt
  #1 was correctly refused by the `MANAGE_ONLY` posture check before reaching MT5 at all (see
  "Step 5" above), because the test position was opened manually, outside local state
  tracking. A real MT5 close (retcode `10009`, verified) has not yet been observed.
- `submit()` raises `NotImplementedError` for anything other than `order_type="LIMIT"` — MARKET
  orders (Step 6) need a mandatory SL/TP follow-up via `modify_position` since
  `place_market_order` cannot carry them at placement; highest-consequence step, done last.
- Wiring `McpOrderExecutor` into `run_grid_cycle`/`run_runner_cycle` for autonomous cycle-driven
  trading is explicitly out of scope for this whole phase's current plan — a separate,
  later-approved effort (Step 8 in the original plan), not assumed to follow automatically.
- `AGENTS.md`'s Progress section still says "Phase 6: not started" as of this checkpoint —
  needs updating to reflect Steps 0–3 done, Step 4 next, once this step is reviewed/committed.
- Nothing from this phase has been committed yet (see git status below) — do not assume any of
  this work is saved until a commit is made.

## Safety constraints that must remain enforced (re-affirm in a new session)

- Never call a TRADING-classified MCP tool without both `require_demo_account_kind()` passing
  AND the request having gone through `order_planning`/risk validation first (untouched by this
  phase — `McpOrderExecutor` takes an already-built `OrderPlan`, never constructs one itself).
- Never fabricate a `retcode` — `parse_trade_response()` must always be the only place success
  is decided, and it must always raise loudly rather than guess on an unexpected shape.
- Never let `McpOrderExecutor.submit()`/`cancel()` proceed when `ExecutionPosture` is anything
  but `NORMAL` (`cancel()` may additionally proceed on `MANAGE_ONLY` for `matched`/`local_only`
  tickets only — never `unknown_real`).
- `.env` (real demo credentials) must never be read, logged, or displayed.
- Legacy project (`../RealTrade/2509_17_mix_supercross`) must remain untouched — re-verify
  `git status` there is clean before and after any further work.
- No trading tool may be called without explicit, separate user approval for that specific live
  step — honored for every call so far, including Step 4's three live attempts (place/cancel
  both eventually succeeded, with explicit approval each time, ending in "one attempt, no
  automatic retry" for the successful one specifically).

## Exact next smallest task

1. **Decision needed from the user**: approve (or reject/modify) writing one local state
   record for ticket `171604527` (based on the human-verified ticket/symbol/side/volume
   already confirmed), then a fresh, single, separately-approved `close_position()` attempt.
   Not started — this checkpoint stops here per the user's explicit "stop for approval"
   instruction.
2. Once Step 5's live close is proven (whichever way it ends up being unblocked), verify
   legacy repo still untouched, then commit Step 5's code (`mcp_order_executor.py`'s
   `close_position()`, its tests, `run_demo_execution_close_smoke_test.py`, and this
   checkpoint's updates) as one commit — wait for explicit approval first. Note:
   `var/order_state.json` (real ticket data) is gitignored and must never be committed.
3. Update `AGENTS.md`'s Progress section once Step 5 is committed.
4. Only after that: Step 6 (MARKET orders + mandatory SL/TP follow-up) — its own, separately-
   approved step, not something to start without being asked.

## Note: Steps 0–4 are already committed

Commits `c70bb0a` (Steps 0–3 + Step 4 attempts #1/#2) and `93d6834` (Step 4 retry #2's
successful live verification, documentation only) both landed earlier in this phase, with
explicit approval each time. Only Step 5's work (this section of the checkpoint) is
uncommitted as of this writing.
