"""The central-bank calendar: which known-date decisions has no scan triaged yet?

Born from the Fed's 2026-09-16 hike, which reached the registry on 2026-10-17 only because the
user asked. `test_replay_...` is the proof that this mechanism would have flagged it two days
after the decision.

Run: uv run pytest tests/unit/test_macro_calendar.py -q
"""
from __future__ import annotations

import json
from datetime import date

import pytest
import yaml

from catalyx.store import macro_calendar as mc


def _cal(meetings, start="2026-09-01"):
    return {"tracking_start": start,
            "meetings": [{"bank": b, "date": d, "kind": "rate_decision"} for b, d in meetings]}


def test_the_real_calendar_is_valid_and_ordered():
    cal = mc.load()
    banks = {m["bank"] for m in cal["meetings"]}
    assert banks == set(mc.BANKS)
    dates = [m["date"] for m in cal["meetings"]]
    assert dates == sorted(dates)
    assert all(d >= "2026-06-01" for d in dates)


def test_replay_of_the_missed_hike_flags_it_two_days_after_the_decision():
    cal = _cal([("ecb", "2026-09-10"), ("fed", "2026-09-16"), ("fed", "2026-10-28")])
    got = mc.due(date(2026, 9, 18), cal, resolved=set())
    assert [r["key"] for r in got] == ["ecb:2026-09-10", "fed:2026-09-16"]
    assert got[1]["days_ago"] == 2


def test_a_triage_entry_resolves_the_meeting(tmp_path):
    (tmp_path / "scan_triage_20260918.json").write_text(
        json.dumps({"items": [{"key": "fed:2026-09-16"}]}), encoding="utf-8")
    cal = _cal([("ecb", "2026-09-10"), ("fed", "2026-09-16")])
    got = mc.due(date(2026, 9, 18), cal, mc.resolved_keys(tmp_path))
    assert [r["key"] for r in got] == ["ecb:2026-09-10"]


def test_tracking_start_and_future_meetings_are_never_due():
    cal = _cal([("fed", "2026-06-17"), ("fed", "2026-10-28")])
    assert mc.due(date(2026, 10, 17), cal, set()) == []


def test_upcoming_lists_the_next_two_weeks_only():
    cal = _cal([("fed", "2026-10-28"), ("boj", "2026-12-18")])
    assert [r["key"] for r in mc.upcoming(date(2026, 10, 17), cal)] == ["fed:2026-10-28"]


def test_an_unreadable_triage_file_raises_instead_of_reopening_meetings(tmp_path):
    (tmp_path / "scan_triage_20260918.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        mc.resolved_keys(tmp_path)


@pytest.mark.parametrize("meetings,why", [
    ([{"bank": "snb", "date": "2026-09-18", "kind": "rate_decision"}], "unknown bank"),
    ([{"bank": "fed", "date": "2026-13-45", "kind": "rate_decision"}], "bad date"),
    ([{"bank": "fed", "date": "2026-09-16", "kind": "rate_decision"}] * 2, "duplicate"),
    ([], "empty"),
])
def test_validation_rejects_a_malformed_calendar(tmp_path, meetings, why):
    fp = tmp_path / "cal.yaml"
    fp.write_text(yaml.safe_dump({"tracking_start": "2026-09-01", "meetings": meetings}),
                  encoding="utf-8")
    with pytest.raises(ValueError):
        mc.load(fp)
