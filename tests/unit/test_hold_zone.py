"""v15.2 — buy at the top, hold until the SELL rule fires, and a swap needs a free line.

`sell_if_any.rank_out_of_top: 10` x `rank_out_consecutive: 2` always declared that a held line is
sold after two cycles outside the top-10. It never governed: a held name that fell out of the
6-name MODEL book got a 0% target, read as maximally overweight, and the overweight TRIM closed
the whole line the first cycle it was out. The 6/7 cut sits 0.02-0.12 composite_z apart against
0.10-0.16 of cycle-to-cycle noise, so the cut — not the rules — was choosing the book.

These tests pin the narrow shape of the fix: it suppresses ONLY the exit the model-book cut
manufactured, never a fundamental exit, the concentration ceiling or the profit ladder; it adds no
threshold; and a new line may fill only a FREE line.
"""
from __future__ import annotations

import pytest

from catalyx.config import weights
from catalyx.execution import rebalance as rb


@pytest.fixture
def cfg():
    return {**weights.rebalance_rules(), "hold_zone": {"enabled": True}}


def _row(**over) -> dict:
    base = {"held": True, "gap_pp": 0.0, "rank": None, "score_rank": 8, "regime_state": "intact",
            "narrative_maturity": "emerging", "exit_action": "hold", "catalyst_status": "active",
            "unrealized_pct": 5.0, "reverify_required": False, "drawdown_tier": "clear",
            "rank_out_streak": 0, "hold_zone": True}
    base.update(over)
    return base


# ── The rule table ───────────────────────────────────────────────────────────

def test_a_held_name_that_left_the_model_book_is_held_not_sold(cfg):
    out = rb.decide_action(_row(), cfg)
    assert out["action"] == "HOLD"
    assert out["reason"].startswith("hold zone")
    # The reason names the streak and the cut that WILL sell it, so the next reader does not
    # have to re-derive why a name outside the book is still held.
    assert "0/2" in out["reason"] and "top-10" in out["reason"]


def test_the_hold_zone_never_shields_a_fundamental_exit(cfg):
    for kill, expected in (({"catalyst_status": "invalidated"}, "SELL"),
                           ({"regime_state": "breaking"}, "SELL"),
                           ({"exit_action": "exit"}, "SELL"),
                           ({"exit_action": "reduce"}, "REDUCE")):
        assert rb.decide_action(_row(**kill), cfg)["action"] == expected, kill


def test_the_sell_rule_still_sells_when_its_streak_completes(cfg):
    # The rule that was always declared: out of the top-10 for two consecutive cycles.
    out = rb.decide_action(_row(rank_out_streak=2, score_rank=14, hold_zone=False), cfg)
    assert out["action"] == "SELL" and "top-10" in out["reason"]


def test_the_hold_zone_does_not_lift_the_concentration_ceiling(cfg):
    # Upstream target = min(actual, cap), so above the ceiling the gap is real and the ordinary
    # overweight TRIM sizes it back to the cap — not to zero.
    out = rb.decide_action(_row(gap_pp=-4.5), cfg)
    assert out["action"] == "TRIM"


def test_the_profit_ladder_still_reaches_a_hold_zone_winner(cfg):
    ladder = rb.decide_action(_row(unrealized_pct=40.0, score_rank=8), cfg)
    plain = rb.decide_action(_row(unrealized_pct=40.0, score_rank=8, hold_zone=False,
                                  gap_pp=0.0), cfg)
    assert ladder["action"] == plain["action"] == "TRIM"


def test_without_the_flag_a_zero_target_is_still_an_exit(cfg):
    # v15.1 behaviour, exactly: this is what `enabled: false` restores.
    out = rb.decide_action(_row(hold_zone=False, gap_pp=-9.0), cfg)
    assert out["action"] == "TRIM"


def test_the_hold_zone_adds_no_threshold():
    # The cut is `sell_if_any`, the line count is `book_shape.n_target`. A second number here
    # would be the choice-of-constant the correlated cap forbids.
    hz = weights.rebalance_rules()["hold_zone"]
    assert set(hz) == {"enabled"}


# ── The slot rule ────────────────────────────────────────────────────────────

def _t(sid, action, actual, trade=0.0, target=10.0, rank=5):
    return {"sector_id": sid, "rule_action": action, "actual_eur": actual, "trade_eur": trade,
            "target_pct": target, "score_rank": rank, "reason": "", "spread_eur": 3.0,
            "cost_drag_eur": 3.0, "breakeven_pct": 0.4, "dz_required": 1.0,
            "expected_edge_eur": 1.0, "net_edge_eur": 1.0}


def test_a_buy_into_a_full_book_is_deferred_with_the_reason(cfg):
    rows = [_t(f"h{i}", "HOLD", 1000.0) for i in range(6)] + [_t("new", "BUY", 0.0, 900.0)]
    out = rb.new_line_slot_pass(rows, 6, cfg)
    new = next(r for r in rows if r["sector_id"] == "new")
    assert new["rule_action"] == "HOLD" and new["trade_eur"] == 0.0
    assert "no free line" in new["reason"]
    assert new["breakeven_pct"] is None and new["cost_drag_eur"] == 0.0
    assert [d["sector_id"] for d in out["deferred"]] == ["new"]


def test_a_sale_in_the_same_table_frees_the_line(cfg):
    rows = ([_t(f"h{i}", "HOLD", 1000.0) for i in range(5)]
            + [_t("old", "SELL", 1000.0, -1000.0, target=0.0)]
            + [_t("new", "BUY", 0.0, 900.0)])
    out = rb.new_line_slot_pass(rows, 6, cfg)
    assert next(r for r in rows if r["sector_id"] == "new")["rule_action"] == "BUY"
    assert out["deferred"] == []


def test_a_full_exit_wearing_the_trim_label_frees_the_line_too(cfg):
    rows = ([_t(f"h{i}", "HOLD", 1000.0) for i in range(5)]
            + [_t("old", "TRIM", 1000.0, -1000.0, target=0.0)]
            + [_t("new", "BUY", 0.0, 900.0)])
    rb.new_line_slot_pass(rows, 6, cfg)
    assert next(r for r in rows if r["sector_id"] == "new")["rule_action"] == "BUY"


def test_the_leader_is_never_skipped_to_make_room(cfg):
    # One free line, two buys: the better-ranked one takes it whatever its size.
    rows = [_t(f"h{i}", "HOLD", 1000.0) for i in range(5)] + [
        _t("rank7", "BUY", 0.0, 1500.0, rank=7), _t("rank6", "BUY", 0.0, 700.0, rank=6)]
    out = rb.new_line_slot_pass(rows, 6, cfg)
    assert out["granted"] == ["rank6"] and [d["sector_id"] for d in out["deferred"]] == ["rank7"]


def test_an_add_to_a_held_line_is_not_a_new_line(cfg):
    rows = [_t(f"h{i}", "HOLD", 1000.0) for i in range(5)] + [_t("h5", "ADD", 1000.0, 400.0)]
    rb.new_line_slot_pass(rows, 6, cfg)
    assert next(r for r in rows if r["sector_id"] == "h5")["rule_action"] == "ADD"


def test_disabled_leaves_the_table_untouched(cfg):
    rows = [_t(f"h{i}", "HOLD", 1000.0) for i in range(6)] + [_t("new", "BUY", 0.0, 900.0)]
    out = rb.new_line_slot_pass(rows, 6, {**cfg, "hold_zone": {"enabled": False}})
    assert rows[-1]["rule_action"] == "BUY" and out["enabled"] is False


# ── End to end through build() ───────────────────────────────────────────────

def _held(sid, eur, etf="X.DE"):
    return {"sector_id": sid, "etf": etf, "invested_eur": eur, "suggested_action": "hold",
            "tax": {"market_value_eur": eur, "unrealized_pct": 3.0},
            "drawdown": {"tier": "clear"}, "attribution": []}


def _seed(tmp_path, model_ids, snap_ids):
    import pandas as pd

    from catalyx.store import lake

    run = "run_20261017_000000"
    holdings = [{"portfolio_id": "catalyx", "run_id": run, "sector_id": s,
                 "rank_in_portfolio": i + 1, "weight_pct": 100.0 / len(model_ids),
                 "composite": 90.0 - i, "primary_etf": f"{s}.DE",
                 "narrative_maturity": "emerging", "regime_state": "intact"}
                for i, s in enumerate(model_ids)]
    lake.append_partition("portfolio_holding", pd.DataFrame(holdings),
                          {"portfolio_id": "catalyx", "run_id": run}, overwrite=True,
                          lake_dir=tmp_path)
    snap = [{"run_id": run, "sector_id": s, "rank": i + 1, "composite": 90.0 - i,
             "narrative_maturity": "emerging", "regime_state": "intact"}
            for i, s in enumerate(snap_ids)]
    lake.append_partition("sector_snapshot", pd.DataFrame(snap), {"run_id": run},
                          overwrite=True, lake_dir=tmp_path)


def _build(tmp_path, monkeypatch, cfg, held, model_ids, snap_ids):
    _seed(tmp_path, model_ids, snap_ids)
    allsec = set(snap_ids)
    monkeypatch.setattr(rb, "_buyable_vehicle", lambda sid: f"{sid}.DE" if sid in allsec else None)
    monkeypatch.setattr(rb, "_investable_sectors", lambda: allsec)
    monkeypatch.setattr(rb, "_vix_last", lambda: 15.0)
    return rb.build(strategy="catalyx", cfg=cfg, lake_dir=tmp_path, total_capital=10_000.0,
                    exit_fn=lambda: {"positions": held, "realized_ytd_eur": 0.0},
                    expected_fn=lambda: {"buckets": {}, "effective_windows": 0},
                    overrides_fn=lambda: {"tally": {}, "total_net_eur": 0.0, "scored": [],
                                          "pending": [], "claude": {}})


def test_build_keeps_a_name_the_model_dropped_and_defers_its_replacement(tmp_path, monkeypatch,
                                                                          cfg):
    # Model book = a, b, c, d, e, f. We hold a-e plus `g` (universe rank 7, not in the model).
    ids = list("abcdefgh")
    model = list("abcdef")
    held = [_held(s, 1200.0) for s in "abcde"] + [_held("g", 1200.0)]
    res = _build(tmp_path, monkeypatch, cfg, held, model, ids)
    rows = {r["sector_id"]: r for r in res["rows"]}
    assert rows["g"]["rule_action"] == "HOLD" and rows["g"]["reason"].startswith("hold zone")
    # `f` is the model's 6th name, not held: the book already has 6 lines, so no free line.
    assert rows["f"]["rule_action"] == "HOLD" and "no free line" in rows["f"]["reason"]
    assert res["hold_zone"]["held"] == ["g"]
    assert [d["sector_id"] for d in res["hold_zone"]["slots"]["deferred"]] == ["f"]


def test_build_with_the_flag_off_reproduces_the_old_exit(tmp_path, monkeypatch, cfg):
    ids = list("abcdefgh")
    held = [_held(s, 1200.0) for s in "abcde"] + [_held("g", 1200.0)]
    off = {**cfg, "hold_zone": {"enabled": False}}
    res = _build(tmp_path, monkeypatch, off, held, list("abcdef"), ids)
    g = next(r for r in res["rows"] if r["sector_id"] == "g")
    assert g["rule_action"] == "TRIM" and g["target_pct"] == 0.0


def test_build_sells_when_the_sell_rule_streak_completes(tmp_path, monkeypatch, cfg):
    # `g` fell to universe rank 14 and the streak is already 2 cycles: the SELL rule fires.
    ids = [f"s{i}" for i in range(14)]
    ids[13] = "g"
    model = ids[:6]
    held = [_held(s, 1200.0) for s in model[:5]] + [_held("g", 1200.0)]
    monkeypatch.setattr(rb, "_rank_streaks", lambda *a, **k: {
        "g": {"streak": 2, "streak_days": 40, "missing": 0, "n_runs": 4}})
    res = _build(tmp_path, monkeypatch, cfg, held, model, ids)
    g = next(r for r in res["rows"] if r["sector_id"] == "g")
    assert g["rule_action"] == "SELL"
