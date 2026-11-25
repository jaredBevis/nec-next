"""Structure tab: every physical and electrical object in the deck, with forms.

Lists wires, arcs, helices, move/copy transforms, feeds (EX), loads (LD),
transmission lines (TL), two-port networks (NT) and transformers (an NT card
tagged with a trailing  ' xfmr Z1:Z2  comment).  Edits are written back into
the deck text, which stays the single source of truth.
"""
from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Tuple

from ..deck import Card, Deck, DeckError, evaluate
from . import theme
from .compat import QAction, Qt, QtCore, QtGui, QtWidgets, Signal

# ------------------------------------------------------------------ specs
EX_TYPES = {0: "Voltage source (applied E-field)", 5: "Voltage source (current-slope discontinuity)"}
LD_TYPES = {
    0: "Series R-L-C", 1: "Parallel R-L-C", 2: "Series R-L-C per metre",
    3: "Parallel R-L-C per metre", 4: "Impedance R + jX", 5: "Wire conductivity (material)",
}
LD_LABELS = {
    0: ("Resistance (Ω)", "Inductance (H)", "Capacitance (F)"),
    1: ("Resistance (Ω)", "Inductance (H)", "Capacitance (F)"),
    2: ("Resistance (Ω/m)", "Inductance (H/m)", "Capacitance (F·m)"),
    3: ("Resistance (Ω/m)", "Inductance (H/m)", "Capacitance (F/m)"),
    4: ("Resistance (Ω)", "Reactance (Ω)", None),
    5: ("Conductivity (S/m)", None, None),
}
MATERIALS = {"Copper": "5.8e7", "Aluminium": "3.5e7", "Brass": "1.5e7", "Steel": "6e6", "Stainless steel": "1.4e6"}

# kind -> list of (field index, label, widget kind, tooltip)
SPECS: Dict[str, List[Tuple[int, str, str, str]]] = {
    "GW": [(0, "Tag", "int", "Wire number used by feeds and loads"),
           (1, "Segments", "int", "More segments = more accurate; aim for < 0.05 λ each"),
           (2, "X1 (m)", "num", ""), (3, "Y1 (m)", "num", ""), (4, "Z1 (m)", "num", ""),
           (5, "X2 (m)", "num", ""), (6, "Y2 (m)", "num", ""), (7, "Z2 (m)", "num", ""),
           (8, "Wire radius (m)", "num", "e.g. 1mm, 12.7mm, 0.5in")],
    "GA": [(0, "Tag", "int", ""), (1, "Segments", "int", ""),
           (2, "Arc radius (m)", "num", "Arc lies in the X-Z plane, centred on the origin"),
           (3, "Start angle (°)", "num", "Measured from +X toward +Z"), (4, "End angle (°)", "num", ""),
           (5, "Wire radius (m)", "num", "")],
    "GH": [(0, "Tag", "int", ""), (1, "Segments", "int", ""),
           (2, "Turn spacing (m)", "num", "Distance between turns along the axis"),
           (3, "Total length (m)", "num", "Along +Z from the origin. Negative = left-hand wound"),
           (4, "Radius X at start (m)", "num", ""), (5, "Radius Y at start (m)", "num", ""),
           (6, "Radius X at end (m)", "num", "Different start/end radii make a tapered helix"),
           (7, "Radius Y at end (m)", "num", ""), (8, "Wire radius (m)", "num", "")],
    "GM": [(0, "Tag increment", "int", "Added to tags of each copy (0 = move, no copy)"),
           (1, "Copies", "int", "0 = move the structure instead of copying"),
           (2, "Rotate X (°)", "num", ""), (3, "Rotate Y (°)", "num", ""), (4, "Rotate Z (°)", "num", ""),
           (5, "Move X (m)", "num", ""), (6, "Move Y (m)", "num", ""), (7, "Move Z (m)", "num", ""),
           (8, "First tag affected", "num", "Only wires with this tag and higher (0 = everything above)")],
    "GR": [(0, "Tag increment", "int", ""), (1, "Total copies", "int", "Rotated evenly about the Z axis")],
    "GS": [(2, "Scale factor", "num", "All coordinates so far are multiplied by this (e.g. 0.0254 for inches)")],
    "GX": [(0, "Tag increment", "int", ""), (1, "Reflect in planes (XYZ flags)", "int",
                                               "e.g. 100 = X-Y? digits: X=YZ plane, Y=XZ plane, Z=XY plane")],
    "EX": [(0, "Source type", "enum_ex", ""), (1, "Wire tag", "tag", ""), (2, "Segment", "seg", ""),
           (4, "Voltage, real (V)", "num", ""), (5, "Voltage, imaginary (V)", "num",
                                                 "Use with several feeds to set relative phase")],
    "LD": [(0, "Load type", "enum_ld", ""), (1, "Wire tag (0 = all wires)", "tag0", ""),
           (2, "First segment (0 = whole wire)", "seg", ""), (3, "Last segment", "seg", ""),
           (4, "F1", "num", ""), (5, "F2", "num", ""), (6, "F3", "num", "")],
    "TL": [(0, "End 1 wire tag", "tag", ""), (1, "End 1 segment", "seg", ""),
           (2, "End 2 wire tag", "tag", ""), (3, "End 2 segment", "seg", ""),
           (4, "Impedance Z0 (Ω)", "num", "Negative = crossed line (180° phase reversal)"),
           (5, "Length (m)", "num", "0 = straight-line distance between the two segments"),
           (6, "Shunt Y end 1, real (S)", "num", ""), (7, "Shunt Y end 1, imag (S)", "num", ""),
           (8, "Shunt Y end 2, real (S)", "num", ""), (9, "Shunt Y end 2, imag (S)", "num", "")],
    "NT": [(0, "Port 1 wire tag", "tag", ""), (1, "Port 1 segment", "seg", ""),
           (2, "Port 2 wire tag", "tag", ""), (3, "Port 2 segment", "seg", ""),
           (4, "Y11 real (S)", "num", ""), (5, "Y11 imag (S)", "num", ""),
           (6, "Y12 real (S)", "num", ""), (7, "Y12 imag (S)", "num", ""),
           (8, "Y22 real (S)", "num", ""), (9, "Y22 imag (S)", "num", "")],
}
NAMES = {"GW": "Wire", "GA": "Arc", "GH": "Helix", "GM": "Move / rotate / copy", "GR": "Rotate-repeat",
         "GS": "Scale", "GX": "Reflect", "EX": "Feed point", "LD": "Load", "TL": "Transmission line",
         "NT": "Two-port network", "XF": "Transformer", "SP": "Surface patch", "SM": "Surface patch",
         "SC": "Patch continuation", "GC": "Wire taper", "GF": "Saved geometry"}
GROUPS = [("Geometry", ("GW", "GA", "GH", "GM", "GR", "GS", "GX", "SP", "SM", "SC", "GC", "GF")),
          ("Feeds", ("EX",)), ("Loads", ("LD",)), ("Transmission lines", ("TL",)),
          ("Transformers & networks", ("XF", "NT"))]
GEOM_KINDS = {"GW", "GA", "GH", "GM", "GR", "GS", "GX", "SP", "SM", "SC", "GC", "GF"}

XF_RE = re.compile(r"^xfmr\s+([^:\s]+)\s*:\s*(\S+)(?:\s+rs=(\S+))?", re.I)


def si(v: float, unit: str) -> str:
    if v == 0:
        return f"0 {unit}"
    for f, p in ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"), (1e-12, "p")):
        if abs(v) >= f * 0.9999:
            return f"{v / f:.4g} {p}{unit}"
    return f"{v:.3g} {unit}"


def is_xfmr(c: Card) -> bool:
    return c.kind == "NT" and bool(XF_RE.match(c.comment or ""))


def xfmr_line(t1, s1, t2, s2, r1: str, r2: str, symbols=None, rs: float = 1e-3) -> str:
    """NT card for a near-ideal transformer with impedance ratio r1:r2 (port 1 : port 2).

    Ideal transformer (turns n = sqrt(r1/r2)) with a tiny series resistance rs on
    port 2 so the admittance matrix exists.  Frequency independent.
    """
    a = evaluate(str(r1), symbols or {})
    b = evaluate(str(r2), symbols or {})
    if a <= 0 or b <= 0:
        raise DeckError("Ratio values must be positive")
    n = math.sqrt(a / b)
    y11, y12, y22 = 1 / (n * n * rs), -1 / (n * rs), 1 / rs
    return (f"NT {t1} {s1} {t2} {s2} {y11:.8g} 0 {y12:.8g} 0 {y22:.8g} 0 ' xfmr {r1}:{r2}"
            + (f" rs={rs:g}" if rs != 1e-3 else ""))


def summary(c: Card) -> str:
    v = c.values
    iv = c.ints()

    def f(i, d=0.0):
        return v[i] if i < len(v) else d
    try:
        if c.kind == "GW":
            L = math.dist(v[2:5], v[5:8]) if len(v) >= 8 else 0
            return f"tag {iv[0]} · {iv[1]} seg · {L:.3g} m · r {si(f(8), 'm')}"
        if c.kind == "GA":
            return f"tag {iv[0]} · {iv[1]} seg · radius {f(2):.3g} m · {f(3):g}°–{f(4):g}°"
        if c.kind == "GH":
            turns = abs(f(3)) / f(2) if f(2) else 0
            hand = "left" if f(3) < 0 else "right"
            return f"tag {iv[0]} · {turns:.3g} turns ({hand}) · Ø {2 * f(4):.3g} m · len {abs(f(3)):.3g} m"
        if c.kind == "GM":
            parts = []
            if any(f(k) for k in (2, 3, 4)):
                parts.append(f"rotate {f(2):g}/{f(3):g}/{f(4):g}°")
            if any(f(k) for k in (5, 6, 7)):
                parts.append(f"move {f(5):g},{f(6):g},{f(7):g} m")
            cp = f"{iv[1]} copies" if iv[1] else "move"
            return f"{cp} · " + (" · ".join(parts) or "no change")
        if c.kind == "GR":
            return f"{iv[1]} copies around Z"
        if c.kind == "GS":
            return f"× {f(2):g}"
        if c.kind == "EX":
            return f"tag {iv[1]} seg {iv[2]} · {f(4):g}{f(5):+g}j V"
        if c.kind == "LD":
            t = iv[0]
            where = "all wires" if iv[1] == 0 else (f"tag {iv[1]}" + ("" if iv[2] == 0 else
                    f" seg {iv[2]}" + (f"–{iv[3]}" if iv[3] and iv[3] != iv[2] else "")))
            if t == 5:
                return f"{where} · σ {f(4):.3g} S/m"
            if t == 4:
                return f"{where} · {f(4):g}{f(5):+g}j Ω"
            parts = []
            if f(4):
                parts.append(si(f(4), "Ω"))
            if f(5):
                parts.append(si(f(5), "H"))
            if f(6):
                parts.append(si(f(6), "F"))
            return f"{where} · " + (" + ".join(parts) or "empty")
        if c.kind == "TL":
            z0 = f(4)
            return (f"{iv[0]}/{iv[1]} → {iv[2]}/{iv[3]} · {abs(z0):g} Ω" + (" crossed" if z0 < 0 else "")
                    + (f" · {f(5):.3g} m" if f(5) else " · straight length"))
        if c.kind == "NT":
            m = XF_RE.match(c.comment or "")
            if m:
                return f"{m.group(1)}:{m.group(2)} · port 1 {iv[0]}/{iv[1]} → port 2 {iv[2]}/{iv[3]}"
            return f"{iv[0]}/{iv[1]} ↔ {iv[2]}/{iv[3]}"
    except (IndexError, ValueError, TypeError):
        pass
    return " ".join(c.fields)


# ------------------------------------------------------------------ panel
class StructurePanel(QtWidgets.QWidget):
    lineEdited = Signal(int, str)
    addLines = Signal(object, str)      # list of lines, section ("geom" | "ctrl")
    deleteLine = Signal(int)
    highlight = Signal(object)          # dict(tag=..., segs=[(tag, seg), ...]) or None
    insertTransformer = Signal(str, str)  # ratio a, b

    def __init__(self, parent=None):
        super().__init__(parent)
        self.deck: Optional[Deck] = None
        self.tags: Dict[int, int] = {}       # tag -> number of segments (from nec2c geometry)
        self.sel_line: Optional[int] = None
        self._form_line: Optional[int] = None
        self._form_kind: Optional[str] = None
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QtWidgets.QHBoxLayout()
        bar.setContentsMargins(6, 6, 6, 4)
        self.add_btn = QtWidgets.QPushButton("+ Add")
        self.add_btn.setObjectName("primary")
        self.add_btn.setMenu(self._add_menu())
        self.dup_btn = QtWidgets.QPushButton("Duplicate")
        self.del_btn = QtWidgets.QPushButton("Delete")
        bar.addWidget(self.add_btn)
        bar.addWidget(self.dup_btn)
        bar.addWidget(self.del_btn)
        bar.addStretch(1)
        lay.addLayout(bar)
        self.dup_btn.clicked.connect(self._duplicate)
        self.del_btn.clicked.connect(self._delete)

        split = QtWidgets.QSplitter(Qt.Orientation.Vertical)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Object", "Details"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setStretchLastSection(True)
        self.tree.setColumnWidth(0, 178)
        self.tree.itemSelectionChanged.connect(self._selected)
        split.addWidget(self.tree)
        self.form_area = QtWidgets.QScrollArea()
        self.form_area.setWidgetResizable(True)
        self.form_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        split.addWidget(self.form_area)
        split.setSizes([260, 320])
        lay.addWidget(split, 1)
        self._empty_form()

    # -------------------------------------------------------------- menu
    def _add_menu(self):
        m = QtWidgets.QMenu(self)
        items = [
            ("Wire", lambda: self._add_geom(f"GW {self._next_tag()} 11 0 0 0 0 0 1 1mm")),
            ("Arc", lambda: self._add_geom(f"GA {self._next_tag()} 12 0.5 0 180 1mm")),
            ("Helix", lambda: self._add_geom(f"GH {self._next_tag()} 40 0.05 0.5 0.1 0.1 0.1 0.1 1mm")),
            ("Move / rotate / copy", lambda: self._add_geom("GM 0 0 0 0 0 0 0 0 0")),
            None,
            ("Feed point", lambda: self._add_ctrl(f"EX 0 {self._t1()} {self._mid(self._t1())} 0 1 0")),
            ("Coil (inductor)", lambda: self._add_ctrl(
                f"LD 0 {self._t1()} {self._mid(self._t1())} {self._mid(self._t1())} 0 10uH 0")),
            ("Capacitor", lambda: self._add_ctrl(
                f"LD 0 {self._t1()} {self._mid(self._t1())} {self._mid(self._t1())} 0 0 100pF")),
            ("Resistor", lambda: self._add_ctrl(
                f"LD 0 {self._t1()} {self._mid(self._t1())} {self._mid(self._t1())} 50 0 0")),
            ("Trap (parallel L-C)", lambda: self._add_ctrl(
                f"LD 1 {self._t1()} {self._mid(self._t1())} {self._mid(self._t1())} 0 4.7uH 26pF")),
            ("Wire material (all wires)", lambda: self._add_ctrl("LD 5 0 0 0 5.8e7")),
            None,
            ("Transmission line / stub", lambda: self._add_ctrl(
                f"TL {self._t1()} {self._mid(self._t1())} {self._tlast()} {self._mid(self._tlast())} 50 0 0 0 0 0")),
            ("Transformer at the feed point…", self._xfmr_at_feed),
            ("Transformer between two segments", lambda: self._add_ctrl(
                xfmr_line(self._t1(), self._mid(self._t1()), self._tlast(), self._mid(self._tlast()), "4", "1"))),
            ("Two-port network (Y parameters)", lambda: self._add_ctrl(
                f"NT {self._t1()} {self._mid(self._t1())} {self._tlast()} {self._mid(self._tlast())} 0 0 0 0 0 0")),
        ]
        for it in items:
            if it is None:
                m.addSeparator()
            else:
                a = m.addAction(it[0])
                a.triggered.connect(it[1])
        return m

    def _next_tag(self) -> int:
        tags = set(self.tags)
        if self.deck:
            for c in self.deck.cards:
                if c.kind in ("GW", "GA", "GH") and c.values:
                    tags.add(int(round(c.values[0])))
        return max(tags, default=0) + 1

    def _t1(self) -> int:
        return min(self.tags) if self.tags else 1

    def _tlast(self) -> int:
        return max(self.tags) if self.tags else 1

    def _mid(self, tag) -> int:
        n = self.tags.get(tag, 1)
        return (n + 1) // 2

    def _add_geom(self, line):
        self._pending_select = line.split()[0]
        self.addLines.emit([line], "geom")

    def _add_ctrl(self, line):
        self._pending_select = line.split()[0]
        self.addLines.emit([line], "ctrl")

    def _xfmr_at_feed(self):
        a, ok = QtWidgets.QInputDialog.getText(
            self, "Transformer at feed point",
            "Impedance ratio, feed side : antenna side\n(e.g. 1:4 for a 4:1 balun feeding ~200 Ω, 1:49 for an EFHW)",
            text="1:4")
        if not ok or ":" not in a:
            return
        r1, r2 = (x.strip() for x in a.split(":", 1))
        self.insertTransformer.emit(r1, r2)

    # -------------------------------------------------------------- load
    def set_tags(self, tags: Dict[int, int]):
        self.tags = dict(tags)
        self._refresh_tag_widgets()

    def load(self, deck: Deck):
        self.deck = deck
        keep = self.sel_line
        pend = getattr(self, "_pending_select", None)
        self.tree.blockSignals(True)
        self.tree.clear()
        groups = {}
        target = None
        pend_target = None
        for title, kinds in GROUPS:
            g = QtWidgets.QTreeWidgetItem([title, ""])
            g.setFlags(Qt.ItemFlag.ItemIsEnabled)
            f = g.font(0)
            f.setBold(True)
            g.setFont(0, f)
            g.setForeground(0, QtGui.QBrush(QtGui.QColor(theme.MUTED)))
            self.tree.addTopLevelItem(g)
            groups[kinds] = g
        for c in deck.cards:
            kind = "XF" if is_xfmr(c) else c.kind
            for kinds, g in groups.items():
                if kind in kinds:
                    name = NAMES.get(kind, kind)
                    if kind == "LD" and c.values:
                        name = LD_TYPES.get(int(round(c.values[0])), "Load").split(" (")[0]
                    it = QtWidgets.QTreeWidgetItem([name, summary(c)])
                    it.setData(0, Qt.ItemDataRole.UserRole, c.line)
                    it.setToolTip(1, f"Line {c.line + 1}: {c.to_nec()}")
                    g.addChild(it)
                    if c.line == keep:
                        target = it
                    if pend and c.kind == pend:
                        pend_target = it   # newest card of the kind just added (last wins)
                    break
        for kinds, g in groups.items():
            g.setText(1, f"{g.childCount()}" if g.childCount() else "none")
            g.setExpanded(True)
        if pend_target is not None:
            target = pend_target
        self._pending_select = None
        self.tree.blockSignals(False)
        if target is not None:
            self.tree.setCurrentItem(target)
            line = target.data(0, Qt.ItemDataRole.UserRole)
            if line != self._form_line or self._card_kind(line) != self._form_kind:
                self._selected()
            else:
                self._update_hints()
        else:
            self.sel_line = None
            self._empty_form()

    def _card(self, line) -> Optional[Card]:
        if not self.deck:
            return None
        for c in self.deck.cards:
            if c.line == line:
                return c
        return None

    def _card_kind(self, line):
        c = self._card(line)
        if not c:
            return None
        return ("XF" if is_xfmr(c) else c.kind) + (f"{int(round(c.values[0]))}" if c.kind in ("LD", "EX") and c.values else "")

    # -------------------------------------------------------------- selection
    def _selected(self):
        it = self.tree.currentItem()
        line = it.data(0, Qt.ItemDataRole.UserRole) if it else None
        self.sel_line = line
        c = self._card(line) if line is not None else None
        if c is None:
            self._empty_form()
            self.highlight.emit(None)
            return
        self._build_form(c)
        self._emit_highlight(c)

    def _emit_highlight(self, c: Card):
        iv = c.ints()
        if c.kind in ("GW", "GA", "GH") and iv:
            self.highlight.emit({"tag": iv[0]})
        elif c.kind == "EX" and len(iv) >= 3:
            self.highlight.emit({"segs": [(iv[1], iv[2])]})
        elif c.kind == "LD" and len(iv) >= 4 and iv[1]:
            if iv[2] == 0:
                self.highlight.emit({"tag": iv[1]})
            else:
                self.highlight.emit({"segs": [(iv[1], s) for s in range(iv[2], max(iv[2], iv[3]) + 1)]})
        elif c.kind in ("TL", "NT") and len(iv) >= 4:
            self.highlight.emit({"segs": [(iv[0], iv[1]), (iv[2], iv[3])]})
        else:
            self.highlight.emit(None)

    def _empty_form(self):
        w = QtWidgets.QWidget()
        l = QtWidgets.QVBoxLayout(w)
        lbl = QtWidgets.QLabel("Select an object to edit it, or use <b>+ Add</b> to create wires, arcs, "
                               "helices, feed points, coils, capacitors, traps, transmission lines and "
                               "transformers.<br><br>Every field accepts numbers, variables and expressions "
                               "such as <code>len/2</code>, <code>12.7mm</code>, <code>4.7uH</code> or "
                               "<code>100pF</code>.")
        lbl.setWordWrap(True)
        lbl.setObjectName("muted")
        l.addWidget(lbl)
        l.addStretch(1)
        self.form_area.setWidget(w)
        self._form_line = None
        self._form_kind = None
        self._fields = {}

    # -------------------------------------------------------------- forms
    def _build_form(self, c: Card):
        kind = "XF" if is_xfmr(c) else c.kind
        self._form_line = c.line
        self._form_kind = self._card_kind(c.line)
        self._fields = {}
        w = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(w)
        outer.setContentsMargins(10, 10, 10, 10)
        title = QtWidgets.QLabel(NAMES.get(kind, kind) + f"  <span style='color:{theme.MUTED};font-weight:400'>"
                                 f"line {c.line + 1} · {c.kind} card</span>")
        title.setObjectName("h1")
        outer.addWidget(title)
        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)
        outer.addLayout(form)
        raw = list(c.fields)

        def rawf(i, default="0"):
            return raw[i] if i < len(raw) and raw[i] != "" else default

        if kind == "XF":
            m = XF_RE.match(c.comment)
            r1, r2 = m.group(1), m.group(2)
            self._add_tag_seg(form, "Port 1 (feed side)", 0, 1, rawf)
            self._add_tag_seg(form, "Port 2 (antenna side)", 2, 3, rawf)
            row = QtWidgets.QHBoxLayout()
            e1 = QtWidgets.QLineEdit(r1)
            e2 = QtWidgets.QLineEdit(r2)
            row.addWidget(e1)
            row.addWidget(QtWidgets.QLabel(":"))
            row.addWidget(e2)
            form.addRow("Impedance ratio", row)
            self._fields["r1"], self._fields["r2"] = e1, e2
            for e in (e1, e2):
                e.editingFinished.connect(self._commit)
            note = ("Near-ideal, frequency-independent transformer. Port 1 sees (ratio) × the impedance at "
                    "port 2. A 4:1 current balun feeding a 200 Ω antenna from 50 Ω is <b>1:4</b> here.<br>"
                    "Each port must sit on its own wire segment; <i>Transformer at the feed point…</i> in "
                    "the Add menu builds the usual layout for you (a 1-segment source wire with the feed on it).")
            self._note(outer, note)
        else:
            spec = SPECS.get(c.kind)
            if spec is None:
                self._note(outer, f"Edit this {c.kind} card in the Deck tab.<br><code>{c.to_nec()}</code>")
            else:
                ld_type = int(round(c.values[0])) if c.kind == "LD" and c.values else 0
                skip = set()
                for idx, label, wk, tip in spec:
                    if idx in skip:
                        continue
                    if c.kind == "LD" and idx >= 4:
                        lab = LD_LABELS.get(ld_type, (None, None, None))[idx - 4]
                        if lab is None:
                            continue
                        label = lab
                    if wk in ("tag", "tag0") and idx + 1 < 20 and any(s[0] == idx + 1 and s[2] == "seg" for s in spec):
                        seg_label = [s[1] for s in spec if s[0] == idx + 1][0]
                        self._add_tag_seg(form, label, idx, idx + 1, rawf, allow_zero=(wk == "tag0"),
                                          seg_label=seg_label, last_idx=3 if c.kind == "LD" else None)
                        skip.add(idx + 1)
                        if c.kind == "LD":
                            skip.add(3)
                        continue
                    if wk == "enum_ex":
                        cb = QtWidgets.QComboBox()
                        for k, v in EX_TYPES.items():
                            cb.addItem(v, k)
                        cb.setCurrentIndex(max(0, cb.findData(int(round(float(c.values[0]))))))
                        cb.currentIndexChanged.connect(lambda _=0: self._commit())
                        self._fields[idx] = cb
                        form.addRow(label, cb)
                        continue
                    if wk == "enum_ld":
                        cb = QtWidgets.QComboBox()
                        for k, v in LD_TYPES.items():
                            cb.addItem(v, k)
                        cb.setCurrentIndex(max(0, cb.findData(ld_type)))
                        cb.currentIndexChanged.connect(lambda _=0: self._commit(rebuild=True))
                        self._fields[idx] = cb
                        form.addRow(label, cb)
                        continue
                    e = QtWidgets.QLineEdit(rawf(idx))
                    e.setToolTip(tip)
                    e.editingFinished.connect(self._commit)
                    self._fields[idx] = e
                    hint = QtWidgets.QLabel("")
                    hint.setObjectName("muted")
                    hint.setMinimumWidth(60)
                    hint.setMaximumWidth(110)
                    row = QtWidgets.QHBoxLayout()
                    row.addWidget(e, 1)
                    row.addWidget(hint)
                    e.setProperty("hint", hint)
                    form.addRow(label, row)
                if c.kind == "LD" and ld_type == 5:
                    cb = QtWidgets.QComboBox()
                    cb.addItem("Pick a material…", "")
                    for k, v in MATERIALS.items():
                        cb.addItem(f"{k} ({v} S/m)", v)
                    cb.currentIndexChanged.connect(lambda i, cb=cb: cb.currentData() and (
                        self._fields[4].setText(cb.currentData()), self._commit()))
                    form.addRow("", cb)
                if c.kind == "LD" and ld_type in (0, 2):
                    self._note(outer, "Series R-L-C: leave C at 0 for no capacitor (a coil is just L). "
                                      "Values accept units: <code>10uH</code>, <code>100pF</code>.")
                if c.kind == "LD" and ld_type in (1, 3):
                    self._note(outer, "Parallel R-L-C (e.g. a trap). Leave R at 0 for no resistor.")
                if c.kind == "EX":
                    self._note(outer, "With several feed points, set magnitudes and phases with the real/"
                                      "imaginary voltages (e.g. 0 and 1 for +90°). The Z/SWR charts show "
                                      "the first feed.")
                if c.kind == "GA":
                    self._note(outer, "Arcs are built in the X-Z plane around the origin. Add a "
                                      "<i>Move / rotate / copy</i> after it to put it in place "
                                      "(set <i>First tag affected</i> to this arc's tag).")
                if c.kind == "GH":
                    self._note(outer, "Helices are built along +Z from the origin. Use <i>Move / rotate / copy</i> "
                                      "to position them. Turns = length ÷ spacing.")
                if c.kind == "GM":
                    self._note(outer, "Applies to everything defined above it with tag ≥ <i>First tag affected</i>.")
        outer.addStretch(1)
        self.form_area.setWidget(w)
        self._update_hints()

    def _note(self, lay, html):
        n = QtWidgets.QLabel(html)
        n.setWordWrap(True)
        n.setObjectName("muted")
        n.setTextFormat(Qt.TextFormat.RichText)
        lay.addSpacing(6)
        lay.addWidget(n)

    def _add_tag_seg(self, form, label, ti, si_, rawf, allow_zero=False, seg_label="Segment", last_idx=None):
        row = QtWidgets.QHBoxLayout()
        tag = QtWidgets.QComboBox()
        tag.setEditable(True)
        tag.setProperty("allow_zero", allow_zero)
        self._fill_tags(tag, rawf(ti))
        seg = QtWidgets.QLineEdit(rawf(si_))
        seg.setMaximumWidth(48)
        seg.setToolTip(seg_label)
        of = QtWidgets.QLabel("")
        of.setObjectName("muted")
        mid = QtWidgets.QPushButton("Mid")
        mid.setToolTip("Put it on the middle segment of the wire")
        row.addWidget(tag, 1)
        row.addWidget(QtWidgets.QLabel("seg"))
        row.addWidget(seg)
        row.addWidget(of)
        last = None
        if last_idx is not None:
            last = QtWidgets.QLineEdit(rawf(last_idx))
            last.setMaximumWidth(48)
            last.setToolTip("Last segment (same as first for a single segment)")
            row.addWidget(QtWidgets.QLabel("to"))
            row.addWidget(last)
            self._fields[last_idx] = last
            last.editingFinished.connect(self._commit)
        row.addWidget(mid)
        form.addRow(label, row)
        self._fields[ti] = tag
        self._fields[si_] = seg
        seg.setProperty("of", of)
        seg.setProperty("tagw", tag)

        def centre():
            t = self._tag_value(tag)
            m = self._mid(t)
            seg.setText(str(m))
            if last is not None:
                last.setText(str(m))
            self._commit()
        mid.clicked.connect(centre)
        tag.activated.connect(lambda _=0: self._commit())
        tag.lineEdit().editingFinished.connect(self._commit)
        seg.editingFinished.connect(self._commit)

    def _fill_tags(self, cb: QtWidgets.QComboBox, current: str):
        cb.blockSignals(True)
        cb.clear()
        if cb.property("allow_zero"):
            cb.addItem("0 · all wires", "0")
        for t in sorted(self.tags):
            cb.addItem(f"{t} · {self.tags[t]} seg", str(t))
        k = cb.findData(str(current).strip())
        if k >= 0:
            cb.setCurrentIndex(k)
        else:
            cb.setEditText(str(current))
        cb.blockSignals(False)

    def _refresh_tag_widgets(self):
        for w in getattr(self, "_fields", {}).values():
            if isinstance(w, QtWidgets.QComboBox) and w.isEditable():
                cur = self._tag_text(w)
                self._fill_tags(w, cur)
        self._update_hints()

    @staticmethod
    def _tag_text(cb: QtWidgets.QComboBox) -> str:
        t = cb.currentText().strip()
        d = cb.currentData()
        if d is not None and cb.itemText(cb.currentIndex()) == t:
            return d
        return t.split("·")[0].strip()

    def _tag_value(self, cb) -> int:
        try:
            return int(round(evaluate(self._tag_text(cb), self.deck.symbols if self.deck else {})))
        except Exception:
            return 0

    def _update_hints(self):
        if not self.deck:
            return
        for idx, w in getattr(self, "_fields", {}).items():
            if not isinstance(w, QtWidgets.QLineEdit):
                continue
            hint = w.property("hint")
            of = w.property("of")
            txt = w.text().strip()
            try:
                val = evaluate(txt, self.deck.symbols)
                ok = True
            except Exception:
                val, ok = None, False
            w.setStyleSheet("" if ok else f"border-color:{theme.BAD}")
            if hint is not None:
                try:
                    float(txt)
                    hint.setText("")
                except ValueError:
                    hint.setText(f"= {val:.6g}" if ok else "error")
            if of is not None:
                t = self._tag_value(w.property("tagw"))
                n = self.tags.get(t)
                of.setText(f"of {n}" if n else "")

    # -------------------------------------------------------------- commit
    def _commit(self, rebuild=False):
        line = self._form_line
        c = self._card(line)
        if c is None:
            return
        f = self._fields
        if is_xfmr(c):
            try:
                vals = [self._tag_text(f[0]), f[1].text().strip(), self._tag_text(f[2]), f[3].text().strip()]
                m = XF_RE.match(c.comment)
                rs = float(m.group(3)) if m.group(3) else 1e-3
                new = xfmr_line(*vals, f["r1"].text().strip(), f["r2"].text().strip(), self.deck.symbols, rs)
            except Exception as e:
                QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), f"Transformer: {e}")
                return
        else:
            spec = SPECS.get(c.kind)
            if not spec:
                return
            n = max([s[0] for s in spec] + [len(c.fields) - 1]) + 1
            raw = list(c.fields) + ["0"] * (n - len(c.fields))
            for idx, w in f.items():
                if not isinstance(idx, int):
                    continue
                if isinstance(w, QtWidgets.QComboBox) and not w.isEditable():
                    raw[idx] = str(w.currentData())
                elif isinstance(w, QtWidgets.QComboBox):
                    raw[idx] = self._tag_text(w) or "0"
                else:
                    raw[idx] = w.text().strip() or "0"
            if c.kind == "LD":
                t = int(raw[0])
                if t != int(round(c.values[0])):
                    defaults = {0: ["0", "10uH", "0"], 1: ["0", "4.7uH", "26pF"], 2: ["0", "0", "0"],
                                3: ["0", "0", "0"], 4: ["50", "0", "0"], 5: ["5.8e7", "0", "0"]}
                    raw[4:7] = defaults[t]
                    if t == 5:
                        raw[1:4] = ["0", "0", "0"]
                    rebuild = True
                if raw[2] not in ("0", "") and raw[3] in ("0", ""):
                    raw[3] = raw[2]
                if t == 5:
                    raw = raw[:5]
            while len(raw) > 2 and raw[-1] == "0" and c.kind in ("TL", "NT", "EX", "LD") and len(raw) > 4:
                raw.pop()
            new = c.kind + " " + " ".join(raw)
            if c.comment:
                new += " ' " + c.comment
        old = self.deck.text.splitlines()[line] if line < len(self.deck.text.splitlines()) else ""
        if new.strip() != old.strip():
            if rebuild:
                self._form_kind = None   # force a form rebuild after reparse
            self.lineEdited.emit(line, new)
        self._update_hints()

    # -------------------------------------------------------------- buttons
    def _duplicate(self):
        c = self._card(self.sel_line)
        if not c:
            return
        line = self.deck.text.splitlines()[c.line]
        if c.kind in ("GW", "GA", "GH"):
            parts = line.split()
            parts[1] = str(self._next_tag())
            line = " ".join(parts)
        self._pending_select = c.kind
        self.addLines.emit([line], "geom" if c.kind in GEOM_KINDS else "ctrl")

    def _delete(self):
        if self.sel_line is not None:
            self.deleteLine.emit(self.sel_line)
            self.sel_line = None
