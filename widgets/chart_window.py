"""K-Line chart window with candlesticks, indicators, and volume."""

import threading
import numpy as np

import pyqtgraph as pg
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QCheckBox, QStatusBar,
    QSizePolicy,
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject
from PyQt5.QtGui import QColor, QPen, QFont

from config import (
    CHART_TIMEFRAMES, DEFAULT_SYMBOLS,
    CHART_COLOR_CANDLE_UP, CHART_COLOR_CANDLE_DOWN,
    CHART_COLOR_MA5, CHART_COLOR_MA20, CHART_COLOR_MA50, CHART_COLOR_MA200,
    CHART_COLOR_VWAP, CHART_COLOR_VOLUME_UP, CHART_COLOR_VOLUME_DOWN,
    CHART_COLOR_BG, CHART_COLOR_CROSSHAIR,
    COLOR_BG, COLOR_BG_DARK, COLOR_BG_PANEL, COLOR_TEXT,
    COLOR_BORDER, COLOR_ACCENT,
)
from widgets.candlestick_item import CandlestickItem
from widgets.chart_indicators import IndicatorCalculator


class _DateAxisItem(pg.AxisItem):
    """Custom X-axis that shows date/time strings."""

    def __init__(self, dates=None, **kwargs):
        super().__init__(**kwargs)
        self._dates = dates or []

    def set_dates(self, dates: list[str]):
        self._dates = dates

    def tickStrings(self, values, scale, spacing):
        result = []
        for v in values:
            idx = int(round(v))
            if 0 <= idx < len(self._dates):
                result.append(self._dates[idx])
            else:
                result.append("")
        return result


class ChartWindow(QMainWindow):
    """Standalone K-line chart window."""

    _bars_loaded = pyqtSignal(int, list)  # internal: reqId, bars

    def __init__(self, engine, symbol: str = "SPY", parent=None):
        super().__init__(parent)
        self._engine = engine
        self._symbol = symbol
        self._current_tf = "5分钟"  # default timeframe
        self._req_id: int | None = None  # active keepUpToDate reqId
        self._bars: list[dict] = []
        self._dates: list[str] = []

        # Indicator plot items
        self._ma_plots: dict[str, pg.PlotDataItem] = {}
        self._vwap_plot: pg.PlotDataItem | None = None
        self._volume_item: pg.BarGraphItem | None = None

        self.setWindowTitle(f"K线图 — {symbol}")
        self.setMinimumSize(900, 600)
        self.resize(1200, 750)

        self._build_ui()
        self._connect_signals()
        self._apply_style()

        # Deferred load after window is shown
        self._bars_loaded.connect(self._on_bars_loaded)

    def show_and_load(self):
        """Show window and trigger initial data load."""
        self.show()
        self._load_data()

    # ── UI Construction ──────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # ── Toolbar row 1: symbol + timeframes + IV ──
        toolbar1 = QHBoxLayout()
        toolbar1.setSpacing(4)

        self._symbol_combo = QComboBox()
        self._symbol_combo.setEditable(True)
        self._symbol_combo.addItems(DEFAULT_SYMBOLS)
        self._symbol_combo.setCurrentText(self._symbol)
        self._symbol_combo.setFixedWidth(100)
        toolbar1.addWidget(self._symbol_combo)

        # Timeframe buttons
        self._tf_buttons: dict[str, QPushButton] = {}
        for tf_name in CHART_TIMEFRAMES:
            btn = QPushButton(tf_name)
            btn.setCheckable(True)
            btn.setFixedHeight(24)
            btn.setMinimumWidth(36)
            btn.clicked.connect(lambda checked, n=tf_name: self._on_tf_clicked(n))
            toolbar1.addWidget(btn)
            self._tf_buttons[tf_name] = btn

        # Highlight default TF
        if self._current_tf in self._tf_buttons:
            self._tf_buttons[self._current_tf].setChecked(True)

        toolbar1.addStretch()

        self._iv_label = QLabel("IV: --")
        self._iv_label.setStyleSheet(f"color: {COLOR_ACCENT}; font-weight: bold;")
        toolbar1.addWidget(self._iv_label)

        layout.addLayout(toolbar1)

        # ── Toolbar row 2: indicator toggles ──
        toolbar2 = QHBoxLayout()
        toolbar2.setSpacing(8)

        self._cb_ma5 = QCheckBox("MA5")
        self._cb_ma5.setChecked(True)
        self._cb_ma20 = QCheckBox("MA20")
        self._cb_ma20.setChecked(True)
        self._cb_ma50 = QCheckBox("MA50")
        self._cb_ma50.setChecked(True)
        self._cb_ma200 = QCheckBox("MA200")
        self._cb_ma200.setChecked(False)
        self._cb_vwap = QCheckBox("VWAP")
        self._cb_vwap.setChecked(True)
        self._cb_volume = QCheckBox("Volume")
        self._cb_volume.setChecked(True)

        for cb in (self._cb_ma5, self._cb_ma20, self._cb_ma50,
                   self._cb_ma200, self._cb_vwap, self._cb_volume):
            toolbar2.addWidget(cb)
            cb.toggled.connect(self._on_indicator_toggled)

        toolbar2.addStretch()
        layout.addLayout(toolbar2)

        # ── Chart area ──
        self._date_axis = _DateAxisItem(orientation="bottom")

        self._chart_widget = pg.GraphicsLayoutWidget()
        self._chart_widget.setBackground(CHART_COLOR_BG)

        # Main price plot
        self._price_plot = self._chart_widget.addPlot(
            row=0, col=0,
            axisItems={"bottom": _DateAxisItem(orientation="bottom")},
        )
        self._price_plot.showGrid(x=True, y=True, alpha=0.15)
        self._price_plot.setLabel("left", "Price")
        self._price_plot.getAxis("bottom").hide()  # hidden; volume plot shows X

        # Candlestick item
        self._candle_item = CandlestickItem(
            color_up=CHART_COLOR_CANDLE_UP,
            color_down=CHART_COLOR_CANDLE_DOWN,
        )
        self._price_plot.addItem(self._candle_item)

        # Crosshair
        self._vline = pg.InfiniteLine(angle=90, movable=False,
                                       pen=pg.mkPen(CHART_COLOR_CROSSHAIR, width=1, style=Qt.DashLine))
        self._hline = pg.InfiniteLine(angle=0, movable=False,
                                       pen=pg.mkPen(CHART_COLOR_CROSSHAIR, width=1, style=Qt.DashLine))
        self._price_plot.addItem(self._vline, ignoreBounds=True)
        self._price_plot.addItem(self._hline, ignoreBounds=True)

        # Volume plot (linked x-axis)
        self._vol_date_axis = _DateAxisItem(orientation="bottom")
        self._vol_plot = self._chart_widget.addPlot(
            row=1, col=0,
            axisItems={"bottom": self._vol_date_axis},
        )
        self._vol_plot.showGrid(x=True, y=True, alpha=0.1)
        self._vol_plot.setLabel("left", "Vol")
        self._vol_plot.setXLink(self._price_plot)
        self._vol_plot.setMaximumHeight(150)

        # Set relative heights
        self._chart_widget.ci.layout.setRowStretchFactor(0, 3)
        self._chart_widget.ci.layout.setRowStretchFactor(1, 1)

        layout.addWidget(self._chart_widget, stretch=1)

        # ── Status bar ──
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("加载中...")

        # Mouse tracking for crosshair
        self._price_plot.scene().sigMouseMoved.connect(self._on_mouse_moved)

    def _connect_signals(self):
        self._symbol_combo.lineEdit().returnPressed.connect(self._on_symbol_enter)

        # Streaming bar updates from engine
        if self._engine and hasattr(self._engine, 'bridge'):
            self._engine.bridge.historical_bar_update.connect(self._on_bar_update)

    # ── Styling ──────────────────────────────────────────────────────

    def _apply_style(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background-color: {COLOR_BG};
                color: {COLOR_TEXT};
            }}
            QPushButton {{
                background-color: {COLOR_BG_DARK};
                color: {COLOR_TEXT};
                border: 1px solid {COLOR_BORDER};
                padding: 2px 6px;
                border-radius: 3px;
            }}
            QPushButton:checked {{
                background-color: {COLOR_BG_PANEL};
                color: {COLOR_ACCENT};
                border: 1px solid {COLOR_ACCENT};
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {COLOR_BG_PANEL};
            }}
            QComboBox {{
                background-color: {COLOR_BG_DARK};
                color: {COLOR_TEXT};
                border: 1px solid {COLOR_BORDER};
                padding: 2px 6px;
                border-radius: 3px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {COLOR_BG_DARK};
                color: {COLOR_TEXT};
                selection-background-color: {COLOR_BG_PANEL};
            }}
            QCheckBox {{
                color: {COLOR_TEXT};
                spacing: 4px;
            }}
            QStatusBar {{
                background-color: {COLOR_BG_DARK};
                color: {COLOR_TEXT};
            }}
            QLabel {{
                color: {COLOR_TEXT};
            }}
        """)

    # ── Data Loading ─────────────────────────────────────────────────

    def _load_data(self):
        """Fetch historical data in a worker thread."""
        if not self._engine or not self._engine.is_connected:
            self._status.showMessage("未连接 — 无法加载数据")
            return

        # Cancel previous streaming subscription
        self._cancel_stream()

        tf_cfg = CHART_TIMEFRAMES.get(self._current_tf)
        if not tf_cfg:
            return

        bar_size, duration, keep_up = tf_cfg
        symbol = self._symbol
        self._status.showMessage(f"加载 {symbol} {self._current_tf} 数据...")

        def worker():
            try:
                req_id, bars = self._engine.request_historical_data(
                    symbol, bar_size, duration,
                    keep_up_to_date=keep_up,
                    timeout=30,
                )
                if keep_up:
                    self._req_id = req_id
                self._bars_loaded.emit(req_id, bars)
            except Exception as e:
                print(f"[Chart] Historical data error: {e}", flush=True)
                self._bars_loaded.emit(-1, [])

        threading.Thread(target=worker, daemon=True).start()

        # Subscribe to IV tick for underlying
        self._subscribe_iv()

    def _cancel_stream(self):
        """Cancel active keepUpToDate subscription."""
        if self._req_id is not None and self._engine:
            self._engine.cancel_historical_data(self._req_id)
            self._req_id = None

    def _subscribe_iv(self):
        """Subscribe to underlying tick with IV generic tick."""
        if not self._engine or not self._engine.is_connected:
            return
        try:
            app = self._engine._app
            contract = self._engine._make_underlying_contract(self._symbol)
            req_id = app.next_req_id()
            key = f"__chart_iv__{self._symbol}"
            app._tick_req_to_key[req_id] = key
            app._active_mkt_data_reqs.add(req_id)
            # genericTickList "106" = implied volatility
            app.reqMktData(req_id, contract, "106", False, False, [])
            self._iv_req_id = req_id
            self._iv_key = key
        except Exception:
            self._iv_req_id = None
            self._iv_key = None

    def _unsubscribe_iv(self):
        if hasattr(self, '_iv_req_id') and self._iv_req_id is not None:
            try:
                self._engine._app.cancelMktData(self._iv_req_id)
                self._engine._app._tick_req_to_key.pop(self._iv_req_id, None)
                self._engine._app._active_mkt_data_reqs.discard(self._iv_req_id)
            except Exception:
                pass
            self._iv_req_id = None

    # ── Data Received ────────────────────────────────────────────────

    def _on_bars_loaded(self, req_id: int, bars: list):
        if not bars:
            self._status.showMessage("无数据")
            return

        self._bars = bars
        self._render_chart()
        count = len(bars)
        self._status.showMessage(f"{self._symbol} {self._current_tf} — {count} 根K线")

    def _on_bar_update(self, req_id: int, bar: dict):
        """Handle streaming bar update."""
        if self._req_id is None or req_id != self._req_id:
            return

        if not self._bars:
            return

        # If same date, update last bar; otherwise append new bar
        last = self._bars[-1]
        if bar["date"] == last["date"]:
            self._bars[-1] = bar
        else:
            self._bars.append(bar)

        self._render_chart()

    # ── Chart Rendering ──────────────────────────────────────────────

    def _render_chart(self):
        bars = self._bars
        if not bars:
            return

        n = len(bars)
        dates = [b["date"] for b in bars]
        self._dates = dates

        opens = np.array([b["open"] for b in bars], dtype=float)
        highs = np.array([b["high"] for b in bars], dtype=float)
        lows = np.array([b["low"] for b in bars], dtype=float)
        closes = np.array([b["close"] for b in bars], dtype=float)
        volumes = np.array([b["volume"] for b in bars], dtype=float)

        # Format dates for axis display
        display_dates = self._format_dates(dates)

        # Update X-axis date labels
        price_axis = self._price_plot.getAxis("bottom")
        if isinstance(price_axis, _DateAxisItem):
            price_axis.set_dates(display_dates)
        self._vol_date_axis.set_dates(display_dates)

        # ── Candlesticks ──
        candle_data = [
            {"date_idx": i, "open": o, "high": h, "low": l, "close": c}
            for i, (o, h, l, c) in enumerate(zip(opens, highs, lows, closes))
        ]
        self._candle_item.set_data(candle_data)

        # ── MA overlays ──
        x = np.arange(n, dtype=float)
        self._update_ma("MA5", 5, closes, x, CHART_COLOR_MA5, self._cb_ma5.isChecked())
        self._update_ma("MA20", 20, closes, x, CHART_COLOR_MA20, self._cb_ma20.isChecked())
        self._update_ma("MA50", 50, closes, x, CHART_COLOR_MA50, self._cb_ma50.isChecked())
        self._update_ma("MA200", 200, closes, x, CHART_COLOR_MA200, self._cb_ma200.isChecked())

        # ── VWAP ──
        if self._vwap_plot is not None:
            self._price_plot.removeItem(self._vwap_plot)
            self._vwap_plot = None

        if self._cb_vwap.isChecked() and np.any(volumes > 0):
            vwap = IndicatorCalculator.vwap(highs, lows, closes, volumes)
            valid = ~np.isnan(vwap)
            if np.any(valid):
                self._vwap_plot = self._price_plot.plot(
                    x[valid], vwap[valid],
                    pen=pg.mkPen(CHART_COLOR_VWAP, width=1, style=Qt.DashLine),
                )

        # ── Volume bars ──
        if self._volume_item is not None:
            self._vol_plot.removeItem(self._volume_item)
            self._volume_item = None

        if self._cb_volume.isChecked() and np.any(volumes > 0):
            colors = IndicatorCalculator.volume_colors(
                opens, closes,
                color_up=CHART_COLOR_VOLUME_UP,
                color_down=CHART_COLOR_VOLUME_DOWN,
            )
            brushes = [pg.mkBrush(c) for c in colors]
            self._volume_item = pg.BarGraphItem(
                x=x, height=volumes, width=0.6, brushes=brushes,
            )
            self._vol_plot.addItem(self._volume_item)

        self._vol_plot.setVisible(self._cb_volume.isChecked())

        # ── Auto-range ──
        self._price_plot.enableAutoRange()
        self._vol_plot.enableAutoRange()

        # Update IV display
        self._update_iv_display()

    def _update_ma(self, name: str, period: int, closes: np.ndarray,
                   x: np.ndarray, color: str, visible: bool):
        """Add/update/remove a moving average line."""
        # Remove old
        if name in self._ma_plots:
            self._price_plot.removeItem(self._ma_plots[name])
            del self._ma_plots[name]

        if not visible or len(closes) < period:
            return

        ma = IndicatorCalculator.moving_average(closes, period)
        valid = ~np.isnan(ma)
        if not np.any(valid):
            return

        plot_item = self._price_plot.plot(
            x[valid], ma[valid],
            pen=pg.mkPen(color, width=1.5),
        )
        self._ma_plots[name] = plot_item

    def _format_dates(self, dates: list[str]) -> list[str]:
        """Format IBKR date strings for axis display."""
        result = []
        for d in dates:
            # IBKR formats: "20260611  09:30:00" or "20260611" or epoch
            d = d.strip()
            if len(d) >= 17 and d[8:10].strip() == "":
                # "20260611  09:30:00" format
                time_part = d[10:].strip()
                if time_part:
                    result.append(time_part[:5])  # "09:30"
                else:
                    result.append(d[:8])
            elif len(d) == 8 and d.isdigit():
                # "20260611" daily format
                result.append(f"{d[4:6]}/{d[6:8]}")
            else:
                # Fallback — try to show last meaningful part
                result.append(d[-8:] if len(d) > 8 else d)
        return result

    def _update_iv_display(self):
        """Update IV label from tick data."""
        if not hasattr(self, '_iv_key') or not self._iv_key:
            return
        try:
            d = self._engine._app._tick_data.get(self._iv_key, {})
            # tickGeneric type 24 = implied volatility (stored by tickGeneric)
            # We need to capture it; for now show what we have
            iv = d.get("impl_vol", None)
            if iv and iv > 0:
                self._iv_label.setText(f"IV: {iv:.1%}")
        except Exception:
            pass

    # ── Event Handlers ───────────────────────────────────────────────

    def _on_tf_clicked(self, tf_name: str):
        """Handle timeframe button click."""
        # Update button states
        for name, btn in self._tf_buttons.items():
            btn.setChecked(name == tf_name)

        self._current_tf = tf_name
        self._load_data()

    def _on_symbol_enter(self):
        """Handle symbol combo enter press."""
        new_symbol = self._symbol_combo.currentText().strip().upper()
        if new_symbol and new_symbol != self._symbol:
            self._symbol = new_symbol
            self.setWindowTitle(f"K线图 — {new_symbol}")
            self._load_data()

    def _on_indicator_toggled(self, checked: bool):
        """Re-render chart when an indicator checkbox changes."""
        if self._bars:
            self._render_chart()

    def _on_mouse_moved(self, pos):
        """Update crosshair and status bar with OHLCV under cursor."""
        if not self._bars:
            return

        vb = self._price_plot.vb
        if not self._price_plot.sceneBoundingRect().contains(pos):
            return

        mouse_point = vb.mapSceneToView(pos)
        x = mouse_point.x()
        y = mouse_point.y()

        self._vline.setPos(x)
        self._hline.setPos(y)

        idx = int(round(x))
        if 0 <= idx < len(self._bars):
            bar = self._bars[idx]
            date_str = bar["date"].strip()
            o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
            v = bar["volume"]
            chg = c - o
            chg_pct = (chg / o * 100) if o > 0 else 0
            sign = "+" if chg >= 0 else ""
            self._status.showMessage(
                f"{date_str}  O:{o:.2f}  H:{h:.2f}  L:{l:.2f}  C:{c:.2f}  "
                f"{sign}{chg:.2f} ({sign}{chg_pct:.2f}%)  V:{v:,}"
            )

    # ── Cleanup ──────────────────────────────────────────────────────

    def cleanup(self):
        """Cancel subscriptions before closing."""
        self._cancel_stream()
        self._unsubscribe_iv()

    def closeEvent(self, event):
        self.cleanup()
        event.accept()
