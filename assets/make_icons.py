# -*- coding: utf-8 -*-
"""生成应用图标（透明底 + 多尺寸 + ICO）

用法：``py -3 assets/make_icons.py``（在工作区根目录执行，需要 PySide6）

为什么有这个脚本：原始素材是从白色背景上导出的 RGB 图，没有 alpha 通道，
装到任务栏/标题栏上就是一个白方块。这里从四边做连通洪泛把白底抠掉
（只抠「与外界连通」的纯白，文档页面内部的浅白保留），再按尺寸缩放
并重新打包 chapflow.ico。
"""

from __future__ import annotations

import os
import struct
import sys
from collections import deque

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

HERE = os.path.dirname(os.path.abspath(__file__))

MASTER = 512
SIZES = (16, 32, 48, 64, 256, 512)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _white(c) -> int:
    """像素的白度：取三通道最小值（越小越「不像白」）"""
    return min(c.red(), c.green(), c.blue())


def knock_out_background(src: QImage, white_cut: int = 246, trim: int = 3) -> QImage:
    """把与画布四边连通的近白像素置为全透明，并削掉边缘的白毛边"""
    img = src.convertToFormat(QImage.Format_ARGB32)
    w, h = img.width(), img.height()
    px = img.bits()                      # memoryview，按 4 字节 BGRA 访问
    stride = img.bytesPerLine()

    def at(x: int, y: int) -> int:
        return y * stride + x * 4

    seen = bytearray(w * h)
    queue = deque()

    def push(x: int, y: int) -> None:
        if 0 <= x < w and 0 <= y < h and not seen[y * w + x]:
            seen[y * w + x] = 1
            queue.append((x, y))

    for x in range(w):
        push(x, 0)
        push(x, h - 1)
    for y in range(h):
        push(0, y)
        push(w - 1, y)

    removed = 0
    while queue:
        x, y = queue.popleft()
        i = at(x, y)
        b, g, r = px[i], px[i + 1], px[i + 2]
        if min(r, g, b) < white_cut:      # 不是白底，停下（边缘就留在这儿）
            continue
        px[i + 3] = 0                     # alpha = 0
        removed += 1
        push(x + 1, y)
        push(x - 1, y)
        push(x, y + 1)
        push(x, y - 1)

    # 削白毛边：贴着透明区的「很白」像素再吃掉几圈，深色背景下不会有白边
    for _ in range(trim):
        victims = []
        for y in range(h):
            for x in range(w):
                i = at(x, y)
                if px[i + 3] == 0:
                    continue
                if min(px[i], px[i + 1], px[i + 2]) < 240:
                    continue
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if 0 <= nx < w and 0 <= ny < h and px[at(nx, ny) + 3] == 0:
                        victims.append(i)
                        break
        if not victims:
            break
        for i in victims:
            px[i + 3] = 0
        removed += len(victims)

    print("  抠掉背景像素：%d（%.1f%%）" % (removed, removed * 100.0 / (w * h)))
    return img


def _png_bytes(img: QImage) -> bytes:
    from PySide6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def write_ico(path: str, images) -> None:
    """写多尺寸 ICO（各尺寸用 PNG 压缩，Windows Vista 以上都认）"""
    payloads = [(im.width(), _png_bytes(im)) for im in images]
    header = struct.pack("<HHH", 0, 1, len(payloads))
    offset = 6 + 16 * len(payloads)
    entries, blobs = b"", b""
    for size, data in payloads:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32,
                               len(data), offset)
        blobs += data
        offset += len(data)
    with open(path, "wb") as f:
        f.write(header + entries + blobs)


def main() -> int:
    master_path = os.path.join(HERE, "chapflow-%d.png" % MASTER)
    if not os.path.exists(master_path):
        print("找不到素材：%s" % master_path, file=sys.stderr)
        return 1

    src = QImage(master_path)
    if src.isNull():
        print("素材读取失败：%s" % master_path, file=sys.stderr)
        return 1

    if src.hasAlphaChannel() and min(
            src.pixelColor(0, 0).alpha(), src.pixelColor(src.width() - 1, 0).alpha(),
            src.pixelColor(0, src.height() - 1).alpha()) == 0:
        print("素材已经是透明底，直接缩放")
        master = src.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    else:
        print("处理素材：%s（%dx%d）" % (os.path.basename(master_path),
                                        src.width(), src.height()))
        master = knock_out_background(src)
        master = master.convertToFormat(QImage.Format_ARGB32_Premultiplied)

    for size in SIZES:
        img = master if size == master.width() else master.scaled(
            size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        out = os.path.join(HERE, "chapflow-%d.png" % size)
        img.save(out, "PNG")
        print("  写出 %s（%d）" % (os.path.basename(out), size))

    ico_imgs = [master.scaled(s, s, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                for s in ICO_SIZES]
    write_ico(os.path.join(HERE, "chapflow.ico"), ico_imgs)
    print("  写出 chapflow.ico（%s）" % "/".join(str(s) for s in ICO_SIZES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
