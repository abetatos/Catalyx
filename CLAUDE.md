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
tests/  conftest.py (runtime-env isolation) + unit/ 483 tests · docs/ DESIGN_*/PLAN_*/MODULES.md
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

- [ ] `analyst_model_revision` event type in `catalyst_taxonomy.yaml` — the copper thesis alpha
      closes when Goldman/JPM update their models, and the scan currently misses that signal
- [x] ~~**v4 — `docs/PLAN_v4_rigor_and_lean_metrics.md`**~~ **COMPLETE 2026-08-31** (v4.0–v4.8, all
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
| 2026-08-31 | v4.8 | **Dos pasos del review borrados, y una suite que deja de depender de tu shell.** Último paso del plan v4 — **v4 completo**. **D-c:** el Step 4 corría los dos summaries de catalizadores que `/catalyx-scan` ya lee en C1 (4.5 KB por review para repetir lo que el paso 0/1 ya traía); el Step 11 barría 31 sectores no invertibles y su propia instrucción era escribir «no watch trigger surfaced» — que un watch trigger salte ES un evento de investabilidad, así que vive en el Step 12 y en ningún otro sitio. El dashboard pasa de 8.5 a **5.5**: el usuario lee el libro ANTES de que se discutan las posiciones, para que la evidencia llegue antes que el argumento. **D-d:** nuevo `sector_study_repo core` — un estudio son ~20 KB y hay 27, y exactamente **dos** de sus campos los consume algo (`narrative_maturity` → crowding_risk y el test de agotamiento; `active_catalyst_ids` → el mapa catalizador→sector). El resto es la INVESTIGACIÓN: el porqué de esos dos valores, que va delante de un humano que reescribe el estudio, no en el contexto de un run que va a leer dos campos. **25.187 → 2.047 bytes**. `age_days` viaja dentro del digest a propósito: un estudio rancio es peor que ninguno, así que la frescura tiene que llegar en el mismo aliento que el valor que califica. **D-e:** el bloque de TODOs decía que faltaban dos SectorStudies que existen hace semanas → sustituido por una tabla de **estado medido** (desplegado 30% vs regla 85% · TWR real −1.88% vs SPY +4.19% = **−6.07pp** · IC composite −0.050 sobre 1 ventana independiente · scorecard aún no puntuable); los briefs de subagente nombran **un fichero de entrada y una forma de salida** en vez de repetir el orden del pipeline. **D-f:** `nav_engine live-all` en un proceso en vez de un bucle de shell sobre 4 estrategias (**10.4s → 1.9s**, mismos números), y `tests/conftest.py` aísla los switches de runtime: con `CATALYX_PRICES_OFFLINE=1` exportado fallaban 6 tests que inyectan su propio `fetch_fn` y no tocan red. Rechazado hacer que el switch ceda ante un fetcher inyectado — un kill switch que un argumento puede saltarse no es un kill switch; el defecto era que el resultado de la suite dependía del shell. Sin cambios de comportamiento en producción. 488 tests en verde (+5), idénticos con y sin la variable. |
| 2026-08-31 | v4.7 | **La tabla de reglas se vuelve falsable.** `calibration` medía el RANKING; `score_overrides` medía las DESVIACIONES; nadie medía **la tabla**. Cada vez que un humano se apartaba de la regla, el desvío se valoraba a 21 días y se contabilizaba por autor — mientras las reglas conservaban su autoridad por no ser puntuadas nunca. Un libro mayor de una sola cara. Nuevo `score_decisions()` + `decision_scorecard()`, paralelo exacto a `score_overrides`. Tres decisiones dentro deciden si el número significa algo: **HOLD es la línea base, no una fila** — la pregunta que una tabla puede responder no es «¿subieron los nombres?» (eso es beta, y es del `deploy_ratio`) sino «¿actuar batió a dejar el libro quieto?»; un BUY que iguala la base puntúa **0.00pp**. **El retorno futuro se firma con la dirección** en que la regla movió dinero (`action_direction`: +1 ADD/BUY, −1 SELL/REDUCE/TRIM, `None` HOLD/RE-SCORE): un SELL contra un −5% es la regla teniendo RAZÓN, y una tabla sin signo lo imprimiría como la peor fila de la página. **Un veredicto exige n Y ventanas independientes** (`min_n: 5`, `min_effective_windows: 2`, contadas como en `calibration.aggregate`: cinco runs dentro de una ventana son una observación); por debajo, `not scoreable yet`; por encima, un edge dentro de 2·se es `noise`. Hoy: **nada puntuable** — 24 filas, ninguna ventana de 63d cerrada. Decirlo ES el resultado. Sin ventana completa **no hay descarga de precios** (test con un `price_fn` que revienta): durante meses la respuesta honesta es «todavía no» y pagar por imprimirla es coste fijo sobre nada. Nunca cambia un umbral: `frozen` sigue significando commit + línea de CHANGELOG. De paso, dos docstrings corregidos — `review_report.py` y `run_digest` decían no leer precios y ambos lo hacen desde v3 vía `score_overrides`: el edge de un override es un retorno FUTURO y ninguna tabla del lake puede guardarlo hasta que la ventana cierre. 483 tests en verde (+10). |
| 2026-08-29 | v4.6 | **Callarse deja de ser gratis.** v3 construyó todo lo que hace visible una ACCIÓN mala — tabla de reglas, palabras prohibidas, override log, aritmética de suspensión — y nada que haga visible la INACCIÓN. Cada fila imprime su fricción al céntimo desde v3; el coste de dejar €6,953 parados 74 días no se imprimía en ningún sitio, y esa asimetría es un pulgar permanente en la balanza a favor de no hacer nada. **C4:** `cash_drag()` → `€6,953 parados desde 2026-06-16 (74d) · benchmark +3.03% → €211 no ganados`, junto a la fricción MÁS PEQUEÑA que bloquea una operación (**€0.78**), porque la comparación es todo el asunto. Parados *desde que el libro cambió por última vez* — una fecha de movimiento, no de run: un review que recomienda y no se ejecuta no reinicia el reloj. Un benchmark inmedible deja el coste en `None`, nunca en €0. **C1:** el shortfall pasa de nota al pie a regla con persistencia — `shortfall_pp` en puntos de capital TOTAL (30% contra una regla del 85% son **54.5pp**, no «el 35% del camino») y `max_shortfall_pp: 10` × `max_shortfall_runs: 2`, contado por FECHA DE REVIEW; hoy está incumplida. **C3:** `unrecorded_deviations()` pregunta al sistema de ficheros en vez de al narrador — las filas non-HOLD del run anterior contra `data/movements/` y el override log; lo que queda se escribe como DEFER de autor **`unrecorded`** contra el run que lo recomendó, y se valora a ~21 días como cualquier desviación deliberada. La primera ejecución encontró **10**: la tabla entera del run 20260728. **C2:** el Step 9 pierde su opción gratis — `Open now / Wait / Skip` → *Ejecutar €objetivo* · *Ejecutar menos, dilo* · *Declinar, con la evidencia*. «Wait» no escribía nada, así que nada lo puntuaba, así que elegirlo siempre no costaba nada. **C5:** `review_report.py --check` lintea la PROSA (ahí es donde vive el hedging, no en una tabla de reglas) en las secciones donde se dicta una decisión, y calla en las de análisis; el generador no se exime de su propia regla (test). 473 tests en verde (+16). |
| 2026-08-28 | v4.5 | **La convicción ahora se gana.** El pipeline fusionaba dos decisiones en un número y solo una tenía justificación: **cuánto capital está trabajando** es beta — la prima de riesgo de la renta variable, no la habilidad de este modelo — y sigue en `deploy_ratio`; **cómo se inclina ese capital** entre nombres es alfa, y su única justificación es el rank IC medido de la columna que ordena. El softmax dispersaba pesos con la misma agresividad con un IC de **−0.050 (se 0.200, UNA ventana no solapada)** que con uno de +0.4: pagar riesgo de concentración, lo más caro que puede comprar un libro, por un orden que nunca se ha demostrado. Nuevo `w_final ∝ neutral + λ·(model − neutral)` con `λ = clamp(IC/target,0,1) · n_eff/(n_eff+prior)` → hoy **λ = 0.00**. **λ=0 no es «sin modelo»**: el modelo sigue eligiendo los nombres, los filtros, el dedupe de vehículos y el cap — solo deja de dimensionarlos. Y **no es refugiarse en caja**: ambas piernas llevan los mismos nombres al mismo gross, así que el libro sigue al 85% que pide la regla (test-pinned). Lo único que cae es la dispersión: ratio top/bottom 2.58× → 1.82×, y el resto que queda **no es equiponderado** — `vol_tilt` corre después, así que el libro neutral es neutral en *riesgo*: la vol es una medición, no una opinión, y sigue aplicando cuando la opinión se retira. Un IC **negativo** fija λ=0, nunca lo invierte (ponerse corto de tu propio ranking con n_eff=1 es una superstición con signo menos — la misma asimetría que v4.3 dio al `net_edge_gate`). El haircut de régimen viaja también en la pierna neutral: si fuera plana, encoger a λ=0 habría deshecho en silencio el overlay que des-arriesgaba ese nombre. Nuevos `calibration.skill_lambda()` + `composite_ic(dimension=…)` con `effective_windows` (tres runs a seis días sobre un horizonte de 63d son UNA observación; el conteo de filas era el denominador equivocado) y `portfolio.skill_shrink()`; un libro `momentum` se encoge por el IC de momentum (−0.114), no por el del composite. λ se **persiste** (`portfolio_holding.tilt_lambda`, `rebalance.book_tilt_lambda`): un libro objetivo releído dentro de seis meses dice si su dispersión fue ganada o asumida. 457 tests en verde (+11). |
| 2026-08-28 | v4.4 | **Two €500 lines were never two equal bets.** The composite decided what to own and with how much conviction; the euro amount then ignored the one input that makes two euros comparable — `semiconductors_design` (vol 55%) and `pharma_large_cap` (vol 18%) were sized as the same bet. **Measurement first:** new `position_metrics.risk_contribution` (`RC_i = w_i·(Σw)_i/σ_p` from the covariance of daily EUR returns over a common window, summing to 100%). It says two things nothing else could: **copper carries 53% of the book's risk on 35% of its capital** — the largest risk position and the one the table wants to SELL — and **pharma's contribution is NEGATIVE**, anticorrelated enough to LOWER book vol, which is why the ADD is right and what would have to be defended before ever trimming it. Plus `effective_n = 1/HHI`: 5 positions behaving like **4.3**. **Then the sizing:** `portfolio.vol_tilt`, `w_i ∝ transform(score_i)/σ_i^α` applied BEFORE `water_fill` so the cap and deadband are untouched. `vol_tilt_alpha: 0.5` — α=0 is the old behaviour byte-for-byte (still the code default), α=1 is full inverse-vol and would underweight exactly the high-beta sectors the mandate exists to own. Effect: pharma +2.2pp, water +2.4pp, semis −3.0pp, space defense −2.4pp; nothing reordered. A missing vol takes the MEDIAN — never 0 (infinite weight) and never 1 (risk-free); vol is measured on the sector's OWN traded vehicle, and the lookup is `allow_fetch=False` because the portfolio builder is not a fetch site. 446 tests green (+11). |
