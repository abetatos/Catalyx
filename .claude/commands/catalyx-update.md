# catalyx-update

Update a structural catalyst's indicators or narrative maturity with new data. Use after receiving a data release (WGC, CFTC COT, IMF, LME, earnings) or when narrative saturation has visibly shifted.

Usage:
- `/catalyx-update <catalyst_id> <indicator_id> <new_value> [note]` — update a numeric indicator
- `/catalyx-update <catalyst_id> narrative_maturity <value>` — update narrative saturation level
  Valid values: `ignored` | `emerging` | `mainstream` | `crowded` | `exhausted`

Examples:
- `/catalyx-update struct_cb_gold_accumulation ind_01 312 "WGC Q2 2026: 312T net CB purchases"`
- `/catalyx-update struct_copper_datacenter_demand ind_02 10450`
- `/catalyx-update struct_nato_rearmament ind_02 0.22 "RHM Q2: order book +22% YoY"`
- `/catalyx-update struct_nato_rearmament narrative_maturity mainstream`

## Steps — indicator update (`<indicator_id>`)

1. Read the target catalyst file: `catalyx/config/structural_catalysts/<catalyst_id>.yaml`
2. Find the indicator with `id: <indicator_id>`.
3. Update the indicator fields in the YAML:
   - `current_value` → `<new_value>`
   - `last_value` → previous `current_value` (shift down)
   - `last_date` → today's date
   - If status changed (🟢→🟡 etc.), flag this prominently before writing
4. If `[note]` provided, add it to the human-readable `update_note` field on the indicator.
5. Update `status_last_reviewed` to today.
6. Check `deactivation_conditions` — if any threshold is now breached or approaching, print:
   ⚠ DEACTIVATION CONDITION APPROACHING: [condition text]
7. Write the updated YAML file.
8. Run the intensity engine to recompute the score and write it back automatically:
   ```bash
   uv run python -m catalyx.scorer.intensity_engine \
     catalyx/config/structural_catalysts/<catalyst_id>.yaml \
     --write-back --period "<current_quarter e.g. 2026-Q2>"
   ```
   Show the output to the user. The engine prints computed_score, stored_score, Δ, and the per-indicator breakdown. No manual arithmetic needed.
9. Resync the DB:
   ```bash
   uv run python -m catalyx.store.structural_catalyst_repo sync
   ```
10. Print a one-line summary: `<catalyst_id> — ind_<id>: <old_value> → <new_value> | intensity: <old_score> → <new_score>`

---

## Steps — narrative_maturity update

1. Read the target catalyst file: `catalyx/config/structural_catalysts/<catalyst_id>.yaml`
2. Validate that `<value>` is one of: `ignored | emerging | mainstream | crowded | exhausted`.
   If not, print the valid values and stop.
3. Read `catalyx/config/scoring_weights.yaml` `narrative_maturity_levels` section and confirm the chosen level fits the observable criteria. State your reasoning in one sentence.
4. Update `narrative_maturity` in the YAML and `status_last_reviewed` to today.
5. Write the YAML file.
6. Resync the DB:
   ```
   uv run python -m catalyx.store.structural_catalyst_repo sync
   ```
7. Print: `<catalyst_id> — narrative_maturity updated: <old> → <new>`

## Rules

- Never manually compute `intensity.current_score` — always delegate to `intensity_engine.py --write-back`. The engine is the single source of truth for the formula.
- Always shift `current_value → last_value` before writing the new `current_value`. Do not lose the prior value.
- If the new value crosses a `deactivation_conditions` threshold, print a warning: ⚠ DEACTIVATION CONDITION APPROACHING: [condition text].
- Update `status_last_reviewed` on every call, even if no status changes.
- Always run `structural_catalyst_repo sync` after writing the YAML, so the DB is current within the session.
