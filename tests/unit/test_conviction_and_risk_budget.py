"""v10 P1/P2/P3/P6/P7 — where we invest, where we do NOT, and when cash is the answer.

The book had exactly one way to be small: fail to deploy. Every mechanism that could shrink it
— the position cap, the caps' arithmetic, a name nobody wanted — arrived at the same place, a
deployment shortfall the persistence rule re-opened every review. There was no way to say "this
sector stopped being worth owning", no way to say "four names is the right number this month",
and no way to say "I want cash until X" that survived a single cycle.

These tests pin the four answers and the one thing they must never become: a hiding place for
cash. Every one of them checks BOTH directions — the gate excludes AND the excluded name is
reported, the shortfall relaxes AND the raw gap stays printed, the stance silences AND it
expires.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import copy

import pytest

from catalyx.execution import portfolio, rebalance


# ── P1 — the conviction gate ────────────────────────────────────────────────

def _universe():
    return pd.DataFrame([
        {"sector_id": "leader", "catalyst_alignment": 95.0, "composite": 64.0},
        {"sector_id": "driverless", "catalyst_alignment": 13.9, "composite": 46.6},
        {"sector_id": "measured_zero", "catalyst_alignment": 0.0, "composite": 33.0},
        {"sector_id": "unstudied", "catalyst_alignment": 50.0, "composite": 48.0,
         "ca_imputed": True},
    ])


def test_the_gate_excludes_a_measured_low_catalyst_alignment():
    """The whole point: a name with no live driver is not bought on price alone.

    `eu_retail_banking` is the live case this is written from — momentum 86.5 with CA 13.9,
    ranked 17th today and one good quarter from `buy_if`'s rank-8 ceiling.
    """
    kept, excluded = portfolio.conviction_gate(
        _universe(), {"entry_gate": {"min_catalyst_alignment": 40}})
    assert set(kept["sector_id"]) == {"leader", "unstudied"}
    assert {e["sector_id"] for e in excluded} == {"driverless", "measured_zero"}


def test_a_measured_zero_is_gated_but_an_imputed_score_is_not():
    """The one inference v5/v6 spent two versions removing: "not measured" must never become
    "fails". A study that FOUND no catalysts keeps its zero — that zero was observed."""
    _, excluded = portfolio.conviction_gate(
        _universe(), {"entry_gate": {"min_catalyst_alignment": 40}})
    ids = {e["sector_id"] for e in excluded}
    assert "measured_zero" in ids, "a measured 0.0 is evidence and must be gated"
    assert "unstudied" not in ids, "an IMPUTED CA must pass — it is not a measurement"


def test_every_exclusion_is_reported_with_its_number():
    """A filter that drops a name silently is indistinguishable from a bug."""
    _, excluded = portfolio.conviction_gate(
        _universe(), {"entry_gate": {"min_catalyst_alignment": 40}})
    for e in excluded:
        assert e["catalyst_alignment"] is not None
        assert str(e["min_catalyst_alignment"]) in e["reason"] or "40" in e["reason"]


def test_the_gate_is_off_by_default_and_when_disabled():
    """No profile is silently re-cut by a key it never declared."""
    df = _universe()
    assert len(portfolio.conviction_gate(df, {})[0]) == len(df)
    assert len(portfolio.conviction_gate(
        df, {"entry_gate": {"enabled": False, "min_catalyst_alignment": 90}})[0]) == len(df)


def test_the_live_profile_gate_does_not_re_cut_todays_book():
    """The floor was read off the measured distribution to state the criterion WITHOUT moving
    a table the user already decided on. If a config edit ever changes that, this fails and the
    change has to be argued rather than discovered in a live review."""
    c = portfolio.load_profile("catalyx")["construction"]
    assert c["entry_gate"]["min_catalyst_alignment"] == 40
    built = portfolio.build_model_holdings("catalyx", persist=False)
    # The property is "the gate does not move the book", not "nobody fails the floor": a name
    # ranked below the cut can fail it (oil_majors, CA 32.9) without touching a single holding.
    # Asserting n_excluded == 0 tied the test to the live tail of the ranking.
    ungated = copy.deepcopy(portfolio.load_profile("catalyx"))
    ungated["construction"]["entry_gate"]["enabled"] = False
    baseline = portfolio.build_model_holdings("catalyx", persist=False, profile=ungated)
    book = lambda b: sorted((h["sector_id"], h["weight_pct"]) for h in b["holdings"])
    assert book(built) == book(baseline)
    assert built["positions"] == c["max_positions"]


def test_fewer_eligible_names_becomes_cash_not_a_concentrated_book():
    """A book of 3 under a 20% ceiling closes at 60% — the freed weight is CASH.

    This is the property that makes "hold fewer names" expressible at all: without it, cutting
    the eligible set just re-concentrates the survivors, which is a scoring problem wearing a
    weighting costume.
    """
    closed = rebalance.close_target_weights([20.0, 20.0, 20.0], max_position_pct=20.0)
    assert closed["pool_pct"] == pytest.approx(60.0, abs=0.01)
    assert not closed["incomplete"]


# ── P2 — the shortfall measures what the book can HOLD ──────────────────────

def test_absorbable_caps_at_what_the_book_can_hold():
    a = rebalance.absorbable_eur(deployable_eur=7500.0, target_total_eur=6000.0)
    assert a["absorbable_eur"] == 6000.0
    assert a["capped_by_book"] is True
    assert a["unreachable_eur"] == 1500.0


def test_absorbable_never_invents_headroom_above_the_deploy_rule():
    """It can only ever LOWER the bar to what is reachable — never raise it. A model book that
    targets more than the deploy rule allows does not get to deploy more."""
    a = rebalance.absorbable_eur(deployable_eur=7500.0, target_total_eur=9000.0)
    assert a["absorbable_eur"] == 7500.0
    assert a["capped_by_book"] is False


def test_the_raw_gap_to_the_deploy_rule_is_always_still_reported():
    """The anti-hiding-place invariant. A rule measurable two ways can be gamed by choosing
    the way, so the number the relaxation could hide must never leave the output."""
    r = rebalance.build()
    assert "shortfall_vs_deployable_pp" in r["shortfall"]
    assert r["shortfall"]["absorbable"]["deployable_eur"] > 0


def test_cash_drag_still_prices_every_idle_euro():
    """Cash the rules themselves created is still cash that costs something."""
    d = rebalance.cash_drag(5000.0, 4.0, "2026-06-01", days=90)
    assert d["forgone_eur"] == 200.0
    assert d["verdict"] == "cost"


# ── P3 — the cash stance: said once, dated, priced, and it EXPIRES ──────────

def _hist(pp: float, n: int = 3):
    return [{"as_of": f"2026-0{i + 1}-01", "shortfall_pp": pp} for i in range(n)]


def _stance(days_ago: int, max_days: int = 90):
    started = date.today() - timedelta(days=days_ago)
    return {"started": started.isoformat(), "author": "user",
            "reason": "Dry powder until the Fed meets.",
            "expires_on": (started + timedelta(days=max_days)).isoformat(),
            "days_left": max_days - days_ago, "age_days": days_ago}


def test_a_breach_with_no_stance_still_breaches():
    s = rebalance.shortfall_status(_hist(48.7), {"deployment": {}})
    assert s["breached"] is True and s["answered_by_stance"] is False


def test_a_live_stance_answers_the_breach_without_deleting_it():
    s = rebalance.shortfall_status(_hist(48.7), {"deployment": {}}, stance=_stance(10))
    assert s["breached"] is False, "the review stops re-asking"
    assert s["breach_raw"] is True, "…but the breach itself is not erased"
    assert s["shortfall_pp"] == 48.7, "…and neither is its size"
    assert "Expires" in s["note"] and "Fed" in s["note"]


def test_a_stance_only_silences_a_rule_it_is_actually_answering():
    """No breach, no silencing — a stance is an answer, not a blanket."""
    s = rebalance.shortfall_status(_hist(1.0), {"deployment": {}}, stance=_stance(10))
    assert s["answered_by_stance"] is False


def test_an_expired_stance_is_not_read_at_all(tmp_path, monkeypatch):
    """Silence is what the shortfall rule exists to break, so a stance that never lapsed would
    rebuild exactly the silence it replaced."""
    from catalyx.store import lake

    old = (date.today() - timedelta(days=200)).isoformat() + "T00:00:00+00:00"
    lake.append_partition(
        "override_log",
        pd.DataFrame([{"run_id": "run_x", "sector_id": rebalance._CASH_STANCE_SECTOR,
                       "rule_action": "DEPLOY", "chosen_action": rebalance._CASH_STANCE_ACTION,
                       "chosen_trade_eur": 0.0, "rule_trade_eur": 4800.0,
                       "reason": "stale stance", "author": "user", "logged_at": old}]),
        {"run_id": "run_x"}, overwrite=True, lake_dir=tmp_path)
    assert rebalance.cash_stance(lake_dir=tmp_path, cfg={"deployment": {}}) is None


def test_a_machine_author_may_not_write_a_cash_stance(tmp_path):
    """Automation excusing its own output answers to nobody."""
    from catalyx.store import lake

    now = date.today().isoformat() + "T00:00:00+00:00"
    lake.append_partition(
        "override_log",
        pd.DataFrame([{"run_id": "run_x", "sector_id": rebalance._CASH_STANCE_SECTOR,
                       "rule_action": "DEPLOY", "chosen_action": rebalance._CASH_STANCE_ACTION,
                       "chosen_trade_eur": 0.0, "rule_trade_eur": 4800.0,
                       "reason": "queued", "author": "ramp", "logged_at": now}]),
        {"run_id": "run_x"}, overwrite=True, lake_dir=tmp_path)
    assert rebalance.cash_stance(lake_dir=tmp_path, cfg={"deployment": {}}) is None


def test_a_live_human_stance_is_found_and_carries_its_expiry(tmp_path):
    from catalyx.store import lake

    now = date.today().isoformat() + "T12:00:00+00:00"
    lake.append_partition(
        "override_log",
        pd.DataFrame([{"run_id": "run_x", "sector_id": rebalance._CASH_STANCE_SECTOR,
                       "rule_action": "DEPLOY", "chosen_action": rebalance._CASH_STANCE_ACTION,
                       "chosen_trade_eur": 0.0, "rule_trade_eur": 4800.0,
                       "reason": "Dry powder.", "author": "user", "logged_at": now}]),
        {"run_id": "run_x"}, overwrite=True, lake_dir=tmp_path)
    st = rebalance.cash_stance(lake_dir=tmp_path, cfg={"deployment": {}})
    assert st is not None and st["author"] == "user"
    assert st["days_left"] == 90 and st["rule_deploy_eur"] == 4800.0


# ── P6/P7 — risk and concentration in the action table ──────────────────────

def _rows():
    return [
        {"sector_id": "held_big", "rule_action": "HOLD", "trade_eur": 0.0, "actual_eur": 2000.0},
        {"sector_id": "adding", "rule_action": "ADD", "trade_eur": 400.0, "actual_eur": 900.0},
        {"sector_id": "selling", "rule_action": "SELL", "trade_eur": -500.0, "actual_eur": 500.0},
    ]


def _cluster(ctr, members, **kw):
    return {"catalyst_id": "struct_x", "risk_ctr_pct": ctr, "over_risk": kw.get("over_risk"),
            "post_pct": kw.get("post_pct", 20.0), "over": kw.get("over", False),
            "rules_disagree": kw.get("rules_disagree", False),
            "sectors": [m[0] for m in members],
            "members_by_risk": [{"sector_id": s, "ctr_pct": c,
                                 "eur": dict(_r_eur())[s]} for s, c in members]}


def _r_eur():
    return {"held_big": 2000.0, "adding": 1300.0, "selling": 500.0}


def test_warn_reports_the_breach_and_changes_no_action(monkeypatch):
    """Arming a new limit in the edit that introduces it would move money on a threshold no
    live table has ever been read against."""
    monkeypatch.setattr("catalyx.config.weights.correlated_catalyst_cap",
                        lambda: {"max_combined_pct": 30.0, "enforcement": "warn",
                                 "max_cluster_variance_pct": 35.0, "risk_enforcement": "warn"})
    rows = _rows()
    out = rebalance.cluster_risk_pass(
        rows, [_cluster(60.0, [("held_big", 40.0), ("adding", 20.0)], over_risk=True)], {})
    assert out["n_breaching"] == 1 and out["armed"] is False
    assert out["rows_changed"] == 0
    assert [r["rule_action"] for r in rows] == ["HOLD", "ADD", "SELL"]
    assert "warn" in out["note"]


def test_block_stops_adds_into_a_cluster_over_its_risk_budget(monkeypatch):
    monkeypatch.setattr("catalyx.config.weights.correlated_catalyst_cap",
                        lambda: {"max_combined_pct": 30.0, "enforcement": "warn",
                                 "max_cluster_variance_pct": 35.0, "risk_enforcement": "block"})
    rows = _rows()
    rebalance.cluster_risk_pass(
        rows, [_cluster(60.0, [("adding", 40.0)], over_risk=True)], {})
    add = next(r for r in rows if r["sector_id"] == "adding")
    assert add["rule_action"] == "HOLD" and add["trade_eur"] == 0.0
    assert "book variance" in add["reason"]


def test_a_risk_limit_never_cancels_a_risk_REMOVAL(monkeypatch):
    """A budget that could veto a SELL would be a limit arguing with itself."""
    monkeypatch.setattr("catalyx.config.weights.correlated_catalyst_cap",
                        lambda: {"max_combined_pct": 30.0, "enforcement": "warn",
                                 "max_cluster_variance_pct": 35.0, "risk_enforcement": "block"})
    rows = _rows()
    rebalance.cluster_risk_pass(
        rows, [_cluster(60.0, [("selling", 50.0)], over_risk=True)], {})
    sell = next(r for r in rows if r["sector_id"] == "selling")
    assert sell["rule_action"] == "SELL" and sell["trade_eur"] == -500.0


def test_the_trim_candidate_must_be_a_position_actually_held(monkeypatch):
    """`members_by_risk` is measured on the POST-trade book, so a name only PROPOSED for
    purchase carries risk there without existing. Trimming it would have the budget cancelling
    a buy and selling the result in the same breath."""
    monkeypatch.setattr("catalyx.config.weights.correlated_catalyst_cap",
                        lambda: {"max_combined_pct": 30.0, "enforcement": "warn",
                                 "max_cluster_variance_pct": 35.0, "risk_enforcement": "block"})
    rows = [{"sector_id": "not_held_yet", "rule_action": "BUY", "trade_eur": 1500.0,
             "actual_eur": 0.0}]
    out = rebalance.cluster_risk_pass(
        rows, [{"catalyst_id": "struct_x", "risk_ctr_pct": 89.0, "over_risk": True,
                "post_pct": 62.5, "over": True, "rules_disagree": False,
                "sectors": ["not_held_yet"],
                "members_by_risk": [{"sector_id": "not_held_yet", "ctr_pct": 89.0,
                                     "eur": 1500.0}]}], {})
    assert out["breaching"][0]["trim_candidate"] is None
    assert rows[0]["rule_action"] == "HOLD", "the buy is blocked, not bought-then-trimmed"


def test_a_held_member_is_trimmed_toward_the_budget(monkeypatch):
    monkeypatch.setattr("catalyx.config.weights.correlated_catalyst_cap",
                        lambda: {"max_combined_pct": 30.0, "enforcement": "warn",
                                 "max_cluster_variance_pct": 35.0, "risk_enforcement": "block"})
    rows = [{"sector_id": "held_big", "rule_action": "HOLD", "trade_eur": 0.0,
             "actual_eur": 2000.0}]
    out = rebalance.cluster_risk_pass(
        rows, [_cluster(70.0, [("held_big", 70.0)], over_risk=True)],
        {"min_ticket_eur": 150.0})
    trim = out["breaching"][0]["trim_candidate"]
    assert trim["sector_id"] == "held_big"
    # first-order step: eur × (1 − budget/ctr) = 2000 × (1 − 35/70)
    assert trim["trim_eur"] == pytest.approx(1000.0, abs=0.01)
    assert rows[0]["rule_action"] == "TRIM" and rows[0]["trade_eur"] == pytest.approx(-1000.0)


def test_unmeasured_risk_is_never_read_as_within_budget(monkeypatch):
    """An unmeasured risk must not pass a risk budget by default — the same rule `cluster_risk_for`
    follows when it returns None instead of 0."""
    monkeypatch.setattr("catalyx.config.weights.correlated_catalyst_cap",
                        lambda: {"max_combined_pct": 30.0, "enforcement": "warn",
                                 "max_cluster_variance_pct": 35.0, "risk_enforcement": "block"})
    rows = _rows()
    out = rebalance.cluster_risk_pass(
        rows, [{"catalyst_id": "struct_unmeasured", "risk_ctr_pct": None, "over_risk": None,
                "post_pct": 40.0, "over": True, "sectors": ["adding"], "members_by_risk": []}],
        {})
    assert out["unmeasured"] == ["struct_unmeasured"]
    assert out["n_breaching"] == 0
    assert "UNMEASURED" in out["note"]
    assert next(r for r in rows if r["sector_id"] == "adding")["rule_action"] == "ADD"


def test_the_two_rules_are_reported_separately_so_disagreement_is_visible():
    """Where the notional cap and the risk budget disagree IS the evidence that decides which
    one survives the cycle. Collapsing them to one boolean would destroy exactly that."""
    r = rebalance.build()
    assert "risk_budget" in r and "catalyst_cap" in r
    for c in r["catalyst_cap"]:
        assert "over" in c and "over_risk" in c and "rules_disagree" in c
        # the notional flag is a bool; the risk flag may be None (unmeasured), never False-by-default
        assert isinstance(c["over"], bool)
        assert c["over_risk"] is None or isinstance(c["over_risk"], bool)
