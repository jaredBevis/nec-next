"""Help ▸ User guide: shows README.md inside the app, with a contents list and search."""
from __future__ import annotations

import os
import re

from . import theme
from .compat import Qt, QtCore, QtGui, QtWidgets, QShortcut

APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
README = os.path.join(APP_DIR, "README.md")

CSS = f"""
body {{ color: {theme.TEXT}; }}
h1 {{ color: {theme.ACCENT}; }}
h2 {{ color: {theme.ACCENT}; }}
h3 {{ color: #e2e8f0; }}
code {{ color: #fbbf24; }}
pre {{ background: #0b0f14; color: #e2e8f0; }}
th {{ background: #1e2733; }}
a {{ color: {theme.ACCENT}; }}
"""


class HelpDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, path: str = README):
        super().__init__(parent)
        self.setWindowTitle("NEC Next user guide")
        self.resize(1100, 800)
        scr = (parent.screen() if parent is not None and hasattr(parent, "screen") else None) \
            or QtWidgets.QApplication.primaryScreen()
        if scr is not None:
            av = scr.availableGeometry()
            self.resize(min(1100, av.width() - 40), min(800, av.height() - 60))
        lay = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search the guide (Enter for next match)")
        self.search.returnPressed.connect(self.find_next)
        top.addWidget(self.search, 1)
        b = QtWidgets.QPushButton("Find next")
        b.clicked.connect(self.find_next)
        top.addWidget(b)
        lay.addLayout(top)
        split = QtWidgets.QSplitter(Qt.Orientation.Horizontal)
        self.toc = QtWidgets.QListWidget()
        self.toc.setMinimumWidth(150)
        self.view = QtWidgets.QTextBrowser()
        self.view.setOpenExternalLinks(True)
        split.addWidget(self.toc)
        split.addWidget(self.view)
        split.setStretchFactor(1, 1)
        split.setSizes([230, 850])
        lay.addWidget(split, 1)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)
        self._load(path)
        self.toc.currentRowChanged.connect(self._jump)
        fs = QShortcut(QtGui.QKeySequence("Ctrl+F"), self)
        fs.activated.connect(lambda: self.search.setFocus())

    def _load(self, path):
        try:
            with open(path, encoding="utf-8") as f:
                md = f.read()
        except OSError:
            self.view.setPlainText(f"The user guide (README.md) was not found next to the app:\n{path}")
            return
        md = md.replace("\r\n", "\n")
        # convert the Markdown once to HTML so the theme's style sheet applies
        tmp = QtGui.QTextDocument()
        tmp.setMarkdown(md)
        html = tmp.toHtml()
        n = [0]

        def img(m):
            n[0] += 1
            w = 640 if n[0] == 1 else 240      # the hero shot, then the small gallery
            return f'<img src="{m.group(1)}" width="{w}" />'
        html = re.sub(r'<img src="([^"]+)"[^>]*/?>', img, html)
        doc = QtGui.QTextDocument(self)
        doc.setDefaultStyleSheet(CSS)
        doc.setBaseUrl(QtCore.QUrl.fromLocalFile(os.path.dirname(path) + os.sep))
        self.view.setDocument(doc)
        self.view.setSearchPaths([os.path.dirname(path)])
        doc.setHtml(html)
        self.view.moveCursor(QtGui.QTextCursor.MoveOperation.Start)
        self.view.verticalScrollBar().setValue(0)
        # table of contents from level 1-3 headings
        self._heads = []
        blk = doc.begin()
        while blk.isValid():
            lvl = blk.blockFormat().headingLevel()
            if lvl in (2, 3) and blk.text().strip():
                self._heads.append(blk.position())
                item = QtWidgets.QListWidgetItem(("    " if lvl == 3 else "") + blk.text().strip())
                if lvl == 2:
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                self.toc.addItem(item)
            blk = blk.next()

    def _jump(self, row):
        if 0 <= row < len(self._heads):
            c = QtGui.QTextCursor(self.view.document())
            c.setPosition(self._heads[row])
            self.view.setTextCursor(c)
            sb = self.view.verticalScrollBar()
            sb.setValue(sb.maximum())          # put the heading at the top of the view
            self.view.ensureCursorVisible()

    def find_next(self):
        t = self.search.text().strip()
        if not t:
            return
        if not self.view.find(t):
            c = self.view.textCursor()
            c.movePosition(QtGui.QTextCursor.MoveOperation.Start)
            self.view.setTextCursor(c)
            self.view.find(t)
