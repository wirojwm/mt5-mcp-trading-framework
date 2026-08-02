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

## Incomplete / explicitly deferred — do NOT treat as done

- ~~No order has ever been placed, and `McpOrderExecutor`'s success path has never been
  observed live~~ — **resolved**: Step 4 retry #2 placed and cancelled a real order (ticket
  `171604513`), retcode `10009` both ways, `verified=True` both ways. Every branch of
  `submit()`/`cancel()` for LIMIT orders is now live-proven, not just unit-tested.
- `close_position()` raises `NotImplementedError` unconditionally — Step 5, needs an actual
  filled position (real spread cost, first time equity moves), its own approval point.
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

1. ~~Propose (don't auto-run) re-running `scripts/run_demo_execution_smoke_test.py` to prove
   the success/verification/state-recording path for real~~ — **done**: Step 4 retry #2
   succeeded live (ticket `171604513`, placed and cancelled cleanly). See "Step 4 retry #2"
   above.
2. Verify legacy repo still untouched, then commit everything from this checkpoint (Steps 0–3,
   all three Step 4 live attempts and their results, the `metatrader_retcodes.py` fix and its
   tests) as one commit — wait for explicit approval first (not yet requested as of this
   writing). Note: `var/order_state.json` (real ticket data from these live runs) is
   gitignored and must never be committed.
3. Update `AGENTS.md`'s Progress section to reflect Phase 6's actual state (Steps 0–3 done,
   Step 4 fully live-proven: LIMIT submit/cancel now confirmed working end-to-end against a
   real demo account), once the commit above lands.
4. Only after that: consider Step 5 (`close_position()`, needs an actual filled position) or
   Step 6 (MARKET orders + mandatory SL/TP follow-up) — each is its own, separately-approved
   step, not something to start without being asked, per this project's established practice.
