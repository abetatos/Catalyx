"""CA conviction tilt (v10 P8) — the catalyst score sizing the book as a DECLARED PRIOR.

What these tests pin, in order of importance:
  1. The doctrine: CA is never disabled — the config floor makes δ=0 unwritable, and an
     imputed CA is never tilted (up or down) on data nobody measured.
  2. The arithmetic: monotonic in CA, winsorized, total-preserving, no-op without a
     cross-section to standardize against.
  3. The ledger: the build persists the δ=0 counterfactual beside the final weight, and
     `ca_tilt_edge` prices the difference — localizing per sector and per driver.
Network-free.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from catalyx.config import weights as weights_cfg
from catalyx.execution import portfolio as pf
from catalyx.scorer import calibration
from catalyx.store import lake


# ── the pure function ────────────────────────────────────────────────────────

def test_higher_ca_never_gets_a_lower_weight():
    scores = [1.0, 1.0, 1.0, 1.0]
    tilted, rows = pf.ca_tilt(scores, [90.0, 70.0, 50.0, 30.0], [False] * 4, delta=0.20)
    assert tilted[0] > tilted[1] > tilted[2] > tilted[3]
    assert rows[0]["mult"] > 1.0 > rows[3]["mult"]


def test_the_total_is_preserved_so_the_tilt_moves_no_gross():
    scores = [3.0, 2.0, 1.0]
    tilted, _ = pf.ca_tilt(scores, [80.0, 50.0, 20.0], [False] * 3, delta=0.20)
    assert sum(tilted) == pytest.approx(sum(scores))


def test_an_imputed_ca_is_never_tilted_and_never_enters_the_moments():
    # The imputed name keeps mult 1.0 exactly; the measured names' z is computed WITHOUT it
    # (with it, mean/std would shift and the untilted row would still distort its neighbours).
    _, rows = pf.ca_tilt([1.0, 1.0, 1.0], [90.0, 10.0, 50.0], [False, False, True], delta=0.2)
    assert rows[2]["mult"] == 1.0 and rows[2]["z"] == 0.0
    _, rows_wo = pf.ca_tilt([1.0, 1.0], [90.0, 10.0], [False, False], delta=0.2)
    assert rows[0]["z"] == pytest.approx(rows_wo[0]["z"])


def test_a_missing_ca_takes_the_neutral_multiplier():
    _, rows = pf.ca_tilt([1.0, 1.0, 1.0], [80.0, None, float("nan")], [False] * 3, delta=0.2)
    assert rows[1]["mult"] == 1.0 and rows[2]["mult"] == 1.0


def test_fewer_than_two_measured_or_a_flat_cross_section_is_a_noop():
    scores = [2.0, 1.0]
    assert pf.ca_tilt(scores, [90.0, None], [False, False], delta=0.2)[0] == scores
    assert pf.ca_tilt(scores, [50.0, 50.0], [False, False], delta=0.2)[0] == scores
    assert pf.ca_tilt([], [], [], delta=0.2)[0] == []


def test_the_winsor_caps_the_multiplier_at_the_declared_tier_scale():
    # One extreme outlier: its z is clamped at ±winsor, so the multiplier lands on the
    # tier-multiple anchors (1 + 0.2·2.5 = 1.5 ceiling, 1 − 0.2·2.5 = 0.5 floor) at most.
    _, rows = pf.ca_tilt([1.0] * 5, [99.0, 50.0, 50.0, 50.0, 1.0], [False] * 5,
                         delta=0.20, winsor_z=2.5)
    assert all(0.5 - 1e-9 <= r["mult"] <= 1.5 + 1e-9 for r in rows)


def test_config_floor_makes_delta_zero_unwritable():
    """User doctrine 2026-09-01: CA is tuned, never disabled."""
    pw = weights_cfg.portfolio_weighting()
    assert pw["ca_tilt_delta_min"] > 0
    assert pw["ca_tilt_delta"] >= pw["ca_tilt_delta_min"]
    # build_model_holdings applies max(delta, delta_min); the YAML ships an active δ
    assert pw["ca_tilt_delta"] == pytest.approx(0.20)


# ── the build: ledger columns and baseline books ─────────────────────────────

def _seed(tmp_path, rows):
    base = {"run_id": "run_t", "snapshot_at": datetime.now(timezone.utc),
            "flow_confirmation": 50.0, "has_study": 1, "scoring_version": "v",
            "crowding_risk": 50, "momentum": 70, "narrative_maturity": "emerging"}
    lake.append_partition("sector_snapshot", pd.DataFrame([{**base, **r} for r in rows]),
                          {"run_id": "run_t"}, lake_dir=tmp_path)


_PROFILE = {
    "portfolio_id": "test", "name": "T", "risk_profile": "balanced",
    "construction": {"max_positions": 3, "min_composite": 50, "min_momentum": 0,
                     "max_crowding": 100, "weighting": "composite_proportional",
                     "max_position_pct": 60},
}


def test_build_persists_the_ledger_and_ca_sizes_the_book(tmp_path):
    # Equal composites → the pre-CA weights are equal; only CA separates the names.
    _seed(tmp_path, [
        {"sector_id": "hi", "rank": 1, "composite": 70, "catalyst_alignment": 90.0,
         "ca_imputed": False, "primary_etf": "AAA"},
        {"sector_id": "mid", "rank": 2, "composite": 70, "catalyst_alignment": 50.0,
         "ca_imputed": False, "primary_etf": "BBB"},
        {"sector_id": "lo", "rank": 3, "composite": 70, "catalyst_alignment": 10.0,
         "ca_imputed": False, "primary_etf": "CCC"},
    ])
    res = pf.build_model_holdings("test", profile=_PROFILE, lake_dir=tmp_path)
    by = {h["sector_id"]: h for h in res["holdings"]}
    assert res["ca_tilt"] is not None and res["ca_tilt"]["active"]
    assert by["hi"]["weight_pct"] > by["mid"]["weight_pct"] > by["lo"]["weight_pct"]
    # the δ=0 counterfactual is persisted beside the final weight, and differs from it
    for h in res["holdings"]:
        assert h["w_pre_ca_pct"] is not None and h["ca_z"] is not None
    assert by["mid"]["w_pre_ca_pct"] == pytest.approx(by["hi"]["w_pre_ca_pct"], abs=0.01)
    assert by["hi"]["weight_pct"] != pytest.approx(by["hi"]["w_pre_ca_pct"], abs=0.01)
    # ...and it reaches the lake, where ca_tilt_edge will read it
    df = lake.read_table("portfolio_holding", lake_dir=tmp_path)
    assert {"w_pre_ca_pct", "ca_z", "ca_tilt_mult", "ca_tilt_delta"} <= set(df.columns)


def test_an_imputed_name_is_sized_on_the_prior_not_on_the_imputation(tmp_path):
    _seed(tmp_path, [
        {"sector_id": "hi", "rank": 1, "composite": 70, "catalyst_alignment": 90.0,
         "ca_imputed": False, "primary_etf": "AAA"},
        {"sector_id": "unstudied", "rank": 2, "composite": 70, "catalyst_alignment": 50.0,
         "ca_imputed": True, "primary_etf": "BBB"},
        {"sector_id": "lo", "rank": 3, "composite": 70, "catalyst_alignment": 10.0,
         "ca_imputed": False, "primary_etf": "CCC"},
    ])
    res = pf.build_model_holdings("test", profile=_PROFILE, lake_dir=tmp_path)
    by = {h["sector_id"]: h for h in res["holdings"]}
    assert by["unstudied"]["ca_tilt_mult"] == 1.0 and by["unstudied"]["ca_imputed"] is True


def test_momentum_and_equal_books_stay_clean_baselines(tmp_path):
    _seed(tmp_path, [
        {"sector_id": "a", "rank": 1, "composite": 70, "catalyst_alignment": 90.0,
         "primary_etf": "AAA"},
        {"sector_id": "b", "rank": 2, "composite": 70, "catalyst_alignment": 10.0,
         "primary_etf": "BBB"},
    ])
    prof = {**_PROFILE, "construction": {**_PROFILE["construction"], "weighting": "momentum"}}
    res = pf.build_model_holdings("test", profile=prof, lake_dir=tmp_path)
    assert res["ca_tilt"] is None
    assert all(h["w_pre_ca_pct"] is None for h in res["holdings"])


# ── the tally: ca_tilt_edge ──────────────────────────────────────────────────

def _seed_ledger(tmp_path, run_id, rows):
    base = {"portfolio_id": "catalyx", "run_id": run_id, "strategy": "s",
            "built_at": datetime.now(timezone.utc), "ca_tilt_delta": 0.20}
    lake.append_partition("portfolio_holding", pd.DataFrame([{**base, **r} for r in rows]),
                          {"portfolio_id": "catalyx", "run_id": run_id}, lake_dir=tmp_path)


def test_edge_prices_the_delta_and_localizes_per_driver(tmp_path, monkeypatch):
    # The tilt moved 5pp toward AAA (which then made +10%) and 5pp out of BBB (−5%):
    # edge = +0.05·10 + (−0.05)·(−5) = +0.75pp. The window is complete; the sign is positive.
    _seed_ledger(tmp_path, "run_20260101_090000", [
        {"sector_id": "a", "primary_etf": "AAA", "weight_pct": 25.0, "w_pre_ca_pct": 20.0},
        {"sector_id": "b", "primary_etf": "BBB", "weight_pct": 15.0, "w_pre_ca_pct": 20.0},
    ])
    monkeypatch.setattr(pf, "_sector_catalyst_map",
                        lambda: {"a": ["struct_x"], "b": ["struct_y"]})
    res = calibration.ca_tilt_edge(lake_dir=tmp_path, as_of="2026-06-01",
                                   fwd_fn=lambda t, s, e, **k: {"AAA": 0.10, "BBB": -0.05})
    assert res["n_windows"] == 1 and res["verdict"] == "paid"
    assert res["windows"][0]["edge_pp"] == pytest.approx(0.75)
    assert res["per_catalyst"]["struct_x"] == pytest.approx(0.5)
    assert res["per_catalyst"]["struct_y"] == pytest.approx(0.25)


def test_a_negative_tally_names_the_catalyst_never_the_off_switch(tmp_path, monkeypatch):
    _seed_ledger(tmp_path, "run_20260101_090000", [
        {"sector_id": "a", "primary_etf": "AAA", "weight_pct": 25.0, "w_pre_ca_pct": 20.0},
    ])
    monkeypatch.setattr(pf, "_sector_catalyst_map", lambda: {"a": ["struct_x"]})
    res = calibration.ca_tilt_edge(lake_dir=tmp_path, as_of="2026-06-01",
                                   fwd_fn=lambda t, s, e, **k: {"AAA": -0.10})
    assert res["verdict"] == "cost"
    assert "struct_x" in res["note"] and "never about disabling CA" in res["note"]
    assert "disab" not in res["verdict"]                    # the verdict is priced, not policed


def test_an_open_window_is_not_priced_yet(tmp_path):
    _seed_ledger(tmp_path, "run_20260101_090000", [
        {"sector_id": "a", "primary_etf": "AAA", "weight_pct": 25.0, "w_pre_ca_pct": 20.0},
    ])
    res = calibration.ca_tilt_edge(lake_dir=tmp_path, as_of="2026-01-15",
                                   fwd_fn=lambda t, s, e, **k: {"AAA": 0.10})
    assert res["n_windows"] == 0 and res["verdict"] == "unmeasured"


def test_no_ledger_reads_as_unmeasured_not_as_zero(tmp_path):
    res = calibration.ca_tilt_edge(lake_dir=tmp_path)
    assert res["verdict"] == "unmeasured" and res["mean_edge_pp"] is None


# ── the retro tally: pre-P8 books priced a posteriori ────────────────────────

def _seed_retro(tmp_path, run_id, holdings, snapshot):
    base_h = {"portfolio_id": "catalyx", "run_id": run_id, "strategy": "s",
              "built_at": datetime.now(timezone.utc)}          # NO w_pre_ca_pct: pre-P8 book
    lake.append_partition("portfolio_holding",
                          pd.DataFrame([{**base_h, **h} for h in holdings]),
                          {"portfolio_id": "catalyx", "run_id": run_id}, lake_dir=tmp_path)
    base_s = {"run_id": run_id, "snapshot_at": datetime.now(timezone.utc)}
    lake.append_partition("sector_snapshot",
                          pd.DataFrame([{**base_s, **s} for s in snapshot]),
                          {"run_id": run_id}, lake_dir=tmp_path)


def test_retro_prices_the_recorded_books_point_in_time(tmp_path, monkeypatch):
    # Equal recorded weights BELOW the cap, CA 90/50/10 as frozen by that run's snapshot:
    # today's δ moves weight from "lo" to "hi". hi then returns +10%, lo −10% → PAID.
    _seed_retro(tmp_path, "run_20260101_090000",
                [{"sector_id": "hi", "primary_etf": "AAA", "weight_pct": 15.0},
                 {"sector_id": "mid", "primary_etf": "CCC", "weight_pct": 15.0},
                 {"sector_id": "lo", "primary_etf": "BBB", "weight_pct": 15.0}],
                [{"sector_id": "hi", "catalyst_alignment": 90.0, "ca_imputed": False},
                 {"sector_id": "mid", "catalyst_alignment": 50.0, "ca_imputed": False},
                 {"sector_id": "lo", "catalyst_alignment": 10.0, "ca_imputed": False}])
    from catalyx.store import snapshot_repo as sr
    monkeypatch.setattr(sr, "_primary_etf",
                        lambda s: {"hi": "AAA", "mid": "CCC", "lo": "BBB"}.get(s))
    monkeypatch.setattr(pf, "_sector_catalyst_map", lambda: {"hi": ["struct_x"]})
    res = calibration.ca_tilt_edge_retro(
        lake_dir=tmp_path, as_of="2026-06-01",
        fwd_fn=lambda t, s, e, **k: {"AAA": 0.10, "CCC": 0.0, "BBB": -0.10})
    assert res["n_windows"] == 1 and res["verdict"] == "paid"
    w = res["windows"][0]
    assert w["edge_pp"] > 0
    # symmetric CAs on equal weights: what hi gains, lo cedes — gross preserved
    dws = {c["sector_id"]: c["delta_w_pp"] for c in w["contributions"]}
    assert dws["hi"] == pytest.approx(-dws["lo"], abs=0.05)


def test_retro_recap_never_manufactures_a_position_above_the_cap(tmp_path, monkeypatch):
    # "hi" is already AT the 20% ceiling and "lo" only wants to shrink: the cap blocks the
    # whole tilt, the counterfactual equals the recorded book, and there is NOTHING to price —
    # a cap-bound book reads as no window, never as a manufactured breach.
    _seed_retro(tmp_path, "run_20260101_090000",
                [{"sector_id": "hi", "primary_etf": "AAA", "weight_pct": 20.0},
                 {"sector_id": "lo", "primary_etf": "BBB", "weight_pct": 10.0}],
                [{"sector_id": "hi", "catalyst_alignment": 90.0, "ca_imputed": False},
                 {"sector_id": "lo", "catalyst_alignment": 10.0, "ca_imputed": False}])
    from catalyx.store import snapshot_repo as sr
    monkeypatch.setattr(sr, "_primary_etf", lambda s: {"hi": "AAA", "lo": "BBB"}.get(s))
    monkeypatch.setattr(pf, "_sector_catalyst_map", lambda: {})
    res = calibration.ca_tilt_edge_retro(
        lake_dir=tmp_path, as_of="2026-06-01",
        fwd_fn=lambda t, s, e, **k: {"AAA": 0.10, "BBB": 0.0})
    assert res["n_windows"] == 0 and res["verdict"] == "unmeasured"


def test_retro_skips_books_that_carry_the_live_ledger(tmp_path, monkeypatch):
    # A post-P8 book (w_pre_ca_pct present) is ca_tilt_edge's window; pricing it here too
    # would count the same window twice with two different baselines.
    _seed_ledger(tmp_path, "run_20260101_090000", [
        {"sector_id": "a", "primary_etf": "AAA", "weight_pct": 25.0, "w_pre_ca_pct": 20.0},
    ])
    res = calibration.ca_tilt_edge_retro(lake_dir=tmp_path, as_of="2026-06-01",
                                         fwd_fn=lambda t, s, e, **k: {"AAA": 0.10})
    assert res["n_windows"] == 0 and res["verdict"] == "unmeasured"


def test_retro_persists_one_row_per_run_rerunnable(tmp_path, monkeypatch):
    _seed_retro(tmp_path, "run_20260101_090000",
                [{"sector_id": "hi", "primary_etf": "AAA", "weight_pct": 15.0},
                 {"sector_id": "lo", "primary_etf": "BBB", "weight_pct": 15.0}],
                [{"sector_id": "hi", "catalyst_alignment": 90.0, "ca_imputed": False},
                 {"sector_id": "lo", "catalyst_alignment": 10.0, "ca_imputed": False}])
    from catalyx.store import snapshot_repo as sr
    monkeypatch.setattr(sr, "_primary_etf", lambda s: {"hi": "AAA", "lo": "BBB"}.get(s))
    monkeypatch.setattr(pf, "_sector_catalyst_map", lambda: {})
    for _ in range(2):                                     # re-run must overwrite, not stack
        calibration.ca_tilt_edge_retro(lake_dir=tmp_path, as_of="2026-06-01", persist=True,
                                       fwd_fn=lambda t, s, e, **k: {"AAA": 0.1, "BBB": 0.0})
    df = lake.read_table("ca_tilt_retro", lake_dir=tmp_path)
    assert len(df) == 1 and df.iloc[0]["run_id"] == "run_20260101_090000"
    assert float(df.iloc[0]["edge_pp"]) != 0.0


def test_dev_run_bursts_collapse_into_one_review_cycle(tmp_path, monkeypatch):
    # The history is development-shaped: three runs in one week are ONE decision, priced once —
    # at the cluster's latest reading — never three observations of the same window.
    for rid in ("run_20260101_090000", "run_20260103_090000", "run_20260105_090000"):
        _seed_retro(tmp_path, rid,
                    [{"sector_id": "hi", "primary_etf": "AAA", "weight_pct": 15.0},
                     {"sector_id": "lo", "primary_etf": "BBB", "weight_pct": 15.0}],
                    [{"sector_id": "hi", "catalyst_alignment": 90.0, "ca_imputed": False},
                     {"sector_id": "lo", "catalyst_alignment": 10.0, "ca_imputed": False}])
    from catalyx.store import snapshot_repo as sr
    monkeypatch.setattr(sr, "_primary_etf", lambda s: {"hi": "AAA", "lo": "BBB"}.get(s))
    monkeypatch.setattr(pf, "_sector_catalyst_map", lambda: {})
    res = calibration.ca_tilt_edge_retro(
        lake_dir=tmp_path, as_of="2026-06-01",
        fwd_fn=lambda t, s, e, **k: {"AAA": 0.10, "BBB": -0.10})
    assert res["n_windows"] == 1
    assert res["windows"][0]["run_id"] == "run_20260105_090000"


# ── the scale the z is taken on (v10 P8b) ────────────────────────────────────

def _logit(v):
    import math
    p = min(max(v / 100.0, 1e-6), 1 - 1e-6)
    return round(math.log(p / (1 - p)), 4)


def test_the_logit_scale_never_reorders_only_re_spaces():
    """The whole safety argument: logit is MONOTONE. It may change how far apart the
    multipliers sit, never which name gets the larger one."""
    ca = [96.3, 96.0, 95.5, 95.0, 76.0]
    lg = [_logit(v) for v in ca]
    imp = [False] * 5
    _, lvl = pf.ca_tilt([1.0] * 5, ca, imp, delta=0.2, scale="level")
    _, log = pf.ca_tilt([1.0] * 5, ca, imp, delta=0.2, ca_logits=lg, scale="logit")
    order_lvl = sorted(range(5), key=lambda i: -lvl[i]["mult"])
    order_log = sorted(range(5), key=lambda i: -log[i]["mult"])
    assert order_lvl == order_log == [0, 1, 2, 3, 4]


def test_the_logit_buys_resolution_exactly_where_the_level_saturates():
    """The measured motivation: the aggregator's ceiling packs the names the book actually
    holds into a span the level scale can no longer separate."""
    ca = [96.3, 96.0, 95.5, 95.0, 76.0]          # four in the ceiling band + one outlier
    lg = [_logit(v) for v in ca]
    _, lvl = pf.ca_tilt([1.0] * 5, ca, [False] * 5, delta=0.2, scale="level")
    _, log = pf.ca_tilt([1.0] * 5, ca, [False] * 5, delta=0.2, ca_logits=lg, scale="logit")
    span_lvl = max(r["z"] for r in lvl[:4]) - min(r["z"] for r in lvl[:4])
    span_log = max(r["z"] for r in log[:4]) - min(r["z"] for r in log[:4])
    assert span_log > span_lvl * 1.5, "the ceiling band must separate materially better"


def test_a_row_without_a_logit_falls_back_to_the_level_for_the_whole_cross_section():
    """A z needs ONE scale. Mid-migration (some rows carry a logit, some do not) the whole
    cross-section standardizes on the level — a mixed z compares units that are not
    comparable — and the fallback is stated, never silent."""
    ca = [90.0, 50.0, 10.0]
    lg = [_logit(90.0), None, _logit(10.0)]
    _, rows = pf.ca_tilt([1.0] * 3, ca, [False] * 3, delta=0.2, ca_logits=lg, scale="logit")
    assert {r["scale"] for r in rows} == {"level"}
    assert [r["x"] for r in rows] == ca


def test_scale_level_reproduces_the_pre_p8b_sizing_exactly():
    ca = [90.0, 50.0, 10.0]
    lg = [_logit(v) for v in ca]
    a, _ = pf.ca_tilt([1.0] * 3, ca, [False] * 3, delta=0.2, scale="level")
    b, _ = pf.ca_tilt([1.0] * 3, ca, [False] * 3, delta=0.2, ca_logits=lg, scale="level")
    assert a == b


def test_the_build_reports_which_scale_it_actually_used(tmp_path):
    """Never assume the request was honoured: a run whose snapshot predates the logit column
    falls back, and the output has to say so rather than leave it to be inferred."""
    _seed(tmp_path, [
        {"sector_id": "a", "rank": 1, "composite": 70, "catalyst_alignment": 90.0,
         "primary_etf": "AAA"},
        {"sector_id": "b", "rank": 2, "composite": 70, "catalyst_alignment": 10.0,
         "primary_etf": "BBB"},
    ])
    prof = {**_PROFILE, "construction": {**_PROFILE["construction"],
                                         "ca_tilt_scale": "logit"}}
    res = pf.build_model_holdings("test", profile=prof, lake_dir=tmp_path)
    assert res["ca_tilt"]["scale_requested"] == "logit"
    assert res["ca_tilt"]["scale_used"] == "level"          # no ca_logit in this snapshot
    assert res["ca_tilt"]["scale_fallback"] is True
    assert "fell back" in res["ca_tilt"]["note"]
