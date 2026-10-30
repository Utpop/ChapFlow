#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核心功能回归测试（无第三方依赖）"""
import io
import os
import re
import shutil
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import chapflow as E          # noqa: E402
import docxconv as DX         # noqa: E402
import txtconv                # noqa: E402

FIX = os.path.join(HERE, "fixtures")

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


def read_zip(path):
    with zipfile.ZipFile(path) as zf:
        return {i.filename: zf.read(i.filename) for i in zf.infolist()}, zf.infolist()


def text_of(files, name):
    return files[name].decode("utf-8")


def assert_no_errors(issues, label):
    errs = [i["text"] for i in issues if i["level"] == "error"]
    check(not errs, "%s 自检无 error：%s" % (label, "; ".join(errs)))


def assert_xml_ok(files, label):
    bad = []
    for name, data in files.items():
        if not name.lower().endswith((".xhtml", ".html", ".opf", ".ncx", ".xml")):
            continue
        raw = data.decode("utf-8", "replace")
        raw = re.sub(r"<!DOCTYPE[^>]*>", "", raw, count=1)
        try:
            ET.fromstring(raw)
        except ET.ParseError as exc:
            bad.append("%s: %s" % (name, exc))
    check(not bad, "%s 全部 XML 结构良好：%s" % (label, "; ".join(bad[:3])))


def nav_titles(files):
    """按顺序取出 nav 目录中的标题（只取 epub:type=toc 的那个 nav）"""
    txt = text_of(files, "OEBPS/nav.xhtml")
    txt = re.sub(r"<!DOCTYPE[^>]*>", "", txt, count=1)
    root = ET.fromstring(txt)
    ns = "{http://www.w3.org/1999/xhtml}"
    epub_type = "{http://www.idpf.org/2007/ops}type"
    toc_nav = None
    for nav in root.iter(ns + "nav"):
        if "toc" in (nav.get(epub_type) or "").split():
            toc_nav = nav
            break
    if toc_nav is None:
        return []
    out = []
    for li in toc_nav.iter(ns + "li"):
        a = li.find(ns + "a")
        sp = li.find(ns + "span")
        el = a if a is not None else sp
        if el is not None:
            out.append("".join(el.itertext()).strip())
    return out


def spine_ids(text):
    return re.findall(r'<itemref[^>]*idref="([^"]+)"', text)


def ncx_titles(text):
    return re.findall(r"<text>([^<]*)</text>", text)


# ---------------------------------------------------------------------------

def test_load():
    print("[1] 载入与结构识别")
    b = E.EpubBook(os.path.join(FIX, "book3.epub"))
    eq(b.version, "3.0", "EPUB 版本")
    eq(b.nav_path, "OEBPS/nav.xhtml", "nav 路径")
    eq(b.ncx_path, "OEBPS/toc.ncx", "ncx 路径")
    c = E.count_nodes(b.toc)
    eq((c["groups"], c["docs"]), (1, 4), "目录结构：1 个大标题 + 4 个章节")
    eq(b.toc[0].kind, "group", "第一个节点是大标题")
    eq(b.toc[0].children[0].file, "OEBPS/Text/ch01.xhtml", "章节文件解析正确")
    eq(b.metadata["title"], "测试之书", "书名")

    b2 = E.EpubBook(os.path.join(FIX, "book2.epub"))
    eq(b2.version, "2.0", "EPUB2 版本")
    eq(b2.nav_path, None, "EPUB2 无 nav")
    eq(E.count_nodes(b2.toc)["docs"], 5, "EPUB2 目录来自 ncx（卷带 content，解析为可点击分组）")


def test_rename(tmp):
    print("[2] 改名并同步 nav / ncx")
    s = E.Session(os.path.join(FIX, "book3.epub"))
    s.rename("1.2", "第二章 迷雾（修订版）")
    out = os.path.join(tmp, "rename.epub")
    rep = s.export(out)
    assert_no_errors(rep["issues"], "改名")

    files, _ = read_zip(out)
    nav = text_of(files, "OEBPS/nav.xhtml")
    ncx = text_of(files, "OEBPS/toc.ncx")
    check("第二章 迷雾（修订版）" in nav, "nav 已写入新标题")
    check("第二章 迷雾（修订版）" in ncx, "ncx 已写入新标题")
    check(">第二章 迷雾<" not in nav, "nav 中旧标题已消失")
    opf = text_of(files, "OEBPS/content.opf")
    check(">测试之书<" in opf and ">测试作者<" in opf, "元数据未被改动")

    orig, _ = read_zip(os.path.join(FIX, "book3.epub"))
    for name in ("OEBPS/Text/ch01.xhtml", "OEBPS/Images/cover.png", "OEBPS/Styles/style.css"):
        check(files[name] == orig[name], "未涉及的文件保持原字节：%s" % name)
    assert_xml_ok(files, "改名后")


def test_move(tmp):
    print("[3] 移动章节（跨层级）并同步阅读顺序")
    s = E.Session(os.path.join(FIX, "book3.epub"))
    s.move("1.2", "3")  # 从大标题里提到根层级第 3 位
    out = os.path.join(tmp, "move.epub")
    rep = s.export(out)
    assert_no_errors(rep["issues"], "移动")
    files, _ = read_zip(out)
    titles = nav_titles(files)
    eq(titles, ["第一卷 试炼", "第一章 初见", "第三章 远行", "第二章 迷雾", "第四章 归途"],
       "nav 顺序正确")
    eq(spine_ids(text_of(files, "OEBPS/content.opf")),
       ["ch01", "ch03", "ch02", "ch04"], "spine 阅读顺序已同步")

    # 同层移动：1.1 移到本组第 2 位
    s2 = E.Session(os.path.join(FIX, "book3.epub"))
    s2.move("1.1", "1.2")
    out2 = os.path.join(tmp, "move2.epub")
    s2.export(out2)
    files2, _ = read_zip(out2)
    eq(nav_titles(files2)[:3], ["第一卷 试炼", "第二章 迷雾", "第一章 初见"], "同层移动顺序正确")

    # 指定序号移动到某组内部
    s3 = E.Session(os.path.join(FIX, "book3.epub"))
    s3.move("2", "1.1")
    out3 = os.path.join(tmp, "move3.epub")
    s3.export(out3)
    files3, _ = read_zip(out3)
    eq(nav_titles(files3), ["第一卷 试炼", "第三章 远行", "第一章 初见", "第二章 迷雾", "第四章 归途"],
       "移入大标题内部顺序正确")


def test_delete(tmp):
    print("[4] 删除章节（含物理删文件）")
    s = E.Session(os.path.join(FIX, "book3.epub"))
    s.delete("1.1")
    out = os.path.join(tmp, "delete.epub")
    rep = s.export(out)
    assert_no_errors(rep["issues"], "删除")
    files, infos = read_zip(out)
    check("OEBPS/Text/ch01.xhtml" not in files, "被删章节文件已从 ZIP 移除")
    opf = text_of(files, "OEBPS/content.opf")
    check('id="ch01"' not in opf, "manifest 中已移除")
    check("ch01" not in spine_ids(opf), "spine 中已移除")
    check("第一章 初见" not in text_of(files, "OEBPS/nav.xhtml"), "nav 中已移除")
    check("第一章 初见" not in text_of(files, "OEBPS/toc.ncx"), "ncx 中已移除")
    check(infos[0].filename == "mimetype" and infos[0].compress_type == 0, "mimetype 仍为第一个且未压缩")

    # 只移出目录、保留文件
    s2 = E.Session(os.path.join(FIX, "book3.epub"))
    s2.delete("3", physical=False)  # 第四章
    out2 = os.path.join(tmp, "delete-soft.epub")
    s2.export(out2)
    files2, _ = read_zip(out2)
    check("OEBPS/Text/ch04.xhtml" in files2, "软删除保留正文文件")


def test_group(tmp):
    print("[5] 大标题增删")
    s = E.Session(os.path.join(FIX, "book3.epub"))
    s.add_group("第二卷 归途", "end")
    s.move("3", "4.1")   # 把第四章移进末尾的新大标题
    out = os.path.join(tmp, "group.epub")
    rep = s.export(out)
    assert_no_errors(rep["issues"], "分组")
    files, _ = read_zip(out)
    eq(nav_titles(files), ["第一卷 试炼", "第一章 初见", "第二章 迷雾", "第三章 远行",
                           "第二卷 归途", "第四章 归途"], "新增大标题并收纳章节")
    check("<span>第二卷 归途</span>" in text_of(files, "OEBPS/nav.xhtml"), "大标题在 nav 中为无链接 span")
    ncx = text_of(files, "OEBPS/toc.ncx")
    check("第二卷 归途" in ncx, "大标题写入 ncx")

    # 删除大标题并提升子项
    s2 = E.Session(os.path.join(FIX, "book3.epub"))
    s2.delete("1", mode="promote", physical=False)
    out2 = os.path.join(tmp, "ungroup.epub")
    s2.export(out2)
    files2, _ = read_zip(out2)
    eq(nav_titles(files2), ["第一章 初见", "第二章 迷雾", "第三章 远行", "第四章 归途"],
       "删除大标题后子项提升")

    # 连续条目打包成组
    s3 = E.Session(os.path.join(FIX, "book3.epub"))
    s3.wrap_group(["2", "3"], "后半部分")
    out3 = os.path.join(tmp, "wrap.epub")
    s3.export(out3)
    files3, _ = read_zip(out3)
    check("后半部分" in nav_titles(files3), "打包分组成功")
    eq(nav_titles(files3)[-1], "第四章 归途", "分组后仍保留后续章节")


def test_append_txt(tmp):
    print("[6] TXT 追加为新章节")
    s = E.Session(os.path.join(FIX, "book3.epub"))
    info = s.append_txt(os.path.join(FIX, "sample_utf8.txt"))
    eq(info["encoding"], "utf-8-sig", "识别 UTF-8 BOM")
    eq((info["groups"], info["chapters"]), (2, 5), "识别 2 个大标题 + 5 个章节（含序章）")

    out = os.path.join(tmp, "append.epub")
    rep = s.export(out)
    assert_no_errors(rep["issues"], "追加 TXT")

    files, infos = read_zip(out)
    titles = nav_titles(files)
    eq(titles, ["第一卷 试炼", "第一章 初见", "第二章 迷雾", "第三章 远行", "第四章 归途",
                "序章", "第一卷\u3000山中来客", "第一章\u3000投宿", "第二章\u3000夜话",
                "第二卷\u3000雾散之后", "第三章\u3000远行", "第四章\u3000归途"], "目录追加顺序正确")

    opf = text_of(files, "OEBPS/content.opf")
    eq(spine_ids(opf)[-5:], ["txt-001", "txt-002", "txt-003", "txt-004", "txt-005"], "spine 末尾追加新章节")
    eq(len(re.findall(r'<item id="txt-00\d"', opf)), 5, "manifest 新增 5 项")
    for name in ("OEBPS/Text/txt-001.xhtml", "OEBPS/Text/txt-005.xhtml"):
        check(name in files, "生成文件存在：%s" % name)
    check("txt-001.xhtml" in text_of(files, "OEBPS/nav.xhtml"), "nav 指向新文件")

    body = text_of(files, "OEBPS/Text/txt-002.xhtml")
    check("&lt;script&gt;alert(1)&lt;/script&gt;" in body, "HTML 特殊字符已转义")
    check("A &amp; B &lt; C &gt; D" in body, "& < > 正确转义")
    check("<h1>第一章　投宿</h1>" in body, "章节标题保留")
    check('href="../Styles/style.css"' in body, "继承书内样式表")
    assert_xml_ok(files, "追加后")

    # 原有章节字节不变
    orig, _ = read_zip(os.path.join(FIX, "book3.epub"))
    for name in ("OEBPS/Text/ch01.xhtml", "OEBPS/Images/cover.png"):
        check(files[name] == orig[name], "原章节未被改写：%s" % name)
    check(">测试之书<" in opf and ">测试作者<" in opf and "示例出版社" in opf, "元数据保持不变")

    # 撤销后不应残留
    s.undo()
    out_undo = os.path.join(tmp, "append-undo.epub")
    s.export(out_undo)
    files_u, _ = read_zip(out_undo)
    check(not any(n.startswith("OEBPS/Text/txt-") for n in files_u), "撤销后新增文件不残留")


def test_append_group(tmp):
    print("[7] TXT 挂到指定大标题 / 指定位置")
    s = E.Session(os.path.join(FIX, "book3.epub"))
    info = s.append_txt(os.path.join(FIX, "sample_gbk.txt"), group_title="新增：外传", at="end")
    eq(info["encoding"], "gb18030", "GBK 文本被正确识别")
    eq(info["chapters"], 3, "识别 3 个章节")
    out = os.path.join(tmp, "append-group.epub")
    rep = s.export(out)
    assert_no_errors(rep["issues"], "TXT 分组追加")
    files, _ = read_zip(out)
    titles = nav_titles(files)
    eq(titles[-4], "新增：外传", "新增大标题")
    eq(titles[-3:], ["楔子", "第一章 初到", "第二章 旧识"], "新章节位于大标题下")

    # 插到第 1 位
    s2 = E.Session(os.path.join(FIX, "book3.epub"))
    s2.append_txt(os.path.join(FIX, "sample_gbk.txt"), at="1")
    out2 = os.path.join(tmp, "append-front.epub")
    s2.export(out2)
    files2, _ = read_zip(out2)
    eq(nav_titles(files2)[0], "楔子", "支持插入到目录首位")


def test_append_epub2(tmp):
    print("[8] EPUB2 追加")
    s = E.Session(os.path.join(FIX, "book2.epub"))
    s.append_txt(os.path.join(FIX, "sample_gbk.txt"))
    out = os.path.join(tmp, "book2-append.epub")
    rep = s.export(out)
    assert_no_errors(rep["issues"], "EPUB2 追加")
    files, _ = read_zip(out)
    check("OEBPS/nav.xhtml" not in files, "EPUB2 不会被塞入 nav.xhtml")
    ncx = text_of(files, "OEBPS/toc.ncx")
    check("第一章 初到" in ncx, "新章节写入 ncx")
    opf = text_of(files, "OEBPS/content.opf")
    check('toc="ncx"' in opf, "spine 仍保留 toc 指向 ncx")
    eq(spine_ids(opf)[-3:], ["txt-001", "txt-002", "txt-003"], "EPUB2 spine 追加")
    check("<guide>" in opf or "<guide" in opf, "guide 保留")


def test_split_modes(tmp):
    print("[9] 切分策略")
    # 无标题 → 按长度兜底
    s = E.Session(os.path.join(FIX, "book3.epub"))
    info = s.append_txt(os.path.join(FIX, "noheading.txt"), size=800)
    check(info["chapters"] >= 2, "无标题时按长度切分为多章（实际 %d）" % info["chapters"])
    check(info["split_rule"] == "size", "标记为 size 规则")

    chapters, _ = txtconv.split_chapters(
        open(os.path.join(FIX, "noheading.txt"), encoding="utf-8").read(), "无名", mode="size", size=800)
    check(len(chapters) >= 2, "size=800 时切出更多章节（实际 %d）" % len(chapters))

    chapters2, _ = txtconv.split_chapters(
        open(os.path.join(FIX, "sample_utf8.txt"), encoding="utf-8-sig").read(), "样本", mode="heading")
    eq([c.title for c in chapters2],
       ["序章", "第一卷　山中来客", "第一章　投宿", "第二章　夜话",
        "第二卷　雾散之后", "第三章　远行", "第四章　归途"], "标题识别完整")
    eq([c.level for c in chapters2], [2, 1, 2, 2, 1, 2, 2], "卷/章层级识别正确")

    # 段落模式
    text = "第一段\n第二段\n\n第三段"
    ch_line, _ = txtconv.split_chapters(text, "x", mode="none", para_mode="line")
    eq(ch_line[0].paragraphs, ["第一段", "第二段", "第三段"], "line 模式每行一段")
    ch_blank, _ = txtconv.split_chapters(text, "x", mode="none", para_mode="blank")
    eq(ch_blank[0].paragraphs, ["第一段第二段", "第三段"], "blank 模式按空行分段")


def test_errors(tmp):
    print("[10] 错误提示")
    def expect_error(fn, keyword, label):
        try:
            fn()
        except Exception as exc:
            check(keyword in str(exc), "%s 提示明确（实际：%s）" % (label, exc))
        else:
            check(False, "%s 未抛出错误" % label)

    expect_error(lambda: E.EpubBook(os.path.join(FIX, "empty.txt")), "不是有效的 EPUB", "空文件当 EPUB")
    expect_error(lambda: open(os.path.join(FIX, "broken.epub"), "rb").read() and
                 E.EpubBook(os.path.join(FIX, "broken.epub")), "不是有效的 EPUB", "损坏的 EPUB")

    s = E.Session(os.path.join(FIX, "book3.epub"))
    expect_error(lambda: s.append_txt(os.path.join(FIX, "empty.txt")), "为空", "空 TXT")
    expect_error(lambda: s.append_txt(os.path.join(FIX, "blank.txt")), "为空", "全空白 TXT")
    expect_error(lambda: s.append_txt(os.path.join(FIX, "missing.txt")), "不存在", "TXT 不存在")
    expect_error(lambda: s.rename("9.9", "x"), "超出范围", "非法路径")
    expect_error(lambda: s.delete("1"), "大标题", "删除非空大标题需指定方式")
    eq(s.dirty, False, "失败的操作不产生脏状态")


def test_validation_and_meta(tmp):
    print("[11] 自检与元数据")
    s = E.Session(os.path.join(FIX, "book3.epub"))
    s.rename("2", "第三章 远行（改）")
    s.append_txt(os.path.join(FIX, "sample_gbk.txt"))
    out = os.path.join(tmp, "final.epub")
    rep = s.export(out)
    assert_no_errors(rep["issues"], "综合编辑")

    files, infos = read_zip(out)
    opf = text_of(files, "OEBPS/content.opf")
    check("2020-01-01T00:00:00Z" not in opf, "dcterms:modified 已刷新")
    check(re.search(r"<meta property=\"dcterms:modified\">\d{4}-\d\d-\d\dT", opf) is not None,
          "新时间戳格式正确")
    check("urn:uuid:11111111-2222-3333-4444-555555555555" in opf, "identifier 未变")
    check(opf.count("<dc:title>测试之书</dc:title>") == 1, "书名未重复/未变")

    # 每个条目名唯一、无空文件
    names = [i.filename for i in infos]
    eq(len(names), len(set(names)), "ZIP 内文件名无重复")
    check(all(i.file_size > 0 for i in infos), "无空文件")

    # 用自检复核
    issues = E.validate_bytes(open(out, "rb").read())
    assert_no_errors(issues, "重新校验")


def test_calibre(tmp):
    print("[12] Calibre 风格多目录 / 锚点")
    b = E.EpubBook(os.path.join(FIX, "calibre.epub"))
    eq(b.nav_path, "EPUB/text/nav.xhtml", "nav 位于 text 子目录")
    eq(b.ncx_path, "EPUB/toc.ncx", "ncx 位于 OPF 同级")
    eq(b.toc[0].children[0].anchor, "sig1", "锚点被解析")
    eq(b.toc[0].children[0].file, "EPUB/text/chapter0001.xhtml", "跨目录 href 解析正确")

    s = E.Session(os.path.join(FIX, "calibre.epub"))
    info = s.append_txt(os.path.join(FIX, "sample_gbk.txt"))
    eq(info["files"][0].split("/")[0:2], ["EPUB", "text"], "新章节写入正文目录")
    s.rename("2", "第二话 途中（改）")
    out = os.path.join(tmp, "calibre.epub")
    rep = s.export(out)
    assert_no_errors(rep["issues"], "Calibre 追加")

    files, _ = read_zip(out)
    body = text_of(files, "EPUB/text/txt-001.xhtml")
    check('href="../styles/style0001.css"' in body, "样式表相对路径正确")
    nav = text_of(files, "EPUB/text/nav.xhtml")
    check("chapter0002.xhtml" in nav, "原有相对链接写法保持")
    check("#sig1" in nav, "锚点链接保持")
    eq(nav_titles_cal(files)[-1], "第二章 旧识", "新章节追加在末尾")
    assert_xml_ok(files, "Calibre 追加后")

    # 删除被 page-list 引用的章节，应同步清理非 toc 导航
    s2 = E.Session(os.path.join(FIX, "calibre.epub"))
    s2.delete("1")
    out2 = os.path.join(tmp, "calibre-del.epub")
    rep2 = s2.export(out2)
    assert_no_errors(rep2["issues"], "Calibre 删除")
    files2, _ = read_zip(out2)
    nav2 = text_of(files2, "EPUB/text/nav.xhtml")
    check("chapter0001.xhtml" not in nav2, "已删除章节的 nav 条目被清除")
    check("chapter0001.xhtml" not in text_of(files2, "EPUB/toc.ncx"), "ncx 条目被清除")
    check("EPUB/text/chapter0001.xhtml" not in files2, "文件被删除")
    check('id="c1"' not in text_of(files2, "EPUB/content.opf"), "manifest 条目被删除")
    assert_xml_ok(files2, "Calibre 删除后")


def nav_titles_cal(files):
    txt = text_of(files, "EPUB/text/nav.xhtml")
    txt = re.sub(r"<!DOCTYPE[^>]*>", "", txt, count=1)
    root = ET.fromstring(txt)
    ns = "{http://www.w3.org/1999/xhtml}"
    epub_type = "{http://www.idpf.org/2007/ops}type"
    for nav in root.iter(ns + "nav"):
        if "toc" in (nav.get(epub_type) or "").split():
            out = []
            for li in nav.iter(ns + "li"):
                a = li.find(ns + "a")
                sp = li.find(ns + "span")
                el = a if a is not None else sp
                if el is not None:
                    out.append("".join(el.itertext()).strip())
            return out
    return []


def test_merge(tmp):
    print("[13] EPUB 合并")
    s = E.Session(os.path.join(FIX, "book3.epub"))
    info = s.merge_epubs([os.path.join(FIX, "book2.epub"), os.path.join(FIX, "calibre.epub")])
    eq(len(info["books"]), 2, "合并两本书")
    eq(info["chapters"], 10, "合并后新增 10 个章节")
    check(all(b["dir"].startswith("OEBPS/merged/") for b in info["books"]), "资源隔离到 OPF 目录内")

    out = os.path.join(tmp, "merged.epub")
    rep = s.export(out)
    assert_no_errors(rep["issues"], "合并")
    files, infos = read_zip(out)

    names = [i.filename for i in infos]
    eq(len(names), len(set(names)), "合并后无路径冲突")
    for name in ("OEBPS/merged/b1/Text/ch01.xhtml", "OEBPS/merged/b1/Styles/style.css",
                 "OEBPS/merged/b2/text/chapter0001.xhtml", "OEBPS/merged/b2/styles/style0001.css"):
        check(name in files, "隔离后的资源存在：%s" % name)

    titles = nav_titles(files)
    check("EPUB2 测试书" in titles and "Calibre 样本" in titles, "每本书生成一个大标题")
    check(titles.index("Calibre 样本") > titles.index("EPUB2 测试书"), "按传入顺序排列")
    eq(titles[-1], "第三话 终点", "最后一章来自第二本并入书")

    opf = text_of(files, "OEBPS/content.opf")
    ids = re.findall(r'<item id="([^"]+)"', opf)
    eq(len(ids), len(set(ids)), "manifest id 唯一")
    eq(spine_ids(opf)[-3:], ["b2-c1", "b2-c2", "b2-c3"], "spine 追加 Calibre 章节")
    check("b1-ch03" in spine_ids(opf), "spine 包含第一本并入书")

    # 原书章节与元数据不动
    orig, _ = read_zip(os.path.join(FIX, "book3.epub"))
    for name in ("OEBPS/Text/ch01.xhtml", "OEBPS/Images/cover.png"):
        check(files[name] == orig[name], "主书资源未被改写：%s" % name)
    check(">测试之书<" in opf and ">测试作者<" in opf, "主书元数据保持不变")

    # 并入书的正文仍能正确引用自己的样式表
    body = text_of(files, "OEBPS/merged/b1/Text/ch01.xhtml")
    check('href="../Styles/style.css"' in body, "并入书正文相对链接保持原样")
    check("OEBPS/merged/b1/Styles/style.css" in files, "对应样式表就位")
    assert_xml_ok(files, "合并后")

    # 不包大标题
    s2 = E.Session(os.path.join(FIX, "book3.epub"))
    s2.merge_epubs([os.path.join(FIX, "book2.epub")], group_each=False)
    out2 = os.path.join(tmp, "merged-flat.epub")
    s2.export(out2)
    files2, _ = read_zip(out2)
    eq(nav_titles(files2)[-1], "第四章 归途", "平铺模式下末章为并入书最后一章")
    check("EPUB2 测试书" not in nav_titles(files2), "平铺模式不生成书名大标题")

    # 合并进 EPUB2 主书
    s3 = E.Session(os.path.join(FIX, "book2.epub"))
    s3.merge_epubs([os.path.join(FIX, "calibre.epub")])
    out3 = os.path.join(tmp, "merged-into-epub2.epub")
    rep3 = s3.export(out3)
    assert_no_errors(rep3["issues"], "合并进 EPUB2")
    files3, _ = read_zip(out3)
    check("OEBPS/nav.xhtml" not in files3, "EPUB2 主书不生成 nav")
    check("第一话 起点" in text_of(files3, "OEBPS/toc.ncx"), "并入内容写入 NCX")

    # 撤销
    s3.undo()
    out4 = os.path.join(tmp, "merged-undo.epub")
    s3.export(out4)
    files4, _ = read_zip(out4)
    check(not any("/merged/" in n for n in files4), "撤销后不残留合并资源")


def test_merge_errors(tmp):
    print("[14] 合并错误处理")
    s = E.Session(os.path.join(FIX, "book3.epub"))
    try:
        s.merge_epubs([os.path.join(FIX, "book3.epub")])
    except Exception as exc:
        check("自己" in str(exc), "拒绝合并自身（%s）" % exc)
    else:
        check(False, "合并自身未报错")

    try:
        s.merge_epubs([os.path.join(FIX, "broken.epub")])
    except Exception as exc:
        check("不是有效的 EPUB" in str(exc), "损坏文件提示明确（%s）" % exc)
    else:
        check(False, "损坏文件未报错")

    try:
        s.merge_epubs([os.path.join(FIX, "empty.txt")])
    except Exception as exc:
        check("不是有效的 EPUB" in str(exc), "非 EPUB 提示明确")
    else:
        check(False, "非 EPUB 未报错")


def test_docx_parse(tmp):
    print("[15] DOCX 解析与渲染")
    d = DX.read_docx(os.path.join(FIX, "sample.docx"))
    eq(d.title, "DOCX 测试文稿", "读取 docProps 标题")
    eq(d.author, "文档作者", "读取作者")
    eq(len(d.chapters), 2, "按一级标题切出 2 章")
    eq(d.chapters[0].title, "第一章　来自 Word", "章节标题（保留全角空格）")
    eq(len(d.media), 1, "抽出 1 张图片")

    html = DX.render_blocks(d.chapters[0], lambda m: "../Images/a.png")
    check("<strong>加粗</strong>" in html, "加粗保留")
    check("<em>斜体</em>" in html, "斜体保留")
    check("A &amp; B &lt; C" in html, "特殊字符转义")
    check("<ul><li>" in html and "<ul><li>嵌套子项" in html, "列表与嵌套层级")
    check("<table class=\"docx-table\">" in html and "<th>项目</th>" in html, "表格渲染")
    check('src="../Images/a.png"' in html, "图片路径正确")
    check("width:200px" in html, "图片按 wp:extent 计算宽度")

    html2 = DX.render_blocks(d.chapters[1], lambda m: "")
    check("<h2>2.1　一个小节</h2>" in html2, "二级标题降为 h2")

    plain = DX.read_docx(os.path.join(FIX, "plain.docx"))
    eq(len(plain.chapters), 1, "无标题文档兜底为单章")
    eq(plain.chapters[0].title, "DOCX 测试文稿", "兜底章节用文档标题")


def test_docx_append(tmp):
    print("[16] DOCX 追加到既有 EPUB")
    s = E.Session(os.path.join(FIX, "book3.epub"))
    info = s.append_docx(os.path.join(FIX, "sample.docx"), group_title="Word 附录")
    eq(info["chapters"], 2, "追加 2 章")
    eq(info["images"], 1, "追加 1 张图片")

    out = os.path.join(tmp, "docx-append.epub")
    rep = s.export(out)
    assert_no_errors(rep["issues"], "DOCX 追加")
    files, _ = read_zip(out)

    check("OEBPS/Text/docx-001.xhtml" in files, "章节文件写入正文目录")
    check("OEBPS/Text/media/docx-001-image1.png" in files, "图片写入 media 目录")
    titles = nav_titles(files)
    check("Word 附录" in titles, "生成大标题")
    eq(titles[-1], "第二章　表格与图片", "目录末尾是 DOCX 最后一章")
    opf = text_of(files, "OEBPS/content.opf")
    check('media-type="image/png"' in opf, "图片进入 manifest")
    eq(spine_ids(opf)[-2:], ["docx-001", "docx-002"], "spine 追加 DOCX 章节")

    body = text_of(files, "OEBPS/Text/docx-001.xhtml")
    check("<strong>加粗</strong>" in body, "正文保留加粗")
    check('src="media/docx-001-image1.png"' in body, "图片相对路径正确")
    orig, _ = read_zip(os.path.join(FIX, "book3.epub"))
    check(files["OEBPS/Text/ch01.xhtml"] == orig["OEBPS/Text/ch01.xhtml"], "原章节未被改写")
    assert_xml_ok(files, "DOCX 追加后")

    s.undo()
    out2 = os.path.join(tmp, "docx-append-undo.epub")
    s.export(out2)
    files2, _ = read_zip(out2)
    check(not any("docx-00" in n for n in files2), "撤销后不残留 DOCX 资源")


def test_docx_to_epub(tmp):
    print("[17] DOCX 转新 EPUB")
    out = os.path.join(tmp, "from-docx.epub")
    rep = E.docx_to_epub(os.path.join(FIX, "sample.docx"), out, title="手稿测试")
    eq(rep["title"], "手稿测试", "书名可覆盖")
    eq(rep["chapters"], 2, "生成 2 章")
    eq(rep["images"], 1, "生成 1 张图片")
    assert_no_errors(rep["issues"], "DOCX 转 EPUB")

    files, infos = read_zip(out)
    for name in ("mimetype", "META-INF/container.xml", "OEBPS/content.opf",
                 "OEBPS/nav.xhtml", "OEBPS/toc.ncx", "OEBPS/Styles/style.css",
                 "OEBPS/Text/chapter001.xhtml", "OEBPS/Images/docx-001.png"):
        check(name in files, "生成 %s" % name)
    eq(infos[0].filename, "mimetype", "mimetype 在首位")
    eq(infos[0].compress_type, 0, "mimetype 未压缩")
    opf = text_of(files, "OEBPS/content.opf")
    check('version="3.0"' in opf and "<dc:title>手稿测试</dc:title>" in opf, "OPF 元数据正确")
    eq(nav_titles(files), ["第一章　来自 Word", "第二章　表格与图片"], "nav 章节顺序正确")
    assert_xml_ok(files, "DOCX 转 EPUB")

    out2 = os.path.join(tmp, "from-plain.epub")
    rep2 = E.docx_to_epub(os.path.join(FIX, "plain.docx"), out2)
    eq(rep2["chapters"], 1, "无标题 DOCX 生成单章")
    assert_no_errors(rep2["issues"], "无标题 DOCX 转 EPUB")


def test_docx_errors(tmp):
    print("[18] DOCX 错误处理")
    import docxconv as DX2

    def expect(fn, keyword, label):
        try:
            fn()
        except Exception as exc:
            check(keyword in str(exc), "%s 提示明确（%s）" % (label, exc))
        else:
            check(False, "%s 未抛出错误" % label)

    expect(lambda: DX2.read_docx(os.path.join(FIX, "broken.docx")),
           "不是有效的 DOCX", "损坏的 DOCX")
    expect(lambda: DX2.read_docx(os.path.join(FIX, "empty.docx")), "为空", "空 DOCX")
    expect(lambda: DX2.read_docx(os.path.join(FIX, "nodoc.docx")),
           "缺少 word/document.xml", "缺 document.xml")
    expect(lambda: DX2.read_docx(os.path.join(FIX, "sample.txt")), "DOCX", "TXT 当 DOCX")
    expect(lambda: DX2.read_docx(os.path.join(FIX, "nope.docx")), "不存在", "文件不存在")


def test_repair(tmp):
    print("[19] 校验后修复")
    s = E.Session(os.path.join(FIX, "dirty.epub"))
    plan = s.repair_plan()
    keys = {p["key"] for p in plan}
    check({"dead", "manifest", "spine", "uid"} <= keys,
          "修复计划识别出目录悬空/清单缺失/顺序无效/标识错误（%s）" % sorted(keys))
    eq(len(s.toc), 4, "原目录含 1 个指向缺失文件的条目")

    # 不清理时导出：应该能被自检发现
    bad = os.path.join(tmp, "dirty-bad.epub")
    rep_bad = s.export(bad, prune_broken=False)
    check(any(i["level"] == "error" for i in rep_bad["issues"]),
          "未修复时自检报错")

    # 修复目录层
    logs = s.repair()
    check(any("移除 1 个" in x for x in logs), "修复移除了悬空目录条目")
    eq(len(s.toc), 3, "悬空条目已从目录移除")
    s.undo()
    eq(len(s.toc), 4, "修复可撤销")
    s.repair()

    good = os.path.join(tmp, "dirty-fixed.epub")
    rep = s.export(good)
    assert_no_errors(rep["issues"], "修复后导出")
    eq(rep["pruned_manifest"], 1, "清理了 1 个指向缺失文件的清单条目")
    eq(rep["pruned_spine"], 1, "清理了 1 个无效的正文顺序引用")
    check(rep["fixed_uid"] is True, "修正了书籍唯一标识")

    files, _ = read_zip(good)
    opf = text_of(files, "OEBPS/content.opf")
    check('id="ghost"' not in opf, "失效清单条目已从 OPF 移除")
    check('idref="nope-ref"' not in opf, "无效 spine 引用已移除")
    check('unique-identifier="bookid"' in opf, "唯一标识已指向存在的 identifier")
    assert_xml_ok(files, "修复后")

    # 空大标题
    s2 = E.Session(os.path.join(FIX, "book3.epub"))
    s2.add_group("空组", "end")
    check(any(p["key"] == "empty" for p in s2.repair_plan()), "能识别空大标题")
    s2.repair()
    check(not any(n.kind == "group" and not n.children for n in s2.toc), "空大标题已移除")


def test_txt_to_epub(tmp):
    print("[20] TXT 转新 EPUB")
    out = os.path.join(tmp, "txt-book.epub")
    rep = E.txt_to_epub(os.path.join(FIX, "sample_utf8.txt"), out, title="TXT 转书")
    eq(rep["title"], "TXT 转书", "书名可覆盖")
    eq(rep["chapters"], 5, "按标题切出 5 章")
    eq(rep.get("groups"), 2, "卷标题生成 2 个大标题")
    assert_no_errors(rep["issues"], "TXT 转 EPUB")

    files, infos = read_zip(out)
    for name in ("OEBPS/content.opf", "OEBPS/nav.xhtml", "OEBPS/toc.ncx",
                 "OEBPS/Text/chapter001.xhtml"):
        check(name in files, "生成 %s" % name)
    eq(infos[0].filename, "mimetype", "mimetype 在首位")
    nav = text_of(files, "OEBPS/nav.xhtml")
    check("<span>第一卷　山中来客</span>" in nav, "卷作为无链接分组")
    body = text_of(files, "OEBPS/Text/chapter001.xhtml")
    check("<h1>序章</h1>" in body, "首章标题写入 h1")
    check("风从北边来" in body, "正文写入")
    assert_xml_ok(files, "TXT 转 EPUB")

    # 无标题文本：按长度兜底
    out2 = os.path.join(tmp, "txt-size.epub")
    rep2 = E.txt_to_epub(os.path.join(FIX, "noheading.txt"), out2, size=800)
    check(rep2["chapters"] >= 2, "无标题时按字数切分（%d 章）" % rep2["chapters"])
    assert_no_errors(rep2["issues"], "无标题 TXT 转 EPUB")

    # 空文件
    try:
        E.txt_to_epub(os.path.join(FIX, "empty.txt"), os.path.join(tmp, "x.epub"))
    except Exception as exc:
        check("为空" in str(exc) or "没有可用的正文" in str(exc),
              "空 TXT 明确报错（%s）" % exc)
    else:
        check(False, "空 TXT 未报错")


def test_ncx_duplicate_target(tmp):
    print("[21] NCX 同一目标重复出现")
    s = E.Session(os.path.join(FIX, "book3.epub"))
    s.toc.append(E.Node("doc", "重出的一章", "OEBPS/Text/ch01.xhtml", "", ""))
    rep = s.export(os.path.join(tmp, "dup.epub"))
    assert_no_errors(rep["issues"], "同一目标重复时不应误报")
    files, _ = read_zip(os.path.join(tmp, "dup.epub"))
    orders = [int(x) for x in re.findall(r'playOrder="(\d+)"', text_of(files, "OEBPS/toc.ncx"))]
    check(orders and sorted(set(orders)) == list(range(1, max(orders) + 1)),
          "playOrder 取值连续（允许回跳）：%s" % orders)
    check(orders != sorted(orders), "重复目标确实复用了较早的 playOrder")


def main():
    if not os.path.isdir(FIX):
        print("请先运行 make_fixtures.py")
        return 1
    tmp = tempfile.mkdtemp(prefix="epubtoc-test-")
    try:
        test_load()
        test_rename(tmp)
        test_move(tmp)
        test_delete(tmp)
        test_group(tmp)
        test_append_txt(tmp)
        test_append_group(tmp)
        test_append_epub2(tmp)
        test_split_modes(tmp)
        test_errors(tmp)
        test_validation_and_meta(tmp)
        test_calibre(tmp)
        test_merge(tmp)
        test_merge_errors(tmp)
        test_docx_parse(tmp)
        test_docx_append(tmp)
        test_docx_to_epub(tmp)
        test_docx_errors(tmp)
        test_repair(tmp)
        test_txt_to_epub(tmp)
        test_ncx_duplicate_target(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("通过 %d 项，失败 %d 项" % (_passed, len(_failed)))
    if _failed:
        for f in _failed:
            print("  - %s" % f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
