"""期权组合定价 — 纯计算, 无 Qt / IBKR 依赖 (可单元测试)。

约定: 组合净价 = Σ 各腿 (BUY:+1 / SELL:-1) × ratio × 腿价。
  正值 = 净借记 (买入组合需支付的成本);
  负值 = 净贷记 (建仓收取的权利金)。

历史组合价: 各腿的历史 close 按时间戳对齐 (取交集) 后逐根合成 —
券商通常不直接提供组合的历史价, 但可由各腿历史价算出。
"""

from __future__ import annotations

from widgets.strategy_defs import StrategyTemplate


def leg_sign(action: str) -> float:
    """BUY → +1 (付钱), SELL → -1 (收钱)。"""
    return 1.0 if action.upper() == "BUY" else -1.0


def resolved_legs(template: StrategyTemplate,
                  strikes_by_param: dict,
                  expiries_by_param: dict) -> list[dict]:
    """把模板的 strike_param / expiry_param 映射为实际腿。
    返回 [{right, action, ratio, strike, expiry}]。"""
    out = []
    for leg in template.legs:
        out.append({
            "right": leg.right,
            "action": leg.action,
            "ratio": leg.ratio,
            "strike": strikes_by_param.get(leg.strike_param),
            "expiry": expiries_by_param.get(leg.expiry_param),
        })
    return out


def combo_price_from_prices(legs: list[dict], leg_prices: list[float]) -> float:
    """由各腿即时价算组合净价。legs 与 leg_prices 一一对应。"""
    total = 0.0
    for leg, price in zip(legs, leg_prices):
        total += leg_sign(leg["action"]) * leg["ratio"] * price
    return total


def compute_combo_series(signed_ratios: list[float],
                         leg_bars: list[list[dict]]) -> list[dict]:
    """合成组合历史价。
    signed_ratios: 每条腿的带符号比例 (= leg_sign × ratio);
    leg_bars: 每条腿的历史 K 线 [{date, close, ...}] (date 为可比较的时间戳字符串)。
    返回按各腿时间交集排序的 [{date, price}]。"""
    maps = []
    for sr, bars in zip(signed_ratios, leg_bars):
        m = {b["date"]: b["close"] for b in bars
             if b.get("close") and b["close"] > 0}
        maps.append((sr, m))
    if not maps or any(not m for _, m in maps):
        return []

    common = set(maps[0][1].keys())
    for _, m in maps[1:]:
        common &= set(m.keys())
    if not common:
        return []

    def _key(d):
        try:
            return float(d)
        except (TypeError, ValueError):
            return d

    series = []
    for date in sorted(common, key=_key):
        price = sum(sr * m[date] for sr, m in maps)
        series.append({"date": date, "price": price})
    return series


def compute_combo_ohlc(signed_ratios: list[float],
                       leg_bars: list[list[dict]]) -> list[dict]:
    """合成组合的「合并 K 线」(OHLC)。
    open/close = Σ 各腿 signed_ratio × leg_open/close;
    high/low: 多头腿(signed_ratio>0)用其 high/low, 空头腿(<0)反向用其 low/high
      —— 给出组合在该根内的价值包络 (上下界), 是价差合并 K 线的常用近似。
    返回按各腿时间交集排序的 [{date, open, high, low, close}]。"""
    maps = []
    for sr, bars in zip(signed_ratios, leg_bars):
        m = {b["date"]: b for b in bars if b.get("close") and b["close"] > 0}
        maps.append((sr, m))
    if not maps or any(not m for _, m in maps):
        return []

    common = set(maps[0][1].keys())
    for _, m in maps[1:]:
        common &= set(m.keys())
    if not common:
        return []

    def _key(d):
        try:
            return float(d)
        except (TypeError, ValueError):
            return d

    out = []
    for date in sorted(common, key=_key):
        o = h = l = c = 0.0
        for sr, m in maps:
            bar = m[date]
            o += sr * bar.get("open", bar["close"])
            c += sr * bar["close"]
            if sr >= 0:
                h += sr * bar.get("high", bar["close"])
                l += sr * bar.get("low", bar["close"])
            else:
                h += sr * bar.get("low", bar["close"])
                l += sr * bar.get("high", bar["close"])
        out.append({"date": date, "open": o, "high": h, "low": l, "close": c})
    return out


def auto_assign_strikes(template: StrategyTemplate,
                        available_strikes: list[float],
                        center: float,
                        width_steps: int = 1) -> dict:
    """把模板的各 strike_param 以 center 为中心、按行权价网格升序分配。
    width_steps: 相邻 param 间隔几个行权价档位 (翼展)。返回 {param: strike}。
    这是合理默认值, 用户可在界面上逐腿微调。"""
    params = template.strike_params
    grid = sorted(available_strikes)
    if not params or not grid:
        return {}
    atm = min(range(len(grid)), key=lambda i: abs(grid[i] - center))
    n = len(params)
    result = {}
    for i, p in enumerate(params):
        offset = int(round((i - (n - 1) / 2.0) * max(1, width_steps)))
        idx = max(0, min(len(grid) - 1, atm + offset))
        result[p] = grid[idx]
    return result
