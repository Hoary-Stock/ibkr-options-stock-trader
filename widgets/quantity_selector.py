"""Quantity selector widget."""

from PyQt5.QtWidgets import QWidget, QHBoxLayout, QLabel, QSpinBox
from PyQt5.QtCore import pyqtSignal


class QuantitySelector(QWidget):
    """Compact quantity selector for order size."""

    quantity_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(QLabel("数量:"))

        self.spin = QSpinBox()
        self.spin.setRange(1, 100)
        self.spin.setValue(1)
        self.spin.setSuffix(" 张")
        self.spin.setMinimumWidth(80)
        self.spin.valueChanged.connect(self.quantity_changed.emit)
        layout.addWidget(self.spin)

    def value(self) -> int:
        return self.spin.value()

    def set_value(self, v: int):
        self.spin.setValue(v)
