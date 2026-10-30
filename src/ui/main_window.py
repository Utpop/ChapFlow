# -*- coding: utf-8 -*-
"""ChapFlow 主窗口：目录树 + 章节详情 + 书籍信息 + 操作日志 + 状态栏"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from PySide6.QtCore import (QItemSelectionModel, QSettings, QSize, Qt, QThread,
                            Signal)
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QMenu, QMessageBox, QPushButton, QScrollArea,
                               QSplitter, QStatusBar, QTreeView, QVBoxLayout, QWidget)

import chapflow as CF
from . import icons
from .dialogs import (AboutDialog, ImportDialog, MergeDialog, NewBookDialog,
                      NumberingDialog, ReportDialog)
from .reader import ReaderWindow
from .models import ChapterFilterProxy, ChapterTreeModel
from .tokens import COLORS, FONTS, METRICS
from .widgets import (Card, ChapterDelegate, ChapterTree, CoverView,
                      DottedBackground, EmptyState, LogView, StatRow)

APP_NAME = "ChapFlow"


class Worker(QThread):
    done = Signal(object)
    fail = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as exc:  # 统一回主线程提示
            self.fail.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.session: Optional[CF.Session] = None
        self.path: Optional[str] = None
        self._worker: Optional[Worker] = None
        self._last_export: Optional[datetime] = None
        self._last_issues: list = []
        self._syncing = False
        self._readers = []
        self.settings = QSettings("ChapFlow", "ChapFlow")

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self.resize(1240, 780)
        self.setMinimumSize(1000, 640)

        self._build_menu()
        self._build_central()
        self._build_status()
        self._update_actions()
        self._update_empty_state()
        self.centralWidget().setFocusPolicy(Qt.StrongFocus)
        self.centralWidget().setFocus()   # 启动时不给按钮加焦点框

    # ==================================================================
    # 构建
    # ==================================================================
    def _build_menu(self) -> None:
        """构建动作与快捷键；菜单栏本身隐藏，入口收进工具栏"更多"按钮"""
        mb = self.menuBar()

        m_file = mb.addMenu("文件(&F)")
        self.act_open = self._action("打开 EPUB…", "Ctrl+O", self.open_epub, "open")
        self.act_import = self._action("追加章节（TXT / DOCX）…", "Ctrl+I", self.import_source, "import")
        self.act_merge = self._action("合并书籍…", "Ctrl+M", self.merge_books, "merge")
        self.act_save = self._action("保存（覆盖当前文件）", "Ctrl+S", self.save_in_place, "save")
        self.act_export = self._action("导出 EPUB…", "Ctrl+Shift+S", self.export_epub, "export")
        m_file.addAction(self.act_open)
        m_file.addAction(self.act_import)
        m_file.addAction(self.act_merge)
        m_file.addSeparator()
        m_file.addAction(self.act_save)
        m_file.addAction(self.act_export)
        m_file.addSeparator()
        self.act_quit = self._action("退出", "Ctrl+Q", self.close)
        m_file.addAction(self.act_quit)

        m_edit = mb.addMenu("编辑(&E)")
        self.act_undo = self._action("撤销", "Ctrl+Z", self.undo, "undo")
        self.act_redo = self._action("重做", "Ctrl+Y", self.redo, "redo")
        m_edit.addAction(self.act_undo)
        m_edit.addAction(self.act_redo)

        m_toc = mb.addMenu("目录(&C)")
        self.act_new_group = self._action("新建大标题…", "Ctrl+G", self.new_group, "folder-plus")
        self.act_up = self._action("上移", "Alt+Up", lambda: self.move_selected(-1), "arrow-up")
        self.act_down = self._action("下移", "Alt+Down", lambda: self.move_selected(1), "arrow-down")
        self.act_delete = self._action("删除章节…", "Delete", self.delete_selected, "trash")
        for a in (self.act_new_group, self.act_up, self.act_down):
            m_toc.addAction(a)
        m_toc.addSeparator()
        m_toc.addAction(self.act_delete)

        m_tool = mb.addMenu("工具(&T)")
        self.act_validate = self._action("校验并生成报告…", "F5", self.validate_report, "check-circle")
        m_tool.addAction(self.act_validate)

        m_help = mb.addMenu("帮助(&H)")
        self.act_about = self._action("关于 ChapFlow", "", self.show_about)
        m_help.addAction(self.act_about)
        m_help.addAction(self._action("查看操作日志", "", lambda: self.log_list.setFocus()))

        # 菜单栏不再占一行：窗口标题区下方直接是工具栏
        mb.setVisible(False)
        self._menus = (m_file, m_edit, m_toc, m_tool)

    def _action(self, text, shortcut, slot, icon_name="") -> QAction:
        act = QAction(text, self)
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
        if icon_name:
            act.setIcon(icons.icon(icon_name, COLORS["text_secondary"], 16))
        act.triggered.connect(slot)
        self.addAction(act)
        return act

    def _build_central(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        v.addWidget(self._build_header())
        v.addWidget(self._build_toolbar())

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(1)
        self.splitter.addWidget(self._build_sidebar())
        self.splitter.addWidget(self._build_workspace())
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([345, 1155])
        v.addWidget(self.splitter, 1)

        self.setCentralWidget(root)

    def _build_header(self) -> QWidget:
        hdr = QWidget()
        hdr.setObjectName("Header")
        hdr.setFixedHeight(METRICS["header_h"])
        lay = QHBoxLayout(hdr)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(9)

        logo = QLabel()
        logo.setObjectName("Logo")
        pm = icons.app_icon().pixmap(22, 22)
        logo.setPixmap(pm)
        lay.addWidget(logo)

        self.app_label = QLabel(APP_NAME)
        self.app_label.setObjectName("AppName")
        self.app_label.setCursor(Qt.PointingHandCursor)
        self.app_label.setToolTip("关于 ChapFlow")
        self.app_label.mouseReleaseEvent = lambda _e: self.show_about()
        lay.addWidget(self.app_label)
        lay.addSpacing(6)

        self.file_label = QLabel("未打开文件")
        self.file_label.setObjectName("FileName")
        lay.addWidget(self.file_label)
        lay.addStretch(1)

        return hdr

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("Toolbar")
        bar.setFixedHeight(METRICS["toolbar_h"] + 6)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 6, 14, 6)
        lay.setSpacing(6)

        def tbtn(text, slot, icon_name, kind="", tip=""):
            b = QPushButton(text)
            b.setObjectName(kind) if kind else None
            b.setIcon(icons.icon(icon_name, "#FFFFFF" if kind == "Primary" else COLORS["text_primary"], 17))
            b.setIconSize(QSize(17, 17))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            if tip:
                b.setToolTip(tip)
            b.setMinimumHeight(34)
            return b

        self.btn_open = tbtn("打开", self.open_epub, "open", tip="打开 EPUB（Ctrl+O）")
        self.btn_convert = tbtn(
            "格式转换", self.convert_book, "convert",
            tip="把 TXT / DOCX 转换成一本新的 EPUB（不需要先打开书）")
        self.btn_import = tbtn("追加章节", self.import_source, "append",
                               tip="把 TXT / DOCX 作为章节追加到当前书（Ctrl+I）")
        self.btn_merge = tbtn("合并书籍", self.merge_books, "merge",
                              tip="把其它 EPUB 并入当前书（Ctrl+M）")
        self.btn_new_group = tbtn(
            "新建大标题", self.new_group, "folder-plus",
            tip="选中章节后点击：把选中的连续条目打包成大标题\n未选中任何条目时点击：在目录末尾新建一个大标题（Ctrl+G）")
        self.btn_numbering = tbtn(
            "编号", self.check_numbering, "numbering",
            tip="检查章节编号是否连续，可重排或给无编号章节补号")
        self.btn_validate = tbtn("校验", self.validate_report, "check-circle",
                                 tip="检查结构并生成报告（F5）；若目录里有失效条目，报告底部可一键修复")
        # 左侧：内容的来源与加工（打开 → 加入章节 → 成书 → 合并 → 分组）
        for b in (self.btn_open, self.btn_import, self.btn_convert, self.btn_merge,
                  self.btn_new_group, self.btn_numbering):
            lay.addWidget(b)

        lay.addStretch(1)

        # 撤销 / 重做：无文字，做成一个连体按钮
        self.seg = QFrame()
        self.seg.setObjectName("Segmented")
        self.seg.setFixedHeight(34)
        seg_lay = QHBoxLayout(self.seg)
        seg_lay.setContentsMargins(1, 1, 1, 1)   # 让按钮不压住外框圆角
        seg_lay.setSpacing(0)

        self.btn_undo = QPushButton()
        self.btn_undo.setObjectName("SegLeft")
        self.btn_undo.setIcon(icons.dual_icon("undo", COLORS["text_primary"], "#C6CDD3", 17))
        self.btn_undo.setCursor(Qt.PointingHandCursor)
        self.btn_undo.setFixedSize(34, 32)
        self.btn_undo.clicked.connect(self.undo)
        self.btn_undo.setToolTip("撤销上一步（Ctrl+Z）")

        self.btn_redo = QPushButton()
        self.btn_redo.setObjectName("SegRight")
        self.btn_redo.setIcon(icons.dual_icon("redo", COLORS["text_primary"], "#C6CDD3", 17))
        self.btn_redo.setCursor(Qt.PointingHandCursor)
        self.btn_redo.setFixedSize(34, 32)
        self.btn_redo.clicked.connect(self.redo)
        self.btn_redo.setToolTip("重做（Ctrl+Y）")

        seg_lay.addWidget(self.btn_undo)
        seg_lay.addWidget(self.btn_redo)
        lay.addWidget(self.seg)

        # 右侧：撤销/重做 + 收尾（先校验，再保存/导出）
        lay.addWidget(self.btn_validate)

        self.btn_save = tbtn("保存", self.save_in_place, "save",
                             tip="把修改直接存回当前文件（Ctrl+S，原文件会备份为 .bak）")
        lay.addWidget(self.btn_save)
        self.btn_export = tbtn("导出 EPUB", self.export_epub, "export", "Primary",
                               tip="另存为新的 EPUB 并自检（Ctrl+Shift+S）")
        lay.addWidget(self.btn_export)

        return bar

    def _build_sidebar(self) -> QWidget:
        side = QWidget()
        side.setObjectName("Sidebar")
        side.setMinimumWidth(250)
        v = QVBoxLayout(side)
        v.setContentsMargins(12, 12, 8, 10)
        v.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(6)
        title = QLabel("目录")
        title.setObjectName("PanelTitle")
        head.addWidget(title)
        self.nav_meta = QLabel("未打开")
        self.nav_meta.setObjectName("Muted")
        head.addWidget(self.nav_meta)
        head.addStretch(1)
        v.addLayout(head)

        self.search = QLineEdit()
        self.search.setObjectName("SearchInput")
        self.search.setPlaceholderText("搜索章节标题或文件…")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icons.icon("search", COLORS["disabled"], 15), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self._on_search)
        v.addWidget(self.search)

        self.tree = ChapterTree()
        self.tree.setObjectName("ChapterTree")
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        self.tree.setDropIndicatorShown(True)
        self.tree.setDragDropMode(QAbstractItemView.InternalMove)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.setItemDelegate(ChapterDelegate(self.tree))
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.tree.doubleClicked.connect(self._on_tree_double_click)
        v.addWidget(self.tree, 1)

        self.tree_empty = EmptyState("folder", "还没有打开书籍",
                                     "打开一个 EPUB 编辑目录，或点上方「格式转换」把 TXT / DOCX 转成 EPUB")
        v.addWidget(self.tree_empty, 1)

        sep = QFrame()
        sep.setObjectName("HLine")
        sep.setFixedHeight(1)
        v.addWidget(sep)

        hint = QLabel("双击章节阅读 · 拖动排序 · 右键更多操作")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # 模型
        self.model = ChapterTreeModel(self)
        self.proxy = ChapterFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.tree.setModel(self.proxy)
        self.tree.selectionModel().selectionChanged.connect(self._on_selection_changed)

        self._tree_menu_build()
        return side

    def _build_workspace(self) -> QWidget:
        bg = DottedBackground()
        lay = QVBoxLayout(bg)
        lay.setContentsMargins(14, 12, 16, 12)
        lay.setSpacing(12)

        # 章节详情
        self.detail = Card("章节详情")
        self.detail.setMinimumHeight(272)
        self.detail_level = QLabel("—")
        self.detail_level.setObjectName("Badge")
        self.detail.add_header_widget(self.detail_level)

        self.detail_stack = QWidget()
        dv = QVBoxLayout(self.detail_stack)
        dv.setContentsMargins(0, 0, 0, 0)
        dv.setSpacing(6)

        lbl = QLabel("目录里显示的名字")
        lbl.setObjectName("FieldLabel")
        dv.addWidget(lbl)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("选择左侧条目后可编辑标题")
        self.title_edit.editingFinished.connect(self._commit_title)
        dv.addWidget(self.title_edit)

        dv.addSpacing(10)
        lbl2 = QLabel("对应的正文文件")
        lbl2.setObjectName("FieldLabel")
        dv.addWidget(lbl2)
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        dv.addWidget(self.file_edit)

        dv.addSpacing(12)
        self.cb_sync = QCheckBox("正文页的标题也一起改")
        self.cb_sync.setToolTip(
            "勾选后，导出时把新标题写进每一章的正文文件；\n"
            "不勾选则只改目录，点开章节时正文顶部仍是旧标题。")
        self.cb_sync.setChecked(self.settings.value("sync_heading", False, bool))
        self.cb_sync.toggled.connect(
            lambda v: self.settings.setValue("sync_heading", v))
        dv.addWidget(self.cb_sync)
        self.sync_hint = QLabel("不勾选：只改目录，正文顶部仍是旧标题")
        self.sync_hint.setObjectName("Muted")
        self.sync_hint.setContentsMargins(22, 0, 0, 0)
        dv.addWidget(self.sync_hint)

        dv.addStretch(1)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.btn_up = QPushButton("上移")
        self.btn_up.setObjectName("DetailAction")
        self.btn_up.setIcon(icons.icon("arrow-up", COLORS["text_secondary"], 15))
        self.btn_up.clicked.connect(lambda: self.move_selected(-1))
        self.btn_down = QPushButton("下移")
        self.btn_down.setObjectName("DetailAction")
        self.btn_down.setIcon(icons.icon("arrow-down", COLORS["text_secondary"], 15))
        self.btn_down.clicked.connect(lambda: self.move_selected(1))
        btns.addWidget(self.btn_up)
        btns.addWidget(self.btn_down)
        btns.addStretch(1)
        self.btn_delete = QPushButton("删除…")
        self.btn_delete.setObjectName("Danger")
        self.btn_delete.setToolTip("删除章节，可选择是否同时删除正文文件（Delete）")
        self.btn_delete.setIcon(icons.icon("trash", COLORS["danger"], 15))
        self.btn_delete.clicked.connect(self.delete_selected)
        btns.addWidget(self.btn_delete)
        dv.addLayout(btns)

        self.detail.add(self.detail_stack)
        self.detail_empty = EmptyState("doc", "未选择章节", "在左侧目录中选择一个条目查看详情")
        self.detail.add(self.detail_empty)

        # 底部两卡
        bottom = QSplitter(Qt.Horizontal)
        bottom.setChildrenCollapsible(False)
        bottom.setHandleWidth(1)

        info = Card("书籍信息")
        # 内容放进滚动区：窗口变矮时滚动，而不是把封面和统计行压到重叠
        info_scroll = QScrollArea()
        info_scroll.setWidgetResizable(True)
        info_scroll.setFrameShape(QFrame.NoFrame)
        info_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        info_inner = QWidget()
        il = QVBoxLayout(info_inner)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(12)
        self.cover = CoverView()
        top.addWidget(self.cover, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(3)
        self.book_title = QLabel("—")
        f = QFont(FONTS["family"], 11)
        f.setBold(True)
        self.book_title.setFont(f)
        self.book_title.setWordWrap(True)
        self.book_meta = QLabel("—")
        self.book_meta.setObjectName("Muted")
        self.book_meta.setWordWrap(True)
        self.book_tag = QLabel("—")
        self.book_tag.setObjectName("Muted")
        col.addWidget(self.book_title)
        col.addWidget(self.book_meta)
        col.addWidget(self.book_tag)
        col.addStretch(1)
        top.addLayout(col, 1)
        il.addLayout(top)

        line = QFrame()
        line.setObjectName("HLine")
        line.setFixedHeight(1)
        il.addWidget(line)

        self.st_manifest = StatRow("书内文件数")
        self.st_spine = StatRow("正文页数（顺序）")
        self.st_toc = StatRow("目录条目")
        self.st_warn = StatRow("打开告警")
        self.st_export = StatRow("上次导出")
        self.st_source = StatRow("目录来自")
        self.st_file = StatRow("原文件")
        for w in (self.st_manifest, self.st_spine, self.st_toc, self.st_warn,
                  self.st_export, self.st_source, self.st_file):
            il.addWidget(w)
        il.addStretch(1)

        info_scroll.setWidget(info_inner)
        info.add(info_scroll)
        self.info_scroll = info_scroll
        self.info_empty = EmptyState("book", "尚未打开书籍",
                                     "打开 EPUB 后显示封面、元数据与统计")
        self.info_empty.setVisible(False)
        info.add(self.info_empty)
        info.setMinimumHeight(150)
        bottom.addWidget(info)

        log_card = Card("操作日志")
        self.log_meta = QLabel("—")
        self.log_meta.setObjectName("Badge")
        log_card.add_header_widget(self.log_meta)
        self.log_list = LogView()
        log_card.add(self.log_list)
        self.log_empty = QLabel("暂无操作记录")
        self.log_empty.setObjectName("Muted")
        self.log_empty.setAlignment(Qt.AlignCenter)
        log_card.add(self.log_empty)
        bottom.addWidget(log_card)
        bottom.setSizes([430, 520])

        vsplit = QSplitter(Qt.Vertical)
        vsplit.setChildrenCollapsible(False)
        vsplit.setHandleWidth(1)
        vsplit.addWidget(self.detail)
        vsplit.addWidget(bottom)
        vsplit.setSizes([296, 460])
        vsplit.setStretchFactor(0, 0)
        vsplit.setStretchFactor(1, 1)
        lay.addWidget(vsplit, 1)
        return bg

    def _build_status(self) -> None:
        sb = QStatusBar()
        sb.setSizeGripEnabled(True)
        sb.setContentsMargins(10, 0, 10, 0)
        self.status_left = QLabel("就绪")
        self.status_left.setObjectName("Muted")
        sb.addWidget(self.status_left, 1)

        self.status_saved = QLabel("")
        self.status_saved.setObjectName("Saved")
        self.status_encoding = QLabel("UTF-8")
        self.status_encoding.setObjectName("Muted")
        self.status_encoding.setToolTip("EPUB 规定内容文档使用 UTF-8；追加 TXT 时的编码见操作日志")
        self.status_version = QLabel("v%s" % CF.__version__)
        self.status_version.setObjectName("Muted")
        for w in (self.status_saved, self.status_encoding, self.status_version):
            sb.addPermanentWidget(w)
        self.setStatusBar(sb)

    def _tree_menu_build(self) -> None:
        from PySide6.QtWidgets import QMenu
        self.tree_menu = QMenu(self)
        self.act_read = self._action("阅读这一章", "", lambda: self.open_reader(), "book")
        self.tree_menu.addAction(self.act_read)
        self.tree_menu.addSeparator()
        self.tree_menu.addAction(self._action("重命名", "", self._focus_title, "doc"))
        self.tree_menu.addAction(self.act_new_group)
        self.tree_menu.addAction(self._action("检查章节编号…", "", self.check_numbering,
                                              "numbering"))
        self.tree_menu.addSeparator()
        self.tree_menu.addAction(self.act_up)
        self.tree_menu.addAction(self.act_down)
        self.tree_menu.addSeparator()
        self.tree_menu.addAction(self.act_delete)

    # ==================================================================
    # 状态刷新
    # ==================================================================
    def _update_empty_state(self) -> None:
        loaded = self.session is not None
        self.tree.setVisible(loaded)
        self.tree_empty.setVisible(not loaded)
        self.info_scroll.setVisible(loaded)
        self.info_empty.setVisible(not loaded)
        self.log_list.setVisible(loaded)
        self.log_empty.setVisible(not loaded or self.log_list.count() == 0)
        self.detail_stack.setVisible(loaded and self._current_node() is not None)
        self.detail_empty.setVisible(not (loaded and self._current_node() is not None))
        self.search.setEnabled(loaded)
        if not loaded:
            self.nav_meta.setText("未打开")
            self.detail_level.setText("—")
            self.detail_level.setVisible(False)
            self.log_meta.setVisible(False)
            self.title_edit.clear()
            self.file_edit.clear()
            self.book_title.setText("—")
            self.book_meta.setText("—")
            self.book_tag.setText("—")
            self.cover.set_image(None)
            for row in (self.st_manifest, self.st_spine, self.st_toc, self.st_warn,
                        self.st_export, self.st_source, self.st_file):
                row.set_value("—")
            self.file_label.setText("未打开文件")
            self.status_left.setText("就绪 · 打开一个 EPUB 开始")
            self.log_meta.setText("—")
            self._update_actions()

    def _update_actions(self) -> None:
        loaded = self.session is not None
        # 没打开书时，把「打开」标成黄色提醒（唯一的起点）
        self.btn_open.setProperty("attention", "false" if loaded else "true")
        self.btn_open.setIcon(icons.icon(
            "open", COLORS["text_primary"] if loaded else COLORS["warning"], 17))
        self.btn_open.style().unpolish(self.btn_open)
        self.btn_open.style().polish(self.btn_open)
        node = self._current_node() if loaded else None
        for a in (self.act_import, self.act_merge, self.act_export, self.act_validate,
                  self.act_new_group):
            a.setEnabled(loaded)
        for b in (self.btn_import, self.btn_merge, self.btn_validate, self.btn_export,
                  self.btn_new_group, self.btn_save, self.btn_numbering):
            b.setEnabled(loaded)
        self.btn_convert.setEnabled(True)
        can_node = node is not None
        self.act_delete.setEnabled(can_node)
        self.btn_delete.setEnabled(can_node)
        self.act_read.setEnabled(bool(node is not None and node.kind != "group" and node.file))
        self.title_edit.setEnabled(can_node)
        self.file_edit.setEnabled(can_node)
        self.cb_sync.setEnabled(loaded)
        if node is not None:
            pos = self._sibling_position(node)
            total = self._sibling_count(node)
            self.act_up.setEnabled(pos > 1)
            self.act_down.setEnabled(pos < total)
            self.btn_up.setEnabled(pos > 1)
            self.btn_down.setEnabled(pos < total)
        else:
            for w in (self.act_up, self.act_down, self.btn_up, self.btn_down):
                w.setEnabled(False)
        undo_ok = loaded and self.session.can_undo
        redo_ok = loaded and self.session.can_redo
        self.act_undo.setEnabled(undo_ok)
        self.act_redo.setEnabled(redo_ok)
        self.btn_undo.setEnabled(undo_ok)
        self.btn_redo.setEnabled(redo_ok)
        depth = self.session.undo_depth if loaded else 0
        self.btn_undo.setToolTip(
            ("撤销上一步（Ctrl+Z）· 还可撤销 %d 步" % depth) if depth
            else "撤销上一步（Ctrl+Z）")
        self.btn_redo.setToolTip(
            "重做（Ctrl+Y）" if not (loaded and self.session.can_redo)
            else "重做（Ctrl+Y）· 有可恢复的步骤")
        self._update_dirty()

    def _update_dirty(self) -> None:
        dirty = bool(self.session and self.session.dirty)
        self.status_saved.setText("未保存" if dirty else ("已保存" if self.session else ""))
        self.status_saved.setObjectName("Dirty" if dirty else "Saved")
        self.status_saved.style().unpolish(self.status_saved)
        self.status_saved.style().polish(self.status_saved)

    def _refresh_tree(self, select_uid: str = "", expand_all: bool = False) -> None:
        expanded = set(self._expanded_uids())
        self.model.refresh()
        self._apply_expansion(expanded, expand_all)
        if select_uid:
            node = self.model.find_uid(select_uid)
            if node is not None:
                self._select_node(node)
        self._refresh_counts()
        self._update_actions()
        self._update_empty_state()
        self.centralWidget().setFocusPolicy(Qt.StrongFocus)
        self.centralWidget().setFocus()   # 启动时不给按钮加焦点框

    def _expanded_uids(self):
        out = []
        if not self.model.session:
            return out

        def walk(items):
            for n in items:
                idx = self.model.index_of(n)
                if idx.isValid() and self.tree.isExpanded(self.proxy.mapFromSource(idx)):
                    out.append(n.uid)
                if n.children:
                    walk(n.children)

        walk(self.session.toc)
        return out

    def _apply_expansion(self, uids, expand_all: bool) -> None:
        if expand_all:
            self.tree.expandAll()
            return

        def walk(items):
            for n in items:
                idx = self.model.index_of(n)
                if idx.isValid():
                    pi = self.proxy.mapFromSource(idx)
                    self.tree.setExpanded(pi, n.uid in uids or not uids)
                if n.children:
                    walk(n.children)

        walk(self.session.toc)

    def _refresh_counts(self) -> None:
        if not self.session:
            return
        c = CF.count_nodes(self.session.toc)
        self.nav_meta.setText("%s · %d 项" % (self._source_name(), c["total"]))
        self.st_toc.set_value(str(c["total"]))
        self.status_left.setText("%s  ·  %d 个目录项  ·  %s"
                                 % (os.path.basename(self.path or ""), c["total"],
                                    self._validate_summary()))

    def _source_name(self) -> str:
        if not self.session:
            return "—"
        src = self.session.book.toc_source
        return {"nav": "nav.xhtml", "ncx": "toc.ncx", "nav+ncx": "nav.xhtml + toc.ncx",
                "spine": "spine 兜底"}.get(src, src)

    def _validate_summary(self) -> str:
        if not self._last_issues:
            return "尚未校验"
        warn = sum(1 for i in self._last_issues if i["level"] == "warn")
        err = sum(1 for i in self._last_issues if i["level"] == "error")
        return "上次校验：%d 警告 %d 错误" % (warn, err)

    # ==================================================================
    # 选择与详情
    # ==================================================================
    def _current_node(self):
        idx = self.tree.currentIndex()
        if not idx.isValid():
            return None
        src = self.proxy.mapToSource(idx)
        return self.model.node_at(src)

    def _sibling_position(self, node) -> int:
        parent = self.model._parent_node(node)
        items = self.model._items(parent)
        try:
            return items.index(node) + 1
        except ValueError:
            return 0

    def _sibling_count(self, node) -> int:
        parent = self.model._parent_node(node)
        return len(self.model._items(parent))

    def _on_selection_changed(self, *_):
        node = self._current_node()
        if node is None:
            self.detail_level.setVisible(False)
            self._update_empty_state()
            self._update_actions()
            return
        depth = self.model._parents.get(node.uid, (None, 0))[1] + 1
        kind = "大标题" if node.kind == "group" else "章节"
        self.detail_level.setText("第 %d 层 · %s" % (depth, kind))
        self.detail_level.setVisible(True)
        self._syncing = True
        self.title_edit.setText(node.title)
        self.file_edit.setText((node.file or "（只是分组，没有正文文件）")
                               + (("#" + node.anchor) if node.anchor else ""))
        self._syncing = False
        self._readers = []
        self.detail_stack.setVisible(True)
        self.detail_empty.setVisible(False)
        self._update_actions()

    def _on_tree_double_click(self, index):
        node = self.model.node_at(self.proxy.mapToSource(index))
        if node is None:
            return
        if node.children:
            # 大标题：双击展开/折叠
            self.tree.setExpanded(index, not self.tree.isExpanded(index))
            return
        # 章节：双击打开内置阅读器
        self.open_reader(node)

    def open_reader(self, node=None) -> None:
        node = node or self._current_node()
        if node is None or node.kind == "group" or not node.file:
            return
        win = ReaderWindow(self.session, node, self)
        win.setAttribute(Qt.WA_DeleteOnClose, True)
        win.finished.connect(lambda _r, w=win: self._forget_reader(w))
        self._readers.append(win)
        win.show()
        win.raise_()
        win.activateWindow()

    def _forget_reader(self, win) -> None:
        if win in self._readers:
            self._readers.remove(win)

    def _focus_title(self) -> None:
        if not self.session or self._current_node() is None:
            return
        self.title_edit.setFocus()
        self.title_edit.selectAll()

    def _select_node(self, node) -> None:
        idx = self.model.index_of(node)
        if idx.isValid():
            pi = self.proxy.mapFromSource(idx)
            if pi.isValid():
                self.tree.setCurrentIndex(pi)
                self.tree.scrollTo(pi)

    # ==================================================================
    # 编辑动作
    # ==================================================================
    def _commit_title(self) -> None:
        if self._syncing or not self.session:
            return
        node = self._current_node()
        if node is None:
            return
        text = self.title_edit.text().strip()
        if not text or text == node.title:
            self.title_edit.setText(node.title)
            return
        try:
            self.session.rename(self.model.path_of(node), text)
        except Exception as exc:
            QMessageBox.warning(self, "改名失败", str(exc))
            self.title_edit.setText(node.title)
            return
        self._log("重命名", "「%s」→「%s」" % (node.title, text), "ok")
        self._refresh_tree(select_uid=node.uid)

    def move_selected(self, delta: int) -> None:
        node = self._current_node()
        if not node or not self.session:
            return
        pos = self._sibling_position(node)
        target = pos + delta
        if target < 1 or target > self._sibling_count(node):
            return
        # 目标必须表达"同一父级内的第 N 位"，否则单数字会被当成根层级位置
        parent = self.model._parent_node(node)
        dest = str(target) if parent is None else "%s.%d" % (self.model.path_of(parent), target)
        try:
            self.session.move(self.model.path_of(node), dest)
        except Exception as exc:
            QMessageBox.warning(self, "移动失败", str(exc))
            return
        self._log("移动", "「%s」→ 第 %d 位" % (node.title, target), "info")
        self._refresh_tree(select_uid=node.uid)

    # -- 大标题：一个入口，按当前选择决定行为 --------------------------------
    def new_group(self) -> None:
        """选中了条目 → 把它们打包进新大标题；没选中 → 在末尾新建空大标题"""
        if not self.session:
            return
        nodes = self._selected_nodes()
        if nodes:
            self._wrap_selection(nodes)
        else:
            self._create_empty_group()

    def _selected_nodes(self):
        rows = self.tree.selectionModel().selectedRows()
        nodes = [self.model.node_at(self.proxy.mapToSource(i)) for i in rows]
        return [n for n in nodes if n is not None]

    def _ask_group_name(self, title: str, default: str = "新建大标题"):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, title, "大标题名称：", text=default)
        if not ok:
            return None
        return (name or "").strip() or default

    def _create_empty_group(self) -> None:
        name = self._ask_group_name("新建大标题")
        if name is None:
            return
        try:
            self.session.add_group(name, "end")
        except Exception as exc:
            QMessageBox.warning(self, "新建失败", str(exc))
            return
        node = self.session.toc[-1] if self.session.toc else None
        self._log("新建", "大标题「%s」（空组，追加到末尾）" % name, "ok")
        self._refresh_tree(select_uid=node.uid if node else "", expand_all=True)

    def _wrap_selection(self, nodes) -> None:
        # 业务约束：同层、连续
        parent = self.model._parent_node(nodes[0])
        positions = []
        for n in nodes:
            if self.model._parent_node(n) is not parent:
                QMessageBox.information(self, "无法打包",
                                        "选中的条目不在同一层级。请只选择同一层级下的连续条目。")
                return
            positions.append(self._sibling_position(n))
        positions.sort()
        if positions != list(range(positions[0], positions[0] + len(positions))):
            QMessageBox.information(self, "无法打包", "被选中的条目必须是连续的一段。")
            return

        name = self._ask_group_name("打包成大标题")
        if name is None:
            return
        # 位置序号是"同级第几位"，必须拼上父级路径才是完整路径
        prefix = "" if parent is None else self.model.path_of(parent)
        paths = [("%s.%d" % (prefix, p)) if prefix else str(p) for p in positions]
        try:
            self.session.wrap_group(paths, name)
        except Exception as exc:
            QMessageBox.warning(self, "打包失败", str(exc))
            return
        self._log("打包", "%d 个条目归入「%s」" % (len(nodes), name), "ok")
        self._refresh_tree(expand_all=True)

    def delete_selected(self) -> None:
        node = self._current_node()
        if not node or not self.session:
            return
        mode = "auto"
        physical = True
        if node.kind == "group" and node.children:
            box = QMessageBox(self)
            box.setWindowTitle("删除大标题")
            box.setIcon(QMessageBox.Warning)
            box.setText("「%s」下还有 %d 个条目，如何处理？" % (node.title, len(node.children)))
            promote = box.addButton("仅删除大标题（子项上提）", QMessageBox.AcceptRole)
            cascade = box.addButton("连子项一起删除", QMessageBox.DestructiveRole)
            box.addButton("取消", QMessageBox.RejectRole)
            box.setDefaultButton(promote)
            box.exec()
            if box.clickedButton() is promote:
                mode = "promote"
                physical = False
            elif box.clickedButton() is cascade:
                mode = "cascade"
            else:
                return
        else:
            box = QMessageBox(self)
            box.setWindowTitle("删除章节")
            box.setIcon(QMessageBox.Warning)
            box.setText("确定删除「%s」？" % node.title)
            box.setInformativeText("将同时移除目录条目、manifest 与 spine 引用，并删除正文文件 %s。"
                                   % (node.file or "（无）"))
            keep = box.addButton("仅移出目录（保留文件）", QMessageBox.AcceptRole)
            full = box.addButton("同时删除正文文件", QMessageBox.DestructiveRole)
            box.addButton("取消", QMessageBox.RejectRole)
            box.setDefaultButton(keep)
            box.exec()
            if box.clickedButton() is keep:
                physical = False
            elif box.clickedButton() is full:
                physical = True
            else:
                return
        try:
            self.session.delete(self.model.path_of(node), mode=mode, physical=physical)
        except Exception as exc:
            QMessageBox.warning(self, "删除失败", str(exc))
            return
        self._log("删除", "「%s」%s" % (node.title, "（正文文件保留）" if not physical else ""), "warn")
        self._refresh_tree()
        self._update_empty_state()

    def undo(self) -> None:
        if not self.session or not self.session.undo():
            return
        self._log("撤销", "回到上一步", "info")
        self._refresh_tree()

    def redo(self) -> None:
        if not self.session or not self.session.redo():
            return
        self._log("重做", "恢复下一步", "info")
        self._refresh_tree()

    def _on_search(self, text: str) -> None:
        self.proxy.set_keyword(text)
        if text.strip() and self.session and self.proxy.rowCount() == 0:
            self.tree_empty.set_text("没有匹配的章节", "试试更短的关键词，或清空搜索框")
            self.tree_empty.setVisible(True)
            self.tree.setVisible(False)
        else:
            self.tree_empty.setVisible(self.session is None)
            self.tree.setVisible(self.session is not None)
            if self.session is None:
                self.tree_empty.set_text("还没有打开书籍", "打开一个 EPUB 后可在此编辑目录")
        if text.strip() and self.session:
            self.tree.expandAll()

    def _tree_menu(self, pos) -> None:
        if not self.session:
            return
        idx = self.tree.indexAt(pos)
        if idx.isValid():
            if not self.tree.selectionModel().isSelected(idx):
                self.tree.selectionModel().select(
                    idx, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
            self.tree.setCurrentIndex(idx)
        self.tree_menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ==================================================================
    # 文件操作（线程）
    # ==================================================================
    def _busy(self, text: str) -> None:
        self.status_left.setText(text)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        for w in (self.btn_open, self.btn_import, self.btn_merge, self.btn_validate, self.btn_export):
            w.setEnabled(False)

    def _idle(self) -> None:
        QApplication.restoreOverrideCursor()
        self._update_actions()

    def _run(self, fn, on_done, busy: str) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.status_left.setText("上一个任务还在进行，请稍候")
            return
        self._busy(busy)
        w = Worker(fn, self)
        self._worker = w

        def ok(result):
            self._idle()
            on_done(result)

        def bad(msg):
            self._idle()
            QMessageBox.critical(self, "操作失败", msg)
            self.status_left.setText("操作失败：%s" % msg)
            self._log("错误", msg, "error")

        w.done.connect(ok)
        w.fail.connect(bad)
        w.start()

    def open_epub(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "打开 EPUB", "", "EPUB (*.epub)")
        if path:
            self.load(path)

    def load(self, path: str) -> None:
        if self.session and self.session.dirty:
            r = QMessageBox.question(self, "未保存的更改",
                                     "当前书有未导出的修改，仍要打开另一本吗？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return

        def job():
            return CF.Session(path)

        def done(session):
            self.session = session
            self.path = path
            self._last_issues = []
            self._last_export = None
            self.model.set_session(session)
            self.tree.setCurrentIndex(self.proxy.index(0, 0))
            self._apply_expansion(set(), expand_all=True)
            self.file_label.setText(os.path.basename(path))
            self._fill_book_info()
            self._refresh_counts()
            self._update_empty_state()
            self._update_actions()
            self.log_list.clear_log()
            self.log_empty.setVisible(True)
            self.tree.setFocus()          # 打开后把焦点放到目录树，避免工具栏出现焦点框
            self._log("打开", "%s（EPUB %s，目录来自 %s）"
                      % (os.path.basename(path), session.book.version, self._source_name()), "info")
            self.status_left.setText("%s  ·  %d 个目录项  ·  尚未校验"
                                     % (os.path.basename(path),
                                        CF.count_nodes(session.toc)["total"]))
            for wmsg in session.book.warnings:
                self._log("告警", wmsg["text"], "warn")

        self._run(job, done, "正在解析 EPUB…")

    def _fill_book_info(self) -> None:
        b = self.session.book
        info = b.info()
        self.cover.set_image(b.cover()[0] if b.cover() else None)
        self.book_title.setText(info["title"] or "（无书名）")
        author = "、".join(info["creators"]) or "未知作者"
        ident = info["identifier"] or ""
        isbn = ident if _looks_isbn(ident) else ""
        parts = [author, info["language"] or "—"]
        if isbn:
            parts.append("ISBN " + isbn)
        self.book_meta.setText(" · ".join(parts))
        self.book_tag.setText("EPUB %s · %s" % (info["version"], _size_text(self.path)))
        self.st_manifest.set_value(str(info["manifest"]))
        self.st_spine.set_value(str(info["spine"]))
        self.st_toc.set_value(str(CF.count_nodes(self.session.toc)["total"]))
        warn = len(info["warnings"])
        self.st_warn.set_value("%d 条%s" % (warn, "（%s）" % info["warnings"][0]["text"][:14]
                                             if warn else ""), warn=warn > 0)
        self.st_export.set_value("—")
        self.st_source.set_value(self._source_name())
        self.st_file.set_value("未改动（原子写盘）" if not self.session.dirty else "有未保存改动",
                               warn=self.session.dirty)

    def check_numbering(self, scope_path: str = "") -> None:
        """检查章节编号：选中大标题时默认只看该组，否则看整本书"""
        if not self.session:
            return
        node = self._current_node()
        if not scope_path and node is not None and node.kind == "group":
            scope_path = self.model.path_of(node)
        dlg = NumberingDialog(self.session, scope_path, self)
        dlg.exec()
        if dlg.applied:
            where = "整本书" if not scope_path else "指定范围"
            self._log("编号", "%s重排/补号 %d 章" % (where, dlg.applied), "ok")
            self._refresh_tree(expand_all=True)

    def convert_book(self) -> None:
        """把 TXT / DOCX 转换成一本新的 EPUB，并在编辑器里打开它"""
        dlg = NewBookDialog(self)
        if not dlg.exec() or not dlg.created_path:
            return
        rep = dlg.report or {}
        self._log("转换", "%s（%d 章）" % (os.path.basename(dlg.created_path),
                                          rep.get("chapters", 0)), "ok")
        self.load(dlg.created_path)
        if rep.get("issues") and any(i["level"] == "error" for i in rep["issues"]):
            ReportDialog(self.session, {"export": rep, "path": dlg.created_path},
                         parent=self).exec()

    def import_source(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "追加章节", "", "文本或文档 (*.txt *.docx);;TXT (*.txt);;DOCX (*.docx)")
        if not path:
            return
        kind = "docx" if path.lower().endswith(".docx") else "txt"
        dlg = ImportDialog(self.session, path, kind, self)
        if dlg.exec() and dlg.result_info:
            info = dlg.result_info
            self._log("追加", "%s「%s」→ %d 章"
                      % (kind.upper(), os.path.basename(path), info.get("chapters", 0)), "ok")
            self._refresh_tree(expand_all=True)
            self._fill_book_info()

    def merge_books(self) -> None:
        dlg = MergeDialog(self.session, self)
        if not dlg.exec() or not dlg.result:
            return
        cfg = dlg.result

        def job():
            info = self.session.merge_epubs(cfg["books"], group_each=cfg["group_each"])
            return info

        def done(info):
            self._log("合并", "并入 %d 本，新增 %d 章" % (len(info["books"]), info["chapters"]), "ok")
            for w in info["warnings"]:
                self._log("告警", w, "warn")
            self._refresh_tree(expand_all=True)
            self._fill_book_info()

        self._run(job, done, "正在合并书籍…")

    def validate_report(self) -> None:
        if not self.session:
            return
        opts = self._export_options()
        # 只把"目录层可修"的问题交给修复按钮；清单类问题导出时会自动清理
        plan = [p for p in self.session.repair_plan() if p["key"] in ("dead", "empty")]

        def job():
            rep = self.session.export(os.devnull, dry_run=True, **opts)
            return rep

        def done(rep):
            self._last_issues = rep["issues"]
            err = sum(1 for i in rep["issues"] if i["level"] == "error")
            warn = sum(1 for i in rep["issues"] if i["level"] == "warn")
            self._log("校验", "自检：%d 错误 %d 警告（dry-run）" % (err, warn),
                      "error" if err else ("warn" if warn else "ok"))
            self._refresh_counts()
            dlg = ReportDialog(self.session, {"export": rep}, parent=self,
                               repair_plan=plan)
            dlg.exec()
            if dlg.repaired:
                self._apply_repair()
                self.validate_report()

        self._run(job, done, "正在自检…")

    def _apply_repair(self) -> None:
        logs = self.session.repair()
        if not logs:
            self._log("修复", "没有需要修复的项目", "info")
            return
        for line in logs:
            self._log("修复", line, "ok")
        self._refresh_tree(expand_all=True)
        self._fill_book_info()

    def export_epub(self) -> None:
        if not self.session:
            return
        from PySide6.QtWidgets import QFileDialog
        base = os.path.splitext(os.path.basename(self.path or "book.epub"))[0]
        default = os.path.join(os.path.dirname(self.path or ""), base + ".toc-edited.epub")
        path, _ = QFileDialog.getSaveFileName(self, "导出 EPUB", default, "EPUB (*.epub)")
        if not path:
            return
        opts = self._export_options()

        def job():
            rep = self.session.export(path, **opts)
            return rep

        def done(rep):
            err = sum(1 for i in rep["issues"] if i["level"] == "error")
            self._last_issues = rep["issues"]
            if not err:
                self.session.dirty = False
                self._last_export = datetime.now()
                self.st_export.set_value(self._last_export.strftime("%Y-%m-%d %H:%M"))
                self.st_file.set_value("已导出（原文件未改）")
                self._log("导出", "%s（%.1f KB，%d 项检查通过）"
                          % (os.path.basename(path), rep.get("bytes", 0) / 1024,
                             sum(1 for i in rep["issues"] if i["level"] == "ok")), "ok")
            else:
                self._log("导出", "失败：%d 个错误，已保留未保存状态" % err, "error")
            self._update_dirty()
            self._update_actions()
            ReportDialog(self.session, {"export": rep, "path": path}, parent=self).exec()

        self._run(job, done, "正在导出…")

    def save_in_place(self) -> None:
        """把修改写回当前文件（首次保存会留一份 .bak 备份）"""
        if not self.session or not self.path:
            return
        path = self.path
        bak = path + ".bak"
        r = QMessageBox.question(
            self, "保存到原文件",
            "将把修改直接写入当前文件：\n%s\n\n原文件会先备份为：\n%s\n\n继续吗？"
            % (os.path.basename(path), os.path.basename(bak)),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        opts = self._export_options()

        def job():
            import shutil
            if os.path.exists(path) and not os.path.exists(bak):
                shutil.copy2(path, bak)
            return self.session.export(path, **opts)

        def done(rep):
            err = sum(1 for i in rep["issues"] if i["level"] == "error")
            self._last_issues = rep["issues"]
            if not err:
                self.session.dirty = False
                self._last_export = datetime.now()
                self.st_export.set_value(self._last_export.strftime("%Y-%m-%d %H:%M"))
                self.st_file.set_value("已保存（备份在 .bak）")
                self._log("保存", "%s（已备份为 .bak）" % os.path.basename(path), "ok")
            else:
                self._log("保存", "失败：%d 个错误，文件未改动" % err, "error")
            self._update_dirty()
            self._update_actions()
            if err:
                ReportDialog(self.session, {"export": rep, "path": path},
                             parent=self).exec()

        self._run(job, done, "正在保存…")

    def _export_options(self) -> dict:
        return {"sync_spine": True,
                "sync_heading": self.cb_sync.isChecked(),
                "touch_modified": True}

    # ==================================================================
    # 杂项
    # ==================================================================
    def _log(self, action: str, detail: str, level: str = "info") -> None:
        self.log_list.add_entry(datetime.now().strftime("%H:%M"), action, detail, level)
        depth = self.session.undo_depth if self.session else 0
        self.log_meta.setText(("可撤销 %d 步" % depth) if depth else "暂无可撤销操作")
        self.log_meta.setVisible(True)
        self.log_list.setVisible(True)
        self.log_empty.setVisible(False)

    def show_about(self) -> None:
        AboutDialog(self).exec()

    def closeEvent(self, event):
        if self.session and self.session.dirty:
            r = QMessageBox.question(self, "未保存的更改",
                                     "有未导出的修改，确定退出吗？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                event.ignore()
                return
        event.accept()


def _size_text(path: Optional[str]) -> str:
    if not path:
        return "—"
    try:
        n = os.path.getsize(path)
    except OSError:
        return "—"
    if n >= 1024 * 1024:
        return "%.1f MB" % (n / 1024 / 1024)
    return "%.0f KB" % (n / 1024)


def _looks_isbn(text: str) -> bool:
    digits = "".join(ch for ch in text if ch.isdigit() or ch in "Xx")
    return len(digits) in (10, 13)
