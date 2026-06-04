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
   For full detail on any specific record needed for scoring, use `get <id>` on the relevant repo.

   **OPTIONAL — Python momentum data (Phase 0.5+):**
   Check if `data/snapshots/momentum_snapshot_*.json` exists. If yes:
   - Read the most recent one (sort by filename date)
   - For each sector, extract `momentum_score` from `snapshot.sectors.<sector_id>.primary`
   - Use this score as the `momentum` dimension in the composite (weight: 0.25)
   - Mark `momentum: ✅ computed` instead of `❌ Missing`
   - If the snapshot is >3 days old, flag it as stale and re-run:
     `uv run python -m catalyx.data.market_data`
   If no snapshot exists, momentum remains missing (mark `❌`).

   To generate a fresh snapshot before running the heatmap:
   ```
   uv run python -m catalyx.data.market_data
   ```

3. For each sector in the taxonomy with `investable: true`, compute `catalyst_alignment`:

   **Structural component** (for each structural catalyst):
   - Primary sector (sector is the direct named beneficiary): `intensity.current_score × 0.95`
   - Secondary sector (indirect or partial beneficiary): `intensity.current_score × 0.70`
   - If multiple structural catalysts apply: `max(primary) + 0.30 × sum(secondary)`, cap at 95

   Map each structural catalyst to sectors using `thematic_tags` and the sector's `demand_drivers`:
   - Semantic match HIGH (direct demand driver): primary
   - Semantic match MEDIUM (related but indirect): secondary
   - No match: 0

   **Event component** — interaction mode depends on `relation_to_structural` field in the event JSON:

   First, compute `remaining_relevance`:
   - `remaining_relevance = exp(-0.693 / decay_halflife_days × days_since_detected)`

   Then apply interaction formula based on `relation_to_structural`:

   **Case A — `relation_to_structural: "confirms"`:**
   - `amplifier_effective = 1.0 + 0.12 × remaining_relevance`
   - `case_a_raw = structural_component × amplifier_effective`
   - `case_c_equivalent = structural_component × 0.45 + strength_score × remaining_relevance × alignment_factor × 0.55`
   - `catalyst_alignment = min(case_a_raw, case_c_equivalent)`, cap at 95
   - Rationale: the event validates the structural thesis — it boosts conviction but does not add independent information. The cap ensures a confirming event never scores higher than an equally-strong independent signal would. As the event decays, the boost fades back toward the structural baseline.

   **Case B — `relation_to_structural: "contradicts"`:**
   - `dampener_effective = 1.0 - 0.18 × remaining_relevance`
   - `catalyst_alignment = structural_component × dampener_effective`
   - Floor at 0
   - Rationale: the event raises doubt about the structural thesis — it reduces effective intensity but doesn't invalidate it.

   **Case C — `relation_to_structural: "independent"` or `null`:**
   - `event_score = strength_score × remaining_relevance × alignment_factor`
   - `alignment_factor`: 0.95 for primary sector, 0.70 for secondary
   - `catalyst_alignment = structural_component × 0.45 + event_score × 0.55`
   - Rationale: the event provides genuinely new information not captured by any structural catalyst.

   If multiple events apply to a sector, apply interaction formula for each separately and take the most impactful result (do not stack multipliers).

   If no active event catalysts: `catalyst_alignment = structural_component`

4. For each sector, note which dimensions are MISSING (momentum, flow, valuation, crowding).
   Mark composite as partial if any dimension is missing.

5. Flag crowding risk qualitatively using `sector_study` if available, or from the structural catalyst user_notes.

6. For `watch_only: true` sectors: compute trigger progress (N triggers met / total triggers).
   Do not score these — only show trigger status.

7. Rank investable sectors by `catalyst_alignment` descending (Phase 0). In Phase 1 rank by `composite`.

8. For the top 5 sectors, write a detailed block including:
   - Which catalysts are driving alignment and why (cite specific catalyst IDs)
   - The non-obvious finding (what the market has NOT priced)
   - Best ETF vehicle for a Spanish investor (UCITS preference, flag AUM < $200M)
   - What real-time data would change the ranking

9. Flag any sector where catalyst_alignment is high (>75) but a sector_study does NOT exist yet — these are gaps to fill.

10. Write report to `data/reports/heatmap_YYYYMMDD.md` following `docs/report_templates/heatmap_template.md`.

## Rules

- Never mention a sector without its `sector_id` in backticks.
- Never recommend an ETF without stating TER, AUM, UCITS status, and spread.
- The non-obvious finding section is mandatory for each top-5 sector. If the reason a sector ranks high is obvious, the analysis adds no value.
- If two adjacent sectors score similarly, explain the differentiation explicitly.

## Output format

Follow `docs/report_templates/heatmap_template.md`.
Filename: `data/reports/heatmap_YYYYMMDD.md`.
After writing, print a ranking table (sector, catalyst_alignment, top ETF) as a quick-reference summary.
