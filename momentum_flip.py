"""ES 动量翻转判断 (Vordinkkk 方法)。

移植自 `Money/vordinkkk_momentum/indicators/{momentum,signals}.py` + `config.yaml`:
  - 1 分钟 K 线;
  - 动量   momentum[t] = close[t] - close[t-period]   (period=10);
  - 导数   derivative[t] = momentum[t] - momentum[t-1];
  - 翻转 (MOMENTUM_REVERSAL): 动量**穿越 0** 且 |导数| >= 阈值 (默认 0.4):
        prev<=0<cur  → 翻多 (UP);  prev>=0>cur → 翻空 (DOWN)。

只用 close 序列即可判断翻转 (vol_momentum/divergence 等此处不需要)。
"""

from __future__ import annotations

# 与 vordinkkk_momentum/config.yaml 一致
MOMENTUM_PERIOD = 10
DERIVATIVE_THRESHOLD = 0.4
# 5MA 斜率法判「趋势 vs 震荡」(slope.py): 5周期均线, 取最近 5 根斜率, 同向占比 >= 0.8 → 趋势
MA_PERIOD = 5
SLOPE_WINDOW = 5
CONSISTENCY_THRESHOLD = 0.8


def compute_flip(closes: list[float], period: int = MOMENTUM_PERIOD,
                 deriv_threshold: float = DERIVATIVE_THRESHOLD,
                 recent_bars: int = 3) -> dict | None:
    """判断 ES 动量当前状态与最近是否翻转。

    返回 dict:
      - momentum      : 最新动量 close[-1]-close[-1-period];
      - derivative    : 最新导数;
      - sign          : 最新动量方向 (+1 多 / -1 空 / 0);
      - flip          : 最近 recent_bars 根内的合格零轴穿越 'UP'/'DOWN', 否则 None;
      - bars_since_flip: 该翻转距今多少根 K (0=最新一根);
    数据不足返回 None。
    """
    n = len(closes)
    if n < period + 2:
        return None

    mom: list[float | None] = [None] * n
    for i in range(period, n):
        mom[i] = closes[i] - closes[i - period]

    cur = mom[-1]
    prev = mom[-2]
    if cur is None or prev is None:
        return None
    derivative = cur - prev
    sign = 1 if cur > 0 else (-1 if cur < 0 else 0)

    # 从最新一根往回, 在 recent_bars 根内找一次合格的零轴穿越
    flip = None
    bars_since = None
    lo = max(period + 1, n - recent_bars)
    for i in range(n - 1, lo - 1, -1):
        m, pm = mom[i], mom[i - 1]
        if m is None or pm is None:
            continue
        d = m - pm
        if abs(d) < deriv_threshold:
            continue
        if pm <= 0 < m:
            flip, bars_since = "UP", (n - 1) - i
            break
        if pm >= 0 > m:
            flip, bars_since = "DOWN", (n - 1) - i
            break

    return {
        "momentum": cur,
        "derivative": derivative,
        "sign": sign,
        "flip": flip,
        "bars_since_flip": bars_since,
    }


def compute_slope_regime(closes: list[float], ma_period: int = MA_PERIOD,
                         slope_window: int = SLOPE_WINDOW,
                         consistency_threshold: float = CONSISTENCY_THRESHOLD
                         ) -> dict | None:
    """5MA 斜率法判「趋势 vs 震荡」(移植 slope.py)。

    取价格 `ma_period` 周期简单均线, 算最近 `slope_window` 根的逐根斜率 (ma[i]-ma[i-1]);
    同向斜率占比 (consistency) >= 阈值 → 趋势 (TRENDING), 否则震荡 (CHOPPY)。
    Vordinkkk 用它把「斜率一致时的价格抖动」判为噪音, 斜率不一致即 chop。

    返回 {regime:'TRENDING'/'CHOPPY', direction:'UP'/'DOWN'/'MIXED', consistency}。
    数据不足返回 None。
    """
    n = len(closes)
    if n < ma_period + slope_window:
        return None

    ma = [None] * n
    for i in range(ma_period - 1, n):
        ma[i] = sum(closes[i - ma_period + 1:i + 1]) / ma_period

    slopes = []
    for i in range(n - slope_window, n):
        if i >= 1 and ma[i] is not None and ma[i - 1] is not None:
            slopes.append(ma[i] - ma[i - 1])
    if not slopes:
        return None

    n_pos = sum(1 for s in slopes if s > 0)
    n_neg = sum(1 for s in slopes if s < 0)
    total = len(slopes)
    pos_ratio = n_pos / total
    neg_ratio = n_neg / total
    consistency = max(pos_ratio, neg_ratio)
    regime = "TRENDING" if consistency >= consistency_threshold else "CHOPPY"
    if pos_ratio > neg_ratio:
        direction = "UP"
    elif neg_ratio > pos_ratio:
        direction = "DOWN"
    else:
        direction = "MIXED"
    return {"regime": regime, "direction": direction, "consistency": consistency}


def analyze(closes: list[float]) -> dict | None:
    """ES 综合分析: 动量翻转 (compute_flip) + 趋势/震荡 (compute_slope_regime)。
    合并为一个 state dict 供 GUI 显示。数据不足返回 None。"""
    flip = compute_flip(closes)
    if flip is None:
        return None
    regime = compute_slope_regime(closes)
    state = dict(flip)
    if regime:
        state["regime"] = regime["regime"]
        state["regime_direction"] = regime["direction"]
        state["consistency"] = regime["consistency"]
    else:
        state["regime"] = None
        state["regime_direction"] = None
        state["consistency"] = None
    return state
