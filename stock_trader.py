"""正股交易 client — 点价交易窗口 (与期权 GUI 同款设计).

与期权 GUI (main.py, clientId=10) 并行运行, 使用 clientId=11.
点价梯 (深度摆盘 + 点击下单) + K线图 + 正股持仓 (今日盈亏) + 委托管理.
"""

import sys
import os
import threading
from datetime import datetime

# Under pythonw there is no console — redirect output to the daily app log
if sys.stdout is None or sys.stderr is None:
    _log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(_log_dir, exist_ok=True)
    _log_file = open(
        os.path.join(_log_dir, f"stock_app_{datetime.now():%Y-%m-%d}.log"),
        "a", encoding="utf-8", buffering=1,
    )
    sys.stdout = sys.stderr = _log_file
    print(f"\n──── Stock trader started {datetime.now():%Y-%m-%d %H:%M:%S} ────")

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QMessageBox, QSplitter,
    QTabWidget,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QIcon

from single_instance import kill_previous_instances
from config import (
    IBKR_STOCK_CLIENT_ID, COLOR_BG, COLOR_BG_DARK, COLOR_TEXT,
    COLOR_BORDER, COLOR_ACCENT, COLOR_GREEN, COLOR_RED,
    INDEX_SYMBOLS,
)
from models import OptionInfo, OrderAction, OrderType, TradingMode
from ibkr_engine import IBKREngine
from widgets.price_ladder import PriceLadder
from widgets.position_panel import PositionPanel
from widgets.order_panel import OrderPanel
from widgets.currency_balance import CurrencyBalanceBar
from sound_alerts import play_fill
# ChartWindow imported lazily (pulls numpy + pyqtgraph)

APP_ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_app.ico")

STYLESHEET = f"""
    QMainWindow, QWidget {{
        background-color: {COLOR_BG};
        color: {COLOR_TEXT};
    }}
    QTableWidget {{
        background-color: {COLOR_BG_DARK};
        color: {COLOR_TEXT};
        gridline-color: {COLOR_BORDER};
        border: 1px solid {COLOR_BORDER};
    }}
    QHeaderView::section {{
        background-color: {COLOR_BG_DARK};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER};
        padding: 4px;
        font-weight: bold;
    }}
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background-color: {COLOR_BG_DARK};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER};
        padding: 4px;
        border-radius: 3px;
    }}
    QTabWidget::pane {{
        border: 1px solid {COLOR_BORDER};
        background-color: {COLOR_BG_DARK};
    }}
    QTabBar::tab {{
        background-color: {COLOR_BG_DARK};
        color: {COLOR_TEXT};
        padding: 6px 16px;
        border: 1px solid {COLOR_BORDER};
    }}
    QTabBar::tab:selected {{
        background-color: {COLOR_ACCENT};
        color: white;
    }}
    QLabel {{ border: none; }}
"""


def make_stock_pseudo(symbol: str) -> OptionInfo:
    """Pseudo contract (right='STK') so the price ladder can drive stocks."""
    return OptionInfo(symbol=symbol, expiry="", strike=0.0, right="STK")


class StockTraderWindow(QMainWindow):
    """Stock/ETF point-and-click (DOM) trading window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"IBKR 正股交易 (clientId={IBKR_STOCK_CLIENT_ID})")
        self.resize(1100, 760)

        self.engine = IBKREngine()
        self._symbol = ""
        self._chart_windows: list = []

        self._build_ui()
        self._connect_signals()
        self.setStyleSheet(STYLESHEET)

    # ── UI ────────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        layout = QVBoxLayout(central)

        # Top bar: connection + symbol + chart
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("模式:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Live", "Paper"])
        self.mode_combo.setFixedWidth(80)
        top_row.addWidget(self.mode_combo)
        self.connect_btn = QPushButton("连接")
        self.connect_btn.setFixedWidth(80)
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        top_row.addWidget(self.connect_btn)
        self.status_label = QLabel("● 未连接")
        self.status_label.setStyleSheet(f"color: {COLOR_RED}; font-weight: bold;")
        top_row.addWidget(self.status_label)

        top_row.addSpacing(24)
        top_row.addWidget(QLabel("标的:"))
        self.symbol_input = QLineEdit()
        self.symbol_input.setPlaceholderText("如 AAPL")
        self.symbol_input.setFixedWidth(100)
        self.symbol_input.returnPressed.connect(self._on_symbol_entered)
        top_row.addWidget(self.symbol_input)
        self.load_btn = QPushButton("加载")
        self.load_btn.setFixedWidth(60)
        self.load_btn.clicked.connect(self._on_symbol_entered)
        top_row.addWidget(self.load_btn)

        self.chart_btn = QPushButton("K线图")
        self.chart_btn.setFixedWidth(70)
        self.chart_btn.clicked.connect(self._on_open_chart)
        top_row.addWidget(self.chart_btn)

        top_row.addStretch()

        # Per-currency cash balances (EUR/USD/...)
        self.currency_bar = CurrencyBalanceBar()
        top_row.addWidget(self.currency_bar)

        layout.addLayout(top_row)

        # Main area: ladder (left) | positions + orders (right)
        splitter = QSplitter(Qt.Horizontal)

        self.price_ladder = PriceLadder()
        self.price_ladder.setMinimumWidth(420)
        splitter.addWidget(self.price_ladder)

        right_tabs = QTabWidget()
        self.position_panel = PositionPanel(default_filter="正股/ETF")
        right_tabs.addTab(self.position_panel, "持仓")
        self.order_panel = OrderPanel()
        right_tabs.addTab(self.order_panel, "委托")
        self.right_tabs = right_tabs
        splitter.addWidget(right_tabs)

        splitter.setSizes([460, 640])
        layout.addWidget(splitter, stretch=1)

        self.setCentralWidget(central)
        self.statusBar().showMessage("就绪 — 点击「连接」开始")

    def _connect_signals(self):
        b = self.engine.bridge
        b.connected.connect(self._on_connected)
        b.disconnected.connect(self._on_disconnected)
        b.error_received.connect(self._on_error)
        b.order_rejected.connect(self._on_order_rejected)
        b.portfolio_position_received.connect(self.position_panel.on_portfolio_position)
        b.portfolio_positions_end.connect(self.position_panel.on_portfolio_positions_end)
        b.pnl_single_updated.connect(self.position_panel.on_pnl_single)
        b.currency_balance_updated.connect(self.currency_bar.on_balance)
        b.execution_received.connect(lambda oid, side, q, p: play_fill(side))

        # Price ladder → orders
        self.price_ladder.order_requested.connect(self._on_order_requested)
        self.price_ladder.market_order_requested.connect(self._on_market_order)
        self.price_ladder.close_position_requested.connect(self._on_close_position)
        self.price_ladder.cancel_all_requested.connect(self._on_cancel_all)

        self.order_panel.cancel_requested.connect(self.engine.cancel_order)

    # ── Connection ────────────────────────────────────────────────────

    def _on_connect_clicked(self):
        if self.connect_btn.text() == "断开":
            self.engine.disconnect()
            return

        mode = (TradingMode.LIVE if self.mode_combo.currentText() == "Live"
                else TradingMode.PAPER)
        self.statusBar().showMessage(f"正在连接 ({mode.value})...")

        def do_connect():
            ok = self.engine.connect(mode, client_id=IBKR_STOCK_CLIENT_ID)
            if not ok:
                self.engine.bridge.error_received.emit(
                    -1, -1, "连接失败 — 请确认 TWS 已启动"
                )

        threading.Thread(target=do_connect, daemon=True).start()

    def _on_connected(self):
        self.status_label.setText("● 已连接")
        self.status_label.setStyleSheet(f"color: {COLOR_GREEN}; font-weight: bold;")
        self.connect_btn.setText("断开")
        self.statusBar().showMessage("已连接")
        self.position_panel.set_engine(self.engine)
        self.order_panel.set_engine(self.engine)
        self.price_ladder.set_engine(self.engine)
        self.engine.request_account_summary()  # needed for reqPnLSingle account name
        self.engine.request_currency_balances()  # 各币种现金余额 (流式更新)
        self.engine.request_positions()
        # Re-subscribe the ladder if a symbol was already loaded
        if self._symbol:
            self.price_ladder.set_option(make_stock_pseudo(self._symbol))

    def _on_disconnected(self):
        self.status_label.setText("● 未连接")
        self.status_label.setStyleSheet(f"color: {COLOR_RED}; font-weight: bold;")
        self.connect_btn.setText("连接")
        self.statusBar().showMessage("已断开连接")
        self.currency_bar.clear_balances()

    # ── Symbol ────────────────────────────────────────────────────────

    def _on_symbol_entered(self):
        symbol = self.symbol_input.text().strip().upper()
        if not symbol:
            return
        if symbol in INDEX_SYMBOLS:
            QMessageBox.warning(self, "无法交易", f"{symbol} 是指数, 不能直接交易正股")
            return
        if not self.engine.is_connected:
            self.statusBar().showMessage("未连接 — 无法订阅行情")
            return

        self._symbol = symbol
        self.symbol_input.setText(symbol)
        self.price_ladder.set_option(make_stock_pseudo(symbol))
        self.statusBar().showMessage(f"已加载: {symbol}")

    # ── Chart ─────────────────────────────────────────────────────────

    def _on_open_chart(self):
        if not self.engine.is_connected:
            QMessageBox.warning(self, "未连接", "请先连接到 TWS")
            return
        if not self._symbol:
            QMessageBox.warning(self, "未选标的", "请先输入并加载标的")
            return

        from widgets.chart_window import ChartWindow

        chart = ChartWindow(engine=self.engine, symbol=self._symbol, parent=None)
        self._chart_windows.append(chart)
        chart.destroyed.connect(lambda: self._chart_windows.remove(chart)
                                if chart in self._chart_windows else None)
        chart.show_and_load()

    # ── Orders (from price ladder) ────────────────────────────────────

    def _on_order_requested(self, option: OptionInfo, action_str: str, price: float):
        action = OrderAction.BUY if action_str == "BUY" else OrderAction.SELL
        qty = self.price_ladder.get_quantity()
        order_id = self.engine.place_stock_order(
            option.symbol, action, qty, price,
            order_type=OrderType.LIMIT,
            outside_rth=self.price_ladder.get_outside_rth(),
        )
        if order_id > 0:
            action_text = "买入" if action == OrderAction.BUY else "卖出"
            self.statusBar().showMessage(
                f"已提交: {action_text} {qty}股 {option.symbol} @ ${price:.2f}"
            )
            self.right_tabs.setCurrentIndex(1)

    def _on_market_order(self, option: OptionInfo, action_str: str):
        action = OrderAction.BUY if action_str == "BUY" else OrderAction.SELL
        qty = self.price_ladder.get_quantity()
        order_id = self.engine.place_stock_order(
            option.symbol, action, qty, 0.0,
            order_type=OrderType.MARKET,
            outside_rth=self.price_ladder.get_outside_rth(),
        )
        if order_id > 0:
            action_text = "市价买入" if action == OrderAction.BUY else "市价卖出"
            self.statusBar().showMessage(f"已提交: {action_text} {qty}股 {option.symbol}")
            self.right_tabs.setCurrentIndex(1)

    def _get_stock_position_qty(self, symbol: str) -> int:
        """Look up held quantity from IBKR portfolio positions."""
        pp = self.position_panel._portfolio_positions.get(f"{symbol}_STK")
        return int(pp.quantity) if pp else 0

    def _on_close_position(self, option: OptionInfo):
        qty = self._get_stock_position_qty(option.symbol)
        if qty <= 0:
            self.statusBar().showMessage(f"无 {option.symbol} 多头持仓可平")
            return
        order_id = self.engine.place_stock_order(
            option.symbol, OrderAction.SELL, qty, 0.0,
            order_type=OrderType.MARKET,
            outside_rth=self.price_ladder.get_outside_rth(),
        )
        if order_id > 0:
            self.statusBar().showMessage(f"已提交平仓: 市价卖出 {qty}股 {option.symbol}")
            self.right_tabs.setCurrentIndex(1)

    def _on_cancel_all(self):
        # NOTE: reqGlobalCancel cancels ALL orders account-wide,
        # including those placed by the options GUI
        self.engine.cancel_all_orders()
        self.statusBar().showMessage("已请求取消所有挂单 (全账户)")

    # ── Errors ────────────────────────────────────────────────────────

    def _on_error(self, req_id: int, code: int, msg: str):
        self.statusBar().showMessage(f"[{code}] {msg}")

    def _on_order_rejected(self, order_id: int, code: int, msg: str):
        self.statusBar().showMessage(f"⚠ 订单 #{order_id} 被拒绝 [{code}]: {msg}")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(f"订单被拒绝 #{order_id}")
        box.setText(f"IBKR 拒绝原因 [{code}]:\n{msg}")
        box.setWindowModality(Qt.NonModal)
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.show()

    def closeEvent(self, event):
        for chart in list(self._chart_windows):
            try:
                chart.close()
            except Exception:
                pass
        self.price_ladder.cleanup()
        self.position_panel.cleanup()
        self.order_panel.cleanup()
        if self.engine.is_connected:
            self.engine.disconnect()
        event.accept()


def main():
    # Kill leftover instances of THIS script (frees clientId in TWS);
    # the options GUI (main.py) is not touched
    kill_previous_instances(__file__)

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    if os.path.exists(APP_ICON):
        app.setWindowIcon(QIcon(APP_ICON))

    window = StockTraderWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
