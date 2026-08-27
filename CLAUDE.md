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
- Assumption critique and discussion       - File + parquet-lake reads/writes
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

### Phase 0.5 — Skill + Python Data Layer (current, and PERMANENT model)
**Goal:** Claude remains the conversational interface and intelligence layer, running as a **skill on the Claude Code session** (leveraging its credits + WebSearch). Python handles deterministic computation, data storage, and market data fetching. Skills call Python modules via `uv run python -m catalyx.*`. **This is not a stepping stone toward a self-hosted LLM/API** — see the roadmap note below.
**Architecture principle:** Python = infrastructure (formulas, parquet lake, fetching). Claude = reasoning, analysis, thesis formulation, discussion. There is **no database** — persistence is files (Tier 1) + the parquet lake (Tier 2).

| Component | Tool |
|---|---|
| News scanning | Claude WebSearch (Claude Code session) |
| Position opening (movements) | Claude via `/catalyx-open` (conversational + Write to `data/movements/*.json`) |
| Market data / momentum | `catalyx/data/market_data.py` (yfinance) |
| Deterministic scoring formulas | Python modules callable from skills |
| Storage | JSON/YAML documents in `data/` + `catalyx/config/` (Tier 1) + parquet lake `data/lake/` (Tier 2). No DB. |
| P&L / tax | `catalyx/execution/tax_engine.py` (Spanish CGT) |
| Scheduling | CronCreate (limited) |

**Claude model:** whatever the Claude Code session runs (Opus/Sonnet). No self-hosted LLM/API client — the session IS the LLM.

### Python infrastructure already built (Phase 0.5)

> One line per module = function + CLI. Design rationale lives inline in each file and in the
> cited `docs/DESIGN_*`/`PLAN_*`. All CLIs run as `uv run python -m <module>`.

| Module | Path | Function + CLI |
|---|---|---|
| Catalyst reader | `store/catalyst_repo.py` | Reads `CatalystEvent` + `TaxonomyGapProposal` (`data/catalysts/`, `data/taxonomy_proposals/`). CLI `{summary,get,set-status}` |
| Sector study reader | `store/sector_study_repo.py` | Reads `SectorStudy` (`data/sector_studies/`). CLI `{summary,get,stale}` |
| Movement reader | `store/movement_repo.py` | Reads `Movement` (`data/movements/*.json`, Tier-1). Derives `positions()` + `catalyst_ledger()`; `ingest` backfills point-in-time `score_context` (no look-ahead) + write-throughs lake. CLI `{summary,get,positions,ledger,ingest}`. `docs/PLAN_movement_restructure.md` |
| Structural catalyst reader | `store/structural_catalyst_repo.py` | Reads `StructuralCatalyst` (`config/structural_catalysts/*.yaml`). CLI `{summary,get}` |
| Market data | `data/market_data.py` | yfinance ETF momentum fetcher → `data/snapshots/momentum_snapshot_*.json` + lake. CLI (no args) |
| Intensity engine | `scorer/intensity_engine.py` | `intensity.current_score` from indicators. CLI `--all [--write-back]` |
| Catalyst scorer | `scorer/catalyst_scorer.py` | confirms/contradicts/independent + decay → `catalyst_alignment`; emits `regime_state` (additive). CLI `<sector_id> [--all]` |
| Structural monitor | `thesis/structural_monitor.py` | Fundamentals-health verdict feeding `regime_state` (intact/contested/breaking). `docs/DESIGN_catalyst_regime_discrimination.md`. CLI `[--all]` |
| Momentum engine | `scorer/momentum_engine.py` | Cross-sectional percentile → `momentum_score`. CLI `[--snapshot path]` |
| Sector scorer | `scorer/sector_scorer.py` | Composite orchestrator → full SectorSnapshot. CLI `<sector_id> [--all --flow N --crowd N]` |
| Dislocation lens | `scorer/dislocation.py` | corr/beta engine → **opportunity** (panic dip) + **diversifier** (rotation target); call is Claude's. CLI `[--window 5 --lookback 90]` |
| Entry timing | `scorer/entry_timing.py` | Recommend-only *when*: micro-tension (RSI/stretch/vol/state) + event overhang → `suggested_verdict`. Config `scoring_weights.yaml` `entry_timing`. CLI `<sector_id>\|--all [--json]` |
| Technical study | `scorer/technical_study.py` | Opt-in deep pre-open TA dossier (superset of entry_timing: MA/MACD/Bollinger/ATR/S-R/volume/OBV/52w → `technical_posture`). Ephemeral. CLI `<sector_id> [--ticker TICK] [--json]` |
| Exit watcher | `scorer/exit_watcher.py` | Sell-signal Family 1: evaluates `risk_discipline.invalidation[]` stops deterministically + assumptions + regime + **FX-correct EUR drawdown floor** (−20 reduce/−30 exit vs real cost, `nav_engine` FX) + **catalyst freshness** (`status_last_reviewed` age; >45d forces re-verify) + after-tax P&L → Exit/Reduce/Watch/Hold. Doctrine: freshness dominates, a drawdown triggers a re-verify not an auto-sell (§Family 1b). Recommend-only, persists `exit_signal`. `docs/DESIGN_sell_signals.md`. CLI `[--json] [--no-persist]` |
| Tax engine | `execution/tax_engine.py` | Spanish CGT 2026 brackets (19/21/23/27%), incremental + YTD. CLI `--gain N [--ytd-prior N --loss N]` |
| Outcome engine | `attribution/outcome.py` | Closed-experiment ledger: realized after-tax P&L + right-thesis×right-reason VERDICT + behavioral flags. Human inputs captured at `/catalyx-close`. Writes lake `validation/movement_outcome`. CLI `{evaluate <mov_id> [--write-back],summary,report}` |
| Flow data | `data/flow_data.py` | shares_outstanding × NAV → `flow_confirmation`; W/W delta needs prior snapshot. CLI `[--write]` |
| History backfill | `data/backfill_history.py` | Writes indicator history to the lake (activates percentile path). CLI `[--dry-run]`; one-off `--migrate-yaml` |
| **Parquet lake** | `store/lake.py` | **Tier 2 source of truth.** Append-only partitioned parquet, git-committed. CLI `{tables,ls,read,seed-from-history}` |
| Indicator history | `store/indicator_history.py` | Externalized `value_history` → lake table `indicator_history` by catalyst_id. `intensity_engine` reads here first |
| Model portfolios | `execution/portfolio.py` | 4 strategies (`momentum`/`catalyx`/`equal_weight`/`low_crowding`) in `config/portfolios/*.yaml`: filter→rank→weight-transform (proportional/softmax)→cap→deadband. Records `portfolio_holding` + `portfolio_catalyst_exposure`. CLI `{profiles,build,build-all,show}` |
| NAV engine | `execution/nav_engine.py` | Buy-and-hold NAV (indexed 100) model OR real vs **SPY** → lake `portfolio_nav`. CLI `{model,real,show}` |
| Lake query | `store/lake_query.py` | Read-only DuckDB read-path (also the dashboard's data layer): `ranking,sector,moves,portfolios,holdings,ledger,lineage,catalyst-exposure,sql`. CLI same |
| Dashboard (Pages) | `site/` + `scripts/build_site.py` + `.github/workflows/pages.yml` | Static DuckDB-WASM dashboard over the committed lake. Live: https://abetatos.github.io/Catalyx/ · Local: `uv run python scripts/build_site.py && python -m http.server -d dist 8000` |

**Storage architecture — two tiers (parquet-first, no database).** See `docs/PLAN_lake_dvc_serving.md`.
- **Tier 1 (git, hand-edited):** config YAML, schemas, and the JSON *documents* skills Read/Write directly (sector_studies, theses, catalysts, taxonomy_proposals). These stay JSON forever — they are the skill interface. The `*_repo.py` modules read these files directly and print digests; writing a file IS the registration (no import step).
- **Tier 2 (parquet lake, git):** all computed time-series — momentum/flow snapshots, score_run/sector_snapshot/rank_event, indicator history, portfolios. Durable, versioned, queryable. Claude never Reads parquet directly — skills get tabular data via a Python CLI emitting JSON to stdout (`lake_query`, `snapshot_repo`).

**SQLite was removed entirely (2026-06-05).** It used to be a Tier-3 query cache, but it was never the source of truth (the files and the lake are), and the `llm_log` table it carried is obsolete now that there is no self-hosted LLM. Reads/writes of computed series go through `catalyx.store.lake`. There is no `CATALYX_DB_URL`, no `init`, no SQLAlchemy.

**Skills call Python modules** using `uv run python -m catalyx.<module> <command>` via Bash tool. This is the integration model — not a separate CLI for the user, but Python as a deterministic backend that skills invoke.

---

> **Direction decision (2026-06-05):** CATALYX stays a **skill on the Claude Code session
> — permanently.** It deliberately does NOT evolve into a self-hosted LLM product. The
> intelligence layer is Claude Code (its credits + WebSearch); the deterministic backbone is
> Python. Consequently the following are **off the roadmap, not "later"**: any `anthropic`/
> `openai` API client, an `llm_client.py`, the `llm_log` table, a Typer CLI built for an
> end-user, FastAPI, and the Postgres migration (its only purpose was scaling a relational DB
> we no longer have). What remains legitimately future is **pure deterministic Python + ML on
> our own closed-thesis data** — none of which needs a self-hosted LLM.

### Future work — deterministic Python only (no self-hosted LLM)
**Python version: 3.12.** Runtime deps are tracked in `pyproject.toml` (yfinance, pandas, pyarrow,
duckdb, jsonschema, pyyaml, ruamel-yaml, httpx, rich). Add a dependency only when a module needs it.

- **Scoring completeness:** `flow_engine` formalized, `return_decomposer` (attribution → lake
  `validation/`). _(`valuation_engine` was DROPPED 2026-06-06, not deferred — `valuation_relative`
  was removed from the composite in schema 1.2; a backtest showed no price-derived metric earns
  that weight. See `experiments/backtest_acceleration.py`.)_
- **Thesis lifecycle helpers:** assumption/invalidation monitors that re-check a thesis's data
  sources (the *checking* is deterministic; the *judgement* stays with Claude in the skill).
- **Feedback loop (ML on closed theses):** `xgboost` / `scikit-learn` on `ClosedThesis` data →
  Bayesian prior hit-rate per catalyst-sector pair. Catalyst novelty filtering via local
  `sentence-transformers` embeddings (`all-MiniLM-L6-v2`, no API cost). All offline, on our lake.
- **Backtesting:** historical catalyst reconstruction (GDELT, CFTC COT archive), walk-forward
  validation. **Critical constraint:** detection in backtest must use only data available at
  signal time — no look-ahead.

These are additive Python modules behind the same `uv run python -m catalyx.*` skill contract.
None of them changes the conversational interface or reintroduces a database.

---

## Repository Structure

> Only what exists on disk today is listed. Python module inventory + CLIs live in the
> module table above ("Python infrastructure already built"); planned/unbuilt modules live
> in "What Is Still Missing". Before citing any path, `ls`/glob to confirm.

```
catalyx/
├── CLAUDE.md                  ← THIS FILE — always read first
├── .claude/{settings.json, hooks/guard.py, commands/}   ← hooks (cross-platform) + 8 catalyx-* skills
├── catalyx/                   ← Python package
│   ├── scorer/    catalyst_scorer, intensity_engine, momentum_engine, sector_scorer,
│   │              dislocation, entry_timing, technical_study, exit_watcher
│   ├── execution/ tax_engine, nav_engine, portfolio
│   ├── attribution/ outcome
│   ├── thesis/    structural_monitor
│   ├── data/      market_data, flow_data, backfill_history
│   ├── store/     lake, lake_query, catalyst_repo, sector_study_repo,
│   │              structural_catalyst_repo, movement_repo, snapshot_repo, indicator_history
│   ├── cli/main.py            ← stub listing module CLIs (no unified user CLI by design)
│   └── config/    sector_taxonomy.yaml (CANONICAL sector IDs), catalyst_taxonomy.yaml,
│                  etf_universe.yaml, scoring_weights.yaml (SINGLE SOURCE weights), weights.py,
│                  portfolios/*.yaml, track_record.yaml, structural_catalysts/*.yaml
├── schemas/       catalyst_event, structural_catalyst, sector_snapshot, sector_study,
│                  movement (primary capital unit, replaced thesis), taxonomy_gap_proposal, portfolio
├── data/          catalysts/ sector_studies/ movements/ taxonomy_proposals/ reports/  (Tier 1, git)
│                  + lake/  ← parquet lake (Tier 2, git)
├── scripts/       build_site.py (dashboard), score_run.sh (record run + opportunity/regime facts),
│                  post_run.sh (portfolios + NAV refresh) — both shared by heatmap + review
├── site/ + .github/workflows/pages.yml   ← DuckDB-WASM dashboard
├── tests/unit/    (200+ tests)
├── docs/          SPEC_v1.1.md + DESIGN_*/PLAN_* (verify before citing)
└── pyproject.toml, CHANGELOG.md
```

---

## Key Files — What to Read When

This section tells Claude which files to read before working on each area. **Always read these before editing.**

| Working on... | Read first |
|---|---|
| Any data schema or Pydantic model | `schemas/<relevant>.json` |
| Sector scoring, heatmap | `catalyx/config/sector_taxonomy.yaml` + `schemas/sector_snapshot.json` |
| Opening/closing positions, attribution | `schemas/movement.json` + `docs/PLAN_movement_restructure.md` (Thesis→Movement) |
| Structural catalysts | `catalyx/config/structural_catalysts/<relevant>.yaml` + `schemas/structural_catalyst.json` |
| Tax engine or P&L | `docs/SPEC_v1.1.md` §Tax section — Spanish CGT brackets are progressive, no short/long term distinction |
| ETF selection logic | `catalyx/config/etf_universe.yaml` — check TER, AUM, replication type, spread |
| CLI commands | `catalyx/cli/main.py` (stub listing the module CLIs — there is no unified user CLI by design) |
| LLM / intelligence | The Claude Code session itself (its credits + WebSearch). There is no self-hosted LLM client — never add one. |
| Feedback loop / priors | `schemas/closed_thesis.json` → `CatalystSectorPrior` _(planned, ML on closed theses — no LLM)_ `store/prior_repo.py` (not built yet) |
| Taxonomy gaps / discovery | `schemas/taxonomy_gap_proposal.json` + `data/taxonomy_proposals/*.json` |
| Parquet lake / computed series | `catalyx/store/lake.py` (write/read primitive) + `catalyx/store/lake_query.py` (DuckDB read-path) |
| Catalyst / thesis / study reads | the file-backed `*_repo.py` — e.g. `python -m catalyx.store.catalyst_repo summary` (reads `data/`, no DB) |
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
3. If sector removed: grep for all `sector_id` references in `data/movements/` — open movements cannot reference removed sectors

---

## Critical Implementation Rules

**Currency:** All P&L in EUR. Non-EUR ETF returns converted at execution date. Tax computed in EUR always.

**Thesis IDs:** Human-readable slugs. Format: `thesis_YYYYMMDD_sectorid_keyword`. Never UUIDs for theses.

**Catalyst IDs:**
- Event: `cat_YYYYMMDD_keyword`
- Structural: `struct_keyword_keyword`

**ETF flow data:** Use shares_outstanding × NAV, NOT total AUM. AUM conflates price appreciation with net flows. iShares API provides shares_outstanding directly.

**LLM model IDs:** N/A — there is no self-hosted LLM. The intelligence layer is the Claude Code session; CATALYX never makes pinned API calls of its own and stores no model IDs. Do not reintroduce an API client.

**Crowding risk** is a scoring penalty, not a reward. High crowding subtracts from composite score.

**Dashboard language:** All user-facing dashboard copy (`site/index.html`, `site/app.js` strings, `scripts/build_site.py` baked text) is **English-only**. The user works in Spanish in chat, but never leak Spanish into rendered dashboard text. (Also marked inline at the top of `site/app.js` + `site/index.html`.)

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
0/1. /catalyx-scan (macro front door — run FIRST, before reading any file)
                                     C0  Macro & big-economy context (Fed/CPI/DXY + generic markets;
                                         frame geo around Trump / US admin / Europe — surfaces more)
                                     Pass 1: Discovery (market-led, no taxonomy) → gaps
                                     Pass 2: Classification → new events + Refresh existing catalysts (Δ)
                                     (review consumes this output; event:<id> mode does a lightweight
                                      single-catalyst refresh instead of the full scan)
2.  /catalyx-update               ← refresh stale indicators, recompute intensity
3.  /catalyx-sector-study         ← PREREQUISITE for heatmap (run for top-5 sectors + any gap sectors)
4.  /catalyx-dashboard            ← derives from updated catalyst YAMLs
5.  /catalyx-heatmap              ← requires updated sector studies
6.  /catalyx-review (Step 6)      ← open-position reviews (movements + risk_discipline + regime)
7.  /catalyx-review (Step 9)      ← position-open RECOMMENDATIONS (opening is /catalyx-open, separate)
8.  Catalyst exposure check       ← combined exposure per catalyst vs cap
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
| Open a position | Sector news + ETF data + which catalyst | Heatmap + `schemas/movement.json` + `data/sector_studies/study_<sector>.json` (via `/catalyx-open`) |
| Position review | Each `risk_discipline` assumption source + news | `data/movements/<mov>.json` + structural catalyst YAML + `regime_state` |
| Catalyst update | Source data for the indicator being updated | Specific `structural_catalysts/<id>.yaml` |

### Slash Commands (skills definidas en `.claude/commands/`)

| Comando | Archivo | Qué hace |
|---|---|---|
| `/catalyx-dashboard` | `.claude/commands/catalyx-dashboard.md` | Catalyst dashboard desde los YAMLs actuales |
| `/catalyx-heatmap` | `.claude/commands/catalyx-heatmap.md` | Sector heatmap rankeado por catalyst_alignment |
| `/catalyx-open <sector_id>` | `.claude/commands/catalyx-open.md` | **Operar (independiente del review).** Escribe un `Movement` (open/add/trim) atribuido a catalizador(es) → `data/movements/*.json` + ingest |
| `/catalyx-close <sector_id\|etf>` | `.claude/commands/catalyx-close.md` | **Operar.** Cierra posición → P&L realizado + CGT español, escribe close movement |
| `/catalyx-scan` | `.claude/commands/catalyx-scan.md` | **Macro front door** (corre PRIMERO en el review scheduled). C0 contexto macro/big-economy + Pass 1 Discovery (gaps) + Pass 2 nuevos CatalystEvent JSON **y refresh de cada catalizador existente** (Δ) |
| `/catalyx-update <id> <ind> <val>` | `.claude/commands/catalyx-update.md` | Actualiza indicador de catalizador estructural |
| `/catalyx-sector-study <sector_id>` | `.claude/commands/catalyx-sector-study.md` | Genera/actualiza SectorStudy JSON |
| `/catalyx-review [scheduled\|event:<catalyst_id>]` | `.claude/commands/catalyx-review.md` | Review/análisis (scan→…→heatmap→opportunities→position reviews→tax). Recomienda, no opera. Periódico o event-driven |

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

## Feedback Loop — Review Checklist

Run `/catalyx-review` (periodic, e.g. first Monday of the month, OR `event:<catalyst_id>` when a
catalyst fires). The skill handles ordering. **Operating (open/close) is separate** — done anytime
via `/catalyx-open` and `/catalyx-close`, never inside the review. Manual reminder of what review does:

0/1. `/catalyx-scan` (macro front door — run FIRST) — C0 macro/big-economy context (frame geo around Trump / US admin / Europe — surfaces more) + Pass 1 Discovery (market-led gaps) + Pass 2 new events above strength 55 AND refresh of every existing catalyst (Δ strengthen/weaken/invalidation). Review consumes this; compare to stored YAML, flag deltas. (`event:<id>` mode: lightweight single-catalyst refresh instead of the full scan.)
2.  `/catalyx-update` — refresh stale indicators, recompute intensity algorithmically
3.  `/catalyx-sector-study` — refresh sector studies for top-5 catalyst_alignment sectors
4.  `/catalyx-dashboard` — regenerate with updated data
5.  `/catalyx-heatmap` — re-rank with updated sector studies
6.  Open-position reviews — for each open movement, check `risk_discipline` + driving-catalyst regime → concrete recommendation
7.  Catalyst exposure check — combined exposure per catalyst vs `correlated_catalyst_cap`
8.  Tax snapshot YTD (realized from closing movements)
12. Taxonomy Gap Review — for each pending proposal: present a context block (thesis / why now / ETF coverage / relation to existing sectors / strength·novelty / risk), then ASK the user (promote / reject / defer). Never decide automatically.

---

## What Has Been Designed (Completed)

The full pipeline, all schemas, taxonomies, scoring weights, the Python scoring/execution/attribution
layer, the parquet lake + DuckDB read-path, and the dashboard are **built** — see the module table
("Python infrastructure already built") for the current inventory + CLIs, and CHANGELOG.md for when each
landed. Only open work is tracked below.

## What Is Still Missing (open TODOs only)

### Phase 0.5 (no code needed)
- [ ] SectorStudy for `eu_defense_prime_contractors` and `ai_infrastructure_data_centers` (both in top-5 catalyst_alignment)
- [ ] Schema migration: update existing catalyst YAMLs to schema v1.2 (add `narrative_maturity`, recompute `intensity` algorithmically)
- [ ] Update copper catalyst indicators with real market data (LME ~$13,965, hyperscaler capex ~$700B)

### Design gaps to fix
- [ ] `analyst_model_revision` event type in `catalyst_taxonomy.yaml` — the copper thesis alpha closes when Goldman/JPM update models; the scan skill currently misses this signal

### Future (Python only — no DB, no self-hosted LLM)
- [ ] `return_decomposer` → lake `validation/`
- [ ] ML feedback loop on closed theses (`prior_repo`, xgboost/sklearn — offline, no LLM)
- [ ] Backtesting harness (GDELT/COT, strict no-look-ahead)

---

## Recent Changes

> Last 5 entries — oldest rotate to [`CHANGELOG.md`](CHANGELOG.md). Read that file only on demand ("when did X change?", "why is field Y structured this way?").
> Convention: the *why* (bug description + fix rationale) lives inline in the modified file. The *what and when* lives here and in CHANGELOG.md.

| Date | File | Version | Change |
|---|---|---|---|
| 2026-08-04 | `catalyx/scorer/exit_watcher.py` + `catalyx/config/{scoring_weights.yaml,weights.py}` + `tests/unit/test_exit_watcher.py` + `docs/DESIGN_sell_signals.md` | v2.25 | **Exit watcher: FX-correct EUR drawdown floor + catalyst-freshness gate (user).** Triggered by a real miss — a EUR grid position sat at −21.7% flagged only `watch`, and a GBP semis position *looked* like −24% but was really −11%. Three defects fixed, all in `exit_watcher`. **(1) FX bug:** `_tax_view` marked `native_price × qty` against an EUR cost basis, so every non-EUR vehicle's P&L/drawdown was garbage (SEMI.L showed −23.8%, real EUR −11.0%; USPY.L +20.2%, real +4.4% — and its CGT estimate was inflated too). `assess` now FX-converts the vehicle columns to EUR via `nav_engine._eur_prices` before marking (stops still evaluate in NATIVE currency — their thresholds are native). **(2) Stops never fired:** the only price stops were "−20% for 10 CONSECUTIVE days" (`review_and_reduce`, and the run reset on any bounce — grid oscillated at −22% for weeks at 6/10). Added a **two-tier floor on the EUR drawdown vs real cost** (`evaluate_drawdown`): `reduce` at `drawdown_reduce_pct` −20, `exit` at `drawdown_exit_pct` −30, no consecutive-day gate. **(3) Stale verdict:** `regime_state`/assumptions are Claude-set and were 2 months old (`intensity.last_updated` looked fresh after a trend-only recompute). Added **catalyst freshness as a first-class input** (`catalyst_freshness`): reads each driving catalyst's `status_last_reviewed` (NOT `intensity.last_updated`), stalest driver governs, `>catalyst_staleness_max_days` 45 → `very_stale`. **Doctrine — freshness dominates, a drawdown is a trigger to RE-VERIFY not an auto-sell** (`drawdown_overlay_action`): only a FRESH+weakening verdict auto-acts (reduce/exit); FRESH+intact only `warn`s (a fear selloff on a live thesis → hold/add is Claude's call); a STALE verdict + drawdown forces a re-verify (protective reduce on the exit tier). Folds into `suggest_action` via `drawdown_action`/`reverify_required` — only ever RAISES the recommendation, stays recommend-only. Live run confirmed the fix: whole book's catalyst verdicts 60-64d stale → all flagged RE-VERIFY; WebSearch showed AI-capex ($700-900B 2026, +36% YoY) and grid (transformer lead times 48-60mo) both intact/accelerating → the selloffs were fear/rotation, hold/add not sells. New `exit_signals` config: `drawdown_reduce_pct`/`drawdown_exit_pct`/`catalyst_staleness_{warn,max}_days`. 213 tests green (+9). Adaptive review cadence (30d floor / ±10%-move or VIX pull-forward / 45d ceiling) documented in `DESIGN_sell_signals.md §Family 1b`; optional CronCreate automation pending user confirmation. |
| 2026-07-28 | `CLAUDE.md` + `CHANGELOG.md` + `.claude/commands/{catalyx-review,catalyx-scan,catalyx-heatmap}.md` + `.claude/{settings.json,hooks/guard.py}` + `scripts/{post_run,score_run}.sh` | v2.24 | **Token-cost reduction pass (user).** Three fronts. **(1) Context load:** CLAUDE.md 100KB→43KB (−57%, ~14k tokens saved EVERY session) — Recent Changes trimmed 26→5 rows (21 moved verbatim to the CHANGELOG archive, zero loss), the Repo Structure roadmap-tree collapsed to real files only, "What Designed/Missing" cut to open TODOs only, and the module table's inline design essays compressed to one line/module (every CLI kept exact). **(2) Execution cost:** `/catalyx-review` Step 3 default flipped from "study ALL ~46 sectors every cycle" (2M+ tokens) to **movement-driven + decision-relevant** refresh (open positions + scan-flagged drivers + stale entry-candidates + never-studied); a sector without a fresh study still ranks on its momentum baseline, so nothing is missed. Full-universe sweep is now opt-in (`full-studies`). New **EXECUTION MODEL** section: bulk-WebSearch / many-file phases (scan, studies, heatmap+portfolio, opportunities, position reviews, watch triggers) run in **subagents that return only digests**, so the main conversation stays a thin orchestrator holding compact summaries; only the two AskUserQuestion steps (9 open-recs, 12 gap review) stay in main (subagents can't ask the user). `/catalyx-scan`: C2b refresh no longer sweeps all ~30 catalysts (only findings-touched; the rest collapse to one "no change" line), analyst-revision queries 5→2 scoped to held sectors. **(3) Hooks + consolidation:** the `.claude/settings.json` hooks were **dead** (PowerShell + `$env:TOOL_OUTPUT`, neither exists on macOS/Linux) → ported to a cross-platform `.claude/hooks/guard.py` (reads the hook JSON on stdin) driving the schema/taxonomy/structural edit reminders + a new post-`snapshot_repo record` reminder. Two new shared scripts collapse narrated Bash chains into one call each: **`scripts/post_run.sh`** (Step 5b: portfolio build-all → per-strategy nav model/live → real nav → rotation; verbose → `data/reports/post_run_<date>.log`, compact NAV digest → stdout) and **`scripts/score_run.sh`** (record run + register-report → the 4 opportunity/regime scorers — the chain `catalyx-heatmap` steps 11-12 and `/catalyx-review` Step 5c BOTH narrated identically; now deduped, record/register → log, scorer JSON → stdout). `catalyx-open` left as-is (short, interactive, decision-heavy — not a delegation target). No schema/pipeline-contract change. |
| 2026-06-12 | `.claude/commands/catalyx-scan.md` + `catalyx-review.md` + `CLAUDE.md` + `data/sector_studies/*` + `data/reports/{heatmap,monthly_review}_20260612.md` + `data/lake/*` + `catalyx/config/structural_catalysts/*.yaml` | v0.5.1 | **Scan as macro front door + scheduled review run (release v0.5.1).** Two things, both backward-compatible. **(1) `catalyx-scan` reframed as the "macro front door":** new **Step C0 — Macro & Big-Economy Context** (generic Fed/CPI/DXY + Trump / US-admin / Europe / China framings, each its own query) and **Pass 2 → Classification + Refresh** (also refreshes every already-registered catalyst's state — strengthen/weaken/invalidation Δ — not just new events). `/catalyx-review` scheduled now runs the scan FIRST and consumes its output (Steps 0/1 merged); `event:<id>` does a lightweight single-catalyst refresh. **(2) Scheduled review 2026-06-12 committed:** 7 stale studies refreshed (copper, gold_physical/miners, grid, ai_infra, semis_memory, eu_defense), 9 intensities recomputed + written back, run `run_20260612_151007` recorded (snapshot/rank/momentum/flow/dislocation/entry_timing/exit_signal/4 portfolios+NAV/real NAV), heatmap + review reports registered. Macro: Iran/Hormuz energy shock (CPI 4.2%), gold −25% from ATH (CB buying intact), AI-capex digestion; space supercycle topped the ranking; real book +0.92% vs SPY −2.55% (5d). 204 tests green. |
| 2026-06-08 | `catalyx/scorer/technical_study.py` (new) + `catalyx/config/scoring_weights.yaml` (`technical_study`) + `weights.py` + `tests/unit/test_technical_study.py` (new) + `.claude/commands/catalyx-open.md` (Step 5.6) | v2.23 | **Deep technical study — opt-in pre-open TA dossier (new pipeline step, user-requested).** When you're about to open a position, `/catalyx-open` now ASKS (AskUserQuestion) whether you want to "revisar la acción a nivel micro" before committing capital — a deeper technical review than the always-on `entry_timing` overlay. New `technical_study.py` is a SUPERSET of `entry_timing` (embeds its micro-state verbatim, single source for RSI/state/verdict) that adds, deterministically from yfinance OHLCV: MA structure (SMA20/50/200 + slopes + 50/200 regime), MACD(12,26,9) + cross, Bollinger %B + bandwidth, ATR (abs + % of price → stop sizing), nearest swing support/resistance + distance, volume surge + OBV trend, 52-week range position → a `synthesis` that buckets each fact bullish/bearish/neutral and maps the net tally to a `technical_posture` ∈ constructive/mixed/weak. SAME doctrine as entry_timing/dislocation/regime: Python surfaces facts + a suggested posture, the enter/scale/wait call is Claude's (with the thesis + WebSearch). **Recommend-only, ephemeral** (NO lake, NO dashboard) — decision support at open-time, like a single-sector entry_timing run. Periods live in `scoring_weights.yaml` `technical_study` (single source of truth). First live run for the €500 MSCI World Semiconductors (SEMI.L) entry: posture **constructive** (net +2) — all MAs rising, 50>200, OBV accumulating, +63.7% vs 200d — but flagged the cautions (MACD just rolled over with a bearish cross, 87.6% of 52w range, two fresh AI event scares). 204 tests green (+24). |
| 2026-06-08 | `catalyx/config/track_record.yaml` (`total_capital_eur`) + `catalyx/config/weights.py` (`total_capital_eur()`) + `scripts/build_site.py` + `site/{app.js,index.html}` | v2.22 | **Positions page: committed-capital + cash model, and reframed the book's framing (user).** Two asks. **(1) Capital plan.** The real book is now funded with an explicit **€10,000 committed up front, deployed progressively as catalysts fire** — not a vague "invested" number. New `total_capital_eur` in `track_record.yaml` (read via `weights.total_capital_eur()`); `build_site` bakes `total_capital_eur` + **`cash_eur`** (= committed − cost basis of open positions) + `deployed_pct` into `positions`. The Positions summary strip gained a **committed-capital** card (with `% deployed`) and a **cash** card (dry powder · awaiting catalysts) — cash is now a first-class variable on the page. Today: €10k committed / €1.5k invested / **€8.5k cash** / 15% deployed. **(2) Framing.** Replaced the "⚠ entry by design — entry was *deliberately bad*, opened into the selloff, book *starts underwater on purpose*, a test of luck" box with a **"Capital plan — €10,000 committed · long-horizon · catalyst-driven"** card: capital deployed progressively, positions sized to conviction and held while the thesis holds — a long-term thesis-driven book, not short-term trading. (Per the user: the old copy read like gambling; this is long-term investing and the dashboard is meant to show rigor.) 180 tests green. |
