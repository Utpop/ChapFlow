# -*- coding: utf-8 -*-
"""离屏渲染截图，用于核对视觉效果（开发辅助，不参与发布）"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from ui.theme import build_qss
from ui.fonts import apply_app_font
from PySide6.QtGui import QPixmap
from ui.main_window import MainWindow
import chapflow as CF

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "shots", "main.png")
    app = QApplication([])
    app.setStyle("Fusion")
    apply_app_font(app)
    app.setStyleSheet(build_qss())

    win = MainWindow()
    import os as _os
    size = _os.environ.get("SHOT_SIZE", "1500x980")
    w_, h_ = (int(x) for x in size.lower().split("x"))
    win.resize(w_, h_)

    if os.environ.get("SHOT_EMPTY"):
        win.file_label.setText("未打开文件")
        win._update_empty_state()
        win._update_actions()
        win.show(); app.processEvents(); app.processEvents()
        pm = QPixmap(win.size()); win.render(pm); pm.save(out)
        print("saved", out, pm.width(), pm.height())
        return 0

    # 造一份内容更接近设计稿的会话：主书 + 若干章节/大标题
    sess = CF.Session(os.path.join(HERE, "fixtures", "book3.epub"))
    sess.add_group("第二部  嵌套结构", "end")
    for i in range(3):
        sess.append_txt(os.path.join(HERE, "fixtures", "sample_gbk.txt"),
                        group_title="番外", at="end")
    win.session = sess
    win.path = sess.book.path
    win.model.set_session(sess)
    win._apply_expansion(set(), expand_all=True)
    win.file_label.setText(os.path.basename(sess.book.path))
    win._fill_book_info()
    win.tree.setFocus()
    win._refresh_counts()
    win._update_empty_state()

    idx = win.proxy.index(0, 0)
    win.tree.setCurrentIndex(idx)
    win._log("打开", "三体（全本）.epub（EPUB3，nav+ncx 双目录）", "info")
    win._log("重命名", "「第一章 科学罗辑」→「第一章 科学边界」", "info")

    win.show()
    app.processEvents()
    app.processEvents()
    pm = QPixmap(win.size())
    win.render(pm)
    pm.save(out)
    print("saved", out, pm.width(), pm.height())
    return 0

if __name__ == "__main__":
    sys.exit(main())
