"""Unit tests for the DuckDB lake query layer (Fase E). Network-free."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from catalyx.store import lake
from catalyx.store import lake_query as q


def _seed(tmp_path):
    now = datetime.now(timezone.utc)
    # two runs of sector_snapshot
    for run, comp in [("run_a", 70.0), ("run_b", 75.0)]:
        df = pd.DataFrame([
            {"run_id": run, "snapshot_at": now, "sector_id": "copper", "rank": 1,
             "composite": comp, "momentum": 80.0, "catalyst_alignment": 90.0,
             "crowding_risk": 50.0, "narrative_maturity": "mainstream",
             "primary_etf": "COPX", "scoring_version": "v1"},
            {"run_id": run, "snapshot_at": now, "sector_id": "gold", "rank": 2,
             "composite": comp - 5, "momentum": 60.0, "catalyst_alignment": 70.0,
             "crowding_risk": 40.0, "narrative_maturity": "emerging",
             "primary_etf": "GDX", "scoring_version": "v1"},
        ])
        lake.append_partition("sector_snapshot", df, {"run_id": run}, lake_dir=tmp_path)
    # portfolio NAV (two portfolios)
    lake.append_partition("portfolio_nav", pd.DataFrame([
        {"portfolio_id": "balanced", "kind": "model", "date": "2026-06-05", "nav": 108.0,
         "return_pct": 8.0, "benchmark_etf": "ACWI", "vs_benchmark_pct": 3.0},
    ]), {"portfolio_id": "balanced"}, lake_dir=tmp_path)
    lake.append_partition("portfolio_nav", pd.DataFrame([
        {"portfolio_id": "real", "kind": "real", "date": "2026-06-05", "nav": 102.0,
         "return_pct": 2.0, "benchmark_etf": "ACWI", "vs_benchmark_pct": -3.0},
    ]), {"portfolio_id": "real"}, lake_dir=tmp_path)
    # a trade + a report on run_b
    lake.append_partition("portfolio_trade", pd.DataFrame([
        {"trade_id": "real_2026-06-05_001", "portfolio_id": "real", "date": "2026-06-05",
         "etf": "COPX", "side": "buy", "qty": 10.0, "price": 90.0, "fees": 1.0,
         "eur_value": 901.0, "thesis_id": "thesis_copper", "run_id": "run_b"},
    ]), {"portfolio_id": "real"}, lake_dir=tmp_path)
    lake.append_partition("report", pd.DataFrame([
        {"run_id": "run_b", "report_type": "heatmap", "report_date": "2026-06-05",
         "path": "data/reports/heatmap_20260605.md", "content_md": "..."},
    ]), lake_dir=tmp_path)


def test_sector_history_ordered(tmp_path):
    _seed(tmp_path)
    h = q.sector_history("copper", lake_dir=tmp_path)
    assert [r["run_id"] for r in h] == ["run_a", "run_b"]
    assert [r["composite"] for r in h] == [70.0, 75.0]


def test_latest_ranking_uses_latest_run(tmp_path):
    _seed(tmp_path)
    r = q.latest_ranking(top_n=5, lake_dir=tmp_path)
    assert [x["sector_id"] for x in r] == ["copper", "gold"]
    assert r[0]["composite"] == 75.0  # run_b (latest), not run_a


def test_portfolio_compare_one_row_each_sorted(tmp_path):
    _seed(tmp_path)
    rows = q.portfolio_compare(lake_dir=tmp_path)
    assert [x["portfolio_id"] for x in rows] == ["balanced", "real"]  # sorted by return desc
    assert rows[0]["return_pct"] == 8.0


def test_lineage_walks_trade_to_run_report_snapshot(tmp_path):
    _seed(tmp_path)
    lin = q.lineage_for_trade("real_2026-06-05_001", lake_dir=tmp_path)
    assert lin["thesis_id"] == "thesis_copper"
    assert lin["run_id"] == "run_b"
    assert lin["reports"][0]["report_type"] == "heatmap"
    assert lin["sector_snapshot"]["sector_id"] == "copper"  # matched COPX → copper in run_b


def test_empty_lake_returns_empty(tmp_path):
    assert q.latest_ranking(lake_dir=tmp_path) == []
    assert q.portfolio_compare(lake_dir=tmp_path) == []
    assert q.lineage_for_trade("x", lake_dir=tmp_path) == {"error": "no trades in lake"}
