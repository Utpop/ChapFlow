# -*- coding: utf-8 -*-
"""审查修复回归测试：阅读器按键/关闭行为 + ImportDialog LLM 切分走工作线程（离屏运行）"""
import os, sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import tempfile
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QMessageBox

# 测试用独立配置目录：不读写用户真实的 QSettings
_CFG_DIR = tempfile.mkdtemp(prefix="chapflow-gui-fixes-cfg-")
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _CFG_DIR)

import chapflow as CF
from ui.reader import ReaderWindow
from ui.dialogs import ImportDialog
from ui import jobs

FIX = os.path.join(HERE, "fixtures")
_pass, _fail = 0, []

def check(cond, msg):
    global _pass
    if cond:
        _pass += 1
    else:
        _fail.append(msg)
        print("  ✗", msg)

def source_of(func):
    """返回绑定方法的源码文本。"""
    import inspect
    return inspect.getsource(func)

def main():
    app = QApplication.instance() or QApplication([])

    sess = CF.Session(os.path.join(FIX, "book3.epub"))
    node = sess.toc[0].children[0]

    # 1) ReaderWindow：进入编辑 → 改字 → 发 Esc → 断言退出编辑且会话正文未变
    r = ReaderWindow(sess, node)
    before = sess.chapter_text(node.file)
    r.start_edit()
    check(r._editing, "进入编辑模式后 _editing 为 True")
    r.editor.setPlainText(before + "\n\n审查回归测试临时改动")
    check(r._dirty_text(), "改字后处于脏状态")

    # 屏蔽可能弹出的确认框，避免离屏卡住（Esc 走的是 cancel_edit，不应弹框）
    r.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))

    check(not r._editing, "Esc 后退出编辑模式（_editing==False）")
    check(sess.chapter_text(node.file) == before, "Esc 取消后会话正文未改变")

    # 2) 源码中 ReaderWindow.keyPressEvent 只定义一次（_BodyEditor 的那次不算）
    import inspect as _inspect
    reader_src = _inspect.getsource(ReaderWindow)
    n_def = reader_src.count("def keyPressEvent(")
    check(n_def == 1, "ReaderWindow 内 keyPressEvent 只定义一次（实际 %d）" % n_def)
    check("def reject(" in reader_src, "ReaderWindow 覆写了 reject()")

    # 3) ImportDialog._split_text 的 LLM 分支包含 run_blocking 调用
    dlg = ImportDialog(sess, os.path.join(FIX, "sample_utf8.txt"), "txt")
    calls = []
    local_result = [("本地切分", {})]

    def fake_run_blocking(parent, fn, title="", *a, **k):
        calls.append((parent, title))
        return True, [("LLM 结果", {})]

    orig_run = jobs.run_blocking
    orig_split = dlg._split_text.__globals__["TX"].split_chapters
    jobs.run_blocking = fake_run_blocking
    try:
        o = {"para": "line", "drop_pn": False, "rules": {},
             "llm": {"enabled": True}}
        # 让本地回退分支也返回可识别结果，便于断言取消路径
        def fake_split(*a, **k):
            if k.get("llm") is not None:
                return [("LLM 结果", {})]
            return local_result[0]
        dlg._split_text.__globals__["TX"].split_chapters = fake_split
        res = dlg._split_text("第一章\n正文", o)
    finally:
        jobs.run_blocking = orig_run
        dlg._split_text.__globals__["TX"].split_chapters = orig_split

    check(len(calls) == 1, "LLM 分支调用 jobs.run_blocking 一次（实际 %d）" % len(calls))
    check(calls and calls[0][1] == "正在请求 LLM 复核标题…",
          "run_blocking 使用正确进度提示")
    check(res == [("LLM 结果", {})], "成功时返回 run_blocking 的结果")

    # 用户取消 → 回退本地规则切分（llm=None 再调一次）
    def fake_cancel(parent, fn, title="", *a, **k):
        calls.append((parent, title))
        return False, None
    local_again = []
    jobs.run_blocking = fake_cancel
    try:
        def fake_split2(*a, **k):
            if k.get("llm") is not None:
                return [("LLM 结果", {})]
            local_again.append(k)
            return local_result[0]
        dlg._split_text.__globals__["TX"].split_chapters = fake_split2
        res2 = dlg._split_text("第一章\n正文", o)
    finally:
        jobs.run_blocking = orig_run
        dlg._split_text.__globals__["TX"].split_chapters = orig_split
    check(len(local_again) == 1 and "llm" not in local_again[0],
          "取消时回退本地规则切分（llm=None 再调一次）")
    check(res2 == local_result[0], "取消时返回本地切分结果")

    # 显式关闭窗口，避免离屏 Qt 在进程退出时析构窗口触发 abort
    dlg.close(); r.close()
    app.processEvents()

    print("test_gui_fixes: %d passed, %d failed" % (_pass, len(_fail)))
    return 1 if _fail else 0

if __name__ == "__main__":
    sys.exit(main())
