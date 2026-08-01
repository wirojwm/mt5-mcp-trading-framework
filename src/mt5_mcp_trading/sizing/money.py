"""
Lot sizing, ported from the legacy project's money_management.py MoneyConfig.decide_lot().

Three modes, preserved exactly: "fixed" (flat lot), "atr_scale" (base * ref/step, clamped),
"risk_percent" (balance*risk / (stop_distance_points*point_value_per_lot), clamped, falling
back to fixed_lot when inputs are insufficient). Unknown mode also falls back to fixed_lot.
See tests/unit/test_sizing_money.py for numeric proof against the legacy formulas.

Three things were deliberately NOT carried forward, all adapter/impurity concerns rather than
sizing behavior worth preserving:

1. `_account_balance()` read MT5.account_info().balance directly when the caller didn't supply
   one -- an adapter call embedded in sizing math. account_balance is now a plain float
   parameter (default 0.0, matching what that fallback returned whenever the adapter was
   unavailable, which was the common case in every test run this project has done so far).
   Supplying the real balance is the caller's job.
2. `_normalize_volume()` called MT5_bridge.normalize_volume() to clamp the result to the
   broker's volume_min/max/step -- a *second* adapter call inside sizing math, and a genuine
   separation-of-concerns problem: this pipeline already has a dedicated later stage
   (order_planning) whose job is exactly "normalize against SymbolInfo". Sizing now decides
   an unclamped-by-broker-rules lot; order_planning (not yet migrated) is where
   SymbolInfo.volume_min/max/step get applied.
3. `self._last_calc` / `last_explain()` / `sizing_debug` print -- decide_lot() mutated the
   MoneyConfig object as a side effect to stash the last result for later inspection, and
   optionally printed it. Incompatible with "pure, deterministic function". The LotDecision
   return value carries everything _last_calc did (mode, lot, reasons); logging it, if
   wanted, is the caller's job via monitoring/, not this function's.

One bug found and fixed, not preserved: `_nz(point_value_per_lot, None)` in the risk_percent
branch crashes with an uncaught TypeError whenever point_value_per_lot is None (float(None)
fails in both the try AND the except-fallback, since alt=None too). This is reachable in
practice -- launcher_grid.GridConfig (the currently-live config type) has no
mm_point_value_per_lot attribute at all, so callers who don't explicitly pass
point_value_per_lot hit this every time risk_percent mode is selected. The line immediately
after (`if rp <= 0 or not sd or not ppv or not pp:`) makes the *intent* obvious -- missing
ppv should fall back to fixed_lot, not crash -- so this is treated as a bug, not a behavior to
preserve. Fixed by making the point_value_per_lot/price_point coercion tolerate None without
raising.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from mt5_mcp_trading.domain.models import LotDecision


def _nz(x: object, alt: float = 0.0) -> float:
    """Matches the legacy _nz() exactly for the cases it was actually used for here (x and
    alt both real numbers, or x missing/invalid with a numeric alt)."""
    try:
        return float(x)  # type: ignore[arg-type]
    except Exception:
        return float(alt)


def _nz_optional(x: Optional[float]) -> Optional[float]:
    """Like _nz(), but for the two call sites (point_value_per_lot, price_point) where the
    legacy code's fallback value was itself None -- this is the fix for the crash described
    in the module docstring: coerce to float if possible, else None, never raise."""
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


@dataclass(frozen=True, slots=True)
class MoneyConfig:
    lot_size_mode: str = "fixed"  # "fixed" | "atr_scale" | "risk_percent"

    fixed_lot: float = 0.01

    atr_scale_base: float = 0.01
    atr_scale_ref: float = 1.0
    atr_scale_min: float = 0.01
    atr_scale_max: float = 0.10

    risk_per_trade: float = 0.0
    stop_distance_points: Optional[float] = None

    max_open_lots_hint: Optional[float] = None


def decide_lot(
    config: MoneyConfig,
    *,
    atr: Optional[float] = None,
    step: Optional[float] = None,
    account_balance: float = 0.0,
    point_value_per_lot: Optional[float] = None,
    price_point: Optional[float] = None,
    open_lots_now: Optional[float] = None,
    pending_lots_now: Optional[float] = None,
) -> LotDecision:
    reasons: list[str] = []
    mode = (config.lot_size_mode or "fixed").lower().strip()

    if mode == "fixed":
        lot = float(config.fixed_lot)
        reasons.append(f"mode=fixed, fixed_lot={lot}")

    elif mode == "atr_scale":
        s = max(1e-9, _nz(step, _nz(atr)))
        base = float(config.atr_scale_base)
        ref = max(1e-9, float(config.atr_scale_ref))
        raw = base * (ref / s)
        lot = max(float(config.atr_scale_min), min(float(config.atr_scale_max), raw))
        reasons.append(
            f"mode=atr_scale, base={base}, ref={ref}, step={s:.6f} -> raw={raw:.6f}, "
            f"lot={lot:.5f} (clamped [{config.atr_scale_min},{config.atr_scale_max}])"
        )

    elif mode == "risk_percent":
        rp = max(0.0, float(config.risk_per_trade))
        sd = config.stop_distance_points
        ppv = _nz_optional(point_value_per_lot)
        pp = _nz_optional(price_point)
        if rp <= 0 or not sd or not ppv or not pp:
            lot = float(config.fixed_lot)
            reasons.append(
                "mode=risk_percent but insufficient inputs "
                f"(risk_per_trade={rp}, stop_points={sd}, point_value_per_lot={ppv}, price_point={pp}) "
                f"-> fallback fixed_lot={lot}"
            )
        else:
            risk_dollar = account_balance * rp
            per_lot_risk = float(sd) * float(ppv)
            lot_raw = risk_dollar / max(1e-9, per_lot_risk)
            lot = max(config.atr_scale_min, min(config.atr_scale_max, lot_raw))
            reasons.append(
                f"mode=risk_percent, balance={account_balance:.2f}, risk%={rp*100:.2f}%, "
                f"risk=${risk_dollar:.2f}, stop_points={sd}, $/lot/point={ppv} "
                f"-> raw={lot_raw:.5f} -> lot={lot:.5f} (clamped [{config.atr_scale_min},{config.atr_scale_max}])"
            )

    else:
        lot = float(config.fixed_lot)
        reasons.append(f"mode={mode} not recognized -> fallback fixed_lot={lot}")

    if config.max_open_lots_hint is not None:
        reasons.append(
            f"hint:max_open_lots={config.max_open_lots_hint}, "
            f"now_open={_nz(open_lots_now)}, now_pending={_nz(pending_lots_now)}"
        )

    return LotDecision(lot=lot, mode=mode, reasons=tuple(reasons))
