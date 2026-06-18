"""期权理论价计算器 (Black-Scholes)。

放在主窗口右下角, 跟随左侧待交易期权 (price ladder 当前选中的合约)。
用 IBKR 推送的 IV + 标的价, 配合行权价与剩余到期时间, 按 Black-Scholes
实时算出期权的「应有价格」, 并与盘口中间价比较 (高估 / 低估)。

刷新由 QTimer 驱动: 既跟随实时行情 (IV / 标的价更新), 也随时间衰减
(剩余到期时间 T 每次重算), 因此理论价会「根据时间实时更新」。

勾掉「跟随实时」即进入手动 what-if 模式, 可任意改 S / IV / 利率 / 剩余天数试算。
"""

from __future__ import annotations

import math
from datetime import datetime

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QLabel, QDoubleSpinBox, QCheckBox,
    QFrame,
)

from models import OptionInfo
from config import (
    RISK_FREE_RATE, DIVIDEND_YIELD, OPTION_MARKET_CLOSE_ET,
    CALCULATOR_REFRESH_MS, COLOR_BG_PANEL, COLOR_BG_DARK, COLOR_BORDER,
    COLOR_TEXT, COLOR_TEXT_DIM, COLOR_GREEN, COLOR_RED, COLOR_ACCENT,
)


# ── Black-Scholes ───────────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black_scholes_price(S, K, T, r, sigma, right, q=0.0):
    """Black-Scholes-Merton 欧式期权理论价。right: 'C'/'P'。无效输入返回 0。"""
    if S <= 0 or K <= 0 or sigma <= 0 or T <= 0:
        return 0.0
    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    if right == "C":
        return (S * math.exp(-q * T) * _norm_cdf(d1)
                - K * math.exp(-r * T) * _norm_cdf(d2))
    return (K * math.exp(-r * T) * _norm_cdf(-d2)
            - S * math.exp(-q * T) * _norm_cdf(-d1))


def years_to_expiry(expiry: str) -> float:
    """从现在到 expiry(YYYYMMDD) 当日 16:00 ET 收盘的年化剩余时间 (年)。已过期返回 0。"""
    if not expiry or len(expiry) != 8:
        return 0.0
    try:
        y, m, d = int(expiry[:4]), int(expiry[4:6]), int(expiry[6:8])
    except ValueError:
        return 0.0
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo("America/New_York")
        now = datetime.now(tz)
        expiry_dt = datetime(y, m, d, OPTION_MARKET_CLOSE_ET, 0, 0, tzinfo=tz)
    except Exception:
        now = datetime.now()
        expiry_dt = datetime(y, m, d, OPTION_MARKET_CLOSE_ET, 0, 0)
    secs = (expiry_dt - now).total_seconds()
    return secs / (365.0 * 24.0 * 3600.0) if secs > 0 else 0.0


# ── Widget ──────────────────────────────────────────────────────────────

class OptionCalculator(QWidget):
    """期权理论价计算器面板。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._engine = None
        self._option: OptionInfo | None = None
        self._mid = 0.0          # 最新盘口中间价 (用于比较)
        self._greeks: dict = {}  # 最新模型 greeks (展示用)

        self._build_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(CALCULATOR_REFRESH_MS)

    # ── UI ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.setStyleSheet(f"""
            QWidget {{ background-color: {COLOR_BG_PANEL}; color: {COLOR_TEXT}; }}
            QLabel {{ background: transparent; }}
            QDoubleSpinBox {{
                background-color: {COLOR_BG_DARK}; color: {COLOR_TEXT};
                border: 1px solid {COLOR_BORDER}; border-radius: 3px;
                padding: 2px 4px;
            }}
            QDoubleSpinBox:read-only {{ color: {COLOR_TEXT_DIM}; }}
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        self._title = QLabel("期权理论价计算器")
        self._title.setStyleSheet(
            f"color: {COLOR_ACCENT}; font-weight: bold; font-size: 13px;"
        )
        root.addWidget(self._title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)

        def _spin(decimals, maximum, step, suffix=""):
            sp = QDoubleSpinBox()
            sp.setDecimals(decimals)
            sp.setRange(0.0, maximum)
            sp.setSingleStep(step)
            if suffix:
                sp.setSuffix(suffix)
            sp.setButtonSymbols(QDoubleSpinBox.NoButtons)
            sp.valueChanged.connect(self._recompute)
            return sp

        self._s_spin = _spin(2, 1_000_000.0, 0.5)            # 标的价
        self._k_spin = _spin(2, 1_000_000.0, 1.0)            # 行权价
        self._iv_spin = _spin(2, 1000.0, 1.0, " %")          # 隐含波动率
        self._r_spin = _spin(2, 100.0, 0.25, " %")           # 无风险利率
        self._days_spin = _spin(4, 3650.0, 1.0, " 天")       # 剩余天数
        self._r_spin.setValue(RISK_FREE_RATE * 100.0)

        rows = [
            ("标的价 S", self._s_spin),
            ("行权价 K", self._k_spin),
            ("隐含波动率 IV", self._iv_spin),
            ("无风险利率 r", self._r_spin),
            ("剩余到期", self._days_spin),
        ]
        for i, (label, spin) in enumerate(rows):
            lab = QLabel(label)
            lab.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
            grid.addWidget(lab, i, 0)
            grid.addWidget(spin, i, 1)
        grid.setColumnStretch(1, 1)
        root.addLayout(grid)

        self._follow_chk = QCheckBox("跟随实时行情与时间 (取消可手动试算)")
        self._follow_chk.setChecked(True)
        self._follow_chk.setStyleSheet("color: %s;" % COLOR_TEXT_DIM)
        self._follow_chk.toggled.connect(self._on_follow_toggled)
        root.addWidget(self._follow_chk)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {COLOR_BORDER};")
        root.addWidget(sep)

        out = QGridLayout()
        out.setHorizontalSpacing(8)
        out.setVerticalSpacing(3)

        lab_theo = QLabel("理论价")
        lab_theo.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        self._theo_label = QLabel("—")
        self._theo_label.setStyleSheet(
            f"color: {COLOR_ACCENT}; font-weight: bold; font-size: 20px;"
        )
        out.addWidget(lab_theo, 0, 0)
        out.addWidget(self._theo_label, 0, 1, Qt.AlignRight)

        lab_mid = QLabel("盘口中间价")
        lab_mid.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        self._mid_label = QLabel("—")
        self._mid_label.setStyleSheet("font-size: 13px;")
        out.addWidget(lab_mid, 1, 0)
        out.addWidget(self._mid_label, 1, 1, Qt.AlignRight)

        lab_val = QLabel("估值")
        lab_val.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        self._valuation_label = QLabel("—")
        self._valuation_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        out.addWidget(lab_val, 2, 0)
        out.addWidget(self._valuation_label, 2, 1, Qt.AlignRight)
        out.setColumnStretch(1, 1)
        root.addLayout(out)

        self._greeks_label = QLabel("")
        self._greeks_label.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 11px;")
        self._greeks_label.setWordWrap(True)
        root.addWidget(self._greeks_label)

        root.addStretch(1)
        self._apply_follow_state(True)
        self._show_placeholder()

    # ── Wiring ───────────────────────────────────────────────────────────

    def set_engine(self, engine):
        self._engine = engine

    def set_option(self, option: OptionInfo):
        """主窗口选中新期权时调用。"""
        self._option = option
        is_opt = option is not None and option.right in ("C", "P")
        self._title.setText(
            f"期权理论价计算器 — {option.display_name}" if option else "期权理论价计算器"
        )
        if not is_opt:
            # 正股伪合约无理论价概念
            self._set_inputs_enabled(False)
            self._theo_label.setText("—")
            self._mid_label.setText("—")
            self._valuation_label.setText("仅期权适用")
            self._valuation_label.setStyleSheet(
                f"color: {COLOR_TEXT_DIM}; font-size: 13px;"
            )
            self._greeks_label.setText("")
            return
        self._set_inputs_enabled(True)
        self._apply_follow_state(self._follow_chk.isChecked())
        block = self._block_spins(True)
        self._k_spin.setValue(option.strike)
        self._block_spins(block)
        self._refresh()

    # ── Refresh loop ─────────────────────────────────────────────────────

    def _refresh(self):
        """定时刷新: 拉取实时 IV/标的价/盘口, 重算剩余时间, 计算理论价。"""
        if self._option is None or self._option.right not in ("C", "P"):
            return

        tick = {}
        if self._engine is not None:
            tick = self._engine.get_tick(self._option.to_ibkr_key()) or {}

        self._mid = self._market_mid(tick)
        self._greeks = {
            "delta": tick.get("delta"), "gamma": tick.get("gamma"),
            "vega": tick.get("vega"), "theta": tick.get("theta"),
        }

        if self._follow_chk.isChecked():
            und = self._live_underlying(tick)
            iv = tick.get("iv", 0.0) or 0.0
            block = self._block_spins(True)
            if und > 0:
                self._s_spin.setValue(und)
            if iv > 0:
                self._iv_spin.setValue(iv * 100.0)
            self._days_spin.setValue(years_to_expiry(self._option.expiry) * 365.0)
            self._block_spins(block)

        self._recompute()

    def _live_underlying(self, opt_tick: dict) -> float:
        """标的实时价。优先用高频的标的 tick (__stock__SYM, 随每笔成交跳动),
        回退到期权模型计算值 undPrice (更新较慢, 故理论价此前显得迟钝)。"""
        if self._engine is not None:
            und_tick = self._engine.get_tick(f"__stock__{self._option.symbol}") or {}
            last = und_tick.get("last", 0.0) or 0.0
            bid = und_tick.get("bid", 0.0) or 0.0
            ask = und_tick.get("ask", 0.0) or 0.0
            if last > 0:
                return last
            if bid > 0 and ask > 0:
                return (bid + ask) / 2.0
        return opt_tick.get("und_price", 0.0) or 0.0

    def _recompute(self):
        if self._option is None or self._option.right not in ("C", "P"):
            return
        S = self._s_spin.value()
        K = self._k_spin.value()
        sigma = self._iv_spin.value() / 100.0
        r = self._r_spin.value() / 100.0
        T = max(self._days_spin.value(), 0.0) / 365.0

        # 盘口中间价
        if self._mid > 0:
            self._mid_label.setText(f"${self._mid:.2f}")
        else:
            self._mid_label.setText("—")

        # 缺少必要输入时给出明确提示, 而非误导性的 0
        if sigma <= 0:
            self._theo_label.setText("—")
            self._valuation_label.setText("等待 IV 行情…")
            self._valuation_label.setStyleSheet(
                f"color: {COLOR_TEXT_DIM}; font-size: 13px;"
            )
            self._update_greeks_label()
            return
        if T <= 0:
            self._theo_label.setText(f"${max(self._intrinsic(S, K), 0):.2f}")
            self._valuation_label.setText("已到期 (仅内在价值)")
            self._valuation_label.setStyleSheet(
                f"color: {COLOR_TEXT_DIM}; font-size: 13px;"
            )
            self._update_greeks_label()
            return

        theo = black_scholes_price(
            S, K, T, r, sigma, self._option.right, DIVIDEND_YIELD
        )
        self._theo_label.setText(f"${theo:.2f}")

        # 估值: 理论价 vs 盘口中间价
        if self._mid > 0 and theo > 0:
            diff = self._mid - theo            # 市场 - 理论
            pct = diff / theo * 100.0
            if diff > 0:   # 市场价高于理论价 → 偏贵 (高估)
                self._valuation_label.setText(f"偏贵 +${diff:.2f} ({pct:+.1f}%)")
                self._valuation_label.setStyleSheet(
                    f"color: {COLOR_RED}; font-size: 13px; font-weight: bold;"
                )
            else:          # 市场价低于理论价 → 偏便宜 (低估)
                self._valuation_label.setText(f"偏便宜 ${diff:.2f} ({pct:+.1f}%)")
                self._valuation_label.setStyleSheet(
                    f"color: {COLOR_GREEN}; font-size: 13px; font-weight: bold;"
                )
        else:
            self._valuation_label.setText("无盘口可比")
            self._valuation_label.setStyleSheet(
                f"color: {COLOR_TEXT_DIM}; font-size: 13px;"
            )

        self._update_greeks_label()

    # ── Helpers ──────────────────────────────────────────────────────────

    def _intrinsic(self, S, K):
        if self._option.right == "C":
            return S - K
        return K - S

    def _market_mid(self, tick: dict) -> float:
        bid = tick.get("bid", 0.0) or 0.0
        ask = tick.get("ask", 0.0) or 0.0
        last = tick.get("last", 0.0) or 0.0
        if bid > 0 and ask > 0:
            return (bid + ask) / 2.0
        return last

    def _update_greeks_label(self):
        g = self._greeks
        parts = []
        if g.get("delta") is not None:
            parts.append(f"Δ {g['delta']:.3f}")
        if g.get("gamma") is not None:
            parts.append(f"Γ {g['gamma']:.4f}")
        if g.get("theta") is not None:
            parts.append(f"Θ {g['theta']:.3f}")
        if g.get("vega") is not None:
            parts.append(f"V {g['vega']:.3f}")
        self._greeks_label.setText("  ".join(parts) if parts else "")

    def _show_placeholder(self):
        self._theo_label.setText("—")
        self._mid_label.setText("—")
        self._valuation_label.setText("选择左侧期权后计算")
        self._valuation_label.setStyleSheet(
            f"color: {COLOR_TEXT_DIM}; font-size: 13px;"
        )

    def _block_spins(self, block: bool) -> bool:
        """统一阻塞/恢复所有输入框信号; 返回 s_spin 之前的阻塞状态以便还原。"""
        prev = self._s_spin.signalsBlocked()
        for sp in (self._s_spin, self._k_spin, self._iv_spin,
                   self._r_spin, self._days_spin):
            sp.blockSignals(block)
        return prev

    def _set_inputs_enabled(self, enabled: bool):
        for sp in (self._s_spin, self._k_spin, self._iv_spin,
                   self._r_spin, self._days_spin):
            sp.setEnabled(enabled)
        self._follow_chk.setEnabled(enabled)

    def _on_follow_toggled(self, checked: bool):
        self._apply_follow_state(checked)
        self._refresh()

    def _apply_follow_state(self, following: bool):
        """跟随实时模式下, 由行情驱动的字段设为只读 (利率始终可改)。"""
        for sp in (self._s_spin, self._iv_spin, self._days_spin, self._k_spin):
            sp.setReadOnly(following)

    def cleanup(self):
        self._timer.stop()
