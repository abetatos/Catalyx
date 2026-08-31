# PLAN v5 — Coherencia dato↔acción, y el prior que nadie ha escrito

> Sucesor de `docs/PLAN_v4_rigor_and_lean_metrics.md`, que cerró completo (v4.0 → v4.9).
> v4 preguntó *"¿qué sobra, cuánto cuesta, y quién decide?"* y lo respondió: libro objetivo
> cerrado, breakeven en vez de forecast, coste de no actuar, scorecard de la propia tabla,
> y −22% de payload determinista por review.
>
> v5 nace de la crítica al `review_20260831.md` (2026-08-31). Su hallazgo central no es un bug
> suelto:
>
> **El report imprime, en la misma página, que su ranking no ordena nada (IC −0.050, top3−rest
> −5.84pp) y que hay que ejecutar 8 trades hacia ese ranking. Ambas cosas pueden ser correctas
> a la vez — pero el documento no dice por qué, porque nadie ha escrito nunca el prior sobre el
> que descansa la presión de despliegue.**
>
> Y a su lado, tres defectos de coherencia: datos muertos alimentando compras vivas, un streak
> sin unidad de tiempo, y un contador de desviaciones que crece con la frecuencia de runs.
>
> Todo lo de §0 está medido sobre este checkout el 2026-08-31, no estimado.
> v4.9 ya cerró lo que era puramente de render (la columna `rk`, el NaN, el overkill).

---

## 0. Diagnóstico

### 0.1 Lo que v4.9 ya cerró (no se replantea aquí)

| Defecto | Cerrado en |
|---|---|
| `rk` con dos semánticas por fila; fallback `~11` en un renderizador y no en el otro | v4.9 |
| `partial_rungs` leyendo el rank del model book para su pata de rank → `rank nan < 6` | v4.9 |
| NaN de parquet sobreviviendo a un guard `is None` | v4.9 |
| §8 con 41 filas de indicador · desviaciones repitiendo el mismo run 10 veces | v4.9 |
| Barrido de ranks unidireccional impreso como N hallazgos | v4.9 |

### 0.2 Defectos verificados que quedan abiertos

**D1 — El report se entrega a medias, y nada lo detecta.**
`review_20260831.md` salió con **cinco** marcadores `<!-- CLAUDE: … -->` sin rellenar: executive
summary, contexto macro C0, evidencia por posición (§4), recomendaciones de apertura (§9) y
revisión de gaps de taxonomía (§10). El esqueleto sin la carne es peor que un report visiblemente
incompleto: §4 ordena dos SELL y el sitio donde iría la evidencia que los sostiene — o el override
que los discute — está en blanco. `lint_prose` ya existe y ya se ejecuta en `--check`
([review_report.py:764](../scripts/review_report.py#L764)), pero solo caza *hedges*; un marcador
intacto pasa el lint limpio. **La arquitectura dice que Python escribe los números y la sesión
escribe el juicio. Hoy solo una de las dos mitades tiene control de calidad.**

**D2 — Indicadores muertos alimentando compras vivas, a 130 líneas de distancia.**
`struct_china_luxury_recovery` tiene `ind_02`/`ind_03` con última observación **2025-09-30**, 240d
por encima de su cadencia trimestral. §3 del mismo report ordena **BUY €1.020 en luxury_goods**, y
el `catalyst_alignment` que lo justifica es **70.4 — exactamente el `intensity.current_score` de
ese catalizador**, computado el 2026-07-28 sobre indicadores de los que dos no se observan desde
septiembre de 2025. Los dos hechos están impresos en el mismo documento y ninguno sabe del otro. El caso simétrico: `pharma_large_cap` tiene
`catalyst_freshness: unknown` y `score drift: —` (sin baseline de entrada) y aun así es rank 1 +
ADD. `catalyst_freshness` **ya** viaja hasta la fila de posición
([rebalance.py:1184](../catalyx/execution/rebalance.py#L1184)) y hasta el digest — pero solo para
posiciones ABIERTAS. Una fila BUY no tiene posición, así que llega sin ninguna calificación de
frescura. CLAUDE.md ya dicta la doctrina para estudios («un estudio STALE es peor que ninguno —
inyecta puntuaciones confiadas y equivocadas»); no está aplicada a los indicadores.

**D3 — «4 consecutive runs» no tiene unidad de tiempo.**
v4.3 arregló el caso same-day (dos runs de una tarde = una observación,
[`_rank_streaks`](../catalyx/execution/rebalance.py#L759)) pero no el general. Los 4 runs deduped
que produjeron el SELL de copper son **2026-06-30, 07-05, 07-28, 08-28**: huecos de 5 días a un
mes. `rank_out_consecutive: 2` significa por tanto «diez días» o «dos meses» según lo activo que
hayas estado ese trimestre. Un umbral cuyo significado depende de tu ritmo de trabajo no es un
umbral congelado, aunque esté en la sección `frozen`.

**D4 — La tally de desviaciones crece con la frecuencia de runs, no con las decisiones.**
`unrecorded_deviations` ([rebalance.py](../catalyx/execution/rebalance.py)) lee las filas non-HOLD
del run ANTERIOR y loggea un DEFER por cada una sin movimiento ni override. No consulta si esa
misma (sector, acción) ya tiene un DEFER pendiente. Ejecutar el pipeline tres veces en una semana
sin operar escribe **30 DEFERs** por las mismas 10 decisiones. Además §5 imprime dos contadores que
no cuadran a la vista — «10 logged but not yet scored» y «claude: 0/5 scored overrides» — porque
son cosas distintas (backlog global vs. el gate de suspensión de
[`claude_override_suspended`](../catalyx/execution/rebalance.py#L1511)) y nada lo dice.

**D5 — La presión de despliegue descansa en un prior que no está escrito.**
El sistema es coherente en el *sizing*: con IC no significativa, λ→0 y el reparto se vuelve
inverse-vol (B1, v4.5). Pero la **selección** — qué 10 nombres, que mueve el 100% del capital
desplegado — corre a convicción plena sin gate alguno, y §4c exige ejecutarla («persistence rule
breached»). Eso puede ser correcto: el prior de estar invertido no es el prior de que TU ranking
ordena. Pero el report no distingue ambos, así que un lector razonable concluye que el sistema se
contradice. **El defecto es de comunicación, no de política — y por eso es barato de arreglar.**

**D6 — El contrafactual del cash drag mide contra el benchmark, no contra la política.**
`cash_drag` está bien firmado (si el benchmark cae, `forgone_eur` sale negativo) pero (a) la
etiqueta **`CASH DRAG` / «forgone» no se invierte**, así que un trimestre en el que estar en
liquidez fue ACERTADO se imprime igual que uno en el que fue caro; y (b) el contrafactual es SPY,
cuando la decisión que se está juzgando es «no ejecuté la tabla». El número que responde a esa
pregunta es lo que habría hecho el **model book `catalyx`**, que ya se computa cada run y que ya
alimenta el `execution_alpha_pp`.

**D7 — Un único `spread_bps: 20.0` para todo vehículo, y un b/e que hereda su uniformidad.**
[`scoring_weights.yaml:811`](../catalyx/config/scoring_weights.yaml#L811) asume 20bps round-trip
para todo, con el comentario ya escrito de que «cuando `etf_universe` lo lleve, gana el valor por
ETF». Efecto visible: **siete de las ocho filas con acción imprimen `b/e 0.20%` idéntico** (la
única distinta es copper, y solo porque lleva CGT encima), así que la columna
no discrimina entre un `IUHE.AS` líquido y un `JEDI.DE` fino. El protocolo de CLAUDE.md ya manda
revisar el spread trimestralmente (>25bps → warning); esa revisión no aterriza en ningún cálculo.

---

## 1. Objetivos de v5

1. **Ningún dato muerto financia una compra sin decirlo en la misma fila.**
2. **Ningún umbral cuyo significado dependa de tu ritmo de trabajo.**
3. **Cada contador cuenta decisiones, no ejecuciones del pipeline.**
4. **El report no se declara terminado si su mitad de juicio está vacía.**
5. **Si el sistema pide convicción sobre evidencia nula, lo dice en una línea.**

No objetivos, explícitamente: cambiar un umbral de `rebalance_rules` (sigue `frozen`: commit +
línea de CHANGELOG), tocar la política de despliegue, o añadir una fuente de datos nueva.

---

## 2. Fase E — Coherencia dato↔acción

### E1 — Frescura del catalizador en la fila que gasta el dinero  ✅ SHIPPED 2026-08-31 (v5.0)

**Qué.** Nueva columna en la tabla de rebalance, poblada para **toda** fila, no solo para
posiciones abiertas. *Entregada como `data` (campo `data_age`), no `cat_fresh`: la cabecera va en
una tabla de ancho fijo y `data` cabe donde `cat_fresh` no.*

**Cómo.** `freshness.overdue()` ya devuelve por indicador; se agrega por catalizador (v4.9 ya
escribió ese *rollup* en `section_freshness`, se extrae a `freshness.by_catalyst()` y se reutiliza).
El mapa sector→catalizadores ya existe en `portfolio.catalyst_exposure_rows`. Por fila:

| valor | significado |
|---|---|
| `fresh` | ningún indicador del catalizador primario vencido |
| `stale (Nd)` | el peor indicador lleva N días sobre su cadencia |
| `blind` | ningún indicador observado dentro de **2× su cadencia** |
| `—` | sector sin catalizador estructural (`uncatalyzed`) |

**Qué NO hace.** No bloquea, no degrada la acción, no entra en `decide_action`. El motivo es la
doctrina de la memoria de sesión (*«freshness dominates: drawdown → re-verify, not auto-sell»*):
un dato viejo es una razón para **verificar**, no para dejar de comprar. Lo que sí hace es que
`luxury_goods BUY €1.020 · data blind (240d)` sea una sola frase, y que el Step 2 del scan
sepa qué refrescar ANTES de que el review recomiende.

**Coste.** Cero llamadas nuevas: `freshness` ya se ejecuta para §8.

**Test.** Una fila BUY sobre un catalizador con un indicador a 300d imprime `blind` y su acción
sigue siendo `BUY` — la calificación informa, no vota.

### E2 — El streak cuenta ciclos separados, no runs adyacentes  ✅ SHIPPED 2026-08-31 (v5.0)

**Qué.** `_rank_streaks` acepta `min_gap_days` (default **21**, ~un ciclo de review) y solo cuenta
un run si dista ≥ `min_gap_days` del anterior contado. Runs más juntos colapsan al último, igual
que v4.3 hizo con los del mismo día — mismo defecto, otra escala.

**Efecto medido sobre este libro.** Los 4 runs de copper (06-30, 07-05, 07-28, 08-28) colapsan a
**3** (07-05 dista 5d de 06-30). `rank_out_consecutive: 2` se sigue cumpliendo, así que **el SELL
de copper no cambia** — pero deja de poder fabricarse con una semana de iteración.

**Qué NO hace.** No mueve `rank_out_consecutive`. El umbral sigue congelado; lo que cambia es la
unidad que cuenta, que hasta ahora no estaba definida.

**Test.** Cuatro runs a 3 días de distancia producen streak 1, no 4. La razón impresa nombra la
ventana real en días, no solo el número de runs.

### E3 — El DEFER se deduplica por decisión pendiente  ✅ SHIPPED 2026-08-31 (v5.0)

**Qué.** `unrecorded_deviations` recibe los overrides `unrecorded` ya abiertos y salta la
(sector, acción) que ya tiene uno sin puntuar. El DEFER existente **conserva su `logged_at`
original** — es la fecha en que la decisión se tomó por primera vez, y es la que su ventana de 21
días debe contar.

**Efecto.** Ejecutar el pipeline tres veces sin operar produce 10 DEFERs, no 30. Y el reloj de
cada DEFER mide desde que dejaste de actuar, no desde el último run.

**Además.** §5 etiqueta sus dos contadores: `backlog` (esperando ventana) vs `gate de suspensión de
claude` (que necesita `min_scored` puntuados para significar algo). Una línea, no una sección.

**Test.** Dos runs consecutivos sin movimiento sobre la misma fila → un solo override, con el
`logged_at` del primero.

---

## 3. Fase F — Decir lo que el sistema sabe de sí mismo

### F1 — Nombrar el prior cuando la evidencia es NONE o ADVERSE  ✅ SHIPPED 2026-08-31 (v5.0)

**Qué.** Una línea generada en §3, junto al estado del gate, cuando
`rank_edge_evidence.verdict ∈ {NONE, ADVERSE}`:

> **Sobre qué descansa esta tabla.** La evidencia medida del ranking es `NONE` (~1 ventana
> independiente). El despliegue que se pide abajo no descansa en ella, sino en el prior de estar
> invertido: la tabla elige NOMBRES sobre una evidencia aún no establecida, mientras que el
> TAMAÑO ya está neutralizado (λ=0.00, inverse-vol). Aceptar las filas es aceptar ese prior. La
> alternativa registrada es un override que nombre el déficit de despliegue.

**Por qué es esto y no un gate.** Un gate sobre la selección sería una política nueva, y el
usuario ya decidió la política. Lo que faltaba era que el documento no pareciera contradecirse:
hoy exige disciplina total hacia un ranking que él mismo declara nulo, sin distinguir los dos
priors. Es una línea de texto y cierra la incoherencia entera.

**Test.** Con `verdict: MEASURED` la línea no aparece. Con `NONE` o `ADVERSE`, sí, y contiene
tanto la palabra `NOMBRES` como el λ vigente — un cambio de política que la haga obsoleta rompe
el test.

### F2 — Lint de completitud: el report no miente sobre estar acabado  ✅ SHIPPED 2026-08-31 (v5.0)

**Qué.** `--check` extiende `lint_prose` con `lint_completeness`: todo marcador
`<!-- CLAUDE: … -->` que sobreviva sin prosa detrás es un hallazgo. Salida distinguida:

```
✗ review_20260831.md — INCOMPLETE: 5 of 5 judgement sections empty
    §Executive summary · §0 Macro · §4 Evidence per position · §9 Openings · §10 Taxonomy
  The deterministic half is done. The report is not.
```

**Cómo se rellena un marcador.** El generador escribe el marcador; la sesión escribe debajo y
**deja el marcador en su sitio** (es la ancla para regenerar sin perder la prosa). El lint mira si
hay ≥1 línea de prosa no vacía entre el marcador y la siguiente cabecera.

**Dónde se ejecuta.** `post_run.sh` NO lo ejecuta — en ese momento el report acaba de nacer vacío
y siempre fallaría. Se ejecuta al final de `/catalyx-review`, que es cuando el documento se declara
terminado, con el mismo exit code que ya usa el lint de hedges.

**Test.** El report recién generado por `build()` falla el lint de completitud (5 hallazgos) y
pasa el de hedges — los dos son ortogonales y ambos deben poder fallar solos.

### F3 — El coste de no actuar, contra la política que dices seguir  ✅ SHIPPED 2026-08-31 (v5.0)

**Qué.** §4c pasa de un número a dos, y la etiqueta se firma:

```
COSTE DE NO EJECUTAR — €6.983 en liquidez desde 2026-06-16 (76d)
  vs benchmark (SPY +2.80%)          → €196 no capturados
  vs el model book `catalyx` (+3.60%) → €251 no capturados   ← la política que la tabla implementa
```

Y cuando el contrafactual es negativo, la etiqueta se invierte a `LIQUIDEZ QUE AHORRÓ` con el
signo correcto. Un ledger que solo puede reprochar no es un ledger.

**Por qué el model book.** La decisión que se juzga no es «¿debí estar en el mercado?» sino «¿debí
ejecutar ESTA tabla?». El model book `catalyx` es exactamente esa política, ya se computa cada run
y ya alimenta `execution_alpha_pp` — el dato existe, solo no se ha usado aquí.

**Test.** Con un benchmark negativo, la etiqueta no dice «forgone» y el signo del € es coherente
con la dirección.

---

## 4. Fase G — Fricción real (la más pequeña, y la única que puede esperar)

### G1 — `spread_bps` por vehículo  ✅ SHIPPED 2026-08-31 (v5.0)

**Qué.** `etf_universe.yaml` gana `spread_bps` opcional por entrada; `cost_drag` ya acepta el
override por parámetro y su docstring ya lo anticipa — solo falta que alguien se lo pase. Sin valor
por ETF, se hereda el global de 20bps (comportamiento actual, sin cambio).

**Cómo se puebla.** La revisión trimestral de AUM/spread que CLAUDE.md ya manda, escribiendo el
número observado en vez de solo emitir un warning. Poblar los **8 vehículos del libro objetivo**
es suficiente para que la columna deje de ser constante; el resto puede quedarse en el default.

**Por qué es la última.** Mueve el b/e unas décimas y no cambia ninguna acción. Es la diferencia
entre una columna correcta y una columna informativa, no entre una decisión buena y una mala.

---

## 5. Orden de ejecución y por qué

| # | Item | Depende de | Justificación del orden |
|---|---|---|---|
| 1 | **F2** lint de completitud | — | Es el que impide que los demás se entreguen a medias. Va primero por eso. |
| 2 | **E1** frescura en la fila | `freshness.by_catalyst` | El defecto con dinero encima: €1.020 hacia un catalizador ciego. |
| 3 | **F1** nombrar el prior | — | Una línea. Cierra la incoherencia que un lector ve primero. |
| 4 | **E3** dedup del DEFER | — | Cuanto más tarde, más basura hay que limpiar del `override_log`. |
| 5 | **E2** suelo temporal | — | No cambia ninguna acción hoy; evita que mañana se fabrique una. |
| 6 | **F3** contrafactual doble | `portfolio_compare` | Mejora un número ya presente. |
| 7 | **G1** spread por vehículo | revisión trimestral | Décimas. Puede esperar al próximo ciclo. |

**Un ítem, un commit, una línea de CHANGELOG.** Ninguno toca `rebalance_rules` congelados.

---

## 6. Lo que este plan rechaza, y por qué queda escrito

- **Un gate sobre la SELECCIÓN cuando la IC no es significativa.** Tentador por simetría con λ,
  pero es una política nueva y la política ya está decidida. Lo que faltaba era decir el prior
  (F1), no cambiarlo. Si el usuario quiere despliegue escalonado mientras el gate está de pie, es
  un edit de config con su línea de CHANGELOG — no una consecuencia lateral de este plan.
- **Bloquear un BUY sobre un catalizador `blind` (E1).** Contradice la doctrina de frescura ya
  registrada: un dato viejo manda **verificar**, no dejar de actuar. Además convertiría un fallo
  de mantenimiento en una prohibición de operar, que es exactamente el sesgo conservador que la
  Fase C de v4 existe para combatir.
- **Refactorizar los dos renderizadores en uno.** v4.9 los realineó en la semántica que importaba.
  Fundirlos es un refactor grande cuyo beneficio es «no volverán a divergir», y el guard barato
  contra eso son los tests que v4.9 ya fija sobre la columna.
- **Bajar el `indent=2` del `run_<date>.json` a compacto** (−4,9 KB, ~1,2k tokens). El fichero está
  en git y se inspecciona a mano; un diff ilegible en un repo cuya premisa es la auditabilidad
  cuesta más de lo que ahorra.

---

## 7. Definición de hecho — TODA CUMPLIDA (v5.0, 2026-08-31)

- [x] `--check` falla sobre un report con marcadores vacíos, y pasa cuando tiene prosa.
- [x] Toda fila BUY/ADD lleva `data`; una fila `blind` sigue siendo BUY.
- [x] §3 nombra el prior cuando la evidencia es NONE/ADVERSE, y calla cuando es MEASURED.
- [x] Dos runs sin movimiento sobre la misma fila producen un DEFER, con el `logged_at` del primero.
- [x] Cuatro runs a 3 días producen streak 1. El SELL de copper sigue vivo (3 ciclos reales).
- [x] §4c imprime los dos contrafactuales y se firma correctamente con un benchmark negativo.
- [x] `cost_drag` usa el `spread_bps` del vehículo cuando existe.
- [x] Suite en verde (511, +18). Cada ítem trae su test de regresión nombrado por el defecto, no por la función.

---

## 8. Lo que apareció al ejecutar el plan (no estaba diagnosticado)

- **Dos de los siete marcadores de juicio son CONDICIONALES** (F2). Exigir prosa donde la
  respuesta honesta es «nada breachea» es como un lint enseña a escribir relleno. El generador ya
  sabe la condición, así que ahora solo emite esos marcadores cuando hay algo que decir.
- **`scr.resolve()` recarga todos los YAML en cada llamada** (E1). La primera versión costaba
  **2,9s** por run; `merged_map()` una vez son 259ms.
- **`portfolio_nav` mezcla modes bajo el mismo `portfolio_id`** (F3), con bases distintas (~124
  backtest vs ~103 live). Leer la ventana sin fijar el mode daba **−16,88%** donde `live` da
  **+0,20%**: un ahorro de €1.179 inventado. Es el defecto que v3.5 cerró en `portfolio_compare`,
  reintroducido en una lectura nueva escrita la misma hora — la tabla no puede expresar la
  restricción, así que cada lectura tiene que repetirla.
- **El campo `spread_bps` no se pudo poblar honestamente** (G1). El snapshot de yfinance devolvió
  `ask < bid` en SEMI.L y cotizaciones fuera de hora de 463bps/193bps. Un número inventado ahí es
  peor que el default: el b/e dejaría de ser constante y parecería informativo siendo ruido.
- **`pharma_large_cap` figura como `uncatalyzed` en §6** mientras su sector study declara tres
  catalizadores activos. **Arreglado en v5.1, y el diagnóstico de arriba era erróneo**: §6 no usa
  el mapa point-in-time de `catalyst_exposure_rows`, lee `movement_repo.catalyst_ledger`, que
  agrupa por el `attribution[]` congelado de cada movimiento. Al mirarlo apareció el defecto de
  verdad: **`struct_copper_datacenter_demand` está merged en `struct_ai_capex_supercycle` y el
  ledger los publicaba como dos filas** — €1.650 (16,5%) de un solo driver presentados como €1.000
  + €650, con €1.350 de headroom anunciado donde había €350. El `correlated_catalyst_cap` estaba
  siendo burlado por el merge que existía para sobrevivir. La mitad de pharma se resuelve
  nombrando el drift, no reescribiendo la atribución. Ver v5.1 en el CHANGELOG.
