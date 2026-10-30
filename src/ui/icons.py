# -*- coding: utf-8 -*-
"""线性图标：用 QPainter 直接绘制，避免依赖外部图标资源，风格统一（1.8px 圆头描边）"""

from __future__ import annotations

import os

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from .tokens import COLORS

_CACHE: dict = {}

_VB = 24.0  # 视图盒尺寸


def _p(points):
    path = QPainterPath()
    first = True
    for x, y in points:
        if first:
            path.moveTo(x, y)
            first = False
        else:
            path.lineTo(x, y)
    return path


def _shape(name: str) -> QPainterPath:
    path = QPainterPath()
    if name == "open":
        path.moveTo(3, 7)
        path.lineTo(9, 7)
        path.lineTo(11, 9.5)
        path.lineTo(20, 9.5)
        path.lineTo(20, 18.5)
        path.lineTo(3, 18.5)
        path.closeSubpath()
        path.moveTo(3, 7)
        path.lineTo(3, 5.5)
        path.lineTo(9, 5.5)
    elif name == "import":
        path.addPath(_p([(12, 4), (12, 14)]))
        path.addPath(_p([(8, 10), (12, 14), (16, 10)]))
        path.addPath(_p([(4, 15), (4, 19), (20, 19), (20, 15)]))
    elif name == "merge":
        path.addRoundedRect(QRectF(3.5, 4, 10, 13), 2, 2)
        path.addRoundedRect(QRectF(10.5, 7, 10, 13), 2, 2)
    elif name == "check":
        path.moveTo(6.5, 12.5)
        path.lineTo(10.5, 16.5)
        path.lineTo(17.5, 8)
    elif name == "check-circle":
        path.addEllipse(QRectF(3, 3, 18, 18))
        path.moveTo(8, 12.2)
        path.lineTo(11, 15.2)
        path.lineTo(16.3, 9.4)
    elif name == "undo":
        path.addPath(_p([(8.5, 7.5), (4.5, 11.5), (8.5, 15.5)]))
        path.moveTo(4.5, 11.5)
        path.lineTo(14, 11.5)
        path.cubicTo(18, 11.5, 19.5, 15.5, 17.5, 18)
    elif name == "redo":
        path.addPath(_p([(15.5, 7.5), (19.5, 11.5), (15.5, 15.5)]))
        path.moveTo(19.5, 11.5)
        path.lineTo(10, 11.5)
        path.cubicTo(6, 11.5, 4.5, 15.5, 6.5, 18)
    elif name == "export":
        path.addPath(_p([(12, 15), (12, 4.5)]))
        path.addPath(_p([(8, 8.5), (12, 4.5), (16, 8.5)]))
        path.addPath(_p([(4, 15.5), (4, 19.5), (20, 19.5), (20, 15.5)]))
    elif name == "search":
        path.addEllipse(QRectF(5, 5, 11, 11))
        path.addPath(_p([(16, 16), (20, 20)]))
    elif name == "book":
        path.moveTo(5, 4.5)
        path.lineTo(5, 19.5)
        path.lineTo(18.5, 19.5)
        path.lineTo(18.5, 4.5)
        path.closeSubpath()
        path.addPath(_p([(8.5, 4.5), (8.5, 19.5)]))
    elif name == "chevron-right":
        path.addPath(_p([(9.5, 6.5), (15, 12), (9.5, 17.5)]))
    elif name == "chevron-down":
        path.addPath(_p([(6.5, 9.5), (12, 15), (17.5, 9.5)]))
    elif name == "plus":
        path.addPath(_p([(12, 5.5), (12, 18.5)]))
        path.addPath(_p([(5.5, 12), (18.5, 12)]))
    elif name == "trash":
        path.addPath(_p([(4.5, 7), (19.5, 7)]))
        path.addPath(_p([(9.5, 7), (9.5, 4.8), (14.5, 4.8), (14.5, 7)]))
        path.moveTo(6.5, 7)
        path.lineTo(7.5, 19.5)
        path.lineTo(16.5, 19.5)
        path.lineTo(17.5, 7)
        path.addPath(_p([(10.5, 10.5), (10.5, 16.5)]))
        path.addPath(_p([(13.5, 10.5), (13.5, 16.5)]))
    elif name == "arrow-up":
        path.addPath(_p([(12, 19), (12, 5.5)]))
        path.addPath(_p([(6.5, 11), (12, 5.5), (17.5, 11)]))
    elif name == "arrow-down":
        path.addPath(_p([(12, 5), (12, 18.5)]))
        path.addPath(_p([(6.5, 13), (12, 18.5), (17.5, 13)]))
    elif name == "folder":
        path.moveTo(3.5, 7.5)
        path.lineTo(3.5, 18.5)
        path.lineTo(20.5, 18.5)
        path.lineTo(20.5, 9)
        path.lineTo(11.5, 9)
        path.lineTo(9, 6.5)
        path.lineTo(3.5, 6.5)
        path.closeSubpath()
    elif name == "folder-plus":
        path.moveTo(3.5, 7.5)
        path.lineTo(3.5, 18.5)
        path.lineTo(20.5, 18.5)
        path.lineTo(20.5, 9)
        path.lineTo(11.5, 9)
        path.lineTo(9, 6.5)
        path.lineTo(3.5, 6.5)
        path.closeSubpath()
        path.addPath(_p([(12, 11.5), (12, 16.5)]))
        path.addPath(_p([(9.5, 14), (14.5, 14)]))
    elif name == "doc":
        path.moveTo(6, 3.5)
        path.lineTo(14, 3.5)
        path.lineTo(18, 7.5)
        path.lineTo(18, 20.5)
        path.lineTo(6, 20.5)
        path.closeSubpath()
        path.addPath(_p([(14, 3.5), (14, 7.5), (18, 7.5)]))
    elif name == "alert":
        path.moveTo(12, 4.5)
        path.lineTo(21, 19.5)
        path.lineTo(3, 19.5)
        path.closeSubpath()
        path.addPath(_p([(12, 10), (12, 14.5)]))
        path.addPath(_p([(12, 17), (12, 17.2)]))
    elif name == "close":
        path.addPath(_p([(6.5, 6.5), (17.5, 17.5)]))
        path.addPath(_p([(17.5, 6.5), (6.5, 17.5)]))
    elif name == "refresh":
        path.arcMoveTo(QRectF(5, 5, 14, 14), 60)
        path.arcTo(QRectF(5, 5, 14, 14), 60, 280)
        path.addPath(_p([(17, 4.5), (18.6, 8.6), (14.5, 9.2)]))
    elif name == "save":
        path.moveTo(5, 5)
        path.lineTo(16, 5)
        path.lineTo(19, 8)
        path.lineTo(19, 19)
        path.lineTo(5, 19)
        path.closeSubpath()
        path.addRect(QRectF(8.5, 12.5, 7, 6.5))
    elif name == "chevron-up":
        path.addPath(_p([(6.5, 14.5), (12, 9), (17.5, 14.5)]))
    elif name == "more":
        for y in (6.5, 12.0, 17.5):
            path.addEllipse(QRectF(10.4, y - 1.6, 3.2, 3.2))
    elif name == "dot":
        path.addEllipse(QRectF(10, 10, 4, 4))
    return path


def icon(name: str, color: str = None, size: int = 18) -> QIcon:
    color = color or COLORS["text_secondary"]
    key = (name, color, size)
    if key in _CACHE:
        return _CACHE[key]

    scale = 3 if size <= 20 else 2
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(size * scale / _VB, size * scale / _VB)
    pen = QPen(QColor(color))
    pen.setWidthF(1.8)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(_shape(name))
    p.end()
    pm.setDevicePixelRatio(scale)

    ic = QIcon(pm)
    _CACHE[key] = ic
    return ic


def app_icon() -> QIcon:
    """窗口图标：优先用 assets 下的 ico，其次退回绘制"""
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for rel in ("assets/chapflow-256.png", "assets/chapflow.ico"):
        path = os.path.join(here, rel)
        if os.path.exists(path):
            ic = QIcon(path)
            if not ic.isNull():
                return ic
    return icon("book", COLORS["accent"], 32)
