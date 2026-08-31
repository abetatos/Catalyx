"""Auto-observable structural indicators — FRED / yfinance / CFTC (v7 O1).

The structural indicator battery is the system's only non-commodity edge, and its measured
state was "every BUY resting on stale or blind evidence". A subset of indicators are plain
public time series; this module fetches those and records the observation through
`indicator_update.apply_one` — the SAME write path as the scan, never a parallel channel.

ONLY indicators whose stored definition MATCHES the public series are mapped. Japan core CPI
(ex-fresh-food) is deliberately NOT mapped: FRED's OECD core is ex-food-AND-energy — a silent
definition swap would move a score for a reason unrelated to the world.

FRED needs FRED_API_KEY (loaded from .env). Missing key → FRED rows are skipped and say so.

CLI:
    uv run python -m catalyx.data.indicator_sources             # dry-run: fetch + compare
    uv run python -m catalyx.data.indicator_sources --apply     # record via indicator_update
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[2]
_FRED_API = "https://api.stlouisfed.org/fred/series/observations"

_LB_PER_TONNE = 2204.62

# Each entry: how one indicator's current value is observed automatically.
#   provider fred            → ref = FRED series id, latest non-missing observation
#   provider yfinance        → ref = ticker, latest close
#   provider cftc_net_short  → ref = CFTC contract code, (short − long)/1000 (thousands net short)
AUTO_INDICATORS: list[dict] = [
    {"catalyst_id": "struct_china_luxury_recovery", "indicator_id": "ind_04",
     "provider": "fred", "ref": "DEXCHUS", "note": "USD/CNY (FRED DEXCHUS)"},
    {"catalyst_id": "struct_em_capital_attraction_india", "indicator_id": "ind_04",
     "provider": "fred", "ref": "DEXINUS", "note": "INR/USD (FRED DEXINUS)"},
    {"catalyst_id": "struct_em_capital_attraction_india", "indicator_id": "ind_01",
     "provider": "yfinance", "ref": "DX-Y.NYB", "note": "ICE DXY (yfinance)"},
    {"catalyst_id": "struct_japan_carry_unwind", "indicator_id": "ind_01",
     "provider": "fred", "ref": "IRSTCI01JPM156N", "note": "BoJ overnight call rate (FRED)"},
    {"catalyst_id": "struct_japan_carry_unwind", "indicator_id": "ind_02",
     "provider": "fred", "ref": "IRLTLT01JPM156N", "note": "10y JGB yield (FRED, OECD)"},
    {"catalyst_id": "struct_japan_carry_unwind", "indicator_id": "ind_03",
     "provider": "cftc_net_short", "ref": "097741", "note": "CFTC JPY net non-comm short (k)"},
    {"catalyst_id": "struct_energy_transition_grid", "indicator_id": "ind_03",
     "provider": "yfinance", "ref": "HG=F", "transform": "lb_to_tonne",
     "note": "COMEX copper front month, USD/lb → USD/tonne"},
]


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(_REPO_ROOT / ".env")
    except Exception:  # noqa: BLE001
        pass


def fetch_fred(series_id: str, api_key: str) -> tuple[float, str] | None:
    url = (f"{_FRED_API}?series_id={series_id}&api_key={api_key}"
           f"&file_type=json&sort_order=desc&limit=10")
    try:
        d = json.load(urllib.request.urlopen(url, timeout=30))
    except Exception:  # noqa: BLE001
        return None
    for o in d.get("observations", []):
        if o.get("value") not in (".", None, ""):
            return float(o["value"]), str(o["date"])
    return None


def fetch_yfinance(ticker: str) -> tuple[float, str] | None:
    try:
        import yfinance as yf
        h = yf.Ticker(ticker).history(period="10d", auto_adjust=True)
        if h.empty:
            return None
        return float(h["Close"].iloc[-1]), str(h.index[-1].date())
    except Exception:  # noqa: BLE001
        return None


def fetch_cftc_net_short(code: str) -> tuple[float, str] | None:
    from catalyx.data.cot_data import _API, _TIMEOUT_S
    import httpx

    params = {"$select": "report_date_as_yyyy_mm_dd,noncomm_positions_long_all,"
                         "noncomm_positions_short_all",
              "$where": f"cftc_contract_market_code='{code}'",
              "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": "1"}
    try:
        rows = httpx.get(_API, params=params, timeout=_TIMEOUT_S).json()
        r = rows[0]
        net_short = (float(r["noncomm_positions_short_all"])
                     - float(r["noncomm_positions_long_all"])) / 1000.0
        return round(net_short, 1), str(r["report_date_as_yyyy_mm_dd"])[:10]
    except Exception:  # noqa: BLE001
        return None


def observe(entry: dict, fred_key: str | None) -> dict:
    """Fetch one entry. Returns {value, obs_date} or {error}."""
    p = entry["provider"]
    if p == "fred":
        if not fred_key:
            return {"error": "FRED_API_KEY not set"}
        got = fetch_fred(entry["ref"], fred_key)
    elif p == "yfinance":
        got = fetch_yfinance(entry["ref"])
    elif p == "cftc_net_short":
        got = fetch_cftc_net_short(entry["ref"])
    else:
        return {"error": f"unknown provider {p}"}
    if got is None:
        return {"error": f"{p}:{entry['ref']} fetch failed"}
    value, obs_date = got
    if entry.get("transform") == "lb_to_tonne":
        value = value * _LB_PER_TONNE
    return {"value": round(float(value), 4), "obs_date": obs_date}


def run(apply: bool = False) -> list[dict]:
    import yaml
    from catalyx.store.indicator_update import apply_one, find_file

    _load_env()
    fred_key = os.environ.get("FRED_API_KEY")
    results = []
    for entry in AUTO_INDICATORS:
        obs = observe(entry, fred_key)
        row = {**entry, **obs}
        if "value" in obs:
            try:
                doc = yaml.safe_load(find_file(entry["catalyst_id"]).read_text(encoding="utf-8"))
                ind = next(i for i in doc.get("indicators", [])
                           if i.get("id") == entry["indicator_id"])
                stored = ind.get("current_value")
                row["stored_value"] = stored
                row["stored_date"] = ind.get("last_date")
                if stored not in (None, 0):
                    row["delta_pct"] = round((obs["value"] - stored) / abs(stored) * 100, 1)
            except Exception:  # noqa: BLE001
                pass
            if apply:
                try:
                    applied = apply_one(entry["catalyst_id"], entry["indicator_id"],
                                        obs["value"], as_of=obs["obs_date"],
                                        note=f"auto: {entry['note']}",
                                        source=f"{entry['provider']}:{entry['ref']}")
                    row["applied"] = True
                    row["skipped_duplicate"] = bool(applied.get("skipped_duplicate"))
                except Exception as e:  # noqa: BLE001
                    row["applied"] = False
                    row["apply_error"] = str(e)
        results.append(row)
    return results


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="CATALYX auto-observable indicators (FRED/yfinance/CFTC)")
    p.add_argument("--apply", action="store_true",
                   help="Record the observations via indicator_update (default: dry-run)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    results = run(apply=args.apply)
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return
    mode = "APPLIED" if args.apply else "dry-run"
    print(f"CATALYX — auto indicators ({mode})\n")
    for r in results:
        tag = f"{r['catalyst_id'].removeprefix('struct_')}/{r['indicator_id']}"
        if r.get("error"):
            print(f"  {tag:<42} ERROR: {r['error']}")
            continue
        delta = f"  Δ{r['delta_pct']:+.1f}%" if r.get("delta_pct") is not None else ""
        stale = f" (stored {r.get('stored_value')} @ {r.get('stored_date')})"
        flag = "  ⚠ >10% — Rule 5" if abs(r.get("delta_pct") or 0) > 10 else ""
        ap = ""
        if args.apply:
            ap = "  → dup, skipped" if r.get("skipped_duplicate") else (
                "  → recorded" if r.get("applied") else f"  → APPLY FAILED: {r.get('apply_error')}")
        print(f"  {tag:<42} {r['value']:>12.4g}  ({r['obs_date']}){delta}{stale}{flag}{ap}")


if __name__ == "__main__":
    main()
