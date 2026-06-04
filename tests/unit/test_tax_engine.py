"""Unit tests for the Spanish CGT engine (catalyx.execution.tax_engine).

Highest-stakes deterministic module (real money), so edge cases are covered
explicitly: bracket boundaries, incremental tax given prior YTD gains, loss
offset, and multi-trade loss carry-forward.

Run: uv run pytest tests/unit/test_tax_engine.py -q
"""
from __future__ import annotations

import pytest

from catalyx.execution.tax_engine import compute_tax, compute_ytd_tax

# 2026 brackets: 19% ≤6k, 21% 6k–50k, 23% 50k–200k, 27% >200k


# ── compute_tax: single-gain bracket math ────────────────────────────────────

def test_gain_within_first_bracket():
    r = compute_tax(5_000)
    assert r.tax_due == pytest.approx(5_000 * 0.19)
    assert r.taxable_gain == 5_000
    assert r.net_gain == pytest.approx(5_000 - 5_000 * 0.19)


def test_gain_exactly_at_first_boundary():
    r = compute_tax(6_000)
    assert r.tax_due == pytest.approx(6_000 * 0.19)


def test_gain_spanning_first_two_brackets():
    # 6k @19% + 4k @21%
    r = compute_tax(10_000)
    expected = 6_000 * 0.19 + 4_000 * 0.21
    assert r.tax_due == pytest.approx(expected)


def test_gain_spanning_all_four_brackets():
    # 6k@19 + 44k@21 + 150k@23 + 50k@27  (gain = 250k)
    r = compute_tax(250_000)
    expected = 6_000 * 0.19 + 44_000 * 0.21 + 150_000 * 0.23 + 50_000 * 0.27
    assert r.tax_due == pytest.approx(expected)


def test_zero_gain():
    r = compute_tax(0)
    assert r.tax_due == 0.0
    assert r.effective_rate == 0.0


def test_negative_gain_raises():
    with pytest.raises(ValueError):
        compute_tax(-100)


def test_negative_loss_raises():
    with pytest.raises(ValueError):
        compute_tax(1_000, losses=-5)


# ── compute_tax: incremental tax given prior YTD gains ───────────────────────

def test_incremental_tax_pushes_into_higher_bracket():
    # Prior 6k already fills the 19% bracket; a new 4k gain is taxed entirely at 21%.
    r = compute_tax(4_000, ytd_prior=6_000)
    assert r.tax_due == pytest.approx(4_000 * 0.21)


def test_incremental_equals_difference_of_cumulative():
    # tax(prior+gain) - tax(prior) must equal the incremental tax_due.
    prior, gain = 48_000, 10_000
    full = compute_tax(prior + gain).tax_due
    base = compute_tax(prior).tax_due
    incr = compute_tax(gain, ytd_prior=prior).tax_due
    assert incr == pytest.approx(full - base)


# ── compute_tax: loss offset ─────────────────────────────────────────────────

def test_loss_offset_reduces_taxable_base():
    r = compute_tax(10_000, losses=4_000)
    assert r.taxable_gain == 6_000
    assert r.tax_due == pytest.approx(6_000 * 0.19)


def test_loss_exceeding_gain_yields_zero_tax():
    r = compute_tax(3_000, losses=5_000)
    assert r.taxable_gain == 0.0
    assert r.tax_due == 0.0


# ── compute_ytd_tax: multi-trade loss carry-forward (regression) ─────────────

def test_loss_carries_forward_across_multiple_gains():
    # REGRESSION: a 100 loss must offset BOTH subsequent 50 gains → zero tax.
    # The old code zeroed the carry after the first gain, taxing the second.
    out = compute_ytd_tax([-100, 50, 50])
    assert out["total_tax"] == 0.0


def test_partial_loss_carry_then_taxed_remainder():
    # 100 loss offsets the first 50 fully and 50 of the second; remaining 50 taxed.
    out = compute_ytd_tax([-100, 50, 100])
    assert out["total_tax"] == pytest.approx(50 * 0.19)


def test_loss_after_gain_does_not_retroactively_refund():
    # Gain taxed first (6k@19 + 4k@21), later loss only carries forward (no refund).
    out = compute_ytd_tax([10_000, -4_000])
    assert out["total_tax"] == pytest.approx(6_000 * 0.19 + 4_000 * 0.21)
    assert out["per_trade"][-1]["loss_carry_balance"] == 4_000.0


def test_carry_balance_tracked_in_per_trade():
    out = compute_ytd_tax([-100, 50, 50])
    balances = [t.get("loss_carry_balance") for t in out["per_trade"]]
    # 100 carried in, 50 left after first gain, 0 after second.
    assert balances == [100.0, 50.0, 0.0]


def test_sequential_gains_apply_brackets_cumulatively():
    # Two 5k gains: first fills 19% bracket to 5k, second spans 1k@19 + 4k@21.
    out = compute_ytd_tax([5_000, 5_000])
    expected = 5_000 * 0.19 + (1_000 * 0.19 + 4_000 * 0.21)
    assert out["total_tax"] == pytest.approx(expected)
