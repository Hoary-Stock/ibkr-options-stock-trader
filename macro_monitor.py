"""宏观行情监控 (macro_monitor.py) — 独立程序, clientId=13, 只读行情, 不下单。

监控:
  - **美债各期限利率** —— CBOE 收益率指数 (IRX 13周 / FVX 5年 / TNX 10年 / TYX 30年)。
  - **原油 / 黄金 / 白银** —— 连续期货 (CONTFUT: CL/GC/SI)。

每项显示「**当前价 + 1月 / 3月 / 6月 / 1年 区间最高/最低**」(由日线历史本地算)。**不画曲线**。

设计:
  - 复用 `IBKREngine` 做连接 (跟随 USE_GATEWAY 选 TWS/Gateway 端口), 但走**独立 clientId=13**;
    自定义合约 (IND / CONTFUT) 直接驱动 `engine._app` 的 `reqMktData` / `reqHistoricalData`,
    复用其 `_tick_data` (现价) 与 `_hist_data` (历史阻塞取数) 机制。
  - **默认连实盘端口**取真实行情 —— 本工具只读不下单, 且模拟盘常缺期货/指数行情
    (见上层 CLAUDE/memory「模拟盘默认无行情」)。仍可在顶栏切到模拟盘。

注意 (行情权限/换算):
  - 美债收益率指数需相应行情权限; 无权限则显示「—」或延迟数据。
  - **TNX/FVX/TYX 的指数值 = 收益率×10** → 这里按 `scale=0.1` 还原成百分比; IRX 约等于收益率
    (`scale=1.0`)。若你的数据源口径不同 (某些源 TNX 已是 4.2 而非 42), 改下面 INSTRUMENTS 的 scale。
"""

import os
import sys
import threading
from datetime import datetime

# pythonw 下无控制台 → 输出重定向到日志 (与期权 GUI 分开)
if sys.stdout is None or sys.stderr is None:
    _log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(_log_dir, exist_ok=True)
    _gw = os.environ.get("IBKR_USE_GATEWAY", "0") == "1"
    _name = f"macro_app{'_gw' if _gw else ''}_{datetime.now():%Y-%m-%d}.log"
    _log_file = open(os.path.join(_log_dir, _name), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _log_file
    print(f"\n──── Macro monitor started {datetime.now():%Y-%m-%d %H:%M:%S} ────")
else:
    os.system("")

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView, QPushButton, QLabel,
    QComboBox, QAbstractItemView,
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QIcon
from ibapi.contract import Contract

from crash_handler import install_crash_handler
install_crash_handler(sys.stderr)

from single_instance import kill_previous_instances
from ibkr_engine import IBKREngine
from models import TradingMode
from config import (
    IBKR_MACRO_CLIENT_ID, USE_GATEWAY,
    IBKR_LIVE_PORT, IBKR_PAPER_PORT, IBKR_GW_LIVE_PORT, IBKR_GW_PAPER_PORT,
    COLOR_BG, COLOR_BG_DARK, COLOR_BG_PANEL, COLOR_TEXT, COLOR_TEXT_DIM,
    COLOR_BORDER, COLOR_ACCENT, COLOR_GREEN, COLOR_RED,
)

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
APP_ICON = os.path.join(_APP_DIR, "app.ico")

# 1Y 日线大约 252 根; 各窗口取末尾 N 根计算区间高低
_WINDOWS = (("1月", 21), ("3月", 63), ("6月", 126), ("1年", 252))


def _ind(symbol: str, exchange: str = "CBOE") -> Contract:
    c = Contract()
    c.symbol = symbol
    c.secType = "IND"
    c.exchange = exchange
    c.currency = "USD"
    return c


def _contfut(symbol: str, exchange: str) -> Contract:
    c = Contract()
    c.symbol = symbol
    c.secType = "CONTFUT"
    c.exchange = exchange
    c.currency = "USD"
    return c


# key, 名称, 类别, 合约工厂, 显示换算 scale, 数值格式
INSTRUMENTS = [
    {"key": "IRX", "name": "美债 13周", "cat": "美债利率",
     "make": lambda: _ind("IRX"), "scale": 1.0, "fmt": "{:.3f}%"},
    {"key": "FVX", "name": "美债 5年", "cat": "美债利率",
     "make": lambda: _ind("FVX"), "scale": 0.1, "fmt": "{:.3f}%"},
    {"key": "TNX", "name": "美债 10年", "cat": "美债利率",
     "make": lambda: _ind("TNX"), "scale": 0.1, "fmt": "{:.3f}%"},
    {"key": "TYX", "name": "美债 30年", "cat": "美债利率",
     "make": lambda: _ind("TYX"), "scale": 0.1, "fmt": "{:.3f}%"},
    {"key": "CL", "name": "原油 WTI", "cat": "商品",
     "make": lambda: _contfut("CL", "NYMEX"), "scale": 1.0, "fmt": "${:.2f}"},
    {"key": "GC", "name": "黄金", "cat": "商品",
     "make": lambda: _contfut("GC", "COMEX"), "scale": 1.0, "fmt": "${:.1f}"},
    {"key": "SI", "name": "白银", "cat": "商品",
     "make": lambda: _contfut("SI", "COMEX"), "scale": 1.0, "fmt": "${:.3f}"},
]


def _display_port(is_live: bool) -> int:
    if USE_GATEWAY:
        return IBKR_GW_LIVE_PORT if is_live else IBKR_GW_PAPER_PORT
    return IBKR_LIVE_PORT if is_live else IBKR_PAPER_PORT


class MacroMonitor(QMainWindow):
    """美债利率 + 原油/金/银 当前价与各周期区间高低 (只读)。"""

    # 后台线程 → GUI: (key, windows{label:(hi,lo)}, last_close)
    _hist_ready = pyqtSignal(str, object, float)
    _status = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.engine = IBKREngine()
        self._live_keys: dict[str, str] = {}     # inst key -> _tick_data key
        self._windows: dict[str, dict] = {}       # inst key -> {label:(hi,lo)}
        self._last_close: dict[str, float] = {}   # inst key -> 最近日线收盘 (现价回退)
        self._row_of: dict[str, int] = {}         # inst key -> 表格行

        gw = " [GW]" if USE_GATEWAY else ""
        self.setWindowTitle(f"宏观行情监控 — 美债/原油/金银{gw}")
        self.setMinimumSize(900, 360)
        self.resize(1040, 420)
        if os.path.exists(APP_ICON):
            self.setWindowIcon(QIcon(APP_ICON))

        self._build_ui()
        self._wire()

        # 现价刷新 (读 _tick_data, 不发新请求)
        self._price_timer = QTimer(self)
        self._price_timer.timeout.connect(self._refresh_prices)
        self._price_timer.setInterval(2000)
        # 历史定时重取 (区间高低变化慢, 5 分钟一次足够)
        self._hist_timer = QTimer(self)
        self._hist_timer.timeout.connect(self._load_history_async)
        self._hist_timer.setInterval(5 * 60 * 1000)

    # ── UI ────────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(QLabel("模式:"))
        self.mode_combo = QComboBox()
        # 默认实盘 (真实行情); 只读不下单, 切实盘不弹确认
        self.mode_combo.addItem("实盘 (真实行情)", TradingMode.LIVE.value)
        self.mode_combo.addItem("IBKR模拟盘", TradingMode.IBKR_PAPER.value)
        top.addWidget(self.mode_combo)
        self.connect_btn = QPushButton("连接")
        top.addWidget(self.connect_btn)
        self.refresh_btn = QPushButton("刷新历史")
        self.refresh_btn.setEnabled(False)
        top.addWidget(self.refresh_btn)
        top.addStretch()
        self.status_label = QLabel("未连接")
        self.status_label.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        top.addWidget(self.status_label)
        root.addLayout(top)

        cols = ["类别", "名称", "现价"]
        for label, _ in _WINDOWS:
            cols += [f"{label}高", f"{label}低"]
        self.table = QTableWidget(len(INSTRUMENTS), len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setAlternatingRowColors(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for i in list(range(0, 1)) + list(range(2, len(cols))):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)

        for r, inst in enumerate(INSTRUMENTS):
            self._row_of[inst["key"]] = r
            self._set(r, 0, inst["cat"], COLOR_TEXT_DIM)
            self._set(r, 1, inst["name"], COLOR_TEXT)
            for c in range(2, len(cols)):
                self._set(r, c, "—", COLOR_TEXT_DIM)
        root.addWidget(self.table)

        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background-color: {COLOR_BG}; color: {COLOR_TEXT}; }}
            QTableWidget {{ background-color: {COLOR_BG_DARK};
                alternate-background-color: {COLOR_BG};
                gridline-color: {COLOR_BORDER}; border: 1px solid {COLOR_BORDER}; }}
            QHeaderView::section {{ background-color: {COLOR_BG_PANEL};
                color: {COLOR_TEXT}; border: 1px solid {COLOR_BORDER};
                padding: 4px; font-weight: bold; }}
            QPushButton {{ background-color: {COLOR_BG_PANEL}; color: {COLOR_ACCENT};
                border: 1px solid {COLOR_BORDER}; border-radius: 3px; padding: 4px 12px; }}
            QPushButton:hover {{ background-color: {COLOR_ACCENT}; color: {COLOR_BG}; }}
            QPushButton:disabled {{ color: #666; }}
            QComboBox {{ background-color: {COLOR_BG_DARK}; color: {COLOR_TEXT};
                border: 1px solid {COLOR_BORDER}; border-radius: 3px; padding: 3px 8px; }}
        """)

    def _set(self, row: int, col: int, text: str, color: str):
        item = self.table.item(row, col)
        if item is None:
            item = QTableWidgetItem()
            item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, col, item)
        item.setText(text)
        item.setForeground(QColor(color))

    def _wire(self):
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        self.refresh_btn.clicked.connect(self._load_history_async)
        self.engine.bridge.connected.connect(self._on_connected)
        self.engine.bridge.disconnected.connect(self._on_disconnected)
        self._hist_ready.connect(self._on_hist_ready)
        self._status.connect(self.status_label.setText)

    # ── 连接 ──────────────────────────────────────────────────────────

    def _on_connect_clicked(self):
        if self.engine.is_connected:
            self.engine.disconnect()
            return
        mode = TradingMode(self.mode_combo.currentData())
        self.connect_btn.setEnabled(False)
        self.status_label.setText("连接中...")

        def do_connect():
            ok = self.engine.connect(mode, client_id=IBKR_MACRO_CLIENT_ID)
            if not ok:
                self._status.emit("连接失败 — 确认 Gateway/TWS 已登录")
                self.engine.bridge.disconnected.emit()

        threading.Thread(target=do_connect, daemon=True).start()

    def _on_connected(self):
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("断开")
        self.refresh_btn.setEnabled(True)
        is_live = TradingMode(self.mode_combo.currentData()).is_live_port
        port = _display_port(is_live)
        self.status_label.setText(f"已连接 (端口 {port}, clientId={IBKR_MACRO_CLIENT_ID})")
        self._subscribe_live()
        self._load_history_async()
        self._price_timer.start()
        self._hist_timer.start()

    def _on_disconnected(self):
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("连接")
        self.refresh_btn.setEnabled(False)
        self._price_timer.stop()
        self._hist_timer.stop()
        self.status_label.setText("已断开")

    # ── 现价 (实时行情订阅 + 定时读缓存) ────────────────────────────────

    def _subscribe_live(self):
        app = self.engine._app
        if app is None:
            return
        for inst in INSTRUMENTS:
            try:
                key = f"__macro__{inst['key']}"
                req_id = app.next_req_id()
                contract = inst["make"]()
                app._tick_req_to_key[req_id] = key
                app._tick_data[key] = {"bid": 0.0, "ask": 0.0, "last": 0.0}
                app._active_mkt_data_reqs.add(req_id)
                # 登记到 _tick_req_contract → 若某项无实时行情(354/10168), 引擎切延迟时
                # 会连同本工具的订阅一起以同 reqId 重订 (否则指数/期货可能一直空)。
                app._tick_req_contract[req_id] = (contract, "")
                self._live_keys[inst["key"]] = key
                app.reqMktData(req_id, contract, "", False, False, [])
            except Exception as e:
                print(f"[MACRO] subscribe {inst['key']} failed: {e}", flush=True)

    def _current_price(self, inst_key: str) -> float:
        """现价: 优先实时 last, 否则 bid/ask 中价, 再退到最近日线收盘。"""
        app = self.engine._app
        d = app._tick_data.get(self._live_keys.get(inst_key, ""), {}) if app else {}
        last = d.get("last", 0.0) or 0.0
        bid = d.get("bid", 0.0) or 0.0
        ask = d.get("ask", 0.0) or 0.0
        if last > 0:
            return last
        if bid > 0 and ask > 0:
            return (bid + ask) / 2.0
        return self._last_close.get(inst_key, 0.0)

    def _refresh_prices(self):
        for inst in INSTRUMENTS:
            raw = self._current_price(inst["key"])
            row = self._row_of[inst["key"]]
            if raw > 0:
                self._set(row, 2, inst["fmt"].format(raw * inst["scale"]), COLOR_ACCENT)
            else:
                self._set(row, 2, "—", COLOR_TEXT_DIM)

    # ── 历史区间高低 (后台阻塞取日线 → 算窗口) ───────────────────────────

    def _load_history_async(self):
        if not self.engine.is_connected:
            return
        self._status.emit("拉取历史日线...")
        threading.Thread(target=self._load_history_worker, daemon=True).start()

    def _load_history_worker(self):
        for inst in INSTRUMENTS:
            bars = self._fetch_daily(inst)
            if not bars:
                self._hist_ready.emit(inst["key"], {}, 0.0)
                continue
            windows = {}
            for label, n in _WINDOWS:
                seg = bars[-n:] if len(bars) >= n else bars
                hi = max(b["high"] for b in seg)
                lo = min(b["low"] for b in seg)
                windows[label] = (hi, lo)
            last_close = bars[-1]["close"]
            self._hist_ready.emit(inst["key"], windows, last_close)
        self._status.emit(f"历史已更新 {datetime.now():%H:%M:%S}")

    def _fetch_daily(self, inst: dict):
        """阻塞取 ~1 年日线 (复用 engine._app._hist_data 事件机制)。失败返回 None。"""
        app = self.engine._app
        if app is None:
            return None
        req_id = app.next_req_id()
        app._hist_data[req_id] = {
            "bars": [], "event": threading.Event(), "error": None,
        }
        try:
            app.reqHistoricalData(
                reqId=req_id, contract=inst["make"](), endDateTime="",
                durationStr="1 Y", barSizeSetting="1 day", whatToShow="TRADES",
                useRTH=0, formatDate=1, keepUpToDate=False, chartOptions=[],
            )
        except Exception as e:
            print(f"[MACRO] hist {inst['key']} req failed: {e}", flush=True)
            app._hist_data.pop(req_id, None)
            return None
        state = app._hist_data[req_id]
        ok = state["event"].wait(timeout=30)
        bars, err = state["bars"], state["error"]
        app._hist_data.pop(req_id, None)
        if not ok:
            print(f"[MACRO] hist {inst['key']} timeout", flush=True)
            return None
        if err:
            print(f"[MACRO] hist {inst['key']} error: {err}", flush=True)
            return None
        return bars

    def _on_hist_ready(self, inst_key: str, windows: dict, last_close: float):
        self._windows[inst_key] = windows
        if last_close > 0:
            self._last_close[inst_key] = last_close
        inst = next(i for i in INSTRUMENTS if i["key"] == inst_key)
        row = self._row_of[inst_key]
        scale, fmt = inst["scale"], inst["fmt"]
        for ci, (label, _) in enumerate(_WINDOWS):
            hi_col = 3 + ci * 2
            if label in windows:
                hi, lo = windows[label]
                self._set(row, hi_col, fmt.format(hi * scale), COLOR_RED)
                self._set(row, hi_col + 1, fmt.format(lo * scale), COLOR_GREEN)
            else:
                self._set(row, hi_col, "—", COLOR_TEXT_DIM)
                self._set(row, hi_col + 1, "—", COLOR_TEXT_DIM)
        # 现价缺实时时, 用最近收盘兜底刷新一次
        self._refresh_prices()

    def closeEvent(self, event):
        self._price_timer.stop()
        self._hist_timer.stop()
        try:
            self.engine.disconnect()
        except Exception:
            pass
        event.accept()


def main():
    kill_previous_instances(__file__)
    app = QApplication(sys.argv)
    if os.path.exists(APP_ICON):
        app.setWindowIcon(QIcon(APP_ICON))
    win = MacroMonitor()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
