"""Deterministic intensity scorer for StructuralCatalyst objects.

Formula (scoring_weights.yaml §STRUCTURAL CATALYST INTENSITY):
  1. Semaphore score per indicator: green=100, yellow=65, red=20
  2. indicator_avg = weighted mean (equal weight unless indicator_weight set)
  3. trend_factor from intensity.history last 2 periods
  4. score = round(indicator_avg × trend_factor, 1), capped to [10, 95]

Usage (callable from skills via Bash):
    uv run python -m catalyx.scorer.intensity_engine <path/to/catalyst.yaml>
    uv run python -m catalyx.scorer.intensity_engine --all
    uv run python -m catalyx.scorer.intensity_engine --all --write-back   # update YAMLs in place

Write-back behaviour:
  - Updates intensity.current_score and intensity.last_updated in the YAML
  - Prepends a new entry to intensity.history (computation_method: "computed")
  - Writes updated semaphore fields on each indicator
  - Does NOT change indicator current_value — that is the user/update-skill's job

Output: JSON with computed_score, stored_score, delta, and per-indicator breakdown.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import yaml

# ── Constants from scoring_weights.yaml ───────────────────────────────────────

SEMAPHORE_SCORES: dict[str, int] = {"🟢": 100, "🟡": 65, "🔴": 20}

TREND_FACTORS: dict[str, float] = {
    "rising_2plus": 1.05,
    "rising_1":     1.02,
    "flat":         1.00,
    "falling_1":    0.97,
    "falling_2plus": 0.93,
}

INTENSITY_MIN = 10
INTENSITY_MAX = 95

_CATALYSTS_DIR = Path(__file__).parents[1] / "config" / "structural_catalysts"


# ── Core logic ─────────────────────────────────────────────────────────────────

def _semaphore(ind: dict) -> str:
    """Compute semaphore from thresholds. Ignores any stored semaphore field."""
    val = ind.get("current_value")
    if val is None:
        return "🔴"
    strong = ind["threshold_strong"]
    weak = ind["threshold_weak"]

    if ind["direction"] == "higher_is_stronger":
        if val >= strong:
            return "🟢"
        if val >= weak:
            return "🟡"
        return "🔴"
    else:  # lower_is_stronger
        if val <= strong:
            return "🟢"
        if val <= weak:
            return "🟡"
        return "🔴"


def _trend_factor(history: list[dict]) -> tuple[float, str]:
    """Return (factor, label) from the two most-recent history entries.

    History is expected most-recent-first (as written in YAML files).
    Consecutive = both the most-recent AND the prior period moved in the same direction.
    """
    scores = [h["score"] for h in history if isinstance(h.get("score"), (int, float))]
    if len(scores) < 2:
        return TREND_FACTORS["flat"], "→ (flat — insufficient history)"

    d1 = scores[0] - scores[1]  # most-recent period delta

    if len(scores) >= 3:
        d2 = scores[1] - scores[2]  # prior period delta
        if d1 > 2 and d2 > 2:
            return TREND_FACTORS["rising_2plus"], "↑↑ (rising 2+ consecutive)"
        if d1 < -2 and d2 < -2:
            return TREND_FACTORS["falling_2plus"], "↓↓ (falling 2+ consecutive)"

    if d1 > 2:
        return TREND_FACTORS["rising_1"], "↑ (rising 1 period)"
    if d1 < -2:
        return TREND_FACTORS["falling_1"], "↓ (falling 1 period)"
    return TREND_FACTORS["flat"], "→ (flat)"


def compute_intensity(catalyst: dict) -> dict:
    """Compute intensity score for a parsed StructuralCatalyst dict.

    Returns a result dict suitable for JSON serialisation and skill consumption.
    """
    indicators = catalyst.get("indicators", [])
    if not indicators:
        return {
            "id": catalyst.get("id"),
            "error": "No indicators defined",
        }

    breakdown = []
    scores_weighted: list[tuple[float, float]] = []  # (score, weight)

    for ind in indicators:
        sem = _semaphore(ind)
        score = SEMAPHORE_SCORES[sem]
        weight = float(ind.get("indicator_weight") or 1.0)
        scores_weighted.append((score, weight))

        stored_sem = ind.get("semaphore", "")
        breakdown.append({
            "id": ind["id"],
            "name": ind.get("name", ""),
            "current_value": ind.get("current_value"),
            "unit": ind.get("unit", ""),
            "direction": ind["direction"],
            "threshold_strong": ind["threshold_strong"],
            "threshold_weak": ind["threshold_weak"],
            "semaphore_computed": sem,
            "semaphore_stored": stored_sem,
            "semaphore_drift": sem != stored_sem and bool(stored_sem),
            "score": score,
            "weight": weight,
        })

    total_weight = sum(w for _, w in scores_weighted)
    indicator_avg = sum(s * w for s, w in scores_weighted) / total_weight

    history = catalyst.get("intensity", {}).get("history", [])
    trend_factor, trend_label = _trend_factor(history)

    raw = indicator_avg * trend_factor
    computed_score = round(max(INTENSITY_MIN, min(INTENSITY_MAX, raw)), 1)

    stored = catalyst.get("intensity", {}).get("current_score")
    delta = round(computed_score - stored, 1) if stored is not None else None

    return {
        "id": catalyst.get("id"),
        "computed_score": computed_score,
        "stored_score": stored,
        "delta": delta,
        "indicator_avg": round(indicator_avg, 2),
        "trend_factor": trend_factor,
        "trend_label": trend_label,
        "capped": raw != computed_score,
        "breakdown": breakdown,
    }


def compute_from_yaml(path: Path) -> dict:
    """Load a YAML file and compute intensity. Returns result dict."""
    catalyst = yaml.safe_load(path.read_text(encoding="utf-8"))
    result = compute_intensity(catalyst)
    result["_source_file"] = str(path)
    return result


def compute_all() -> list[dict]:
    """Compute intensity for every YAML in the structural_catalysts directory."""
    results = []
    for f in sorted(_CATALYSTS_DIR.glob("*.yaml")):
        results.append(compute_from_yaml(f))
    return results


def write_back(path: Path, result: dict, period: str | None = None) -> None:
    """Write computed score back into the YAML file in place.

    Uses ruamel.yaml to preserve original formatting, comments, and block scalars.

    Updates:
      - intensity.current_score, computation_method, last_updated
      - intensity.history — prepends new entry (skips if same period+score already logged)
      - indicators[*].semaphore — set to computed value
      Does NOT touch indicator current_value.
    """
    from ruamel.yaml import YAML as RuamelYAML
    from ruamel.yaml.comments import CommentedMap

    ry = RuamelYAML()
    ry.preserve_quotes = True
    ry.width = 120

    with path.open("r", encoding="utf-8") as fh:
        catalyst = ry.load(fh)

    today = date.today().isoformat()
    new_score = result["computed_score"]
    stored_score = result.get("stored_score")
    breakdown = result.get("breakdown", [])

    intensity = catalyst["intensity"]
    intensity["current_score"] = new_score
    intensity["computation_method"] = "computed"
    intensity["last_updated"] = today

    history = intensity["history"]
    entry_period = period or today
    already_logged = (
        len(history) > 0
        and history[0].get("period") == entry_period
        and history[0].get("score") == new_score
    )
    if not already_logged:
        note = f"Computed {today}: avg={result['indicator_avg']} trend={result['trend_label']}"
        if stored_score is not None and stored_score != new_score:
            note += f" (was {stored_score})"
        new_entry = CommentedMap({
            "period": entry_period,
            "score": new_score,
            "note": note,
            "computation_method": "computed",
        })
        history.insert(0, new_entry)

    sem_map = {b["id"]: b["semaphore_computed"] for b in breakdown}
    for ind in catalyst.get("indicators", []):
        if ind["id"] in sem_map:
            ind["semaphore"] = sem_map[ind["id"]]

    with path.open("w", encoding="utf-8") as fh:
        ry.dump(catalyst, fh)


# ── CLI ────────────────────────────────────────────────────────────────────────

def _format_result(r: dict) -> str:
    """Human-readable one-line summary of a result."""
    if "error" in r:
        return f"  {r['id']}: ERROR — {r['error']}"
    delta_str = f"  Δ={r['delta']:+.1f}" if r["delta"] is not None else ""
    drift = [b for b in r.get("breakdown", []) if b.get("semaphore_drift")]
    drift_str = f"  ⚠ semaphore drift: {[b['id'] for b in drift]}" if drift else ""
    return (
        f"  {r['id']}: computed={r['computed_score']}  "
        f"stored={r['stored_score']}{delta_str}  "
        f"avg={r['indicator_avg']}  trend={r['trend_label']}{drift_str}"
    )


def main() -> None:
    # Force UTF-8 on Windows consoles
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="CATALYX intensity engine — compute structural catalyst scores from indicator semaphores"
    )
    parser.add_argument(
        "yaml_file", nargs="?", type=Path,
        help="Path to a structural catalyst YAML. Omit to use --all."
    )
    parser.add_argument(
        "--all", action="store_true",
        help=f"Compute all catalysts in {_CATALYSTS_DIR}"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output raw JSON (default: human-readable summary + JSON)"
    )
    parser.add_argument(
        "--write-back", action="store_true",
        help="Write computed score back to YAML file(s) in place"
    )
    parser.add_argument(
        "--period", type=str, default=None,
        help="Period label for history entry, e.g. '2026-Q2' (default: today's date)"
    )
    args = parser.parse_args()

    if args.all or args.yaml_file is None:
        results = compute_all()
        paths = sorted(_CATALYSTS_DIR.glob("*.yaml")) if args.write_back else []
    else:
        results = [compute_from_yaml(args.yaml_file)]
        paths = [args.yaml_file] if args.write_back else []

    if args.write_back:
        for path, result in zip(paths, results):
            if "error" not in result:
                write_back(path, result, period=args.period)
                print(f"Updated: {path.name}  score={result['computed_score']}", file=sys.stderr)

    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0], indent=2, ensure_ascii=False))
        return

    print("CATALYX — Structural Catalyst Intensity Engine\n")
    for r in results:
        print(_format_result(r))
        if "breakdown" in r:
            for b in r["breakdown"]:
                drift_flag = " ⚠ DRIFT" if b.get("semaphore_drift") else ""
                print(
                    f"    {b['id']}: {b['semaphore_computed']} "
                    f"score={b['score']}  val={b['current_value']} {b['unit']}"
                    f"{drift_flag}"
                )
        print()

    print("--- JSON output ---")
    print(json.dumps(results if len(results) > 1 else results[0], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
