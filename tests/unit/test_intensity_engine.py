"""Unit tests for the continuous intensity engine (catalyx.scorer.intensity_engine, v1.5).

Covers the two scoring paths (linear fallback + empirical percentile), the
lower_is_stronger inversion, the cliff fix that motivated the redesign, additive
trend deltas, color derivation, and the final [10, 95] clamp.

Run: uv run pytest tests/unit/test_intensity_engine.py -q
"""
from __future__ import annotations

import pytest

from catalyx.scorer import intensity_engine as ie


def _ind(**kw):
    """Build an indicator dict with sensible defaults."""
    base = {
        "id": "ind_01",
        "direction": "higher_is_stronger",
        "threshold_strong": 200,
        "threshold_weak": 80,
        "current_value": 150,
        "value_history": [],
        "unit": "x",
    }
    base.update(kw)
    return base


# ── Saturating threshold fallback (cold start, < min_history_points) ─────────

def test_fallback_midpoint_between_thresholds():
    # weak=80→50, strong=200→80. Midpoint 140 (x=0.5) → 50 + 0.5×30 = 65.
    score = ie._indicator_score(_ind(current_value=140, threshold_strong=200, threshold_weak=80))
    assert score == pytest.approx(65.0, abs=0.05)


def test_fallback_at_strong_threshold_is_80():
    # Being AT threshold_strong scores the strong anchor (80), not 100 — headroom above.
    score = ie._indicator_score(_ind(current_value=200, threshold_strong=200, threshold_weak=80))
    assert score == pytest.approx(80.0, abs=0.05)


def test_fallback_at_weak_threshold_is_50():
    score = ie._indicator_score(_ind(current_value=80, threshold_strong=200, threshold_weak=80))
    assert score == pytest.approx(50.0, abs=0.05)


def test_fallback_far_above_strong_saturates_below_100():
    # x=3.5 → 80 + 20×(1 - exp(-0.693×2.5)) ≈ 96.5. Graded, not clamped at 100.
    score = ie._indicator_score(_ind(current_value=500, threshold_strong=200, threshold_weak=80))
    assert score == pytest.approx(96.5, abs=0.1)
    assert score < 100.0


def test_cliff_fix_cofer_case():
    """The motivating bug: COFER ind_02, lower_is_stronger, strong=0.58, weak=0.62,
    value=0.582 scored 🟡=65 under the old semaphore (a cliff). The saturating fallback
    gives a continuous 78.5 — honestly 'right at the strong threshold', no discontinuity."""
    ind = _ind(
        direction="lower_is_stronger",
        threshold_strong=0.58,
        threshold_weak=0.62,
        current_value=0.582,
    )
    score = ie._indicator_score(ind)
    assert score == pytest.approx(78.5, abs=0.05)
    assert ie._color(score) == "🟡"


def test_lower_is_stronger_below_strong_scores_above_80():
    # value below the strong (low) threshold → above strong on the band → >80, graded.
    ind = _ind(direction="lower_is_stronger", threshold_strong=0.58, threshold_weak=0.62, current_value=0.55)
    score = ie._indicator_score(ind)
    assert score == pytest.approx(88.1, abs=0.2)
    assert 80.0 < score < 100.0


def test_none_value_floors():
    assert ie._indicator_score(_ind(current_value=None)) == ie._IND_CLAMP_LO


# ── Empirical percentile (>= min_history_points) ─────────────────────────────

# The percentile ARITHMETIC is pinned on a NON-trending sample of 8, i.e. outside the
# min_history blend band and below the detrend τ gate, so this tests one thing. The two
# mechanics that used to be tangled into it get their own tests below (v6 J4).
_CHOPPY = [30, 10, 50, 20, 40, 25, 45]        # τ vs time ≈ 0 → no detrend


def test_percentile_path_activates_with_enough_history():
    ind = _ind(current_value=60, value_history=[{"value": v} for v in _CHOPPY],
               threshold_strong=999, threshold_weak=0)
    # mean rank of the unique max among n=8: (7 + 0.5*1)/8 = 93.75
    assert ie._scoring_mode(ind) == "percentile"
    assert ie._indicator_score(ind) == pytest.approx(93.8, abs=0.1)


def test_percentile_inverts_for_lower_is_stronger():
    ind = _ind(direction="lower_is_stronger", current_value=60,
               value_history=[{"value": v} for v in _CHOPPY],
               threshold_strong=0, threshold_weak=999)
    assert ie._indicator_score(ind) == pytest.approx(100.0 - 93.8, abs=0.1)


def test_percentile_median_is_50ish():
    ind = _ind(current_value=32, value_history=[{"value": v} for v in _CHOPPY])
    # 4 of the 7 history values are below 32, and 32 ties with itself in the sample of 8:
    # (4 + 0.5·1)/8 = 56.25
    assert ie._indicator_score(ind) == pytest.approx(56.2, abs=0.1)


# ── v6 J4: the trending series stops pinning at 100 ──────────────────────────

def test_a_relentlessly_rising_series_no_longer_pins_the_percentile_at_the_top():
    """D5: a persistently rising driver sits at its own maximum nearly every month, so the level
    percentile stops discriminating on exactly the catalysts StructuralCatalyst exists for."""
    rising = [{"value": v} for v in [10, 20, 30, 40, 50, 60, 70]]
    ind = _ind(current_value=80, value_history=rising, threshold_strong=999, threshold_weak=0)
    d = ie._indicator_score_detail(ind)
    assert d["level_percentile"] == pytest.approx(93.8, abs=0.1)   # still at its record
    assert d["detrended_percentile"] is not None
    assert d["score"] < d["level_percentile"], "the blend must pull a pinned level down"


def test_a_record_high_still_outscores_a_slump_on_the_same_trend():
    """The blend, not a replacement: 'at an all-time high' is information, and detrending alone
    would score a record-level driver mid-range for being exactly on its own trend."""
    base = [{"value": v} for v in [10, 20, 30, 40, 50, 60, 70]]
    hot = ie._indicator_score(_ind(current_value=95, value_history=base,
                                   threshold_strong=999, threshold_weak=0))
    cold = ie._indicator_score(_ind(current_value=62, value_history=base,
                                    threshold_strong=999, threshold_weak=0))
    assert hot > cold


def test_two_records_are_separated_by_whether_they_are_accelerating():
    """The discriminating power D5 was after: under the level percentile alone both of these
    score identically, because both are at their own maximum."""
    accel = [{"value": v} for v in [10, 12, 15, 20, 30, 50]]      # curving up
    decel = [{"value": v} for v in [10, 30, 45, 55, 60, 62]]      # flattening out
    a = ie._indicator_score(_ind(current_value=80, value_history=accel,
                                 threshold_strong=999, threshold_weak=0))
    d = ie._indicator_score(_ind(current_value=63, value_history=decel,
                                 threshold_strong=999, threshold_weak=0))
    assert a != d


def test_a_choppy_series_is_not_detrended_at_all():
    ind = _ind(current_value=32, value_history=[{"value": v} for v in _CHOPPY])
    detail = ie._indicator_score_detail(ind)
    assert abs(detail["trend_tau"]) < 0.5
    assert detail["detrended_percentile"] is None
    assert detail["detrend_weight_applied"] == 0.0


def test_zeroing_the_detrend_weight_reproduces_the_pre_v6_percentile(monkeypatch):
    rising = [{"value": v} for v in [10, 20, 30, 40, 50, 60, 70]]
    ind = _ind(current_value=80, value_history=rising, threshold_strong=999, threshold_weak=0)
    monkeypatch.setattr(ie, "_DETREND_WEIGHT", 0.0)
    assert ie._indicator_score(ind) == pytest.approx(93.8, abs=0.1)


# ── v6 J4: no regime jump when the 6th observation arrives ───────────────────

def test_one_new_observation_cannot_move_the_score_by_a_regime():
    """The score used to switch outright from the saturating curve to the percentile on the
    arrival of the min_history_points'th value — tens of points for reasons unrelated to the
    world. Same family of mini-cliff v1.5 killed in the semaphore and I6 in the VIX brake."""
    vals = [30, 10, 50, 20, 40, 25, 45, 35, 15]
    scores = []
    for n in range(3, 10):
        ind = _ind(current_value=38, value_history=[{"value": v} for v in vals[:n]])
        scores.append(ie._indicator_score(ind))
    steps = [abs(b - a) for a, b in zip(scores, scores[1:])]
    assert max(steps) < 12.0, f"a single observation still moves the score by {max(steps):.1f}"


def test_the_blend_band_is_reported_so_a_score_says_how_it_was_made():
    ind = _ind(current_value=38, value_history=[{"value": v} for v in _CHOPPY[:5]])
    d = ie._indicator_score_detail(ind)
    assert d["mode"] == "blended"
    assert 0.0 < d["blend_t"] < 1.0


def test_a_cold_start_still_uses_the_saturating_curve_alone():
    ind = _ind(current_value=38, value_history=[{"value": v} for v in _CHOPPY[:3]])
    assert ie._indicator_score_detail(ind)["mode"] == "fallback"


# ── Trend deltas (additive) ──────────────────────────────────────────────────

def test_trend_delta_rising_two_consecutive():
    history = [{"score": 90}, {"score": 84}, {"score": 78}]  # most-recent-first
    delta, label = ie._trend_delta(history)
    assert delta == 5
    assert "↑↑" in label


def test_trend_delta_falling_one():
    history = [{"score": 80}, {"score": 90}]
    delta, _ = ie._trend_delta(history)
    assert delta == -3


def test_trend_delta_flat_insufficient_history():
    delta, _ = ie._trend_delta([{"score": 90}])
    assert delta == 0


# ── Color derivation ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("score,expected", [(95, "🟢"), (80, "🟢"), (79.9, "🟡"), (50, "🟡"), (49.9, "🔴")])
def test_color_thresholds(score, expected):
    assert ie._color(score) == expected


# ── compute_intensity: end-to-end with clamp ─────────────────────────────────

def test_compute_intensity_clamps_to_max():
    cat = {
        "id": "struct_test",
        "indicators": [_ind(current_value=500), _ind(id="ind_02", current_value=500)],
        "intensity": {"current_score": 80, "history": [{"score": 90}, {"score": 84}, {"score": 78}]},
    }
    r = ie.compute_intensity(cat)
    # each indicator (val=500, far above strong) → ~96.5; avg ~96.5, +5 trend → clamped to 95
    assert r["indicator_avg"] == pytest.approx(96.5, abs=0.1)
    assert r["trend_delta"] == 5
    assert r["computed_score"] == 95.0


def test_compute_intensity_no_indicators_errors():
    r = ie.compute_intensity({"id": "x", "indicators": []})
    assert "error" in r


# ── v6 J4: the migration aid must be a real comparison, not a re-implementation ──

def test_compare_legacy_reaches_the_same_code_path_with_the_features_off():
    """The legacy number has to come from THIS code with detrend_weight=0 and blend_span=0 —
    a separate reimplementation of the old formula could drift from what actually ran."""
    res = ie.compare_legacy()
    assert res["n_catalysts"] > 0
    assert all(r["legacy_score"] is not None and r["new_score"] is not None
               for r in res["catalysts"])


def test_compare_legacy_restores_the_module_constants():
    before = (ie._DETREND_WEIGHT, ie._BLEND_SPAN)
    ie.compare_legacy()
    assert (ie._DETREND_WEIGHT, ie._BLEND_SPAN) == before


def test_compare_legacy_attributes_each_change_to_a_cause():
    res = ie.compare_legacy()
    for r in res["catalysts"]:
        if abs(r["delta"]) > 0.05:
            assert r["cause"] in ("detrend", "history blend", "both")


def test_the_stored_score_is_untouched_by_the_new_formula():
    """catalyst_scorer reads the STORED intensity.current_score, so nothing downstream moves
    until someone runs --write-back. That is the migration note, as a test."""
    import yaml as _yaml

    res = ie.compare_legacy()
    changed = [r for r in res["catalysts"] if abs(r["delta"]) > 0.05]
    assert changed, "expected at least one catalyst to move, else this test proves nothing"
    for r in changed:
        path = next(p for p in ie._CATALYSTS_DIR.glob("*.yaml")
                    if (_yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("id")
                    == r["catalyst_id"])
        stored = (_yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        assert stored["intensity"]["current_score"] == r["stored_score"]
