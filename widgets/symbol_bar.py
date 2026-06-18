"""Top bar: symbol search with autocomplete + connection status + mode switch."""

from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
    QLabel, QPushButton, QComboBox, QMessageBox, QAbstractItemView,
)
from PyQt5.QtCore import pyqtSignal, Qt, QTimer, QEvent
from PyQt5.QtGui import QFont

from config import (
    DEFAULT_SYMBOLS, COLOR_GREEN, COLOR_RED, COLOR_ACCENT, COLOR_TEXT,
    COLOR_BG_DARK, COLOR_BORDER, COLOR_BG_PANEL, COLOR_TEXT_DIM,
)
from models import TradingMode


class SymbolBar(QWidget):
    """Top toolbar with symbol search, mode switch, connection status."""

    symbol_changed = pyqtSignal(str)
    mode_changed = pyqtSignal(str)  # TradingMode.value: "Paper"/"IBKRPaper"/"Live"
    connect_clicked = pyqtSignal()
    disconnect_clicked = pyqtSignal()
    reconnect_requested = pyqtSignal(str)  # mode value — hot switch while connected

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected = False
        self._engine = None
        self._build_ui()

        # Debounce timer for search
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._do_search)

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        # Symbol search input
        layout.addWidget(QLabel("标的:"))

        self.symbol_input = QLineEdit()
        self.symbol_input.setPlaceholderText("输入标的代码...")
        self.symbol_input.setText("SPY")
        self.symbol_input.setMinimumWidth(120)
        self.symbol_input.setMaximumWidth(180)
        self.symbol_input.textChanged.connect(self._on_text_changed)
        self.symbol_input.returnPressed.connect(self._on_enter_pressed)
        # Hide search popup when the input loses focus (stale async results
        # would otherwise pop up the window while the user is trading)
        self.symbol_input.installEventFilter(self)
        self.symbol_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {COLOR_BG_DARK};
                color: {COLOR_TEXT};
                border: 1px solid {COLOR_BORDER};
                padding: 4px 8px;
                border-radius: 3px;
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border: 1px solid {COLOR_ACCENT};
            }}
        """)
        layout.addWidget(self.symbol_input)

        # Floating popup for search results
        self.symbol_popup = QListWidget()
        self.symbol_popup.setWindowFlags(Qt.ToolTip)
        self.symbol_popup.setFocusPolicy(Qt.NoFocus)
        self.symbol_popup.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.symbol_popup.setMaximumHeight(200)
        self.symbol_popup.setStyleSheet(f"""
            QListWidget {{
                background-color: {COLOR_BG_DARK};
                color: {COLOR_TEXT};
                border: 1px solid {COLOR_ACCENT};
                font-size: 12px;
                outline: none;
            }}
            QListWidget::item {{
                padding: 4px 8px;
            }}
            QListWidget::item:hover {{
                background-color: {COLOR_BG_PANEL};
            }}
            QListWidget::item:selected {{
                background-color: {COLOR_BG_PANEL};
                color: {COLOR_ACCENT};
            }}
        """)
        self.symbol_popup.itemClicked.connect(self._on_popup_item_clicked)
        self.symbol_popup.hide()

        layout.addSpacing(20)

        # Mode selector — 三种模式, item 文本为中文, data 为 TradingMode.value
        self.mode_combo = QComboBox()
        for mode in (TradingMode.PAPER, TradingMode.IBKR_PAPER, TradingMode.LIVE):
            self.mode_combo.addItem(mode.label, mode.value)
        self.mode_combo.setMinimumWidth(110)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        layout.addWidget(QLabel("模式:"))
        layout.addWidget(self.mode_combo)

        layout.addSpacing(20)

        # Connect button
        self.connect_btn = QPushButton("连接")
        self.connect_btn.setMinimumWidth(80)
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        self.connect_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_ACCENT};
                color: white;
                border: none;
                padding: 4px 12px;
                border-radius: 3px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #0097a7;
            }}
        """)
        layout.addWidget(self.connect_btn)

        # Connection status
        self.status_label = QLabel("● 未连接")
        self.status_label.setStyleSheet(f"color: {COLOR_RED}; font-weight: bold;")
        layout.addWidget(self.status_label)

        layout.addStretch()

        # Current symbol display
        self.current_symbol_label = QLabel("")
        self.current_symbol_label.setStyleSheet(
            f"color: {COLOR_ACCENT}; font-size: 14px; font-weight: bold;"
        )
        layout.addWidget(self.current_symbol_label)

    # ── Engine integration ────────────────────────────────────────────

    def set_engine(self, engine):
        """Connect to IBKR engine for symbol search."""
        self._engine = engine
        engine.bridge.symbol_search_results.connect(self._on_search_results)

    # ── Search logic ──────────────────────────────────────────────────

    def _on_text_changed(self, text: str):
        text = text.strip()
        if len(text) < 1:
            self.symbol_popup.hide()
            return
        self._search_timer.start()

    def _do_search(self):
        text = self.symbol_input.text().strip().upper()
        if not text:
            self.symbol_popup.hide()
            return

        if self._engine and self._connected:
            # Use IBKR API search
            self._engine.search_symbols(text)
        else:
            # Fallback: filter DEFAULT_SYMBOLS locally
            matches = [s for s in DEFAULT_SYMBOLS if text in s.upper()]
            self._show_popup([(s, "STK" if s not in ("SPX",) else "IND", "") for s in matches])

    def _on_search_results(self, results: list):
        """Handle search results from IBKR API."""
        self._show_popup(results)

    def eventFilter(self, obj, event):
        if obj is self.symbol_input and event.type() == QEvent.FocusOut:
            # Small delay so a click on a popup item still registers
            QTimer.singleShot(150, self.symbol_popup.hide)
        return super().eventFilter(obj, event)

    def _show_popup(self, results: list):
        """Show popup with search results. results: list of (symbol, secType, description)."""
        self.symbol_popup.clear()
        if not results:
            self.symbol_popup.hide()
            return

        # Results arrive async from IBKR (sometimes seconds later) — only
        # show if the user is still in the search box
        if not self.symbol_input.hasFocus():
            return

        for symbol, sec_type, desc in results:
            display = f"{symbol}  —  {sec_type}"
            if desc:
                display += f"  —  {desc}"
            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, symbol)
            self.symbol_popup.addItem(item)

        # Position popup below input
        pos = self.symbol_input.mapToGlobal(self.symbol_input.rect().bottomLeft())
        self.symbol_popup.setFixedWidth(max(self.symbol_input.width(), 300))
        self.symbol_popup.move(pos)
        self.symbol_popup.show()

    def _on_popup_item_clicked(self, item: QListWidgetItem):
        symbol = item.data(Qt.UserRole)
        self.symbol_input.blockSignals(True)
        self.symbol_input.setText(symbol)
        self.symbol_input.blockSignals(False)
        self.symbol_popup.hide()
        self.symbol_changed.emit(symbol)

    def _on_enter_pressed(self):
        symbol = self.symbol_input.text().strip().upper()
        if symbol:
            self.symbol_input.setText(symbol)
            self.symbol_popup.hide()
            self.symbol_changed.emit(symbol)

    # ── Mode / Connection ─────────────────────────────────────────────

    def _on_mode_changed(self, _index=0):
        mode_value = self.mode_combo.currentData()  # "Paper"/"IBKRPaper"/"Live"
        if self._connected:
            if mode_value == TradingMode.LIVE.value:
                reply = QMessageBox.question(
                    self, "切换到实盘",
                    "确认切换到实盘模式？\n将断开当前连接并重新连接到实盘端口 (真实资金)。",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    self.mode_combo.blockSignals(True)
                    self.mode_combo.setCurrentIndex(0)  # 退回本地模拟
                    self.mode_combo.blockSignals(False)
                    return
            self.reconnect_requested.emit(mode_value)
        else:
            self.mode_changed.emit(mode_value)

    def _on_connect_clicked(self):
        if self.connect_btn.text() == "连接":
            self.connect_clicked.emit()
        else:
            self.disconnect_clicked.emit()

    def set_connected(self, connected: bool, mode: TradingMode = TradingMode.PAPER):
        self._connected = connected
        if connected:
            self.status_label.setText(f"● 已连接 ({mode.label})")
            self.status_label.setStyleSheet(f"color: {COLOR_GREEN}; font-weight: bold;")
            self.connect_btn.setText("断开")
            self.connect_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_RED};
                    color: white;
                    border: none;
                    padding: 4px 12px;
                    border-radius: 3px;
                    font-weight: bold;
                }}
                QPushButton:hover {{ background-color: #d50000; }}
            """)
        else:
            self.status_label.setText("● 未连接")
            self.status_label.setStyleSheet(f"color: {COLOR_RED}; font-weight: bold;")
            self.connect_btn.setText("连接")
            self.connect_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_ACCENT};
                    color: white;
                    border: none;
                    padding: 4px 12px;
                    border-radius: 3px;
                    font-weight: bold;
                }}
                QPushButton:hover {{ background-color: #0097a7; }}
            """)

    def set_switching(self, switching: bool):
        """Disable controls during mode transition."""
        self.mode_combo.setEnabled(not switching)
        self.connect_btn.setEnabled(not switching)
        self.symbol_input.setEnabled(not switching)
        if switching:
            self.status_label.setText("● 切换中...")
            self.status_label.setStyleSheet(f"color: {COLOR_ACCENT}; font-weight: bold;")

    def set_current_option(self, text: str):
        self.current_symbol_label.setText(text)

    def set_symbol(self, symbol: str):
        """外部跳转标的时同步输入框显示 (不触发搜索弹窗)。"""
        self.symbol_input.blockSignals(True)
        self.symbol_input.setText(symbol)
        self.symbol_input.blockSignals(False)
        self.symbol_popup.hide()

    def get_symbol(self) -> str:
        return self.symbol_input.text().strip().upper()

    def get_mode(self) -> TradingMode:
        return TradingMode(self.mode_combo.currentData())
