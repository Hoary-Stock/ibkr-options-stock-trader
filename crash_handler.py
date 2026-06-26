"""全局崩溃捕获 —— 交易程序绝不能静默闪退。

把以下三类崩溃统统写进日志文件 (logs/app[_gw]_*.log), 并尽量让程序**不退出**:

1. **主线程未处理异常** (含 PyQt **槽函数**里抛出的异常) —— `sys.excepthook`。
   PyQt5 默认在槽函数抛异常后会 **直接 abort 整个进程** (闪退), 装上自定义
   excepthook 后改为: 记录完整 traceback + 弹窗提示, **事件循环继续**, 交易会话
   不因单个槽异常整体崩掉。
2. **工作线程异常** (do_connect / do_load / reader 线程等) —— `threading.excepthook`。
3. **硬崩溃** (C 层 segfault / abort, 如 ibapi/Qt 内部) —— `faulthandler`, 崩溃瞬间
   dump 出 C 级调用栈; Qt 的 `QtFatalMsg` 也会先 dump 再 abort。

用法 (在入口设置好日志重定向后, import main_window 之前调用一次):
    from crash_handler import install_crash_handler
    install_crash_handler(sys.stderr)   # sys.stderr 此时已是日志文件
"""

import sys
import faulthandler
import threading
import traceback
from datetime import datetime

_log_stream = None
_in_hook = False          # 防重入 (异常发生在绘制/弹窗里时, 避免无限递归)
_dialogs: list = []       # 持有非模态弹窗引用, 防被 GC


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _write(text: str):
    """写到日志流 + (若不同) stderr, 任何失败都吞掉, 绝不让记录本身再抛。"""
    seen = set()
    for s in (_log_stream, sys.stderr, sys.__stderr__):
        if s is None or id(s) in seen:
            continue
        seen.add(id(s))
        try:
            s.write(text)
            s.flush()
        except Exception:
            pass


def _show_dialog(title: str, summary: str, detail: str):
    """非模态弹窗提示 (不进入嵌套事件循环, 失败静默)。"""
    try:
        from PyQt5.QtWidgets import QApplication, QMessageBox
        from PyQt5.QtCore import Qt
        if QApplication.instance() is None:
            return
        box = QMessageBox()
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle(title)
        box.setText(summary)
        box.setDetailedText(detail)
        box.setStandardButtons(QMessageBox.Ok)
        box.setWindowModality(Qt.NonModal)
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.finished.connect(lambda _=0, b=box: _dialogs.remove(b)
                             if b in _dialogs else None)
        _dialogs.append(box)
        box.show()
    except Exception:
        pass


def _excepthook(exc_type, exc_value, exc_tb):
    global _in_hook
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    _write(f"\n==== 未处理异常 (主线程/槽函数) {_ts()} ====\n{tb}"
           f"================================================\n")
    if _in_hook:
        return  # 记录里又崩 → 只落日志, 不再弹窗, 避免递归
    _in_hook = True
    try:
        _show_dialog(
            "程序异常 (已记录日志, 未退出)",
            "捕获到一个未处理异常, 已写入日志文件。\n"
            "程序已尽量继续运行 —— 请立即核对持仓/挂单是否正常, "
            "必要时去 IBKR 客户端手动处理。",
            tb,
        )
    finally:
        _in_hook = False
    # 关键: 不调用 sys.exit / 不重新抛 → Qt 事件循环继续, 不闪退


def _thread_excepthook(args):
    if issubclass(args.exc_type, KeyboardInterrupt):
        return
    tb = "".join(traceback.format_exception(
        args.exc_type, args.exc_value, args.exc_traceback))
    name = getattr(args.thread, "name", "?")
    _write(f"\n==== 未处理异常 (线程 {name}) {_ts()} ====\n{tb}"
           f"================================================\n")


def _qt_msg_handler(mode, context, message):
    try:
        from PyQt5.QtCore import QtMsgType
        label = {
            QtMsgType.QtDebugMsg: "QtDebug",
            QtMsgType.QtInfoMsg: "QtInfo",
            QtMsgType.QtWarningMsg: "QtWarning",
            QtMsgType.QtCriticalMsg: "QtCritical",
            QtMsgType.QtFatalMsg: "QtFatal",
        }.get(mode, "Qt")
        _write(f"[{label} {_ts()}] {message}\n")
        if mode == QtMsgType.QtFatalMsg:
            # Qt 致命 → 进程即将 abort, 先把所有线程的 C 栈 dump 出来
            try:
                faulthandler.dump_traceback(
                    file=_log_stream or sys.stderr, all_threads=True)
            except Exception:
                pass
    except Exception:
        pass


def install_crash_handler(log_stream=None):
    """安装全局崩溃捕获。log_stream 一般传 sys.stderr (此时已重定向到日志文件)。"""
    global _log_stream
    _log_stream = log_stream if log_stream is not None else sys.stderr

    # 1) 硬崩溃 (segfault / abort) → C 级调用栈
    try:
        faulthandler.enable(file=_log_stream, all_threads=True)
    except Exception:
        pass

    # 2) 主线程未处理异常 (含 PyQt 槽函数异常 → 不再静默 abort)
    sys.excepthook = _excepthook

    # 3) 工作线程异常 (Python 3.8+)
    if hasattr(threading, "excepthook"):
        threading.excepthook = _thread_excepthook

    # 4) Qt 内部消息 (warning / critical / fatal) 落日志
    try:
        from PyQt5.QtCore import qInstallMessageHandler
        qInstallMessageHandler(_qt_msg_handler)
    except Exception:
        pass

    _write(f"[CRASH HANDLER] 已安装全局崩溃捕获 {_ts()}\n")
