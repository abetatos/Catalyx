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
| History backfill | `catalyx/data/backfill_history.py` | Writes indicator history to the **lake** (yfinance for market-priced indicators + cited note values) so the percentile path activates. No longer touches YAMLs. CLI: `uv run python -m catalyx.data.backfill_history [--dry-run]`; one-off `--migrate-yaml` (inline value_history → lake). |
| **Parquet lake** | `catalyx/store/lake.py` | **Tier 2 source of truth.** Append-only, partitioned parquet (one logical table = folder of partition files), committed to git. `append_partition` (immutable), `read_table` (union via glob), `connect()` (DuckDB views). market_data/flow_data dual-write here; snapshot_repo write-throughs here. CLI: `uv run python -m catalyx.store.lake {tables,ls,read,seed-from-history}` |
| Indicator history | `catalyx/store/indicator_history.py` | Externalized `value_history` (was inline in catalyst YAMLs). Lake table `indicator_history` partitioned by catalyst_id. `history_for` / `write_catalyst` / `append_observation`. `intensity_engine` reads here first (YAML fallback for unmigrated catalysts). |
| Model portfolios | `catalyx/execution/portfolio.py` | **Fase D.** Deterministic model portfolios = `(score_run × strategy)`. Reads lake `sector_snapshot`, applies the strategy (filter → dedupe-by-ETF → rank/weight → water-fill cap), records `entry_price` (from lake momentum) + `config_version`, writes `portfolio_holding`. **4 strategies** in `config/portfolios/*.yaml`: `momentum` / `conviction` / `equal` / `low_crowding` (genuinely different selection+weights). CLI: `uv run python -m catalyx.execution.portfolio {profiles,build,build-all,show}`. |
| NAV engine | `catalyx/execution/nav_engine.py` | **Fase D.2.** Buy-and-hold NAV series (indexed 100) from holdings — model OR real — vs benchmark (**SPY/S&P500**). `holdings_nav` (newly-listed/short-history ETFs → held as cash, never poison the series), `compute_model_nav(--backtest-days N)` = trailing backtest answering "¿batimos mercado?", `compute_real_nav` → lake `portfolio_nav`. CLI: `… nav_engine {model,real,show}`. |
| Trade logger | `catalyx/execution/trade_logger.py` | **Fase D.2.** Real-money leg: `log_trade` (carries `thesis_id`+`run_id` lineage) → lake `portfolio_trade`; `real_holdings` reduces the log to net positions (qty, avg EUR cost, realized P&L) that feed nav_engine. EUR only. CLI: `… trade_logger {log,holdings,trades} <portfolio_id>`. |
| Lake query | `catalyx/store/lake_query.py` | **Fase E.** Unified DuckDB read-path over the lake — the day-to-day query layer and the data foundation for the GitHub-Pages dashboard (DuckDB-WASM runs the same SQL in-browser). Read-only, defensive (empty table → empty result). `sector_history` / `latest_ranking` / `rank_moves` / `portfolio_compare` / `portfolio_holdings` / `lineage_for_trade` / `sql`. CLI: `… lake_query {ranking,sector,moves,portfolios,holdings,lineage,sql}`. `snapshot_repo` read queries (history/runs/events) now read the lake too. |
| Dashboard (Pages) | `site/` + `scripts/build_site.py` + `.github/workflows/pages.yml` | **Fase F.** Static **DuckDB-WASM** dashboard — reads the committed parquet lake in-browser (no backend, no DVC). Tabs: ranking, sector history, model portfolios, rank moves, lineage, SQL console. `build_site.py` bakes parquet + `manifest.json` into `dist/`; the workflow builds + deploys to Pages on push to `main`. **Live: https://abetatos.github.io/Catalyx/** Preview locally: `uv run python scripts/build_site.py && python -m http.server -d dist 8000`. (Replaces the earlier Evidence.dev `dashboard/` — its workflow was removed.) |

**Storage architecture — three tiers (parquet-first).** See `docs/PLAN_lake_dvc_serving.md`.
- **Tier 1 (git, hand-edited):** config YAML, schemas, and the JSON *documents* skills Read/Write directly (sector_studies, theses, catalysts, taxonomy_proposals). These stay JSON forever — they are the skill interface. Never migrated.
- **Tier 2 (parquet lake, git):** all computed time-series — momentum/flow snapshots, score_run/sector_snapshot/rank_event, indicator history, portfolios. Durable, versioned, queryable. Claude never Reads parquet directly — skills get tabular data via a Python CLI emitting JSON to stdout.
- **Tier 3 (gitignored, rebuildable):** `data/catalyx.db`. A query cache derived from the lake. Rebuild any time: `uv run python -m catalyx.store.snapshot_repo rebuild`.

During migration, the snapshot writers keep a **compat JSON** alongside the parquet (`momentum_engine` reads the lake by default; `--snapshot path.json` forces JSON). The old `snapshot_repo export` to `data/history/` is **deprecated** — the partitioned lake replaces it.

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

> **Legend:** `✅` = built and on disk today (Phase 0.5). Unmarked entries are the
> TARGET architecture (Phase 1+) and **do not exist yet** — do not assume you can
> read them. Before citing a path from this tree, confirm it is `✅` or run a quick
> `ls`/glob. This tree is a roadmap, not an inventory.

```
catalyx/
├── CLAUDE.md                          ← THIS FILE — always read first  ✅
├── .claude/
│   ├── settings.json                  ← Hooks: auto-validation on schema edits  ✅
│   └── commands/                      ← 7 catalyx-* skill definitions  ✅
├── catalyx/                           ← Main Python package
│   ├── scanner/                       ← (planned, Phase 1/2)
│   │   ├── signal_ingester.py
│   │   ├── novelty_filter.py
│   │   ├── catalyst_detector.py
│   │   ├── strength_scorer.py
│   │   └── structural_monitor.py      ← Phase 2
│   ├── scorer/
│   │   ├── catalyst_scorer.py         ← catalyst_alignment (confirms/contradicts + decay)  ✅
│   │   ├── intensity_engine.py        ← structural intensity from semaphores  ✅
│   │   ├── momentum_engine.py         ← cross-sectional percentile rank  ✅
│   │   ├── sector_scorer.py           ← composite orchestrator  ✅
│   │   ├── flow_engine.py             ← (planned — live today as data/flow_data.py)
│   │   └── valuation_engine.py        ← (planned — valuation_relative still manual)
│   ├── thesis/                        ← (planned, Phase 1)
│   │   ├── thesis_builder.py
│   │   ├── thesis_validator.py
│   │   ├── assumption_monitor.py
│   │   └── invalidation_watcher.py
│   ├── execution/
│   │   ├── tax_engine.py              ← Spanish CGT progressive brackets  ✅
│   │   ├── trade_logger.py            ← (planned)
│   │   └── pnl_engine.py              ← (planned)
│   ├── attribution/
│   │   ├── thesis_scorer.py           ← right_reason_score  ✅
│   │   └── return_decomposer.py       ← (planned)
│   ├── feedback/                      ← (planned, Phase 3)
│   │   ├── prior_updater.py
│   │   └── pattern_reporter.py
│   ├── sector_study/                  ← (planned, Phase 1 — studies are JSON today)
│   │   ├── study_builder.py
│   │   ├── study_updater.py
│   │   └── watch_trigger_monitor.py
│   ├── data/
│   │   ├── market_data.py             ← yfinance momentum fetcher  ✅
│   │   ├── flow_data.py               ← shares_outstanding × NAV → flow_confirmation  ✅
│   │   ├── cot_data.py                ← (planned — CFTC COT parser)
│   │   ├── news_adapter.py            ← (planned)
│   │   ├── cb_calendar.py             ← (planned)
│   │   └── llm_client.py              ← (planned — Anthropic+OpenAI, logs all calls)
│   ├── store/
│   │   ├── db.py                      ← SQLAlchemy engine + LLMLog  ✅
│   │   ├── catalyst_repo.py           ← CatalystEvent + TaxonomyGapProposal CRUD  ✅
│   │   ├── sector_study_repo.py       ← SectorStudy CRUD  ✅
│   │   ├── structural_catalyst_repo.py← StructuralCatalyst CRUD  ✅
│   │   ├── thesis_repo.py             ← Thesis + ClosedThesis CRUD  ✅
│   │   ├── snapshot_repo.py           ← (planned)
│   │   ├── trade_repo.py              ← (planned)
│   │   └── prior_repo.py              ← (planned — CatalystSectorPrior table)
│   ├── cli/
│   │   ├── main.py                    ← Phase 0.5 stub (lists module CLIs)  ✅
│   │   ├── cmd_scan.py                ← (planned)
│   │   ├── cmd_score.py               ← (planned)
│   │   ├── cmd_thesis.py              ← (planned)
│   │   ├── cmd_trade.py               ← (planned)
│   │   └── cmd_feedback.py            ← (planned)
│   └── config/
│       ├── sector_taxonomy.yaml       ← CANONICAL: all sector IDs live here  ✅
│       ├── catalyst_taxonomy.yaml     ← Catalyst types and subtypes enum  ✅
│       ├── etf_universe.yaml          ← ETFs per sector (quarterly review)  ✅
│       ├── scoring_weights.yaml       ← Dimension weights — SINGLE SOURCE OF TRUTH  ✅
│       ├── weights.py                 ← Loads scoring_weights.yaml for all scorers  ✅
│       └── structural_catalysts/      ← One .yaml per structural catalyst  ✅
│           ├── ai_capex_supercycle.yaml        ✅
│           ├── cb_gold_accumulation.yaml       ✅
│           ├── copper_datacenter_demand.yaml   ✅
│           ├── energy_transition_grid.yaml     ✅
│           └── nato_rearmament.yaml            ✅
├── schemas/                           ← JSON Schema files (source of truth for objects)  ✅
│   ├── catalyst_event.json            ✅
│   ├── structural_catalyst.json       ✅
│   ├── sector_snapshot.json           ✅
│   ├── sector_study.json              ✅
│   ├── thesis.json                    ✅
│   ├── closed_thesis.json             ✅
│   └── taxonomy_gap_proposal.json     ← Discovery Pass output  ✅
├── data/                              ← Runtime data  ✅
│   ├── catalysts/  snapshots/  theses/  sector_studies/  taxonomy_proposals/  reports/  ✅
│   └── catalyx.db                     ← SQLite (gitignored)  ✅
├── tests/
│   ├── unit/
│   │   ├── test_tax_engine.py         ← bracket + carry-forward edge cases  ✅
│   │   ├── test_strength_scorer.py    ← (planned)
│   │   └── test_return_decomposer.py  ← (planned)
│   └── integration/                   ← (planned)
├── notebooks/                         ← (planned)
├── docs/
│   └── SPEC_v1.1.md                   ← (referenced; verify before citing)
├── pyproject.toml                     ✅
└── .env.example                       ✅
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
| CLI commands | `catalyx/cli/main.py` (Phase 0.5 stub today; `cmd_*.py` are Phase 1, not built) |
| LLM integration | _(planned)_ `catalyx/data/llm_client.py` — all calls must go through this, pinned model IDs only. **Not built yet** — Phase 0.5 uses the Claude Code session directly |
| Feedback loop / priors | `schemas/closed_thesis.json` → `CatalystSectorPrior` table _(planned)_ `store/prior_repo.py` (not built yet) |
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

**Correlated-catalyst allocation cap:** theses sharing the same primary structural catalyst are correlated (they rise/fall together). The combined allocation across them is capped by `correlated_catalyst_cap.max_combined_pct` in `scoring_weights.yaml` (default **20%**). This is DISTINCT from the per-position `conviction_tiers` ceiling (12/8/4%). The cap is **flexible**: `enforcement: "warn"` means a breach is flagged and requires an explicit `correlation_note` override, but is not prohibited. Set `enforcement: "block"` to make it a hard block.

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

Users rank catalysts with `user_rank` (integer, 1 = highest priority). **v1.5: `user_rank` is a display ORDERING tiebreaker, not a score multiplier.**

`display_priority = algorithmic_score` (the computed intensity). Catalysts are ranked by `algorithmic_score` descending, with `user_rank` (1 = highest) breaking ties only. This honors user preference among near-equals but never lets a weaker catalyst leapfrog a materially stronger one.

> The old multiplicative table (`user_rank ×1.40…0.60`) is **deprecated** — kept in `scoring_weights.yaml` (`user_rank_multipliers`) for one major version per the Schema Change Protocol, but no longer applied. Config: `user_rank_ordering`.

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
12. Taxonomy Gap Review           ← contextualize each pending proposal, then ASK user (promote/reject/defer)
```

**Why Step 3 before Step 5:** The heatmap ranks ALL investable sectors (`sector_scorer --universe`), but a sector with a fresh study scores on every dimension (catalyst_alignment + crowding from `analyst_narrative_score`/`narrative_maturity`), whereas a sector without one ranks on a momentum-only baseline (catalyst_alignment=0, default crowding). Running studies first means the catalyst-driven sectors are scored on full information; momentum-only sectors still appear (flagged) as study candidates. A STALE study is worse than none — it injects misleading full-dimension scores — hence the 7-day freshness gate blocks the heatmap.

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
`intensity.current_score` MUST be derived from the **continuous indicator scores** using the formula in `scoring_weights.yaml` (v1.5: `round(clamp(indicator_avg + trend_delta, 10, 95), 1)`). Each indicator is scored to a continuous [0,100] (empirical percentile of its `value_history` once ≥ `min_history_points`, else a **saturating threshold curve** — weak→50, strong→80, asymptoting to 100 far above strong) — **not** the old 🟢/🟡/🔴 100/65/20 buckets. The color is a display-only label derived from the score. Run `/catalyx-update` after every indicator change — it recomputes intensity automatically. **Indicator `value_history` lives in the parquet lake** (`data/lake/indicators/`, table `indicator_history` keyed by catalyst_id) — externalized from the YAMLs (schema 1.4, inline field deprecated). `intensity_engine` reads the lake first, falling back to inline YAML `value_history` only for unmigrated catalysts. Backfill market-priced indicators with `uv run python -m catalyx.data.backfill_history` (writes to the lake); new observations append via `catalyx.store.indicator_history.append_observation`. Only `computation_method: "bootstrap"` allows manual values, and only at file creation.

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
12. Taxonomy Gap Review — for each pending proposal: present a context block (thesis / why now / ETF coverage / relation to existing sectors / strength·novelty / risk), then ASK the user (promote / reject / defer). Never decide automatically.

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
- [x] Portfolio correlation enforcement in `/catalyx-thesis draft` + monthly-review Step 9 — combined allocation checked against `correlated_catalyst_cap` (20%, flexible "warn"); Step 9 now asks the user per draft candidate (AskUserQuestion)
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
| 2026-06-05 | `catalyx/execution/portfolio.py` + `nav_engine.py` + `config/portfolios/*` (4 strategies) + `site/*` (redesign) + `catalyx-monthly-review.md` (Step 5b) | v2.5 | **Portfolio strategies + market comparison + dashboard redesign.** Portfolios are now 4 distinct **strategies** (momentum/conviction/equal/low_crowding) — replaces the 3 risk profiles that produced near-identical weights; each holding records `entry_price`. `nav_engine` gained `--backtest-days` (trailing backtest of current holdings vs **SPY**) → all 4 beat the market over 180d (momentum +41.9% vs SPY +11.4%). Fixed `holdings_nav` so newly-listed ETFs (no window history) are held as cash instead of poisoning the whole series via row-wise dropna. **Dashboard v3:** light/clean theme (was dark), cards + progress bars + sparklines (catalysts show indicator score-bars + history sparklines; portfolios show NAV-vs-SPY sparkline + "batimos mercado"), studies as structured docs (no raw JSON), event-catalyst summary fixed (was reading the wrong field → now `description`). Consolidated the duplicate dev run. Monthly-review Step 5b builds portfolios + NAV. 82 tests green. |
| 2026-06-05 | `site/index.html` + `site/app.js` (new) + `scripts/build_site.py` (new) + `.github/workflows/pages.yml` (new) | v2.4 | **Fase F — DuckDB-WASM dashboard, LIVE on GitHub Pages.** Static site reads the committed parquet lake in-browser (no backend): ranking, sector history, model portfolios, rank moves, lineage, SQL console. `build_site.py` bakes parquet + manifest into `dist/`; Actions deploys to **https://abetatos.github.io/Catalyx/** on push. Replaced the prior Evidence.dev `dashboard/` (removed `deploy-dashboard.yml` — both were deploying to the same Pages URL). Fixes during bring-up: tz-safe `substr(snapshot_at::VARCHAR,1,10)` (lake mixes tz-aware/naive timestamps → `CAST … AS DATE` fails in DuckDB), `portfolio_nav` guard (graceful when no NAV yet), and inlined SQL literals instead of DuckDB-WASM prepared statements (bind path was breaking the parameterised tabs). Committed scoped to self-contained files; tree WIP untouched. |
| 2026-06-05 | `catalyx/store/lake_query.py` (new) + `snapshot_repo.py` (reads → lake) | v2.3 | **Fase E — unified DuckDB read-path.** `lake_query`: read-only analytical queries over the lake (the page's data layer; DuckDB-WASM will run the same SQL in-browser) — `sector_history`, `latest_ranking`, `rank_moves`, `portfolio_compare`, `portfolio_holdings`, `lineage_for_trade` (trade → run → reports + snapshot), ad-hoc `sql`. Defensive: empty table → empty result. `snapshot_repo.history/list_runs/rank_events` repointed from SQLite to the lake (parquet-first reads complete; SQLite now only a cache + external-tool surface). Verified on the real lake (ranking, sector history, portfolio aggregates). 5 new tests, 82 total green. |
| 2026-06-05 | `catalyx/execution/nav_engine.py` (new) + `trade_logger.py` (new) + `schemas/thesis.json` (1.3) + `lake.py` | v2.2 | **Fase D.2 — NAV-over-time + real-money log + lineage.** `nav_engine`: buy-and-hold NAV series (indexed 100) from holdings — model or real — vs benchmark; price source injectable (yfinance default) → lake `portfolio_nav` (one file/portfolio). `trade_logger`: real trades (with `thesis_id`+`run_id` lineage) → `portfolio_trade`; `real_holdings` derives net positions + realized P&L feeding the same NAV math, so model-vs-real curves are comparable (execution alpha). Thesis schema 1.2→1.3 (enum-tolerant): `metadata.lineage` (origin_run_id/report/heatmap_rank) → trade→thesis→run_id→report+snapshot is one join. End-to-end verified on real yfinance prices (67-pt real NAV). 8 new tests, 77 total green. |
| 2026-06-05 | `catalyx/execution/portfolio.py` (new) + `schemas/portfolio.json` (new) + `config/portfolios/{conservative,balanced,aggressive}.yaml` (new) | v2.1 | **Fase D.1 — model portfolios by risk profile.** Deterministic, network-free: a portfolio = `(score_run × risk_config)`. `build_model_holdings` reads lake `sector_snapshot`, applies the profile (filter on composite/momentum/crowding/narrative → dedupe-by-ETF → top-N → composite-proportional weights water-filled under `max_position_pct`), persists to lake `portfolio_holding` (partition portfolio_id+run_id) tagged with `config_version` (md5 of the profile). 3 profiles built from the current run show clean risk separation (conservative drops all `crowded` AI/semis → 5 emerging/mainstream names @ ~20%; aggressive rides them → 12 @ ~8%). 7 new tests, 69 total green. NAV-over-time + real-money trades + thesis/trade lineage = next. |
