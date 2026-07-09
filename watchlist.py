"""自选监控 (Watch List) — 把标的/合约加入列表, 实时看价 + 到价警报。

与 conditional_orders.py 同构: 依赖通过 configure() 注入 (get_tick/订阅/退订),
QTimer 0.5s 巡检; 持久化 watchlist.json (gitignore, 个人数据);
启动 resume() 加载时**自动清理过期合约** (期权 expiry < 今日, 期货月份 < 本月);
警报 (高于/低于某价) 为**一次性**: 触发后该方向警报值自动清零, 避免反复响。
"""

import os
import json
import time
from dataclasses import dataclass

from PyQt5.QtCore import QObject, pyqtSignal, QTimer

from models import OptionInfo
from conditional_orders import _current_price

_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "watchlist.json")
_TICK_MS = 500   # 价格刷新/警报巡检间隔 (用户要求 0.5s)


@dataclass
class WatchItem:
    """一条自选监控: 合约 + 可选的高于/低于警报价 (0 = 未设)。"""
    option: OptionInfo
    alert_above: float = 0.0   # >0 时启用: 现价 >= 该值 → 警报
    alert_below: float = 0.0   # >0 时启用: 现价 <= 该值 → 警报

    @property
    def key(self) -> str:
        return self.option.to_ibkr_key()

    def is_expired(self, today: str) -> bool:
        """过期判定: 期权 expiry(YYYYMMDD) < 今日; 期货合约月份 < 本月。"""
        o = self.option
        if o.right in ("C", "P") and len(o.expiry) == 8:
            return o.expiry < today
        if o.right == "FUT" and len(o.expiry) >= 6:
            return o.expiry[:6] < today[:6]
        return False   # 正股不过期

    def to_dict(self) -> dict:
        o = self.option
        return {
            "symbol": o.symbol, "expiry": o.expiry, "strike": o.strike,
            "right": o.right, "con_id": o.con_id,
            "alert_above": self.alert_above, "alert_below": self.alert_below,
        }

    @staticmethod
    def from_dict(d: dict) -> "WatchItem":
        opt = OptionInfo(
            symbol=d["symbol"], expiry=d.get("expiry", ""),
            strike=d.get("strike", 0.0), right=d.get("right", "C"),
            con_id=d.get("con_id", 0),
        )
        return WatchItem(
            option=opt,
            alert_above=float(d.get("alert_above", 0.0) or 0.0),
            alert_below=float(d.get("alert_below", 0.0) or 0.0),
        )


class WatchListManager(QObject):
    """管理自选列表: 订阅行情、0.5s 巡检价格与警报、持久化。"""

    changed = pyqtSignal()                    # 列表结构/警报设置变化 (面板重建)
    ticked = pyqtSignal()                     # 0.5s 心跳 (面板刷新价格)
    alerted = pyqtSignal(object, str, float)  # (WatchItem, "above"/"below", 现价)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: dict[str, WatchItem] = {}   # key -> WatchItem
        self._req_ids: dict[str, int] = {}       # key -> tick reqId

        # 注入的回调 (默认空操作, configure 后生效)
        self._get_tick = lambda key: {}
        self._subscribe = lambda opt: None
        self._unsubscribe = lambda req_id: None

        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.start(_TICK_MS)

    def configure(self, get_tick, subscribe, unsubscribe):
        self._get_tick = get_tick
        self._subscribe = subscribe
        self._unsubscribe = unsubscribe

    # ── 查询 ──────────────────────────────────────────────────────────
    def items(self) -> list:
        return list(self._items.values())

    def price_of(self, key: str) -> dict:
        """最新 tick (bid/ask/last), 供面板显示。"""
        return self._get_tick(key) or {}

    # ── 增删 / 警报设置 ──────────────────────────────────────────────
    def add(self, option: OptionInfo) -> bool:
        """加入自选; 已存在返回 False。"""
        item = WatchItem(option=option)
        if item.key in self._items:
            return False
        self._items[item.key] = item
        self._ensure_subscribed(item)
        self._save()
        self.changed.emit()
        return True

    def remove(self, key: str):
        if self._items.pop(key, None) is None:
            return
        self._drop_subscription(key)
        self._save()
        self.changed.emit()

    def set_alert(self, key: str, above: float = None, below: float = None):
        """更新某条的警报价 (None = 不改该方向; 0 = 清除)。"""
        item = self._items.get(key)
        if item is None:
            return
        if above is not None:
            item.alert_above = max(float(above), 0.0)
        if below is not None:
            item.alert_below = max(float(below), 0.0)
        self._save()

    # ── 连接生命周期 (MainWindow 调用) ────────────────────────────────
    def resume(self):
        """连接成功后调用: 从磁盘恢复 (清理过期) 并订阅行情。"""
        self._load()
        for item in self._items.values():
            self._ensure_subscribed(item)
        self.changed.emit()

    def suspend(self):
        """断开时调用: 退订行情但保留列表 (重连后 resume)。"""
        for key in list(self._req_ids):
            self._drop_subscription(key)

    # ── 巡检 (0.5s) ──────────────────────────────────────────────────
    def _tick(self):
        if not self._items:
            return
        fired = False
        for item in list(self._items.values()):
            price = _current_price(self._get_tick(item.key))
            if price <= 0:
                continue
            if item.alert_above > 0 and price >= item.alert_above:
                level, item.alert_above = item.alert_above, 0.0  # 一次性
                print(f"[WATCH] {item.option.display_name} 现价 {price:.2f} "
                      f"≥ {level:.2f} 高价警报", flush=True)
                self.alerted.emit(item, "above", price)
                fired = True
            if item.alert_below > 0 and price <= item.alert_below:
                level, item.alert_below = item.alert_below, 0.0
                print(f"[WATCH] {item.option.display_name} 现价 {price:.2f} "
                      f"≤ {level:.2f} 低价警报", flush=True)
                self.alerted.emit(item, "below", price)
                fired = True
        if fired:
            self._save()
            self.changed.emit()   # 警报清零 → 面板重建显示
        self.ticked.emit()

    # ── 行情订阅 ──────────────────────────────────────────────────────
    def _ensure_subscribed(self, item: WatchItem):
        if item.key in self._req_ids:
            return
        try:
            req_id = self._subscribe(item.option)
            if req_id is not None:
                self._req_ids[item.key] = req_id
        except Exception as e:
            print(f"[WATCH] subscribe error: {e}", flush=True)

    def _drop_subscription(self, key: str):
        req_id = self._req_ids.pop(key, None)
        if req_id is not None:
            try:
                self._unsubscribe(req_id)
            except Exception:
                pass

    # ── 持久化 ────────────────────────────────────────────────────────
    def _save(self):
        try:
            with open(_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump([it.to_dict() for it in self._items.values()],
                          f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[WATCH] save error: {e}", flush=True)

    def _load(self):
        if not os.path.exists(_STATE_PATH):
            return
        try:
            with open(_STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[WATCH] load error: {e}", flush=True)
            return
        self._items.clear()
        dropped = 0
        today = time.strftime("%Y%m%d")
        for d in data:
            try:
                item = WatchItem.from_dict(d)
            except Exception:
                continue
            if item.is_expired(today):
                dropped += 1   # 过期合约直接丢弃
                continue
            self._items[item.key] = item
        if dropped:
            print(f"[WATCH] 已清理 {dropped} 条过期自选", flush=True)
            self._save()

    def cleanup(self):
        self._timer.stop()
        for key in list(self._req_ids):
            self._drop_subscription(key)
