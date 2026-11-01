# -*- coding: utf-8 -*-
"""GUI 功能串联测试：确认界面按钮背后都是真实业务调用（离屏运行）"""
import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import tempfile
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox, QPushButton

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

    # 1a) 「打开」开过书后仍可交互：只是视觉退成灰色，状态与样式解耦
    check(win.btn_open.isEnabled(), "打开一本书后「打开」按钮仍可点")
    check(win.btn_open.property("secondary") == "true", "已有书时按钮退成次要（灰色）样式")
    check(win.btn_open.property("attention") == "false", "已有书时不再是黄色提醒")
    check("其他书籍" in win.btn_open.toolTip(), "提示可打开其他书籍（%s）" % win.btn_open.toolTip())
    win._busy("正在解析 EPUB…"); app.processEvents()
    check(not win.btn_open.isEnabled(), "读盘中只是临时禁用")
    win._idle(); app.processEvents()
    check(win.btn_open.isEnabled(), "读盘结束立刻恢复可点")
    win.load(os.path.join(FIX, "book2.epub"))
    check(pump(app, lambda: (win.path or "").endswith("book2.epub"), 15), "能再打开另一本书")
    check(win.btn_open.isEnabled() and win.btn_open.property("secondary") == "true",
          "换书后按钮依旧可点")
    win.load(os.path.join(FIX, "book3.epub"))
    check(pump(app, lambda: (win.path or "").endswith("book3.epub"), 15), "再切回原书也正常")

    # 1b) 书籍信息 + 元数据合并为一个页面：默认只读，编辑按钮就地切换
    from ui.dialogs import BookInfoDialog
    bid = BookInfoDialog(win.session, extras={"path": win.path})
    check(bid.title_label.text() == "测试之书", "书籍信息页显示真实书名（%s）" % bid.title_label.text())
    check("EPUB 3.0" in bid.tag_label.text(), "页面显示 EPUB 版本")
    check(bid.stats["manifest"]._value.text() == "8", "manifest 计数来自解析结果")
    check(bid.stats["spine"]._value.text() == "4", "spine 计数来自解析结果")
    check(bid.stats["toc"]._value.text()
          == str(CF.count_nodes(win.session.toc)["total"]), "目录条目数与会话一致")
    check(bid.path_label.text().endswith("book3.epub"), "页面显示原文件路径")
    check(len(bid.fields) == 12, "元数据 12 个字段都在同一页（%d）" % len(bid.fields))
    # 封面说明：贴在封面图下方，内容是格式 + 体积（不是 MIME 末段那种「jpeg」）
    check(bid.cover_meta.text().startswith("PNG"), "封面说明显示真实格式（%s）" % bid.cover_meta.text())
    check(any(u in bid.cover_meta.text() for u in ("B", "KB", "MB")),
          "封面说明带体积（%s）" % bid.cover_meta.text())
    check(abs(bid.cover_meta.geometry().center().x() - bid.cover_view.geometry().center().x()) <= 8,
          "封面说明水平居中于封面图")
    check(bid.cover_meta.geometry().top() - bid.cover_view.geometry().bottom() <= 12,
          "封面说明紧贴封面图下方，不漂到卡片底部")

    # 默认只读
    check(not bid.editing, "默认不在编辑模式")
    check(bid.toggle_btn.text() == "编辑", "底部按钮是「编辑」（%s）" % bid.toggle_btn.text())
    check(all(e.isReadOnly() for e in bid.fields.values()), "只读时所有输入框不可编辑")
    check(bid.desc_edit.isReadOnly(), "只读时简介不可编辑")
    check(bid.btn_pick_cover.isVisibleTo(bid), "只读时封面按钮也在（常驻占位，布局才不跳）")

    # 点编辑 → 可编辑，按钮变保存
    bid._toggle()
    check(bid.editing, "点「编辑」进入编辑模式")
    check(bid.toggle_btn.text() == "保存", "按钮切换为「保存」（%s）" % bid.toggle_btn.text())
    check(not bid.fields["title"].isReadOnly(), "编辑模式下输入框可编辑")
    check(not bid.desc_edit.isReadOnly(), "编辑模式下简介可编辑")
    check(bid.btn_pick_cover.isVisibleTo(bid), "编辑模式下出现「选择图片」")

    # 保存 → 写回会话并回到只读
    bid.fields["title"].setText("弹窗里改的书名")
    bid.fields["creators"].setText("弹窗作者")
    check(bid._save(), "保存返回成功")
    check(win.session.metadata()["title"] == "弹窗里改的书名", "保存后书名写入会话")
    check(win.session.metadata()["creators"] == "弹窗作者", "保存后作者写入会话")
    check(not bid.editing and bid.toggle_btn.text() == "编辑", "保存后退出编辑、按钮回到「编辑」")
    check(bid.fields["title"].isReadOnly(), "保存后回到只读")
    check(bid.changed, "对话框标记为已修改")
    check(bid.title_label.text() == "弹窗里改的书名", "页面标题随保存刷新（%s）" % bid.title_label.text())
    win.undo()                                   # 还原，避免影响后续断言
    check(win.session.metadata()["title"] == "测试之书", "元数据修改可撤销")
    check(not hasattr(win, "info_scroll"), "主界面已移除常驻书籍信息卡")

    # 1c) 工具栏顺序与分组：打开 │ 成书 │ 加正文 │ 理目录
    xs = [b.pos().x() for b in (win.btn_open, win.btn_convert, win.btn_merge,
                                win.btn_add_chapter, win.btn_import,
                                win.btn_new_group, win.btn_numbering,
                                win.btn_book_info)]
    check(xs == sorted(xs), "工具栏按钮从左到右按「打开→成书→加正文→理目录→查看信息」排列（%s）" % xs)
    check(win.btn_book_info.isEnabled(), "「书籍信息」按钮在有书时可用")

    # 2) 选中第一项 → 详情卡
    idx = win.proxy.index(0, 0)
    win.tree.setCurrentIndex(idx)
    app.processEvents()
    node = win._current_node()
    check(node is not None, "选中后拿到节点")
    check(win.title_edit.text() == node.title, "详情卡标题与节点一致")
    check(win.detail_level.text().startswith("第 1 层"), "层级标签正确")

    # 2b) 目录标题行：展开全部 / 收起全部
    win.tree.expandAll(); app.processEvents()
    win.btn_collapse.click(); app.processEvents()
    check(not any(win.tree.isExpanded(win.proxy.index(i, 0)) for i in range(win.proxy.rowCount())),
          "点「收起全部」后所有节点都收起")
    check(win.btn_collapse.isEnabled() and win.act_collapse.isEnabled(),
          "收起按钮与菜单项在有书时可用")
    win.btn_expand.click(); app.processEvents()
    check(win.tree.isExpanded(win.proxy.index(0, 0)), "点「展开全部」后一级条目展开")
    check(win.btn_expand.isEnabled() and win.act_expand.isEnabled(),
          "展开按钮与菜单项在有书时可用")
    check("目录项" in win.btn_expand.toolTip() and "目录项" in win.btn_collapse.toolTip(),
          "两个按钮都有说明（%s / %s）" % (win.btn_expand.toolTip(), win.btn_collapse.toolTip()))
    check(win.btn_expand.pos().x() < win.btn_collapse.pos().x(), "展开在左、收在右")
    win.tree.expandAll(); app.processEvents()      # 还原，后续用例还要点开子项

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

    # 下移回归：move() 的 target 是“插到第 N 位之前”，同层后移会被摘除位移抵消，
    # 过去这里传 pos+1 等于插回自己身上，表现为“能上移、不能下移”
    win.move_selected(1); app.processEvents()
    check([c.title for c in parent.children] == before,
          "下移真的把章节挪回去了（%s）" % [c.title for c in parent.children])
    check(win.btn_up.isEnabled() and not win.btn_down.isEnabled(),
          "回到末位后下移再次禁用")

    # 5b) 章节详情：显示所属一级标题 + 一键升为一级
    sg = CF.Session(os.path.join(FIX, "book3.epub"))
    win.session = sg
    win.model.set_session(sg)
    win.preview_view.set_session(sg)
    win._apply_expansion(set(), expand_all=True); app.processEvents()
    grp = sg.toc[0]
    kid = grp.children[1]
    win._select_node(kid); app.processEvents()
    check(win.parent_row.isVisibleTo(win), "选中二级条目时显示「所属一级标题」")
    check("第一卷 试炼" in win.parent_label.text() and "第 1 位" in win.parent_label.text(),
          "信息含一级标题名称与位置（%s）" % win.parent_label.text())
    check(win.btn_promote.isEnabled() and win.act_promote.isEnabled(), "二级条目时升级按钮可用")
    win._select_node(grp); app.processEvents()
    check(not win.parent_row.isVisibleTo(win), "选中一级条目时整行隐藏")
    check(not win.btn_promote.isEnabled(), "一级条目时升级按钮禁用")
    check(not win.act_promote.isEnabled(), "一级条目时菜单项同样禁用")
    win._select_node(kid); app.processEvents()
    check(kid.title in win.title_edit.text() and "第 1 位" in win.parent_label.text(),
          "换选其它二级条目后信息实时刷新")
    win.promote_selected(); app.processEvents()
    check([n.title for n in sg.toc][1] == kid.title, "升级后排在原一级条目之后（%s）"
          % [n.title for n in sg.toc])
    check(win._current_node() is not None and win._current_node().title == kid.title,
          "升级后仍选中该条目")
    check(not win.parent_row.isVisibleTo(win), "升级后它自己是一级条目，该行隐藏")

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

    # 7c) 拖放：一级标题拖到另一个一级标题上，必须真的成为它的子项
    #      （Qt 的 QSortFilterProxyModel 默认转发会丢掉父级索引 → 过去只会挪到末尾）
    from ui.models import ChapterFilterProxy, ChapterTreeModel
    from PySide6.QtCore import Qt as _Qt
    sd = CF.Session(os.path.join(FIX, "book3.epub"))
    sd.add_group("第二卷 风起", "end")
    _m = ChapterTreeModel(); _m.set_session(sd)
    _p = ChapterFilterProxy(); _p.setSourceModel(_m)
    _mime = _p.mimeData([_p.mapFromSource(_m.index_of(sd.toc[-1]))])
    _target = _p.mapFromSource(_m.index_of(sd.toc[0]))
    check(_p.dropMimeData(_mime, _Qt.MoveAction, -1, 0, _target), "拖到大标题上返回成功")
    check(sd.toc[0].children and sd.toc[0].children[-1].title == "第二卷 风起",
          "一级标题拖进另一个一级标题，成为二级条目")
    check(all(n.title != "第二卷 风起" for n in sd.toc), "被拖动的条目已从根层级移走")

    # 章节拖进大标题同样生效
    sd2 = CF.Session(os.path.join(FIX, "book3.epub"))
    _m2 = ChapterTreeModel(); _m2.set_session(sd2)
    _p2 = ChapterFilterProxy(); _p2.setSourceModel(_m2)
    _doc = sd2.toc[1]
    _mime2 = _p2.mimeData([_p2.mapFromSource(_m2.index_of(_doc))])
    _tgt2 = _p2.mapFromSource(_m2.index_of(sd2.toc[0]))
    check(_p2.dropMimeData(_mime2, _Qt.MoveAction, -1, 0, _tgt2), "章节拖到大标题上返回成功")
    check(sd2.toc[0].children[-1].title == _doc.title, "章节拖进大标题成为其子项")

    # 7d) 过滤开启时拖放：代理行号必须映射回源行号，否则落点错位
    sf = CF.Session(os.path.join(FIX, "book3.epub"))
    sf.add_group("过滤甲", "end")
    sf.add_group("过滤乙", "end")
    n_root = len(sf.toc)
    _mf = ChapterTreeModel(); _mf.set_session(sf)
    _pf = ChapterFilterProxy(); _pf.setSourceModel(_mf)
    _pf.set_keyword("过滤")                      # 只剩 过滤甲 / 过滤乙 两行
    check(_pf.rowCount() == 2, "过滤后只剩 2 行（实际 %d）" % _pf.rowCount())
    _mi = _pf.mimeData([_pf.mapFromSource(_mf.index_of(sf.toc[-1]))])   # 拖「过滤乙」
    root_idx = QModelIndex()
    check(_pf.dropMimeData(_mi, _Qt.MoveAction, 0, 0, root_idx),
          "过滤态拖放到指定行返回成功")
    check(sf.toc[-2].title == "过滤乙" and sf.toc[-1].title == "过滤甲",
          "过滤态拖放按可见顺序落到正确位置（乙到甲前）")
    check(all(n.title not in ("过滤甲", "过滤乙") for n in sf.toc[:-2]),
          "其余条目位置未被打乱")

    # 过滤态拖到列表末尾（row == rowCount）也映射正确
    _mf.refresh()
    _mi2 = _pf.mimeData([_pf.mapFromSource(_mf.index_of(sf.toc[-1]))])  # 拖「过滤甲」
    check(_pf.dropMimeData(_mi2, _Qt.MoveAction, _pf.rowCount(), 0, root_idx),
          "过滤态拖放到末尾返回成功")
    check(sf.toc[-1].title == "过滤甲" and sf.toc[-2].title == "过滤乙",
          "过滤态拖到末尾仍落在可见顺序末尾")

    # 7e) 批量移动：多选拖拽 / 整块上下移 / 移入已有大标题
    from ui import models as _MD
    sb = CF.Session(os.path.join(FIX, "book3.epub"))
    _mb = ChapterTreeModel(); _mb.set_session(sb)
    _pb = ChapterFilterProxy(); _pb.setSourceModel(_mb)
    _pair = sb.toc[1:3]                      # 第三章 / 第四章（根层级）
    _mime3 = _pb.mimeData([_pb.mapFromSource(_mb.index_of(n)) for n in _pair])
    check(_mime3.data(_MD.MIME).count(b'"n') == 2, "多选拖拽把两个节点一起带上")
    _gt = _pb.mapFromSource(_mb.index_of(sb.toc[0]))
    check(_pb.dropMimeData(_mime3, _Qt.MoveAction, -1, 0, _gt), "多选拖到大标题上返回成功")
    check([c.title for c in sb.toc[0].children][-2:] == ["第三章 远行", "第四章 归途"],
          "多选拖拽两章一起进大标题，顺序不变")
    check(len(sb.toc) == 1, "多选拖拽后根层级干净")

    # 老格式（单个 uid 字符串）仍能拖
    _mime4 = _pb.mimeData([_pb.mapFromSource(_mb.index_of(sb.toc[0].children[-1]))])
    check(_pb.dropMimeData(_mime4, _Qt.MoveAction, -1, 0, _gt), "单条拖放仍然可用")

    # 整块上下移：根层级 [第一卷, 第三章, 第四章] 里选中后两章，一次上移整块挪一格
    sb2 = CF.Session(os.path.join(FIX, "book3.epub"))
    win.session = sb2; win.model.set_session(sb2)
    win._apply_expansion(set(), expand_all=True); app.processEvents()
    _pair2 = sb2.toc[1:3]
    win._select_nodes([n.uid for n in _pair2]); app.processEvents()
    check(len(win._selected_nodes()) == 2, "批量选中后保持两行（不会被当前项覆盖成单选）")
    win.move_selected(-1); app.processEvents()
    check([n.title for n in sb2.toc] == ["第三章 远行", "第四章 归途", "第一卷 试炼"],
          "整块上移一格（%s）" % [n.title for n in sb2.toc])
    check(len(win._selected_nodes()) == 2, "移动后仍保持整块选中")
    win.move_selected(1); app.processEvents()
    check([n.title for n in sb2.toc] == ["第一卷 试炼", "第三章 远行", "第四章 归途"],
          "整块再下移一格复原")

    # 右键不能把多选压成单选：QTreeView.setCurrentIndex 会重建选区
    sb4 = CF.Session(os.path.join(FIX, "book3.epub"))
    win.session = sb4; win.model.set_session(sb4)
    win._apply_expansion(set(), expand_all=True); app.processEvents()
    win._select_nodes([n.uid for n in sb4.toc[1:3]]); app.processEvents()
    _in_sel = win.proxy.mapFromSource(win.model.index_of(sb4.toc[2]))
    win._menu_selection(_in_sel); app.processEvents()
    check(len(win._selected_nodes()) == 2, "右键点在选区内仍保持多选（%s）"
          % [n.title for n in win._selected_nodes()])
    check(win.tree.currentIndex() == _in_sel, "当前项切到右键那一行")
    win.move_into_group(title="第一卷 试炼"); app.processEvents()
    check(len(sb4.toc[0].children) == 4, "右键后「移入大标题…」把两章都挪进去了（%d）"
          % len(sb4.toc[0].children))
    # 点选区外：回到「只选这一行」
    win._select_nodes([sb4.toc[0].children[0].uid]); app.processEvents()
    _out = win.proxy.mapFromSource(win.model.index_of(sb4.toc[0].children[-1]))
    win._menu_selection(_out); app.processEvents()
    check([n.title for n in win._selected_nodes()] == ["第四章 归途"],
          "右键点选区外改成只选这一行（%s）" % [n.title for n in win._selected_nodes()])

    # 移入已有大标题：一次挪多章，一步可撤销
    sb3 = CF.Session(os.path.join(FIX, "book3.epub"))
    sb3.add_group("第二卷 终章", "end")
    win.session = sb3; win.model.set_session(sb3)
    win._apply_expansion(set(), expand_all=True); app.processEvents()
    win._select_nodes([n.uid for n in sb3.toc[1:3]]); app.processEvents()
    _root_before = [n.title for n in sb3.toc]
    win.move_into_group(title="第二卷 终章"); app.processEvents()
    check([c.title for c in sb3.toc[-1].children] == ["第三章 远行", "第四章 归途"],
          "两章一起挪进已有大标题（%s）" % [c.title for c in sb3.toc[-1].children])
    check([n.title for n in sb3.toc] == ["第一卷 试炼", "第二卷 终章"],
          "根层级只剩两个大标题")
    sb3.undo(); app.processEvents()
    check([n.title for n in sb3.toc] == _root_before and not sb3.toc[-1].children,
          "一次撤销把两章一起退回（%s）" % [n.title for n in sb3.toc])

    # 7e) jobs.run_blocking：任务在工作线程完成，结果同步回到调用方
    from ui import jobs
    _ok, _val = jobs.run_blocking(win, lambda: 40 + 2, "测试…", cancelable=False)
    check(_ok and _val == 42, "run_blocking 同步返回工作线程的结果")

    def _boom():
        raise ValueError("boom")

    try:
        jobs.run_blocking(win, _boom, "测试…", cancelable=False)
        check(False, "run_blocking 应把任务异常抛回调用方")
    except RuntimeError as exc:
        check("boom" in str(exc), "run_blocking 抛出带原始消息的 RuntimeError")

    # 8) 导入对话框：预览解析真实文件
    dlg = ImportDialog(win.session, os.path.join(FIX, "sample_utf8.txt"), "txt")
    check(dlg.preview_list.count() == 7, "导入预览识别 7 章（实际 %d）" % dlg.preview_list.count())
    check(dlg.ok_btn.isEnabled(), "有章节时导入按钮可用")
    info = dlg._apply(dry_run=False)
    # 默认「通杀」规则比旧的简易规则多认出「序章 / 第一卷 / 第二卷」，写入数与预览一致
    check(info is not None and info["append"]["chapters"] == dlg.preview_list.count(),
          "对话框内导入写入真实会话（%s 章）" % (info["append"]["chapters"] if info else None))

    # 8b) 直接输入正文新增章节：空内容拦截 + 写入真实会话
    from ui.dialogs import AddChapterDialog
    win.session = CF.Session(os.path.join(FIX, "book3.epub"))
    win.model.set_session(win.session)
    win._apply_expansion(set(), expand_all=True); app.processEvents()
    dlg_add = AddChapterDialog(win.session)
    check(dlg_add.text_edit.placeholderText() != "", "正文框有输入提示")
    check(dlg_add.title_edit.placeholderText() != "", "标题框提示可留空自动命名")
    n_before = len(win.session.toc)
    dlg_add._submit()
    app.processEvents()
    check(dlg_add.result_info is None, "空正文不会提交")
    check("不能为空" in dlg_add.error.text(), "空正文给出提示（%s）" % dlg_add.error.text())
    check(len(win.session.toc) == n_before, "空正文不改动目录")
    dlg_add.text_edit.setPlainText("第一段。\n\n第二段。")
    app.processEvents()
    check("8 字 · 2 段" in dlg_add.counter.text(), "字数/段数实时统计（%s）" % dlg_add.counter.text())
    check(dlg_add.error.text() == "", "补上正文后旧提示自动清掉")
    dlg_add.title_edit.setText("输入的新章")
    dlg_add._submit()
    check(dlg_add.result() == 1 and dlg_add.result_info is not None, "有内容时提交成功并关闭")
    check(win.session.toc[-1].title == "输入的新章", "新章节写入真实会话")
    check(dlg_add.result_info["paragraphs"] == 2, "对话框按空行分段")

    s_add = CF.Session(os.path.join(FIX, "book3.epub"))
    dlg_after = AddChapterDialog(s_add, selected_path="1.1")
    check(len(dlg_after.pos_chips.labels()) == 3, "选中条目时多一个「之后」选项")
    check(dlg_after.pos_chips.value() == "after", "选中条目时默认接在它后面")
    check(dlg_after.pos_chips.labels()[1].endswith("之后"), "选项用平铺标签展示（%s）"
          % dlg_after.pos_chips.labels())
    check(not hasattr(dlg_after, "pos_combo"), "下拉框已被平铺标签取代")

    # 追加对话框：插入位置卡位于切分与段落之下、章节预览之上；标签与选项同一行
    _ib = ImportDialog(win.session, os.path.join(FIX, "sample_utf8.txt"), "txt")
    _lay = _ib._body.layout()
    _titles = [w.title_label.text() for i in range(_lay.count())
               for w in [_lay.itemAt(i).widget()]
               if w is not None and getattr(w, "title_label", None) is not None]
    check(_titles.index("插入位置") == _titles.index("切分与段落") + 1,
          "插入位置紧跟切分与段落（%s）" % _titles)
    check(_titles.index("插入位置") == _titles.index("章节预览") - 1,
          "插入位置在章节预览之前（%s）" % _titles)
    _ys = {b.geometry().y() for b in _ib.pos_chips.findChildren(QPushButton)}
    check(len(_ys) == 1, "三个选项横向排在同一行（%s）" % _ys)
    dlg_after.title_edit.setText("插队章")
    dlg_after.text_edit.setPlainText("正文。")
    dlg_after._submit()
    check([c.title for c in s_add.toc[0].children][1] == "插队章", "可插到选中条目之后")

    # 界面入口：按钮 + 添加后自动选中新章节
    import ui.main_window as MW
    check(win.btn_add_chapter.isEnabled(), "工具栏有「添加章节」按钮且可用")

    class _FakeAddDlg:
        def __init__(self, session, parent=None, selected_path=""):
            self.session = session
            self.result_info = None

        def exec(self):
            self.result_info = self.session.add_chapter("界面新增", "界面正文。\n\n第二段。")
            return 1

    _real_dlg = MW.AddChapterDialog
    MW.AddChapterDialog = _FakeAddDlg
    try:
        win.add_chapter()
    finally:
        MW.AddChapterDialog = _real_dlg
    app.processEvents()
    check(any(n.title == "界面新增" for n in win.session.toc), "界面按钮写入真实会话")
    check(win._current_node() is not None and win._current_node().title == "界面新增",
          "添加后自动选中新章节")
    check("界面新增" in win.status_left.text(), "状态栏提示已添加（%s）" % win.status_left.text())

    # 8b) DOCX 没有标题样式时兜底切分 + 预览里手动删章节
    from docxkit import build_docx
    from ui.dialogs import NewBookDialog as _NBD
    _tmpd = tempfile.mkdtemp(prefix="chapflow-docx-")
    _pdoc = os.path.join(_tmpd, "plain.docx")
    build_docx(_pdoc, ["第一章 雪夜", "这是第一章的正文。", "第二章 归途", "这是第二章的正文。",
                       "番外 后记", "这是番外的正文。"])
    _nb = _NBD(None, _pdoc)
    check(_nb.preview_list.count() == 3, "无标题样式的 DOCX 按正文「第X章」切出 3 章（%d）"
          % _nb.preview_list.count())
    check("按正文" in _nb.file_meta.text(), "界面说明这是兜底切分（%s）" % _nb.file_meta.text())
    check([t for t, _m in _nb.preview_list._titles] == ["第一章 雪夜", "第二章 归途", "番外 后记"],
          "预览标题与文档标题行一致")

    _nb.preview_list.item(1).setSelected(True)
    _nb._refresh_preview_counts()
    check(_nb.btn_drop.isEnabled(), "选中章节后「删除选中」可用")
    _nb.btn_drop.click()
    check(_nb.preview_list.count() == 2, "删除后列表少一行")
    check(_nb.preview_list.deleted_indices() == [1], "记录被删的是第 2 章")
    check(_nb.count_badge.text() == "2 章", "章数徽标同步（%s）" % _nb.count_badge.text())
    check(not _nb.btn_drop.text() and not _nb.btn_drop.icon().isNull(),
          "删除按钮只留垃圾桶图标，不再带文字")
    check(_nb.btn_drop.toolTip().startswith("删除识别有误的章节"), "图标按钮有悬停说明（%s）"
          % _nb.btn_drop.toolTip())
    check(_nb.count_badge.isVisibleTo(_nb), "有章节时显示章数气泡")
    check("已跳过 1 章" in _nb.hint.text(), "底部提示已跳过（%s）" % _nb.hint.text())
    _nb.preview_list.restore_all()
    check(_nb.preview_list.count() == 3 and _nb.count_badge.text() == "3 章", "可一键恢复全部")
    for _i in range(_nb.preview_list.count()):
        _nb.preview_list.item(_i).setSelected(True)
    _nb.preview_list.delete_selected()
    _nb._refresh_preview_counts(); app.processEvents()
    check(not _nb.count_badge.isVisibleTo(_nb), "章节全删光时绿色气泡隐藏，不留空占位")
    _nb.preview_list.restore_all(); _nb._refresh_preview_counts(); app.processEvents()
    check(_nb.count_badge.isVisibleTo(_nb), "恢复章节后气泡重新出现")

    # 下拉里的「不切分（整篇一章）」
    _nb.level_combo.setCurrentIndex(_nb.level_combo.findData(0))
    _nb._load_preview()
    check(_nb.preview_list.count() == 1, "选择「不切分」后整篇一章")
    _nb.level_combo.setCurrentIndex(_nb.level_combo.findData(1))
    _nb._load_preview()
    check(_nb.preview_list.count() == 3, "切回 1 级标题后重新切分，删除记录已清空")

    # 删除的章节不会被追加进书里
    _sd = CF.Session(os.path.join(FIX, "book3.epub"))
    _d = ImportDialog(_sd, _pdoc, "docx")
    check(_d.preview_list.count() == 3, "追加对话框同样识别 3 章")
    _d.preview_list.item(0).setSelected(True)
    _d.btn_drop.click()
    _info = _d._apply(dry_run=False)
    check(_info is not None and _info["append"]["chapters"] == 2, "被删掉的章节不会写入书里")
    check([n.title for n in _sd.toc][-2:] == ["第二章 归途", "番外 后记"], "追加后目录只剩 2 章")

    # 8c) DOCX 也能用和 TXT 一样的切分规则
    _nb2 = _NBD(None, _pdoc)
    check(_nb2._basis() == "style", "DOCX 默认按 Word 标题样式")
    check(not _nb2.rule_panel.isVisibleTo(_nb2), "默认不显示分章规则面板")
    check(_nb2.docx_row.isVisibleTo(_nb2), "默认显示「每章从哪级标题开始」")
    _nb2.rb_text.setChecked(True); app.processEvents()
    check(_nb2.rule_panel.isVisibleTo(_nb2), "选「按正文文本规则」后出现与 TXT 相同的分章面板")
    check(not _nb2.docx_row.isVisibleTo(_nb2), "此时标题级别下拉收起")
    check("按正文文本规则" in _nb2.file_meta.text(), "文件信息标注切分依据（%s）"
          % _nb2.file_meta.text())
    _nb2.rule_panel.rb_regex.setChecked(True)
    _nb2.rule_panel.regex_edit.setText(r"^番外")
    _nb2.rule_panel.changed.emit(); app.processEvents()
    check([t for t, _m in _nb2.preview_list._titles] == ["前言", "番外 后记"],
          "正则规则立刻作用于 DOCX 预览（%s）" % [t for t, _m in _nb2.preview_list._titles])
    _nb2.rb_style.setChecked(True); app.processEvents()
    check(not _nb2.rule_panel.isVisibleTo(_nb2) and _nb2.preview_list.count() == 3,
          "切回标题样式后面板收起、章节恢复")

    _id = ImportDialog(CF.Session(os.path.join(FIX, "book3.epub")), _pdoc, "docx")
    check(_id._basis() == "style" and not _id.rule_panel.isVisibleTo(_id),
          "追加对话框的 DOCX 默认按标题样式")
    _id.rb_text.setChecked(True); app.processEvents()
    check(_id.rule_panel.isVisibleTo(_id) and _id._opts()["basis"] == "text",
          "追加对话框也能切到正文文本规则")

    # 9) 合并对话框：预览结构
    md = MergeDialog(win.session)
    md.books = [os.path.join(FIX, "calibre.epub")]
    md._refresh_list()
    check(md.start_btn.isEnabled(), "选择书籍后开始合并可用")
    _root = md.preview.topLevelItem(0)
    check(_root is not None and "原位" in _root.text(0), "目录预览顶层是主书（%s）"
          % (_root.text(0) if _root else ""))
    check(_root.childCount() == 1 and "merged/b1" in _root.child(0).text(0),
          "目录预览里子项标注 merged/b1 隔离目录")
    check(md.list_card.title_label.text() == "待合并书籍（1）", "卡片标题实时显示数量（%s）"
          % md.list_card.title_label.text())
    # 书多于可视行数时列表可滚动，滚动条可见
    md.books = [os.path.join(FIX, "calibre.epub")] * 5
    md._refresh_list()
    md.show(); app.processEvents(); app.processEvents()      # 显示后布局才完成，滚动范围才正确
    sb = md.list.verticalScrollBar()
    check(md.list.count() == 5, "5 本书都在列表里")
    # 高度改成「下限可滚动 / 上限贴条目」后，窗口没被撑高时列表落在区间内
    check(132 <= md.list.height() <= 284,
          "列表高度落在可滚动区间（%d）" % md.list.height())
    check(sb.maximum() > 0 or md.list.height() >= 284, "条目多于可视区时可滚动（max=%d）"
          % sb.maximum())

    # 小窗口不能把内容挤爆：列表不能顶到卡片外，卡片之间不能重叠
    md.books = [os.path.join(FIX, "calibre.epub")] * 6
    md._refresh_list(); app.processEvents()
    md.resize(940, 460); app.processEvents(); app.processEvents()
    from ui.widgets import Card as _Card
    _opt = next(c for c in md.findChildren(_Card)
                if c.title_label is not None and "合并选项" in c.title_label.text())
    check(md.height() >= 560, "窗口不能被压到内容装不下（实际 %d）" % md.height())
    _list_bottom = md.list_card.mapTo(md, md.list_card.rect().bottomLeft()).y()
    _opt_top = _opt.mapTo(md, _opt.rect().topLeft()).y()
    check(_list_bottom <= _opt_top,
          "待合并卡片没有压到合并选项（%d <= %d）" % (_list_bottom, _opt_top))
    check(md.list.geometry().bottom() <= md.list_card.rect().bottom(),
          "列表没有溢出自己的卡片（%d <= %d）"
          % (md.list.geometry().bottom(), md.list_card.rect().bottom()))
    _btn_add = next(b for b in md.list_card.findChildren(QPushButton)
                    if "添加" in b.text())
    check(_btn_add.geometry().top() > md.list.geometry().bottom(),
          "添加按钮没有被列表盖住（%d > %d）"
          % (_btn_add.geometry().top(), md.list.geometry().bottom()))
    sb.setValue(sb.maximum()); app.processEvents()
    check(md.list.visualItemRect(md.list.item(4)).bottom()
          <= md.list.viewport().height() + 1, "滚动到底后最后一行完整可见")
    check(bool([w for w in md.findChildren(type(md.summary))
                if w.objectName() == "BannerOk"]), "有书时出现检查通过横幅")
    check(not md._book_meta(os.path.join(FIX, "no-such.epub")).get("ok"),
          "读不了的 EPUB 会被判为不可合并")
    md.rejected = [("秘密之园.epub", "含 DRM 加密条目，不支持合并")]
    md._refresh_banners()
    _errs = [w.text() for w in md.findChildren(type(md.summary))
             if w.objectName() == "BannerError"]
    check(_errs and "已拒绝" in _errs[0], "被拒绝的书用红色横幅说明原因（%s）"
          % (_errs[0] if _errs else ""))
    # 选中行后可用性：首行不能再上移
    md.list.setCurrentRow(0); app.processEvents()
    check(not md.btn_up.isEnabled() and md.btn_rm.isEnabled(), "首行上移禁用、移除可用")
    md.rejected = []

    # 10) 报告对话框：真实自检结果
    rep = win.session.export(os.devnull, dry_run=True)
    rd = ReportDialog(win.session, {"export": rep, "path": "out.epub"})
    check(any(i["level"] == "ok" for i in rep["issues"]), "自检产生通过项")
    check(rd.conclusion.text().startswith("校验结论"), "报告结论已生成")
    # 顶部三大色块：绿(通过) / 黄(警告) / 红(错误)，数值取自真实自检结果
    from ui.widgets import CountBlock
    blocks = rd.findChildren(CountBlock)
    check(len(blocks) == 3, "顶部是三块大色块（%d）" % len(blocks))
    check([b.property("tone") for b in blocks] == ["ok", "warn", "error"],
          "色块顺序为 通过 / 警告 / 错误")
    want = {"ok": sum(1 for i in rep["issues"] if i["level"] == "ok"),
            "warn": sum(1 for i in rep["issues"] if i["level"] == "warn"),
            "error": sum(1 for i in rep["issues"] if i["level"] == "error")}
    check(all(b.value_label.text() == str(want[b.property("tone")]) for b in blocks),
          "数字与自检结果一致（%s）" % [(b.property("tone"), b.value_label.text()) for b in blocks])
    check(blocks[1].text_label.text().startswith("警告"), "警告块文案提示可继续导出")
    check(all(b.height() == 70 for b in blocks), "色块等高")

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

    # 10c) 卷首页修复：大标题与首章共用一页时，报告里能看见、一键能修
    swallow = CF.Session(os.path.join(FIX, "book2.epub"))
    plan_s = [p for p in swallow.repair_plan() if p["key"] == "swallow"]
    check(len(plan_s) == 1 and "吞掉第一章" in plan_s[0]["label"],
          "报告里说明「大标题与首章共用同一页」（%s）"
          % (plan_s[0]["label"] if plan_s else None))
    rd3 = ReportDialog(swallow, {}, repair_plan=plan_s)
    from PySide6.QtWidgets import QLabel as _QLabel
    check(any("大标题" in lb.text() for lb in rd3.findChildren(_QLabel)),
          "修复卡片里列出了这一项")
    logs = swallow.repair()
    check(any("卷标题页" in x for x in logs), "修复日志说明补了卷首页（%s）" % logs)
    check(not swallow.swallowed_groups(), "修复后不再有会被吞章的大标题")
    out_s = os.path.join(HERE, "shots", "gui_export_vol.epub")
    swallow.export(out_s)
    import zipfile as _zf
    _n = _zf.ZipFile(out_s).namelist()
    check(any("part" in x for x in _n), "落盘的书里多了卷首页")
    os.remove(out_s)

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
    # 1.2.10 起默认规则＝「中文标题通杀」（正则），开箱即可认出各种写法
    import re as _re2
    from ui import splitmem as _SM
    check(nb.rule_panel.rules().mode == "regex", "默认走「中文标题通杀」正则")
    check(nb.rule_panel.rules().pattern == _SM.UNIVERSAL_PATTERN, "默认正则就是通杀表达式")
    check(bool(_re2.compile(nb.rule_panel.rules().pattern)), "默认正则能编译")
    check("中文标题通杀" in nb.rule_panel.preset_combo.currentText(),
          "预设下拉默认停在「中文标题通杀」（%s）" % nb.rule_panel.preset_combo.currentText())
    nb2 = NewBookDialog(None, os.path.join(FIX, "sample.docx"))
    check(nb2.preview_list.count() == 2, "DOCX 预览识别 2 章")
    check(nb2.docx_row.isVisibleTo(nb2) and not nb2.rule_panel.isVisibleTo(nb2),
          "DOCX 模式改为按标题层级切分")
    # 11b) LLM 辅助切分：面板开关 + 设置对话框
    panel = nb.rule_panel
    # 面板里的操作全部图标化，文字只留悬停提示
    for _name, _btn in (("保存预设", panel.btn_save_preset), ("删除预设", panel.btn_del_preset),
                        ("LLM 设置", panel.btn_llm)):
        check(not _btn.text(), "%s 按钮不带文字" % _name)
        check(not _btn.icon().isNull(), "%s 按钮有图标" % _name)
        check(len(_btn.toolTip()) > 6, "%s 按钮的说明在 tooltip 里" % _name)
    check(sum(1 for b in panel.findChildren(QPushButton)
              if b.objectName() == "IconButton") >= 5,
          "面板至少 5 个图标按钮（保存/删除/两个导入/齿轮）")
    check(panel.cb_llm.isVisibleTo(nb), "分章面板有「LLM 辅助识别标题」开关")
    check(not panel.cb_llm.isChecked(), "默认不启用 LLM（不联网）")
    check(panel.llm_options() is None, "未勾选时 llm_options() 返回 None")
    panel.cb_llm.setChecked(True)
    app.processEvents()
    opt = panel.llm_options()
    check(opt is not None and opt.enabled, "勾选后返回已启用的配置")
    check(panel.llm_dot.isVisibleTo(panel) and panel.llm_dot.level() in ("ok", "error"),
          "勾选后状态灯点亮（绿=就绪 / 红=配置有问题，当前 %s）" % panel.llm_dot.level())
    check("配置有误" in panel.llm_dot.toolTip() or "就绪" in panel.llm_dot.toolTip(),
          "状态灯悬停有说明")
    panel.cb_llm.setChecked(False)
    app.processEvents()
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
    check(nd.tidy_box.isVisibleTo(nd), "扫描结果里出现「章节名可规范」提示条")
    check("第一章.测试标题" in nd.tidy_text.text() or "测试标题" in nd.tidy_text.text(),
          "提示条给出具体示例（%s）" % nd.tidy_text.text()[:40])
    check("不会" not in nd.tidy_text.text() and "可撤销" in nd.tidy_text.text(),
          "提示条说明了后果：只改名字、可撤销")
    check(nd.tidy_btn.text().startswith("规范"), "按钮文字带数量（%s）" % nd.tidy_btn.text())
    nd._apply_tidy()
    check(s_tidy.toc[0].title == "第一章 测试标题", "只规范章节名：分隔符换成空格")
    check(nd.tidied == 1, "返回规范处数")
    # 预览行逐行对齐：带「补」徽标的行也要和别的行同一条竖线
    from PySide6.QtWidgets import QLabel as _QL
    s_rows = CF.Session(os.path.join(FIX, "book3.epub"))
    s_rows.toc[0].children[0].title = "没有编号的章节"      # 制造一条「补号」
    nd_rows = NumberingDialog(s_rows)
    nd_rows.cb_add.setChecked(True); nd_rows._scan(); app.processEvents()
    xs = []
    for i in range(nd_rows.list.count()):
        w = nd_rows.list.itemWidget(nd_rows.list.item(i))
        labs = [c for c in w.findChildren(_QL)]
        xs.append((labs[2].geometry().x(), labs[3].geometry().x()))   # 箭头 x、右列 x
    check(len(set(xs)) == 1, "所有预览行的箭头与右列 x 一致（%s）" % xs)
    check(any(l.text() == "补" for i in range(nd_rows.list.count())
              for l in nd_rows.list.itemWidget(nd_rows.list.item(i)).findChildren(_QL)),
          "补号行仍显示「补」徽标")
    check(nd.rb_cont.isChecked() and not nd._per_group(), "默认整书连续编号（单选）")
    check(nd.rb_group.isEnabled(), "书里有大标题时「独立编号」可选")
    check(nd.scope_combo.count() == 2, "检查范围 = 全部大标题 + 1 个大标题")
    # 目录标题里混进整段正文时：不当作章节名来规范，只提示用户自己改名
    s_long = CF.Session(os.path.join(FIX, "book3.epub"))
    s_long.toc[0].children[0].title = ("第二章：绷带从脚腕开始缠。她在镜子前转着圈，"
                                      "一圈一圈将绷带缠在脚骨上，从脚趾下延伸到脚踝。")
    s_long.toc[0].children[1].title = "第三章.雪停之后"
    nd_long = NumberingDialog(s_long)
    check(not nd_long.cb_skip_odd.isChecked(), "「跳过疑似混入正文的标题」默认不勾选（不过滤）")
    check(nd_long._tidy_paths is None, "默认模式不改写规范范围（全部目录项）")
    nd_long.cb_skip_odd.setChecked(True); app.processEvents()
    _txt = nd_long.tidy_text.text()
    check("已跳过" in _txt and "疑似混入正文" in _txt,
          "超长目录标题被跳过并说明（%s）" % _txt[:40])
    check("雪停之后" in _txt and "脚腕" not in _txt.split("疑似")[0],
          "示例只取正常章节名，不展示正文片段")
    check(nd_long._tidy_paths == ["1.2"], "只把正常条目纳入规范范围（%s）" % nd_long._tidy_paths)
    nd_long._apply_tidy()
    check(s_long.toc[0].children[0].title.startswith("第二章：绷带"),
          "疑似正文的长标题保持原样，未被改写")
    check(s_long.toc[0].children[1].title == "第三章 雪停之后", "正常章节名被规范")

    # 无需规范时提示条收起
    s_clean = CF.Session(os.path.join(FIX, "book3.epub"))
    nd_clean = NumberingDialog(s_clean)
    check(not nd_clean.tidy_box.isVisibleTo(nd_clean), "没有可规范项时提示条隐藏")
    check(not nd_clean.tidy_btn.isEnabled(), "对应按钮也置灰")
    nd2 = NumberingDialog(s_tidy)
    nd2.scope_combo.setCurrentIndex(1)          # 只看第一个大标题
    QApplication.processEvents()
    check(nd2._scope() == "1", "选中大标题后范围变成该组")
    check("大标题" in nd2.summary.text() or "卷" in nd2.summary.text(),
          "扫描结果按所选大标题统计（%s）" % nd2.summary.text()[:40])

    # 11d2) 控件样式统一：数字输入框不能是 Fusion 的方角凹槽，图标按钮圆角与按钮一致
    from ui.theme import build_qss as _qss
    from ui import tokens as _tk
    _css = _qss()
    check("QSpinBox" in _css and "QDoubleSpinBox" in _css, "数字输入框有统一样式")
    _spin = _css.split("QSpinBox, QDoubleSpinBox {")[1].split("}")[0]
    check("border-radius: %dpx" % _tk.METRICS["btn_radius"] in _spin,
          "数字输入框圆角与按钮一致（%d）" % _tk.METRICS["btn_radius"])
    check("QSpinBox::up-arrow" in _css and "chevron-up.svg" in _css,
          "数字框上下箭头用同一套图标")
    # 次级按钮（取消 / 关闭）：要有灰底，不能是裸文字
    check("QPushButton#Secondary" in _css, "次级按钮样式存在")
    _sec = _css.split("QPushButton#Secondary {")[1].split("}")[0]
    check("background:" in _sec and "transparent" not in _sec,
          "次级按钮有底色（不是透明）")
    _nb2 = NewBookDialog(None, os.path.join(FIX, "sample_utf8.txt"))
    _cancel = next((b for b in _nb2.findChildren(QPushButton) if b.text() == "取消"), None)
    check(_cancel is not None and _cancel.objectName() == "Secondary",
          "对话框的「取消」用次级按钮样式（%s）"
          % (_cancel.objectName() if _cancel else "无"))

    _icon_btn = _css.split("QPushButton#IconButton {")[1].split("}")[0]
    check("border-radius: %dpx" % _tk.METRICS["btn_radius"] in _icon_btn,
          "图标按钮圆角与其它按钮一致（%d）" % _tk.METRICS["btn_radius"])

    # 11d3) 差几像素导致的「拉不动的滚动条」要被吃掉
    from PySide6.QtWidgets import (QDialog as _QD, QLabel as _QL, QPushButton as _QPB,
                                   QScrollArea as _QSA, QVBoxLayout as _QVB,
                                   QWidget as _QWidget)
    from ui.dialogs import absorb_tiny_scroll as _absorb, _scrollable
    _td = _QD()
    _v = _QVB(_td); _v.setContentsMargins(0, 0, 0, 0); _v.setSpacing(0)
    _v.addWidget(_QL("头部"))
    _tb = _QWidget()
    _tv = _QVB(_tb); _tv.setContentsMargins(0, 0, 0, 0)
    for _i in range(9):
        _tv.addWidget(_QL("第 %d 行内容" % _i))
    _v.addWidget(_scrollable(_tb), 1)
    _v.addWidget(_QPB("底部"))
    _td.resize(420, 200); _td.show(); app.processEvents(); app.processEvents()
    _tsc = _td.findChild(_QSA)
    _before_max = _tsc.verticalScrollBar().maximum()
    _td.resize(420, _td.height() - 3)          # 只差 3 像素
    app.processEvents(); app.processEvents()
    _absorb(_td); app.processEvents(); app.processEvents(); app.processEvents()
    check(_before_max > 0, "测试窗口本来需要滚动（%d）" % _before_max)
    check(_tsc.verticalScrollBar().maximum() == 0,
          "差 3 像素时滚动条被吃掉（max=%d）" % _tsc.verticalScrollBar().maximum())

    # 书籍信息：不允许出现「只差一点」的滚动条
    from ui.dialogs import BookInfoDialog as _BID
    _bi = _BID(CF.Session(os.path.join(FIX, "book3.epub")))
    _bi.show(); app.processEvents(); app.processEvents()
    _bisc = _bi.findChild(_QSA)
    _bimax = _bisc.verticalScrollBar().maximum() if _bisc is not None else 0
    check(_bimax == 0 or _bimax > 40, "书籍信息的滚动条要么没有、要么是真的要滚（max=%d）" % _bimax)

    # 11d4) 点「编辑」后布局不许有任何变化：换封面按钮是封面图的子控件（绝对定位）
    from ui.widgets import Card as _Card2
    from uuid import uuid4 as _uuid4
    _b2 = _BID(CF.Session(os.path.join(FIX, "book3.epub")))
    _b2.show(); app.processEvents(); app.processEvents()
    _sizes = lambda dlg: [(c.title_label.text() if c.title_label else "", c.size())
                          for c in dlg.findChildren(_Card2)]
    _read_sizes = _sizes(_b2)
    check(_b2.btn_pick_cover.isVisibleTo(_b2), "换封面按钮在只读态也常驻显示（占位稳定）")
    check(_b2.btn_pick_cover.parentWidget() is not _b2.cover_view,
          "换封面按钮不在封面图上，而是封面右侧的普通按钮")
    check(_b2.btn_pick_cover.text() == "更换封面"
          and _b2.btn_pick_cover.objectName() == "Secondary",
          "有封面时是灰色「更换封面」（%s / %s）"
          % (_b2.btn_pick_cover.text(), _b2.btn_pick_cover.objectName()))
    _b2._set_editing(True); app.processEvents(); app.processEvents()
    check(_sizes(_b2) == _read_sizes,
          "点「编辑」后卡片尺寸不变（%s → %s）" % (_read_sizes, _sizes(_b2)))
    check(_b2.btn_pick_cover.isVisibleTo(_b2), "编辑态同样显示换封面按钮")

    # 无封面时按钮变成「添加封面」并带一点主题色（提醒这本书缺封面）
    _b3 = _BID(CF.Session(os.path.join(FIX, "book2.epub")))
    _b3.show(); app.processEvents(); app.processEvents()
    check(_b3.btn_pick_cover.text() == "添加封面"
          and _b3.btn_pick_cover.objectName() == "TintAction",
          "无封面时是浅绿「添加封面」（%s / %s）"
          % (_b3.btn_pick_cover.text(), _b3.btn_pick_cover.objectName()))
    _no_cover_sizes = _sizes(_b3)
    _b3._set_editing(True); app.processEvents(); app.processEvents()
    check(_sizes(_b3) == _no_cover_sizes, "无封面的书点「编辑」后布局同样不变")

    # 11e) 章节特别多时：窗口不能撑出屏幕，长清单要截断，正文可滚动
    s_big = CF.Session(os.path.join(FIX, "book3.epub"))
    s_big.toc = [CF.Node("doc", "第%d章 标题%d" % (i, i), file="OEBPS/Text/ch01.xhtml")
                 for i in range(1, 240, 2)]      # 120 章，隔一个缺一个
    nd_big = NumberingDialog(s_big)
    nd_big.show(); app.processEvents(); app.processEvents()
    _scr = nd_big.screen().availableGeometry()
    check(nd_big.height() <= _scr.height(), "编号对话框不高于屏幕（%d ≤ %d）"
          % (nd_big.height(), _scr.height()))
    check(nd_big.width() <= _scr.width(), "编号对话框不宽于屏幕（%d ≤ %d）"
          % (nd_big.width(), _scr.width()))
    _issue = nd_big.issues.text()
    check(_issue.count("\n") + 1 <= 9, "缺号清单最多 9 行（%d 行）"
          % (_issue.count("\n") + 1))
    check("另有" in _issue, "超出部分给出「另有 N 条」说明")
    check(nd_big.list.count() == len(s_big.toc), "预览仍列出全部章节（%d）"
          % nd_big.list.count())
    from PySide6.QtWidgets import QScrollArea as _SA
    check(any(sa.widget() is nd_big._body for sa in nd_big.findChildren(_SA)),
          "编号对话框正文放在滚动区里")

    # 新建章节 / 校验报告同样不许超出屏幕
    from ui.dialogs import AddChapterDialog as _ACD
    _ad = _ACD(win.session); _ad.show(); app.processEvents()
    check(_ad.height() <= _scr.height() and _ad.width() <= _scr.width(),
          "新建章节对话框在屏幕内（%dx%d）" % (_ad.width(), _ad.height()))
    _rep = ReportDialog(win.session, {"export": win.session.export(os.devnull, dry_run=True)})
    _rep.show(); app.processEvents()
    check(_rep.height() <= _scr.height() and _rep.width() <= _scr.width(),
          "校验报告对话框在屏幕内（%dx%d）" % (_rep.width(), _rep.height()))

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

    # 11ac) 同一页面的编辑→保存流程（直接以编辑模式打开）
    sm = CF.Session(os.path.join(FIX, "book3.epub"))
    md_dlg = BookInfoDialog(sm, start_editing=True)
    check(md_dlg.editing, "start_editing=True 直接进编辑模式")
    check(md_dlg.fields["title"].text() == "测试之书", "对话框回填书名")
    check(md_dlg.fields["creators"].text() == "测试作者", "对话框回填作者")
    md_dlg.fields["title"].setText("元数据新书名")
    md_dlg.fields["subjects"].setText("科幻、文学")
    md_dlg.desc_edit.setPlainText("这是简介")
    md_dlg._save()
    check(sm.metadata()["title"] == "元数据新书名", "保存后书名写入会话")
    check(sm.metadata()["subjects"] == "科幻、文学", "标签写入会话")
    check(sm.metadata()["description"] == "这是简介", "简介写入会话")
    check(not md_dlg.editing and md_dlg.toggle_btn.text() == "编辑", "保存后回到只读")
    check(win.btn_book_info.isEnabled(), "「书籍信息」按钮可用")

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
    win.preview_view.set_session(sess2)   # 与 load() 的真实路径保持一致
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

    # 11h) 在阅读窗口里编辑正文
    win.open_reader(chapter); app.processEvents()
    ed = win._readers[-1]
    check(ed.btn_save.isVisibleTo(ed) is False, "阅读模式下不显示保存按钮")
    ed.start_edit(); app.processEvents()
    check(ed._editing, "点「编辑正文」进入编辑模式")
    check(ed.editor.isVisibleTo(ed), "编辑模式显示编辑框")
    check(not ed.view.isVisibleTo(ed), "编辑模式隐藏正文视图")
    check(ed.btn_save.isVisibleTo(ed) and ed.btn_cancel.isVisibleTo(ed), "编辑模式出现保存/取消")
    check(not ed.btn_prev.isEnabled(), "编辑时禁用上一章，避免串章")
    check("他推开门" in ed.editor.toPlainText(), "编辑框里是当前正文")
    check("<p" not in ed.editor.toPlainText(), "编辑框是纯文本（不带标签）")

    # 无改动保存：不产生撤销点
    depth = sess2.undo_depth
    ed.save_edit(); app.processEvents()
    check(not ed._editing, "无改动保存后退出编辑模式")
    check(sess2.undo_depth == depth, "没改动不新增撤销点")

    # 真改一次
    ed.start_edit(); app.processEvents()
    ed.editor.setPlainText(ed.editor.toPlainText().replace("他推开门", "她推开门"))
    ed.save_edit(); app.processEvents()
    check(sess2.undo_depth == depth + 1, "改动保存产生一个撤销点")
    check("她推开门" in sess2.new_files.get(chapter.file, b"").decode("utf-8"),
          "改动写入会话（导出即生效）")
    check("她推开门" in ed.view.toPlainText(), "保存后阅读器重新渲染新正文")
    check("她推开门" in win.preview_view.toPlainText(), "主界面左侧预览同步刷新")
    check(win.status_saved.text().find("未保存") >= 0 or sess2.dirty, "主界面标记为未保存")

    # 取消编辑不该影响正文
    ed.start_edit(); app.processEvents()
    ed.editor.setPlainText("这是一次不该被保存的修改")
    ed.cancel_edit(); app.processEvents()
    check(not ed._editing, "取消后回到阅读模式")
    check("不该被保存" not in sess2.new_files.get(chapter.file, b"").decode("utf-8"),
          "取消编辑不写入正文")

    # 保存失败时给出提示而不是崩（此处用空正文触发 ValueError）
    ed.start_edit(); app.processEvents()
    ed.editor.setPlainText("   ")
    orig_warn = QMessageBox.warning
    warned = {"n": 0}
    QMessageBox.warning = staticmethod(lambda *a, **k: warned.__setitem__("n", warned["n"] + 1))
    try:
        ed.save_edit(); app.processEvents()
    finally:
        QMessageBox.warning = orig_warn
    check(warned["n"] == 1, "空正文保存给出警告")
    check(ed._editing, "保存失败时留在编辑模式（不丢输入）")
    ed.cancel_edit(); app.processEvents()

    # 主界面撤销回到原文（阅读窗口的编辑走的是同一条撤销栈）
    sess2.undo()
    chapter = sess2.toc[0].children[0]     # 撤销会重建目录树，节点要重新取
    win._refresh_tree(chapter.uid)
    app.processEvents()
    check("他推开门" in win.preview_view.toPlainText(), "撤销后预览回到原文")
    check("她推开门" not in sess2.new_files.get(chapter.file, b"").decode("utf-8"),
          "撤销后会话里的改动被撤掉")
    ed.close(); app.processEvents()

    # 11g) 主界面左侧「预览」列
    win._select_node(sess2.toc[0])            # 大标题：没有正文
    app.processEvents()
    check(not win._preview_ready, "选中大标题时预览为空")
    check(win.preview_empty.isVisibleTo(win) or not win.preview_view.isVisibleTo(win),
          "空状态可见")
    check(not win.preview_words.isVisibleTo(win), "无正文时不显示字数气泡（空占位已隐藏）")
    win._select_node(chapter)                 # 章节：渲染正文
    app.processEvents()
    check(win._preview_ready, "选中章节后预览就绪")
    check("他推开门" in win.preview_view.toPlainText(), "预览里是这一章的正文")
    check(win.preview_words.isVisibleTo(win), "有正文时字数气泡出现")
    check(win.preview_words.text().endswith("字"), "预览标题栏显示字数（%s）"
          % win.preview_words.text())
    check(win.btn_preview_open.isEnabled(), "预览里的「阅读」按钮可用")
    check(win.preview_view.verticalScrollBar().value() == 0, "换章后回到顶部")
    # 预览列比原来宽：最小 300，初始占比不低于三分之一
    sizes = win.workspace_split.sizes()
    check(win.preview_card.minimumWidth() >= 300, "预览列最小宽度已调大（%d）"
          % win.preview_card.minimumWidth())
    check(sizes[0] >= 380, "预览列初始宽度更大（%s）" % sizes)

    # 11h) 分章规则：预设与本地记忆
    panel = win.rule_panel if hasattr(win, "rule_panel") else None
    from ui.widgets import SplitRulePanel
    panel = SplitRulePanel()
    check(panel.preset_combo.count() >= 7,
          "预设下拉：占位 + 内置写法（%d 项）" % panel.preset_combo.count())
    # 「中文标题通杀」预设：章/回/话/卷/序章/番外/数字编号/英文全部识别
    import re as _re
    _all = [panel.preset_combo.itemText(i) for i in range(panel.preset_combo.count())]
    _hit = next((i for i, t in enumerate(_all) if "中文标题通杀" in t), -1)
    check(_hit > 0, "内置预设里有「中文标题通杀」（%s）" % _all[:6])
    panel.preset_combo.setCurrentIndex(_hit); panel._apply_preset()
    _r = panel.rules()
    check(_r.mode == "regex" and _r.pattern, "通杀预设是正则模式且带表达式")
    check(_re.compile(_r.pattern), "通杀正则能编译")
    _sample = ("序章 起\n正文\n\n第一章 雪夜\n正文\n\n第十二回 风起\n正文\n\n"
               "第三话 相遇\n正文\n\n卷二 走向\n正文\n\n1、初见\n正文\n\n"
               "楔子\n正文\n\n番外 后记\n正文\n\nChapter 3\n正文\n\nPrologue\n正文\n\n"
               "这一章讲了什么？普通正文。\n")
    import txtconv as _TX
    _chs, _st = _TX.split_chapters(_sample, "book", para_mode="blank", rules=_r)
    check(len(_chs) == 10, "通杀预设切出 10 章（%d）" % len(_chs))
    check(all(not c.title.startswith("这一章") for c in _chs), "正文里提到「这一章」不会被误判")
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
    # 11d) 升级迁移：记忆里存的是旧版默认值 → 直接用新默认「通杀」；自己调过的保留
    SM.save_last(dict(SM.LEGACY_DEFAULT))
    check(SM.load_last() is None, "旧版默认值不再作为「上次设置」恢复")
    panel3 = SplitRulePanel()
    check(panel3._mode() == "regex" and panel3.regex_edit.text() == SM.UNIVERSAL_PATTERN,
          "升级后默认规则就是「中文标题通杀」，不用手动选")
    check("中文标题通杀" in panel3.preset_combo.currentText(),
          "预设下拉同步显示通杀（%s）" % panel3.preset_combo.currentText())
    SM.save_last(dict(SM.DEFAULT_PARAMS, unit="回"))
    check(SM.load_last() is not None, "自己调过的规则仍会被记住")
    panel4 = SplitRulePanel()
    check(panel4.unit_combo.currentText() == "回", "自调规则照旧恢复（%s）"
          % panel4.unit_combo.currentText())
    check(panel4.preset_combo.currentIndex() == 0, "对不上任何预设时下拉回到占位项")

    # 清理：删掉测试预设
    panel2._delete_preset("测试规则")
    panel2._autosave()
    check("测试规则" not in [it["name"] for it in SM.load_custom()],
          "删除预设生效")

    # 12) 空状态：未打开时不应显示伪数据
    win.session = None; win._update_empty_state(); app.processEvents()
    check(win.tree_empty.isVisible(), "未打开时显示空状态")
    check(win.btn_open.isEnabled(), "没有书时「打开」同样可点")
    check(win.btn_open.property("attention") == "true"
          and win.btn_open.property("secondary") == "false", "没有书时回到黄色提醒样式")
    check(not win.btn_book_info.isEnabled(), "未打开时「书籍信息」按钮置灰")
    check(not win.preview_words.isVisibleTo(win), "未打开时不显示字数气泡")

    print("\n通过 %d 项，失败 %d 项" % (_pass, len(_fail)))
    return 1 if _fail else 0

if __name__ == "__main__":
    sys.exit(main())
