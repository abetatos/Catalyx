# LLM ↔ Pipeline Scoring Stability Test — 2026-06-04

**Question:** Can an LLM, scoring by free judgment from the *same source inputs*, replicate the
deterministic Python pipeline? This is a direct test of the failure mode the
`AI Scoring Stability Rules` (CLAUDE.md) exist to prevent.

- **LLM scores:** `llm_scores.json` (claude-opus-4-8, free judgment, formulas NOT executed)
- **Pipeline scores:** `after_scores.json` (sector_scorer v1 deterministic)
- **Controls:** identical frozen inputs (no live WebSearch), composite recomputed from each
  side's own dimension scores with the published `composite_weights`.

---

## 1. Composite — head to head

| Sector | LLM | Pipeline | Δ (LLM−Pipe) |
|---|---:|---:|---:|
| ai_infrastructure_data_centers | 68.4 | 75.9 | **−7.5** |
| copper_miners | 70.1 | 69.8 | +0.3 |
| eu_defense_prime_contractors | 58.7 | 52.8 | +5.9 |
| gold_miners | 56.5 | 52.0 | +4.5 |
| gold_physical | 55.8 | 49.1 | +6.7 |
| grid_infrastructure_utilities | 70.1 | 71.2 | −1.1 |

**Composite MAE = 4.3 points.** Mean signed Δ = +1.5 (LLM marginally more generous overall,
except it penalizes the crowded AI name).

## 2. Ranking — the decision-level view (Rule 4: ordinal > cardinal)

| Rank | LLM | Pipeline |
|---|---|---|
| 1 | copper_miners (70.1) | ai_infrastructure (75.9) |
| 2 | grid_infrastructure (70.1) | grid_infrastructure (71.2) |
| 3 | ai_infrastructure (68.4) | copper_miners (69.8) |
| 4 | eu_defense (58.7) | eu_defense (52.8) |
| 5 | gold_miners (56.5) | gold_miners (52.0) |
| 6 | gold_physical (55.8) | gold_physical (49.1) |

- **Top-3 set is identical** {ai, grid, copper}; only the internal order of the top cluster
  flips (the three are within 7 pts of each other on both sides — a near-tie).
- **Bottom-3 order is exactly identical**: eu_defense > gold_miners > gold_physical.
- **Spearman ρ = 0.81** (Σd² = 6.5, n = 6). Strong rank agreement.

The only real ranking disagreement is AI #1 vs #3 — driven entirely by crowding/valuation
penalties I applied that the pipeline currently does not (see §4).

## 3. Per-dimension agreement (Mean Absolute Error across 6 sectors)

| Dimension | MAE | Interpretation |
|---|---:|---|
| momentum | **19.4** | Largest gap — but it is STRUCTURAL, not noise (see §4a) |
| crowding_risk | 18.7 | Pipeline emits a constant 35; LLM differentiated (see §4b) |
| valuation_relative | 7.5 | Pipeline emits a constant 50; LLM differentiated |
| catalyst_alignment | 6.2 | Genuinely computed both sides; LLM runs systematically LOW (see §4c) |
| flow_confirmation | 3.5 | Both ~neutral 50; closest agreement |
| **composite** | **4.3** | Errors partially cancel in the weighted blend |

## 4. Where the divergence comes from — and what it means

### 4a. Momentum (MAE 19.4): an LLM cannot replicate a cross-sectional percentile
This is the headline finding. The gap is NOT bad judgment — it is a methodology the LLM
structurally cannot reproduce from the inputs it was given.

| Sector | LLM | Pipeline | Why |
|---|---:|---:|---|
| gold_physical | 40 | 2.9 | I read IGLN.L's own momentum_score (42.6). Pipeline ranks raw momentum (−4.49) against the **full 17-sector universe** where semis/solar/cyber dominate → near-zero percentile. |
| eu_defense | 38 | 8.8 | same: −4.15 raw → bottom of universe |
| gold_miners | 42 | 14.7 | same: −4.13 raw |
| copper | 80 | 67.6 | mid-pack of 17 (raw 22.4) reads lower than COPX-in-isolation |
| ai / grid | 93 / 82 | 91.2 / 73.5 | close — strong names rank high either way |

The pipeline's momentum_score is a **percentile over a universe of 17 sectors the LLM was
never asked to score.** No amount of judgment recovers a rank statistic without the full
population. → **An LLM should never be asked to produce the momentum dimension; it must be
fed the engine's percentile.** The pipeline is unambiguously correct here.

### 4b. crowding_risk (18.7) & valuation_relative (7.5): the LLM added signal the pipeline lacks
The pipeline currently emits **constants** — crowding_risk = 35 and valuation_relative = 50
for every sector (the `valuation_engine` / `crowding` inputs are not wired; passed as flat
defaults). My judgment differentiated them:
- crowding: AI **80** (most crowded theme) → gold_physical **42** (under-owned bullion)
- valuation: AI **30** (stretched +56%/6m) → eu_defense **58** (−12.6%/3m, cheaper re-entry)

So this "divergence" is the opposite of drift: it is the LLM contributing exactly the
qualitative judgment the hybrid architecture reserves for it. **Once the pipeline's flat
35/50 placeholders are replaced, this is where Claude's input should be plugged in — not
catalyst_alignment or momentum.**

### 4c. catalyst_alignment (6.2): small but SYSTEMATIC (directional) LLM bias
The LLM sat **below** the pipeline on every multi-catalyst sector (ai −5.1, copper −8.2,
grid −6.2, gold −7.1). Cause: the pipeline's **max-anchored noisy-OR** aggregation pushes
sectors with 2-3 stacked confirming catalysts to 95-97, whereas my judgment intuitively
**compressed toward 85-92** — I under-credit catalyst stacking. This is a clean example of
why the rule "compute, don't guess" exists: my bias is consistent and predictable, so the
formula is the right authority, but it also means an LLM left unchecked would systematically
under-rank the strongest multi-catalyst sectors.

## 5. Verdict

| Claim | Evidence |
|---|---|
| LLM replicates the **ranking** well | ρ = 0.81; identical top-3 set and bottom-3 order |
| LLM replicates the **composite level** decently | MAE 4.3 pts |
| LLM **cannot** replicate cross-sectional momentum | MAE 19.4, structural (needs full universe) |
| LLM has a **systematic** (not random) catalyst bias | −6.2 mean, always low on stacked catalysts |
| LLM is **additive** exactly where the pipeline is blind | crowding/valuation still constants |

**Bottom line:** The CLAUDE.md thesis holds. For *ranking decisions* the LLM is reliable
(use it as a sanity check). For the two genuinely-computed dimensions it is either
systematically biased (catalyst_alignment, −6 pts) or structurally unable to reproduce the
statistic (momentum). The LLM's real edge shows up precisely where the pipeline currently
punts (crowding_risk, valuation_relative) — which is the correct division of labor in the
permanent hybrid model.

> **Caveat on this run:** the comparison author (claude-opus-4-8) had necessarily seen
> `after_scores.json` open in the IDE before scoring. Scores were derived independently from
> source, but a truly blind re-run (fresh session, file withheld) would tighten the result.
> The systematic *direction* of the catalyst bias and the structural momentum gap are robust
> to that caveat; the exact composite MAE is the soft number.
