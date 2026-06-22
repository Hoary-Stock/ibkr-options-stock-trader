"""Price Ladder — Futu-style 5-column order book with depth bars.

Layout (top to bottom):
  - "点价交易" title tab
  - Contract search bar + clear button + quantity selector (- N +)
  - Contract display label
  - Confirm checkbox
  - Position summary row
  - Action buttons: 市价买入 | 市价卖出 | 市价平仓 | 取消所有订单
  - Column headers: 我的买单 | 买入量 | 价格 | 卖出量 | 我的卖单
  - Scrollable order book rows with depth bar visualization
"""

import re

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QScrollArea, QFrame,
    QLineEdit, QCheckBox, QMessageBox, QSpinBox,
)
from PyQt5.QtCore import pyqtSignal, Qt, QTimer
from PyQt5.QtGui import QColor, QPainter, QBrush, QFont

from config import (
    TICK_SIZE_SMALL, TICK_SIZE_LARGE, TICK_THRESHOLD, TICK_SIZE_OVERRIDES,
    LADDER_ROWS, COLOR_BUY, COLOR_SELL, COLOR_TEXT, COLOR_TEXT_DIM,
    COLOR_BUTTON_DISABLED, COLOR_BG_DARK, COLOR_BORDER, COLOR_BG,
    COLOR_BG_PANEL, COLOR_GREEN, COLOR_RED, COLOR_ACCENT,
    COLOR_DEPTH_BID, COLOR_DEPTH_ASK, COLOR_MY_ORDER, FUTURES_SPECS,
)
from models import OptionInfo, OrderAction


def parse_option_string(text: str) -> OptionInfo | None:
    """Parse strings like 'TSLA260610P385000' or 'SPY260610C590000' into OptionInfo."""
    text = text.strip().upper()
    # Pattern: SYMBOL YYMMDD [C/P] STRIKE (strike as integer, cents implied by last 3 digits)
    m = re.match(r'^([A-Z]+)(\d{6})([CP])(\d+)$', text)
    if not m:
        return None
    symbol = m.group(1)
    expiry = "20" + m.group(2)  # YYMMDD -> YYYYMMDD
    right = m.group(3)
    strike_raw = m.group(4)
    # OCC format: last 3 digits are decimal portion (e.g. 385000 -> 385.000)
    if len(strike_raw) >= 4:
        strike = int(strike_raw) / 1000
    else:
        strike = float(strike_raw)
    return OptionInfo(symbol=symbol, expiry=expiry, strike=strike, right=right)


# Shared, immutable paint resources for the depth bars. Built once on first
# paint (after QApplication exists) instead of allocating new QColor/QFont
# objects on every paintEvent across ~400 depth-bar widgets.
_DEPTH_PALETTE: dict = {}


def _depth_palette() -> dict:
    if not _DEPTH_PALETTE:
        _DEPTH_PALETTE.update({
            "bg_dark":        QColor(COLOR_BG_DARK),
            "bid_hl_bg":      QColor("#1a3a2a"),
            "ask_hl_bg":      QColor("#3a1a1a"),
            "bid_bar":        QColor(COLOR_DEPTH_BID),
            "ask_bar":        QColor(COLOR_DEPTH_ASK),
            "bid_bar_hl":     QColor("#2a8a4a"),
            "ask_bar_hl":     QColor("#8a2a2a"),
            "border":         QColor(COLOR_BORDER),
            "bid_text":       QColor(COLOR_GREEN),
            "ask_text":       QColor(COLOR_RED),
            "bid_text_hl":    QColor("#00ff88"),
            "ask_text_hl":    QColor("#ff6666"),
            "font":           QFont("Segoe UI", 10),
        })
    return _DEPTH_PALETTE


class DepthBarWidget(QWidget):
    """Custom widget that paints a proportional colored bar behind size text.
    Clickable — bid side click = BUY, ask side click = SELL.
    """

    clicked = pyqtSignal()

    def __init__(self, side: str = "bid", parent=None):
        super().__init__(parent)
        self._side = side  # "bid" or "ask"
        self._size = 0
        self._max_size = 1
        self._text = ""
        self.setFixedHeight(26)
        self.setMinimumWidth(60)
        self.setCursor(Qt.PointingHandCursor)
        self._highlighted = False  # True when this is at the current bid/ask level
        # Prevent Qt from erasing to parent background before paintEvent.
        # Without this, highlighted (green/red) bars flash dark on every repaint.
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def set_highlighted(self, highlighted: bool):
        """Highlight this depth bar when it's at the current bid/ask price."""
        if self._highlighted != highlighted:
            self._highlighted = highlighted
            self.update()

    def set_data(self, size: int, max_size: int):
        max_size = max(max_size, 1)
        text = str(size) if size > 0 else ""
        if self._size == size and self._max_size == max_size:
            return  # No change — skip repaint
        # Skip repaint for empty bars when only max_size changed —
        # they look identical (no bar, no text) regardless of max_size.
        if self._size == 0 and size == 0:
            self._max_size = max_size
            return
        self._size = size
        self._max_size = max_size
        self._text = text
        self.update()

    def paintEvent(self, event):
        pal = _depth_palette()
        is_bid = self._side == "bid"
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        h = self.height()

        # Background (brighter tint when highlighted = at current bid/ask level)
        if self._highlighted:
            bg = pal["bid_hl_bg"] if is_bid else pal["ask_hl_bg"]
        else:
            bg = pal["bg_dark"]
        painter.fillRect(0, 0, w, h, bg)

        # Depth bar (brighter when highlighted)
        if self._size > 0 and self._max_size > 0:
            ratio = min(self._size / self._max_size, 1.0)
            bar_w = int(w * ratio)

            if is_bid:
                bar_color = pal["bid_bar_hl"] if self._highlighted else pal["bid_bar"]
                painter.fillRect(w - bar_w, 0, bar_w, h, bar_color)
            else:
                bar_color = pal["ask_bar_hl"] if self._highlighted else pal["ask_bar"]
                painter.fillRect(0, 0, bar_w, h, bar_color)

        # Cell border
        painter.setPen(pal["border"])
        painter.drawRect(0, 0, w - 1, h - 1)

        # Text (brighter when highlighted)
        if self._text:
            if is_bid:
                pen_color = pal["bid_text_hl"] if self._highlighted else pal["bid_text"]
            else:
                pen_color = pal["ask_text_hl"] if self._highlighted else pal["ask_text"]
            painter.setPen(pen_color)
            painter.setFont(pal["font"])
            painter.drawText(0, 0, w, h, Qt.AlignCenter, self._text)

        painter.end()


class PriceLadderRow(QWidget):
    """Single row in the Futu-style order book.

    Columns: [my_buy_qty] [bid_depth] [price] [ask_depth] [my_sell_qty]
    """

    price_left_clicked = pyqtSignal(float)   # Bid depth click = buy
    price_right_clicked = pyqtSignal(float)  # Ask depth click = sell

    def __init__(self, price: float, parent=None):
        super().__init__(parent)
        self.price = price
        self._is_bid = False
        self._is_ask = False
        self.setFixedHeight(26)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Column 1: My buy order qty
        self.my_buy_label = QLabel("")
        self.my_buy_label.setFixedWidth(70)
        self.my_buy_label.setFixedHeight(26)
        self.my_buy_label.setAlignment(Qt.AlignCenter)
        self.my_buy_label.setStyleSheet(
            f"color: {COLOR_MY_ORDER}; font-size: 11px; "
            f"background-color: {COLOR_BG_DARK}; "
            f"border: 1px solid {COLOR_BORDER};"
        )
        layout.addWidget(self.my_buy_label)

        # Column 2: Bid depth bar (click to BUY at this price)
        self.bid_depth = DepthBarWidget("bid")
        self.bid_depth.setFixedWidth(80)
        self.bid_depth.clicked.connect(lambda: self.price_left_clicked.emit(self.price))
        layout.addWidget(self.bid_depth)

        # Column 3: Price display (non-interactive)
        self.price_label = QLabel(f"{self.price:.2f}")
        self.price_label.setFixedSize(80, 26)
        self.price_label.setAlignment(Qt.AlignCenter)
        self.price_label.setStyleSheet(f"""
            QLabel {{
                background-color: {COLOR_BG};
                color: {COLOR_TEXT};
                border: 1px solid {COLOR_BORDER};
                font-size: 12px;
                font-weight: bold;
            }}
        """)
        layout.addWidget(self.price_label)

        # Column 4: Ask depth bar (click to SELL at this price)
        self.ask_depth = DepthBarWidget("ask")
        self.ask_depth.setFixedWidth(80)
        self.ask_depth.clicked.connect(lambda: self.price_right_clicked.emit(self.price))
        layout.addWidget(self.ask_depth)

        # Column 5: My sell order qty
        self.my_sell_label = QLabel("")
        self.my_sell_label.setFixedWidth(70)
        self.my_sell_label.setFixedHeight(26)
        self.my_sell_label.setAlignment(Qt.AlignCenter)
        self.my_sell_label.setStyleSheet(
            f"color: {COLOR_MY_ORDER}; font-size: 11px; "
            f"background-color: {COLOR_BG_DARK}; "
            f"border: 1px solid {COLOR_BORDER};"
        )
        layout.addWidget(self.my_sell_label)

    def set_bid_highlight(self, is_bid: bool):
        if self._is_bid == is_bid:
            return  # No change — skip stylesheet update
        self._is_bid = is_bid
        self.bid_depth.set_highlighted(is_bid)
        self._update_price_style()

    def set_ask_highlight(self, is_ask: bool):
        if self._is_ask == is_ask:
            return  # No change — skip stylesheet update
        self._is_ask = is_ask
        self.ask_depth.set_highlighted(is_ask)
        self._update_price_style()

    def _update_price_style(self):
        if self._is_ask:
            self.price_label.setStyleSheet(f"""
                QLabel {{
                    background-color: #3a2a00;
                    color: #ffff00;
                    border: 1px solid {COLOR_BORDER};
                    font-size: 12px;
                    font-weight: bold;
                }}
            """)
        elif self._is_bid:
            self.price_label.setStyleSheet(f"""
                QLabel {{
                    background-color: #003a3a;
                    color: #00e5ff;
                    border: 1px solid {COLOR_BORDER};
                    font-size: 12px;
                    font-weight: bold;
                }}
            """)
        else:
            self.price_label.setStyleSheet(f"""
                QLabel {{
                    background-color: {COLOR_BG};
                    color: {COLOR_TEXT};
                    border: 1px solid {COLOR_BORDER};
                    font-size: 12px;
                    font-weight: bold;
                }}
            """)

    def set_my_orders(self, buy_qty: int, sell_qty: int):
        buy_text = str(buy_qty) if buy_qty > 0 else ""
        sell_text = str(sell_qty) if sell_qty > 0 else ""
        if self.my_buy_label.text() != buy_text:
            self.my_buy_label.setText(buy_text)
        if self.my_sell_label.text() != sell_text:
            self.my_sell_label.setText(sell_text)

    def set_depth(self, bid_size: int, ask_size: int, max_bid: int, max_ask: int):
        self.bid_depth.set_data(bid_size, max_bid)
        self.ask_depth.set_data(ask_size, max_ask)


class PriceLadder(QWidget):
    """Futu-style price ladder with 5-column order book."""

    order_requested = pyqtSignal(object, str, float)  # OptionInfo, "BUY"/"SELL", price
    contract_searched = pyqtSignal(object)             # OptionInfo from search bar
    market_order_requested = pyqtSignal(object, str)   # OptionInfo, "BUY"/"SELL"
    close_position_requested = pyqtSignal(object)      # OptionInfo
    cancel_all_requested = pyqtSignal()
    detach_requested = pyqtSignal()                    # Detach into standalone window

    def __init__(self, parent=None):
        super().__init__(parent)
        self._option: OptionInfo | None = None
        self._rows: list[PriceLadderRow] = []
        self._engine = None
        self._quantity_fn = None  # callable returning int

        # Depth data (from market depth subscription)
        self._depth_bids: list[tuple[float, int]] = []  # [(price, size), ...] sorted desc
        self._depth_asks: list[tuple[float, int]] = []  # [(price, size), ...] sorted asc
        self._depth_available = False

        # Own tick subscription for the currently displayed option
        self._tick_req_id: int | None = None

        # Cache last known valid bid/ask to survive momentary data gaps
        self._last_bid = 0.0
        self._last_ask = 0.0

        self._build_ui()

        # Refresh timer
        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self._refresh)
        self._refresh_timer.start(200)

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # ── "点价交易" title tab ──
        title_layout = QHBoxLayout()
        title_layout.setContentsMargins(0, 0, 0, 0)

        self.title_tab = QLabel("点价交易")
        self.title_tab.setStyleSheet(f"""
            QLabel {{
                background-color: {COLOR_BG_PANEL};
                color: {COLOR_ACCENT};
                font-size: 13px;
                font-weight: bold;
                padding: 6px 16px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                border: 1px solid {COLOR_BORDER};
                border-bottom: none;
            }}
        """)
        title_layout.addWidget(self.title_tab)
        title_layout.addStretch()

        self.detach_btn = QPushButton("弹出")
        self.detach_btn.setFixedSize(48, 26)
        self.detach_btn.setCursor(Qt.PointingHandCursor)
        self.detach_btn.setToolTip("弹出为独立窗口，原位置显示K线图")
        self.detach_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_BG_PANEL};
                color: {COLOR_ACCENT};
                border: 1px solid {COLOR_BORDER};
                border-radius: 3px;
                font-size: 11px;
            }}
            QPushButton:hover {{ background-color: {COLOR_ACCENT}; color: white; }}
        """)
        self.detach_btn.clicked.connect(self.detach_requested.emit)
        title_layout.addWidget(self.detach_btn)

        main_layout.addLayout(title_layout)

        # ── Contract search bar + clear + quantity selector (- N +) ──
        search_layout = QHBoxLayout()
        search_layout.setSpacing(4)

        search_icon = QLabel("Q")
        search_icon.setFixedWidth(20)
        search_icon.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-weight: bold;")
        search_layout.addWidget(search_icon)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("TSLA260610P385000")
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {COLOR_BG_DARK};
                color: {COLOR_TEXT};
                border: 1px solid {COLOR_BORDER};
                padding: 4px 8px;
                border-radius: 3px;
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border-color: {COLOR_ACCENT};
            }}
        """)
        self.search_input.returnPressed.connect(self._on_search)
        search_layout.addWidget(self.search_input)

        # Clear button (x)
        self.clear_btn = QPushButton("x")
        self.clear_btn.setFixedSize(24, 24)
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {COLOR_TEXT_DIM};
                border: none;
                font-size: 13px;
                font-weight: bold;
            }}
            QPushButton:hover {{ color: {COLOR_TEXT}; }}
        """)
        self.clear_btn.clicked.connect(lambda: self.search_input.clear())
        search_layout.addWidget(self.clear_btn)

        # Quantity selector: - N +
        qty_minus_btn = QPushButton("-")
        qty_minus_btn.setFixedSize(28, 28)
        qty_minus_btn.setCursor(Qt.PointingHandCursor)
        qty_minus_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_BG_DARK};
                color: {COLOR_TEXT};
                border: 1px solid {COLOR_BORDER};
                border-radius: 3px;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {COLOR_BORDER}; }}
        """)
        qty_minus_btn.clicked.connect(self._qty_decrement)
        search_layout.addWidget(qty_minus_btn)

        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, 100)
        self.qty_spin.setValue(1)
        self.qty_spin.setFixedWidth(50)
        self.qty_spin.setAlignment(Qt.AlignCenter)
        self.qty_spin.setButtonSymbols(QSpinBox.NoButtons)
        self.qty_spin.setStyleSheet(f"""
            QSpinBox {{
                background-color: {COLOR_BG_DARK};
                color: {COLOR_TEXT};
                border: 1px solid {COLOR_BORDER};
                border-radius: 3px;
                font-size: 13px;
                font-weight: bold;
                padding: 2px;
            }}
        """)
        search_layout.addWidget(self.qty_spin)

        qty_plus_btn = QPushButton("+")
        qty_plus_btn.setFixedSize(28, 28)
        qty_plus_btn.setCursor(Qt.PointingHandCursor)
        qty_plus_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_BG_DARK};
                color: {COLOR_TEXT};
                border: 1px solid {COLOR_BORDER};
                border-radius: 3px;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {COLOR_BORDER}; }}
        """)
        qty_plus_btn.clicked.connect(self._qty_increment)
        search_layout.addWidget(qty_plus_btn)

        main_layout.addLayout(search_layout)

        # ── Contract display label ──
        self.contract_label = QLabel("选择期权以开始")
        self.contract_label.setStyleSheet(
            f"color: {COLOR_TEXT}; font-size: 13px; font-weight: bold; padding: 2px;"
        )
        main_layout.addWidget(self.contract_label)

        # ── Checkboxes row: confirm + outside RTH ──
        checkbox_layout = QHBoxLayout()
        checkbox_layout.setSpacing(12)

        self.no_confirm_checkbox = QCheckBox("免确认下单")
        self.no_confirm_checkbox.setChecked(False)
        self.no_confirm_checkbox.setToolTip(
            "勾选后点价/市价下单不弹出确认框，直接提交\n"
            "TWS 端: 请在 全局配置 → API → 设置 中\n"
            "勾选「Bypass Order Precautions for API Orders」"
        )
        self.no_confirm_checkbox.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 11px;")
        checkbox_layout.addWidget(self.no_confirm_checkbox)

        self.outside_rth_checkbox = QCheckBox("盘外交易 (GTH)")
        self.outside_rth_checkbox.setChecked(True)
        self.outside_rth_checkbox.setToolTip(
            "允许在盘前/盘后/夜盘 (GTH/Curb) 时段执行订单\n"
            "SPX 期权 GTH: 20:15-09:15 ET\n"
            "SPX 期权 RTH: 09:30-16:15 ET"
        )
        self.outside_rth_checkbox.setStyleSheet(
            f"color: {COLOR_ACCENT}; font-size: 11px; font-weight: bold;"
        )
        checkbox_layout.addWidget(self.outside_rth_checkbox)

        checkbox_layout.addStretch()
        main_layout.addLayout(checkbox_layout)

        # ── Position summary row ──
        pos_frame = QFrame()
        pos_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {COLOR_BG_DARK};
                border: 1px solid {COLOR_BORDER};
                border-radius: 3px;
            }}
        """)
        pos_layout = QHBoxLayout(pos_frame)
        pos_layout.setContentsMargins(8, 4, 8, 4)
        pos_layout.setSpacing(12)

        self.pos_qty_label = self._make_pos_label("持有数量", "0")
        self.pos_avg_label = self._make_pos_label("平均成本价", "--")
        self.pos_total_pnl_label = self._make_pos_label("净盈亏(含费)", "--")
        self.pos_unrealized_label = self._make_pos_label("手续费", "--")
        self.pos_today_label = self._make_pos_label("盈亏%", "--")

        for title_lbl, value_lbl in [
            self.pos_qty_label, self.pos_avg_label,
            self.pos_total_pnl_label, self.pos_unrealized_label,
            self.pos_today_label,
        ]:
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(title_lbl)
            col.addWidget(value_lbl)
            pos_layout.addLayout(col)

        pos_layout.addStretch()
        main_layout.addWidget(pos_frame)

        # ── Action buttons ──
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(4)

        self.market_buy_btn = QPushButton("市价买入")
        self.market_buy_btn.setFixedHeight(32)
        self.market_buy_btn.setCursor(Qt.PointingHandCursor)
        self.market_buy_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_BUY};
                color: white;
                border: none;
                border-radius: 3px;
                font-weight: bold;
                font-size: 12px;
            }}
            QPushButton:hover {{ background-color: #00e676; }}
            QPushButton:pressed {{ background-color: #00a040; }}
        """)
        self.market_buy_btn.clicked.connect(self._on_market_buy)
        btn_layout.addWidget(self.market_buy_btn)

        self.market_sell_btn = QPushButton("市价卖出")
        self.market_sell_btn.setFixedHeight(32)
        self.market_sell_btn.setCursor(Qt.PointingHandCursor)
        self.market_sell_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_SELL};
                color: white;
                border: none;
                border-radius: 3px;
                font-weight: bold;
                font-size: 12px;
            }}
            QPushButton:hover {{ background-color: #ff5252; }}
            QPushButton:pressed {{ background-color: #c60000; }}
        """)
        self.market_sell_btn.clicked.connect(self._on_market_sell)
        btn_layout.addWidget(self.market_sell_btn)

        self.close_pos_btn = QPushButton("市价平仓")
        self.close_pos_btn.setFixedHeight(32)
        self.close_pos_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_BUTTON_DISABLED};
                color: #666666;
                border: none;
                border-radius: 3px;
                font-size: 12px;
            }}
        """)
        self.close_pos_btn.setEnabled(False)
        self.close_pos_btn.clicked.connect(self._on_close_position)
        btn_layout.addWidget(self.close_pos_btn)

        self.cancel_all_btn = QPushButton("取消所有订单")
        self.cancel_all_btn.setFixedHeight(32)
        self.cancel_all_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_BG_DARK};
                color: {COLOR_TEXT};
                border: 1px solid {COLOR_BORDER};
                border-radius: 3px;
                font-size: 12px;
            }}
            QPushButton:hover {{ background-color: {COLOR_BORDER}; }}
        """)
        self.cancel_all_btn.clicked.connect(self._on_cancel_all)
        btn_layout.addWidget(self.cancel_all_btn)

        main_layout.addLayout(btn_layout)

        # ── Column headers ──
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(0)

        headers = [
            ("我的买单 0", 70),
            ("买入量", 80),
            ("价格", 80),
            ("卖出量", 80),
            ("我的卖单 0", 70),
        ]
        self._header_labels = []
        for text, width in headers:
            lbl = QLabel(text)
            lbl.setFixedWidth(width)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(
                f"color: {COLOR_TEXT_DIM}; font-size: 11px; "
                f"background-color: {COLOR_BG_DARK}; padding: 2px;"
            )
            header_layout.addWidget(lbl)
            self._header_labels.append(lbl)

        main_layout.addLayout(header_layout)

        # ── Scroll area for price rows ──
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setFrameShape(QFrame.NoFrame)

        self.rows_container = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(0)

        self.scroll_area.setWidget(self.rows_container)
        main_layout.addWidget(self.scroll_area)

    def _make_pos_label(self, title: str, value: str) -> tuple[QLabel, QLabel]:
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 10px; border: none;")
        title_lbl.setAlignment(Qt.AlignCenter)

        value_lbl = QLabel(value)
        value_lbl.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 12px; font-weight: bold; border: none;")
        value_lbl.setAlignment(Qt.AlignCenter)

        return title_lbl, value_lbl

    def set_engine(self, engine):
        self._engine = engine
        # Connect depth signal if available
        if hasattr(engine, 'bridge') and hasattr(engine.bridge, 'depth_updated'):
            try:
                engine.bridge.depth_updated.disconnect(self._on_depth_update)
            except (TypeError, RuntimeError):
                pass
            engine.bridge.depth_updated.connect(self._on_depth_update)

    def set_quantity_fn(self, fn):
        """Set a callable that returns current quantity (kept for compatibility)."""
        self._quantity_fn = fn

    def get_quantity(self) -> int:
        """Get current quantity from the integrated spinner."""
        return self.qty_spin.value()

    def _tick_sizes(self) -> tuple[float, float]:
        """当前合约的 (小价 tick, 大价 tick)。
        正股=penny; 期货按 FUTURES_SPECS 固定 tick; 指数期权用 overrides;
        其余期权用 penny-pilot 0.01/0.05。"""
        opt = self._option
        if opt is None:
            return TICK_SIZE_SMALL, TICK_SIZE_LARGE
        sym = opt.symbol.upper()
        if opt.right == "STK":
            return 0.01, 0.01
        if opt.right == "FUT":
            spec = FUTURES_SPECS.get(sym)
            t = spec[2] if spec else 0.25
            return t, t
        if sym in TICK_SIZE_OVERRIDES:
            return TICK_SIZE_OVERRIDES[sym]
        return TICK_SIZE_SMALL, TICK_SIZE_LARGE

    def _unit(self) -> str:
        """下单数量单位 (确认框文案): 期货=手, 正股=股, 期权=张。"""
        if self._option is None:
            return "张"
        return {"FUT": "手", "STK": "股"}.get(self._option.right, "张")

    def get_outside_rth(self) -> bool:
        """Whether orders should be allowed outside regular trading hours."""
        return self.outside_rth_checkbox.isChecked()

    def _qty_increment(self):
        self.qty_spin.setValue(self.qty_spin.value() + 1)

    def _qty_decrement(self):
        self.qty_spin.setValue(max(1, self.qty_spin.value() - 1))

    def set_option(self, option: OptionInfo):
        """Load a new option into the price ladder."""
        self._option = option
        self.contract_label.setText(option.display_name)
        self.search_input.setText("")

        # Reset depth data
        self._depth_bids.clear()
        self._depth_asks.clear()
        self._depth_available = False
        self._last_bid = 0.0
        self._last_ask = 0.0

        if self._engine:
            # Cancel previous tick subscription
            if self._tick_req_id is not None:
                self._engine.unsubscribe_tick(self._tick_req_id)
                self._tick_req_id = None

            # Subscribe to market depth
            self._engine.subscribe_market_depth(option)

            # Subscribe to tick data independently (don't rely on option chain)
            self._tick_req_id = self._engine.subscribe_option_tick(option)

        self._rebuild_ladder()

    def _rebuild_ladder(self):
        """Rebuild all price ladder rows centered on mid price."""
        # Clear existing rows
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()

        if not self._option:
            return

        # Determine tick size and center price
        mid = self._option.mid
        if mid <= 0:
            mid = self._option.ask if self._option.ask > 0 else self._option.bid
        if mid <= 0:
            mid = 1.0

        # Use instrument-specific tick size (SPX/futures use wider ticks).
        # Stocks always trade in pennies (the $3 threshold is options-only).
        ts, tl = self._tick_sizes()
        tick = ts if mid < TICK_THRESHOLD else tl

        # Generate price levels centered on mid
        half = LADDER_ROWS // 2
        center_tick = round(mid / tick)
        prices = []
        for i in range(half, -half - 1, -1):
            p = round((center_tick + i) * tick, 2)
            if p > 0:
                prices.append(p)

        # Build rows (high to low)
        for price in prices:
            row = PriceLadderRow(price)
            row.price_left_clicked.connect(lambda p: self._on_buy(p))
            row.price_right_clicked.connect(lambda p: self._on_sell(p))
            self._rows.append(row)
            self.rows_layout.addWidget(row)

        # Scroll to center
        QTimer.singleShot(100, self._scroll_to_center)

    def _scroll_to_center(self):
        if self._rows:
            mid_idx = len(self._rows) // 2
            if mid_idx < len(self._rows):
                self.scroll_area.ensureWidgetVisible(self._rows[mid_idx])

    def _on_depth_update(self, req_id: int, position: int, operation: int,
                         side: int, price: float, size: int):
        """Handle market depth updates. operation: 0=insert, 1=update, 2=delete."""
        self._depth_available = True

        if side == 1:  # Bid side
            target = self._depth_bids
        else:  # Ask side (side == 0)
            target = self._depth_asks

        if operation == 0:  # Insert
            if position >= len(target):
                target.append((price, size))
            else:
                target.insert(position, (price, size))
        elif operation == 1:  # Update
            if position < len(target):
                target[position] = (price, size)
        elif operation == 2:  # Delete
            if position < len(target):
                target.pop(position)

    def _refresh(self):
        """Update bid/ask highlights, depth, position summary, and button states."""
        if not self._option or not self._engine:
            return

        key = self._option.to_ibkr_key()
        tick = self._engine.get_tick(key)
        bid = tick.get("bid", 0)
        ask = tick.get("ask", 0)

        # Cache last known valid bid/ask (survive momentary data gaps)
        if bid > 0:
            self._last_bid = bid
        else:
            bid = self._last_bid
        if ask > 0:
            self._last_ask = ask
        else:
            ask = self._last_ask

        # Update option info
        self._option.bid = bid
        self._option.ask = ask
        self._option.last = tick.get("last", 0)

        # Auto re-center if current price is outside the visible ladder range
        if self._rows and (bid > 0 or ask > 0):
            mid = (bid + ask) / 2 if bid > 0 and ask > 0 else (bid or ask)
            top_price = self._rows[0].price
            bottom_price = self._rows[-1].price
            if mid > top_price or mid < bottom_price:
                self._rebuild_ladder()
                return

        # Build depth lookup from depth data, or fall back to tick data
        bid_depth_map: dict[float, int] = {}
        ask_depth_map: dict[float, int] = {}

        if self._depth_available:
            # Snapshot depth lists (they may be mutated by callbacks)
            for p, s in list(self._depth_bids):
                bid_depth_map[round(p, 2)] = s
            for p, s in list(self._depth_asks):
                ask_depth_map[round(p, 2)] = s

        # Determine current tick size for grid snapping
        ts, tl = self._tick_sizes()
        cur_mid = (bid + ask) / 2 if bid > 0 and ask > 0 else (bid or ask)
        cur_tick = ts if cur_mid < TICK_THRESHOLD else tl

        # Snap a price to the nearest tick grid point
        def snap(price: float) -> float:
            return round(round(price / cur_tick) * cur_tick, 2)

        # Always ensure current bid/ask from tick data are visible
        # (unconditional — overrides or fills in depth gaps)
        # Snap to tick grid so the key matches row.price exactly.
        bid_sz = tick.get("bid_size", 0)
        ask_sz = tick.get("ask_size", 0)
        if bid > 0:
            bid_key = snap(bid)
            bid_depth_map[bid_key] = max(bid_depth_map.get(bid_key, 0), bid_sz, 1)
        if ask > 0:
            ask_key = snap(ask)
            ask_depth_map[ask_key] = max(ask_depth_map.get(ask_key, 0), ask_sz, 1)

        max_bid = max((s for s in bid_depth_map.values()), default=1)
        max_ask = max((s for s in ask_depth_map.values()), default=1)

        # Check position for sell enable / position summary
        pos_qty = self._engine.get_position_qty(key)
        has_position = pos_qty > 0

        # Update close position button (guard to avoid redundant setStyleSheet)
        if has_position and not self.close_pos_btn.isEnabled():
            self.close_pos_btn.setEnabled(True)
            self.close_pos_btn.setCursor(Qt.PointingHandCursor)
            self.close_pos_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_BG_DARK};
                    color: {COLOR_TEXT};
                    border: 1px solid {COLOR_BORDER};
                    border-radius: 3px;
                    font-size: 12px;
                }}
                QPushButton:hover {{ background-color: {COLOR_BORDER}; }}
            """)
        elif not has_position and self.close_pos_btn.isEnabled():
            self.close_pos_btn.setEnabled(False)
            self.close_pos_btn.setCursor(Qt.ForbiddenCursor)
            self.close_pos_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_BUTTON_DISABLED};
                    color: #666666;
                    border: none;
                    border-radius: 3px;
                    font-size: 12px;
                }}
            """)

        # Update position summary
        self._update_position_summary(key, pos_qty)

        # Check for pending orders at each price
        pending_buy_at: dict[float, int] = {}
        pending_sell_at: dict[float, int] = {}
        total_buy_orders = 0
        total_sell_orders = 0
        for order in self._engine.orders.values():
            if (order.option.to_ibkr_key() == key and
                    order.status.value in ("PendingSubmit", "Submitted")):
                if order.action == OrderAction.BUY:
                    pending_buy_at[order.limit_price] = (
                        pending_buy_at.get(order.limit_price, 0) + order.quantity
                    )
                    total_buy_orders += order.quantity
                else:
                    pending_sell_at[order.limit_price] = (
                        pending_sell_at.get(order.limit_price, 0) + order.quantity
                    )
                    total_sell_orders += order.quantity

        # Update header labels with order counts
        self._header_labels[0].setText(f"我的买单 {total_buy_orders}")
        self._header_labels[4].setText(f"我的卖单 {total_sell_orders}")

        # Snap bid/ask to tick grid for highlight matching
        snapped_bid = snap(bid) if bid > 0 else 0
        snapped_ask = snap(ask) if ask > 0 else 0

        # Update each row
        for row in self._rows:
            p = round(row.price, 2)

            # Bid/Ask highlights (compare against grid-snapped prices)
            is_bid = snapped_bid > 0 and abs(p - snapped_bid) < 0.001
            is_ask = snapped_ask > 0 and abs(p - snapped_ask) < 0.001
            row.set_bid_highlight(is_bid)
            row.set_ask_highlight(is_ask)

            # Depth bars
            bid_sz = bid_depth_map.get(p, 0)
            ask_sz = ask_depth_map.get(p, 0)
            row.set_depth(bid_sz, ask_sz, max_bid, max_ask)

            # My order markers
            buy_qty = pending_buy_at.get(row.price, 0)
            sell_qty = pending_sell_at.get(row.price, 0)
            row.set_my_orders(buy_qty, sell_qty)

    def _update_position_summary(self, key: str, qty: int):
        """Update the position summary row."""
        _, qty_val = self.pos_qty_label
        qty_val.setText(str(qty))

        if qty > 0:
            # 持仓以 API 为准: get_position() (真实引擎读 reqPositions, 模拟引擎读本地撮合)
            pos = self._engine.get_position(key) if self._engine else None
            if pos:
                _, avg_val = self.pos_avg_label
                avg_val.setText(f"{pos.avg_price:.2f}")

                # Get current price
                tick = self._engine.get_tick(key)
                last = tick.get("last", 0)
                bid_price = tick.get("bid", 0)
                ask_price = tick.get("ask", 0)
                current = last if last > 0 else (
                    (bid_price + ask_price) / 2 if bid_price > 0 and ask_price > 0 else bid_price
                )
                if current > 0:
                    pos.current_price = current

                # Net P&L (including commissions)
                net_pnl = pos.net_pnl
                _, total_val = self.pos_total_pnl_label
                color = COLOR_GREEN if net_pnl >= 0 else COLOR_RED
                sign = "+" if net_pnl >= 0 else ""
                total_val.setText(f"{sign}{net_pnl:.2f}")
                total_val.setStyleSheet(
                    f"color: {color}; font-size: 12px; font-weight: bold; border: none;"
                )

                # Commission
                _, comm_val = self.pos_unrealized_label
                comm = pos.total_commission
                comm_val.setText(f"-{comm:.2f}")
                comm_val.setStyleSheet(
                    f"color: {COLOR_TEXT_DIM}; font-size: 12px; font-weight: bold; border: none;"
                )

                # Net P&L percentage
                _, pct_val = self.pos_today_label
                pct = pos.net_pnl_pct
                pct_color = COLOR_GREEN if pct >= 0 else COLOR_RED
                pct_sign = "+" if pct >= 0 else ""
                pct_val.setText(f"{pct_sign}{pct:.1f}%")
                pct_val.setStyleSheet(
                    f"color: {pct_color}; font-size: 12px; font-weight: bold; border: none;"
                )
                return

        # No position — reset
        _, avg_val = self.pos_avg_label
        avg_val.setText("--")
        avg_val.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 12px; font-weight: bold; border: none;")
        for label_pair in [self.pos_total_pnl_label, self.pos_unrealized_label, self.pos_today_label]:
            _, val = label_pair
            val.setText("--")
            val.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 12px; font-weight: bold; border: none;")

    # ── Search ─────────────────────────────────────────────────────────

    def _on_search(self):
        text = self.search_input.text().strip()
        if not text:
            return
        option = parse_option_string(text)
        if option:
            self.contract_searched.emit(option)
        else:
            self.search_input.setStyleSheet(f"""
                QLineEdit {{
                    background-color: {COLOR_BG_DARK};
                    color: {COLOR_RED};
                    border: 1px solid {COLOR_RED};
                    padding: 4px 8px;
                    border-radius: 3px;
                    font-size: 13px;
                }}
            """)
            QTimer.singleShot(1500, lambda: self.search_input.setStyleSheet(f"""
                QLineEdit {{
                    background-color: {COLOR_BG_DARK};
                    color: {COLOR_TEXT};
                    border: 1px solid {COLOR_BORDER};
                    padding: 4px 8px;
                    border-radius: 3px;
                    font-size: 13px;
                }}
                QLineEdit:focus {{ border-color: {COLOR_ACCENT}; }}
            """))

    # ── Action Buttons ─────────────────────────────────────────────────

    def _on_market_buy(self):
        if self._option:
            if not self.no_confirm_checkbox.isChecked():
                qty = self.get_quantity()
                reply = QMessageBox.question(
                    self, "确认市价买入",
                    f"确认市价买入 {qty} {self._unit()}\n{self._option.display_name}？",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return
            self.market_order_requested.emit(self._option, "BUY")

    def _on_market_sell(self):
        if self._option:
            if not self.no_confirm_checkbox.isChecked():
                qty = self.get_quantity()
                reply = QMessageBox.question(
                    self, "确认市价卖出",
                    f"确认市价卖出 {qty} {self._unit()}\n{self._option.display_name}？",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return
            self.market_order_requested.emit(self._option, "SELL")

    def _on_close_position(self):
        if self._option:
            key = self._option.to_ibkr_key()
            pos_qty = self._engine.get_position_qty(key) if self._engine else 0
            # Stock positions live in the portfolio (reqPositions), not the
            # engine's option tracking — let the window resolve the quantity
            if pos_qty <= 0 and self._option.right != "STK":
                return
            if not self.no_confirm_checkbox.isChecked():
                reply = QMessageBox.question(
                    self, "确认市价平仓",
                    f"确认市价平仓 {pos_qty} {self._unit()}\n{self._option.display_name}？",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return
            self.close_position_requested.emit(self._option)

    def _on_cancel_all(self):
        if not self.no_confirm_checkbox.isChecked():
            reply = QMessageBox.question(
                self, "确认取消",
                "确认取消所有挂单？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self.cancel_all_requested.emit()

    # ── Price Button Clicks ────────────────────────────────────────────

    def _on_buy(self, price: float):
        if self._option:
            if not self.no_confirm_checkbox.isChecked():
                qty = self.get_quantity()
                reply = QMessageBox.question(
                    self, "确认买入",
                    f"确认限价买入 {qty} {self._unit()}\n"
                    f"{self._option.display_name}\n"
                    f"价格: ${price:.2f}",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return
            self.order_requested.emit(self._option, "BUY", price)

    def _on_sell(self, price: float):
        if self._option:
            if not self.no_confirm_checkbox.isChecked():
                qty = self.get_quantity()
                reply = QMessageBox.question(
                    self, "确认卖出",
                    f"确认限价卖出 {qty} {self._unit()}\n"
                    f"{self._option.display_name}\n"
                    f"价格: ${price:.2f}",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return
            self.order_requested.emit(self._option, "SELL", price)

    def cleanup(self):
        self._refresh_timer.stop()
        if self._engine:
            self._engine.unsubscribe_market_depth()
            if self._tick_req_id is not None:
                self._engine.unsubscribe_tick(self._tick_req_id)
                self._tick_req_id = None
