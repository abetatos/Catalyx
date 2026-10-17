"""Unit tests for the v11 realism layer on the catalyst score.

Six changes, all aimed at the same thing: the catalyst score is a deterministic formula, but
two of its inputs were still free cardinals an LLM typed after reading the news, and nothing in
the pipeline ever contrasted any of it against the tape.

  P1 disconfirming search   — prose (catalyx-scan.md), nothing to unit-test here
  P2 rubric + anchors       — `catalyst_audit.rubric_total` / `.anchor_bracket`
  P3 halflife by type       — `catalyst_scorer._halflife_for`
  P4 rank-calibrated strength — `catalyst_scorer.calibrate_strength`
  P5 CA at the ceiling      — `catalyst_scorer._logit_score`
  P6 event tape check       — `event_market_check.abnormal_z` / `.aggregate`

Run: uv run pytest tests/unit/test_catalyst_score_realism.py -q
"""
from __future__ import annotations

import math

import pytest

from catalyx.scorer import catalyst_audit as audit
from catalyx.scorer import catalyst_scorer as cs
from catalyx.scorer import event_market_check as tape


# ── P4 — rank calibration of strength_score ──────────────────────────────────

POP = [55, 58, 60, 62, 68, 70, 72, 72, 72, 75, 75, 78, 80, 80, 82, 82, 91, 94, 76]


def test_calibration_preserves_the_population_range():
    """Range-preserving is the whole safety property: the transform re-spaces, it does not
    re-scale, so nothing downstream (decay, the ±10/15 boost) changes units."""
    lo, hi = min(POP), max(POP)
    out = [cs.calibrate_strength(v, POP)[0] for v in POP]
    assert min(out) >= lo - 1e-9
    assert max(out) <= hi + 1e-9


def test_calibration_is_monotone_and_never_reorders():
    vals = sorted(set(POP))
    out = [cs.calibrate_strength(v, POP)[0] for v in vals]
    assert out == sorted(out)


def test_equal_inputs_get_equal_outputs():
    """The three events an author wrote as exactly '72' must resolve to one shared level —
    a tie in the input is a tie, not a coincidence to be broken by rank order."""
    a, _ = cs.calibrate_strength(72, POP)
    b, _ = cs.calibrate_strength(72, POP)
    assert a == b


def test_calibration_skipped_below_min_population():
    """A percentile over a handful of observations is noise, and the pre-v11 raw score is the
    honest fallback — same rule as every other 'not measured is not a finding' path."""
    val, applied = cs.calibrate_strength(72, [60, 70, 80])
    assert (val, applied) == (72.0, False)


def test_calibration_handles_degenerate_population():
    val, applied = cs.calibrate_strength(70, [70] * 15)
    assert (val, applied) == (70.0, False)


def test_calibration_moves_the_live_scores_only_mildly():
    """The advertised magnitude. If this starts failing, the transform stopped being a
    re-spacing and became a re-scaling, and the CHANGELOG claim is stale."""
    deltas = [abs(cs.calibrate_strength(v, POP)[0] - v) for v in POP]
    assert max(deltas) < 10.0


# ── P3 — the halflife comes from the type, not from the author ───────────────

def test_halflife_stored_wins_under_the_shipping_default(monkeypatch):
    monkeypatch.setattr(cs, "_HALFLIFE_SOURCE", "stored")
    monkeypatch.setattr(cs, "_TYPE_HALFLIVES", {"commodity_supply": 21.0})
    hl, drift = cs._halflife_for({"catalyst_type": "commodity_supply", "decay_halflife_days": 90})
    assert hl == 90.0
    assert drift is True          # reported either way — a disagreement nobody prints is how
                                  # prose lost to a field in the first place


def test_halflife_taxonomy_wins_when_switched(monkeypatch):
    monkeypatch.setattr(cs, "_HALFLIFE_SOURCE", "taxonomy")
    monkeypatch.setattr(cs, "_TYPE_HALFLIVES", {"commodity_supply": 21.0})
    hl, drift = cs._halflife_for({"catalyst_type": "commodity_supply", "decay_halflife_days": 90})
    assert (hl, drift) == (21.0, True)


def test_halflife_falls_back_to_the_table_then_the_global_default(monkeypatch):
    monkeypatch.setattr(cs, "_HALFLIFE_SOURCE", "stored")
    monkeypatch.setattr(cs, "_TYPE_HALFLIVES", {"regulatory": 45.0})
    assert cs._halflife_for({"catalyst_type": "regulatory"})[0] == 45.0
    assert cs._halflife_for({"catalyst_type": "unmapped"})[0] == cs._DEFAULT_HALFLIFE


def test_no_drift_reported_when_stored_matches_the_table(monkeypatch):
    monkeypatch.setattr(cs, "_TYPE_HALFLIVES", {"geopolitical": 60.0})
    assert cs._halflife_for({"catalyst_type": "geopolitical",
                             "decay_halflife_days": 60})[1] is False


def test_structural_types_are_absent_not_zero():
    """`catalyst_taxonomy.yaml` stores null for the structural types. Mapping that to 0 would
    mean 'never decays' in `_decayed_strength` — the exact opposite of a missing value."""
    from catalyx.config import weights

    table = weights.catalyst_type_halflives()
    assert "technology_adoption" not in table
    assert table["commodity_supply"] == 21.0


# ── P5 — CA on a scale that still discriminates at the ceiling ───────────────

def test_logit_endpoints_stay_finite_and_ordered():
    """`infrastructure_core` scores a measured 0.0 today. An infinite value would drop the row
    out of the column entirely, which is not what a measured zero means."""
    assert math.isfinite(cs._logit_score(0.0))
    assert math.isfinite(cs._logit_score(100.0))
    assert cs._logit_score(0.0) < cs._logit_score(50.0) < cs._logit_score(100.0)


def test_logit_is_strictly_monotone_in_the_saturated_band():
    band = [95.0, 95.5, 96.0, 96.2, 96.3, 96.7]
    out = [cs._logit_score(v) for v in band]
    assert out == sorted(out)
    assert len(set(out)) == len(band)     # no collapsing to a shared value


def test_logit_buys_resolution_where_the_level_scale_has_none():
    """The claim in the code comment and the CHANGELOG: the nine sectors the level scale packs
    into 1.7 points get materially more separation, measured against each scale's own σ."""
    ca = [96.7, 96.3, 96.3, 96.2, 96.0, 95.5, 95.5, 95.0, 95.0, 92.6, 92.4, 92.4, 92.4,
          87.4, 85.1, 81.7, 77.7, 76.0, 69.6, 69.6, 68.5, 63.1, 36.0, 13.7, 13.7, 0.0]
    top = ca[:9]

    def sd(xs):
        m = sum(xs) / len(xs)
        return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5

    level = (max(top) - min(top)) / sd(ca)
    lg = [cs._logit_score(v) for v in ca]
    logit = (max(lg[:9]) - min(lg[:9])) / sd(lg)
    assert logit > 3.0 * level


# ── P2 — the rubric and the anchor bracket ──────────────────────────────────

RUBRIC = [{"id": "str_01", "weight": 25}, {"id": "str_02", "weight": 25},
          {"id": "str_03", "weight": 20}, {"id": "str_04", "weight": 15},
          {"id": "str_05", "weight": 15}]


def test_rubric_total_sums_only_the_true_answers():
    scores = {"str_01": True, "str_02": True, "str_03": False, "str_04": True}
    assert audit.rubric_total(scores, RUBRIC) == 65.0


def test_rubric_total_is_none_when_nothing_was_answered():
    assert audit.rubric_total({}, RUBRIC) is None
    assert audit.rubric_total(None, RUBRIC) is None


def test_rubric_weights_sum_to_100():
    from catalyx.config import weights

    assert sum(q["weight"] for q in weights.strength_score_rubric()) == 100


def test_anchor_bracket_accepts_a_score_inside_it():
    ok, _ = audit.anchor_bracket(75.0, weaker=82.0, stronger=70.0)
    assert ok


@pytest.mark.parametrize("score,weaker,stronger", [(65.0, 82.0, 70.0), (90.0, 82.0, 70.0)])
def test_anchor_bracket_rejects_a_score_outside_it(score, weaker, stronger):
    ok, why = audit.anchor_bracket(score, weaker, stronger)
    assert not ok and why


def test_anchor_bracket_names_an_inverted_pair():
    """stronger_than above weaker_than is an empty bracket — the easy mistake, so the failure
    text has to say which way round they go rather than leaving it to the reader."""
    ok, why = audit.anchor_bracket(75.0, weaker=70.0, stronger=82.0)
    assert not ok and "invert" in why


def test_anchor_bracket_requires_at_least_one_anchor():
    ok, why = audit.anchor_bracket(75.0, None, None)
    assert not ok and why == "no anchors"


def test_audit_names_a_bare_cardinal_and_a_stretched_halflife(monkeypatch):
    """The finding v11 was built to expose, pinned on a FIXTURE: an event with no rubric and no
    bracket is named as a bare cardinal, and a halflife stretched past its type's table is
    counted as drift toward persistence.

    It used to assert this about the LIVE registry ("not one event carries a rubric"), which is
    exactly the state the v11 workflow is designed to remove — scan by scan the rubric gets
    filled, and on 2026-10-17 the test failed for being succeeded. A lint's test should pin the
    lint, not the data it happens to be linting this month."""
    from catalyx.config import weights
    ctype, table = next(iter(weights.catalyst_type_halflives().items()))
    rubric_ids = [q["id"] for q in weights.strength_score_rubric()]
    base = {"catalyst_type": ctype, "status": "active", "strength_score": 60,
            "event_date": "2026-09-01T00:00:00Z"}
    bare = {**base, "id": "cat_20260901_bare", "decay_halflife_days": float(table) * 4}
    scored = {**base, "id": "cat_20260901_scored", "decay_halflife_days": float(table),
              "strength_rubric_scores": {i: True for i in rubric_ids[:3]},
              "strength_anchors": {"stronger_than": "cat_20260901_bare"}}
    monkeypatch.setattr(audit, "_load_all_event_catalysts", lambda: [bare, scored])

    res = audit.audit(active_only=True)
    rows = {r["event_id"]: r for r in res["events"]}
    assert res["n_events"] == 2
    assert res["n_no_rubric"] == 1
    assert any("bare cardinal" in i for i in rows["cat_20260901_bare"]["issues"])
    assert rows["cat_20260901_scored"]["rubric_total"] is not None
    assert res["n_halflife_drift"] == 1
    assert res["n_halflife_drift_longer"] == 1


# ── P6 — did the tape agree with the event? ─────────────────────────────────

def _flat(n, start=100.0, step=0.0):
    return [start + step * i for i in range(n)]


def test_abnormal_z_is_zero_when_sector_tracks_the_benchmark():
    sector = [100.0 * (1.004 ** i) for i in range(200)]
    bench = [50.0 * (1.004 ** i) for i in range(200)]
    z = tape.abnormal_z(sector, bench, idx=150, window=5, min_vol_days=30)
    assert z is not None and abs(z) < 1e-6


def test_abnormal_z_is_positive_when_the_sector_outruns_the_benchmark():
    sector = [100.0 * (1.002 ** i) for i in range(200)]
    sector = sector[:151] + [v * 1.05 for v in sector[151:]]
    bench = [50.0 * (1.002 ** i) for i in range(200)]
    z = tape.abnormal_z(sector, bench, idx=150, window=5, min_vol_days=30)
    assert z is not None and z > 0


def test_abnormal_z_needs_the_window_to_fit_inside_the_series():
    assert tape.abnormal_z(_flat(160), _flat(160), idx=158, window=5) is None


def test_abnormal_z_needs_a_pre_event_sample():
    """The denominator is measured strictly BEFORE the event. A vol window that contained the
    move it scales would be a fixed point on itself."""
    sector = [100.0 * (1.01 ** i) for i in range(60)]
    assert tape.abnormal_z(sector, sector, idx=10, window=5, min_vol_days=30) is None


def test_aggregate_returns_none_not_zero_when_nothing_was_measured():
    """A zero here would read as 'the market was indifferent', which is a finding. 'We could
    not price the window' is not."""
    out = tape.aggregate([{"signed_z": None, "strength": 80}])
    assert out["event_tape_z"] is None
    assert out == {"event_tape_z": None, "event_tape_n": 0, "event_tape_unmeasured": 1}


def test_aggregate_weights_by_strength():
    rows = [{"signed_z": 2.0, "strength": 90}, {"signed_z": -2.0, "strength": 10}]
    out = tape.aggregate(rows)
    assert out["event_tape_n"] == 2
    assert out["event_tape_z"] == pytest.approx((90 * 2.0 - 10 * 2.0) / 100.0, abs=1e-6)


def test_aggregate_falls_back_to_equal_weights_when_strengths_are_absent():
    out = tape.aggregate([{"signed_z": 1.0, "strength": 0}, {"signed_z": 3.0, "strength": 0}])
    assert out["event_tape_z"] == pytest.approx(2.0)


def test_contradicts_is_confirmed_by_a_fall():
    """The sign convention that makes the column mean one thing: a `contradicts` event whose
    sector FELL is a confirmed event, not a refuted one."""
    detail = {"breakdown": [{"events": [
        {"event_id": "cat_x", "relation": "contradicts", "event_date": "2026-01-05",
         "strength_original": 80, "strength_calibrated": 80.0},
    ]}]}
    evs = tape._events_for_sector(detail)
    assert evs[0]["relation"] == "contradicts"
    # aggregate() consumes an already-signed z; the sign flip happens in compute(), so this
    # asserts the contract the two halves share rather than re-deriving it.
    assert tape.aggregate([{"signed_z": +1.5, "strength": 80}])["event_tape_z"] == 1.5


def test_events_for_sector_dedupes_an_event_carried_by_two_structurals():
    detail = {"breakdown": [
        {"events": [{"event_id": "cat_x", "relation": "confirms", "event_date": "2026-01-05",
                     "strength_original": 80, "strength_calibrated": 80.0}]},
        {"events": [{"event_id": "cat_x", "relation": "confirms", "event_date": "2026-01-05",
                     "strength_original": 80, "strength_calibrated": 80.0}]},
    ], "direct_events": []}
    assert len(tape._events_for_sector(detail)) == 1


def test_events_for_sector_skips_a_direct_event_counted_via_its_structural():
    """A linked event emitted without strength fields is the 'counted via linked structural'
    marker — including it would double-count the same event in the tape aggregate."""
    detail = {"breakdown": [], "direct_events": [
        {"event_id": "cat_y", "relation": "confirms", "modified_score": None,
         "note": "counted via linked structural (not double-counted)"},
    ]}
    assert tape._events_for_sector(detail) == []


# ── The columns reach the lake ──────────────────────────────────────────────

def test_candidate_columns_are_registered_for_calibration():
    """A column nothing measures is a column that can never earn a weight — the v7 discipline."""
    from catalyx.scorer.calibration import CANDIDATE_DIMENSIONS

    assert CANDIDATE_DIMENSIONS["ca_logit"] is False
    assert CANDIDATE_DIMENSIONS["event_tape_z"] is False


def test_ca_logit_is_published_on_a_real_sector_result():
    res = cs.compute_catalyst_alignment("copper_miners")
    assert res.get("ca_logit") is not None
    assert res["ca_logit"] == cs._logit_score(res["catalyst_alignment"])


def test_ca_logit_survives_a_measured_zero_but_not_an_imputed_one():
    """`infrastructure_core` has a study whose `active_catalyst_ids` is empty — a zero that was
    MEASURED, which v6 H2 keeps and scores. Deriving the column from `catalyst_detail` dropped
    exactly that row (both no-catalyst paths return early), so the column lost the one sector
    whose zero is a finding. An IMPUTED CA is the other case and stays None."""
    from catalyx.scorer.sector_scorer import score_sector

    measured = score_sector("infrastructure_core")
    assert measured["score_breakdown"]["catalyst_alignment"] == 0.0
    assert measured["ca_imputed"] is False
    assert measured["ca_logit"] == pytest.approx(cs._logit_score(0.0))
    assert math.isfinite(measured["ca_logit"])
