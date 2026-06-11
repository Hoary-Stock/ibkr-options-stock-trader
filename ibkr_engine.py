"""IBKR API Engine — EWrapper/EClient + order management.

Reuses connection patterns from tradebot/ibkr_paper_trader.py,
adds order placement/cancellation and Qt signal bridge.
"""

import time
import threading
from datetime import datetime

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order

from PyQt5.QtCore import QObject, pyqtSignal

from config import (
    IBKR_HOST, IBKR_PAPER_PORT, IBKR_LIVE_PORT,
    IBKR_CLIENT_ID, MARKET_DATA_TYPE, IGNORED_ERROR_CODES,
    COMMISSION_PER_CONTRACT, COMMISSION_MIN, DEPTH_ROWS,
    INDEX_SYMBOLS,
)
from models import (
    OptionInfo, OrderInfo, PositionInfo, PortfolioPosition,
    OrderAction, OrderStatus, OrderType, TradingMode,
)


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


# ── IBKR API App ─────────────────────────────────────────────────────

class IBKRApp(EWrapper, EClient):
    """EWrapper + EClient with callbacks for option chain, ticks, orders."""

    def __init__(self, bridge: IBKRSignalBridge):
        EClient.__init__(self, self)
        self.bridge = bridge

        self.connected_event = threading.Event()
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

        # Account summary tracking
        self._account_summary_req_id: int | None = None

        # PnL tracking
        self._pnl_req_id: int | None = None

        # Account name (discovered from accountSummary)
        self._account_name: str = ""

        # One-time market data warning flag
        self._mkt_data_warned: bool = False

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

        d = self._tick_data.setdefault(key, {"bid": 0.0, "ask": 0.0, "last": 0.0})

        if tickType in (1, 66):     # bid / delayed bid
            d["bid"] = float(price)
        elif tickType in (2, 67):   # ask / delayed ask
            d["ask"] = float(price)
        elif tickType in (4, 68):   # last / delayed last
            d["last"] = float(price)

        self.bridge.tick_updated.emit(key, d["bid"], d["ask"], d["last"])

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
        self.bridge.order_status_changed.emit(
            orderId, status, float(filled), float(remaining), float(avgFillPrice)
        )

    def openOrder(self, orderId, contract, order, orderState):
        # Restore existing orders on reconnect
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
        self.bridge.pnl_updated.emit(
            float(dailyPnL), float(unrealizedPnL), float(realizedPnL)
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
        d = self._tick_data.setdefault(key, {"bid": 0.0, "ask": 0.0, "last": 0.0})
        d["bid"] = float(bidPrice)
        d["ask"] = float(askPrice)
        d["bid_size"] = int(bidSize)
        d["ask_size"] = int(askSize)
        self.bridge.tick_updated.emit(key, d["bid"], d["ask"], d.get("last", 0.0))

    def tickByTickAllLast(self, reqId, tickType, time_, price,
                          size, tickAttribLast, exchange, specialConditions):
        key = self._tick_req_to_key.get(reqId)
        if key is None:
            return
        d = self._tick_data.setdefault(key, {"bid": 0.0, "ask": 0.0, "last": 0.0})
        d["last"] = float(price)
        self.bridge.tick_updated.emit(key, d["bid"], d["ask"], d["last"])


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
        self._mode = TradingMode.PAPER

        # Order & position tracking
        self._orders: dict[int, OrderInfo] = {}
        self._positions: dict[str, PositionInfo] = {}  # key -> PositionInfo

        # Connect internal signals
        self.bridge.order_status_changed.connect(self._on_order_status)
        self.bridge.execution_received.connect(self._on_execution)

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

    def connect(self, mode: TradingMode = TradingMode.PAPER) -> bool:
        """Connect to TWS/Gateway. Returns True on success."""
        self._mode = mode
        port = IBKR_PAPER_PORT if mode == TradingMode.PAPER else IBKR_LIVE_PORT

        self._app = IBKRApp(self.bridge)
        try:
            self._app.connect(IBKR_HOST, port, IBKR_CLIENT_ID)
        except Exception as e:
            self.bridge.error_received.emit(-1, -1, f"Connection failed: {e}")
            return False

        self._thread = threading.Thread(
            target=self._app.run, daemon=True, name="ibkr-reader"
        )
        self._thread.start()

        if not self._app.connected_event.wait(timeout=self.CONNECT_TIMEOUT):
            try:
                self._app.disconnect()
            except Exception:
                pass
            self._connected = False
            return False

        self._connected = True
        self._app.reqMarketDataType(MARKET_DATA_TYPE)
        return True

    def disconnect(self):
        """Disconnect from TWS."""
        self.cancel_account_summary()
        self.cancel_pnl()
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

        expirations = sorted(all_expirations)
        strikes = sorted(all_strikes)
        return expirations, strikes

    # ── Market Data Subscription ──────────────────────────────────────

    def subscribe_option_tick(self, option: OptionInfo) -> int:
        """Subscribe to streaming tick data for an option. Returns reqId."""
        contract = self._make_option_contract(
            option.symbol, option.expiry, option.strike, option.right
        )
        req_id = self._app.next_req_id()
        key = option.to_ibkr_key()
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
        contract = self._make_option_contract(
            option.symbol, option.expiry, option.strike, option.right
        )
        req_id = self._app.next_req_id()
        self._app._depth_req_id = req_id
        try:
            self._app.reqMktDepth(req_id, contract, DEPTH_ROWS, False, [])
        except Exception:
            pass

    def unsubscribe_market_depth(self):
        """Cancel market depth subscription."""
        if self._app and self._app._depth_req_id is not None:
            try:
                self._app.cancelMktDepth(self._app._depth_req_id, False)
            except Exception:
                pass
            self._app._depth_req_id = None

    # ── Order Management ──────────────────────────────────────────────

    def place_limit_order(self, option: OptionInfo, action: OrderAction,
                          quantity: int, price: float) -> int:
        """Place a limit order. Returns orderId."""
        contract = self._make_option_contract(
            option.symbol, option.expiry, option.strike, option.right
        )

        order = Order()
        order.action = action.value
        order.orderType = "LMT"
        order.totalQuantity = quantity
        order.lmtPrice = price
        order.eTradeOnly = ""
        order.firmQuoteOnly = ""
        order.tif = "DAY"

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

        self._app.placeOrder(order_id, contract, order)
        self.bridge.order_status_changed.emit(
            order_id, OrderStatus.PENDING.value, 0, float(quantity), 0
        )
        return order_id

    def place_market_order(self, option: OptionInfo, action: OrderAction,
                           quantity: int) -> int:
        """Place a market order. Returns orderId."""
        contract = self._make_option_contract(
            option.symbol, option.expiry, option.strike, option.right
        )

        order = Order()
        order.action = action.value
        order.orderType = "MKT"
        order.totalQuantity = quantity
        order.eTradeOnly = ""
        order.firmQuoteOnly = ""
        order.tif = "DAY"

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

        self._app.placeOrder(order_id, contract, order)
        self.bridge.order_status_changed.emit(
            order_id, OrderStatus.PENDING.value, 0, float(quantity), 0
        )
        return order_id

    def cancel_order(self, order_id: int):
        """Cancel an order."""
        self._app.cancelOrder(order_id, "")

    def cancel_all_orders(self):
        """Cancel all pending orders."""
        for order_id, order in list(self._orders.items()):
            if order.status in (OrderStatus.PENDING, OrderStatus.SUBMITTED):
                try:
                    self._app.cancelOrder(order_id, "")
                except Exception:
                    pass

    def close_position(self, option: OptionInfo) -> int:
        """Close entire position with a market sell order. Returns orderId or -1."""
        key = option.to_ibkr_key()
        pos = self._positions.get(key)
        if not pos or pos.quantity <= 0:
            self.bridge.error_received.emit(-1, -1, "无持仓可平")
            return -1
        return self.place_market_order(option, OrderAction.SELL, pos.quantity)

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

        key = order.option.to_ibkr_key()
        action_sign = 1 if side == "BOT" or order.action == OrderAction.BUY else -1
        fill_qty = int(qty) * action_sign

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
        else:
            if fill_qty != 0:
                self._positions[key] = PositionInfo(
                    option=order.option,
                    quantity=fill_qty,
                    avg_price=price,
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
                              strike: float, right: str) -> Contract:
        c = Contract()
        c.symbol = symbol
        c.secType = "OPT"
        c.exchange = "SMART"
        c.currency = "USD"
        c.lastTradeDateOrContractMonth = expiry
        c.strike = strike
        c.right = right
        c.multiplier = "100"
        return c

    def get_position_qty(self, option_key: str) -> int:
        """Get current position quantity for an option key."""
        pos = self._positions.get(option_key)
        return pos.quantity if pos else 0
