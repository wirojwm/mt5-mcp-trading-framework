"""
Assembles a GridLevels into the two TradeIntents grid always proposes: one BUY_LIMIT at
buy_price, one SELL_LIMIT at sell_price. Matches the legacy launcher_grid._grid_worker, which
computes both sides every cycle regardless of any directional bias -- whether either one
actually gets placed is decided later, by risk guards (duplicate-order prevention, exposure
caps), not by this function.

signal_ref is left as None on both intents -- see TradeIntent's docstring for why that field
is Optional at all (this module is the reason it had to become one).
"""

from __future__ import annotations

from mt5_mcp_trading.domain.models import GridLevels, TradeIntent


def grid_levels_to_trade_intents(levels: GridLevels, strategy_name: str = "grid") -> tuple[TradeIntent, TradeIntent]:
    buy_intent = TradeIntent(
        symbol=levels.symbol,
        side="BUY",
        strategy_name=strategy_name,
        desired_order_type="LIMIT",
        timestamp=levels.timestamp,
        reference_price=levels.buy_price,
    )
    sell_intent = TradeIntent(
        symbol=levels.symbol,
        side="SELL",
        strategy_name=strategy_name,
        desired_order_type="LIMIT",
        timestamp=levels.timestamp,
        reference_price=levels.sell_price,
    )
    return buy_intent, sell_intent
