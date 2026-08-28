"""Unit tests for the deterministic catalyst lifecycle (catalyx.scorer.catalyst_lifecycle).

These rules used to be applied by LLM judgment in the review skill. The tests pin the exact
arithmetic — especially the AND in the archive rule and the reactivation path out of dormant,
which are the two places a loose reading silently shrinks or inflates the catalyst universe.
"""
from __future__ import annotations

import json
from datetime import date

import pytest
import yaml

from catalyx.scorer import catalyst_lifecycle as lc

CFG = {
    "governance": "auto",
    "event_archive_strength_below": 20,
    "event_archive_priced_in_min": 0.75,
    "structural_dormant_intensity_below": 40,
    "structural_dormant_consecutive_cycles": 2,
    "structural_dormant_if_exhausted": True,
    "event_promote_to_structural_cycles": 3,
}


# ── Event rules ──────────────────────────────────────────────────────────────

def test_event_archives_only_when_spent_AND_priced_in():
    assert lc.event_transition(12.0, 0.90, CFG)[0] == "archived"
    # Decayed but NOT absorbed → still tradable; the market hasn't priced it.
    assert lc.event_transition(12.0, 0.50, CFG)[0] is None
    # Priced in but still strong → still shaping the sector.
    assert lc.event_transition(65.0, 1.0, CFG)[0] is None
    assert lc.event_transition(19.9, 0.75, CFG)[0] == "archived"      # boundary: strictly below / at min


def test_event_with_missing_inputs_is_left_alone_not_guessed():
    status, reason = lc.event_transition(None, 0.9, CFG)
    assert status is None and "cannot evaluate" in reason
    assert lc.event_transition(10.0, None, CFG)[0] is None


def test_promotion_needs_repeat_detection_and_a_non_decaying_underlying():
    assert lc.promotion_candidate(3, 60.0, 80.0, CFG) is True        # 75% of raw → ongoing
    assert lc.promotion_candidate(2, 60.0, 80.0, CFG) is False       # not re-detected enough
    assert lc.promotion_candidate(4, 20.0, 80.0, CFG) is False       # 25% of raw → a spent spike


# ── Structural rules ─────────────────────────────────────────────────────────

def test_structural_goes_dormant_only_after_consecutive_cycles():
    assert lc.structural_transition("active", 35.0, "mainstream", 2, CFG)[0] == "dormant"
    got, reason = lc.structural_transition("active", 35.0, "mainstream", 1, CFG)
    assert got is None and "1/2 consecutive" in reason               # one dip is noise


def test_exhausted_narrative_goes_dormant_regardless_of_intensity():
    status, reason = lc.structural_transition("active", 88.0, "exhausted", 0, CFG)
    assert status == "dormant" and "exhausted" in reason


def test_dormant_reactivates_when_intensity_repoints():
    # Without this path `status` is a one-way ratchet that quietly shrinks the universe.
    assert lc.structural_transition("dormant", 55.0, "emerging", 0, CFG)[0] == "active"
    assert lc.structural_transition("dormant", 30.0, "emerging", 0, CFG)[0] is None
    # …but not while the narrative is exhausted.
    assert lc.structural_transition("dormant", 90.0, "exhausted", 0, CFG)[0] is None


def test_consecutive_below_counts_from_the_newest_cycle_only():
    hist = [{"score": 30}, {"score": 35}, {"score": 80}, {"score": 20}]
    assert lc.consecutive_below(hist, 40) == 2          # the run of 20 is ancient history
    assert lc.consecutive_below([{"score": 80}, {"score": 10}], 40) == 0
    assert lc.consecutive_below([], 40) == 0
    assert lc.consecutive_below([{"score": None}], 40) == 0          # unscored breaks the run


# ── End-to-end over files ────────────────────────────────────────────────────

@pytest.fixture
def dirs(tmp_path):
    sdir, edir = tmp_path / "structural", tmp_path / "events"
    sdir.mkdir()
    edir.mkdir()

    (sdir / "weak.yaml").write_text(yaml.safe_dump({
        "id": "struct_weak", "status": "active", "narrative_maturity": "mainstream",
        "intensity": {"current_score": 30, "history": [{"score": 30}, {"score": 32}]},
    }), encoding="utf-8")
    (sdir / "strong.yaml").write_text(yaml.safe_dump({
        "id": "struct_strong", "status": "active", "narrative_maturity": "emerging",
        "intensity": {"current_score": 88, "history": [{"score": 88}]},
    }), encoding="utf-8")
    (sdir / "merged.yaml").write_text(yaml.safe_dump({
        "id": "struct_merged", "status": "merged",
        "intensity": {"current_score": 5, "history": [{"score": 5}, {"score": 5}]},
    }), encoding="utf-8")

    # Old + fully priced in → archive. Anchored on the id's date, not on detected_at.
    (edir / "cat_20250101_spent.json").write_text(json.dumps({
        "id": "cat_20250101_spent", "status": "active", "strength_score": 80,
        "decay_halflife_days": 30, "is_priced_in_estimate": 1.0,
        "detected_at": "2026-08-01T00:00:00Z",
    }), encoding="utf-8")
    (edir / "cat_20260801_fresh.json").write_text(json.dumps({
        "id": "cat_20260801_fresh", "status": "active", "strength_score": 75,
        "decay_halflife_days": 120, "is_priced_in_estimate": 0.25,
        "event_date": "2026-08-01",
    }), encoding="utf-8")
    return sdir, edir


def test_evaluate_proposes_the_right_transitions(dirs):
    sdir, edir = dirs
    out = lc.evaluate(cfg=CFG, structural_dir=sdir, event_dir=edir, as_of=date(2026, 8, 27))
    moves = {t["catalyst_id"]: t["to"] for t in out["transitions"]}

    assert moves == {"struct_weak": "dormant", "cat_20250101_spent": "archived"}
    unchanged = {u["catalyst_id"] for u in out["unchanged"]}
    assert {"struct_strong", "cat_20260801_fresh"} <= unchanged
    assert "struct_merged" not in moves and "struct_merged" not in unchanged   # terminal → skipped


def test_evaluate_is_a_dry_run_until_apply(dirs):
    sdir, edir = dirs
    lc.evaluate(cfg=CFG, structural_dir=sdir, event_dir=edir)
    assert yaml.safe_load((sdir / "weak.yaml").read_text(encoding="utf-8"))["status"] == "active"


def test_apply_writes_status_and_keeps_a_reversible_audit_trail(dirs):
    sdir, edir = dirs
    out = lc.evaluate(cfg=CFG, structural_dir=sdir, event_dir=edir)
    applied = lc.apply_transitions(out["transitions"], as_of="2026-08-27")
    assert len(applied) == 2

    weak = yaml.safe_load((sdir / "weak.yaml").read_text(encoding="utf-8"))
    assert weak["status"] == "dormant"
    assert weak["lifecycle_log"][0]["from"] == "active"          # prior status preserved
    assert weak["intensity"]["current_score"] == 30              # nothing else is touched

    spent = json.loads((edir / "cat_20250101_spent.json").read_text(encoding="utf-8"))
    assert spent["status"] == "archived"
    assert spent["lifecycle_log"][0]["to"] == "archived"


def test_reversal_comes_from_scan_evidence_not_from_arithmetic(dirs):
    sdir, edir = dirs
    deltas = [{"catalyst_id": "cat_20260801_fresh", "verdict": "breaking",
               "invalidated": True, "evidence": "policy walked back 2026-08-20"}]
    out = lc.evaluate(cfg=CFG, deltas=deltas, structural_dir=sdir, event_dir=edir)
    row = next(t for t in out["transitions"] if t["catalyst_id"] == "cat_20260801_fresh")

    assert row["to"] == "invalidated"                             # strong+unpriced, yet reversed
    assert "policy walked back" in row["reason"]

    lc.apply_transitions([row], as_of="2026-08-27")
    doc = json.loads((edir / "cat_20260801_fresh.json").read_text(encoding="utf-8"))
    assert doc["status"] == "invalidated" and doc["invalidation_reason"]


def test_applying_twice_is_idempotent(dirs):
    sdir, edir = dirs
    lc.apply_transitions(lc.evaluate(cfg=CFG, structural_dir=sdir, event_dir=edir)["transitions"])
    second = lc.evaluate(cfg=CFG, structural_dir=sdir, event_dir=edir)
    # Archived events are terminal; the dormant structural has no further move at this intensity.
    assert [t["catalyst_id"] for t in second["transitions"]] == []
