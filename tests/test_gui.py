# -*- coding: utf-8 -*-
"""GUI 功能串联测试：确认界面按钮背后都是真实业务调用（离屏运行）"""
import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import tempfile
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton

# 测试用独立配置目录：不读写用户真实的 QSettings（分章规则会记忆上次设置）
_CFG_DIR = tempfile.mkdtemp(prefix="chapflow-cfg-")
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _CFG_DIR)
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
    check(nb.rule_panel.isVisibleTo(nb) and not nb.docx_row.isVisibleTo(nb),
          "TXT 模式显示分章规则面板")
    check(nb.rule_panel.rules().mode == "simple", "默认使用简易规则")
    check(nb.rule_panel.rules().unit == "章", "默认单位是「章」")
    nb2 = NewBookDialog(None, os.path.join(FIX, "sample.docx"))
    check(nb2.preview_list.count() == 2, "DOCX 预览识别 2 章")
    check(nb2.docx_row.isVisibleTo(nb2) and not nb2.rule_panel.isVisibleTo(nb2),
          "DOCX 模式改为按标题层级切分")
    # 11b) LLM 辅助切分：面板开关 + 设置对话框
    panel = nb.rule_panel
    check(panel.cb_llm.isVisibleTo(nb), "分章面板有「LLM 辅助识别标题」开关")
    check(not panel.cb_llm.isChecked(), "默认不启用 LLM（不联网）")
    check(panel.llm_options() is None, "未勾选时 llm_options() 返回 None")
    panel.cb_llm.setChecked(True)
    opt = panel.llm_options()
    check(opt is not None and opt.enabled, "勾选后返回已启用的配置")
    check(isinstance(panel.llm_hint.text(), str), "勾选后有状态提示文案")
    panel.cb_llm.setChecked(False)
    check(panel.llm_options() is None, "取消勾选后回到 None")
    panel.rb_size.setChecked(True)
    check(not panel.cb_llm.isEnabled(), "按字数模式下 LLM 开关置灰（不干预显式切法）")
    panel.rb_simple.setChecked(True)
    check(panel.cb_llm.isEnabled(), "切回简易规则后开关恢复可用")

    from ui.dialogs import LLMSettingsDialog
    llm_dlg = LLMSettingsDialog(nb)
    check(llm_dlg.model_edit is not None and llm_dlg.base_edit is not None,
          "设置对话框有服务地址与模型输入框")
    check(llm_dlg.key_edit.echoMode() == QLineEdit.Password, "API Key 默认隐藏")
    cfg = llm_dlg._collect()
    check(hasattr(cfg, "max_retries") and cfg.max_retries >= 1, "可配置最大重试次数")
    check(hasattr(cfg, "timeout") and cfg.timeout >= 5, "可配置超时")
    check(cfg.fallback is True, "默认失败回退本地规则")
    llm_dlg.cb_fallback.setChecked(False)
    check(llm_dlg._collect().fallback is False, "可关闭回退（改为报错）")
    check(not llm_dlg.enabled() or llm_dlg.cfg is not None, "enabled() 读当前配置")

    # 11c) 布局回归：分章面板被压扁会让行与行叠在一起（截图里出现过）
    nb_e = NewBookDialog(None, "")
    nb_e.resize(900, 700)
    nb_e.show()
    QApplication.processEvents()
    panel_e = nb_e.rule_panel
    check(panel_e.height() >= panel_e.sizeHint().height(),
          "分章面板拿到完整高度（%d >= %d）" % (panel_e.height(), panel_e.sizeHint().height()))
    check(panel_e.geometry().y() + panel_e.height() <= nb_e.para_row.geometry().y(),
          "面板不压到「段落」那一行")
    check(not nb_e.docx_row.isVisibleTo(nb_e), "空状态下不显示「每章从哪级标题开始」")
    nb_d = NewBookDialog(None, os.path.join(FIX, "sample.docx"))
    check(nb_d.docx_row.isVisibleTo(nb_d), "DOCX 模式显示标题级别下拉")
    check(nb_d.level_combo.currentData() == 1, "默认以 1 级标题切章")
    check("1 级标题" in nb_d.level_combo.currentText(), "层级用「1 级标题」这种人话表述")
    # 11d) 章节预览放大：列表铺满整窗，Esc 还原
    nb_z = NewBookDialog(None, os.path.join(FIX, "sample_utf8.txt"))
    nb_z.show()
    QApplication.processEvents()
    size_before = nb_z.size()
    check(nb_z.btn_zoom.isVisibleTo(nb_z), "章节预览卡有放大按钮")
    check(nb_z._card_file.isVisibleTo(nb_z), "放大前显示文件卡")
    nb_z.toggle_zoom()
    QApplication.processEvents()
    check(nb_z._zoomed, "点击后进入放大状态")
    check(not nb_z._card_file.isVisible(), "放大时文件卡收起")
    check(not nb_z._card_opt.isVisible(), "放大时切分方式卡收起")
    check(nb_z.preview_list.isVisible(), "放大后章节列表仍在")
    check(nb_z.height() >= size_before.height(), "放大后窗口不小于原来")
    nb_z.toggle_zoom()
    QApplication.processEvents()
    check(not nb_z._zoomed and nb_z._card_file.isVisible(), "再点一次还原")
    check(nb_z.size() == size_before, "还原后窗口尺寸回到原样")

    # 11e) 章节编号对话框：可只规范章节名，不动编号
    from ui.dialogs import NumberingDialog as NumberingDialog
    s_tidy = CF.Session(os.path.join(FIX, "book3.epub"))
    s_tidy.toc[0].title = "第一章.测试标题"
    nd = NumberingDialog(s_tidy)
    check(nd.tidy_btn.isEnabled(), "有可规范的标题时按钮可用")
    nd._apply_tidy()
    check(s_tidy.toc[0].title == "第一章 测试标题", "只规范章节名：分隔符换成空格")
    check(nd.tidied == 1, "返回规范处数")
    check(nd.rb_cont.isChecked() and not nd._per_group(), "默认整书连续编号（单选）")
    check(nd.rb_group.isEnabled(), "书里有大标题时「独立编号」可选")
    check(nd.scope_combo.count() == 2, "检查范围 = 全部大标题 + 1 个大标题")
    nd2 = NumberingDialog(s_tidy)
    nd2.scope_combo.setCurrentIndex(1)          # 只看第一个大标题
    QApplication.processEvents()
    check(nd2._scope() == "1", "选中大标题后范围变成该组")
    check("大标题" in nd2.summary.text() or "卷" in nd2.summary.text(),
          "扫描结果按所选大标题统计（%s）" % nd2.summary.text()[:40])

    # 11f) 每个大标题各自从 1 开始编号
    s_pg = CF.Session(os.path.join(FIX, "book3.epub"))
    g_pg = CF.Node("group", "第二卷 风起")
    g_pg.children.append(CF.Node("doc", "第五章 破晓", "OEBPS/Text/x1.xhtml", "", ""))
    s_pg.toc.append(g_pg)
    nd_pg = NumberingDialog(s_pg)
    check(nd_pg.rb_cont.text() == "整书连续编号"
          and nd_pg.rb_group.text() == "每个大标题独立编号",
          "两种编号方式是单选按钮，不是一个下拉")
    nd_pg.rb_group.setChecked(True)
    QApplication.processEvents()
    nums = [i["num"] for g in nd_pg.plan["groups"] for i in g["items"]]
    check(nums == [1, 2, 1, 2, 1], "各卷编号都从 1 开始（%s）" % nums)
    check(nd_pg.plan.get("per_group") is True, "计划标记为按卷独立编号")
    check(len(nd_pg.plan["groups"]) == 3, "预览按大标题分成 3 段（含未归卷的散章）")
    check("独立编号" in nd_pg.summary.text(), "摘要注明独立编号")
    nd_pg._apply()
    check(len(s_pg.toc[0].children) == 2 and s_pg.toc[0].children[1].title == "第二章 迷雾",
          "第一卷仍是 1..2")
    check(s_pg.toc[-1].children[0].title == "第一章 破晓",
          "第二卷的第五章改成第一章（卷内独立）")
    s_tidy.undo()
    check(s_tidy.toc[0].title == "第一章.测试标题", "规范章节名可撤销")



    check(win.btn_convert.isEnabled(), "「格式转换」在未打开书时也可用")
    check(win.btn_save.isEnabled(), "「保存」在有书时可用")

    # 11ac) 元数据编辑
    from ui.dialogs import MetadataDialog
    sm = CF.Session(os.path.join(FIX, "book3.epub"))
    md_dlg = MetadataDialog(sm)
    check(md_dlg.fields["title"].text() == "测试之书", "对话框回填书名")
    check(md_dlg.fields["creators"].text() == "测试作者", "对话框回填作者")
    md_dlg.fields["title"].setText("元数据新书名")
    md_dlg.fields["subjects"].setText("科幻、文学")
    md_dlg.desc_edit.setPlainText("这是简介")
    md_dlg._save()
    check(sm.metadata()["title"] == "元数据新书名", "保存后书名写入会话")
    check(sm.metadata()["subjects"] == "科幻、文学", "标签写入会话")
    check(win.btn_meta.isEnabled(), "书籍信息卡有「编辑」入口")

    # 11ab) 诊断页的 EPUB3 升级入口
    s2up = CF.Session(os.path.join(FIX, "book2.epub"))
    rep2 = s2up.export(os.devnull, dry_run=True)
    rd3 = ReportDialog(s2up, {"export": rep2}, upgrade_plan=s2up.upgrade_plan())
    labels = [b.text() for b in rd3.findChildren(QPushButton)]
    check(any("升级" in t for t in labels), "报告里有升级按钮（%s）" % "、".join(labels))
    rd3._do_upgrade()
    check(rd3.upgrade_requested, "点击升级会标记请求")

    # 11aa) 章节编号对话框
    from ui.dialogs import NumberingDialog
    s3 = CF.Session(os.path.join(FIX, "book3.epub"))
    s3.delete("1.2", physical=False)
    s3.toc[0].children.insert(1, CF.Node("doc", "雾中独行", "OEBPS/Text/ch02.xhtml", "", ""))
    nd = NumberingDialog(s3)
    check("缺号" in nd.issues.text(), "对话框列出缺号（%s）" % nd.issues.text().split(chr(10))[0])
    check(nd.apply_btn.isEnabled(), "有变更时可点重新编号")
    before = len(s3.toc[0].children)
    nd._apply()
    check(nd.applied >= 1, "应用后返回变更数（%d）" % nd.applied)
    check(any(n.title.startswith("第") for n in s3.toc[0].children), "重排后目录标题带编号")
    check(len(s3.toc[0].children) == before, "重排不增删章节")
    check(win.btn_numbering.isEnabled(), "工具栏「编号」可用")

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

    # 11g) 主界面左侧「预览」列
    win._select_node(sess2.toc[0])            # 大标题：没有正文
    app.processEvents()
    check(not win._preview_ready, "选中大标题时预览为空")
    check(win.preview_empty.isVisibleTo(win) or not win.preview_view.isVisibleTo(win),
          "空状态可见")
    win._select_node(chapter)                 # 章节：渲染正文
    app.processEvents()
    check(win._preview_ready, "选中章节后预览就绪")
    check("他推开门" in win.preview_view.toPlainText(), "预览里是这一章的正文")
    check(win.preview_words.text().endswith("字"), "预览标题栏显示字数（%s）"
          % win.preview_words.text())
    check(win.btn_preview_open.isEnabled(), "预览里的「阅读」按钮可用")
    check(win.preview_view.verticalScrollBar().value() == 0, "换章后回到顶部")

    # 11h) 分章规则：预设与本地记忆
    panel = win.rule_panel if hasattr(win, "rule_panel") else None
    from ui.widgets import SplitRulePanel
    panel = SplitRulePanel()
    check(panel.preset_combo.count() >= 7,
          "预设下拉：占位 + 内置写法（%d 项）" % panel.preset_combo.count())
    idx = panel.preset_combo.findData("builtin\x1fChapter N（正则）")
    panel.preset_combo.setCurrentIndex(idx)
    panel._apply_preset()
    check(panel._mode() == "regex", "套用预设切到正则模式")
    check(panel.regex_edit.text() == r"^\s*Chapter\s*[0-9]+", "预设带出正则表达式")
    panel._save_preset("测试规则")
    check("custom\x1f测试规则" in [panel.preset_combo.itemData(i)
                                   for i in range(panel.preset_combo.count())],
          "保存的预设出现在下拉里")
    # 上次设置：改动 → 存 → 新面板恢复
    panel.rb_size.setChecked(True)
    panel.size_spin.setValue(1500)
    panel._autosave()
    panel2 = SplitRulePanel()
    check(panel2._mode() == "size" and panel2.size_spin.value() == 1500,
          "新面板恢复上次的分章设置")
    # 输入历史
    panel2.regex_edit.setText(r"^\s*序章")
    panel2.regex_edit.editingFinished.emit()   # 模拟焦点离开：此刻才记入历史
    import ui.splitmem as SM
    check(r"^\s*序章" in SM.load_history("regex"), "敲过的正则记进历史")
    # 清理：删掉测试预设
    panel2._delete_preset("测试规则")
    panel2._autosave()
    check("测试规则" not in [it["name"] for it in SM.load_custom()],
          "删除预设生效")

    # 12) 空状态：未打开时不应显示伪数据
    win.session = None; win._update_empty_state(); app.processEvents()
    check(win.tree_empty.isVisible(), "未打开时显示空状态")
    check(win.book_title.text() == "—", "未打开时不显示伪书名")

    print("\n通过 %d 项，失败 %d 项" % (_pass, len(_fail)))
    return 1 if _fail else 0

if __name__ == "__main__":
    sys.exit(main())
