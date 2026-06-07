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
    # a movement (mirror) + a report on run_b
    lake.append_partition("movement", pd.DataFrame([
        {"id": "mov_20260605_copper_x", "executed_at": "2026-06-05T00:00:00Z", "action": "open",
         "sector_id": "copper", "etf": "COPX", "currency": "EUR", "amount_eur": 901.0,
         "qty": 10.0, "price": 90.0, "fees": 1.0, "trigger": "new_catalyst", "conviction": "medium",
         "attribution_json": '[{"catalyst_id": "struct_copper", "weight": 1.0}]',
         "score_run_id": "run_b", "score_composite": 75.0, "score_catalyst_alignment": 90.0,
         "score_regime_state": "intact", "run_id": "run_b"},
    ]), {"sector_id": "copper"}, lake_dir=tmp_path)
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


def test_lineage_walks_movement_to_run_report_snapshot(tmp_path):
    _seed(tmp_path)
    lin = q.lineage_for_movement("mov_20260605_copper_x", lake_dir=tmp_path)
    assert lin["catalysts"][0]["catalyst_id"] == "struct_copper"
    assert lin["run_id"] == "run_b"
    assert lin["reports"][0]["report_type"] == "heatmap"
    assert lin["sector_snapshot"]["sector_id"] == "copper"


def test_catalyst_ledger_reads_latest_snapshot(tmp_path):
    _seed(tmp_path)
    lake.append_partition("catalyst_performance", pd.DataFrame([
        {"catalyst_id": "struct_copper", "invested_eur": 1000.0, "realized_eur": 0.0,
         "n_movements": 1, "sectors": "copper", "as_of": "2026-06-06"},
    ]), {"as_of": "2026-06-06"}, lake_dir=tmp_path)
    led = q.catalyst_ledger(lake_dir=tmp_path)
    assert led[0]["catalyst_id"] == "struct_copper" and led[0]["invested_eur"] == 1000.0


def test_catalyst_lineage_combines_strategy_exposure_over_runs(tmp_path, monkeypatch):
    _seed(tmp_path)
    # catalyst → sectors comes from the Tier-1 studies; stub it so the test is self-contained.
    monkeypatch.setattr(q, "_sectors_for_catalyst", lambda cid: ["copper", "gold"])
    # two portfolios across two runs holding the catalyst's sectors (a rebalance between runs).
    # The lake partition key is (portfolio_id, run_id) → one append per (pid, run) group.
    holds = {
        ("momentum", "run_a"): [("copper", 10.0), ("gold", 5.0)],
        ("catalyx", "run_a"): [("copper", 8.0)],
        # run_b: momentum trims copper + exits gold; catalyx enters gold
        ("momentum", "run_b"): [("copper", 7.0)],
        ("catalyx", "run_b"): [("copper", 8.0), ("gold", 4.0)],
    }
    for (pid, run), secs in holds.items():
        lake.append_partition("portfolio_holding", pd.DataFrame([
            {"portfolio_id": pid, "run_id": run, "sector_id": sec, "weight_pct": w,
             "primary_etf": "X", "composite": 70.0, "momentum": 60.0,
             "narrative_maturity": "mainstream", "rank_in_portfolio": i + 1}
            for i, (sec, w) in enumerate(secs)
        ]), {"portfolio_id": pid, "run_id": run}, lake_dir=tmp_path)

    lin = q.catalyst_lineage("struct_x", lake_dir=tmp_path)
    assert lin["sectors"] == ["copper", "gold"]
    ts = {t["run_id"]: t for t in lin["timeseries"]}
    # run_a: momentum 15, catalyx 8 → combined mean = 11.5
    assert ts["run_a"]["by_strategy"] == {"momentum": 15.0, "catalyx": 8.0}
    assert ts["run_a"]["combined_pct"] == 11.5
    # run_b: momentum 7 (gold gone), catalyx 12 → combined mean = 9.5
    assert ts["run_b"]["by_strategy"] == {"momentum": 7.0, "catalyx": 12.0}
    assert ts["run_b"]["combined_pct"] == 9.5
    # latest move: momentum −8pp (15→7), catalyx +4pp (8→12)
    latest = {x["portfolio_id"]: x for x in lin["latest"]}
    assert latest["momentum"]["move"] == "-8.0pp" and latest["momentum"]["exposure_pct"] == 7.0
    assert latest["catalyx"]["move"] == "+4.0pp"


def test_empty_lake_returns_empty(tmp_path):
    assert q.latest_ranking(lake_dir=tmp_path) == []
    assert q.portfolio_compare(lake_dir=tmp_path) == []
    assert q.catalyst_ledger(lake_dir=tmp_path) == []
    assert q.lineage_for_movement("x", lake_dir=tmp_path) == {"error": "no movements in lake (run movement_repo ingest)"}
    assert q.catalyst_lineage("x", lake_dir=tmp_path) == {
        "catalyst_id": "x", "sectors": [], "movements": [], "timeseries": [], "latest": []}
