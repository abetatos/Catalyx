# CATALYX — Project Intelligence

> Every session working on this project must start by reading this file.
> It is the single source of truth for architecture decisions, versions, and development protocol.

---

## What This Project Is

CATALYX is a sector ETF analysis platform built around a single investment pipeline:

**MACRO CATALYST → THESIS FORMULATION → POSITION EXECUTION → VALIDATION & FEEDBACK**

It exists to:
1. Detect and score macro catalysts before they are priced in
2. Formulate structured, falsifiable, machine-readable theses
3. Track execution with full Spanish tax-aware P&L
4. Measure whether a thesis was right — and whether it was right *for the right reasons*
5. Feed validated/invalidated theses back into future scoring as a prior probability table

**Investor profile:** Data scientist and experienced trader. High risk tolerance. Momentum and catalyst-driven. ETFs only (equities, commodities, sector-specific). Monthly review cadence with event-driven updates.

**Non-negotiable principle:** Sectors must be maximally granular. Gold ≠ Gold miners ≠ Silver ≠ Copper. EU defense prime contractors ≠ US defense ≠ Cybersecurity. Every sector differentiation has a reason.

---

## Architecture Philosophy — Permanent Hybrid Model

**This is not a migration path from Claude to Python.** The target architecture is a permanent hybrid:

```
Claude (interface + intelligence)          Python (deterministic backbone)
─────────────────────────────────          ───────────────────────────────
- Conversational thesis formulation        - Scoring formulas (no LLM drift)
- News analysis & catalyst detection       - Market data fetching (yfinance)
- Assumption critique and discussion       - DB reads/writes (SQLAlchemy)
- Monthly review orchestration             - Tax computation (Spanish CGT)
- Qualitative judgment (sector narrative)  - Attribution decomposition
- Output formatting for the user           - Event decay calculation
```

**Skills invoke Python.** A skill (.md file) calls `uv run python -m catalyx.<module> <args>` via Bash, receives deterministic JSON output, and uses that as data for reasoning. Claude never free-assigns numbers that a formula can compute.

**Why this design is stable long-term:**
- Formulas in code are tested, version-controlled, and reproducible across sessions
- Claude handles the parts that genuinely require reasoning — not arithmetic
- Adding Python modules expands capability without changing the conversational interface
- The feedback loop (Phase 3 ML) requires structured data that Python produces; Claude produces the analysis on top of it

---

## Catalyst Model: Dual Types

CATALYX supports two fundamentally different catalyst types. Never collapse them into one.

| Type | Example | Temporality | Validated by |
|---|---|---|---|
| `EventCatalyst` | NATO 3.5% GDP announcement | Discrete, timestamped, decays | Did the event materialize? |
| `StructuralCatalyst` | Central banks systematically buying gold | Onset period + ongoing, persistent | Are `indicators[]` still active? |

Structural catalysts are the floor signal. Event catalysts are the spike. Both contribute to `SectorSnapshot.scores.catalyst_alignment` with different decay functions.

---

## Development Phases & Version Stacks

### Phase 0.5 — Skill + Python Data Layer (current)
**Goal:** Claude remains the conversational interface and intelligence layer. Python handles deterministic computation, data storage, and market data fetching. Skills call Python modules via `uv run python -m catalyx.*`.
**Architecture principle:** Python = infrastructure (formulas, DB, fetching). Claude = reasoning, analysis, thesis formulation, discussion.

| Component | Tool |
|---|---|
| News scanning | Claude WebSearch |
| Thesis drafting | Claude (conversational + Write to JSON) |
| Market data / momentum | `catalyx/data/market_data.py` (yfinance) |
| Deterministic scoring formulas | Python modules callable from skills |
| Storage | SQLite `data/catalyx.db` via SQLAlchemy + JSON files in `data/` |
| P&L / tax | Manual (tax_engine.py next) |
| Scheduling | CronCreate (limited) |

**Claude model:** `claude-sonnet-4-6` (current session model)
**No pinned LLM API yet — this phase uses the Claude Code session directly.**

### Python infrastructure already built (Phase 0.5)

| Module | Path | What it does |
|---|---|---|
| DB + LLM log | `catalyx/store/db.py` | SQLAlchemy engine, Base, `LLMLog` table, `init_db()` |
| Catalyst repo | `catalyx/store/catalyst_repo.py` | CRUD for `CatalystEvent` + `TaxonomyGapProposal`. CLI: `python -m catalyx.store.catalyst_repo summary` |
| Sector study repo | `catalyx/store/sector_study_repo.py` | CRUD for `SectorStudy` objects |
| Thesis repo | `catalyx/store/thesis_repo.py` | CRUD for `Thesis` + `ClosedThesis` |
| Structural catalyst repo | `catalyx/store/structural_catalyst_repo.py` | CRUD for `StructuralCatalyst` |
| Market data | `catalyx/data/market_data.py` | yfinance ETF momentum fetcher. CLI: `uv run python -m catalyx.data.market_data` → `data/snapshots/momentum_snapshot_YYYYMMDD.json` |
| Intensity engine | `catalyx/scorer/intensity_engine.py` | Compute `intensity.current_score` from indicator semaphores. CLI: `uv run python -m catalyx.scorer.intensity_engine --all [--write-back]` |
| Catalyst scorer | `catalyx/scorer/catalyst_scorer.py` | v1.3/v1.4 confirms/contradicts/independent formula + event decay → `catalyst_alignment`. CLI: `uv run python -m catalyx.scorer.catalyst_scorer <sector_id>` |
| Momentum engine | `catalyx/scorer/momentum_engine.py` | Cross-sectional percentile rank from yfinance snapshot → `momentum_score [0–100]`. CLI: `uv run python -m catalyx.scorer.momentum_engine [--snapshot path]` |
| Sector scorer | `catalyx/scorer/sector_scorer.py` | Composite formula orchestrator: calls catalyst_scorer + momentum_engine → full SectorSnapshot scores. CLI: `uv run python -m catalyx.scorer.sector_scorer <sector_id> [--flow N --val N --crowd N]` |
| Tax engine | `catalyx/execution/tax_engine.py` | Spanish CGT 2026 progressive brackets (19/21/23/27%). Incremental + YTD computation. CLI: `uv run python -m catalyx.execution.tax_engine --gain N [--ytd-prior N --loss N]` |
| Thesis scorer | `catalyx/attribution/thesis_scorer.py` | `right_reason_score` formula from ClosedThesis. CLI: `uv run python -m catalyx.attribution.thesis_scorer <path.json>` |
| Flow data | `catalyx/data/flow_data.py` | ETF shares_outstanding × NAV → `flow_confirmation [0–100]`. Writes to `data/snapshots/flow_snapshot_YYYYMMDD.json`. Week-over-week delta requires prior snapshot. CLI: `uv run python -m catalyx.data.flow_data [--write]` |

**DB location:** `data/catalyx.db` (SQLite). URL override via `CATALYX_DB_URL` env var.
**Init command:** `uv run python -m catalyx.store.catalyst_repo init`

**Skills call Python modules** using `uv run python -m catalyx.<module> <command>` via Bash tool. This is the integration model — not a separate CLI for the user, but Python as a deterministic backend that skills invoke.

---

### Phase 1 — Python CLI (next)
**Goal:** Full working pipeline loop via `catalyx` CLI. Every schema object produced and stored. At least one closed thesis with attribution.

**Python version: 3.12**

| Package | Version | Role |
|---|---|---|
| `anthropic` | `>=0.40` | Claude API client |
| `openai` | `>=1.40` | Classification and bulk LLM tasks |
| `pydantic` | `>=2.7` | Schema validation (v2 only — no v1 compat) |
| `typer` | `>=0.12` | CLI framework |
| `rich` | `>=13.7` | CLI output tables and formatting |
| `sqlalchemy` | `>=2.0` | ORM (async-compatible) |
| `alembic` | `>=1.13` | DB migrations |
| `yfinance` | `>=0.2.40` | Market data (prices, ETF metadata) |
| `httpx` | `>=0.27` | Async HTTP (news, flow data) |
| `jsonschema` | `>=4.22` | JSON Schema validation against `schemas/` |
| `python-dotenv` | `>=1.0` | Env var loading |

**Storage:** SQLite via SQLAlchemy. Never use SQLite-specific syntax (`ROWID`, `PRAGMA`) in application code — the Phase 2 Postgres migration must be a connection-string swap only.

**Claude models (pinned — never use aliases):**

| Use case | Model ID |
|---|---|
| Thesis drafting, deep analysis | `claude-opus-4-8` |
| Sector scoring rationale, monitoring | `claude-sonnet-4-6` |
| Bulk news classification | `claude-haiku-4-5-20251001` |

**OpenAI models (pinned — never use aliases like `"gpt-4o"`):**

| Use case | Model ID |
|---|---|
| Thesis drafting (compare/backup) | `gpt-4o-2024-08-06` |
| Bulk news classification | `gpt-4o-mini-2024-07-18` |

**Every LLM call must log:** model_id, prompt_tokens, completion_tokens, timestamp, calling_function. Table: `llm_log` in SQLite.

---

### Phase 2 — Automation + ML Foundation
**Python:** 3.12

| Package | Version | Role |
|---|---|---|
| `sentence-transformers` | `>=2.7` | Catalyst novelty filtering (embedding distance) |
| `fastapi` | `>=0.111` | API layer over CLI |
| `apscheduler` | `>=3.10` | Background scheduling (scanner, monitor) |
| `psycopg2-binary` | `>=2.9` | Postgres adapter (Phase 2 DB migration) |

**Embedding model:** `all-MiniLM-L6-v2` (local, no API cost, sufficient for novelty filtering)

**New modules:** `scanner/structural_monitor.py`, `sector_study/`, `data/flow_data.py` (iShares API), `data/cot_data.py` (CFTC parser)

---

### Phase 3 — ML Scoring
**Python:** 3.12

| Package | Version | Role |
|---|---|---|
| `xgboost` | `>=2.0` | Catalyst strength prediction |
| `scikit-learn` | `>=1.5` | Feature pipelines, Bayesian update |
| `numpy` | `>=2.0` | Numerical ops |
| `pandas` | `>=2.2` | Data manipulation |
| `optuna` | `>=3.6` | Hyperparameter optimization |

---

### Phase 4 — Backtesting
**New dependency:** GDELT API (historical news), COT historical archive (CFTC)
**Critical constraint:** Catalyst detection in backtest must use only data available at signal time. No look-ahead.

---

## Repository Structure

```
catalyx/
├── CLAUDE.md                          ← THIS FILE — always read first
├── .claude/
│   └── settings.json                  ← Hooks: auto-validation on schema edits
├── catalyx/                           ← Main Python package (Phase 1+)
│   ├── scanner/
│   │   ├── signal_ingester.py
│   │   ├── novelty_filter.py
│   │   ├── catalyst_detector.py
│   │   ├── strength_scorer.py
│   │   └── structural_monitor.py      ← Phase 2
│   ├── scorer/
│   │   ├── sector_scorer.py
│   │   ├── momentum_engine.py
│   │   ├── flow_engine.py
│   │   └── valuation_engine.py
│   ├── thesis/
│   │   ├── thesis_builder.py
│   │   ├── thesis_validator.py
│   │   ├── assumption_monitor.py
│   │   └── invalidation_watcher.py
│   ├── execution/
│   │   ├── trade_logger.py
│   │   ├── pnl_engine.py
│   │   └── tax_engine.py              ← Spanish CGT progressive brackets
│   ├── attribution/
│   │   ├── return_decomposer.py
│   │   └── thesis_scorer.py
│   ├── feedback/
│   │   ├── prior_updater.py
│   │   └── pattern_reporter.py
│   ├── sector_study/                  ← Phase 1 (bottom-up analysis)
│   │   ├── study_builder.py
│   │   ├── study_updater.py
│   │   └── watch_trigger_monitor.py
│   ├── data/
│   │   ├── market_data.py             ← yfinance wrapper
│   │   ├── flow_data.py               ← ETF AUM (shares outstanding × NAV, not AUM)
│   │   ├── cot_data.py                ← CFTC COT parser
│   │   ├── news_adapter.py
│   │   ├── cb_calendar.py
│   │   └── llm_client.py              ← Anthropic + OpenAI wrappers, logs all calls
│   ├── store/
│   │   ├── db.py
│   │   ├── catalyst_repo.py
│   │   ├── snapshot_repo.py
│   │   ├── thesis_repo.py
│   │   ├── trade_repo.py
│   │   └── prior_repo.py
│   ├── cli/
│   │   ├── main.py
│   │   ├── cmd_scan.py
│   │   ├── cmd_score.py
│   │   ├── cmd_thesis.py
│   │   ├── cmd_trade.py
│   │   └── cmd_feedback.py
│   └── config/
│       ├── sector_taxonomy.yaml       ← CANONICAL: all sector IDs live here
│       ├── catalyst_taxonomy.yaml     ← Catalyst types and subtypes enum
│       ├── etf_universe.yaml          ← ETFs per sector (quarterly review)
│       ├── scoring_weights.yaml       ← Dimension weights for composite score
│       └── structural_catalysts/      ← One .yaml per structural catalyst
│           ├── cb_gold_accumulation.yaml
│           ├── ai_capex_supercycle.yaml
│           ├── nato_rearmament.yaml
│           ├── deglobalization_reshoring.yaml
│           ├── energy_transition_grid.yaml
│           ├── negative_real_rates.yaml
│           ├── em_consumer_rise.yaml
│           └── water_scarcity.yaml
├── schemas/                           ← JSON Schema files (source of truth for all objects)
│   ├── catalyst_event.json
│   ├── structural_catalyst.json
│   ├── sector_snapshot.json
│   ├── sector_study.json
│   ├── thesis.json
│   ├── closed_thesis.json
│   └── taxonomy_gap_proposal.json     ← Output of Discovery Pass: themes not in taxonomy yet
├── data/                              ← Runtime data (gitignored except examples)
│   ├── catalysts/
│   ├── snapshots/
│   ├── theses/
│   ├── sector_studies/
│   ├── taxonomy_proposals/            ← TaxonomyGapProposal JSON files (gap_YYYYMMDD_slug.json)
│   └── catalyx.db
├── tests/
│   ├── unit/
│   │   ├── test_tax_engine.py         ← Test all bracket edge cases
│   │   ├── test_strength_scorer.py
│   │   └── test_return_decomposer.py
│   └── integration/
│       ├── test_scan_to_score.py
│       └── test_thesis_lifecycle.py
├── notebooks/
│   ├── calibrate_scoring_weights.ipynb
│   └── prior_table_analysis.ipynb
├── docs/
│   └── SPEC_v1.1.md                   ← Full technical specification
├── pyproject.toml
└── .env.example
```

---

## Key Files — What to Read When

This section tells Claude which files to read before working on each area. **Always read these before editing.**

| Working on... | Read first |
|---|---|
| Any data schema or Pydantic model | `schemas/<relevant>.json` |
| Sector scoring, heatmap | `catalyx/config/sector_taxonomy.yaml` + `schemas/sector_snapshot.json` |
| Thesis formulation or validation | `schemas/thesis.json` + `schemas/closed_thesis.json` |
| Structural catalysts | `catalyx/config/structural_catalysts/<relevant>.yaml` + `schemas/structural_catalyst.json` |
| Tax engine or P&L | `docs/SPEC_v1.1.md` §Tax section — Spanish CGT brackets are progressive, no short/long term distinction |
| ETF selection logic | `catalyx/config/etf_universe.yaml` — check TER, AUM, replication type, spread |
| CLI commands | `catalyx/cli/main.py` first, then the relevant `cmd_*.py` |
| LLM integration | `catalyx/data/llm_client.py` — all calls must go through this, pinned model IDs only |
| Feedback loop / priors | `schemas/closed_thesis.json` → `CatalystSectorPrior` table schema in `store/prior_repo.py` |
| Taxonomy gaps / discovery | `schemas/taxonomy_gap_proposal.json` + `data/taxonomy_proposals/*.json` |
| DB schema / SQLAlchemy models | `catalyx/store/db.py` (Base, LLMLog) then the relevant `*_repo.py` |
| Catalyst DB operations (read/write/query) | `catalyx/store/catalyst_repo.py` — has CLI: `python -m catalyx.store.catalyst_repo summary` |
| Scoring formulas (computing, not config) | `catalyx/config/scoring_weights.yaml` + the relevant `catalyx/scorer/*.py` |
| Market data / momentum snapshot | `catalyx/data/market_data.py` — run to produce `data/snapshots/momentum_snapshot_YYYYMMDD.json` |

---

## Schema Change Protocol

When any file in `schemas/` is modified:

1. **Bump `schema_version`** in the modified schema file
2. **Add migration note** to `docs/SPEC_v1.1.md` under the Changelog section
3. **Update Pydantic model** in the corresponding Python module
4. **Check all existing JSON files** in `data/` that use this schema — they need a migration or a version-tagged read path
5. **Never delete fields** — mark deprecated fields with `"deprecated": true` and keep them for one major version

When `sector_taxonomy.yaml` is modified (sector added, removed, or field changed):
1. Check `catalyx/config/etf_universe.yaml` — does the new sector have ETF coverage?
2. Check `catalyx/config/scoring_weights.yaml` — does it need a demand_driver weight override?
3. If sector removed: grep for all `sector_id` references in `data/theses/` — active theses cannot reference removed sectors

---

## Critical Implementation Rules

**Currency:** All P&L in EUR. Non-EUR ETF returns converted at execution date. Tax computed in EUR always.

**Thesis IDs:** Human-readable slugs. Format: `thesis_YYYYMMDD_sectorid_keyword`. Never UUIDs for theses.

**Catalyst IDs:**
- Event: `cat_YYYYMMDD_keyword`
- Structural: `struct_keyword_keyword`

**ETF flow data:** Use shares_outstanding × NAV, NOT total AUM. AUM conflates price appreciation with net flows. iShares API provides shares_outstanding directly.

**LLM model IDs:** Always pin exact version strings. Never use aliases (`"gpt-4o"`, `"claude-opus"` etc.). Model silently updates → classification drift → corrupt training data.

**Crowding risk** is a scoring penalty, not a reward. High crowding subtracts from composite score.

**Watch-only sectors** (`investable: false` in taxonomy): appear in heatmap with "NOT YET INVESTABLE" banner. Cannot be the target of a `Thesis` object. Monitor `watch_triggers` only.

**Spanish CGT:** Progressive brackets on ALL capital gains regardless of holding period (no short/long distinction). Tax year is calendar year. Apply brackets sequentially across all realized gains YTD. Brackets as of 2026: 19% up to €6k, 21% up to €50k, 23% up to €200k, 27% above.

**Attribution decomposition confidence:** Mark `"low"` when holding_days < 60 or when sector_beta and catalyst_alignment are both > 80% (collinear). Never claim false precision.

---

## Sector Taxonomy Rules

- `sector_id` is the canonical identifier. Free-text sector names are never used in application code.
- `sector_taxonomy.yaml` is the single source of truth for all valid `sector_id` values.
- Sectors have `investable: true/false`. Only investable sectors can be thesis targets.
- `watch_only` sectors track `watch_triggers[]` — when triggers fire, flag for taxonomy update.
- Quarterly review: check ETF AUM (< €200M → liquidity warning), spread (> 25bps → warning).

---

## User Catalyst Management

Users rank catalysts with `user_rank` (integer, 1 = highest priority). This multiplies into `display_priority`:

| user_rank | multiplier |
|---|---|
| 1 | ×1.40 |
| 2 | ×1.20 |
| 3 | ×1.00 (neutral) |
| 4 | ×0.80 |
| 5+ | ×0.60 |
| unranked | ×1.00 |

`display_priority = algorithmic_score × user_rank_multiplier`

Archived catalysts are retained in DB with `status: "archived"`. History is never deleted.

---

## Phase 0 Workflow (Current — Skill-Based)

**Philosophy:** Generate → Critique → Improve. Claude produces structured outputs from config files. User critiques the reasoning. Pipeline improves iteratively before Phase 1 is built.

### Monthly Pipeline Order — MANDATORY

The order below is not a suggestion. Each step provides data that the next step requires.

```
0.  Macro & Geopolitical Context  ← WebSearch FIRST, before reading any file
1.  /catalyx-scan                 ← Pass 1: Discovery (market-led, no taxonomy) → gaps
                                     Pass 2: Classification (taxonomy-led) → new events
2.  /catalyx-update               ← refresh stale indicators, recompute intensity
3.  /catalyx-sector-study         ← PREREQUISITE for heatmap (run for top-5 sectors + any gap sectors)
4.  /catalyx-dashboard            ← derives from updated catalyst YAMLs
5.  /catalyx-heatmap              ← requires updated sector studies
6.  /catalyx-thesis review        ← uses WebSearch + updated catalysts
7.  /catalyx-thesis draft         ← only after heatmap confirms sector ranking
8.  Portfolio correlation check   ← before opening any new position
12. Taxonomy Gap Review           ← review data/taxonomy_proposals/, promote or reject
```

**Why Step 3 before Step 5:** The heatmap uses `demand_drivers`, `analyst_narrative_score`, and `cycle_position` from sector studies. Without a fresh sector study, the heatmap fills those fields with null or stale data. Sectors without studies rank incorrectly.

**Why Step 0 before everything:** Project files reflect last month's data. WebSearch reflects today. The delta between them is often the most important finding of the review.

**Why Discovery Pass runs without reading the taxonomy:** The scan's Pass 1 is designed to find investment themes the taxonomy does not cover. Reading the taxonomy first would bias the search toward known sectors and create blind spots for emerging themes.

### Files Claude reads for each task

| Task | Step 0: WebSearch first | Then read |
|---|---|---|
| Any analysis | Current date + relevant macro keywords | `CLAUDE.md` + `scoring_weights.yaml` |
| Catalyst dashboard | Indicator updates per active catalyst | All `structural_catalysts/*.yaml` + `data/catalysts/*.json` |
| Sector study | Sector name + ETF price + current news | `sector_taxonomy.yaml` + `etf_universe.yaml` + existing study if present |
| Heatmap | No additional (Step 3 already done) | Above + `data/sector_studies/*.json` |
| Thesis draft | Sector news + ETF data | Heatmap + `schemas/thesis.json` + `data/sector_studies/study_<sector>.json` |
| Thesis review | Each assumption data source + news | `data/theses/<thesis>.json` + structural catalyst YAML |
| Catalyst update | Source data for the indicator being updated | Specific `structural_catalysts/<id>.yaml` |

### Slash Commands (skills definidas en `.claude/commands/`)

| Comando | Archivo | Qué hace |
|---|---|---|
| `/catalyx-dashboard` | `.claude/commands/catalyx-dashboard.md` | Catalyst dashboard desde los YAMLs actuales |
| `/catalyx-heatmap` | `.claude/commands/catalyx-heatmap.md` | Sector heatmap rankeado por catalyst_alignment |
| `/catalyx-thesis draft <sector_id>` | `.claude/commands/catalyx-thesis.md` | Draft completo de thesis siguiendo schema |
| `/catalyx-thesis review <thesis_id>` | `.claude/commands/catalyx-thesis.md` | Revisa assumptions con WebSearch actual |
| `/catalyx-thesis close <thesis_id>` | `.claude/commands/catalyx-thesis.md` | Cierra thesis y calcula ClosedThesis + tax |
| `/catalyx-scan` | `.claude/commands/catalyx-scan.md` | WebSearch → nuevos CatalystEvent JSON |
| `/catalyx-update <id> <ind> <val>` | `.claude/commands/catalyx-update.md` | Actualiza indicador de catalizador estructural |
| `/catalyx-sector-study <sector_id>` | `.claude/commands/catalyx-sector-study.md` | Genera/actualiza SectorStudy JSON |
| `/catalyx-monthly-review` | `.claude/commands/catalyx-monthly-review.md` | Review completo mensual (todos los módulos) |

### Data files state (Phase 0)

```
data/
├── catalysts/
│   └── cat_20260603_nato_defense_gdp.json      ← 1 evento registrado
├── sector_studies/
│   ├── study_grid_infrastructure.json           ← estudio completo
│   ├── study_copper_miners.json                 ← estudio completo
│   └── study_gold_miners.json                   ← estudio completo
├── theses/                                      ← vacío — pendiente primer draft
├── taxonomy_proposals/                          ← vacío — se puebla en el primer scan con Discovery Pass
└── reports/
    ├── catalyst_dashboard_20260603.md
    └── heatmap_20260603.md
```

All JSON files written to `data/` follow the schemas in `schemas/`.

---

## AI Scoring Stability Rules

LLMs produce unstable numeric scores across sessions. A free-floating "84" from one session ≠ "84" from another. These rules enforce reproducibility.

**Rule 1 — Compute intensity, never guess it.**
`intensity.current_score` MUST be derived from indicator semaphores using the formula in `scoring_weights.yaml`. Run `/catalyx-update` after every indicator change — it recomputes intensity automatically. Only `computation_method: "bootstrap"` allows manual values, and only at file creation.

**Rule 2 — Use categories for qualitative dimensions.**
- `narrative_maturity`: use the 5-level enum (`ignored / emerging / mainstream / crowded / exhausted`), NOT a number. See `scoring_weights.yaml` for anchored criteria with examples.
- `is_priced_in_estimate`: use one of 5 stepped levels (0 / 0.25 / 0.50 / 0.75 / 1.0) only.
- `novelty_score`: answer the 5 rubric questions in `novelty_rubric_scores`, then compute as count(true) × 20.

**Rule 3 — Anchor new catalysts relative to existing ones.**
When creating a new structural catalyst, compare to an existing one: "intensity similar to `struct_cb_gold_accumulation` (84)" or "weaker than `struct_ai_capex_supercycle` (89)". This inter-catalyst calibration persists across sessions.

**Rule 4 — Ordinal ranking is more stable than cardinal scoring.**
When comparing sectors in the heatmap, "A ranks above B" is more reliable than "A=87, B=84". Use the formula-computed scores but interpret results as a ranking, not precise measurements.

**Rule 5 — WebSearch before reading YAML.**
Catalyst YAMLs contain last-month's data. Always search for current values before trusting what's stored. Flag any indicator where the live value differs from the YAML by >10%.

---

## Feedback Loop — Monthly Review Checklist

Run `/catalyx-monthly-review` on the first Monday of each month. The skill handles ordering.
Manual reminder of what that skill does:

0.  WebSearch: current macro/geo context — compare to stored YAML data, flag deltas
1.  `/catalyx-scan` — Pass 1: Discovery (market-led gaps) + Pass 2: new events above strength 55
2.  `/catalyx-update` — refresh stale indicators, recompute intensity algorithmically
3.  `/catalyx-sector-study` — refresh sector studies for top-5 catalyst_alignment sectors
4.  `/catalyx-dashboard` — regenerate with updated data
5.  `/catalyx-heatmap` — re-rank with updated sector studies
6.  `/catalyx-thesis review` — for each open thesis, concrete recommendation
7.  Portfolio correlation check — flag any new thesis sharing primary catalyst with open thesis
8.  Tax snapshot YTD
12. Taxonomy Gap Review — review `data/taxonomy_proposals/`, promote or reject pending gaps

---

## What Has Been Designed (Completed)

- [x] Full pipeline architecture (6 stages)
- [x] `CatalystEvent` schema (event-driven)
- [x] `StructuralCatalyst` schema (secular trends)
- [x] `SectorSnapshot` schema
- [x] `SectorStudy` schema (bottom-up analysis)
- [x] `Thesis` schema (full, with all fields)
- [x] `ClosedThesis` schema (with attribution and assumption validation)
- [x] Sector taxonomy (60+ sectors including futuristic watch-only)
- [x] ETF universe (2-3 ETFs per sector, UCITS flagged)
- [x] Catalyst taxonomy (types, subtypes, decay half-lives)
- [x] Scoring weights (composite formula + conviction tiers)
- [x] 5 structural catalysts pre-configured with indicators
- [x] 4 event catalysts registered (NATO 3.5% GDP, NATO Hague 5%, copper supply deficit, AI chip controls)
- [x] 3 sector studies (grid_infrastructure, copper_miners, gold_miners)
- [x] Report templates (catalyst_dashboard, heatmap)
- [x] 2 reports generated (catalyst dashboard + partial heatmap)
- [x] Spanish CGT tax model (spec only)
- [x] Return attribution decomposition method (spec only)
- [x] User catalyst ranking system
- [x] Phase 0/1/2/3/4 roadmap with pinned model versions
- [x] Phase 0.5 workflow documented (generate → critique → improve loop)
- [x] Signal-first architecture: Discovery Pass in `/catalyx-scan` (Pass 1 market-led, Pass 2 taxonomy-led)
- [x] `TaxonomyGapProposal` schema — tracks emerging themes not in taxonomy
- [x] Monthly taxonomy gap review step (Step 12) in `/catalyx-monthly-review`
- [x] `catalyx/store/db.py` — SQLAlchemy engine, `LLMLog` table, `init_db()`
- [x] `catalyx/store/catalyst_repo.py` — CRUD + CLI for `CatalystEvent` and `TaxonomyGapProposal`
- [x] `catalyx/store/sector_study_repo.py` — CRUD for `SectorStudy`
- [x] `catalyx/store/thesis_repo.py` — CRUD for `Thesis` + `ClosedThesis`
- [x] `catalyx/store/structural_catalyst_repo.py` — CRUD for `StructuralCatalyst`
- [x] `catalyx/data/market_data.py` — yfinance ETF momentum fetcher with fixed formula
- [x] `data/catalyx.db` — SQLite DB initialized and present
- [x] `catalyx/scorer/intensity_engine.py` — `intensity.current_score` from semaphores, `--write-back` to YAML (ruamel.yaml preserves format)
- [x] `catalyx/scorer/catalyst_scorer.py` — `catalyst_alignment` per sector: confirms/contradicts/independent formula with exponential decay

## What Is Still Missing

### Phase 0.5 (no code needed)
- [x] Thesis draft — `thesis_20260603_copper_miners_datacenter_alpha` (status: draft, entry params need recalibration to current prices)
- [x] Thesis draft for `grid_infrastructure_utilities` — `thesis_20260603_grid_infrastructure_utilities_bindingconstraint` exists
- [x] SectorStudy for `gold_physical` — `data/sector_studies/study_gold_physical.json` present
- [ ] Open the copper thesis after recalibrating entry price limit (COPX ~$90, drafted at $10,200 copper; actual LME ~$13,965)
- [ ] SectorStudy for `eu_defense_prime_contractors` and `ai_infrastructure_data_centers` (both in top-5 catalyst_alignment)
- [ ] Schema migration: update existing catalyst YAMLs to schema v1.2 (add `narrative_maturity`, recompute `intensity` algorithmically)
- [ ] Update copper catalyst indicators with real market data (LME ~$13,965, hyperscaler capex ~$700B)

### Design gaps to fix (identified in pipeline tests)
- [x] Structural ↔ event interaction formula — `cat_20260603_nato_defense_gdp.json` already has `relation_to_structural: "confirms"` and `related_catalyst_ids: ["struct_nato_rearmament"]`
- [x] Heatmap LLM drift — skill now calls `sector_scorer --all` for Python-computed scores; sector study freshness gate (7-day max age) enforced before scoring
- [ ] Portfolio correlation enforcement in `/catalyx-thesis draft` skill — check combined allocation before opening
- [ ] `analyst_model_revision` event type in `catalyst_taxonomy.yaml` — the copper thesis alpha closes when Goldman/JPM update models; the scan skill currently misses this signal

### Python scoring layer (highest stability impact — callable from skills)
- [x] `catalyx/scorer/intensity_engine.py` — compute `intensity.current_score` from indicator semaphores. CLI: `uv run python -m catalyx.scorer.intensity_engine --all [--write-back] [--period 2026-Q2]`
- [x] `catalyx/scorer/catalyst_scorer.py` — confirms/contradicts/independent formula with event decay. CLI: `uv run python -m catalyx.scorer.catalyst_scorer <sector_id> [--all]`
- [x] `catalyx/scorer/momentum_engine.py` — cross-sectional percentile normalization (17 sectors). CLI: `uv run python -m catalyx.scorer.momentum_engine [--snapshot path]`
- [x] `catalyx/scorer/sector_scorer.py` — composite formula orchestrator. CLI: `uv run python -m catalyx.scorer.sector_scorer <sector_id> [--all --flow N --val N --crowd N]`
- [x] `catalyx/execution/tax_engine.py` — Spanish CGT 2026 brackets (19/21/23/27%), incremental + YTD. CLI: `uv run python -m catalyx.execution.tax_engine --gain N [--ytd-prior N --loss N]`
- [x] `catalyx/attribution/thesis_scorer.py` — `right_reason_score` formula. CLI: `uv run python -m catalyx.attribution.thesis_scorer <path.json> [--all]`

### Phase 1 (Python required — infrastructure completion)
- [x] `catalyx/data/flow_data.py` → flow_confirmation scores from ETF shares_outstanding × price. Baseline snapshot written 2026-06-04. Week-over-week delta activates from next run.
- [ ] Alembic migrations scaffold → `alembic init alembic/` + first migration from current models
- [ ] `catalyx/cli/` Typer commands (wraps Python modules for direct terminal use)

---

## Recent Changes

> Last 5 entries — oldest rotate to [`CHANGELOG.md`](CHANGELOG.md). Read that file only on demand ("when did X change?", "why is field Y structured this way?").
> Convention: the *why* (bug description + fix rationale) lives inline in the modified file. The *what and when* lives here and in CHANGELOG.md.

| Date | File | Version | Change |
|---|---|---|---|
| 2026-06-04 | `catalyx/data/flow_data.py` | new | ETF flow data: shares_outstanding × NAV → flow_confirmation [0–100]. Baseline snapshot written. |
| 2026-06-04 | `.claude/commands/catalyx-heatmap.md` | — | Wired to sector_scorer + momentum_engine + flow_data; 7-day study freshness gate |
| 2026-06-04 | `.claude/commands/catalyx-thesis.md` | — | close: tax_engine + thesis_scorer replace manual computation |
| 2026-06-04 | `catalyx/scorer/momentum_engine.py` | new | Cross-sectional percentile normalization (17 sectors from yfinance snapshot) |
| 2026-06-04 | `catalyx/scorer/sector_scorer.py` | new | Composite formula orchestrator: calls catalyst_scorer + momentum_engine + flow_data |
