# DESIGN — Noise vs Regime: discriminating a transient event from a structural break

> **Status:** prototype implemented + validated (state signal live; veto/#3 still pending — see §7)
> **Motivated by:** [experiments/exp_2026-06-05_ai_selloff.md](../experiments/exp_2026-06-05_ai_selloff.md)
> **Touches:** `catalyst_scorer`, `intensity_engine`, `snapshot_repo`, portfolio construction,
> the planned `structural_monitor` / `invalidation_watcher` modules.

## 1. The problem

A single schema object — an `EventCatalyst` with `relation_to_structural: "contradicts"` — can
mean two opposite things:

- **(a) Noise.** A one-off shock (Broadcom's AI-chip guidance miss + a hot jobs print) that moved
  *sentiment* but not *fundamentals*. It should **decay** and leave the strategy intact.
- **(b) Regime change.** The first tremor of the `struct_ai_capex_supercycle` actually rolling
  over. It should trigger a **permanent** rotation, not a temporary haircut.

You cannot tell which on day 0. The pipeline must therefore not *classify* at detection time — it
must **resolve over time**, and it must act differently in the meantime depending on which way the
evidence is leaning. This document defines how.

The veto from the experiment (recommendation #2) is necessary but not sufficient: a smarter veto
still cannot, by itself, distinguish (a) from (b). The discrimination has to come from a *second
gate* — the structural's own indicators — not from making the event signal cleverer.

## 2. The principle the pipeline already encodes

CATALYX already separates the two signals by **where they live**, and this is the whole basis of
the solution:

| Signal | Where it lives | Behaviour | Reads |
|---|---|---|---|
| **Event** (`contradicts`) | `catalyst_scorer` — modifies the structural's *modified_score* in the alignment calc | **decays** (exp half-life) | market *opinion* |
| **Structural health** | `intensity.current_score` — computed by `intensity_engine` from `indicators[]` | **persists** (no decay) | *fundamentals* |

Verified in code: a `contradicts` event **does not touch** `intensity.current_score`. It only
lowers the alignment's `modified_score` (`catalyst_scorer._apply_contradicts`). The structural's
measured health is an independent channel driven by `indicators[]`.

> **Opinion decays; fundamentals persist. Noise moves opinion without moving fundamentals; a
> regime change moves both. The discriminator is whether the indicators corroborate the event.**

So we do not need a new "is this structural?" classifier. We need a **bridge** (event → re-check
the indicators) and an **escalation rule** (corroboration over time → downgrade the structural).

## 3. The model — three states, driven by two clocks

Two clocks run against each other:

```
  The EVENT decays at half-life λ.                     (transient pressure, fades)
  CORROBORATION accumulates:                           (persistent pressure, compounds)
    · indicators of the structural degrading
    · repeated independent contradicting events
    · a deactivation_condition tripping

  decay wins        → it was NOISE      → strategy returns to intact on its own
  accumulation wins → it is a REGIME    → permanent rotation + thesis review
```

### Per-structural state

Each structural catalyst carries a `regime_state ∈ {intact, contested, breaking}`:

| State | Condition | Meaning |
|---|---|---|
| `intact` | no live contradicting event, indicators healthy | thesis fully on |
| `contested` | a **live** contradict (decayed strength ≥ τ_evt) **AND** indicators still healthy | opinion turned, fundamentals haven't — **default for a fresh contradict** |
| `breaking` | indicators degrading **OR** deactivation_condition tripped **OR** ≥2 independent contradicts before the first decays | fundamentals turning — regime change |

Crucially, `breaking` is **not** reachable by a single event, no matter how strong. A lone
contradict can only ever produce `contested`. That is the structural guarantee that *"un evento
puntual no mete ruido"*: by construction, one event decays out.

### Per-sector state (what the strategy consumes)

A sector inherits the **worst state among the structurals that *materially drive its alignment***
— weighted by exposure, not a flat OR. A structural is a "material driver" if it is the
max-anchor of the sector's noisy-OR alignment, or contributes above a share threshold to it.

This is what makes the heterogeneity in the experiment *correct rather than accidental*:

- `semiconductors_memory`: only structural is `struct_ai_capex_supercycle` → it **is** the driver
  → sector state = state of that structural.
- `ai_infrastructure_data_centers`: alignment is anchored by `struct_energy_transition_grid`
  (95.0, `intact`); `struct_ai_capex_supercycle` is present but **not the anchor** → the
  contradict barely drives this sector → it stays `intact`/lightly `contested`.

(The exposure-weighting is the same plumbing as recommendation #3 — see §7.)

## 4. The escalation rule (concrete thresholds — to be calibrated)

```
on a contradict event E hitting structural S:
    S.state := contested                       if decayed_strength(E) ≥ τ_evt   (default τ_evt = 50)
    trigger monitor(S):                         # the bridge — do NOT wait for the monthly scan
        refresh S.indicators[]  (best-effort; some are quarterly — see §6)
        recompute intensity via intensity_engine
        evaluate S.deactivation_conditions[] against current data

escalate S.state := breaking  if ANY:
    (i)   ≥ N_ind of S.indicators cross below threshold_weak   (default N_ind = 2 of 3)
          OR intensity.current_score falls > Δ_int over one update cycle (default Δ_int = 15)
    (ii)  a deactivation_condition evaluates true
    (iii) a 2nd independent contradicting event arrives while E is still ≥ 50% strength

de-escalate:
    contested → intact   when E decays below τ_evt AND indicators still healthy
    breaking  → (manual)  a regime exit is a judgement call → flag for the Claude review step,
                          never auto-revert
```

The asymmetry is deliberate: **entering `contested` is automatic and cheap; entering `breaking`
is hard and requires fundamental corroboration; leaving `breaking` requires a human (the skill).**
False positives in `contested` cost little and self-correct; false positives in `breaking` are
expensive, so the bar is high and the indicators (not the headline) set it.

## 5. How the strategy acts on each state — REVISED (the system recommends, it does not trade)

The first draft had `contested` trigger an automatic haircut. **The exp_2026-06-05 A/B killed that
idea**, and the project's objective (monthly cadence, conviction — not active trading) seals it:

| Variant on `contested` | Selloff drawdown vs SPY | 180d edge vs SPY |
|---|---|---|
| do nothing (flag-only) | −3.46 (baseline) | +26.93 (baseline) |
| haircut → redistribute | −3.27 (**+0.19**) | +25.46 (**−1.47**) |
| haircut → cash (gross-down) | −2.30 (+1.16) | +19.97 (**−6.96**) |

Acting on a `contested` (one-off, possibly-reverting — *"el lunes podría subir"*) signal barely
helps the drawdown and costs real edge; redistribution is near-useless because the whole momentum
book is one correlated bet (you reshuffle a falling cluster). So the converged design is:

> **The system RECOMMENDS, it does not trade. It reacts to PERSISTENCE, not to the event. And it
> rotates toward what is NOT correlated.**

| State | Action | Who acts | Default |
|---|---|---|---|
| `intact` | full weight | — | — |
| `contested` | **flag only** — carried onto the holdings + heatmap for the monthly review. **No weight change.** | nobody (watch) | inert (`contested_haircut: 0`) |
| `breaking` | **recommend** a rotation toward low-correlation sectors; surface a thesis-review flag; downgrade the structural intensity | the human, at the monthly review | recommend, not auto-exclude (`exclude_breaking: false`) |

The haircut/exclude machinery remains in `portfolio.py` as **opt-in** (profile YAML
`risk_overlay:`), for anyone who wants the active version — but it is OFF by default. Resilience is
achieved by *not acting on noise*; the value is the signal feeding human judgement, not an auto-trade.

## 6. The hard part: cadence mismatch

The cleanest discriminators are the slowest:

| Discriminator | Latency | Noise |
|---|---|---|
| hyperscaler capex guidance (`ind_01`) | **quarterly** | low |
| Nvidia DC revenue (`ind_02`) | quarterly | low |
| IEA power demand (`ind_03`) | quarterly | low |
| price / breadth / the event itself | intraday | **high** |

So in the window between "event fires" and "next quarterly print", you are *forced* to act on the
fast, noisy signal. This is **why `contested` (reversible haircut) is the correct default** and
why `breaking` must wait for corroboration. It also implies two pipeline requirements:

1. The monitor must be **event-triggered**, not tied to the monthly review — the experiment's
   event was even detected a day late (`detected_at` 2026-06-06 for a 2026-06-05 event). Waiting
   for the first Monday of the month is too slow.
2. We may want a **fast interim indicator** (e.g. relative-strength break of the sector ETF vs the
   structural's basket, or options-implied skew) as an *early, downweighted* corroboration input —
   explicitly marked low-confidence so it can nudge toward `breaking` but not commit alone.

## 7. Where each piece lives (implementation surface)

| Change | File | Type |
|---|---|---|
| Expose per-structural `relation`, `decayed_strength`, and `regime_state` (not just the aggregate number) | `catalyx/scorer/catalyst_scorer.py` | additive to the result dict |
| Exposure-weighted sector state from its structurals (reuses the noisy-OR contribution shares) | `catalyx/scorer/catalyst_scorer.py` | new helper (shared with rec #3) |
| Indicator-degradation + deactivation-condition evaluation on trigger | new `catalyx/thesis/structural_monitor.py` | the "bridge" (currently *planned* in CLAUDE.md) |
| Persist `regime_state` per sector | `catalyx/store/snapshot_repo.py` → `sector_snapshot` table | additive column (no schema break) |
| Consume state in construction (haircut on `contested`, exclude on `breaking`) | `catalyx/execution/portfolio.py` | new post-weighting step |
| Penalty-aware aggregation (rec #3) so `breaking` actually lowers `catalyst_alignment` | `catalyx/scorer/catalyst_scorer.py::_aggregate_alignment` | **core formula change** → bumps `scoring_version` |

Note the dependency chain from the previous discussion holds: the per-sector contradiction signal
(#2 plumbing) is the prerequisite; the penalty-aware aggregation (#3) is the same signal applied
to the score, and is the only piece that touches the core formula — do it last, once the state
model has proven out.

## 8. Validation against exp_2026-06-05 (computed from current data)

Does the model classify the AI selloff as `contested` (noise) — i.e. would the pipeline correctly
*not* permanently rotate out of AI on one Broadcom miss? Checking `struct_ai_capex_supercycle`
against the escalation rule with **today's** values:

| Escalation test | Current value | Trips `breaking`? |
|---|---|---|
| `ind_01` capex guidance vs `threshold_weak=40` | 87 (score 92.2 🟢) | no |
| `ind_02` Nvidia DC rev YoY vs `threshold_weak=0.00` | +0.22 (score 85.5 🟢) | no |
| `ind_03` IEA power demand vs `threshold_weak=0.05` | +0.28 (score 91.9 🟢) | no |
| ≥2 of 3 indicators below weak? | 0 of 3 | **no** |
| intensity drop > 15 in a cycle? | 89.9 → 86.9 (−3.0) | **no** |
| `deact_01`: capex guidance −30% YoY for 2 quarters? | guidance rising | **no** |
| 2nd independent contradict before E decays? | none (single event) | **no** |

**Result: `contested`, not `breaking`.** → temporary haircut on the pure-play semis, auto-unwinds
over the 30-day half-life. The fundamentals (all three indicators 🟢, intensity ~87, guidance
rising) do not corroborate the price action, so the pipeline treats it as noise — exactly the
desired behaviour. The intensity *did* tick down 89.9→86.9, which is the system already registering
mild pressure without overreacting; the escalation threshold (Δ_int > 15) correctly does not fire.

**The counterfactual that *would* be `breaking`:** if next quarter `ind_01` came in with hyperscaler
capex guidance cut and `ind_02` decelerating below 0, two indicators cross weak → escalate →
intensity downgraded, `struct_ai_capex_supercycle` flagged for deactivation review → permanent
rotation, and likely a new `StructuralCatalyst` for the rotation-into-defensives regime. The event
was the spark; the structural is *deactivated or created*, never "promoted from the event".

### 8.1 Live prototype result (run `run_20260606_114437`)

The state signal is implemented (`catalyx/thesis/structural_monitor.py` + `regime_state` emitted by
`catalyst_scorer` and persisted in the lake `sector_snapshot`). On a real scoring run over 53
sectors with the AI selloff catalyst active:

```
intact     46
contested   7   semiconductors_design, semiconductors_memory, semiconductors_equipment,
                semiconductors_foundry, robotics_automation, cloud_software_saas,
                real_estate_data_centers
breaking    0
```

`structural_monitor --all` reports all 8 structurals **healthy** (0 weak indicators each; largest
intensity drop −3.0, well under the −15 escalation threshold). The contradict propagates **only** to
the AI-capex pure-plays, **all as `contested`** (reversible), while multi-catalyst sectors anchored
elsewhere (`ai_infrastructure_data_centers`, `copper_miners`) stay `intact`. **Nothing escalates to
`breaking`** — the fundamentals do not corroborate the price action. This is the designed behaviour:
the pipeline treats the one-off selloff as noise, exactly the requirement. The signal does **not**
change `catalyst_alignment` or the composite (verified: 88 tests green, `scoring_version` unchanged).

### 8.2 First live watch case — `struct_japan_carry_unwind`

Added 2026-06-06 as a **watch-only systemic-risk structural** (BoJ normalization + yen carry-trade
unwind + JGB/UST repatriation). Deliberately **not linked to any sector study**, so it never boosts
a sector's alignment — `structural_monitor` watches it, nothing else. Current read (WebSearch-grounded):
BoJ 0.75% (≈80% odds of a June-16 hike), 10Y JGB ≈2.66% (above its strong threshold), core CPI 2.8%
→ intensity 68.1, monitor `healthy` (0/4 weak). This is exactly the kind of structural whose
*indicators degrading* (or a `deact_03` disorderly-unwind trip) should drive a broad **risk-off
recommendation** — the `breaking` path of §5 — rather than a sector tailwind. It is the canonical
test bed for the persistence + correlation layers below.

## 8.5 Agreed next build — persistence (Layer 2) + uncorrelated recommendation (Layer 3)

The user's requirement: *don't move on a one-off, but if the pressure persists/repeats, recommend
rotating to LESS-correlated sectors.* Two additive modules, both feeding the monthly review (no
auto-trade):

**Layer 2 — persistence as CONTEXT, judged by Claude (built).** Two hard rules learned here:

1. **Time-independence.** Persistence must read the WORLD's clock (event timestamps over a calendar
   window), never a counter incremented per run — otherwise the verdict depends on whether you
   launch the analysis daily/weekly/monthly. The pipeline is a *stateless render* of timestamped
   state: two evaluations at the same instant give the same answer, run once or 100×. Implemented:
   `within_window()` + distinct-live event counting from `event_date`, no run state anywhere.

2. **Python does not auto-escalate off an event count — Claude judges.** A hard "≥2 events →
   breaking" is brittle and off-model: *two consecutive-day drops confirm nothing; the same two
   events a month apart are real recurrence.* Spacing + macro context decide, and that is reasoning,
   not arithmetic. So Python labels only the OBJECTIVE state (`breaking` ⟸ measured fundamental
   degradation; `contested` ⟸ ≥1 live contradict) and emits a **contextual dossier**
   (`persistence_evidence`): distinct developments, calendar span, `clustered_one_shock` vs
   dispersed, decayed strengths, and an advisory `review_recommended` (dispersed multiples only).
   `catalyst_scorer` surfaces it per sector (`regime_review_recommended`). **Claude makes the
   escalation call in the skill**, with WebSearch macro context — the hybrid model: Python computes
   facts, Claude reasons. (A future relative-strength-vs-market channel can add a second corroborating
   fact, also windowed/time-independent — left out of v1 because lake price data is flaky.)

**Layer 3 — correlation-aware recommendation.** When something goes `breaking`, rank candidate
replacements by **high composite × LOW correlation** to the breaking cluster (correlation matrix
computed from the lake's ETF return history). This directly fixes the experiment's "illusory
diversification" finding: rotate semis → *uncorrelated* (gold, defensives, JPY-hedged), never
semis → AI-infra. For `struct_japan_carry_unwind` specifically, a `breaking` read = a global
risk-off, so the low-correlation set is the recommendation surface.

## 9. The dislocation engine (BUILT) — same gap, both directions, one engine, two lenses

`catalyx/scorer/dislocation.py` reads the SAME price-vs-fundamentals gap that drives `regime_state`,
but for **capital deployment**. One shared correlation/beta engine over yfinance returns (90d), two
lenses:

- **OPPORTUNITY (panic dip).** A sector that fell hard but is `intact` + catalyst-confirmed, whose
  drop is mostly **contagion** (β×market move), not idiosyncratic → "it fell *with* the tape, its
  thesis didn't break" → candidate buy. The decomposition is forward-looking return attribution:
  `drawdown = contagion (β·market) + idiosyncratic residual`. A clean panic dip = high contagion
  fraction; a large idiosyncratic residual is a **red flag** (Claude must find the hidden cause
  before calling it a dip).
- **DIVERSIFIER (rotation target).** Healthy, high-composite sectors with the LOWEST correlation to
  the stressed cluster (contested/breaking, or worst-drawdown) — Layer 3, the "rotate to
  uncorrelated" fix.

Same correlation matrix, opposite use of correlation. Python computes the facts; the BUY/ROTATE
call is Claude's (with WebSearch). **Live verification (2026-06-05 selloff window):**

- `ai_infrastructure_data_centers`: −5.1%, β 1.97 → contagion −4.9% vs idiosyncratic **−0.15%**
  (≈97% contagion), `intact`, catalyst 96.7 → **cleanest panic-dip opportunity**.
- `semiconductors_memory`: **excluded** — it is `contested` (the Broadcom miss questions *its own*
  thesis), so its drop is partly justified, not innocent panic. Correct discrimination: "fell
  because the bad news touches it" vs "fell dragged along though it doesn't" (AIPO).
- `solar_energy`: −10% but β 0.66 → only −1.7% contagion, **−8.3% idiosyncratic** → red flag, not a
  clean dip — Claude investigates.
- `copper_miners`: only −1.7% in the real window (held up *better* than β predicted) → no
  dislocation, correctly absent. The engine reports real data; it does not force the hypothesis.
- Diversifiers vs the stressed semis cluster: cybersecurity (corr 0.19), royalty/streaming metals
  (0.25), solar (0.29) — low-correlation rotation surface.

## 9. Open decisions (for evaluation before building)

1. **Thresholds.** τ_evt=50, N_ind=2/3, Δ_int=15 are first guesses. Calibrate against history once
   the backtesting harness exists (CLAUDE.md future work) — until then they are explicit, tunable
   config in `scoring_weights.yaml`, not magic numbers in code.
2. **Fast interim indicator?** Do we add a low-confidence price/breadth corroboration input (§6.2),
   or accept that `breaking` always lags to the next fundamental print? Trade-off: faster
   protection vs more false `breaking`.
3. **Haircut shape.** Linear in decayed strength, or stepped? Capped at a floor weight, or to zero?
4. **State granularity in the lake.** Store only the per-sector state, or also the per-structural
   states behind it (more queryable, slightly heavier partitions)?
5. **#3 timing.** Build penalty-aware aggregation now (cleaner model, breaks comparability) or
   defer until the state model is validated (recommended)?

## 10. Summary

- The discrimination is **not** a day-0 classification — it is a **decay-vs-accumulation race**
  resolved over time.
- The pipeline already has the right two channels (event decays = opinion; intensity persists =
  fundamentals); what is missing is the **bridge** (event triggers an indicator re-check) and the
  **escalation rule**.
- Three states — `intact` / `contested` / `breaking` — let the strategy act reversibly on the fast
  noisy signal and commit capital only on the slow corroborated one.
- A single event can only ever reach `contested`, which guarantees *"un evento puntual no mete
  ruido."* `breaking` requires fundamental corroboration and a human to exit.
- Validated against exp_2026-06-05: with current indicators the AI selloff classifies `contested`,
  not `breaking` — the pipeline would correctly treat it as noise.
