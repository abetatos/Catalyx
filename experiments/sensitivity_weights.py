"""Which of these constants actually have consequences? (plan v6 I4)

The honest answer to "your percentages are arbitrary" is not a better story about where each
one came from. It is a measurement: perturb each constant one at a time and see whether the
OUTPUT moves. A constant that cannot flip the ranking or move a euro is not worth arguing
about, and should stop consuming review attention; one that flips it on a ±25% nudge is where
evidence is owed before anything else.

TWO OUTPUTS, because the chain has two halves and conflating them would report a false zero:

  RANKING knobs  — composite weights, the catalyst-alignment internals, the momentum periods.
                   Measured as Kendall τ against the base ranking, plus the Jaccard overlap of
                   the top-10 set (τ can stay high while the top of the book churns, and the
                   top is the part that becomes positions).

  SIZING knobs   — `sharpness`, `vol_tilt_alpha`. These CANNOT reorder anything; scoring them
                   on τ would print a meaningless 1.000. They are measured as the largest
                   weight change in percentage points on the model book — TWICE: as the book is
                   actually built, and again at λ=1. `skill_shrink` blends the conviction leg
                   toward a neutral book by the measured rank IC, and that IC is noise today, so
                   λ=0 and a decisive constant can read as inert purely because the layer it
                   feeds is switched off. Those are opposite conclusions and get separate columns.

Everything is perturbed ±25% and ±50%, one at a time, over the last recorded run. Nothing is
written: this reads the lake and the config and prints a table.

Run: uv run python experiments/sensitivity_weights.py [--top-n 10] [--json]
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalyx.config import weights as W                                    # noqa: E402
from catalyx.scorer import catalyst_scorer as CS                           # noqa: E402
from catalyx.scorer import intensity_engine as IE                          # noqa: E402
from catalyx.scorer import momentum_engine as ME                           # noqa: E402
from catalyx.scorer import sector_scorer as SS                             # noqa: E402
from catalyx.store import snapshot_repo as SR                              # noqa: E402

FACTORS = (0.5, 0.75, 1.25, 1.5)


# ── Rank agreement ───────────────────────────────────────────────────────────

def kendall_tau(a: list[str], b: list[str]) -> float:
    """τ-a between two orderings of the same items. 1.0 = identical, −1.0 = reversed."""
    pos_a = {s: i for i, s in enumerate(a)}
    pos_b = {s: i for i, s in enumerate(b)}
    items = [s for s in a if s in pos_b]
    n = len(items)
    if n < 2:
        return 1.0
    conc = disc = 0
    for i in range(n):
        for j in range(i + 1, n):
            x, y = items[i], items[j]
            s = (pos_a[x] - pos_a[y]) * (pos_b[x] - pos_b[y])
            if s > 0:
                conc += 1
            elif s < 0:
                disc += 1
    return (conc - disc) / (n * (n - 1) / 2)


def jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if (sa | sb) else 1.0


# ── The scored universe, recomputed under whatever is currently patched ──────

def _score() -> tuple[list[str], dict[str, float]]:
    """(ranking, {sector: composite}). Both are needed: a constant can move every score without
    moving the ORDER, and it can also fail to move anything at all — and those two are very
    different findings that a τ of 1.000 reports identically."""
    ids = SS._investable_sector_ids()
    mats = {s: SR._narrative_maturity(s) for s in ids}
    scored = [SS.score_sector(s, crowding_risk=SR._crowding_for(mats[s])) for s in ids]
    SS.commensurate(scored)
    order = sorted(zip(ids, scored), key=lambda kv: SS.rank_key(scored)(kv[1]))
    return [s for s, _ in order], {s: r["composite"] for s, r in zip(ids, scored)}


def _model_weights() -> dict[str, float]:
    from catalyx.execution import portfolio as PF
    res = PF.build_model_holdings("catalyx", persist=False)
    return {h["sector_id"]: h["weight_pct"] for h in res.get("holdings", [])}


# ── Knobs ────────────────────────────────────────────────────────────────────
#
# Each knob patches the constant WHERE IT WAS CAPTURED. Several scorers read their weights
# once at import time into module-level names, so patching the YAML or the accessor would
# change nothing and the harness would confidently report every one of them as inert — a
# false negative that looks exactly like a real result.

@contextlib.contextmanager
def _patch(obj, attr, value):
    old = getattr(obj, attr)
    setattr(obj, attr, value)
    try:
        yield
    finally:
        setattr(obj, attr, old)


@contextlib.contextmanager
def _composite_weight(dim: str, f: float):
    base = dict(W.composite_weights())
    new = dict(base)
    new[dim] = base[dim] * f
    attr = {"catalyst_alignment": "_W_CATALYST", "momentum": "_W_MOMENTUM",
            "flow_confirmation": "_W_FLOW", "crowding_risk": "_W_CROWDING"}[dim]
    with _patch(W, "composite_weights", lambda: new), _patch(SS, attr, new[dim]):
        yield


@contextlib.contextmanager
def _momentum_period(period: str, f: float):
    # `_MPW` is unpacked into _WEIGHT_1M/3M/6M at import, and THOSE are what the engine reads.
    # Patching `_MPW` changes nothing and the harness reported the momentum periods as inert —
    # a false negative indistinguishable from a real result. Patch where the value is consumed.
    attr = {"return_1m": "_WEIGHT_1M", "return_3m": "_WEIGHT_3M", "return_6m": "_WEIGHT_6M"}[period]
    with _patch(ME, attr, getattr(ME, attr) * f):
        yield


@contextlib.contextmanager
def _sub_weights(f: float):
    """Scale the EVENT component; structural takes the remainder, as the pair is a split."""
    ev = min(0.99, CS._EVENT_SUB_WEIGHT * f)
    with _patch(CS, "_EVENT_SUB_WEIGHT", ev), _patch(CS, "_STRUCTURAL_SUB_WEIGHT", 1.0 - ev):
        yield


@contextlib.contextmanager
def _scalar(mod, attr, f: float):
    with _patch(mod, attr, getattr(mod, attr) * f):
        yield


@contextlib.contextmanager
def _trend_deltas(f: float):
    with _patch(IE, "_TREND_DELTAS", {k: v * f for k, v in IE._TREND_DELTAS.items()}):
        yield


@contextlib.contextmanager
def _weighting(key: str, f: float, unshrunk: bool = False):
    """Scale a sizing constant BOTH globally and in the profile that overrides it.

    `build_model_holdings` reads `c.get(key, pw.get(key))` — the profile wins. `catalyx.yaml`
    declares its own `sharpness`, so patching only the global accessor reached nothing and the
    harness reported sharpness as inert. Same false-negative class as the momentum periods:
    a knob that misses its target is indistinguishable from a constant that does not matter.
    """
    from catalyx.execution import portfolio as PF

    glob = dict(W.portfolio_weighting())
    glob[key] = glob[key] * f
    base_profile = PF.load_profile("catalyx")

    def patched(pid: str):
        prof = dict(base_profile)
        con = dict(prof["construction"])
        if key in con:
            con[key] = con[key] * f
        if unshrunk:
            con["tilt_shrinkage"] = False       # λ=1: what the constant does when it is ON
        prof["construction"] = con
        return prof

    with _patch(W, "portfolio_weighting", lambda: glob), \
            _patch(PF, "load_profile", patched):
        yield


# Why a knob can come back NOT REACHED. Verified 2026-08-31 — a harness that prints "no effect"
# without saying whether it ever touched the code path is reporting its own wiring as a finding.
NOT_REACHED_WHY = {
    "event_decay.default_halflife_days":
        "all 15 active events declare their own decay_halflife_days — the default is never read",
    "catalyst_interaction.confirm_max":
        "needs an event that CONFIRMS a structural present in the same sector; none today",
    "catalyst_interaction.contradict_max":
        "needs an event that CONTRADICTS a structural present in the same sector",
    "intensity_trend_deltas (all)":
        "catalyst_scorer reads the STORED intensity.current_score — these only apply at "
        "indicator_update / intensity_engine --write-back time, not at scoring time",
    "catalyst_sub_weights.event_component":
        "only bites where a sector has BOTH structural and event contributions to blend",
}

RANK_KNOBS = {
    "composite_weights.catalyst_alignment": lambda f: _composite_weight("catalyst_alignment", f),
    "composite_weights.momentum":           lambda f: _composite_weight("momentum", f),
    "composite_weights.flow_confirmation":  lambda f: _composite_weight("flow_confirmation", f),
    "composite_weights.crowding_risk":      lambda f: _composite_weight("crowding_risk", f),
    "momentum_period_weights.return_3m":    lambda f: _momentum_period("return_3m", f),
    "momentum_period_weights.return_6m":    lambda f: _momentum_period("return_6m", f),
    "catalyst_sub_weights.event_component": _sub_weights,
    "multi_catalyst.reinforce_factor":      lambda f: _scalar(CS, "_REINFORCE_FACTOR", f),
    "event_decay.default_halflife_days":    lambda f: _scalar(CS, "_DEFAULT_HALFLIFE", f),
    "catalyst_interaction.confirm_max":     lambda f: _scalar(CS, "_CONFIRM_MAX_POINTS", f),
    "catalyst_interaction.contradict_max":  lambda f: _scalar(CS, "_CONTRADICT_MAX_POINTS", f),
    "intensity_trend_deltas (all)":         _trend_deltas,
    # Control: a monotone rescaling of the composite. It MUST move every score and NO position
    # in the order — if it fails either half, the harness is broken, and a sensitivity table
    # nobody can falsify is decoration.
    "composite_scale.z_scale [control]":    lambda f: _weighting_noop(f),
}

SIZE_KNOBS = {
    "portfolio_weighting.sharpness":      "sharpness",
    "portfolio_weighting.vol_tilt_alpha": "vol_tilt_alpha",
}


@contextlib.contextmanager
def _weighting_noop(f: float):
    base = dict(W.composite_scale())
    base["z_scale"] = base["z_scale"] * f
    with _patch(W, "composite_scale", lambda: base):
        yield


# ── Run ──────────────────────────────────────────────────────────────────────

def run(top_n: int = 10) -> dict:
    base_rank, base_scores = _score()
    base_w = _model_weights()

    rank_rows = []
    for name, knob in RANK_KNOBS.items():
        taus, jacs, touched = [], [], 0
        for f in FACTORS:
            with knob(f):
                r, sc = _score()
            taus.append(round(kendall_tau(base_rank, r), 3))
            jacs.append(round(jaccard(base_rank[:top_n], r[:top_n]), 3))
            touched = max(touched, sum(1 for s, v in base_scores.items()
                                       if abs(sc.get(s, v) - v) > 1e-9))
        rank_rows.append({"constant": name, "min_tau": min(taus), "min_top_jaccard": min(jacs),
                          "sectors_moved": touched, "tau_by_factor": dict(zip(FACTORS, taus))})
    rank_rows.sort(key=lambda r: (r["min_tau"], r["min_top_jaccard"], -r["sectors_moved"]))

    # Measured twice on purpose. `skill_shrink` blends the whole conviction leg toward a neutral
    # book by λ, and λ is 0 today because the measured rank IC is noise — so a sizing constant can
    # be DECISIVE and read as inert simply because the layer it feeds is switched off. Reporting
    # only the live number would file "currently disabled by a measurement" under "does not
    # matter", which is the opposite conclusion.
    with _weighting("sharpness", 1.0, unshrunk=True):
        base_w_unshrunk = _model_weights()
    size_rows = []
    for name, key in SIZE_KNOBS.items():
        live, unshrunk = [], []
        for f in FACTORS:
            with _weighting(key, f):
                w = _model_weights()
            live.append(round(max((abs(w.get(k, 0.0) - v) for k, v in base_w.items()),
                                  default=0.0), 2))
            with _weighting(key, f, unshrunk=True):
                w2 = _model_weights()
            unshrunk.append(round(max((abs(w2.get(k, 0.0) - v) for k, v in
                                       base_w_unshrunk.items()), default=0.0), 2))
        size_rows.append({"constant": name, "max_weight_delta_pp": max(live),
                          "max_weight_delta_unshrunk_pp": max(unshrunk),
                          "delta_by_factor": dict(zip(FACTORS, live))})
    size_rows.sort(key=lambda r: -r["max_weight_delta_unshrunk_pp"])

    return {"top_n": top_n, "factors": list(FACTORS), "n_sectors": len(base_rank),
            "base_top": base_rank[:top_n], "ranking": rank_rows, "sizing": size_rows}


def render(res: dict) -> str:
    out = [f"CATALYX — constant sensitivity   {res['n_sectors']} sectors · perturbed "
           f"×{', ×'.join(str(f) for f in res['factors'])}, one at a time", ""]
    out += ["RANKING — Kendall τ vs the base order, and the top-%d set overlap. Worst case over "
            "all four\n         perturbations. τ=1.000 with Jaccard 1.000 means the constant "
            "cannot change what gets bought." % res["top_n"], ""]
    hdr = f"  {'constant':<40} {'min τ':>7} {'min top-J':>10} {'moved':>6}  verdict"
    out += [hdr, "  " + "-" * (len(hdr) - 2)]
    for r in res["ranking"]:
        inert = r["min_tau"] >= 0.999 and r["min_top_jaccard"] >= 0.999
        if "[control]" in r["constant"]:
            ok = r["sectors_moved"] > 0 and r["min_tau"] >= 0.999
            v = ("PASS — moved every score, reordered nothing (harness is sound)" if ok
                 else "FAIL — the harness itself is broken; ignore this table")
            out.append(f"  {r['constant']:<40} {r['min_tau']:>7.3f} {r['min_top_jaccard']:>10.3f} "
                       f"{r['sectors_moved']:>6}  {v}")
            continue
        if r["sectors_moved"] == 0:
            why = NOT_REACHED_WHY.get(r["constant"], "the live scoring path never consults it")
            v = f"NOT REACHED — {why}"
        elif inert:
            why = NOT_REACHED_WHY.get(r["constant"])
            v = "INERT — moves scores, never the order" + (f" ({why})" if why else "")
        elif r["min_top_jaccard"] < 0.8:
            v = "DECISIVE — evidence owed here first"
        else:
            v = "matters at the margin"
        out.append(f"  {r['constant']:<40} {r['min_tau']:>7.3f} {r['min_top_jaccard']:>10.3f} "
                   f"{r['sectors_moved']:>6}  {v}")

    out += ["", "SIZING — these cannot reorder anything, so τ would print a meaningless 1.000. "
                "Measured as the\n         largest weight move on the model book, in percentage "
                "points.", ""]
    h2 = f"  {'constant':<40} {'live Δpp':>9} {'at λ=1':>8}  verdict"
    out += [h2, "  " + "-" * (len(h2) - 2)]
    for r in res["sizing"]:
        d, u = r["max_weight_delta_pp"], r["max_weight_delta_unshrunk_pp"]
        if d < 0.01 <= u:
            v = f"SWITCHED OFF — worth {u:.1f}pp, and λ=0 spends none of it"
        elif d < 0.5:
            v = "INERT"
        elif d >= 5.0:
            v = "DECISIVE — moves real money"
        else:
            v = "matters at the margin"
        out.append(f"  {r['constant']:<40} {d:>9.2f} {u:>8.2f}  {v}")
    out += ["", "`moved` = how many sectors' composites changed AT ALL under the perturbation. "
                "It separates the\ntwo things a τ of 1.000 reports identically: a constant that "
                "shifts every score without\nreordering anything (INERT), and one the live "
                "scoring path never consults (NOT REACHED —\ne.g. a value already frozen into a "
                "stored score, or whose precondition no sector meets\ntoday). NOT REACHED is a "
                "statement about the data and the wiring, NEVER about the constant.",
           "",
           "An INERT constant is not thereby WRONG — it is un-arguable on today's universe, and "
           "the\nfinding may not survive a differently-shaped one. What this buys is knowing "
           "which debates\nare worth having: evidence is owed on the decisive rows before any "
           "other."]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Perturb each constant, measure what moves.")
    ap.add_argument("--top-n", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    res = run(args.top_n)
    print(json.dumps(res, indent=2, default=str) if args.json else render(res))


if __name__ == "__main__":
    main()
