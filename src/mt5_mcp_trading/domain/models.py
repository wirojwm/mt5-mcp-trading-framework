"""
Shared, immutable data contracts for the whole pipeline:

    market_data -> features -> strategy -> signal -> trade_intent
        -> sizing -> risk -> order_planning -> execution -> mt5_adapter -> mcp_adapter

Every model here is a frozen dataclass: a stage in the pipeline never mutates a value it
receives, it produces a new one. This keeps every stage replayable and testable in isolation,
and makes it possible to log/inspect the exact value that flowed between two stages.

Nothing in this module knows about MT5, MCP, or any adapter. It is imported by every layer,
including the pure ones (strategy/signal/trade_intent/sizing/risk/order_planning) — that is
allowed; only *adapter* packages are forbidden from those layers, not plain data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

Side = Literal["BUY", "SELL"]
Direction = Literal["LONG", "SHORT", "FLAT"]
OrderType = Literal["MARKET", "LIMIT"]
TradeMode = Literal["DEMO", "CONTEST", "REAL"]


@dataclass(frozen=True, slots=True)
class MarketBar:
    symbol: str
    timeframe: str
    time: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread: int


@dataclass(frozen=True, slots=True)
class Tick:
    symbol: str
    bid: float
    ask: float
    time: datetime


@dataclass(frozen=True, slots=True)
class FeatureSet:
    symbol: str
    timeframe: str
    timestamp: datetime
    ema_fast: Optional[float] = None
    ema_slow: Optional[float] = None
    atr: Optional[float] = None
    macd: Optional[float] = None


@dataclass(frozen=True, slots=True)
class GridLevels:
    """
    Output of the grid strategy (strategy/grid.py): a center price plus one candidate
    BUY_LIMIT/SELL_LIMIT level on each side, not a directional Signal. Grid seeds both sides
    regardless of directional belief, which doesn't fit LONG/SHORT/FLAT -- deliberately kept
    as its own type rather than forced into Signal. How GridLevels becomes TradeIntents is a
    later-phase decision (see strategy/grid.py's module docstring).
    """

    symbol: str
    timestamp: datetime
    center: float
    atr: float
    step_price: float
    tp_price: float
    buy_price: float
    sell_price: float


GuardActionKind = Literal["NONE", "PARTIAL_CLOSE", "FLATTEN"]


@dataclass(frozen=True, slots=True)
class GuardState:
    """Carried across successive evaluate_guard() calls by whatever stateful caller invokes
    it repeatedly (out of scope for this migration, same as GridLevels->TradeIntent and
    runner's insufficient-bars case) -- a pure function has no memory of its own."""

    deadline_bars_remaining: int = 0
    last_evaluated_bar_time: Optional[datetime] = None


@dataclass(frozen=True, slots=True)
class GuardAction:
    """What the guard wants done. ratio is only meaningful for PARTIAL_CLOSE. Executing this
    (closing positions, freezing/unfreezing the grid worker, restoring its levels cap) is an
    adapter/orchestration concern, deliberately not this type's job."""

    kind: GuardActionKind
    ratio: Optional[float] = None


@dataclass(frozen=True, slots=True)
class Signal:
    symbol: str
    strategy_name: str
    direction: Direction
    timestamp: datetime
    rationale: str = ""


@dataclass(frozen=True, slots=True)
class TradeIntent:
    """
    Expresses *what* a strategy wants, not how much or at what price.

    signal_ref is Optional: originally required, on the assumption every strategy's output is
    a directional Signal. Found to be wrong the first time something tried to actually
    construct a TradeIntent from grid's output (trade_intent/grid.py) -- grid produces
    GridLevels, not a Signal, by design (see GridLevels' docstring: it proposes both sides
    regardless of directional belief, which doesn't fit LONG/SHORT/FLAT). Fixed here rather
    than forcing grid to fabricate a meaningless Signal just to satisfy the type.
    """

    symbol: str
    side: Side
    strategy_name: str
    desired_order_type: OrderType
    timestamp: datetime
    reference_price: Optional[float] = None
    signal_ref: Optional[Signal] = None


@dataclass(frozen=True, slots=True)
class LotDecision:
    """Output of a sizing-stage function (sizing/money.py): the proposed lot size plus a
    trace of why. Deliberately returned as its own value rather than mutated onto a config
    object (the legacy money_management.MoneyConfig._last_calc pattern) -- callers get the
    explanation directly from the call that produced it. How a LotDecision becomes a
    SizedIntent is a later-phase decision, same as GridLevels -> TradeIntent."""

    lot: float
    mode: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SizedIntent:
    intent: TradeIntent
    volume: float
    sizing_mode: str
    sizing_rationale: str = ""


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """If approved is False, no OrderPlan may be built from the intent this decision covers."""

    approved: bool
    reasons: tuple[str, ...] = ()
    adjusted_volume: Optional[float] = None
    blocking_guard: Optional[str] = None


@dataclass(frozen=True, slots=True)
class OrderPlan:
    """Fully normalized: nothing about broker constraints is left for the adapter to decide."""

    symbol: str
    order_type: OrderType
    side: Side
    volume: float
    price: Optional[float]
    sl: float
    tp: float
    deviation: int
    magic: int
    comment: str
    filling_mode: Optional[str] = None
    expiry: Optional[datetime] = None


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """order_plan is None for cancel/close results, which don't originate from a new OrderPlan."""

    order_plan: Optional[OrderPlan]
    success: bool
    retcode: int
    ticket: Optional[int] = None
    deal: Optional[int] = None
    executed_price: Optional[float] = None
    executed_volume: Optional[float] = None
    broker_comment: str = ""
    verified: bool = False
    verification_details: str = ""


@dataclass(frozen=True, slots=True)
class AccountState:
    """
    login and server are Optional: originally required, on the assumption every AccountReader
    implementation could identify the connected account. Found wrong wiring the first real
    (MCP-backed) AccountReader -- metatrader-mcp-server's get_account_info tool exposes
    balance/equity/margin/trade_mode but no login or server field at all, and no other tool
    fills the gap. Same pattern as TradeIntent.signal_ref: a real implementation revealed an
    unstated assumption in the domain model, fixed here rather than faked with a sentinel.

    trade_mode itself should also be treated with caution when sourced from
    metatrader-mcp-server: see mt5_adapter/mcp_account.py's module docstring for a confirmed
    bug in how that server reports account type.
    """

    balance: float
    equity: float
    margin_free: float
    trade_mode: TradeMode
    login: Optional[int] = None
    server: Optional[str] = None


@dataclass(frozen=True, slots=True)
class PositionState:
    """sl/tp: 0.0 means "no stop set" (MT5's own convention), never None -- a position always
    has a real sl/tp value on the wire, even when that value is the no-stop sentinel. Added
    for Phase 6 Step 6 (MARKET order SL/TP-attachment verification): the raw data was already
    available from get_positions_with_magic (see scripts/metatrader_mcp_extended_server.py's
    columns mapping), just not parsed until a caller needed it."""

    ticket: int
    symbol: str
    side: Side
    volume: float
    price_open: float
    profit: float
    magic: int
    sl: float = 0.0
    tp: float = 0.0


@dataclass(frozen=True, slots=True)
class OrderState:
    ticket: int
    symbol: str
    side: Side
    volume: float
    price: float
    magic: int


@dataclass(frozen=True, slots=True)
class SymbolInfo:
    symbol: str
    digits: int
    point: float
    volume_min: float
    volume_max: float
    volume_step: float
    stops_level: int
    freeze_level: int
    filling_modes: tuple[str, ...] = field(default_factory=tuple)
    spread: int = 0


@dataclass(frozen=True, slots=True)
class ConnectionState:
    connected: bool
    terminal_build: Optional[int] = None
    detail: str = ""
