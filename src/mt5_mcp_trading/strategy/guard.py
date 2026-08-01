"""
EMA exit-guard: partial-close on an adverse EMA-long crossing, flatten after a deadline.

Ported from the legacy project's guard_ema.py `_loop()`. Rule (unchanged): using the last
FULLY CLOSED bar, if there's long inventory and that bar's high is below EMA(close, ema_long)
computed as of that same bar, partial-close; symmetrically for short inventory with low above
EMA-long. If nothing re-triggers, count down `deadline_bars` new bars and then flatten.

Two real bugs found by reading the legacy loop directly, fixed rather than preserved -- the
parameter names, doc comments, and two-stage partial+deadline design all make the *intent*
unambiguous, and both bugs work directly against that intent:

1. No bar-level deduplication. The legacy loop polls every ~1 second regardless of the
   underlying timeframe (M5 by default) and re-evaluates `hi[-2]`/`lo[-2]`/`emaL[-2]` against
   whatever the current last-closed bar is every single iteration -- with no check for
   "have I already processed this bar". While a trigger condition holds (which, once true for
   a given closed bar, stays true until the next bar closes -- ~5 minutes on M5), the legacy
   loop calls `_partial_close` again AND resets the deadline again on every ~1s poll, likely
   draining the position within seconds rather than doing one measured partial close followed
   by a deadline countdown. evaluate_guard() below only produces a non-NONE action once per
   newly-closed bar (tracked via GuardState.last_evaluated_bar_time), exactly the way this
   project's ported runner strategy already avoids the equivalent problem (and the way the
   legacy project's OWN ema_crossover_core_multi.py runner already got right via its
   `last_bar_time` check -- guard_ema.py just didn't have the equivalent guard).
2. `deadline` decrements once per loop iteration (~1s), not once per new bar, despite being
   named `deadline_bars` and documented in Thai as a bar count. A "6 bar" deadline on M5
   should mean ~30 minutes; the legacy code actually flattens after ~6 seconds. Fixed by only
   decrementing GuardState.deadline_bars_remaining when evaluate_guard() is called with a
   genuinely new closed bar (the same fix as #1 also resolves this).

Also NOT ported: `ema_short` is computed in the legacy loop (`emaS = _ema(cl, ema_short)`) but
never referenced by either trigger condition -- dead code. GuardConfig has no ema_short field.

Preserved faithfully even though it may look surprising: a PARTIAL_CLOSE action's `ratio`
applies to every position under the given magic, not just the side that triggered it -- this
matches the legacy `_partial_close()`, which filters positions by magic only, not by side.

Freeze-on-trigger and levels-cap restoration (`LG._set_freeze`, `LG.set_levels_cap` in the
legacy code) are grid-worker adapter/orchestration actions, not decided by this function --
same scope boundary as GridLevels->TradeIntent and runner's state-persistence gap. A caller
wiring this up for real is expected to freeze grid seeding on any non-NONE action and
unfreeze/restore on FLATTEN, matching the legacy intent, but that wiring is a later phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from mt5_mcp_trading.domain.models import GuardAction, GuardState, MarketBar, PositionState
from mt5_mcp_trading.features.ema import ema


@dataclass(frozen=True, slots=True)
class GuardConfig:
    ema_long: int = 26
    partial_ratio_long: float = 0.5
    partial_ratio_short: float = 0.5
    deadline_bars: int = 6


_NONE_ACTION = GuardAction(kind="NONE")


def evaluate_guard(
    bars: Sequence[MarketBar],
    positions: Sequence[PositionState],
    magic: int,
    state: GuardState,
    config: GuardConfig,
) -> tuple[GuardState, GuardAction]:
    """
    bars: most recent last; bars[-1] may be the currently-forming (incomplete) bar, bars[-2]
    is the last fully closed one -- the only one this function ever looks at, matching the
    legacy code's own `[-2]` indexing.
    """
    required = config.ema_long + 5
    if len(bars) < required:
        raise ValueError(
            f"evaluate_guard requires at least {required} bars (ema_long+5), got {len(bars)}"
        )

    last_closed = bars[-2]

    if state.last_evaluated_bar_time == last_closed.time:
        # Already evaluated this closed bar -- see module docstring, bug 1. No new
        # information, so no action and no state change.
        return state, _NONE_ACTION

    closes_through_last_closed = [b.close for b in bars[:-1]]
    ema_long_value = ema(closes_through_last_closed, config.ema_long)

    has_long = any(p.magic == magic and p.side == "BUY" for p in positions)
    has_short = any(p.magic == magic and p.side == "SELL" for p in positions)

    long_trigger = has_long and last_closed.high < ema_long_value
    short_trigger = has_short and last_closed.low > ema_long_value
    # long_trigger and short_trigger can never both be true: the first requires
    # ema_long_value > high, the second requires ema_long_value < low, and high >= low always.

    deadline = state.deadline_bars_remaining
    action = _NONE_ACTION

    if long_trigger:
        action = GuardAction(kind="PARTIAL_CLOSE", ratio=config.partial_ratio_long)
        deadline = config.deadline_bars
    elif short_trigger:
        action = GuardAction(kind="PARTIAL_CLOSE", ratio=config.partial_ratio_short)
        deadline = config.deadline_bars
    elif deadline > 0:
        deadline -= 1
        if deadline == 0:
            action = GuardAction(kind="FLATTEN")

    new_state = GuardState(deadline_bars_remaining=deadline, last_evaluated_bar_time=last_closed.time)
    return new_state, action
