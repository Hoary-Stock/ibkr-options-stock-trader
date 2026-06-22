"""本地条件单管理器 — 止盈/止损 (到价才发限价单到 IBKR)。

「本地条件单」在到价前**不发到 IBKR**:本程序每 0.5 秒读现价,触发后才提交一张限价单。
好处:规避 IBKR「同合约不能双向挂单」(错误 201);坏处:**只在程序运行时监控**,
关程序 / 崩溃 / 断网就不会触发(已持久化到 conditional_orders.json,重连后恢复监控)。

原生 STP LMT(服务器端)不归本管理器,由 MainWindow 直接调 engine.place_stop_limit_order。
"""

import os
import json
import time

from PyQt5.QtCore import QObject, pyqtSignal, QTimer

from models import ConditionalOrder, OptionInfo


_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "conditional_orders.json")
_RETRY_COOLDOWN_S = 5.0   # 触发后下单失败的重试间隔


def _current_price(tick: dict) -> float:
    """现价: 优先 last, 退回中价/单边。"""
    last = tick.get("last", 0) or 0
    if last > 0:
        return last
    bid = tick.get("bid", 0) or 0
    ask = tick.get("ask", 0) or 0
    if bid > 0 and ask > 0:
        return (bid + ask) / 2
    return bid or ask or 0.0


class ConditionalOrderManager(QObject):
    """监控本地条件单, 到价触发下单。所有外部依赖通过 configure() 注入,
    与具体引擎/品种路由解耦 (MainWindow 提供 place 回调按品种发单)。"""

    changed = pyqtSignal()                 # 列表变化 (UI 刷新)
    triggered = pyqtSignal(object, int)    # (ConditionalOrder, order_id) 已触发并下单
    failed = pyqtSignal(object, str)       # (ConditionalOrder, msg) 触发后下单失败

    def __init__(self, parent=None):
        super().__init__(parent)
        self._conds: dict[int, ConditionalOrder] = {}
        self._req_ids: dict[int, int] = {}        # cond_id -> tick reqId
        self._cooldown: dict[int, float] = {}     # cond_id -> 下次可重试时间
        self._next_id = 1

        # 注入的回调 (默认空操作, configure 后生效)
        self._get_tick = lambda key: {}
        self._place = lambda opt, action, lmt, qty, outside: -1
        self._subscribe = lambda opt: None
        self._unsubscribe = lambda req_id: None

        self._timer = QTimer()
        self._timer.timeout.connect(self._check)
        self._timer.start(500)

    # ── 依赖注入 ──────────────────────────────────────────────────────
    def configure(self, get_tick, place, subscribe, unsubscribe):
        self._get_tick = get_tick
        self._place = place
        self._subscribe = subscribe
        self._unsubscribe = unsubscribe

    # ── 查询 ──────────────────────────────────────────────────────────
    def all(self) -> list:
        return list(self._conds.values())

    def for_key(self, key: str) -> list:
        return [c for c in self._conds.values() if c.key == key]

    # ── 武装 / 取消 ───────────────────────────────────────────────────
    def arm(self, option: OptionInfo, kind: str, trigger_price: float,
            limit_price: float, quantity: int, outside_rth: bool) -> ConditionalOrder:
        """新增一个本地条件单并开始监控。kind: 'TP'/'SL'。"""
        cond = ConditionalOrder(
            cond_id=self._next_id, option=option, kind=kind, action="SELL",
            trigger_price=float(trigger_price), limit_price=float(limit_price),
            quantity=int(quantity), native=False, outside_rth=bool(outside_rth),
        )
        self._next_id += 1
        self._conds[cond.cond_id] = cond
        self._ensure_subscribed(cond)
        self._save()
        self.changed.emit()
        return cond

    def cancel(self, cond_id: int):
        cond = self._conds.pop(cond_id, None)
        if cond is None:
            return
        self._drop_subscription(cond_id)
        self._cooldown.pop(cond_id, None)
        self._save()
        self.changed.emit()

    def cancel_for_key(self, key: str):
        for cid in [c.cond_id for c in self.for_key(key)]:
            self._conds.pop(cid, None)
            self._drop_subscription(cid)
            self._cooldown.pop(cid, None)
        self._save()
        self.changed.emit()

    def clear_all(self, persist: bool = True):
        for cid in list(self._req_ids):
            self._drop_subscription(cid)
        self._conds.clear()
        self._cooldown.clear()
        if persist:
            self._save()
        self.changed.emit()

    # ── 连接生命周期 (MainWindow 调用) ────────────────────────────────
    def resume(self):
        """连接成功后调用: 从磁盘恢复条件单并重新订阅各合约行情。"""
        self._load()
        for cond in self._conds.values():
            self._ensure_subscribed(cond)
        self.changed.emit()

    def suspend(self):
        """断开时调用: 退订行情但**保留**条件单 (重连后 resume)。"""
        for cid in list(self._req_ids):
            self._drop_subscription(cid)

    # ── 监控循环 ──────────────────────────────────────────────────────
    def _check(self):
        if not self._conds:
            return
        now = time.time()
        for cond in list(self._conds.values()):
            if self._cooldown.get(cond.cond_id, 0) > now:
                continue
            price = _current_price(self._get_tick(cond.key))
            if price <= 0 or not cond.is_triggered(price):
                continue
            # 触发 → 下单
            try:
                order_id = self._place(
                    cond.option, cond.action, cond.limit_price,
                    cond.quantity, cond.outside_rth,
                )
            except Exception as e:
                order_id = -1
                print(f"[COND] place error: {e}", flush=True)
            if order_id and order_id > 0:
                print(f"[COND] {cond.kind_label} 触发 @ {price:.2f} → 下单 "
                      f"{cond.option.display_name} lmt={cond.limit_price:.2f} "
                      f"id={order_id}", flush=True)
                self._conds.pop(cond.cond_id, None)
                self._drop_subscription(cond.cond_id)
                self._cooldown.pop(cond.cond_id, None)
                self._save()
                self.triggered.emit(cond, order_id)
                self.changed.emit()
            else:
                # 下单失败 (如资金不足/反向单 201) → 冷却后重试, 不丢失止损
                self._cooldown[cond.cond_id] = now + _RETRY_COOLDOWN_S
                self.failed.emit(cond, "下单失败, 稍后重试 (检查资金/反向挂单/连接)")

    # ── 行情订阅 ──────────────────────────────────────────────────────
    def _ensure_subscribed(self, cond: ConditionalOrder):
        if cond.cond_id in self._req_ids:
            return
        try:
            req_id = self._subscribe(cond.option)
            if req_id is not None:
                self._req_ids[cond.cond_id] = req_id
        except Exception as e:
            print(f"[COND] subscribe error: {e}", flush=True)

    def _drop_subscription(self, cond_id: int):
        req_id = self._req_ids.pop(cond_id, None)
        if req_id is not None:
            try:
                self._unsubscribe(req_id)
            except Exception:
                pass

    # ── 持久化 ────────────────────────────────────────────────────────
    def _save(self):
        try:
            with open(_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump([c.to_dict() for c in self._conds.values()],
                          f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[COND] save error: {e}", flush=True)

    def _load(self):
        if not os.path.exists(_STATE_PATH):
            return
        try:
            with open(_STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[COND] load error: {e}", flush=True)
            return
        self._conds.clear()
        max_id = 0
        for d in data:
            try:
                cond = ConditionalOrder.from_dict(d)
                self._conds[cond.cond_id] = cond
                max_id = max(max_id, cond.cond_id)
            except Exception:
                continue
        self._next_id = max(self._next_id, max_id + 1)

    def cleanup(self):
        self._timer.stop()
        for cid in list(self._req_ids):
            self._drop_subscription(cid)
