"""Run state digest — the compact snapshot a review reads BEFORE spending a single search.

WHY (v3 Phase 1, docs/PLAN_v3_lean_pipeline_rebalance.md §2.7):
    A `/catalyx-review` used to open with ~40 WebSearches, because it had no cheap way to know
    what had actually changed. It re-verified every registered catalyst, re-studied sectors whose
    drivers had not moved, and only discovered the book's real problems (a −21% position, a stale
    verdict) near the END, after the expensive phases had already run.

    This module inverts that: every DETERMINISTIC fact — book P&L, drawdowns, exit signals, stale
    indicators, stale fundamental verdicts, pending lifecycle transitions, the current ranking —
    is computed offline first and collapsed into one ≤3k-token digest. The review reads it and
    then searches only what the digest says is decision-relevant. Facts before questions.

    The `work_list` it emits is the direct input to the scan and to the study refresh: the set of
    catalysts whose verdict is stale or whose position is hurting, and the sectors that are held
    or top-ranked. Everything else is knowingly, explicitly skipped — and the digest says so, so
    "we didn't look" never masquerades as "nothing changed".

OFFLINE — with `CATALYX_PRICES_OFFLINE=1` (or a warm price cache) this runs with no network at
all, which is what makes it safe to schedule as a cheap heartbeat between reviews.

CLI:
    uv run python -m catalyx.store.run_state              # human digest
    uv run python -m catalyx.store.run_state --json       # machine-readable
    uv run python -m catalyx.store.run_state --write      # → data/reports/state_<date>.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[2]
_REPORTS_DIR = _REPO_ROOT / "data" / "reports"

# How many top-ranked sectors count as "decision-relevant" for the work list — a candidate the
# review could plausibly recommend this cycle; beyond that, a momentum baseline is enough.
# ADAPTIVE, because a fixed 15 meant two different things before and after the 2026-08-27
# universe cut: 15/53 sectors (top quartile — selective) became 15/26 (top 58% — barely a
# filter). Roughly the top third of the investable universe, floored so a tiny universe still
# offers real choice and capped so a large one stays affordable.
TOP_N_MIN, TOP_N_MAX, TOP_N_FRACTION = 5, 15, 3.0
TOP_N_RELEVANT = None          # None → derive from the taxonomy (see relevant_top_n)


def investable_sectors() -> set[str]:
    """Sectors that can actually hold capital today, per the taxonomy."""
    import yaml

    path = _REPO_ROOT / "catalyx" / "config" / "sector_taxonomy.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return set()
    return {s["id"] for s in data.get("sectors", [])
            if s.get("investable") and not s.get("watch_only")}


def relevant_top_n(n_investable: int) -> int:
    """How many top-ranked sectors are 'decision-relevant', scaled to the universe size."""
    if not n_investable:
        return TOP_N_MIN
    return max(TOP_N_MIN, min(TOP_N_MAX, int(n_investable / TOP_N_FRACTION)))


def _drivers_for(sector_ids: list[str]) -> set[str]:
    """Catalysts driving `sector_ids`, from each sector study's `active_catalyst_ids`.

    Same source `catalyst_scorer` uses to score alignment, so "drives a decision-relevant
    sector" means exactly what the ranking meant by it.
    """
    studies = _REPO_ROOT / "data" / "sector_studies"
    out: set[str] = set()
    for sid in sector_ids:
        fp = studies / f"study_{sid}.json"
        if not fp.exists():
            continue
        try:
            doc = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        out.update(doc.get("active_catalyst_ids") or [])
    return out


def tier_work_list(driving: list[str], stale_by_id: dict[str, dict],
                   sector_drivers: set[str]) -> tuple[list[dict], list[dict], list[str]]:
    """Split the re-verify candidates into MUST / SHOULD / OPTIONAL.

    A flat "re-verify everything stale" list is ~27 catalysts — as expensive as the full sweep
    this replaces, which is how the old pipeline ended up searching everything every cycle. The
    tiers encode WHY each candidate earns a search, so a review can spend down the list until
    its budget runs out and still have covered everything real money depends on:

      MUST     drives an open position — capital is exposed to this verdict being wrong. Included
               even when the stamp looks fresh: freshness is a floor, not a substitute for
               checking what you hold.
      SHOULD   stale AND drives a sector this cycle could act on (held or top-ranked).
      OPTIONAL stale, but nothing this cycle hangs on it → one "no change" line, no search.
    """
    must = [{"catalyst_id": c, "why": "drives an open position"} for c in driving]
    should = [{"catalyst_id": c, "why": f"stale {stale_by_id[c]['freshness']} · drives a "
                                        f"decision-relevant sector"}
              for c in sorted(sector_drivers & set(stale_by_id)) if c not in driving]
    optional = sorted(set(stale_by_id) - set(driving) - {s["catalyst_id"] for s in should})
    return must, should, optional


def _safe(fn, default):
    """Never let one unavailable input (no lake yet, no network) kill the whole digest."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)} if isinstance(default, dict) else default


def build(top_n: int | None = None, as_of: date | None = None) -> dict:
    """Compose the digest. Read-only: computes, persists nothing, decides nothing."""
    from catalyx.scorer import catalyst_lifecycle, exit_watcher, freshness
    from catalyx.store import (catalyst_review, lake_query, movement_repo,
                               structural_catalyst_repo)

    today = as_of or date.today()

    book = _safe(movement_repo.positions, {"holdings": []})
    holdings = book.get("holdings", []) or []
    held_sectors = sorted({h["sector_id"] for h in holdings if h.get("sector_id")})

    exits = _safe(lambda: exit_watcher.assess(today=today, persist=False), {"positions": []})
    exit_rows = []
    for p in (exits.get("positions") or []):
        dd = p.get("drawdown") or {}
        tax = p.get("tax") or {}
        fresh = p.get("catalyst_freshness") or {}
        exit_rows.append({
            "sector_id": p.get("sector_id"),
            "etf": p.get("etf"),
            "action": p.get("suggested_action"),
            "unrealized_pct": tax.get("unrealized_pct"),
            "unrealized_eur": tax.get("unrealized_eur"),
            "drawdown_tier": dd.get("tier"),
            "reverify_required": dd.get("reverify_required"),
            "catalyst_freshness": fresh.get("status"),
            "regime_state": p.get("regime_state"),
            "fired_stops": [s["id"] for s in (p.get("stops_checked") or [])
                            if s.get("status") == "fired"],
            "assumptions_violated": (p.get("assumptions") or {}).get("violated", 0),
        })

    verdicts = _safe(lambda: catalyst_review.review_status(as_of=today), [])
    stale_verdicts = [v for v in verdicts if v["freshness"] in ("stale", "very_stale", "unknown")]

    stale_indicators = _safe(lambda: freshness.overdue(as_of=today), [])

    lifecycle = _safe(lambda: catalyst_lifecycle.evaluate(as_of=today),
                      {"transitions": [], "promotion_candidates": []})

    # The last recorded run may predate a taxonomy change, so its ranking can name sectors that
    # are no longer investable (after 2026-08-27, 7 of the stored top-15 were). Filter to what
    # can hold capital TODAY before anything spends a search on it — read a deeper slice first
    # so the filtering does not silently shrink the list below top_n.
    investable = _safe(investable_sectors, set())
    top_n = top_n or relevant_top_n(len(investable))
    ranking_all = _safe(lambda: lake_query.latest_ranking(top_n=max(top_n * 3, 30)), [])
    ranking = [r for r in ranking_all
               if r.get("sector_id") and (not investable or r["sector_id"] in investable)][:top_n]
    top_sectors = [r["sector_id"] for r in ranking]
    dropped = [r.get("sector_id") for r in ranking_all[:top_n]
               if investable and r.get("sector_id") not in investable]

    # ── The work list: what the expensive phases are allowed to spend tokens on ──
    # TIERED, not a flat union. A flat "everything stale" list is ~27 catalysts — as expensive
    # as the full sweep this replaces. The tiers encode WHY each one earns a search, so the
    # review can spend down the list until its budget runs out and still have covered what
    # actually drives money at risk.
    driving = sorted({c for h in holdings for c in (h.get("catalyst_ids") or [])})
    if not driving:                       # positions() may not carry attribution — read the ledger
        ledger = _safe(lambda: lake_query.catalyst_ledger(), [])
        driving = sorted({r["catalyst_id"] for r in ledger
                          if r.get("catalyst_id") and r["catalyst_id"] != "uncatalyzed"})
    # Movements keep the id they were opened against, and after the 2026-08-27 merges some of
    # those are `status: merged` — `compute_all()` skips them, so putting one on the MUST list
    # spends a search on a catalyst nothing scores. Ask the survivor instead.
    driving = _safe(lambda: structural_catalyst_repo.resolve_all(driving), driving)

    hurting = [r["sector_id"] for r in exit_rows
               if r["action"] in ("reduce", "exit") or r.get("reverify_required")]

    stale_by_id = {v["catalyst_id"]: v for v in stale_verdicts if v["catalyst_id"]}
    relevant_sectors = sorted(set(held_sectors) | set(top_sectors))
    top_drivers = _safe(lambda: _drivers_for(relevant_sectors), set())

    must, should, optional = tier_work_list(driving, stale_by_id, top_drivers)

    total_invested = float(book.get("total_invested_eur") or 0.0)
    marked = sum(float(r["unrealized_eur"] or 0) for r in exit_rows
                 if r.get("unrealized_eur") is not None)

    return {
        "as_of": today.isoformat(),
        "book": {
            "n_positions": len(holdings),
            "invested_eur": round(total_invested, 2),
            "unrealized_eur": round(marked, 2),
            "unrealized_pct": round(marked / total_invested * 100, 2) if total_invested else None,
            "realized_ytd_eur": book.get("realized_eur"),
            "held_sectors": held_sectors,
        },
        "positions": exit_rows,
        "attention": {
            "positions_needing_action": hurting,
            "stale_verdicts": len(stale_verdicts),
            "stale_indicators": len(stale_indicators),
            "pending_lifecycle": len(lifecycle.get("transitions") or []),
        },
        "stale_verdicts": [{"catalyst_id": v["catalyst_id"], "age_days": v["age_days"],
                            "freshness": v["freshness"]} for v in stale_verdicts],
        "stale_indicators": [{"catalyst_id": i.get("catalyst_id"), "indicator": i.get("indicator_id"),
                              "last_date": i.get("last_date"), "overdue_by": i.get("overdue_by_days"),
                              "cadence": i.get("check_frequency")} for i in stale_indicators],
        "lifecycle_transitions": [{"catalyst_id": t["catalyst_id"], "from": t["from"],
                                   "to": t["to"], "reason": t["reason"]}
                                  for t in (lifecycle.get("transitions") or [])],
        "ranking_top": [{"sector_id": r.get("sector_id"), "rank": r.get("rank"),
                         "composite": r.get("composite"), "momentum": r.get("momentum"),
                         "narrative_maturity": r.get("narrative_maturity")}
                        for r in ranking],
        "ranking_note": (f"{len(dropped)} sector(s) in the stored top-{top_n} are no longer "
                         f"investable and were dropped: {', '.join(dropped)}" if dropped else None),
        "work_list": {
            "top_n": top_n,
            "must_reverify": must,
            "should_reverify": should,
            "optional_reverify": optional,
            "sectors_decision_relevant": relevant_sectors,
            "note": ("Spend searches on must_reverify, then should_reverify. optional_reverify "
                     "collapses to a single 'no change' line with NO search, and every sector "
                     "outside sectors_decision_relevant ranks on its momentum baseline. Skipping "
                     "is explicit here so 'we did not look' never reads as 'nothing changed'."),
        },
    }


def write(digest: dict, reports_dir: Path | None = None) -> Path:
    d = reports_dir or _REPORTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    fp = d / f"state_{digest['as_of'].replace('-', '')}.json"
    fp.write_text(json.dumps(digest, indent=2, ensure_ascii=False, default=str) + "\n",
                  encoding="utf-8")
    return fp


def render(digest: dict) -> str:
    """The human digest — what the review prints, and all it needs to plan the cycle."""
    b = digest["book"]
    att = digest["attention"]
    out = [f"CATALYX — run state  {digest['as_of']}", ""]

    pct = f"{b['unrealized_pct']:+.2f}%" if b.get("unrealized_pct") is not None else "n/a"
    out.append(f"  BOOK   {b['n_positions']} positions · €{b['invested_eur']:.0f} invested · "
               f"unrealized €{b['unrealized_eur']:+.0f} ({pct}) · realized YTD "
               f"€{float(b.get('realized_ytd_eur') or 0):.0f}")
    out.append("")
    out.append(f"  {'sector':<32}{'etf':<10}{'P&L':>9}  {'action':<8}{'dd':<8}{'verdict':<12}regime")
    for p in digest["positions"]:
        upct = f"{p['unrealized_pct']:+.1f}%" if p.get("unrealized_pct") is not None else "    n/a"
        flag = " ⚠" if p.get("reverify_required") else ""
        out.append(f"  {str(p['sector_id']):<32}{str(p['etf']):<10}{upct:>9}  "
                   f"{str(p['action']):<8}{str(p.get('drawdown_tier') or '—'):<8}"
                   f"{str(p.get('catalyst_freshness')):<12}{p.get('regime_state')}{flag}")

    out.append("")
    out.append(f"  ATTENTION  {len(att['positions_needing_action'])} position(s) need an action · "
               f"{att['stale_verdicts']} stale verdict(s) · {att['stale_indicators']} stale "
               f"indicator(s) · {att['pending_lifecycle']} lifecycle transition(s)")
    if att["positions_needing_action"]:
        out.append(f"    → act on: {', '.join(att['positions_needing_action'])}")
    for t in digest["lifecycle_transitions"]:
        out.append(f"    → lifecycle: {t['catalyst_id']} {t['from']} → {t['to']} ({t['reason']})")

    wl = digest["work_list"]
    out.append("")
    out.append(f"  WORK LIST  must={len(wl['must_reverify'])} · should={len(wl['should_reverify'])} "
               f"· optional={len(wl['optional_reverify'])} (no search) · "
               f"{len(wl['sectors_decision_relevant'])} decision-relevant sector(s)")
    for row in wl["must_reverify"]:
        out.append(f"    MUST   {row['catalyst_id']:<48}{row['why']}")
    for row in wl["should_reverify"][:8]:
        out.append(f"    should {row['catalyst_id']:<48}{row['why']}")
    if len(wl["should_reverify"]) > 8:
        out.append(f"    should … +{len(wl['should_reverify']) - 8} more")
    out.append(f"    sectors: {', '.join(wl['sectors_decision_relevant'][:10])}"
               + ("…" if len(wl["sectors_decision_relevant"]) > 10 else ""))
    return "\n".join(out)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(
        description="CATALYX run-state digest — deterministic facts before the review searches")
    p.add_argument("--json", action="store_true")
    p.add_argument("--write", action="store_true", help="Also write data/reports/state_<date>.json")
    p.add_argument("--top", type=int, default=TOP_N_RELEVANT)
    args = p.parse_args()

    digest = build(top_n=args.top)
    if args.write:
        fp = write(digest)
    if args.json:
        print(json.dumps(digest, indent=2, ensure_ascii=False, default=str))
    else:
        print(render(digest))
        if args.write:
            print(f"\n  → {fp.relative_to(_REPO_ROOT)}")


if __name__ == "__main__":
    main()
