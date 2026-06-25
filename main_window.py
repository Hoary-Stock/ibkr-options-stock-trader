"""Main window layout — assembles all widgets."""

import threading
from datetime import datetime

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTabWidget, QMessageBox, QStatusBar,
    QPushButton, QLabel, QApplication,
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QEvent, QSettings

from config import (
    COLOR_BG, COLOR_BG_DARK, COLOR_BG_PANEL, COLOR_TEXT,
    COLOR_BORDER, COLOR_ACCENT, COLOR_GREEN, COLOR_RED,
    SPX_SESSION_GTH_START, SPX_SESSION_GTH_END,
    SPX_SESSION_RTH_START, SPX_SESSION_RTH_END,
    DATA_CONNECTION_ERROR_CODES,
)
from config import FUTURES_SPECS, FUTURES_MAX_EXPIRIES
from models import OptionInfo, OrderAction, OrderType, TradingMode
from ibkr_engine import IBKREngine
from paper_engine import PaperEngine
from conditional_orders import ConditionalOrderManager
from sound_alerts import play_fill
from widgets.symbol_bar import SymbolBar
from widgets.option_chain import OptionChainWidget
from widgets.price_ladder import PriceLadder
from widgets.position_panel import PositionPanel
from widgets.order_panel import OrderPanel
from widgets.account_bar import AccountBar
from widgets.option_calculator import OptionCalculator
from widgets.strategy_window import StrategyPanel
# ChartWindow is imported lazily (first chart open) — it pulls in
# numpy + pyqtgraph (~25MB), which shouldn't load at startup


DARK_STYLESHEET = f"""
    QMainWindow, QWidget {{
        background-color: {COLOR_BG};
        color: {COLOR_TEXT};
    }}
    QTableWidget {{
        background-color: {COLOR_BG_DARK};
        alternate-background-color: {COLOR_BG};
        color: {COLOR_TEXT};
        gridline-color: {COLOR_BORDER};
        border: 1px solid {COLOR_BORDER};
        selection-background-color: {COLOR_BG_PANEL};
    }}
    QTableWidget::item {{
        padding: 2px 4px;
    }}
    QHeaderView::section {{
        background-color: {COLOR_BG_PANEL};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER};
        padding: 4px;
        font-weight: bold;
    }}
    QTabWidget::pane {{
        border: 1px solid {COLOR_BORDER};
        background-color: {COLOR_BG_DARK};
    }}
    QTabBar::tab {{
        background-color: {COLOR_BG_DARK};
        color: {COLOR_TEXT};
        padding: 6px 12px;
        border: 1px solid {COLOR_BORDER};
        border-bottom: none;
        margin-right: 2px;
    }}
    QTabBar::tab:selected {{
        background-color: {COLOR_BG_PANEL};
        color: {COLOR_ACCENT};
        font-weight: bold;
    }}
    QLineEdit {{
        background-color: {COLOR_BG_DARK};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER};
        padding: 4px 8px;
        border-radius: 3px;
    }}
    QComboBox {{
        background-color: {COLOR_BG_DARK};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER};
        padding: 4px 8px;
        border-radius: 3px;
    }}
    QComboBox::drop-down {{
        border: none;
    }}
    QComboBox QAbstractItemView {{
        background-color: {COLOR_BG_DARK};
        color: {COLOR_TEXT};
        selection-background-color: {COLOR_BG_PANEL};
    }}
    QSpinBox {{
        background-color: {COLOR_BG_DARK};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER};
        padding: 4px;
        border-radius: 3px;
    }}
    QLabel {{
        color: {COLOR_TEXT};
    }}
    QSplitter::handle {{
        background-color: {COLOR_BORDER};
    }}
    QScrollArea {{
        border: none;
    }}
    QScrollBar:vertical {{
        background-color: {COLOR_BG_DARK};
        width: 10px;
    }}
    QScrollBar::handle:vertical {{
        background-color: {COLOR_BORDER};
        border-radius: 4px;
        min-height: 20px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QStatusBar {{
        background-color: {COLOR_BG_DARK};
        color: {COLOR_TEXT};
    }}
"""


class MainWindow(QMainWindow):
    """Main application window."""

    _search_validated = pyqtSignal(object)  # OptionInfo — validated search result
    _futures_resolved = pyqtSignal(object)  # (symbol, contracts) — futures resolved off-thread

    def __init__(self):
        super().__init__()
        self.setWindowTitle("IBKR 点价交易")
        # 较小的最小尺寸 → 窗口可上下左右自由缩放; 实际大小由 _restore_layout 恢复
        self.setMinimumSize(900, 600)
        self.resize(1400, 900)

        # 记忆窗口大小与各 splitter 分割位置 (跨会话持久化)
        self._settings = QSettings("MoneyTrader", "ibkr_options_gui")

        # Engines
        self.ibkr_engine = IBKREngine()
        self.paper_engine = PaperEngine(self.ibkr_engine)
        self._active_engine = self.paper_engine  # Default to paper

        # 本地条件单 (止盈/止损) 管理器 —— 回调用 self._active_engine (随模式切换)
        self.cond_manager = ConditionalOrderManager(self)
        self.cond_manager.configure(
            get_tick=lambda key: self._active_engine.get_tick(key),
            place=lambda opt, action, lmt, qty, outside: self._place_order(
                opt, OrderAction(action), OrderType.LIMIT, lmt, qty, outside),
            subscribe=lambda opt: self._active_engine.subscribe_option_tick(opt),
            unsubscribe=lambda req_id: self._active_engine.unsubscribe_tick(req_id),
        )

        self._current_symbol = "SPY"
        self._current_option: OptionInfo | None = None
        self._instrument = "OPT"   # "OPT"(默认) / "STK" / "FUT"
        self._future_expiries: list = []  # 当前期货标的的合约月份 [{expiry,con_id,...}]
        self._chart_windows: list = []  # list[ChartWindow]

        # Detachable price ladder state
        self._ladder_detached = False
        self._ladder_window: QMainWindow | None = None
        self._embedded_chart = None  # ChartWindow | None

        self._build_ui()
        self._connect_signals()

        self.setStyleSheet(DARK_STYLESHEET)
        self.statusBar().showMessage("就绪 — 点击「连接」开始")

        # Session indicator timer (updates every 10 seconds)
        self._session_timer = QTimer()
        self._session_timer.timeout.connect(self._update_session_indicator)
        self._session_timer.start(10_000)
        self._update_session_indicator()

        # 恢复上次的窗口大小与各分割位置 (若有)
        self._restore_layout()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # ── Top bar ──
        top_bar_layout = QHBoxLayout()

        self.symbol_bar = SymbolBar()
        top_bar_layout.addWidget(self.symbol_bar, stretch=1)

        self._chart_btn = QPushButton("K线图")
        self._chart_btn.setFixedHeight(30)
        self._chart_btn.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_BG_PANEL}; color: {COLOR_ACCENT}; "
            f"border: 1px solid {COLOR_BORDER}; padding: 2px 12px; border-radius: 3px; "
            f"font-weight: bold; }}"
            f"QPushButton:hover {{ background-color: {COLOR_ACCENT}; color: {COLOR_BG}; }}"
        )
        self._chart_btn.clicked.connect(self._on_open_chart)
        top_bar_layout.addWidget(self._chart_btn)

        # Session indicator (shows current market session for SPX options)
        self._session_label = QLabel("--")
        self._session_label.setFixedHeight(30)
        self._session_label.setStyleSheet(
            f"color: {COLOR_TEXT}; background-color: {COLOR_BG_PANEL}; "
            f"border: 1px solid {COLOR_BORDER}; padding: 2px 10px; "
            f"border-radius: 3px; font-size: 12px; font-weight: bold;"
        )
        self._session_label.setToolTip(
            "SPX 期权交易时段 (ET)\n"
            "GTH 夜盘: 20:15 - 09:15\n"
            "RTH 正常盘: 09:30 - 16:15"
        )
        top_bar_layout.addWidget(self._session_label)

        main_layout.addLayout(top_bar_layout)

        # ── Account bar ──
        self.account_bar = AccountBar()
        main_layout.addWidget(self.account_bar)

        # ── Main content: vertical splitter ──
        self.main_splitter = QSplitter(Qt.Vertical)

        # Top: Option chain
        self.option_chain = OptionChainWidget()
        self.main_splitter.addWidget(self.option_chain)

        # Bottom: horizontal splitter (price ladder | position/order panels)
        self.bottom_splitter = QSplitter(Qt.Horizontal)

        # Left: Price ladder
        self.price_ladder = PriceLadder()
        self.bottom_splitter.addWidget(self.price_ladder)

        # Right: Position + Order tabs (top) + 期权理论价计算器 (bottom-right corner)
        self.right_tabs = QTabWidget()
        self.position_panel = PositionPanel()
        self.order_panel = OrderPanel()
        self.right_tabs.addTab(self.position_panel, "持仓")
        self.right_tabs.addTab(self.order_panel, "委托")

        self.right_splitter = QSplitter(Qt.Vertical)
        self.right_splitter.addWidget(self.right_tabs)
        self.calculator = OptionCalculator()
        self.right_splitter.addWidget(self.calculator)
        self.right_splitter.setSizes([520, 300])
        # 右侧竖向: 持仓/委托 Tab 主要吸收增长, 计算器小幅跟随
        self.right_splitter.setStretchFactor(0, 5)
        self.right_splitter.setStretchFactor(1, 2)
        self.right_splitter.setChildrenCollapsible(False)
        self.bottom_splitter.addWidget(self.right_splitter)

        self.bottom_splitter.setSizes([380, 500])
        # 下方横向: 点价梯与右侧面板按 4:5 比例联动缩放
        self.bottom_splitter.setStretchFactor(0, 4)
        self.bottom_splitter.setStretchFactor(1, 5)
        self.bottom_splitter.setChildrenCollapsible(False)
        self.main_splitter.addWidget(self.bottom_splitter)

        self.main_splitter.setSizes([400, 400])
        # 主竖向: 期权链与下方区域等比例联动缩放
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setChildrenCollapsible(False)

        # ── 中央: 顶层 Tab —「单腿点价」(现有点价梯) /「多腿组合」(策略组合) ──
        # 两个模块相互独立: 单腿点价用左侧点价梯; 多腿组合是嵌入的策略生成器。
        self.center_tabs = QTabWidget()

        single_leg_tab = QWidget()
        single_leg_layout = QVBoxLayout(single_leg_tab)
        single_leg_layout.setContentsMargins(0, 0, 0, 0)
        single_leg_layout.addWidget(self.main_splitter)
        self.center_tabs.addTab(single_leg_tab, "单腿点价")

        # 多腿组合面板 (懒加载期权链: 首次切到该 Tab 才拉)
        self.strategy_panel = StrategyPanel(symbol=self._current_symbol)
        self.center_tabs.addTab(self.strategy_panel, "多腿组合")

        main_layout.addWidget(self.center_tabs)

    def _connect_signals(self):
        # Symbol bar
        self.symbol_bar.connect_clicked.connect(self._on_connect)
        self.symbol_bar.disconnect_clicked.connect(self._on_disconnect)
        self.symbol_bar.symbol_changed.connect(self._on_symbol_changed)
        self.symbol_bar.mode_changed.connect(self._on_mode_changed)
        self.symbol_bar.reconnect_requested.connect(self._on_reconnect_requested)
        self.symbol_bar.instrument_changed.connect(self._on_instrument_changed)
        self.symbol_bar.future_expiry_changed.connect(self._on_future_expiry_changed)

        # Option chain -> price ladder
        self.option_chain.option_selected.connect(self._on_option_selected)

        # Price ladder -> order (limit orders from price clicks)
        self.price_ladder.order_requested.connect(self._on_order_requested)

        # Price ladder -> market orders
        self.price_ladder.market_order_requested.connect(self._on_market_order_requested)

        # Price ladder -> close position
        self.price_ladder.close_position_requested.connect(self._on_close_position_requested)

        # Price ladder -> cancel all
        self.price_ladder.cancel_all_requested.connect(self._on_cancel_all_requested)

        # Price ladder -> detach
        self.price_ladder.detach_requested.connect(self._on_detach_ladder)

        # Price ladder -> 条件单 (止盈/止损)
        self.price_ladder.conditional_requested.connect(self._on_conditional_requested)
        self.price_ladder.conditional_cancel_requested.connect(self._on_conditional_cancel)
        self.cond_manager.changed.connect(self._refresh_conditionals)
        self.cond_manager.triggered.connect(self._on_conditional_triggered)
        self.cond_manager.failed.connect(self._on_conditional_failed)
        # 点价梯换合约后刷新该合约的条件单显示
        self.price_ladder.option_loaded.connect(self._refresh_conditionals)

        # Price ladder -> contract search
        self.price_ladder.contract_searched.connect(self._on_contract_searched)
        self._search_validated.connect(self._load_validated_contract)
        self._futures_resolved.connect(self._apply_futures_contracts)

        # Position panel -> open ladder
        self.position_panel.position_clicked.connect(self._on_option_selected)

        # 双击委托/交易记录的合约 -> 跳到该标的并加载到点价梯
        self.order_panel.option_selected.connect(self._on_option_selected)

        # Order panel -> cancel
        self.order_panel.cancel_requested.connect(self._on_cancel_order)

        # 中央 Tab 切换 -> 进入「多腿组合」时懒加载期权链
        self.center_tabs.currentChanged.connect(self._on_center_tab_changed)

        # IBKR engine signals
        self.ibkr_engine.bridge.connected.connect(self._on_connected)
        self.ibkr_engine.bridge.disconnected.connect(self._on_disconnected)
        self.ibkr_engine.bridge.error_received.connect(self._on_error)
        self.ibkr_engine.bridge.order_rejected.connect(self._on_order_rejected)
        self.ibkr_engine.bridge.pnl_single_updated.connect(
            self.position_panel.on_pnl_single
        )

        # IBKR account/portfolio signals
        self.ibkr_engine.bridge.account_summary_updated.connect(self.account_bar.update_account)
        self.ibkr_engine.bridge.pnl_updated.connect(self.account_bar.update_daily_pnl)
        self.ibkr_engine.bridge.computed_daily_pnl.connect(
            self.account_bar.on_computed_daily
        )
        self.ibkr_engine.bridge.portfolio_position_received.connect(
            self.position_panel.on_portfolio_position
        )
        self.ibkr_engine.bridge.portfolio_positions_end.connect(
            self.position_panel.on_portfolio_positions_end
        )
        self.ibkr_engine.bridge.account_summary_end.connect(self._on_account_summary_end)
        self.ibkr_engine.bridge.currency_balance_updated.connect(
            self.account_bar.on_currency_balance
        )
        # 真正成交 → 提示音 (仅真实引擎; 本地模拟不响)
        self.ibkr_engine.bridge.execution_received.connect(self._on_fill_sound)

        # Paper engine signals
        self.paper_engine.bridge.error_received.connect(self._on_error)
        self.paper_engine.bridge.account_summary_updated.connect(self.account_bar.update_account)
        self.paper_engine.bridge.pnl_updated.connect(self.account_bar.update_daily_pnl)
        self.paper_engine.bridge.computed_daily_pnl.connect(
            self.account_bar.on_computed_daily
        )
        self.paper_engine.bridge.currency_balance_updated.connect(
            self.account_bar.on_currency_balance
        )

    # ── Connection ────────────────────────────────────────────────────

    def _on_connect(self):
        mode = self.symbol_bar.get_mode()
        self.statusBar().showMessage(f"正在连接 ({mode.label})...")

        # Connect in background thread
        def do_connect():
            success = self.ibkr_engine.connect(mode)
            if not success:
                self.ibkr_engine.bridge.error_received.emit(
                    -1, -1, "连接失败 — 请确认 Gateway/TWS 已登录。若刚才还能用、"
                    "现在连不上, 多半是 Gateway 卡死(JTS死锁), 请重启 Gateway 后再连。"
                )

        threading.Thread(target=do_connect, daemon=True).start()

    def _on_disconnect(self):
        self.account_bar.stop()
        self.option_chain.cleanup()
        self.ibkr_engine.disconnect()

    def _on_connected(self):
        # 结束"切换中"状态 → 重新启用模式下拉 + 标的输入框 (热切换后必须复位,
        # 否则切到模拟/实盘后这俩控件一直禁用, 无法再改标的/再切回)。
        self.symbol_bar.set_switching(False)
        mode = self.ibkr_engine.mode
        # 本地模拟走 PaperEngine; IBKR模拟盘 / 实盘都走真实 IBKR 引擎 (真实发单)
        if mode.uses_ibkr_engine:
            self._active_engine = self.ibkr_engine
        else:
            self._active_engine = self.paper_engine

        self.symbol_bar.set_connected(True, mode)
        self.symbol_bar.set_engine(self.ibkr_engine)
        self.option_chain.set_engine(self._active_engine)
        self.price_ladder.set_engine(self._active_engine)
        self.position_panel.set_engine(self._active_engine)
        self.order_panel.set_engine(self._active_engine)
        self.account_bar.set_engine(self._active_engine)
        self.calculator.set_engine(self._active_engine)
        self.strategy_panel.set_engine(self._active_engine)
        # 若当前正停在「多腿组合」Tab, 立即加载期权链
        if self.center_tabs.currentWidget() is self.strategy_panel:
            self.strategy_panel.ensure_loaded()

        self.statusBar().setStyleSheet("")
        self.statusBar().showMessage(f"已连接 ({mode.label})")

        # Request account data
        self._active_engine.request_account_summary()
        self.ibkr_engine.request_positions()
        self.account_bar.start()

        # 恢复本地条件单 (从磁盘) 并重新订阅各合约行情
        self.cond_manager.resume()

        # 按当前交易品种加载 (期权链 / 正股伪合约 / 期货合约)
        self._load_current_instrument()

    def _on_disconnected(self):
        # 复位"切换中"状态, 避免切换失败后控件卡在禁用
        self.symbol_bar.set_switching(False)
        self.symbol_bar.set_connected(False)
        self.account_bar.stop()
        # 退订条件单行情 (保留条件单本身, 重连后 resume)
        self.cond_manager.suspend()
        self.statusBar().setStyleSheet(
            f"QStatusBar {{ color: {COLOR_RED}; }}"
        )
        self.statusBar().showMessage("已断开连接")

    def _on_fill_sound(self, order_id: int, side: str, qty: float, price: float):
        """真正成交回报 → 播放提示音 (后台线程, 不阻塞 GUI)。"""
        play_fill(side)

    def _on_account_summary_end(self):
        """After first account summary, request PnL (needs account name)."""
        self._active_engine.request_pnl()

    # ── Symbol / Mode ─────────────────────────────────────────────────

    def _on_symbol_changed(self, symbol: str):
        self._current_symbol = symbol
        # 多腿组合面板跟随标的 (重置已加载状态; 若该 Tab 正显示则重载)
        self.strategy_panel.set_symbol(symbol)
        if self.ibkr_engine.is_connected:
            self._load_current_instrument()

    def _on_center_tab_changed(self, index: int):
        """切到「多腿组合」Tab → 懒加载期权链 (已连接且未加载时)。"""
        if (self.center_tabs.widget(index) is self.strategy_panel
                and self.ibkr_engine.is_connected):
            self.strategy_panel.set_engine(self._active_engine)
            self.strategy_panel.ensure_loaded()

    # ── Instrument type (期权/正股/期货) ────────────────────────────────

    def _on_instrument_changed(self, kind: str):
        """顶栏「类型」切换: 期权 / 正股 / 期货。"""
        if kind == self._instrument:
            return
        self._instrument = kind
        # 期权链仅在期权模式显示
        self.option_chain.setVisible(kind == "OPT")
        # 持仓筛选随品种切换
        self.position_panel.set_filter(
            {"OPT": "期权", "STK": "正股/ETF", "FUT": "期货"}[kind]
        )
        # 清空期货合约月份缓存 (重新解析)
        if kind != "FUT":
            self._future_expiries = []
        # 切到期货且当前标的不是期货根代码 → 默认填一个 (ES), 避免提示"不在列表"
        if kind == "FUT" and self._current_symbol.upper() not in FUTURES_SPECS:
            default_fut = "ES" if "ES" in FUTURES_SPECS else next(iter(FUTURES_SPECS))
            self._current_symbol = default_fut
            self.symbol_bar.set_symbol(default_fut)
        if self.ibkr_engine.is_connected:
            self._load_current_instrument()

    def _load_current_instrument(self):
        """按当前 _instrument 加载: 期权链 / 正股伪合约 / 期货合约到点价梯。"""
        sym = self._current_symbol
        if self._instrument == "OPT":
            self._load_option_chain(sym)
        elif self._instrument == "STK":
            self._load_stock_into_ladder(sym)
        elif self._instrument == "FUT":
            self._load_futures(sym)

    def _load_stock_into_ladder(self, symbol: str):
        """正股: 直接用伪合约 (right='STK') 载入点价梯 (复用 stock_trader 模式)。"""
        from config import INDEX_SYMBOLS
        if symbol in INDEX_SYMBOLS:
            self.statusBar().showMessage(f"{symbol} 是指数, 不能直接交易正股")
            return
        pseudo = OptionInfo(symbol=symbol, expiry="", strike=0.0, right="STK")
        self.price_ladder.set_option(pseudo)
        self.calculator.set_option(pseudo)
        self.symbol_bar.set_current_option(pseudo.display_name)
        self.statusBar().showMessage(f"正股: 已加载 {symbol}")

    def _load_futures(self, symbol: str):
        """期货: 后台解析近月起若干合约 → 填充月份下拉 → 默认载入近月。"""
        symbol = symbol.upper()
        if symbol not in FUTURES_SPECS:
            avail = ", ".join(FUTURES_SPECS.keys())
            self.statusBar().showMessage(
                f"{symbol} 不在内置期货列表; 可用: {avail}"
            )
            self.symbol_bar.populate_future_expiries([])
            return

        self.statusBar().showMessage(f"解析 {symbol} 期货合约...")

        def do_resolve():
            try:
                contracts = self.ibkr_engine.resolve_futures_contracts(
                    symbol, max_count=FUTURES_MAX_EXPIRIES
                )
                if not contracts:
                    self.ibkr_engine.bridge.error_received.emit(
                        -1, -1, f"{symbol} 无可用期货合约"
                    )
                    return
                # 跳回 GUI 线程填充下拉并载入近月
                self._futures_resolved.emit((symbol, contracts))
            except Exception as e:
                self.ibkr_engine.bridge.error_received.emit(-1, -1, f"期货解析失败: {e}")

        threading.Thread(target=do_resolve, daemon=True).start()

    def _apply_futures_contracts(self, payload):
        """在 GUI 线程: 填充期货月份下拉, 默认载入近月。payload=(symbol, contracts)。"""
        symbol, contracts = payload
        # 用户可能在解析期间又切走了品种/标的 — 丢弃过期结果
        if self._instrument != "FUT" or symbol != self._current_symbol.upper():
            return
        self._future_expiries = contracts
        items = []
        for i, c in enumerate(contracts):
            exp = c["expiry"]
            mon = exp[:6] if len(exp) >= 6 else exp
            mon_disp = f"{mon[:4]}-{mon[4:6]}" if len(mon) >= 6 else mon
            tag = " (近月)" if i == 0 else (" (季月)" if i == 1 else "")
            items.append((f"{mon_disp}{tag}", exp))
        self.symbol_bar.populate_future_expiries(items)
        # 默认载入近月
        self._load_future_contract(symbol, contracts[0]["expiry"])

    def _load_future_contract(self, symbol: str, expiry: str):
        """把指定期货合约月份载入点价梯。"""
        con_id = 0
        for c in self._future_expiries:
            if c["expiry"] == expiry:
                con_id = c.get("con_id", 0)
                break
        pseudo = OptionInfo(symbol=symbol, expiry=expiry, strike=0.0,
                            right="FUT", con_id=con_id)
        self.price_ladder.set_option(pseudo)
        self.calculator.set_option(pseudo)
        self.symbol_bar.set_current_option(pseudo.display_name)
        self.statusBar().showMessage(f"期货: 已加载 {pseudo.display_name}")

    def _on_future_expiry_changed(self, expiry: str):
        """用户切换期货合约月份下拉。"""
        if self._instrument == "FUT" and expiry:
            self._load_future_contract(self._current_symbol, expiry)

    def _on_mode_changed(self, mode_value: str):
        """Handle mode change before connection."""
        mode = TradingMode(mode_value)
        if self.ibkr_engine.is_connected:
            if mode.uses_ibkr_engine:
                self._active_engine = self.ibkr_engine
            else:
                self._active_engine = self.paper_engine
            self.option_chain.set_engine(self._active_engine)
            self.price_ladder.set_engine(self._active_engine)
            self.position_panel.set_engine(self._active_engine)
            self.order_panel.set_engine(self._active_engine)
            self.account_bar.set_engine(self._active_engine)
            self.calculator.set_engine(self._active_engine)
            self.strategy_panel.set_engine(self._active_engine)

    def _on_reconnect_requested(self, mode_value: str):
        """Handle hot switch: disconnect and reconnect to different port."""
        mode = TradingMode(mode_value)
        self.statusBar().showMessage(f"切换到 {mode.label} 模式...")
        self.symbol_bar.set_switching(True)
        self.account_bar.stop()

        def do_reconnect():
            success = self.ibkr_engine.reconnect(mode)
            if not success:
                self.ibkr_engine.bridge.error_received.emit(
                    -1, -1, f"切到 {mode.label} 失败 — 若 Gateway 刚才还能用现在连不上, "
                    "多半是 Gateway 卡死(JTS死锁)或旧连接未释放, 请重启 Gateway 后再连。"
                )
                self.ibkr_engine.bridge.disconnected.emit()

        threading.Thread(target=do_reconnect, daemon=True).start()

    # ── Option Chain Loading ──────────────────────────────────────────

    def _load_option_chain(self, symbol: str):
        self.statusBar().showMessage(f"加载 {symbol} 期权链...")

        def do_load():
            try:
                print(f"[DEBUG] Loading option chain for {symbol}...", flush=True)
                expirations, strikes = self.ibkr_engine.request_option_chain(symbol)
                print(f"[DEBUG] Got {len(expirations)} expirations, {len(strikes)} strikes", flush=True)

                if not expirations or not strikes:
                    self.ibkr_engine.bridge.error_received.emit(
                        -1, -1, f"{symbol} 期权链为空"
                    )
                    return

                # Get stock price via a one-shot tick subscription
                stock_price = self._fetch_stock_price(symbol)
                print(f"[DEBUG] Stock price for {symbol}: {stock_price}", flush=True)

                # Update UI on main thread via signal
                self.ibkr_engine.bridge.chain_ready.emit(expirations, strikes)
                # Store for the callback
                self._pending_chain = (symbol, expirations, strikes, stock_price)
            except Exception as e:
                print(f"[DEBUG] Option chain error: {e}", flush=True)
                self.ibkr_engine.bridge.error_received.emit(-1, -1, str(e))

        # Connect chain_ready to update (one-shot)
        try:
            self.ibkr_engine.bridge.chain_ready.disconnect()
        except TypeError:
            pass

        def on_chain_ready(expirations, strikes):
            data = getattr(self, '_pending_chain', None)
            if data:
                sym, exps, stks, price = data
                print(f"[DEBUG] on_chain_ready: {sym}, price={price}, "
                      f"{len(exps)} exp, {len(stks)} strikes", flush=True)
                self.option_chain.load_chain(sym, exps, stks, stock_price=price)
                self.statusBar().showMessage(
                    f"{sym} 期权链已加载: {len(exps)} 个到期日, "
                    f"{len(stks)} 个行权价 (股价=${price:.2f})"
                )
            try:
                self.ibkr_engine.bridge.chain_ready.disconnect(on_chain_ready)
            except TypeError:
                pass

        self.ibkr_engine.bridge.chain_ready.connect(on_chain_ready)
        threading.Thread(target=do_load, daemon=True).start()

    def _fetch_stock_price(self, symbol: str) -> float:
        """Subscribe to underlying stock price and return initial value.

        The subscription stays alive so the option chain title can display
        a continuously-updating price.  The tick data key is
        ``__stock__{symbol}`` inside ``app._tick_data``.
        """
        import time

        app = self.ibkr_engine._app
        key = f"__stock__{symbol}"

        # Cancel previous underlying subscription if any
        old_req = getattr(self, '_stock_price_req_id', None)
        if old_req is not None:
            try:
                app.cancelMktData(old_req)
            except Exception:
                pass
            app._active_mkt_data_reqs.discard(old_req)

        contract = IBKREngine._make_underlying_contract(symbol)
        req_id = app.next_req_id()
        self._stock_price_req_id = req_id
        self._stock_price_key = key
        app._tick_req_to_key[req_id] = key
        app._tick_data[key] = {"bid": 0.0, "ask": 0.0, "last": 0.0}
        app._active_mkt_data_reqs.add(req_id)
        app.reqMktData(req_id, contract, "", False, False, [])

        # Wait up to 5 seconds for initial price
        for _ in range(50):
            time.sleep(0.1)
            d = app._tick_data.get(key, {})
            if d.get("last", 0) > 0 or d.get("bid", 0) > 0:
                break

        # Return initial price (subscription stays alive)
        d = app._tick_data.get(key, {})
        last = d.get("last", 0)
        bid = d.get("bid", 0)
        ask = d.get("ask", 0)
        if last > 0:
            return last
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
        if bid > 0:
            return bid
        if ask > 0:
            return ask
        return 0.0

    # ── Option Selected ───────────────────────────────────────────────

    def _set_instrument_ui(self, kind: str):
        """同步「类型」下拉 + 期权链可见性 + 持仓筛选 (不触发重新加载)。"""
        if self._instrument != kind:
            self._instrument = kind
            self.symbol_bar.set_instrument(kind)
            self.option_chain.setVisible(kind == "OPT")
            self.position_panel.set_filter(
                {"OPT": "期权", "STK": "正股/ETF", "FUT": "期货"}[kind]
            )

    def _on_option_selected(self, option: OptionInfo):
        self._current_option = option
        right = getattr(option, "right", "")
        sym = getattr(option, "symbol", "")

        # 正股 / 期货合约 (多来自双击对应持仓): 切到该品种模式并直接载入点价梯,
        # 不加载期权链
        if right in ("STK", "FUT"):
            if sym:
                self._current_symbol = sym
                self.symbol_bar.set_symbol(sym)
            self._set_instrument_ui(right)
            self.price_ladder.set_option(option)
            self.calculator.set_option(option)
            self.symbol_bar.set_current_option(option.display_name)
            self.statusBar().showMessage(f"已选择: {option.display_name}")
            return

        # 期权 (C/P): 若属于另一个标的则切换标的并重载期权链 (并切回期权模式)
        if sym and right != "COMBO" and (sym != self._current_symbol
                                         or self._instrument != "OPT"):
            self._current_symbol = sym
            self.symbol_bar.set_symbol(sym)
            self._set_instrument_ui("OPT")
            if self.ibkr_engine.is_connected:
                self._load_option_chain(sym)
        # 加载到点价梯 + 计算器 (点价交易界面就在左侧, 一直可见)
        self.price_ladder.set_option(option)
        self.calculator.set_option(option)
        self.symbol_bar.set_current_option(option.display_name)
        self.statusBar().showMessage(f"已选择: {option.display_name}")

    # ── Contract Search (from price ladder search bar) ────────────────

    def _on_contract_searched(self, option: OptionInfo):
        """Handle contract search — validate contract exists before loading."""
        if not self.ibkr_engine.is_connected:
            self.statusBar().showMessage("未连接 — 无法搜索合约")
            return

        self.statusBar().showMessage(f"验证合约: {option.display_name}...")
        self.price_ladder.contract_label.setText(f"验证中: {option.display_name}...")

        def do_validate():
            try:
                contract = IBKREngine._make_option_contract(
                    option.symbol, option.expiry, option.strike, option.right
                )
                app = self.ibkr_engine._app
                req_id = app.next_req_id()
                app._contract_data[req_id] = {
                    "details": [], "event": threading.Event(), "error": None,
                }
                app.reqContractDetails(req_id, contract)

                state = app._contract_data[req_id]
                if not state["event"].wait(timeout=5):
                    self.ibkr_engine.bridge.error_received.emit(
                        -1, -1, f"合约验证超时: {option.display_name}"
                    )
                    app._contract_data.pop(req_id, None)
                    return

                if state["error"] or not state["details"]:
                    self.ibkr_engine.bridge.error_received.emit(
                        -1, -1, f"合约不存在: {option.display_name}"
                    )
                    app._contract_data.pop(req_id, None)
                    return

                app._contract_data.pop(req_id, None)
                # Valid — notify GUI thread
                self._search_validated.emit(option)

            except Exception as e:
                self.ibkr_engine.bridge.error_received.emit(-1, -1, f"搜索错误: {e}")

        threading.Thread(target=do_validate, daemon=True).start()

    def _load_validated_contract(self, option: OptionInfo):
        """Load a validated searched contract into the price ladder."""
        self._current_option = option

        # Subscribe to tick data
        if self._active_engine and self.ibkr_engine.is_connected:
            key = option.to_ibkr_key()
            if not any(k == key for k in self.ibkr_engine._app._tick_req_to_key.values()):
                self._active_engine.subscribe_option_tick(option)

        self.price_ladder.set_option(option)
        self.calculator.set_option(option)
        self.symbol_bar.set_current_option(option.display_name)
        self.statusBar().showMessage(f"已加载: {option.display_name}")

    # ── Order Handling ────────────────────────────────────────────────

    def _unit_for(self, option: OptionInfo) -> str:
        """下单数量单位: 期货=手, 正股=股, 期权=张。"""
        return {"FUT": "手", "STK": "股"}.get(option.right, "张")

    def _place_order(self, option: OptionInfo, action: OrderAction,
                     order_type: OrderType, price: float, qty: int,
                     outside_rth: bool) -> int:
        """按品种路由下单: 期权 / 正股 / 期货, 返回 orderId。"""
        eng = self._active_engine
        if option.right in ("C", "P"):
            if order_type == OrderType.LIMIT:
                return eng.place_limit_order(option, action, qty, price,
                                             outside_rth=outside_rth)
            return eng.place_market_order(option, action, qty,
                                          outside_rth=outside_rth)
        if option.right == "STK":
            return eng.place_stock_order(option.symbol, action, qty, price,
                                         order_type=order_type,
                                         outside_rth=outside_rth)
        if option.right == "FUT":
            return eng.place_futures_order(option.symbol, option.expiry, action,
                                           qty, price, order_type=order_type,
                                           outside_rth=outside_rth)
        return -1

    def _on_order_requested(self, option: OptionInfo, action_str: str, price: float):
        action = OrderAction.BUY if action_str == "BUY" else OrderAction.SELL
        qty = self.price_ladder.get_quantity()
        outside_rth = self.price_ladder.get_outside_rth()

        order_id = self._place_order(option, action, OrderType.LIMIT, price, qty,
                                     outside_rth)
        if order_id > 0:
            action_text = "买入" if action == OrderAction.BUY else "卖出"
            rth_tag = " [盘外]" if outside_rth else ""
            self.statusBar().showMessage(
                f"已提交: {action_text} {qty}{self._unit_for(option)} "
                f"{option.display_name} @ ${price:.2f}{rth_tag}"
            )
            # Switch to order tab
            self.right_tabs.setCurrentIndex(1)

    def _on_market_order_requested(self, option: OptionInfo, action_str: str):
        """Handle market order from price ladder action buttons."""
        action = OrderAction.BUY if action_str == "BUY" else OrderAction.SELL
        qty = self.price_ladder.get_quantity()
        outside_rth = self.price_ladder.get_outside_rth()

        order_id = self._place_order(option, action, OrderType.MARKET, 0.0, qty,
                                     outside_rth)
        if order_id > 0:
            action_text = "市价买入" if action == OrderAction.BUY else "市价卖出"
            rth_tag = " [盘外]" if outside_rth else ""
            self.statusBar().showMessage(
                f"已提交: {action_text} {qty}{self._unit_for(option)} "
                f"{option.display_name}{rth_tag}"
            )
            self.right_tabs.setCurrentIndex(1)

    def _on_close_position_requested(self, option: OptionInfo):
        """Handle close position from price ladder (期权/正股/期货)。"""
        outside_rth = self.price_ladder.get_outside_rth()

        if option.right in ("C", "P"):
            order_id = self._active_engine.close_position(
                option, outside_rth=outside_rth
            )
        else:
            # 正股/期货: 用市价反向单平掉 reqPositions 报来的持仓数量
            qty = self._active_engine.get_position_qty(option.to_ibkr_key())
            if qty <= 0:
                self.statusBar().showMessage(f"无 {option.display_name} 多头持仓可平")
                return
            order_id = self._place_order(option, OrderAction.SELL,
                                         OrderType.MARKET, 0.0, qty, outside_rth)
        if order_id > 0:
            rth_tag = " [盘外]" if outside_rth else ""
            self.statusBar().showMessage(f"已提交平仓: {option.display_name}{rth_tag}")
            self.right_tabs.setCurrentIndex(1)

    def _on_cancel_all_requested(self):
        """Handle cancel all orders from price ladder."""
        self._active_engine.cancel_all_orders()
        self.statusBar().showMessage("已请求取消所有挂单")

    def _on_cancel_order(self, order_id: int):
        self._active_engine.cancel_order(order_id)
        self.statusBar().showMessage(f"已请求撤单: #{order_id}")

    # ── 条件单 (止盈/止损) ─────────────────────────────────────────────

    def _on_conditional_requested(self, req: dict):
        """点价梯「挂条件单」: 按勾选挂止盈/止损 (本地 或 IBKR 原生)。"""
        opt = getattr(self.price_ladder, "_option", None)
        if opt is None:
            self.statusBar().showMessage("未选合约 — 无法挂条件单")
            return
        if not self.ibkr_engine.is_connected:
            self.statusBar().showMessage("未连接 — 无法挂条件单")
            return
        native = req["native"]
        qty = req["qty"]
        outside = req["outside_rth"]
        msgs = []
        if req["tp_on"]:
            if native:
                # 止盈 = 高于市价的卖出限价, 原生即普通 SELL LMT(到价成交)
                oid = self._place_order(opt, OrderAction.SELL, OrderType.LIMIT,
                                        req["tp_price"], qty, outside)
                if oid > 0:
                    msgs.append(f"止盈(原生限价@{req['tp_price']:.2f})")
            else:
                self.cond_manager.arm(opt, "TP", req["tp_price"],
                                      req["tp_price"], qty, outside)
                msgs.append(f"止盈(本地≥{req['tp_price']:.2f})")
        if req["sl_on"]:
            if native:
                oid = self._active_engine.place_stop_limit_order(
                    opt, OrderAction.SELL, qty, req["sl_price"],
                    req["sl_price"], outside_rth=outside)
                if oid > 0:
                    msgs.append(f"止损(原生STP LMT@{req['sl_price']:.2f})")
            else:
                self.cond_manager.arm(opt, "SL", req["sl_price"],
                                      req["sl_price"], qty, outside)
                msgs.append(f"止损(本地≤{req['sl_price']:.2f})")
        if msgs:
            tag = "" if native else " (本地: 仅程序运行时监控)"
            self.statusBar().showMessage("已挂条件单: " + " + ".join(msgs) + tag)
            if native:
                self.right_tabs.setCurrentIndex(1)
        self._refresh_conditionals()

    def _on_conditional_cancel(self, cond_id: int):
        self.cond_manager.cancel(cond_id)
        self.statusBar().showMessage(f"已取消本地条件单 #{cond_id}")

    def _refresh_conditionals(self):
        """把当前点价梯合约的本地条件单推给点价梯显示。"""
        opt = getattr(self.price_ladder, "_option", None)
        conds = self.cond_manager.for_key(opt.to_ibkr_key()) if opt else []
        self.price_ladder.set_conditionals(conds)

    def _on_conditional_triggered(self, cond, order_id: int):
        play_fill("SLD")
        self.statusBar().showMessage(
            f"⚡ 条件单触发: {cond.kind_label} 卖出 {cond.quantity} "
            f"{cond.option.display_name} @ {cond.limit_price:.2f} → 已下单 #{order_id}"
        )
        self.right_tabs.setCurrentIndex(1)
        self._refresh_conditionals()

    def _on_conditional_failed(self, cond, msg: str):
        self.statusBar().setStyleSheet(f"QStatusBar {{ color: #ff9800; }}")
        self.statusBar().showMessage(f"条件单 {cond.kind_label} 触发但{msg}")

    # ── Chart Window ─────────────────────────────────────────────────

    def _on_open_chart(self):
        """Open a K-line chart window for the current symbol (lazy import)."""
        if not self.ibkr_engine.is_connected:
            QMessageBox.warning(self, "未连接", "请先连接到 IBKR")
            return

        from widgets.chart_window import ChartWindow

        chart = ChartWindow(
            engine=self.ibkr_engine,
            symbol=self._current_symbol,
            parent=None,  # independent window
        )
        self._chart_windows.append(chart)
        chart.destroyed.connect(lambda: self._chart_windows.remove(chart)
                                if chart in self._chart_windows else None)
        chart.show_and_load()

    # ── Detachable Price Ladder ──────────────────────────────────────

    def _on_detach_ladder(self):
        """Pop out price ladder into a standalone window; replace its spot with a chart."""
        if self._ladder_detached:
            return

        self._ladder_detached = True
        self.price_ladder.detach_btn.setText("已弹出")
        self.price_ladder.detach_btn.setEnabled(False)

        # Remove price ladder from splitter (keep the widget alive)
        self.price_ladder.setParent(None)

        # Create standalone window for the price ladder
        self._ladder_window = QMainWindow(None)
        self._ladder_window.setWindowTitle("点价交易")
        self._ladder_window.setMinimumSize(420, 600)
        self._ladder_window.resize(440, 800)
        self._ladder_window.setCentralWidget(self.price_ladder)
        self._ladder_window.setStyleSheet(DARK_STYLESHEET)
        self._ladder_window.installEventFilter(self)

        # Create an embedded chart in the vacated splitter spot
        if self.ibkr_engine.is_connected:
            from widgets.chart_window import ChartWindow
            self._embedded_chart = ChartWindow(
                engine=self.ibkr_engine,
                symbol=self._current_symbol,
                parent=None,
            )
            # Embed ChartWindow directly in splitter (QMainWindow is a QWidget)
            self._embedded_chart.setWindowFlags(Qt.Widget)
            self.bottom_splitter.insertWidget(0, self._embedded_chart)
            self._embedded_chart.show_and_load()
        else:
            # No connection — show placeholder
            placeholder = QLabel("连接 IBKR 后显示K线图")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet(
                f"color: {COLOR_TEXT}; font-size: 14px; background-color: {COLOR_BG_DARK};"
            )
            placeholder.setObjectName("chart_placeholder")
            self.bottom_splitter.insertWidget(0, placeholder)

        self.bottom_splitter.setSizes([500, 380])
        # insertWidget 会把新 index0 的 stretch 重置, 重新设回比例联动
        self.bottom_splitter.setStretchFactor(0, 4)
        self.bottom_splitter.setStretchFactor(1, 5)
        self._ladder_window.show()

    def _on_reattach_ladder(self):
        """Return the price ladder to its original splitter position and remove the chart."""
        if not self._ladder_detached:
            return

        # Clean up embedded chart
        if self._embedded_chart:
            self._embedded_chart.cleanup()
            self._embedded_chart.setParent(None)
            self._embedded_chart.deleteLater()
            self._embedded_chart = None
        else:
            # Remove placeholder if present
            for i in range(self.bottom_splitter.count()):
                w = self.bottom_splitter.widget(i)
                if w and w.objectName() == "chart_placeholder":
                    w.setParent(None)
                    w.deleteLater()
                    break

        # Reparent price ladder back into the splitter
        self.price_ladder.setParent(None)
        self.bottom_splitter.insertWidget(0, self.price_ladder)
        self.bottom_splitter.setSizes([380, 500])
        # 重新设回 stretch (insertWidget 重置了 index0 的 stretch factor)
        self.bottom_splitter.setStretchFactor(0, 4)
        self.bottom_splitter.setStretchFactor(1, 5)

        # Reset state
        self.price_ladder.detach_btn.setText("弹出")
        self.price_ladder.detach_btn.setEnabled(True)
        self._ladder_detached = False

        if self._ladder_window:
            self._ladder_window.removeEventFilter(self)
            self._ladder_window.deleteLater()
            self._ladder_window = None

    def eventFilter(self, obj, event):
        """Catch the ladder window being closed to trigger reattach."""
        if obj is self._ladder_window and event.type() == QEvent.Close:
            self._on_reattach_ladder()
            return True  # Consume the close event (we handle cleanup)
        return super().eventFilter(obj, event)

    # ── Session Indicator ────────────────────────────────────────────

    def _update_session_indicator(self):
        """Update the session status label based on current ET time."""
        try:
            import zoneinfo
            et = datetime.now(zoneinfo.ZoneInfo("America/New_York"))
        except Exception:
            # Fallback: assume local time is ET (close enough for display)
            et = datetime.now()

        h, m = et.hour, et.minute
        t = h * 60 + m  # minutes since midnight

        gth_start = SPX_SESSION_GTH_START[0] * 60 + SPX_SESSION_GTH_START[1]  # 20:15 = 1215
        gth_end = SPX_SESSION_GTH_END[0] * 60 + SPX_SESSION_GTH_END[1]        # 09:15 = 555
        rth_start = SPX_SESSION_RTH_START[0] * 60 + SPX_SESSION_RTH_START[1]  # 09:30 = 570
        rth_end = SPX_SESSION_RTH_END[0] * 60 + SPX_SESSION_RTH_END[1]        # 16:15 = 975

        weekday = et.weekday()  # 0=Mon ... 6=Sun
        is_weekend = weekday >= 5

        if is_weekend:
            session_text = "休市"
            session_color = COLOR_RED
        elif t >= rth_start and t < rth_end:
            session_text = "RTH 正常盘"
            session_color = COLOR_GREEN
        elif t >= gth_start or t < gth_end:
            session_text = "GTH 夜盘"
            session_color = COLOR_ACCENT
        elif t >= gth_end and t < rth_start:
            session_text = "盘前过渡"
            session_color = "#ff9800"
        elif t >= rth_end and t < gth_start:
            session_text = "盘后"
            session_color = "#ff9800"
        else:
            session_text = "休市"
            session_color = COLOR_RED

        time_str = et.strftime("%H:%M ET")
        self._session_label.setText(f"{session_text} {time_str}")
        self._session_label.setStyleSheet(
            f"color: {session_color}; background-color: {COLOR_BG_PANEL}; "
            f"border: 1px solid {COLOR_BORDER}; padding: 2px 10px; "
            f"border-radius: 3px; font-size: 12px; font-weight: bold;"
        )

    # ── Error Handling ────────────────────────────────────────────────

    def _on_error(self, req_id: int, code: int, msg: str):
        # Data connection errors — show specific warning in status bar
        if code in DATA_CONNECTION_ERROR_CODES:
            if code in (2104, 2106):
                # 连接正常/已恢复
                self.statusBar().showMessage("行情数据连接正常")
                self.statusBar().setStyleSheet("")
            elif code in (2107, 2108):
                # farm「inactive but should be available upon demand」=
                # 空闲待命 (取数据时自动重连), 是 IBKR 正常状态, 不是故障 → 不标红
                self.statusBar().showMessage(f"行情farm空闲 [{code}] (按需自动重连, 正常)")
                self.statusBar().setStyleSheet("")
            else:
                # 仅 2100 / 2103 / 2105 是真正的连接断开 → 标红告警
                self.statusBar().showMessage(f"⚠ 行情数据连接异常 [{code}]: {msg}")
                self.statusBar().setStyleSheet(
                    f"QStatusBar {{ color: {COLOR_RED}; }}"
                )
            return

        # Heartbeat tick timeout (code=-2, synthetic from heartbeat)
        if code == -2:
            self.statusBar().showMessage(f"⚠ {msg}")
            self.statusBar().setStyleSheet(
                f"QStatusBar {{ color: #ff9800; }}"  # orange warning
            )
            return

        # General errors — reset status bar style
        self.statusBar().setStyleSheet("")
        self.statusBar().showMessage(f"错误 [{code}]: {msg}")

        # Reset "验证中" state in price ladder on search error
        if "验证中" in self.price_ladder.contract_label.text():
            self.price_ladder.contract_label.setText("选择期权以开始")

    def _on_order_rejected(self, order_id: int, code: int, msg: str):
        """Order rejected/cancelled by IBKR — show the reason prominently."""
        order = self.ibkr_engine.orders.get(order_id)
        desc = ""
        if order:
            desc = (f"{order.display_action} {order.quantity}张 "
                    f"{order.option.display_name} @ ${order.limit_price:.2f}\n\n")

        self.statusBar().setStyleSheet(f"QStatusBar {{ color: {COLOR_RED}; }}")
        self.statusBar().showMessage(f"⚠ 订单 #{order_id} 被拒绝 [{code}]: {msg}")

        # Non-modal popup — doesn't block further trading clicks
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(f"订单被拒绝 #{order_id}")
        box.setText(f"{desc}IBKR 拒绝原因 [{code}]:\n{msg}")
        box.setWindowModality(Qt.NonModal)
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.show()

    # ── Cleanup ───────────────────────────────────────────────────────

    # ── Layout persistence ────────────────────────────────────────────

    def _layout_splitters(self):
        """(name, splitter) 对, 用于保存/恢复各分割位置。"""
        return (
            ("main", self.main_splitter),
            ("bottom", self.bottom_splitter),
            ("right", self.right_splitter),
        )

    def _restore_layout(self):
        """恢复上次会话的窗口几何与各 splitter 分割位置 (首次运行则用默认值)。"""
        geo = self._settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        for name, splitter in self._layout_splitters():
            state = self._settings.value(f"splitter/{name}")
            if state is not None:
                splitter.restoreState(state)

    def _save_layout(self):
        """保存当前窗口几何与各 splitter 分割位置。"""
        self._settings.setValue("geometry", self.saveGeometry())
        for name, splitter in self._layout_splitters():
            self._settings.setValue(f"splitter/{name}", splitter.saveState())

    def closeEvent(self, event):
        # Stop session timer
        self._session_timer.stop()

        # Reattach ladder if detached (cleans up embedded chart too)
        if self._ladder_detached:
            self._on_reattach_ladder()

        # Persist window size + splitter positions (ladder is reattached by now)
        self._save_layout()

        # Close all chart windows
        for chart in list(self._chart_windows):
            chart.cleanup()
            chart.close()
        self._chart_windows.clear()

        # 多腿组合面板 (退订各腿行情 + 停止刷新定时器)
        self.strategy_panel.cleanup()

        self.cond_manager.cleanup()
        self.account_bar.cleanup()
        self.option_chain.cleanup()
        self.price_ladder.cleanup()
        self.position_panel.cleanup()
        self.order_panel.cleanup()
        self.calculator.cleanup()
        self.ibkr_engine.disconnect()
        event.accept()
