# catalyx-sector-study

Generate or update a SectorStudy — the sector's fundamental file. **Two depths**, because a study
is expensive and most refreshes do not need the deep version.

Usage:
- `/catalyx-sector-study <sector_id>` — **core refresh (default)**. ~2 WebSearches, ~3 KB.
- `/catalyx-sector-study <sector_id> --deep` — full dossier. ~6 WebSearches, ~25 KB.

## Which depth?

| Run `--deep` when | Run the default `core` when |
|---|---|
| The sector has **no study yet** (first time) | A periodic review refresh |
| You are **about to open a position** in it | The driving catalyst moved and you need the new state |
| **Quarterly** deep refresh | Anything else |

**Why core is the default (schema 1.3):** only two study fields are read by any code —
`active_catalyst_ids` (→ `catalyst_scorer`) and `narrative_maturity` (→ crowding in
`snapshot_repo`). A full dossier costs ≈45k tokens to re-derive prose that nothing consumes, and
`etf_analysis` duplicates `etf_universe.yaml` (the single source of truth). A sector without a
fresh study still ranks — on its momentum baseline — so a cheap refresh is never a blind spot.

---

## Steps — CORE refresh (default)

1. Read the contract + config:
   - `schemas/sector_study.json` — set `study_type: "core"`, `schema_version: "1.3"`.
   - `catalyx/config/sector_taxonomy.yaml` — the entry for `<sector_id>` (it already carries
     `demand_drivers`; do NOT re-derive them into the study).
   ```
   uv run python -m catalyx.store.sector_study_repo get study_<sector_id>
   ```
   "Not found" → this is a new study, so run `--deep` instead. Otherwise preserve `created_at`
   and every field you are not refreshing.

2. **Two WebSearches, no more:**
   ```
   "<sector_label> outlook <MONTH YEAR>"
   "<sector_label> analyst estimate revision <MONTH YEAR>"
   ```

3. Update only the decision-relevant fields:
   - `active_catalyst_ids` — which registered catalysts drive this sector NOW. This is the field
     the scorer reads; a catalyst that went dormant/archived must come out.
   - `narrative_maturity` — the 5-level enum, anchored to `scoring_weights.yaml`
     `narrative_maturity_levels.score_equiv` (ignored→10, emerging→35, mainstream→60, crowded→80,
     exhausted→95) + `analyst_narrative_score` to the matching integer.
   - `narrative_notes` — one or two sentences justifying the level with what you just read. A
     level without a rationale is useless.
   - `narrative_trend` — increasing / stable / decreasing.
   - `key_metrics_to_monitor[]` — refresh `current` values for the metrics already listed.
   - `risks[]` — keep the top 3–6; add one only if the search surfaced something genuinely new.
   - `cycle_position` — update only if the search changed your read.
   - `last_updated` → today.

4. Write `data/sector_studies/study_<sector_id>.json` and validate:
   ```
   uv run python -c "import json,jsonschema; jsonschema.validate(json.load(open('data/sector_studies/study_<sector_id>.json',encoding='utf-8')), json.load(open('schemas/sector_study.json')))"
   ```

5. Print two lines: what changed vs the stored study (especially a `narrative_maturity` flip or a
   catalyst added/removed), and the strongest active catalyst.

---

## Steps — DEEP dossier (`--deep`)

Everything in the core refresh, plus the full bottom-up file. Set `study_type: "full"`.

1. Also read `catalyx/config/etf_universe.yaml` for the sector's vehicles.
   ```
   uv run python -m catalyx.store.structural_catalyst_repo summary
   ```

2. Four additional WebSearches:
   ```
   "<sector_label> supply demand outlook <YEAR>"
   "<sector primary company> earnings <YEAR>"
   "<sector_label> ETF performance <YEAR>"
   "<sector_label> <its key commodity or metric> forecast"
   ```

3. Populate the deep blocks:
   - `demand_drivers[]` — specific and quantified. Not "demand is growing" but "China is 55% of
     copper consumption and PMI has been above 50 for 6 consecutive months."
   - `supply_constraints[]` — what limits supply response inside 5 years?
   - `cycle_position` — be opinionated, back it with data.
   - `technology_maturity`, `historical_catalyst_performance` — how the sector responded to this
     catalyst type before.
   - `taxonomy.differentiation_note` — **the most important field.** Why is this sector NOT the
     same as its neighbours? Minimum 2 sentences. If you cannot articulate it, say so — it means
     the taxonomy's granularity is wrong.
   - `etf_analysis[]` — **DEPRECATED (schema 1.3), leave it out.** Cite `etf_universe.yaml`
     instead; it is the single source of truth and the only one any code reads. Fix that file if a
     vehicle is wrong or missing.

4. For a `watch_only: true` sector: `study_type: "watch_only"`. Fill only `taxonomy`,
   `technology_maturity`, `risks`, and the `watch_triggers` status.

---

## Rules

- **Never fabricate a number.** An unknown TER/AUM/metric is `null` — the schema permits it
  precisely so no one invents precision.
- `differentiation_note` must explain why this sector ≠ its adjacent sectors (deep only, but it
  carries over — never blank it in a core refresh).
- `analyst_narrative_score` must be justified in `narrative_notes`.
- ETF selection lives in `etf_universe.yaml`, never in the study. For a Spanish investor prefer
  UCITS with AUM > $200M and spread < 20bps.
- The written JSON **is** the registration — `sector_study_repo summary`/`get`/`stale` read the
  directory directly. No import step.
- A core refresh that finds nothing changed should say so in one line and still bump
  `last_updated` — "checked, unchanged" is information; silence is not.
