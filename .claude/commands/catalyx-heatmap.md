# catalyx-heatmap

Generate the CATALYX Sector Heatmap — ranks all investable sectors by composite score using available data.

## Steps

0. Rebuild DB index:
   ```
   uv run python -c "from catalyx.store import init_all; init_all()"
   ```

1. Read `CLAUDE.md` for scoring methodology and rules.

2. Read config files (source of truth, not in DB):
   - `catalyx/config/sector_taxonomy.yaml` — all sector IDs and metadata
   - `catalyx/config/scoring_weights.yaml` — composite formula and weights
   - `catalyx/config/etf_universe.yaml` — ETF options per sector

   Load runtime data from DB:
   ```
   uv run python -m catalyx.store.structural_catalyst_repo summary
   uv run python -m catalyx.store.catalyst_repo summary
   uv run python -m catalyx.store.sector_study_repo summary
   ```

3. **Prerequisite gate — sector study freshness check.**

   The heatmap scores exactly the sectors that have a `data/sector_studies/study_<sector_id>.json` file.
   No study → not ranked (appears only in the GAPS section at step 9).

   A study is considered stale if `last_updated` is older than 7 days.

   **Scope of this gate:** only the sectors that DO have a study file. Read their `last_updated` field.
   For each study file found in `data/sector_studies/`:
   - Parse `last_updated` and compute days since that date
   - If > 7 days: mark stale

   **If any existing study is stale, STOP HERE.**
   Print a checklist of what must be refreshed before proceeding:

   ```
   ⛔ HEATMAP BLOCKED — stale sector studies:
   [ ] /catalyx-sector-study <sector_id>   ← last_updated: YYYY-MM-DD (N days old)
   ...
   Run the above commands, then re-run /catalyx-heatmap.
   ```

   If all studies are fresh (≤ 7 days), proceed. Sectors with NO study are not blocked —
   they simply don't appear in the ranking (flagged as gaps in step 9).

4. **Run Python scoring pipeline (one call per module).**

   Refresh market data if stale (>3 days old):
   ```bash
   uv run python -m catalyx.data.market_data     # momentum snapshot → data/snapshots/momentum_snapshot_YYYYMMDD.json
   uv run python -m catalyx.data.flow_data --write  # flow snapshot → data/snapshots/flow_snapshot_YYYYMMDD.json
   ```

   The flow snapshot computes week-over-week shares_outstanding delta (ETF creation/redemption).
   On first run it initialises to 50 (neutral) — `flow_pct_1w` becomes meaningful from the second run onward.

   Then run the scoring pipeline:
   ```bash
   # Cross-sectional momentum percentile ranks across all sectors in snapshot
   uv run python -m catalyx.scorer.momentum_engine --json

   # Composite scores for all sectors
   # Auto-loads latest momentum + flow snapshots; catalyst_alignment derived from sector studies
   uv run python -m catalyx.scorer.sector_scorer --all --json
   ```

   These outputs are the authoritative scores. Do NOT recompute catalyst_alignment, momentum, or flow manually.

5. **Apply crowding_risk from sector studies.**

   The sector_scorer defaults `crowding_risk` to 35. Override it per sector using `narrative_maturity`
   from the sector study:
   - `ignored`    → 10
   - `emerging`   → 25
   - `mainstream` → 55
   - `crowded`    → 75
   - `exhausted`  → 90

   For each sector whose study has `narrative_maturity` set, re-run with the override:
   ```bash
   uv run python -m catalyx.scorer.sector_scorer <sector_id> --crowd <N> --json
   ```

   Mark each sector's crowding source: `🟢 study.narrative_maturity` or `⚠ default (35)`.

6. For `watch_only: true` sectors: compute trigger progress (N triggers met / total triggers).
   Do not score — only show trigger status.

7. Rank all investable sectors by `composite` descending. Note which dimensions are Phase 0.5 defaults:
   - `flow_confirmation`: ⚠ default (50) — no ETF flow data yet
   - `valuation_relative`: ⚠ default (50) — no formal percentile yet
   - `crowding_risk`: 🟢 from study or ⚠ default (35)

8. For the top 5 sectors, write a detailed block including:
   - Which catalysts are driving alignment and why (cite specific catalyst IDs and their `catalyst_alignment` breakdown from the scorer output)
   - The non-obvious finding (what the market has NOT priced)
   - Best ETF vehicle for a Spanish investor (UCITS preference, flag AUM < $200M)
   - What real-time data would change the ranking

9. Flag any sector where `catalyst_alignment > 75` but where the composite is pulling it down due to weak momentum or high crowding — these are "strong catalyst, bad timing" sectors worth monitoring.

10. Write report to `data/reports/heatmap_YYYYMMDD.md` following `docs/report_templates/heatmap_template.md`.

## Rules

- Never mention a sector without its `sector_id` in backticks.
- Never recommend an ETF without stating TER, AUM, UCITS status, and spread.
- The non-obvious finding section is mandatory for each top-5 sector. If the reason a sector ranks high is obvious, the analysis adds no value.
- If two adjacent sectors score similarly, explain the differentiation explicitly.
- **Pre-calibration banner is mandatory.** The composite score weights (0.30/0.25/0.20/0.15/0.10) are uncalibrated — they require N > 50 closed theses to validate. Until then, all composite scores carry calibration uncertainty. Include this notice at the top of every heatmap report and at the top of every ranking table: `⚠ PRE-CALIBRATION: weights unvalidated (0 closed theses). Scores indicate relative ordering, not precise conviction levels.`

## Output format

Follow `docs/report_templates/heatmap_template.md`.
Filename: `data/reports/heatmap_YYYYMMDD.md`.
After writing, print a ranking table (sector, catalyst_alignment, top ETF) as a quick-reference summary.
