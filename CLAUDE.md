# CATALYX — Project Intelligence

> Read this file first, every session. It is the single source of truth for architecture
> decisions and development protocol. It is loaded into the main session **and into every
> subagent**, so anything that is not needed on every run belongs in a linked doc instead:
> **module inventory + CLIs → [`docs/MODULES.md`](docs/MODULES.md)** · version history →
> [`CHANGELOG.md`](CHANGELOG.md) · design rationale → inline in each module + `docs/DESIGN_*`.

---

## What This Project Is

A sector-ETF analysis platform built around one pipeline:

**MACRO CATALYST → THESIS FORMULATION → POSITION EXECUTION → VALIDATION & FEEDBACK**

1. Detect and score macro catalysts before they are priced in
2. Formulate structured, falsifiable, machine-readable theses
3. Track execution with full Spanish tax-aware P&L
4. Measure whether a thesis was right — and whether it was right *for the right reasons*
5. Feed validated/invalidated theses back into future scoring as a prior probability table

**Investor profile:** data scientist and experienced trader. High risk tolerance. Momentum and
catalyst-driven. ETFs only. Monthly review cadence with event-driven updates.

**Non-negotiable:** sectors are maximally granular. Gold ≠ gold miners ≠ silver ≠ copper. EU
defense primes ≠ US defense ≠ cybersecurity. Every differentiation has a reason.

---

## Architecture — permanent hybrid, not a migration path

| Claude (interface + intelligence) | Python (deterministic backbone) |
|---|---|
| Conversational thesis formulation | Scoring formulas (no LLM drift) |
| News analysis & catalyst detection | Market data (yfinance, one cached fetch/run) |
| Assumption critique, sector narrative | File + parquet-lake reads/writes |
| Review orchestration, output for the user | Spanish CGT, attribution, decay, rebalance rules |

**Skills invoke Python.** A skill (`.claude/commands/*.md`) calls `uv run python -m catalyx.<module>`
via Bash, gets deterministic JSON, and reasons on top of it. **Claude never free-assigns a number a
formula can compute.**

> **Direction decision (2026-06-05) — permanent.** CATALYX stays a skill on the Claude Code
> session. The intelligence layer IS the session (its credits + WebSearch). Therefore these are
> **off the roadmap, not "later"**: any `anthropic`/`openai` client, an `llm_client.py`, the
> `llm_log` table, a user-facing Typer CLI, FastAPI, Postgres. There is **no database** — see
> storage below. Never reintroduce one.

**Storage — two tiers, parquet-first.** *Tier 1 (git, hand-edited):* config YAML, `schemas/`, and
the JSON documents skills Read/Write (`data/sector_studies|movements|catalysts|taxonomy_proposals`).
These stay JSON forever — they are the skill interface, and writing the file IS the registration.
*Tier 2 (parquet lake, git):* every computed series — snapshots, score runs, indicator history,
portfolios, NAV, rebalance, overrides. **Claude never Reads parquet directly**; a Python CLI emits
JSON to stdout (`lake_query`, `snapshot_repo`). Details: `docs/PLAN_lake_dvc_serving.md`.

---

## Catalyst Model: Dual Types

Never collapse these into one.

| Type | Example | Temporality | Validated by |
|---|---|---|---|
| `EventCatalyst` | NATO 3.5% GDP announcement | discrete, timestamped, decays | did the event materialize? |
| `StructuralCatalyst` | central banks systematically buying gold | onset + ongoing, persistent | are `indicators[]` still active? |

Structural = the floor signal. Event = the spike. Both feed `catalyst_alignment` with different
decay functions.

---

## Repository map

```
CLAUDE.md · CHANGELOG.md · pyproject.toml
.claude/       settings.json, hooks/guard.py (cross-platform), commands/ (8 catalyx-* skills)
catalyx/       scorer/ execution/ attribution/ thesis/ data/ store/ config/ cli/main.py (stub)
               execution/ = tax_engine · nav_engine · portfolio · rebalance · position_metrics
  config/      sector_taxonomy.yaml (CANONICAL ids) · etf_universe.yaml (only BUYABLE vehicles)
               scoring_weights.yaml (SINGLE SOURCE of weights + rules) · weights.py (accessors)
               portfolios/*.yaml · track_record.yaml · structural_catalysts/*.yaml
schemas/       catalyst_event · structural_catalyst · sector_snapshot · sector_study
               movement (primary capital unit, replaced thesis) · taxonomy_gap_proposal · portfolio
data/          catalysts/ sector_studies/ movements/ taxonomy_proposals/ reports/  (Tier 1)
               lake/  ← parquet lake (Tier 2)
scripts/       pre_run.sh (facts before questions; --check = silent heartbeat) · score_run.sh
               post_run.sh (portfolios, NAV, + rebalance) · review_report.py (report skeleton)
               build_site.py → site/ + .github/workflows/pages.yml (DuckDB-WASM dashboard)
tests/unit/    358 tests · docs/  DESIGN_*/PLAN_*/MODULES.md
```

Before citing any path, `ls`/glob to confirm. Module inventory + every CLI: `docs/MODULES.md`.

---

## Key Files — What to Read When

**Always read these before editing that area.**

| Working on… | Read first |
|---|---|
| Any data schema / model | `schemas/<relevant>.json` |
| Sector scoring, heatmap | `config/sector_taxonomy.yaml` + `schemas/sector_snapshot.json` |
| Opening/closing positions, attribution | `schemas/movement.json` + `docs/PLAN_movement_restructure.md` |
| Structural catalysts | `config/structural_catalysts/<id>.yaml` + `schemas/structural_catalyst.json` |
| Tax / P&L | `catalyx/execution/tax_engine.py` — Spanish CGT, progressive, no short/long split |
| ETF selection | `config/etf_universe.yaml` — buyability first (see Broker reality) |
| Scoring formulas | `config/scoring_weights.yaml` + the relevant `catalyx/scorer/*.py` |
| Rebalance / position actions | `catalyx/execution/rebalance.py` + `scoring_weights.yaml rebalance_rules` |
| Sell signals, exits | `docs/DESIGN_sell_signals.md` + `catalyx/scorer/exit_watcher.py` |
| Parquet lake / computed series | `store/lake.py` (primitive) + `store/lake_query.py` (DuckDB read-path) |
| Catalyst / study / movement reads | the file-backed `*_repo.py` (read `data/` directly, no DB) |
| Taxonomy gaps | `schemas/taxonomy_gap_proposal.json` + `data/taxonomy_proposals/*.json` |
| LLM / intelligence | the Claude Code session itself. There is no client, and never will be. |

---

## Critical Implementation Rules

**Broker reality — the filter that outranks every other (universe v2.0, 2026-08-27).**
El usuario opera con **Revolut, residente fiscal en España**. Un ETF US no-UCITS (`ITA`, `XLE`,
`GDX`, `XBI`, `COPX`, `SOXX`, `TAN`, `LIT`, `ROBO`…) **NO se puede comprar**: PRIIPs exige un KID
que los emisores US no publican. No es el bróker, es regulatorio — no hay workaround.
- **`etf_universe.yaml` solo contiene instrumentos comprables.** Añadir una entrada exige
  verificarla contra yfinance (`longName`/`currency`/`exchange`) y copiar el `longName` REAL. La
  v1.1 tenía 66/96 entradas inaccesibles y **errores de identidad** (`IQQR.DE` etiquetado robótica
  siendo *MSCI Eastern Europe*) — de ahí los tickets que no se podían abrir.
- **Un sector es `investable: true` solo si tiene vehículo comprable.** Sin vehículo no puede ser
  objetivo de un `Movement`; estudiarlo y rankearlo cada ciclo es gasto puro.
- **El momentum se mide sobre el vehículo que se opera** (`SECTOR_TICKERS` en `market_data.py`), no
  sobre un hermano US: puntuar `COPX` y comprar `4COP.DE` mide un retorno que no obtienes.
  *Excepción deliberada:* `SECTOR_FLOW_TICKERS` en `flow_data.py` sí usa proxies US porque yfinance
  solo expone `sharesOutstanding` en fondos US. **No unificar ambas tablas.**
- **`broker_access`**: `verified` = operado de hecho (evidencia en `data/movements/`) · `assumed` =
  UCITS en LSE/XETRA/Euronext/SIX, sin confirmar en la app. Al proponer un `assumed`, decirlo.

**Un catalizador = un driver económico.** Dos catalizadores que suben y bajan por la misma razón
cuentan doble en `catalyst_alignment` y burlan el `correlated_catalyst_cap` (que existe justo para
eso). Un driver puede golpear varios sectores — para eso está `affected_sectors`; lo que no puede
haber es el mismo driver modelado dos veces. Al fusionar: el superviviente conserva su `id` (lake y
sector studies lo referencian), el absorbido queda `status: merged` + `merged_into` +
`merge_rationale`, y **los indicadores NO se copian** — su historia vive en el lake indexada por el
`catalyst_id` original y moverla falsearía el percentil de `intensity_engine`. `compute_all()` salta
`merged`, `deactivated` y `role: macro_context`; usa `structural_catalyst_repo.resolve()` para
seguir un `merged_into` antes de leer la frescura de un catalizador.

**Currency:** all P&L in EUR. Non-EUR ETFs converted at execution date; tax always in EUR. A
native-currency mark against an EUR cost basis is a bug (it shipped once — see v2.25).

**Spanish CGT:** progressive brackets on ALL capital gains regardless of holding period. Calendar
tax year, applied sequentially across realized gains YTD. 2026: 19% ≤€6k · 21% ≤€50k · 23% ≤€200k ·
27% above.

**IDs:** movements/theses `thesis_YYYYMMDD_sectorid_keyword` · events `cat_YYYYMMDD_keyword` ·
structurals `struct_keyword_keyword`. Human-readable slugs, never UUIDs.

**ETF flow data:** `shares_outstanding × NAV`, never total AUM (AUM conflates price appreciation
with net flows).

**Crowding risk is a penalty, not a reward** — high crowding subtracts from the composite.

**Correlated-catalyst allocation cap:** positions sharing a primary structural catalyst rise and
fall together. Combined allocation is capped by `correlated_catalyst_cap.max_combined_pct`
(default **20%**) — DISTINCT from the per-position `conviction_tiers` ceiling (12/8/4%).
`enforcement: "warn"` flags a breach and requires an explicit `correlation_note`; set `"block"` to
make it hard.

**Position actions come from the rule table, not from judgement.** `rebalance.py` emits
`SELL > REDUCE > TRIM > ADD > BUY > HOLD` in fixed precedence. **`watch`, `monitor`, `consider`,
`optional` do not exist in the enum** (`BANNED_ACTION_WORDS`, test-enforced) — a verdict that does
not move money is HOLD, said once. Thresholds are **frozen** (`rebalance_rules.frozen`); changing
one is a config edit plus a CHANGELOG line, never a mid-review adjustment. Deviating is allowed
**only as a logged override**, which is priced ~21 trading days later against the action it
replaced and tallied by author.

**Watch-only sectors** (`investable: false`): appear in the heatmap with a NOT-YET-INVESTABLE
banner, cannot be a `Movement` target, and are monitored via `watch_triggers[]` only.

**Attribution confidence:** mark `"low"` when `holding_days < 60`, or when sector_beta and
catalyst_alignment are both > 80% (collinear). Never claim false precision.

**Dashboard language:** all rendered dashboard copy (`site/index.html`, `site/app.js`,
`build_site.py` baked text) is **English-only**. The user works in Spanish in chat — never leak it
into the app.

---

## AI Scoring Stability Rules

LLM numbers are unstable across sessions: an "84" from one session ≠ an "84" from another.

1. **Compute intensity, never guess it.** `intensity.current_score` derives from the continuous
   indicator scores per `scoring_weights.yaml` (`round(clamp(indicator_avg + trend_delta, 10, 95), 1)`).
   Each indicator scores to [0,100] by empirical percentile of its lake history once
   ≥ `min_history_points`, else a saturating threshold curve. The colour is a display label derived
   from the score. **History lives in the parquet lake**, keyed by `catalyst_id` — the inline
   `value_history` is deprecated (schema 1.4) and `intensity_engine` reads the lake first. Record
   observations with `catalyx.store.indicator_update`, never by hand-editing the YAML. Only
   `computation_method: "bootstrap"` permits a manual value, and only at file creation.
2. **Categories for qualitative dimensions.** `narrative_maturity` → the 5-level enum
   (`ignored/emerging/mainstream/crowded/exhausted`), never a number. `is_priced_in_estimate` →
   one of 0 / 0.25 / 0.50 / 0.75 / 1.0. `novelty_score` → count(true) in `novelty_rubric_scores` × 20.
3. **Anchor a new catalyst to an existing one** ("intensity similar to `struct_cb_gold_accumulation`
   (84)"). That inter-catalyst calibration is what persists across sessions.
4. **Ordinal beats cardinal.** "A ranks above B" is more reliable than "A=87, B=84". Use the
   computed scores, interpret them as a ranking.
5. **WebSearch before reading YAML.** Stored values are last month's. Flag any indicator where the
   live value differs by >10%.

---

## Sector taxonomy & user_rank

- `sector_id` is the canonical identifier; free-text sector names never appear in code.
- `sector_taxonomy.yaml` is the single source of truth for valid ids and for `investable`.
- Quarterly: check ETF AUM (< €200M → liquidity warning) and spread (> 25bps → warning).
- `user_rank` is a **display ordering tiebreaker, not a multiplier** (v1.5). Catalysts sort by
  `algorithmic_score` desc, `user_rank` breaking ties only — user preference among near-equals never
  lets a weaker catalyst leapfrog a stronger one. Config `user_rank_ordering`; the old
  `user_rank_multipliers` table was deleted 2026-08-28 (formula in CHANGELOG under v1.5).
- Nothing is deleted: retired sectors keep `retired_*` fields, archived catalysts keep
  `status: "archived"`.

---

## Pipeline order — MANDATORY

Not a suggestion: each step produces what the next one needs. `/catalyx-review` orchestrates it.

```
0.  scripts/pre_run.sh        deterministic facts + tiered work list + override tally. BEFORE any search.
0/1 /catalyx-scan             macro front door: C0 context · Pass 1 discovery (no taxonomy) · Pass 2
                              refresh of every catalyst ON THE WORK LIST → scan_deltas_<date>.json
2.  apply the deltas          indicator_update batch + catalyst_review batch + catalyst_lifecycle
                              --deltas --apply   (one file, three consumers)
3.  /catalyx-sector-study     PREREQUISITE for the heatmap — movement-driven, not a sweep
4.  catalyst digests          structural_catalyst_repo summary + catalyst_repo summary
5.  /catalyx-heatmap          re-rank; then score_run.sh + post_run.sh (portfolios, NAV,
                              position metrics, REBALANCE)
6.  position reviews          risk_discipline + regime, action taken FROM the rebalance table
7.  catalyst exposure         combined exposure per catalyst vs the cap
8.  tax snapshot YTD          realized from closing movements
9.  open recommendations      AskUserQuestion — opening itself is /catalyx-open, separate
11. watch-only triggers       findings-driven, never a 30-search sweep
12. taxonomy gap review       context block per proposal, then ASK (promote/reject/defer)
```

- **Why pre_run first:** a review that opens with searches discovers its own book's problems last.
- **Why the scan before any file:** project files are a month stale, WebSearch is today, and the
  delta between them is often the review's most important finding.
- **Why discovery ignores the taxonomy:** reading it first biases the search toward known sectors
  and creates a blind spot for exactly the themes the pass exists to find.
- **Why studies before the heatmap:** a sector with a fresh study scores on every dimension; one
  without ranks on a momentum-only baseline. A STALE study is worse than none — it injects
  confident, wrong full-dimension scores.
- **The review recommends; it never operates.** Opening and closing are `/catalyx-open` and
  `/catalyx-close`, run separately, whenever the user decides.

## Slash commands (`.claude/commands/`)

| Comando | Qué hace |
|---|---|
| `/catalyx-review [scheduled\|event:<catalyst_id>]` | El review completo, en el orden de arriba. Recomienda, no opera. |
| `/catalyx-scan` | Macro front door: C0 + discovery + refresh por catalizador → `scan_deltas_<date>.json` |
| `/catalyx-sector-study <sector_id>` | Genera/actualiza el `SectorStudy` JSON |
| `/catalyx-heatmap` | Ranking de sectores leído del run grabado |
| `/catalyx-update <id> <ind> <val>` | Observación de indicador → `indicator_update` (nunca a mano) |
| `/catalyx-open <sector_id>` | **Opera.** Escribe un `Movement` (open/add/trim) + ingest |
| `/catalyx-close <sector_id\|etf>` | **Opera.** Cierra → P&L realizado + CGT + close movement |
| `/catalyx-dashboard` | Digest de catalizadores. El report `catalyst_dashboard_*.md` está retirado del review. |

---

## Schema Change Protocol

When any file in `schemas/` changes:
1. **Bump `schema_version`** in that schema file.
2. **Add a migration note to `CHANGELOG.md`** — what changed, and how old documents are read.
3. Update the Pydantic model / reader in the corresponding Python module.
4. Check every existing JSON in `data/` using it — migrate, or add a version-tagged read path.
5. **Never delete a field** — mark `"deprecated": true` and keep it for one major version.

When `sector_taxonomy.yaml` changes: does the new sector have a **buyable** vehicle in
`etf_universe.yaml`? Does it need a `demand_driver` weight override? If a sector is removed, grep
`data/movements/` — an open movement cannot reference a removed sector.

---

## What Is Still Missing (open TODOs only)

Everything else — schemas, taxonomies, the scoring/execution/attribution layer, the lake +
DuckDB read-path, the rebalance engine, the dashboard — is **built**. See `docs/MODULES.md` for the
inventory and `CHANGELOG.md` for when each landed.

- [ ] SectorStudy for `eu_defense_prime_contractors` and `ai_infrastructure_data_centers`
- [ ] `analyst_model_revision` event type in `catalyst_taxonomy.yaml` — the copper thesis alpha
      closes when Goldman/JPM update their models, and the scan currently misses that signal
- [ ] v3 Phase 2 remainder: `portfolio/position_metrics` lake table + the dashboard Rebalance tab
      (`docs/PLAN_v3_lean_pipeline_rebalance.md` §3.4/§3.5)
- [ ] `return_decomposer` → lake `validation/`
- [ ] ML feedback loop on closed theses (`prior_repo`, xgboost/sklearn — offline, no LLM)
- [ ] Backtesting harness (GDELT/COT, strict no-look-ahead: detection may use only data available
      at signal time)

---

## Recent Changes

> One line each. Full entries — the *why*, the bug, the rationale — in
> [`CHANGELOG.md`](CHANGELOG.md), newest first. Read it on demand ("when did X change?"), not
> every session. The *why* also lives inline in the modified file.

| Date | Version | Change |
|---|---|---|
| 2026-08-28 | v3.4 | **Position & book metrics + the dashboard Rebalance tab — v3 plan COMPLETE.** New `execution/position_metrics.py` → lake `position_metrics`/`book_metrics`: EUR P&L split **price / FX / named basis residual** (an identity, `entry_fx` implied by the cost basis), drawdown **from peak**, days held, vol since entry, **score drift** vs the `score_context` the position was opened on (first run: grid at **−37.4** — the model stopped believing before the price did). Book: deployment, HHI, FX exposure, vol/Sharpe/maxDD/beta+corr, tracking error, model overlap. **Two defects found by running it:** `portfolio_nav` stores backtest/live/forward under ONE portfolio_id, so reading them by date splices two curves and invents ±18% daily moves (95.5% → 22.3% tracking error; `_nav_series` now takes a mode); and textbook active share assumes both books sum to 100% — ours do not, so it was answering "how far apart" not "how much of the model do we own" (both now reported: active share 49.3%, **model overlap 15.9%**). `corr_vs_spy` is printed beside beta for the same reason — beta 1.00 here is 2× the vol at half the correlation, not index tracking. Dashboard `#/rebalance`: target vs actual with action/€/CGT/net edge, the measurement table, the book-shape strip with the currency split, and the override log; non-buyable sectors flagged red, not dropped. 375 tests green (+17). |
| 2026-08-28 | v3.3 | **Context & maintenance hygiene.** CLAUDE.md 54.9 KB → 19.7 KB (module table → `docs/MODULES.md`, Recent Changes → one-line rows); review skill 651 → 296 lines; new `scripts/review_report.py` writes every deterministic report section from the lake and marks ranked sectors that are not buyable today; `pre_run.sh --check` is the silent weekly heartbeat (exit 0 quiet / 10 attention). Deleted for being wrong, not long: a June-era "Data files state" block, and the review's hand-applied lifecycle rules that `catalyst_lifecycle.py` had owned for six weeks. |
| 2026-08-28 | v3.2 | **Thresholds frozen + the override log gets scored.** `deployment.base` 0.60→0.70, `floor` 0.30→0.40, profit ladder cut to one rank-coupled rung, `rank_out_of_top` 12→10. Overrides priced as `(trade_chosen − trade_rule) × EUR forward return`, tallied by author; Claude's authorship suspends arithmetically on a net-negative record. |
| 2026-08-28 | v3.1 | **Scan → update in one hop + kill list.** `indicator_update.py` replaces ~44 hand-applied observations that were writing to a deprecated field the scorer no longer read. Searches per review ~40 → ~15. `merged` catalysts no longer reach the work list or the freshness gate. |
| 2026-08-28 | v3.0 | **Rebalance engine** — target vs actual, in €, after tax. Fixed-precedence action enum, deployment ratio instead of cash-by-feel, rank-bucket calibration as the €-denominated edge term. |
