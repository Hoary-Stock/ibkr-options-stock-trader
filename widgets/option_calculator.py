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
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QDoubleSpinBox,
    QCheckBox, QFrame, QPushButton,
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


def solve_underlying_for_price(target, K, T, r, sigma, right, q=0.0):
    """反向求解: 给定目标期权价 target 与 (K, T, r, sigma), 求所需标的价 S。

    BS 价格对 S 单调 (Call 递增 / Put 递减), 用括弧二分法稳健求解。
    无解时返回 0.0:
      - 输入非法 (target/K/sigma/T <= 0);
      - Call 目标价低于内在价值下界几乎为 0 仍可解, 但极端值括不住时返回 0;
      - Put 目标价 >= K*exp(-rT) (需 S<=0 才能达到) → 无解。
    """
    if target <= 0 or K <= 0 or sigma <= 0 or T <= 0:
        return 0.0

    def f(S):
        return black_scholes_price(S, K, T, r, sigma, right, q)

    lo, hi = 1e-6, max(K, target) * 2.0 + 1.0

    if right == "C":
        # 价格随 S 递增: 扩大上界直到 f(hi) >= target
        for _ in range(128):
            if f(hi) >= target:
                break
            hi *= 2.0
        else:
            return 0.0
        for _ in range(200):
            mid = (lo + hi) / 2.0
            if f(mid) < target:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2.0

    # Put: 价格随 S 递减, 在 S→0 时取最大 K*exp(-rT)
    if target >= K * math.exp(-r * T):
        return 0.0  # 目标价过高, 需 S<=0 才能达到 — 无解
    for _ in range(128):
        if f(hi) <= target:
            break
        hi *= 2.0
    else:
        return 0.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if f(mid) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


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
        self._solver_seeded = False  # 右列是否已用实时值初始化 (每次换合约重置)

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

        # 两列布局: 左 = 实时理论价计算器, 右 = 反向求解标的价 (what-if)
        columns = QHBoxLayout()
        columns.setSpacing(10)
        left_col = QVBoxLayout()
        left_col.setSpacing(6)
        right_col = QVBoxLayout()
        right_col.setSpacing(6)

        vsep = QFrame()
        vsep.setFrameShape(QFrame.VLine)
        vsep.setStyleSheet(f"color: {COLOR_BORDER};")

        columns.addLayout(left_col, 1)
        columns.addWidget(vsep)
        columns.addLayout(right_col, 1)
        root.addLayout(columns, 1)

        self._build_left_column(left_col)
        self._build_right_column(right_col)

        self._apply_follow_state(True)
        self._show_placeholder()

    @staticmethod
    def _make_spin(decimals, maximum, step, suffix="", on_change=None):
        sp = QDoubleSpinBox()
        sp.setDecimals(decimals)
        sp.setRange(0.0, maximum)
        sp.setSingleStep(step)
        if suffix:
            sp.setSuffix(suffix)
        sp.setButtonSymbols(QDoubleSpinBox.NoButtons)
        if on_change is not None:
            sp.valueChanged.connect(on_change)
        return sp

    def _build_left_column(self, col: QVBoxLayout):
        """左列: 与原计算器一致 — 实时跟随, 正向算理论价。"""
        head = QLabel("正向 · 理论价")
        head.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 11px; font-weight: bold;")
        col.addWidget(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)

        self._s_spin = self._make_spin(2, 1_000_000.0, 0.5, on_change=self._recompute)
        self._k_spin = self._make_spin(2, 1_000_000.0, 1.0, on_change=self._recompute)
        self._iv_spin = self._make_spin(2, 1000.0, 1.0, " %", on_change=self._recompute)
        self._r_spin = self._make_spin(2, 100.0, 0.25, " %", on_change=self._recompute)
        self._days_spin = self._make_spin(4, 3650.0, 1.0, " 天", on_change=self._recompute)
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
        col.addLayout(grid)

        self._follow_chk = QCheckBox("跟随实时行情与时间 (取消可手动试算)")
        self._follow_chk.setChecked(True)
        self._follow_chk.setStyleSheet("color: %s;" % COLOR_TEXT_DIM)
        self._follow_chk.toggled.connect(self._on_follow_toggled)
        col.addWidget(self._follow_chk)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {COLOR_BORDER};")
        col.addWidget(sep)

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
        col.addLayout(out)

        self._greeks_label = QLabel("")
        self._greeks_label.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 11px;")
        self._greeks_label.setWordWrap(True)
        col.addWidget(self._greeks_label)

        col.addStretch(1)

    def _build_right_column(self, col: QVBoxLayout):
        """右列: what-if — 改各参数 + 目标期权价, 反向求解所需标的价 S。"""
        head = QLabel("反向 · 求标的价")
        head.setStyleSheet(f"color: {COLOR_ACCENT}; font-size: 11px; font-weight: bold;")
        col.addWidget(head)

        hint = QLabel("设定目标期权价与到期时间, 反推标的需到的价位")
        hint.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 10px;")
        hint.setWordWrap(True)
        col.addWidget(hint)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)

        self._target_spin = self._make_spin(2, 1_000_000.0, 0.05, on_change=self._solve)
        self._wk_spin = self._make_spin(2, 1_000_000.0, 1.0, on_change=self._solve)
        self._wiv_spin = self._make_spin(2, 1000.0, 1.0, " %", on_change=self._solve)
        self._wr_spin = self._make_spin(2, 100.0, 0.25, " %", on_change=self._solve)
        self._wdays_spin = self._make_spin(4, 3650.0, 0.5, " 天", on_change=self._solve)
        self._wr_spin.setValue(RISK_FREE_RATE * 100.0)

        rows = [
            ("目标期权价", self._target_spin),
            ("行权价 K", self._wk_spin),
            ("隐含波动率 IV", self._wiv_spin),
            ("无风险利率 r", self._wr_spin),
            ("剩余到期", self._wdays_spin),
        ]
        for i, (label, spin) in enumerate(rows):
            lab = QLabel(label)
            lab.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
            grid.addWidget(lab, i, 0)
            grid.addWidget(spin, i, 1)
        grid.setColumnStretch(1, 1)
        col.addLayout(grid)

        self._sync_btn = QPushButton("↺ 用实时值填充")
        self._sync_btn.setCursor(Qt.PointingHandCursor)
        self._sync_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_BG_DARK}; color: {COLOR_TEXT_DIM};
                border: 1px solid {COLOR_BORDER}; border-radius: 3px; padding: 3px;
                font-size: 11px;
            }}
            QPushButton:hover {{ color: {COLOR_TEXT}; border-color: {COLOR_ACCENT}; }}
        """)
        self._sync_btn.clicked.connect(self._seed_solver_from_live)
        col.addWidget(self._sync_btn)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {COLOR_BORDER};")
        col.addWidget(sep)

        out = QGridLayout()
        out.setHorizontalSpacing(8)
        out.setVerticalSpacing(3)

        lab_s = QLabel("所需标的价")
        lab_s.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        self._solved_s_label = QLabel("—")
        self._solved_s_label.setStyleSheet(
            f"color: {COLOR_ACCENT}; font-weight: bold; font-size: 20px;"
        )
        out.addWidget(lab_s, 0, 0)
        out.addWidget(self._solved_s_label, 0, 1, Qt.AlignRight)

        lab_cur = QLabel("当前标的")
        lab_cur.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        self._cur_under_label = QLabel("—")
        self._cur_under_label.setStyleSheet("font-size: 13px;")
        out.addWidget(lab_cur, 1, 0)
        out.addWidget(self._cur_under_label, 1, 1, Qt.AlignRight)

        lab_move = QLabel("需变动")
        lab_move.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        self._move_label = QLabel("—")
        self._move_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        out.addWidget(lab_move, 2, 0)
        out.addWidget(self._move_label, 2, 1, Qt.AlignRight)
        out.setColumnStretch(1, 1)
        col.addLayout(out)

        col.addStretch(1)

    # ── Wiring ───────────────────────────────────────────────────────────

    def set_engine(self, engine):
        self._engine = engine

    def set_option(self, option: OptionInfo):
        """主窗口选中新期权时调用。"""
        self._option = option
        self._solver_seeded = False  # 换合约 → 右列重新用实时值播种
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
            self._solved_s_label.setText("—")
            self._cur_under_label.setText("—")
            self._move_label.setText("仅期权适用")
            self._move_label.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 13px;")
            return
        self._set_inputs_enabled(True)
        self._apply_follow_state(self._follow_chk.isChecked())
        block = self._block_spins(True)
        self._k_spin.setValue(option.strike)
        self._wk_spin.setValue(option.strike)
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

        # 右列首次拿到有效实时数据时, 自动播种一次 (之后由用户自由编辑)
        if not self._solver_seeded and (tick.get("iv", 0.0) or 0.0) > 0:
            self._seed_solver_from_live()

        self._recompute()
        self._solve()

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

    # ── Reverse solver (right column) ─────────────────────────────────────

    def _seed_solver_from_live(self):
        """用左列实时值播种右列: K/IV/r/到期天数, 目标价默认取当前盘口中间价。"""
        if self._option is None or self._option.right not in ("C", "P"):
            return
        block = self._block_solver_spins(True)
        self._wk_spin.setValue(self._k_spin.value() or self._option.strike)
        iv = self._iv_spin.value()
        if iv > 0:
            self._wiv_spin.setValue(iv)
        self._wr_spin.setValue(self._r_spin.value())
        self._wdays_spin.setValue(self._days_spin.value())
        if self._mid > 0:
            self._target_spin.setValue(self._mid)
        elif self._theo_value() > 0:
            self._target_spin.setValue(self._theo_value())
        self._block_solver_spins(block)
        self._solver_seeded = True
        self._solve()

    def _theo_value(self) -> float:
        """左列当前理论价 (数值)，供播种目标价时回退使用。"""
        try:
            txt = self._theo_label.text().lstrip("$")
            return float(txt)
        except (ValueError, AttributeError):
            return 0.0

    def _solve(self):
        """反向求解: 由目标期权价 + (K,IV,r,T) 求所需标的价 S, 并对比当前标的。"""
        if self._option is None or self._option.right not in ("C", "P"):
            return
        target = self._target_spin.value()
        K = self._wk_spin.value()
        sigma = self._wiv_spin.value() / 100.0
        r = self._wr_spin.value() / 100.0
        T = max(self._wdays_spin.value(), 0.0) / 365.0

        # 当前标的价 (用最近一次实时值)
        cur = self._live_underlying(
            self._engine.get_tick(self._option.to_ibkr_key()) or {}
            if self._engine is not None else {}
        )
        self._cur_under_label.setText(f"${cur:.2f}" if cur > 0 else "—")

        if target <= 0 or sigma <= 0 or T <= 0:
            self._solved_s_label.setText("—")
            msg = "等待 IV 行情…" if sigma <= 0 else (
                "已到期" if T <= 0 else "设定目标价")
            self._move_label.setText(msg)
            self._move_label.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 13px;")
            return

        solved = solve_underlying_for_price(
            target, K, T, r, sigma, self._option.right, DIVIDEND_YIELD
        )
        if solved <= 0:
            self._solved_s_label.setText("无解")
            self._move_label.setText("目标价超出可达范围")
            self._move_label.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 13px;")
            return

        self._solved_s_label.setText(f"${solved:.2f}")

        if cur > 0:
            diff = solved - cur
            pct = diff / cur * 100.0
            # CALL 需上涨 / PUT 需下跌为利好方向; 这里直接显示标的需变动方向
            up = diff >= 0
            arrow = "↑" if up else "↓"
            color = COLOR_GREEN if up else COLOR_RED
            self._move_label.setText(f"{arrow} ${abs(diff):.2f} ({pct:+.1f}%)")
            self._move_label.setStyleSheet(
                f"color: {color}; font-size: 13px; font-weight: bold;"
            )
        else:
            self._move_label.setText("无当前标的可比")
            self._move_label.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 13px;")

    def _block_solver_spins(self, block: bool) -> bool:
        prev = self._target_spin.signalsBlocked()
        for sp in (self._target_spin, self._wk_spin, self._wiv_spin,
                   self._wr_spin, self._wdays_spin):
            sp.blockSignals(block)
        return prev

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
        self._solved_s_label.setText("—")
        self._cur_under_label.setText("—")
        self._move_label.setText("选择期权后求解")
        self._move_label.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 13px;")

    def _block_spins(self, block: bool) -> bool:
        """统一阻塞/恢复所有输入框信号; 返回 s_spin 之前的阻塞状态以便还原。"""
        prev = self._s_spin.signalsBlocked()
        for sp in (self._s_spin, self._k_spin, self._iv_spin,
                   self._r_spin, self._days_spin):
            sp.blockSignals(block)
        return prev

    def _set_inputs_enabled(self, enabled: bool):
        for sp in (self._s_spin, self._k_spin, self._iv_spin,
                   self._r_spin, self._days_spin,
                   self._target_spin, self._wk_spin, self._wiv_spin,
                   self._wr_spin, self._wdays_spin):
            sp.setEnabled(enabled)
        self._follow_chk.setEnabled(enabled)
        self._sync_btn.setEnabled(enabled)

    def _on_follow_toggled(self, checked: bool):
        self._apply_follow_state(checked)
        self._refresh()

    def _apply_follow_state(self, following: bool):
        """跟随实时模式下, 由行情驱动的字段设为只读 (利率始终可改)。"""
        for sp in (self._s_spin, self._iv_spin, self._days_spin, self._k_spin):
            sp.setReadOnly(following)

    def cleanup(self):
        self._timer.stop()
