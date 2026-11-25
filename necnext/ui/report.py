"""Printable design report: results, charts and the finished geometry.

The report is built as HTML. The preview and PDF use a QTextDocument with the chart
images added as resources; "Save HTML" writes a single self-contained file with the
images embedded as base64 PNGs.
"""
from __future__ import annotations

import base64
import datetime as _dt
import html
import math
from typing import Dict, List, Optional

import numpy as np

from .. import builder as B
from ..engine import pattern_metrics
from .compat import Qt, QtCore, QtGui, QtWidgets

CSS = """
body { font-family: 'Segoe UI', 'DejaVu Sans', Arial, sans-serif; font-size: 10pt; color: #111; }
h1 { font-size: 20pt; margin: 0 0 2px 0; color: #0b3d63; }
h2 { font-size: 13pt; margin: 18px 0 6px 0; color: #0b3d63; border-bottom: 1px solid #9bb; }
h3 { font-size: 11pt; margin: 10px 0 4px 0; color: #234; }
.muted { color: #567; }
table { border-collapse: collapse; margin: 4px 0 8px 0; }
th { background: #e6eef5; text-align: left; padding: 3px 6px; border: 1px solid #c4d2de; font-weight: 600; }
td { padding: 2px 6px; border: 1px solid #d6e0e8; }
td.n { text-align: right; font-family: 'Consolas', 'DejaVu Sans Mono', monospace; }
pre { font-family: 'Consolas', 'DejaVu Sans Mono', monospace; font-size: 8pt; background: #f4f7f9;
      border: 1px solid #d6e0e8; padding: 6px; }
.kpi td { border: none; padding: 4px 14px 4px 0; }
.kpi .v { font-size: 15pt; font-weight: 600; color: #0b3d63; }
.warn { color: #9a5b00; }
"""


def _e(s) -> str:
    return html.escape(str(s))


def _f(v, d=3) -> str:
    try:
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            return "—"
        return f"{v:.{d}f}"
    except (TypeError, ValueError):
        return str(v)


def _table(headers: List[str], rows: List[List], num_cols=()) -> str:
    out = ["<table><tr>" + "".join(f"<th>{_e(h)}</th>" for h in headers) + "</tr>"]
    for r in rows:
        cells = []
        for i, c in enumerate(r):
            cls = ' class="n"' if i in num_cols else ""
            cells.append(f"<td{cls}>{_e(c)}</td>")
        out.append("<tr>" + "".join(cells) + "</tr>")
    out.append("</table>")
    return "".join(out)


# ------------------------------------------------------------------ geometry rows
def finished_geometry(win) -> List[dict]:
    """One row per wire (tag) of the finished antenna, in build order."""
    rows: List[dict] = []
    gen = getattr(win, "gen", None)
    design = getattr(win, "design", None)
    if gen is not None and design is not None:
        by_tag: Dict[int, List[B.GenWire]] = {}
        order: List[int] = []
        for w in gen.wires:
            if w.tag not in by_tag:
                order.append(w.tag)
            by_tag.setdefault(w.tag, []).append(w)
        for t in order:
            ws = by_tag[t]
            w0 = ws[0]
            if w0.kind == "source":
                continue
            ei, ii = gen.tag_map.get(t, (w0.element, w0.item))
            try:
                it = design["elements"][ei]["items"][ii]
                what = f"{ii + 1}. {B.title(it)}"
            except (IndexError, KeyError):
                what = ""
            if w0.kind == "lumped":
                lab = [r["item"] for r in gen.lumped if r["tag"] == t]
                what = " + ".join(lab) or what
            length = sum(w.length for w in ws)
            rows.append({"tag": t, "element": w0.label, "item": what, "kind": w0.kind,
                         "p1": ws[0].p1, "p2": ws[-1].p2, "length": length,
                         "radius": w0.radius, "segs": sum(w.segs for w in ws), "pieces": len(ws)})
        return rows
    geom = win.geom_res.geometry if getattr(win, "geom_res", None) else None
    if geom is None:
        return rows
    by_tag = {}
    order = []
    for s in geom.segments:
        if s.tag not in by_tag:
            order.append(s.tag)
        by_tag.setdefault(s.tag, []).append(s)
    for t in order:
        ss = by_tag[t]
        length = sum(float(np.linalg.norm(s.p2 - s.p1)) for s in ss)
        rows.append({"tag": t, "element": "", "item": "", "kind": "wire", "p1": ss[0].p1, "p2": ss[-1].p2,
                     "length": length, "radius": ss[0].radius, "segs": len(ss), "pieces": 1})
    return rows


# ------------------------------------------------------------------ html
def build_html(win, images: Dict[str, str]) -> str:
    """images: name -> src (resource URL or data URI)."""
    design = getattr(win, "design", None)
    gen = getattr(win, "gen", None)
    name = (design or {}).get("name") or (win.path or getattr(win, "_example_name", None) or "Antenna")
    z0 = win.z0.value()
    f = win.pattern_freq or win.f_pat.value()
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    h = [f"<html><head><meta charset='utf-8'><title>{_e(name)}</title><style>{CSS}</style></head><body>"]
    h.append(f"<h1>{_e(name)}</h1><div class='muted'>NEC Next antenna report · {now}</div>")
    notes = (design or {}).get("notes") or ""
    if not design:
        notes = "\n".join(c.text[2:].strip() for c in win.deck.comments) if hasattr(win.deck, "comments") else ""
    if notes.strip():
        h.append("<p>" + "<br>".join(_e(n) for n in notes.splitlines()) + "</p>")

    # ---- key results
    fr = None
    if win.pat and win.pat.freqs:
        fr = win.pat.freqs[0]
    elif win.sweep and win.sweep.freqs:
        fr = min(win.sweep.freqs, key=lambda r: abs(r.freq - f))
    h.append(f"<h2>Performance at {_f(f, 4)} MHz</h2>")
    kp = []
    if fr is not None and fr.sources:
        z = fr.z
        kp.append(("Feed impedance", f"{z.real:.1f} {'+' if z.imag >= 0 else '−'} j{abs(z.imag):.1f} Ω"))
        s = fr.swr(z0)
        kp.append((f"SWR ({z0:g} Ω)", f"{s:.2f}" if s < 100 else "∞"))
    m = None
    if win.pat and win.pat.freqs and win.pat.freqs[0].pattern is not None:
        m = pattern_metrics(win.pat.freqs[0].pattern)
        kp.append(("Max gain", f"{m['gain']:.2f} dBi"))
        kp.append(("Direction of max", f"az {m['phi']:.0f}°, el {m['elev']:.0f}°"))
        kp.append(("Front / back", f"{m['fb']:.1f} dB" if m["fb"] < 99 else "symmetric"))
        if not math.isnan(m.get("bw_az", float("nan"))):
            kp.append(("Beamwidth (az / el)", f"{_f(m['bw_az'], 0)}° / {_f(m['bw_el'], 0)}°"))
        eff = win.pat.freqs[0].efficiency
        if not math.isnan(eff):
            kp.append(("Efficiency", f"{eff:.1f} %"))
    if kp:
        h.append("<table class='kpi'>")
        for i in range(0, len(kp), 4):
            h.append("<tr>" + "".join(f"<td><div class='muted'>{_e(a)}</div><div class='v'>{_e(b)}</div></td>"
                                      for a, b in kp[i:i + 4]) + "</tr>")
        h.append("</table>")
    else:
        h.append("<p class='muted'>No results yet. Run the simulation first.</p>")

    # ---- sweep summary
    if win.sweep and win.sweep.freqs:
        fs = win.sweep.freqs
        swr = [r.swr(z0) for r in fs]
        k = int(np.argmin(swr))
        band = [r.freq for r, s in zip(fs, swr) if s <= 2.0]
        h.append("<h2>Frequency sweep</h2>")
        line = f"Minimum SWR {swr[k]:.2f} at {fs[k].freq:.4f} MHz."
        if band:
            line += f" SWR ≤ 2 from {min(band):.4f} to {max(band):.4f} MHz ({(max(band) - min(band)) * 1000:.0f} kHz)."
        else:
            line += " SWR stays above 2 across the sweep."
        h.append(f"<p>{_e(line)}</p>")
        for key in ("sweep", "smith"):
            if key in images:
                h.append(f"<p><img src='{images[key]}' width='640'></p>")
        step = max(1, int(math.ceil(len(fs) / 25)))
        idx = sorted(set(list(range(0, len(fs), step)) + [len(fs) - 1, k]))
        has_gain = any(r.pattern is not None for r in fs)
        rows = []
        for i in idx:
            r = fs[i]
            row = [f"{r.freq:.4f}", f"{r.z.real:.2f}", f"{r.z.imag:.2f}",
                   f"{swr[i]:.2f}" if swr[i] < 100 else "∞"]
            if has_gain:
                if r.pattern is not None:
                    mm = pattern_metrics(r.pattern)
                    row += [f"{mm['gain']:.2f}", f"{mm['fb']:.1f}" if mm["fb"] < 99 else "—"]
                else:
                    row += ["—", "—"]
            rows.append(row)
        hdr = ["MHz", "R (Ω)", "X (Ω)", "SWR"] + (["Gain (dBi)", "F/B (dB)"] if has_gain else [])
        h.append(_table(hdr, rows, num_cols=range(len(hdr))))

    # ---- pattern
    if "pattern" in images or "view3d" in images:
        h.append(f"<h2>Radiation pattern and currents</h2>")
        for key in ("pattern", "view3d"):
            if key in images:
                h.append(f"<p><img src='{images[key]}' width='640'></p>")

    # ---- simulation settings
    h.append("<h2>Simulation settings</h2>")
    srows = [["Sweep", f"{win.f_start.value():.4f} – {win.f_stop.value():.4f} MHz, {win.f_steps.value()} points"],
             ["Pattern frequency", f"{f:.4f} MHz, {win.pat_res.currentText()} steps"],
             ["Reference impedance", f"{z0:g} Ω"],
             ["Ground", win.ground.currentText() + (
                 f" (εr {win.eps.value():g}, σ {win.sigma.value():g} S/m"
                 f"{', Sommerfeld' if win.somm.isChecked() else ''})" if win.ground.currentIndex() >= 2 else "")]]
    if design is not None:
        srows += [["Default wire", f"radius {design.get('radius', '')}, {design.get('material', '')}"],
                  ["Segmentation", f"λ/{design.get('density', '40')} at the top frequency"
                                   + (f" (target {gen.seg_target:.3g} m)" if gen else "")]]
    h.append(_table(["Setting", "Value"], srows))

    # ---- variables
    vars_ = (design or {}).get("variables") or []
    if vars_:
        sym = gen.symbols if gen else {}
        h.append("<h2>Variables</h2>")
        h.append(_table(["Name", "Expression", "Value"],
                        [[n, e, _f(sym.get(n), 4) if n in sym else ""] for n, e in vars_], num_cols=(2,)))

    # ---- build list
    if design is not None:
        h.append("<h2>Build list</h2>")
        sym = gen.symbols if gen else {}
        for ei, el in enumerate(design.get("elements", [])):
            h.append(f"<h3>{_e(el.get('name') or f'Element {ei + 1}')}"
                     f"{'' if el.get('enabled', True) else ' (disabled)'}</h3>")
            h.append(f"<div class='muted'>Starts at ({_e(el['x'])}, {_e(el['y'])}, {_e(el['z'])}) m, heading "
                     f"azimuth {_e(el['azimuth'])}°, elevation {_e(el['elevation'])}°</div>")
            rows = []
            for ii, it in enumerate(el.get("items", [])):
                pos = gen.positions.get((ei, ii)) if gen else None
                at = f"{pos[0][0]:.3f}, {pos[0][1]:.3f}, {pos[0][2]:.3f}" if pos else ""
                rows.append([ii + 1, B.title(it), B.describe(it, sym), at])
            h.append(_table(["#", "Item", "Details", "Ends at x, y, z (m)"], rows, num_cols=(0,)))

    # ---- finished geometry
    geo = finished_geometry(win)
    if geo:
        h.append("<h2>Finished geometry</h2>")
        rows = []
        total = 0.0
        for g in geo:
            p1, p2 = g["p1"], g["p2"]
            total += g["length"]
            rows.append([g["tag"], g["element"], g["item"] + (" (helix)" if g["kind"] == "helix" else ""),
                         f"{p1[0]:.4f}", f"{p1[1]:.4f}", f"{p1[2]:.4f}",
                         f"{p2[0]:.4f}", f"{p2[1]:.4f}", f"{p2[2]:.4f}",
                         f"{g['length']:.4f}", f"{g['radius'] * 1000:.3g}", g["segs"]])
        h.append(_table(["Tag", "Element", "Part", "x1", "y1", "z1", "x2", "y2", "z2", "Length (m)",
                         "Radius (mm)", "Segs"], rows, num_cols=(0, 3, 4, 5, 6, 7, 8, 9, 10, 11)))
        nseg = sum(g["segs"] for g in geo)
        h.append(f"<p>Total conductor length <b>{total:.3f} m</b> ({total * 3.28084:.2f} ft) · "
                 f"{len(geo)} wires · {nseg} segments. Coordinates in metres; z is height above ground.</p>")
    if gen and gen.lumped:
        h.append("<h2>Feeds, loads and lines</h2>")
        rows = [[r["element"], r["item"], r["detail"], f"{r['pos'][0]:.3f}, {r['pos'][1]:.3f}, {r['pos'][2]:.3f}",
                 r["tag"]] for r in gen.lumped]
        h.append(_table(["Element", "Component", "Value", "At x, y, z (m)", "Tag"], rows, num_cols=(4,)))
    if gen and gen.warnings:
        h.append("<h2>Warnings</h2><ul>" + "".join(f"<li class='warn'>{_e(w)}</li>" for w in gen.warnings) + "</ul>")

    # ---- appendix
    h.append("<h2>Appendix: NEC deck</h2>")
    deck_text = gen.deck_text if gen else win.editor.toPlainText()
    h.append(f"<pre>{_e(deck_text)}</pre>")
    h.append("</body></html>")
    return "".join(h)


# ------------------------------------------------------------------ dialog
class ReportDialog(QtWidgets.QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Report")
        self.resize(900, 820)
        lay = QtWidgets.QVBoxLayout(self)
        self.view = QtWidgets.QTextBrowser()
        self.view.setStyleSheet("QTextBrowser { background: white; color: #111; }")
        lay.addWidget(self.view, 1)
        row = QtWidgets.QHBoxLayout()
        self.cb_img = QtWidgets.QCheckBox("Include charts")
        self.cb_img.setChecked(True)
        self.cb_img.toggled.connect(self.refresh)
        row.addWidget(self.cb_img)
        row.addStretch(1)
        for text, fn in (("Save PDF…", self.save_pdf), ("Save HTML…", self.save_html), ("Close", self.accept)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)
        self.images: Dict[str, QtGui.QImage] = {}
        self._grab()
        self.refresh()

    def _grab(self):
        w = self.win
        tabs = w.tabs
        cur = tabs.currentIndex()
        targets = [("view3d", 0), ("sweep", 1), ("smith", 2), ("pattern", 3)]
        have = {"sweep": bool(w.sweep), "smith": bool(w.sweep), "pattern": bool(w.pat),
                "view3d": bool(getattr(w, "geom_res", None))}
        for key, idx in targets:
            if not have[key]:
                continue
            try:
                tabs.setCurrentIndex(idx)
                QtWidgets.QApplication.processEvents()
                if key == "view3d":
                    img = w.view3d.grab_image()
                else:
                    img = tabs.widget(idx).grab().toImage()
                if img is not None and not img.isNull():
                    self.images[key] = img
            except Exception:  # noqa: BLE001 - a missing chart must not break the report
                pass
        tabs.setCurrentIndex(cur)

    def make_doc(self) -> QtGui.QTextDocument:
        doc = QtGui.QTextDocument(self)
        srcs = {}
        if self.cb_img.isChecked():
            for k, img in self.images.items():
                url = QtCore.QUrl(f"nnimg://{k}.png")
                add_image(doc, url, img)
                srcs[k] = url.toString()
        doc.setHtml(build_html(self.win, srcs))
        return doc

    def refresh(self):
        self.doc = self.make_doc()
        self.view.setDocument(self.doc)

    def _ask(self, title, ext, filt):
        s = self.win.settings
        d = s.value("last_dir", QtCore.QDir.homePath())
        name = ((self.win.design or {}).get("name") or "antenna") + " report" + ext
        fn, _ = QtWidgets.QFileDialog.getSaveFileName(self, title, QtCore.QDir(d).filePath(name), filt)
        if fn and not fn.lower().endswith(ext):
            fn += ext
        return fn

    def save_pdf(self):
        fn = self._ask("Save report as PDF", ".pdf", "PDF (*.pdf)")
        if not fn:
            return
        write_pdf(self.make_doc(), fn)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(fn))

    def save_html(self):
        fn = self._ask("Save report as HTML", ".html", "HTML (*.html)")
        if not fn:
            return
        srcs = {}
        if self.cb_img.isChecked():
            for k, img in self.images.items():
                srcs[k] = "data:image/png;base64," + base64.b64encode(image_png(img)).decode()
        with open(fn, "w", encoding="utf-8") as f:
            f.write(build_html(self.win, srcs))
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(fn))


def add_image(doc, url, img):
    try:
        rt = QtGui.QTextDocument.ResourceType.ImageResource
    except AttributeError:
        rt = QtGui.QTextDocument.ImageResource
    try:
        doc.addResource(rt, url, img)
    except TypeError:
        doc.addResource(int(getattr(rt, "value", rt)), url, img)


def image_png(img: QtGui.QImage) -> bytes:
    ba = QtCore.QByteArray()
    buf = QtCore.QBuffer(ba)
    buf.open(QtCore.QIODevice.OpenModeFlag.WriteOnly if hasattr(QtCore.QIODevice, "OpenModeFlag")
             else QtCore.QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def write_pdf(doc: QtGui.QTextDocument, fn: str):
    w = QtGui.QPdfWriter(fn)
    try:
        w.setPageSize(QtGui.QPageSize(QtGui.QPageSize.PageSizeId.A4))
    except AttributeError:
        w.setPageSize(QtGui.QPageSize(QtGui.QPageSize.A4))
    w.setResolution(150)
    try:
        w.setPageMargins(QtCore.QMarginsF(14, 14, 14, 14), QtGui.QPageLayout.Unit.Millimeter)
    except (AttributeError, TypeError):
        w.setPageMargins(QtCore.QMarginsF(14, 14, 14, 14))
    d = doc
    # QTextDocument lays out in device pixels of the writer
    rect = w.pageLayout().paintRectPixels(w.resolution())
    d.setPageSize(QtCore.QSizeF(rect.width(), rect.height()))
    d.setDefaultFont(QtGui.QFont(d.defaultFont().family(), 9))
    d.print_(w) if hasattr(d, "print_") else d.print(w)
