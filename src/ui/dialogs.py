# -*- coding: utf-8 -*-
"""对话框：追加 TXT/DOCX 章节、格式转换、合并书籍、自检报告、关于"""

from __future__ import annotations

import os
from datetime import datetime
from typing import List, Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                               QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QRadioButton, QScrollArea, QSizePolicy, QSpinBox,
                               QVBoxLayout, QWidget)

from . import icons
from .tokens import COLORS, FONTS, METRICS
from .widgets import Card, CoverView, SplitRulePanel

PARA_TIPS = {
    "blank": "空行才分段，段内的换行会合并成一段。适合英文书、论文等排版过的文本。",
    "line": "每一个换行都算一段（不合并）。适合中文小说 TXT，通常一行就是一段。",
    "indent": "行首有两个空格或缩进就视为新段落，没有缩进算上一段的续行。适合段首空两格、段间无空行的排版。",
}

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
# 追加 TXT / DOCX 章节
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
        self.setWindowTitle("追加 %s 章节" % ("DOCX" if kind == "docx" else "TXT"))
        self.setMinimumSize(900, 820)
        self.setModal(True)
        self._build()
        self._load_preview()

    # -- UI --------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(DialogHeader(
            "追加 %s 章节" % ("DOCX" if self.kind == "docx" else "TXT"),
            "识别标题层级并切分；追加后写入目录与阅读顺序，原书其余内容不变"))

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
        opt.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.rule_panel = SplitRulePanel()
        self.rule_panel.changed.connect(self._load_preview)
        opt.body().addWidget(self.rule_panel)

        self.para_block = QWidget()
        pl = QVBoxLayout(self.para_block)
        pl.setContentsMargins(0, 4, 0, 0)
        pl.setSpacing(6)
        pl.addWidget(self._label("段落模式"))
        prow = QHBoxLayout()
        prow.setSpacing(10)
        self.para_btns = {}
        for key, text in self.PARAS.items():
            rb = QRadioButton(text)
            rb.setChecked(key == "line")
            rb.setToolTip(PARA_TIPS.get(key, ""))
            self.para_btns[key] = rb
            prow.addWidget(rb)
        prow.addStretch(1)
        pl.addLayout(prow)
        opt.body().addWidget(self.para_block)

        self.docx_row = QWidget()
        dl = QHBoxLayout(self.docx_row)
        dl.setContentsMargins(0, 4, 0, 0)
        dl.setSpacing(8)
        dl.addWidget(self._label("按几级标题切章"))
        self.level_spin = QSpinBox()
        self.level_spin.setRange(1, 4)
        self.level_spin.setValue(1)
        self.level_spin.setPrefix("标题 ")
        dl.addWidget(self.level_spin)
        dl.addStretch(1)
        opt.body().addWidget(self.docx_row)

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
        self.preview_list.setMinimumHeight(150)
        self.preview_list.setSelectionMode(QAbstractItemView.NoSelection)
        prev.add(self.preview_list)
        lay.addWidget(prev, 1)

        # 插入位置
        pos = QHBoxLayout()
        pos.setSpacing(8)
        pos.addWidget(self._label("插入位置"))
        self.pos_combo = QComboBox()
        self.pos_combo.addItem("目录末尾", "end")
        self.pos_combo.addItem("新建大标题「追加章节」", "group")
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
        ok = _btn("追加", "Primary")
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

    def _opts(self) -> dict:
        para = next((k for k, b in self.para_btns.items() if b.isChecked()), "line")
        enc = None
        if self.cb_encoding.isChecked():
            enc = self.enc_combo.currentText().lower().replace("-", "")
            enc = {"utf8": "utf-8", "utf16": "utf-16"}.get(enc, enc)
        return {"rules": self.rule_panel.rules(), "para": para,
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
                self.rule_panel.setVisible(False)
            else:
                text, enc = TX.read_text_file(self.path, o["encoding"])
                chapters_raw, stats = TX.split_chapters(
                    text, os.path.splitext(os.path.basename(self.path))[0],
                    para_mode=o["para"], drop_page_numbers=o["drop_pn"],
                    rules=o["rules"])
                chapters = [(c.title, c.char_count) for c in chapters_raw]
                self.file_meta.setText("%s · 自动识别编码 %s · 换行已归一化"
                                       % (_size_text(self.path), enc.upper()))
                self.rule_panel.setVisible(True)
                self.docx_row.setVisible(False)
                self.count_badge.setToolTip("识别到 %d 个标题" % stats.get("headings", 0))
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
        ReportDialog(self.session, info, title="追加预览（未落盘）", parent=self).exec()

    def _apply(self, dry_run: bool):
        o = self._opts()
        s = self.session
        before_undo = s.undo_depth
        try:
            if self.kind == "docx":
                info = s.append_docx(self.path, group_title="追加章节" if
                                     self.pos_combo.currentData() == "group" else None,
                                     at="end" if self.pos_combo.currentData() in ("end", "group")
                                     else str(self.pos_combo.currentData()),
                                     split_level=o["level"], link_css=o["css"])
            else:
                info = s.append_txt(self.path, group_title="追加章节" if
                                    self.pos_combo.currentData() == "group" else None,
                                    at="end" if self.pos_combo.currentData() in ("end", "group")
                                    else str(self.pos_combo.currentData()),
                                    para_mode=o["para"],
                                    encoding=o["encoding"], link_css=o["css"],
                                    drop_page_numbers=o["drop_pn"], rules=o["rules"])
            if dry_run:
                rep = s.export(os.devnull, dry_run=True)
            else:
                rep = {"issues": []}
            return {"append": info, "export": rep}
        except Exception as exc:
            QMessageBox.warning(self, "追加失败", str(exc))
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
                 parent=None, repair_plan=None, upgrade_plan=None):
        super().__init__(parent)
        self.session = session
        self.payload = payload
        self.repair_plan = repair_plan or []
        self.upgrade_plan = upgrade_plan or {}
        self.repaired = False
        self.upgrade_requested = False
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

        if self.upgrade_plan.get("needed"):
            up_card = Card("可以升级到 EPUB 3")
            badge = QLabel("%s → %s" % (self.upgrade_plan.get("from"),
                                        self.upgrade_plan.get("to")))
            badge.setObjectName("Badge")
            up_card.add_header_widget(badge)
            for line in self.upgrade_plan.get("items", []):
                row = QWidget()
                h = QHBoxLayout(row)
                h.setContentsMargins(0, 0, 0, 0)
                h.setSpacing(8)
                ic = QLabel()
                ic.setPixmap(icons.icon("check-circle", COLORS["accent"], 15).pixmap(15, 15))
                ic.setFixedWidth(18)
                txt = QLabel(line)
                txt.setWordWrap(True)
                h.addWidget(ic, 0, Qt.AlignTop)
                h.addWidget(txt, 1)
                up_card.add(row)
            il.insertWidget(0, up_card)   # 放在最前，进去就能看到

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
        if self.upgrade_plan.get("needed"):
            up = _btn("升级为 EPUB 3…", "Primary")
            up.setToolTip("另存为 EPUB3 并自动打开；当前文件保持不变")
            up.clicked.connect(self._do_upgrade)
            fl.addWidget(up)
        if self.repair_plan:
            total = sum(p["count"] for p in self.repair_plan)
            fix = _btn("修复 %d 项并重新检查" % total,
                       "" if self.upgrade_plan.get("needed") else "Primary")
            fix.setToolTip("移除指向缺失文件的目录条目、清理空大标题；修复后可撤销")
            fix.clicked.connect(self._do_repair)
            fl.addWidget(fix)
        root.addWidget(foot)

    def _do_upgrade(self) -> None:
        self.upgrade_requested = True
        self.accept()

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


# =========================================================================
# 从 TXT / DOCX 新建一本书
# =========================================================================

class NewBookDialog(QDialog):
    """把 TXT / DOCX 转换成新的 EPUB3（不依附于已打开的书）"""

    SPLITS = {"auto": "自动", "heading": "按标题", "size": "按字数", "none": "整段"}
    PARAS = {"line": "每行一段", "blank": "空行分段", "indent": "缩进分段"}

    def __init__(self, parent=None, path: str = ""):
        super().__init__(parent)
        self.path = path
        self.kind = "docx" if path.lower().endswith(".docx") else "txt"
        self.created_path = ""
        self.report = None
        self.setWindowTitle("格式转换")
        self.setMinimumSize(860, 820)
        self.setModal(True)
        self._build()
        if self.path:
            self._load_preview()

    # -- UI --------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(DialogHeader("格式转换 · TXT / DOCX → EPUB",
                                    "选择文件 → 确认书名与切分方式 → 生成一本全新的 EPUB"))

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(22, 16, 22, 16)
        lay.setSpacing(12)
        root.addWidget(body, 1)

        # 文件
        file_card = Card()
        fl = QHBoxLayout()
        fl.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(icons.icon("doc", COLORS["accent"], 26).pixmap(26, 26))
        fl.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.file_name = QLabel("尚未选择文件")
        self.file_name.setStyleSheet("font-weight:600;")
        self.file_meta = QLabel("支持 .txt 与 .docx")
        self.file_meta.setObjectName("Muted")
        col.addWidget(self.file_name)
        col.addWidget(self.file_meta)
        fl.addLayout(col, 1)
        self.btn_pick = _btn("选择文件…", "Primary")
        self.btn_pick.clicked.connect(self._choose_file)
        fl.addWidget(self.btn_pick)
        file_card.body().addLayout(fl)
        lay.addWidget(file_card)

        # 元数据
        meta = Card("书名与作者")
        grid = QHBoxLayout()
        grid.setSpacing(12)
        v1 = QVBoxLayout()
        v1.setSpacing(5)
        v1.addWidget(self._label("书名"))
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("默认取文件名")
        v1.addWidget(self.title_edit)
        grid.addLayout(v1, 2)
        v2 = QVBoxLayout()
        v2.setSpacing(5)
        v2.addWidget(self._label("作者"))
        self.author_edit = QLineEdit()
        self.author_edit.setPlaceholderText("可留空")
        v2.addWidget(self.author_edit)
        grid.addLayout(v2, 1)
        v3 = QVBoxLayout()
        v3.setSpacing(5)
        v3.addWidget(self._label("语言"))
        self.lang_edit = QLineEdit("zh")
        self.lang_edit.setMaximumWidth(90)
        v3.addWidget(self.lang_edit)
        grid.addLayout(v3, 0)
        meta.body().addLayout(grid)
        lay.addWidget(meta)

        # 切分
        opt = Card("切分方式")
        opt.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.rule_panel = SplitRulePanel()
        self.rule_panel.changed.connect(self._load_preview)
        opt.body().addWidget(self.rule_panel)

        self.para_row = QWidget()
        pr = QHBoxLayout(self.para_row)
        pr.setContentsMargins(0, 0, 0, 0)
        pr.setSpacing(14)
        pr.addWidget(self._label("段落"))
        self.para_btns = {}
        for key, text in self.PARAS.items():
            rb = QRadioButton(text)
            rb.setChecked(key == "line")
            rb.setToolTip(PARA_TIPS.get(key, ""))
            self.para_btns[key] = rb
            pr.addWidget(rb)
        pr.addStretch(1)
        self.cb_pagenum = QCheckBox("丢弃纯页码行")
        pr.addWidget(self.cb_pagenum)
        self.enc_combo = QComboBox()
        self.enc_combo.addItem("自动识别编码", "")
        for e in ("UTF-8", "GB18030", "GBK", "BIG5", "UTF-16"):
            self.enc_combo.addItem(e, e.lower().replace("-", ""))
        pr.addWidget(self.enc_combo)
        opt.body().addWidget(self.para_row)

        self.docx_row = QWidget()
        dr = QHBoxLayout(self.docx_row)
        dr.setContentsMargins(0, 0, 0, 0)
        dr.setSpacing(10)
        dr.addWidget(self._label("按几级标题切章"))
        self.level_spin = QSpinBox()
        self.level_spin.setRange(1, 4)
        self.level_spin.setValue(1)
        self.level_spin.setPrefix("标题 ")
        dr.addWidget(self.level_spin)
        dr.addStretch(1)
        opt.body().addWidget(self.docx_row)
        lay.addWidget(opt)

        # 预览
        prev = Card("章节预览")
        self.count_badge = QLabel("—")
        self.count_badge.setObjectName("Badge")
        prev.add_header_widget(self.count_badge)
        self.preview_list = QListWidget()
        self.preview_list.setMinimumHeight(120)
        self.preview_list.setSelectionMode(QAbstractItemView.NoSelection)
        prev.add(self.preview_list)
        lay.addWidget(prev, 1)

        foot = QWidget()
        foot.setObjectName("Toolbar")
        fl2 = QHBoxLayout(foot)
        fl2.setContentsMargins(22, 10, 22, 10)
        fl2.setSpacing(10)
        self.hint = QLabel("生成后可继续在 ChapFlow 里调整目录")
        self.hint.setObjectName("Muted")
        fl2.addWidget(self.hint)
        fl2.addStretch(1)
        cancel = _btn("取消", "Ghost")
        cancel.clicked.connect(self.reject)
        self.ok_btn = _btn("转换并保存…", "Primary")
        self.ok_btn.setEnabled(False)
        self.ok_btn.clicked.connect(self._generate)
        fl2.addWidget(cancel)
        fl2.addWidget(self.ok_btn)
        root.addWidget(foot)

    def _label(self, text: str) -> QLabel:
        lb = QLabel(text)
        lb.setObjectName("FieldLabel")
        return lb

    # -- 数据 ------------------------------------------------------------
    def _opts(self) -> dict:
        para = next((k for k, b in self.para_btns.items() if b.isChecked()), "line")
        return {"rules": self.rule_panel.rules(), "para": para,
                "level": self.level_spin.value(),
                "encoding": self.enc_combo.currentData() or None,
                "drop_pn": self.cb_pagenum.isChecked()}

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择要转换的文件", self.path, "文本或文档 (*.txt *.docx)")
        if not path:
            return
        self.path = path
        self.kind = "docx" if path.lower().endswith(".docx") else "txt"
        self._load_preview()

    def _load_preview(self) -> None:
        self.preview_list.clear()
        self.file_name.setText(os.path.basename(self.path))
        self.btn_pick.setText("更换文件…")
        self.btn_pick.setObjectName("Ghost")
        self.btn_pick.style().unpolish(self.btn_pick)
        self.btn_pick.style().polish(self.btn_pick)
        o = self._opts()
        try:
            if self.kind == "docx":
                doc = DX.read_docx(self.path, split_level=o["level"])
                titles = [c.title for c in doc.chapters]
                self.file_meta.setText("%s · %d 章 · 图片 %d 张"
                                       % (_size_text(self.path), len(titles), len(doc.media)))
                self.title_edit.setText(doc.title or os.path.splitext(os.path.basename(self.path))[0])
                if doc.author:
                    self.author_edit.setText(doc.author)
                if doc.language:
                    self.lang_edit.setText(doc.language)
                self.rule_panel.setVisible(False)
                self.para_row.setVisible(False)
                self.docx_row.setVisible(True)
            else:
                text, enc = TX.read_text_file(self.path, o["encoding"])
                chapters, _stats = TX.split_chapters(
                    text, os.path.splitext(os.path.basename(self.path))[0],
                    para_mode=o["para"], drop_page_numbers=o["drop_pn"],
                    rules=o["rules"])
                titles = [c.title for c in chapters]
                self.file_meta.setText("%s · 编码 %s · 换行已归一化"
                                       % (_size_text(self.path), enc.upper()))
                if not self.title_edit.text().strip():
                    self.title_edit.setText(os.path.splitext(os.path.basename(self.path))[0])
                self.rule_panel.setVisible(True)
                self.para_row.setVisible(True)
                self.docx_row.setVisible(False)
        except Exception as exc:
            self.file_meta.setText("读取失败：%s" % exc)
            self.count_badge.setText("—")
            self.ok_btn.setEnabled(False)
            return

        self.ok_btn.setEnabled(bool(titles))
        self.count_badge.setText("%d 章" % len(titles))
        for i, title in enumerate(titles[:200], start=1):
            item = QListWidgetItem()
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 2, 8, 2)
            h.setSpacing(10)
            idx = QLabel("%02d" % i)
            idx.setObjectName("Muted")
            idx.setFixedWidth(26)
            t = QLabel(title)
            t.setToolTip(title)
            h.addWidget(idx)
            h.addWidget(t, 1)
            item.setSizeHint(QSize(120, 28))
            self.preview_list.addItem(item)
            self.preview_list.setItemWidget(item, row)

    def _generate(self) -> None:
        if not self.path:
            return
        base = os.path.splitext(os.path.basename(self.path))[0]
        out, _ = QFileDialog.getSaveFileName(
            self, "保存 EPUB", os.path.join(os.path.dirname(self.path), base + ".epub"),
            "EPUB (*.epub)")
        if not out:
            return
        o = self._opts()
        title = self.title_edit.text().strip() or base
        author = self.author_edit.text().strip()
        lang = self.lang_edit.text().strip() or "zh"
        try:
            if self.kind == "docx":
                rep = CF.docx_to_epub(self.path, out, title=title, author=author,
                                      language=lang, split_level=o["level"])
            else:
                rep = CF.txt_to_epub(self.path, out, title=title, author=author,
                                     language=lang, para_mode=o["para"],
                                     encoding=o["encoding"],
                                     drop_page_numbers=o["drop_pn"], rules=o["rules"])
        except Exception as exc:
            QMessageBox.warning(self, "生成失败", str(exc))
            return
        self.created_path = out
        self.report = rep
        self.accept()


# =========================================================================
# 章节编号：缺号检测与重新编号
# =========================================================================

class NumberingDialog(QDialog):
    """扫描章节编号，列出缺号 / 乱序，并可按目录顺序重排或补号"""

    STYLES = [
        ("auto", "自动（沿用书中现有写法）"),
        ("cn:章", "第一章（中文数字）"),
        ("digit:章", "第1章（阿拉伯数字）"),
        ("chapter", "Chapter 1"),
    ]

    def __init__(self, session, scope_path: str = "", parent=None):
        super().__init__(parent)
        self.session = session
        self.scope_path = scope_path
        self.applied = 0
        self.setWindowTitle("章节编号")
        self.setMinimumSize(880, 720)
        self.setModal(True)
        self._build()
        self._scan()

    # -- UI --------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(DialogHeader("章节编号",
                                    "检查编号是否连续；可只重排已有编号，也可给无编号章节补号"))

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(22, 14, 22, 14)
        lay.setSpacing(12)
        root.addWidget(body, 1)

        opt = Card("范围与规则")
        row1 = QHBoxLayout()
        row1.setSpacing(16)
        row1.addWidget(self._label("范围"))
        self.rb_all = QRadioButton("整本书")
        self.rb_scope = QRadioButton("当前大标题")
        self.rb_all.setChecked(not self.scope_path)
        self.rb_scope.setChecked(bool(self.scope_path))
        self.rb_scope.setEnabled(bool(self.scope_path))
        self.rb_all.toggled.connect(self._scan)
        self.rb_scope.toggled.connect(self._scan)
        row1.addWidget(self.rb_all)
        row1.addWidget(self.rb_scope)
        row1.addStretch(1)
        self.cb_add = QCheckBox("给没有编号的章节补号")
        self.cb_add.setChecked(True)
        self.cb_add.setToolTip("序章、前言、附录、番外等特殊章节会跳过，既不占号也不加号")
        self.cb_add.toggled.connect(self._scan)
        row1.addWidget(self.cb_add)
        opt.body().addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(16)
        row2.addWidget(self._label("编号风格"))
        self.style_combo = QComboBox()
        for key, text in self.STYLES:
            self.style_combo.addItem(text, key)
        self.style_combo.currentIndexChanged.connect(self._scan)
        row2.addWidget(self.style_combo, 1)
        opt.body().addLayout(row2)
        lay.addWidget(opt)

        summary = Card("扫描结果")
        self.summary = QLabel("—")
        self.summary.setWordWrap(True)
        summary.add(self.summary)
        self.issues = QLabel("")
        self.issues.setWordWrap(True)
        summary.add(self.issues)
        lay.addWidget(summary)

        prev = Card("重新编号预览")
        self.badge = QLabel("—")
        self.badge.setObjectName("Badge")
        prev.add_header_widget(self.badge)
        self.list = QListWidget()
        self.list.setMinimumHeight(180)
        self.list.setSelectionMode(QAbstractItemView.NoSelection)
        prev.add(self.list)
        lay.addWidget(prev, 1)

        note = QLabel("只改目录标题（nav / NCX）；正文首个标题是否同步，取决于主界面「正文页的标题也一起改」选项。"
                      "正文里的交叉引用（如“见第 3 章”）不会被改动。")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        lay.addWidget(note)

        foot = QWidget()
        foot.setObjectName("Toolbar")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(22, 10, 22, 10)
        fl.setSpacing(10)
        self.hint = QLabel("")
        self.hint.setObjectName("Muted")
        fl.addWidget(self.hint)
        fl.addStretch(1)
        close = _btn("关闭", "Ghost")
        close.clicked.connect(self.reject)
        self.apply_btn = _btn("重新编号", "Primary")
        self.apply_btn.clicked.connect(self._apply)
        fl.addWidget(close)
        fl.addWidget(self.apply_btn)
        root.addWidget(foot)

    def _label(self, text: str) -> QLabel:
        lb = QLabel(text)
        lb.setObjectName("FieldLabel")
        return lb

    # -- 逻辑 ------------------------------------------------------------
    def _style_arg(self):
        key = self.style_combo.currentData()
        if key == "auto":
            return None
        if key == "chapter":
            return {"kind": "chapter", "prefix": "Chapter"}
        kind, unit = key.split(":")
        return {"kind": kind, "unit": unit}

    def _scan(self) -> None:
        scope = self.scope_path if self.rb_scope.isChecked() else "all"
        try:
            plan = self.session.numbering_plan(scope, add_missing=self.cb_add.isChecked(),
                                               style=self._style_arg())
        except Exception as exc:
            self.summary.setText("扫描失败：%s" % exc)
            self.apply_btn.setEnabled(False)
            return
        self.plan = plan
        if plan.get("error"):
            self.summary.setText(plan["error"])
            self.apply_btn.setEnabled(False)
            return

        g = plan["groups"][0] if plan["groups"] else {}
        self.summary.setText(
            "范围：%s · 共 %d 章（带编号 %d，无编号 %d，特殊章节 %d）· 编号风格：%s"
            % (plan["scope"], plan["total_docs"], plan["numbered_docs"],
               plan["plain_docs"], plan["special_docs"], plan["style_label"]))

        parts = []
        for gap in g.get("gaps", []):
            parts.append("缺号：第 %s 章（在「%s」与「%s」之间）"
                         % ("、".join(str(n) for n in gap["missing"]),
                            gap["after_title"], gap["before_title"]))
        for bad in g.get("disorder", []):
            parts.append("乱序：「%s」编号 %d，却排在 %d 之后"
                         % (bad["title"], bad["cur"], bad["prev"]))
        for dup in g.get("duplicates", []):
            parts.append("重复：编号 %d 出现多次（如「%s」）" % (dup["num"], dup["title"]))
        if g.get("special"):
            parts.append("跳过（特殊章节）：%s" % "、".join("「%s」" % t for t in g["special"]))
        if not parts:
            parts.append("未发现缺号、乱序或重复。")
        self.issues.setText("\n".join(parts))

        changes = plan["changes"]
        self.list.clear()
        self.badge.setText("%d 处变更" % len(changes))
        for i, it in enumerate(g.get("items", []), start=1):
            item = QListWidgetItem()
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 3, 8, 3)
            h.setSpacing(10)
            idx = QLabel("%02d" % i)
            idx.setObjectName("Muted")
            idx.setFixedWidth(26)
            old = QLabel(it["title"])
            old.setStyleSheet("color:%s;" % COLORS["text_secondary"])
            arrow = QLabel()
            arrow.setPixmap(icons.icon("chevron-right", COLORS["text_secondary"], 14).pixmap(14, 14))
            arrow.setFixedWidth(20)
            arrow.setAlignment(Qt.AlignCenter)
            new = QLabel(it["new_title"])
            new.setStyleSheet("color:%s; font-weight:600;" % COLORS["text_primary"])
            h.addWidget(idx)
            h.addWidget(old, 1)
            h.addWidget(arrow)
            h.addWidget(new, 1)
            if it.get("added"):
                tag = QLabel("补")
                tag.setObjectName("Badge")
                h.addWidget(tag)
            item.setSizeHint(QSize(120, 30))
            self.list.addItem(item)
            self.list.setItemWidget(item, row)

        self.apply_btn.setEnabled(bool(changes))
        self.hint.setText("可撤销（Ctrl+Z）" if changes else "无需修改")

    def _apply(self) -> None:
        scope = self.scope_path if self.rb_scope.isChecked() else "all"
        applied = self.session.renumber(scope, add_missing=self.cb_add.isChecked(),
                                        style=self._style_arg())
        self.applied = len(applied)
        self.accept()


# =========================================================================
# 元数据编辑
# =========================================================================

class MetadataDialog(QDialog):
    """编辑 OPF 元数据：书名/作者/语言/标识符/出版信息/标签/丛书/评分/封面"""

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.new_cover = b""
        self.new_cover_name = ""
        self.setWindowTitle("编辑元数据")
        self.setMinimumSize(880, 720)
        self.setModal(True)
        self._build()
        self._fill()

    # -- UI --------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(DialogHeader("编辑元数据",
                                    "修改 OPF 里的书名、作者、出版信息与封面；保存后写入导出结果"))

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(22, 14, 22, 14)
        lay.setSpacing(12)
        root.addWidget(body, 1)

        top = QHBoxLayout()
        top.setSpacing(12)

        info = Card("书籍信息")
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        self.fields = {}
        rows = [("书名", "title"), ("排序名", "sort_title"),
                ("作者", "creators"), ("排序作者", "sort_creator"),
                ("语言", "language"), ("标识符", "identifier"),
                ("出版方", "publisher"), ("出版日期", "date"),
                ("丛书", "series"), ("丛书编号", "series_index"),
                ("标签", "subjects"), ("评分", "rating")]
        for i, (label, key) in enumerate(rows):
            lb = QLabel(label)
            lb.setObjectName("FieldLabel")
            edit = QLineEdit()
            self.fields[key] = edit
            grid.addWidget(lb, i // 2, (i % 2) * 2)
            grid.addWidget(edit, i // 2, (i % 2) * 2 + 1)
        info.body().addLayout(grid)
        hint = QLabel("标签用顿号或逗号分隔；排序名用于阅读器按字母排序；评分 0–10（5 星=10）。")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        info.body().addWidget(hint)
        info.add_stretch(1)
        top.addWidget(info, 3)

        cover_card = Card("封面")
        self.cover_view = CoverView()
        self.cover_view.setFixedSize(120, 168)
        cover_card.body().addWidget(self.cover_view, 0, Qt.AlignHCenter)
        self.cover_meta = QLabel("当前封面")
        self.cover_meta.setObjectName("Muted")
        self.cover_meta.setWordWrap(True)
        self.cover_meta.setAlignment(Qt.AlignCenter)
        cover_card.body().addWidget(self.cover_meta)
        pick = _btn("选择图片…", "Ghost")
        pick.clicked.connect(self._pick_cover)
        cover_card.body().addWidget(pick)
        cover_card.add_stretch(1)
        top.addWidget(cover_card, 2)
        lay.addLayout(top)

        desc = Card("简介")
        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setMinimumHeight(120)
        self.desc_edit.setStyleSheet(
            "QPlainTextEdit { background:%s; border:1px solid %s; border-radius:8px; padding:8px; }"
            % (COLORS["surface_subtle"], COLORS["border"]))
        desc.add(self.desc_edit)
        lay.addWidget(desc, 1)

        foot = QWidget()
        foot.setObjectName("Toolbar")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(22, 10, 22, 10)
        fl.setSpacing(10)
        self.hint = QLabel("")
        self.hint.setObjectName("Muted")
        fl.addWidget(self.hint)
        fl.addStretch(1)
        cancel = _btn("取消", "Ghost")
        cancel.clicked.connect(self.reject)
        save = _btn("保存", "Primary")
        save.clicked.connect(self._save)
        fl.addWidget(cancel)
        fl.addWidget(save)
        root.addWidget(foot)

    def _fill(self) -> None:
        meta = self.session.metadata()
        for key, edit in self.fields.items():
            value = meta.get(key, "")
            edit.setText(value)
            edit.setCursorPosition(0)   # 长值（如 UUID）从头显示
            edit.setToolTip(value)
        self.desc_edit.setPlainText(meta.get("description", ""))
        cover = self.session.book.cover()
        if cover:
            self.cover_view.set_image(cover[0])
            self.cover_meta.setText("当前封面（%s）" % cover[1].split("/")[-1])
        else:
            self.cover_view.set_image(None)
            self.cover_meta.setText("没有封面")
            self.cover_meta.setText("没有封面，可选择一个图片")

    def _pick_cover(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "选择封面图片", "",
                                              "图片 (*.jpg *.jpeg *.png *.gif *.webp)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as exc:
            QMessageBox.warning(self, "读取失败", str(exc))
            return
        self.new_cover = data
        self.new_cover_name = os.path.basename(path)
        self.cover_view.set_image(data)
        self.cover_meta.setText("将替换为：%s" % self.new_cover_name)
        self.hint.setText("保存后封面会写入导出结果")

    def _save(self) -> None:
        values = {k: e.text().strip() for k, e in self.fields.items()}
        values["description"] = self.desc_edit.toPlainText().strip()
        try:
            self.session.update_metadata(values, cover_bytes=self.new_cover or None,
                                         cover_name=self.new_cover_name)
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.accept()
