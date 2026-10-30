# -*- coding: utf-8 -*-
"""按控件截图，便于 1:1 检查局部视觉（开发辅助）"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap
from ui.theme import build_qss
from ui.fonts import apply_app_font
from ui.main_window import MainWindow
import chapflow as CF

app = QApplication([]); app.setStyle("Fusion"); apply_app_font(app); app.setStyleSheet(build_qss())
win = MainWindow(); win.resize(1500, 980)
sess = CF.Session(os.path.join(HERE, "fixtures", "book3.epub"))
sess.add_group("第二部  嵌套结构", "end")
sess.append_txt(os.path.join(HERE, "fixtures", "sample_gbk.txt"), group_title="番外", at="end")
win.session = sess; win.path = sess.book.path
win.model.set_session(sess); win._apply_expansion(set(), expand_all=True)
win._fill_book_info(); win._refresh_counts(); win._update_empty_state()
win.tree.setCurrentIndex(win.proxy.index(0, 0))
win._log("打开", "三体（全本）.epub（EPUB3，nav、ncx 双目录）", "info")
win._log("重命名", "「第一章 科学罗辑」→「第一章 科学边界」", "info")
win.show(); app.processEvents(); app.processEvents()

for name, w in (("detail", win.detail), ("log", win.log_list), ("tree", win.tree),
                ("info", win.st_manifest.parentWidget()), ("status", win.statusBar()),
                ("toolbar", win.centralWidget().layout().itemAt(1).widget())):
    if w is None: continue
    pm = QPixmap(w.size()); w.render(pm); pm.save(os.path.join(HERE, "shots", "part_%s.png" % name))
    print(name, w.size().width(), w.size().height())
print("done")
