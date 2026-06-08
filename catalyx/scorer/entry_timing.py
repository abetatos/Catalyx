"""Entry-timing overlay — the micro execution window for a position you've ALREADY decided on.

CATALYX's composite answers WHICH sector to be in; `dislocation.py` answers IF a sector is cheap
vs its fundamentals (opportunity lens: "fell hard + intact + catalyst-confirmed → buy the panic").
This module answers the orthogonal question: **WHEN to enter**. Even when the thesis is right and
the dip is real, you don't want to deploy full size into UNRESOLVED micro-tension (a falling knife
that hasn't based) or right before a known near-term event (an overhang — e.g. a peer mega-IPO whose
lock-up/allocation flow can dump the read-across name). The discriminator is twofold:

  (a) STABILIZATION — has the recent decline actually stopped? (separates a good dip from a knife)
  (b) EVENT OVERHANG — is there a discrete CatalystEvent with an event_date inside the entry window?

Two facets, one advisory verdict:

  MICRO-TENSION (price-derived, deterministic, yfinance): RSI, stretch vs MA20, realized-vol regime,
    short-term trend, drawdown from a local high, and a stabilization check → a `micro_timing_state`
    ∈ {neutral, overbought, falling, basing} plus a `tension_score` for ordering. A market
    backdrop gauge (^VIX level + 5d change, SPY 5d) is attached as context.

    State vocabulary (TA-standard, 2026-06-07 — renamed from calm/stretched/falling_unstable/
    stabilizing): two dichotomies — neutral↔overbought (no-extreme vs overextended up, the oscillator
    axis) and basing↔falling (turned vs still-declining, the drawdown axis).

  EVENT OVERHANG (reuse the existing CatalystEvent model — NO separate registry): a near-term discrete
    event touches the sector (resolved exactly like catalyst_scorer: listed in the study's
    active_catalyst_ids, or linked via related_catalyst_ids to a structural that is). The module
    surfaces the FACT (catalyst_id, event_date, days_until); it does NOT decide bullish-vs-adverse —
    that direction call (is this a whale-dump risk?) is Claude's, with WebSearch, in the skill.

Same philosophy as dislocation/regime: Python computes facts + a SUGGESTED verdict; Claude judges.
Nothing here trades, persists, or moves portfolio weights. Thresholds live in scoring_weights.yaml
(`entry_timing`) — single source of truth, no LLM drift.

CLI:
    uv run python -m catalyx.scorer.entry_timing <sector_id> [--json]
    uv run python -m catalyx.scorer.entry_timing --all [--json]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date, datetime, timezone

from catalyx.config import weights
from catalyx.scorer import catalyst_scorer as cs
from catalyx.scorer.dislocation import yfinance_prices
from catalyx.store import lake

# NOTE: catalyx.data.market_data is imported LAZILY (inside _primary_etf), not here —
# importing it swaps sys.stdout/stderr at module load (its win32 UTF-8 shim), which breaks
# pytest's output capture. The sector→ETF map is the only thing we need from it.


# ── Pure math (unit-tested, no network) ──────────────────────────────────────

def _returns(closes: list[float]) -> list[float]:
    """Simple daily returns from a close series. Skips any non-positive prior price."""
    out = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev:
            out.append(closes[i] / prev - 1.0)
    return out


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder's RSI over the last `period` of a close series. None if too short.
    >70 = overbought (chasing an extended move); <30 = oversold (falling knife)."""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):  # Wilder smoothing over the rest
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return round(100.0 - 100.0 / (1.0 + rs), 1)


def sma(values: list[float], window: int) -> float | None:
    if len(values) < window or window <= 0:
        return None
    return sum(values[-window:]) / window


def stretch_vs_ma(closes: list[float], window: int) -> float | None:
    """% distance of the last price from its `window`-day SMA. Far above → extended
    (mean-reversion risk on a long entry); far below → still falling."""
    m = sma(closes, window)
    if m is None or m == 0:
        return None
    return round((closes[-1] / m - 1.0) * 100.0, 2)


def realized_vol(closes: list[float], window: int) -> float | None:
    """Std of the last `window` daily returns (units cancel in the ratio, so unannualized)."""
    rets = _returns(closes)
    if len(rets) < window or window < 2:
        return None
    return statistics.pstdev(rets[-window:])


def vol_ratio(closes: list[float], short: int, long: int) -> float | None:
    """short-window realized vol / long-window realized vol. >1 = tension rising vs its own baseline."""
    vs = realized_vol(closes, short)
    vl = realized_vol(closes, long)
    if vs is None or vl is None or vl == 0:
        return None
    return round(vs / vl, 2)


def pct_return(closes: list[float], window: int) -> float | None:
    """% return over the last `window` sessions."""
    if len(closes) < window + 1 or closes[-1 - window] == 0:
        return None
    return round((closes[-1] / closes[-1 - window] - 1.0) * 100.0, 2)


def drawdown_from_local_high(closes: list[float], window: int) -> float | None:
    """% below the highest close over the last `window` sessions (≤ 0)."""
    if len(closes) < 2:
        return None
    hi = max(closes[-window:]) if len(closes) >= window else max(closes)
    if hi == 0:
        return None
    return round((closes[-1] / hi - 1.0) * 100.0, 2)


def is_stabilizing(closes: list[float], up_closes: int, reclaim_ma: int) -> bool:
    """Has the recent decline stopped? True when EITHER the last `up_closes` daily closes
    are strictly rising, OR price has reclaimed its `reclaim_ma`-day SMA after having been
    below it in the prior session (a turn back above the short mean). This is the key
    discriminator between a stabilizing dip (enter / scale in) and a falling knife (wait)."""
    if len(closes) >= up_closes + 1:
        tail = closes[-(up_closes + 1):]
        if all(tail[i] > tail[i - 1] for i in range(1, len(tail))):
            return True
    if len(closes) >= reclaim_ma + 1:
        ma_now = sma(closes, reclaim_ma)
        ma_prev = sma(closes[:-1], reclaim_ma)
        if ma_now is not None and ma_prev is not None:
            if closes[-1] >= ma_now and closes[-2] < ma_prev:  # crossed back above the short MA
                return True
    return False


def trend_deadband_pct(closes: list[float], cfg: dict) -> float:
    """Vol-scaled noise band (in %) for the short-horizon `falling` gate (A′, 2026-06-07). A 5d
    move counts as a decline only when it is bearish BEYOND this band; within it the move is
    statistically indistinguishable from flat, so it does not flip a name into the tension branch.

        band = k · σ_daily · √h · 100        (h = short_trend_window)

    σ uses the LONG realized-vol window — a stable noise floor that does NOT itself jump during a
    vol spike, so the band (and hence the classification) stays steady run-to-run. We band the SHORT
    horizon rather than lengthen it: a 5d return is responsive (it catches fresh turns and forgets
    digested gaps), whereas a long OLS slope lags those by ~half its window. Falls back to the short
    vol window, then to 0.0 (legacy raw-sign behaviour) when history is too short to estimate σ."""
    k = float(cfg.get("trend_deadband_k", 0.0) or 0.0)
    if k <= 0:
        return 0.0
    sigma = (realized_vol(closes, cfg["vol_ratio_window_long"])
             or realized_vol(closes, cfg["vol_ratio_window_short"]))
    if not sigma:
        return 0.0
    return round(k * sigma * (cfg["short_trend_window"] ** 0.5) * 100.0, 2)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def classify_state(rsi_v, stretch_v, vol_ratio_v, short_ret, ddown, stabilizing, cfg,
                   trend_deadband_pct: float = 0.0) -> str:
    """Map the micro-tension facts to a state. Precedence: an extended/overbought push is
    `overbought` (chasing); otherwise if there is tension it is `basing` or `falling`
    depending on the stabilization check; otherwise `neutral`.

    `overbought` fires on EITHER hard line (RSI ≥ rsi_overbought OR stretch ≥ stretch_overbought_pct),
    OR when ≥ `borderline_min_axes` of the softer "warm" upside-extension axes (rsi_warm /
    stretch_warm_pct / vol_ratio_warm) trip together. The multi-axis band catches the failure mode
    where a name sits JUST under every hard line at once (RSI 68.9 + +7.75% stretch + vol 1.31) and
    used to read "neutral" — being borderline-overbought AND borderline-extended AND vol-rising
    simultaneously IS chasing. A SINGLE warm axis (e.g. only vol elevated, as in a selloff) does NOT
    qualify — it routes to the falling/basing branch instead, so a knife is never "overbought".

    `trend_deadband_pct` (A′, 2026-06-07) de-noises the `falling` term: a 5d move counts as falling
    only when it is bearish BEYOND this vol-scaled noise band (short_ret < −deadband), so a move
    within ~1 SE of flat is NOT treated as a decline (kills the run-to-run coin-flip on borderline
    names). The band is computed in `assess` from the name's own realized vol; default 0.0 reproduces
    the legacy raw-sign behaviour (so the pure-function tests stay unchanged)."""
    overbought = rsi_v is not None and rsi_v >= cfg["rsi_overbought"]
    extended = stretch_v is not None and stretch_v >= cfg["stretch_overbought_pct"]
    elevated_vol = vol_ratio_v is not None and vol_ratio_v >= cfg["vol_ratio_elevated"]
    oversold = rsi_v is not None and rsi_v <= cfg["rsi_oversold"]
    falling = short_ret is not None and short_ret < -abs(trend_deadband_pct)
    in_drawdown = ddown is not None and ddown <= -3.0

    rsi_warm = rsi_v is not None and rsi_v >= cfg.get("rsi_warm", 65)
    stretch_warm = stretch_v is not None and stretch_v >= cfg.get("stretch_warm_pct", 6.0)
    vol_warm = vol_ratio_v is not None and vol_ratio_v >= cfg.get("vol_ratio_warm", 1.2)
    borderline_axes = sum([rsi_warm, stretch_warm, vol_warm])

    if overbought or extended or borderline_axes >= cfg.get("borderline_min_axes", 2):
        return "overbought"
    if elevated_vol or oversold or (falling and in_drawdown):
        return "basing" if stabilizing else "falling"
    return "neutral"


def tension_score(rsi_v, vol_ratio_v, ddown, stabilizing) -> float:
    """A 0–100 ordering aid (higher = more tense to enter now). Not load-bearing — the STATE
    and the overhang flag drive the verdict; this just ranks sectors in the review surface."""
    comp_vol = _clamp01((vol_ratio_v - 1.0)) if vol_ratio_v is not None else 0.0   # 2.0× → 1.0
    comp_draw = _clamp01(-ddown / 10.0) if ddown is not None else 0.0              # -10% → 1.0
    comp_rsi = _clamp01(abs(rsi_v - 50.0) / 30.0) if rsi_v is not None else 0.0    # extreme → 1.0
    raw = 0.40 * comp_vol + 0.35 * comp_draw + 0.25 * comp_rsi
    if stabilizing:
        raw *= 0.6  # a confirmed turn relieves tension
    return round(100.0 * _clamp01(raw), 1)


# ── Event overhang resolution (reuse CatalystEvent — no new registry) ─────────

def _parse_anchor(anchor: str) -> date | None:
    """Parse the event anchor date (from catalyst_scorer._anchor_date) to a date."""
    if not anchor:
        return None
    try:
        dt = datetime.fromisoformat(str(anchor).replace("Z", "+00:00"))
        return dt.date()
    except ValueError:
        try:
            return datetime.strptime(str(anchor)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def select_overhangs(events: list[dict], active_ids: list[str], today: date, cfg: dict) -> list[dict]:
    """PURE: from already-loaded event catalysts, pick the near-term ones that TOUCH this sector.

    Touches: the event id is listed directly in the study's active_catalyst_ids, OR the event's
    related_catalyst_ids points to an id that is listed there (mirrors catalyst_scorer's direct +
    linked branches). Near-term: |event_date - today| ≤ overhang_window_days. Market-moving:
    magnitude in overhang_magnitudes OR strength_score ≥ overhang_min_strength.

    Surfaces the fact only — the bullish-vs-adverse (whale-dump) call is left to Claude.
    """
    active = set(active_ids or [])
    window = int(cfg["overhang_window_days"])
    magnitudes = set(cfg.get("overhang_magnitudes") or [])
    min_strength = float(cfg.get("overhang_min_strength", 60))
    out = []
    for e in events:
        if e.get("status") not in (None, "active"):
            continue
        eid = e.get("id")
        related = e.get("related_catalyst_ids") or []
        touches = (eid in active) or any(r in active for r in related)
        if not touches:
            continue
        anchor = _parse_anchor(cs._anchor_date(e))
        if anchor is None:
            continue
        days_until = (anchor - today).days  # >0 upcoming, ≤0 already fired
        if abs(days_until) > window:
            continue
        magnitude = e.get("magnitude")
        strength = float(e.get("strength_score") or 0)
        if magnitude not in magnitudes and strength < min_strength:
            continue
        desc = (e.get("description") or "").strip().split(". ")[0][:160]
        out.append({
            "catalyst_id": eid,
            "event_date": anchor.isoformat(),
            "days_until": days_until,
            "upcoming": days_until >= 0,
            "magnitude": magnitude,
            "strength_score": strength,
            "description": desc,
        })
    out.sort(key=lambda o: (not o["upcoming"], abs(o["days_until"])))
    return out


def overhangs_for_sector(sector_id: str, today: date, cfg: dict) -> list[dict]:
    """Disk wrapper: load the sector study's active_catalyst_ids + all active events, then
    delegate to the pure `select_overhangs`."""
    study = cs._load_sector_study(sector_id)
    active_ids = (study or {}).get("active_catalyst_ids", []) if study else []
    if not active_ids:
        return []
    events = cs._load_all_event_catalysts()
    return select_overhangs(events, active_ids, today, cfg)


# ── Verdict ──────────────────────────────────────────────────────────────────

def suggest_verdict(state: str, overhangs: list[dict]) -> tuple[str, str | None]:
    """Advisory verdict. An UPCOMING overhang dominates the micro-state (the discrete event risk
    is the binding constraint); otherwise the verdict follows the micro-tension state."""
    upcoming = [o for o in overhangs if o["upcoming"]]
    if upcoming:
        nearest = min(upcoming, key=lambda o: o["days_until"])
        return "wait_event", nearest["event_date"]
    return {
        "neutral": "enter_now",
        "basing": "scale_in",
        "overbought": "wait_stabilize",
        "falling": "wait_stabilize",
    }.get(state, "enter_now"), None


# ── Engine ───────────────────────────────────────────────────────────────────

def _primary_etf(sector_id: str) -> str | None:
    from catalyx.data.market_data import SECTOR_TICKERS  # lazy — see import note at top
    tickers = SECTOR_TICKERS.get(sector_id)
    return tickers[0] if tickers else None


def _latest_run_id(lake_dir=None) -> str | None:
    """Latest score_run id from the lake (so a persisted timing read ties to a known run).
    Mirrors how dislocation anchors to the most recent sector_snapshot run."""
    try:
        df = lake.read_table("sector_snapshot", lake_dir=lake_dir)
    except Exception:
        return None
    if df is None or df.empty or "run_id" not in df.columns:
        return None
    vals = df["run_id"].dropna().unique()
    return max(vals) if len(vals) else None


def assess(sector_ids: list[str], cfg: dict | None = None, price_fn=None,
           today: date | None = None, persist: bool = False, run_id: str | None = None,
           lake_dir=None) -> dict:
    """Compute the entry-timing overlay for one or more sectors. ONE price fetch for all tickers
    (mirrors dislocation's batch). Returns a JSON-able dict.

    If `persist`, materialize one flat row per assessed sector to the lake table `entry_timing`
    (keyed by run_id, overwritten) so the static GitHub-Pages dashboard reads it in-browser. Only
    the full-universe (`--all`) pipeline path persists — a single-sector run at /catalyx-open stays
    ephemeral so it never overwrites the run partition with one sector."""
    cfg = cfg or weights.entry_timing()
    price_fn = price_fn or yfinance_prices
    today = today or date.today()

    resolved = [(sid, _primary_etf(sid)) for sid in sector_ids]
    etfs = [etf for _, etf in resolved if etf]
    vix = cfg.get("market_tension_ticker", "^VIX")
    bench = cfg.get("benchmark", "SPY")
    tickers = list(dict.fromkeys(etfs + [vix, bench]))

    prices = None
    if tickers:
        start = (today - _td(cfg["lookback_days"] + 10)).isoformat()
        prices = price_fn(tickers, start, today.isoformat())

    # Market backdrop (shared across sectors)
    market = {"vix": None, "vix_5d_change": None, "spy_5d_pct": None}
    if prices is not None and len(prices):
        market["vix"] = _last(prices, vix)
        vix_series = _col(prices, vix)
        if vix_series and len(vix_series) > 6:
            market["vix_5d_change"] = round(vix_series[-1] - vix_series[-6], 2)
        spy_series = _col(prices, bench)
        if spy_series:
            market["spy_5d_pct"] = pct_return(spy_series, cfg["short_trend_window"])

    results = []
    for sid, etf in resolved:
        if etf is None:
            results.append({"sector_id": sid, "note": "no liquid ETF proxy (not in SECTOR_TICKERS)"})
            continue
        closes = _col(prices, etf) if prices is not None else None
        overhangs = overhangs_for_sector(sid, today, cfg)
        if not closes or len(closes) < cfg["rsi_period"] + 2:
            state = "unknown"
            verdict, ev_date = suggest_verdict(state, overhangs)
            results.append({
                "sector_id": sid, "primary_etf": etf, "micro_timing_state": state,
                "note": "insufficient price history", "event_overhangs": overhangs,
                "suggested_verdict": verdict, "wait_until": ev_date,
            })
            continue

        rsi_v = rsi(closes, cfg["rsi_period"])
        stretch_v = stretch_vs_ma(closes, cfg["ma_stretch_window"])
        vol_v = vol_ratio(closes, cfg["vol_ratio_window_short"], cfg["vol_ratio_window_long"])
        short_ret = pct_return(closes, cfg["short_trend_window"])
        ddown = drawdown_from_local_high(closes, cfg["drawdown_local_high_window"])
        stabilizing = is_stabilizing(closes, cfg["stabilization_up_closes"],
                                     cfg["stabilization_reclaim_ma"])
        band_pct = trend_deadband_pct(closes, cfg)
        state = classify_state(rsi_v, stretch_v, vol_v, short_ret, ddown, stabilizing, cfg, band_pct)
        tscore = tension_score(rsi_v, vol_v, ddown, stabilizing)
        verdict, ev_date = suggest_verdict(state, overhangs)

        results.append({
            "sector_id": sid, "primary_etf": etf,
            "micro_timing_state": state, "tension_score": tscore,
            "rsi_14": rsi_v, "stretch_vs_ma20_pct": stretch_v, "vol_ratio_10_90": vol_v,
            "return_5d_pct": short_ret, "drawdown_from_20d_high_pct": ddown,
            "trend_deadband_pct": band_pct,
            "stabilizing": stabilizing,
            "event_overhangs": overhangs,
            "suggested_verdict": verdict, "wait_until": ev_date,
        })

    if persist:
        run_id = run_id or _latest_run_id(lake_dir)
        if run_id:
            _persist_lake(run_id, today, market, results, lake_dir)

    return {
        "as_of": today.isoformat(), "market": market, "n_sectors": len(results),
        "run_id": run_id if persist else None, "sectors": results,
        "note": "Execution-timing overlay (recommend-only). Python surfaces the micro-tension facts "
                "+ near-term event overhangs; the enter/scale/wait and the bullish-vs-adverse "
                "event read are Claude's, with WebSearch. Not a block, never auto-trades.",
    }


def _persist_lake(run_id: str, today: date, market: dict, results: list[dict], lake_dir) -> None:
    """One flat row per assessed sector → lake table `entry_timing` (overwrite per run). Overhangs
    (a list) are flattened to a count + the nearest upcoming one so the row stays queryable in
    DuckDB-WASM. Sectors without a usable price read (no ETF / short history) are skipped."""
    import pandas as pd

    computed_at = datetime.now(timezone.utc)
    recs = []
    for s in results:
        state = s.get("micro_timing_state")
        if state in (None, "unknown"):
            continue
        overhangs = s.get("event_overhangs") or []
        upcoming = [o for o in overhangs if o.get("upcoming")]
        nearest = min(upcoming, key=lambda o: o["days_until"]) if upcoming else None
        recs.append({
            "run_id": run_id, "computed_at": computed_at, "as_of": today.isoformat(),
            "sector_id": s["sector_id"], "primary_etf": s.get("primary_etf"),
            "micro_timing_state": state, "tension_score": s.get("tension_score"),
            "rsi_14": s.get("rsi_14"), "stretch_vs_ma20_pct": s.get("stretch_vs_ma20_pct"),
            "vol_ratio_10_90": s.get("vol_ratio_10_90"), "return_5d_pct": s.get("return_5d_pct"),
            "trend_deadband_pct": s.get("trend_deadband_pct"),
            "drawdown_from_20d_high_pct": s.get("drawdown_from_20d_high_pct"),
            "stabilizing": bool(s.get("stabilizing")),
            "suggested_verdict": s.get("suggested_verdict"), "wait_until": s.get("wait_until"),
            "n_overhangs": len(overhangs), "has_upcoming_overhang": bool(upcoming),
            "nearest_overhang_id": nearest["catalyst_id"] if nearest else None,
            "nearest_overhang_date": nearest["event_date"] if nearest else None,
            "nearest_overhang_days_until": nearest["days_until"] if nearest else None,
            "vix": market.get("vix"), "vix_5d_change": market.get("vix_5d_change"),
            "spy_5d_pct": market.get("spy_5d_pct"),
        })
    if recs:
        lake.append_partition("entry_timing", pd.DataFrame(recs), {"run_id": run_id},
                              overwrite=True, lake_dir=lake_dir)


# ── small helpers over the price frame (kept network-free) ────────────────────

def _td(days: int):
    from datetime import timedelta
    return timedelta(days=days)


def _col(prices, ticker) -> list[float] | None:
    """Column of a yfinance frame as a clean list of floats (NaNs dropped), or None."""
    if prices is None or ticker not in getattr(prices, "columns", []):
        return None
    s = prices[ticker].dropna()
    return [float(x) for x in s.tolist()] if len(s) else None


def _last(prices, ticker) -> float | None:
    col = _col(prices, ticker)
    return round(col[-1], 2) if col else None


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="CATALYX entry-timing overlay (recommend-only)")
    p.add_argument("sector_id", nargs="?", help="sector_id to assess (omit with --all)")
    p.add_argument("--all", action="store_true", help="assess every sector with a study")
    p.add_argument("--no-persist", action="store_true",
                   help="do not write the entry_timing lake table (only --all persists by default)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.all:
        sector_ids = cs._all_sector_ids()
    elif args.sector_id:
        sector_ids = [args.sector_id]
    else:
        p.error("provide a sector_id or --all")

    # Only the full-universe pipeline path persists (so a single-sector ad-hoc run at
    # /catalyx-open never overwrites the run partition with one sector).
    r = assess(sector_ids, persist=args.all and not args.no_persist)
    if args.json:
        print(json.dumps(r, indent=2, ensure_ascii=False, default=str))
        return

    m = r["market"]
    print(f"CATALYX — Entry timing   as_of={r['as_of']}   "
          f"VIX={m['vix']} (Δ5d {m['vix_5d_change']})   SPY 5d={m['spy_5d_pct']}%\n")
    hdr = f"  {'sector':<34}{'etf':<9}{'state':<17}{'tens':>5}{'rsi':>6}{'strch':>7}{'vol':>6}{'5d%':>7}  verdict"
    print(hdr)
    for s in r["sectors"]:
        if "micro_timing_state" not in s or s.get("micro_timing_state") in ("unknown", None):
            print(f"  {s['sector_id']:<34}{str(s.get('primary_etf') or '—'):<9}{'—':<17}"
                  f"{'':>5}{'':>6}{'':>7}{'':>6}{'':>7}  {s.get('note','')}")
            continue
        print(f"  {s['sector_id']:<34}{str(s['primary_etf']):<9}{s['micro_timing_state']:<17}"
              f"{s['tension_score']:>5}{str(s['rsi_14']):>6}{str(s['stretch_vs_ma20_pct']):>7}"
              f"{str(s['vol_ratio_10_90']):>6}{str(s['return_5d_pct']):>7}  "
              f"{s['suggested_verdict']}{(' @'+s['wait_until']) if s['wait_until'] else ''}")
        for o in s["event_overhangs"]:
            when = f"in {o['days_until']}d" if o["upcoming"] else f"{-o['days_until']}d ago"
            print(f"      ⚠ overhang {o['catalyst_id']} ({o['magnitude']}, {when}): {o['description']}")
    print(f"\n  {r['note']}")


if __name__ == "__main__":
    main()
