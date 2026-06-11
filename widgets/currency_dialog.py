"""Currency Exchange Dialog — forex conversion between USD and other currencies."""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QRadioButton, QDoubleSpinBox, QPushButton, QButtonGroup,
    QMessageBox, QFrame,
)
from PyQt5.QtCore import Qt

from config import (
    FOREX_PAIRS, COLOR_BG, COLOR_BG_DARK, COLOR_TEXT,
    COLOR_BORDER, COLOR_ACCENT, COLOR_BUY, COLOR_SELL,
)


class CurrencyExchangeDialog(QDialog):
    """Dialog for placing forex conversion orders."""

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self._engine = engine
        self.setWindowTitle("换汇")
        self.setFixedSize(380, 320)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {COLOR_BG};
                color: {COLOR_TEXT};
            }}
            QLabel {{
                color: {COLOR_TEXT};
            }}
            QComboBox {{
                background-color: {COLOR_BG_DARK};
                color: {COLOR_TEXT};
                border: 1px solid {COLOR_BORDER};
                padding: 4px 8px;
                border-radius: 3px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {COLOR_BG_DARK};
                color: {COLOR_TEXT};
                selection-background-color: {COLOR_ACCENT};
            }}
            QDoubleSpinBox {{
                background-color: {COLOR_BG_DARK};
                color: {COLOR_TEXT};
                border: 1px solid {COLOR_BORDER};
                padding: 4px;
                border-radius: 3px;
            }}
            QRadioButton {{
                color: {COLOR_TEXT};
            }}
        """)

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 16, 20, 16)

        # Title
        title = QLabel("货币兑换")
        title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {COLOR_ACCENT};")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # Currency pair selector
        pair_layout = QHBoxLayout()
        pair_layout.addWidget(QLabel("货币对:"))
        self.pair_combo = QComboBox()
        for base, quote in FOREX_PAIRS:
            self.pair_combo.addItem(f"{base}/{quote}", (base, quote))
        self.pair_combo.setMinimumWidth(140)
        pair_layout.addWidget(self.pair_combo)
        pair_layout.addStretch()
        layout.addLayout(pair_layout)

        # Direction
        dir_layout = QHBoxLayout()
        dir_layout.addWidget(QLabel("方向:"))
        self.buy_radio = QRadioButton("买入 (Base)")
        self.sell_radio = QRadioButton("卖出 (Base)")
        self.buy_radio.setChecked(True)
        self.dir_group = QButtonGroup()
        self.dir_group.addButton(self.buy_radio, 0)
        self.dir_group.addButton(self.sell_radio, 1)
        dir_layout.addWidget(self.buy_radio)
        dir_layout.addWidget(self.sell_radio)
        dir_layout.addStretch()
        layout.addLayout(dir_layout)

        # Amount
        amount_layout = QHBoxLayout()
        amount_layout.addWidget(QLabel("金额:"))
        self.amount_spin = QDoubleSpinBox()
        self.amount_spin.setRange(1, 10_000_000)
        self.amount_spin.setValue(25000)
        self.amount_spin.setDecimals(2)
        self.amount_spin.setPrefix("$ ")
        self.amount_spin.setSingleStep(1000)
        self.amount_spin.setMinimumWidth(160)
        amount_layout.addWidget(self.amount_spin)
        amount_layout.addStretch()
        layout.addLayout(amount_layout)

        # Warning
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background-color: {COLOR_BORDER};")
        layout.addWidget(sep)

        warning = QLabel(
            "注意: 换汇使用市价单执行，实际成交价可能与当前报价不同。\n"
            "IDEALPRO 最低金额要求约 25,000 单位。"
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #ff9800; font-size: 11px;")
        layout.addWidget(warning)

        layout.addStretch()

        # Buttons
        btn_layout = QHBoxLayout()
        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedSize(80, 32)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_BG_DARK};
                color: {COLOR_TEXT};
                border: 1px solid {COLOR_BORDER};
                border-radius: 3px;
            }}
            QPushButton:hover {{ background-color: {COLOR_BORDER}; }}
        """)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        btn_layout.addStretch()

        confirm_btn = QPushButton("确认换汇")
        confirm_btn.setFixedSize(100, 32)
        confirm_btn.setCursor(Qt.PointingHandCursor)
        confirm_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_ACCENT};
                color: white;
                border: none;
                border-radius: 3px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: #0097a7; }}
        """)
        confirm_btn.clicked.connect(self._on_confirm)
        btn_layout.addWidget(confirm_btn)

        layout.addLayout(btn_layout)

    def _on_confirm(self):
        pair_data = self.pair_combo.currentData()
        if not pair_data:
            return
        base, quote = pair_data
        action = "BUY" if self.buy_radio.isChecked() else "SELL"
        amount = self.amount_spin.value()
        action_text = "买入" if action == "BUY" else "卖出"

        reply = QMessageBox.question(
            self, "确认换汇",
            f"确认以市价{action_text} {amount:,.2f} {base}\n"
            f"货币对: {base}/{quote}\n\n"
            f"此操作不可撤销，确认继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            self._engine.place_forex_order(base, quote, action, amount)
            self.accept()
