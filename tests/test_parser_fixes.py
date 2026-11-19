# -*- coding: utf-8 -*-
"""
解析器修复回归测试（P1/P2/P3）

覆盖审查发现的编码误判、KV 兜底正则误伤、属性转义、URL 校验绕过、
灾难正则护栏、DOCX 解析异常可读化与「引言」标题识别。
可直接运行：python tests/test_parser_fixes.py
"""
import os
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import docxconv                # noqa: E402
import llmseg                  # noqa: E402
import txtconv                 # noqa: E402
from ui import splitmem        # noqa: E402

_passed = 0
_failed = []


def check(cond, msg):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(msg)
        print("  ✗ %s" % msg)


def eq(actual, expected, msg):
    check(actual == expected, "%s（期望 %r，实际 %r）" % (msg, expected, actual))


# --- P1-2: 散文里的 i=2 不再被当成决策 -------------------------------------
def test_prose_kv_not_decision():
    got = llmseg.parse_decisions("我认为 i=2 是标题", [1, 2, 3])
    check(not got, "散文中 i=2 不应解析为决策（实际 %r）" % (got,))
    # 正常 KV 行仍要能识别
    got2 = llmseg.parse_decisions("i=2 head=true lv=2 t=第一章", [1, 2, 3])
    check(bool(got2) and 2 in got2, "正规 KV 行仍应解析出序号 2")


# --- P1-3: 属性用 escape_attr，元素内容用 escape_text -----------------------
def test_docx_attr_escape():
    evil = 'x" onload="alert(1)'
    out = docxconv.wrap_xhtml("body", "<b>&", language=evil, css_href=evil)
    # 属性值里的引号应被转义，无法闭合出 onload 属性
    check('onload="alert(1)"' not in out, "完整属性注入串不应出现在输出中")
    check("&quot;" in out, "属性值中的引号应被转义为 &quot;")
    # <title>/<h1> 元素内容仍按文本转义
    check("&lt;b&gt;&amp;" in out, "元素内容仍用 escape_text 转义")


# --- P1-1: 无 BOM Big5 自动识别，带 BOM UTF-16 仍正常 -----------------------
def test_big5_without_bom():
    text = "第一章 測試標題"
    raw = text.encode("big5")
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(raw)
        path = f.name
    try:
        got, enc = txtconv.read_text_file(path)
        eq(enc, "big5", "无 BOM Big5 应识别为 big5")
        check("測試標題" in got, "Big5 文本应正确解码")
    finally:
        os.unlink(path)


def test_utf16_with_bom():
    text = "第一章 標題"
    raw = text.encode("utf-16")
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(raw)
        path = f.name
    try:
        got, enc = txtconv.read_text_file(path)
        check("標題" in got, "带 BOM 的 UTF-16 应正确解码（编码 %s）" % enc)
        check("\ufeff" not in got, "BOM 应被去除")
    finally:
        os.unlink(path)


# --- P2-5: 换行 URL 被拒 ----------------------------------------------------
def test_newline_url_rejected():
    evil = "http://example.com\nGET /admin"
    o = llmseg.LLMOptions(enabled=True, base_url=evil, api_key="k", model="m")
    check(bool(o.problem()), "含换行的 URL 必须被拒绝")
    o2 = llmseg.LLMOptions(enabled=True, base_url="https://api.example.com/v1",
                           api_key="k", model="m")
    eq(o2.problem(), "", "合法 https URL 应通过校验")


# --- P2-6: 两类灾难正则被拒，内置预设仍可编译 -------------------------------
def test_catastrophic_regex_rejected():
    for pat in ("(a|a?)+b$", "(a|aa)*b$", "(a+)+$"):
        try:
            txtconv.compile_user_regex(pat)
            check(False, "灾难正则 %r 应被拒绝" % pat)
        except txtconv.TxtError:
            check(True, "灾难正则 %r 被拒绝" % pat)


def test_builtin_presets_compile():
    presets = splitmem.builtin_presets()
    eq(len(presets), 13, "内置预设应为 13 条")
    for p in presets:
        params = p["params"]
        pat = params.get("pattern") or params.get("extra")
        if not pat:
            continue
        try:
            txtconv.compile_user_regex(pat)
            check(True, "预设 %s 的正则可编译" % p["name"])
        except txtconv.TxtError as exc:
            check(False, "预设 %s 的正则不应被护栏误杀：%s" % (p["name"], exc))


# --- P2-4(docx): document.xml 解析失败抛可读 DocxError ----------------------
def test_docx_bad_xml_readable_error():
    path = os.path.join(tempfile.gettempdir(), "chapflow_bad_docx.docx")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", "<w:document><broken></w:document>")
    try:
        try:
            docxconv.read_docx(path)
            check(False, "损坏的 document.xml 应抛 DocxError")
        except docxconv.DocxError as exc:
            check("DOCX" in str(exc),
                  "异常信息应可读（实际 %s）" % exc)
    finally:
        os.unlink(path)


# --- P3: match_heading('引言') 有结果 ---------------------------------------
def test_yinyan_heading():
    got = txtconv.match_heading("引言")
    check(got is not None, "match_heading('引言') 应有结果")
    if got:
        eq(got[1], "引言", "识别出的标题文字应为 引言")


def main():
    for fn in (
        test_prose_kv_not_decision,
        test_docx_attr_escape,
        test_big5_without_bom,
        test_utf16_with_bom,
        test_newline_url_rejected,
        test_catastrophic_regex_rejected,
        test_builtin_presets_compile,
        test_docx_bad_xml_readable_error,
        test_yinyan_heading,
    ):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            check(False, "%s 抛出异常：%r" % (fn.__name__, exc))
    print("\n通过 %d，失败 %d" % (_passed, len(_failed)))
    if _failed:
        for msg in _failed:
            print("  ✗ %s" % msg)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
