# CATALYX — Experiments Log

Controlled stress-tests of the CATALYX pipeline. Each experiment injects a real-world
event (a catalyst, a price shock, a data outage) and measures **how the deterministic
pipeline reacts** — does it stay stable, does it respond in the right direction, and where
does it drift from reality?

The point is not to be right about the market. It is to know, *before* we trust the
pipeline with money, **which parts are resilient and which are fragile**.

## Convention

- One file per experiment: `exp_YYYY-MM-DD_<slug>.md`, dated by the **event** under test.
- Every experiment records, at minimum:
  1. **Hypothesis** — what we expect the pipeline to do.
  2. **Method** — exact catalysts added, runs compared (`run_id`s), commands.
  3. **Findings** — before/after deltas, with numbers.
  4. **Resilience scorecard** — per layer: `STABLE` / `FRAGILE` / `BLIND`.
  5. **Recommendations** — concrete changes to keep the strategy intact.
- Experiments mutate the lake (a new `score_run`), but **append-only** — every run is
  reproducible from its `run_id` + `scoring_version`. Nothing is overwritten or deleted.
- Catalysts added during an experiment are tagged `"experiment"` in `tags[]` and reference
  the experiment slug in `user_notes`, but they are **real catalysts** and stay in the
  dataset (the event genuinely happened).

## Resilience scorecard vocabulary

| Verdict | Meaning |
|---|---|
| `STABLE`   | Layer ingested the shock without breaking; output changed in the correct direction and magnitude. |
| `BLIND`    | Layer did not react at all to a shock it *should* have seen (by design or by bug). |
| `FRAGILE`  | Layer reacted, but the reaction is unreliable (silent failure, stale input, masked signal). |

## Index

| Date | Experiment | Event | Headline finding |
|---|---|---|---|
| 2026-06-05 | [exp_2026-06-05_ai_selloff](exp_2026-06-05_ai_selloff.md) | AI/semiconductor selloff (S&P −2.64%, Nasdaq −4.18%) triggered by Broadcom AI-capex guidance miss + hot jobs report | Scoring layer **STABLE** & directionally correct, but the **momentum strategy is BLIND** to a contradicting catalyst and the **momentum data layer is FRAGILE** (78% stale on the event day). All 4 strategies underperform SPY by ~2.8pts — diversification is illusory. |

## Design notes spawned by experiments

- [DESIGN — Noise vs Regime discrimination](../docs/DESIGN_catalyst_regime_discrimination.md) —
  how the pipeline should tell a transient `contradicts` event (noise, decays) apart from the
  first tremor of a structural break (regime change, permanent rotation). Three-state model
  (`intact` / `contested` / `breaking`); validated against exp_2026-06-05 (classifies `contested`).
