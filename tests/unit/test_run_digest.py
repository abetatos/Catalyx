"""Unit tests for the run digest and the CLI digests (v4 Phase D).

Two things are pinned here, and both are about COST, which is the one property that silently
regresses because nothing fails when it does:

1. `run_digest` is the review's single input. It must survive a run where any source is missing —
   a digest that raises sends the review back to threading eight payloads by hand — and it must
   preserve the rule precedence, because Step 6 reads its `actions[]` in order.
2. No scorer may dump its full JSON unconditionally. Every one of them has `--json`; a scorer that
   prints 100 KB after its table is charging the caller for output nobody asked for, and the only
   way that stays fixed is a test that reads the source.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from catalyx.store import run_digest as rd


# ── The digest survives a run that produced nothing ──────────────────────────

def test_a_run_with_no_lake_and_no_state_still_produces_a_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(rd, "_REPORTS", tmp_path / "reports")
    d = rd.build(as_of="2026-01-01", lake_dir=tmp_path / "empty_lake")
    assert d["as_of"] == "2026-01-01"
    assert d["missing"], "a digest with no sources must SAY so, not present empty as complete"
    assert rd.render(d)                       # never raises on a partial dict


def test_render_never_raises_on_a_half_built_digest():
    # Every section is optional by construction; the renderer is what the review reads first, so
    # one absent table must not take the whole read down.
    assert rd.render({"as_of": "2026-01-01"})
    assert rd.render({"as_of": "2026-01-01", "rebalance": {}, "book": {}, "portfolios": []})


# ── The rule precedence survives the trip through the digest ─────────────────

def _seed_rebalance(tmp_path, rows):
    import pandas as pd

    from catalyx.store import lake

    run = "run_20260828_000000"
    for r in rows:
        r.setdefault("run_id", run)
        r.setdefault("strategy", "catalyx")
        r.setdefault("book_total_capital_eur", 10_000.0)
        r.setdefault("book_cash_action_eur", 1_000.0)
        r.setdefault("deploy_ratio", 0.85)
    lake.append_partition("rebalance", pd.DataFrame(rows),
                          {"run_id": run, "strategy": "catalyx"}, overwrite=True,
                          lake_dir=tmp_path)
    return run


def test_actions_come_out_in_rule_precedence_and_holds_are_separated(tmp_path, monkeypatch):
    monkeypatch.setattr(rd, "_REPORTS", tmp_path / "reports")
    _seed_rebalance(tmp_path, [
        {"sector_id": "a", "rule_action": "BUY", "trade_eur": 100.0},
        {"sector_id": "b", "rule_action": "HOLD", "trade_eur": 0.0},
        {"sector_id": "c", "rule_action": "SELL", "trade_eur": -500.0},
        {"sector_id": "d", "rule_action": "ADD", "trade_eur": 200.0},
    ])
    reb = rd.build(as_of="2026-08-28", lake_dir=tmp_path)["rebalance"]
    assert [r["sector_id"] for r in reb["actions"]] == ["c", "d", "a"]
    assert reb["holds"] == ["b"]
    assert reb["n_actions"] == 3 and reb["n_hold"] == 1
    # The book-level constants ride on every row; the digest must lift them out, or the cash row
    # cannot be priced downstream.
    assert reb["total_capital_eur"] == 10_000.0
    assert reb["cash_action_eur"] == 1_000.0


def test_the_written_file_is_valid_json_and_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(rd, "_REPORTS", tmp_path / "reports")
    _seed_rebalance(tmp_path, [{"sector_id": "a", "rule_action": "BUY", "trade_eur": 100.0}])
    d = rd.build(as_of="2026-08-28", lake_dir=tmp_path)
    path = rd.write(d)
    assert path.name == "run_20260828.json"
    assert json.loads(path.read_text(encoding="utf-8"))["as_of"] == "2026-08-28"


# ── No scorer bills the caller for output nobody asked for ───────────────────

_SCORERS = ("sector_scorer", "catalyst_scorer", "momentum_engine", "intensity_engine")


@pytest.mark.parametrize("module", _SCORERS)
def test_no_scorer_dumps_its_full_json_unconditionally(module):
    src = Path("catalyx/scorer") / f"{module}.py"
    text = src.read_text(encoding="utf-8")
    assert "--- JSON output ---" not in text, (
        f"{module} prints its full JSON after the table. The table is the human output and "
        f"--json is the machine output; printing both charges every caller for the larger one."
    )
    assert '"--json"' in text, f"{module} must still expose --json for the machine path"


@pytest.mark.parametrize("module", ("sector_scorer", "catalyst_scorer"))
def test_the_two_universe_scorers_offer_a_digest(module):
    text = (Path("catalyx/scorer") / f"{module}.py").read_text(encoding="utf-8")
    assert '"--digest"' in text, (
        f"{module} is called across the whole universe every run; without --digest the only "
        f"machine path is the ~100 KB --json dump."
    )


# ── D-d: the study digest a DECISION reads, not the dossier ──────────────────

def test_core_returns_the_consumed_fields_and_the_age_that_qualifies_them():
    from catalyx.store import sector_study_repo as ssr

    rec = ssr.core("copper_miners")
    assert rec is not None
    # The two fields anything downstream actually consumes: `narrative_maturity` (→ crowding_risk
    # in snapshot_repo, → the exhaustion test in catalyst_lifecycle) and `active_catalyst_ids`
    # (→ the catalyst→sector map in portfolio.catalyst_exposure_rows).
    assert "narrative_maturity" in rec and "active_catalyst_ids" in rec
    # …and the freshness, in the same breath as the value it qualifies: a STALE study is worse
    # than none, so `age_days` must never be one CLI call away from the number it discredits.
    assert rec["age_days"] is None or isinstance(rec["age_days"], int)
    assert rec["last_updated"]


def test_core_is_an_order_of_magnitude_smaller_than_the_dossier():
    import json

    from catalyx.store import sector_study_repo as ssr

    full = ssr.get_study("study_copper_miners")
    if full is None:                                   # pragma: no cover - data-dependent
        pytest.skip("no copper study in this checkout")
    small = len(json.dumps(ssr.core("copper_miners"), default=str))
    assert small < len(json.dumps(full, default=str)) / 5, (
        "the whole point of `core` is that a run reading two fields does not pay for 20 KB of "
        "research it will not read"
    )


def test_core_all_is_sorted_freshest_first_and_never_raises():
    from catalyx.store import sector_study_repo as ssr

    rows = ssr.core_all()
    ages = [r["age_days"] for r in rows if r["age_days"] is not None]
    assert ages == sorted(ages)


def test_core_on_an_unknown_sector_is_none_not_an_exception():
    from catalyx.store import sector_study_repo as ssr

    assert ssr.core("not_a_sector_id") is None
