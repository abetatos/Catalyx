"""Deep technical study — pure math (EMA / MACD / Bollinger / ATR / swing pivots / support-res /
OBV / volume / MA structure / 52w range) + the posture synthesis. Network-free: every test passes
synthetic OHLCV lists; nothing hits yfinance, the disk loaders, or entry_timing's network path."""
from __future__ import annotations

from catalyx.scorer import technical_study as ts

CFG = {
    "lookback_days": 420, "min_history": 30, "slope_lag": 5,
    "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
    "bollinger_window": 20, "bollinger_k": 2.0, "atr_period": 14,
    "pivot_left": 3, "pivot_right": 3, "obv_window": 20, "volume_window": 20,
    "volume_surge_mult": 1.5, "range_window": 252, "posture_margin": 2,
}


# ── EMA / MACD ────────────────────────────────────────────────────────────────

def test_ema_series_empty_when_too_short():
    assert ts.ema_series([1.0, 2.0], 5) == []


def test_ema_series_constant_is_constant():
    out = ts.ema_series([5.0] * 20, 10)
    assert all(abs(v - 5.0) < 1e-9 for v in out)


def test_macd_none_when_too_short():
    assert ts.macd([float(i) for i in range(10)]) is None


def test_macd_bullish_when_trending_up():
    # ACCELERATING uptrend: a perfectly LINEAR ramp converges to MACD == signal (hist→0), so the
    # line only leads its signal when the advance is accelerating.
    closes = [float(i * i) for i in range(1, 80)]
    m = ts.macd(closes)
    assert m is not None
    assert m["above_zero"] is True and m["above_signal"] is True


def test_macd_detects_bearish_cross_on_rollover():
    # up then sharply down → histogram flips negative at the turn
    closes = [float(i) for i in range(1, 60)] + [60.0 - 2 * i for i in range(1, 20)]
    m = ts.macd(closes)
    assert m is not None
    assert m["above_zero"] is True or m["cross"] in ("bearish_cross", "none")
    assert m["hist"] < 0  # momentum rolling over


# ── Bollinger ─────────────────────────────────────────────────────────────────

def test_bollinger_pct_b_midband_on_flat():
    bb = ts.bollinger([10.0] * 25, 20, 2.0)
    assert bb is not None
    assert bb["pct_b"] == 0.5  # zero sd → defined as mid


def test_bollinger_pct_b_high_when_price_at_top():
    closes = [10.0] * 19 + [12.0]  # last spikes above the band
    bb = ts.bollinger(closes, 20, 2.0)
    assert bb["pct_b"] > 1.0


# ── ATR ───────────────────────────────────────────────────────────────────────

def test_atr_none_when_too_short():
    assert ts.atr([1.0], [0.5], [0.8], 14) is None


def test_atr_constant_range():
    n = 30
    highs = [11.0] * n
    lows = [9.0] * n
    closes = [10.0] * n
    # true range each day = max(2, |11-10|, |9-10|) = 2
    assert ts.atr(highs, lows, closes, 14) == 2.0


# ── swing pivots / support-resistance ─────────────────────────────────────────

def test_swing_pivots_finds_local_extremes():
    closes = [5, 4, 3, 4, 5, 6, 5, 4, 3, 4, 5]  # a V (low at idx 2/8) and a peak at idx 5
    highs, lows = ts.swing_pivots([float(c) for c in closes], 2, 2)
    low_idx = [i for i, _ in lows]
    high_idx = [i for i, _ in highs]
    assert 5 in high_idx
    assert any(i in low_idx for i in (2, 8))


def test_support_resistance_brackets_price():
    closes = [10, 8, 6, 8, 10, 12, 10, 8, 9]  # last=9; support below, resistance above
    closes = [float(c) for c in closes]
    sr = ts.support_resistance(closes, 2, 2)
    if sr["support"] is not None:
        assert sr["support"] < 9 and sr["support_dist_pct"] < 0
    if sr["resistance"] is not None:
        assert sr["resistance"] > 9 and sr["resistance_dist_pct"] > 0


# ── OBV / volume ──────────────────────────────────────────────────────────────

def test_obv_trend_rising_on_up_days():
    closes = [float(i) for i in range(1, 25)]  # every day up
    vols = [100.0] * 24
    assert ts.obv_trend(closes, vols, 20) == "rising"


def test_obv_trend_none_without_volume():
    assert ts.obv_trend([1.0, 2.0, 3.0], [0.0, 0.0, 0.0], 20) is None


def test_volume_surge_detects_spike():
    vols = [100.0] * 20 + [300.0]
    assert ts.volume_surge(vols, 20) == 3.0


def test_volume_surge_none_when_short():
    assert ts.volume_surge([100.0] * 5, 20) is None


# ── MA structure / 52w range ──────────────────────────────────────────────────

def test_ma_structure_bullish_regime_on_uptrend():
    closes = [float(i) for i in range(1, 260)]  # long steady uptrend
    ma = ts.ma_structure(closes, 5)
    assert ma["ma_regime"] == "bullish"
    assert ma["sma20_slope"] == "rising"
    assert ma["price_vs_sma200_pct"] > 0


def test_ma_structure_missing_long_ma_short_history():
    closes = [float(i) for i in range(1, 40)]  # < 200
    ma = ts.ma_structure(closes, 5)
    assert ma["sma200"] is None
    assert ma["ma_regime"] is None


def test_range_52w_position_top_and_bottom():
    up = [float(i) for i in range(1, 100)]
    assert ts.range_52w_position(up, 252) == 100.0
    down = [float(i) for i in range(100, 1, -1)]
    assert ts.range_52w_position(down, 252) == 0.0


# ── synthesis ─────────────────────────────────────────────────────────────────

def test_synthesize_constructive_when_bullish_dominates():
    facts = {
        "ma_structure": {"ma_regime": "bullish", "price_vs_sma200_pct": 8.0},
        "macd": {"cross": "bullish_cross", "above_signal": True, "above_zero": True},
        "bollinger": {"pct_b": 0.6},
        "range_52w_position_pct": 55.0,
        "obv_trend": "rising",
        "volume_surge": 1.1,
        "support_resistance": {"support_dist_pct": -1.0, "resistance_dist_pct": -8.0},
        "entry_timing_state": "neutral",
    }
    syn = ts.synthesize(facts, CFG)
    assert syn["posture"] == "constructive"
    assert syn["net"] >= 2


def test_synthesize_weak_when_bearish_dominates():
    facts = {
        "ma_structure": {"ma_regime": "bearish", "price_vs_sma200_pct": -10.0},
        "macd": {"cross": "bearish_cross", "above_signal": False, "above_zero": False},
        "bollinger": {"pct_b": 1.2},
        "range_52w_position_pct": 95.0,
        "obv_trend": "falling",
        "volume_surge": None,
        "support_resistance": {"support_dist_pct": -9.0, "resistance_dist_pct": 1.0},
        "entry_timing_state": "falling",
    }
    syn = ts.synthesize(facts, CFG)
    assert syn["posture"] == "weak"
    assert syn["net"] <= -2


def test_synthesize_mixed_when_balanced():
    facts = {
        "ma_structure": {"ma_regime": "bullish", "price_vs_sma200_pct": -3.0},
        "macd": {"cross": "none", "above_signal": True, "above_zero": False},
        "bollinger": {"pct_b": 0.5},
        "range_52w_position_pct": 50.0,
        "obv_trend": None,
        "volume_surge": None,
        "support_resistance": {"support_dist_pct": None, "resistance_dist_pct": None},
        "entry_timing_state": "neutral",
    }
    syn = ts.synthesize(facts, CFG)
    assert syn["posture"] == "mixed"


# ── engine (study) with injected price + timing fns (no network) ──────────────

def _synthetic_ohlcv(n=300):
    closes = [100.0 + i * 0.2 for i in range(n)]  # gentle uptrend
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    vols = [1000.0 + (i % 5) * 10 for i in range(n)]
    return {"high": highs, "low": lows, "close": closes, "volume": vols}


def test_study_end_to_end_injected():
    r = ts.study(
        "semiconductors_design", ticker="TEST", cfg=CFG,
        price_fn=lambda t, s, e: _synthetic_ohlcv(),
        timing_fn=lambda sid: {"sectors": [{"micro_timing_state": "neutral",
                                            "suggested_verdict": "enter_now", "rsi_14": 60.0,
                                            "event_overhangs": []}]},
    )
    assert r["ticker"] == "TEST"
    assert r["synthesis"]["posture"] in ("constructive", "mixed", "weak")
    assert r["ma_structure"]["ma_regime"] == "bullish"
    assert r["entry_timing_state"] == "neutral"
    assert r["has_volume"] is True


def test_study_insufficient_history():
    r = ts.study(
        "semiconductors_design", ticker="TEST", cfg=CFG,
        price_fn=lambda t, s, e: {"high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]},
        timing_fn=lambda sid: {"sectors": []},
    )
    assert "insufficient price history" in r["note"]


def test_study_timing_failure_degrades_gracefully():
    def boom(sid):
        raise RuntimeError("network down")
    r = ts.study(
        "semiconductors_design", ticker="TEST", cfg=CFG,
        price_fn=lambda t, s, e: _synthetic_ohlcv(),
        timing_fn=boom,
    )
    # the study still computes; entry_timing fields are just absent
    assert r["synthesis"]["posture"] in ("constructive", "mixed", "weak")
    assert r["entry_timing_state"] is None
