"""Dislocation engine — contagion-vs-idiosyncratic decomposition + beta (pure math)."""
import pandas as pd

from catalyx.scorer import dislocation as dl


def test_beta_recovers_slope():
    mkt = pd.Series([0.01, -0.02, 0.03, -0.01, 0.02, -0.015, 0.005, 0.012])
    asset = mkt * 2.0  # beta exactly 2
    assert dl.beta(asset, mkt) == 2.0


def test_beta_undefined_on_short_or_flat():
    assert dl.beta(pd.Series([0.01, 0.02]), pd.Series([0.01, 0.02])) is None  # too few points
    flat = pd.Series([0.0] * 8)
    assert dl.beta(pd.Series([0.01] * 8), flat) is None  # zero market variance


def test_decompose_pure_contagion():
    # whole drop explained by beta×market → fraction 1.0, no idiosyncratic residual
    d = dl.decompose(window_return=-0.05, beta_val=1.0, market_window_return=-0.05)
    assert d["contagion_fraction"] == 1.0
    assert d["idiosyncratic_pct"] == 0.0


def test_decompose_partial_contagion_leaves_residual():
    # copper-like: fell 8%, beta 1.8, market -2.8% → ~5% explained, ~3% idiosyncratic
    d = dl.decompose(window_return=-0.08, beta_val=1.8, market_window_return=-0.028)
    assert d["expected_pct"] == round(1.8 * -0.028 * 100, 2)   # -5.04
    assert d["idiosyncratic_pct"] < 0                          # fell MORE than beta explains
    assert 0.0 < d["contagion_fraction"] < 1.0


def test_decompose_market_up_sector_down_is_not_contagion():
    d = dl.decompose(window_return=-0.04, beta_val=1.0, market_window_return=0.02)
    assert d["contagion_fraction"] == 0.0


def test_decompose_no_beta_is_none():
    d = dl.decompose(window_return=-0.05, beta_val=None, market_window_return=-0.03)
    assert d["contagion_fraction"] is None
