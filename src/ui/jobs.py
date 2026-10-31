# -*- coding: utf-8 -*-
"""把阻塞任务放到工作线程执行：UI 保持响应（可重绘、可取消），结果同步返回。

用于「测试连接」「LLM 切分」这类可能联网几十秒的操作——原先它们直接跑在
UI 线程上，期间窗口无法重绘也无法取消。这里用 QThread 跑任务 + 局部事件循环
等待 + 模态进度框（ApplicationModal 防止重入），语义仍是同步的，调用方无感。
"""

from __future__ import annotations

from typing import Any, Callable, Set, Tuple

from PySide6.QtCore import QEventLoop, Qt, QThread, Signal
from PySide6.QtWidgets import QApplication, QProgressDialog

# 正在运行的线程（不挂 parent：调用方窗口可能在任务结束前就被关掉，
# 若把线程挂成窗口子对象，销毁运行中的 QThread 会直接 abort）
_alive: Set[QThread] = set()


class _Runner(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self._fn = fn

    def run(self) -> None:  # noqa: D102 - 工作线程入口
        try:
            self.done.emit(self._fn())
        except Exception as exc:  # noqa: BLE001 - 统一转成可展示的错误文本
            self.failed.emit(str(exc) or exc.__class__.__name__)


def run_blocking(parent, fn: Callable[[], Any], title: str = "请稍候…",
                 cancelable: bool = True) -> Tuple[bool, Any]:
    """在工作线程执行 fn，阻塞调用方直到完成/取消，期间 UI 可重绘。

    返回 (completed, result)：
      · completed=False 表示用户点了取消（fn 在后台继续跑完，结果被丢弃）；
      · fn 抛异常时，转成 RuntimeError（保留原始消息）抛回调用方。
    """
    app = QApplication.instance()
    progress = QProgressDialog(title, "取消" if cancelable else None, 0, 0, parent)
    progress.setWindowModality(Qt.ApplicationModal)
    progress.setMinimumDuration(0)
    progress.setAutoClose(False)
    progress.setAutoReset(False)
    if not cancelable:
        progress.setCancelButton(None)
    progress.show()
    if app is not None:
        app.processEvents()

    state = {"result": None, "error": None, "done": False, "cancelled": False}
    loop = QEventLoop()
    runner = _Runner(fn)
    _alive.add(runner)

    def on_ok(value):
        state["result"] = value
        state["done"] = True
        loop.quit()

    def on_bad(msg):
        state["error"] = msg
        state["done"] = True
        loop.quit()

    def on_cancel():
        state["cancelled"] = True
        loop.quit()

    def on_finished():
        _alive.discard(runner)
        runner.deleteLater()

    runner.done.connect(on_ok)
    runner.failed.connect(on_bad)
    progress.canceled.connect(on_cancel)
    runner.finished.connect(on_finished)
    runner.start()
    loop.exec()

    # 取消后任务可能还在跑：断开连接，迟到的结果直接丢弃。
    # 注意 progress.close() 会触发 QProgressDialog 的 canceled 信号，
    # 必须先断开 on_cancel，否则成功的结果会被误判成“已取消”。
    runner.done.disconnect(on_ok)
    runner.failed.disconnect(on_bad)
    progress.canceled.disconnect(on_cancel)
    progress.close()
    progress.deleteLater()

    if state["cancelled"] or not state["done"]:
        return False, None
    if state["error"] is not None:
        raise RuntimeError(state["error"])
    return True, state["result"]


__all__ = ["run_blocking"]
