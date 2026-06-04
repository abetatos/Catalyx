# catalyx-monthly-review

Run the full monthly review pipeline. Executes all sub-analyses and produces a consolidated monthly review report.

Run on: first Monday of each month, or after a significant macro event.

## PIPELINE ORDER — CRITICAL

The order below is mandatory. Each step provides data that the next step requires.
Do NOT run heatmap before sector studies. Do NOT run thesis review before the dashboard.

```
Step 0:  Macro & Geopolitical Context (WebSearch — always first)
Step 1:  Catalyst Scan (Pass 1: Discovery → gaps | Pass 2: Classification → events)
Step 2:  Structural Catalyst Updates → refresh stale indicators
Step 3:  Sector Studies → refresh priority sectors (BEFORE heatmap)
Step 4:  Catalyst Dashboard
Step 5:  Sector Heatmap (requires updated sector studies)
Step 6:  Open Thesis Reviews
Step 7:  Portfolio Correlation Check  ← MUST run before any thesis draft decision
Step 8:  Tax Snapshot
Step 9:  Thesis Draft Decision  ← uses Step 5 heatmap + Step 6 reviews + Step 7 correlation
Step 10: Stale Indicators Check
Step 11: Watch-Only Trigger Progress
Step 12: Taxonomy Gap Review → promote or reject pending proposals
```

---

## Steps

### Step 0 — DB Rebuild + Macro & Geopolitical Context

First, rebuild the DB index from all JSON files written since the last session:
```
uv run python -c "from catalyx.store import init_all; init_all()"
```

Then run WebSearch for macro context (BEFORE reading any YAML files):

Run these WebSearch queries BEFORE reading any YAML or JSON files. The project files contain
data from last month — the web searches establish what is TRUE TODAY.

**Mandatory searches (run all):**

```
Macro / Central Banks:
- "Fed interest rate decision [MONTH YEAR]"
- "ECB rate policy [MONTH YEAR]"
- "US CPI inflation [MONTH YEAR]"
- "USD DXY dollar index [MONTH YEAR]"

Geopolitical:
- "Russia Ukraine war news [MONTH YEAR]"
- "NATO defense spending update [MONTH YEAR]"
- "China Taiwan geopolitical [MONTH YEAR]"
- "US China trade tariffs [MONTH YEAR]"

Commodities:
- "LME copper price [MONTH YEAR]"
- "gold price USD [MONTH YEAR]"
- "WGC central bank gold purchases [MONTH YEAR]"
- "oil price OPEC [MONTH YEAR]"

Active catalyst checks (one per active structural catalyst):
- "[catalyst keyword] latest news [MONTH YEAR]"
  e.g. "hyperscaler AI capex guidance [MONTH YEAR]"
  e.g. "NATO defense budget European [MONTH YEAR]"
  e.g. "LME copper inventory [MONTH YEAR]"
```

**Output of Step 0:** A bullet-point summary of current macro/geopolitical state.
Compare this to the most recent report — note any deltas from the project's stored data.
Flag any indicator where the WebSearch value differs significantly from the YAML current_value.

---

### Step 1 — Catalyst Scan

Follow all steps in `catalyx-scan` skill. The scan runs two passes automatically:

**Pass 1 (Discovery):** broad market queries with no taxonomy dependency. Output: new or updated `data/taxonomy_proposals/*.json` files. This pass catches investment themes the taxonomy doesn't cover yet.

**Pass 2 (Classification):** taxonomy-led queries. Output: new `data/catalysts/*.json` CatalystEvent files above strength threshold 55.

After the scan, check each existing structural catalyst: should intensity be revised based on Step 0 findings?

---

### Step 2 — Structural Catalyst Updates

For any indicator flagged as stale OR where Step 0 found a value different from the YAML:
- Run `/catalyx-update <struct_id> <ind_id> <new_value> "<source note>"` for each
- After updating, recompute intensity.current_score using the algorithmic formula:
  `intensity = round(indicator_avg × trend_factor, 1)` (see scoring_weights.yaml)
- Do NOT manually assign intensity — derive it from the indicators

---

### Step 3 — Sector Studies (PREREQUISITE FOR HEATMAP)

For each sector in the top-10 catalyst_alignment ranking that lacks a current sector study
OR whose sector study is > 30 days old:
- Run `/catalyx-sector-study <sector_id>` using WebSearch for current data
- Priority order: sectors with open theses first, then highest catalyst_alignment sectors

**Minimum sector studies required before heatmap:**
- Any sector where a thesis draft is being considered
- Any sector in top-5 catalyst_alignment without an existing study
- If time-constrained: write partial studies (study_type: "partial") with key metrics only

---

### Step 4 — Catalyst Dashboard

Follow all steps in `catalyx-dashboard` skill.
Write to `data/reports/catalyst_dashboard_YYYYMMDD.md`.

---

### Step 5 — Sector Heatmap

Follow all steps in `catalyx-heatmap` skill.
Heatmap reads sector_study data — this is why Step 3 must come first.
Write to `data/reports/heatmap_YYYYMMDD.md`.

---

### Step 6 — Open Thesis Reviews

For each file in `data/theses/*.json` where `status == "open"`:
- Follow the `review` sub-flow from `catalyx-thesis` skill
- Use WebSearch findings from Step 0 to check each assumption against current data
- Summarize in one row: thesis_id, days_open, assumption_status (N/N validated), recommended_action

---

### Step 7 — Portfolio Correlation Check

Run BEFORE any thesis draft decision (Step 9 depends on this output):

```
uv run python -m catalyx.store.thesis_repo summary
```

- List all open/draft theses and their primary structural catalyst IDs
- For each potential new thesis (sectors in top-5 with no open thesis), compute:
  - Is the primary structural catalyst shared with any open thesis?
  - `combined_allocation = existing_open_pct + proposed_new_pct`
  - If combined > Tier 2 ceiling (8%): flag as BLOCKED unless explicitly authorized
- Record this check in the monthly report

---

### Step 8 — Tax Snapshot

```
uv run python -m catalyx.store.thesis_repo tax-snapshot
```

If the DB is not current (new closed theses written this session):
```
uv run python -m catalyx.store.thesis_repo import-dir data/theses
uv run python -m catalyx.store.thesis_repo tax-snapshot
```

Show: total realized gains, tax paid YTD, current marginal bracket, estimated full-year tax if open theses close at current mark-to-market.
If no closed theses: state YTD gains = 0.

---

### Step 9 — Thesis Draft Decision

Based on heatmap (Step 5), thesis reviews (Step 6), and correlation check (Step 7):
- List sectors that rank in top-5 AND have no open thesis: are any ready for a draft?
- Only propose a draft if Step 7 confirmed combined allocation is within tier ceiling

---

### Step 10 — Stale Indicators Check

For each structural catalyst, for each indicator:
- Compute: days since `last_date` vs `check_frequency`
- Flag if overdue: daily > 3 days, weekly > 10 days, monthly > 40 days, quarterly > 95 days
- List all overdue in a single table (this step catches what Step 2 might have missed)

---

### Step 11 — Watch-Only Trigger Progress

For each sector with `watch_only: true`:
- Use WebSearch to check if any `watch_triggers` may have fired since last review
- Report: no change / trigger approaching / trigger fired

---

### Step 12 — Taxonomy Gap Review

Load all gap proposals from DB:
```
uv run python -m catalyx.store.catalyst_repo summary
```
(The taxonomy gap proposals section shows all non-promoted, non-rejected gaps.)

For each proposal, update the file mechanically:
- If detected again this cycle: increment `signal_count`, append a new entry to `evidence[]` with today's date, update `last_seen`, set `status: accumulating`.
- If NOT detected this cycle: leave unchanged. Note it in the report as "not seen this cycle".

Then present the full gap table to the user. **Do not promote or reject automatically** — the user decides.

The table is purely informational. The user reads it and says "promote X" or "reject Y". No automatic criteria.

**Promotion action (only when user explicitly says so):**
- Set `proposed_sector_id` and `promoted_date` in the gap file, set `status: promoted`
- Add sector entry to `catalyx/config/sector_taxonomy.yaml`
- Add ETF entry to `catalyx/config/etf_universe.yaml` (or mark as gap if no ETF found)
- Bump `schema_version` in `sector_taxonomy.yaml`

**Rejection action (only when user explicitly says so):**
- Set `status: rejected`, fill `rejection_reason`

---

### Output

Write consolidated monthly review to `data/reports/monthly_review_YYYYMMDD.md`:

```markdown
# CATALYX — Monthly Review YYYY-MM-DD

## 0. Macro & Geopolitical Context
[Bullet summary of current state. Deltas vs prior month. Indicator discrepancies flagged.]

## Executive Summary
[3-5 bullets: most important changes. At least one NON-OBVIOUS finding required.]

## 1. Catalyst Updates
[New events registered. Structural indicators updated. Intensity recomputations.]

## 2. Sector Studies Refreshed
[List of sector studies run this cycle. Partial studies flagged.]

## 3. Catalyst Dashboard
[Link to catalyst_dashboard_YYYYMMDD.md + key changes vs prior]

## 4. Sector Heatmap
[Link to heatmap_YYYYMMDD.md + ranking changes vs last month]

## 5. Open Theses
| Thesis | Days open | Assumptions (N/N ok) | Action |
|---|---|---|---|

## 6. Portfolio Correlation
| Open theses | Shared catalyst | Combined % | Status |
|---|---|---|---|

## 7. Thesis Draft Decisions
[Sectors proposed for new draft. Blocked drafts (correlation ceiling exceeded) listed separately.]

## 8. Tax Snapshot YTD
| Metric | Value |
|---|---|
| Realized gains | €X |
| Tax paid | €X |
| Current marginal bracket | X% |
| Projected YTD if open positions close at mark | €X |

## 8. Stale Indicators
| Catalyst | Indicator | Last updated | Overdue by |
|---|---|---|---|

## 9. Watch-Only Triggers
| Sector | Triggers met | Change |
|---|---|---|

## 10. Taxonomy Gap Review
| Gap ID | Theme | Signal count | Weeks persistent | ETF found | Status | Action |
|---|---|---|---|---|---|---|

## Pending Actions
[Prioritized. Format: 🔴 HIGH / 🟡 MEDIUM / 🟢 LOW]
```

Print to chat: "Monthly review complete. Key findings: [3 bullets]. Full report: data/reports/monthly_review_YYYYMMDD.md"

---

## Rules

- **Step 0 is mandatory.** Never start by reading YAML files. WebSearch first — project data is always one month stale.
- **Pass 1 (Discovery) runs before Pass 2 (Classification) in Step 1.** Never read `sector_taxonomy.yaml` during the Discovery pass — the point is to find what the taxonomy misses.
- **Sector studies before heatmap.** Never run heatmap without refreshing sector studies for the top-5 sectors.
- The Executive Summary must contain at least one NON-OBVIOUS finding. If everything is "no change", state that explicitly.
- Open thesis review must make a concrete recommendation (Hold / Add / Reduce / Exit). "Monitor" is not a recommendation.
- Stale indicators are not optional to flag — they are data quality issues that corrupt downstream analysis.
- **Taxonomy promotions require explicit user confirmation** before writing to `sector_taxonomy.yaml`. Never promote automatically.
- **Portfolio correlation check (Step 7) is a prerequisite for any draft decision (Step 9).** Never propose a new thesis without first checking combined allocation against open theses sharing the same primary structural catalyst.
- **AI SCORING RULE:** Never assign `intensity.current_score` manually. Always recompute from indicator semaphores using the formula in `scoring_weights.yaml`. If a user_override is needed, document the reason in `computation_note`.
