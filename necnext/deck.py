"""NEC2 deck model: parsing, symbolic variables (SY), and run-deck generation.

The text of the deck is the source of truth.  A deck is parsed into a list of
:class:`Card` objects; fields may contain expressions that reference SY
variables (4NEC2 style), which are evaluated to numbers when building the deck
that is handed to nec2c.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# Number of leading integer fields for each card type.  Everything after these
# is a floating point field.
INT_FIELDS: Dict[str, int] = {
    "GW": 2, "GA": 2, "GH": 2, "GM": 2, "GR": 2, "GS": 2, "GE": 1, "GX": 2,
    "GC": 2, "SP": 2, "SM": 2, "SC": 2, "GF": 1, "GD": 2,
    "GN": 4, "EX": 4, "LD": 4, "FR": 4, "RP": 4, "TL": 4, "NT": 4, "PT": 4,
    "XQ": 1, "NE": 4, "NH": 4, "EK": 1, "KH": 2, "CP": 4, "PQ": 4, "WG": 0,
    "NX": 0, "EN": 0,
}
GEOMETRY_CARDS = {"GW", "GA", "GH", "GM", "GR", "GS", "GX", "GC", "SP", "SM", "SC", "GF"}
CONTROL_CARDS = {"GN", "EX", "LD", "FR", "RP", "TL", "NT", "PT", "XQ", "NE", "NH",
                 "EK", "KH", "CP", "PQ", "GD", "WG", "NX", "EN"}
# Cards the application generates itself in "managed" run mode.
MANAGED_CARDS = {"FR", "RP", "XQ", "GN", "GD", "PT", "NE", "NH", "EN", "NX", "WG", "CP", "PQ"}

CARD_HELP = {
    "CM": "Comment", "CE": "End of comments",
    "SY": "Symbol / variable definitions (e.g. SY len=5.2, h=10)",
    "GW": "Straight wire: tag segs x1 y1 z1 x2 y2 z2 radius",
    "GA": "Wire arc: tag segs radius ang1 ang2 wire_radius",
    "GH": "Helix: tag segs spacing length a1 b1 a2 b2 radius",
    "GM": "Move/rotate/copy: tag_inc copies rotX rotY rotZ dX dY dZ first_tag",
    "GR": "Cylindrical repeat: tag_inc copies",
    "GS": "Scale: 0 0 factor",
    "GX": "Reflect: tag_inc XYZ-flags",
    "GE": "Geometry end: ground flag (0 none, 1 ground, -1 no image)",
    "SP": "Surface patch", "SM": "Multiple patch", "SC": "Patch continuation",
    "GC": "Tapered wire (follows GW with radius 0)",
    "GN": "Ground: type (-1 none, 0 refl, 1 perfect, 2 Sommerfeld) ... eps sigma",
    "EX": "Excitation: type tag seg flags real imag",
    "LD": "Load: type tag seg_first seg_last R/L/C or conductivity",
    "TL": "Transmission line: tag1 seg1 tag2 seg2 Z0 length ...",
    "NT": "Two-port network",
    "FR": "Frequency: type count 0 0 start step",
    "RP": "Radiation pattern: mode ntheta nphi XNDA theta0 phi0 dtheta dphi",
    "XQ": "Execute", "PT": "Print control", "EK": "Extended thin-wire kernel",
    "NE": "Near E field", "NH": "Near H field", "EN": "End of run",
}

_FUNCS = {
    # 4NEC2 trig functions work in degrees.
    "sin": lambda d: math.sin(math.radians(d)),
    "cos": lambda d: math.cos(math.radians(d)),
    "tan": lambda d: math.tan(math.radians(d)),
    "atn": lambda x: math.degrees(math.atan(x)),
    "atan": lambda x: math.degrees(math.atan(x)),
    "asin": lambda x: math.degrees(math.asin(x)),
    "acos": lambda x: math.degrees(math.acos(x)),
    "sqr": math.sqrt, "sqrt": math.sqrt, "abs": abs, "exp": math.exp,
    "log": math.log10, "ln": math.log, "int": int, "round": round,
    "min": min, "max": max, "pi": math.pi, "PI": math.pi,
}
# Unit suffixes understood in numeric fields (converted to metres).
_UNITS = {"mm": 1e-3, "cm": 1e-2, "km": 1e3, "in": 0.0254, "ft": 0.3048,
          # component values (converted to F / H)
          "pF": 1e-12, "nF": 1e-9, "uF": 1e-6, "\u00b5F": 1e-6,
          "nH": 1e-9, "uH": 1e-6, "\u00b5H": 1e-6, "mH": 1e-3}
_UNIT_ALT = "|".join(sorted(_UNITS, key=len, reverse=True))
_UNIT_RE = re.compile(r"^([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)(" + _UNIT_ALT + r")$")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*$")


class DeckError(Exception):
    def __init__(self, msg: str, line: Optional[int] = None):
        super().__init__(msg)
        self.line = line


@dataclass
class Card:
    kind: str                     # two-letter card id, upper case
    fields: List[str]             # raw field text (may be expressions)
    line: int                     # 0-based line number in source text
    text: str = ""                # comment text for CM/CE, raw text for SY
    comment: str = ""             # trailing ' comment on a data card
    values: List[float] = field(default_factory=list)  # evaluated fields

    def ints(self) -> List[int]:
        n = INT_FIELDS.get(self.kind, 0)
        return [int(round(v)) for v in self.values[:n]]

    def floats(self) -> List[float]:
        n = INT_FIELDS.get(self.kind, 0)
        return self.values[n:]

    def to_nec(self) -> str:
        if self.kind in ("CM", "CE"):
            return f"{self.kind} {self.text}".rstrip()
        n = INT_FIELDS.get(self.kind, 0)
        parts = [self.kind]
        for i, v in enumerate(self.values):
            parts.append(str(int(round(v))) if i < n else _fmt(v))
        return " ".join(parts)


def _fmt(v: float) -> str:
    if v == 0:
        return "0"
    if abs(v) >= 1e-4 and abs(v) < 1e7:
        s = f"{v:.10g}"
    else:
        s = f"{v:.8E}"
    return s


def split_fields(s: str) -> List[str]:
    """Split on whitespace/commas that are not inside parentheses."""
    out, cur, depth = [], [], 0
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if depth == 0 and (ch.isspace() or ch == ","):
            if cur:
                out.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


def evaluate(expr: str, symbols: Dict[str, float]) -> float:
    expr = expr.strip()
    if not expr:
        return 0.0
    try:
        return float(expr)
    except ValueError:
        pass
    m = _UNIT_RE.match(expr)
    if m:
        return float(m.group(1)) * _UNITS[m.group(2)]
    py = expr.replace("^", "**")
    # unit suffix on a number inside an expression, e.g. 2*12in
    py = re.sub(r"(\d+\.?\d*|\.\d+)(" + _UNIT_ALT + r")(?![A-Za-z_0-9])",
                lambda mm: f"({mm.group(1)}*{_UNITS[mm.group(2)]})", py)
    env = dict(_FUNCS)
    env.update(symbols)
    try:
        val = eval(compile(py, "<expr>", "eval"), {"__builtins__": {}}, env)  # noqa: S307
    except NameError as e:
        raise DeckError(f"Unknown variable in '{expr}': {e}") from None
    except Exception as e:  # SyntaxError, ZeroDivisionError...
        raise DeckError(f"Cannot evaluate '{expr}': {e}") from None
    return float(val)


@dataclass
class Variable:
    name: str
    expr: str
    value: float
    line: int


class Deck:
    """A parsed NEC deck."""

    def __init__(self, text: str = ""):
        self.text = text
        self.cards: List[Card] = []
        self.variables: List[Variable] = []
        self.symbols: Dict[str, float] = {}
        self.errors: List[DeckError] = []
        self.parse(text)

    # ------------------------------------------------------------------ parse
    def parse(self, text: str) -> None:
        self.text = text
        self.cards, self.variables, self.symbols, self.errors = [], [], {}, []
        for i, raw in enumerate(text.splitlines()):
            line = raw.rstrip()
            if not line.strip():
                continue
            kind = line.strip()[:2].upper()
            rest = line.strip()[2:]
            if kind in ("CM", "CE"):
                self.cards.append(Card(kind, [], i, text=rest.strip()))
                continue
            if line.strip().startswith(("'", "!", "#")):
                continue  # whole-line comment
            # trailing comment
            comment = ""
            for c in ("'", "!"):
                if c in rest and kind != "SY":
                    rest, comment = rest.split(c, 1)[0], rest.split(c, 1)[1].strip()
            if kind == "SY":
                self._parse_sy(rest, i)
                self.cards.append(Card("SY", [], i, text=rest.strip()))
                continue
            if kind not in INT_FIELDS:
                self.errors.append(DeckError(f"Unknown card '{kind}'", i))
                continue
            self.cards.append(Card(kind, split_fields(rest), i, comment=comment))
        self.evaluate()

    def _parse_sy(self, rest: str, line: int) -> None:
        body = rest.split("'", 1)[0]
        for part in re.split(r",(?![^(]*\))", body):
            part = part.strip()
            if not part:
                continue
            if "=" not in part:
                self.errors.append(DeckError(f"Bad SY definition '{part}'", line))
                continue
            name, expr = (s.strip() for s in part.split("=", 1))
            if not _IDENT_RE.match(name):
                self.errors.append(DeckError(f"Bad variable name '{name}'", line))
                continue
            try:
                val = evaluate(expr, self.symbols)
            except DeckError as e:
                e.line = line
                self.errors.append(e)
                val = float("nan")
            self.symbols[name] = val
            self.variables.append(Variable(name, expr, val, line))

    def evaluate(self) -> None:
        for c in self.cards:
            if c.kind in ("CM", "CE", "SY"):
                continue
            vals = []
            for f in c.fields:
                try:
                    vals.append(evaluate(f, self.symbols))
                except DeckError as e:
                    e.line = c.line
                    self.errors.append(e)
                    vals.append(0.0)
            n = INT_FIELDS.get(c.kind, 0)
            nmin = n + (6 if c.kind in ("EX", "GN", "RP", "TL", "NT", "NE", "NH") else 0)
            while len(vals) < nmin:
                vals.append(0.0)
            c.values = vals

    # --------------------------------------------------------------- queries
    def comments(self) -> str:
        return "\n".join(c.text for c in self.cards if c.kind in ("CM", "CE") and c.text)

    def first(self, kind: str) -> Optional[Card]:
        for c in self.cards:
            if c.kind == kind:
                return c
        return None

    def all(self, kind: str) -> List[Card]:
        return [c for c in self.cards if c.kind == kind]

    def geometry_cards(self) -> List[Card]:
        return [c for c in self.cards if c.kind in GEOMETRY_CARDS]

    def ge_card(self) -> Optional[Card]:
        return self.first("GE")

    def frequency_hint(self) -> Optional[Tuple[float, float, int]]:
        """(start, stop, steps) from the first FR card, if any."""
        fr = self.first("FR")
        if not fr:
            return None
        ints, fl = fr.ints(), fr.floats()
        n = max(1, ints[1] if len(ints) > 1 else 1)
        f0 = fl[0] if fl else 14.0
        df = fl[1] if len(fl) > 1 else 0.0
        if ints and ints[0] == 1:  # multiplicative stepping
            return f0, f0 * (df ** (n - 1)) if df else f0, n
        return f0, f0 + df * (n - 1), n

    def ground_hint(self) -> Optional[Dict]:
        gn = self.first("GN")
        if not gn:
            ge = self.ge_card()
            if ge and ge.ints() and ge.ints()[0] != 0:
                return {"type": 1}
            return None
        ints, fl = gn.ints(), gn.floats()
        g = {"type": ints[0], "eps": fl[0] if fl else 13.0, "sigma": fl[1] if len(fl) > 1 else 0.005}
        return g

    # ------------------------------------------------------------ build runs
    def evaluated_lines(self, keep=None, drop=None) -> List[str]:
        out = []
        for c in self.cards:
            if c.kind == "SY":
                continue
            if keep is not None and c.kind not in keep:
                continue
            if drop is not None and c.kind in drop:
                continue
            out.append(c.to_nec())
        return out

    def geometry_block(self, ground: bool) -> List[str]:
        """CM/CE + geometry cards + GE with ground flag."""
        lines = [c.to_nec() for c in self.cards if c.kind == "CM"]
        lines.append("CE")
        lines += [c.to_nec() for c in self.geometry_cards()]
        ge = self.ge_card()
        flag = ge.ints()[0] if ge and ge.ints() else 0
        if ground and flag == 0:
            flag = 1
        if not ground:
            flag = 0
        lines.append(f"GE {flag}")
        return lines

    def source_block(self) -> List[str]:
        """Excitations, loads, transmission lines, networks, kernel options."""
        keep = {"EX", "LD", "TL", "NT", "EK", "KH"}
        return [c.to_nec() for c in self.cards if c.kind in keep]

    def as_is(self) -> str:
        lines = self.evaluated_lines()
        if not lines or not lines[-1].startswith("EN"):
            lines.append("EN")
        return "\n".join(lines) + "\n"

    # --------------------------------------------------------- text editing
    def set_variable(self, name: str, new_value) -> str:
        """Return new deck text with variable *name* set to *new_value*."""
        lines = self.text.splitlines()
        for v in self.variables:
            if v.name == name:
                ln = lines[v.line]
                pat = re.compile(r"(\b" + re.escape(name) + r"\s*=\s*)([^,']+)")
                lines[v.line] = pat.sub(lambda m: m.group(1) + (new_value if isinstance(new_value, str) else _fmt(new_value)), ln, count=1)
                break
        return "\n".join(lines) + ("\n" if self.text.endswith("\n") else "")


@dataclass
class Ground:
    """Ground model used for managed runs."""
    kind: str = "free"        # free | perfect | real
    eps: float = 13.0
    sigma: float = 0.005
    sommerfeld: bool = True

    def card(self) -> Optional[str]:
        if self.kind == "free":
            return None
        if self.kind == "perfect":
            return "GN 1"
        t = 2 if self.sommerfeld else 0
        return f"GN {t} 0 0 0 {_fmt(self.eps)} {_fmt(self.sigma)}"

    @property
    def present(self) -> bool:
        return self.kind != "free"


GROUND_PRESETS = {
    "Average (13, 5 mS/m)": (13.0, 0.005),
    "Poor / city (5, 1 mS/m)": (5.0, 0.001),
    "Good / pasture (20, 30 mS/m)": (20.0, 0.0303),
    "Very good (30, 10 mS/m)": (30.0, 0.010),
    "Sandy dry (10, 2 mS/m)": (10.0, 0.002),
    "Fresh water (80, 1 mS/m)": (80.0, 0.001),
    "Sea water (81, 5 S/m)": (81.0, 5.0),
}


def build_sweep_deck(deck: Deck, ground: Ground, f_start: float, f_stop: float,
                     steps: int, with_gain: bool, gain_step: float = 10.0) -> str:
    lines = deck.geometry_block(ground.present)
    lines += deck.source_block()
    if ground.card():
        lines.append(ground.card())
    lines.append("PT -1 0 0 0")
    steps = max(1, int(steps))
    df = (f_stop - f_start) / (steps - 1) if steps > 1 else 0.0
    lines.append(f"FR 0 {steps} 0 0 {_fmt(f_start)} {_fmt(df)}")
    if with_gain:
        nth, th0 = _theta_range(ground.present, gain_step)
        nph = int(round(360.0 / gain_step))
        lines.append(f"RP 0 {nth} {nph} 1000 {_fmt(th0)} 0 {_fmt(gain_step)} {_fmt(gain_step)}")
    else:
        lines.append("XQ 0")
    lines.append("EN")
    return "\n".join(lines) + "\n"


def build_pattern_deck(deck: Deck, ground: Ground, freq: float, step: float = 5.0) -> str:
    lines = deck.geometry_block(ground.present)
    lines += deck.source_block()
    if ground.card():
        lines.append(ground.card())
    lines.append(f"FR 0 1 0 0 {_fmt(freq)} 0")
    nth, th0 = _theta_range(ground.present, step)
    nph = int(round(360.0 / step)) + 1
    lines.append(f"RP 0 {nth} {nph} 1000 {_fmt(th0)} 0 {_fmt(step)} {_fmt(step)}")
    lines.append("EN")
    return "\n".join(lines) + "\n"


def build_geometry_deck(deck: Deck) -> str:
    gnd = deck.ge_card() is not None and bool(deck.ge_card().ints()) and deck.ge_card().ints()[0] != 0
    lines = deck.geometry_block(gnd)
    lines.append("EN")
    return "\n".join(lines) + "\n"


def _theta_range(ground: bool, step: float) -> Tuple[int, float]:
    if ground:
        # theta 0..90 (upper hemisphere); stop just above the horizon
        return int(round(90.0 / step)) + 1, 0.0
    return int(round(180.0 / step)) + 1, 0.0


def split_unit(expr: str):
    """'4.7uH' -> (4.7, 'uH', 1e-6); plain number -> (x, '', 1); otherwise None."""
    e = expr.strip()
    try:
        return float(e), "", 1.0
    except ValueError:
        pass
    m = _UNIT_RE.match(e)
    if m:
        return float(m.group(1)), m.group(2), _UNITS[m.group(2)]
    return None
