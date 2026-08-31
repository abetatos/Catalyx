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
scripts/       pre_run.sh (facts before questions; --check = silent heartbeat) · heartbeat.sh
               (launchd mar+vie 08:12 → notifica si exit 10; --install) · score_run.sh
               post_run.sh (portfolios, NAV, rebalance, → run_<date>.json) · review_report.py
               build_site.py → site/ + .github/workflows/pages.yml (DuckDB-WASM dashboard)
tests/  conftest.py (runtime-env isolation) + unit/ 652 tests · docs/ DESIGN_*/PLAN_*/MODULES.md
experiments/   backtest_signals.py (v8 punto-en-tiempo, caches en scratchpad, → validation/backtest_ic)
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

**The book's shape is one knob, and the constants are linked (v6 L1).** `max_position_pct` is not
only a concentration ceiling: with a deployment target it is a lower bound on the position COUNT,
`n_min = deploy_max / cap` (and `deploy_max / catalyst_cap` bounds the number of independent
drivers). At 12% it silently demanded an 8-position book — 8 of the operator's **10 free trades a
month**. So `book_shape.n_target` (**6**, the measured knee: ρ=0.245 weekly, past 6 each name buys
<2.5% of relative vol and costs a monthly slot) is the only declared number, and
`max_position_pct` + `conviction_tiers` (**20/14/7%**, = `tier_multiples × deploy_max/n_target`)
derive from it. Never edit a derived value; edit `n_target`.
`tests/unit/test_config_feasibility.py` fails the suite if the triple stops being satisfiable —
each constant looked defensible alone, and the triple was not. The model book carries the same
numbers: one it could not execute measures a ceiling no disciplined executor reaches.

**Correlated-catalyst allocation cap:** positions sharing a primary structural catalyst rise and
fall together. Combined allocation is capped by `correlated_catalyst_cap.max_combined_pct`
(**30%**, v6 L2) — DISTINCT from the per-position `conviction_tiers` ceiling. It was 20%, which at
n_target=6 admitted only ONE neutral-weight position per driver — an infeasible rule, permanently
breached, therefore permanently ignored — and it was raised only AFTER `covariance` could publish
risk per cluster, because raising a cap first deletes a breach by decree.
**The cap is notional and the risk column now says notional ranks the buckets wrong** (2026-08-31:
biopharma breaches at 21.9% notional carrying 11.7% of book variance, while gold accumulation sits
at 7.2% carrying 16.6%). That column is evidence FOR a future rule, not a rule — replacing the
notional cap with a risk budget is the user's call.
`enforcement: "warn"` flags a breach and requires an explicit `correlation_note`; set `"block"` to
make it hard. **The cap reads `exposure_eur` — the FULL position behind every driver it names —
never the weight-split `invested_eur`, which is P&L credit.** Weights answer "who gets credit for
the return"; the cap answers "how much money moves if this driver breaks", and a position does not
own 30% of an ETF. Feeding it the split also inverts the incentive: declaring a second driver would
lower the weight on the first and buy headroom for free. Rows therefore sum to more than the book.
And the cap is checked on the **proposed** table too (`movement_repo.cap_check`), not only the held
book — headroom exists to constrain new positions, and §6 and §3 used to be joined by nobody.

**Attribution has two tenses and both are recorded.** `attribution[]` = why the line was opened; a
dated judgement, the validation loop's input, **never rewritten**. `reattribution[]` (movement
schema 1.3, append-only) = what it is held for now — what the cap reads, via
`movement_repo.effective_attribution()`. An entry may also list `not_attributed[]`: a driver the
review saw and declined, with the reason, so `attribution_drift()` stops re-raising a question
already answered. A check that cries wolf trains its reader to skip the table.

**Position actions come from the rule table, not from judgement.** `rebalance.py` emits
`SELL > REDUCE > TRIM > RE-SCORE > ADD > BUY > HOLD` in fixed precedence. `RE-SCORE` moves no
money and is not a verdict: it is what a rank-based SELL degrades to when the sector was absent
from too many recent runs to have a rank worth selling on — "we do not know" is a real state, and
it used to arrive disguised as a sell. **`watch`, `monitor`, `consider`,
`optional` do not exist in the enum** (`BANNED_ACTION_WORDS`, test-enforced) — a verdict that does
not move money is HOLD, said once. Thresholds are **frozen** (`rebalance_rules.frozen`); changing
one is a config edit plus a CHANGELOG line, never a mid-review adjustment. Deviating is allowed
**only as a logged override**, which is priced ~21 trading days later against the action it
replaced and tallied by author.

**The table is audited on the same clock as the deviations from it.** `score_decisions` prices
every recorded `rule_action` over a complete 63d window against the **HOLD baseline** (beating
"leave the book alone", not "the names went up" — that is beta), with the forward return signed by
the direction the rule moved money, so a positive `rule_edge_pp` always means the rule was right.
A verdict needs `min_n` AND `min_effective_windows`; below either it says so. It is evidence for a
config edit, never an edit.

**Inaction is priced, and silence is logged.** Friction is on every row; so is its alternative.
`cash_drag` prices the idle capital against the benchmark it declined, counted from the last
MOVEMENT (a review that recommends and is not executed does not restart the clock). A shortfall
above `deployment.max_shortfall_pp` surviving `max_shortfall_runs` **recorded review dates** must
be executed or overridden in writing, naming the shortfall. And any non-HOLD row of the previous
run with no movement and no override is written by `_log_unrecorded` as a DEFER authored
`unrecorded` — against the run that recommended it, scored like any deviation. Step 9 has no
"Wait": *Execute · Execute less (state it) · Decline (state the evidence)*, every branch logged.
`review_report.py --check` lints the appended PROSE for hedges in the decision sections — that is
where hedging lives; a rule table cannot hedge.

**Sizing separates beta from alpha.** *How much* is at work is `deploy_ratio` — justified by the
equity risk premium, never by this model's skill. *How* the working capital is tilted between the
names is justified only by the measured rank IC of the column doing the ranking, and is shrunk
toward a neutral book by `λ = clamp(IC/tilt_ic_target, 0, 1) · n_eff/(n_eff + tilt_prior_windows)`
(`calibration.skill_lambda` → `portfolio.skill_shrink`). λ=0 keeps every name, every filter, the
cap and the full gross — the model just stops sizing. A **negative IC clamps λ to 0 and never
inverts the book**. Neutral here means neutral in *risk*: `vol_tilt` runs after the shrinkage.

**A weight only means what it says if the scales are commensurable (v6 H).** In a ranking by
weighted sum the effective weight of a dimension is `w·σ_cross`, not `w`. So the composite is
combined in **z-space** — `clamp(50 + z_scale·Σ wᵢ·zᵢ, 0, 100)`, z winsorized, crowding
sign-flipped — via `sector_scorer.commensurate()`, which runs over the WHOLE universe of a run and
therefore cannot be computed for one sector alone. `composite_z` (the raw `Σ wᵢ·zᵢ`, 0.0 = that
run's universe average) is the unit every selection floor is written in (`min_composite_z`),
because a level meant something different every run. **A composite from before schema 1.4 is on
the old absolute scale and is not comparable** — compare by `composite_z` or by rank, and note
that `portfolio` and `dislocation` detect which scale a run carries before applying a floor.
A dimension whose σ_cross collapses is **named** by the dead-dimension lint instead of weighing
zero in silence: that is how `valuation_relative` hid under a 0.15 weight for months (v1.6 removed
the instance, not the mechanism). And **"not measured" is imputed to the prior, never to the worst
case**: no study → CA at z=0, excluded from the CA moments, flagged `ca_imputed`. A study that
found no catalysts keeps its zero — that zero was measured. Like every data flag since v5, it is a
COLUMN, not a gate.

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

**Measured state (2026-08-31)** — four numbers no agent should have to rediscover, and the reason
several rules read the way they do. Refresh them when they move materially, not every run.

| | | |
|---|---|---|
| deployed | **26%** against a 75% rule | the shortfall is 48.7pp and breaches the persistence rule (2 runs) |
| real book, TWR | **−1.43%** vs SPY +4.19% → **−5.62pp** | `vs_benchmark_pct` is a DIFFERENCE; the book is BEHIND |
| composite rank IC | **−0.027** (se 0.209, **1** independent window) | `noise` → λ=0, and the after-tax gate stays disarmed |
| rule scorecard | **nothing scoreable yet** — 54 rows pending a complete 63d window | first verdicts ~63d after the earliest recorded run |

- [x] ~~**v5 — `docs/PLAN_v5_data_action_coherence.md`**~~ **COMPLETO 2026-08-31** (v5.0, los 7
      ítems). Lo que deja abierto y necesita TIEMPO, no código: poblar `spread_bps` por vehículo
      (G1 dejó el mecanismo; el campo se rellena a mano en la revisión trimestral, con mercado
      abierto — un snapshot de yfinance devolvió `ask < bid`), y refrescar los catalizadores que
      la nueva columna `data` marca `blind`: **todos los BUY de la tabla actual se apoyan en
      evidencia stale o blind**, cuatro de ellos 148–240 días ciegos.
- [x] ~~**Decisión humana pendiente (drift de atribución)**~~ **CERRADA 2026-08-31 (v5.2)** con
      `reattribution[]`: copper 0.65 AI capex / 0.35 grid; pharma 0.5 `uncatalyzed` / 0.5 patent
      cliff, declinando GLP-1 por escrito. `attribution_drift()` devuelve `[]`.
- [x] ~~**Decisión pendiente (cap ai_capex al 72,5% bajo la tabla v8)**~~ **DECIDIDA
      2026-08-31:** ejecutar HASTA el cap, no a través de él. Se ejecutan TRIM
      `pharma_large_cap` −€556 y BUY `ai_infrastructure_data_centers` €1.500 (llena el
      cluster exactamente al 30% = €3.000); los otros cuatro rows del cluster quedaron
      DECLINADOS por escrito en el override log del run `run_20260831_184616` (water,
      robotics, cloud — este además `wait_stabilize` —, y el ADD de semis), que es también
      el override que nombra el shortfall de despliegue. Se preciarán a 21d contra la regla,
      autor claude. **Pendiente solo la EJECUCIÓN física:** las dos órdenes en Revolut +
      `/catalyx-close pharma_large_cap` (trim) y `/catalyx-open ai_infrastructure_data_centers`.
- [x] ~~**v8 — `docs/PLAN_v8_backtest_promotion.md`**~~ **EJECUTADO 2026-08-31 (v8.0, P+Q).**
      Harness `experiments/backtest_signals.py` (176 meses, hermanos US, sin look-ahead) →
      `validation/backtest_ic`. Promovido con la tabla delante: momentum oficial = 12-1+52w-high
      (§`momentum_spec`), crowding oficial = comomentum medido con fallback a etiqueta
      (§`crowding_source`), reparto GK shrunk 0.35/0.275/0.24/0.135 (CA y flow en prior
      declarado). COT y Trends siguen candidatas (ruido / panel corto). λ sigue gated por IC
      VIVO; las ventanas vivas de `calibration` son el out-of-sample del reparto nuevo.
      Refrescos que necesita: el backtest se re-corre a demanda (caches en scratchpad),
      nunca alimenta `sector_snapshot`.
- [x] ~~**v7 — `docs/PLAN_v7_signal_content.md`**~~ **EJECUTADO 2026-08-31 (v7.0, M·N·O).**
      9 columnas candidatas con peso 0 en `sector_snapshot`, medidas por `calibration`
      (`CANDIDATE_DIMENSIONS`) desde `run_20260831_172858` — ahí arranca el reloj de ventanas.
      `commensurate()` publica correlación entre dimensiones + n_eff cada run (mom~flow
      **ρ=+0.84** sobre filas medidas). Lo que v7 deja corriendo y necesita TIEMPO: ~3 ventanas
      de 63d antes de que ninguna candidata pueda ascender a peso; refresco MENSUAL de
      `trends_data` (fuente rate-limited, no está en `score_run.sh`); `indicator_sources
      --apply` vive en el scan (en `score_run.sh` corre DRY). El swap etiqueta→`crowding_measured`
      (N4) y cualquier reponderación (K1) siguen siendo decisiones del usuario con IC delante.
- [x] ~~**v6 — `docs/PLAN_v6_signal_scale_and_covariance.md`**~~ **COMPLETO 2026-08-31** (v6.0–v6.7, 17/17 ítems; K queda esperando datos, abajo).
      Lo primero es la FORMA DEL LIBRO: `max_position_pct` no es un techo de concentración,
      es `n_min_posiciones = deploy / cap` — con 12% y despliegue 85% la config OBLIGA a 8
      posiciones y consume 8 de las **10 operaciones gratis al mes**, dejando 2 para un
      mandato event-driven. La terna (techo · cap por catalizador · despliegue) es
      conjuntamente infactible y por eso AI capex sale al 35,6% contra el 20%. Fase L:
      `n_target: 6` (rodilla medida — ρ semanal 0.245, pasar a 7+ compra <2.5% de vol por
      slot) como mando único del que se derivan techo y tiers, presupuesto de operaciones
      con precedencia riesgo→cash drag→rotación, y test de factibilidad. **Fase L (L1·L3·L4·L5)
      y Fase H (H1–H4) + I6 COMPLETAS (v6.0–v6.2).** Queda: covarianza **semanal** (efecto Epps
      medido: ρ diario 0.127 vs semanal 0.245 — la diaria subestima el riesgo a la mitad) → MCTR
      por cluster (I2), que es lo único que desbloquea L2 (subir el `correlated_catalyst_cap` de
      20% a 30%); el deadband que no mueve lo que decidió no mover (J2); columnas duales
      crédito/exposición en el model book (J3); `vol_tilt_alpha` 0.5→1.0 con el supuesto escrito
      (I3); derivación escrita de tiers + `line_risk_pct` (I5); sensibilidad por constante (I4);
      percentil destendenciado (J4). K1–K5 bloqueados por datos, no por código.
- [ ] **v6 Fase K — bloqueada por DATOS, no por código** (`PLAN_v6` §6). No se implementa: se
      espera. Cada una necesita historia que hoy no existe, y construirla antes sería calibrar
      contra ruido — el mismo error que `skill_lambda` existe para no cometer.
      **K1** pesos del compuesto por IC-por-dimensión shrunk (prior = los pesos actuales) —
      bloqueada por ≥3 ventanas independientes de 63d; hoy hay **1**, con IC −0,05.
      **K2** momentum 12-1 — ~~requiere ampliar la ventana de fetch~~ **la premisa era stale**
      (verificado 2026-08-31): `fetch_metrics` pide `period="1y"` desde siempre y el lake ya
      lleva `return_1y_pct` (15/26 vehículos) y `near_52w_high_pct`. Se ejecuta como columna
      sin peso en PLAN v7 (M2/M3); el reemplazo del blend 3m/6m sigue esperando IC.
      **K3** halflife de eventos por `catalyst_type`, calibrada del lake — requiere eventos
      cerrados suficientes por tipo.
      **K4** crowding MEDIDO (comomentum Lou–Polk sobre los vehículos, o percentil de flujos)
      en vez de la etiqueta narrativa, y penalización convexa por encima de `crowded`.
      **K5** `reinforce_factor × (1−ρ)` entre catalizadores, con ρ de la correlación de sus
      historias de indicadores: el noisy-OR dejaría de asumir independencia y la duplicación
      encubierta dejaría de pagar sola. Ahora mismo la mayoría de indicadores tienen 5–6
      observaciones; ρ entre ellos no es estimable.
      *Contexto de prioridad (medido, v6.6):* los cuatro pesos del compuesto son las constantes
      más consecuentes de la cadena (τ 0,86–0,94), así que K1 es la que más compra — y es
      justamente la que más datos necesita.
- [ ] `analyst_model_revision` event type in `catalyst_taxonomy.yaml` — the copper thesis alpha
      closes when Goldman/JPM update their models, and the scan currently misses that signal
- [x] ~~**v4 — `docs/PLAN_v4_rigor_and_lean_metrics.md`**~~ **COMPLETE 2026-08-31** (v4.0–v4.9, all
      10 steps). Two of its diagnoses were verified FALSE against the lake and corrected in place
      rather than quietly dropped — execution alpha is negative (the model books beat the real
      book), and neither current SELL fires on a missing rank. What v4 leaves running: the
      calibration windows, the rule scorecard and the override tally all need TIME, not code.
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
| 2026-08-31 | v8.0 | **Backtest punto-en-tiempo y promoción de candidatas: 12-1, comomentum medido y el reparto GK.** El usuario decidió no esperar ~3 ventanas vivas (≈2027-05); la salida fue ampliar la muestra hacia ATRÁS, no bajar el listón: `experiments/backtest_signals.py` reconstruye las señales sin look-ahead sobre hermanos US — **176 meses, 3.613 sector-mes, fwd63** — y persiste en `validation/backtest_ic`. Veredictos: `momentum_12_1` **+0.104** (69% meses >0, positiva en los tres sub-periodos), `near_52w_high` +0.087 (vive pre-2020), la vigente 3m6m +0.080 y ÚLTIMA en GK (ρ 0.55/0.64 — redundante), `crowding_comomentum` +0.063 (se apaga 2023+), `cot_crowding` +0.006±0.054 RUIDO (su peso GK es artefacto de diversificación), `trends` 46 meses sin veredicto. Un defecto casi entierra al COT: `_MIN_CROSS=8` descartaba todos los meses de un sleeve que cubre 5 sectores — suelo por señal, test fija. **El edit (frozen-threshold, aprobado con la tabla delante):** Q1 momentum oficial = `0.635·pct(12-1) + 0.365·pct(52w-high)` (§`momentum_spec`; fallback 3m6m flaggeado por fila donde no hay 1y, 11/26); Q2 crowding oficial = percentil de comomentum donde existe (15/26), etiqueta donde no (`crowding_source` por fila; COT y Trends NO entran — siguen candidatas); Q3 pool medido 0.41 repartido por GK shrunk (γ≈0.824) → **momentum 0.275, crowding 0.135**; CA 0.35 y flow 0.24 conservan prior DECLARADO (no backtesteables). **Q4:** λ sigue gated por IC vivo, backfill prohibido, ventanas vivas = out-of-sample del reparto, sin grid-search. **Efecto en la tabla:** ai_infrastructure 8→2 (su crowding medido 16,7 vs etiqueta 75), silver 21→10 (12-1 ≫ 3m6m), biotech 3→7 (comomentum 93,3: SÍ está crowded), cloud aguanta el 1 con momentum 98→42 porque su comomentum es 3,3. 652 tests en verde (+13). |
| 2026-08-31 | v6.9 | **La dimensión de flujo medía otra cosa en 20 de 26 sectores.** Salió porque el usuario preguntó por qué el flow imprimía un warning. Lo imprimía con razón: `stockanalysis.com`, la fuente PRIMARIA de shares outstanding, devolvía **47 errores y 0 aciertos** — su endpoint no oficial había empezado a dar 404 —, así que todo caía en cascada hasta CMF, un oscilador de precio+volumen que la propia nota del módulo etiqueta «⚠ not true flow». Veinte sectores puntuaban `flow_confirmation` (peso 0,15) con un indicador técnico. **El arreglo:** el dato sigue en la página, solo murió la ruta. Dos caminos la sustituyen, el más pequeño primero — el payload `__data.json` de SvelteKit (~49 KB, cuya clave `sharesOut` guarda un ÍNDICE al array aplanado y hay que desreferenciar: leída como valor da 27) y la página renderizada (~228 KB) como respaldo. **Revalidado contra el screener oficial de iShares** en 8 fondos solapados: −4,15%..+0,92%, dentro de la banda ~0,25–1,75% con la que esta fuente se aceptó en su día. Cobertura de 6 a **18 de 26** sectores con shares reales. **Lo que la reparación MIDIÓ, que es lo que hay que quedarse:** doce sectores pasaron de CMF a shares reales de golpe, así que el error del sustituto ya no se supone, se observa — **|error| medio 13,1 puntos sobre 100, máximo 25,6** —, y los dos errores mayores cayeron justo en los dos nombres sobre los que la tabla actuaba: `gold_miners` inflado (CMF 78,2 vs 52,6 real) y `cybersecurity_commercial` desinflado (28,8 vs 54,2). **Una hipótesis se comprobó y se DESCARTÓ antes de escribirla:** CMF no es momentum disfrazado — su correlación con la dimensión de momentum es +0,16, así que el fallo es inexactitud, no doble contabilidad. **Por eso CMF ahora se imputa, no se puntúa:** quedan 8 sectores sin proxy US limpio, y 13 puntos de error medio no son un proxy. Se tratan como v6 H2 ya trata un estudio ausente — imputados al prior (z=0), fuera de los momentos de la dimensión, y marcados como COLUMNA (`flow_imputed`, `~flow` en el digest), nunca como puerta. La rama específica de CA en `commensurate` se generalizó a un mapa `_IMPUTED_FLAG` en vez de duplicarse, con un test que fija que el camino de CA no ha regresado. **Dos defectos de ranking que destapó el re-scoring, de la misma forma que el #3 de v6.8:** `composite_z` se guardaba con `round(s, 3)` — y desde que v6.8 lo convirtió en la unidad sobre la que se ordena, ese redondeo es el mismo defecto un decimal más abajo (`water_infrastructure` y `semiconductors_design` empataban en +0,457 y los separaba el orden de la lista); ahora 6 decimales, así que un empate es un empate de verdad. Y el digest imprimía solo `comp` a 1 decimal mientras ordenaba por z, lo que hacía parecer mal ordenada una tabla correcta; ahora imprime `z=` al lado. **Efecto en la tabla viva, que es por lo que esto no es cosmético:** `cybersecurity_commercial` pasa de rank **10 a 5**, convirtiendo un **TRIM −€565 en un ADD +€727** — el trim se apoyaba en un peldaño cuya condición es «el modelo ya no lidera este nombre», y el modelo solo dejó de liderarlo porque una fuente rota le había desinflado el flujo 25 puntos. `semiconductors_design` 5→6 (ADD → HOLD), `ai_infrastructure_data_centers` 6→8 (se cae su BUY de €1.456), `gold_miners` 8→11. De ocho acciones a seis, y el incumplimiento del cap de `struct_ai_capex_supercycle` baja de **55,4% a 38,8%** del nocional (€881 por encima, no €2.540). 614 tests en verde (+2). |
| 2026-08-31 | v7.1 | **La pipeline corre sola: heartbeat en launchd (mar+vie 08:12), determinista, sin LLM.** `scripts/heartbeat.sh` refresca COT + valoración (+ Trends si >28d), aplica los indicadores auto-observables por `indicator_update` + write-back, y corre `pre_run.sh --check` — que gana dos disparadores de la doctrina de cadencia adaptativa: **techo duro de 45d** desde el último run y **VIX ≥ ramp_start (25)**. Exit 10 → notificación de macOS; el silencio es un RESULTADO. La intelligence sigue siendo la sesión (decisión permanente): el ping dice «abre un review», nunca opera. El CronCreate pendiente de la memoria 2026-08-04 se descartó con causa: es solo-de-sesión (expira a 7 días) y no puede sostener una cadencia de 30-45d. Instalación: `bash scripts/heartbeat.sh --install`. |
| 2026-08-31 | v7.0 | **PLAN v7 ejecutado (M·N·O): 9 columnas candidatas con peso 0, medidas por `calibration` desde `run_20260831_172858`.** `momentum_12_1`, `near_52w_high`, `ca_unpriced`, `flow_resid`, `inst_sponsorship`, `crowding_comomentum`, `cot_crowding`, `trends_crowding`, `crowding_measured` — ningún peso del compuesto cambia (test-enforced). `commensurate()` publica matriz de correlación + n_eff con cada run (primer run: **mom~flow ρ=+0.84 sobre filas medidas, n_eff 2.9/4**). Reparado de paso: el 13F llevaba muerto desde el universo v2.0 (el primario pasó a ser siempre UCITS → ahora camina la cadena al hermano US, `inst_proxy_ticker`). COT en vivo: oro percentil **99.2**, cobre **100** de posicionamiento especulativo 5y. O1: 7 indicadores auto-observados (FRED/yfinance/CFTC) vía `indicator_update` — la primera pasada cazó USD/CNY −7.3% y JPY net short +58% con datos de mayo/junio. O3: serie `valuation` nueva en el lake (19/26 con PE). Detalle en CHANGELOG. |
| 2026-08-31 | v6.8 | **Seis defectos que el review se encontró a sí mismo, y el que llevaba meses reescribiendo scores.** Salieron al correr un SEGUNDO `/catalyx-review` el mismo día — condición que varios necesitaban para hacerse visibles. Ningún threshold movido; todo son correcciones. **(1) La intensidad alimentaba su propia tendencia.** `write_back` escribía la fila de HOY en `intensity.history` y `_trend_delta` leía `scores[0]-scores[1]` — o sea, su propia estimación previa. El score era una iteración de punto fijo sobre sí mismo: `struct_cb_gold_accumulation` hizo **78,6 → 68,5 → 64,5** en dos runs con el mundo quieto, cada paso reportado como `↓ falling 1 period`. Compone, y en la única dirección que parece una tesis muriéndose. Arreglado en tres capas: `compute_intensity` excluye la fila con la fecha de hoy (es su propia estimación, no un periodo cerrado; una etiqueta explícita `--period 2026-Q3` no casa y se conserva), `write_back` REEMPLAZA la fila de ese periodo en vez de apilar otra, y `_trend_delta` deduplica por periodo al leer, lo que repara todo fichero ya escrito del modo viejo. **Nueve de trece catalizadores tenían periodos duplicados —hasta SEIS filas de `2026-Q2`—, así que esto llevaba meses malleyendo la pata de tendencia**, no solo hoy. Un test fija que tres write-backs seguidos dan salida idéntica. **(2) `--all --write-back` escribía los scores en los ficheros EQUIVOCADOS.** `compute_all()` salta `merged` y `role: macro_context` (13 resultados) pero el CLI los emparejaba con un `glob("*.yaml")` fresco (18 rutas): el score del oro acabó en `biopharma_patent_cliff_ma`, el de `ai_capex` en `commercial_space`, y los últimos cinco ficheros no se escribieron nunca. Los resultados ya llevan `_source_file`; ahora cada escritura se direcciona por él. Es el comando que la entrada de v6.7 manda ejecutar, así que el bug estaba armado esperando a quien siguiera las instrucciones. **(3) El rank publicado salía del compuesto REDONDEADO.** `composite` va a un decimal, así que cualquier par a menos de 0,05 era un empate resuelto por el orden del fichero de taxonomía — cuando v6/H1 ya había declarado `composite_z` la unidad comparable. En vivo hoy: `space_defense_satellite` (49,4, z −0,041) por encima de `nuclear_energy` (49,4, z −0,038) por pura suerte alfabética. `sector_scorer.rank_key()` elige la unidad y se usa en todos los sitios que ordenan, incluido el corte top-N de `portfolio` — que es donde el redondeo cuesta dinero. **(4) El ranking del work list no era el ranking grabado:** `--universe` pasaba un `--crowd` plano a todos, σ_cross de crowding = 0 y el lint de dimensión muerta lo cantaba, en la vista cuyo único trabajo es alimentar los estudios. **(5) Cinco sellos de frescura rebotaron, en los catalizadores recién verificados:** el enum de `catalyst_review` tenía tres valores mientras `catalyx-scan.md` documentaba cinco, y el docstring afirmaba que mapeaban «sin traducción». `strengthening` se rechazaba, así que tres drivers de posiciones abiertas seguían leyéndose stale el día que se re-verificaron. **(6) Dos líneas que mentían en el transcript:** `catalyst_lifecycle --apply` imprimía «dry run — use --apply» cuando no había transiciones, e `indicator_update batch` imprimía `Σ None` en la única línea que dice qué catalizador se movió. 612 tests en verde (+6). |
