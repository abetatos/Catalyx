"""Unit tests for the rebalance engine (catalyx.execution.rebalance).

This module exists to take a portfolio verdict out of LLM judgment, so the tests pin the two
things that make that real: (1) the decision table produces one of six actions in a fixed
precedence, with no "watch/monitor/consider" escape hatch; (2) none of the guards degrade to
inaction on MISSING data — the specific failure mode this engine was built to remove.
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


# ── Deployment ratio: cash stops being a feeling ─────────────────────────────

def test_deploy_ratio_rises_with_the_number_of_intact_leaders(cfg):
    # Frozen 2026-08-28: base 0.70 at the neutral point (5 intact top-8 sectors), +5pp each.
    assert rb.deploy_ratio(5, 15.0, cfg)["ratio"] == pytest.approx(0.70)
    assert rb.deploy_ratio(8, 15.0, cfg)["ratio"] == pytest.approx(0.85)
    assert rb.deploy_ratio(10, 15.0, cfg)["ratio"] == pytest.approx(0.95)


def test_vix_is_the_only_macro_brake_and_the_floor_holds(cfg):
    calm = rb.deploy_ratio(8, 15.0, cfg)
    panic = rb.deploy_ratio(8, 35.0, cfg)
    assert panic["ratio"] == pytest.approx(calm["ratio"] - 0.20)
    assert panic["vix_brake"] is True
    # Even with nothing intact and VIX blown out, the rules never fall below the floor —
    # "everything to cash" is not an available option.
    assert rb.deploy_ratio(0, 60.0, cfg)["ratio"] == pytest.approx(cfg["deployment"]["floor"])


def test_deploy_ratio_explains_itself(cfg):
    assert "base" in rb.deploy_ratio(8, 15.0, cfg)["why"]


def test_unknown_vix_does_not_apply_the_brake(cfg):
    # A missing price must not read as "risk-off" — that is the conservatism bias via a data gap.
    assert rb.deploy_ratio(8, None, cfg)["vix_brake"] is False


# ── The decision table ───────────────────────────────────────────────────────

def test_the_action_enum_has_no_hedging_words(cfg):
    seen = set()
    for over in ({}, {"gap_pp": 9.0}, {"held": False, "gap_pp": 9.0}, {"gap_pp": -9.0},
                 {"exit_action": "exit"}, {"regime_state": "breaking"},
                 {"rank_out_streak": 3}, {"exit_action": "reduce"}):
        seen.add(rb.decide_action(_row(**over), cfg)["action"])
    assert seen <= set(rb.PRECEDENCE)
    assert not any(w in a.lower() for a in seen for w in rb.BANNED_ACTION_WORDS)


def test_precedence_a_broken_regime_outranks_being_underweight(cfg):
    # The dangerous ordering bug: "it is 9pp under target" must never quietly beat "it is broken".
    out = rb.decide_action(_row(regime_state="breaking", gap_pp=9.0), cfg)
    assert out["action"] == "SELL"


def test_exit_watcher_exit_and_a_dead_catalyst_both_sell(cfg):
    assert rb.decide_action(_row(exit_action="exit"), cfg)["action"] == "SELL"
    assert rb.decide_action(_row(catalyst_status="invalidated"), cfg)["action"] == "SELL"


def test_rank_streak_needs_consecutive_runs_not_one_bad_print(cfg):
    assert rb.decide_action(_row(rank_out_streak=1), cfg)["action"] != "SELL"
    assert rb.decide_action(_row(rank_out_streak=2), cfg)["action"] == "SELL"


def test_a_stale_verdict_alone_does_not_halve_a_winner(cfg):
    # The 2026-08-04 doctrine: freshness dominates, but a drawdown is what forces the protective
    # reduce. Trimming a +13% position because its YAML is 60 days old is the bias in disguise.
    up = _row(reverify_required=True, drawdown_tier="clear", unrealized_pct=13.0)
    assert rb.decide_action(up, cfg)["action"] == "HOLD"
    down = _row(reverify_required=True, drawdown_tier="reduce", unrealized_pct=-21.0)
    assert rb.decide_action(down, cfg)["action"] == "REDUCE"


def test_underweight_leader_adds_and_an_unheld_leader_buys(cfg):
    assert rb.decide_action(_row(gap_pp=5.0, rank=3), cfg)["action"] == "ADD"
    assert rb.decide_action(_row(held=False, gap_pp=6.0, rank=3), cfg)["action"] == "BUY"


def test_a_new_line_is_held_to_a_higher_bar_than_an_add(cfg):
    # gap 3.5pp clears add_if (3.0) but not buy_if (4.0) — starting a position needs more.
    assert rb.decide_action(_row(gap_pp=3.5, rank=3), cfg)["action"] == "ADD"
    assert rb.decide_action(_row(held=False, gap_pp=3.5, rank=3), cfg)["action"] == "HOLD"


def test_exhausted_narrative_blocks_a_new_line_but_the_deadband_speaks_first(cfg):
    assert rb.decide_action(
        _row(held=False, gap_pp=9.0, narrative_maturity="exhausted"), cfg)["action"] == "HOLD"
    assert "deadband" in rb.decide_action(_row(gap_pp=1.0), cfg)["reason"]


def test_overweight_trims_back_to_target(cfg):
    out = rb.decide_action(_row(gap_pp=-6.0), cfg)
    assert out["action"] == "TRIM" and "above target" in out["reason"]


# ── Profit ladder ────────────────────────────────────────────────────────────

def test_profit_ladder_takes_the_highest_rung_cleared():
    # The mechanism still supports several rungs — this pins the ORDER, not the config.
    ladder = [{"gain_pct_min": 25.0, "rank_min": 6, "trim_fraction": 0.33},
              {"gain_pct_min": 50.0, "rank_min": 0, "trim_fraction": 0.33}]
    assert rb.profit_ladder_step(60.0, 9, ladder)["gain_pct_min"] == 50.0
    assert rb.profit_ladder_step(30.0, 9, ladder)["gain_pct_min"] == 25.0
    assert rb.profit_ladder_step(30.0, 2, ladder) is None      # still a leader → no partial
    assert rb.profit_ladder_step(None, 9, ladder) is None      # unmarked → no invented trim


def test_the_frozen_ladder_never_trims_a_position_the_model_still_leads(cfg):
    """The 2026-08-28 freeze (plan §7.2): trailing rank is the exit, not a fixed-gain ladder.

    A rung with `rank_min: 0` fires on gain alone, which cuts a winner the model still ranks #1 —
    the disposition effect with a threshold attached, and the exact opposite of a momentum
    mandate. Concentration is bounded by `max_position_pct` and `trim_if` instead. If a future
    edit re-adds a rank-free rung, this test is where that decision has to be argued again.
    """
    ladder = cfg["profit_ladder"]
    assert ladder, "the ladder was disabled, not re-tuned — say so in the config comment"
    assert all(int(r.get("rank_min", 0)) > 0 for r in ladder)
    assert rb.profit_ladder_step(120.0, 1, ladder) is None
    assert rb.decide_action(_row(unrealized_pct=120.0, rank=1), cfg)["action"] == "HOLD"


# ── Sizing ───────────────────────────────────────────────────────────────────

def test_a_trade_below_the_minimum_ticket_is_not_a_small_trade(cfg):
    out = rb.size_trade("ADD", 40.0, 500.0, cfg)
    assert out["trade_eur"] == 0.0 and out["downgraded"] is True


def test_a_full_exit_is_never_downgraded_for_being_small(cfg):
    # A broken thesis does not become acceptable because the line is €80.
    out = rb.size_trade("SELL", -80.0, 80.0, cfg)
    assert out["trade_eur"] == -80.0 and out["downgraded"] is False


def test_reduce_halves_and_sell_closes(cfg):
    assert rb.size_trade("REDUCE", 0.0, 1000.0, cfg)["trade_eur"] == -500.0
    assert rb.size_trade("SELL", 0.0, 1000.0, cfg)["trade_eur"] == -1000.0


def test_ladder_trim_sizes_off_market_value_not_the_gap(cfg):
    assert rb.size_trade("TRIM", 0.0, 900.0, cfg, ladder_fraction=0.33)["trade_eur"] \
        == pytest.approx(-297.0)


# ── The after-tax gate — and its refusal to freeze the book ──────────────────

def test_a_taxable_sale_that_does_not_pay_is_blocked(cfg):
    out = rb.apply_gate("SELL", -1000.0, 50.0, -20.0, cfg, evaluable=True)
    assert out["gated"] is True and out["final_action"] == "HOLD"
    out = rb.apply_gate("SELL", -1000.0, 50.0, 80.0, cfg, evaluable=True)
    assert out["final_action"] == "SELL"


def test_an_unmeasured_edge_never_becomes_a_veto(cfg):
    # With ~1 calibration window E[r]≈0, so net = −(tax+spread) and EVERY taxable sale would
    # fail forever. An unmeasured quantity must not silently acquire a vote.
    out = rb.apply_gate("SELL", -1000.0, 50.0, -14.0, cfg, evaluable=False)
    assert out["final_action"] == "SELL" and out["gated"] is False
    assert "not evaluable" in out["gate_note"]


def test_a_loss_making_sale_is_not_gated(cfg):
    # No realized gain → no CGT → the gate has nothing to weigh; cutting a loser stays available.
    out = rb.apply_gate("SELL", -1000.0, 0.0, -3.0, cfg, evaluable=True)
    assert out["final_action"] == "SELL"


def test_purchases_are_not_gated_on_a_noisy_edge(cfg):
    out = rb.apply_gate("BUY", 900.0, 0.0, -2.0, cfg, evaluable=True)
    assert out["final_action"] == "BUY" and out["gated"] is False


# ── Costs and edge ───────────────────────────────────────────────────────────

def test_cost_drag_counts_tax_and_spread(cfg):
    out = rb.cost_drag(-1000.0, 12.0, cfg)
    assert out["spread_eur"] == pytest.approx(2.0)          # 20bps of €1000
    assert out["cost_drag_eur"] == pytest.approx(14.0)


def test_expected_edge_is_signed_by_direction():
    assert rb.expected_edge(1000.0, 2.0) == pytest.approx(20.0)    # buying earns the bucket
    assert rb.expected_edge(-1000.0, 2.0) == pytest.approx(-20.0)  # selling forfeits it
    assert rb.expected_edge(1000.0, None) is None                  # unknown stays unknown


# ── Concentration + streak helpers ───────────────────────────────────────────

def test_hhi_reads_as_concentration():
    assert rb.hhi([100.0]) == 1.0
    assert rb.hhi([25.0] * 4) == pytest.approx(0.25)
    assert rb.hhi([]) is None


def test_rank_out_streak_counts_from_the_newest_and_resets_on_a_good_run():
    assert rb.rank_out_streak([20, 25, 30], 12) == 3
    assert rb.rank_out_streak([30, 25, 5], 12) == 0        # back inside the cut → reset
    assert rb.rank_out_streak([5, 30, 30], 12) == 2
    assert rb.rank_out_streak([5, None], 12) == 1          # absent from the ranking = outside it
    assert rb.rank_out_streak([], 12) == 0


# ── Overrides — the escape hatch, and the arithmetic that keeps it honest ────
#
# The override log exists so that being conservative is ALLOWED but never free: the deviation is
# recorded, priced a month later against the action it replaced, and tallied by author. These
# tests pin the sign convention (the thing that is easy to get backwards and impossible to spot
# afterwards) and the two guards on the suspension rule.

def test_declining_a_sell_is_scored_as_the_exposure_it_kept():
    # Rule said SELL €500; we held. That is +€500 of retained exposure. A 10% fall costs €50.
    assert rb.override_edge(-500.0, 0.0, -10.0) == pytest.approx(-50.0)
    assert rb.override_edge(-500.0, 0.0, 10.0) == pytest.approx(50.0)


def test_declining_a_buy_carries_the_opposite_sign():
    # Rule said BUY €500; we passed. Exposure is €500 LOWER, so a fall is a win for the override.
    assert rb.override_edge(500.0, 0.0, -10.0) == pytest.approx(50.0)
    assert rb.override_edge(500.0, 0.0, 10.0) == pytest.approx(-50.0)


def test_a_partial_override_scores_only_the_difference():
    assert rb.override_edge(-1000.0, -400.0, -10.0) == pytest.approx(-60.0)


def test_an_unpriceable_override_scores_nothing_rather_than_zero():
    # None is "not yet known". Zero would be "the deviation was free", which is a claim.
    assert rb.override_edge(-500.0, 0.0, None) is None


def test_the_tally_separates_authors_and_counts_the_wins():
    tally = rb.author_tally([
        {"author": "claude", "override_edge_eur": -120.0},
        {"author": "claude", "override_edge_eur": 40.0},
        {"author": "user", "override_edge_eur": 300.0},
    ])
    assert tally["claude"]["n"] == 2 and tally["claude"]["net_eur"] == pytest.approx(-80.0)
    assert tally["claude"]["wins"] == 1
    assert tally["user"]["net_eur"] == pytest.approx(300.0)


def test_one_bad_call_does_not_suspend_claude(cfg):
    tally = {"claude": {"n": 1, "net_eur": -900.0, "wins": 0}}
    out = rb.claude_override_suspended(tally, cfg)
    assert out["suspended"] is False and "not enough to judge" in out["why"]


def test_a_net_negative_record_over_the_sample_does_suspend_claude(cfg):
    tally = {"claude": {"n": 6, "net_eur": -50.0, "wins": 2}}
    assert rb.claude_override_suspended(tally, cfg)["suspended"] is True
    ok = {"claude": {"n": 6, "net_eur": 10.0, "wins": 4}}
    assert rb.claude_override_suspended(ok, cfg)["suspended"] is False


def test_an_override_needs_an_author_who_may_and_a_reason(tmp_path, cfg):
    with pytest.raises(ValueError, match="may not override"):
        rb.log_override("run_1", "copper_miners", "SELL", "HOLD", "gut feel", "someone_else",
                        lake_dir=tmp_path, cfg=cfg)
    with pytest.raises(ValueError, match="no reason"):
        rb.log_override("run_1", "copper_miners", "SELL", "HOLD", "   ", "user",
                        lake_dir=tmp_path, cfg=cfg)


def test_an_override_records_what_was_actually_moved(tmp_path, cfg):
    from catalyx.store import lake
    rb.log_override("run_1", "copper_miners", "SELL", "REDUCE", "catalyst re-verified intact",
                    "user", lake_dir=tmp_path, chosen_trade_eur=-400.0, cfg=cfg)
    df = lake.read_table("override_log", lake_dir=tmp_path)
    assert len(df) == 1 and df.iloc[0]["chosen_trade_eur"] == pytest.approx(-400.0)


def _seed_override_lake(tmp_path, run_id="run_20260601_090000"):
    import pandas as pd

    from catalyx.store import lake
    lake.append_partition("rebalance", pd.DataFrame([
        {"run_id": run_id, "sector_id": "copper_miners", "etf": "4COP.DE", "as_of": "2026-06-01",
         "rule_action": "SELL", "trade_eur": -1000.0, "cost_drag_eur": 45.0}]),
        {"run_id": run_id}, overwrite=True, lake_dir=tmp_path)
    return run_id


def test_score_overrides_prices_the_deviation_and_tallies_it(tmp_path, cfg):
    import pandas as pd

    run_id = _seed_override_lake(tmp_path)
    rb.log_override(run_id, "copper_miners", "SELL", "HOLD", "thesis re-verified", "claude",
                    lake_dir=tmp_path, chosen_trade_eur=0.0, cfg=cfg)

    idx = pd.bdate_range("2026-06-01", periods=40)
    fake = pd.DataFrame({"4COP.DE": [100.0] * 39 + [90.0]}, index=idx)

    res = rb.score_overrides(lake_dir=tmp_path, cfg=cfg, price_fn=lambda t, s, e: fake,
                             as_of="2026-07-27", ccy_fn=lambda t: {x: "EUR" for x in t})
    assert len(res["scored"]) == 1 and not res["pending"]
    row = res["scored"][0]
    # Held €1000 of exposure the rule wanted gone; it fell 10% → the override cost ~€100.
    assert row["override_edge_eur"] == pytest.approx(-100.0, abs=1.0)
    assert row["rule_cost_eur"] == pytest.approx(45.0)      # reported beside, never added in
    assert res["tally"]["claude"]["net_eur"] == pytest.approx(-100.0, abs=1.0)


def test_an_override_younger_than_the_window_is_pending_not_scored(tmp_path, cfg):
    import pandas as pd

    run_id = _seed_override_lake(tmp_path)
    rb.log_override(run_id, "copper_miners", "SELL", "HOLD", "give it a cycle", "user",
                    lake_dir=tmp_path, chosen_trade_eur=0.0, cfg=cfg)
    idx = pd.bdate_range("2026-06-01", periods=5)
    fake = pd.DataFrame({"4COP.DE": [100.0, 99.0, 98.0, 97.0, 80.0]}, index=idx)

    res = rb.score_overrides(lake_dir=tmp_path, cfg=cfg, price_fn=lambda t, s, e: fake,
                             as_of="2026-06-05", ccy_fn=lambda t: {x: "EUR" for x in t})
    # A 4-day price difference is a coin. Letting it into the tally would suspend on noise.
    assert not res["scored"] and len(res["pending"]) == 1
    assert "4/21 trading days" in res["pending"][0]["status"]
    assert res["tally"] == {}
