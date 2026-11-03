# -*- coding: utf-8 -*-
"""ChapFlow 主窗口：目录树 + 章节详情 + 书籍信息 + 操作日志 + 状态栏"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import List, Optional

from PySide6.QtCore import (QItemSelection, QItemSelectionModel, QObject, QSettings,
                            QSize, Qt, QThreadPool, QTimer, QRunnable,
                            Signal)
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QMenu, QMessageBox, QPushButton, QSplitter,
                               QStatusBar, QTreeView, QVBoxLayout, QWidget)

import chapflow as CF
from . import icons
from .dialogs import (AboutDialog, AddChapterDialog, BookInfoDialog, ImportDialog,
                      MergeDialog, NewBookDialog, NumberingDialog, ReportDialog)
from .reader import ChapterPreview, ReaderWindow
from .models import ChapterFilterProxy, ChapterTreeModel
from .tokens import COLORS, METRICS
from .widgets import (Card, ChapterDelegate, ChapterTree,
                      DottedBackground, EmptyState, LogView)

APP_NAME = "ChapFlow"


class WorkerSignals(QObject):
    """工作线程回主线程的三条信号（QObject 才能挂信号，QRunnable 不行）"""
    done = Signal(object)
    fail = Signal(str)
    finished = Signal()


class Worker(QRunnable):
    """一次性的后台任务

    以前每次操作用一个 QThread：建线程 + 销毁 + 跨线程信号要 1~3 ms，连点还会
    抖动。改用 QThreadPool（Qt 自带、线程复用），任务对象里的信号仍回到主线程。
    """

    def __init__(self, fn):
        super().__init__()
        self._fn = fn
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    def run(self):  # noqa: D102 - 工作线程入口
        try:
            self.signals.done.emit(self._fn())
        except Exception as exc:  # 统一回主线程提示
            self.signals.fail.emit(str(exc))
        finally:
            self.signals.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.session: Optional[CF.Session] = None
        self.path: Optional[str] = None
        self._worker: Optional[Worker] = None
        self._last_export: Optional[datetime] = None
        self._export_note = "—"
        self._file_note = "未改动（原子写入）"
        self._io_busy = False          # 正在读盘 / 导出：只有此时才真正禁用按钮
        self._last_issues: list = []
        self._syncing = False
        self._readers = []
        self._preview_pending = None          # 待渲染的章节（推迟到下一轮事件循环）
        self._preview_queued = False
        self._suppress_actions = 0            # 打开书籍期间先攒着，末尾统一刷一次
        self._actions_dirty = False
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
        self.act_book_info = self._action("书籍信息…", "", self.show_book_info, "book")
        self.act_save = self._action("保存（覆盖当前文件）", "Ctrl+S", self.save_in_place, "save")
        self.act_export = self._action("导出 EPUB…", "Ctrl+Shift+S", self.export_epub, "export")
        m_file.addAction(self.act_open)
        m_file.addAction(self.act_import)
        m_file.addAction(self.act_merge)
        m_file.addAction(self.act_book_info)
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
        self.act_add_chapter = self._action("新建章节（直接输入正文）…", "Ctrl+N",
                                            self.add_chapter, "plus")
        self.act_expand = self._action("展开全部目录", "", self.expand_tree, "expand")
        self.act_collapse = self._action("收起全部目录", "", self.collapse_tree, "collapse")
        self.act_promote = self._action("升为一级标题", "", self.promote_selected, "arrow-up")
        self.act_move_into = self._action("移入大标题…", "", self.move_into_group, "folder")
        self.act_new_group = self._action("新建大标题…", "Ctrl+G", self.new_group, "folder-plus")
        self.act_up = self._action("上移", "Alt+Up", lambda: self.move_selected(-1), "arrow-up")
        self.act_down = self._action("下移", "Alt+Down", lambda: self.move_selected(1), "arrow-down")
        self.act_delete = self._action("删除章节…", "Delete", self.delete_selected, "trash")
        for a in (self.act_add_chapter, self.act_new_group, self.act_promote,
                  self.act_move_into, self.act_expand, self.act_collapse,
                  self.act_up, self.act_down):
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
            tip="把 TXT / DOCX 转换成一本新的 EPUB（无需先打开书籍）")
        self.btn_merge = tbtn("合并书籍", self.merge_books, "merge",
                              tip="把其他 EPUB 并入当前书（Ctrl+M）")
        self.btn_add_chapter = tbtn(
            "新建章节", self.add_chapter, "plus",
            tip="直接输入正文新建章节并写入目录，无需 TXT / DOCX 文件（Ctrl+N）")
        self.btn_import = tbtn("追加章节", self.import_source, "append",
                               tip="从 TXT / DOCX 追加章节到当前书籍（Ctrl+I）")
        self.btn_new_group = tbtn(
            "新建大标题", self.new_group, "folder-plus",
            tip="选中章节后点击：将所选连续目录项合并为大标题\n未选中任何目录项时点击：在目录末尾新建大标题（Ctrl+G）")
        self.btn_numbering = tbtn(
            "编号", self.check_numbering, "numbering",
            tip="检查章节编号是否连续，可重排或给无编号章节补号")
        self.btn_validate = tbtn("校验", self.validate_report, "check-circle",
                                 tip="校验结构并生成报告（F5）；若目录中存在失效条目，报告底部可一键修复")
        self.btn_book_info = tbtn("书籍信息", self.show_book_info, "book",
                                 tip="查看封面、元数据与结构统计")

        def tsep():
            """分组竖线：打开 → 成书 → 加正文 → 理目录，四段分开，扫一眼就知道从哪开始"""
            f = QFrame()
            f.setObjectName("VLine")
            f.setFixedSize(1, 22)
            return f

        # 四段：打开一本书 │ 生成/合成书 │ 往书里加正文 │ 调整目录结构
        lay.addWidget(self.btn_open)
        lay.addWidget(tsep())
        for w in (self.btn_convert, self.btn_merge):
            lay.addWidget(w)
        lay.addWidget(tsep())
        for w in (self.btn_add_chapter, self.btn_import):
            lay.addWidget(w)
        lay.addWidget(tsep())
        for w in (self.btn_new_group, self.btn_numbering):
            lay.addWidget(w)

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

        # 右侧：撤销/重做 + 收尾（先看书籍信息，再校验、保存/导出）
        lay.addWidget(self.btn_book_info)
        lay.addWidget(self.btn_validate)

        self.btn_save = tbtn("保存", self.save_in_place, "save",
                             tip="把修改直接存回当前文件（Ctrl+S，原文件会备份为 .bak）")
        lay.addWidget(self.btn_save)
        self.btn_export = tbtn("导出 EPUB", self.export_epub, "export", "Primary",
                               tip="另存为新的 EPUB 并校验（Ctrl+Shift+S）")
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
        def tree_btn(text: str, tip: str, slot) -> QPushButton:
            # 图标版的 expand / collapse 只差镜像，小尺寸下分不清，改用短文字更直白
            b = QPushButton(text)
            b.setObjectName("MiniButton")
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            return b

        self.btn_expand = tree_btn("展开", "展开全部目录项，显示所有层级", self.expand_tree)
        self.btn_collapse = tree_btn("收起", "折叠全部目录项，仅显示一级标题",
                                     self.collapse_tree)
        head.addSpacing(4)
        head.addWidget(self.btn_expand)
        head.addWidget(self.btn_collapse)
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

        self.tree_empty = EmptyState("folder", "尚未打开书籍",
                                     "打开 EPUB 以编辑目录，或点击上方「格式转换」将 TXT / DOCX 转换为 EPUB")
        v.addWidget(self.tree_empty, 1)

        sep = QFrame()
        sep.setObjectName("HLine")
        sep.setFixedHeight(1)
        v.addWidget(sep)

        hint = QLabel("双击阅读章节 · 拖动调整顺序 · 右键查看更多操作")
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

        lbl = QLabel("目录中显示的标题")
        lbl.setObjectName("FieldLabel")
        dv.addWidget(lbl)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("选择左侧目录项后可编辑标题")
        self.title_edit.editingFinished.connect(self._commit_title)
        dv.addWidget(self.title_edit)

        dv.addSpacing(10)
        lbl2 = QLabel("对应的正文文件")
        lbl2.setObjectName("FieldLabel")
        dv.addWidget(lbl2)
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        dv.addWidget(self.file_edit)

        # 所属一级标题：只有下级条目才有，选中时实时刷新；旁边就是「升为一级」
        self.parent_row = QWidget()
        pr = QHBoxLayout(self.parent_row)
        pr.setContentsMargins(0, 6, 0, 0)
        pr.setSpacing(8)
        self.parent_label = QLabel("—")
        self.parent_label.setObjectName("Muted")
        self.parent_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.btn_promote = QPushButton("升为一级")
        self.btn_promote.setObjectName("DetailAction")
        self.btn_promote.setIcon(icons.icon("arrow-up", COLORS["text_secondary"], 15))
        self.btn_promote.setCursor(Qt.PointingHandCursor)
        self.btn_promote.setToolTip(
            "将所选目录项移至其所属一级标题之后，成为一级条目；\n"
            "其子级与正文链接一并保留")
        self.btn_promote.clicked.connect(self.promote_selected)
        pr.addWidget(self.parent_label, 1)
        pr.addWidget(self.btn_promote)
        dv.addWidget(self.parent_row)

        dv.addSpacing(12)
        self.cb_sync = QCheckBox("正文页的标题也一起改")
        self.cb_sync.setToolTip(
            "勾选后，导出时把新标题写进每一章的正文文件；\n"
            "不勾选则只改目录，打开章节时正文顶部仍是旧标题。")
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
        self.detail_empty = EmptyState("doc", "未选择章节", "在左侧目录中选择目录项以查看详情")
        self.detail.add(self.detail_empty)

        # 操作日志：书籍信息已收进弹窗，这里独占整条底栏，纵向空间都给正文预览与日志
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
        log_card.setMinimumHeight(140)

        vsplit = QSplitter(Qt.Vertical)
        vsplit.setChildrenCollapsible(False)
        vsplit.setHandleWidth(1)
        vsplit.addWidget(self.detail)
        vsplit.addWidget(log_card)
        vsplit.setSizes([420, 260])
        vsplit.setStretchFactor(0, 1)
        vsplit.setStretchFactor(1, 0)

        # 左侧「预览」列：选中章节后在这里直接读正文，其余组件整体右移
        preview = Card("预览")
        preview.setMinimumWidth(300)
        self.preview_words = QLabel("")
        self.preview_words.setObjectName("Badge")
        self.preview_words.setVisible(False)      # 有正文时才显示字数气泡
        preview.add_header_widget(self.preview_words)
        self.btn_preview_open = QPushButton("阅读")
        self.btn_preview_open.setObjectName("Ghost")
        self.btn_preview_open.setIcon(icons.icon("book", COLORS["text_secondary"], 14))
        self.btn_preview_open.setCursor(Qt.PointingHandCursor)
        self.btn_preview_open.setToolTip("在内置阅读器里打开当前章节（支持上一章 / 下一章）")
        self.btn_preview_open.clicked.connect(lambda: self.open_reader())
        preview.add_header_widget(self.btn_preview_open)

        self.preview_view = ChapterPreview(None)
        preview.add(self.preview_view)
        self.preview_empty = EmptyState("doc", "未选择章节",
                                        "点击左侧目录中的章节，此处显示正文")
        preview.add(self.preview_empty)

        hsplit = QSplitter(Qt.Horizontal)
        hsplit.setChildrenCollapsible(False)
        hsplit.setHandleWidth(1)
        hsplit.addWidget(preview)
        hsplit.addWidget(vsplit)
        hsplit.setSizes([460, 800])
        hsplit.setStretchFactor(0, 0)
        hsplit.setStretchFactor(1, 1)
        self.workspace_split = hsplit
        self.preview_card = preview
        lay.addWidget(hsplit, 1)
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
        self.tree_menu.addAction(self.act_add_chapter)
        self.tree_menu.addAction(self.act_new_group)
        self.tree_menu.addAction(self.act_move_into)
        self.tree_menu.addAction(self.act_promote)
        self.tree_menu.addAction(self._action("检查章节编号…", "", self.check_numbering,
                                              "numbering"))
        self.tree_menu.addAction(self.act_expand)
        self.tree_menu.addAction(self.act_collapse)
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
        self.log_list.setVisible(loaded)
        self.log_empty.setVisible(not loaded or self.log_list.count() == 0)
        self.detail_stack.setVisible(loaded and self._current_node() is not None)
        self.detail_empty.setVisible(not (loaded and self._current_node() is not None))
        has_text = getattr(self, "_preview_ready", False) and loaded \
            and self._current_node() is not None
        self.preview_view.setVisible(has_text)
        self.preview_empty.setVisible(not has_text)
        self.preview_words.setVisible(has_text)      # 没内容就不留一个空气泡
        self.btn_preview_open.setEnabled(has_text)
        self.search.setEnabled(loaded)
        if not loaded:
            self.nav_meta.setText("未打开")
            self.detail_level.setText("—")
            self.detail_level.setVisible(False)
            self.log_meta.setVisible(False)
            self.title_edit.clear()
            self.file_edit.clear()
            self.preview_view.clear()
            self._preview_ready = False
            self.preview_words.setText("")
            self.file_label.setText("未打开文件")
            self.status_left.setText("就绪 · 打开一个 EPUB 开始")
            self.log_meta.setText("—")
            self._update_actions()

    def _update_open_button(self, loaded: bool = None) -> None:
        """「打开」始终保持可用：打开书籍后仅在视觉上退为次要样式。

        状态（enabled）与样式（attention / secondary）彻底分开：
        只有正在读盘（_busy）时才真正禁用，读完后必定恢复；
        已经有书时按钮变灰、底色透明，但依然能再打开一本新书。
        """
        if loaded is None:
            loaded = self.session is not None
        self.btn_open.setEnabled(not self._io_busy)   # 只有读盘/导出期间才临时禁用
        self.btn_open.setProperty("attention", "false" if loaded else "true")
        self.btn_open.setProperty("secondary", "true" if loaded else "false")
        self.btn_open.setToolTip(
            "打开 EPUB（Ctrl+O）" if not loaded
            else "打开其他书籍（Ctrl+O）；当前书籍如有未保存修改，将先提示保存")
        self.btn_open.setIcon(icons.icon(
            "open", COLORS["warning"] if not loaded else COLORS["text_secondary"], 17))
        self.btn_open.style().unpolish(self.btn_open)
        self.btn_open.style().polish(self.btn_open)

    def _batched(self, fn):
        """把一组界面刷新攒到最后统一做

        打开一本书会连着触发三次 _update_actions（选中变化一次、空状态一次、
        done 末尾一次），300 章时每次 3~5 ms。这里只在最外层退出时刷一次。
        """
        def wrapper(*args, **kwargs):
            self._suppress_actions += 1
            try:
                return fn(*args, **kwargs)
            finally:
                self._suppress_actions -= 1
                if not self._suppress_actions and self._actions_dirty:
                    self._update_actions()
        return wrapper

    def _update_actions(self) -> None:
        if self._suppress_actions:          # 攒着，等 _batched 的出口统一刷
            self._actions_dirty = True
            return
        self._actions_dirty = False
        loaded = self.session is not None
        self._update_open_button(loaded)
        node = self._current_node() if loaded else None
        for a in (self.act_add_chapter, self.act_import, self.act_merge, self.act_export,
                  self.act_validate, self.act_new_group, self.act_move_into,
                  self.act_book_info, self.act_expand, self.act_collapse):
            a.setEnabled(loaded)
        for b in (self.btn_add_chapter, self.btn_import, self.btn_merge, self.btn_validate,
                  self.btn_export, self.btn_new_group, self.btn_save, self.btn_numbering,
                  self.btn_book_info, self.btn_expand, self.btn_collapse):
            b.setEnabled(loaded)
        self.btn_convert.setEnabled(True)
        can_node = node is not None
        self._update_parent_info(node)
        self.act_move_into.setEnabled(bool(loaded and self._selected_nodes()))
        self.act_delete.setEnabled(can_node)
        self.btn_delete.setEnabled(can_node)
        self.act_read.setEnabled(bool(node is not None and node.kind != "group" and node.file))
        self.title_edit.setEnabled(can_node)
        self.file_edit.setEnabled(can_node)
        self.cb_sync.setEnabled(loaded)
        if node is not None:
            pos = self._sibling_position(node)
            total = self._sibling_count(node)
            up_ok, down_ok = pos > 1, pos < total
            selected = self._selected_nodes()
            if len(selected) > 1:
                # 多选时按整块的上下边界判断（整块在顶就不能再上移）
                parent = self.model._parent_node(selected[0])
                if all(self.model._parent_node(n) is parent for n in selected):
                    items = self.model._items(parent)
                    idxs = [items.index(n) + 1 for n in selected if n in items]
                    if idxs:
                        up_ok, down_ok = min(idxs) > 1, max(idxs) < len(items)
            self.act_up.setEnabled(up_ok)
            self.act_down.setEnabled(down_ok)
            self.btn_up.setEnabled(up_ok)
            self.btn_down.setEnabled(down_ok)
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
        if dirty:
            text = "未保存"
        elif not self.session:
            text = ""
        elif getattr(self, "_exported_copy", False):
            # 导出到别的文件 ≠ 保存到原文件：别把「已导出」说成「已保存」
            text = "已导出"
        else:
            text = "已保存"
        self.status_saved.setText(text)
        self.status_saved.setObjectName("Dirty" if dirty else "Saved")
        self.status_saved.style().unpolish(self.status_saved)
        self.status_saved.style().polish(self.status_saved)

    EXPAND_ALL_LIMIT = 80          # 目录项不超过这个数才全展开（够快，也看得全）

    def _refresh_tree(self, select_uid: str = "", expand_all: bool = False,
                      select_uids=None) -> None:
        expanded = set(self._expanded_uids())
        self.model.refresh()
        self._apply_expansion(expanded, expand_all)
        if select_uids:
            self._select_nodes(select_uids)     # 批量操作后保持整段选中，方便接着挪
        elif select_uid:
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
        """按 uid 恢复展开状态；expand_all=True 才全展开

        注意 uids 为空时「什么都不展开」——原来写成 `uid in uids or not uids`，
        用户手动折叠全部之后随便改个名字，整本书又被全展开了。
        """
        if expand_all:
            self.tree.expandAll()
            return

        def walk(items):
            for n in items:
                idx = self.model.index_of(n)
                if idx.isValid():
                    pi = self.proxy.mapFromSource(idx)
                    self.tree.setExpanded(pi, n.uid in uids)
                if n.children:
                    walk(n.children)

        walk(self.session.toc)

    # -- 展开状态跨次保留（B1） -------------------------------------------
    def _expansion_key(self) -> str:
        """按书籍路径存展开状态：Node.uid 每次打开都重新编号，只能拿路径当键"""
        digest = hashlib.md5(os.path.abspath(self.path or "").encode("utf-8")).hexdigest()
        return "view/expanded/%s" % digest[:16]

    def _expanded_paths(self) -> List[str]:
        """当前展开的大标题路径（形如 "1.3"），用于下次打开时恢复"""
        out: List[str] = []
        if not self.session:
            return out

        def walk(items, prefix=""):
            for i, n in enumerate(items, 1):
                path = ("%s.%d" % (prefix, i)) if prefix else str(i)
                if n.children:
                    idx = self.model.index_of(n)
                    if idx.isValid() and self.tree.isExpanded(self.proxy.mapFromSource(idx)):
                        if len(out) < 500:          # 全展开的巨书不必把几千条都存进配置
                            out.append(path)
                    walk(n.children, path)

        walk(self.session.toc)
        return out

    def _save_expansion(self) -> None:
        if not self.session or not self.path:
            return
        try:
            self.settings.setValue(self._expansion_key(),
                                   json.dumps({"paths": self._expanded_paths()}))
        except Exception:                       # 配置写不进去不影响主流程
            pass

    def _apply_expansion_paths(self, paths) -> None:
        want = set(paths or ())

        def walk(items, prefix=""):
            for i, n in enumerate(items, 1):
                path = ("%s.%d" % (prefix, i)) if prefix else str(i)
                if n.children:
                    idx = self.model.index_of(n)
                    if idx.isValid():
                        self.tree.setExpanded(self.proxy.mapFromSource(idx), path in want)
                    walk(n.children, path)

        walk(self.session.toc)

    def _expand_ancestors(self, node) -> None:
        """把某个节点的上级依次展开，保证它在视野里"""
        parent = self.model._parent_node(node)
        chain = []
        while parent is not None:
            chain.append(parent)
            parent = self.model._parent_node(parent)
        for p in reversed(chain):
            idx = self.model.index_of(p)
            if idx.isValid():
                pi = self.proxy.mapFromSource(idx)
                if pi.isValid():
                    self.tree.expand(pi)

    def _restore_expansion(self, expand_all: bool = False) -> None:
        """打开书籍时决定展开到哪一层

        过去一律 expandAll()：300 章要 17~68 ms、1000 章 43~103 ms，而用户
        其实只想看第一层。现在：小书照旧全展开；大书恢复上次的展开状态，
        第一次打开只展开第一层。
        """
        if not self.session:
            return
        if expand_all:
            self.tree.expandAll()
            return
        try:
            raw = self.settings.value(self._expansion_key(), "")
        except Exception:
            raw = ""
        paths = None
        if isinstance(raw, str) and raw.strip():
            try:
                paths = json.loads(raw).get("paths")
            except (ValueError, AttributeError):
                paths = None
        if paths is not None:
            self._apply_expansion_paths(paths)
            node = self._current_node()
            if node is not None:
                self._expand_ancestors(node)
            return
        if self.session.counts()["total"] <= self.EXPAND_ALL_LIMIT:
            self.tree.expandAll()               # 小书全展开既快又是一眼看全
        else:
            self.tree.expandToDepth(0)          # 大书只展开第一层

    def _refresh_counts(self) -> None:
        if not self.session:
            return
        c = self.session.counts()      # 有缓存，状态栏与导航栏不必各遍历一次
        self.nav_meta.setText("%s · %d 项" % (self._source_name(), c["total"]))
        self.status_left.setText("%s  ·  %d 个目录项  ·  %s"
                                 % (os.path.basename(self.path or ""), c["total"],
                                    self._validate_summary()))

    def _source_name(self) -> str:
        if not self.session:
            return "—"
        src = self.session.book.toc_source
        return {"nav": "nav.xhtml", "ncx": "toc.ncx", "nav+ncx": "nav.xhtml + toc.ncx",
                "spine": "spine 回退"}.get(src, src)

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

    def _update_parent_info(self, node) -> None:
        """章节详情里的「所属一级标题」：下级条目才显示，选中谁就显示谁的上级"""
        if node is None or not self.session:
            self.parent_row.setVisible(False)
            self.btn_promote.setEnabled(False)
            self.act_promote.setEnabled(False)
            return
        parent = self.model._parent_node(node)
        if parent is None:                 # 一级条目：没有上级，整行收起
            self.parent_row.setVisible(False)
            self.btn_promote.setEnabled(False)
            self.act_promote.setEnabled(False)
            return
        root = self.session.root_of(node) or parent
        try:
            pos = self.session.toc.index(root) + 1
        except ValueError:                 # pragma: no cover - root_of 已保证在目录里
            pos = 0
        self.parent_label.setText("所属一级标题：《%s》 · 第 %d 位" % (root.title, pos))
        self.parent_label.setToolTip("点击右侧「升为一级」会将《%s》移至《%s》之后"
                                     % (node.title, root.title))
        self.parent_row.setVisible(True)
        self.btn_promote.setEnabled(True)
        self.act_promote.setEnabled(True)

    def promote_selected(self) -> None:
        """把选中的下级条目升为一级条目（子级与正文链接一并带走）"""
        node = self._current_node()
        if not node or not self.session or self.model._parent_node(node) is None:
            return
        try:
            msg = self.session.promote_to_top(self.model.path_of(node))
        except Exception as exc:
            QMessageBox.warning(self, "升级失败", str(exc))
            return
        self._log("升级", msg, "ok")
        self._refresh_tree(select_uid=node.uid)

    def _on_selection_changed(self, *_):
        node = self._current_node()
        if node is None:
            self.detail_level.setVisible(False)
            self._update_empty_state()
            self._update_actions()
            return
        depth = self.model._parents.get(node.uid, (None, 0))[1] + 1
        kind = "大标题" if node.is_group_like else "章节"
        self.detail_level.setText("第 %d 层 · %s" % (depth, kind))
        self.detail_level.setVisible(True)
        self._schedule_preview(node)
        self._syncing = True
        self.title_edit.setText(node.title)
        self.file_edit.setText((node.file or "（仅作分组，无正文文件）")
                               + (("#" + node.anchor) if node.anchor else ""))
        self._syncing = False
        # 这里原来有 self._readers = []：每次选中条目都把阅读器登记表清空，
        # 之后 _forget_reader 找不到窗口，换书时也就关不掉旧书的阅读窗口。
        self.detail_stack.setVisible(True)
        self.detail_empty.setVisible(False)
        self._update_actions()

    def _schedule_preview(self, node) -> None:
        """选中变化只登记，渲染留到下一轮事件循环

        打开书籍时 setCurrentIndex 会连带渲染首章正文；几百 KB 的章节要 30~70 ms，
        用户看到的是「点了打开，窗口半天不出来」。推迟一拍，先让首帧画出来。
        连续切换选中项时还会自然合并成「只渲染最后一条」。
        """
        self._preview_pending = node
        if self._preview_queued:
            return
        self._preview_queued = True
        QTimer.singleShot(0, self._flush_preview)

    def _flush_preview(self) -> None:
        self._preview_queued = False
        node = self._preview_pending
        self._preview_pending = None
        if node is None or not self.session:
            return
        # 排队期间节点可能已被删除 / 换书，uid 不在模型里就别渲染了
        if node.uid not in self.model._parents and node not in self.session.toc:
            return
        self._load_preview(node)

    def _load_preview(self, node) -> None:
        """选中条目后把正文渲染到左侧预览（纯分组大标题或无正文条目显示空状态）

        带链接的大标题（其他软件做的卷/部，既有自己的页面又有子项）同样预览
        自己那一页，别因为「有子项」就一律显示空状态。
        """
        words = ""
        ok = False
        if node is not None and node.file:
            ok = self.preview_view.show_node(node)
            if ok:
                text = self.preview_view.toPlainText()
                words = "%d 字" % len("".join(text.split()))
        if not ok:
            self.preview_view.clear()
        self._preview_ready = ok
        self.preview_words.setText(words)
        self.preview_words.setVisible(bool(words))    # 没字数就不显示空白气泡
        self.preview_view.setVisible(ok)
        self.preview_empty.setVisible(not ok)
        self.btn_preview_open.setEnabled(ok and node is not None)

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
        win.chapterEdited.connect(self._on_chapter_edited)
        self._readers.append(win)
        win.show()
        win.raise_()
        win.activateWindow()

    def _on_chapter_edited(self, path: str) -> None:
        """阅读窗口保存了正文：刷新左侧预览、字数与「未保存」状态。"""
        node = self._current_node()
        if node is not None and node.file == path:
            self._load_preview(node)
        self._refresh_counts()
        self._update_actions()

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
                self._expand_ancestors(node)    # 折叠着的父级先打开，否则选中的行看不见
                self.tree.setCurrentIndex(pi)
                self.tree.scrollTo(pi)

    def _select_nodes(self, uids) -> None:
        """按 uid 批量选中（顺带展开祖先，保证选中项在视野里）"""
        sel = QItemSelection()
        first = None
        for uid in uids:
            node = self.model.find_uid(uid)
            if node is None:
                continue
            idx = self.model.index_of(node)
            if not idx.isValid():
                continue
            parent = self.model._parent_node(node)
            while parent is not None:
                pi = self.proxy.mapFromSource(self.model.index_of(parent))
                if pi.isValid():
                    self.tree.expand(pi)
                parent = self.model._parent_node(parent)
            pi = self.proxy.mapFromSource(idx)
            if pi.isValid():
                sel.select(pi, pi)
                first = first or pi
        if first is None:
            return
        sm = self.tree.selectionModel()
        sm.select(sel, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        # 注意：QTreeView.setCurrentIndex() 会顺手改成「只选当前项」，
        # 多选会当场退化成单选，所以当前项必须用 NoUpdate 单独设
        sm.setCurrentIndex(first, QItemSelectionModel.NoUpdate)
        self.tree.scrollTo(first)

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
        selected = self._selected_nodes()
        if len(selected) > 1 and self._move_block(selected, delta):
            return
        pos = self._sibling_position(node)
        total = self._sibling_count(node)
        if not (1 <= pos + delta <= total):
            return
        # move() 的 target 是「插到第 N 位之前」，同层后移时源节点会被先摘掉、
        # 后面的元素整体左移一格；下移必须把这格补回来，否则目标正好落在自己身上，
        # 表现为「点了没反应」（上移不受影响，所以过去只有下移是坏的）。
        dest_pos = pos + delta + (1 if delta > 0 else 0)
        # 目标必须表达"同一父级内的第 N 位"，否则单数字会被当成根层级位置
        parent = self.model._parent_node(node)
        dest = str(dest_pos) if parent is None \
            else "%s.%d" % (self.model.path_of(parent), dest_pos)
        try:
            self.session.move(self.model.path_of(node), dest)
        except Exception as exc:
            QMessageBox.warning(self, "移动失败", str(exc))
            return
        self._log("移动", "「%s」→ 第 %d 位" % (node.title, pos + delta), "info")
        self._refresh_tree(select_uid=node.uid)

    def _move_block(self, nodes, delta: int) -> bool:
        """多选、同层时整块挪一格；返回 False 表示不适用（交给单条逻辑）"""
        parent = self.model._parent_node(nodes[0])
        if any(self.model._parent_node(n) is not parent for n in nodes):
            return False
        items = self.model._items(parent)
        pairs = []
        for n in nodes:
            try:
                pairs.append((items.index(n) + 1, n))
            except ValueError:
                return False
        pairs.sort(key=lambda t: t[0])
        pos = [p for p, _n in pairs]
        ordered = [n for _p, n in pairs]
        total = len(items)
        first, last = pos[0], pos[-1]
        if delta < 0:
            if first <= 1:
                return True                     # already 到顶
            dest_pos = first - 1
        else:
            if last >= total:
                return True                     # 已经到底
            dest_pos = last + 2                 # 移到块后那个元素的后面
        prefix = "" if parent is None else self.model.path_of(parent)
        if dest_pos > total + 1:
            dest = "end" if not prefix else "end:" + prefix
        else:
            dest = str(dest_pos) if not prefix else "%s.%d" % (prefix, dest_pos)
        try:
            msg = self.session.move_many([self.model.path_of(n) for n in ordered], dest)
        except Exception as exc:
            QMessageBox.warning(self, "移动失败", str(exc))
            return True
        self._log("移动", msg, "info")
        self._refresh_tree(select_uids=[n.uid for n in ordered])
        return True

    def _group_nodes(self):
        """现有的大标题清单：[(path, node), …]（按目录顺序）"""
        out = []

        def walk(items, prefix=""):
            for i, n in enumerate(items, 1):
                path = ("%s.%d" % (prefix, i)) if prefix else str(i)
                if n.is_group_like:
                    out.append((path, n))
                walk(n.children, path)

        walk(self.session.toc)
        return out

    def move_into_group(self, title: str = "") -> None:
        """把选中的条目成组挪到某个已有大标题下（可一次挪多章）

        title 仅测试用：直接给大标题名称就跳过选择框。
        """
        nodes = self._selected_nodes()
        if not nodes or not self.session:
            return
        chosen = set(n.uid for n in nodes)
        candidates = []
        for path, g in self._group_nodes():
            # 不能把条目挪进自己的子树里（含「把大标题挪进自己」）
            inside = False
            probe = g
            while probe is not None and not inside:
                inside = probe.uid in chosen
                probe = self.model._parent_node(probe)
            if not inside:
                candidates.append((path, g))
        if not candidates:
            QMessageBox.information(self, "没有可用的目标",
                                    "目录里还没有大标题，可先用「新建大标题…」建一个。")
            return

        labels = ["《%s》 · 第 %s 位" % (g.title, path.split(".")[-1]) for path, g in candidates]
        if title:
            hit = next(((p, g) for p, g in candidates if g.title == title), None)
        else:
            from PySide6.QtWidgets import QInputDialog
            name, ok = QInputDialog.getItem(
                self, "移入大标题",
                "把选中的 %d 个条目移到哪个大标题下：" % len(nodes),
                labels, 0, False)
            hit = candidates[labels.index(name)] if ok and name in labels else None
        if hit is None:
            return
        target_path, target = hit
        # 已经在这个大标题末尾：再挪也是原地不动，别让用户以为按钮坏了
        kids = target.children
        if len(nodes) <= len(kids) and all(
                nodes[i] is kids[len(kids) - len(nodes) + i] for i in range(len(nodes))):
            QMessageBox.information(self, "无需移动",
                                    "选中的条目已经排在《%s》末尾了。" % target.title)
            return
        try:
            msg = self.session.move_many([self.model.path_of(n) for n in nodes],
                                         "end:" + target_path)
        except Exception as exc:
            QMessageBox.warning(self, "移动失败", str(exc))
            return
        self._log("移动", "%s → 《%s》" % (msg, target.title), "ok")
        self.status_left.setText("已把 %d 个条目移到《%s》末尾" % (len(nodes), target.title))
        self._refresh_tree(select_uids=[n.uid for n in nodes])

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
                                        "所选条目不在同一层级，请仅选择同一层级下的连续条目。")
                return
            positions.append(self._sibling_position(n))
        positions.sort()
        if positions != list(range(positions[0], positions[0] + len(positions))):
            QMessageBox.information(self, "无法打包", "所选条目必须为连续的一段。")
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
        if node.children:
            box = QMessageBox(self)
            box.setWindowTitle("删除大标题")
            box.setIcon(QMessageBox.Warning)
            box.setText("「%s」下还有 %d 个目录项，如何处理？" % (node.title, len(node.children)))
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
            box.setInformativeText("将同时移除目录项、manifest 与 spine 引用，并删除正文文件 %s。"
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

    def expand_tree(self) -> None:
        """展开全部目录：显示所有层级，不改变数据"""
        if not self.session:
            return
        self.tree.expandAll()
        node = self._current_node()
        if node is not None:
            self._select_node(node)      # 展开后把选中项带回视野
        self.tree.setFocus()

    def collapse_tree(self) -> None:
        """折叠全部目录项：只改展开状态，不改变数据；回到顶部看一级条目"""
        if not self.session:
            return
        self.tree.collapseAll()
        self.tree.scrollToTop()
        self.tree.setFocus()

    def _on_search(self, text: str) -> None:
        self.proxy.set_keyword(text)
        if text.strip() and self.session and self.proxy.rowCount() == 0:
            self.tree_empty.set_text("没有匹配的章节", "请尝试更短的关键词，或清空搜索框")
            self.tree_empty.setVisible(True)
            self.tree.setVisible(False)
        else:
            self.tree_empty.setVisible(self.session is None)
            self.tree.setVisible(self.session is not None)
            if self.session is None:
                self.tree_empty.set_text("尚未打开书籍", "打开 EPUB 后可在此编辑目录")
        if text.strip() and self.session:
            self.tree.expandAll()

    def _menu_selection(self, idx) -> None:
        """右键点到选区内时保住多选

        QTreeView.setCurrentIndex() 会顺手把选区改成「只选这一行」，于是右键前
        框选好的十几章会当场退化成单选——「移入大标题…」就只挪了一个，看着像没反应。
        """
        if idx is None or not idx.isValid():
            return
        sm = self.tree.selectionModel()
        if not sm.isSelected(idx):
            # 点在选区之外：老行为——改成只选这一行
            sm.select(idx, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        sm.setCurrentIndex(idx, QItemSelectionModel.NoUpdate)

    def _tree_menu(self, pos) -> None:
        if not self.session:
            return
        self._menu_selection(self.tree.indexAt(pos))
        self.tree_menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ==================================================================
    # 文件操作（线程）
    # ==================================================================
    def _busy(self, text: str) -> None:
        self._io_busy = True
        self.status_left.setText(text)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        for w in (self.btn_open, self.btn_import, self.btn_merge, self.btn_validate,
                  self.btn_export):
            w.setEnabled(False)      # 读盘期间临时禁用，_idle → _update_actions 会恢复

    def _idle(self) -> None:
        self._io_busy = False
        QApplication.restoreOverrideCursor()
        self._update_actions()

    def _run(self, fn, on_done, busy: str) -> None:
        if self._worker is not None:
            self.status_left.setText("上一个任务还在进行，请稍候")
            return
        self._busy(busy)
        w = Worker(fn)
        self._worker = w

        @self._batched          # _idle 与 on_done 里的按钮刷新也合并成一次
        def ok(result):
            self._idle()
            on_done(result)

        def bad(msg):
            self._idle()
            QMessageBox.critical(self, "操作失败", msg)
            self.status_left.setText("操作失败：%s" % msg)
            self._log("错误", msg, "error")

        def cleanup():
            # QRunnable 由线程池回收（setAutoDelete），这里只松开 Python 侧引用
            if self._worker is w:
                self._worker = None

        w.signals.done.connect(ok)
        w.signals.fail.connect(bad)
        w.signals.finished.connect(cleanup)
        QThreadPool.globalInstance().start(w)

    def open_epub(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "打开 EPUB", "", "EPUB (*.epub)")
        if path:
            self.load(path)

    def load(self, path: str) -> None:
        if self.session and self.session.dirty:
            r = QMessageBox.question(self, "未保存的更改",
                                     "当前书籍有未导出的修改，仍要打开其他书籍吗？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        self._save_expansion()          # 换书前先记住这本书展开到哪
        for w in list(self._readers):   # 旧的阅读窗口跟着旧书一起关掉，别拖住整本书
            w.close()

        def job():
            return CF.Session(path)

        @self._batched                     # 期间的三次按钮刷新合并成一次
        def done(session):
            self.session = session
            self.path = path
            self._last_issues = []
            self._exported_copy = False
            self._last_export = None
            self._export_note = "—"
            self._file_note = "未改动（原子写入）"
            self._preview_pending = None    # 上一本书排队的预览任务作废
            self.model.set_session(session)
            self.preview_view.clear()       # 旧书的正文先清掉，别在新书上闪一下
            self.preview_view.set_session(session)
            self._restore_expansion()       # 小书全展开；大书只展开第一层（或上次的状态）
            self.tree.setCurrentIndex(self.proxy.index(0, 0))
            self.file_label.setText(os.path.basename(path))
            self._refresh_counts()
            self._update_empty_state()
            self._update_actions()
            self.log_list.clear_log()
            self.log_empty.setVisible(True)
            self.tree.setFocus()          # 打开后把焦点放到目录树，避免工具栏出现焦点框
            self._log("打开", "%s（EPUB %s，目录来源 %s）"
                      % (os.path.basename(path), session.book.version, self._source_name()), "info")
            self.status_left.setText("%s  ·  %d 个目录项  ·  尚未校验"
                                     % (os.path.basename(path),
                                        session.counts()["total"]))
            for wmsg in session.book.warnings:
                self._log("告警", wmsg["text"], "warn")

        self._run(job, done, "正在解析 EPUB…")

    def show_book_info(self, start_editing: bool = False) -> None:
        """书籍信息与元数据合并为一个页面：默认只读，点击底部「编辑」就地修改，再点击「保存」"""
        if not self.session:
            return
        extras = {"path": self.path or "", "source": self._source_name(),
                  "export": self._export_note, "file": self._file_note}
        dlg = BookInfoDialog(self.session, self, extras=extras,
                             start_editing=start_editing)
        dlg.exec()
        if dlg.changed:
            meta = self.session.metadata()
            self._log("元数据", "已更新「%s」" % (meta.get("title") or "无书名"), "ok")
            self._refresh_tree()
            self._update_dirty()
            self.status_left.setText("已更新书籍信息，尚未保存到文件")

    def edit_metadata(self) -> None:
        """直接进入编辑模式（菜单/快捷键入口）；普通查看走 show_book_info()"""
        self.show_book_info(start_editing=True)

    def check_numbering(self, scope_path: str = "") -> None:
        """检查章节编号：选中大标题时默认仅检查该组，否则检查全书"""
        if not self.session:
            return
        node = self._current_node()
        if not scope_path and node is not None and node.is_group_like:
            scope_path = self.model.path_of(node)
        dlg = NumberingDialog(self.session, scope_path, self)
        dlg.exec()
        used = getattr(dlg, "scope_used", scope_path)
        if used in ("", "all"):
            where = "整本书（各大标题独立编号）" if getattr(dlg, "per_group_used", False) else "整本书"
        else:
            where = "指定大标题"
        if dlg.applied:
            self._log("编号", "%s重排/补号 %d 章" % (where, dlg.applied), "ok")
        if getattr(dlg, "tidied", 0):
            self._log("标题", "规范章节名 %d 处（去掉「第X章.」里的分隔符）" % dlg.tidied, "ok")
        if dlg.applied or getattr(dlg, "tidied", 0):
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

    def add_chapter(self) -> None:
        """直接输入正文新建章节（无需 TXT / DOCX 文件）"""
        if not self.session:
            self.status_left.setText("请先打开一本 EPUB，再新建章节")
            return
        node = self._current_node()
        sel = self.model.path_of(node) if node is not None else ""
        dlg = AddChapterDialog(self.session, self, selected_path=sel)
        if not dlg.exec() or not dlg.result_info:
            return
        info = dlg.result_info
        self._log("新建章节", "《%s》%d 字 / %d 段 → %s"
                  % (info["title"], info["chars"], info["paragraphs"], info["where"]), "ok")
        self._refresh_tree(select_uid=info.get("uid", ""), expand_all=True)
        self.status_left.setText("已新建章节《%s》· %d 字，尚未保存"
                                 % (info["title"], info["chars"]))

    def merge_books(self) -> None:
        dlg = MergeDialog(self.session, self)
        if not dlg.exec() or not dlg.result:
            return
        cfg = dlg.result

        def job():
            info = self.session.merge_epubs(cfg["books"], group_each=cfg["group_each"])
            return info

        def done(info):
            self._log("合并", "并入 %d 本，新增 %d 章%s"
                      % (len(info["books"]), info["chapters"],
                         "、%d 个大标题" % info["groups"] if info.get("groups") else ""), "ok")
            for w in info["warnings"]:
                self._log("告警", w, "warn")
            self._refresh_tree(expand_all=True)

        self._run(job, done, "正在合并书籍…")

    def validate_report(self) -> None:
        if not self.session:
            return
        opts = self._export_options()
        # 只把"目录层可修"的问题交给修复按钮；清单类问题导出时会自动清理
        plan = [p for p in self.session.repair_plan()
                if p["key"] in ("dead", "empty", "downgrade")]

        def job():
            rep = self.session.export(os.devnull, dry_run=True, **opts)
            return rep

        def done(rep):
            self._last_issues = rep["issues"]
            err = sum(1 for i in rep["issues"] if i["level"] == "error")
            warn = sum(1 for i in rep["issues"] if i["level"] == "warn")
            self._log("校验", "校验：%d 错误 %d 警告（dry-run）" % (err, warn),
                      "error" if err else ("warn" if warn else "ok"))
            self._refresh_counts()
            up = self.session.upgrade_plan() if self.session.can_upgrade else None
            dlg = ReportDialog(self.session, {"export": rep}, parent=self,
                               repair_plan=plan, upgrade_plan=up)
            dlg.exec()
            if dlg.upgrade_requested:
                self.upgrade_epub()
            elif dlg.repaired:
                self._apply_repair()
                self.validate_report()

        self._run(job, done, "正在校验…")

    def upgrade_epub(self) -> None:
        """把 EPUB2 另存为 EPUB3，并载入升级后的文件"""
        if not self.session or not self.session.can_upgrade:
            return
        from PySide6.QtWidgets import QFileDialog
        base = os.path.splitext(os.path.basename(self.path or "book.epub"))[0]
        default = os.path.join(os.path.dirname(self.path or ""), base + "-EPUB3.epub")
        out, _ = QFileDialog.getSaveFileName(self, "升级为 EPUB 3", default, "EPUB (*.epub)")
        if not out:
            return

        def job():
            return self.session.upgrade_to_epub3(out)

        def done(rep):
            err = sum(1 for i in rep["issues"] if i["level"] == "error")
            if err:
                self._log("升级", "失败：%d 个错误，未生成文件" % err, "error")
                ReportDialog(self.session, {"export": rep, "path": out}, parent=self).exec()
                return
            self.session.dirty = False
            self._log("升级", "EPUB %s → %s，已生成 %s"
                      % (rep["version_from"], rep["version_to"], os.path.basename(out)), "ok")
            self.load(out)
            ReportDialog(self.session, {"export": rep, "path": out}, parent=self).exec()

        self._run(job, done, "正在升级为 EPUB 3…")

    def _apply_repair(self) -> None:
        logs = self.session.repair()
        if not logs:
            self._log("修复", "没有需要修复的项目", "info")
            return
        for line in logs:
            self._log("修复", line, "ok")
        self._refresh_tree(expand_all=True)

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
                self._exported_copy = True
                self._last_export = datetime.now()
                self._export_note = self._last_export.strftime("%Y-%m-%d %H:%M")
                self._file_note = "已导出（原文件未修改）"
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
            # 先干跑一次自检：有问题就一个字节都不动，避免"提示未改动其实已覆盖"
            probe = self.session.export(path, dry_run=True, **opts)
            err = sum(1 for i in probe["issues"] if i["level"] == "error")
            if err:
                return {"issues": probe["issues"], "written": False}
            if os.path.exists(path) and not os.path.exists(bak):
                shutil.copy2(path, bak)
            rep = self.session.export(path, **opts)
            rep["written"] = True
            return rep

        def done(rep):
            err = sum(1 for i in rep["issues"] if i["level"] == "error")
            self._last_issues = rep["issues"]
            written = rep.get("written", False)
            if written and not err:
                self.session.dirty = False
                self._exported_copy = False
                self._last_export = datetime.now()
                self._export_note = self._last_export.strftime("%Y-%m-%d %H:%M")
                self._file_note = "已保存（备份在 .bak）"
                self._log("保存", "%s（已备份为 .bak）" % os.path.basename(path), "ok")
            elif written:
                self._log("保存", "已写入但有 %d 个错误，请查看报告（备份在 .bak）" % err, "error")
            else:
                self._log("保存", "已取消：校验发现 %d 个错误，原文件未改动" % err, "error")
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
        self._save_expansion()
        if self.session and self.session.dirty:
            r = QMessageBox.question(self, "未保存的更改",
                                     "有未导出的修改，确定退出吗？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                event.ignore()
                return
        event.accept()
