"""Run with: python -m pytest tests   (needs nec2c on PATH)"""
import glob, os
import pytest
from necnext.deck import Deck, Ground, build_pattern_deck, build_sweep_deck, evaluate
from necnext.engine import Runner, find_nec2c, pattern_metrics

EX = os.path.join(os.path.dirname(__file__), "..", "necnext", "examples")


def test_expressions():
    assert evaluate("12.7mm", {}) == pytest.approx(0.0127)
    assert evaluate("a/2+sin(30)", {"a": 4}) == pytest.approx(2.5)
    d = Deck("SY a=2, b=a*3\nGW 1 5 0 0 0 0 0 b 1mm\n")
    assert d.symbols["b"] == 6 and d.first("GW").values[7] == 6


@pytest.mark.skipif(not find_nec2c(), reason="nec2c not installed")
@pytest.mark.parametrize("fn", sorted(glob.glob(os.path.join(EX, "*.nec"))))
def test_examples_run(fn):
    d = Deck(open(fn).read())
    assert not d.errors
    lo, hi, n = d.frequency_hint()
    r = Runner().run(build_sweep_deck(d, Ground("real"), lo, hi, 5, True))
    assert len(r.freqs) == 5 and all(f.sources for f in r.freqs)
    p = Runner().run(build_pattern_deck(d, Ground("free"), (lo + hi) / 2, 10))
    m = pattern_metrics(p.freqs[0].pattern)
    assert -10 < m["gain"] < 20


# ---------------------------------------------------------------- builder
def test_builder_turns_move_coordinates():
    from necnext import builder as B
    d = B.new_design("loop")
    d["elements"] = [B.new_element("Loop", 0, 0, 10, 90, 0,
                                   [B.new_item("wire", length="2"), B.new_item("turn", yaw="0", pitch="90"),
                                    B.new_item("wire", length="2"), B.new_item("turn", yaw="0", pitch="90"),
                                    B.new_item("wire", length="2"), B.new_item("turn", yaw="0", pitch="90"),
                                    B.new_item("wire", length="2"), B.new_item("feed")])]
    g = B.generate(d, 30.0)
    end = g.positions[(0, 6)][0]
    assert abs(end[0]) < 1e-9 and abs(end[1]) < 1e-9 and abs(end[2] - 10) < 1e-9
    assert any(ln.startswith("EX 0 ") for ln in g.deck_text.splitlines())


def test_builder_lumped_items_sorted_and_carved():
    from necnext import builder as B
    d = B.new_design("dipole")
    d["elements"] = [B.new_element("D", 0, -5, 10, 90, 0,
                                   [B.new_item("wire", length="5"), B.new_item("feed"),
                                    B.new_item("load", kind="coil", L="1uH"), B.new_item("wire", length="5")])]
    g = B.generate(d, 14.0)
    lines = g.deck_text.splitlines()
    ld = [i for i, ln in enumerate(lines) if ln.startswith("LD")]
    ex = [i for i, ln in enumerate(lines) if ln.startswith("EX")]
    assert ld and ex and max(ld) < min(ex)
    total = sum(w.length for w in g.wires)
    assert abs(total - 10.0) < 1e-6


def test_import_deck_roundtrip_quad():
    from necnext import builder as B
    text = open(os.path.join(EX, "05 Quad loop 20m.nec")).read()
    d, _notes = B.import_deck(Deck(text))
    turns = [i for e in d["elements"] for i in e["items"] if i["type"] == "turn"]
    assert len(turns) == 3 and all(B.describe(t) == "up 90°" for t in turns)
