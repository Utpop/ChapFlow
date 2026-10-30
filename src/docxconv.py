# -*- coding: utf-8 -*-
"""
DOCX (OOXML) 解析与 XHTML 渲染
==============================

纯标准库实现：docx 本质是 ZIP + XML，复用 xmldom 解析，不依赖 python-docx。

支持：
* 标题样式（Heading N / 标题 N / outlineLvl）→ 章节切分与多级小标题
* 段落与行内格式（加粗 / 斜体 / 下划线）
* 项目符号与编号（numPr + numbering.xml 的 numFmt 判定 ul/ol，按 ilvl 嵌套）
* 表格（w:tbl → table，合并单元格按首个单元格降级）
* 图片（a:blip r:embed → media 文件，按 wp:extent 计算显示宽度）
* 元数据（docProps/core.xml 的标题 / 作者 / 语言）
"""

from __future__ import annotations

import os
import posixpath
import re
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from xmldom import X, child_els, escape_attr, escape_text, first_child, local_name, parse_xml, text_of, walk

DOCX_ERROR_MISSING = "不是有效的 DOCX 文件（缺少 word/document.xml）：%s"
DOCX_ERROR_ZIP = "不是有效的 DOCX 文件（无法按 ZIP 解包，可能已损坏）：%s"

MEDIA_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff", ".webp": "image/webp",
    ".emf": "image/emf", ".wmf": "image/wmf",
}

EMU_PER_PX = 9525


class DocxError(ValueError):
    """DOCX 处理相关的可读错误"""


class Run:
    __slots__ = ("text", "bold", "italic", "underline")

    def __init__(self, text: str = "", bold: bool = False, italic: bool = False,
                 underline: bool = False):
        self.text = text
        self.bold = bold
        self.italic = italic
        self.underline = underline

    def __repr__(self) -> str:  # pragma: no cover
        return "<Run %r>" % self.text


class Block:
    """块级元素：h（标题）/ p（段落）/ list / table / img"""

    __slots__ = ("kind", "level", "runs", "items", "rows", "media", "width")

    def __init__(self, kind: str, level: int = 0, runs: Optional[List[Run]] = None,
                 items=None, rows=None, media: str = "", width: int = 0):
        self.kind = kind
        self.level = level
        self.runs = runs or []
        self.items = items or []      # list: [{level, ordered, runs}]
        self.rows = rows or []        # table: [[str, ...], ...]
        self.media = media
        self.width = width

    @property
    def plain(self) -> str:
        return "".join(r.text for r in self.runs).strip()


class Chapter:
    __slots__ = ("title", "level", "blocks")

    def __init__(self, title: str, level: int = 1, blocks: Optional[List[Block]] = None):
        self.title = title
        self.level = level
        self.blocks = blocks or []

    def images(self) -> List[str]:
        out = []
        for b in self.blocks:
            if b.kind == "img" and b.media and b.media not in out:
                out.append(b.media)
        return out


class Docx:
    def __init__(self) -> None:
        self.path = ""
        self.title = ""
        self.author = ""
        self.language = "zh"
        self.chapters: List[Chapter] = []
        self.media: Dict[str, bytes] = {}   # word 内路径 -> 字节
        self.warnings: List[str] = []


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

def read_docx(path: str, split_level: int = 1) -> Docx:
    if not os.path.exists(path):
        raise DocxError("DOCX 文件不存在：%s" % path)
    if os.path.isdir(path):
        raise DocxError("这是一个文件夹，不是 DOCX 文件：%s" % path)
    if os.path.getsize(path) == 0:
        raise DocxError("DOCX 文件为空（0 字节）：%s" % path)

    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise DocxError(DOCX_ERROR_ZIP % path)

    names = set(zf.namelist())
    if "word/document.xml" not in names:
        raise DocxError(DOCX_ERROR_MISSING % path)

    doc = Docx()
    doc.path = path
    doc.title, doc.author, doc.language = _read_core(zf)
    rels = _read_rels(zf)
    styles = _read_styles(zf)
    numbering = _read_numbering(zf)

    body = _find_body(parse_xml(zf.read("word/document.xml").decode("utf-8", "replace")))
    if body is None:
        raise DocxError("DOCX 结构异常：word/document.xml 中没有 body")

    blocks = _read_blocks(body, rels, numbering, styles, doc)
    if not blocks:
        raise DocxError("DOCX 里没有任何可用的正文内容：%s" % path)

    doc.chapters = _split_chapters(blocks, split_level,
                                  doc.title or os.path.splitext(os.path.basename(path))[0])
    # media 只保留实际用到的图片
    used = {b.media for ch in doc.chapters for b in ch.blocks if b.kind == "img"}
    doc.media = {k: v for k, v in _read_media(zf, rels, used).items()}
    return doc


def _root(doc: X) -> X:
    """parse_xml 返回虚拟 root，取真正的文档元素"""
    for c in doc.children:
        if c.kind == "element":
            return c
    return doc


def _read_core(zf: zipfile.ZipFile) -> Tuple[str, str, str]:
    title = author = language = ""
    try:
        raw = zf.read("docProps/core.xml")
    except KeyError:
        return title, author, "zh"
    root = _root(parse_xml(raw.decode("utf-8", "replace")))

    def visit(n: X) -> None:
        nonlocal title, author, language
        ln = local_name(n.name)
        if ln == "title" and not title:
            title = text_of(n).strip()
        elif ln == "creator" and not author:
            author = text_of(n).strip()
        elif ln == "language" and not language:
            language = text_of(n).strip()

    def walk(n: X) -> None:
        for c in n.children:
            if c.kind == "element":
                visit(c)
                walk(c)

    walk(root)
    return title, author, language or "zh"


def _read_rels(zf: zipfile.ZipFile) -> Dict[str, Dict[str, str]]:
    """rId -> {target, type}；target 已解析为包内路径"""
    try:
        raw = zf.read("word/_rels/document.xml.rels")
    except KeyError:
        return {}
    root = _root(parse_xml(raw.decode("utf-8", "replace")))
    out: Dict[str, Dict[str, str]] = {}
    for rel in child_els(root, "Relationship"):
        rid = rel.get_loose("Id")
        if not rid:
            continue
        target = rel.get_loose("Target") or ""
        rel_type = rel.get_loose("Type") or ""
        if target.startswith(("http://", "https://", "mailto:")):
            out[rid] = {"target": target, "type": rel_type, "external": "1"}
            continue
        out[rid] = {"target": posixpath.normpath(posixpath.join("word", target)),
                    "type": rel_type, "external": ""}
    return out


def _read_styles(zf: zipfile.ZipFile) -> Dict[str, Tuple[str, Optional[str]]]:
    """styleId -> (样式名, basedOn)"""
    try:
        raw = zf.read("word/styles.xml")
    except KeyError:
        return {}
    root = _root(parse_xml(raw.decode("utf-8", "replace")))
    out: Dict[str, Tuple[str, Optional[str]]] = {}
    for st in child_els(root, "style"):
        sid = st.get_loose("styleId")
        if not sid:
            continue
        name_el = first_child(st, "name")
        based = first_child(st, "basedOn")
        out[sid] = (name_el.get_loose("val") if name_el is not None else sid,
                    based.get_loose("val") if based is not None else None)
    return out


def _read_numbering(zf: zipfile.ZipFile) -> Dict[str, Dict[int, bool]]:
    """numId -> {ilvl: 是否有序}"""
    try:
        raw = zf.read("word/numbering.xml")
    except KeyError:
        return {}
    root = _root(parse_xml(raw.decode("utf-8", "replace")))
    abstract: Dict[str, Dict[int, bool]] = {}
    for an in child_els(root, "abstractNum"):
        aid = an.get_loose("abstractNumId")
        if not aid:
            continue
        levels: Dict[int, bool] = {}
        for lvl in child_els(an, "lvl"):
            try:
                ilvl = int(lvl.get_loose("ilvl") or 0)
            except ValueError:
                ilvl = 0
            fmt = first_child(lvl, "numFmt")
            fmt_val = (fmt.get_loose("val") or "") if fmt is not None else ""
            levels[ilvl] = fmt_val not in ("bullet", "none", "")
        abstract[aid] = levels

    out: Dict[str, Dict[int, bool]] = {}
    for num in child_els(root, "num"):
        nid = num.get_loose("numId")
        aid_el = first_child(num, "abstractNumId")
        if not nid or aid_el is None:
            continue
        out[nid] = abstract.get(aid_el.get_loose("val") or "", {})
    return out


def _find_body(doc: X) -> Optional[X]:
    hit: List[X] = []

    def visit(n: X) -> None:
        if not hit and local_name(n.name) == "body":
            hit.append(n)

    walk(doc, visit)
    return hit[0] if hit else None


def _read_media(zf: zipfile.ZipFile, rels: Dict[str, Dict[str, str]],
                used: set) -> Dict[str, bytes]:
    out: Dict[str, bytes] = {}
    for rid, info in rels.items():
        target = info["target"]
        if target not in used:
            continue
        try:
            out[target] = zf.read(target)
        except KeyError:
            pass
    return out


# ---------------------------------------------------------------------------
# 块解析
# ---------------------------------------------------------------------------

def _read_blocks(body: X, rels, numbering, styles, doc: Docx) -> List[Block]:
    blocks: List[Block] = []
    pending_list: List[Dict[str, Any]] = []

    def flush_list() -> None:
        if pending_list:
            blocks.append(Block("list", items=list(pending_list)))
            pending_list.clear()

    for node in body.children:
        if node.kind != "element":
            continue
        ln = local_name(node.name)
        if ln == "p":
            parsed = _parse_paragraph(node, rels, numbering, styles, doc)
            for b in parsed:
                if b.kind == "li":
                    pending_list.append({"level": b.level, "ordered": b.width > 0,
                                         "runs": b.runs})
                    continue
                flush_list()
                blocks.append(b)
        elif ln == "tbl":
            flush_list()
            table = _parse_table(node)
            if table is not None:
                blocks.append(table)
    flush_list()
    return blocks


def _parse_paragraph(p: X, rels, numbering, styles, doc: Docx) -> List[Block]:
    ppr = first_child(p, "pPr")
    style_id = None
    outline = None
    num_id = None
    ilvl = 0
    if ppr is not None:
        ps = first_child(ppr, "pStyle")
        style_id = ps.get_loose("val") if ps is not None else None
        ol = first_child(ppr, "outlineLvl")
        if ol is not None:
            try:
                outline = int(ol.get_loose("val") or 0)
            except ValueError:
                outline = None
        npr = first_child(ppr, "numPr")
        if npr is not None:
            nid = first_child(npr, "numId")
            lv = first_child(npr, "ilvl")
            num_id = nid.get_loose("val") if nid is not None else None
            try:
                ilvl = int(lv.get_loose("val") or 0) if lv is not None else 0
            except ValueError:
                ilvl = 0

    runs, images = _parse_runs(p, rels)

    level = _heading_level(style_id, outline, styles)
    if level is not None:
        title = "".join(r.text for r in runs).strip()
        if title:
            return [Block("h", level=level, runs=runs)]

    out: List[Block] = []
    for media, width in images:
        out.append(Block("img", media=media, width=width))

    text = "".join(r.text for r in runs).strip()
    if text:
        if num_id and num_id != "0":
            ordered = bool(numbering.get(num_id, {}).get(ilvl, False))
            out.append(Block("li", level=ilvl, runs=runs, width=1 if ordered else 0))
        else:
            out.append(Block("p", runs=runs))
    return out


def _heading_level(style_id: Optional[str], outline: Optional[int],
                   styles: Dict[str, Tuple[str, Optional[str]]]) -> Optional[int]:
    if outline is not None and 0 <= outline <= 8:
        return outline + 1
    if not style_id:
        return None

    # 沿 basedOn 链最多找 3 层，命中 Heading N / 标题 N 即返回
    cur, depth = style_id, 0
    while cur and depth < 3:
        name, based = styles.get(cur, (cur, None))
        text = "%s %s" % (cur, name)
        m = re.search(r"(?:heading|标题|titre|überschrift)\s*([1-9])", text, re.I)
        if m:
            return int(m.group(1))
        if re.fullmatch(r"[1-9]", cur.strip()):
            return int(cur.strip())
        cur, depth = based, depth + 1
    return None


def _parse_runs(p: X, rels) -> Tuple[List[Run], List[Tuple[str, int]]]:
    runs: List[Run] = []
    images: List[Tuple[str, int]] = []

    def visit(node: X, bold=False, italic=False, underline=False) -> None:
        for c in node.children:
            if c.kind != "element":
                continue
            ln = local_name(c.name)
            if ln == "r":
                b, i, u = _run_format(c)
                visit(c, bold or b, italic or i, underline or u)
            elif ln == "t":
                runs.append(Run(c.value if c.value else text_of(c), bold, italic, underline))
            elif ln == "tab":
                runs.append(Run(" ", bold, italic, underline))
            elif ln == "br":
                runs.append(Run("\n", bold, italic, underline))
            elif ln in ("drawing", "pict", "object"):
                img = _find_image(c, rels)
                if img:
                    images.append(img)
            elif ln in ("pPr", "rPr", "bookmarkStart", "bookmarkEnd", "proofErr"):
                continue
            else:
                visit(c, bold, italic, underline)

    visit(p)
    return runs, images


def _run_format(r: X) -> Tuple[bool, bool, bool]:
    rpr = first_child(r, "rPr")
    if rpr is None:
        return False, False, False

    def flag(name: str) -> bool:
        el0 = first_child(rpr, name)
        if el0 is None:
            return False
        v = el0.get("val")
        return v not in ("0", "false", "off")

    return flag("b"), flag("i"), flag("u")


def _find_image(node: X, rels) -> Optional[Tuple[str, int]]:
    width = 0
    rid = None

    def walk(n: X) -> None:
        nonlocal rid, width
        for c in n.children:
            if c.kind != "element":
                continue
            ln = local_name(c.name)
            if ln == "extent" and not width:
                try:
                    width = int(round(int(c.get_loose("cx") or 0) / EMU_PER_PX))
                except ValueError:
                    width = 0
            if ln == "blip" and not rid:
                for k, v in c.attrs:
                    if local_name(k) == "embed":
                        rid = v
                        break
            walk(c)

    walk(node)
    if not rid:
        return None
    info = rels.get(rid)
    if not info or info.get("external"):
        return None
    return info["target"], width


def _parse_table(tbl: X) -> Optional[Block]:
    rows: List[List[str]] = []
    for tr in child_els(tbl, "tr"):
        cells = []
        for tc in child_els(tr, "tc"):
            parts = []
            for p in child_els(tc, "p"):
                t = text_of(p).strip()
                if t:
                    parts.append(t)
            cells.append(" ".join(parts))
        if cells:
            rows.append(cells)
    if not rows:
        return None
    return Block("table", rows=rows)


# ---------------------------------------------------------------------------
# 章节切分
# ---------------------------------------------------------------------------

def _split_chapters(blocks: List[Block], split_level: int, fallback_title: str) -> List[Chapter]:
    split_level = max(1, min(6, int(split_level)))
    chapters: List[Chapter] = []
    current: Optional[Chapter] = None
    preface: List[Block] = []

    for b in blocks:
        if b.kind == "h" and b.level <= split_level:
            current = Chapter(b.plain or "未命名章节", b.level)
            chapters.append(current)
            continue
        if current is None:
            preface.append(b)
        else:
            current.blocks.append(b)

    if chapters and any(b.plain for b in preface):
        chapters.insert(0, Chapter("前言", 1, [b for b in preface if b.plain or b.kind == "img"]))
    elif not chapters:
        chapters = [Chapter(fallback_title, 1, blocks)]

    return chapters


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

def render_runs(runs: List[Run]) -> str:
    out = []
    for r in runs:
        text = escape_text(r.text).replace("\n", "<br/>")
        if not text:
            continue
        if r.bold:
            text = "<strong>%s</strong>" % text
        if r.italic:
            text = "<em>%s</em>" % text
        if r.underline:
            text = '<span style="text-decoration:underline">%s</span>' % text
        out.append(text)
    return "".join(out)


def render_blocks(chapter: Chapter, href_for_image, heading_base: int = 1) -> str:
    """把章节内容渲染成 XHTML 片段；章节标题由调用方输出为 h1"""
    out: List[str] = []
    i = 0
    blocks = chapter.blocks
    while i < len(blocks):
        b = blocks[i]
        if b.kind == "h":
            lv = min(6, max(2, b.level - heading_base + 1))
            out.append("<h%d>%s</h%d>" % (lv, render_runs(b.runs), lv))
        elif b.kind == "p":
            out.append("<p>%s</p>" % render_runs(b.runs))
        elif b.kind == "img":
            href = href_for_image(b.media)
            if href:
                style = ' style="max-width:100%%;width:%dpx"' % b.width if b.width else ' style="max-width:100%%"'
                out.append('<p class="img"><img src="%s" alt=""%s/></p>'
                           % (escape_attr(href), style))
        elif b.kind == "table":
            out.append(_render_table(b.rows))
        elif b.kind == "list":
            out.append(_render_list(b.items))
        i += 1
    return "\n".join(x for x in out if x)


def _render_table(rows: List[List[str]]) -> str:
    body = []
    for idx, row in enumerate(rows):
        tag = "th" if idx == 0 else "td"
        body.append("<tr>%s</tr>" % "".join(
            "<%s>%s</%s>" % (tag, escape_text(c), tag) for c in row))
    return '<table class="docx-table">\n%s\n</table>' % "\n".join(body)


def _render_list(items: List[Dict[str, Any]]) -> str:
    """按 ilvl 嵌套生成 ul/ol"""
    out: List[str] = []
    stack: List[Tuple[int, bool]] = []

    def close_to(level: int) -> None:
        while stack and stack[-1][0] > level:
            _, ordered = stack.pop()
            out.append("</li></%s>" % ("ol" if ordered else "ul"))

    for it in items:
        level = max(0, int(it["level"]))
        ordered = bool(it["ordered"])
        while stack and stack[-1][0] > level:
            _, o = stack.pop()
            out.append("</li></%s>" % ("ol" if o else "ul"))
        if stack and stack[-1][0] == level:
            if stack[-1][1] == ordered:
                out.append("</li>")
            else:
                _, o = stack.pop()
                out.append("</li></%s>" % ("ol" if o else "ul"))
        if not stack or stack[-1][0] < level:
            tag = "ol" if ordered else "ul"
            out.append("<%s>" % tag)
            stack.append((level, ordered))
        out.append("<li>%s" % render_runs(it["runs"]))
    while stack:
        _, o = stack.pop()
        out.append("</li></%s>" % ("ol" if o else "ul"))
    return "".join(out)


def wrap_xhtml(body: str, title: str, *, epub_version: str = "3.0",
               language: str = "zh", css_href: Optional[str] = None) -> str:
    is_epub2 = str(epub_version).startswith("2")
    doctype = ('<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
               '"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">' if is_epub2 else "<!DOCTYPE html>")
    charset = ('<meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>'
               if is_epub2 else '<meta charset="utf-8"/>')
    css = '<link rel="stylesheet" type="text/css" href="%s"/>' % escape_text(css_href) if css_href else ""
    return """<?xml version="1.0" encoding="utf-8"?>
%s
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="%s" lang="%s">
<head>
%s
<title>%s</title>
<style type="text/css">
body{margin:0 5%%;line-height:1.7;}
h1{font-size:1.4em;margin:1.4em 0 1em;text-align:center;}
p{margin:0.5em 0;text-indent:2em;}
p.img{text-indent:0;text-align:center;}
table.docx-table{border-collapse:collapse;margin:1em 0;width:100%%;}
table.docx-table th,table.docx-table td{border:1px solid #bbb;padding:0.3em 0.5em;}
ul,ol{margin:0.5em 0 0.5em 1.6em;padding:0;}
li{margin:0.2em 0;}
</style>
%s
</head>
<body>
<div class="docx-chapter">
<h1>%s</h1>
%s
</div>
</body>
</html>
""" % (doctype, escape_text(language), escape_text(language), charset,
       escape_text(title), css, escape_text(title), body)
