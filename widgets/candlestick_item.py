"""Custom pyqtgraph GraphicsObject for drawing OHLC candlesticks."""

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import QRectF, QPointF
from PyQt5.QtGui import QPicture, QPainter, QColor, QPen, QBrush


class CandlestickItem(pg.GraphicsObject):
    """Draws OHLC candlestick bars using QPicture for performance."""

    def __init__(self, color_up="#00c853", color_down="#ff1744"):
        super().__init__()
        self._picture = QPicture()
        self._data = []  # list of dict with date_idx, open, high, low, close
        self._color_up = QColor(color_up)
        self._color_down = QColor(color_down)
        self._bounding_rect = QRectF(0, 0, 1, 1)

    def set_data(self, data: list[dict]):
        """Set candlestick data.

        Each dict must have: date_idx (int x-position), open, high, low, close.
        """
        self._data = data
        self._generate_picture()
        self.informViewBoundsChanged()
        self.update()

    def _generate_picture(self):
        self._picture = QPicture()
        if not self._data:
            self._bounding_rect = QRectF(0, 0, 1, 1)
            return

        painter = QPainter(self._picture)
        painter.setRenderHint(QPainter.Antialiasing, False)

        w = 0.33  # half-width of candle body

        all_lows = []
        all_highs = []

        for bar in self._data:
            x = bar["date_idx"]
            o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]

            all_lows.append(l)
            all_highs.append(h)

            if c >= o:
                color = self._color_up
            else:
                color = self._color_down

            pen = QPen(color)
            pen.setWidthF(1.0)
            painter.setPen(pen)

            # Wick (high-low line)
            painter.drawLine(QPointF(x, l), QPointF(x, h))

            # Body
            brush = QBrush(color)
            painter.setBrush(brush)
            body_top = max(o, c)
            body_bot = min(o, c)
            body_h = body_top - body_bot
            if body_h < 1e-8:
                # Doji — draw a thin line
                painter.drawLine(QPointF(x - w, o), QPointF(x + w, o))
            else:
                painter.drawRect(QRectF(x - w, body_bot, w * 2, body_h))

        painter.end()

        if all_lows and all_highs:
            min_y = min(all_lows)
            max_y = max(all_highs)
            n = len(self._data)
            self._bounding_rect = QRectF(-1, min_y, n + 1, max_y - min_y)
        else:
            self._bounding_rect = QRectF(0, 0, 1, 1)

    def paint(self, painter, *args):
        painter.drawPicture(0, 0, self._picture)

    def boundingRect(self):
        return self._bounding_rect
