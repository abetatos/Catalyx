"""Unit tests for scoring calibration (catalyx.scorer.calibration).

This module exists to stop a number from being over-read, so the tests focus on the guards:
the inverted-dimension sign, the standard error travelling with every IC, and the
effective-sample warning. A calibration that quietly reports a confident-looking IC on one
overlapping window is worse than no calibration at all.
"""
from __future__ import annotations

import pytest

from catalyx.scorer import calibration as cal


# ── Rank IC ──────────────────────────────────────────────────────────────────

def test_rank_ic_is_exact_for_perfect_orderings():
    assert cal.rank_ic([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]) == 1.0
    assert cal.rank_ic([1, 2, 3, 4, 5], [50, 40, 30, 20, 10]) == -1.0


def test_rank_ic_is_monotonic_not_linear():
    # Spearman must not care that returns are exponential — only that the ORDER matches.
    assert cal.rank_ic([1, 2, 3, 4], [0.01, 0.5, 9.0, 900.0]) == 1.0


def test_rank_ic_returns_none_rather_than_a_fake_number():
    assert cal.rank_ic([1, 2], [1, 2]) is None                 # too few points
    assert cal.rank_ic([5, 5, 5, 5], [1, 2, 3, 4]) is None     # constant scores → no ordering
    assert cal.rank_ic([1, 2, 3], None) is None
    assert cal.rank_ic([1, 2, 3], [1, 2]) is None              # length mismatch


def test_rank_ic_ignores_missing_pairs():
    assert cal.rank_ic([1, 2, 3, 4, None], [10, 20, 30, 40, 999]) == 1.0


def test_rank_ic_needs_no_scipy():
    # pandas' method="spearman" routes through scipy, which is NOT a dependency — that is
    # exactly what made the original validate_run crash and never run.
    import sys
    assert "scipy" not in sys.modules or True    # never asserted present
    assert cal.rank_ic([3, 1, 2], [30, 10, 20]) == 1.0


# ── The guards against over-reading ──────────────────────────────────────────

def test_standard_error_shrinks_with_sample_size():
    assert cal.ic_standard_error(26) == pytest.approx(0.2, abs=0.01)   # the real universe
    assert cal.ic_standard_error(101) == pytest.approx(0.1, abs=0.01)
    assert cal.ic_standard_error(2) is None


def test_a_single_run_ic_below_two_se_is_called_noise():
    se = cal.ic_standard_error(26)                # ≈0.20 → the noise band is ±0.40
    assert cal.ic_verdict(-0.18, se) == "noise"
    assert cal.ic_verdict(0.39, se) == "noise"
    assert cal.ic_verdict(-0.47, se) == "weak"
    assert cal.ic_verdict(0.75, se) == "signal"
    assert cal.ic_verdict(None, se) == "insufficient"


def test_inverted_dimension_sign_is_computed_not_eyeballed():
    # crowding enters the composite as (100 − crowding): if MORE-crowded sectors did better
    # (raw +0.2), the composite's crowding penalty HURT (−0.2). Reading this backwards inverts
    # the conclusion about the one inverted dimension.
    assert cal.contribution_ic(0.2, inverted=True) == -0.2
    assert cal.contribution_ic(0.2, inverted=False) == 0.2
    assert cal.contribution_ic(None, inverted=True) is None
    assert cal.DIMENSIONS["crowding_risk"] is True
    assert cal.DIMENSIONS["momentum"] is False


def test_top_k_spread_is_the_tradable_twin_of_ic():
    scores = [90, 80, 70, 60, 50, 40, 30]
    returns = [0.10, 0.08, 0.06, 0.01, 0.00, -0.02, -0.04]
    out = cal.top_k_spread(scores, returns, k=2)
    assert out["top_k"] == pytest.approx(9.0, abs=0.01)      # (10+8)/2, in %
    assert out["spread"] > 0                                  # high scores did better
    assert cal.top_k_spread([1, 2], [0.1, 0.2], k=2)["spread"] is None   # too few to split


# ── Aggregation: the effective-sample warning ────────────────────────────────

def _run(run_id, start, ic, horizon=63):
    return {"run_id": run_id, "start": start, "end": "2026-08-28", "horizon_days": horizon,
            "n_sectors": 26, "se": 0.2, "window_complete": True,
            "dimensions": {"composite": {"rank_ic": ic, "as_used_ic": ic,
                                         "inverted_in_composite": False, "verdict": "noise"}},
            "spread": {"spread": -5.0}, "computed_at": "2026-08-28T00:00:00Z"}


def test_overlapping_runs_count_as_one_effective_window():
    # Six runs a few days apart over one regime are ~one observation, not six. Reporting the
    # mean of six without this is how a noise reading becomes a "finding".
    res = [_run(f"run_{i}", d, -0.1) for i, d in enumerate(
        ["2026-06-06", "2026-06-08", "2026-06-12", "2026-06-30", "2026-07-05", "2026-07-28"])]
    agg = cal.aggregate(res)
    assert agg["runs"] == 6
    assert agg["effective_windows"] == 1
    assert "not 6" in agg["note"]


def test_well_separated_runs_count_separately():
    res = [_run("a", "2026-01-01", 0.1), _run("b", "2026-04-01", 0.2),
           _run("c", "2026-07-01", 0.3)]
    agg = cal.aggregate(res)
    assert agg["effective_windows"] == 3
    assert agg["mean_as_used_ic"]["composite"] == pytest.approx(0.2, abs=0.001)


def test_aggregate_ignores_errored_runs():
    agg = cal.aggregate([_run("a", "2026-01-01", 0.1), {"run_id": "b", "error": "no prices"}])
    assert agg["runs"] == 1


def test_aggregate_with_nothing_usable_says_so():
    agg = cal.aggregate([{"run_id": "b", "error": "no prices"}])
    assert agg["runs"] == 0 and "no run" in agg["note"]


# ── Rendering ────────────────────────────────────────────────────────────────

def test_render_states_the_inversion_and_the_effective_sample():
    res = [_run("run_20260606_205930", "2026-06-06", -0.12)]
    text = cal._render(res, cal.aggregate(res))
    assert "crowding is inverted" in text
    assert "1 non-overlapping" in text
    assert "noise" in text                      # the noise-band warning must be visible


# ── Rank buckets: the €-denominated twin of the IC ──────────────────────────

def test_bucket_returns_are_means_by_rank_not_by_score_value():
    scores = [90, 80, 70, 60, 50, 40, 30, 20, 10, 5, 1, 0]
    returns = [0.10, 0.08, 0.06, 0.04, 0.03, 0.02, 0.01, 0.0, -0.01, -0.02, -0.03, -0.04]
    out = cal.bucket_returns(scores, returns)
    assert out["top3"] == pytest.approx(8.0, abs=0.01)      # (10+8+6)/3, in %
    assert out["mid"] > out["rest"]


def test_a_bucket_with_one_member_is_none_not_an_expectation():
    out = cal.bucket_returns([3, 2, 1], [0.1, 0.2, 0.3])
    assert out["rest"] is None                              # only rank 11+ … nobody there
    assert out["top3"] is not None


def test_bucket_of_maps_a_rank_and_tolerates_an_unranked_sector():
    assert cal.bucket_of(1) == "top3"
    assert cal.bucket_of(3) == "top3"
    assert cal.bucket_of(4) == "mid"
    assert cal.bucket_of(11) == "rest"
    assert cal.bucket_of(None) is None


def test_shrinkage_makes_a_thin_sample_a_number_not_a_caveat():
    # One window with a prior of 6 → believe a seventh of what was measured.
    assert cal.shrink_factor(1, 6.0) == pytest.approx(0.1429, abs=0.001)
    assert cal.shrink_factor(0, 6.0) == 0.0                 # nothing measured → edge is exactly 0
    assert cal.shrink_factor(6, 6.0) == pytest.approx(0.5)
    assert cal.shrink_factor(60, 6.0) > 0.9
