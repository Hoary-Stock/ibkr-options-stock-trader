"""成交提示音 —— 真正成交时播放声音。

播放优先级:
  1. `sounds/BUY.(wav|mp3)` / `sounds/SELL.(wav|mp3)` —— 语音文件 (可用
     GPT-SoVITS 生成); 找不到买/卖专用时回退 `sounds/FILL.(wav|mp3)`;
  2. 都没有 → winsound 蜂鸣 (买: 升调 / 卖: 降调)。

播放放在**后台线程**: Windows MCI 的 `play ... wait` 会阻塞,绝不能卡住 Qt GUI 线程。
零额外依赖 (ctypes + winmm + winsound), 与 trading_signal 的播放方式一致。
"""

import os
import ctypes
import threading

SOUNDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")


def _find(name: str) -> str | None:
    for ext in (".wav", ".mp3"):
        p = os.path.join(SOUNDS_DIR, name + ext)
        if os.path.isfile(p):
            return p
    return None


def _play_file(path: str):
    """用 Windows MCI 播放 wav/mp3 (零依赖)。会阻塞, 故仅在后台线程调用。"""
    mci = ctypes.windll.winmm.mciSendStringW
    buf = ctypes.create_unicode_buffer(256)
    alias = "_ibkr_fill"
    mci(f'close {alias}', buf, 255, 0)
    mci(f'open "{path}" type mpegvideo alias {alias}', buf, 255, 0)
    mci(f'play {alias} wait', buf, 255, 0)
    mci(f'close {alias}', buf, 255, 0)


def _beep(is_buy: bool):
    try:
        import winsound
        if is_buy:
            winsound.Beep(880, 120)
            winsound.Beep(1320, 160)
        else:
            winsound.Beep(1320, 120)
            winsound.Beep(880, 160)
    except Exception:
        pass


def play_fill(side: str):
    """成交时播放提示音 (非阻塞)。

    side: IBKR 成交方向 'BOT'/'SLD' (或 'BUY'/'SELL')。
    """
    is_buy = str(side).upper() in ("BOT", "BUY", "B")
    name = "BUY" if is_buy else "SELL"

    def run():
        path = _find(name) or _find("FILL")
        try:
            if path:
                _play_file(path)
            else:
                _beep(is_buy)
        except Exception:
            _beep(is_buy)

    threading.Thread(target=run, daemon=True, name="fill-sound").start()
