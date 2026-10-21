"""OVERWEIGHT ORIGIN + REACHABILITY: columns that move nothing (v15.8)."""
import math

from catalyx.execution.rebalance import (_render_overweight_origin, overweight_decomposition,
                                         reachability)


def _row(sid, target, actual):
    return {"sector_id": sid, "target_pct": target, "actual_pct": actual,
            "actual_eur": actual * 100, "gap_pp": round(target - actual, 2)}


def test_origin_splits_exactly_into_position_and_target_moved():
    now = [_row("bio", 13.41, 17.6)]
    (d,) = overweight_decomposition(now, {"bio": {"target_pct": 17.5, "as_of": "2026-10-06"}})
    assert d["over_pp"] == 4.19
    assert d["from_position_pp"] == 0.1 and d["from_target_pp"] == 4.09
    assert math.isclose(d["from_position_pp"] + d["from_target_pp"], d["over_pp"], abs_tol=0.01)
    assert d["anchor"] == "2026-10-06"


def test_origin_a_price_rise_shows_up_as_position():
    (d,) = overweight_decomposition([_row("w", 10.0, 14.0)], {"w": {"target_pct": 10.0}})
    assert d["from_position_pp"] == 4.0 and d["from_target_pp"] == 0.0


def test_origin_without_an_anchor_says_unknown():
    (d,) = overweight_decomposition([_row("x", 10, 14)], {})
    assert d["from_position_pp"] is None and d["anchor"] is None


def test_origin_ignores_lines_not_held():
    assert overweight_decomposition([{**_row("x", 10, 0), "actual_eur": 0}], {}) == []


def test_reachability_sums_the_deferred_buys_and_carries_the_note():
    slots = {"deferred": [{"sector_id": "ai", "trade_eur": 1500.0},
                          {"sector_id": "ph", "trade_eur": 1000.0}]}
    r = reachability(slots, [], 10_000)
    assert r["unreachable_eur"] == 2500.0 and r["unreachable_pct"] == 25.0
    assert r["excluded"] == [] and r["reallocated_eur"] == 0.0
    r2 = reachability({}, [], 10_000, {"excluded": {"ph", "ai"}, "eur": 2500.0})
    assert r2["excluded"] == ["ai", "ph"] and r2["reallocated_pct"] == 25.0


def test_render_names_the_origin_and_the_unreachable_book():
    origin = overweight_decomposition([_row("bio", 13.41, 17.6)],
                                      {"bio": {"target_pct": 17.5, "as_of": "2026-10-06"}})
    out = "\n".join(_render_overweight_origin(
        origin, reachability({"deferred": [{"sector_id": "ai", "trade_eur": 2652.0}]}, [], 10_000),
        None))
    assert "target moved since +4.1" in out and "UNREACHABLE" in out and "ai" in out


def test_line_risk_shares_are_euler_and_sum_to_the_book():
    from catalyx.execution.best_moves import line_risk_shares
    sig = {"a": {"a": 0.09, "b": 0.01}, "b": {"a": 0.01, "b": 0.01}}
    sh = line_risk_shares({"a": 1000.0, "b": 1000.0}, sig, 10_000)
    assert math.isclose(sum(sh.values()), 100.0, abs_tol=0.2)
    assert sh["a"] > 80 > sh["b"], "the volatile line carries most of the variance at equal euros"
    assert line_risk_shares({}, sig, 10_000) == {}
