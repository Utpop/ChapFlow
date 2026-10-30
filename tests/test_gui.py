# -*- coding: utf-8 -*-
"""GUI 功能串联测试：确认界面按钮背后都是真实业务调用（离屏运行）"""
import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from PySide6.QtWidgets import QApplication, QPushButton
from ui.theme import build_qss
from ui.fonts import apply_app_font
from ui.main_window import MainWindow
from ui.dialogs import ImportDialog, MergeDialog, ReportDialog
import chapflow as CF

FIX = os.path.join(HERE, "fixtures")
_pass, _fail = 0, []

def check(cond, msg):
    global _pass
    if cond: _pass += 1
    else:
        _fail.append(msg); print("  ✗", msg)

def pump(app, cond, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond(): return True
        time.sleep(0.02)
    return False

def main():
    app = QApplication([]); app.setStyle("Fusion"); apply_app_font(app); app.setStyleSheet(build_qss())
    win = MainWindow(); win.resize(1400, 900); win.show()
    app.processEvents()

    # 1) 打开
    win.load(os.path.join(FIX, "book3.epub"))
    ok = pump(app, lambda: win.session is not None)
    check(ok, "打开 EPUB 后会话就绪")
    check(win.model.rowCount() > 0, "目录树已填充")
    check(win.book_title.text() == "测试之书", "书籍信息卡显示真实书名（%s）" % win.book_title.text())
    check("EPUB 3.0" in win.book_tag.text(), "显示 EPUB 版本")
    check(win.st_manifest._value.text() == "8", "manifest 计数来自解析结果")
    check(win.st_spine._value.text() == "4", "spine 计数来自解析结果")

    # 2) 选中第一项 → 详情卡
    idx = win.proxy.index(0, 0)
    win.tree.setCurrentIndex(idx)
    app.processEvents()
    node = win._current_node()
    check(node is not None, "选中后拿到节点")
    check(win.title_edit.text() == node.title, "详情卡标题与节点一致")
    check(win.detail_level.text().startswith("第 1 层"), "层级标签正确")

    # 3) 改名（走真实 rename）
    first_uid = node.uid
    win.title_edit.setText("第一卷 试炼（改）")
    win._commit_title()
    app.processEvents()
    n2 = win.model.find_uid(first_uid)
    check(n2.title == "第一卷 试炼（改）", "改名写入业务模型")
    check(win.session.can_undo, "改名后产生撤销记录")
    check("还可撤销 1 步" in win.btn_undo.toolTip(),
          "撤销按钮提示显示可撤销步数（%s）" % win.btn_undo.toolTip())
    check(win.btn_undo.text() == "", "撤销按钮为纯图标（无文字）")

    # 4) 撤销 / 重做
    win.undo(); app.processEvents()
    check(win.model.find_uid(first_uid).title == "第一卷 试炼", "撤销恢复原标题")
    check(win.session.can_redo, "撤销后可以重做")
    check(win.btn_redo.isEnabled(), "重做按钮已启用")
    win.redo(); app.processEvents()
    check(win.model.find_uid(first_uid).title == "第一卷 试炼（改）", "重做恢复修改")

    # 5) 移动（上移/下移按钮背后的 move）
    parent = win.model.find_uid(first_uid)
    child = parent.children[1]
    win._select_node(child)
    app.processEvents()
    before = [c.title for c in parent.children]
    win.move_selected(-1); app.processEvents()
    after = [c.title for c in parent.children]
    check(after[0] == before[1] and after[1] == before[0], "上移改变了真实顺序")
    check(not win.btn_up.isEnabled() and win.btn_down.isEnabled(),
          "移到首位后上移禁用、下移可用（按钮状态按位置刷新）")

    # 6) 搜索过滤
    win.search.setText("迷雾"); app.processEvents()
    check(win.proxy.rowCount() > 0, "搜索有结果")
    check(win.tree.isVisible(), "搜索时树仍可见")
    win.search.setText("不存在的标题xyz"); app.processEvents()
    check(win.proxy.rowCount() == 0, "无匹配时过滤为空（空状态接管）")
    win.search.clear(); app.processEvents()

    # 7) 新建大标题：一个入口，两种行为
    from PySide6.QtCore import QModelIndex, QItemSelectionModel
    win._ask_group_name = lambda title, default="新建大标题": "测试分组"
    before_n = len(win.session.toc)
    win.tree.clearSelection(); win.tree.setCurrentIndex(QModelIndex()); app.processEvents()
    win.new_group(); app.processEvents()
    check(len(win.session.toc) == before_n + 1, "无选中时新建空大标题")
    check(win.session.toc[-1].kind == "group" and not win.session.toc[-1].children,
          "新建的是空分组")

    # 选中两个同级条目 → 打包（顶层）
    win.tree.selectionModel().clearSelection()
    for r in (win.proxy.index(1, 0), win.proxy.index(2, 0)):
        win.tree.selectionModel().select(r, QItemSelectionModel.Select | QItemSelectionModel.Rows)
    app.processEvents()
    win.new_group(); app.processEvents()
    wrapped = [n for n in win.session.toc if n.title == "测试分组" and n.children]
    check(len(wrapped) == 1 and len(wrapped[0].children) == 2, "选中条目被打包进新大标题")

    # 嵌套层级打包：路径必须带父级前缀
    s2 = CF.Session(os.path.join(FIX, "book3.epub"))
    win.session = s2; win.model.set_session(s2)
    win._apply_expansion(set(), expand_all=True); app.processEvents()
    child = s2.toc[0].children[0]
    win._select_node(child); app.processEvents()
    win.new_group(); app.processEvents()
    kids = s2.toc[0].children
    check(kids[0].kind == "group" and kids[0].children[0].title == child.title,
          "嵌套条目打包进父级下的新大标题")
    check(len(s2.toc) == 3, "打包不会改变父级条目数量")

    # 8) 导入对话框：预览解析真实文件
    dlg = ImportDialog(win.session, os.path.join(FIX, "sample_utf8.txt"), "txt")
    check(dlg.preview_list.count() == 7, "导入预览识别 7 章（实际 %d）" % dlg.preview_list.count())
    check(dlg.ok_btn.isEnabled(), "有章节时导入按钮可用")
    info = dlg._apply(dry_run=False)
    check(info is not None and info["append"]["chapters"] == 5, "对话框内导入写入真实会话")

    # 9) 合并对话框：预览结构
    md = MergeDialog(win.session)
    md.books = [os.path.join(FIX, "calibre.epub")]
    md._refresh_list()
    check(md.start_btn.isEnabled(), "选择书籍后开始合并可用")
    check("merged/b1" in md.preview.toPlainText(), "预览显示资源隔离目录")

    # 10) 报告对话框：真实自检结果
    rep = win.session.export(os.devnull, dry_run=True)
    rd = ReportDialog(win.session, {"export": rep, "path": "out.epub"})
    check(any(i["level"] == "ok" for i in rep["issues"]), "自检产生通过项")
    check(rd.conclusion.text().startswith("自检结论"), "报告结论已生成")

    # 10b) 报告里的“修复”入口（脏书）
    dirty = CF.Session(os.path.join(FIX, "dirty.epub"))
    plan = [p for p in dirty.repair_plan() if p["key"] in ("dead", "empty")]
    check(bool(plan), "脏书能给出可修复项")
    rep_bad = dirty.export(os.devnull, dry_run=True, prune_broken=False)
    rd2 = ReportDialog(dirty, {"export": rep_bad}, repair_plan=plan)
    labels = [b.text() for b in rd2.findChildren(QPushButton)]
    check(any(t.startswith("修复") for t in labels), "报告底部有一键修复按钮（%s）" % labels)
    rd2._do_repair()
    check(rd2.repaired, "点击修复会标记待修复")
    for line in dirty.repair():
        pass
    check(len(dirty.toc) == 3, "修复后悬空目录条目被移除")

    # 11) 导出到磁盘（绕过文件对话框）
    out = os.path.join(HERE, "shots", "gui_export.epub")
    result = win.session.export(out)
    check(not any(i["level"] == "error" for i in result["issues"]), "导出无 error")
    check(os.path.exists(out) and os.path.getsize(out) > 0, "导出文件已落盘")

    # 11a) 新建书对话框（TXT / DOCX 两种模式）
    from ui.dialogs import NewBookDialog
    nb = NewBookDialog(None, os.path.join(FIX, "sample_utf8.txt"))
    check(nb.preview_list.count() == 7, "新建书预览识别 7 章（%d）" % nb.preview_list.count())
    check(nb.ok_btn.isEnabled(), "有内容时可生成")
    check(nb.title_edit.text() != "", "书名自动填充")
    check(nb.split_row.isVisibleTo(nb) and not nb.docx_row.isVisibleTo(nb),
          "TXT 模式显示文本切分选项")
    nb2 = NewBookDialog(None, os.path.join(FIX, "sample.docx"))
    check(nb2.preview_list.count() == 2, "DOCX 预览识别 2 章")
    check(nb2.docx_row.isVisibleTo(nb2) and not nb2.split_row.isVisibleTo(nb2),
          "DOCX 模式改为按标题层级切分")
    check(win.btn_convert.isEnabled(), "「格式转换」在未打开书时也可用")
    check(win.btn_save.isEnabled(), "「保存」在有书时可用")

    # 11b) 内置阅读器
    from PySide6.QtGui import QTextDocument
    from PySide6.QtCore import QUrl
    sess2 = CF.Session(os.path.join(FIX, "book3.epub"))
    win.session = sess2; win.model.set_session(sess2)
    win._apply_expansion(set(), expand_all=True); app.processEvents()
    chapter = sess2.toc[0].children[0]
    win._select_node(chapter)
    win.open_reader(chapter)
    app.processEvents()
    check(len(win._readers) == 1, "打开阅读器窗口")
    rd = win._readers[0]
    check("他推开门" in rd.view.toPlainText(), "阅读器渲染出章节正文")
    check(rd.pos_label.text().startswith("第 1 /"), "显示章节位置（%s）" % rd.pos_label.text())
    check(rd.btn_prev.isEnabled() is False, "首章时“上一章”禁用")
    rd.next(); app.processEvents()
    check(rd.title_label.text() == "第二章 迷雾", "“下一章”切到下一章")
    check(rd.btn_prev.isEnabled(), "切章后“上一章”可用")
    img = rd.view.loadResource(QTextDocument.ImageResource, QUrl("epub:/OEBPS/Images/cover.png"))
    check(img is not None and not img.isNull(), "能直接从 EPUB 读取图片资源")
    before = len(win._readers)
    win.open_reader(sess2.toc[0]); app.processEvents()
    check(len(win._readers) == before, "大标题不会打开阅读器")
    rd.close(); app.processEvents()

    # 12) 空状态：未打开时不应显示伪数据
    win.session = None; win._update_empty_state(); app.processEvents()
    check(win.tree_empty.isVisible(), "未打开时显示空状态")
    check(win.book_title.text() == "—", "未打开时不显示伪书名")

    print("\n通过 %d 项，失败 %d 项" % (_pass, len(_fail)))
    return 1 if _fail else 0

if __name__ == "__main__":
    sys.exit(main())
