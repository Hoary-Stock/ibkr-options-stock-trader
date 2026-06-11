"""Main window layout — assembles all widgets."""

import threading
from datetime import datetime

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTabWidget, QMessageBox, QStatusBar,
    QPushButton, QLabel,
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal

from config import (
    COLOR_BG, COLOR_BG_DARK, COLOR_BG_PANEL, COLOR_TEXT,
    COLOR_BORDER, COLOR_ACCENT, COLOR_GREEN, COLOR_RED,
    SPX_SESSION_GTH_START, SPX_SESSION_GTH_END,
    SPX_SESSION_RTH_START, SPX_SESSION_RTH_END,
)
from models import OptionInfo, OrderAction, TradingMode
from ibkr_engine import IBKREngine
from paper_engine import PaperEngine
from widgets.symbol_bar import SymbolBar
from widgets.option_chain import OptionChainWidget
from widgets.price_ladder import PriceLadder
from widgets.position_panel import PositionPanel
from widgets.order_panel import OrderPanel
from widgets.account_bar import AccountBar
from widgets.currency_dialog import CurrencyExchangeDialog
from widgets.chart_window import ChartWindow


DARK_STYLESHEET = f"""
    QMainWindow, QWidget {{
        background-color: {COLOR_BG};
        color: {COLOR_TEXT};
    }}
    QTableWidget {{
        background-color: {COLOR_BG_DARK};
        alternate-background-color: {COLOR_BG};
        color: {COLOR_TEXT};
        gridline-color: {COLOR_BORDER};
        border: 1px solid {COLOR_BORDER};
        selection-background-color: {COLOR_BG_PANEL};
    }}
    QTableWidget::item {{
        padding: 2px 4px;
    }}
    QHeaderView::section {{
        background-color: {COLOR_BG_PANEL};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER};
        padding: 4px;
        font-weight: bold;
    }}
    QTabWidget::pane {{
        border: 1px solid {COLOR_BORDER};
        background-color: {COLOR_BG_DARK};
    }}
    QTabBar::tab {{
        background-color: {COLOR_BG_DARK};
        color: {COLOR_TEXT};
        padding: 6px 12px;
        border: 1px solid {COLOR_BORDER};
        border-bottom: none;
        margin-right: 2px;
    }}
    QTabBar::tab:selected {{
        background-color: {COLOR_BG_PANEL};
        color: {COLOR_ACCENT};
        font-weight: bold;
    }}
    QLineEdit {{
        background-color: {COLOR_BG_DARK};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER};
        padding: 4px 8px;
        border-radius: 3px;
    }}
    QComboBox {{
        background-color: {COLOR_BG_DARK};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER};
        padding: 4px 8px;
        border-radius: 3px;
    }}
    QComboBox::drop-down {{
        border: none;
    }}
    QComboBox QAbstractItemView {{
        background-color: {COLOR_BG_DARK};
        color: {COLOR_TEXT};
        selection-background-color: {COLOR_BG_PANEL};
    }}
    QSpinBox {{
        background-color: {COLOR_BG_DARK};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER};
        padding: 4px;
        border-radius: 3px;
    }}
    QLabel {{
        color: {COLOR_TEXT};
    }}
    QSplitter::handle {{
        background-color: {COLOR_BORDER};
    }}
    QScrollArea {{
        border: none;
    }}
    QScrollBar:vertical {{
        background-color: {COLOR_BG_DARK};
        width: 10px;
    }}
    QScrollBar::handle:vertical {{
        background-color: {COLOR_BORDER};
        border-radius: 4px;
        min-height: 20px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QStatusBar {{
        background-color: {COLOR_BG_DARK};
        color: {COLOR_TEXT};
    }}
"""


class MainWindow(QMainWindow):
    """Main application window."""

    _search_validated = pyqtSignal(object)  # OptionInfo — validated search result

    def __init__(self):
        super().__init__()
        self.setWindowTitle("IBKR 点价交易")
        self.setMinimumSize(1200, 800)
        self.resize(1400, 900)

        # Engines
        self.ibkr_engine = IBKREngine()
        self.paper_engine = PaperEngine(self.ibkr_engine)
        self._active_engine = self.paper_engine  # Default to paper

        self._current_symbol = "SPY"
        self._current_option: OptionInfo | None = None
        self._chart_windows: list[ChartWindow] = []

        self._build_ui()
        self._connect_signals()

        self.setStyleSheet(DARK_STYLESHEET)
        self.statusBar().showMessage("就绪 — 点击「连接」开始")

        # Session indicator timer (updates every 10 seconds)
        self._session_timer = QTimer()
        self._session_timer.timeout.connect(self._update_session_indicator)
        self._session_timer.start(10_000)
        self._update_session_indicator()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # ── Top bar ──
        top_bar_layout = QHBoxLayout()

        self.symbol_bar = SymbolBar()
        top_bar_layout.addWidget(self.symbol_bar, stretch=1)

        self._chart_btn = QPushButton("K线图")
        self._chart_btn.setFixedHeight(30)
        self._chart_btn.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_BG_PANEL}; color: {COLOR_ACCENT}; "
            f"border: 1px solid {COLOR_BORDER}; padding: 2px 12px; border-radius: 3px; "
            f"font-weight: bold; }}"
            f"QPushButton:hover {{ background-color: {COLOR_ACCENT}; color: {COLOR_BG}; }}"
        )
        self._chart_btn.clicked.connect(self._on_open_chart)
        top_bar_layout.addWidget(self._chart_btn)

        # Session indicator (shows current market session for SPX options)
        self._session_label = QLabel("--")
        self._session_label.setFixedHeight(30)
        self._session_label.setStyleSheet(
            f"color: {COLOR_TEXT}; background-color: {COLOR_BG_PANEL}; "
            f"border: 1px solid {COLOR_BORDER}; padding: 2px 10px; "
            f"border-radius: 3px; font-size: 12px; font-weight: bold;"
        )
        self._session_label.setToolTip(
            "SPX 期权交易时段 (ET)\n"
            "GTH 夜盘: 20:15 - 09:15\n"
            "RTH 正常盘: 09:30 - 16:15"
        )
        top_bar_layout.addWidget(self._session_label)

        main_layout.addLayout(top_bar_layout)

        # ── Account bar ──
        self.account_bar = AccountBar()
        main_layout.addWidget(self.account_bar)

        # ── Main content: vertical splitter ──
        self.main_splitter = QSplitter(Qt.Vertical)

        # Top: Option chain
        self.option_chain = OptionChainWidget()
        self.main_splitter.addWidget(self.option_chain)

        # Bottom: horizontal splitter (price ladder | position/order panels)
        self.bottom_splitter = QSplitter(Qt.Horizontal)

        # Left: Price ladder
        self.price_ladder = PriceLadder()
        self.bottom_splitter.addWidget(self.price_ladder)

        # Right: Position + Order tabs
        self.right_tabs = QTabWidget()
        self.position_panel = PositionPanel()
        self.order_panel = OrderPanel()
        self.right_tabs.addTab(self.position_panel, "持仓")
        self.right_tabs.addTab(self.order_panel, "委托")
        self.bottom_splitter.addWidget(self.right_tabs)

        self.bottom_splitter.setSizes([380, 500])
        self.main_splitter.addWidget(self.bottom_splitter)

        self.main_splitter.setSizes([400, 400])
        main_layout.addWidget(self.main_splitter)

    def _connect_signals(self):
        # Symbol bar
        self.symbol_bar.connect_clicked.connect(self._on_connect)
        self.symbol_bar.disconnect_clicked.connect(self._on_disconnect)
        self.symbol_bar.symbol_changed.connect(self._on_symbol_changed)
        self.symbol_bar.mode_changed.connect(self._on_mode_changed)
        self.symbol_bar.reconnect_requested.connect(self._on_reconnect_requested)

        # Option chain -> price ladder
        self.option_chain.option_selected.connect(self._on_option_selected)

        # Price ladder -> order (limit orders from price clicks)
        self.price_ladder.order_requested.connect(self._on_order_requested)

        # Price ladder -> market orders
        self.price_ladder.market_order_requested.connect(self._on_market_order_requested)

        # Price ladder -> close position
        self.price_ladder.close_position_requested.connect(self._on_close_position_requested)

        # Price ladder -> cancel all
        self.price_ladder.cancel_all_requested.connect(self._on_cancel_all_requested)

        # Price ladder -> contract search
        self.price_ladder.contract_searched.connect(self._on_contract_searched)
        self._search_validated.connect(self._load_validated_contract)

        # Position panel -> open ladder
        self.position_panel.position_clicked.connect(self._on_option_selected)

        # Order panel -> cancel
        self.order_panel.cancel_requested.connect(self._on_cancel_order)

        # Account bar -> currency exchange
        self.account_bar.currency_exchange_clicked.connect(self._on_currency_exchange)

        # IBKR engine signals
        self.ibkr_engine.bridge.connected.connect(self._on_connected)
        self.ibkr_engine.bridge.disconnected.connect(self._on_disconnected)
        self.ibkr_engine.bridge.error_received.connect(self._on_error)

        # IBKR account/portfolio signals
        self.ibkr_engine.bridge.account_summary_updated.connect(self.account_bar.update_account)
        self.ibkr_engine.bridge.pnl_updated.connect(self.account_bar.update_daily_pnl)
        self.ibkr_engine.bridge.portfolio_position_received.connect(
            self.position_panel.on_portfolio_position
        )
        self.ibkr_engine.bridge.portfolio_positions_end.connect(
            self.position_panel.on_portfolio_positions_end
        )
        self.ibkr_engine.bridge.account_summary_end.connect(self._on_account_summary_end)

        # Paper engine signals
        self.paper_engine.bridge.error_received.connect(self._on_error)
        self.paper_engine.bridge.account_summary_updated.connect(self.account_bar.update_account)
        self.paper_engine.bridge.pnl_updated.connect(self.account_bar.update_daily_pnl)

    # ── Connection ────────────────────────────────────────────────────

    def _on_connect(self):
        mode = self.symbol_bar.get_mode()
        self.statusBar().showMessage(f"正在连接 ({mode.value})...")

        # Connect in background thread
        def do_connect():
            success = self.ibkr_engine.connect(mode)
            if not success:
                self.ibkr_engine.bridge.error_received.emit(
                    -1, -1, f"连接失败 — 请确认 TWS/Gateway 已启动"
                )

        threading.Thread(target=do_connect, daemon=True).start()

    def _on_disconnect(self):
        self.account_bar.stop()
        self.option_chain.cleanup()
        self.ibkr_engine.disconnect()

    def _on_connected(self):
        mode = self.ibkr_engine.mode
        if mode == TradingMode.PAPER:
            self._active_engine = self.paper_engine
        else:
            self._active_engine = self.ibkr_engine

        self.symbol_bar.set_connected(True, mode)
        self.symbol_bar.set_engine(self.ibkr_engine)
        self.option_chain.set_engine(self._active_engine)
        self.price_ladder.set_engine(self._active_engine)
        self.position_panel.set_engine(self._active_engine)
        self.order_panel.set_engine(self._active_engine)
        self.account_bar.set_engine(self._active_engine)

        self.statusBar().showMessage(f"已连接 ({mode.value})")

        # Request account data
        self._active_engine.request_account_summary()
        self.ibkr_engine.request_positions()
        self.account_bar.start()

        # Auto-load option chain for current symbol
        self._load_option_chain(self._current_symbol)

    def _on_disconnected(self):
        self.symbol_bar.set_connected(False)
        self.account_bar.stop()
        self.statusBar().showMessage("已断开连接")

    def _on_account_summary_end(self):
        """After first account summary, request PnL (needs account name)."""
        self._active_engine.request_pnl()

    # ── Symbol / Mode ─────────────────────────────────────────────────

    def _on_symbol_changed(self, symbol: str):
        self._current_symbol = symbol
        if self.ibkr_engine.is_connected:
            self._load_option_chain(symbol)

    def _on_mode_changed(self, mode_text: str):
        """Handle mode change before connection."""
        mode = TradingMode.PAPER if mode_text == "Paper" else TradingMode.LIVE
        if self.ibkr_engine.is_connected:
            if mode == TradingMode.PAPER:
                self._active_engine = self.paper_engine
            else:
                self._active_engine = self.ibkr_engine
            self.option_chain.set_engine(self._active_engine)
            self.price_ladder.set_engine(self._active_engine)
            self.position_panel.set_engine(self._active_engine)
            self.order_panel.set_engine(self._active_engine)
            self.account_bar.set_engine(self._active_engine)

    def _on_reconnect_requested(self, mode_text: str):
        """Handle hot switch: disconnect and reconnect to different port."""
        mode = TradingMode.PAPER if mode_text == "Paper" else TradingMode.LIVE
        self.statusBar().showMessage(f"切换到 {mode.value} 模式...")
        self.symbol_bar.set_switching(True)
        self.account_bar.stop()

        def do_reconnect():
            success = self.ibkr_engine.reconnect(mode)
            if not success:
                self.ibkr_engine.bridge.error_received.emit(
                    -1, -1, f"重连失败 — 请确认 TWS/Gateway 已启动 ({mode.value})"
                )
                self.ibkr_engine.bridge.disconnected.emit()

        threading.Thread(target=do_reconnect, daemon=True).start()

    # ── Option Chain Loading ──────────────────────────────────────────

    def _load_option_chain(self, symbol: str):
        self.statusBar().showMessage(f"加载 {symbol} 期权链...")

        def do_load():
            try:
                print(f"[DEBUG] Loading option chain for {symbol}...", flush=True)
                expirations, strikes = self.ibkr_engine.request_option_chain(symbol)
                print(f"[DEBUG] Got {len(expirations)} expirations, {len(strikes)} strikes", flush=True)

                if not expirations or not strikes:
                    self.ibkr_engine.bridge.error_received.emit(
                        -1, -1, f"{symbol} 期权链为空"
                    )
                    return

                # Get stock price via a one-shot tick subscription
                stock_price = self._fetch_stock_price(symbol)
                print(f"[DEBUG] Stock price for {symbol}: {stock_price}", flush=True)

                # Update UI on main thread via signal
                self.ibkr_engine.bridge.chain_ready.emit(expirations, strikes)
                # Store for the callback
                self._pending_chain = (symbol, expirations, strikes, stock_price)
            except Exception as e:
                print(f"[DEBUG] Option chain error: {e}", flush=True)
                self.ibkr_engine.bridge.error_received.emit(-1, -1, str(e))

        # Connect chain_ready to update (one-shot)
        try:
            self.ibkr_engine.bridge.chain_ready.disconnect()
        except TypeError:
            pass

        def on_chain_ready(expirations, strikes):
            data = getattr(self, '_pending_chain', None)
            if data:
                sym, exps, stks, price = data
                print(f"[DEBUG] on_chain_ready: {sym}, price={price}, "
                      f"{len(exps)} exp, {len(stks)} strikes", flush=True)
                self.option_chain.load_chain(sym, exps, stks, stock_price=price)
                self.statusBar().showMessage(
                    f"{sym} 期权链已加载: {len(exps)} 个到期日, "
                    f"{len(stks)} 个行权价 (股价=${price:.2f})"
                )
            try:
                self.ibkr_engine.bridge.chain_ready.disconnect(on_chain_ready)
            except TypeError:
                pass

        self.ibkr_engine.bridge.chain_ready.connect(on_chain_ready)
        threading.Thread(target=do_load, daemon=True).start()

    def _fetch_stock_price(self, symbol: str) -> float:
        """Fetch current stock price via a short-lived tick subscription."""
        import time
        from ibapi.contract import Contract

        contract = IBKREngine._make_underlying_contract(symbol)
        app = self.ibkr_engine._app
        req_id = app.next_req_id()
        key = f"__stock__{symbol}"
        app._tick_req_to_key[req_id] = key
        app._tick_data[key] = {"bid": 0.0, "ask": 0.0, "last": 0.0}
        app.reqMktData(req_id, contract, "", False, False, [])

        # Wait up to 5 seconds for a price
        for _ in range(50):
            time.sleep(0.1)
            d = app._tick_data.get(key, {})
            if d.get("last", 0) > 0 or d.get("bid", 0) > 0:
                break

        try:
            app.cancelMktData(req_id)
        except Exception:
            pass
        app._tick_req_to_key.pop(req_id, None)

        d = app._tick_data.pop(key, {})
        last = d.get("last", 0)
        bid = d.get("bid", 0)
        ask = d.get("ask", 0)
        if last > 0:
            return last
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
        if bid > 0:
            return bid
        if ask > 0:
            return ask
        return 0.0

    # ── Option Selected ───────────────────────────────────────────────

    def _on_option_selected(self, option: OptionInfo):
        self._current_option = option
        self.price_ladder.set_option(option)
        self.symbol_bar.set_current_option(option.display_name)
        self.statusBar().showMessage(f"已选择: {option.display_name}")

    # ── Contract Search (from price ladder search bar) ────────────────

    def _on_contract_searched(self, option: OptionInfo):
        """Handle contract search — validate contract exists before loading."""
        if not self.ibkr_engine.is_connected:
            self.statusBar().showMessage("未连接 — 无法搜索合约")
            return

        self.statusBar().showMessage(f"验证合约: {option.display_name}...")
        self.price_ladder.contract_label.setText(f"验证中: {option.display_name}...")

        def do_validate():
            try:
                contract = IBKREngine._make_option_contract(
                    option.symbol, option.expiry, option.strike, option.right
                )
                app = self.ibkr_engine._app
                req_id = app.next_req_id()
                app._contract_data[req_id] = {
                    "details": [], "event": threading.Event(), "error": None,
                }
                app.reqContractDetails(req_id, contract)

                state = app._contract_data[req_id]
                if not state["event"].wait(timeout=5):
                    self.ibkr_engine.bridge.error_received.emit(
                        -1, -1, f"合约验证超时: {option.display_name}"
                    )
                    app._contract_data.pop(req_id, None)
                    return

                if state["error"] or not state["details"]:
                    self.ibkr_engine.bridge.error_received.emit(
                        -1, -1, f"合约不存在: {option.display_name}"
                    )
                    app._contract_data.pop(req_id, None)
                    return

                app._contract_data.pop(req_id, None)
                # Valid — notify GUI thread
                self._search_validated.emit(option)

            except Exception as e:
                self.ibkr_engine.bridge.error_received.emit(-1, -1, f"搜索错误: {e}")

        threading.Thread(target=do_validate, daemon=True).start()

    def _load_validated_contract(self, option: OptionInfo):
        """Load a validated searched contract into the price ladder."""
        self._current_option = option

        # Subscribe to tick data
        if self._active_engine and self.ibkr_engine.is_connected:
            key = option.to_ibkr_key()
            if not any(k == key for k in self.ibkr_engine._app._tick_req_to_key.values()):
                self._active_engine.subscribe_option_tick(option)

        self.price_ladder.set_option(option)
        self.symbol_bar.set_current_option(option.display_name)
        self.statusBar().showMessage(f"已加载: {option.display_name}")

    # ── Order Handling ────────────────────────────────────────────────

    def _on_order_requested(self, option: OptionInfo, action_str: str, price: float):
        action = OrderAction.BUY if action_str == "BUY" else OrderAction.SELL
        qty = self.price_ladder.get_quantity()

        # Live mode confirmation (if not already confirmed by ladder checkbox)
        if self._active_engine.mode == TradingMode.LIVE and not self.price_ladder.confirm_checkbox.isChecked():
            action_text = "买入" if action == OrderAction.BUY else "卖出"
            reply = QMessageBox.question(
                self, "确认下单",
                f"确认在实盘 {action_text} {qty} 张\n"
                f"{option.display_name}\n"
                f"限价: ${price:.2f}？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        outside_rth = self.price_ladder.get_outside_rth()
        order_id = self._active_engine.place_limit_order(
            option, action, qty, price, outside_rth=outside_rth
        )
        if order_id > 0:
            action_text = "买入" if action == OrderAction.BUY else "卖出"
            rth_tag = " [盘外]" if outside_rth else ""
            self.statusBar().showMessage(
                f"已提交: {action_text} {qty}张 {option.display_name} @ ${price:.2f}{rth_tag}"
            )
            # Switch to order tab
            self.right_tabs.setCurrentIndex(1)

    def _on_market_order_requested(self, option: OptionInfo, action_str: str):
        """Handle market order from price ladder action buttons."""
        action = OrderAction.BUY if action_str == "BUY" else OrderAction.SELL
        qty = self.price_ladder.get_quantity()
        outside_rth = self.price_ladder.get_outside_rth()

        order_id = self._active_engine.place_market_order(
            option, action, qty, outside_rth=outside_rth
        )
        if order_id > 0:
            action_text = "市价买入" if action == OrderAction.BUY else "市价卖出"
            rth_tag = " [盘外]" if outside_rth else ""
            self.statusBar().showMessage(
                f"已提交: {action_text} {qty}张 {option.display_name}{rth_tag}"
            )
            self.right_tabs.setCurrentIndex(1)

    def _on_close_position_requested(self, option: OptionInfo):
        """Handle close position from price ladder."""
        outside_rth = self.price_ladder.get_outside_rth()
        order_id = self._active_engine.close_position(
            option, outside_rth=outside_rth
        )
        if order_id > 0:
            rth_tag = " [盘外]" if outside_rth else ""
            self.statusBar().showMessage(f"已提交平仓: {option.display_name}{rth_tag}")
            self.right_tabs.setCurrentIndex(1)

    def _on_cancel_all_requested(self):
        """Handle cancel all orders from price ladder."""
        self._active_engine.cancel_all_orders()
        self.statusBar().showMessage("已请求取消所有挂单")

    def _on_cancel_order(self, order_id: int):
        self._active_engine.cancel_order(order_id)
        self.statusBar().showMessage(f"已请求撤单: #{order_id}")

    # ── Currency Exchange ─────────────────────────────────────────────

    def _on_currency_exchange(self):
        """Open currency exchange dialog."""
        if not self.ibkr_engine.is_connected:
            QMessageBox.warning(self, "未连接", "请先连接到 IBKR")
            return
        dialog = CurrencyExchangeDialog(self._active_engine, self)
        dialog.exec_()

    # ── Chart Window ─────────────────────────────────────────────────

    def _on_open_chart(self):
        """Open a K-line chart window for the current symbol."""
        if not self.ibkr_engine.is_connected:
            QMessageBox.warning(self, "未连接", "请先连接到 IBKR")
            return

        chart = ChartWindow(
            engine=self.ibkr_engine,
            symbol=self._current_symbol,
            parent=None,  # independent window
        )
        self._chart_windows.append(chart)
        chart.destroyed.connect(lambda: self._chart_windows.remove(chart)
                                if chart in self._chart_windows else None)
        chart.show_and_load()

    # ── Session Indicator ────────────────────────────────────────────

    def _update_session_indicator(self):
        """Update the session status label based on current ET time."""
        try:
            import zoneinfo
            et = datetime.now(zoneinfo.ZoneInfo("America/New_York"))
        except Exception:
            # Fallback: assume local time is ET (close enough for display)
            et = datetime.now()

        h, m = et.hour, et.minute
        t = h * 60 + m  # minutes since midnight

        gth_start = SPX_SESSION_GTH_START[0] * 60 + SPX_SESSION_GTH_START[1]  # 20:15 = 1215
        gth_end = SPX_SESSION_GTH_END[0] * 60 + SPX_SESSION_GTH_END[1]        # 09:15 = 555
        rth_start = SPX_SESSION_RTH_START[0] * 60 + SPX_SESSION_RTH_START[1]  # 09:30 = 570
        rth_end = SPX_SESSION_RTH_END[0] * 60 + SPX_SESSION_RTH_END[1]        # 16:15 = 975

        weekday = et.weekday()  # 0=Mon ... 6=Sun
        is_weekend = weekday >= 5

        if is_weekend:
            session_text = "休市"
            session_color = COLOR_RED
        elif t >= rth_start and t < rth_end:
            session_text = "RTH 正常盘"
            session_color = COLOR_GREEN
        elif t >= gth_start or t < gth_end:
            session_text = "GTH 夜盘"
            session_color = COLOR_ACCENT
        elif t >= gth_end and t < rth_start:
            session_text = "盘前过渡"
            session_color = "#ff9800"
        elif t >= rth_end and t < gth_start:
            session_text = "盘后"
            session_color = "#ff9800"
        else:
            session_text = "休市"
            session_color = COLOR_RED

        time_str = et.strftime("%H:%M ET")
        self._session_label.setText(f"{session_text} {time_str}")
        self._session_label.setStyleSheet(
            f"color: {session_color}; background-color: {COLOR_BG_PANEL}; "
            f"border: 1px solid {COLOR_BORDER}; padding: 2px 10px; "
            f"border-radius: 3px; font-size: 12px; font-weight: bold;"
        )

    # ── Error Handling ────────────────────────────────────────────────

    def _on_error(self, req_id: int, code: int, msg: str):
        self.statusBar().showMessage(f"错误 [{code}]: {msg}")
        # Reset "验证中" state in price ladder on search error
        if "验证中" in self.price_ladder.contract_label.text():
            self.price_ladder.contract_label.setText("选择期权以开始")

    # ── Cleanup ───────────────────────────────────────────────────────

    def closeEvent(self, event):
        # Stop session timer
        self._session_timer.stop()

        # Close all chart windows
        for chart in list(self._chart_windows):
            chart.cleanup()
            chart.close()
        self._chart_windows.clear()

        self.account_bar.cleanup()
        self.option_chain.cleanup()
        self.price_ladder.cleanup()
        self.position_panel.cleanup()
        self.order_panel.cleanup()
        self.ibkr_engine.disconnect()
        event.accept()
