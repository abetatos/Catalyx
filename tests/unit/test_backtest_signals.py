"""v8 Fase P — the backtest harness's pure parts: point-in-time discipline and sign conventions.

Run: uv run pytest tests/unit/test_backtest_signals.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from experiments import backtest_signals as bt


def _series(n=400, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    return pd.Series(100 * np.cumprod(1 + rng.normal(0.0005, 0.01, n)), index=idx)


def test_12_1_skips_the_last_month():
    s = _series()
    i = 300
    expected = s.iloc[i - 21] / s.iloc[i - 252] - 1
    assert bt.sig_12_1(s, i) == pytest.approx(expected)


def test_signals_need_enough_history():
    s = _series(100)
    assert bt.sig_12_1(s, 99) is None
    assert bt.sig_52w(s, 99) is None


def test_52w_high_is_nonpositive_and_zero_at_the_high():
    s = _series()
    i = int(np.argmax(s.values[:301]))
    if i >= 252:
        assert bt.sig_52w(s, i) == pytest.approx(0.0)
    assert bt.sig_52w(s, 300) <= 1e-12


def test_forward_return_never_reads_past_the_series():
    s = _series(300)
    assert bt.fwd_ret(s, 280, 63) is None            # window incomplete -> None, not partial
    assert bt.fwd_ret(s, 200, 63) == pytest.approx(s.iloc[263] / s.iloc[200] - 1)


def test_crowding_ics_are_sign_flipped_to_as_used():
    rows = []
    t = pd.Timestamp("2024-01-31")
    for k in range(10):
        rows.append({"t": t, "sector_id": f"s{k}",
                     "cot_crowding": float(k), "fwd63": -0.01 * k})
    panel = pd.DataFrame(rows)
    ics = bt.monthly_ics(panel, "cot_crowding", "fwd63")
    assert ics.iloc[0] == pytest.approx(1.0)          # high crowding -> low return = penalty WORKS


def test_gk_discounts_a_redundant_signal():
    summary = [
        {"signal": "a", "n_months": 120, "n_eff": 40.0, "mean_ic": 0.05},
        {"signal": "b", "n_months": 120, "n_eff": 40.0, "mean_ic": 0.05},
        {"signal": "c", "n_months": 120, "n_eff": 40.0, "mean_ic": 0.05},
    ]
    omega = pd.DataFrame([[1.0, 0.9, 0.0], [0.9, 1.0, 0.0], [0.0, 0.0, 1.0]],
                         index=list("abc"), columns=list("abc"))
    w = bt.gk_weights(summary, omega)
    assert w["c"] > w["a"] * 1.5                      # the independent signal earns more
    assert w["a"] == pytest.approx(w["b"], abs=1e-9)


def test_cot_ic_survives_its_five_name_sleeve():
    rows = []
    t = pd.Timestamp("2024-01-31")
    for k in range(5):
        rows.append({"t": t, "sector_id": f"s{k}",
                     "cot_crowding": float(k), "mom_3m6m": float(k), "fwd63": -0.01 * k})
    panel = pd.DataFrame(rows)
    assert len(bt.monthly_ics(panel, "cot_crowding", "fwd63")) == 1   # floor 4 admits it
    assert bt.monthly_ics(panel, "mom_3m6m", "fwd63").empty           # global floor 8 holds


def test_gk_needs_at_least_two_measured_signals():
    summary = [{"signal": "a", "n_months": 120, "n_eff": 40.0, "mean_ic": 0.05}]
    omega = pd.DataFrame([[1.0]], index=["a"], columns=["a"])
    assert bt.gk_weights(summary, omega) == {}
