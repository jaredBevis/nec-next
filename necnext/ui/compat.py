"""Qt binding compatibility (PySide6, PyQt6, PyQt5) via pyqtgraph's shim.

On Raspberry Pi OS the easiest install is the apt packages python3-pyqt5 +
python3-pyqtgraph; on Windows PySide6 from pip.  pyqtgraph picks whichever
binding is importable, and this module papers over the remaining differences.
"""
import os

# Prefer PySide6 when several bindings are installed (it can be forced with
# PYQTGRAPH_QT_LIB=PyQt5 etc.)
if "PYQTGRAPH_QT_LIB" not in os.environ:
    for _lib in ("PySide6", "PyQt6", "PyQt5"):
        try:
            __import__(_lib)
            os.environ["PYQTGRAPH_QT_LIB"] = _lib
            break
        except ImportError:
            continue

import pyqtgraph as pg  # noqa: E402
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets  # noqa: E402

QT_LIB = pg.Qt.QT_LIB
Signal = QtCore.Signal
Qt = QtCore.Qt

QAction = getattr(QtGui, "QAction", None) or QtWidgets.QAction
QShortcut = getattr(QtGui, "QShortcut", None) or QtWidgets.QShortcut
QActionGroup = getattr(QtGui, "QActionGroup", None) or QtWidgets.QActionGroup


def exec_(obj, *a):
    fn = getattr(obj, "exec", None) or getattr(obj, "exec_")
    return fn(*a)


def event_pos(ev):
    """Mouse event position as QPointF for Qt5 and Qt6."""
    if hasattr(ev, "position"):
        return ev.position()
    return QtCore.QPointF(ev.pos())


class FlowLayout(QtWidgets.QLayout):
    """Lays widgets out left to right and wraps onto new rows when narrow."""

    def __init__(self, parent=None, margin=0, hspacing=4, vspacing=4):
        super().__init__(parent)
        self._items = []
        self._h, self._v = hspacing, vspacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return QtCore.Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do(QtCore.QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QtCore.QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QtCore.QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do(self, rect, test):
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = r.x(), r.y(), 0
        for it in self._items:
            sz = it.sizeHint()
            nx = x + sz.width() + self._h
            if nx - self._h > r.right() + 1 and line_h > 0:
                x = r.x()
                y += line_h + self._v
                nx = x + sz.width() + self._h
                line_h = 0
            if not test:
                it.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), sz))
            x = nx
            line_h = max(line_h, sz.height())
        return y + line_h - rect.y() + m.bottom()
