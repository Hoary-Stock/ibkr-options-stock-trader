"""各币种现金余额显示条 (CurrencyBalanceBar)。

订阅引擎的 `currency_balance_updated(currency, cash)` 信号 (来自 IBKR
reqAccountSummary "$LEDGER:ALL"), 把账户里每个币种的现金量并排显示, 例如:

    币种: EUR €414.00   USD $0.00

用于一眼看清"有欧元、没美元"这类情况 (买美元期权被拒的常见根因)。
期权 GUI 嵌在 AccountBar 里, 正股 client 放在顶栏。
"""

from PyQt5.QtWidgets import QLabel

from config import COLOR_TEXT, COLOR_TEXT_DIM


class CurrencyBalanceBar(QLabel):
    """显示各币种现金余额的单行标签。"""

    # 常见币种符号; 未知币种回退到只显示代码
    SYMBOLS = {
        "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥",
        "HKD": "HK$", "CNH": "¥", "CNY": "¥", "AUD": "A$",
        "CAD": "C$", "CHF": "Fr", "SGD": "S$",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._balances: dict[str, float] = {}
        self.setStyleSheet(
            f"color: {COLOR_TEXT_DIM}; font-size: 12px; border: none;"
        )
        self._render()

    def on_balance(self, currency: str, cash: float):
        """槽: 收到某币种现金余额 (来自引擎信号)。"""
        if not currency or currency == "BASE":
            return
        self._balances[currency] = cash
        self._render()

    def clear_balances(self):
        """断开连接 / 切换账户时清空。"""
        self._balances.clear()
        self._render()

    def _render(self):
        if not self._balances:
            self.setText("币种: --")
            self.setStyleSheet(
                f"color: {COLOR_TEXT_DIM}; font-size: 12px; border: none;"
            )
            return
        # 非零余额优先, 再按币种代码排序; 全部显示 (USD 0.00 也要看到)
        items = sorted(
            self._balances.items(),
            key=lambda kv: (abs(kv[1]) < 0.005, kv[0]),
        )
        parts = []
        for ccy, amt in items:
            sym = self.SYMBOLS.get(ccy, "")
            parts.append(f"{ccy} {sym}{amt:,.2f}")
        self.setText("币种: " + "   ".join(parts))
        self.setStyleSheet(
            f"color: {COLOR_TEXT}; font-size: 12px; border: none;"
        )
