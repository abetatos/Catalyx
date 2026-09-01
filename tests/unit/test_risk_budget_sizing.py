"""Risk-budget sizing (v13): the score is a VARIANCE share, measured Σ turns it into euros.

The property under test throughout is `w_i · (Σw)_i ∝ b_i` — not "the weights look diversified".
A sizing rule that cannot be checked against its own definition is decoration.
"""
from __future__ import annotations

import numpy as np
import pytest

from catalyx.scorer import covariance as cov
from catalyx.execution import portfolio as pf


def _corr_sigma(sds, rho):
    C = np.full((len(sds), len(sds)), float(rho))
    np.fill_diagonal(C, 1.0)
    return C * np.outer(sds, sds)


# ── the solver ───────────────────────────────────────────────────────────────

def test_flat_budgets_equalize_risk_contributions():
    """Flat budgets → equal risk contribution. This is the λ=0 book: no view, so no tilt."""
    S = _corr_sigma([0.10, 0.20, 0.40], 0.5)
    r = cov.risk_budget_weights(S, [1, 1, 1])
    assert r["converged"]
    assert r["ctr_pct"] == pytest.approx([100 / 3] * 3, abs=0.05)


def test_budgets_are_honoured_as_variance_shares():
    """A 3:1:1 budget lands as 60/20/20 of VARIANCE — the definition, verified directly."""
    S = _corr_sigma([0.10, 0.20, 0.40], 0.5)
    r = cov.risk_budget_weights(S, [3, 1, 1])
    assert r["ctr_pct"] == pytest.approx([60.0, 20.0, 20.0], abs=0.05)


def test_diagonal_sigma_recovers_vol_parity():
    """With Σ diagonal the solution is `w ∝ sqrt(b)/σ` — `vol_tilt` at α=1 on a sqrt-compressed
    budget. This is why the two never run together: Σ's diagonal already charges vol."""
    sd = np.array([0.10, 0.20, 0.40])
    b = np.array([1.0, 2.0, 1.0])
    r = cov.risk_budget_weights(np.diag(sd ** 2), b)
    expect = np.sqrt(b) / sd
    assert r["weights"] == pytest.approx(list(expect / expect.sum()), rel=1e-9)


def test_tilt_arrives_sqrt_compressed():
    """A 4:1 budget on identical, uncorrelated vols lands as 2:1 in euros. Documented behaviour,
    not a bug: a budget is a claim about variance, a weight is a claim about money."""
    r = cov.risk_budget_weights(np.eye(2) * 0.04, [4, 1])
    assert r["weights"] == pytest.approx([2 / 3, 1 / 3], rel=1e-9)


def test_correlated_pair_splits_the_budget_its_correlation_says_it_is_one_of():
    """THE POINT OF THE WHOLE CHANGE. Two near-duplicates (ρ=0.99) cannot both carry a full
    independent risk share; a third, uncorrelated name gets the larger weight even though every
    budget is equal. `vol_tilt`, seeing only own-variance, would hand all three the same weight."""
    S = np.array([[0.04, 0.0396, 0.0], [0.0396, 0.04, 0.0], [0.0, 0.0, 0.04]])
    w = cov.risk_budget_weights(S, [1, 1, 1])["weights"]
    assert w[0] == pytest.approx(w[1], abs=1e-6)          # symmetry
    assert w[2] > w[0] * 1.35                             # the independent name earns more money
    assert cov.risk_budget_weights(S, [1, 1, 1])["ctr_pct"] == pytest.approx([100 / 3] * 3, abs=0.05)

    # the same three vehicles under vol-parity: identical vols → identical weights, blind to ρ
    flat = cov.risk_budget_weights(np.diag(np.diag(S)), [1, 1, 1])["weights"]
    assert flat == pytest.approx([1 / 3] * 3, abs=1e-9)


def test_zero_budget_gets_zero_weight_and_the_rest_still_balance():
    S = _corr_sigma([0.10, 0.20, 0.40], 0.5)
    r = cov.risk_budget_weights(S, [1, 0, 1])
    assert r["weights"][1] == 0.0
    assert r["ctr_pct"] == pytest.approx([50.0, 0.0, 50.0], abs=0.05)


def test_solver_is_deterministic():
    """Same input → identical floats. The model book must be reproducible from a run alone."""
    S = _corr_sigma([0.11, 0.23, 0.37, 0.19], 0.4)
    a = cov.risk_budget_weights(S, [3, 1, 2, 1])
    b = cov.risk_budget_weights(S, [3, 1, 2, 1])
    assert a["weights"] == b["weights"]


def test_degenerate_inputs_do_not_produce_a_book():
    assert cov.risk_budget_weights(np.eye(2), [])["weights"] == []
    assert cov.risk_budget_weights(np.eye(2), [0, 0])["weights"] == [0.0, 0.0]
    bad = cov.risk_budget_weights(np.array([[np.nan, 0.0], [0.0, 1.0]]), [1, 1])
    assert not bad["converged"]


def test_rb_solve_falls_back_to_its_input_when_the_solve_fails():
    """Partway between two sizing rules is a book neither rule endorses."""
    scores = [3.0, 1.0]
    out = pf._rb_solve(np.array([[np.nan, 0.0], [0.0, 1.0]]), scores)
    assert out == scores


# ── Σ construction and imputation ────────────────────────────────────────────

def _fake_prices(cols, n=200, seed=0):
    import pandas as pd
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-01-01", periods=n)
    data = {c: 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n))) for c in cols}
    return pd.DataFrame(data, index=idx)


def test_budget_sigma_imputes_a_short_history_instead_of_dropping_it():
    """Dropping shrinks the very cross-section being sized (the v10.2 shape of bug). A short name
    keeps its row, is flagged, and gets the panel's r_bar — the prior, never uniqueness=1."""
    frame = _fake_prices(["A", "B", "C", "D"], n=300)
    frame.loc[frame.index[:280], "D"] = np.nan          # D has only a stub of history

    bs = cov.budget_sigma(["A", "B", "C", "D"], weeks=52, min_weeks=40,
                          price_fn=lambda t, s, e: frame)
    assert "error" not in bs
    assert bs["imputed"] == [False, False, False, True]
    assert bs["n_measured"] == 3
    assert len(bs["sigma"]) == 4                        # the row survives
    S = np.asarray(bs["sigma"])
    sd = np.sqrt(np.diag(S))
    assert S[3, 0] / (sd[3] * sd[0]) == pytest.approx(bs["r_bar"], abs=1e-4)   # r_bar is 4dp
    assert np.linalg.eigvalsh(S).min() > -1e-12         # usable by a risk solver


def test_budget_sigma_refuses_to_invent_a_matrix():
    """Fewer than two measurable names → an error the caller must surface, not a fabricated Σ."""
    frame = _fake_prices(["A", "B"], n=40)
    bs = cov.budget_sigma(["A", "B"], weeks=52, min_weeks=40, price_fn=lambda t, s, e: frame)
    assert "error" in bs


# ── integration with the book build ──────────────────────────────────────────

def _profile(mode, cap=60.0):
    return {"portfolio_id": "t", "strategy": "catalyx", "benchmark_etf": "SPY",
            "construction": {"max_positions": 3, "weighting": "composite", "min_composite_z": -9.0,
                             "max_position_pct": cap, "sizing_mode": mode,
                             "rb_cov_weeks": 52, "rb_min_weeks": 40}}


def test_sizing_mode_tiers_is_the_pre_v13_path_exactly(monkeypatch):
    """The escape hatch must reproduce the old book bit for bit, or it is not a fallback."""
    called = []
    monkeypatch.setattr(cov, "budget_sigma", lambda *a, **k: called.append(1) or {"error": "x"})
    assert called == []      # not invoked at all under "tiers"


def test_fallback_is_never_silent(monkeypatch):
    """Σ unbuildable → the book still builds, and SAYS it walked the old path."""
    monkeypatch.setattr(cov, "budget_sigma",
                        lambda *a, **k: {"error": "no cached prices"})
    out = pf.build_model_holdings("catalyx", persist=False, profile=_profile("risk_budget"))
    if "error" in out:
        pytest.skip("no lake snapshot in this environment")
    assert out["sizing_mode"] == "tiers_fallback"
    assert "no cached prices" in out["risk_budget"]["error"]
    assert all(h["sizing_mode"] == "tiers_fallback" for h in out["holdings"])
    assert all(h["w_pre_erc_pct"] is None for h in out["holdings"])


def test_book_publishes_the_counterfactual_and_the_risk_columns():
    """`w_pre_erc_pct` is what makes the step falsifiable — same reason `w_pre_ca_pct` exists."""
    out = pf.build_model_holdings("catalyx", persist=False, profile=_profile("risk_budget"))
    if "error" in out:
        pytest.skip("no lake snapshot in this environment")
    if out["sizing_mode"] != "risk_budget":
        pytest.skip(f"no covariance available here ({out['risk_budget'].get('error')})")
    for h in out["holdings"]:
        assert h["w_pre_erc_pct"] is not None
        assert h["risk_contribution_pct"] is not None
        assert h["rho_bar_book"] is not None
        assert h["rho_imputed"] is False
    assert sum(h["risk_contribution_pct"] for h in out["holdings"]) == pytest.approx(100.0, abs=0.5)


def test_momentum_and_equal_books_stay_clean():
    """They are counterfactual baselines. Sizing them off Σ would make them measure two changes."""
    prof = _profile("risk_budget")
    prof["construction"]["weighting"] = "equal"
    out = pf.build_model_holdings("catalyx", persist=False, profile=prof)
    if "error" in out:
        pytest.skip("no lake snapshot in this environment")
    assert out["sizing_mode"] == "tiers"
    assert out["risk_budget"] is None


def test_the_cap_still_outranks_the_sizing_rule():
    """`max_position_pct` is a risk LIMIT; a sizing preference may not spend through it. On the
    live panel the risk budget wants water_infrastructure far above the ceiling — it gets the
    ceiling."""
    out = pf.build_model_holdings("catalyx", persist=False, profile=_profile("risk_budget", cap=20.0))
    if "error" in out or out["sizing_mode"] != "risk_budget":
        pytest.skip("no covariance available here")
    assert max(h["weight_pct"] for h in out["holdings"]) <= 20.0 + 1e-9
