"""Point-and-click antenna builder.

A *design* is a list of *elements* (separate conductors such as the reflector,
driven element and directors of a Yagi).  Each element starts at a point with
a heading and holds an ordered *path* of items that are walked like a pen:

  wire   – a straight conductor of a given length along the current heading
  turn   – change heading (left/right = azimuth, up/down = elevation, or absolute)
  gap    – move without making a conductor
  helix  – a coil wound along the current heading
  feed   – a voltage source at this point (optionally through a transformer)
  load   – coil / capacitor / resistor / trap / R+jX at this point
  tl     – one end of a transmission line; two ends with the same link name are joined

Lumped items (feed, load, tl) sit at the point between neighbouring wires.
The generator carves a short one-segment wire there to carry them, so the
total geometry is unchanged.  Wires are segmented automatically from the
highest simulated frequency.  Every numeric field may be an expression using
the design's variables (``len/2``, ``12.7mm``, ``4.7uH``...).
"""
from __future__ import annotations

import copy
import json
import textwrap
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .deck import Deck, DeckError, evaluate

C0 = 299.792458  # speed of light, m/us -> lambda[m] = C0 / f[MHz]

MATERIALS = {"perfect": None, "copper": 5.8e7, "aluminium": 3.5e7, "brass": 1.5e7,
             "steel": 6.0e6, "stainless": 1.4e6, "copper-clad steel": 2.0e7}

ITEM_DEFAULTS: Dict[str, Dict[str, str]] = {
    "wire": {"length": "1", "radius": "", "segments": ""},
    "turn": {"mode": "relative", "yaw": "90", "pitch": "0"},
    "gap": {"length": "0.1"},
    "helix": {"turns": "5", "diameter": "50mm", "length": "100mm", "radius": "", "hand": "right",
              "segs_per_turn": "12"},
    "feed": {"voltage": "1", "phase": "0", "ratio": ""},
    "load": {"kind": "coil", "L": "10uH", "C": "100pF", "R": "0", "X": "0", "Q": "200"},
    "tl": {"link": "A", "z0": "50", "length": "0", "vf": "1", "crossed": "no"},
}
LUMPED = {"feed", "load", "tl"}
LOAD_KINDS = {"coil": "Coil (L, with Q)", "capacitor": "Capacitor (C)", "resistor": "Resistor (R)",
              "trap": "Trap (parallel L-C, with Q)", "series_lc": "Series L-C", "impedance": "Impedance R + jX"}


def new_item(type_: str, **kw) -> dict:
    it = {"type": type_, "name": ""}
    it.update(copy.deepcopy(ITEM_DEFAULTS[type_]))
    it.update({k: str(v) for k, v in kw.items()})
    return it


def new_element(name="Element", x="0", y="0", z="10", azimuth="0", elevation="0", items=None) -> dict:
    return {"name": name, "x": str(x), "y": str(y), "z": str(z), "azimuth": str(azimuth),
            "elevation": str(elevation), "enabled": True, "items": items or []}


def new_design(name="New antenna") -> dict:
    return {"format": "necnext-design", "version": 1, "name": name, "notes": "",
            "variables": [], "radius": "1mm", "material": "copper", "density": "40",
            "sim": {}, "elements": []}


def describe(item: dict, symbols: Optional[dict] = None) -> str:
    """One-line human summary of an item."""
    t = item["type"]
    s = symbols or {}

    def v(k, unit=""):
        raw = item.get(k, "")
        try:
            val = evaluate(raw, s)
            num = f"{val:.4g}"
            return raw + unit if raw.replace(".", "", 1).replace("-", "", 1).isdigit() else f"{raw} ({num}{unit})"
        except Exception:
            return raw
    if t == "wire":
        return f"{v('length', ' m')}"
    if t == "turn":
        if item.get("mode") == "absolute":
            return f"point to azimuth {item['yaw']}°, elevation {item['pitch']}°"
        parts = []
        try:
            yaw = evaluate(item["yaw"], s)
            pitch = evaluate(item["pitch"], s)
        except Exception:
            return f"yaw {item['yaw']}, pitch {item['pitch']}"
        if yaw:
            parts.append(f"{'left' if yaw > 0 else 'right'} {abs(yaw):g}°")
        if pitch:
            parts.append(f"{'up' if pitch > 0 else 'down'} {abs(pitch):g}°")
        return ", ".join(parts) or "no change"
    if t == "gap":
        return f"move {v('length', ' m')} (no wire)"
    if t == "helix":
        return f"{item['turns']} turns, Ø {item['diameter']}, length {item['length']}, {item['hand']}-hand"
    if t == "feed":
        r = f", via {item['ratio']} transformer" if item.get("ratio") else ""
        return f"{item['voltage']} V ∠{item['phase']}°{r}"
    if t == "load":
        k = item.get("kind", "coil")
        if k == "coil":
            return f"L {item['L']}, Q {item['Q'] or '∞'}"
        if k == "capacitor":
            return f"C {item['C']}"
        if k == "resistor":
            return f"R {item['R']} Ω"
        if k == "trap":
            return f"L {item['L']} ∥ C {item['C']}, Q {item['Q'] or '∞'}"
        if k == "series_lc":
            return f"L {item['L']} + C {item['C']}"
        return f"{item['R']} + j{item['X']} Ω"
    if t == "tl":
        return f"line '{item['link']}' · {item['z0']} Ω" + (f" · {item['length']} m" if item.get("length") not in ("", "0") else "") \
            + (f" · VF {item['vf']}" if item.get("vf") not in ("", "1") else "") + (" · crossed" if item.get("crossed") == "yes" else "")
    return t


TITLES = {"wire": "Wire", "turn": "Turn", "gap": "Gap", "helix": "Helix", "feed": "Feed point",
          "load": "Load", "tl": "Transmission line end"}


def title(item: dict) -> str:
    if item.get("name"):
        return item["name"]
    if item["type"] == "load":
        return {"coil": "Coil", "capacitor": "Capacitor", "resistor": "Resistor", "trap": "Trap",
                "series_lc": "Series L-C", "impedance": "Impedance"}.get(item.get("kind"), "Load")
    return TITLES.get(item["type"], item["type"])


# ------------------------------------------------------------------ geometry
def heading(az: float, el: float) -> np.ndarray:
    """Unit heading. Elevation may run past ±90° (pitching 'over the top'), which
    makes Up/Down turns behave like a turtle: four 'Up 90' turns close a vertical loop."""
    a, e = math.radians(az), math.radians(el)
    return np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])


def _frame(h: np.ndarray):
    """Two unit vectors perpendicular to h."""
    ref = np.array([0.0, 0.0, 1.0]) if abs(h[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = np.cross(ref, h)
    u /= np.linalg.norm(u)
    v = np.cross(h, u)
    return u, v


@dataclass
class GenWire:
    tag: int
    p1: np.ndarray
    p2: np.ndarray
    radius: float
    segs: int
    element: int
    item: int                 # index of the path item (wire/helix) or first lumped item
    kind: str                 # wire | helix | lumped | source
    label: str = ""

    @property
    def length(self):
        return float(np.linalg.norm(self.p2 - self.p1))


@dataclass
class GenResult:
    deck_text: str
    wires: List[GenWire]
    tag_map: Dict[int, Tuple[int, int]]
    lumped: List[dict]                       # report rows
    warnings: List[str] = field(default_factory=list)
    seg_target: float = 0.0
    symbols: Dict[str, float] = field(default_factory=dict)
    positions: Dict[Tuple[int, int], tuple] = field(default_factory=dict)


class BuildError(Exception):
    pass


def _fmt(v: float) -> str:
    if abs(v) < 1e-12:
        return "0"
    return f"{v:.9g}"


def graded(L: float, target: float, s1: float, s2: float, n_fixed: int = 0) -> List[Tuple[float, int]]:
    """Split a wire of length L into pieces [(length, segments)] whose segment
    lengths grow geometrically (x2) from s1 at the start and s2 at the end up
    to *target*, so neighbouring segments never differ by more than about 2:1."""
    if n_fixed:
        return [(L, n_fixed)]
    n_uniform = max(1, math.ceil(L / target - 1e-9))
    nominal = L / n_uniform
    head, tail = [], []
    rem = L
    a, b = s1, s2
    # grow from both ends while the neighbour is much shorter than our nominal segment
    for _ in range(40):
        moved = False
        if a and a * 2 < nominal and rem > 4 * a:
            seg = 2 * a
            head.append((seg, 1))
            rem -= seg
            a = seg
            moved = True
        if b and b * 2 < nominal and rem > 4 * b:
            seg = 2 * b
            tail.append((seg, 1))
            rem -= seg
            b = seg
            moved = True
        if not moved:
            break
    n_mid = max(1, math.ceil(rem / target - 1e-9))
    mid = [(rem, n_mid)]
    pieces = head + mid + tail[::-1]
    # merge consecutive single segments of equal length
    out: List[Tuple[float, int]] = []
    for ln, n in pieces:
        if out and abs(out[-1][0] / out[-1][1] - ln / n) < 1e-9:
            out[-1] = (out[-1][0] + ln, out[-1][1] + n)
        else:
            out.append((ln, n))
    return out


def generate(design: dict, f_max: float, f_center: Optional[float] = None, sim_cards: Optional[List[str]] = None) -> GenResult:
    """Turn a design into a NEC deck."""
    symbols: Dict[str, float] = {}
    for name, expr in design.get("variables", []):
        try:
            symbols[name] = evaluate(expr, symbols)
        except DeckError as e:
            raise BuildError(f"Variable {name}: {e}") from None
    f_center = f_center or f_max

    def E(expr, where, default=None):
        if (expr is None or str(expr).strip() == "") and default is not None:
            return default
        try:
            return evaluate(str(expr), symbols)
        except DeckError as e:
            raise BuildError(f"{where}: {e}") from None

    density = max(4.0, E(design.get("density", "40"), "Segments per wavelength"))
    lam = C0 / max(f_max, 1e-6)
    seg_target = lam / density
    r_default = E(design.get("radius", "1mm"), "Default wire radius")
    warnings: List[str] = []

    pieces_all = []   # per element: list of tokens
    positions: Dict[Tuple[int, int], tuple] = {}   # (element, item) -> (point after item, az, el)
    for ei, el in enumerate(design.get("elements", [])):
        if not el.get("enabled", True):
            pieces_all.append([])
            continue
        en = el.get("name") or f"Element {ei + 1}"
        pos = np.array([E(el.get(k, "0"), f"{en} start {k}") for k in ("x", "y", "z")], dtype=float)
        az = E(el.get("azimuth", "0"), f"{en} azimuth")
        elv = E(el.get("elevation", "0"), f"{en} elevation")
        toks = []
        for ii, it in enumerate(el.get("items", [])):
            t = it["type"]
            where = f"{en} › {ii + 1}. {title(it)}"
            if t == "wire":
                L = E(it.get("length"), where)
                if L <= 0:
                    raise BuildError(f"{where}: length must be positive")
                rad = E(it.get("radius"), where, r_default)
                segs = int(E(it.get("segments"), where, 0))
                p2 = pos + heading(az, elv) * L
                toks.append({"k": "wire", "p1": pos.copy(), "p2": p2, "r": rad, "segs": segs, "ii": ii,
                             "where": where})
                pos = p2
            elif t == "gap":
                pos = pos + heading(az, elv) * E(it.get("length"), where)
                toks.append({"k": "gap"})
            elif t == "turn":
                y, p = E(it.get("yaw"), where, 0.0), E(it.get("pitch"), where, 0.0)
                if it.get("mode") == "absolute":
                    az, elv = y, p
                else:
                    az, elv = az + y, elv + p
                toks.append({"k": "turn"})
            elif t == "helix":
                n_t = E(it.get("turns"), where)
                D = E(it.get("diameter"), where)
                HL = E(it.get("length"), where)
                rad = E(it.get("radius"), where, r_default)
                spt = max(6, int(E(it.get("segs_per_turn"), where, 12)))
                if n_t <= 0 or D <= 0:
                    raise BuildError(f"{where}: turns and diameter must be positive")
                h = heading(az, elv)
                u, v = _frame(h)
                R = D / 2
                centre = pos - u * R          # helix passes through the current point
                sign = 1.0 if it.get("hand", "right") == "right" else -1.0
                n = max(2, int(round(n_t * spt)))
                pts = []
                for k in range(n + 1):
                    phi = 2 * math.pi * n_t * k / n
                    pts.append(centre + u * R * math.cos(phi) + v * R * sign * math.sin(phi) + h * HL * k / n)
                toks.append({"k": "helix", "pts": pts, "r": rad, "ii": ii, "where": where})
                pos = pts[-1]
            elif t in LUMPED:
                if toks and toks[-1]["k"] == "lump" and toks[-1].get("open", True):
                    toks[-1]["items"].append(ii)
                else:
                    toks.append({"k": "lump", "items": [ii], "pos": pos.copy(), "dir": heading(az, elv),
                                 "where": where})
            else:
                raise BuildError(f"{where}: unknown item type {t}")
            positions[(ei, ii)] = (pos.copy(), az, elv)
        pieces_all.append(toks)

    # ---- per-element segment target: λ/density, but at least ~12 segments per element
    el_target = []
    for toks in pieces_all:
        tot = sum(float(np.linalg.norm(t["p2"] - t["p1"])) for t in toks if t["k"] == "wire")
        el_target.append(min(seg_target, tot / 12.0) if tot > 0 else seg_target)

    # ---- carve lumped segments
    for ei, toks in enumerate(pieces_all):
        seg_target_el = el_target[ei]
        for k, tk in enumerate(toks):
            if tk["k"] != "lump":
                continue
            prev, turn_before = None, False
            for j in range(k - 1, -1, -1):
                kk = toks[j]["k"]
                if kk == "turn":
                    turn_before = True
                    continue
                if kk == "wire":
                    prev = toks[j]
                break
            nxt, turn_after = None, False
            for j in range(k + 1, len(toks)):
                kk = toks[j]["k"]
                if kk == "turn":
                    turn_after = True
                    continue
                if kk == "wire":
                    nxt = toks[j]
                break
            Lp = np.linalg.norm(prev["p2"] - prev["p1"]) if prev is not None else 0
            Ln = np.linalg.norm(nxt["p2"] - nxt["p1"]) if nxt is not None else 0
            if prev is not None and nxt is not None and not turn_before and not turn_after:
                ell = min(seg_target_el, 0.5 * Lp, 0.5 * Ln)
                d1 = (prev["p2"] - prev["p1"]) / Lp
                d2 = (nxt["p2"] - nxt["p1"]) / Ln
                prev["p2"] = prev["p2"] - d1 * ell / 2
                nxt["p1"] = nxt["p1"] + d2 * ell / 2
                tk["p1"], tk["p2"], tk["r"] = prev["p2"].copy(), nxt["p1"].copy(), min(prev["r"], nxt["r"])
            elif prev is not None and nxt is not None:
                # at a corner: take a short piece from the longer neighbour so the
                # feed/load stays close to the corner point
                if Ln >= Lp:
                    ell = min(0.5 * seg_target_el, 0.25 * Ln)
                    d2 = (nxt["p2"] - nxt["p1"]) / Ln
                    tk["p1"] = nxt["p1"].copy()
                    nxt["p1"] = nxt["p1"] + d2 * ell
                    tk["p2"], tk["r"] = nxt["p1"].copy(), nxt["r"]
                else:
                    ell = min(0.5 * seg_target_el, 0.25 * Lp)
                    d1 = (prev["p2"] - prev["p1"]) / Lp
                    tk["p2"] = prev["p2"].copy()
                    prev["p2"] = prev["p2"] - d1 * ell
                    tk["p1"], tk["r"] = prev["p2"].copy(), prev["r"]
            elif nxt is not None and not turn_after:
                ell = min(seg_target_el, 0.5 * Ln)
                d2 = (nxt["p2"] - nxt["p1"]) / Ln
                tk["p1"] = nxt["p1"].copy()
                nxt["p1"] = nxt["p1"] + d2 * ell
                tk["p2"], tk["r"] = nxt["p1"].copy(), nxt["r"]
            elif prev is not None and not turn_before:
                ell = min(seg_target_el, 0.5 * Lp)
                d1 = (prev["p2"] - prev["p1"]) / Lp
                tk["p2"] = prev["p2"].copy()
                prev["p2"] = prev["p2"] - d1 * ell
                tk["p1"], tk["r"] = prev["p2"].copy(), prev["r"]
            elif nxt is not None:
                ell = min(seg_target_el, 0.5 * Ln)
                d2 = (nxt["p2"] - nxt["p1"]) / Ln
                tk["p1"] = nxt["p1"].copy()
                nxt["p1"] = nxt["p1"] + d2 * ell
                tk["p2"], tk["r"] = nxt["p1"].copy(), nxt["r"]
            elif prev is not None:
                ell = min(seg_target_el, 0.5 * Lp)
                d1 = (prev["p2"] - prev["p1"]) / Lp
                tk["p2"] = prev["p2"].copy()
                prev["p2"] = prev["p2"] - d1 * ell
                tk["p1"], tk["r"] = prev["p2"].copy(), prev["r"]
            else:
                # free-standing point (e.g. a source at the far end of a feed line)
                ell = min(seg_target_el * 0.5, 0.01 * lam)
                tk["p1"] = tk["pos"] - tk["dir"] * ell / 2
                tk["p2"] = tk["pos"] + tk["dir"] * ell / 2
                tk["r"] = r_default
                warnings.append(f"{tk['where']}: no wire next to it, so a short stand-alone wire was added")

    # ---- nominal segment length of every token, then neighbours of each wire
    def nominal(tk, tgt):
        if tk["k"] == "wire":
            L = float(np.linalg.norm(tk["p2"] - tk["p1"]))
            return L / (tk["segs"] or max(1, math.ceil(L / tgt - 1e-9)))
        if tk["k"] == "lump":
            return float(np.linalg.norm(tk["p2"] - tk["p1"]))
        if tk["k"] == "helix":
            return float(np.linalg.norm(tk["pts"][1] - tk["pts"][0]))
        return 0.0
    for ei, toks in enumerate(pieces_all):
        for k, tk in enumerate(toks):
            if tk["k"] != "wire":
                continue
            tk["tgt"] = el_target[ei]
            tk["s1"] = tk["s2"] = 0.0
            for j in range(k - 1, -1, -1):
                if toks[j]["k"] == "turn":
                    continue
                if toks[j]["k"] in ("wire", "lump", "helix"):
                    tk["s1"] = nominal(toks[j], el_target[ei])
                break
            for j in range(k + 1, len(toks)):
                if toks[j]["k"] == "turn":
                    continue
                if toks[j]["k"] in ("wire", "lump", "helix"):
                    tk["s2"] = nominal(toks[j], el_target[ei])
                break

    # ---- emit wires
    wires: List[GenWire] = []
    tag_map: Dict[int, Tuple[int, int]] = {}
    lump_tags: Dict[Tuple[int, int], int] = {}
    tag = 0
    for ei, toks in enumerate(pieces_all):
        en = design["elements"][ei].get("name") or f"Element {ei + 1}"
        for tk in toks:
            if tk["k"] == "wire":
                L = float(np.linalg.norm(tk["p2"] - tk["p1"]))
                if L < 1e-6:
                    raise BuildError(f"{tk['where']}: nothing is left of this wire after placing the "
                                     f"feed/load next to it; make it longer")
                pieces = graded(L, tk.get("tgt", seg_target), tk.get("s1", 0.0), tk.get("s2", 0.0), tk["segs"])
                tag += 1
                tag_map[tag] = (ei, tk["ii"])
                d = (tk["p2"] - tk["p1"]) / L
                at = tk["p1"].copy()
                for ln, n in pieces:
                    if not tk["segs"] and ln / n < 4 * tk["r"]:
                        n = max(1, min(n, int(ln / (4 * tk["r"]))))
                    if ln / n < 2 * tk["r"]:
                        warnings.append(f"{tk['where']}: segments are short compared with the wire radius "
                                        f"({ln / n:.3g} m vs radius {tk['r']:.3g} m); accuracy may suffer")
                    nxt_pt = at + d * ln
                    wires.append(GenWire(tag, at, nxt_pt, tk["r"], n, ei, tk["ii"], "wire", en))
                    at = nxt_pt
            elif tk["k"] == "helix":
                tag += 1
                tag_map[tag] = (ei, tk["ii"])
                pts = tk["pts"]
                for a, b in zip(pts[:-1], pts[1:]):
                    wires.append(GenWire(tag, a, b, tk["r"], 1, ei, tk["ii"], "helix", en))
                sl = float(np.linalg.norm(pts[1] - pts[0]))
                if sl < 4 * tk["r"]:
                    warnings.append(f"{tk['where']}: helix segments ({sl:.3g} m) are short for the wire "
                                    "radius; lower segments per turn or use thinner wire")
            elif tk["k"] == "lump":
                tag += 1
                tag_map[tag] = (ei, tk["items"][0])
                for ii in tk["items"]:
                    lump_tags[(ei, ii)] = tag
                wires.append(GenWire(tag, tk["p1"], tk["p2"], tk["r"], 1, ei, tk["items"][0], "lumped", en))

    if not wires:
        raise BuildError("The design has no wires yet. Add an element with a wire.")

    # ---- electrical cards
    ctrl: List[str] = []
    lumped_rows: List[dict] = []
    tl_ends: Dict[str, list] = {}
    lo = np.min([np.minimum(w.p1, w.p2) for w in wires], axis=0)
    hi = np.max([np.maximum(w.p1, w.p2) for w in wires], axis=0)
    span = float(max(np.max(hi - lo), 0.1))
    n_src = 0
    w_freq = 2 * math.pi * f_center * 1e6
    for ei, el in enumerate(design.get("elements", [])):
        if not el.get("enabled", True):
            continue
        en = el.get("name") or f"Element {ei + 1}"
        for ii, it in enumerate(el.get("items", [])):
            if it["type"] not in LUMPED:
                continue
            t = lump_tags[(ei, ii)]
            where = f"{en} › {ii + 1}. {title(it)}"
            gw = next(w for w in wires if w.tag == t)
            mid = (gw.p1 + gw.p2) / 2
            row = {"element": en, "item": title(it), "detail": describe(it, symbols), "tag": t,
                   "pos": mid}
            if it["type"] == "feed":
                V = E(it.get("voltage"), where, 1.0)
                ph = math.radians(E(it.get("phase"), where, 0.0))
                vr, vi = V * math.cos(ph), V * math.sin(ph)
                ratio = (it.get("ratio") or "").strip()
                if ratio and ratio not in ("1:1",):
                    if ":" not in ratio:
                        raise BuildError(f"{where}: transformer ratio must look like 1:49")
                    a, b = (E(x, where) for x in ratio.split(":", 1))
                    if a <= 0 or b <= 0:
                        raise BuildError(f"{where}: transformer ratio values must be positive")
                    n_src += 1
                    tag += 1
                    sx = hi[0] + span * (1 + 0.2 * n_src)
                    sp1 = np.array([sx, (lo[1] + hi[1]) / 2, max((lo[2] + hi[2]) / 2, 0.5)])
                    sl = min(seg_target * 0.5, 0.01 * lam)
                    wires.append(GenWire(tag, sp1, sp1 + np.array([0, 0, sl]), r_default, 1, ei, ii, "source",
                                         en + " (transformer source)"))
                    tag_map[tag] = (ei, ii)
                    nr = math.sqrt(a / b)
                    rs = 1e-3
                    ctrl.append(f"EX 0 {tag} 1 0 {_fmt(vr)} {_fmt(vi)}")
                    ctrl.append(f"NT {tag} 1 {t} 1 {_fmt(1 / (nr * nr * rs))} 0 {_fmt(-1 / (nr * rs))} 0 "
                                f"{_fmt(1 / rs)} 0 ' xfmr {ratio}")
                else:
                    ctrl.append(f"EX 0 {t} 1 0 {_fmt(vr)} {_fmt(vi)}")
            elif it["type"] == "load":
                k = it.get("kind", "coil")
                L = E(it.get("L"), where, 0.0)
                C = E(it.get("C"), where, 0.0)
                R = E(it.get("R"), where, 0.0)
                Q = E(it.get("Q"), where, 0.0)
                if k == "coil":
                    rq = w_freq * L / Q if Q > 0 else 0.0
                    ctrl.append(f"LD 0 {t} 1 1 {_fmt(R + rq)} {_fmt(L)} 0")
                elif k == "capacitor":
                    ctrl.append(f"LD 0 {t} 1 1 {_fmt(R)} 0 {_fmt(C)}")
                elif k == "resistor":
                    ctrl.append(f"LD 0 {t} 1 1 {_fmt(R)} 0 0")
                elif k == "trap":
                    rp = Q * w_freq * L if Q > 0 else 0.0
                    ctrl.append(f"LD 1 {t} 1 1 {_fmt(rp)} {_fmt(L)} {_fmt(C)}")
                elif k == "series_lc":
                    ctrl.append(f"LD 0 {t} 1 1 {_fmt(R)} {_fmt(L)} {_fmt(C)}")
                else:
                    ctrl.append(f"LD 4 {t} 1 1 {_fmt(R)} {_fmt(E(it.get('X'), where, 0.0))}")
            elif it["type"] == "tl":
                tl_ends.setdefault(it.get("link", "A").strip() or "A", []).append((t, it, where, mid))
            lumped_rows.append(row)

    for link, ends in tl_ends.items():
        if len(ends) != 2:
            raise BuildError(f"Transmission line '{link}' needs exactly two ends (found {len(ends)})")
        (t1, it, where, m1), (t2, _it2, _w2, m2) = ends
        z0 = E(it.get("z0"), where, 50.0)
        vf = E(it.get("vf"), where, 1.0) or 1.0
        length = E(it.get("length"), where, 0.0)
        if length <= 0:
            length = float(np.linalg.norm(m2 - m1))
        nec_len = length / vf
        if it.get("crossed") == "yes":
            z0 = -abs(z0)
        ctrl.append(f"TL {t1} 1 {t2} 1 {_fmt(z0)} {_fmt(nec_len)} 0 0 0 0")

    mat = design.get("material", "copper")
    sigma = MATERIALS.get(mat) if mat in MATERIALS else None
    if mat not in MATERIALS:
        try:
            sigma = float(mat)
        except ValueError:
            sigma = None
    if sigma:
        ctrl.append(f"LD 5 0 0 0 {_fmt(sigma)}")

    # NEC needs all LD cards together; a later LD group would replace earlier ones
    ctrl = [c for c in ctrl if c.startswith("LD")] + [c for c in ctrl if not c.startswith("LD")]
    if not any(c.startswith("EX") for c in ctrl):
        warnings.append("No feed point yet: add one with Insert ▸ Feed point")

    # nec2c reads comment cards into a fixed buffer: keep each one short
    lines = [f"CM {design.get('name', 'Antenna')}"[:72]]
    for ln in (design.get("notes") or "").splitlines():
        for part in textwrap.wrap(ln.strip(), 68):
            lines.append("CM " + part)
    lines.append("CM Generated by NEC Next from a builder design")
    lines.append("CE")
    for w in wires:
        lines.append(f"GW {w.tag} {w.segs} " + " ".join(_fmt(v) for v in (*w.p1, *w.p2)) + f" {_fmt(w.radius)}")
    lines.append("GE 0")
    lines += ctrl
    lines += sim_cards or []
    lines.append("EN")
    return GenResult("\n".join(lines) + "\n", wires, tag_map, lumped_rows, warnings, seg_target, symbols,
                     positions)


# ------------------------------------------------------------------ files
def save(design: dict, path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(design, f, indent=2)


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if d.get("format") != "necnext-design":
        raise ValueError("Not a NEC Next design file")
    base = new_design()
    base.update(d)
    for el in base["elements"]:
        for k, v in new_element().items():
            el.setdefault(k, v)
        for it in el["items"]:
            for k, v in ITEM_DEFAULTS.get(it["type"], {}).items():
                it.setdefault(k, v)
            it.setdefault("name", "")
    return base


# ------------------------------------------------------------------ import
def _az_el(d: np.ndarray) -> Tuple[float, float]:
    d = d / np.linalg.norm(d)
    el = math.degrees(math.asin(max(-1.0, min(1.0, d[2]))))
    if abs(abs(el) - 90) < 1e-6:
        return float("nan"), el
    return math.degrees(math.atan2(d[1], d[0])), el


def _wrap(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def import_deck(deck: Deck) -> Tuple[dict, List[str]]:
    """Best-effort conversion of a NEC deck into a builder design.

    Straight wires that meet end-to-end become one element with turns between
    them.  Feeds (EX) and single-segment loads (LD 0/1/4) become lumped items.
    Returns (design, notes about anything that could not be converted).
    """
    notes: List[str] = []
    design = new_design(deck.comments().splitlines()[0] if deck.comments() else "Imported antenna")
    design["notes"] = "\n".join(deck.comments().splitlines()[1:])
    design["variables"] = [[v.name, v.expr] for v in deck.variables]
    scale = 1.0
    unsupported = set()
    gws = []
    for c in deck.cards:
        if c.kind == "GW":
            iv, fv = c.ints(), c.floats()
            if len(fv) < 7:
                continue
            gws.append({"tag": iv[0], "ns": max(1, iv[1]), "p1": np.array(fv[0:3]) * 1.0,
                        "p2": np.array(fv[3:6]) * 1.0, "r": fv[6], "loads": []})
        elif c.kind == "GS":
            scale = c.floats()[0] if c.floats() and c.floats()[0] else 1.0
        elif c.kind == "NT" and (c.comment or "").lower().startswith("xfmr"):
            pass
        elif c.kind in ("GA", "GH", "GM", "GR", "GX", "SP", "SM", "SC", "GC", "GF", "NT", "TL", "NE", "NH"):
            unsupported.add(c.kind)
    if scale != 1.0:
        for g in gws:
            g["p1"] *= scale
            g["p2"] *= scale
            g["r"] *= scale
    by_tag = {}
    for g in gws:
        by_tag.setdefault(g["tag"], []).append(g)
    # transformer-fed sources (NT ' xfmr a:b with the EX on port 1): move the feed to port 2
    xfmr_moves = {}
    for c in deck.cards:
        if c.kind == "NT" and (c.comment or "").lower().startswith("xfmr"):
            iv = c.ints()
            ratio = c.comment.split(None, 1)[1].split()[0] if len(c.comment.split()) > 1 else ""
            xfmr_moves[iv[0]] = (iv[2], iv[3], ratio)
    for src_tag in xfmr_moves:
        if src_tag in by_tag and len(by_tag[src_tag]) == 1 and by_tag[src_tag][0]["ns"] == 1:
            gws.remove(by_tag[src_tag][0])
    for c in deck.cards:
        if c.kind == "EX" and c.ints() and len(c.ints()) > 1 and c.ints()[1] in xfmr_moves:
            iv, fv = c.ints(), c.floats()
            t2, s2, ratio = xfmr_moves[iv[1]]
            if t2 in by_tag:
                vr, vi = (fv + [1, 0])[:2]
                by_tag[t2][0]["loads"].append((s2, new_item(
                    "feed", voltage=f"{math.hypot(vr, vi):.6g}",
                    phase=f"{math.degrees(math.atan2(vi, vr)):.6g}", ratio=ratio)))
            continue
        if c.kind == "EX":
            iv, fv = c.ints(), c.floats()
            if iv[0] not in (0, 5) or iv[1] not in by_tag:
                unsupported.add("EX (plane wave or unknown tag)")
                continue
            g = by_tag[iv[1]][0]
            vr, vi = (fv + [1, 0])[:2]
            mag = math.hypot(vr, vi)
            g["loads"].append((iv[2], new_item("feed", voltage=f"{mag:.6g}",
                                               phase=f"{math.degrees(math.atan2(vi, vr)):.6g}")))
        elif c.kind == "LD":
            iv, fv = c.ints(), (c.floats() + [0, 0, 0])[:3]
            if iv[0] == 5:
                design["material"] = next((k for k, v in MATERIALS.items() if v and abs(v - fv[0]) / v < 0.05),
                                          f"{fv[0]:.6g}")
                continue
            if iv[1] not in by_tag or iv[2] == 0 or (iv[3] and iv[3] != iv[2]) or iv[0] in (2, 3):
                unsupported.add("LD spread over several segments")
                continue
            R, L, C = fv
            if iv[0] == 0:
                kind = "series_lc" if (L and C) else ("coil" if L else ("capacitor" if C else "resistor"))
                it = new_item("load", kind=kind, R=f"{R:.6g}", L=f"{L:.6g}", C=f"{C:.6g}", Q="0")
            elif iv[0] == 1:
                it = new_item("load", kind="trap", L=f"{L:.6g}", C=f"{C:.6g}", Q="0")
            else:
                it = new_item("load", kind="impedance", R=f"{R:.6g}", X=f"{L:.6g}")
            by_tag[iv[1]][0]["loads"].append((iv[2], it))
    if unsupported:
        notes.append("Not converted: " + ", ".join(sorted(unsupported)))

    # chain wires that meet end to end
    elements = []
    cur = None
    end = None
    az = el = 0.0
    for g in gws:
        p1, p2 = g["p1"], g["p2"]
        if cur is not None and np.linalg.norm(p1 - end) < 1e-6:
            pass
        elif cur is not None and np.linalg.norm(p2 - end) < 1e-6 and not g["loads"]:
            p1, p2 = p2, p1
        else:
            cur = None
        d = p2 - p1
        L = float(np.linalg.norm(d))
        if L < 1e-9:
            continue
        a, e = _az_el(d)
        if cur is None:
            a0 = 0.0 if math.isnan(a) else a
            cur = new_element(f"Element {len(elements) + 1}", f"{p1[0]:.6g}", f"{p1[1]:.6g}", f"{p1[2]:.6g}",
                              f"{a0:.6g}", f"{e:.6g}")
            elements.append(cur)
            az, el = a0, e
        else:
            u = d / L
            h0 = heading(az, el)
            ang = math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(h0, u))))))
            done = ang < 1e-4
            # prefer a plain up/down or left/right turn when one does the job
            for dy, dp in ((0.0, ang), (0.0, -ang), (ang, 0.0), (-ang, 0.0)):
                if done:
                    break
                if np.allclose(heading(az + dy, el + dp), u, atol=1e-6):
                    cur["items"].append(new_item("turn", yaw=f"{dy:.6g}", pitch=f"{dp:.6g}"))
                    az, el = az + dy, el + dp
                    done = True
            if not done:
                na = az if math.isnan(a) else a
                dy, dp = _wrap(na - az), e - el
                cur["items"].append(new_item("turn", yaw=f"{dy:.6g}", pitch=f"{dp:.6g}"))
                az, el = na, e
        rad = f"{g['r']:.6g}"
        rad = "" if design["radius"] and abs(evaluate(design["radius"], {}) - g["r"]) < 1e-12 else rad
        # split at lumped positions
        loads = sorted(g["loads"], key=lambda t: t[0])
        prev = 0.0
        for seg, it in loads:
            # a source on an end segment means "at the end"; otherwise the segment centre
            at = 0.0 if seg <= 1 else (L if seg >= g["ns"] else (seg - 0.5) / g["ns"] * L)
            if g["ns"] == 1:
                at = L / 2
            if at - prev > 1e-9:
                cur["items"].append(new_item("wire", length=f"{at - prev:.6g}", radius=rad))
            cur["items"].append(it)
            prev = at
        if L - prev > 1e-9:
            cur["items"].append(new_item("wire", length=f"{L - prev:.6g}", radius=rad))
        end = g["p2"] if np.allclose(p2, g["p2"]) else g["p1"]
        end = p2
    if gws:
        design["radius"] = f"{gws[0]['r']:.6g}"
        for elx in elements:
            for it in elx["items"]:
                if it["type"] == "wire" and it.get("radius") and abs(float(it["radius"]) - gws[0]["r"]) < 1e-12:
                    it["radius"] = ""
    design["elements"] = elements
    return design, notes
