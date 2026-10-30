#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ChapFlow —— EPUB 目录编辑器 · 核心引擎与命令行
=====================================

纯 Python 标准库实现（无第三方依赖），供桌面端 GUI / CLI 复用。

能力
----
* 读取 EPUB2 / EPUB3，解析 nav.xhtml 与 toc.ncx 两套目录
* 目录模型：大标题（分组，无链接）与章节（指向正文文件）两级/多级树
* 操作：改名、移动（拖动/指定序号等价）、删除章节、增删大标题
* 回写时同步：nav.xhtml、toc.ncx（playOrder 重排）、OPF manifest / spine / guide、
  spine 阅读顺序、landmarks 中的失效引用
* 导出后自检：mimetype 位置与压缩方式、manifest 完整性、链接可达性、playOrder 连续性

命令行示例
----------
    python chapflow.py info book.epub
    python chapflow.py tree book.epub
    python chapflow.py edit book.epub -o out.epub --op "rename 1.2 新的标题"
    python chapflow.py edit book.epub -o out.epub --ops ops.json
    python chapflow.py validate out.epub
"""

from __future__ import annotations

import argparse
import copy
import io
import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote, unquote

__version__ = "1.0.0"

MIMETYPE_CONTENT = b"application/epub+zip"
XHTML11_DOCTYPE = ('<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
                   '"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">')

# =========================================================================
from xmldom import (  # noqa: E402
    X, escape_attr, escape_text, parse_xml, serialize_xml, local_name,
    is_el, child_els, first_child, text_of, walk, el, tx, detach,
)



# =========================================================================
# 2. 路径工具
# =========================================================================

def dirname(p: str) -> str:
    return posixpath.dirname(p)


def basename(p: str) -> str:
    return posixpath.basename(p)


def split_anchor(href: str) -> Tuple[str, str]:
    i = href.find("#")
    return (href, "") if i < 0 else (href[:i], href[i + 1:])


def resolve_href(base_dir: str, href: str) -> Optional[Tuple[str, str]]:
    """把相对 href 解析成 (ZIP 内绝对路径, 锚点)；外链返回 None"""
    if not href:
        return None
    if len(href) > 1 and href[1] == ":" or href.startswith(("http:", "https:", "mailto:", "tel:")):
        return None
    path_part, anchor = split_anchor(href)
    try:
        path_part = unquote(path_part)
    except Exception:
        pass
    if path_part.startswith("/"):
        segs = path_part[1:].split("/")
        out: List[str] = []
    else:
        segs = path_part.split("/")
        out = [s for s in base_dir.split("/") if s] if base_dir else []
    for s in segs:
        if s in ("", "."):
            continue
        if s == "..":
            if out:
                out.pop()
        else:
            out.append(s)
    return "/".join(out), anchor


def relative_path(from_dir: str, to_file: str) -> str:
    a = [s for s in from_dir.split("/") if s] if from_dir else []
    b = [s for s in to_file.split("/") if s]
    i = 0
    while i < len(a) and i < len(b) and a[i] == b[i]:
        i += 1
    parts = [".."] * (len(a) - i) + b[i:]
    return "/".join(parts)


def encode_href(p: str) -> str:
    """只编码空白等必须编码的字符，保留中文直写（EPUB 允许 UTF-8 IRI）"""
    return "/".join(quote(seg, safe="!$&'()*+,;=:@-._~") for seg in p.split("/"))


# =========================================================================
# 3. TOC 模型
# =========================================================================

class Node:
    """目录条目：kind = 'group'（大标题，无链接） / 'doc'（章节，指向正文）"""

    __slots__ = ("kind", "title", "file", "anchor", "href_raw", "children", "uid")

    _seq = 0

    def __init__(self, kind: str, title: str = "", file: Optional[str] = None,
                 anchor: str = "", href_raw: str = ""):
        Node._seq += 1
        self.uid = "n%d" % Node._seq
        self.kind = kind
        self.title = title
        self.file = file
        self.anchor = anchor or ""
        self.href_raw = href_raw or ""
        self.children: List["Node"] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.uid, "kind": self.kind, "title": self.title,
            "file": self.file, "anchor": self.anchor,
            "children": [c.to_dict() for c in self.children],
        }


def clone_toc(items: List[Node]) -> List[Node]:
    out = []
    for n in items:
        c = Node(n.kind, n.title, n.file, n.anchor, n.href_raw)
        c.uid = n.uid
        c.children = clone_toc(n.children)
        out.append(c)
    return out


def count_nodes(items: List[Node]) -> Dict[str, int]:
    groups = docs = 0
    stack = list(items)
    while stack:
        n = stack.pop()
        if n.kind == "group":
            groups += 1
        else:
            docs += 1
        stack.extend(n.children)
    return {"groups": groups, "docs": docs, "total": groups + docs}


def first_file_of(node: Node) -> Optional[Tuple[str, str]]:
    if node.file:
        return node.file, node.anchor
    for c in node.children:
        r = first_file_of(c)
        if r:
            return r
    return None


def flatten_files(items: List[Node]) -> List[str]:
    out: List[str] = []
    for n in items:
        if n.kind != "group" and n.file and n.file not in out:
            out.append(n.file)
        out.extend(flatten_files(n.children))
    return out


def collect_files(items: List[Node]) -> set:
    s = set()
    stack = list(items)
    while stack:
        n = stack.pop()
        if n.file:
            s.add(n.file)
        stack.extend(n.children)
    return s


def parse_path(path: str) -> List[int]:
    """'1.2.3' -> [0,1,2]（用户看到的是 1 基序号）"""
    if not path or not str(path).strip():
        raise ValueError("路径不能为空")
    out = []
    for seg in str(path).replace(" ", "").split("."):
        if not seg:
            continue
        if not seg.isdigit():
            raise ValueError("非法路径：%s" % path)
        idx = int(seg) - 1
        if idx < 0:
            raise ValueError("序号从 1 开始：%s" % path)
        out.append(idx)
    if not out:
        raise ValueError("非法路径：%s" % path)
    return out


def locate(items: List[Node], path: str) -> Tuple[Node, List[Node], int]:
    idxs = parse_path(path)
    cur_list = items
    parent: List[Node] = items
    node: Optional[Node] = None
    for depth, idx in enumerate(idxs):
        if idx >= len(cur_list):
            raise ValueError("路径超出范围：%s" % path)
        node = cur_list[idx]
        if depth == len(idxs) - 1:
            return node, parent, idx
        parent = node.children
        cur_list = node.children
    raise ValueError("非法路径：%s" % path)


def parse_target(items: List[Node], target: str) -> Tuple[List[Node], int]:
    """
    移动目标位置 -> (容器列表, 插入下标 0 基)
    允许：
      '3'      根层级第 3 个位置（插到现第 3 项之前）
      '2.1'    节点 2 内部第 1 个位置
      'end'    根末尾
      'end:2'  节点 2 内部末尾
    """
    t = str(target).strip()
    if t in ("end", "last", ""):
        return items, len(items)
    if t.startswith("end:") or t.startswith("last:"):
        node, _, _ = locate(items, t.split(":", 1)[1])
        return node.children, len(node.children)
    parts = [p for p in t.split(".") if p]
    if len(parts) == 1:
        pos = int(parts[0])
        if pos < 1:
            raise ValueError("序号从 1 开始")
        return items, min(pos - 1, len(items))
    container, _, _ = locate(items, ".".join(parts[:-1]))
    pos = int(parts[-1])
    if pos < 1:
        raise ValueError("序号从 1 开始")
    return container.children, min(pos - 1, len(container.children))


def move_node(items: List[Node], path: str, target: str) -> Tuple[Node, str]:
    """把 path 指向的条目移动到 target 位置；返回 (节点, 说明)"""
    node, parent_list, idx = locate(items, path)
    container, ins = parse_target(items, target)

    # 不能把节点移进自己的后代
    if container is node.children or _list_inside(node, container):
        raise ValueError("不能把条目移动到它自己的内部")

    del parent_list[idx]
    ins = max(0, min(ins, len(container)))
    container.insert(ins, node)

    # 生成人类可读位置
    for i, n in enumerate(container):
        if n is node:
            if container is items:
                return node, "第 %d 位（顶级）" % (i + 1)
            return node, "第 %d 位" % (i + 1)
    return node, target


def _list_inside(node: Node, container: List[Node]) -> bool:
    if container is node.children:
        return True
    for c in node.children:
        if _list_inside(c, container):
            return True
    return False


# =========================================================================
# 4. EPUB 读取
# =========================================================================

class EpubBook:
    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as f:
            self.raw = f.read()
        if not self.raw:
            raise ValueError("文件为空，不是有效的 EPUB：%s" % path)
        try:
            self.zf = zipfile.ZipFile(io.BytesIO(self.raw))
        except zipfile.BadZipFile:
            raise ValueError("不是有效的 EPUB 文件（无法按 ZIP 解包，可能已损坏或并非 EPUB）：%s" % path)
        self.infos: Dict[str, zipfile.ZipInfo] = {i.filename: i for i in self.zf.infolist()}
        self.order: List[str] = [i.filename for i in self.zf.infolist()]
        self.warnings: List[Dict[str, str]] = []
        self._load()

    # -- 基础 ------------------------------------------------------------
    def read(self, name: str) -> bytes:
        return self.zf.read(name)

    def text(self, name: str) -> str:
        data = self.zf.read(name)
        for enc in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", "replace")

    def has(self, name: str) -> bool:
        return name in self.infos

    # -- 解析 ------------------------------------------------------------
    def _load(self) -> None:
        if "META-INF/encryption.xml" in self.infos:
            self.warnings.append({"level": "warn",
                                  "text": "检测到 META-INF/encryption.xml（可能是 DRM 或字体混淆），修改后部分阅读器可能校验失败"})
        if "mimetype" not in self.infos:
            self.warnings.append({"level": "warn", "text": "缺少 mimetype 文件"})
        else:
            mt = self.text("mimetype").strip()
            if mt != "application/epub+zip":
                self.warnings.append({"level": "warn", "text": "mimetype 内容异常：%s" % mt})
            if self.order and self.order[0] != "mimetype":
                self.warnings.append({"level": "warn", "text": "mimetype 不是第一个条目（导出时自动修复）"})

        if "META-INF/container.xml" not in self.infos:
            raise ValueError("不是有效的 EPUB：缺少 META-INF/container.xml")
        cdoc = parse_xml(self.text("META-INF/container.xml"))
        rootfiles: List[Tuple[str, str]] = []
        walk(cdoc, lambda n: rootfiles.append((n.get("full-path") or "", n.get("media-type") or ""))
             if local_name(n.name) == "rootfile" else None)
        rootfiles = [(p, mt) for p, mt in rootfiles if p]
        if not rootfiles:
            raise ValueError("container.xml 中没有 rootfile")

        self.opf_path = rootfiles[0][0]
        for p, mt in rootfiles:
            if "oebps-package" in mt or "epub" in mt:
                self.opf_path = p
                break
        if self.opf_path not in self.infos:
            raise ValueError("OPF 文件不存在：%s" % self.opf_path)

        self.opf_text = self.text(self.opf_path)
        self.opf_doc = parse_xml(self.opf_text)
        self.pkg = _find_first(self.opf_doc, "package")
        if self.pkg is None:
            raise ValueError("OPF 中找不到 package 元素")
        self.opf_dir = dirname(self.opf_path)
        self.version = self.pkg.get("version") or "2.0"

        self.metadata = self._read_metadata()
        self.manifest = self._read_manifest()
        self.spine = self._read_spine()

        # nav
        nav_item = next((m for m in self.manifest if "nav" in (m["properties"] or "").split()), None)
        if nav_item is None:
            nav_item = next((m for m in self.manifest
                             if m["media_type"] == "application/xhtml+xml"
                             and posixpath.basename(m["path"]).lower() in ("nav.xhtml", "nav.html")), None)
        self.nav_path = nav_item["path"] if nav_item else None

        # ncx
        spine_el = first_child(self.pkg, "spine")
        ncx_item = None
        if spine_el is not None:
            toc_id = spine_el.get("toc")
            if toc_id:
                ncx_item = next((m for m in self.manifest if m["id"] == toc_id), None)
        if ncx_item is None:
            ncx_item = next((m for m in self.manifest
                             if m["media_type"] == "application/x-dtbncx+xml"), None)
        self.ncx_path = ncx_item["path"] if ncx_item else None

        # TOC
        self.toc: List[Node] = []
        self.toc_source = "none"
        if self.nav_path and self.has(self.nav_path):
            try:
                self.toc = parse_nav(self.text(self.nav_path), dirname(self.nav_path))
                if self.toc:
                    self.toc_source = "nav"
            except Exception as exc:  # pragma: no cover
                self.warnings.append({"level": "warn", "text": "nav 解析失败：%s" % exc})
        if not self.toc and self.ncx_path and self.has(self.ncx_path):
            try:
                self.toc = parse_ncx(self.text(self.ncx_path), dirname(self.ncx_path))
                if self.toc:
                    self.toc_source = "nav+ncx" if self.toc_source == "nav" else "ncx"
            except Exception as exc:  # pragma: no cover
                self.warnings.append({"level": "warn", "text": "NCX 解析失败：%s" % exc})
        if not self.toc:
            for s in self.spine:
                if s["item"]:
                    self.toc.append(Node("doc", _title_from_name(s["item"]["path"]),
                                         s["item"]["path"], "", ""))
            self.toc_source = "spine"
            self.warnings.append({"level": "info", "text": "原书目录为空，已按阅读顺序（spine）生成基础目录"})

    def _read_metadata(self) -> Dict[str, Any]:
        meta = {"title": "", "creators": [], "language": "", "identifier": "",
                "modified": "", "publisher": ""}
        md = first_child(self.pkg, "metadata")
        if md is None:
            return meta
        for c in child_els(md):
            ln = local_name(c.name)
            if ln == "title" and not meta["title"]:
                meta["title"] = text_of(c).strip()
            elif ln == "creator":
                meta["creators"].append(text_of(c).strip())
            elif ln == "language" and not meta["language"]:
                meta["language"] = text_of(c).strip()
            elif ln == "identifier" and not meta["identifier"]:
                meta["identifier"] = text_of(c).strip()
            elif ln == "publisher" and not meta["publisher"]:
                meta["publisher"] = text_of(c).strip()
            elif ln == "meta" and c.get("property") == "dcterms:modified":
                meta["modified"] = text_of(c).strip()
        return meta

    def _read_manifest(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        mf = first_child(self.pkg, "manifest")
        if mf is None:
            return out
        for it in child_els(mf, "item"):
            href = it.get("href") or ""
            res = resolve_href(self.opf_dir, href)
            out.append({
                "id": it.get("id") or "",
                "href": href,
                "path": res[0] if res else href,
                "media_type": it.get("media-type") or "",
                "properties": it.get("properties") or "",
                "node": it,
                "parent": mf,
            })
        return out

    def _read_spine(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        sp = first_child(self.pkg, "spine")
        if sp is None:
            return out
        by_id = {m["id"]: m for m in self.manifest}
        for ir in child_els(sp, "itemref"):
            idref = ir.get("idref") or ""
            out.append({"idref": idref, "node": ir, "parent": sp,
                        "linear": ir.get("linear") or "yes",
                        "item": by_id.get(idref)})
        return out

    # -- 封面 ------------------------------------------------------------
    def cover(self) -> Optional[Tuple[bytes, str]]:
        """返回 (图片字节, media_type)；没有封面或读取失败返回 None"""
        cover_id = None
        md = first_child(self.pkg, "metadata")
        if md is not None:
            for m in child_els(md, "meta"):
                if (m.get("name") or "") == "cover":
                    cover_id = m.get("content")
        item = None
        if cover_id:
            item = next((x for x in self.manifest if x["id"] == cover_id), None)
        if item is None:
            item = next((x for x in self.manifest
                         if "cover-image" in (x["properties"] or "").split()), None)
        if item is None or item["media_type"] not in (
                "image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml"):
            return None
        if item["path"] not in self.infos:
            return None
        try:
            return self.read(item["path"]), item["media_type"]
        except KeyError:
            return None

    # -- 报告 ------------------------------------------------------------
    def info(self) -> Dict[str, Any]:
        counts = count_nodes(self.toc)
        return {
            "file": self.path,
            "size": len(self.raw),
            "version": self.version,
            "title": self.metadata["title"],
            "creators": self.metadata["creators"],
            "language": self.metadata["language"],
            "identifier": self.metadata["identifier"],
            "opf": self.opf_path,
            "nav": self.nav_path,
            "ncx": self.ncx_path,
            "toc_source": self.toc_source,
            "counts": counts,
            "spine": len(self.spine),
            "manifest": len(self.manifest),
            "warnings": self.warnings,
        }


def _find_first(root: X, name: str) -> Optional[X]:
    hit = None

    def visit(n: X) -> None:
        nonlocal hit
        if hit is None and local_name(n.name) == name:
            hit = n

    walk(root, visit)
    return hit


def _title_from_name(path: str) -> str:
    return os.path.splitext(basename(path))[0] or path


# =========================================================================
# 5. nav / ncx 解析
# =========================================================================

def _find_toc_nav(doc: X) -> Optional[X]:
    hit = None

    def visit(n: X) -> None:
        nonlocal hit
        if hit is not None or local_name(n.name) != "nav":
            return
        t = n.get_loose("type") or ""
        if "toc" in t.split():
            hit = n

    walk(doc, visit)
    if hit is None:
        def visit2(n: X) -> None:
            nonlocal hit
            if hit is None and local_name(n.name) == "nav" and n.get("id") == "toc":
                hit = n

        walk(doc, visit2)
    return hit


def _norm_title(s: str) -> str:
    """只压掉换行/制表符与多余半角空格，保留全角空格等原有排版"""
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return re.sub(r" {2,}", " ", s).strip()


def parse_nav(text: str, nav_dir: str) -> List[Node]:
    doc = parse_xml(text)
    nav = _find_toc_nav(doc)
    if nav is None:
        return []
    ol = first_child(nav, "ol")
    if ol is None:
        return []
    return _parse_nav_list(ol, nav_dir)


def _parse_nav_list(ol: X, nav_dir: str) -> List[Node]:
    out: List[Node] = []
    for li in child_els(ol, "li"):
        a = first_child(li, "a")
        span = first_child(li, "span")
        label = a if a is not None else span
        href = a.get("href") if a is not None else None
        title = _norm_title(text_of(label)) if label is not None else ""
        if href:
            res = resolve_href(nav_dir, href)
            node = Node("doc", title, res[0] if res else None, res[1] if res else "", href)
        else:
            node = Node("group", title)
        sub = first_child(li, "ol")
        if sub is not None:
            node.children = _parse_nav_list(sub, nav_dir)
        out.append(node)
    return out


def parse_ncx(text: str, ncx_dir: str) -> List[Node]:
    doc = parse_xml(text)
    nav_map = _find_first(doc, "navMap")
    if nav_map is None:
        return []
    return _parse_nav_points(nav_map, ncx_dir)


def _parse_nav_points(parent: X, base_dir: str) -> List[Node]:
    out: List[Node] = []
    for np in child_els(parent, "navPoint"):
        label = first_child(np, "navLabel")
        title = ""
        if label is not None:
            t = first_child(label, "text")
            title = _norm_title(text_of(t if t is not None else label))
        content = first_child(np, "content")
        src = content.get("src") if content is not None else None
        if src:
            res = resolve_href(base_dir, src)
            node = Node("doc", title, res[0] if res else None, res[1] if res else "", src)
        else:
            node = Node("group", title)
        node.children = _parse_nav_points(np, base_dir)
        out.append(node)
    return out


# =========================================================================
# 6. nav / ncx 生成
# =========================================================================

def href_of(node: Node, base_dir: str) -> str:
    if not node.file:
        return ""
    if node.href_raw:
        res = resolve_href(base_dir, node.href_raw)
        if res and res[0] == node.file and (res[1] or "") == (node.anchor or ""):
            return node.href_raw
    rel = encode_href(relative_path(base_dir, node.file))
    return rel + ("#" + node.anchor if node.anchor else "")


def _build_nav_ol(items: List[Node], pad: str, base_dir: str) -> X:
    ol = el("ol")
    for it in items:
        ol.children.append(tx("\n" + pad + "  "))
        li = el("li")
        if it.kind == "group" or not it.file:
            li.children.append(el("span", children=[tx(it.title)]))
        else:
            li.children.append(el("a", {"href": href_of(it, base_dir)}, [tx(it.title)]))
        if it.children:
            li.children.append(_build_nav_ol(it.children, pad + "  ", base_dir))
            li.children.append(tx("\n" + pad + "  "))
        ol.children.append(li)
    ol.children.append(tx("\n" + pad))
    return ol


def max_depth(items: List[Node]) -> int:
    d = 0
    for n in items:
        d = max(d, 1 + (max_depth(n.children) if n.children else 0))
    return d


def _build_ncx_points(items: List[Node], base_dir: str, ctx: Dict[str, Any], pad: str) -> List[X]:
    """
    NCX 规则要点：
    * 每个 navPoint 必须带 content（DTD 强制）
    * 指向同一目标的多个 navPoint 必须共用同一个 playOrder，且序号连续（1,1,2,3…）
      所以大标题与其首个子项共用 playOrder；无目标的分组无法表达，降级为扁平（子项上提）
    """
    out: List[X] = []
    for it in items:
        target = (it.file, it.anchor) if it.file else first_file_of(it)
        if not target:
            # 空分组：NCX 里没有合法写法，子项上提、自身丢弃
            if it.children:
                out.extend(_build_ncx_points(it.children, base_dir, ctx, pad))
            ctx["flattened"] = ctx.get("flattened", 0) + 1
            continue

        if it.file:
            src = href_of(it, base_dir)
        else:
            rel = encode_href(relative_path(base_dir, target[0]))
            src = rel + ("#" + target[1] if target[1] else "")

        by_src = ctx.setdefault("by_src", {})
        if src in by_src:
            play = by_src[src]
        else:
            ctx["play"] += 1
            play = ctx["play"]
            by_src[src] = play

        np = el("navPoint", {"id": "navPoint-%d" % ctx["seq"], "playOrder": str(play)})
        ctx["seq"] += 1
        np.children.append(tx("\n" + pad + "  "))
        np.children.append(el("navLabel", children=[el("text", children=[tx(it.title)])]))
        np.children.append(tx("\n" + pad + "  "))
        np.children.append(el("content", {"src": src}))
        if it.children:
            np.children.extend(_build_ncx_points(it.children, base_dir, ctx, pad + "  "))
            np.children.append(tx("\n" + pad + "  "))
        else:
            np.children.append(tx("\n" + pad))
        out.append(np)
        out.append(tx("\n" + pad))
    return out


def write_nav_text(nav_text: str, items: List[Node], nav_dir: str) -> Optional[str]:
    doc = parse_xml(nav_text)
    nav = _find_toc_nav(doc)
    if nav is None:
        body = _find_first(doc, "body")
        if body is None:
            return None
        attrs = {"id": "toc"}
        if "xmlns:epub" in nav_text:
            attrs = {"epub:type": "toc", "id": "toc"}
        nav = el("nav", attrs, [el("h1", children=[tx("目录")])])
        body.children.insert(0, nav)
        body.children.insert(1, tx("\n"))

    old_ol = first_child(nav, "ol")
    new_ol = _build_nav_ol(items, "", nav_dir)
    if old_ol is not None:
        for i, c in enumerate(nav.children):
            if c is old_ol:
                nav.children[i] = new_ol
                break
    else:
        nav.children.extend([tx("\n"), new_ol, tx("\n")])

    _prune_dead_refs(doc, nav_dir, collect_files(items))
    return serialize_xml(doc)


def build_nav_text(items: List[Node], nav_dir: str, title: str) -> str:
    body = _build_nav_ol(items, "", nav_dir)
    doc = el("html", {
        "xmlns": "http://www.w3.org/1999/xhtml",
        "xmlns:epub": "http://www.idpf.org/2007/ops",
    }, [
        tx("\n"),
        el("head", children=[tx("\n    "), el("title", children=[tx(title or "目录")]), tx("\n  ")]),
        tx("\n  "),
        el("body", children=[
            tx("\n    "),
            el("nav", {"epub:type": "toc", "id": "toc"}, [
                tx("\n      "), el("h1", children=[tx(title or "目录")]), tx("\n      "),
                body, tx("\n    "),
            ]),
            tx("\n  "),
        ]),
        tx("\n"),
    ])
    return '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n' + serialize_xml(doc) + "\n"


def write_ncx_text(ncx_text: str, items: List[Node], ncx_dir: str) -> Optional[str]:
    doc = parse_xml(ncx_text)
    nav_map = _find_first(doc, "navMap")
    if nav_map is None:
        return None
    ctx = {"play": 0, "seq": 1}
    points = _build_ncx_points(items, ncx_dir, ctx, "")

    # 保留 navMap 前后的空白格式
    head_ws = "\n"
    tail_ws = "\n  "
    if nav_map.children and nav_map.children[0].kind == "text":
        head_ws = nav_map.children[0].value
    nav_map.children = [tx(head_ws)] + points + [tx(tail_ws)]

    def fix_depth(n: X) -> None:
        if local_name(n.name) == "meta" and n.get("name") == "dtb:depth":
            n.set("content", str(max(1, max_depth(items))))

    walk(doc, fix_depth)
    return serialize_xml(doc)


def build_ncx_text(items: List[Node], ncx_dir: str, metadata: Dict[str, Any]) -> str:
    ctx = {"play": 0, "seq": 1}
    points = _build_ncx_points(items, ncx_dir, ctx, "")
    doc = el("ncx", {"xmlns": "http://www.daisy.org/z3986/2005/ncx/", "version": "2005-1"}, [
        tx("\n  "),
        el("head", children=[
            tx("\n    "),
            el("meta", {"name": "dtb:uid", "content": metadata.get("identifier") or "urn:uuid:epub-toc-editor"}),
            tx("\n    "),
            el("meta", {"name": "dtb:depth", "content": str(max(1, max_depth(items)))}),
            tx("\n    "),
            el("meta", {"name": "dtb:totalPageCount", "content": "0"}),
            tx("\n    "),
            el("meta", {"name": "dtb:maxPageNumber", "content": "0"}),
            tx("\n  "),
        ]),
        tx("\n  "),
        el("docTitle", children=[el("text", children=[tx(metadata.get("title") or "")])]),
        tx("\n  "),
        el("navMap", children=[tx("\n")] + points + [tx("\n  ")]),
        tx("\n"),
    ])
    return '<?xml version="1.0" encoding="utf-8"?>\n' + serialize_xml(doc) + "\n"


def _prune_dead_refs(doc: X, base_dir: str, alive: set) -> None:
    """清理 landmarks / page-list 等非 toc 导航里指向已删文件的条目"""
    parents: Dict[int, X] = {}

    def index(node: X) -> None:
        for c in node.children:
            parents[id(c)] = node
            if c.kind == "element":
                index(c)

    index(doc)
    victims: List[X] = []

    def visit(n: X) -> None:
        if local_name(n.name) != "nav":
            return
        nav_type = n.get_loose("type") or ""
        if "toc" in nav_type.split():
            return
        for a in _iter_els(n, "a"):
            href = a.get("href")
            if not href:
                continue
            res = resolve_href(base_dir, href)
            if res and res[0] not in alive:
                victims.append(a)

    walk(doc, visit)
    for a in victims:
        li = a
        while li is not None and local_name(li.name) != "li":
            li = parents.get(id(li))
        target = li or a
        parent = parents.get(id(target))
        if parent is not None:
            detach(parent, target)


def _iter_els(root: X, name: str) -> Iterable[X]:
    for c in root.children:
        if c.kind != "element":
            continue
        if local_name(c.name) == name:
            yield c
        yield from _iter_els(c, name)


# =========================================================================
# 7. 导出（同步 OPF / spine / nav / ncx 并重新打包）
# =========================================================================

def _drop_empty_groups(items: List[Node], counter: Optional[List[int]] = None):
    """EPUB 规范不允许“只有标题、没有子项”的分组（span 必须跟非空 ol），导出时丢弃"""
    counter = counter if counter is not None else [0]
    out: List[Node] = []
    for n in items:
        n.children = _drop_empty_groups(n.children, counter) if n.children else []
        if n.kind == "group" and not n.children:
            counter[0] += 1
            continue
        if n.kind == "doc" and not n.file:
            counter[0] += 1
            continue
        out.append(n)
    return out


def _normalize_xhtml(text: str, epub2: bool) -> str:
    """合并异构版本时统一 DOCTYPE 与 charset 声明"""
    if epub2:
        text = re.sub(r"<!DOCTYPE\s+html[^>]*>", XHTML11_DOCTYPE, text, count=1, flags=re.I)
        text = re.sub(r"<meta\s+charset=\"[^\"]*\"\s*/?>",
                      '<meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>',
                      text, count=1, flags=re.I)
    else:
        text = re.sub(r"<!DOCTYPE\s+html[^>]*>", "<!DOCTYPE html>", text, count=1, flags=re.I)
        text = re.sub(r"<meta\s+http-equiv=\"Content-Type\"[^>]*?/?>",
                      '<meta charset="utf-8"/>', text, count=1, flags=re.I)
    return text


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _opf_prefix(pkg: X, wanted: str) -> str:
    for c in child_els(pkg):
        if local_name(c.name) == wanted:
            i = c.name.find(":")
            return "" if i < 0 else c.name[:i + 1]
    return ""


def _ensure_manifest_item(pkg: X, item_id: str, href: str, media_type: str,
                          properties: str = "") -> Optional[X]:
    mf = first_child(pkg, "manifest")
    if mf is None:
        return None
    items = child_els(mf, "item")
    existing = next((it for it in items if it.get("id") == item_id), None)
    if existing is not None and (existing.get("href") or "") == href:
        existing.set("media-type", media_type)
        if properties:
            existing.set("properties", properties)
        return existing
    if existing is not None:
        # id 冲突但指向别的文件：生成新 id，绝不覆盖原有资源
        used = {it.get("id") for it in items}
        k = 2
        new_id = "%s-%d" % (item_id, k)
        while new_id in used:
            k += 1
            new_id = "%s-%d" % (item_id, k)
        item_id = new_id
    node = el(_opf_prefix(pkg, "item") + "item",
              {"id": item_id, "href": href, "media-type": media_type})
    if properties:
        node.attrs.append(["properties", properties])
    mf.children.extend([tx("\n    "), node, tx("\n  ")])
    return node


def _remove_manifest_item(pkg: X, item_node: X) -> None:
    mf = first_child(pkg, "manifest")
    if mf is not None:
        detach(mf, item_node)


def _remove_spine_ref(pkg: X, idref: str) -> None:
    sp = first_child(pkg, "spine")
    if sp is None:
        return
    for ir in list(child_els(sp, "itemref")):
        if ir.get("idref") == idref:
            detach(sp, ir)


def _reorder_spine(pkg: X, manifest: List[Dict[str, Any]], doc_order: List[str]) -> None:
    sp = first_child(pkg, "spine")
    if sp is None:
        return
    itemrefs = child_els(sp, "itemref")
    by_path = {m["path"]: m["id"] for m in manifest}
    wanted: List[str] = []
    for p in doc_order:
        mid = by_path.get(p)
        if mid and mid not in wanted:
            wanted.append(mid)
    if not wanted:
        return
    wanted_set = set(wanted)
    orig_ids = [ir.get("idref") for ir in itemrefs]
    if not any(i in wanted_set for i in orig_ids):
        return
    picked = {ir.get("idref"): ir for ir in itemrefs if ir.get("idref") in wanted_set}
    ordered = [picked[i] for i in wanted if i in picked]
    if [ir.get("idref") for ir in itemrefs if ir.get("idref") in wanted_set] == [i for i in wanted if i in picked]:
        return  # 顺序已一致，避免无谓重排

    new_children: List[X] = []
    head_ws = "\n    "
    if sp.children and sp.children[0].kind == "text":
        head_ws = sp.children[0].value
    new_children.append(tx(head_ws))
    inserted = False
    for rid in orig_ids:
        if rid in wanted_set:
            if not inserted:
                for ir in ordered:
                    new_children.extend([ir, tx("\n    ")])
                inserted = True
        else:
            ir = next((x for x in itemrefs if x.get("idref") == rid), None)
            if ir is not None:
                new_children.extend([ir, tx("\n    ")])
    if new_children and new_children[-1].kind == "text":
        new_children[-1] = tx("\n  ")
    sp.children = new_children


def _fix_unique_identifier(pkg: X) -> bool:
    """unique-identifier 指向不存在的 id 时，改为第一个 dc:identifier 的 id"""
    md = first_child(pkg, "metadata")
    if md is None:
        return False
    ids = child_els(md, "identifier")
    if not ids:
        return False
    uid = pkg.get("unique-identifier")
    if uid and any((el.get("id") or "") == uid for el in ids):
        return False
    first = ids[0]
    if not first.get("id"):
        first.set("id", "bookid")
    pkg.set("unique-identifier", first.get("id"))
    return True


def _update_modified(pkg: X, stamp: str) -> None:
    md = first_child(pkg, "metadata")
    if md is None:
        return
    for c in child_els(md, "meta"):
        if c.get("property") == "dcterms:modified":
            c.children = [tx(stamp)]
            return


def _rename_in_html(html: str, title: str) -> str:
    import re
    safe = escape_text(title)
    out = re.sub(r"(<title[^>]*>)([\s\S]*?)(</title>)", lambda m: m.group(1) + safe + m.group(3),
                 html, count=1, flags=re.I)
    def repl(m):
        inner = m.group(3)
        if "<" in inner:
            return m.group(0)
        return m.group(1) + safe + m.group(4)
    out = re.sub(r"(<h([1-6])[^>]*>)([\s\S]*?)(</h\2>)", repl, out, count=1, flags=re.I)
    return out


def export_epub(book: EpubBook, toc: List[Node], removed: List[Dict[str, str]],
                sync_spine: bool = True, sync_heading: bool = False,
                new_files: Optional[Dict[str, bytes]] = None,
                new_manifest: Optional[List[Dict[str, str]]] = None,
                spine_append: Optional[List[str]] = None,
                touch_modified: bool = True,
                prune_broken: bool = True) -> Tuple[bytes, Dict[str, Any]]:
    """按编辑后的目录重建 EPUB，返回 (字节流, 报告)"""
    report: Dict[str, Any] = {"removed_files": [], "created_nav": False,
                              "created_ncx": False, "reordered_spine": False,
                              "synced_headings": 0, "added_files": [], "warnings": [],
                              "pruned_manifest": 0, "pruned_spine": 0, "fixed_uid": False}
    new_files = new_files or {}
    new_manifest = new_manifest or []
    spine_append = spine_append or []

    files: Dict[str, bytes] = {}
    order: List[str] = [n for n in book.order]
    for name in order:
        files[name] = book.read(name)

    opf_doc = parse_xml(book.opf_text)
    pkg = _find_first(opf_doc, "package")
    if pkg is None:
        raise ValueError("OPF 解析失败")
    # 用新文档重新收集 manifest
    manifest_nodes = []
    mf = first_child(pkg, "manifest")
    if mf is not None:
        for it in child_els(mf, "item"):
            res = resolve_href(book.opf_dir, it.get("href") or "")
            manifest_nodes.append({
                "id": it.get("id") or "", "href": it.get("href") or "",
                "path": res[0] if res else (it.get("href") or ""),
                "media_type": it.get("media-type") or "",
                "properties": it.get("properties") or "",
                "node": it,
            })

    # 1) 物理删除
    alive = collect_files(toc)
    for r in removed:
        if r.get("path") in alive:
            continue
        m = next((x for x in manifest_nodes if x["path"] == r.get("path") or x["id"] == r.get("item_id")), None)
        if m is not None:
            _remove_manifest_item(pkg, m["node"])
            _remove_spine_ref(pkg, m["id"])
            manifest_nodes.remove(m)
        guide = first_child(pkg, "guide")
        if guide is not None:
            for ref in list(child_els(guide, "reference")):
                res = resolve_href(book.opf_dir, ref.get("href") or "")
                if res and res[0] == r.get("path"):
                    detach(guide, ref)
        if r.get("path") in files:
            del files[r["path"]]
            report["removed_files"].append(r["path"])
        if r.get("path") in order:
            order.remove(r["path"])

    # 1.5) 丢弃空大标题（规范无合法写法）
    drop_counter = [0]
    toc = _drop_empty_groups(clone_toc(toc), drop_counter)
    if drop_counter[0]:
        report["warnings"].append("有 %d 个空大标题未写入目录（大标题必须至少含一个子项）"
                                 % drop_counter[0])

    # 2) 目录文件路径（EPUB2 不凭空创建 nav，避免 OEBPS 2.0 校验报错）
    is_epub2 = str(book.version).startswith("2")
    nav_path = book.nav_path
    ncx_path = book.ncx_path
    if not nav_path and not is_epub2:
        nav_path = (book.opf_dir + "/nav.xhtml") if book.opf_dir else "nav.xhtml"
        report["created_nav"] = True
    if not ncx_path:
        ncx_path = (book.opf_dir + "/toc.ncx") if book.opf_dir else "toc.ncx"
        report["created_ncx"] = True
    nav_dir = dirname(nav_path) if nav_path else book.opf_dir
    ncx_dir = dirname(ncx_path)

    # 3) nav
    if nav_path:
        if report["created_nav"] or nav_path not in files:
            nav_text = build_nav_text(toc, nav_dir, book.metadata.get("title") or "目录")
        else:
            old = files[nav_path].decode("utf-8", "replace")
            nav_text = write_nav_text(old, toc, nav_dir)
            if nav_text is None:
                nav_text = build_nav_text(toc, nav_dir, book.metadata.get("title") or "目录")
        files[nav_path] = nav_text.encode("utf-8")
        if nav_path not in order:
            order.append(nav_path)

    # 4) ncx
    if book.ncx_path and ncx_path in files:
        old_ncx = files[ncx_path].decode("utf-8", "replace")
        ncx_text = write_ncx_text(old_ncx, toc, ncx_dir) or build_ncx_text(toc, ncx_dir, book.metadata)
    else:
        ncx_text = build_ncx_text(toc, ncx_dir, book.metadata)
    files[ncx_path] = ncx_text.encode("utf-8")
    if ncx_path not in order:
        order.append(ncx_path)

    # 5) manifest 中确保 nav / ncx 存在
    spine_el = first_child(pkg, "spine")
    nav_node = None
    nav_id = "nav"
    if nav_path:
        nav_id = next((m["id"] for m in manifest_nodes if m["path"] == nav_path), "nav")
        nav_node = _ensure_manifest_item(pkg, nav_id, relative_path(book.opf_dir, nav_path),
                                         "application/xhtml+xml", "nav")
    ncx_id = next((m["id"] for m in manifest_nodes if m["path"] == ncx_path), "ncx")
    ncx_node = _ensure_manifest_item(pkg, ncx_id, relative_path(book.opf_dir, ncx_path),
                                     "application/x-dtbncx+xml")
    if spine_el is not None and ncx_node is not None:
        spine_el.set("toc", ncx_node.get("id") or ncx_id)

    # 5.5) 新增文件（如 TXT 转换出的章节）
    for path, data in new_files.items():
        files[path] = data
        if path not in order:
            order.append(path)
        report["added_files"].append(path)

    for item in new_manifest:
        _ensure_manifest_item(pkg, item["id"], relative_path(book.opf_dir, item["path"]),
                              item.get("media_type") or "application/xhtml+xml",
                              item.get("properties") or "")

    if spine_append and spine_el is not None:
        pre = _opf_prefix(pkg, "itemref")
        existing = {ir.get("idref") for ir in child_els(spine_el, "itemref")}
        for idref in spine_append:
            if idref in existing:
                continue
            spine_el.children.extend([tx("\n    "), el(pre + "itemref", {"idref": idref}), tx("\n  ")])
            existing.add(idref)

    # 5.8) 修复：清理指向不存在文件的 manifest 项与失效 spine 引用
    if prune_broken:
        mf_el = first_child(pkg, "manifest")
        if mf_el is not None:
            for it in list(child_els(mf_el, "item")):
                res = resolve_href(book.opf_dir, it.get("href") or "")
                path = res[0] if res else ""
                if path and path not in files:
                    _remove_spine_ref(pkg, it.get("id") or "")
                    detach(mf_el, it)
                    report["pruned_manifest"] += 1
            valid_ids = {it.get("id") for it in child_els(mf_el, "item")}
            sp_el = first_child(pkg, "spine")
            if sp_el is not None:
                for ir in list(child_els(sp_el, "itemref")):
                    if (ir.get("idref") or "") not in valid_ids:
                        detach(sp_el, ir)
                        report["pruned_spine"] += 1
        report["fixed_uid"] = _fix_unique_identifier(pkg)

    # 6) spine 阅读顺序
    if sync_spine:
        mf2 = []
        mf_el = first_child(pkg, "manifest")
        if mf_el is not None:
            for it in child_els(mf_el, "item"):
                res = resolve_href(book.opf_dir, it.get("href") or "")
                mf2.append({"id": it.get("id") or "", "path": res[0] if res else (it.get("href") or "")})
        before = [ir.get("idref") for ir in child_els(first_child(pkg, "spine"), "itemref")] \
            if first_child(pkg, "spine") is not None else []
        _reorder_spine(pkg, mf2, flatten_files(toc))
        after = [ir.get("idref") for ir in child_els(first_child(pkg, "spine"), "itemref")] \
            if first_child(pkg, "spine") is not None else []
        report["reordered_spine"] = before != after

    # 7) 正文标题同步（可选）
    if sync_heading:
        done = set()

        def visit(n: Node) -> None:
            if n.file and not n.children and n.file not in done:
                done.add(n.file)
                if n.file in files:
                    try:
                        old = files[n.file].decode("utf-8")
                        new = _rename_in_html(old, n.title)
                        if new != old:
                            files[n.file] = new.encode("utf-8")
                            report["synced_headings"] += 1
                    except (UnicodeDecodeError, KeyError):
                        report["warnings"].append("正文标题同步失败：%s" % n.file)

        for it in _walk_nodes(toc):
            visit(it)

    # 8) 时间戳
    if touch_modified:
        _update_modified(pkg, _iso_now())

    # 9) OPF 回写
    files[book.opf_path] = serialize_xml(opf_doc).encode("utf-8")

    # 10) 打包（mimetype 必须第一且不压缩）
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo("mimetype", date_time=datetime.now().timetuple()[:6])
        info.compress_type = zipfile.ZIP_STORED
        info.external_attr = 0o644 << 16
        zf.writestr(info, MIMETYPE_CONTENT)
        for name in order:
            if name == "mimetype" or name not in files:
                continue
            src_info = book.infos.get(name)
            new_info = zipfile.ZipInfo(name, date_time=(src_info.date_time if src_info
                                                       else datetime.now().timetuple()[:6]))
            new_info.compress_type = zipfile.ZIP_DEFLATED
            new_info.external_attr = (src_info.external_attr if src_info else (0o644 << 16))
            zf.writestr(new_info, files[name])
    data = buf.getvalue()
    report["bytes"] = len(data)
    report["spine_count"] = len(child_els(first_child(pkg, "spine"), "itemref")) \
        if first_child(pkg, "spine") is not None else 0
    return data, report


def _walk_nodes(items: List[Node]) -> Iterable[Node]:
    for n in items:
        yield n
        yield from _walk_nodes(n.children)


# =========================================================================
# 8. 自检
# =========================================================================

def validate_bytes(data: bytes) -> List[Dict[str, str]]:
    issues: List[Dict[str, str]] = []

    def ok(t): issues.append({"level": "ok", "text": t})
    def warn(t): issues.append({"level": "warn", "text": t})
    def fail(t): issues.append({"level": "error", "text": t})

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception as exc:
        fail("无法读取生成的文件：%s" % exc)
        return issues
    infos = zf.infolist()
    names = [i.filename for i in infos]
    fileset = set(names)

    if not names or names[0] != "mimetype":
        fail("mimetype 不是第一个条目")
    else:
        info = infos[0]
        if info.compress_type != zipfile.ZIP_STORED:
            fail("mimetype 被压缩（OCF 规范要求 STORE）")
        elif info.extra:
            fail("mimetype 带 extra field（规范不允许）")
        else:
            ok("mimetype 位置、存储方式、extra field 均合规")

    if "META-INF/container.xml" not in fileset:
        fail("缺少 META-INF/container.xml")
        return issues
    cdoc = parse_xml(zf.read("META-INF/container.xml").decode("utf-8", "replace"))
    opf_path = None
    for p, _mt in [(n.get("full-path"), n.get("media-type")) for n in _iter_els(cdoc, "rootfile")]:
        if p:
            opf_path = p
            break
    if not opf_path or opf_path not in fileset:
        fail("OPF 缺失")
        return issues

    opf_doc = parse_xml(zf.read(opf_path).decode("utf-8", "replace"))
    pkg = _find_first(opf_doc, "package")
    if pkg is None:
        fail("OPF 无 package 元素")
        return issues
    opf_dir = dirname(opf_path)

    manifest = []
    mf = first_child(pkg, "manifest")
    if mf is not None:
        for it in child_els(mf, "item"):
            res = resolve_href(opf_dir, it.get("href") or "")
            manifest.append({"id": it.get("id") or "", "path": res[0] if res else "",
                             "media_type": it.get("media-type") or "",
                             "properties": it.get("properties") or ""})

    missing = [m["path"] for m in manifest if m["path"] not in fileset]
    if missing:
        fail("manifest 引用缺失：%s" % ", ".join(missing[:5]))
    else:
        ok("manifest %d 个资源全部存在" % len(manifest))

    spine = []
    sp = first_child(pkg, "spine")
    if sp is not None:
        ids = {m["id"] for m in manifest}
        for ir in child_els(sp, "itemref"):
            if ir.get("idref") not in ids:
                fail("spine 引用不存在的 id：%s" % ir.get("idref"))
            else:
                spine.append(ir.get("idref"))
    ok("spine 共 %d 项" % len(spine))

    nav_item = next((m for m in manifest if "nav" in (m["properties"] or "").split()), None)
    if nav_item is None:
        warn("未找到 EPUB3 nav 文档")
    elif nav_item["path"] not in fileset:
        fail("nav 文档不存在：%s" % nav_item["path"])
    else:
        doc = parse_xml(zf.read(nav_item["path"]).decode("utf-8", "replace"))
        nav = _find_toc_nav(doc)
        if nav is None:
            fail("nav 文档缺少 epub:type=\"toc\"")
        else:
            links = [a.get("href") for a in _iter_els(nav, "a")]
            bad = []
            for h in links:
                res = resolve_href(dirname(nav_item["path"]), h or "")
                if not res or res[0] not in fileset:
                    bad.append(h or "")
            if bad:
                fail("nav 链接失效：%s" % ", ".join(bad[:5]))
            else:
                ok("nav.xhtml 目录 %d 个链接全部可达" % len(links))

    ncx_item = next((m for m in manifest if m["media_type"] == "application/x-dtbncx+xml"), None)
    if ncx_item is None:
        warn("未找到 toc.ncx（EPUB2 阅读器可能无目录）")
    elif ncx_item["path"] not in fileset:
        fail("toc.ncx 不存在")
    else:
        doc = parse_xml(zf.read(ncx_item["path"]).decode("utf-8", "replace"))
        points = list(_iter_els(doc, "navPoint"))
        link_bad = 0
        missing_content = 0
        orders: List[int] = []
        for p in points:
            try:
                orders.append(int(p.get("playOrder") or 0))
            except ValueError:
                orders.append(0)
            c = first_child(p, "content")
            if c is None:
                missing_content += 1
                continue
            res = resolve_href(dirname(ncx_item["path"]), c.get("src") or "")
            if not res or res[0] not in fileset:
                link_bad += 1
        # 同一目标可共用 playOrder（1,1,2,3…），但必须非递减且集合为 1..max
        order_bad = 0
        if not orders or min(orders) < 1:
            order_bad += 1
        if orders != sorted(orders):
            order_bad += 1
        if orders and sorted(set(orders)) != list(range(1, max(orders) + 1)):
            order_bad += 1
        if missing_content:
            fail("NCX 有 %d 个 navPoint 缺少 content" % missing_content)
        if order_bad:
            fail("NCX playOrder 序号异常（不连续或乱序）")
        else:
            ok("NCX %d 个节点，playOrder 连续" % len(points))
        if link_bad:
            fail("NCX 有 %d 个链接失效" % link_bad)
        else:
            ok("NCX 链接全部可达")
    # 元数据（真实检查，不做装饰）
    md = first_child(pkg, "metadata")
    version = pkg.get("version") or ""
    if md is not None:
        if version.startswith("3"):
            mod = ""
            for m in child_els(md, "meta"):
                if (m.get("property") or "") == "dcterms:modified":
                    mod = text_of(m).strip()
                    break
            if not mod:
                fail("EPUB3 缺少 dcterms:modified")
            elif not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", mod):
                fail("dcterms:modified 格式不合法：%s" % mod)
            else:
                ok("dcterms:modified 格式正确：%s" % mod)
        uid = pkg.get("unique-identifier")
        if uid:
            hit = any((x.get("id") or "") == uid for x in child_els(md))
            if hit:
                ok("unique-identifier 指向存在的 dc:identifier")
            else:
                fail("unique-identifier 找不到对应的 dc:identifier")
    return issues


# =========================================================================
# 9. 编辑会话（供 CLI 与 GUI 复用）
# =========================================================================

class Session:
    """一次打开 → 多次编辑 → 导出 的会话"""

    def __init__(self, path: str):
        self.book = EpubBook(path)
        self.toc = clone_toc(self.book.toc)
        self.removed: List[Dict[str, str]] = []
        self.new_files: Dict[str, bytes] = {}
        self.new_manifest: List[Dict[str, str]] = []
        self.spine_append: List[str] = []
        self.dirty = False
        self.log: List[str] = []
        self._history: List[Tuple[Any, ...]] = []
        self._future: List[Tuple[Any, ...]] = []
        if self.book.toc_source == "spine":
            # 目录由 spine 兜底生成时，不应把书名当章节名写回，仅作展示
            pass

    # -- 历史 ------------------------------------------------------------
    def _snapshot(self) -> Tuple[Any, ...]:
        return (clone_toc(self.toc), copy.deepcopy(self.removed),
                dict(self.new_files), copy.deepcopy(self.new_manifest),
                list(self.spine_append))

    def _restore(self, snap: Tuple[Any, ...]) -> None:
        (self.toc, self.removed, self.new_files,
         self.new_manifest, self.spine_append) = snap

    def push(self) -> None:
        self._history.append(self._snapshot())
        self._future.clear()
        if len(self._history) > 100:
            self._history.pop(0)

    @property
    def can_undo(self) -> bool:
        return bool(self._history)

    @property
    def can_redo(self) -> bool:
        return bool(self._future)

    @property
    def undo_depth(self) -> int:
        return len(self._history)

    def undo(self) -> bool:
        if not self._history:
            return False
        self._future.append(self._snapshot())
        self._restore(self._history.pop())
        self.dirty = True
        return True

    def redo(self) -> bool:
        if not self._future:
            return False
        self._history.append(self._snapshot())
        self._restore(self._future.pop())
        self.dirty = True
        return True

    # -- 查询 ------------------------------------------------------------
    def tree(self) -> List[Dict[str, Any]]:
        return [n.to_dict() for n in self.toc]

    def find(self, path: str) -> Dict[str, Any]:
        node, parent, idx = locate(self.toc, path)
        return {
            "path": path, "title": node.title, "kind": node.kind,
            "file": node.file, "anchor": node.anchor,
            "children": len(node.children),
            "position": idx + 1,
            "siblings": len(parent),
        }

    # -- 编辑 ------------------------------------------------------------
    def rename(self, path: str, title: str) -> str:
        title = (title or "").strip()
        if not title:
            raise ValueError("标题不能为空")
        self.push()
        node, _, _ = locate(self.toc, path)
        old = node.title
        node.title = title
        self.dirty = True
        return "改名：%s → %s" % (old, title)

    def move(self, path: str, target: str) -> str:
        self.push()
        node, desc = move_node(self.toc, path, target)
        self.dirty = True
        return "移动《%s》到 %s" % (node.title, desc)

    def delete(self, path: str, mode: str = "auto", physical: bool = True) -> str:
        """
        mode: auto / promote（大标题提升子项）/ cascade（大标题连子项一起删）
        physical: 是否真删正文文件
        """
        node, parent, idx = locate(self.toc, path)
        self.push()
        if node.kind == "group" and node.children:
            if mode == "auto":
                raise ValueError("《%s》是一个大标题，含 %d 个子条目，请指定 mode=promote 或 cascade"
                                 % (node.title, len(node.children)))
            if mode == "promote":
                for c in reversed(node.children):
                    parent.insert(idx, c)
                del parent[idx + len(node.children)]
                self.dirty = True
                return "删除大标题《%s》，%d 个子条目已提升到上一层" % (node.title, len(node.children))
            # cascade
            victims = list(_walk_nodes([node]))
            for v in victims:
                if v.kind != "group" and v.file and physical:
                    self._mark_removed(v)
            del parent[idx]
            self.dirty = True
            return "删除大标题《%s》（连带 %d 条）" % (node.title, len(node.children))
        # 普通章节
        if physical and node.file:
            self._mark_removed(node)
        del parent[idx]
        self.dirty = True
        return "删除《%s》%s" % (node.title, "（文件已标记删除）" if physical and node.file else "（仅移出目录）")

    def _mark_removed(self, node: Node) -> None:
        item = next((m for m in self.book.manifest if m["path"] == node.file), None)
        if item is None:
            return
        if not any(r.get("path") == node.file for r in self.removed):
            self.removed.append({"path": node.file, "item_id": item["id"]})

    def add_group(self, title: str, at: str = "end") -> str:
        title = (title or "").strip() or "新建大标题"
        container, idx = parse_target(self.toc, at)
        self.push()
        node = Node("group", title)
        container.insert(idx, node)
        self.dirty = True
        return "新增大标题《%s》于第 %d 位" % (title, idx + 1)

    def wrap_group(self, paths: List[str], title: str) -> str:
        if not paths:
            raise ValueError("请至少指定一个子条目")
        title = (title or "").strip() or "新建大标题"
        nodes = []
        for p in paths:
            node, parent, idx = locate(self.toc, p)
            nodes.append((p, node, parent, idx))
        # 必须同层且连续
        parents = {id(x[2]) for x in nodes}
        if len(parents) != 1:
            raise ValueError("被分组的条目必须处于同一层级")
        parent = nodes[0][2]
        idxs = sorted(x[3] for x in nodes)
        if idxs != list(range(idxs[0], idxs[0] + len(idxs))):
            raise ValueError("被分组的条目必须是连续的一段")
        self.push()
        group = Node("group", title)
        group.children = [x[1] for x in nodes]
        for _ in range(len(nodes)):
            del parent[idxs[0]]
        parent.insert(idxs[0], group)
        self.dirty = True
        return "已将 %d 个条目归入大标题《%s》" % (len(nodes), title)

    # -- TXT 追加 --------------------------------------------------------
    def append_txt(self, txt_path: str, *, group_title: Optional[str] = None,
                   at: str = "end", split: str = "auto", size: int = 4000,
                   para_mode: str = "line", encoding: Optional[str] = None,
                   link_css: bool = True, drop_page_numbers: bool = False) -> Dict[str, Any]:
        """
        把 TXT 按章节切分后作为新章节追加到本书：
        新增 XHTML 文件 + manifest 项 + spine 项 + 目录条目，原有章节与元数据不动。
        """
        try:
            import txtconv  # type: ignore
        except ImportError:  # pragma: no cover - 单独运行 src 目录时的兜底
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import txtconv  # type: ignore

        text, used_enc = txtconv.read_text_file(txt_path, encoding)
        base_title = os.path.splitext(os.path.basename(txt_path))[0] or "追加内容"
        chapters, stats = txtconv.split_chapters(
            text, base_title, mode=split, size=size,
            para_mode=para_mode, drop_page_numbers=drop_page_numbers)
        if not chapters:
            raise ValueError("TXT 没有任何可用的正文内容：%s" % txt_path)
        if not any(c.char_count for c in chapters):
            raise ValueError("TXT 内容为空（只有标题、没有正文），已中止：%s" % txt_path)

        # 内容目录：沿用书中正文最常见的位置
        dirs: Dict[str, int] = {}
        for m in self.book.manifest:
            if m["media_type"] == "application/xhtml+xml":
                d = dirname(m["path"])
                dirs[d] = dirs.get(d, 0) + 1
        content_dir = max(dirs, key=lambda k: dirs[k]) if dirs else self.book.opf_dir

        # 沿用书中样式表，保证排版观感一致
        css_href = None
        if link_css:
            css = next((m for m in self.book.manifest if m["media_type"] == "text/css"), None)
            if css is not None:
                css_href = encode_href(relative_path(content_dir, css["path"])) if content_dir else \
                    encode_href(basename(css["path"]))

        used_paths = set(self.book.infos) | set(self.new_files)
        used_ids = {m["id"] for m in self.book.manifest} | {m["id"] for m in self.new_manifest}

        self.push()

        seq = 0
        entries: List[Tuple[int, Node]] = []
        for ch in chapters:
            # 纯标题（卷/部，无正文）→ 仅作大标题，不生成空白页
            if ch.level == 1 and ch.char_count == 0:
                entries.append((1, Node("group", ch.title)))
                continue

            seq += 1
            stem = "txt-%03d" % seq
            path = (content_dir + "/" + stem + ".xhtml") if content_dir else (stem + ".xhtml")
            k = 1
            while path in used_paths:
                k += 1
                path = (content_dir + "/" + "%s-%d.xhtml" % (stem, k)) if content_dir else \
                    ("%s-%d.xhtml" % (stem, k))
            used_paths.add(path)

            item_id = stem
            while item_id in used_ids:
                k += 1
                item_id = "%s-%d" % (stem, k)
            used_ids.add(item_id)

            xhtml = txtconv.build_xhtml(ch, epub_version=self.book.version,
                                        language=self.book.metadata.get("language") or "zh",
                                        css_href=css_href)
            self.new_files[path] = xhtml.encode("utf-8")
            self.new_manifest.append({"id": item_id, "path": path,
                                      "media_type": "application/xhtml+xml"})
            self.spine_append.append(item_id)
            entries.append((ch.level, Node("doc", ch.title, path, "", "")))

        # 挂到目录树上
        container, idx = parse_target(self.toc, at)
        if group_title:
            group = Node("group", group_title.strip() or "新增章节")
            container.insert(idx, group)
            group.children.extend(n for _lv, n in entries)
            where = "大标题《%s》下" % group.title
        else:
            pos = idx
            current: Optional[Node] = None
            for level, node in entries:
                if level == 1:
                    container.insert(pos, node)
                    pos += 1
                    current = node
                elif current is not None:
                    current.children.append(node)
                else:
                    container.insert(pos, node)
                    pos += 1
            where = "目录末尾" if at in ("end", "") else "指定位置"

        self.dirty = True
        self.log.append("追加 TXT：%s" % txt_path)
        return {
            "encoding": used_enc,
            "chapters": len([1 for lv, _ in entries if lv == 2]) or len(entries),
            "groups": len([1 for lv, _ in entries if lv == 1]),
            "files": [m["path"] for m in self.new_manifest],
            "where": where,
            "split_rule": stats.get("rule"),
            "headings": stats.get("headings", 0),
            "chars": sum(c.char_count for c in chapters),
        }

    # -- DOCX 追加 --------------------------------------------------------
    def append_docx(self, docx_path: str, *, group_title: Optional[str] = None,
                    at: str = "end", split_level: int = 1,
                    link_css: bool = True) -> Dict[str, Any]:
        """
        把 DOCX 按标题切分后作为新章节追加：保留加粗/斜体、列表、表格、图片。
        """
        try:
            import docxconv  # type: ignore
        except ImportError:  # pragma: no cover
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import docxconv  # type: ignore

        doc = docxconv.read_docx(docx_path, split_level=split_level)
        if not doc.chapters:
            raise ValueError("DOCX 里没有任何可用的正文内容：%s" % docx_path)

        dirs: Dict[str, int] = {}
        for m in self.book.manifest:
            if m["media_type"] == "application/xhtml+xml":
                d = dirname(m["path"])
                dirs[d] = dirs.get(d, 0) + 1
        content_dir = max(dirs, key=lambda k: dirs[k]) if dirs else self.book.opf_dir

        css_href = None
        if link_css:
            css = next((m for m in self.book.manifest if m["media_type"] == "text/css"), None)
            if css is not None:
                css_href = encode_href(relative_path(content_dir, css["path"])) if content_dir \
                    else encode_href(basename(css["path"]))

        used_paths = set(self.book.infos) | set(self.new_files)
        used_ids = {m["id"] for m in self.book.manifest} | {m["id"] for m in self.new_manifest}

        self.push()

        entries: List[Node] = []
        image_count = 0
        for idx, ch in enumerate(doc.chapters, start=1):
            stem = "docx-%03d" % idx
            path = (content_dir + "/" + stem + ".xhtml") if content_dir else (stem + ".xhtml")
            k = 1
            while path in used_paths:
                k += 1
                path = (content_dir + "/" + "%s-%d.xhtml" % (stem, k)) if content_dir \
                    else ("%s-%d.xhtml" % (stem, k))
            used_paths.add(path)

            item_id = stem
            k = 1
            while item_id in used_ids:
                k += 1
                item_id = "%s-%d" % (stem, k)
            used_ids.add(item_id)

            # 本章用到的图片：落到 <正文目录>/media/ 下，名字带章节前缀避免冲突
            href_map: Dict[str, str] = {}
            for media_name in ch.images():
                data = doc.media.get(media_name)
                if not data:
                    continue
                ext = posixpath.splitext(media_name)[1].lower() or ".png"
                media_type = docxconv.MEDIA_TYPES.get(ext)
                if not media_type:
                    continue
                fname = "%s-%s%s" % (stem, posixpath.splitext(posixpath.basename(media_name))[0], ext)
                media_path = ((content_dir + "/media/") if content_dir else "media/") + fname
                k = 1
                while media_path in used_paths:
                    k += 1
                    media_path = ((content_dir + "/media/") if content_dir else "media/") + \
                        "%s-%d%s" % (posixpath.splitext(fname)[0], k, ext)
                used_paths.add(media_path)
                self.new_files[media_path] = data

                mid = "img-%s" % posixpath.splitext(fname)[0]
                k = 1
                while mid in used_ids:
                    k += 1
                    mid = "img-%s-%d" % (posixpath.splitext(fname)[0], k)
                used_ids.add(mid)
                self.new_manifest.append({"id": mid, "path": media_path, "media_type": media_type})
                href_map[media_name] = encode_href(relative_path(dirname(path), media_path))
                image_count += 1

            body = docxconv.render_blocks(ch, lambda m: href_map.get(m, ""),
                                          heading_base=split_level)
            xhtml = docxconv.wrap_xhtml(body, ch.title,
                                        epub_version=self.book.version,
                                        language=self.book.metadata.get("language") or "zh",
                                        css_href=css_href)
            self.new_files[path] = xhtml.encode("utf-8")
            self.new_manifest.append({"id": item_id, "path": path,
                                      "media_type": "application/xhtml+xml"})
            self.spine_append.append(item_id)
            entries.append(Node("doc", ch.title, path, "", ""))

        container, idx = parse_target(self.toc, at)
        if group_title:
            group = Node("group", group_title.strip() or "DOCX 导入")
            container.insert(idx, group)
            group.children.extend(entries)
            where = "大标题《%s》下" % group.title
        else:
            container[idx:idx] = entries
            where = "目录末尾" if at in ("end", "") else "指定位置"

        self.dirty = True
        self.log.append("追加 DOCX：%s" % docx_path)
        return {
            "title": doc.title,
            "author": doc.author,
            "chapters": len(entries),
            "images": image_count,
            "split_level": split_level,
            "where": where,
            "files": [m["path"] for m in self.new_manifest
                      if m["media_type"] == "application/xhtml+xml"],
        }

    # -- EPUB 合并 --------------------------------------------------------
    def merge_epubs(self, paths: List[str], group_each: bool = True,
                    prefix: str = "merged") -> Dict[str, Any]:
        """
        把若干本 EPUB 合并进当前书：
        每本书的资源整棵子树搬到 <prefix>/bN/ 下（内部相对链接原样有效），
        manifest / spine / 目录按新路径重映射，元数据以当前书为准。
        """
        if not paths:
            raise ValueError("请至少指定一本要合并的 EPUB")

        self_path = os.path.abspath(self.book.path)
        others: List[EpubBook] = []
        for p in paths:
            if os.path.abspath(p) == self_path:
                raise ValueError("不能把书合并进它自己：%s" % p)
            book = EpubBook(p)
            if "META-INF/encryption.xml" in book.infos:
                raise ValueError("《%s》包含加密条目（DRM 或字体混淆），无法合并：%s"
                                 % (book.metadata.get("title") or basename(p), p))
            others.append(book)

        self.push()
        used_paths = set(self.book.infos) | set(self.new_files)
        used_ids = {m["id"] for m in self.book.manifest} | {m["id"] for m in self.new_manifest}
        prefix_dir = (prefix or "merged").strip("/")
        added_nodes: List[Node] = []
        warnings: List[str] = []
        stats: List[Dict[str, Any]] = []
        total_docs = total_groups = total_files = 0

        for bi, other in enumerate(others, start=1):
            src_dir = other.opf_dir
            base_dir = ((self.book.opf_dir + "/") if self.book.opf_dir else "") + \
                "%s/b%d" % (prefix_dir, bi)
            skip = {other.opf_path}
            if other.nav_path:
                skip.add(other.nav_path)
            if other.ncx_path:
                skip.add(other.ncx_path)

            src_to_new: Dict[str, str] = {}
            for name in other.order:
                if not name or name.endswith("/") or name == "mimetype":
                    continue
                if name.startswith("META-INF/") or name in skip:
                    continue
                if src_dir and name.startswith(src_dir + "/"):
                    new = base_dir + "/" + name[len(src_dir) + 1:]
                elif not src_dir:
                    new = base_dir + "/" + name
                else:
                    # 落在 OPF 目录之外的资源（少见的共享目录），单独收纳并改写引用
                    new = base_dir + "/_ext/" + posixpath.basename(name)
                if new in used_paths:
                    stem, ext = posixpath.splitext(new)
                    k = 1
                    while new in used_paths:
                        k += 1
                        new = "%s-%d%s" % (stem, k, ext)
                used_paths.add(new)
                src_to_new[name] = new
                self.new_files[new] = other.read(name)

            # 跨版本合并（EPUB2 ↔ EPUB3）需要统一 XHTML 文档头，否则校验报 HTM-004
            target_epub2 = str(self.book.version).startswith("2")
            cross_version = (str(other.version).startswith("2") != target_epub2)
            if cross_version:
                warnings.append("《%s》是 EPUB %s，与主书版本不同，已统一 XHTML 文档头"
                                % (other.metadata.get("title") or basename(other.path),
                                   other.version))

            for name, new in list(src_to_new.items()):
                ext = posixpath.splitext(name)[1].lower()
                if ext not in (".xhtml", ".html", ".htm", ".css", ".svg"):
                    continue
                try:
                    text = self.new_files[new].decode("utf-8")
                except UnicodeDecodeError:
                    continue
                updated = text
                if cross_version and ext in (".xhtml", ".html", ".htm"):
                    updated = _normalize_xhtml(updated, target_epub2)
                if src_dir:
                    updated = _rewrite_external_refs(updated, name, new, src_dir, src_to_new)
                if updated != text:
                    self.new_files[new] = updated.encode("utf-8")

            # manifest / spine
            mt_by_path = {m["path"]: m["media_type"] for m in other.manifest}
            id_by_path = {m["path"]: m["id"] for m in other.manifest}
            props_by_path = {m["path"]: m["properties"] for m in other.manifest}
            id_map: Dict[str, str] = {}
            for name, new in src_to_new.items():
                media_type = mt_by_path.get(name) or guess_media_type(new)
                if not media_type or media_type == "application/x-dtbncx+xml":
                    continue
                raw_id = id_by_path.get(name) or posixpath.splitext(posixpath.basename(new))[0]
                safe = re.sub(r"[^0-9A-Za-z_-]", "-", raw_id) or "item"
                item_id = "b%d-%s" % (bi, safe)
                if item_id in used_ids:
                    k = 1
                    base_id = item_id
                    while item_id in used_ids:
                        k += 1
                        item_id = "%s-%d" % (base_id, k)
                used_ids.add(item_id)
                id_map[name] = item_id
                # 保留 svg/scripted 等属性；nav 与 cover-image 不重复声明
                props = " ".join(x for x in (props_by_path.get(name) or "").split()
                                 if x not in ("nav", "cover-image"))
                entry = {"id": item_id, "path": new, "media_type": media_type}
                if props:
                    entry["properties"] = props
                self.new_manifest.append(entry)

            for ir in other.spine:
                item = ir["item"]
                if not item:
                    continue
                nid = id_map.get(item["path"])
                if nid and nid not in self.spine_append:
                    self.spine_append.append(nid)

            # 目录映射
            def convert(nodes: List[Node]) -> List[Node]:
                out: List[Node] = []
                for n in nodes:
                    if n.file:
                        newf = src_to_new.get(n.file)
                        if not newf:
                            continue
                        c = Node("doc", n.title, newf, n.anchor, "")
                        c.children = convert(n.children)
                        out.append(c)
                    elif n.children:
                        c = Node("group", n.title)
                        c.children = convert(n.children)
                        if c.children:
                            out.append(c)
                return out

            sub = convert(other.toc)
            title = other.metadata.get("title") or posixpath.splitext(os.path.basename(other.path))[0]
            if not sub:
                warnings.append("《%s》没有可映射的目录条目，正文已并入但不进目录" % title)
                continue
            if group_each:
                g = Node("group", title)
                g.children = sub
                added_nodes.append(g)
            else:
                added_nodes.extend(sub)

            cnt = count_nodes(sub)
            total_docs += cnt["docs"]
            total_groups += cnt["groups"]
            total_files += len(src_to_new)
            stats.append({"title": title, "path": other.path, "docs": cnt["docs"],
                          "groups": cnt["groups"], "files": len(src_to_new),
                          "dir": base_dir})

            # 元数据差异提示（合并后统一用主书的）
            for key, label in (("language", "语言"), ("creators", "作者")):
                if other.metadata.get(key) and other.metadata.get(key) != self.book.metadata.get(key):
                    warnings.append("《%s》的%s与主书不同，合并后沿用主书" % (title, label))

        if not added_nodes:
            self.undo()
            raise ValueError("没有合并到任何内容，请检查这些 EPUB 的目录结构")

        self.toc.extend(added_nodes)
        self.dirty = True
        self.log.append("合并 %d 本 EPUB" % len(stats))
        return {
            "books": stats,
            "chapters": total_docs,
            "groups": total_groups,
            "files": total_files,
            "prefix": prefix_dir,
            "warnings": warnings,
        }

    # -- 校验后修复 ------------------------------------------------------
    def _alive_files(self) -> set:
        alive = set(self.book.infos)
        alive |= set(self.new_files)
        for r in self.removed:
            alive.discard(r.get("path"))
        return alive

    def repair_plan(self) -> List[Dict[str, Any]]:
        """列出可以安全自动修复的问题（只读，不改动任何东西）"""
        alive = self._alive_files()
        dead = empty = 0

        def walk(items):
            nonlocal dead, empty
            for n in items:
                if n.file and n.file not in alive:
                    dead += 1
                    continue
                if n.children:
                    walk(n.children)
                elif n.kind == "group":
                    empty += 1

        walk(self.toc)
        manifest_bad = sum(1 for m in self.book.manifest if m["path"] not in self.book.infos)
        ids = {m["id"] for m in self.book.manifest}
        spine_bad = sum(1 for s in self.book.spine if s["idref"] not in ids)
        uid_bad = 0
        pkg = self.book.pkg
        md = first_child(pkg, "metadata") if pkg is not None else None
        if md is not None:
            uid = pkg.get("unique-identifier")
            hits = [el for el in child_els(md, "identifier") if (el.get("id") or "") == uid]
            if uid and not hits:
                uid_bad = 1
        plan = []
        if dead:
            plan.append({"key": "dead", "label": "目录里有 %d 个条目指向不存在的文件" % dead,
                         "count": dead})
        if empty:
            plan.append({"key": "empty", "label": "有 %d 个空大标题（没有任何子项）" % empty,
                         "count": empty})
        if manifest_bad:
            plan.append({"key": "manifest", "label": "%d 个文件清单条目指向不存在的文件" % manifest_bad,
                         "count": manifest_bad})
        if spine_bad:
            plan.append({"key": "spine", "label": "%d 个正文顺序引用是无效的" % spine_bad,
                         "count": spine_bad})
        if uid_bad:
            plan.append({"key": "uid", "label": "书籍唯一标识指向了不存在的编号", "count": 1})
        return plan

    def repair(self) -> List[str]:
        """修掉目录层的悬空条目与空大标题；文件清单类问题在导出时一并清理"""
        alive = self._alive_files()
        logs: List[str] = []
        removed_dead = removed_empty = 0

        def walk(items):
            nonlocal removed_dead, removed_empty
            keep = []
            for n in items:
                if n.file and n.file not in alive:
                    removed_dead += 1
                    continue
                if n.children:
                    n.children = walk(n.children)
                if n.kind == "group" and not n.children:
                    removed_empty += 1
                    continue
                keep.append(n)
            return keep

        probe = self.repair_plan()
        need_model_fix = any(p["key"] in ("dead", "empty") for p in probe)
        if need_model_fix:
            self.push()
            self.toc = walk(self.toc)
            self.dirty = True
            if removed_dead:
                logs.append("移除 %d 个指向缺失文件的目录条目" % removed_dead)
            if removed_empty:
                logs.append("移除 %d 个空大标题" % removed_empty)
        plan = self.repair_plan()
        rest = [p for p in plan if p["key"] in ("manifest", "spine", "uid")]
        for p in rest:
            logs.append("导出时将自动处理：" + p["label"])
        return logs

    # -- 导出 ------------------------------------------------------------
    def export(self, out_path: str, sync_spine: bool = True,
               sync_heading: bool = False, dry_run: bool = False,
               touch_modified: bool = True, prune_broken: bool = True) -> Dict[str, Any]:
        data, report = export_epub(self.book, self.toc, self.removed,
                                   sync_spine=sync_spine, sync_heading=sync_heading,
                                   new_files=self.new_files, new_manifest=self.new_manifest,
                                   spine_append=self.spine_append,
                                   touch_modified=touch_modified,
                                   prune_broken=prune_broken)
        issues = validate_bytes(data)
        report["issues"] = issues
        if not dry_run:
            tmp = out_path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, out_path)
        return report


# =========================================================================
# 9.5 合并辅助
# =========================================================================

MEDIA_BY_EXT = {
    ".xhtml": "application/xhtml+xml", ".html": "application/xhtml+xml", ".htm": "application/xhtml+xml",
    ".css": "text/css", ".svg": "image/svg+xml",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
    ".webp": "image/webp", ".avif": "image/avif", ".bmp": "image/bmp", ".tif": "image/tiff",
    ".ttf": "font/ttf", ".otf": "font/otf", ".woff": "font/woff", ".woff2": "font/woff2",
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".ogg": "audio/ogg",
    ".mp4": "video/mp4", ".webm": "video/webm",
    ".js": "application/javascript", ".json": "application/json",
    ".txt": "text/plain", ".ncx": "application/x-dtbncx+xml",
}

_ATTR_REF_RE = None
_CSS_URL_RE = None


def guess_media_type(path: str) -> Optional[str]:
    ext = posixpath.splitext(path)[1].lower()
    return MEDIA_BY_EXT.get(ext)


def _rewrite_external_refs(text: str, src_file: str, new_file: str,
                           src_dir: str, src_to_new: Dict[str, str]) -> str:
    """只改写"目标位于源 OPF 目录之外"的引用；目录内相对结构保留，不做任何改写"""
    global _ATTR_REF_RE, _CSS_URL_RE
    if _ATTR_REF_RE is None:
        _ATTR_REF_RE = re.compile(r"\b(href|src)\s*=\s*([\"'])([^\"']*)\2", re.I)
        _CSS_URL_RE = re.compile(r"url\(\s*([\"']?)([^)\"']+)\1\s*\)", re.I)

    src_dir_of_file = dirname(src_file)
    new_dir_of_file = dirname(new_file)

    def fix(ref: str) -> Optional[str]:
        if not ref or ref.startswith(("#", "//")):
            return None
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", ref):
            return None
        path_part, anchor = split_anchor(ref)
        if not path_part:
            return None
        res = resolve_href(src_dir_of_file, path_part)
        if not res:
            return None
        target = src_to_new.get(res[0])
        if not target:
            return None
        # 目标在 OPF 目录内 → 相对结构未变，保持原样
        inside = res[0].startswith(src_dir + "/") if src_dir else True
        if inside:
            return None
        rel = relative_path(new_dir_of_file, target)
        return rel + (("#" + anchor) if anchor else "")

    def repl_attr(m):
        new_ref = fix(m.group(3))
        return m.group(0) if new_ref is None else '%s=%s%s%s' % (m.group(1), m.group(2), new_ref, m.group(2))

    out = _ATTR_REF_RE.sub(repl_attr, text)

    def repl_css(m):
        new_ref = fix(m.group(2))
        return m.group(0) if new_ref is None else "url(%s%s%s)" % (m.group(1), new_ref, m.group(1))

    return _CSS_URL_RE.sub(repl_css, out)


# =========================================================================
# 9.9 DOCX → 新 EPUB
# =========================================================================

DOCX_CSS = """body { margin: 0 5%; line-height: 1.7; font-family: serif; }
h1 { font-size: 1.5em; text-align: center; margin: 1.4em 0 1em; }
h2, h3, h4 { margin: 1.2em 0 0.6em; }
p { margin: 0.5em 0; text-indent: 2em; }
p.img { text-indent: 0; text-align: center; }
table.docx-table { border-collapse: collapse; margin: 1em 0; width: 100%; }
table.docx-table th, table.docx-table td { border: 1px solid #bbb; padding: 0.3em 0.5em; }
ul, ol { margin: 0.5em 0 0.5em 1.6em; padding: 0; }
li { margin: 0.2em 0; }
"""

CONTAINER_XML = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def _pack_epub(out_path: Optional[str], *, title: str, author: str, language: str,
               nodes: List[Node], files: Dict[str, bytes],
               manifest_items: List[Dict[str, str]], spine: List[str]) -> Dict[str, Any]:
    """把已经生成好的章节文件打包成 EPUB3（nav + NCX + OPF + CSS），并做结构自检"""
    import uuid as _uuid

    manifest = [
        {"id": "nav", "href": "nav.xhtml", "media_type": "application/xhtml+xml",
         "properties": "nav"},
        {"id": "ncx", "href": "toc.ncx", "media_type": "application/x-dtbncx+xml"},
        {"id": "css", "href": "Styles/style.css", "media_type": "text/css"},
    ] + manifest_items

    meta = {"title": title, "identifier": "urn:uuid:%s" % _uuid.uuid4(),
            "language": language}
    files["OEBPS/nav.xhtml"] = build_nav_text(nodes, "OEBPS", title).encode("utf-8")
    files["OEBPS/toc.ncx"] = build_ncx_text(nodes, "OEBPS", meta).encode("utf-8")
    files["OEBPS/Styles/style.css"] = DOCX_CSS.encode("utf-8")

    opf_items = []
    for m in manifest:
        extra = ' properties="%s"' % m["properties"] if m.get("properties") else ""
        opf_items.append('    <item id="%s" href="%s" media-type="%s"%s/>'
                         % (escape_attr(m["id"]), escape_attr(m["href"]),
                            escape_attr(m["media_type"]), extra))
    opf_spine = "\n".join('    <itemref idref="%s"/>' % escape_attr(x) for x in spine)
    opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="%s">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">%s</dc:identifier>
    <dc:title>%s</dc:title>
%s    <dc:language>%s</dc:language>
    <meta property="dcterms:modified">%s</meta>
  </metadata>
  <manifest>
%s
  </manifest>
  <spine toc="ncx">
%s
  </spine>
</package>
""" % (escape_attr(language), escape_text(meta["identifier"]), escape_text(title),
       ('    <dc:creator>%s</dc:creator>\n' % escape_text(author)) if author else "",
       escape_text(language), _iso_now(), "\n".join(opf_items), opf_spine)
    files["OEBPS/content.opf"] = opf.encode("utf-8")
    files["META-INF/container.xml"] = CONTAINER_XML.encode("utf-8")

    order = ["META-INF/container.xml", "OEBPS/content.opf", "OEBPS/nav.xhtml",
             "OEBPS/toc.ncx", "OEBPS/Styles/style.css"]
    order += [p for p in files if p not in order]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo("mimetype", date_time=datetime.now().timetuple()[:6])
        info.compress_type = zipfile.ZIP_STORED
        info.external_attr = 0o644 << 16
        zf.writestr(info, MIMETYPE_CONTENT)
        for name in order:
            ni = zipfile.ZipInfo(name, date_time=datetime.now().timetuple()[:6])
            ni.compress_type = zipfile.ZIP_DEFLATED
            ni.external_attr = 0o644 << 16
            zf.writestr(ni, files[name])
    data = buf.getvalue()

    if out_path:
        tmp = out_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, out_path)

    return {"bytes": len(data), "title": title, "author": author,
            "language": language, "chapters": len(spine), "images": 0,
            "split_level": 1, "issues": validate_bytes(data)}


def docx_to_epub(docx_path: str, out_path: Optional[str] = None, *,
                 title: Optional[str] = None, author: Optional[str] = None,
                 language: Optional[str] = None, split_level: int = 1) -> Dict[str, Any]:
    """
    把一份 DOCX 转成全新的 EPUB3 书：
    标题切分为章节，图片抽出到 Images/，生成 nav + NCX + OPF，并做结构自检。
    """
    try:
        import docxconv  # type: ignore
    except ImportError:  # pragma: no cover
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import docxconv  # type: ignore

    doc = docxconv.read_docx(docx_path, split_level=split_level)
    book_title = (title or doc.title or
                  os.path.splitext(os.path.basename(docx_path))[0]).strip()
    book_author = (author if author is not None else doc.author).strip()
    lang = (language or doc.language or "zh").strip() or "zh"

    files: Dict[str, bytes] = {}
    manifest_items: List[Dict[str, str]] = []
    spine: List[str] = []
    nodes: List[Node] = []
    img_seq = 0

    for i, ch in enumerate(doc.chapters, start=1):
        stem = "chapter%03d" % i
        path = "OEBPS/Text/%s.xhtml" % stem
        href_map: Dict[str, str] = {}
        for media in ch.images():
            data = doc.media.get(media)
            if not data:
                continue
            ext = posixpath.splitext(media)[1].lower() or ".png"
            media_type = docxconv.MEDIA_TYPES.get(ext)
            if not media_type:
                continue
            img_seq += 1
            ipath = "OEBPS/Images/docx-%03d%s" % (img_seq, ext)
            files[ipath] = data
            manifest_items.append({"id": "img%03d" % img_seq,
                                   "href": "Images/docx-%03d%s" % (img_seq, ext),
                                   "media_type": media_type})
            href_map[media] = encode_href(relative_path("OEBPS/Text", ipath))

        body = docxconv.render_blocks(ch, lambda m: href_map.get(m, ""),
                                      heading_base=split_level)
        files[path] = docxconv.wrap_xhtml(body, ch.title, epub_version="3.0",
                                          language=lang, css_href="../Styles/style.css").encode("utf-8")
        manifest_items.append({"id": stem, "href": "Text/%s.xhtml" % stem,
                               "media_type": "application/xhtml+xml"})
        spine.append(stem)
        nodes.append(Node("doc", ch.title, path, "", ""))

    rep = _pack_epub(out_path, title=book_title, author=book_author, language=lang,
                     nodes=nodes, files=files, manifest_items=manifest_items, spine=spine)
    rep["images"] = img_seq
    rep["split_level"] = split_level
    return rep


def txt_to_epub(txt_path: str, out_path: Optional[str] = None, *,
                title: Optional[str] = None, author: str = "", language: str = "zh",
                split: str = "auto", size: int = 4000, para_mode: str = "line",
                encoding: Optional[str] = None,
                drop_page_numbers: bool = False) -> Dict[str, Any]:
    """把一份 TXT 转成全新的 EPUB3 书：按标题切章，卷标题作为大标题分组"""
    try:
        import txtconv  # type: ignore
        import docxconv  # type: ignore
    except ImportError:  # pragma: no cover
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import txtconv  # type: ignore
        import docxconv  # type: ignore

    text, used = txtconv.read_text_file(txt_path, encoding)
    base = os.path.splitext(os.path.basename(txt_path))[0]
    chapters, stats = txtconv.split_chapters(
        text, base, mode=split, size=size, para_mode=para_mode,
        drop_page_numbers=drop_page_numbers)
    if not chapters or not any(c.char_count for c in chapters):
        raise ValueError("TXT 没有可用的正文内容：%s" % txt_path)

    book_title = (title or base).strip()
    lang = (language or "zh").strip() or "zh"

    files: Dict[str, bytes] = {}
    manifest_items: List[Dict[str, str]] = []
    spine: List[str] = []
    nodes: List[Node] = []
    container: List[Node] = nodes
    seq = 0

    for ch in chapters:
        if ch.level == 1:
            container = nodes
            if ch.char_count == 0:
                group = Node("group", ch.title)
                nodes.append(group)
                container = group.children
                continue
        seq += 1
        stem = "chapter%03d" % seq
        path = "OEBPS/Text/%s.xhtml" % stem
        body = "\n".join("<p>%s</p>" % escape_text(p) for p in ch.paragraphs)
        files[path] = docxconv.wrap_xhtml(body, ch.title, epub_version="3.0",
                                          language=lang, css_href="../Styles/style.css").encode("utf-8")
        manifest_items.append({"id": stem, "href": "Text/%s.xhtml" % stem,
                               "media_type": "application/xhtml+xml"})
        spine.append(stem)
        container.append(Node("doc", ch.title, path, "", ""))

    rep = _pack_epub(out_path, title=book_title, author=(author or "").strip(),
                     language=lang, nodes=nodes, files=files,
                     manifest_items=manifest_items, spine=spine)
    rep["encoding"] = used
    rep["split_rule"] = stats.get("rule")
    rep["groups"] = sum(1 for n in _iter_groups(nodes))
    return rep


def _iter_groups(items: List[Node]):
    for n in items:
        if n.kind == "group":
            yield n
        if n.children:
            yield from _iter_groups(n.children)


# =========================================================================
# 9.8 EPUBCheck 集成（可选，找不到就跳过）
# =========================================================================

def _project_roots() -> List[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    roots = [os.getcwd(), here, os.path.dirname(here)]
    seen, out = set(), []
    for r in roots:
        if r and r not in seen:
            seen.add(r)
            out.append(r)
    return out


def find_epubcheck() -> Optional[str]:
    """定位 epubcheck.jar：环境变量 → <根>/tools/epubcheck*/epubcheck.jar"""
    env = os.environ.get("EPUBCHECK_JAR")
    if env and os.path.isfile(env):
        return env
    import glob
    for root in _project_roots():
        hits = sorted(glob.glob(os.path.join(root, "tools", "epubcheck*", "epubcheck.jar")))
        if hits:
            return hits[-1]
        direct = os.path.join(root, "tools", "epubcheck.jar")
        if os.path.isfile(direct):
            return direct
    return None


def find_java() -> Optional[str]:
    """定位 java：JAVA_HOME → PATH → Windows 常见安装目录"""
    import glob
    home = os.environ.get("JAVA_HOME")
    if home:
        for exe in ("java.exe", "java"):
            cand = os.path.join(home, "bin", exe)
            if os.path.isfile(cand):
                return cand
    found = shutil.which("java")
    if found:
        return found
    for pattern in (r"C://Program Files//Java//*//bin//java.exe",
                    r"C://Program Files//Eclipse Adoptium//*//bin//java.exe",
                    r"C://Program Files//Amazon Corretto//*//bin//java.exe",
                    r"C://Program Files//Microsoft//jdk*//bin//java.exe"):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return None


def run_epubcheck(epub_path: str, timeout: int = 300) -> List[Dict[str, str]]:
    """调用 EPUBCheck 做规范级校验，返回与内部自检同构的 issues"""
    issues: List[Dict[str, str]] = []
    jar = find_epubcheck()
    java = find_java()
    if not jar:
        return [{"level": "warn", "text": "未找到 EPUBCheck（可放到 tools/epubcheck-*/epubcheck.jar）"}]
    if not java:
        return [{"level": "warn", "text": "未找到 Java 运行时，跳过 EPUBCheck"}]

    try:
        proc = subprocess.run([java, "-jar", jar, epub_path],
                              capture_output=True, timeout=timeout)
        out = (proc.stdout or b"").decode("utf-8", "replace") + \
              (proc.stderr or b"").decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError) as exc:
        return [{"level": "warn", "text": "EPUBCheck 执行失败：%s" % exc}]

    counts = ""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith(("ERROR(", "FATAL(")):
            issues.append({"level": "error", "text": "EPUBCheck " + line})
        elif line.startswith("WARNING("):
            issues.append({"level": "warn", "text": "EPUBCheck " + line})
        elif line.startswith("USAGE("):
            issues.append({"level": "info", "text": "EPUBCheck " + line})
        elif line.startswith("Messages:"):
            counts = line
    if not any(i["level"] == "error" for i in issues):
        issues.insert(0, {"level": "ok", "text": "EPUBCheck 通过（%s）" % (counts or "无错误")})
    return issues


# =========================================================================
# 10. CLI
# =========================================================================

def _print_tree(items: List[Node], out=sys.stdout, prefix: str = "", show_file: bool = False) -> None:
    for i, n in enumerate(items):
        last = i == len(items) - 1
        branch = "└─ " if last else "├─ "
        mark = "▣ " if n.kind == "group" else "  "
        path_hint = ""
        if show_file and n.file:
            path_hint = "  ← %s%s" % (n.file, "#" + n.anchor if n.anchor else "")
        out.write("%s%s%s%s%s\n" % (prefix, branch, mark, n.title, path_hint))
        if n.children:
            _print_tree(n.children, out, prefix + ("   " if last else "│  "), show_file)


def _parse_op_args(op_text: str) -> List[Dict[str, Any]]:
    """把 '--op "rename 1.2 新标题"' 解析为操作字典"""
    parts = shlex.split(op_text)
    if not parts:
        raise ValueError("空操作")
    verb = parts[0].lower().replace("_", "-")
    if verb == "rename" and len(parts) >= 3:
        return [{"op": "rename", "path": parts[1], "title": " ".join(parts[2:])}]
    if verb == "move" and len(parts) >= 3:
        return [{"op": "move", "path": parts[1], "to": parts[2]}]
    if verb == "delete" and len(parts) >= 2:
        op = {"op": "delete", "path": parts[1], "physical": True}
        if "--keep" in parts:
            op["physical"] = False
        for m in ("promote", "cascade"):
            if m in parts:
                op["mode"] = m
        return [op]
    if verb in ("group-add", "group", "add-group") and len(parts) >= 3:
        at = "end"
        if "@" in parts[2]:
            at = parts[2].split("@", 1)[1]
            title = parts[2].split("@", 1)[0]
        else:
            title = " ".join(parts[2:])
        return [{"op": "group_add", "title": title, "at": at}]
    if verb in ("group-wrap", "wrap") and len(parts) >= 3:
        return [{"op": "group_wrap", "title": parts[1], "paths": parts[2:]}]
    if verb in ("group-remove", "ungroup") and len(parts) >= 2:
        mode = "promote"
        for m in ("promote", "cascade"):
            if m in parts[1:]:
                mode = m
        return [{"op": "group_remove", "path": parts[1], "mode": mode}]
    raise ValueError("无法识别的操作：%s" % op_text)


def apply_ops(session: Session, ops: List[Dict[str, Any]]) -> List[str]:
    logs = []
    for i, op in enumerate(ops):
        kind = (op.get("op") or "").replace("-", "_")
        try:
            if kind == "rename":
                logs.append(session.rename(str(op["path"]), str(op["title"])))
            elif kind == "move":
                logs.append(session.move(str(op["path"]), str(op["to"])))
            elif kind == "delete":
                logs.append(session.delete(str(op["path"]),
                                           mode=op.get("mode", "auto"),
                                           physical=bool(op.get("physical", True))))
            elif kind == "group_add":
                logs.append(session.add_group(str(op.get("title", "")), str(op.get("at", "end"))))
            elif kind == "group_wrap":
                logs.append(session.wrap_group([str(p) for p in op["paths"]],
                                               str(op.get("title", ""))))
            elif kind == "group_remove":
                logs.append(session.delete(str(op["path"]), mode=op.get("mode", "promote"),
                                           physical=False))
            elif kind == "sync_heading":
                continue
            else:
                raise ValueError("未知操作：%s" % kind)
        except Exception as exc:
            raise ValueError("第 %d 个操作失败（%s）：%s" % (i + 1, kind, exc))
    return logs


def cmd_info(args) -> int:
    book = EpubBook(args.epub)
    info = book.info()
    if args.json:
        info["toc"] = [n.to_dict() for n in book.toc]
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0
    print("文件      : %s (%.1f KB)" % (info["file"], info["size"] / 1024.0))
    print("版本      : EPUB %s" % info["version"])
    print("书名      : %s" % info["title"])
    print("作者      : %s" % ("、".join(info["creators"]) or "-"))
    print("OPF       : %s" % info["opf"])
    print("nav.xhtml : %s" % (info["nav"] or "（无）"))
    print("toc.ncx   : %s" % (info["ncx"] or "（无）"))
    print("目录来源  : %s" % info["toc_source"])
    print("条目      : %d 个大标题 / %d 个章节" % (info["counts"]["groups"], info["counts"]["docs"]))
    print("spine     : %d 项，manifest %d 项" % (info["spine"], info["manifest"]))
    for w in info["warnings"]:
        print("  ! %s" % w["text"])
    return 0


def cmd_tree(args) -> int:
    book = EpubBook(args.epub)
    if args.json:
        print(json.dumps([n.to_dict() for n in book.toc], ensure_ascii=False, indent=2))
    else:
        _print_tree(book.toc, show_file=args.files)
    return 0


def cmd_edit(args) -> int:
    session = Session(args.epub)
    ops: List[Dict[str, Any]] = []
    if args.ops:
        with open(args.ops, "r", encoding="utf-8") as f:
            data = json.load(f)
            ops.extend(data if isinstance(data, list) else data.get("ops", []))
    for text in args.op or []:
        ops.extend(_parse_op_args(text))
    if not ops and not args.dry_run:
        print("没有指定任何操作（--op / --ops）", file=sys.stderr)
        return 2

    logs = apply_ops(session, ops)
    for line in logs:
        print("· %s" % line)

    out = args.output or os.path.splitext(args.epub)[0] + ".toc-edited.epub"
    report = session.export(out, sync_spine=not args.no_sync_spine,
                            sync_heading=args.sync_heading, dry_run=args.dry_run)

    print()
    if args.dry_run:
        print("预览模式（未写文件），编辑后的目录：")
        _print_tree(session.toc, show_file=True)
    else:
        print("已导出：%s (%.1f KB)" % (out, report["bytes"] / 1024.0))
    if report["removed_files"]:
        print("已删除文件：%s" % ", ".join(report["removed_files"]))
    if report["created_nav"]:
        print("已补建 nav.xhtml")
    if report["created_ncx"]:
        print("已补建 toc.ncx")
    if report["reordered_spine"]:
        print("已按新目录顺序同步 spine 阅读顺序")

    if args.epubcheck and not args.dry_run:
        report["issues"].extend(run_epubcheck(out))

    print()
    for it in report["issues"]:
        mark = {"ok": "  ✓", "warn": "  !", "error": "  ✗"}[it["level"]]
        print("%s %s" % (mark, it["text"]))
    has_error = any(i["level"] == "error" for i in report["issues"])
    return 1 if has_error else 0


def cmd_validate(args) -> int:
    with open(args.epub, "rb") as f:
        data = f.read()
    issues = validate_bytes(data)
    if args.epubcheck:
        issues.extend(run_epubcheck(args.epub))
    for it in issues:
        mark = {"ok": "✓", "warn": "!", "error": "✗"}[it["level"]]
        print("%s %s" % (mark, it["text"]))
    return 1 if any(i["level"] == "error" for i in issues) else 0


def cmd_append(args) -> int:
    session = Session(args.epub)
    source = args.source
    if source.lower().endswith(".docx"):
        info = session.append_docx(source, group_title=args.group, at=args.at,
                                   split_level=args.split_level,
                                   link_css=not args.no_css)
        print("来源          : DOCX《%s》%s" % (info["title"],
                                                "（作者：%s）" % info["author"] if info["author"] else ""))
        print("切分规则      : 按标题层级 ≤ %d 切章" % info["split_level"])
        print("新增章节      : %d 个，内嵌图片 %d 张" % (info["chapters"], info["images"]))
        print("挂载位置      : %s" % info["where"])
        print("新增文件      : %s" % ", ".join(info["files"][:3]) +
              (" …" if len(info["files"]) > 3 else ""))
    else:
        info = session.append_txt(
            source, group_title=args.group, at=args.at, split=args.split,
            size=args.size, para_mode=args.paragraphs, encoding=args.encoding,
            link_css=not args.no_css, drop_page_numbers=args.drop_page_numbers,
        )
        print("TXT 编码      : %s" % info["encoding"])
        print("切分规则      : %s（命中标题 %d 处）" % (info["split_rule"], info["headings"]))
        print("新增章节      : %d 个%s，共 %d 字"
              % (info["chapters"], "，大标题 %d 个" % info["groups"] if info["groups"] else "",
                 info["chars"]))
        print("挂载位置      : %s" % info["where"])
        print("新增文件      : %s" % ", ".join(info["files"][:3]) +
              (" …" if len(info["files"]) > 3 else ""))

    out = args.output or os.path.splitext(args.epub)[0] + ".with-txt.epub"
    report = session.export(out, sync_spine=not args.no_sync_spine,
                            dry_run=args.dry_run, touch_modified=not args.keep_modified)

    print()
    if args.dry_run:
        print("预览模式（未写文件），追加后的目录：")
        _print_tree(session.toc, show_file=True)
    else:
        print("已导出：%s (%.1f KB)" % (out, report["bytes"] / 1024.0))
    if report["reordered_spine"]:
        print("已同步 spine 阅读顺序")

    if args.epubcheck and not args.dry_run:
        report["issues"].extend(run_epubcheck(out))

    print()
    for it in report["issues"]:
        print("%s %s" % ({"ok": "  ✓", "warn": "  !", "error": "  ✗"}[it["level"]], it["text"]))
    return 1 if any(i["level"] == "error" for i in report["issues"]) else 0


def cmd_fromdocx(args) -> int:
    out = args.output or os.path.splitext(args.docx)[0] + ".epub"
    rep = docx_to_epub(args.docx, out, title=args.title, author=args.author,
                       language=args.language, split_level=args.split_level)
    print("书名      : %s" % rep["title"])
    print("作者      : %s" % (rep["author"] or "-"))
    print("语言      : %s" % rep["language"])
    print("章节      : %d 章 / 图片 %d 张（按标题层级 ≤ %d 切分）"
          % (rep["chapters"], rep["images"], rep["split_level"]))
    print("已生成    : %s (%.1f KB)" % (out, rep["bytes"] / 1024.0))

    issues = list(rep["issues"])
    if args.epubcheck:
        issues.extend(run_epubcheck(out))

    print()
    for it in issues:
        print("%s %s" % ({"ok": "  ✓", "warn": "  !", "error": "  ✗"}[it["level"]], it["text"]))
    return 1 if any(i["level"] == "error" for i in issues) else 0


def cmd_fromtxt(args) -> int:
    out = args.output or os.path.splitext(args.txt)[0] + ".epub"
    rep = txt_to_epub(args.txt, out, title=args.title, author=args.author or "",
                      language=args.language or "zh", split=args.split,
                      size=args.size, para_mode=args.paragraphs,
                      encoding=args.encoding,
                      drop_page_numbers=args.drop_page_numbers)
    print("书名      : %s" % rep["title"])
    print("作者      : %s" % (rep["author"] or "-"))
    print("编码      : %s（%s）" % (rep.get("encoding"), rep.get("split_rule")))
    print("章节      : %d 章 / %d 个大标题" % (rep["chapters"], rep.get("groups", 0)))
    print("已生成    : %s (%.1f KB)" % (out, rep["bytes"] / 1024.0))

    issues = list(rep["issues"])
    if args.epubcheck:
        issues.extend(run_epubcheck(out))
    print()
    for it in issues:
        print("%s %s" % ({"ok": "  ✓", "warn": "  !", "error": "  ✗"}[it["level"]], it["text"]))
    return 1 if any(i["level"] == "error" for i in issues) else 0


def cmd_merge(args) -> int:
    session = Session(args.epub)
    info = session.merge_epubs(args.others, group_each=not args.no_group, prefix=args.prefix)

    print("主书      : %s" % session.book.metadata.get("title"))
    for b in info["books"]:
        print("并入      : 《%s》 %d 章 / %d 组 → %s/" % (b["title"], b["docs"], b["groups"], b["dir"]))
    print("合计新增  : %d 章 / %d 组 / %d 个文件"
          % (info["chapters"], info["groups"], info["files"]))
    for w in info["warnings"]:
        print("  ! %s" % w)

    out = args.output or os.path.splitext(args.epub)[0] + ".merged.epub"
    report = session.export(out, sync_spine=not args.no_sync_spine,
                            dry_run=args.dry_run, touch_modified=not args.keep_modified)

    print()
    if args.dry_run:
        print("预览模式（未写文件），合并后的目录：")
        _print_tree(session.toc, show_file=False)
    else:
        print("已导出：%s (%.1f KB)" % (out, report["bytes"] / 1024.0))

    if args.epubcheck and not args.dry_run:
        report["issues"].extend(run_epubcheck(out))

    print()
    for it in report["issues"]:
        print("%s %s" % ({"ok": "  ✓", "warn": "  !", "error": "  ✗"}[it["level"]], it["text"]))
    return 1 if any(i["level"] == "error" for i in report["issues"]) else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="chapflow",
        description="ChapFlow —— EPUB 目录编辑器（核心功能 / 命令行）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python chapflow.py info book.epub
  python chapflow.py tree book.epub --files
  python chapflow.py edit book.epub -o out.epub --op "rename 1.2 新的标题"
  python chapflow.py edit book.epub -o out.epub --op "move 1.5 2.1" --op "delete 1.9"
  python chapflow.py edit book.epub -o out.epub --op "group-add 第一卷@3"
  python chapflow.py edit book.epub -o out.epub --op "group-wrap 第一卷 1.1 1.2 1.3"
  python chapflow.py edit book.epub --dry-run --ops ops.json
  python chapflow.py append book.epub 新章节.txt -o out.epub --group "外传"
  python chapflow.py merge 主书.epub 外传.epub 番外.epub -o 合集.epub
  python chapflow.py append 书.epub 新章节.docx -o out.epub --group "附录"
  python chapflow.py fromdocx 手稿.docx -o 手稿.epub --split-level 1
  python chapflow.py fromtxt 手稿.txt -o 手稿.epub --author 某人
  python chapflow.py validate out.epub
""")
    p.add_argument("--version", action="version", version="ChapFlow " + __version__)
    sub = p.add_subparsers(dest="cmd")

    pi = sub.add_parser("info", help="查看书籍信息与统计")
    pi.add_argument("epub")
    pi.add_argument("--json", action="store_true")
    pi.set_defaults(func=cmd_info)

    pt = sub.add_parser("tree", help="打印目录树")
    pt.add_argument("epub")
    pt.add_argument("--files", action="store_true", help="显示对应文件路径")
    pt.add_argument("--json", action="store_true")
    pt.set_defaults(func=cmd_tree)

    pe = sub.add_parser("edit", help="编辑目录并导出")
    pe.add_argument("epub")
    pe.add_argument("-o", "--output", help="输出文件（默认 *.toc-edited.epub）")
    pe.add_argument("--op", action="append", metavar="TEXT",
                    help='单条操作，如 "rename 1.2 新标题"')
    pe.add_argument("--ops", metavar="JSON", help="操作列表 JSON 文件")
    pe.add_argument("--no-sync-spine", action="store_true", help="不同步 spine 阅读顺序")
    pe.add_argument("--sync-heading", action="store_true", help="同时改写正文 <title> 与首个 h1-h6")
    pe.add_argument("--dry-run", action="store_true", help="只预览，不写文件")
    pe.add_argument("--epubcheck", action="store_true", help="额外跑 EPUBCheck 规范校验（需 Java）")
    pe.set_defaults(func=cmd_edit)

    pv = sub.add_parser("validate", help="检查 EPUB 结构与目录一致性")
    pv.add_argument("epub")
    pv.add_argument("--epubcheck", action="store_true", help="额外跑 EPUBCheck 规范校验（需 Java）")
    pv.set_defaults(func=cmd_validate)

    pa = sub.add_parser("append", aliases=["append-txt", "append-docx"],
                        help="把 TXT / DOCX 转成章节追加到 EPUB")
    pa.add_argument("epub")
    pa.add_argument("source", metavar="SOURCE", help="TXT 或 DOCX 文件（按扩展名自动识别）")
    pa.add_argument("-o", "--output", help="输出文件（默认 *.with-txt.epub）")
    pa.add_argument("--group", metavar="TITLE", help="把新增章节放进一个新的大标题")
    pa.add_argument("--at", default="end", metavar="POS",
                    help="插入位置：end / 3 / end:2 / 2.1（默认目录末尾）")
    pa.add_argument("--split", default="auto", choices=["auto", "heading", "size", "none"],
                    help="切分方式（默认 auto：有标题按标题，否则按长度）")
    pa.add_argument("--size", type=int, default=4000, help="按长度切分时每章约多少字（默认 4000）")
    pa.add_argument("--paragraphs", default="line", choices=["line", "blank", "indent"],
                    help="段落判定：line 每行一段 / blank 空行分段 / indent 缩进分段")
    pa.add_argument("--encoding", help="强制指定 TXT 编码（utf-8 / gbk / big5 …）")
    pa.add_argument("--no-css", action="store_true", help="不继承书内样式表")
    pa.add_argument("--drop-page-numbers", action="store_true", help="丢弃纯页码行")
    pa.add_argument("--split-level", type=int, default=1, metavar="N",
                    help="DOCX 按几级标题切章（默认 1，即“标题 1 / Heading 1”）")
    pa.add_argument("--no-sync-spine", action="store_true", help="不重排 spine 阅读顺序")
    pa.add_argument("--keep-modified", action="store_true", help="不改写 dcterms:modified 时间戳")
    pa.add_argument("--dry-run", action="store_true", help="只预览，不写文件")
    pa.add_argument("--epubcheck", action="store_true", help="额外跑 EPUBCheck 规范校验（需 Java）")
    pa.set_defaults(func=cmd_append)

    pf = sub.add_parser("fromdocx", aliases=["docx2epub"],
                        help="把 DOCX 直接转换成一本新的 EPUB")
    pf.add_argument("docx")
    pf.add_argument("-o", "--output", help="输出文件（默认与 DOCX 同名 .epub）")
    pf.add_argument("--title", help="书名（默认取 DOCX 属性或文件名）")
    pf.add_argument("--author", help="作者（默认取 DOCX 属性）")
    pf.add_argument("--language", help="语言代码（默认取 DOCX 属性，缺省 zh）")
    pf.add_argument("--split-level", type=int, default=1, metavar="N",
                    help="按几级标题切章（默认 1）")
    pf.add_argument("--epubcheck", action="store_true", help="额外跑 EPUBCheck 规范校验（需 Java）")
    pf.set_defaults(func=cmd_fromdocx)

    pt = sub.add_parser("fromtxt", aliases=["txt2epub"],
                        help="把 TXT 直接转换成一本新的 EPUB")
    pt.add_argument("txt")
    pt.add_argument("-o", "--output", help="输出文件（默认与 TXT 同名 .epub）")
    pt.add_argument("--title", help="书名（默认取文件名）")
    pt.add_argument("--author", help="作者")
    pt.add_argument("--language", default="zh", help="语言代码（默认 zh）")
    pt.add_argument("--split", default="auto", choices=["auto", "heading", "size", "none"],
                    help="切分方式（默认 auto）")
    pt.add_argument("--size", type=int, default=4000, help="按字数切分时每章字数")
    pt.add_argument("--paragraphs", default="line", choices=["line", "blank", "indent"])
    pt.add_argument("--encoding", help="强制指定编码")
    pt.add_argument("--drop-page-numbers", action="store_true", help="丢弃纯页码行")
    pt.add_argument("--epubcheck", action="store_true", help="额外跑 EPUBCheck（需 Java）")
    pt.set_defaults(func=cmd_fromtxt)

    pm = sub.add_parser("merge", help="把多本 EPUB 合并成一本（资源自动隔离重命名）")
    pm.add_argument("epub", help="主书：元数据、封面与阅读起点以它为准")
    pm.add_argument("others", nargs="+", help="要并入的一本或多本 EPUB")
    pm.add_argument("-o", "--output", help="输出文件（默认 *.merged.epub）")
    pm.add_argument("--no-group", action="store_true", help="不按书名包一层大标题，直接平铺")
    pm.add_argument("--prefix", default="merged", help="合并资源的目录前缀（默认 merged）")
    pm.add_argument("--no-sync-spine", action="store_true", help="不重排 spine 阅读顺序")
    pm.add_argument("--keep-modified", action="store_true", help="不改写 dcterms:modified 时间戳")
    pm.add_argument("--dry-run", action="store_true", help="只预览，不写文件")
    pm.add_argument("--epubcheck", action="store_true", help="额外跑 EPUBCheck 规范校验（需 Java）")
    pm.set_defaults(func=cmd_merge)
    return p



def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except (ValueError, FileNotFoundError) as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 1
    except zipfile.BadZipFile:
        print("错误：不是有效的 ZIP/EPUB 文件", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
