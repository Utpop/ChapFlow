# -*- coding: utf-8 -*-
"""对话框：追加 TXT/DOCX 章节、格式转换、合并书籍、校验报告、关于"""

from __future__ import annotations

import os
from datetime import datetime
from typing import List, Optional

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                               QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QRadioButton, QScrollArea, QSizePolicy, QSpinBox,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import icons
from . import jobs
from .tokens import COLORS, FONTS, METRICS
from .widgets import (Card, ChapterPreviewList, ChoiceChips, CountBlock, CoverView,
                      ElideLabel, SplitRulePanel, StatRow)

PARA_TIPS = {
    "blank": "仅按空行分段，段内换行合并为一段。适用于英文书籍、论文等已排版文本。",
    "line": "每个换行视为一段（不合并）。适用于中文小说 TXT，通常一行即为一段。",
    "indent": "行首含两个空格或缩进即视为新段落，无缩进则视为上一段的续行。适用于段首空两格、段间无空行的排版。",
}

import chapflow as CF
import docxconv as DX
import txtconv as TX


def _basis_row(label, style_rb, text_rb, label_cls) -> QHBoxLayout:
    """DOCX 的「章节依据」单选行：按标题样式 / 按正文文本规则"""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(14)
    row.addWidget(label_cls(label))
    row.addWidget(style_rb)
    row.addWidget(text_rb)
    row.addStretch(1)
    return row


def _llm_note(stats) -> str:
    """把切分统计里的 llm 结果说成一句短话（失败时点明已回退）"""
    info = (stats or {}).get("llm") if isinstance(stats, dict) else None
    if not info:
        return ""
    try:
        import llmseg  # type: ignore
        text = llmseg.summarize(info)
    except Exception:  # pragma: no cover - 统计结构异常不该影响界面
        return ""
    return ("LLM " + text) if text else ""


def _llm_tooltip(stats) -> str:
    note = _llm_note(stats)
    return ("\nLLM 复核：%s" % note) if note else ""


def absorb_tiny_scroll(dlg: QDialog, tolerance: int = 40, rounds: int = 3) -> None:
    """把「只差几个像素」的滚动条吃掉

    内容刚好比窗口高两三像素时，右侧会挂一条拉不动的滚动条，看着像没做完。
    这里按滚动条的可滚动距离把窗口补高一点（屏幕装得下才补）。
    """
    if getattr(dlg, "_absorbing", False):
        return
    dlg._absorbing = True

    def tick(left: int) -> None:
        scroll = dlg.findChild(QScrollArea)
        if scroll is None or left <= 0:
            dlg._absorbing = False
            return
        over = scroll.verticalScrollBar().maximum()
        if over <= 0 or over > tolerance:
            dlg._absorbing = False
            return
        scr = dlg.screen()
        avail = scr.availableGeometry().height() if scr is not None else dlg.height() + over
        if dlg.height() + over + 2 > avail:
            dlg._absorbing = False
            return
        dlg.resize(dlg.width(), dlg.height() + over + 2)
        lay = dlg.layout()
        if lay is not None:
            lay.activate()
        QTimer.singleShot(0, lambda: tick(left - 1))

    QTimer.singleShot(0, lambda: tick(rounds))


def _fit_to_content(dlg: QDialog, body: QWidget, min_w: int = 860,
                    min_h: int = 640, min_width_hard: int = 0) -> None:
    """按内容铺开窗口：装得下就不出滚动条，屏幕小才滚动。

    滚动区的 sizeHint 不含内容高度，所以要自己把 body 的期望高度加上页眉页脚。
    """
    lay = dlg.layout()
    chrome = 0
    if lay is not None:
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            if isinstance(w, QScrollArea):
                continue
            if w is not None:
                chrome += max(w.sizeHint().height(), w.minimumSizeHint().height())
        chrome += lay.contentsMargins().top() + lay.contentsMargins().bottom()
    want_h = body.sizeHint().height() + chrome + 8
    screen = dlg.screen()
    max_h = int(screen.availableGeometry().height() * 0.9) if screen else 980
    dlg.setMinimumSize(min_width_hard or min_w, min_h)   # 允许窗口收窄，标签才会换行
    dlg.resize(max(min_w, min(body.sizeHint().width() + 44, 1200)),
               max(min_h, min(want_h, max_h)))
    absorb_tiny_scroll(dlg)


def _scrollable(body: QWidget) -> QScrollArea:
    """把内容装进滚动区：窗口再小也只是滚动，不会把各行的控件挤到一起"""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setWidget(body)
    scroll.viewport().setAutoFillBackground(False)
    return scroll


def clamp_to_screen(dlg: "QDialog", margin: int = 56) -> None:
    """把对话框限制在屏幕可用范围内

    内容再多（几百条变更、几百行校验问题）也只出滚动条，
    不会把窗口撑到比屏幕还高——那样顶部会被系统裁掉，底部的按钮也点不到。
    """
    scr = dlg.screen()
    if scr is None:
        from PySide6.QtWidgets import QApplication
        scr = QApplication.primaryScreen()
    if scr is None:                                   # pragma: no cover
        return
    g = scr.availableGeometry()
    max_w = max(420, g.width() - margin)
    max_h = max(360, g.height() - margin)
    mn = dlg.minimumSize()
    dlg.setMinimumSize(min(mn.width(), max_w), min(mn.height(), max_h))
    dlg.setMaximumSize(max_w, max_h)
    # 内容想要的尺寸落在屏幕内就照办：比最小尺寸高一点，列表能多露几行
    hint = dlg.sizeHint()
    want_w = min(max_w, max(dlg.width(), hint.width()))
    want_h = min(max_h, max(dlg.height(), hint.height()))
    want_h = max(want_h, min(mn.height(), max_h))
    if want_w != dlg.width() or want_h != dlg.height():
        dlg.resize(want_w, want_h)


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

class LLMSettingsDialog(QDialog):
    """LLM 辅助切分的接入配置：地址 / 模型 / 密钥 / 超时重试 / 复核范围"""

    PRESETS = [
        ("DeepSeek", "https://api.deepseek.com", "deepseek-chat"),
        ("OpenAI 兼容", "https://api.openai.com/v1", "gpt-4o-mini"),
        ("本地 Ollama", "http://127.0.0.1:11434", "qwen2.5:7b"),
    ]
    SCOPES = [
        ("auto", "自动：本地没认出标题时才让它找漏判"),
        ("refine", "只复核：仅确认/否决本地已认出的标题"),
        ("augment", "扩展：同时寻找漏判的短标题"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LLM 辅助切分设置")
        self.setMinimumWidth(520)
        import llmseg  # type: ignore
        self._llmseg = llmseg
        self.cfg = llmseg.LLMOptions.load()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(DialogHeader(
            "LLM 辅助切分",
            "只复核候选标题行，正文不经模型；调用失败自动回退本地规则。"))

        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(22, 16, 22, 8)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)

        self.cb_enabled = QCheckBox("启用 LLM 辅助切分")
        self.cb_enabled.setChecked(bool(self.cfg.enabled))
        grid.addWidget(self.cb_enabled, 0, 0, 1, 2)

        row = 1
        grid.addWidget(self._label("预设"), row, 0)
        preset_row = QHBoxLayout()
        preset_row.setSpacing(6)
        for text, base, model in self.PRESETS:
            b = _btn(text, "Ghost")
            b.clicked.connect(lambda _=False, a=base, m=model: self._apply_preset(a, m))
            preset_row.addWidget(b)
        preset_row.addStretch(1)
        grid.addLayout(preset_row, row, 1)

        row += 1
        grid.addWidget(self._label("服务地址"), row, 0)
        self.base_edit = QLineEdit(self.cfg.base_url)
        self.base_edit.setPlaceholderText("https://api.deepseek.com")
        grid.addWidget(self.base_edit, row, 1)

        row += 1
        grid.addWidget(self._label("模型"), row, 0)
        self.model_edit = QLineEdit(self.cfg.model)
        self.model_edit.setPlaceholderText("deepseek-chat / qwen-plus / qwen2.5:7b")
        grid.addWidget(self.model_edit, row, 1)

        row += 1
        grid.addWidget(self._label("API Key"), row, 0)
        key_row = QHBoxLayout()
        key_row.setSpacing(6)
        self.key_edit = QLineEdit(self.cfg.api_key)
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("sk-…（本地 Ollama 可留空）")
        show = QCheckBox("显示")
        show.toggled.connect(lambda on: self.key_edit.setEchoMode(
            QLineEdit.Normal if on else QLineEdit.Password))
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(show)
        grid.addLayout(key_row, row, 1)

        row += 1
        grid.addWidget(self._label("超时 / 重试"), row, 0)
        num_row = QHBoxLayout()
        num_row.setSpacing(6)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 600)
        self.timeout_spin.setValue(int(self.cfg.timeout))
        self.timeout_spin.setSuffix(" 秒")
        self.retry_spin = QSpinBox()
        self.retry_spin.setRange(1, 6)
        self.retry_spin.setValue(int(self.cfg.max_retries))
        self.retry_spin.setPrefix("最多 ")
        self.retry_spin.setSuffix(" 次")
        self.batch_spin = QSpinBox()
        self.batch_spin.setRange(5, 200)
        self.batch_spin.setValue(int(self.cfg.batch))
        self.batch_spin.setPrefix("每批 ")
        self.batch_spin.setSuffix(" 条")
        for w in (self.timeout_spin, self.retry_spin, self.batch_spin):
            num_row.addWidget(w)
        num_row.addStretch(1)
        grid.addLayout(num_row, row, 1)

        row += 1
        grid.addWidget(self._label("复核范围"), row, 0)
        self.scope_combo = QComboBox()
        for key, text in self.SCOPES:
            self.scope_combo.addItem(text, key)
        idx = max(0, self.scope_combo.findData(self.cfg.scope))
        self.scope_combo.setCurrentIndex(idx)
        grid.addWidget(self.scope_combo, row, 1)

        row += 1
        grid.addWidget(self._label("失败处理"), row, 0)
        self.cb_fallback = QCheckBox("调用失败时回退本地规则（推荐；取消勾选则直接报错）")
        self.cb_fallback.setChecked(bool(self.cfg.fallback))
        grid.addWidget(self.cb_fallback, row, 1)

        row += 1
        self.test_label = QLabel("")
        self.test_label.setObjectName("Muted")
        self.test_label.setWordWrap(True)
        test_btn = _btn("测试连接")
        test_btn.clicked.connect(self._test)
        test_row = QHBoxLayout()
        test_row.setSpacing(8)
        test_row.addWidget(test_btn)
        test_row.addWidget(self.test_label, 1)
        grid.addLayout(test_row, row, 1)

        row += 1
        tip = QLabel("提示：密钥经系统加密（DPAPI）后保存在 %s，只在本机使用；命令行可用 "
                     "`chapflow.py llm --test` 验证同一份配置。" % self._llmseg.config_path())
        tip.setObjectName("Muted")
        tip.setWordWrap(True)
        grid.addWidget(tip, row, 0, 1, 2)

        root.addWidget(body, 1)

        foot = QWidget()
        foot.setObjectName("Toolbar")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(22, 10, 22, 10)
        fl.setSpacing(10)
        fl.addStretch(1)
        cancel = _btn("取消", "Ghost")
        cancel.clicked.connect(self.reject)
        save = _btn("保存", "Primary")
        save.clicked.connect(self._save)
        fl.addWidget(cancel)
        fl.addWidget(save)
        root.addWidget(foot)

    def _label(self, text: str) -> QLabel:
        lb = QLabel(text)
        lb.setObjectName("FieldLabel")
        return lb

    # -- 交互 ------------------------------------------------------------
    def _apply_preset(self, base: str, model: str) -> None:
        self.base_edit.setText(base)
        if not self.model_edit.text().strip():
            self.model_edit.setText(model)

    def _collect(self):
        cfg = self._llmseg.LLMOptions.load()
        cfg.enabled = self.cb_enabled.isChecked()
        cfg.base_url = self.base_edit.text().strip()
        cfg.model = self.model_edit.text().strip()
        cfg.api_key = self.key_edit.text().strip()
        cfg.timeout = self.timeout_spin.value()
        cfg.max_retries = self.retry_spin.value()
        cfg.batch = self.batch_spin.value()
        cfg.scope = self.scope_combo.currentData() or "auto"
        cfg.fallback = self.cb_fallback.isChecked()
        if "11434" in cfg.base_url:
            cfg.provider = "ollama"
        cfg = cfg.normalized()
        return cfg

    def _test(self) -> None:
        cfg = self._collect()
        cfg.enabled = True
        problem = cfg.problem()
        if problem:
            self.test_label.setText(problem)
            return
        self.test_label.setText("正在请求…")
        ok, result = jobs.run_blocking(
            self, lambda: self._llmseg.test_connection(cfg), "正在测试连接…")
        if not ok:
            self.test_label.setText("已取消测试")
            return
        self.test_label.setText(("✓ " if result["ok"] else "✗ ") + result["message"])

    def _save(self) -> None:
        cfg = self._collect()
        path = cfg.save()
        if not path:
            QMessageBox.warning(self, "保存失败", "配置文件写入失败，请检查目录权限。")
            return
        if cfg.enabled and cfg.problem():
            QMessageBox.information(
                self, "已保存（尚不可用）",
                "%s\n\n切分时会自动回退到本地规则。" % cfg.problem())
        self.cfg = cfg
        self.accept()

    def enabled(self) -> bool:
        return bool(getattr(self, "cfg", None) and self.cfg.enabled)


# =========================================================================
# 追加 TXT / DOCX 章节
# =========================================================================

# DOCX 的标题层级：给用户看的说法，别只写「标题 1」
TITLE_LEVELS = [
    (1, "1 级标题（多数书就是「第一章」）"),
    (2, "2 级标题（更细的小节，如 1.1）"),
    (3, "3 级标题"),
    (4, "4 级标题"),
    (0, "不切分（整篇作为一章）"),
]


class _PreviewZoomMixin:
    """将一张卡片（章节预览）放大至整窗：其余卡片收起，按 Esc 或再次点击还原。"""

    def _init_zoom(self, card: Card, hide: list) -> None:
        self._zoom_card = card
        self._zoom_hide = [w for w in hide if w is not None]
        self._zoomed = False
        self._size_before = None
        self.btn_zoom = QPushButton()
        self.btn_zoom.setObjectName("Ghost")
        self.btn_zoom.setFixedSize(30, 26)
        self.btn_zoom.setCursor(Qt.PointingHandCursor)
        self.btn_zoom.setIcon(icons.icon("expand", COLORS["text_secondary"], 16))
        self.btn_zoom.setToolTip("放大预览：把章节列表铺满整个窗口（Esc 还原）")
        self.btn_zoom.clicked.connect(self.toggle_zoom)
        card.add_header_widget(self.btn_zoom)

    def toggle_zoom(self) -> None:
        self._zoomed = not getattr(self, "_zoomed", False)
        for w in self._zoom_hide:
            w.setVisible(not self._zoomed)
        if self._zoomed:
            self._size_before = self.size()
            geo = self.screen().availableGeometry() if self.screen() else None
            if geo is not None:
                self.resize(int(geo.width() * 0.92), int(geo.height() * 0.92))
            self.btn_zoom.setIcon(icons.icon("collapse", COLORS["text_secondary"], 16))
            self.btn_zoom.setToolTip("还原（Esc）")
        else:
            if self._size_before is not None:
                self.resize(self._size_before)
            self.btn_zoom.setIcon(icons.icon("expand", COLORS["text_secondary"], 16))
            self.btn_zoom.setToolTip("放大预览：把章节列表铺满整个窗口（Esc 还原）")
        self.btn_zoom.clearFocus()

    def keyPressEvent(self, event):
        if getattr(self, "_zoomed", False) and event.key() == Qt.Key_Escape:
            self.toggle_zoom()
            return
        super().keyPressEvent(event)


class ImportDialog(_PreviewZoomMixin, QDialog):
    LABELS = {"auto": "自动", "heading": "按标题", "size": "按字数", "none": "整段"}
    PARAS = {"blank": "空行分段", "line": "每行一段", "indent": "缩进分段"}

    def __init__(self, session, path: str, kind: str, parent=None):
        super().__init__(parent)
        self.session = session
        self.kind = kind
        self.path = path
        self.result_info = None
        self.setWindowTitle("追加 %s 章节" % ("DOCX" if kind == "docx" else "TXT"))
        self.setModal(True)
        self._build()
        self._load_preview()

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_fitted", False):
            self._fitted = True
            _fit_to_content(self, self._body, 900, min_width_hard=620)

    # -- UI --------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(DialogHeader(
            "追加 %s 章节" % ("DOCX" if self.kind == "docx" else "TXT"),
            "识别标题层级并切分，追加后写入目录与阅读顺序；书籍其余内容不变"))

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(22, 16, 22, 16)
        lay.setSpacing(14)
        self._body = body
        root.addWidget(_scrollable(body), 1)

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
        self._card_file = file_card

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

        # DOCX：先选“章节依据”，再用下面同一套切分规则 / 标题层级
        self.rb_style = QRadioButton("按 Word 标题样式")
        self.rb_style.setToolTip("按文档里「标题 1 / 标题 2」的层级切章（默认）")
        self.rb_text = QRadioButton("按正文文本规则")
        self.rb_text.setToolTip("与 TXT 一致：使用下方的前缀、单位、正则或字数规则从正文中识别章节标题")
        self.rb_style.setChecked(True)
        for _rb in (self.rb_style, self.rb_text):
            _rb.toggled.connect(self._on_basis_changed)
        self.docx_basis = QWidget()
        _bl = _basis_row("章节依据", self.rb_style, self.rb_text, self._label)
        self.docx_basis.setLayout(_bl)
        opt.body().insertWidget(1, self.docx_basis)   # 0 是卡片标题行，插在它下面
        self.docx_basis.setVisible(False)

        self.docx_row = QWidget()
        dl = QHBoxLayout(self.docx_row)
        dl.setContentsMargins(0, 4, 0, 0)
        dl.setSpacing(8)
        dl.addWidget(self._label("每章从哪级标题开始"))
        self.level_combo = QComboBox()
        for _lv, _text in TITLE_LEVELS:
            self.level_combo.addItem(_text, _lv)
        self.level_combo.setToolTip(
            "Word 文档的段落带有「标题 1 / 标题 2」等层级。\n"
            "选 1 级：每个「标题 1」另起一章（多数小说采用此层级）；\n"
            "选 2 级：「标题 1」视为大标题（卷、部），仅「标题 2」切分为章。")
        dl.addWidget(self.level_combo, 1)
        dl.addStretch(1)
        opt.body().addWidget(self.docx_row)
        self.docx_row.setVisible(False)      # 选到 DOCX 才显示
        self._card_opt = opt

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
        self.cb_skip_odd = QCheckBox("跳过疑似混入正文的标题")
        self.cb_skip_odd.setChecked(False)
        self.cb_skip_odd.setToolTip(
            "勾选后：标题过长（超过 40 字）或带句末标点的章节会被自动跳过，\n"
            "不写入本次追加；仅影响本次结果，原文件不动。")
        self.cb_skip_odd.toggled.connect(lambda *_: self._sync_auto_skip())
        checks.addWidget(self.cb_pagenum)
        checks.addWidget(self.cb_css)
        checks.addWidget(self.cb_skip_odd)
        checks.addWidget(self.cb_encoding)
        checks.addWidget(self.enc_combo)
        checks.addStretch(1)
        opt.body().addLayout(checks)
        lay.addWidget(opt)

        # 插入位置：位于切分与段落之下、章节预览之上；标签与三个选项同一行横向排列
        pos_card = Card("插入位置")
        pos_card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        pos_row = QHBoxLayout()
        pos_row.setSpacing(10)
        pos_row.addWidget(self._label("插入位置"))
        self.pos_chips = ChoiceChips(
            [("加到目录末尾", "end"),
             ("新建大标题「追加章节」", "group"),
             ("放到最前面（第 1 位）", "1")],
            value="end",
            tip="新章节在目录中的位置；位置非法时将给出提示且不写入")
        pos_row.addWidget(self.pos_chips, 1)
        pos_card.body().addLayout(pos_row)
        lay.addWidget(pos_card)

        # 预览
        prev = Card("章节预览")
        self.count_badge = QLabel("")
        self.count_badge.setObjectName("Badge")
        self.count_badge.setVisible(False)      # 有内容才显示章数气泡
        prev.add_header_widget(self.count_badge)
        self.btn_drop = QPushButton()
        self.btn_drop.setObjectName("IconButton")
        self.btn_drop.setFixedSize(26, 26)
        self.btn_drop.setIcon(icons.icon("trash", COLORS["text_secondary"], 15))
        self.btn_drop.setCursor(Qt.PointingHandCursor)
        self.btn_drop.setToolTip("删除识别有误的章节（Delete）；仅影响本次追加，不修改原文件")
        self.btn_drop.setEnabled(False)
        prev.add_header_widget(self.btn_drop)
        self._init_zoom(prev, [self._card_file, self._card_opt])
        self.preview_list = ChapterPreviewList()
        self.preview_list.setMinimumHeight(150)
        self.preview_list.changed.connect(self._refresh_preview_counts)
        self.preview_list.itemSelectionChanged.connect(self._refresh_preview_counts)
        self.btn_drop.clicked.connect(self.preview_list.delete_selected)
        prev.add(self.preview_list)
        lay.addWidget(prev, 1)

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
        preview = _btn("预览（不写入）")
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

    def _basis(self) -> str:
        """DOCX 的切分依据：style（标题样式）/ text（正文文本规则）"""
        return "text" if self.rb_text.isChecked() else "style"

    def _on_basis_changed(self, *_args) -> None:
        if self.kind != "docx":
            return
        text_mode = self._basis() == "text"
        self.rule_panel.setVisible(text_mode)
        self.docx_row.setVisible(not text_mode)
        self._load_preview()

    def _opts(self) -> dict:
        para = next((k for k, b in self.para_btns.items() if b.isChecked()), "line")
        enc = None
        if self.cb_encoding.isChecked():
            enc = self.enc_combo.currentText().lower().replace("-", "")
            enc = {"utf8": "utf-8", "utf16": "utf-16"}.get(enc, enc)
        return {"rules": self.rule_panel.rules(), "para": para,
                "encoding": enc, "drop_pn": self.cb_pagenum.isChecked(),
                "css": self.cb_css.isChecked(), "level": self.level_combo.currentData(),
                "llm": self.rule_panel.llm_options(), "basis": self._basis()}

    def _load_preview(self) -> None:
        self.preview_list.clear()
        o = self._opts()
        try:
            if self.kind == "docx":
                text_mode = self._basis() == "text"
                doc = DX.read_docx(self.path, split_level=o["level"],
                                   rules=o["rules"] if text_mode else None)
                chapters = [(c.title, len(c.blocks)) for c in doc.chapters]
                split_note = {"pattern": " · 无标题样式，按正文「第X章」切分",
                              "rules": " · 按正文文本规则切分",
                              "none": " · 未识别到章节标题"}.get(doc.split_source, "")
                self.file_meta.setText("%s · %d 章 · 图片 %d 张%s"
                                       % (_size_text(self.path), len(chapters), len(doc.media),
                                          split_note))
                self.docx_basis.setVisible(True)
                self.docx_row.setVisible(not text_mode)
                self.rule_panel.setVisible(text_mode)     # DOCX 也能用 TXT 那套规则
                self.para_block.setVisible(False)
                self.cb_pagenum.setEnabled(False)
                self.cb_encoding.setEnabled(False)
                self.enc_combo.setEnabled(False)
            else:
                text, enc = TX.read_text_file(self.path, o["encoding"])
                chapters_raw, stats = self._split_text(text, o)
                chapters = [(c.title, c.char_count) for c in chapters_raw]
                self.file_meta.setText("%s · 自动识别编码 %s · 换行已归一化"
                                       % (_size_text(self.path), enc.upper()))
                self.rule_panel.setVisible(True)
                self.docx_basis.setVisible(False)
                self.docx_row.setVisible(False)
                self.count_badge.setToolTip("识别到 %d 个标题%s"
                                            % (stats.get("headings", 0),
                                               _llm_tooltip(stats)))
                self._llm_note = _llm_note(stats)
        except Exception as exc:
            self.file_meta.setText("读取失败：%s" % exc)
            self.count_badge.setText("")
            self.count_badge.setVisible(False)
            self.ok_btn.setEnabled(False)
            return

        self.preview_list.set_chapters(chapters)     # 重新切分 → 清空之前的删除记录
        self._sync_auto_skip()
        self._refresh_preview_counts()

    def _sync_auto_skip(self) -> None:
        """按「跳过疑似混入正文的标题」开关，标记要自动跳过的章节"""
        if not hasattr(self, "preview_list") or not hasattr(self, "cb_skip_odd"):
            return
        if self.cb_skip_odd.isChecked():
            idx = [i for i, (t, _m) in enumerate(self.preview_list.all_chapters())
                   if not _looks_like_title(t)]
        else:
            idx = []
        self.preview_list.set_auto_skip(idx)
        if hasattr(self, "summary"):
            self._refresh_preview_counts()

    def _refresh_preview_counts(self) -> None:
        """删除/恢复/换选后同步：章数徽标、底部摘要与按钮可用性"""
        left = self.preview_list.remaining_count()
        gone = self.preview_list.deleted_count()
        auto = self.preview_list.auto_count()
        self.count_badge.setText("%d 章" % left if left else "")
        self.count_badge.setVisible(left > 0)
        self.count_badge.setToolTip(("已跳过 %d 章，仅影响本次追加" % gone) if gone else "")
        self.btn_drop.setEnabled(bool(self.preview_list.selectedIndexes()))
        self.ok_btn.setEnabled(left > 0)
        note = getattr(self, "_llm_note", "")
        extra = ("（已跳过 %d 章%s）" % (gone, "，其中 %d 章疑似正文" % auto if auto else "")
                 ) if gone else ""
        self.summary.setText("共 %d 章%s%s" % (left, extra, (" · " + note) if note else ""))

    def _split_text(self, text: str, o: dict):
        """切分预览。LLM 启用时联网放在工作线程执行，界面可取消。"""
        base = os.path.splitext(os.path.basename(self.path))[0]
        llm = o.get("llm")
        if llm is None:
            return TX.split_chapters(text, base, para_mode=o["para"],
                                     drop_page_numbers=o["drop_pn"], rules=o["rules"])
        from PySide6.QtWidgets import QApplication
        self.file_meta.setText("正在请求 LLM 复核标题，请稍候…")
        self.summary.setText("LLM 正在识别…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            return TX.split_chapters(text, base, para_mode=o["para"],
                                     drop_page_numbers=o["drop_pn"],
                                     rules=o["rules"], llm=llm)
        finally:
            QApplication.restoreOverrideCursor()

    def _preview(self) -> None:
        """以试运行方式导出，不写入磁盘，直接展示校验结果"""
        info = self._apply(dry_run=True)
        if info is None:
            return
        ReportDialog(self.session, info, title="追加预览（未写入）", parent=self).exec()

    def _apply(self, dry_run: bool):
        o = self._opts()
        s = self.session
        before_undo = s.undo_depth
        try:
            if self.kind == "docx":
                info = s.append_docx(self.path, group_title="追加章节" if
                                     self.pos_chips.value() == "group" else None,
                                     at="end" if self.pos_chips.value() in ("end", "group")
                                     else str(self.pos_chips.value()),
                                     split_level=o["level"], link_css=o["css"],
                                     skip=self.preview_list.deleted_indices(),
                                     rules=o["rules"] if o["basis"] == "text" else None)
            else:
                info = s.append_txt(self.path, group_title="追加章节" if
                                    self.pos_chips.value() == "group" else None,
                                    at="end" if self.pos_chips.value() in ("end", "group")
                                    else str(self.pos_chips.value()),
                                    para_mode=o["para"],
                                    encoding=o["encoding"], link_css=o["css"],
                                    drop_page_numbers=o["drop_pn"], rules=o["rules"],
                                    llm=o.get("llm"),
                                    skip=self.preview_list.deleted_indices())
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


class AddChapterDialog(QDialog):
    """直接输入正文新增章节：不经过 TXT / DOCX 文件，提交即写入目录"""

    PARAS = {"auto": "自动", "blank": "空行分段", "line": "每行一段"}
    PARA_TIPS = {
        "auto": "正文含空行时按空行分段，否则每行一段。",
        "blank": "一个空行分隔一个自然段，段内换行合并（适用于已排版的文本）。",
        "line": "每个换行视为一段（适用于中文小说，一行即一段）。",
    }

    def __init__(self, session, parent=None, selected_path: str = ""):
        super().__init__(parent)
        self.session = session
        self.selected_path = selected_path or ""
        self.result_info = None
        self.setWindowTitle("新建章节")
        self.setMinimumSize(520, 520)      # 可收窄：插入位置标签会自动换行
        self.resize(900, 740)
        self.setModal(True)
        self._build()
        self._sync_state()
        clamp_to_screen(self)

    # -- UI --------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(DialogHeader(
            "新建章节",
            "直接输入正文，确认后作为新章节写入目录与阅读顺序；书籍原有内容保持不变"))

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(22, 16, 22, 16)
        lay.setSpacing(12)
        root.addWidget(body, 1)

        meta = Card("章节信息")
        meta.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        # 插入位置放最上面：平铺标签，所有可选位置一眼看全
        pos_opts = [("加到目录末尾", "end")]
        if self.selected_path:
            pos_opts.append(("接在所选目录项之后", "after"))
        pos_opts.append(("放到最前面（第 1 位）", "1"))
        pos_row = QHBoxLayout()
        pos_row.setSpacing(10)
        pos_row.addWidget(self._label("插入位置"))
        self.pos_chips = ChoiceChips(
            pos_opts, value="after" if self.selected_path else "end",
            tip="新章节在目录里的位置；插入位置非法时会给出提示且不写入")
        pos_row.addWidget(self.pos_chips, 1)
        meta.body().addLayout(pos_row)

        trow = QHBoxLayout()
        trow.setSpacing(10)
        trow.addWidget(self._label("标题"))
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("留空将自动命名（新章节 / 新章节 2 …）")
        self.title_edit.setMaxLength(120)
        self.title_edit.setClearButtonEnabled(True)
        trow.addWidget(self.title_edit, 1)
        meta.body().addLayout(trow)

        parow = QHBoxLayout()
        parow.setSpacing(16)
        parow.addWidget(self._label("分段方式"))
        self.para_btns = {}
        for key, text in self.PARAS.items():
            rb = QRadioButton(text)
            rb.setChecked(key == "auto")
            rb.setToolTip(self.PARA_TIPS.get(key, ""))
            rb.toggled.connect(self._sync_state)
            self.para_btns[key] = rb
            parow.addWidget(rb)
        parow.addStretch(1)
        meta.body().addLayout(parow)
        lay.addWidget(meta)

        text_card = Card("正文")
        self.counter = QLabel("0 字 · 0 段")
        self.counter.setObjectName("Badge")
        text_card.add_header_widget(self.counter)
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText(
            "在此输入或粘贴章节正文…\n\n"
            "空行分段：一个空行分隔一个自然段；如需一行一段，可修改上方的「分段方式」。\n"
            "Ctrl + Enter 可直接提交。")
        self.text_edit.setMinimumHeight(220)
        self.text_edit.setStyleSheet(
            "QPlainTextEdit { background:%s; border:1px solid %s; border-radius:8px; padding:10px; }"
            % (COLORS["surface_subtle"], COLORS["border"]))
        self.text_edit.textChanged.connect(self._sync_state)
        text_card.add(self.text_edit)
        lay.addWidget(text_card, 1)

        foot = QWidget()
        foot.setObjectName("Toolbar")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(22, 10, 22, 10)
        fl.setSpacing(10)
        self.error = QLabel("")
        self.error.setStyleSheet("color:%s;" % COLORS["danger"])
        self.error.setWordWrap(True)
        fl.addWidget(self.error, 1)
        cancel = _btn("取消", "Ghost")
        cancel.clicked.connect(self.reject)
        self.ok_btn = _btn("新建", "Primary")
        self.ok_btn.clicked.connect(self._submit)
        fl.addWidget(cancel)
        fl.addWidget(self.ok_btn)
        root.addWidget(foot)

        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        shortcut.activated.connect(self._submit)
        self.text_edit.setFocus()

    def _label(self, text: str) -> QLabel:
        lb = QLabel(text)
        lb.setObjectName("FieldLabel")
        return lb

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_focused", False):
            self._focused = True
            self.text_edit.setFocus()    # 打开即能粘贴正文

    # -- 状态 ------------------------------------------------------------
    def _para_mode(self) -> str:
        return next((k for k, b in self.para_btns.items() if b.isChecked()), "auto")

    def _target(self) -> str:
        """把「选中条目之后」换算成 parse_target 能识别的序号"""
        data = self.pos_chips.value()
        if data != "after" or not self.selected_path:
            return data or "end"
        parts = self.selected_path.split(".")
        try:
            parts[-1] = str(int(parts[-1]) + 1)
        except ValueError:      # 路径异常时退回末尾，不阻断添加
            return "end"
        return ".".join(parts)

    def _sync_state(self, *_args) -> None:
        paragraphs, used = CF.typed_paragraphs(self.text_edit.toPlainText(), self._para_mode())
        chars = sum(len(p) for p in paragraphs)
        self.counter.setText("%d 字 · %d 段" % (chars, len(paragraphs)))
        self.counter.setToolTip("按「%s」解析为 %d 段" % (self.PARAS.get(used, used), len(paragraphs)))
        if chars and self.error.text().startswith("正文不能为空"):
            self.error.setText("")      # 用户补上正文后清掉旧提示

    # -- 提交 ------------------------------------------------------------
    def _submit(self) -> None:
        if not self.text_edit.toPlainText().strip():
            self.error.setText("正文不能为空：请输入或粘贴章节内容后再点击「新建」")
            self.text_edit.setFocus()
            return
        before = self.session.undo_depth
        try:
            info = self.session.add_chapter(self.title_edit.text(),
                                           self.text_edit.toPlainText(),
                                           at=self._target(), para_mode=self._para_mode())
        except Exception as exc:      # 位置非法 / 内容异常 / 写入失败
            while self.session.undo_depth > before:
                self.session.undo()
            self.error.setText("添加失败：%s" % exc)
            QMessageBox.warning(self, "新建章节失败", "%s" % exc)
            return
        self.result_info = info
        self.error.setText("")
        self.accept()


class ReportDialog(QDialog):
    """导出校验报告，可附带一键修复"""

    def __init__(self, session, payload: dict, title: str = "导出校验报告",
                 parent=None, repair_plan=None, upgrade_plan=None):
        super().__init__(parent)
        self.session = session
        self.payload = payload
        self.repair_plan = repair_plan or []
        self.upgrade_plan = upgrade_plan or {}
        self.repaired = False
        self.upgrade_requested = False
        self.setWindowTitle(title)
        self.setMinimumSize(820, 640)
        self.setModal(True)
        self._build()
        clamp_to_screen(self)

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
        hdr = DialogHeader("校验报告", "%s · %s" % (sub, datetime.now().strftime("%Y-%m-%d %H:%M")))
        root.addWidget(hdr)

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(12)
        root.addWidget(body, 1)

        stats = QHBoxLayout()
        stats.setSpacing(12)
        for icon_name, value, label, tone in (
                ("check-circle", len(ok), "通过", "ok"),
                ("alert", len(warn), "警告（可继续导出）", "warn"),
                ("close", len(err), "错误", "error")):
            stats.addWidget(CountBlock(icon_name, value, label, tone))
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
            il.addWidget(self._group("其他", rest))

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
            il.insertWidget(0, up_card)   # 放在最前，打开即可看到

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
        concl = "校验结论：%s" % ("通过（可导出）" if not err else "存在 %d 个错误，建议修复后再导出" % len(err))
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
            fix.setToolTip("移除指向缺失文件的目录项、清理空大标题；修复后可撤销")
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
    """合并多本 EPUB：左边主书与合并后的目录结构，右边待合并书籍、选项与检查结果"""

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.books: List[str] = []
        self.result = None
        self.rejected: List[tuple] = []      # [(书名, 原因)]
        self._meta: dict = {}                # 路径 → 解析结果（避免每次刷新都重读压缩包）
        self.setWindowTitle("合并多本 EPUB")
        self.setMinimumSize(940, 540)
        self.resize(1020, 618)
        self.setModal(True)
        self._build()
        self._refresh_list()
        clamp_to_screen(self)

    # -- UI --------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(DialogHeader(
            "合并多本 EPUB",
            "合并后的书名、作者与封面以主书为准；其他书籍内容原样保留，不会互相覆盖"))

        body = QWidget()
        h = QHBoxLayout(body)
        h.setContentsMargins(18, 14, 18, 14)
        h.setSpacing(10)
        self._body = body
        # 装进滚动区：屏幕再矮也只是出滚动条，不会把卡片压到互相重叠
        root.addWidget(_scrollable(body), 1)

        # ---------- 左列：主书 + 合并后的目录结构 ----------
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(10)
        left.setFixedWidth(324)

        master = Card("主书（元数据以此为准）")
        top = QHBoxLayout()
        top.setSpacing(12)
        self.cover = CoverView()
        self.cover.setFixedSize(84, 112)
        top.addWidget(self.cover, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(3)
        self.master_label = QLabel("—")
        f = QFont(FONTS["family"], 11)
        f.setBold(True)
        self.master_label.setFont(f)
        self.master_label.setWordWrap(True)
        self.master_meta = QLabel("—")
        self.master_meta.setObjectName("Muted")
        self.master_meta.setWordWrap(True)
        col.addWidget(self.master_label)
        col.addWidget(self.master_meta)
        col.addStretch(1)
        top.addLayout(col, 1)
        master.body().addLayout(top)
        note = QLabel("合并后的书名、作者与语言以主书为准；其他书籍内容原样保留")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        master.body().addWidget(note)
        lv.addWidget(master)

        tree_card = Card("合并后目录结构预览")
        self.preview = QTreeWidget()
        self.preview.setObjectName("MergeTree")
        self.preview.setHeaderHidden(True)
        self.preview.setRootIsDecorated(True)
        self.preview.setIndentation(14)
        self.preview.setMinimumHeight(120)
        self.preview.setFrameShape(QFrame.NoFrame)
        self.preview.setSelectionMode(QAbstractItemView.NoSelection)
        tree_card.add(self.preview)
        tree_hint = QLabel("若存在同名文件，将自动归入各自的 merged/bN/ 目录，不会互相覆盖")
        tree_hint.setObjectName("Muted")
        tree_hint.setWordWrap(True)
        tree_card.add(tree_hint)
        lv.addWidget(tree_card)      # 高度贴合内容，剩余空间留白（与设计稿一致）
        lv.addStretch(1)
        h.addWidget(left, 0)

        # ---------- 右列：待合并书籍 ----------
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(10)

        self.list_card = Card("待合并书籍")
        order_hint = QLabel("书籍按此顺序依次合并至主书")
        order_hint.setObjectName("Muted")
        self.list_card.add_header_widget(order_hint)

        self.list = QListWidget()
        self.list.setObjectName("MergeList")
        self.list.setMinimumHeight(96)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.itemSelectionChanged.connect(self._sync_tools)
        self.list_card.add(self.list)

        add = _btn("＋  添加 EPUB 文件")
        add.setObjectName("AddFile")
        add.setMinimumHeight(36)
        add.setToolTip("可多选；带版权保护（DRM）的书籍将被拒绝并说明原因")
        add.clicked.connect(self._add_files)
        self.list_card.add(add)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        tip = QLabel("选择一行后可调整顺序")
        tip.setObjectName("Muted")
        tools.addWidget(tip)
        tools.addStretch(1)
        self.btn_up = _btn("上移", "Ghost", "arrow-up")
        self.btn_down = _btn("下移", "Ghost", "arrow-down")
        self.btn_rm = _btn("移除", "Ghost", "trash")
        for b, slot in ((self.btn_up, lambda: self._move(-1)),
                        (self.btn_down, lambda: self._move(1)),
                        (self.btn_rm, self._remove)):
            b.clicked.connect(slot)
            b.setFixedHeight(28)
            tools.addWidget(b)
        self.list_card.body().addLayout(tools)
        # 拉伸给得比底部留白大得多：窗口拉高时先让列表长到「装下全部条目」，
        # 再多出来的高度才留白（1:1 会被 spacer 分走一半，列表永远长不满）
        rv.addWidget(self.list_card, 10)

        opt = Card("合并选项")
        orow = QHBoxLayout()
        orow.setSpacing(20)
        self.cb_group = QCheckBox("各书籍章节按书名分组")
        self.cb_group.setToolTip(
            "勾选：各书籍章节归入对应书名的大标题之下，目录层次清楚\n"
            "不勾选：所有章节直接排列在主书目录之后")
        self.cb_group.setChecked(True)
        self.cb_spine = QCheckBox("翻页顺序与目录保持一致")
        self.cb_spine.setToolTip(
            "建议勾选：阅读器中的章节顺序与目录一致\n"
            "不勾选：翻页顺序还是各书原来的，可能可能导致翻页顺序错乱")
        self.cb_spine.setChecked(True)
        self.cb_modified = QCheckBox("更新书籍的最近修改时间")
        self.cb_modified.setToolTip("把元数据里的修改时间改成合并的时间，更符合 EPUB3 规范")
        self.cb_modified.setChecked(True)
        for cb in (self.cb_group, self.cb_spine, self.cb_modified):
            orow.addWidget(cb)
        orow.addStretch(1)
        opt.body().addLayout(orow)
        self.spine_hint = QLabel("建议勾选：确保翻页顺序与目录一致，避免阅读中途出现顺序错乱")
        self.spine_hint.setObjectName("Muted")
        self.spine_hint.setWordWrap(True)
        opt.body().addWidget(self.spine_hint)
        rv.addWidget(opt)

        self.banner_box = QVBoxLayout()
        self.banner_box.setSpacing(8)
        rv.addLayout(self.banner_box)
        rv.addStretch(1)
        h.addWidget(right, 1)

        # ---------- 底部 ----------
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

        # 主书信息
        b = self.session.book
        self.cover.set_image(b.cover()[0] if b.cover() else None)
        self.master_label.setText(b.metadata.get("title") or os.path.basename(b.path))
        self.master_meta.setText("EPUB %s · %s\n目录 %s · %d 章"
                                 % (b.version, _size_text(b.path),
                                    b.toc_source, CF.count_nodes(self.session.toc)["docs"]))

    # -- 书籍信息（带缓存） ------------------------------------------------
    def _book_meta(self, path: str) -> dict:
        if path in self._meta:
            return self._meta[path]
        info = {"ok": False, "title": os.path.basename(path), "error": ""}
        try:
            book = CF.EpubBook(path)
            if "META-INF/encryption.xml" in book.infos:
                info["error"] = "含 DRM 加密条目，不支持合并"
            else:
                names = {os.path.basename(m["path"]) for m in book.manifest}
                cover = book.cover()
                info = {"ok": True, "title": book.metadata.get("title") or os.path.basename(path),
                        "version": book.version, "size": _size_text(path),
                        "docs": CF.count_nodes(book.toc)["docs"], "toc": book.toc_source,
                        "names": names, "cover": cover[0] if cover else None}
        except Exception as exc:      # 不是 EPUB / 文件损坏
            info["error"] = str(exc)
        self._meta[path] = info
        return info

    # -- 列表操作 --------------------------------------------------------
    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "选择要合并的 EPUB", "", "EPUB (*.epub)")
        self.rejected = []
        for p in paths:
            if os.path.abspath(p) == os.path.abspath(self.session.book.path):
                self.rejected.append((os.path.basename(p), "该文件即当前主书"))
                continue
            if p in self.books:
                continue
            meta = self._book_meta(p)
            if not meta.get("ok"):
                self.rejected.append((os.path.basename(p), meta.get("error") or "无法读取"))
                continue
            self.books.append(p)
        self._refresh_list()

    def _remove(self) -> None:
        r = self.list.currentRow()
        if 0 <= r < len(self.books):
            self.books.pop(r)
            self._refresh_list()
            self.list.setCurrentRow(min(r, len(self.books) - 1))

    def _move(self, delta: int) -> None:
        r = self.list.currentRow()
        t = r + delta
        if 0 <= r < len(self.books) and 0 <= t < len(self.books):
            self.books[r], self.books[t] = self.books[t], self.books[r]
            self._refresh_list()
            self.list.setCurrentRow(t)

    def _sync_tools(self) -> None:
        r = self.list.currentRow()
        has = 0 <= r < len(self.books)
        self.btn_up.setEnabled(has and r > 0)
        self.btn_down.setEnabled(has and r < len(self.books) - 1)
        self.btn_rm.setEnabled(has)

    # -- 刷新 ------------------------------------------------------------
    def _refresh_list(self) -> None:
        self.list.clear()
        for i, p in enumerate(self.books, start=1):
            meta = self._book_meta(p)
            item = QListWidgetItem()
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 6, 8, 6)
            h.setSpacing(10)
            badge = QLabel("b%d" % i)
            badge.setObjectName("Badge")
            badge.setFixedWidth(32)
            badge.setAlignment(Qt.AlignCenter)
            thumb = CoverView()
            thumb.setFixedSize(36, 50)
            thumb.set_image(meta.get("cover"))
            col = QVBoxLayout()
            col.setSpacing(2)
            t = QLabel(meta.get("title") or os.path.basename(p))
            t.setStyleSheet("font-weight:600;")
            m = QLabel("EPUB %s · %s · %d 章 · 目录来源 %s"
                       % (meta.get("version", "?"), meta.get("size", "—"),
                          meta.get("docs", 0), meta.get("toc", "—")))
            m.setObjectName("Muted")
            col.addWidget(t)
            col.addWidget(m)
            h.addWidget(badge)
            h.addWidget(thumb)
            h.addLayout(col, 1)
            item.setSizeHint(QSize(120, 64))
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
        # 固定高度会把卡片顶破：窗口不够高时列表伸到邻居身上（按钮被压、行重叠）。
        # 改成「下限可滚动 + 上限贴条目」，窗口变小只影响可见行数，不会压坏布局
        need = max(132, min(284, len(self.books) * 68 + 8))
        self.list.setMinimumHeight(132)
        self.list.setMaximumHeight(need)
        # 卡片本身也要能跟着长（156 ≈ 标题 + 添加按钮 + 工具行的高度），
        # 否则窗口拉高时列表仍停在旧高度，多余空间全落在底部空白
        self.list_card.setMaximumHeight(need + 156)
        self.list_card.title_label.setText("待合并书籍（%d）" % len(self.books))
        self._fit_minimum()
        QTimer.singleShot(0, self._fit_minimum)   # 布局生效后再校一次，高度才收得住
        absorb_tiny_scroll(self)                  # 只差几像素时把滚动条吃掉
        self.start_btn.setEnabled(bool(self.books))
        self._sync_tools()
        self._refresh_preview()
        self._refresh_banners()

    def _fit_minimum(self) -> None:
        """最小尺寸跟着内容走

        原来固定 940×540：书一多，内容最小高度到了 740，窗口却能压到 540，
        Qt 只能硬压 → 列表伸到按钮上、标题压到卡片边框上。
        """
        lay = self.layout()
        if lay is None:
            return
        need = lay.minimumSize()
        body = getattr(self, "_body", None)
        if body is not None and body.layout() is not None:
            chrome = self.height() - body.height()
            if chrome <= 0:                     # 还没显示过，用标题+底栏的固定高度估
                chrome = 126
            need = QSize(need.width(), body.layout().minimumSize().height() + chrome)
        avail = 900
        scr = self.screen()
        if scr is not None:
            avail = max(560, scr.availableGeometry().height() - 96)
        self.setMinimumSize(max(940, need.width()), min(max(560, need.height()), avail))
        # 加书之后若装不下全部条目，就按「差多少补多少」长高一点（上限＝屏幕可用高度）
        if getattr(self, "list", None) is not None:
            short = max(0, self.list.maximumHeight() - self.list.height())
            if short and self.height() + short <= avail:
                self.resize(max(self.width(), self.minimumWidth()), self.height() + short)

    def _refresh_preview(self) -> None:
        """合并后的目录结构：主书在上，各书的资源目录挂在下面"""
        self.preview.clear()
        t = CF.count_nodes(self.session.toc)
        total = t["docs"]
        root_item = QTreeWidgetItem(["%s · %d 章（原位）" % (self.master_label.text(), t["docs"])])
        root_item.setIcon(0, icons.icon("book", COLORS["accent"], 15))
        self.preview.addTopLevelItem(root_item)
        for i, p in enumerate(self.books, start=1):
            meta = self._book_meta(p)
            n = meta.get("docs", 0)
            total += n
            child = QTreeWidgetItem(["%s · %d 章（merged/b%d/）"
                                     % (meta.get("title") or os.path.basename(p), n, i)])
            child.setIcon(0, icons.icon("doc", COLORS["text_secondary"], 14))
            root_item.addChild(child)
        root_item.setExpanded(True)
        rows = 1 + len(self.books)          # 高度跟着条目走，避免空白太高
        need_h = min(280, max(96, rows * 26 + 18))
        self.preview.setMinimumHeight(96)
        self.preview.setMaximumHeight(need_h)
        if not self.books:
            empty = QTreeWidgetItem(["尚未添加待合并书籍"])
            empty.setForeground(0, QColor(COLORS["disabled"]))
            self.preview.addTopLevelItem(empty)
            self.summary.setText("合并预览：主书 %d 章" % t["docs"])
            return

        size_text = ""
        try:
            total_bytes = sum(os.path.getsize(x)
                              for x in [self.session.book.path] + self.books)
            size_text = (" · 预计输出 %.1f MB" % (total_bytes / 1024 / 1024)
                         if total_bytes >= 1024 * 1024
                         else " · 预计输出 %d KB" % max(1, round(total_bytes / 1024)))
        except OSError:
            size_text = ""
        self.summary.setText("合并预览：主书 %d 章 + %d 本共 %d 章 → 约 %d 章%s"
                             % (t["docs"], len(self.books), total - t["docs"], total, size_text))

    def _refresh_banners(self) -> None:
        while self.banner_box.count():
            it = self.banner_box.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        for name, reason in self.rejected:
            lb = QLabel("已拒绝 %s：%s" % (name, reason))
            lb.setObjectName("BannerError")
            lb.setWordWrap(True)
            self.banner_box.addWidget(lb)
        if self.books:
            seen: dict = {}
            for p in self.books:
                for nm in self._book_meta(p).get("names", ()):
                    seen[nm] = seen.get(nm, 0) + 1
            dup = sum(1 for n, c in seen.items() if c > 1)
            text = ("冲突检查通过：%d 处同名文件将按 merged/bN/ 隔离，链接无需改写" % dup
                    if dup else "冲突检查通过：没有同名文件冲突，资源整树搬入 merged/bN/")
            lb = QLabel(text)
            lb.setObjectName("BannerOk")
            lb.setWordWrap(True)
            self.banner_box.addWidget(lb)

    def _start(self) -> None:
        self.result = {
            "books": list(self.books),
            "group_each": self.cb_group.isChecked(),
            "spine": self.cb_spine.isChecked(),
            "modified": self.cb_modified.isChecked(),
        }
        self.accept()


def _looks_like_title(text: str, limit: int = 40) -> bool:
    """目录标题是否像正常的章节名：过长或带句末标点的，多半是混进了正文"""
    t = (text or "").strip()
    if not t or len(t) > limit:
        return False
    return not any(ch in t for ch in "。！？；!?;")


def _clip(text: str, limit: int = 18) -> str:
    """截断长串用于示例展示（只影响提示里的示例文字，不参与任何写入）"""
    t = (text or "").strip()
    return t if len(t) <= limit else t[:limit].rstrip() + "…"


def _bytes_text(size: int) -> str:
    """字节数 → 人类可读（封面这种几十 KB 的图用）"""
    if size >= 1024 * 1024:
        return "%.1f MB" % (size / 1024 / 1024)
    if size >= 1024:
        return "%.0f KB" % (size / 1024)
    return "%d B" % size


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
        desc = QLabel("支持重命名、拖动排序、增删大标题、追加 TXT / DOCX 章节与多书合并，\n"
                      "并同步 nav.xhtml、toc.ncx 与 spine，导出前执行校验。")
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

class NewBookDialog(_PreviewZoomMixin, QDialog):
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
        self.setModal(True)
        self._build()
        if self.path:
            self._load_preview()

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_fitted", False):
            self._fitted = True
            _fit_to_content(self, self._body)

    # -- UI --------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(DialogHeader("格式转换 · TXT / DOCX → EPUB",
                                    "选择文件 → 确认书名与切分方式 → 生成一本全新的 EPUB"))

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(22, 14, 22, 14)
        lay.setSpacing(10)
        self._body = body
        root.addWidget(_scrollable(body), 1)

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
        self._card_file = file_card

        # 元数据
        meta = Card("书名与作者")
        grid = QHBoxLayout()
        grid.setSpacing(12)
        v1 = QVBoxLayout()
        v1.setSpacing(5)
        v1.addWidget(self._label("书名"))
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("默认取自文件名")
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
        self._card_meta = meta

        # 切分
        opt = Card("切分方式")
        self._card_opt = opt
        # 纵向 Minimum：卡片的实际高度跟随内容（Fixed 会把分章面板压扁，行与行叠在一起）
        opt.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.rule_panel = SplitRulePanel()
        self.rule_panel.changed.connect(self._load_preview)
        opt.body().addWidget(self.rule_panel)

        self.rb_style = QRadioButton("按 Word 标题样式")
        self.rb_style.setToolTip("按文档里「标题 1 / 标题 2」的层级切章（默认）")
        self.rb_text = QRadioButton("按正文文本规则")
        self.rb_text.setToolTip("与 TXT 一致：使用下方的前缀、单位、正则或字数规则从正文中识别章节标题")
        self.rb_style.setChecked(True)
        for _rb in (self.rb_style, self.rb_text):
            _rb.toggled.connect(self._on_basis_changed)
        self.docx_basis = QWidget()
        self.docx_basis.setLayout(_basis_row("章节依据", self.rb_style, self.rb_text, self._label))
        opt.body().insertWidget(1, self.docx_basis)   # 0 是卡片标题行，插在它下面
        self.docx_basis.setVisible(False)

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

        # 独立一行：切分依据是「标题样式」时也可能出现超长标题（段落被设成标题样式）
        odd_row = QHBoxLayout()
        odd_row.setSpacing(10)
        self.cb_skip_odd = QCheckBox("跳过疑似混入正文的标题")
        self.cb_skip_odd.setChecked(False)
        self.cb_skip_odd.setToolTip(
            "勾选后：标题过长（超过 40 字）或带句末标点的章节会被自动跳过，\n"
            "不写入本次生成；仅影响本次结果，原文件不动。")
        self.cb_skip_odd.toggled.connect(lambda *_: self._sync_auto_skip())
        odd_row.addWidget(self.cb_skip_odd)
        odd_row.addStretch(1)

        self.docx_row = QWidget()
        dr = QHBoxLayout(self.docx_row)
        dr.setContentsMargins(0, 0, 0, 0)
        dr.setSpacing(10)
        dr.addWidget(self._label("每章从哪级标题开始"))
        self.level_combo = QComboBox()
        for _lv, _text in TITLE_LEVELS:
            self.level_combo.addItem(_text, _lv)
        self.level_combo.setToolTip(
            "Word 文档的段落带有「标题 1 / 标题 2」等层级。\n"
            "选 1 级：每个「标题 1」另起一章（多数小说采用此层级）；\n"
            "选 2 级：「标题 1」视为大标题（卷、部），仅「标题 2」切分为章。")
        dr.addWidget(self.level_combo, 1)
        dr.addStretch(1)
        opt.body().addWidget(self.docx_row)
        self.docx_row.setVisible(False)      # 选到 DOCX 才显示
        opt.body().addLayout(odd_row)        # 开关放最后一行，两种切分依据下都可见
        lay.addWidget(opt)

        # 预览
        prev = Card("章节预览")
        self.count_badge = QLabel("")
        self.count_badge.setObjectName("Badge")
        self.count_badge.setVisible(False)      # 有内容才显示章数气泡
        prev.add_header_widget(self.count_badge)
        self.btn_drop = QPushButton()
        self.btn_drop.setObjectName("IconButton")
        self.btn_drop.setFixedSize(26, 26)
        self.btn_drop.setIcon(icons.icon("trash", COLORS["text_secondary"], 15))
        self.btn_drop.setCursor(Qt.PointingHandCursor)
        self.btn_drop.setToolTip("删除识别有误的章节（Delete）；仅影响本次生成，不修改原文件")
        self.btn_drop.setEnabled(False)
        prev.add_header_widget(self.btn_drop)
        self._init_zoom(prev, [self._card_file, self._card_meta, self._card_opt])
        self.preview_list = ChapterPreviewList()
        self.preview_list.setMinimumHeight(120)
        self.preview_list.changed.connect(self._refresh_preview_counts)
        self.preview_list.itemSelectionChanged.connect(self._refresh_preview_counts)
        self.btn_drop.clicked.connect(self.preview_list.delete_selected)
        prev.add(self.preview_list)
        lay.addWidget(prev, 1)

        foot = QWidget()
        foot.setObjectName("Toolbar")
        fl2 = QHBoxLayout(foot)
        fl2.setContentsMargins(22, 10, 22, 10)
        fl2.setSpacing(10)
        self.hint = QLabel("生成后可在 ChapFlow 中继续调整目录")
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
                "level": self.level_combo.currentData(),
                "encoding": self.enc_combo.currentData() or None,
                "drop_pn": self.cb_pagenum.isChecked(),
                "llm": self.rule_panel.llm_options(), "basis": self._basis()}

    def _split_text(self, text: str, o: dict):
        """切分预览。LLM 启用时联网放在工作线程执行，界面可取消。"""
        base = os.path.splitext(os.path.basename(self.path))[0]
        llm = o.get("llm")
        if llm is None:
            return TX.split_chapters(text, base, para_mode=o["para"],
                                     drop_page_numbers=o["drop_pn"], rules=o["rules"])
        self.file_meta.setText("正在请求 LLM 复核标题，请稍候…")
        ok, res = jobs.run_blocking(
            self, lambda: TX.split_chapters(text, base, para_mode=o["para"],
                                            drop_page_numbers=o["drop_pn"],
                                            rules=o["rules"], llm=llm),
            "LLM 正在识别标题…")
        if ok:
            return res
        # 用户取消 → 回退本地规则，不让预览空着
        return TX.split_chapters(text, base, para_mode=o["para"],
                                 drop_page_numbers=o["drop_pn"], rules=o["rules"])

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
                text_mode = self._basis() == "text"
                doc = DX.read_docx(self.path, split_level=o["level"],
                                   rules=o["rules"] if text_mode else None)
                chapters = [(c.title, "") for c in doc.chapters]
                split_note = {"pattern": " · 无标题样式，按正文「第X章」切分",
                              "rules": " · 按正文文本规则切分",
                              "none": " · 未识别到章节标题"}.get(doc.split_source, "")
                self.file_meta.setText("%s · %d 章 · 图片 %d 张%s"
                                       % (_size_text(self.path), len(chapters), len(doc.media),
                                          split_note))
                self.title_edit.setText(doc.title or os.path.splitext(os.path.basename(self.path))[0])
                if doc.author:
                    self.author_edit.setText(doc.author)
                if doc.language:
                    self.lang_edit.setText(doc.language)
                self.docx_basis.setVisible(True)
                self.rule_panel.setVisible(text_mode)     # DOCX 也能用 TXT 那套规则
                self.para_row.setVisible(False)
                self.docx_row.setVisible(not text_mode)
            else:
                text, enc = TX.read_text_file(self.path, o["encoding"])
                chapters, stats = self._split_text(text, o)
                chapters = [(c.title, "") for c in chapters]
                self.file_meta.setText("%s · 编码 %s · 换行已归一化%s"
                                       % (_size_text(self.path), enc.upper(),
                                          (" · " + _llm_note(stats)) if _llm_note(stats) else ""))
                if not self.title_edit.text().strip():
                    self.title_edit.setText(os.path.splitext(os.path.basename(self.path))[0])
                self.docx_basis.setVisible(False)
                self.rule_panel.setVisible(True)
                self.para_row.setVisible(True)
                self.docx_row.setVisible(False)
        except Exception as exc:
            self.file_meta.setText("读取失败：%s" % exc)
            self.count_badge.setText("")
            self.count_badge.setVisible(False)
            self.ok_btn.setEnabled(False)
            return

        self.preview_list.set_chapters(chapters)     # 重新切分 → 清空之前的删除记录
        self._sync_auto_skip()
        self._refresh_preview_counts()

    def _sync_auto_skip(self) -> None:
        """按「跳过疑似混入正文的标题」开关，标记要自动跳过的章节"""
        if not hasattr(self, "preview_list") or not hasattr(self, "cb_skip_odd"):
            return
        if self.cb_skip_odd.isChecked():
            idx = [i for i, (t, _m) in enumerate(self.preview_list.all_chapters())
                   if not _looks_like_title(t)]
        else:
            idx = []
        self.preview_list.set_auto_skip(idx)
        if hasattr(self, "hint"):
            self._refresh_preview_counts()

    def _refresh_preview_counts(self) -> None:
        """删除/恢复/换选后同步：章数徽标、底部提示与按钮可用性"""
        left = self.preview_list.remaining_count()
        gone = self.preview_list.deleted_count()
        auto = self.preview_list.auto_count()
        self.count_badge.setText("%d 章" % left if left else "")
        self.count_badge.setVisible(left > 0)
        self.count_badge.setToolTip(("已跳过 %d 章，仅影响本次生成" % gone) if gone else "")
        self.btn_drop.setEnabled(bool(self.preview_list.selectedIndexes()))
        self.ok_btn.setEnabled(left > 0)
        if gone:
            extra = "（其中 %d 章疑似正文标题）" % auto if auto else ""
            self.hint.setText("已跳过 %d 章%s，仅影响本次生成，原文件不动" % (gone, extra))
        else:
            self.hint.setText("生成后可在 ChapFlow 中继续调整目录")

    def _basis(self) -> str:
        """DOCX 的切分依据：style（标题样式）/ text（正文文本规则）"""
        return "text" if self.rb_text.isChecked() else "style"

    def _on_basis_changed(self, *_args) -> None:
        if self.kind != "docx":
            return
        text_mode = self._basis() == "text"
        self.rule_panel.setVisible(text_mode)
        self.docx_row.setVisible(not text_mode)
        self._load_preview()

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
                                      language=lang, split_level=o["level"],
                                      skip=self.preview_list.deleted_indices(),
                                      rules=o["rules"] if o["basis"] == "text" else None)
            else:
                rep = CF.txt_to_epub(self.path, out, title=title, author=author,
                                     language=lang, para_mode=o["para"],
                                     encoding=o["encoding"],
                                     drop_page_numbers=o["drop_pn"], rules=o["rules"],
                                     llm=o.get("llm"),
                                     skip=self.preview_list.deleted_indices())
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
        self.tidied = 0
        self.scope_used = scope_path or "all"
        self.per_group_used = False
        self.setWindowTitle("章节编号")
        self.setMinimumSize(880, 600)
        self.setModal(True)
        self._build()
        self._scan()
        # 装得下就铺开（列表能多露几行），装不下再出滚动条
        _fit_to_content(self, self._body, 880, 600)
        clamp_to_screen(self)

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
        self._body = body
        # 章节几百章时扫描结果很长：正文装进滚动区，窗口再不会被撑出屏幕
        root.addWidget(_scrollable(body), 1)

        opt = Card("编号设置")
        row1 = QHBoxLayout()
        row1.setSpacing(16)
        row1.addWidget(self._label("方式"))
        self.rb_cont = QRadioButton("整书连续编号")
        self.rb_cont.setChecked(True)
        self.rb_cont.setToolTip("全书 1…N 一路数下去，跨大标题不重置")
        self.rb_group = QRadioButton("每个大标题独立编号")
        self.rb_group.setToolTip("各大标题分别自 1 编号，互不影响；书中没有大标题时等同于连续编号")
        for rb in (self.rb_cont, self.rb_group):
            rb.toggled.connect(lambda *_: self._scan())
            row1.addWidget(rb)
        groups = self._group_list()
        if not groups:
            self.rb_group.setEnabled(False)   # 没有卷，独立编号与连续编号没区别
        row1.addStretch(1)
        self.cb_add = QCheckBox("给没有编号的章节补号")
        self.cb_add.setChecked(True)
        self.cb_add.setToolTip("序章、前言、附录、番外等特殊章节会跳过，既不占用编号也不补充编号")
        self.cb_add.toggled.connect(self._scan)
        row1.addWidget(self.cb_add)
        opt.body().addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(16)
        row2.addWidget(self._label("风格"))
        self.style_combo = QComboBox()
        for key, text in self.STYLES:
            self.style_combo.addItem(text, key)
        self.style_combo.currentIndexChanged.connect(self._scan)
        self.style_combo.setMinimumWidth(260)
        row2.addWidget(self.style_combo)
        row2.addStretch(1)

        # 「只看某个大标题」是过滤条件，不是编号方式；只有书里真有卷时才出现
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("全部大标题", ("all", False))
        for path, group in groups:
            self.scope_combo.addItem("只看「%s」" % group.title, (path, False))
        if self.scope_path:
            idx = self.scope_combo.findData((self.scope_path, False))
            if idx >= 0:
                self.scope_combo.setCurrentIndex(idx)
        self.scope_combo.currentIndexChanged.connect(lambda *_: self._scan())
        self.scope_label = self._label("范围")
        row2.addWidget(self.scope_label)
        row2.addWidget(self.scope_combo)
        for w in (self.scope_label, self.scope_combo):
            w.setVisible(bool(groups))
        row2.addSpacing(12)
        self.cb_skip_odd = QCheckBox("跳过疑似混入正文的标题")
        self.cb_skip_odd.setToolTip(
            "勾选后：标题过长（超过 40 字）或带句末标点的目录项，不参与「规范章节名」，\n"
            "只在提示条里说明，避免把整段正文当成章节名处理。\n"
            "不勾选（默认）：所有目录标题一视同仁。")
        self.cb_skip_odd.setChecked(False)
        self.cb_skip_odd.toggled.connect(lambda *_: self._scan())
        row2.addWidget(self.cb_skip_odd)
        opt.body().addLayout(row2)
        lay.addWidget(opt)

        summary = Card("扫描结果")
        self.summary = QLabel("—")
        self.summary.setWordWrap(True)
        summary.add(self.summary)
        self.issues = QLabel("")
        self.issues.setWordWrap(True)
        summary.add(self.issues)

        # 「只规范章节名」原来是个角落灰按钮，既看不见也不知道干什么：
        # 挪进扫描结果里，配一句人话说明 + 带图标的按钮，有需要才出现
        self.tidy_box = QFrame()
        self.tidy_box.setObjectName("TidyBox")
        tb = QHBoxLayout(self.tidy_box)
        tb.setContentsMargins(12, 9, 10, 9)
        tb.setSpacing(10)
        tb_icon = QLabel()
        tb_icon.setPixmap(icons.icon("alert", COLORS["warning"], 16).pixmap(16, 16))
        tb_icon.setFixedSize(18, 22)        # 高度=正文行高，图标在行盒里居中，和首行文字对齐
        tb_icon.setAlignment(Qt.AlignCenter)
        self.tidy_text = QLabel("")
        self.tidy_text.setObjectName("TidyText")
        self.tidy_text.setWordWrap(True)
        self.tidy_btn = _btn("规范章节名")
        self.tidy_btn.setObjectName("TidyAction")
        self.tidy_btn.setIcon(icons.icon("refresh", COLORS["warning"], 14))
        self.tidy_btn.setCursor(Qt.PointingHandCursor)
        self.tidy_btn.clicked.connect(self._apply_tidy)
        tb.addWidget(tb_icon, 0, Qt.AlignTop)
        tb.addWidget(self.tidy_text, 1)
        tb.addWidget(self.tidy_btn, 0, Qt.AlignVCenter)
        self.tidy_box.setVisible(False)
        summary.add(self.tidy_box)
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
    def _scope_titles(self, scope: str):
        """范围内的全部目录标题（只读目录节点，不读正文）"""
        def walk(items):
            for n in items:
                yield n.title
                if n.children:
                    yield from walk(n.children)

        if not scope or scope in ("all", "root"):
            return list(walk(self.session.toc))
        try:
            node, _, _ = CF.locate(self.session.toc, scope)
        except Exception:
            return list(walk(self.session.toc))
        return [node.title] + list(walk(node.children))

    def _group_list(self):
        """列出书里所有大标题及其目录路径（含嵌套），供范围下拉使用"""
        out = []

        def walk(items, prefix=""):
            for i, n in enumerate(items, start=1):
                path = ("%s.%d" % (prefix, i)) if prefix else str(i)
                if getattr(n, "kind", "") == "group":
                    out.append((path, n))
                if getattr(n, "children", None):
                    walk(n.children, path)

        try:
            walk(self.session.toc)
        except Exception:  # pragma: no cover - 目录结构异常不该拖垮对话框
            return out
        return out

    def _scope(self) -> str:
        """检查范围：目录路径；未指定时返回 "all" """
        combo = getattr(self, "scope_combo", None)
        if combo is None or not combo.isVisibleTo(self):
            return "all"
        data = self.scope_combo.currentData()
        return (data[0] if isinstance(data, tuple) else data) or "all"

    def _per_group(self) -> bool:
        """选的是不是「每个大标题独立编号」"""
        return bool(getattr(self, "rb_group", None) and self.rb_group.isChecked())

    def _style_arg(self):
        key = self.style_combo.currentData()
        if key == "auto":
            return None
        if key == "chapter":
            return {"kind": "chapter", "prefix": "Chapter"}
        kind, unit = key.split(":")
        return {"kind": kind, "unit": unit}

    def _scan(self) -> None:
        scope = self._scope()
        per_group = self._per_group()
        try:
            plan = self.session.numbering_plan(scope, add_missing=self.cb_add.isChecked(),
                                               style=self._style_arg(),
                                               per_group=per_group)
        except Exception as exc:
            self.summary.setText("扫描失败：%s" % exc)
            self.apply_btn.setEnabled(False)
            return
        self.plan = plan
        if plan.get("error"):
            self.summary.setText(plan["error"])
            self.apply_btn.setEnabled(False)
            return

        groups = plan["groups"]
        self.summary.setText(
            "范围：%s · 共 %d 章（带编号 %d，无编号 %d，特殊章节 %d）· 编号风格：%s%s"
            % (plan["scope"], plan["total_docs"], plan["numbered_docs"],
               plan["plain_docs"], plan["special_docs"], plan["style_label"],
               "· 每个大标题独立编号" if plan.get("per_group") else ""))

        parts = []
        multi = len(groups) > 1
        for g in groups:
            prefix = "【%s】" % g["label"] if multi else ""
            for gap in g.get("gaps", []):
                parts.append("%s缺号：第 %s 章（在「%s」与「%s」之间）"
                             % (prefix, "、".join(str(n) for n in gap["missing"]),
                                gap["after_title"], gap["before_title"]))
            for bad in g.get("disorder", []):
                parts.append("%s乱序：「%s」编号 %d，却排在 %d 之后"
                             % (prefix, bad["title"], bad["cur"], bad["prev"]))
            for dup in g.get("duplicates", []):
                parts.append("%s重复：编号 %d 出现多次（如「%s」）"
                             % (prefix, dup["num"], dup["title"]))
        if multi:
            specials = [t for g in groups for t in g.get("special", [])]
            if specials:
                parts.append("跳过（特殊章节）：%s"
                             % "、".join("「%s」" % t for t in specials))
        if not parts:
            parts.append("未发现缺号、乱序或重复。")
        # 书一大，问题清单能到几十上百行，整段铺开会把窗口撑爆；
        # 只列前若干条并给出总数，重排/补号之后这些提示本来就会消失
        limit = 8
        if len(parts) > limit:
            parts = parts[:limit] + ["…另有 %d 条同类问题（重新编号后会一并消除）"
                                     % (len(parts) - limit)]
        self.issues.setText("\n".join(parts))

        changes = plan["changes"]
        self.list.clear()
        self.badge.setText("%d 处变更" % len(changes))
        for g in groups:
            if multi:
                # 段头用普通列表项而不是自绘控件：高度由列表自己算，不会被裁掉半行
                sep = QListWidgetItem(g["label"] or "未命名大标题")
                f = sep.font()
                f.setBold(True)
                f.setPointSizeF(max(9.0, f.pointSizeF()))
                sep.setFont(f)
                sep.setForeground(QColor(COLORS["text_primary"]))
                sep.setFlags(Qt.NoItemFlags)
                self.list.addItem(sep)
            for it in g.get("items", []):
                item = QListWidgetItem()
                row = QWidget()
                h = QHBoxLayout(row)
                h.setContentsMargins(8, 3, 8, 3)
                h.setSpacing(10)
                idx = QLabel("%02d" % it.get("num", 0))
                idx.setObjectName("Muted")
                idx.setFixedWidth(26)
                # 用 ElideLabel（宽度与文本长度无关）：两列等宽，箭头才能每行对齐
                old = ElideLabel(it["title"])
                old.setStyleSheet("color:%s;" % COLORS["text_secondary"])
                arrow = QLabel()
                arrow.setPixmap(icons.icon("chevron-right", COLORS["text_secondary"],
                                           14).pixmap(14, 14))
                arrow.setFixedWidth(20)
                arrow.setAlignment(Qt.AlignCenter)
                new = ElideLabel(it["new_title"])
                new.setStyleSheet("color:%s; font-weight:600;" % COLORS["text_primary"])
                h.addWidget(idx)
                h.addWidget(old, 1)
                h.addWidget(arrow)
                h.addWidget(new, 1)
                # 徽标位每行都占固定宽度：只有补号的行写「补」，其余留空，
                # 否则该行两列的拉伸空间被挤小，箭头和右列会跟别的行错开
                tag = QLabel("补" if it.get("added") else "")
                tag.setFixedWidth(34)
                tag.setAlignment(Qt.AlignCenter)
                if it.get("added"):
                    tag.setObjectName("Badge")
                h.addWidget(tag)
                item.setSizeHint(QSize(120, 30))
                self.list.addItem(item)
                self.list.setItemWidget(item, row)

        self.apply_btn.setEnabled(bool(changes))
        # 扫描范围只有目录条目本身（nav / ncx 的文本），不读正文；
        # 但有的书目录标题里混进了整段正文，这类“异常标题”不当作章节名来规范，
        # 单独提示让用户自己改名，避免把一大段正文当成章名展示和改写
        tidy_all = self.session.tidy_titles(scope, dry_run=True)
        skip_odd = self.cb_skip_odd.isChecked()
        if skip_odd:      # 可选过滤：只规范看起来像章节名的标题
            tidy_changes = [c for c in tidy_all if _looks_like_title(c["old"])]
            odd_n = sum(1 for _t in self._scope_titles(scope) if not _looks_like_title(_t))
            self._tidy_paths = [c["path"] for c in tidy_changes]
        else:             # 默认：所有目录标题一视同仁
            tidy_changes = tidy_all
            odd_n = 0
            self._tidy_paths = None
        tidy_n = len(tidy_changes)
        notes = []
        if tidy_n:
            sample = min(tidy_changes, key=lambda c: len(c["old"]))
            demo = "（如「%s」→「%s」）" % (_clip(sample["old"]), _clip(sample["new"]))
            notes.append("另有 %d 处章节名只差分隔符%s；规范后仅修改名称，编号与层级均不变，可撤销。"
                         % (tidy_n, demo))
        if odd_n:
            notes.append("已跳过 %d 个标题过长、疑似混入正文的目录项；"
                         "如需一并处理，请取消勾选「跳过疑似混入正文的标题」。" % odd_n)
        self.tidy_box.setVisible(bool(notes))
        self.tidy_btn.setEnabled(tidy_n > 0)
        self.tidy_btn.setVisible(tidy_n > 0)
        if notes:
            self.tidy_text.setText("".join(notes))
        if tidy_n:
            self.tidy_btn.setText("规范 %d 处" % tidy_n)
            self.tidy_btn.setToolTip("将标题中多余的句点或连字符替换为空格，编号保持不变")
        if changes:
            self.hint.setText("重新编号后可撤销（Ctrl+Z）")
        elif tidy_n:
            self.hint.setText("编号无需调整；上方的 %d 处章节名可选择整理" % tidy_n)
        elif odd_n:
            self.hint.setText("编号无需调整；已按开关跳过 %d 个疑似正文的标题" % odd_n)
        else:
            self.hint.setText("无需修改")

    def _apply(self) -> None:
        scope = self._scope()
        per_group = self._per_group()
        applied = self.session.renumber(scope, add_missing=self.cb_add.isChecked(),
                                        style=self._style_arg(), per_group=per_group)
        self.applied = len(applied)
        self.scope_used = scope
        self.per_group_used = per_group
        self.accept()

    def _apply_tidy(self) -> None:
        scope = self._scope()
        paths = getattr(self, "_tidy_paths", None)
        self.tidied = len(self.session.tidy_titles(scope, paths=paths))
        self.scope_used = scope
        if self.tidied:
            self.accept()


# =========================================================================
# 书籍信息 / 元数据（同一页面：默认只读，就地编辑）
# =========================================================================

META_FIELDS = [("书名", "title"), ("排序名", "sort_title"),
               ("作者", "creators"), ("排序作者", "sort_creator"),
               ("语言", "language"), ("标识符", "identifier"),
               ("出版方", "publisher"), ("出版日期", "date"),
               ("丛书", "series"), ("丛书编号", "series_index"),
               ("标签", "subjects"), ("评分", "rating")]


def _looks_isbn(text: str) -> bool:
    digits = "".join(ch for ch in (text or "") if ch.isdigit() or ch in "Xx")
    return len(digits) in (10, 13)


class BookInfoDialog(QDialog):
    """书籍信息 + 元数据：默认只读展示，底部「编辑」切到可编辑，按钮变「保存」。

    合并了原来的「书籍信息」与「编辑元数据」两个弹窗：同一份数据只在一个页面里看和改，
    省掉「先看统计、再另开一个窗口去改」的来回。
    保存成功后自动退出编辑模式、回到只读，并刷新页面上的标题与统计。
    """

    def __init__(self, session, parent=None, extras: Optional[dict] = None,
                 start_editing: bool = False):
        super().__init__(parent)
        self.session = session
        self.extras = extras or {}
        self.editing = False
        self.changed = False
        self.new_cover = b""
        self.new_cover_name = ""
        self.fields = {}
        self.stats = {}
        self.setWindowTitle("书籍信息")
        self.setMinimumSize(860, 640)
        self.resize(920, 920)
        self.setModal(True)
        self._build()
        self._fill()
        self._set_editing(bool(start_editing))

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_fitted", False):
            self._fitted = True
            _fit_to_content(self, self._body, 900, 640)

    # -- UI --------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(DialogHeader(
            "书籍信息",
            "封面、元数据与结构统计；默认只读，点击底部「编辑」即可就地修改并保存"))

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(22, 16, 22, 16)
        lay.setSpacing(12)
        self._body = body
        root.addWidget(_scrollable(body), 1)

        top = QHBoxLayout()
        top.setSpacing(12)

        base = Card("基本信息")
        row = QHBoxLayout()
        row.setSpacing(14)

        # 封面占一列：图 + 图注 + 换图按钮，说明文字贴着它描述的图片，不再漂在卡片底部
        cover_col = QVBoxLayout()
        cover_col.setSpacing(5)
        self.cover_view = CoverView()
        self.cover_view.setFixedSize(92, 128)
        cover_col.addWidget(self.cover_view, 0, Qt.AlignHCenter)
        self.cover_meta = QLabel("—")
        self.cover_meta.setObjectName("Muted")
        self.cover_meta.setWordWrap(True)
        self.cover_meta.setAlignment(Qt.AlignHCenter)
        self.cover_meta.setFixedWidth(104)
        self.cover_meta.setStyleSheet("font-size:9pt;")
        cover_col.addWidget(self.cover_meta, 0, Qt.AlignHCenter)
        self.btn_pick_cover = _btn("选择图片…", "Ghost")
        self.btn_pick_cover.clicked.connect(self._pick_cover)
        cover_col.addWidget(self.btn_pick_cover, 0, Qt.AlignHCenter)
        cover_col.addStretch(1)
        row.addLayout(cover_col, 0)

        col = QVBoxLayout()
        col.setSpacing(4)
        self.title_label = QLabel("—")
        f = QFont(FONTS["family"], 12)
        f.setBold(True)
        self.title_label.setFont(f)
        self.title_label.setWordWrap(True)
        self.meta_label = QLabel("—")
        self.meta_label.setWordWrap(True)
        self.tag_label = QLabel("—")
        self.tag_label.setObjectName("Muted")
        self.path_label = QLabel("—")
        self.path_label.setObjectName("Muted")
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        col.addWidget(self.title_label)
        col.addWidget(self.meta_label)
        col.addWidget(self.tag_label)
        col.addSpacing(4)
        col.addWidget(self.path_label)
        col.addStretch(1)
        row.addLayout(col, 1)
        base.body().addLayout(row)
        top.addWidget(base, 3)

        stat_card = Card("结构统计")
        for key, label in (("manifest", "书内文件数"), ("spine", "正文页数（顺序）"),
                           ("toc", "目录项"), ("warn", "打开告警"),
                           ("source", "目录来源"), ("file", "文件状态"),
                           ("export", "上次导出")):
            r = StatRow(label)
            self.stats[key] = r
            stat_card.body().addWidget(r)
        top.addWidget(stat_card, 2)
        lay.addLayout(top)

        meta = Card("元数据")
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)
        for i, (label, key) in enumerate(META_FIELDS):
            lb = QLabel(label)
            lb.setObjectName("FieldLabel")
            edit = QLineEdit()
            self.fields[key] = edit
            grid.addWidget(lb, i // 2, (i % 2) * 2)
            grid.addWidget(edit, i // 2, (i % 2) * 2 + 1)
        meta.body().addLayout(grid)
        hint = QLabel("标签用顿号或逗号分隔；排序名用于阅读器按字母排序；评分 0–10（5 星 = 10）。")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        meta.body().addWidget(hint)
        lay.addWidget(meta)

        desc = Card("简介")
        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setFixedHeight(120)
        desc.add(self.desc_edit)
        lay.addWidget(desc)

        self.warn_note = QLabel("")
        self.warn_note.setObjectName("Muted")
        self.warn_note.setWordWrap(True)
        lay.addWidget(self.warn_note)
        lay.addStretch(1)

        foot = QWidget()
        foot.setObjectName("Toolbar")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(22, 10, 22, 10)
        fl.setSpacing(10)
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        fl.addWidget(self.status, 1)
        close = _btn("关闭", "Ghost")
        close.clicked.connect(self.reject)
        self.toggle_btn = _btn("编辑", "Primary")
        self.toggle_btn.setIcon(icons.icon("doc", "#FFFFFF", 15))
        self.toggle_btn.setToolTip("切换到可编辑模式；修改后点击同一按钮保存")
        self.toggle_btn.clicked.connect(self._toggle)
        fl.addWidget(close)
        fl.addWidget(self.toggle_btn)
        root.addWidget(foot)

    # -- 数据 ------------------------------------------------------------
    def _fill(self) -> None:
        b = self.session.book
        info = b.info()
        cover = b.cover()
        self.cover_view.set_image(cover[0] if cover else None)
        self.title_label.setText(info["title"] or "（无书名）")

        author = "、".join(info["creators"]) or "未知作者"
        ident = info["identifier"] or ""
        parts = [author, info["language"] or "—"]
        if _looks_isbn(ident):
            parts.append("ISBN " + ident)
        self.meta_label.setText(" · ".join(parts))

        path = self.extras.get("path") or getattr(b, "path", "") or ""
        self.tag_label.setText("EPUB %s · %s" % (info["version"], _size_text(path)))
        self.path_label.setText(path or "—")
        self.path_label.setToolTip(path)
        if cover:
            fmt = (cover[1] or "").split("/")[-1].upper() or "图片"
            fmt = {"SVG+XML": "SVG"}.get(fmt, fmt)
            self.cover_meta.setText("%s · %s" % (fmt, _bytes_text(len(cover[0]))))
            self.cover_meta.setToolTip("这本书当前使用的封面图（%s）" % fmt)
            self.cover_meta.setVisible(True)
        else:
            # 没有封面：占位框里已写「无封面」，这里只在编辑模式下提示可换图，避免重复
            self.cover_meta.setText("无封面，可点击下方按钮选择图片" if self.editing else "")
            self.cover_meta.setVisible(self.editing)

        meta = self.session.metadata()
        for key, edit in self.fields.items():
            value = meta.get(key, "")
            edit.setText(value)
            edit.setCursorPosition(0)      # 长值（如 UUID）从头显示
            edit.setToolTip(value)
        self.desc_edit.setPlainText(meta.get("description", ""))

        warn = len(info["warnings"])
        warn_text = "无" if not warn else "%d 条（%s）" % (warn, info["warnings"][0]["text"][:14])
        source = self.extras.get("source") or self._source_name(b.toc_source)
        rows = {
            "manifest": (str(info["manifest"]), False),
            "spine": (str(info["spine"]), False),
            "toc": (str(CF.count_nodes(self.session.toc)["total"]), False),
            "warn": (warn_text, warn > 0),
            "source": (source, False),
            "file": (self.extras.get("file") or "未改动（原子写入）", self.session.dirty),
            "export": (self.extras.get("export") or "—", False),
        }
        for key, (value, is_warn) in rows.items():
            self.stats[key].set_value(value, warn=is_warn)

        warnings = [w["text"] for w in info["warnings"]]
        self.warn_note.setText("打开时的告警：\n" + "\n".join("· " + w for w in warnings[:5])
                               if warnings else "")

    @staticmethod
    def _source_name(src: str) -> str:
        return {"nav": "nav.xhtml", "ncx": "toc.ncx", "nav+ncx": "nav.xhtml + toc.ncx",
                "spine": "spine 回退"}.get(src, src or "—")

    # -- 只读 / 编辑 ------------------------------------------------------
    def _set_editing(self, on: bool) -> None:
        self.editing = bool(on)
        for edit in self.fields.values():
            edit.setReadOnly(not on)
            edit.setProperty("editing", "true" if on else "false")
            edit.style().unpolish(edit)
            edit.style().polish(edit)
        self.desc_edit.setReadOnly(not on)
        self.desc_edit.setStyleSheet(
            "QPlainTextEdit { background:%s; border:1px %s %s; border-radius:8px; padding:8px; }"
            % (COLORS["surface"] if on else COLORS["surface_subtle"],
               "solid" if on else "dashed", COLORS["border"]))
        self.btn_pick_cover.setVisible(on)
        if not self.session.book.cover():
            self.cover_meta.setVisible(on)     # 编辑模式才提示「没有封面，可换图」
            self.cover_meta.setText("无封面\n可点击下方选择" if on else "")
        if on:
            self.toggle_btn.setText("保存")
            self.toggle_btn.setIcon(icons.icon("save", "#FFFFFF", 15))
            self.toggle_btn.setToolTip("把修改写入会话（可撤销）；保存后回到只读")
            self.status.setText("编辑模式：修改后点击「保存」，或直接关闭以放弃改动")
            self.fields["title"].setFocus()
            self.fields["title"].selectAll()
        else:
            self.toggle_btn.setText("编辑")
            self.toggle_btn.setIcon(icons.icon("doc", "#FFFFFF", 15))
            self.toggle_btn.setToolTip("切换到可编辑模式；修改后点击同一按钮保存")

    def _toggle(self) -> None:
        if self.editing:
            self._save()
        else:
            self._set_editing(True)

    def _values(self) -> dict:
        values = {k: e.text().strip() for k, e in self.fields.items()}
        values["description"] = self.desc_edit.toPlainText().strip()
        return values

    def _save(self) -> bool:
        try:
            self.session.update_metadata(self._values(),
                                         cover_bytes=self.new_cover or None,
                                         cover_name=self.new_cover_name)
        except Exception as exc:      # 评分非法 / 封面无法识别 等
            self.status.setText("保存失败：%s" % exc)
            QMessageBox.warning(self, "保存失败", str(exc))
            return False
        self.changed = True
        self.new_cover = b""
        self.new_cover_name = ""
        self._fill()                  # 标题、封面、统计都按新值重画
        self._set_editing(False)
        self.status.setText("已保存至会话；导出或保存文件后写入 EPUB")
        return True

    def reject(self) -> None:
        if self.editing and self._values() != self._stored_values():
            r = QMessageBox.question(self, "放弃修改", "存在未保存的修改，确定关闭吗？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        super().reject()

    def _stored_values(self) -> dict:
        meta = self.session.metadata()
        out = {k: (meta.get(k) or "").strip() for k in self.fields}
        out["description"] = (meta.get("description") or "").strip()
        return out

    # -- 封面 ------------------------------------------------------------
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
        fmt = os.path.splitext(self.new_cover_name)[1].lstrip(".").upper() or "图片"
        self.cover_meta.setText("待保存 · %s" % fmt)
        self.cover_meta.setToolTip("将替换为：%s" % self.new_cover_name)
        self.cover_meta.setVisible(True)
        self.status.setText("保存后封面将在导出时写入")
