"""Check if IBKR account can trade SPX options (RTH / GTH / Curb).

Read-only — does NOT place any orders.
Connects to TWS live port (7496), uses clientId=99 to avoid conflicts.
"""

import time
import threading
from datetime import datetime, timedelta

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract


class CheckApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.connected_event = threading.Event()
        self.contract_details_list = []
        self.contract_event = threading.Event()
        self.opt_params = []
        self.opt_event = threading.Event()
        self.tick_data = {}
        self.errors = []

    def nextValidId(self, orderId):
        self.connected_event.set()

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        # Skip informational codes
        if errorCode in (2104, 2106, 2108, 2158, 2119, 2100, 2103, 2105, 2107, 2150, 2157, 2168, 2169):
            return
        self.errors.append((reqId, errorCode, errorString))
        if errorCode in (200, 354, 10189, 10167):
            # These might be relevant — print but don't crash
            pass
        # Unblock waiting threads on error
        if reqId == 1001:
            self.contract_event.set()
        elif reqId == 1002:
            self.opt_event.set()

    def contractDetails(self, reqId, contractDetails):
        self.contract_details_list.append(contractDetails)

    def contractDetailsEnd(self, reqId):
        self.contract_event.set()

    def securityDefinitionOptionParameter(self, reqId, exchange, underlyingConId,
                                           tradingClass, multiplier, expirations, strikes):
        self.opt_params.append({
            "exchange": exchange,
            "tradingClass": tradingClass,
            "multiplier": multiplier,
            "expirations": sorted(expirations)[:5],  # just first 5
            "num_expirations": len(expirations),
            "num_strikes": len(strikes),
        })

    def securityDefinitionOptionParameterEnd(self, reqId):
        self.opt_event.set()

    def tickPrice(self, reqId, tickType, price, attrib):
        if price > 0:
            names = {1: "bid", 2: "ask", 4: "last", 6: "high", 7: "low",
                     9: "close", 66: "delayed_bid", 67: "delayed_ask", 68: "delayed_last"}
            name = names.get(tickType, f"type_{tickType}")
            self.tick_data[name] = price

    def tickSize(self, reqId, tickType, size):
        if size > 0:
            names = {0: "bid_size", 3: "ask_size", 5: "last_size", 8: "volume"}
            name = names.get(tickType, None)
            if name:
                self.tick_data[name] = size

    def tickString(self, reqId, tickType, value):
        pass

    def tickGeneric(self, reqId, tickType, value):
        pass


def main():
    print("=" * 65)
    print("  SPX 期权账户权限检查 (只读, 不下单)")
    print("=" * 65)
    print()

    app = CheckApp()
    app.connect("127.0.0.1", 7496, clientId=99)

    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()

    if not app.connected_event.wait(timeout=10):
        print("[FAIL] 无法连接 TWS — 请确认 TWS 已启动 (端口 7496)")
        return

    print("[OK] 已连接到 TWS\n")
    app.reqMarketDataType(1)  # Live data

    # ── Step 1: Check SPX index contract ──────────────────────────────
    print("─" * 65)
    print("Step 1: 检查 SPX 指数合约")
    print("─" * 65)

    spx = Contract()
    spx.symbol = "SPX"
    spx.secType = "IND"
    spx.exchange = "CBOE"
    spx.currency = "USD"

    app.contract_details_list.clear()
    app.contract_event.clear()
    app.reqContractDetails(1001, spx)

    if not app.contract_event.wait(timeout=10):
        print("[FAIL] 获取 SPX 合约详情超时")
    elif not app.contract_details_list:
        print("[FAIL] SPX 合约未找到")
        _print_errors(app, 1001)
    else:
        cd = app.contract_details_list[0]
        c = cd.contract
        print(f"  [OK] SPX 合约: conId={c.conId}, exchange={c.exchange}")
        print(f"  交易时间: {cd.tradingHours[:120]}...")
        print(f"  液态时间: {cd.liquidHours[:120]}...")
        spx_con_id = c.conId
    print()

    # ── Step 2: Check SPX option parameters ──────────────────────────
    print("─" * 65)
    print("Step 2: 检查 SPX 期权链参数")
    print("─" * 65)

    app.opt_params.clear()
    app.opt_event.clear()
    app.reqSecDefOptParams(1002, "SPX", "", "IND", spx_con_id)

    if not app.opt_event.wait(timeout=10):
        print("[FAIL] 获取 SPX 期权参数超时")
    elif not app.opt_params:
        print("[FAIL] 无 SPX 期权参数返回")
        _print_errors(app, 1002)
    else:
        print(f"  [OK] 返回 {len(app.opt_params)} 个交易所/类别:")
        for p in app.opt_params:
            print(f"    exchange={p['exchange']}, class={p['tradingClass']}, "
                  f"multiplier={p['multiplier']}, "
                  f"{p['num_expirations']} 个到期日, {p['num_strikes']} 个行权价")
            print(f"    最近到期: {', '.join(p['expirations'])}")
    print()

    # ── Step 3: Request a specific SPX option contract detail ────────
    print("─" * 65)
    print("Step 3: 检查具体 SPX 期权合约 (含交易时段)")
    print("─" * 65)

    # Pick nearest Friday expiry or next available
    if app.opt_params:
        # Use first available expiry from SMART or any exchange
        all_exps = []
        for p in app.opt_params:
            all_exps.extend(p["expirations"])
        if all_exps:
            expiry = sorted(set(all_exps))[0]
        else:
            expiry = (datetime.now() + timedelta(days=3)).strftime("%Y%m%d")
    else:
        expiry = (datetime.now() + timedelta(days=3)).strftime("%Y%m%d")

    # Find a strike near current SPX level (~5900)
    strike = 5900.0

    opt = Contract()
    opt.symbol = "SPX"
    opt.secType = "OPT"
    opt.exchange = "SMART"
    opt.currency = "USD"
    opt.lastTradeDateOrContractMonth = expiry
    opt.strike = strike
    opt.right = "C"
    opt.multiplier = "100"

    app.contract_details_list.clear()
    app.contract_event.clear()
    app.errors.clear()
    app.reqContractDetails(1003, opt)

    if not app.contract_event.wait(timeout=10):
        print(f"  [WARN] SPX {expiry} C{strike} 合约查询超时")
    elif not app.contract_details_list:
        print(f"  [WARN] SPX {expiry} C{strike} 合约未找到, 尝试 CBOE 交易所...")
        _print_errors(app, 1003)

        # Retry with CBOE exchange
        opt.exchange = "CBOE"
        app.contract_details_list.clear()
        app.contract_event.clear()
        app.errors.clear()
        app.reqContractDetails(1004, opt)

        if not app.contract_event.wait(timeout=10):
            print(f"  [FAIL] CBOE 查询也超时")
        elif not app.contract_details_list:
            print(f"  [FAIL] SPX 期权合约无法解析")
            _print_errors(app, 1004)

    if app.contract_details_list:
        cd = app.contract_details_list[0]
        c = cd.contract
        print(f"  [OK] 合约: {c.symbol} {c.lastTradeDateOrContractMonth} "
              f"{c.right} {c.strike} @ {c.exchange}")
        print(f"  conId: {c.conId}")
        print(f"  multiplier: {c.multiplier}")
        print()
        print(f"  交易时间 (tradingHours):")
        _print_trading_hours(cd.tradingHours)
        print()
        print(f"  液态时间 (liquidHours):")
        _print_trading_hours(cd.liquidHours)

        opt_for_tick = c  # save for Step 4
    else:
        opt_for_tick = None
    print()

    # ── Step 4: Try subscribing to market data ───────────────────────
    print("─" * 65)
    print("Step 4: 测试行情订阅 (确认数据权限)")
    print("─" * 65)

    if opt_for_tick:
        app.tick_data.clear()
        app.errors.clear()
        app.reqMktData(1005, opt_for_tick, "", False, False, [])

        print(f"  等待行情数据 (5 秒)...")
        time.sleep(5)

        app.cancelMktData(1005)

        if app.tick_data:
            print(f"  [OK] 收到行情数据:")
            for k, v in sorted(app.tick_data.items()):
                print(f"    {k}: {v}")
        else:
            print(f"  [WARN] 未收到行情数据")
            relevant = [(r, c, m) for r, c, m in app.errors if r == 1005]
            if relevant:
                for r, c, m in relevant:
                    print(f"    错误 [{c}]: {m}")
            else:
                print(f"    可能缺少 CBOE 实时行情数据订阅")
    else:
        print("  [SKIP] 无可用合约, 跳过行情测试")
    print()

    # ── Step 5: Summary ──────────────────────────────────────────────
    print("─" * 65)
    print("检查结果汇总")
    print("─" * 65)

    # Print all errors collected
    if app.errors:
        print("\n  所有错误/警告:")
        seen = set()
        for r, c, m in app.errors:
            key = (c, m)
            if key not in seen:
                seen.add(key)
                print(f"    [{c}] {m}")
    print()

    # Disconnect
    try:
        app.disconnect()
    except Exception:
        pass
    print("[DONE] 检查完成")


def _print_errors(app, req_id):
    relevant = [(c, m) for r, c, m in app.errors if r == req_id]
    for c, m in relevant:
        print(f"    错误 [{c}]: {m}")


def _print_trading_hours(hours_str: str):
    """Parse and display IBKR tradingHours format.
    Format: "20260611:0930-20260611:1615;20260612:0930-20260612:1615"
    or with CLOSED entries.
    """
    if not hours_str:
        print("    (无)")
        return
    segments = hours_str.split(";")
    for seg in segments[:10]:  # show first 10 segments
        seg = seg.strip()
        if not seg:
            continue
        if "CLOSED" in seg:
            print(f"    {seg}")
        elif "-" in seg:
            parts = seg.split("-")
            if len(parts) == 2:
                start, end = parts
                print(f"    {_fmt_ibkr_dt(start)} → {_fmt_ibkr_dt(end)}")
            else:
                print(f"    {seg}")
        else:
            print(f"    {seg}")
    if len(segments) > 10:
        print(f"    ... 共 {len(segments)} 个时段")


def _fmt_ibkr_dt(dt_str: str) -> str:
    """Format '20260611:0930' -> '2026-06-11 09:30'."""
    dt_str = dt_str.strip()
    if len(dt_str) == 13 and ":" in dt_str:
        d, t = dt_str.split(":")
        return f"{d[:4]}-{d[4:6]}-{d[6:8]} {t[:2]}:{t[2:]}"
    return dt_str


if __name__ == "__main__":
    main()
