"""Regime discrimination — time-independent persistence + Claude-judged escalation.

Locks in the design decisions from exp_2026-06-05:
  - Python labels only OBJECTIVE states; it NEVER escalates to `breaking` from an event count.
  - persistence is read from event timestamps over a calendar window (run-frequency independent).
  - clustered consecutive-day developments are flagged as one shock (no review); only dispersed
    multiples advise a review — and even then the escalation call is Claude's, not the code's.
See docs/DESIGN_catalyst_regime_discrimination.md.
"""
from datetime import date, timedelta

from catalyx.thesis import structural_monitor as sm


# ── classify_structural: Python only labels objective states ─────────────────────

def test_no_live_contradict_is_intact():
    assert sm.classify_structural(degrading=False, n_live_contradicts=0) == "intact"


def test_one_live_contradict_is_contested():
    assert sm.classify_structural(degrading=False, n_live_contradicts=1) == "contested"


def test_event_count_never_auto_escalates_to_breaking():
    # The whole point: two (or more) events do NOT mechanically mean breaking.
    assert sm.classify_structural(degrading=False, n_live_contradicts=2) == "contested"
    assert sm.classify_structural(degrading=False, n_live_contradicts=5) == "contested"


def test_only_fundamental_degradation_is_breaking():
    assert sm.classify_structural(degrading=True, n_live_contradicts=0) == "breaking"


# ── within_window: time-independent (reads event timestamps) ─────────────────────

def test_within_window_recent_true_old_false():
    today = date.today().isoformat()
    old = (date.today() - timedelta(days=sm.WINDOW_DAYS + 15)).isoformat()
    assert sm.within_window(today) is True
    assert sm.within_window(old) is False


def test_within_window_handles_datetime_and_bad_input():
    assert sm.within_window(date.today().isoformat() + "T09:00:00Z") is True
    assert sm.within_window(None) is False
    assert sm.within_window("not-a-date") is False


# ── persistence_evidence: context dossier, advisory only ─────────────────────────

def _ev(eid, d, s=70):
    return {"event_id": eid, "event_date": d, "strength_decayed": s}


def test_single_development_no_review():
    p = sm.persistence_evidence([_ev("a", "2026-06-05")])
    assert p["distinct_developments"] == 1
    assert p["review_recommended"] is False


def test_clustered_consecutive_days_is_one_shock_no_review():
    # "dos días seguidos no confirma nada"
    p = sm.persistence_evidence([_ev("a", "2026-06-04"), _ev("b", "2026-06-05")])
    assert p["clustered_one_shock"] is True
    assert p["review_recommended"] is False


def test_dispersed_developments_recommend_review():
    p = sm.persistence_evidence([_ev("a", "2026-05-12"), _ev("b", "2026-06-05")])
    assert p["clustered_one_shock"] is False
    assert p["span_days"] >= sm.DISPERSION_MIN_DAYS
    assert p["review_recommended"] is True


def test_distinct_dedup_by_id():
    # same development logged twice must not look like two
    p = sm.persistence_evidence([_ev("a", "2026-06-04"), _ev("a", "2026-06-05")])
    assert p["distinct_developments"] == 1
