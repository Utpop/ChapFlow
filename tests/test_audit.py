# -*- coding: utf-8 -*-
"""
ChapFlow 全面审计测试
=====================

覆盖：解析健壮性 / 路径工具 / 目录模型 / 撤销重做 / 资源释放 /
      元数据 / 导出边界 / TXT / 编号 / 合并 / CLI。

与 run_tests.py 的区别：本套件专门针对**异常输入与边界条件**，
对会导致死循环的用例用子进程 + 超时隔离，避免拖垮整个测试进程。

    python tests/test_audit.py
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "src"))
FIX = os.path.join(HERE, "fixtures")
sys.path.insert(0, SRC)

import chapflow as E          # noqa: E402
import xmldom                 # noqa: E402
import txtconv                # noqa: E402

_passed = 0
_failed: list = []
_current = ""


def section(name: str) -> None:
    global _current
    _current = name
    print("\n[%s]" % name)


def check(cond: bool, msg: str) -> None:
    global _passed
    if cond:
        _passed += 1
        print("  ✓ %s" % msg)
    else:
        _failed.append("[%s] %s" % (_current, msg))
        print("  ✗ %s" % msg)


def eq(actual, expected, msg: str) -> None:
    check(actual == expected, "%s（期望 %r，实际 %r）" % (msg, expected, actual))


def raises(fn, exc=Exception, msg: str = "") -> None:
    try:
        fn()
    except exc:
        check(True, msg or "应抛出异常 %s" % exc.__name__)
        return
    except Exception as other:
        check(False, "%s（抛出了 %s: %s，期望 %s）"
              % (msg or "应抛异常", type(other).__name__, other, exc.__name__))
        return
    check(False, "%s（未抛异常）" % (msg or "应抛异常"))


# ---------------------------------------------------------------------------
# 子进程隔离：用于可能死循环的用例
# ---------------------------------------------------------------------------

def run_isolated(code: str, timeout: int = 8):
    """在子进程里跑一段代码，返回 (是否超时, 返回码, 输出)"""
    script = os.path.join(HERE, "_iso_tmp.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write("import sys, os\nsys.path.insert(0, %r)\n" % SRC)
        f.write(textwrap.dedent(code))
    try:
        p = subprocess.run([sys.executable, "-u", script],
                           capture_output=True, timeout=timeout)
        return False, p.returncode, p.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return True, -1, ""
    finally:
        try:
            os.remove(script)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 1. XML 解析健壮性
# ---------------------------------------------------------------------------

def test_xml_parser():
    section("1. XML 解析健壮性")

    # 1.1 文本中的裸 '<' —— 会触发无限循环
    for bad in ("<p>a < b</p>", "<p>1<2</p>", "<p>x<i</p>", "<p>a <b</p>"):
        hung, rc, out = run_isolated(
            "import xmldom\n"
            "import sys\n"
            "d = xmldom.parse_xml(%r)\n"
            "print(xmldom.serialize_xml(d))\n" % bad, timeout=6)
        check(not hung, "parse_xml 不死循环：%s" % bad)

    # 1.2 未知实体不应关闭整体转义（会产出非法 XML）
    doc = xmldom.parse_xml("<p>x &foo; & y</p>")
    out = xmldom.serialize_xml(doc)
    # 未知实体原文要留住，同时裸 & 必须变成 &amp;（去掉实体后不该再有裸 &）
    stripped = re.sub(r"&(?:#[0-9]+|#x[0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);", "", out)
    eq(out, "<p>x &foo; &amp; y</p>", "未知实体保留原文、裸 & 被转义")
    check("&" not in stripped, "转义后不含裸 &（实际输出：%s）" % out)

    # 1.3 正常实体往返
    doc = xmldom.parse_xml("<p>a &amp; b &lt; c &gt; d</p>")
    eq(xmldom.serialize_xml(doc), "<p>a &amp; b &lt; c &gt; d</p>", "标准实体往返保真")

    # 1.4 属性转义
    doc = xmldom.parse_xml('<a href="x?a=1&amp;b=2"/>')
    a = xmldom.child_els(doc, "a")[0]
    a.set("href", 'q"z')
    check('&quot;' in xmldom.serialize_xml(doc), "属性值中的引号被转义")

    # 1.5 注释 / CDATA / PI / DOCTYPE 保真
    src = ('<?xml version="1.0"?>\n<!DOCTYPE html>\n<!-- c -->\n'
           '<r><![CDATA[<raw>]]></r>')
    out = xmldom.serialize_xml(xmldom.parse_xml(src))
    check("<!-- c -->" in out and "<![CDATA[<raw>]]>" in out
          and "<!DOCTYPE html>" in out, "注释/CDATA/DOCTYPE 保真")

    # 1.6 未闭合标签不应死循环
    hung, _, _ = run_isolated(
        "import xmldom\nxmldom.parse_xml('<r><a><b></a>')\nprint('ok')", timeout=6)
    check(not hung, "未闭合标签不死循环")

    # 1.7 空标签 / 自闭合
    eq(xmldom.serialize_xml(xmldom.parse_xml("<br/>")), "<br/>", "自闭合标签保真")

    # 1.8 detach 不存在的子节点
    r = xmldom.el("r")
    check(xmldom.detach(r, xmldom.el("x")) is False, "detach 不存在子节点返回 False")


# ---------------------------------------------------------------------------
# 2. 路径与链接工具
# ---------------------------------------------------------------------------

def test_paths():
    section("2. 路径与链接工具")

    eq(E.resolve_href("OEBPS/Text", "../Images/c.png"),
       ("OEBPS/Images/c.png", ""), "相对路径上溯")
    eq(E.resolve_href("OEBPS", "Text/c.xhtml#p1"),
       ("OEBPS/Text/c.xhtml", "p1"), "带锚点解析")
    eq(E.resolve_href("OEBPS", "/abs/x.xhtml"), ("abs/x.xhtml", ""), "绝对路径")
    eq(E.resolve_href("OEBPS", "a/b/../../../c.xhtml"), ("c.xhtml", ""), "上溯越界被夹住")
    eq(E.resolve_href("OEBPS", "http://x/y"), None, "外链返回 None")
    eq(E.resolve_href("OEBPS", ""), None, "空 href 返回 None")

    eq(E.relative_path("OEBPS/Text", "OEBPS/Text/a.xhtml"), "a.xhtml", "同目录相对路径")
    eq(E.relative_path("OEBPS/Text", "OEBPS/Images/c.png"), "../Images/c.png", "跨目录相对路径")
    eq(E.relative_path("", "a.xhtml"), "a.xhtml", "根目录相对路径")
    eq(E.relative_path("OEBPS", "OEBPS"), "", "同路径相对为空")

    # 编码：中文保留，空格编码
    eq(E.encode_href("Text/第 一 章.xhtml"), "Text/%E7%AC%AC%20%E4%B8%80%20%E7%AB%A0.xhtml",
       "href 只编码必要字符")

    # parse_path 边界
    eq(E.parse_path("1.2.3"), [0, 1, 2], "多级路径")
    raises(lambda: E.parse_path("0"), ValueError, "序号 0 报错")
    raises(lambda: E.parse_path(""), ValueError, "空路径报错")
    raises(lambda: E.parse_path("a"), ValueError, "非数字报错")

    # split_anchor
    eq(E.split_anchor("a.xhtml"), ("a.xhtml", ""), "无锚点")
    eq(E.split_anchor("a.xhtml#x"), ("a.xhtml", "x"), "有锚点")
    eq(E.split_anchor("#x"), ("", "x"), "纯锚点")


# ---------------------------------------------------------------------------
# 3. 目录模型
# ---------------------------------------------------------------------------

def mk(kind, title, f=None, kids=None):
    n = E.Node(kind, title, f)
    if kids:
        n.children = kids
    return n


def test_toc_model():
    section("3. 目录模型")

    toc = [mk("doc", "A", "a"), mk("doc", "B", "b"), mk("doc", "C", "c")]

    # 3.1 wrap_group 必须按文档顺序收纳子项（走真实的 Session.wrap_group）
    def wrap_real(paths):
        s = E.Session(os.path.join(FIX, "book3.epub"))
        s.wrap_group(paths, "G")
        return [n.title for n in s.toc[0].children]

    doc_order = ["第一卷 试炼", "第三章 远行", "第四章 归途"]
    eq(wrap_real(["1", "2", "3"]), doc_order, "顺序传入：子项正序")
    eq(wrap_real(["3", "2", "1"]), doc_order, "逆序传入：子项仍应按文档顺序")
    eq(wrap_real(["2", "3", "1"]), doc_order, "乱序传入：子项仍应按文档顺序")
    toc = [mk("doc", "A", "a"), mk("doc", "B", "b"), mk("doc", "C", "c")]

    # 3.2 不能移动到自身内部
    t = E.clone_toc([mk("group", "G", None, [mk("doc", "A", "a")]), mk("doc", "B", "b")])
    raises(lambda: E.move_node(t, "1", "1.1"), ValueError, "禁止移入自身后代")

    # 3.3 越界路径
    raises(lambda: E.locate(toc, "9"), ValueError, "越界序号报错")
    raises(lambda: E.locate(toc, "1.1"), ValueError, "叶子节点下越界报错")

    # 3.4 move 到不同位置
    t = E.clone_toc(toc)
    E.move_node(t, "3", "1")
    eq([n.title for n in t], ["C", "A", "B"], "移动 3→1")
    t = E.clone_toc(toc)
    E.move_node(t, "1", "end")
    eq([n.title for n in t], ["B", "C", "A"], "移动 1→end")

    # 3.4b 向下移动：目标位应落到「原第 N 项之前」
    def four():
        return [E.Node("doc", "A", "a"), E.Node("doc", "B", "b"),
                E.Node("doc", "C", "c"), E.Node("doc", "D", "d")]

    t = four()
    E.move_node(t, "1", "3")
    eq([n.title for n in t], ["B", "A", "C", "D"], "向下移动 1→3 落在 C 之前")
    t = four()
    E.move_node(t, "1", "4")
    eq([n.title for n in t], ["B", "C", "A", "D"], "向下移动 1→4 落在 D 之前")
    t = four()
    E.move_node(t, "2", "4")
    eq([n.title for n in t], ["A", "C", "B", "D"], "向下移动 2→4 落在 D 之前")
    t = four()
    E.move_node(t, "1", "2")
    eq([n.title for n in t], ["A", "B", "C", "D"], "移动到紧邻的下一格应为空操作")

    # 3.5 parse_target 非法输入应给出可读错误
    try:
        E.parse_target(toc, "x")
        check(False, "parse_target('x') 应报错")
    except ValueError as exc:
        check("序号" in str(exc) or "非法" in str(exc) or "数字" in str(exc),
              "parse_target 非法输入给出可读错误（实际：%s）" % exc)

    # 3.6 count_nodes / flatten_files / collect_files
    tree = [mk("group", "G", None, [mk("doc", "A", "a"), mk("doc", "B", "a")]),
            mk("doc", "C", "c")]
    eq(E.count_nodes(tree), {"groups": 1, "docs": 3, "total": 4}, "节点计数")
    eq(E.flatten_files(tree), ["a", "c"], "展平去重（同一文件只出现一次）")
    eq(E.collect_files(tree), {"a", "c"}, "收集文件集合")

    # 3.7 first_file_of
    eq(E.first_file_of(tree[0]), ("a", ""), "大标题取首个后代文件")
    eq(E.first_file_of(mk("group", "空")), None, "空大标题返回 None")

    # 3.8 clone_toc 深拷贝且保留 uid
    c = E.clone_toc(tree)
    eq(c[0].uid, tree[0].uid, "clone 保留 uid")
    check(c[0] is not tree[0] and c[0].children[0] is not tree[0].children[0],
          "clone 是深拷贝")


# ---------------------------------------------------------------------------
# 4. 撤销 / 重做
# ---------------------------------------------------------------------------

def test_undo_redo():
    section("4. 撤销 / 重做")

    path = os.path.join(FIX, "book3.epub")
    s = E.Session(path)
    before = [n.title for n in s.toc]

    s.rename("1", "改名A")
    eq([n.title for n in s.toc][0], "改名A", "改名生效")
    s.undo()
    eq([n.title for n in s.toc], before, "撤销恢复原名")
    s.redo()
    eq([n.title for n in s.toc][0], "改名A", "重做恢复改名")

    # 4.1 失败的操作不应占用一步撤销
    s2 = E.Session(path)
    depth0 = s2.undo_depth
    raises(lambda: s2.rename("99", "x"), ValueError, "越界改名报错")
    eq(s2.undo_depth, depth0, "失败的 rename 不应产生撤销记录")

    s3 = E.Session(path)
    d0 = s3.undo_depth
    raises(lambda: s3.move("99", "end"), ValueError, "越界移动报错")
    eq(s3.undo_depth, d0, "失败的 move 不应产生撤销记录")

    # 4.2 大标题 auto 模式删除失败也不应占撤销
    s4 = E.Session(path)
    gidx = next((i for i, n in enumerate(s4.toc) if n.kind == "group"), None)
    if gidx is None:
        s4.add_group("临时卷", "1")
        gidx = 0
        s4.toc[gidx].children.append(E.Node("doc", "临时章", "OEBPS/Text/ch01.xhtml"))
    d0 = s4.undo_depth
    try:
        s4.delete(str(gidx + 1), mode="auto")
        check(False, "含子项的大标题 auto 删除应报错")
    except ValueError:
        check(True, "含子项的大标题 auto 删除报错")
    eq(s4.undo_depth, d0, "失败的 delete 不应产生撤销记录")

    # 4.3 撤销栈上限
    s5 = E.Session(path)
    for i in range(120):
        s5.rename("1", "t%d" % i)
    check(s5.undo_depth <= 100, "撤销栈上限 100（实际 %d）" % s5.undo_depth)

    # 4.4 合并失败后不应污染重做栈
    s6 = E.Session(path)
    bad = _mk_minimal_epub("无目录书")
    try:
        s6.merge_epubs([bad])
    except ValueError:
        pass
    check(not s6.can_redo, "合并失败后重做栈应为空")


def _mk_minimal_epub(title, with_toc=False):
    """造一本最小 EPUB：with_toc=False 时目录为空"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zi = zipfile.ZipInfo("mimetype")
        zi.compress_type = zipfile.ZIP_STORED
        zf.writestr(zi, b"application/epub+zip")
        zf.writestr("META-INF/container.xml",
                    '<?xml version="1.0"?><container version="1.0" '
                    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                    '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                    'media-type="application/oebps-package+xml"/></rootfiles></container>')
        opf = ('<?xml version="1.0" encoding="utf-8"?>\n'
               '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
               'unique-identifier="bookid">\n'
               '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
               '    <dc:identifier id="bookid">urn:uuid:test-%s</dc:identifier>\n'
               '    <dc:title>%s</dc:title>\n'
               '    <dc:language>zh</dc:language>\n'
               '    <meta property="dcterms:modified">2020-01-01T00:00:00Z</meta>\n'
               '  </metadata>\n'
               '  <manifest>\n'
               '    <item id="c1" href="Text/c1.xhtml" media-type="application/xhtml+xml"/>\n'
               '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
               'properties="nav"/>\n'
               '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>\n'
               '  </manifest>\n'
               '  <spine toc="ncx"><itemref idref="c1"/></spine>\n'
               '</package>' % (abs(hash(title)), title))
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/Text/c1.xhtml",
                    '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html>'
                    '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>c1</title>'
                    '</head><body><h1>%s</h1><p>正文</p></body></html>' % title)
        if with_toc:
            nav = ('<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html>'
                   '<html xmlns="http://www.w3.org/1999/xhtml" '
                   'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>nav</title></head>'
                   '<body><nav epub:type="toc" id="toc"><ol>'
                   '<li><a href="Text/c1.xhtml">第一话</a></li>'
                   '</ol></nav></body></html>')
        else:
            nav = ('<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html>'
                   '<html xmlns="http://www.w3.org/1999/xhtml" '
                   'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>nav</title></head>'
                   '<body><nav epub:type="toc" id="toc"><ol></ol></nav></body></html>')
        zf.writestr("OEBPS/nav.xhtml", nav)
        zf.writestr("OEBPS/toc.ncx",
                    '<?xml version="1.0" encoding="utf-8"?>'
                    '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
                    '<head><meta name="dtb:uid" content="urn:uuid:test"/></head>'
                    '<docTitle><text>%s</text></docTitle><navMap></navMap></ncx>' % title)
    p = os.path.join(tempfile.mkdtemp(prefix="cf-audit-"), "x.epub")
    with open(p, "wb") as f:
        f.write(buf.getvalue())
    return p


# ---------------------------------------------------------------------------
# 5. 资源释放
# ---------------------------------------------------------------------------

def test_resources():
    section("5. 资源释放")

    # 5.1 read_docx 后能否删除源文件（Windows 上句柄未释放会 PermissionError）
    docx = os.path.join(FIX, "sample.docx")
    if not os.path.exists(docx):
        print("  (跳过：无 sample.docx)")
        return
    tmpdir = tempfile.mkdtemp(prefix="cf-audit-docx-")
    tmp = os.path.join(tmpdir, "copy.docx")
    with open(docx, "rb") as f:
        data = f.read()
    with open(tmp, "wb") as f:
        f.write(data)

    code = (
        "import sys\n"
        "import docxconv\n"
        "d = docxconv.read_docx(%r)\n"
        "import os\n"
        "os.remove(%r)\n"
        "print('DELETED-OK')\n" % (tmp, tmp))
    hung, rc, out = run_isolated(code, timeout=20)
    check(not hung and "DELETED-OK" in out,
          "read_docx 后源文件可删除（句柄已释放）；实际 rc=%s out=%s" % (rc, out.strip()[:120]))

    # 5.2 EpubBook：连续打开大量文件不应累积句柄
    code = (
        "import sys\n"
        "import chapflow as E, os, gc\n"
        "p = %r\n"
        "for i in range(50):\n"
        "    b = E.EpubBook(p)\n"
        "print('OPEN-OK')\n" % os.path.join(FIX, "book3.epub"))
    hung, rc, out = run_isolated(code, timeout=30)
    check(not hung and "OPEN-OK" in out, "反复打开 EPUB 不报错")

    # 5.3 导出后 .tmp 不应残留
    s = E.Session(os.path.join(FIX, "book3.epub"))
    tmpd = tempfile.mkdtemp(prefix="cf-audit-exp-")
    out = os.path.join(tmpd, "o.epub")
    s.export(out)
    leftovers = [n for n in os.listdir(tmpd) if n.endswith(".tmp")]
    eq(leftovers, [], "导出后无 .tmp 残留")

    # 5.4 导出到不存在的目录应给出可读错误
    s2 = E.Session(os.path.join(FIX, "book3.epub"))
    raises(lambda: s2.export(os.path.join(tmpd, "no", "such", "o.epub")),
           OSError, "导出到不存在的目录抛 OSError")

    # 5.5 read_docx 抛异常时（zip 已打开）也不应锁住源文件
    tmpdir2 = tempfile.mkdtemp(prefix="cf-audit-docx2-")
    broken = os.path.join(tmpdir2, "broken.docx")
    with zipfile.ZipFile(broken, "w") as zf:
        zf.writestr("hello.txt", "不是 docx")
    code = (
        "import sys\n"
        "import docxconv, os\n"
        "try:\n"
        "    docxconv.read_docx(%r)\n"
        "except Exception as exc:\n"
        "    print('RAISED:', type(exc).__name__)\n"
        "os.remove(%r)\n"
        "print('DELETED-OK')\n" % (broken, broken))
    hung, rc, out = run_isolated(code, timeout=20)
    check(not hung and "DELETED-OK" in out,
          "read_docx 异常路径也释放句柄；实际 rc=%s out=%s" % (rc, out.strip()[:160]))


# ---------------------------------------------------------------------------
# 6. 元数据
# ---------------------------------------------------------------------------

def test_metadata():
    section("6. 元数据")

    path = os.path.join(FIX, "book3.epub")
    s = E.Session(path)
    md = s.metadata()
    check("title" in md and "creators" in md, "metadata() 返回基本字段")

    tmpd = tempfile.mkdtemp(prefix="cf-audit-md-")

    # 6.1 改书名后导出应生效
    s.update_metadata({"title": "新书名X", "creators": "甲、乙", "language": "zh"})
    out = os.path.join(tmpd, "m1.epub")
    rep = s.export(out)
    errs = [i["text"] for i in rep["issues"] if i["level"] == "error"]
    eq(errs, [], "改元数据后自检无 error")
    b = E.EpubBook(out)
    eq(b.metadata["title"], "新书名X", "新书名写入")
    eq(b.metadata["creators"], ["甲", "乙"], "多作者按顿号拆分")

    # 6.2 refines 必须指向真实存在的 id
    s2 = E.Session(path)
    s2.update_metadata({"title": "T", "sort_title": "排序T"})
    opf = E.serialize_xml(s2.book.opf_doc)
    import re as _re
    refines = _re.findall(r'refines="#([^"]+)"', opf)
    ids = set(_re.findall(r'<(?:dc:)?title[^>]*\bid="([^"]+)"', opf))
    for rf in refines:
        check(rf in ids, "refines 指向的 id 存在（%s / 现有 %s）" % (rf, sorted(ids)))

    # 6.3 清空作者
    s3 = E.Session(path)
    s3.update_metadata({"creators": ""})
    md3 = E.first_child(s3.book.pkg, "metadata")
    creators = [c for c in E.child_els(md3) if E.local_name(c.name) == "creator"]
    vals = [E.text_of(c) for c in creators]
    check(all(v == "" for v in vals), "清空作者后 creator 为空（实际 %r）" % vals)

    # 6.4 封面替换
    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
    s4 = E.Session(path)
    changed = s4.update_metadata({"title": "带封面"}, cover_bytes=png)
    check("封面" in changed, "封面替换被记录")
    out4 = os.path.join(tmpd, "m4.epub")
    rep4 = s4.export(out4)
    eq([i["text"] for i in rep4["issues"] if i["level"] == "error"], [],
       "换封面后自检无 error")

    # 6.5 非法封面数据
    s5 = E.Session(path)
    raises(lambda: s5.update_metadata({}, cover_bytes=b"not an image"),
           ValueError, "非法封面数据报错")


# ---------------------------------------------------------------------------
# 7. 导出边界
# ---------------------------------------------------------------------------

def test_export_edges():
    section("7. 导出边界")

    tmpd = tempfile.mkdtemp(prefix="cf-audit-exp-")
    path = os.path.join(FIX, "book3.epub")

    # 7.1 删除全部章节
    s = E.Session(path)
    while s.toc:
        s.delete("1", mode="cascade")
    eq(len(s.toc), 0, "全部删除后目录为空")
    out = os.path.join(tmpd, "empty.epub")
    rep = s.export(out)
    check(isinstance(rep["issues"], list), "空目录仍可导出（不崩溃）")
    errs = [i["text"] for i in rep["issues"] if i["level"] == "error"]
    check(True, "空目录导出 issues：%s" % (errs or "无 error"))

    # 7.2 标题含 XML 特殊字符
    s2 = E.Session(path)
    s2.rename("1", "<a> & \"b\" 'c'")
    out2 = os.path.join(tmpd, "esc.epub")
    rep2 = s2.export(out2)
    eq([i["text"] for i in rep2["issues"] if i["level"] == "error"], [],
       "特殊字符标题导出无 error")
    with zipfile.ZipFile(out2) as zf:
        nav = zf.read("OEBPS/nav.xhtml").decode("utf-8")
    check("&lt;a&gt;" in nav and "&amp;" in nav, "标题中的 < & 被正确转义")
    # 再解析一遍，确认是合法 XML
    hung, rc, o = run_isolated(
        "import xmldom, zipfile\n"
        "z = zipfile.ZipFile(%r)\n"
        "xmldom.parse_xml(z.read('OEBPS/nav.xhtml').decode('utf-8'))\n"
        "print('PARSE-OK')\n" % out2, timeout=10)
    check(not hung and "PARSE-OK" in o, "导出的 nav 可被重新解析")

    # 7.3 空大标题被丢弃且给出告警
    s3 = E.Session(path)
    s3.add_group("空卷", "end")
    out3 = os.path.join(tmpd, "emptygrp.epub")
    rep3 = s3.export(out3)
    check(any("空大标题" in w for w in rep3["warnings"]), "空大标题导出时告警")
    eq([i["text"] for i in rep3["issues"] if i["level"] == "error"], [],
       "空大标题导出无 error")

    # 7.4 dry_run 不写文件
    s4 = E.Session(path)
    out4 = os.path.join(tmpd, "dry.epub")
    s4.export(out4, dry_run=True)
    check(not os.path.exists(out4), "dry_run 不落盘")

    # 7.5 重复导出结果稳定（不含时间戳字段时）
    s5 = E.Session(path)
    a = os.path.join(tmpd, "a.epub")
    b2 = os.path.join(tmpd, "b.epub")
    s5.export(a, touch_modified=False)
    s5.export(b2, touch_modified=False)
    with zipfile.ZipFile(a) as z1, zipfile.ZipFile(b2) as z2:
        n1 = {i.filename: z1.read(i.filename) for i in z1.infolist() if i.filename != "mimetype"}
        n2 = {i.filename: z2.read(i.filename) for i in z2.infolist() if i.filename != "mimetype"}
    eq(n1, n2, "同一会话重复导出内容一致")

    # 7.6 export 不应改动会话里的目录树（空大标题丢弃不能污染）
    s6 = E.Session(path)
    s6.add_group("空卷2", "end")
    n_before = len(s6.toc)
    s6.export(os.path.join(tmpd, "e6.epub"))
    eq(len(s6.toc), n_before, "export 不修改会话目录树（空大标题仍留在会话里）")

    # 7.7 空目录导出：EPUB 规范要求 spine / navMap / nav 至少各有一项。
    # 章节全删光后没有可引用的正文，无法凭空补齐，但必须如实报出来，
    # 不能像以前那样把「spine 共 0 项」「nav 0 个链接」当成 ok 放行。
    s7 = E.Session(path)
    while s7.toc:
        s7.delete("1", mode="cascade")
    rep7 = s7.export(os.path.join(tmpd, "empty2.epub"))
    errs7 = [i["text"] for i in rep7["issues"] if i["level"] == "error"]
    oks7 = [i["text"] for i in rep7["issues"] if i["level"] == "ok"]
    check(any("spine" in e and "至少" in e for e in errs7),
          "空 spine 必须报 error（实际 %r）" % errs7)
    check(any("nav.xhtml" in e for e in errs7),
          "空 nav 目录必须报 error（实际 %r）" % errs7)
    check(any("toc.ncx" in e for e in errs7),
          "空 navMap 必须报 error（实际 %r）" % errs7)
    check(not any("spine 共 0" in o for o in oks7)
          and not any("0 个链接全部可达" in o for o in oks7),
          "不得把空结构标成 ok（实际 %r）" % oks7)


# ---------------------------------------------------------------------------
# 8. TXT 转换
# ---------------------------------------------------------------------------

def test_txt():
    section("8. TXT 转换")

    tmpd = tempfile.mkdtemp(prefix="cf-audit-txt-")

    def write(name, text, enc="utf-8"):
        p = os.path.join(tmpd, name)
        with open(p, "w", encoding=enc, newline="") as f:
            f.write(text)
        return p

    # 8.1 空文件 / 只有空白
    p = write("empty.txt", "")
    raises(lambda: txtconv.read_text_file(p), txtconv.TxtError, "空文件报错")
    p = write("ws.txt", "   \n\n  ")
    raises(lambda: txtconv.read_text_file(p), txtconv.TxtError, "纯空白文件报错")
    raises(lambda: txtconv.read_text_file(os.path.join(tmpd, "nope.txt")),
           txtconv.TxtError, "不存在的文件报错")
    raises(lambda: txtconv.read_text_file(tmpd), txtconv.TxtError, "传文件夹报错")

    # 8.2 二进制文件
    p = os.path.join(tmpd, "bin.txt")
    with open(p, "wb") as f:
        f.write(b"\x00\x01\x02" * 100)
    raises(lambda: txtconv.read_text_file(p), txtconv.TxtError, "含 NUL 的二进制报错")

    # 8.3 无标题 -> auto 按字数切
    body = "这是一段测试正文。" * 200
    chs, st = txtconv.split_chapters(body, "书", mode="auto", size=4000)
    check(len(chs) >= 1 and all(c.paragraphs for c in chs), "无标题 auto 按字数切分")
    eq(st["rule"], "size", "无标题时 rule=size")

    # 8.4 有标题 -> 按标题切
    t = "第一章 甲\n正文甲\n第二章 乙\n正文乙\n第三章 丙\n正文丙\n"
    chs, st = txtconv.split_chapters(t, "书", mode="auto")
    eq([c.title for c in chs], ["第一章 甲", "第二章 乙", "第三章 丙"], "按标题切分")

    # 8.5 mode=heading 但没标题 -> 报错
    raises(lambda: txtconv.split_chapters("只有正文\n没有标题", "书", mode="heading"),
           txtconv.TxtError, "heading 模式无标题报错")

    # 8.6 mode=none -> 整篇一章
    chs, _ = txtconv.split_chapters("甲\n乙\n丙", "书", mode="none")
    eq(len(chs), 1, "none 模式一章")

    # 8.7 size 下限
    chs, _ = txtconv.split_chapters("短" * 100, "书", mode="size", size=1)
    check(len(chs) == 1, "极小 size 被夹到下限（不产生海量空章）")

    # 8.8 标题行尾带句号不算标题
    chs, _ = txtconv.split_chapters("第一章 甲。\n正文\n第二章 乙\n正文\n", "书", mode="heading")
    eq(len(chs), 1, "行尾带句号的行不识别为标题")

    # 8.9 简易规则：unit 为空不应被当成大标题
    r = txtconv.SplitRules(mode="simple", prefix="第", unit="", builtin=False)
    m = txtconv.build_matcher(r)
    hit = m("第一章 测试", "第一章 测试")
    eq(hit[0] if hit else None, 2, "unit 为空时按章（level 2）处理，不应退回 level 1")

    # 8.10 段落模式
    t2 = "甲1\n甲2\n\n乙1\n乙2\n"
    c1, _ = txtconv.split_chapters(t2, "书", mode="none", para_mode="line")
    eq(len(c1[0].paragraphs), 4, "line 模式每行一段")
    c2, _ = txtconv.split_chapters(t2, "书", mode="none", para_mode="blank")
    eq(len(c2[0].paragraphs), 2, "blank 模式空行分段")

    # 8.11 页码行丢弃
    c3, _ = txtconv.split_chapters("正文\n123\n- 45 -\n正文2\n", "书", mode="none",
                                   drop_page_numbers=True)
    eq(c3[0].paragraphs, ["正文", "正文2"], "纯页码行被丢弃")

    # 8.12 非法正则
    raises(lambda: txtconv.build_matcher(
        txtconv.SplitRules(mode="simple", extra="[unclosed")), txtconv.TxtError,
        "附加规则非法正则报错")
    raises(lambda: txtconv.build_matcher(
        txtconv.SplitRules(mode="regex", pattern="*bad(")), txtconv.TxtError,
        "regex 模式非法正则报错")

    # 8.13 XHTML 转义
    ch = txtconv.Chapter("第<a>章", 2, ["a & b < c"])
    x = txtconv.build_xhtml(ch)
    check("&lt;a&gt;" in x and "&amp;" in x, "XHTML 特殊字符转义")
    hung, rc, o = run_isolated(
        "import xmldom\nxmldom.parse_xml(%r)\nprint('OK')" % x, timeout=8)
    check(not hung and "OK" in o, "生成的 XHTML 可被解析")

    # 8.14 整书转换
    p = write("book.txt", "第一章 甲\n" + "正文" * 50 + "\n第二章 乙\n" + "正文" * 50)
    out = os.path.join(tmpd, "book.epub")
    rep = E.txt_to_epub(p, out, title="转换书", author="某人")
    eq([i["text"] for i in rep["issues"] if i["level"] == "error"], [],
       "TXT 转 EPUB 自检无 error")
    b = E.EpubBook(out)
    eq(len(b.toc), 2, "转换后两章目录")


# ---------------------------------------------------------------------------
# 9. 章节编号
# ---------------------------------------------------------------------------

def test_numbering():
    section("9. 章节编号")

    # 9.1 中文数字往返
    bad = [n for n in range(1, 3000) if E.cn_to_int(E.int_to_cn(n)) != n]
    eq(bad, [], "1..2999 中阿数字往返一致")

    # 9.2 1001 应为「一千零一」而非「一千一」
    eq(E.int_to_cn(1001), "一千零一", "int_to_cn(1001) 补「零」")

    # 9.3 缺号检测
    toc = [mk("doc", "第一章 A", "a"), mk("doc", "第二章 B", "b"),
           mk("doc", "第五章 C", "c")]
    p = E.scan_numbering(toc)
    check(p["has_gap"], "检测到缺号")
    gaps = [g for grp in p["groups"] for g in grp["gaps"]]
    eq(len(gaps), 1, "一处缺号")
    eq(gaps[0]["missing"], [3, 4], "缺 3、4")

    # 9.4 乱序检测
    toc = [mk("doc", "第一章 A", "a"), mk("doc", "第三章 B", "b"),
           mk("doc", "第二章 C", "c")]
    p = E.scan_numbering(toc)
    check(p["has_disorder"], "检测到乱序")

    # 9.5 重排
    toc = [mk("doc", "第五章 A", "a"), mk("doc", "第七章 B", "b")]
    changes = E._plan_for_entries(E._entries_for(E._walk_docs(toc)),
                                  {"kind": "cn", "unit": "章", "prefix": "第"},
                                  False, "x")["changes"]
    eq([c["new"] for c in changes], ["第一章 A", "第二章 B"], "重排为连续编号")

    # 9.6 特殊章节不参与编号
    toc = [mk("doc", "序章 开端", "a"), mk("doc", "第一章 A", "b"),
           mk("doc", "第二章 B", "c")]
    p = E.scan_numbering(toc, add_missing=True)
    eq(p["special_docs"], 1, "序章被识别为特殊章节")
    items = [i for grp in p["groups"] for i in grp["items"]]
    new_titles = [i["new_title"] for i in items]
    check("序章 开端" not in new_titles and new_titles == ["第一章 A", "第二章 B"],
          "补号时特殊章节被跳过且不占号（实际 %r）" % new_titles)

    # 9.7 分隔符规范
    eq(E._format_title({"kind": "cn", "unit": "章", "prefix": "第"}, 2, ".俾斯麦"),
       "第二章 俾斯麦", "点号分隔规范为空格")
    eq(E._format_title({"kind": "chapter", "prefix": "Chapter"}, 3, " X"),
       "Chapter 3 X", "Chapter 风格")

    # 9.8 per_group
    toc = [mk("group", "卷一", None, [mk("doc", "第一章 A", "a"),
                                      mk("doc", "第二章 B", "b")]),
           mk("group", "卷二", None, [mk("doc", "第一章 C", "c"),
                                      mk("doc", "第二章 D", "d")])]
    p = E.scan_numbering(toc, per_group=True)
    eq(p["changes"], [], "per_group 各卷独立编号时无需改动")

    # 9.9 编号风格继承
    toc = [mk("doc", "Chapter 1", "a"), mk("doc", "Chapter 5", "b")]
    p = E.scan_numbering(toc)
    eq(p["style"]["kind"], "chapter", "继承 Chapter 风格")

    # 9.10 空范围
    eq(E.scan_numbering([])["total_docs"], 0, "空目录扫描不报错")

    # 9.11 tidy_title
    eq(txtconv.tidy_title("第二章.俾斯麦"), "第二章 俾斯麦", "tidy_title 规范点号")
    eq(txtconv.tidy_title("无编号标题"), "无编号标题", "无编号标题原样")
    eq(txtconv.tidy_title(""), "", "空标题原样")


# ---------------------------------------------------------------------------
# 10. 合并
# ---------------------------------------------------------------------------

def test_merge():
    section("10. 合并")

    tmpd = tempfile.mkdtemp(prefix="cf-audit-merge-")
    path = os.path.join(FIX, "book3.epub")

    # 10.1 合并自身应报错
    s = E.Session(path)
    raises(lambda: s.merge_epubs([path]), ValueError, "不能合并自身")

    # 10.2 空列表
    raises(lambda: s.merge_epubs([]), ValueError, "空列表报错")

    # 10.3 无目录的书：spine 兜底生成目录后仍应能合并出合规结果
    bad = _mk_minimal_epub("无目录", with_toc=False)
    s2 = E.Session(path)
    try:
        s2.merge_epubs([bad])
        merged_ok = True
    except ValueError:
        merged_ok = False
    if merged_ok:
        out_b = os.path.join(tmpd, "merged_notoc.epub")
        r = s2.export(out_b)
        eq([i["text"] for i in r["issues"] if i["level"] == "error"], [],
           "无目录的书（spine 兜底）合并后自检无 error")
    else:
        check(True, "无目录的书合并被拒绝，且会话保持干净")
    check(not s2.can_redo, "合并失败后重做栈干净")

    # 10.4 正常合并
    other = os.path.join(FIX, "book2.epub")
    s3 = E.Session(path)
    rep = s3.merge_epubs([other])
    check(rep["chapters"] > 0, "合并成功，章节数 %d" % rep["chapters"])
    out = os.path.join(tmpd, "merged.epub")
    r = s3.export(out)
    eq([i["text"] for i in r["issues"] if i["level"] == "error"], [], "合并后自检无 error")

    # 10.5 合并后资源路径不冲突
    with zipfile.ZipFile(out) as zf:
        names = [i.filename for i in zf.infolist()]
    eq(len(names), len(set(names)), "合并后无重名条目")

    # 10.6 合并 DRM 书应报错（构造含 encryption.xml 的 EPUB）
    enc = os.path.join(tmpd, "enc.epub")
    with zipfile.ZipFile(enc, "w", zipfile.ZIP_DEFLATED) as zf:
        zi = zipfile.ZipInfo("mimetype")
        zi.compress_type = zipfile.ZIP_STORED
        zf.writestr(zi, b"application/epub+zip")
        zf.writestr("META-INF/container.xml",
                    '<?xml version="1.0"?><container version="1.0" '
                    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                    '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                    'media-type="application/oebps-package+xml"/></rootfiles></container>')
        zf.writestr("META-INF/encryption.xml", '<encryption/>')
        zf.writestr("OEBPS/content.opf",
                    '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
                    'version="3.0" unique-identifier="id"><metadata '
                    'xmlns:dc="http://purl.org/dc/elements/1.1/">'
                    '<dc:identifier id="id">u</dc:identifier><dc:title>DRM</dc:title>'
                    '</metadata><manifest/><spine/></package>')
    s4 = E.Session(path)
    raises(lambda: s4.merge_epubs([enc]), ValueError, "合并含 DRM 的书报错")


# ---------------------------------------------------------------------------
# 11. 升级与修复
# ---------------------------------------------------------------------------

def test_upgrade_repair():
    section("11. 升级与修复")

    tmpd = tempfile.mkdtemp(prefix="cf-audit-up-")
    e2 = os.path.join(FIX, "book2.epub")
    s = E.Session(e2)
    check(s.can_upgrade, "book2 可升级（EPUB2 → EPUB3）")
    out = os.path.join(tmpd, "up.epub")
    rep = s.export(out, target_version="3.0")
    eq([i["text"] for i in rep["issues"] if i["level"] == "error"], [], "升级后自检无 error")
    b = E.EpubBook(out)
    eq(b.version, "3.0", "版本号已改为 3.0")
    check(b.nav_path is not None, "升级后存在 nav 文档")

    # 11.2 已经是 3.0 的书不需要升级
    s3 = E.Session(os.path.join(FIX, "book3.epub"))
    check(not s3.can_upgrade, "EPUB3 不可再升级")

    # 11.3 repair：悬空条目
    dirty = os.path.join(FIX, "dirty.epub")
    if os.path.exists(dirty):
        s2 = E.Session(dirty)
        plan = s2.repair_plan()
        check(isinstance(plan, list), "repair_plan 返回列表：%d 项" % len(plan))
        logs = s2.repair()
        check(isinstance(logs, list), "repair 返回日志")
        # 修复后应能干净导出
        out2 = os.path.join(tmpd, "repaired.epub")
        r2 = s2.export(out2)
        errs = [i["text"] for i in r2["issues"] if i["level"] == "error"]
        check(True, "修复后导出：%s" % (errs or "无 error"))
        # 再检一次，应无 dead/empty 残留
        plan2 = s2.repair_plan()
        keys = {p["key"] for p in plan2}
        check("dead" not in keys and "empty" not in keys,
              "修复后不再有悬空/空大标题（剩余 %s）" % sorted(keys))
    else:
        print("  (跳过：无 dirty.epub)")


# ---------------------------------------------------------------------------
# 12. CLI
# ---------------------------------------------------------------------------

def test_cli():
    section("12. CLI")

    def run(args, timeout=30):
        p = subprocess.run([sys.executable, "-u", os.path.join(SRC, "chapflow.py")] + args,
                           capture_output=True, timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")

    path = os.path.join(FIX, "book3.epub")
    tmpd = tempfile.mkdtemp(prefix="cf-audit-cli-")

    rc, out, err = run(["info", path])
    eq(rc, 0, "cli info 正常退出")
    check("测试之书" in out or "title" in out.lower(), "cli info 输出书名")

    rc, out, err = run(["tree", path])
    eq(rc, 0, "cli tree 正常退出")

    # 12.1 不存在的文件 -> 退出码 1 且有中文错误
    rc, out, err = run(["info", os.path.join(tmpd, "nope.epub")])
    eq(rc, 1, "不存在的文件退出码 1")
    check("错误" in err, "错误信息可读：%s" % err.strip()[:60])

    # 12.2 非 EPUB 文件
    bad = os.path.join(tmpd, "bad.epub")
    with open(bad, "wb") as f:
        f.write(b"this is not a zip" * 10)
    rc, out, err = run(["info", bad])
    eq(rc, 1, "损坏文件退出码 1")
    check("错误" in err, "损坏文件错误信息可读：%s" % err.strip()[:60])

    # 12.3 edit 操作
    outp = os.path.join(tmpd, "edit.epub")
    rc, out, err = run(["edit", path, "-o", outp, "--op", "rename 1 新标题"])
    eq(rc, 0, "cli edit rename 成功")
    check(os.path.exists(outp), "edit 产出文件存在")

    # 12.4 validate
    rc, out, err = run(["validate", outp])
    eq(rc, 0, "cli validate 正常退出")

    # 12.5 非法 op
    rc, out, err = run(["edit", path, "-o", os.path.join(tmpd, "x.epub"),
                        "--op", "nosuchop 1"])
    check(rc != 0, "未知操作非零退出")
    check("错误" in err or "无法识别" in err, "未知操作有可读错误：%s" % err.strip()[:60])

    # 12.6 越界路径
    rc, out, err = run(["edit", path, "-o", os.path.join(tmpd, "y.epub"),
                        "--op", "rename 99 x"])
    check(rc != 0, "越界路径非零退出")


# ---------------------------------------------------------------------------

def test_icons():
    section("14. 应用图标：透明底与多尺寸")

    assets = os.path.abspath(os.path.join(HERE, "..", "assets"))
    pngs = [f for f in sorted(os.listdir(assets))
            if re.match(r"chapflow-\d+\.png$", f)]
    check(bool(pngs), "assets/ 里有 chapflow-<size>.png（%d 个）" % len(pngs))

    for name in pngs:
        with open(os.path.join(assets, name), "rb") as f:
            head = f.read(33)
        check(head[:8] == b"\x89PNG\r\n\x1a\n", "%s 是合法 PNG" % name)
        # IHDR: 8 字节签名 + 4 长度 + 4 类型 ；颜色类型在偏移 25
        color_type = head[25]
        check(color_type in (4, 6), "%s 带 alpha 通道（color_type=%d）"
              % (name, color_type))

    ico = os.path.join(assets, "chapflow.ico")
    with open(ico, "rb") as f:
        data = f.read()
    reserved, kind, count = int.from_bytes(data[0:2], "little"), \
        int.from_bytes(data[2:4], "little"), int.from_bytes(data[4:6], "little")
    check(reserved == 0 and kind == 1, "chapflow.ico 头部合法")
    check(count >= 5, "chapflow.ico 含多尺寸（%d 个）" % count)
    sizes = [data[6 + 16 * i] or 256 for i in range(count)]
    for need in (16, 32, 48, 256):
        check(need in sizes, "ICO 里包含 %dpx（%s）" % (need, sizes))


def test_failure_rollback():
    section("13. 失败操作的回滚")

    tmpd = tempfile.mkdtemp(prefix="cf-audit-roll-")
    path = os.path.join(FIX, "book3.epub")
    txt = os.path.join(tmpd, "a.txt")
    with open(txt, "w", encoding="utf-8") as f:
        f.write("第一章 甲\n" + "正文" * 30)

    # 13.1 append_txt 指定了非法位置：失败后不得残留孤儿文件
    s = E.Session(path)
    try:
        s.append_txt(txt, at="99.99")
        raised = False
    except ValueError:
        raised = True
    check(raised, "非法位置追加应报错")
    eq(len(s.new_files), 0, "追加失败后不得残留 new_files")
    eq(len(s.new_manifest), 0, "追加失败后不得残留 new_manifest")
    eq(len(s.spine_append), 0, "追加失败后不得残留 spine_append")
    eq(s.undo_depth, 0, "追加失败后不得残留撤销记录")
    check(not s.dirty, "追加失败后不应标记 dirty")

    # 13.2 若残留了，保存时会写出「目录里看不到、但进了阅读顺序」的隐形章节
    s2 = E.Session(path)
    n_toc = len(s2.toc)
    try:
        s2.append_txt(txt, at="99.99")
    except ValueError:
        pass
    if s2.new_files:
        out = os.path.join(tmpd, "orphan.epub")
        r = s2.export(out)
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
        orphans = [n for n in names if "txt-" in n]
        check(False, "导出里出现了目录中不存在的隐形章节：%s" % orphans)
    else:
        check(True, "无残留：导出不会出现隐形章节")

    # 13.3 append_docx 同理
    docx = os.path.join(FIX, "sample.docx")
    if os.path.exists(docx):
        s3 = E.Session(path)
        try:
            s3.append_docx(docx, at="99.99")
        except ValueError:
            pass
        eq(len(s3.new_files), 0, "DOCX 追加失败后不得残留 new_files")
        eq(len(s3.new_manifest), 0, "DOCX 追加失败后不得残留 new_manifest")

    # 13.4 合并失败不应留下痕迹
    s4 = E.Session(path)
    try:
        s4.merge_epubs([path])
    except ValueError:
        pass
    eq(len(s4.new_files), 0, "合并自身失败后不残留")
    check(not s4.dirty, "合并自身失败后不应标记 dirty")

    # 13.5 失败后仍可正常保存出合规文件
    out5 = os.path.join(tmpd, "after_fail.epub")
    r5 = s.export(out5)
    eq([i["text"] for i in r5["issues"] if i["level"] == "error"], [],
       "经历失败操作后仍能导出合规文件")


def main() -> int:
    test_xml_parser()
    test_paths()
    test_toc_model()
    test_undo_redo()
    test_resources()
    test_metadata()
    test_export_edges()
    test_txt()
    test_numbering()
    test_merge()
    test_upgrade_repair()
    test_cli()
    test_icons()
    test_failure_rollback()

    print()
    print("通过 %d 项，失败 %d 项" % (_passed, len(_failed)))
    if _failed:
        print("\n失败明细：")
        for f in _failed:
            print("  - %s" % f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
