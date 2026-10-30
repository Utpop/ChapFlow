#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ChapFlow 图形界面入口

    python src/chapflow_gui.py [book.epub]
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui import icons  # noqa: E402
from ui.fonts import apply_app_font  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from ui.theme import build_qss  # noqa: E402
from ui.tokens import FONTS  # noqa: E402


APP_USER_MODEL_ID = "ChapFlow.Desktop.1"


def _set_windows_app_id() -> None:
    """让 Windows 把本进程当作独立应用（否则任务栏显示 Python 解释器的图标）"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def _selftest() -> int:
    """打包自检（--selftest）：验证资源随包分发、界面能起来、能导出一本合规 EPUB。

    供打包后验证 exe 用，正常启动不会走到这里。退出码 0 表示通过。
    """
    import tempfile
    from ui.paths import assets_dir, icon_dir
    from PySide6.QtWidgets import QApplication

    missing = [p for p in (assets_dir("chapflow.ico"), assets_dir("chapflow-256.png"),
                           icon_dir())
               if not os.path.exists(p)]
    if missing:
        print("SELFTEST-FAIL 资源缺失: %s" % missing)
        return 1
    if len(os.listdir(icon_dir())) < 10:
        print("SELFTEST-FAIL 图标过少: %d" % len(os.listdir(icon_dir())))
        return 1

    app = QApplication(["chapflow-selftest"])
    app.setStyle("Fusion")
    from ui.theme import build_qss
    app.setStyleSheet(build_qss())
    from ui.icons import app_icon, icon
    if app_icon().isNull():
        print("SELFTEST-FAIL 应用图标为空")
        return 1
    if icon("folder").isNull() or icon("doc").isNull():
        print("SELFTEST-FAIL 目录树图标为空")
        return 1

    from ui.main_window import MainWindow
    w = MainWindow()
    w.show()
    print("SELFTEST-OK resources=%d icons" % len(os.listdir(icon_dir())))
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if "--selftest" in argv[1:]:
        return _selftest()
    _set_windows_app_id()
    app = QApplication(argv)
    app.setApplicationName("ChapFlow")
    app.setApplicationDisplayName("ChapFlow")
    app.setOrganizationName("ChapFlow")
    app.setStyle("Fusion")            # QSS 在 Fusion 上行为最稳定
    apply_app_font(app)
    app.setStyleSheet(build_qss())
    app.setWindowIcon(icons.app_icon())

    win = MainWindow()
    win.show()
    for arg in argv[1:]:
        if arg.lower().endswith(".epub") and os.path.exists(arg):
            win.load(arg)
            break
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
