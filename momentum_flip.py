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
