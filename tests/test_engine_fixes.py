#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.3.2 审查修复回归（独立 runner，不改动其它测试文件）"""
import io
import os
import sys
import zipfile
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import chapflow as E  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
_passed = 0
_failed = []


def check(cond, msg):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(msg)
        print("  FAIL: %s" % msg)


def eq(a, b, msg):
    check(a == b, "%s (expected %r, got %r)" % (msg, b, a))


def _epub_with_remote_nav_ncx(tmp, path):
    """基于 book3.epub 造一本 nav 与 NCX 各含一个外链的书"""
    src = os.path.join(FIX, "book3.epub")
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(path, "w") as zout:
        for it in zin.infolist():
            data = zin.read(it.filename)
            if it.filename == "OEBPS/nav.xhtml":
                txt = data.decode("utf-8")
                txt = txt.replace(
                    '<li><a href="Text/ch01.xhtml">第一章 初见</a></li>',
                    '<li><a href="Text/ch01.xhtml">第一章 初见</a></li>\n'
                    '          <li><a href="https://example.com/remote.xhtml">远程章节</a></li>',
                    1,
                )
                data = txt.encode("utf-8")
            elif it.filename == "OEBPS/toc.ncx":
                txt = data.decode("utf-8")
                txt = txt.replace(
                    '<content src="Text/ch01.xhtml"/>',
                    '<content src="https://example.com/remote.xhtml"/>',
                    1,
                )
                data = txt.encode("utf-8")
            zout.writestr(it, data)


def test_remote_href_validate(tmp):
    """P1-6: validate_bytes 不应把远程 nav/NCX 链接记为失效"""
    p = os.path.join(tmp, "remote-links.epub")
    _epub_with_remote_nav_ncx(tmp, p)
    with open(p, "rb") as f:
        issues = E.validate_bytes(f.read())
    errs = [i["text"] for i in issues if i["level"] == "error"]
    check(not errs, "远程 nav/NCX 链接不报错误: %s" % errs[:3])
    oks = [i["text"] for i in issues if i["level"] == "ok"]
    check(any("远程链接" in t and "nav" in t for t in oks),
          "nav 的 ok 提示包含远程链接说明: %s" % oks)
    check(any("远程链接" in t and "NCX" in t for t in oks),
          "NCX 的 ok 提示包含远程链接说明: %s" % oks)


def test_cmd_numbering_apply_per_group(tmp):
    """P1-7: --apply 执行路径应与 preview 使用一致的 per_group 参数"""
    import types
    import chapflow

    calls = []
    seen = {}

    class FakeSession:
        def __init__(self, path):
            pass

        def numbering_plan(self, scope, add_missing=False, per_group=False, **kw):
            seen["preview_per_group"] = per_group
            return {
                "error": None, "scope": "整本书", "total_docs": 1,
                "numbered_docs": 1, "plain_docs": 0, "special_docs": 0,
                "groups": [{"label": "", "numbers": [], "gaps": [],
                            "disorder": [], "duplicates": [], "special": []}],
                "style_label": "第N章", "per_group": per_group, "changes": [],
            }

        def renumber(self, scope, add_missing=False, per_group=False, **kw):
            calls.append({"scope": scope, "add_missing": add_missing,
                          "per_group": per_group})
            return []

        def export(self, out):
            return {"issues": []}

    orig = chapflow.Session
    chapflow.Session = FakeSession
    try:
        for apply in (False, True):
            args = types.SimpleNamespace(
                epub="dummy.epub", scope="all", add_missing=False,
                per_group=True, apply=apply, output=os.path.join(tmp, "out.epub"),
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = chapflow.cmd_numbering(args)
            eq(rc, 0, "cmd_numbering apply=%s 返回 0" % apply)
            eq(seen.get("preview_per_group"), True,
               "preview per_group apply=%s" % apply)
        eq(len(calls), 1, "--apply 调用了一次 renumber")
        eq(calls[0]["per_group"], True, "--apply 把 per_group=True 传给 renumber")
    finally:
        chapflow.Session = orig


def test_repair_respects_user_edits(tmp):
    """P2-3: repair 清洗坏 XML 时不得覆盖用户已有编辑"""
    import shutil

    s_path = os.path.join(tmp, "dirty2-edits.epub")
    shutil.copy2(os.path.join(FIX, "dirty2.epub"), s_path)
    broken = "OEBPS/Text/ch03.xhtml"
    # 用户编辑仍是坏 XML（含未定义实体），这样 _scan_broken_docs 会把它列为
    # 可清洗项，才能验证「跳过清洗」分支；若编辑本身合法则不会被扫描到。
    user_edit = b'<html><body><p>&nbsp; USER-EDIT-DO-NOT-OVERWRITE</p></body></html>'
    s = E.Session(s_path)
    try:
        check(any(p == broken for p, _ in s._scan_broken_docs()[0]),
              "用户编辑的坏正文应被扫描为可清洗")
        s.new_files[broken] = user_edit
        check(any(p == broken for p, _ in s._scan_broken_docs()[0]),
              "用户编辑后的坏正文仍在可清洗列表")
        logs = s.repair()
        eq(s.new_files.get(broken), user_edit, "repair 后用户编辑仍在")
        check(any("用户编辑优先" in x for x in logs),
              "repair 日志说明跳过清洗: %s" % logs)
    finally:
        s.book.close()


def test_reorder_spine_keeps_duplicates(tmp):
    """P2-7: _reorder_spine 保留同一 idref 的多个 itemref 实例"""
    root = E.parse_xml(
        '<package><spine>\n'
        '  <itemref idref="a"/><itemref idref="b"/><itemref idref="a"/>'
        '  <itemref idref="c"/>'
        '</spine></package>'
    )
    pkg = E.first_child(root, "package")
    sp = E.first_child(pkg, "spine")
    before = [ir.get("idref") for ir in E.child_els(sp, "itemref")]
    eq(before, ["a", "b", "a", "c"], "原始 spine 含重复 idref")

    manifest = [
        {"id": "a", "path": "a.xhtml"},
        {"id": "b", "path": "b.xhtml"},
        {"id": "c", "path": "c.xhtml"},
    ]
    # 目标顺序 b -> a；原 spine 中 a 的两个实例必须按原相对次序一起输出
    E._reorder_spine(pkg, manifest, ["b.xhtml", "a.xhtml"])
    after = [ir.get("idref") for ir in E.child_els(sp, "itemref")]
    eq(after, ["b", "a", "a", "c"], "重复 idref 实例全部保留且按 wanted 顺序展开")


def test_main_catches_oserror(tmp):
    """P2-8: CLI main 捕获 OSError，打印可读错误并返回 1"""
    import chapflow

    args = chapflow.build_parser().parse_args(["validate", os.path.join(tmp, "no-such.epub")])
    buf_out, buf_err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        rc = chapflow.main(["validate", os.path.join(tmp, "no-such.epub")])
    eq(rc, 1, "validate 不存在文件返回 1")
    check("错误" in buf_err.getvalue(), "stderr 含可读错误: %r" % buf_err.getvalue())


def test_decode_sniff_probe(tmp):
    """P2-1/P2-2 文本探针: 多编码嗅探与 _decode_xml_text 一致"""
    gb = "<html><body>中文正文</body></html>".encode("gb18030")
    eq(E._decode_xml_text(gb), gb.decode("gb18030"), "gb18030 文本可被嗅探解码")


def main():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                print("[RUN] %s" % name)
                try:
                    fn(tmp)
                except Exception as exc:
                    import traceback
                    traceback.print_exc()
                    _failed.append("%s raised %r" % (name, exc))
    total = _passed + len(_failed)
    print("test_engine_fixes: %d passed, %d failed, %d total" % (_passed, len(_failed), total))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
