# catalyx-review

Run the analysis & review pipeline (scan → update → studies → dashboard → heatmap → portfolios →
opportunities → position reviews → tax). Produces a consolidated review report. This is the
ANALYTICAL cycle — it is **independent of operating**: opening/closing positions is done anytime
via `/catalyx-open` and `/catalyx-close`, NOT here. The review recommends; it never trades.

Usage:
- `/catalyx-review` or `/catalyx-review scheduled` — full periodic review (run monthly, or ad-hoc).
- `/catalyx-review event:<catalyst_id>` — **event-driven**: a punctual catalyst fired and you want
  to react now, not wait for the periodic cycle.

**Trigger modes:**
- `scheduled` (default): run every step below, full universe.
- `event:<catalyst_id>`: run only the steps the event touches — Step 0/1 (lightweight refresh of
  THAT catalyst only — search its keyword, note strengthen/weaken/invalidation; do NOT run the full
  `/catalyx-scan`), Step 2 (update the affected catalyst's indicators), Step 3 (re-study only the
  sectors that catalyst drives), Steps 5/5b/5c (re-score + regime + dislocation), Step 6 (review open
  positions attributed to that catalyst). Skip the full-universe Steps 11–12 unless the event
  surfaces a taxonomy gap. State at the top of the report which trigger ran and why each skipped step
  was skipped.

## PIPELINE ORDER — CRITICAL

The order below is mandatory. Each step provides data that the next step requires.
Do NOT run heatmap before sector studies. Do NOT run position review before the dashboard.

```
Step 0/1: Catalyst Scan & Macro Context — run /catalyx-scan FIRST (it is the macro front door)
          scheduled → one scan returns it all: C0 macro/big-economy context + Pass 2 refresh of
                      existing catalysts + Pass 1/2 new gaps & events. Consume its output.
          event:<id> → SKIP the full scan; do a lightweight refresh of THAT catalyst only
                      (search its keyword, note strengthen/weaken/invalidation)
Step 1.5: Freshness & Lifecycle GATE (stale-indicator audit + catalyst lifecycle)  ← BEFORE scoring
Step 2:   Structural Catalyst Updates → refresh the indicators flagged stale in 1.5
Step 3:   Sector Studies → refresh priority sectors (BEFORE heatmap)
Step 4:   Catalyst Dashboard
Step 5:   Sector Heatmap (requires updated sector studies)
Step 5b:  Model Portfolios + NAV vs S&P500 (after the run is recorded)
Step 5c:  Opportunities & Rotation (regime watch + dislocation lens — recommendations, not trades)
Step 6:   Open Position Reviews (movements + risk_discipline + regime)
Step 7:   Catalyst Exposure / Correlation Check  ← informs any open-position recommendation
Step 8:   Tax Snapshot (realized YTD from closing movements)
Step 9:   Position Open Recommendations  ← uses Step 5 heatmap + Step 6 reviews + Step 7 exposure
Step 11:  Watch-Only Trigger Progress
Step 12:  Taxonomy Gap Review → contextualize each pending proposal, then ASK the user (promote/reject/defer)
```

> **Why 1.5 runs BEFORE scoring (fixed 2026-06-05):** the stale-indicator audit and the
> catalyst lifecycle (archive spent / dormant weak catalysts) were previously Step 10 — AFTER
> the heatmap recorded the run. That meant the run scored `catalyst_alignment` on un-pruned,
> stale catalyst state (e.g. sectors ranking top-10 on indicators 100–500 days old, or a
> fully-priced-in event still contributing near-full strength). Freshness must GATE the
> scoring, not trail it: prune/refresh first, then score on clean state. The old "Step 10"
> body now lives under the "Step 1.5" heading below.

---

## Steps

### Step 0/1 — Catalyst Scan & Macro Context

The review's FIRST action. `/catalyx-scan` is the macro front door — one scan establishes what is
TRUE TODAY and feeds the whole early pipeline. The web searches always come before trusting any
stored value (project files are a month stale).

**`scheduled` — run `/catalyx-scan` first, then consume its output.** The scan returns, in one pass:
- **C0 macro & big-economy context** — central banks, big economies, commodities, key geopolitics
  (the generic Trump / US / Europe framings live there — broad queries surface more ideas).
- **Pass 2 Refresh** — a per-catalyst delta for every registered catalyst (strengthen / weaken /
  invalidation trigger). This is the input to Step 1.5 (lifecycle gate) and Step 2 (`/catalyx-update`).
- **Pass 1 + Pass 2 discovery** — new `data/taxonomy_proposals/*.json` (themes the taxonomy misses)
  + new `data/catalysts/*.json` CatalystEvents above strength 55.

Do NOT repeat the scan's searches here — read its summary tables (C0 context, refresh deltas, new
gaps/events) and carry them forward.

**`event:<catalyst_id>` — SKIP the full scan; do a lightweight refresh of THAT catalyst only.**
A punctual event doesn't warrant a full-universe Discovery Pass. Instead:
1. `uv run python -m catalyx.store.catalyst_repo get <catalyst_id>` (or `structural_catalyst_repo`)
   for its keyword + stored state.
2. One or two WebSearches on that keyword, e.g. `"<catalyst keyword> latest news [MONTH YEAR]"`.
3. Note the delta: did it STRENGTHEN, WEAKEN, or hit an **invalidation trigger**? Feed Step 1.5 / Step 2.

**Output of Step 0/1:** the macro context bullets + the per-catalyst refresh deltas (flag any that
should move to `invalidated`/`weakening`) + any brand-new theme/event surfaced.

---

### Step 2 — Structural Catalyst Updates

For any indicator flagged as stale OR where Step 0/1 found a value different from the YAML:
- Run `/catalyx-update <struct_id> <ind_id> <new_value> "<source note>"` for each
- After updating, recompute intensity.current_score using the algorithmic formula:
  `intensity = round(indicator_avg × trend_factor, 1)` (see scoring_weights.yaml)
- Do NOT manually assign intensity — derive it from the indicators

---

### Step 3 — Sector Studies (PREREQUISITE FOR HEATMAP)

**Default coverage: ALL investable sectors.** The pipeline studies every investable sector
in the taxonomy each cycle (not just the catalyst-driven top-N) so that opportunities in
sectors without a structural catalyst — uranium, silver, lithium, etc. — are never missed.

**Freshness skip (rotation):** skip any sector whose existing study `last_updated` is ≤ 7 days
old (already fresh this cycle). Study everything else. This naturally rotates coverage and
avoids paying to re-study a sector analyzed days ago.

Build the work list — list investable sectors and their study freshness:
```
uv run python -m catalyx.scorer.sector_scorer --universe --json   # gives the full investable sector_id list
uv run python -m catalyx.store.sector_study_repo stale --days 7    # which studies are stale/missing
```
For each investable sector_id whose study is missing or > 7 days old, run a sector study.

**Parallelize with subagents.** Sector studies are independent and WebSearch-bound, so fan
them out across background subagents (Agent tool, `subagent_type: general-purpose`,
`model: sonnet`, `run_in_background: true`), one or a few sectors per agent. Each agent must
follow `.claude/commands/catalyx-sector-study.md` for its assigned sector(s) and Write the
JSON to `data/sector_studies/study_<sector_id>.json`.

> Subagents Write the study JSON directly into `data/sector_studies/`. That file IS the
> registration — `sector_study_repo summary`/`get`/`stale` read the directory directly, so no
> import step is needed (there is no database).

**Cost note:** a single sonnet sector study runs ≈ 45–50k tokens / 6 WebSearches /
~3–3.5 min wall-clock. Studying the full ~46-sector universe is a material spend — fan out
in parallel batches and let freshness-skip shrink the list on subsequent cycles.

**Time-constrained fallback:** if a full run is not feasible, prioritize (a) sectors with an
open position, (b) top catalyst_alignment sectors, (c) highest-momentum sectors from the latest
snapshot; write `study_type: "partial"` for the rest.

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

### Step 5b — Model Portfolios + NAV vs S&P500

After the heatmap records the run (so `sector_snapshot` exists in the lake), rebuild the model
portfolios from this run and refresh their NAV vs the market. This is what feeds the dashboard's
Carteras tab (4 strategies + "¿batimos mercado?").

```bash
# build the 4 strategy portfolios from the latest run → lake portfolio_holding (records entry_price)
uv run python -m catalyx.execution.portfolio build-all

for p in catalyx momentum equal_weight low_crowding; do
  # reference: trailing-backtest of CURRENT holdings vs SPY (hypothetical — shown only until live accrues)
  uv run python -m catalyx.execution.nav_engine model "$p" --backtest-days 180
  # the headline: LIVE walk-forward track record — chains each run's ACTUAL holdings from
  # track_record.yaml inception (no look-ahead). Run AFTER the backtest (live merges, backtest overwrites).
  uv run python -m catalyx.execution.nav_engine live "$p"
done

# REAL book NAV vs SPY — the actual-money curve from the movement files, indexed 100 at inception.
# Grows one trading day at a time; this is what the Positions "Performance vs S&P 500" tab compares
# against the model strategies (same measure: return vs SPY + vol/Sharpe/maxDD). Run every review.
uv run python -m catalyx.execution.nav_engine real real --benchmark SPY
```

The **live** curve is the real track record (`mode='live'`): it starts empty at inception and grows
one run at a time, so each review adds a rebalance point. The dashboard shows the live curve once it
has ≥2 points; until then it labels the book *accruing* and shows the backtest for reference only.
Report in the summary: each strategy's return and whether it beat SPY (`vs_benchmark_pct`).

**Portfolio rotation targets (real book).** Derive the held sectors from the real positions and
compute diversifiers ANCHORED to them (healthy, least-correlated to what you already own → where to
add next without doubling the same bet). Persists the `portfolio_rotation` lake table → Positions page.
```bash
held=$(uv run python -m catalyx.store.movement_repo positions | python -c "import sys,json;print(','.join(sorted({h['sector_id'] for h in json.load(sys.stdin)['holdings']})))")
uv run python -m catalyx.scorer.dislocation --anchor-sectors "$held"
```
Strategies live in `catalyx/config/portfolios/*.yaml`; NAV math/benchmark in `nav_engine.py`.

---

### Step 5c — Opportunities & Rotation (regime watch + dislocation lens)

This is **step 12 of the `catalyx-heatmap` skill** — run it here (the run is recorded, so
`regime_state` is in the lake) and surface its findings in the monthly report. **Recommendations
for your judgement, never auto-trades.** Python computes the facts; you make the calls.

```bash
uv run python -m catalyx.scorer.catalyst_scorer --all --json   # regime_state + persistence dossier per sector
uv run python -m catalyx.thesis.structural_monitor --all       # fundamentals health (flags degrading → breaking)
uv run python -m catalyx.scorer.dislocation --window 5 --json  # opportunities (panic dips) + diversifiers (rotation)
uv run python -m catalyx.scorer.entry_timing --all --json      # entry-timing overlay (micro-tension + event overhang)
```

- **Regime watch.** `contested` = watch only (no action); a single `clustered_one_shock` development
  is noise. Escalate to a regime call ONLY when `review_recommended` (dispersed multiples) OR a
  structural is `degrading` — then WebSearch the macro context and decide. Time-independent: same
  verdict at any cadence.
- **Opportunities.** Sectors that fell hard but are `intact` + catalyst-confirmed and whose drop is
  mostly CONTAGION (low `idiosyncratic_pct`) → panic dips. WebSearch each to rule out a hidden cause
  behind the idiosyncratic residual before treating it as an entry.
- **Diversifiers.** Healthy sectors with LOW correlation to the stressed cluster → where to rotate
  without re-buying the same correlated bet.
- **Entry timing (the *when*, complementary to dislocation's *whether*).** For each top-ranked /
  opportunity sector, read `entry_timing`: a `micro_timing_state` + `suggested_verdict`. Flag any
  high-ranked sector with bad near-term timing — `falling` (knife not yet based →
  `wait_stabilize`), `overbought` (overextended up), or an **event overhang** (`wait_event`: a
  discrete CatalystEvent with an `event_date` in the window — e.g. a peer mega-IPO whose flow could
  dump the read-across name). The module surfaces the fact; the adverse-vs-bullish call on an
  overhang is yours (WebSearch). `basing` → `scale_in`; `neutral` → no timing objection. This is
  a recommendation about the execution window, never a trade and never a change to the composite.

---

### Step 6 — Open Position Reviews

Load the live book and the catalyst attribution:
```
uv run python -m catalyx.store.movement_repo positions
uv run python -m catalyx.store.lake_query ledger
```
For each open position, read its opening movement in `data/movements/` and review its
`risk_discipline`:
- For each `assumptions[]`: use Step 0/1 WebSearch findings to assess `holding` / `weakening` /
  `violated` — cite specific evidence (date, source, value).
- For each `invalidation[]`: check whether the stop/condition has been breached (price/inventory/
  rate). A `market_data` stop is checkable from the latest snapshot.
- Cross the position's attributed catalyst(s) against this run's `regime_state` (Step 5c): if a
  driving catalyst is `contested`/`breaking`, flag the position.
- Summarize in one row: sector, days_open, assumptions (N/N holding), regime of driving catalyst,
  recommended_action (Hold / Add / Reduce / Exit). "Monitor" is not a recommendation.
- **Recommend only.** Any actual Add/Reduce/Exit is executed by the user via `/catalyx-open` or
  `/catalyx-close` — never written here.

---

### Step 7 — Catalyst Exposure / Correlation Check

Informs the open-position recommendations (Step 9). Read exposure already attributed per catalyst:
```
uv run python -m catalyx.store.lake_query ledger
```
- For each catalyst, the ledger gives `invested_eur` and the sectors carrying it.
- Read `correlated_catalyst_cap` from `scoring_weights.yaml` (`max_combined_pct`, default 0.20;
  `enforcement`, default "warn").
- For each potential new position (top-5 sector with no open position on that catalyst), compute:
  - Does it share a primary catalyst with existing exposure?
  - `combined_exposure_pct = existing_catalyst_pct + proposed_new_pct` (as a % of the book).
  - If combined > `max_combined_pct`: flag ⚠ OVER-CAP (flexible warning unless `enforcement ==
    "block"`). The user may still authorize it in Step 9 with an override note.
- Record this check in the report.

---

### Step 8 — Tax Snapshot

Realized YTD comes from the closing movements (the `realized_eur` of the net book this calendar
year):
```
uv run python -m catalyx.store.movement_repo positions   # realized_eur = YTD realized
```
Feed that as `--ytd-prior` to preview the marginal bracket / projected full-year tax if open
positions closed at mark:
```
uv run python -m catalyx.execution.tax_engine --gain <projected_unrealized_eur> --ytd-prior <realized_eur> --json
```
Show: total realized gains, tax paid YTD, current marginal bracket, projected full-year tax.
If no closing movements yet: state YTD realized = 0.

---

### Step 9 — Position Open Recommendations

Based on heatmap (Step 5), position reviews (Step 6), and exposure check (Step 7):
- List sectors that rank in top-5 AND have no open position — these are the candidates.

**This step only RECOMMENDS. It never opens a position** — opening is the user's action via
`/catalyx-open`. Present a context block per candidate so the user can decide:

```
### <sector_id>   [heatmap rank: #N | composite: X]
- **Why it ranks:** dominant catalyst(s) + their alignment, and momentum (flag if parabolic — high rank ≠ entry point).
- **Crowding:** narrative_maturity and what it implies (crowded/exhausted ⇒ less edge left).
- **Entry timing (Step 5c `entry_timing`):** `micro_timing_state` + `suggested_verdict`, and any event overhang. This is the EXECUTION-window read, separate from crowding — e.g. `falling` ⇒ wait to base, `wait_event` ⇒ a discrete catalyst is in the window. If `scale_in`, suggest a smaller first tranche.
- **Best ETF (UCITS):** ticker, TER, AUM, UCITS status, spread. Flag AUM < $200M.
- **Exposure fit:** proposed size, shared catalyst with existing exposure, `combined_exposure` vs `max_combined_pct` (Step 7). If ⚠ OVER-CAP, state the breach amount — flexible warning, the user may authorize it.
- **Recommendation:** Open now / Wait (bad timing) / Skip — with one line of reasoning (cite the entry_timing verdict when it is the reason to wait).
```

After presenting all context blocks, use the **AskUserQuestion** tool — one question per candidate
— with options **Open now**, **Wait / defer**, **Skip** (and let the user add notes).
- If the user selects **Open now**: hand off to `/catalyx-open <sector_id>` (that skill writes the
  movement file, runs the correlation check, and ingests). This review never writes a movement.

---

### Step 1.5 — Freshness & Lifecycle GATE (stale indicators + auto-deprecation)

> **Runs BEFORE scoring (was Step 10).** This is the freshness gate: audit + prune the
> catalyst state HERE so Steps 2–5 score on clean data. Stale-flagged indicators feed
> straight into Step 2 (refresh them); archived/dormant catalysts drop out of
> `catalyst_alignment` before the heatmap/run in Step 5 is recorded. Running this after the
> run (the old order) baked stale/spent catalysts into the recorded scores.

Read `catalyst_lifecycle` from `scoring_weights.yaml` for the thresholds and `governance` mode.

**1.5a. Stale indicators.** Run the deterministic audit (do NOT eyeball dates):
```
uv run python -m catalyx.scorer.freshness          # pretty table of overdue indicators
uv run python -m catalyx.scorer.freshness --json   # machine-readable list for Step 2
```
It computes days-since-`last_date` against the indicator's **native cadence** — thresholds:
daily > 3, weekly > 10, monthly > 40, quarterly > 95, **semiannual > 200, annual > 400**. The
annual/semiannual tiers exist because indicators sourced from annual reports (Gartner, IBM
X-Force, BloombergNEF, NATO annual report) legitimately print one value per year — auditing them
at the quarterly 95-day threshold flags them ~9 months early (false positive, fixed 2026-06-05).

- `check_frequency` is the single source of truth for cadence. A row marked `⚠mislabel`
  (cadence not in the tier list) means the YAML's `check_frequency` is wrong — **fix the YAML**.
- `last_date` = the date the `current_value` was observed (the data-point date from the
  `update_note`), NOT the date of the previous value in `value_history`. A current value entered
  with a stale `last_date` is the other false-positive source — fix it when you spot it.
- **Feed every overdue row into Step 2** — it is a refresh target before scoring.

**1.5b. Auto-deprecation (governance: "auto" → apply + log; "ask" → prompt per transition).**
History is NEVER deleted — only the `status` field flips. Evaluate every active catalyst:

- **Event → `archived`:** if `strength_decayed < event_archive_strength_below` AND `priced_in ≥ event_archive_priced_in_min`. The event is spent and fully absorbed. `strength_decayed` comes from `catalyst_scorer._decayed_strength`, which (fixed 2026-06-05) anchors decay on the event's OCCURRENCE date (`event_date` → date parsed from the `cat_YYYYMMDD_…` id → `detected_at` fallback), not on when we registered it — so late-registered events decay from when they actually happened.
- **Event → `invalidated`:** if Step 0/1 found the event reversed (policy walked back). Set `invalidation_reason`. Immediate, regardless of decay.
- **Structural → `dormant`:** if `intensity.current_score < structural_dormant_intensity_below` for `structural_dormant_consecutive_cycles` consecutive reviews, OR `narrative_maturity == "exhausted"`. Reactivatable — if indicators repoint above threshold next cycle, flip back to `active`.
- **Event → promote to structural:** if the same event has been re-detected for `event_promote_to_structural_cycles` consecutive cycles and is not decaying (the underlying is ongoing) → draft a structural catalyst from it.

If `governance == "auto"`: apply each transition (write the status change to the catalyst file) and record it in the report's lifecycle log. If `governance == "ask"`: present each pending transition and use AskUserQuestion before writing.

> **Note (Phase 0.5):** these transitions are applied by this skill today. The deterministic home is a future `catalyx/scorer/catalyst_lifecycle.py` module (Phase 1) so the rules run in Python, not via LLM judgment.

---

### Step 11 — Watch-Only Trigger Progress

For each sector with `watch_only: true`:
- Use WebSearch to check if any `watch_triggers` may have fired since last review
- Report: no change / trigger approaching / trigger fired

---

### Step 12 — Taxonomy Gap Review

Load all gap proposals (file-backed reads):
```
uv run python -m catalyx.store.catalyst_repo summary
```
(The taxonomy gap proposals section shows all non-promoted, non-rejected gaps.)

For each proposal, update the file mechanically:
- If detected again this cycle: increment `signal_count`, append a new entry to `evidence[]` with today's date, update `last_seen`, set `status: accumulating`.
- If NOT detected this cycle: leave unchanged. Note it in the report as "not seen this cycle".

**Then, for EACH pending proposal (`status` in `proposed` / `accumulating`), present a context block AND ask the user to decide. Do not skip the question and do not decide automatically.**

For each pending proposal, write a short context block so the user can decide without opening the JSON:

```
### <proposed_sector_id or theme>   [signal_count: N | first_seen → last_seen]
- **Investment thesis:** one line — why this theme could move, what the demand/supply driver is.
- **Why now:** what surfaced it this cycle (cite the Step 0/1 evidence, not last month's).
- **ETF coverage:** pure-play ticker if found (TER/AUM if known), else best proxies and their estimated exposure %.
- **Relation to existing sectors:** which current `sector_id`(s) it overlaps with or complements, and whether it is genuinely distinct under the granularity principle (Gold ≠ Gold miners). If it is just a slice of an existing sector, say so.
- **Strength / novelty:** strength_score and novelty_score from the proposal; how it compares to an existing catalyst for calibration.
- **Risk / reason to wait:** liquidity, single-issuer ETF, cyclical timing, or "signal too thin (signal_count < 3)".
- **Recommendation:** Promote / Reject / Defer — with one line of reasoning.
```

After presenting all context blocks, use the **AskUserQuestion** tool — one question per pending proposal — with options **Promote**, **Reject**, **Defer to next cycle** (and let the user add notes). Carry out the action the user selects for each. Never write to `sector_taxonomy.yaml` before the user answers.

- A proposal with `signal_count < 3` should default the recommendation to **Defer** (signal too thin) unless Step 0/1 produced a strong fresh catalyst for it.
- Already-`rejected` proposals are not re-asked; list them in the report for the record only.

**Promotion action (only when the user selects Promote):**
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

## 4b. Opportunities & Rotation  (recommendations — not trades)
**Regime watch** (only non-`intact` sectors)
| Sector | Regime | Persistence (n · span · clustered?) | Read |
|---|---|---|---|

**Opportunities** (fell hard · intact · catalyst-confirmed · contagion-driven)
| Sector | Drawdown % | Contagion % / Idiosyncratic % | catalyst_alignment | Verdict |
|---|---|---|---|---|

**Diversifiers** (healthy · low correlation to the stressed cluster)
| Sector | Composite | Corr to stressed | Note |
|---|---|---|---|

**Entry timing** (execution window for top-ranked / candidate sectors — recommend-only)
| Sector | State | RSI / vol / 5d% | Event overhang? | Suggested verdict |
|---|---|---|---|---|

## 5. Open Positions
| Sector | Days open | Assumptions (N/N holding) | Driving catalyst regime | Action |
|---|---|---|---|---|

## 6. Catalyst Exposure
| Catalyst | Invested € | Sectors | Combined % | Status |
|---|---|---|---|---|

## 7. Position Open Recommendations
[Top-5 sectors with no open position. ⚠ OVER-CAP candidates (combined exposure > cap) flagged separately. Recommendations only — opening is done via /catalyx-open.]

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

- **Step 0/1 is mandatory and runs FIRST: `/catalyx-scan` is the macro front door.** In `scheduled` mode the review's first action is one `/catalyx-scan` — it returns the C0 macro/big-economy context, the Pass 2 per-catalyst refresh deltas, and the new gaps/events; consume that output, do not repeat the searches. In `event:<id>` mode SKIP the full scan and do a lightweight refresh of that one catalyst. Never trust a stored YAML/JSON value before searching — project data is always one month stale.
- **Within the scan: C0 macro context → Pass 1 (Discovery) → Pass 2 (Classification + Refresh).** Never read `sector_taxonomy.yaml` during the Discovery pass — the point is to find what the taxonomy misses.
- **Sector studies before heatmap.** Never run heatmap without refreshing sector studies for the top-5 sectors.
- The Executive Summary must contain at least one NON-OBVIOUS finding. If everything is "no change", state that explicitly.
- Open position review must make a concrete recommendation (Hold / Add / Reduce / Exit). "Monitor" is not a recommendation. The review only recommends; the user executes via /catalyx-open or /catalyx-close.
- Stale indicators are not optional to flag — they are data quality issues that corrupt downstream analysis.
- **Step 12 actively asks the user per pending proposal** (AskUserQuestion: Promote / Reject / Defer) after presenting a context block for each. Never present the gap table as read-only and never promote, reject, or skip a proposal without an explicit answer. Writing to `sector_taxonomy.yaml` only happens after the user selects Promote.
- **Catalyst exposure check (Step 7) informs any open-position recommendation (Step 9).** Never recommend a new position without first checking combined exposure against existing positions sharing the same catalyst.
- **Step 9 actively asks the user per candidate** (AskUserQuestion: Open now / Wait / Skip) after presenting a context block for each. The review NEVER opens a position — on "Open now" it hands off to `/catalyx-open`. The `correlated_catalyst_cap` (default 20%, `enforcement: warn`) is a FLEXIBLE warning — a breach is surfaced and requires an override note, but does not by itself block.
- **AI SCORING RULE:** Never assign `intensity.current_score` manually. Always recompute from indicator semaphores using the formula in `scoring_weights.yaml`. If a user_override is needed, document the reason in `computation_note`.
- **Regime / opportunities (Step 5c) are recommendations, never auto-trades, and never move portfolio weights.** A `contested` sector keeps its full score and weight — it is a watch flag. The pipeline reacts to PERSISTENCE (dispersed developments or measured fundamental degradation), not to a single event, and the escalation + buy/rotate decisions are the user's. "Two consecutive-day drops confirm nothing."
