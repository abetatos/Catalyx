# catalyx-dashboard

Generate the CATALYX Catalyst Dashboard report from current data files.

## Steps

1. Read `CLAUDE.md` to confirm schema versions and scoring rules.

2. Load structural catalysts (read directly from the YAML files):
   ```
   uv run python -m catalyx.store.structural_catalyst_repo summary
   ```
   For full indicator detail on any catalyst, use:
   ```
   uv run python -m catalyx.store.structural_catalyst_repo get <id>
   ```

3. Load event catalysts (read directly from the JSON files):
   ```
   uv run python -m catalyx.store.catalyst_repo summary
   ```
   For full detail on any event catalyst, use:
   ```
   uv run python -m catalyx.store.catalyst_repo get <id>
   ```

4. Read `catalyx/config/scoring_weights.yaml` for `user_rank_ordering` and `indicator_color_thresholds`.

5. For each structural catalyst, compute:
   - `display_priority = intensity.current_score` (v1.5: user_rank no longer multiplies — see step 7).
   - Indicator color per indicator: read the DERIVED `semaphore` field (written by the intensity engine from the continuous `score`). Do not recompute from thresholds — the score, not the threshold bucket, is the source of truth. 🟢 if `score ≥ green`, 🟡 if `≥ amber`, else 🔴 (`indicator_color_thresholds`).
   - Trend arrow from `intensity.history`: ↑↑ (2+ periods rising), ↑ (1 period rising), → (flat), ↓ (falling)
   - Days since `status_last_reviewed` — flag if > 45 days

6. For each event catalyst, compute:
   - Remaining relevance using decay: `score × exp(-0.693 / halflife_days × days_since_detected)`
   - Days since `detected_at`

7. Rank all catalysts by `display_priority` (= algorithmic score) descending, breaking ties with `user_rank` ascending (1 = highest). user_rank only reorders near-equals — it can no longer push a weaker catalyst above a materially stronger one.

8. Identify alerts:
   - 🔴 Critical: any indicator below `threshold_weak`
   - 🟡 Monitoring: any indicator in yellow zone OR `status_last_reviewed` > 45 days ago
   - Structural catalysts with `intensity` dropping >5 points vs prior period

9. Build the next review dates table: for each indicator, compute due date from `last_date + check_frequency`.

10. Write the report to `data/reports/catalyst_dashboard_YYYYMMDD.md` following the template at `docs/report_templates/catalyst_dashboard_template.md`.

11. State clearly which catalysts have stale data (indicators not updated in > 1 check_frequency period).

## Output format

Follow `docs/report_templates/catalyst_dashboard_template.md` exactly.
Report filename: `data/reports/catalyst_dashboard_YYYYMMDD.md` where YYYYMMDD is today's date.
After writing, print a one-paragraph summary of the most important finding.
