"""Account summary bar — displays portfolio value, cash, buying power, P&L."""

import math
from datetime import datetime

from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QFrame,
)
from PyQt5.QtCore import QTimer

from config import (
    COLOR_BG_DARK, COLOR_BG_PANEL, COLOR_TEXT, COLOR_TEXT_DIM,
    COLOR_GREEN, COLOR_RED, COLOR_ACCENT, COLOR_BORDER,
    ACCOUNT_REFRESH_MS,
)
from widgets.currency_balance import CurrencyBalanceBar


class AccountBar(QWidget):
    """Horizontal bar showing account summary and a live US-Eastern clock."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._engine = None

        # Account data
        self._net_liquidation = 0.0
        self._total_cash = 0.0
        self._buying_power = 0.0
        self._unrealized_pnl = 0.0
        self._realized_pnl = 0.0
        self._daily_pnl = 0.0
        self._account_name = ""
        # reqPnL 流是否已给出未实现盈亏。一旦给出, 账户摘要里(每3秒重订、常推0/陈旧值)
        # 的 UnrealizedPnL 就不再覆盖显示 —— 避免未实现盈亏闪 0。
        self._unrealized_from_stream = False
        # 今日盈亏改用引擎自算值 (今日成交现金流 + 持仓市值 − 手续费), 不依赖 IBKR dailyPnL
        # (其常为 -- / 未含费)。computed_daily_pnl 信号送来 (已扣费总额, 今日手续费)。
        self._daily_computed = None     # 自算今日盈亏 (已扣费)
        self._today_commission = 0.0    # 今日累计手续费 (仅用于标注)

        self._build_ui()

        # Periodic refresh to re-request account summary
        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self._refresh)
        self._refresh_timer.setInterval(ACCOUNT_REFRESH_MS)

        # 美东时间实时时钟 (每秒刷新, 独立于连接, 始终运行)
        self._clock_timer = QTimer()
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._update_clock()

    def _build_ui(self):
        self.setFixedHeight(36)
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {COLOR_BG_DARK};
                border-bottom: 1px solid {COLOR_BORDER};
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 2, 12, 2)
        layout.setSpacing(20)

        # Account label
        self.account_label = QLabel("账户: --")
        self.account_label.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 12px; border: none;")
        layout.addWidget(self.account_label)

        # Separator
        layout.addWidget(self._make_sep())

        # Net liquidation
        self.net_liq_label = QLabel("总资产: --")
        self.net_liq_label.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 12px; font-weight: bold; border: none;")
        layout.addWidget(self.net_liq_label)

        layout.addWidget(self._make_sep())

        # Total cash
        self.cash_label = QLabel("可用资金: --")
        self.cash_label.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 12px; border: none;")
        layout.addWidget(self.cash_label)

        layout.addWidget(self._make_sep())

        # Buying power
        self.bp_label = QLabel("购买力: --")
        self.bp_label.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 12px; border: none;")
        layout.addWidget(self.bp_label)

        layout.addWidget(self._make_sep())

        # Unrealized P&L
        self.unrealized_label = QLabel("未实现盈亏: --")
        self.unrealized_label.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 12px; border: none;")
        layout.addWidget(self.unrealized_label)

        layout.addWidget(self._make_sep())

        # Daily P&L
        self.daily_pnl_label = QLabel("今日盈亏: --")
        self.daily_pnl_label.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 12px; border: none;")
        layout.addWidget(self.daily_pnl_label)

        layout.addWidget(self._make_sep())

        # Per-currency cash balances (EUR/USD/...)
        self.currency_bar = CurrencyBalanceBar()
        layout.addWidget(self.currency_bar)

        layout.addStretch()

        # 美东时间实时时钟 (替代原「换汇」按钮)
        self.clock_label = QLabel("🕐 --:--:--")
        self.clock_label.setStyleSheet(
            f"color: {COLOR_ACCENT}; font-size: 13px; font-weight: bold; "
            f"border: none; font-family: 'Consolas', 'Menlo', monospace;"
        )
        self.clock_label.setToolTip("美东时间 (America/New_York)")
        layout.addWidget(self.clock_label)

    def _make_sep(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet(f"color: {COLOR_BORDER}; border: none; background-color: {COLOR_BORDER};")
        sep.setFixedWidth(1)
        sep.setFixedHeight(20)
        return sep

    def set_engine(self, engine):
        self._engine = engine

    def start(self):
        """Start periodic refresh."""
        self._refresh_timer.start()

    def stop(self):
        """Stop periodic refresh."""
        self._refresh_timer.stop()
        # Clear per-currency balances so a disconnect / account switch doesn't
        # leave a stale EUR/USD reading on screen.
        self.currency_bar.clear_balances()
        # 让重连后账户摘要的未实现盈亏可再次作初始回退, 直到新的 reqPnL 流接管。
        self._unrealized_from_stream = False
        # 重连会重新拉取当日成交/手续费重算, 先清零避免叠加旧会话的值。
        self._today_commission = 0.0
        self._daily_computed = None
        # 断开/切换账户时把盈亏与净值显示清回 "--",避免实盘↔模拟切换时残留上一个账户的数字
        # (今日盈亏/净值/现金等按各自账户独立显示)。
        for lab, txt in (
            (self.daily_pnl_label, "今日盈亏: --"),
            (self.unrealized_label, "未实现盈亏: --"),
            (self.net_liq_label, "总资产: --"),
            (self.cash_label, "可用资金: --"),
            (self.bp_label, "购买力: --"),
        ):
            lab.setText(txt)
            lab.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 12px; border: none;")

    def on_currency_balance(self, currency: str, cash: float):
        """Forward a per-currency cash balance to the embedded display."""
        self.currency_bar.on_balance(currency, cash)

    def update_account(self, tag: str, value: str, currency: str, account: str):
        """Handle account_summary_updated signal."""
        self._account_name = account
        self.account_label.setText(f"账户: {account}")
        self.account_label.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 12px; border: none;")

        try:
            val = float(value)
        except (ValueError, TypeError):
            return

        if tag == "NetLiquidation":
            self._net_liquidation = val
            self.net_liq_label.setText(f"总资产: ${val:,.2f}")
        elif tag == "TotalCashValue":
            self._total_cash = val
            self.cash_label.setText(f"可用资金: ${val:,.2f}")
        elif tag == "BuyingPower":
            self._buying_power = val
            self.bp_label.setText(f"购买力: ${val:,.2f}")
        elif tag == "UnrealizedPnL":
            # 仅作初始回退: reqPnL 流一旦接管就不再用账户摘要的值 (它每3秒重订、
            # 常推 0 或陈旧值, 会把好值闪没)。
            if not self._unrealized_from_stream:
                self._unrealized_pnl = val
                color = COLOR_GREEN if val >= 0 else COLOR_RED
                sign = "+" if val >= 0 else ""
                self.unrealized_label.setText(f"未实现盈亏: {sign}${val:,.2f}")
                self.unrealized_label.setStyleSheet(
                    f"color: {color}; font-size: 12px; font-weight: bold; border: none;"
                )
        elif tag == "RealizedPnL":
            self._realized_pnl = val

    def on_computed_daily(self, total: float, commission: float):
        """已弃用: 自算今日盈亏会在成交/持仓与账户不匹配时严重出错(曾误显示巨额盈利)。
        今日盈亏改回直接用 IBKR 的 dailyPnL(由 update_daily_pnl 驱动)。"""
        return

    def update_daily_pnl(self, daily: float, unrealized: float, realized: float):
        """Handle pnl_updated signal. 今日盈亏 = IBKR reqPnL 的 dailyPnL;
        **实测本账户 dailyPnL 常年不可用(DBL_MAX→NaN), 故用 已实现+未实现 兜底**
        (IBKR 的 realizedPnL 已含手续费)。NaN 时保留上一次好值, 避免闪烁/归 0。"""
        eff_daily = daily
        if math.isnan(eff_daily) and not math.isnan(realized):
            eff_daily = realized + (0.0 if math.isnan(unrealized) else unrealized)
        if not math.isnan(eff_daily):
            self._daily_pnl = eff_daily
            color = COLOR_GREEN if eff_daily >= 0 else COLOR_RED
            sign = "+" if eff_daily >= 0 else ""
            self.daily_pnl_label.setText(f"今日盈亏: {sign}${eff_daily:,.2f}")
            self.daily_pnl_label.setStyleSheet(
                f"color: {color}; font-size: 12px; font-weight: bold; border: none;"
            )

        # Also update unrealized from PnL stream (权威来源, 接管后账户摘要不再覆盖)
        if not math.isnan(unrealized):
            self._unrealized_from_stream = True
            self._unrealized_pnl = unrealized
            u_color = COLOR_GREEN if unrealized >= 0 else COLOR_RED
            u_sign = "+" if unrealized >= 0 else ""
            self.unrealized_label.setText(f"未实现盈亏: {u_sign}${unrealized:,.2f}")
            self.unrealized_label.setStyleSheet(
                f"color: {u_color}; font-size: 12px; font-weight: bold; border: none;"
            )

    def _refresh(self):
        """Periodically re-request account summary + per-currency balances."""
        if self._engine:
            self._engine.request_account_summary()
            if hasattr(self._engine, "request_currency_balances"):
                self._engine.request_currency_balances()

    def _update_clock(self):
        """刷新美东时间显示 (每秒)。无 tz 数据时回退本地时间。"""
        try:
            import zoneinfo
            et = datetime.now(zoneinfo.ZoneInfo("America/New_York"))
            tz = et.tzname() or "ET"
        except Exception:
            et = datetime.now()
            tz = "本地"
        self.clock_label.setText(f"🕐 美东 {et:%m-%d %H:%M:%S} {tz}")

    def cleanup(self):
        self._refresh_timer.stop()
        self._clock_timer.stop()
