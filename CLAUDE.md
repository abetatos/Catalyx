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
tests/  conftest.py (runtime-env isolation) + unit/ 667 tests · docs/ DESIGN_*/PLAN_*/MODULES.md
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
- **UCITS no es el test; el KID de PRIIPs lo es.** Un fondo de un solo metal NO PUEDE ser UCITS
  (la diversificación lo prohíbe), así que todo el oro/plata físicos de Europa son **ETCs**
  `ucits: false` — con KID, en Xetra y LSE, y comprables. `snapshot_repo.primary_etf` filtra por
  **VENUE** (`PRIIPS_VENUES`), no por el flag: una cotización US falla diga lo que diga `ucits`.
  Filtrar por `ucits: true` (2026-08-28 → 09-01) dejaba `gold_physical` y `silver_physical` sin
  vehículo, y `portfolio.py` **los excluye del libro** cuando `primary_etf` es null — silver
  rankeaba 10º sin poder recibir un euro. Un fallback silencioso a un ticker no comprable sigue
  siendo peor que `None`.
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
at 7.2% carrying 16.6%). **The user made that call (v10 P6, 2026-09-01) and the answer was BOTH,
for one cycle.** `max_cluster_variance_pct: 35.0` runs BESIDE the notional cap, not instead of it;
both stay `warn`, and where they DISAGREE is the evidence that decides which survives — collapsing
them to one verdict would delete the only thing the pairing is for. Live today:
`struct_energy_transition_grid` clears the notional cap at 15.0% while carrying 44.6% of book
variance. A cluster whose risk cannot be measured is `over_risk: None` and is reported UNMEASURED —
never read as within budget. `cluster_risk_pass` (a post-pass, because a cluster's risk is a
property of the POST-trade book) blocks ADD/BUY over budget and names the highest-contribution
HELD member as a trim candidate; it never touches SELL or REDUCE, because a risk limit that can
cancel a risk REMOVAL is arguing with itself.
`enforcement: "warn"` flags a breach and requires an explicit `correlation_note`; set `"block"` to
make it hard. **The cap reads `exposure_eur` — the FULL position behind every driver it names —
never the weight-split `invested_eur`, which is P&L credit.** Weights answer "who gets credit for
the return"; the cap answers "how much money moves if this driver breaks", and a position does not
own 30% of an ETF. Feeding it the split also inverts the incentive: declaring a second driver would
lower the weight on the first and buy headroom for free. Rows therefore sum to more than the book.
And the cap is checked on the **proposed** table too (`movement_repo.cap_check`), not only the held
book — headroom exists to constrain new positions, and §6 and §3 used to be joined by nobody.
**That check counts the SELLS that fund the buys** (v9): it used to skip every negative row, so a
table closing a position to buy inside the same cluster was measured as if the sale never happened
— live, that read 39.1% (breach) against 29.1% (compliant) on the same table. The deduction is
capped at the exposure BASIS: a winner sold above cost cannot retire more exposure than it carried.

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

**The catalyst score is a formula; its INPUTS were where the LLM still typed numbers (v11).**
The chain is deterministic end to end — indicator → percentile over its own history → intensity →
event/structural interaction → noisy-OR — and no LLM assigns the final score. It assigns *inputs*,
and two of them were bare cardinals with nothing behind them.
- **`strength_score` needs a rubric AND a bracket.** `novelty_score` has been stable across
  sessions since v1.5 because it is `count(true) × 20`; strength had prose ("magnitude × surprise ×
  confirmation" — a formula only if all three are defined, and none were) and behaved accordingly:
  16 events in [55, 91] clustered on round numbers, three of them exactly 72. Now
  `strength_score_rubric` (5 observable booleans) plus `strength_anchors` naming one registered
  event it is stronger than and one it is weaker than. **The bracket is what survives the session;
  the absolute number never did.** Lint before writing: `catalyst_audit --active`.
- **`decay_halflife_days` comes from the type table.** The author picks the TYPE, the table picks
  the number; deviating needs `halflife_override_reason` (schema 1.3). This rule existed in the
  scan's prose from the start and 6 of 16 events ignored it, **5 toward longer persistence** — the
  direction that keeps a spent event scoring. `halflife_source` ships as `stored`, NOT `taxonomy`,
  and that is deliberate: adopting the table moves a live position's CA (copper's confirming event
  decays 38.9 → 4.0) on the authority of a constant six reviews declined to use. Either the six
  authors were wrong or `commodity_supply: 21` is, and defaulting to the table answers that **by
  decree** — the move this file already forbids for the correlated cap. Both are published; the
  choice is the user's, with the priced table in front of them.
- **The strength LEVEL is re-spaced by its rank** inside the registered-event population
  (`calibrate_strength`), range-preserving and monotone — it fixes spacing, never scale or order.
  Same medicine `intensity_engine` gives indicators, and CLAUDE.md rule 4 finally acting on events.
- **Absence of contradicting evidence is only evidence if someone looked.** The registry held 31
  `confirms` against 2 `contradicts` while the scan carried no instruction to search for the short
  case — every query is written around a catalyst's own thesis, so it retrieves the corpus that
  agrees. Meanwhile the scorer's disconfirmation machinery (`contradict_max_points` 15 vs `confirm`
  10; two live contradicts → `breaking`) ran on a feed nobody was filling. Scan Step **C2c** is
  mandatory for `must_reverify`, and `disconfirming.found: false` is a RESULT, never a blank.
- **`ca_logit` and `event_tape_z` are COLUMNS, weight 0** (v7 discipline: ≥3 independent 63d
  windows before anyone argues for a weight). `ca_logit` exists because the level scale packs nine
  sectors into 1.7 points (0.06σ) exactly where the book concentrates and `ca_conviction_tilt`
  takes its z; the logit buys ×3.56 resolution there. `event_tape_z` is the first input in the
  chain not read from a document — the sector's abnormal EUR return after each event, in units of
  its own PRE-event vol, signed by what the event claims (a `contradicts` is confirmed by a FALL).
  It **names** an unconfirmed event and never downgrades one: the market being slow is this
  project's premise, and auto-deferring to the tape would invert the mandate.

**The table is audited on the same clock as the deviations from it.** `score_decisions` prices
every recorded `rule_action` over a complete 63d window against the **HOLD baseline** (beating
"leave the book alone", not "the names went up" — that is beta), with the forward return signed by
the direction the rule moved money, so a positive `rule_edge_pp` always means the rule was right.
A verdict needs `min_n` AND `min_effective_windows`; below either it says so. It is evidence for a
config edit, never an edit.

**Where we do NOT invest is a decision, and holding fewer names is one of its answers (v10).**
The book used to have exactly one way to be small — fail to deploy — so every mechanism that could
shrink it arrived as a shortfall the persistence rule re-opened forever. Three pieces fix that and
they are separate on purpose. **(1) The conviction gate is ABSOLUTE and the composite floor is
not:** `min_composite_z: 0.0` means "above THIS run's average", which in a fading universe still
admits half the field, so `portfolio.conviction_gate()` reads `catalyst_alignment` — a real level
in [0,100] — against `entry_gate.min_catalyst_alignment` (40, read off the measured bimodal gap,
not chosen). An **imputed** CA is not judged (turning "not measured" into "fails" is the inference
v5/v6 removed); a **measured zero** is. Top-N runs over the survivors, so cash appears only when
fewer than `max_positions` names qualify — which is the state that had no way to exist before.
**(2) The shortfall is measured against what the book can HOLD** (`absorbable_eur`,
`deployment.shortfall_basis`): at a 20% ceiling four names top out at 80%, so a book the rules cut
to four was scored against an unreachable target. The relaxation is not a hiding place — the
ceiling is the cap's own arithmetic, `shortfall_vs_deployable_pp` never leaves the output, and
`cash_drag` still prices every idle euro. **(3) A cash stance is an override**, not a new document
type: same log, same author, same reason, priced at ~21d against the benchmark it declined — and it
EXPIRES (90d), because silence is what the shortfall rule exists to break. It sets `breached:
false` with `breach_raw: true`: the breach is answered, never erased, and it re-opens by itself.
Machine authors (`budget`, `ramp`, `unrecorded`) may not write one. CLI: `rebalance cash-stance`.
**Freshness is still NOT a gate** — `_data_age_by_sector` qualifies a row and never votes, and
stale evidence means RE-VERIFY, not stop acting. A `blind`-blocks-BUY rule was considered in v10
and deliberately not built for that reason; it remains the user's open call.

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

**The ratio says WHERE the book belongs; the ramp says how fast it gets there (v9 R1).**
`deployment.ramp.max_step_pp` (**15pp** of total capital) is the most one review may raise the
DEPLOYED share, so an under-deployed book stops being asked to close the whole gap at one
afternoon's prices — the largest timing bet the system can make and the one nobody chose. It caps
the NET, so a rotation passes untouched; it fills in RANK order to FULL size, because six
quarter-positions cost six slots and six spreads to buy a quarter of the exposure; and it
**never deletes the breach** — the shortfall is still measured against the full `deployable`, the
cash drag still prints beside it, and every queued row is logged (author `ramp`) and priced at 21
days, so the schedule itself is falsifiable. Order matters: **cash is rationed before slots**
(ramp, then `trade_budget_plan`) — a slot spent on a row the ramp will queue is spent on nothing.
The table's *destination* and its *order ticket* are different questions and are printed as such:
`THIS ITERATION` lists what to place today, a trailing `*` marks what a scarcity queued.
**Neither scarcity ever queues a full exit.** The ramp grants every negative row whatever its
size (selling deploys nothing), and the slot budget exempts `SELL`/`REDUCE` *and* any row that
closes the line — `is_full_exit()` + `exempt_full_exits`, because a full exit arrives labelled
TRIM whenever the model target is 0%, which is how every exit from a sector the book no longer
holds arrives. A TRIM against a LIVE target is still turnover and still starves first.

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
- [ ] **Decisión abierta del usuario: ¿se mueve `n_target` ahora que ρ se mide en EUR?** (v10.4)
      La migración a EUR está HECHA (señal, target, covarianza, comomentum). Lo que queda es la
      constante: ρ semanal de los 26 vehículos del universo cayó **0,199 → 0,179** al convertir —
      y ésa es la muestra de la que se leyó la rodilla de `n_target: 6`. Un ρ más bajo empuja
      hacia MÁS nombres. **No se tocó a propósito:** mover el mando único (del que derivan
      `max_position_pct` y los `conviction_tiers`) en el mismo edit que cambia la base de medición
      destruye la capacidad de atribuir el efecto. Sobre el libro VIVO de 6 nombres el cambio es
      inmaterial (ρ +0,0025), así que no hay urgencia. Recalcular la rodilla con la serie EUR es
      el siguiente paso cuando se decida.
- [x] ~~**¿Se convierten los retornos a EUR antes de puntuar?**~~ **RESUELTO 2026-09-01 (v10.4).**
      Sí, y en los cuatro sitios a la vez: señal (`market_data`), **target**
      (`calibration.forward_returns` — el que más pesaba), covarianza y comomentum. Una fila
      declara su base (`return_basis`), un panel mezclado avisa (`basis_mixed`), y una fila que no
      pudo convertirse se queda nativa y lo dice. Lo que deja CORRIENDO y necesita tiempo, no
      código: las particiones de momentum anteriores a la migración se leen como `"native"` y no
      son comparables con las nuevas — el histórico ES recomputable desde los cierres cacheados
      (`prices` guarda nativo + divisa por ticker), pero sólo hasta donde llega la caché.
- [ ] **v11 — decisión humana pendiente: ¿quién tiene razón sobre el halflife?** El mecanismo está
      construido y las dos lecturas se publican; falta elegir. `commodity_supply: 21` está en la
      tabla desde el principio y **seis eventos consecutivos lo ignoraron** (90/90/120), siempre
      hacia más persistencia. Una de las dos cosas es falsa: o el halflife de 21 días es
      demasiado corto para una disrupción de oferta real, o los autores estiraban el número para
      que su evento siguiera contando. **El coste de equivocarse no es simétrico** — con la tabla,
      el evento confirmador del cobre pasa de aportar 38,9 a aportar 4,0. Correr
      `catalyst_audit` (imprime la tabla preciada), decidir, y o bien poner
      `event_catalyst_decay.halflife_source: taxonomy`, o bien **editar la tabla** por tipo — pero
      no dejar que cada evento siga eligiendo. Edit de umbral congelado + línea de CHANGELOG.
- [ ] **v11 — los 19 eventos registrados son cardinales desnudos.** `catalyst_audit --active` da
      **16/16 FAIL**: ninguno lleva `strength_rubric_scores` ni `strength_anchors`. No es urgente
      (nada se cae, el scorer los puntúa igual) y no debe hacerse de golpe: la rúbrica se responde
      con la evidencia del evento delante, así que lo natural es rellenarla en el próximo
      `/catalyx-scan` para los que sigan vivos y dejar morir a los demás por decay.
- [ ] **v11 — `event_tape_z` y `ca_logit` necesitan TIEMPO, no código.** Ambas son columnas peso 0
      desde el primer score run posterior a 2026-09-01; ahí arranca el reloj. ≥3 ventanas
      independientes de 63d antes de que ninguna pueda argumentar un peso, y en el caso de
      `ca_logit` la pregunta concreta es si el `ca_conviction_tilt` debe tomar su z del logit en
      vez del nivel — decisión del usuario con el IC delante.
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
| 2026-09-01 | v11.0 | **El score de catalizadores es una fórmula; dos de sus ENTRADAS las escribía un LLM a mano, y nadie las contrastaba nunca contra la cinta.** Salió de «veo números muy altos y de repente muy bajos… al fin y al cabo lo está asignando un LLM». La premisa era medio falsa y por eso importa el diagnóstico: la cadena es determinista de punta a punta (indicador → percentil sobre su historia → intensidad → interacción → noisy-OR); ningún LLM asigna el score final. Pero entra por seis puertas en los inputs. **Lo que causa el patrón, medido:** la bimodalidad es **sesgo de selección doble** — nadie escribe un estructural a intensidad 40, y el scan solo crea eventos con `strength ≥ 55`, así que la distribución está censurada por debajo **por diseño**; y el techo está saturado, **nueve sectores entre 95,0 y 96,7** = span 1,7 contra σ_cross 28,5 = **0,06σ**, menos resolución que el redondeo del propio agregador — justo la banda donde concentra el libro y donde `ca_conviction_tilt` hace su z. **P1: la búsqueda solo miraba a un lado.** 31 `confirms` contra 2 `contradicts`, y el scan no tenía **ni una** instrucción de buscar evidencia contraria (grep: cero menciones) — toda query se escribe alrededor de la tesis, así que recupera el corpus que le da la razón, y esa ausencia viajaba aguas abajo como si hubiéramos mirado. El scorer **ya tenía la maquinaria** (`contradict_max_points` 15 vs `confirm` 10; dos contradicts vivos → `breaking`) corriendo sobre un flujo al que nadie le pidió alimentarla. Nuevo **C2c**: una query por catalizador de `must_reverify` escrita para ROMPERLO, y el resultado se graba **encuentre o no** — `found: false` es la medición que separa «buscamos el caso corto y no está» de «nadie miró». **P2: `strength_score` era el último cardinal libre** y se comportaba como tal (16 activos en [55,91], σ=10, agrupados en redondos: tres 72, dos 80, dos 82). Ahora rúbrica de 5 preguntas OBSERVABLES + **anchors**: se nombra un evento del que es más fuerte y otro del que es más débil, y el score cae dentro. **El bracket es lo que persiste entre sesiones; el número absoluto nunca lo hizo.** **P3: el halflife era un SEGUNDO cardinal libre** que compone con el primero — decide cuánto tiempo sigue contando. La tabla por `catalyst_type` existe desde el principio y el scan la exigía en prosa; **6 de 16 la contradicen, 5 hacia MÁS persistencia**. **Lo que NO se hizo es la decisión de diseño:** se envía `stored`, no `taxonomy`, porque adoptar la tabla hoy movería el CA de una posición VIVA por la autoridad de una constante que seis reviews declinaron usar (cobre 38,9→4,0) — o los seis autores se equivocaron o `commodity_supply: 21` está mal, y adoptarla por defecto contesta eso **por decreto**, justo lo que CLAUDE.md prohíbe para el cap. Se publican las dos, el lint precia la discrepancia, decide el usuario. Lo que sí queda cerrado: el drift no puede CONTINUAR (schema 1.3 exige `halflife_override_reason`). **P4:** el nivel del strength se sustituye por su rango dentro de la población registrada, **range-preserving** (re-espacia, no re-escala; monótona, nunca reordena): |Δ| máx 5,8, y los tres «72» resuelven al mismo valor. **P5 `ca_logit`:** ×3,56 de resolución en el techo. **P6 `event_tape_z`:** la primera entrada que NO se lee de un documento — retorno anormal del vehículo tras el evento, en unidades de su vol PREVIA, firmado por lo que el evento afirma (un `contradicts` se confirma con una CAÍDA), ambas patas en EUR antes de restar (un benchmark USD contra vehículo EUR apuntaría el EURUSD como alfa — v8.1 un nivel más abajo). Ya nombra el evento confirmador del cobre (fuerza 78) con z **−1,47** sobre el propio cobre. **Dos defectos que destapó la implementación:** el mensaje de bracket invertido era **código muerto** (un bracket vacío falla una cota para cualquier score, así que el mensaje de cota siempre ganaba — y mandaba a cambiar el SCORE cuando lo mal puesto era el anchor); y `ca_logit` se leía del detalle, que retorna temprano en los dos caminos sin catalizadores, perdiendo la fila cuyo cero es **medido** (`infrastructure_core`). P5/P6 son columnas **peso 0**; un evento no confirmado se NOMBRA, nunca se degrada solo — que el mercado vaya tarde es la premisa del mandato. Schema `catalyst_event` 1.2→1.3, los 19 documentos siguen validando. 771 tests en verde (+36). |
| 2026-09-01 | v10.4 | **La señal se medía en cuatro divisas; el libro se cobra en una.** Cierra lo que v10.3 dejó abierto. **15 de los 26 vehículos puntuados no cotizan en euros** (10 USD, 4 GBp, 1 GBP): dentro de UNA serie la escala se cancela en un retorno, **entre series no**, y todo lo que ordena o mide compara series. **La pieza que más importaba no era el momentum, era el TARGET:** `calibration.forward_returns` leía cierres nativos, así que el IC preguntaba «¿el composite predice el retorno de un comprador en dólares?» cuando lo que se cobra es el retorno en euros — y de ese `fwd` cuelgan el rank IC que **gatea λ**, el scorecard contra la baseline HOLD, el tally del `ca_tilt` y el precio a 21d de cada override. Convertir solo la señal habría dejado los dos lados midiendo cosas distintas: peor que no tocar nada. **Cuatro consumidores, una implementación:** la conversión ya existía en `nav_engine` desde el primer día y se ha movido a `prices.py`, la capa que ya posee la caché **y** la divisa por ticker, así que la regla GBp×0.01 existe UNA vez (`to_eur`/`fx_series`/`read_eur`/`basis_by_ticker`); `nav_engine._to_eur` y `_default_fx_fn` quedan como delegados finos y el contrato inyectable `ccy_fn`/`fx_fn` no cambia. Migrados: `calibration.forward_returns`, `covariance._prices`, `comomentum` y `market_data.fetch_metrics`. **Medido, separando efectos en vez de sumarlos:** ρ semanal de los 26 vehículos **0,199 → 0,179 (−0,020)**; ρ del libro modelo de 6 nombres **0,3213 → 0,3238 (+0,0025)**, vol 18,63% → 18,40%; ranking de momentum **12 plazas, máx 2**, top-6 intacto. Dos lecturas que no se pueden saltar: **(1) la correlación BAJA al convertir, no sube** — la predicción intuitiva «un factor FX común eleva la correlación» es falsa aquí, la pata FX compensa parcialmente el co-movimiento —, y baja justo en la muestra de la que se leyó la rodilla de `n_target`; **(2) sobre el libro VIVO el efecto es inmaterial**, así que nada de la tabla de riesgo de hoy se explica por la divisa. Las dos son ciertas a la vez y decir solo una sería propaganda. **`n_target` NO se toca:** un ρ más bajo empuja hacia MÁS nombres, y mover el mando único en el mismo edit que cambia la base de medición destruye la capacidad de saber cuál de los dos cambios hizo qué — se publica el número y decide el usuario, misma regla que el cap nocional contra el presupuesto de varianza. **Una fila declara su base y una fila que no pudo convertirse NO dice EUR:** `return_basis` por fila, `momentum_engine` publica `basis_mixed` y **avisa cuando el panel mezcla bases** (diagnóstico, nunca puerta); las particiones previas se leen como `"native"`, porque «no medido» y «medido en euros» son afirmaciones distintas; y una columna sin FX **se queda nativa y lo dice** en vez de desaparecer del frame — perderla encogería en silencio la sección transversal sobre la que se rankea. **Confound de calendario fijado con test:** el FX se reindexa sobre las fechas de cotización del propio instrumento; un índice unión mete días en que cotizó la divisa y no el fondo, y ese artefacto **por sí solo duplicaba el efecto aparente** (26 plazas/máx 3 contra las 12/máx 2 reales) — mismo error de forma que el `_load_shares_series` de v10.2. 782 tests en verde (+11). |
| 2026-09-01 | v10.3 | **La divisa del vehículo se leía por defecto, no por lectura.** Hallazgo colateral de v10.2 + «esto tiene fix simple?». La lectura sí; la consecuencia no. `fetch_metrics` tomaba la divisa de `hist.attrs.get("currency", "USD")` y en esta versión de yfinance **`hist.attrs` viene siempre vacío** (comprobado en 6 tickers): las 26 filas de momentum decían `USD` **por defecto, no por lectura** — y ésa es justo la comprobación en la que se apoyó v8.1 para cazar que `IH2O.L` cotiza en GBp mientras el operador paga en EUR. Llevaba muerta en silencio desde entonces. **Arreglo:** `Ticker.history_metadata` trae `currency` en el MISMO response del chart (cero red extra) y distingue `GBp` de `GBP`/`EUR`/`USD`; si no se puede leer se escribe **`None`, nunca `"USD"`** — convertir «no medido» en un valor es el defecto que ocultó éste, misma doctrina que v6 H2 con un estudio ausente. **Lo que la columna restaurada deja ver, medido: 15 de los 26 vehículos puntuados NO cotizan en euros** (11 EUR, 10 USD, 4 GBp, 1 GBP). Dentro de UNA serie la escala se cancela en un retorno — por eso este edit no mueve ni un número, y hay test que lo fija —; lo que no se cancela es el FX ENTRE series, y el compuesto ordena 26 sectores medidos en cuatro divisas. v10.1 ya lo había medido en un caso: mismo fondo, mismo día, 1y **+28,24% (EUR) vs +24,62% (USD)**. **Tamaño del sesgo, medido** convirtiendo las 26 series a EUR con EURUSD/EURGBP **reindexado sobre las fechas del propio ETF** — un índice unión mete días en que cotizó el FX y no el fondo, y eso solo ya duplicaba el efecto aparente (26 plazas / máx 3 antes de alinear, **12 plazas / máx 2** después): el top-6 no se mueve; los cambios reales son `gold_miners` 11→9 y `copper_miners` 9→11. Segundo orden frente al error de identidad de vehículo de v10.2 (7 plazas él solo) — pero `gold_miners` está en cartera. **Lo que NO se hace aquí:** convertir los retornos a EUR cambia la BASE de todas las series de momentum del lake, así que las filas de antes y después dejan de ser comparables — la misma rotura de escala que el `composite` pre-schema-1.4, que allí exigió nota de migración y detección de escala —, y necesita serie FX cacheada por run y alineación de calendario por ticker. Queda como **decisión abierta del usuario** con la tabla delante, no como efecto colateral de arreglar una lectura. 735 tests en verde (+7). |
| 2026-09-01 | v10.2 | **El vehículo medía otro sector, y el CMF se comía el dato que lo habría arreglado.** Salió de «el xaix de data centers no ha podido calcular bien el flow» + «he buscado en revolut y tengo un v9n, son equivalentes?». **No lo son, y no es de matiz:** `XAIX.DE` es Xtrackers AI & Big Data ($8.556M) — 70,1% tech, **0% real estate**, top-10 con Walmart y Bank of America —; `V9N.DE` es Global X Data Center REITs ($342M) — **53,4% real estate**, EQIX 13,3 / DLR 12,3 / AMT 10,2 / CCI 6,8. **Solape en el top-10: cero.** El sector se llama `ai_infrastructure_data_centers` y lo puntuaba un fondo sin un solo data center, cuya beta ya estaba dos veces en el libro (cloud rank 1, semis rank 4). V9N.DE a tier-1 y a `chain[0]`, XAIX.DE baja a tier-2 con la razón escrita; identidad verificada contra yfinance como exige el universo desde `IQQR.DE`. **Coste medido del vehículo equivocado** (contrafactual sobre `run_20260831_184616`, baseline reproducido EXACTO 26/26): momentum **67,3 → 36,5** (3m −13,9% vs −4,8%; 6m +12,1% vs +35,8%), **rank 2 → 9**, composite 60,5 → 53,4 — o sea, **el BUY de €1.500 aprobado se apoyaba en un rank que lo ganó otro fondo**. Ningún `Movement` lo referencia: la orden nunca se ejecutó, no hay base de coste que tocar. **El defecto que destapó vale más que el cambio de ticker.** El flujo no fallaba por ser XAIX UCITS: la cadena `→ AIQ` **sí** devolvía shares (158,45M), solo faltaba la lectura ANTERIOR → `kind: baseline` → CMF. Y al entrar el CMF, `fetch_flow_data` sobrescribía `flow_proxy_ticker` con el vehículo de OHLCV y `flow_to_rows` buscaba `etfs["XAIX.DE"]` en vez de `etfs["AIQ"]`: **el baseline se tiraba a la basura en cada run**, así que el siguiente empezaba de cero — CMF permanente por construcción (`2026-08-31 | XAIX.DE | shares=NaN`). **No era un sector: eran seis de los ocho en CMF** — AIQ 158,45M, SLV 545,85M, GLD 352,40M, ICLN 125,60M, DAPP 17,25M, MOO 12,30M, todos devolviendo conteo real que el run descartaba; solo `eu_defense` y `luxury_goods` carecen de verdad de hermano US. Los 13,1 puntos de error medio del CMF que midió v6.9 se pagaban en seis sectores que no lo necesitaban. **El arreglo separa las dos preguntas que la fila mezclaba:** `flow_proxy_ticker` = de qué vehículo se leyó la señal (bajo CMF, el de OHLCV); el nuevo `shares_ticker` = a quién pertenece el conteo — el único de los tres campos correcto en una fila CMF, y por eso `_load_shares_series` también casa por él. Columna aditiva (`union_by_name` / `pd.concat`), las particiones viejas se leen igual. **El score sigue siendo el del CMF y sigue marcado `volume_proxy`:** preservar el baseline no convierte una aproximación en medición — la medición llega al run SIGUIENTE, porque la serie exige la lectura previa estrictamente anterior a hoy. **Efecto verificado end-to-end sin escribir al lake:** data centers pasa a flujo REAL hoy mismo (SRVR, −0,805% → 43,6) porque SRVR sí tiene historia — la tuvo hasta el 2026-07-05 y el cambio de vehículo de universo v2.0 la había roto; gold/silver siguen en CMF este run (correcto) pero ya persisten `shares_ticker=GLD/SLV` y gradúan en el siguiente. **Hallazgo colateral NO corregido:** `fetch_metrics` lee la divisa de `hist.attrs.get("currency", "USD")` y en esta versión de yfinance `hist.attrs` viene **siempre vacío** — las 26 filas de momentum dicen `USD` por defecto, no por lectura, y ésa es justo la comprobación en la que se apoyó v8.1 para cazar el GBp de `IH2O.L`. Merece su propia entrada. 728 tests en verde (+3). |
| 2026-09-01 | v10.1 | **UCITS no es lo que hace comprable a un vehículo; el KID de PRIIPs sí.** Salió de «veo que oro no tiene asignado etf, en Revolut veo PPFB». Las dos mitades eran ciertas y la segunda explica la primera: `gold_physical` y `silver_physical` imprimían `—` en la columna ETF, y no era display — `portfolio.py:576` filtra `primary_etf.notna()`, así que **estaban excluidos de la construcción del libro**, con `silver_physical` rankeando **10º** sin poder recibir un euro. **Causa: una sobrecorrección de la propia doctrina de comprabilidad.** El 08-28, `primary_etf` cayendo al pool no-UCITS imprimió `BUY biotech €891` contra **IBB** (US, imposible bajo PRIIPs); el arreglo fue exigir `ucits: true`. Y `ucits` no es el discriminante: un fondo de UN SOLO METAL **no puede** ser UCITS — la diversificación lo prohíbe —, así que todo el oro y la plata físicos de Europa son ETCs, llevan KID y cotizan en Xetra/LSE. El flag juntaba «US, sin KID, ilegal aquí» con «nota europea perfectamente comprable», y el universo v2.0 **ya no tiene ni una entrada US**: el filtro defendía de una amenaza ausente del fichero mientras tiraba 2 de 26 sectores investables. **El arreglo filtra por VENUE** (`PRIIPS_VENUES`), que es la garantía que el parche del 08-28 buscaba: un `IBB` que se declarase `ucits: true` sigue fallando, y hay test que lo fija. **Vehículo:** `PPFB.DE` (iShares Physical Gold ETC, EUR, Xetra, verificado contra yfinance) a tier-1 y a `chain[0]`; `IGLN.L` baja a tier-2 sin borrarse. Mismo ETC, y es la línea que el buscador devuelve — misma regla que v8.1. La escala se cancela en un retorno, **la divisa no**: mismo fondo, mismo día, 1y **+28,24% (EUR)** contra **+24,62% (`IGLN.L`, USD)**. `4GLD.DE` sigue tier-2 a propósito (bono al portador alemán, fiscalidad distinta). **Lo que NO cambia:** el run ya grabado no se re-persiste — sus overrides se firmaron contra esa tabla y `primary_etf` se escribe en el snapshot, así que el criterio aparece en el PRÓXIMO score run en vez de recortar por detrás una tabla decidida; ninguno de los dos está en cartera. Test nuevo que vale por sí solo: **el vehículo que se puntúa es el que se recomienda** (`SECTOR_TICKERS[sid][0] == primary_etf(sid)`) — el defecto «puntuar COPX y comprar 4COP» de la v2.0, un nivel más abajo. 725 tests en verde (+16). |
| 2026-09-01 | v10.0 | **El libro solo tenía una manera de ser pequeño: fallar.** Salió de dos frases del usuario: «si bajo catalizadores o riesgo hay sectores que dejan de ser interesantes deberíamos eliminarlos, no ir a por los n primeros» y «hay que incluir el riesgo y la concentración como métricas para recomendar rebalanceos». Todo mecanismo capaz de encoger el libro desembocaba en un *shortfall* que la persistencia reabría cada review; no había forma de decir «este sector dejó de merecer la pena», «cuatro nombres es el número correcto» ni «quiero caja hasta X» que sobreviviera un ciclo. **La mitad ya estaba construida** (`min_momentum`/`max_crowding` ya absolutos, `deploy_ratio` ya modela caja, y `sell_if_any.catalyst_status: [invalidated, dormant]` ya es salida DIRECTA por driver muerto). **P1 puerta de convicción:** `min_composite_z: 0.0` es RELATIVO — «mejor que la media de este run» —, así que en un universo que se apaga sigue admitiendo a la mitad del campo; un z-score ordena, no opina sobre si lo ordenado merece poseerse. `conviction_gate()` lee `catalyst_alignment` (absoluto, y ya el mayor peso del compuesto, 0,35). **El suelo 40 se LEE de la distribución**: los 26 CA son bimodales — veinte entre 71,5 y 96,2, hueco de 25 puntos, luego 36,2/13,9/13,9/0,0, justo los sectores sin driver vivo. No mueve la tabla de hoy (los cuatro rankean 17º o peor) y ése es el punto: enuncia el criterio sin recortar en silencio una tabla ya decidida. Para el caso que aún no pasó — `eu_retail_banking`, momentum 86,5 con CA 13,9, a un trimestre del techo rank-8 de `buy_if`. Un CA **imputado no se juzga** (convertir «no medido» en «suspende» es lo que v5/v6 quitaron); un **cero medido sí**. **P2 el shortfall mide contra lo que el libro PUEDE sostener:** con techo 20%, cuatro nombres topan en 80% y tres en 60%, así que un libro que las reglas cortan a cuatro se medía contra un objetivo inalcanzable por construcción — infactible, incumplido siempre, ignorado después; el mismo fallo que v6 L2 por el otro lado. `absorbable_eur()` baja el listón a `min(deployable, Σ target)` y **no es escondite**: el techo es aritmética del cap, `shortfall_vs_deployable_pp` nunca sale de la página, y `cash_drag` sigue preciando cada euro ocioso. **P3 postura de caja:** un override normal (log, autor, razón, preciado a 21d contra el benchmark que declinó) que responde a una regla de LIBRO y **caduca a 90d** — el silencio es lo que la regla existe para romper. `breached: false` con `breach_raw: true`: la brecha no se borra, se contesta, y se reabre sola. Autores máquina no pueden escribirla. **P6/P7 riesgo en la tabla:** el párrafo del cap terminaba desde v6.4 en «replacing the notional cap with a risk budget is a decision for the user» — tomada. `max_cluster_variance_pct: 35` NO sustituye al nocional: ambos `warn` un ciclo, y la evidencia decide. `cluster_risk_pass()` es post-pase (el riesgo de un cluster es propiedad del libro POST-trade, que no existe hasta que la primera pasada lo produce), bloquea ADD/BUY sobre presupuesto, nombra candidato a TRIM, **nunca toca SELL/REDUCE** (un límite que cancela una retirada de riesgo discute consigo mismo) y **un riesgo no medido jamás cuenta como cumplido**. **El defecto que destapó, que vale más que la función:** el candidato a TRIM salía del libro POST-trade, así que podía recortar una posición cuya compra acababa de bloquear — ahora exige posición realmente tenida y no bloqueada en la misma pasada. **Lo que dice hoy:** `ai_capex` incumple ambos (62,5% nocional, **89,2% de la varianza**), pero `energy_transition_grid` **cumple el nocional al 15,0% cargando el 44,6% de la varianza** — las reglas discrepan sobre un driver que el cap declara limpio, que es exactamente la evidencia pedida. **Lo que se decidió NO hacer, con causa:** la pata «`blind` >N días bloquea un BUY» se descartó — `_data_age_by_sector` fija por escrito la doctrina contraria («stale data is a reason to RE-VERIFY, not to stop acting»), y revertirla en silencio dentro de un edit sobre otra cosa sería justo el cambio que este proyecto registra para no repetir. Queda como decisión abierta. **P8 — el score de catalizadores DIMENSIONA el libro (`ca_conviction_tilt`), como PRIOR DECLARADO.** Salió de «para el rebalanceo no se usa nuestro score de catalizadores, y es el CORE» + «quiero darle un peso aunque no lo tengamos calculado… si no, no tenemos nada que medir». CA elegía los NOMBRES (0,35 del compuesto → rank; puerta P1; status → SELL) pero con λ=0 no ponía un euro de diferencia entre ellos — y el argumento de esperar al IC tiene un fallo causal: **un tilt que nunca mueve euros no genera jamás la serie que lo calibraría**. Mecanismo: `w ← w·(1+δ·z_CA)` entre `skill_shrink` y `vol_tilt` (convicción antes que comparabilidad-en-riesgo; el cap manda último), z entre los nombres SELECCIONADOS, winsor ±2,5, CA imputado a z=0 y fuera de los momentos; solo libros composite — momentum/equal son baselines y se quedan limpios. **δ=0,20 no se inventa:** mapea el winsor sobre los `tier_multiples` YA declarados (+2σ→×1,4, −2,5σ→×0,5 — los tiers que ningún código leía). Lo que lo mantiene honesto es un LIBRO MAYOR, no una puerta: cada build persiste el contrafactual δ=0 exacto (`w_pre_ca_pct`, ambos libros recorren idéntico vol_tilt→cap→deadband) y `calibration --ca-tilt` precia `Σ(w_post−w_pre)·fwd` por ventana completa de 63d, descompuesto por sector y por DRIVER (crédito solapado, la regla de `exposure_eur`). **Doctrina del usuario, en el código: CA nunca se apaga** — `ca_tilt_delta_min: 0.05` hace `delta: 0` inescribible; un tally negativo NOMBRA el catalizador cuyo cálculo desvió el sizing (indicadores, decay, noisy-OR, frescura), jamás el interruptor; mover δ es edit congelado con el tally delante. Efecto vivo (model book, sin re-persistir el run ya decidido — sus overrides se firmaron contra la tabla pre-tilt): los CA 95–96 amplifican ×1,06–1,12 y `cybersecurity_commercial` (CA 79, z −2,23) cae ×0,55 → target 17%→10,2%, su ADD degrada a HOLD. La tabla gana la columna `caZ` junto a `rk` y la línea `CA-TILT δ` bajo `TILT λ`. 709 tests en verde (+14). |
