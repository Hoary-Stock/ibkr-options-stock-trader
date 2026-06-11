"""IBKR 点价交易 — Entry point."""

import sys
import os

# Enable ANSI on Windows
if sys.platform == "win32":
    os.system("")

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from main_window import MainWindow


def main():
    # High DPI support
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))

    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
