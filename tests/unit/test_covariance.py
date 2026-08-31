"""v6 I1 — Ledoit–Wolf covariance on weekly returns.

The estimator's properties are testable without market data, which is the point: δ is estimated
from the sample, not chosen, so it must respond to the sample in the way the derivation says.
"""
from __future__ import annotations

import numpy as np
import pytest

from catalyx.scorer import covariance as cv


def _draw(corr, T, seed, sd=0.02):
    rng = np.random.default_rng(seed)
    L = np.linalg.cholesky(np.asarray(corr) + 1e-10 * np.eye(len(corr)))
    return (rng.standard_normal((T, len(corr))) @ L.T) * sd


def _const_corr(n, rho):
    c = np.full((n, n), rho)
    np.fill_diagonal(c, 1.0)
    return c


def _blocks(n, rho):
    c = np.eye(n)
    h = n // 2
    c[:h, :h] = rho
    c[h:, h:] = rho
    np.fill_diagonal(c, 1.0)
    return c


# ── The estimator ────────────────────────────────────────────────────────────

def test_shrinkage_is_full_when_the_target_is_the_truth():
    """Constant correlation IS the target, so the misspecification γ is small and δ goes high —
    the estimator is supposed to notice it is right."""
    r = cv.ledoit_wolf(_draw(_const_corr(10, 0.4), T=60, seed=7))
    assert r["shrinkage"] > 0.8


def test_shrinkage_backs_off_when_the_target_is_wrong():
    """Block structure cannot be represented by one constant ρ, so shrinking toward it would
    destroy real structure. δ must collapse."""
    r = cv.ledoit_wolf(_draw(_blocks(10, 0.85), T=60, seed=7))
    assert r["shrinkage"] < 0.25


def test_shrinkage_vanishes_as_observations_accumulate():
    """δ ∝ 1/T: with enough data the sample covariance is reliable and needs no help."""
    d = [cv.ledoit_wolf(_draw(_blocks(10, 0.85), T=t, seed=11))["shrinkage"]
         for t in (60, 250, 2000)]
    assert d[0] > d[1] > d[2]
    assert d[2] < 0.01


def test_the_result_is_a_valid_covariance_matrix():
    s = cv.ledoit_wolf(_draw(_blocks(10, 0.85), T=60, seed=3))["sigma"]
    assert np.allclose(s, s.T)
    assert np.linalg.eigvalsh(s).min() > 0, "a shrunk matrix must be positive definite"


def test_shrinkage_stays_inside_its_bounds_on_pathological_input():
    for seed in range(5):
        r = cv.ledoit_wolf(_draw(_const_corr(12, 0.95), T=14, seed=seed))   # T barely over N
        assert 0.0 <= r["shrinkage"] <= 1.0
        assert np.linalg.eigvalsh(r["sigma"]).min() > 0


def test_too_few_observations_raises_rather_than_returning_a_number():
    with pytest.raises(ValueError):
        cv.ledoit_wolf(np.zeros((1, 5)))


# ── Weekly resampling (the Epps fix) ─────────────────────────────────────────

def test_weekly_resampling_collapses_daily_bars_to_friday_returns():
    import pandas as pd

    idx = pd.bdate_range("2026-01-05", periods=20)
    frame = pd.DataFrame({"A": np.linspace(100, 110, 20)}, index=idx)
    weekly = cv.to_weekly(frame)
    assert len(weekly) == 3          # 4 Fridays → 3 returns
    assert (weekly["A"] > 0).all()


def test_an_empty_frame_returns_empty_rather_than_raising():
    import pandas as pd

    assert cv.to_weekly(pd.DataFrame()).empty


# ── Risk decomposition ───────────────────────────────────────────────────────

def test_position_risk_contributions_sum_to_one_hundred():
    """The Euler decomposition is what makes 'this name carries 40% of the risk on 20% of the
    money' a sentence with a defined meaning."""
    sigma = np.diag([0.04, 0.01, 0.0025]) + 0.002
    rows = cv.risk_contributions(sigma, [50.0, 30.0, 20.0])
    assert sum(r["ctr_pct"] for r in rows) == pytest.approx(100.0, abs=0.05)


def test_the_riskier_vehicle_carries_more_than_its_weight():
    sigma = np.diag([0.09, 0.0025])
    rows = cv.risk_contributions(sigma, [50.0, 50.0])
    assert rows[0]["ctr_pct"] > rows[0]["weight_pct"] > rows[1]["ctr_pct"]


def test_a_book_of_perfectly_correlated_names_is_no_safer_than_one_name():
    sigma = np.full((4, 4), 0.04)
    assert cv.portfolio_vol(sigma, [25.0] * 4) == pytest.approx(
        cv.portfolio_vol(sigma, [100.0, 0.0, 0.0, 0.0]), abs=0.01)


def test_cash_lowers_the_book_vol_because_weights_are_of_the_whole_book():
    sigma = np.diag([0.04, 0.04])
    assert cv.portfolio_vol(sigma, [25.0, 25.0]) < cv.portfolio_vol(sigma, [50.0, 50.0])


def test_overlapping_clusters_sum_to_more_than_the_book_on_purpose():
    """Same rule as `exposure_eur` since v5.2: a position with two drivers is wholly in both,
    because the question is how much moves if a driver breaks, not who gets the credit."""
    sigma = np.diag([0.04, 0.04, 0.04])
    w = [40.0, 30.0, 30.0]
    a = cv.cluster_risk(sigma, w, [0, 1])
    b = cv.cluster_risk(sigma, w, [0, 2])
    assert a["ctr_pct"] + b["ctr_pct"] > 100.0
    assert a["notional_pct"] == pytest.approx(70.0)


def test_disjoint_clusters_partition_the_risk():
    sigma = np.diag([0.04, 0.01, 0.0025]) + 0.001
    w = [40.0, 30.0, 30.0]
    total = sum(cv.cluster_risk(sigma, w, m)["ctr_pct"] for m in ([0], [1], [2]))
    assert total == pytest.approx(100.0, abs=0.05)


def test_a_cluster_standalone_vol_ignores_the_rest_of_the_book():
    sigma = np.diag([0.04, 100.0])
    w = [50.0, 50.0]
    assert cv.cluster_risk(sigma, w, [0])["standalone_vol_pct"] == pytest.approx(
        cv.portfolio_vol(sigma, [50.0, 0.0]), abs=0.01)


def test_an_unbuildable_covariance_reports_nothing_rather_than_zero_risk():
    """A missing risk column must read as 'not measured'. Returning 0 would say 'no risk'."""
    assert cv.cluster_risk_for({"s1": ("A", 100.0)}, {"c": ["s1"]}) is None
    assert cv.cluster_risk_for({}, {}) is None


# ── v6 I5 / I3: the assumptions behind the sizing constants ──────────────────

def test_the_vol_tilt_assumption_matches_the_declared_alpha():
    """α is a claim about what the score MEANS, not a dial: score∝μ ⇒ α=2, score∝Sharpe ⇒ α=1.
    The composite is a cross-sectional percentile — an ordinal rank, Sharpe-like — so α=1."""
    from catalyx.config import weights

    assert weights.portfolio_weighting()["vol_tilt_alpha"] == 1.0


def test_the_tier_loss_budget_is_what_the_ceilings_and_the_stop_imply():
    """v6 I5: a tier ceiling paired with the protective exit floor IS a per-line loss budget.
    It is a consequence of n_target, not an independent choice — this pins the arithmetic so a
    change to either constant surfaces as a change to the budget."""
    from catalyx.config import weights

    stop = abs(weights.exit_signals()["drawdown_exit_pct"]) / 100.0
    tiers = weights.conviction_tiers()
    budget = {t: round(pct * stop, 2) for t, pct in tiers.items()}
    assert budget == {1: 6.0, 2: 4.2, 3: 2.1}
    assert budget[1] < 10.0, "a single line able to cost 10% of the book is not fractional Kelly"
