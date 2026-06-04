# CATALYX — Pipeline Test Prompt

> Use this document to start a fresh Claude Code session and stress-test the pipeline.
> Goal: find gaps, inconsistencies, and things to refine before Phase 1 (Python).
> Run each test in order. Critique outputs. Update config and skills accordingly.

---

## Context for the new session

You are working on CATALYX, a macro catalyst → ETF thesis platform.

**Read these files before doing anything:**
1. `CLAUDE.md` — full project spec, all rules, file locations
2. `catalyx/config/sector_taxonomy.yaml` — 60+ sectors with `investable` and `watch_only` flags
3. `catalyx/config/scoring_weights.yaml` — composite score formula and conviction tiers

**Current state (Phase 0 — skill-based, no Python yet):**

| Layer | Status |
|---|---|
| Config files | ✅ Complete (taxonomy, ETF universe, scoring weights, catalyst taxonomy) |
| Schemas | ✅ 6 JSON schemas (catalyst_event, structural_catalyst, sector_snapshot, sector_study, thesis, closed_thesis) |
| Structural catalysts | ✅ 5 active (CB gold, AI capex, NATO rearmament, energy grid, copper DC demand) |
| Event catalysts | ✅ 1 registered (`cat_20260603_nato_defense_gdp`) |
| Sector studies | ✅ 3 of ~15 priority sectors (grid_infrastructure, copper_miners, gold_miners) |
| Thesis | ✅ 1 draft (`thesis_20260603_copper_miners_datacenter_alpha`) — status: draft, not open |
| Reports | ✅ catalyst_dashboard_20260603.md + heatmap_20260603.md |
| Skills | ✅ 7 slash commands in `.claude/commands/` |
| Python code | ❌ None — Phase 1 not started |
| Real-time market data | ❌ None — momentum, flows, valuation all missing |

**Investment philosophy:** Macro-catalyst driven, ETF-only, high conviction, granular sectors. Spanish investor — all P&L in EUR, Spanish CGT brackets (19/21/23/27%).

---

## What Has Been Reviewed — Depth Map

Not everything in this project has received the same level of scrutiny. Before running tests, understand what is solid vs what is assumed.

### Reviewed thoroughly (confiar)

| Area | What was done |
|---|---|
| Dual catalyst model (structural + event) | Architecture designed, 5 structural YAMLs with indicators, thresholds, deactivation conditions. Schema validated. |
| Sector taxonomy | 60+ sectors with granularity requirements, `investable`/`watch_only` flags, differentiation notes per sector. Multiple iterations. |
| ETF universe | 2-3 ETFs per sector. UCITS flags, AUM/spread thresholds ($200M / 25bps), replication type, recommendation tiers. |
| Copper miners thesis | Full thesis JSON with 4 measurable assumptions and 4 specific invalidation conditions. Vehicle selection justified. |
| Dashboard methodology | `display_priority = intensity × user_rank_multiplier`. Semaphore logic against thresholds. Decay formula for events. |
| Scoring formula | `composite = alignment×0.30 + momentum×0.25 + flow×0.20 + valuation×0.15 - crowding_penalty×0.10`. Conviction tier ceilings (4/8/12%). |
| Spanish CGT | Progressive brackets (19/21/23/27%), no holding period distinction, FIFO lots, YTD accumulation logic. |
| Schema design | All 6 schemas with required fields, enums, pattern validation, additionalProperties: false. |

### Reviewed partially (validar durante los tests)

| Area | What's incomplete |
|---|---|
| Structural ↔ event catalyst interaction | The formula treats them as additive and independent. But an event that CONFIRMS a structural catalyst should amplify it; one that CONTRADICTS it should dampen. The current model doesn't handle this. See TEST 8 below. |
| Sector primary/secondary mapping | Defined by hand in the heatmap skill. Never validated against actual sector drivers. `nuclear_energy` as "secondary" to AI capex may be wrong — it could be primary if data center baseload demand is the thesis. |
| `catalyst_alignment` weights (0.45/0.55) | Structural gets 0.45, event gets 0.55. Justified by "events are spikes." But with only 1 event active, structural dominates — the weight is theoretically inverted from practice. |
| Watch-only trigger monitoring | Defined in the schema and taxonomy, but no skill actively checks triggers. `/catalyx-scan` could do it but doesn't explicitly. |
| Portfolio correlation across theses | Acknowledged in copper thesis metadata (`tags: ["AI", "infrastructure"]`) but no mechanism in the system to flag when two open theses are the same bet. See position sizing decision below. |

### Not reviewed at all (asumir que está roto)

| Area | Status |
|---|---|
| ClosedThesis + attribution flow | Designed in schema and thesis skill, never executed. Tax bracket logic has never been run against a real close. |
| Feedback loop / prior table | No closed theses → prior table is empty → `prior_hit_rate_catalyst_sector: null` everywhere. |
| `catalyx-scan` finding real catalysts | The skill has been defined but never run. We don't know if the WebSearch queries return useful data or noise. |
| Monthly review execution | Skill defined, never run end-to-end. |
| Attribution decomposition | Return decomposition formula (market beta / sector beta / catalyst alpha / timing luck) is designed in SPEC_v1.1 but not in any skill file. The thesis close skill asks the user for benchmark returns but doesn't compute the decomposition. |
| ETF flow data quality | The project notes that AUM ≠ net flows (price appreciation conflates). This is documented as a known flaw but no alternative computation is in place. |

---

## Position Sizing Decision: Copper + Grid = One Position

**Decision:** `copper_miners` and `grid_infrastructure_utilities` are treated as **a single position** for sizing purposes, not two independent positions.

**Rationale:** Both theses are driven by the same underlying structural theme — AI capex requiring physical infrastructure. The correlation between COPX and GRID during AI capex acceleration periods is likely high. Sizing them independently (e.g., 6% + 6% = 12% portfolio) would be overestimating diversification and underestimating concentration.

**Rule encoded here:** Before opening a second thesis, check: does this thesis share a primary structural catalyst with any open thesis? If yes, the combined allocation cannot exceed the Tier 2 ceiling (8%) unless the correlation is explicitly quantified and found to be low.

**The differentiation that does exist (and matters for execution, not sizing):**
- `copper_miners`: equity operational leverage, re-rating when analyst models update. Thesis is a narrative trade.
- `grid_infrastructure_utilities`: regulated utility model, 10-15 year demand visibility, interest rate sensitivity. Thesis is a structural capex trade.
- These have different RISK SOURCES (FCX AISC vs interest rates) and different EXIT TRIGGERS, so they are managed separately even if sized as one.

**What the pipeline needs to handle this:**
- A `portfolio_correlation_check` field or process in the thesis drafting skill
- A warning when two open theses share the same primary `catalyst_event_id`
- Aggregate position reporting: "effective AI infrastructure exposure = copper_miners + grid_infrastructure = 6% + 6% = 12% → OVER TIER 2 CEILING"

This is a gap to encode in `catalyx-thesis draft` skill and in the monthly review. Currently absent.

---

## TEST 8 — Structural ↔ Event Catalyst Interaction (new, critical)

This test has no command — it requires reasoning through the model design.

**The current model:**
```
catalyst_alignment = structural_component × 0.45 + event_component × 0.55
```
Structural and event catalysts are treated as **additive and independent**. This is wrong in at least three real cases:

**Case A — Confirmation (amplification):**
`struct_nato_rearmament` (structural, intensity 88) + `cat_20260603_nato_defense_gdp` (event, strength 91).
The event is a CONFIRMATION of the structural trend — it is not independent new information. The NATO 3.5% announcement does not add entirely new demand; it accelerates existing procurement. The current model adds `88 × 0.45 + 91 × 0.55 = 89.7`. But the "correct" answer might be closer to `88 × 1.15 = 101.2` (structural amplified by confirming event) — not a sum but a multiplier.

**Case B — Contradiction (dampening):**
`struct_ai_capex_supercycle` (structural, intensity 89) + a hypothetical `cat_deepseek_v3_efficiency_shock` (event: AI achieves 100x efficiency gain, reducing compute demand). The event CONTRADICTS the structural. The current model would still add them, producing a high combined score when the structural thesis is under threat.

**Case C — Linked catalysts:**
`struct_nato_rearmament` has `linked_event_catalyst_ids: ["cat_20260603_nato_defense_gdp"]`. The schema supports linking but the scoring formula doesn't use it. A linked event should get a higher `alignment_factor` for the structural's primary sectors, and the structural's intensity should be reviewed for upward update.

**Questions to answer during this test:**

1. Should the formula be changed from additive to multiplicative for confirmed events? What would the formula be?
2. How does the pipeline detect that an event CONTRADICTS vs CONFIRMS a structural? Is it possible to do this via LLM classification, or does it require a manual flag?
3. Should `linked_event_catalyst_ids` in the structural catalyst YAML affect the scoring? How?
4. What happens to the heatmap score for `eu_defense_prime_contractors` in 90 days when the NATO event has decayed to ~50% relevance but the structural is still at 88 intensity? Is the sector still correctly ranked?
5. Draft a proposed formula change. It should handle: confirmation amplifies, contradiction dampens, independent adds. Propose specific coefficients and explain the rationale.

**Where this needs to be encoded:**
- `catalyx/config/scoring_weights.yaml` — add interaction parameters
- `.claude/commands/catalyx-heatmap.md` — update the catalyst_alignment computation section
- `schemas/catalyst_event.json` — possibly add a `relation_to_structural` field: `confirms | contradicts | independent`

---

## Test Sequence

Run each test. After each output, answer the critique questions before moving to the next test.

---

### TEST 1 — Catalyst Dashboard
**Command:** `/catalyx-dashboard`

**What to validate:**
- [ ] All 5 structural catalysts appear, ranked by `display_priority = intensity × user_rank_multiplier`
- [ ] Indicator semaphores (🟢🟡🔴) are computed correctly against `threshold_strong` / `threshold_weak`
- [ ] The NATO event catalyst appears with correct decay calculation (days since 2026-06-03)
- [ ] Alerts section is populated (IMF COFER ind_02 should be 🟡)
- [ ] Next review dates are computed and listed

**Critique questions:**
1. Are the `intensity.current_score` values for each structural catalyst defensible? Or are they arbitrary?
2. The CB gold accumulation is ranked #1 (user_rank 1) with display_priority 117.6, but AI capex has higher raw intensity (89 vs 84). Does that user_rank decision still make sense?
3. Are there structural catalysts missing that should be active? What macro trends are not covered?
4. Do the indicator thresholds feel right? E.g., LME inventory threshold_strong at 150k tonnes — is that calibrated correctly?

---

### TEST 2 — Sector Heatmap
**Command:** `/catalyx-heatmap`

**What to validate:**
- [ ] `grid_infrastructure_utilities` ranks #1 (catalyst_alignment ~95) due to dual structural catalyst confluence
- [ ] `copper_miners` ranks #2 (~89) for the same reason
- [ ] `semiconductors_design` appears with a crowding flag despite high alignment
- [ ] Watch-only sectors appear in a separate block with trigger progress
- [ ] For each top-5 sector: a "non-obvious finding" is stated (not just repeating the catalyst description)

**Critique questions:**
1. The primary/secondary sector mapping for each structural catalyst was defined by hand in the skill instructions. Is it correct? For example: is `nuclear_energy` really only a secondary beneficiary of AI capex, or should it be primary?
2. `catalyst_alignment = structural × 0.45 + event × 0.55`. With only 1 event catalyst active (NATO), most sectors are being scored almost entirely on structural. Is the 45/55 weighting right for a structural-heavy environment?
3. Look at sectors that appear in the taxonomy but score near 0 on catalyst_alignment (e.g., `luxury_goods`, `pharma_large_cap`). Are there structural catalysts missing that should be driving them?
4. The heatmap has no momentum, flow, or valuation data. If you had to guess the top 3 sectors by full composite score, what would change vs the current catalyst-only ranking?

---

### TEST 3 — Thesis Review (existing draft)
**Command:** `/catalyx-thesis review thesis_20260603_copper_miners_datacenter_alpha`

**What to validate:**
- [ ] The skill reads the thesis JSON correctly and checks each assumption against current information
- [ ] It uses WebSearch to find actual news/data for each assumption
- [ ] It reports on each of the 4 assumptions: validated / monitoring / at_risk / invalidated
- [ ] It checks the 4 invalidation conditions against current market data
- [ ] It makes a concrete recommendation: Hold / Add / Reduce / Exit

**Critique questions:**
1. `asm_04` (China Caixin PMI < 47 for 3 consecutive months) — is this threshold right? Caixin PMI has been oscillating around 49-51. Should the threshold be higher (e.g., <49 for 2 months) to be a more sensitive early warning?
2. `inv_01` (copper spot < $8,500 for 10 days) — is $8,500 the right level? That's ~17% below current price. Is this too forgiving, or right for a structural thesis?
3. The entry trigger requires "COPX AUM +2% WoW for 2 consecutive weeks." Is ETF AUM a good entry signal, or is it a lagging indicator (money chasing price)?
4. The vehicle selection chose COPX (not UCITS) over COPX.L (UCITS, lower AUM). For a Spanish investor, was this the right call?

---

### TEST 4 — New Sector Study
**Command:** `/catalyx-sector-study gold_physical`

**What to validate:**
- [ ] The skill generates a complete SectorStudy JSON following `schemas/sector_study.json`
- [ ] `differentiation_note` clearly explains gold_physical ≠ gold_miners ≠ silver
- [ ] ETF analysis pulls from `etf_universe.yaml` (IGLN.L, WGLD.L, XGLD.DE)
- [ ] `active_catalyst_ids` correctly references `struct_cb_gold_accumulation`
- [ ] `analyst_narrative_score` is justified with a specific rationale
- [ ] File is written to `data/sector_studies/study_gold_physical.json`

**Critique questions:**
1. The skill uses WebSearch to populate current values. How reliable is this for financial data? What fields are likely to be wrong or outdated?
2. `gold_physical` and `gold_miners` share the same primary catalyst (`struct_cb_gold_accumulation`) but have different risk profiles. Does the SectorStudy differentiation capture this clearly enough for a thesis decision?
3. After reading the generated study: is there anything in the demand_drivers or risks that was missed or wrong?

---

### TEST 5 — New Thesis Draft
**Command:** `/catalyx-thesis draft grid_infrastructure_utilities`

**What to validate:**
- [ ] The skill drafts a complete thesis JSON for the #1 sector in the heatmap
- [ ] Assumptions are binary pass/fail with specific data sources — no vague statements
- [ ] Invalidation conditions have specific numbers (not "if the market deteriorates")
- [ ] Vehicle selection explains why (GRID vs INFR.L vs IQQH.DE) with TER/AUM/UCITS reasoning
- [ ] Entry trigger is concrete and checkable
- [ ] Tax section includes a warning to update `ytd_realized_gains_eur_at_entry`
- [ ] File written to `data/theses/thesis_YYYYMMDD_grid_infrastructure_*.json`

**Critique questions:**
1. Compare the grid_infrastructure thesis to the copper_miners thesis. The grid thesis should have a DIFFERENT primary catalyst story (energy_transition_grid as primary, not AI capex). Does it?
2. GRID (the ETF) is not UCITS and has only $510M AUM. INFR.L has $3.1B AUM and is UCITS but is less pure. Which should the thesis pick, and why? Does the skill make the right call?
3. What assumptions are hardest to monitor in real time for grid infrastructure? Does the thesis acknowledge that some assumptions (e.g., transformer lead times) are only available quarterly?
4. At this point you have 2 thesis drafts. Do they feel like they could coexist in a portfolio, or are they correlated (both benefit from AI capex)?

---

### TEST 6 — Catalyst Scan
**Command:** `/catalyx-scan`

**What to validate:**
- [ ] The skill runs multiple WebSearch queries covering the major catalyst types
- [ ] It correctly identifies which findings are above threshold (strength ≥ 55) vs below
- [ ] It checks existing `data/catalysts/*.json` before registering new events (no duplicates)
- [ ] At least 1-2 new CatalystEvent JSON files are written if qualifying events found
- [ ] Any structural catalyst that should update its `intensity` is flagged

**Critique questions:**
1. The scan queries are fixed in the skill file. Are the right topics covered? What macro areas are missing from the query list?
2. After the scan: are any of the events found actually acceleration events for the 5 existing structural catalysts? If so, were they correctly linked?
3. The minimum threshold is `strength_score ≥ 55`. Is this too low (too many events), too high (misses relevant signals), or about right?
4. How does the skill handle ambiguous cases — e.g., an analyst forecast vs an official announcement?

---

### TEST 7 — Indicator Update
**Command:** `/catalyx-update struct_cb_gold_accumulation ind_01 315 "WGC Q2 2026: 315T CB net purchases, above Q1"`

**What to validate:**
- [ ] The YAML is updated: `current_value: 315`, `last_value: 290` (shifted correctly), `last_date` updated
- [ ] Semaphore is correctly 🟢 (315 > threshold_strong 200)
- [ ] The skill proposes an `intensity.current_score` adjustment and asks for confirmation before writing
- [ ] `status_last_reviewed` is updated to today
- [ ] The note appears in `intensity.history` for the current quarter

**Critique questions:**
1. The skill proposes an intensity adjustment but asks the user to confirm. Is this the right behavior, or should it auto-update based on the indicator delta?
2. If ind_01 rises but ind_02 (IMF COFER) is still in yellow, the overall intensity might not change much. Does the skill reason about this correctly, or does it update intensity based on ind_01 alone?
3. After updating: does the catalyst dashboard change meaningfully if regenerated?

---

## Synthesis Questions

After running all 7 tests, answer these:

### On the pipeline design
1. **The biggest gap in Phase 0** is the absence of real-time momentum data. Without knowing if COPX or GRID is actually going up, the heatmap is half-blind. How much does this matter for actual decision-making?
2. The `catalyst_alignment` score is the only fully computed dimension. If you had to add one more dimension using only data available to Claude Code (WebSearch, no APIs), what would it be and how would you compute it?
3. The feedback loop (ClosedThesis → prior table) has no data yet. What's the minimum number of closed theses needed before the prior table is statistically meaningful? How should we handle the low-N period?

### On the thesis quality
4. The copper_miners thesis identifies the AI data center angle as the alpha. But `struct_ai_capex_supercycle` (the parent theme) has user_rank 2 and is already in the system. Is the thesis actually a derivative of `struct_ai_capex_supercycle` or genuinely new alpha?
5. **Copper + grid = one position (decided).** The pipeline currently has no mechanism to enforce this. After TEST 5, verify: does the grid thesis acknowledge the copper thesis is open? Does it cap combined allocation? If not, this is a skill gap to fix in `/catalyx-thesis draft`.

### On the structural ↔ event interaction (from TEST 8)
6. After working through the three interaction cases (confirmation, contradiction, independent), propose a revised formula. It does not need to be mathematically perfect — it needs to be implementable in the heatmap skill without Python.
7. The NATO event linked to NATO structural is the clearest test case. Recompute `eu_defense_prime_contractors` alignment score manually using: (a) current additive formula, (b) your proposed multiplicative formula. Which result feels more correct?
8. Should `relation_to_structural` be a field the user sets manually (on each event catalyst JSON), or should the skill classify it automatically via LLM? What are the failure modes of each approach?

### On the skill definitions
9. The `/catalyx-thesis draft` skill has a rule: "If you cannot state a specific mispricing, the thesis should not be drafted." Did the copper_miners thesis pass this test? Could the grid_infrastructure thesis pass it?
10. The `/catalyx-scan` skill excludes "analyst forecasts and market commentary." But the primary alpha indicator for copper_miners is exactly this — when Goldman/JPM update their models. Is this a contradiction in the pipeline design? Propose a fix: either a new event type (`analyst_model_revision`) or a different mechanism to track narrative closing.

### On what to build next in Phase 0
11. Given the position sizing decision (copper + grid = one position), which thesis should be opened first and why? The answer depends on: which has more momentum right now, which has cleaner assumptions, which ETF vehicle is more accessible.
12. Which of these would most improve the quality of analysis before Phase 1:
    - a) Fix the structural ↔ event interaction formula in `scoring_weights.yaml` and the heatmap skill
    - b) Add portfolio correlation check to `/catalyx-thesis draft`
    - c) Run `/catalyx-scan` for 4 weeks to build a real event catalyst catalogue
    - d) Close the copper_miners thesis after 4 weeks and run the full ClosedThesis flow (tests the attribution and tax engine)

---

## Known Gaps — Prioritized

### 🔴 Design gaps (affect correctness of analysis)

| Gap | Problem | Where to fix |
|---|---|---|
| Structural ↔ event catalyst interaction | Additive formula treats confirmation and contradiction the same. A NATO event that confirms NATO structural should amplify, not just add. | `scoring_weights.yaml` + `catalyx-heatmap.md` skill |
| Portfolio correlation not enforced | Copper + grid = same bet. Pipeline has no mechanism to flag or cap combined allocation across correlated theses. | `catalyx-thesis.md` draft skill + monthly review |
| `relation_to_structural` field missing | `CatalystEvent` has no field to declare whether it confirms, contradicts, or is independent of a linked structural catalyst. | `schemas/catalyst_event.json` + `catalyx-scan.md` |
| Sector primary/secondary mapping | Defined by hand in heatmap skill. Not derived from taxonomy or catalyst files. Will drift as catalysts are added. | `catalyx-heatmap.md` skill — needs a formal mapping table |
| `analyst_model_revision` missing event type | The copper miners alpha closes when Goldman/JPM update models — but the scan skill explicitly excludes analyst commentary. Gap between what we track and what the thesis depends on. | `catalyst_taxonomy.yaml` + `catalyx-scan.md` |

### 🟡 Data quality gaps (affect completeness of analysis)

| Gap | Problem | Where to fix |
|---|---|---|
| No momentum data | Heatmap composite is catalyst_alignment only. Sectors with strong catalysts but poor momentum would be incorrectly ranked. | Phase 1: `data/market_data.py` with yfinance |
| ETF AUM ≠ net flows | AUM changes conflate price appreciation with actual share creation/redemption. Flow signal is noisy. | Phase 1: use shares_outstanding × NAV delta |
| `ytd_realized_gains_eur_at_entry = 0` | Always a placeholder. If user has other gains in the year, the tax bracket is wrong. | Manual update required every time — no automation in Phase 0 |
| Indicator data staleness | Some structural catalyst indicators were set at file creation and may not reflect current reality. No automated staleness check in Phase 0. | `catalyx-dashboard.md` — already flags overdue indicators, but data must be updated manually |

### 🟢 Known but acceptable in Phase 0 (defer to Phase 1)

| Gap | Why acceptable now |
|---|---|
| No prior table | No closed theses yet. Expected. Low-N handling: use uninformative prior (0.5) until N ≥ 10. |
| LLM classification drift | Model version pinned in CLAUDE.md for Phase 1. In Phase 0, all classification is reviewed by user before filing. |
| China PMI threshold (47.0) | Conservative by design for a first thesis. Revisit after first thesis review — may raise to 49.0. |
| No attribution decomposition | ClosedThesis flow not yet tested. Attribution formula is designed in SPEC_v1.1 but not in skill yet. |
| No feedback loop | Requires at least 5 closed theses. Not possible yet. |

---

## After Each Test Cycle — Update This File

When a test reveals a new gap or validates a design decision, add a row here:

| Date | Test | Finding | Action taken |
|---|---|---|---|
| 2026-06-03 | Design review | Copper + grid are one position, not two | Encoded in position sizing decision section above |
| 2026-06-03 | Design review | Structural ↔ event formula is additive, should be multiplicative for confirmation | Added TEST 8, flagged in 🔴 gaps |
| — | — | — | — |

---

*Generated from CATALYX project state as of 2026-06-03.*
*Last reviewed: 2026-06-03.*
*Run all 8 tests in a fresh session. Update the table above with each finding.*
