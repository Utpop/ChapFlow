# -*- coding: utf-8 -*-
"""分章规则的本地记忆
====================

三样东西都存 QSettings（与主界面「正文页的标题也一起改」同一份存储）：

1. **上次用的设置** —— 关掉对话框再打开，规则还在，不用每次重设；
2. **规则预设** —— 内置几条常见写法（第X章 / 第X回 / 章回節集卷混用 / Chapter N /
   番外特典短篇），外加用户自己「保存当前…」攒下来的，可删；
3. **输入历史** —— 附加规则、正则表达式这两个输入框敲过什么，记最近若干条，可回选。

只存纯数据（dict / list / str），不碰 Qt 控件，方便单独测试。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QSettings

ORG = "ChapFlow"
APP = "ChapFlow"

HISTORY_MAX = 12

#: 面板参数的默认值（与 SplitRulePanel 构造时一致）
DEFAULT_PARAMS: Dict[str, Any] = {
    "mode": "simple",
    "indent": False,
    "prefix": "第",
    "numerals": "any",
    "unit": "章",
    "extra": "",
    "pattern": "",
    "size": 4000,
    "builtin": True,
}


def _s() -> QSettings:
    # 显式指定 IniFormat + UserScope：落到 %APPDATA% 下的人类可读 ini 文件，
    # 而不是注册表；测试里也能用 QSettings.setPath 整体隔离到临时目录。
    from PySide6.QtCore import QSettings as _Q
    return _Q(_Q.IniFormat, _Q.UserScope, ORG, APP)


# ---------------------------------------------------------------------------
# 内置预设
# ---------------------------------------------------------------------------

_SPECIAL = (r"^\s*(序章|序曲|楔子|引子|前言|后记|後記|尾声|尾聲|番外|外传|外傳|特典|"
            r"特别篇|特別篇|短篇|附录|附錄|简介|簡介|正文|前传|前傳)")

BUILTIN_PRESETS: List[Dict[str, Any]] = [
    {
        "name": "第X章（简体，最常用）",
        "params": dict(DEFAULT_PARAMS, mode="simple", prefix="第", numerals="any",
                       unit="章", builtin=True),
    },
    {
        "name": "第X回 / 第X节",
        "params": dict(DEFAULT_PARAMS, mode="simple", prefix="第", numerals="any",
                       unit="回", builtin=True),
    },
    {
        "name": "第X卷 / 第X部（作大标题）",
        "params": dict(DEFAULT_PARAMS, mode="simple", prefix="第", numerals="any",
                       unit="卷", builtin=True),
    },
    {
        "name": "章 / 回 / 節 / 卷 混用（正则，简繁都认）",
        "params": dict(DEFAULT_PARAMS, mode="regex",
                       pattern=r"^\s*[第卷]?\s*[0-9一二三四五六七八九十百千零〇两]+"
                               r"\s*[章回節节話话折卷部集篇]"),
    },
    {
        "name": "Chapter N（正则）",
        "params": dict(DEFAULT_PARAMS, mode="regex",
                       pattern=r"^\s*Chapter\s*[0-9]+"),
    },
    {
        "name": "番外 / 特典 / 短篇 也单独成章",
        "params": dict(DEFAULT_PARAMS, mode="simple", prefix="第", numerals="any",
                       unit="章", builtin=True, extra=_SPECIAL),
    },
]


def builtin_presets() -> List[Dict[str, Any]]:
    import copy
    return copy.deepcopy(BUILTIN_PRESETS)


# ---------------------------------------------------------------------------
# 上次用的设置
# ---------------------------------------------------------------------------

def load_last() -> Optional[Dict[str, Any]]:
    """读上次的分章设置；没存过返回 None"""
    raw = _s().value("split/last", "")
    return _decode_dict(raw)


def save_last(params: Dict[str, Any]) -> None:
    if not params:
        return
    try:
        q = _s()
        q.setValue("split/last", json.dumps(params, ensure_ascii=False))
        q.sync()          # 否则同进程里另开的实例读到的还是旧值
    except (TypeError, ValueError):
        pass


# ---------------------------------------------------------------------------
# 用户保存的预设
# ---------------------------------------------------------------------------

def load_custom() -> List[Dict[str, Any]]:
    raw = _s().value("split/presets", "")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    out: List[Dict[str, Any]] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("params"), dict):
                name = str(item.get("name") or "").strip()
                if name:
                    out.append({"name": name, "params": dict(DEFAULT_PARAMS,
                                                             **item["params"])})
    return out


def save_custom(items: List[Dict[str, Any]]) -> None:
    try:
        q = _s()
        q.setValue("split/presets", json.dumps(items, ensure_ascii=False))
        q.sync()
    except (TypeError, ValueError):
        pass


def add_custom(name: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """同名覆盖，返回更新后的列表"""
    name = (name or "").strip()
    if not name:
        return load_custom()
    items = [it for it in load_custom() if it["name"] != name]
    items.append({"name": name, "params": dict(DEFAULT_PARAMS, **params)})
    save_custom(items)
    return items


def remove_custom(name: str) -> List[Dict[str, Any]]:
    items = [it for it in load_custom() if it["name"] != name]
    save_custom(items)
    return items


def presets() -> List[Dict[str, Any]]:
    """内置在前、自定义在后"""
    return builtin_presets() + load_custom()


# ---------------------------------------------------------------------------
# 输入历史
# ---------------------------------------------------------------------------

def load_history(kind: str) -> List[str]:
    raw = _s().value("split/history-%s" % kind, "")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(x) for x in data if str(x).strip()]


def push_history(kind: str, value: str) -> List[str]:
    """把一条输入记到最前面（去重、限长），返回新的历史"""
    value = (value or "").strip()
    if not value:
        return load_history(kind)
    items = [x for x in load_history(kind) if x != value]
    items.insert(0, value)
    del items[HISTORY_MAX:]
    try:
        q = _s()
        q.setValue("split/history-%s" % kind, json.dumps(items, ensure_ascii=False))
        q.sync()
    except (TypeError, ValueError):
        pass
    return items


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------

def _decode_dict(raw: Any) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    merged = dict(DEFAULT_PARAMS)
    for key, val in data.items():
        if key in DEFAULT_PARAMS and isinstance(val, type(DEFAULT_PARAMS[key])):
            merged[key] = val
        elif key == "size":
            try:
                merged[key] = int(val)
            except (TypeError, ValueError):
                pass
    return merged


__all__ = ["DEFAULT_PARAMS", "BUILTIN_PRESETS", "builtin_presets", "load_last",
           "save_last", "load_custom", "save_custom", "add_custom", "remove_custom",
           "presets", "load_history", "push_history"]
