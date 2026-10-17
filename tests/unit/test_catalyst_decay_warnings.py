"""v15 Phase 3 — the audit names a reinforced-but-decayed event and a same-driver stack.

Both are INFORMATIONAL: they never change an event's verdict nor the `--strict` exit code.
Dates are relative to today so the fixtures do not rot.

Run: uv run pytest tests/unit/test_catalyst_decay_warnings.py -q
"""
from __future__ import annotations

from datetime import date, timedelta

from catalyx.scorer import catalyst_audit as audit


def _ev(eid, age_days, halflife=30, log=None, related=None, sectors=("gold_physical",),
        status="active"):
    d = (date.today() - timedelta(days=age_days)).isoformat()
    return {"id": eid, "status": status, "catalyst_type": "monetary_policy", "strength_score": 70,
            "event_date": f"{d}T00:00:00Z", "decay_halflife_days": halflife,
            "review_log": log or [], "related_catalyst_ids": related or [],
            "affected_sectors": list(sectors)}


def _log(*pairs):
    return [{"date": d, "verdict": v} for d, v in pairs]


def test_old_event_whose_latest_review_is_strengthening_is_flagged():
    e = _ev("cat_old", 120, log=_log(("2026-10-17", "strengthening")))   # 0.5^4 = 6%
    out = audit.decay_warnings([e])["reinforced_but_decayed"]
    assert [r["event_id"] for r in out] == ["cat_old"]
    assert out[0]["retained_pct"] < 10


def test_same_event_with_an_intact_review_is_not_flagged():
    e = _ev("cat_old", 120, log=_log(("2026-10-17", "intact")))
    assert audit.decay_warnings([e])["reinforced_but_decayed"] == []


def test_latest_review_is_by_date_not_by_list_order():
    # newest-first on disk today, but the code must not rely on it
    newest_last = _log(("2026-09-01", "strengthening"), ("2026-10-17", "intact"))
    newest_first = list(reversed(newest_last))
    for log in (newest_last, newest_first):
        e = _ev("cat_old", 120, log=log)
        assert audit.decay_warnings([e])["reinforced_but_decayed"] == []


def test_a_fresh_strengthening_event_is_not_flagged():
    e = _ev("cat_new", 5, log=_log(("2026-10-17", "strengthening")))
    assert audit.decay_warnings([e])["reinforced_but_decayed"] == []


def test_inactive_events_are_ignored():
    e = _ev("cat_old", 120, log=_log(("2026-10-17", "strengthening")), status="archived")
    assert audit.decay_warnings([e])["reinforced_but_decayed"] == []


def test_two_linked_fresh_events_on_a_shared_sector_are_a_stack():
    a = _ev("cat_a", 5, related=["cat_b"])
    b = _ev("cat_b", 10)
    out = audit.decay_warnings([a, b])["same_driver_stack"]
    assert len(out) == 1 and out[0]["events"] == ["cat_a", "cat_b"]
    assert out[0]["shared_sectors"] == ["gold_physical"]


def test_link_in_either_direction_counts_once():
    a = _ev("cat_a", 5, related=["cat_b"])
    b = _ev("cat_b", 10, related=["cat_a"])
    assert len(audit.decay_warnings([a, b])["same_driver_stack"]) == 1


def test_a_decayed_partner_is_not_a_stack():
    a = _ev("cat_a", 5, related=["cat_b"])
    b = _ev("cat_b", 120)
    assert audit.decay_warnings([a, b])["same_driver_stack"] == []


def test_no_shared_sector_is_not_a_stack():
    a = _ev("cat_a", 5, related=["cat_b"], sectors=("gold_physical",))
    b = _ev("cat_b", 10, sectors=("oil_majors_integrated",))
    assert audit.decay_warnings([a, b])["same_driver_stack"] == []


def test_warnings_never_change_verdicts_or_strict_exit(monkeypatch):
    old = _ev("cat_old", 120, log=_log(("2026-10-17", "strengthening")))
    monkeypatch.setattr(audit, "_load_all_event_catalysts", lambda: [old])
    with_w = audit.audit()
    assert with_w["reinforced_but_decayed"]
    plain = [{k: v for k, v in r.items()} for r in with_w["events"]]
    monkeypatch.setattr(audit, "decay_warnings", lambda ev: {"reinforced_but_decayed": [],
                                                              "same_driver_stack": []})
    without = audit.audit()
    assert plain == without["events"]
    assert with_w["n_fail"] == without["n_fail"]


def test_render_prints_the_sections():
    res = {"n_fail": 0, "n_events": 0, "strength_calibration": "x", "halflife_source": "stored",
           "events": [], "n_halflife_drift": 0, "n_halflife_drift_longer": 0,
           "reinforced_but_decayed": [{"event_id": "cat_old", "retained_pct": 5.9,
                                       "last_review": "2026-10-17", "note": ""}],
           "same_driver_stack": [{"events": ["cat_a", "cat_b"], "shared_sectors": ["s"],
                                  "retained_pct": {"cat_a": 90.0, "cat_b": 80.0}}]}
    txt = audit.render(res)
    assert "REINFORCED BUT DECAYED" in txt and "cat_old" in txt
    assert "SAME-DRIVER STACK" in txt and "cat_a" in txt


# ── v15 Phase 4 — event_not_linked ───────────────────────────────────────────

def _linked(events, studies, structural=("struct_x",), investable=None):
    return audit.event_not_linked(events, studies.get, set(structural), investable or {})


def _ev4(eid="cat_e", sectors=("a", "b"), related=None, relation=None):
    e = {"id": eid, "status": "active", "affected_sectors": list(sectors),
         "related_catalyst_ids": related or []}
    if relation:
        e["relation_to_structural"] = relation
    return e


def test_only_the_sector_whose_study_omits_the_event_is_listed():
    studies = {"a": {"active_catalyst_ids": ["cat_e"]}, "b": {"active_catalyst_ids": []}}
    rows = _linked([_ev4()], studies)
    assert [(r["event_id"], r["sector_id"]) for r in rows] == [("cat_e", "b")]


def test_declined_in_writing_is_not_listed():
    studies = {"a": {"active_catalyst_ids": ["cat_e"]},
               "b": {"active_catalyst_ids": [],
                     "declined_catalyst_ids": [{"catalyst_id": "cat_e", "date": "2026-10-17",
                                                "reason": "headwind, not a demand driver"}]}}
    assert _linked([_ev4()], studies) == []


def test_linked_through_a_listed_structural_with_independent_relation():
    studies = {s: {"active_catalyst_ids": ["struct_x"]} for s in ("a", "b")}
    e = _ev4(related=["struct_x"], relation="independent")
    assert _linked([e], studies) == []


def test_a_structural_link_without_a_scoring_relation_does_not_count():
    studies = {s: {"active_catalyst_ids": ["struct_x"]} for s in ("a", "b")}
    e = _ev4(related=["struct_x"], relation="supports")
    assert len(_linked([e], studies)) == 2


def test_a_structural_the_study_does_not_list_does_not_link():
    studies = {s: {"active_catalyst_ids": []} for s in ("a", "b")}
    e = _ev4(related=["struct_x"], relation="confirms")
    assert len(_linked([e], studies)) == 2


def test_sector_without_study_is_named_no_study():
    rows = _linked([_ev4(sectors=("a",))], {})
    assert rows[0]["reason"] == "no_study"


def test_investable_flag_is_carried_and_inactive_events_ignored():
    rows = _linked([_ev4(sectors=("a",)), {**_ev4("cat_old"), "status": "archived"}],
                   {"a": {"active_catalyst_ids": []}}, investable={"a": False})
    assert len(rows) == 1 and rows[0]["investable"] is False


def test_linking_relations_match_the_scorer():
    import inspect
    from catalyx.scorer import catalyst_scorer as cs
    src = inspect.getsource(cs)
    for r in audit.LINKING_RELATIONS:
        assert f'"{r}"' in src
    assert '("confirms", "contradicts", "independent")' in src
