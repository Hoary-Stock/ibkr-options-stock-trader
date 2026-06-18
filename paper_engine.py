"""Paper Trading Engine — local simulation, no IBKR order submission.

Simulates order fills based on streaming tick data.
BUY limit: fills when ask <= limit_price
SELL limit: fills when bid >= limit_price
No short selling allowed.
"""

import threading
from datetime import datetime

from PyQt5.QtCore import QObject, pyqtSignal, QTimer

from config import COMMISSION_PER_CONTRACT, COMMISSION_MIN, PAPER_STARTING_CAPITAL
from models import (
    OptionInfo, OrderInfo, PositionInfo,
    OrderAction, OrderStatus, OrderType, TradingMode,
)


class PaperSignalBridge(QObject):
    """Signal bridge matching IBKRSignalBridge interface."""

    tick_updated = pyqtSignal(str, float, float, float)
    chain_ready = pyqtSignal(list, list)
    order_status_changed = pyqtSignal(int, str, float, float, float)
    execution_received = pyqtSignal(int, str, float, float)
    position_changed = pyqtSignal()
    connected = pyqtSignal()
    disconnected = pyqtSignal()
    error_received = pyqtSignal(int, int, str)
    contract_detail_received = pyqtSignal(int, object)

    # Match new IBKR bridge signals
    account_summary_updated = pyqtSignal(str, str, str, str)
    account_summary_end = pyqtSignal()
    portfolio_position_received = pyqtSignal(object)
    portfolio_positions_end = pyqtSignal()
    pnl_updated = pyqtSignal(float, float, float)
    depth_updated = pyqtSignal(int, int, int, int, float, int)
    open_order_received = pyqtSignal(int, object, str, int, float, str, str)
    order_rejected = pyqtSignal(int, int, str)
    pnl_single_updated = pyqtSignal(int, float, float, float, float)


class PaperEngine:
    """Local paper trading engine. Uses IBKR for market data only."""

    def __init__(self, ibkr_engine):
        """
        Args:
            ibkr_engine: The IBKREngine instance for market data access.
        """
        self.ibkr = ibkr_engine
        self.bridge = PaperSignalBridge()

        self._next_order_id = 100000
        self._order_lock = threading.Lock()
        self._orders: dict[int, OrderInfo] = {}
        self._positions: dict[str, PositionInfo] = {}
        self._starting_capital = PAPER_STARTING_CAPITAL
        self._realized_pnl = 0.0

        # Timer to check fills periodically
        self._fill_timer = QTimer()
        self._fill_timer.timeout.connect(self._check_fills)
        self._fill_timer.start(500)  # Check every 500ms

    @property
    def is_connected(self) -> bool:
        return self.ibkr.is_connected

    @property
    def mode(self) -> TradingMode:
        return TradingMode.PAPER

    @property
    def positions(self) -> dict[str, PositionInfo]:
        return self._positions

    @property
    def orders(self) -> dict[int, OrderInfo]:
        return self._orders

    def next_order_id(self) -> int:
        with self._order_lock:
            self._next_order_id += 1
            return self._next_order_id

    # Per-position PnL — delegate to IBKR engine (real account data)
    def request_pnl_single(self, con_id: int) -> int:
        return self.ibkr.request_pnl_single(con_id)

    def cancel_pnl_single(self, con_id: int):
        self.ibkr.cancel_pnl_single(con_id)

    # ── Delegate market data to IBKR engine ───────────────────────────

    def request_option_chain(self, symbol):
        return self.ibkr.request_option_chain(symbol)

    def subscribe_option_tick(self, option):
        return self.ibkr.subscribe_option_tick(option)

    def unsubscribe_tick(self, req_id):
        return self.ibkr.unsubscribe_tick(req_id)

    def get_tick(self, key):
        return self.ibkr.get_tick(key)

    def get_con_id(self, symbol):
        return self.ibkr.get_con_id(symbol)

    def connect(self, mode=TradingMode.PAPER):
        return self.ibkr.connect(mode)

    def disconnect(self):
        self._fill_timer.stop()
        return self.ibkr.disconnect()

    # ── Delegate depth to IBKR engine ─────────────────────────────────

    def subscribe_market_depth(self, option):
        return self.ibkr.subscribe_market_depth(option)

    def unsubscribe_market_depth(self):
        return self.ibkr.unsubscribe_market_depth()

    # ── Account Summary (simulated) ───────────────────────────────────

    def request_account_summary(self):
        """Emit simulated account summary."""
        unrealized = self._calc_unrealized_pnl()
        net_liq = self._starting_capital + unrealized + self._realized_pnl
        total_cash = self._starting_capital + self._realized_pnl - self._calc_cost_basis()
        buying_power = max(0, total_cash)

        self.bridge.account_summary_updated.emit(
            "NetLiquidation", f"{net_liq:.2f}", "USD", "Paper"
        )
        self.bridge.account_summary_updated.emit(
            "TotalCashValue", f"{total_cash:.2f}", "USD", "Paper"
        )
        self.bridge.account_summary_updated.emit(
            "BuyingPower", f"{buying_power:.2f}", "USD", "Paper"
        )
        self.bridge.account_summary_updated.emit(
            "UnrealizedPnL", f"{unrealized:.2f}", "USD", "Paper"
        )
        self.bridge.account_summary_updated.emit(
            "RealizedPnL", f"{self._realized_pnl:.2f}", "USD", "Paper"
        )
        self.bridge.account_summary_end.emit()

    def cancel_account_summary(self):
        pass  # No-op for paper

    def request_positions(self):
        pass  # Paper positions tracked internally

    def cancel_positions(self):
        pass

    def request_pnl(self, account=""):
        """Emit simulated PnL."""
        unrealized = self._calc_unrealized_pnl()
        self.bridge.pnl_updated.emit(
            unrealized + self._realized_pnl, unrealized, self._realized_pnl
        )

    def cancel_pnl(self):
        pass

    def _calc_unrealized_pnl(self) -> float:
        total = 0.0
        for key, pos in self._positions.items():
            tick = self.ibkr.get_tick(key)
            last = tick.get("last", 0)
            bid = tick.get("bid", 0)
            ask = tick.get("ask", 0)
            current = last if last > 0 else ((bid + ask) / 2 if bid > 0 and ask > 0 else bid)
            if current > 0:
                pos.current_price = current
            total += pos.unrealized_pnl
        return total

    def _calc_cost_basis(self) -> float:
        total = 0.0
        for pos in self._positions.values():
            total += abs(pos.cost_basis)
        return total

    # ── Order Management ──────────────────────────────────────────────

    def place_limit_order(self, option: OptionInfo, action: OrderAction,
                          quantity: int, price: float,
                          outside_rth: bool = False) -> int:
        """Place a simulated limit order. Returns orderId."""
        # Prevent short selling
        if action == OrderAction.SELL:
            key = option.to_ibkr_key()
            pos = self._positions.get(key)
            available = pos.quantity if pos else 0
            if available <= 0:
                self.bridge.error_received.emit(-1, -1, "无持仓，不允许卖出")
                return -1
            if quantity > available:
                quantity = available

        order_id = self.next_order_id()
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
        self.bridge.order_status_changed.emit(
            order_id, OrderStatus.SUBMITTED.value, 0, float(quantity), 0
        )

        # Try immediate fill
        self._try_fill(order_info)
        return order_id

    def place_market_order(self, option: OptionInfo, action: OrderAction,
                           quantity: int,
                           outside_rth: bool = False) -> int:
        """Place a simulated market order — immediate fill at ask (buy) or bid (sell)."""
        if action == OrderAction.SELL:
            key = option.to_ibkr_key()
            pos = self._positions.get(key)
            available = pos.quantity if pos else 0
            if available <= 0:
                self.bridge.error_received.emit(-1, -1, "无持仓，不允许卖出")
                return -1
            if quantity > available:
                quantity = available

        key = option.to_ibkr_key()
        tick = self.ibkr.get_tick(key)
        bid = tick.get("bid", 0)
        ask = tick.get("ask", 0)
        last = tick.get("last", 0)

        if action == OrderAction.BUY:
            fill_price = ask if ask > 0 else (last if last > 0 else bid)
        else:
            fill_price = bid if bid > 0 else (last if last > 0 else ask)

        if fill_price <= 0:
            self.bridge.error_received.emit(-1, -1, "无法获取价格，市价单失败")
            return -1

        order_id = self.next_order_id()
        commission = max(COMMISSION_PER_CONTRACT * quantity, COMMISSION_MIN)
        order_info = OrderInfo(
            order_id=order_id,
            option=option,
            action=action,
            quantity=quantity,
            limit_price=fill_price,
            order_type=OrderType.MARKET,
            commission=commission,
        )
        self._orders[order_id] = order_info

        # Immediate fill
        order_info.status = OrderStatus.FILLED
        order_info.filled_qty = quantity
        order_info.filled_price = fill_price
        order_info.fill_time = datetime.now()

        self._update_position(order_info, fill_price)

        self.bridge.order_status_changed.emit(
            order_id, OrderStatus.FILLED.value,
            float(quantity), 0, fill_price
        )
        self.bridge.execution_received.emit(
            order_id,
            "BOT" if action == OrderAction.BUY else "SLD",
            float(quantity),
            fill_price,
        )
        return order_id

    def cancel_order(self, order_id: int):
        """Cancel a pending order."""
        order = self._orders.get(order_id)
        if order and order.status in (OrderStatus.PENDING, OrderStatus.SUBMITTED):
            order.status = OrderStatus.CANCELLED
            self.bridge.order_status_changed.emit(
                order_id, OrderStatus.CANCELLED.value, 0, 0, 0
            )

    def cancel_all_orders(self):
        """Cancel all pending orders."""
        for order_id, order in list(self._orders.items()):
            if order.status in (OrderStatus.PENDING, OrderStatus.SUBMITTED):
                order.status = OrderStatus.CANCELLED
                self.bridge.order_status_changed.emit(
                    order_id, OrderStatus.CANCELLED.value, 0, 0, 0
                )

    def close_position(self, option: OptionInfo,
                       outside_rth: bool = False) -> int:
        """Close entire position with a market sell."""
        key = option.to_ibkr_key()
        pos = self._positions.get(key)
        if not pos or pos.quantity <= 0:
            self.bridge.error_received.emit(-1, -1, "无持仓可平")
            return -1
        return self.place_market_order(option, OrderAction.SELL, pos.quantity,
                                       outside_rth=outside_rth)

    def place_forex_order(self, base: str, quote: str, action: str, amount: float):
        """Forex not supported in paper mode."""
        self.bridge.error_received.emit(-1, -1, "模拟模式不支持换汇")

    def reconnect(self, mode: TradingMode) -> bool:
        """Delegate reconnect to IBKR engine."""
        return self.ibkr.reconnect(mode)

    def _try_fill(self, order: OrderInfo) -> bool:
        """Check if an order can be filled at current prices."""
        if order.status not in (OrderStatus.PENDING, OrderStatus.SUBMITTED):
            return False

        key = order.option.to_ibkr_key()
        tick = self.ibkr.get_tick(key)
        bid = tick.get("bid", 0)
        ask = tick.get("ask", 0)

        filled = False
        fill_price = 0.0

        if order.action == OrderAction.BUY:
            # BUY limit: fills when ask <= limit_price (or at limit_price)
            if ask > 0 and ask <= order.limit_price:
                fill_price = ask
                filled = True
            elif ask <= 0 and bid > 0 and bid <= order.limit_price:
                # No ask available, use bid as reference
                fill_price = order.limit_price
                filled = True
        else:
            # SELL limit: fills when bid >= limit_price
            if bid > 0 and bid >= order.limit_price:
                fill_price = bid
                filled = True

        if filled:
            order.status = OrderStatus.FILLED
            order.filled_qty = order.quantity
            order.filled_price = fill_price
            order.fill_time = datetime.now()

            # Update position
            self._update_position(order, fill_price)

            self.bridge.order_status_changed.emit(
                order.order_id, OrderStatus.FILLED.value,
                float(order.quantity), 0, fill_price
            )
            self.bridge.execution_received.emit(
                order.order_id,
                "BOT" if order.action == OrderAction.BUY else "SLD",
                float(order.quantity),
                fill_price,
            )
            return True
        return False

    def _update_position(self, order: OrderInfo, fill_price: float):
        """Update positions after a fill."""
        key = order.option.to_ibkr_key()
        qty_change = order.quantity if order.action == OrderAction.BUY else -order.quantity

        if key in self._positions:
            pos = self._positions[key]
            old_qty = pos.quantity
            new_qty = old_qty + qty_change

            if new_qty == 0:
                # Closing position: record realized P&L (minus commissions)
                self._realized_pnl += (fill_price - pos.avg_price) * old_qty * 100
                self._realized_pnl -= pos.total_commission + order.commission
                del self._positions[key]
            elif new_qty < 0:
                # Should not happen (short sell blocked), but handle gracefully
                del self._positions[key]
            else:
                if qty_change > 0 and old_qty >= 0:
                    total_cost = pos.avg_price * old_qty + fill_price * order.quantity
                    pos.avg_price = total_cost / new_qty
                elif qty_change < 0:
                    # Partial close: record realized P&L for closed portion
                    closed_qty = abs(qty_change)
                    self._realized_pnl += (fill_price - pos.avg_price) * closed_qty * 100
                pos.quantity = new_qty
                pos.total_commission += order.commission
        else:
            if qty_change > 0:
                self._positions[key] = PositionInfo(
                    option=order.option,
                    quantity=qty_change,
                    avg_price=fill_price,
                    total_commission=order.commission,
                )

        self.bridge.position_changed.emit()

    def _check_fills(self):
        """Periodically check if pending orders can be filled."""
        for order in list(self._orders.values()):
            if order.status in (OrderStatus.PENDING, OrderStatus.SUBMITTED):
                self._try_fill(order)

    def resolve_option_con_id(self, symbol: str, expiry: str,
                               strike: float, right: str) -> int:
        """Delegate to IBKR engine for contract resolution."""
        return self.ibkr.resolve_option_con_id(symbol, expiry, strike, right)

    def place_combo_order(self, symbol: str, legs: list,
                          action: str, quantity: int,
                          limit_price: float,
                          outside_rth: bool = False) -> int:
        """Combo orders not supported in paper mode."""
        self.bridge.error_received.emit(-1, -1, "模拟模式不支持组合订单")
        return -1

    def get_position_qty(self, option_key: str) -> int:
        """Get current position quantity for an option key."""
        pos = self._positions.get(option_key)
        return pos.quantity if pos else 0
