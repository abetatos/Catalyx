"""Every 0-100 column in this system is a DIFFERENT quantity, and they used to look alike.

`catalyst 95` and `momentum 95` read as one sentence and are two: the first is an absolute level
that means the same thing in every run, the second says "beat 95% of the universe TODAY" and
averages 50 by construction. Drawn side by side in the same heatmap, with the same colour scale
and no unit, a reader has no way to tell them apart — and two of the scores are not even bounded
at 100.

These tests pin the units themselves, not the numbers. Nothing here changes a formula: the
ambiguity was always in the reading.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_APP = (_ROOT / "site" / "app.js").read_text(encoding="utf-8")


def test_the_three_units_are_declared_together():
    """One table, so a new column cannot invent a fourth unit without touching it."""
    for u in ("lvl:", "pct:", "rel:"):
        assert u in _APP, f"UNITS is missing {u} — the legend and the headers read from it"


def test_every_heatmap_column_declares_its_unit():
    """A column added without a unit renders unmarked beside three that carry one, which is
    worse than none carrying one: the reader assumes the unmarked share the neighbour's."""
    block = re.search(r"const SEC_COLS = \[(.*?)\n\];", _APP, re.S)
    assert block, "SEC_COLS not found"
    cols = [ln for ln in block.group(1).splitlines() if "{ k:" in ln]
    assert len(cols) >= 4
    for ln in cols:
        assert "unit:" in ln, f"heatmap column without a unit: {ln.strip()[:80]}"


def test_momentum_is_labelled_a_percentile_and_catalyst_a_level():
    """The specific confusion this exists to end. If these two ever carry the same unit, either
    a formula changed or the label is lying — both are worth failing a build over."""
    block = re.search(r"const SEC_COLS = \[(.*?)\n\];", _APP, re.S).group(1)
    mom = next(ln for ln in block.splitlines() if "'momentum'" in ln)
    cat = next(ln for ln in block.splitlines() if "catalyst_alignment" in ln)
    assert "unit: 'pct'" in mom, "momentum is a cross-sectional percentile"
    assert "unit: 'lvl'" in cat, "catalyst_alignment is an absolute level"


def test_the_composite_is_marked_relative_not_absolute():
    """`50 + 15z` is a LEVEL computed from a relative position. v6/H1 introduced `composite_z`
    for exactly this reason and pre-1.4 composites are not comparable to today's."""
    block = re.search(r"const SEC_COLS = \[(.*?)\n\];", _APP, re.S).group(1)
    comp = next(ln for ln in block.splitlines() if "k: 'composite'" in ln)
    assert "unit: 'rel'" in comp


def test_crowding_carries_its_unit_per_row_not_in_the_header():
    """Since v8 crowding is a measured comomentum PERCENTILE where one exists and a mapped
    narrative label where it does not — row by row. A single header unit would be a claim about
    rows that do not carry it."""
    assert "function crowdLabel(v, src)" in _APP
    assert "crowding_source" in _APP, "the row's source must reach the front to be shown"
    header = next(ln for ln in _APP.splitlines() if 'data-sort="crowding_risk"' in ln)
    assert "MIXED units" in header


def test_the_legend_is_rendered_with_the_table():
    assert "unitsLegend()" in _APP
    assert _APP.count("unitsLegend()") >= 2, "defined and actually called"


def test_the_unbounded_scores_name_their_unit_instead_of_pretending_to_be_0_100():
    """`opportunity_score` = |drawdown pp| × CA/100 and `diversifier_score` = composite × (1−ρ).
    Neither is capped at 100 and neither was ever a 0-100 score. Clipping them to match the
    columns beside them would replace a real unit with an invented one — the fix is to say what
    they are."""
    from catalyx.scorer import dislocation

    src = Path(dislocation.__file__).read_text(encoding="utf-8")
    assert 'r2["opportunity_unit"] = "pp_drawdown_x_ca"' in src
    assert 'r2["diversifier_unit"] = "composite_rel_x_1_minus_rho"' in src
    # and no silent clamp crept in behind the label
    assert "min(100" not in src.split("opportunity_score")[1][:200]
