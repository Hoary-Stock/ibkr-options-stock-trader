"""Option Chain — T-shaped quote table with expiry tabs + date range filter."""

from datetime import datetime, timedelta

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QLabel,
    QPushButton, QButtonGroup,
)
from PyQt5.QtCore import pyqtSignal, Qt, QTimer
from PyQt5.QtGui import QColor, QBrush

from config import (
    COLOR_BG_PANEL, COLOR_BG_DARK, COLOR_TEXT, COLOR_TEXT_DIM,
    COLOR_ATM_HIGHLIGHT, COLOR_GREEN, COLOR_RED, COLOR_ACCENT,
    COLOR_BORDER, COLOR_BUTTON_DISABLED,
    MAX_EXPIRY_TABS_PER_RANGE, MAX_SIMULTANEOUS_STREAMS,
)
from models import OptionInfo


# Range filter names in display order
RANGE_NAMES = ["0DTE", "本周", "下周", "本月", "下月", "远月", "全部"]


class OptionChainWidget(QWidget):
    """T-shaped option chain with expiry tabs and date range filter."""

    option_selected = pyqtSignal(object)  # OptionInfo

    def __init__(self, parent=None):
        super().__init__(parent)
        self._symbol = ""
        self._all_expirations: list[str] = []  # ALL future expirations
        self._expirations: list[str] = []       # Currently displayed (filtered)
        self._strikes: list[float] = []
        self._options: dict[str, OptionInfo] = {}  # key -> OptionInfo
        self._sub_req_ids: dict[str, int] = {}      # key -> reqId
        self._engine = None
        self._stock_price = 0.0
        self._range_buckets: dict[str, list[str]] = {}  # range_name -> [expirations]
        self._active_range: str = ""

        self._build_ui()

        # Refresh timer
        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self._refresh_prices)
        self._refresh_timer.start(1000)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.title_label = QLabel("期权链")
        self.title_label.setStyleSheet(
            "font-size: 14px; font-weight: bold; padding: 4px;"
        )
        layout.addWidget(self.title_label)

        # ── Range filter bar ──
        self._range_bar = QWidget()
        range_layout = QHBoxLayout(self._range_bar)
        range_layout.setContentsMargins(4, 2, 4, 2)
        range_layout.setSpacing(4)

        self._range_btn_group = QButtonGroup(self)
        self._range_btn_group.setExclusive(True)
        self._range_buttons: dict[str, QPushButton] = {}

        for name in RANGE_NAMES:
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setMinimumWidth(50)
            btn.setFixedHeight(26)
            btn.setCursor(Qt.PointingHandCursor)
            self._apply_range_btn_style(btn, selected=False, enabled=True)
            btn.clicked.connect(lambda checked, n=name: self._on_range_clicked(n))
            self._range_btn_group.addButton(btn)
            self._range_buttons[name] = btn
            range_layout.addWidget(btn)

        range_layout.addStretch()
        self._range_bar.hide()  # Hidden until chain is loaded
        layout.addWidget(self._range_bar)

        # ── Tab widget ──
        self.tab_widget = QTabWidget()
        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tab_widget)

    def _apply_range_btn_style(self, btn: QPushButton, selected: bool, enabled: bool):
        if not enabled:
            btn.setEnabled(False)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_BUTTON_DISABLED};
                    color: #555555;
                    border: 1px solid {COLOR_BORDER};
                    border-radius: 3px;
                    font-size: 12px;
                    padding: 2px 6px;
                }}
            """)
        elif selected:
            btn.setEnabled(True)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_ACCENT};
                    color: white;
                    border: 1px solid {COLOR_ACCENT};
                    border-radius: 3px;
                    font-weight: bold;
                    font-size: 12px;
                    padding: 2px 6px;
                }}
            """)
        else:
            btn.setEnabled(True)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_BG_DARK};
                    color: {COLOR_TEXT_DIM};
                    border: 1px solid {COLOR_BORDER};
                    border-radius: 3px;
                    font-size: 12px;
                    padding: 2px 6px;
                }}
                QPushButton:hover {{
                    background-color: {COLOR_BG_PANEL};
                    color: {COLOR_TEXT};
                }}
            """)

    def set_engine(self, engine):
        self._engine = engine

    def load_chain(self, symbol: str, expirations: list[str], strikes: list[float],
                   stock_price: float = 0):
        """Load option chain data."""
        self._symbol = symbol
        self._stock_price = stock_price

        # Filter to future expirations only
        today = datetime.now().strftime("%Y%m%d")
        self._all_expirations = [e for e in expirations if e >= today]

        # Filter strikes near the money (+-15 strikes around ATM)
        if stock_price > 0:
            atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - stock_price))
        else:
            atm_idx = len(strikes) // 2
        start = max(0, atm_idx - 15)
        end = min(len(strikes), atm_idx + 16)
        self._strikes = strikes[start:end]

        print(f"[DEBUG] Option chain: {len(self._all_expirations)} total expirations, "
              f"{len(self._strikes)} strikes displayed, "
              f"ATM idx={atm_idx}, stock_price={stock_price}", flush=True)

        self.title_label.setText(f"期权链 — {symbol}")

        # Categorize expirations into range buckets
        self._categorize_expirations()

        # Update range button states
        self._update_range_buttons()
        self._range_bar.show()

        # Auto-select the first range that has expirations
        auto_range = "全部"
        for name in RANGE_NAMES:
            if self._range_buckets.get(name):
                auto_range = name
                break

        self._apply_range_filter(auto_range)

    def _categorize_expirations(self):
        """Classify each expiration into a range bucket."""
        now = datetime.now()
        today = now.date()

        # End of this week (Sunday)
        days_until_sunday = 6 - today.weekday()  # Monday=0, Sunday=6
        end_of_week = today + timedelta(days=days_until_sunday)

        # Next week
        next_monday = end_of_week + timedelta(days=1)
        next_sunday = next_monday + timedelta(days=6)

        # Current month end
        if now.month == 12:
            this_month_end = today.replace(year=now.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            this_month_end = today.replace(month=now.month + 1, day=1) - timedelta(days=1)

        # Next month
        if now.month == 12:
            next_month_start = today.replace(year=now.year + 1, month=1, day=1)
            next_month_end = today.replace(year=now.year + 1, month=2, day=1) - timedelta(days=1)
        elif now.month == 11:
            next_month_start = today.replace(month=12, day=1)
            next_month_end = today.replace(year=now.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            next_month_start = today.replace(month=now.month + 1, day=1)
            next_month_end = today.replace(month=now.month + 2, day=1) - timedelta(days=1)

        buckets: dict[str, list[str]] = {name: [] for name in RANGE_NAMES}

        for exp_str in self._all_expirations:
            exp_date = datetime.strptime(exp_str, "%Y%m%d").date()

            if exp_date == today:
                buckets["0DTE"].append(exp_str)
            elif exp_date <= end_of_week:
                buckets["本周"].append(exp_str)
            elif next_monday <= exp_date <= next_sunday:
                buckets["下周"].append(exp_str)
            elif exp_date <= this_month_end:
                buckets["本月"].append(exp_str)
            elif next_month_start <= exp_date <= next_month_end:
                buckets["下月"].append(exp_str)
            else:
                buckets["远月"].append(exp_str)

            # "全部" always gets everything
            buckets["全部"].append(exp_str)

        self._range_buckets = buckets

    def _update_range_buttons(self):
        """Enable/disable range buttons based on available expirations."""
        for name, btn in self._range_buttons.items():
            has_data = bool(self._range_buckets.get(name))
            self._apply_range_btn_style(btn, selected=False, enabled=has_data)

    def _on_range_clicked(self, range_name: str):
        self._apply_range_filter(range_name)

    def _apply_range_filter(self, range_name: str):
        """Filter expirations by range and rebuild tabs."""
        self._active_range = range_name

        # Update button styles
        for name, btn in self._range_buttons.items():
            has_data = bool(self._range_buckets.get(name))
            is_selected = (name == range_name)
            self._apply_range_btn_style(btn, selected=is_selected, enabled=has_data)
            if is_selected:
                btn.setChecked(True)

        # Get filtered expirations
        filtered = self._range_buckets.get(range_name, [])
        self._expirations = filtered[:MAX_EXPIRY_TABS_PER_RANGE]

        # Rebuild tabs
        self.tab_widget.blockSignals(True)
        self._unsubscribe_all()
        self.tab_widget.clear()
        self._options.clear()

        for exp in self._expirations:
            display = self._format_expiry(exp)
            table = self._create_table(exp)
            self.tab_widget.addTab(table, display)

        self.tab_widget.blockSignals(False)

        # Load first tab
        if self._expirations:
            self._on_tab_changed(0)

    def _format_expiry(self, exp: str) -> str:
        """Format expiry for tab display. Shows DTE."""
        today = datetime.now()
        exp_date = datetime.strptime(exp, "%Y%m%d")
        dte = (exp_date - today).days
        if dte < 0:
            dte = 0
        month_day = f"{exp[4:6]}/{exp[6:8]}"
        if dte == 0:
            return f"0DTE {month_day}"
        elif dte == 1:
            return f"1DTE {month_day}"
        else:
            return f"{dte}D {month_day}"

    def _create_table(self, expiry: str) -> QTableWidget:
        """Create the T-shaped table for one expiry."""
        headers = [
            "C.Bid", "C.Ask", "C.Last", "C.Vol",
            "Strike",
            "P.Bid", "P.Ask", "P.Last", "P.Vol",
        ]
        table = QTableWidget(len(self._strikes), len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(False)

        # Column widths
        header = table.horizontalHeader()
        for i in range(len(headers)):
            if i == 4:  # Strike column
                header.setSectionResizeMode(i, QHeaderView.Fixed)
                table.setColumnWidth(i, 70)
            else:
                header.setSectionResizeMode(i, QHeaderView.Stretch)

        # Populate strikes
        for row, strike in enumerate(self._strikes):
            # Strike column (center)
            strike_str = f"{int(strike)}" if strike == int(strike) else f"{strike:g}"
            item = QTableWidgetItem(strike_str)
            item.setTextAlignment(Qt.AlignCenter)
            item.setData(Qt.UserRole, strike)

            # ATM highlighting
            if self._stock_price > 0:
                if abs(strike - self._stock_price) <= 1.0:
                    item.setBackground(QBrush(QColor(COLOR_ATM_HIGHLIGHT)))

            table.setItem(row, 4, item)

            # Create OptionInfo for Call and Put
            for right in ("C", "P"):
                opt = OptionInfo(
                    symbol=self._symbol,
                    expiry=expiry,
                    strike=strike,
                    right=right,
                )
                self._options[opt.to_ibkr_key()] = opt

            # Initialize price cells
            for col in range(9):
                if col == 4:
                    continue
                item = QTableWidgetItem("—")
                item.setTextAlignment(Qt.AlignCenter)
                item.setForeground(QBrush(QColor(COLOR_TEXT_DIM)))
                table.setItem(row, col, item)

        table.cellClicked.connect(lambda r, c: self._on_cell_clicked(table, r, c))

        # Set row height
        for row in range(table.rowCount()):
            table.setRowHeight(row, 28)

        return table

    def _on_tab_changed(self, index: int):
        """When tab changes, resubscribe to market data."""
        if index < 0 or index >= len(self._expirations):
            return

        # Unsubscribe old
        self._unsubscribe_all()

        # Subscribe new
        expiry = self._expirations[index]
        count = 0
        for strike in self._strikes:
            for right in ("C", "P"):
                if count >= MAX_SIMULTANEOUS_STREAMS:
                    break
                opt = OptionInfo(
                    symbol=self._symbol, expiry=expiry,
                    strike=strike, right=right,
                )
                key = opt.to_ibkr_key()
                if self._engine and key not in self._sub_req_ids:
                    req_id = self._engine.subscribe_option_tick(opt)
                    self._sub_req_ids[key] = req_id
                    count += 1

    def _unsubscribe_all(self):
        """Cancel all current subscriptions."""
        if self._engine:
            for key, req_id in list(self._sub_req_ids.items()):
                self._engine.unsubscribe_tick(req_id)
        self._sub_req_ids.clear()

    def _refresh_prices(self):
        """Update displayed prices from tick data."""
        if not self._engine:
            return

        idx = self.tab_widget.currentIndex()
        if idx < 0 or idx >= len(self._expirations):
            return

        table = self.tab_widget.widget(idx)
        if not isinstance(table, QTableWidget):
            return

        expiry = self._expirations[idx]

        for row, strike in enumerate(self._strikes):
            for right_idx, right in enumerate(("C", "P")):
                key = f"{self._symbol}_{expiry}_{right}_{strike}"
                tick = self._engine.get_tick(key)

                bid = tick.get("bid", 0)
                ask = tick.get("ask", 0)
                last = tick.get("last", 0)

                if right == "C":
                    cols = (0, 1, 2)  # bid, ask, last
                else:
                    cols = (5, 6, 7)  # bid, ask, last

                for ci, val in zip(cols, (bid, ask, last)):
                    item = table.item(row, ci)
                    if item and val > 0:
                        item.setText(f"{val:.2f}")
                        item.setForeground(QBrush(QColor(COLOR_TEXT)))

                        # Color code: green bid, red ask
                        if ci in (0, 5):  # bid
                            item.setForeground(QBrush(QColor(COLOR_GREEN)))
                        elif ci in (1, 6):  # ask
                            item.setForeground(QBrush(QColor(COLOR_RED)))

                # Update the OptionInfo
                opt = self._options.get(key)
                if opt:
                    opt.bid = tick.get("bid", 0)
                    opt.ask = tick.get("ask", 0)
                    opt.last = tick.get("last", 0)

    def _on_cell_clicked(self, table: QTableWidget, row: int, col: int):
        """Click on a cell -> emit option_selected."""
        if row < 0 or row >= len(self._strikes):
            return

        idx = self.tab_widget.currentIndex()
        if idx < 0 or idx >= len(self._expirations):
            return

        strike = self._strikes[row]
        expiry = self._expirations[idx]

        # Determine Call or Put based on column
        if col <= 3:
            right = "C"
        elif col >= 5:
            right = "P"
        else:
            right = "C"  # Strike column -> default to Call

        key = f"{self._symbol}_{expiry}_{right}_{strike}"
        opt = self._options.get(key)
        if opt:
            self.option_selected.emit(opt)

    def update_stock_price(self, price: float):
        """Update ATM highlighting when stock price changes."""
        self._stock_price = price

    def cleanup(self):
        """Cleanup subscriptions."""
        self._refresh_timer.stop()
        self._unsubscribe_all()
