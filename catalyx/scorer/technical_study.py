"""Deep technical study — the opt-in, pre-open micro review of the actual vehicle.

This is the THOROUGH cousin of `entry_timing.py`. `entry_timing` is a fast, always-on overlay
(RSI / stretch-vs-MA20 / vol regime / 5d drawdown / stabilization + event overhang → a one-line
verdict) that runs on EVERY sector in the pipeline. This module is offered OPT-IN at `/catalyx-open`,
on the single ETF you are about to buy, when you want to "revisarlo todo antes de abrir" — a fuller
deterministic technical-analysis dossier before committing capital.

It is a SUPERSET of entry_timing: it embeds entry_timing's micro-tension read verbatim (no
re-derivation, single source of truth for RSI/state/verdict) and adds the layers a deeper TA review
wants:

  • MA STRUCTURE  — SMA20/50/200, price vs each, each MA's slope, and the 50/200 regime (the
                    "bullish/bearish trend" backdrop a single MA20 stretch can't see).
  • MACD(12,26,9) — line / signal / histogram + the latest cross (momentum turning up or down).
  • BOLLINGER     — %B (where price sits in the 20d ±2σ band) + bandwidth (a squeeze = a pending
                    expansion; an %B>1 / <0 = a band-riding extreme).
  • ATR(14)       — average true range, absolute + as % of price → stop-distance context for sizing.
  • SUPPORT/RES   — nearest swing-pivot low below and pivot high above the current price, with the
                    distance % (how much room before a level, where a stop or an add makes sense).
  • VOLUME        — latest vs 20d-average (a surge confirms a move) + OBV trend (accumulation vs
                    distribution) — the only facet that needs OHLCV, not just closes.
  • 52-WEEK RANGE — % position between the 1y low and high (buying near a high vs off a base).

Then a SYNTHESIS: each fact is bucketed bullish / bearish / neutral, and the net tally maps to a
`technical_posture` ∈ {constructive, mixed, weak}. SAME doctrine as the rest of the platform —
Python surfaces the facts and a suggested posture; the enter/scale/wait call is Claude's (with the
fundamental thesis + WebSearch context). Nothing here trades, persists, or moves portfolio weights;
it is ephemeral decision support, like a single-sector entry_timing run.

Thresholds/periods live in scoring_weights.yaml (`technical_study`) — single source of truth.

CLI:
    uv run python -m catalyx.scorer.technical_study <sector_id> [--ticker TICK] [--json]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date, timedelta

from catalyx.config import weights
from catalyx.scorer import entry_timing as et


# ── Pure math (unit-tested, network-free) ─────────────────────────────────────

def ema_series(values: list[float], period: int) -> list[float]:
    """EMA series, seeded with the SMA of the first `period` values (Wilder/standard convention).
    Aligned so out[0] corresponds to values[period-1]. Empty if too short."""
    if period <= 0 or len(values) < period:
        return []
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out = [seed]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict | None:
    """MACD(fast,slow,signal). Returns line/signal/hist (latest) + the latest cross direction and
    the above-signal / above-zero booleans. None if history is too short for a signal line."""
    if len(closes) < slow + signal:
        return None
    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, slow)
    off = len(ema_fast) - len(ema_slow)            # fast series is longer by (slow-fast)
    macd_line = [ema_fast[i + off] - ema_slow[i] for i in range(len(ema_slow))]
    signal_line = ema_series(macd_line, signal)
    if not signal_line:
        return None
    off2 = len(macd_line) - len(signal_line)
    hist = [macd_line[i + off2] - signal_line[i] for i in range(len(signal_line))]
    hist_now = hist[-1]
    hist_prev = hist[-2] if len(hist) >= 2 else None
    cross = "none"
    if hist_prev is not None:
        if hist_prev <= 0 < hist_now:
            cross = "bullish_cross"
        elif hist_prev >= 0 > hist_now:
            cross = "bearish_cross"
    return {
        "macd": round(macd_line[-1], 4), "signal": round(signal_line[-1], 4),
        "hist": round(hist_now, 4), "cross": cross,
        "above_signal": macd_line[-1] > signal_line[-1], "above_zero": macd_line[-1] > 0,
    }


def bollinger(closes: list[float], window: int = 20, k: float = 2.0) -> dict | None:
    """Bollinger band read on the last `window` closes: %B (0=lower band, 1=upper) + bandwidth
    ((upper-lower)/mid, a squeeze gauge). None if too short."""
    if len(closes) < window or window <= 0:
        return None
    seg = closes[-window:]
    mid = sum(seg) / window
    sd = statistics.pstdev(seg)
    upper, lower = mid + k * sd, mid - k * sd
    last = closes[-1]
    pct_b = (last - lower) / (upper - lower) if upper != lower else 0.5
    bandwidth = (upper - lower) / mid if mid else None
    return {
        "upper": round(upper, 2), "lower": round(lower, 2), "mid": round(mid, 2),
        "pct_b": round(pct_b, 2), "bandwidth": round(bandwidth, 4) if bandwidth is not None else None,
    }


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    """Wilder ATR over true ranges. None if too short."""
    n = min(len(highs), len(lows), len(closes))
    if n < period + 1:
        return None
    trs = []
    for i in range(1, n):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    a = sum(trs[:period]) / period
    for tr in trs[period:]:
        a = (a * (period - 1) + tr) / period
    return round(a, 4)


def swing_pivots(closes: list[float], left: int = 3, right: int = 3) -> tuple[list, list]:
    """Fractal swing pivots on the close series. A pivot high is a STRICT local max over the
    [i-left, i+right] window (unique max); pivot low symmetric. Returns (highs, lows) as
    lists of (index, price)."""
    highs, lows = [], []
    n = len(closes)
    for i in range(left, n - right):
        window = closes[i - left:i + right + 1]
        c = closes[i]
        if c == max(window) and window.count(c) == 1:
            highs.append((i, c))
        if c == min(window) and window.count(c) == 1:
            lows.append((i, c))
    return highs, lows


def support_resistance(closes: list[float], left: int = 3, right: int = 3) -> dict:
    """Nearest swing-pivot SUPPORT below the last price and RESISTANCE above it, with distance %.
    None on either side if no qualifying pivot exists."""
    highs, lows = swing_pivots(closes, left, right)
    last = closes[-1]
    supports = [p for _, p in lows if p < last]
    resistances = [p for _, p in highs if p > last]
    support = max(supports) if supports else None
    resistance = min(resistances) if resistances else None
    return {
        "support": round(support, 2) if support is not None else None,
        "support_dist_pct": round((support / last - 1) * 100, 2) if support else None,
        "resistance": round(resistance, 2) if resistance is not None else None,
        "resistance_dist_pct": round((resistance / last - 1) * 100, 2) if resistance else None,
    }


def obv_trend(closes: list[float], volumes: list[float], window: int = 20) -> str | None:
    """On-balance-volume trend over the last `window` sessions: 'rising' (accumulation) /
    'falling' (distribution) / 'flat'. None if no volume data."""
    n = min(len(closes), len(volumes))
    if n < 2 or not any(volumes):
        return None
    obv = [0.0]
    for i in range(1, n):
        if closes[i] > closes[i - 1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])
    seg = obv[-window:] if len(obv) >= window else obv
    slope = seg[-1] - seg[0]
    return "rising" if slope > 0 else ("falling" if slope < 0 else "flat")


def volume_surge(volumes: list[float], window: int = 20) -> float | None:
    """Latest volume / average of the PRIOR `window` sessions. >1 = above-average participation.
    None if no usable volume."""
    if len(volumes) < window + 1:
        return None
    prior = volumes[-window - 1:-1]
    avg = sum(prior) / window
    if avg <= 0:
        return None
    return round(volumes[-1] / avg, 2)


def ma_structure(closes: list[float], slope_lag: int = 5) -> dict:
    """SMA20/50/200, price vs each (%), each MA's slope (rising/falling vs `slope_lag` sessions
    ago), and the 50/200 regime (bullish = 50 above 200). Missing MAs → None (short history)."""
    last = closes[-1]
    out: dict = {}
    for w in (20, 50, 200):
        m = et.sma(closes, w)
        out[f"sma{w}"] = round(m, 2) if m else None
        out[f"price_vs_sma{w}_pct"] = round((last / m - 1) * 100, 2) if m else None
        prev = et.sma(closes[:-slope_lag], w) if len(closes) > w + slope_lag else None
        out[f"sma{w}_slope"] = (("rising" if m > prev else "falling" if m < prev else "flat")
                                if (m and prev) else None)
    s50, s200 = out["sma50"], out["sma200"]
    out["ma_regime"] = ("bullish" if s50 > s200 else "bearish") if (s50 and s200) else None
    return out


def range_52w_position(closes: list[float], window: int = 252) -> float | None:
    """% position of the last price within the trailing-`window` (≈1y) low→high range. None if flat."""
    seg = closes[-window:] if len(closes) >= window else closes
    hi, lo, last = max(seg), min(seg), closes[-1]
    if hi == lo:
        return None
    return round((last - lo) / (hi - lo) * 100, 1)


# ── Synthesis ─────────────────────────────────────────────────────────────────

def synthesize(facts: dict, cfg: dict) -> dict:
    """Bucket the computed facts into bullish / bearish / neutral signal strings and map the net
    tally to a `technical_posture`. Deliberately simple and transparent — it ORDERS the evidence,
    it does not make the buy call (that is Claude's, with the thesis). Each entry is a short reason."""
    bull, bear, neu = [], [], []

    ma = facts.get("ma_structure") or {}
    regime = ma.get("ma_regime")
    if regime == "bullish":
        bull.append("50d SMA above 200d (bullish trend regime)")
    elif regime == "bearish":
        bear.append("50d SMA below 200d (bearish trend regime)")
    pv200 = ma.get("price_vs_sma200_pct")
    if pv200 is not None:
        (bull if pv200 >= 0 else bear).append(f"price {pv200:+.1f}% vs 200d SMA")

    m = facts.get("macd") or {}
    if m.get("cross") == "bullish_cross":
        bull.append("MACD bullish cross (histogram turned positive)")
    elif m.get("cross") == "bearish_cross":
        bear.append("MACD bearish cross (histogram turned negative)")
    elif m.get("above_signal") is True and m.get("above_zero") is True:
        bull.append("MACD above signal and above zero")
    elif m.get("above_signal") is False and m.get("above_zero") is False:
        bear.append("MACD below signal and below zero")

    bb = facts.get("bollinger") or {}
    pct_b = bb.get("pct_b")
    if pct_b is not None:
        if pct_b > 1.0:
            bear.append(f"riding the upper Bollinger band (%B {pct_b}) — overextended")
        elif pct_b < 0.0:
            bull.append(f"below the lower Bollinger band (%B {pct_b}) — oversold snap-back zone")
        else:
            neu.append(f"inside the Bollinger band (%B {pct_b})")

    pos = facts.get("range_52w_position_pct")
    if pos is not None:
        if pos >= 90:
            bear.append(f"near 52-week high ({pos}% of range) — little room, chasing")
        elif pos >= 85:
            # 85–90% is NOT "mid" — it's the upper band, extended after a run. Flag it as a
            # caution (not a full bearish signal, so net_signal is unchanged) so the posture
            # doesn't read an extended entry as neutral. (calibration 2026-06-08)
            neu.append(f"upper 52-week range ({pos}%) — extended, buying near the highs")
        elif pos <= 15:
            bull.append(f"near 52-week low ({pos}% of range) — value zone if thesis intact")
        else:
            neu.append(f"mid 52-week range ({pos}%)")

    ovt = facts.get("obv_trend")
    if ovt == "rising":
        bull.append("OBV rising (accumulation)")
    elif ovt == "falling":
        bear.append("OBV falling (distribution)")
    vs = facts.get("volume_surge")
    if vs is not None and vs >= cfg.get("volume_surge_mult", 1.5):
        neu.append(f"volume surge ×{vs} vs 20d avg — move has participation (direction per price)")

    sr = facts.get("support_resistance") or {}
    if sr.get("support_dist_pct") is not None and sr["support_dist_pct"] >= -2.0:
        bull.append(f"sitting just above support ({sr['support_dist_pct']}%) — defined-risk entry")
    if sr.get("resistance_dist_pct") is not None and sr["resistance_dist_pct"] <= 2.0:
        bear.append(f"just below resistance ({sr['resistance_dist_pct']}%) — overhead supply")

    # Embed entry_timing's micro state as one more axis (no re-derivation).
    state = facts.get("entry_timing_state")
    if state == "overbought":
        bear.append("entry_timing: overbought (chasing an extended move)")
    elif state == "falling":
        bear.append("entry_timing: falling (knife not yet based)")
    elif state == "basing":
        bull.append("entry_timing: basing (dip turning up)")
    elif state == "neutral":
        neu.append("entry_timing: neutral (no acute micro-tension)")

    net = len(bull) - len(bear)
    margin = cfg.get("posture_margin", 2)
    posture = "constructive" if net >= margin else ("weak" if net <= -margin else "mixed")
    return {"posture": posture, "net": net, "bullish": bull, "bearish": bear, "neutral": neu}


# ── Price source (injectable, OHLCV) ──────────────────────────────────────────

def yfinance_ohlcv(ticker: str, start: str, end: str) -> dict:
    """High/Low/Close/Volume lists for ONE ticker (auto-adjusted). Network. Injectable for tests."""
    import pandas as pd
    import yfinance as yf

    data = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    if data is None or not len(data):
        return {"high": [], "low": [], "close": [], "volume": []}

    def col(name: str) -> list[float]:
        if isinstance(data.columns, pd.MultiIndex):
            if name not in data.columns.get_level_values(0):
                return []
            s = data[name]
            s = s.iloc[:, 0] if hasattr(s, "columns") else s
        else:
            if name not in data.columns:
                return []
            s = data[name]
        s = s.dropna()
        return [float(x) for x in s.tolist()]

    return {"high": col("High"), "low": col("Low"), "close": col("Close"), "volume": col("Volume")}


# ── Engine ────────────────────────────────────────────────────────────────────

def study(sector_id: str, ticker: str | None = None, cfg: dict | None = None,
          price_fn=None, timing_fn=None, today: date | None = None) -> dict:
    """Deep technical dossier for the vehicle you are about to buy. Resolves the sector's primary
    ETF (overridable with `ticker`), fetches OHLCV once, computes the full indicator set, embeds the
    entry_timing micro read, and synthesizes a posture. JSON-able dict; no persistence."""
    cfg = cfg or weights.technical_study()
    price_fn = price_fn or yfinance_ohlcv
    today = today or date.today()
    tick = ticker or et._primary_etf(sector_id)
    if not tick:
        return {"sector_id": sector_id, "note": "no liquid ETF proxy (not in SECTOR_TICKERS)"}

    start = (today - timedelta(days=cfg["lookback_days"])).isoformat()
    ohlcv = price_fn(tick, start, today.isoformat())
    closes = ohlcv.get("close") or []
    if len(closes) < cfg["min_history"]:
        return {"sector_id": sector_id, "ticker": tick,
                "note": f"insufficient price history ({len(closes)} closes)"}

    highs, lows, vols = ohlcv.get("high") or [], ohlcv.get("low") or [], ohlcv.get("volume") or []
    last = round(closes[-1], 2)
    atr_v = atr(highs, lows, closes, cfg["atr_period"])

    # Embed entry_timing's micro-tension read (its own fetch — single source of truth for RSI/state).
    timing_fn = timing_fn or (lambda sid: et.assess([sid], today=today))
    timing = {}
    try:
        tr = timing_fn(sector_id)
        secs = tr.get("sectors") if isinstance(tr, dict) else None
        timing = (secs[0] if secs else {}) or {}
    except Exception as exc:  # network / disk — degrade, never fail the whole study
        timing = {"note": f"entry_timing unavailable: {exc}"}

    facts = {
        "sector_id": sector_id, "ticker": tick, "as_of": today.isoformat(), "last_price": last,
        "ma_structure": ma_structure(closes, cfg["slope_lag"]),
        "macd": macd(closes, cfg["macd_fast"], cfg["macd_slow"], cfg["macd_signal"]),
        "bollinger": bollinger(closes, cfg["bollinger_window"], cfg["bollinger_k"]),
        "atr": atr_v, "atr_pct": round(atr_v / last * 100, 2) if (atr_v and last) else None,
        "support_resistance": support_resistance(closes, cfg["pivot_left"], cfg["pivot_right"]),
        "obv_trend": obv_trend(closes, vols, cfg["obv_window"]) if vols else None,
        "volume_surge": volume_surge(vols, cfg["volume_window"]) if vols else None,
        "range_52w_position_pct": range_52w_position(closes, cfg["range_window"]),
        "has_volume": bool(vols and any(vols)),
        # entry_timing headline (recommend-only verdict + the micro-tension state)
        "entry_timing_state": timing.get("micro_timing_state"),
        "entry_timing_verdict": timing.get("suggested_verdict"),
        "entry_timing_rsi14": timing.get("rsi_14"),
        "event_overhangs": timing.get("event_overhangs", []),
    }
    facts["synthesis"] = synthesize(facts, cfg)
    facts["note"] = ("Deep technical study (recommend-only). Python surfaces the TA facts + a "
                     "synthesized posture; the enter/scale/wait call is Claude's, with the thesis "
                     "and WebSearch. Not a block, never auto-trades, not persisted.")
    return facts


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="CATALYX deep technical study (opt-in, recommend-only)")
    p.add_argument("sector_id", help="sector_id to study")
    p.add_argument("--ticker", help="override the ETF studied (default: sector primary)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    r = study(args.sector_id, ticker=args.ticker)
    if args.json:
        print(json.dumps(r, indent=2, ensure_ascii=False, default=str))
        return

    if "note" in r and "ma_structure" not in r:
        print(f"{args.sector_id}: {r['note']}")
        return

    ma, m, bb, sr = r["ma_structure"], r.get("macd") or {}, r.get("bollinger") or {}, r["support_resistance"]
    syn = r["synthesis"]
    print(f"CATALYX — Deep technical study   {r['sector_id']}  ({r['ticker']})   "
          f"as_of={r['as_of']}   last={r['last_price']}\n")
    print(f"  POSTURE: {syn['posture'].upper()}   (net {syn['net']:+d})   "
          f"entry_timing: {r['entry_timing_state']} → {r['entry_timing_verdict']}  "
          f"(RSI {r['entry_timing_rsi14']})\n")

    print("  Trend / MAs:")
    for w in (20, 50, 200):
        print(f"    SMA{w:<3} {str(ma.get(f'sma{w}')):>9}   price {str(ma.get(f'price_vs_sma{w}_pct')):>7}%   "
              f"slope {ma.get(f'sma{w}_slope')}")
    print(f"    50/200 regime: {ma.get('ma_regime')}   |   52w range position: {r['range_52w_position_pct']}%")
    print(f"  MACD: line {m.get('macd')}  signal {m.get('signal')}  hist {m.get('hist')}  cross {m.get('cross')}")
    print(f"  Bollinger %B {bb.get('pct_b')}  bandwidth {bb.get('bandwidth')}   |   "
          f"ATR {r['atr']} ({r['atr_pct']}% of price)")
    print(f"  Support {sr['support']} ({sr['support_dist_pct']}%)   "
          f"Resistance {sr['resistance']} ({sr['resistance_dist_pct']}%)")
    print(f"  Volume: surge ×{r['volume_surge']}   OBV {r['obv_trend']}   (has_volume={r['has_volume']})")
    if r.get("event_overhangs"):
        for o in r["event_overhangs"]:
            when = f"in {o['days_until']}d" if o["upcoming"] else f"{-o['days_until']}d ago"
            print(f"    ⚠ overhang {o['catalyst_id']} ({o.get('magnitude')}, {when})")

    print("\n  Signals:")
    for s in syn["bullish"]:
        print(f"    🟢 {s}")
    for s in syn["bearish"]:
        print(f"    🔴 {s}")
    for s in syn["neutral"]:
        print(f"    ⚪ {s}")
    print(f"\n  {r['note']}")


if __name__ == "__main__":
    main()
