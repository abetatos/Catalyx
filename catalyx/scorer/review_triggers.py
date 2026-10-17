"""Market review triggers — shocks with no date, which the central-bank calendar cannot see.

WHY (docs/PLAN_v15_catalyst_capture.md, Phase 5): the calendar catches decisions that happen on a
known day. A Hormuz re-escalation or a bond rout has no date; only the tape shows it. These
triggers pull a review forward and put the move in front of the scan (Step C0b), nothing more —
not a score input and not a rule action.

Thresholds live in `scoring_weights.yaml review_triggers` with the share of days each fired over
10.8 years of history. Inputs are NATIVE series: `^TNX` is a yield in percent (5.1 = 5.1%), so
a change in basis points is `Δ × 100`; Brent is a continuous future, read over 20 sessions.

A series that is missing or shorter than 21 observations yields an "unavailable" move — a trigger
that cannot be evaluated must alert, never read as quiet.

CLI:  uv run python -m catalyx.scorer.review_triggers [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta

import pandas as pd

WINDOW = 20
MIN_OBS = WINDOW + 1
TICKERS = ("^TNX", "BZ=F", "SPY", "^STOXX50E")


def _series(frame: pd.DataFrame | None, ticker: str) -> pd.Series | None:
    if frame is None or ticker not in getattr(frame, "columns", []):
        return None
    s = frame[ticker].dropna()
    return s if len(s) >= MIN_OBS else None


def moves(frame: pd.DataFrame | None, cfg: dict) -> list[dict]:
    """Pure. One dict per fired trigger: {key, ticker, kind, value, message}. `key` is the triage
    key (`market:<ticker>:<last-date>`)."""
    out: list[dict] = []

    def add(ticker, kind, value, msg, when):
        out.append({"key": f"market:{ticker}:{when}", "ticker": ticker, "kind": kind,
                    "value": value, "message": msg})

    def unavailable(ticker):
        out.append({"key": f"market:{ticker}:unavailable", "ticker": ticker,
                    "kind": "unavailable", "value": None,
                    "message": f"{ticker} unavailable for review trigger"})

    s = _series(frame, "^TNX")
    if s is None:
        unavailable("^TNX")
    else:
        bp = (float(s.iloc[-1]) - float(s.iloc[-1 - WINDOW])) * 100
        if abs(bp) >= float(cfg["us10y_change_bp_20d"]):
            add("^TNX", "us10y_change", round(bp, 1),
                f"US 10y yield {bp:+.0f}bp over {WINDOW} sessions "
                f"(now {float(s.iloc[-1]):.2f}%) — threshold {cfg['us10y_change_bp_20d']}bp",
                s.index[-1].date())

    s = _series(frame, "BZ=F")
    if s is None:
        unavailable("BZ=F")
    else:
        pct = (float(s.iloc[-1]) / float(s.iloc[-1 - WINDOW]) - 1) * 100
        if abs(pct) >= float(cfg["brent_change_pct_20d"]):
            add("BZ=F", "brent_change", round(pct, 1),
                f"Brent {pct:+.1f}% over {WINDOW} sessions (now {float(s.iloc[-1]):.1f}) — "
                f"threshold {cfg['brent_change_pct_20d']}%", s.index[-1].date())

    for tk in ("SPY", "^STOXX50E"):
        s = _series(frame, tk)
        if s is None:
            unavailable(tk)
            continue
        dd = (float(s.iloc[-1]) / float(s.iloc[-MIN_OBS:].max()) - 1) * 100
        if dd <= -float(cfg["equity_drawdown_pct_from_20d_high"]):
            add(tk, "equity_drawdown", round(dd, 1),
                f"{tk} {dd:.1f}% below its {WINDOW}-session high — "
                f"threshold {cfg['equity_drawdown_pct_from_20d_high']}%", s.index[-1].date())
    return out


def evaluate(frame: pd.DataFrame | None, cfg: dict) -> list[str]:
    return [m["message"] for m in moves(frame, cfg)]


def read_frame(as_of: date | None = None) -> pd.DataFrame:
    """Native closes from the shared cache, offline. ~3 months is plenty for a 20-session window."""
    from catalyx.data import prices
    end = as_of or date.today()
    return prices.read(list(TICKERS), str(end - timedelta(days=100)), str(end), allow_fetch=False)


def current_moves(as_of: date | None = None) -> list[dict]:
    from catalyx.config import weights
    return moves(read_frame(as_of), weights.review_triggers())


def main() -> None:
    ap = argparse.ArgumentParser(description="CATALYX — market review triggers")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    ms = current_moves()
    if args.json:
        json.dump(ms, sys.stdout, indent=2, ensure_ascii=False)
        print()
    elif not ms:
        print("· no market trigger fired")
    else:
        for m in ms:
            print(f"  • {m['message']}   [{m['key']}]")


if __name__ == "__main__":
    main()
