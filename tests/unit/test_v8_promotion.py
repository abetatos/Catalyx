"""v8 Q1/Q2/Q3 — the promoted specs and the reweighted composite.

The backtest (lake validation/backtest_ic) is the EVIDENCE; these tests pin the EDIT:
momentum = 12-1 + 52w-high blend with a flagged 3m6m fallback, crowding = measured
comomentum with a flagged label fallback, and the GK-shrunk 0.35/0.275/0.24/0.135 split.
"""
from __future__ import annotations

import pytest

from catalyx.config import weights


def test_composite_weights_are_the_gk_shrunk_split():
    w = weights.composite_weights()
    assert w["catalyst_alignment"] == pytest.approx(0.35)   # NOT backtestable: prior held
    assert w["flow_confirmation"] == pytest.approx(0.24)    # NOT backtestable: prior held
    assert w["momentum"] == pytest.approx(0.275)
    assert w["crowding_risk"] == pytest.approx(0.135)
    assert sum(w.values()) == pytest.approx(1.0)


def test_momentum_spec_is_the_backtested_blend():
    spec = weights.momentum_spec()
    assert spec["mode"] == "12_1_52w"
    b = spec["blend"]
    assert b["momentum_12_1"] + b["near_52w_high"] == pytest.approx(1.0)
    assert b["momentum_12_1"] > b["near_52w_high"]          # 12-1 is the family winner


def test_crowding_source_is_measured_comomentum():
    assert weights.crowding_source()["mode"] == "measured_comomentum"


def _primaries(with_1y: bool):
    out = {}
    for k in range(6):
        p = {"return_1m_pct": 1.0 + k, "return_3m_pct": 3.0 + k, "return_6m_pct": 6.0 + k,
             "near_52w_high_pct": -2.0 * k, "return_1y_pct": (10.0 + k) if with_1y else None}
        out[f"s{k}"] = p
    return out


def test_engine_blends_12_1_and_52w_when_1y_history_exists(monkeypatch):
    from catalyx.scorer import momentum_engine as me
    monkeypatch.setattr(me, "_primaries_from_lake", lambda **kw: ("2026-08-31", _primaries(True)))
    res = me.compute_momentum_scores()
    s = res["scores"]["s5"]
    assert s["momentum_spec_used"] == "12_1_52w"
    pct_52w = (0 + 0.5) / 6 * 100                           # s5 sits farthest below its high
    assert s["momentum_score"] == pytest.approx(
        me._BLEND_121 * s["momentum_12_1"] + me._BLEND_52W * pct_52w, abs=0.1)


def test_engine_falls_back_flagged_without_1y_history(monkeypatch):
    from catalyx.scorer import momentum_engine as me
    monkeypatch.setattr(me, "_primaries_from_lake", lambda **kw: ("2026-08-31", _primaries(False)))
    res = me.compute_momentum_scores()
    for s in res["scores"].values():
        assert s["momentum_spec_used"] == "3m6m_fallback"   # measured, weaker, and SAID
        assert s["momentum_12_1"] is None
