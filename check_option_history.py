"""诊断: 本账户到底有没有「期权历史数据」权限?

只读, 不下单。用 clientId=99 连 live TWS (与期权 GUI=10 / 正股=11 / 组合=12 互不冲突),
请求一个近月 ATM 附近 SPY 期权的「当日 1 分钟」历史 bar, 分别试 TRADES 与 MIDPOINT,
打印拿到多少根 / 报什么错码, 据此判断:

  • 两者都拿到数据      → 有期权历史权限, combo_analyzer 的「计算组合历史价」可直接用
  • 报错 162 / 354 / 10197 等 → 无 (或受限) 历史权限, 请改用「▶ 录制当日」实时累积
  • 仅 MIDPOINT 有        → 用 MIDPOINT 数据类型 (TRADES 因无成交而稀疏)

用法:  python check_option_history.py   [SYMBOL]   (默认 SPY)
"""

import sys
import time

from ibkr_engine import IBKREngine
from models import TradingMode

CLIENT_ID = 99


def main():
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "SPY").upper()
    eng = IBKREngine()
    print(f"连接 TWS (clientId={CLIENT_ID}, live)...")
    if not eng.connect(TradingMode.LIVE, client_id=CLIENT_ID):
        print("✗ 连接失败 — 确认 TWS 已登录且开启 API。")
        return
    time.sleep(1.0)

    try:
        print(f"加载 {symbol} 期权链...")
        expirations, strikes = eng.request_option_chain(symbol)
        if not expirations or not strikes:
            print("✗ 期权链为空 — 无法继续。")
            return
        expiry = sorted(expirations)[0]                 # 最近到期
        strike = sorted(strikes)[len(strikes) // 2]     # 中位行权价 (近似 ATM)
        print(f"测试合约: {symbol} {expiry} C {strike:g}  (当日 1 分钟 bar)\n")

        for what in ("TRADES", "MIDPOINT"):
            try:
                bars = eng.request_option_historical_data(
                    symbol, expiry, strike, "C",
                    bar_size="1 min", duration="1 D",
                    what_to_show=what, timeout=30,
                )
                if bars:
                    first, last = bars[0], bars[-1]
                    print(f"  ✓ {what:9s}: {len(bars)} 根  "
                          f"(close {first['close']} → {last['close']})")
                else:
                    print(f"  ⚠ {what:9s}: 连接正常但返回 0 根 (可能无成交/无权限)")
            except Exception as e:
                print(f"  ✗ {what:9s}: {e}")

        print("\n判断:")
        print("  • 上面有 ✓ → 有期权历史权限, 「计算组合历史价」可用 (优先用有数据的那种)")
        print("  • 全是 ✗ (尤其 code 162/354/10197) → 无历史权限, 请用「▶ 录制当日」")
    finally:
        eng.disconnect()
        print("\n已断开。")


if __name__ == "__main__":
    main()
