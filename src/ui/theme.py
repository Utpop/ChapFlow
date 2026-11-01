# -*- coding: utf-8 -*-
"""浅色 QSS 主题（颜色全部来自 tokens，避免散落硬编码）"""

import os

from .paths import qss_assets_dir
from .tokens import COLORS as C, METRICS as M, FONTS as F

# 勾选/单选指示器用 SVG，路径在 QSS 里必须是绝对路径
_ASSETS = qss_assets_dir()


def build_qss() -> str:
    qss = f"""
* {{
    font-family: {F['stack']};
    outline: none;
}}
QWidget {{
    color: {C['text_primary']};
    font-size: {F['body']}pt;
}}
QMainWindow, QWidget#Root {{
    background: {C['window_bg']};
}}

/* ---------------- 顶部 ---------------- */
QWidget#Header {{
    background: {C['surface']};
    border-bottom: 1px solid {C['border']};
}}
QLabel#AppName {{
    font-size: 11pt;
    font-weight: 600;
    color: {C['text_primary']};
}}
QLabel#FileName {{
    color: {C['text_secondary']};
    font-size: {F['secondary']}pt;
}}
QLabel#Logo {{
    background: transparent;
}}
QWidget#Toolbar {{
    background: {C['surface']};
    border-bottom: 1px solid {C['border']};
}}
/* ---------------- 按钮 ---------------- */
QPushButton {{
    min-height: {M['button_h']}px;
    padding: 0 12px;
    color: {C['text_primary']};
    background: {C['surface_subtle']};
    border: 1px solid {C['border']};
    border-radius: {M['btn_radius']}px;
}}
QPushButton:hover {{ background: {C['hover']}; }}
QPushButton:pressed {{ background: {C['pressed']}; }}
QPushButton:focus {{ border: 1px solid {C['accent']}; }}
QPushButton:disabled {{
    color: {C['disabled']};
    background: {C['window_bg']};
    border-color: #E8EAEC;
}}
QPushButton#Primary {{
    color: #FFFFFF;
    background: {C['accent']};
    border: 1px solid {C['accent']};
    font-weight: 600;
    padding: 0 16px;
}}
QPushButton#Primary:hover {{ background: {C['accent_hover']}; border-color: {C['accent_hover']}; }}
QPushButton#Primary:pressed {{ background: #0E5C34; }}
QPushButton#Primary:disabled {{ background: #A9C9B7; border-color: #A9C9B7; color: #F2F7F4; }}
QPushButton#DetailAction {{
    padding: 0 10px;
}}
QPushButton[attention="true"] {{
    color: {C['warning']};
    background: {C['warning_soft']};
    border: 1px solid #E7C79A;
    font-weight: bold;
}}
QPushButton[attention="true"]:hover {{ background: #F6E7CD; border-color: #DDB475; }}
/* 已开书时的「打开」：灰字弱化，但保留正常边框，任何状态下都看得见轮廓 */
QPushButton[secondary="true"] {{
    color: {C['text_secondary']};
    background: {C['surface_subtle']};
    border: 1px solid {C['border']};
}}
QPushButton[secondary="true"]:hover {{
    color: {C['text_primary']};
    background: {C['hover']};
    border-color: {C['border']};
}}
QPushButton[secondary="true"]:pressed {{ background: {C['pressed']}; }}
QPushButton[secondary="true"]:focus {{ border: 1px solid {C['accent']}; }}
QPushButton[secondary="true"]:disabled {{
    color: {C['disabled']};
    background: {C['window_bg']};
    border-color: {C['border']};
}}

QPushButton#Danger {{
    color: {C['danger']};
    background: {C['danger_soft']};
    border: 1px solid #F0CFCC;
}}
QPushButton#Danger:hover {{ background: #F7DEDC; }}
QPushButton#MiniButton {{
    min-height: 24px;
    max-height: 24px;
    padding: 0 9px;
    font-size: {F['secondary']}pt;
    color: {C['text_secondary']};
    background: {C['surface_subtle']};
    border: 1px solid {C['border']};
    border-radius: 6px;
}}
QPushButton#MiniButton:hover {{ background: {C['hover']}; color: {C['text_primary']}; }}
/* 合并对话框：虚线上传按钮 + 检查结果横幅 + 目录结构树 */
QPushButton#AddFile {{
    color: {C['accent_hover']};
    background: {C['surface']};
    border: 1px dashed {C['border']};
    border-radius: 8px;
    font-weight: 600;
}}
QPushButton#AddFile:hover {{ background: {C['accent_soft']}; border-color: {C['accent']}; }}
/* 自检报告的三大色块：整块浅色底 + 实心圆图标 + 大号数字 */
QFrame#CountBlock {{ border: none; border-radius: 10px; }}
QFrame#CountBlock[tone="ok"] {{ background: #E9F6EE; }}
QFrame#CountBlock[tone="warn"] {{ background: #FDF4E3; }}
QFrame#CountBlock[tone="error"] {{ background: #FDECEC; }}
QLabel#CountDisc {{ border-radius: 15px; }}
QLabel#CountDisc[tone="ok"] {{ background: #2E9E5B; }}
QLabel#CountDisc[tone="warn"] {{ background: #E0A33A; }}
QLabel#CountDisc[tone="error"] {{ background: #D95050; }}
QLabel#CountValue {{ font-size: 19pt; font-weight: 600; }}
QLabel#CountLabel {{ font-size: 9pt; }}
QFrame#CountBlock[tone="ok"] QLabel#CountValue {{ color: #1B7A45; }}
QFrame#CountBlock[tone="ok"] QLabel#CountLabel {{ color: #4A8F6A; }}
QFrame#CountBlock[tone="warn"] QLabel#CountValue {{ color: #A9701A; }}
QFrame#CountBlock[tone="warn"] QLabel#CountLabel {{ color: #B08640; }}
QFrame#CountBlock[tone="error"] QLabel#CountValue {{ color: #B33A3A; }}
QFrame#CountBlock[tone="error"] QLabel#CountLabel {{ color: #B96A6A; }}
QLabel#StatusDot {{ border-radius: 5px; background: {C['disabled']}; }}
QLabel#StatusDot[level="ok"] {{ background: {C['accent']}; }}
QLabel#StatusDot[level="warn"] {{ background: {C['warning']}; }}
QLabel#StatusDot[level="error"] {{ background: {C['danger']}; }}
QLabel#BannerError {{
    color: {C['danger']};
    background: {C['danger_soft']};
    border-radius: 8px;
    padding: 8px 10px;
}}
/* 平铺单选标签（替代下拉框）：未选浅底描边，选中实心强调色 */
QPushButton#Chip {{
    min-height: 30px;
    padding: 0 14px;
    color: {C['text_secondary']};
    background: {C['surface_subtle']};
    border: 1px solid {C['border']};
    border-radius: 15px;
    font-weight: 500;
}}
QPushButton#Chip:hover {{ background: {C['hover']}; color: {C['text_primary']}; }}
QPushButton#Chip:checked {{
    color: #FFFFFF;
    background: {C['accent']};
    border: 1px solid {C['accent']};
    font-weight: 600;
}}
QPushButton#Chip:checked:hover {{ background: {C['accent_hover']}; border-color: {C['accent_hover']}; }}
/* 编号对话框里的「章节名可规范」提示条 */
QFrame#TidyBox {{
    background: {C['warning_soft']};
    border: 1px solid #EBD4AC;
    border-radius: 8px;
}}
QLabel#TidyText {{ color: #8A6A2F; }}
QPushButton#TidyAction {{
    min-height: 26px;
    padding: 0 10px;
    color: #8A6A2F;
    background: {C['surface']};
    border: 1px solid #E0C69B;
    border-radius: 6px;
}}
QPushButton#TidyAction:hover {{ background: #FFF6E4; border-color: {C['warning']}; }}
QPushButton#TidyAction:pressed {{ background: #FBEBCD; }}
QPushButton#TidyAction:disabled {{ color: {C['disabled']}; border-color: #E8EAEC; }}
QLabel#BannerOk {{
    color: {C['accent_hover']};
    background: {C['accent_soft']};
    border-radius: 8px;
    padding: 8px 10px;
}}
QTreeWidget#MergeTree {{ background: transparent; border: none; }}
QTreeWidget#MergeTree::item {{ height: 26px; }}
/* 待合并列表：书多时滚动，滚动条给出浅色轨道让人看出来能滚 */
QListWidget#MergeList QScrollBar:vertical {{ background: #F1F3F5; width: 8px; margin: 2px; }}
QListWidget#MergeList QScrollBar::handle:vertical {{
    background: #C4CCD3; min-height: 24px; border-radius: 3px;
}}
QPushButton#MiniButton:pressed {{ background: {C['pressed']}; }}
QPushButton#MiniButton:disabled {{
    color: {C['disabled']}; background: {C['window_bg']}; border-color: #E8EAEC;
}}
QFrame#VLine {{ background: {C['border']}; border: none; }}
/* 次级按钮（取消 / 关闭）：要有「按得下去」的样子，否则在底部工具栏里就是一段裸文字 */
QPushButton#Secondary {{
    background: {C['surface_subtle']};
    border: 1px solid {C['border']};
    color: {C['text_primary']};
}}
QPushButton#Secondary:hover {{ background: {C['hover']}; }}
QPushButton#Secondary:pressed {{ background: {C['pressed']}; }}
QPushButton#Secondary:focus {{ border: 1px solid {C['accent']}; }}
QPushButton#Secondary:disabled {{
    color: {C['text_secondary']};
    background: {C['window_bg']};
}}

/* 带一点主题色的轻量操作（换封面这类小动作）：比灰按钮醒目一点，比主按钮轻 */
QPushButton#TintAction {{
    background: {C['accent_soft']};
    border: 1px solid transparent;
    color: {C['accent_hover']};
}}
QPushButton#TintAction:hover {{ border-color: {C['accent']}; }}
QPushButton#TintAction:pressed {{ background: {C['hover']}; }}
QPushButton#TintAction:disabled {{
    background: {C['window_bg']};
    color: {C['disabled']};
}}

QPushButton#Ghost {{
    background: transparent;
    border: 1px solid transparent;
    color: {C['text_secondary']};
}}
QPushButton#Ghost:hover {{ background: {C['surface_subtle']}; color: {C['text_primary']}; }}
QFrame#Segmented {{
    background: {C['surface_subtle']};
    border: 1px solid {C['border']};
    border-radius: {M['btn_radius']}px;
}}
QPushButton#SegLeft, QPushButton#SegRight {{
    background: transparent;
    border: none;
    padding: 0;
    min-height: 30px;
    min-width: 32px;
}}
QPushButton#SegLeft {{
    border-top-left-radius: {M['btn_radius']}px;
    border-bottom-left-radius: {M['btn_radius']}px;
    border-right: 1px solid {C['border']};
}}
QPushButton#SegRight {{
    border-top-right-radius: {M['btn_radius']}px;
    border-bottom-right-radius: {M['btn_radius']}px;
}}
QPushButton#SegLeft:hover, QPushButton#SegRight:hover {{ background: {C['hover']}; }}
QPushButton#SegLeft:pressed, QPushButton#SegRight:pressed {{ background: {C['pressed']}; }}
QPushButton#SegLeft:focus, QPushButton#SegRight:focus {{ border: 1px solid {C['accent']}; }}
QPushButton#SegLeft:disabled, QPushButton#SegRight:disabled {{ background: transparent; }}

QPushButton#IconButton {{
    min-width: 30px;
    max-width: 30px;
    min-height: 30px;
    padding: 0;
    background: transparent;
    border: 1px solid transparent;
    border-radius: {M['btn_radius']}px;
}}
QPushButton#IconButton:hover {{ background: {C['surface_subtle']}; }}
QPushButton#IconButton:disabled {{ background: transparent; }}

/* ---------------- 输入 ---------------- */
QLineEdit {{
    min-height: {M['input_h']}px;
    padding: 0 10px;
    color: {C['text_primary']};
    background: {C['surface_subtle']};
    border: 1px solid transparent;
    border-radius: {M['btn_radius']}px;
    selection-background-color: {C['accent_soft']};
    selection-color: {C['text_primary']};
}}
QLineEdit:focus {{
    background: {C['surface']};
    border: 1px solid {C['accent']};
}}
QLineEdit:disabled {{ color: {C['text_secondary']}; background: {C['window_bg']}; }}
QLineEdit[readOnly="true"] {{
    color: {C['text_secondary']};
    background: {C['surface_subtle']};
    border: 1px dashed {C['border']};
}}
QLineEdit[editing="true"] {{
    color: {C['text_primary']};
    background: {C['surface']};
    border: 1px solid {C['border']};
}}
QLineEdit[editing="true"]:focus {{ border: 1px solid {C['accent']}; }}
QLineEdit#SearchInput {{ padding-left: 28px; }}

/* 阅读窗口的正文编辑框：和白底正文同一观感，左右留白对齐阅读区 */
QPlainTextEdit#BodyEditor {{
    color: {C['text_primary']};
    background: {C['surface']};
    border: none;
    padding: 18px 46px;
    selection-background-color: {C['accent_soft']};
    selection-color: {C['text_primary']};
}}

QComboBox {{
    min-height: {M['input_h']}px;
    padding: 0 10px;
    background: {C['surface_subtle']};
    border: 1px solid transparent;
    border-radius: {M['btn_radius']}px;
}}
QComboBox:hover {{ border-color: {C['border']}; }}
QComboBox:focus {{ background: {C['surface']}; border-color: {C['accent']}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{
    image: url("{{A}}/chevron-down.svg");
    width: 12px;
    height: 12px;
}}
QComboBox QAbstractItemView {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 8px;
    padding: 4px;
    selection-background-color: {C['accent_soft']};
    selection-color: {C['accent_hover']};
}}

/* 数字输入：沿用输入框/下拉的底色与圆角（Fusion 默认是方角凹槽，放一起像另一个软件） */
QSpinBox, QDoubleSpinBox {{
    min-height: {M['input_h']}px;
    padding: 0 22px 0 10px;
    color: {C['text_primary']};
    background: {C['surface_subtle']};
    border: 1px solid transparent;
    border-radius: {M['btn_radius']}px;
    selection-background-color: {C['accent_soft']};
    selection-color: {C['text_primary']};
}}
QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {C['border']}; }}
QSpinBox:focus, QDoubleSpinBox:focus {{
    background: {C['surface']};
    border: 1px solid {C['accent']};
}}
QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {C['text_secondary']};
    background: {C['window_bg']};
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    width: 18px;
    background: transparent;
    border: none;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-position: top right;
    border-top-right-radius: {M['btn_radius']}px;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-position: bottom right;
    border-bottom-right-radius: {M['btn_radius']}px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {C['hover']};
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url("{{A}}/chevron-up.svg");
    width: 10px;
    height: 10px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url("{{A}}/chevron-down.svg");
    width: 10px;
    height: 10px;
}}

QCheckBox {{ spacing: 7px; color: {C['text_primary']}; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: none;
    background: transparent;
    image: url("{{A}}/check-off.svg");
}}
QCheckBox::indicator:checked {{
    image: url("{{A}}/check-on.svg");
}}
QCheckBox:disabled {{ color: {C['disabled']}; }}

QRadioButton {{ spacing: 7px; }}
QRadioButton::indicator {{
    width: 16px; height: 16px;
    border: none;
    background: transparent;
    image: url("{{A}}/radio-off.svg");
}}
QRadioButton::indicator:checked {{
    image: url("{{A}}/radio-on.svg");
}}
QRadioButton:disabled {{ color: {C['disabled']}; }}

/* ---------------- 卡片 ---------------- */
QFrame[card="true"] {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: {M['card_radius']}px;
}}
QLabel#CardTitle {{
    font-size: {F['card_title']}pt;
    font-weight: bold;
    color: {C['text_primary']};
}}
QLabel#PanelTitle {{
    font-size: {F['panel_title']}pt;
    font-weight: bold;
}}
QLabel#Muted {{ color: {C['text_secondary']}; font-size: {F['secondary']}pt; }}
QLabel#FieldLabel {{ color: {C['text_secondary']}; font-size: {F['secondary']}pt; }}
QLabel#StatValue {{ color: {C['text_primary']}; font-size: {F['body']}pt; }}
QLabel#StatValueWarn {{ color: {C['warning']}; font-size: {F['body']}pt; }}
QLabel#Badge {{
    color: {C['accent_hover']};
    background: {C['accent_soft']};
    border-radius: 9px;
    padding: 2px 8px;
    font-size: {F['secondary']}pt;
}}
QFrame#HLine {{ background: {C['border']}; max-height: 1px; min-height: 1px; border: none; }}

/* ---------------- 左侧面板 ---------------- */
QWidget#Sidebar {{
    background: {C['sidebar_bg']};
    border-right: 1px solid {C['border']};
}}
QTreeView#ChapterTree {{
    background: transparent;
    border: none;
    show-decoration-selected: 0;
}}
QTreeView#ChapterTree::item {{
    height: {M['tree_row']}px;
    border-radius: 6px;
    padding-left: 2px;
    color: {C['text_primary']};
}}
QTreeView#ChapterTree::item:hover {{ background: #E3E8EA; }}
QTreeView#ChapterTree::item:selected {{
    background: {C['accent_soft']};
    color: {C['accent_hover']};
}}
QTreeView#ChapterTree::item:selected:active {{ background: {C['accent_soft']}; }}

/* ---------------- 状态栏 ---------------- */
QStatusBar {{
    background: {C['surface']};
    border-top: 1px solid {C['border']};
    min-height: {M['status_h']}px;
}}
QStatusBar QLabel {{ font-size: {F['status']}pt; color: {C['text_secondary']}; }}
QStatusBar QLabel#Dirty {{ color: {C['warning']}; }}
QStatusBar QLabel#Saved {{ color: {C['accent']}; }}
QStatusBar QLabel#ErrorText {{ color: {C['danger']}; }}

/* ---------------- 菜单 ---------------- */
QMenuBar {{
    background: {C['surface']};
    border-bottom: 1px solid {C['border']};
    padding: 2px 6px;
}}
QMenuBar::item {{ padding: 4px 10px; background: transparent; border-radius: 5px; }}
QMenuBar::item:selected {{ background: {C['surface_subtle']}; }}
QMenu {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 8px;
    padding: 5px;
}}
QMenu::item {{ padding: 6px 26px 6px 24px; border-radius: 5px; }}
QMenu::item:selected {{ background: {C['accent_soft']}; color: {C['accent_hover']}; }}
QMenu::item:disabled {{ color: {C['disabled']}; }}
QMenu::separator {{ height: 1px; background: {C['border']}; margin: 4px 8px; }}

/* ---------------- 滚动条 ---------------- */
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #CBD2D7; min-height: 30px; border-radius: 4px;
}}
QScrollBar::handle:vertical:hover {{ background: #B3BCC3; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* ---------------- 对话框 ---------------- */
QDialog {{ background: {C['window_bg']}; }}
QWidget#DialogHeader {{
    background: {C['surface']};
    border-bottom: 1px solid {C['border']};
}}
QLabel#DialogTitle {{ font-size: 12pt; font-weight: 600; }}
QLabel#StepActive {{
    color: #FFFFFF; background: {C['accent']};
    border-radius: 9px; padding: 1px 8px; font-size: {F['secondary']}pt;
}}
QLabel#StepIdle {{
    color: {C['text_secondary']}; background: {C['surface_subtle']};
    border-radius: 9px; padding: 1px 8px; font-size: {F['secondary']}pt;
}}
QListWidget {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 8px;
    padding: 4px;
}}
QListWidget::item {{ padding: 6px 8px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {C['accent_soft']}; color: {C['accent_hover']}; }}
QToolTip {{
    background: #2B3137;
    color: #FFFFFF;
    border: none;
    padding: 5px 8px;
    border-radius: 5px;
}}
"""

    return qss.replace("{A}", _ASSETS)


def card_style() -> str:
    return (f"QFrame {{ background: {C['surface']}; border: 1px solid {C['border']}; "
            f"border-radius: {M['card_radius']}px; }}")
