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
import threading

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QScrollArea, QFrame,
    QLineEdit, QCheckBox, QMessageBox, QSpinBox, QDoubleSpinBox,
)
from PyQt5.QtCore import pyqtSignal, Qt, QTimer
from PyQt5.QtGui import QColor, QPainter, QBrush, QFont

from config import (
    TICK_SIZE_SMALL, TICK_SIZE_LARGE, TICK_THRESHOLD, TICK_SIZE_OVERRIDES,
    LADDER_ROWS, LADDER_ROW_HEIGHT, LADDER_EXTEND_CHUNK, LADDER_MAX_ROWS,
    COLOR_BUY, COLOR_SELL, COLOR_TEXT, COLOR_TEXT_DIM,
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
    conditional_requested = pyqtSignal(object)         # dict: 止盈/止损条件单请求
    conditional_cancel_requested = pyqtSignal(int)     # cond_id
    option_loaded = pyqtSignal()                       # set_option 后 (刷新条件单显示)

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
        # 换合约时把订阅/退订的 IBKR socket 调用放到后台线程, 避免 Gateway 繁忙时
        # socket.send 卡住 GUI 线程 (表现为切标的时窗口"未响应"灰屏)。代数计数器
        # 保证只有最近一次切换的订阅被保留, 快速连切不会泄漏行情线。
        self._sub_generation = 0

        # Cache last known valid bid/ask to survive momentary data gaps
        self._last_bid = 0.0
        self._last_ask = 0.0

        # 滚轮滚到边缘时自动扩展档位; 该标志在扩展/复位滚动条期间屏蔽重入
        self._extending = False

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

        # 「条件单」开关 (止盈/止损) —— 点开下方条件单面板
        self.cond_toggle_btn = QPushButton("条件单 ▾")
        self.cond_toggle_btn.setCheckable(True)
        self.cond_toggle_btn.setFixedHeight(24)
        self.cond_toggle_btn.setCursor(Qt.PointingHandCursor)
        self.cond_toggle_btn.setToolTip("挂止盈/止损条件单(到价才发限价单到 IBKR)")
        self.cond_toggle_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_BG_DARK}; color: {COLOR_ACCENT};
                border: 1px solid {COLOR_BORDER}; border-radius: 3px;
                padding: 2px 10px; font-size: 11px; font-weight: bold;
            }}
            QPushButton:checked {{ background-color: {COLOR_ACCENT}; color: white; }}
        """)
        self.cond_toggle_btn.toggled.connect(self._on_cond_toggle)
        checkbox_layout.addWidget(self.cond_toggle_btn)

        checkbox_layout.addStretch()
        main_layout.addLayout(checkbox_layout)

        # ── 条件单面板 (止盈/止损, 默认隐藏) ──
        self.cond_panel = self._build_cond_panel()
        self.cond_panel.setVisible(False)
        main_layout.addWidget(self.cond_panel)

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

        # 滚到顶/底边缘 → 自动向该方向扩展更多价格档 (够到远离现价的挂单价)
        self.scroll_area.verticalScrollBar().valueChanged.connect(
            self._on_ladder_scrolled
        )

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

    # ── 条件单 (止盈/止损) ────────────────────────────────────────────

    def _build_cond_panel(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{ background-color: {COLOR_BG_DARK}; "
            f"border: 1px solid {COLOR_ACCENT}; border-radius: 3px; }}"
        )
        v = QVBoxLayout(frame)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(4)

        self._cond_hint = QLabel("止盈/止损 (平多·SELL): 到价才发限价单到 IBKR")
        self._cond_hint.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 10px; border: none;")
        v.addWidget(self._cond_hint)

        # 两种条件单: ①「挂条件单」按钮 = 对**当前持仓**(按下面「数量」);
        #            ② 勾此项 = 用点价梯**买入**开仓时, 按**买入数量**自动附带同样的止盈/止损。
        self.attach_buy_check = QCheckBox("随买入单附带 (按买入数量自动挂)")
        self.attach_buy_check.setToolTip(
            "勾选: 用点价梯「买入/市价买入」开仓时, 自动按**买入数量**挂上方设定的止盈/止损。\n"
            "不勾选: 「挂条件单」按钮只对**当前持仓**按下面「数量」挂。\n"
            "(期货开多始终强制带止盈+止损, 无论此项是否勾选)"
        )
        self.attach_buy_check.setStyleSheet(
            f"color: {COLOR_ACCENT}; font-size: 11px; border: none;"
        )
        v.addWidget(self.attach_buy_check)

        def _price_spin():
            sp = QDoubleSpinBox()
            sp.setDecimals(2)
            sp.setRange(0.0, 1_000_000.0)
            sp.setSingleStep(0.01)
            sp.setFixedWidth(100)
            sp.setStyleSheet(
                f"QDoubleSpinBox {{ background-color: {COLOR_BG}; color: {COLOR_TEXT}; "
                f"border: 1px solid {COLOR_BORDER}; border-radius: 3px; padding: 2px; }}"
            )
            return sp

        # 止盈行
        tp_row = QHBoxLayout()
        self.tp_check = QCheckBox("止盈")
        self.tp_check.setStyleSheet(f"color: {COLOR_GREEN}; font-size: 11px; font-weight: bold; border: none;")
        tp_row.addWidget(self.tp_check)
        self._tp_price_label = QLabel("触发价")
        tp_row.addWidget(self._tp_price_label)
        self.tp_price_spin = _price_spin()
        tp_row.addWidget(self.tp_price_spin)
        tp_row.addStretch()
        v.addLayout(tp_row)

        # 止损行
        sl_row = QHBoxLayout()
        self.sl_check = QCheckBox("止损")
        self.sl_check.setStyleSheet(f"color: {COLOR_RED}; font-size: 11px; font-weight: bold; border: none;")
        sl_row.addWidget(self.sl_check)
        self._sl_price_label = QLabel("触发价")
        sl_row.addWidget(self._sl_price_label)
        self.sl_price_spin = _price_spin()
        sl_row.addWidget(self.sl_price_spin)
        sl_row.addStretch()
        v.addLayout(sl_row)

        # 数量 + 原生 + 挂单
        opt_row = QHBoxLayout()
        opt_row.addWidget(QLabel("数量"))
        self.cond_qty_spin = QSpinBox()
        self.cond_qty_spin.setRange(1, 1000)
        self.cond_qty_spin.setValue(1)
        self.cond_qty_spin.setFixedWidth(56)
        self.cond_qty_spin.setStyleSheet(
            f"QSpinBox {{ background-color: {COLOR_BG}; color: {COLOR_TEXT}; "
            f"border: 1px solid {COLOR_BORDER}; border-radius: 3px; padding: 2px; }}"
        )
        opt_row.addWidget(self.cond_qty_spin)

        self.cond_native_check = QCheckBox("用IBKR原生")
        self.cond_native_check.setToolTip(
            "勾选: 用 IBKR 原生 STP LMT 挂到服务器(关程序也有效, 但同合约反向挂单受 201 限制)。\n"
            "不勾选(默认): 本地监控, 到价才发单(可规避 201, 但仅程序运行时有效)。"
        )
        self.cond_native_check.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 11px; border: none;")
        opt_row.addWidget(self.cond_native_check)
        opt_row.addStretch()

        self.arm_btn = QPushButton("挂条件单")
        self.arm_btn.setFixedHeight(26)
        self.arm_btn.setCursor(Qt.PointingHandCursor)
        self.arm_btn.setStyleSheet(f"""
            QPushButton {{ background-color: {COLOR_ACCENT}; color: white; border: none;
                border-radius: 3px; font-weight: bold; font-size: 11px; padding: 2px 12px; }}
            QPushButton:hover {{ background-color: #0097a7; }}
        """)
        self.arm_btn.clicked.connect(self._on_arm_conditional)
        opt_row.addWidget(self.arm_btn)
        v.addLayout(opt_row)

        # 已挂条件单列表
        self.cond_list_container = QWidget()
        self.cond_list_layout = QVBoxLayout(self.cond_list_container)
        self.cond_list_layout.setContentsMargins(0, 0, 0, 0)
        self.cond_list_layout.setSpacing(2)
        v.addWidget(self.cond_list_container)

        return frame

    def open_cond_panel(self):
        """展开「条件单」面板 (期货拦截时提示用户填止盈/止损)。"""
        if not self.cond_toggle_btn.isChecked():
            self.cond_toggle_btn.setChecked(True)  # toggled → _on_cond_toggle 展开

    def attach_to_buy(self) -> bool:
        """是否「随买入单附带」条件单 (按买入数量自动挂)。"""
        return self.attach_buy_check.isChecked()

    def _is_futures(self) -> bool:
        return self._option is not None and self._option.right == "FUT"

    def _sync_cond_input_mode(self):
        """按当前合约切换条件单输入语义: 期货=「+点/-点」(相对买入/持仓价),
        其它=「触发价」(绝对价)。仅改标签与提示, 不动已填数值。"""
        if not hasattr(self, "_tp_price_label"):
            return
        if self._is_futures():
            self._tp_price_label.setText("止盈 +点")
            self._sl_price_label.setText("止损 -点")
            self._cond_hint.setText("期货: 止盈/止损按**点数**(相对买入/持仓均价); 平多·SELL")
        else:
            self._tp_price_label.setText("触发价")
            self._sl_price_label.setText("触发价")
            self._cond_hint.setText("止盈/止损 (平多·SELL): 到价才发限价单到 IBKR")

    def get_bracket(self, require_both: bool = False) -> dict | None:
        """读条件单面板, 供「附加到买入单」用。返回
        `{tp_on, tp_price, sl_on, sl_price, native, by_points}`, 无有效腿则 None。
        `by_points=True` (期货) 时 tp_price/sl_price 是**点数偏移**, 否则是绝对触发价。

        - ``require_both=True``  (期货强制): 止盈+止损都勾选且数值 >0, 否则 None;
        - ``require_both=False`` (期权可选附带): 至少一条腿有效即可。
        """
        tp_on = self.tp_check.isChecked()
        sl_on = self.sl_check.isChecked()
        tp = round(self.tp_price_spin.value(), 2)
        sl = round(self.sl_price_spin.value(), 2)
        tp_valid = tp_on and tp > 0
        sl_valid = sl_on and sl > 0
        if require_both:
            if not (tp_valid and sl_valid):
                return None
        elif not (tp_valid or sl_valid):
            return None
        return {
            "tp_on": tp_valid, "tp_price": tp,
            "sl_on": sl_valid, "sl_price": sl,
            "native": self.cond_native_check.isChecked(),
            "by_points": self._is_futures(),
        }

    def _on_cond_toggle(self, checked: bool):
        self.cond_panel.setVisible(checked)
        self.cond_toggle_btn.setText("条件单 ▴" if checked else "条件单 ▾")
        # 打开时用现价播种触发价输入框 (方便微调)。期货是「点数」语义, 不按现价播种。
        if checked and self._option and self._engine and not self._is_futures():
            tick = self._engine.get_tick(self._option.to_ibkr_key())
            cur = tick.get("last", 0) or ((tick.get("bid", 0) + tick.get("ask", 0)) / 2
                                          if tick.get("bid") and tick.get("ask") else 0)
            if cur > 0:
                if self.tp_price_spin.value() == 0:
                    self.tp_price_spin.setValue(round(cur * 1.2, 2))
                if self.sl_price_spin.value() == 0:
                    self.sl_price_spin.setValue(round(cur * 0.85, 2))

    def _on_arm_conditional(self):
        if not self._option:
            QMessageBox.warning(self, "未选合约", "请先在点价梯加载一个合约")
            return
        tp_on = self.tp_check.isChecked()
        sl_on = self.sl_check.isChecked()
        if not tp_on and not sl_on:
            QMessageBox.warning(self, "未勾选", "请至少勾选「止盈」或「止损」其中之一")
            return
        tp_price = round(self.tp_price_spin.value(), 2)
        sl_price = round(self.sl_price_spin.value(), 2)
        by_points = self._is_futures()
        unit = "点数" if by_points else "触发价"
        if tp_on and tp_price <= 0:
            QMessageBox.warning(self, "止盈无效", f"请填写止盈{unit}")
            return
        if sl_on and sl_price <= 0:
            QMessageBox.warning(self, "止损无效", f"请填写止损{unit}")
            return
        self.conditional_requested.emit({
            "tp_on": tp_on, "tp_price": tp_price,
            "sl_on": sl_on, "sl_price": sl_price,
            "qty": self.cond_qty_spin.value(),
            "native": self.cond_native_check.isChecked(),
            "outside_rth": self.get_outside_rth(),
            "by_points": by_points,
        })

    def set_conditionals(self, conds: list):
        """刷新「已挂本地条件单」列表 (conds: list[ConditionalOrder])。"""
        # 清空旧行
        while self.cond_list_layout.count():
            item = self.cond_list_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        for c in conds:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(4)
            color = COLOR_GREEN if c.kind == "TP" else COLOR_RED
            arrow = "≥" if c.kind == "TP" else "≤"
            lbl = QLabel(f"{c.kind_label} SELL {c.quantity} {arrow}{c.trigger_price:.2f} "
                         f"→限{c.limit_price:.2f} [本地]")
            lbl.setStyleSheet(f"color: {color}; font-size: 10px; border: none;")
            h.addWidget(lbl)
            h.addStretch()
            x = QPushButton("✕")
            x.setFixedSize(18, 18)
            x.setCursor(Qt.PointingHandCursor)
            x.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {COLOR_TEXT_DIM}; "
                f"border: none; font-size: 11px; }} QPushButton:hover {{ color: {COLOR_RED}; }}"
            )
            cid = c.cond_id
            x.clicked.connect(lambda _=False, i=cid: self.conditional_cancel_requested.emit(i))
            h.addWidget(x)
            self.cond_list_layout.addWidget(row)

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
        # 期货条件单用「点数」表示 (相对买入/持仓均价); 其它用绝对触发价
        self._sync_cond_input_mode()

        # Reset depth data
        self._depth_bids.clear()
        self._depth_asks.clear()
        self._depth_available = False
        self._last_bid = 0.0
        self._last_ask = 0.0

        if self._engine:
            # 退订旧行情 + 订阅新行情都走后台线程: 这些是 IBKR socket 调用,
            # Gateway 繁忙时会阻塞数秒, 放后台可让切标的瞬间完成、GUI 不卡。
            self._sub_generation += 1
            gen = self._sub_generation
            eng = self._engine
            old_req = self._tick_req_id
            self._tick_req_id = None
            threading.Thread(
                target=self._resubscribe_worker,
                args=(eng, option, old_req, gen),
                daemon=True,
            ).start()

        self._rebuild_ladder()
        self.option_loaded.emit()

    def _resubscribe_worker(self, eng, option, old_req, gen: int):
        """后台线程: 退订旧 tick + 订阅新合约的盘口/tick。
        仅当本次仍是最近一次切换 (代数匹配) 才保留 reqId, 否则立即退订, 防泄漏。"""
        try:
            if old_req is not None:
                eng.unsubscribe_tick(old_req)
            eng.subscribe_market_depth(option)
            rid = eng.subscribe_option_tick(option)
        except Exception:
            return
        if gen == self._sub_generation:
            self._tick_req_id = rid
        else:
            # 已被更晚的切换取代 → 退订这条, 避免占用行情线
            try:
                eng.unsubscribe_tick(rid)
            except Exception:
                pass

    def _center_price(self) -> float:
        """点价梯居中价 — bid/ask 都在取中值, 否则取存在的一侧, 再退到 last。

        `_rebuild_ladder`(建梯)与 `_refresh`(判定是否需重建)**必须共用此式**:
        否则单边报价(只有 bid 或只有 ask, SPY 0DTE 很常见)时两者算出的中心不一致
        —— `_refresh` 看到单边价在范围外要求重建, 而 `_rebuild_ladder` 却用 `last`
        居中, 下一拍 `_refresh` 又判定在范围外 → 反复重建 201 行 = 点价梯闪烁。
        """
        opt = self._option
        if opt is None:
            return 0.0
        bid = self._last_bid if self._last_bid > 0 else opt.bid
        ask = self._last_ask if self._last_ask > 0 else opt.ask
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
        if ask > 0:
            return ask
        if bid > 0:
            return bid
        return opt.last if opt.last > 0 else 0.0

    def _rebuild_ladder(self):
        """Rebuild all price ladder rows centered on mid price."""
        # Clear existing rows
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()

        if not self._option:
            return

        # Determine tick size and center price (与 _refresh 的重建判定共用 _center_price)
        mid = self._center_price()
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
            row = self._make_row(price)
            self._rows.append(row)
            self.rows_layout.addWidget(row)

        # Scroll to center
        QTimer.singleShot(100, self._scroll_to_center)

    def _make_row(self, price: float) -> "PriceLadderRow":
        """建一行价格档并接好买/卖点击信号 (建梯与边缘扩展共用)。"""
        row = PriceLadderRow(price)
        row.price_left_clicked.connect(lambda p: self._on_buy(p))
        row.price_right_clicked.connect(lambda p: self._on_sell(p))
        return row

    def _on_ladder_scrolled(self, value: int):
        """滚动条到达顶/底边缘 → 向该方向扩展更多价格档。

        采用「只增不重建」: 现价始终落在 [最低档, 最高档] 之间, 故 `_refresh`
        的自动重建判定 (现价超出可见范围才重建) 不会被触发, 不会把扩展抹掉,
        也不会闪烁。`_extending` 屏蔽扩展期间复位滚动条带来的重入。
        """
        if self._extending or not self._rows:
            return
        vbar = self.scroll_area.verticalScrollBar()
        if vbar.maximum() <= 0:
            return  # 内容还不足一屏, 无需扩展
        edge = LADDER_ROW_HEIGHT * 2
        if value <= vbar.minimum() + edge:
            self._extend_ladder(up=True)
        elif value >= vbar.maximum() - edge:
            self._extend_ladder(up=False)

    def _extend_ladder(self, up: bool):
        """向上 (更高价) 或向下 (更低价) 追加 LADDER_EXTEND_CHUNK 个档位。"""
        if len(self._rows) >= LADDER_MAX_ROWS:
            return
        ts, tl = self._tick_sizes()
        self._extending = True
        try:
            if up:
                cur = self._rows[0].price
                added = 0
                for _ in range(LADDER_EXTEND_CHUNK):
                    tick = ts if cur < TICK_THRESHOLD else tl
                    cur = round(cur + tick, 2)
                    row = self._make_row(cur)
                    self.rows_layout.insertWidget(0, row)
                    self._rows.insert(0, row)
                    added += 1
                # 顶部新增 added 行 → 内容整体下移; 复位滚动条保持视觉位置不跳
                shift = added * LADDER_ROW_HEIGHT
                QTimer.singleShot(0, lambda s=shift: self._preserve_after_prepend(s))
            else:
                cur = self._rows[-1].price
                for _ in range(LADDER_EXTEND_CHUNK):
                    tick = ts if cur < TICK_THRESHOLD else tl
                    cur = round(cur - tick, 2)
                    if cur <= 0:
                        break
                    row = self._make_row(cur)
                    self.rows_layout.addWidget(row)
                    self._rows.append(row)
        finally:
            self._extending = False

    def _preserve_after_prepend(self, shift: int):
        """顶部插入新行后, 把滚动条下移 shift 像素, 让用户视野停在原处。"""
        vbar = self.scroll_area.verticalScrollBar()
        self._extending = True
        try:
            vbar.setValue(min(vbar.value() + shift, vbar.maximum()))
        finally:
            self._extending = False

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

        # Auto re-center if current price is outside the visible ladder range.
        # 用与建梯相同的 _center_price() —— 否则单边报价时中心算法不一致会反复重建(闪烁)。
        if self._rows and (bid > 0 or ask > 0):
            mid = self._center_price()
            top_price = self._rows[0].price
            bottom_price = self._rows[-1].price
            if mid > 0 and (mid > top_price or mid < bottom_price):
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
