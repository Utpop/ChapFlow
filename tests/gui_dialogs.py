# -*- coding: utf-8 -*-
"""渲染三个对话框，核对与 PPT 第 2/3/4 页的对应关系（开发辅助）"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
import tempfile
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

# 截图脚本用独立配置目录：不读写用户真实的分章规则记忆
_CFG = tempfile.mkdtemp(prefix="chapflow-shot-cfg-")
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _CFG)
from PySide6.QtGui import QPixmap
from ui.theme import build_qss
from ui.fonts import apply_app_font
from ui.dialogs import (BookInfoDialog, ImportDialog, LLMSettingsDialog, MergeDialog,
                        NewBookDialog, NumberingDialog, ReportDialog)
from ui.reader import ReaderWindow
import chapflow as CF

app = QApplication([]); app.setStyle("Fusion"); apply_app_font(app); app.setStyleSheet(build_qss())
sess = CF.Session(os.path.join(HERE, "fixtures", "book3.epub"))

def shot(w, name, size=None):
    if size: w.resize(*size)
    w.show(); app.processEvents(); app.processEvents()
    if size:                     # 有的对话框 showEvent 会自适应尺寸，截图按指定尺寸再压一次
        w.resize(*size); app.processEvents()
    pm = QPixmap(w.size()); w.render(pm)
    pm.save(os.path.join(HERE, "shots", "dlg_%s.png" % name))
    print("saved", name, w.size().width(), w.size().height())

d1 = ImportDialog(sess, os.path.join(HERE, "fixtures", "sample_utf8.txt"), "txt")
shot(d1, "import", (920, 830))

d2 = MergeDialog(sess)
d2.books = [os.path.join(HERE, "fixtures", "book2.epub"), os.path.join(HERE, "fixtures", "calibre.epub")]
d2._refresh_list()
shot(d2, "merge", (1020, 618))

rep = sess.export(os.devnull, dry_run=True)
d3 = ReportDialog(sess, {"export": rep})
shot(d3, "report", (860, 760))

# 脏书：报告里带"可自动修复"
dirty = CF.Session(os.path.join(HERE, "fixtures", "dirty.epub"))
plan = [p for p in dirty.repair_plan() if p["key"] in ("dead", "empty")]
rep_bad = dirty.export(os.devnull, dry_run=True, prune_broken=False)
d4 = ReportDialog(dirty, {"export": rep_bad}, repair_plan=plan)
shot(d4, "report_fix", (860, 760))
# EPUB2 报告（含升级入口）
sess2 = CF.Session(os.path.join(HERE, "fixtures", "book2.epub"))
rep2 = sess2.export(os.devnull, dry_run=True)
d5 = ReportDialog(sess2, {"export": rep2}, upgrade_plan=sess2.upgrade_plan())
shot(d5, "report_upgrade", (860, 780))

# 书籍信息（只读）与同一页面的编辑模式
sm = CF.Session(os.path.join(HERE, "fixtures", "book3.epub"))
_bi = BookInfoDialog(sm, extras={"path": sm.book.path}, start_editing=False)
shot(_bi, "book_info", (900, 940))
_ei = BookInfoDialog(sm, extras={"path": sm.book.path}, start_editing=True)
_ei.fields["subjects"].setText("科幻、文学")
_ei.fields["series"].setText("三体")
_ei.desc_edit.setPlainText("这是一段简介，用于演示同一页面的编辑模式。")
shot(_ei, "book_info_edit", (900, 940))

# 章节编号
sess_n = CF.Session(os.path.join(HERE, "fixtures", "book3.epub"))
sess_n.delete("1.2", physical=False)
sess_n.toc[0].children.insert(1, CF.Node("doc", "雾中独行", "OEBPS/Text/ch02.xhtml", "", ""))
sess_n.toc[0].title = "第一卷.试炼"          # 演示：卷标题里的分隔符 + 「只规范章节名」可用
nd = NumberingDialog(sess_n)
shot(nd, "numbering", (900, 740))

# 新建书
nb = NewBookDialog(None, os.path.join(HERE, "fixtures", "sample_utf8.txt"))
shot(nb, "newbook", (880, 830))

nz = NewBookDialog(None, os.path.join(HERE, "fixtures", "sample_utf8.txt"))
nz.show(); app.processEvents(); nz.toggle_zoom(); app.processEvents()
shot(nz, "newbook_zoom")

llmd = LLMSettingsDialog(None)
shot(llmd, "llm_settings", (560, 640))

# 添加章节（直接输入正文）
from ui.dialogs import AddChapterDialog
acy = AddChapterDialog(sess, selected_path="1.1")
acy.title_edit.setText("番外 归途之后")
acy.text_edit.setPlainText(
    "他推开门，院子里的雪已经停了。\n\n"
    "远处传来一声钟响，像是替这一年收了尾。\n\n"
    "“走吧。”她说。")
shot(acy, "add_chapter", (900, 740))

# 阅读器
r = ReaderWindow(sess, sess.toc[0].children[0])
shot(r, "reader", (860, 780))
# 阅读器：编辑正文模式
r.start_edit()
shot(r, "reader_edit", (860, 780))
r.cancel_edit()
print("done")
