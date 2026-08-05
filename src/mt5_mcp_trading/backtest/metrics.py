"""
Pure metrics over BacktestLedger.closed_trades -- Phase 8 Step 1's decided edge-validation
metric (docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md): per-trade expectancy in R-multiples, net
of transaction costs (already baked into each ClosedTrade.r_multiple by the engine's spread
modeling), computed separately per strategy. Callers are responsible for filtering
closed_trades by magic/comment themselves before calling these -- nothing here blends
strategies together, on purpose (see the checkpoint doc's reasoning: blending could hide one
strategy's losing edge behind the other's positive one).
"""

from __future__ import annotations

from mt5_mcp_trading.backtest.ledger import ClosedTrade

MIN_TRADES_FOR_A_CLAIM = 30  # Step 1's decided floor -- docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md


def expectancy_r(trades: list[ClosedTrade]) -> float:
    if not trades:
        raise ValueError("expectancy_r() requires at least one trade")
    return sum(t.r_multiple for t in trades) / len(trades)


def max_drawdown_r(trades: list[ClosedTrade]) -> float:
    """Peak-to-trough decline of the cumulative R curve, in R units, not $ -- see the checkpoint
    doc's Step 3 entry for why. `trades` must already be in chronological order --
    BacktestLedger.closed_trades is append-only in bar-processing order, so it already is,
    as long as the caller doesn't re-sort it before calling this."""
    if not trades:
        raise ValueError("max_drawdown_r() requires at least one trade")
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        cumulative += t.r_multiple
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    return max_dd


def has_minimum_sample(trades: list[ClosedTrade], minimum: int = MIN_TRADES_FOR_A_CLAIM) -> bool:
    return len(trades) >= minimum
