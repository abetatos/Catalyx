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

| Module | Path | What it does |
|---|---|---|
| Catalyst reader | `catalyx/store/catalyst_repo.py` | File-backed reader for `CatalystEvent` + `TaxonomyGapProposal` (reads `data/catalysts/` + `data/taxonomy_proposals/`). CLI: `python -m catalyx.store.catalyst_repo {summary,get,set-status}` |
| Sector study reader | `catalyx/store/sector_study_repo.py` | File-backed reader for `SectorStudy` (reads `data/sector_studies/`). CLI: `{summary,get,stale}` |
| Movement reader | `catalyx/store/movement_repo.py` | **Thesis→Movement (2026-06-06).** File-backed reader for `Movement` (reads `data/movements/*.json`, Tier-1 truth). Derives `positions()` (net book per ETF — same shape the old trade_logger fed nav_engine) + `catalyst_ledger()` (P&L attributed per catalyst by `attribution[].weight`). `ingest` backfills point-in-time `score_context` (score_run as-of `executed_at`, no look-ahead) + write-throughs a `movement` mirror + time-versioned `catalyst_performance` to the lake. CLI: `{summary,get,positions,ledger,ingest}`. See `docs/PLAN_movement_restructure.md`. |
| Structural catalyst reader | `catalyx/store/structural_catalyst_repo.py` | File-backed reader for `StructuralCatalyst` (reads `config/structural_catalysts/*.yaml`). CLI: `{summary,get}` |
| Market data | `catalyx/data/market_data.py` | yfinance ETF momentum fetcher. CLI: `uv run python -m catalyx.data.market_data` → `data/snapshots/momentum_snapshot_YYYYMMDD.json` |
| Intensity engine | `catalyx/scorer/intensity_engine.py` | Compute `intensity.current_score` from indicator semaphores. CLI: `uv run python -m catalyx.scorer.intensity_engine --all [--write-back]` |
| Catalyst scorer | `catalyx/scorer/catalyst_scorer.py` | v1.3/v1.4 confirms/contradicts/independent formula + event decay → `catalyst_alignment`. Also emits `regime_state` (intact/contested/breaking) per sector — additive, does NOT change the composite. CLI: `uv run python -m catalyx.scorer.catalyst_scorer <sector_id>` |
| Structural monitor | `catalyx/thesis/structural_monitor.py` | **Noise-vs-regime bridge.** Reads a structural's `indicators[]` + intensity history → fundamental-health verdict (`degrading`), independent of any event. Feeds `regime_state`: a lone `contradicts` event → `contested` (decays, reversible); fundamentals corroborating / ≥2 contradicts / deactivation → `breaking` (permanent rotation). See `docs/DESIGN_catalyst_regime_discrimination.md`. CLI: `uv run python -m catalyx.thesis.structural_monitor [--all]` |
| Momentum engine | `catalyx/scorer/momentum_engine.py` | Cross-sectional percentile rank from yfinance snapshot → `momentum_score [0–100]`. CLI: `uv run python -m catalyx.scorer.momentum_engine [--snapshot path]` |
| Sector scorer | `catalyx/scorer/sector_scorer.py` | Composite formula orchestrator: calls catalyst_scorer + momentum_engine → full SectorSnapshot scores. CLI: `uv run python -m catalyx.scorer.sector_scorer <sector_id> [--flow N --val N --crowd N]` |
| Dislocation lens | `catalyx/scorer/dislocation.py` | **Price-vs-fundamentals gap for capital deployment.** One corr/beta engine (yfinance 90d), two lenses: **opportunity** (fell hard + `intact` + catalyst-confirmed + drop is mostly *contagion* β·market, low idiosyncratic residual → panic dip to buy) and **diversifier** (healthy, LOW correlation to the stressed cluster → rotation target). Python computes the decomposition; the BUY/ROTATE call is Claude's. CLI: `uv run python -m catalyx.scorer.dislocation [--window 5 --lookback 90]` |
| Entry timing | `catalyx/scorer/entry_timing.py` | **Execution-timing overlay (recommend-only) — the *when*, not the *whether*.** Answers "¿buen momento o espero?" for a position already decided. Two facets: **micro-tension** (yfinance: RSI / stretch-vs-MA20 / realized-vol regime / 5d trend / drawdown / stabilization → state ∈ neutral/overbought/falling/basing; the `falling` gate is vol-deadbanded so a sub-noise 5d move reads neutral, not a coin-flip) + **event overhang** (reuses CatalystEvent — a near-term discrete event touching the sector via the study's `active_catalyst_ids`, e.g. a peer mega-IPO; NO separate registry). Emits a `suggested_verdict` (enter_now/scale_in/wait_stabilize/wait_event); the adverse-vs-bullish overhang call + final decision are Claude's. Thresholds in `scoring_weights.yaml` `entry_timing`. Does NOT touch the composite, never trades, no persistence. CLI: `uv run python -m catalyx.scorer.entry_timing <sector_id>|--all [--json]` |
| Technical study | `catalyx/scorer/technical_study.py` | **OPT-IN deep pre-open TA dossier (recommend-only) — the thorough cousin of `entry_timing`.** Offered at `/catalyx-open` (AskUserQuestion gate) on the exact vehicle you're about to buy when you want to "revisarlo todo antes de abrir". A SUPERSET of `entry_timing` (embeds its micro-state verbatim) that adds, from yfinance OHLCV: **MA structure** (SMA20/50/200 + slopes + 50/200 regime), **MACD**(12,26,9) + cross, **Bollinger** %B + bandwidth, **ATR** (abs + % of price → stop sizing), **support/resistance** (nearest swing-pivot below/above + distance), **volume** surge + **OBV** trend, **52-week** range position → a `synthesis` (bullish/bearish/neutral signal lists + `technical_posture` ∈ constructive/mixed/weak). Same doctrine: Python surfaces facts + posture, the enter/scale/wait call is Claude's. Ephemeral (NO lake, NO dashboard), like a single-sector entry_timing run. Periods in `scoring_weights.yaml` `technical_study`. CLI: `uv run python -m catalyx.scorer.technical_study <sector_id> [--ticker TICK] [--json]` |
| Exit watcher | `catalyx/scorer/exit_watcher.py` | **Sell-signal Family 1 (recommend-only) — reads the stops nobody read.** For each open position: evaluates `risk_discipline.invalidation[]` price stops DETERMINISTICALLY via the schema-1.1 structured eval fields (`eval_ticker`/`comparator`/`threshold`/`consecutive_days` → `fired`/`approaching`/`clear`, fires only after the breach holds the full consecutive-day window; `eval_ticker:null` ⇒ Claude-checks-with-WebSearch), rolls up `assumptions[].current_status`, crosses sector `regime_state`, and marks the position + after-tax exit P&L (`tax_engine`). Severity arbitration → Exit/Reduce/Watch/Hold (a fired `full_exit` overrides all). Writes nothing (D6); persists `exit_signal` lake table. Config: `scoring_weights.yaml` `exit_signals`. See `docs/DESIGN_sell_signals.md`. CLI: `uv run python -m catalyx.scorer.exit_watcher [--json] [--no-persist]` |
| Tax engine | `catalyx/execution/tax_engine.py` | Spanish CGT 2026 progressive brackets (19/21/23/27%). Incremental + YTD computation. CLI: `uv run python -m catalyx.execution.tax_engine --gain N [--ytd-prior N --loss N]` |
| Outcome engine | `catalyx/attribution/outcome.py` | **Closed-experiment ledger — the sell-side mirror of `score_context`, rebuilds the deleted `right_reason_score` on Movement.** Turns a closed/trimmed movement into a registered experiment: realized P&L gross + **after-tax** (`tax_engine`), the **right-thesis × right-reason VERDICT** (skill / luck / variance / correct_invalidation — separates a win-for-the-right-reason from a lucky one), and **behavioral flags** from the files alone, no network (`held_past_full_exit`, `exited_intact_at_loss` = the "sold too early / panic" shape, `discretionary_exit`, `overrode_signal`). Python computes P&L/verdict/flags; the human-judged inputs (`exit_note` in-the-moment, `assumption_resolution`, `catalyst_materialized`, `followed_signal`) are captured at `/catalyx-close` and live on the Tier-1 file (editable — later realizations append to `additional_notes`, `exit_note` never overwritten). No look-ahead (reads only the close + prior opening movements). Writes lake `validation/movement_outcome` (1 row/experiment) → dashboard Positions "Experiment ledger". CLI: `uv run python -m catalyx.attribution.outcome {evaluate <mov_id> [--write-back],summary,report}`. |
| Flow data | `catalyx/data/flow_data.py` | ETF shares_outstanding × NAV → `flow_confirmation [0–100]`. Writes to `data/snapshots/flow_snapshot_YYYYMMDD.json`. Week-over-week delta requires prior snapshot. CLI: `uv run python -m catalyx.data.flow_data [--write]` |
| History backfill | `catalyx/data/backfill_history.py` | Writes indicator history to the **lake** (yfinance for market-priced indicators + cited note values) so the percentile path activates. No longer touches YAMLs. CLI: `uv run python -m catalyx.data.backfill_history [--dry-run]`; one-off `--migrate-yaml` (inline value_history → lake). |
| **Parquet lake** | `catalyx/store/lake.py` | **Tier 2 source of truth.** Append-only, partitioned parquet (one logical table = folder of partition files), committed to git. `append_partition` (immutable), `read_table` (union via glob), `connect()` (DuckDB views). market_data/flow_data dual-write here; snapshot_repo write-throughs here. CLI: `uv run python -m catalyx.store.lake {tables,ls,read,seed-from-history}` |
| Indicator history | `catalyx/store/indicator_history.py` | Externalized `value_history` (was inline in catalyst YAMLs). Lake table `indicator_history` partitioned by catalyst_id. `history_for` / `write_catalyst` / `append_observation`. `intensity_engine` reads here first (YAML fallback for unmigrated catalysts). |
| Model portfolios | `catalyx/execution/portfolio.py` | **Fase D.** Deterministic model portfolios = `(score_run × strategy)`. Reads lake `sector_snapshot`, applies the strategy (filter → dedupe-by-ETF → rank by the strategy signal → **conviction-weight transform** → water-fill cap → **rebalance deadband**), records `entry_price` (from lake momentum) + `config_version`, writes `portfolio_holding`. **4 strategies** in `config/portfolios/*.yaml`: `momentum` / `catalyx` (flagship composite book) / `equal_weight` / `low_crowding` (genuinely different selection+weights). **Sizing is separate from selection** (v2.12): the weight TRANSFORM (`portfolio_weighting` in `scoring_weights.yaml`, override per profile) maps the raw score → weight before the cap — `proportional` (default) or `softmax` over the z-normalized score (`catalyx` opts in → real dispersion instead of a near-flat band). `rebalance_deadband_pct` keeps a weight within N pts of what's already held (turnover/CGT guard). **Catalyst decomposition (v2.21):** every build also records `portfolio_catalyst_exposure` — the notional book (€1000 assumed) split by catalyst, each holding's weight divided EQUALLY across the catalysts driving its sector (point-in-time from the studies' `active_catalyst_ids`; sectors with none → `uncatalyzed`; the cap remainder → `cash`) → the % of the book exposed to each catalyst, per rebalance, so it can be tracked over time. CLI: `uv run python -m catalyx.execution.portfolio {profiles,build,build-all,show}`. |
| NAV engine | `catalyx/execution/nav_engine.py` | **Fase D.2.** Buy-and-hold NAV series (indexed 100) from holdings — model OR real — vs benchmark (**SPY/S&P500**). `holdings_nav` (newly-listed/short-history ETFs → held as cash, never poison the series), `compute_model_nav(--backtest-days N)` = trailing backtest answering "¿batimos mercado?", `compute_real_nav` (real book = `movement_repo.positions`) → lake `portfolio_nav`. CLI: `… nav_engine {model,real,show}`. |
| Lake query | `catalyx/store/lake_query.py` | **Fase E.** Unified DuckDB read-path over the lake — the day-to-day query layer and the data foundation for the GitHub-Pages dashboard (DuckDB-WASM runs the same SQL in-browser). Read-only, defensive (empty table → empty result). `sector_history` / `latest_ranking` / `rank_moves` / `portfolio_compare` / `portfolio_holdings` / `catalyst_ledger` / `lineage_for_movement` (movement → catalysts → run → reports+snapshot) / `portfolio_catalyst_exposure` (a portfolio's notional book decomposed **by catalyst** per rebalance + a **time-weighted average** — how the book's catalyst mix shifts over time; reads the `portfolio_catalyst_exposure` lake table) / `sql`. CLI: `… lake_query {ranking,sector,moves,portfolios,holdings,ledger,lineage,catalyst-exposure,sql}`. `snapshot_repo` read queries (history/runs/events) now read the lake too. |
| Dashboard (Pages) | `site/` + `scripts/build_site.py` + `.github/workflows/pages.yml` | **Fase F.** Static **DuckDB-WASM** dashboard — reads the committed parquet lake in-browser (no backend, no DVC). Tabs: ranking, sector history, model portfolios, rank moves, lineage, SQL console. `build_site.py` bakes parquet + `manifest.json` into `dist/`; the workflow builds + deploys to Pages on push to `main`. **Live: https://abetatos.github.io/Catalyx/** Preview locally: `uv run python scripts/build_site.py && python -m http.server -d dist 8000`. (Replaced the earlier Evidence.dev `dashboard/`, now deleted — its workflow was already removed.) |

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
│   │   └── valuation_engine.py        ← (DROPPED 2026-06-06 — valuation_relative removed from composite, schema 1.2)
│   ├── thesis/                        ← (planned, Phase 1)
│   │   ├── thesis_builder.py
│   │   ├── thesis_validator.py
│   │   ├── assumption_monitor.py
│   │   └── invalidation_watcher.py
│   ├── execution/
│   │   ├── tax_engine.py              ← Spanish CGT progressive brackets  ✅
│   │   ├── nav_engine.py              ← model/real NAV vs SPY (real ← movement_repo)  ✅
│   │   └── pnl_engine.py              ← (planned)
│   ├── attribution/
│   │   ├── thesis_scorer.py           ← REMOVED 2026-06-06 (right_reason rebuilt in Fase 2)
│   │   └── return_decomposer.py       ← (planned — Fase 2)
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
│   │   └── cb_calendar.py             ← (planned)
│   ├── store/                         ← no DB — file readers + parquet lake
│   │   ├── lake.py                    ← parquet lake (Tier 2 source of truth)  ✅
│   │   ├── lake_query.py              ← DuckDB read-path over the lake  ✅
│   │   ├── catalyst_repo.py           ← CatalystEvent + TaxonomyGapProposal file reader  ✅
│   │   ├── sector_study_repo.py       ← SectorStudy file reader  ✅
│   │   ├── structural_catalyst_repo.py← StructuralCatalyst file reader  ✅
│   │   ├── movement_repo.py           ← Movement reader → positions + catalyst_ledger + ingest  ✅
│   │   ├── snapshot_repo.py           ← score-run history over the lake  ✅
│   │   ├── indicator_history.py       ← indicator value_history in the lake  ✅
│   │   └── prior_repo.py              ← (planned — CatalystSectorPrior, ML feedback loop)
│   ├── cli/
│   │   └── main.py                    ← stub listing module CLIs (no unified user CLI by design)  ✅
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
│   ├── movement.json                  ← Movement (primary capital unit, replaced thesis)  ✅
│   └── taxonomy_gap_proposal.json     ← Discovery Pass output  ✅
├── data/                              ← Runtime data  ✅
│   ├── catalysts/  theses/  sector_studies/  taxonomy_proposals/  reports/  ✅  (Tier 1, git)
│   └── lake/                          ← parquet lake (Tier 2, git): scores/snapshots/portfolios  ✅
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
0.  Macro & Geopolitical Context  ← WebSearch FIRST, before reading any file
1.  /catalyx-scan                 ← Pass 1: Discovery (market-led, no taxonomy) → gaps
                                     Pass 2: Classification (taxonomy-led) → new events
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
| `/catalyx-scan` | `.claude/commands/catalyx-scan.md` | WebSearch → nuevos CatalystEvent JSON |
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

0.  WebSearch: current macro/geo context — compare to stored YAML data, flag deltas
1.  `/catalyx-scan` — Pass 1: Discovery (market-led gaps) + Pass 2: new events above strength 55
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
- [x] Roadmap reframed: permanent skill-on-Claude-Code model (no self-hosted LLM/API/Postgres)
- [x] Phase 0.5 workflow documented (generate → critique → improve loop)
- [x] Signal-first architecture: Discovery Pass in `/catalyx-scan` (Pass 1 market-led, Pass 2 taxonomy-led)
- [x] `TaxonomyGapProposal` schema — tracks emerging themes not in taxonomy
- [x] Monthly taxonomy gap review step (Step 12) in `/catalyx-monthly-review`
- [x] `catalyx/store/lake.py` + `lake_query.py` — parquet lake (Tier 2 source of truth) + DuckDB read-path
- [x] `catalyx/store/catalyst_repo.py` — file-backed reader/CLI for `CatalystEvent` and `TaxonomyGapProposal`
- [x] `catalyx/store/sector_study_repo.py` — file-backed reader for `SectorStudy`
- [x] `catalyx/store/thesis_repo.py` — file-backed reader for `Thesis` + `ClosedThesis` + `tax-snapshot`
- [x] `catalyx/store/structural_catalyst_repo.py` — file-backed reader for `StructuralCatalyst`
- [x] `catalyx/data/market_data.py` — yfinance ETF momentum fetcher with fixed formula
- [x] SQLite removed entirely — persistence is files (Tier 1) + parquet lake (Tier 2)
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

### Future (Python only — no DB, no self-hosted LLM)
- [x] `catalyx/data/flow_data.py` → flow_confirmation scores from ETF shares_outstanding × price. Baseline snapshot written 2026-06-04. Week-over-week delta activates from next run.
- [x] ~~`valuation_engine`~~ **DROPPED 2026-06-06** — `valuation_relative` removed from the composite (schema 1.2). It was a constant-50 placeholder that never moved the ranking; a backtest (`experiments/backtest_acceleration.py`) showed no price-derived metric (incl. momentum acceleration) earns the 15% — it was redistributed proportionally to catalyst/momentum/flow/crowding.
- [ ] `return_decomposer` → lake `validation/`
- [ ] ML feedback loop on closed theses (`prior_repo`, xgboost/sklearn — offline, no LLM)
- [ ] Backtesting harness (GDELT/COT, strict no-look-ahead)

---

## Recent Changes

> Last 5 entries — oldest rotate to [`CHANGELOG.md`](CHANGELOG.md). Read that file only on demand ("when did X change?", "why is field Y structured this way?").
> Convention: the *why* (bug description + fix rationale) lives inline in the modified file. The *what and when* lives here and in CHANGELOG.md.

| Date | File | Version | Change |
|---|---|---|---|
| 2026-06-08 | `catalyx/scorer/technical_study.py` (new) + `catalyx/config/scoring_weights.yaml` (`technical_study`) + `weights.py` + `tests/unit/test_technical_study.py` (new) + `.claude/commands/catalyx-open.md` (Step 5.6) | v2.23 | **Deep technical study — opt-in pre-open TA dossier (new pipeline step, user-requested).** When you're about to open a position, `/catalyx-open` now ASKS (AskUserQuestion) whether you want to "revisar la acción a nivel micro" before committing capital — a deeper technical review than the always-on `entry_timing` overlay. New `technical_study.py` is a SUPERSET of `entry_timing` (embeds its micro-state verbatim, single source for RSI/state/verdict) that adds, deterministically from yfinance OHLCV: MA structure (SMA20/50/200 + slopes + 50/200 regime), MACD(12,26,9) + cross, Bollinger %B + bandwidth, ATR (abs + % of price → stop sizing), nearest swing support/resistance + distance, volume surge + OBV trend, 52-week range position → a `synthesis` that buckets each fact bullish/bearish/neutral and maps the net tally to a `technical_posture` ∈ constructive/mixed/weak. SAME doctrine as entry_timing/dislocation/regime: Python surfaces facts + a suggested posture, the enter/scale/wait call is Claude's (with the thesis + WebSearch). **Recommend-only, ephemeral** (NO lake, NO dashboard) — decision support at open-time, like a single-sector entry_timing run. Periods live in `scoring_weights.yaml` `technical_study` (single source of truth). First live run for the €500 MSCI World Semiconductors (SEMI.L) entry: posture **constructive** (net +2) — all MAs rising, 50>200, OBV accumulating, +63.7% vs 200d — but flagged the cautions (MACD just rolled over with a bearish cross, 87.6% of 52w range, two fresh AI event scares). 204 tests green (+24). |
| 2026-06-08 | `catalyx/config/track_record.yaml` (`total_capital_eur`) + `catalyx/config/weights.py` (`total_capital_eur()`) + `scripts/build_site.py` + `site/{app.js,index.html}` | v2.22 | **Positions page: committed-capital + cash model, and reframed the book's framing (user).** Two asks. **(1) Capital plan.** The real book is now funded with an explicit **€10,000 committed up front, deployed progressively as catalysts fire** — not a vague "invested" number. New `total_capital_eur` in `track_record.yaml` (read via `weights.total_capital_eur()`); `build_site` bakes `total_capital_eur` + **`cash_eur`** (= committed − cost basis of open positions) + `deployed_pct` into `positions`. The Positions summary strip gained a **committed-capital** card (with `% deployed`) and a **cash** card (dry powder · awaiting catalysts) — cash is now a first-class variable on the page. Today: €10k committed / €1.5k invested / **€8.5k cash** / 15% deployed. **(2) Framing.** Replaced the "⚠ entry by design — entry was *deliberately bad*, opened into the selloff, book *starts underwater on purpose*, a test of luck" box with a **"Capital plan — €10,000 committed · long-horizon · catalyst-driven"** card: capital deployed progressively, positions sized to conviction and held while the thesis holds — a long-term thesis-driven book, not short-term trading. (Per the user: the old copy read like gambling; this is long-term investing and the dashboard is meant to show rigor.) 180 tests green. |
| 2026-06-08 | `catalyx/execution/portfolio.py` + `catalyx/store/lake.py` (`portfolio_catalyst_exposure` table) + `catalyx/store/lake_query.py` (`portfolio_catalyst_exposure` + CLI) + `scripts/build_site.py` + `site/{app.js,index.html}` + `tests/unit/test_lake_query.py` | v2.21 | **Lineage reframed again (user) → PORTFOLIO-anchored catalyst exposure OVER TIME.** v2.20's catalyst→strategies cut was the wrong axis. The right question: take a strategy's book (assume **€1000 split across its holdings**), decompose it **by catalyst**, and track how that mix shifts as the book **rebalances every run**. New deterministic decomposition recorded at each `portfolio.py` build: each holding's `weight_pct` is divided **EQUALLY across the catalysts driving its sector** (point-in-time from the studies' `active_catalyst_ids`; sectors with no catalyst → `uncatalyzed`; the water-fill remainder → `cash`) → the % of the book exposed to each catalyst. Persisted to a new lake table **`portfolio_catalyst_exposure`** (portfolio_id, run_id, catalyst_id, pct, eur, notional_eur) — partition (portfolio_id, run_id), one row per catalyst per rebalance. `lake_query.portfolio_catalyst_exposure(pid)` returns `{timeseries[{run_id,date,by_catalyst}], average[{catalyst_id,avg_pct,avg_eur}]}` where the average is **TIME-WEIGHTED** — each rebalance weighted by how long its allocation was live (Δt to the next run, last → now), the 'tiempo activo' rule the user asked for. `build_site` bakes it per portfolio into `overview.json` (zero-WASM first paint). Dashboard: the residual catalyst-dropdown lineage REPLACED by a portfolio-anchored **"Catalyst exposure over time"** that follows the selected strategy — current composition bars (% + € of the €1000), a multi-line exposure-over-time chart (one line per catalyst, `lineChart` auto-scaled via `o.maxY`), and the time-weighted-average table. **Only 1 build exists today** → the chart + avg populate from the next recompute; the composition bars are live now. Verified: catalyx 24.9% ai_capex / momentum 29.8% ai_capex / low_crowding 22.4% NATO. 180 tests green. |
| 2026-06-07 | `catalyx/store/lake_query.py` (`catalyst_lineage` + CLI) + `tests/unit/test_lake_query.py` + `site/{app.js,index.html}` | v2.20 | **Decision lineage re-anchored on the CATALYST + per-strategy exposure over time (was residual).** The dashboard's "Decision lineage" was a vestigial movement→run/reports table dump buried in Portfolios. Reframed it to the unit that actually carries the track record — the **catalyst** — and to answer the question the model strategies pose: since the four books **rebalance every run**, *how does the system's bet on a catalyst accumulate and shift over time?* Pick a catalyst → (1) the real-book movements attributed to it, (2) the sectors it drives (`study.active_catalyst_ids`), (3) **strategy exposure**: each strategy's TOTAL weight in those sectors = its exposure to the catalyst, charted **per run (each rebalance is a point)** with a bold **`combined`** line = mean exposure across the 4 strategies (the system's average conviction), a headline combined-% + Δ-vs-last-rebalance, and a latest-snapshot table with the per-strategy ENTERED/EXITED/±pp move. New Python `catalyst_lineage(catalyst_id)` (parity + skill contract): reads the catalyst→sector map from the Tier-1 studies, then a `GROUP BY portfolio_id, run_id SUM(weight_pct)` over `portfolio_holding` → `{sectors, movements, timeseries[{run_id, combined_pct, by_strategy}], latest}`. CLI `catalyst-lineage <id>`. `lineChart` generalized with an `o.maxY` (exposure is ~0–40%, not 0–100). The infra was already there — the cross-link helpers (`sectorsForCatalyst`/`movementsForCatalyst`) and `docs.json` (study `active_catalyst_ids`) — only the lineage view was thin. **Only 1 portfolio build exists today**, so the curve is a single point + all moves read `held`; ENTERED/EXITED/±pp + the trend line populate from the next recompute (by design). 179 tests green (+1). |
| 2026-06-07 | `catalyx/scorer/entry_timing.py` + `catalyx/config/scoring_weights.yaml` (`entry_timing.trend_deadband_k`) + `weights.py` + `tests/unit/test_entry_timing.py` + `site/{app.js,index.html}` + `.claude/commands/{catalyx-open,catalyx-review,catalyx-heatmap}.md` | v2.19 | **Entry-timing: de-noised the `falling` gate (A′) + renamed the micro-states to TA-standard.** Two changes. **(1) A′ deadband.** The tension gate `(falling AND in_drawdown)` keyed off the RAW SIGN of the 5d return — but at ±2-4% that sign is within ~1 SE of zero (5d-sum SE ≈ σ·√5), a coin-flip that made borderline names flicker state run-to-run (the original symptom: steel vs semis looked near-identical yet split falling↔calm). Now `falling ⟺ short_ret < −k·(σ_daily·√h)·100`: a move inside the vol-scaled band reads not-falling. Deliberately kept the SHORT horizon (responsive to fresh turns / digested gaps) and only banded it — a longer OLS slope was rejected because it LAGS turns by ~half its window (V-bottoms, post-gap bases, news-driven bounces would read "still falling" for days; traced through 4 cases). σ from the LONG vol window = a stable noise floor that doesn't itself widen in a vol spike. `k=0.6` (not 1.0): 1 full SE was too permissive — it flattened steel's −3.8%/5d inside a −6% drawdown into `enter_now`; 0.6 SE kills the genuine ±1-2% flicker while keeping moderate real declines as `falling`. New `trend_deadband_pct` helper (reuses `realized_vol`) + `band_pct` surfaced in the JSON; `classify_state` takes an optional band (default 0.0 = legacy raw-sign, so the pure-function tests are unchanged). **(2) States renamed → TA-standard** (`calm/stabilizing/stretched/falling_unstable` → **`neutral/basing/overbought/falling`**) — two dichotomies: neutral↔overbought (oscillator axis) and basing↔falling (drawdown axis); "calm" mixed register with what it measures. Threaded through `classify_state` returns + the `suggest_verdict` map, dashboard pills/`strong·neutral` chip/`=== 'neutral'` filters, the three skill docs and the YAML verdict-map comment. No lake migration (the `entry_timing` table was never persisted yet). Verified live: steel→`falling`/`wait_stabilize`, ASML→`neutral`/`enter_now`, rare_earth/solar→`falling`. 178 tests green (entry_timing file 21→25, +4 for the deadband). |
| 2026-06-07 | `catalyx/attribution/outcome.py` (new) + `schemas/movement.json` (v1.2, `outcome` block) + `catalyx/store/lake.py` (`movement_outcome` table) + `tests/unit/test_outcome.py` (new) + `.claude/commands/catalyx-close.md` + `scripts/build_site.py` + `site/{index.html,app.js}` (Experiment ledger) + `docs/DESIGN_experiment_ledger.md` (new) | v2.18 | **Experiment ledger — every closed position scored as a registered experiment (rebuilds the deleted `right_reason_score` / `ClosedThesis` on the Movement model).** Reframes backtesting for a discretionary book: not a statistical IC backtest (intractable honestly when the signal is partly LLM judgement — look-ahead can't be rebuilt) but a **decision journal where each trade is an experiment** — hypothesis (opening `attribution`+`score_context`+`risk_discipline`) vs result (the close), dodging look-ahead by recording forward. `outcome.py` computes, network-free from the files: realized P&L gross + **after-tax** (`tax_engine`, with YTD-prior reconstructed from prior closes), the **right-thesis × right-reason VERDICT** (skill=won-for-the-reason / luck=won-despite-wrong-reason / variance=lost-but-reason-held / correct_invalidation=lost-and-reason-failed; `confidence:low` when holding<60d or assumptions mostly unresolved), and **behavioral self-learning flags** (`held_past_full_exit`, `exited_intact_at_loss` = the user's "salí muy pronto / pánico" shape, `discretionary_exit`, `overrode_signal`). Schema 1.2 ADDITIVE `outcome` block holds the human-judged inputs captured at `/catalyx-close` — **`exit_note` (in-the-moment, never overwritten) + append-only `additional_notes[]`** (user-decided: a free message of his own, editable-by-adding so later realizations don't erase the in-the-moment read), `assumption_resolution`, `catalyst_materialized`, `signal_context.followed_signal` — and the computed `pnl`/`behavioral_flags`/`verdict`. `/catalyx-close` rewritten: run `exit_watcher` first (did you follow your own signal?) → capture the experiment → `outcome evaluate --write-back` → reflect on flags. Lake `validation/movement_outcome` (1 row/experiment) → dashboard Positions **"Experiment ledger"** (verdict-coloured, after-tax, flags, exit_note). `outcome report` = aggregate self-learning view (verdict mix, flag frequency, signal-discipline rate, after-tax win rate, exit-note journal). **NO automation (user-decided): review-driven only**; the deterministic signal-snapshot during a holding (the "sold day 12 vs day 30" resolution) is a future GitHub Action, not Claude. 198 tests green (+20). Design: `docs/DESIGN_experiment_ledger.md`. |
| 2026-06-07 | `catalyx/scorer/exit_watcher.py` (new) + `catalyx/config/scoring_weights.yaml` (`exit_signals`) + `weights.py` + `catalyx/store/lake.py` (`exit_signal` table) + `tests/unit/test_exit_watcher.py` (new) + `catalyx/thesis/structural_monitor.py` (within_window fix) + `scripts/build_site.py` + `site/{index.html,app.js}` (dashboard surface) | v2.17 | **Sell-signal layer — `exit_watcher.py` Family 1 built + surfaced on the dashboard (build step 2).** The bridge that READS the `risk_discipline.invalidation[]` stops (authored on every movement but previously unread by any code). For each open position: (a) evaluates each price stop carrying the schema-1.1 structured eval fields DETERMINISTICALLY — fetch `eval_ticker`, count trailing breaching closes, fire only when the breach holds for the full `consecutive_days` window (time-independent stateless read) → `fired`/`approaching`/`clear`; `eval_ticker:null` stops route to a Claude-checks-with-WebSearch list. (b) rolls up `assumptions[].current_status` (`violated`⇒exit input, `weakening`⇒watch). (c) crosses the sector `regime_state` (breaking⇒reduce, contested⇒watch). (d) marks the position + surfaces the AFTER-TAX exit consequence via `tax_engine` (a loss ⇒ harvestable, no CGT + recompra note). **Severity arbitration (§5):** a fired `full_exit` stop ⇒ Exit and overrides everything; else fired-reduce/breaking/violated ⇒ Reduce; approaching/contested/weakening ⇒ Watch; else Hold. **RECOMMEND-ONLY (D6):** writes nothing — not even `triggered=true`. Persists a per-run `exit_signal` lake table for the dashboard. Config in `scoring_weights.yaml` `exit_signals`. **Live verified:** copper→WATCH (asm_02 supply-tightness weakening; price stops clear via HG=F/EURUSD=X; inventory + hyperscaler stops Claude-checked), grid→HOLD. Also fixed a latent time-of-day bug in `structural_monitor.within_window` (a same-day event stamped 09:00Z read as out-of-window when run before 09:00Z — contradicted the module's run-frequency-independent design; added a 1-day future grace). 156 tests green (+14, +1 fixed). Next: dashboard surface (Positions page Exit-watch panel), then `exit_timing.py` (Family 2), then exhaustion/rotation + `profit_take[]`. |
| 2026-06-07 | `docs/DESIGN_sell_signals.md` (new) + `schemas/movement.json` (v1.1) + `data/movements/*` (migrated) | v2.16 | **Sell-signal layer — design + schema groundwork (build step 1).** The platform was asymmetric: the BUY stack is fully deterministic (`composite → dislocation → entry_timing → regime_state`) but exits were hand-judged — the `risk_discipline.invalidation[]` stops authored on every movement were **never read by any code**, and there was no `exit_timing`. New `docs/DESIGN_sell_signals.md` defines the exit side: **4 families** — (1) **invalidation** (read the stops + assumptions + regime cross — the planned `invalidation_watcher`), (2) **exit timing** (mirror `entry_timing`, inverted: overbought→`sell_into_strength`, knife→`hold_dont_panic_sell`), (3) **exhaustion** (momentum-percentile + crowding + conviction-tier drift + spent event catalyst), (4) **rotation** (rank drop + uncorrelated `dislocation` diversifier → trim-to-fund pairs) — plus a **tax** dimension the buy side lacks (after-tax P&L via `tax_engine`, Spanish 2-month recompra rule). Doctrine: asymmetric STANCE (a pre-committed `full_exit` stop is loudest) but — user-decided — **still recommend-only, never auto-writes** (D6); tax **soft-reorders + flags** rotation, never suppresses (D5); severity arbitration (§5) lets the most pre-committed/fundamental trigger bind. **This commit = build step 1:** ADDITIVE structured eval fields on `invalidation[]` (`comparator`/`threshold`/`consecutive_days`/`eval_ticker`/`eval_note`) so `exit_watcher` can evaluate price stops DETERMINISTICALLY instead of parsing free-text — `condition` stays human, the new pair is machine-checkable (threshold in eval_ticker's units; null eval_ticker ⇒ Claude-checks-with-WebSearch). Migrated the 2 open movements: copper inv_01 LME→`HG=F` COMEX proxy (threshold 4.99 = $11k/t ÷ 2204.62, basis-approx flagged), inv_03 LME-inventory→Claude-checked (no feed + 4-week-rolling not consecutive-day), inv_04 EUR/USD→`EURUSD=X` clean; grid inv_04 IQQH.DE clean. Next: `exit_watcher.py` Family 1, then `exit_timing.py`, then exhaustion/rotation + `profit_take[]`. |
| 2026-06-06 | `catalyx/scorer/entry_timing.py` + `catalyx/config/scoring_weights.yaml` (`entry_timing` warm band) + `site/app.js` + `tests/unit/test_entry_timing.py` | v2.15 | **Entry-timing `stretched` no longer needs BOTH hard lines — fixes "extended" reading as "calm".** The user spotted `cybersecurity_commercial` (ISPY.L) flagged **`strong · calm` → enter_now** on the dashboard while it was actually RSI 68.9 / +7.75% vs MA20 / vol× 1.31 — sitting JUST under EVERY hard threshold at once, into the 2026-06-05 risk-off tape (VIX +40%, S&P worst day in a year). Root cause: `classify_state` required `overbought AND extended` (RSI≥70 AND stretch≥8%) for `stretched`; a name a hair below both fell through to `calm`. Fix (option 1, the real one): `stretched` now fires on EITHER hard line **OR** when ≥ `borderline_min_axes` (=2) of the softer "warm" axes trip together (`rsi_warm` 65 / `stretch_warm_pct` 6.0 / `vol_ratio_warm` 1.2) — borderline-overbought AND borderline-extended AND vol-rising simultaneously IS chasing. A SINGLE warm axis (e.g. only vol elevated in a selloff) does NOT qualify → a knife still routes to falling/stabilizing, never `stretched`. ISPY.L now → `stretched`/`wait_stabilize`; universe-wide only 2 sectors flip (cyber + genomics — the two that ran UP against the tape), 11 stay calm / 30 falling_unstable, so it's surgical, not over-flagging. Plus a light display-honesty pass (option 3): the `strong · calm` chip tooltip now substantiates the claim with the raw RSI/stretch/vol numbers + the macro backdrop (VIX / S&P 5d) instead of a bald "clean buy-ready entry" — the verdict is a suggestion, not a vetted call. **Deliberately did NOT fold the macro backdrop INTO the verdict (option 2):** the module's design is "Python surfaces facts, Claude judges"; a mechanical "VIX up → scale_in" rule is crude market-timing that fights that stance — the backdrop stays a surfaced fact. 140 tests green (+4). |
| 2026-06-06 | `catalyx/data/flow_data.py` + `catalyx/store/snapshot_repo.py` + `schemas/sector_snapshot.json` (v1.3) + `scripts/build_site.py` + `site/app.js` + `tests/unit/test_flow_data.py` (new) | v2.14 | **Flow coverage: per-sector fallback chains (all ~49 sectors) + carry-forward resilience.** Extends v2.13 after the user saw most cells still at 50 (it was a Saturday — market closed → yfinance serves no `sharesOutstanding`). Two resilience layers so the pipeline never silently parks a sector at neutral 50: (a) **`SECTOR_FLOW_TICKERS`** — the single source of truth, now an ORDERED FALLBACK CHAIN per sector (`[tradeable_primary, us_fallback1, us_fallback2]`); `_resolve_flow_signal` walks it and uses the first ticker that yields a computable delta (else the first with direct shares as a baseline for next run). US-listed fallbacks are preferred because yfinance exposes their shares (UCITS rarely). Coverage went 17 → ~49 investable sectors; adding one is a single documented line (region-specific caveats noted inline — e.g. EU banks use EUFN, not a US bank ETF). (b) **Carry-forward** (`_carry_forward_flow`, ≤7-day window): when a run can't compute fresh (closed market / fetch fail), reuse the last genuine reading marked `carried` (+`flow_carried_from`) instead of 50 — correct because a closed market has no new flow. data_quality ∈ {computed, proxy_computed, **carried**, estimated}; the prior-lookup skips derived/weekend rows to reach the last DIRECT reading. Dashboard marks each flow cell: ᴾ proxy / ↻ carried / ~ no-reading, all with tooltips + a detail note, and the marker now also flags uncovered/None as ~. 136 tests green. _(superseded the same-day v2.13 row — same feature, completed.)_<br>**v2.13 (folded in):** same-theme proxy for UCITS vehicles + a basis-integrity gate (kills phantom inflows). Two problems behind the wall of neutral-50 `flow_confirmation`. (1) **UCITS vehicles expose no `sharesOutstanding` via yfinance** → creation/redemption invisible → flow stuck at 50, silently hiding inflows/outflows (and the opportunities they signal). Fix: `FLOW_PROXY` decouples the **flow-signal ticker** from the **execution vehicle** — for GLOBAL/FUNGIBLE themes the signal is read from the most liquid same-theme US sibling (`gold_physical→GLD`, `silver_physical→SLV`, `semiconductors_design→SOXX`); valid because the structural flow into the THEME is vehicle-agnostic (gold is gold). Region-specific themes are deliberately NOT proxied (a US defense ETF measures a different investor base). Execution stays the tier-1 UCITS in `etf_universe.yaml`; only the number borrows the sibling, with full provenance recorded. (2) **Basis-integrity bug:** when yfinance dropped `sharesOutstanding` the old code derived shares = `totalAssets/nav`; comparing a derived count to a prior DIRECT count re-injects price — a price drop INFLATED derived shares → a phantom "+8% inflow" exactly during a selloff (COPX 06-06: nav −7.6%, fake +8.2% inflow). That is the precise AUM-vs-flow confound CLAUDE.md forbids. Fix: a flow delta is computed ONLY on a consistent DIRECT `sharesOutstanding` basis on BOTH dates (`basis_ok` gate); a derived/mixed basis yields NO signal (neutral 50), never an inverted one. So `data_quality ∈ {computed, proxy_computed, estimated}` (the `*_aum` states are gone — derived is never trusted for flow). Prior lookup hardened: strictly-before-today (same-day re-runs reuse yesterday, not self), matches the proxy ticker against either `ticker`/`flow_proxy_ticker`, **and skips `derived_from_total_assets` rows so it reaches back to the last DIRECT reading** (e.g. a Monday delta uses Friday's clean shares, not a stale weekend row). Provenance (`flow_data_quality` / `flow_proxy_ticker` / `flow_proxy_used`) threads flow_data → lake `flow` → `sector_snapshot` (schema 1.3, additive) → `overview.json` → dashboard: the Sectors table flags flow with <sup>ᴾ</sup> (proxy) / <sup>~</sup> (no reading) + a detail note, so a 50 is never mistaken for a real neutral. **Root-caused a scary symptom:** the day this shipped (2026-06-06 = **Saturday, market closed**) every cell read 50 — because yfinance only serves `sharesOutstanding` for many US ETFs (COPX/GDX/NLR) during/around market hours; on the weekend it returns only stale `totalAssets`, which the gate correctly refuses. The weekday runs (Thu 06-04 / Fri 06-05) DID compute real flow. So this is a market-calendar data-availability effect, not a regression: real values return on a weekday run, and gold/silver/semis compute via proxy once a second snapshot exists (Saturday wrote GLD/SLV/SOXX direct-share baselines). Open follow-ups: (a) a reliable shares source (iShares/issuer API — the `_fetch_ishares` stub) to remove the market-hours dependence; (b) the totalAssets-only US ETFs have no clean flow source while closed. 133 tests green (+8). |
| 2026-06-06 | `catalyx/execution/portfolio.py` + `catalyx/config/scoring_weights.yaml` (`portfolio_weighting`) + `weights.py` + `catalyx/config/portfolios/catalyx.yaml` + `catalyx/execution/nav_engine.py` | v2.12 | **Conviction sizing (softmax) — the weights now express the ranking + persist fix.** Problem: composite-PROPORTIONAL weighting produced near-identical weights, because the top-10 composites sit in a narrow high band (74.0→65.4, ratio 1.13 → weights 10.7%→9.4%). The "brutal" ranking was thrown away by the sizing — we did the analysis then didn't trust it. Fix: SEPARATE the selection/ranking signal (still `weighting` per profile) from the magnitude TRANSFORM (new). New `portfolio_weighting` section (single source of truth) → `transform` (proportional\|softmax), `sharpness`, `rebalance_deadband_pct`; `weights.portfolio_weighting()` accessor; a profile's `construction` overrides per book. `conviction_transform()` = **softmax over the z-NORMALIZED score** (`w ∝ exp(sharpness·z)`): z-norm makes `sharpness` mean "std-devs of tilt" so dispersion keeps its meaning even as the band compresses next run (a raw-score softmax would drift) — monotonic, so it never reorders the ranking, only magnitudes; std≈0 → equal. `apply_deadband()` keeps a weight within N pts of what's already HELD (prev run) → a turnover/CGT guard against tax-churn from tiny score wiggles. Both wired into `build_model_holdings`. Default transform stays `proportional` (momentum/low_crowding/equal unchanged); the flagship **`catalyx` opts into softmax → now disperses 15.3%→6.7% (≈2.3x)**, `equal_weight` stays flat (control). Also fixed a `portfolio_nav` persist bug: `_persist_nav_rows` wrote ALL portfolios into one portfolio's `{portfolio_id}` partition → rows duplicated on read (catalyx hit 52 copies); now writes only that portfolio's slice, mode-scoped so backtest/live/real coexist without clobbering. |
| 2026-06-06 | `site/{index.html,app.js}` + `scripts/build_site.py` + `catalyx/scorer/dislocation.py` + `catalyx/execution/nav_engine.py` (`compute_live_nav`) + `catalyx/config/track_record.yaml` + `catalyx/store/lake.py` + `data/movements/*` + `.claude/commands/catalyx-review.md` | v2.11 | **Dashboard: dedicated Timing + Positions pages, live track record, portfolio rotation.** (1) **Entry-timing on the dashboard:** persisted `entry_timing` lake table (per run, baked as a by-sector map) → a dedicated **Timing page** (sortable: composite/state/verdict/RSI/vol/stretch/5d/drawdown/overhang) + inline timing in Overview opportunity tickets and the sector detail. **Opportunity now requires a composite floor (≥55)** — a dip is only an opportunity if we'd own the sector on the full blend (fixes flagging high-catalyst/low-composite sectors). The Timing table ALSO flags **`strong · calm`** (composite ≥66 + calm timing = clean buy-ready entry), ordered dips→strong→rest by composite. (2) **Positions page** (the real book, split out from the model strategies): summary (invested/value/vs-SPY/vol/Sharpe), NAV vs SPY, holdings, a **movements ledger that REFERENCES catalysts (chips) — no duplicated catalyst detail**, catalyst exposure, and **rotation targets** = `dislocation --anchor-sectors <held>` (diversifiers least-correlated to YOUR holdings → new `portfolio_rotation` lake table). Removed the duplicate Positions sub-tab from Catalysts. Copper vehicle ticker `4COP`→`4COP.DE` (yfinance-resolvable Xetra/EUR) so the real NAV prices. (3) **Live track record wired:** `nav_engine.compute_live_nav` (walk-forward; chains each run's ACTUAL holdings from `track_record.yaml` inception, no look-ahead) is the headline (`mode='live'`); the trailing backtest is demoted to a reference shown only while *accruing*. Inception anchored to the first real position (Fri **2026-06-05**) so model + real compare from the same day vs SPY; Portfolios tab labeled a **theoretical exercise** (no prices/fees/taxes, rebalances to the recommendation each run). 125 tests green. |
| 2026-06-06 | `catalyx/scorer/entry_timing.py` (new) + `catalyx/config/scoring_weights.yaml` + `weights.py` + `tests/unit/test_entry_timing.py` (new) + `.claude/commands/{catalyx-open,catalyx-review,catalyx-heatmap}.md` | v2.10 | **Entry-timing overlay — the micro execution window (recommend-only).** New question the system didn't answer: the composite says WHICH sector, `dislocation` says IF it is cheap, but neither said WHEN to enter a position already decided. Entering into the 2026-06-05 correction (€1000+€500 movements) motivated it: fundamentals intact (scores high) yet a falling tape = poor *timing*. `entry_timing.py` computes, from yfinance (no LLM drift): **micro-tension** — RSI14, stretch-vs-MA20, realized-vol regime (10d/90d), 5d trend, drawdown-from-20d-high, and a **stabilization** check (the discriminator between a good dip and a falling knife) → `micro_timing_state` ∈ {calm, stretched, falling_unstable, stabilizing} + a `tension_score`, with a ^VIX/SPY market backdrop. Second facet: **event overhang** — a near-term discrete `CatalystEvent` touching the sector (resolved exactly like `catalyst_scorer`: listed in the study's `active_catalyst_ids` or linked via `related_catalyst_ids`), within `overhang_window_days`. Per the user, an overhang **is a catalyst, not a separate flow** — the SpaceX mega-IPO is registered as a normal CatalystEvent with a future `event_date`; NO `data/event_calendar/` registry. Emits a `suggested_verdict` (enter_now/scale_in/wait_stabilize/wait_event); Python surfaces facts, the adverse-vs-bullish overhang read + final call are Claude's (same stance as dislocation/regime — recommends, never trades, never moves the composite, no persistence yet). Thresholds in `scoring_weights.yaml` `entry_timing` (tunable, single source of truth). Wired into `/catalyx-open` (Step 5.5 gate before writing the movement), `/catalyx-review` Step 5c + output table, `/catalyx-heatmap` step 12c. Verified live: `copper_miners`/`grid` = `falling_unstable` → `wait_stabilize` (the correction, intact fundamentals); grid surfaces 2 real overhangs. 125 tests green (+20). |
| 2026-06-06 | `schemas/movement.json` (new) + `catalyx/store/movement_repo.py` (new) + `data/movements/*` (new) + `nav_engine.py` + `lake_query.py` + `lake.py` + `.claude/commands/{catalyx-open,catalyx-close,catalyx-review}.md` + `site/*` + `scripts/build_site.py` + `cli/main.py` + tests + **deletions** (`thesis_repo.py`, `thesis_scorer.py`, `trade_logger.py`, `schemas/thesis.json`, `schemas/closed_thesis.json`, `data/theses/`, `catalyx-thesis.md`) | v0.3.1 | **Thesis → Movement restructure (full, no legacy).** The primary capital unit is no longer a heavyweight falsifiable `Thesis`; it is a **`Movement`** — €X attributed directly to catalyst(s) via weighted `attribution[]`, with `action` (open/add/trim/close), `trigger`, `conviction`, and a point-in-time `score_context`. The **Catalyst** becomes the unit of the track record (`catalyst_ledger` = P&L by catalyst). Movements are Tier-1 JSON files in `data/movements/` (drop a file → run `movement_repo ingest`; the ingest joins `score_context` to the score_run as-of `executed_at`, **no look-ahead**, and write-throughs a `movement` mirror + `catalyst_performance` to the lake). The falsifiable discipline survives as an **optional, machine-checkable `risk_discipline`** block on the movement (assumptions + invalidation/stops — the chosen "option 1"). **Skills restructured**: operating is now `/catalyx-open` + `/catalyx-close` (independent, anytime); `/catalyx-thesis` deleted; `/catalyx-monthly-review` → **`/catalyx-review`** (parametrized `scheduled` \| `event:<catalyst_id>` — reviews are no longer monthly-only). The 2 open theses (copper €1000, grid €500, bought on the dip 2026-06-04, full positions, no rebalance) migrated to movements. `nav_engine` real book ← `movement_repo.positions`; `lake_query` lineage walks movement→catalysts→run; dashboard "Catalysts & theses" → "Catalysts & positions". SQLite-era trade log + the empty `portfolio_trade` table dropped. 105 tests green. Plan: `docs/PLAN_movement_restructure.md`. |
| 2026-06-06 | `catalyx/config/scoring_weights.yaml` + `weights.py` + `catalyx/scorer/sector_scorer.py` + `catalyx/store/snapshot_repo.py` + `schemas/sector_snapshot.json` (v1.2) + `scripts/build_site.py` + `site/app.js` + `tests/unit/test_portfolio.py` + `experiments/backtest_acceleration.py` (new) | v2.9 | **`valuation_relative` removed from the composite (schema 1.2).** It had always been a constant-50 placeholder (no `valuation_engine`), so it never changed the *ranking* (a constant × fixed weight shifts every composite equally) — it only diluted the real dimensions toward 50. Before removing, tested whether ANY price-derived metric earns that 15%: a walk-forward, no-look-ahead backtest of **momentum acceleration** (2nd derivative: `r3m×4 − r6m×2`) over 48 monthly rebalances / 43 sectors (`experiments/backtest_acceleration.py`). Result: acceleration is orthogonal to momentum-level (corr +0.28) but has **NEGATIVE** monthly IC (−0.054, top quintile *under*performs −0.39%) — short-term reversal dominates; the blend *hurt* pure momentum. **Verdict: no price-derived 4th dimension earns the weight.** So `valuation_relative`'s 0.15 was redistributed **proportionally** (each survivor × 1/0.85) → catalyst **0.35** / momentum **0.29** / flow **0.24** / crowding **0.12** (relative importances unchanged). Composite formula + schema description updated; field marked `deprecated` (nullable) in schema 1.2 for one-major-version read-back of pre-1.2 snapshots; dropped from the lake write-path + dashboard queries (dashboard already hid the column). New `sector_snapshot` partitions omit the column (old read back via `union_by_name`). `valuation_engine` moved from "planned" to **DROPPED** in the roadmap. |
| 2026-06-06 | `scripts/build_site.py` + `site/app.js` + `site/index.html` | v2.8.5 | **Cache-busting + sectors-table legibility.** (a) **Cache-bust:** `build_site` injects a per-build token → `index.html` sets `window.__BUILD__` and loads `app.js?v=TOKEN`; `app.js` appends it to `overview.json`/`docs.json`/`manifest.json` + the DuckDB-WASM parquet URLs. Fixes the class of bug where Pages served a fresh `index.html` with a browser-cached old `app.js` (DOM-contract mismatch → Sectors/Catalysts blanked with `null.innerHTML`). Also busts rewritten same-name parquet (e.g. backfilled `score_run`). (b) **Sectors table → heatmap** for legibility: score cells are colour-tinted (green/amber/red) numbers instead of look-alike mini-bars; **crowding is now a categorical label** (low/medium/high — it only takes 3 values, deriving from `narrative_maturity`); **`valuation_relative` column removed** — it is a hardcoded 50 placeholder (no `valuation_engine` yet) so a column of identical 50s was pure noise (kept in data + detail with a note). `flow_confirmation` retained (it does vary, 27–68). 104 tests green. |
| 2026-06-06 | `site/app.js` + `site/index.html` + `scripts/build_site.py` | v2.8.3 | **Dashboard UX pass (feedback).** (1) **Sectors** is now a full **comparison table** — every score dimension side by side (composite, catalyst, momentum, **flow**, **valuation**, crowding) with colored mini-bars, **sortable** column headers, click-row→detail; replaces the narrow master-detail list (user: "ver todas las variables para comparar"). Added flow_confirmation/valuation_relative to the baked + dynamic ranking queries. (2) **Sector score history** redesigned as an **axed multi-line chart** (0–100 gridlines + y labels + x date ticks + legend) showing composite/catalyst/momentum/**crowding**; dropped the per-run table (user: "con la gráfica sirve, pon crowding y ejes"). (3) **Catalysts** section now has **sub-tabs (Structural / Event / Theses)**, all in the same rich master-detail card format (event → Signal chips + related catalysts + driven sectors; thesis → catalyst/sector rationale + vehicle + entry + assumptions/invalidation). (4) Fixed **`[object Object]`** in study fields: object-valued fields (`cycle_position`, `technology_maturity`) render their `assessment` text via a new `fmtMeta` helper (never `String(obj)`). Run dropdown already replaced by the sidebar card + Data timeline (v2.8.1). 104 tests green. |
| 2026-06-06 | `catalyx/store/snapshot_repo.py` + `scripts/build_site.py` + `site/app.js` + `score_run` lake partitions (backfilled) | v2.8.2 | **Pipeline-authored per-run change summary.** `record_run` now computes a deterministic `summary` digest at run time and stores it as a JSON column on **`score_run`** (schema-on-read; old partitions read back null via `union_by_name`). The digest captures WHAT changed vs the previous run: biggest rank movers (▲/▼), top-N entries/exits, **new event catalysts detected in the run's time window**, **regime stress** (contested/breaking counts), and **composite breadth** (sectors up/down + mean Δ — a market-direction proxy). New helpers `_run_summary` + `_new_catalysts_in_window`; one-off `snapshot_repo backfill-summaries` recomputes it for all existing runs from the lake (ran for the 5 current runs). `build_site` ships the stored summary verbatim (falls back to a build-time compute only if a run lacks one); the dashboard renders it in the Overview ("What changed this run") and the Data run-timeline. This is the pipeline half of the v2.8.1 run-navigation redesign — the summary is now generated where the run is created, not by the dashboard. 104 tests green. |
| 2026-06-06 | `site/app.js` + `site/index.html` + `scripts/build_site.py` | v2.8.1 | **Dashboard hotfix (blank page) + run-navigation redesign.** Root-caused the "nothing precomputed / can't pick a run" report: `app.js` did a **static top-level `import` of duckdb-wasm (~MBs)** — if that CDN module is slow/unreachable the whole module fails to execute, blanking the precomputed first paint that was supposed to need **zero** WASM. Fix: duckdb-wasm and `marked` are now **dynamic `import()`** (duckdb only inside `ensureDuckDB`; `marked` best-effort with an escaped-text fallback). **RULE: never static-import a heavy/CDN module at the top of `app.js` — it couples the first paint to that download.** Verified by rendering with `cdn.jsdelivr.net` DNS-blocked → overview + runs timeline still render. **Run navigation redesigned** (the dropdown "doesn't scale"): sidebar now shows a compact current-run card (date · latest/historical · notes · "Browse all runs →"); the **Data section is the run timeline** — each run card shows a build-time **digest of what changed vs the previous run** (`build_site` now bakes per-run `summary`: top rank movers ▲/▼, top-10 entries/exits, and **new event catalysts detected in that run's window** — e.g. `cat_20260605_ai_capex_peak_scare`). 104 tests green. |
| 2026-06-06 | `site/index.html` + `site/app.js` + `scripts/build_site.py` + `catalyx/config/portfolios/{conviction.yaml→catalyx.yaml}` + `schemas/portfolio.json` (v1.1) + `tests/unit/test_portfolio.py` + lake migration (`portfolio_nav`/`portfolio_holding` partitions `conviction`→`catalyx`) | v2.8 | **Dashboard full refactor (entity-centric, run-aware) + portfolio rename.** Replaced the 10 flat tabs with a **sidebar IA of 4 sections + Data** (Overview / Sectors / Catalysts & theses / Portfolios), hash-routed (`#/section/id`, shareable deep-links). **Sector view unifies** ranking + study + history and cross-links to its catalysts/thesis/holding-portfolios (links derived from `study.active_catalyst_ids`, `thesis.sector`, `latest_holdings`); **theses now surfaced** (were in no tab). **Precompute-vs-lazy re-architected for scale:** `build_site._bake_overview` bakes only the LATEST run + prev-run ranks + `latest_holdings` + portfolio NAV/risk-metrics/config into a **bounded ~32KB `overview.json`** (first paint needs **zero WASM**); any **historical run loads on demand** from the lake (DuckDB-WASM reads just the `run_id` partition, cached) via a **global "Viewing run" switcher** that re-renders ranking/sectors/holdings. Overview shows **rank-movement deltas** (▲/▼/NEW vs previous run, computed from baked rankings — independent of `rank_event`), alerts now label **catalyst-alignment** + sector standing (rank/composite). Portfolios show **volatility / Sharpe / max-drawdown vs SPY** + a "how weights are built" methodology panel (from config `construction`); holdings render comp/mom as colored bars. **Renamed portfolio `conviction`→`catalyx`** (the flagship composite book): config + schema enum (v1.1) + lake parquet partitions migrated (column + filename) + test. SQL console dropped. 104 tests green. Dashboard still deploys from `main` via `.github/workflows/pages.yml`. |
| 2026-06-06 | `catalyx/thesis/structural_monitor.py` (new) + `catalyx/scorer/catalyst_scorer.py` + `catalyx/store/snapshot_repo.py` + `catalyx/execution/portfolio.py` + `config/structural_catalysts/japan_carry_unwind.yaml` (new) + `experiments/` (new) + `docs/DESIGN_catalyst_regime_discrimination.md` (new) + `data/catalysts/cat_20260605_ai_capex_peak_scare.json` (new) + `README.md` | v2.7 | **Pipeline resilience experiment + noise-vs-regime state signal (flag-only) + Japan watch catalyst.** Stress-tested the pipeline vs the 2026-06-05 AI selloff (Broadcom AI-capex miss; S&P −2.64%) with a `contradicts` catalyst on `struct_ai_capex_supercycle` (`experiments/exp_2026-06-05_ai_selloff.md`): scoring core **stable**, but momentum strategy **blind** to contradicts, noisy-OR **absorbs** them, momentum snapshot **78% stale** on the day; all 4 strategies −2.8pts vs SPY (illusory diversification). Built discrimination: `structural_monitor` (fundamentals gate) + `regime_state` (intact/contested/breaking) from `catalyst_scorer`, persisted in `sector_snapshot` (additive — no change to `catalyst_alignment`/composite/`scoring_version`). Selloff classifies **`contested` (7 pure-plays), 0 `breaking`** = noise by construction. **A/B verdict:** acting on `contested` (haircut) barely helps drawdown (+0.19/+1.16) and costs edge (−1.47/−6.96) → portfolio overlay defaults to **flag-only** (haircut/exclude are opt-in via `risk_overlay:` in the profile YAML). Converged design: *system recommends, doesn't trade; reacts to persistence, not the event; rotates to uncorrelated.* Added `struct_japan_carry_unwind` — **watch-only** systemic-risk monitor (BoJ/JGB/carry/CPI indicators), unlinked to sectors. **Layer 2 (persistence) built — TIME-INDEPENDENT + Claude-judged:** escalation reads event timestamps over a calendar window (stateless render — same verdict whether run daily/weekly/monthly, not a run counter); Python labels only OBJECTIVE states (`breaking` ⟸ measured fundamental degradation, `contested` ⟸ ≥1 live contradict) and **never auto-escalates off an event count** — it emits a contextual dossier (`persistence_evidence`: distinct developments, span, clustered-one-shock vs dispersed, `review_recommended`) for Claude to make the call ("two consecutive-day drops confirm nothing"). **Dislocation engine built** (`catalyx/scorer/dislocation.py`): one corr/beta engine over yfinance, two lenses — **opportunity** (panic dip: fell hard + `intact` + catalyst-confirmed + contagion-explained, low idiosyncratic residual) and **diversifier** (Layer 3: healthy + LOW correlation to the stressed cluster). Verified on the selloff: `ai_infrastructure` = cleanest opportunity (97% contagion, intact, catalyst 96.7); `semiconductors_memory` correctly EXCLUDED (contested — the miss touches its own thesis); `solar_energy` flagged red (mostly idiosyncratic). Python computes facts, Claude judges. **Wired to the skill + dashboard:** heatmap step 12 / monthly-review step 5c run regime+dislocation (recommendations, never auto-trades); `dislocation` persists a lake table → new **Opportunities** tab on the GitHub-Pages dashboard (opportunities + diversifiers + regime watch). 104 tests green. |
| 2026-06-05 | `catalyx/store/{db.py removed, __init__.py, *_repo.py, snapshot_repo.py, lake.py}` + `pyproject.toml` + `.gitignore` + `cli/main.py` + docs (CLAUDE/README/PLAN/CHANGELOG) + all `.claude/commands/*.md` | v2.6 | **SQLite removed entirely + roadmap reframed to skill-permanent.** Decision (user): CATALYX stays a **skill on the Claude Code session** (credits + WebSearch) — no self-hosted LLM/API, no Postgres. SQLite was never a source of truth (files = Tier 1, lake = Tier 2) and its only own table `llm_log` was an empty Phase-1 placeholder, now obsolete → **deleted `db.py`/SQLAlchemy**. The 4 Tier-1 `*_repo.py` became **file-backed readers** (`summary`/`get`/`set-status`/`tax-snapshot`/`stale` read the JSON/YAML directly; writing a file IS the registration — no import/sync/rebuild/init). `snapshot_repo` repointed its last 3 SQL uses (prev-run lookup, register-report, validate) to the lake; dropped `rebuild`/`export`/cache models. Deps pruned (sqlalchemy, alembic, datasette, typer, pydantic, anthropic/openai extra). Storage is now **two tiers, no DB**. Skills updated (removed Step-0 "rebuild DB" + all import/sync calls). 82 tests green. |
| 2026-06-05 | `catalyx/execution/portfolio.py` + `nav_engine.py` + `config/portfolios/*` (4 strategies) + `site/*` (redesign) + `catalyx-monthly-review.md` (Step 5b) | v2.5 | **Portfolio strategies + market comparison + dashboard redesign.** Portfolios are now 4 distinct **strategies** (momentum/conviction/equal/low_crowding) — replaces the 3 risk profiles that produced near-identical weights; each holding records `entry_price`. `nav_engine` gained `--backtest-days` (trailing backtest of current holdings vs **SPY**) → all 4 beat the market over 180d (momentum +41.9% vs SPY +11.4%). Fixed `holdings_nav` so newly-listed ETFs (no window history) are held as cash instead of poisoning the whole series via row-wise dropna. **Dashboard v3:** light/clean theme (was dark), cards + progress bars + sparklines (catalysts show indicator score-bars + history sparklines; portfolios show NAV-vs-SPY sparkline + "batimos mercado"), studies as structured docs (no raw JSON), event-catalyst summary fixed (was reading the wrong field → now `description`). Consolidated the duplicate dev run. Monthly-review Step 5b builds portfolios + NAV. 82 tests green. |
| 2026-06-05 | `site/index.html` + `site/app.js` (new) + `scripts/build_site.py` (new) + `.github/workflows/pages.yml` (new) | v2.4 | **Fase F — DuckDB-WASM dashboard, LIVE on GitHub Pages.** Static site reads the committed parquet lake in-browser (no backend): ranking, sector history, model portfolios, rank moves, lineage, SQL console. `build_site.py` bakes parquet + manifest into `dist/`; Actions deploys to **https://abetatos.github.io/Catalyx/** on push. Replaced the prior Evidence.dev `dashboard/` (removed `deploy-dashboard.yml` — both were deploying to the same Pages URL). Fixes during bring-up: tz-safe `substr(snapshot_at::VARCHAR,1,10)` (lake mixes tz-aware/naive timestamps → `CAST … AS DATE` fails in DuckDB), `portfolio_nav` guard (graceful when no NAV yet), and inlined SQL literals instead of DuckDB-WASM prepared statements (bind path was breaking the parameterised tabs). Committed scoped to self-contained files; tree WIP untouched. |
| 2026-06-05 | `catalyx/store/lake_query.py` (new) + `snapshot_repo.py` (reads → lake) | v2.3 | **Fase E — unified DuckDB read-path.** `lake_query`: read-only analytical queries over the lake (the page's data layer; DuckDB-WASM will run the same SQL in-browser) — `sector_history`, `latest_ranking`, `rank_moves`, `portfolio_compare`, `portfolio_holdings`, `lineage_for_trade` (trade → run → reports + snapshot), ad-hoc `sql`. Defensive: empty table → empty result. `snapshot_repo.history/list_runs/rank_events` repointed from SQLite to the lake (parquet-first reads complete; SQLite now only a cache + external-tool surface). Verified on the real lake (ranking, sector history, portfolio aggregates). 5 new tests, 82 total green. |
