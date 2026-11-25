"""3-D antenna view: wires coloured by current, feeds/loads, ground and 3-D pattern.

Two back-ends share one interface:
  * GLAntennaView  – pyqtgraph.opengl (fast, shaded 3-D pattern surface)
  * PainterAntennaView – pure QPainter (works without OpenGL, e.g. over VNC)
"""
from __future__ import annotations

import math
import os
from typing import List, Optional, Tuple

import numpy as np

from ..deck import Deck
from ..engine import Geometry, Pattern
from . import theme
from .compat import Qt, QtCore, QtGui, QtWidgets, Signal, event_pos

try:  # OpenGL is optional
    if os.environ.get("NECNEXT_NO_GL"):
        raise ImportError("disabled")
    import OpenGL  # noqa: F401
    import pyqtgraph.opengl as gl
    HAVE_GL = True
except Exception:  # pragma: no cover
    gl = None
    HAVE_GL = False


# ------------------------------------------------------------------ helpers
def seg_index(geom: Geometry, tag: int, seg: int) -> Optional[int]:
    """Absolute segment index (0-based) from NEC tag + segment-within-tag."""
    if tag == 0:
        k = seg - 1
        return k if 0 <= k < len(geom.segments) else None
    n = 0
    for i, s in enumerate(geom.segments):
        if s.tag == tag:
            n += 1
            if n == seg:
                return i
    return None


def xfmr_sources(deck: Optional[Deck]) -> dict:
    """Tags of stand-alone transformer source wires -> (tag, seg) of the antenna side."""
    out = {}
    if deck is None:
        return out
    for c in deck.all("NT"):
        if "xfmr" not in (getattr(c, "comment", "") or ""):
            continue
        iv = c.ints()
        if len(iv) >= 4 and iv[0] != iv[2]:
            out[iv[0]] = (iv[2], iv[3])
    return out


def strip_sources(geom: Geometry, deck: Optional[Deck], currents=None):
    """Hide transformer source wires (they sit far from the antenna) from the view."""
    hidden = xfmr_sources(deck)
    if not hidden or geom is None:
        return geom, currents
    keep = [i for i, s in enumerate(geom.segments) if s.tag not in hidden]
    g = Geometry([geom.segments[i] for i in keep], list(geom.patches))
    if currents is not None and len(currents) == len(geom.segments):
        currents = np.asarray(currents)[keep]
    return g, currents


def markers(geom: Geometry, deck: Optional[Deck]):
    """Return (feeds, loads, tlines) as lists of points / point pairs."""
    feeds, loads, tls = [], [], []
    if deck is None or not geom.segments:
        return feeds, loads, tls
    mid = lambda i: (geom.segments[i].p1 + geom.segments[i].p2) / 2  # noqa: E731
    xs = xfmr_sources(deck)
    for c in deck.all("EX"):
        iv = c.ints()
        if len(iv) >= 3 and iv[0] in (0, 5):
            tag, sg = xs.get(iv[1], (iv[1], iv[2]))
            k = seg_index(geom, tag, sg)
            if k is not None:
                feeds.append(mid(k))
    for c in deck.all("LD"):
        iv = c.ints()
        if len(iv) < 4 or iv[0] == -1:
            continue
        tag, s1, s2 = iv[1], iv[2], iv[3]
        if s1 == 0 and s2 == 0:
            idx = [i for i, s in enumerate(geom.segments) if tag == 0 or s.tag == tag]
            if len(idx) > 40:
                continue  # whole-structure conductivity; don't clutter
        else:
            idx = [seg_index(geom, tag, k) for k in range(s1, (s2 or s1) + 1)]
        loads += [mid(i) for i in idx if i is not None]
    for c in deck.all("TL"):
        iv = c.ints()
        if len(iv) >= 4:
            a, b = seg_index(geom, iv[0], iv[1]), seg_index(geom, iv[2], iv[3])
            if a is not None and b is not None:
                tls.append((mid(a), mid(b)))
    return feeds, loads, tls


def pattern_mesh(p: Pattern, scale: float, floor_db: float = 40.0):
    """Vertices (N,3), faces (M,3) and per-vertex normalised gain (N,)."""
    th = np.radians(p.theta)
    ph = np.radians(p.phi)
    g = np.where(p.total > -900, p.total, -999.0)
    gmax = np.nanmax(g)
    rel = np.clip(g - gmax, -floor_db, 0.0)
    r = scale * np.power(0.89, -rel / 2.0)          # ARRL-style log radius
    r = (r - scale * 0.89 ** (floor_db / 2)) / (1 - 0.89 ** (floor_db / 2))
    r = np.clip(r, 0, None)
    T, P = np.meshgrid(th, ph, indexing="ij")
    x = r * np.sin(T) * np.cos(P)
    y = r * np.sin(T) * np.sin(P)
    z = r * np.cos(T)
    verts = np.stack([x, y, z], axis=-1).reshape(-1, 3)
    nt, nph = T.shape
    faces = []
    for i in range(nt - 1):
        for j in range(nph - 1):
            a = i * nph + j
            b = a + 1
            c = a + nph
            d = c + 1
            faces.append((a, b, d))
            faces.append((a, d, c))
    faces = np.array(faces, dtype=np.int32)
    # drop degenerate triangles (poles, zero-gain points) to keep normals finite
    v0, v1, v2 = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    area = np.linalg.norm(np.cross(v1 - v0, v2 - v0), axis=1)
    faces = faces[area > 1e-12 * max(scale, 1e-9) ** 2]
    return verts, faces, ((rel + floor_db) / floor_db).reshape(-1), T.shape


class Legend(QtWidgets.QWidget):
    """Small colour bar overlay."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title, self.lo, self.hi = "", "", ""
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.resize(190, 52)

    def set(self, title, lo, hi):
        self.title, self.lo, self.hi = title, lo, hi
        self.setVisible(bool(title))
        self.update()

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        bg = QtGui.QColor(theme.PANEL)
        bg.setAlpha(220)
        p.setBrush(bg)
        p.setPen(QtGui.QColor(theme.BORDER))
        p.drawRoundedRect(QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 8, 8)
        grad = QtGui.QLinearGradient(10, 0, self.width() - 10, 0)
        cm = theme.colormap(np.linspace(0, 1, 6))
        for k, c in enumerate(cm):
            grad.setColorAt(k / 5, QtGui.QColor.fromRgbF(*c))
        p.setPen(QtGui.QColor(theme.TEXT))
        p.drawText(10, 16, self.title)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(QtCore.QRectF(10, 22, self.width() - 20, 8), 3, 3)
        p.setPen(QtGui.QColor(theme.MUTED))
        p.drawText(10, 46, self.lo)
        fm = p.fontMetrics()
        p.drawText(self.width() - 10 - fm.horizontalAdvance(self.hi), 46, self.hi)
        p.end()


class _Base:
    """State shared by both back-ends."""

    def _init_state(self):
        self.geom: Optional[Geometry] = None
        self.deck: Optional[Deck] = None
        self.currents: Optional[np.ndarray] = None
        self.pattern: Optional[Pattern] = None
        self.ground = False
        self.show_currents = True
        self.show_pattern = True
        self.highlight: Optional[int] = None
        self.highlight_segs = None     # list of (tag, seg) to emphasise

    def span(self) -> float:
        if not self.geom or not (self.geom.segments or self.geom.patches):
            return 1.0
        lo, hi = self.geom.bounds()
        return float(max(np.max(hi - lo), 1e-3))

    def centre(self) -> np.ndarray:
        if not self.geom or not (self.geom.segments or self.geom.patches):
            return np.zeros(3)
        lo, hi = self.geom.bounds()
        return (lo + hi) / 2

    def wire_colors(self) -> np.ndarray:
        n = len(self.geom.segments)
        acc = QtGui.QColor(theme.ACCENT)
        base = np.tile([acc.redF(), acc.greenF(), acc.blueF(), 1.0], (n, 1))
        if self.show_currents and self.currents is not None and len(self.currents) == n:
            mag = np.abs(self.currents)
            mx = mag.max() if mag.max() > 0 else 1
            base = theme.colormap(mag / mx)
        if self.highlight is not None:
            hl = self.highlight if isinstance(self.highlight, (set, frozenset, list, tuple)) else {self.highlight}
            for i, s in enumerate(self.geom.segments):
                if s.tag in hl:
                    base[i] = (1, 1, 1, 1)
        for t, sg in (self.highlight_segs or []):
            k = seg_index(self.geom, t, sg)
            if k is not None:
                base[k] = (1, 1, 1, 1)
        return base

    def _pick_tag(self, px: float, py: float, xy1, xy2, tol: float = 9.0):
        """Return the tag of the segment nearest the click (screen coords), or None."""
        if not self.geom or not self.geom.segments:
            return None
        x1, y1 = xy1
        x2, y2 = xy2
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        t = np.where(L2 > 1e-9, ((px - x1) * dx + (py - y1) * dy) / np.where(L2 > 1e-9, L2, 1), 0)
        t = np.clip(t, 0, 1)
        d = np.hypot(x1 + t * dx - px, y1 + t * dy - py)
        k = int(np.argmin(d))
        if d[k] > tol:
            return None
        return self.geom.segments[k].tag

    def _highlight_points(self):
        pts = [np.asarray(q, float) for q in (getattr(self, "highlight_pts", None) or [])]
        if self.geom and self.highlight_segs:
            for t, sg in self.highlight_segs:
                k = seg_index(self.geom, t, sg)
                if k is not None:
                    s = self.geom.segments[k]
                    pts.append((s.p1 + s.p2) / 2)
        return pts

    def legend_text(self):
        if self.show_pattern and self.pattern is not None:
            gmax = float(np.nanmax(self.pattern.total))
            return ("Pattern gain", f"{gmax - 40:.0f} dBi", f"{gmax:.1f} dBi")
        if self.show_currents and self.currents is not None and self.geom and \
                len(self.currents) == len(self.geom.segments):
            mx = float(np.max(np.abs(self.currents)))
            return ("Current |I|", "0", f"{mx * 1000:.3g} mA")
        return ("", "", "")


# ------------------------------------------------------------------ GL view
if HAVE_GL:
    class GLAntennaView(QtWidgets.QWidget, _Base):
        picked = Signal(object)

        def __init__(self, parent=None):
            QtWidgets.QWidget.__init__(self, parent)
            self._init_state()
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            self.view = gl.GLViewWidget()
            self.view.installEventFilter(self)
            self._press = None
            self.view.setBackgroundColor(QtGui.QColor("#0b0f14"))
            lay.addWidget(self.view)
            self.items = []
            self.legend = Legend(self)
            self.legend.move(12, 12)
            self.legend.hide()
            self._fitted = False

        def set_data(self, geom, deck, currents=None, pattern=None, ground=False, refit=False):
            old_span = self.span() if self.geom else None
            geom, currents = strip_sources(geom, deck, currents)
            self.geom, self.deck, self.currents, self.pattern, self.ground = geom, deck, currents, pattern, ground
            if old_span and not refit:
                r = self.span() / old_span
                refit = r > 1.3 or r < 0.7
            if refit:
                self._fitted = False
            self.rebuild()

        def rebuild(self):
            for it in self.items:
                self.view.removeItem(it)
            self.items = []
            if not self.geom:
                return
            span = self.span()
            c = self.centre()
            # ground / reference grid
            g = gl.GLGridItem()
            gs = 10 ** math.ceil(math.log10(span * 2.2))
            step = gs / 20
            g.setSize(gs, gs)
            g.setSpacing(step, step)
            g.setColor((60, 80, 100, 90) if not self.ground else (52, 211, 153, 70))
            g.translate(c[0], c[1], 0 if self.ground else c[2] - span * 0.55)
            self._add(g)
            # axes
            L = span * 0.25
            o = np.array([c[0] - span * 0.6, c[1] - span * 0.6, 0 if self.ground else c[2] - span * 0.55])
            for d, col, lbl in (((1, 0, 0), (0.97, 0.44, 0.44, 1), "X"),
                                ((0, 1, 0), (0.20, 0.83, 0.60, 1), "Y"),
                                ((0, 0, 1), (0.22, 0.74, 0.97, 1), "Z")):
                e = o + np.array(d) * L
                self._add(gl.GLLinePlotItem(pos=np.array([o, e]), color=col, width=2, antialias=True))
                if hasattr(gl, "GLTextItem"):
                    t = gl.GLTextItem(pos=e + np.array(d) * L * 0.12, text=lbl,
                                      color=QtGui.QColor.fromRgbF(*col))
                    self._add(t)
            # wires
            segs = self.geom.segments
            if segs:
                pos = np.empty((2 * len(segs), 3))
                pos[0::2] = [s.p1 for s in segs]
                pos[1::2] = [s.p2 for s in segs]
                cols = np.repeat(self.wire_colors(), 2, axis=0)
                self._add(gl.GLLinePlotItem(pos=pos, color=cols, width=4, mode="lines", antialias=True))
                # segment joints (subtle)
                self._add(gl.GLScatterPlotItem(pos=pos, size=3, color=(1, 1, 1, 0.25), pxMode=True))
            for pt in self.geom.patches:
                a = math.sqrt(pt.area) / 2
                q = [pt.center + (sx * pt.t1 + sy * pt.t2) * a for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
                m = gl.GLMeshItem(vertexes=np.array(q), faces=np.array([[0, 1, 2], [0, 2, 3]]),
                                  color=(0.6, 0.65, 0.75, 0.6), glOptions="translucent", drawEdges=True,
                                  edgeColor=(0.8, 0.85, 0.9, 1))
                self._add(m)
            feeds, loads, tls = markers(self.geom, self.deck)
            if feeds:
                self._add(gl.GLScatterPlotItem(pos=np.array(feeds), size=16, color=(0.97, 0.36, 0.36, 1), pxMode=True))
            if loads:
                self._add(gl.GLScatterPlotItem(pos=np.array(loads), size=10, color=(0.98, 0.75, 0.14, 1), pxMode=True))
            hp = self._highlight_points()
            if hp:
                self._add(gl.GLScatterPlotItem(pos=np.array(hp), size=24, color=(1, 1, 1, 0.45), pxMode=True))
            for a, b in tls:
                self._add(gl.GLLinePlotItem(pos=np.array([a, b]), color=(0.65, 0.55, 0.98, 1), width=2, antialias=True))
            # pattern
            if self.show_pattern and self.pattern is not None:
                verts, faces, t, shape = pattern_mesh(self.pattern, span * 0.75)
                verts = verts + c
                cols = theme.colormap(t)
                cols[:, 3] = 0.55
                mesh = gl.GLMeshItem(vertexes=verts, faces=faces, vertexColors=cols, smooth=True,
                                     drawEdges=False, glOptions="translucent")
                self._add(mesh)
                # wireframe lines along theta/phi for shape readability
                grid = verts.reshape(shape[0], shape[1], 3)
                lines = []
                for i in range(0, shape[0], max(1, shape[0] // 12)):
                    row = grid[i]
                    lines += [row[:-1], row[1:]]
                for j in range(0, shape[1], max(1, shape[1] // 24)):
                    col = grid[:, j]
                    lines += [col[:-1], col[1:]]
                if lines:
                    segs_a = np.concatenate(lines[0::2])
                    segs_b = np.concatenate(lines[1::2])
                    pos = np.empty((2 * len(segs_a), 3))
                    pos[0::2], pos[1::2] = segs_a, segs_b
                    self._add(gl.GLLinePlotItem(pos=pos, color=(1, 1, 1, 0.12), width=1, mode="lines",
                                                antialias=True))
            lt = self.legend_text()
            self.legend.set(*lt)
            self.legend.raise_()
            if not self._fitted:
                self.reset_camera()

        def reset_camera(self):
            span = self.span()
            c = self.centre()
            self.view.opts["center"] = QtGui.QVector3D(float(c[0]), float(c[1]), float(c[2]))
            self.view.setCameraPosition(distance=span * 2.6, elevation=22, azimuth=-60)
            self._fitted = True

        def set_view(self, which: str):
            presets = {"top": (90, -90), "front": (0, -90), "side": (0, 0), "iso": (22, -60)}
            el, az = presets[which]
            self.view.setCameraPosition(elevation=el, azimuth=az)

        def _add(self, it):
            self.view.addItem(it)
            self.items.append(it)

        def eventFilter(self, obj, ev):
            et = ev.type()
            if et == QtCore.QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
                self._press = event_pos(ev)
            elif et == QtCore.QEvent.Type.MouseButtonRelease and self._press is not None:
                p = event_pos(ev)
                if (p - self._press).manhattanLength() < 4:
                    self._click(p.x(), p.y())
                self._press = None
            return False

        def _screen(self, pts):
            m = None
            try:
                vp = self.view.getViewport()
                m = self.view.projectionMatrix(vp, vp)          # pyqtgraph >= 0.13.4
            except TypeError:
                try:
                    m = self.view.projectionMatrix()            # older pyqtgraph
                except Exception:  # noqa: BLE001
                    return None
            except Exception:  # noqa: BLE001
                return None
            m = m * self.view.viewMatrix()
            w, h = self.view.width(), self.view.height()
            xs, ys = [], []
            for q in pts:
                v = m.map(QtGui.QVector3D(float(q[0]), float(q[1]), float(q[2])))
                xs.append((v.x() + 1) / 2 * w)
                ys.append((1 - v.y()) / 2 * h)
            return np.array(xs), np.array(ys)

        def _click(self, x, y):
            if not self.geom or not self.geom.segments:
                return
            a = self._screen([s.p1 for s in self.geom.segments])
            b = self._screen([s.p2 for s in self.geom.segments])
            if a is None:
                return
            self.picked.emit(self._pick_tag(x, y, a, b))

        def grab_image(self):
            return self.view.grabFramebuffer()


# ------------------------------------------------------------------ painter
class PainterAntennaView(QtWidgets.QWidget, _Base):
    picked = Signal(object)

    def __init__(self, parent=None):
        QtWidgets.QWidget.__init__(self, parent)
        self._init_state()
        self.yaw, self.pitch, self.zoom = -60.0, 22.0, 1.0
        self.pan = QtCore.QPointF(0, 0)
        self._drag = None
        self.legend = Legend(self)
        self.legend.move(12, 12)
        self.legend.hide()
        self.setMinimumSize(200, 200)

    def set_data(self, geom, deck, currents=None, pattern=None, ground=False, refit=False):
        old_span = self.span() if self.geom else None
        geom, currents = strip_sources(geom, deck, currents)
        self.geom, self.deck, self.currents, self.pattern, self.ground = geom, deck, currents, pattern, ground
        if old_span and not refit:
            r = self.span() / old_span
            refit = r > 1.3 or r < 0.7
        if refit:
            self.reset_camera()
        self.legend.set(*self.legend_text())
        self.update()

    def rebuild(self):
        self.legend.set(*self.legend_text())
        self.update()

    def reset_camera(self):
        self.yaw, self.pitch, self.zoom = -60.0, 22.0, 1.0
        self.pan = QtCore.QPointF(0, 0)
        self.update()

    def set_view(self, which):
        self.pitch, self.yaw = {"top": (90, -90), "front": (0, -90), "side": (0, 0), "iso": (22, -60)}[which]
        self.update()

    def grab_image(self):
        return self.grab().toImage()

    def _proj(self, pts: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        c = self.centre()
        p = np.atleast_2d(pts) - c
        # camera looks from azimuth=yaw, elevation=pitch (same convention as GL view)
        az, el = math.radians(self.yaw), math.radians(self.pitch)
        # view direction vector from centre to eye
        right = np.array([-math.sin(az), math.cos(az), 0])
        fwd = np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])
        up = np.cross(fwd, right)
        x = p @ right
        y = p @ up
        depth = p @ fwd
        s = self.zoom * min(self.width(), self.height()) * 0.8 / self.span()
        return self.width() / 2 + self.pan.x() + x * s, self.height() / 2 + self.pan.y() - y * s, depth

    def paintEvent(self, ev):
        qp = QtGui.QPainter(self)
        qp.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        qp.fillRect(self.rect(), QtGui.QColor("#0b0f14"))
        if not self.geom:
            qp.setPen(QtGui.QColor(theme.MUTED))
            qp.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No geometry")
            return
        span = self.span()
        c = self.centre()
        zg = 0 if self.ground else c[2] - span * 0.55
        # grid
        gs = 10 ** math.ceil(math.log10(span * 2.2))
        n = 10
        qp.setPen(QtGui.QPen(QtGui.QColor(52, 211, 153, 60) if self.ground else QtGui.QColor(60, 80, 100, 110), 1))
        for k in range(-n, n + 1):
            v = k * gs / (2 * n)
            a = np.array([[c[0] + v, c[1] - gs / 2, zg], [c[0] + v, c[1] + gs / 2, zg],
                          [c[0] - gs / 2, c[1] + v, zg], [c[0] + gs / 2, c[1] + v, zg]])
            x, y, _ = self._proj(a)
            qp.drawLine(QtCore.QPointF(x[0], y[0]), QtCore.QPointF(x[1], y[1]))
            qp.drawLine(QtCore.QPointF(x[2], y[2]), QtCore.QPointF(x[3], y[3]))
        # pattern wireframe
        if self.show_pattern and self.pattern is not None:
            verts, faces, t, shape = pattern_mesh(self.pattern, span * 0.75)
            verts = verts + c
            x, y, d = self._proj(verts)
            X, Y = x.reshape(shape), y.reshape(shape)
            cols = theme.colormap(t).reshape(shape[0], shape[1], 4)
            for i in range(shape[0]):
                for j in range(shape[1] - 1):
                    cc = cols[i, j]
                    qp.setPen(QtGui.QPen(QtGui.QColor.fromRgbF(cc[0], cc[1], cc[2], 0.55), 1))
                    qp.drawLine(QtCore.QPointF(X[i, j], Y[i, j]), QtCore.QPointF(X[i, j + 1], Y[i, j + 1]))
            for j in range(0, shape[1], 2):
                for i in range(shape[0] - 1):
                    cc = cols[i, j]
                    qp.setPen(QtGui.QPen(QtGui.QColor.fromRgbF(cc[0], cc[1], cc[2], 0.45), 1))
                    qp.drawLine(QtCore.QPointF(X[i, j], Y[i, j]), QtCore.QPointF(X[i + 1, j], Y[i + 1, j]))
        # wires sorted back-to-front
        segs = self.geom.segments
        if segs:
            p1 = np.array([s.p1 for s in segs])
            p2 = np.array([s.p2 for s in segs])
            x1, y1, d1 = self._proj(p1)
            x2, y2, d2 = self._proj(p2)
            cols = self.wire_colors()
            order = np.argsort((d1 + d2) / 2)
            for i in order:
                cc = cols[i]
                qp.setPen(QtGui.QPen(QtGui.QColor.fromRgbF(*cc), 3.5, Qt.PenStyle.SolidLine,
                                     Qt.PenCapStyle.RoundCap))
                qp.drawLine(QtCore.QPointF(x1[i], y1[i]), QtCore.QPointF(x2[i], y2[i]))
        for pt in self.geom.patches:
            a = math.sqrt(pt.area) / 2
            q = np.array([pt.center + (sx * pt.t1 + sy * pt.t2) * a for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
            x, y, _ = self._proj(q)
            poly = QtGui.QPolygonF([QtCore.QPointF(x[k], y[k]) for k in range(4)])
            qp.setPen(QtGui.QPen(QtGui.QColor(200, 210, 225), 1))
            qp.setBrush(QtGui.QColor(150, 165, 190, 120))
            qp.drawPolygon(poly)
        qp.setBrush(Qt.BrushStyle.NoBrush)
        feeds, loads, tls = markers(self.geom, self.deck)
        for a, b in tls:
            x, y, _ = self._proj(np.array([a, b]))
            qp.setPen(QtGui.QPen(QtGui.QColor(167, 139, 250), 1.5, Qt.PenStyle.DashLine))
            qp.drawLine(QtCore.QPointF(x[0], y[0]), QtCore.QPointF(x[1], y[1]))
        for pts, col, r in ((loads, QtGui.QColor(251, 191, 36), 4.5), (feeds, QtGui.QColor(248, 92, 92), 7)):
            if pts:
                x, y, _ = self._proj(np.array(pts))
                qp.setPen(QtGui.QPen(QtGui.QColor("#0b0f14"), 1.5))
                qp.setBrush(col)
                for k in range(len(x)):
                    qp.drawEllipse(QtCore.QPointF(x[k], y[k]), r, r)
        hp = self._highlight_points()
        if hp:
            x, y, _ = self._proj(np.array(hp))
            qp.setBrush(Qt.BrushStyle.NoBrush)
            qp.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 220), 2))
            for k in range(len(x)):
                qp.drawEllipse(QtCore.QPointF(x[k], y[k]), 11, 11)
        # axes triad (screen corner)
        o = np.array([0.0, 0.0, 0.0])
        cen = self.centre()
        L = span * 0.15
        base = cen + np.array([0, 0, 0])
        x0, y0, _ = self._proj(base)
        ox, oy = 50 - x0[0], self.height() - 50 - y0[0]
        for d, col, lbl in (((1, 0, 0), "#f87171", "X"), ((0, 1, 0), "#34d399", "Y"), ((0, 0, 1), "#38bdf8", "Z")):
            x, y, _ = self._proj(np.array([base, base + np.array(d) * L]))
            qp.setPen(QtGui.QPen(QtGui.QColor(col), 2))
            qp.drawLine(QtCore.QPointF(x[0] + ox, y[0] + oy), QtCore.QPointF(x[1] + ox, y[1] + oy))
            qp.drawText(QtCore.QPointF(x[1] + ox + 3, y[1] + oy - 3), lbl)
        del o
        qp.end()

    def mousePressEvent(self, ev):
        self._drag = (event_pos(ev), ev.buttons(), self.yaw, self.pitch, QtCore.QPointF(self.pan))

    def mouseMoveEvent(self, ev):
        if not self._drag:
            return
        p0, btn, yaw, pitch, pan = self._drag
        d = event_pos(ev) - p0
        if btn & Qt.MouseButton.LeftButton:
            self.yaw = yaw - d.x() * 0.5
            self.pitch = max(-90, min(90, pitch + d.y() * 0.5))
        else:
            self.pan = pan + d
        self.update()

    def mouseReleaseEvent(self, ev):
        if self._drag and ev.button() == Qt.MouseButton.LeftButton:
            p = event_pos(ev)
            if (p - self._drag[0]).manhattanLength() < 4 and self.geom and self.geom.segments:
                a = self._proj(np.array([s.p1 for s in self.geom.segments]))
                b = self._proj(np.array([s.p2 for s in self.geom.segments]))
                self.picked.emit(self._pick_tag(p.x(), p.y(), a[:2], b[:2]))
        self._drag = None

    def wheelEvent(self, ev):
        self.zoom *= 1.15 if ev.angleDelta().y() > 0 else 1 / 1.15
        self.update()


def make_view(prefer_gl: bool = True):
    if prefer_gl and HAVE_GL:
        try:
            return GLAntennaView()
        except Exception:
            pass
    return PainterAntennaView()
