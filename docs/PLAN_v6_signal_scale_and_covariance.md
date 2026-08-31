# PLAN v6 — Escalas conmensurables, riesgo con covarianza, constantes con ancla

> **ESTADO: COMPLETO 2026-08-31 (v6.0 → v6.7).** 17/17 ítems ejecutables cerrados.
> La fase K NO se implementa — está bloqueada por DATOS y anotada como tal en los TODOs
> de `CLAUDE.md`. Queda un dato pendiente del usuario: `fee_after_free_eur`.
>
> Sucesor de `docs/PLAN_v5_data_action_coherence.md` (cerrado completo, v5.0 → v5.2).
> v5 preguntó *"¿qué dato sostiene cada acción?"* y lo respondió: frescura por catalizador,
> DEFERs dedupeados, el cap leyendo riesgo y no crédito.
>
> v6 nace de la auditoría del 2026-08-31 sobre la **distribución de pesos**: la cadena
> compuesto → softmax → shrinkage → vol_tilt → water_fill → deadband → caps. Su hallazgo
> central no es un bug suelto:
>
> **Los pesos nominales del compuesto no son los pesos efectivos. Una suma ponderada de
> escalas no conmensurables (un percentil uniforme, un nivel clampeado 10–95, una constante
> 50 por default, un enum {10,25,55,75,90}) hace que el peso real de cada dimensión sea
> ≈ w·σ_cross-sectional — y ese producto nadie lo ha mirado nunca. El bug de
> `valuation_relative` (v1.6: una constante diluyendo el ranking bajo un peso de 0.15) no
> se extirpó: se extirpó UNA instancia. El mecanismo que lo produce sigue abierto, y el
> default de flow (50) lo reproduce hoy para cada sector sin snapshot.**
>
> Y alrededor, tres familias más: aritmética que contradice sus propias reglas (dos techos
> para la misma posición; "sin estudio" puntuando como "pésimo"), riesgo sin covarianza en
> ningún punto del sistema, y constantes de diseño (α=0.5, 1m=0.20, VIX-cliff, 46d) que no
> corresponden a ningún supuesto declarado ni a ninguna medición.
>
> Todo lo de §0 está medido o verificado sobre este checkout el 2026-08-31, no estimado.

---

## 0. Diagnóstico

### 0.1 Verificados con aritmética (no opinables)

**D1 — Dos techos distintos para la misma posición: 16% vs 12%.**
`portfolios/catalyx.yaml` fija `max_position_pct: 16`; `rebalance_rules.max_position_pct: 12`
capa todo BUY real ("ceiling per sector = conviction tier 1"). El model book puede concentrar
donde el libro real tiene prohibido llegar → la medición de execution alpha (modelo vs real)
queda sesgada POR CONSTRUCCIÓN: parte del "alpha no capturado" es inalcanzable por regla. El
tramo 12–16% del model book es un target que ningún ejecutor disciplinado puede tomar.

**D2 — Pesos nominales ≠ pesos efectivos (el mecanismo de v1.6, aún abierto).**
En un ranking por suma ponderada, el peso efectivo de una dimensión es ≈ w·σ_cross. Hoy:
`momentum` es percentil uniforme (σ≈29), `catalyst_alignment` vive en una banda ~60–90
(σ≈10–15), `flow_confirmation` cae a la constante 50 sin snapshot
(`sector_scorer.py::_DEFAULT_FLOW`), `crowding` es un enum de 5 valores. Momentum pesa de
facto MÁS que su 0.29; CA menos que su 0.35; y una dimensión degenerada a constante pesa
CERO diga lo que diga el YAML — sin que nada lo detecte.

**D3 — Sin estudio ⇒ CA=0: "no medido" puntúa como "pésimo", y el filtro lo hace absoluto.**
Máximo compuesto alcanzable sin estudio, con flow en default:
`0×0.35 + 100×0.29 + 50×0.24 + (100−35)×0.12 = 48.8 < min_composite 55`.
**Un sector nuevo con momentum perfecto no puede entrar jamás al libro flagship** (con flow
también perfecto llega a 60.8 — exige dos señales en máximos simultáneos). Es un sesgo
pro-incumbentes que contradice la misión del pipeline (detectar ANTES de priced-in): el
descubrimiento depende al 100% del paso manual de estudios. Estadísticamente, un missing se
imputa al prior, nunca al peor caso.

**D4 — Umbral absoluto sobre un compuesto semi-relativo.**
`min_composite: 55` es un nivel, pero momentum es un percentil (media ≈50 SIEMPRE, por
construcción) mientras CA es un nivel que deriva con el ciclo de catalizadores. El
significado de "55" cambia cada run con el nivel medio de CA del universo. Un umbral
congelado cuyo significado no está congelado no es un umbral congelado.

**D5 — El percentil contra la propia historia satura en series tendenciales.**
`intensity_engine`: una serie con tendencia (compras CB de oro, capex hyperscaler) tiene su
valor actual casi siempre cerca del máximo histórico → percentil ≈100 permanente → el
indicador pierde poder discriminante EXACTAMENTE en los catalizadores estructurales más
persistentes, que son la razón de ser del tipo `StructuralCatalyst`. Además hay un salto de
régimen al llegar el 6º punto (curva saturante → percentil): un mini-cliff de la familia que
v1.5 eliminó.

**D6 — El model book aún reparte exposición por catalizador con la semántica de crédito.**
`portfolio.catalyst_exposure_rows` divide `w/len(cats)` en partes iguales. v5.2 estableció
para el libro real que riesgo = exposición COMPLETA detrás de cada driver; la tabla
`portfolio_catalyst_exposure` del lake queda escrita con la semántica que v5.2 acaba de
desterrar. Dos definiciones para la misma palabra según el libro.

**D7 — El deadband mueve los pesos que acaba de decidir no mover.**
`apply_deadband` renormaliza multiplicando TODOS los pesos (kept incluidos) por `total/s`
— reintroduce los micro-trades que el band suprimió y puede empujar un peso por encima de
`max_position_pct` (el water_fill ya corrió).

**D8 — El VIX es un acantilado binario.** `deployment.vix_pause_above: 30` con penalty
0.20: VIX 29.9 → 30.1 mueve el 20% del capital objetivo. Sin histéresis, un VIX oscilando
en 30 hace oscilar el target completo run a run.

**D9 — Tres constantes ligadas por identidades que nadie escribió, y hoy son conjuntamente
INFACTIBLES bajo la restricción real del bróker.**
`max_position_pct` no es principalmente un techo de concentración: **con un objetivo de
despliegue es una COTA INFERIOR del número de posiciones**, y `correlated_catalyst_cap` es
una cota inferior del número de drivers independientes:

```
n_min_posiciones = deploy_ratio / max_position_pct
n_min_drivers    = deploy_ratio / correlated_catalyst_cap
```

Con los valores congelados (deploy máx 0.85 = `base 0.70 + step 0.05 × (8−5)`):
`0.85/0.12 = 8 posiciones mínimo` y `0.85/0.20 = 5 drivers independientes mínimo`.

La restricción operativa real es **10 operaciones gratis al mes** (Revolut). Montar el libro
que la config exige consume **8 de 10 slots**, y deja 2 para todo el mes — en un mandato cuya
propuesta de valor entera es actuar sobre eventos que llegan de forma estocástica entre
reviews. Y 5 drivers independientes entre 8 nombres temáticamente enlazados no ocurre nunca:
por eso `struct_ai_capex_supercycle` sale al 35,6% contra el cap del 20% (v5.2). **El cap no
está roto porque el libro sea temerario; está roto porque la terna es infactible.** Un cap
permanentemente incumplido es un cap permanentemente ignorado, que es peor que uno puesto
donde puede morder.

Medición que decide el `n` objetivo (2026-08-31, 44 vehículos, retornos semanales, ρ=0.245):
`σ_rel = √(ρ + (1−ρ)/n)` → n=5: 0.629 · **n=6: 0.609** · n=7: 0.594 · n=8: 0.583 · n=10: 0.566.
Pasar de 6 a 10 nombres compra un 7% de vol relativa y cuesta 4 de los 10 slots mensuales.
La rodilla está en 6.

**D10 — La correlación diaria subestima el riesgo a la mitad (efecto Epps).**
Medido sobre los mismos 44 vehículos: ρ medio **diario 0.127 → semanal 0.245 → quincenal
0.243** (converge en semanal). Los UCITS de LSE/XETRA cotizan con horarios y liquidez
distintos, y el precio de cierre no sincronizado sesga la covarianza a la baja. Cualquier
matriz de covarianza construida con retornos diarios — I1 tal y como estaba escrita —
**subestimaría sistemáticamente la vol de cartera y la concentración por cluster.** El
`vol_lookback_days: 120` diario de `_sector_vols` tiene el mismo sesgo en la vol individual,
aunque ahí es mucho menor (afecta a la covarianza, no tanto a la varianza propia).

### 0.2 Constantes sin ancla (el supuesto que las justificaría no está declarado — o las contradice)

| Constante | Valor | Contra qué choca |
|---|---|---|
| `momentum_period_weights.return_1m` | **+0.20** | Reversal de corto plazo (Jegadeesh 1990, Lehmann 1990); el estándar es 12-1 con skip del último mes (Jegadeesh–Titman 1993). El propio backtest v1.6 encontró IC negativo en la señal de corto — y el 1m sigue entrando con signo positivo |
| `composite_weights` | .35/.29/.24/.12 | Herencia de redistribuir proporcionalmente unos pesos que nunca fueron estimados. Grinold–Kahn: peso ∝ IC por señal, ajustado por correlación entre señales. El IC compuesto medido es −0.05 (ruido) y los IC por dimensión no ponderan nada |
| crowding lineal ×0.12 | resta lineal | El riesgo de crowding es de cola, no lineal (momentum crashes, Daniel–Moskowitz 2016). Penalizar 55 vs 25 apenas informa; lo que mata es el extremo. Y el input es la etiqueta narrativa (el dato más ruidoso del sistema, canal lineal directo) |
| `vol_tilt_alpha` | 0.5 | No corresponde a ningún supuesto: score∝μ ⇒ α=2 (mean-variance); score∝Sharpe ⇒ α=1 (inverse-vol). El score es un percentil (ordinal, Sharpe-like), no un μ. 0.5 es un hedge indocumentado |
| caps 20% / tiers 12-8-4 | notional | Correlation-blind y vol-blind: 20% en un bucket a vol 55% ≠ 20% a vol 18% — el mismo defecto que v4 §2 A4 arregló en el sizing, vivo en el cap. **No hay matriz de covarianza en ningún punto del sistema** |
| `multi_catalyst_aggregation.reinforce_factor` | 0.25 | El noisy-OR asume INDEPENDENCIA entre catalizadores; premia el conteo — exactamente lo que la regla editorial "un catalizador = un driver" combate a mano. La duplicación encubierta paga |
| `event_catalyst_decay.default_halflife_days` | 46 | Sin fuente. (Prior defendible: el drift post-anuncio ≈60 días hábiles, PEAD — pero no está escrito) |
| `tilt_ic_target` | 0.20 | Con breadth ~26 mensual, el IC realista de rotación sectorial es 0.03–0.07 (IR = IC·√BR). A target 0.20 el tilt vivirá permanentemente a fracción. Correcto SI es half-Kelly deliberado — no está documentado como tal, así que se leerá como fracaso del modelo |

### 0.3 Lo que está bien y este plan NO toca

El softmax z-normalizado (sharpness = desviaciones típicas, invariante a compresión de
banda) · el skill shrinkage con clamp a 0 en IC negativo (Black-Litterman informal: prior
neutral + view escalada por credibilidad) · la asimetría del `net_edge_gate` · el cap
leyendo `exposure_eur` (v5.2) · el percentil cross-sectional como normalización de momentum.

**Contexto que ordena las prioridades:** con λ=0 hoy (IC medido = ruido), los pesos finos
NO determinan el retorno del libro. Lo determinan deploy_ratio, la selección/filtros y los
caps de riesgo. Por eso la fase de riesgo (I) va antes que cualquier recalibración de pesos
del compuesto (K), y por eso K espera a tener ventanas, no código.

---

## 1. Objetivos de v6

0. Que la forma del libro (cuántas posiciones, qué techo, cuántas operaciones) salga de UN
   mando declarado y de la restricción real del bróker, y que la config no pueda volver a
   quedarse conjuntamente infactible sin que la suite lo diga.
1. Que los pesos del compuesto SIGNIFIQUEN lo que dicen (conmensurabilidad), y que una
   dimensión muerta se detecte sola en vez de esperar a otra v1.6.
2. Que "no medido" deje de puntuar como "pésimo" — sin violar la doctrina v5 de que el dato
   califica y la regla actúa (columna, no gate).
3. Una covarianza en el sistema, y el riesgo por cluster de catalizador MEDIDO junto al cap
   notional — evidencia para un config edit futuro, nunca el edit.
4. Cada constante de sizing o con supuesto DECLARADO en el YAML, o con sensibilidad medida
   que demuestre que no importa.
5. Coherencia modelo↔real: un solo techo por posición, una sola semántica de exposición.

---

## 2. Fase H — Conmensurabilidad de la señal

**H1 — z-score cross-sectional por dimensión + lint de dimensión muerta.**
`sector_scorer` estandariza cada dimensión dentro del run (z winsorizado a ±3) ANTES de
ponderar; el compuesto pasa a `50 + Σ w_i·z_i·escala` (misma media, misma legibilidad
0-100, pero ahora 0.29 significa 0.29). El breakdown conserva los valores crudos — el
z-score es interno a la combinación. Nuevo lint por run: `σ_cross(dim) < umbral` →
`"dimensión muerta: <dim> (σ=X)"` en el run report. Ese lint habría cazado
`valuation_relative` en su primer run y caza el default de flow hoy. Los tests fijan la
propiedad: una dimensión constante no puede mover el ranking Y no puede pasar el lint en
silencio.

**H2 — Imputación al prior para CA sin estudio (columna, no gate).**
Sin estudio, CA se imputa a z=0 (la mediana del universo) con `ca_imputed: true` en el
snapshot y en la fila del heatmap. La doctrina v5 se mantiene test-enforced: la columna
CALIFICA (un BUY sobre CA imputada llega marcado, como llega marcado `blind`), nunca gate —
y `/catalyx-open` ya exige estudio antes de operar, que es donde la exigencia pertenece.
Resultado esperado y deseado: sectores nuevos con momentum+flow reales ENTRAN al ranking
visible y el embudo "el screen promueve a estudios" funciona por primera vez sin ayuda.

**H3 — El umbral de selección se vuelve relativo.**
`min_composite: 55` (nivel) → `min_composite_z: 0.0` (media del universo) como criterio del
perfil, manteniendo los filtros absolutos que SÍ tienen unidades (min_momentum como
percentil, max_crowding como enum-score). La selección real siempre fue "top
`max_positions` que pasan filtros"; esto solo hace que el filtro signifique lo mismo en
cada run. Migración: los perfiles YAML ganan la clave nueva; la vieja se lee un major más
(Schema Change Protocol).

**H4 — Momentum sin la pata de reversal.**
`return_1m: 0.20 → 0.0`; renormalizar a `return_3m: 0.56 / return_6m: 0.44` (misma
proporción relativa 45:35). Es un cambio de threshold congelado: config edit + CHANGELOG,
con la literatura como racional y el propio backtest v1.6 como evidencia local. Añadir
12-1 requiere ampliar el snapshot de momentum → va a K (necesita datos, no urge).

## 3. Fase I — Riesgo con covarianza

**I1 — Covarianza Ledoit–Wolf sobre retornos SEMANALES (no diarios — D10).**
Nuevo `catalyx/scorer/covariance.py`: matriz de los `primary_etf` del libro desde la misma
cache de precios (allow_fetch=False), **muestreada semanalmente** por el efecto Epps medido
en D10 — con retornos diarios la matriz reporta ρ=0.13 donde el valor convergido es 0.245, y
una covarianza que subestima a la mitad convierte el MCTR de I2 en un tranquilizante. Ventana
≥52 semanas (frente a los 120d diarios de `_sector_vols`, que se queda como está: el sesgo
Epps golpea la covarianza, no tanto la varianza propia). Shrinkage Ledoit–Wolf hacia
correlación constante — con ~26 series × 52 obs es obligatorio, no opcional. Emite JSON: vol
por vehículo, ρ medio, vol de cartera para un vector de pesos, y el ρ diario al lado con la
nota del sesgo, para que nadie lo "arregle" volviendo a diario. Sin dependencias nuevas
(~50 líneas).

**I2 — El cap por catalizador gana una columna de riesgo, no cambia de regla.**
Junto al `exposure_eur` notional (v5.2), cada fila del ledger/cap_check reporta la
**contribución del cluster a la vol de cartera** (MCTR%, vía I1). El cap sigue siendo
notional 20% y `enforcement: warn` — doctrina de casa: la medición es evidencia PARA un
config edit, nunca el edit. Cuando dos clusters al mismo notional muestren 2× de diferencia
en riesgo real, el número estará impreso y la decisión de cambiar la regla será del usuario
con evidencia, no de este plan.

**I3 — `vol_tilt_alpha` 0.5 → 1.0, con el supuesto escrito.**
El score que entra al tilt es un percentil/ordinal — proxy de "convicción por unidad de
riesgo" (Sharpe-like), no de μ. El supuesto coherente es w ∝ score/σ (α=1, inverse-vol
condicional). El miedo original ("underweight sistemático de high-beta") lo cubre la
SELECCIÓN, que es score-driven y no se toca; el tilt solo iguala el riesgo por euro entre
los ya elegidos. Config edit + CHANGELOG; el YAML gana dos líneas declarando el supuesto
(score∝Sharpe ⇒ α=1) para que el próximo debate sea sobre el supuesto, no sobre el número.

**I4 — Harness de sensibilidad: qué constantes importan.**
`experiments/sensitivity_weights.py`: perturba cada constante de la cadena (pesos del
compuesto, sharpness, α, reinforce_factor, sub-weights evento/estructural, trend deltas)
±25% y ±50%, una a la vez, sobre el último run grabado; mide estabilidad del top-10
(Kendall τ + Jaccard del set). Salida: tabla "constante → τ mínimo bajo perturbación".
Toda constante que NO voltea el ranking deja de ser debate (y se anota); toda la que SÍ lo
voltea queda señalada como la que necesita evidencia antes que ninguna otra. Es la
respuesta barata y honesta a "estos porcentajes son arbitrarios": medir cuáles tienen
consecuencias.

**I5 — Tiers y deployment con su derivación escrita.**
Sin cambio de números, cambio de fundamento visible: (a) los tiers 12/8/4 se documentan en
el YAML como presupuesto de pérdida por línea contra el floor de drawdown del exit_watcher
(12%×30% stop = 3.6% del libro · 8%→2.4% · 4%→1.2%) — Kelly fraccional operativo; el
digest de posiciones imprime esa columna (`line_risk_pct = weight × stop_distance`).
(b) `deployment.base: 0.70` se documenta como parámetro de aversión (NO deriva del ERP; el
ERP justifica estar invertido, no el 0.70 — decirlo evita re-litigarlo cada review).

**I6 — VIX en rampa.**
`vix_penalty_ramp: 0.20·clamp((VIX−25)/10, 0, 1)` sustituye al escalón. Continuo, con el
mismo máximo en VIX 35 y media penalización en 30 — el punto congelado actual queda DENTRO
de la rampa, así que el estado neutral no cambia. Config edit + CHANGELOG.

## 4. Fase J — Higiene de cálculo

**J1 — Un solo techo, y se fija DESDE `n_target`, no al revés. (REVISADO — la versión
original de este plan lo unificaba a la baja, que era el sentido equivocado: ver D9.)**
Unificar en 12% habría forzado 8 posiciones mínimo y con ellas 8 de los 10 slots mensuales.
El techo no se elige: se deriva del número de posiciones que el operador quiere sostener.
`n_target` pasa a ser el ÚNICO mando, y de él salen los demás (§L1). El model book y el
libro real comparten el número resultante — la comparabilidad del execution alpha sigue
siendo el objetivo, pero se consigue subiendo el real, no bajando el modelo.

**J2 — Deadband que no mueve lo que mantiene.**
`apply_deadband` renormaliza SOLO sobre las posiciones no-kept; si no hay margen, la
diferencia va a cash (que es lo que el band significa: "no operes esto"). Y un clamp final
a `max_position_pct` para que la renormalización no supere el cap. Tests con el caso
exacto: un kept + renorm >1 no debe generar trade en el kept.

**J3 — Una semántica de exposición también en el model book.**
`catalyst_exposure_rows` emite ambas columnas como el libro real: `pct_credit` (el split,
para atribución de retorno — suma 100) y `pct_exposure` (la posición completa por driver —
suma >100, a propósito). La partición del lake gana las dos; los lectores actuales de `pct`
se migran a la que su pregunta necesita.

**J4 — Percentil de intensidad sin saturación tendencial.**
El percentil se computa sobre la serie DETRENDED (residuo contra una media rolling de la
propia serie) cuando la serie tiene tendencia significativa; y el paso curva-saturante →
percentil se hace con blend lineal en n∈[min_history−2, min_history+2] en vez de escalón.
Mata el mini-cliff del 6º punto y devuelve poder discriminante a los indicadores
estructurales persistentes. (Es el ítem más delicado de v6: cambia scores publicados →
CHANGELOG con nota de migración y comparación antes/después impresa en el run report.)

## 5. Fase L — La restricción del bróker como ciudadano de primera clase

> Esta fase no existía en la primera redacción del plan. Nace de un dato operativo que
> ninguna parte del sistema modela: **10 operaciones gratis al mes**, y la preferencia
> explícita del operador por pocas posiciones abiertas. Es la restricción MÁS VINCULANTE
> del sistema y era invisible para el código — `fee_eur: 0.0` afirma que operar es gratis,
> y para las 10 primeras del mes lo es *contablemente*. Económicamente no: el slot es un
> recurso escaso con valor de opción, y su precio sombra es positivo siempre que la
> restricción muerde.

**L1 — `n_target` como mando único; el techo, los tiers y el cap se derivan.**
Nuevo bloque `book_shape` en `scoring_weights.yaml`:

```
n_target: 6          # la rodilla medida en D9: pasar a 7+ compra <2.5% de vol por slot/mes
neutral_weight = deploy_max / n_target            = 0.85/6 = 14.2%
max_position_pct = tier_1 = 1.4 × neutral         ≈ 20%
conviction_tiers = [1.4, 1.0, 0.5] × neutral      ≈ 20 / 14 / 7%
```

Los tiers dejan de ser tres absolutos (12/8/4) que envejecen cuando cambia el tamaño del
libro y pasan a ser MÚLTIPLOS del peso neutral — así siguen significando lo mismo si
`n_target` se mueve. Riesgo por línea resultante (I5): tier 1 = 20% × 30% de stop = **6% del
libro en una línea**; se imprime, porque un mandato declaradamente de alto riesgo debe ver
ese número, no deducirlo.

**L2 — El cap por catalizador sube a 30%, y SOLO después de que I2 exista.**
Con `n_target: 6` y despliegue 0.85, un cap del 20% exige ≥5 drivers independientes entre 6
nombres: infactible en un libro de catalizadores enlazados (D9). Sube a **0.30** (⟹ ≥3
drivers independientes a pleno despliegue: exigente y alcanzable). **Secuencia
innegociable:** el cap solo sube cuando I2 ya publica el MCTR% por cluster. Subir un cap
elimina un incumplimiento por decreto; la disciplina que ese cap prestaba tiene que estar ya
sustituida por la medición de riesgo, o esto es maquillaje. Escrito aquí para que dentro de
seis meses conste que se supo lo que se hacía.

**L3 — Presupuesto de operaciones, con precedencia por lo que es MEDIBLE.**
Nuevo bloque en `rebalance_rules`:

```yaml
trade_budget:
  free_per_month: 10          # la restricción vinculante real
  reserve_for_events: 3       # slots que un review NO puede gastar: el mandato es event-driven
  planned_max_per_review: 6   # filas que mueven dinero que un review puede emitir
  fee_after_free_eur: null    # coste explícito marginal pasada la franquicia — POR RELLENAR
  exempt_actions: [SELL, REDUCE]   # quitar riesgo nunca hace cola detrás del presupuesto
```

Cuando la tabla propone más filas que el presupuesto, se ordenan y se emiten las k primeras.
El orden **no** puede ser por retorno esperado: el IC del ranking es ruido (−0.05) y ordenar
por un retorno que no sabemos estimar sería inventar precisión justo donde el sistema ya
declaró no tenerla. Se ordena por lo que sí está medido:

1. **Riesgo que se retira** — SELL/REDUCE por régimen `breaking`, catalizador invalidado o
   suelo de drawdown. Exentas del presupuesto: la seguridad no hace cola.
2. **Coste de la inacción** — despliegue de caja ociosa hacia el objetivo. `cash_drag` es un
   coste medido, no una estimación.
3. **Rotación entre nombres ya en cartera** — turnover puro, la primera en morir de hambre.

Esa precedencia es Gârleanu–Pedersen (2013) en su forma operativa: con costes de
transacción no se opera hasta la cartera óptima sino PARCIALMENTE hacia ella, y lo primero
que se sacrifica es el ajuste fino entre posiciones existentes. El deadband ya era una
versión tosca de ese resultado; el presupuesto lo hace explícito.

**L4 — `budget` como autor de aplazamiento, distinto de `unrecorded`.**
Una fila aplazada por presupuesto **no es silencio ni desviación discrecional**: es la regla
funcionando. Se registra con `author: budget` y no entra en el tally de overrides de
`user`/`claude`. Meterla ahí envenenaría con filas que nadie eligió exactamente el contador
que v5 construyó para que la conservadurismo no saliera gratis. La doctrina v5 (toda
inacción se registra) se cumple; lo que cambia es a quién se le imputa.

**L5 — Test de factibilidad de la config.**
`tests/unit/test_config_feasibility.py` afirma las identidades de D9 como invariante:
`ceil(deploy_max / max_position_pct) ≤ n_target`, `ceil(deploy_max / correlated_cap) ≤`
drivers plausibles, `n_target + reserve_for_events ≤ free_per_month`. Un config edit futuro
que vuelva a dejar la terna infactible falla la suite en vez de descubrirse quince reviews
después. Es el mismo patrón que `BANNED_ACTION_WORDS`: la regla se vigila a sí misma.

## 6. Fase K — Necesita TIEMPO, no código (queda escrito para no re-descubrirlo)

- **K1** Pesos del compuesto por IC-por-dimensión shrunk (mismo patrón de credibilidad que
  λ: `w_i ∝ IC_i shrunk`, prior = pesos actuales). Bloqueado por: ≥3 ventanas
  independientes de 63d. Hasta entonces los pesos actuales son el prior declarado.
- **K2** 12-1 momentum en el snapshot (requiere ampliar la ventana de fetch).
- **K3** Halflife de eventos por `catalyst_type`, calibrada del lake.
- **K4** Crowding medido (comomentum Lou–Polk sobre los vehículos del lake, o percentil de
  flujos) sustituyendo o corroborando la etiqueta narrativa; y penalización convexa por
  encima de `crowded` en vez de lineal.
- **K5** `reinforce_factor` × (1−ρ) entre catalizadores (correlación de sus historias de
  indicadores en el lake): el noisy-OR deja de asumir independencia y la duplicación
  encubierta deja de pagar sola.

## 7. Orden de ejecución y por qué

```
L1 → L5 → J1   (la FORMA DEL LIBRO primero: es la restricción vinculante, y fija el techo
                que J1 ya no elige. El test de factibilidad antes que nada que lo use.)
L3 → L4        (presupuesto de operaciones + su autor de aplazamiento)
H1 → H2 → H3   (conmensurabilidad: todo lo demás rankea sobre esta base)
H4, I6         (config edits independientes, cada uno con su línea de CHANGELOG)
J2, J3         (higiene local, sin dependencias)
I1 → I2 → L2   (covarianza semanal → MCTR por cluster → SOLO ENTONCES sube el cap a 30%)
I5, I3         (derivación escrita; α=1 después de I1 para imprimir el efecto con vol real)
I4             (el harness al final: mide la cadena YA corregida)
J4             (el más delicado — solo, con su comparación antes/después)
K              (no se ejecuta: se espera)
```

L va primero porque es la restricción que muerde: mientras el techo obligue a 8 posiciones,
afinar los pesos del compuesto es optimizar dentro de un conjunto factible equivocado.
H antes que I porque el ranking alimenta todo. **L2 después de I2, nunca antes** — es la
única dependencia dura del plan y la razón está en §5. I4 al final porque medir la
sensibilidad de una cadena que vas a cambiar la semana siguiente es medir dos veces.

## 8. Lo que este plan rechaza, y por qué queda escrito

- **Black-Litterman formal.** El mecanismo λ ya ES la esencia (prior neutral + view
  escalada por confianza medida); formalizar Ω y τ sobre n_eff=1 es rigor cosmético.
- **Risk parity completo / optimizador de media-varianza.** Con 10 posiciones, caps duros y
  un mandato momentum, un optimizador MV sobre μ estimados con IC≈0 es un generador de
  ruido con pedigrí. La covarianza entra como MEDICIÓN (I1/I2), no como optimizador.
- **Invertir pesos o señales con IC negativo.** Ya doctrinal (v4): un IC negativo apaga,
  jamás invierte.
- **Sustituir el cap notional por uno de riesgo YA.** El MCTR entra como MEDICIÓN (I2); el
  cap sigue siendo notional y `warn`. Doctrina de casa: evidencia → edit, en ese orden.
- **Ajustar pesos del compuesto "a ojo" mientras llegan las ventanas de K1.** Un número
  sin estimador se cambia por un supuesto declarado (H4, I3) o no se cambia.
- **Bajar `n_target` a 4–5 "porque menos operaciones es mejor".** La medición de D9 dice que
  de 6 a 5 se PAGA un 3,2% de vol relativa por recuperar un solo slot; de 6 a 4, un 8%. 6 es
  la rodilla, no un punto medio de compromiso.
- **Un optimizador que reparta los slots por retorno esperado.** El IC es ruido; ordenar por
  un retorno que no sabemos estimar sería inventar precisión donde el sistema ya declaró no
  tenerla. Se ordena por riesgo retirado y coste de inacción, ambos medidos (L3).

## 9. Definición de hecho

- [x] **L1** `book_shape` con `n_target: 6`; techo y tiers derivados (20/14/7%) — v6.0
- [x] **L2** cap por catalizador 0.20 → 0.30, tras I2, con 2 invariantes nuevas — v6.4
- [x] **L3** `trade_budget_plan` + precedencia (riesgo → cash drag → rotación), exentos — v6.1
- [x] **L4** `author: budget`, logueado contra el run actual, fuera de `unrecorded` — v6.1
- [x] **L5** `tests/unit/test_config_feasibility.py`, 5 invariantes — v6.0
- [x] **H1** z-scores + lint dimensión muerta; `composite = 50 + 15·Σ w·z` — v6.2
- [x] **H2** CA imputada a z=0 y excluida de los momentos, `ca_imputed`, columna-no-gate — v6.2
- [x] **H3** `min_composite_z` en los 4 perfiles + ambas lentes de dislocation; fallback pre-v6 — v6.2
- [x] **H4** `return_1m` → 0, 3m/6m a 0.5625/0.4375 — v6.2
- [x] **I1** `covariance.py` (LW, semanal) + CLI JSON + `epps_gap` del propio libro — v6.4
- [x] **I2** riesgo por cluster en `cap_check` (None si no medible, nunca 0) — v6.4
- [x] **I3** α=1.0 con el supuesto declarado; efecto medido y acotado por el cap — v6.5
- [x] **I4** `experiments/sensitivity_weights.py` + perilla de control + columna `moved` — v6.6
- [x] **I5** derivación de tiers/deployment escrita + `line_risk_pct` — v6.5
- [x] **I6** rampa VIX 25→35 en lugar del escalón en 30 — v6.2
- [x] **J1** techo único derivado de `n_target`: `catalyx.yaml` 10→6 posiciones, 16→20% — v6.0
- [x] **J2** deadband sin mover kept, residuo solo en las libres, clamp a gross 100 y al cap — v6.3
- [x] **J3** `pct_credit` / `pct_exposure` en el model book y en `lake_query` — v6.3
- [x] **J4** percentil destendenciado (blend) + blend del cliff, `--compare-legacy` — v6.7
- [x] **K1–K5** anotados como bloqueados-por-datos en los TODOs de CLAUDE.md — v6.7
- [x] Suite en verde (606); cada threshold cambiado con su línea de CHANGELOG
- [ ] **Dato pendiente del usuario:** `fee_after_free_eur` — el coste explícito de la
      operación 11ª del mes. Es un hecho del bróker, no una preferencia: no se inventa.
