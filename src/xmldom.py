# -*- coding: utf-8 -*-
"""ChapFlow 的轻量 XML DOM

独立成模块，供 EPUB 引擎与 DOCX(OOXML) 解析共用。
特性：保留注释 / DOCTYPE / 处理指令 / 原始空白与属性顺序，回写时最大限度保真。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

# 1. 轻量 XML DOM（保留注释、DOCTYPE、处理指令与原始空白，回写时最大限度保真）
# =========================================================================

NAMED_ENTITIES = {
    "amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'", "nbsp": "\u00a0",
    "copy": "\u00a9", "reg": "\u00ae", "trade": "\u2122", "mdash": "\u2014",
    "ndash": "\u2013", "hellip": "\u2026", "lsquo": "\u2018", "rsquo": "\u2019",
    "ldquo": "\u201c", "rdquo": "\u201d", "middot": "\u00b7", "times": "\u00d7",
    "divide": "\u00f7", "deg": "\u00b0", "larr": "\u2190", "rarr": "\u2192",
    "uarr": "\u2191", "darr": "\u2193", "shy": "\u00ad", "emsp": "\u2003",
    "ensp": "\u2002", "thinsp": "\u2009",
}

_ENTITY_RE = None


def _entity_re():
    global _ENTITY_RE
    if _ENTITY_RE is None:
        import re
        _ENTITY_RE = re.compile(r"&(#x?[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*);")
    return _ENTITY_RE


class X:
    """极简 XML 节点。kind: root/element/text/comment/cdata/pi/doctype"""

    __slots__ = ("kind", "name", "attrs", "children", "value", "raw")

    def __init__(self, kind: str, name: str = "", attrs=None, children=None,
                 value: str = "", raw: bool = False):
        self.kind = kind
        self.name = name
        self.attrs: List[List[str]] = attrs if attrs is not None else []
        self.children: List["X"] = children if children is not None else []
        self.value = value
        self.raw = raw

    # -- 便捷访问 --------------------------------------------------------
    def get(self, name: str) -> Optional[str]:
        for k, v in self.attrs:
            if k == name:
                return v
        return None

    def get_loose(self, name: str) -> Optional[str]:
        for k, v in self.attrs:
            if k == name or local_name(k) == name:
                return v
        return None

    def set(self, name: str, value: str) -> None:
        for pair in self.attrs:
            if pair[0] == name or local_name(pair[0]) == name:
                pair[1] = value
                return
        self.attrs.append([name, value])

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "<X %s %s>" % (self.kind, self.name or self.value[:20])


def local_name(name: str) -> str:
    i = name.find(":")
    return name if i < 0 else name[i + 1:]


def _decode_entities(s: str) -> Tuple[str, bool]:
    if "&" not in s:
        return s, False
    unknown = [False]

    def repl(m):
        body = m.group(1)
        if body[0] == "#":
            try:
                code = int(body[2:], 16) if body[1:2].lower() == "x" else int(body[1:])
                return chr(code)
            except (ValueError, OverflowError):
                return m.group(0)
        if body in NAMED_ENTITIES:
            return NAMED_ENTITIES[body]
        unknown[0] = True
        return m.group(0)

    return _entity_re().sub(repl, s), unknown[0]


def escape_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_attr(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def _parse_start_tag(src: str, start: int):
    n = len(src)
    i = start + 1
    name_chars = []
    while i < n and src[i] not in " \t\r\n/>":
        name_chars.append(src[i])
        i += 1
    attrs: List[List[str]] = []
    self_close = False
    while i < n:
        while i < n and src[i] in " \t\r\n":
            i += 1
        if i < n and src[i] == ">":
            i += 1
            break
        if src.startswith("/>", i):
            self_close = True
            i += 2
            break
        an = []
        while i < n and src[i] not in " \t\r\n=/>":
            an.append(src[i])
            i += 1
        while i < n and src[i] in " \t\r\n":
            i += 1
        av = ""
        if i < n and src[i] == "=":
            i += 1
            while i < n and src[i] in " \t\r\n":
                i += 1
            if i < n and src[i] in "\"'":
                q = src[i]
                i += 1
                end = src.find(q, i)
                if end < 0:
                    end = n
                av = src[i:end]
                i = end + 1
            else:
                buf = []
                while i < n and src[i] not in " \t\r\n>":
                    buf.append(src[i])
                    i += 1
                av = "".join(buf)
        if an:
            attrs.append(["".join(an), _decode_entities(av)[0]])
    return "".join(name_chars), attrs, self_close, i


def parse_xml(src: str) -> X:
    root = X("root")
    stack: List[X] = [root]
    n = len(src)
    i = 0

    def add_text(s: str) -> None:
        if not s:
            return
        text, raw = _decode_entities(s)
        stack[-1].children.append(X("text", value=text, raw=raw))

    while i < n:
        lt = src.find("<", i)
        if lt < 0:
            add_text(src[i:])
            break
        if lt > i:
            add_text(src[i:lt])

        if src.startswith("<!--", lt):
            end = src.find("-->", lt + 4)
            stack[-1].children.append(X("comment", value=src[lt + 4: n if end < 0 else end]))
            i = n if end < 0 else end + 3
            continue
        if src.startswith("<![CDATA[", lt):
            end = src.find("]]>", lt + 9)
            stack[-1].children.append(X("cdata", value=src[lt + 9: n if end < 0 else end]))
            i = n if end < 0 else end + 3
            continue
        if src.startswith("<?", lt):
            end = src.find("?>", lt + 2)
            stack[-1].children.append(X("pi", value=src[lt + 2: n if end < 0 else end]))
            i = n if end < 0 else end + 2
            continue
        if src.startswith("<!", lt):
            e = lt + 2
            depth = 0
            while e < n:
                ch = src[e]
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                elif ch == ">" and depth <= 0:
                    break
                e += 1
            stack[-1].children.append(X("doctype", value=src[lt + 2:e]))
            i = e + 1
            continue
        if src.startswith("</", lt):
            end = src.find(">", lt + 2)
            name = src[lt + 2: n if end < 0 else end].strip()
            for k in range(len(stack) - 1, 0, -1):
                if stack[k].name == name:
                    del stack[k:]
                    break
            i = n if end < 0 else end + 1
            continue

        name, attrs, self_close, end = _parse_start_tag(src, lt)
        node = X("element", name=name, attrs=attrs)
        stack[-1].children.append(node)
        i = end
        if not self_close:
            stack.append(node)
    return root


def serialize_xml(node: X) -> str:
    if node.kind == "root":
        return "".join(serialize_xml(c) for c in node.children)
    if node.kind == "text":
        return node.value if node.raw else escape_text(node.value)
    if node.kind == "comment":
        return "<!--%s-->" % node.value
    if node.kind == "cdata":
        return "<![CDATA[%s]]>" % node.value
    if node.kind == "pi":
        return "<?%s?>" % node.value
    if node.kind == "doctype":
        return "<!%s>" % node.value
    attrs = "".join(' %s="%s"' % (k, escape_attr(v)) for k, v in node.attrs)
    if not node.children:
        return "<%s%s/>" % (node.name, attrs)
    inner = "".join(serialize_xml(c) for c in node.children)
    return "<%s%s>%s</%s>" % (node.name, attrs, inner, node.name)


# -- 树操作 ---------------------------------------------------------------

def is_el(n: Optional[X]) -> bool:
    return n is not None and n.kind == "element"


def child_els(node: X, name: Optional[str] = None) -> List[X]:
    return [c for c in node.children
            if c.kind == "element" and (name is None or local_name(c.name) == name)]


def first_child(node: X, name: str) -> Optional[X]:
    for c in node.children:
        if c.kind == "element" and local_name(c.name) == name:
            return c
    return None


def text_of(node: Optional[X]) -> str:
    if node is None:
        return ""
    out = []
    for c in node.children:
        if c.kind in ("text", "cdata"):
            out.append(c.value)
        elif c.kind == "element":
            out.append(text_of(c))
    return "".join(out)


def walk(node: X, fn) -> None:
    for c in list(node.children):
        if c.kind != "element":
            continue
        fn(c)
        walk(c, fn)


def el(name: str, attrs: Optional[Dict[str, str]] = None, children=None) -> X:
    return X("element", name=name,
             attrs=[[k, str(v)] for k, v in (attrs or {}).items()],
             children=children or [])


def tx(s: str) -> X:
    return X("text", value=s)


def detach(parent: X, child: X) -> bool:
    for i, c in enumerate(parent.children):
        if c is child:
            del parent.children[i]
            return True
    return False
