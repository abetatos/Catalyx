"""Closed-experiment outcome engine — pure logic (P&L, after-tax, the right-thesis × right-reason
verdict matrix, assumption resolution, behavioral deviation flags) plus a full evaluate()/report()
round-trip on a temp movements dir + temp lake. Network-free: no yfinance, no real disk loaders."""
from __future__ import annotations

import json

from catalyx.attribution import outcome as O


# ── realized P&L ──────────────────────────────────────────────────────────────

def _open(qty=100, eur=1000.0, etf="XXX", at="2026-01-01T00:00:00Z", **rd):
    m = {"id": "mov_20260101_x_open", "action": "open", "executed_at": at,
         "vehicle": {"etf": etf}, "qty": qty, "amount_eur": eur}
    if rd:
        m["risk_discipline"] = rd.get("risk_discipline")
    return m


def _close(qty=100, eur=1300.0, etf="XXX", at="2026-04-01T00:00:00Z", trigger="profit_take", outcome=None):
    return {"id": "mov_20260401_x_close", "action": "close", "executed_at": at,
            "vehicle": {"etf": etf}, "qty": qty, "amount_eur": eur, "trigger": trigger,
            "attribution": [{"catalyst_id": "struct_x", "weight": 1.0}],
            "sector_id": "sec_x", "outcome": outcome or {}}


def test_realized_pnl_gain():
    p = O.realized_pnl(_close(eur=1300.0), [_open()])
    assert p["gross_pnl_eur"] == 300.0
    assert p["return_pct"] == 30.0
    assert p["holding_days"] == 90
    assert p["avg_cost"] == 10.0


def test_realized_pnl_loss():
    p = O.realized_pnl(_close(eur=800.0), [_open()])
    assert p["gross_pnl_eur"] == -200.0
    assert p["return_pct"] == -20.0


def test_realized_pnl_avg_cost_over_two_buys():
    buys = [_open(qty=100, eur=1000.0), _open(qty=100, eur=1400.0, at="2026-02-01T00:00:00Z")]
    p = O.realized_pnl(_close(qty=200, eur=2600.0), buys)
    assert p["avg_cost"] == 12.0          # (1000+1400)/200
    assert p["gross_pnl_eur"] == 200.0     # 2600 − 12*200


# ── after-tax (Spanish CGT) ───────────────────────────────────────────────────

def test_after_tax_gain_first_bracket():
    at = O.after_tax(300.0, ytd_prior=0.0)
    assert at["tax_due_eur"] == 57.0       # 19% of 300 (under €6k)
    assert at["after_tax_pnl_eur"] == 243.0


def test_after_tax_loss_is_free():
    at = O.after_tax(-200.0, ytd_prior=0.0)
    assert at["tax_due_eur"] == 0.0
    assert at["after_tax_pnl_eur"] == -200.0


# ── verdict matrix: all four quadrants ────────────────────────────────────────

def test_verdict_skill():
    v = O.compute_verdict(243.0, [{"id": "asm_01", "outcome": "validated"}], True, 90)
    assert v["label"] == "skill"
    assert v["right_thesis"] and v["right_reason"]
    assert v["confidence"] == "high"


def test_verdict_luck():
    v = O.compute_verdict(243.0, [{"id": "asm_01", "outcome": "falsified"}], False, 90)
    assert v["label"] == "luck"
    assert v["right_thesis"] is True and v["right_reason"] is False


def test_verdict_variance():
    v = O.compute_verdict(-100.0, [{"id": "asm_01", "outcome": "validated"}], True, 90)
    assert v["label"] == "variance"


def test_verdict_correct_invalidation():
    v = O.compute_verdict(-100.0, [{"id": "asm_01", "outcome": "falsified"}], False, 90)
    assert v["label"] == "correct_invalidation"


def test_verdict_indeterminate_when_reason_unresolved():
    v = O.compute_verdict(243.0, [{"id": "asm_01", "outcome": "unresolved"}], None, 90)
    assert v["label"] == "indeterminate"
    assert v["right_reason"] is None
    assert v["confidence"] == "low"


def test_verdict_low_confidence_on_short_hold():
    v = O.compute_verdict(243.0, [{"id": "asm_01", "outcome": "validated"}], True, 30)
    assert v["label"] == "skill"
    assert v["confidence"] == "low"        # held < 60 days


def test_verdict_catalyst_overrides_assumption_balance():
    # assumptions say validated, but the catalyst explicitly did NOT materialize → right_reason False
    v = O.compute_verdict(243.0, [{"id": "asm_01", "outcome": "validated"}], False, 90)
    assert v["right_reason"] is False
    assert v["label"] == "luck"


# ── assumption resolution fallback ────────────────────────────────────────────

def test_resolve_assumptions_uses_captured_when_present():
    captured = [{"id": "asm_01", "outcome": "falsified"}]
    out = O.resolve_assumptions(captured, [{"id": "asm_01", "current_status": "holding"}])
    assert out == captured


def test_resolve_assumptions_falls_back_to_status():
    out = O.resolve_assumptions(None, [
        {"id": "asm_01", "current_status": "holding"},
        {"id": "asm_02", "current_status": "violated"},
        {"id": "asm_03", "current_status": "monitoring"},
    ])
    by = {a["id"]: a["outcome"] for a in out}
    assert by == {"asm_01": "validated", "asm_02": "falsified", "asm_03": "unresolved"}


# ── behavioral flags ──────────────────────────────────────────────────────────

def test_flag_held_past_full_exit():
    rd = {"invalidation": [{"id": "inv_01", "severity": "full_exit",
                            "triggered": True, "triggered_at": "2026-03-01T00:00:00Z"}]}
    flags = O.behavioral_flags(_close(at="2026-04-01T00:00:00Z"), [_open(risk_discipline=rd)],
                               300.0, [], None)
    assert any(f.startswith("held_past_full_exit:inv_01:+31d") for f in flags)


def test_flag_exited_intact_at_loss():
    # loss, no stop fired, no assumption falsified → the 'sold too early / panic' shape
    flags = O.behavioral_flags(_close(eur=800.0, trigger="reconsideration"), [_open()],
                               -200.0, [{"id": "asm_01", "outcome": "validated"}], None)
    assert "exited_intact_at_loss" in flags
    assert "discretionary_exit" in flags


def test_flag_overrode_signal():
    flags = O.behavioral_flags(_close(), [_open()], 300.0, [], followed_signal=False)
    assert "overrode_signal" in flags


def test_no_flags_on_clean_signal_aligned_win():
    flags = O.behavioral_flags(_close(trigger="stop_hit"), [_open()], 300.0,
                               [{"id": "asm_01", "outcome": "validated"}], followed_signal=True)
    assert flags == []


# ── full evaluate() + report() round-trip on temp dirs ────────────────────────

def _write(d, mov):
    (d / f"{mov['id']}.json").write_text(json.dumps(mov), encoding="utf-8")


def test_evaluate_end_to_end(tmp_path):
    mdir = tmp_path / "movements"
    mdir.mkdir()
    ldir = tmp_path / "lake"
    rd = {"invalidation": [{"id": "inv_01", "severity": "full_exit", "triggered": False}],
          "assumptions": [{"id": "asm_01", "current_status": "holding"}]}
    open_mov = _open(risk_discipline=rd)
    open_mov["sector_id"] = "sec_x"
    open_mov["attribution"] = [{"catalyst_id": "struct_x", "weight": 1.0}]
    close_mov = _close(eur=1300.0, outcome={
        "exit_reason": "took profit into strength",
        "exit_note": "felt greedy holding longer",
        "catalyst_materialized": True,
        "assumption_resolution": [{"id": "asm_01", "outcome": "validated"}],
        "signal_context": {"exit_watcher_action": "watch", "followed_signal": True},
    })
    _write(mdir, open_mov)
    _write(mdir, close_mov)

    r = O.evaluate(close_mov["id"], write_back=True, persist=True,
                   movements_dir=mdir, lake_dir=ldir)
    assert r["verdict"]["label"] == "skill"
    assert r["pnl"]["gross_pnl_eur"] == 300.0
    assert r["pnl"]["after_tax_pnl_eur"] == 243.0

    # write-back persisted the merged block to the file
    saved = json.loads((mdir / f"{close_mov['id']}.json").read_text(encoding="utf-8"))
    assert saved["outcome"]["verdict"]["label"] == "skill"
    assert saved["outcome"]["pnl"]["gross_pnl_eur"] == 300.0
    assert saved["outcome"]["exit_note"] == "felt greedy holding longer"  # captured field preserved

    # lake row persisted
    from catalyx.store import lake
    df = lake.read_table("movement_outcome", lake_dir=ldir)
    assert len(df) == 1
    assert df.iloc[0]["verdict_label"] == "skill"

    # aggregate report
    rep = O.report(movements_dir=mdir)
    assert rep["n_closed"] == 1
    assert rep["verdict_mix"] == {"skill": 1}
    assert rep["after_tax"]["total_eur"] == 243.0
    assert rep["journal"][0]["exit_note"] == "felt greedy holding longer"


def test_evaluate_rejects_non_close(tmp_path):
    mdir = tmp_path / "movements"
    mdir.mkdir()
    _write(mdir, _open())
    import pytest
    with pytest.raises(ValueError):
        O.evaluate("mov_20260101_x_open", persist=False, movements_dir=mdir)
