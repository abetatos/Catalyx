"""`max_position_pct` is derived on TOTAL capital; model weights live on the DEPLOYED book.

The bug these tests pin (v13.2): the derived ceiling was applied straight to a deployed-base
weight, landing 1.050 × neutral instead of the tier table's 1.400 × — so every sizing step
(softmax, λ, the CA tilt, the v13 risk budget) was flattened to equal weight by arithmetic.
"""
from __future__ import annotations

import copy

import pytest
import yaml

from catalyx.config import weights as wcfg
from catalyx.execution import portfolio as pf
from catalyx.execution import rebalance as rb


def test_the_two_bases_are_the_same_ceiling_in_different_units():
    bs = wcfg.book_shape()
    assert bs["max_position_pct_deployed"] == pytest.approx(
        bs["max_position_pct"] / bs["deploy_max"], abs=0.01)
    # and deploy_max cancels: the deployed ceiling is just tier-1's multiple of an equal book
    assert bs["max_position_pct_deployed"] == pytest.approx(
        100.0 * bs["tier_multiples"][0] / bs["n_target"], abs=0.01)


def test_the_ceiling_keeps_the_room_over_neutral_the_tier_table_declares():
    """THE DEFECT, stated as arithmetic. Equal weight on n_target names is 100/n of the deployed
    book; tier 1 must sit `tier_multiples[0]` × above it, not 1.05 × above it."""
    bs = wcfg.book_shape()
    neutral_deployed = 100.0 / bs["n_target"]
    assert bs["max_position_pct_deployed"] / neutral_deployed == pytest.approx(
        bs["tier_multiples"][0], abs=1e-3)   # the deployed cap is rounded to 2dp
    # what shipped before the fix, kept as the failure it was
    assert bs["max_position_pct"] / neutral_deployed < 1.10


def test_cap_frac_converts_only_a_cap_declared_on_total_capital():
    dmax = wcfg.deploy_max()
    assert pf.cap_frac({"max_position_pct": 17.5, "max_position_basis": "total"}) == \
        pytest.approx(0.175 / dmax)
    assert pf.cap_frac({"max_position_pct": 12}) == pytest.approx(0.12)          # default
    assert pf.cap_frac({"max_position_pct": 12, "max_position_basis": "deployed"}) == \
        pytest.approx(0.12)


def test_the_baseline_books_are_not_silently_rescaled():
    """Their caps were hand-picked against deployed weights. Converting them would change what
    the counterfactual measures — the one thing a control book may not do."""
    for pid in ("equal_weight", "low_crowding", "momentum"):
        c = pf.load_profile(pid)["construction"]
        assert "max_position_basis" not in c
        assert pf.cap_frac(c) == pytest.approx(float(c["max_position_pct"]) / 100.0)


def test_the_flagship_book_stops_coming_out_equal_weighted():
    """The observable symptom the user reported: six names inside a 2pp band, every sizing step
    invisible. With the cap on its own base the ranking reaches the weights again."""
    out = pf.build_model_holdings("catalyx", persist=False)
    if "error" in out or len(out["holdings"]) < 3:
        pytest.skip("no lake snapshot in this environment")
    w = sorted(h["weight_pct"] for h in out["holdings"])
    assert w[-1] - w[0] > 4.0
    assert w[-1] <= wcfg.book_shape()["max_position_pct_deployed"] + 1e-6


def test_the_close_re_cap_uses_the_same_base_as_the_build():
    """`close_target_weights` re-applies the cap after rescaling. Handing it the total-capital
    number there would undo the fix one step downstream, silently."""
    prof = pf.load_profile("catalyx")["construction"]
    cfg_cap = wcfg.rebalance_rules()["max_position_pct"]
    close_cap = pf.cap_frac({**prof, "max_position_pct": cfg_cap}) * 100.0
    assert close_cap == pytest.approx(wcfg.book_shape()["max_position_pct_deployed"], abs=0.01)

    # and it is the cap the re-cap actually enforces
    n = 6
    res = rb.close_target_weights([90.0] + [2.0] * (n - 1), close_cap)
    assert max(res["weights"]) <= close_cap + 1e-6


def test_a_cap_on_the_wrong_base_would_flatten_the_book():
    """Regression in the direction of the bug: the same scores under the total-capital number
    come out inside a band no sizing rule chose."""
    prof = yaml.safe_load(open("catalyx/config/portfolios/catalyx.yaml"))
    bad = copy.deepcopy(prof)
    bad["construction"].pop("max_position_basis", None)
    out = pf.build_model_holdings("catalyx", persist=False, profile=bad)
    if "error" in out or len(out["holdings"]) < 6:
        pytest.skip("no lake snapshot in this environment")
    w = sorted(h["weight_pct"] for h in out["holdings"])
    assert w[-1] - w[0] < 3.0
