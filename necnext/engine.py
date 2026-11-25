"""Running nec2c and parsing its output."""
from __future__ import annotations

import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

NUM = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[EeDd][-+]?\d+)?")


# --------------------------------------------------------------- locating
def find_nec2c(user_path: str = "") -> Optional[str]:
    """Locate the nec2c executable."""
    if user_path and os.path.isfile(user_path):
        return user_path
    env = os.environ.get("NEC2C")
    if env and os.path.isfile(env):
        return env
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    exe = "nec2c.exe" if os.name == "nt" else "nec2c"
    cands = []
    if os.name == "nt":
        cands.append(os.path.join(here, "bin", "windows", exe))
    else:
        mach = platform.machine().lower()
        cands.append(os.path.join(here, "bin", f"linux-{mach}", exe))
    if getattr(sys, "frozen", False):  # PyInstaller bundle
        cands.insert(0, os.path.join(getattr(sys, "_MEIPASS", here), exe))
    for c in cands:
        if os.path.isfile(c):
            return c
    return shutil.which("nec2c") or shutil.which("nec2c.exe")


class NecError(Exception):
    pass


# --------------------------------------------------------------- results
@dataclass
class Segment:
    tag: int
    p1: np.ndarray
    p2: np.ndarray
    radius: float


@dataclass
class Patch:
    center: np.ndarray
    normal: np.ndarray
    t1: np.ndarray
    t2: np.ndarray
    area: float


@dataclass
class Geometry:
    segments: List[Segment] = field(default_factory=list)
    patches: List[Patch] = field(default_factory=list)

    def bounds(self):
        pts = [s.p1 for s in self.segments] + [s.p2 for s in self.segments] + \
              [p.center for p in self.patches]
        if not pts:
            return np.zeros(3), np.ones(3)
        a = np.array(pts)
        return a.min(axis=0), a.max(axis=0)


@dataclass
class Source:
    tag: int
    seg: int
    voltage: complex
    current: complex
    z: complex
    power: float


@dataclass
class Pattern:
    theta: np.ndarray       # unique theta values (deg)
    phi: np.ndarray         # unique phi values (deg)
    vert: np.ndarray        # [ntheta, nphi] dB
    horiz: np.ndarray
    total: np.ndarray
    gain_type: str = "POWER"

    def max_gain(self):
        """(gain, theta, phi) of the main lobe.

        Ties (e.g. a dipole's doughnut) are broken toward the horizon so the
        azimuth cut is meaningful.
        """
        g = self.total
        gmax = float(np.nanmax(g))
        cand = np.argwhere(g >= gmax - 0.02)
        k = min(cand, key=lambda ij: (abs(self.theta[ij[0]] - 90.0), self.phi[ij[1]] % 360.0))
        return gmax, float(self.theta[k[0]]), float(self.phi[k[1]])

    def value_at(self, theta: float, phi: float, which: str = "total") -> float:
        arr = getattr(self, which)
        phi = phi % 360.0
        it = int(np.argmin(np.abs(self.theta - theta)))
        dp = np.abs(((self.phi - phi) + 180.0) % 360.0 - 180.0)
        ip = int(np.argmin(dp))
        return float(arr[it, ip])


@dataclass
class FreqResult:
    freq: float
    sources: List[Source] = field(default_factory=list)
    currents: Optional[np.ndarray] = None     # complex per segment
    input_power: float = float("nan")
    radiated_power: float = float("nan")
    efficiency: float = float("nan")
    pattern: Optional[Pattern] = None

    @property
    def z(self) -> complex:
        return self.sources[0].z if self.sources else complex("nan")

    def swr(self, z0: float = 50.0) -> float:
        z = self.z
        g = abs((z - z0) / (z + z0))
        return (1 + g) / (1 - g) if g < 1 else float("inf")


@dataclass
class RunResult:
    freqs: List[FreqResult]
    geometry: Geometry
    output: str
    warnings: List[str] = field(default_factory=list)


# --------------------------------------------------------------- parsing
def _nums(line: str) -> List[float]:
    return [float(x.replace("D", "E").replace("d", "e")) for x in NUM.findall(line)]


def parse_output(text: str) -> RunResult:
    lines = text.splitlines()
    geom = Geometry()
    freqs: List[FreqResult] = []
    warnings: List[str] = []
    cur: Optional[FreqResult] = None
    i, n = 0, len(lines)
    while i < n:
        ln = lines[i]
        s = ln.strip()
        if "SEGMENTATION DATA" in ln:
            i = _skip_header(lines, i)
            while i < n and lines[i].strip():
                v = _nums(lines[i])
                if len(v) >= 12:
                    c = np.array(v[1:4])
                    L, a, b = v[4], math.radians(v[5]), math.radians(v[6])
                    d = np.array([math.cos(a) * math.cos(b), math.cos(a) * math.sin(b), math.sin(a)])
                    geom.segments.append(Segment(int(v[11]), c - d * L / 2, c + d * L / 2, v[7]))
                i += 1
            continue
        if "SURFACE PATCH DATA" in ln:
            i = _skip_header(lines, i)
            while i < n and lines[i].strip():
                v = _nums(lines[i])
                if len(v) >= 14:
                    geom.patches.append(Patch(np.array(v[1:4]), np.array(v[4:7]),
                                              np.array(v[8:11]), np.array(v[11:14]), v[7]))
                i += 1
            continue
        if s.startswith("FREQUENCY :") or s.startswith("FREQUENCY="):
            v = _nums(s)
            cur = FreqResult(freq=v[0])
            freqs.append(cur)
        elif "ANTENNA INPUT PARAMETERS" in ln and cur is not None:
            i = _skip_header(lines, i)
            while i < n and lines[i].strip():
                v = _nums(lines[i])
                if len(v) >= 11:
                    cur.sources.append(Source(int(v[0]), int(v[1]), complex(v[2], v[3]),
                                              complex(v[4], v[5]), complex(v[6], v[7]), v[10]))
                i += 1
            continue
        elif "CURRENTS AND LOCATION" in ln and cur is not None:
            i = _skip_header(lines, i)
            cs = []
            while i < n and lines[i].strip():
                v = _nums(lines[i])
                if len(v) >= 10:
                    cs.append(complex(v[6], v[7]))
                i += 1
            cur.currents = np.array(cs)
            continue
        elif s.startswith("INPUT POWER") and cur is not None:
            cur.input_power = _nums(s)[0]
        elif s.startswith("RADIATED POWER") and cur is not None:
            cur.radiated_power = _nums(s)[0]
        elif s.startswith("EFFICIENCY") and cur is not None:
            cur.efficiency = _nums(s)[0]
        elif "RADIATION PATTERNS" in ln and cur is not None:
            gtype = "POWER"
            j = i + 1
            while j < n and "DEGREES" not in lines[j]:
                if "DIRECTIVE GAINS" in lines[j]:
                    gtype = "DIRECTIVE"
                j += 1
            i = j + 1
            rows = []
            while i < n and lines[i].strip():
                v = _nums(lines[i])
                if len(v) >= 5:
                    rows.append(v[:5])
                i += 1
            if rows:
                cur.pattern = _grid(np.array(rows), gtype)
            continue
        elif ("ERROR" in s or "WARNING" in s) and "INTEGRATION" not in s:
            warnings.append(s)
        i += 1
    return RunResult(freqs, geom, text, warnings)


def _skip_header(lines: List[str], i: int) -> int:
    """Advance from a section title to the first data row (after 'No:' header)."""
    j = i + 1
    while j < len(lines) and j < i + 12:
        if "No:" in lines[j]:
            return j + 1
        j += 1
    return i + 1


def _grid(rows: np.ndarray, gtype: str) -> Pattern:
    th = np.unique(np.round(rows[:, 0], 4))
    ph = np.unique(np.round(rows[:, 1], 4))
    shape = (len(th), len(ph))
    v = np.full(shape, -999.99)
    h = np.full(shape, -999.99)
    t = np.full(shape, -999.99)
    ti = {x: k for k, x in enumerate(th)}
    pi = {x: k for k, x in enumerate(ph)}
    for r in rows:
        a, b = ti[round(r[0], 4)], pi[round(r[1], 4)]
        v[a, b], h[a, b], t[a, b] = r[2], r[3], r[4]
    return Pattern(th, ph, v, h, t, gtype)


# --------------------------------------------------------------- running
class Runner:
    """Runs nec2c on a deck string. Thread-safe per instance; cancellable."""

    def __init__(self, exe: Optional[str] = None):
        self.exe = exe or find_nec2c()
        self._proc: Optional[subprocess.Popen] = None
        self._cancelled = False

    def cancel(self):
        self._cancelled = True
        if self._proc and self._proc.poll() is None:
            self._proc.kill()

    def run(self, deck_text: str, timeout: float = 600.0) -> RunResult:
        if not self.exe:
            raise NecError("nec2c was not found. Install it (Linux: 'sudo apt install nec2c') "
                           "or set its path in Settings.")
        self._cancelled = False
        with tempfile.TemporaryDirectory(prefix="necnext_") as d:
            inp = os.path.join(d, "in.nec")
            out = os.path.join(d, "out.txt")
            with open(inp, "w", newline="\n") as f:
                f.write(deck_text)
            kw = {}
            if os.name == "nt":
                kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            self._proc = subprocess.Popen([self.exe, "-i", inp, "-o", out], cwd=d,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          text=True, **kw)
            try:
                so, se = self._proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                raise NecError("nec2c timed out")
            rc = self._proc.returncode
            self._proc = None
            if self._cancelled:
                raise NecError("Cancelled")
            text = ""
            if os.path.exists(out):
                with open(out, errors="replace") as f:
                    text = f.read()
        res = parse_output(text)
        err_lines = [l.strip() for l in (se or "").splitlines() if l.strip()]
        if rc != 0 or (not res.freqs and not res.geometry.segments and not res.geometry.patches):
            msg = _extract_error(text) or "\n".join(err_lines) or f"nec2c exited with code {rc}"
            raise NecError(msg)
        res.warnings += err_lines
        return res


def _extract_error(text: str) -> str:
    out = []
    for l in text.splitlines():
        u = l.strip()
        if any(k in u for k in ("ERROR", "INVALID", "FAULTY", "NOT ALLOWED", "EXCEED")):
            out.append(u)
    return "\n".join(out[-5:])


# --------------------------------------------------------------- analysis
def swr_from_z(z: np.ndarray, z0: float) -> np.ndarray:
    g = np.abs((z - z0) / (z + z0))
    with np.errstate(divide="ignore", invalid="ignore"):
        s = (1 + g) / (1 - g)
    s[g >= 1] = np.inf
    return s


def pattern_metrics(p: Pattern) -> dict:
    gmax, th, ph = p.max_gain()
    el = 90.0 - th
    back = p.value_at(th, ph + 180.0)
    # front-to-rear: worst value over rear 180 degrees of azimuth at same theta
    it = int(np.argmin(np.abs(p.theta - th)))
    rel = ((p.phi - ph) % 360.0)
    rear = (rel >= 90.0) & (rel <= 270.0)
    fr = gmax - float(np.max(p.total[it, rear])) if rear.any() else float("nan")
    # azimuth beamwidth at theta of max
    bw_az = _beamwidth(p.phi, p.total[it, :], ph, wrap=True)
    ip = int(np.argmin(np.abs(((p.phi - ph) + 180) % 360 - 180)))
    bw_el = _beamwidth(p.theta, p.total[:, ip], th, wrap=False)
    return {"gain": gmax, "theta": th, "elev": el, "phi": ph,
            "fb": gmax - back if back > -900 else float("inf"),
            "fr": fr, "bw_az": bw_az, "bw_el": bw_el}


def _beamwidth(ang: np.ndarray, g: np.ndarray, center: float, wrap: bool) -> float:
    if len(ang) < 3:
        return float("nan")
    gmax = np.max(g)
    thr = gmax - 3.0
    step = abs(ang[1] - ang[0]) or 1.0
    ic = int(np.argmin(np.abs(ang - center)))
    n = len(ang)
    # if last sample duplicates first (0 and 360) drop it for wrap
    if wrap and n > 1 and abs((ang[-1] - ang[0]) - 360.0) < 1e-6:
        n -= 1

    def walk(direction):
        k, dist = ic, 0.0
        prev = g[ic]
        for _ in range(n):
            k2 = k + direction
            if wrap:
                k2 %= n
            elif k2 < 0 or k2 >= n:
                return float("nan")
            val = g[k2]
            if val < thr:
                frac = (prev - thr) / (prev - val) if prev != val else 0
                return dist + frac * step
            dist += step
            prev = val
            k = k2
        return float("nan")

    a, b = walk(1), walk(-1)
    if math.isnan(a) or math.isnan(b):
        return float("nan")
    return min(a + b, 360.0)
