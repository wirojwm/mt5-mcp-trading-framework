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

**Exact next step**: build a dedicated live smoke-test script (minimum lot, explicit expected
values, one attempt, same pattern as `run_demo_execution_close_smoke_test.py`), then request
separate, explicit approval before running it. No live call, and no such script, exists yet.

**Continuation prompt for the next session**:
> Continue Phase 6 Step 6. Planning and implementation (6a-6d) are done and committed — read
> this checkpoint's "Step 6" section and `mt5_adapter/mcp_order_executor.py`'s module docstring
> first. Next: build the live smoke-test script for a single minimum-lot MARKET order with
> mandatory SL/TP (mirror `scripts/run_demo_execution_close_smoke_test.py`'s pattern: exact
> expected-value checks, one attempt, no retry), then stop and wait for explicit approval
> before running it. Do not make any live MCP/MT5 call without that separate approval.

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
