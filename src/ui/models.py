# -*- coding: utf-8 -*-
"""目录树模型：直接映射 chapflow.Session 的 Node 树，拖放调用真实 move 接口"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from PySide6.QtCore import QAbstractItemModel, QMimeData, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor

from . import icons
from .tokens import COLORS

UID_ROLE = Qt.UserRole + 1
KIND_ROLE = Qt.UserRole + 2
DEPTH_ROLE = Qt.UserRole + 3
COUNT_ROLE = Qt.UserRole + 4
FILE_ROLE = Qt.UserRole + 5

MIME = "application/x-chapflow-node"


class ChapterTreeModel(QAbstractItemModel):
    """只读结构 + 通过 session 修改；不做本地排序，避免与业务顺序脱节"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session = None
        self._parents: Dict[str, object] = {}
        self._icon_group = icons.icon("folder", COLORS["accent"], 17)
        self._icon_doc = icons.icon("doc", COLORS["text_secondary"], 17)

    # -- 绑定 ------------------------------------------------------------
    def set_session(self, session) -> None:
        self.beginResetModel()
        self.session = session
        self._rebuild_parents()
        self.endResetModel()

    def refresh(self) -> None:
        self.beginResetModel()
        self._rebuild_parents()
        self.endResetModel()

    def _rebuild_parents(self) -> None:
        self._parents = {}

        def walk(items, parent=None, depth=0):
            for n in items:
                self._parents[n.uid] = (parent, depth)
                if n.children:
                    walk(n.children, n, depth + 1)

        if self.session:
            walk(self.session.toc)

    def _items(self, parent_node):
        if parent_node is None:
            return self.session.toc if self.session else []
        return parent_node.children

    def _parent_node(self, node):
        entry = self._parents.get(node.uid)
        return entry[0] if entry else None

    def node_at(self, index: QModelIndex):
        if not index.isValid():
            return None
        return index.internalPointer()

    def path_of(self, node) -> str:
        """按当前树位置拼出 1 基路径"""
        parts = []
        cur = node
        while cur is not None:
            parent = self._parent_node(cur)
            siblings = self._items(parent)
            try:
                parts.append(siblings.index(cur) + 1)
            except ValueError:
                return ""
            cur = parent
        return ".".join(str(p) for p in reversed(parts))

    def index_of(self, node) -> QModelIndex:
        parent = self._parent_node(node)
        siblings = self._items(parent)
        try:
            row = siblings.index(node)
        except ValueError:
            return QModelIndex()
        return self.createIndex(row, 0, node)

    def find_uid(self, uid: str):
        if not self.session:
            return None
        stack = list(self.session.toc)
        while stack:
            n = stack.pop()
            if n.uid == uid:
                return n
            stack.extend(n.children)
        return None

    # -- Qt 接口 ---------------------------------------------------------
    def index(self, row, column, parent=QModelIndex()):
        parent_node = parent.internalPointer() if parent.isValid() else None
        items = self._items(parent_node)
        if 0 <= row < len(items):
            return self.createIndex(row, column, items[row])
        return QModelIndex()

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        node = index.internalPointer()
        parent_node = self._parent_node(node)
        if parent_node is None:
            return QModelIndex()
        grand = self._parent_node(parent_node)
        siblings = self._items(grand)
        try:
            row = siblings.index(parent_node)
        except ValueError:
            return QModelIndex()
        return self.createIndex(row, 0, parent_node)

    def rowCount(self, parent=QModelIndex()):
        if not self.session:
            return 0
        if parent.column() > 0:
            return 0
        parent_node = parent.internalPointer() if parent.isValid() else None
        return len(self._items(parent_node))

    def columnCount(self, parent=QModelIndex()):
        return 1

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        node = index.internalPointer()
        if role == Qt.DisplayRole:
            return node.title
        if role == Qt.DecorationRole:
            return self._icon_group if node.is_group_like else self._icon_doc
        if role == Qt.ToolTipRole:
            depth = self._parents.get(node.uid, (None, 0))[1]
            loc = node.file or "（大标题，无独立链接）"
            return "%s\n%s\n层级：第 %d 层" % (node.title, loc, depth + 1)
        if role == Qt.ForegroundRole and node.is_group_like:
            return QColor(COLORS["text_primary"])
        if role == UID_ROLE:
            return node.uid
        if role == KIND_ROLE:
            return node.kind
        if role == DEPTH_ROLE:
            return self._parents.get(node.uid, (None, 0))[1]
        if role == COUNT_ROLE:
            return len(node.children)
        if role == FILE_ROLE:
            return (node.file or "") + (("#" + node.anchor) if node.anchor else "")
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemIsDropEnabled
        node = index.internalPointer()
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled
        if node.is_group_like:
            f |= Qt.ItemIsDropEnabled
        return f

    def mimeTypes(self):
        return [MIME]

    def supportedDropActions(self):
        return Qt.MoveAction

    def mimeData(self, indexes):
        """多选拖拽：把选中的全部 uid 一起带上（JSON 数组）

        以前只写 indexes[0]，框选十来章往大标题里拖，只有第一章会动。
        """
        md = QMimeData()
        uids: List[str] = []
        for idx in indexes:
            node = idx.internalPointer() if idx.isValid() else None
            if node is not None and all(node.uid != u for u in uids):
                uids.append(node.uid)
        if uids:
            md.setData(MIME, json.dumps(uids).encode("utf-8"))
        return md

    def dropMimeData(self, data, action, row, column, parent):
        if not data.hasFormat(MIME) or not self.session:
            return False
        raw = bytes(data.data(MIME)).decode("utf-8", "replace")
        try:
            uids = json.loads(raw)
        except (TypeError, ValueError):
            uids = [raw]            # 兼容旧格式：单个 uid 字符串
        if isinstance(uids, str):
            uids = [uids]
        nodes = [n for n in (self.find_uid(str(u)) for u in uids) if n is not None]
        if not nodes:
            return False

        parent_node = parent.internalPointer() if parent.isValid() else None

        # 目标若在被拖节点子树内 → 拒绝（业务层也会拒绝，这里先拦）
        for node in nodes:
            probe = parent_node
            while probe is not None:
                if probe is node:
                    return False
                probe = self._parent_node(probe)

        try:
            if parent_node is None:
                target = "end" if row < 0 else str(row + 1)
            else:
                container_path = self.path_of(parent_node)
                target = ("end:" + container_path) if row < 0 \
                    else "%s.%d" % (container_path, row + 1)
            paths = [self.path_of(n) for n in nodes]
            if len(paths) == 1:
                self.session.move(paths[0], target)
            else:
                self.session.move_many(paths, target)
        except Exception:
            return False
        self.refresh()
        return True


class ChapterFilterProxy(QSortFilterProxyModel):
    """按标题/文件名过滤，保留命中项的祖先链"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDynamicSortFilter(True)
        self.setRecursiveFilteringEnabled(True)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setFilterRole(Qt.DisplayRole)

    def set_keyword(self, text: str) -> None:
        self.setFilterFixedString((text or "").strip())

    # -- 拖放：不要走 QSortFilterProxyModel 的默认转发 -----------------------
    # Qt 的默认实现在 (row=-1, column=-1) 时会把父级索引一起丢掉，源模型收到的
    # 是「根层级」——于是「拖到某个大标题上」实际变成「挪到目录末尾」，
    # 表现为“一级标题拖不进另一个一级标题里”。这里显式映射父级再转发。
    def mimeData(self, indexes):
        source = self.sourceModel()
        if source is None:
            return None
        return source.mimeData([self.mapToSource(i) for i in indexes if i.isValid()])

    def dropMimeData(self, data, action, row, column, parent):
        source = self.sourceModel()
        if source is None:
            return False
        source_parent = self.mapToSource(parent) if parent.isValid() else QModelIndex()
        # row 是代理坐标系的插入位置；过滤隐藏部分行后与源模型不同，
        # 必须先映射到源行号，否则搜索过滤开启时会落到错误位置
        if row >= 0:
            idx = self.index(row, 0, parent)
            if idx.isValid():
                row = self.mapToSource(idx).row()
            else:
                # 落在列表末尾（row == rowCount）：映射最后一行再 +1
                last = self.index(row - 1, 0, parent)
                row = self.mapToSource(last).row() + 1 if last.isValid() else -1
        return source.dropMimeData(data, action, row, column, source_parent)

    def supportedDropActions(self):
        source = self.sourceModel()
        return source.supportedDropActions() if source is not None else Qt.MoveAction

    def mimeTypes(self):
        source = self.sourceModel()
        return source.mimeTypes() if source is not None else [MIME]
