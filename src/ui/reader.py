# -*- coding: utf-8 -*-
"""内置阅读器：用 QTextBrowser 渲染章节，资源直接从 EPUB 包内读取

为什么用 QTextBrowser 而不是 QtWebEngine：
* QTextBrowser 随 QtWidgets 提供，零新增依赖（QtWebEngine 需要 PySide6-Addons，约 +150MB）
* 它是 HTML4 + CSS2 子集：段落、标题、列表、表格、图片、字号、缩进都能正确呈现
* 不执行脚本，打开他人 EPUB 更安全
局限：不支持 JS、flex/grid 等现代 CSS，复杂排版会降级为纯文本流（可接受）。
"""

from __future__ import annotations

import posixpath
import re
from typing import List, Optional

from PySide6.QtCore import QByteArray, QSize, Qt, QUrl
from PySide6.QtGui import (QFont, QFontMetricsF, QImage, QTextCursor,
                           QTextDocument)
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
                               QTextBrowser, QVBoxLayout, QWidget)

from . import icons
from .tokens import COLORS, FONTS

READER_CSS = """
body { margin: 0; padding: 0 6%; line-height: 1.75;
       font-family: "__FAMILY__", serif; font-size: __SIZE__pt; color: #202428; }
h1, h2, h3, h4, h5, h6 { line-height: 1.4; margin: 1.1em 0 0.6em; font-weight: bold; }
h1 { font-size: 1.5em; text-align: center; }
p { margin: 0.55em 0; text-indent: 2em; }
img { max-width: 100%; }
table { border-collapse: collapse; }
td, th { border: 1px solid #D6DCE0; padding: 3px 6px; }
blockquote { margin: 0.8em 2em; color: #737B83; }
a { color: #16834B; }
"""


def _strip_scripts(html: str) -> str:
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S | re.I)
    html = re.sub(r"<script\b[^>]*/>", "", html, flags=re.I)
    return html


def _css_urls_to_epub(css: str, css_path: str) -> str:
    """把 CSS 里的相对 url() 改写成 epub: 绝对路径，便于资源解析"""
    base = posixpath.dirname(css_path)

    def repl(m):
        raw = m.group(2).strip().strip("\"'")
        if raw.startswith(("data:", "http:", "https:", "#", "epub:")):
            return m.group(0)
        target = posixpath.normpath(posixpath.join(base, raw))
        return "url('epub:/%s')" % target

    return re.sub(r"url\(\s*(['\"]?)([^)'\"]+)\1\s*\)", repl, css)


def read_chapter(session, path: Optional[str]) -> Optional[str]:
    """读一章正文（本次新增文件优先），解码成 str；找不到返回 None"""
    if not path:
        return None
    data = None
    if path in session.new_files:
        data = session.new_files[path]
    elif path in session.book.infos:
        try:
            data = session.book.read(path)
        except KeyError:
            data = None
    if data is None:
        return None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def read_asset_text(session, path: str) -> Optional[str]:
    """读包内的文本资源（主要是 CSS）"""
    data = None
    if path in session.new_files:
        data = session.new_files[path]
    elif path in session.book.infos:
        try:
            data = session.book.read(path)
        except KeyError:
            data = None
    if data is None:
        return None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def prepare_html(session, html: str, path: str, font_size: float) -> str:
    """注入阅读样式与书内 CSS，去掉脚本；图片等资源交给 EpubView 解析"""
    html = _strip_scripts(html)
    css_parts = [READER_CSS.replace("__FAMILY__", FONTS["family"])
                 .replace("__SIZE__", str(font_size))]
    base = posixpath.dirname(path)

    for m in re.finditer(r"<link\b[^>]*>", html, flags=re.I):
        tag = m.group(0)
        if "stylesheet" not in tag.lower():
            continue
        href = re.search(r"href\s*=\s*[\"']([^\"']+)[\"']", tag, flags=re.I)
        if not href:
            continue
        target = posixpath.normpath(posixpath.join(base, href.group(1).split("#")[0]))
        css = read_asset_text(session, target)
        if css:
            css = re.sub(r"@font-face\s*\{[^}]*\}", "", css, flags=re.S | re.I)
            css = re.sub(r"@import[^;]+;", "", css, flags=re.I)
            css_parts.append(_css_urls_to_epub(css, target))

    style = "<style type=\"text/css\">%s</style>" % "\n".join(css_parts)
    if re.search(r"</head>", html, flags=re.I):
        html = re.sub(r"</head>", style + "</head>", html, count=1, flags=re.I)
    else:
        html = style + html
    return html


def apply_paragraph_indent(view) -> None:
    """QTextBrowser 对 CSS text-indent 支持不稳定，用块格式兜底（标题与居中行跳过）"""
    doc = view.document()
    indent = QFontMetricsF(view.font()).horizontalAdvance("\u3000\u3000")
    block = doc.begin()
    while block.isValid():
        if block.text().strip():
            bf = block.blockFormat()
            centered = bool(bf.alignment() & Qt.AlignHCenter)
            if not centered and bf.headingLevel() == 0:
                bf.setTextIndent(indent)
                cursor = QTextCursor(block)
                cursor.setBlockFormat(bf)
        block = block.next()


class EpubView(QTextBrowser):
    """从 EPUB（或本次新增文件）按需读取图片等资源"""

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setFrameShape(QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.document().setDocumentMargin(28)

    def loadResource(self, rtype, url):  # noqa: N802
        if rtype == QTextDocument.ImageResource:
            path = self._zip_path(url)
            if not path:
                return None
            data = self._read(path)
            if data is None:
                return None
            img = QImage()
            if img.loadFromData(QByteArray(data)) and not img.isNull():
                return img
            return None
        return super().loadResource(rtype, url)

    @staticmethod
    def _zip_path(url: QUrl) -> str:
        if url.scheme() not in ("epub", ""):
            return ""
        path = url.path().lstrip("/")
        return posixpath.normpath(path) if path else ""

    def _read(self, path: str) -> Optional[bytes]:
        files = self.session.new_files
        if path in files:
            return files[path]
        book = self.session.book
        if path in book.infos:
            try:
                return book.read(path)
            except KeyError:
                return None
        return None


class ReaderWindow(QDialog):
    """章节阅读窗口：上一章 / 下一章 / 字号 / 内部链接跳转"""

    def __init__(self, session, node, parent=None):
        super().__init__(parent)
        self.session = session
        self.node = node
        self.docs = self._collect_docs(session.toc)
        self._font_size = 11

        self.setWindowTitle("阅读 · %s" % (node.title or "章节"))
        self.setWindowIcon(icons.app_icon())
        self.setWindowFlag(Qt.Window, True)
        self.setModal(False)
        self.resize(820, 760)
        self.setMinimumSize(520, 420)

        self._build()
        self.show_node(node)

    # -- 目录顺序 --------------------------------------------------------
    @staticmethod
    def _collect_docs(items) -> List:
        out = []

        def walk(nodes):
            for n in nodes:
                if n.kind != "group" and n.file:
                    out.append(n)
                if n.children:
                    walk(n.children)

        walk(items)
        return out

    # -- 界面 ------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header.setObjectName("Header")
        header.setFixedHeight(64)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(20, 10, 14, 10)
        hl.setSpacing(10)

        col = QVBoxLayout()
        col.setSpacing(2)
        self.title_label = QLabel("—")
        f = QFont(FONTS["family"], 12)
        f.setBold(True)
        self.title_label.setFont(f)
        self.meta_label = QLabel("")
        self.meta_label.setObjectName("Muted")
        col.addWidget(self.title_label)
        col.addWidget(self.meta_label)
        hl.addLayout(col, 1)

        self.pos_label = QLabel("")
        self.pos_label.setObjectName("Badge")
        hl.addWidget(self.pos_label)
        root.addWidget(header)

        bar = QWidget()
        bar.setObjectName("Toolbar")
        bar.setFixedHeight(46)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 6, 12, 6)
        bl.setSpacing(6)
        self.btn_prev = self._btn("上一章", "chevron-left", self.prev)
        self.btn_next = self._btn("下一章", "chevron-right", self.next)
        bl.addWidget(self.btn_prev)
        bl.addWidget(self.btn_next)
        bl.addStretch(1)
        self.btn_smaller = self._btn("A-", None, lambda: self._zoom(-1))
        self.btn_bigger = self._btn("A+", None, lambda: self._zoom(1))
        self.btn_smaller.setToolTip("缩小字号")
        self.btn_bigger.setToolTip("放大字号")
        bl.addWidget(self.btn_smaller)
        bl.addWidget(self.btn_bigger)
        root.addWidget(bar)

        self.view = EpubView(self.session, self)
        self.view.setStyleSheet("QTextBrowser { background: %s; border: none; }" % COLORS["surface"])
        self.view.anchorClicked.connect(self._on_link)
        root.addWidget(self.view, 1)

        self.status = QLabel("")
        self.status.setObjectName("Muted")
        self.status.setContentsMargins(20, 6, 20, 8)
        root.addWidget(self.status)

    def _btn(self, text, icon_name, slot) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("DetailAction")
        if icon_name:
            b.setIcon(icons.icon(icon_name, COLORS["text_secondary"], 15))
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(slot)
        return b

    # -- 渲染 ------------------------------------------------------------
    def show_node(self, node) -> None:
        self.node = node
        raw = self._read_chapter(node.file)
        if raw is None:
            self.view.setHtml("<p>这一章没有可显示的正文文件。</p>")
            self.status.setText("找不到正文文件：%s" % (node.file or ""))
            self._update_nav()
            return

        html = self._prepare(raw, node.file)
        self.view.document().setBaseUrl(QUrl("epub:/%s/" % posixpath.dirname(node.file)))
        self.view.setHtml(html)
        self._apply_paragraph_indent()
        if node.anchor:
            self.view.scrollToAnchor(node.anchor)

        self.title_label.setText(node.title or "未命名章节")
        self.meta_label.setText(node.file + (("#" + node.anchor) if node.anchor else ""))
        self.setWindowTitle("阅读 · %s" % (node.title or "章节"))
        self.status.setText("内容来自原书文件，只读预览；修改标题请在主界面编辑。")
        self._update_nav()

    def _apply_paragraph_indent(self) -> None:
        apply_paragraph_indent(self.view)

    def _read_chapter(self, path: Optional[str]) -> Optional[str]:
        return read_chapter(self.session, path)

    def _prepare(self, html: str, path: str) -> str:
        return prepare_html(self.session, html, path, self._font_size)

    def _read_text(self, path: str) -> Optional[str]:
        return read_asset_text(self.session, path)

    def _on_link(self, url: QUrl) -> None:
        """章内锚点滚动；指向其它章节则跳转过去"""
        if url.scheme() in ("http", "https", "mailto"):
            return
        if not url.path():
            if url.fragment():
                self.view.scrollToAnchor(url.fragment())
            return
        base = posixpath.dirname(self.node.file or "")
        target = posixpath.normpath(posixpath.join(base, url.path()))
        for n in self.docs:
            if n.file == target:
                self.show_node(n)
                if url.fragment():
                    self.view.scrollToAnchor(url.fragment())
                return
        self.status.setText("链接指向的内容不在本书目录中：%s" % url.toString())

    def _index(self) -> int:
        for i, n in enumerate(self.docs):
            if n is self.node or (n.file == self.node.file and n.anchor == self.node.anchor):
                return i
        return -1

    def prev(self) -> None:
        i = self._index()
        if i > 0:
            self.show_node(self.docs[i - 1])

    def next(self) -> None:
        i = self._index()
        if 0 <= i < len(self.docs) - 1:
            self.show_node(self.docs[i + 1])

    def _update_nav(self) -> None:
        i = self._index()
        total = len(self.docs)
        self.pos_label.setText("第 %d / %d 章" % (i + 1, total) if i >= 0 else "%d 章" % total)
        self.btn_prev.setEnabled(i > 0)
        self.btn_next.setEnabled(0 <= i < total - 1)

    def _zoom(self, delta: int) -> None:
        self._font_size = max(8, min(20, self._font_size + delta))
        self.show_node(self.node)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Left:
            self.prev()
        elif event.key() == Qt.Key_Right:
            self.next()
        else:
            super().keyPressEvent(event)


class ChapterPreview(EpubView):
    """主界面里的只读章节预览：渲染当前选中章节的正文，资源仍从 EPUB 读取。

    和阅读器共用同一套解码 / 样式注入 / 缩进兜底逻辑，只是尺寸更小、没有翻页控件。
    """

    FONT_SIZE = 10.5

    def __init__(self, session=None, parent=None):
        super().__init__(session, parent)
        self.view = self
        self.document().setDocumentMargin(16)
        self.setOpenLinks(False)
        self.setFrameShape(QFrame.NoFrame)

    def set_session(self, session) -> None:
        self.session = session

    def show_node(self, node) -> bool:
        """渲染节点正文；没有正文文件时返回 False"""
        path = getattr(node, "file", None)
        if not self.session or not path:
            self.clear()
            return False
        raw = read_chapter(self.session, path)
        if raw is None:
            self.clear()
            return False
        html = prepare_html(self.session, raw, path, self.FONT_SIZE)
        self.document().setBaseUrl(QUrl("epub:/%s/" % posixpath.dirname(path)))
        self.setHtml(html)
        apply_paragraph_indent(self)
        self.verticalScrollBar().setValue(0)
        anchor = getattr(node, "anchor", "") or ""
        if anchor:
            self.scrollToAnchor(anchor)
        return True
