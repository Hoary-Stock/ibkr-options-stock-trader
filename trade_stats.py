"""已平仓交易统计 (TradeStats) —— 笔数 / 胜率 / 盈亏比, 不记录每一笔明细。

按「持平 → 再次持平」一个完整回合 (round-trip) 统计:
  - 操作笔数 = 已平仓回合数 (开 + 平算 1 笔; 中途加仓/分批止盈仍算同一回合);
  - 胜率     = 盈利回合数 / 总回合数;
  - 盈亏比   = 平均每笔盈利 / 平均每笔亏损 (avg win / avg loss)。

每个合约 (按唯一 key, 真实用 conId / 模拟用合约 key) 维护当前回合的:
带符号净持仓、FIFO 开仓批次、已实现盈亏累计、手续费累计。净持仓回到 0 即回合完成,
按该回合净盈亏 (已扣手续费) 归为盈 / 亏 / 平。

由成交流驱动 (真实引擎含当日历史回放 → 覆盖「今日全部」; 模拟引擎为本次运行)。
佣金用估算 (max(每张佣金×张数, 单笔下限)), 足够用于盈亏分类与比值。
"""

from __future__ import annotations

from collections import deque


def _is_buy(side: str) -> bool:
    s = (side or "").upper()
    return s in ("BOT", "BUY", "B")


class TradeStats:
    """回合制已平仓交易统计 (线程内顺序调用; 由各引擎成交回调喂入)。"""

    def __init__(self):
        self.reset()

    def reset(self):
        # key -> 当前回合: {"net": 净持仓, "lots": deque[[qty, price]], "realized": 已实现, "comm": 佣金}
        self._cyc: dict = {}
        self.count = 0
        self.wins = 0
        self.losses = 0
        self.sum_win = 0.0    # 盈利回合净盈亏之和 (正)
        self.sum_loss = 0.0   # 亏损回合净亏损之和 (正, 取绝对值)

    @staticmethod
    def _new_cycle() -> dict:
        return {"net": 0.0, "lots": deque(), "realized": 0.0, "comm": 0.0}

    def record_fill(self, key, side: str, shares: float, price: float,
                    mult: float, commission: float):
        """喂入一笔成交。key 为合约唯一标识 (conId 或合约 key)。"""
        if shares <= 0 or price <= 0:
            return
        buy = _is_buy(side)
        c = self._cyc.get(key)
        if c is None:
            c = self._new_cycle()
            self._cyc[key] = c
        c["comm"] += commission

        remaining = shares
        # 与当前净持仓反向 → 先 FIFO 平仓
        if (c["net"] > 1e-9 and not buy) or (c["net"] < -1e-9 and buy):
            remaining = self._close_against(c, shares, price, mult,
                                            long=c["net"] > 0)
            if abs(c["net"]) < 1e-9:
                # 回合完成 (净持仓归 0); 翻转的剩余开新回合
                self._finish_cycle(key, c)
                c = self._new_cycle()
                self._cyc[key] = c

        # 剩余量按成交方向开仓 / 加仓
        if remaining > 1e-9:
            c["lots"].append([remaining, price])
            c["net"] += remaining if buy else -remaining

    def _close_against(self, c: dict, qty: float, price: float, mult: float,
                       long: bool) -> float:
        """用 qty 平掉 FIFO 开仓批次, 累加已实现盈亏。返回未匹配的剩余量。"""
        remaining = qty
        while remaining > 1e-9 and c["lots"]:
            lot = c["lots"][0]
            take = min(remaining, lot[0])
            if long:   # 平多: (卖价 − 买价)
                c["realized"] += (price - lot[1]) * take * mult
                c["net"] -= take
            else:      # 平空: (开仓卖价 − 平仓买价)
                c["realized"] += (lot[1] - price) * take * mult
                c["net"] += take
            lot[0] -= take
            remaining -= take
            if lot[0] <= 1e-9:
                c["lots"].popleft()
        return remaining

    def _finish_cycle(self, key, c: dict):
        pnl = c["realized"] - c["comm"]   # 净盈亏 (已扣佣金)
        self.count += 1
        if pnl > 1e-9:
            self.wins += 1
            self.sum_win += pnl
        elif pnl < -1e-9:
            self.losses += 1
            self.sum_loss += -pnl
        # 否则视为打平: 计入总笔数, 不计盈也不计亏
        self._cyc.pop(key, None)

    def snapshot(self) -> dict:
        win_rate = (self.wins / self.count) if self.count else 0.0
        avg_win = (self.sum_win / self.wins) if self.wins else 0.0
        avg_loss = (self.sum_loss / self.losses) if self.losses else 0.0
        # 无亏损回合 → 盈亏比无穷 (None, GUI 显示 ∞); 无盈利且有亏损 → 0
        pl_ratio = (avg_win / avg_loss) if avg_loss > 1e-9 else None
        return {
            "count": self.count, "wins": self.wins, "losses": self.losses,
            "win_rate": win_rate, "pl_ratio": pl_ratio,
            "avg_win": avg_win, "avg_loss": avg_loss,
        }
