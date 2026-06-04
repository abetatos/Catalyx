# catalyx-dashboard

Generate the CATALYX Catalyst Dashboard report from current data files.

## Steps

0. Rebuild DB index:
   ```
   uv run python -c "from catalyx.store import init_all; init_all()"
   ```

1. Read `CLAUDE.md` to confirm schema versions and scoring rules.

2. Load structural catalysts from DB:
   ```
   uv run python -m catalyx.store.structural_catalyst_repo summary
   ```
   For full indicator detail on any catalyst, use:
   ```
   uv run python -m catalyx.store.structural_catalyst_repo get <id>
   ```

3. Load event catalysts from DB:
   ```
   uv run python -m catalyx.store.catalyst_repo summary
   ```
   For full detail on any event catalyst, use:
   ```
   uv run python -m catalyx.store.catalyst_repo get <id>
   ```

4. Read `catalyx/config/scoring_weights.yaml` for user_rank multipliers.

5. For each structural catalyst, compute:
   - `display_priority = intensity.current_score × user_rank_multiplier`
   - Indicator status per indicator: 🟢 if above `threshold_strong`, 🟡 if between thresholds, 🔴 if below `threshold_weak`. Invert logic for `lower_is_stronger` indicators.
   - Trend arrow from `intensity.history`: ↑↑ (2+ periods rising), ↑ (1 period rising), → (flat), ↓ (falling)
   - Days since `status_last_reviewed` — flag if > 45 days

6. For each event catalyst, compute:
   - Remaining relevance using decay: `score × exp(-0.693 / halflife_days × days_since_detected)`
   - Days since `detected_at`

7. Rank all catalysts by `display_priority` descending.

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
