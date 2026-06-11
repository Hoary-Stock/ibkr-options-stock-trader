"""Custom pyqtgraph GraphicsObject for drawing OHLC candlesticks.

Style: Up candles = hollow (green outline), Down candles = filled (red).
All pens are cosmetic (pixel-width) so candles look clean at any zoom.
"""

import pyqtgraph as pg
from PyQt5.QtCore import QRectF, QPointF, Qt
from PyQt5.QtGui import QPicture, QPainter, QColor, QPen, QBrush


class CandlestickItem(pg.GraphicsObject):
    """Draws OHLC candlestick bars using QPicture for performance.

    Rendering approach (matches TradingView / Futu conventions):
    - Wick: 1px cosmetic line from low to high
    - Body: rectangle from open to close, 50% of bar spacing
    - Up candles: hollow (outline only), Down candles: filled
    - All pen widths in pixels (cosmetic), not data coordinates
    """

    def __init__(self, color_up="#00c853", color_down="#ff1744"):
        super().__init__()
        self._picture = QPicture()
        self._data = []
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

        # Body half-width in data-X units (50% of bar spacing = thinner, cleaner)
        w = 0.25

        # Pre-build cosmetic pens (pixel-width, independent of zoom)
        wick_pen_up = QPen(self._color_up)
        wick_pen_up.setWidthF(1.0)
        wick_pen_up.setCosmetic(True)

        wick_pen_down = QPen(self._color_down)
        wick_pen_down.setWidthF(1.0)
        wick_pen_down.setCosmetic(True)

        body_pen_up = QPen(self._color_up)
        body_pen_up.setWidthF(1.0)
        body_pen_up.setCosmetic(True)

        body_pen_down = QPen(self._color_down)
        body_pen_down.setWidthF(1.0)
        body_pen_down.setCosmetic(True)

        brush_down = QBrush(self._color_down)

        all_lows = []
        all_highs = []

        for bar in self._data:
            x = bar["date_idx"]
            o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]

            all_lows.append(l)
            all_highs.append(h)

            if c >= o:
                # Up candle: hollow body (outline only)
                painter.setPen(wick_pen_up)
                painter.setBrush(Qt.NoBrush)
                # Wick
                painter.drawLine(QPointF(x, l), QPointF(x, h))
                # Body
                painter.setPen(body_pen_up)
                body_top = c
                body_bot = o
            else:
                # Down candle: filled body
                painter.setPen(wick_pen_down)
                painter.setBrush(brush_down)
                # Wick
                painter.drawLine(QPointF(x, l), QPointF(x, h))
                # Body
                painter.setPen(body_pen_down)
                body_top = o
                body_bot = c

            body_h = body_top - body_bot
            if body_h < 1e-8:
                # Doji — 1px horizontal line at body width
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
