# DESIGN — Experiment ledger (closed-position validation + behavioral self-learning)

> Status: built 2026-06-07 (v2.18). Module: `catalyx/attribution/outcome.py`. Schema: `movement.json` v1.2 `outcome` block. Lake: `validation/movement_outcome`. Dashboard: Positions → "Experiment ledger".

## Why this exists (and what it is NOT)

The platform's BUY stack is fully deterministic and point-in-time. The question "did it work?" was not answered anywhere — closing a position realized P&L and tax, but the *reasoning* was never scored.

A purely statistical backtest of the catalyst signal is **intractable honestly**: `catalyst_alignment` is partly LLM judgement (catalyst detection + scoring in the scan), and you cannot rebuild "what you knew in March" without contaminating it with hindsight — the historical reconstruction (GDELT/COT) leaks look-ahead through the LLM, the taxonomy (chosen knowing what worked), and the weights (calibrated today). And the forward, no-look-ahead path has near-zero statistical power for years (~40 sectors × monthly ⇒ a catalyst IC is indistinguishable from noise until 3-5y in).

So this is **not** a statistical backtest. It is a **decision journal where each closed movement is a registered experiment**:

- **Hypothesis** = the opening movement: `attribution` (why / which catalyst), `score_context` (what the system knew, point-in-time, no look-ahead), `risk_discipline.assumptions[]` + `invalidation[]` (the falsifiable predictions).
- **Result** = the closing movement: realized P&L, which assumptions held, whether the catalyst materialized, and — the part a quant has no field for — **whether the human followed their own discipline**.

It dodges the look-ahead problem entirely by **recording forward**, and it delivers value from experiment #1 (N=1 already teaches "was this well-reasoned?") rather than waiting for statistical significance.

## What Python computes vs what the human supplies

Doctrine (same as the rest of the system): **Python surfaces facts, the human judges.**

| Computed by `outcome.py` (network-free, from the files) | Captured at `/catalyx-close` (human-judged) |
|---|---|
| Realized P&L gross + **after-tax** (`tax_engine`, YTD-prior reconstructed from prior closes) | `exit_reason` (free text), `exit_note` (in-the-moment) |
| `holding_days`, `return_pct` | `assumption_resolution[]` (validated / falsified / unresolved) |
| The **verdict** matrix (right_thesis × right_reason) | `catalyst_materialized` (did the mechanism play out?) |
| **behavioral_flags** (deviations from discipline) | `signal_context.followed_signal` (did you obey `exit_watcher`?) |

## The verdict matrix — separate skill from luck

`right_thesis` = made money after tax. `right_reason` = the assumptions held / the catalyst materialized (the catalyst question dominates when answered; else the validated-vs-falsified balance).

|                | reason held            | reason failed             |
|----------------|------------------------|---------------------------|
| **made money** | `skill` — repeat       | `luck` — don't over-learn |
| **lost money** | `variance` — sound process, bad draw / short horizon | `correct_invalidation` — the discipline worked |

`confidence: low` when `holding_days < 60` or the assumptions are mostly `unresolved` — no false precision. Unanswered reason ⇒ `indeterminate`.

Without this, a winning trade teaches the wrong lesson when you won for the wrong reason. The matrix is the rebuilt `right_reason_score` / `ClosedThesis`, on the Movement model.

## Behavioral self-learning — the new layer

The deviation is computable; the *why* is not. The engine **detects** the deviation from the files and **prompts** a reflection; the user **annotates** it. Flags (files-only, no network):

- `held_past_full_exit:<inv_id>:+Nd` — you sat past your own fired `full_exit` stop (discipline failure).
- `exited_intact_at_loss` — closed flat/down with the thesis still holding + no stop fired → the "salí muy pronto / pánico" shape.
- `discretionary_exit` — a `reconsideration`/`profit_take` exit with nothing triggered.
- `overrode_signal` — you went against `exit_watcher`.

A flag is a **prompt to reflect, not an accusation**. The reflection is the corpus: over many experiments, patterns emerge ("I trim winners on risk-off days").

## Editability decision (user-decided)

The annotation is a **free message of the user's own** (not an enum — many exits resist a clean category; `exit_trigger_type` is an *optional* light tag for aggregation only). It is **editable by adding**, never by overwriting:

- `exit_note` (+ `exit_note_at`) — the **in-the-moment** read. By convention NEVER rewritten — the in-the-moment emotional read is more valuable than the calmer story told later.
- `additional_notes[]` — append-only `{at, note}` later realizations.

This preserves the evolution (felt → understood) which is the learning signal. These live on the Tier-1 JSON file (hand-editable by design); the point-in-time, no-look-ahead data (`score_context`, the stops) was frozen separately at open, so editing notes never corrupts the experiment's integrity.

## Automation stance (user-decided)

**No automation — review-driven only.** Signal evaluation (`exit_watcher`) runs when the user launches a review/close, not on a schedule. The deterministic part *could* run as a zero-credit GitHub Action (the same pattern as `pages.yml`) to capture the day-by-day signal history during a holding (the "sold day 12 vs day 30" resolution) — that is a **future add-on**, not built. The judgement parts stay with the human. Distinction that matters: automating the *mechanical snapshot* is trivial Python-in-an-Action (no Claude); automating the *judgement* is the expensive, low-value path and is deliberately off the table.

## The other two backtest questions (status)

- **"Does it beat SPY?"** → `nav_engine.compute_real_nav` vs SPY (built, accruing from 2026-06-05).
- **"Does alpha survive tax?"** → real book: `tax_engine` at close (this ledger's after-tax P&L). Model book / rotation cost: the Fase-5 rebalance simulator (cost + CGT + FIFO + 2-month rule) — planned, deterministic, no deep history needed.

See `docs/PLAN_movement_restructure.md` Fase 4/5 and `CLAUDE.md` future-work for the remaining deterministic pieces.
