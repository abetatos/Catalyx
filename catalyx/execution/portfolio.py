"""Model portfolio construction (Fase D).

A MODEL portfolio is a deterministic function of (score_run × risk_config): the same
monthly run feeds N portfolios (conservative / balanced / aggressive), and each holding
records the `config_version` (md5 of the profile YAML) so an evolution is always traceable
to the rules that produced it. This is the "what the system said" leg; the real-money leg
(executed trades) is logged separately and compared against these holdings to measure
execution alpha — see docs/PLAN_lake_dvc_serving.md (Fase D).

Construction (network-free — reads only the lake's `sector_snapshot`):
  1. filter: composite ≥ min_composite, momentum ≥ min_momentum, crowding ≤ max_crowding,
     narrative_maturity not excluded, primary_etf present
  2. dedupe by ETF (two sectors sharing one ETF → keep the higher composite)
  3. take the top `max_positions` by composite
  4. weight (composite-proportional or equal) then water-fill the `max_position_pct` cap;
     if every position hits the cap the remainder is implicit cash

Holdings are written to the lake table `portfolio_holding`, partitioned by
(portfolio_id, run_id) — append-only, one immutable file per (portfolio, run).

CLI:
    uv run python -m catalyx.execution.portfolio profiles
    uv run python -m catalyx.execution.portfolio build <portfolio_id> [--run-id ...]
    uv run python -m catalyx.execution.portfolio build-all
    uv run python -m catalyx.execution.portfolio show <portfolio_id>
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from catalyx.store import lake

_REPO_ROOT = Path(__file__).parents[2]
_PROFILES_DIR = _REPO_ROOT / "catalyx" / "config" / "portfolios"
_HOLDING_TABLE = "portfolio_holding"


# ── Profiles ─────────────────────────────────────────────────────────────────

def profile_path(portfolio_id: str) -> Path:
    return _PROFILES_DIR / f"{portfolio_id}.yaml"


def load_profile(portfolio_id: str) -> dict:
    p = profile_path(portfolio_id)
    if not p.exists():
        raise FileNotFoundError(f"no portfolio profile {portfolio_id!r} at {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def config_version(portfolio_id: str) -> str:
    """md5 of the profile YAML — changes whenever the construction rules change."""
    try:
        return hashlib.md5(profile_path(portfolio_id).read_bytes()).hexdigest()[:12]
    except FileNotFoundError:
        return "unknown"


def list_profiles() -> list[str]:
    return sorted(p.stem for p in _PROFILES_DIR.glob("*.yaml"))


# ── Weighting ────────────────────────────────────────────────────────────────

def water_fill(scores: list[float], max_w: float) -> list[float]:
    """Allocate weights ∝ `scores`, no weight exceeding `max_w` (a fraction in (0,1]).

    Excess from capped positions is redistributed proportionally among the uncapped.
    If n × max_w < 1 every position caps and the weights sum to < 1 (the rest is cash).
    Returns weights as fractions (same order as `scores`).
    """
    n = len(scores)
    weights = [0.0] * n
    if n == 0:
        return weights
    remaining = {i for i in range(n) if scores[i] > 0}
    if not remaining:  # all-zero scores → equal split under the cap
        w = min(1.0 / n, max_w)
        return [w] * n
    pool = 1.0
    while remaining:
        s = sum(scores[i] for i in remaining)
        newly_capped = [i for i in remaining if pool * scores[i] / s >= max_w]
        if not newly_capped:
            for i in remaining:
                weights[i] = pool * scores[i] / s
            break
        for i in newly_capped:
            weights[i] = max_w
            remaining.discard(i)
        pool -= max_w * len(newly_capped)
        if pool <= 1e-9:
            break
    return weights


# ── Build ────────────────────────────────────────────────────────────────────

def _entry_prices(lake_dir: Path | None = None) -> dict:
    """{sector_id: current_price of its primary ETF} from the latest momentum partition.
    Used as the model entry price so NAV/return can be measured against the market."""
    mdf = lake.read_table("momentum", lake_dir=lake_dir)
    if mdf.empty or "current_price" not in mdf.columns:
        return {}
    if "role" in mdf.columns:
        mdf = mdf[mdf["role"] == "primary"]
    if mdf.empty:
        return {}
    latest = mdf["date"].max()
    mdf = mdf[mdf["date"] == latest]
    out = {}
    for _, r in mdf.iterrows():
        v = r.get("current_price")
        if v is not None:
            out[r["sector_id"]] = float(v)
    return out


def _latest_run_id(df) -> str | None:
    """Latest run by run_id. The id format `run_YYYYMMDD_HHMMSS` sorts lexically =
    chronologically, and (unlike timestamps) is immune to tz-naive/aware mismatches
    across partitions seeded from different sources."""
    if df.empty or "run_id" not in df.columns:
        return None
    ids = [r for r in df["run_id"].dropna().unique()]
    return max(ids) if ids else None


def build_model_holdings(portfolio_id: str, run_id: str | None = None,
                         profile: dict | None = None, persist: bool = True,
                         lake_dir: Path | None = None, risk_overlay: bool = True) -> dict:
    """Build a model portfolio's holdings from a score_run's sector_snapshot. Deterministic.

    `risk_overlay`: enables the (OPT-IN) noise-vs-regime weight actions. By DEFAULT these are
    inert (haircut 0, no exclusion) — the regime_state is only carried onto holdings for the
    monthly review (flag-only). When configured in the profile YAML:
      - `exclude_breaking: true` → `breaking` sectors dropped from selection (a healthy sector
        takes the slot). Default off — `breaking` is surfaced as a *recommendation*, not auto-acted.
      - `contested_haircut: 0..1` → trims `contested` weights; `contested_action: redistribute`
        (to healthy names) or `cash` (gross-down). exp_2026-06-05 A/B: redistribute barely helps a
        broad risk-off, cash helps more but costs edge — so both are opt-in, not default.
    See docs/DESIGN_catalyst_regime_discrimination.md. `--no-overlay` forces everything off.
    """
    profile = profile or load_profile(portfolio_id)
    c = profile["construction"]
    overlay = profile.get("risk_overlay", {}) or {}
    # DEFAULT = flag-only: the regime signal is carried onto holdings for the monthly review,
    # but it does NOT move weights. exp_2026-06-05 A/B showed acting on `contested` (a one-off,
    # possibly-reverting event) barely helps drawdown and costs edge — and it contradicts the
    # project's monthly/conviction objective. A sector only warrants action when it goes
    # `breaking` (persistent + fundamentally corroborated), and even then as a *recommendation*
    # to the human, not an auto-trade. The haircut/exclude machinery below is OPT-IN: set
    # `risk_overlay.contested_haircut` / `exclude_breaking` in the profile YAML to enable it.
    contested_haircut = float(overlay.get("contested_haircut", 0.0))
    exclude_breaking = bool(overlay.get("exclude_breaking", False))
    # how the freed `contested` weight is handled:
    #   redistribute → goes to the healthy names (gross unchanged) — cheap but, per exp_2026-06-05,
    #                  near-useless in a broad risk-off (reshuffles a correlated cluster)
    #   cash         → becomes cash (gross-down) — the variant that actually cuts the drawdown
    contested_action = str(overlay.get("contested_action", "redistribute"))

    df = lake.read_table("sector_snapshot", lake_dir=lake_dir)
    if df.empty:
        return {"portfolio_id": portfolio_id, "error": "no sector_snapshot in lake"}
    if run_id is None:
        run_id = _latest_run_id(df)
    df = df[df["run_id"] == run_id].copy()
    if df.empty:
        return {"portfolio_id": portfolio_id, "error": f"run_id {run_id} not in lake"}
    # regime_state column is additive (older runs lack it) — default to intact
    if "regime_state" not in df.columns:
        df["regime_state"] = "intact"
    df["regime_state"] = df["regime_state"].fillna("intact")

    # 1. filters
    df = df[df["composite"] >= c["min_composite"]]
    df = df[df["momentum"] >= c.get("min_momentum", 0)]
    df = df[df["crowding_risk"] <= c.get("max_crowding", 100)]
    excl = set(c.get("exclude_narrative_maturity") or [])
    if excl:
        df = df[~df["narrative_maturity"].isin(excl)]
    df = df[df["primary_etf"].notna()]
    # regime overlay: drop `breaking` sectors (permanent rotation) before selection
    if risk_overlay and exclude_breaking:
        df = df[df["regime_state"] != "breaking"]

    # 2. select + 3. dedupe by ETF + top N — ranked by the STRATEGY's signal.
    weighting = c["weighting"]                       # momentum | composite | equal
    rank_col = "momentum" if weighting == "momentum" else "composite"
    df = df.sort_values(rank_col, ascending=False)
    df = df.drop_duplicates("primary_etf", keep="first")
    df = df.head(int(c["max_positions"]))

    if df.empty:
        return {"portfolio_id": portfolio_id, "run_id": run_id, "holdings": [],
                "error": "no sectors passed the construction filters"}

    # 4. weights per strategy
    if weighting == "equal":
        scores = [1.0] * len(df)
    elif weighting == "momentum":
        scores = [float(x) for x in df["momentum"]]
    else:  # composite (conviction, low_crowding)
        scores = [float(x) for x in df["composite"]]
    # regime overlay: haircut the WEIGHTING score of `contested` sectors (not their ranking, so
    # they stay selected — only de-risked). water_fill then redistributes the freed weight to the
    # healthy names (or to cash at the cap). Reversible: unwinds as the contradict decays.
    states = list(df["regime_state"])
    veto_on = risk_overlay and contested_haircut > 0
    if veto_on and contested_action == "redistribute":
        scores = [s * (1.0 - contested_haircut) if st == "contested" else s
                  for s, st in zip(scores, states)]
    weights = water_fill(scores, float(c["max_position_pct"]) / 100.0)
    if veto_on and contested_action == "cash":
        # gross-down: trim contested FINAL weights; the freed weight stays as cash (not
        # redistributed). exp_2026-06-05 A/B: this is what actually cuts a broad-risk-off
        # drawdown — redistribution merely reshuffles within a correlated momentum cluster.
        weights = [w * (1.0 - contested_haircut) if st == "contested" else w
                   for w, st in zip(weights, states)]

    entry_prices = _entry_prices(lake_dir)
    cfg_ver = config_version(portfolio_id)
    strategy = profile.get("strategy", weighting)
    built_at = datetime.now(timezone.utc)
    rows = []
    for rank, ((_, r), w) in enumerate(zip(df.iterrows(), weights), 1):
        rows.append({
            "portfolio_id": portfolio_id,
            "run_id": run_id,
            "config_version": cfg_ver,
            "strategy": strategy,
            "rank_in_portfolio": rank,
            "sector_id": r["sector_id"],
            "primary_etf": r["primary_etf"],
            "composite": float(r["composite"]),
            "momentum": float(r["momentum"]),
            "crowding_risk": float(r["crowding_risk"]),
            "narrative_maturity": r.get("narrative_maturity"),
            "regime_state": r.get("regime_state", "intact"),
            "weight_pct": round(w * 100.0, 2),
            "entry_price": entry_prices.get(r["sector_id"]),
            "built_at": built_at,
        })

    cash_pct = round(100.0 - sum(x["weight_pct"] for x in rows), 2)
    n_contested = sum(1 for x in rows if x.get("regime_state") == "contested")

    if persist:
        import pandas as pd
        lake.append_partition(_HOLDING_TABLE, pd.DataFrame(rows),
                              {"portfolio_id": portfolio_id, "run_id": run_id},
                              overwrite=True, lake_dir=lake_dir)

    return {"portfolio_id": portfolio_id, "run_id": run_id, "config_version": cfg_ver,
            "positions": len(rows), "cash_pct": cash_pct, "overlay": risk_overlay,
            "contested": n_contested, "holdings": rows}


def show_holdings(portfolio_id: str, run_id: str | None = None, lake_dir: Path | None = None) -> dict:
    df = lake.read_table(_HOLDING_TABLE, lake_dir=lake_dir)
    if df.empty or "portfolio_id" not in df.columns:
        return {"portfolio_id": portfolio_id, "holdings": []}
    df = df[df["portfolio_id"] == portfolio_id]
    if df.empty:
        return {"portfolio_id": portfolio_id, "holdings": []}
    if run_id is None:
        run_id = _latest_run_id(df)
    df = df[df["run_id"] == run_id].sort_values("rank_in_portfolio")
    return {"portfolio_id": portfolio_id, "run_id": run_id,
            "holdings": df.to_dict(orient="records")}


# ── CLI ──────────────────────────────────────────────────────────────────────

def _print_holdings(res: dict) -> None:
    if res.get("error"):
        print(f"  {res['portfolio_id']}: {res['error']}")
        return
    overlay_str = ""
    if res.get("overlay") is not None:
        overlay_str = f"  overlay={'on' if res['overlay'] else 'OFF'}  contested={res.get('contested', 0)}"
    print(f"  {res['portfolio_id']}  run={res.get('run_id')}  "
          f"cfg={res.get('config_version','?')}  positions={res.get('positions', len(res['holdings']))}"
          + (f"  cash={res['cash_pct']}%" if res.get('cash_pct') is not None else "")
          + overlay_str)
    print(f"    {'#':<3}{'sector_id':<34}{'etf':<10}{'wt%':>7}{'comp':>7}{'mom':>7}  {'maturity':<11}regime")
    for h in res["holdings"]:
        print(f"    {h['rank_in_portfolio']:<3}{h['sector_id']:<34}{str(h['primary_etf']):<10}"
              f"{h['weight_pct']:>7}{h['composite']:>7.1f}{h['momentum']:>7.1f}  {str(h.get('narrative_maturity')):<11}{h.get('regime_state','intact')}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="CATALYX model portfolios (Fase D)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("profiles", help="List portfolio profiles")
    b = sub.add_parser("build", help="Build a model portfolio from a score_run")
    b.add_argument("portfolio_id")
    b.add_argument("--run-id", default=None)
    b.add_argument("--no-persist", action="store_true")
    b.add_argument("--no-overlay", action="store_true", help="Disable the regime risk-overlay (veto off)")
    sub.add_parser("build-all", help="Build every profile from the latest run")
    s = sub.add_parser("show", help="Show a portfolio's latest holdings")
    s.add_argument("portfolio_id")
    args = p.parse_args()

    if args.cmd == "profiles":
        for pid in list_profiles():
            prof = load_profile(pid)
            c = prof["construction"]
            print(f"  {pid:<14} {prof['name']:<14} maxpos={c['max_positions']:<3} "
                  f"min_comp={c['min_composite']:<4} cap={c['max_position_pct']}%  cfg={config_version(pid)}")
    elif args.cmd == "build":
        _print_holdings(build_model_holdings(args.portfolio_id, run_id=args.run_id,
                                             persist=not args.no_persist,
                                             risk_overlay=not args.no_overlay))
    elif args.cmd == "build-all":
        for pid in list_profiles():
            _print_holdings(build_model_holdings(pid))
            print()
    elif args.cmd == "show":
        _print_holdings(show_holdings(args.portfolio_id))


if __name__ == "__main__":
    main()
