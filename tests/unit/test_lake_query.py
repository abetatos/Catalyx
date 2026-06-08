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


def test_portfolio_catalyst_exposure_timeseries_and_time_weighted_avg(tmp_path):
    # score_run dates (both in the past) so the average is time-weighted: run_a was live ~9 days
    # (until run_b), run_b is the current allocation (live until 'now' >> 9d → dominates the avg).
    for rid, ts in [("run_a", "2026-05-01T00:00:00"), ("run_b", "2026-05-10T00:00:00")]:
        lake.append_partition("score_run", pd.DataFrame([
            {"run_id": rid, "run_at": ts, "scoring_version": "v1", "sector_count": 2},
        ]), {"run_id": rid}, lake_dir=tmp_path)
    # one portfolio decomposed by catalyst across two rebalances
    exp = {
        "run_a": [("struct_ai", 60.0), ("struct_nato", 40.0)],
        "run_b": [("struct_ai", 20.0), ("struct_nato", 30.0), ("cash", 50.0)],
    }
    for rid, cats in exp.items():
        lake.append_partition("portfolio_catalyst_exposure", pd.DataFrame([
            {"portfolio_id": "catalyx", "run_id": rid, "catalyst_id": cid, "pct": pct,
             "eur": pct * 10, "notional_eur": 1000.0}
            for cid, pct in cats
        ]), {"portfolio_id": "catalyx", "run_id": rid}, lake_dir=tmp_path)

    res = q.portfolio_catalyst_exposure("catalyx", lake_dir=tmp_path)
    assert res["notional_eur"] == 1000.0
    ts = {t["run_id"]: t["by_catalyst"] for t in res["timeseries"]}
    assert ts["run_a"] == {"struct_ai": 60.0, "struct_nato": 40.0}
    assert ts["run_b"]["cash"] == 50.0
    # time weights: run_a live 9 days, run_b live until ~now (>> 9d), so run_b dominates the avg.
    avg = {a["catalyst_id"]: a["avg_pct"] for a in res["average"]}
    # struct_ai avg between its two values (60, 20), pulled toward 20 by run_b's larger weight
    assert 20.0 <= avg["struct_ai"] < 60.0
    assert res["average"][0]["avg_eur"] == round(res["average"][0]["avg_pct"] * 10, 2)


def test_catalyst_exposure_rows_split_equally(tmp_path):
    # portfolio.catalyst_exposure_rows splits a sector's weight across its catalysts.
    from catalyx.execution import portfolio as pf
    holdings = [
        {"sector_id": "ai_infra", "weight_pct": 12.0},   # 3 catalysts → 4% each
        {"sector_id": "gold", "weight_pct": 9.0},         # 1 catalyst → 9%
        {"sector_id": "mystery", "weight_pct": 5.0},      # none → uncatalyzed
    ]
    smap = {"ai_infra": ["c_ai", "c_grid", "c_cu"], "gold": ["c_gold"], "mystery": []}
    rows = pf.catalyst_exposure_rows("catalyx", "run_a", holdings, built_at=None,
                                     sector_catalysts=smap, notional=1000.0)
    by = {r["catalyst_id"]: r["pct"] for r in rows}
    assert by == {"c_ai": 4.0, "c_grid": 4.0, "c_cu": 4.0, "c_gold": 9.0, "uncatalyzed": 5.0}
    assert sum(by.values()) == 26.0  # = total deployed weight (12+9+5), nothing double-counted
    gold = next(r for r in rows if r["catalyst_id"] == "c_gold")
    assert gold["eur"] == 90.0  # 9% of €1000


def test_empty_lake_returns_empty(tmp_path):
    assert q.latest_ranking(lake_dir=tmp_path) == []
    assert q.portfolio_compare(lake_dir=tmp_path) == []
    assert q.catalyst_ledger(lake_dir=tmp_path) == []
    assert q.lineage_for_movement("x", lake_dir=tmp_path) == {"error": "no movements in lake (run movement_repo ingest)"}
    assert q.portfolio_catalyst_exposure("x", lake_dir=tmp_path) == {
        "portfolio_id": "x", "notional_eur": None, "timeseries": [], "average": []}
