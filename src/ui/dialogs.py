# -*- coding: utf-8 -*-
"""对话框：导入 TXT/DOCX、合并书籍、自检报告、关于"""

from __future__ import annotations

import os
from datetime import datetime
from typing import List, Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                               QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QRadioButton, QScrollArea, QSizePolicy, QSpinBox,
                               QVBoxLayout, QWidget)

from . import icons
from .tokens import COLORS, FONTS, METRICS
from .widgets import Card

import chapflow as CF
import docxconv as DX
import txtconv as TX


def _hline() -> QFrame:
    f = QFrame()
    f.setObjectName("HLine")
    f.setFixedHeight(1)
    return f


def _btn(text: str, kind: str = "", icon_name: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    if icon_name:
        b.setIcon(icons.icon(icon_name, "#FFFFFF" if kind == "Primary" else COLORS["text_secondary"], 16))
    b.setCursor(Qt.PointingHandCursor)
    return b


class DialogHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("DialogHeader")
        self.setFixedHeight(72)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(22, 12, 22, 12)
        col = QVBoxLayout()
        col.setSpacing(2)
        t = QLabel(title)
        t.setObjectName("DialogTitle")
        col.addWidget(t)
        self.sub = QLabel(subtitle)
        self.sub.setObjectName("Muted")
        col.addWidget(self.sub)
        lay.addLayout(col)
        lay.addStretch(1)


# =========================================================================
# 导入 TXT / DOCX
# =========================================================================

class ImportDialog(QDialog):
    LABELS = {"auto": "自动", "heading": "按标题", "size": "按字数", "none": "整段"}
    PARAS = {"blank": "空行分段", "line": "每行一段", "indent": "缩进分段"}

    def __init__(self, session, path: str, kind: str, parent=None):
        super().__init__(parent)
        self.session = session
        self.kind = kind
        self.path = path
        self.result_info = None
        self.setWindowTitle("导入 %s 章节" % ("DOCX" if kind == "docx" else "TXT"))
        self.setMinimumSize(880, 720)
        self.setModal(True)
        self._build()
        self._load_preview()

    # -- UI --------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(DialogHeader(
            "导入 %s 章节" % ("DOCX" if self.kind == "docx" else "TXT"),
            "识别标题层级并切分；导入后写入目录与阅读顺序，原书其余内容不变"))

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(22, 16, 22, 16)
        lay.setSpacing(14)
        root.addWidget(body, 1)

        # 文件行
        file_card = Card()
        fl = QHBoxLayout()
        fl.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(icons.icon("doc", COLORS["accent"], 26).pixmap(26, 26))
        fl.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.file_name = QLabel(os.path.basename(self.path))
        self.file_name.setStyleSheet("font-weight:600;")
        self.file_meta = QLabel("读取中…")
        self.file_meta.setObjectName("Muted")
        col.addWidget(self.file_name)
        col.addWidget(self.file_meta)
        fl.addLayout(col, 1)
        change = _btn("更换", "Ghost")
        change.clicked.connect(self._choose_file)
        fl.addWidget(change)
        file_card.body().addLayout(fl)
        lay.addWidget(file_card)

        # 选项
        opt = Card("切分与段落")
        grid = QHBoxLayout()
        grid.setSpacing(28)

        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(self._label("切分模式"))
        row = QHBoxLayout()
        row.setSpacing(10)
        self.split_btns = {}
        for key, text in self.LABELS.items():
            rb = QRadioButton(text)
            rb.setChecked(key == "auto")
            rb.toggled.connect(lambda _c, k=key: self._on_split_changed(k))
            self.split_btns[key] = rb
            row.addWidget(rb)
        row.addStretch(1)
        left.addLayout(row)
        self.split_hint = QLabel("已识别「第X章 / 卷 / 序章 / Chapter N」等标题模式")
        self.split_hint.setObjectName("Muted")
        left.addWidget(self.split_hint)

        self.size_row = QWidget()
        sr = QHBoxLayout(self.size_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.setSpacing(8)
        sr.addWidget(self._label("每章约"))
        self.size_spin = QSpinBox()
        self.size_spin.setRange(500, 20000)
        self.size_spin.setSingleStep(500)
        self.size_spin.setValue(3000)
        self.size_spin.setSuffix(" 字")
        sr.addWidget(self.size_spin)
        sr.addStretch(1)
        left.addWidget(self.size_row)
        grid.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(8)
        self.para_block = QWidget()
        pl = QVBoxLayout(self.para_block)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(8)
        pl.addWidget(self._label("段落模式"))
        prow = QHBoxLayout()
        prow.setSpacing(10)
        self.para_btns = {}
        for key, text in self.PARAS.items():
            rb = QRadioButton(text)
            rb.setChecked(key == "line")
            self.para_btns[key] = rb
            prow.addWidget(rb)
        prow.addStretch(1)
        pl.addLayout(prow)
        self.para_hint = QLabel("按 3000 字切分时将在段落边界断开")
        self.para_hint.setObjectName("Muted")
        pl.addWidget(self.para_hint)
        right.addWidget(self.para_block)

        self.docx_row = QWidget()
        dl = QHBoxLayout(self.docx_row)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(8)
        dl.addWidget(self._label("按几级标题切章"))
        self.level_spin = QSpinBox()
        self.level_spin.setRange(1, 4)
        self.level_spin.setValue(1)
        self.level_spin.setPrefix("标题 ")
        dl.addWidget(self.level_spin)
        dl.addStretch(1)
        right.addWidget(self.docx_row)
        grid.addLayout(right, 1)
        opt.body().addLayout(grid)

        checks = QHBoxLayout()
        checks.setSpacing(18)
        self.cb_pagenum = QCheckBox("丢弃纯页码行")
        self.cb_css = QCheckBox("继承书内样式表")
        self.cb_css.setChecked(True)
        self.cb_encoding = QCheckBox("强制指定编码")
        self.enc_combo = QComboBox()
        self.enc_combo.addItems(["UTF-8", "GB18030", "GBK", "BIG5", "UTF-16"])
        self.enc_combo.setEnabled(False)
        self.cb_encoding.toggled.connect(self.enc_combo.setEnabled)
        checks.addWidget(self.cb_pagenum)
        checks.addWidget(self.cb_css)
        checks.addWidget(self.cb_encoding)
        checks.addWidget(self.enc_combo)
        checks.addStretch(1)
        opt.body().addLayout(checks)
        lay.addWidget(opt)

        # 预览
        prev = Card("章节预览")
        self.count_badge = QLabel("—")
        self.count_badge.setObjectName("Badge")
        prev.add_header_widget(self.count_badge)
        self.preview_list = QListWidget()
        self.preview_list.setMinimumHeight(200)
        self.preview_list.setSelectionMode(QAbstractItemView.NoSelection)
        prev.add(self.preview_list)
        lay.addWidget(prev, 1)

        # 插入位置
        pos = QHBoxLayout()
        pos.setSpacing(8)
        pos.addWidget(self._label("插入位置"))
        self.pos_combo = QComboBox()
        self.pos_combo.addItem("目录末尾", "end")
        self.pos_combo.addItem("新建大标题「导入章节」", "group")
        self.pos_combo.addItem("第 1 位", "1")
        pos.addWidget(self.pos_combo, 1)
        lay.addLayout(pos)

        # 底部
        foot = QWidget()
        foot.setObjectName("Toolbar")
        fl2 = QHBoxLayout(foot)
        fl2.setContentsMargins(22, 10, 22, 10)
        fl2.setSpacing(10)
        self.summary = QLabel("")
        self.summary.setObjectName("Muted")
        fl2.addWidget(self.summary)
        fl2.addStretch(1)
        cancel = _btn("取消", "Ghost")
        cancel.clicked.connect(self.reject)
        preview = _btn("预览（不落盘）")
        preview.clicked.connect(self._preview)
        ok = _btn("导入", "Primary")
        ok.clicked.connect(self._accept)
        self.ok_btn = ok
        fl2.addWidget(cancel)
        fl2.addWidget(preview)
        fl2.addWidget(ok)
        root.addWidget(foot)

    def _label(self, text: str) -> QLabel:
        lb = QLabel(text)
        lb.setObjectName("FieldLabel")
        return lb

    # -- 交互 ------------------------------------------------------------
    def _choose_file(self) -> None:
        flt = "DOCX (*.docx)" if self.kind == "docx" else "TXT (*.txt)"
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", self.path, flt)
        if path:
            self.path = path
            self.file_name.setText(os.path.basename(path))
            self._load_preview()

    def _on_split_changed(self, key: str) -> None:
        self.size_row.setVisible(key == "size")
        self.split_hint.setText(
            "已识别「第X章 / 卷 / 序章 / Chapter N」等标题模式" if key in ("auto", "heading")
            else "将在段落边界断开，标题按顺序编号")

    def _opts(self) -> dict:
        split = next(k for k, b in self.split_btns.items() if b.isChecked())
        para = next((k for k, b in self.para_btns.items() if b.isChecked()), "line")
        enc = None
        if self.cb_encoding.isChecked():
            enc = self.enc_combo.currentText().lower().replace("-", "")
            enc = {"utf8": "utf-8", "utf16": "utf-16"}.get(enc, enc)
        return {"split": split, "size": self.size_spin.value(), "para": para,
                "encoding": enc, "drop_pn": self.cb_pagenum.isChecked(),
                "css": self.cb_css.isChecked(), "level": self.level_spin.value()}

    def _load_preview(self) -> None:
        self.preview_list.clear()
        o = self._opts()
        try:
            if self.kind == "docx":
                doc = DX.read_docx(self.path, split_level=o["level"])
                chapters = [(c.title, sum(len(p.text) for p in []) or len(c.blocks)) for c in doc.chapters]
                self.file_meta.setText("%s · %d 章 · 图片 %d 张"
                                       % (_size_text(self.path), len(doc.chapters), len(doc.media)))
                self.docx_row.setVisible(True)
                self.para_block.setVisible(False)
                self.cb_pagenum.setEnabled(False)
                self.cb_encoding.setEnabled(False)
                self.enc_combo.setEnabled(False)
                self.split_hint.setText("按 Word 标题样式（标题 1 / Heading 1）切章")
            else:
                text, enc = TX.read_text_file(self.path, o["encoding"])
                chapters_raw, stats = TX.split_chapters(
                    text, os.path.splitext(os.path.basename(self.path))[0],
                    mode=o["split"], size=o["size"], para_mode=o["para"],
                    drop_page_numbers=o["drop_pn"])
                chapters = [(c.title, c.char_count) for c in chapters_raw]
                self.file_meta.setText("%s · 自动识别编码 %s · 换行已归一化"
                                       % (_size_text(self.path), enc.upper()))
                self.split_hint.setText("已识别「第X章 / 卷 / 序章 / Chapter N」等标题模式（命中 %d 处）"
                                        % stats.get("headings", 0))
                self.docx_row.setVisible(False)
                self.para_hint.setText("按 %d 字切分时将在段落边界断开" % o["size"])
        except Exception as exc:
            self.file_meta.setText("读取失败：%s" % exc)
            self.count_badge.setText("—")
            self.ok_btn.setEnabled(False)
            return

        self.ok_btn.setEnabled(bool(chapters))
        self.count_badge.setText("识别 %d 章" % len(chapters))
        self.summary.setText("共 %d 章" % len(chapters))
        for i, (title, _n) in enumerate(chapters[:200], start=1):
            item = QListWidgetItem()
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 2, 8, 2)
            h.setSpacing(10)
            idx = QLabel("%02d" % i)
            idx.setObjectName("Muted")
            idx.setFixedWidth(26)
            idx.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            t = QLabel(title)
            t.setToolTip(title)
            t.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            h.addWidget(idx)
            h.addWidget(t, 1)
            item.setSizeHint(QSize(120, 28))
            self.preview_list.addItem(item)
            self.preview_list.setItemWidget(item, row)

    def _preview(self) -> None:
        """真实 dry-run 导出，不落盘，直接展示自检结果"""
        info = self._apply(dry_run=True)
        if info is None:
            return
        ReportDialog(self.session, info, title="导入预览（未落盘）", parent=self).exec()

    def _apply(self, dry_run: bool):
        o = self._opts()
        s = self.session
        before_undo = s.undo_depth
        try:
            if self.kind == "docx":
                info = s.append_docx(self.path, group_title="导入章节" if
                                     self.pos_combo.currentData() == "group" else None,
                                     at="end" if self.pos_combo.currentData() in ("end", "group")
                                     else str(self.pos_combo.currentData()),
                                     split_level=o["level"], link_css=o["css"])
            else:
                info = s.append_txt(self.path, group_title="导入章节" if
                                    self.pos_combo.currentData() == "group" else None,
                                    at="end" if self.pos_combo.currentData() in ("end", "group")
                                    else str(self.pos_combo.currentData()),
                                    split=o["split"], size=o["size"], para_mode=o["para"],
                                    encoding=o["encoding"], link_css=o["css"],
                                    drop_page_numbers=o["drop_pn"])
            if dry_run:
                rep = s.export(os.devnull, dry_run=True)
            else:
                rep = {"issues": []}
            return {"append": info, "export": rep}
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            while s.undo_depth > before_undo:
                s.undo()
            return None

    def _accept(self) -> None:
        info = self._apply(dry_run=False)
        if info is None:
            return
        self.result_info = info["append"]
        self.accept()


class ReportDialog(QDialog):
    """导出自检报告（对应 PPT 第 4 页），可附带一键修复"""

    def __init__(self, session, payload: dict, title: str = "导出自检报告",
                 parent=None, repair_plan=None):
        super().__init__(parent)
        self.session = session
        self.payload = payload
        self.repair_plan = repair_plan or []
        self.repaired = False
        self.setWindowTitle(title)
        self.setMinimumSize(820, 720)
        self.setModal(True)
        self._build()

    def _build(self) -> None:
        rep = self.payload.get("export", {}) if isinstance(self.payload, dict) else {}
        issues = list(rep.get("issues", []))
        ok = [i for i in issues if i["level"] == "ok"]
        warn = [i for i in issues if i["level"] == "warn"]
        err = [i for i in issues if i["level"] == "error"]

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        path = self.payload.get("path") or ""
        sub = os.path.basename(path) if path else "当前编辑结果"
        hdr = DialogHeader("自检报告", "%s · %s" % (sub, datetime.now().strftime("%Y-%m-%d %H:%M")))
        root.addWidget(hdr)

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(12)
        root.addWidget(body, 1)

        stats = QHBoxLayout()
        stats.setSpacing(12)
        for value, label, color in ((len(ok), "通过", COLORS["accent"]),
                                    (len(warn), "警告（可继续导出）", COLORS["warning"]),
                                    (len(err), "错误", COLORS["danger"])):
            box = Card()
            box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            box.setFixedHeight(96)
            v = QLabel(str(value))
            v.setStyleSheet("font-size:22pt; font-weight:600; color:%s;" % color)
            l = QLabel(label)
            l.setObjectName("Muted")
            box.body().addWidget(v)
            box.body().addWidget(l)
            stats.addWidget(box)
        lay.addLayout(stats)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        il = QVBoxLayout(inner)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(10)

        groups = [
            ("ZIP 容器", ("mimetype",)),
            ("包结构（manifest / spine）", ("manifest", "spine")),
            ("nav.xhtml / toc.ncx", ("nav", "ncx", "playOrder")),
            ("目录与引用", ("目录", "链接", "清理")),
            ("元数据与兼容性", ("dcterms", "unique-identifier", "元数据")),
        ]
        used = set()
        for gname, keys in groups:
            rows = []
            for it in issues:
                if id(it) in used:
                    continue
                if any(k.lower() in it["text"].lower() for k in keys):
                    rows.append(it)
                    used.add(id(it))
            if rows:
                il.addWidget(self._group(gname, rows))
        rest = [i for i in issues if id(i) not in used]
        if rest:
            il.addWidget(self._group("其它", rest))

        if self.repair_plan:
            fix_card = Card("可以自动修复")
            total = sum(p["count"] for p in self.repair_plan)
            badge = QLabel("%d 项" % total)
            badge.setObjectName("Badge")
            fix_card.add_header_widget(badge)
            for p in self.repair_plan:
                row = QWidget()
                h = QHBoxLayout(row)
                h.setContentsMargins(0, 0, 0, 0)
                h.setSpacing(8)
                ic = QLabel()
                ic.setPixmap(icons.icon("alert", COLORS["warning"], 15).pixmap(15, 15))
                ic.setFixedWidth(18)
                txt = QLabel(p["label"])
                txt.setWordWrap(True)
                h.addWidget(ic, 0, Qt.AlignTop)
                h.addWidget(txt, 1)
                fix_card.add(row)
            il.addWidget(fix_card)

        il.addStretch(1)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

        foot = QWidget()
        foot.setObjectName("Toolbar")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(20, 10, 20, 10)
        fl.setSpacing(10)
        concl = "自检结论：%s" % ("通过（可导出）" if not err else "存在 %d 个错误，建议修复后再导出" % len(err))
        self.conclusion = QLabel(concl)
        self.conclusion.setStyleSheet(
            "color:%s; font-weight:600;" % (COLORS["accent"] if not err else COLORS["danger"]))
        fl.addWidget(self.conclusion)
        fl.addStretch(1)
        close = _btn("关闭", "Ghost")
        close.clicked.connect(self.reject)
        fl.addWidget(close)
        if self.repair_plan:
            total = sum(p["count"] for p in self.repair_plan)
            fix = _btn("修复 %d 项并重新检查" % total, "Primary")
            fix.setToolTip("移除指向缺失文件的目录条目、清理空大标题；修复后可撤销")
            fix.clicked.connect(self._do_repair)
            fl.addWidget(fix)
        root.addWidget(foot)

    def _do_repair(self) -> None:
        self.repaired = True
        self.accept()

    def _group(self, name: str, rows: List[dict]) -> QWidget:
        card = Card(name)
        warn = sum(1 for r in rows if r["level"] == "warn")
        err = sum(1 for r in rows if r["level"] == "error")
        badge = QLabel("%d 项%s" % (len(rows), "警告" if warn and not err else ("错误" if err else "通过")))
        badge.setObjectName("Badge")
        card.add_header_widget(badge)
        for r in rows:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)
            name_map = {"ok": ("check-circle", COLORS["accent"]),
                        "warn": ("alert", COLORS["warning"]),
                        "error": ("alert", COLORS["danger"])}
            ic_name, color = name_map.get(r["level"], ("dot", COLORS["text_secondary"]))
            ic = QLabel()
            ic.setPixmap(icons.icon(ic_name, color, 15).pixmap(15, 15))
            ic.setFixedWidth(18)
            text = QLabel(r["text"])
            text.setWordWrap(True)
            text.setStyleSheet("color:%s;" % (COLORS["danger"] if r["level"] == "error"
                                              else COLORS["text_primary"]))
            h.addWidget(ic, 0, Qt.AlignTop)
            h.addWidget(text, 1)
            card.add(row)
        return card


# =========================================================================
# 合并
# =========================================================================

class MergeDialog(QDialog):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.books: List[str] = []
        self.result = None
        self.setWindowTitle("合并多本 EPUB")
        self.setMinimumSize(920, 720)
        self.setModal(True)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        hdr = DialogHeader("合并多本 EPUB", "主书的元数据与封面为准；其余书的资源整树搬入 merged/bN/")
        step = QWidget()
        sl = QHBoxLayout(step)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(6)
        for i, text in enumerate(("选择书籍", "排序与分组", "确认合并"), start=1):
            lb = QLabel("%d  %s" % (i, text))
            lb.setObjectName("StepActive" if i == 1 else "StepIdle")
            sl.addWidget(lb)
        hdr.layout().addWidget(step)
        root.addWidget(hdr)

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(12)
        root.addWidget(body, 1)

        top = QHBoxLayout()
        top.setSpacing(12)
        master = Card("主书（元数据以此为准）")
        self.master_label = QLabel("—")
        self.master_label.setStyleSheet("font-weight:600;")
        self.master_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.master_meta = QLabel("—")
        self.master_meta.setObjectName("Muted")
        self.master_meta.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        master.body().addWidget(self.master_label)
        master.body().addWidget(self.master_meta)
        master.add_stretch(1)
        top.addWidget(master, 1)

        opt = Card("合并选项")
        self.cb_group = QCheckBox("每本书放进一个大标题")
        self.cb_group.setToolTip("用书名作为大标题，把该书的章节收在下面；取消勾选则全部平铺")
        self.cb_group.setChecked(True)
        self.cb_spine = QCheckBox("翻页顺序跟目录一致")
        self.cb_spine.setToolTip(
            "合并后按目录顺序重排正文文件的先后（spine）。\n"
            "不勾选：保留各书原来的先后，可能出现“目录第 3 章，翻页却跳到别的书”。")
        self.cb_spine.setChecked(True)
        self.cb_modified = QCheckBox("更新书的修改时间")
        self.cb_modified.setToolTip("刷新元数据里的修改时间（dcterms:modified），符合 EPUB3 规范")
        self.cb_modified.setChecked(True)
        opt.body().addWidget(self.cb_group)
        opt.body().addWidget(self.cb_spine)
        self.spine_hint = QLabel("让下一页的顺序和目录一致，避免翻到别的书")
        self.spine_hint.setObjectName("Muted")
        self.spine_hint.setContentsMargins(22, 0, 0, 0)
        self.spine_hint.setWordWrap(True)
        opt.body().addWidget(self.spine_hint)
        opt.body().addWidget(self.cb_modified)
        top.addWidget(opt, 1)
        lay.addLayout(top)

        list_card = Card("待合并书籍")
        self.count_badge = QLabel("0 本")
        self.count_badge.setObjectName("Badge")
        list_card.add_header_widget(self.count_badge)
        self.list = QListWidget()
        self.list.setMinimumHeight(110)
        list_card.add(self.list)
        row = QHBoxLayout()
        add = _btn("添加 EPUB 文件", "", "plus")
        add.clicked.connect(self._add_files)
        up = _btn("上移", "Ghost")
        up.clicked.connect(lambda: self._move(-1))
        down = _btn("下移", "Ghost")
        down.clicked.connect(lambda: self._move(1))
        rm = _btn("移除", "Ghost")
        rm.clicked.connect(self._remove)
        row.addWidget(add)
        row.addStretch(1)
        row.addWidget(up)
        row.addWidget(down)
        row.addWidget(rm)
        list_card.body().addLayout(row)
        lay.addWidget(list_card, 1)

        preview = Card("合并后目录结构预览")
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(150)
        self.preview.setStyleSheet(
            "background:%s; border:1px solid %s; border-radius:8px; padding:8px;"
            % (COLORS["surface_subtle"], COLORS["border"]))
        preview.add(self.preview)
        lay.addWidget(preview, 1)

        foot = QWidget()
        foot.setObjectName("Toolbar")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(20, 10, 20, 10)
        fl.setSpacing(10)
        self.summary = QLabel("")
        self.summary.setObjectName("Muted")
        fl.addWidget(self.summary)
        fl.addStretch(1)
        cancel = _btn("取消", "Ghost")
        cancel.clicked.connect(self.reject)
        self.start_btn = _btn("开始合并", "Primary")
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._start)
        fl.addWidget(cancel)
        fl.addWidget(self.start_btn)
        root.addWidget(foot)

        b = self.session.book
        self.master_label.setText(b.metadata.get("title") or os.path.basename(b.path))
        self.master_meta.setText("EPUB %s · %s · %s 章 · 目录来自 %s"
                                 % (b.version, _size_text(b.path),
                                    CF.count_nodes(self.session.toc)["docs"],
                                    b.toc_source))

    # -- 书籍列表 --------------------------------------------------------
    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "选择要合并的 EPUB", "", "EPUB (*.epub)")
        for p in paths:
            if os.path.abspath(p) == os.path.abspath(self.session.book.path):
                continue
            if p in self.books:
                continue
            self.books.append(p)
        self._refresh_list()

    def _remove(self) -> None:
        r = self.list.currentRow()
        if 0 <= r < len(self.books):
            self.books.pop(r)
            self._refresh_list()

    def _move(self, delta: int) -> None:
        r = self.list.currentRow()
        t = r + delta
        if 0 <= r < len(self.books) and 0 <= t < len(self.books):
            self.books[r], self.books[t] = self.books[t], self.books[r]
            self._refresh_list()
            self.list.setCurrentRow(t)

    def _refresh_list(self) -> None:
        self.list.clear()
        for i, p in enumerate(self.books, start=1):
            title, meta = os.path.basename(p), ""
            try:
                info = CF.EpubBook(p).info()
                title = info["title"] or title
                meta = "EPUB %s · %s · %d 章 · 目录来自 %s" % (
                    info["version"], _size_text(p), info["counts"]["docs"], info["toc_source"])
            except Exception as exc:
                meta = "无法读取：%s" % exc
            item = QListWidgetItem()
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 4, 8, 4)
            h.setSpacing(10)
            badge = QLabel("b%d" % i)
            badge.setObjectName("Badge")
            badge.setFixedWidth(34)
            col = QVBoxLayout()
            col.setSpacing(1)
            t = QLabel(title)
            t.setStyleSheet("font-weight:600;")
            m = QLabel(meta)
            m.setObjectName("Muted")
            col.addWidget(t)
            col.addWidget(m)
            h.addWidget(badge)
            h.addLayout(col, 1)
            item.setSizeHint(row.sizeHint() + _size(0, 8))
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
        self.count_badge.setText("%d 本" % len(self.books))
        self.start_btn.setEnabled(bool(self.books))
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        if not self.books:
            self.preview.setPlainText("尚未添加书籍。")
            self.summary.setText("")
            return
        t = CF.count_nodes(self.session.toc)
        lines = ["%s  ·  %d 章（原位）" % (self.master_label.text(), t["docs"])]
        total = t["docs"]
        for i, p in enumerate(self.books, start=1):
            try:
                b = CF.EpubBook(p)
                n = CF.count_nodes(b.toc)["docs"]
                total += n
                lines.append("└ %s  ·  %d 章（merged/b%d/）"
                             % (b.metadata.get("title") or os.path.basename(p), n, i))
            except Exception:
                lines.append("└ %s  ·  无法读取" % os.path.basename(p))
        self.preview.setPlainText("\n".join(lines))
        self.summary.setText("合并预览：主书 %d 章 + %d 本共 %d 章 → 约 %d 章"
                             % (t["docs"], len(self.books), total - t["docs"], total))

    def _start(self) -> None:
        self.result = {
            "books": list(self.books),
            "group_each": self.cb_group.isChecked(),
            "spine": self.cb_spine.isChecked(),
            "modified": self.cb_modified.isChecked(),
        }
        self.accept()


def _size_text(path: str) -> str:
    try:
        n = os.path.getsize(path)
    except OSError:
        return "—"
    if n >= 1024 * 1024:
        return "%.1f MB" % (n / 1024 / 1024)
    if n >= 1024:
        return "%.0f KB" % (n / 1024)
    return "%d B" % n


def _size(w: int, h: int):
    from PySide6.QtCore import QSize
    return QSize(w, h)


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("关于 ChapFlow")
        self.setFixedSize(420, 300)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(8)
        title = QLabel("ChapFlow")
        f = QFont(FONTS["family"], 16)
        f.setBold(True)
        title.setFont(f)
        ver = QLabel("v%s · EPUB 目录编辑器" % CF.__version__)
        ver.setObjectName("Muted")
        desc = QLabel("改名、拖动排序、增删大标题、TXT / DOCX 追加、多本合并，\n"
                      "并同步 nav.xhtml / toc.ncx / spine，导出前自检。")
        desc.setWordWrap(True)
        tech = QLabel("PySide6 · Python %d.%d · 引擎零第三方依赖"
                      % (__import__("sys").version_info.major,
                         __import__("sys").version_info.minor))
        tech.setObjectName("Muted")
        lay.addWidget(title)
        lay.addWidget(ver)
        lay.addSpacing(6)
        lay.addWidget(desc)
        lay.addStretch(1)
        lay.addWidget(tech)
        ok = _btn("关闭", "Primary")
        ok.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(ok)
        lay.addLayout(row)
