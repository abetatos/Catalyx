"""v11.1 — crowding prices the marginal BUYER, and a rotation states what it costs the BOOK.

Two defects, one origin: every cost the table carried was a cost of the TRADE, and every filter
it applied was written to decide an ENTRY. So a held winner could be sold for having become
crowded — crowding rises with success — and the rotation that replaced it could leave the book
materially more correlated without a single number on the page moving.

These tests pin the narrow shape of both fixes. The crowding suppression must fire ONLY when
lifting the bar would actually have selected the name, must not shield a position from any
other exit, and must fail toward the sale. The risk delta must be a column: it reports, and it
returns None rather than a comforting zero when it cannot measure.
"""
from __future__ import annotations

import pytest

from catalyx.config import weights
from catalyx.execution import rebalance as rb


@pytest.fixture
def cfg():
    return weights.rebalance_rules()


def _row(**over) -> dict:
    base = {"held": True, "gap_pp": 0.0, "rank": 3, "regime_state": "intact",
            "narrative_maturity": "emerging", "exit_action": "hold", "catalyst_status": "active",
            "unrealized_pct": 5.0, "reverify_required": False, "drawdown_tier": "clear",
            "rank_out_streak": 0}
    base.update(over)
    return base


# ── The suppression, and its limits ──────────────────────────────────────────

def test_a_held_name_excluded_only_by_crowding_is_not_trimmed(cfg):
    # The live case: pharma_large_cap, rank 4 of 26, crowding 94.4 over an 85 bar, so its model
    # target is 0% and the overweight TRIM reads the whole line as excess.
    # Upstream the target is re-read as min(actual, cap), so a holding inside the ceiling has a
    # zero gap — the "maximally overweight" reading is exactly what that removes.
    out = rb.decide_action(_row(gap_pp=0.0, crowding_excluded_only=True,
                                crowding_risk=94.4, max_crowding=85.0,
                                score_rank=4, rank=None), cfg)
    assert out["action"] == "HOLD"
    assert "crowding" in out["reason"]
    # The reason must carry the numbers that produced it — a verdict whose cause is not legible
    # is one the next reader re-litigates from scratch.
    assert "94.4" in out["reason"] and "85" in out["reason"]
    assert "rank 4" in out["reason"]


def test_an_overweight_name_NOT_excluded_by_crowding_is_still_trimmed(cfg):
    # The suppression must not become a general amnesty for overweight positions.
    out = rb.decide_action(_row(gap_pp=-5.6), cfg)
    assert out["action"] == "TRIM"


def test_crowding_never_shields_a_position_from_a_real_exit(cfg):
    # A dead driver, a broken regime and a fired exit watcher are exits on their own evidence.
    # Crowding suppresses ONE thing — the overweight trim — and must not reach any of these.
    for kill in ({"catalyst_status": "invalidated"}, {"regime_state": "breaking"},
                 {"exit_action": "exit"}):
        out = rb.decide_action(_row(gap_pp=-5.6, crowding_excluded_only=True,
                                    crowding_risk=94.4, max_crowding=85.0, **kill), cfg)
        assert out["action"] == "SELL", kill


def test_the_profit_ladder_still_reaches_a_crowding_excluded_holding(cfg):
    # The ladder trims on GAIN, not on the target gap, so the exclusion is irrelevant to it.
    # A +40% winner at a rank the ladder accepts still gives back a rung.
    laddered = rb.decide_action(_row(gap_pp=-1.0, unrealized_pct=40.0, rank=8, score_rank=8,
                                     crowding_excluded_only=True,
                                     crowding_risk=94.4, max_crowding=85.0), cfg)
    plain = rb.decide_action(_row(gap_pp=-1.0, unrealized_pct=40.0, rank=8, score_rank=8), cfg)
    assert laddered["action"] == plain["action"]


def test_crowding_suppresses_the_exit_but_never_the_concentration_ceiling(cfg):
    """The hole the first cut of this left open, and the reason it is not left open.

    Suppressing the overweight TRIM outright also switches off the ONLY mechanism that trims
    for SIZE. A crowding-excluded holding would then be free to appreciate to any share of the
    book and still read HOLD, because its target is 0% and nothing else measures concentration.
    Upstream the target becomes `min(actual, max_position_pct)`, so `over` stays 0 inside the
    ceiling and becomes real above it — and the ordinary TRIM both fires and sizes to the cap.
    """
    # Above the ceiling: the excess is real overweight and is trimmed.
    over_cap = rb.decide_action(_row(gap_pp=-6.0, crowding_excluded_only=True,
                                     crowding_risk=94.4, max_crowding=85.0, score_rank=4), cfg)
    assert over_cap["action"] == "TRIM"
    # Inside the ceiling the gap is zero by construction, and the row explains itself.
    inside = rb.decide_action(_row(gap_pp=0.0, crowding_excluded_only=True,
                                   crowding_risk=94.4, max_crowding=85.0, score_rank=4), cfg)
    assert inside["action"] == "HOLD"
    assert "crowding" in inside["reason"]


def test_a_missing_flag_reproduces_the_old_behaviour_exactly(cfg):
    # Rows built before this existed carry no flag at all. They must trim as they always did.
    assert rb.decide_action(_row(gap_pp=-5.6), cfg)["action"] == "TRIM"


# ── The counterfactual that sets the flag ────────────────────────────────────

def test_only_names_the_bar_ALONE_kept_out_are_listed():
    """`crowding_excluded` is a counterfactual, not a threshold comparison.

    A sector over the crowding bar that would have MISSED the top-N anyway is not excluded by
    crowding — it is simply not good enough — and listing it would hand `rebalance` a reason to
    cancel a sound exit. On the 2026-09-01 universe both pharma_large_cap and
    biotech_drug_development sit at crowding 94.4; only pharma (rank 4) would have taken a slot,
    because lifting the bar puts pharma in and pushes the 6th name out before biotech (rank 7)
    is reached.
    """
    from catalyx.execution import portfolio as P

    res = P.build_model_holdings("catalyx", persist=False)
    if res.get("error"):
        pytest.skip("no scored run in the lake")
    listed = {e["sector_id"] for e in res["crowding_excluded"]}
    book = {h["sector_id"] for h in res["holdings"]}
    # Nothing in the book can also be listed as excluded from it.
    assert listed.isdisjoint(book)
    for e in res["crowding_excluded"]:
        # Every listed name must actually be over the bar it names.
        assert e["crowding_risk"] > e["max_crowding"]


def test_the_exclusion_lookup_fails_toward_the_sale(monkeypatch):
    # A mechanism whose job is to CANCEL a sell must fail toward selling. An exception here
    # returns {}, no row gets the flag, and the overweight TRIM fires exactly as before.
    def boom(*a, **k):
        raise RuntimeError("lake unavailable")

    monkeypatch.setattr("catalyx.execution.portfolio.build_model_holdings", boom)
    assert rb._crowding_excluded("catalyx", "run_x") == {}


# ── The book-risk column ─────────────────────────────────────────────────────

def _rows_for_risk():
    return [
        {"sector_id": "a", "etf": "AAA", "actual_eur": 1000.0, "trade_eur": -1000.0,
         "budget_state": "n/a", "ramp_state": "n/a"},
        {"sector_id": "b", "etf": "BBB", "actual_eur": 1000.0, "trade_eur": 0.0,
         "budget_state": "n/a", "ramp_state": "n/a"},
        {"sector_id": "c", "etf": "CCC", "actual_eur": 0.0, "trade_eur": 1000.0,
         "budget_state": "n/a", "ramp_state": "n/a"},
    ]


def test_the_risk_delta_reports_a_more_correlated_book():
    calls = []

    def fake_analyze(tickers=None, weights=None, **k):
        calls.append(set(tickers))
        # after the swap the book is AAA-free; give it the higher correlation
        after = "CCC" in set(tickers)
        return {"portfolio_vol_annual_pct": 0.21 if after else 0.18,
                "mean_pairwise_corr_weekly": 0.30 if after else 0.12}

    out = rb.book_risk_delta(_rows_for_risk(), analyze_fn=fake_analyze)
    assert out["more_correlated"] is True
    assert out["delta_vol_pp"] == pytest.approx(3.0)
    assert out["delta_mean_corr"] == pytest.approx(0.18)
    # BEFORE must be the book as held — the sold name present, the bought one absent.
    assert calls[0] == {"AAA", "BBB"}
    assert calls[1] == {"BBB", "CCC"}


def test_a_queued_row_is_not_part_of_the_book_it_measures():
    # A row the ramp or the trade budget deferred is not executing today, so counting it would
    # price a book nobody is about to hold.
    rows = _rows_for_risk()
    rows[0]["trade_eur"] = 0.0          # keep both held names, so the after-book is measurable
    rows[2]["ramp_state"] = "deferred"
    seen = []

    def fake_analyze(tickers=None, weights=None, **k):
        seen.append(set(tickers))
        return {"portfolio_vol_annual_pct": 0.2, "mean_pairwise_corr_weekly": 0.2}

    rb.book_risk_delta(rows, analyze_fn=fake_analyze)
    assert "CCC" not in seen[1]


def test_unmeasurable_risk_is_None_never_a_comforting_zero():
    def dead(*a, **k):
        raise RuntimeError("no prices")

    assert rb.book_risk_delta(_rows_for_risk(), analyze_fn=dead) is None
    # A one-name book has no correlation to report, and must say so rather than print 0.0.
    assert rb.book_risk_delta([_rows_for_risk()[1]], analyze_fn=dead) is None


def test_the_risk_column_moves_no_money():
    """It reports; it never edits an action. The gate that acts on risk is cluster_risk_pass."""
    rows = _rows_for_risk()
    before = [dict(r) for r in rows]

    def fake_analyze(tickers=None, weights=None, **k):
        return {"portfolio_vol_annual_pct": 0.5, "mean_pairwise_corr_weekly": 0.9}

    rb.book_risk_delta(rows, analyze_fn=fake_analyze)
    assert rows == before


# ── A config edit must not manufacture a sale (v13.1) ────────────────────────

def test_a_stale_model_book_degrades_to_re_score_not_a_trim(cfg):
    """The live regression. Lifting `max_crowding` 85 → 100 leaves the PERSISTED model book
    still excluding pharma while the rebuilt `crowding_excluded` set is now empty — so the
    v12.1 suppression cannot fire and the held line reads as maximally overweight against a 0%
    target. That flipped pharma to `TRIM −€556`: a config edit alone selling the very position
    the edit exists to admit. A model book built under other rules is not a zero target."""
    out = rb.decide_action(_row(gap_pp=-5.6, crowding_excluded_only=False,
                                stale_model_cfg="model book built under config abc, now def"),
                           cfg)
    assert out["action"] == "RE-SCORE"
    assert "re-run the score" in out["reason"]


def test_the_stale_guard_does_not_swallow_a_real_overweight(cfg):
    """It is narrow by construction: only a name ABSENT from the model book gets the flag
    (`build` sets it that way), so a book member 5.6pp overweight is still trimmed for real."""
    out = rb.decide_action(_row(gap_pp=-5.6, stale_model_cfg=None), cfg)
    assert out["action"] == "TRIM"
