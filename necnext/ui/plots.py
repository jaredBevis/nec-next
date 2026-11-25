"""Frequency sweep charts, Smith chart and polar pattern plots."""
from __future__ import annotations

import math
from typing import List, Optional

import numpy as np

from .. import bands as hb
from ..engine import FreqResult, Pattern, pattern_metrics, swr_from_z
from . import theme
from .compat import Qt, QtCore, QtGui, QtWidgets, Signal, pg


def _pen(color, width=2.0, style=None):
    p = pg.mkPen(color=color, width=width)
    if style is not None:
        p.setStyle(style)
    return p


def _style_plot(p: pg.PlotItem, left: str, bottom: str = ""):
    p.showGrid(x=True, y=True, alpha=0.12)
    for ax in ("left", "bottom"):
        a = p.getAxis(ax)
        a.setPen(pg.mkPen(theme.BORDER))
        a.setTextPen(pg.mkPen(theme.MUTED))
    p.setLabel("left", left, color=theme.MUTED)
    if bottom:
        p.setLabel("bottom", bottom, color=theme.MUTED)
    p.getViewBox().setMenuEnabled(False)
    p.hideButtons()


# ---------------------------------------------------------------- sweep
class SweepView(QtWidgets.QWidget):
    """SWR, impedance, return loss and gain/F-B against frequency."""
    frequencyPicked = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        top = QtWidgets.QHBoxLayout()
        self.readout = QtWidgets.QLabel("Run a sweep (F7) to see SWR and impedance.")
        self.readout.setObjectName("muted")
        self.readout.setWordWrap(True)
        self.readout.setMinimumWidth(80)
        self.readout.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        top.addWidget(self.readout, 1)
        self.mode = QtWidgets.QComboBox()
        self.mode.addItems(["SWR", "Return loss"])
        self.mode.currentIndexChanged.connect(lambda _: self._redraw())
        self.region = QtWidgets.QComboBox()
        self.region.addItems(["No bands"] + list(hb.BAND_PLANS))
        self.region.setToolTip("Highlight the amateur radio bands for this region")
        self.region.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.region.setMinimumContentsLength(6)
        self.region.setMaximumWidth(220)
        self.region.view().setMinimumWidth(300)
        self._settings = QtCore.QSettings("NecNext", "NecNext")
        reg = self._settings.value("band_region", hb.DEFAULT_REGION)
        i = self.region.findText(reg)
        self.region.setCurrentIndex(i if i >= 0 else 1)
        self.region.currentIndexChanged.connect(self._region_changed)
        top.addWidget(QtWidgets.QLabel("Ham bands:"))
        top.addWidget(self.region)
        top.addSpacing(10)
        top.addWidget(QtWidgets.QLabel("Top chart:"))
        top.addWidget(self.mode)
        lay.addLayout(top)
        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setBackground(theme.PANEL)
        lay.addWidget(self.glw, 1)
        self.p_swr = self.glw.addPlot(row=0, col=0)
        self.p_z = self.glw.addPlot(row=1, col=0)
        self.p_g = self.glw.addPlot(row=2, col=0)
        _style_plot(self.p_swr, "SWR")
        _style_plot(self.p_z, "Impedance (Ω)")
        _style_plot(self.p_g, "Gain (dBi) · F/B (dB)", "Frequency (MHz)")
        self.p_z.setXLink(self.p_swr)
        self.p_g.setXLink(self.p_swr)
        for p in (self.p_swr, self.p_z, self.p_g):
            p.getAxis("left").setWidth(56)
        self.p_z.addLegend(offset=(-10, 6), labelTextColor=theme.MUTED, brush=pg.mkBrush(theme.PANEL + "cc"))
        self.p_g.addLegend(offset=(-10, 6), labelTextColor=theme.MUTED, brush=pg.mkBrush(theme.PANEL + "cc"))
        self.markers = []
        for p in (self.p_swr, self.p_z, self.p_g):
            m = pg.InfiniteLine(angle=90, movable=True, pen=_pen("#ffffff", 1, Qt.PenStyle.DashLine),
                                hoverPen=_pen(theme.ACCENT, 2))
            m.setZValue(50)
            p.addItem(m, ignoreBounds=True)
            m.sigDragged.connect(self._marker_dragged)
            m.sigPositionChangeFinished.connect(self._marker_done)
            self.markers.append(m)
        for p in (self.p_swr, self.p_z, self.p_g):
            p.scene().sigMouseClicked.connect(self._clicked)
        self.freqs: List[FreqResult] = []
        self.z0 = 50.0
        self.sel_freq: Optional[float] = None

    def _region_changed(self, _=None):
        self._settings.setValue("band_region", self.region.currentText())
        self._redraw()

    def _draw_bands(self, f0: float, f1: float, ybot: Optional[float]):
        span = max(f1 - f0, 1e-9)
        for name, lo, hi in hb.bands(self.region.currentText()):
            if hi < f0 - 0.02 * span or lo > f1 + 0.02 * span:
                continue
            for p in (self.p_swr, self.p_z, self.p_g):
                reg = pg.LinearRegionItem(values=(lo, hi), orientation="vertical", movable=False,
                                          brush=pg.mkBrush(251, 191, 36, 26),
                                          pen=pg.mkPen((251, 191, 36, 110), width=1))
                reg.setZValue(-20)
                p.addItem(reg, ignoreBounds=True)
            if ybot is not None:
                t = pg.TextItem(name, color=(251, 191, 36), anchor=(0.5, 1))
                t.setPos((max(lo, f0) + min(hi, f1)) / 2, ybot)
                self.p_swr.addItem(t, ignoreBounds=True)

    def set_data(self, freqs: List[FreqResult], z0: float):
        self.freqs = [f for f in freqs if f.sources]
        self.z0 = z0
        self._redraw()

    def set_z0(self, z0):
        self.z0 = z0
        self._redraw()

    def _redraw(self):
        for p in (self.p_swr, self.p_z, self.p_g):
            for it in list(p.listDataItems()):
                p.removeItem(it)
            for it in [i for i in p.items if isinstance(i, (pg.LinearRegionItem, pg.TextItem))]:
                p.removeItem(it)
        if self.p_z.legend:
            self.p_z.legend.clear()
        if self.p_g.legend:
            self.p_g.legend.clear()
        if not self.freqs:
            return
        f = np.array([r.freq for r in self.freqs])
        z = np.array([r.z for r in self.freqs])
        swr = swr_from_z(z, self.z0)
        if self.mode.currentIndex() == 0:
            # shaded "good" bands
            ymax = float(min(10.0, max(3.0, np.nanmax(np.clip(swr, 1, 10)) * 1.1)))
            for lim, col in ((2.0, (52, 211, 153, 18)), (1.5, (52, 211, 153, 30))):
                band = pg.LinearRegionItem(values=(1.0, lim), orientation="horizontal", movable=False,
                                           brush=pg.mkBrush(col), pen=pg.mkPen(None))
                band.setZValue(-10)
                self.p_swr.addItem(band)
            self.p_swr.plot(f, np.clip(swr, 1, 10), pen=_pen(theme.ACCENT, 2.5),
                            fillLevel=1.0, brush=pg.mkBrush(56, 189, 248, 40))
            self.p_swr.setYRange(1.0, ymax, padding=0.02)
            self._draw_bands(float(f.min()), float(f.max()), 1.0)
            self.p_swr.setLabel("left", f"SWR ({self.z0:g} Ω)", color=theme.MUTED)
            # bandwidth annotation
            ok = swr <= 2.0
            if ok.any():
                fl, fh = f[ok].min(), f[ok].max()
                t = pg.TextItem(f"SWR<2: {fl:.3f}–{fh:.3f} MHz ({(fh - fl) * 1000:.0f} kHz)",
                                color=theme.GOOD, anchor=(0, 0))
                t.setPos(f[0], ymax)
                self.p_swr.addItem(t)
        else:
            gam = np.abs((z - self.z0) / (z + self.z0))
            rl = -20 * np.log10(np.clip(gam, 1e-6, 1))
            self.p_swr.plot(f, rl, pen=_pen(theme.ACCENT2, 2.5))
            self.p_swr.invertY(False)
            self.p_swr.enableAutoRange(axis="y")
            self._draw_bands(float(f.min()), float(f.max()), None)
            self.p_swr.setLabel("left", "Return loss (dB)", color=theme.MUTED)
        self.p_z.plot(f, z.real, pen=_pen(theme.SERIES["r"], 2.2), name="R")
        self.p_z.plot(f, z.imag, pen=_pen(theme.SERIES["x"], 2.2), name="X")
        self.p_z.plot(f, np.abs(z), pen=_pen(theme.SERIES["absz"], 1.2, Qt.PenStyle.DashLine), name="|Z|")
        self.p_z.addLine(y=0, pen=_pen(theme.BORDER, 1))
        pats = [(r.freq, r.pattern) for r in self.freqs if r.pattern is not None]
        if pats:
            ff = np.array([a for a, _ in pats])
            ms = [pattern_metrics(p) for _, p in pats]
            g = np.array([m["gain"] for m in ms])
            fb = np.array([min(m["fb"], 60) for m in ms])
            self.p_g.plot(ff, g, pen=_pen(theme.SERIES["gain"], 2.2), name="Gain dBi",
                          symbol="o", symbolSize=4, symbolBrush=theme.SERIES["gain"], symbolPen=None)
            if np.nanmax(fb) > 0.5:
                self.p_g.plot(ff, fb, pen=_pen(theme.SERIES["fb"], 2.0), name="F/B dB")
            lo = float(np.nanmin(np.concatenate([g, fb]) if np.nanmax(fb) > 0.5 else g))
            hi = float(np.nanmax(np.concatenate([g, fb]) if np.nanmax(fb) > 0.5 else g))
            if hi - lo < 3.0:
                mid = (hi + lo) / 2
                lo, hi = mid - 1.5, mid + 1.5
            self.p_g.setYRange(lo, hi, padding=0.08)
            self.p_g.setVisible(True)
        else:
            self.p_g.setVisible(False)
        self.p_swr.setXRange(f.min(), f.max(), padding=0.01)
        if self.sel_freq is None or not (f.min() <= self.sel_freq <= f.max()):
            self.sel_freq = float(f[int(np.nanargmin(swr))])
        self.set_marker(self.sel_freq)

    def set_marker(self, freq: float):
        self.sel_freq = freq
        for m in self.markers:
            m.blockSignals(True)
            m.setValue(freq)
            m.blockSignals(False)
        self._update_readout(freq)

    def _update_readout(self, freq):
        if not self.freqs:
            return
        f = np.array([r.freq for r in self.freqs])
        i = int(np.argmin(np.abs(f - freq)))
        r = self.freqs[i]
        z = r.z
        swr = r.swr(self.z0)
        best = self.freqs[int(np.argmin([x.swr(self.z0) for x in self.freqs]))]
        self.readout.setText(
            f"<b>{r.freq:.4f} MHz</b> &nbsp; Z = {z.real:.1f} {'+' if z.imag >= 0 else '−'} j{abs(z.imag):.1f} Ω"
            f" &nbsp; SWR {swr:.2f} &nbsp;&nbsp; <span style='color:{theme.MUTED}'>min SWR {best.swr(self.z0):.2f}"
            f" @ {best.freq:.4f} MHz</span>" + self._band_note(r.freq))

    def _band_note(self, f: float) -> str:
        reg = self.region.currentText()
        if reg not in hb.BAND_PLANS:
            return ""
        b = hb.band_at(reg, f)
        if b:
            return f" &nbsp; <span style='color:#fbbf24'>in {b} band</span>"
        return f" &nbsp; <span style='color:{theme.BAD}'>outside ham bands</span>"

    def _marker_dragged(self, m):
        v = m.value()
        for o in self.markers:
            if o is not m:
                o.blockSignals(True)
                o.setValue(v)
                o.blockSignals(False)
        self._update_readout(v)

    def _marker_done(self, m):
        if not self.freqs:
            return
        f = np.array([r.freq for r in self.freqs])
        v = float(f[int(np.argmin(np.abs(f - m.value())))])
        self.set_marker(v)
        self.frequencyPicked.emit(v)

    def _clicked(self, ev):
        if not self.freqs or ev.button() != Qt.MouseButton.LeftButton:
            return
        for p in (self.p_swr, self.p_z, self.p_g):
            if p.isVisible() and p.sceneBoundingRect().contains(ev.scenePos()):
                x = p.vb.mapSceneToView(ev.scenePos()).x()
                f = np.array([r.freq for r in self.freqs])
                v = float(f[int(np.argmin(np.abs(f - x)))])
                self.set_marker(v)
                self.frequencyPicked.emit(v)
                return


# ---------------------------------------------------------------- smith
class SmithView(QtWidgets.QWidget):
    frequencyPicked = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.readout = QtWidgets.QLabel("Reflection coefficient vs frequency.")
        self.readout.setObjectName("muted")
        self.readout.setWordWrap(True)
        self.readout.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        lay.addWidget(self.readout)
        self.pw = pg.PlotWidget()
        self.pw.setBackground(theme.PANEL)
        self.pw.setAspectLocked(True)
        self.pw.hideAxis("left")
        self.pw.hideAxis("bottom")
        self.pw.getViewBox().setMenuEnabled(False)
        self.pw.setXRange(-1.15, 1.15)
        self.pw.setYRange(-1.1, 1.1)
        lay.addWidget(self.pw, 1)
        self._draw_grid()
        self.trace = self.pw.plot([], [], pen=_pen(theme.ACCENT, 2.5))
        self.ends = pg.ScatterPlotItem(size=8, pen=None)
        self.pw.addItem(self.ends)
        self.marker = pg.ScatterPlotItem(size=14, pen=_pen("#ffffff", 2), brush=pg.mkBrush(theme.ACCENT))
        self.pw.addItem(self.marker)
        self.label = pg.TextItem("", color=theme.TEXT, anchor=(0, 1))
        self.pw.addItem(self.label)
        self.freqs: List[FreqResult] = []
        self.z0 = 50.0
        self.pw.scene().sigMouseClicked.connect(self._clicked)

    def _draw_grid(self):
        t = np.linspace(0, 2 * np.pi, 361)
        grid = _pen("#33404f", 1)
        major = _pen("#4b5a6b", 1.3)
        self.pw.plot(np.cos(t), np.sin(t), pen=major)
        self.pw.plot([-1, 1], [0, 0], pen=grid)
        # SWR 2 circle
        g2 = 1 / 3
        self.pw.plot(g2 * np.cos(t), g2 * np.sin(t), pen=_pen(theme.GOOD, 1, Qt.PenStyle.DashLine))
        big = np.concatenate([-np.logspace(3, -3, 300), [0.0], np.logspace(-3, 3, 300)])
        rr = np.concatenate([[0.0], np.logspace(-3, 3, 400)])
        for r in (0.2, 0.5, 1.0, 2.0, 5.0):
            g = ((r + 1j * big) - 1) / ((r + 1j * big) + 1)
            self.pw.plot(g.real, g.imag, pen=major if r == 1 else grid)
            c, rad = r / (1 + r), 1 / (1 + r)
            txt = pg.TextItem(f"{r:g}", color="#6b7a8f", anchor=(1.05, 0))
            txt.setPos(c - rad, 0)
            self.pw.addItem(txt)
        for x in (0.2, 0.5, 1.0, 2.0, 5.0):
            for sgn in (1, -1):
                z = rr + 1j * sgn * x
                g = (z - 1) / (z + 1)
                self.pw.plot(g.real, g.imag, pen=grid)
                lb = complex(0, sgn * x)
                gm = (lb - 1) / (lb + 1)
                txt = pg.TextItem(f"{'+' if sgn > 0 else '−'}j{x:g}", color="#6b7a8f",
                                  anchor=(0.5, 1 if sgn > 0 else 0))
                txt.setPos(gm.real * 1.07, gm.imag * 1.07)
                self.pw.addItem(txt)

    def set_data(self, freqs: List[FreqResult], z0: float):
        self.freqs = [f for f in freqs if f.sources]
        self.z0 = z0
        if not self.freqs:
            self.trace.setData([], [])
            return
        z = np.array([r.z for r in self.freqs])
        g = (z - z0) / (z + z0)
        self.trace.setData(g.real, g.imag)
        self.ends.setData([g.real[0], g.real[-1]], [g.imag[0], g.imag[-1]],
                          brush=[pg.mkBrush(theme.GOOD), pg.mkBrush(theme.BAD)])
        self.readout.setText(f"Normalised to {z0:g} Ω · green dot = {self.freqs[0].freq:.3f} MHz, "
                             f"red = {self.freqs[-1].freq:.3f} MHz · dashed circle = SWR 2")

    def set_marker(self, freq: float):
        if not self.freqs:
            return
        f = np.array([r.freq for r in self.freqs])
        i = int(np.argmin(np.abs(f - freq)))
        z = self.freqs[i].z
        g = (z - self.z0) / (z + self.z0)
        self.marker.setData([g.real], [g.imag])
        self.label.setText(f" {f[i]:.3f} MHz\n {z.real:.1f}{'+' if z.imag >= 0 else '−'}j{abs(z.imag):.1f} Ω")
        self.label.setPos(g.real, g.imag)

    def _clicked(self, ev):
        if not self.freqs:
            return
        p = self.pw.getViewBox().mapSceneToView(ev.scenePos())
        z = np.array([r.z for r in self.freqs])
        g = (z - self.z0) / (z + self.z0)
        i = int(np.argmin(np.abs(g - complex(p.x(), p.y()))))
        self.set_marker(self.freqs[i].freq)
        self.frequencyPicked.emit(self.freqs[i].freq)


# ---------------------------------------------------------------- polar
class PolarPlot(QtWidgets.QWidget):
    """Anti-aliased polar plot of gain in dB, drawn with QPainter."""

    def __init__(self, title: str, kind: str, parent=None):
        super().__init__(parent)
        self.title = title
        self.kind = kind            # "az" or "el"
        self.traces = []            # (angles_deg, dB array, color, name, width)
        self.subtitle = ""
        self.gmax = 0.0
        self.range_db = 40.0
        self.log_scale = True       # ARRL-style log scale
        self.annot = []
        self.full = False           # elevation plot over full circle (free space)
        self.setMinimumSize(170, 170)

    def set_traces(self, traces, gmax, subtitle="", annot=None):
        self.traces = traces
        self.gmax = gmax
        self.subtitle = subtitle
        self.annot = annot or []
        self.update()

    def _r(self, db, R):
        rel = np.clip(np.asarray(db, dtype=float) - self.gmax, -self.range_db, 0)
        if self.log_scale:
            # ARRL log-periodic scale: 0.89^(-dB/2)
            return R * np.power(0.89, -rel / 2.0)
        return R * (1 + rel / self.range_db)

    def _xy(self, ang, r, cx, cy):
        a = np.radians(ang)
        if self.kind == "az":
            # 0 deg (+X) at top, counter-clockwise (+Y) to the left like a map
            return cx - r * np.sin(a), cy - r * np.cos(a)
        # elevation: 0 at right horizon, 90 up, 180 left horizon
        return cx + r * np.cos(a), cy - r * np.sin(a)

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QtGui.QColor(theme.PANEL))
        p.setPen(QtGui.QColor(theme.TEXT))
        f = p.font()
        f.setPointSizeF(10.5)
        f.setBold(True)
        p.setFont(f)
        p.drawText(12, 22, self.title)
        f.setBold(False)
        f.setPointSizeF(8.5)
        p.setFont(f)
        p.setPen(QtGui.QColor(theme.MUTED))
        p.drawText(12, 40, self.subtitle)
        top = 50
        half = self.kind == "el" and not self.full
        if half:
            R = min(w / 2 - 30, (h - top - 40) * 0.95)
            cx, cy = w / 2, top + 10 + R
        else:
            R = min(w, h - top - 30) / 2 - 24
            cx, cy = w / 2, top + 6 + R + 6
        if R < 20:
            return
        grid = QtGui.QPen(QtGui.QColor("#2c3746"), 1)
        p.setPen(grid)
        p.setBrush(Qt.BrushStyle.NoBrush)
        rings = [0, -3, -6, -10, -20, -30] if self.log_scale else [0, -10, -20, -30]
        for db in rings:
            r = float(self._r(self.gmax + db, R))
            if half:
                p.drawArc(QtCore.QRectF(cx - r, cy - r, 2 * r, 2 * r), 0, 180 * 16)
            else:
                p.drawEllipse(QtCore.QPointF(cx, cy), r, r)
            p.setPen(QtGui.QColor("#6b7a8f"))
            p.drawText(QtCore.QPointF(cx + 3, cy - r + 11), f"{db}" if db else "0 dB")
            p.setPen(grid)
        span = 180 if half else 360
        for a in range(0, span + 1 if half else span, 30):
            x, y = self._xy(a, R, cx, cy)
            p.drawLine(QtCore.QPointF(cx, cy), QtCore.QPointF(float(x), float(y)))
            lx, ly = self._xy(a, R + 14, cx, cy)
            p.setPen(QtGui.QColor("#6b7a8f"))
            if self.kind == "az":
                lab = f"{a}°"
            else:
                lab = f"{a if a <= 90 else (180 - a if a <= 270 else a - 360)}°"
            p.drawText(QtCore.QRectF(float(lx) - 20, float(ly) - 8, 40, 16), Qt.AlignmentFlag.AlignCenter, lab)
            p.setPen(grid)
        if self.kind == "el":
            p.setPen(QtGui.QPen(QtGui.QColor("#4b5a6b"), 1.5))
            p.drawLine(QtCore.QPointF(cx - R, cy), QtCore.QPointF(cx + R, cy))
        # traces
        for ang, db, color, name, width in self.traces:
            valid = np.asarray(db) > -900
            x, y = self._xy(np.asarray(ang), self._r(np.where(valid, db, self.gmax - self.range_db), R), cx, cy)
            path = QtGui.QPainterPath()
            path.moveTo(float(x[0]), float(y[0]))
            for k in range(1, len(x)):
                path.lineTo(float(x[k]), float(y[k]))
            if (self.kind == "az" or self.full) and len(x) > 2:
                path.closeSubpath()
            col = QtGui.QColor(color)
            if width >= 2.5:
                fill = QtGui.QColor(col)
                fill.setAlpha(40)
                p.setBrush(fill)
            else:
                p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QtGui.QPen(col, width))
            p.drawPath(path)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # legend
        lx = 12
        ly = h - 12
        for _, _, color, name, _w in self.traces:
            p.setPen(QtGui.QPen(QtGui.QColor(color), 3))
            p.drawLine(QtCore.QPointF(lx, ly - 4), QtCore.QPointF(lx + 16, ly - 4))
            p.setPen(QtGui.QColor(theme.MUTED))
            p.drawText(QtCore.QPointF(lx + 20, ly), name)
            lx += 30 + p.fontMetrics().horizontalAdvance(name)
        for i, a in enumerate(self.annot):
            p.setPen(QtGui.QColor(theme.TEXT))
            p.drawText(QtCore.QRectF(w - 220, h - 16 - 16 * (len(self.annot) - 1 - i), 210, 16),
                       Qt.AlignmentFlag.AlignRight, a)
        p.end()


class PatternView(QtWidgets.QWidget):
    """Azimuth and elevation cuts through the 3-D far-field pattern."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        bar = QtWidgets.QHBoxLayout()
        self.info = QtWidgets.QLabel("Run a pattern (F8) to see azimuth and elevation plots.")
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)
        self.info.setMinimumWidth(60)
        self.info.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        bar.addWidget(self.info, 1)
        bar.addWidget(QtWidgets.QLabel("Elev. cut"))
        self.el_spin = QtWidgets.QDoubleSpinBox()
        self.el_spin.setRange(-90, 90)
        self.el_spin.setSuffix("°")
        self.el_spin.setSpecialValueText("max")
        self.el_spin.setMinimum(-91)
        self.el_spin.setValue(-91)
        self.el_spin.setToolTip("Elevation angle for the azimuth plot ('max' follows the main lobe)")
        bar.addWidget(self.el_spin)
        self.pol = QtWidgets.QComboBox()
        self.pol.addItems(["Total", "Total + V + H"])
        bar.addWidget(self.pol)
        self.scale = QtWidgets.QComboBox()
        self.scale.addItems(["ARRL log", "Linear dB"])
        bar.addWidget(self.scale)
        lay.addLayout(bar)
        h = QtWidgets.QHBoxLayout()
        self.az = PolarPlot("Azimuth", "az")
        self.el = PolarPlot("Elevation", "el")
        h.addWidget(self.az, 1)
        h.addWidget(self.el, 1)
        lay.addLayout(h, 1)
        self.pattern: Optional[Pattern] = None
        self.freq = 0.0
        for w in (self.pol, self.scale):
            w.currentIndexChanged.connect(lambda _: self._redraw())
        self.el_spin.valueChanged.connect(lambda _: self._redraw())

    def set_pattern(self, pat: Optional[Pattern], freq: float):
        self.pattern = pat
        self.freq = freq
        self._redraw()

    def _redraw(self):
        p = self.pattern
        if p is None:
            return
        m = pattern_metrics(p)
        log = self.scale.currentIndex() == 0
        self.az.log_scale = self.el.log_scale = log
        gmax = m["gain"]
        th_cut = m["theta"] if self.el_spin.value() < -90 else 90.0 - self.el_spin.value()
        it = int(np.argmin(np.abs(p.theta - th_cut)))
        el_cut = 90 - p.theta[it]
        self.el.full = bool(p.theta.max() > 90.5)
        sets = [("total", theme.SERIES["total"], "Total", 2.6)]
        if self.pol.currentIndex() == 1:
            sets += [("vert", theme.SERIES["vert"], "Vertical", 1.5), ("horiz", theme.SERIES["horiz"], "Horizontal", 1.5)]
        az_tr = []
        phis = p.phi
        for key, col, name, wd in sets:
            arr = getattr(p, key)[it, :]
            az_tr.append((phis, arr, col, name, wd))
        sub = f"{self.freq:.3f} MHz · elevation {el_cut:.0f}° · outer ring {gmax:.2f} dBi"
        self.az.set_traces(az_tr, gmax, sub, [f"Max {np.max(p.total[it]):.2f} dBi",
                                              f"F/B {min(m['fb'], 99):.1f} dB · F/R {m['fr']:.1f} dB",
                                              f"−3 dB width {m['bw_az']:.0f}°" if not math.isnan(m['bw_az']) else ""])
        # elevation: phi of max and its opposite to make a full 0..180 cut
        ph = m["phi"]
        el_tr = []
        for key, col, name, wd in sets:
            arr = getattr(p, key)
            ip = int(np.argmin(np.abs(((p.phi - ph) + 180) % 360 - 180)))
            ib = int(np.argmin(np.abs(((p.phi - ph - 180) + 180) % 360 - 180)))
            th = p.theta
            # angle from the right-hand horizon, counter-clockwise
            ang = np.concatenate([(90 - th) % 360.0, 90 + th])
            val = np.concatenate([arr[:, ip], arr[:, ib]])
            ang, uniq = np.unique(np.round(ang, 4), return_index=True)
            val = val[uniq]
            if self.el.full:
                ang = np.append(ang, ang[0] + 360)
                val = np.append(val, val[0])
            el_tr.append((ang, val, col, name, wd))
        self.el.set_traces(el_tr, gmax, f"azimuth {ph:.0f}° (right) / {(ph + 180) % 360:.0f}° (left)",
                           [f"Take-off {m['elev']:.0f}°",
                            f"−3 dB width {m['bw_el']:.0f}°" if not math.isnan(m['bw_el']) else ""])
        self.info.setText(f"<b>{gmax:.2f} dBi</b> at azimuth {ph:.0f}°, elevation {m['elev']:.0f}°"
                          f" &nbsp;·&nbsp; {p.gain_type.lower()} gain")
