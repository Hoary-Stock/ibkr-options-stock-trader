"""Data models for IBKR Trader."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class OrderAction(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(Enum):
    PENDING = "PendingSubmit"
    SUBMITTED = "Submitted"
    FILLED = "Filled"
    CANCELLED = "Cancelled"
    ERROR = "Error"


class TradingMode(Enum):
    PAPER = "Paper"
    LIVE = "Live"


class InstrumentType(Enum):
    OPTION = "OPT"
    STOCK = "STK"
    ETF = "ETF"


class OrderType(Enum):
    LIMIT = "LMT"
    MARKET = "MKT"


@dataclass
class OptionInfo:
    """Represents a single option contract."""
    symbol: str           # Underlying, e.g. "SPY"
    expiry: str           # "20260516"
    strike: float
    right: str            # "C" or "P"
    con_id: int = 0
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    volume: int = 0
    open_interest: int = 0

    @property
    def display_name(self) -> str:
        """e.g. 'SPY 260516 C 585'; stock pseudo-contracts show the symbol."""
        if self.right == "STK":
            return f"{self.symbol} (正股)"
        strike_str = f"{int(self.strike)}" if self.strike == int(self.strike) else f"{self.strike:g}"
        return f"{self.symbol} {self.expiry[2:]} {self.right} {strike_str}"

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.last

    def to_ibkr_key(self) -> str:
        """Unique key for tick subscription tracking.
        Stock pseudo-contracts share the '__stock__' key space so the
        price ladder and underlying subscriptions see the same data."""
        if self.right == "STK":
            return f"__stock__{self.symbol}"
        return f"{self.symbol}_{self.expiry}_{self.right}_{self.strike}"


@dataclass
class OrderInfo:
    """Represents an order (pending or filled)."""
    order_id: int
    option: OptionInfo
    action: OrderAction
    quantity: int
    limit_price: float
    order_type: OrderType = OrderType.LIMIT
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: int = 0
    filled_price: float = 0.0
    commission: float = 0.0
    create_time: datetime = field(default_factory=datetime.now)
    fill_time: datetime | None = None
    error_msg: str = ""   # IBKR rejection reason (set when status == ERROR)

    @property
    def display_action(self) -> str:
        return "买入" if self.action == OrderAction.BUY else "卖出"

    @property
    def display_status(self) -> str:
        status_map = {
            OrderStatus.PENDING: "挂单中",
            OrderStatus.SUBMITTED: "已提交",
            OrderStatus.FILLED: "已成交",
            OrderStatus.CANCELLED: "已撤单",
            OrderStatus.ERROR: "已拒绝",
        }
        return status_map.get(self.status, self.status.value)


@dataclass
class PositionInfo:
    """Represents a position in a specific option."""
    option: OptionInfo
    quantity: int          # Positive = long, negative = short
    avg_price: float       # Average entry price
    current_price: float = 0.0
    total_commission: float = 0.0  # Accumulated commissions (entry + exit)

    @property
    def unrealized_pnl(self) -> float:
        return (self.current_price - self.avg_price) * self.quantity * 100

    @property
    def net_pnl(self) -> float:
        """Unrealized P&L minus accumulated commissions."""
        return self.unrealized_pnl - self.total_commission

    @property
    def pnl_pct(self) -> float:
        if self.avg_price <= 0:
            return 0.0
        return (self.current_price - self.avg_price) / self.avg_price * 100

    @property
    def net_pnl_pct(self) -> float:
        """Net P&L percentage including commissions."""
        cost = self.cost_basis
        if cost <= 0:
            return 0.0
        return self.net_pnl / cost * 100

    @property
    def market_value(self) -> float:
        return self.current_price * self.quantity * 100

    @property
    def cost_basis(self) -> float:
        return self.avg_price * self.quantity * 100

    @property
    def position_key(self) -> str:
        return self.option.to_ibkr_key()


@dataclass
class AccountSummary:
    """IBKR account summary data."""
    net_liquidation: float = 0.0
    total_cash: float = 0.0
    buying_power: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0


@dataclass
class PortfolioPosition:
    """Generalized position for any instrument (options, stocks, ETFs)."""
    con_id: int = 0
    symbol: str = ""
    sec_type: str = ""       # "OPT", "STK", "ETF"
    expiry: str = ""
    strike: float = 0.0
    right: str = ""          # "C", "P", or ""
    quantity: float = 0.0
    avg_price: float = 0.0
    market_price: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    daily_pnl: float = 0.0     # Today's PnL (from reqPnLSingle)
    has_pnl_data: bool = False  # True once reqPnLSingle data arrived
    currency: str = "USD"
    multiplier: float = 1.0

    @property
    def display_name(self) -> str:
        if self.sec_type == "OPT":
            strike_str = f"{int(self.strike)}" if self.strike == int(self.strike) else f"{self.strike:g}"
            exp_short = self.expiry[2:] if len(self.expiry) >= 8 else self.expiry
            return f"{self.symbol} {exp_short} {self.right} {strike_str}"
        return self.symbol

    @property
    def position_key(self) -> str:
        if self.sec_type == "OPT":
            return f"{self.symbol}_{self.expiry}_{self.right}_{self.strike}"
        return f"{self.symbol}_{self.sec_type}"

    @property
    def pnl_pct(self) -> float:
        cost = self.avg_price * abs(self.quantity) * self.multiplier
        if cost <= 0:
            return 0.0
        return self.unrealized_pnl / cost * 100

    @property
    def instrument_type(self) -> str:
        """Return display type string."""
        type_map = {"OPT": "期权", "STK": "正股", "ETF": "ETF"}
        return type_map.get(self.sec_type, self.sec_type)


@dataclass
class ComboLegInfo:
    """Represents a single leg in a combo/spread order."""
    con_id: int
    symbol: str
    expiry: str
    strike: float
    right: str        # "C" or "P"
    action: str       # "BUY" or "SELL"
    ratio: int = 1
    exchange: str = "SMART"
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0


@dataclass
class DepthRow:
    """Single row of market depth data."""
    price: float = 0.0
    bid_size: int = 0
    ask_size: int = 0
    my_buy_qty: int = 0
    my_sell_qty: int = 0
