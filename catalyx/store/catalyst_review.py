"""Catalyst review stamp — the write path that makes the freshness gate satisfiable.

THE BUG THIS FIXES (v3 Phase 1, docs/PLAN_v3_lean_pipeline_rebalance.md §2.3):
    `exit_watcher.catalyst_freshness` keys the whole sell-side doctrine on
    `status_last_reviewed` — "a stale fundamental verdict is never trusted blind" (2026-08-04).
    But the ONLY code that ever wrote that field was `/catalyx-update`, and only as a side
    effect of changing a numeric INDICATOR. The place where a catalyst actually gets its verdict
    re-checked — the scan's Pass-2 refresh — is recommend-only and writes nothing.

    Net effect: a review could re-verify every driving catalyst against live sources and the
    book would STILL read `very_stale` the next day (observed: all five positions at 83–87 days
    the morning after a full review). The discipline could not be satisfied by the pipeline as
    wired — not because the rule was wrong, but because it had no write path.

    This module is that write path: a cheap, explicit "I looked at this catalyst today and here
    is the verdict", separate from "I changed one of its numbers".

WHAT A STAMP RECORDS — `status_last_reviewed` (the field the gate reads) plus an append-only
`review_log[]` entry: {date, verdict, evidence, source}. The log is what turns freshness from a
timestamp into an audit trail: WHY was it judged intact 3 reviews ago, and did that hold?

VERDICTS — the scan's delta vocabulary, recorded verbatim: `strengthening` (thesis holds and the
evidence got stronger), `intact` (holds), `weakening` (evidence eroding, not broken), `breaking`
(mechanism failing → a lifecycle candidate), `invalidated` (mechanism gone). The GATE reads them
as three states — strengthening folds into intact, invalidated into breaking — but the log records
the word the scan actually used, because "this got stronger today" is a finding and flattening it
to `intact` deletes it.

This enum was three values while `catalyx-scan.md` documented five, and the docstring claimed the
two mapped "with no translation". They did not: on 2026-08-31 five catalysts came back
`strengthening` — three of them driving open positions, all with hard evidence — and every one of
those stamps was REJECTED, so the freshness gate went on reporting them stale the day they were
re-verified. A stamp that bounces is worse than one never attempted: the scan reports success and
the gate silently disagrees.

DOCTRINE — a stamp is a RECORD, never an action. It does not change `status`, intensity, or any
score; lifecycle transitions stay in `catalyst_lifecycle`, indicator values in `/catalyx-update`.
Stamping a catalyst you did not actually re-verify is the one way to corrupt the gate, so the
`--evidence` note is required for anything other than `intact`.

CLI:
    uv run python -m catalyx.store.catalyst_review stamp <catalyst_id> --verdict intact \\
        --evidence "WGC Q2: CB buying 850t, on track" [--source url-or-note]
    uv run python -m catalyx.store.catalyst_review batch data/reports/scan_deltas_<date>.json
    uv run python -m catalyx.store.catalyst_review status [--json] [--days 45]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[2]
_STRUCTURAL_DIR = _REPO_ROOT / "catalyx" / "config" / "structural_catalysts"
_EVENT_DIR = _REPO_ROOT / "data" / "catalysts"

VERDICTS = ("strengthening", "intact", "weakening", "breaking", "invalidated")

# The three-state view the freshness/regime consumers read. Kept explicit so widening the
# recorded vocabulary can never silently widen what the gate acts on.
VERDICT_REGIME = {"strengthening": "intact", "intact": "intact", "weakening": "weakening",
                  "breaking": "breaking", "invalidated": "breaking"}
# Verdicts that assert the thesis still holds — the ones that do not need evidence to record.
_HOLDS = ("intact", "strengthening")

# Kept in sync with `scoring_weights.yaml exit_signals` — the gate the stamp feeds.
DEFAULT_WARN_DAYS = 30
DEFAULT_MAX_DAYS = 45


# ── Locating a catalyst's backing file ───────────────────────────────────────

def find_file(catalyst_id: str, structural_dir: Path | None = None,
              event_dir: Path | None = None) -> Path | None:
    """The YAML (structural) or JSON (event) file backing `catalyst_id`, or None.

    Structural files are named after the id WITHOUT the `struct_` prefix
    (`struct_cb_gold_accumulation` → `cb_gold_accumulation.yaml`), which is why this cannot be
    a plain filename join — the same lookup `catalyst_scorer._load_structural` does.
    """
    sdir = structural_dir or _STRUCTURAL_DIR
    edir = event_dir or _EVENT_DIR
    for cand in (sdir / f"{catalyst_id.removeprefix('struct_')}.yaml", sdir / f"{catalyst_id}.yaml"):
        if cand.exists():
            return cand
    direct = edir / f"{catalyst_id}.json"
    if direct.exists():
        return direct
    if edir.exists():
        for f in sorted(edir.glob("*.json")):
            try:
                if json.loads(f.read_text(encoding="utf-8")).get("id") == catalyst_id:
                    return f
            except Exception:  # noqa: BLE001 — a malformed neighbour must not break the lookup
                continue
    return None


# ── Pure helper (unit-tested) ────────────────────────────────────────────────

def build_entry(verdict: str, evidence: str | None, source: str | None,
                as_of: str | None = None) -> dict:
    """One `review_log[]` entry. Raises on an unknown verdict or a missing required evidence.

    Evidence is mandatory for `weakening`/`breaking`/`invalidated`: those verdicts feed lifecycle
    decisions and an unsourced "it feels weaker" is exactly the LLM drift the scoring rules exist
    to stop. `strengthening` is exempt with `intact` — it moves nothing on its own, and demanding
    a citation to record "still holds, more so" is what pushed the scan to omit the row instead.
    """
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}, got {verdict!r}")
    if verdict not in _HOLDS and not (evidence or "").strip():
        raise ValueError(f"verdict {verdict!r} requires --evidence (what changed, with a source)")
    entry = {"date": as_of or date.today().isoformat(), "verdict": verdict}
    if evidence:
        entry["evidence"] = evidence.strip()
    if source:
        entry["source"] = source.strip()
    return entry


def days_since(stamp: str | None, as_of: date | None = None) -> int | None:
    if not stamp:
        return None
    try:
        return ((as_of or date.today()) - datetime.fromisoformat(str(stamp)[:10]).date()).days
    except (ValueError, TypeError):
        return None


def freshness_status(age_days: int | None, warn_days: int = DEFAULT_WARN_DAYS,
                     max_days: int = DEFAULT_MAX_DAYS) -> str:
    """`fresh` / `stale` / `very_stale` / `unknown` — the same tiers `exit_watcher` reports."""
    if age_days is None:
        return "unknown"
    if age_days > max_days:
        return "very_stale"
    if age_days > warn_days:
        return "stale"
    return "fresh"


# ── Write ────────────────────────────────────────────────────────────────────

def _stamp_yaml(path: Path, entry: dict, max_log: int) -> None:
    """ruamel round-trip so the structural YAMLs keep their comments and block scalars —
    those files are hand-authored documentation as much as data."""
    from ruamel.yaml import YAML as RuamelYAML
    from ruamel.yaml.comments import CommentedMap

    ry = RuamelYAML()
    ry.preserve_quotes = True
    ry.width = 120
    with path.open("r", encoding="utf-8") as fh:
        doc = ry.load(fh)

    doc["status_last_reviewed"] = entry["date"]
    log = doc.get("review_log")
    if log is None:
        log = []
        doc["review_log"] = log
    log.insert(0, CommentedMap(entry))
    del log[max_log:]

    with path.open("w", encoding="utf-8") as fh:
        ry.dump(doc, fh)


def _stamp_json(path: Path, entry: dict, max_log: int) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["status_last_reviewed"] = entry["date"]
    log = list(doc.get("review_log") or [])
    log.insert(0, entry)
    doc["review_log"] = log[:max_log]
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def stamp(catalyst_id: str, verdict: str = "intact", evidence: str | None = None,
          source: str | None = None, as_of: str | None = None, max_log: int = 12,
          structural_dir: Path | None = None, event_dir: Path | None = None) -> dict:
    """Record a re-verification of `catalyst_id`. Returns the written entry + the file path."""
    path = find_file(catalyst_id, structural_dir, event_dir)
    if path is None:
        raise FileNotFoundError(f"no catalyst file for {catalyst_id!r}")
    entry = build_entry(verdict, evidence, source, as_of)
    if path.suffix == ".yaml":
        _stamp_yaml(path, entry, max_log)
    else:
        _stamp_json(path, entry, max_log)
    return {"catalyst_id": catalyst_id, "path": str(path), **entry}


def stamp_batch(deltas: list[dict], as_of: str | None = None, **kw) -> dict:
    """Stamp every catalyst the scan actually re-verified.

    Input is the scan's machine-readable delta list — `[{catalyst_id, verdict, evidence,
    source}]` (`data/reports/scan_deltas_<date>.json`). This is the hop that closes the loop:
    the scan already PRODUCES this judgement per catalyst; until now it only printed it.
    """
    done, failed = [], []
    for d in deltas:
        cid = d.get("catalyst_id") or d.get("id")
        try:
            done.append(stamp(cid, d.get("verdict", "intact"), d.get("evidence"),
                              d.get("source"), as_of=as_of, **kw))
        except (FileNotFoundError, ValueError) as e:
            failed.append({"catalyst_id": cid, "error": str(e)})
    return {"stamped": done, "failed": failed}


# ── Read / audit ─────────────────────────────────────────────────────────────

def review_status(warn_days: int = DEFAULT_WARN_DAYS, max_days: int = DEFAULT_MAX_DAYS,
                  as_of: date | None = None, structural_dir: Path | None = None,
                  event_dir: Path | None = None) -> list[dict]:
    """Freshness of every catalyst's fundamental verdict — the audit `pre_run.sh` emits."""
    import yaml

    rows: list[dict] = []
    sdir = structural_dir or _STRUCTURAL_DIR
    edir = event_dir or _EVENT_DIR

    for f in sorted(sdir.glob("*.yaml")) if sdir.exists() else []:
        try:
            doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        if doc.get("status") in ("deactivated", "merged"):
            continue
        rows.append(_status_row(doc, "structural", warn_days, max_days, as_of))

    for f in sorted(edir.glob("*.json")) if edir.exists() else []:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if doc.get("status") in ("archived", "invalidated"):
            continue
        rows.append(_status_row(doc, "event", warn_days, max_days, as_of))

    rows.sort(key=lambda r: (r["age_days"] is None, -(r["age_days"] or 0)))
    return rows


def _status_row(doc: dict, kind: str, warn_days: int, max_days: int,
                as_of: date | None) -> dict:
    stamped = doc.get("status_last_reviewed")
    age = days_since(stamped, as_of)
    log = doc.get("review_log") or []
    return {
        "catalyst_id": doc.get("id"),
        "kind": kind,
        "status": doc.get("status", "active"),
        "last_reviewed": stamped,
        "age_days": age,
        "freshness": freshness_status(age, warn_days, max_days),
        "last_verdict": (log[0] or {}).get("verdict") if log else None,
        "n_reviews": len(log),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(
        description="Record a catalyst re-verification (feeds the exit_watcher freshness gate)")
    sub = p.add_subparsers(dest="cmd", required=True)

    st = sub.add_parser("stamp", help="Stamp ONE catalyst as re-verified today")
    st.add_argument("catalyst_id")
    st.add_argument("--verdict", default="intact", choices=list(VERDICTS))
    st.add_argument("--evidence", default=None, help="What you checked (required if not intact)")
    st.add_argument("--source", default=None, help="URL or source note")
    st.add_argument("--as-of", default=None, help="Override the stamp date (YYYY-MM-DD)")

    b = sub.add_parser("batch", help="Stamp every catalyst in a scan-deltas JSON file")
    b.add_argument("deltas_path")
    b.add_argument("--as-of", default=None)

    s = sub.add_parser("status", help="Freshness of every catalyst's fundamental verdict")
    s.add_argument("--json", action="store_true")
    s.add_argument("--warn-days", type=int, default=DEFAULT_WARN_DAYS)
    s.add_argument("--days", type=int, default=DEFAULT_MAX_DAYS, dest="max_days")
    s.add_argument("--stale-only", action="store_true")

    args = p.parse_args()

    if args.cmd == "stamp":
        try:
            out = stamp(args.catalyst_id, args.verdict, args.evidence, args.source, args.as_of)
        except (FileNotFoundError, ValueError) as e:
            print(f"  ✖ {e}", file=sys.stderr)
            sys.exit(1)
        print(f"  ✓ {out['catalyst_id']} reviewed {out['date']} → {out['verdict']}"
              + (f"  ({out['evidence'][:70]})" if out.get("evidence") else ""))
    elif args.cmd == "batch":
        deltas = json.loads(Path(args.deltas_path).read_text(encoding="utf-8"))
        if isinstance(deltas, dict):
            deltas = deltas.get("deltas") or deltas.get("catalysts") or []
        out = stamp_batch(deltas, as_of=args.as_of)
        print(f"  ✓ stamped {len(out['stamped'])} catalyst(s)")
        for row in out["stamped"]:
            print(f"    {row['catalyst_id']:<48} {row['verdict']}")
        for row in out["failed"]:
            print(f"    ✖ {row['catalyst_id']}: {row['error']}", file=sys.stderr)
    elif args.cmd == "status":
        rows = review_status(args.warn_days, args.max_days)
        if args.stale_only:
            rows = [r for r in rows if r["freshness"] in ("stale", "very_stale", "unknown")]
        if args.json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
            return
        if not rows:
            print("  (no catalysts)")
            return
        print(f"  {'catalyst_id':<48}{'kind':<12}{'reviewed':<12}{'age':>5}  {'freshness':<11}verdict")
        for r in rows:
            age = "?" if r["age_days"] is None else str(r["age_days"])
            print(f"  {str(r['catalyst_id']):<48}{r['kind']:<12}"
                  f"{str(r['last_reviewed'] or '—'):<12}{age:>5}  {r['freshness']:<11}"
                  f"{r['last_verdict'] or '—'}")
        n_bad = sum(1 for r in rows if r["freshness"] in ("stale", "very_stale", "unknown"))
        print(f"\n  {n_bad}/{len(rows)} need a re-verify "
              f"(> {args.warn_days}d stale, > {args.max_days}d very stale)")


if __name__ == "__main__":
    main()
