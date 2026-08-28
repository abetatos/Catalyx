"""Unit tests for the catalyst review stamp (catalyx.store.catalyst_review).

The stamp is the write path that makes `exit_watcher`'s freshness gate satisfiable, so the
tests pin the two things that gate depends on: the field it reads (`status_last_reviewed`) and
the evidence trail behind the verdict. All fixtures are tmp files — no repo YAML is touched.
"""
from __future__ import annotations

import json
from datetime import date

import pytest
import yaml

from catalyx.store import catalyst_review as cr


@pytest.fixture
def dirs(tmp_path):
    sdir, edir = tmp_path / "structural", tmp_path / "events"
    sdir.mkdir()
    edir.mkdir()
    (sdir / "cb_gold_accumulation.yaml").write_text(
        "# a hand-authored file with comments worth preserving\n"
        'id: struct_cb_gold_accumulation\n'
        'title: "Central banks accumulating gold"\n'
        'status: active\n'
        'status_last_reviewed: "2026-01-01"\n'
        "intensity:\n  current_score: 84\n",
        encoding="utf-8")
    (edir / "cat_20260603_nato.json").write_text(
        json.dumps({"id": "cat_20260603_nato", "status": "active",
                    "status_last_reviewed": "2026-01-01"}), encoding="utf-8")
    return sdir, edir


# ── Locating files ───────────────────────────────────────────────────────────

def test_find_file_strips_the_struct_prefix(dirs):
    sdir, edir = dirs
    # struct_cb_gold_accumulation → cb_gold_accumulation.yaml (the repo's naming convention)
    assert cr.find_file("struct_cb_gold_accumulation", sdir, edir).name == "cb_gold_accumulation.yaml"


def test_find_file_locates_events_by_id_field_not_only_filename(dirs):
    sdir, edir = dirs
    (edir / "oddly_named.json").write_text(
        json.dumps({"id": "cat_20260701_elsewhere"}), encoding="utf-8")
    assert cr.find_file("cat_20260701_elsewhere", sdir, edir).name == "oddly_named.json"


def test_find_file_returns_none_for_an_unknown_id(dirs):
    sdir, edir = dirs
    assert cr.find_file("struct_does_not_exist", sdir, edir) is None


# ── Entry construction ───────────────────────────────────────────────────────

def test_non_intact_verdict_requires_evidence():
    # An unsourced "it feels weaker" is exactly the LLM drift the scoring rules exist to stop.
    with pytest.raises(ValueError, match="requires --evidence"):
        cr.build_entry("weakening", None, None)
    with pytest.raises(ValueError, match="requires --evidence"):
        cr.build_entry("breaking", "   ", None)
    assert cr.build_entry("weakening", "LME stocks +40% MoM", "lme.com")["verdict"] == "weakening"


def test_intact_needs_no_evidence_and_unknown_verdicts_are_rejected():
    assert cr.build_entry("intact", None, None, as_of="2026-08-27")["date"] == "2026-08-27"
    with pytest.raises(ValueError, match="verdict must be"):
        cr.build_entry("looks_fine", "x", None)


# ── Freshness tiers (mirror exit_watcher) ────────────────────────────────────

@pytest.mark.parametrize("age,expected", [
    (0, "fresh"), (30, "fresh"), (31, "stale"), (45, "stale"), (46, "very_stale"), (None, "unknown"),
])
def test_freshness_tiers(age, expected):
    assert cr.freshness_status(age) == expected


def test_days_since_handles_a_malformed_stamp():
    assert cr.days_since("not-a-date") is None
    assert cr.days_since(None) is None
    assert cr.days_since("2026-08-20", as_of=date(2026, 8, 27)) == 7


# ── Stamping ─────────────────────────────────────────────────────────────────

def test_stamp_yaml_updates_the_field_the_gate_reads_and_keeps_comments(dirs):
    sdir, edir = dirs
    out = cr.stamp("struct_cb_gold_accumulation", "intact", "WGC Q2: 850t on track",
                   source="gold.org", as_of="2026-08-27", structural_dir=sdir, event_dir=edir)
    text = (sdir / "cb_gold_accumulation.yaml").read_text(encoding="utf-8")
    doc = yaml.safe_load(text)

    assert doc["status_last_reviewed"] == "2026-08-27"      # ← the field exit_watcher reads
    assert doc["review_log"][0]["verdict"] == "intact"
    assert doc["review_log"][0]["evidence"] == "WGC Q2: 850t on track"
    assert "# a hand-authored file with comments worth preserving" in text  # ruamel round-trip
    assert doc["intensity"]["current_score"] == 84          # untouched: a stamp is not a rescore
    assert out["path"].endswith("cb_gold_accumulation.yaml")


def test_stamp_json_event_appends_newest_first(dirs):
    sdir, edir = dirs
    cr.stamp("cat_20260603_nato", "intact", as_of="2026-07-01",
             structural_dir=sdir, event_dir=edir)
    cr.stamp("cat_20260603_nato", "weakening", "ceasefire signed", as_of="2026-08-27",
             structural_dir=sdir, event_dir=edir)
    doc = json.loads((edir / "cat_20260603_nato.json").read_text(encoding="utf-8"))

    assert doc["status_last_reviewed"] == "2026-08-27"
    assert [e["verdict"] for e in doc["review_log"]] == ["weakening", "intact"]   # newest first
    assert doc["status"] == "active"     # a stamp NEVER changes status — that is lifecycle's job


def test_review_log_is_capped_but_keeps_the_recent_history(dirs):
    sdir, edir = dirs
    for i in range(1, 8):
        cr.stamp("cat_20260603_nato", "intact", as_of=f"2026-0{i}-01", max_log=3,
                 structural_dir=sdir, event_dir=edir)
    doc = json.loads((edir / "cat_20260603_nato.json").read_text(encoding="utf-8"))
    assert len(doc["review_log"]) == 3
    assert doc["review_log"][0]["date"] == "2026-07-01"      # newest kept, oldest dropped


def test_stamp_raises_for_an_unknown_catalyst(dirs):
    sdir, edir = dirs
    with pytest.raises(FileNotFoundError):
        cr.stamp("struct_nope", structural_dir=sdir, event_dir=edir)


# ── Batch (the scan → stamp hop) ─────────────────────────────────────────────

def test_batch_stamps_what_it_can_and_reports_the_rest(dirs):
    sdir, edir = dirs
    out = cr.stamp_batch([
        {"catalyst_id": "struct_cb_gold_accumulation", "verdict": "intact"},
        {"catalyst_id": "cat_20260603_nato", "verdict": "weakening", "evidence": "reversal risk"},
        {"catalyst_id": "struct_ghost", "verdict": "intact"},
        {"catalyst_id": "cat_20260603_nato", "verdict": "breaking"},   # missing evidence
    ], as_of="2026-08-27", structural_dir=sdir, event_dir=edir)

    assert len(out["stamped"]) == 2
    assert {r["catalyst_id"] for r in out["failed"]} == {"struct_ghost", "cat_20260603_nato"}
    # One bad row must not abort the batch — a scan stamps ~20 catalysts in one hop.
    assert yaml.safe_load((sdir / "cb_gold_accumulation.yaml").read_text(
        encoding="utf-8"))["status_last_reviewed"] == "2026-08-27"


# ── Audit ────────────────────────────────────────────────────────────────────

def test_review_status_ranks_the_stalest_first_and_skips_dead_records(dirs):
    sdir, edir = dirs
    (sdir / "dead.yaml").write_text(
        'id: struct_dead\nstatus: merged\nstatus_last_reviewed: "2020-01-01"\n', encoding="utf-8")
    cr.stamp("cat_20260603_nato", "intact", as_of="2026-08-20",
             structural_dir=sdir, event_dir=edir)

    rows = cr.review_status(as_of=date(2026, 8, 27), structural_dir=sdir, event_dir=edir)
    ids = [r["catalyst_id"] for r in rows]

    assert "struct_dead" not in ids                       # merged/deactivated are not live state
    assert ids[0] == "struct_cb_gold_accumulation"        # stalest first (2026-01-01)
    assert rows[0]["freshness"] == "very_stale"
    nato = next(r for r in rows if r["catalyst_id"] == "cat_20260603_nato")
    assert nato["age_days"] == 7 and nato["freshness"] == "fresh"
