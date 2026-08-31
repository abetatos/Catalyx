"""Catalyst lifecycle — deterministic status transitions (was Step 1.5b of the review skill).

WHY THIS IS PYTHON NOW (v3 Phase 1, docs/PLAN_v3_lean_pipeline_rebalance.md §2.2):
    The rules below are arithmetic — "decayed strength < 20 AND priced_in ≥ 0.75 → archived",
    "intensity < 40 for 2 consecutive cycles → dormant". They were nonetheless applied by LLM
    judgment inside `/catalyx-review`, which cost tokens on every review, produced a different
    reading of the same numbers across sessions, and (the reason the step exists at all) was
    skippable — a spent, fully-priced-in event kept contributing near-full strength to
    `catalyst_alignment` whenever the step was rushed. The review skill's own note said the
    deterministic home was "a future catalyst_lifecycle.py". This is it.

TRANSITIONS
  event → archived     spent and absorbed: `strength_decayed < event_archive_strength_below`
                       AND `is_priced_in_estimate ≥ event_archive_priced_in_min`. Decay is
                       anchored on the OCCURRENCE date via `catalyst_scorer._anchor_date`
                       (a late-registered event must not read as young).
  event → invalidated  the event REVERSED (policy walked back, ceasefire, deal signed). This is
                       evidence, not arithmetic → it comes in from the scan's delta file, never
                       inferred here.
  structural → dormant `intensity.current_score < structural_dormant_intensity_below` for
                       `structural_dormant_consecutive_cycles` consecutive recorded cycles, OR
                       `narrative_maturity == "exhausted"` (when `structural_dormant_if_exhausted`).
  dormant → active     REACTIVATION: a dormant structural whose intensity has repointed above the
                       threshold comes back. Dormancy is a pause, not a grave — without this the
                       status is a one-way ratchet that quietly shrinks the universe forever.
  event → promote      re-detected `event_promote_to_structural_cycles` times and NOT decaying →
                       flagged as a structural-catalyst candidate. Flag only: drafting the
                       structural is a judgment call (indicators, thresholds) that stays human.

DOCTRINE — `status` is the ONLY field this module writes, and history is never deleted (the
CLAUDE.md rule). `governance: auto` applies transitions; `ask` emits them as pending for the
skill to put to the user. Nothing here touches intensity, scores, or evidence.

CLI:
    uv run python -m catalyx.scorer.catalyst_lifecycle            # dry-run table (proposals)
    uv run python -m catalyx.scorer.catalyst_lifecycle --json
    uv run python -m catalyx.scorer.catalyst_lifecycle --apply    # write the status changes
    uv run python -m catalyx.scorer.catalyst_lifecycle --deltas data/reports/scan_deltas_X.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from catalyx.config import weights as weights_cfg
from catalyx.scorer.catalyst_scorer import _anchor_date, _decayed_strength
from catalyx.store import catalyst_review

_REPO_ROOT = Path(__file__).parents[2]
_STRUCTURAL_DIR = _REPO_ROOT / "catalyx" / "config" / "structural_catalysts"
_EVENT_DIR = _REPO_ROOT / "data" / "catalysts"

# Statuses that are already terminal — a transition proposal for them would be noise.
_DEAD_EVENT = ("archived", "invalidated")
_DEAD_STRUCTURAL = ("deactivated", "merged")


# ── Pure rules (unit-tested; no files, no clock) ─────────────────────────────

def event_transition(strength_decayed: float | None, priced_in: float | None,
                     cfg: dict) -> tuple[str | None, str]:
    """(new_status, reason) for an event catalyst. `None` status = leave it active.

    BOTH conditions must hold: a decayed-but-unpriced event is still tradable (the market has
    not absorbed it), and a priced-in-but-strong event is still shaping the sector.
    """
    floor = float(cfg.get("event_archive_strength_below", 20))
    min_priced = float(cfg.get("event_archive_priced_in_min", 0.75))
    if strength_decayed is None or priced_in is None:
        return None, "missing strength or priced_in — cannot evaluate"
    if strength_decayed < floor and priced_in >= min_priced:
        return "archived", (f"spent: decayed strength {strength_decayed:.1f} < {floor} "
                            f"and priced_in {priced_in:.2f} ≥ {min_priced}")
    return None, (f"active: decayed strength {strength_decayed:.1f}, priced_in {priced_in:.2f}")


def structural_transition(current_status: str, intensity: float | None, maturity: str | None,
                          consecutive_below: int, cfg: dict) -> tuple[str | None, str]:
    """(new_status, reason) for a structural catalyst — including REACTIVATION out of dormant."""
    floor = float(cfg.get("structural_dormant_intensity_below", 40))
    need = int(cfg.get("structural_dormant_consecutive_cycles", 2))
    exhausted_rule = bool(cfg.get("structural_dormant_if_exhausted", True))

    if current_status == "dormant":
        if intensity is not None and intensity >= floor and maturity != "exhausted":
            return "active", f"reactivated: intensity {intensity:.1f} back above {floor}"
        return None, f"stays dormant: intensity {intensity if intensity is not None else '?'}"

    if exhausted_rule and maturity == "exhausted":
        return "dormant", "narrative exhausted — no edge left to harvest"
    if intensity is not None and intensity < floor and consecutive_below >= need:
        return "dormant", (f"intensity {intensity:.1f} < {floor} for {consecutive_below} "
                           f"consecutive cycles (need {need})")
    if intensity is not None and intensity < floor:
        return None, (f"below {floor} but only {consecutive_below}/{need} consecutive cycles "
                      f"— not yet dormant")
    return None, f"active: intensity {intensity if intensity is not None else '?'}"


def consecutive_below(history: list[dict], floor: float) -> int:
    """How many of the MOST RECENT recorded cycles scored below `floor` (newest first).

    Counting from the newest entry is what makes "2 consecutive cycles" mean *now*, not "twice
    at some point in 2024" — a catalyst that dipped, recovered, and dipped again is at 1.
    """
    n = 0
    for entry in history or []:
        score = (entry or {}).get("score")
        if score is None or float(score) >= floor:
            break
        n += 1
    return n


def promotion_candidate(detections: int, strength_decayed: float | None,
                        strength_raw: float | None, cfg: dict) -> bool:
    """Re-detected N cycles AND not decaying (the underlying is ongoing, not a one-off spike)."""
    need = int(cfg.get("event_promote_to_structural_cycles", 3))
    if detections < need or strength_decayed is None or strength_raw in (None, 0):
        return False
    return (strength_decayed / float(strength_raw)) >= 0.5


# ── Evaluation over the real files ───────────────────────────────────────────

def _load_events(event_dir: Path) -> list[tuple[Path, dict]]:
    out = []
    for f in sorted(event_dir.glob("*.json")) if event_dir.exists() else []:
        try:
            out.append((f, json.loads(f.read_text(encoding="utf-8"))))
        except Exception:  # noqa: BLE001
            continue
    return out


def _load_structurals(structural_dir: Path) -> list[tuple[Path, dict]]:
    out = []
    for f in sorted(structural_dir.glob("*.yaml")) if structural_dir.exists() else []:
        try:
            doc = yaml.safe_load(f.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                out.append((f, doc))
        except Exception:  # noqa: BLE001
            continue
    return out


def evaluate(cfg: dict | None = None, deltas: list[dict] | None = None,
             structural_dir: Path | None = None, event_dir: Path | None = None,
             as_of: date | None = None) -> dict:
    """Evaluate every catalyst against the lifecycle rules. Pure read — writes nothing."""
    cfg = cfg or weights_cfg.catalyst_lifecycle()
    sdir = structural_dir or _STRUCTURAL_DIR
    edir = event_dir or _EVENT_DIR
    halflife_default = weights_cfg.event_default_halflife()
    floor = float(cfg.get("structural_dormant_intensity_below", 40))

    # Reversals are EVIDENCE, supplied by the scan — never inferred from numbers here.
    reversed_ids = {
        (d.get("catalyst_id") or d.get("id")): (d.get("evidence") or d.get("reason") or "reversed")
        for d in (deltas or [])
        if (d.get("verdict") == "breaking" and d.get("invalidated"))
        or d.get("lifecycle_flag") == "invalidated"
    }

    transitions, unchanged, promotions = [], [], []

    for path, ev in _load_events(edir):
        cid, status = ev.get("id"), ev.get("status", "active")
        if status in _DEAD_EVENT:
            continue
        strength = ev.get("strength_score")
        halflife = ev.get("decay_halflife_days") or halflife_default
        decayed = _decayed_strength(float(strength), float(halflife), _anchor_date(ev)) \
            if strength is not None else None
        priced = ev.get("is_priced_in_estimate")

        if cid in reversed_ids:
            transitions.append({"catalyst_id": cid, "kind": "event", "from": status,
                                "to": "invalidated", "reason": f"reversed — {reversed_ids[cid]}",
                                "path": str(path)})
            continue

        new_status, reason = event_transition(decayed, priced, cfg)
        row = {"catalyst_id": cid, "kind": "event", "from": status, "to": new_status,
               "reason": reason, "path": str(path), "strength_decayed": decayed,
               "priced_in": priced}
        (transitions if new_status else unchanged).append(row)

        if promotion_candidate(int(ev.get("detection_count") or 1), decayed, strength, cfg):
            promotions.append({"catalyst_id": cid, "detections": ev.get("detection_count"),
                               "strength_decayed": decayed,
                               "note": "re-detected and not decaying → draft a structural"})

    for path, sc in _load_structurals(sdir):
        cid, status = sc.get("id"), sc.get("status", "active")
        if status in _DEAD_STRUCTURAL:
            continue
        intensity_block = sc.get("intensity") or {}
        intensity = intensity_block.get("current_score")
        n_below = consecutive_below(intensity_block.get("history") or [], floor)
        new_status, reason = structural_transition(
            status, float(intensity) if intensity is not None else None,
            sc.get("narrative_maturity"), n_below, cfg)
        row = {"catalyst_id": cid, "kind": "structural", "from": status, "to": new_status,
               "reason": reason, "path": str(path), "intensity": intensity,
               "consecutive_below": n_below}
        (transitions if new_status else unchanged).append(row)

    return {
        "as_of": (as_of or date.today()).isoformat(),
        "governance": cfg.get("governance", "auto"),
        "transitions": transitions,
        "promotion_candidates": promotions,
        "unchanged": unchanged,
    }


# ── Apply ────────────────────────────────────────────────────────────────────

def apply_transitions(transitions: list[dict], as_of: str | None = None) -> list[dict]:
    """Write each `status` change to its backing file. Only `status` (+ an audit note) changes.

    History is never deleted (CLAUDE.md): the prior status is preserved in `lifecycle_log[]`, so
    a wrong archive is always reversible and always explains itself.
    """
    applied = []
    stamp = as_of or date.today().isoformat()
    for t in transitions:
        if not t.get("to"):
            continue
        path = Path(t["path"])
        entry = {"date": stamp, "from": t["from"], "to": t["to"], "reason": t["reason"]}
        try:
            if path.suffix == ".yaml":
                _apply_yaml(path, t["to"], entry)
            else:
                _apply_json(path, t["to"], entry)
        except Exception as e:  # noqa: BLE001
            t["error"] = str(e)
            continue
        applied.append(t)
    return applied


def _apply_yaml(path: Path, new_status: str, entry: dict) -> None:
    from ruamel.yaml import YAML as RuamelYAML
    from ruamel.yaml.comments import CommentedMap

    ry = RuamelYAML()
    ry.preserve_quotes = True
    ry.width = 120
    with path.open("r", encoding="utf-8") as fh:
        doc = ry.load(fh)
    doc["status"] = new_status
    log = doc.get("lifecycle_log")
    if log is None:
        log = []
        doc["lifecycle_log"] = log
    log.insert(0, CommentedMap(entry))
    with path.open("w", encoding="utf-8") as fh:
        ry.dump(doc, fh)


def _apply_json(path: Path, new_status: str, entry: dict) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["status"] = new_status
    if new_status == "invalidated" and not doc.get("invalidation_reason"):
        doc["invalidation_reason"] = entry["reason"]
    doc["lifecycle_log"] = [entry] + list(doc.get("lifecycle_log") or [])
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(
        description="CATALYX catalyst lifecycle — deterministic status transitions")
    p.add_argument("--apply", action="store_true", help="Write the transitions (default: dry run)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--deltas", default=None,
                   help="Scan deltas JSON — supplies REVERSALS (invalidation is evidence, not math)")
    p.add_argument("--show-unchanged", action="store_true")
    args = p.parse_args()

    deltas = None
    if args.deltas:
        raw = json.loads(Path(args.deltas).read_text(encoding="utf-8"))
        deltas = raw if isinstance(raw, list) else (raw.get("deltas") or raw.get("catalysts") or [])

    result = evaluate(deltas=deltas)
    governance = result["governance"]

    if args.apply:
        if governance == "ask":
            print("  governance='ask' → transitions are PENDING; the skill must confirm each "
                  "with the user before --apply is honoured.", file=sys.stderr)
        else:
            result["applied"] = apply_transitions(result["transitions"])
            result["applied_at"] = datetime.now(timezone.utc).isoformat()

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    trans = result["transitions"]
    # `"applied" in result`, not `result.get("applied")`: with --apply and zero transitions the
    # applied list is empty and falsy, so the header used to tell the operator to pass the flag
    # they had just passed. "Nothing to apply" and "you forgot the flag" are different states.
    verb = ("APPLIED" if "applied" in result
            else "PENDING (governance=ask)" if governance == "ask"
            else "PROPOSED (dry run — use --apply)")
    print(f"CATALYX — catalyst lifecycle  [{verb}]  as of {result['as_of']}\n")
    if not trans:
        print("  no transitions — every catalyst is in the right state")
    else:
        print(f"  {'catalyst_id':<48}{'kind':<12}{'from':<10}→ {'to':<12}reason")
        for t in trans:
            print(f"  {str(t['catalyst_id']):<48}{t['kind']:<12}{t['from']:<10}→ "
                  f"{str(t['to']):<12}{t['reason']}")
    if result["promotion_candidates"]:
        print("\n  Promotion candidates (event → structural; drafting stays human):")
        for c in result["promotion_candidates"]:
            print(f"    {c['catalyst_id']:<48}{c['note']}")
    if args.show_unchanged:
        print(f"\n  Unchanged ({len(result['unchanged'])}):")
        for u in result["unchanged"]:
            print(f"    {str(u['catalyst_id']):<48}{u['reason']}")
    else:
        print(f"\n  {len(result['unchanged'])} catalyst(s) unchanged (--show-unchanged for detail)")

    stale = [r for r in catalyst_review.review_status()
             if r["freshness"] in ("very_stale", "unknown")]
    if stale:
        print(f"  ⚠ {len(stale)} catalyst(s) carry a stale/absent fundamental verdict — "
              f"`catalyst_review status --stale-only`")


if __name__ == "__main__":
    main()
