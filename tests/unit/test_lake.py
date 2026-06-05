"""Unit tests for the parquet lake primitive (catalyx.store.lake)."""
from __future__ import annotations

import pandas as pd
import pytest

from catalyx.store import lake


def test_append_and_read_unions_partitions(tmp_path):
    df_jun = pd.DataFrame({"sector_id": ["copper", "gold"], "composite": [72.0, 80.0]})
    df_jul = pd.DataFrame({"sector_id": ["copper", "gold"], "composite": [78.0, 79.0]})

    lake.append_partition("sector_snapshot", df_jun, {"run_id": "run_jun"}, lake_dir=tmp_path)
    lake.append_partition("sector_snapshot", df_jul, {"run_id": "run_jul"}, lake_dir=tmp_path)

    out = lake.read_table("sector_snapshot", lake_dir=tmp_path)
    assert len(out) == 4                                  # both months unioned
    assert set(out["run_id"]) == {"run_jun", "run_jul"}   # partition key materialized as a column
    assert sorted(out["composite"]) == [72.0, 78.0, 79.0, 80.0]


def test_partition_key_filename_is_human_navigable(tmp_path):
    fp = lake.append_partition("score_run",
                               pd.DataFrame({"run_id": ["run_x"], "sector_count": [44]}),
                               {"run_id": "run_x"}, lake_dir=tmp_path)
    assert fp.name == "run_id=run_x.parquet"
    assert fp.parent == lake.table_dir("score_run", lake_dir=tmp_path)


def test_append_is_immutable_by_default(tmp_path):
    df = pd.DataFrame({"run_id": ["run_x"], "composite": [1.0]})
    lake.append_partition("sector_snapshot", df, {"run_id": "run_x"}, lake_dir=tmp_path)
    with pytest.raises(FileExistsError):
        lake.append_partition("sector_snapshot", df, {"run_id": "run_x"}, lake_dir=tmp_path)
    # overwrite=True is the only escape hatch (same-day correction)
    lake.append_partition("sector_snapshot", df, {"run_id": "run_x"},
                          overwrite=True, lake_dir=tmp_path)


def test_unpartitioned_table_accumulates_timestamped_parts(tmp_path):
    a = lake.append_partition("forward_returns", pd.DataFrame({"etf": ["A"], "ret": [0.1]}),
                              lake_dir=tmp_path)
    b = lake.append_partition("forward_returns", pd.DataFrame({"etf": ["B"], "ret": [0.2]}),
                              lake_dir=tmp_path)
    assert a.name != b.name                               # distinct part files, no clobber
    assert len(lake.read_table("forward_returns", lake_dir=tmp_path)) == 2


def test_missing_partition_key_raises(tmp_path):
    with pytest.raises(ValueError):
        lake.append_partition("sector_snapshot", pd.DataFrame({"composite": [1.0]}),
                              partition={}, lake_dir=tmp_path)


def test_unknown_table_raises():
    with pytest.raises(KeyError):
        lake.table_dir("does_not_exist")


def test_read_empty_table_returns_empty_frame(tmp_path):
    out = lake.read_table("momentum", lake_dir=tmp_path)
    assert isinstance(out, pd.DataFrame) and out.empty
