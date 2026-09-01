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

import pytest
import yaml

from catalyx.config import weights
from catalyx.execution import portfolio, rebalance


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
        assert round(float(raw["conviction_tiers"][tier]["max_position_pct"]) * 100.0, 1) == pct


def test_model_book_is_executable_under_the_real_books_rules():
    """A model book the rebalance table would refuse to execute measures a ceiling no
    disciplined executor could reach, and its execution alpha is partly fiction (v6 J1)."""
    c = portfolio.load_profile("catalyx")["construction"]
    bs = weights.book_shape()
    assert c["max_position_pct"] <= bs["max_position_pct"]
    assert c["max_positions"] <= bs["n_target"]


# ── v10 — the two new limits must be satisfiable by the book they govern ─────

def test_the_target_book_can_absorb_the_deploy_rule():
    """`absorbable_eur` (v10 P2) lowers the shortfall bar to what the book can HOLD. That is a
    correction for the case where too few names qualify — it must never be the NORMAL state, or
    the deployment rule would quietly stop binding on a full book. At n_target names under the
    position ceiling, the book must still be able to absorb everything the deploy rule asks for.
    """
    bs = weights.book_shape()
    reachable = bs["n_target"] * bs["max_position_pct"] / 100.0
    assert reachable >= bs["deploy_max"], (
        f"{bs['n_target']} names at {bs['max_position_pct']}% reach {reachable:.0%}, under the "
        f"{bs['deploy_max']:.0%} deploy ceiling — a FULL book would read as under-deployed, and "
        f"P2's relaxation would fire every run instead of only when the gate cuts the book")


def test_the_risk_budget_admits_a_single_position():
    """The v6 L2 failure, in variance units. A budget below the share ONE name carries in an
    equal-risk book of n_target would be breached the day any position is opened — infeasible,
    permanently breached, therefore permanently ignored."""
    bs = weights.book_shape()
    budget = weights.correlated_catalyst_cap()["max_cluster_variance_pct"]
    one_name = 100.0 / bs["n_target"]
    assert budget > one_name, (
        f"risk budget {budget}% is at or below the {one_name:.1f}% a single name carries in an "
        f"equal-risk {bs['n_target']}-name book")


def test_the_risk_budget_is_not_stricter_than_the_notional_cap_it_runs_beside():
    """Both run for a cycle so their DISAGREEMENT can be read. A variance budget tighter than
    the notional cap's equal-risk equivalent would simply supersede it, and there would be
    nothing to compare — the notional rule would never be the binding one on any book."""
    ccc = weights.correlated_catalyst_cap()
    assert ccc["max_cluster_variance_pct"] >= ccc["max_combined_pct"], (
        f"variance budget {ccc['max_cluster_variance_pct']}% is tighter than the notional cap "
        f"{ccc['max_combined_pct']}% under equal risk — the notional cap could never bind first")


def test_both_caps_ship_unarmed_while_the_evidence_is_collected():
    """A new limit that moves money in the same edit that introduces it has been read against
    no live table. Both stay `warn` until two runs of evidence say which one survives."""
    ccc = weights.correlated_catalyst_cap()
    assert ccc["enforcement"] == "warn"
    assert ccc["risk_enforcement"] == "warn"


def test_the_conviction_gate_cannot_empty_the_book():
    """A floor above the level a leader scores would cut every name and hand the whole book to
    cash — the opposite failure to the one P1 exists to fix, and just as silent."""
    c = portfolio.load_profile("catalyx")["construction"]
    gate = c.get("entry_gate") or {}
    if not gate.get("enabled", True) or gate.get("min_catalyst_alignment") is None:
        return
    floor = float(gate["min_catalyst_alignment"])
    assert 0 < floor < 70, (
        f"catalyst_alignment floor {floor} is outside the sane band: at or below 0 it gates "
        f"nothing, at 70+ it starts cutting names the composite calls leaders")


# ── Buyability: every investable sector must resolve to a vehicle ────────────────
#
# `primary_etf` filtered on `ucits: true` from 2026-08-28 (the IBB incident) until
# 2026-09-01. UCITS is not what makes a vehicle buyable — a PRIIPs KID is — and a
# single-metal fund cannot legally BE UCITS, so gold_physical and silver_physical
# resolved to None for months: no ETF in the ranking table, ineligible for a BUY,
# while the fund was visible in the broker. These tests fail the suite the next time
# an investable sector loses its vehicle, instead of it surfacing as a dash in a table.

def test_every_investable_sector_resolves_to_a_buyable_vehicle():
    from catalyx.data.market_data import SECTOR_TICKERS
    from catalyx.store.snapshot_repo import primary_etf

    missing = [sid for sid in SECTOR_TICKERS if not primary_etf(sid)]
    assert not missing, f"investable sectors with no buyable vehicle: {missing}"


def test_the_scored_vehicle_is_the_recommended_vehicle():
    """Momentum is measured on `SECTOR_TICKERS[sid][0]`; the table recommends
    `primary_etf(sid)`. Scoring one instrument and buying another measures a return you
    do not obtain — the same defect as the v2.0 universe cut (COPX scored, 4COP bought)."""
    from catalyx.data.market_data import SECTOR_TICKERS
    from catalyx.store.snapshot_repo import primary_etf

    diverged = {sid: (primary_etf(sid), chain[0])
                for sid, chain in SECTOR_TICKERS.items()
                if primary_etf(sid) != chain[0]}
    assert not diverged, f"universe vehicle != scored vehicle: {diverged}"


def test_a_us_listing_never_resolves_whatever_it_claims(tmp_path, monkeypatch):
    """The guard the UCITS filter was reaching for: venue decides, not the flags."""
    from catalyx.store import snapshot_repo

    f = tmp_path / "etf_universe.yaml"
    f.write_text(
        "etf_universe:\n"
        "  fake_sector:\n"
        "    - ticker: 'IBB'\n"
        "      exchange: NASDAQ\n"
        "      ucits: true\n"          # even a lying flag must not get it through
        "      instrument: etf\n"
        "      recommendation_tier: 1\n", encoding="utf-8")
    monkeypatch.setattr(snapshot_repo, "_ETF_PATH", f)
    assert snapshot_repo.primary_etf("fake_sector") is None


def test_an_eu_listed_etc_resolves(tmp_path, monkeypatch):
    """A physical-metal ETC is `ucits: false` by legal necessity and is still buyable."""
    from catalyx.store import snapshot_repo

    f = tmp_path / "etf_universe.yaml"
    f.write_text(
        "etf_universe:\n"
        "  fake_gold:\n"
        "    - ticker: 'PPFB.DE'\n"
        "      exchange: XETRA\n"
        "      ucits: false\n"
        "      instrument: etc\n"
        "      recommendation_tier: 1\n", encoding="utf-8")
    monkeypatch.setattr(snapshot_repo, "_ETF_PATH", f)
    assert snapshot_repo.primary_etf("fake_gold") == "PPFB.DE"


def test_the_deploy_ceiling_is_reachable_by_the_book_that_is_counted():
    """v12 B3 — `deploy_max` is not free-standing: `book_shape` reads the neutral weight, every
    conviction tier and `max_position_pct` off it. It used to take the step term from
    `intact_rank_max` (8) while `rebalance.py` counts `n_intact` over the MODEL BOOK, which
    holds at most `n_target` = 6 — so the whole ladder rested on a deployment ratio the counter
    could never produce (0.85 declared, 0.75 reachable), and `max_position_pct` shipped 2.5pp
    too high. `intact_rank_max: 8` was a leftover from a 10-name book.

    This asserts the identity against `deploy_ratio` itself rather than re-deriving it, so the
    two cannot drift again: a FULL, fully-intact book, no VIX brake, must reach exactly the
    ceiling the position cap is derived from. The other feasibility tests all passed while this
    was broken because they only ever compared derived values to each other.
    """
    bs = weights.book_shape()
    cfg = weights.rebalance_rules()
    full = rebalance.deploy_ratio(bs["n_target"], None, cfg)["ratio"]
    assert full == pytest.approx(bs["deploy_max"]), (
        f"a full intact {bs['n_target']}-name book deploys {full:.0%}, but the cap ladder is "
        f"derived from deploy_max {bs['deploy_max']:.0%} — the ceiling is unreachable")


def test_every_rank_ceiling_is_reachable():
    """v12 B4 — a `rank_max` above `max_positions` is dead config. `decide_action` is handed
    `rank_in_portfolio`, which runs 1..max_positions (6), so `buy_if.rank_max: 8` passed every
    member of the model book and filtered nothing. It was written for a 10-name book and was
    never re-read when v6 L1 cut the book to 6; CLAUDE.md still cites it as a live constraint.
    """
    cfg = weights.rebalance_rules()
    n_max = int(portfolio.load_profile("catalyx")["construction"]["max_positions"])
    for rule in ("buy_if", "add_if"):
        rm = (cfg.get(rule) or {}).get("rank_max")
        if rm is None:
            continue
        assert int(rm) <= n_max, (
            f"{rule}.rank_max = {rm} but rank_in_portfolio never exceeds max_positions "
            f"({n_max}) — the ceiling can never bind")
