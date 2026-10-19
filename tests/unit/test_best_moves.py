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


# ── v15.5 — the 63d scorer ────────────────────────────────────────────────────

from catalyx.execution import rebalance as rb  # noqa: E402

_SC = {"scorecard": {"horizon_days": 63, "min_n": 2, "min_effective_windows": 1}}


def _sm(pred, real, in_plan=True, as_of="2026-01-05"):
    return {"predicted_eur": pred, "realized_eur": real, "in_plan": in_plan, "as_of": as_of}


def test_a_scorecard_with_no_data_is_empty_not_a_crash():
    sc = rb.best_moves_scorecard([], _SC)
    assert sc["rows"] == [] and sc["n_scored"] == 0


def test_error_inside_its_own_noise_reads_calibrated_and_outside_it_does_not():
    ok = rb.best_moves_scorecard([_sm(10, 12), _sm(10, 8), _sm(10, 13), _sm(10, 7)], _SC)
    assert ok["rows"][0]["verdict"].startswith("calibrated")
    bad = rb.best_moves_scorecard([_sm(10, -20), _sm(10, -21), _sm(10, -19), _sm(10, -20)], _SC)
    assert bad["rows"][0]["verdict"] == "over-predicted"
    under = rb.best_moves_scorecard([_sm(1, 30), _sm(1, 31), _sm(1, 29), _sm(1, 30)], _SC)
    assert under["rows"][0]["verdict"] == "under-predicted"


def test_one_independent_window_is_not_enough_when_two_are_required():
    sc = rb.best_moves_scorecard([_sm(1, 1)] * 6, {"scorecard": {"min_n": 2,
                                                                   "min_effective_windows": 2}})
    assert "not scoreable" in sc["rows"][0]["verdict"] and sc["effective_windows"] == 1


def test_in_plan_and_out_of_plan_are_kept_apart():
    sc = rb.best_moves_scorecard([_sm(5, 5), _sm(5, 5), _sm(5, -5, in_plan=False),
                                  _sm(5, -5, in_plan=False)], _SC)
    assert [r["group"] for r in sc["rows"]] == ["in plan", "not in plan"]
    assert sc["rows"][0]["hit_pct"] == 100.0 and sc["rows"][1]["hit_pct"] == 0.0


def _persist_moves(tmp_path, run, as_of, moves):
    import pandas as pd

    from catalyx.store import lake
    lake.append_partition("rebalance_best_moves", pd.DataFrame(
        [{"run_id": run, "as_of": as_of, "rank": i + 1, **m} for i, m in enumerate(moves)]),
        {"run_id": run}, overwrite=True, lake_dir=tmp_path)


def test_an_open_window_costs_no_price_fetch_and_is_pending(tmp_path):
    _persist_moves(tmp_path, "run_20260828_000000", "2026-08-28", [
        {"kind": "swap", "sell": "a", "buy": "b", "sell_etf": "A.DE", "buy_etf": "B.DE",
         "eur": 1000.0, "in_plan": True, "d_premium_eur": 1.0, "d_alpha_eur": 1.0}])

    def boom(*a, **k):
        raise AssertionError("no complete window - must not fetch")
    res = rb.score_best_moves(lake_dir=tmp_path, as_of="2026-09-10", price_fn=boom)
    assert res["scored"] == [] and "window open" in res["pending"][0]["status"]


def test_a_swap_is_priced_as_buy_minus_sell_and_a_cash_buy_against_cash(tmp_path):
    import pandas as pd

    _persist_moves(tmp_path, "run_20260105_000000", "2026-01-05", [
        {"kind": "swap", "sell": "a", "buy": "b", "sell_etf": "A.DE", "buy_etf": "B.DE",
         "eur": 1000.0, "in_plan": True, "d_premium_eur": 2.0, "d_alpha_eur": 1.0},
        {"kind": "buy", "sell": None, "buy": "b", "sell_etf": None, "buy_etf": "B.DE",
         "eur": 500.0, "in_plan": False, "d_premium_eur": 4.0, "d_alpha_eur": 0.0}])
    idx = pd.to_datetime(["2026-01-05", "2026-03-06"])
    px = pd.DataFrame({"A.DE": [100.0, 105.0], "B.DE": [50.0, 60.0]}, index=idx)
    fn = lambda tickers, start, end: px
    res = rb.score_best_moves(lake_dir=tmp_path, as_of="2026-04-20", price_fn=fn,
                              ccy_fn=lambda t: {x: "EUR" for x in t}, fx_fn=lambda *a: {})
    got = {(s["kind"]): s["realized_eur"] for s in res["scored"]}
    assert got["swap"] == pytest.approx(1000 * (0.20 - 0.05))
    assert got["buy"] == pytest.approx(500 * 0.20)
    assert res["pending"] == []


def test_build_stamps_the_vehicle_of_each_leg_on_the_move():
    m = _built(0.2)["top"][0]
    assert m["buy_etf"] == "LO.DE" and m["sell_etf"] == "HI.DE"


# ── v15.5 — what the dashboard reads ──────────────────────────────────────────

def _result_with(bm_out):
    return {"run_id": "run_20261019_000000", "strategy": "catalyx", "as_of": "2026-10-19",
            "best_moves": bm_out, "diversifiers": {"lo": {"role": "diversifier",
                                                           "as_of": "2026-10-19"}}}


def test_risk_price_and_diversifier_health_persist_one_row_per_k_and_per_line(tmp_path):
    from catalyx.store import lake
    out = _rp(budget=10.0)
    bm_out = {"risk_price": out,
              "diversifier_health": bm.diversifier_health(
                  {"hi": 3_000.0, "lo": 1_000.0}, _two(s_hi=0.40, s_lo=0.15), 10_000.0, {"lo"}, 56)}
    n = rb._persist_risk_price(_result_with(bm_out), "t", lake_dir=tmp_path)
    assert n == len(out["frontier"]) + 1
    rp = lake.read_table("rebalance_risk_price", lake_dir=tmp_path)
    assert list(rp["k"]) == [r["k"] for r in out["frontier"]] and rp["budget_pct"].iloc[0] == 10.0
    dv = lake.read_table("rebalance_diversifier", lake_dir=tmp_path)
    assert list(dv["sector_id"]) == ["lo"] and dv["role_as_of"].iloc[0] == "2026-10-19"


def test_an_errored_column_persists_neither_table(tmp_path):
    assert rb._persist_risk_price(_result_with({"error": "no cached prices"}), "t",
                                  lake_dir=tmp_path) == 0
    assert not list(tmp_path.rglob("*.parquet"))
