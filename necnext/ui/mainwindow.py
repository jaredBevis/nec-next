"""Main application window."""
from __future__ import annotations

import csv
import math
import os
import threading
import time
from typing import Optional

import numpy as np

from .. import __version__
from ..deck import GROUND_PRESETS, Deck, Ground, build_geometry_deck, build_pattern_deck, build_sweep_deck
from ..engine import NecError, RunResult, Runner, find_nec2c, pattern_metrics
from . import theme
from .compat import QAction, Qt, QtCore, QtGui, QtWidgets, Signal, exec_
from .editor import DeckEditor, VariablePanel
from .structure import StructurePanel, xfmr_line
from .designer import DesignPanel
from .. import builder as B
from .plots import PatternView, SmithView, SweepView
from .view3d import HAVE_GL, make_view

EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "examples")

NEW_DECK = """CM New antenna
CE
SY f0=14.2, len=10.3, h=10, r=1mm
GW 1 21 0 -len/2 h 0 len/2 h r
GE 0
GN 2 0 0 0 13 0.005
EX 0 1 11 0 1 0
FR 0 41 0 0 13.8 0.02
EN
"""


# ------------------------------------------------------------------ jobs
class Job(QtCore.QObject):
    """Runs a nec2c deck on a worker thread and reports back on the GUI thread."""
    done = Signal(object, object)       # (RunResult, tag)
    failed = Signal(str, object)

    def __init__(self, exe: Optional[str]):
        super().__init__()
        self.exe = exe
        self.runner: Optional[Runner] = None

    def start(self, deck_text: str, tag=None):
        self.cancel()
        runner = Runner(self.exe)
        self.runner = runner

        def work():
            try:
                res = runner.run(deck_text)
                if runner is self.runner:
                    self.done.emit(res, tag)
            except NecError as e:
                if runner is self.runner and str(e) != "Cancelled":
                    self.failed.emit(str(e), tag)
            except Exception as e:  # pragma: no cover
                if runner is self.runner:
                    self.failed.emit(f"{type(e).__name__}: {e}", tag)
        threading.Thread(target=work, daemon=True).start()

    def cancel(self):
        if self.runner:
            self.runner.cancel()
            self.runner = None

    @property
    def busy(self):
        return self.runner is not None


# ------------------------------------------------------------------ tiles
class Tile(QtWidgets.QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("tile")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(0)
        t = QtWidgets.QLabel(title.upper())
        t.setObjectName("tileTitle")
        self.value = QtWidgets.QLabel("—")
        self.value.setObjectName("tileValue")
        self.sub = QtWidgets.QLabel(" ")
        self.sub.setObjectName("tileSub")
        lay.addWidget(t)
        lay.addWidget(self.value)
        lay.addWidget(self.sub)

    def set(self, value: str, sub: str = " ", color: Optional[str] = None):
        self.value.setText(value)
        self.value.setStyleSheet(f"color:{color}" if color else "")
        self.sub.setText(sub)


# ------------------------------------------------------------------ main
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, path: Optional[str] = None):
        super().__init__()
        self.settings = QtCore.QSettings("NecNext", "NecNext")
        self.path: Optional[str] = None
        self.deck = Deck("")
        self.sweep: Optional[RunResult] = None
        self.pat: Optional[RunResult] = None
        self.geom_res: Optional[RunResult] = None
        self.pattern_freq: Optional[float] = None
        self.design: Optional[dict] = None
        self.gen = None
        self._dirty = False
        self._t0 = {}
        exe = self.settings.value("nec2c_path", "") or None
        self.exe = find_nec2c(exe or "")
        self.job_geom = Job(self.exe)
        self.job_sweep = Job(self.exe)
        self.job_pat = Job(self.exe)
        self.job_geom.done.connect(self._geom_done)
        self.job_geom.failed.connect(self._geom_failed)
        self.job_sweep.done.connect(self._sweep_done)
        self.job_sweep.failed.connect(self._run_failed)
        self.job_pat.done.connect(self._pat_done)
        self.job_pat.failed.connect(self._run_failed)

        self.setWindowTitle("NEC Next")
        self.resize(1480, 900)
        self._build_ui()
        self._build_actions()
        self.parse_timer = QtCore.QTimer(self, singleShot=True, interval=350)
        self.parse_timer.timeout.connect(self._reparse)
        self.gen_timer = QtCore.QTimer(self, singleShot=True, interval=250)
        self.gen_timer.timeout.connect(self._regenerate)
        self.editor.textChanged.connect(self._text_changed)
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        self._fit_to_screen()
        if path:
            self.open_file(path)
        else:
            ex = os.path.join(EXAMPLES_DIR, "03 Yagi 3-element 20m.nnx")
            if os.path.exists(ex):
                self.open_file(ex, as_example=True)
            else:
                self._set_design(B.new_design(), None)
        self._update_status()

    def _fit_to_screen(self):
        """Keep the window inside the screen it is on (saved sizes can be too big)."""
        scr = self.screen() if hasattr(self, "screen") else None
        if scr is None:
            scr = QtWidgets.QApplication.primaryScreen()
        if scr is None:
            return
        av = scr.availableGeometry()
        fr = self.frameGeometry()
        if fr.width() > av.width() or fr.height() > av.height():
            self.resize(min(self.width(), av.width() - 16), min(self.height(), av.height() - 40))
            self.move(av.topLeft())
        elif not av.intersects(fr):
            self.move(av.topLeft())

    # -------------------------------------------------------------- UI build
    def _build_ui(self):
        central = QtWidgets.QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        split = QtWidgets.QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split)

        # ----- left: deck + simulation
        left = QtWidgets.QWidget()
        ll = QtWidgets.QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(8)
        self.left_tabs = QtWidgets.QTabWidget()
        self.editor = DeckEditor()
        self.wires = StructurePanel()
        self.vars = VariablePanel()
        self.designer = DesignPanel()
        self.designer.designChanged.connect(self._design_changed)
        self.designer.selectionChanged.connect(self._design_selected)
        self._mode = None
        lsplit = QtWidgets.QSplitter(Qt.Orientation.Vertical)
        top = QtWidgets.QWidget()
        tl = QtWidgets.QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        self.banner = QtWidgets.QFrame()
        self.banner.setObjectName("tile")
        bl = QtWidgets.QHBoxLayout(self.banner)
        bl.setContentsMargins(10, 6, 6, 6)
        self.banner_lbl = QtWidgets.QLabel("")
        self.banner_lbl.setWordWrap(True)
        self.banner_btn = QtWidgets.QPushButton("")
        bl.addWidget(self.banner_lbl, 1)
        bl.addWidget(self.banner_btn)
        self.banner_btn.clicked.connect(self._banner_action)
        self.banner.hide()
        tl.addWidget(self.banner)
        tl.addWidget(self.left_tabs, 1)
        self.err = QtWidgets.QLabel("")
        self.err.setObjectName("error")
        self.err.setWordWrap(True)
        self.err.hide()
        tl.addWidget(self.err)
        lsplit.addWidget(top)
        sim_scroll = QtWidgets.QScrollArea()
        sim_scroll.setWidgetResizable(True)
        sim_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        sim_scroll.setWidget(self._build_sim_box())
        sim_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lsplit.addWidget(sim_scroll)
        lsplit.setStretchFactor(0, 1)
        lsplit.setStretchFactor(1, 0)
        lsplit.setSizes([700, 220])
        lsplit.setChildrenCollapsible(False)
        ll.addWidget(lsplit, 1)
        split.addWidget(left)
        self.wires.lineEdited.connect(self._replace_line)
        self.wires.addLines.connect(self._add_lines)
        self.wires.deleteLine.connect(self._delete_line)
        self.wires.highlight.connect(self._highlight)
        self.wires.insertTransformer.connect(self._insert_transformer)
        self.vars.addVariable.connect(self._add_variable)
        self.vars.deleteVariable.connect(self._delete_variable)
        self.vars.usage_fn = self._variable_usage
        self.vars.variableChanged.connect(self._set_variable)

        # ----- right: tiles + views
        right = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)
        tiles = QtWidgets.QHBoxLayout()
        tiles.setSpacing(8)
        self.t_freq = Tile("Frequency")
        self.t_z = Tile("Impedance")
        self.t_swr = Tile("SWR")
        self.t_gain = Tile("Max gain")
        self.t_fb = Tile("Front / back")
        self.t_el = Tile("Take-off")
        self.t_eff = Tile("Efficiency")
        for t in (self.t_freq, self.t_z, self.t_swr, self.t_gain, self.t_fb, self.t_el, self.t_eff):
            t.setMinimumWidth(56)
            t.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
            tiles.addWidget(t, 1)
        rl.addLayout(tiles)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.setVisible(False)
        rl.addWidget(self.progress)
        self.tabs = QtWidgets.QTabWidget()
        prefer_gl = self.settings.value("use_gl", "true") in ("true", True)
        self.view3d = make_view(prefer_gl)
        view_wrap = QtWidgets.QWidget()
        vw = QtWidgets.QVBoxLayout(view_wrap)
        vw.setContentsMargins(0, 0, 0, 0)
        vw.setSpacing(0)
        vbar = QtWidgets.QHBoxLayout()
        vbar.setContentsMargins(8, 6, 8, 6)
        for name in ("iso", "top", "front", "side"):
            b = QtWidgets.QPushButton(name.capitalize())
            b.clicked.connect(lambda _=False, n=name: self.view3d.set_view(n))
            vbar.addWidget(b)
        fit = QtWidgets.QPushButton("Fit")
        fit.clicked.connect(lambda: self.view3d.reset_camera())
        vbar.addWidget(fit)
        self.cb_currents = QtWidgets.QCheckBox("Currents")
        self.cb_currents.setChecked(True)
        self.cb_pattern = QtWidgets.QCheckBox("3D pattern")
        self.cb_pattern.setChecked(True)
        self.cb_currents.toggled.connect(self._view_opts)
        self.cb_pattern.toggled.connect(self._view_opts)
        vbar.setSpacing(6)
        vbar.addWidget(self.cb_currents)
        vbar.addSpacing(12)
        vbar.addWidget(self.cb_pattern)
        vbar.addSpacing(16)
        hint = QtWidgets.QLabel("drag to rotate · wheel to zoom")
        hint.setObjectName("muted")
        hint.setMinimumWidth(10)
        hint.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        vbar.addWidget(hint)
        vw.addLayout(vbar)
        vw.addWidget(self.view3d, 1)
        self.sweepv = SweepView()
        self.smith = SmithView()
        self.patv = PatternView()
        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.tabs.addTab(view_wrap, "3D view")
        self.tabs.addTab(self.sweepv, "SWR / Impedance")
        self.tabs.addTab(self.smith, "Smith chart")
        self.tabs.addTab(self.patv, "Pattern")
        self.tabs.addTab(self.output, "nec2c output")
        rl.addWidget(self.tabs, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([540, 1000])
        self.sweepv.frequencyPicked.connect(self.set_pattern_freq)
        self.view3d.picked.connect(self._picked)
        self.smith.frequencyPicked.connect(self.set_pattern_freq)

        sb = self.statusBar()
        self.status_l = QtWidgets.QLabel("")
        self.status_r = QtWidgets.QLabel("")
        sb.addWidget(self.status_l, 1)
        sb.addPermanentWidget(self.status_r)

    def _build_sim_box(self):
        box = QtWidgets.QGroupBox("Simulation")
        g = QtWidgets.QGridLayout(box)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(6)

        def dspin(lo, hi, dec, suffix=""):
            s = QtWidgets.QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setDecimals(dec)
            s.setSuffix(suffix)
            s.setKeyboardTracking(False)
            s.setMinimumWidth(70)
            s.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
            return s
        self.f_start = dspin(0.001, 1e5, 4, " MHz")
        self.f_stop = dspin(0.001, 1e5, 4, " MHz")
        self.f_steps = QtWidgets.QSpinBox()
        self.f_steps.setRange(2, 2001)
        self.f_steps.setValue(41)
        self.f_pat = dspin(0.001, 1e5, 4, " MHz")
        self.z0 = dspin(1, 1000, 1, " Ω")
        self.z0.setValue(50)
        self.ground = QtWidgets.QComboBox()
        self.ground.setMinimumContentsLength(8)
        self.ground.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.ground.addItems(["Free space", "Perfect ground"] + list(GROUND_PRESETS) + ["Custom real ground"])
        self.eps = dspin(1, 100, 2)
        self.sigma = dspin(0, 10, 4, " S/m")
        self.somm = QtWidgets.QCheckBox("Sommerfeld (accurate near ground)")
        self.somm.setChecked(True)
        self.pat_res = QtWidgets.QComboBox()
        self.pat_res.addItems(["10°", "5°", "3°", "2°"])
        self.pat_res.setCurrentIndex(1)
        self.cb_gain = QtWidgets.QCheckBox("Gain and F/B vs frequency")
        self.cb_gain.setChecked(True)
        self.cb_auto = QtWidgets.QCheckBox("Re-run automatically on edits")
        self.cb_auto.setChecked(True)

        r = 0
        g.addWidget(QtWidgets.QLabel("Sweep"), r, 0)
        g.addWidget(self.f_start, r, 1)
        g.addWidget(QtWidgets.QLabel("to"), r, 2)
        g.addWidget(self.f_stop, r, 3)
        r += 1
        g.addWidget(QtWidgets.QLabel("Points"), r, 0)
        g.addWidget(self.f_steps, r, 1)
        g.addWidget(QtWidgets.QLabel("Z₀"), r, 2)
        g.addWidget(self.z0, r, 3)
        r += 1
        g.addWidget(QtWidgets.QLabel("Pattern at"), r, 0)
        g.addWidget(self.f_pat, r, 1)
        g.addWidget(QtWidgets.QLabel("Step"), r, 2)
        g.addWidget(self.pat_res, r, 3)
        r += 1
        g.addWidget(QtWidgets.QLabel("Ground"), r, 0)
        g.addWidget(self.ground, r, 1, 1, 3)
        r += 1
        self.eps_l = QtWidgets.QLabel("εr / σ")
        g.addWidget(self.eps_l, r, 0)
        g.addWidget(self.eps, r, 1)
        g.addWidget(self.sigma, r, 2, 1, 2)
        r += 1
        g.addWidget(self.somm, r, 0, 1, 4)
        r += 1
        g.addWidget(self.cb_gain, r, 0, 1, 4)
        r += 1
        g.addWidget(self.cb_auto, r, 0, 1, 4)
        r += 1
        btns = QtWidgets.QHBoxLayout()
        self.b_run = QtWidgets.QPushButton("▶  Run  (F5)")
        self.b_run.setObjectName("primary")
        self.b_run.clicked.connect(self.run_all)
        self.b_stop = QtWidgets.QPushButton("Stop")
        self.b_stop.clicked.connect(self.stop)
        btns.addWidget(self.b_run, 1)
        btns.addWidget(self.b_stop)
        g.addLayout(btns, r, 0, 1, 4)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(3, 1)
        self.ground.currentIndexChanged.connect(self._ground_changed)
        self.f_start.valueChanged.connect(lambda _: self._freq_changed())
        self.f_stop.valueChanged.connect(lambda _: self._freq_changed())
        self.f_steps.valueChanged.connect(lambda _: self._freq_changed())
        self.z0.valueChanged.connect(self._z0_changed)
        self.f_pat.valueChanged.connect(lambda v: self.set_pattern_freq(v, from_spin=True))
        for w in (self.eps, self.sigma):
            w.valueChanged.connect(lambda _: self._settings_changed())
        self.somm.toggled.connect(lambda _: self._settings_changed())
        self._ground_changed()
        return box

    def _build_actions(self):
        mb = self.menuBar()
        tb = QtWidgets.QToolBar("Main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(tb)

        def act(text, slot, key=None, tip=None):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if key:
                a.setShortcut(QtGui.QKeySequence(key))
            if tip:
                a.setToolTip(tip)
            return a
        a_new = act("New", self.new_file, "Ctrl+N")
        a_open = act("Open…", self.open_dialog, "Ctrl+O")
        a_save = act("Save", self.save, "Ctrl+S")
        a_saveas = act("Save as…", self.save_as, "Ctrl+Shift+S")
        a_csv = act("Export sweep as CSV…", self.export_csv)
        self.a_export_nec = act("Export NEC deck (.nec)…", self.export_nec)
        a_report = act("Report…", self.show_report, "Ctrl+R", "Printable report with results and finished geometry")
        self.a_to_builder = act("Convert this deck to a builder design", self.convert_to_builder)
        self.a_to_raw = act("Edit as a raw NEC deck", self.detach_to_raw)
        a_png = act("Export current view as PNG…", self.export_png)
        a_quit = act("Quit", self.close, "Ctrl+Q")
        self.a_run = act("Run", self.run_all, "F5", "Sweep + pattern (F5)")
        self.a_sweep = act("Sweep", self.run_sweep, "F7", "Frequency sweep only (F7)")
        self.a_pat = act("Pattern", self.run_pattern, "F8", "Far-field pattern at the selected frequency (F8)")
        a_stop = act("Stop", self.stop, "Esc")
        a_settings = act("Settings…", self.settings_dialog)
        a_about = act("About", self.about)
        a_cards = act("NEC card reference", self.card_help, "Shift+F1")
        a_guide = act("User guide", self.user_guide, "F1")
        a_lic = act("License", self.show_license)

        m = mb.addMenu("&File")
        for a in (a_new, a_open, a_save, a_saveas):
            m.addAction(a)
        self.recent_menu = m.addMenu("Open recent")
        self._fill_recent()
        ex = m.addMenu("Examples")
        if os.path.isdir(EXAMPLES_DIR):
            files = sorted(os.listdir(EXAMPLES_DIR))
            for fn in files:
                if fn.lower().endswith(".nnx"):
                    p = os.path.join(EXAMPLES_DIR, fn)
                    ex.addAction(act(fn[:-4], lambda _=False, p=p: self._confirm_discard() and
                                     self.open_file(p, as_example=True)))
            raw = ex.addMenu("Raw NEC decks")
            for fn in files:
                if fn.lower().endswith(".nec"):
                    p = os.path.join(EXAMPLES_DIR, fn)
                    raw.addAction(act(fn[:-4], lambda _=False, p=p: self._confirm_discard() and
                                      self.open_file(p, as_example=True)))
        m.addSeparator()
        m.addAction(a_report)
        m.addAction(self.a_export_nec)
        m.addAction(a_csv)
        m.addAction(a_png)
        m.addSeparator()
        m.addAction(self.a_to_builder)
        m.addAction(self.a_to_raw)
        m.addSeparator()
        m.addAction(a_quit)
        m = mb.addMenu("&Run")
        for a in (self.a_run, self.a_sweep, self.a_pat, a_stop):
            m.addAction(a)
        m = mb.addMenu("&Tools")
        m.addAction(a_settings)
        m = mb.addMenu("&Help")
        m.addAction(a_guide)
        m.addAction(a_cards)
        m.addSeparator()
        m.addAction(a_lic)
        m.addAction(a_about)

        for a in (a_new, a_open, a_save):
            tb.addAction(a)
        tb.addAction(a_report)
        tb.addSeparator()
        tb.addAction(self.a_run)
        tb.widgetForAction(self.a_run).setObjectName("primary")
        tb.addAction(self.a_sweep)
        tb.addAction(self.a_pat)
        tb.addAction(a_stop)
        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        self.title_lbl = QtWidgets.QLabel("")
        self.title_lbl.setObjectName("muted")
        tb.addWidget(self.title_lbl)

    # -------------------------------------------------------------- files
    def new_file(self):
        if not self._confirm_discard():
            return
        d = B.new_design("New antenna")
        d["variables"] = [["len", "10.3"], ["h", "10"]]
        d["elements"] = [B.new_element("Dipole", "0", "-len/2", "h", "90", "0",
                                       [B.new_item("wire", length="len/2"), B.new_item("feed"),
                                        B.new_item("wire", length="len/2")])]
        d["sim"] = {"f_start": 13.8, "f_stop": 14.6, "steps": 41, "ground": "Average (13, 5 mS/m)"}
        self._example_name = None
        self._set_design(d, None)

    def open_dialog(self):
        if not self._confirm_discard():
            return
        d = self.settings.value("last_dir", os.path.expanduser("~"))
        fn, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open design or NEC deck", d,
            "Designs and decks (*.nnx *.nec *.NEC *.nec2);;NEC Next designs (*.nnx);;"
            "NEC decks (*.nec *.NEC *.nec2 *.txt);;All files (*)")
        if fn:
            self.open_file(fn)

    def open_file(self, fn: str, as_example: bool = False):
        if fn.lower().endswith(".nnx"):
            try:
                d = B.load(fn)
            except (OSError, ValueError) as e:
                QtWidgets.QMessageBox.warning(self, "Open", str(e))
                return
            self._example_name = os.path.basename(fn)[:-4] if as_example else None
            self._set_design(d, None if as_example else fn)
            if not as_example:
                self.settings.setValue("last_dir", os.path.dirname(fn))
                self._add_recent(fn)
            return
        self._set_mode(False)
        try:
            with open(fn, errors="replace") as f:
                text = f.read()
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "Open", str(e))
            return
        self.path = None if as_example else fn
        self._example_name = os.path.basename(fn)[:-4] if as_example else None
        self.sweep = self.pat = None
        self.pattern_freq = None
        self.editor.blockSignals(True)
        self.editor.setPlainText(text)
        self.editor.blockSignals(False)
        deck = Deck(text)
        self._load_settings_from_deck(deck)
        self._dirty = False
        if not as_example:
            self.settings.setValue("last_dir", os.path.dirname(fn))
            self._add_recent(fn)
        self._update_title()
        self._clear_results()
        self.banner_lbl.setText("This is a raw NEC deck. Convert it to a builder design to edit it as a "
                                "point-and-click list with automatic tags and segments.")
        self.banner_btn.setText("Convert to builder")
        self.banner.show()
        self._reparse(refit=True, force_run=True)

    def _load_settings_from_deck(self, deck: Deck):
        hint = deck.frequency_hint()
        if hint:
            lo, hi, n = hint
            if n <= 1 or hi <= lo:
                lo, hi, n = lo * 0.95, lo * 1.05, 41
            self._set_quiet(self.f_start, lo)
            self._set_quiet(self.f_stop, hi)
            self.f_steps.blockSignals(True)
            self.f_steps.setValue(max(2, min(n, 401)))
            self.f_steps.blockSignals(False)
            self._set_quiet(self.f_pat, (lo + hi) / 2)
        gh = deck.ground_hint()
        self.ground.blockSignals(True)
        if not gh or gh["type"] == -1:
            self.ground.setCurrentIndex(0)
        elif gh["type"] == 1:
            self.ground.setCurrentIndex(1)
        else:
            idx = None
            for k, (name, (e, s)) in enumerate(GROUND_PRESETS.items()):
                if abs(e - gh["eps"]) < 1e-6 and abs(s - gh["sigma"]) < 1e-9:
                    idx = k + 2
            if idx is None:
                idx = self.ground.count() - 1
            self.ground.setCurrentIndex(idx)
            self._set_quiet(self.eps, gh["eps"])
            self._set_quiet(self.sigma, gh["sigma"])
            self.somm.blockSignals(True)
            self.somm.setChecked(gh["type"] == 2)
            self.somm.blockSignals(False)
        self.ground.blockSignals(False)
        self._ground_changed(run=False)

    @staticmethod
    def _set_quiet(w, v):
        w.blockSignals(True)
        w.setValue(v)
        w.blockSignals(False)

    def save(self):
        if not self.path:
            return self.save_as()
        try:
            if self.design is not None:
                self.design["sim"] = self._sim_dict()
                B.save(self.design, self.path)
            else:
                with open(self.path, "w", newline="\n") as f:
                    f.write(self.editor.toPlainText())
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "Save", str(e))
            return False
        self._dirty = False
        self._update_title()
        self._add_recent(self.path)
        return True

    def save_as(self):
        d = self.settings.value("last_dir", os.path.expanduser("~"))
        base = getattr(self, "_example_name", None) or (self.design or {}).get("name") or "antenna"
        ext = ".nnx" if self.design is not None else ".nec"
        filt = "NEC Next design (*.nnx)" if self.design is not None else "NEC decks (*.nec)"
        fn, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save", os.path.join(d, base + ext), filt)
        if not fn:
            return False
        if not fn.lower().endswith(ext):
            fn += ext
        self.path = fn
        self.settings.setValue("last_dir", os.path.dirname(fn))
        return self.save()

    def _confirm_discard(self):
        if not self._dirty:
            return True
        r = QtWidgets.QMessageBox.question(
            self, "Unsaved changes", "Save changes to the current deck?",
            QtWidgets.QMessageBox.StandardButton.Save | QtWidgets.QMessageBox.StandardButton.Discard |
            QtWidgets.QMessageBox.StandardButton.Cancel)
        if r == QtWidgets.QMessageBox.StandardButton.Save:
            return bool(self.save())
        return r == QtWidgets.QMessageBox.StandardButton.Discard

    def closeEvent(self, ev):
        if not self._confirm_discard():
            ev.ignore()
            return
        self.settings.setValue("geometry", self.saveGeometry())
        for j in (self.job_geom, self.job_sweep, self.job_pat):
            j.cancel()
        ev.accept()

    def _add_recent(self, fn):
        rec = self.settings.value("recent", []) or []
        if isinstance(rec, str):
            rec = [rec]
        rec = [fn] + [r for r in rec if r != fn]
        self.settings.setValue("recent", rec[:10])
        self._fill_recent()

    def _fill_recent(self):
        self.recent_menu.clear()
        rec = self.settings.value("recent", []) or []
        if isinstance(rec, str):
            rec = [rec]
        for fn in rec:
            a = self.recent_menu.addAction(fn)
            a.triggered.connect(lambda _=False, p=fn: self._confirm_discard() and self.open_file(p))
        self.recent_menu.setEnabled(bool(rec))

    def _update_title(self):
        name = os.path.basename(self.path) if self.path else (getattr(self, "_example_name", None) or "untitled")
        star = " •" if self._dirty else ""
        self.setWindowTitle(f"{name}{star} — NEC Next")
        self.title_lbl.setText(f"{name}{star}   ")

    # -------------------------------------------------------------- editing
    def _text_changed(self):
        if self.design is not None:
            return
        if not self._dirty:
            self._dirty = True
            self._update_title()
        self.parse_timer.start()

    def _replace_line(self, line: int, text: str):
        self.editor.replace_line(line, text)

    @staticmethod
    def _insert_index(lines, section: str) -> int:
        kinds = [l.strip()[:2].upper() for l in lines]
        if section == "geom":
            for i, k in enumerate(kinds):
                if k == "GE":
                    return i
            for i, k in enumerate(kinds):
                if k in ("EX", "LD", "TL", "NT", "FR", "GN", "RP", "XQ", "EN"):
                    return i
            return len(lines)
        last = None
        for i, k in enumerate(kinds):
            if k in ("GE", "GN", "EX", "LD", "TL", "NT", "EK", "KH"):
                last = i
        if last is not None:
            return last + 1
        for i, k in enumerate(kinds):
            if k in ("FR", "RP", "XQ", "EN"):
                return i
        return len(lines)

    def _add_lines(self, new_lines, section: str):
        lines = self.editor.toPlainText().splitlines()
        idx = self._insert_index(lines, section)
        if section == "ctrl" and new_lines and new_lines[0].startswith("LD"):
            # keep LD cards in one group (NEC replaces loads when a second LD group appears)
            kinds = [l.strip()[:2].upper() for l in lines]
            lds = [i for i, k in enumerate(kinds) if k == "LD"]
            if lds:
                idx = lds[-1] + 1
            else:
                firsts = [i for i, k in enumerate(kinds) if k in ("EX", "TL", "NT", "FR", "RP", "XQ", "EN")]
                idx = firsts[0] if firsts else idx
        for k, t in enumerate(new_lines):
            lines.insert(idx + k, t)
        self._set_text_keep_scroll("\n".join(lines) + "\n")

    def _insert_transformer(self, r1: str, r2: str):
        ex = self.deck.first("EX")
        if ex is None or len(ex.ints()) < 3:
            QtWidgets.QMessageBox.information(self, "Transformer", "Add a feed point first, then insert "
                                              "the transformer at it.")
            return
        geom = self.geom_res.geometry if self.geom_res else None
        tag, seg = ex.ints()[1], ex.ints()[2]
        tags = {s.tag for s in geom.segments} if geom else set()
        for c in self.deck.cards:
            if c.kind in ("GW", "GA", "GH") and c.values:
                tags.add(int(round(c.values[0])))
        new_tag = max(tags, default=0) + 1
        # a short 1-segment source wire placed well away from the antenna
        if geom and geom.segments:
            lo, hi = geom.bounds()
            span = float(max(hi - lo)) or 1.0
            x, y, z = float(hi[0] + span), float((lo[1] + hi[1]) / 2), float(max((lo[2] + hi[2]) / 2, span * 0.05))
        else:
            span, x, y, z = 1.0, 2.0, 0.0, 1.0
        L = max(span * 0.01, 0.01)
        try:
            nt = xfmr_line(new_tag, 1, tag, seg, r1, r2, self.deck.symbols)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Transformer", str(e))
            return
        lines = self.editor.toPlainText().splitlines()
        f = list(ex.fields) + ["0"] * 6
        lines[ex.line] = f"EX {f[0]} {new_tag} 1 {f[3]} {f[4]} {f[5]}".rstrip()
        gi = self._insert_index(lines, "geom")
        lines.insert(gi, f"GW {new_tag} 1 {x:.4g} {y:.4g} {z:.4g} {x:.4g} {y:.4g} {z + L:.4g} 1mm"
                         f" ' source wire for transformer")
        ci = self._insert_index(lines, "ctrl")
        lines.insert(ci, nt)
        self.wires._pending_select = "NT"
        self._set_text_keep_scroll("\n".join(lines) + "\n")

    def _add_variable(self, name: str, expr: str):
        if self.design is not None:
            self.design["variables"].append([name, expr])
            self._design_changed()
            return
        lines = self.editor.toPlainText().splitlines()
        last_sy = None
        ce = None
        for i, l in enumerate(lines):
            k = l.strip()[:2].upper()
            if k == "SY":
                last_sy = i
            if k == "CE" and ce is None:
                ce = i
        idx = (last_sy + 1) if last_sy is not None else ((ce + 1) if ce is not None else 0)
        lines.insert(idx, f"SY {name}={expr}")
        self._set_text_keep_scroll("\n".join(lines) + "\n")

    _FIELD_NAMES = {"length": "length", "radius": "radius", "segments": "segments", "yaw": "left/right angle",
                    "pitch": "up/down angle", "turns": "turns", "diameter": "diameter",
                    "segs_per_turn": "segments per turn", "voltage": "voltage", "phase": "phase",
                    "ratio": "transformer ratio", "L": "inductance", "C": "capacitance", "R": "resistance",
                    "X": "reactance", "Q": "Q", "z0": "Z0", "vf": "velocity factor", "x": "start X",
                    "y": "start Y", "z": "start Z", "azimuth": "start azimuth", "elevation": "start elevation"}

    def _variable_usage(self, name: str):
        """Human-readable list of the places a variable is used."""
        import re as _re
        pat = _re.compile(r"(?<![A-Za-z0-9_.])" + _re.escape(name) + r"(?![A-Za-z0-9_])")
        hit = lambda v: isinstance(v, str) and bool(pat.search(v))  # noqa: E731
        out = []
        if self.design is not None:
            d = self.design
            for n, e in d.get("variables", []):
                if n != name and hit(e):
                    out.append(f"Variable {n} = {e}")
            for k, lbl in (("radius", "default wire radius"), ("density", "segments per wavelength")):
                if hit(d.get(k)):
                    out.append(f"Design settings › {lbl}")
            for ei, el in enumerate(d.get("elements", [])):
                en = el.get("name") or f"Element {ei + 1}"
                for k in ("x", "y", "z", "azimuth", "elevation"):
                    if hit(el.get(k)):
                        out.append(f"{en} › {self._FIELD_NAMES[k]}")
                for ii, it in enumerate(el.get("items", [])):
                    for k, v in it.items():
                        if k in ("type", "name", "mode", "hand", "kind", "link", "crossed"):
                            continue
                        if hit(v):
                            out.append(f"{en} › {ii + 1}. {B.title(it)} › {self._FIELD_NAMES.get(k, k)}")
            return out
        for c in self.deck.cards:
            if c.kind == "SY":
                for v in self.deck.variables:
                    if v.name != name and v.line == c.line and hit(v.expr):
                        out.append(f"Variable {v.name} = {v.expr} (line {c.line + 1})")
            elif c.kind not in ("CM", "CE") and any(hit(f) for f in c.fields):
                out.append(f"Line {c.line + 1}: {c.kind} {' '.join(c.fields)}")
        return out

    def _delete_variable(self, name: str):
        import re as _re
        if self._variable_usage(name):
            return
        if self.design is not None:
            self.design["variables"] = [v for v in self.design["variables"] if v[0] != name]
            self._design_changed()
            return
        for v in self.deck.variables:
            if v.name != name:
                continue
            lines = self.editor.toPlainText().splitlines()
            ln = lines[v.line]
            body = ln.strip()[2:]
            parts = [p for p in _re.split(r",(?![^(]*\))", body) if p.strip()]
            keep = [p.strip() for p in parts if p.split("=", 1)[0].strip() != name]
            if keep:
                lines[v.line] = "SY " + ", ".join(keep)
            else:
                del lines[v.line]
            self._set_text_keep_scroll("\n".join(lines) + "\n")
            break

    def _delete_line(self, line: int):
        lines = self.editor.toPlainText().splitlines()
        if 0 <= line < len(lines):
            del lines[line]
            self._set_text_keep_scroll("\n".join(lines) + "\n")

    def _set_text_keep_scroll(self, text):
        sb = self.editor.verticalScrollBar().value()
        cur = self.editor.textCursor()
        c = QtGui.QTextCursor(self.editor.document())
        c.select(QtGui.QTextCursor.SelectionType.Document)
        c.insertText(text)   # keeps undo history
        self.editor.verticalScrollBar().setValue(sb)
        del cur

    def _set_variable(self, name: str, value):
        if self.design is not None:
            for v in self.design["variables"]:
                if v[0] == name:
                    v[1] = value if isinstance(value, str) else f"{value:.6g}"
            self._design_changed()
            return
        new = self.deck.set_variable(name, value)
        for v in self.deck.variables:
            if v.name == name:
                ln = new.splitlines()[v.line]
                self.editor.replace_line(v.line, ln)
                break

    def _highlight(self, h):
        if self.design is not None:
            return
        self.view3d.highlight = h.get("tag") if h else None
        self.view3d.highlight_segs = h.get("segs") if h else None
        self.view3d.rebuild()

    def _reparse(self, refit=False, force_run=False):
        if self.design is not None:
            return
        self._process_deck(Deck(self.editor.toPlainText()), refit, force_run)

    def _process_deck(self, deck, refit=False, force_run=False):
        self.deck = deck
        errs = self.deck.errors
        self.editor.set_errors([e.line for e in errs if e.line is not None])
        if errs:
            self.err.setText("⚠ " + "\n⚠ ".join(
                (f"Line {e.line + 1}: " if e.line is not None else "") + str(e) for e in errs[:4]))
            self.err.show()
        else:
            self.err.hide()
        if self.design is None:
            self.wires.load(self.deck)
            self.vars.load(self.deck)
        if not self.deck.geometry_cards():
            return
        self._refit = refit
        self._t0["geom"] = time.time()
        self.job_geom.start(build_geometry_deck(self.deck), tag=force_run)
        if (self.cb_auto.isChecked() or force_run) and not errs:
            self.run_all()

    # -------------------------------------------------------------- builder mode
    def _set_mode(self, design: bool):
        if self._mode == design:
            return
        self._mode = design
        cur = self.left_tabs.currentIndex()
        self.left_tabs.blockSignals(True)
        while self.left_tabs.count():
            self.left_tabs.removeTab(0)
        if design:
            self.left_tabs.addTab(self.designer, "Builder")
            self.left_tabs.addTab(self.vars, "Variables")
            self.left_tabs.addTab(self.editor, "NEC deck (generated)")
            self.editor.setReadOnly(True)
            self.banner.hide()
        else:
            self.left_tabs.addTab(self.editor, "Deck")
            self.left_tabs.addTab(self.wires, "Structure")
            self.left_tabs.addTab(self.vars, "Variables")
            self.editor.setReadOnly(False)
            self.design = None
            self.gen = None
            self.view3d.highlight = None
            self.view3d.highlight_pts = None
        self.left_tabs.setCurrentIndex(0 if cur < 0 else 0)
        self.left_tabs.blockSignals(False)
        self.a_export_nec.setEnabled(design)
        self.a_to_builder.setEnabled(not design)
        self.a_to_raw.setEnabled(design)

    def _set_design(self, d: dict, path: Optional[str]):
        self._set_mode(True)
        self.design = d
        self.path = path
        self.sweep = self.pat = None
        self.pattern_freq = None
        self._apply_sim_dict(d.get("sim") or {})
        self.designer.set_design(d)
        self._dirty = False
        self._update_title()
        self._clear_results()
        self._regenerate(refit=True, force_run=True)
        self.designer.select(0, None) if d.get("elements") else None

    def _sim_dict(self) -> dict:
        return {"f_start": self.f_start.value(), "f_stop": self.f_stop.value(), "steps": self.f_steps.value(),
                "pattern": self.pattern_freq or self.f_pat.value(), "z0": self.z0.value(),
                "ground": self.ground.currentText(), "eps": self.eps.value(), "sigma": self.sigma.value(),
                "sommerfeld": self.somm.isChecked(), "pattern_step": self.pat_res.currentText()}

    def _apply_sim_dict(self, sd: dict):
        lo = float(sd.get("f_start", 14.0))
        hi = float(sd.get("f_stop", max(lo * 1.05, lo + 0.1)))
        self._set_quiet(self.f_start, lo)
        self._set_quiet(self.f_stop, hi)
        self.f_steps.blockSignals(True)
        self.f_steps.setValue(int(sd.get("steps", 41)))
        self.f_steps.blockSignals(False)
        if sd.get("pattern"):
            self._set_quiet(self.f_pat, float(sd["pattern"]))
            self.pattern_freq = float(sd["pattern"])
        else:
            self._set_quiet(self.f_pat, (lo + hi) / 2)
        self._set_quiet(self.z0, float(sd.get("z0", self.z0.value())))
        g = sd.get("ground", "Free space")
        i = self.ground.findText(g)
        self.ground.blockSignals(True)
        self.ground.setCurrentIndex(i if i >= 0 else 0)
        self.ground.blockSignals(False)
        if "eps" in sd:
            self._set_quiet(self.eps, float(sd["eps"]))
            self._set_quiet(self.sigma, float(sd["sigma"]))
        self.somm.blockSignals(True)
        self.somm.setChecked(bool(sd.get("sommerfeld", True)))
        self.somm.blockSignals(False)
        j = self.pat_res.findText(str(sd.get("pattern_step", "5°")))
        if j >= 0:
            self.pat_res.setCurrentIndex(j)
        self._ground_changed(run=False)

    def _sim_cards(self):
        g = self._ground()
        n = self.f_steps.value()
        lo, hi = self.f_start.value(), self.f_stop.value()
        df = (hi - lo) / (n - 1) if n > 1 else 0
        out = []
        if g.card():
            out.append(g.card())
        out.append(f"FR 0 {n} 0 0 {lo:.6g} {df:.6g}")
        out.append("XQ 0")
        return out

    def _design_changed(self):
        if self.design is None:
            return
        if not self._dirty:
            self._dirty = True
            self._update_title()
        self.gen_timer.start()

    def _freq_changed(self):
        if self.design is not None:
            self.gen_timer.start()
        elif self.cb_auto.isChecked() and self.deck.geometry_cards():
            self.run_sweep()

    def _regenerate(self, refit=False, force_run=False):
        if self.design is None:
            return
        f_max = max(self.f_stop.value(), self.f_start.value())
        f_c = self.pattern_freq or self.f_pat.value() or f_max
        try:
            gen = B.generate(self.design, f_max, f_c, self._sim_cards())
        except B.BuildError as e:
            self.gen = None
            self.designer.set_gen(None, str(e))
            self._show_error(str(e))
            return
        self.gen = gen
        self.designer.set_gen(gen, "")
        self.editor.blockSignals(True)
        self.editor.setPlainText(gen.deck_text)
        self.editor.blockSignals(False)
        sy = "\n".join(f"SY {n}={e}" for n, e in self.design.get("variables", []))
        self.vars.load(Deck(sy + "\nEN\n"))
        self._process_deck(Deck(gen.deck_text), refit, force_run)
        self._design_selected(self.designer.current_selection())

    def _design_selected(self, sel):
        if self.design is None:
            return
        tags, pts = set(), []
        if self.gen and sel:
            ei, ii = sel
            for w in self.gen.wires:
                if w.element == ei and (ii is None or w.item == ii) and w.kind != "source":
                    tags.add(w.tag)
            if ii is not None and not tags and (ei, ii) in self.gen.positions:
                pts.append(self.gen.positions[(ei, ii)][0])
        self.view3d.highlight = tags or None
        self.view3d.highlight_pts = pts
        self.view3d.rebuild()

    def _picked(self, tag):
        if self.design is None:
            if tag is not None:
                self.wires.select_tag(tag) if hasattr(self.wires, "select_tag") else None
            return
        if tag is None or not self.gen or tag not in self.gen.tag_map:
            return
        ei, ii = self.gen.tag_map[tag]
        self.left_tabs.setCurrentWidget(self.designer)
        self.designer.select(ei, ii)

    def _banner_action(self):
        self.convert_to_builder()

    def convert_to_builder(self):
        if self.design is not None:
            return
        deck = Deck(self.editor.toPlainText())
        if deck.errors:
            QtWidgets.QMessageBox.warning(self, "Convert", "Fix the deck errors first.")
            return
        try:
            d, notes = B.import_deck(deck)
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "Convert", f"This deck cannot be converted: {e}")
            return
        msg = "The deck will be rebuilt as a point-and-click design. Tags and segments are then managed " \
              "automatically. The original deck stays on disk untouched."
        if notes:
            msg += "\n\nNotes:\n• " + "\n• ".join(notes[:12])
        r = QtWidgets.QMessageBox.question(self, "Convert to builder design", msg,
                                           QtWidgets.QMessageBox.StandardButton.Ok |
                                           QtWidgets.QMessageBox.StandardButton.Cancel)
        if r != QtWidgets.QMessageBox.StandardButton.Ok:
            return
        d["sim"] = self._sim_dict()
        base = os.path.splitext(os.path.basename(self.path))[0] if self.path else getattr(self, "_example_name", None)
        self._example_name = base
        self._set_design(d, None)
        self._dirty = True
        self._update_title()

    def detach_to_raw(self):
        if self.design is None:
            return
        r = QtWidgets.QMessageBox.question(
            self, "Edit as raw NEC deck",
            "Switch to editing the generated NEC deck by hand? The builder list is left behind "
            "(save the design first if you want to keep it).",
            QtWidgets.QMessageBox.StandardButton.Ok | QtWidgets.QMessageBox.StandardButton.Cancel)
        if r != QtWidgets.QMessageBox.StandardButton.Ok:
            return
        text = self.editor.toPlainText()
        self._set_mode(False)
        self.path = None
        self.editor.blockSignals(True)
        self.editor.setPlainText(text)
        self.editor.blockSignals(False)
        self._dirty = True
        self._update_title()
        self._reparse(refit=False)

    def export_nec(self):
        if not self.gen:
            return
        d = self.settings.value("last_dir", os.path.expanduser("~"))
        base = (self.design or {}).get("name") or "antenna"
        fn, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export NEC deck", os.path.join(d, base + ".nec"),
                                                      "NEC decks (*.nec)")
        if not fn:
            return
        if not fn.lower().endswith(".nec"):
            fn += ".nec"
        with open(fn, "w", newline="\n") as f:
            f.write(self.gen.deck_text)
        self.status_l.setText(f"Exported {fn}")

    def show_report(self):
        from .report import ReportDialog
        ReportDialog(self).exec()

    # -------------------------------------------------------------- running
    def _ground(self) -> Ground:
        i = self.ground.currentIndex()
        if i == 0:
            return Ground("free")
        if i == 1:
            return Ground("perfect")
        return Ground("real", self.eps.value(), self.sigma.value(), self.somm.isChecked())

    def _ground_changed(self, *_, run=True):
        i = self.ground.currentIndex()
        names = list(GROUND_PRESETS)
        real = i >= 2
        custom = i == self.ground.count() - 1
        if real and not custom:
            e, s = GROUND_PRESETS[names[i - 2]]
            self._set_quiet(self.eps, e)
            self._set_quiet(self.sigma, s)
        for w in (self.eps, self.sigma, self.eps_l):
            w.setEnabled(custom)
        self.somm.setEnabled(real)
        if run:
            self._settings_changed()

    def _settings_changed(self):
        if self.design is not None:
            self.gen_timer.start()
            return
        if self.cb_auto.isChecked() and self.deck.geometry_cards():
            self.run_all()

    def _z0_changed(self, v):
        self.sweepv.set_z0(v)
        if self.sweep:
            self.smith.set_data(self.sweep.freqs, v)
            self.smith.set_marker(self.pattern_freq or self.f_pat.value())
        self._update_tiles()

    def _precheck(self) -> bool:
        if not self.exe:
            self._show_error("nec2c was not found. Install it (Raspberry Pi / Linux: sudo apt install nec2c) "
                             "or set its location under Tools ▸ Settings.")
            return False
        if self.deck.errors:
            self._show_error("Fix the deck errors first.")
            return False
        if not self.deck.all("EX"):
            self._show_error("The deck has no EX (excitation) card, so there is nothing to feed.")
            return False
        if self.f_stop.value() <= self.f_start.value():
            self._show_error("Sweep stop frequency must be above the start frequency.")
            return False
        return True

    def run_all(self):
        if not self._precheck():
            return
        self.run_sweep()
        self.run_pattern()

    def run_sweep(self):
        if not self._precheck():
            return
        gstep = 10.0
        txt = build_sweep_deck(self.deck, self._ground(), self.f_start.value(), self.f_stop.value(),
                               self.f_steps.value(), self.cb_gain.isChecked(), gstep)
        self._t0["sweep"] = time.time()
        self.job_sweep.start(txt)
        self._busy()

    def run_pattern(self):
        if not self._precheck():
            return
        f = self.pattern_freq or self.f_pat.value()
        step = float(self.pat_res.currentText().rstrip("°"))
        txt = build_pattern_deck(self.deck, self._ground(), f, step)
        self._t0["pat"] = time.time()
        self.job_pat.start(txt, tag=f)
        self._busy()

    def stop(self):
        for j in (self.job_sweep, self.job_pat, self.job_geom):
            j.cancel()
        self._busy()
        self.status_l.setText("Stopped.")

    def set_pattern_freq(self, f: float, from_spin=False):
        if f <= 0:
            return
        self.pattern_freq = f
        if not from_spin:
            self._set_quiet(self.f_pat, f)
        self.sweepv.set_marker(f)
        self.smith.set_marker(f)
        self._update_tiles()
        if self.deck.all("EX") and not self.deck.errors:
            self.run_pattern()

    def _busy(self):
        busy = self.job_sweep.busy or self.job_pat.busy
        self.progress.setVisible(busy)
        self.b_stop.setEnabled(busy)

    def _show_error(self, msg):
        self.err.setText("⚠ " + msg)
        self.err.show()
        self.status_l.setText(msg.splitlines()[0])

    # -------------------------------------------------------------- results
    def _geom_done(self, res: RunResult, _tag):
        self.geom_res = res
        tags = {}
        for sgm in res.geometry.segments:
            tags[sgm.tag] = tags.get(sgm.tag, 0) + 1
        self.wires.set_tags(tags)
        same = self.pat and len(self.pat.geometry.segments) == len(res.geometry.segments)
        cur = self.pat.freqs[0].currents if (same and self.pat.freqs) else None
        pat = self.pat.freqs[0].pattern if (same and self.pat.freqs) else None
        self.view3d.set_data(res.geometry, self.deck, cur, pat, self._ground().present,
                             refit=getattr(self, "_refit", False))
        self._refit = False
        n = len(res.geometry.segments)
        self.status_r.setText(f"{n} segments · {len(res.geometry.patches)} patches   " if res.geometry.patches
                              else f"{n} segments   ")
        if not self.err.text().startswith("⚠ Line"):
            self.err.hide()

    def _geom_failed(self, msg, _tag):
        self._show_error("Geometry: " + msg)

    def _run_failed(self, msg, _tag):
        self._show_error(msg)
        self.job_sweep.runner = None if not self.job_sweep.busy else self.job_sweep.runner
        self._busy()

    def _sweep_done(self, res: RunResult, _tag):
        self.job_sweep.runner = None
        self.sweep = res
        dt = time.time() - self._t0.get("sweep", time.time())
        z0 = self.z0.value()
        self.sweepv.set_data(res.freqs, z0)
        self.smith.set_data(res.freqs, z0)
        if self.pattern_freq is None and res.freqs:
            # first run: put the marker at the SWR minimum
            best = min(res.freqs, key=lambda r: r.swr(z0))
            self.pattern_freq = best.freq
            self._set_quiet(self.f_pat, best.freq)
            self.run_pattern()
        f = self.pattern_freq or self.f_pat.value()
        self.sweepv.set_marker(f)
        self.smith.set_marker(f)
        self.status_l.setText(f"Sweep: {len(res.freqs)} frequencies in {dt:.2f} s")
        self._update_tiles()
        self._busy()

    def _pat_done(self, res: RunResult, freq):
        self.job_pat.runner = None
        self.pat = res
        dt = time.time() - self._t0.get("pat", time.time())
        fr = res.freqs[0] if res.freqs else None
        if fr:
            self.patv.set_pattern(fr.pattern, fr.freq)
            self.view3d.set_data(res.geometry, self.deck, fr.currents, fr.pattern, self._ground().present,
                                 refit=getattr(self, "_refit", False))
            self._refit = False
        self.output.setPlainText(res.output)
        if not self.job_sweep.busy:
            self.status_l.setText(f"Pattern at {freq:.4f} MHz in {dt:.2f} s"
                                  + (f" · {len(res.warnings)} warning(s)" if res.warnings else ""))
        self._update_tiles()
        self._busy()

    def _clear_results(self):
        for t in (self.t_freq, self.t_z, self.t_swr, self.t_gain, self.t_fb, self.t_el, self.t_eff):
            t.set("—")

    def _update_tiles(self):
        z0 = self.z0.value()
        f = self.pattern_freq or self.f_pat.value()
        self.t_freq.set(f"{f:.3f}", "MHz")
        fr = None
        if self.pat and self.pat.freqs and abs(self.pat.freqs[0].freq - f) < 1e-6:
            fr = self.pat.freqs[0]
        elif self.sweep and self.sweep.freqs:
            fr = min(self.sweep.freqs, key=lambda r: abs(r.freq - f))
        if fr and fr.sources:
            z = fr.z
            self.t_z.set(f"{z.real:.1f} {'+' if z.imag >= 0 else '−'} j{abs(z.imag):.1f}", "Ω")
            s = fr.swr(z0)
            col = theme.GOOD if s <= 1.5 else (theme.WARN if s <= 2.5 else theme.BAD)
            self.t_swr.set(f"{s:.2f}" if s < 100 else "∞", f"{z0:g} Ω ref", col)
        if self.pat and self.pat.freqs and self.pat.freqs[0].pattern is not None:
            p = self.pat.freqs[0]
            m = pattern_metrics(p.pattern)
            self.t_gain.set(f"{m['gain']:.2f}", f"dBi @ az {m['phi']:.0f}°")
            fb = m["fb"]
            self.t_fb.set(f"{fb:.1f}" if fb < 99 else "—", "dB" if fb < 99 else "symmetric")
            self.t_el.set(f"{m['elev']:.0f}°", "elevation of max")
            eff = p.efficiency
            self.t_eff.set(f"{eff:.1f}" if not math.isnan(eff) else "—", "%")

    def _view_opts(self):
        self.view3d.show_currents = self.cb_currents.isChecked()
        self.view3d.show_pattern = self.cb_pattern.isChecked()
        self.view3d.rebuild()

    def _update_status(self):
        if self.exe:
            self.status_l.setText(f"nec2c: {self.exe}")
        else:
            self._show_error("nec2c was not found. Install it (sudo apt install nec2c) or set it in Tools ▸ Settings.")

    # -------------------------------------------------------------- export
    def export_csv(self):
        if not self.sweep:
            QtWidgets.QMessageBox.information(self, "Export", "Run a sweep first.")
            return
        fn, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export sweep", "sweep.csv", "CSV (*.csv)")
        if not fn:
            return
        z0 = self.z0.value()
        with open(fn, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["freq_MHz", "R_ohm", "X_ohm", f"SWR_{z0:g}", "return_loss_dB", "gain_dBi", "FB_dB"])
            for r in self.sweep.freqs:
                if not r.sources:
                    continue
                z = r.z
                g = abs((z - z0) / (z + z0))
                rl = -20 * math.log10(max(g, 1e-9))
                gain = fb = ""
                if r.pattern is not None:
                    m = pattern_metrics(r.pattern)
                    gain, fb = f"{m['gain']:.2f}", f"{m['fb']:.2f}"
                w.writerow([f"{r.freq:.6f}", f"{z.real:.4f}", f"{z.imag:.4f}", f"{r.swr(z0):.4f}",
                            f"{rl:.3f}", gain, fb])
        self.status_l.setText(f"Saved {fn}")

    def export_png(self):
        fn, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export image", "antenna.png", "PNG (*.png)")
        if not fn:
            return
        w = self.tabs.currentWidget()
        if self.tabs.currentIndex() == 0:
            img = self.view3d.grab_image()
            img.save(fn)
        else:
            w.grab().save(fn)
        self.status_l.setText(f"Saved {fn}")

    # -------------------------------------------------------------- dialogs
    def settings_dialog(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Settings")
        lay = QtWidgets.QFormLayout(dlg)
        path = QtWidgets.QLineEdit(self.exe or "")
        browse = QtWidgets.QPushButton("Browse…")
        row = QtWidgets.QHBoxLayout()
        row.addWidget(path, 1)
        row.addWidget(browse)
        lay.addRow("nec2c executable", row)
        browse.clicked.connect(lambda: path.setText(
            QtWidgets.QFileDialog.getOpenFileName(dlg, "nec2c executable")[0] or path.text()))
        use_gl = QtWidgets.QCheckBox("Use OpenGL for the 3D view (restart to apply)")
        use_gl.setChecked(self.settings.value("use_gl", "true") in ("true", True))
        use_gl.setEnabled(HAVE_GL)
        lay.addRow("", use_gl)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                        QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addRow(bb)
        if exec_(dlg):
            self.settings.setValue("nec2c_path", path.text().strip())
            self.settings.setValue("use_gl", "true" if use_gl.isChecked() else "false")
            self.exe = find_nec2c(path.text().strip())
            for j in (self.job_geom, self.job_sweep, self.job_pat):
                j.exe = self.exe
            self._update_status()

    def card_help(self):
        from ..deck import CARD_HELP
        rows = "".join(f"<tr><td><b>{k}</b></td><td>{v}</td></tr>" for k, v in CARD_HELP.items())
        QtWidgets.QMessageBox.information(
            self, "NEC card reference",
            f"<table cellspacing=6>{rows}</table><p>Fields may use SY variables and expressions, "
            "e.g. <code>len/2</code>, <code>h+0.5</code>, <code>12.7mm</code>. Trig functions use degrees.</p>"
            "<p>In the default run mode NEC Next writes the FR, GN and RP cards from the Simulation panel; "
            "your geometry, EX, LD, TL and NT cards are used as written.</p>")

    def user_guide(self):
        from .helpdoc import HelpDialog
        if getattr(self, "_help", None) is None:
            self._help = HelpDialog(self)
        self._help.show()
        self._help.raise_()
        self._help.activateWindow()

    def show_license(self):
        from .helpdoc import APP_DIR
        path = os.path.join(APP_DIR, "LICENSE")
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("License")
        dlg.resize(760, 640)
        lay = QtWidgets.QVBoxLayout(dlg)
        lay.addWidget(QtWidgets.QLabel(
            "NEC Next is free software: you can redistribute it and/or modify it under the terms of the "
            "<b>GNU General Public License</b> as published by the Free Software Foundation, either version 3 "
            "of the License, or (at your option) any later version. It is distributed WITHOUT ANY WARRANTY."))
        lay.itemAt(0).widget().setWordWrap(True)
        txt = QtWidgets.QPlainTextEdit()
        txt.setReadOnly(True)
        try:
            with open(path, encoding="utf-8") as f:
                txt.setPlainText(f.read())
        except OSError:
            txt.setPlainText("The full license text is at https://www.gnu.org/licenses/gpl-3.0.txt")
        lay.addWidget(txt, 1)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.exec() if hasattr(dlg, "exec") else dlg.exec_()

    def about(self):
        from .compat import QT_LIB
        QtWidgets.QMessageBox.about(
            self, "About NEC Next",
            f"<h3>NEC Next {__version__}</h3>"
            "<p>A modern, cross-platform front end for the nec2c antenna simulator.</p>"
            "<p>Author: <b>KB6JRD</b> · 73!</p>"
            "<p><b>License:</b> © 2026 KB6JRD. Free software under the GNU General Public License v3.0 "
            "or later, with NO WARRANTY. See <i>Help ▸ License</i>.</p>"
            "<p><b>Built on:</b> nec2c by Neoklis Kyriazis, 5B4AZ (GPL-3.0), a C translation of NEC-2 "
            f"from Lawrence Livermore National Laboratory; {QT_LIB} / Qt {QtCore.qVersion()} "
            "(LGPL/GPL); pyqtgraph (MIT); NumPy (BSD); PyOpenGL (BSD).</p>"
            f"<p style='color:{theme.MUTED}'>nec2c: {self.exe or 'not found'}<br>"
            f"3D renderer: {'OpenGL' if HAVE_GL else 'software'}</p>")