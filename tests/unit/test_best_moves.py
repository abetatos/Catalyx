"""v15.3 — every candidate trade priced in one currency. A column: it moves nothing.

Run: uv run pytest tests/unit/test_best_moves.py -q
"""
from __future__ import annotations

import pytest

from catalyx.execution import best_moves as bm

H = 63 / 252


def _mu(betas, z=None, erp=0.05, ic=0.06, scs=0.12):
    return bm.expected_returns(betas, z or {}, erp, ic, scs, H)


def test_delta_is_the_market_implied_risk_aversion():
    assert bm.implied_delta(0.04, 0.05) == pytest.approx(1.25)
    assert bm.implied_delta(0.0, 0.05) is None


def test_the_market_portfolio_is_where_premium_and_risk_balance():
    # One asset = the market. Under δ = ERP/σ², the marginal utility of the market weight is zero
    # at w=1: d/dw [w·ERP − δ/2·w²σ²] = ERP − δσ² = 0. Cutting beta is NOT free.
    s2 = 0.04
    delta = bm.implied_delta(s2, 0.05)
    sigma = {"m": {"m": s2}}
    mu = _mu({"m": 1.0}, ic=0.0)
    u = lambda w: bm.utility({"m": w}, mu, sigma, delta, H, 1.0)["total"]
    assert u(1.0) > u(0.9) and u(1.0) > u(1.1)


def _two(rho=0.0, s_hi=0.30, s_lo=0.15):
    return {"hi": {"hi": s_hi**2, "lo": rho * s_hi * s_lo},
            "lo": {"hi": rho * s_hi * s_lo, "lo": s_lo**2}}


def _plan(held, cands, mu, sigma, delta, cost=0.0, slots=6, n=6, cash=0.0):
    return bm.plan(held, {c: True for c in cands}, mu, sigma, delta, H, 10_000.0,
                   lambda *a: cost, slots, n, 1_250.0, cash)


def test_a_move_that_only_cuts_beta_does_not_pay():
    # Same idiosyncratic shape, lower beta: the premium lost matches the risk removed.
    sigma = {"hi": {"hi": 0.09, "lo": 0.0}, "lo": {"hi": 0.0, "lo": 0.0225}}
    var_m = 0.04
    delta = bm.implied_delta(var_m, 0.05)
    # betas consistent with a one-factor model: var = β²σm² (pure market risk)
    mu = _mu({"hi": 1.5, "lo": 0.75}, ic=0.0)
    p = _plan({"hi": 2_000.0}, ["lo"], mu, sigma, delta, cost=1.0)
    assert p["steps"] == [] and p["clearing"]["net_eur"] <= 0


def test_a_move_that_cuts_RESIDUAL_risk_at_equal_beta_pays():
    delta = 2.0
    sigma = _two(s_hi=0.40, s_lo=0.15)
    mu = _mu({"hi": 1.0, "lo": 1.0}, ic=0.0)
    p = _plan({"hi": 3_000.0}, ["lo"], mu, sigma, delta, cost=1.0)
    assert [s["buy"] for s in p["steps"]] == ["lo"]
    assert p["steps"][0]["d_risk_eur"] > 0 and p["steps"][0]["d_premium_eur"] == pytest.approx(0)


def test_cost_alone_can_stop_a_move():
    delta = 2.0
    sigma = _two(s_hi=0.40, s_lo=0.15)
    mu = _mu({"hi": 1.0, "lo": 1.0}, ic=0.0)
    gross = _plan({"hi": 3_000.0}, ["lo"], mu, sigma, delta)["steps"][0]["gross_eur"]
    p = _plan({"hi": 3_000.0}, ["lo"], mu, sigma, delta, cost=gross + 0.01)
    assert p["steps"] == []


def test_greedy_reprices_after_each_move_so_a_fix_is_not_counted_twice():
    # Two held high-vol lines, one low-vol candidate. Only ONE swap can use the candidate.
    delta = 2.0
    s = {"a": 0.40, "b": 0.40, "c": 0.15}
    sigma = {i: {j: (s[i] ** 2 if i == j else 0.0) for j in s} for i in s}
    mu = _mu({k: 1.0 for k in s}, ic=0.0)
    p = _plan({"a": 2_000.0, "b": 2_000.0}, ["c"], mu, sigma, delta)
    assert len(p["steps"]) == 1


def test_a_swap_spends_two_slots_and_the_plan_respects_them():
    delta = 2.0
    sigma = _two(s_hi=0.40, s_lo=0.15)
    mu = _mu({"hi": 1.0, "lo": 1.0}, ic=0.0)
    p = _plan({"hi": 3_000.0}, ["lo"], mu, sigma, delta, slots=1)
    assert p["steps"] == [] and p["slots_used"] == 0


def test_a_free_line_allows_a_buy_from_cash():
    delta = 2.0
    sigma = _two(s_hi=0.30, s_lo=0.10)
    mu = _mu({"hi": 1.0, "lo": 1.0}, ic=0.0)
    p = _plan({"hi": 2_000.0}, ["lo"], mu, sigma, delta, n=2, cash=3_000.0)
    kinds = {m["kind"] for m in p["steps"]}
    assert "buy" in kinds


def test_variance_split_names_how_much_is_paid_market_risk():
    sigma = {"a": {"a": 0.04}}
    vs = bm.variance_split({"a": 1.0}, {"a": 1.0}, sigma, 0.04)
    assert vs["systematic_pct"] == pytest.approx(100.0) and vs["residual_vol_pct"] == 0.0


def test_a_missing_covariance_is_an_error_never_an_empty_answer():
    rows = [{"sector_id": "a", "actual_eur": 1000.0, "etf": "A.DE", "unrealized_pct": 1.0}]
    out = bm.build(rows, {}, {"best_moves": {}}, capital=10_000.0, cash_eur=0.0, n_target=6,
                   slots=6, neutral_eur=1_250.0, ic=0.06, sigma_cs=0.12, horizon_days=63,
                   realized_ytd=0.0, spread_bps={}, default_bps=20.0, eligible={"b"},
                   vehicle_fn=lambda s: f"{s.upper()}.DE",
                   sigma_fn=lambda *a: {"error": "no cached prices"})
    assert "error" in out and "no cached prices" in out["error"]
    assert bm.render(out)[-1].startswith("BEST MOVES (column)  unavailable")


def _built(ic_se):
    rows = [{"sector_id": "hi", "actual_eur": 3_000.0, "etf": "HI.DE", "unrealized_pct": 0.0}]
    s_hi, s_lo, s_m = 0.40, 0.15, 0.16
    # weekly Σ ×52 inside build: feed weekly variances so annual = the numbers above
    wk = lambda x: x / 52.0
    names = ["HI.DE", "LO.DE", "SPY"]
    cov = [[wk(s_hi**2), 0.0, wk(0.5 * s_hi * s_m)],
           [0.0, wk(s_lo**2), wk(0.5 * s_lo * s_m)],
           [wk(0.5 * s_hi * s_m), wk(0.5 * s_lo * s_m), wk(s_m**2)]]
    return bm.build(rows, {"hi": {"composite_z": 1.0}, "lo": {"composite_z": 0.2}},
                    {"best_moves": {"equilibrium_erp": 0.05}}, capital=10_000.0, cash_eur=0.0,
                    n_target=6, slots=6, neutral_eur=1_250.0, ic=0.06, sigma_cs=0.12,
                    horizon_days=63, realized_ytd=0.0, spread_bps={}, default_bps=10.0,
                    eligible={"lo"}, vehicle_fn=lambda s: f"{s.upper()}.DE", ic_se=ic_se,
                    sigma_fn=lambda *a: {"sigma": cov, "tickers": names, "weeks": 56,
                                         "imputed": [False] * 3})


def test_every_move_carries_its_own_standard_error():
    m = _built(0.2)["top"][0]
    assert m["net_se_eur"] > 0 and m["t"] == pytest.approx(m["net_eur"] / m["net_se_eur"], abs=0.02)


def test_a_looser_ic_widens_the_error_and_never_changes_the_net():
    a, b = _built(0.05)["top"][0], _built(0.40)["top"][0]
    assert b["net_se_eur"] > a["net_se_eur"] and b["net_eur"] == a["net_eur"]


def test_the_se_does_not_choose_the_plan():
    # Expected utility picks the move; the SE only says how far to trust it.
    assert _built(5.0)["steps"] == _built(0.0)["steps"]


def test_cluster_share_is_the_euler_contribution_and_sums_to_one_for_a_single_cluster():
    sig = {"a": {"a": 0.04, "b": 0.0}, "b": {"a": 0.0, "b": 0.01}}
    sh = bm.cluster_shares({"a": 1_000.0, "b": 1_000.0}, sig, 10_000.0,
                           {"a": {"x"}, "b": {"x"}})
    assert sh["x"] == pytest.approx(100.0, abs=0.1)
    sh = bm.cluster_shares({"a": 1_000.0, "b": 1_000.0}, sig, 10_000.0, {"a": {"x"}, "b": {"y"}})
    assert sh["x"] == pytest.approx(80.0, abs=0.1) and sh["y"] == pytest.approx(20.0, abs=0.1)


def _rp(budget, k_grid=(1, 2, 4, 8), fee=1.0):
    # hi is the only high-vol line and carries almost all the variance; lo diversifies.
    sigma = _two(s_hi=0.40, s_lo=0.15)
    mu = _mu({"hi": 1.0, "lo": 1.0}, ic=0.0)
    drivers = {"hi": {"x"}, "lo": {"y"}}
    return bm.risk_price({"hi": 3_000.0, "lo": 1_000.0}, {"new": True}, {**mu, "new": mu["lo"]},
                         {**sigma, "new": {**sigma["lo"], "new": 0.0225}, "hi": {**sigma["hi"], "new": 0.0},
                          "lo": {**sigma["lo"], "new": 0.0}},
                         2.0, 63 / 252, 10_000.0, lambda *a: 1.0, 6, 6, 1_250.0, 0.0,
                         drivers, budget, list(k_grid), fee)


def test_a_higher_risk_price_buys_a_calmer_book_and_costs_return():
    rp = _rp(budget=10.0)
    first, last = rp["frontier"][0], rp["frontier"][-1]
    assert last["vol_after_pct"] <= first["vol_after_pct"]
    assert last["d_return_eur"] <= first["d_return_eur"] <= 0.0 + 1e-9


def test_the_budget_is_solved_for_never_imposed():
    # An unreachable budget does not raise and does not invent a plan: it says so, and still
    # names the closest point on the frontier.
    rp = _rp(budget=0.5)
    assert rp["unreachable"] is True and rp["k_star"] is None and rp["closest_k"] is not None


def test_extra_trades_are_a_price_not_a_wall():
    sigma = _two(s_hi=0.40, s_lo=0.15)
    mu = _mu({"hi": 1.0, "lo": 1.0}, ic=0.0)
    wall = _plan({"hi": 3_000.0}, ["lo"], mu, sigma, 2.0, slots=1)
    soft = bm.plan({"hi": 3_000.0}, {"lo": True}, mu, sigma, 2.0, H, 10_000.0,
                   lambda *a: 0.0, 1, 6, 1_250.0, 0.0, over_slot_fee_eur=1.0)
    assert wall["steps"] == [] and len(soft["steps"]) == 1
    assert soft["steps"][0]["cost_eur"] == pytest.approx(1.0)       # one leg beyond the free slot
