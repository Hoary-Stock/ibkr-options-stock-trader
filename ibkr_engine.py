"""IBKR API Engine — EWrapper/EClient + order management.

Reuses connection patterns from tradebot/ibkr_paper_trader.py,
adds order placement/cancellation and Qt signal bridge.
"""

import json
import os
import time
import threading
from datetime import datetime

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order

from PyQt5.QtCore import QObject, pyqtSignal, QTimer

from config import (
    IBKR_HOST, IBKR_PAPER_PORT, IBKR_LIVE_PORT,
    IBKR_CLIENT_ID, MARKET_DATA_TYPE, IGNORED_ERROR_CODES,
    DATA_CONNECTION_ERROR_CODES,
    COMMISSION_PER_CONTRACT, COMMISSION_MIN, DEPTH_ROWS,
    INDEX_SYMBOLS,
)
from models import (
    OptionInfo, OrderInfo, PositionInfo, PortfolioPosition,
    OrderAction, OrderStatus, OrderType, TradingMode,
)


# Known PM-settled weekly option trading classes for index symbols, used as
# a fallback when the option chain hasn't been loaded yet (e.g. a contract
# typed directly into the price ladder). These are the actively-quoted
# intraday/0DTE classes.
_INDEX_WEEKLY_CLASS = {
    "SPX": "SPXW",
    "XSP": "XSP",
    "RUT": "RUTW",
    "NDX": "NDXP",
}


# ── Qt Signal Bridge ─────────────────────────────────────────────────

class IBKRSignalBridge(QObject):
    """Thread-safe bridge: IBKR reader thread -> Qt GUI thread."""

    tick_updated = pyqtSignal(str, float, float, float)  # key, bid, ask, last
    chain_ready = pyqtSignal(list, list)                 # expirations, strikes
    order_status_changed = pyqtSignal(int, str, float, float, float)  # orderId, status, filled, remaining, avgPrice
    execution_received = pyqtSignal(int, str, float, float)  # orderId, action, qty, price
    position_changed = pyqtSignal()
    connected = pyqtSignal()
    disconnected = pyqtSignal()
    error_received = pyqtSignal(int, int, str)  # reqId, code, msg
    contract_detail_received = pyqtSignal(int, object)  # reqId, contractDetails

    # Symbol search results: list of (symbol, secType, description) tuples
    symbol_search_results = pyqtSignal(list)

    # New signals for account, portfolio, depth, pnl
    account_summary_updated = pyqtSignal(str, str, str, str)  # tag, value, currency, account
    account_summary_end = pyqtSignal()
    portfolio_position_received = pyqtSignal(object)  # PortfolioPosition
    portfolio_positions_end = pyqtSignal()
    pnl_updated = pyqtSignal(float, float, float)  # dailyPnL, unrealizedPnL, realizedPnL
    depth_updated = pyqtSignal(int, int, int, int, float, int)  # reqId, position, operation, side, price, size

    # Historical data signals
    historical_bars_ready = pyqtSignal(int, list)   # reqId, list[dict]
    historical_bar_update = pyqtSignal(int, dict)   # reqId, bar dict (streaming)

    # Open order restore (from TWS on reconnect)
    # orderId, OptionInfo, action_str, qty, price, order_type_str, status_str
    open_order_received = pyqtSignal(int, object, str, int, float, str, str)

    # Order rejected/cancelled by IBKR (not user-requested): orderId, code, msg
    order_rejected = pyqtSignal(int, int, str)

    # Per-position PnL (reqPnLSingle): conId, pos, dailyPnL, unrealizedPnL, value
    pnl_single_updated = pyqtSignal(int, float, float, float, float)


# ── IBKR API App ─────────────────────────────────────────────────────

class IBKRApp(EWrapper, EClient):
    """EWrapper + EClient with callbacks for option chain, ticks, orders."""

    def __init__(self, bridge: IBKRSignalBridge):
        EClient.__init__(self, self)
        self.bridge = bridge

        self.connected_event = threading.Event()
        self.client_id_in_use = False  # set on error 326
        self._req_id_lock = threading.Lock()
        self._req_id = 1000
        self._next_order_id = 0
        self._order_id_lock = threading.Lock()

        # Contract details
        self._contract_data: dict[int, dict] = {}

        # Option parameters (reqSecDefOptParams)
        self._opt_data: dict[int, dict] = {}

        # Tick subscriptions: reqId -> option key
        self._tick_req_to_key: dict[int, str] = {}
        self._tick_data: dict[str, dict] = {}  # key -> {bid, ask, last}

        # Active subscriptions for cleanup
        self._active_mkt_data_reqs: set[int] = set()

        # Market depth tracking
        self._depth_req_id: int | None = None
        self._depth_not_supported: bool = False
        self._depth_is_smart: bool = False

        # Account summary tracking
        self._account_summary_req_id: int | None = None

        # PnL tracking
        self._pnl_req_id: int | None = None

        # Per-position PnL subscriptions: reqId -> conId
        self._pnl_single_reqs: dict[int, int] = {}

        # Account name (discovered from accountSummary)
        self._account_name: str = ""

        # One-time market data warning flag
        self._mkt_data_warned: bool = False

        # Heartbeat: timestamp of last tick received (for timeout detection)
        self._last_tick_time: float = 0.0

        # Historical data tracking
        self._hist_data: dict[int, dict] = {}

    def next_req_id(self) -> int:
        with self._req_id_lock:
            self._req_id += 1
            return self._req_id

    def next_order_id(self) -> int:
        with self._order_id_lock:
            oid = self._next_order_id
            self._next_order_id += 1
            return oid

    # ── Connection ────────────────────────────────────────────────────

    def nextValidId(self, orderId: int):
        self._next_order_id = orderId
        self.connected_event.set()
        self.bridge.connected.emit()

    def connectionClosed(self):
        self.bridge.disconnected.emit()

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        # 326: client id already in use — wake the waiting connect() so the
        # engine can retry with a fallback id instead of timing out
        if errorCode == 326:
            print(f"[CONNECT] clientId in use: {errorString}", flush=True)
            self.client_id_in_use = True
            self.connected_event.set()
            return

        # Log order-related errors (reqId in order ID range) for debugging
        if reqId >= self._next_order_id - 100 and reqId < self._next_order_id + 100:
            print(f"[ORDER ERROR] reqId={reqId} code={errorCode} "
                  f"msg={errorString}", flush=True)
            if advancedOrderRejectJson:
                print(f"[ORDER REJECT] {advancedOrderRejectJson}", flush=True)

        # Market data subscription info — show one-time warning then suppress
        if errorCode == 10167:
            if not self._mkt_data_warned:
                self._mkt_data_warned = True
                print(f"[INFO] {errorString}", flush=True)
                self.bridge.error_received.emit(
                    reqId, errorCode, "行情为延迟数据 (15分钟延迟)"
                )
            return

        if errorCode in IGNORED_ERROR_CODES:
            return

        # Data-connection codes: log + surface to GUI (not silenced)
        if errorCode in DATA_CONNECTION_ERROR_CODES:
            print(f"[DATA CONN] code={errorCode} msg={errorString}", flush=True)
            self.bridge.error_received.emit(reqId, errorCode, errorString)
            return

        # Any error on depth request — silently give up (price ladder
        # falls back to bid/ask from tick data).  Covers 10092 ("deep
        # market data not supported") AND 200 ("no security definition").
        if self._depth_req_id is not None and reqId == self._depth_req_id:
            self._depth_req_id = None
            self._depth_not_supported = True
            return

        # 10189: "tick-by-tick data is not supported" (no live subscription)
        # Silently ignore — reqMktData fallback already active
        if errorCode == 10189:
            return

        # Error 200 on a tick/market-data subscription — contract not found.
        # Clean up tracking so dead reqIds don't accumulate, and suppress
        # the status-bar spam (option chain routinely hits strikes with
        # no listed contract).
        if errorCode == 200 and reqId in self._tick_req_to_key:
            self._tick_req_to_key.pop(reqId, None)
            self._active_mkt_data_reqs.discard(reqId)
            return

        # Signal errors to waiting threads (reqContractDetails, etc.)
        for store in (self._contract_data, self._opt_data, self._hist_data):
            if reqId in store:
                store[reqId]["error"] = (errorCode, errorString)
                store[reqId]["event"].set()
                return  # handled by waiting thread, don't also spam bridge

        self.bridge.error_received.emit(reqId, errorCode, errorString)

    # ── Contract Details ──────────────────────────────────────────────

    def contractDetails(self, reqId, contractDetails):
        if reqId in self._contract_data:
            self._contract_data[reqId]["details"].append(contractDetails)
        self.bridge.contract_detail_received.emit(reqId, contractDetails)

    def contractDetailsEnd(self, reqId):
        if reqId in self._contract_data:
            self._contract_data[reqId]["event"].set()

    # ── Option Parameters ─────────────────────────────────────────────

    def securityDefinitionOptionParameter(
        self, reqId, exchange, underlyingConId, tradingClass,
        multiplier, expirations, strikes,
    ):
        if reqId in self._opt_data:
            self._opt_data[reqId]["params"].append({
                "exchange": exchange,
                "tradingClass": tradingClass,
                "expirations": sorted(expirations),
                "strikes": sorted(strikes),
            })

    def securityDefinitionOptionParameterEnd(self, reqId):
        if reqId in self._opt_data:
            self._opt_data[reqId]["event"].set()

    # ── Tick Data ─────────────────────────────────────────────────────

    def tickPrice(self, reqId, tickType, price, attrib):
        if price <= 0 or price != price:
            return

        key = self._tick_req_to_key.get(reqId)
        if key is None:
            return

        self._last_tick_time = time.time()

        d = self._tick_data.setdefault(key, {"bid": 0.0, "ask": 0.0, "last": 0.0})

        if tickType in (1, 66):     # bid / delayed bid
            d["bid"] = float(price)
        elif tickType in (2, 67):   # ask / delayed ask
            d["ask"] = float(price)
        elif tickType in (4, 68):   # last / delayed last
            d["last"] = float(price)

        # Note: no tick_updated emit. The GUI widgets (price ladder, option
        # chain) poll _tick_data via engine.get_tick() on their own refresh
        # timers, so emitting a cross-thread queued signal here would post
        # hundreds of no-op events per second to an empty receiver list.

    def tickSize(self, reqId, tickType, size):
        # Emit volume tick types for tracking
        key = self._tick_req_to_key.get(reqId)
        if key is None:
            return
        d = self._tick_data.setdefault(key, {"bid": 0.0, "ask": 0.0, "last": 0.0})
        if tickType in (0, 69):     # bid size / delayed bid size
            d["bid_size"] = int(size)
        elif tickType in (3, 70):   # ask size / delayed ask size
            d["ask_size"] = int(size)
        elif tickType in (5, 71):   # last size
            d["last_size"] = int(size)
        elif tickType in (8, 74):   # volume
            d["volume"] = int(size)

    def tickString(self, reqId, tickType, value):
        pass

    def tickGeneric(self, reqId, tickType, value):
        pass

    def tickOptionComputation(
        self, reqId, tickType, tickAttrib, impliedVol, delta, optPrice,
        pvDividend, gamma, vega, theta, undPrice,
    ):
        """IBKR 期权模型计算回调 (IV + Greeks + 标的价)。
        tickType 13 = 模型值 (IB 模型 IV/greeks/undPrice, 最优来源);
        10/11/12 = bid/ask/last 计算值 (作为 IV/undPrice 的回退)。
        结果写入 _tick_data[key], 期权计算器在自己的刷新定时器里轮询。"""
        key = self._tick_req_to_key.get(reqId)
        if key is None:
            return
        self._last_tick_time = time.time()
        d = self._tick_data.setdefault(key, {"bid": 0.0, "ask": 0.0, "last": 0.0})

        def _ok(x):  # 非 None / 非 NaN / 正数 (IBKR 用 NaN 或负值表示无效)
            return x is not None and x == x and x > 0

        def _num(x):  # 非 None / 非 NaN (greeks 可为负, 不要求正)
            return x is not None and x == x

        if tickType == 13:  # 模型值 — 优先覆盖
            if _ok(impliedVol):
                d["iv"] = float(impliedVol)
            if _ok(undPrice):
                d["und_price"] = float(undPrice)
            if _num(delta):
                d["delta"] = float(delta)
            if _num(gamma):
                d["gamma"] = float(gamma)
            if _num(vega):
                d["vega"] = float(vega)
            if _num(theta):
                d["theta"] = float(theta)
        elif tickType in (10, 11, 12):  # bid/ask/last 计算值 — 仅在模型值缺失时回退
            if "iv" not in d and _ok(impliedVol):
                d["iv"] = float(impliedVol)
            if "und_price" not in d and _ok(undPrice):
                d["und_price"] = float(undPrice)

    # ── Historical Data Callbacks ─────────────────────────────────────

    def historicalData(self, reqId, bar):
        """Accumulate bars during initial historical data load."""
        if reqId in self._hist_data:
            self._hist_data[reqId]["bars"].append({
                "date": bar.date,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": int(bar.volume),
                "wap": float(bar.average) if bar.average else 0.0,
                "count": int(bar.barCount) if bar.barCount else 0,
            })

    def historicalDataEnd(self, reqId, start, end):
        """All initial bars received — set event for waiting thread."""
        if reqId in self._hist_data:
            self._hist_data[reqId]["event"].set()

    def historicalDataUpdate(self, reqId, bar):
        """Streaming bar update (keepUpToDate=True)."""
        bar_dict = {
            "date": bar.date,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": int(bar.volume),
            "wap": float(bar.average) if bar.average else 0.0,
            "count": int(bar.barCount) if bar.barCount else 0,
        }
        self.bridge.historical_bar_update.emit(reqId, bar_dict)

    # ── Order Callbacks ───────────────────────────────────────────────

    def orderStatus(self, orderId, status, filled, remaining,
                    avgFillPrice, permId, parentId, lastFillPrice,
                    clientId, whyHeld, mktCapPrice):
        print(f"[ORDER STATUS] orderId={orderId} status={status} "
              f"filled={filled} remaining={remaining} "
              f"avgFill={avgFillPrice} whyHeld={whyHeld}", flush=True)
        self.bridge.order_status_changed.emit(
            orderId, status, float(filled), float(remaining), float(avgFillPrice)
        )

    def openOrder(self, orderId, contract, order, orderState):
        # Build OptionInfo from contract for orders placed in prior sessions
        # or via TWS directly.  Only track option orders.
        if contract.secType == "OPT":
            option = OptionInfo(
                symbol=contract.symbol,
                expiry=contract.lastTradeDateOrContractMonth,
                strike=contract.strike,
                right=contract.right,
                con_id=contract.conId,
            )
            action = OrderAction.BUY if order.action == "BUY" else OrderAction.SELL
            price = order.lmtPrice if order.orderType == "LMT" else 0.0
            order_type = OrderType.LIMIT if order.orderType == "LMT" else OrderType.MARKET
            self.bridge.open_order_received.emit(
                orderId, option, action.value, int(order.totalQuantity),
                price, order_type.value, orderState.status,
            )
        elif contract.secType == "BAG":
            # Combo/spread order — track with synthetic OptionInfo
            option = OptionInfo(
                symbol=contract.symbol,
                expiry="",
                strike=0.0,
                right="COMBO",
                con_id=contract.conId,
            )
            action = OrderAction.BUY if order.action == "BUY" else OrderAction.SELL
            price = order.lmtPrice if order.orderType == "LMT" else 0.0
            order_type = OrderType.LIMIT if order.orderType == "LMT" else OrderType.MARKET
            self.bridge.open_order_received.emit(
                orderId, option, action.value, int(order.totalQuantity),
                price, order_type.value, orderState.status,
            )
        self.bridge.order_status_changed.emit(
            orderId, orderState.status, 0, 0, 0
        )

    def execDetails(self, reqId, contract, execution):
        self.bridge.execution_received.emit(
            execution.orderId,
            execution.side,
            float(execution.shares),
            float(execution.price),
        )

    def commissionReport(self, commissionReport):
        """Handle real commission data from IBKR.

        This arrives after execDetails for each fill.
        We store it so the engine can update position commissions.
        """
        exec_id = commissionReport.execId
        commission = float(commissionReport.commission)
        if commission < 1e9:  # IBKR sends 1.7976931e+308 for unknown
            self._last_commission = (exec_id, commission)
            print(f"[COMMISSION] execId={exec_id} commission=${commission:.2f} "
                  f"currency={commissionReport.currency}", flush=True)

    # ── Account Summary Callbacks ────────────────────────────────────

    def accountSummary(self, reqId, account, tag, value, currency):
        self._account_name = account
        self.bridge.account_summary_updated.emit(tag, value, currency, account)

    def accountSummaryEnd(self, reqId):
        self.bridge.account_summary_end.emit()

    # ── Position Callbacks ────────────────────────────────────────────

    def position(self, account, contract, pos, avgCost):
        pp = PortfolioPosition(
            con_id=contract.conId,
            symbol=contract.symbol,
            sec_type=contract.secType,
            expiry=getattr(contract, 'lastTradeDateOrContractMonth', ''),
            strike=getattr(contract, 'strike', 0.0),
            right=getattr(contract, 'right', ''),
            quantity=float(pos),
            avg_price=float(avgCost),
            currency=contract.currency,
            multiplier=float(contract.multiplier) if contract.multiplier else 1.0,
        )
        # For options, IBKR returns avgCost already multiplied by multiplier
        if pp.sec_type == "OPT" and pp.multiplier > 1:
            pp.avg_price = float(avgCost) / pp.multiplier
        self.bridge.portfolio_position_received.emit(pp)

    def positionEnd(self):
        self.bridge.portfolio_positions_end.emit()

    # ── PnL Callbacks ─────────────────────────────────────────────────

    def pnl(self, reqId, dailyPnL, unrealizedPnL, realizedPnL):
        # IBKR 对尚未算出的字段推 DBL_MAX (~1.8e308) → 转 NaN, GUI 保留上一次的好值
        def _clean(v):
            v = float(v)
            return float("nan") if abs(v) > 1e300 else v
        self.bridge.pnl_updated.emit(
            _clean(dailyPnL), _clean(unrealizedPnL), _clean(realizedPnL)
        )

    def pnlSingle(self, reqId, pos, dailyPnL, unrealizedPnL,
                  realizedPnL, value):
        con_id = self._pnl_single_reqs.get(reqId)
        if con_id is None:
            return

        # IBKR 对尚未算出的字段推 DBL_MAX (~1.8e308)。这些字段是「本次无效」,
        # 而非真的 0 —— 转成 NaN, 让 GUI 保留上一次的好值, 避免盈亏闪烁成 0。
        def _clean(v):
            v = float(v)
            return float("nan") if abs(v) > 1e300 else v

        self.bridge.pnl_single_updated.emit(
            con_id, float(pos), _clean(dailyPnL),
            _clean(unrealizedPnL), _clean(value),
        )

    # ── Market Depth Callbacks ────────────────────────────────────────

    def updateMktDepth(self, reqId, position, operation, side, price, size):
        self.bridge.depth_updated.emit(
            reqId, position, operation, side, float(price), int(size)
        )

    def updateMktDepthL2(self, reqId, position, marketMaker, operation,
                         side, price, size, isSmartDepth):
        # Treat L2 depth same as L1 for our purposes
        self.bridge.depth_updated.emit(
            reqId, position, operation, side, float(price), int(size)
        )

    # ── Symbol Search Callbacks ─────────────────────────────────────

    def symbolSamples(self, reqId, contractDescriptions):
        results = []
        for cd in contractDescriptions:
            c = cd.contract
            if c.currency == "USD" and c.secType in ("STK", "IND", "ETF"):
                derivs = ", ".join(cd.derivativeSecTypes) if cd.derivativeSecTypes else ""
                desc = f"{c.primaryExchange}"
                if derivs:
                    desc += f" [{derivs}]"
                results.append((c.symbol, c.secType, desc))
        self.bridge.symbol_search_results.emit(results)

    # ── Tick-by-Tick Callbacks ────────────────────────────────────────

    def tickByTickBidAsk(self, reqId, time_, bidPrice, askPrice,
                         bidSize, askSize, tickAttribBidAsk):
        key = self._tick_req_to_key.get(reqId)
        if key is None:
            return
        self._last_tick_time = time.time()
        d = self._tick_data.setdefault(key, {"bid": 0.0, "ask": 0.0, "last": 0.0})
        d["bid"] = float(bidPrice)
        d["ask"] = float(askPrice)
        d["bid_size"] = int(bidSize)
        d["ask_size"] = int(askSize)
        # GUI polls via get_tick(); no cross-thread emit needed (see tickPrice).

    def tickByTickAllLast(self, reqId, tickType, time_, price,
                          size, tickAttribLast, exchange, specialConditions):
        key = self._tick_req_to_key.get(reqId)
        if key is None:
            return
        self._last_tick_time = time.time()
        d = self._tick_data.setdefault(key, {"bid": 0.0, "ask": 0.0, "last": 0.0})
        d["last"] = float(price)
        # GUI polls via get_tick(); no cross-thread emit needed (see tickPrice).


# ── IBKR Engine (high-level interface) ───────────────────────────────

class IBKREngine:
    """High-level engine wrapping IBKRApp for the GUI."""

    CONNECT_TIMEOUT = 10

    def __init__(self):
        self.bridge = IBKRSignalBridge()
        self._app: IBKRApp | None = None
        self._thread: threading.Thread | None = None
        self._connected = False
        self._con_id_cache: dict[str, int] = {}
        # Per-expiry option trading class, learned from request_option_chain.
        # Key "SYMBOL|EXPIRY" -> tradingClass (e.g. "SPX" vs "SPXW").
        # Index options (SPX/XSP/...) are ambiguous without it: reqMktData
        # returns error 200 and the price silently never arrives.
        self._opt_trading_class: dict[str, str] = {}
        self._mode = TradingMode.PAPER

        # Order & position tracking
        self._orders: dict[int, OrderInfo] = {}
        self._positions: dict[str, PositionInfo] = {}  # key -> PositionInfo

        # Order IDs the user asked to cancel (suppress reject popup for these)
        self._user_cancel_ids: set[int] = set()

        # Per-position PnL subscriptions: conId -> reqId
        self._pnl_single_by_conid: dict[int, int] = {}

        # API client id (default from config; stock trader overrides)
        self._client_id = IBKR_CLIENT_ID

        # Heartbeat timer (checks reader thread + tick timeout)
        self._heartbeat_timer: QTimer | None = None
        self._tick_timeout_warned = False

        # Connect internal signals
        self.bridge.order_status_changed.connect(self._on_order_status)
        self.bridge.execution_received.connect(self._on_execution)
        self.bridge.open_order_received.connect(self._on_open_order)
        self.bridge.error_received.connect(self._on_order_error)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def mode(self) -> TradingMode:
        return self._mode

    @mode.setter
    def mode(self, value: TradingMode):
        self._mode = value

    @property
    def positions(self) -> dict[str, PositionInfo]:
        return self._positions

    @property
    def orders(self) -> dict[int, OrderInfo]:
        return self._orders

    # ── Connection ────────────────────────────────────────────────────

    def connect(self, mode: TradingMode = TradingMode.PAPER,
                client_id: int | None = None) -> bool:
        """Connect to TWS/Gateway. Returns True on success.
        client_id: override config IBKR_CLIENT_ID (e.g. stock trader uses 11).
        """
        self._mode = mode
        if client_id is not None:
            self._client_id = client_id
        port = IBKR_PAPER_PORT if mode == TradingMode.PAPER else IBKR_LIVE_PORT

        # Retry with fallback ids on error 326 (zombie connection / double
        # launch). +10 steps avoid colliding with the other GUI's base id.
        base_id = self._client_id
        for attempt in range(3):
            cid = base_id + attempt * 10
            self._app = IBKRApp(self.bridge)
            try:
                self._app.connect(IBKR_HOST, port, cid)
            except Exception as e:
                self.bridge.error_received.emit(-1, -1, f"Connection failed: {e}")
                return False

            self._thread = threading.Thread(
                target=self._run_wrapper, args=(self._app,),
                daemon=True, name="ibkr-reader",
            )
            self._thread.start()

            if not self._app.connected_event.wait(timeout=self.CONNECT_TIMEOUT):
                try:
                    self._app.disconnect()
                except Exception:
                    pass
                self._connected = False
                return False

            if self._app.client_id_in_use:
                print(f"[CONNECT] clientId={cid} occupied, "
                      f"retrying with {cid + 10}", flush=True)
                self.bridge.error_received.emit(
                    -1, 326, f"clientId={cid} 被占用, 尝试 {cid + 10}..."
                )
                try:
                    self._app.disconnect()
                except Exception:
                    pass
                time.sleep(0.5)
                continue

            self._client_id = cid
            break
        else:
            self.bridge.error_received.emit(
                -1, 326,
                "clientId 全部被占用 — 检查是否已开了另一个实例, "
                "或等待 TWS 释放旧连接后重试",
            )
            self._connected = False
            return False

        self._connected = True
        self._app.reqMarketDataType(MARKET_DATA_TYPE)

        # Fetch existing open orders (from previous session or TWS-placed)
        self._app.reqOpenOrders()

        # Start heartbeat monitoring
        self._start_heartbeat()

        return True

    def _run_wrapper(self, app: IBKRApp):
        """Wrapper around app.run() that catches reader thread crashes."""
        try:
            app.run()
        except Exception as e:
            print(f"[FATAL] Reader thread crashed: {e}", flush=True)
        finally:
            # Notify GUI only if this app is still the active connection
            # (discarded retry attempts exit silently)
            if app is self._app:
                self._connected = False
                try:
                    self.bridge.disconnected.emit()
                except RuntimeError:
                    pass  # Qt object may already be destroyed

    def _start_heartbeat(self):
        """Start a 10-second timer that checks reader thread + tick freshness."""
        self._stop_heartbeat()
        self._tick_timeout_warned = False
        self._heartbeat_timer = QTimer()
        self._heartbeat_timer.timeout.connect(self._on_heartbeat)
        self._heartbeat_timer.start(10_000)

    def _stop_heartbeat(self):
        """Stop the heartbeat timer."""
        if self._heartbeat_timer is not None:
            self._heartbeat_timer.stop()
            self._heartbeat_timer = None

    def _on_heartbeat(self):
        """Periodic check: reader thread alive? tick data still flowing?"""
        # 1. Check if the reader thread is still alive
        if self._thread is not None and not self._thread.is_alive():
            print("[HEARTBEAT] Reader thread is dead!", flush=True)
            self._connected = False
            self._stop_heartbeat()
            self.bridge.disconnected.emit()
            return

        # 2. Check tick data freshness (only warn if we have active subscriptions)
        if self._app and self._app._active_mkt_data_reqs:
            last_tick = self._app._last_tick_time
            if last_tick > 0:
                elapsed = time.time() - last_tick
                if elapsed > 30:
                    if not self._tick_timeout_warned:
                        self._tick_timeout_warned = True
                        print(f"[HEARTBEAT] No tick data for {elapsed:.0f}s", flush=True)
                        self.bridge.error_received.emit(
                            -1, -2, f"行情数据超时 ({elapsed:.0f}秒无更新)"
                        )
                else:
                    # Tick resumed — clear warning
                    self._tick_timeout_warned = False

    def disconnect(self):
        """Disconnect from TWS."""
        self._stop_heartbeat()
        self.cancel_account_summary()
        self.cancel_pnl()
        for con_id in list(self._pnl_single_by_conid):
            self.cancel_pnl_single(con_id)
        self.cancel_positions()
        self.unsubscribe_market_depth()

        if self._app:
            # Cancel all market data subscriptions
            for req_id in list(self._app._active_mkt_data_reqs):
                try:
                    self._app.cancelMktData(req_id)
                except Exception:
                    pass
            self._app._active_mkt_data_reqs.clear()

            try:
                self._app.disconnect()
            except Exception:
                pass
        self._connected = False
        self.bridge.disconnected.emit()

    def reconnect(self, mode: TradingMode) -> bool:
        """Disconnect, clear state, and reconnect to a different port."""
        self.disconnect()
        time.sleep(1.5)  # Socket cleanup
        self._orders.clear()
        self._positions.clear()
        return self.connect(mode)

    # ── Symbol Search ──────────────────────────────────────────────────

    def search_symbols(self, pattern: str):
        """Search for matching symbols via IBKR API (non-blocking)."""
        if not self._app or not self._connected:
            return
        req_id = self._app.next_req_id()
        self._app.reqMatchingSymbols(req_id, pattern)

    # ── Contract Resolution ───────────────────────────────────────────

    def get_con_id(self, symbol: str) -> int:
        """Get conId for a stock/index symbol (cached, blocking)."""
        if symbol in self._con_id_cache:
            return self._con_id_cache[symbol]

        contract = self._make_underlying_contract(symbol)
        req_id = self._app.next_req_id()
        self._app._contract_data[req_id] = {
            "details": [], "event": threading.Event(), "error": None,
        }
        self._app.reqContractDetails(req_id, contract)

        state = self._app._contract_data[req_id]
        if not state["event"].wait(timeout=10):
            raise RuntimeError(f"Timeout getting contract details for {symbol}")
        if state["error"]:
            code, msg = state["error"]
            raise RuntimeError(f"Contract error {symbol}: code={code} {msg}")
        if not state["details"]:
            raise RuntimeError(f"No contract details for {symbol}")

        con_id = state["details"][0].contract.conId
        self._con_id_cache[symbol] = con_id
        self._app._contract_data.pop(req_id, None)
        return con_id

    # ── Option Chain Discovery ────────────────────────────────────────

    def request_option_chain(self, symbol: str) -> tuple[list[str], list[float]]:
        """Get expirations and strikes for a symbol (blocking).
        Returns (expirations, strikes)."""
        con_id = self.get_con_id(symbol)

        req_id = self._app.next_req_id()
        self._app._opt_data[req_id] = {
            "params": [], "event": threading.Event(), "error": None,
        }
        sec_type = "IND" if symbol.upper() in INDEX_SYMBOLS else "STK"
        self._app.reqSecDefOptParams(req_id, symbol, "", sec_type, con_id)

        if not self._app._opt_data[req_id]["event"].wait(timeout=10):
            raise RuntimeError(f"Timeout getting option params for {symbol}")

        params_list = self._app._opt_data[req_id]["params"]
        self._app._opt_data.pop(req_id, None)

        if not params_list:
            raise RuntimeError(f"No option params for {symbol}")

        # Merge expirations and strikes (prefer SMART exchange)
        all_expirations: set[str] = set()
        all_strikes: set[float] = set()
        for p in params_list:
            if p["exchange"] == "SMART":
                all_expirations.update(p["expirations"])
                all_strikes.update(p["strikes"])
        if not all_expirations:
            for p in params_list:
                all_expirations.update(p["expirations"])
                all_strikes.update(p["strikes"])

        # Learn each expiry's trading class so option contracts can be
        # disambiguated later. SPX lists two classes — "SPX" (monthly,
        # AM-settled) and "SPXW" (weeklies/dailies/0DTE, PM-settled). Without
        # the exact tradingClass, reqMktData is ambiguous → error 200 → no
        # price. On the rare overlap (3rd Friday), prefer the "...W" class
        # since that's the intraday-quoted PM-settled contract.
        sym_up = symbol.upper()
        for p in params_list:
            tclass = p.get("tradingClass") or ""
            if not tclass:
                continue
            prefer_w = tclass.upper().endswith("W")
            for exp in p["expirations"]:
                map_key = f"{sym_up}|{exp}"
                if map_key not in self._opt_trading_class or prefer_w:
                    self._opt_trading_class[map_key] = tclass

        expirations = sorted(all_expirations)
        strikes = sorted(all_strikes)
        return expirations, strikes

    def _trading_class_for(self, symbol: str, expiry: str) -> str:
        """Best-known option tradingClass for (symbol, expiry).
        Empty string lets SMART resolve it (fine for single-class names
        like SPY). Falls back to a known weekly class for index symbols
        when the chain hasn't been loaded (e.g. direct entry in the ladder)."""
        sym_up = symbol.upper()
        tclass = self._opt_trading_class.get(f"{sym_up}|{expiry}")
        if tclass:
            return tclass
        if sym_up in INDEX_SYMBOLS:
            return _INDEX_WEEKLY_CLASS.get(sym_up, "")
        return ""

    # ── Market Data Subscription ──────────────────────────────────────

    def subscribe_option_tick(self, option: OptionInfo) -> int:
        """Subscribe to streaming tick data for an option (or a stock
        pseudo-contract with right='STK'). Returns reqId."""
        if option.right == "STK":
            contract = self._make_underlying_contract(option.symbol)
        else:
            contract = self._make_option_contract(
                option.symbol, option.expiry, option.strike, option.right,
                self._trading_class_for(option.symbol, option.expiry),
            )
        req_id = self._app.next_req_id()
        key = option.to_ibkr_key()
        self._app._tick_req_to_key[req_id] = key
        self._app._active_mkt_data_reqs.add(req_id)
        # generic tick 106 = Option Implied Volatility; 确保期权计算器拿到 IV。
        # 模型 greeks 与标的价 (tickOptionComputation tickType 13) 随期权订阅默认下发。
        # 正股伪合约无 IV 概念, 保持空 generic tick。
        generic_ticks = "" if option.right == "STK" else "106"
        self._app.reqMktData(req_id, contract, generic_ticks, False, False, [])
        return req_id

    def subscribe_stock_tick(self, symbol: str) -> int:
        """Subscribe to streaming tick data for a stock/ETF. Returns reqId.
        Tick data lands under key '__stock__<symbol>'."""
        contract = self._make_underlying_contract(symbol)
        req_id = self._app.next_req_id()
        key = f"__stock__{symbol}"
        self._app._tick_req_to_key[req_id] = key
        self._app._active_mkt_data_reqs.add(req_id)
        self._app.reqMktData(req_id, contract, "", False, False, [])
        return req_id

    def unsubscribe_tick(self, req_id: int):
        """Cancel a tick data subscription."""
        key = self._app._tick_req_to_key.pop(req_id, None)
        self._app._active_mkt_data_reqs.discard(req_id)
        try:
            self._app.cancelMktData(req_id)
        except Exception:
            pass

    def get_tick(self, key: str) -> dict:
        """Get latest tick data for a key."""
        if self._app:
            return self._app._tick_data.get(key, {"bid": 0.0, "ask": 0.0, "last": 0.0})
        return {"bid": 0.0, "ask": 0.0, "last": 0.0}

    # ── Historical Data ─────────────────────────────────────────────

    def request_historical_data(
        self, symbol: str, bar_size: str, duration: str,
        keep_up_to_date: bool = False, timeout: float = 30,
        end_date_time: str = "",
    ) -> tuple[int, list[dict]]:
        """Request historical bars (blocking). Returns (reqId, bars).
        If keep_up_to_date is True, caller must later call cancel_historical_data(reqId).
        end_date_time: "" = now, or "yyyyMMdd HH:mm:ss" for earlier data.
        """
        if not self._app or not self._connected:
            raise RuntimeError("Not connected")

        contract = self._make_underlying_contract(symbol)
        req_id = self._app.next_req_id()
        self._app._hist_data[req_id] = {
            "bars": [], "event": threading.Event(), "error": None,
        }

        self._app.reqHistoricalData(
            reqId=req_id,
            contract=contract,
            endDateTime=end_date_time,
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=0,               # include extended hours
            formatDate=1,           # yyyyMMdd HH:mm:ss format
            keepUpToDate=keep_up_to_date,
            chartOptions=[],
        )

        state = self._app._hist_data[req_id]
        if not state["event"].wait(timeout=timeout):
            # Timeout — cancel and clean up
            try:
                self._app.cancelHistoricalData(req_id)
            except Exception:
                pass
            self._app._hist_data.pop(req_id, None)
            raise RuntimeError(f"Timeout requesting historical data for {symbol}")

        if state["error"]:
            code, msg = state["error"]
            self._app._hist_data.pop(req_id, None)
            raise RuntimeError(f"Historical data error {symbol}: code={code} {msg}")

        bars = state["bars"]
        if not keep_up_to_date:
            self._app._hist_data.pop(req_id, None)
        return req_id, bars

    def cancel_historical_data(self, req_id: int):
        """Cancel a keepUpToDate historical data subscription."""
        if self._app:
            try:
                self._app.cancelHistoricalData(req_id)
            except Exception:
                pass
            self._app._hist_data.pop(req_id, None)

    # ── Account Summary ──────────────────────────────────────────────

    def request_account_summary(self):
        """Request account summary (non-blocking)."""
        if not self._app or not self._connected:
            return
        self.cancel_account_summary()
        req_id = self._app.next_req_id()
        self._app._account_summary_req_id = req_id
        tags = "NetLiquidation,TotalCashValue,BuyingPower,UnrealizedPnL,RealizedPnL"
        self._app.reqAccountSummary(req_id, "All", tags)

    def cancel_account_summary(self):
        """Cancel account summary subscription."""
        if self._app and self._app._account_summary_req_id is not None:
            try:
                self._app.cancelAccountSummary(self._app._account_summary_req_id)
            except Exception:
                pass
            self._app._account_summary_req_id = None

    # ── Positions ─────────────────────────────────────────────────────

    def request_positions(self):
        """Request all portfolio positions (non-blocking)."""
        if not self._app or not self._connected:
            return
        self._app.reqPositions()

    def cancel_positions(self):
        """Cancel positions subscription."""
        if self._app and self._connected:
            try:
                self._app.cancelPositions()
            except Exception:
                pass

    # ── PnL ───────────────────────────────────────────────────────────

    def request_pnl(self, account: str = ""):
        """Request daily P&L updates."""
        if not self._app or not self._connected:
            return
        if not account:
            account = self._app._account_name
        if not account:
            return  # Need account name first
        self.cancel_pnl()
        req_id = self._app.next_req_id()
        self._app._pnl_req_id = req_id
        self._app.reqPnL(req_id, account, "")

    def cancel_pnl(self):
        """Cancel PnL subscription."""
        if self._app and self._app._pnl_req_id is not None:
            try:
                self._app.cancelPnL(self._app._pnl_req_id)
            except Exception:
                pass
            self._app._pnl_req_id = None

    def request_pnl_single(self, con_id: int) -> int:
        """Subscribe to per-position daily/unrealized PnL (reqPnLSingle).
        Returns reqId, or -1 if not ready (needs account name from summary).
        Does NOT consume a market data line.
        """
        if not self._app or not self._connected:
            return -1
        account = self._app._account_name
        if not account:
            return -1  # Account name not known yet — caller may retry
        if con_id in self._pnl_single_by_conid:
            return self._pnl_single_by_conid[con_id]
        req_id = self._app.next_req_id()
        self._app._pnl_single_reqs[req_id] = con_id
        self._pnl_single_by_conid[con_id] = req_id
        self._app.reqPnLSingle(req_id, account, "", con_id)
        return req_id

    def cancel_pnl_single(self, con_id: int):
        """Cancel a per-position PnL subscription."""
        req_id = self._pnl_single_by_conid.pop(con_id, None)
        if req_id is None or not self._app:
            return
        self._app._pnl_single_reqs.pop(req_id, None)
        try:
            self._app.cancelPnLSingle(req_id)
        except Exception:
            pass

    # ── Market Depth ──────────────────────────────────────────────────

    def subscribe_market_depth(self, option: OptionInfo):
        """Subscribe to market depth for an option.
        Skips silently if depth was already found to be unsupported (error 10092).
        """
        if not self._app or not self._connected:
            return
        # Skip if depth is known to be unsupported for this exchange/data type
        if self._app._depth_not_supported:
            return
        self.unsubscribe_market_depth()
        if option.right == "STK":
            contract = self._make_underlying_contract(option.symbol)
            smart_depth = True   # aggregate book across exchanges
        else:
            contract = self._make_option_contract(
                option.symbol, option.expiry, option.strike, option.right,
                self._trading_class_for(option.symbol, option.expiry),
            )
            smart_depth = False
        req_id = self._app.next_req_id()
        self._app._depth_req_id = req_id
        self._app._depth_is_smart = smart_depth
        try:
            self._app.reqMktDepth(req_id, contract, DEPTH_ROWS, smart_depth, [])
        except Exception:
            pass

    def unsubscribe_market_depth(self):
        """Cancel market depth subscription."""
        if self._app and self._app._depth_req_id is not None:
            try:
                self._app.cancelMktDepth(
                    self._app._depth_req_id, self._app._depth_is_smart
                )
            except Exception:
                pass
            self._app._depth_req_id = None

    # ── Order Management ──────────────────────────────────────────────

    def place_limit_order(self, option: OptionInfo, action: OrderAction,
                          quantity: int, price: float,
                          outside_rth: bool = False) -> int:
        """Place a limit order. Returns orderId.
        outside_rth: allow execution during GTH/Curb sessions (盘外交易).
        """
        contract = self._make_option_contract(
            option.symbol, option.expiry, option.strike, option.right,
            self._trading_class_for(option.symbol, option.expiry),
        )

        order = Order()
        order.action = action.value
        order.orderType = "LMT"
        order.totalQuantity = quantity
        order.lmtPrice = price
        order.eTradeOnly = False
        order.firmQuoteOnly = False
        order.tif = "DAY"
        order.outsideRth = outside_rth

        order_id = self._app.next_order_id()

        # Track order
        commission = max(COMMISSION_PER_CONTRACT * quantity, COMMISSION_MIN)
        order_info = OrderInfo(
            order_id=order_id,
            option=option,
            action=action,
            quantity=quantity,
            limit_price=price,
            order_type=OrderType.LIMIT,
            commission=commission,
        )
        self._orders[order_id] = order_info

        print(f"[ORDER] Placing LMT {action.value} {quantity}x "
              f"{option.display_name} @ {price:.2f} "
              f"outsideRth={outside_rth} orderId={order_id}", flush=True)
        self._app.placeOrder(order_id, contract, order)
        self.bridge.order_status_changed.emit(
            order_id, OrderStatus.PENDING.value, 0, float(quantity), 0
        )
        return order_id

    def place_market_order(self, option: OptionInfo, action: OrderAction,
                           quantity: int,
                           outside_rth: bool = False) -> int:
        """Place a market order. Returns orderId.
        outside_rth: allow execution during GTH/Curb sessions (盘外交易).
        """
        contract = self._make_option_contract(
            option.symbol, option.expiry, option.strike, option.right,
            self._trading_class_for(option.symbol, option.expiry),
        )

        order = Order()
        order.action = action.value
        order.orderType = "MKT"
        order.totalQuantity = quantity
        order.eTradeOnly = False
        order.firmQuoteOnly = False
        order.tif = "DAY"
        order.outsideRth = outside_rth

        order_id = self._app.next_order_id()

        commission = max(COMMISSION_PER_CONTRACT * quantity, COMMISSION_MIN)
        # Use mid price as reference for limit_price field (display only)
        ref_price = option.mid if option.mid > 0 else option.last
        order_info = OrderInfo(
            order_id=order_id,
            option=option,
            action=action,
            quantity=quantity,
            limit_price=ref_price,
            order_type=OrderType.MARKET,
            commission=commission,
        )
        self._orders[order_id] = order_info

        print(f"[ORDER] Placing MKT {action.value} {quantity}x "
              f"{option.display_name} outsideRth={outside_rth} "
              f"orderId={order_id}", flush=True)
        self._app.placeOrder(order_id, contract, order)
        self.bridge.order_status_changed.emit(
            order_id, OrderStatus.PENDING.value, 0, float(quantity), 0
        )
        return order_id

    def place_stock_order(self, symbol: str, action: OrderAction,
                          quantity: int, price: float = 0.0,
                          order_type: OrderType = OrderType.LIMIT,
                          outside_rth: bool = False) -> int:
        """Place a stock/ETF order (LMT or MKT). Returns orderId.
        Tracked with a pseudo OptionInfo (right='STK')."""
        from config import STOCK_COMMISSION_PER_SHARE, STOCK_COMMISSION_MIN

        contract = self._make_underlying_contract(symbol)

        order = Order()
        order.action = action.value
        order.orderType = order_type.value
        order.totalQuantity = quantity
        if order_type == OrderType.LIMIT:
            order.lmtPrice = price
        order.eTradeOnly = False
        order.firmQuoteOnly = False
        order.tif = "DAY"
        order.outsideRth = outside_rth

        order_id = self._app.next_order_id()

        commission = max(
            STOCK_COMMISSION_PER_SHARE * quantity, STOCK_COMMISSION_MIN
        )
        pseudo = OptionInfo(symbol=symbol, expiry="", strike=0.0, right="STK")
        self._orders[order_id] = OrderInfo(
            order_id=order_id,
            option=pseudo,
            action=action,
            quantity=quantity,
            limit_price=price,
            order_type=order_type,
            commission=commission,
        )

        print(f"[STOCK ORDER] {order_type.value} {action.value} {quantity}x "
              f"{symbol} @ {price:.2f} outsideRth={outside_rth} "
              f"orderId={order_id}", flush=True)
        self._app.placeOrder(order_id, contract, order)
        self.bridge.order_status_changed.emit(
            order_id, OrderStatus.PENDING.value, 0, float(quantity), 0
        )
        return order_id

    def cancel_order(self, order_id: int):
        """Cancel an order."""
        print(f"[ORDER] Cancelling orderId={order_id}", flush=True)
        self._user_cancel_ids.add(order_id)
        self._app.cancelOrder(order_id)

    def cancel_all_orders(self):
        """Cancel all open orders (including those placed in prior sessions)."""
        self._user_cancel_ids.update(self._orders.keys())
        self._app.reqGlobalCancel()

    def close_position(self, option: OptionInfo,
                       outside_rth: bool = False) -> int:
        """Close entire position with a market sell order. Returns orderId or -1."""
        key = option.to_ibkr_key()
        pos = self._positions.get(key)
        if not pos or pos.quantity <= 0:
            self.bridge.error_received.emit(-1, -1, "无持仓可平")
            return -1
        return self.place_market_order(option, OrderAction.SELL, pos.quantity,
                                       outside_rth=outside_rth)

    def place_forex_order(self, base: str, quote: str, action: str, amount: float):
        """Place a forex conversion order on IDEALPRO."""
        if not self._app or not self._connected:
            self.bridge.error_received.emit(-1, -1, "未连接")
            return

        contract = Contract()
        contract.symbol = base
        contract.secType = "CASH"
        contract.exchange = "IDEALPRO"
        contract.currency = quote

        order = Order()
        order.action = action  # "BUY" or "SELL"
        order.orderType = "MKT"
        order.totalQuantity = amount
        order.eTradeOnly = ""
        order.firmQuoteOnly = ""

        order_id = self._app.next_order_id()
        self._app.placeOrder(order_id, contract, order)
        self.bridge.error_received.emit(
            -1, 0, f"换汇订单已提交: {action} {amount} {base}.{quote}"
        )

    # ── Order/Execution Callbacks ─────────────────────────────────────

    def _on_open_order(self, order_id: int, option: OptionInfo,
                       action_str: str, quantity: int, price: float,
                       order_type_str: str, status_str: str):
        """Restore an order from TWS (prior session or TWS-placed)."""
        if order_id in self._orders:
            return  # Already tracked

        action = OrderAction.BUY if action_str == "BUY" else OrderAction.SELL
        order_type = OrderType.LIMIT if order_type_str == "LMT" else OrderType.MARKET
        order_info = OrderInfo(
            order_id=order_id,
            option=option,
            action=action,
            quantity=quantity,
            limit_price=price,
            order_type=order_type,
        )
        # Map status
        sl = status_str.lower()
        if "fill" in sl:
            order_info.status = OrderStatus.FILLED
        elif "cancel" in sl:
            order_info.status = OrderStatus.CANCELLED
        elif "submit" in sl:
            order_info.status = OrderStatus.SUBMITTED
        elif "inactive" in sl:
            order_info.status = OrderStatus.ERROR
        else:
            order_info.status = OrderStatus.PENDING

        self._orders[order_id] = order_info
        print(f"[ORDER RESTORE] id={order_id} {action_str} {quantity}x "
              f"{option.display_name} @ {price:.2f} status={status_str}",
              flush=True)

    # Order-event warnings (399/2109) — order may still be working, not a reject
    ORDER_WARN_CODES = {399, 2109}

    def _on_order_error(self, req_id: int, code: int, msg: str):
        """Detect IBKR order rejections and surface the reason prominently.

        IBKR reports order errors with reqId == orderId.  Codes 201/202/103
        etc. mean the order was rejected or cancelled server-side — without
        this, the only trace is a console line and a transient status-bar
        message that is easy to miss.
        """
        order = self._orders.get(req_id)
        if order is None:
            return
        if code in self.ORDER_WARN_CODES:
            return  # warning only — status bar already shows it
        if code == 202 and req_id in self._user_cancel_ids:
            self._user_cancel_ids.discard(req_id)
            return  # normal user-requested cancel, not a rejection

        order.status = OrderStatus.ERROR
        order.error_msg = f"[{code}] {msg}"
        print(f"[ORDER REJECTED] orderId={req_id} code={code} msg={msg}",
              flush=True)
        self._log_rejection(order, code, msg)
        self.bridge.order_rejected.emit(req_id, code, msg)

    # Rejection log directory: ibkr_trader/logs/
    LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

    def _log_rejection(self, order: OrderInfo, code: int, msg: str):
        """Append rejection details to logs/order_rejects_YYYY-MM-DD.jsonl.

        One JSON object per line with full context (order, quote at the
        moment of rejection, current position, other tracked orders) so the
        failure can be analyzed offline.
        """
        try:
            os.makedirs(self.LOG_DIR, exist_ok=True)
            now = datetime.now()
            path = os.path.join(
                self.LOG_DIR, f"order_rejects_{now:%Y-%m-%d}.jsonl"
            )
            key = order.option.to_ibkr_key()
            tick = self.get_tick(key)
            record = {
                "time": now.isoformat(timespec="seconds"),
                "mode": self._mode.value,
                "order_id": order.order_id,
                "contract": order.option.display_name,
                "con_id": order.option.con_id,
                "action": order.action.value,
                "quantity": order.quantity,
                "order_type": order.order_type.value,
                "limit_price": order.limit_price,
                "create_time": order.create_time.isoformat(timespec="seconds"),
                "reject_code": code,
                "reject_msg": msg,
                "quote": {
                    "bid": tick.get("bid", 0.0),
                    "ask": tick.get("ask", 0.0),
                    "last": tick.get("last", 0.0),
                },
                "position_qty": self.get_position_qty(key),
                "other_orders": [
                    {
                        "id": o.order_id,
                        "contract": o.option.display_name,
                        "action": o.action.value,
                        "qty": o.quantity,
                        "price": o.limit_price,
                        "status": o.status.value,
                    }
                    for o in self._orders.values()
                    if o.order_id != order.order_id
                ],
            }
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(f"[LOG] Rejection logged to {path}", flush=True)
        except Exception as e:
            # Logging must never break order handling
            print(f"[LOG] Failed to write rejection log: {e}", flush=True)

    def _on_order_status(self, order_id: int, status: str,
                         filled: float, remaining: float, avg_price: float):
        """Handle order status update from IBKR."""
        order = self._orders.get(order_id)
        if order is None:
            return

        status_lower = status.lower()
        if "fill" in status_lower:
            order.status = OrderStatus.FILLED
            order.filled_qty = int(filled)
            order.filled_price = avg_price
            order.fill_time = datetime.now()
        elif "cancel" in status_lower:
            # Keep ERROR (rejection reason already recorded) — the follow-up
            # "Cancelled" status from IBKR must not mask the rejection
            if order.status != OrderStatus.ERROR:
                order.status = OrderStatus.CANCELLED
        elif "submit" in status_lower:
            order.status = OrderStatus.SUBMITTED
        elif "pending" in status_lower:
            order.status = OrderStatus.PENDING
        elif "error" in status_lower or "inactive" in status_lower:
            order.status = OrderStatus.ERROR

    def _on_execution(self, order_id: int, side: str,
                      qty: float, price: float):
        """Handle execution report — update positions."""
        order = self._orders.get(order_id)
        if order is None:
            return

        # Stock fills: skip local tracking (PositionInfo math assumes the
        # option ×100 multiplier; reqPositions streams the real position)
        if order.option.right == "STK":
            self.bridge.position_changed.emit()
            return

        key = order.option.to_ibkr_key()
        action_sign = 1 if side == "BOT" or order.action == OrderAction.BUY else -1
        fill_qty = int(qty) * action_sign

        # Calculate commission for this fill
        fill_commission = max(COMMISSION_PER_CONTRACT * int(qty), COMMISSION_MIN)

        if key in self._positions:
            pos = self._positions[key]
            old_qty = pos.quantity
            new_qty = old_qty + fill_qty

            if new_qty == 0:
                del self._positions[key]
            else:
                if action_sign > 0 and old_qty >= 0:
                    # Adding to long position
                    total_cost = pos.avg_price * old_qty + price * int(qty)
                    pos.avg_price = total_cost / new_qty
                pos.quantity = new_qty
                pos.total_commission += fill_commission
        else:
            if fill_qty != 0:
                self._positions[key] = PositionInfo(
                    option=order.option,
                    quantity=fill_qty,
                    avg_price=price,
                    total_commission=fill_commission,
                )

        self.bridge.position_changed.emit()

    # ── Contract Helpers ──────────────────────────────────────────────

    @staticmethod
    def _make_underlying_contract(symbol: str) -> Contract:
        """Create a contract for the underlying (stock or index)."""
        c = Contract()
        c.symbol = symbol
        c.currency = "USD"
        if symbol.upper() in INDEX_SYMBOLS:
            c.secType = "IND"
            c.exchange = "CBOE"
        else:
            c.secType = "STK"
            c.exchange = "SMART"
            c.primaryExchange = "ARCA"
        return c

    @staticmethod
    def _make_stock_contract(symbol: str) -> Contract:
        """Legacy — use _make_underlying_contract instead."""
        return IBKREngine._make_underlying_contract(symbol)

    @staticmethod
    def _make_option_contract(symbol: str, expiry: str,
                              strike: float, right: str,
                              trading_class: str = "") -> Contract:
        c = Contract()
        c.symbol = symbol
        c.secType = "OPT"
        c.exchange = "SMART"
        c.currency = "USD"
        c.lastTradeDateOrContractMonth = expiry
        c.strike = strike
        c.right = right
        c.multiplier = "100"
        # tradingClass disambiguates symbols with multiple option classes
        # (e.g. SPX monthly vs SPXW weekly). Without it index options come
        # back ambiguous (error 200) and never stream a price.
        if trading_class:
            c.tradingClass = trading_class
        return c

    # ── Combo / Spread Orders ────────────────────────────────────────

    def resolve_option_con_id(self, symbol: str, expiry: str,
                               strike: float, right: str) -> int:
        """Resolve conId for a specific option contract (blocking).
        Uses reqContractDetails + threading.Event, same pattern as get_con_id.
        """
        contract = self._make_option_contract(
            symbol, expiry, strike, right,
            self._trading_class_for(symbol, expiry),
        )
        req_id = self._app.next_req_id()
        self._app._contract_data[req_id] = {
            "details": [], "event": threading.Event(), "error": None,
        }
        self._app.reqContractDetails(req_id, contract)

        state = self._app._contract_data[req_id]
        if not state["event"].wait(timeout=10):
            self._app._contract_data.pop(req_id, None)
            raise RuntimeError(
                f"Timeout resolving conId: {symbol} {expiry} {right} {strike}"
            )
        if state["error"]:
            code, msg = state["error"]
            self._app._contract_data.pop(req_id, None)
            raise RuntimeError(
                f"Contract error {symbol} {expiry} {right} {strike}: "
                f"code={code} {msg}"
            )
        if not state["details"]:
            self._app._contract_data.pop(req_id, None)
            raise RuntimeError(
                f"No contract found: {symbol} {expiry} {right} {strike}"
            )

        con_id = state["details"][0].contract.conId
        self._app._contract_data.pop(req_id, None)
        return con_id

    def place_combo_order(self, symbol: str, legs: list,
                          action: str, quantity: int,
                          limit_price: float,
                          outside_rth: bool = False) -> int:
        """Place a BAG (combo) order for a multi-leg strategy.

        Args:
            symbol: Underlying symbol (e.g. "SPY")
            legs: List of ComboLegInfo objects (con_id must be resolved)
            action: "BUY" or "SELL"
            quantity: Number of combo units
            limit_price: Net limit price for the combo
            outside_rth: Allow execution outside regular trading hours

        Returns:
            orderId
        """
        from ibapi.contract import ComboLeg
        from ibapi.tag_value import TagValue

        if not self._app or not self._connected:
            self.bridge.error_received.emit(-1, -1, "未连接")
            return -1

        # Build BAG contract
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "BAG"
        contract.currency = "USD"
        contract.exchange = "SMART"

        combo_legs = []
        for leg in legs:
            cl = ComboLeg()
            cl.conId = leg.con_id
            cl.ratio = leg.ratio
            cl.action = leg.action
            cl.exchange = leg.exchange or "SMART"
            combo_legs.append(cl)
        contract.comboLegs = combo_legs

        # Build order
        order = Order()
        order.action = action
        order.orderType = "LMT"
        order.totalQuantity = quantity
        order.lmtPrice = limit_price
        order.eTradeOnly = False
        order.firmQuoteOnly = False
        order.tif = "DAY"
        order.outsideRth = outside_rth
        order.smartComboRoutingParams = [
            TagValue("NonGuaranteed", "1"),
        ]

        order_id = self._app.next_order_id()

        # Track with synthetic OptionInfo (right="COMBO")
        from models import ComboLegInfo
        total_contracts = sum(leg.ratio for leg in legs)
        commission = max(
            COMMISSION_PER_CONTRACT * total_contracts * quantity,
            COMMISSION_MIN,
        )
        option = OptionInfo(
            symbol=symbol,
            expiry="",
            strike=0.0,
            right="COMBO",
        )
        order_info = OrderInfo(
            order_id=order_id,
            option=option,
            action=OrderAction.BUY if action == "BUY" else OrderAction.SELL,
            quantity=quantity,
            limit_price=limit_price,
            order_type=OrderType.LIMIT,
            commission=commission,
        )
        self._orders[order_id] = order_info

        leg_desc = " + ".join(
            f"{l.action} {l.ratio}x {l.right}{l.strike}" for l in legs
        )
        print(f"[COMBO ORDER] {action} {quantity}x {symbol} "
              f"[{leg_desc}] @ {limit_price:.2f} "
              f"outsideRth={outside_rth} orderId={order_id}", flush=True)

        self._app.placeOrder(order_id, contract, order)
        self.bridge.order_status_changed.emit(
            order_id, OrderStatus.PENDING.value, 0, float(quantity), 0
        )
        return order_id

    def get_position_qty(self, option_key: str) -> int:
        """Get current position quantity for an option key."""
        pos = self._positions.get(option_key)
        return pos.quantity if pos else 0
