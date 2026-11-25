"""NEC deck text editor (line numbers + highlighting), wire table and variable tuner."""
from __future__ import annotations

import math
import re
from typing import List, Optional

from ..deck import CARD_HELP, Deck, split_fields, split_unit
from . import theme
from .compat import Qt, QtCore, QtGui, QtWidgets, Signal


# ------------------------------------------------------------------ highlight
class NecHighlighter(QtGui.QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)

        def fmt(color, bold=False, italic=False):
            f = QtGui.QTextCharFormat()
            f.setForeground(QtGui.QColor(color))
            if bold:
                f.setFontWeight(QtGui.QFont.Weight.Bold)
            f.setFontItalic(italic)
            return f
        self.f_comment = fmt("#6b7a8f", italic=True)
        self.f_geom = fmt(theme.ACCENT, bold=True)
        self.f_ctrl = fmt(theme.ACCENT2, bold=True)
        self.f_sy = fmt(theme.WARN, bold=True)
        self.f_num = fmt("#e6edf3")
        self.f_expr = fmt("#fcd34d")
        self.f_end = fmt(theme.BAD, bold=True)
        self.num_re = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")

    def highlightBlock(self, text):
        s = text.lstrip()
        off = len(text) - len(s)
        k = s[:2].upper()
        if k in ("CM", "CE") or s.startswith(("'", "!", "#")):
            self.setFormat(0, len(text), self.f_comment)
            return
        if k == "SY":
            self.setFormat(off, 2, self.f_sy)
            return
        if k == "EN":
            self.setFormat(off, 2, self.f_end)
            return
        if k.startswith("G") or k in ("SP", "SM", "SC"):
            self.setFormat(off, 2, self.f_geom)
        elif k:
            self.setFormat(off, 2, self.f_ctrl)
        for m in re.finditer(r"[^\s,]+", text[off + 2:]):
            tok = m.group(0)
            if tok.startswith(("'", "!")):
                self.setFormat(off + 2 + m.start(), len(text), self.f_comment)
                break
            f = self.f_num if self.num_re.match(tok) else self.f_expr
            self.setFormat(off + 2 + m.start(), len(tok), f)


# ------------------------------------------------------------------ editor
class _LineArea(QtWidgets.QWidget):
    def __init__(self, ed):
        super().__init__(ed)
        self.ed = ed

    def sizeHint(self):
        return QtCore.QSize(self.ed.line_area_width(), 0)

    def paintEvent(self, ev):
        self.ed.paint_lines(ev)


class DeckEditor(QtWidgets.QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        f = QtGui.QFont()
        f.setFamilies(["Cascadia Mono", "JetBrains Mono", "Consolas", "DejaVu Sans Mono", "Monospace"]) \
            if hasattr(f, "setFamilies") else f.setFamily("Monospace")
        f.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        f.setPointSizeF(10.5)
        self.setFont(f)
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        self.hl = NecHighlighter(self.document())
        self.area = _LineArea(self)
        self.error_lines: List[int] = []
        self.blockCountChanged.connect(lambda _: self._update_margin())
        self.updateRequest.connect(self._on_update)
        self.cursorPositionChanged.connect(self._highlight)
        self._update_margin()
        self.setMouseTracking(True)

    def line_area_width(self):
        d = len(str(max(1, self.blockCount())))
        return 16 + self.fontMetrics().horizontalAdvance("9") * max(3, d)

    def _update_margin(self):
        self.setViewportMargins(self.line_area_width(), 0, 0, 0)

    def _on_update(self, rect, dy):
        if dy:
            self.area.scroll(0, dy)
        else:
            self.area.update(0, rect.y(), self.area.width(), rect.height())

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        cr = self.contentsRect()
        self.area.setGeometry(QtCore.QRect(cr.left(), cr.top(), self.line_area_width(), cr.height()))

    def paint_lines(self, ev):
        p = QtGui.QPainter(self.area)
        p.fillRect(ev.rect(), QtGui.QColor(theme.BG))
        block = self.firstVisibleBlock()
        num = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        cur = self.textCursor().blockNumber()
        while block.isValid() and top <= ev.rect().bottom():
            if block.isVisible() and bottom >= ev.rect().top():
                if num in self.error_lines:
                    p.fillRect(0, top, 4, bottom - top, QtGui.QColor(theme.BAD))
                p.setPen(QtGui.QColor(theme.TEXT if num == cur else "#4b5563"))
                p.drawText(0, top, self.area.width() - 8, self.fontMetrics().height(),
                           Qt.AlignmentFlag.AlignRight, str(num + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            num += 1

    def set_errors(self, lines: List[int]):
        self.error_lines = lines
        self._highlight()
        self.area.update()

    def _highlight(self):
        sels = []
        s = QtWidgets.QTextEdit.ExtraSelection()
        s.format.setBackground(QtGui.QColor("#161d27"))
        s.format.setProperty(QtGui.QTextFormat.Property.FullWidthSelection, True)
        s.cursor = self.textCursor()
        s.cursor.clearSelection()
        sels.append(s)
        for ln in self.error_lines:
            blk = self.document().findBlockByNumber(ln)
            if not blk.isValid():
                continue
            e = QtWidgets.QTextEdit.ExtraSelection()
            e.format.setUnderlineStyle(QtGui.QTextCharFormat.UnderlineStyle.WaveUnderline)
            e.format.setUnderlineColor(QtGui.QColor(theme.BAD))
            e.cursor = QtGui.QTextCursor(blk)
            e.cursor.movePosition(QtGui.QTextCursor.MoveOperation.EndOfBlock,
                                  QtGui.QTextCursor.MoveMode.KeepAnchor)
            sels.append(e)
        self.setExtraSelections(sels)
        self.area.update()

    def event(self, ev):
        if ev.type() == QtCore.QEvent.Type.ToolTip:
            pos = ev.pos()
            cur = self.cursorForPosition(pos)
            txt = cur.block().text().strip()[:2].upper()
            if txt in CARD_HELP:
                QtWidgets.QToolTip.showText(ev.globalPos(), f"<b>{txt}</b> — {CARD_HELP[txt]}", self)
            else:
                QtWidgets.QToolTip.hideText()
            return True
        return super().event(ev)

    def replace_line(self, line: int, text: str):
        blk = self.document().findBlockByNumber(line)
        if not blk.isValid():
            return
        c = QtGui.QTextCursor(blk)
        c.movePosition(QtGui.QTextCursor.MoveOperation.EndOfBlock, QtGui.QTextCursor.MoveMode.KeepAnchor)
        c.insertText(text)


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


# ------------------------------------------------------------------ variables
class VariablePanel(QtWidgets.QWidget):
    """Digit-stepping number boxes for SY variables; add and delete variables."""
    variableChanged = Signal(str, str)
    addVariable = Signal(str, str)
    deleteVariable = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        bar = QtWidgets.QHBoxLayout()
        bar.setContentsMargins(6, 6, 6, 4)
        add = QtWidgets.QPushButton("+ Variable")
        add.setObjectName("primary")
        add.clicked.connect(self._add)
        bar.addWidget(add)
        bar.addStretch(1)
        outer.addLayout(bar)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        outer.addWidget(self.scroll, 1)
        self.inner = QtWidgets.QWidget()
        self.lay = QtWidgets.QVBoxLayout(self.inner)
        self.lay.setContentsMargins(10, 6, 10, 10)
        self.lay.setSpacing(10)
        self.scroll.setWidget(self.inner)
        self._sig = None
        self._deck: Optional[Deck] = None
        self.usage_fn = None     # name -> list of places the variable is used

    def _usage(self, name):
        if self.usage_fn is None:
            return []
        try:
            return self.usage_fn(name) or []
        except Exception:  # noqa: BLE001 - never block the panel on a lookup problem
            return []

    def _add(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("New variable")
        f = QtWidgets.QFormLayout(dlg)
        name = QtWidgets.QLineEdit()
        name.setPlaceholderText("e.g. h, len, coil_L")
        val = QtWidgets.QLineEdit("1")
        val.setPlaceholderText("number or expression: 10.2, 12.7mm, 4.7uH, len/2")
        f.addRow("Name", name)
        f.addRow("Value", val)
        err = QtWidgets.QLabel("")
        err.setObjectName("error")
        f.addRow(err)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                        QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        f.addRow(bb)
        bb.rejected.connect(dlg.reject)

        def ok():
            n, v = name.text().strip(), val.text().strip()
            from ..deck import evaluate
            if not re.match(r"^[A-Za-z_][A-Za-z_0-9]*$", n):
                err.setText("Use letters, digits and _ ; start with a letter.")
                return
            if self._deck and n in self._deck.symbols:
                err.setText(f"'{n}' already exists.")
                return
            try:
                evaluate(v, self._deck.symbols if self._deck else {})
            except Exception as e:
                err.setText(str(e))
                return
            dlg.accept()
            self.addVariable.emit(n, v)
        bb.accepted.connect(ok)
        dlg.exec() if hasattr(dlg, "exec") else dlg.exec_()

    def load(self, deck: Deck):
        self._deck = deck
        sig = [(v.name, split_unit(v.expr) is not None, "" if split_unit(v.expr) else v.expr,
                (split_unit(v.expr) or (0, ""))[1]) for v in deck.variables]
        if sig == self._sig:
            for w in self.inner.findChildren(_VarRow):
                v = deck.symbols.get(w.name)
                su = next((split_unit(x.expr) for x in deck.variables if x.name == w.name), None)
                if su:
                    v = su[0]
                w.set_usage(self._usage(w.name))
                if v is not None:
                    if w.spin is not None and not w.spin.hasFocus():
                        w.set_value(v)
                    elif w.spin is None:
                        w.set_value(v)
            return
        self._sig = sig
        while self.lay.count():
            it = self.lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if not deck.variables:
            lbl = QtWidgets.QLabel("No variables yet. Click <b>+ Variable</b> to create one (for example "
                                   "<code>h = 10</code>), then use its name in any field, such as a wire "
                                   "height or a coil value. Number variables get a box whose arrows step the digit you click.")
            lbl.setObjectName("muted")
            lbl.setWordWrap(True)
            self.lay.addWidget(lbl)
        for v in deck.variables:
            su = split_unit(v.expr)
            row = _VarRow(v.name, su[0] if su else v.value, None if su else v.expr, su[1] if su else "")
            row.changed.connect(self.variableChanged)
            row.deleted.connect(self.deleteVariable)
            row.set_usage(self._usage(v.name))
            self.lay.addWidget(row)
        self.lay.addStretch(1)


class DigitSpinBox(QtWidgets.QDoubleSpinBox):
    """Number box whose arrows step the digit under the cursor.

    Click (or select) a digit, then use the arrows, the Up/Down keys or the
    mouse wheel: the ones place steps by 1, the tenths by 0.1, and so on.
    The active digit stays highlighted.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._exp = 0          # power of ten of the active digit
        self.setKeyboardTracking(False)
        self.lineEdit().cursorPositionChanged.connect(self._cursor_moved)
        self.lineEdit().selectionChanged.connect(self._cursor_moved)

    def _number_span(self, text):
        return len(self.prefix()), len(text) - len(self.suffix())

    def _dp(self, text, a, b):
        dp_char = self.locale().decimalPoint()
        dp_char = dp_char if isinstance(dp_char, str) and dp_char else "."
        k = text.find(dp_char, a, b)
        return k if k >= 0 else b

    def _cursor_moved(self, *_):
        le = self.lineEdit()
        text = le.text()
        a, b = self._number_span(text)
        if le.hasSelectedText():
            pos = le.selectionStart()
        else:
            # the digit just left of the cursor; right after the point, the first decimal
            pos = le.cursorPosition() - 1
            if pos < a or (0 <= pos < len(text) and not text[pos].isdigit()):
                pos += 1
        if not (a <= pos < b) or not text[pos].isdigit():
            return
        dp = self._dp(text, a, b)
        self._exp = dp - pos - 1 if pos < dp else -(pos - dp)

    def stepBy(self, steps):
        exp = max(self._exp, -self.decimals())
        self.setValue(self.value() + steps * (10.0 ** exp))
        self._select_digit(exp)

    def _select_digit(self, exp):
        le = self.lineEdit()
        text = le.text()
        a, b = self._number_span(text)
        dp = self._dp(text, a, b)
        idx = dp - exp - 1 if exp >= 0 else dp - exp
        idx = max(a, min(idx, b - 1))
        while idx < b and not text[idx].isdigit():
            idx += 1
        if idx < b:
            le.blockSignals(True)
            le.setSelection(idx, 1)
            le.blockSignals(False)

    def shift_digit(self, d: int):
        """Move the active digit left (d=-1, bigger place) or right (d=+1)."""
        text = self.lineEdit().text()
        a, b = self._number_span(text)
        dp = self._dp(text, a, b)
        n_int = sum(1 for c in text[a:dp] if c.isdigit())
        exp = max(-self.decimals(), min(n_int - 1, self._exp - d))
        self.setFocus()
        self._exp = exp
        self._select_digit(self._exp)

    def step_active(self, steps: int):
        exp = self._exp
        self.setFocus()
        self._exp = exp
        self.stepBy(steps)

    def textFromValue(self, v):
        return f"{v:.{self.decimals()}f}"


class _VarRow(QtWidgets.QFrame):
    changed = Signal(str, str)
    deleted = Signal(str)

    def __init__(self, name: str, value: float, expr: Optional[str] = None, unit: str = ""):
        super().__init__()
        self.setObjectName("tile")
        self.name = name
        g = QtWidgets.QGridLayout(self)
        g.setContentsMargins(10, 6, 10, 6)
        lbl = QtWidgets.QLabel(name)
        lbl.setStyleSheet("font-weight:600")
        lbl.setMinimumWidth(36)
        rm = QtWidgets.QToolButton()
        rm.setText("✕")
        rm.setToolTip(f"Delete variable {name}")
        rm.clicked.connect(self._delete_clicked)
        self.rm = rm
        self.usage = []
        g.addWidget(lbl, 0, 0)
        g.addWidget(rm, 0, 2)
        self._busy = False
        self.unit = unit
        if expr is not None:
            # computed from other variables: show the formula and its value
            self.spin = None
            self.val = QtWidgets.QLabel()
            self.val.setObjectName("muted")
            self.val.setToolTip("Computed from an expression of other variables")
            self.expr = expr
            g.addWidget(self.val, 0, 1)
            self.set_value(value)
            return
        self.spin = DigitSpinBox()
        dec = 3
        if value and abs(value) < 0.01:
            # keep small unit-less values (e.g. 0.0005 m) visible
            dec = min(8, 2 + int(math.ceil(-math.log10(abs(value)))))
        self.spin.setDecimals(dec)
        self.spin.setRange(-1e6, 1e6)
        if unit:
            self.spin.setSuffix(" " + unit)
        self.spin.setValue(value)
        self.spin.setToolTip("Click a digit, then use the arrows, the Up/Down keys or the mouse wheel to step "
                             "that digit (ones place = 1, tenths = 0.1 …). You can also type a value.")
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(2)
        row.addWidget(self.spin, 1)
        # hide the built-in arrows (the buttons below replace them); NoButtons breaks the
        # text area under a style sheet on some platforms, so make them zero-width instead
        self.spin.setObjectName("digits")
        self.spin.setMinimumWidth(90)
        self.spin.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
        for txt, fn, tip in (("◀", lambda: self.spin.shift_digit(-1), "Select a bigger digit (×10)"),
                             ("▶", lambda: self.spin.shift_digit(1), "Select a smaller digit (÷10)"),
                             ("▲", lambda: self.spin.step_active(1), "Increase the selected digit"),
                             ("▼", lambda: self.spin.step_active(-1), "Decrease the selected digit")):
            b = QtWidgets.QToolButton()
            b.setObjectName("digit")
            b.setText(txt)
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setAutoRepeat(txt in ("▲", "▼"))
            b.clicked.connect(lambda _=False, fn=fn: fn())
            row.addWidget(b)
        g.addLayout(row, 0, 1)
        g.setColumnStretch(1, 1)
        self.spin.valueChanged.connect(self._spin)

    def set_usage(self, usage):
        self.usage = list(usage)
        if self.usage:
            self.rm.setStyleSheet(f"QToolButton {{ color: {theme.BORDER}; }}")
            more = f"\n… and {len(self.usage) - 8} more" if len(self.usage) > 8 else ""
            self.rm.setToolTip(f"{self.name} is in use, so it can't be deleted yet. Used in:\n• "
                               + "\n• ".join(self.usage[:8]) + more)
        else:
            self.rm.setStyleSheet("")
            self.rm.setToolTip(f"Delete variable {self.name}")

    def _delete_clicked(self):
        if not self.usage:
            self.deleted.emit(self.name)
            return
        more = f"<br>… and {len(self.usage) - 20} more" if len(self.usage) > 20 else ""
        QtWidgets.QMessageBox.information(
            self, "Variable in use",
            f"<b>{self.name}</b> can't be deleted because it is used in:<br><br>• "
            + "<br>• ".join(self.usage[:20]) + more
            + "<br><br>Replace it with a number or another variable in those places first, then delete it.")

    def set_value(self, v):
        if self.spin is None:
            self.val.setText(f"{self.expr}  =  {v:.6g}")
            return
        self._busy = True
        self.spin.setValue(v)
        self._busy = False

    def _spin(self, v):
        if not self._busy:
            self.changed.emit(self.name, self._text(v))

    def _text(self, v):
        return f"{round(v, self.spin.decimals()):.10g}{self.unit}"
