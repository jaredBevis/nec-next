"""Builder panel: an ordered, point-and-click list of elements and path items."""
from __future__ import annotations

import copy
from typing import Optional, Tuple

from .. import builder as B
from ..deck import evaluate
from . import theme
from .compat import FlowLayout, Qt, QtCore, QtGui, QtWidgets, QShortcut, Signal

ROLE = Qt.ItemDataRole.UserRole

ICONS = {"wire": "━", "turn": "↱", "gap": "┄", "helix": "➰", "feed": "⚡", "load": "◉", "tl": "═",
         "element": "▣", "design": "◆"}

DENSITY = {"Coarse (20 per λ)": "20", "Normal (40 per λ)": "40", "Fine (80 per λ)": "80"}
MATERIAL_NAMES = {"perfect": "Perfect conductor (lossless)", "copper": "Copper", "aluminium": "Aluminium",
                  "brass": "Brass", "steel": "Steel", "stainless": "Stainless steel",
                  "copper-clad steel": "Copper-clad steel"}


class DesignPanel(QtWidgets.QWidget):
    designChanged = Signal()
    selectionChanged = Signal(object)   # (ei, ii) / (ei, None) / None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.design: Optional[dict] = None
        self.gen: Optional[B.GenResult] = None
        self._sel: Tuple = ("design",)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # --- quick insert strip
        strip = QtWidgets.QWidget()
        quick = FlowLayout(strip, 0, 4, 4)
        quick.setContentsMargins(6, 6, 6, 4)

        def qb(text, tip, fn):
            b = QtWidgets.QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            quick.addWidget(b)
            return b
        qb("━ Wire", "Insert a straight wire after the selected item", lambda: self.insert(B.new_item("wire")))
        qb("↰ Left", "Turn left 90° (azimuth +90)", lambda: self.insert(B.new_item("turn", yaw="90", pitch="0")))
        qb("↱ Right", "Turn right 90° (azimuth −90)", lambda: self.insert(B.new_item("turn", yaw="-90", pitch="0")))
        qb("↑ Up", "Turn up 90°", lambda: self.insert(B.new_item("turn", yaw="0", pitch="90")))
        qb("↓ Down", "Turn down 90°", lambda: self.insert(B.new_item("turn", yaw="0", pitch="-90")))
        qb("⚡ Feed", "Insert a feed point here", lambda: self.insert(B.new_item("feed")))
        qb("◉ Coil", "Insert a loading coil here", lambda: self.insert(B.new_item("load", kind="coil")))
        bar = quick
        self.ins = QtWidgets.QPushButton("+ Insert")
        self.ins.setObjectName("primary")
        self.ins.setMenu(self._insert_menu())
        self.up = QtWidgets.QPushButton("▲")
        self.up.setToolTip("Move up (Ctrl+Up)")
        self.dn = QtWidgets.QPushButton("▼")
        self.dn.setToolTip("Move down (Ctrl+Down)")
        self.dup = QtWidgets.QPushButton("Duplicate")
        self.rm = QtWidgets.QPushButton("Delete")
        for w in (self.ins, self.up, self.dn, self.dup, self.rm):
            bar.addWidget(w)
        lay.addWidget(strip)
        self.up.clicked.connect(lambda: self.move(-1))
        self.dn.clicked.connect(lambda: self.move(1))
        self.dup.clicked.connect(self.duplicate)
        self.rm.clicked.connect(self.delete)

        split = QtWidgets.QSplitter(Qt.Orientation.Vertical)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Item", "Details", "Ends at (x, y, z)"])
        self.tree.setUniformRowHeights(True)
        self.tree.setColumnWidth(0, 190)
        self.tree.setColumnWidth(1, 200)
        self.tree.itemSelectionChanged.connect(self._on_select)
        split.addWidget(self.tree)
        self.form_area = QtWidgets.QScrollArea()
        self.form_area.setWidgetResizable(True)
        self.form_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        split.addWidget(self.form_area)
        split.setSizes([330, 280])
        split.setChildrenCollapsible(False)
        self.tree.setMinimumHeight(110)
        self.form_area.setMinimumHeight(90)
        lay.addWidget(split, 1)
        self.msg = QtWidgets.QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setObjectName("muted")
        self.msg.setContentsMargins(8, 4, 8, 4)
        lay.addWidget(self.msg)

        for key, fn in (("Ctrl+Up", lambda: self.move(-1)), ("Ctrl+Down", lambda: self.move(1)),
                        ("Delete", self.delete), ("Ctrl+D", self.duplicate)):
            sc = QShortcut(QtGui.QKeySequence(key), self.tree)
            sc.activated.connect(fn)

    # ------------------------------------------------------------ menu
    def _insert_menu(self):
        m = QtWidgets.QMenu(self)

        def add(menu, text, fn):
            a = menu.addAction(text)
            a.triggered.connect(fn)
        add(m, "━  Wire", lambda: self.insert(B.new_item("wire")))
        t = m.addMenu("↱  Turn")
        add(t, "Left 90°", lambda: self.insert(B.new_item("turn", yaw="90", pitch="0")))
        add(t, "Right 90°", lambda: self.insert(B.new_item("turn", yaw="-90", pitch="0")))
        add(t, "Up 90°", lambda: self.insert(B.new_item("turn", yaw="0", pitch="90")))
        add(t, "Down 90°", lambda: self.insert(B.new_item("turn", yaw="0", pitch="-90")))
        add(t, "Left 45°", lambda: self.insert(B.new_item("turn", yaw="45", pitch="0")))
        add(t, "Right 45°", lambda: self.insert(B.new_item("turn", yaw="-45", pitch="0")))
        add(t, "Custom angle…", lambda: self.insert(B.new_item("turn", yaw="30", pitch="0")))
        add(t, "Point in a direction (absolute)…", lambda: self.insert(B.new_item("turn", mode="absolute", yaw="0", pitch="0")))
        add(m, "┄  Gap (move without wire)", lambda: self.insert(B.new_item("gap")))
        add(m, "➰  Helix / physical coil", lambda: self.insert(B.new_item("helix")))
        m.addSeparator()
        add(m, "⚡  Feed point", lambda: self.insert(B.new_item("feed")))
        add(m, "⚡  Feed point through a transformer", lambda: self.insert(B.new_item("feed", ratio="1:4")))
        ld = m.addMenu("◉  Load")
        add(ld, "Coil (inductor)", lambda: self.insert(B.new_item("load", kind="coil")))
        add(ld, "Capacitor", lambda: self.insert(B.new_item("load", kind="capacitor")))
        add(ld, "Resistor", lambda: self.insert(B.new_item("load", kind="resistor", R="50")))
        add(ld, "Trap (parallel L-C)", lambda: self.insert(B.new_item("load", kind="trap", L="4.7uH", C="26pF")))
        add(ld, "Series L-C", lambda: self.insert(B.new_item("load", kind="series_lc")))
        add(ld, "Impedance R + jX", lambda: self.insert(B.new_item("load", kind="impedance", R="50")))
        add(m, "═  Transmission line end", lambda: self.insert(B.new_item("tl")))
        m.addSeparator()
        add(m, "▣  New element (separate conductor)", self.new_element)
        return m

    # ------------------------------------------------------------ data
    def set_design(self, design: Optional[dict]):
        self.design = design
        self.gen = None
        self._sel = ("design",)
        self.rebuild()

    def set_gen(self, gen: Optional[B.GenResult], error: str = ""):
        self.gen = gen
        if error:
            self.msg.setText(f"<span style='color:{theme.BAD}'>⚠ {error}</span>")
        elif gen and gen.warnings:
            self.msg.setText("<br>".join(f"<span style='color:{theme.WARN}'>⚠ {w}</span>" for w in gen.warnings[:4]))
        elif gen:
            nseg = sum(w.segs for w in gen.wires)
            self.msg.setText(f"{len(set(w.tag for w in gen.wires))} wires · {nseg} segments · target segment "
                             f"{gen.seg_target:.3g} m (λ/{self.design.get('density', '40')} at the top frequency)")
        if gen:
            self._refresh_labels()
        self._update_positions()

    def _symbols(self):
        if self.gen:
            return self.gen.symbols
        s = {}
        for n, e in (self.design or {}).get("variables", []):
            try:
                s[n] = evaluate(e, s)
            except Exception:
                pass
        return s

    def rebuild(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        if not self.design:
            self.tree.blockSignals(False)
            return
        sym = self._symbols()
        root = QtWidgets.QTreeWidgetItem([f"{ICONS['design']}  {self.design.get('name') or 'Design'}",
                                          "radius, material, segmentation", ""])
        root.setData(0, ROLE, ("design",))
        f = root.font(0)
        f.setBold(True)
        root.setFont(0, f)
        self.tree.addTopLevelItem(root)
        target = root if self._sel == ("design",) else None
        for ei, el in enumerate(self.design["elements"]):
            n = len(el["items"])
            e = QtWidgets.QTreeWidgetItem([f"{ICONS['element']}  {el.get('name') or f'Element {ei + 1}'}",
                                           f"start {el['x']}, {el['y']}, {el['z']} · az {el['azimuth']}° el {el['elevation']}°"
                                           + ("" if el.get("enabled", True) else " · OFF"), ""])
            e.setData(0, ROLE, ("el", ei))
            e.setFont(0, f)
            if not el.get("enabled", True):
                e.setForeground(0, QtGui.QBrush(QtGui.QColor(theme.MUTED)))
            self.tree.addTopLevelItem(e)
            if self._sel == ("el", ei):
                target = e
            for ii, it in enumerate(el["items"]):
                c = QtWidgets.QTreeWidgetItem([f"{ii + 1:>2}. {ICONS.get(it['type'], '•')}  {B.title(it)}",
                                               B.describe(it, sym), ""])
                c.setData(0, ROLE, ("it", ei, ii))
                col = {"feed": theme.BAD, "load": theme.WARN, "tl": theme.ACCENT2, "turn": theme.MUTED}.get(it["type"])
                if col:
                    c.setForeground(0, QtGui.QBrush(QtGui.QColor(col)))
                e.addChild(c)
                if self._sel == ("it", ei, ii):
                    target = c
            e.setExpanded(True)
            if n == 0:
                hint = QtWidgets.QTreeWidgetItem(["     (empty: select the element and insert a wire)", "", ""])
                hint.setFlags(Qt.ItemFlag.NoItemFlags)
                hint.setData(0, ROLE, ("el", ei))
                e.addChild(hint)
        self.tree.blockSignals(False)
        self._update_positions()
        if target is None:
            target = root
        self.tree.setCurrentItem(target)
        self._build_form()

    def _update_positions(self):
        if not self.gen:
            return
        it = QtWidgets.QTreeWidgetItemIterator(self.tree)
        while it.value():
            node = it.value()
            key = node.data(0, ROLE)
            if key and key[0] == "it":
                p = self.gen.positions.get((key[1], key[2]))
                if p is not None:
                    x, y, z = p[0]
                    node.setText(2, f"{x:.3f}, {y:.3f}, {z:.3f}")
            it += 1

    # ------------------------------------------------------------ selection
    def _on_select(self):
        cur = self.tree.currentItem()
        key = cur.data(0, ROLE) if cur else None
        if not key:
            return
        self._sel = tuple(key)
        self._build_form()
        if key[0] == "it":
            self.selectionChanged.emit((key[1], key[2]))
        elif key[0] == "el":
            self.selectionChanged.emit((key[1], None))
        else:
            self.selectionChanged.emit(None)

    def current_selection(self):
        s = getattr(self, "_sel", None) or ("design",)
        if s[0] == "it":
            return (s[1], s[2])
        if s[0] == "el":
            return (s[1], None)
        return None

    def select(self, ei: int, ii: Optional[int]):
        self._sel = ("it", ei, ii) if ii is not None else ("el", ei)
        it = QtWidgets.QTreeWidgetItemIterator(self.tree)
        while it.value():
            if tuple(it.value().data(0, ROLE) or ()) == self._sel:
                self.tree.setCurrentItem(it.value())
                self.tree.scrollToItem(it.value())
                return
            it += 1

    def _cursor(self):
        """(element index, insert position) for a new item."""
        s = self._sel
        if not self.design["elements"]:
            self.design["elements"].append(B.new_element("Element 1"))
            return 0, 0
        if s[0] == "it":
            return s[1], s[2] + 1
        if s[0] == "el":
            return s[1], len(self.design["elements"][s[1]]["items"])
        ei = len(self.design["elements"]) - 1
        return ei, len(self.design["elements"][ei]["items"])

    # ------------------------------------------------------------ edits
    def _changed(self, rebuild=True):
        if rebuild:
            self.rebuild()
        self.designChanged.emit()

    def insert(self, item: dict):
        if self.design is None:
            return
        ei, pos = self._cursor()
        self.design["elements"][ei]["items"].insert(pos, item)
        self._sel = ("it", ei, pos)
        self._changed()

    def new_element(self):
        if self.design is None:
            return
        n = len(self.design["elements"])
        el = B.new_element(f"Element {n + 1}")
        if self._sel[0] in ("el", "it"):
            src = self.design["elements"][self._sel[1]]
            el.update({k: src[k] for k in ("x", "y", "z", "azimuth", "elevation")})
            idx = self._sel[1] + 1
        else:
            idx = n
        el["items"] = [B.new_item("wire")]
        self.design["elements"].insert(idx, el)
        self._sel = ("el", idx)
        self._changed()

    def move(self, d: int):
        s = self._sel
        els = self.design["elements"] if self.design else []
        if s[0] == "el":
            ei = s[1]
            j = ei + d
            if 0 <= j < len(els):
                els[ei], els[j] = els[j], els[ei]
                self._sel = ("el", j)
                self._changed()
        elif s[0] == "it":
            ei, ii = s[1], s[2]
            items = els[ei]["items"]
            j = ii + d
            if 0 <= j < len(items):
                items[ii], items[j] = items[j], items[ii]
                self._sel = ("it", ei, j)
            elif j < 0 and ei > 0:
                it = items.pop(ii)
                els[ei - 1]["items"].append(it)
                self._sel = ("it", ei - 1, len(els[ei - 1]["items"]) - 1)
            elif j >= len(items) and ei + 1 < len(els):
                it = items.pop(ii)
                els[ei + 1]["items"].insert(0, it)
                self._sel = ("it", ei + 1, 0)
            else:
                return
            self._changed()

    def duplicate(self):
        s = self._sel
        if s[0] == "el":
            el = copy.deepcopy(self.design["elements"][s[1]])
            el["name"] = (el.get("name") or "Element") + " copy"
            self.design["elements"].insert(s[1] + 1, el)
            self._sel = ("el", s[1] + 1)
        elif s[0] == "it":
            it = copy.deepcopy(self.design["elements"][s[1]]["items"][s[2]])
            self.design["elements"][s[1]]["items"].insert(s[2] + 1, it)
            self._sel = ("it", s[1], s[2] + 1)
        else:
            return
        self._changed()

    def delete(self):
        s = self._sel
        if s[0] == "el":
            del self.design["elements"][s[1]]
            self._sel = ("el", s[1] - 1) if s[1] > 0 else ("design",)
        elif s[0] == "it":
            del self.design["elements"][s[1]]["items"][s[2]]
            items = self.design["elements"][s[1]]["items"]
            self._sel = ("it", s[1], min(s[2], len(items) - 1)) if items else ("el", s[1])
        else:
            return
        self._changed()

    # ------------------------------------------------------------ forms
    def _build_form(self):
        w = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(w)
        outer.setContentsMargins(10, 8, 10, 10)
        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)
        s = self._sel
        if not self.design:
            self.form_area.setWidget(w)
            return
        sym = self._symbols()

        def head(text, sub=""):
            h = QtWidgets.QLabel(f"{text} <span style='color:{theme.MUTED};font-weight:400'>{sub}</span>")
            h.setObjectName("h1")
            outer.addWidget(h)

        def note(html):
            n = QtWidgets.QLabel(html)
            n.setWordWrap(True)
            n.setObjectName("muted")
            outer.addSpacing(4)
            outer.addWidget(n)

        def text_field(obj, key, label, tip="", rebuild=False, placeholder=""):
            e = QtWidgets.QLineEdit(str(obj.get(key, "")))
            e.setToolTip(tip)
            if placeholder:
                e.setPlaceholderText(placeholder)
            hint = QtWidgets.QLabel("")
            hint.setObjectName("muted")
            hint.setMaximumWidth(120)

            def upd():
                t = e.text().strip()
                if not t:
                    hint.setText("")
                    e.setStyleSheet("")
                    return
                try:
                    if ":" in t:
                        a_, b_ = (evaluate(x, sym) for x in t.split(":", 1))
                        if a_ <= 0 or b_ <= 0:
                            raise ValueError
                        hint.setText(f"Z × {b_ / a_:.3g}")
                        e.setStyleSheet("")
                        return
                    v = evaluate(t, sym)
                    try:
                        float(t)
                        hint.setText("")
                    except ValueError:
                        hint.setText(f"= {v:.6g}")
                    e.setStyleSheet("")
                except Exception:
                    hint.setText("error")
                    e.setStyleSheet(f"border-color:{theme.BAD}")

            def commit():
                if obj.get(key, "") != e.text().strip():
                    obj[key] = e.text().strip()
                    self._refresh_labels()
                    self.designChanged.emit()
                upd()
            e.editingFinished.connect(commit)
            e.textChanged.connect(lambda _=None: upd())
            upd()
            row = QtWidgets.QHBoxLayout()
            row.addWidget(e, 1)
            row.addWidget(hint)
            form.addRow(label, row)
            return e

        def plain_field(obj, key, label):
            e = QtWidgets.QLineEdit(str(obj.get(key, "")))

            def commit():
                if obj.get(key, "") != e.text():
                    obj[key] = e.text()
                    self._refresh_labels()
                    self.designChanged.emit()
            e.editingFinished.connect(commit)
            form.addRow(label, e)
            return e

        def combo(obj, key, label, options: dict, rebuild_form=False):
            cb = QtWidgets.QComboBox()
            for k, v in options.items():
                cb.addItem(v, k)
            i = cb.findData(obj.get(key))
            if i < 0:
                cb.addItem(str(obj.get(key)), obj.get(key))
                i = cb.count() - 1
            cb.setCurrentIndex(i)

            def ch(_):
                obj[key] = cb.currentData()
                self._refresh_labels()
                if rebuild_form:
                    QtCore.QTimer.singleShot(0, self._build_form)
                self.designChanged.emit()
            cb.currentIndexChanged.connect(ch)
            form.addRow(label, cb)
            return cb

        if s[0] == "design":
            d = self.design
            head("Design", "applies to every element")
            plain_field(d, "name", "Name")
            text_field(d, "radius", "Default wire radius", "Used by any wire whose radius is left blank, e.g. 1mm, 12.7mm, 0.5in")
            mats = dict(MATERIAL_NAMES)
            combo(d, "material", "Wire material", mats)
            dens = {v: k for k, v in DENSITY.items()}
            combo(d, "density", "Segmentation", dens)
            outer.addLayout(form)
            notes = QtWidgets.QPlainTextEdit(d.get("notes", ""))
            notes.setPlaceholderText("Notes (shown in the report)")
            notes.setMaximumHeight(80)
            notes.textChanged.connect(lambda: d.__setitem__("notes", notes.toPlainText()))
            outer.addWidget(notes)
            note("Segments are sized automatically from the highest sweep frequency and grow gradually "
                 "away from feeds and loads. Tags and segment numbers are assigned for you.")
        elif s[0] == "el":
            el = self.design["elements"][s[1]]
            head(el.get("name") or f"Element {s[1] + 1}", "separate conductor")
            plain_field(el, "name", "Name")
            text_field(el, "x", "Start X (m)")
            text_field(el, "y", "Start Y (m)")
            text_field(el, "z", "Start Z / height (m)")
            text_field(el, "azimuth", "Start heading azimuth (°)", "0 = +X, 90 = +Y (counter-clockwise seen from above)")
            text_field(el, "elevation", "Start heading elevation (°)", "0 = horizontal, 90 = straight up, −90 = down")
            en = QtWidgets.QCheckBox("Include in the simulation")
            en.setChecked(el.get("enabled", True))

            def toggle(v):
                el["enabled"] = bool(v)
                self._refresh_labels()
                self.designChanged.emit()
            en.toggled.connect(toggle)
            form.addRow("", en)
            outer.addLayout(form)
            note("The path starts at this point and heading. Items below are laid end to end; turns change "
                 "the heading. Wires that touch at the same point (also across elements) are connected.")
        else:
            it = self.design["elements"][s[1]]["items"][s[2]]
            t = it["type"]
            head(f"{ICONS.get(t, '')} {B.title(it)}", f"item {s[2] + 1} of {self.design['elements'][s[1]].get('name') or 'element'}")
            plain_field(it, "name", "Label (optional)")
            if t == "wire":
                text_field(it, "length", "Length (m)")
                text_field(it, "radius", "Wire radius", "Blank = design default", placeholder="default")
                text_field(it, "segments", "Segments", "Blank = automatic", placeholder="auto")
            elif t == "turn":
                combo(it, "mode", "Kind", {"relative": "Turn relative to the current heading",
                                           "absolute": "Point in an absolute direction"}, rebuild_form=True)
                if it.get("mode") == "absolute":
                    text_field(it, "yaw", "Azimuth (°)", "0 = +X, 90 = +Y")
                    text_field(it, "pitch", "Elevation (°)", "0 = horizontal, 90 = up")
                else:
                    text_field(it, "yaw", "Left / right (°)", "Positive = left (counter-clockwise from above), negative = right")
                    text_field(it, "pitch", "Up / down (°)", "Positive = up, negative = down")
                    row = QtWidgets.QHBoxLayout()
                    for lab, y, p in (("Left 90", 90, 0), ("Right 90", -90, 0), ("Up 90", 0, 90),
                                      ("Down 90", 0, -90), ("Reverse", 180, 0)):
                        b = QtWidgets.QPushButton(lab)
                        b.clicked.connect(lambda _=False, y=y, p=p: (it.update(yaw=str(y), pitch=str(p)),
                                                                    self._changed()))
                        row.addWidget(b)
                    form.addRow("Quick", row)
            elif t == "gap":
                text_field(it, "length", "Distance (m)")
            elif t == "helix":
                text_field(it, "turns", "Turns")
                text_field(it, "diameter", "Coil diameter")
                text_field(it, "length", "Coil length (along the heading)")
                text_field(it, "radius", "Wire radius", "Blank = design default", placeholder="default")
                combo(it, "hand", "Winding", {"right": "Right-hand", "left": "Left-hand"})
                text_field(it, "segs_per_turn", "Segments per turn")
            elif t == "feed":
                text_field(it, "voltage", "Voltage (V)")
                text_field(it, "phase", "Phase (°)", "Relative phase when there are several feeds")
                text_field(it, "ratio", "Transformer (feed : antenna)",
                           "Blank for none. 1:4 = 4:1 balun feeding ~200 Ω, 1:49 = EFHW transformer",
                           placeholder="none, e.g. 1:4 or 1:49")
            elif t == "load":
                combo(it, "kind", "Type", B.LOAD_KINDS, rebuild_form=True)
                k = it.get("kind", "coil")
                if k in ("coil", "trap", "series_lc"):
                    text_field(it, "L", "Inductance", "e.g. 10uH, 470nH")
                if k in ("capacitor", "trap", "series_lc"):
                    text_field(it, "C", "Capacitance", "e.g. 100pF, 1nF")
                if k in ("coil", "trap"):
                    text_field(it, "Q", "Q (0 = lossless)", "Coil Q at the pattern frequency; sets the loss resistance")
                if k in ("resistor", "capacitor", "series_lc", "impedance", "coil"):
                    text_field(it, "R", "Series resistance (Ω)" if k != "resistor" else "Resistance (Ω)")
                if k == "impedance":
                    text_field(it, "X", "Reactance (Ω)")
            elif t == "tl":
                plain_field(it, "link", "Line name")
                text_field(it, "z0", "Impedance Z0 (Ω)")
                text_field(it, "length", "Length (m)", "0 = straight-line distance between the two ends")
                text_field(it, "vf", "Velocity factor", "e.g. 0.66 for solid PE coax")
                combo(it, "crossed", "Crossed (phase reversal)", {"no": "No", "yes": "Yes"})
            outer.addLayout(form)
            tips = {
                "wire": "Runs along the current heading. Leave segments on auto.",
                "turn": "Changes the heading for everything after it. Example: three wires with two left turns "
                        "follow the corners of a yard.",
                "gap": "Moves the pen without making a wire, e.g. to start a new section elsewhere.",
                "helix": "Wound along the current heading, starting at the current point; the path continues "
                         "from the end of the coil.",
                "feed": "Sits at the point between the wires before and after it. With a transformer the "
                        "source is placed on a small separate wire and coupled through an ideal transformer.",
                "load": "Sits at the point between the wires before and after it. Use ▲ ▼ to slide it along "
                        "the antenna.",
                "tl": "Give two ends the same line name to join them with a transmission line. Physical "
                      "length is divided by the velocity factor.",
            }
            note(tips.get(t, ""))
        outer.addStretch(1)
        self.form_area.setWidget(w)

    def _refresh_labels(self):
        """Update tree texts without rebuilding (keeps focus in the form)."""
        sym = self._symbols()
        it = QtWidgets.QTreeWidgetItemIterator(self.tree)
        while it.value():
            node = it.value()
            key = node.data(0, ROLE)
            if key and key[0] == "it" and node.flags() != Qt.ItemFlag.NoItemFlags:
                try:
                    item = self.design["elements"][key[1]]["items"][key[2]]
                    node.setText(0, f"{key[2] + 1:>2}. {ICONS.get(item['type'], '•')}  {B.title(item)}")
                    node.setText(1, B.describe(item, sym))
                except (IndexError, KeyError):
                    pass
            elif key and key[0] == "el" and node.childCount() >= 0 and node.parent() is None:
                el = self.design["elements"][key[1]]
                node.setText(0, f"{ICONS['element']}  {el.get('name') or f'Element {key[1] + 1}'}")
                node.setText(1, f"start {el['x']}, {el['y']}, {el['z']} · az {el['azimuth']}° el {el['elevation']}°"
                             + ("" if el.get("enabled", True) else " · OFF"))
            elif key and key[0] == "design":
                node.setText(0, f"{ICONS['design']}  {self.design.get('name') or 'Design'}")
            it += 1
