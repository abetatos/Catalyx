"""Unit tests for the externalized indicator value-history store (Fase C)."""
from __future__ import annotations

from catalyx.store import indicator_history as ih


def test_write_and_read_roundtrip(tmp_path):
    ih.write_catalyst("struct_x", {
        "ind_01": [{"date": "2025-12-31", "value": 399}, {"date": "2026-03-31", "value": 290}],
        "ind_02": [{"date": "2026-01-01", "value": 12500.0}],
    }, lake_dir=tmp_path)

    hist = ih.history_for("struct_x", lake_dir=tmp_path)
    assert set(hist) == {"ind_01", "ind_02"}
    assert [p["value"] for p in hist["ind_01"]] == [399.0, 290.0]
    assert hist["ind_02"][0]["date"] == "2026-01-01"


def test_write_catalyst_is_one_partition_per_catalyst(tmp_path):
    ih.write_catalyst("struct_a", {"i": [{"date": "2026-01-01", "value": 1}]}, lake_dir=tmp_path)
    ih.write_catalyst("struct_b", {"i": [{"date": "2026-01-01", "value": 2}]}, lake_dir=tmp_path)
    # each catalyst is isolated to its own partition
    assert [p["value"] for p in ih.history_for("struct_a", lake_dir=tmp_path)["i"]] == [1.0]
    assert [p["value"] for p in ih.history_for("struct_b", lake_dir=tmp_path)["i"]] == [2.0]


def test_append_observation_preserves_existing(tmp_path):
    ih.write_catalyst("struct_x", {"ind_01": [{"date": "2026-01-01", "value": 10}]},
                      lake_dir=tmp_path)
    ih.append_observation("struct_x", "ind_01", "2026-02-01", 20, source="update_skill",
                          lake_dir=tmp_path)
    vals = sorted(p["value"] for p in ih.history_for("struct_x", lake_dir=tmp_path)["ind_01"])
    assert vals == [10.0, 20.0]


def test_skips_none_values(tmp_path):
    n = ih.write_catalyst("struct_x", {
        "ind_01": [{"date": "2026-01-01", "value": None}, {"date": "2026-02-01", "value": 5}],
    }, lake_dir=tmp_path)
    assert n == 1  # the None observation is dropped


def test_read_missing_catalyst_returns_empty(tmp_path):
    assert ih.history_for("struct_absent", lake_dir=tmp_path) == {}
