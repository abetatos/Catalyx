"""Unit tests for deterministic indicator updates (catalyx.store.indicator_update).

The nine hand-executed steps this replaces had one silent failure mode and one loud one. The
silent one: the prior observation was appended to the DEPRECATED inline `value_history` while
`intensity_engine` reads the lake, so the observation looked recorded and the percentile never
saw it. The loud one: the `current → last` shift done in the wrong order loses a data point
permanently. Both are pinned below, plus idempotence — a re-applied scan must not stack rows,
because the percentile weights by row count.

Every test writes to a tmp_path copy. Nothing here may touch catalyx/config/structural_catalysts.
"""
from __future__ import annotations

import json

import pytest
import yaml

from catalyx.store import indicator_update as iu

_CATALYST = {
    "id": "struct_test_thing",
    "name": "Test catalyst",
    "status": "active",
    "status_last_reviewed": "2026-01-01",
    "narrative_maturity": "emerging",
    "indicators": [
        {"id": "ind_01", "name": "Up is good", "direction": "higher_is_stronger",
         "threshold_strong": 100.0, "threshold_weak": 50.0,
         "current_value": 80.0, "last_value": 70.0, "last_date": "2026-06-01"},
        {"id": "ind_02", "name": "Down is good", "direction": "lower_is_stronger",
         "threshold_strong": 2.0, "threshold_weak": 8.0,
         "current_value": 5.0, "last_value": 6.0, "last_date": "2026-06-01"},
    ],
    "deactivation_conditions": [
        {"id": "deact_01", "condition": "The thing stops being a thing.",
         "action": "full_deactivation"},
    ],
}


@pytest.fixture
def catalyst_dir(tmp_path):
    d = tmp_path / "structural_catalysts"
    d.mkdir()
    (d / "test_thing.yaml").write_text(yaml.safe_dump(_CATALYST, sort_keys=False),
                                       encoding="utf-8")
    return d


def _read(catalyst_dir):
    return yaml.safe_load((catalyst_dir / "test_thing.yaml").read_text(encoding="utf-8"))


# ── Locating the file ────────────────────────────────────────────────────────

def test_find_file_accepts_the_id_with_or_without_the_struct_prefix(catalyst_dir):
    assert iu.find_file("struct_test_thing", catalyst_dir).name == "test_thing.yaml"
    assert iu.find_file("test_thing", catalyst_dir).name == "test_thing.yaml"


def test_a_missing_catalyst_raises_rather_than_creating_one(catalyst_dir):
    with pytest.raises(FileNotFoundError):
        iu.find_file("struct_does_not_exist", catalyst_dir)


# ── The value shift ──────────────────────────────────────────────────────────

def test_the_shift_keeps_the_prior_reading_instead_of_overwriting_it():
    ind = {"current_value": 80.0, "last_value": 70.0, "last_date": "2026-06-01"}
    out = iu.shift_values(ind, 95.0, "2026-08-28")
    assert out["fields"] == {"current_value": 95.0, "last_value": 80.0,
                             "last_date": "2026-08-28"}
    # The PRIOR value is what gets archived, stamped with the date it was actually observed.
    assert out["archive"] == {"date": "2026-06-01", "value": 80.0}


def test_a_first_ever_reading_archives_nothing():
    out = iu.shift_values({"current_value": None}, 12.0, "2026-08-28")
    assert out["archive"] is None
    assert out["fields"]["last_value"] is None


# ── Direction awareness ──────────────────────────────────────────────────────

def test_weakened_reads_the_indicators_own_direction():
    up = {"direction": "higher_is_stronger"}
    down = {"direction": "lower_is_stronger"}
    assert iu.weakened(up, 80, 70) is True
    assert iu.weakened(up, 80, 90) is False
    assert iu.weakened(down, 5, 7) is True          # rising is bad here
    assert iu.weakened(down, 5, 3) is False


def test_an_unknown_direction_is_never_reported_as_weakening():
    # An unknown must not become a warning, nor an all-clear. It stays unknown.
    assert iu.weakened({}, 80, 10) is False
    assert iu.weakened({"direction": "higher_is_stronger"}, None, 10) is False


def test_threshold_weak_is_evaluated_on_the_correct_side():
    up = {"direction": "higher_is_stronger", "threshold_weak": 50.0}
    down = {"direction": "lower_is_stronger", "threshold_weak": 8.0}
    assert iu.crossed_weak_threshold(up, 40.0) is True
    assert iu.crossed_weak_threshold(up, 60.0) is False
    assert iu.crossed_weak_threshold(down, 9.0) is True
    assert iu.crossed_weak_threshold(down, 3.0) is False
    assert iu.crossed_weak_threshold({"direction": "higher_is_stronger"}, 3.0) is False


def test_deactivation_conditions_surface_only_when_the_number_actually_weakens():
    ind = _CATALYST["indicators"][0]
    assert iu.deactivation_notice(_CATALYST, ind, 80.0, 90.0) == []      # improving → silence
    notice = iu.deactivation_notice(_CATALYST, ind, 80.0, 70.0)
    assert notice and "moved against the thesis" in notice[0]
    assert any("stops being a thing" in line for line in notice)
    # Below threshold_weak names the sharper reason.
    assert "threshold_weak" in iu.deactivation_notice(_CATALYST, ind, 80.0, 40.0)[0]


# ── Applying (writes) ────────────────────────────────────────────────────────

def test_apply_one_writes_the_shift_and_stamps_the_review_date(catalyst_dir, tmp_path):
    out = iu.apply_one("struct_test_thing", "ind_01", 95.0, as_of="2026-08-28",
                       note="Q2 print", structural_dir=catalyst_dir, lake_dir=tmp_path / "lake")
    doc = _read(catalyst_dir)
    ind = doc["indicators"][0]
    assert (ind["current_value"], ind["last_value"], ind["last_date"]) == (95.0, 80.0, "2026-08-28")
    assert ind["update_note"] == "Q2 print"
    assert doc["status_last_reviewed"] == "2026-08-28"
    assert out["old_value"] == 80.0 and out["new_value"] == 95.0


def test_history_goes_to_the_lake_not_to_the_deprecated_inline_field(catalyst_dir, tmp_path):
    from catalyx.store import lake

    lake_dir = tmp_path / "lake"
    iu.apply_one("struct_test_thing", "ind_01", 95.0, as_of="2026-08-28",
                 structural_dir=catalyst_dir, lake_dir=lake_dir, source="test")
    df = lake.read_table("indicator_history", lake_dir=lake_dir)
    assert len(df) == 1
    assert df.iloc[0]["value"] == 80.0 and df.iloc[0]["date"] == "2026-06-01"
    # …and the deprecated inline field is left alone rather than quietly re-populated.
    assert "value_history" not in _read(catalyst_dir)["indicators"][0]


def test_re_applying_the_same_observation_does_not_stack_rows(catalyst_dir, tmp_path):
    from catalyx.store import lake

    lake_dir = tmp_path / "lake"
    iu.apply_one("struct_test_thing", "ind_01", 95.0, as_of="2026-08-28",
                 structural_dir=catalyst_dir, lake_dir=lake_dir)
    second = iu.apply_one("struct_test_thing", "ind_01", 95.0, as_of="2026-08-28",
                          structural_dir=catalyst_dir, lake_dir=lake_dir)
    # The second call archives (2026-08-28, 95.0) — a NEW point, so history grows by exactly one,
    # and re-running it a third time adds nothing.
    third = iu.apply_one("struct_test_thing", "ind_01", 95.0, as_of="2026-08-28",
                         structural_dir=catalyst_dir, lake_dir=lake_dir)
    assert third["archived"] == "skipped (already in history)"
    assert len(lake.read_table("indicator_history", lake_dir=lake_dir)) == 2
    assert second["archived"] != "skipped (already in history)"


def test_an_unknown_indicator_id_names_the_ones_that_exist(catalyst_dir, tmp_path):
    with pytest.raises(ValueError, match="ind_01"):
        iu.apply_one("struct_test_thing", "ind_99", 1.0, structural_dir=catalyst_dir,
                     lake_dir=tmp_path / "lake")


# ── Narrative maturity ───────────────────────────────────────────────────────

def test_maturity_accepts_only_the_five_level_enum(catalyst_dir):
    out = iu.set_maturity("struct_test_thing", "crowded", as_of="2026-08-28",
                          structural_dir=catalyst_dir)
    assert out["old"] == "emerging" and _read(catalyst_dir)["narrative_maturity"] == "crowded"
    with pytest.raises(ValueError):
        iu.set_maturity("struct_test_thing", "72", structural_dir=catalyst_dir)


# ── The batch contract (shared with catalyst_review + catalyst_lifecycle) ────

def test_parse_batch_accepts_both_shapes_of_the_scan_deltas_file():
    nested = {"deltas": [{"catalyst_id": "struct_a", "source": "WGC",
                          "indicators": [{"id": "ind_01", "value": 1.0},
                                         {"indicator_id": "ind_02", "value": 2.0}]}]}
    flat = [{"catalyst_id": "struct_a", "indicator_id": "ind_01", "value": 1.0}]
    assert len(iu.parse_batch(nested)) == 2
    assert iu.parse_batch(nested)[0]["source"] == "WGC"     # inherits the delta's source
    assert len(iu.parse_batch(flat)) == 1


def test_parse_batch_ignores_verdict_only_deltas():
    # The same file feeds catalyst_review (verdicts) and this module (values). A catalyst that
    # was re-verified but had no new number must not become a phantom update.
    payload = [{"catalyst_id": "struct_a", "verdict": "intact", "evidence": "no change"}]
    assert iu.parse_batch(payload) == []


def test_apply_batch_recomputes_each_touched_catalyst_once(catalyst_dir, tmp_path, monkeypatch):
    calls = []

    class FakeEngine:
        @staticmethod
        def compute_from_yaml(path):
            calls.append(("compute", str(path)))
            return {"catalyst_id": "struct_test_thing", "stored_score": 70.0,
                    "computed_score": 75.0}

        @staticmethod
        def write_back(path, result, period=None):
            calls.append(("write", str(path)))

    import catalyx.scorer.intensity_engine as real
    monkeypatch.setattr(real, "compute_from_yaml", FakeEngine.compute_from_yaml)
    monkeypatch.setattr(real, "write_back", FakeEngine.write_back)

    payload = [{"catalyst_id": "struct_test_thing",
                "indicators": [{"id": "ind_01", "value": 95.0},
                               {"id": "ind_02", "value": 3.0}]}]
    res = iu.apply_batch(payload, as_of="2026-08-28", structural_dir=catalyst_dir,
                         lake_dir=tmp_path / "lake")
    assert len(res["applied"]) == 2 and res["failed"] == []
    # TWO indicators, ONE recompute — the whole point of the batch hop.
    assert [c for c in calls if c[0] == "compute"] == [("compute", str(catalyst_dir / "test_thing.yaml"))]


def test_a_bad_row_fails_alone_and_does_not_lose_the_good_ones(catalyst_dir, tmp_path):
    payload = [{"catalyst_id": "struct_test_thing", "indicator_id": "ind_01", "value": 95.0},
               {"catalyst_id": "struct_nope", "indicator_id": "ind_01", "value": 1.0}]
    res = iu.apply_batch(payload, as_of="2026-08-28", recompute=False,
                         structural_dir=catalyst_dir, lake_dir=tmp_path / "lake")
    assert len(res["applied"]) == 1 and len(res["failed"]) == 1
    assert "struct_nope" in json.dumps(res["failed"])
    assert _read(catalyst_dir)["indicators"][0]["current_value"] == 95.0
