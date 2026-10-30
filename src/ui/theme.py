# -*- coding: utf-8 -*-
"""浅色 QSS 主题（颜色全部来自 tokens，避免散落硬编码）"""

import os

from .tokens import COLORS as C, METRICS as M, FONTS as F

# 勾选/单选指示器用 SVG，路径在 QSS 里必须是绝对路径
_ASSETS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets", "icons").replace("\\", "/")


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

QPushButton#Danger {{
    color: {C['danger']};
    background: {C['danger_soft']};
    border: 1px solid #F0CFCC;
}}
QPushButton#Danger:hover {{ background: #F7DEDC; }}
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
    border-radius: 6px;
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
QLineEdit#SearchInput {{ padding-left: 28px; }}

QComboBox {{
    min-height: {M['input_h']}px;
    padding: 0 10px;
    background: {C['surface_subtle']};
    border: 1px solid transparent;
    border-radius: {M['btn_radius']}px;
}}
QComboBox:hover {{ border-color: {C['border']}; }}
QComboBox:focus {{ background: {C['surface']}; border-color: {C['accent']}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 8px;
    padding: 4px;
    selection-background-color: {C['accent_soft']};
    selection-color: {C['accent_hover']};
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
