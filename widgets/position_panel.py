"""Position panel — displays current holdings with P/L.

Supports option positions (from engine) and IBKR portfolio positions
(stocks/ETFs via portfolio_position_received signal).
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QLabel, QComboBox,
)
from PyQt5.QtCore import pyqtSignal, Qt, QTimer
from PyQt5.QtGui import QColor, QBrush

from config import COLOR_GREEN, COLOR_RED, COLOR_TEXT, COLOR_TEXT_DIM, COLOR_ACCENT, COLOR_BG_DARK, COLOR_BORDER
from models import PositionInfo, PortfolioPosition


class PositionPanel(QWidget):
    """Displays current positions with real-time P/L."""

    position_clicked = pyqtSignal(object)  # OptionInfo — double-click to open ladder

    def __init__(self, parent=None):
        super().__init__(parent)
        self._engine = None

        # IBKR portfolio positions (stocks, ETFs, options from reqPositions)
        self._portfolio_positions: dict[str, PortfolioPosition] = {}

        self._current_filter = "全部"

        self._build_ui()

        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self._refresh)
        self._refresh_timer.start(1000)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Top bar: title + filter
        top_layout = QHBoxLayout()

        self.title = QLabel("持仓")
        self.title.setStyleSheet("font-size: 13px; font-weight: bold; padding: 4px;")
        top_layout.addWidget(self.title)

        top_layout.addStretch()

        # Filter combo
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["全部", "期权", "正股/ETF"])
        self.filter_combo.setFixedWidth(100)
        self.filter_combo.currentTextChanged.connect(self._on_filter_changed)
        self.filter_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {COLOR_BG_DARK};
                color: {COLOR_TEXT};
                border: 1px solid {COLOR_BORDER};
                padding: 2px 6px;
                border-radius: 3px;
                font-size: 11px;
            }}
        """)
        top_layout.addWidget(self.filter_combo)

        layout.addLayout(top_layout)

        headers = ["类型", "合约", "数量", "均价", "市价", "市值", "盈亏", "盈亏%"]
        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for i in range(2, len(headers)):
            header.setSectionResizeMode(i, QHeaderView.ResizeToContents)

        self.table.doubleClicked.connect(self._on_double_click)
        layout.addWidget(self.table)

        # Summary
        self.summary_label = QLabel("总盈亏: $0.00")
        self.summary_label.setStyleSheet(f"color: {COLOR_TEXT_DIM}; padding: 4px;")
        layout.addWidget(self.summary_label)

    def set_engine(self, engine):
        self._engine = engine

    def on_portfolio_position(self, pos: PortfolioPosition):
        """Handle portfolio_position_received signal from IBKR."""
        key = pos.position_key
        if abs(pos.quantity) > 0:
            self._portfolio_positions[key] = pos
        else:
            self._portfolio_positions.pop(key, None)

    def on_portfolio_positions_end(self):
        """Called when position snapshot is complete."""
        pass  # Positions already accumulated via on_portfolio_position

    def _on_filter_changed(self, text: str):
        self._current_filter = text

    def _refresh(self):
        if not self._engine:
            return

        # Merge engine positions (options tracked locally) + IBKR portfolio positions
        rows_data = []
        seen_keys = set()

        # Engine option positions (Paper or Live tracked positions)
        for key, pos in self._engine.positions.items():
            # Update current price from tick data
            tick = self._engine.get_tick(key)
            last = tick.get("last", 0)
            bid = tick.get("bid", 0)
            ask = tick.get("ask", 0)
            if last > 0:
                pos.current_price = last
            elif bid > 0 and ask > 0:
                pos.current_price = (bid + ask) / 2
            elif bid > 0:
                pos.current_price = bid

            row = {
                "type": "期权",
                "sec_type": "OPT",
                "name": pos.option.display_name,
                "qty": pos.quantity,
                "avg_price": pos.avg_price,
                "current_price": pos.current_price,
                "market_value": pos.market_value,
                "pnl": pos.unrealized_pnl,
                "pnl_pct": pos.pnl_pct,
                "option": pos.option,
                "key": key,
            }
            rows_data.append(row)
            seen_keys.add(key)

        # IBKR portfolio positions (stocks, ETFs, possibly options)
        for key, pp in self._portfolio_positions.items():
            if key in seen_keys:
                continue  # Avoid double-counting options

            row = {
                "type": pp.instrument_type,
                "sec_type": pp.sec_type,
                "name": pp.display_name,
                "qty": int(pp.quantity),
                "avg_price": pp.avg_price,
                "current_price": pp.market_price,
                "market_value": pp.market_value,
                "pnl": pp.unrealized_pnl,
                "pnl_pct": pp.pnl_pct,
                "option": None,
                "key": key,
            }
            rows_data.append(row)

        # Apply filter
        if self._current_filter == "期权":
            rows_data = [r for r in rows_data if r["sec_type"] == "OPT"]
        elif self._current_filter == "正股/ETF":
            rows_data = [r for r in rows_data if r["sec_type"] in ("STK", "ETF")]

        self.table.setRowCount(len(rows_data))
        total_pnl = 0.0

        for row_idx, data in enumerate(rows_data):
            # Type
            self._set_cell(row_idx, 0, data["type"], COLOR_ACCENT)

            # Contract name
            self._set_cell(row_idx, 1, data["name"], COLOR_TEXT)

            # Quantity
            self._set_cell(row_idx, 2, str(data["qty"]), COLOR_TEXT)

            # Avg price
            self._set_cell(row_idx, 3, f"${data['avg_price']:.2f}", COLOR_TEXT)

            # Current price
            self._set_cell(row_idx, 4, f"${data['current_price']:.2f}", COLOR_TEXT)

            # Market value
            mv = data["market_value"]
            self._set_cell(row_idx, 5, f"${mv:,.2f}", COLOR_TEXT)

            # P/L
            pnl = data["pnl"]
            total_pnl += pnl
            pnl_color = COLOR_GREEN if pnl >= 0 else COLOR_RED
            pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
            self._set_cell(row_idx, 6, pnl_str, pnl_color)

            # P/L %
            pct = data["pnl_pct"]
            pct_color = COLOR_GREEN if pct >= 0 else COLOR_RED
            self._set_cell(row_idx, 7, f"{pct:+.1f}%", pct_color)

            self.table.setRowHeight(row_idx, 28)

        # Summary
        pnl_color = COLOR_GREEN if total_pnl >= 0 else COLOR_RED
        pnl_str = f"+${total_pnl:.2f}" if total_pnl >= 0 else f"-${abs(total_pnl):.2f}"
        self.summary_label.setText(f"总盈亏: {pnl_str}")
        self.summary_label.setStyleSheet(f"color: {pnl_color}; padding: 4px; font-weight: bold;")

        count = len(rows_data)
        self.title.setText(f"持仓 ({count})")

    def _set_cell(self, row, col, text, color):
        item = self.table.item(row, col)
        if item is None:
            item = QTableWidgetItem()
            item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, col, item)
        item.setText(text)
        item.setForeground(QBrush(QColor(color)))

    def _on_double_click(self, index):
        if not self._engine:
            return
        row = index.row()

        # Rebuild same data to find the option at this row
        rows_data = []
        for key, pos in self._engine.positions.items():
            rows_data.append({"option": pos.option, "sec_type": "OPT", "key": key})

        for key, pp in self._portfolio_positions.items():
            if key not in {r["key"] for r in rows_data}:
                rows_data.append({"option": None, "sec_type": pp.sec_type, "key": key})

        # Apply same filter
        if self._current_filter == "期权":
            rows_data = [r for r in rows_data if r["sec_type"] == "OPT"]
        elif self._current_filter == "正股/ETF":
            rows_data = [r for r in rows_data if r["sec_type"] in ("STK", "ETF")]

        if row < len(rows_data):
            opt = rows_data[row].get("option")
            if opt:
                self.position_clicked.emit(opt)

    def cleanup(self):
        self._refresh_timer.stop()
