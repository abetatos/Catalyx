"""The config must be jointly SATISFIABLE, not just individually reasonable (plan v6 L5).

Three constants were tuned independently for years and were linked by identities nobody had
written down:

    n_min_positions = deploy_max / max_position_pct
    n_min_drivers   = deploy_max / correlated_catalyst_cap

At the frozen values (deploy_max 0.85, cap 12%) the config demanded an 8-position book while
the operator has 10 free trades a month — and the same identity is why the catalyst cap was
breached every single run. Each number looked defensible alone; the triple was infeasible.
These tests fail the suite the next time that happens, instead of it surfacing fifteen reviews
later as a permanent breach nobody can act on.
"""
from __future__ import annotations

import math

import yaml

from catalyx.config import weights
from catalyx.execution import portfolio


def test_position_cap_admits_the_target_book():
    """The cap must not demand MORE positions than the book is built for."""
    bs = weights.book_shape()
    n_min = math.ceil(bs["deploy_max"] * 100.0 / bs["max_position_pct"])
    assert n_min <= bs["n_target"], (
        f"cap {bs['max_position_pct']}% forces >= {n_min} positions to deploy "
        f"{bs['deploy_max']:.0%}, but n_target is {bs['n_target']}")


def test_building_the_book_fits_the_monthly_trade_budget():
    """Opening the target book plus the event reserve must fit in the free allowance —
    the reserve is the whole point: an event-driven mandate that spends every slot on its
    scheduled rebalance cannot act on what arrives between reviews."""
    bs = weights.book_shape()
    tb = bs["trade_budget"]
    assert bs["n_target"] + tb["reserve_for_events"] <= tb["free_per_month"]
    assert tb["planned_max_per_review"] + tb["reserve_for_events"] <= tb["free_per_month"]


def test_a_single_average_position_fits_under_the_catalyst_cap():
    """The driver cap must at minimum admit one neutral-weight position.

    At 20% it admitted exactly one (14.2% neutral), i.e. no two positions could share a driver
    — which is why AI capex breached every run. v6 L2 raised it to 30% once I2 could publish
    risk per cluster. This test guards the floor either way.
    """
    bs = weights.book_shape()
    cap = weights.correlated_catalyst_cap()["max_combined_pct"]
    assert cap >= bs["neutral_weight_pct"], (
        f"catalyst cap {cap}% is below the neutral weight {bs['neutral_weight_pct']}% — a "
        f"single average position would breach it on the day it is opened")


def test_the_driver_cap_admits_two_neutral_positions():
    """The v6 L1 diagnosis, as an invariant. A themed book of 6 names cannot hold 6 unrelated
    drivers; a cap that forbids any two names from sharing one is not a discipline, it is a
    permanent breach, and a permanently breached cap is a permanently ignored one."""
    bs = weights.book_shape()
    cap = weights.correlated_catalyst_cap()["max_combined_pct"]
    assert cap >= 2 * bs["neutral_weight_pct"], (
        f"catalyst cap {cap}% admits only {cap / bs['neutral_weight_pct']:.1f} neutral "
        f"positions per driver — at n_target={bs['n_target']} that forbids two names from "
        f"sharing a driver, which no themed book can satisfy")


def test_the_driver_cap_still_binds_on_a_concentrated_book():
    """Raising a cap must not make it decorative: it has to be reachable by a plausible book.
    Two tier-1 positions on one driver must still breach."""
    bs = weights.book_shape()
    cap = weights.correlated_catalyst_cap()["max_combined_pct"]
    assert 2 * bs["max_position_pct"] > cap, (
        f"two positions at the {bs['max_position_pct']}% ceiling sum to "
        f"{2 * bs['max_position_pct']}%, under the {cap}% cap — the cap can never fire")


def test_yaml_mirrors_the_derived_values():
    """The derived numbers are duplicated into the YAML so they stay greppable. Duplication is
    fine only while it cannot drift silently — that is this test."""
    bs = weights.book_shape()
    assert weights.rebalance_rules()["max_position_pct"] == bs["max_position_pct"]
    raw = yaml.safe_load(weights._WEIGHTS_PATH.read_text(encoding="utf-8"))
    for tier, pct in weights.conviction_tiers().items():
        assert round(float(raw["conviction_tiers"][tier]["max_position_pct"]) * 100.0) == pct


def test_model_book_is_executable_under_the_real_books_rules():
    """A model book the rebalance table would refuse to execute measures a ceiling no
    disciplined executor could reach, and its execution alpha is partly fiction (v6 J1)."""
    c = portfolio.load_profile("catalyx")["construction"]
    bs = weights.book_shape()
    assert c["max_position_pct"] <= bs["max_position_pct"]
    assert c["max_positions"] <= bs["n_target"]
