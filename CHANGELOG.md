# CATALYX Changelog

> Full version history. **Do not read this file every session** — the `Recent Changes` table in `CLAUDE.md` covers the last 5 entries and is always in context.
> Read this file only when you need to answer: "when did X change?", "what was the previous formula?", "why was field Y added?"
>
> **How to add an entry:** when `Recent Changes` in CLAUDE.md reaches 6 entries, move the oldest row here verbatim and add detail below it.
>
> **Versioning (since v0.3.1):** SemVer, **pre-1.0** (early/unstable), one number in `pyproject.toml`, tagged `vX.Y.Z` on `main`. See `RELEASING.md`. The earlier `vN.M` labels below are an informal pre-tag change counter, not SemVer.

---






## v8.0 — Backtest punto-en-tiempo y promoción de candidatas: 12-1, comomentum y el reparto GK (2026-08-31)

**El usuario decidió que esperar ~3 ventanas vivas (≈2027-05) para usar las candidatas de v7 no
era aceptable.** La salida no fue bajar el listón de evidencia sino ampliar la muestra hacia
atrás: `experiments/backtest_signals.py` reconstruye las señales reconstruibles SIN look-ahead
sobre los hermanos US (COPX 2010, SOXX 2001, XLE 1998; COT desde 1986) — **176 meses, 3.613
sector-mes, horizonte 63d** — y persiste el veredicto en el lake (`validation/backtest_ic`).
Es el TODO "Backtesting harness (strict no-look-ahead)" de CLAUDE.md, ejecutado para la decisión
que lo necesitaba (PLAN v8).

**La tabla (IC medio · se por bloques · n_eff · sub-periodos 2012-19 / 2020-22 / 2023+):**
`momentum_12_1` **+0.104** (se 0.037, 69% de meses >0, positiva en los TRES sub-periodos) ·
`near_52w_high` +0.087 (concentrada pre-2020: +0.136 vs +0.014/+0.032) · `mom_3m6m` (la
vigente) +0.080 — y con Ω delante (ρ 0.55 con 12-1, 0.64 con 52w-high) el GK le da el peso MÁS
BAJO de la familia: casi todo lo que sabe ya lo dicen las otras dos mejor · `crowding_comomentum`
+0.063 como penalización (se apaga en 2023+: −0.010) · `cot_crowding` +0.006 ± 0.054 — RUIDO;
su peso GK (+0.036) es un artefacto de diversificación (ρ −0.3 con momentum) que cambia de
signo perturbando el IC 1se · `trends_crowding` +0.019 con 46 meses — sin veredicto.

**Un defecto del harness casi entierra al COT:** el suelo global de 8 nombres por sección
transversal (`_MIN_CROSS`) descartaba TODOS los meses del sleeve COT, que estructuralmente
cubre 5 sectores (oro físico, mineras, plata, cobre, petróleo) — imprimía «sin datos» con 40
años de historia en la mano. Suelo por señal (`_MIN_CROSS_BY_SIG`, 4 para COT), test que lo fija.

**El edit (Q1·Q2·Q3, aprobado por el usuario con la tabla delante, frozen-threshold protocol):**
- **Q1** — la dimensión oficial de momentum pasa del blend 3m/6m a
  `0.635·pct(12-1) + 0.365·pct(52w-high)` (§`momentum_spec`; el split es el GK intra-familia
  0.0674/0.0387). Sin historia 1y (11/26 vehículos) cae al percentil 3m6m — una spec MEDIDA más
  débil, no una imputación — y lo dice por fila (`momentum_spec_used: "3m6m_fallback"`).
- **Q2** — el input oficial de crowding pasa de la etiqueta narrativa (evidencia: CERO) al
  percentil de comomentum medido donde existe (15/26), etiqueta donde no, con procedencia por
  fila (`crowding_source`). **COT y Trends NO entran** en la dimensión oficial — el backtest
  dice ruido/panel corto — y siguen como columnas candidatas midiéndose en vivo.
- **Q3** — el pool medido (momentum+crowding, 0.41 combinado) se reparte por Grinold–Kahn:
  IC 0.104/0.063, ρ as-used 0.14 → 66.2/33.8, shrunk hacia el split vigente 70.7/29.3 con
  γ = n_eff/(n_eff+12) ≈ 0.824 → **momentum 0.29→0.275, crowding 0.12→0.135**. **CA (0.35) y
  flow (0.24) conservan su prior DECLARADO** — sin historia de indicadores/shares no son
  backtesteables, y un GK a nivel de familias estaría dominado por lo no medido.

**Salvaguardas que NO se mueven (Q4):** λ sigue gated por IC VIVO (el backtest autoriza qué
ordena la selección, nunca cuánto se aparta de neutral el sizing); el backfill de
`sector_snapshot` sigue prohibido (el backtest vive en `validation/backtest_ic`); las ventanas
vivas de `calibration` quedan como out-of-sample del reparto nuevo — el backtest es in-sample
para siempre una vez usado. Sin grid-search: solo la forma cerrada GK + shrinkage.

652 tests en verde (+13: 8 del harness, 5 de la promoción).

## v7.1 — La pipeline corre sola: heartbeat launchd, sin LLM (2026-08-31)

El sistema entero esperaba a que alguien abriera sesión. Ahora `scripts/heartbeat.sh` corre
**mar+vie 08:12** vía launchd (`scripts/launchd/com.catalyx.heartbeat.plist`, instalado con
`--install`): refresca COT + valoración (+ Trends si el snapshot supera 28d — fuente
rate-limited), aplica los indicadores auto-observables por `indicator_update` (dedup-safe) con
write-back, y corre `pre_run.sh --check`. Exit 10 → **notificación de macOS**; el silencio es un
resultado, no un fallo. `pre_run --check` gana los dos disparadores que faltaban de la doctrina
de cadencia adaptativa (2026-08-04): **techo duro de 45 días** desde el último score run (lo que
impide que un mercado quieto silencie el heartbeat para siempre) y **VIX ≥ vix_ramp_start (25)**
como pull-forward de mercado caliente. Frontera respetada: la intelligence sigue siendo la
sesión — el ping dice «abre `/catalyx-review`», jamás opera. El CronCreate pendiente en la
memoria del 2026-08-04 queda descartado con causa: es solo-de-sesión y expira a 7 días —
no puede sostener una cadencia de 30-45d.

## v7.0 — Contenido de señal: 9 columnas candidatas, medidas desde hoy, peso 0 (2026-08-31)

Ejecuta `docs/PLAN_v7_signal_content.md` fases M, N y O (N3 incluida: pytrends resultó operativo,
26/26 términos). **Ningún peso del compuesto cambia** — test-enforced
(`test_no_candidate_carries_a_composite_weight`). Todo entra como columna del snapshot, registrado
en `calibration.CANDIDATE_DIMENSIONS` y medido con el mismo IC/se/verdict que las dimensiones
oficiales; el primer run con columnas es `run_20260831_172858`, y ahí arranca el reloj de ventanas.

**Columnas nuevas en `sector_snapshot`** (cobertura del primer run, n=26): `momentum_12_1` (15 —
12-1 estándar desde `return_1y_pct`, que el lake llevaba desde siempre sin que nada lo leyera; la
nota de K2 "requiere ampliar el fetch" era stale), `near_52w_high` (26, George–Hwang, también ya
grabado), `ca_unpriced` (25 — CA descontado por `is_priced_in` en eventos y por
`narrative_maturity → maturity_priced_in` en estructurales; sin señal → prior 0.5, flag),
`flow_resid` (18 — residuo OLS del z-flow sobre z-momentum, solo filas medidas),
`inst_sponsorship` (la puntuación 13F que viajaba en cada output sin pesar en nada),
`crowding_comomentum` (15 — Lou–Polk simplificado: correlación media de residuos semanales vs SPY
entre hermanos de catalizador; módulo `scorer/comomentum.py` sobre la infra I1), `cot_crowding`
(5 — percentil 5y del net-spec/OI de CFTC vía Socrata: oro/plata/cobre/WTI; `data/cot_data.py`),
`trends_crowding` (26 — percentil 5y de atención de búsqueda, un término por payload a propósito;
`data/trends_data.py`, dependencia nueva `pytrends`) y `crowding_measured` (26 — blend N4 de las
partes medidas disponibles; la etiqueta narrativa sigue siendo la dimensión oficial).

**M1 — la estructura de correlación se publica con el run.** `commensurate()` añade la matriz
Spearman entre dimensiones (imputados excluidos por par), `n_eff_dimensions` (entropía de
autovalores) y una `correlation_lint` SEPARADA del lint de dimensión muerta (mezclarlas rompía la
semántica del existente — lo dijo un test). Primer run: **mom~flow ρ=+0.84 (n=18), n_eff 2.9 de
4** — sobre filas medidas el acoplamiento es peor que el +0.57 que salía con imputados dentro.

**Dos reparaciones que salieron al construir, no del plan:** (1) el 13F murió con el universo
v2.0 — `chain[0]` pasó a ser siempre UCITS y `_fetch_institutional_ownership` devolvía
`not_available_ucits` para todo el libro; ahora camina la cadena hasta el primer hermano US
(`inst_proxy_ticker`), la misma doctrina proxy del flow. (2) COT en vivo: oro percentil **99.2** y
cobre **100.0** de posicionamiento especulativo a 5 años — la medición ya ordena distinto que la
etiqueta.

**O1 — 7 indicadores estructurales se auto-observan** (`data/indicator_sources.py`: FRED con
`FRED_API_KEY` de `.env`, yfinance, CFTC), escribiendo por `indicator_update.apply_one` — nunca un
canal paralelo — y solo donde la serie pública COINCIDE con la definición almacenada (el core CPI
japonés ex-fresh-food se excluye a propósito: el de FRED es ex-food-and-energy). Primera pasada
aplicada: USD/CNY 7.25→6.72 (−7.3%, dato de mayo), BoJ +12.1% y **JPY net short 40k→63.3k
(+58%)** — dos banderas Rule-5 que llevaban meses sin observarse. En `score_run.sh` corre en DRY
(facts para el review); el `--apply` es del scan. **O3** — serie `valuation` nueva en el lake
(yfinance fund-level, 19/26 con PE real), leída por nada: existe para que un test de ancla de
valor sea posible cuando haya historia. **Caveat de Trends escrito:** todo el universo busca
"X stocks" cerca de máximos de 5 años — hay un factor de atención de mercado en el nivel; lo
informativo es la dispersión (solar 96.2 / semis 95.0 contra lithium 59.8 / nuclear 71.3).

639 tests en verde (+25, y una exención aritmética escrita en el test del knob de control: una
fila en z≈0 se reescala por debajo del decimal de display — gold_miners a 50.1 lo destapó en
vivo el mismo día).

## The flow dimension was measuring the wrong thing on 20 of 26 sectors (2026-08-31, v6.9)

Found because the user asked why the flow line printed a warning. It printed one because it was
right to: `stockanalysis.com`, the PRIMARY shares-outstanding source, was returning **47 errors and
0 successes**. Its unofficial `/api/symbol/e/{tk}/overview` endpoint had started 404'ing, so every
lookup fell through the cascade to CMF — a price+volume oscillator that `flow_data`'s own note
labels "⚠ not true flow". **20 of 26 sectors were scoring `flow_confirmation` (weight 0.15) off a
technical indicator.**

**The fix, and the reason to trust it.** The number is still on the page; only the route died. Two
page-backed paths replace the endpoint, smallest first: the SvelteKit `__data.json` payload (~49 KB,
whose `sharesOut` key holds an INDEX into the flattened node array and must be dereferenced — read
as a value it yields 27) and the rendered page (~228 KB) as a fallback. Re-validated against the
**iShares official screener** across 8 overlapping funds: −4.15%..+0.92%, consistent with the
~0.25–1.75% band this source was originally accepted on. Coverage went from 6 to **18 of 26**
sectors on real share counts. `_shares_stockanalysis` now also distinguishes "site up, ticker not
covered" from "source down", so a genuine coverage gap no longer inflates the error count.

**What the repair MEASURED, which is the part worth keeping.** Twelve sectors moved off CMF onto
real shares in one step, so the CMF stand-in's error is now observed rather than assumed: **mean
|error| 13.1 points on a 0–100 scale, max 25.6**. And the two largest errors landed on the two names
the rebalance table was acting on — `gold_miners` inflated (CMF 78.2 vs 52.6 real) and
`cybersecurity_commercial` deflated (CMF 28.8 vs 54.2 real). One hypothesis was checked and
**rejected** before being written down: CMF is not simply momentum in disguise — its correlation to
the momentum dimension is +0.16 across the universe, so the failure is inaccuracy, not
double-counting.

**Therefore CMF is now imputed, not scored.** Eight sectors still have no clean US proxy, and a
13-point mean error is not a proxy. They are treated the way v6 H2 already treats a missing study:
imputed to the prior (z=0), excluded from the dimension's moments, and flagged as a COLUMN
(`flow_imputed`, `~flow` in the digest), never a gate. The CA-specific branch in `commensurate` was
generalised to an `_IMPUTED_FLAG` map rather than duplicated, with a test pinning that the CA path
did not regress.

**Two ranking defects the re-score exposed, both the same shape as v6.8 #3.** `composite_z` was
stored `round(s, 3)` — and since v6.8 made it the unit the RANK is computed on, that is the same
rounding defect one decimal deeper: `water_infrastructure` and `semiconductors_design` tied at
+0.457 and were separated by list order while their composites genuinely differed. Now 6dp, so a tie
is a real tie. And the `--universe --digest` table printed only the 1dp `comp` while ordering by z,
which made a correctly-sorted table look mis-sorted; it now prints `z=` beside it.

**Effect on the live table — the reason this is not cosmetic.** `cybersecurity_commercial` moved
rank **10 → 5**, turning a **TRIM −€565 into an ADD +€727**: the trim rested on a partials rung whose
condition is "the model no longer leads this name", and the model only stopped leading it because a
broken data source had deflated its flow by 25 points. `semiconductors_design` 5 → 6 (ADD → HOLD),
`ai_infrastructure_data_centers` 6 → 8 (its €1,456 BUY drops out), `gold_miners` 8 → 11. Eight rule
actions become six, and the `struct_ai_capex_supercycle` cap breach falls from **55.4% to 38.8%** of
notional (over by €881, not €2,540). 614 tests green (+2).

## Seven defects the review found in itself, and the one that was silently rewriting scores (2026-08-31, v6.8)

Found while running a second `/catalyx-review` on the same date, which is the condition several of
these needed to become visible. No config threshold moved; every change here is a correctness fix.
612 tests green (+6). An eighth was caught by an existing test rather than by me — `test_a_conditional_marker_tracks_its_condition_in_both_directions`, which reads the live book on purpose, went red the moment the budget DEFERs landed.

**1. The intensity score fed its own trend, so re-running the review moved it.** `write_back`
prepended a row to `intensity.history`, and `_trend_delta` read `scores[0] - scores[1]` — where
`scores[0]` was the row `write_back` had just written for TODAY. The score was therefore a
fixed-point iteration on itself: `struct_cb_gold_accumulation` went **78.6 → 68.5 → 64.5** across
two runs on a world that had not moved, each step reported as `↓ falling 1 period`. Compounding,
and in the one direction that looks like a thesis dying. Fixed in three places, because the bug had
three layers: `compute_intensity` now excludes any history row stamped with today's date (that row
is this computation's own earlier estimate, not a period that has been through — an explicit
`--period 2026-Q3` label never matches and is kept); `write_back` REPLACES that period's row
instead of stacking a second one beside it (the old guard skipped only when period AND score
matched, so a moved score always appended); and `_trend_delta` dedupes by period on read, which
repairs every file already written the old way. Nine of thirteen catalysts carried duplicate
periods — up to **six** rows of `2026-Q2` — so this had been mis-reading the trend leg for months,
not just today. 24 duplicate rows removed from the YAMLs. A test now pins that three consecutive
write-backs produce byte-identical output.

**2. `--all --write-back` wrote scores into the WRONG catalysts' files.** `compute_all()` skips
`merged` and `role: macro_context` catalysts (13 results), but the CLI zipped those results against
a fresh `glob("*.yaml")` (18 paths). Every result after the first skipped file landed one or more
files off: gold's score into `biopharma_patent_cliff_ma`, `ai_capex`'s into `commercial_space`, and
the last five files never written at all. The results already carry `_source_file`; the loop now
addresses each write by it and never re-derives the path. This is the command the v6.7 entry tells
the operator to run, so the bug was armed and waiting for the first person to follow the
instructions. The affected files were restored and recomputed.

**3. The published rank was derived from the rounded DISPLAY composite.** `composite` is rounded to
one decimal, so any pair closer than 0.05 became a tie broken by taxonomy file order — while v6/H1
had already named `composite_z` (3 decimals) the comparable unit. Live on today's universe:
`space_defense_satellite` (49.4, z −0.041) outranked `nuclear_energy` (49.4, z −0.038) for no
reason but alphabetical luck. `sector_scorer.rank_key()` now picks the unit, and it is used at
every ranking site — the two CLI prints, `snapshot_repo` (which writes `rank` to the lake),
`portfolio`'s top-N selection cut (where the rounding costs money, at the `max_positions`
boundary), and `rebalance`'s re-rank of the surviving book, which now receives `composite_z` on the
holding row. A pre-v6 run, or a mixed collection, falls back to the level: comparing a z against an
absolute level ranks worse than a tie does.

**4. The work-list ranking was not the recorded ranking.** `sector_scorer --universe` passed one
flat `--crowd` default to every sector, so crowding's σ_cross was 0 and the dead-dimension lint
correctly reported the 0.12-weighted dimension as ranking nothing — in the view whose only job is
to feed the study work list, while `snapshot_repo` ranked the recorded run with crowding live. Now
`universe_crowding()` derives it from `narrative_maturity`, the same way the recorded run does; an
explicit `--crowd` still overrides universe-wide, which is what asking for one number means.

**5. Five freshness stamps bounced, on the catalysts that had just been verified.** `catalyst_review`
declared its enum "deliberately the same vocabulary as `regime_state` so the scan's delta rows map
onto it with no translation" — but the enum held three values while `catalyx-scan.md` documented
five. Every `strengthening` verdict was rejected, so on 2026-08-31 five catalysts came back with
hard evidence (three of them driving open positions) and the freshness gate went on reporting them
stale the day they were re-verified. A stamp that bounces is worse than one never attempted: the
scan reports success and the gate silently disagrees. `VERDICTS` now holds the scan's five, recorded
verbatim — flattening `strengthening` to `intact` would delete a finding — with `VERDICT_REGIME`
mapping them onto the three states consumers act on. `strengthening` is exempt from the evidence
requirement alongside `intact`; demanding a citation to record "still holds, more so" is what
pushed the scan to omit the row instead.

**6. Two false alarms in the review's own attention list.** `run_digest` marked `rank_moves`
**MISSING** while the lake held six real ones: `_rank_moves` keeps only |Δ| ≥ 5, so a run whose
biggest move is 4 legitimately yields `[]` — a RESULT, not an absent input. `_try` already had
`empty_ok` for exactly this ("listing a passing check under MISSING reads as a hole in the run and
teaches the reader to discount the whole list"); it just was not used here, and MISSING is now
reserved for the lake having no rank events at all. And `review_report` raised the "write a reason
for each override logged THIS run" marker on `budget` DEFERs — the v6 L4 trade-budget deferrals,
which the precedence rule chose rather than a person, which the rebalance table's BUDGET line
already names, and for which "the evidence the rule was missing" is by construction nothing. They
now join `unrecorded` as auto-authors the marker skips. Both are the cry-wolf failure the
attribution-drift note warns about: a check that fires on a clean result trains its reader to skip
the table.

**7. Two lines that lied in the transcript.** `catalyst_lifecycle --apply` printed
`[PROPOSED (dry run — use --apply)]` whenever there were zero transitions, because the label tested
the truthiness of an empty applied list — telling the operator to pass the flag they had just
passed. And `indicator_update batch` printed `Σ None` for every recompute: it read `catalyst_id`
from a result that returns the id under `id`, on the one line that says which catalyst's intensity
just moved.

## The percentile saturated on exactly the catalysts it exists to measure (2026-08-31, v6.7)

Plan v6 J4 — the most delicate item in the plan, because it changes PUBLISHED scores.

**The diagnosis (D5).** `intensity_engine` scores an indicator by its empirical percentile within
its own history. For a persistently rising series — central-bank gold buying, hyperscaler capex —
the current value sits at or near its own maximum nearly every month, so the percentile pins at
~100 and stops discriminating. That happens on precisely the persistent drivers `StructuralCatalyst`
exists to model, and it means the first sign of a slowdown arrives only when the value actually
falls below past readings, which for a trending series is very late.

**But detrending alone would be the opposite error.** "At an all-time high" is real information;
scoring the residual instead of the level would put a record-level driver mid-range for the crime
of being exactly on its own trend. So the two are **blended**: the level says how strong this is
against its own history, the residual says whether it is running above or below its own
trajectory. Two catalysts both at records — one accelerating, one flattening — now score
differently, which is the discriminating power D5 was after. `detrend_weight: 0.0` reproduces the
old score exactly. Detrending only engages when the series actually trends (|Kendall τ vs time| ≥
0.5, rank-based so one huge print cannot masquerade as a trend).

**Deviation from the plan's own wording, deliberately.** The plan said "residual against a rolling
mean". A rolling window over a 6-point series discards most of the sample and returns nothing for
the earliest points; a linear OLS detrend keeps every observation, which is the binding constraint
on indicators observed monthly.

**The second half: the 6th-observation cliff.** The score used to switch outright from the
saturating threshold curve to the percentile when the `min_history_points`th value arrived, so ONE
new data point could move an indicator by tens of points for reasons unrelated to the world. Now
blended linearly across `[min − 2, min + 2]`: n≤4 curve, n=6 half and half, n≥8 percentile. Same
family of mini-cliff v1.5 removed from the semaphore buckets and I6 removed from the VIX brake.

**A real off-by-one, caught by not trusting the first table.** The initial before/after used
`history_blend_span: 0` as the "pre-v6" baseline, as the config claimed. It was not: at exactly
`n == min_history_points` the boundary was `n <= lo`, so span=0 took the FALLBACK where pre-v6
took the percentile. The first comparison I generated was therefore against a baseline that never
existed, and it reported the largest move as −24.8 when the true figure is **+44.0**. Fixed to a
strict `<`; `--compare-legacy` now runs the SAME code path with the features zeroed rather than a
reimplementation that could drift.

**The measured effect, with each change attributed.** 6 of 48 indicators move; 4 of 13 catalysts:

| catalyst | pre-v6 | v6.7 | Δ | cause |
|---|---|---|---|---|
| `struct_enterprise_cyber_spend_supercycle` | 70.8 | 79.8 | **+9.0** | both |
| `struct_cb_gold_accumulation` | 75.6 | 69.1 | −6.5 | history blend |
| `struct_commercial_space_supercycle` | 86.7 | 84.5 | −2.2 | both |
| `struct_energy_transition_grid` | 94.2 | 93.8 | −0.4 | detrend |

**The instructive case.** CrowdStrike ARR YoY growth: history 0.33 → 0.29 → 0.27 → 0.22 → 0.24,
now 0.23, against thresholds weak 0.10 / strong 0.20. Three legitimate readings of the same
number — level percentile **25** (near the bottom of its own range), threshold curve **84** (well
above "strong"), detrended percentile **83** (τ = −0.73: the series is falling and 0.23 sits ABOVE
that decline, so the deceleration is flattening). Pre-v6 published a flat 25 and threw the other
two away.

**And note the detrend cuts BOTH ways, which the motivating example hides.** D5 was about rising
series pinning at 100; here the series is falling and the residual RAISES the score. That is
symmetric and correct — the residual measures position against your own trajectory, whatever its
sign — but a reader expecting "detrend = damper on hot catalysts" would be surprised, so the
output says it. The distinct downward channel for a genuinely dying catalyst is `trend_delta`,
which is unchanged.

**Migration.** `catalyst_scorer` reads the STORED `intensity.current_score`, so **nothing
downstream moves until someone runs `intensity_engine --write-back`** — a test pins that. Inspect
first with `uv run python -m catalyx.scorer.intensity_engine --compare-legacy`, which prints the
table above and attributes each change to the detrend or the blend.

606 tests green (+12).

## Which constants actually have consequences — and the harness that almost lied about it (2026-08-31, v6.6)

Plan v6 I4. `experiments/sensitivity_weights.py` perturbs each constant ±25% and ±50%, one at a
time, over the last recorded run, and measures whether the OUTPUT moves: Kendall τ against the
base ranking plus the Jaccard overlap of the top-10 set (τ can stay high while the top churns,
and the top is the part that becomes positions). It is the cheap, honest answer to "these
percentages are arbitrary" — not a better story about each number's provenance, but a measurement
of which ones are worth arguing over.

**THE HARNESS'S OWN FAILURE MODE IS THE FALSE NEGATIVE, and it hit twice before the table was
trustworthy.** A knob that misses its target prints identically to a constant that does not
matter — a confident "INERT" that is really "I never touched it".

- **The momentum periods came back τ=1.000, INERT.** But H4 had just measured that reweighting
  the momentum blend moves ranks by up to 7 places. `momentum_engine` unpacks `_MPW` into
  `_WEIGHT_1M/3M/6M` at import, and THOSE are what the engine reads; the knob patched `_MPW` and
  reached nothing. Fixed, and they are now among the more consequential rows (τ 0.895 / 0.938).
- **`sharpness` came back 0.00pp.** `build_model_holdings` reads `construction.sharpness` before
  the global, and `catalyx.yaml` declares its own. Same class, one layer down.

So the harness now carries a **control knob** — `composite_scale.z_scale`, a monotone rescaling
that MUST move every score and reorder nothing. If it fails either half the table prints "FAIL —
the harness itself is broken; ignore this table". A sensitivity table nobody can falsify is
decoration. Nine tests pin the machinery, including both false negatives as regressions.

**A third distinction the first table collapsed.** τ=1.000 was reporting two completely different
things as one: a constant that shifts every score without reordering anything, and one the live
path never consults. A `moved` column (how many sectors' composites changed AT ALL) separates
them, and each NOT-REACHED row now carries its VERIFIED reason:

| constant | verdict |
|---|---|
| `event_decay.default_halflife_days` | NOT REACHED — all 15 active events declare their own halflife |
| `catalyst_interaction.confirm_max` | NOT REACHED — needs an event confirming a structural in the same sector |
| `intensity_trend_deltas` | NOT REACHED — `catalyst_scorer` reads the STORED `intensity.current_score`; these apply at write-back, not at scoring |

That last one is a fact about the architecture worth knowing on its own: changing the trend deltas
does nothing until the next `indicator_update` / `--write-back`.

**And the finding the harness was worth building for.** `sharpness` moves weights **0.00pp live
and 2.78pp at λ=1**. It is not inert — it is **SWITCHED OFF**, because `skill_shrink` collapses
the whole conviction leg toward a neutral book and λ=0 today (the measured rank IC is noise).
Filing "currently disabled by a measurement" under "does not matter" would be the opposite
conclusion, so the sizing rows are measured twice and get both columns. `vol_tilt_alpha` reads
2.40 live vs 2.37 unshrunk — nearly identical, which is the documented design confirming itself:
`vol_tilt` runs AFTER the shrinkage precisely so that neutral means neutral in RISK.

**What the ranking table says.** The four composite weights are the most consequential rows
(τ 0.858–0.938, top-10 Jaccard 0.818 — a ±50% nudge on any of them churns two of ten names), then
the momentum periods, then `reinforce_factor` (τ 0.994). Nothing is DECISIVE by the harness's own
threshold (top-10 Jaccard < 0.8). Read that as: the chain is not balanced on a knife edge, and
evidence is owed on the composite weights before anything else — which is exactly what phase K is
waiting on data to provide.

**An INERT constant is not thereby a WRONG one.** It is un-arguable on today's universe, and the
finding may not survive a differently-shaped one. The output says so.

594 tests green (+9).

## Constants with their assumption written down — and a second risk decomposition found in production (2026-08-31, v6.5)

Plan v6 I5 · I3.

**I5(a) — what the conviction tiers IMPLY, so it stops being re-derived.** A tier ceiling is also
a per-line LOSS BUDGET once paired with the exit floor: `exit_watcher.drawdown_exit_pct` is −30%,
so the most a single line can cost the book before the protective exit fires is `weight × 30%` —
**6.0% / 4.2% / 2.1%** for tiers 1/2/3. That is fractional-Kelly sizing made explicit: full Kelly
on an edge this uncertain (measured rank IC ≈ 0) would be far larger. The number is a CONSEQUENCE
of `n_target`, not an independent choice, and the YAML now says so — if 6% on the top line is too
much, the lever is `n_target`, and raising it costs monthly trade slots.

**I5(b) — `deployment.base: 0.70` is an aversion parameter, and the entry says that outright.**
The equity risk premium justifies being INVESTED; it says nothing about 0.70 versus 0.60 versus
0.85, and quoting it as though it did would dress a preference as a result. What the ERP does
license is the ASYMMETRY — invested is the default and cash must be argued for — which is why the
step term only moves the ratio up and the sole downward term is the VIX ramp. The one derived
number there is the ceiling (0.85 = `deploy_max`), because `n_target` and the position cap are
read off it.

**I5(c) — `line_risk_pct`, the realized version, per line.** The position digest prints
`weight_of_total_capital × stop` beside each name and totals it: today **7.89% if every stop fired
at once**, top line 3.15%. A config comment became a number the review can read.

**AND THE THING I5 TURNED UP: a second risk decomposition, built the two ways v6 I1 exists to
fix.** `position_metrics` has carried its own `risk_contribution` since v4 — computed on a **raw
daily sample covariance**. Both defects at once, one layer from the module written to fix them:
daily closes across LSE/XETRA/Euronext/SIX are asynchronous so their covariance is biased down
(Epps: universe ρ 0.127 daily vs 0.245 weekly), and an unshrunk sample covariance with T not
comfortably above N has biased extreme eigenvalues — the exact directions a risk decomposition
reads. It now routes through `scorer/covariance.py`: **weekly returns with Ledoit–Wolf when there
is enough history, daily as the documented fallback**, because 52 weeks is a lot to ask of a book
opened this spring. Today it falls back, and SAYS SO: *"only 12 common weeks — under the 52 needed
to sample weekly… read the risk shares as ordering, and the book vol as a floor."* The basis and
the shrinkage are printed in the header. A number computed two different ways must say which one
produced it.

**I3 — `vol_tilt_alpha` 0.5 → 1.0, with the assumption declared.** α is not a dial, it is a claim
about what the score MEANS: `score ∝ μ ⇒ α=2` (mean-variance), `score ∝ Sharpe ⇒ α=1`
(inverse-vol), `score ∝ nothing ⇒ α=0`. The composite is a cross-sectional PERCENTILE — an ordinal
rank, which is a Sharpe-like statement — so the coherent value is 1. **0.5 was a hedge between two
positions and corresponded to no stated belief about the score at all.** The original objection
("full inverse-vol underweights exactly the high-beta sectors this mandate exists to own") is
answered by SELECTION, which is score-driven and untouched: the tilt only equalizes risk per euro
among names already chosen for their catalysts.

**Measured before claiming it, and the measurement trimmed the claim.** On the model book: weights
move at most 2.1pp (gold miners −2.1, AI infra +2.1), book vol 19.4% → 18.9%. But the risk/capital
spread barely moves — **1.71 → 1.66, with gold miners still carrying 2.2× its capital share**. **α
is a weaker lever than it looks, and the reason is the chain order:** `water_fill` at
`max_position_pct` runs AFTER the tilt, so once the low-vol names hit the 20% cap they cannot
absorb more of the redistribution and the tilt is clipped. That is correct — a risk limit must
outrank a sizing preference — but it means equalizing risk per euro is not something α can deliver
on a capped book. If that dispersion is the target, the instrument is a risk budget inside the cap
(the I2 column), not α. The YAML records this rather than the tidier claim.

585 tests green (+2).

## There was no covariance matrix anywhere, so every risk limit was notional (2026-08-31, v6.4)

Plan v6 I1 · I2 · L2, in that order, and **the order is the substance**.

**I1 — `catalyx/scorer/covariance.py`.** Until now nothing in CATALYX computed a covariance
matrix. Every risk limit — `max_position_pct`, `correlated_catalyst_cap`, the conviction tiers —
was NOTIONAL, so 20% of the book in a bucket at 55% vol and 20% in one at 18% vol were the same
number to the rules. The module computes the missing object and nothing else: Ledoit–Wolf
shrinkage toward constant correlation, per-vehicle vol, portfolio vol, and the Euler risk
decomposition (`ctr_pct`, which sums to 100 across positions — that is what makes "this name
carries 40% of the risk on 20% of the money" a sentence with a defined meaning).

**Weekly sampling, and it is not a preference.** Measured across the 44-vehicle universe: mean
pairwise ρ runs **daily 0.127 → weekly 0.245 → fortnightly 0.243**. That is the Epps effect — the
book's UCITS lines trade on LSE, XETRA, Euronext and SIX with different hours and liquidity, so
daily closes are not synchronous and the sample covariance is biased down, here by about half. A
daily matrix would have reported the book as half as concentrated as it is and turned the MCTR
column into a tranquilizer. This finding rewrote the plan item before it was built.

**And the honest wrinkle: on THIS book the gap runs the other way.** Ten liquid names over 57
weeks give ρ weekly 0.333 vs daily 0.350. So the module reports `epps_gap` — the book's own
number — and the printed line says which case it is, rather than asserting "the daily figure is
biased down" next to a number that is not. The universe-wide measurement is why the matrix samples
weekly; it does not license a claim about every subset.

**Shrinkage is not optional here and it is not tuned.** With ~6-26 series and ~52-104 weekly
observations, T is not comfortably larger than N: the sample covariance is badly conditioned and
its extreme eigenvalues — exactly the directions a risk decomposition leans on — are biased.
δ* is ESTIMATED by the Ledoit–Wolf (2004) formula, not chosen, and the tests pin that it responds
to the sample the way the derivation says: δ→1.0 when the truth IS constant correlation (the
target is right), δ→0.08 when the truth is block-structured (the target is wrong and shrinking
would destroy real structure), and δ→0 as 1/T (0.109 → 0.020 → 0.003 at T = 60 / 250 / 2000).
Symmetric and positive definite in every case, including T barely above N. No new dependency.

**I2 — the cap gains a risk column and does not change its rule.** `cluster_risk_for` gives
`movement_repo.cap_check` the share of book variance each driver carries, on the POST-trade book,
beside the notional `exposure_eur` it already reads. Clusters overlap — a sector with two drivers
is wholly in both, the same rule `exposure_eur` follows since v5.2 — so neither column sums to
100, on purpose. When the matrix cannot be built the column is **None**, never 0: an unmeasured
risk must not read as no risk, and a test pins it.

**What the column says, immediately.** On the post-trade book:

| driver | notional | risk (share of variance) |
|---|---|---|
| `struct_ai_capex_supercycle` | 41.6% | **50.8%** |
| `struct_biopharma_patent_cliff_ma` | 21.9% ← breaches | **11.7%** |
| `struct_energy_transition_grid` | 19.7% | **27.1%** |
| `struct_cb_gold_accumulation` | 7.2% | **16.6%** |
| `struct_stablecoin_payment_rails` | 6.8% | **16.8%** |

**A notional cap ranks these wrong.** Biopharma breaches the cap while carrying *less than
three-quarters* of the risk gold accumulation carries under it at a third of the notional. The
plan's stated trigger for this measurement — "when two clusters at the same notional show 2× the
real risk" — is met and exceeded. The cap nevertheless stays notional and stays `warn`: house
doctrine is that a measurement is evidence FOR a config edit, never the edit. Replacing the
notional cap with a risk budget is a decision for the user, now with the number in front of them.

**L2 — the cap goes 20% → 30%, and only now.** At n_target=6 the neutral weight is 14.2%, so a
20% cap forbade ANY TWO positions from sharing a driver. No themed book of six names satisfies
that, which is why AI capex printed a breach every single run — and a permanently breached cap is
a permanently ignored one, which is worse than one set where it can bite. It was deliberately left
at 20% through v6.0 because raising it first would have deleted the breach by decree; the
replacement discipline (I2) had to land first. 30% admits two neutral positions per driver and
**still bites on the real case**: AI capex prints 41.6% today. Two new feasibility invariants stop
this from being re-litigated — the cap must admit two neutral positions, and two tier-1 positions
on one driver must still breach it, so relaxing it cannot make it decorative.

583 tests green (+18).

## The turnover guard was generating turnover, and the model book had the wrong word for exposure (2026-08-31, v6.3)

Plan v6 J2 · J3. Two local defects, both of the same family: a mechanism whose stated purpose and
actual arithmetic had drifted apart.

**J2 — `apply_deadband` moved the positions it had just decided not to move.** The band's whole
job is to say "this target is close enough to what you hold — do not trade it". It then
renormalized to preserve the gross by multiplying **every** weight, kept ones included, so each
protected position came out at `held × total/Σkept` instead of `held`: a micro-trade on every
position the guard existed to protect, and taxable ones, since the guard was written to suppress
tax churn. It could also push a weight **past `max_position_pct`** — `water_fill` had already
applied the cap, and the renormalization ran after it.

The residual is now absorbed by the FREE positions only; a kept weight is returned exactly as
held. Three edge cases decided explicitly rather than left to the arithmetic: with **no free
position** to absorb it the difference is simply cash, which is what the band means; when the kept
positions **already fill the gross** (`residual ≤ 0`, bounded by `n_kept × deadband`) the free
names take their targets unchanged, because paying for a decision NOT to trade by forcing a trade
elsewhere is exactly the trade the band exists to prevent; and a final clamp keeps the gross at or
under 100 (leverage is not an available state) and every weight at or under the cap — the cap is a
risk limit and outranks the band, with the freed weight staying as cash rather than being
redistributed, the same rule the contested haircut already follows.

**`apply_deadband` had no tests at all.** That is why D7 survived — eight cases now pin it,
including the two the bug produced: a kept weight must come back exactly as held, and no
renormalization may carry a weight through the cap.

**Inert on today's book, and that is the point.** Every held weight is more than a point from its
target (the previous book had 10 positions, the new one 6), so nothing is kept and old and new
agree exactly. The fix bites once the book stabilizes — which is precisely when the deadband is
supposed to act, and precisely when the bug would have fired every run.

**J3 — the model book learned the word the real book learned in v5.2.** `catalyst_exposure_rows`
split each holding's weight equally across its catalysts and called the result exposure. That
split is **P&L CREDIT** — who is credited with the return — and it partitions the book. It is not
**RISK**: if a driver breaks, the whole position behind it moves, and nobody owns 30% of an ETF.
v5.2 established this for the real book and left the model book publishing the discarded semantics
into `portfolio_catalyst_exposure`, so the same word meant two things depending on which book you
were reading.

Both columns are now emitted: `pct_credit`/`credit_eur` (partitions, sums to 100) and
`pct_exposure`/`exposure_eur` (the full position per driver, sums to MORE than the book, on
purpose). `pct`/`eur` keep the credit split for pre-v6 readers. The point the split hides is in
the tests: declaring a second driver **lowers** the credit on the first, so under a notional cap
honesty would have bought headroom for free — the exposure column does not move. `lake_query`
reports both averages and probes for the column so pre-v6 partitions read back as the split rather
than dropping the run.

565 tests green (+12).

## A weight only means what it says if the scales are commensurable (2026-08-31, v6.2)

Plan v6 Fase H, all four items, plus I6. The diagnosis behind them (audit 2026-08-31, D2): in a
ranking by weighted sum, the EFFECTIVE weight of a dimension is not `w` but `w·σ_cross`. The
composite added a uniform cross-sectional percentile (momentum), a level in a narrow band
(catalyst_alignment), a constant 50 wherever no flow snapshot existed, and a five-value enum
(crowding) — so the numbers in `composite_weights` were never the weights actually applied, and a
dimension degenerate to a constant weighed **zero** whatever the YAML said. That is exactly what
`valuation_relative` did for months under a 0.15 weight (v1.6). v1.6 removed the instance. **The
mechanism was left running**, and `_DEFAULT_FLOW = 50` reproduces it today for every sector with
no flow snapshot.

**H1 — the combination moves to z-space.** `sector_scorer.commensurate()` standardizes each
dimension across THIS run's universe (z winsorized to ±3, crowding sign-flipped so higher is
better everywhere), and the composite becomes `clamp(50 + z_scale·Σ wᵢ·zᵢ, 0, 100)` with
`z_scale: 15`. Raw values stay in `score_breakdown`; the standardization is internal to the
combination. `composite_z` — the raw `Σ wᵢ·zᵢ` — is persisted beside it, because that is the unit
that means the same thing in every run. Now 0.29 means 0.29: a name 1σ better on momentum scores
`0.29 × 15` points higher, whatever that dimension's raw spread happens to be that month.

**And the lint, which is the half that survives.** A dimension whose `σ_cross` falls below
`dead_dimension_sigma` is NAMED in the run summary. It fired on its first run: `sector_scorer
--universe` from the CLI applies the default crowding 35 to every sector, so crowding is dead
there (σ=0.0) — a known footnote in `catalyx-heatmap.md` that had never been a machine-checkable
fact. Two tests pin the property rather than a number: a constant dimension cannot change the
ranking, and it cannot fail to be flagged.

**What the measurement said, including where the diagnosis was wrong.** On the real recording path
the four σ are 28.0 / 29.4 / 19.4 / 18.7, giving effective weights **.389 / .338 / .185 / .089**
against nominal .35 / .29 / .24 / .12. So flow and crowding were under-counted, as D2 said — but
D2 also claimed catalyst_alignment was under-counted (σ≈10-15) and it was **over**-counted, and
for a reason that is itself a v6 finding: the CA=0 of study-less sectors (D3) puts a spike on the
floor and inflates the dispersion. The correction is therefore small today (max rank change ±2,
same book) and gets larger once H2 stops manufacturing that spike.

**H2 — not measured is imputed to the prior, never to the worst case.** No study meant CA=0, so
"we have not looked" scored identically to "we looked and there is nothing". With flow at its
default the best a study-less sector could reach was `0×.35 + 100×.29 + 50×.24 + 65×.12 = 48.8`
against `min_composite: 55` — **a new sector with perfect momentum could not enter the flagship
book, ever**, which is a pro-incumbent bias in a pipeline whose stated job is to detect before
things are priced in. `compute_catalyst_alignment` now returns a machine-readable `reason`, and
only `no_study` is imputed: it lands at z=0 and is EXCLUDED from the CA moments (an imputed value
that drags the mean it is then measured against is not an imputation). `no_active_catalysts` keeps
its zero — a study that looked and found nothing is a finding, not a gap. Flagged `ca_imputed` on
the row, and test-enforced as a COLUMN, not a gate: it qualifies a BUY the way `blind` freshness
does (v5 E1), and `/catalyx-open` still requires a study before any money moves. **Today it fires
on nothing** — all 26 investable sectors have studies. Its value is prospective: the next sector
promoted out of `/catalyx-scan` discovery is no longer dead on arrival.

**H3 — the selection floor stops being a level.** `min_composite: 55` was an absolute number
applied to a semi-relative blend: momentum is a percentile (mean 50 by construction) while CA
drifts with the catalyst cycle, so what "55" excluded changed every run. A frozen threshold whose
MEANING is not frozen is not a frozen threshold. Profiles now declare `min_composite_z`; the
pre-v6 key is read one major more, translated through the same map. The flagship is set to
**0.0** — "we do not hold a name below this run's universe average" — and deliberately NOT the
mechanical translation of 55 (=+0.33), which leaves 8 candidates for 6 slots, thin enough that the
floor starts co-selecting with top-N. The floor excludes; the selector is top-N under the caps.
The dislocation lenses migrate too (the plan had not named them): the opportunity floor KEEPS the
strict stance at +0.33 because it does not co-select with anything.

**The migration hazard this exposed, and how it is handled.** A z-derived floor compared against a
pre-v6 run's absolute composites is a comparison between two scales, and it would have silently
admitted most of the universe. `portfolio._apply_composite_floor` and `dislocation.analyze` both
detect whether the run they are reading carries `composite_z` and fall back to the pre-v6 absolute
floors when it does not. Verified live against the current lake run, which is pre-v6.

**H4 — the momentum blend stops paying for the reversal window.** `return_1m: 0.20 → 0.0`,
3m/6m renormalized to the exact 0.5625 / 0.4375 (45:35 preserved). The last month is the
short-term REVERSAL window (Jegadeesh 1990, Lehmann 1990) — the standard cross-sectional signal
is 12-1 precisely because it skips it (Jegadeesh–Titman 1993) — and this repo had already measured
the same thing locally: the v1.6 acceleration backtest found NEGATIVE monthly IC on the short leg,
and the 1m leg still entered with a positive sign. **This is the only item in the block with real
bite:** uranium −7 ranks, gold miners −4, gold physical −4 — the hot-last-month names.

**I6 — the VIX brake ramps instead of cliffing.** `vix_pause_above: 30` with a 0.20 penalty meant
29.9 → 30.1 moved a fifth of the target capital, so a VIX oscillating around 30 oscillated the
whole book run to run. Now linear from `vix_ramp_start: 25` to `vix_ramp_full: 35`: zero below 25,
half the brake at the old cliff point, full at 35. An unmigrated config centres the ramp on its own
`vix_pause_above`, so it keeps its stance. `why` prints the ramp inputs like everything else.

**The honest bottom line: the H block does not change today's book.** Same six names before and
after; only the internal order moves (gold miners 4th → 6th, pharma up). Candidate pool 11 → 11 —
the momentum and crowding filters bind before the composite floor does, at either floor value. It
is a correctness fix and a base, not an alpha change, and saying otherwise would be inventing a
result. What it buys is that the two silent failure modes are now impossible: a dead dimension is
named, and an unmeasured one goes to the prior.

**Also touched.** `sector_snapshot` schema **1.3 → 1.4** (`composite_z`, `composite_absolute`,
`ca_imputed`; `composite` redescribed, with the note that pre-1.4 values are NOT comparable across
the boundary — compare by `composite_z` or by rank). The dashboard's fixed 66/40 traffic light
would have rendered every sector amber forever on a scale centred at 50, so composite gets its own
`compositeColor` at ±0.5σ and `STRONG_COMPOSITE` derives from it; `scoreColor` keeps the old cuts
for the genuinely 0-100 columns. 553 tests green (+22).

## A trade slot is scarce, so the table stops pretending it is free (2026-08-31, v6.1)

Plan v6 L3 · L4. `rebalance_rules.fee_eur: 0.0` said trading costs nothing. Inside the monthly
allowance that is true in ACCOUNTING terms and false in economic ones: the operator has 10 free
trades a month, the mandate spends slots on catalysts arriving BETWEEN reviews, and a slot
therefore carries option value — its shadow price is positive whenever the constraint binds. The
table had no concept of it and would happily emit more rows than the month could carry.

**`trade_budget_plan`** splits the money-moving rows into what a review may execute and what it
must defer. The ordering is the part that matters: **not** expected return. The composite's rank
IC is noise (−0.05), so ranking slots by a forecast the system has already measured as unreliable
would invent precision exactly where it declared none. The three tiers are what IS measured —
**risk removed** (SELL/REDUCE, exempt: a book does not keep risk it decided to shed because the
month ran out of free trades) → **cost of inaction** (BUY/ADD; `cash_drag` is a measured cost) →
**rotation** (TRIM), which is what Gârleanu–Pedersen (2013) says to starve first when trading is
costly: with transaction costs you trade partway toward the target and sacrifice the fine
adjustment between existing names. Within a tier, the biggest mover wins the slot — most money
moved per scarce unit. Exempt rows still CONSUME slots, and if they alone exhaust the budget that
is reported (`over_budget`), not hidden.

**Nothing is zeroed.** `rule_action` and `trade_eur` stay the rule's ask; the row is flagged
`budget_state` and printed with a trailing `*`. So the scorecard still judges the RULE — we want
to know whether it was right even when it could not be executed — and the deferral is priced
separately as what the constraint cost.

**L4 — `budget` is an override author, and deliberately not `unrecorded`.** `unrecorded` means
nobody wrote the decision down; a budget deferral is the rule working. Filing them together would
fill the deviation tally with rows nobody chose, which is the exact contamination v5 built that
tally to avoid. Budget defers are logged against the CURRENT run, which also means the next run's
`unrecorded_deviations` finds an override for that sector and does not re-file the same decision
as silence. They are still scored: ~21 trading days later `override_edge` says what the budget
cost, so the constraint is falsifiable like everything else in the table.

On the live book the budget binds immediately: **8 money-moving rows, 6 fit, 2 deferred (€1,163)**
— the pharma ADD and the cybersecurity TRIM. `fee_after_free_eur` is still null; when the cost of
the 11th trade is known, going over becomes a priced choice rather than a refused one. An existing
render test caught the legend colliding with the table header and was right to. 531 tests (+7).

**Left standing, and worth knowing:** the swap ledger pairs the deferred TRIM with a granted BUY
as its financing, though with €7,368 idle the buys are funded by cash, not by that trim. The
pairing predates this change; the budget only made it visible.

## The position cap was a position COUNT, and nobody had written the identity (2026-08-31, v6.0)

First step of `docs/PLAN_v6_signal_scale_and_covariance.md` (L1 · L5 · J1).

**The defect.** `max_position_pct` had been read for years as a concentration ceiling. With a
deployment target it is mostly the opposite — a lower bound on how MANY positions the book must
hold: `n_min = deploy_max / max_position_pct`. At the frozen 12% and a deploy_max of 0.85 that is
**8 positions minimum**, and the operator's binding constraint is **10 free trades a month**
(Revolut). Building the book the config demanded spent 8 of 10 slots and left 2 for a mandate
whose entire value proposition is acting on catalysts that arrive BETWEEN reviews. The sibling
identity `deploy_max / correlated_catalyst_cap` explains the breach v5.2 found: at a 6-name book
the neutral weight is 14.2%, so a 20% driver cap forbids *any two positions from sharing a
driver* — which is why `struct_ai_capex_supercycle` printed 35.6% against it every run. **The cap
was not being broken by a reckless book; the three constants were jointly infeasible.** Each was
defensible alone, which is exactly why nobody caught it: there was no test that read them together.

**v4 had seen half of it and decided the wrong way.** D-3 of the v4 plan noted that
`max_position_pct` was 12 in `rebalance_rules` and 16 in `portfolios/catalyx.yaml`, and
recommended "12 wins". It was never shipped — fortunately, because unifying DOWN would have
tightened the count constraint to 8 positions. A ceiling cannot be chosen without asking how many
positions it forces.

**The fix — the ceiling is no longer chosen.** New `book_shape` block: `n_target` is the one
declared number and `max_position_pct` + `conviction_tiers` derive from it as multiples of the
neutral weight (`deploy_max / n_target`). The old 12/8/4 absolutes were picked for a ~10-name book
and meant nothing at 6 — every position would have sat above tier 1. Now `[1.4, 1.0, 0.5] × 14.2%`
≈ **20/14/7%**, and a tier keeps meaning "1.4× a normal line" whatever `n_target` becomes.

**n_target = 6 is measured, not preferred.** Mean pairwise ρ across the 44 universe vehicles is
**0.245 on weekly returns** — and 0.127 on daily, which is the Epps effect (asynchronous closes on
LSE/XETRA UCITS lines bias covariance down; the estimate converges by weekly, fortnightly gives
0.243). With σ_rel(n) = √(ρ + (1−ρ)/n): n=5 0.629 · **n=6 0.609** · n=7 0.594 · n=8 0.583 · n=10
0.566. Past 6, each additional name buys under 2.5% of relative vol and costs one of ten monthly
trade slots; below 6 the vol given up climbs steeply. 6 is the knee of the measured curve. (That
Epps finding also rewrote the plan's covariance step before it was built: a daily-returns matrix
would have understated portfolio risk by half and turned the MCTR into a tranquilizer.)

**`trade_budget` recorded, not yet wired.** `fee_eur: 0.0` says trading is free; inside the
allowance that is true in accounting terms and false in economic ones, since a slot is scarce and
carries option value. The block declares `free_per_month`, `reserve_for_events` and
`planned_max_per_review`; the rebalance engine consumes it in L3. `fee_after_free_eur` is left
**null on purpose** — the cost of the 11th trade is a broker fact, and inventing it would price the
constraint wrongly in both directions.

**What deliberately did NOT change.** `correlated_catalyst_cap` stays at 20% though it is now
provably too tight. Raising a cap deletes a breach by decree, so it moves only after covariance/
MCTR publishes risk per cluster and can carry the discipline the notional cap was providing. The
other three model books (`momentum`, `equal_weight`, `low_crowding`) keep their shapes: they exist
to compare strategies against the flagship and have lake history under those shapes.

**`tests/unit/test_config_feasibility.py`** is the part that outlives the numbers: the cap must
admit the target book, `n_target + reserve_for_events` must fit the monthly allowance, the driver
cap must admit one neutral position, the YAML must mirror the derived values, and the model book
must be executable under the real book's rules. The next config edit that makes the triple
unsatisfiable fails the suite instead of surfacing fifteen reviews later as a permanent breach.
524 tests green (+5). Model book rebuilt: 6 positions, cap binding at 20% on `pharma_large_cap`.

## Closing the drift found the cap reading the wrong number, on the wrong book (2026-08-31, v5.2)

v5.1 printed the attribution drift and left the decision to the user. The user took it — "cerramos
el drift" — and closing it properly required fixing two things underneath, because re-attributing a
position with the old accounting would have made the book look SAFER.

**The mechanism: `reattribution[]`, append-only (schema movement 1.3).** `attribution[]` answers
why a line was opened — a dated judgement, the input the validation loop scores — so nothing
rewrites it. What was missing was anywhere to record the present-tense answer. Each entry carries
`as_of`, the new `attribution[]`, an optional `not_attributed[]` and a `rationale`; latest `as_of`
wins; readers go through `movement_repo.effective_attribution()`. `not_attributed[]` exists because
a check that re-raises a question already answered teaches its reader to skip the table: a driver
the review looked at and declined must stop appearing, with the reason on the file rather than in
somebody's habit.

**The two decisions.** `copper_miners` €1,000 → `struct_ai_capex_supercycle` 0.65 /
`struct_energy_transition_grid` 0.35 (grid electrification drives copper demand independently of
datacenter racks and would survive an AI capex pause; datacenter was the opening thesis and stays
primary). `pharma_large_cap` €500 → `uncatalyzed` 0.5 / `struct_biopharma_patent_cliff_ma` 0.5,
**declining** `struct_glp1_obesity_supercycle` in writing — it was opened as a defensive line with
no catalyst and half of that is still true, so `uncatalyzed` keeps its weight instead of being
retconned into a thesis it never had; and a broad large-cap pharma ETF is not a GLP-1 vehicle.

**What that exposed: the cap was reading the P&L number.** `catalyst_ledger` splits each movement
by attribution weight — correct for CREDIT, so one euro of return is not credited twice — and §6
fed that split to `correlated_catalyst_cap`. But the grid position (€500, 0.7 grid / 0.3 AI capex)
contributed **€150** to the AI-capex row, while if AI capex breaks the whole €500 is at risk. Nobody
owns 30% of a utilities ETF. Worse, the incentive ran backwards: naming a second driver LOWERED the
weight on the first, so honesty bought headroom. Re-attributing copper under the old accounting
would have *reduced* reported AI-capex exposure. New `exposure_eur` — the full position behind every
driver it names — is what the cap reads; `invested_eur` stays as P&L credit, relabelled in §6 so
nobody reads the wrong one. Rows now sum to more than the book, on purpose. On this book the
correlated bucket was understated by €350 *before* any re-attribution: **€1,650 reported against
€2,000 real, on a €2,000 cap.**

**And then the number that mattered.** §6 checks what is held; §3 proposes what to buy; nothing
joined them, though the cap's own sentence is "headroom is what a NEW position may still take". New
`cap_check()` prices the proposed table: executing the current one as printed puts
`struct_ai_capex_supercycle` at **35.6%** against the 20% cap — €1,560 of new money into a bucket
whose headroom is exactly **€0** — and `struct_energy_transition_grid` at 23.7%. Resolution differs
by action type, and it has to: a BUY has no position yet, so the sector study's structural drivers
are the honest estimate; an ADD is held, so its recorded attribution governs — including a declined
driver, which re-deriving from the study would quietly overrule. The §6 marker now fires on a
breach held OR proposed.

Two smaller things found on the way. `_try` in `run_digest` reported an empty result as MISSING,
so the drift check listed itself as a hole in the run at the exact moment it came back clean —
`empty_ok` for checks, where nothing found IS the result. And two anti-conservatism tests asserted
"this book breaches no cap", which was true when written and is now false: both were rewritten to
read the expectation off the book (`("cap and by how much" in text) is breached`) instead of
freezing a fact about one day's data into a test of a mechanism. 517 tests green (+4).

---

## The cap was being evaded by the merge it was built to survive (2026-08-31, v5.1)

Chasing the loose end v5.0 left open — "§6 lists `pharma_large_cap` as `uncatalyzed` while its
study names three catalysts" — found a bigger and different defect, and the note in CLAUDE.md had
the mechanism wrong. It was not a point-in-time sector map in `catalyst_exposure_rows`; §6 reads
`movement_repo.catalyst_ledger`, which buckets by each movement's own frozen `attribution[]`.

**`struct_copper_datacenter_demand` is `merged_into: struct_ai_capex_supercycle`, and the ledger
reported them as two rows.** €1,000 under the absorbed id, €650 under the survivor — one economic
driver, published as two. Combined it is **€1,650 = 16.5% against a 20% cap**, so §6 advertised
**€1,350 of headroom where €350 existed**, on the single largest exposure in the book. The one
control designed to stop correlated double-counting was being evaded by exactly the event it was
meant to survive.

CLAUDE.md already required it: *"usa `structural_catalyst_repo.resolve()` para seguir un
`merged_into` antes de leer la frescura de un catalizador"*, and *"dos catalizadores que suben y
bajan por la misma razón … burlan el `correlated_catalyst_cap` (que existe justo para eso)"*.
`run_state` applies `resolve_all` and its comment names the 2026-08-27 merges by date. The ledger
was the one reader that did not. It now resolves through `merged_map()` (one YAML read, not one per
id — the v5.0 lesson) and reports `absorbed_ids` so the collapse stays auditable and every number
still ties back to the movement file that produced it. `resolve_merged=False` returns the raw
record: the movement files are untouched and the validation loop still scores what was written.

**Second, the smaller half, unchanged by design.** `pharma_large_cap` was opened 2026-06-16 as a
defensive line with a literal `attribution: [{uncatalyzed, 1.0}]`; its study now names three active
drivers, one of which — `struct_biopharma_patent_cliff_ma` — it shares with the €978
`biotech_drug_development` BUY on the same table. The cap cannot see an overlap filed under
`uncatalyzed`. The fix is NOT to rewrite the attribution: it is the dated record of *why* the line
was opened and the input the validation loop scores. New `attribution_drift()` names the gap
instead — recorded vs today's structural drivers, per held position — and §6 prints it under the
cap table. Event catalysts are excluded because the cap is written "per shared primary STRUCTURAL
catalyst"; listing every `cat_*` a study mentions buried the two real cases under a dozen that are
not. Today: copper missing `struct_energy_transition_grid`, pharma missing two. Closing them is a
human decision in `/catalyx-open`, or leaving them standing — knowingly.

§6 and the digest now read the ledger LIVE from the movement files rather than the
`catalyst_performance` partition, which freezes the merge map of the day it was written — the same
class of defect one layer down. The partition itself is fixed on the next `ingest` (already run).

513 tests green (+2).

---

## Data-action coherence: no dead data funds a live buy in silence (2026-08-31, v5.0)

All seven items of `docs/PLAN_v5_data_action_coherence.md`, in the plan's own order. Nothing here
edits a frozen `rebalance_rules` threshold; §6 of the plan records what was rejected and why.

**F2 — the report can no longer claim to be finished.** `review_20260831.md` shipped with all five
judgement markers blank and passed `--check` clean: `lint_prose` policed the prose that WAS there,
nothing policed the prose that was not. New `lint_completeness` — a `<!-- CLAUDE: … -->` with no
prose between it and the next heading is a finding, and the marker STAYS in the file (it is the
anchor that lets the report regenerate without losing what was written). Two orthogonal lints,
either can fail alone. Found while implementing: two of the seven markers are CONDITIONAL — "for
each override logged THIS run" and "flag any catalyst over the cap" — and demanding prose where the
honest answer is *nothing breached* is how a lint teaches people to write filler. The generator
already knows, so `section_overrides` emits its marker only when a DELIBERATE override is pending
(the `unrecorded` DEFERs are auto-logged precisely because nobody wrote one, and §4c already names
them), and `section_exposure` only when a cap is actually breached.

**E1 — the row carries the age of the evidence it spends on.** §8 listed 41 overdue indicators and
§3, 130 lines earlier, ordered €1,020 into `luxury_goods` — whose `catalyst_alignment` of 70.4 IS
the `intensity.current_score` of `struct_china_luxury_recovery`, two of whose indicators had not
been observed since 2025-09-30. Both facts were printed; neither knew about the other.
`catalyst_freshness` existed but only for OPEN positions, so a BUY — the row that commits new
capital — arrived with nothing qualifying it. New `freshness.by_catalyst()` (a three-level verdict:
`fresh` · `stale` · `blind` = nothing inside 2× the cadence) + `rebalance._data_age_by_sector()`,
which takes the WORST status among a sector's catalysts (a book is only as current as the stalest
driver it pays for) and resolves `merged_into` first. The result on this book is the finding:
**every BUY rests on stale or blind evidence, four of them 148–240 days blind.** It is a COLUMN,
not a rule — `decide_action` does not read it, test-enforced, because the freshness doctrine is
that stale data is a reason to re-verify, never to stop acting. Also found: `scr.resolve()` reloads
every YAML per call, so the first cut cost **2.9s** per run; `merged_map()` once is 259ms.

**F1 — the table says what it rests on.** The review demanded eight trades toward a ranking the
same page reported as ordering nothing (IC −0.050, top3−rest −5.84pp) and called not doing them a
breach. Both can be right — being invested in leaders is a different prior from THIS ranking
working — but the document never separated them, so it read as self-contradictory. New
`selection_prior()` prints one line when the evidence is NONE or ADVERSE, and nothing when it is
MEASURED: the rule picks NAMES on an edge not yet established while the SIZE is already
neutralized (λ=0), and accepting the rows is accepting that prior. A sentence, not a gate —
gating the SELECTION would be a new policy, and the policy is the user's to set.

**E3 — one standing decision, one DEFER.** The dedup was scoped to the PRIOR run, so a rule that
keeps asking and a human that keeps declining wrote a fresh DEFER every run: three pipeline
executions in a week produced 30 rows for 10 decisions, and the tally measured how often the
pipeline ran. `unrecorded_deviations` now takes `open_defers` and skips a (sector, action) already
on the clock, keeping its original `logged_at` — the 21-day window has to run from when you
stopped acting. A movement after the defer settles it, so the next refusal is a new decision. §5's
two counters are labelled `backlog` and `suspension gate`; they measure different things and
looked contradictory side by side.

**E2 — the streak counts review cycles.** v4.3 collapsed two runs of one afternoon; same defect one
scale up. The four runs behind copper's SELL were 06-30, 07-05, 07-28, 08-28 — gaps of five days to
a month — so `rank_out_consecutive: 2` meant "ten days" or "two months" depending on how busy the
quarter had been. A threshold whose meaning depends on your working rhythm is not frozen, whatever
section it sits in. New `_cycle_runs` walks backwards keeping the latest reading and requires
`min_gap_days: 21`; the reason now names the calendar span (`for 3 consecutive review cycles
(54d)`). **No action changes**: copper's streak goes 4 → 3 and still fires, grid's 3 → 4.

**F3 — the cash row becomes a ledger instead of a reprimand.** Two defects: `forgone` was
hardcoded, so a quarter in which sitting out was CORRECT printed identically to one in which it was
expensive; and the counterfactual was SPY, when the decision on trial is "should I have executed
THIS table?". Both fixed — the label flips (`CASH DRAG` / `CASH THAT SAVED YOU`), the model book
leads when available, and each leg keeps its own honest sign. The two numbers disagree by 14×:
**€196 forgone vs the benchmark, €14 vs the `catalyx` book.** The benchmark had been overstating
the cost of inaction by an order of magnitude.

*Found while writing F3, in code written the same hour:* `portfolio_nav` holds backtest / live /
forward rows under the SAME `portfolio_id`, at overlapping dates and on different NAV bases (~124
backtest vs ~103 live). Reading the window without pinning the mode took the first row from one
series and the last from another and reported **−16.88%**, i.e. a €1,179 "saving" from holding cash
that never happened. It is exactly the defect v3.5 fixed in `portfolio_compare`, reintroduced at a
new read — the table cannot express the constraint, so every read must repeat it. Pinned by a test
with three modes in one fixture.

**G1 — the spread is a property of the ticker.** `b/e 0.20%` printed identically on seven of eight
action rows; a column that cannot tell a liquid `IUHE.AS` from a thin `JEDI.DE` is not telling you
anything. `cost_drag` has accepted a per-ETF override since v3 and its comment promised the
universe would carry one — nothing passed it. New `weights.spread_bps_by_ticker()`, and the vehicle
is now resolved BEFORE the costs so the lookup can happen. **The field is deliberately still
empty:** populating it from a yfinance snapshot returned `ask < bid` on SEMI.L and off-hours quotes
of 463bps / 193bps elsewhere, and an invented number here is worse than the honest default — the
b/e would stop being constant and look informative while being noise. Recorded in the config
comment, on the same standard `name` and `ter` already hold; it is filled by hand at the quarterly
spread review. Absent entries inherit the global, so today's output is unchanged.

511 tests green (+18). Report 13,486 → 14,565 bytes: F2's conditional markers and v4.9's trims paid
for E1's column, F1's line and F3's second counterfactual, so the judgement half got cheaper to
check while the deterministic half got strictly more honest.

---

## A column that meant two things, and three blocks that said one thing N times (2026-08-31, v4.9)

Two defects, one cause: a fallback introduced to *hide* a missing value instead of naming it.

**`rk` meant the model-book rank on some rows and nothing on others.** The model book contains
only the sectors the model kept, so `rank` is null for exactly the sectors it dropped — i.e. blank
on every row whose reason cites a number. The 2026-08-31 review printed `rk` empty beside
"ranked below top-10 (#11) for 4 consecutive runs". v4.3 papered over it in `rebalance.render`
with a marked fallback (`~11` = "not in the book, universe rank 11"), and **that patch is the
defect**: a column whose semantics change per row obscures more than it says, it needs a legend
line to be read at all, and the other renderer — `scripts/review_report.py`, which produces the
document the user actually reads — never got it, so the two diverged within a week of the fix.

Now **one semantic in all three renderers** (CLI table, report §3, digest `actions[]`): the
universe rank for this run — the number the reasons cite and §1 of the report shows. The
model-book rank stays internal, where it is unambiguous. `_REBALANCE_FIELDS` carries `score_rank`
and no longer carries `rank`; the digest used to ship `"rank": null` beside `"reason": "…(#11)…"`.

**Underneath the fallback was a real bug.** `partial_rungs` read the *model-book* rank for its
ladder's rank leg — the leg that means "the model has STOPPED leading this name". It was therefore
blind precisely when it should fire: copper, #11 in the universe and dropped from the book,
rendered `still a leader (rank nan < 6)` — the opposite of the truth, about a rank nobody had. Two
things were wrong: the leg read the wrong rank, and a pandas NaN off the parquet round-trip
survived an `is None` guard and printed as a value. Fixed with `_clean_rank` at every rank read and
a NaN→None pass at the parquet boundary in `_rebalance_rows`. `render`'s row loop is extracted to
`_render_rows`, pure, so the column semantics are pinned by a test with no lake.

**Overkill removed, no reading lost.** Three blocks stated one fact N times:

- §8 went from **41 indicator rows to 13 catalyst rows** — per catalyst, how many indicators are
  overdue and which is worst, by how much, against which cadence. What the review acts on is
  "which catalyst is running blind, and how blind"; the full per-indicator list is one
  `catalyx.scorer.freshness` call away.
- Unrecorded deviations aggregate **by run** ("`run_20260728_103246` → 10 action(s): 3×ADD ·
  5×BUY · 2×SELL"), instead of printing the same run id on ten rows.
- A rank sweep where **every** |Δ| ≥ 5 move points the same way is no longer printed as N
  findings. Twenty `rank_up` and zero `rank_down` is the DENOMINATOR moving — a universe cut or a
  scoring edit — and rendering it as twenty independent sector stories trains the reader to
  discount the table. It is now stated as the one fact it is, in both the report and the digest
  (`_rank_moves` → `{uniform_sweep, n, direction, note}`; **2,491 → 235 bytes**). A genuinely
  mixed run still prints its rows.

The explanatory blockquotes restated, every single run, the rationale that already lives in the
module docstring and in this file — why the breakeven replaced `net edge €`, why λ clamps rather
than inverts, why both rungs are reported. That reasoning is worth writing once, not re-paying for
monthly: what remains is one line of semantics per section. In the digest, the rung definitions are
hoisted out of the rows (they come from ONE config; repeating label + `rank_min` per position said
the rule five times to state five distances once).

Measured, same information: report **19,431 → 14,054 bytes** (−28%), `run_<date>.json`
**24,289 → 19,917** (−18%). 492 tests green (+4), pinning that the `rk` column never contradicts
the reason beside it, that a NaN rank renders as a gap and never satisfies the ladder, that a
uniform sweep is one fact, and that the rung labels belong to the run rather than to each row.

---

## Two review steps deleted, and a test suite that stops depending on your shell (2026-08-31, v4.8)

Step 10 of `docs/PLAN_v4_rigor_and_lean_metrics.md` (§5 D-c–D-f) — the last one. **v4 is complete.**

**D-c — two steps deleted, not shortened.** Step 4 ran the two catalyst summaries that
`/catalyx-scan` already reads in C1: 4.5 KB per review to restate what step 0/1 carried forward.
Step 11 swept 31 non-investable sectors and its own instruction was to write "no watch trigger
surfaced" — a watch trigger firing IS an investability event, so it belongs in Step 12 and nowhere
else. The dashboard moved from 8.5 to **5.5**: the user reads the book — the rebalance table, the
risk contributions, the cost of not acting — *before* the positions are argued about, so the
evidence arrives ahead of the argument instead of after it. (`structural_monitor` stays: the merge
was rejected on 2026-08-28 and the reason is recorded in the plan.)

**D-d — the study step reads a digest, not a dossier.** New `sector_study_repo.core()` /
`core_all()` + `core [--all] [--json]`. A study is ~20 KB and there are 27; exactly **two** of
their fields are consumed anywhere — `narrative_maturity` (→ `crowding_risk` in `snapshot_repo`,
→ the exhaustion test in `catalyst_lifecycle`) and `active_catalyst_ids` (→ the catalyst→sector map
in `portfolio.catalyst_exposure_rows`). Everything else is the RESEARCH: the reason those two
values are what they are, which belongs in front of a human rewriting the study, not in the context
of a run that will read two fields. **25,187 → 2,047 bytes** for one study; `core --all` is a
26-line table. `age_days` travels inside the digest on purpose — a stale study is worse than none
because it injects confident, wrong full-dimension scores, so the freshness must arrive in the same
breath as the value it qualifies, never one CLI call away.

**D-e — context hygiene.** CLAUDE.md's TODO block claimed two SectorStudies were missing; both have
existed for weeks. Replaced with a **measured state** table — deployed 30% vs an 85% rule · real
book TWR −1.88% vs SPY +4.19% (**−6.07pp**) · composite IC −0.050 on 1 independent window ·
scorecard not yet scoreable — four numbers no agent should have to rediscover, and the reason
several rules read the way they do. The repo map's "414 tests" is now 483, and the review skill's
subagent briefs name **one input file and one output shape** each instead of restating the pipeline
order; the order is in the skill and the state is in `run_<date>.json`, and the restatement is the
part that grew every time a step was added.

**D-f — time, not tokens.** `post_run.sh` ran `nav_engine live` as a **shell loop over four
strategies**: four interpreter startups, four import graphs and four passes over the same warm price
cache, to answer four questions about that one cache. New `nav_engine live-all` does it in one
process — **10.4s → 1.9s**, identical numbers.

And the dev-environment defect, which was subtler than "the group is undeclared" (that was fixed in
v3): with `CATALYX_PRICES_OFFLINE=1` exported in the shell, `uv run pytest` **failed six tests**
that inject their own `fetch_fn` and touch no network at all. The temptation is to make the offline
switch yield to an injected fetcher — rejected: a kill switch an argument can override is not a
kill switch, and `test_offline_read_never_calls_the_backend` pins exactly that strictness. The real
defect is that a suite's result depended on the shell it was launched from, so new `tests/conftest.py`
clears the runtime switches per test; the one test that is *about* offline behaviour still sets it
itself with `monkeypatch`. **No production behaviour changed.** 488 tests green (+5), passing
identically with and without the variable exported.

## The rule table becomes falsifiable (2026-08-31, v4.7)

Step 9 of `docs/PLAN_v4_rigor_and_lean_metrics.md` (§3 B4). `calibration` measured the RANKING.
`score_overrides` measured the DEVIATIONS. Nothing measured the **table**. Every time a human
departed from the rules the departure was priced 21 trading days later and tallied by author, while
the rules kept their authority by never being scored at all — a one-sided ledger that made the
table unfalsifiable by construction, which is the one property this project refuses everywhere else.

New `rebalance.score_decisions()` + `decision_scorecard()`, exactly parallel to `score_overrides`:
same lake, same clock, same refusal to read a verdict off a sample too small to have one.

```
RULE SCORECARD — what the table's own actions earned (63d forward)
  action     n  mean fwd   vs HOLD   rule edge  verdict
```

Three decisions inside it that decide whether the number means anything:

**HOLD is the baseline, not a row.** The question a rule table can answer is not "did the names go
up" — that is beta, and `deploy_ratio` owns it — but "did acting beat leaving the book alone". A
BUY that matches the HOLD mean scores **0.00pp**: matching the baseline is the baseline, not skill.

**The forward return is signed by the direction the rule moved money** (`action_direction`: +1 for
ADD/BUY, −1 for SELL/REDUCE/TRIM, `None` for HOLD and RE-SCORE, which move none). A SELL into a
−5% move is the rule being RIGHT, and an unsigned table would print it as the worst row on the
page. So `rule_edge_pp = (mean − HOLD mean) × direction`, and positive always means right.

**A verdict needs both n and independent windows.** `min_n: 5` and `min_effective_windows: 2`,
where effective windows counts NON-OVERLAPPING horizons exactly as `calibration.aggregate` does:
five runs inside one 63-day window are one observation. Below either bar every row reads
`not scoreable yet`; above them, an edge inside 2·se reads `noise`. Today: **nothing scoreable** —
24 recorded rows, no complete 63d window, first verdicts ~63 days after the earliest recorded run.
Saying so is the point; a scorecard that produced a verdict on this sample would be worse than none.

Cost discipline: with no complete window there is **no price fetch at all** — for months the honest
answer is "not yet", and paying for a download to print a fixed non-answer is a fixed cost on
nothing (test-enforced with a `price_fn` that raises). And the scorecard never changes a threshold:
`rebalance_rules.frozen` still means a rule moves by a config edit and a CHANGELOG line.

Surfaced on `rebalance scorecard`, report **§5b**, the run digest and a dashboard section beside
the overrides table. Two docstrings corrected while wiring it: `review_report.py` and `run_digest`
both claimed to fetch no price, and both have read one since v3 through `score_overrides` — an
override's edge is a *forward* return and no lake table can hold it until the window closes. The
claim now names its two exceptions instead of being quietly false. 483 tests green (+10).

## Quiet stops being free (2026-08-29, v4.6)

Step 8 of `docs/PLAN_v4_rigor_and_lean_metrics.md` (§4 C1–C5). v3 built everything that makes a bad
ACTION visible: a rule table, banned words, an override log, a suspension arithmetic. It built
nothing that makes INACTION visible. Every row has printed its friction to the cent since v3; the
cost of leaving €6,953 idle for 74 days was printed nowhere. That asymmetry is not neutral — it is
a standing thumb on the scale for doing nothing, on every run.

**C4 — the idle cash is priced in the same units as the friction.** New `rebalance.cash_drag()`,
on the book strip, in the report, the digest and the dashboard:

```
CASH DRAG €6,953 idle since 2026-06-16 (74d) · benchmark +3.03% over that window → €211 forgone
```

Idle *since the book last changed* — a movement date, not a run date: a review that recommends and
is not executed does not restart the clock. Beside it, the report names the smallest friction
blocking a trade on the table (**€0.78**), because the comparison is the entire point. An
unmeasurable benchmark leaves the cost `None`, never €0 — "the cash cost nothing" is a claim, and
printing it because the price cache was cold is exactly how inaction gets a free pass.

**C1 — the shortfall is an action with a persistence rule.** `UNDER-deployed by €5,453` printed for
months as a line of text nobody had to answer. New `shortfall_pp()` (points of TOTAL capital: a book
at 30% against an 85% rule is **54.5pp** short, not "35% of the way there") + `shortfall_status()`
with `deployment.max_shortfall_pp: 10.0` × `max_shortfall_runs: 2`. Breached today. Counted per
REVIEW DATE, never per run id — two runs in one afternoon are one review, and a single compliant
review resets the streak, because a book that moves is not the failure being described. The CASH
row now says where the money already is: *"already allocated on the rows above; declining a row IS
the override, not the cash."*

**C3 — the deviation nobody wrote down.** An override existed only if the narrator chose to write
one, and after three reviews with non-HOLD rows the log was **empty**. Now `unrecorded_deviations()`
asks the filesystem instead of the narrator: the next run reads the previous run's non-HOLD rows,
matches them against `data/movements/*.json` in the interval and against the override log, and
`_log_unrecorded` writes what is left as DEFERs authored **`unrecorded`** — against the run that
recommended them, so `override_edge` prices them ~21 trading days later exactly like a deliberate
deviation. Idempotent by construction (a logged override clears the row). The first run found
**10**: the whole of run 20260728's table — 2 SELLs, 3 ADDs, 5 BUYs — executed as nothing and
recorded as nothing.

**C2 — Step 9 loses its costless default.** `Open now / Wait / Skip` becomes three priced branches:
*Execute €target* · *Execute a smaller size — state it* · *Decline — state the evidence*. "Wait" is
gone: it is the option that is never wrong today and never right in the record, because it writes
nothing, so nothing scores it, so it costs nothing to choose forever. Every branch now produces a
logged decision. New `cap_headroom_eur` per row so the smaller size is priced rather than guessed.

**C5 — the language rule enforced in the generator, not in the prompt.** `BANNED_ACTION_WORDS` was
enforced on `rebalance`'s own output, where hedging was never possible — a rule table cannot hedge.
Hedging lives in the prose appended at the `<!-- CLAUDE: … -->` markers. New
`review_report.py --check`: `lint_prose()` fails the committed report on `watch`/`monitor`/
`consider`/`revisit`/`next cycle`/`for now`/`cautious` inside the sections where a decision is
stated, and stays silent in the macro-context sections, where analysis is allowed to be tentative
(policing hedges there would only teach the narrator to write confidently about what it does not
know). Tables, blockquotes and code are skipped. The generator gets no exemption from the rule it
enforces — pinned by a test that lints the generated report itself, which cost one "for now" in the
new §4c.

New report section **§4c "The cost of not acting"**, a `CASH DRAG` / `SHORTFALL` pair on the book
strip and the digest, two dashboard cards, and a "Not acting has a price too" methodology block.
Persisted, so nothing downstream re-fetches a price to render it: `book_cash_drag_eur`,
`book_cash_idle_since|days`, `book_bench_return_pct`, `book_shortfall_pp|runs|breached`.
473 tests green (+16).

## The tilt is now earned, not assumed (2026-08-28, v4.5)

Step 7 of `docs/PLAN_v4_rigor_and_lean_metrics.md` (§3 B1). The pipeline fused two decisions into
one number, and only one of them had a justification.

| Decision | Justified by | Where it lives now |
|---|---|---|
| **How much is at work** (beta) | the equity risk premium — not this model's skill | `deploy_ratio`, untouched |
| **How the working capital is tilted** (alpha) | this model's *measured* rank IC | shrunk by λ |

The conviction softmax was dispersing weights exactly as aggressively on a composite rank IC of
**−0.050 (se 0.200, ONE non-overlapping 63-day window)** as it would on an IC of +0.4. That is
paying concentration risk — the most expensive thing a book can buy — for an ordering that has
never been shown to order anything.

```
w_final ∝ neutral + λ · (model − neutral)
λ = clamp(IC / tilt_ic_target, 0, 1) · n_eff/(n_eff + tilt_prior_windows)
```

Two independent haircuts, because two different things can be wrong with a tilt: the ranking may
not order returns (the IC leg), and it may not have been measured often enough for its IC to mean
anything (the credibility leg — the same `shrink_factor` the bucket table already uses). Today
both fail: `λ = 0.00`.

**λ = 0 is not "no model".** The model still selects the names, applies every filter, dedupes the
vehicles and enforces `max_position_pct`. It declines only to also *size* them. And it is not a
retreat to cash: both legs carry the same names at the same gross, so the book stays at the 85%
the deploy rule asks for — test-pinned (`test_lambda_never_changes_how_much_is_deployed`), because
"the model is unproven" quietly becoming "hold cash" is the exact conservatism this plan exists to
prevent. What λ=0 costs is dispersion, and only dispersion:

```
sector                                 λ=1     λ=0     Δpp
pharma_large_cap                     15.88   13.22   -2.66
biotech_drug_development             15.16   10.98   -4.18
semiconductors_design                10.01    7.37   -2.64
cybersecurity_commercial             10.49    8.71   -1.78
water_infrastructure                  9.80   13.38   +3.58
luxury_goods                          9.40   11.98   +2.58
robotics_automation                   7.17    9.32   +2.15
GROSS                               100.01   99.98
```

Top/bottom ratio 2.58× → 1.82×. The residual spread is **not** flat weighting: `vol_tilt` (v4.4)
runs after the shrinkage, so the neutral book is *risk*-neutral, not naively equal-weight — vol is
a measurement, not a view, and it keeps applying when the view is withdrawn.

**A negative IC clamps λ to zero; it never goes negative.** Shorting your own ranking on n_eff=1
is a superstition with a minus sign. This is the same asymmetry v4.3 gave `net_edge_gate`: a
backwards ranking is a scoring problem to fix, never a licence to trade the ranking upside down.

**The regime haircut rides on the NEUTRAL leg too.** If neutral were flat, shrinking to λ=0 would
have silently undone the `contested` overlay and re-risked the very name the overlay de-risked.
The overlay is a risk statement, not a conviction one, and only the conviction leg is shrunk.

New: `calibration.skill_lambda()` (+ `composite_ic(dimension=…)`, now also returning
`effective_windows`, and `_effective_windows()` — three runs six days apart over one 63-day
horizon are ONE observation, and the row count was the wrong denominator), and
`portfolio.skill_shrink()`. A `momentum`-weighted book shrinks by the **momentum** IC (−0.114),
not the composite's: the shrink measures the column actually doing the ranking. Config
`portfolio_weighting.tilt_shrinkage|tilt_ic_target|tilt_prior_windows|tilt_lambda_floor`, per-book
overridable; the code default is `False` (λ=1, the pre-v4 behaviour byte-for-byte), so a book opts
in through YAML.

λ is **persisted**, not just displayed: `portfolio_holding.tilt_lambda` and
`rebalance.book_tilt_lambda`, so a target book read back in six months says whether its dispersion
was earned or assumed. Surfaced on the rebalance table (`TILT` line), report §3, the run digest
and the dashboard (a `tilt earned (λ)` card beside the deploy-ratio card, plus a
"Two decisions, kept apart" methodology block). 457 tests green (+11).

## Two €500 lines were never two equal bets (2026-08-28, v4.4)

Step 6 of `docs/PLAN_v4_rigor_and_lean_metrics.md` (§2 A4). The composite decided WHAT to own and
with how much conviction, and then the euro amount ignored the one input that makes two euros
comparable. On this book `semiconductors_design` (vol 55%) and `pharma_large_cap` (vol 18%) were
sized as the same bet; semis carries ~3× the risk per euro spent.

**Measurement first: risk contribution on every position.** New `position_metrics.risk_contribution`
— `RC_i = w_i·(Σw)_i / σ_p`, from the covariance of daily EUR returns over a window common to every
held vehicle, summing to 100% by Euler's theorem so the shares are exhaustive and comparable.

```
RISK CONTRIBUTION — where the book's volatility comes from (60 common days, annualized)
  sector                          etf       capital %   vol %   risk %  note
  copper_miners                   4COP.DE        34.9    43.2     52.9  1.5x its capital share
  semiconductors_design           SEMI.L         15.1    55.5     25.5  1.7x its capital share
  cybersecurity_commercial        USPY.L         18.6    36.1     14.0
  grid_infrastructure_utilities   IQQH.DE        12.9    30.5     11.4
  pharma_large_cap                IUHE.AS        18.5    17.8     -3.8  NEGATIVE — lowers book vol
  BOOK                                          100.0    25.6    100.0  effective N 4.3 on 5 positions
```

Two things this says that nothing in the pipeline could say before. **`copper_miners` carries 53%
of the book's risk on 35% of its capital** — the single largest risk position, and the one the
table wants to SELL. And **`pharma_large_cap`'s contribution is negative**: it is anticorrelated
enough with the rest to *lower* total volatility. The rule table wants to ADD to it, which is
right, but for a reason nothing could previously articulate — and which is the first thing that
would need defending if the position ever came up for a trim. A negative contribution is a real
property, not an artefact to clamp away.

`effective_n = 1/HHI` joins it: 5 positions at these weights behave like **4.3** equal ones, which
is the number a concentration limit is actually about. One division.

**Then the sizing.** New `portfolio.vol_tilt`: `w_i ∝ transform(score_i) / σ_i^α`, applied BEFORE
`water_fill`, so `max_position_pct` and the deadband keep meaning exactly what they said.
`portfolio_weighting.vol_tilt_alpha: 0.5` — `α = 0` is the previous behaviour byte-for-byte
(and stays the code default, so a book opts in via config), `α = 1` is full inverse-vol, which
would systematically underweight precisely the high-beta sectors a catalyst-driven mandate exists
to own. 0.5 halves the risk dispersion without turning a momentum book into a low-vol fund.

Effect on the model book, stated rather than buried — the largest single move is 3pp and nothing
is reordered:

| sector | α=0 | α=0.5 | Δ |
|---|---:|---:|---:|
| pharma_large_cap (vol 18%) | 13.7 | 15.9 | +2.2 |
| water_infrastructure | 7.4 | 9.8 | +2.4 |
| biotech_drug_development | 13.1 | 15.2 | +2.1 |
| semiconductors_design (vol 50%) | 13.0 | 10.0 | −3.0 |
| space_defense_satellite | 8.5 | 6.2 | −2.4 |

Details that matter for correctness. A **missing** vol takes the median of the ones present — never
zero (which divides into an infinite weight) and never one (which would read as risk-free);
`min_vol_pct: 5.0` does the same job for a stale or flat series. Vol is measured on the sector's
OWN traded vehicle, per the broker-reality rule — scoring COPX and buying 4COP.DE measures a risk
you do not carry. And `_sector_vols` reads the price cache with `allow_fetch=False`: the portfolio
builder is not a fetch site, and a cold ticker drops to the median rather than opening a network
round-trip inside a weighting loop.

Surfaced in `position_metrics` (new block), report §4b, the run digest, and the dashboard's
measurement table (capital vs risk columns, amber when risk exceeds capital share, green when
negative) plus two new book cards. 446 tests green (+11).


## Two ways the table could fire on data nobody collected (2026-08-28, v4.3)

Step 5 of `docs/PLAN_v4_rigor_and_lean_metrics.md` (§3 B2 + B3). Both defects had the same shape:
an absent or unmeasured quantity resolving into a confident verdict.

**B2 — the after-tax gate was armed to fire backwards.** `net_edge_gate` stood aside while
`effective_windows < min_windows_to_gate (3)`, which correctly stopped an unmeasured quantity from
becoming a veto. But a window COUNT cannot see DIRECTION, and the bucket table the gate would use
when it armed is built from the same ranking whose composite IC is **−0.050 against an se of
0.200** — `top3` at −0.056 sitting below `rest` at +0.779. The moment the third independent window
landed (~9 months out, per the config's own comment) the gate would have begun blocking sales out
of the bottom bucket and waving through sales out of the top: it would have **inverted** the
profit-taking rule, silently, on a sample the calibration module labels `noise`.

New `calibration.composite_ic()` (mean IC over COMPLETE windows, with se and verdict) and
`rebalance.gate_status()`, which arms only on a joint condition, naming each failure:

```
GATE  after-tax gate STANDS ASIDE · composite IC -0.050 (se 0.200 → noise) · ~1 window(s)
      STANDS ASIDE — ~1 independent window(s) < 3; |IC| 0.050 < 0.20 — the ranking orders
      nothing; IC -0.050 is NEGATIVE — arming would invert the rule, not enforce it
```

The sign condition is the new one and it is deliberately asymmetric: a negative IC **disables**
the gate, it never inverts it. "Our ranking is backwards" is a scoring problem to fix, not a
licence to trade the ranking upside down. Config lives under `net_edge_gate.requires`
(`min_independent_windows`, `min_abs_ic: 0.20` ≈ 1·se at n=26, `ic_sign_must_be_positive`), and
the status is now printed on the table, in the report §3 and in the run digest — a gate that does
not fire is a decision too, and it is the reason the rule actions stand unmodified.

**B3 — a missing rank was being read as a verdict.** `rank_out_streak` counted `None` as "outside
the cut", so a sector absent from a run's `sector_snapshot` — including absent because the
universe changed shape — accumulated a sell signal out of measurements nobody took. Fixed: a gap
BREAKS the streak, and enough gaps get their own action.

`RE-SCORE` joins the enum as a first-class, non-money action: `SELL > REDUCE > TRIM > **RE-SCORE**
> ADD > BUY > HOLD`. It sits with the sell-side actions on purpose — it is what a rank-out SELL
degrades to, and parking it beside HOLD would bury a work item under the rows that move money. It
fires AFTER the fundamental sells (a broken thesis is a sell whatever its rank did) and BEFORE the
rank-streak sell, which is exactly the verdict missing data cannot support. Config `rescore_if`
(`lookback_runs: 4`, `missing_runs_min: 2`).

**What the plan got wrong about this run, corrected here.** D6 claimed today's two SELLs
(`copper_miners`, `grid_infrastructure_utilities`) fire on a rank that does not exist, "35% of the
deployed book decided by a missing value". They do not. Both sectors are scored every run — copper
at #11, grid at #14 — and the `rk = —` that prompted the claim is the **model-book** rank, blank
for precisely the sectors the model dropped, i.e. blank on every row whose reason cites a rank.
The defect in `rank_out_streak` is real and is now fixed; the reading of this book's rows was not.
The table no longer permits the confusion: the `rk` column falls back to the universe rank marked
`~11`, and the reason names the number it fired on (`ranked below top-10 (#11)`).

**Found while fixing it.** Run ids are `run_<YYYYMMDD>_<HHMMSS>` and `_rank_streaks` counted every
one, so re-running the pipeline twice in an afternoon wrote two "consecutive runs" — and
`rank_out_consecutive: 2` meant a single day of iteration could manufacture a SELL by itself.
Today's lake has exactly that: two runs stamped 2026-08-28. Only the last run of each date now
counts, which is what "consecutive review cycles" always meant; grid's streak drops 4 → 3 and stays
above the threshold on its own merits.

Also added: a `COLUMNS` legend line, and the six order-maps that restated the action enum by hand
(report, digest, dashboard) now import `PRECEDENCE` — a seventh action added in one place and
forgotten in another sorts to the bottom silently. Dashboard pill, precedence strip and copy updated.
435 tests green (+11), including one that pins the old `rank_out_streak([5, None]) == 1` assertion
inverted, with the reason written where the next reader will find it.


## A hurdle you can check instead of a forecast you cannot (2026-08-28, v4.2)

Step 4 of `docs/PLAN_v4_rigor_and_lean_metrics.md` (§2 A3 + A5) — the user's two questions,
*"¿renta vender?"* and *"¿parciales?"*, answered without asking the model to predict anything.

**A3 — `net_edge_eur` left the decision table.** It answered "does this trade pay?" by multiplying
the trade by `E[r | rank bucket]`, a mean over ~1 non-overlapping 63-day window, shrunk 86% toward
zero for exactly that reason. The result was ±€1 on a €900 trade, printed in a column beside a real
€11 tax bill. The number was never *wrong* — it was **unfalsifiable**, and per the plan's D5 it
turns actively harmful the moment the gate arms, because today's bucket table has `top3` at −0.056
sitting BELOW `rest` at +0.779.

Replaced by a **breakeven**, which needs no forecast: `friction ÷ capital actually moved`, where
friction is CGT + spread + fees — all observable today. New `leg_friction`, `breakeven_pct`,
`swap_ledger` and `rank_edge_evidence`; the table's `net€` column became `b/e%`, and so did the
dashboard's and the report's.

```
SWAP LEDGER — what each rotation costs to make, and the hurdle it must clear over 63d
  SELL   copper_miners                       €1,020  →  BUY  biotech_drug_development
         friction €15.47 (CGT €11.39 + spread €4.08)  →  BREAKEVEN 1.52%
  TOTAL  €1,412 rotated · friction €17.03 · weighted breakeven 1.21%
         · €42 of the sells pairs with no buy above the €150 ticket and lands in cash
  EVIDENCE for that spread: rank-bucket top3−rest = -5.84pp → NONE
         (~1 independent window(s) < the 3 the gate requires — the sign is not established)
```

Three properties the old number did not have. Every input is observable now. The output is a
**hurdle the user accepts or rejects with their own view** — which is the judgement a human should
make and a model should not. And it is checkable against the realized spread one horizon later,
which a point estimate never is.

The evidence line is deliberately blunt: it says `NONE` on a thin sample and `ADVERSE` when the
measured top3 sits below rest, rather than dressing either up. It is **not** a veto — the rule
still fires on its own trigger (rank-out, regime, exit watcher), and an unmeasured quantity must
never resolve to inaction. Stating the hurdle and stating that the evidence for clearing it is not
yet established are two facts; turning them into a softened verdict is the conservatism this plan
exists to remove.

Details that cost a second pass. The ledger pairs sells to buys largest-first and **pro-rates CGT
across the legs one sale funds** — charging each leg the full bill turned €20 of tax into €40 of
phantom friction. Legs below `min_ticket_eur` are skipped, since `size_trade` would refuse to print
that order anyway; the unpaired remainder lands in cash, which the CASH row already prices, and the
TOTAL line says so rather than letting the ledger quietly under-sum the sells.

**A5 — partials stop arriving as a surprise.** New `partial_rungs` reports, per held line, the
distance to **both** rungs:

```
PARTIALS — distance to each rung
  sector                            gain  rk  ladder +25% & rank ≥ 6 → trim 33%           overweight ≥ 4pp
  copper_miners                    +6.2%   —  needs +18.8% · rank unknown                 MET (+10.6pp)  → SELL LIVE
  pharma_large_cap                +12.8%   1  needs +12.2% · still a leader (rank 1 < 6)   needs +8.6pp more
  cybersecurity_commercial        +13.4%   4  needs +11.6% · still a leader (rank 4 < 6)   needs +8.1pp more
```

Both rungs, never a "nearest": they are measured in different units — points of TOTAL capital above
target versus % gain ON the position — and collapsing them to one number compares quantities that
are not comparable. The first cut of this did exactly that and the overweight rung won every row,
hiding the ladder entirely.

The rank leg is phrased as what it means. `rank_min: 6` fires once the model has **stopped** leading
a name, so pharma at rank 1 failing it is the rule working; a bare `rank ✗` read as a defect. A
**missing** rank never satisfies it — that would let a +80% position with no model rank trigger a
trim on absent data (the open B3 defect; two SELLs already fire on `rk = —` this run).

And the sizing vocabulary is now printed once, above the verdicts: `SELL = 100% of the line ·
REDUCE = 50% · TRIM = back to target (or 33% on a ladder rung)`. Three fractions are the entire
partial-sale language and they had never appeared together, so a reader could not tell which "TRIM"
halved a line and which shaved 4pp off it.

Both blocks are pure functions over the rebalance rows, so `review_report.py` and `run_digest`
rebuild them from the **persisted lake partition** instead of re-running the engine — which reads
VIX, and would have broken `review_report`'s own "fetches no price" contract. Report §3b/§3c,
dashboard column and copy, run digest and the review skill's Step 6 all updated. 424 tests green
(+10).


## The run says what it found once, in one file (2026-08-28, v4.1)

Step 3 of `docs/PLAN_v4_rigor_and_lean_metrics.md` (§5 D-a + D-b). No formula changed and no
threshold moved — this is entirely about what a review costs to run.

**D-a — four scorers were printing their whole working, every run.** `sector_scorer` and
`catalyst_scorer`, `momentum_engine` and `intensity_engine` each rendered a human table and then
appended `--- JSON output ---` plus the complete result dict, unconditionally. All four already
had `--json`. So the dump was never the machine path — it was the table's toll, and across the
universe it is enormous: `sector_scorer --all --json` is **97 KB**, `catalyst_scorer --all --json`
**78 KB**, of which the ranking reads four fields per sector and the review reads five. Deleted in
all four; `--json` is untouched and is still the way to get the full trace for one sector.

Two new `--digest` paths for the calls that are made across the whole universe every run:

- `sector_scorer --digest` → `rank · sector_id · composite · momentum · vehicle`, one line each.
  The vehicle comes from `snapshot_repo.primary_etf`, so a sector with no buyable UCITS wrapper
  reads `— (not buyable)` in the same place a decision would look for its ticker.
- `catalyst_scorer --digest` → `alignment · regime_state · confirms · contradicts · ⚠ regime review`.
  The per-event decay trace that made up the other 76 KB is in the lake and re-derivable per sector.

`dislocation` and `entry_timing` needed no code — they already printed a table by default and
`score_run.sh` was asking them for `--json` anyway. Dropped. And the heatmap's separate
`momentum_engine --json` pass (12.5 KB) computed nothing `sector_scorer` does not: it re-scored
the same snapshot to print a column the composite already carries. Removed from the skill, kept as
a CLI for the raw 1m/3m/6m returns.

Measured on this run, the deterministic payload a review pushes through context:

| call | before | after |
|---|---:|---:|
| `sector_scorer --universe` (review Step 3) | 100,086 | 2,214 |
| `momentum_engine` (heatmap) | 12,577 | — |
| `sector_scorer --all` (heatmap) | 97,275 | 2,214 |
| `catalyst_scorer --all` (score_run) | 77,646 | 2,090 |
| `structural_monitor --all` | 2,047 | 2,047 |
| `dislocation` | 9,718 | 2,261 |
| `entry_timing` (scoped) | 5,152 | 1,483 |
| **total** | **~297 KB** | **~12 KB** |

≈ 74k tokens a run, recovered on every run from here. Nothing is lost: everything deleted is either
persisted in the lake or one scoped `--json` away.

**The one merge the plan asked for and did not get.** D-c proposed folding `structural_monitor
--all` into the catalyst digest on the grounds that it duplicates `regime_state`. It does not.
`structural_monitor` is indexed by STRUCTURAL catalyst and carries `intensity`, the weak-indicator
count and the intensity drop against the degrade threshold; the digest is indexed by SECTOR. Only
the regime label overlaps, and at 2 KB the rest is the cheapest fundamentals gate in the pipeline.
Kept, with the reason recorded here so it is not re-proposed.

**D-b — one state file per run.** New `catalyx/store/run_digest.py`, called last in `post_run.sh`,
writes `data/reports/run_<date>.json`: book, attention, work list, the scan deltas' counts,
ranking + rank moves, the NAV comparison including `execution_alpha_pp`, book metrics,
per-position metrics, the whole rebalance table with its cash constants lifted out of the rows,
catalyst exposure, and the override tally. It is read-only over the lake and the Tier-1 files —
no scorer, no fetch, nothing persisted but itself — so it assembles only what the run already
computed and costs nothing to re-print.

The point is not the file; it is what the skill no longer has to say. The review was threading
`state_<date>.json`, `scan_deltas_<date>.json`, three stdout digests, the NAV lines and the
rebalance table through its own context by hand, and every step ever added made it restate more of
that plumbing. Now it reads one file, and `run_digest` re-prints a ~25-line summary for free
instead of re-running a CLI. Deliberately NOT merged: `scripts/review_report.py` keeps reading the
lake directly rather than the digest, so the report can never quote a digest that went stale
between the two calls.

414 tests green (+10) — including a source-level test that no scorer may reintroduce an
unconditional JSON dump, since that regression fails nothing at runtime.


## The target book closes, and the benchmark is read the right way up (2026-08-28, v4.0)

First two steps of `docs/PLAN_v4_rigor_and_lean_metrics.md` (§2 A1/A2, §2 A6 + §0.2 D7). The plan
was written after measuring the pipeline end to end; these are the two defects everything else was
being compared against.

**D1 — the benchmark line was printed with the wrong meaning, and the conclusion was inverted.**
`vs_benchmark_pct` is `nav − benchmark_nav`, a DIFFERENCE in index points. `nav_engine`'s real-book
CLI printed it under a bare benchmark label:

```
TWR (selection, vs benchmark) = -0.9555%   [SPY -5.3939%]
```

which reads as "SPY fell 5.4% and we only fell 1%". The lake says `benchmark_nav = 104.4384`: SPY
returned **+4.44%** and the book was **5.39pp behind it**. The misreading had already reached prose
— CHANGELOG v3.5 above repeats it verbatim. All three CLIs now print three named numbers through
one renderer (`_bench_line`): the book's return, the benchmark's own return, and the differential
in pp. `last_benchmark_return_pct` ships on every result dict.

**A second defect was hiding inside it.** `lake_query.portfolio_compare` takes the latest stored row
per portfolio and sorts by return — but model NAVs are only rebuilt when `post_run.sh` runs, so the
model rows were stamped 2026-07-30 against a real row stamped 2026-08-27. Five curves side by side,
stopping on different days, sorted by returns measured over different windows. Rebuilt to a common
date the ranking reverses:

| book | TWR to 2026-08-27 | vs SPY (+4.44%) |
|---|---|---|
| momentum (model) | +3.89% | −0.55pp |
| catalyx (model) | +3.88% | −0.56pp |
| low_crowding (model) | +2.90% | −1.54pp |
| equal_weight (model) | +2.61% | −1.83pp |
| **real** | **−0.96%** | **−5.39pp** |

New: **execution alpha**, the real book against the model book it implements, computed only when
both curves end on the same date (a calendar gap must never be reported as skill). Today
**−4.84pp** — the rule table has been right and the deviations from it expensive. That number did
not exist anywhere in the pipeline, and it is the one v3 was built to answer.

**D2 — the target book leaked 36% of its weight, so the deployment rule was unreachable.**
`portfolio_holding` sums to exactly 100%. `build()` dropped the names that are not buyable today
and computed `target_eur = weight_pct/100 × deployable` on the survivors, so the dropped weight
evaporated: the deploy rule asked for €7,000 at work, the targets summed to €4,476, and executing
EVERY rule action left the book at 38% against a 70% rule. The deployment ratio is this module's
anti-cash-hoarding device; a missing renormalization made it arithmetically impossible to satisfy.

Fixed in three ordered steps, none of which silently concentrates the book:
1. **Substitution first** — a dropped name's weight goes to the next investable, buyable sector by
   composite from the same run's `sector_snapshot` (`_substitutes`). The model asked for 10 lines
   and the universe has 26 to fill them from; a universe cut is not a decision to concentrate.
2. **Then rescale the residual** (`close_target_weights`, pure + tested), capped by the new
   `max_dropped_pct` (40%). Past that the rescale stops and the run says `MODEL BOOK INCOMPLETE`
   and closes deliberately below the rule — a ranking whose top decile is unbuyable is a scoring
   problem, not a weighting one.
3. **Re-cap per position** with the model builder's own `water_fill`; what the cap sheds becomes
   cash, never another position.

`rank_in_portfolio` is now re-derived from composite over the book that actually exists. The stored
ranks described a 10-name book after 4 had been removed, and `add_if`/`buy_if` read them as "does
the model still call this a leader".

**The table now closes.** `Σ target% + cash% = 100` and `Σ actual% + cash% = 100`, both pinned by
tests, with **CASH as a priced row carrying its own action** (`DEPLOY €5,454`) instead of a
footnote nobody has to answer.

**Side effect, deliberate and worth stating.** `deploy_ratio` counts intact sectors inside the
model's top-8, and the leak had been truncating that count to the 6 survivors. On a complete book
it reads 8 → the rule moves **70% → 85%**. No threshold was edited; the frozen rule is simply being
evaluated on the book it was always meant to see.

**D3 — the table recommended vehicles that cannot be bought.** It printed `BUY
biotech_drug_development €891` against **IBB** and `BUY ai_infrastructure €567` against **AIPO**,
both US non-UCITS. Two causes, both fixed: the ticker was read off a `portfolio_holding` row frozen
before the 08-27 universe cut (now re-resolved at table time from `etf_universe.yaml`), and
`snapshot_repo._primary_etf` fell back to the non-UCITS pool when a sector had no UCITS entry (now
`primary_etf`, UCITS-only, returning `None` — a fact a caller can act on, where a US ticker is a
recommendation that looks actionable and is not). A sector missing a buyable vehicle is dropped
through the same substitution path as a non-investable one. Pinned by a test in the shape of
`BANNED_ACTION_WORDS`: no row with `rule_action != HOLD` may name an unbuyable vehicle.

Today's table: `BTEC.L`, `XAIX.DE`, `WCLD.L`, plus `GLUX.SW` / `RBOT.L` / `JEDI.DE` / `IH2O.L`
substituted in — 10 model names, all buyable, closing at 100%.

**D7 — report plumbing that rendered blank or misleading.** §6 catalyst exposure asked for
`n_sectors` where the ledger returns `sectors` (the column was `—` on every row since it was
written) and carried neither a % of capital nor the cap — the only two numbers that make it a
CHECK; it now shows both plus headroom €, with the breach flagged inline. §8 asked for
`check_frequency`/`overdue_by`, neither of which `freshness.overdue()` emits; now `cadence`,
`limit` and a derived `overdue by`, with `⚠mislabel` surfaced. New `weights.correlated_catalyst_cap()`
normalizes `max_combined_pct: 0.20` to the percent its name promises — every caller comparing an
exposure of `10.0` against `0.20` had a check that could never fire.

404 tests green (+8).

## Real book: time-weighted curve, MWR, and a sample-size gate (2026-08-28, v3.5)

**Trigger.** The user asked a methodological question: a portfolio updated every N days — are
Sharpe and volatility being computed over the whole series, or only over the update points?

**The answer, and the worse thing behind it.** The series was already daily: `nav_engine` builds
from the daily price frame, and the model leg's `mode='live'` already chained segment returns
across rebalances correctly. But `compute_real_nav` did something the model leg never did — it
read `movement_repo.positions()` (a SNAPSHOT of what is held today) and projected those
quantities backwards to the first movement, dividing by today's total cost:

```python
nav_series = 100.0 * value / total_cost   # h["qty"] of TODAY, applied to EVERY date
```

The book was built in three tranches (€1500 on 06-05, +€1000 on 06-08, +€500 on 06-16), so the
curve modelled €3000 of exposure from day one and gave SEMI.L — bought on the 16th — a full
position on the 5th. Every number derived from that curve described a portfolio nobody held, and
v3.4 had just widened the blast radius: `position_metrics._book_metrics` began sourcing
vol/Sharpe/maxDD/beta/corr/tracking-error from it and persisting them to `book_metrics` for
month-over-month comparison.

**Fix — derive the path from the ledger, not from a snapshot.** Three primitives in `nav_engine`:

- `daily_ledger(movements, index)` — qty per ETF and external EUR flow per DAY, from ALL
  movements. Closed positions included: they own the stretch of curve during which they were
  actually held, and dropping them is survivorship bias in the track record.
- `twr_series(value, flow)` — time-weighted NAV, `r_t = V_t / (V_{t−1} + F_t) − 1`. Contributions
  are neutralized, so a €500 top-up is not read as performance.
- `xirr(flows)` — money-weighted IRR by bisection on NPV; returns None rather than diverging when
  the flows do not bracket a root.

**Three questions, three numbers, each labelled.** A single "return" for a book that receives
contributions has to lie about at least two of them:

| | value | answers |
|---|---|---|
| TWR | −0.96% (SPY −5.39%) | is the selection any good? |
| MWR (IRR, ann.) | +7.16% | what did MY money earn, given its timing? |
| vs cost | +1.53% | what the broker shows |

**Convention: START-of-day.** Booking flows at the close (`(V_t − F_t)/V_{t−1}`) silently drops
the first day of every new position. That is not a rounding difference: 4COP.DE was bought at
60.62 on 2026-06-05 and closed at 56.41 the same day, a real −6.9% on €1000 that the end-of-day
convention erases. Between two defensible conventions, the one that cannot flatter the record by
construction is the right default.

`value_eur` / `flow_eur` / `net_contributed_eur` are persisted per date and `mwr_pct` on the final
row, so the divergence between the three numbers is auditable instead of asserted.

**Found while fixing — execution prices logged from stale quotes.** Three of the five movements
carry a `price` that matches the PRIOR session's close, not that day's (IUHE.AS to the cent:
6.918 = 6.918; 4COP.DE 60.62 vs a 60.57 prior close and a 56.41 same-day close). Because `qty` is
derived from that price, either the share count or the amount is wrong, and only the broker
statement can say which. New `execution_price_checks` reports it as a warning on every real-NAV
run — repairing it silently would invent a cost basis, and picking the convention that hides it
would be the same flattering this entry just removed.

**Sample size, stated instead of implied.** No construction fix makes 59 daily observations
sufficient: the standard error of an annualized Sharpe is ≈ √((1+Ŝ²/2)/n)·√252, which puts this
book at **0.95 ± 4.05** (95%). New `sharpe_ci95` + `metrics_reliability` in `position_metrics`,
`n_days`/`sharpe_ci95`/`reliable` from `build_site._series_metrics`, and config `risk_metrics`
(`min_days_for_sharpe: 120`, `min_days_for_vol: 30`). The dashboard prints the interval beside the
ratio and flags "59/120 — indicative"; the curve's shape stays visible, the false precision does not.

**No daily capture required.** The whole series re-derives from scratch at any time: qty from the
movement files (written the day a trade happens — the only irrecoverable input), prices and FX
from the `prices` cache, which backfills whatever history yfinance still serves (~4.5 years for
these vehicles, `auto_adjust=True`, so total return). Recomputing in `post_run.sh`, which already
invokes it, is enough.

**Found in the follow-up audit.** Three more, all real:

- **The gate covered one of four render sites.** The dashboard prints Sharpe in four places, and
  only the portfolio-detail strip had been fixed — including the Positions page, the one view of
  the actual book. All four now carry the interval or the "indicative" flag.
- **`daily_ledger` did not cap an oversized sale.** `movement_repo.positions()` caps a `close`
  larger than the held quantity and warns; the ledger did not, so a hand-authored typo would have
  left a negative qty and silently marked a short position across the curve.
- **`lake_query.portfolio_compare` ignored `mode` (pre-existing, v3.4-era).** backtest/live/forward
  share a portfolio_id AND a last date, so the tie broke arbitrarily and the review report could
  show a model book's HYPOTHETICAL backtest return beside the real book's actual one. It was:
  the model books read **+25.0% / +24.0% / +19.0%** where their live walk-forward record is
  **−3.1% / −4.4% / −4.8% / −4.9%**. With live preferred (the rule `position_metrics._nav_series`
  already applied), the real book's −0.96% TWR is the best of the five, not the worst. `mode` and
  `mwr_pct` are probed rather than assumed, so older partitions still read.

**Files:** `catalyx/execution/nav_engine.py`, `catalyx/execution/position_metrics.py`,
`catalyx/config/{scoring_weights.yaml,weights.py}`, `scripts/build_site.py`, `site/app.js`,
`catalyx/store/lake_query.py`, `scripts/review_report.py`,
`tests/unit/{test_nav_and_trades,test_position_metrics,test_lake_query}.py`. 396 tests green (+29).

---

## Position & book metrics + the dashboard Rebalance tab (2026-08-28, v3 Phase 2 completion)

**Why:** the book was measured in exactly two ways — `invested_eur` (what went in) and a NAV curve
(how the whole thing moved). Everything between them was either absent or re-derived by hand inside
a review and never written down, and a number recomputed conversationally each month cannot be
compared to itself across months, which is the only thing that makes it useful.

**New `catalyx/execution/position_metrics.py`** → lake `position_metrics` + `book_metrics`, one row
per held sector per run, run by `post_run.sh` immediately before `rebalance` (it explains the rows
rebalance is about to act on). Three of its numbers did not exist anywhere before:

1. **The price/FX split.** A EUR investor holding a GBP or USD vehicle owns two positions — the
   sector thesis and the currency — and `unrealized_eur` cannot say which one is working. The
   decomposition is an identity, not an estimate:
   `price = qty × (P_now − P_entry) × fx_entry`, `fx = qty × P_now × (fx_now − fx_entry)`, and a
   third **named** `basis_residual_eur` for fees and cost-basis rounding. The three sum exactly to
   the EUR P&L. `entry_fx` is implied by the cost basis (EUR/unit ÷ native/unit), so the rate used
   is the one actually paid and no FX history is needed; an EUR listing is pinned to 1.0, otherwise
   fee rounding prints a phantom currency effect on a domestic vehicle.
2. **Score drift** — today's composite/rank vs the point-in-time `score_context` the position was
   opened on. Price is the market's opinion; drift is your own model's. First run:
   `grid_infrastructure_utilities` at **−37.4**, the position the exit watcher already flags.
3. **Max drawdown from PEAK**, not from cost. `exit_watcher` measures against the cost basis
   because its stops are written that way; a position that ran +40% and gave back to +5% reads as a
   healthy +5% there and a −25% round trip here. Both true, and only the second explains the feel.

Book level: deployment, HHI, FX exposure by listing currency, and vol / Sharpe / max DD / beta from
the real NAV series, plus tracking error and overlap vs the `catalyx` model.

**Two defects the plan did not anticipate, both surfaced by running it.**
*(a) The NAV table splices three curves.* `portfolio_nav` stores `backtest`, `live` and `forward`
rows under the SAME `portfolio_id`; reading them all sorted by date interleaves two different
curves and manufactures ±18% daily moves out of nothing — the first run reported a **95.5% tracking
error** that way (22.3% once fixed). `_nav_series` now takes a mode and dedupes by date, and a test
pins it. *(b) Active share was quietly answering a different question.* The textbook ½Σ|w−b|
assumes both books sum to 100%; ours do not (the real book is a % of total capital, the model is
fully deployed), so it measures "how far apart are the two books" and not "how much of the model do
we own". Both are now reported under separate names — `active_share_pct` 49.3% and
`model_overlap_pct` **15.9%** — because one number pretending to answer both is how a book ends up
looking diversified and being concentrated. Same reason `corr_vs_spy` is now printed beside
`beta_vs_spy`: the real book's beta is exactly **1.00**, which reads as "we track the index" and is
a coincidence of being twice as volatile at half the correlation (0.50).

**Dashboard `#/rebalance` tab** (§3.5), baked by `build_site.py` from lake `rebalance` +
`position_metrics` + `book_metrics` + `override_log`: target vs actual with the action, € to move,
CGT and net edge per row; the per-position measurement table; the book-shape strip with the
currency split; and the override log with its authors. Sectors that are no longer buyable are
flagged in red rather than dropped. English-only, recommend-only — nothing on the page executes.

375 tests green (+17). **This completes the v3 plan.**

## Context & maintenance hygiene (2026-08-28, v3 Phase 4)

**Why:** `CLAUDE.md` had grown to **54.9 KB**, and it is loaded by the main session AND by every
subagent a review spawns — so a scan, eight studies and four phase subagents paid for it a dozen
times per run. Most of that weight was the module table: ~11 KB answering "which module does X and
what is its CLI?", a question that arises only when you are about to touch a module.

**CLAUDE.md 54.9 KB → 19.7 KB (−64%).** The module inventory moved **verbatim** to
`docs/MODULES.md`; `Recent Changes` collapsed from five multi-paragraph rows to five one-line rows
pointing at this file; the storage/architecture prose compressed; the duplicated
"Files Claude reads for each task" table merged into "Key Files"; the "Feedback Loop — Review
Checklist" section deleted as a third copy of the pipeline order. **The ≤15 KB target in the plan
was not reached, deliberately.** What is left is rules that bind on every run — broker reality
(PRIIPs), the one-driver rule, Spanish CGT, the five AI-scoring rules, the fixed-precedence action
enum — and a rule moved into a file nobody opens is not a rule, it is a rule that will be broken
politely. Two dangling references to a `docs/SPEC_v1.1.md` that does not exist were repointed at
`tax_engine.py` and `CHANGELOG.md`.

**Deleted for being wrong, not merely long.** CLAUDE.md's "Data files state" block still described
June — `theses/ ← vacío`, one catalyst event, a `catalyst_dashboard_20260603.md` — as the current
state of `data/`. And the review skill's Step 1.5b spelled out every lifecycle transition rule
(archive a spent event, dormant a weak structural, promote a repeat) for the LLM to apply by hand,
six weeks after `catalyst_lifecycle.py` took them over deterministically, closing with a note that
"the deterministic home is a future module". Stale documentation of a solved problem is worse than
no documentation: it invites solving it again, by hand, differently.

**Review skill 651 → 296 lines (−55%).** Every rule now owned by Python was deleted from the prose
and replaced by the command plus what to report. What was kept is exactly what needs judgement: the
study work-list triggers, the regime/opportunity/entry-timing reads, the evidence standard per
assumption, the override discipline, and the two AskUserQuestion steps.

**New `scripts/review_report.py`.** Most of `monthly_review_<date>.md` was Claude re-typing numbers
it had just read — the ranking, the NAV lines, the rebalance rows, the exposure ledger, the tax
snapshot, the overdue indicators. Re-typing costs tokens and introduces the one error class a review
cannot absorb: a transcription that silently disagrees with the lake it came from. The generator is
read-only over the lake (no scorer, no price fetch, no persist) and emits those sections directly,
leaving explicit `<!-- CLAUDE: … -->` markers for the parts that are actually reasoning: macro
context, executive summary + non-obvious finding, the evidence line per position, override reasons,
and the two decision blocks. It also **marks ranked sectors that are not investable today** rather
than dropping them — 7 of the current top-15 come from a run that predates the 08-27 universe cut,
and a top-of-table row telling you to buy something unbuyable is not a neutral row.

**`pre_run.sh --check` — the weekly heartbeat.** Runs the same deterministic chain into the log and
prints only if a human is needed: a rule action, a position the exit watcher flags (named), a stale
catalyst verdict, a pending lifecycle transition, or a book move past ±10%. Exit 0 = quiet,
10 = attention. Search-free, so it is safe unattended — and silent by construction, because a job
that speaks every week teaches you to ignore it. First live check: 6 rule actions, 2 flagged
positions, 24 stale verdicts, 1 pending transition. The plan's weekly **cron was offered and
declined** — the heartbeat is run by hand.

358 tests green (unchanged — this phase moved prose, not logic).

## Thresholds frozen + the override log gets scored (2026-08-28, v3 Phase 3)

**Why:** the rule table shipped with its numbers marked "DRAFTS UNTIL FROZEN BY THE USER", and a
threshold that can move whenever a run dislikes its own output is not a rule — it is a mood with a
number attached. The user delegated the call ("congélalos, tú tienes permiso"). Freezing is the
step that makes §4.1 binding; the four changes made at the freeze all push the same direction the
whole v3 plan does, away from the model's default conservatism.

| | draft | frozen | why |
|---|---|---|---|
| `deployment.base` | 0.60 | **0.70** | the ratio applies to capital already sized for risk in `track_record.yaml`. Sizing happened when that number was set; idle cash inside the envelope is not prudence, it is an unchosen zero-return position |
| `deployment.floor` | 0.30 | **0.40** | binds only with nothing intact AND VIX > 30. It floors the TARGET and never forces a buy — a book whose positions are all SELL by rule still goes to cash through the SELL rows |
| `profit_ladder` | 2 rungs | **1, rank-coupled** | the `rank_min: 0` rung trimmed a +50% position the model still ranked #1: the disposition effect wearing a discipline costume, and the opposite of a momentum mandate. Concentration is already bounded twice, by `max_position_pct` (12%) and by `trim_if.overweight_pp_min` |
| `sell_if_any.rank_out_of_top` | 12 | **10** | = `portfolios/catalyx.yaml max_positions`, i.e. "the model book no longer holds it". 12 was chosen against a 53-sector universe; after the 08-27 cut to 26 it meant "sell the below-average half" |

Live effect: the rule now says deploy **70% → €7,000** against €3,046 actually at work —
**€3,954 under-deployed**, up from €2,954 under the draft. Same 6 non-HOLD actions.

**The two open design questions were answered, not deferred.** *Ladder vs trailing rank
(plan §7.2):* trailing rank is the default exit; the ladder survives only rank-coupled, pinned by a
test that fails if a rank-free rung is ever re-added, so that decision has to be argued again rather
than drifting back. *Who may override (§7.3):* **both the user and Claude, both scored.** Making the
user the sole author sounds safer and is not — the model would keep proposing the same conservative
deviation as prose inside the review, unlogged and unscored, which is precisely the failure the
table exists to remove. An override is cheap to record and expensive to repeat badly.

**Override scoring (§4.3), the part that keeps the escape hatch honest.** An override log nobody
prices is a comment field. `rebalance.score_overrides` compares the two worlds as a difference of
EXPOSURES — everything else about them is identical, so their P&L difference is exactly that gap
times the vehicle's EUR return since the run:

    override_edge_eur = (trade_eur_chosen − trade_eur_rule) × forward_return_pct / 100

Declining a −€500 SELL is +€500 of retained exposure: a 10% fall means the override cost €50.
Declining a +€500 BUY carries the opposite sign. `rule_cost_eur` (the CGT + spread the rule action
would have paid) is reported *beside* the edge and never added into it — deferring tax is not
earning it, and folding a deferral in would let "I didn't sell" win by arithmetic. An override
younger than `score_after_trading_days` (21) is PENDING with its age shown, never scored: a
four-day price difference is a coin, and letting it into the tally would suspend on noise.
`log_override` now stores `chosen_trade_eur` rather than inferring it, refuses an author outside
`authors_allowed`, and refuses an empty reason. Claude's suspension is arithmetic — net-negative
over ≥5 scored overrides and `--author claude` raises.

**Wiring.** New CLI subcommands `rebalance override <sector> <action> --reason … --author … --trade-eur …`
and `rebalance overrides` (the tally); the bare invocation is unchanged so `post_run.sh` and every
existing call site keep working. `pre_run.sh` prints the tally as its last block — before any
reasoning, so a review knows what its own last "let us give it another cycle" cost before it
proposes the next one. `build()` carries the tally with the table, and `render()` emits a
Python-generated `SUMMARY` line (deployed % vs rule and floor · N rule actions · override tally)
because a summary composed by hand is a summary that quietly drops the inconvenient number.

**Review skill (§4.4).** Step 6 gets the real override command including `DEFER` — "revisit next
cycle" IS a deviation and is the form conservatism usually takes, invisible unless logged. Step 9:
a Wait/Skip on a sector the table marked BUY/ADD is logged as a user override, not to police the
decision but to price it later; a deferral that is never recorded cannot be wrong, which is exactly
why it accumulates. The report template's Open Positions table now has `rule_action` / `trade €` /
`net edge €` columns with the banned words named inline, plus an "Overrides this run" table, and the
executive summary's mandatory first line is the `SUMMARY` row verbatim.

358 tests green (+12).

## Scan → update in one hop, and the kill list (2026-08-28, v3 Phase 1 completion)

**Why:** applying a scan was the most repetitive thing the review did — one conversational turn per
indicator across ~44 indicators, each re-deriving the same nine bookkeeping steps. And it was
**drifting**: the skill still instructed Claude to append the prior reading to the inline
`value_history`, but schema 1.4 moved history to the parquet lake and `intensity_engine` reads the
lake first. Every hand-applied observation therefore looked recorded while the empirical percentile
never saw it — a failure invisible by construction.

**New `catalyx/store/indicator_update.py`** (`set` / `batch` / `maturity`): shifts
`current_value → last_value`, stamps `last_date` and `status_last_reviewed`, archives the PRIOR
reading to the lake, and recomputes intensity ONCE per touched catalyst. Idempotent — re-applying
the same observation is a no-op, because `intensity_engine`'s percentile weights by row count, so a
duplicate silently re-weights history. Direction-aware: `weakened()` and `crossed_weak_threshold()`
read the indicator's own `direction`, and only when a reading actually moves against the thesis are
the catalyst's deactivation conditions printed verbatim for the human to judge. (The first cut
matched condition text against `indicator_id` — no condition in any YAML contains one, so it would
have been dead code that nobody would notice never firing.)

**One file, three consumers.** `/catalyx-scan` now writes `data/reports/scan_deltas_<date>.json`
(`catalyst_id`, `verdict`, `evidence`, `source`, optional `indicators[]`), read by
`indicator_update batch` (values), `catalyst_review batch` (freshness stamps) and
`catalyst_lifecycle --deltas` (reversals, which are evidence and are never inferred). Review Step 2
is now those three commands instead of N conversational updates.

**Merged catalysts were reaching two places they must not.** (a) `run_state`'s work list put
`struct_copper_datacenter_demand` on MUST — a `status: merged` catalyst that `compute_all()` skips,
i.e. a WebSearch spent on something nothing scores. (b) `exit_watcher.catalyst_freshness` read that
same merged file's `status_last_reviewed`, which the 2026-08-27 merge had itself just stamped — a
fresh-looking date for a thesis nobody re-verified, which is precisely the false all-clear that
function exists to prevent. New `structural_catalyst_repo.resolve`/`resolve_all` follow
`merged_into` (chains handled, cycles terminate) and both call sites now ask the survivor. The
freshness result carries `evaluated_ids` + `merged_from` so a row never reads as a verdict on an id
whose file was never opened. Movements keep their original attribution — that is the historical
record and it stays.

**Scan slimmed:** C0 14 → 6 queries (the four commodity and four macro queries returned overlapping
result sets — one search engine does not reward splitting "LME copper" from "gold price"), Discovery
7 → 3 (the pass is a net, not a census: a theme big enough to matter shows up in any framing), and
C2's fixed eight sector queries replaced by the `state_<date>.json` work list — `must_reverify`
always, `should` budget permitting, `optional` never. Budget ≈ 15 searches, was ~40.

**Kill list.** `/catalyx-dashboard` removed as a review step (last written 2026-06-30, skipped in
practice since; the digests + site + report §1 carry it three times over). Heatmap step 2 no longer
re-reads `sector_taxonomy.yaml` + `scoring_weights.yaml` + `etf_universe.yaml` (2,221 lines,
~60–90k tokens per run to reproduce facts the Python already applies); step 5's per-sector
`sector_scorer --crowd N` re-runs are gone because `snapshot_repo.record` already applies crowding
from `narrative_maturity` via the same map — the ranking is now read back from the recorded run
(`lake_query ranking`), which also removes the drift between the displayed table and the persisted
one. The per-top-5 prose block became one line each, with the "non-obvious finding" written once
for the book. `user_rank_multipliers` deleted (deprecated in v1.5, read by nothing, re-read as
context every load). `data/reports/heatmap_blocks/` and `llm_vs_pipeline_stability_20260604.md`
moved to `experiments/` **and `snapshot_repo._BLOCKS_DIR` repointed** — moving them alone would have
made `rationale_md` silently None.

**One kill-list item was NOT executed as planned.** Review Step 11 (watch-only triggers) was listed
as "dead". It is not: after the 2026-08-27 cut there are ~30 watch-only sectors and a fired trigger
is precisely how one returns to the investable universe. What was actually wrong is the *sweep* — 30
WebSearches to report "no change" 29 times, the worst cost-per-decision step in the review. Step 11
is now findings-driven: a watch sector is checked only when the scan's Discovery pass surfaces a
matching theme, or a `retired_reason` is directly addressed. Usually one line, zero extra searches.

346 tests green (+20).

## Rebalance engine — target vs actual, in € and after tax (2026-08-28, v3 Phase 2)

**Why:** the review ended in prose. The pipeline computed a model book to two decimals and then
the recommendation was an adjective — "hold", "watch it", "consider a small add". Two failures in
one: no number ever said HOW MUCH, and an LLM asked for a verdict drifts to whichever option
cannot be blamed, which in a portfolio means holding cash and holding losers. The real book was
30% deployed (€3,046 of €10,000) with no decision anywhere that had chosen that.

**What shipped:** `catalyx/execution/rebalance.py` — per sector: `target_pct` (model weights ×
deployable capital), `actual_pct` (marked to market in EUR), `gap_eur`, a `rule_action` from the
decision table, `trade_eur`, `realized_gain_eur`/`tax_eur` (Spanish CGT, YTD-aware),
`cost_drag_eur`, `expected_edge_eur`, `net_edge_eur`. Book level: deployment ratio with its
inputs, turnover, HHI, cash before/after. The action enum is `SELL > REDUCE > TRIM > ADD > BUY >
HOLD` in fixed precedence — there is no `watch`, `monitor`, `consider` or `optional`, and
`BANNED_ACTION_WORDS` is asserted by a test so the ban is enforced rather than hoped for.
Deviating is allowed only as a recorded override (lake `override_log`: run, sector, rule action,
chosen action, reason, author) so it can be scored against the rule it replaced.

New config `rebalance_rules` in `scoring_weights.yaml` (thresholds are DRAFTS until the user
freezes them) + `weights.rebalance_rules()`. New lake tables `rebalance`, `override_log`,
`calibration_bucket`. `calibration.py` gained rank-bucket forward returns (`bucket_returns`,
`bucket_of`, `shrink_factor`, `expected_returns`) — an IC is a correlation and cannot be
multiplied by a trade size; a bucket's mean forward return can. Wired as the last step of
`scripts/post_run.sh`, on stdout in full: it IS the review's Step 6/9 table.

**Three fixes forced by running it against the real book — all of them anti-conservatism:**

1. **The after-tax gate cannot bind on an unmeasured edge.** With ~1 independent calibration
   window E[r]≈0, so `net_edge = −(tax + spread)` and every taxable sale fails — the gate would
   have become a permanent, invisible ban on ever taking a profit. It now blocks only once
   calibration has `min_windows_to_gate` (3) independent 63d windows; below that it prints the
   cost and stands aside. An unmeasured quantity must never acquire a veto.
2. **The gate binds sales, not purchases** (`net_edge_gate.applies_to_purchases: false`). A sale
   pays CGT now and irreversibly — that is the user's "¿renta vender?". A purchase out of idle
   cash pays only the spread, and gating it on a noise-grade edge estimate would reimport the
   conservatism through the back door. Cash drag is a certain cost; the edge estimate is not.
3. **A stale catalyst verdict alone does not trim a winner.** `exit_watcher.reverify_required`
   fires on staleness even with no drawdown (`drawdown_overlay_action("clear", "very_stale", …)`
   → `warn, True`), so the first cut halved a +13.4% cybersecurity position because its YAML was
   60 days old. REDUCE now requires staleness **and** a drawdown tier of reduce/exit — the
   2026-08-04 doctrine as actually written. Staleness alone surfaces as a row `flag`, not an
   action.

**Also fixed while wiring it:** model books are read from the last recorded run, which predates
the 2026-08-27 universe cut — 4 of its 10 names (`cybersecurity_defense`,
`genomics_precision_medicine`, `longevity_biotech`, `semiconductors_equipment`) are no longer
investable. They are dropped from the trade list and NAMED in a warning, because a BUY
recommendation for an unbuyable sector is worse than none: it looks actionable. A second warning
fires on rank-streak SELLs, whose stored ranks were recorded when the scored universe was a
different size ("outside the top-12" is not the same cut across universes). Bucket assignment
uses the **universe composite rank** (`sector_snapshot.rank`), not `rank_in_portfolio` — the
model book ranks only its ~10 selections, while the buckets were measured across all investable
sectors; feeding one into the other compares two different orderings.

**First live output** (offline cache, model book from `run_20260728_103246`): committed €10,000,
deployed €3,046 (30%), rule says deploy 60% → €6,000 (5 intact leaders that are investable
today), so the book is **€2,954 under-deployed**. 6 non-HOLD actions. 326 tests green (+30).

## Schema migration — `sector_study` 1.2 → 1.3 (2026-08-27)

**Why:** a 25 KB sector study fed the scorers exactly two fields — `catalyst_scorer` reads
`active_catalyst_ids`, `snapshot_repo` reads `narrative_maturity` (→ crowding). Nothing in the
codebase read `demand_drivers`, `etf_analysis`, `key_metrics_to_monitor`, `cycle_position`,
`supply_constraints` or `historical_catalyst_performance` (verified by grep across
`catalyx/`, `scripts/`, `site/`). Yet `demand_drivers` and `etf_analysis` were REQUIRED, so every
refresh had to be a full dossier: ≈45k tokens and 6 WebSearches per sector, ~350–450k tokens per
review. See `docs/PLAN_v3_lean_pipeline_rebalance.md` §2.5.

**Changes**
- `study_type` gains **`core`** (now the default): the cheap, decision-relevant refresh —
  `active_catalyst_ids`, `narrative_maturity` + `narrative_notes`, `cycle_position`,
  `key_metrics_to_monitor`, `risks`, `last_updated`. ~0.7–3 KB, 2 WebSearches.
  `full` is unchanged and stays the deep dossier (creation, pre-open, quarterly).
- `demand_drivers` + `etf_analysis` removed from top-level `required`, and re-required
  **conditionally** via `allOf/if-then` when `study_type == "full"` — so a study that claims to be
  full still has to carry them.
- `etf_analysis` marked **`deprecated: true`**: it duplicated `catalyx/config/etf_universe.yaml`,
  which is the single source of truth and the only one any code reads (`snapshot_repo._primary_etf`).
  Kept readable for one major version per the Schema Change Protocol.
- `schema_version` `const: "1.2"` → `enum: ["1.2", "1.3"]`. **No data migration needed** — all 26
  existing studies stay tagged 1.2 and validate unchanged; new/refreshed studies write 1.3.

**Two pre-existing validation bugs fixed in passing** (schema validation for studies was
effectively dead — 16 of 26 files failed against 1.2, before any change here):
- `$schema` was not declared as a property while `additionalProperties: false` was set, so every
  study carrying the repo-wide `"$schema"` pointer was invalid. `movement.json` already declared
  it; sector_study now follows the same precedent.
- Underscore-prefixed human annotations (e.g. `_universe_v2_note`) now validate via
  `patternProperties: {"^_": …}` instead of failing the whole document.
- In the deprecated `etf_analysis` block, `ter` / `aum_m_usd` / `spread_bps` / `replication` accept
  `null`. An unknown TER was honestly recorded as null and the schema demanded a number — the rule
  is never to claim false precision, so the schema now permits the honest gap.

**Result:** 26/26 studies validate (was 10/26). A `core` study is ~714 bytes vs ~25,000.

---

## v2.26 — 2026-08-27 — Poda del universo: comprabilidad como filtro maestro

> Rotated from the CLAUDE.md `Recent Changes` table. The rule it established — a sector is
> investable only if a UCITS vehicle can actually be bought from Spain — lives permanently
> in CLAUDE.md §Critical Implementation Rules ("Broker reality").

| Date | File | Version | Change |
|---|---|---|---|
| 2026-08-27 | `catalyx/config/{etf_universe.yaml,sector_taxonomy.yaml}` + `catalyx/data/{market_data,flow_data}.py` + `catalyx/config/structural_catalysts/*.yaml` + `catalyx/scorer/{intensity_engine,catalyst_scorer}.py` + `catalyx/store/structural_catalyst_repo.py` + `data/sector_studies/` + `.claude/commands/{catalyx-open,catalyx-review,catalyx-scan}.md` + `tests/unit/test_flow_data.py` | **v2.26** | **Poda del universo — comprabilidad como filtro maestro (user).** Disparado por una queja real: "muchos catalizadores duplicados, muchos ETF que no tengo disponibles en Revolut, el proceso cuesta muchísimo". El diagnóstico encontró tres defectos, el tercero grave. **(1) 66 de 96 ETFs eran US no-UCITS** (`ITA`,`XLE`,`GDX`,`XBI`,`COPX`,`TAN`,`LIT`,`ROBO`…) — inaccesibles para retail EEA por PRIIPs. No es Revolut, es regulatorio. **(2) Errores de IDENTIDAD**: el `name` no era el fondo del ticker — `IQQR.DE` listado como robótica tier-1 es *iShares MSCI Eastern Europe Capped*; `LNGA.L` listado como LNG $280M es *WisdomTree Natural Gas 2x Daily Leveraged* ($10M, decaimiento diario); `DFEN.DE`/`EUDF.L`/`NATO.PA`/`IQQH.DE` todos mal atribuidos; `NUKE.L`,`WTRD.L`,`AIPO.DE`,`XGLD.DE`,`XSLV.DE`,`LUXE.PA` muertos o inexistentes. Y **3 de los 5 ETFs realmente en cartera** (`4COP.DE`,`USPY.L`,`IUHE.AS`) **no estaban en el fichero**: el universo describía un mercado que no se opera. **(3) El momentum se medía sobre ETFs US no comprables** — `SECTOR_TICKERS` ponía `COPX` de chain[0] para copper_miners mientras la posición real es `4COP.DE` (otra divisa): se rankeaba el heatmap con un retorno no obtenible, y `flow_data.SECTOR_TICKERS` exponía esos mismos tickers US como "primary tradeable ticker". **Reconstrucción:** `etf_universe.yaml` v2.0 = 26 sectores / 51 entradas, **todas verificadas contra yfinance** (longName/currency/exchange/AUM), nuevos campos `broker_access` (verified|assumed) e `instrument` (etf|etc), `ter: null` a propósito (los TER de la v1.1 pertenecían a fondos que resultaron ser otros). Rescates UCITS para sectores con catalizador vivo: `JEDI.DE` (espacio), `NUKL.DE`+`URNU.DE`/`URNM.L` (nuclear/uranio), `BTEC.L`+`HEAL.L` (biotech), `WCLD.L` (cloud), `SPAG.L` (agro), `DAPP.L` (cripto), `RBOT.L` (robótica, corrige el error de IQQR.DE), `XAIX.DE` (IA, $8.6B), `SPGP.L`/`GDX.L` (mineras de oro, vs los $679M de AUCO.L), `IH2O.L` (agua), `GLUX.SW`/`LUXU.L` (lujo). `sector_taxonomy.yaml` v2.0: **investable 53→26**; los 27 retirados quedan watch-only con `retired_2026_08_27` + `retired_reason` (nada se borra, Schema Change Protocol) y sus 27 estudios se archivan en `data/sector_studies/_archive/`. Catalizadores **18→12 activos**, fusionados por *driver compartido* (no por vehículo): `copper_datacenter_demand`→`ai_capex_supercycle`; `solar_lcoe`+`battery_storage`→`energy_transition_grid`; `crispr`+`biosecure`→`ai_drug_discovery`; `japan_carry_unwind`→`role: macro_context` (no hay ETF que exprese un unwind del carry — es régimen, no posición); `stablecoin_payment_rails` re-apuntado a crypto_infrastructure al caer fintech_payments. Los absorbidos conservan `status: merged`+`merged_into`+`merge_rationale` y **sus indicadores NO se copian** (su `value_history` vive en el lake bajo el id original; moverla falsearía el percentil de `intensity_engine`) — quedan listados en `absorbed_note` para decidirlos en el próximo `/catalyx-update`. `compute_all()` y `structural_catalyst_repo` ahora saltan merged/macro_context. **Bug preexistente arreglado de paso:** `catalyst_scorer --all` moría con `KeyError: 'strength_original'` al imprimir un evento contado vía su structural enlazado (afectaba a 8 sectores, entre ellos `pharma_large_cap` y `eu_defense_prime_contractors`) — tumbaba el run entero antes de emitir el JSON. Nuevo gate de vehículo obligatorio en `/catalyx-open` (ticker debe estar en el universo + verificación yfinance antes de escribir el Movement). El test de amplitud de flow_data (`>= 45 sectores`, que premiaba justo el defecto eliminado) se sustituye por dos invariantes de alineación: cobertura == sectores investables, y chain[0] ∈ etf_universe. **Coste por ciclo:** sectores puntuados 53→26, estudios 53→26, catalizadores a refrescar 18→12, indicadores 66→44. 214 tests verdes. |

## v2.25 — 2026-08-04 — Exit watcher: FX-correct EUR drawdown floor + catalyst-freshness gate

> Rotated verbatim from the CLAUDE.md `Recent Changes` table. Doctrine detail lives in
> `docs/DESIGN_sell_signals.md` §Family 1b.

| Date | File | Version | Change |
|---|---|---|---|
| 2026-08-04 | `catalyx/scorer/exit_watcher.py` + `catalyx/config/{scoring_weights.yaml,weights.py}` + `tests/unit/test_exit_watcher.py` + `docs/DESIGN_sell_signals.md` | v2.25 | **Exit watcher: FX-correct EUR drawdown floor + catalyst-freshness gate (user).** Triggered by a real miss — a EUR grid position sat at −21.7% flagged only `watch`, and a GBP semis position *looked* like −24% but was really −11%. Three defects fixed, all in `exit_watcher`. **(1) FX bug:** `_tax_view` marked `native_price × qty` against an EUR cost basis, so every non-EUR vehicle's P&L/drawdown was garbage (SEMI.L showed −23.8%, real EUR −11.0%; USPY.L +20.2%, real +4.4% — and its CGT estimate was inflated too). `assess` now FX-converts the vehicle columns to EUR via `nav_engine._eur_prices` before marking (stops still evaluate in NATIVE currency — their thresholds are native). **(2) Stops never fired:** the only price stops were "−20% for 10 CONSECUTIVE days" (`review_and_reduce`, and the run reset on any bounce — grid oscillated at −22% for weeks at 6/10). Added a **two-tier floor on the EUR drawdown vs real cost** (`evaluate_drawdown`): `reduce` at `drawdown_reduce_pct` −20, `exit` at `drawdown_exit_pct` −30, no consecutive-day gate. **(3) Stale verdict:** `regime_state`/assumptions are Claude-set and were 2 months old (`intensity.last_updated` looked fresh after a trend-only recompute). Added **catalyst freshness as a first-class input** (`catalyst_freshness`): reads each driving catalyst's `status_last_reviewed` (NOT `intensity.last_updated`), stalest driver governs, `>catalyst_staleness_max_days` 45 → `very_stale`. **Doctrine — freshness dominates, a drawdown is a trigger to RE-VERIFY not an auto-sell** (`drawdown_overlay_action`): only a FRESH+weakening verdict auto-acts (reduce/exit); FRESH+intact only `warn`s (a fear selloff on a live thesis → hold/add is Claude's call); a STALE verdict + drawdown forces a re-verify (protective reduce on the exit tier). Folds into `suggest_action` via `drawdown_action`/`reverify_required` — only ever RAISES the recommendation, stays recommend-only. Live run confirmed the fix: whole book's catalyst verdicts 60-64d stale → all flagged RE-VERIFY; WebSearch showed AI-capex ($700-900B 2026, +36% YoY) and grid (transformer lead times 48-60mo) both intact/accelerating → the selloffs were fear/rotation, hold/add not sells. New `exit_signals` config: `drawdown_reduce_pct`/`drawdown_exit_pct`/`catalyst_staleness_{warn,max}_days`. 213 tests green (+9). Adaptive review cadence (30d floor / ±10%-move or VIX pull-forward / 45d ceiling) documented in `DESIGN_sell_signals.md §Family 1b`; optional CronCreate automation pending user confirmation. |

## v2.24 — 2026-07-28 — Token-cost reduction pass

> Rotated verbatim from the CLAUDE.md `Recent Changes` table.

| Date | File | Version | Change |
|---|---|---|---|
| 2026-07-28 | `CLAUDE.md` + `CHANGELOG.md` + `.claude/commands/{catalyx-review,catalyx-scan,catalyx-heatmap}.md` + `.claude/{settings.json,hooks/guard.py}` + `scripts/{post_run,score_run}.sh` | v2.24 | **Token-cost reduction pass (user).** Three fronts. **(1) Context load:** CLAUDE.md 100KB→43KB (−57%, ~14k tokens saved EVERY session) — Recent Changes trimmed 26→5 rows (21 moved verbatim to the CHANGELOG archive, zero loss), the Repo Structure roadmap-tree collapsed to real files only, "What Designed/Missing" cut to open TODOs only, and the module table's inline design essays compressed to one line/module (every CLI kept exact). **(2) Execution cost:** `/catalyx-review` Step 3 default flipped from "study ALL ~46 sectors every cycle" (2M+ tokens) to **movement-driven + decision-relevant** refresh (open positions + scan-flagged drivers + stale entry-candidates + never-studied); a sector without a fresh study still ranks on its momentum baseline, so nothing is missed. Full-universe sweep is now opt-in (`full-studies`). New **EXECUTION MODEL** section: bulk-WebSearch / many-file phases (scan, studies, heatmap+portfolio, opportunities, position reviews, watch triggers) run in **subagents that return only digests**, so the main conversation stays a thin orchestrator holding compact summaries; only the two AskUserQuestion steps (9 open-recs, 12 gap review) stay in main (subagents can't ask the user). `/catalyx-scan`: C2b refresh no longer sweeps all ~30 catalysts (only findings-touched; the rest collapse to one "no change" line), analyst-revision queries 5→2 scoped to held sectors. **(3) Hooks + consolidation:** the `.claude/settings.json` hooks were **dead** (PowerShell + `$env:TOOL_OUTPUT`, neither exists on macOS/Linux) → ported to a cross-platform `.claude/hooks/guard.py` (reads the hook JSON on stdin) driving the schema/taxonomy/structural edit reminders + a new post-`snapshot_repo record` reminder. Two new shared scripts collapse narrated Bash chains into one call each: **`scripts/post_run.sh`** (Step 5b: portfolio build-all → per-strategy nav model/live → real nav → rotation; verbose → `data/reports/post_run_<date>.log`, compact NAV digest → stdout) and **`scripts/score_run.sh`** (record run + register-report → the 4 opportunity/regime scorers — the chain `catalyx-heatmap` steps 11-12 and `/catalyx-review` Step 5c BOTH narrated identically; now deduped, record/register → log, scorer JSON → stdout). `catalyx-open` left as-is (short, interactive, decision-heavy — not a delegation target). No schema/pipeline-contract change. |

## v2.21 — 2026-06-08 — Portfolio-anchored catalyst exposure over time

> Rotated out of the CLAUDE.md Recent Changes table (2026-08-04) when it reached 6 entries. Verbatim row:

| Date | File | Version | Change |
|---|---|---|---|
| 2026-06-08 | `catalyx/execution/portfolio.py` + `catalyx/store/lake.py` (`portfolio_catalyst_exposure` table) + `catalyx/store/lake_query.py` (`portfolio_catalyst_exposure` + CLI) + `scripts/build_site.py` + `site/{app.js,index.html}` + `tests/unit/test_lake_query.py` | v2.21 | **Lineage reframed again (user) → PORTFOLIO-anchored catalyst exposure OVER TIME.** v2.20's catalyst→strategies cut was the wrong axis. The right question: take a strategy's book (assume **€1000 split across its holdings**), decompose it **by catalyst**, and track how that mix shifts as the book **rebalances every run**. New deterministic decomposition recorded at each `portfolio.py` build: each holding's `weight_pct` is divided **EQUALLY across the catalysts driving its sector** (point-in-time from the studies' `active_catalyst_ids`; sectors with no catalyst → `uncatalyzed`; the water-fill remainder → `cash`) → the % of the book exposed to each catalyst. Persisted to a new lake table **`portfolio_catalyst_exposure`** (portfolio_id, run_id, catalyst_id, pct, eur, notional_eur) — partition (portfolio_id, run_id), one row per catalyst per rebalance. `lake_query.portfolio_catalyst_exposure(pid)` returns `{timeseries[{run_id,date,by_catalyst}], average[{catalyst_id,avg_pct,avg_eur}]}` where the average is **TIME-WEIGHTED** — each rebalance weighted by how long its allocation was live (Δt to the next run, last → now), the 'tiempo activo' rule the user asked for. `build_site` bakes it per portfolio into `overview.json` (zero-WASM first paint). Dashboard: the residual catalyst-dropdown lineage REPLACED by a portfolio-anchored **"Catalyst exposure over time"** that follows the selected strategy — current composition bars (% + € of the €1000), a multi-line exposure-over-time chart (one line per catalyst, `lineChart` auto-scaled via `o.maxY`), and the time-weighted-average table. **Only 1 build exists today** → the chart + avg populate from the next recompute; the composition bars are live now. Verified: catalyx 24.9% ai_capex / momentum 29.8% ai_capex / low_crowding 22.4% NATO. 180 tests green. |

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

| 2026-06-08 | `catalyx/config/track_record.yaml` (`total_capital_eur`) + `catalyx/config/weights.py` (`total_capital_eur()`) + `scripts/build_site.py` + `site/{app.js,index.html}` | v2.22 | **Positions page: committed-capital + cash model, and reframed the book's framing (user).** Two asks. **(1) Capital plan.** The real book is now funded with an explicit **€10,000 committed up front, deployed progressively as catalysts fire** — not a vague "invested" number. New `total_capital_eur` in `track_record.yaml` (read via `weights.total_capital_eur()`); `build_site` bakes `total_capital_eur` + **`cash_eur`** (= committed − cost basis of open positions) + `deployed_pct` into `positions`. The Positions summary strip gained a **committed-capital** card (with `% deployed`) and a **cash** card (dry powder · awaiting catalysts) — cash is now a first-class variable on the page. Today: €10k committed / €1.5k invested / **€8.5k cash** / 15% deployed. **(2) Framing.** Replaced the "⚠ entry by design — entry was *deliberately bad*, opened into the selloff, book *starts underwater on purpose*, a test of luck" box with a **"Capital plan — €10,000 committed · long-horizon · catalyst-driven"** card: capital deployed progressively, positions sized to conviction and held while the thesis holds — a long-term thesis-driven book, not short-term trading. (Per the user: the old copy read like gambling; this is long-term investing and the dashboard is meant to show rigor.) 180 tests green. |
