"""v15.4 — a position can be held to LOWER the book's variance, and the rule table must know it.

`water_infrastructure` was filed 100% under the AI-capex driver because that was the only driver on
file; it loads ~0.15 on that basket and is in the book to diversify it. Held for its rank it is
sold when the rank wobbles; held for what it does, it is not. The role removes exactly ONE exit -
the rank-out streak, which measures something a diversifier was never bought for - and nothing else.

Run: uv run pytest tests/unit/test_diversifier_role.py -q
"""
from __future__ import annotations

import json

import pytest

from catalyx.config import weights
from catalyx.execution import best_moves as bm
from catalyx.execution import rebalance as rb
from catalyx.store import movement_repo as mr


# ── movement_repo.sector_roles ───────────────────────────────────────────────

def _mov(tmp, mid, when, action, sector, etf, qty, reattr=None):
    m = {"id": mid, "schema_version": "1.4", "executed_at": when, "action": action,
         "sector_id": sector, "vehicle": {"etf": etf}, "qty": qty, "amount_eur": qty * 10.0,
         "attribution": [{"catalyst_id": "uncatalyzed", "weight": 1.0}]}
    if reattr:
        m["reattribution"] = reattr
    (tmp / f"{mid}.json").write_text(json.dumps(m), encoding="utf-8")


def _ra(as_of, role=None, why="x"):
    e = {"as_of": as_of, "attribution": [{"catalyst_id": "uncatalyzed", "weight": 1.0}],
         "rationale": why}
    if role is not None:
        e["role"] = role
    return e


def test_a_declared_diversifier_is_read_with_its_date_and_reason(tmp_path):
    _mov(tmp_path, "mov_1", "2026-10-06T10:00", "open", "water", "IH2O.L", 10,
         [_ra("2026-10-19", "diversifier", "beta 0.15 on the AI basket")])
    r = mr.sector_roles(tmp_path)
    assert r["water"]["role"] == "diversifier" and r["water"]["as_of"] == "2026-10-19"
    assert "0.15" in r["water"]["rationale"]


def test_no_declaration_means_no_entry_so_the_default_is_the_full_rule_table(tmp_path):
    _mov(tmp_path, "mov_1", "2026-10-06T10:00", "open", "water", "IH2O.L", 10, [_ra("2026-10-19")])
    assert mr.sector_roles(tmp_path) == {}


def test_a_later_reattribution_without_a_role_does_not_reset_it(tmp_path):
    _mov(tmp_path, "mov_1", "2026-10-06T10:00", "open", "water", "IH2O.L", 10,
         [_ra("2026-10-19", "diversifier"), _ra("2026-11-20")])
    assert mr.sector_roles(tmp_path)["water"]["role"] == "diversifier"


def test_writing_catalyst_explicitly_reverts_the_role(tmp_path):
    _mov(tmp_path, "mov_1", "2026-10-06T10:00", "open", "water", "IH2O.L", 10,
         [_ra("2026-10-19", "diversifier"), _ra("2026-11-20", "catalyst")])
    assert mr.sector_roles(tmp_path) == {}


def test_a_closed_and_reopened_line_starts_again_as_a_catalyst(tmp_path):
    # A role is a judgement about THAT holding. Carrying it onto a new purchase would let a
    # position that was sold for cause come back already exempt from the rank-out rule.
    _mov(tmp_path, "mov_1", "2026-10-06T10:00", "open", "water", "IH2O.L", 10,
         [_ra("2026-10-07", "diversifier")])
    _mov(tmp_path, "mov_2", "2026-10-10T10:00", "close", "water", "IH2O.L", 10)
    _mov(tmp_path, "mov_3", "2026-10-12T10:00", "open", "water", "IH2O.L", 10)
    assert mr.sector_roles(tmp_path) == {}


def test_a_closed_line_has_no_role_and_a_partial_trim_keeps_it(tmp_path):
    _mov(tmp_path, "mov_1", "2026-10-06T10:00", "open", "water", "IH2O.L", 10,
         [_ra("2026-10-07", "diversifier")])
    _mov(tmp_path, "mov_2", "2026-10-10T10:00", "trim", "water", "IH2O.L", 4)
    assert "water" in mr.sector_roles(tmp_path)
    _mov(tmp_path, "mov_3", "2026-10-11T10:00", "close", "water", "IH2O.L", 6)
    assert mr.sector_roles(tmp_path) == {}


def test_a_typo_reads_as_catalyst_never_as_an_exemption(tmp_path):
    _mov(tmp_path, "mov_1", "2026-10-06T10:00", "open", "water", "IH2O.L", 10,
         [_ra("2026-10-19", "diversifer")])
    assert mr.sector_roles(tmp_path) == {}


def test_the_live_roles_are_only_ones_a_person_declared_with_a_reason():
    # Every live role comes from a dated, reasoned reattribution entry - never inferred.
    for sid, r in mr.sector_roles().items():
        assert r["role"] == "diversifier" and r["as_of"] and len(r["rationale"] or "") > 40, sid


# ── the rule table ───────────────────────────────────────────────────────────

@pytest.fixture
def cfg():
    return weights.rebalance_rules()


def _row(**over) -> dict:
    base = {"held": True, "gap_pp": 0.0, "rank": None, "score_rank": 14, "regime_state": "intact",
            "narrative_maturity": "emerging", "exit_action": "hold", "catalyst_status": "active",
            "unrealized_pct": 5.0, "reverify_required": False, "drawdown_tier": "clear",
            "rank_out_streak": 2, "hold_zone": False, "diversifier": True,
            "diversifier_hold": True}
    base.update(over)
    return base


def test_a_completed_rank_streak_sells_a_catalyst_line_but_not_a_diversifier(cfg):
    assert rb.decide_action(_row(diversifier=False, diversifier_hold=False), cfg)["action"] == "SELL"
    out = rb.decide_action(_row(), cfg)
    assert out["action"] == "HOLD" and out["reason"].startswith("diversifier")


def test_the_role_never_shields_a_fundamental_exit(cfg):
    for kill, expected in (({"catalyst_status": "invalidated"}, "SELL"),
                           ({"regime_state": "breaking"}, "SELL"),
                           ({"exit_action": "exit"}, "SELL"),
                           ({"exit_action": "reduce"}, "REDUCE")):
        assert rb.decide_action(_row(**kill), cfg)["action"] == expected, kill


def test_the_role_does_not_lift_the_ceiling_or_the_ladder(cfg):
    assert rb.decide_action(_row(gap_pp=-4.5), cfg)["action"] == "TRIM"
    assert rb.decide_action(_row(unrealized_pct=40.0), cfg)["action"] == "TRIM"


# ── end to end through build() ───────────────────────────────────────────────

from tests.unit.test_hold_zone import _build, _held  # noqa: E402


def _roles(monkeypatch, roles):
    monkeypatch.setattr(mr, "sector_roles", lambda *a, **k: roles)


def test_build_holds_a_diversifier_the_sell_rule_would_have_sold(tmp_path, monkeypatch, cfg):
    ids = [f"s{i}" for i in range(14)]
    ids[13] = "g"
    model = ids[:6]
    held = [_held(s, 1200.0) for s in model[:5]] + [_held("g", 1200.0)]
    monkeypatch.setattr(rb, "_rank_streaks", lambda *a, **k: {
        "g": {"streak": 2, "streak_days": 40, "missing": 0, "n_runs": 4}})
    _roles(monkeypatch, {"g": {"role": "diversifier", "as_of": "2026-10-19", "rationale": "r"}})
    res = _build(tmp_path, monkeypatch, {**cfg, "hold_zone": {"enabled": False}}, held, model, ids)
    g = next(r for r in res["rows"] if r["sector_id"] == "g")
    assert g["rule_action"] == "HOLD" and g["reason"].startswith("diversifier")
    assert g["target_pct"] == g["actual_pct"]                    # no opinion, not a zero target
    assert "g" in res["diversifiers"]


def test_build_still_sells_the_same_line_without_the_role(tmp_path, monkeypatch, cfg):
    ids = [f"s{i}" for i in range(14)]
    ids[13] = "g"
    model = ids[:6]
    held = [_held(s, 1200.0) for s in model[:5]] + [_held("g", 1200.0)]
    monkeypatch.setattr(rb, "_rank_streaks", lambda *a, **k: {
        "g": {"streak": 2, "streak_days": 40, "missing": 0, "n_runs": 4}})
    _roles(monkeypatch, {})
    res = _build(tmp_path, monkeypatch, cfg, held, model, ids)
    assert next(r for r in res["rows"] if r["sector_id"] == "g")["rule_action"] == "SELL"
    assert res["diversifiers"] == {}


def test_build_fails_toward_the_sale_when_roles_cannot_be_read(tmp_path, monkeypatch, cfg):
    ids = [f"s{i}" for i in range(14)]
    ids[13] = "g"
    model = ids[:6]
    held = [_held(s, 1200.0) for s in model[:5]] + [_held("g", 1200.0)]
    monkeypatch.setattr(rb, "_rank_streaks", lambda *a, **k: {
        "g": {"streak": 2, "streak_days": 40, "missing": 0, "n_runs": 4}})

    def boom(*a, **k):
        raise OSError("movements unreadable")
    monkeypatch.setattr(mr, "sector_roles", boom)
    res = _build(tmp_path, monkeypatch, cfg, held, model, ids)
    assert next(r for r in res["rows"] if r["sector_id"] == "g")["rule_action"] == "SELL"


# ── the health column ────────────────────────────────────────────────────────

def _sig(s_hi=0.40, s_lo=0.15, rho=0.0):
    c = rho * s_hi * s_lo
    return {"hi": {"hi": s_hi**2, "lo": c}, "lo": {"hi": c, "lo": s_lo**2}}


def test_a_diversifier_carries_less_variance_than_capital_and_says_so():
    h = bm.diversifier_health({"hi": 3_000.0, "lo": 3_000.0}, _sig(), 10_000.0, {"lo"}, 56)[0]
    assert h["sector_id"] == "lo" and h["rho_rest"] == 0.0
    assert h["ratio"] < 1.0 and h["risk_share_pct"] < h["capital_share_pct"]


def test_a_line_that_stopped_diversifying_reads_ratio_above_one():
    h = bm.diversifier_health({"hi": 1_000.0, "lo": 3_000.0}, _sig(s_hi=0.15, s_lo=0.40, rho=0.9),
                              10_000.0, {"lo"}, 56)[0]
    assert h["rho_rest"] > 0.8 and h["ratio"] > 1.0


def test_the_correlation_error_shrinks_with_weeks():
    a = bm.diversifier_health({"hi": 3_000.0, "lo": 3_000.0}, _sig(rho=0.3), 10_000.0, {"lo"}, 20)[0]
    b = bm.diversifier_health({"hi": 3_000.0, "lo": 3_000.0}, _sig(rho=0.3), 10_000.0, {"lo"}, 80)[0]
    assert a["se_rho"] > b["se_rho"]


def test_a_sole_line_is_unmeasured_not_a_comforting_number():
    h = bm.diversifier_health({"lo": 3_000.0}, {"lo": {"lo": 0.0225}}, 10_000.0, {"lo"}, 56)[0]
    assert h["rho_rest"] is None and h["ratio"] is None
    assert "unmeasured" in bm._render_diversifiers([h])[-2]
