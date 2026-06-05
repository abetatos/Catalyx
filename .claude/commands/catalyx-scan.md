# catalyx-scan

Scan for macro catalysts and emerging investment themes. Runs two sequential passes:

- **Pass 1 — Discovery** (market-led, no taxonomy dependency): detects what is actually moving in the market and flags themes not covered by the current taxonomy.
- **Pass 2 — Classification** (taxonomy-led): searches for new events within known sectors and registers CatalystEvent JSON files.

---

## Pass 1 — Discovery Pass

**Goal:** Find investment themes the taxonomy doesn't know about yet. No taxonomy file is read in this pass.

### Step D1 — Load existing gap proposals

```
uv run python -m catalyx.store.catalyst_repo summary
```
The taxonomy gap proposals section shows all existing gaps. Use `get <gap_id>` for full detail on any specific gap.

### Step D2 — Run broad market WebSearch queries

These queries are intentionally generic — they are designed to surface what the market is pricing, not what the taxonomy already knows about.

```
"best performing ETF sectors this week"
"sector ETF biggest movers [MONTH YEAR]"
"sector ETF inflows outflows [MONTH YEAR]"
"emerging investment theme [MONTH YEAR]"
"new sector rally [MONTH YEAR]"
"analyst initiates coverage [sector] [MONTH YEAR]"
"[MONTH YEAR] sector rotation trade"
```

### Step D3 — Identify themes from signal output

For each significant signal found (ETF move, news cluster, analyst initiation):

1. **Name the theme** in plain language: "AI memory chips / HBM demand", "European defense rearmament", etc.
2. **Classify signal type**: `price_outlier | flow_anomaly | news_volume_spike | analyst_coverage_initiation | earnings_surprise | broad_market_query`
3. **Check existing taxonomy from prior session context** — do NOT read `sector_taxonomy.yaml` here, that would bias Pass 1 toward known sectors: does this theme map cleanly to a sector you already know?
   - If yes → skip (it will be handled in Pass 2)
   - If no → proceed to Step D4
4. **Check existing gap proposals**: does this theme match a gap already in `data/taxonomy_proposals/`?
   - If yes → update `signal_count`, `last_seen`, and add new evidence entry. Do not create a duplicate file.
   - If no → create a new proposal in Step D4

### Step D4 — Write TaxonomyGapProposal

For each theme with no matching sector and no existing gap file:

- Write to `data/taxonomy_proposals/gap_YYYYMMDD_<slug>.json` following `schemas/taxonomy_gap_proposal.json`
- Fields to infer from the discovery signals:
  - `label_inferred`: what would this sector be called?
  - `differentiation_inferred`: why is this NOT covered by adjacent existing sectors? (This is the critical field — if it can't be differentiated, don't create a gap.)
  - `closest_existing_sectors`: which existing sectors are most similar, and why they're insufficient
  - `etf_candidates`: search for `"<theme name> ETF"` — is there a pure-play vehicle? If not, what proxies exist?
  - `investability_assessment`: based on ETF search result
- Set `signal_count: 1`, `status: "proposed"`
- Set `promotion_threshold.current_status` to a plain-English description of what's missing for promotion

**Only create a gap proposal if:**
- The theme caused a measurable market move (ETF >3% week, or news volume spike, or analyst coverage initiation)
- It cannot be adequately captured by any existing sector + ETF in the current taxonomy
- It has a plausible investment thesis (not just interesting news)

---

## Pass 2 — Classification Pass

**Goal:** Find new discrete events within known sectors and register them as CatalystEvent JSON files.

### Step C1 — Load taxonomy and existing catalysts

Read config files (source of truth):
- `CLAUDE.md` — catalyst ID format, schema version
- `schemas/catalyst_event.json` — all required fields
- `catalyx/config/catalyst_taxonomy.yaml` — valid catalyst_type and catalyst_subtype values
- `catalyx/config/sector_taxonomy.yaml` — sector IDs for tagging

Load runtime data via the repo summaries (one digest instead of reading individual JSON/YAML files):
```
uv run python -m catalyx.store.catalyst_repo summary
uv run python -m catalyx.store.structural_catalyst_repo summary
```
Use `get <id>` on either repo for full detail when a specific record is needed.

### Step C2 — Run sector-specific WebSearch queries

```
"central bank policy rate decision" last 7 days
"defense spending NATO announcement" last 30 days
"semiconductor export controls chips" last 30 days
"copper supply disruption mine" last 30 days
"AI data center capex investment announced" last 30 days
"ECB Federal Reserve forward guidance" last 14 days
"geopolitical escalation sanctions" last 14 days
"commodity supply OPEC production" last 14 days
```

**Analyst model revision queries — run every scan (thesis close signal):**
```
"Goldman Sachs JPMorgan Morgan Stanley sector research report [MONTH YEAR]"
"sell-side analyst initiates upgrades sector [MONTH YEAR]"
"copper price target revision Goldman JPMorgan [MONTH YEAR]"
"analyst commodities model update copper gold [MONTH YEAR]"
"Wall Street copper outlook revision [MONTH YEAR]"
```

These detect `corporate_event / analyst_model_revision` — the signal that a thesis mispricing is closing. Classification rule: if ≥2 of {GS, JPM, MS, BofA, UBS} publish revised sector models in the same 30-day window with ≥10% change in sector revenue estimate or price target, register as `corporate_event / analyst_model_revision`. This is a **thesis exit trigger** — flag explicitly in the "Structural catalyst flags" table and note which open thesis it affects.

Also run one query per sector that appeared in the Discovery Pass output, to check for discrete events.

### Step C3 — Classify and score each significant result

For each significant result found:
- **Novelty check**: does this substantially overlap with any existing catalyst in `data/catalysts/`? If yes, skip.
- **Structural match**: does this accelerate an existing structural catalyst in `catalyx/config/structural_catalysts/`? If yes, set `linked_structural_catalyst_ids` and lower the novelty_score.
- **Classify**: assign `catalyst_type` and `catalyst_subtype` from `catalyst_taxonomy.yaml`
- **Score**:
  - `novelty_score` (0-100): how different from prior catalysts of same type
  - `strength_score` (0-100): magnitude × consensus_surprise × confirmation
  - `consensus_surprise` (0-1): was this expected by the market?
  - `is_priced_in_estimate` (0-1): based on asset price reaction since news broke

### Step C4 — Write CatalystEvent files

Only create a CatalystEvent file if `strength_score ≥ 55`. Below that, mention in summary but do not write a file.

For each qualifying event, write to `data/catalysts/cat_YYYYMMDD_<keyword>.json` following `schemas/catalyst_event.json`. Set `status: "active"`.

The JSON file IS the registration — `catalyst_repo summary` / `get` read `data/catalysts/` and
`data/taxonomy_proposals/` directly, so a file written this session is queryable immediately. No import step.

---

## Output

Present a unified summary table after both passes:

```
## Catalyx Scan — YYYY-MM-DD

### Pass 1 — Discovery: New taxonomy gaps detected
| Gap ID | Theme | Signal type | Closest existing sector | ETF found? | Action |
|---|---|---|---|---|---|

### Pass 1 — Discovery: Existing gaps updated
| Gap ID | Theme | Previous signal_count | New signal_count | Weeks persistent |
|---|---|---|---|---|

### Pass 2 — Classification: New catalysts registered (strength ≥ 55)
| ID | Type | Strength | Sectors affected | Written? |
|---|---|---|---|---|

### Pass 2 — Classification: Events below threshold (not written)
| Event | Strength | Reason skipped |
|---|---|---|

### Structural catalyst flags
| Structural ID | New event | Recommended intensity update |
|---|---|---|

### Analyst model revision flags (thesis exit signals)
| Catalyst ID | Banks revised | Sector | Affected open thesis | Action |
|---|---|---|---|---|
```

If any structural catalyst's `indicators` appear to have changed materially, flag it explicitly:
"Recommend updating `intensity.current_score` for `<id>` — evidence: [cite]".

---

## Rules

- **Pass 1 runs before Pass 2.** Never read `sector_taxonomy.yaml` during Pass 1 — the point is to find what the taxonomy misses.
- **All dates in evidence entries must be absolute (YYYY-MM-DD).** Never write relative dates ("last week", "this month") — the gap files persist across sessions and relative dates become meaningless.
- **Promotion is always a user decision.** Never set `status: promoted` or suggest promotion automatically. Only present the evidence table; the user decides.
- Never fabricate sources. If WebSearch returns no results for a query, say so.
- If a search result is paywalled, note the headline only and mark `source.url` with the URL but leave `description` as a summary of what is inferable from the headline.
- Do not register opinion pieces, analyst forecasts, or market commentary as CatalystEvents. Only register: policy decisions, official announcements, data releases, confirmed corporate events.
- Always set `decay_halflife_days` from `catalyst_taxonomy.yaml` based on `catalyst_type`.
- A gap proposal requires genuine differentiation. If the theme fits an existing sector even imperfectly, do not create a gap — instead tag it to the closest sector and note the imperfect fit.
