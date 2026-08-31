"""CATALYX — apply indicator observations to a structural catalyst, deterministically.

WHY THIS MODULE EXISTS (docs/PLAN_v3_lean_pipeline_rebalance.md §2.4).

`/catalyx-update` used to be nine hand-executed steps: read the YAML, find the indicator, shift
`current_value → last_value`, append to `value_history`, stamp `last_date`, stamp
`status_last_reviewed`, check the deactivation conditions, write the file, then run the intensity
engine. Every one of those is arithmetic or bookkeeping, and doing it in the conversation cost a
few thousand tokens per indicator — with ~44 indicators across 12 live catalysts, that is the
single most repetitive thing the review does.

It was also DRIFTING. The skill still told Claude to append the prior observation to the inline
`value_history`, but schema 1.4 moved history to the parquet lake (`indicator_history`, keyed by
catalyst_id) and `intensity_engine` reads the lake FIRST. So hand-applied updates were writing to
a deprecated field that the scorer no longer reads: the observation looked recorded, and the
empirical percentile never saw it. That class of bug is invisible by construction — which is
exactly why this belongs in code with a test, not in prose with a checklist.

WHAT IT DOES NOT DO: it never decides that a value is right, never sets `score` or `semaphore`
(both derived by `intensity_engine`), and never changes `status`. It records an observation the
human or the scan supplied, and recomputes what follows from it.

CLI:
    uv run python -m catalyx.store.indicator_update set struct_nato_rearmament ind_02 0.22 \\
        --note "RHM Q2: order book +22% YoY" --source "Rheinmetall Q2 2026 report"
    uv run python -m catalyx.store.indicator_update batch data/reports/scan_deltas_20260828.json
    uv run python -m catalyx.store.indicator_update maturity struct_ai_capex_supercycle crowded
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[2]
_STRUCTURAL_DIR = _REPO_ROOT / "catalyx" / "config" / "structural_catalysts"

MATURITY_LEVELS = ("ignored", "emerging", "mainstream", "crowded", "exhausted")


# ── Pure helpers (unit-tested) ───────────────────────────────────────────────

def find_file(catalyst_id: str, structural_dir: Path | None = None) -> Path:
    """Locate a structural catalyst's YAML. Accepts the id with or without the `struct_` prefix."""
    d = structural_dir or _STRUCTURAL_DIR
    stem = catalyst_id[len("struct_"):] if catalyst_id.startswith("struct_") else catalyst_id
    for candidate in (d / f"{stem}.yaml", d / f"{catalyst_id}.yaml"):
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"no structural catalyst YAML for '{catalyst_id}' in {d}")


def shift_values(indicator: dict, new_value: float, as_of: str) -> dict:
    """The value shift, as data: `current → last`, new value in, `last_date` stamped.

    Returns the fields to write plus the observation to archive. Kept pure so the ordering (the
    part a human gets wrong at 11pm) is pinned by a test rather than by a numbered instruction.
    """
    prior_value = indicator.get("current_value")
    prior_date = indicator.get("last_date")
    return {
        "fields": {"current_value": new_value,
                   "last_value": prior_value,
                   "last_date": as_of},
        "archive": ({"date": prior_date or as_of, "value": float(prior_value)}
                    if prior_value is not None else None),
    }


def weakened(indicator: dict, old_value, new_value) -> bool:
    """Did this observation move AGAINST the thesis, per the indicator's own `direction`?

    `higher_is_stronger` weakens on a fall, `lower_is_stronger` on a rise. Unknown direction or
    a missing prior → False: an unknown must not be reported as a weakening (nor as a
    strengthening), and the caller shows nothing rather than guessing.
    """
    if old_value is None or new_value is None:
        return False
    d = str(indicator.get("direction") or "")
    try:
        delta = float(new_value) - float(old_value)
    except (TypeError, ValueError):
        return False
    if d == "higher_is_stronger":
        return delta < 0
    if d == "lower_is_stronger":
        return delta > 0
    return False


def crossed_weak_threshold(indicator: dict, new_value) -> bool:
    """Is the new reading on the weak side of `threshold_weak`?

    This is the one condition the YAML states numerically, so it is the one worth evaluating.
    Everything else about deactivation is prose written for a human to judge.
    """
    t = indicator.get("threshold_weak")
    if t is None or new_value is None:
        return False
    try:
        t, v = float(t), float(new_value)
    except (TypeError, ValueError):
        return False
    d = str(indicator.get("direction") or "")
    if d == "higher_is_stronger":
        return v < t
    if d == "lower_is_stronger":
        return v > t
    return False


def deactivation_notice(catalyst: dict, indicator: dict, old_value, new_value) -> list[str]:
    """Deactivation conditions to put in front of the human, and why they are being shown.

    Deliberately NOT an evaluator. The conditions are prose ("Comprehensive Russia-Ukraine
    ceasefire signed AND NATO formally revises GDP targets downward") and no `indicator_id`
    appears in any of them, so a text match would be silently dead code — worse than the
    checklist step it replaces, because nobody would notice it never fired. Instead the numeric
    facts ARE evaluated (direction, `threshold_weak`) and, when the reading weakens, the
    conditions are printed verbatim for the judgement that is genuinely human.
    """
    weak = weakened(indicator, old_value, new_value)
    crossed = crossed_weak_threshold(indicator, new_value)
    if not (weak or crossed):
        return []
    why = ("crossed threshold_weak" if crossed else "moved against the thesis direction")
    out = [f"{indicator.get('id')} {why} ({old_value} → {new_value}) — re-read the "
           f"deactivation conditions:"]
    for c in catalyst.get("deactivation_conditions") or []:
        text = c if isinstance(c, str) else (c.get("condition") or c.get("description") or "")
        action = f" [{c.get('action')}]" if isinstance(c, dict) and c.get("action") else ""
        out.append(f"  · {text}{action}")
    return out


def _already_recorded(catalyst_id: str, indicator_id: str, obs_date: str, value: float,
                      lake_dir: Path | None = None) -> bool:
    """Is this exact (indicator, date, value) already in the lake's history?"""
    from catalyx.store import lake
    try:
        df = lake.read_table("indicator_history", lake_dir=lake_dir)
    except Exception:
        return False
    if df.empty or "catalyst_id" not in df.columns:
        return False
    hit = df[(df["catalyst_id"] == catalyst_id) & (df["indicator_id"] == indicator_id)
             & (df["date"].astype(str) == str(obs_date))]
    return bool(len(hit)) and any(abs(float(v) - float(value)) < 1e-9 for v in hit["value"])


def _round_trip():
    from ruamel.yaml import YAML as RuamelYAML
    ry = RuamelYAML()
    ry.preserve_quotes = True
    ry.width = 120
    return ry


# ── Apply ────────────────────────────────────────────────────────────────────

def apply_one(catalyst_id: str, indicator_id: str, value: float, as_of: str | None = None,
              note: str | None = None, source: str | None = None,
              structural_dir: Path | None = None, lake_dir: Path | None = None,
              write_history: bool = True) -> dict:
    """Record one observation. Writes the YAML and archives the PRIOR value to the lake.

    The prior value is what gets archived, not the new one: `current_value` already carries the
    latest reading, so appending it too would double-count the newest point in the empirical
    percentile that `intensity_engine` computes from the history.
    """
    from catalyx.store import indicator_history

    as_of = as_of or date.today().isoformat()
    path = find_file(catalyst_id, structural_dir)
    ry = _round_trip()
    with path.open("r", encoding="utf-8") as fh:
        catalyst = ry.load(fh)

    indicators = catalyst.get("indicators") or []
    ind = next((i for i in indicators if i.get("id") == indicator_id), None)
    if ind is None:
        raise ValueError(f"{catalyst_id}: no indicator '{indicator_id}' "
                         f"(has: {', '.join(str(i.get('id')) for i in indicators)})")

    shift = shift_values(ind, value, as_of)
    prior = ind.get("current_value")
    for k, v in shift["fields"].items():
        ind[k] = v
    if note:
        ind["update_note"] = note
    catalyst["status_last_reviewed"] = as_of

    with path.open("w", encoding="utf-8") as fh:
        ry.dump(catalyst, fh)

    rows, archived = None, None
    if write_history and shift["archive"]:
        a = shift["archive"]
        cid = catalyst.get("id") or catalyst_id
        # Idempotence: applying the same observation twice (a re-run of a scan, a corrected
        # value re-entered) must not stack duplicate rows — the empirical percentile in
        # intensity_engine weights by row count, so a double entry silently re-weights history.
        if _already_recorded(cid, indicator_id, a["date"], a["value"], lake_dir=lake_dir):
            archived = "skipped (already in history)"
        else:
            rows = indicator_history.append_observation(
                cid, indicator_id, a["date"], a["value"], source=source, lake_dir=lake_dir)
            archived = f"{a['date']}={a['value']}"

    return {"catalyst_id": catalyst.get("id") or catalyst_id, "indicator_id": indicator_id,
            "old_value": prior, "new_value": value, "as_of": as_of,
            "history_rows": rows, "archived": archived, "path": str(path),
            "weakened": weakened(ind, prior, value),
            "warnings": deactivation_notice(catalyst, ind, prior, value)}


def set_maturity(catalyst_id: str, level: str, as_of: str | None = None,
                 structural_dir: Path | None = None) -> dict:
    """Set `narrative_maturity` (the 5-level enum — never a number, per AI Scoring Rule 2)."""
    if level not in MATURITY_LEVELS:
        raise ValueError(f"narrative_maturity must be one of {', '.join(MATURITY_LEVELS)}")
    as_of = as_of or date.today().isoformat()
    path = find_file(catalyst_id, structural_dir)
    ry = _round_trip()
    with path.open("r", encoding="utf-8") as fh:
        catalyst = ry.load(fh)
    old = catalyst.get("narrative_maturity")
    catalyst["narrative_maturity"] = level
    catalyst["status_last_reviewed"] = as_of
    with path.open("w", encoding="utf-8") as fh:
        ry.dump(catalyst, fh)
    return {"catalyst_id": catalyst.get("id") or catalyst_id, "old": old, "new": level,
            "as_of": as_of, "path": str(path)}


def parse_batch(payload) -> list[dict]:
    """Normalize the scan-deltas file into a flat observation list.

    Accepts the same envelope as `catalyst_review batch` and `catalyst_lifecycle --deltas` — one
    file per scan, read by three consumers — in either shape:
        [{catalyst_id, indicators: [{id|indicator_id, value, note, source}]}, …]
        [{catalyst_id, indicator_id, value, …}, …]
    """
    items = payload
    if isinstance(payload, dict):
        items = payload.get("deltas") or payload.get("catalysts") or []
    out = []
    for d in items or []:
        cid = d.get("catalyst_id") or d.get("id")
        inds = d.get("indicators")
        if inds:
            for i in inds:
                if i.get("value") is None:
                    continue
                out.append({"catalyst_id": cid,
                            "indicator_id": i.get("indicator_id") or i.get("id"),
                            "value": i.get("value"), "note": i.get("note"),
                            "source": i.get("source") or d.get("source")})
        elif d.get("indicator_id") and d.get("value") is not None:
            out.append({"catalyst_id": cid, "indicator_id": d.get("indicator_id"),
                        "value": d.get("value"), "note": d.get("note"),
                        "source": d.get("source")})
    return out


def apply_batch(payload, as_of: str | None = None, recompute: bool = True,
                structural_dir: Path | None = None, lake_dir: Path | None = None) -> dict:
    """Apply every observation in a scan-deltas file, then recompute each touched catalyst ONCE.

    The "once" is the point: applying five indicators to one catalyst used to mean five separate
    conversational turns and five intensity recomputations of the same file.
    """
    from catalyx.scorer import intensity_engine

    obs = parse_batch(payload)
    applied, failed = [], []
    for o in obs:
        try:
            applied.append(apply_one(o["catalyst_id"], o["indicator_id"], float(o["value"]),
                                     as_of=as_of, note=o.get("note"), source=o.get("source"),
                                     structural_dir=structural_dir, lake_dir=lake_dir))
        except (FileNotFoundError, ValueError, TypeError) as e:
            failed.append({**o, "error": str(e)})

    recomputed = []
    if recompute:
        for path in sorted({a["path"] for a in applied}):
            try:
                res = intensity_engine.compute_from_yaml(Path(path))
                intensity_engine.write_back(Path(path), res, period=as_of)
                # `compute_from_yaml` returns the id under "id"; reading "catalyst_id" made
                # every recompute line print "Σ None" — the one line that says which catalyst's
                # intensity just moved.
                recomputed.append({"path": path, "catalyst_id": res.get("id"),
                                   "stored_score": res.get("stored_score"),
                                   "computed_score": res.get("computed_score")})
            except Exception as e:                      # a bad YAML must not lose the applied edits
                recomputed.append({"path": path, "error": str(e)})

    return {"as_of": as_of or date.today().isoformat(), "applied": applied,
            "failed": failed, "recomputed": recomputed}


# ── CLI ──────────────────────────────────────────────────────────────────────

def _render(res: dict) -> str:
    out = []
    for a in res["applied"]:
        out.append(f"  ✓ {a['catalyst_id']} {a['indicator_id']}: "
                   f"{a['old_value']} → {a['new_value']}")
        for i, w in enumerate(a["warnings"]):
            out.append(f"    {'⚠ ' if i == 0 else '  '}{w}")
    for f in res["failed"]:
        out.append(f"  ✗ {f.get('catalyst_id')} {f.get('indicator_id')}: {f['error']}")
    for r in res["recomputed"]:
        if r.get("error"):
            out.append(f"  ✗ intensity {r['path']}: {r['error']}")
        else:
            delta = ""
            if r.get("stored_score") is not None and r.get("computed_score") is not None:
                delta = f"  (Δ {r['computed_score'] - r['stored_score']:+.1f})"
            out.append(f"  Σ {r['catalyst_id']}: intensity {r.get('stored_score')} → "
                       f"{r.get('computed_score')}{delta}")
    out.append(f"\n{len(res['applied'])} applied · {len(res['failed'])} failed · "
               f"{len(res['recomputed'])} catalyst(s) recomputed")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Apply indicator observations to structural "
                                             "catalysts and recompute intensity.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("set", help="Record one indicator observation")
    s.add_argument("catalyst_id")
    s.add_argument("indicator_id")
    s.add_argument("value", type=float)
    s.add_argument("--note", default=None)
    s.add_argument("--source", default=None)
    s.add_argument("--as-of", default=None)
    s.add_argument("--no-recompute", action="store_true")

    b = sub.add_parser("batch", help="Apply every observation in a scan-deltas JSON file")
    b.add_argument("deltas_path")
    b.add_argument("--as-of", default=None)
    b.add_argument("--no-recompute", action="store_true")

    m = sub.add_parser("maturity", help="Set narrative_maturity (5-level enum)")
    m.add_argument("catalyst_id")
    m.add_argument("level", choices=list(MATURITY_LEVELS))
    m.add_argument("--as-of", default=None)

    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.cmd == "maturity":
        out = set_maturity(args.catalyst_id, args.level, as_of=args.as_of)
        print(json.dumps(out, indent=2) if args.json else
              f"{out['catalyst_id']} — narrative_maturity: {out['old']} → {out['new']}")
        return

    if args.cmd == "set":
        payload = [{"catalyst_id": args.catalyst_id, "indicator_id": args.indicator_id,
                    "value": args.value, "note": args.note, "source": args.source}]
    else:
        payload = json.loads(Path(args.deltas_path).read_text(encoding="utf-8"))

    res = apply_batch(payload, as_of=args.as_of, recompute=not args.no_recompute)
    print(json.dumps(res, indent=2, default=str) if args.json else _render(res))


if __name__ == "__main__":
    sys.exit(main())
