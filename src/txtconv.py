#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TXT → EPUB 章节转换
===================

职责单一：把纯文本按章节标题切分，并生成合规 XHTML 片段。
不依赖第三方库，被 chapflow 调用，也可单独测试。
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 编码
# ---------------------------------------------------------------------------

ENCODING_CANDIDATES = ("utf-8-sig", "utf-8", "utf-16", "gb18030", "big5", "utf-16-le", "utf-16-be")

_BINARY_HINT_BYTES = bytes(range(0, 8)) + bytes(range(14, 32))


class TxtError(ValueError):
    """TXT 处理相关的可读错误"""


def read_text_file(path: str, encoding: Optional[str] = None) -> Tuple[str, str]:
    """读取 TXT，返回 (归一化文本, 实际编码)。失败时抛出带建议的 TxtError。"""
    if not os.path.exists(path):
        raise TxtError("TXT 文件不存在：%s" % path)
    if os.path.isdir(path):
        raise TxtError("这是一个文件夹，不是 TXT 文件：%s" % path)
    size = os.path.getsize(path)
    if size == 0:
        raise TxtError("TXT 文件为空（0 字节），无法生成章节：%s" % path)

    with open(path, "rb") as f:
        raw = f.read()
    if not raw.strip():
        raise TxtError("TXT 内容为空（只有空白字符），无法生成章节：%s" % path)

    # 明显的二进制文件
    if b"\x00" in raw[:4096] and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise TxtError("文件看起来不是纯文本（包含 NUL 字节）：%s" % path)

    tried = []
    order = ([encoding] if encoding else []) + [e for e in ENCODING_CANDIDATES if e != encoding]
    for enc in order:
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            tried.append(enc)
            continue
        if _looks_garbage(text):
            tried.append(enc)
            continue
        return normalize_text(text), enc

    raise TxtError(
        "无法识别文本编码（已尝试：%s）。请用 --encoding 指定，例如 --encoding gbk / utf-8"
        % ", ".join(tried)
    )


def _looks_garbage(text: str) -> bool:
    sample = text[:4000]
    if not sample:
        return True
    bad = sum(1 for ch in sample if ch == "\ufffd" or (ord(ch) < 32 and ch not in "\t\n\r\f\v"))
    return bad / max(1, len(sample)) > 0.02


def normalize_text(text: str) -> str:
    """统一换行、去 BOM/零宽字符、去控制字符，保留正常全角空格"""
    if text.startswith("\ufeff"):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
    text = text.replace("\f", "\n").replace("\v", "\n")
    # XML 1.0 不允许的控制字符
    text = "".join(ch for ch in text
                   if ch in "\t\n" or unicodedata.category(ch) != "Cc")
    # 全角空格保留在行首（用于段落判定），其它位置保留
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text


# ---------------------------------------------------------------------------
# 标题识别
# ---------------------------------------------------------------------------

_CN_NUM = r"[零〇一二三四五六七八九十百千两0-9]{1,8}"

_HEADING_RULES: List[Tuple[int, "re.Pattern[str]"]] = [
    # (层级 1=大标题/卷, 2=章节)
    (1, re.compile(r"^第\s*" + _CN_NUM + r"\s*[卷部篇集]\s*.{0,30}$")),
    (2, re.compile(r"^第\s*" + _CN_NUM + r"\s*[章回节话]\s*.{0,30}$")),
    (2, re.compile(r"^(序章|序言|自序|楔子|引子|前言|后记|尾声|终章|番外篇?|附录|作者的话|内容简介|简介)\s*.{0,30}$")),
    (2, re.compile(r"^(?i:chapter|chap\.?|ch\.)\s*([0-9]{1,4}|[ivxlcdm]{1,8})\b.{0,40}$")),
    (1, re.compile(r"^(?i:part|book|volume)\s+([0-9]{1,4}|[ivxlcdm]{1,8})\b.{0,40}$")),
]

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")

MAX_TITLE_LEN = 45


class SplitRules:
    """自定义分章规则（供 GUI / CLI 传入，未提供时用内置 match_heading）"""

    __slots__ = ("mode", "indent", "prefix", "numerals", "unit", "extra",
                 "pattern", "size", "builtin")

    def __init__(self, mode: str = "simple", indent: bool = False, prefix: str = "第",
                 numerals: str = "any", unit: str = "章", extra: str = "",
                 pattern: str = "", size: int = 4000, builtin: bool = True):
        self.mode = mode            # simple | regex | size | none
        self.indent = indent        # 要求行首有空白
        self.prefix = prefix        # 编号前文字，如「第」
        self.numerals = numerals    # cn | digit | any
        self.unit = unit            # 章 / 节 / 回 / 卷 …
        self.extra = extra          # 附加正则（命中即视为标题）
        self.pattern = pattern      # regex 模式下的自定义正则
        self.size = size
        self.builtin = builtin      # 简易规则下是否同时识别内置的序章/卷/Chapter 等写法

    def to_dict(self) -> Dict[str, object]:
        return {k: getattr(self, k) for k in self.__slots__}


_NUMERALS = {
    "cn": r"[零〇一二三四五六七八九十百千两]{1,8}",
    "digit": r"[0-9]{1,8}",
    "any": r"[零〇一二三四五六七八九十百千两0-9]{1,8}",
}
_TITLE_TAIL = ("。", "，", "！", "？", "；", "…", ".", ",", ";", "!")


def build_matcher(rules: SplitRules):
    """按规则生成 (raw_line, stripped) -> (level, title) | None 的匹配函数"""
    if rules.mode == "simple":
        num = _NUMERALS.get(rules.numerals) or _NUMERALS["any"]
        # 注意：必须用「等于某个卷部篇集字」，写成 `unit in "卷部篇集"` 会让空字符串也命中
        level = 1 if (rules.unit or "") in ("卷", "部", "篇", "集") else 2
        head = r"^\s+" if rules.indent else r"^\s*"
        main = re.compile(head + re.escape(rules.prefix or "") + r"\s*" + num
                          + r"\s*" + re.escape(rules.unit or "") + r".{0,30}$")
        extra = None
        if (rules.extra or "").strip():
            try:
                extra = re.compile(rules.extra)
            except re.error as exc:
                raise TxtError("附加规则正则无效：%s" % exc)

        def match(raw: str, s: str):
            if not s or len(s) > MAX_TITLE_LEN:
                return None
            if s.endswith(_TITLE_TAIL):
                return None
            if main.match(raw if rules.indent else s):
                return level, s
            if extra is not None and extra.search(s):
                return 2, s
            if rules.builtin:
                # 勾了"行首空格"时，内置规则也必须满足缩进，避免被放宽
                if rules.indent and not raw[:1].isspace():
                    return None
                return match_heading(s)
            return None

        return match

    if rules.mode == "regex":
        if not (rules.pattern or "").strip():
            raise TxtError("正则表达式为空")
        try:
            rx = re.compile(rules.pattern)
        except re.error as exc:
            raise TxtError("正则表达式无效：%s" % exc)

        def match(raw: str, s: str):
            if not s:
                return None
            # 与内置规则同样的护栏：标题不会是一整段正文
            if len(s) > MAX_TITLE_LEN or s.endswith(_TITLE_TAIL):
                return None
            return (2, s) if rx.search(s) else None

        return match

    return None


def match_heading(line: str) -> Optional[Tuple[int, str]]:
    """命中标题返回 (level, title)，否则 None。line 需已 strip。"""
    if not line or len(line) > MAX_TITLE_LEN:
        return None
    if line.endswith(("。", "，", "！", "？", "；", "…", ".", ",", ";", "!")):
        return None
    m = _MD_HEADING.match(line)
    if m:
        level = 1 if len(m.group(1)) <= 2 else 2
        return level, m.group(2).strip()
    for level, rx in _HEADING_RULES:
        if rx.match(line):
            return level, line
    return None


# ---------------------------------------------------------------------------
# 切分
# ---------------------------------------------------------------------------

def _llm_refine(text: str, marks: List[Tuple[int, int, str]], llm: object,
                mode: str) -> Tuple[List[Tuple[int, int, str]], Optional[Dict[str, object]]]:
    """把 marks 交给 llmseg 复核。任何失败都保留原 marks（或升级成可读的 TxtError）。

    延迟导入 llmseg：没装 / 没用的环境不需要加载它，也不形成循环导入。
    """
    try:
        import llmseg  # type: ignore
    except ImportError:  # pragma: no cover - 单独运行 src 目录时的兜底
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        try:
            import llmseg  # type: ignore
        except ImportError:
            return marks, {"used": False, "degraded": True,
                           "reason": "找不到 llmseg 模块，已跳过 LLM 复核"}
    try:
        new_marks, info = llmseg.refine_marks(text, marks, llm)
    except Exception as exc:   # fallback=False 时会走到这里
        raise TxtError("LLM 辅助切分失败：%s" % exc)
    info = dict(info or {})
    info.setdefault("mode", mode)
    return [(int(a), int(b), str(c)) for a, b, c in new_marks], info


class Chapter:
    __slots__ = ("title", "level", "paragraphs", "char_count")

    def __init__(self, title: str, level: int = 2, paragraphs: Optional[List[str]] = None):
        self.title = title
        self.level = level
        self.paragraphs = paragraphs or []
        self.char_count = sum(len(p) for p in self.paragraphs)

    def __repr__(self) -> str:  # pragma: no cover
        return "<Chapter %s %d chars>" % (self.title, self.char_count)


def split_chapters(text: str, fallback_title: str, mode: str = "auto",
                   size: int = 4000, para_mode: str = "line",
                   drop_page_numbers: bool = False,
                   rules: Optional["SplitRules"] = None,
                   llm: Optional[object] = None) -> Tuple[List[Chapter], Dict[str, object]]:
    """
    mode: auto（有标题按标题，否则按长度）/ heading（只用标题）/ size（只按长度）/ none（整篇一章）
    para_mode: line（每行一段）/ blank（空行分段）/ indent（缩进分段）
    llm: 可选，LLM 辅助裁决标题行。None 时行为与不带该参数完全一致。
         可传 llmseg.LLMOptions / dict / 配置文件路径 / 自带 refine() 的裁决器；
         只在 mode 为 auto / heading 时生效（size / none 是显式指定的切法，不干预）。
    """
    matcher = None
    if rules is not None:
        if rules.mode in ("simple", "regex"):
            matcher = build_matcher(rules)
            mode = "heading"
        elif rules.mode == "size":
            mode, size = "size", rules.size
        elif rules.mode == "none":
            mode = "none"

    lines = text.split("\n")
    marks: List[Tuple[int, int, str]] = []  # (line_index, level, title)
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s:
            continue
        hit = matcher(raw, s) if matcher else match_heading(s)
        if hit:
            marks.append((i, hit[0], hit[1]))

    # LLM 复核：只增删标题行，正文与段落归并仍走下面的原逻辑
    llm_info: Optional[Dict[str, object]] = None
    if llm is not None and mode in ("auto", "heading"):
        marks, llm_info = _llm_refine(text, marks, llm, mode)

    stats: Dict[str, object] = {"lines": len(lines), "headings": len(marks), "encodings": {}}
    if llm_info:
        stats["llm"] = llm_info

    if mode == "none":
        body = _paragraphs(lines, 0, len(lines), para_mode, drop_page_numbers)
        return [Chapter(fallback_title, 2, body)], stats

    if mode == "size" or (mode == "auto" and not marks):
        stats["rule"] = "size" if not marks else "heading+size"
        return _split_by_size(lines, fallback_title, size, para_mode, drop_page_numbers,
                              stats, had_headings=bool(marks)), stats

    if mode == "heading" and not marks:
        hint = ""
        if llm_info and llm_info.get("degraded"):
            hint = "（LLM 复核未生效：%s）" % (llm_info.get("reason") or "原因未知")
        raise TxtError("未识别到任何章节标题：请调整「简易规则」的前缀/单位/数字类型，"
                       "或改用「正则表达式」「按字数均分」" + hint)

    chapters: List[Chapter] = []

    # 第一个标题之前的正文
    if marks and marks[0][0] > 0:
        body = _paragraphs(lines, 0, marks[0][0], para_mode, drop_page_numbers)
        if sum(len(p) for p in body) >= 20:
            chapters.append(Chapter("前言", 2, body))

    for idx, (line_no, level, title) in enumerate(marks):
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(lines)
        body = _paragraphs(lines, line_no + 1, end, para_mode, drop_page_numbers)
        chapters.append(Chapter(_clean_title(title), level, body))

    stats["rule"] = "heading+llm" if (llm_info and llm_info.get("used")) else "heading"
    return chapters, stats


# 编号与章节名之间常见的分隔符：第二章.xxx / 第二章·xxx / 第二章、xxx / 第二章：xxx
_TITLE_SEPS = ".．·‚、,，:：;；-—–_|/"

# 需要跟着编号一起规范的「无编号」章节名：序章.开端 → 序章 开端
# 这份名单同时供 chapflow.is_special_chapter（编号时跳过这些章）使用，
# 别再各自维护一份：两处不一致会让「内容简介」这类标题在导入/编号里表现不同。
SPECIAL_HEADS = ("序章", "序言", "自序", "楔子", "引子", "引言", "前言",
                 "后记", "尾声", "终章", "番外", "附录", "作者的话", "内容简介")
_SPECIAL_HEAD = SPECIAL_HEADS


def tidy_title(title: str) -> str:
    """编号与章节名之间的分隔符统一成一个空格：第二章.俾斯麦 → 第二章 俾斯麦

    只处理「第X章 / Chapter N」这类编号标题；没有编号的标题原样返回。
    原本就用空格（含全角空格）分开的，沿用原来的空格，避免整本书被无端改写。
    """
    t = (title or "").strip()
    if not t:
        return t
    m = re.match(r"^(第\s*[零〇一二三四五六七八九十百千两0-9]{1,8}\s*[卷部篇集章回节话]"
                 r"|(?i:chapter|part|book|volume)\s*[0-9ivxlcdm]{1,8}"
                 r"|" + "|".join(_SPECIAL_HEAD) + r")(.*)$", t)
    if not m:
        return t
    head, rest = m.group(1), m.group(2)
    if not rest:
        return t
    if rest[:1] in (" ", "\u3000"):
        body = rest.lstrip(" \u3000\t")
        while body[:1] and body[0] in _TITLE_SEPS:
            body = body[1:].lstrip()
        return (head + rest[:1] + body) if body else head
    body = rest
    while body[:1] and body[0] in _TITLE_SEPS:
        body = body[1:].lstrip()
    return (head + " " + body) if body else head


def _clean_title(title: str) -> str:
    t = title.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    t = re.sub(r" {2,}", " ", t).strip()
    t = tidy_title(t)
    return t[:120] if len(t) > 120 else t


def _split_by_size(lines: List[str], title: str, size: int, para_mode: str,
                   drop_pn: bool, stats: Dict[str, object], had_headings: bool) -> List[Chapter]:
    size = max(500, int(size))
    chunks: List[Chapter] = []
    buf: List[str] = []
    buf_len = 0
    part = 1

    def flush() -> None:
        nonlocal buf, buf_len, part
        if not buf:
            return
        paras = _paragraphs("\n".join(buf).split("\n"), 0, len(buf), para_mode, drop_pn)
        if paras:
            name = title if len(chunks) == 0 and buf_len >= size // 2 else "%s（%d）" % (title, part)
            chunks.append(Chapter(name, 2, paras))
            part += 1
        buf = []
        buf_len = 0

    for line in lines:
        buf.append(line)
        buf_len += len(line)
        if buf_len >= size:
            # 尽量在段落边界断开：遇到空行时切
            if not line.strip() or buf_len >= size * 1.3:
                flush()
    flush()

    if not chunks:
        chunks = [Chapter(title, 2, [])]
    stats["rule"] = "size" if not had_headings else "heading+size"
    stats["chunks"] = len(chunks)
    return chunks


def _paragraphs(lines: List[str], start: int, end: int, mode: str,
                drop_pn: bool) -> List[str]:
    out: List[str] = []
    if mode == "blank":
        block: List[str] = []
        for i in range(start, end):
            s = lines[i].strip()
            if not s:
                if block:
                    out.append(_join(block))
                    block = []
                continue
            if drop_pn and _is_page_number(s):
                continue
            block.append(s)
        if block:
            out.append(_join(block))
    elif mode == "indent":
        cur: List[str] = []
        for i in range(start, end):
            raw = lines[i]
            s = raw.strip()
            if not s:
                if cur:
                    out.append(_join(cur))
                    cur = []
                continue
            if drop_pn and _is_page_number(s):
                continue
            indented = raw.startswith(("　　", "    ", "\t", "  "))
            if indented and cur:
                out.append(_join(cur))
                cur = [s]
            elif not cur:
                cur = [s]
            else:
                cur.append(s)
        if cur:
            out.append(_join(cur))
    else:  # line
        for i in range(start, end):
            s = lines[i].strip()
            if not s:
                continue
            if drop_pn and _is_page_number(s):
                continue
            out.append(s)
    return [p for p in out if p]


def _join(buf: List[str]) -> str:
    out = buf[0]
    for nxt in buf[1:]:
        if not out:
            out = nxt
            continue
        prev_ch = out[-1]
        next_ch = nxt[:1]
        if (prev_ch.isascii() and prev_ch.isalnum() and next_ch.isascii() and next_ch.isalnum()):
            out += " " + nxt
        else:
            out += nxt
    return out.strip()


def _is_page_number(s: str) -> bool:
    return bool(re.fullmatch(r"[-—\s]*\d{1,4}[-—\s]*", s))


# ---------------------------------------------------------------------------
# XHTML 生成
# ---------------------------------------------------------------------------

CONTROL_CHARS = set(chr(c) for c in list(range(0, 32)) if c not in (9, 10)) | {"\uFFFE", "\uFFFF"}


def xml_escape(s: str, attr: bool = False) -> str:
    out = []
    for ch in s:
        if ch in CONTROL_CHARS:
            continue
        if ch == "&":
            out.append("&amp;")
        elif ch == "<":
            out.append("&lt;")
        elif ch == ">":
            out.append("&gt;")
        elif attr and ch == '"':
            out.append("&quot;")
        else:
            out.append(ch)
    return "".join(out)


XHTML11_DOCTYPE = ('<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
                   '"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">')
HTML5_DOCTYPE = "<!DOCTYPE html>"

BASE_STYLE = (
    "body{margin:0 5%;line-height:1.6;}\n"
    "h1{font-size:1.4em;margin:1.6em 0 1em;text-align:center;line-height:1.4;}\n"
    "p{margin:0.55em 0;text-indent:2em;}\n"
)


def build_xhtml(chapter: Chapter, *, epub_version: str = "3.0", language: str = "zh",
                css_href: Optional[str] = None, include_style: bool = True) -> str:
    """生成单个章节的 XHTML 文档"""
    is_epub2 = str(epub_version).startswith("2")
    head = ['<?xml version="1.0" encoding="utf-8"?>', HTML5_DOCTYPE if not is_epub2 else XHTML11_DOCTYPE]
    charset_meta = ('<meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>'
                    if is_epub2 else '<meta charset="utf-8"/>')
    head.append('<html xmlns="http://www.w3.org/1999/xhtml"%s>'
                % (' xml:lang="%s" lang="%s"' % (xml_escape(language or "zh", True),
                                                 xml_escape(language or "zh", True))))
    head.append("<head>")
    head.append(charset_meta)
    head.append("<title>%s</title>" % xml_escape(chapter.title or "未命名章节"))
    if include_style:
        head.append('<style type="text/css">%s</style>' % BASE_STYLE)
    if css_href:
        head.append('<link rel="stylesheet" type="text/css" href="%s"/>' % xml_escape(css_href, True))
    head.append("</head>")
    head.append("<body>")

    body = ['<div class="txt-chapter">',
            "<h1>%s</h1>" % xml_escape(chapter.title or "未命名章节")]
    for p in chapter.paragraphs:
        body.append("<p>%s</p>" % xml_escape(p))
    body.append("</div>")
    head.extend(body)
    head.append("</body>")
    head.append("</html>")
    return "\n".join(head) + "\n"


def safe_filename(title: str, fallback: str = "chapter") -> str:
    """把标题变成安全的文件名片段（保留中文，去掉路径与非法字符）"""
    t = re.sub(r'[\\/:*?"<>|\r\n\t]', "", title or "").strip().strip(".")
    t = re.sub(r"\s+", "-", t)
    if not t:
        t = fallback
    return t[:40]
