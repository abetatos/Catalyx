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

1. Read `CLAUDE.md` for schema change protocol.
2. Read the target catalyst file: `catalyx/config/structural_catalysts/<catalyst_id>.yaml`
3. Find the indicator with `id: <indicator_id>`.
4. Update:
   - `current_value` → `<new_value>`
   - `last_value` → previous `current_value` (shift down)
   - `last_date` → today's date
5. Compute new indicator status:
   - For `higher_is_stronger`: 🟢 if new_value ≥ threshold_strong, 🟡 if threshold_weak ≤ new_value < threshold_strong, 🔴 if new_value < threshold_weak
   - For `lower_is_stronger`: invert
   - If status changed (e.g., 🟢 → 🟡), flag this prominently
6. If `[note]` provided, add it to `intensity.history` for the current quarter. If a history entry for the current quarter already exists, append to its `note`.
7. Update `status_last_reviewed` to today.
8. Compute the new `intensity.current_score` using the algorithmic formula from `scoring_weights.yaml`.
   **Do not guess or use qualitative ranges — compute the exact value:**

   **8a — Score each indicator** (using the updated value for the modified indicator, stored values for all others):
   - `higher_is_stronger`: value ≥ threshold_strong → 100 | threshold_weak ≤ value < threshold_strong → 65 | value < threshold_weak → 20
   - `lower_is_stronger`: value ≤ threshold_strong → 100 | threshold_strong < value ≤ threshold_weak → 65 | value > threshold_weak → 20

   **8b — `indicator_avg` = arithmetic mean of all indicator scores (equal weight)**

   **8c — Trend factor** from the last 2 periods in `intensity.history`:
   - ↑↑ (2+ consecutive rising periods): × 1.05
   - ↑ (1 period rising): × 1.02
   - → (flat, ≤2 point change): × 1.00
   - ↓ (1 period falling): × 0.97
   - ↓↓ (2+ consecutive falling): × 0.93

   **8d — `new_intensity = round(indicator_avg × trend_factor, 1)`, clamped to [10, 95]**

   If a `deactivation_conditions` threshold is approaching, print: ⚠ DEACTIVATION CONDITION APPROACHING: [condition text]

   Present to user: "`<catalyst_id>`: intensity `<current_score>` → `<new_intensity>` (computed from indicators — semaphore scores: [list each])" and ask user to confirm before writing.

9. Write the updated YAML file (after user confirms intensity).
10. Resync the DB so dashboard and heatmap read fresh data in this session:
    ```
    uv run python -m catalyx.store.structural_catalyst_repo sync
    ```
11. Print a one-line summary: `<catalyst_id> — ind_<id> updated: <old> → <new> [status change if any]`

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

- Never update `intensity.current_score` without computing it from the formula — present the exact computed value and ask user to confirm. Do NOT suggest qualitative ranges (+2 to +5, etc.).
- Always shift `current_value → last_value` before writing the new `current_value`. Do not lose the prior value.
- If the new value crosses a `deactivation_conditions` threshold, print a warning: ⚠ DEACTIVATION CONDITION APPROACHING: [condition text].
- Update `status_last_reviewed` on every call, even if no status changes.
- Always run `structural_catalyst_repo sync` after writing the YAML, so the DB is current within the session.
