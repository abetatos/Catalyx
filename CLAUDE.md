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
  `broker_absent` (v14.0) = **medido**: instrumento válido que el bróker NO lista — se conserva
  (vuelve intacto si aparece) y `primary_etf` lo excluye, porque el venue es una HEURÍSTICA de
  comprabilidad y esto es la MEDICIÓN. Falsar un `assumed` no retira el sector si el FONDO sigue
  comprable en otra plaza: `BTEC.L` → `OM3E`/`NBTK.DE` (v14.1) es el caso, `cloud_software_saas`
  (ningún vehículo en ninguna plaza) es el otro.

**Un catalizador = un driver económico.** Dos catalizadores que suben y bajan por la misma razón
cuentan doble en `catalyst_alignment` y burlan el `correlated_catalyst_cap` (que existe justo para
eso). Un driver puede golpear varios sectores — para eso está `affected_sectors`; lo que no puede
haber es el mismo driver modelado dos veces. (Una guía y su realización —el Fed que avisa en junio y sube en septiembre— son eventos SECUENCIALES, no el mismo driver dos veces; el doble conteo real lo vigila `catalyst_audit` como `same_driver_stack`.) Al fusionar: el superviviente conserva su `id` (lake y
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
**And it is an ENTRY test, never an exit one (v12.1).** Crowding prices what the NEXT buyer pays
into a theme; a holder already paid the old price and the crowd is what is paying them. It also
RISES WITH SUCCESS — a theme becomes crowded by working (`pharma_large_cap`: composite 28.5 → 80.5
while crowding went 55 → 94.4) — so `max_crowding` applied to a held name sells winners for having
won, and sells them twice on one metric: the composite already charges crowding at 0.135, and
pharma out-ranked that penalty to **4th of 26** before a binary veto killed it. The fix is NOT a
higher bar — only two sectors sit above 90, so an exit bar at 95 would not sharpen the rule, it
would delete it, and picking the number that produces the wanted answer is what the correlated cap
already forbids. `portfolio` publishes `crowding_excluded` as a **counterfactual** (listed only if
lifting the bar would ACTUALLY have selected the name — so pharma is listed and biotech, same
94.4, is not), the model book still excludes them (it must stay reproducible from a run alone or
its NAV stops being comparable to the real book's), and `rebalance.decide_action` suppresses
**only** the overweight TRIM. Every other exit still reaches the position — dead driver, `breaking`
regime, exit watcher, rank-out, profit ladder. `_crowding_excluded` **fails toward the sale**: an
exception returns `{}` and the TRIM fires, because a mechanism that cancels a sell must fail by
selling.
**A rotation is priced on the trade; `book_risk_delta` prices the BOOK it leaves (v12.1).** Every
row cost — CGT, spread, breakeven — is a cost of trading, so a table could sell the only holding
that diversifies and buy into the cluster already carrying the variance with no number moving.
Measured weekly over 56w: exiting pharma leaves ρ 0.2215 / vol 19%; keeping it leaves ρ 0.1883 /
vol 16%. The `BOOK RISK` block reports vol and mean ρ before vs after, over the rows that would
EXECUTE today (a ramp- or budget-queued row is not part of today's book). Column, not gate — it
cancels nothing; `cluster_risk_pass` is what acts on risk, and only on the buy side. Unmeasurable
returns **None**, never a comforting zero.

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

**A ranking with no denominator is not a decision rule (v13.3).** The table printed
`breakeven_pct` — real money — beside a rank, which is a position in a dimensionless ordering, and
nothing converted one into the other; "rank 8 → sell" was a verdict with no units. The bridge is
the same arithmetic λ rests on: `E[Δr] ≈ IC · Δz · σ_cs`, so **`Δz_required = breakeven ÷ (IC ·
σ_cs)`** (`rebalance.swap_hurdle`, σ_cs from `calibration.dispersion`). This is deliberately NOT a
threshold — it falls with the row's own friction, rises as measured skill or dispersion fall, and
**goes to infinity when the IC stands aside**, so with no established edge no rank-based rotation
is justified. It also produces, for free, the asymmetry nobody has to impose: a buy from cash
(~0.20% friction) is ~13× cheaper to justify than a rotation (~2.57%, CGT included). Today the
project runs two philosophies at once — SIZING stands aside for want of edge (λ=0) while SELECTION
rotates at full conviction — and this is the seam. **It is a COLUMN, not a gate**, for the reason
v7 gives every new signal: promoting it is a policy change on evidence that does not exist yet
(the rule scorecard has no complete window). Fundamental exits — exit watcher, broken regime, dead
driver, drawdown, rank-out STREAK — are about the POSITION, not a marginal gap, and stay outside
the hurdle even if it is ever armed. `pays` is three-valued: `None` is "not measured", never a
soft no. **And with λ=0 the model's whole contribution is the CUT**, so `selection_margin` and
`rank_size_spearman` print how close it was (2026-09-01: **+0.025 composite_z**, ρ **+0.03**) — a
number that is assumed and never printed is the kind that goes wrong quietly.

**A scarcity that queues a row must not queue the rows behind it (v13.3).** The deployment ramp
stopped filling at the first row too big for the tranche, on a written rationale ("not a knapsack
that would skip the leader") that does not apply: a row that does not fit is deferred either way,
so continuing down the ranking displaces nobody and only stops discarding the tranche already
authorised. Live, that abandoned €1,059.67 of its own headroom while two rows that fit sat in the
queue. Rank order stays ABSOLUTE (never skip a leader to make room) and grants stay full size.
Separately: `max_step_pp` (15pp) is smaller than `max_position_pct` (17.5pp), so a full-size line
can exceed a whole tranche — that row is not "next review", it is **never**, and the ramp NAMES it
(`unfittable_rows`, `**` on the action) rather than resolving a frozen constant by decree. The
test runs AFTER the sells, because a sale genuinely enlarges this review's tranche.

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
0/1 /catalyx-scan             macro front door: C0 context · C0b TRIAGE (every due central-bank decision
                              + decided C0 fact + market move gets a row) · Pass 1 discovery (no taxonomy)
                              · Pass 2 refresh of every catalyst ON THE WORK LIST → scan_deltas_<date>.json
                              + scan_triage_<date>.json. The scan ENDS with `scan_triage check` green;
                              no delta is applied while it is red
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
| deployed | **63%** against a 75% rule | shortfall **11,9pp** (2026-09-01: ejecutadas las 4 filas, €4.236; 3 de ellas encoladas por el ramp → override autor `user`) |
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
      crédito/exposición en el model book (J3); derivación escrita de tiers + `line_risk_pct` (I5);
      sensibilidad por constante (I4); percentil destendenciado (J4). (**I3 ya está entregado** —
      `vol_tilt_alpha: 1.0` con la derivación escrita en la config; esta lista se quedó stale y lo
      recogió la auditoría v12.) K1–K5 bloqueados por datos, no por código.
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
- [ ] **v12 — `sector_impact` está construido y vacío: 12 eventos directos sin dirección
      declarada, en 11 sectores.** El campo existe (schema 1.4) y la ausencia se reporta
      (`direct_events_direction_unstated`), pero nadie lo ha rellenado, así que el canal de
      eventos directos sigue asumiendo «supportive» en 12 casos — y los CA de **14,3** de
      `eu_retail_banking` y `oil_majors_integrated` se sostienen ENTEROS sobre uno solo de ellos
      (`cat_20260617_fed_hawkish_pivot`, un Fed hawkish que difícilmente apoya a ninguno de los
      dos). No se rellena de golpe ni por inferencia: la dirección de un evento sobre un sector
      se contesta con la evidencia del evento delante. **El skill ya lo pide** — C3/C4 para
      eventos nuevos, C2b backfill solo de los que un hallazgo TOQUE —, así que esto se cierra
      solo a lo largo de los próximos scans; los que nadie toque mueren por decay, igual que la
      rúbrica de `strength_score` de v11. Lo que queda es correr el scan.
- [ ] **v12 — `n_target` es ahora una decisión MÁS urgente, no menos.** A la razón de v10.4 (ρ
      semanal cayó 0,199 → 0,179 al medir en EUR, y de esa muestra se leyó la rodilla) se suma
      que B3 acaba de cambiar la escalera que cuelga del mando: `deploy_max` 0,85 → 0,75 y los
      tiers 20/14/7 → 17,5/12,5/6,2. Un ρ más bajo empuja hacia MÁS nombres, y con `n_target: 7`
      el peso neutral bajaría a 10,7% y el techo a 15,0%. Sigue sin tocarse a propósito — mover
      el mando en el mismo edit que corrige su derivación destruye la atribución del efecto —
      pero recalcular la rodilla con la serie EUR es el siguiente paso.
- [ ] **v12 — `buy_if.rank_max: 5` ya NO es gratis: el 6º puesto del libro es incomprable
      (evidencia nueva 2026-09-01, v14.0).** La pregunta original era si `buy_if` debe ser
      ESTRICTAMENTE más fuerte que `add_if` también en rank — está en **5** = `add_if.rank_max`,
      el mínimo que quita la contradicción con el comentario de su propia regla, y «no costaba
      nada» porque las compras de aquel día estaban en rank 1, 2 y 4. **La retirada de cloud lo
      cambió de dirección.** El 6º puesto del libro modelo lo ocupaba `copper_miners`, que está
      HELD — la regla nunca tuvo que COMPRARLO, así que el techo no mordía. Ahora lo hereda
      `biotech_drug_development`, no held, con un target de **€1.265,87**, y la tabla emite
      **HOLD** («gap open but no rule condition met»): `rank_in_portfolio` 6 contra un techo de 5.
      **Con `n_target: 6`, el último puesto del libro solo es comprable si ya lo tienes** — lo que
      convierte el techo en una regla que congela la composición en vez de exigir un caso más
      fuerte. El shortfall subió **48,68 → 54,33pp** por esto. Las tres salidas: subir `buy_if` a
      **6** (lo hace alcanzable en todo el libro, pero entonces buy deja de ser más estricto que
      add en rank — vuelve la contradicción que v12 B4b arregló), **bajar `n_target`**, o
      **dejarlo** y aceptar que el 6º slot solo se llena por promoción de un nombre ya held. Es
      edit de umbral congelado; sigue sin haber evidencia de que el rank 5 sea peor que el 6, y
      elegir el número que produce la respuesta que quiero es lo que el cap correlacionado
      prohíbe. Lo que SÍ es nuevo es que ahora hay un precio en la mesa.
- [x] ~~**v13 — ¿sobre qué BASE se aplica `max_position_pct`?**~~ **CERRADO 2026-09-01 (v13.2)**,
      y no era una decisión de umbral: era un bug de conversión. La constante NO se ha tocado —
      17,5 sigue siendo 17,5% del capital TOTAL, derivado de `n_target`. Lo que se corrige es que
      `water_fill` lo aplicaba sobre pesos en base DESPLEGADA. `book_shape()` publica
      `max_position_pct_deployed` = `tier_multiples[0] × 100/n_target` = **23,33%** (`deploy_max`
      se cancela, así que el techo en esa base no se mueve con el ratio vivo), `portfolio.cap_frac`
      convierte, y un perfil declara su base con `max_position_basis` — sólo `catalyx` lleva
      `total`, porque es el único con techo derivado. `close_target_weights` tenía el mismo error
      aguas abajo. Medido: dispersión 2,08 → **9,99pp**, vol 16,16 → **14,94%**.
- [ ] **v13.3 — el listón de swap es COLUMNA; armarlo es decisión del usuario, y hoy no hay con
      qué.** `Δz_requerido` imprime ∞ en todas las filas porque el IC está apartado (−0,143, 1
      ventana de 3). Necesita TIEMPO: ≥3 ventanas independientes de 63d y un IC positivo por
      encima de 0,20, exactamente la misma puerta que el veto after-tax. Cuando llegue, la
      pregunta concreta es si el listón **bloquea** una rotación o sólo la reporta — y la
      respuesta por defecto de este fichero es que una regla nueva viaja a peso 0 hasta que la
      evidencia hable. Mientras tanto la columna no distingue filas (∞ para todas); empieza a
      informar el día que el IC se arme.
- [ ] **v13.3 — el `composite_drift` no es una regla, y probablemente debería serlo.**
      `decide_action` lee nivel (rank), régimen y exit watcher; **nunca la dirección**. Una tesis
      puede caerse 23 puntos con el rank intacto y ninguna regla lo ve — `cybersecurity_commercial`
      **77,1 → 54,0**, el peor drift del libro, mientras la tabla lo vendía por «rank 8». La
      columna ya se publica. Convertirla en salida es una regla nueva: necesita un umbral (¿−20
      puntos? ¿−1σ del drift del universo?), y sobre todo necesita no duplicar lo que el exit
      watcher ya hace. **Es la decisión que hace falta ANTES de armar el listón de swap**: una
      zona de indiferencia por rank sin regla de deterioro calcifica el libro de verdad.
- [ ] **v13.3 — `max_step_pp` (15pp) < `max_position_pct` (17,5pp): una línea a tamaño completo
      puede no caber en NINGÚN tramo.** Hoy no muerde (la venta de cyber agranda el tramo y
      `water_infrastructure` entra), y el ramp ya lo NOMBRA en vez de esconderlo como un
      diferimiento más. Tres salidas y ninguna es gratis: **(a)** subir `max_step_pp` a ≥17,5 —
      umbral congelado, y es el mando que limita la mayor apuesta de timing del sistema;
      **(b)** permitir llenado PARCIAL sólo para filas estructuralmente inencajables, con suelo
      anti-colilla (≥50% de la petición) — rompe «a tamaño completo, no seis cuartos de posición»;
      **(c)** dejarlo nombrado y no arreglarlo. Está en (c) a propósito: resolver un conflicto
      entre dos constantes congeladas eligiendo la que da la respuesta que quiero es justo lo que
      el cap correlacionado prohíbe.
- [x] ~~**v14.1 — el spec oficial de momentum se aplica a 11 de 26 sectores, y encoge sola.**~~
      **CERRADO 2026-09-02 (v14.3)**, y la premisa era correcta pero el arreglo NO era la
      ventana de fetch de `fetch_metrics`: era que `market_data` fuese la única fuente de
      precios que se saltaba `prices`. El cache compartido YA tenía 400 días para los 26
      vehículos, así que la cobertura pasó de **12/25 a 25/25 sin una llamada de red nueva**.
      Medido dos veces (contrafactual offline y run grabado, coinciden): 19 de 25 sectores se
      mueven, máx 8 puestos, Spearman 0,90, y el top-6 cambia de composición. Con él viajó el
      acoplamiento que lo habría estropeado: `high_52w` era el máximo de la ventana TRAÍDA, no
      de 52 semanas. Lo que deja abierto está justo debajo.
- [ ] **v14.3 — `_safe_return` cuenta BARRAS, no fechas, y el ranking es transversal.** El ancla
      de «252 barras atrás» cae en un rango de **13 días** entre sectores (2025-08-21 `GLUX.SW`
      ↔ 2025-09-03 `IUHE.AS`; a 63 barras el rango es 3 días), así que las filas que se comparan
      entre sí no miden exactamente la misma ventana. Es el MISMO mecanismo que acaba de costar
      media cobertura —indexar por barra una pregunta que es de calendario—, solo que en segundo
      orden: 13 días sobre 12 meses, y con la ventana ya profunda ninguna fila se cae por ello.
      **No se arregló en v14.3 a propósito:** anclar por fecha mueve TODOS los retornos de
      TODAS las filas, o sea es otro re-rank del universo entero por una razón distinta dentro
      del mismo edit, y eso destruye la atribución del que ya se hizo. Se mide primero (correr
      el score run con las dos anclas y comparar ranking) y se decide con la tabla delante.
- [ ] **v14.3 — 9-1 vs 12-1 sigue abierta, y ahora por fin es contestable.** Era la pregunta que
      la nota de v14.1 aparcó por detrás de la cobertura, con razón: discutir el horizonte
      cuando el spec alcanzaba al 48% del universo era discutir sobre 12 filas. Con **25/25** en
      `12_1_52w` la comparación ya es sobre el universo entero. Sigue siendo un cambio de spec
      PROMOVIDO CON BACKTEST (v8 Q1, 176 meses, 12-1 IC +0,104 en los tres subperíodos), así que
      moverlo pide evidencia del mismo orden, no una corazonada — y el reparto 0,635/0,365 del
      blend está calibrado sobre 12-1, no sobre 9-1. Nada urgente: el spec vigente ya no está
      roto, solo era invisible.
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
| 2026-10-19 | v15.4 | **Una posición se mantiene por una razón, y no siempre es un catalizador.** `water_infrastructure` estaba al 100% en `struct_ai_capex_supercycle` solo porque no existe otro driver; medido (56 sem EUR) carga β 0,15 sobre esa cesta contra 1,39 de semis, y está en el libro para BAJAR varianza. Esquema de movimiento **1.4**: `reattribution[].role` = `catalyst` (defecto) \| `diversifier`. Un diversificador **no se vende por la racha de rank-out** y fuera del libro modelo tiene «sin opinión» (objetivo = lo que tiene, con techo) en vez de 0; exit watcher, régimen, driver muerto, drawdown, techo y escalera siguen. Gana la última entrada que DECLARA rol; solo cuenta el episodio abierto (cerrar y reabrir vuelve a `catalyst`); un valor desconocido o un fallo de lectura caen hacia la regla que VENDE. Columna `diversifier_health` (ρ y β contra el resto del libro, ±se, cuota de varianza vs de capital): no vende, y no hay umbral — la salida «ya no diversifica» la hace el plan de best moves. **La puerta CA de entrada NO se aflojó:** reproducción de turnover 32 sem con la Σ de cada fecha — con puerta 4 patas y ninguna en 7 meses; sin ella 6–8 con ping-pong entre pharma/grid/oil/agri. Water etiquetada por el usuario (atribución SIN tocar: moverla a `uncatalyzed` relajaría el cap nocional por relabel). 1091 tests. |
| 2026-10-19 | v15.3 | **Best moves: cada operación posible en una sola moneda (columna, peso 0).** `net = Δ(β·ERP) + Δ(IC·z·σ_cs) − δ/2·ΔVar − (CGT + spread)` en € a 63d, δ = ERP/σ²(SPY) (Black-Litterman), Σ semanal EUR Ledoit-Wolf; plan voraz con re-precio tras cada paso, ±se y t por movimiento (no elige, solo dice cuánto fiarse). **RISK PRICE:** el `max_cluster_variance_pct` 35% pasa de aviso a PRECIO — la misma utilidad a δ·k, frontera por k, `k_star` o `closest_k` (la cuota de cluster no es monótona en k); k=2 es el punto de referencia aceptado (copper→pharma + semis→grid: vol 11,2→8,8%, ai_capex 73→51%, ≈−€25/63d). Las operaciones extra son PRECIO (`extra_trade_fee_eur` 1 € = SUPUESTO), no muro; destinos con suelo `min_composite_z`. Persistido en `rebalance_best_moves` + panel del dashboard. Además IUS2.DE `verified` (visto en Revolut) y DFCU.DE `broker_absent` → `us_defense` watch-only. |
| 2026-10-18 | v15.2 | **Comprar arriba, mantener hasta que la regla de venta diga lo contrario.** El corte 6/7 del libro modelo (0,02–0,12 composite_z) mandaba más que la regla `rank_out_of_top` × `rank_out_consecutive`: un nombre fuera del libro tenía objetivo 0, leía «sobrepeso máximo» y se vendía entero el primer ciclo. **Hold zone:** un held fuera del libro modelo conserva objetivo = min(actual, techo) mientras su racha de SELL esté bajo `rank_out_consecutive`; **regla de hueco:** un BUY de línea nueva solo llena una línea libre (< `n_target`). No suprime exits fundamentales, techo ni escalera; sin umbral nuevo. |
| 2026-10-17 | v15.0 | **El Fed subió el 09-16 y el pipeline no registró nada; solo existió porque el usuario lo señaló.** Cuatro causas: (F1) el heartbeat llevaba muerto desde el 09-04 — launchd no tiene `~/.local/bin` en el PATH, `uv` daba rc 127 y `heartbeat.sh` solo hablaba con rc 10, así que imprimió «quiet» once veces; (F2) una decisión con fecha pública no estaba vigilada en ningún sitio; (F3) C0 LEYÓ la subida y la usó como evidencia del oro sin decidir qué era ella misma, y la regla de novedad la trataba como «mismo driver» que el evento de junio (que pesaba ~6%: el decay se ancla en `event_date`); (F4) el scorer no lee `affected_sectors`, así que un evento registrado puede pesar sobre ningún sector. Arreglo en seis fases: scripts resuelven `uv` y un check roto alerta; `macro_calendar.yaml` (Fed/BCE/BoJ/BoE) + alerta de decisiones sin triar; **triaje obligatorio C0b** (`scan_triage_<date>.json` + `scan_triage check`, el scan no termina en rojo); regla de novedad «mismo hecho ≠ mismo driver» + avisos `reinforced_but_decayed`/`same_driver_stack`; `event_not_linked` + `declined_catalyst_ids` (sector_study 1.4); disparadores de mercado `^TNX`/Brent/drawdown con umbrales MEDIDOS a 10,8 años (el 10y sube de 25 a 40pb: 25 disparaba el 24,5% de los días). **Ni un peso, ni un umbral congelado, ni los scorers.** Pendiente del próximo scan, no de código: triar las 4 decisiones de septiembre (BoE/BoJ nunca se revisaron) y resolver 16 pares evento-sector sin enlazar. |
| 2026-09-02 | v14.3 | **El spec oficial de momentum llegaba al 48% del universo, y lo decidía el calendario de la plaza.** Observación del usuario: «la prioridad real es la COBERTURA, no el horizonte». El run vivo corría con **12 de 25** sectores en `12_1_52w`. La causa es una resta: 12-1 necesita `return_1y_pct` y `_safe_return(closes, 252)` pide **253 BARRAS**, pero `fetch_metrics` pedía `period="1y"` = 365 días **de calendario**, y cuántas barras da eso es propiedad de la PLAZA, no del fondo — medido: **Xetra 251-252 · SIX 247 · LSE 252-254 · Euronext 255**. El corte caía por el medio. Ningún sector estaba en el fallback por falta de historia. **Y el sesgo se agravaba solo:** las migraciones por realidad del bróker mueven vehículos HACIA Xetra (`BTEC.L`→`NBTK.DE`, `SEMI.L`→`SEC0.DE`), así que cada corrección hacia un vehículo más comprable ENCOGÍA la cobertura del puntuado — el siguiente run habría sido 10/25. **El arreglo no es ampliar `period=`:** `market_data` era el ÚLTIMO módulo que se saltaba `prices` (el cache compartido de v3, cuyo docstring lo nombra el primero de sus ofensores), y ese cache YA tenía 400 días / 276-282 barras para los 26 — la cobertura se arreglaba **sin una sola llamada de red nueva**. `run_snapshot` pide UN panel para todo el universo, `compute_metrics` es pura, y `_to_eur_series` desaparece: era una segunda copia de la regla GBp/FX que `prices.to_eur` ya tenía. `MOMENTUM_HISTORY_DAYS: 450` y no 400 porque a 400 `GLUX.SW` quedaba a **19 barras** del precipicio, y un margen fino falla como falló el anterior — en silencio, por plaza, hacia menos cobertura. Resultado: **25/25**. **El segundo arreglo va en el mismo commit porque el primero lo habría roto:** `high_52w` era `closes.max()` sobre TODA la serie traída, o sea el **36,5%** del blend oficial estaba atado a la ventana de fetch; profundizarla habría convertido `near_52w_high_pct` en proximidad a un máximo de 15 meses para las 26 filas en silencio. Windowed a 252 barras — la misma unidad que cuenta `_safe_return`. Su efecto HOY es **exactamente 0,00 en los 26**, y ése es el punto: latente, sin test, a una barra de re-rankear por accidente. **El re-rank, medido dos veces** (contrafactual offline y run grabado, coinciden): **19 de 25 se mueven, máx 8, Spearman 0,90**, y el **top-6 — que con `n_target: 6` ES EL LIBRO — cambia de composición**: copper 5→**1**, `ai_infrastructure_data_centers` 12→**6** (entra), `water_infrastructure` 2→**7** (sale), space 21→13, luxury 13→20. **Y hay que decir entero lo que le hace a la tabla:** water es la fila de **€1.732 ejecutada ayer**, y la tabla nueva emite `TRIM water €-1.738` + `BUY biotech €1.490`, con el corte decidido por **+0,074 composite_z**. Un 42% de cobertura no era ruido de segundo orden: elegía posiciones. **No se ha tocado ni un peso, ni un umbral congelado, ni un vehículo, ni el spec** — 9-1 vs 12-1 sigue abierta y sin evidencia, y mezclarla aquí habría destruido la atribución. Y un footgun preexistente que salió al probar el arreglo: un `--tickers` ad-hoc escribía el snapshot del DÍA (ya se saltaba la partición del lake por esa razón exacta, pero el JSON lo pisaba igual) — reconstruido desde el lake, y ahora un run ad-hoc escribe un fichero ad-hoc. 930 tests (+12); 1 fallo preexistente y ajeno verificado contra HEAD y arreglado justo después — era deriva de fixture (el test leía la historia VIVA de un catalizador para afirmar que el score se MUEVE, y `_TREND_DELTAS` es una función escalón), **931 en verde**. |
