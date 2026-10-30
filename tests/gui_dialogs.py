# -*- coding: utf-8 -*-
"""渲染三个对话框，核对与 PPT 第 2/3/4 页的对应关系（开发辅助）"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap
from ui.theme import build_qss
from ui.fonts import apply_app_font
from ui.dialogs import ImportDialog, MergeDialog, NewBookDialog, ReportDialog
from ui.reader import ReaderWindow
import chapflow as CF

app = QApplication([]); app.setStyle("Fusion"); apply_app_font(app); app.setStyleSheet(build_qss())
sess = CF.Session(os.path.join(HERE, "fixtures", "book3.epub"))

def shot(w, name, size=None):
    if size: w.resize(*size)
    w.show(); app.processEvents(); app.processEvents()
    pm = QPixmap(w.size()); w.render(pm)
    pm.save(os.path.join(HERE, "shots", "dlg_%s.png" % name))
    print("saved", name, w.size().width(), w.size().height())

d1 = ImportDialog(sess, os.path.join(HERE, "fixtures", "sample_utf8.txt"), "txt")
shot(d1, "import", (900, 760))

d2 = MergeDialog(sess)
d2.books = [os.path.join(HERE, "fixtures", "book2.epub"), os.path.join(HERE, "fixtures", "calibre.epub")]
d2._refresh_list()
shot(d2, "merge", (940, 740))

rep = sess.export(os.devnull, dry_run=True)
d3 = ReportDialog(sess, {"export": rep})
shot(d3, "report", (860, 760))

# 脏书：报告里带"可自动修复"
dirty = CF.Session(os.path.join(HERE, "fixtures", "dirty.epub"))
plan = [p for p in dirty.repair_plan() if p["key"] in ("dead", "empty")]
rep_bad = dirty.export(os.devnull, dry_run=True, prune_broken=False)
d4 = ReportDialog(dirty, {"export": rep_bad}, repair_plan=plan)
shot(d4, "report_fix", (860, 760))
# 新建书
nb = NewBookDialog(None, os.path.join(HERE, "fixtures", "sample_utf8.txt"))
shot(nb, "newbook", (860, 700))

# 阅读器
r = ReaderWindow(sess, sess.toc[0].children[0])
shot(r, "reader", (860, 780))
print("done")
