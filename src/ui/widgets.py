# -*- coding: utf-8 -*-
"""通用组件：卡片、统计行、封面、日志、点阵背景、空状态、目录项代理"""

from __future__ import annotations

from PySide6.QtCore import (QModelIndex, QRect, QSize, QStringListModel, Qt,
                            QTimer, Signal)
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QCompleter, QFrame,
                               QHBoxLayout, QInputDialog, QMessageBox,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QRadioButton, QSizePolicy, QSpinBox,
                               QStyle, QStyledItemDelegate, QTreeView,
                               QVBoxLayout, QWidget)

from . import icons, splitmem
from .models import COUNT_ROLE, KIND_ROLE
from .tokens import COLORS, FONTS, METRICS


class ElideLabel(QLabel):
    """单行文本：按可用宽度省略，完整内容放 tooltip"""

    def __init__(self, text: str = "", parent=None):
        super().__init__("", parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        self.setToolTip(text)
        self._apply()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply()

    def showEvent(self, event):
        super().showEvent(event)
        self._apply()

    def _apply(self) -> None:
        # 尚未完成布局时保留原文，避免被省略成空串
        if self.width() <= 8:
            return
        fm = self.fontMetrics()
        super().setText(fm.elidedText(self._full, Qt.ElideRight, max(0, self.width() - 2)))


class Card(QFrame):
    """白底、轻边框、11px 圆角的卡片（规范 §4.4）"""

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setProperty("card", True)
        self._layout = QVBoxLayout(self)
        pad = METRICS["card_pad"]
        self._layout.setContentsMargins(pad, 15, pad, pad)
        self._layout.setSpacing(10)
        self.header = None
        self.title_label = None
        if title:
            self.header = QWidget(self)
            hl = QHBoxLayout(self.header)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(8)
            self.header.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            self.title_label = QLabel(title, self.header)
            self.title_label.setObjectName("CardTitle")
            hl.addWidget(self.title_label)
            hl.addStretch(1)
            self._header_layout = hl
            self._layout.addWidget(self.header)

    def add_header_widget(self, w: QWidget) -> None:
        if self.header is not None:
            self._header_layout.addWidget(w)

    def body(self) -> QVBoxLayout:
        return self._layout

    def add_stretch(self, factor: int = 1) -> None:
        self._layout.addStretch(factor)

    def add(self, w) -> None:
        self._layout.addWidget(w)


class StatRow(QWidget):
    """左标签右数值的统计行"""

    def __init__(self, label: str, value: str = "—", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._label = QLabel(label)
        self._label.setObjectName("FieldLabel")
        self._value = QLabel(value)
        self._value.setObjectName("StatValue")
        self._value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self._label)
        lay.addStretch(1)
        lay.addWidget(self._value)

    def set_value(self, value: str, warn: bool = False) -> None:
        self._value.setText(value)
        self._value.setObjectName("StatValueWarn" if warn else "StatValue")
        self._value.style().unpolish(self._value)
        self._value.style().polish(self._value)

    def set_label(self, text: str) -> None:
        self._label.setText(text)


class CoverView(QWidget):
    """封面缩略图；无封面/加载失败时画统一占位"""

    W, H = 72, 96

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.W, self.H)
        self._pm: QPixmap | None = None

    def set_image(self, data: bytes | None) -> None:
        if not data:
            self._pm = None
        else:
            pm = QPixmap()
            if pm.loadFromData(data):
                w, h = max(1, self.width()), max(1, self.height())
                self._pm = pm.scaled(w, h, Qt.KeepAspectRatioByExpanding,
                                     Qt.SmoothTransformation)
            else:
                self._pm = None
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = self.rect().adjusted(0, 0, -1, -1)
        path_r = 8
        if self._pm is not None:
            p.save()
            clip = p.clipPath  # 保持接口一致
            p.setClipRect(rect)
            scaled = self._pm
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            p.drawPixmap(x, y, scaled)
            p.restore()
        else:
            p.setPen(QPen(QColor(COLORS["border"]), 1))
            p.setBrush(QColor(COLORS["surface_subtle"]))
            p.drawRoundedRect(rect, path_r, path_r)
            icon = icons.icon("book", COLORS["disabled"], 30)
            icon.paint(p, QRect(0, self.H // 2 - 22, self.W, 30), Qt.AlignCenter)
            p.setPen(QColor(COLORS["disabled"]))
            f = QFont(FONTS["family"], 7)
            p.setFont(f)
            p.drawText(QRect(0, self.H // 2 + 10, self.W, 18), Qt.AlignCenter, "无封面")
        p.setPen(QPen(QColor(COLORS["border"]), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect, path_r, path_r)
        p.end()


class ChapterTree(QTreeView):
    """目录树：点击空白区域清除选择（这样"新建大标题"按钮可以走新建空组的路径）"""

    def mousePressEvent(self, event):
        if not self.indexAt(event.position().toPoint()).isValid():
            self.clearSelection()
            self.setCurrentIndex(QModelIndex())
        super().mousePressEvent(event)


LOG_ROLE = Qt.UserRole + 10
LOG_ACTION_ROLE = Qt.UserRole + 11
LOG_DETAIL_ROLE = Qt.UserRole + 12
LOG_LEVEL_ROLE = Qt.UserRole + 13
LOG_COLOR = {"info": COLORS["text_secondary"], "ok": COLORS["accent"],
             "warn": COLORS["warning"], "error": COLORS["danger"]}


class LogDelegate(QStyledItemDelegate):
    """时间 / 动作 / 描述 三列式绘制，描述过长自动省略（规范 §12：不为每条创建控件）"""

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = option.rect.adjusted(6, 0, -6, 0)
        fm = painter.fontMetrics()
        if option.state & QStyle.State_MouseOver:
            painter.fillRect(rect, QColor("#F3F5F6"))

        time_text = index.data(LOG_ROLE) or ""
        action = index.data(LOG_ACTION_ROLE) or ""
        detail = index.data(LOG_DETAIL_ROLE) or ""
        level = index.data(LOG_LEVEL_ROLE) or "info"

        x = rect.left() + 2
        ty = rect.top() + (rect.height() - fm.height()) // 2
        painter.setPen(QColor(COLORS["text_secondary"]))
        painter.drawText(QRect(x, ty, 44, fm.height()), Qt.AlignLeft | Qt.AlignVCenter, time_text)
        x += 48
        painter.setPen(QColor(LOG_COLOR.get(level, COLORS["text_secondary"])))
        painter.drawText(QRect(x, ty, 52, fm.height()), Qt.AlignLeft | Qt.AlignVCenter, action)
        x += 56
        painter.setPen(QColor(COLORS["text_primary"]))
        width = max(10, rect.right() - x - 2)
        painter.drawText(QRect(x, ty, width, fm.height()),
                         Qt.AlignLeft | Qt.AlignVCenter,
                         fm.elidedText(detail, Qt.ElideRight, width))
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(120, 26)


class LogView(QListWidget):
    """操作日志：只存数据，绘制交给委托"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setSelectionMode(QListWidget.NoSelection)
        self.setFocusPolicy(Qt.NoFocus)
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.setMouseTracking(True)
        self.setItemDelegate(LogDelegate(self))
        self.setStyleSheet("QListWidget { background: transparent; border: none; padding: 0; }")

    def add_entry(self, time_text: str, action: str, detail: str, level: str = "info") -> None:
        item = QListWidgetItem()
        item.setData(LOG_ROLE, time_text)
        item.setData(LOG_ACTION_ROLE, action)
        item.setData(LOG_DETAIL_ROLE, detail)
        item.setData(LOG_LEVEL_ROLE, level)
        item.setToolTip("%s  %s  %s" % (time_text, action, detail))
        item.setSizeHint(QSize(120, 26))
        self.insertItem(0, item)

    def clear_log(self) -> None:
        self.clear()


class DottedBackground(QWidget):
    """低对比点阵纹理（规范 §4.5，可关闭）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.enabled = True

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(COLORS["window_bg"]))
        if not self.enabled:
            p.end()
            return
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(32, 36, 40, 12))
        step = 18
        for y in range(0, self.height(), step):
            for x in range(0, self.width(), step):
                p.drawEllipse(x, y, 1.6, 1.6)
        p.end()


class EmptyState(QWidget):
    """空状态：图标 + 标题 + 说明（不显示伪造数据）"""

    def __init__(self, icon_name: str = "doc", title: str = "", hint: str = "", parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(8)
        lay.addStretch(1)
        ic = QLabel()
        ic.setPixmap(icons.icon(icon_name, COLORS["disabled"], 34).pixmap(34, 34))
        ic.setAlignment(Qt.AlignCenter)
        self._title = QLabel(title)
        self._title.setAlignment(Qt.AlignCenter)
        self._title.setStyleSheet("color:%s; font-weight:600;" % COLORS["text_secondary"])
        self._hint = QLabel(hint)
        self._hint.setObjectName("Muted")
        self._hint.setAlignment(Qt.AlignCenter)
        self._hint.setWordWrap(True)
        lay.addWidget(ic)
        lay.addWidget(self._title)
        lay.addWidget(self._hint)
        lay.addStretch(1)

    def set_text(self, title: str, hint: str = "") -> None:
        self._title.setText(title)
        self._hint.setText(hint)


class ChapterDelegate(QStyledItemDelegate):
    """目录项绘制：选中浅绿底 + 深绿字、悬停浅灰、分组显示子项数量"""

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        rect = option.rect.adjusted(0, 1, -2, -1)

        if selected:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(COLORS["accent_soft"]))
            painter.drawRoundedRect(rect, 6, 6)
        elif hover:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#E3E8EA"))
            painter.drawRoundedRect(rect, 6, 6)

        icon = index.data(Qt.DecorationRole)
        icon_size = 16
        ix = rect.left() + 4
        iy = rect.center().y() - icon_size // 2 + 1
        if icon is not None:
            icon.paint(painter, QRect(ix, iy, icon_size, icon_size))

        kind = index.data(KIND_ROLE)
        count = index.data(COUNT_ROLE) or 0
        right_pad = 40 if (kind == "group" and count > 0) else 8

        text_rect = QRect(ix + icon_size + 7, rect.top(),
                          max(10, rect.right() - (ix + icon_size + 7) - right_pad),
                          rect.height())
        painter.setFont(option.font)
        painter.setPen(QColor(COLORS["accent_hover"] if selected else COLORS["text_primary"]))
        fm = painter.fontMetrics()
        title = index.data(Qt.DisplayRole) or ""
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter,
                         fm.elidedText(title, Qt.ElideRight, text_rect.width()))

        if kind == "group" and count > 0:
            label = str(count)
            w = 10 + len(label) * 6
            badge = QRect(rect.right() - w - 6, rect.center().y() - 9, w, 18)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(COLORS["surface"] if selected else COLORS["surface_subtle"]))
            painter.drawRoundedRect(badge, 9, 9)
            painter.setPen(QColor(COLORS["text_secondary"]))
            f = painter.font()
            f.setPointSizeF(max(7.0, f.pointSizeF() - 1.0))
            painter.setFont(f)
            painter.drawText(badge, Qt.AlignCenter, label)
        painter.restore()

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        return QSize(s.width(), METRICS["tree_row"])


class SplitRulePanel(QWidget):
    """TXT 分章规则面板：简易规则 / 正则 / 按字数 / 整段"""

    changed = Signal()

    UNIT_CHOICES = ["章", "节", "回", "篇", "集", "部", "卷"]
    NUM_CHOICES = [("中英文数字", "any"), ("纯中文数字", "cn"), ("纯数字", "digit")]

    def __init__(self, parent=None):
        super().__init__(parent)
        import txtconv  # 只用于构造规则对象
        self._txtconv = txtconv

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        # 简易规则
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.rb_simple = QRadioButton("简易规则")
        self.rb_simple.setChecked(True)
        self.cb_indent = QCheckBox("行首空格")
        self.cb_indent.setToolTip("只把缩进开头的行当作标题，可避开正文里的「第X章」引用")
        self.prefix_edit = QLineEdit("第")
        self.prefix_edit.setFixedWidth(46)
        self.prefix_edit.setToolTip("编号前的文字，例如「第」")
        self.num_combo = QComboBox()
        for text, key in self.NUM_CHOICES:
            self.num_combo.addItem(text, key)
        self.unit_combo = QComboBox()
        self.unit_combo.addItems(self.UNIT_CHOICES)
        self.unit_combo.setFixedWidth(64)
        self.cb_builtin = QCheckBox("含常见写法")
        self.cb_builtin.setChecked(True)
        self.cb_builtin.setToolTip("同时识别 序章 / 第一卷 / Chapter N 等常见标题写法")
        row1.addWidget(self.rb_simple)
        row1.addWidget(self.cb_indent)
        row1.addSpacing(6)
        row1.addWidget(self._muted("前缀"))
        row1.addWidget(self.prefix_edit)
        row1.addWidget(self.num_combo)
        row1.addWidget(self.unit_combo)
        row1.addWidget(self.cb_builtin)
        row1.addStretch(1)
        v.addLayout(row1)

        # 附加规则
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addSpacing(24)
        row2.addWidget(self._muted("附加规则"))
        self.extra_edit = QLineEdit()
        self.extra_edit.setPlaceholderText(r"额外标题正则，例如 ^(番外|附录)\s+\S+")
        row2.addWidget(self.extra_edit, 1)
        btn_extra = QPushButton("从文件…")
        btn_extra.setObjectName("Ghost")
        btn_extra.clicked.connect(lambda: self._load_pattern(self.extra_edit))
        row2.addWidget(btn_extra)
        v.addLayout(row2)

        # 正则表达式
        row3 = QHBoxLayout()
        row3.setSpacing(8)
        self.rb_regex = QRadioButton("正则表达式")
        row3.addWidget(self.rb_regex)
        self.regex_edit = QLineEdit()
        self.regex_edit.setPlaceholderText(r"每命中一行即一章，例如 ^\s*第[一二三四五六七八九十\d]+章")
        row3.addWidget(self.regex_edit, 1)
        btn_regex = QPushButton("从文件…")
        btn_regex.setObjectName("Ghost")
        btn_regex.clicked.connect(lambda: self._load_pattern(self.regex_edit))
        row3.addWidget(btn_regex)
        v.addLayout(row3)

        # 按字数 / 整段
        row4 = QHBoxLayout()
        row4.setSpacing(8)
        self.rb_size = QRadioButton("按字数均分")
        self.size_spin = QSpinBox()
        self.size_spin.setRange(500, 20000)
        self.size_spin.setSingleStep(500)
        self.size_spin.setValue(4000)
        self.size_spin.setSuffix(" 字")
        self.rb_none = QRadioButton("整段不切分")
        row4.addWidget(self.rb_size)
        row4.addWidget(self._muted("每章约"))
        row4.addWidget(self.size_spin)
        row4.addSpacing(18)
        row4.addWidget(self.rb_none)
        row4.addStretch(1)

        # LLM 辅助识别（可选）：放在本行右端，不额外占一行高度
        self.cb_llm = QCheckBox("LLM 辅助识别标题")
        self.cb_llm.setToolTip(
            "把候选标题行交给大模型复核：剔除正文里的误判、补出没写「第X章」的短标题。\n"
            "只裁决标题行，正文不经过模型；未配置或调用失败时自动回退本地规则。\n"
            "仅在「简易规则 / 正则表达式」下生效。")
        self.btn_llm = QPushButton("设置…")
        self.btn_llm.setObjectName("Ghost")
        self.btn_llm.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.btn_llm.setToolTip("配置服务地址、模型、API Key、超时与重试")
        self.btn_llm.clicked.connect(self._open_llm_settings)
        self.llm_hint = QLabel("")
        self.llm_hint.setObjectName("Muted")
        row4.addWidget(self.cb_llm)
        row4.addWidget(self.btn_llm)
        row4.addWidget(self.llm_hint)
        v.addLayout(row4)
        self.cb_llm.toggled.connect(self._on_llm_toggled)
        self._refresh_llm_hint()

        # 预设：内置写法 + 用户自己存的；选中即套用
        row5 = QHBoxLayout()
        row5.setSpacing(8)
        row5.addWidget(self._muted("预设"))
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumWidth(230)
        self.preset_combo.setToolTip("内置的常见写法，以及你自己「保存当前…」存下的规则")
        self.preset_combo.activated.connect(lambda *_: self._apply_preset())
        row5.addWidget(self.preset_combo, 1)
        self.btn_save_preset = QPushButton("保存当前…")
        self.btn_save_preset.setObjectName("Ghost")
        self.btn_save_preset.setToolTip("把现在这套规则存成预设，下次直接选，不用重敲")
        self.btn_save_preset.clicked.connect(self._save_preset)
        row5.addWidget(self.btn_save_preset)
        self.btn_del_preset = QPushButton("删除")
        self.btn_del_preset.setObjectName("Ghost")
        self.btn_del_preset.setToolTip("删除自己保存的预设（内置的不能删）")
        self.btn_del_preset.clicked.connect(self._delete_preset)
        row5.addWidget(self.btn_del_preset)
        v.addLayout(row5)

        # 附加规则 / 正则：把敲过的写进补全候选（本地记忆）
        self._attach_history(self.extra_edit, "extra")
        self._attach_history(self.regex_edit, "regex")

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color:%s;" % COLORS["danger"])
        self.error_label.setVisible(False)
        v.addWidget(self.error_label)

        for rb in (self.rb_simple, self.rb_regex, self.rb_size, self.rb_none):
            rb.toggled.connect(self._on_mode_changed)
        for w in (self.cb_indent, self.prefix_edit, self.extra_edit, self.regex_edit,
                  self.cb_builtin):
            if hasattr(w, "textChanged"):
                w.textChanged.connect(self._on_text_changed)
            else:
                w.toggled.connect(self._on_text_changed)
        for w in (self.num_combo, self.unit_combo):
            w.currentIndexChanged.connect(self._on_text_changed)
        self.size_spin.valueChanged.connect(self._on_text_changed)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._autosave)

        self._on_mode_changed()
        self._ready = True
        self._rebuild_presets()
        self._restore_last()
        # 高度按当前字体与 QSS 实时算：构造时定死会在高 DPI / 大字号下把最后一行挤出去
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    def sizeHint(self):
        lay = self.layout()
        return lay.sizeHint() if lay is not None else super().sizeHint()

    def minimumSizeHint(self):
        lay = self.layout()
        return lay.minimumSize() if lay is not None else super().minimumSizeHint()

    # -- 内部 ------------------------------------------------------------
    def _muted(self, text: str) -> QLabel:
        lb = QLabel(text)
        lb.setObjectName("FieldLabel")
        return lb

    def _load_pattern(self, target: QLineEdit) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "选择规则文件", "", "文本 (*.txt);;所有文件 (*)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        except OSError as exc:
            self.error_label.setText("读取失败：%s" % exc)
            self.error_label.setVisible(True)
            return
        if lines:
            target.setText("|".join(lines) if len(lines) > 1 else lines[0])

    def _mode(self) -> str:
        if self.rb_regex.isChecked():
            return "regex"
        if self.rb_size.isChecked():
            return "size"
        if self.rb_none.isChecked():
            return "none"
        return "simple"

    def _on_mode_changed(self, *_):
        mode = self._mode()
        simple = mode == "simple"
        for w in (self.cb_indent, self.prefix_edit, self.num_combo, self.unit_combo,
                  self.extra_edit, self.cb_builtin):
            w.setEnabled(simple)
        self.regex_edit.setEnabled(mode == "regex")
        self.size_spin.setEnabled(mode == "size")
        # 按字数 / 整段是显式指定的切法，LLM 不参与
        llm_ok = mode in ("simple", "regex")
        self.cb_llm.setEnabled(llm_ok)
        self.btn_llm.setEnabled(llm_ok)
        self.llm_hint.setEnabled(llm_ok)
        self._validate()

    # -- LLM 辅助识别 ----------------------------------------------------
    def _on_llm_toggled(self, on: bool) -> None:
        self._refresh_llm_hint()
        self.changed.emit()

    def _llm_config(self):
        try:
            import llmseg  # type: ignore
        except ImportError:
            return None
        try:
            return llmseg.LLMOptions.load()
        except Exception:  # pragma: no cover - 配置异常不该拖垮面板
            return None

    def _refresh_llm_hint(self) -> None:
        cfg = self._llm_config()
        if cfg is None:
            self.llm_hint.setText("")
            return
        if not self.cb_llm.isChecked():
            self.llm_hint.setStyleSheet("")
            self.llm_hint.setToolTip("")
            self.llm_hint.setText("")
            return
        problem = cfg.problem()
        self.llm_hint.setStyleSheet(("color:%s;" % COLORS["danger"]) if problem else "")
        self.llm_hint.setText(problem if problem else "已启用 · %s" % (cfg.model or "未选模型"))
        self.llm_hint.setToolTip(problem or
                                 "候选标题行交模型复核，正文不经过模型；失败自动回退本地规则")

    def _open_llm_settings(self) -> None:
        from .dialogs import LLMSettingsDialog
        dlg = LLMSettingsDialog(self)
        dlg.exec()
        if dlg.enabled() and not self.cb_llm.isChecked():
            self.cb_llm.setChecked(True)
        self._refresh_llm_hint()

    def llm_options(self):
        """给 split_chapters 用的 LLM 选项；没勾选返回 None（行为与原逻辑一致）"""
        if not self.cb_llm.isChecked():
            return None
        cfg = self._llm_config()
        if cfg is None:
            return None
        cfg.enabled = True
        return cfg

    def _on_text_changed(self, *_):
        self._validate()
        self._touch()

    def _validate(self) -> bool:
        import re
        mode = self._mode()
        pattern = (self.regex_edit.text() if mode == "regex" else
                   self.extra_edit.text() if mode == "simple" else "")
        if pattern.strip():
            try:
                re.compile(pattern)
            except re.error as exc:
                self.error_label.setText("正则无效：%s" % exc)
                self.error_label.setVisible(True)
                self.changed.emit()
                return False
        self.error_label.setVisible(False)
        self.changed.emit()
        return True

    # -- 本地记忆：预设 / 上次设置 / 输入历史 -----------------------------
    def params(self) -> dict:
        """当前这套规则（用于存预设与记住上次）"""
        return {"mode": self._mode(), "indent": self.cb_indent.isChecked(),
                "prefix": self.prefix_edit.text().strip() or "第",
                "numerals": self.num_combo.currentData(),
                "unit": self.unit_combo.currentText(),
                "extra": self.extra_edit.text().strip(),
                "pattern": self.regex_edit.text().strip(),
                "size": self.size_spin.value(),
                "builtin": self.cb_builtin.isChecked()}

    def set_params(self, params: dict) -> None:
        p = dict(splitmem.DEFAULT_PARAMS, **(params or {}))
        self._loading = True
        try:
            {"regex": self.rb_regex, "size": self.rb_size,
             "none": self.rb_none}.get(p["mode"], self.rb_simple).setChecked(True)
            self.cb_indent.setChecked(bool(p["indent"]))
            self.prefix_edit.setText(str(p["prefix"]))
            idx = self.num_combo.findData(p["numerals"])
            self.num_combo.setCurrentIndex(idx if idx >= 0 else 0)
            unit = str(p["unit"])
            if self.unit_combo.findText(unit) < 0:
                self.unit_combo.addItem(unit)
            self.unit_combo.setCurrentText(unit)
            self.extra_edit.setText(str(p["extra"]))
            self.regex_edit.setText(str(p["pattern"]))
            try:
                self.size_spin.setValue(int(p["size"]))
            except (TypeError, ValueError):
                pass
            self.cb_builtin.setChecked(bool(p["builtin"]))
        finally:
            self._loading = False
        self._on_mode_changed()

    def _touch(self) -> None:
        """输入变化后延迟存一次，避免每敲一个字就写盘"""
        timer = getattr(self, "_save_timer", None)
        if timer is not None and getattr(self, "_ready", False):
            timer.start()

    def _autosave(self) -> None:
        try:
            splitmem.save_last(self.params())
        except Exception:  # pragma: no cover - 存不下也不该影响使用
            pass

    def _restore_last(self) -> None:
        try:
            last = splitmem.load_last()
        except Exception:  # pragma: no cover
            last = None
        if last:
            self.set_params(last)

    def _rebuild_presets(self) -> None:
        try:
            builtin = splitmem.builtin_presets()
            custom = splitmem.load_custom()
        except Exception:  # pragma: no cover
            builtin, custom = [], []
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem("选一个预设…", None)
        for item in builtin:
            # 注意：PySide6 存 tuple 取回来会变 list，findData(元组) 永远匹配不上，用字符串键
            self.preset_combo.addItem("内置 · %s" % item["name"],
                                      "builtin\x1f%s" % item["name"])
        for item in custom:
            self.preset_combo.addItem("我的 · %s" % item["name"],
                                      "custom\x1f%s" % item["name"])
        self.preset_combo.setCurrentIndex(0)
        self.preset_combo.blockSignals(False)
        self.btn_del_preset.setEnabled(False)

    def _apply_preset(self) -> None:
        data = self.preset_combo.currentData()
        if not data or "\x1f" not in str(data):
            return
        kind, name = str(data).split("\x1f", 1)
        items = splitmem.builtin_presets() if kind == "builtin" else splitmem.load_custom()
        for item in items:
            if item["name"] == name:
                self.set_params(item["params"])
                break
        self.btn_del_preset.setEnabled(kind == "custom")

    def _save_preset(self, name: str = "") -> None:
        """存预设。测试可以直接传名字跳过输入框。"""
        if not name:
            name, ok = QInputDialog.getText(self, "保存为预设",
                                            "预设名称（同名会覆盖）：", text="我的规则")
            if not ok:
                return
        if not name.strip():
            return
        name = name.strip()
        splitmem.add_custom(name, self.params())
        self._rebuild_presets()
        idx = self.preset_combo.findData("custom\x1f%s" % name)
        if idx >= 0:
            self.preset_combo.setCurrentIndex(idx)
            self.btn_del_preset.setEnabled(True)

    def _delete_preset(self, name: str = "") -> None:
        """删预设。测试可以直接传名字跳过确认框。"""
        if not name:
            data = str(self.preset_combo.currentData() or "")
            if not data.startswith("custom\x1f"):
                QMessageBox.information(self, "删除预设",
                                        "内置预设不能删；只能删自己保存的。")
                return
            name = data.split("\x1f", 1)[1]
            if QMessageBox.question(self, "删除预设",
                                    "删除预设「%s」？" % name) != QMessageBox.Yes:
                return
        splitmem.remove_custom(name)
        self._rebuild_presets()

    def _attach_history(self, edit, kind: str) -> None:
        """把敲过的规则记进补全候选（本地记忆，下次直接选）"""
        comp = QCompleter(splitmem.load_history(kind), edit)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setMaxVisibleItems(8)
        edit.setCompleter(comp)

        def remember():
            value = edit.text().strip()
            if not value:
                return
            items = splitmem.push_history(kind, value)
            comp.setModel(QStringListModel(items, comp))

        edit.editingFinished.connect(remember)

    # -- 对外 ------------------------------------------------------------
    def rules(self):
        mode = self._mode()
        return self._txtconv.SplitRules(
            mode=mode, indent=self.cb_indent.isChecked(),
            prefix=self.prefix_edit.text().strip() or "第",
            numerals=self.num_combo.currentData(),
            unit=self.unit_combo.currentText(),
            extra=self.extra_edit.text().strip(),
            pattern=self.regex_edit.text().strip(),
            size=self.size_spin.value(), builtin=self.cb_builtin.isChecked())

    def is_valid(self) -> bool:
        return self._validate()

    def setVisible(self, visible: bool) -> None:  # noqa: N802
        super().setVisible(visible)
