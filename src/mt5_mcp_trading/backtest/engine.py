"""
Bar-by-bar backtest engine (Phase 8 Step 3, docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md). Drives
the real, completely unmodified pipeline.run_grid_cycle()/run_runner_cycle() against replayed
historical bars, via three backtest-flavored implementations of this project's own
MarketDataSource/AccountReader/OrderExecutor Protocols -- the same seam
DryRunExecutor/MockMarketDataSource/MockAccountReader already exploit for dry-run testing. No
strategy/risk/order_planning logic is reimplemented anywhere in this module.

LOOK-AHEAD BIAS -- the single most important property of this file, and the reason
ReplayCursor.visible_bars() exists as its own small, directly-testable unit rather than being
inlined: BacktestMarketDataSource.get_bars() can only ever return bars up to and including the
cursor's current position, never anything past it. Every downstream strategy decision goes
through this one method, so if this one thing is correct, the strategy logic downstream
structurally cannot see the future -- see tests/unit/test_backtest_engine.py's dedicated
look-ahead test.

FILL/EXIT TIMING -- a deliberate, documented convention, not an accident:
- A MARKET order (runner) fills immediately, at the CURRENT bar's close +/- half the bar's own
  real historical spread -- this is not a look-ahead: the close is already-known information at
  the moment the strategy decided to act on it.
- A LIMIT order (grid) can only fill starting from the NEXT bar onward, when a later bar's
  high/low range actually crosses the limit price -- never the same bar it was placed on, since
  a bar-by-bar replay has no sub-bar granularity to know whether that bar's own remaining price
  action would have touched it before or after the order existed.
- The replay loop enforces this ordering structurally: check_fills_and_exits(bar) for bar i
  always runs BEFORE that same bar's run_grid_cycle()/run_runner_cycle() calls, so anything
  placed during bar i's own cycle calls is never checked until bar i+1.
- A position opened this bar (either a fresh MARKET fill or a LIMIT fill) is, by the same
  ordering, never checked for its own SL/TP against the bar it opened on either -- exits start
  from the next bar too.
- SAME-BAR SL/TP AMBIGUITY: if one bar's high/low range contains both a position's SL and TP,
  OHLC data alone cannot say which was hit first. This engine assumes SL hit first -- the
  conservative, worse-case assumption, consistent with this project's general risk-averse
  posture (docs/PHASE8_STRATEGY_RESEARCH_CHECKPOINT.md, Step 3 scoping notes). This will bias
  results pessimistically on the rare bar where it applies; that is the intended, chosen
  direction of the bias, not an oversight.

COST MODEL: spread only, using each bar's own real historical `spread` field (already present
in the cached data from Phase 8 Step 2) -- see the checkpoint doc's Step 3 entry for why
commission/swap were researched and excluded (get_deals exposes them only for this account's
real historical deals, not as a per-bar backtest input; this account's broker profile is
consistent with spread-only retail CFD pricing).

$ BALANCE/EQUITY: BacktestAccountReader reports a NOMINAL balance (starting_balance + sum of
notional_pnl), not a calibrated dollar figure -- see ledger.py's module docstring. Nothing in
run_grid_cycle()/run_runner_cycle() actually reads get_account_state() at all (confirmed by
reading both), so this is informational only, never load-bearing for the replay itself.
"""

from __future__ import annotations

from typing import Optional, Sequence

from mt5_mcp_trading.backtest.ledger import BacktestLedger
from mt5_mcp_trading.domain.models import (
    AccountState,
    ConnectionState,
    ExecutionResult,
    MarketBar,
    OrderPlan,
    OrderState,
    PositionState,
    SymbolInfo,
    Tick,
)
from mt5_mcp_trading.pipeline.grid_cycle import run_grid_cycle
from mt5_mcp_trading.pipeline.runner_cycle import run_runner_cycle
from mt5_mcp_trading.risk.portfolio_guards import ExposureCaps
from mt5_mcp_trading.sizing.money import MoneyConfig
from mt5_mcp_trading.strategy.grid import GridStrategyConfig
from mt5_mcp_trading.strategy.runner import RunnerStrategyConfig


class ReplayCursor:
    """Owns the one piece of mutable state everything else in this module reads from: which bar
    is "now". `bars` must already be sorted oldest-first (backtest/market_data_cache.py's
    load_bars() guarantees this)."""

    def __init__(self, bars: Sequence[MarketBar]) -> None:
        if not bars:
            raise ValueError("ReplayCursor requires at least one bar")
        self._bars = list(bars)
        self.index = 0

    @property
    def current_bar(self) -> MarketBar:
        return self._bars[self.index]

    def visible_bars(self, count: int) -> list[MarketBar]:
        """The look-ahead-bias control point -- see this module's docstring. Never returns
        anything past `self.index`, regardless of `count`."""
        start = max(0, self.index - count + 1)
        return self._bars[start : self.index + 1]

    def __len__(self) -> int:
        return len(self._bars)


class BacktestMarketDataSource:
    def __init__(self, cursor: ReplayCursor, symbol_info: SymbolInfo) -> None:
        self._cursor = cursor
        self._symbol_info = symbol_info

    async def get_bars(self, symbol: str, timeframe: str, count: int) -> list[MarketBar]:
        return self._cursor.visible_bars(count)

    async def get_tick(self, symbol: str) -> Tick:
        bar = self._cursor.current_bar
        half_spread = (bar.spread * self._symbol_info.point) / 2
        return Tick(symbol=symbol, bid=bar.close - half_spread, ask=bar.close + half_spread, time=bar.time)

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        return self._symbol_info


class BacktestAccountReader:
    def __init__(self, ledger: BacktestLedger, starting_balance: float = 10_000.0) -> None:
        self._ledger = ledger
        self._starting_balance = starting_balance

    async def get_account_state(self) -> AccountState:
        realized = sum(t.notional_pnl for t in self._ledger.closed_trades)
        balance = self._starting_balance + realized
        return AccountState(balance=balance, equity=balance, margin_free=balance, trade_mode="DEMO")

    async def get_positions(
        self, symbol: Optional[str] = None, magic: Optional[int] = None
    ) -> list[PositionState]:
        return [
            PositionState(ticket=p.ticket, symbol=p.symbol, side=p.side, volume=p.volume,
                          price_open=p.price_open, profit=0.0, magic=p.magic, sl=p.sl, tp=p.tp)
            for p in self._ledger.positions_for(symbol, magic)
        ]

    async def get_orders(
        self, symbol: Optional[str] = None, magic: Optional[int] = None
    ) -> list[OrderState]:
        return [
            OrderState(ticket=o.ticket, symbol=o.symbol, side=o.side, volume=o.volume,
                      price=o.price, magic=o.magic)
            for o in self._ledger.orders_for(symbol, magic)
        ]

    async def get_connection_state(self) -> ConnectionState:
        return ConnectionState(connected=True, detail="backtest replay -- not a real connection")


class BacktestOrderExecutor:
    def __init__(self, cursor: ReplayCursor, ledger: BacktestLedger, symbol_info: SymbolInfo) -> None:
        self._cursor = cursor
        self._ledger = ledger
        self._symbol_info = symbol_info

    def _half_spread(self) -> float:
        return (self._cursor.current_bar.spread * self._symbol_info.point) / 2

    async def submit(self, order_plan: OrderPlan) -> ExecutionResult:
        bar = self._cursor.current_bar
        if order_plan.order_type == "MARKET":
            half = self._half_spread()
            fill_price = bar.close + half if order_plan.side == "BUY" else bar.close - half
            ticket = self._ledger.add_open_position(
                symbol=order_plan.symbol, side=order_plan.side, volume=order_plan.volume,
                price_open=fill_price, sl=order_plan.sl, tp=order_plan.tp,
                magic=order_plan.magic, comment=order_plan.comment, opened_at=bar.time,
            )
            return ExecutionResult(order_plan=order_plan, success=True, retcode=10009,
                                    ticket=ticket, deal=ticket, executed_price=fill_price,
                                    executed_volume=order_plan.volume, verified=True)

        if order_plan.price is None:
            raise ValueError(
                "LIMIT order_plan.price is None -- build_order_plan() should never produce this"
            )
        ticket = self._ledger.add_pending_order(
            symbol=order_plan.symbol, side=order_plan.side, volume=order_plan.volume,
            price=order_plan.price, sl=order_plan.sl, tp=order_plan.tp,
            magic=order_plan.magic, comment=order_plan.comment, placed_at=bar.time,
        )
        return ExecutionResult(order_plan=order_plan, success=True, retcode=10009, ticket=ticket,
                                verified=True)

    async def cancel(self, ticket: int) -> ExecutionResult:
        existed = self._ledger.pending_orders.pop(ticket, None) is not None
        return ExecutionResult(order_plan=None, success=existed, retcode=10009 if existed else -1,
                                ticket=ticket, verified=existed)

    async def close_position(self, ticket: int, volume: Optional[float] = None) -> ExecutionResult:
        if ticket not in self._ledger.open_positions:
            return ExecutionResult(order_plan=None, success=False, retcode=-1, ticket=ticket,
                                    verified=False)
        bar = self._cursor.current_bar
        half = self._half_spread()
        position = self._ledger.open_positions[ticket]
        close_price = bar.close - half if position.side == "BUY" else bar.close + half
        trade = self._ledger.close_position(ticket, price_close=close_price, reason="MANUAL",
                                             closed_at=bar.time)
        return ExecutionResult(order_plan=None, success=True, retcode=10009, ticket=ticket,
                                deal=ticket, executed_price=close_price,
                                executed_volume=trade.volume, verified=True)

    def check_fills_and_exits(self, bar: MarketBar) -> None:
        """Called by run_backtest() once per bar, BEFORE that bar's cycle functions run -- see
        this module's docstring for why that ordering is what enforces "no same-bar fill".

        `just_filled` matters for a second, separate reason beyond that: without it, a pending
        order that fills during THIS call would immediately fall into the exit-check loop below
        and could close again in the same call if its own SL/TP happens to also sit inside this
        bar's range (a real bug found by testing, not a hypothetical -- see
        tests/unit/test_backtest_engine.py's same-bar test). A freshly-filled position must wait
        until the NEXT call to be eligible for an exit, exactly like a freshly-placed pending
        order must wait until the next call to be eligible for a fill."""
        just_filled: set[int] = set()
        for ticket, order in list(self._ledger.pending_orders.items()):
            if order.symbol != bar.symbol:
                continue
            if bar.low <= order.price <= bar.high:
                self._ledger.fill_pending_order(ticket, closed_at=bar.time)
                just_filled.add(ticket)

        for ticket, position in list(self._ledger.open_positions.items()):
            if ticket in just_filled:
                continue
            if position.symbol != bar.symbol:
                continue
            if position.side == "BUY":
                sl_hit = bar.low <= position.sl
                tp_hit = bar.high >= position.tp
            else:
                sl_hit = bar.high >= position.sl
                tp_hit = bar.low <= position.tp

            if sl_hit:  # SL-first tie-break on a same-bar double-hit -- see module docstring
                self._ledger.close_position(ticket, price_close=position.sl, reason="SL",
                                             closed_at=bar.time)
            elif tp_hit:
                self._ledger.close_position(ticket, price_close=position.tp, reason="TP",
                                             closed_at=bar.time)


async def run_backtest(
    bars: Sequence[MarketBar], symbol: str, timeframe: str, bars_count: int,
    symbol_info: SymbolInfo, grid_config: GridStrategyConfig, runner_config: RunnerStrategyConfig,
    money_config: MoneyConfig, caps: ExposureCaps, grid_magic: int, runner_magic: int,
    cycle_interval_bars: int = 1,
) -> BacktestLedger:
    """Mirrors scripts/run_demo_execution_pipeline_loop.py's own per-cycle shape (both
    strategies, sequentially) -- not a new orchestration pattern, the same established one.
    Returns the ledger holding every closed trade (tagged by magic/comment so per-strategy
    metrics can be computed, see metrics.py) plus whatever's still open at the end (excluded
    from closed-trade stats on purpose -- see ledger.py's module docstring).

    `cycle_interval_bars`: how many bars apart new-order evaluation happens -- NOT how often
    fills/exits are checked, which always happens every single bar (real SL/TP monitoring is
    continuous, only NEW cycle evaluation is throttled in the real system). Defaults to 1 (every
    bar) for engine-mechanics testing, but this does NOT match how this project has ever
    actually run live: the bounded autonomous loop (scripts/run_demo_execution_pipeline_loop.py)
    evaluates a cycle once every CYCLE_INTERVAL_SECONDS=300s (5 minutes) regardless of the M1
    bar timeframe strategies compute signals from -- confirmed by reading that script directly,
    not assumed. A real BTCUSD M1 backtest first run at cycle_interval_bars=1 produced 27,234
    runner trades over 50,000 bars, an obviously implausible rate (more than one trade every 2
    bars) -- traced to this exact cadence mismatch (evaluating 5x more often than any real
    deployment ever has), not a genuine edge finding. Callers reproducing real deployment
    behavior should pass cycle_interval_bars=5 for M1 data (300s / 60s per bar)."""
    if len(bars) < bars_count:
        raise ValueError(f"run_backtest needs at least {bars_count} bars to start, got {len(bars)}")
    if cycle_interval_bars < 1:
        raise ValueError(f"cycle_interval_bars must be >= 1, got {cycle_interval_bars}")

    cursor = ReplayCursor(bars)
    ledger = BacktestLedger()
    market_data = BacktestMarketDataSource(cursor, symbol_info)
    account = BacktestAccountReader(ledger)
    executor = BacktestOrderExecutor(cursor, ledger, symbol_info)

    start = bars_count - 1
    for i in range(start, len(bars)):
        cursor.index = i
        executor.check_fills_and_exits(cursor.current_bar)  # every bar -- continuous, unthrottled
        if (i - start) % cycle_interval_bars == 0:  # new-order evaluation -- throttled
            await run_grid_cycle(market_data, account, executor, symbol, timeframe, bars_count,
                                  grid_config, money_config, caps, grid_magic)
            await run_runner_cycle(market_data, account, executor, symbol, timeframe, bars_count,
                                    runner_config, money_config, caps, runner_magic)

    return ledger
