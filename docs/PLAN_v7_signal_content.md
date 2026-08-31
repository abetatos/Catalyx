# PLAN v7 — Contenido de señal: medir lo que ya se computa, computar lo que ya es gratis

> **ESTADO: EJECUTADO 2026-08-31 (v7.0).** M1–M6, N1–N4 y O1–O3 completos; N3 (Trends) resultó
> operativo (26/26 términos) en vez de rechazado. O2 (refresh de catalizadores blind) es
> OPERATIVO y queda para el próximo scan. Primer run con columnas: `run_20260831_172858`.
> Lo que queda es TIEMPO: ~3 ventanas de 63d antes de que una candidata pueda ganar peso.
> Desviaciones del plan escritas en la entrada v7.0 del CHANGELOG (el 13F estaba muerto desde
> el universo v2.0 y se reparó como parte de M6; `crowding_measured` entra como columna ya).
>
> Sucesor de `docs/PLAN_v6_signal_scale_and_covariance.md`
> (cerrado, v6.0 → v6.8). v6 arregló la **maquinaria** de combinación — conmensurabilidad,
> covarianza, constantes con ancla. v7 va del **contenido**: qué señales entran, cuáles ya
> están computadas sin que nada las lea, y cuáles son accesibles gratis con el acceso a datos
> REAL de este proyecto (yfinance + EDGAR + CSVs públicos + WebSearch de la sesión) y no
> están conectadas.
>
> **La restricción que gobierna todo el plan:** el IC del compuesto está medido en −0.027
> sobre UNA ventana (`noise`), así que ninguna señal nueva puede *demostrar* hoy que mejora
> el ranking. Por doctrina de la casa (v6 I2→L2: la medición aterriza antes que la regla),
> **todo lo de este plan entra como COLUMNA con peso 0** — grabada al lake, medida por
> `calibration` — y el ascenso de cualquier columna a peso es una decisión de config futura,
> con ventanas de IC delante. Este plan no toca `composite_weights` ni un solo threshold.
>
> Todo lo de §0 está medido o verificado sobre este checkout el 2026-08-31, no estimado.

---

## 0. Diagnóstico

### 0.1 Medidos sobre el lake (2026-08-31)

**D1 — Nadie publica la estructura de correlación entre las dimensiones del compuesto.**
Medido sobre los 4 runs del 2026-08-31 (Spearman, crowding invertido para alinear signos):

| par | ρ por run |
|---|---|
| momentum ~ flow | **+0.40 · +0.40 · +0.22 · +0.57** |
| momentum ~ CA | +0.14 · +0.06 · +0.02 · +0.02 |
| CA ~ crowding_inv | −0.11 · −0.23 · −0.25 · −0.25 |
| flow ~ crowding_inv | −0.19 · −0.23 · −0.20 · −0.35 |

n_eff (entropía de autovalores sobre la matriz de rangos del último run): **3.42 de 4**.

Tres lecturas. (a) La sospecha a priori "flow persigue al momentum → son una dimensión"
resulta **parcialmente** cierta: acoplados (+0.2…+0.6) pero no colapsados — la corrección de
la fuente de flow (v6.8: 12 sectores de CMF a shares reales) probablemente explica que no sea
peor. (b) La pareja es INESTABLE entre runs del mismo día — sensible al snapshot de flow que
toque — y eso en sí es un dato de calidad de señal. (c) CA ~ crowding_inv ≈ −0.25: los
sectores con mejor catalizador tienden a estar más crowded, así que el compuesto ya devuelve
parte del CA vía la penalización — es diseño (para eso existe el penalty), pero no está
publicado en ningún sitio cuánto. **Caveat honesto: 4 runs del mismo día son ≈ 1 observación;
la matriz hay que verla acumularse run a run, que es exactamente lo que M1 hace.**

**D2 — CA puntúa NIVEL de catalizador; el mercado paga el catalizador NO descontado.**
`is_priced_in_estimate` existe en 30 catalizadores de evento (`data/catalysts/`), anclado con
criterios observables (§`is_priced_in_levels`) — y **no entra al composite por ninguna vía**:
su único consumidor es `catalyst_lifecycle` (archivar eventos gastados). Un driver a
intensidad 90 y priced_in 0.75 rankea por encima de uno a 70 y priced_in 0.25, cuando la
posición mejor es la segunda. Los estructurales no llevan `is_priced_in` (0 ficheros) — su
vía de descuento es `narrative_maturity`, que sí puntúa, pero vía crowding y a nivel
*sector*, no *driver*.

**D3 — Tres señales ya computadas y grabadas que ningún ranking lee.**
- `inst_sponsorship_score` (13F breadth EDGAR, U-invertida pre-descubrimiento/convicción/
  masificado): viaja en CADA output de `sector_scorer` y pesa 0 en todo.
- `near_52w_high_pct`: **ya está en la tabla `momentum` del lake** — la señal de proximidad
  a máximo 52s (George–Hwang 2004, ancla el momentum mejor que el retorno puro) grabada
  y sin leer.
- `return_1y_pct`: **ya está en el lake** (15/26 vehículos primarios; el resto son UCITS
  con historia corta). **La nota de K2 en `CLAUDE.md` — "requiere ampliar la ventana de
  fetch del snapshot" — es FALSA hoy:** `market_data.fetch_metrics` pide `period="1y"`
  desde siempre. K2 está bloqueada por una frase stale, no por datos.

**D4 — Crowding es la única dimensión sin ninguna medición, y es la única con fuentes
gratis sin conectar.** Hoy puntúa la etiqueta LLM (`narrative_maturity` → enum → 5 valores).
K4 ya identifica el reemplazo medible; lo que este diagnóstico añade es que **ninguno exige
datos de pago**: comomentum Lou–Polk necesita solo precios (la infra de covarianza de v6 I1
ya existe), el COT de la CFTC es un CSV semanal público que cubre oro/plata/cobre/crudo — el
sleeve de metales de este libro —, Google Trends es gratis (API inestable, cadencia mensual
suficiente), y el 13F ya está computado (D3).

**D5 — La maquinaria de IC por dimensión EXISTE y las candidatas no están dentro.**
`calibration.py` mide y persiste rank IC + se + verdict por run y por dimensión (tabla
`validation/calibration`) — pero solo para las 4 dimensiones oficiales. Una señal candidata
que no se graba como columna del snapshot **no acumula historia de IC**, y cuando llegue el
momento de decidir pesos estará donde K1 está hoy: bloqueada por ventanas que no se
recogieron a tiempo. Cada run que pasa sin grabar las candidatas es una ventana perdida.
**Este es el ítem más urgente del plan y la razón de su orden de ejecución.**

**D6 — No hay valoración en ningún punto del sistema.** `dislocation` es precio-contra-precio
(beta, contagio); v1.6 eliminó `valuation_relative` porque era una constante-50, y el
backtest que lo justificó midió *aceleración de momentum*, no valoración — "ningún métrico
derivado de precio gana ese 15%" es una generalización desde n=1 métrico. Un libro
catalyst+momentum+flow compra sistemáticamente lo caro acelerando; sin ancla de valor el
modelo no distingue "temprano" de "tarde" en el ciclo del tema. El acceso retail a valoración
es pobre (yfinance `.info` a nivel fondo, spotty) — razón para grabarla como serie best-effort
desde ya, no para pesarla.

**D7 — La mejor señal del sistema es la peor mantenida.** El único edge no-commodity de
CATALYX es la batería de indicadores estructurales curados a mano (WGC, COFER, capex
hyperscaler, ARR…) — momentum, flow y vol los computa cualquiera. Y el estado medido (v5) es
que **todos los BUY de la tabla actual se apoyan en evidencia stale o blind, cuatro de ellos
148–240 días ciegos**. Refrescar esa señal rinde más que añadir la quinta. Parte es cadencia
de scan (operativo), pero parte es automatizable: varias series macro de los indicadores
viven en FRED (key gratuita).

### 0.2 Lo que está bien y este plan NO toca

- **La maquinaria de combinación** — z-space, winsor, imputación al prior, lint de dimensión
  muerta, `skill_shrink` (v6). Decil alto de la práctica profesional; nada que tocar.
- **La separación qué / si-barato / cuándo / cuánto** (composite / dislocation /
  entry_timing / sizing). Evita el error clásico de meter timing en el ranking.
- **Los pesos 35/29/24/12** — congelados hasta K1 (≥3 ventanas independientes). Este plan
  expresamente no los discute: con IC ruido, discutir 0.35 vs 0.30 es decorar.
- **Las fallback chains de flow** — bien resuelto dado el acceso (proxies US para temas
  fungibles, UCITS-only donde el proxy mediría otra base inversora).
- **El inventario de lo INALCANZABLE, para dejar de pensarlo:** revisiones de beneficios
  agregadas (IBES), flujos institucionales reales (EPFR), posicionamiento dealer/gamma,
  holdings diarios, intradía. Si una mejora futura "necesita" una de estas, la mejora no
  existe. Queda escrito aquí para no re-descubrirlo.

---

## 1. Objetivos de v7

1. **Toda señal candidata accesible con el acceso real queda grabada como columna del run**,
   dentro de la maquinaria de `calibration`, para que la decisión de pesos de 2027 tenga
   historia en vez de volver a estar bloqueada (D5).
2. El diagnóstico de `commensurate()` publica la **estructura de correlación** entre
   dimensiones — la pregunta previa a cualquier debate de pesos (D1).
3. **Crowding gana mediciones** al lado de la etiqueta (D4), con el mismo patrón I2→L2:
   la disciplina sustituta aterriza ANTES de que la regla cambie.
4. La frescura de la señal propia (indicadores estructurales) gana automatización donde
   la fuente lo permite (D7).
5. **Ningún peso del compuesto cambia.** Un test lo fija.

---

## 2. Fase M — Publicar lo que ya se sabe (coste ≈ cero, solo yfinance/lake)

**M1 — Matriz de correlación entre dimensiones + n_eff en el diagnóstico de `commensurate()`.**
Spearman por pares sobre las dimensiones alineadas (crowding invertido) + n_eff por entropía
de autovalores, dentro del dict que `commensurate()` ya devuelve, y persistido con el run.
Lint (aviso, no gate) si un par supera |ρ| > 0.6 de forma persistente: "estas dos dimensiones
están rankeando lo mismo; sus pesos se suman de facto". Los números de D1 dejan de ser una
medición de una tarde y pasan a ser una serie.

**M2 — `momentum_12_1` como columna.** `((1+r_1y)/(1+r_1m)) − 1`, percentil transversal —
el estándar de la literatura (Jegadeesh–Titman), consistente con la decisión v6 H4 de anular
el 1m por reversal. Los datos YA fluyen (D3); esto es leerlos. 11/26 vehículos sin 1y →
null, imputación al prior en la columna estandarizada, flag — el patrón H2, nunca el peor
caso. **Ejecuta K2 de facto** (como columna, no como reemplazo del blend 3m/6m — eso sería
un cambio de regla y espera IC). Corrige la línea stale de K2 en `CLAUDE.md`.

**M3 — `near_52w_high` como columna del snapshot de scores.** Ya está en la tabla momentum
del lake; propagarla al snapshot y estandarizarla. Señal hermana del 12-1, gratis del todo.

**M4 — `ca_unpriced` como columna.** Por evento: contribución escalada por
`(1 − is_priced_in)`. Por estructural (sin `is_priced_in`): mapping declarado
`narrative_maturity → priced_in` (ignored 0 / emerging 0.25 / mainstream 0.5 / crowded 0.75 /
exhausted 1.0 — la misma escala escalonada que ya existe) o null honesto si el catalizador no
tiene maturity. Publica junto a CA la versión "sorpresa" del nivel, para poder medir cuál de
las dos ordena mejor ANTES de decidir nada (D2).

**M5 — `flow_resid` como columna.** Residuo OLS del z-flow sobre el z-momentum dentro del
run: el flujo NO explicado por el retorno reciente, que es la parte informativa según la
literatura de feedback trading. Barato porque ambos z ya existen en `commensurate()`. La
comparación IC(flow) vs IC(flow_resid) que `calibration` acumulará es la respuesta empírica
a D1 para esta pareja.

**M6 — Las candidatas entran en `calibration.DIMENSIONS`.** `momentum_12_1`,
`near_52w_high`, `ca_unpriced`, `flow_resid`, `inst_sponsorship` (que lleva meses grabándose
sin medirse) — mismas métricas que las oficiales: rank IC, se, verdict, per-run y agregado.
**Este ítem es el que arranca el reloj**; todo lo anterior existe para alimentarlo. Las
columnas de la fase N se añaden aquí cuando existan.

## 3. Fase N — Crowding medido (dirección K4; la regla no cambia)

**N1 — Comomentum Lou–Polk sobre los vehículos.** Correlación media intra-tema de los
residuos de retorno (la firma de capital masificado moviéndose junto), sobre retornos
semanales reutilizando `scorer/covariance.py` (v6 I1). Solo precios, cero fuentes nuevas.
Columna `crowding_comomentum`.

**N2 — COT (CFTC, CSV semanal público).** Net spec positioning como percentil de su propia
historia, mapeado a los sectores con futuro subyacente: oro (físico y mineras), plata,
cobre, crudo. Columna `cot_crowding`, null en el resto del universo — cubre poco universo
pero cubre el sleeve donde este libro concentra, y es posicionamiento REAL, no narrativa.
Fetcher nuevo pequeño + tabla del lake para la historia (el percentil necesita serie).

**N3 — Google Trends (pytrends, mensual).** Interés de búsqueda por tema como percentil
vs ~5 años: el proxy medible de la atención retail que `narrative_maturity` etiqueta a ojo.
Fuente inestable → carried-forward + flag de frescura, el mismo patrón que flow ya usa.
Columna `trends_crowding`. Si pytrends resulta demasiado frágil en la práctica, el ítem se
cierra como "rechazado por fiabilidad de fuente" con la evidencia — no se mantiene un
fetcher zombie.

**N4 — `crowding_measured`: el blend declarado de lo disponible.** Combinación escrita
(media de las disponibles, con el 13F entrando por su U-invertida) de
comomentum / COT / trends / inst_sponsorship, null si ninguna existe para el sector.
Columna al lado de la etiqueta — **el swap etiqueta→medición en el compuesto es una decisión
de config futura, cuando N4 tenga ventanas de IC** (patrón I2→L2: primero la medición
publica, luego el usuario decide). La etiqueta narrativa queda como fallback documentado
donde ninguna medición llega.

## 4. Fase O — Frescura y valoración (datos, no señal nueva)

**O1 — FRED para los indicadores estructurales automatizables.** Inventariar los ~48
indicadores activos: cuáles son series FRED (tipos, HY OAS, breakevens, DXY, TIPS…) y
conectarlos a `indicator_update` como observación automática en `pre_run`/scan — misma vía
de escritura que hoy, nunca un canal paralelo. Los indicadores no-FRED (WGC, COFER, capex,
ARR) siguen siendo trabajo del scan: **son el edge precisamente porque no hay API** (D7).
Requiere key gratuita de FRED (dato a pedir al usuario).

**O2 — Refresh de los catalizadores blind.** Operativo, no código: los 4 catalizadores
148–240 días ciegos que sostienen BUYs actuales, priorizados en el próximo `/catalyx-scan`.
`pre_run` ya los lista; este ítem existe en el plan solo para que el orden de ejecución
diga en voz alta que va ANTES que cualquier fetcher nuevo.

**O3 — Serie de valoración best-effort al lake.** Por vehículo, lo que yfinance `.info`
devuelva (`trailingPE`, `priceToBook`) con flag de cobertura, cada run. Sin columna en el
snapshot siquiera — solo la serie, para que un test de ancla de valor sea POSIBLE en 2027
(D6). La lección de K1 aplicada antes de necesitarla: la calibración se bloquea por datos
que no se recogieron a tiempo.

---

## 5. Lo que este plan rechaza, y por qué queda escrito

- **Tocar `composite_weights` o añadir una quinta dimensión con peso.** Bloqueado por K1
  (≥3 ventanas). Todo lo de arriba es columna.
- **Reemplazar flow por `flow_resid` (o el blend 3m/6m por 12-1) DENTRO del compuesto.**
  Cambio de regla; espera a que M6 acumule IC comparado. La columna existe justo para que
  esa decisión futura sea una lectura, no una fe.
- **Put/call OI por ETF** (yfinance option chains): ruidoso a horizonte mensual, cadenas
  frágiles en UCITS, mantenimiento > valor esperado.
- **Short interest FINRA:** cadencia bimensual, solo US, horizonte mal casado con el mensual.
- **Scraping de issuers para P/E de fondos:** frágil; O3 captura lo capturable sin
  mantenimiento.
- **Backfill retroactivo de las columnas nuevas en runs viejos.** Un snapshot es lo que se
  sabía ese día; recomputar señales hacia atrás con datos de hoy fabrica look-ahead en la
  misma tabla que `calibration` lee. Las columnas empiezan donde empiezan.

---

## 6. Orden de ejecución y por qué

```
O2  (operativo, próximo scan)     la mejor señal existente se refresca antes de añadir ninguna
M2 M3 M4 M5  (columnas)           coste ≈ cero, solo lecturas de datos ya presentes
M6  (calibration)                 EL ÍTEM CRÍTICO: arranca el reloj de ventanas de las candidatas
M1  (diagnóstico correlación)     convierte D1 en serie
N1  (comomentum)                  gratis con la infra I1
N2 N3  (COT, Trends)              fetchers nuevos — después de que todo lo gratis esté grabado
N4  (blend crowding)              cuando N1–N3 existan
O1  (FRED)                        necesita key del usuario; independiente del resto
O3  (valoración)                  independiente, cualquier momento
```

La regla del orden: **primero lo que arranca relojes** (M6 — cada run sin él es una ventana
perdida para todas las candidatas), **después lo que añade fuentes** (N2/N3, que traen
mantenimiento), y el refresh operativo (O2) por delante de todo porque mejora la señal con
mayor peso del sistema sin escribir una línea.

## 7. Definición de hecho

- El run graba `momentum_12_1`, `near_52w_high`, `ca_unpriced`, `flow_resid` (+ las N cuando
  existan) y `calibration` las mide con IC/se/verdict por run y agregado.
- `commensurate()` publica la matriz de correlación entre dimensiones + n_eff, persistida.
- `composite_weights` y todos los thresholds congelados: **sin cambios**, con un test que
  falla si una candidata adquiere peso > 0 sin tocar este plan y el CHANGELOG.
- La línea stale de K2 en `CLAUDE.md` corregida; entrada de CHANGELOG por versión ejecutada.
- Suite completa en verde; cada columna nueva con tests (incluido el caso null/sin-historia).
