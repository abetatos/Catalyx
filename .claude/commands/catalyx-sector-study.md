# catalyx-sector-study

Generate or update a SectorStudy for a given sector. Produces a structured bottom-up analysis JSON file.

Usage: `/catalyx-sector-study <sector_id>`

## Steps

0. Rebuild DB index:
   ```
   uv run python -c "from catalyx.store import init_all; init_all()"
   ```

1. Read config files:
   - `CLAUDE.md` — sector_id validation rules
   - `schemas/sector_study.json` — all required fields
   - `catalyx/config/sector_taxonomy.yaml` — entry for `<sector_id>`
   - `catalyx/config/etf_universe.yaml` — ETF entries for `<sector_id>`

   Load runtime data from DB:
   ```
   uv run python -m catalyx.store.structural_catalyst_repo summary
   uv run python -m catalyx.store.sector_study_repo get study_<sector_id>
   ```
   If `get` returns "Not found", this is a new study. If it returns a record, this is an update — preserve `created_at` and only overwrite changed fields.

2. If the sector has `watch_only: true`: use `study_type: "watch_only"`. Only fill `taxonomy`, `technology_maturity`, `risks`, and `etf_analysis` (with "no ETF available" entry). Focus on `watch_triggers` status.

3. Run WebSearch for current information:
   ```
   "<sector_label> ETF performance 2026"
   "<sector_label> supply demand outlook 2026"
   "<sector primary company> earnings 2026"
   "<sector_label> analyst estimate revision"
   ```

4. From the taxonomy and search results, populate:
   - `demand_drivers[]`: list each driver as a specific, concrete statement. Not "demand is growing" but "China accounts for 55% of copper consumption and PMI has been above 50 for 6 consecutive months."
   - `supply_constraints[]`: what limits supply response in the <5yr horizon?
   - `cycle_position`: where in the cycle is this sector today? Be opinionated. Back it with data.
   - `key_metrics_to_monitor[]`: 4-6 specific metrics with sources, units, and current values if available
   - `etf_analysis[]`: from `etf_universe.yaml` — populate all fields. Flag AUM < $200M or spread > 25bps.
   - `risks[]`: 4-6 specific risks. Not generic — each risk should be sector-specific.
   - `analyst_narrative_score` (0-100): how saturated is this sector in mainstream media/analyst coverage? High = crowded narrative = less alpha. **Anchor to `scoring_weights.yaml` `narrative_maturity_levels.score_equiv`**: ignored→10, emerging→35, mainstream→60, crowded→80, exhausted→95. Set the integer closest to the matching level rather than a free-float number.
   - `narrative_trend`: increasing / stable / decreasing

5. For `differentiation_note` in taxonomy block: this is the most important field. Explain specifically why this sector is NOT the same as adjacent sectors. If you cannot articulate a clear differentiation, flag it — it may mean the granularity of the taxonomy is wrong.

6. For `historical_catalyst_performance`: use WebSearch to find how the sector responded historically to its key catalyst types.

7. Write to `data/sector_studies/study_<sector_id>.json` following `schemas/sector_study.json`.

8. If updating an existing study: preserve `created_at`, update `last_updated` to today. Only overwrite fields that have changed.

9. Import the study into the DB so it is queryable within this session:
   ```
   uv run python -m catalyx.store.sector_study_repo import-file data/sector_studies/study_<sector_id>.json
   ```

9. After writing, print a one-paragraph summary: sector position in cycle, strongest active catalyst, most important risk, best ETF vehicle.

## Rules

- `differentiation_note` must explain why this sector ≠ its adjacent sectors. Minimum 2 sentences. Required field — do not leave generic.
- `demand_drivers` must be specific and quantified where possible. No driver should be vague.
- ETF recommendations must flag UCITS status. For a Spanish investor, always prefer UCITS with AUM > $200M and spread < 20bps as tier 1.
- `analyst_narrative_score` must be justified in `narrative_notes`. A score without a rationale is useless.
