"""Technical indicator calculations for the K-line chart."""

import numpy as np


class IndicatorCalculator:
    """Static methods for computing chart indicators."""

    @staticmethod
    def moving_average(closes: np.ndarray, period: int) -> np.ndarray:
        """Simple Moving Average. Returns array of same length (NaN-padded)."""
        if len(closes) < period:
            return np.full_like(closes, np.nan, dtype=float)
        kernel = np.ones(period) / period
        ma = np.convolve(closes, kernel, mode="full")[:len(closes)]
        ma[:period - 1] = np.nan
        return ma

    @staticmethod
    def vwap(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
             volumes: np.ndarray) -> np.ndarray:
        """Volume-Weighted Average Price (cumulative, no session reset)."""
        typical = (highs + lows + closes) / 3.0
        cum_tp_vol = np.cumsum(typical * volumes)
        cum_vol = np.cumsum(volumes)
        with np.errstate(divide="ignore", invalid="ignore"):
            result = np.where(cum_vol > 0, cum_tp_vol / cum_vol, np.nan)
        return result

    @staticmethod
    def volume_colors(opens: np.ndarray, closes: np.ndarray,
                      color_up: str = "#1b5e20",
                      color_down: str = "#b71c1c") -> list[str]:
        """Return a color string per bar based on close vs open."""
        return [
            color_up if c >= o else color_down
            for o, c in zip(opens, closes)
        ]
