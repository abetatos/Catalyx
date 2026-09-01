"""Did the market notice the event we scored 91? (v11 P6 — candidate column, weight 0.)

Every input to `catalyst_alignment` is read from documents: an indicator value someone
recorded, a `narrative_maturity` enum, a `strength_score` an author assigned after reading the
news. Nothing in the chain has ever asked the question a trader asks first — when this
happened, did the sector actually move?

That gap is the whole reason a catalyst score can drift on prose. An event scored 91 that the
tape never noticed is one of two things, and they point opposite ways:

  - MISPRICED — the market has not woken up yet. This is the mandate's entire premise, and
    the correct response is to hold the score and act on it.
  - MISJUDGED — the author read a headline as a repricing event and it was not.

This module cannot tell those apart, and does not try. It NAMES the event, and the naming is
the deliverable: `event_tape_z` is published as a candidate column so `calibration` can
measure, over ≥3 independent 63d windows, whether tape-confirmed catalysts actually rank
better than unconfirmed ones. Only that evidence can settle which of the two readings is
usually right. Until then it carries WEIGHT 0 and gates nothing — auto-downgrading an
unconfirmed catalyst would encode "the market is right and we are late", which is the exact
opposite of what this project is for.

Measurement, per (event, sector):

    abnormal = (sector EUR return over the window) − (benchmark EUR return over the window)
    z        = abnormal / (sector daily vol before the event × √window)
    signed   = z, times −1 for a `contradicts` event (which is confirmed by a FALL)

Both legs are converted to EUR through `nav_engine._eur_prices` before subtraction. This is
not a formality: the operator pays in EUR, the vehicles quote in EUR/GBp/CHF and the benchmark
in USD, so a raw subtraction would book the EURUSD move of the window as sector alpha. Same
defect v8.1 found in the momentum column ("la escala se cancela; la divisa no"), one layer down.

Volatility is measured over the window ENDING THE DAY BEFORE the event, never through it — a
denominator that contains the move it is scaling is a fixed point on itself, the same family of
bug `intensity_engine._trend_delta` documents for same-day history rows.

Usage:
    uv run python -m catalyx.scorer.event_market_check
    uv run python -m catalyx.scorer.event_market_check --json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime, timedelta

from catalyx.config import weights
from catalyx.data.market_data import SECTOR_TICKERS
from catalyx.scorer.catalyst_scorer import compute_catalyst_alignment

_CFG = weights.event_tape_check()
_WINDOW = int(_CFG.get("window_days", 5))
_BENCHMARK = str(_CFG.get("benchmark", "SPY"))
_VOL_LOOKBACK = int(_CFG.get("vol_lookback_days", 120))
_MIN_VOL_DAYS = int(_CFG.get("min_vol_days", 30))
_MAX_AGE = int(_CFG.get("max_event_age_days", 400))


# ── Pure statistics (unit-tested, no I/O) ────────────────────────────────────

def abnormal_z(sector: list[float], bench: list[float], idx: int,
               window: int = _WINDOW, min_vol_days: int = _MIN_VOL_DAYS) -> float | None:
    """Benchmark-relative move over [idx, idx+window], in units of the sector's own prior vol.

    `sector` and `bench` are aligned close series in the SAME currency. `idx` is the position
    of the first trading day at or after the event. Returns None when the window runs past the
    end of the series or the pre-event sample is too short to give the denominator a meaning.
    """
    n = len(sector)
    if n != len(bench) or idx < 0 or idx + window >= n:
        return None
    p0, p1 = sector[idx], sector[idx + window]
    b0, b1 = bench[idx], bench[idx + window]
    if not all(isinstance(v, float) and v > 0 and v == v for v in (p0, p1, b0, b1)):
        return None
    abnormal = (p1 / p0 - 1.0) - (b1 / b0 - 1.0)

    # Strictly BEFORE the event: `sector[:idx]` excludes the event day itself.
    pre = [v for v in sector[max(0, idx - _VOL_LOOKBACK):idx]
           if isinstance(v, float) and v > 0 and v == v]
    if len(pre) < min_vol_days + 1:
        return None
    rets = [pre[i] / pre[i - 1] - 1.0 for i in range(1, len(pre))]
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    vol = var ** 0.5
    if vol <= 0:
        return None
    return round(abnormal / (vol * math.sqrt(window)), 3)


def aggregate(rows: list[dict]) -> dict:
    """Strength-weighted mean signed z over one sector's measured events.

    Weighted by `strength_calibrated`, NOT by `strength_decayed`: the question is whether the
    event was real WHEN IT HAPPENED, and decay answers a different one (how much it still
    matters today). Weighting by the decayed value would quietly make an old event's tape
    verdict count for nothing, which is backwards — an old event has MORE tape to verify it.

    No measured event → `event_tape_z: None`, never 0. A zero here would read as "the market
    was indifferent", which is a finding; "we could not price the window" is not.
    """
    measured = [r for r in rows if r.get("signed_z") is not None]
    unmeasured = [r for r in rows if r.get("signed_z") is None]
    if not measured:
        return {"event_tape_z": None, "event_tape_n": 0,
                "event_tape_unmeasured": len(unmeasured)}
    wts = [max(0.0, float(r.get("strength") or 0.0)) for r in measured]
    total = sum(wts)
    if total <= 0:
        wts, total = [1.0] * len(measured), float(len(measured))
    z = sum(w * r["signed_z"] for w, r in zip(wts, measured)) / total
    return {"event_tape_z": round(z, 3), "event_tape_n": len(measured),
            "event_tape_unmeasured": len(unmeasured)}


# ── Event extraction ─────────────────────────────────────────────────────────

def _events_for_sector(detail: dict) -> list[dict]:
    """Every event the sector's catalyst_alignment rests on, deduped by id.

    Both carriers are included: events linked to a structural (`breakdown[].events[]`) and
    direct events. Only the DOMINANT linked event actually moves the structural's score, but
    the tape check reports on all of them — an event that was registered and is not driving
    anything is precisely the one worth knowing about.
    """
    out: dict[str, dict] = {}
    for sc in detail.get("breakdown") or []:
        for ev in sc.get("events") or []:
            out.setdefault(ev["event_id"], {
                "event_id": ev["event_id"],
                "relation": ev.get("relation", "independent"),
                "event_date": ev.get("event_date"),
                "strength": ev.get("strength_calibrated", ev.get("strength_original")),
                "strength_original": ev.get("strength_original"),
            })
    for de in detail.get("direct_events") or []:
        if de.get("modified_score") is None or "strength_original" not in de:
            continue
        out.setdefault(de["event_id"], {
            "event_id": de["event_id"],
            "relation": de.get("relation", "independent"),
            "event_date": None,       # direct events carry no event_date in the detail payload
            "strength": de.get("strength_calibrated", de.get("strength_original")),
            "strength_original": de.get("strength_original"),
        })
    return list(out.values())


def _anchor_dates() -> dict[str, str]:
    """{event_id: decay anchor date} — the same anchor `catalyst_scorer` decays against, so the
    tape window and the decay clock can never disagree about when an event happened."""
    from catalyx.scorer.catalyst_scorer import _anchor_date, _load_all_event_catalysts

    return {e["id"]: _anchor_date(e) for e in _load_all_event_catalysts() if e.get("id")}


# ── Orchestrator ─────────────────────────────────────────────────────────────

def compute(details_by_sector: dict[str, dict] | None = None, price_fn=None,
            ccy_fn=None, fx_fn=None, today: str | None = None) -> dict:
    """{"sectors": {sector_id: {...}}, "events": [...]} — the per-sector candidate column.

    `details_by_sector` lets a caller that has already run the catalyst scorer (snapshot_repo
    does, for every sector) pass the details in instead of paying for a second pass.
    """
    import pandas as pd

    end = str(today or date.today().isoformat())[:10]
    if details_by_sector is None:
        details_by_sector = {sid: compute_catalyst_alignment(sid) for sid in SECTOR_TICKERS}

    anchors = _anchor_dates()
    per_sector: dict[str, list[dict]] = {}
    for sid, detail in details_by_sector.items():
        if not isinstance(detail, dict):
            continue
        rows = []
        for ev in _events_for_sector(detail):
            d = ev.get("event_date") or anchors.get(ev["event_id"])
            rows.append({**ev, "sector_id": sid, "event_date": str(d)[:10] if d else None})
        if rows:
            per_sector[sid] = rows

    all_rows = [r for rows in per_sector.values() for r in rows]
    if not all_rows:
        return {"sectors": {}, "events": [], "config": dict(_CFG)}

    dates = [r["event_date"] for r in all_rows if r["event_date"]]
    if not dates:
        return {"sectors": {sid: aggregate(rows) for sid, rows in per_sector.items()},
                "events": all_rows, "config": dict(_CFG)}

    # One batched window covering every event plus the vol lookback that precedes the earliest.
    start = (date.fromisoformat(min(dates)) - timedelta(days=_VOL_LOOKBACK * 2)).isoformat()
    tickers = sorted({SECTOR_TICKERS[sid][0] for sid in per_sector if SECTOR_TICKERS.get(sid)})
    tickers = [t for t in tickers if t]
    if not tickers:
        return {"sectors": {sid: aggregate(rows) for sid, rows in per_sector.items()},
                "events": all_rows, "config": dict(_CFG)}

    if price_fn is None:
        from catalyx.data import prices as _prices
        price_fn = _prices.read
    px = price_fn(tickers + [_BENCHMARK], start, end)
    if px is None or getattr(px, "empty", True):
        for r in all_rows:
            r["signed_z"] = None
            r["note"] = "no price history"
        return {"sectors": {sid: aggregate(rows) for sid, rows in per_sector.items()},
                "events": all_rows, "config": dict(_CFG)}

    # EUR on BOTH legs before the subtraction — see the module docstring. A failure here
    # leaves the native series alone and flags it, rather than silently booking FX as alpha.
    fx_applied = True
    try:
        from catalyx.execution.nav_engine import (_default_ccy_fn, _default_fx_fn, _eur_prices)
        px = _eur_prices(px, start, end, ccy_fn or _default_ccy_fn, fx_fn or _default_fx_fn)
    except Exception:
        fx_applied = False

    idx = [str(d)[:10] for d in px.index]
    bench = [float(v) if v == v else float("nan")
             for v in px[_BENCHMARK]] if _BENCHMARK in px.columns else None

    cutoff = (date.fromisoformat(end) - timedelta(days=_MAX_AGE)).isoformat()
    for sid, rows in per_sector.items():
        tk = (SECTOR_TICKERS.get(sid) or [None])[0]
        series = ([float(v) if v == v else float("nan") for v in px[tk]]
                  if tk and tk in px.columns else None)
        for r in rows:
            r["ticker"] = tk
            r["fx_applied"] = fx_applied
            d = r.get("event_date")
            if series is None or bench is None or not d:
                r["signed_z"] = None
                r["note"] = "no price series" if series is None or bench is None else "no event date"
                continue
            if d < cutoff:
                r["signed_z"] = None
                r["note"] = f"older than max_event_age_days ({_MAX_AGE})"
                continue
            pos = next((i for i, x in enumerate(idx) if x >= d), None)
            if pos is None:
                r["signed_z"] = None
                r["note"] = "event date past the price history"
                continue
            z = abnormal_z(series, bench, pos)
            if z is None:
                r["signed_z"] = None
                r["note"] = "window or pre-event vol sample too short"
                continue
            r["abnormal_z"] = z
            r["signed_z"] = round(-z if r.get("relation") == "contradicts" else z, 3)

    return {"sectors": {sid: aggregate(rows) for sid, rows in per_sector.items()},
            "events": all_rows, "config": dict(_CFG), "fx_applied": fx_applied}


# ── CLI ──────────────────────────────────────────────────────────────────────

def render(res: dict) -> str:
    evs = sorted(res.get("events") or [],
                 key=lambda r: (r.get("signed_z") is not None, -(r.get("strength_original") or 0)))
    out = ["CATALYX — event tape check (v11 P6, candidate column, weight 0)", ""]
    if not res.get("fx_applied", True):
        out.append("  ⚠ EUR conversion unavailable — the benchmark leg is in its native currency,")
        out.append("    so these z's include the FX move of the window. Treat as indicative.")
        out.append("")
    hdr = (f"  {'event':<50} {'sector':<32} {'str':>4} {'z':>7}  note")
    out += [hdr, "  " + "-" * (len(hdr) - 2)]
    for r in evs:
        z = f"{r['signed_z']:+.2f}" if r.get("signed_z") is not None else "—"
        out.append(f"  {r['event_id']:<50} {r['sector_id']:<32} "
                   f"{(r.get('strength_original') or 0):>4.0f} {z:>7}  {r.get('note', '')}")
    out += ["", f"  {'sector':<40} {'tape_z':>8} {'n':>4} {'unmeasured':>12}",
            "  " + "-" * 66]
    for sid, s in sorted(res.get("sectors", {}).items(),
                         key=lambda kv: -(kv[1].get("event_tape_z") if
                                          kv[1].get("event_tape_z") is not None else -99)):
        z = f"{s['event_tape_z']:+.2f}" if s.get("event_tape_z") is not None else "—"
        out.append(f"  {sid:<40} {z:>8} {s['event_tape_n']:>4} {s['event_tape_unmeasured']:>12}")
    out += ["", "  A positive z = the tape moved the way the event claimed, in units of the sector's",
            "  own volatility. A negative z on a strong event is the row worth reading: either the",
            "  market has not woken up (the mandate's premise) or the event was over-read. This",
            "  column does not decide which — it carries weight 0 and names the row."]
    return "\n".join(out)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="CATALYX — did the tape confirm the events we scored?")
    p.add_argument("--json", action="store_true", help="Raw JSON output")
    args = p.parse_args()
    res = compute()
    print(json.dumps(res, indent=2, ensure_ascii=False) if args.json else render(res))


if __name__ == "__main__":
    main()
