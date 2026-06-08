"""Entry-timing overlay — pure math (RSI / stretch / vol / stabilization / state) + the
event-overhang resolution. Network-free: every test passes synthetic close series or fixture
catalyst dicts; nothing hits yfinance or the disk loaders."""
from __future__ import annotations

from datetime import date

from catalyx.scorer import entry_timing as et

CFG = {
    "lookback_days": 90, "rsi_period": 14, "rsi_overbought": 70, "rsi_oversold": 30,
    "ma_stretch_window": 20, "stretch_overbought_pct": 8.0,
    "vol_ratio_window_short": 10, "vol_ratio_window_long": 90, "vol_ratio_elevated": 1.5,
    "rsi_warm": 65, "stretch_warm_pct": 6.0, "vol_ratio_warm": 1.2, "borderline_min_axes": 2,
    "short_trend_window": 5, "drawdown_local_high_window": 20,
    "stabilization_up_closes": 2, "stabilization_reclaim_ma": 5,
    "overhang_window_days": 21, "overhang_magnitudes": ["high", "extreme"],
    "overhang_min_strength": 60, "market_tension_ticker": "^VIX", "benchmark": "SPY",
}


# ── RSI ──────────────────────────────────────────────────────────────────────

def test_rsi_all_gains_is_100():
    closes = [float(i) for i in range(1, 30)]  # monotonic up
    assert et.rsi(closes, 14) == 100.0


def test_rsi_all_losses_is_low():
    closes = [float(i) for i in range(30, 1, -1)]  # monotonic down
    assert et.rsi(closes, 14) == 0.0


def test_rsi_none_when_too_short():
    assert et.rsi([1.0, 2.0, 3.0], 14) is None


# ── stretch / vol / return / drawdown ────────────────────────────────────────

def test_stretch_vs_ma_positive_when_above_mean():
    closes = [10.0] * 19 + [11.0]  # last 5% above a flat 10 mean (MA over 20 incl. last)
    s = et.stretch_vs_ma(closes, 20)
    assert s is not None and s > 0


def test_vol_ratio_elevated_when_recent_choppier():
    # calm baseline then a volatile tail → short vol > long vol
    calm = [100.0]
    for _ in range(80):
        calm.append(calm[-1] * 1.001)
    choppy = list(calm)
    for i in range(12):
        choppy.append(choppy[-1] * (1.05 if i % 2 == 0 else 0.95))
    vr = et.vol_ratio(choppy, 10, 90)
    assert vr is not None and vr > 1.5


def test_pct_return_and_drawdown():
    closes = [100.0, 102.0, 101.0, 99.0, 98.0, 95.0]
    assert et.pct_return(closes, 5) == round((95.0 / 100.0 - 1) * 100, 2)
    dd = et.drawdown_from_local_high(closes, 20)
    assert dd is not None and dd < 0  # below the local high of 102


# ── stabilization (the discriminator) ────────────────────────────────────────

def test_stabilizing_true_on_consecutive_up_closes():
    closes = [100, 96, 93, 91, 92, 93]  # fell, then 2 up closes
    assert et.is_stabilizing([float(c) for c in closes], up_closes=2, reclaim_ma=5) is True


def test_falling_knife_not_stabilizing():
    closes = [float(c) for c in [100, 98, 96, 94, 92, 90]]  # still dropping every day
    assert et.is_stabilizing(closes, up_closes=2, reclaim_ma=5) is False


# ── state classification ──────────────────────────────────────────────────────

def test_state_calm_when_no_tension():
    assert et.classify_state(rsi_v=55, stretch_v=1.0, vol_ratio_v=1.0,
                             short_ret=0.5, ddown=-1.0, stabilizing=False, cfg=CFG) == "neutral"


def test_state_stretched_when_overbought_and_extended():
    assert et.classify_state(rsi_v=75, stretch_v=9.0, vol_ratio_v=1.1,
                             short_ret=2.0, ddown=-0.5, stabilizing=False, cfg=CFG) == "overbought"


def test_state_stretched_on_either_hard_line():
    # only RSI over the hard line (stretch below) → still stretched (was AND, now OR)
    assert et.classify_state(rsi_v=72, stretch_v=3.0, vol_ratio_v=1.0,
                             short_ret=1.0, ddown=-0.5, stabilizing=False, cfg=CFG) == "overbought"
    # only stretch over the hard line (RSI below) → still stretched
    assert et.classify_state(rsi_v=60, stretch_v=8.5, vol_ratio_v=1.0,
                             short_ret=1.0, ddown=-0.5, stabilizing=False, cfg=CFG) == "overbought"


def test_state_stretched_on_multi_axis_borderline():
    # the cybersecurity_commercial 2026-06-06 case: JUST under every hard line on 3 warm axes
    # at once → "overbought" (used to fall through to "neutral").
    assert et.classify_state(rsi_v=68.9, stretch_v=7.75, vol_ratio_v=1.31,
                             short_ret=2.46, ddown=-4.7, stabilizing=False, cfg=CFG) == "overbought"


def test_state_single_warm_axis_is_not_stretched():
    # only ONE warm axis (mildly warm RSI, no stretch, calm vol) → not a chasing cluster → calm
    assert et.classify_state(rsi_v=66, stretch_v=1.0, vol_ratio_v=1.0,
                             short_ret=0.5, ddown=-1.0, stabilizing=False, cfg=CFG) == "neutral"
    # a falling knife with only vol warm (1 axis) must NOT be mislabeled stretched
    assert et.classify_state(rsi_v=35, stretch_v=-6.0, vol_ratio_v=1.3,
                             short_ret=-4.0, ddown=-7.0, stabilizing=False, cfg=CFG) == "falling"


def test_state_falling_unstable_vs_stabilizing():
    # elevated vol + drawdown + falling, not stabilized → knife
    assert et.classify_state(rsi_v=35, stretch_v=-6.0, vol_ratio_v=1.8,
                             short_ret=-4.0, ddown=-7.0, stabilizing=False, cfg=CFG) == "falling"
    # same tension but turned up → stabilizing
    assert et.classify_state(rsi_v=35, stretch_v=-6.0, vol_ratio_v=1.8,
                             short_ret=-4.0, ddown=-7.0, stabilizing=True, cfg=CFG) == "basing"


# ── trend deadband (A′) — de-noise the `falling` gate ─────────────────────────

def test_trend_deadband_scales_with_vol_and_disables_at_zero_k():
    import random
    random.seed(0)
    closes = [100.0]
    for _ in range(120):                       # ~2% daily vol → 5d-sum SE ≈ 0.02·√5·100 ≈ 4.5%
        closes.append(closes[-1] * (1 + random.gauss(0, 0.02)))
    band = et.trend_deadband_pct(closes, dict(CFG, trend_deadband_k=1.0))
    assert 3.0 < band < 6.0                     # ≈ 1 SE of a 5-day move at ~2% daily vol
    assert et.trend_deadband_pct(closes, dict(CFG, trend_deadband_k=0.0)) == 0.0   # disabled
    assert et.trend_deadband_pct([100.0, 101.0, 102.0], dict(CFG, trend_deadband_k=1.0)) == 0.0  # too short


def test_state_deadband_treats_subnoise_5d_as_flat():
    # in a drawdown, but the bearish 5d move is WITHIN the noise band → not a decline → calm
    # (this is the run-to-run coin-flip the band kills: same name, ±noise, used to flicker)
    assert et.classify_state(rsi_v=50, stretch_v=-1.0, vol_ratio_v=1.0,
                             short_ret=-2.0, ddown=-4.0, stabilizing=False, cfg=CFG,
                             trend_deadband_pct=4.5) == "neutral"
    # the SAME setup but the move is BEYOND the band → a real decline → falling_unstable
    assert et.classify_state(rsi_v=50, stretch_v=-1.0, vol_ratio_v=1.0,
                             short_ret=-5.0, ddown=-4.0, stabilizing=False, cfg=CFG,
                             trend_deadband_pct=4.5) == "falling"
    # default deadband 0.0 reproduces the legacy raw-sign gate (a tiny dip still counts as falling)
    assert et.classify_state(rsi_v=50, stretch_v=-1.0, vol_ratio_v=1.0,
                             short_ret=-0.2, ddown=-4.0, stabilizing=False, cfg=CFG) == "falling"


def test_tension_score_higher_when_more_stressed_and_relieved_by_stabilization():
    stressed = et.tension_score(rsi_v=28, vol_ratio_v=2.0, ddown=-10.0, stabilizing=False)
    relieved = et.tension_score(rsi_v=28, vol_ratio_v=2.0, ddown=-10.0, stabilizing=True)
    calm = et.tension_score(rsi_v=52, vol_ratio_v=1.0, ddown=-1.0, stabilizing=False)
    assert stressed > relieved > calm


# ── event overhang resolution ─────────────────────────────────────────────────

TODAY = date(2026, 6, 6)


def _ev(eid, event_date, magnitude="high", strength=70, related=None, status="active"):
    return {"id": eid, "event_date": event_date, "magnitude": magnitude,
            "strength_score": strength, "related_catalyst_ids": related or [],
            "status": status, "description": "Mega event. Second sentence ignored."}


def test_overhang_direct_listing_in_window():
    ev = _ev("cat_20260615_spacex_ipo", "2026-06-15T00:00:00Z")
    out = et.select_overhangs([ev], active_ids=["cat_20260615_spacex_ipo"], today=TODAY, cfg=CFG)
    assert len(out) == 1
    o = out[0]
    assert o["catalyst_id"] == "cat_20260615_spacex_ipo"
    assert o["upcoming"] is True and o["days_until"] == 9
    assert o["description"] == "Mega event"  # first sentence only


def test_overhang_linked_via_structural():
    ev = _ev("cat_20260612_peer_print", "2026-06-12", related=["struct_space_economy"])
    out = et.select_overhangs([ev], active_ids=["struct_space_economy"], today=TODAY, cfg=CFG)
    assert len(out) == 1 and out[0]["upcoming"] is True


def test_overhang_excluded_outside_window():
    ev = _ev("cat_20260901_far_event", "2026-09-01")  # ~87 days out
    out = et.select_overhangs([ev], active_ids=["cat_20260901_far_event"], today=TODAY, cfg=CFG)
    assert out == []


def test_overhang_excluded_when_weak_and_low_magnitude():
    ev = _ev("cat_20260610_minor", "2026-06-10", magnitude="low", strength=40)
    out = et.select_overhangs([ev], active_ids=["cat_20260610_minor"], today=TODAY, cfg=CFG)
    assert out == []


def test_overhang_not_touching_sector_excluded():
    ev = _ev("cat_20260610_other", "2026-06-10")
    out = et.select_overhangs([ev], active_ids=["struct_unrelated"], today=TODAY, cfg=CFG)
    assert out == []


def test_recently_fired_event_surfaced_but_not_upcoming():
    ev = _ev("cat_20260602_just_fired", "2026-06-02")  # 4 days ago
    out = et.select_overhangs([ev], active_ids=["cat_20260602_just_fired"], today=TODAY, cfg=CFG)
    assert len(out) == 1 and out[0]["upcoming"] is False and out[0]["days_until"] == -4


# ── verdict ───────────────────────────────────────────────────────────────────

def test_verdict_upcoming_overhang_dominates_state():
    overhangs = [{"upcoming": True, "days_until": 9, "event_date": "2026-06-15"}]
    verdict, when = et.suggest_verdict("neutral", overhangs)
    assert verdict == "wait_event" and when == "2026-06-15"


def test_verdict_follows_state_without_upcoming_overhang():
    assert et.suggest_verdict("neutral", [])[0] == "enter_now"
    assert et.suggest_verdict("basing", [])[0] == "scale_in"
    assert et.suggest_verdict("falling", [])[0] == "wait_stabilize"
    assert et.suggest_verdict("overbought", [])[0] == "wait_stabilize"
    # a past (non-upcoming) overhang does NOT force wait_event
    past = [{"upcoming": False, "days_until": -4, "event_date": "2026-06-02"}]
    assert et.suggest_verdict("neutral", past)[0] == "enter_now"
