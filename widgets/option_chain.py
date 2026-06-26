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
        self._atm_row: int = 0   # ATM 行索引 (切 Tab 时滚到此处, 优先取价)
        # 视口取价去抖: 滚动停止 ~250ms 后只对当前可见行拉一次快照 (省行情线)
        self._snap_timer = QTimer(self)
        self._snap_timer.setSingleShot(True)
        self._snap_timer.timeout.connect(self._request_snapshots)

        # Cached brushes — reused every refresh instead of allocating a new
        # QBrush/QColor per cell per second.
        self._brush_text = QBrush(QColor(COLOR_TEXT))
        self._brush_bid = QBrush(QColor(COLOR_GREEN))
        self._brush_ask = QBrush(QColor(COLOR_RED))

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

        # 手动刷新按钮: 拉一次快照报价 (不占常驻行情线, 避免持续订阅压垮 Gateway)
        self._refresh_btn = QPushButton("🔄 刷新报价")
        self._refresh_btn.setFixedHeight(26)
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_ACCENT};
                color: white;
                border: 1px solid {COLOR_ACCENT};
                border-radius: 3px;
                font-weight: bold;
                font-size: 12px;
                padding: 2px 10px;
            }}
            QPushButton:hover {{ background-color: #0097a7; }}
            QPushButton:disabled {{
                background-color: {COLOR_BUTTON_DISABLED}; color: #555555;
                border: 1px solid {COLOR_BORDER};
            }}
        """)
        self._refresh_btn.clicked.connect(self._request_snapshots)
        range_layout.addWidget(self._refresh_btn)

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

        # 显示**全部**行权价 (能滚到远端如 SPY 695 put); 不再按 ATM±N 裁剪。
        # 行情线占用由「按视口取价」控制 (只对当前滚动可见行拉快照), 而非靠少显示。
        self._strikes = sorted(strikes)
        if stock_price > 0 and self._strikes:
            self._atm_row = min(range(len(self._strikes)),
                                key=lambda i: abs(self._strikes[i] - stock_price))
        else:
            self._atm_row = len(self._strikes) // 2

        print(f"[DEBUG] Option chain: {len(self._all_expirations)} total expirations, "
              f"{len(self._strikes)} strikes displayed (full), "
              f"ATM row={self._atm_row}, stock_price={stock_price}", flush=True)

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
        """Create the T-shaped table **shell** for one expiry (rows built lazily).

        显示全部行权价后, 单条链可能有几百档 × 多个到期日 Tab。若在加载时一次性
        把每个 Tab 的行都建出来, 主线程会卡顿。故这里只建表头/列, 行留到该 Tab
        首次显示时 (`_on_tab_changed` → `_ensure_table_built`) 再填充。
        """
        headers = [
            "C.Bid", "C.Ask", "C.Last", "C.Vol",
            "Strike",
            "P.Bid", "P.Ask", "P.Last", "P.Vol",
        ]
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(False)
        table._expiry = expiry   # 该 Tab 的到期日 (供懒填充/点击用)
        table._built = False     # 行是否已填充

        # Column widths
        header = table.horizontalHeader()
        for i in range(len(headers)):
            if i == 4:  # Strike column
                header.setSectionResizeMode(i, QHeaderView.Fixed)
                table.setColumnWidth(i, 70)
            else:
                header.setSectionResizeMode(i, QHeaderView.Stretch)

        table.cellClicked.connect(lambda r, c: self._on_cell_clicked(table, r, c))
        # 滚动该到期日表 → 去抖后只对新进入视口的行拉快照 (省行情线)
        table.verticalScrollBar().valueChanged.connect(self._on_table_scrolled)
        return table

    def _ensure_table_built(self, table: QTableWidget):
        """首次显示该到期日 Tab 时, 填充全部行权价行 (懒加载)。"""
        if getattr(table, "_built", False):
            return
        expiry = getattr(table, "_expiry", "")
        table.setRowCount(len(self._strikes))

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

        # Set row height
        for row in range(table.rowCount()):
            table.setRowHeight(row, 28)

        table._built = True

        return table

    def _on_table_scrolled(self, _value: int):
        """表格滚动: 重启去抖定时器, 停下后对可见行拉快照。"""
        self._snap_timer.start(250)

    def _on_tab_changed(self, index: int):
        """切到新到期日: 把表滚到最接近现价的行权价居中, 再对可见行拉快照。

        居中**延后**到下一轮事件循环: 刚建/刚切的表此刻 viewport 高度可能还是 0,
        立即 scrollToItem 会无效 → 表停在最上面(最低行权价, 如 0.5)。延后到布局
        完成后再滚, 才能真正居中到平值附近。
        """
        if index < 0 or index >= len(self._expirations):
            return
        table = self.tab_widget.widget(index)
        if not isinstance(table, QTableWidget):
            return
        self._ensure_table_built(table)
        QTimer.singleShot(0, lambda t=table: self._center_and_snapshot(t))

    def _center_and_snapshot(self, table: QTableWidget):
        """布局就绪后: 先把最接近现价的行权价滚到居中, 再对(居中后的)可见行拉快照。"""
        if table is not self.tab_widget.currentWidget():
            return  # 期间又切走了, 放弃
        self._scroll_table_to_atm(table)
        self._request_snapshots()

    def _recompute_atm_row(self):
        """用**最新现价**重算最接近的行权价行 (现价随实时行情更新, 故每次切表都重算)。"""
        if not self._strikes:
            self._atm_row = 0
        elif self._stock_price > 0:
            self._atm_row = min(range(len(self._strikes)),
                                key=lambda i: abs(self._strikes[i] - self._stock_price))
        else:
            self._atm_row = len(self._strikes) // 2

    def _scroll_table_to_atm(self, table: QTableWidget):
        """把表格滚动到最接近现价的行权价行居中, 使默认可见区聚焦平值附近。"""
        self._recompute_atm_row()
        if 0 <= self._atm_row < table.rowCount():
            item = table.item(self._atm_row, 4)
            if item:
                table.scrollToItem(item, QAbstractItemView.PositionAtCenter)

    def _visible_strike_rows(self, table: QTableWidget) -> list[int]:
        """当前视口内可见的行索引 (上下各留几行缓冲, 滚动时预取)。

        首次显示时视口高度可能尚未布局好 (rowAt 返回 -1) → 回退到以 ATM 为中心的
        一个窗口, 保证平值附近先有报价。
        """
        n = table.rowCount()
        if n == 0:
            return []
        vp_h = table.viewport().height()
        top = table.rowAt(0)
        bottom = table.rowAt(max(vp_h - 1, 0))
        if top < 0 or bottom < 0 or vp_h <= 0:
            # 视口未就绪: 以 ATM 为中心取一窗口 (约半个行情线预算的行数)
            half = max(8, MAX_SIMULTANEOUS_STREAMS // 4)
            lo = max(0, self._atm_row - half)
            hi = min(n - 1, self._atm_row + half)
            return list(range(lo, hi + 1))
        buf = 4
        lo = max(0, top - buf)
        hi = min(n - 1, bottom + buf)
        return list(range(lo, hi + 1))

    def _request_snapshots(self):
        """对当前到期日**视口内可见**的行权价拉一次 one-shot 快照报价。

        用完即弃 (IBKR 自动取消), **不占常驻行情线**。只取当前滚动可见的行
        (而非整条链), 这样既能滚到任意远端行权价取价, 又不会因一次性订阅几百档
        压垮 Gateway 行情线上限。快照异步到达, 由 `_refresh_prices` 绘出;
        另排几个 singleShot 让它更快显示。
        """
        if not self._engine or not hasattr(self._engine, "snapshot_option_tick"):
            return
        idx = self.tab_widget.currentIndex()
        if idx < 0 or idx >= len(self._expirations):
            return
        table = self.tab_widget.widget(idx)
        if not isinstance(table, QTableWidget):
            return
        expiry = self._expirations[idx]
        count = 0
        for row in self._visible_strike_rows(table):
            if row >= len(self._strikes):
                continue
            strike = self._strikes[row]
            for right in ("C", "P"):
                if count >= MAX_SIMULTANEOUS_STREAMS:
                    break
                opt = OptionInfo(
                    symbol=self._symbol, expiry=expiry,
                    strike=strike, right=right,
                )
                self._engine.snapshot_option_tick(opt)
                count += 1
        # 快照异步返回, 多排几次重绘让数据尽快出现
        for delay_ms in (300, 800, 1500, 2500):
            QTimer.singleShot(delay_ms, self._refresh_prices)

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

        # Update title with real-time underlying price
        if self._symbol:
            stock_key = f"__stock__{self._symbol}"
            stock_tick = self._engine.get_tick(stock_key)
            price = stock_tick.get("last", 0)
            if price <= 0:
                bid = stock_tick.get("bid", 0)
                ask = stock_tick.get("ask", 0)
                price = (bid + ask) / 2 if bid > 0 and ask > 0 else (bid or ask)
            if price > 0:
                self._stock_price = price
                self.title_label.setText(f"期权链 — {self._symbol}  ${price:.2f}")

        idx = self.tab_widget.currentIndex()
        if idx < 0 or idx >= len(self._expirations):
            return

        table = self.tab_widget.widget(idx)
        if not isinstance(table, QTableWidget):
            return
        if not getattr(table, "_built", False):
            return  # 行尚未懒填充, 无可更新单元格

        expiry = self._expirations[idx]

        for row, strike in enumerate(self._strikes):
            for right_idx, right in enumerate(("C", "P")):
                key = f"{self._symbol}_{expiry}_{right}_{strike}"
                tick = self._engine.get_tick(key)

                bid = tick.get("bid", 0)
                ask = tick.get("ask", 0)
                last = tick.get("last", 0)
                volume = tick.get("volume", 0)

                if right == "C":
                    cols = (0, 1, 2)  # bid, ask, last
                    vol_col = 3
                else:
                    cols = (5, 6, 7)  # bid, ask, last
                    vol_col = 8

                for ci, val in zip(cols, (bid, ask, last)):
                    item = table.item(row, ci)
                    if item and val > 0:
                        text = f"{val:.2f}"
                        if item.text() != text:   # skip unchanged cells (no repaint)
                            item.setText(text)
                            # Color code: green bid, red ask, default for last.
                            # Only set when text changes — the cached brushes
                            # avoid per-tick QBrush/QColor allocation.
                            if ci in (0, 5):    # bid
                                item.setForeground(self._brush_bid)
                            elif ci in (1, 6):  # ask
                                item.setForeground(self._brush_ask)
                            else:
                                item.setForeground(self._brush_text)

                # Volume column (was never rendered before)
                vol_item = table.item(row, vol_col)
                if vol_item and volume > 0:
                    vtext = str(int(volume))
                    if vol_item.text() != vtext:
                        vol_item.setText(vtext)
                        vol_item.setForeground(self._brush_text)

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
