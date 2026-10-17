"""The selection indifference band — the top-N cut stops being a hard cliff (2026-09-02).

The cut was `head(max_positions)` on `composite_z`, a DENSE and NOISY column: on
run_20260902_064125 the 6th and 7th names sat 0.074z apart. This project's own standard for a
legitimate hard threshold is the conviction gate's — "read off the measured bimodal gap", i.e.
placed where the distribution has no density. The rank cut failed that test, so names the score
cannot resolve are now separated by measured risk instead.

What these tests pin is mostly what the band must NOT do. Two wrong versions were written and
measured on the live run before this one, and both looked reasonable:

  1. an indifference ZONE of ±band around the cut — an interval of width 2·band, inside which
     any member could displace any other. Indifference is not transitive, so a name at z=0.67
     lost its slot to one at z=0.16 on a band of 0.27.
  2. pairwise swaps against the CURRENT occupant — which moved the same walk from inside one
     iteration to across iterations: three individually legal hops (0.145 → 0.259 → 0.246) with
     the displaced name re-entering the pool as the next stepping stone.

Both are regression-tested below, because both produce a plausible-looking book and neither
raises anything.
"""
import pandas as pd
import pytest

from catalyx.execution import portfolio


def _frame(rows):
    return pd.DataFrame([{"sector_id": s, "primary_etf": f"{s.upper()}.L", "composite_z": z}
                         for s, z in rows])


def _sigma_fn(vols: dict, rho: dict | None = None):
    """Σ over the requested tickers: given standalone vols and an optional pairwise ρ (default 0.9
    so that ANY diversification shows up, making an inert result meaningful)."""
    import numpy as np

    def fn(tickers):
        ts = list(dict.fromkeys(tickers))
        n = len(ts)
        S = np.zeros((n, n))
        for i, a in enumerate(ts):
            for j, b in enumerate(ts):
                r = 1.0 if i == j else (rho or {}).get(frozenset((a, b)), 0.9)
                S[i, j] = r * vols[a] * vols[b]
        return {"sigma": S, "tickers": ts}
    return fn


# ── the band itself ──────────────────────────────────────────────────────────

def test_band_shrinks_as_declared_skill_rises():
    """Δz_band = friction ÷ (IC · σ_cs), so a model that orders returns needs a narrower band.
    At λ=1 the band all but vanishes and the plain top-N cut is the correct limit: a ranking that
    works has no business deferring to risk to break its ties."""
    lo = portfolio.selection_band_z(lam=0.3, ic_target=0.20, sigma_cs_pct=12.26, friction_pct=0.20)
    hi = portfolio.selection_band_z(lam=1.0, ic_target=0.20, sigma_cs_pct=12.26, friction_pct=0.20)
    assert lo["band_z"] == pytest.approx(0.2718, abs=1e-3)
    assert hi["band_z"] == pytest.approx(0.0816, abs=1e-3)
    assert hi["band_z"] < lo["band_z"]


def test_a_degenerate_lambda_cannot_silently_abolish_the_ranking():
    """λ=0 means no gap is resolvable, i.e. an UNBOUNDED band — which is not "wide", it is the
    deletion of the ranking. It is capped and the cap is reported, never applied quietly."""
    out = portfolio.selection_band_z(lam=0.0, ic_target=0.20, sigma_cs_pct=12.26,
                                     friction_pct=0.20, max_band=1.0)
    assert out["band_z"] == 1.0 and out["capped"] is True
    assert "capped" in out["note"]


def test_unmeasured_dispersion_gives_no_band_not_a_zero_one():
    """A zero band would read as "the ranking resolves everything" — the opposite of not knowing."""
    out = portfolio.selection_band_z(lam=0.3, ic_target=0.20, sigma_cs_pct=None, friction_pct=0.20)
    assert out["band_z"] is None and "unmeasured" in out["note"]


# ── selection ────────────────────────────────────────────────────────────────

def test_without_a_band_or_a_matrix_the_cut_is_the_old_one():
    f = _frame([("a", 1.0), ("b", 0.9), ("c", 0.8), ("d", 0.75)])
    for band, fn in ((None, _sigma_fn({})), (0.5, None)):
        picked, rep = portfolio.band_select(f, 3, "composite_z", band, fn)
        assert list(picked["sector_id"]) == ["a", "b", "c"]
        assert rep["applied"] is False and rep["reason"]


def test_an_unbuildable_matrix_falls_back_and_says_so():
    """Never a silent second selection rule: a book that walked a different cut must say which."""
    picked, rep = portfolio.band_select(
        _frame([("a", 1.0), ("b", 0.9), ("c", 0.8)]), 2, "composite_z", 0.5,
        lambda t: {"error": "no cached prices"})
    assert list(picked["sector_id"]) == ["a", "b"]
    assert rep["applied"] is False and "no cached prices" in rep["reason"]


def test_a_diversifier_inside_the_band_takes_the_marginal_slot():
    """`d` ranks below `c` by 0.05z — inside the band — and is uncorrelated with the book, so it
    lowers book vol and earns the slot. This is the whole point of the mechanism."""
    f = _frame([("a", 1.0), ("b", 0.9), ("c", 0.8), ("d", 0.75)])
    fn = _sigma_fn({"A.L": 0.2, "B.L": 0.2, "C.L": 0.2, "D.L": 0.2},
                   rho={frozenset(("A.L", "D.L")): 0.0, frozenset(("B.L", "D.L")): 0.0})
    picked, rep = portfolio.band_select(f, 3, "composite_z", 0.2, fn)
    assert set(picked["sector_id"]) == {"a", "b", "d"}
    assert rep["applied"] is True and rep["dropped"] == ["c"] and rep["added"] == ["d"]
    assert rep["vol_pct_after"] < rep["vol_pct_before"]


def test_the_score_is_never_overruled_from_outside_the_band():
    """`d` is a perfect diversifier and would slash book vol — and it is 0.5z below `c`, outside
    a band of 0.2. Risk breaks TIES; it does not win arguments the score is making."""
    f = _frame([("a", 1.0), ("b", 0.9), ("c", 0.8), ("d", 0.3)])
    fn = _sigma_fn({"A.L": 0.2, "B.L": 0.2, "C.L": 0.2, "D.L": 0.01},
                   rho={frozenset(("A.L", "D.L")): 0.0, frozenset(("B.L", "D.L")): 0.0})
    picked, rep = portfolio.band_select(f, 3, "composite_z", 0.2, fn)
    assert list(picked["sector_id"]) == ["a", "b", "c"]
    assert rep["applied"] is False


def test_indifference_is_not_transitive_and_the_band_may_not_walk_the_ranking():
    """REGRESSION (design bug 1). `b` (0.9) and `d` (0.5) are 0.4 apart on a band of 0.25 — they
    are NOT tied, whatever the chain of ties between them. A ±band zone around the cut would have
    admitted both to one contested pool and let `d` take `b`'s slot."""
    f = _frame([("a", 1.0), ("b", 0.9), ("c", 0.7), ("d", 0.5)])
    fn = _sigma_fn({"A.L": 0.2, "B.L": 0.2, "C.L": 0.2, "D.L": 0.02},
                   rho={frozenset(("A.L", "D.L")): 0.0, frozenset(("B.L", "D.L")): 0.0,
                        frozenset(("C.L", "D.L")): 0.0})
    picked, rep = portfolio.band_select(f, 3, "composite_z", 0.25, fn)
    assert "b" in set(picked["sector_id"]), "a name 0.4z above the challenger kept its slot"
    for s in rep.get("steps", []):
        assert s["z_gap"] <= 0.25 + 1e-9


def test_a_slot_is_judged_against_its_original_occupant_not_the_current_one():
    """REGRESSION (design bug 2). Each hop below is legal against the CURRENT holder, so a
    per-iteration check lets `d` reach `a`'s slot through `b` and `c` — total drift 0.6z on a band
    of 0.25. The slot remembers who it was ranked for, and a displaced name does not return to
    the pool to be the next stepping stone."""
    f = _frame([("a", 1.0), ("b", 0.8), ("c", 0.6), ("d", 0.4)])
    # every name is a better diversifier than the one above it, so vol falls at every hop
    fn = _sigma_fn({"A.L": 0.30, "B.L": 0.22, "C.L": 0.15, "D.L": 0.08},
                   rho={frozenset(("A.L", "D.L")): 0.0, frozenset(("A.L", "C.L")): 0.1})
    picked, rep = portfolio.band_select(f, 2, "composite_z", 0.25, fn)
    assert "a" in set(picked["sector_id"]), "the top name kept a slot no legal pair could take"
    for s in rep.get("steps", []):
        assert s["z_gap"] <= 0.25 + 1e-9, f"drift beyond one band: {s}"


def test_an_inert_run_reports_that_it_was_inert():
    """A selection rule invisible on the runs where it does nothing cannot be audited on the run
    where it bites."""
    f = _frame([("a", 1.0), ("b", 0.9), ("c", 0.85)])
    fn = _sigma_fn({"A.L": 0.2, "B.L": 0.2, "C.L": 0.9})
    picked, rep = portfolio.band_select(f, 2, "composite_z", 0.3, fn)
    assert list(picked["sector_id"]) == ["a", "b"]
    assert rep["applied"] is False and "lowers book vol" in rep["reason"]


def test_the_swap_count_is_capped():
    """A rule that rewrites the whole book in one review has stopped breaking ties. The cap is
    where that becomes visible instead of being discovered in the turnover."""
    f = _frame([("a", 1.0), ("b", 0.95), ("c", 0.9), ("d", 0.88), ("e", 0.86)])
    fn = _sigma_fn({"A.L": 0.5, "B.L": 0.5, "C.L": 0.5, "D.L": 0.05, "E.L": 0.05})
    _, rep = portfolio.band_select(f, 3, "composite_z", 0.3, fn, max_swaps=1)
    assert len(rep.get("steps", [])) == 1


def test_the_lambda_floor_does_not_reach_the_counterfactual_baselines():
    """The floor is a declared prior about THE COMPOSITE, derived from the composite's own weight
    split against the backtest ICs. A `momentum` book is the counterfactual this book is measured
    against; lending it a conviction it never claimed makes the comparison answer a different
    question. Same rule the CA tilt and risk-budget sizing already obey."""
    from catalyx.config import weights as W
    floor = float(W.portfolio_weighting().get("tilt_lambda_floor", 0.0))
    if floor <= 0:
        pytest.skip("no floor configured")
    for pid in ("momentum", "equal_weight"):
        d = portfolio.build_model_holdings(pid, persist=False)
        lam = (d.get("tilt") or {}).get("lambda")
        assert lam in (None, 0.0), f"{pid} inherited the composite's λ floor ({lam})"
    lam = (portfolio.build_model_holdings("catalyx", persist=False).get("tilt") or {}).get("lambda")
    assert lam is not None and lam >= floor
