# CATALYX Changelog

> Full version history. **Do not read this file every session** — the `Recent Changes` table in `CLAUDE.md` covers the last 5 entries and is always in context.
> Read this file only when you need to answer: "when did X change?", "what was the previous formula?", "why was field Y added?"
>
> **How to add an entry:** when `Recent Changes` in CLAUDE.md reaches 6 entries, move the oldest row here verbatim and add detail below it.
>
> **Versioning (since v0.3.1):** SemVer, **pre-1.0** (early/unstable), one number in `pyproject.toml`, tagged `vX.Y.Z` on `main`. See `RELEASING.md`. The earlier `vN.M` labels below are an informal pre-tag change counter, not SemVer.

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
