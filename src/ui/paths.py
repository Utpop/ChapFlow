# -*- coding: utf-8 -*-
"""资源路径解析：源码运行与 PyInstaller 打包后都要能找到 assets/

打包后 __file__ 指向解包目录（sys._MEIPASS）里的模块，
层级与源码树不一样，因此不能再用 dirname 往上回溯。
"""

from __future__ import annotations

import os
import sys


def root_dir() -> str:
    """项目根目录（源码运行）或解包目录（frozen）"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    # src/ui/paths.py -> 上三级为项目根
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def assets_dir(*parts: str) -> str:
    return os.path.join(root_dir(), "assets", *parts)


def icon_dir() -> str:
    return assets_dir("icons")


def qss_assets_dir() -> str:
    """QSS 里的 url() 只认正斜杠"""
    return icon_dir().replace("\\", "/")
