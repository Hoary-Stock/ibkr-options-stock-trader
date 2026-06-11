"""Account summary bar — displays portfolio value, cash, buying power, P&L."""

from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QPushButton, QFrame,
)
from PyQt5.QtCore import pyqtSignal, Qt, QTimer

from config import (
    COLOR_BG_DARK, COLOR_BG_PANEL, COLOR_TEXT, COLOR_TEXT_DIM,
    COLOR_GREEN, COLOR_RED, COLOR_ACCENT, COLOR_BORDER,
    ACCOUNT_REFRESH_MS,
)


class AccountBar(QWidget):
    """Horizontal bar showing account summary and forex button."""

    currency_exchange_clicked = pyqtSignal()

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

        self._build_ui()

        # Periodic refresh to re-request account summary
        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self._refresh)
        self._refresh_timer.setInterval(ACCOUNT_REFRESH_MS)

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

        layout.addStretch()

        # Forex button
        self.forex_btn = QPushButton("换汇")
        self.forex_btn.setFixedSize(60, 26)
        self.forex_btn.setCursor(Qt.PointingHandCursor)
        self.forex_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_BG_PANEL};
                color: {COLOR_ACCENT};
                border: 1px solid {COLOR_ACCENT};
                border-radius: 3px;
                font-size: 11px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {COLOR_ACCENT};
                color: white;
            }}
        """)
        self.forex_btn.clicked.connect(self.currency_exchange_clicked.emit)
        layout.addWidget(self.forex_btn)

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
            self._unrealized_pnl = val
            color = COLOR_GREEN if val >= 0 else COLOR_RED
            sign = "+" if val >= 0 else ""
            self.unrealized_label.setText(f"未实现盈亏: {sign}${val:,.2f}")
            self.unrealized_label.setStyleSheet(
                f"color: {color}; font-size: 12px; font-weight: bold; border: none;"
            )
        elif tag == "RealizedPnL":
            self._realized_pnl = val

    def update_daily_pnl(self, daily: float, unrealized: float, realized: float):
        """Handle pnl_updated signal."""
        self._daily_pnl = daily
        color = COLOR_GREEN if daily >= 0 else COLOR_RED
        sign = "+" if daily >= 0 else ""
        self.daily_pnl_label.setText(f"今日盈亏: {sign}${daily:,.2f}")
        self.daily_pnl_label.setStyleSheet(
            f"color: {color}; font-size: 12px; font-weight: bold; border: none;"
        )

        # Also update unrealized from PnL stream
        self._unrealized_pnl = unrealized
        u_color = COLOR_GREEN if unrealized >= 0 else COLOR_RED
        u_sign = "+" if unrealized >= 0 else ""
        self.unrealized_label.setText(f"未实现盈亏: {u_sign}${unrealized:,.2f}")
        self.unrealized_label.setStyleSheet(
            f"color: {u_color}; font-size: 12px; font-weight: bold; border: none;"
        )

    def _refresh(self):
        """Periodically re-request account summary."""
        if self._engine:
            self._engine.request_account_summary()

    def cleanup(self):
        self._refresh_timer.stop()
