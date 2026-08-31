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
               post_run.sh (portfolios, NAV, rebalance, → run_<date>.json) · review_report.py
               build_site.py → site/ + .github/workflows/pages.yml (DuckDB-WASM dashboard)
tests/  conftest.py (runtime-env isolation) + unit/ 517 tests · docs/ DESIGN_*/PLAN_*/MODULES.md
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
| deployed | **30%** against an 85% rule | the shortfall is 54.5pp and breaches the persistence rule |
| real book, TWR | **−1.88%** vs SPY +4.19% → **−6.07pp** | `vs_benchmark_pct` is a DIFFERENCE; the book is BEHIND |
| composite rank IC | **−0.050** (se 0.200, **1** independent window) | `noise` → λ=0, and the after-tax gate stays disarmed |
| rule scorecard | **nothing scoreable yet** — no complete 63d window | first verdicts ~63d after the earliest recorded run |

- [x] ~~**v5 — `docs/PLAN_v5_data_action_coherence.md`**~~ **COMPLETO 2026-08-31** (v5.0, los 7
      ítems). Lo que deja abierto y necesita TIEMPO, no código: poblar `spread_bps` por vehículo
      (G1 dejó el mecanismo; el campo se rellena a mano en la revisión trimestral, con mercado
      abierto — un snapshot de yfinance devolvió `ask < bid`), y refrescar los catalizadores que
      la nueva columna `data` marca `blind`: **todos los BUY de la tabla actual se apoyan en
      evidencia stale o blind**, cuatro de ellos 148–240 días ciegos.
- [x] ~~**Decisión humana pendiente (drift de atribución)**~~ **CERRADA 2026-08-31 (v5.2)** con
      `reattribution[]`: copper 0.65 AI capex / 0.35 grid; pharma 0.5 `uncatalyzed` / 0.5 patent
      cliff, declinando GLP-1 por escrito. `attribution_drift()` devuelve `[]`.
- [ ] **Decisión pendiente AHORA (v5.2 la destapó):** ejecutar la tabla tal cual deja
      `struct_ai_capex_supercycle` en **35,6%** contra el cap del 20% — €1.560 de dinero nuevo
      (`ai_infrastructure_data_centers` + `cloud_software_saas`) hacia un bucket con **€0** de
      headroom — y `struct_energy_transition_grid` en 23,7%. `enforcement: warn`, así que el cap no
      decide: hay que bajar tamaño, soltar un nombre, o escribir el `correlation_note`.
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
| 2026-08-31 | v5.2 | **Cerrar el drift destapó que el cap leía el número equivocado.** v5.1 imprimió el drift y dejó la decisión; el usuario la tomó, y cerrarla bien exigió arreglar dos cosas debajo — porque re-atribuir con la contabilidad vieja habría hecho el libro parecer MÁS SEGURO. **El mecanismo:** `reattribution[]` append-only (movement 1.3). `attribution[]` sigue intacto (por qué se abrió la línea, lo que puntúa el loop de validación); lo que faltaba era dónde escribir el presente. Cada entrada lleva `as_of`, la nueva atribución, un `not_attributed[]` opcional y un `rationale`; gana el `as_of` más reciente, vía `effective_attribution()`. `not_attributed[]` existe porque un check que revuelve una pregunta YA respondida enseña a saltarse la tabla: el driver declinado desaparece, con la razón en el fichero. **Las dos decisiones:** copper 0.65 AI capex / 0.35 grid (la electrificación de red mueve cobre con independencia de los racks y sobreviviría a una pausa de capex; el datacenter era la tesis de apertura y sigue primaria); pharma 0.5 `uncatalyzed` / 0.5 patent cliff, **declinando GLP-1 por escrito** — se abrió como línea defensiva sin catalizador y esa mitad sigue siendo verdad, así que `uncatalyzed` conserva peso en vez de que se le invente una tesis que nunca tuvo; y un ETF de pharma large-cap no es un vehículo GLP-1. **Lo que eso destapó: el cap estaba leyendo el número de P&L.** `catalyst_ledger` reparte por peso de atribución — correcto para CRÉDITO — y §6 le daba ese reparto al `correlated_catalyst_cap`. Pero la posición de grid (€500, 0.7/0.3) aportaba **€150** a la fila de AI capex cuando, si AI capex se rompe, los €500 enteros están en riesgo: nadie tiene el 30% de un ETF. Y el incentivo iba al revés — nombrar un segundo driver BAJABA el peso del primero, así que la honestidad compraba headroom. Nuevo `exposure_eur` (la posición completa detrás de cada driver) es lo que lee el cap; `invested_eur` se queda como crédito de P&L, reetiquetado en §6. Las filas suman más que el libro, a propósito. En este libro el bucket estaba infravalorado en €350 **antes** de re-atribuir nada: €1.650 contra €2.000 reales sobre un cap de €2.000. **Y entonces el número que importaba:** §6 mira lo que se tiene, §3 propone lo que comprar, y nadie los unía — pese a que la propia frase del cap es «headroom es lo que una posición NUEVA puede tomar». Nuevo `cap_check()`: ejecutar la tabla actual tal cual deja `struct_ai_capex_supercycle` en **35,6%** contra el 20% — €1.560 de dinero nuevo hacia un bucket con **€0** de headroom — y grid en 23,7%. Un BUY resuelve por el estudio del sector (no hay posición que atribuir); un ADD por la atribución de la posición, driver declinado incluido, porque re-derivarlo del estudio anularía en silencio un juicio ya escrito. De paso: `_try` del digest marcaba MISSING un resultado vacío, así que el check de drift se listaba como agujero del run justo al salir limpio (`empty_ok`); y dos tests afirmaban «este libro no rompe ningún cap» — cierto al escribirlos, falso ahora — reescritos para leer la expectativa del libro en vez de congelar un dato de un día en un test de un mecanismo. 517 tests en verde (+4). |
| 2026-08-31 | v5.1 | **El cap estaba siendo burlado por el merge que debía sobrevivir.** Persiguiendo el cabo suelto que dejó v5.0 apareció un defecto mayor, y la nota de CLAUDE.md tenía el mecanismo MAL: §6 no usa el mapa point-in-time de `catalyst_exposure_rows`, lee `movement_repo.catalyst_ledger`, que agrupa por el `attribution[]` congelado de cada movimiento. **`struct_copper_datacenter_demand` está `merged_into: struct_ai_capex_supercycle` y el ledger los publicaba como dos filas** — €1.000 bajo el id absorbido y €650 bajo el superviviente, un solo driver económico contado dos veces. Juntos son **€1.650 = 16,5% contra un cap del 20%**, así que §6 anunciaba **€1.350 de headroom donde había €350**, sobre la mayor exposición del libro. CLAUDE.md ya lo exigía («usa `resolve()` para seguir un `merged_into`») y `run_state` ya lo aplicaba nombrando los merges del 2026-08-27 por fecha — el ledger era el único lector que no. Ahora resuelve vía `merged_map()` (una lectura de YAML, no una por id — la lección de v5.0) y reporta `absorbed_ids` para que el colapso sea auditable; `resolve_merged=False` devuelve el registro crudo, los ficheros de movimiento no se tocan y el loop de validación sigue puntuando lo que se escribió. **La otra mitad, deliberadamente sin cambiar:** `pharma_large_cap` se abrió con `attribution: [uncatalyzed 1.0]` y su estudio nombra ahora tres drivers, uno de ellos (`struct_biopharma_patent_cliff_ma`) **compartido con el BUY de €978 en biotech de la misma tabla** — el cap no puede ver un solapamiento archivado bajo `uncatalyzed`. El arreglo NO es reescribir la atribución: es el registro fechado de POR QUÉ se abrió la línea. Nuevo `attribution_drift()` nombra el hueco (solo estructurales: el cap se escribe «per shared primary STRUCTURAL catalyst», y listar cada `cat_*` enterraba los dos casos reales bajo una docena que no lo son). §6 y el digest leen el ledger EN VIVO de los ficheros de movimiento en vez de la partición `catalyst_performance`, que congela el mapa de merges del día en que se escribió — el mismo defecto una capa más abajo. 513 tests en verde (+2). |
| 2026-08-31 | v5.0 | **Coherencia dato↔acción: ningún dato muerto financia una compra en silencio.** Los siete ítems del plan v5, en su orden. **F2:** el `review_20260831.md` salió con sus **cinco** marcadores de juicio vacíos y pasó `--check` limpio — `lint_prose` vigila la prosa que ESTÁ, nada vigilaba la que falta. Nuevo `lint_completeness`. Al implementarlo: dos de los siete marcadores son CONDICIONALES (overrides de este run, breach del cap) y exigir prosa donde la respuesta honesta es «nada» es como un lint enseña a escribir relleno → el generador, que ya lo sabe, solo los emite cuando hay algo que decir. **E1:** §8 listaba 41 indicadores vencidos y §3, 130 líneas antes, ordenaba €1.020 a `luxury_goods` — cuyo `catalyst_alignment` 70.4 ES la intensity de un catalizador con dos indicadores sin observar desde 2025-09-30. `catalyst_freshness` existía solo para posiciones ABIERTAS, así que un BUY llegaba sin nada que lo calificara. Nuevo `freshness.by_catalyst()` (`fresh`·`stale`·`blind` = nada dentro de 2× la cadencia) + el peor estado entre los catalizadores del sector. El resultado ES el hallazgo: **todos los BUY de esta tabla se apoyan en datos stale o blind, cuatro de ellos 148–240 días ciegos.** Es una COLUMNA, no una regla — `decide_action` no la lee, test-enforced: dato viejo manda RE-VERIFICAR, no dejar de actuar. **F1:** el report exigía 8 trades hacia un ranking que él mismo declara nulo y llamaba breach a no hacerlos; ambas cosas pueden ser ciertas — el prior de estar invertido no es el prior de que TU ranking ordena — pero nunca los separaba. Una línea cuando la evidencia es NONE/ADVERSE, nada cuando es MEASURED. Frase, no gate. **E3:** el dedup miraba solo el run anterior, así que la misma decisión pendiente escribía un DEFER por run — 30 filas para 10 decisiones. Ahora una decisión, un DEFER, con su `logged_at` original. **E2:** los 4 runs del SELL de copper distaban de 5 días a un mes; `min_gap_days: 21` y la razón nombra el span (`3 consecutive review cycles (54d)`). **Ninguna acción cambia.** **F3:** `forgone` estaba hardcodeado (un trimestre en que estar en liquidez fue ACERTADO se imprimía igual) y medía contra SPY cuando lo juzgado es «¿debí ejecutar ESTA tabla?». La etiqueta ahora se invierte y manda el model book: **€196 contra el benchmark, €14 contra `catalyx`** — el benchmark exageraba el coste de no actuar 14×. *Encontrado escribiendo F3, en código de la misma hora:* `portfolio_nav` mezcla modes `backtest`/`live` bajo el mismo `portfolio_id` con bases distintas (~124 vs ~103) y sin fijar el mode salía **−16,88%** — un ahorro de €1.179 que nunca ocurrió. Es el defecto que v3.5 arregló en `portfolio_compare`, reintroducido en una lectura nueva. **G1:** `b/e 0.20%` idéntico en 7 de 8 filas; `cost_drag` acepta override por ETF desde v3 y nadie se lo pasaba. Mecanismo listo, campo **vacío a propósito**: el snapshot de yfinance devolvió `ask < bid` en SEMI.L y 463bps/193bps fuera de hora — un número inventado ahí es peor que el default. 511 tests en verde (+18). |
| 2026-08-31 | v4.9 | **Una columna que significaba dos cosas, y tres bloques que decían lo mismo N veces.** **`rk` tenía dos semánticas según la fila.** Mostraba el rank del MODEL BOOK, ausente para exactamente los sectores que el modelo descartó — o sea en blanco en toda fila cuya razón cita un número («ranked below top-10 (#11)» junto a `rk` vacío). v4.3 lo tapó con un fallback marcado `~11` en el render del CLI, y ese parche **es el defecto**: una columna que cambia de significado por fila oscurece más de lo que dice, y el otro renderizador (`review_report.py`) nunca lo recibió, así que los dos divergieron en una semana. Ahora **una sola semántica en los tres sitios** — el rank del universo, el número que citan las razones y que muestra §1. El del model book se queda interno. **Debajo del fallback había un bug de verdad:** `partial_rungs` leía el rank del model book para su pata de rank, que significa «el modelo ha DEJADO de liderar este nombre» — es decir, era ciega exactamente cuando debía disparar: copper (#11, fuera del book) imprimía `still a leader (rank nan < 6)`, al revés de lo cierto, y con un NaN que sobrevivió a un `is None` porque venía de parquet. Nuevo `_clean_rank` + saneo NaN→None en el borde del parquet; el render de filas sale a `_render_rows`, puro y testeable sin lake. **Overkill fuera, sin perder un dato:** §8 pasa de 41 filas de indicador a 13 de catalizador (el peor indicador de cada uno + cuántos van vencidos; la lista completa es una llamada al CLI); las desviaciones no registradas se agregan por run («1 run → 10 acciones: 3×ADD · 5×BUY · 2×SELL») en vez de repetir el mismo run diez veces; y un barrido de ranks **todo en la misma dirección** deja de imprimirse como N hallazgos independientes — 20 filas de `rank_up` y ninguna `rank_down` es el DENOMINADOR moviéndose (corte de universo o edit de scoring), y ahora se dice como el único hecho que es. Las notas explicativas repetían cada run el porqué que ya vive en el docstring y el CHANGELOG: se quedan en una línea de semántica. En el digest, las definiciones de los rungs se izan fuera de las filas (vienen de UNA config; repetirlas por posición decía la regla cinco veces para decir cinco distancias una). Medido: report **19.431 → 14.054 bytes** (−28%), `run_<date>.json` **24.289 → 19.917** (−18%). 492 tests en verde (+4). |
| 2026-08-31 | v4.8 | **Dos pasos del review borrados, y una suite que deja de depender de tu shell.** Último paso del plan v4 — **v4 completo**. **D-c:** el Step 4 corría los dos summaries de catalizadores que `/catalyx-scan` ya lee en C1 (4.5 KB por review para repetir lo que el paso 0/1 ya traía); el Step 11 barría 31 sectores no invertibles y su propia instrucción era escribir «no watch trigger surfaced» — que un watch trigger salte ES un evento de investabilidad, así que vive en el Step 12 y en ningún otro sitio. El dashboard pasa de 8.5 a **5.5**: el usuario lee el libro ANTES de que se discutan las posiciones, para que la evidencia llegue antes que el argumento. **D-d:** nuevo `sector_study_repo core` — un estudio son ~20 KB y hay 27, y exactamente **dos** de sus campos los consume algo (`narrative_maturity` → crowding_risk y el test de agotamiento; `active_catalyst_ids` → el mapa catalizador→sector). El resto es la INVESTIGACIÓN: el porqué de esos dos valores, que va delante de un humano que reescribe el estudio, no en el contexto de un run que va a leer dos campos. **25.187 → 2.047 bytes**. `age_days` viaja dentro del digest a propósito: un estudio rancio es peor que ninguno, así que la frescura tiene que llegar en el mismo aliento que el valor que califica. **D-e:** el bloque de TODOs decía que faltaban dos SectorStudies que existen hace semanas → sustituido por una tabla de **estado medido** (desplegado 30% vs regla 85% · TWR real −1.88% vs SPY +4.19% = **−6.07pp** · IC composite −0.050 sobre 1 ventana independiente · scorecard aún no puntuable); los briefs de subagente nombran **un fichero de entrada y una forma de salida** en vez de repetir el orden del pipeline. **D-f:** `nav_engine live-all` en un proceso en vez de un bucle de shell sobre 4 estrategias (**10.4s → 1.9s**, mismos números), y `tests/conftest.py` aísla los switches de runtime: con `CATALYX_PRICES_OFFLINE=1` exportado fallaban 6 tests que inyectan su propio `fetch_fn` y no tocan red. Rechazado hacer que el switch ceda ante un fetcher inyectado — un kill switch que un argumento puede saltarse no es un kill switch; el defecto era que el resultado de la suite dependía del shell. Sin cambios de comportamiento en producción. 488 tests en verde (+5), idénticos con y sin la variable. |
