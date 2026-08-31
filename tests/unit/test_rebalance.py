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
    # v4 B3: absent from the ranking is MISSING DATA, not evidence of being outside it. This
    # assertion used to read `== 1` — that is the defect: a sector nobody scored accumulated a
    # sell signal out of measurements that were never taken.
    assert rb.rank_out_streak([5, None], 12) == 0
    assert rb.rank_out_streak([30, 30, None], 12) == 0     # the gap breaks it, however bad the past
    assert rb.rank_out_streak([None, 30, 30], 12) == 2     # …but only the runs after it count
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


# ── The book closes (plan v4 §2 A1) ──────────────────────────────────────────
#
# `portfolio_holding` sums to 100%. The engine then drops the names that are not buyable today,
# and until 2026-08-28 it computed `target_eur = weight_pct/100 × deployable` on the survivors —
# so the dropped weight evaporated. 36.1% of the model book vanished that day: the deploy rule
# asked for €7,000 at work while the targets summed to €4,476, and executing EVERY rule action
# left the book at 38% against a 70% rule. These tests pin the identity that makes the table a
# comparison instead of two unrelated lists.

def test_the_target_book_closes_on_a_hundred_percent():
    out = rb.close_target_weights([40.0, 30.0, 30.0], max_position_pct=100.0)
    assert sum(out["weights"]) == pytest.approx(100.0)
    assert out["residual_pct"] == 0.0 and out["scale"] == pytest.approx(1.0)


def test_dropped_weight_is_redistributed_never_lost():
    # Two of four names removed: 100 − 35 = 65 survives and must be rescaled back to 100.
    out = rb.close_target_weights([40.0, 25.0], max_position_pct=100.0)
    assert sum(out["weights"]) == pytest.approx(100.0)
    assert out["scale"] == pytest.approx(100.0 / 65.0, abs=1e-4)
    assert out["residual_pct"] == pytest.approx(35.0)
    # Relative conviction is preserved — a universe cut must not reorder the book.
    assert out["weights"][0] / out["weights"][1] == pytest.approx(40.0 / 25.0, abs=1e-4)


def test_a_gutted_model_book_is_reported_not_silently_concentrated():
    # Only 20% of the model survives. Rescaling ×5 would turn a universe cut into a conviction
    # increase in whatever happened to remain; the rescale is capped and the caller is told.
    out = rb.close_target_weights([12.0, 8.0], max_position_pct=100.0, max_dropped_pct=40.0)
    assert out["incomplete"] is True and out["capped"] is True
    assert sum(out["weights"]) < 100.0                # closes BELOW the rule, deliberately
    assert out["scale"] == pytest.approx(100.0 / 60.0, abs=1e-4)


def test_the_position_cap_survives_the_rescale_and_its_excess_becomes_cash():
    # Rescaling can push a name through a ceiling it had already cleared. The cap is a risk
    # limit: what it sheds is cash, never another position.
    out = rb.close_target_weights([50.0, 10.0], max_position_pct=12.0)
    assert max(out["weights"]) <= 12.0 + 1e-9
    assert sum(out["weights"]) < 100.0


def test_an_empty_model_book_does_not_divide_by_zero():
    out = rb.close_target_weights([], max_position_pct=12.0)
    assert out["weights"] == [] and out["incomplete"] is True


# ── The vehicle is resolved at table time (plan v4 §2 A2) ────────────────────

def _seed_lake(tmp_path, weights_pct):
    import pandas as pd

    from catalyx.store import lake

    run = "run_20260828_000000"
    holdings = [{"portfolio_id": "catalyx", "run_id": run, "sector_id": f"sec_{i}",
                 "rank_in_portfolio": i + 1, "weight_pct": w, "composite": 90.0 - i,
                 "primary_etf": f"OLD{i}", "narrative_maturity": "emerging",
                 "regime_state": "intact"}
                for i, w in enumerate(weights_pct)]
    lake.append_partition("portfolio_holding", pd.DataFrame(holdings),
                          {"portfolio_id": "catalyx", "run_id": run}, overwrite=True,
                          lake_dir=tmp_path)
    snap = [{"run_id": run, "sector_id": f"sec_{i}", "rank": i + 1, "composite": 90.0 - i,
             "narrative_maturity": "emerging", "regime_state": "intact"}
            for i in range(len(weights_pct) + 3)]
    lake.append_partition("sector_snapshot", pd.DataFrame(snap), {"run_id": run},
                          overwrite=True, lake_dir=tmp_path)
    return run


def _build(tmp_path, monkeypatch, cfg, buyable, investable=None):
    monkeypatch.setattr(rb, "_buyable_vehicle", lambda sid: buyable.get(sid))
    monkeypatch.setattr(rb, "_investable_sectors",
                        lambda: investable if investable is not None else set(buyable))
    monkeypatch.setattr(rb, "_vix_last", lambda: 15.0)
    return rb.build(strategy="catalyx", cfg=cfg, lake_dir=tmp_path, total_capital=10_000.0,
                    exit_fn=lambda: {"positions": [], "realized_ytd_eur": 0.0},
                    expected_fn=lambda: {"buckets": {}, "effective_windows": 0},
                    overrides_fn=lambda: {"tally": {}, "total_net_eur": 0.0, "scored": [],
                                          "pending": [], "claude": {}})


def test_no_action_row_may_name_a_vehicle_that_cannot_be_bought(tmp_path, monkeypatch, cfg):
    # 2026-08-28 printed `BUY biotech_drug_development €891` against IBB — a US non-UCITS ETF —
    # because the ticker was read off a model book frozen before the universe cut. The ban is
    # only real if something checks it, exactly like BANNED_ACTION_WORDS.
    _seed_lake(tmp_path, [40.0, 35.0, 25.0])
    buyable = {"sec_0": "AAA.L", "sec_1": "BBB.DE", "sec_2": None,
               "sec_3": "CCC.L", "sec_4": "DDD.L", "sec_5": "EEE.L"}
    res = _build(tmp_path, monkeypatch, cfg, buyable, investable=set(buyable))
    for row in res["rows"]:
        if row["rule_action"] != "HOLD":
            assert row["etf"], f"{row['sector_id']} carries no buyable vehicle"
            assert not row["etf"].startswith("OLD"), "vehicle read off the frozen model book"


def test_build_closes_target_plus_cash_to_a_hundred(tmp_path, monkeypatch, cfg):
    _seed_lake(tmp_path, [40.0, 35.0, 25.0])
    buyable = {"sec_0": "AAA.L", "sec_1": "BBB.DE", "sec_2": None,
               "sec_3": "CCC.L", "sec_4": "DDD.L", "sec_5": "EEE.L"}
    b = _build(tmp_path, monkeypatch, cfg, buyable, investable=set(buyable))["book"]
    assert b["target_pct"] + b["cash_target_pct"] == pytest.approx(100.0, abs=0.01)
    assert b["deployed_pct"] + b["cash_actual_pct"] == pytest.approx(100.0, abs=0.01)


def test_an_unbuyable_name_is_substituted_not_deleted(tmp_path, monkeypatch, cfg):
    # The model asked for 3 lines. One vehicle disappeared; the book must still be 3 lines wide,
    # filled from the same run's ranking — a universe cut is not a decision to concentrate.
    _seed_lake(tmp_path, [40.0, 35.0, 25.0])
    buyable = {"sec_0": "AAA.L", "sec_1": "BBB.DE", "sec_2": None,
               "sec_3": "CCC.L", "sec_4": "DDD.L", "sec_5": "EEE.L"}
    res = _build(tmp_path, monkeypatch, cfg, buyable, investable=set(buyable))
    mdl = res["book"]["model"]
    assert mdl["n_holdings"] == 3
    assert mdl["dropped"] == ["sec_2"] and mdl["substituted"] == ["sec_3"]
    assert mdl["dropped_weight_pct"] == pytest.approx(25.0)
    assert mdl["residual_pct"] == 0.0          # substitution covered it; nothing to rescale


# ── A3: the breakeven replaces a forecast the data cannot support ────────────

def test_breakeven_is_friction_over_the_capital_that_actually_moves(cfg):
    # €1,000 rotated with €12 CGT and 20bps each way = €12 + €2 + €2 = €16 → 1.6%.
    f_sell = rb.leg_friction(1000.0, 12.0, cfg)
    f_buy = rb.leg_friction(1000.0, 0.0, cfg)
    total = f_sell["friction_eur"] + f_buy["friction_eur"]
    assert total == pytest.approx(16.0)
    assert rb.breakeven_pct(total, 1000.0) == pytest.approx(1.6)


def test_a_breakeven_needs_no_forecast_and_never_divides_by_zero(cfg):
    # The whole point of A3: every input is observable today. A zero-notional trade has no
    # hurdle — it must return None, not inf and not 0 (which would read as "free").
    assert rb.breakeven_pct(10.0, 0.0) is None
    assert rb.breakeven_pct(0.0, 1000.0) == 0.0


def test_the_swap_ledger_pairs_sells_to_buys_and_prorates_the_tax(cfg):
    rows = [
        {"sector_id": "out", "etf": "O.L", "rule_action": "SELL", "trade_eur": -1000.0,
         "tax_eur": 20.0},
        {"sector_id": "in_a", "etf": "A.L", "rule_action": "BUY", "trade_eur": 600.0},
        {"sector_id": "in_b", "etf": "B.L", "rule_action": "BUY", "trade_eur": 400.0},
    ]
    swaps = rb.swap_ledger(rows, cfg, horizon_days=63)
    assert [(w["from_sector"], w["to_sector"]) for w in swaps] == [("out", "in_a"), ("out", "in_b")]
    assert [w["moved_eur"] for w in swaps] == [600.0, 400.0]
    # One sale pays its CGT once however many buys it funds — so the legs must SPLIT it, never
    # each carry the full bill (which would double-count €20 into €40 of phantom friction).
    assert sum(w["tax_eur"] for w in swaps) == pytest.approx(20.0)


def test_a_swap_leg_below_the_minimum_ticket_is_not_a_rotation(cfg):
    rows = [{"sector_id": "out", "rule_action": "SELL", "trade_eur": -1000.0, "tax_eur": 0.0},
            {"sector_id": "big", "rule_action": "BUY", "trade_eur": 900.0},
            {"sector_id": "dust", "rule_action": "BUY", "trade_eur": 100.0}]
    swaps = rb.swap_ledger(rows, cfg, horizon_days=63)
    # €100 is below the €150 ticket the engine itself refuses to print as an order; pairing it
    # would put a rotation in the ledger that `size_trade` would never let happen.
    assert [w["to_sector"] for w in swaps] == ["big"]


def test_every_sale_row_carries_a_hurdle_and_the_forecast_stays_out_of_the_table(tmp_path,
                                                                                monkeypatch, cfg):
    _seed_lake(tmp_path, [40.0, 35.0, 25.0])
    res = _build(tmp_path, monkeypatch, cfg,
                 {f"sec_{i}": f"S{i}.L" for i in range(6)})
    for row in res["rows"]:
        if row["trade_eur"]:
            assert row["breakeven_pct"] is not None, f"{row['sector_id']} has no hurdle"
            assert row["breakeven_pct"] >= 0
    # net_edge_eur is NOT deleted — it feeds the calibration panel — it just stopped driving.
    assert all("net_edge_eur" in r for r in res["rows"])
    assert "b/e%" in rb.render(res) and "net€" not in rb.render(res)


def test_the_evidence_line_refuses_to_dress_up_a_thin_sample(cfg):
    thin = rb.rank_edge_evidence({"raw": {"top3": 5.0, "rest": 1.0}, "effective_windows": 1,
                                  "horizon_days": 63}, cfg)
    assert thin["verdict"] == "NONE", "a 1-window sample must not be reported as a measured edge"
    adverse = rb.rank_edge_evidence({"raw": {"top3": -0.4, "rest": 5.4}, "effective_windows": 9,
                                     "horizon_days": 63}, cfg)
    assert adverse["verdict"] == "ADVERSE" and adverse["spread_pp"] < 0
    good = rb.rank_edge_evidence({"raw": {"top3": 5.0, "rest": 1.0}, "effective_windows": 9,
                                  "horizon_days": 63}, cfg)
    assert good["verdict"] == "MEASURED" and good["spread_pp"] == pytest.approx(4.0)


# ── A5: partials stop arriving as a surprise ─────────────────────────────────

def test_both_rungs_are_reported_because_their_units_are_not_comparable(cfg):
    rows = [{"sector_id": "held", "actual_eur": 1000.0, "gap_pp": -1.0, "score_rank": 3,
             "unrealized_pct": 13.4, "rule_action": "HOLD"}]
    p = rb.partial_rungs(rows, cfg)[0]
    assert p["overweight"]["need_pp"] == pytest.approx(3.0)      # 4pp rung, 1pp above target
    assert p["ladder"]["need_gain_pct"] == pytest.approx(11.6)   # 25% rung, +13.4% so far
    # Rank 3 fails the rung's `rank_min: 6` — which is the rule WORKING (the ladder fires only
    # once the model has stopped leading the name), so this must not read as a blocked position.
    assert p["ladder"]["rank_ok"] is False
    assert p["live"] is False


def test_a_missing_rank_never_silently_satisfies_the_ladder(cfg):
    rows = [{"sector_id": "held", "actual_eur": 1000.0, "gap_pp": 0.0, "score_rank": None,
             "unrealized_pct": 80.0, "rule_action": "HOLD"}]
    p = rb.partial_rungs(rows, cfg)[0]
    assert p["ladder"]["gain_met"] is True
    assert p["ladder"]["rank_ok"] is False, "an absent rank must not be read as 'rank ≥ 6'"
    # A NaN off a parquet round-trip is the same gap — it must neither satisfy the leg nor
    # render as a number ("rank nan" shipped once).
    rows[0]["score_rank"] = float("nan")
    p = rb.partial_rungs(rows, cfg)[0]
    assert p["rank"] is None and p["ladder"]["rank_ok"] is False


def test_a_firing_rung_is_marked_live_and_cash_only_rows_are_skipped(cfg):
    rows = [{"sector_id": "held", "actual_eur": 1000.0, "gap_pp": -6.0, "score_rank": 8,
             "unrealized_pct": 30.0, "rule_action": "TRIM"},
            {"sector_id": "not_held", "actual_eur": 0.0, "gap_pp": 5.0, "score_rank": 2,
             "unrealized_pct": None, "rule_action": "BUY"}]
    parts = rb.partial_rungs(rows, cfg)
    assert [p["sector_id"] for p in parts] == ["held"]
    assert parts[0]["live"] is True
    assert parts[0]["overweight"]["met"] is True
    assert parts[0]["ladder"]["rank_ok"] is True


def test_the_sizing_vocabulary_is_stated_before_any_verdict(tmp_path, monkeypatch, cfg):
    _seed_lake(tmp_path, [40.0, 35.0, 25.0])
    res = _build(tmp_path, monkeypatch, cfg, {f"sec_{i}": f"S{i}.L" for i in range(6)})
    text = rb.render(res)
    assert "SIZING" in text
    # All three fractions, or the reader cannot tell what a TRIM moved.
    assert "SELL = 100%" in text and "REDUCE = 50%" in text and "back to target" in text
    assert text.index("SIZING") < text.index("ACTION"), "the vocabulary must precede the verdicts"


# ── B2: the gate arms on significance, never on a window count alone ─────────

def _exp(windows):
    return {"effective_windows": windows, "buckets": {}, "raw": {}, "horizon_days": 63}


def test_the_gate_refuses_to_arm_on_a_negative_ic(cfg):
    # THE FAILURE THIS PINS (plan D5): with enough windows and a decent |IC| the old guard would
    # have armed — on a ranking whose top bucket earns LESS than its bottom. Arming there does not
    # enforce the profit-taking rule, it inverts it.
    g = rb.gate_status(_exp(5), {"ic": -0.35, "se": 0.2, "verdict": "noise"}, cfg)
    assert g["armed"] is False
    assert "NEGATIVE" in g["why"]


def test_the_gate_refuses_to_arm_on_an_ic_indistinguishable_from_zero(cfg):
    g = rb.gate_status(_exp(5), {"ic": 0.05, "se": 0.2, "verdict": "noise"}, cfg)
    assert g["armed"] is False and "orders nothing" in g["why"]


def test_the_gate_still_refuses_to_arm_on_a_thin_sample(cfg):
    # v3's insight survives intact: an UNMEASURED quantity must never become a veto.
    g = rb.gate_status(_exp(1), {"ic": 0.40, "se": 0.2, "verdict": "signal"}, cfg)
    assert g["armed"] is False and "independent window" in g["why"]


def test_the_gate_arms_only_when_all_three_conditions_hold(cfg):
    g = rb.gate_status(_exp(4), {"ic": 0.31, "se": 0.2, "verdict": "weak"}, cfg)
    assert g["armed"] is True
    assert g["requires"]["ic_sign_must_be_positive"] is True


def test_a_standing_aside_gate_says_why_on_the_row(cfg):
    res = rb.apply_gate("SELL", -1000.0, 50.0, -60.0, cfg, evaluable=False,
                        why="STANDS ASIDE — IC -0.050 is NEGATIVE")
    assert res["final_action"] == "SELL", "an unmeasured gate must never turn a rule into inaction"
    assert "NEGATIVE" in res["gate_note"]


# ── B3: a missing rank is missing data, not a verdict ────────────────────────

def test_rank_coverage_separates_scored_runs_from_absent_ones():
    c = rb.rank_coverage([12, None, None, 14])
    assert c == {"n_runs": 4, "scored": 2, "missing": 2, "missing_recent": 0}


def test_a_sector_absent_from_too_many_runs_gets_rescore_not_sell(cfg):
    row = _row(held=True, rank=None, rank_out_streak=4, rank_missing_runs=2, rank_runs=4,
               gap_pp=-8.0)
    d = rb.decide_action(row, cfg)
    assert d["action"] == "RE-SCORE"
    assert "no rank to sell on" in d["reason"]


def test_rescore_never_outranks_a_broken_thesis(cfg):
    # A missing rank is a reason not to trust a RANK-based sell. It is not a reason to keep
    # holding something whose regime has broken — that sell never depended on the rank.
    row = _row(held=True, rank=None, rank_missing_runs=4, rank_runs=4, regime_state="breaking")
    assert rb.decide_action(row, cfg)["action"] == "SELL"
    row = _row(held=True, rank=None, rank_missing_runs=4, rank_runs=4, exit_action="exit")
    assert rb.decide_action(row, cfg)["action"] == "SELL"


def test_rescore_is_in_the_enum_and_moves_no_money(cfg):
    assert "RE-SCORE" in rb.PRECEDENCE
    assert rb.PRECEDENCE.index("RE-SCORE") < rb.PRECEDENCE.index("HOLD"), \
        "a work item must not sort below the rows that move money"
    assert rb.size_trade("RE-SCORE", 5000.0, 1000.0, cfg)["trade_eur"] == 0.0
    for w in rb.BANNED_ACTION_WORDS:
        assert w not in "RE-SCORE".lower()


def test_a_sector_scored_every_run_is_unaffected_by_the_rescore_rule(cfg):
    row = _row(held=True, rank=None, rank_out_streak=3, rank_missing_runs=0, rank_runs=4,
               score_rank=14)
    d = rb.decide_action(row, cfg)
    assert d["action"] == "SELL" and "#14" in d["reason"], \
        "the reason must name the rank it fired on"


def test_the_rk_column_never_contradicts_the_reason_beside_it(cfg):
    """ONE rank semantic — the universe rank — everywhere it is rendered.

    The `rk` column used to show the MODEL-BOOK rank, which is absent for exactly the sectors the
    model dropped: every row whose reason cited a number rendered `rk` blank. Papering over that
    with a marked `~11` fallback made the column mean two different things depending on the row.
    """
    rows = [{"sector_id": "sold", "etf": "S.L", "rank": None, "score_rank": 11,
             "target_pct": 0.0, "actual_pct": 10.5, "gap_eur": -1050.0, "rule_action": "SELL",
             "trade_eur": -1050.0, "breakeven_pct": 1.12,
             "reason": "ranked below top-10 (#11) for 4 consecutive runs"}]
    line = rb._render_rows(rows)[0]
    assert " 11 " in line and "~" not in line, "the column shows the number the reason names"
    assert "None" not in line and "nan" not in line

    # A sector nobody scored this run has no rank. That is a gap, and it renders as one — never
    # as a number borrowed from somewhere else.
    rows[0].update(score_rank=None, reason="regime broken")
    assert "—" in rb._render_rows(rows)[0]
    rows[0]["score_rank"] = float("nan")
    assert "nan" not in rb._render_rows(rows)[0]


def test_two_runs_on_the_same_day_are_one_observation(tmp_path):
    import pandas as pd

    from catalyx.store import lake

    # `rank_out_consecutive: 2` means two consecutive review CYCLES. Re-running the pipeline twice
    # in an afternoon used to write two "consecutive runs" and could manufacture a SELL by itself.
    for rid, rank in (("run_20260801_090000", 20), ("run_20260828_102303", 20),
                      ("run_20260828_102925", 20)):
        lake.append_partition("sector_snapshot",
                              pd.DataFrame([{"run_id": rid, "sector_id": "s", "rank": rank}]),
                              {"run_id": rid}, overwrite=True, lake_dir=tmp_path)
    st = rb._rank_streaks(10, n_runs=4, lake_dir=tmp_path)["s"]
    assert st["n_runs"] == 2, "the two same-day runs must collapse to one observation"
    assert st["streak"] == 2


# ── E1: the row carries the age of the evidence it spends on, and nothing else ─

def test_a_blind_catalyst_qualifies_the_buy_it_funds_but_never_vetoes_it(cfg):
    """The 2026-08-31 review ordered €1,020 into `luxury_goods` on the same page that listed its
    catalyst's indicators as unobserved since 2025-09-30. Both facts were printed; neither knew
    about the other.

    The fix is a COLUMN, not a rule. `decide_action` must not read it: the freshness doctrine is
    that stale data is a reason to re-verify, never a reason to stop acting — turning a
    maintenance failure into a trading prohibition is the conservative bias Phase C fights.
    """
    ctx = _row(held=False, gap_pp=10.2, rank=8)
    baseline = rb.decide_action(ctx, cfg)
    for status in ("fresh", "stale", "blind", None):
        ctx["data_age_status"] = status
        ctx["data_age"] = None if status is None else f"{status} (240d)"
        assert rb.decide_action(ctx, cfg) == baseline, \
            "the data age qualifies the row; it must not change the action"


def test_a_sector_inherits_the_stalest_driver_it_pays_for(monkeypatch):
    """A book is only as current as the worst catalyst behind it — reporting the freshest one
    would let a live indicator launder a dead one sharing the same position."""
    from catalyx.execution import portfolio
    from catalyx.scorer import freshness
    from catalyx.store import structural_catalyst_repo as scr

    monkeypatch.setattr(portfolio, "_sector_catalyst_map",
                        lambda: {"mixed": ["c_fresh", "c_blind"], "clean": ["c_fresh"],
                                 "orphan": [], "absorbed": ["c_old"]})
    monkeypatch.setattr(freshness, "by_catalyst", lambda: {
        "c_fresh": {"status": "fresh", "label": "fresh"},
        "c_blind": {"status": "blind", "label": "blind (240d)"},
        "c_live": {"status": "stale", "label": "stale (30d)"}})
    # A merged catalyst's own file can never be refreshed again; the survivor holds the live
    # indicators, so the age must be read through `merged_into`.
    monkeypatch.setattr(scr, "merged_map", lambda: {"c_old": "c_live"})

    got = rb._data_age_by_sector()
    assert got["mixed"]["label"] == "blind (240d)"
    assert got["clean"]["label"] == "fresh"
    assert got["absorbed"]["label"] == "stale (30d)", "a merged id must resolve to its survivor"
    # An uncatalyzed sector has no structural driver whose age could qualify it. A blank is the
    # honest answer; inventing a status would be worse than the gap.
    assert "orphan" not in got


# ── F1: the table says what it rests on when its own evidence is not it ──────

def test_the_prior_is_named_only_when_the_ranking_has_not_earned_the_table():
    """The 2026-08-31 review demanded eight trades toward a ranking it reported as ordering
    nothing (IC −0.050, top3−rest −5.84pp), and called not doing them a breach. Both can be
    right — being invested in leaders is a different prior from THIS ranking working — but the
    document never separated them, so it read as self-contradictory.
    """
    thin = {"verdict": "NONE", "why": "~1 independent window(s) < the 3 the gate requires"}
    sp = rb.selection_prior(thin, 0.0)
    assert sp and "NAMES" in sp["note"] and "λ=0.00" in sp["note"]
    # The asymmetry is the point: size is already shrunk, selection is not — say which is which.
    assert "SIZE" in sp["note"] and "override" in sp["note"]

    adverse = rb.selection_prior({"verdict": "ADVERSE", "why": "top3 sits BELOW rest"}, 0.0)
    assert adverse and adverse["verdict"] == "ADVERSE"

    # Once the edge is measured the table rests on the measurement, and the line would be noise.
    assert rb.selection_prior({"verdict": "MEASURED", "why": "over ~9 windows"}, 0.4) is None
    assert rb.selection_prior(None, 0.0) is None


def test_naming_the_prior_changes_no_action(cfg):
    """It is a sentence, not a gate. Gating the SELECTION on an unmeasured IC would be a new
    policy, and the policy is the user's to set — v5 §6 records that rejection."""
    ctx = _row(held=False, gap_pp=10.2, rank=8)
    before = rb.decide_action(ctx, cfg)
    rb.selection_prior({"verdict": "ADVERSE", "why": "x"}, 0.0)
    assert rb.decide_action(ctx, cfg) == before


# ── E3: one standing decision, one DEFER ─────────────────────────────────────

def test_the_same_standing_decision_is_not_re_logged_every_run():
    """The dedup was scoped to the PRIOR run, so a rule that keeps asking and a human that keeps
    declining wrote a fresh DEFER each time: three pipeline runs in a week produced 30 rows for
    10 decisions, and the tally measured how often the pipeline ran."""
    prior = [{"sector_id": "luxury_goods", "rule_action": "BUY", "trade_eur": 1020.0,
              "run_id": "run_20260828_000000"}]
    first = rb.unrecorded_deviations(prior, [], [], since="2026-07-28", until="2026-08-28")
    assert [u["sector_id"] for u in first] == ["luxury_goods"]

    standing = [{"sector_id": "luxury_goods", "rule_action": "BUY", "author": "unrecorded",
                 "logged_at": "2026-08-28"}]
    again = rb.unrecorded_deviations(prior, [], [], since="2026-08-28", until="2026-08-31",
                                     open_defers=standing)
    assert again == [], "the same silence must not be charged twice"


def test_acting_settles_the_defer_so_the_next_refusal_is_a_new_decision():
    """A movement AFTER the defer resolves it. If the rule then asks again and is declined again,
    that is a second decision and owes its own row."""
    prior = [{"sector_id": "luxury_goods", "rule_action": "BUY", "trade_eur": 1020.0}]
    standing = [{"sector_id": "luxury_goods", "rule_action": "BUY", "author": "unrecorded",
                 "logged_at": "2026-08-01"}]
    moves = [{"sector_id": "luxury_goods", "executed_at": "2026-08-10"}]
    out = rb.unrecorded_deviations(prior, moves, [], since="2026-08-28", until="2026-08-31",
                                   open_defers=standing)
    assert [u["sector_id"] for u in out] == ["luxury_goods"]

    # A DIFFERENT action on the same sector is also a different decision.
    out = rb.unrecorded_deviations([{"sector_id": "luxury_goods", "rule_action": "SELL",
                                     "trade_eur": -500.0}], [], [],
                                   since="2026-08-28", open_defers=standing)
    assert [u["rule_action"] for u in out] == ["SELL"]


# ── E2: the streak counts review cycles, not adjacent runs ───────────────────

def test_a_week_of_iteration_cannot_manufacture_a_sell_streak():
    """v4.3 collapsed two runs of one afternoon. Same defect one scale up: copper's four runs were
    06-30, 07-05, 07-28, 08-28 — gaps of five days to a month — so `rank_out_consecutive: 2` meant
    "ten days" or "two months" depending on how busy the quarter had been."""
    dense = ["20260801", "20260804", "20260807", "20260810"]
    assert rb._cycle_runs(dense, 4, 21) == ["20260810"], "four runs in ten days are one cycle"

    spaced = ["20260501", "20260601", "20260701", "20260801"]
    assert rb._cycle_runs(spaced, 4, 21) == spaced

    # Walking backwards keeps the LATEST reading — the one the decision is actually about.
    mixed = ["20260612", "20260630", "20260705", "20260728", "20260828"]
    assert rb._cycle_runs(mixed, 4, 21) == ["20260612", "20260705", "20260728", "20260828"]
    assert "20260630" not in rb._cycle_runs(mixed, 4, 21), "5 days after 06-30 is the same cycle"


def test_the_sell_reason_names_the_calendar_span_not_just_a_count(cfg):
    """"3 consecutive cycles" covered anything from ten days to two months before the floor, and
    the reader could not tell which."""
    row = _row(held=True, rank=None, rank_out_streak=3, rank_missing_runs=0, rank_runs=4,
               score_rank=14, rank_streak_days=54)
    d = rb.decide_action(row, cfg)
    assert d["action"] == "SELL"
    assert "3 consecutive review cycles (54d)" in d["reason"] and "#14" in d["reason"]

    # No span recorded (an old partition) degrades to the count alone, never to a made-up number.
    row["rank_streak_days"] = None
    assert "(54d)" not in rb.decide_action(row, cfg)["reason"]


def test_the_span_helper_measures_calendar_days_between_first_and_last():
    assert rb._span_days(["20260705", "20260728", "20260828"]) == 54
    assert rb._span_days(["20260828"]) == 0


# ── F3: the cash row is a ledger, not a reprimand ────────────────────────────

def test_the_cash_row_flips_its_label_when_holding_cash_was_right():
    """`forgone` was hardcoded, so a quarter in which sitting out was the correct call printed
    identically to one in which it was expensive. A ledger that can only reprove is not one."""
    up = rb.cash_drag(10_000.0, 2.8, "2026-06-16", 76, model_return_pct=3.6)
    assert up["verdict"] == "cost" and up["headline"] == "CASH DRAG"
    assert "forgone" in up["note"] and "avoided" not in up["note"]

    down = rb.cash_drag(10_000.0, -4.0, "2026-06-16", 76, model_return_pct=-5.0)
    assert down["verdict"] == "saved" and down["headline"] == "CASH THAT SAVED YOU"
    assert "avoided" in down["note"] and "forgone" not in down["note"]
    # The € is an absolute amount beside a signed word — never a "-€500 forgone".
    assert "€500 avoided" in down["note"] and "€400 avoided" in down["note"]


def test_the_headline_follows_the_policy_on_trial_not_the_benchmark():
    """The question the cash row answers is not "should I have been invested?" but "should I have
    executed THIS table?" — so the model book leads when both are available."""
    split = rb.cash_drag(10_000.0, 2.0, "2026-06-16", 76, model_return_pct=-1.0)
    assert split["verdict"] == "saved", "the model book decides the headline"
    assert "€200 forgone" in split["note"], "the benchmark leg keeps its own honest sign"
    assert "€100 avoided" in split["note"]

    # With no model curve the benchmark carries it alone — degraded, never invented.
    only_bench = rb.cash_drag(10_000.0, 2.0, "2026-06-16", 76)
    assert only_bench["verdict"] == "cost" and only_bench["model_forgone_eur"] is None
    assert "model book" not in only_bench["note"]


# ── G1: the spread is a property of the ticker, not a constant ───────────────

def test_a_per_vehicle_spread_wins_over_the_global_and_absence_inherits_it(cfg):
    """`b/e 0.20%` printed identically on 7 of 8 action rows: a column that cannot tell a liquid
    IUHE.AS from a thin JEDI.DE is not telling you anything. The comment beside `spread_bps` has
    promised the per-ETF override since v3; nothing passed it."""
    flat = rb.cost_drag(-1000.0, 0.0, cfg)
    wide = rb.cost_drag(-1000.0, 0.0, cfg, spread_bps=60.0)
    assert wide["spread_eur"] == pytest.approx(6.0)
    assert wide["spread_eur"] > flat["spread_eur"]
    assert rb.breakeven_pct(wide["cost_drag_eur"], -1000.0) > \
        rb.breakeven_pct(flat["cost_drag_eur"], -1000.0)

    # None is not zero — an unmeasured vehicle inherits the global, it does not trade for free.
    assert rb.cost_drag(-1000.0, 0.0, cfg, spread_bps=None) == flat


def test_the_universe_accessor_reads_only_observed_values():
    """The field is deliberately empty today: a yfinance snapshot returned ask < bid on SEMI.L
    and off-hours quotes elsewhere, and an invented number here is worse than the honest default
    — the b/e would stop being constant and look informative while being noise."""
    from catalyx.config import weights as w

    got = w.spread_bps_by_ticker()
    assert isinstance(got, dict)
    assert all(isinstance(v, float) for v in got.values())
    # Whatever is populated, the sector→vehicle tickers must be the keys, never sector ids.
    assert not any("_" in k for k in got), "keys are tickers, not sector ids"


def test_the_model_counterfactual_never_mixes_nav_modes(tmp_path):
    """`portfolio_nav` holds backtest / live / forward rows under the SAME portfolio_id, at
    overlapping dates and on DIFFERENT NAV bases. Reading the window without pinning the mode
    took the first row from one series and the last from another: this book reported −16.88%
    where `live` returned +0.20%, a €1,179 "saving" that never happened. Same defect v3.5 fixed
    in `portfolio_compare` — the table cannot express the constraint, so every read repeats it.
    """
    import pandas as pd

    from catalyx.store import lake

    rows = [{"portfolio_id": "catalyx", "mode": "backtest", "date": "2026-06-16", "nav": 124.6},
            {"portfolio_id": "catalyx", "mode": "backtest", "date": "2026-07-30", "nav": 124.0},
            {"portfolio_id": "catalyx", "mode": "live", "date": "2026-06-16", "nav": 103.39},
            {"portfolio_id": "catalyx", "mode": "live", "date": "2026-08-31", "nav": 103.60}]
    lake.append_partition("portfolio_nav", pd.DataFrame(rows), {"portfolio_id": "catalyx"},
                          overwrite=True, lake_dir=tmp_path)
    got = rb._model_return_pct("2026-06-16", "2026-08-31", "catalyx", lake_dir=tmp_path)
    assert got == pytest.approx(0.2, abs=0.01), "live is the record; mixing bases invents a return"

    # With only a backtest series it reports the backtest honestly rather than nothing…
    only_bt = [r for r in rows if r["mode"] == "backtest"]
    lake.append_partition("portfolio_nav", pd.DataFrame(only_bt), {"portfolio_id": "catalyx"},
                          overwrite=True, lake_dir=tmp_path)
    assert rb._model_return_pct("2026-06-16", "2026-08-31", "catalyx",
                                lake_dir=tmp_path) == pytest.approx(-0.48, abs=0.01)
    # …and one lone point is not a return.
    lake.append_partition("portfolio_nav", pd.DataFrame(only_bt[:1]), {"portfolio_id": "catalyx"},
                          overwrite=True, lake_dir=tmp_path)
    assert rb._model_return_pct("2026-06-16", "2026-08-31", "catalyx", lake_dir=tmp_path) is None
