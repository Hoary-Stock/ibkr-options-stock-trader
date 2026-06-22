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
    PAPER = "Paper"            # 本地模拟: PaperEngine 本地撮合, 不向 TWS 发单
    IBKR_PAPER = "IBKRPaper"   # IBKR 模拟盘: 真实 API 发单到 7497 端口的模拟账户
    LIVE = "Live"             # 实盘: 真实 API 发单到 7496 端口

    @property
    def label(self) -> str:
        """中文显示名 (状态栏 / 连接状态)。"""
        return {
            "Paper": "本地模拟",
            "IBKRPaper": "IBKR模拟盘",
            "Live": "实盘",
        }[self.value]

    @property
    def uses_ibkr_engine(self) -> bool:
        """是否走真实 IBKR 引擎 (真实下单)。仅本地模拟用 PaperEngine。"""
        return self is not TradingMode.PAPER

    @property
    def is_live_port(self) -> bool:
        """是否连实盘端口 (7496)。两种模拟都连 7497。"""
        return self is TradingMode.LIVE


class InstrumentType(Enum):
    OPTION = "OPT"
    STOCK = "STK"
    ETF = "ETF"
    FUTURE = "FUT"


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
        """e.g. 'SPY 260516 C 585'; stock/futures pseudo-contracts show the symbol."""
        if self.right == "STK":
            return f"{self.symbol} (正股)"
        if self.right == "FUT":
            # 含合约月份, 便于区分近月与季月: 'ES 期货 2606'
            mon = self.expiry[2:6] if len(self.expiry) >= 6 else self.expiry
            return f"{self.symbol} (期货 {mon})" if mon else f"{self.symbol} (期货)"
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
        price ladder and underlying subscriptions see the same data.
        Futures use '__fut__SYM_YYYYMM' (合约月份) 以区分近月/季月,
        且与 reqPositions 回来的合约月份对齐 (取前 6 位 = 年月)。"""
        if self.right == "STK":
            return f"__stock__{self.symbol}"
        if self.right == "FUT":
            mon = self.expiry[:6] if self.expiry else ""
            return f"__fut__{self.symbol}_{mon}"
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
        if self.sec_type == "FUT":
            # 含合约月份, 区分近月/季月 (与面板筛选/双击一致)
            mon = self.expiry[:6] if self.expiry else ""
            return f"{self.symbol}_FUT_{mon}"
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
        type_map = {"OPT": "期权", "STK": "正股", "ETF": "ETF", "FUT": "期货"}
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
class ConditionalOrder:
    """本地/原生「条件单」(止盈 TP / 止损 SL)。

    含义:价格到达 trigger_price 后,挂出一张 limit_price 的限价单。
    - native=False(本地): 到价前**不发到 IBKR**,由本程序监控现价、到价才提交限价单
      (规避「同合约不能双向挂单」201, 但只在程序运行时有效)。
    - native=True: 立即用 IBKR 原生 STP LMT 挂到服务器(关程序也有效, 受 201 限制)。

    TP(止盈): 现价 **>=** trigger 时触发(向上); SL(止损): 现价 **<=** trigger 时触发(向下)。
    当前面向「平多」: action 固定 SELL(可后续扩展买入侧)。
    """
    cond_id: int
    option: "OptionInfo"
    kind: str                 # "TP"(止盈) / "SL"(止损)
    action: str = "SELL"      # 平多 → 卖出
    trigger_price: float = 0.0
    limit_price: float = 0.0   # 触发后挂的限价 (默认 = trigger_price)
    quantity: int = 1
    native: bool = False       # True=IBKR 原生 STP LMT; False=本地监控
    outside_rth: bool = False
    armed_time: datetime = field(default_factory=datetime.now)

    @property
    def key(self) -> str:
        return self.option.to_ibkr_key()

    @property
    def kind_label(self) -> str:
        return "止盈" if self.kind == "TP" else "止损"

    def is_triggered(self, price: float) -> bool:
        """现价是否已达触发条件 (TP 向上 / SL 向下)。"""
        if price <= 0:
            return False
        return price >= self.trigger_price if self.kind == "TP" else price <= self.trigger_price

    def to_dict(self) -> dict:
        o = self.option
        return {
            "cond_id": self.cond_id,
            "symbol": o.symbol, "expiry": o.expiry, "strike": o.strike,
            "right": o.right, "con_id": o.con_id,
            "kind": self.kind, "action": self.action,
            "trigger_price": self.trigger_price, "limit_price": self.limit_price,
            "quantity": self.quantity, "native": self.native,
            "outside_rth": self.outside_rth,
        }

    @staticmethod
    def from_dict(d: dict) -> "ConditionalOrder":
        opt = OptionInfo(
            symbol=d["symbol"], expiry=d.get("expiry", ""),
            strike=d.get("strike", 0.0), right=d.get("right", "C"),
            con_id=d.get("con_id", 0),
        )
        return ConditionalOrder(
            cond_id=d["cond_id"], option=opt, kind=d["kind"],
            action=d.get("action", "SELL"),
            trigger_price=d.get("trigger_price", 0.0),
            limit_price=d.get("limit_price", 0.0),
            quantity=d.get("quantity", 1), native=d.get("native", False),
            outside_rth=d.get("outside_rth", False),
        )


@dataclass
class DepthRow:
    """Single row of market depth data."""
    price: float = 0.0
    bid_size: int = 0
    ask_size: int = 0
    my_buy_qty: int = 0
    my_sell_qty: int = 0
