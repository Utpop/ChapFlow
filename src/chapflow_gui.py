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


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
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
