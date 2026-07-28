# CATALYX Changelog

> Full version history. **Do not read this file every session** — the `Recent Changes` table in `CLAUDE.md` covers the last 5 entries and is always in context.
> Read this file only when you need to answer: "when did X change?", "what was the previous formula?", "why was field Y added?"
>
> **How to add an entry:** when `Recent Changes` in CLAUDE.md reaches 6 entries, move the oldest row here verbatim and add detail below it.
>
> **Versioning (since v0.3.1):** SemVer, **pre-1.0** (early/unstable), one number in `pyproject.toml`, tagged `vX.Y.Z` on `main`. See `RELEASING.md`. The earlier `vN.M` labels below are an informal pre-tag change counter, not SemVer.

---

## v0.5.1 — 2026-06-12 — Scan as macro front door + scheduled review run

**Patch release.** Backward-compatible: a skill-doc refactor plus the 2026-06-12 scheduled pipeline
run committed (data + lake), no schema or contract change.

- **`catalyx-scan` reframed as the "macro front door."** Added **Step C0 — Macro & Big-Economy
  Context** (generic Fed/CPI/DXY + Trump / US administration / Europe / China framings, each its own
  query — broad framings surface more ideas) and turned **Pass 2** into **Classification + Refresh**:
  it now also refreshes the state of every already-registered catalyst (strengthen / weaken /
  invalidation Δ), not just registers new events. `/catalyx-review` (scheduled) now runs the scan
  FIRST and **consumes its output** instead of repeating the macro searches; `event:<id>` mode does
  a lightweight single-catalyst refresh. Threaded through `CLAUDE.md` (pipeline order, skill table,
  review checklist) + `catalyx-review.md` (Steps 0/1 merged).
- **Scheduled review 2026-06-12 committed** (the run itself): 7 stale sector studies refreshed
  (copper, gold_physical, gold_miners, grid, ai_infrastructure, semiconductors_memory,
  eu_defense_prime_contractors), all 9 structural intensities recomputed from indicators and written
  back, run `run_20260612_151007` recorded to the lake (sector_snapshot, rank_event, momentum/flow
  snapshots, dislocation/entry_timing/exit_signal, 4 model portfolios + NAV, real-book NAV), and the
  heatmap + consolidated review reports registered. Macro backdrop: Iran/Hormuz energy shock (CPI
  4.2%), gold −25% from its ATH (CB buying intact), AI-capex digestion; space supercycle took the
  top of the ranking. Real book +0.92% vs SPY −2.55% over the 5d window.

## v0.5.0 — 2026-06-08 — Sell signals, Decision Journal, technical study & catalyst lineage

**Feature release.** The exit side of the platform, a forward-recorded experiment ledger, a deep
pre-open TA dossier, and a reframing of the dashboard around catalysts — all on top of the v0.4.0
Movement model; recommend-only, nothing trades.

- **Sell-signal layer — `exit_watcher` Family 1.** Reads each open position's pre-committed
  `risk_discipline.invalidation[]` stops DETERMINISTICALLY (schema-1.1 structured eval fields:
  `comparator`/`threshold`/`consecutive_days`/`eval_ticker`, fires only after the breach holds the
  full window; `eval_ticker:null` ⇒ Claude-checks-with-WebSearch), rolls up assumptions, crosses
  sector `regime_state`, and marks the after-tax exit P&L → Exit/Reduce/Watch/Hold. Persists an
  `exit_signal` lake table + Positions-page panel. Design in `docs/DESIGN_sell_signals.md`.
- **Experiment ledger / Decision Journal** (`catalyx/attribution/outcome.py`): every closed
  position scored as a registered experiment — realized + after-tax P&L, the right-thesis ×
  right-reason verdict (skill/luck/variance/correct_invalidation), and behavioral flags (sold too
  early, held past stop, overrode signal). Schema 1.2 additive `outcome` block; lake
  `movement_outcome`; dashboard "Decision Journal" page.
- **Entry-timing**: de-noised the `falling` gate (vol-deadbanded so a sub-noise 5d move reads
  neutral) and renamed the micro-states to TA-standard (neutral/basing/overbought/falling).
- **Decision lineage re-anchored on the CATALYST, then on the PORTFOLIO**: each book's notional
  split BY CATALYST per rebalance + a time-weighted average → `portfolio_catalyst_exposure` lake
  table + dashboard "Catalyst exposure over time".
- **Positions: committed-capital + cash model** — €10,000 committed up front, deployed
  progressively as catalysts fire; cash = committed − cost basis. Long-horizon framing.
- **Deep technical study** (`catalyx/scorer/technical_study.py`, v2.23): opt-in pre-open TA dossier
  (MA structure, MACD, Bollinger, ATR, support/resistance, volume/OBV, 52w range → posture),
  offered at `/catalyx-open`. Recommend-only, ephemeral.
- **Dashboard / Positions fixes:** the "Performance vs S&P 500" comparison table moved from
  Positions to Portfolios; the Positions NAV-vs-SPY chart upgraded from an axis-less sparkline to
  an axed line chart; **currency-aware mark-to-market** (convert the quoted price to EUR via the
  yfinance quote currency + FX, and skip a non-EUR holding when its FX rate is missing rather than
  mismark a GBp/USD line as €/share); cyber vehicle corrected ISPY.L/GBp → USPY.L/USD (the line
  actually held).

## v0.4.0 — 2026-06-06 — Entry timing, Positions & live track record

**Feature release.** Three execution-layer additions on top of the v0.3.1 Movement model, all
surfaced on the GitHub-Pages dashboard; recommend-only, nothing trades.

- **Entry-timing overlay** (`catalyx/scorer/entry_timing.py`): micro-tension from yfinance (RSI14,
  stretch-vs-MA20, 10d/90d realized-vol regime, 5d trend, drawdown, a stabilization check) →
  `micro_timing_state` + `tension_score`, plus near-term **event overhangs** (reuse CatalystEvent;
  no new flow). Persisted `entry_timing` lake table; dedicated sortable **Timing page** + inline
  timing in Overview tickets and the sector detail. Thresholds in `scoring_weights.yaml`.
- **Opportunities sharpened:** require a **composite floor (≥55)** (a dip is only an opportunity
  if we'd own the sector on the full blend); the Timing table also flags **`strong · calm`**
  (composite ≥66 + calm) as a clean buy-ready entry.
- **Positions page** (real book, split from the model strategies): summary + **mark-to-market vs
  avg cost** (real unrealized P&L, not the entry-indexed NAV), NAV vs SPY, holdings, a movements
  ledger that **references catalysts** (no duplicated detail), catalyst exposure, and **rotation
  targets anchored to the held sectors** (`dislocation --anchor-sectors` → `portfolio_rotation`).
  Fixed the copper vehicle ticker `4COP` → `4COP.DE`.
- **Live track record** (`nav_engine.compute_live_nav`): walk-forward, chains each run's actual
  holdings from `track_record.yaml` inception (no look-ahead) — the headline; the trailing backtest
  is demoted to a reference shown only while *accruing*. Inception = first real position (Fri
  2026-06-05). Portfolios tab labeled a theoretical exercise; `catalyx` pinned first.
- **Flow fix:** `flow_confirmation` is no longer a constant 50 for sectors without direct flow data
  — `flow_data.py` resolves a proxy (with `flow_proxy_ticker` / `flow_proxy_used` / `flow_data_quality`
  recorded on `sector_snapshot`); the dashboard reads them NULL-safe for pre-fix partitions.
- **Lake hygiene:** pruned orphaned/dev score-runs to a single consistent run; dashboard reads
  one clean run end-to-end.

New lake tables: `entry_timing`, `portfolio_rotation`. 142 tests green.

---

## v0.3.1 — 2026-06-06 — Thesis → Movement (first tagged release)

**Breaking data-model pivot.** The primary capital unit is no longer a heavyweight falsifiable
`Thesis`; it is a **`Movement`** — EUR attributed directly to catalyst(s) via weighted
`attribution[]`, with `action` (open/add/trim/close), `trigger`, `conviction`, and a point-in-time
`score_context`. The **Catalyst** becomes the unit of the track record (`catalyst_ledger`).
Movements are Tier-1 JSON files in `data/movements/` (drop a file → `movement_repo ingest`, which
joins `score_context` to the score_run as-of `executed_at` — no look-ahead — and write-throughs a
`movement` mirror + `catalyst_performance` to the lake). The falsifiable discipline survives as an
optional, machine-checkable `risk_discipline` block.

- **New:** `schemas/movement.json`, `catalyx/store/movement_repo.py`, `data/movements/*`,
  `docs/PLAN_movement_restructure.md`, skills `/catalyx-open` + `/catalyx-close`.
- **Renamed:** `/catalyx-monthly-review` → `/catalyx-review` (`scheduled | event:<catalyst_id>` —
  reviews are no longer monthly-only; operating is independent of reviewing).
- **Repointed:** `nav_engine` real book ← `movement_repo.positions`; `lake_query` lineage walks
  movement → catalysts → run; dashboard "Catalysts & theses" → "Catalysts & positions".
- **Migrated:** the 2 open theses → movements (copper €1000, grid €500, full positions bought on
  the dip 2026-06-04, no rebalance).
- **Deleted (no legacy):** `thesis_repo.py`, `thesis_scorer.py`, `trade_logger.py`,
  `schemas/thesis.json`, `schemas/closed_thesis.json`, `data/theses/`, `catalyx-thesis.md`, the
  empty `portfolio_trade` lake table, a stale dislocation sentinel partition.
- 105 tests green. `pyproject.toml` version 0.1.0 → 0.3.1 (first tagged release; pre-1.0 — see `RELEASING.md`).

---

## 2026-06-06 — Rotated from Recent Changes (CLAUDE.md)

| 2026-06-05 | `catalyx/execution/nav_engine.py` (new) + `trade_logger.py` (new) + `schemas/thesis.json` (1.3) + `lake.py` | v2.2 | **Fase D.2 — NAV-over-time + real-money log + lineage.** `nav_engine`: buy-and-hold NAV series (indexed 100) from holdings — model or real — vs benchmark; price source injectable (yfinance default) → lake `portfolio_nav` (one file/portfolio). `trade_logger`: real trades (with `thesis_id`+`run_id` lineage) → `portfolio_trade`; `real_holdings` derives net positions + realized P&L feeding the same NAV math, so model-vs-real curves are comparable (execution alpha). Thesis schema 1.2→1.3 (enum-tolerant): `metadata.lineage` (origin_run_id/report/heatmap_rank) → trade→thesis→run_id→report+snapshot is one join. End-to-end verified on real yfinance prices (67-pt real NAV). 8 new tests, 77 total green. |

## 2026-06-05 — Rotated from Recent Changes (CLAUDE.md)

| 2026-06-05 | `catalyx/execution/portfolio.py` (new) + `schemas/portfolio.json` (new) + `config/portfolios/{conservative,balanced,aggressive}.yaml` (new) | v2.1 | **Fase D.1 — model portfolios by risk profile.** Deterministic, network-free: a portfolio = `(score_run × risk_config)`. `build_model_holdings` reads lake `sector_snapshot`, applies the profile (filter on composite/momentum/crowding/narrative → dedupe-by-ETF → top-N → composite-proportional weights water-filled under `max_position_pct`), persists to lake `portfolio_holding` (partition portfolio_id+run_id) tagged with `config_version` (md5 of the profile). 3 profiles built from the current run show clean risk separation (conservative drops all `crowded` AI/semis → 5 emerging/mainstream names @ ~20%; aggressive rides them → 12 @ ~8%). 7 new tests, 69 total green. NAV-over-time + real-money trades + thesis/trade lineage = next. (Risk profiles later replaced by 4 strategies in v2.5.) |

## 2026-06-05 — Rotated from Recent Changes (CLAUDE.md)

| 2026-06-05 | `catalyx/store/indicator_history.py` (new) + `lake.py` + `intensity_engine.py` + `backfill_history.py` + `schemas/structural_catalyst.json` (1.4) | v2.0 | **Fase C — indicator `value_history` externalized to the lake.** Moved 273 observations across 8 catalysts out of the hand-edited YAMLs into `data/lake/indicators/` (table `indicator_history`, partitioned by catalyst_id). `intensity_engine` reads the lake first (inline YAML = deprecated fallback for unmigrated catalysts) — post-migration parity verified IDENTICAL. `backfill_history` now writes to the lake (`--migrate-yaml` one-off, no network); new observations append via `indicator_history.append_observation`. Schema 1.3→1.4 (enum-tolerant of 1.3), `value_history` marked `deprecated`. 5 new tests, 62 total green. |

## 2026-06-05 — Rotated from Recent Changes (CLAUDE.md)

| 2026-06-05 | `catalyx/store/lake.py` (new) + `market_data.py` + `flow_data.py` + `momentum_engine.py` + `snapshot_repo.py` + `pyproject.toml` + `.gitignore` + `catalyx-heatmap.md` + `docs/PLAN_lake_dvc_serving.md` (new) | v1.9 | **Parquet lake — Tier 2 source of truth (parquet-first).** New `lake.py`: append-only partitioned parquet (one table = folder of `key=val.parquet` files, committed to git), `append_partition`/`read_table`/`connect()` (DuckDB). `market_data` + `flow_data` dual-write (parquet + compat JSON); `momentum_engine` reads the lake by default (`--snapshot` forces JSON) — lake/JSON parity verified exact (44 sectors, 0 diff). `snapshot_repo.record_run`/`register_report` write through to the lake; new `rebuild` (lake → SQLite). SQLite is now a disposable cache (gitignored, rebuildable); `export` to data/history deprecated. 3-tier storage model documented; +pandas/duckdb. 7 lake tests, 57 total green. |

## 2026-06-05 — Rotated from Recent Changes (CLAUDE.md)

| 2026-06-05 | `catalyx/store/snapshot_repo.py` (new) + `db.py` + `weights.py` + `scoring_weights.yaml` + `catalyx-heatmap.md` (Step 11) + `pyproject.toml` (pyarrow) | v1.8 | **Score history layer (validation foundation).** New append-only store: `score_run` (tags each run with `scoring_version` = md5 of scoring_weights.yaml + git commit), `sector_snapshot` (5 dims + composite + rank + primary ETF + `rationale_md` = the per-sector narrative block), `rank_event` (derived diff vs prior run: entered/exited top-N, rank moves), `report` (markdown linked to run). CLI: `snapshot_repo record\|history\|runs\|events\|register-report\|export\|validate`. `export` → `data/history/*.parquet` (pandas/pyarrow) for notebooks/Evidence/GitHub-Pages. `validate` computes rank-IC + top-N forward-return spread via yfinance (needs ≥2 runs). `crowding_from_maturity` map moved to scoring_weights.yaml (single source — was hardcoded in skill+scripts). Heatmap Step 11 now records every run automatically. |

## 2026-06-05 — Rotated from Recent Changes (CLAUDE.md)

| 2026-06-05 | `catalyst_scorer.py` + `scoring_weights.yaml` + `catalyx-monthly-review.md` (Step 9, 10) + `catalyx-thesis.md` + 3 new structural YAMLs | v1.7 | **Catalyst lifecycle + correlation gate + independent-event scoring.** (1) `catalyst_scorer` now scores **direct/independent events** listed in a study's `active_catalyst_ids` (own decayed-strength term in the noisy-OR), with dedup so an event already linked to a present structural is not double-counted — fixes the `semiconductors_design` "YAML not found" error (89.9→91.5). (2) New `correlated_catalyst_cap` (combined allocation across theses sharing a catalyst = **20%**, flexible `enforcement: warn`) — replaces the old 8% that wrongly reused the Tier-2 single-position ceiling. (3) New `catalyst_lifecycle` config: auto-deprecation (event→archived/invalidated, structural→dormant) applied + logged in Step 10. (4) Step 9 now ASKS per draft candidate (AskUserQuestion). (5) Registered 3 structural catalysts for the momentum-only standouts: `struct_enterprise_cyber_spend_supercycle` (cyber 86), `struct_commercial_space_supercycle` (space 82), `struct_solar_lcoe_deployment` (solar 78) → those sectors jumped composite ~45→71 / 47→72 / 43→66. |

## 2026-06-05 — Rotated from Recent Changes (CLAUDE.md)

| 2026-06-05 | `market_data.py` (v1.6) + `sector_scorer.py` + `catalyx-heatmap.md` + `catalyx-monthly-review.md` (Step 3) | v1.6 | **Full-universe coverage.** `SECTOR_TICKERS` expanded from 17 → ~44 investable sectors (uranium, silver, nuclear, lithium, oil, etc. now fetched). `sector_scorer --universe` scores ALL investable sectors from the taxonomy (momentum baseline even without a study); heatmap no longer gated on study-file existence. Monthly-review Step 3 now studies every investable sector by default (freshness-skip ≤7d, fan out via subagents). **2 bug fixes:** (a) market_data crashed formatting newly-listed ETFs with `None` 3m/6m returns; (b) `dropna()` on closes — yfinance's empty same-day bar (US ETFs fetched in EU morning) was poisoning every US-ticker momentum to NaN→0. |

## 2026-06-05 — Rotated from Recent Changes (CLAUDE.md)

| 2026-06-04 | `catalyx-monthly-review.md` (Step 12) + `CLAUDE.md` | — | Taxonomy Gap Review now contextualizes each pending proposal (thesis / why now / ETF coverage / relation to existing sectors / strength·novelty / risk) and ASKS the user per proposal (AskUserQuestion: promote/reject/defer) instead of a read-only table. `signal_count < 3` defaults to Defer. |

## 2026-06-05 — Rotated from Recent Changes (CLAUDE.md)

| 2026-06-05 | `intensity_engine.py` + `data/backfill_history.py` | v1.5 | De-compress: percentile fallback is a SATURATING curve (weak→50, strong→80, asymptote 100) so over-threshold values grade by margin instead of clamping at 100. `backfill_history.py` pulls real value_history (yfinance: copper HG=F, GLD/DFNS.L flow proxies + cited note values). Catalyst scores now spread 81–95 (gold/nato separate from copper/grid/ai) |

## 2026-06-05 — Rotated from Recent Changes (CLAUDE.md)

| 2026-06-04 | `intensity_engine.py` + `scoring_weights.yaml` + `structural_catalyst.json` | v1.5 | Indicator scoring: 🟢/🟡/🔴 100/65/20 buckets → continuous percentile + fallback. Trend & event interaction → additive points. `user_rank` → display ordering tiebreaker. Color is display-only, derived. `value_history[]` added per indicator (schema 1.2→1.3) |

## 2026-06-05 — Rotated from Recent Changes (CLAUDE.md)

| 2026-06-04 | `catalyx/config/weights.py` | new | Single source of truth: scorers now load weights from `scoring_weights.yaml` instead of hardcoding them (drift fix) |

## 2026-06-04 — Rotated from Recent Changes (CLAUDE.md)

| 2026-06-04 | `catalyx/scorer/catalyst_scorer.py` | v1.5 | Multi-catalyst aggregation: arithmetic mean → max-anchored noisy-OR (mean diluted strong catalysts) |
| 2026-06-04 | `catalyx/execution/tax_engine.py` | fix | `compute_ytd_tax` loss carry-forward: excess loss now carries to later gains instead of being zeroed |

---

## 2026-06-05 — Scoring redesign v1.5: continuous indicators, additive adjustments

Replaces the traffic-light (🟢/🟡/🔴 = 100/65/20) indicator discretization and the
chained multipliers the user flagged as opaque and unstable.

### `catalyx/scorer/intensity_engine.py` + `scoring_weights.yaml` — continuous indicator scoring
**Problem:** the semaphore mapped every indicator to one of three values (100/65/20),
creating a CLIFF — e.g. `cb_gold_accumulation` `ind_02` (COFER, strong=0.58, weak=0.62,
lower_is_stronger, value=0.582) scored 🟡=65 despite sitting right at the strong threshold;
a 0.002 move to 0.580 jumped it to 100. Anchors arbitrary, gaps asymmetric (45 vs 35).
**Fix:** `indicator_scoring.method = percentile_with_saturating_fallback`. Each indicator is
scored to a continuous [0,100]: empirical percentile of its own `value_history` once
≥ `min_history_points` (6) accrue, else a SATURATING threshold curve (weak→50, strong→80,
asymptoting to 100 far above strong). Strong→80 leaves headroom so over-threshold values
grade by margin instead of all clamping to 100 — a naive linear fallback re-saturated
because the data sits far above the thresholds. The COFER case now scores 78.5. Color
(🟢/🟡/🔴) is DERIVED from the score and is display-only — it no longer drives math.

### `catalyx/data/backfill_history.py` — real history activates the percentile path
Pulls `value_history` from yfinance for the market-priced indicators (copper `HG=F`→USD/tonne;
gold/defense ETF flow proxies via `GLD`/`DFNS.L` monthly returns) and seeds the rest from
values explicitly cited in the YAML notes (no fabricated points). With real history, catalyst
intensities de-compress from a flat 95 to a 81–95 spread: `cb_gold` 81.1 (COFER at threshold +
gold ETF flows at the 69th percentile) and `nato_rearmament` 82.7 (defense ETF flows at the
58th percentile) now separate from `copper_datacenter`/`energy_transition`/`ai_capex` (~95).

### Additive adjustments replace multipliers
- **Trend:** `intensity_trend_factors` (×1.05…0.93) → `intensity_trend_deltas` (+5…−7),
  applied as `indicator_avg + trend_delta` instead of `× factor`.
- **Event interaction (`catalyst_scorer.py`):** `confirmation_amplifier ×1.12` /
  `contradiction_dampener ×0.82` → `catalyst_interaction_deltas`
  (`confirm_max_points: 10`, `contradict_max_points: 15`), scaled by decayed strength.
  Floor/cap guards preserved (confirm ≥ structural and ≤ independent blend; contradict ≤ structural, ≥ 0).
- **`user_rank`:** `user_rank_multipliers` (×1.40…0.60 on `display_priority`) →
  `user_rank_ordering` (rank descending by `algorithmic_score`, `user_rank` breaks ties only).
  Stops a weaker-but-preferred catalyst from leapfrogging a materially stronger one.

### Schema migration 1.2 → 1.3 (`schemas/structural_catalyst.json`, 5 catalyst YAMLs)
Each indicator gains `value_history[]` (seeded with the recoverable prior observation),
plus derived `score` and `semaphore` fields. Old config sections kept with
`deprecated: true` for one major version per the Schema Change Protocol.

---

## 2026-06-04 — Critique fixes: wiring, tax, aggregation, single-source weights

Session-wide pass triggered by a project critique. Five concrete defects fixed plus
documentation realignment.

### `catalyx/scorer/sector_scorer.py` — flow auto-load was dead via CLI
**Bug:** `--flow` defaulted to `50.0`, never `None`. Auto-load of the flow snapshot only
fires when `flow_confirmation is None`, so the heatmap (`sector_scorer --all`, no `--flow`)
always used neutral 50 and `inst_sponsorship_score` was always `null`. The entire
`flow_data.py` pipeline was disconnected from scoring.
**Fix:** `--flow` default → `None`; neutral defaults applied inside `score_sector` only when
no datum exists. `inst_sponsorship_score` now surfaces (e.g. copper_miners = 78.2 from EDGAR
13F). Composite scores unchanged today (baseline flow snapshot is all-50).

### `catalyx/execution/tax_engine.py` — loss carry-forward discarded excess losses
**Bug:** `compute_ytd_tax` reset `ytd_loss_carry = 0.0` after applying a loss to a single
gain. A 100 loss followed by two 50 gains taxed the second gain; correct result is zero tax.
**Fix:** consume only `loss_used = pnl - taxable_gain` and carry the remainder forward.
Added `loss_offset_used` / `loss_carry_balance` to the per-trade breakdown.

### `catalyx/scorer/catalyst_scorer.py` v1.4 → v1.5 — aggregation dilution
**Bug:** sector `catalyst_alignment` was the arithmetic mean of per-catalyst scores, so adding
a weaker catalyst *lowered* a strong sector's score — the opposite of the stated intent that
more confirming catalysts = stronger signal.
**Fix:** max-anchored noisy-OR (`_aggregate_alignment`). Strongest catalyst sets the floor;
each additional one closes part of the remaining gap to 100 scaled by its strength and
`reinforce_factor` (0.25, in `scoring_weights.yaml §multi_catalyst_aggregation`). Monotonic,
bounded `[max, 100]`. Single-catalyst sectors unchanged; ai_infrastructure (3 catalysts at 95)
95.0 → 97.1, copper/grid (2) → 96.2.

### `catalyx/config/weights.py` (new) — single source of truth for weights
**Problem:** composite weights, momentum period weights, interaction amplifier/dampener,
sub-weights and decay halflife were hardcoded in the scorers AND listed in `scoring_weights.yaml`.
Recalibrating the YAML changed nothing — the code never read it, violating the project's own
"formulas in code, no drift" principle.
**Fix:** `catalyx.config.weights` loads `scoring_weights.yaml` once (cached) with documented
fallbacks. `sector_scorer`, `momentum_engine` and `catalyst_scorer` now import from it.
Behaviour-preserving (YAML values equalled the old constants).

### `tests/unit/test_tax_engine.py` (new) + `catalyx/cli/main.py` (new)
First unit tests in the repo: 16 cases covering bracket boundaries, incremental tax given
prior YTD gains, loss offset, and the carry-forward regression. CLI `main.py` is a Phase 0.5
stub that lists the wired module CLIs — fixes the `[project.scripts] catalyx` entry point that
pointed to a non-existent module.

### `CLAUDE.md` — documentation realignment
Repository Structure tree annotated with `✅ built` vs `(planned)` so future sessions don't chase
non-existent modules (`llm_client.py`, `valuation_engine.py`, `prior_repo.py`, etc.). Structural
catalyst list corrected to the real 5 files. Key Files table marks unbuilt targets.

---

## 2026-06-04 — Scoring formula fixes + thesis schema v1.2

### `catalyx/config/scoring_weights.yaml` v1.3 → v1.4

**Bug:** Contradiction dampener was flat (`structural × 0.82`) regardless of event strength. A rumor (strength 10) and an official policy reversal (strength 91) produced identical -18% dampening. This was the same asymmetry fixed for the confirms amplifier in v1.3 but left unresolved for contradicts.

**Fix:** Dampener now scales by `effective_event_strength = event_strength × remaining_relevance(t)`:
```
dampener_effective = 1.0 - 0.18 × (effective_event_strength / 100)
catalyst_alignment = max(0, min(structural × dampener_effective, structural))
```
At strength 10: -1.8% dampening. At strength 91: -16.4% dampening. At fully decayed: 0% dampening.

**Also fixed in same session:** `catalyx-heatmap.md` Case A confirms formula was using `remaining_relevance` alone instead of `event_strength × remaining_relevance / 100` to scale the amplifier. Floor added to Case A: `max(structural_component, ...)` — a weak confirming event can no longer reduce the structural baseline.

---

### `schemas/thesis.json` v1.1 → v1.2

**Added: `entry_missed` status**
When `entry_window_closes` passes without the thesis transitioning to `open`, the status becomes `entry_missed`. The thesis remains valid but entry parameters must be re-evaluated before re-activating. Previously the thesis would stay in `draft` with an expired window and no flag.

**Added: `correlation_check` object in `metadata`**
Formalizes the output already produced by `/catalyx-thesis draft` step 2.5. Fields: `correlated_open_theses[]`, `shared_catalysts[]`, `combined_allocation_pct`, `combined_at_tier_ceiling`, `correlation_note`. Previously the skill produced this data but the schema had no slot for it — it would fail `additionalProperties` validation in strict mode.

**Migration:** `thesis_20260603_copper_miners_datacenter_alpha.json` and `thesis_20260603_grid_infrastructure_utilities_bindingconstraint.json` updated from `schema_version: "1.1"` to `"1.2"`.

---

### `.claude/commands/catalyx-heatmap.md` (no version, skill file)

- Case A (confirms): `amplifier_effective = 1.0 + 0.12 × (effective_event_strength / 100)`. Previously used `remaining_relevance` alone, ignoring event strength.
- Case B (contradicts): `dampener_effective = 1.0 - 0.18 × (effective_event_strength / 100)`. Previously flat.
- Floor added to Case A result: `max(structural_component, min(case_a_raw, case_c_equivalent))`.
- Cap added to Case B result: `min(structural_component × dampener_effective, structural_component)`.
- Pre-calibration banner added to Rules: mandatory `⚠ PRE-CALIBRATION` notice on all heatmap output until N > 50 closed theses.

---

### `.claude/commands/catalyx-scan.md` (no version, skill file)

- Added 5 WebSearch queries targeting `analyst_model_revision` events (Goldman/JPM/MS/BofA/UBS sector research).
- Added classification rule: ≥2 Tier-1 banks with ≥10% sector estimate revision in same 30-day window → register as `corporate_event / analyst_model_revision`.
- Added output table "Analyst model revision flags" to the scan summary, linking detected events to affected open theses. This is the primary exit signal for `thesis_20260603_copper_miners_datacenter_alpha`.

---

## 2026-06-03 — Phase 0.5 bootstrap (initial session)

### All schemas — initial versions

| Schema | Version | Notes |
|---|---|---|
| `catalyst_event.json` | 1.2 | Includes `relation_to_structural`, `novelty_rubric_scores[]` |
| `structural_catalyst.json` | 1.2 | Includes `narrative_maturity` enum, `indicators[]` with semaphores |
| `sector_snapshot.json` | 1.1 | Composite score formula slots |
| `sector_study.json` | 1.2 | Includes `cycle_position`, `etf_analysis[]`; deprecated `analyst_narrative_score` |
| `thesis.json` | 1.1 | Full thesis lifecycle (draft → closed); Spanish CGT tax block |
| `closed_thesis.json` | 1.1 | Attribution decomposition, `right_reason_score` formula |
| `taxonomy_gap_proposal.json` | 1.0 | Discovery Pass output format |

### `catalyx/config/scoring_weights.yaml` — v1.3 (initial)

Introduced in this session with scoring stability rules (v1.2 additions), confirms amplifier formula (v1.3), momentum percentile normalization (v1.3), narrative maturity aggregation rule (v1.3), and closed thesis rubrics (v1.3).

### Python infrastructure initialized

- `catalyx/store/db.py` — SQLAlchemy engine, `LLMLog` table
- `catalyx/store/catalyst_repo.py`, `sector_study_repo.py`, `thesis_repo.py`, `structural_catalyst_repo.py`
- `catalyx/data/market_data.py` — yfinance momentum fetcher
- `data/catalyx.db` — SQLite DB initialized

### Data files created

- 5 structural catalyst YAMLs (`cb_gold_accumulation`, `ai_capex_supercycle`, `nato_rearmament`, `energy_transition_grid`, `deglobalization_reshoring`)
- 4 event catalyst JSONs
- 3 sector studies (`grid_infrastructure`, `copper_miners`, `gold_miners`)
- 2 thesis drafts (`copper_miners_datacenter_alpha`, `grid_infrastructure_utilities_bindingconstraint`)

---

## Pre-tag change-counter entries (vN.M) — rotated from CLAUDE.md Recent Changes

> These are the informal `vN.M` change-counter rows (not SemVer), moved here verbatim when the
> CLAUDE.md `Recent Changes` table exceeded its last-5 window. Newest first.

| Date | File | Version | Change |
|---|---|---|---|
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
