"""自选监控面板 — 位于持仓/委托右侧, 实时显示 watch list 各合约现价 + 到价警报。

表格列: 合约 | 现价 | ⚠≥(高于警报价) | ⚠≤(低于警报价) | ✕(删除)。
- 现价每 0.5s 刷新 (WatchListManager.ticked; 面板不可见时跳过重绘, 警报照常巡检);
- 警报价双击单元格直接编辑, 留空/0 = 关闭该方向; 触发后一次性自动清除;
- 触发时: 声音 (sound_alerts.play_alert) + 非模态弹窗 + 行高亮 3 秒。
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QMessageBox, QAbstractItemView,
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor

from config import (
    COLOR_BG_DARK, COLOR_BG_PANEL, COLOR_TEXT, COLOR_TEXT_DIM,
    COLOR_BORDER, COLOR_ACCENT, COLOR_GREEN, COLOR_RED,
)
from sound_alerts import play_alert

_COL_NAME, _COL_PRICE, _COL_ABOVE, _COL_BELOW, _COL_DEL = range(5)
_HIGHLIGHT_MS = 3000


class WatchPanel(QWidget):
    """自选监控面板 (视图); 逻辑在 watchlist.WatchListManager。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager = None
        self._rebuilding = False       # 重建表格期间屏蔽 itemChanged
        self._last_prices: dict[str, float] = {}   # key -> 上次显示价 (涨跌着色)
        self._alert_boxes: list = []   # 非模态弹窗引用 (防 GC)
        self._build_ui()

    def set_manager(self, manager):
        self._manager = manager
        manager.changed.connect(self._rebuild)
        manager.ticked.connect(self._refresh_prices)
        manager.alerted.connect(self._on_alert)
        self._rebuild()

    # ── UI ────────────────────────────────────────────────────────────
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        title = QLabel("自选监控")
        title.setStyleSheet(
            f"color: {COLOR_ACCENT}; font-size: 12px; font-weight: bold; "
            f"padding: 2px; border: none;"
        )
        title.setToolTip(
            "点价梯左下角「☆ 加自选」把当前合约加入监控。\n"
            "双击 ⚠≥ / ⚠≤ 列设置到价警报 (留空或 0 = 关闭);\n"
            "触发后该警报自动清除 (一次性)。启动时自动删除已过期合约。"
        )
        layout.addWidget(title)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["合约", "现价", "⚠≥", "⚠≤", ""])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QAbstractItemView.NoSelection)
        self._table.setShowGrid(False)
        self._table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {COLOR_BG_DARK}; color: {COLOR_TEXT};
                border: 1px solid {COLOR_BORDER}; font-size: 11px;
            }}
            QHeaderView::section {{
                background-color: {COLOR_BG_PANEL}; color: {COLOR_TEXT_DIM};
                border: none; padding: 3px; font-size: 11px;
            }}
        """)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(_COL_NAME, QHeaderView.Stretch)
        for col, w in ((_COL_PRICE, 56), (_COL_ABOVE, 52),
                       (_COL_BELOW, 52), (_COL_DEL, 24)):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
            self._table.setColumnWidth(col, w)
        self._table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._table)

    # ── 表格重建 (列表/警报变化时) ────────────────────────────────────
    def _rebuild(self):
        if self._manager is None:
            return
        self._rebuilding = True
        try:
            items = self._manager.items()
            self._table.setRowCount(len(items))
            for row, it in enumerate(items):
                name = QTableWidgetItem(it.option.display_name)
                name.setFlags(Qt.ItemIsEnabled)
                name.setData(Qt.UserRole, it.key)
                name.setToolTip(it.option.display_name)
                self._table.setItem(row, _COL_NAME, name)

                price = QTableWidgetItem("--")
                price.setFlags(Qt.ItemIsEnabled)
                price.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self._table.setItem(row, _COL_PRICE, price)

                above = QTableWidgetItem(
                    f"{it.alert_above:g}" if it.alert_above > 0 else "")
                above.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                above.setForeground(QColor(COLOR_GREEN))
                above.setToolTip("现价 ≥ 此值时警报 (双击编辑, 空=关)")
                self._table.setItem(row, _COL_ABOVE, above)

                below = QTableWidgetItem(
                    f"{it.alert_below:g}" if it.alert_below > 0 else "")
                below.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                below.setForeground(QColor(COLOR_RED))
                below.setToolTip("现价 ≤ 此值时警报 (双击编辑, 空=关)")
                self._table.setItem(row, _COL_BELOW, below)

                btn = QPushButton("✕")
                btn.setFixedSize(20, 20)
                btn.setCursor(Qt.PointingHandCursor)
                btn.setToolTip("从自选移除")
                btn.setStyleSheet(
                    f"QPushButton {{ color: {COLOR_TEXT_DIM}; border: none; "
                    f"background: transparent; font-size: 11px; }}"
                    f"QPushButton:hover {{ color: {COLOR_RED}; }}"
                )
                btn.clicked.connect(
                    lambda _c, k=it.key: self._manager.remove(k))
                self._table.setCellWidget(row, _COL_DEL, btn)
        finally:
            self._rebuilding = False
        self._refresh_prices(force=True)

    # ── 现价刷新 (0.5s 心跳) ─────────────────────────────────────────
    def _refresh_prices(self, force: bool = False):
        if self._manager is None:
            return
        if not force and not self.isVisible():
            return   # 面板不可见时跳过重绘 (警报巡检在 manager 侧照常)
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, _COL_NAME)
            price_item = self._table.item(row, _COL_PRICE)
            if name_item is None or price_item is None:
                continue
            key = name_item.data(Qt.UserRole)
            tick = self._manager.price_of(key)
            last = tick.get("last", 0) or 0
            bid = tick.get("bid", 0) or 0
            ask = tick.get("ask", 0) or 0
            price = last if last > 0 else ((bid + ask) / 2 if bid > 0 and ask > 0
                                           else (bid or ask or 0.0))
            if price <= 0:
                price_item.setText("--")
                continue
            prev = self._last_prices.get(key, 0.0)
            price_item.setText(f"{price:.2f}")
            price_item.setToolTip(f"买 {bid:.2f} / 卖 {ask:.2f}")
            if prev > 0 and abs(price - prev) > 1e-9:
                price_item.setForeground(
                    QColor(COLOR_GREEN if price > prev else COLOR_RED))
            self._last_prices[key] = price

    # ── 警报价编辑 ────────────────────────────────────────────────────
    def _on_item_changed(self, item: QTableWidgetItem):
        if self._rebuilding or self._manager is None:
            return
        col = item.column()
        if col not in (_COL_ABOVE, _COL_BELOW):
            return
        name_item = self._table.item(item.row(), _COL_NAME)
        if name_item is None:
            return
        key = name_item.data(Qt.UserRole)
        text = item.text().strip()
        try:
            value = float(text) if text else 0.0
        except ValueError:
            value = 0.0
        if col == _COL_ABOVE:
            self._manager.set_alert(key, above=value)
        else:
            self._manager.set_alert(key, below=value)
        # 规范化显示 (非法输入清空)
        self._rebuilding = True
        item.setText(f"{value:g}" if value > 0 else "")
        self._rebuilding = False

    # ── 警报触发 ──────────────────────────────────────────────────────
    def _on_alert(self, item, direction: str, price: float):
        play_alert()
        self._highlight_row(item.key)

        sign = "≥ 高于" if direction == "above" else "≤ 低于"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("⚠ 到价警报")
        box.setText(f"{item.option.display_name}\n现价 {price:.2f} {sign}警报价")
        box.setModal(False)
        box.finished.connect(
            lambda _r, b=box: self._alert_boxes.remove(b)
            if b in self._alert_boxes else None)
        self._alert_boxes.append(box)
        box.show()

    def _highlight_row(self, key: str):
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, _COL_NAME)
            if name_item is not None and name_item.data(Qt.UserRole) == key:
                cells = [self._table.item(row, c) for c in
                         (_COL_NAME, _COL_PRICE, _COL_ABOVE, _COL_BELOW)]
                for c in cells:
                    if c is not None:
                        c.setBackground(QColor("#5d4037"))
                QTimer.singleShot(_HIGHLIGHT_MS, lambda: [
                    c.setBackground(QColor(0, 0, 0, 0))
                    for c in cells if c is not None])
                return

    def cleanup(self):
        for box in self._alert_boxes:
            try:
                box.close()
            except Exception:
                pass
        self._alert_boxes.clear()
