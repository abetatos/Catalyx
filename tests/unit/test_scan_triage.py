"""The scan is not finished until every decided fact C0 saw has a recorded outcome (v15 Phase 2).

Run: uv run pytest tests/unit/test_scan_triage.py -q
"""
from __future__ import annotations

import json

from catalyx.store import scan_triage as st

IDS = {"cat_20260916_fed_hike", "struct_cb_gold_accumulation"}


def _state(*due):
    return {"work_list": {"calendar_due": [{"key": k, "days_ago": 2} for k in due]}}


def _item(key="c0:x", **kw):
    base = {"key": key, "source": "c0", "fact": "f", "outcome": "not_an_event", "reason": "opinion"}
    base.update(kw)
    return base


def test_complete_triage_is_clean():
    t = {"items": [
        _item("fed:2026-09-16", source="calendar", outcome="registered",
              catalyst_id="cat_20260916_fed_hike", reason=None),
        _item("c0:gold"),
    ]}
    assert st.validate(t, _state("fed:2026-09-16"), IDS) == []


def test_a_due_meeting_without_a_row_is_named():
    errs = st.validate({"items": [_item()]}, _state("fed:2026-09-16"), IDS)
    assert any("fed:2026-09-16" in e for e in errs)


def test_zero_c0_rows_is_an_error():
    t = {"items": [_item("fed:x", source="calendar")]}
    assert any("source: c0" in e for e in st.validate(t, _state(), IDS))


def test_refresh_of_needs_a_catalyst_id():
    errs = st.validate({"items": [_item(outcome="refresh_of")]}, _state(), IDS)
    assert any("requires `catalyst_id`" in e for e in errs)


def test_unknown_catalyst_id_is_an_error():
    errs = st.validate({"items": [_item(outcome="registered", catalyst_id="cat_nope")]}, _state(), IDS)
    assert any("does not exist" in e for e in errs)


def test_outcome_outside_the_enum():
    errs = st.validate({"items": [_item(outcome="skipped")]}, _state(), IDS)
    assert any("not in" in e for e in errs)


def test_not_an_event_and_below_threshold_need_a_reason():
    for out in ("not_an_event", "below_threshold"):
        errs = st.validate({"items": [_item(outcome=out, reason=" ")]}, _state(), IDS)
        assert any("requires `reason`" in e for e in errs), out


def test_duplicate_key_is_an_error():
    errs = st.validate({"items": [_item("c0:a"), _item("c0:a")]}, _state(), IDS)
    assert any("duplicate" in e for e in errs)


def test_calendar_error_in_state_blocks_the_triage():
    state = {"work_list": {"calendar_due": None}, "calendar_error": "ValueError: boom"}
    errs = st.validate({"items": [_item()]}, state, IDS)
    assert any("could not evaluate the calendar" in e for e in errs)


def test_replay_of_the_missed_hike():
    """2026-09-18: the Fed hiked on 09-16. C0 used it as gold evidence and registered nothing."""
    t = {"items": [_item("c0:gold-down-6pct", outcome="refresh_of",
                         catalyst_id="struct_cb_gold_accumulation", reason=None)]}
    errs = st.validate(t, _state("ecb:2026-09-10", "fed:2026-09-16"), IDS)
    assert any("fed:2026-09-16" in e for e in errs)
    assert any("ecb:2026-09-10" in e for e in errs)


def test_disk_wrapper_missing_files_fail(tmp_path):
    assert any("pre_run" in e for e in st.check("2026-10-20", reports_dir=tmp_path))
    (tmp_path / "state_20261020.json").write_text(json.dumps(_state()))
    assert any("not found" in e for e in st.check("2026-10-20", reports_dir=tmp_path))


def test_disk_wrapper_end_to_end(tmp_path):
    ev, sd = tmp_path / "ev", tmp_path / "sd"
    ev.mkdir(); sd.mkdir()
    (ev / "cat_20260916_fed_hike.json").write_text("{}")
    (tmp_path / "state_20261020.json").write_text(json.dumps(_state("fed:2026-09-16")))
    (tmp_path / "scan_triage_20261020.json").write_text(json.dumps({"items": [
        _item("fed:2026-09-16", source="calendar", outcome="registered",
              catalyst_id="cat_20260916_fed_hike", reason=None),
        _item("c0:a")]}))
    assert st.check("2026-10-20", reports_dir=tmp_path, events_dir=ev, structural_dir=sd) == []
    (tmp_path / "scan_triage_20261020.json").write_text("{not json")
    assert any("unreadable" in e for e in st.check("2026-10-20", reports_dir=tmp_path,
                                                    events_dir=ev, structural_dir=sd))


def test_a_fired_market_move_needs_its_row():
    state = {"work_list": {"calendar_due": [], "market_moves": [
        {"key": "market:BZ=F:2026-10-16", "message": "Brent +21%"}]}}
    errs = st.validate({"items": [_item()]}, state, IDS)
    assert any("market:BZ=F:2026-10-16" in e for e in errs)
    ok = {"items": [_item(), _item("market:BZ=F:2026-10-16")]}
    assert st.validate(ok, state, IDS) == []
