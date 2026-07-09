"""Position panel — displays current holdings with P/L.

Supports option positions (from engine) and IBKR portfolio positions
(stocks/ETFs via portfolio_position_received signal).
"""

import math

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QLabel, QComboBox,
)
from PyQt5.QtCore import pyqtSignal, Qt, QTimer
from PyQt5.QtGui import QColor, QBrush

from config import COLOR_GREEN, COLOR_RED, COLOR_TEXT, COLOR_TEXT_DIM, COLOR_ACCENT, COLOR_BG_DARK, COLOR_BORDER
from models import PositionInfo, PortfolioPosition, OptionInfo


class PositionPanel(QWidget):
    """Displays current positions with real-time P/L."""

    position_clicked = pyqtSignal(object)  # OptionInfo — double-click to open ladder

    def __init__(self, parent=None, default_filter: str = "期权"):
        super().__init__(parent)
        self._engine = None

        # IBKR portfolio positions (stocks, ETFs, options from reqPositions)
        self._portfolio_positions: dict[str, PortfolioPosition] = {}

        # Default filter: main app shows options only (user preference);
        # the stock trader client passes "正股/ETF"
        self._current_filter = default_filter

        # Cached brushes — avoid allocating a QBrush/QColor per cell per second
        self._brush_cache: dict[str, QBrush] = {}

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
        self.filter_combo.addItems(["全部", "期权", "正股/ETF", "期货"])
        self.filter_combo.setCurrentText(self._current_filter)
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

        headers = ["类型", "合约", "数量", "均价", "市价", "市值", "今日盈亏", "盈亏(含费)", "盈亏%"]
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
            # Keep PnL data already received for this position
            old = self._portfolio_positions.get(key)
            if old is not None and old.has_pnl_data:
                pos.daily_pnl = old.daily_pnl
                pos.unrealized_pnl = old.unrealized_pnl
                pos.market_price = old.market_price
                pos.market_value = old.market_value
                pos.has_pnl_data = True
            self._portfolio_positions[key] = pos
            self._subscribe_pnl_single(pos)
        else:
            if self._engine and hasattr(self._engine, "cancel_pnl_single"):
                self._engine.cancel_pnl_single(pos.con_id)
            self._portfolio_positions.pop(key, None)

    def _subscribe_pnl_single(self, pos: PortfolioPosition):
        """Subscribe per-position PnL (daily + unrealized + market value).
        Returns silently if engine not ready (retried from _refresh)."""
        if self._engine and hasattr(self._engine, "request_pnl_single"):
            self._engine.request_pnl_single(pos.con_id)

    def on_pnl_single(self, con_id: int, qty: float, daily_pnl: float,
                      unrealized_pnl: float, value: float):
        """Handle reqPnLSingle update — fill in market data for the position.
        NaN 字段表示 IBKR 本次未提供该值 (原 DBL_MAX), 跳过以保留上一次的好值,
        避免未实现盈亏 / 今日盈亏偶尔闪烁成 0。"""
        for pp in self._portfolio_positions.values():
            if pp.con_id == con_id:
                got_any = False
                if not math.isnan(daily_pnl):
                    pp.daily_pnl = daily_pnl
                    got_any = True
                if not math.isnan(unrealized_pnl):
                    pp.unrealized_pnl = unrealized_pnl
                    got_any = True
                if not math.isnan(value):
                    pp.market_value = value
                    if pp.quantity and pp.multiplier:
                        pp.market_price = abs(
                            value / (pp.quantity * pp.multiplier)
                        )
                if got_any:
                    pp.has_pnl_data = True
                break

    def on_portfolio_positions_end(self):
        """Called when position snapshot is complete."""
        pass  # Positions already accumulated via on_portfolio_position

    def _on_filter_changed(self, text: str):
        self._current_filter = text

    def set_filter(self, text: str):
        """外部 (切换交易品种时) 设置持仓筛选, 同步下拉显示。"""
        if text not in ("全部", "期权", "正股/ETF", "期货"):
            return
        self._current_filter = text
        self.filter_combo.blockSignals(True)
        self.filter_combo.setCurrentText(text)
        self.filter_combo.blockSignals(False)

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
                "pnl": pos.net_pnl,
                "pnl_pct": pos.net_pnl_pct,
                "commission": pos.total_commission,
                "daily": None,  # engine-tracked options: no daily PnL feed
                "has_pnl": True,  # 本地撮合持仓盈亏即时可算
                "option": pos.option,
                "key": key,
            }
            rows_data.append(row)
            seen_keys.add(key)

        # IBKR portfolio positions (stocks, ETFs, possibly options)
        for key, pp in self._portfolio_positions.items():
            if key in seen_keys:
                continue  # Avoid double-counting options

            # Retry PnL subscription (account name may arrive after positions)
            if not pp.has_pnl_data:
                self._subscribe_pnl_single(pp)

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
                "commission": (
                    # 真实引擎: 该合约今日实际佣金 (commissionReport 按 execId 去重);
                    # 旧实现硬编码 0 → 「费$」后缀从不显示
                    self._engine.get_position_commission(key)
                    if hasattr(self._engine, "get_position_commission") else 0
                ),
                "daily": pp.daily_pnl if pp.has_pnl_data else None,
                "has_pnl": pp.has_pnl_data,  # API 持仓: reqPnLSingle 到达前显示"--"而非 0
                "option": None,
                "key": key,
            }
            rows_data.append(row)

        # Apply filter
        if self._current_filter == "期权":
            rows_data = [r for r in rows_data if r["sec_type"] == "OPT"]
        elif self._current_filter == "正股/ETF":
            rows_data = [r for r in rows_data if r["sec_type"] in ("STK", "ETF")]
        elif self._current_filter == "期货":
            rows_data = [r for r in rows_data if r["sec_type"] == "FUT"]

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

            # 盈亏数据未到达前 — 显示 "--" 而非误导性的 $0.00 (含 API 期权持仓在
            # reqPnLSingle 到达前; 以及正股/ETF 行情未到时)
            if not data.get("has_pnl", True) or (
                data["sec_type"] != "OPT" and data["current_price"] <= 0
            ):
                for col in (4, 5, 6, 7, 8):
                    self._set_cell(row_idx, col, "--", COLOR_TEXT_DIM)
                self.table.setRowHeight(row_idx, 28)
                continue

            # Current price
            self._set_cell(row_idx, 4, f"${data['current_price']:.2f}", COLOR_TEXT)

            # Market value
            mv = data["market_value"]
            self._set_cell(row_idx, 5, f"${mv:,.2f}", COLOR_TEXT)

            # Daily P/L (from reqPnLSingle; options tracked locally have none)
            daily = data.get("daily")
            if daily is None:
                self._set_cell(row_idx, 6, "--", COLOR_TEXT_DIM)
            else:
                d_color = COLOR_GREEN if daily >= 0 else COLOR_RED
                d_str = f"+${daily:.2f}" if daily >= 0 else f"-${abs(daily):.2f}"
                self._set_cell(row_idx, 6, d_str, d_color)

            # P/L (net of commissions for engine-tracked positions)
            pnl = data["pnl"]
            total_pnl += pnl
            pnl_color = COLOR_GREEN if pnl >= 0 else COLOR_RED
            comm = data.get("commission", 0)
            if comm > 0:
                pnl_str = (f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}")
                pnl_str += f" (费${comm:.2f})"
            else:
                pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
            self._set_cell(row_idx, 7, pnl_str, pnl_color)

            # P/L %
            pct = data["pnl_pct"]
            pct_color = COLOR_GREEN if pct >= 0 else COLOR_RED
            self._set_cell(row_idx, 8, f"{pct:+.1f}%", pct_color)

            self.table.setRowHeight(row_idx, 28)

        # Summary
        pnl_color = COLOR_GREEN if total_pnl >= 0 else COLOR_RED
        pnl_str = f"+${total_pnl:.2f}" if total_pnl >= 0 else f"-${abs(total_pnl):.2f}"
        self.summary_label.setText(f"总盈亏: {pnl_str}")
        self.summary_label.setStyleSheet(f"color: {pnl_color}; padding: 4px; font-weight: bold;")

        count = len(rows_data)
        self.title.setText(f"持仓 ({count})")

    def _brush(self, color: str) -> QBrush:
        b = self._brush_cache.get(color)
        if b is None:
            b = QBrush(QColor(color))
            self._brush_cache[color] = b
        return b

    def _set_cell(self, row, col, text, color):
        item = self.table.item(row, col)
        if item is None:
            item = QTableWidgetItem()
            item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, col, item)
        # Skip redundant setText (avoids needless cell repaint for static
        # columns like name/qty/avg that don't change between ticks).
        if item.text() != text:
            item.setText(text)
        item.setForeground(self._brush(color))

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
                # Build an OptionInfo so double-clicking an option held from a
                # prior session (only known via reqPositions) opens the ladder
                # and lets the user close it. Stocks/ETFs stay None (handled
                # by the dedicated stock trader).
                opt = None
                if pp.sec_type == "OPT":
                    opt = OptionInfo(
                        symbol=pp.symbol, expiry=pp.expiry,
                        strike=pp.strike, right=pp.right, con_id=pp.con_id,
                    )
                elif pp.sec_type in ("STK", "ETF"):
                    opt = OptionInfo(symbol=pp.symbol, expiry="",
                                     strike=0.0, right="STK", con_id=pp.con_id)
                elif pp.sec_type == "FUT":
                    opt = OptionInfo(symbol=pp.symbol, expiry=pp.expiry,
                                     strike=0.0, right="FUT", con_id=pp.con_id)
                rows_data.append({"option": opt, "sec_type": pp.sec_type, "key": key})

        # Apply same filter
        if self._current_filter == "期权":
            rows_data = [r for r in rows_data if r["sec_type"] == "OPT"]
        elif self._current_filter == "正股/ETF":
            rows_data = [r for r in rows_data if r["sec_type"] in ("STK", "ETF")]
        elif self._current_filter == "期货":
            rows_data = [r for r in rows_data if r["sec_type"] == "FUT"]

        if row < len(rows_data):
            opt = rows_data[row].get("option")
            if opt:
                self.position_clicked.emit(opt)

    def cleanup(self):
        self._refresh_timer.stop()
