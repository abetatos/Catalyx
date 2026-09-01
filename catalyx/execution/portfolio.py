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
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from catalyx.config import weights as weights_cfg
from catalyx.store import lake

_REPO_ROOT = Path(__file__).parents[2]
_PROFILES_DIR = _REPO_ROOT / "catalyx" / "config" / "portfolios"
_HOLDING_TABLE = "portfolio_holding"
_EXPOSURE_TABLE = "portfolio_catalyst_exposure"
# The model book is sizeless; to record a € exposure per catalyst we assume a fixed notional
# divided across the holdings (the user's "asume €1000 repartidos entre todos"). The PCT is the
# real quantity — eur is purely pct × notional for a tangible read.
_NOTIONAL_EUR = 1000.0
_UNCATALYZED = "uncatalyzed"


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


def _composite_floor(c: dict) -> float:
    """The selection floor as a composite level, from the profile's `min_composite_z`.

    v6 H3: `min_composite: 55` was a LEVEL applied to a semi-relative composite — momentum
    is a percentile (mean 50 by construction) while catalyst_alignment drifts with the
    catalyst cycle, so what "55" excluded changed run to run. The floor is now expressed in
    the units the composite is built from (`50 + z_scale·z`), where 0.0 means "the universe
    average of this run". The pre-v6 key is read for one major version, translated through
    the same map so an unmigrated profile keeps its stance.
    """
    return weights_cfg.composite_floor(c, "min_composite_z", "min_composite")


def _apply_composite_floor(df, c: dict):
    """Filter a run's rows by the selection floor, in the units THAT RUN was scored in.

    A pre-v6 run holds absolute-level composites and no `composite_z`; comparing them
    against a z-derived floor would compare two different scales and admit almost the whole
    universe. So a run that carries `composite_z` is filtered on it; an older one falls back
    to its own absolute floor.
    """
    if "composite_z" in df.columns and df["composite_z"].notna().any():
        z_floor = (_composite_floor(c) - 50.0) / float(
            weights_cfg.composite_scale().get("z_scale", 15.0))
        return df[df["composite_z"] >= z_floor]
    return df[df["composite"] >= float(c.get("min_composite", 50.0))]


# ── The conviction gate — an ABSOLUTE floor beside the relative one (v10 P1) ──

def conviction_gate(df, c: dict) -> tuple:
    """Names that fail an absolute standard, whatever the rest of the universe did.

    WHY THIS IS NOT THE COMPOSITE FLOOR. `min_composite_z` is RELATIVE by construction: 0.0
    means "above this run's own average", so in a universe where every driver is fading it
    still admits half the field, and the book stays full because the book is always full. The
    user's criterion is the other one — "if a sector stops being interesting under its
    catalysts or its risk, drop it" — and that question cannot be asked in z-units, because a
    z-score has no opinion about whether the thing it ranks is worth owning at all.

    So the gate reads `catalyst_alignment`, which IS an absolute level in [0,100] and carries
    the largest weight in the composite (0.35). The floor is read off the measured distribution
    rather than chosen: on run_20260831_184616 the 26 CA values are bimodal — twenty sit
    between 71.5 and 96.2, then a 25-point gap, then 36.2 / 13.9 / 13.9 / 0.0, which are the
    sectors carrying no live structural driver at all. A floor of 40 sits in that gap. It
    changes NOTHING about today's book (all four excluded names rank 17th or worse) and that is
    the point: it encodes the criterion without silently re-cutting a table the user already
    decided on. What it stops is the case that has never yet happened and would not be caught —
    `eu_retail_banking` at momentum 86.5 with CA 13.9, one good quarter away from `buy_if`'s
    rank-8 ceiling, bought on price alone with no driver behind it.

    WHAT THE GATE REFUSES TO JUDGE. An IMPUTED catalyst_alignment (`ca_imputed`, v6 H2 — no
    study, so CA was set to the universe prior rather than to zero) is NOT evaluated: it passes
    and is reported as unevaluated. Gating on it would convert "we have not measured this" into
    "this fails", which is the one inference v5/v6 spent two versions removing from this
    pipeline. A MEASURED zero is different and is gated: `infrastructure_core` scores CA 0.0
    with a study behind it, and that zero was observed.

    A dropped name is not replaced by a lower-ranked one out of politeness: top-N runs over the
    survivors, so the next eligible name does come in. Cash appears only when FEWER THAN
    `max_positions` names clear the gate — which is exactly the state the user asked to be
    representable, and which the book previously had no way to express.

    Returns `(kept_df, excluded_rows)`; `excluded_rows` is what the caller reports.
    """
    g = c.get("entry_gate") or {}
    if not g or not bool(g.get("enabled", True)) or df.empty:
        return df, []
    min_ca = g.get("min_catalyst_alignment")
    if min_ca is None or "catalyst_alignment" not in df.columns:
        return df, []
    min_ca = float(min_ca)

    imputed = (df["ca_imputed"].fillna(False).astype(bool) if "ca_imputed" in df.columns
               else df["catalyst_alignment"].notna() & False)
    ca = df["catalyst_alignment"]
    fails = (~imputed) & ca.notna() & (ca < min_ca)

    excluded = [{"sector_id": str(r["sector_id"]),
                 "catalyst_alignment": round(float(r["catalyst_alignment"]), 1),
                 "min_catalyst_alignment": min_ca,
                 "reason": (f"catalyst_alignment {float(r['catalyst_alignment']):.1f} < "
                            f"{min_ca:.0f} — no live driver behind the name")}
                for _, r in df[fails].iterrows()]
    return df[~fails], excluded


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


def conviction_transform(raw: list[float], transform: str, sharpness: float) -> list[float]:
    """Map raw ranking scores → relative weighting scores BEFORE the water_fill cap.

    Separates the magnitude shape from the selection signal so a narrow, high score band
    (e.g. composites 65–74) still produces dispersed weights instead of near-equal ones.

      proportional → returns `raw` unchanged (weight ∝ raw).
      softmax      → z = (raw − mean)/std ; returns exp(sharpness · z). The z-NORMALIZATION
                     makes `sharpness` mean "std-devs of tilt", so the dispersion keeps its
                     meaning even if the band compresses next run (a raw-score softmax would
                     silently change). Monotonic → never reorders the ranking. std≈0 (all
                     scores equal) → falls back to equal (returns all 1.0).
    """
    import math

    n = len(raw)
    if n == 0:
        return []
    if transform != "softmax":
        return list(raw)
    mean = sum(raw) / n
    var = sum((x - mean) ** 2 for x in raw) / n
    std = math.sqrt(var)
    if std < 1e-9:
        return [1.0] * n
    return [math.exp(sharpness * (x - mean) / std) for x in raw]


def skill_shrink(model: list[float], neutral: list[float], lam: float) -> list[float]:
    """Blend the model's conviction tilt toward a neutral book by λ (plan v4 §3 B1).

        w_final ∝ neutral + λ · (model − neutral)

    Two decisions were fused in one number. **How much is at work** is beta, justified by the
    equity risk premium and not by this model's skill — that is `deploy_ratio` and it is not
    touched here: both legs carry the same names at the same gross, so λ=0 deploys exactly as
    much as λ=1. **How the working capital is tilted** is alpha, and its only justification is
    the measured rank IC of the ranking doing the tilting. Today that IC is −0.05 against an
    se of 0.20 on one non-overlapping window, and the softmax was dispersing weights just as
    aggressively as it would on an IC of +0.4.

    λ=0 is not "no model": the model still chose the names, the filters and the cap. It is the
    model declining to also size them until the ordering has earned it. λ=1 is the pre-v4
    behaviour byte-for-byte.

    Both legs are normalized before blending so λ is a true mix and not a function of whatever
    scale `conviction_transform` happened to return; the result is rescaled to the model's
    total, which `water_fill` ignores but a human reading the intermediate does not.
    """
    n = len(model)
    if n == 0 or lam >= 1.0:
        return list(model)
    lam = max(0.0, float(lam))
    tm, tn = sum(model), sum(neutral)
    if tm <= 0 or tn <= 0:
        return list(model)
    out = [(nv / tn) + lam * ((mv / tm) - (nv / tn)) for mv, nv in zip(model, neutral)]
    tot = sum(out)
    return [x * tm / tot for x in out] if tot else list(model)


def vol_tilt(scores: list[float], vols_pct: list[float | None], alpha: float,
             min_vol_pct: float = 5.0) -> list[float]:
    """Divide the weighting scores by `σ^alpha` — risk-budgeted sizing (plan v4 §2 A4).

    The composite decides WHAT to own and with how much conviction; before this, the euro amount
    then ignored the only input that makes two euros comparable. On the 2026-08-28 book
    `semiconductors_design` (vol 55%) and `pharma_large_cap` (vol 18%) were sized as the same bet,
    and semis carried ~3x the risk per euro spent.

    `alpha = 0` is the old behaviour exactly (returns `scores` untouched, so the default is a
    no-op until a book opts in). `alpha = 1` is full inverse-vol, which systematically underweights
    precisely the high-beta sectors a catalyst-driven mandate exists to own — hence 0.5, which
    halves the risk dispersion without turning a momentum book into a low-vol fund.

    A MISSING vol takes the median of the ones present, never a zero or a one: an unknown must not
    divide into an infinite weight, and it must not be silently treated as risk-free either. The
    floor `min_vol_pct` does the same job for a stale or flat series.
    """
    if not scores or alpha <= 0:
        return list(scores)
    known = sorted(float(v) for v in vols_pct if v)
    if not known:
        return list(scores)
    mid = len(known) // 2
    median = known[mid] if len(known) % 2 else (known[mid - 1] + known[mid]) / 2.0
    out = []
    for sc, v in zip(scores, vols_pct):
        sigma = max(float(v) if v else median, float(min_vol_pct))
        out.append(float(sc) / (sigma ** alpha))
    # Rescale to the original total so the numbers stay on a familiar scale; water_fill only
    # cares about ratios, but a legible intermediate is worth the one division.
    tot_in, tot_out = sum(scores), sum(out)
    return [x * tot_in / tot_out for x in out] if tot_out else out


def _sector_vols(tickers: list[str], lookback_days: int, as_of: str | None = None,
                 price_fn=None) -> dict[str, float]:
    """Annualized vol per traded vehicle over a COMMON recent window. {} when unavailable.

    Reads the shared price cache the run already warmed — no extra fetch — and fails soft: a
    ticker with no usable history simply does not appear, and `vol_tilt` substitutes the median.
    """
    from datetime import timedelta

    tickers = [t for t in dict.fromkeys(tickers) if t]
    if not tickers:
        return {}
    end = date.fromisoformat(as_of) if as_of else date.today()
    start = end - timedelta(days=int(lookback_days * 1.6) + 10)   # calendar → ~lookback trading
    try:
        from catalyx.data import prices
        # `allow_fetch=False` on purpose: the portfolio builder is not a fetch site. `pre_run.sh`
        # warms the cache once per run and everything downstream reads it, so a cold ticker here
        # drops to the median vol rather than opening a network round-trip inside a weighting loop.
        frame = (price_fn(tickers, start.isoformat(), end.isoformat()) if price_fn
                 else prices.read(tickers, start.isoformat(), end.isoformat(), allow_fetch=False))
    except Exception:
        return {}
    if frame is None or getattr(frame, "empty", True):
        return {}
    out = {}
    for t in tickers:
        if t not in frame.columns:
            continue
        col = [float(v) for v in frame[t].dropna()][-lookback_days:]
        if len(col) < 30:
            continue
        rets = [col[i] / col[i - 1] - 1.0 for i in range(1, len(col)) if col[i - 1]]
        if len(rets) < 20:
            continue
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        out[t] = round((var ** 0.5) * (252 ** 0.5) * 100.0, 2)
    return out


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


def _held_weights(portfolio_id: str, before_run_id: str, lake_dir: Path | None = None) -> dict:
    """{sector_id: weight_pct} of the most recent holdings strictly BEFORE `before_run_id`
    for this portfolio. The held book the deadband compares against. {} if none."""
    df = lake.read_table(_HOLDING_TABLE, lake_dir=lake_dir)
    if df.empty or "portfolio_id" not in df.columns:
        return {}
    df = df[(df["portfolio_id"] == portfolio_id) & (df["run_id"] < before_run_id)]
    if df.empty:
        return {}
    prev = max(df["run_id"].dropna().unique())
    df = df[df["run_id"] == prev]
    return {r["sector_id"]: float(r["weight_pct"]) for _, r in df.iterrows()}


def apply_deadband(targets: list[float], held: list[float | None], deadband_pct: float,
                   max_position_pct: float | None = None) -> list[float]:
    """Turnover guard: where a target weight is within `deadband_pct` points of the weight
    already held, keep the held weight (no trade); otherwise take the target. Suppresses
    tax-churn from tiny score wiggles. `held[i]` is None for a position not previously held
    (no deadband → take target). `deadband_pct` ≤ 0 disables.

    v6 J2: the renormalization used to multiply EVERY weight, kept ones included, so it moved
    the positions it had just decided not to move — reintroducing the micro-trades the band
    exists to suppress — and it could push a weight past `max_position_pct`, which `water_fill`
    had already applied. The residual is now absorbed by the FREE positions only; a kept weight
    is returned exactly as held.
    """
    if deadband_pct <= 0:
        return list(targets)
    total = sum(targets)
    is_kept = [h is not None and abs(t - h) < deadband_pct for t, h in zip(targets, held)]
    out = [float(h) if k else float(t) for t, h, k in zip(targets, held, is_kept)]

    free_total = sum(t for t, k in zip(targets, is_kept) if not k)
    residual = total - sum(o for o, k in zip(out, is_kept) if k)
    if free_total > 1e-9 and residual > 0:
        s = residual / free_total
        out = [o if k else t * s for o, t, k in zip(out, targets, is_kept)]
    # No free position to absorb it → the difference is cash, which is what the band means.
    # residual ≤ 0 (the kept alone already fill the gross) → the free names take their targets
    # unchanged: paying for a decision NOT to trade by forcing a trade elsewhere is precisely
    # the trade the band exists to prevent. Bounded by n_kept × deadband, and capped below.

    gross = sum(out)
    if gross > 100.0:            # not a preference — a book cannot hold more than it has
        out = [o * 100.0 / gross for o in out]
    if max_position_pct is not None:
        # the cap is a risk limit and outranks the band; the freed weight stays as cash rather
        # than being redistributed, exactly as the contested haircut does
        out = [min(o, float(max_position_pct)) for o in out]
    return out


def _sector_catalyst_map() -> dict[str, list[str]]:
    """{sector_id: [catalyst_id, …]} from the Tier-1 sector studies' `active_catalyst_ids`.
    Read at build time so the decomposition captures the catalyst→sector mapping point-in-time."""
    from catalyx.store import sector_study_repo as ssr
    return {s["sector_id"]: list(s.get("active_catalyst_ids") or []) for s in ssr._load_all()}


def catalyst_exposure_rows(portfolio_id: str, run_id: str, holdings: list[dict],
                           built_at, sector_catalysts: dict[str, list[str]] | None = None,
                           notional: float = _NOTIONAL_EUR) -> list[dict]:
    """Decompose a portfolio's weights per catalyst, in BOTH senses — the model book carries
    the same two columns the real book got in v5.2, because they answer different questions.

      `pct_credit`   — the weight split equally across the catalysts driving the sector. This
                       is P&L CREDIT: who gets credited with the return. Partitions the book,
                       so credit + uncatalyzed + the cash remainder sums to 100.
      `pct_exposure` — the FULL position behind every driver it names. This is RISK: how much
                       money moves if this driver breaks. Nobody owns 30% of an ETF, so the
                       rows sum to MORE than the book, on purpose. This is what a correlated-
                       catalyst cap must read; feeding it the split also inverts the incentive,
                       since declaring a second driver would buy headroom for free.

    `pct`/`eur` stay as the credit split for pre-v6 readers (deprecated — migrate to the column
    your question needs). One row per (portfolio, run, catalyst), tracked across rebalances."""
    smap = _sector_catalyst_map() if sector_catalysts is None else sector_catalysts
    credit: dict[str, float] = {}
    exposure: dict[str, float] = {}
    for h in holdings:
        w = float(h.get("weight_pct") or 0.0)
        if w <= 0:
            continue
        cats = smap.get(h["sector_id"]) or []
        if cats:
            share = w / len(cats)                       # split equally across the sector's catalysts
            for cid in cats:
                credit[cid] = credit.get(cid, 0.0) + share
                exposure[cid] = exposure.get(cid, 0.0) + w      # the WHOLE position, per driver
        else:
            credit[_UNCATALYZED] = credit.get(_UNCATALYZED, 0.0) + w
            exposure[_UNCATALYZED] = exposure.get(_UNCATALYZED, 0.0) + w
    return [{
        "portfolio_id": portfolio_id, "run_id": run_id, "catalyst_id": cid,
        "pct": round(pct, 2), "eur": round(pct / 100.0 * notional, 2),
        "pct_credit": round(pct, 2), "credit_eur": round(pct / 100.0 * notional, 2),
        "pct_exposure": round(exposure[cid], 2),
        "exposure_eur": round(exposure[cid] / 100.0 * notional, 2),
        "notional_eur": notional, "built_at": built_at,
    } for cid, pct in sorted(credit.items(), key=lambda kv: kv[1], reverse=True)]


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
    df = _apply_composite_floor(df, c)
    # The ABSOLUTE gate runs beside the relative floor, never instead of it: one asks "is this
    # above average today", the other "is this worth owning at all". See `conviction_gate`.
    df, gate_excluded = conviction_gate(df, c)
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
    # `composite` is rounded to 1dp for display; the top-N cut is where that rounding costs
    # money — two sectors 0.05 apart at the `max_positions` boundary are a tie broken by row
    # order. Rank on `composite_z` when the run carries it for every sector (the same
    # fallback rule as `_apply_composite_floor`). See `sector_scorer.rank_key`.
    if rank_col == "composite" and "composite_z" in df.columns and df["composite_z"].notna().all():
        rank_col = "composite_z"
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
    # conviction sizing: shape the raw ranking scores BEFORE the cap, so a narrow score band
    # (e.g. composites 65–74) still produces dispersed weights. `equal` keeps flat scores (std≈0
    # → softmax falls back to equal anyway). Per-book override of the global portfolio_weighting.
    pw = weights_cfg.portfolio_weighting()
    transform = str(c.get("weighting_transform", pw.get("transform", "proportional")))
    sharpness = float(c.get("sharpness", pw.get("sharpness", 0.25)))
    deadband = float(c.get("rebalance_deadband_pct", pw.get("rebalance_deadband_pct", 0.0)))
    if weighting != "equal":
        scores = conviction_transform(scores, transform, sharpness)
    # SKILL SHRINKAGE: how far the tilt is allowed to depart from neutral is set by the measured
    # rank IC of the very column doing the ranking, not assumed. The neutral leg carries the
    # regime haircut, so a contested sector stays de-risked even at λ=0 — the overlay is a risk
    # statement, not a conviction one, and only the conviction leg is being shrunk.
    lam_info = None
    if weighting != "equal" and bool(c.get("tilt_shrinkage", pw.get("tilt_shrinkage", False))):
        from catalyx.scorer import calibration
        lam_info = calibration.skill_lambda(
            lake_dir=lake_dir, dimension=("momentum" if weighting == "momentum" else "composite"),
            ic_target=float(c.get("tilt_ic_target", pw.get("tilt_ic_target", 0.20))),
            prior_windows=float(c.get("tilt_prior_windows", pw.get("tilt_prior_windows", 3.0))),
            floor=float(c.get("tilt_lambda_floor", pw.get("tilt_lambda_floor", 0.0))))
        neutral = [(1.0 - contested_haircut) if (veto_on and contested_action == "redistribute"
                                                and st == "contested") else 1.0
                   for st in states]
        scores = skill_shrink(scores, neutral, lam_info["lambda"])
    # RISK BUDGET: divide by σ^alpha before the cap, so `max_position_pct` still means what it
    # says and the deadband is untouched. alpha 0 (the default) leaves `scores` identical.
    alpha = float(c.get("vol_tilt_alpha", pw.get("vol_tilt_alpha", 0.0)))
    vols_used: dict[str, float] = {}
    if alpha > 0:
        vols_used = _sector_vols(
            [str(r.get("primary_etf") or "") for _, r in df.iterrows()],
            int(c.get("vol_lookback_days", pw.get("vol_lookback_days", 120))))
        scores = vol_tilt(scores, [vols_used.get(str(r.get("primary_etf") or ""))
                                   for _, r in df.iterrows()],
                          alpha, float(c.get("min_vol_pct", pw.get("min_vol_pct", 5.0))))
    weights = water_fill(scores, float(c["max_position_pct"]) / 100.0)
    if veto_on and contested_action == "cash":
        # gross-down: trim contested FINAL weights; the freed weight stays as cash (not
        # redistributed). exp_2026-06-05 A/B: this is what actually cuts a broad-risk-off
        # drawdown — redistribution merely reshuffles within a correlated momentum cluster.
        weights = [w * (1.0 - contested_haircut) if st == "contested" else w
                   for w, st in zip(weights, states)]

    # turnover guard: keep weights within `deadband` points of what's already held (prev run),
    # so tiny score wiggles don't trigger taxable rebalances. No prior run → no-op.
    if deadband > 0:
        held_map = _held_weights(portfolio_id, run_id, lake_dir=lake_dir)
        if held_map:
            held = [held_map.get(r["sector_id"]) for _, r in df.iterrows()]
            pct = apply_deadband([w * 100.0 for w in weights], held, deadband,
                                 max_position_pct=float(c["max_position_pct"]))
            weights = [p / 100.0 for p in pct]

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
            # carried so downstream re-rankings (rebalance re-ranks the surviving book) sort
            # on the same unit the selection did, not on the rounded display number
            "composite_z": (None if r.get("composite_z") is None or r.get("composite_z") != r.get("composite_z")
                            else float(r["composite_z"])),
            "momentum": float(r["momentum"]),
            "crowding_risk": float(r["crowding_risk"]),
            "narrative_maturity": r.get("narrative_maturity"),
            "regime_state": r.get("regime_state", "intact"),
            "weight_pct": round(w * 100.0, 2),
            "tilt_lambda": (lam_info or {}).get("lambda"),
            "entry_price": entry_prices.get(r["sector_id"]),
            "built_at": built_at,
        })

    cash_pct = round(100.0 - sum(x["weight_pct"] for x in rows), 2)
    n_contested = sum(1 for x in rows if x.get("regime_state") == "contested")

    # catalyst decomposition of the notional book (recorded at this rebalance for the time-series)
    exposure = catalyst_exposure_rows(portfolio_id, run_id, rows, built_at)
    if cash_pct > 0.01:                                  # the un-deployed remainder is honest cash
        cash_eur = round(cash_pct / 100.0 * _NOTIONAL_EUR, 2)
        # cash has one driver and one owner, so credit and exposure coincide
        exposure.append({"portfolio_id": portfolio_id, "run_id": run_id, "catalyst_id": "cash",
                         "pct": cash_pct, "eur": cash_eur,
                         "pct_credit": cash_pct, "credit_eur": cash_eur,
                         "pct_exposure": cash_pct, "exposure_eur": cash_eur,
                         "notional_eur": _NOTIONAL_EUR, "built_at": built_at})

    if persist:
        import pandas as pd
        lake.append_partition(_HOLDING_TABLE, pd.DataFrame(rows),
                              {"portfolio_id": portfolio_id, "run_id": run_id},
                              overwrite=True, lake_dir=lake_dir)
        if exposure:
            lake.append_partition(_EXPOSURE_TABLE, pd.DataFrame(exposure),
                                  {"portfolio_id": portfolio_id, "run_id": run_id},
                                  overwrite=True, lake_dir=lake_dir)

    # WHY THE GATE'S CASH IS REPORTED SEPARATELY (v10 P2). `cash_pct` is the residue of three
    # very different things: the per-position cap, a contested haircut, and — now — names the
    # conviction gate deliberately refused. Only the last is a DECISION about where not to
    # invest, and `rebalance.close_target_weights` must not rescale it away as if it were weight
    # lost to an unbuyable vehicle. Downstream reads `n_eligible` to know how much of the book
    # the model could fill at all, which is what the deployment shortfall is then measured
    # against — see `rebalance.absorbable_eur`.
    return {"portfolio_id": portfolio_id, "run_id": run_id, "config_version": cfg_ver,
            "positions": len(rows), "cash_pct": cash_pct, "overlay": risk_overlay,
            "contested": n_contested, "tilt": lam_info,
            "conviction_gate": {
                "enabled": bool((c.get("entry_gate") or {}).get("enabled", True)
                                and (c.get("entry_gate") or {}).get("min_catalyst_alignment")
                                is not None),
                "min_catalyst_alignment": (c.get("entry_gate") or {}).get(
                    "min_catalyst_alignment"),
                "excluded": gate_excluded,
                "n_excluded": len(gate_excluded),
                "n_eligible": len(rows),
                "max_positions": int(c["max_positions"]),
                # The book is SHORT of its own target because too few names cleared an absolute
                # standard — not because capital was mislaid. This is the state the user asked
                # to be representable, and it is the one the shortfall rule must not punish.
                "short_of_target": len(rows) < int(c["max_positions"]),
                "note": (f"{len(rows)} of {c['max_positions']} slots filled; "
                         f"{len(gate_excluded)} name(s) failed the absolute conviction floor"
                         + (": " + ", ".join(e["sector_id"] for e in gate_excluded)
                            if gate_excluded else "")),
            },
            "holdings": rows, "catalyst_exposure": exposure}


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
    tilt = res.get("tilt")
    if tilt:
        print(f"    tilt λ={tilt['lambda']:.2f}  ({tilt['note'].split('— ', 1)[-1]})"
              + ("  → neutral book: the model picks the names, not the sizes"
                 if tilt["lambda"] < 0.05 else ""))
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
