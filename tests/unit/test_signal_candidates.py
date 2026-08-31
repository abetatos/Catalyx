"""v7 Fase M — candidate signal columns: weight 0, measured, never gating.

The guarantee under test: every candidate is a COLUMN (momentum_12_1, near_52w_high,
ca_unpriced, flow_resid, inst_sponsorship) that calibration can measure, and none of them
carries a weight in the composite. Promotion to a weight is a human config edit (PLAN_v7 §5).

Run: uv run pytest tests/unit/test_signal_candidates.py -q
"""
from __future__ import annotations

import json

import pytest
import yaml

from catalyx.config import weights
from catalyx.scorer import catalyst_scorer as cs
from catalyx.scorer import momentum_engine as me
from catalyx.scorer.sector_scorer import commensurate


# ── M2: momentum 12-1 ─────────────────────────────────────────────────────────

def test_12_1_skips_the_reversal_month():
    # +30% over 12m of which +10% came in the last month → 12-1 ≈ +18.2%
    r = me._raw_12_1({"return_1y_pct": 30.0, "return_1m_pct": 10.0})
    assert r == pytest.approx((1.30 / 1.10 - 1) * 100, abs=1e-6)


def test_12_1_is_none_without_a_full_year():
    assert me._raw_12_1({"return_1y_pct": None, "return_1m_pct": 5.0}) is None
    assert me._raw_12_1({"return_1y_pct": 20.0, "return_1m_pct": None}) is None


def test_a_flat_last_month_leaves_12_1_equal_to_1y():
    assert me._raw_12_1({"return_1y_pct": 25.0, "return_1m_pct": 0.0}) == pytest.approx(25.0)


# ── M4: ca_unpriced ───────────────────────────────────────────────────────────

def test_priced_in_structural_maps_maturity_to_the_stepped_scale():
    m = weights.maturity_priced_in()
    assert cs._priced_in_structural({"narrative_maturity": "ignored"}) == (m["ignored"], False)
    assert cs._priced_in_structural({"narrative_maturity": "exhausted"}) == (m["exhausted"], False)


def test_priced_in_missing_is_imputed_to_the_prior_never_the_worst_case():
    p, imputed = cs._priced_in_structural({})
    assert p == weights.PRICED_IN_PRIOR and imputed
    p, imputed = cs._priced_in_event({})
    assert p == weights.PRICED_IN_PRIOR and imputed


def _write_structural(tmp_path, sid, intensity, maturity=None):
    doc = {"id": f"struct_{sid}", "intensity": {"current_score": intensity}, "indicators": []}
    if maturity:
        doc["narrative_maturity"] = maturity
    (tmp_path / f"{sid}.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")


@pytest.fixture()
def scorer_dirs(tmp_path, monkeypatch):
    struct_dir = tmp_path / "structural"
    cat_dir = tmp_path / "catalysts"
    study_dir = tmp_path / "studies"
    for d in (struct_dir, cat_dir, study_dir):
        d.mkdir()
    monkeypatch.setattr(cs, "_STRUCTURAL_DIR", struct_dir)
    monkeypatch.setattr(cs, "_CATALYST_DIR", cat_dir)
    monkeypatch.setattr(cs, "_STUDY_DIR", study_dir)
    return struct_dir, cat_dir, study_dir


def test_an_exhausted_catalyst_scores_near_zero_unpriced(scorer_dirs):
    struct_dir, _, _ = scorer_dirs
    _write_structural(struct_dir, "spent", 90.0, maturity="exhausted")
    r = cs.compute_catalyst_alignment("x", structural_ids=["struct_spent"])
    assert r["catalyst_alignment"] == 90.0
    assert r["ca_unpriced"] == 0.0
    assert r["ca_unpriced_imputed_n"] == 0


def test_an_ignored_catalyst_keeps_its_full_score_unpriced(scorer_dirs):
    struct_dir, _, _ = scorer_dirs
    _write_structural(struct_dir, "fresh", 70.0, maturity="ignored")
    r = cs.compute_catalyst_alignment("x", structural_ids=["struct_fresh"])
    assert r["ca_unpriced"] == 70.0


def test_unpriced_reorders_level_when_discount_differs(scorer_dirs):
    struct_dir, _, _ = scorer_dirs
    _write_structural(struct_dir, "loud", 90.0, maturity="crowded")    # 90 × 0.25 = 22.5
    _write_structural(struct_dir, "quiet", 70.0, maturity="emerging")  # 70 × 0.75 = 52.5
    a = cs.compute_catalyst_alignment("x", structural_ids=["struct_loud"])
    b = cs.compute_catalyst_alignment("y", structural_ids=["struct_quiet"])
    assert a["catalyst_alignment"] > b["catalyst_alignment"]
    assert a["ca_unpriced"] < b["ca_unpriced"]


def test_missing_maturity_is_counted_as_imputed(scorer_dirs):
    struct_dir, _, _ = scorer_dirs
    _write_structural(struct_dir, "bare", 80.0)
    r = cs.compute_catalyst_alignment("x", structural_ids=["struct_bare"])
    assert r["ca_unpriced"] == pytest.approx(80.0 * (1 - weights.PRICED_IN_PRIOR))
    assert r["ca_unpriced_imputed_n"] == 1


# ── M5: flow_resid ────────────────────────────────────────────────────────────

def _row(sid, ca, mom, flow, crowd, flow_imputed=False):
    return {"sector_id": sid, "composite": 0.0, "flow_imputed": flow_imputed,
            "score_breakdown": {"catalyst_alignment": ca, "momentum": mom,
                                "flow_confirmation": flow, "crowding_risk": crowd}}


def test_flow_resid_is_zero_when_flow_is_pure_momentum_echo():
    rows = [_row(f"s{i}", 50 + i, 10 * i, 20 + 8 * i, 50) for i in range(10)]
    commensurate(rows)
    for r in rows:
        assert r["flow_resid"] == pytest.approx(0.0, abs=0.01)


def test_flow_resid_isolates_the_part_momentum_does_not_explain():
    rows = [_row(f"s{i}", 50, 10 * i, 20 + 8 * i, 50) for i in range(10)]
    rows[3]["score_breakdown"]["flow_confirmation"] += 30.0   # idiosyncratic inflow
    commensurate(rows)
    top = max(rows, key=lambda r: r["flow_resid"])
    assert top["sector_id"] == "s3"


def test_flow_resid_stays_none_on_imputed_rows():
    rows = [_row(f"s{i}", 50 + i, 10 * i, 20 + 8 * i, 50) for i in range(10)]
    rows.append(_row("blind", 50, 50, 50, 50, flow_imputed=True))
    commensurate(rows)
    assert next(r for r in rows if r["sector_id"] == "blind").get("flow_resid") is None


# ── M1: cross-dimension correlation diagnostic ────────────────────────────────

def test_correlation_matrix_and_n_eff_are_published():
    rows = [_row(f"s{i}", 50 + i, 10 * i, 20 + 8 * i, 55 - i) for i in range(10)]
    scale = commensurate(rows)
    pairs = {(p["a"], p["b"]): p["rho"] for p in scale["dimension_correlation"]}
    assert pairs[("momentum", "flow_confirmation")] == pytest.approx(1.0)
    assert scale["n_eff_dimensions"] == pytest.approx(1.0, abs=0.05)


def test_correlated_pairs_are_linted_separately_from_dead_dimensions():
    rows = [_row(f"s{i}", 50 + i, 10 * i, 20 + 8 * i, 55 - i) for i in range(10)]
    scale = commensurate(rows)
    assert scale["correlation_lint"]
    assert scale["lint"] == []          # dead-dimension lint keeps its meaning


def test_independent_dimensions_raise_n_eff():
    vals = [(3, 1, 4, 1), (5, 9, 2, 6), (8, 9, 7, 9), (3, 2, 3, 8), (4, 6, 2, 6),
            (9, 7, 9, 3), (2, 3, 8, 4), (6, 2, 6, 4), (3, 3, 8, 3), (2, 7, 9, 5)]
    rows = [_row(f"s{i}", 40 + a * 5, b * 10, 30 + c * 6, 20 + d * 7)
            for i, (a, b, c, d) in enumerate(vals)]
    scale = commensurate(rows)
    assert scale["n_eff_dimensions"] > 2.0


# ── 13F falls back to the US sibling (post-universe-v2.0 every primary is UCITS) ──

def test_inst_sponsorship_walks_the_chain_to_a_us_sibling(monkeypatch):
    from catalyx.data import flow_data as fd

    def fake_inst(ticker):
        if ticker == "USETF":
            return {"inst_sponsorship_score": 55.0, "inst_13f_filer_count": 900,
                    "inst_source": "edgar_13f"}
        return {"inst_sponsorship_score": None, "inst_source": "not_available_ucits"}

    monkeypatch.setattr(fd, "_fetch_institutional_ownership", fake_inst)
    monkeypatch.setattr(fd, "_resolve_flow_signal", lambda chain, health: (
        chain[0], {"flow_pct": 1.0, "shares_source": "test", "flow_window_days": 7,
                   "flow_days_covered": 7, "implied_aum_m_usd": 100.0}, "computed"))
    snap = fd.fetch_flow_data({"some_sector": ["FAKE.L", "USETF"]})
    s = snap["sector_scores"]["some_sector"]
    assert s["inst_sponsorship_score"] == 55.0
    assert s["inst_proxy_ticker"] == "USETF"


# ── N1: comomentum ────────────────────────────────────────────────────────────

def test_siblings_share_a_catalyst_or_are_alone():
    from catalyx.scorer.comomentum import siblings_map

    cats = {"a": {"c1", "c2"}, "b": {"c2"}, "c": {"c3"}}
    m = siblings_map(cats)
    assert m["a"] == ["b"] and m["b"] == ["a"] and m["c"] == []


def test_residuals_are_orthogonal_to_the_benchmark():
    import numpy as np
    import pandas as pd
    from catalyx.scorer.comomentum import residual_returns

    rng = np.random.default_rng(7)
    idx = pd.date_range("2025-01-03", periods=60, freq="W-FRI")
    spy = pd.Series(rng.normal(0, 0.02, 60), index=idx)
    a = 1.3 * spy + rng.normal(0, 0.01, 60)
    weekly = pd.DataFrame({"A": a, "SPY": spy})
    resid = residual_returns(weekly)
    assert abs(resid["A"].corr(spy)) < 0.05


def test_comomentum_reads_shared_crowding_and_says_none_without_siblings(monkeypatch):
    import numpy as np
    import pandas as pd
    from catalyx.scorer import comomentum as cm

    rng = np.random.default_rng(11)
    idx = pd.date_range("2024-06-07", periods=80, freq="W-FRI")
    spy = pd.Series(rng.normal(0.001, 0.02, 80), index=idx)
    theme = pd.Series(rng.normal(0, 0.03, 80), index=idx)     # shared crowded factor
    px = (1 + pd.DataFrame({
        "T1": spy + theme + rng.normal(0, 0.005, 80),
        "T2": spy + theme + rng.normal(0, 0.005, 80),
        "T3": spy + rng.normal(0, 0.03, 80),
        "SPY": spy,
    })).cumprod()

    monkeypatch.setattr(cm, "sector_catalysts",
                        lambda: {"s1": {"cX"}, "s2": {"cX"}, "s3": {"cY"}})
    import catalyx.store.snapshot_repo as sr
    monkeypatch.setattr(sr, "primary_etf", {"s1": "T1", "s2": "T2", "s3": "T3"}.get)

    r = cm.compute(weeks=52, as_of="2026-01-02", price_fn=lambda t, s, e: px)
    assert r["sectors"]["s1"]["crowding_comomentum"] > 0.5
    assert r["sectors"]["s3"]["crowding_comomentum"] is None


# ── N2: COT ───────────────────────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


class _FakeClient:
    def __init__(self, payload):
        self._p = payload

    def get(self, url, params=None, timeout=None):
        return _FakeResp(self._p)


def test_cot_scores_the_latest_reading_against_its_own_history():
    from catalyx.data import cot_data as cd

    rows = [{"report_date_as_yyyy_mm_dd": f"2026-{m:02d}-01", "open_interest_all": "1000",
             "noncomm_positions_long_all": str(500 + i), "noncomm_positions_short_all": "400"}
            for i, m in enumerate([8] + [7] * 60, 0) for _ in [0]]
    # newest first, newest has the highest net → percentile ≈ 100
    rows[0]["noncomm_positions_long_all"] = "700"
    snap = cd.compute(client=_FakeClient(rows))
    for label, m in snap["markets"].items():
        assert m["cot_crowding"] > 95
    assert snap["sector_scores"]["gold_physical"]["cot_crowding"] > 95
    assert "uranium_miners" not in snap["sector_scores"]


def test_cot_snapshot_freshness_guard(tmp_path, monkeypatch):
    from catalyx.data import cot_data as cd

    monkeypatch.setattr(cd, "_SNAPSHOTS_DIR", tmp_path)
    (tmp_path / "cot_snapshot_20260101.json").write_text(
        json.dumps({"date": "2026-01-01", "sector_scores": {}}), encoding="utf-8")
    assert cd.load_latest(max_age_days=14) is None    # stale positioning is not positioning


# ── N3: Google Trends ─────────────────────────────────────────────────────────

def test_trends_scores_recent_attention_vs_history():
    from catalyx.data.trends_data import score_series

    flat = [50.0] * 100
    spike = [10.0] * 96 + [90.0, 92.0, 95.0, 91.0]
    assert score_series(spike) > 90
    assert score_series(flat) == pytest.approx(50.0, abs=2)
    assert score_series([1.0] * 10) is None           # below 1y of history


# ── M6 + the freeze: candidates are measured, and weigh nothing ───────────────

def test_calibration_registers_every_candidate_column():
    from catalyx.scorer import calibration as cal

    for dim in ("momentum_12_1", "near_52w_high", "ca_unpriced", "flow_resid",
                "inst_sponsorship", "crowding_comomentum", "cot_crowding",
                "trends_crowding", "crowding_measured"):
        assert dim in cal.DIMENSIONS
        assert dim in cal.CANDIDATE_DIMENSIONS
    for dim in ("crowding_comomentum", "cot_crowding", "trends_crowding", "crowding_measured"):
        assert cal.CANDIDATE_DIMENSIONS[dim] is True   # crowding is a penalty


def test_no_candidate_carries_a_composite_weight():
    """PLAN_v7 §5: promotion to a weight is a deliberate config edit, never a side effect.
    This test fails the suite the day a candidate silently appears in composite_weights."""
    from catalyx.scorer import calibration as cal

    cw = set(weights.composite_weights())
    assert cw == {"catalyst_alignment", "momentum", "flow_confirmation", "crowding_risk"}
    assert not (set(cal.CANDIDATE_DIMENSIONS) & cw)


def test_candidate_rows_are_flagged_in_the_calibration_output():
    from catalyx.scorer import calibration as cal

    assert "composite" not in cal.CANDIDATE_DIMENSIONS
    assert cal.DIMENSIONS["crowding_risk"] is True     # inversion survives the merge
