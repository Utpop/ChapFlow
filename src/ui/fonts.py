# -*- coding: utf-8 -*-
"""字体引导：正常桌面直接可用；离屏/精简环境缺少字体库时手动加载系统中文字体"""

from __future__ import annotations

import os
import sys

from PySide6.QtGui import QFont, QFontDatabase

from .tokens import FONTS

_CANDIDATES = [
    r"C:/Windows/Fonts/msyh.ttc",
    r"C:/Windows/Fonts/msyhbd.ttc",
    r"C:/Windows/Fonts/msyhl.ttc",
    r"C:/Windows/Fonts/simhei.ttf",
    r"C:/Windows/Fonts/simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def resolve_family() -> str:
    """确保界面字体可用，返回最终使用的族名"""
    families = set(QFontDatabase.families())
    if FONTS["family"] in families:
        return FONTS["family"]

    for path in _CANDIDATES:
        if not os.path.exists(path):
            continue
        fid = QFontDatabase.addApplicationFont(path)
        if fid >= 0:
            names = QFontDatabase.applicationFontFamilies(fid) or []
            for want in (FONTS["family"], "Microsoft YaHei", "SimHei", "SimSun", "PingFang SC"):
                if want in names:
                    return want
            if names:
                return names[0]

    # 实在没有中文字体时退回系统默认，至少不崩
    return QFont().defaultFamily()


def apply_app_font(app) -> str:
    family = resolve_family()
    f = QFont(family, int(round(FONTS["body"])))
    f.setHintingPreference(QFont.PreferFullHinting)
    app.setFont(f)
    return family
