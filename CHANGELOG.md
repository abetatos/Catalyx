# CATALYX Changelog

> Full version history. **Do not read this file every session** — the `Recent Changes` table in `CLAUDE.md` covers the last 5 entries and is always in context.
> Read this file only when you need to answer: "when did X change?", "what was the previous formula?", "why was field Y added?"
>
> **How to add an entry:** when `Recent Changes` in CLAUDE.md reaches 6 entries, move the oldest row here verbatim and add detail below it.
>
> **Versioning (since v0.3.1):** SemVer, **pre-1.0** (early/unstable), one number in `pyproject.toml`, tagged `vX.Y.Z` on `main`. See `RELEASING.md`. The earlier `vN.M` labels below are an informal pre-tag change counter, not SemVer.

---





## v14.3 — El spec oficial de momentum llegaba al 48% del universo, y lo decidía el calendario de la plaza (2026-09-02)

**Observación del usuario, no un test:** «la prioridad real es la cobertura, no el horizonte —
un lookback bien elegido que solo alcanza al 42% del universo es un problema mayor que la
diferencia entre 9-1 y 12-1». Correcto, y peor de lo anotado: el run vivo
`run_20260901_165828` corría con **12 de 25** sectores en `12_1_52w` y 13 en `3m6m_fallback`.

**La causa es una resta.** `momentum_spec.mode: "12_1_52w"` es el spec PROMOVIDO CON BACKTEST
en v8 (176 meses, IC +0,104 contra +0,080 del 3m6m). Necesita `return_1y_pct`, y
`_safe_return(closes, 252)` pide **253 BARRAS**. `fetch_metrics` pedía `period="1y"` = 365 días
**de calendario**, y cuántas barras devuelve eso no es una propiedad del fondo sino de la plaza:
medido sobre el universo vivo, **Xetra 251-252 · SIX 247 · LSE 252-254 · Euronext 255**. El
corte caía justo por el medio de esa distribución. Ningún sector estaba en el fallback por
falta de historia — los fondos tienen años; estaban por el calendario de su bolsa.

**Y el sesgo se agravaba solo.** Las migraciones por realidad del bróker mueven vehículos HACIA
Xetra (v14.1 `BTEC.L`→`NBTK.DE`, v14.2 `SEMI.L`→`SEC0.DE`), así que cada corrección hacia un
vehículo más comprable ENCOGÍA la cobertura del vehículo puntuado. El siguiente run habría sido
10 de 25.

**El arreglo no es ampliar `period=`, es dejar de tener una segunda fuente.**
`catalyx/data/prices.py` existe desde v3 para colapsar los ~15 fetches independientes de un
review, y su docstring nombra a `market_data` **el primero** de la lista de ofensores —
`market_data` era el ÚLTIMO módulo que seguía sin usarlo, con su propio
`yf.Ticker().history()` por ticker y el cache compartido solo para el FX. Ese cache ya tenía
400 días / 276-282 barras para los 26 vehículos, **en el lake, hoy**: la cobertura se arreglaba
sin una sola llamada de red nueva. Ahora `run_snapshot` pide UN panel para todo el universo
(`price_panel`), `compute_metrics` es pura y no sabe de red, y la conversión a EUR sale de
`market_data` entera — `_to_eur_series` era una segunda implementación de la regla GBp/FX que
`prices.to_eur` ya tenía, y de la que este fichero exige una sola copia. `market_data` ya no
importa yfinance, y un test lo fija.

**`MOMENTUM_HISTORY_DAYS: 450`, no 400.** El cache cold-fetch está en 400 y habría funcionado
(272-282 barras contra 253), pero `GLUX.SW` quedaba a **19 barras** del precipicio y un margen
fino aquí falla como falló el anterior: en silencio, por plaza, y hacia MENOS cobertura. 450 da
306-315 barras, ~55 de margen. Medido tras el cambio: **25 de 25** sectores en `12_1_52w`, los
25 en base EUR.

**El otro arreglo va en el mismo commit porque el primero lo habría roto.** `high_52w` era
`closes.max()` sobre **toda la serie traída**, o sea el significado de la proximidad al máximo
de 52 semanas —el **36,5%** del blend oficial— estaba atado a la ventana de fetch. Mientras la
ventana fue `period="1y"` las dos coincidían y nada se veía mal; profundizarla para arreglar la
cobertura habría convertido `near_52w_high_pct` en proximidad a un máximo de 15 meses para las
26 filas a la vez, en silencio. Windowed a `MOMENTUM_WINDOW_BARS = 252` — la misma unidad en la
que cuenta `_safe_return`, así que el máximo y el ancla del 12-1 se leen del mismo trozo de la
misma serie. **Medido, el efecto de este acoplamiento hoy era exactamente 0,00 en los 26
vehículos** (nadie hizo máximo en las ~26 barras extra del cache de 400 días): no arregla nada
visible, y ése es el punto — era latente, sin test, y a una barra de re-rankear el universo por
accidente. Cuatro tests lo fijan, incluido el invariante que hace seguro el cambio de ventana
(misma serie, fetch más profundo, lectura idéntica del máximo).

**El re-rank, medido dos veces.** Contrafactual offline (mismos vehículos, mismos scores en todo
lo demás, solo la ventana) y después el run grabado, y coinciden: **19 de 25 sectores se mueven,
máx 8 puestos, Spearman 0,90**. Un 42% de cobertura no era ruido de segundo orden — **el top-6,
que CON `n_target: 6` ES EL LIBRO, cambia de composición**:

| sector | rank | momentum | spec anterior |
|---|---|---|---|
| `copper_miners` | 5 → **1** | 63,5 → 88,2 | `3m6m_fallback` |
| `ai_infrastructure_data_centers` | 12 → **6** | 36,5 → 72,5 | `3m6m_fallback` — **ENTRA** |
| `water_infrastructure` | 2 → **7** | 59,6 → 38,4 | `3m6m_fallback` — **SALE** |
| `space_defense_satellite` | 21 → **13** | 5,8 → 50,3 | `3m6m_fallback` |
| `luxury_goods` | 13 → **20** | 40,4 → 25,0 | `3m6m_fallback` |

**Lo que esto le hace a la tabla hay que decirlo entero:** `water_infrastructure` es la fila de
**€1.732 ejecutada ayer** —encolada por el ramp, ejecutada como override autor `user`— y la
tabla nueva emite **`TRIM water_infrastructure €-1.738`** (17,4pp sobre target) + **`BUY
biotech_drug_development €1.490`**. El `SELECTION` lo cifra: el corte se decidió por **+0,074
composite_z**. No se ejecuta nada aquí — el review recomienda y no opera —, pero la fila
existe porque la cobertura elegía posiciones, que es exactamente lo que había que arreglar.

**Lo que NO se ha tocado, a propósito.** Ni un peso, ni un umbral congelado, ni un vehículo, ni
el propio spec: sigue siendo `12_1_52w` con el reparto 0,635/0,365 que promovió el backtest. La
pregunta 9-1 vs 12-1 sigue abierta y sin evidencia, y mezclarla aquí habría destruido la
atribución del efecto — el re-rank de arriba es de la VENTANA y de nada más.

**Residual nombrado, no arreglado:** `_safe_return` cuenta BARRAS, no fechas, así que el ancla
de «252 barras atrás» cae en un rango de **13 días** entre sectores (2025-08-21 `GLUX.SW` ↔
2025-09-03 `IUHE.AS`) y un ranking transversal compara ventanas ligeramente distintas. Es de
segundo orden al lado de lo anterior, pero es el mismo mecanismo, y arreglarlo mueve TODOS los
retornos — o sea, otro re-rank por otra razón dentro del mismo edit. Queda como decisión
abierta con el precio delante.

**Y un footgun preexistente que salió al probar el propio arreglo:** un `--tickers` ad-hoc
escribía el snapshot del DÍA. Ya se saltaba la partición del lake por esta razón exacta
(«colisionaría con la partición diaria real»), pero el JSON lo pisaba igual — un smoke test de
dos tickers reemplazó el snapshot de 25 sectores de un run ya grabado, en silencio y bajo el
nombre que todo lector resuelve por fecha. El JSON se reconstruyó desde la partición del lake
(que es la fuente de verdad y estaba intacta) y ahora un run ad-hoc escribe un fichero ad-hoc:
misma regla en los dos sumideros. Test incluido.

930 tests (+12), 1 fallo **preexistente y ajeno**
(`test_intensity_is_idempotent_across_repeated_write_backs`, verificado contra HEAD) —
**arreglado justo después**, y no era un bug del motor sino deriva de fixture: el test leía la
historia VIVA de `cb_gold_accumulation.yaml` para afirmar que el score se MUEVE, y
`_TREND_DELTAS` es una función escalón, así que cuando la historia real de gold se volvió una
racha bajista la fila que el test inyectaba cayó en el mismo escalón. Rehecho sobre un
catalizador sintético: **931 en verde**.

## v14.2 — Dos cotizaciones del mismo fondo, y la tabla solo veía una (2026-09-01)

**No salió de un test: salió de ejecutar.** El usuario ejecutó las cuatro filas de la tabla del
run `run_20260901_165828` entre las 18:34 y las 18:39 — pharma €935, semis €564, water €1.732,
robotics €1.005 — y al registrarlas y recalcular, la tabla volvió a pedir **`ADD semis €564`**:
dinero que ya estaba puesto.

**La causa es una línea.** `movement_repo.positions()` está indexado por ETF, y eso es correcto y
tiene que seguir así — dos cotizaciones de un fondo son dos LOTES, con su cantidad, su base de
coste y su divisa de cotización, y el NAV tiene que marcar cada uno a su propio precio. Pero la
tabla de rebalanceo hace una pregunta de SECTOR («cuánto del libro está en semis») y la contestaba
con `{p["sector_id"]: p for p in book["positions"]}`: **con dos lotes del mismo sector, el segundo
pisa al primero**. Gana el último, y el dinero del otro lote desaparece de la comparación sin
error, sin aviso y sin traza.

**Por qué apareció justo ahora.** El add de semis se ejecutó en **SEC0.DE** — la línea de Xetra en
EUR — mientras el lote en cartera vivía en **SEMI.L**, la de LSE en GBP. Mismo fondo (`longName`
idéntico en yfinance, mismo AUM), una sola posición económica, dos lotes. La tabla leyó `act% 4,6`
del lote de SEMI.L solo e imprimió un `ADD €564` **para comprar por segunda vez la misma
exposición**. Todo lo demás estaba bien: el fill, el fichero de movimiento y `positions()` eran
correctos. Pharma no lo sufrió porque su add fue sobre `IUHE.AS`, el mismo ETF que ya tenía — el
defecto solo se manifiesta cuando el bróker te lleva a otra línea del mismo fondo, que es
exactamente lo que acaba de pasar por primera vez.

**El arreglo es `_positions_by_sector()`, y cada regla de fusión se inclina hacia ACTUAR:**
- `invested_eur` **suma**. Ese era el problema entero.
- `market_value_eur` suma **solo si TODOS los lotes están marcados**, si no `None`. Sumar un lote
  marcado a mercado con uno a base de coste es el bug de divisas de v2.25 con otro disfraz;
  `None` es un hecho que el llamante ya sabe tratar (cae a `invested` sumado), una mezcla no.
- `suggested_action` y `drawdown` toman **el más severo** de los lotes. Una fusión que escondiera
  la señal de salida de un lote detrás de otro más sano sería un mecanismo que cancela una venta,
  y esos tienen que fallar **hacia la venta**.
- `etf` nombra el lote que la tabla debe operar: el que coincide con el `primary_etf` de hoy si
  alguno lo hace, si no el mayor por base de coste. Nombrar un vehículo que hoy nadie compraría es
  cómo un universo stale llega a la orden.
- `attribution` concatena — un driver detrás de cualquier lote es un driver detrás de la posición.

**Medido, misma cartera y mismos scores:** semis pasa de `act% 4,6` con `ADD €564` a **`act% 10,2`
contra un target de 10,2 → HOLD**, gap €1. Desplegado **57% → 63%** (los €564 fantasma dejan de
contar como caja), y la tabla queda en **HOLD en las cinco líneas**: el libro está donde el modelo
lo quiere.

**Estado tras ejecutar las cuatro filas:** desplegado **21% → 63%** (€6.306), shortfall **54,3 →
11,9pp**, vol del libro **13,0%**, ρ medio 0,271. `struct_ai_capex_supercycle` sigue en **85,2% de
la varianza** contra un presupuesto del 35% — la concentración que la salida de cloud agravó no la
arregla desplegar.

**Las tres filas encoladas se ejecutaron, y eso es un override, no cumplimiento.** El ramp
concedió UNA fila este ciclo (robotics €1.005) y encoló las otras tres (€3.231). Se ejecutaron
igual, llevando el despliegue de 21% a 63% a los precios de una sola tarde — que es la apuesta de
timing que `max_step_pp` existe para trocear. Registrado en el `override_log` de
`run_20260901_165828`, autor **`user`**, `rule_trade_eur: 0` contra el `chosen_trade_eur` real, y
se preciará a ~21 días contra el €0 que el ramp autorizaba. La razón que lo sostiene, escrita: el
shortfall llevaba 3 runs por encima del límite y la regla de persistencia obliga a ejecutar o a
justificar. `water` además venía marcada `**` — excedía un tramo entero (€1.732 > €1.500), o sea
que a tamaño completo no era «la próxima revisión» sino **nunca**; ejecutarla resuelve de facto el
conflicto entre `max_step_pp` y `max_position_pct` que el fichero de proyecto dejó a propósito sin
resolver.

**Config tocada por los fills, no por criterio.** `SEC0.DE` entra en `etf_universe.yaml` como
`verified` tier-1 (mismo fondo que SEMI.L, línea EUR — para un residente en España evita el spread
de FX que la GBP cobra en cada operación), y pasa a **cabeza de la cadena de momentum**: la regla
es puntuar el vehículo que se OPERA, y `test_the_scored_vehicle_is_the_recommended_vehicle` lo
cazó en el mismo commit en cuanto `primary_etf` y `SECTOR_TICKERS[0]` divergieron. `IQQQ.DE` y
`2B76.DE` pasan de `assumed` a `verified` con su `verified_note` — el contrapunto útil al `assumed`
de WCLD.L que falló el mismo día.

918 tests en verde (+4): los tres de fusión de lotes y el de paso-por-referencia cuando el sector
tiene un solo lote.

---

## v14.1 — El segundo `assumed` que falla, y esta vez lo que falta es la PLAZA, no el fondo (2026-09-01)

**Mismo disparador que v14.0 y desenlace opuesto.** El usuario, delante del buscador del broker:
«el BTEC no lo tengo, tengo OM3E y NBTK». `BTEC.L` era tier-1 de `biotech_drug_development` desde
el corte del 08-27, era el ticker contra el que se puntuaba el sector y el que la tabla venía
recomendando. Es el segundo `assumed` comprobado en 24 horas y el segundo que se cae — pero
**aquí el instrumento existe y es comprable**: `OM3E` es el MISMO fondo (iShares IV,
`IE00BYXG2H39`), su línea alemana en EUR. Lo ausente es la cotización de LSE, no el vehículo, así
que **el sector no se retira**: es el patrón de `IQQQ.DE`, `PPFB.DE` y `2B76.DE` (comprar la línea
que el buscador devuelve), no el de `cloud_software_saas`. Lo que sí es nuevo frente a esos tres
es que **cambia el emisor**, porque del fondo de iShares no hay línea de Xetra: yfinance solo lo
cotiza en plazas regionales (Múnich, Düsseldorf, Hamburgo).

**Cuál de los dos comprables PUNTÚA es una decisión medida, no una preferencia.** `OM3E` es el
fondo grande ($1.147M) y el sucesor natural, y aun así **no es el que se puntúa**: su cotización de
Múnich es de creador de mercado — volumen medio **6 títulos**, bid/ask 8,789/9,11 = **358bps**,
**245 de 254 días a volumen cero** — y mide **ρ semanal EUR 0,727 contra SU PROPIA línea de LSE**,
que siendo el mismo fondo debería dar ~1,00. Ese 0,27 es ruido de cotización, no exposición, y
puntuar sobre él lo metería entero en el compuesto. `NBTK.DE` (Invesco NASDAQ Biotech UCITS,
XETRA, EUR, $476M, volumen medio ~6.000, spread **37bps**) sigue el **mismo índice** NASDAQ
Biotechnology y mide **ρ 0,984** contra `BTEC.L`, con retornos 3m/6m/12m dentro de **0,6pp**.
Nótese el contraste con v14.0, donde los dos sustitutos evaluados medían la beta equivocada
(`QDVE.DE` ρ 0,74 con semis, que ya se tienen): **aquí el sustituto ES el índice**, así que
sustituir no es meterse en la SELECCIÓN. `NBTK.DE` a tier-1 (y `SECTOR_TICKERS`), `OM3E.MU` tier-2
comprable, `HEAL.L` sin comprobar, `BTEC.L` a **`broker_absent`** tier-3 — conservado intacto,
vuelve a tier-1 el día que aparezca. `primary_etf` → `NBTK.DE`; `flow_data` mantiene `IBB` detrás
porque, siguiendo NBTK el mismo índice, `IBB` es su hermano US exacto. Ambos nuevos quedan
`assumed`, no `verified`: en este fichero `verified` significa **operado de hecho**, y la
confirmación del buscador va en las notas, igual que en `PPFB.DE`.

**El estudio se corrige donde está vivo y NO se le toca la fecha.** `key_metrics_to_monitor` decía
«BTEC.L (traded vehicle)» y ahora nombra `NBTK.DE`/`OM3E`; el bloque `etf_analysis` (DEPRECATED
desde schema 1.3) se anota apuntando a `etf_universe.yaml`. `last_updated` **no se sube a
propósito**: ha cambiado el vehículo, no la evidencia, y subirla fingiría frescura sobre un estudio
que la propia tabla marca `blind` a 148 días.

**Y el hallazgo del re-score vale más que el cambio de vehículo.** El coste directo es
despreciable: momentum **79,0 → 79,4**. Pero `NBTK.DE` **no tiene retorno 12-1**, porque un fetch
de `period="1y"` devuelve **251 barras** en Xetra y `_safe_return` necesita 253 — así que biotech
cae del spec oficial de v8 (`12_1_52w`, promovido con backtest) al `3m6m_fallback`. Medido sobre
el universo entero: **15 de 26** vehículos de `chain[0]` están HOY en el fallback, y una ventana de
15 meses recupera el 12-1 en todos los probados (NBTK 53,72 · 4COP 92,44 · IQQQ 1,37 · 2B76 39,39
· INFR 9,70 · GLUX −0,76). O sea: **el spec oficial de momentum se está aplicando a 11 de 26
sectores**, y los excluidos son justamente las líneas de Xetra que la migración por realidad del
bróker no para de añadir — la cobertura ENCOGE cada vez que se corrige un vehículo. **No se
arregla aquí:** ampliar la ventana de fetch re-rankea el universo entero, y hacerlo dentro del edit
que cambia un vehículo destruye la atribución del efecto — la misma razón por la que `n_target`
sigue sin moverse. Queda como decisión abierta con la medición delante. 914 tests en verde.

---

## v14.0 — El primer `assumed` que se comprueba y falla: cloud sale del universo (2026-09-01)

**No salió de un test ni de una auditoría: el usuario fue a comprar y el vehículo no estaba.**
`WCLD.L` llevaba `broker_access: assumed` desde el corte de universo del 2026-08-27 — «UCITS en
LSE/XETRA/Euronext/SIX, alta probabilidad en Revolut ES, SIN confirmar en la app». Es el primero
que se comprueba de verdad, y falla.

**Lo comprobado, no lo supuesto.** Se buscaron uno a uno, con ticker, nombre e ISIN: WisdomTree
Cloud Computing (`IE00BJGWQN72`) en sus cuatro plazas — LSE USD, Milán EUR, París EUR, SIX USD —
más la línea GBp `KLWD.L`; First Trust Cloud Computing (`IE00BFD2H405`) como `SKYE` en Xetra y
Ámsterdam, `FSKY` en Milán, `SKYU` en LSE; HAN-GINS `SKYY` en LSE y Milán; iShares Digitalisation
`DGTL` en Ámsterdam, LSE y SIX. **Ninguno aparece**, y solo se ofrece CFD. El control es lo que
hace la conclusión firme: `USPY.L`, `SEMI.L` y `4COP.DE` **sí** aparecen en el mismo buscador —
los tres `verified`, operados de hecho. **El problema es la lista curada del bróker, no la
regulación ni el buscador**, y por eso no es el caso PRIIPs de `oil_field_services`: allí no
existe UCITS; aquí existen cuatro y son correctos.

**Se evaluaron dos sustitutos y los dos miden la beta equivocada.** No se aceptó ninguno, y las
dos razones son medidas, no argumentadas.
- **`QDVE.DE`** (iShares S&P 500 IT, Xetra, EUR, AUM $18.400M) es un instrumento impecable y no
  es cloud: ρ semanal en EUR sobre 52 semanas **0,74 con `SEMI.L`** — que YA se tiene — contra
  **0,36 con `WCLD.L`**, el que sustituiría. Comprarlo como cloud es **doblar semis** y anotarlo
  en la casilla equivocada; es el defecto por el que `XAIX.DE` está degradado a tier-2.
- **`USPY.L`** correlaciona **0,83** con `WCLD.L`, y precisamente por eso es la trampa: es el
  vehículo de `cybersecurity_commercial` **cerrado ese mismo día** (+€42,82 brutos, €8,14 de CGT
  ya devengados), y recomprarlo bajo otra etiqueta convierte una rotación ejecutada en un
  round-trip. Además ρ 0,83 **no es identidad**: en el mismo run puntúan momentum **81,5 contra
  41,7** y CA **71,9 contra 97,7** — comparten el factor «software US de beta alta», no el nivel
  ni la tendencia ni los drivers. Sustituir por correlación es meter ρ en la **SELECCIÓN**, que
  es exactamente lo que v13.0 declinó hacer sin un IC delante.

**Qué se hizo.** `cloud_software_saas` → `investable: false` + `watch_only: true`, con el patrón
completo de los retirados: la entrada se queda ENTERA en la taxonomía con `retired_2026_09_01`,
el motivo medido, y **dos `watch_triggers`** que la devuelven sola (que Revolut liste cualquiera
de los seis como ETF, o que se abra un bróker con acceso a LSE/Xetra — el vehículo y la tesis ya
están verificados, solo faltaba la plaza). En `etf_universe.yaml` **tampoco se borra nada**: se
introduce un tercer valor, **`broker_access: broker_absent`** = instrumento VÁLIDO (UCITS + KID +
plaza PRIIPs) que el bróker del usuario NO lista, buscado y ausente. Sigue sin existir `blocked`:
lo *regulatoriamente* imposible se borra; esto se conserva porque volvería intacto.

**Y el filtro tenía que vivir en el read-path, no en si la fila existe.** `snapshot_repo.primary_etf`
filtraba por VENUE, que es una **heurística** de comprabilidad; `broker_absent` es una **medición**
de lo mismo, y la medición gana. Sin ese cambio la entrada conservada seguiría resolviendo como
vehículo primario — un `BUY €1.265,87` contra un ticker que no se puede comprar, que es
literalmente el fallo que el docstring de esa función lleva describiendo desde el 08-28: *«un
fallback silencioso a un ticker no comprable es estrictamente peor que None»*. `primary_etf` ahora
excluye `broker_absent`, y el sector resuelve a `None`.

**El coste, medido.** Era el **rank 2 de 26** (composite 62,2, CA 97,7, crowding 2,8) con un BUY
vivo de **€1.265,87**. El libro REAL nunca lo tuvo — ningún `movement` lo referencia — así que no
hay posición que deshacer ni P&L que realizar. El universo pasa de 26 a **25** sectores.

**Lo que el re-score destapa vale más que la retirada, y son dos cosas que nadie habría buscado.**
- **El hueco no fue a la siguiente idea comprable: fue a caja.** Los targets de las otras cinco
  filas quedan **idénticos** al punto decimal (pharma 14,91 · semis 10,24 · water 17,32 · robotics
  10,05). El 6º puesto del libro modelo lo hereda `biotech_drug_development` con **€1.265,87** —
  el mismo euro que tenía cloud — y la tabla lo emite **HOLD**: *«gap open but no rule condition
  met»*. Es `buy_if.rank_max: 5` contra un `rank_in_portfolio` de 6. **Antes ese 6º puesto lo
  ocupaba `copper_miners`, que está HELD**, así que la regla nunca tuvo que comprarlo y el techo
  no mordía. Ahora el 6º puesto del libro es estructuralmente **incomprable**. Esto es evidencia
  NUEVA para la decisión abierta de v12 sobre `buy_if`, cuyo propio comentario dice *«no cuesta
  nada hoy: las compras están en rank 1, 2 y 4»* — hoy cuesta **€1.265,87**, y el shortfall de
  despliegue sube **48,68 → 54,33pp**.
- **Cyber NO vuelve por sí solo, y pierde terreno.** Sale de 8/26 y entra en **8/25**: con cloud
  fuera todos los de debajo suben un puesto y cyber es el único que no. Le adelanta
  `uranium_miners` (9 → **7**), y el mecanismo es que **el crowding se re-percentila al cambiar el
  universo**: cloud era el ancla baja (2,8, el menos concentrado de los 26), así que al salir el
  crowding MEDIDO de uranium cae **58,3 → 44,1** y su composite sube 53,9 → 55,6. El de cyber se
  queda clavado en **75,0** porque no está medido — es el valor de la ETIQUETA `crowded` de
  `crowding_from_maturity`. **Cyber compite con una etiqueta contra rivales medidos, y la etiqueta
  no responde a un cambio de universo.** Es el `crowding_source` de v8 (etiqueta como fallback
  declarado) mostrando su coste por primera vez en una decisión concreta.

**Riesgo: la retirada concentra el libro y la tabla lo dice.** `struct_ai_capex_supercycle` pasa a
**92,1% de la varianza del libro** contra un presupuesto de 35% (+57,1pp), y `BOOK RISK` avisa de
que la tabla deja el libro MÁS correlacionado de lo que lo encontró (ρ 0,197 → 0,263). Cloud era
un diversificador real — v13.1 lo midió en ρ 0,08 contra el resto. Perderlo tiene ese precio y
queda impreso, no escondido.

910 tests en verde (+3 respecto a los 907 que quedaban tras retirar el sector de las tablas de
señal; la suite ya tenía el invariante que lo exige — `test_flow_data` y
`test_config_feasibility` fallaron los tres a la vez en cuanto la taxonomía cambió, que es
exactamente para lo que están).

---

## v13.3 — El ramp tiraba su propio tramo, y el ranking no tenía denominador (2026-09-01)

**Tres huecos que salieron de auditar la tabla del 2026-09-01 como analista externo, no de un
test.** Ninguno era una constante mal elegida; los tres eran cosas que el sistema hacía sin
medirlas o medía sin decirlas.

**H1 — el ramp encolaba detrás de la primera fila que no cabía, y tiraba el resto del tramo que
él mismo acababa de autorizar.** `deployment_ramp_plan` ponía `full = False` en el primer row que
no entraba y difería TODO lo que venía por detrás. La justificación escrita — «no es una mochila
que se saltaría al líder para meter dos nombres más baratos» — no aplica: **una fila que no cabe
se difiere igual**, así que seguir bajando por el ranking no desplaza a nadie; sólo deja de tirar
el remanente. Vivo: **€1.059,67 de headroom autorizado sin usar**, marcado `headroom_usable: True`,
con dos filas que SÍ cabían (semis €564, pharma €935) encoladas detrás de una que no (cloud
€1.266). Colocados €440 de €1.500 autorizados y 2 de 10 operaciones del mes. **El orden por rank
sigue siendo absoluto** — nunca se salta a un líder PARA HACER SITIO a uno peor, y las concesiones
siguen siendo a tamaño completo; lo único que cambia es que un row que no cabe deja de bloquear a
los que vienen detrás. Medido sobre el run vivo: concede además `semiconductors_design` €564,
desplegado tras el tramo **30,7% → 36,4%**.
**Y el conflicto de constantes que esto destapa se NOMBRA en vez de resolverse por decreto:**
`max_step_pp` (15pp = €1.500) es MENOR que `max_position_pct` (17,5% = €1.750), así que una línea a
tamaño completo no cabe en un tramo entero y no es «para la próxima revisión» — es **nunca**, hasta
que se mueva una constante o la petición. `tranche_eur` + `unfittable_rows` + un `**` en la acción
lo separan del `*` de «encolado». Hoy no dispara — la venta de cyber agranda el tramo a €2.064 y
`water_infrastructure` (€1.732) entra —, y ésa es exactamente la razón de medirlo **después** de
las ventas: una venta agranda el tramo de verdad. La constante NO se ha tocado: es umbral congelado
y el mando que limita la mayor apuesta de timing del sistema.
*Defecto derivado, arreglado en el mismo paso:* el aviso de shortfall citaba `allowed_after_eur`
(el TECHO que autoriza el tramo) como «ejecuta las filas concedidas». Coincidían mientras el
llenado gastaba el tramo entero o nada; con backfill el techo **sobreestima** lo que produce
ejecutar la tabla — aquí en €496. Pasa a `after_eur`.

**H2 — la tabla ponía un breakeven en % al lado de un rank, y nada convertía uno en otro.** Un
rank es una posición en un orden adimensional; `breakeven_pct` es dinero real. Sin denominador,
«rank 8 → vende» es un veredicto sin unidades — y la tabla viva vendía una línea al **2,574%** de
breakeven para comprar otra **0,32 composite_z** por encima. La aritmética es la misma sobre la
que descansa λ: `E[Δr] ≈ IC · Δz · σ_cs`, luego

    Δz_requerido = breakeven_pct / (IC · σ_cs)

**No es un threshold, y ésa es toda la gracia.** Respira con la fricción de la fila (una compra
desde caja paga ~0,20%, una rotación ~2,57%: la MISMA fórmula hace desplegar caja **~13× más
barato** que rotar, una asimetría que nadie tiene que imponer), con la dispersión medida del
régimen, y con el skill medido. Y tiene la propiedad que el proyecto ya exige en todas partes:
**IC → 0 manda el listón a infinito**, así que sin edge establecido NINGUNA rotación por rank está
justificada. Es la medicina que `skill_lambda` da al SIZING, aplicada por fin al turnover — hoy el
sizing se aparta por falta de edge y la selección rota a pleno rendimiento, y ésa era la costura.
Pieza nueva en `calibration`: `cross_sectional_sigma` (σ transversal de los retornos forward de una
ventana, en %) persistida por run como `fwd_dispersion_pct`, y `dispersion()` que devuelve la
MEDIANA sobre ventanas completas con sus cuartiles. Medido: **σ_cs = 12,26%** (4 ventanas de 63d,
p25 12,04 · p75 12,78). Con IC 0,20 la rotación cyber→water pediría **1,05z** cuando el gap real es
0,32z y el spread ENTERO del rank 1 al rank 6 del run son 0,42z.
**COLUMNA, NO PUERTA, y a propósito.** Promoverla a veto es un cambio de política sobre evidencia
que aún no existe (el scorecard no tiene ninguna ventana completa), y la disciplina v7 del propio
proyecto es que una señal nueva viaja a peso 0 hasta que tres ventanas independientes hablen. No
cancela nada. Las salidas fundamentales — exit watcher, régimen roto, driver muerto, drawdown,
racha de rank-out — son sobre la POSICIÓN, no sobre un gap marginal, y quedarían fuera del listón
aunque algún día se armara. `pays` es de **tres valores**: `None` = no medido, y colapsarlo en
`False` se leería como «medido y rechazado».

**H3 — lo que el modelo aporta de verdad no se imprimía en ningún sitio.** Con λ=0 el ranking no
dimensiona nada, así que el 100% de la contribución del modelo es QUÉ nombres quedan dentro del
corte. Tres líneas nuevas, ninguna mueve un euro:
- **`selection_margin`** — el corte del run se decidió por **+0,025 composite_z** (`copper_miners`
  dentro, `biotech_drug_development` fuera). Toda la maquinaria colgando de una moneda al aire, y
  nada lo decía.
- **`rank_size_spearman`** — **+0,03** entre rank y peso objetivo. Correcto por diseño (el
  presupuesto de riesgo dimensiona, el ranking selecciona), pero era un hecho asumido y nunca
  impreso. Y las razones de la tabla decían «rank 4, not held» al lado de un euro en el que el
  rank no participó: pasan a «rank 4 opens the gate, Xpp under the **risk-budget** target».
- **`composite_drift` por fila** — `decide_action` lee nivel (rank), régimen y exit watcher, y
  **nunca la DIRECCIÓN** en la que ha viajado el score. Una tesis puede caerse 23 puntos con el
  rank intacto y ninguna regla lo ve: `cybersecurity_commercial` **77,1 → 54,0 (−23,1)**, el peor
  del libro, mientras la tabla lo vendía por «rank 8» — el motivo correcto existía y no estaba en
  la tabla de reglas. Se publica como columna; convertir el drift en una salida es una regla nueva
  y necesita su propia evidencia.

**Y el hueco de verdad estaba en el front: el swap ledger NUNCA ha existido en la página.** El
usuario lo vio antes que yo — «lo veo igual» sobre una tabla que ya llevaba la columna `Δz req`.
Cierto: la tabla de acciones sólo puede cambiar un asterisco (el fill del ramp concede semis), y el
único sitio donde el sistema DICE que una rotación no paga es el bloque `SWAP LEDGER`, que se
calculaba en `plan()`, se renderizaba en el CLI y **no se persistía**. Un dashboard que se hornea
del lake no puede enseñar lo que el lake no guarda. Nueva tabla `rebalance_swap` (grano propio: una
rotación es un PAR, no un sector) y bloque nuevo en la página con out/in, fricción, b/e, el gap Δz
apostado, el Δz que exige el listón y el veredicto. **Se escribe también el ledger VACÍO**: «esta
tabla no implica ninguna rotación» es un hecho, y dejar en pie el emparejamiento del run anterior
contestaría una pregunta sobre hoy con las operaciones de ayer.

**En el dashboard, no sólo en el CLI.** Los tres bloques son propiedades del RUN, así que viajan
al lake a nivel de libro (`book_swap_hurdle_*`, `book_sigma_cs_*`, `book_rank_size_spearman`,
`book_selection_*`, `book_ramp_tranche_eur|headroom_eur|unfittable`) y el front los hornea desde
ahí — una página que los re-derivara estaría enseñando los números de otra tarde. Las columnas por
fila (`dz_required`, `dz_hurdle_verdict`, `composite_drift`…) ya persistían solas, porque `persist`
serializa la fila entera. En `site/app.js`: tarjeta `swap hurdle` en la tira de resumen, columna
`Δz req` en la tabla de acciones, `**` para el row que excede un tramo entero, y un panel
**SELECTION** con el margen del corte, el ρ rank↔tamaño y el drift por línea. Copy en inglés, como
todo el dashboard.

909 tests en verde (+19).

## v13.2 — El techo de posición se aplicaba sobre una base que no era la suya, y aplastaba el sizing entero a equiponderado (2026-09-01)

**Salió de una observación del usuario sobre la tabla, no de un test:** «los que pones 700 es para
llegar a 1300, todos tienen aprox el mismo dinero — nuestro cálculo no está bien hecho, los pesos
deberían cambiar según lo que hemos definido». Cierto y medible: el libro modelo del run
`run_20260901_142127` tenía sus **6 nombres dentro de una banda de 2,08pp** (15,42–17,50%), con
dos pegados al techo. Softmax, λ, el CA-tilt y el presupuesto de riesgo de v13.0 estaban todos
corriendo — y ninguno se veía.

**LA CAUSA ES ARITMÉTICA, NO DE CRITERIO, y estaba escrita en este mismo fichero desde v13.0 como
decisión abierta.** Los pesos del libro modelo son fracciones del libro **DESPLEGADO**
(`target_eur = weight_pct/100 × deployable`), y la prueba es exacta: peso igual sobre `n_target`=6
nombres = 16,67% del desplegado = **12,50% del total = `deploy_max/n_target`**, el neutral
declarado. Pero `max_position_pct: 17,5` está derivado como `tier_multiples[0] × deploy_max/
n_target`, es decir **17,5% del capital TOTAL**, y `water_fill` lo aplicaba tal cual sobre pesos en
base desplegada. El techo acababa en **1,050 × neutral** en vez de los **1,400 ×** que declara la
tabla de tiers: **0,83pp de margen sobre el peso igual en vez de 6,67pp**. Con 6 nombres y 17,5%
de techo el rango entero alcanzable es [12,5%, 17,5%] — el libro salía equiponderado **por
aritmética, no por decisión**, y cualquier paso de sizing que se añadiera encima moría ahí.

**Lo que NO se ha hecho, y es la parte que importa: no se ha tocado la constante.** 17,5 sigue
siendo 17,5% del capital total, derivado de `n_target` como siempre. Lo que se corrige es la
CONVERSIÓN de base, que es un bug, no un umbral. `book_shape()` publica ahora
`max_position_pct_deployed` = `tier_multiples[0] × 100/n_target` = **23,33%** — y nótese que
`deploy_max` se cancela: el techo en espacio desplegado no depende del ratio de despliegue vivo,
así que no se mueve con él. `portfolio.cap_frac()` hace la conversión y **un perfil declara sobre
qué base está su número** (`max_position_basis`, por defecto `deployed`): sólo `catalyx` lleva
`total`, porque es el único cuyo techo es DERIVADO. Los baselines (`equal_weight` 12,
`low_crowding` 16, `momentum` 18) tienen números elegidos a mano contra los pesos que capan;
reescalarlos en silencio cambiaría lo que mide el contrafactual, que es lo único que un libro de
control no puede hacer.

**El mismo error estaba una vez más aguas abajo.** `close_target_weights` (rebalance) re-aplica el
techo DESPUÉS del rescale, también sobre pesos en base desplegada, y leía `cfg["max_position_pct"]`
directo — habría deshecho el arreglo un paso después, en silencio. Ahora usa `cap_frac` con el
perfil de la estrategia. Los otros dos usos del número en `rebalance.py` sí son euros sobre capital
total (`cap_eur` del holding excluido por crowding en la línea 2030, `cap_headroom_eur` en la 2119)
y se quedan como estaban: ahí la base correcta ya era el total.

**Medido sobre el run vivo** (`run_20260901_143420`, misma selección, mismos scores):

| | antes (base mala) | después |
|---|---|---|
| dispersión de pesos (máx−mín) | 2,08pp | **9,99pp** |
| vol anualizada del libro modelo | 16,16% | **14,94%** |
| paso de riesgo v13.0 (`w_pre_erc` → `w`) | −0,38pp de vol | **−0,68pp** |
| pesos | 15,4–17,5 (todos) | water 23,1 · pharma 19,9 · cloud 16,9 · semis 13,7 · robotics 13,4 · copper 13,1 |

El orden es el que dicta el riesgo, no el score: `robotics` es el nombre con más composite (62,7)
y baja de 21,2% (camino pre-v13) a **13,4%** porque es el más correlacionado del libro (ρ̄ 0,27);
`cloud` sube de 12,7% a 16,9% con ρ̄ 0,08. **Eso es exactamente lo que v13.0 construyó y lo que el
techo mal-basado impedía ver** — el «casi inerte» que su propia entrada reportó era este bug, no el
mecanismo.

La tabla de rebalanceo resultante: `TRIM cybersecurity −€565` y `BUY robotics €1.005` hoy, con
`ADD pharma €935`, `ADD semis €564`, `BUY water €1.732` y `BUY cloud €1.266` encolados por el ramp
(15pp por review). 890 tests en verde (+7), en `tests/unit/test_position_cap_basis.py`, incluida
una regresión en la dirección del bug (sin el flag de base, el libro vuelve a salir dentro de 3pp).

---

## v13.1 — El crowding se cobraba dos veces, y las dos víctimas eran los diversificadores (2026-09-01)

**Salió de seguir la pregunta de v13 hasta el final: si el sizing ya lee la correlación, ¿por qué
sigue el libro con el 83% detrás de un solo driver?** La respuesta no estaba en la regla de
selección. Probada la versión principista — **maximizar el score ponderado por riesgo**, sin
constante nueva ni parámetro de diversificación — elige **exactamente los mismos 6 nombres**: la
ventaja en score de los líderes supera lo que compra diversificar. Resultado negativo, y es el
que reorienta el diagnóstico.

**El pool tenía 9 candidatos para 6 plazas, y los dos que faltaban eran los diversificadores.**
`pharma_large_cap` (rank 4, ρ̄ **0,168**, ρ 0,00 contra semis, 0,12 contra cloud) y
`biotech_drug_development` (rank 7, ρ̄ 0,239) quedaban fuera por `max_crowding: 85` con 94,4 los
dos — **y eran los dos ÚNICOS sectores del universo por encima de 90**. La barra no era un filtro:
era una lista negra de dos nombres, y los dos nombres eran la cura de la concentración que tiene
el resto del libro.

**Es el mismo doble cargo que v12.1 quitó del lado de la VENTA, en el lado de la ENTRADA.** Aquella
entrada ya lo escribió: «el compuesto ya cobra crowding a 0,135, y pharma le ganó a esa penalización
hasta el **4º de 26** antes de que un veto binario lo matara». Arregló el exit y dejó la entrada sin
revisar. `max_crowding: 85 → 100`: el crowding conserva su peso 0,135 en el compuesto, donde es una
penalización que el nombre tiene que GANAR; lo que deja de hacer es además borrarlo.

**Medido, y sale sin trade-off que negociar:**

```
                       vol libro   composite medio   driver más cargado
con veto (85)             16,8%          59,0              83%
sin veto (100)            14,1%          59,8              74%
```

Menos riesgo **y** más score. La misma regla mete `pharma` como la MAYOR posición y saca
`cybersecurity` — que mide ρ **0,65** contra cloud y es el duplicado real del libro. Que no haya
trade-off es en sí la evidencia de que la barra estaba mal preciando algo, no expresando una
preferencia.

**Lo que se cede, y es un argumento real:** el crowding precia lo que paga el SIGUIENTE comprador,
así que meter dinero NUEVO en un tema masificado no es lo mismo que mantenerlo, y este libro ya
puede hacerlo. La defensa es la penalización del compuesto más todas las salidas ordinarias
(rank-out, conviction gate, reglas de venta, escalera de beneficios). Los libros baseline
**conservan sus barras** — `equal_weight` a 85 y `low_crowding` a 55 son el contrafactual
permanente: si esto fue un error, el lake lo dirá contra dos libros que no lo cometieron.

**EL DEFECTO QUE DESTAPÓ EL EDIT, Y QUE VALE MÁS QUE EL EDIT.** Al levantar la barra, la tabla viva
imprimió **`TRIM pharma_large_cap −€556`** — un cierre de la posición que este cambio existe
justamente para admitir. Causa: `_crowding_excluded` **reconstruye** la selección con la config de
HOY sobre el run GRABADO, y su docstring afirmaba que eso es «exacto, no una aproximación de con
qué se construyó el libro persistido». Cierto **sólo mientras la config no se mueve**. Cuando se
mueve, las dos discrepan en la única dirección que cuesta dinero: el libro persistido sigue
EXCLUYENDO el nombre (se construyó con la barra vieja) mientras la reconstrucción ya lo INCLUYE,
así que `crowding_excluded` sale vacío, la supresión de v12.1 no puede dispararse, y la línea viva
se lee como máximamente sobreponderada contra un target del 0%. **Un edit de config, él solo,
fabricó una venta.**

**Un libro modelo construido con otras reglas no es un target de cero: es «no lo sabemos»** — y
este fichero ya tiene el veredicto para ese estado. Un nombre EN CARTERA y ausente de un libro
modelo stale degrada a **RE-SCORE**, que no mueve dinero, exactamente como un SELL por rank degrada
cuando el sector no tiene un rank sobre el que vender. La detección reutiliza el `config_version`
que cada holding ya persiste: si ninguno de los grabados coincide con el actual, el libro es stale.
Se resuelve solo en el siguiente score run. Es estrecho por construcción — sólo marca nombres
AUSENTES del libro modelo, así que un miembro del libro 5,6pp sobreponderado se sigue trimeando de
verdad, con test que lo fija.

**Verificado end-to-end:** reconstruido el libro modelo con la config nueva, `pharma` entra al
17,5% y la tabla pasa de `TRIM −€556` a **`ADD +€756`**, mientras `cybersecurity` sale con
`TRIM −€565`. 883 tests en verde (+2).

---

## v13.0 — El sizing leía una ETIQUETA de cluster; la cinta dice otra cosa, y ahora la lee (2026-09-01)

**Salió de: «Cuando creamos las recomendaciones no estamos teniendo en cuenta la concentración
[...] ¿cómo diversificamos sin alejarnos de nuestras tesis?», y al proponer un llenado por
cluster declarado, del rechazo correcto: «pero robotics no es ai capex, no es el que más se
alinea. Hay que tener un score de correlación y asignar pesos basado en ello».**

**La premisa del rechazo se verifica y es cierta.** Toda regla de tamaño anterior leía una
etiqueta *declarada* (la atribución a un driver estructural). Medido sobre el top-12 vivo, 56
semanas, retornos SEMANALES en EUR, Ledoit–Wolf:

| par | ρ medido | etiqueta |
|---|---|---|
| robotics–cloud | **0,28** | MISMO cluster declarado (`ai_capex`) — dimensionados como una apuesta |
| cloud–cyber | **0,65** | clusters DISTINTOS — el duplicado real del libro |
| semis–data_centers | 0,63 | mismo cluster (aquí la etiqueta acierta) |
| pharma–biotech | 0,59 · copper–uranium 0,52 | |
| water_infrastructure | ρ̄ **0,20**, ρ −0,00 vs cloud | etiquetado `ai_capex`, es **el diversificador del libro** |

La etiqueta era un proxy de la correlación, y la correlación **se mide**. Así que el paso de
sizing la mide. El cap nocional por driver **se queda**: exposición declarada («cuánto dinero se
mueve si este driver se rompe») y co-movimiento medido son preguntas distintas, y v10 P6 ya
decidió que corren en paralelo.

**El mecanismo.** El score deja de ser un peso y pasa a ser una **cuota de VARIANZA**; la Σ
medida de los vehículos seleccionados la convierte en euros — `covariance.risk_budget_weights`,
que resuelve `wᵢ·(Σw)ᵢ ∝ bᵢ` por descenso coordinado cíclico sobre la forma convexa de Spinu
(2013). Dos nombres muy correlacionados se reparten solos el presupuesto que su ρ dice que son
uno de: **no hace falta ninguna etiqueta**. `vol_tilt` es el caso particular exacto — con Σ
diagonal la solución es `w ∝ √b/σ` —, así que **lo reemplaza en vez de apilarse** (la diagonal de
Σ ya cobra la vol; correr los dos la cobraría dos veces).

**Lo que NO toca: la selección.** Los nombres son los que eligió el composite, top-N por score,
conviction gate intacto. Meter ρ DENTRO del composite exigiría un IC que lo justificara; esto es
un término de RIESGO y dimensiona sin rankear nunca, el mismo estatuto que `vol_tilt` ha tenido
siempre.

**El tilt llega comprimido en raíz** — 4:1 de presupuesto aterriza como 2:1 en euros, porque un
presupuesto es una afirmación sobre varianza y un peso lo es sobre dinero. Con el rank IC del
composite todavía en ruido (−0,027, 1 ventana), un tilt de convicción que llega comprimido es la
dirección que `skill_lambda` ya defiende; cuando el IC se gane λ, los presupuestos se abren y los
euros los siguen.

**El defecto del solver que se cazó implementándolo:** el punto fijo ingenuo `w ← b/(Σw)`
normalizado en cada paso tiene el fixed point CORRECTO pero **oscila** — verificado, falla en
converger sobre una Σ diagonal en 500 pasos y devuelve su propia semilla. Un paso de sizing que
devuelve su entrada en silencio cuando no converge es el peor fallo disponible, así que se usa
CCD (Griveau-Billion et al. 2013), cerrado en forma en cada coordenada. Una no-convergencia
devuelve los scores de entrada **intactos**: a medio camino entre dos reglas de tamaño hay un
libro que ninguna de las dos suscribe.

**Imputación, no descarte.** Un vehículo con menos de `rb_min_weeks` semanas conserva su fila:
su propia σ si tiene historia usable (≥8 semanas — insuficiente para una correlación, suficiente
para una varianza), y el `r_bar` del panel contra todos. Descartarlo encogería la sección
transversal que se está dimensionando — la forma de defecto de `_load_shares_series` (v10.2) —, y
`uniqueness = 1` le daría al nombre NO medido el mayor peso del libro. Σ imposible de construir →
`sizing_mode: tiers_fallback` **publicado**, nunca en silencio. Mezclar un bloque medido con un
borde de correlación constante puede dejar C ligeramente indefinida: se clipan los autovalores y
se **reporta** (`psd_clipped`) — una matriz reparada en silencio es una matriz mal en silencio.

**LO QUE MIDE HOY, Y ES LA PARTE QUE IMPORTA MÁS QUE EL MECANISMO.** Sobre el libro vivo de 6
nombres el paso mueve la vol del libro **18,65% → 18,61%**. Es decir: **prácticamente inerte**, y
la causa no es el mecanismo sino el techo — que aplasta por igual al softmax, a λ, al CA-tilt y
al vol_tilt. Sin el cap, la misma Σ y los mismos presupuestos dan **14,61%** (−4,0pp), con
`water_infrastructure` pedido al 41,1% por ser el diversificador.

**Y la razón por la que el techo aplasta tanto es un desajuste de BASE, medido aquí y NO
corregido** (es edit de umbral congelado y decisión del usuario):

```
neutral      = deploy_max / n_target      = 0,75/6 = 12,50%  de capital TOTAL
tier 1       = 1,4 x neutral              =         17,50%  de capital TOTAL  <- max_position_pct
peso igual sobre 6 nombres = 16,67% del DESPLEGADO = 12,50% del total  <- == neutral, EXACTO
```

Esa última igualdad es la prueba: los pesos del libro modelo son fracciones del libro
**DESPLEGADO** (`rebalance`: `target_eur = weight_pct/100 × deployable`). Pero `water_fill`
aplica `max_position_pct: 17,5` contra esa MISMA base, no contra la base de capital total sobre
la que está derivado. Resultado: el techo se sitúa en **1,050 × neutral** cuando la tabla de
tiers dice **1,400 ×** — deja **0,83pp** de margen sobre el peso igual en vez de 6,67pp, y el
libro sale equiponderado **por aritmética, no por decisión**. Se ve en el propio output del
rebalance vivo: todos los `tgt%` son **13,1% = 17,5 × 0,75**. `rebalance.py:2081` sí usa la base
correcta (`total_capital × max_position_pct`), así que el proyecto usa las dos bases a la vez.
Contrafactual con la base derivada (23,33% sobre el libro modelo): tiers 17,99% de vol, risk
budget **17,59%**, dispersión de pesos 4,96pp → 11,71pp.

**No se toca el cap.** Es una constante congelada de la que cuelga la escalera entera, cambiarla
mueve los tamaños del libro vivo, y decidir por decreto es justo lo que este fichero prohíbe para
el cap correlado y para el halflife. Se publica la medición y decide el usuario. Nótese que
incluso con la base corregida el efecto es modesto (−0,40pp de vol): ERC quiere concentrar en el
diversificador y un techo de concentración lo prohíbe — **el techo haciendo su trabajo**, tal y
como el comentario de `vol_tilt_alpha` ya anticipaba («si esa dispersión es el objetivo, el
instrumento es un presupuesto de riesgo, no α»). La palanca de diversificación por TESIS sigue
estando en la selección, no en el sizing; son complementarias.

**Ledger.** `w_pre_erc_pct` persiste el libro pre-v13 al lado del nuevo, para que
`calibration` pueda preciar el paso por ventana completa igual que hace con el CA-tilt — un paso
de sizing que no se puede contrastar contra el libro al que sustituye no puede demostrar nunca
que se ha ganado su sitio. Columnas nuevas por fila: `sizing_mode`, `w_pre_erc_pct`,
`risk_contribution_pct` (Euler, la magnitud que el presupuesto dice fijar), `rho_bar_book` (el
número medido que la etiqueta representaba), `rho_imputed`. A nivel libro: `sizing_mode`,
`risk_budget` (cov_weeks_used, n_measured, n_rho_imputed, r_bar, shrinkage, psd_clipped,
book_vol_pct, pre_erc_vol_pct). Los runs anteriores no llevan `w_pre_erc_pct` y se leen como
`sizing_mode: tiers`. Los libros `momentum` y `equal` quedan limpios: son baselines
contrafactuales y dimensionarlos con Σ les haría medir dos cambios a la vez.

**Config** (`scoring_weights.yaml`): `sizing_mode: "risk_budget"` · `rb_cov_weeks: 56` (la misma
ventana que ya lee `cluster_risk_pass`) · `rb_min_weeks: 40`. 881 tests en verde (+16), incluido
el que fija la propiedad de verdad: un par a ρ=0,99 con presupuestos IGUALES se reparte, mientras
la vol-parity sobre las mismas tres vol idénticas les da 1/3 a cada uno, ciega a ρ.

---

## v12.1 — El crowding mide el precio que pagaría el SIGUIENTE comprador, y estaba vendiendo a los que ya habían pagado (2026-09-01)

**Salió de una observación del usuario sobre una posición viva: «farma descorrelaciona y ahora
mismo es lo que más ha subido de mi cartera. Ha sido una buena apuesta. Pero ahora no lo estamos
identificando como tal. En nuestra pipeline falta algo».** La premisa era medio falsa y el matiz
es el hallazgo: pharma **no** es lo que más ha subido (cyber +12,9% contra +11,3%). Es la mejor
por retorno por unidad de vol — 11,3/19 = **0,59** contra 0,36 (cyber), 0,12 (copper), −0,15
(semis) — y con contribución de riesgo **negativa**. O sea, el instinto medía una dimensión que
la cadena no calcula en ningún punto.

**El diagnóstico, y por qué no es el umbral.** Los filtros de construcción se aplican idénticos a
un nombre que ya tienes y a uno que vas a comprar ([`portfolio.py`] filtro `max_crowding`). No hay
histéresis, y un filtro escrito para decidir QUÉ COMPRAR estaba decidiendo QUÉ VENDER: excluido
del libro modelo → target 0% → el TRIM por sobrepeso lee la línea entera como exceso y la vende.
La primera idea fue histéresis (barra de entrada 85, de salida 95) y **se descartó con los datos
delante**: en el universo del run sólo hay dos sectores por encima de 90 (pharma y biotech, ambos
94,4), así que una barra en 95 no afina el criterio, **mata la regla** — nada podría salir nunca
por crowding. Elegir 95 habría sido escoger el número que produce la respuesta deseada, que es
exactamente lo que este fichero prohíbe para el `correlated_catalyst_cap`.

**El defecto es la dirección, no el nivel. El crowding mide lo que pagaría el comprador marginal
por entrar al tema; quien ya está dentro pagó el precio viejo, y la masa es la que le está
pagando.** «No compres un tema concurrido» es sólido. «Vende porque el tema se concurrió» está del
revés. Y compone con un segundo hecho mecánico: **el crowding SUBE cuando aciertas** — un tema se
vuelve concurrido *por* funcionar. La serie de pharma lo enseña entera: composite 28,5 → 80,5
mientras crowding iba 55 → 75 → 94,4 (el salto 75 → 93,3 es además el swap v8 N4 de etiqueta
narrativa a comomentum medido, en `run_20260831_184616`). La regla vendía ganadores por haber
ganado. Encima **contaba dos veces**: crowding ya pesa 0,135 invertido en el compuesto — pharma
pagó esa penalización y aun así rankea **4º de 26** — y después moría a un veto binario sobre la
misma métrica.

**Dónde NO va el arreglo, y esto decidió la implementación.** No en `portfolio.py`: el libro
modelo es una estrategia de referencia y debe ser reproducible desde un run solo. En el momento en
que consulta lo que TIENES, su NAV deja de ser comparable con el del libro real y la medición de
alfa de ejecución muere. Así que el libro modelo **sigue excluyendo** a pharma, y la decisión
sobre un nombre TENIDO se toma donde el target del modelo se encuentra con el libro real —
`rebalance`, que ya tiene el precedente exacto: `RE-SCORE`, en lo que degrada un SELL por rank
cuando el rank no es motivo suficiente para vender.

**Lo que se construyó:**
- `portfolio.build_model_holdings` publica `crowding_excluded`. **Es un contrafactual, no una
  comparación con el umbral:** un nombre se lista sólo si levantar la barra lo habría
  seleccionado DE VERDAD. Por eso hoy aparece pharma (rank 4) y **no** biotech, que está en el
  mismo 94,4 de crowding pero se quedaría fuera del top-6 igualmente. Un sector que tampoco
  llegaba está sobrepesado de verdad y se recorta de verdad; decir lo contrario le daría a
  `rebalance` una excusa para cancelar una salida sana. La cadena de filtros se factorizó en
  `_filter_and_select(frame, apply_crowding)` para que las dos pasadas difieran **sólo** en eso.
- `rebalance.decide_action` suprime **únicamente** el TRIM por sobrepeso cuando
  `crowding_excluded_only`, y devuelve HOLD nombrando la causa con sus números. No toca nada más:
  un driver invalidado, un régimen `breaking`, el exit watcher y el rank-out siguen vendiendo, y
  la escalera de beneficio — que dispara por GANANCIA, no por el gap — sigue alcanzando a la
  posición. Hay test por cada una de esas cuatro puertas.
- `rebalance._crowding_excluded` **falla hacia la venta a propósito**: cualquier excepción
  devuelve `{}`, ningún row recibe el flag y el TRIM dispara como antes de este cambio. Un
  mecanismo cuyo trabajo es CANCELAR una venta tiene que fallar vendiendo — una excepción
  silenciosa que se quedara la posición sería la misma clase de bug que el fallback a un ticker
  no comprable.

**Segunda pata: una rotación decía lo que cuesta OPERARLA, nunca lo que cuesta el LIBRO que deja.**
Todo coste de la tabla es coste del TRADE — CGT, spread, el breakeven que la rotación debe superar.
Ninguno es coste de la cartera resultante. Así que la tabla podía vender la única posición que
diversifica y comprar un quinto nombre dentro del cluster que ya carga la mayor parte de la
varianza, y **ningún número de la página se movía**. Medido sobre este run, contrafactual de los
dos libros de 6 nombres, semanales y 56 semanas (el muestreo que el propio proyecto declara
correcto, no el diario sesgado por Epps): vender pharma y mantener cyber deja ρ **0,2215** y vol
**19%**; mantener pharma y dejar salir a cyber por rank deja ρ **0,1883** y vol **16%**. La regla
estaba rotando hacia el libro **más** correlado y **más** volátil, y el ticket de esa rotación
imprimía €12,92 de fricción. Nuevo `book_risk_delta` + bloque `BOOK RISK`: vol y ρ medio del libro
antes y después de la tabla, **sólo con las filas que se ejecutarían hoy** (una fila que el ramp o
el presupuesto de operaciones encoló no es parte del libro de hoy, y contarla preciaría una cartera
que nadie va a tener). Es **columna, no puerta** — misma doctrina que `data`, `ca_imputed` y el
cluster no medido: precia y no cancela nada; quien actúa sobre riesgo sigue siendo
`cluster_risk_pass`, y sólo del lado de la compra. **Un riesgo no medible devuelve `None`, nunca un
cero tranquilizador**, porque un libro cuya correlación no se puede calcular no es un libro cuya
correlación no se mueve.

**Efecto vivo:** pharma pasa de `TRIM −€556` a `HOLD` con la causa escrita en la fila («no model
target: excluded by the ENTRY crowding filter (94.4 > 85) — held at rank 4, not sold for being
crowded»). La tabla de hoy dispara además el aviso nuevo: BUY robotics lleva el libro de ρ 0,120 a
0,200 y de vol 20,0% a 21,0%.

**Lo que NO se hizo, con causa.** Meter covarianza en la SELECCIÓN (objetivo por contribución
marginal) se descartó: con el IC del compuesto en −0,143 sobre ~1 ventana independiente, montar un
optimizador consciente de covarianza encima de un ranking que todavía no ordena nada es ajustar
ruido con más maquinaria — el error que `skill_lambda` existe para no cometer. Y el término
ajustado por riesgo en `movement_outcome` (hoy registra `return_pct` y `right_thesis`, así que
pharma se cerrará como «+11,3%, acertada» sin que quede escrito lo único que la hizo la mejor
línea del libro) queda **abierto**: es real, no es urgente, y necesita el contrafactual que ya
existe en `w_pre_ca_pct`. 864 tests en verde (+11).

## v12.0 — La capa de eventos era un trinquete de un solo sentido, y la escalera de posición descansaba en un número inalcanzable (2026-09-01)

**Salió de una petición de auditoría del usuario: «hemos tocado muchas cosas sobre cómo calculamos
el score de catalyx, cómo hacemos las particiones de cuánto dinero, de qué quitamos, dónde
ponemos… ¿puedes revisar las cadenas, encontrar bugs y proponer mejoras?».** Auditoría de las dos
cadenas de punta a punta, cada hipótesis falsada contra los datos vivos con contrafactuales
`persist=False` antes de tocar una línea. Lo que sigue es lo que sobrevivió a esa comprobación,
en el orden en que hay que arreglarlo — porque tiene dependencias.

**B1 — el canal `confirms` era aritméticamente inerte: 32 de 32 instancias aportaban EXACTAMENTE
0.** `_apply_confirms` calculaba `max(S, min(S + boost, 0.45·S + 0.55·D))`. Ese techo cae **por
debajo de su propio suelo** siempre que el evento sea más débil que el estructural que confirma:
`0.45S + 0.55D > S ⟺ D > S`. Los estructurales vivos corren 77–95 contra fuerzas decaídas de
14–45, así que el `min` quedaba tragado por el `max` y el boost se anulaba, **cada vez, desde
v1.5** — mientras `_apply_contradicts` mordía incondicionalmente. La capa de eventos era un
trinquete que solo podía girar hacia ABAJO, dentro de un módulo cuyo propio docstring de
agregación dice que más catalizadores confirmando = señal más fuerte. El techo defendía una
comparación que no necesita defensa: un `independent` enlazado **sustituye** al estructural en el
pool, no compite con él por una plaza, y un evento que corrobora una tesis ya fuerte sí es
evidencia más fuerte que un evento ajeno del mismo tamaño. Quitarlo restaura la simetría con
`_apply_contradicts` (suelo 0, techo el caso sin evento). **Medido: 15 de 25 sectores se mueven,
todos hacia arriba, de +1,2 a +4,5** — `eu_defense` +4,5, `lithium`/`solar` +4,0, `cybersecurity`
+2,6, `uranium`/`water` +2,5, `cloud`/`semis` +2,2, `copper`/`grid` +1,9, `ai_infra` +1,7. **La
selección NO cambia**: los mismos seis nombres, y los tres sectores bajo la puerta de convicción
(CA < 40) siguen siendo los mismos tres.

**B2 — un evento `independent` enlazado destruía el estructural según se moría.** `_apply_independent`
era la mezcla desnuda `0,45·S + 0,55·D`, sin suelo, así que su límite cuando D→0 era `0,45·S`:
`consumer_india_em` leía 64,5 → 61,81 sobre un evento decaído a 59,6, camino de 29,0. Un evento
apagándose se llevaba por delante un tercio de una intensidad estructural que nunca contradijo. El
límite correcto cuando D→0 es el caso **sin evento**, `S`; con el suelo la función es continua con
«no hay evento». `independent` significa «golpea al sector, no dice nada sobre esta tesis»: puede
AÑADIR un portador, nunca restar uno. **Solo `contradicts` está autorizado a bajar un estructural**,
y hay test que lo fija para las tres relaciones a la vez.

**B2b — un `confirms` podía silenciar un `contradicts` vivo.** La regla era `max(eventos,
key=|impacto|)`: sobrevivía **un** evento por estructural, así que un confirm de magnitud 4,5
borraba del score un contradict de magnitud 3,0 — en un registro que tiene 31 `confirms` contra 2
`contradicts`, bajo una config que precia deliberadamente la desconfirmación más alto (contradict
15 vs confirm 10). La contradicción sobrevivía solo en `regime_state`, que anota y no puntúa.
Ahora se aplican el mayor empuje y el mayor arrastre, **los dos** (`_net_event_impact`). Con
eventos en una sola dirección es idéntica a la regla vieja — hoy **ningún** estructural lleva las
dos, así que esto no mueve un número: es un defecto latente cerrado, no un cambio de tabla. Sigue
siendo un evento POR DIRECCIÓN, a propósito: varios titulares confirmando el mismo desarrollo
están correlacionados, y apilarlos es justo el doble conteo que `_REINFORCE_FACTOR` amortigua un
nivel más arriba.

**B12 — y nada de esto habría aparecido en el lake.** `scoring_version` era el hash del YAML de
pesos **solo**, así que un cambio de FÓRMULA dejaba la cadena de versión idéntica y el lake
afirmando que las filas de antes y después son comparables. No es hipotético: B1/B2 mueven el CA
de 15 de 25 sectores sin tocar un peso. `git_commit` va al lado y habría permitido reconstruir la
verdad, pero un campo de versión que dice en silencio «mismo scoring» es peor que no tenerlo —
todo consumidor que segmenta por él (ventanas de `calibration`, el scorecard de reglas,
`score_ic`) mezclaría dos funciones de scoring distintas. Ahora el hash cubre el YAML **y** la capa
de fórmulas (`catalyx/scorer/*.py`): `6ef216707e66` → `77064452e8e5`.

**B4 — la decisión pendiente del halflife estaba preciada en la unidad equivocada, y ese era el
motivo del orden.** `catalyst_audit` imprimía la discrepancia en FUERZA DECAÍDA, que no es lo que
lee nada aguas abajo. CLAUDE.md llevaba desde v11 citando «el evento confirmador del cobre pasa de
aportar 38,9 a aportar 4,0» como la razón de que la decisión fuera cara — pero **el CA del cobre se
mueve 0,1 puntos**, porque ahí el noisy-OR está saturado y el evento no es el portador. Con el
canal `confirms` inerte (B1) la adopción de la tabla tocaba **2 de 26** sectores; con el canal
vivo toca **8**, y el que de verdad se mueve es `agriculture_soft_commodities` (36,1 → **3,8**),
que la columna de fuerza nunca nombró, más `lithium_miners` y `solar_energy` (−3,6 cada uno). El
audit ahora publica la tabla POR SECTOR junto a la de fuerza. **La decisión sigue siendo del
usuario y `halflife_source` sigue en `stored`**: adoptar la tabla por defecto contestaría por
decreto — lo que este fichero prohíbe para el cap correlacionado — la pregunta de si se
equivocaron los seis autores o la constante. Lo que cambia es que ahora está preciada donde
aterriza. Nota: `agriculture` ya está por debajo de la puerta de convicción con las dos lecturas,
así que la adopción tampoco cambiaría la selección de hoy.

**B3 — `deploy_max` era 0,85 y el contador no puede llegar ahí; toda la escalera de posición
colgaba de ese número.** El término del step se leía de `intact_rank_max: 8`, pero `n_intact` se
cuenta sobre el **LIBRO MODELO**, que tiene como mucho `n_target` = 6 nombres: `base + step·(8−5)`
describe un estado imposible, y el run vivo ya imprimía `base 0.70 + 0.05×(6−5) → 75%`, clavado en
su techo real. `intact_rank_max: 8` es un resto de cuando `max_positions` era 10. El número importa
mucho más allá del objetivo de despliegue, porque `book_shape` lee de él la escalera entera — y
`max_position_pct` es **la constante que este proyecto insiste en que es DERIVADA**. Corregido:
`deploy_max` 0,85 → **0,75**, peso neutral 14,2% → **12,5%**, tiers 20/14/7 → **17,5/12,5/6,2**,
`max_position_pct` 20 → **17,5**. El presupuesto de pérdida por línea que v6 I5 dejó escrito baja
en consecuencia: 6,0/4,2/2,1 → **5,25/3,75/1,86%** del libro. **Efecto en el libro modelo vivo:
los mismos seis nombres; cinco topan ahora en 17,5% y `cybersecurity` sube a 12,5%** — el libro
queda más plano porque un techo más bajo lo alcanzan más nombres. Los otros seis tests de
factibilidad pasaban con el bug puesto porque **solo comparaban valores derivados entre sí**; el
test nuevo asserta la identidad contra `deploy_ratio` mismo, que es lo que lo habría cazado.

**B4b — `buy_if.rank_max: 8` era config muerta.** `decide_action` recibe `rank_in_portfolio`, que
va de 1 a `max_positions` = 6, así que un techo de 8 dejaba pasar a **todo** miembro del libro
modelo y no filtraba nada; se escribió para un libro de 10 nombres y nadie lo releyó cuando v6 L1
lo cortó a 6. CLAUDE.md aún lo citaba como restricción viva («`eu_retail_banking`, a un trimestre
del techo rank-8 de `buy_if`»). Puesto a **5**, no a 6: el comentario de la propia regla dice «a
new line needs a stronger case than an add» y el 8 lo **contradecía** — buy ya es más estricto que
add en gap (4,0 vs 3,0), en régimen (excluye además `contested`) y en madurez (excluye
`exhausted`), y era más LAXO en rank. `= add_if.rank_max` es el mínimo que quita la contradicción,
y **no mueve un euro hoy**: las compras de este run están en `rank_in_portfolio` 1, 2 y 4 (tabla
verificada idéntica antes y después). Bajar de `add_if` haría buy estrictamente más fuerte en las
cuatro dimensiones y es una decisión de umbral congelado aparte; `test_config_feasibility` ahora
tumba cualquier techo que no pueda vincular. `sell_if_any.rank_out_of_top: 10` **no** tenía este defecto (se compara contra el rank de
UNIVERSO, 26 sectores) pero su comentario afirmaba una coincidencia con `max_positions` que
desapareció en v6 L1; corregido en el sitio.

**B5 — el canal de eventos DIRECTOS era ciego al signo y solo podía SUBIR el CA.**
`relation_to_structural` contesta otra pregunta — cómo se relaciona el evento con una TESIS
estructural — y un evento directo no tiene padre estructural, así que el scorer no tenía nada que
leer y asumía que todos eran portadores positivos (`contributes = relation in ("independent",
"confirms")`); el noisy-OR es monótono creciente. Vivo: `cat_20260617_fed_hawkish_pivot` (Fed
hawkish, fuerza 80, archivado `independent`) entraba como **+14,3 de apoyo** a siete sectores,
entre ellos oro, cobre y banca minorista europea. Y un `contradicts` sin padre estructural se
**descartaba** — la única dirección de la que el registro está famélico. Nuevo campo opcional
`sector_impact` (`catalyst_event` schema 1.3 → **1.4**): `{sector_id: supportive | adverse}`. Un
`adverse` se aplica al AGREGADO con la misma penalización `_apply_contradicts` que ya recibe un
estructural contradicho (no hay portador que amortiguar); un `contradicts` directo implica
`adverse`. **La ausencia se REPORTA, nunca se adivina**: sin el campo se conserva el
comportamiento previo — este añadido no mueve ningún score vivo — y
`direct_events_direction_unstated` cuenta cuánto CA descansa sobre una suposición que nadie hizo:
hoy **12 eventos directos en 11 sectores**, entre ellos los CA de 14,3 de `eu_retail_banking` y
`oil_majors_integrated`, que se sostienen **enteros** sobre ese único evento sin dirección
declarada. Convertir «nadie lo dijo» en «adverso» sería el mismo error en la otra dirección.
**Y el campo tiene productor**: `/catalyx-scan` lo pide por sector en C3/C4, lo explica con el
caso del Fed hawkish delante, y en C2b hace backfill de los eventos que un hallazgo TOQUE este
ciclo — nunca un barrido sobre el registro entero. Un campo que nadie tiene orden de rellenar es
un mecanismo muerto, y los eventos que nadie toca mueren por decay, que es la misma eliminación
que v11 eligió para los cardinales desnudos de `strength_score`.

**B13 — 11 de los 19 documentos de evento no validaban contra su propio esquema, y nada lo
comprobaba.** `catalyst_event.json` declara `additionalProperties: false` y el pipeline llevaba
escribiendo cinco campos que el esquema nunca declaró: `affected_sectors` — **que CLAUDE.md cita
como la respuesta a «un catalizador = un driver»** —, `review_log`, `status_last_reviewed`,
`lifecycle_log`, `invalidation_reason`. El CHANGELOG de v11 registra «los 19 documentos siguen
validando»; validaban 8. El protocolo de cambio de esquema de CLAUDE.md manda «comprobar cada JSON
existente en `data/`» y no había nada que lo hiciera. Los cinco campos quedan declarados (schema
1.4) y **`tests/unit/test_schema_documents_validate.py` valida ahora los 53 documentos Tier-1** —
eventos, estudios, movimientos y propuestas de taxonomía — contra su esquema. El test cazó de
inmediato una segunda deriva: `taxonomy_gap_proposal` llevaba desde 2026-06-04 un valor
(`not_investable_no_buyable_vehicle`) fuera de su propio enum, cuya descripción además seguía
diciendo «confirmed UCITS/accessible» — el test que v10.1 demostró equivocado (lo que hace
comprable a un vehículo es el KID de PRIIPs, y un fondo de un solo metal no puede ser UCITS).
Esquema 1.0 → 1.1 con la doctrina de VENUE escrita.

**B7 — el presupuesto de riesgo solo miraba los clusters que la tabla TOCA.** `cap_check`
construía sus filas iterando `added`, es decir, los drivers que el review está negociando, nunca
los que el libro está CARGANDO. Un cluster ya por encima de su presupuesto de varianza que la
tabla de hoy no toca no se medía, no se marcaba y no recibía candidato a TRIM — que es la mitad de
lo que v10 P7 existe para hacer. Con la tabla vacía, `cap_check` devolvía **cero filas** sobre un
libro vivo. Ahora enumera todo driver presente en el libro post-trade: **4 clusters, 2 de ellos
por encima del presupuesto del 35%** (`struct_ai_capex_supercycle` 82,4%,
`struct_energy_transition_grid` 65,1%), ninguno tocado por la tabla. `sectors` nombra el bucket
entero; `sectors_traded` y `touched_by_table` conservan la distinción.

**B8 — la descomposición de riesgo corría sobre BASE DE COSTE.** `post_book` mezclaba dos unidades
por construcción: las líneas tenidas llevaban `invested_eur`, que `positions()` calcula como coste
medio, y las patas propuestas llevaban `trade_eur`, que es mercado. Esa mezcla es **correcta** para
el cap nocional — CLAUDE.md lo fija a `exposure_eur` y fija la deducción del SELL a la base de
coste, para que un ganador vendido por encima de coste no retire más exposición de la que llevaba —
y es **incorrecta** para una cuota de varianza: una línea +40% carga un 40% más de riesgo del que
dice su coste, y meter coste en la covarianza infravalora justo a los ganadores que un límite de
concentración existe para cazar. Los dos libros se construyen ahora por separado en vez de doblar
uno para servir a ambos (`_risk_book`); los precios salen de la caché con `allow_fetch=False`
—`covariance` va a leer la misma ventana— y un ticker sin cierre cacheado **conserva su base de
coste** en vez de caerse del libro, que encogería en silencio el denominador contra el que se
calcula cada cuota.

**B9 — el tally del CA-tilt podía cobrarse un término de beta.** `edge_pp = Σ dwᵢ·rᵢ` solo
significa «lo que compró el tilt» cuando `Σdw = 0`. `ca_tilt` preserva el total por construcción,
pero `water_fill`, el gross-down por contested y el deadband corren DESPUÉS y no tienen que
devolver exactamente lo que quitaron. La identidad es exacta:
`Σ dwᵢ·rᵢ = (Σdw)·r̄ + Σ dwᵢ·(rᵢ − r̄)`. Se publican las dos mitades: `edge_pp` conserva su
significado (lo que el libro se llevó — el número no se redefine en silencio) y
`edge_pp_ex_gross` es la reasignación sola, que es de lo que responde δ. Medido en el retro vivo:
las filas preciadas cargan **+5,00pp de peso NETO** — porque una fila sin retorno disponible sale
de `contribs` pero su dw no —, así que de un edge de −0,01pp, **+0,11pp era beta y −0,12pp era el
tilt**.

**B10 — las dos escaseces se contradecían en el orden.** El ramp llena las compras por
`score_rank` y lo dice por escrito («the model's own ordering, not trade size»); después
`trade_budget_plan` re-ordenaba a los supervivientes por `-abs(trade_eur)`, así que un BUY grande
de rank bajo le quitaba la plaza a uno más pequeño de rank alto que el ramp acababa de priorizar.
Se raciona el cash antes que las plazas precisamente para que ambos coincidan en QUÉ nombres.
Ahora, dentro de cada tier, rank primero y tamaño como desempate — donde sí hacía trabajo real:
entre dos filas de igual calidad de rank, la mayor compra más exposición por plaza.

**B11 — `risk_state` era una columna muerta.** `cluster_risk_pass` terminaba con
`r.setdefault("risk_state", "n/a")` y nada le asignaba nunca otra cosa. Ahora dice qué concluyó el
pase sobre la fila (`blocked` · `trim_candidate` · `over_cluster` · `unmeasured` · `n/a`), que es
la única forma de que un lector distinga una fila que el presupuesto de riesgo DECLINÓ de una que
ni miró.

**C1 — la migración a EUR de v10.4 se quedó a medias en el sitio que más pesa.**
`portfolio._sector_vols` seguía leyendo `prices.read` (nativo) mientras `covariance._prices` ya
leía `read_eur`: **dos modelos de riesgo sobre dos bases dentro de un mismo build**, y con
`vol_tilt_alpha: 1.0` y λ=0 el `vol_tilt` es el mayor determinante del sizing. Efecto medido sobre
el libro de hoy: **≤0,08pp de peso** — un defecto de coherencia, no una fuga de dinero, y por eso
sobrevivió a la migración: nada imprimía mal.

**Lo que NO se hizo, con causa.** No se adopta la tabla de halflife (decisión del usuario, ahora
preciada donde aterriza). No se mueve `n_target` — sigue siendo la decisión abierta de v10.4, y
B3 la hace más urgente, no menos: la escalera que cuelga de él acaba de cambiar. No se rellena
`sector_impact` en los 19 documentos: la dirección de un evento sobre un sector se contesta con la
evidencia del evento delante, así que se rellena en el próximo `/catalyx-scan` para los que sigan
vivos, igual que la rúbrica de `strength_score` de v11. Y no se re-persiste el run ya grabado
(`run_20260901_095443`): sus overrides se firmaron contra esa tabla, y los criterios nuevos
aparecen en el PRÓXIMO score run en vez de recortar por detrás una tabla ya decidida — misma regla
que v10.1 y v10 P8.

**853 tests en verde (+56).**



## v11.0 — El score de catalizadores es una fórmula, pero dos de sus entradas las escribía un LLM a mano (2026-09-01)

**Salió de una observación del usuario: «veo números muy altos y de repente muy bajos… al fin y al
cabo lo está asignando un LLM y estos tienen problemas».** La premisa era medio falsa y por eso el
diagnóstico importa: la cadena **es determinista de punta a punta** — indicador → percentil sobre su
propia historia → intensidad → interacción evento/estructural → noisy-OR. Ningún LLM asigna el score
final. Pero el LLM entra por seis puertas en los INPUTS, y dos de ellas explican exactamente el
patrón que el usuario veía.

**Lo que causa el «alto y de repente bajo», medido, no supuesto.** La bimodalidad del digest (22
sectores entre 63 y 97, luego 36 / 13,7 / 13,7 / 0) no es una medición del mundo: es **sesgo de
selección doble**. Nadie escribe un catalizador estructural a intensidad 40, y el scan solo crea
eventos con `strength_score ≥ 55` — la distribución está censurada por debajo **por diseño**. Y el
techo está saturado: **nueve sectores entre 95,0 y 96,7**, un span de 1,7 puntos contra σ_cross de
28,5, o sea **0,06σ** — menos resolución que el redondeo del propio agregador. Ahí es donde el libro
concentra, y es donde `portfolio.ca_conviction_tilt` hace z-score entre los nombres seleccionados: el
tilt lleva desde v10 leyendo diferencias que la escala de nivel ya no transporta.

**P1 — la búsqueda solo miraba a un lado, y la ausencia de contradicción se leía como medición.**
El registro tiene **31 eventos `confirms` contra 2 `contradicts`**, y `catalyx-scan.md` no contenía
ni una instrucción de buscar evidencia contraria (verificado por grep: cero menciones). Toda query de
C0 y C2 se escribe alrededor de la tesis del propio catalizador, así que recupera el corpus que le da
la razón. Lo grave es lo de después: esa ausencia viaja aguas abajo como si hubiéramos mirado. Y el
scorer **ya tenía la maquinaria montada** — `contradict_max_points: 15` contra `confirm_max_points:
10` (una desconfirmación mueve el score vez y media más que una confirmación), y dos contradicts
vivos vuelcan un estructural a `breaking` vía `structural_monitor`. Esa maquinaria llevaba toda la
vida del proyecto corriendo sobre un flujo de entrada al que **nadie le había pedido que la
alimentara**. Nuevo **Step C2c**: una query por catalizador de `must_reverify`, escrita para
ROMPERLO (desde sus `invalidation_triggers`, enmarcada como el caso corto — «¿quién dice que X se
acabó?» no recupera lo mismo que «¿sigue X siendo cierto?»), y el resultado se graba **encuentre o
no**: `disconfirming: {searched, query, found, evidence}` en `scan_deltas`. `found: false` es la
medición — es lo que separa «buscamos el caso corto y no está» de «nadie miró», dos estados que han
sido indistinguibles durante toda la vida del registro. Misma doctrina que `not_attributed[]` ya
aplica al drift de atribución: una pregunta contestada tiene que constar, o se vuelve a hacer eterna.

**P2 — `strength_score` era el último cardinal libre, y se comportaba como tal.** La guía era prosa:
«magnitude × consensus_surprise × confirmation», que es una fórmula solo si las tres están definidas,
y ninguna lo estaba. Firma clásica: los 16 activos en [55, 91], σ=10, **agrupados en números
redondos** — tres 72, dos 80, dos 82, dos 75. `novelty_score` es estable entre sesiones desde v1.5
justamente porque es `count(true) × 20` sobre un rúbrica; `strength_score` no tenía ninguna. Ahora
tiene **`strength_score_rubric`** (5 preguntas OBSERVABLES, pesos 25/25/20/15/15: ¿es grande la
magnitud en términos del propio sector? ¿está DECIDIDO y no propuesto? ¿sorprendió al consenso? ¿lo
corrobora una segunda fuente independiente? ¿cambia una TASA o solo un nivel?) y **`strength_anchors`**
— la regla 3 de CLAUDE.md hecha mecánica: se nombra un evento registrado del que este es MÁS fuerte y
otro del que es MÁS débil, y el score tiene que caer dentro. **El bracket es lo que persiste entre
sesiones; el número absoluto nunca lo hizo.** Divergencia permitida contra la rúbrica: 15 puntos —
exigir acuerdo exacto solo entrenaría al autor a retro-ajustar los booleanos a un número ya elegido.

**P3 — el halflife era un SEGUNDO cardinal libre, y compone con el primero: decide cuánto tiempo
sigue contando el número libre.** `catalyst_taxonomy.yaml` lleva un halflife por `catalyst_type`
desde el principio y `catalyx-scan.md` dice en prosa «always set `decay_halflife_days` from
`catalyst_taxonomy.yaml`». La prosa perdió: **6 de 16 eventos activos contradicen la tabla, 5 hacia
MÁS persistencia** — que es la dirección que mantiene puntuando a un evento gastado.
`commodity_supply` tabla 21 → almacenado 90/90/120; `institutional_flow` tabla 14 → almacenado 90.
**Lo que NO se hizo, y es la decisión de diseño de esta versión.** `halflife_source` se envía como
`stored`, no como `taxonomy`, aunque `taxonomy` sea el estado final correcto. Adoptar la tabla hoy
movería el CA de una posición VIVA por la autoridad de una constante que seis reviews consecutivas
declinaron usar: el evento confirmador del cobre decae **38,9 → 4,0**, el Niño 36,0 → 3,8, el
contradict de crédito privado 21,9 → 0,0. O los seis autores se equivocaron o `commodity_supply: 21`
está mal — y adoptar la tabla por defecto **contesta esa pregunta por decreto**, que es exactamente
lo que CLAUDE.md prohíbe para el cap correlacionado («raising a cap first deletes a breach by
decree»). Así que se publican las dos, el lint imprime la discrepancia con la consecuencia
**preciada**, y el número queda como edit de umbral congelado del usuario con la tabla delante. Lo
que sí queda cerrado es que el drift no puede CONTINUAR: schema 1.3 exige `halflife_override_reason`
para discrepar, y el scan lo dice como regla, no como frase.

**P4 — el nivel del strength se sustituye por su rango dentro de la población registrada.** Misma
medicina que `intensity_engine` aplica desde v1.5 a los indicadores, y la regla 4 de CLAUDE.md
(«ordinal beats cardinal») por fin actuando sobre eventos. `calibrate_strength` mapea cada score a su
percentil empírico dentro de **todos** los eventos registrados —cualquier status: un evento archivado
sigue midiendo dónde caía un «72» cuando alguien lo escribió, y encoger la referencia según se
acumula historia es lo contrario de lo que quiere una calibración— y lo devuelve al **rango propio de
esa población**. **Range-preserving a propósito: re-espacia, no re-escala.** El máximo y el mínimo son
puntos fijos, es monótona (no puede reordenar nunca) y nada aguas abajo cambia de unidades. Efecto
vivo: |Δ| máximo 5,8 puntos, media 2,4, y solo 4 sectores mueven su CA (ninguno cambia de rank) —
suave por construcción. Los tres eventos escritos como exactamente «72» resuelven al mismo 69,4, que
es lo correcto: un empate en la entrada es un empate, no una coincidencia de números redondos.

**P5 — `ca_logit`, resolución donde la escala de nivel ya no tiene.** El logit es el inverso natural
del mapa saturante que creó el techo: los mismos nueve sectores pasan de **0,06σ a 0,212σ**, **×3,56**
de resolución. Columna candidata, **peso 0**. Que el `ca_conviction_tilt` deba cambiar a ella es una
decisión del usuario con el IC delante, no una sustitución hecha dentro de un edit de scoring.

**P6 — la primera entrada de la cadena que NO se lee de un documento.** Todo lo demás —un valor de
indicador, un enum de `narrative_maturity`, un strength asignado tras leer noticias— viene de papel.
Nada preguntaba nunca lo que un trader pregunta primero: cuando esto pasó, ¿se movió el sector?
`event_market_check` mide, por (evento, sector), el retorno anormal del vehículo sobre la ventana
posterior, en unidades de su propia volatilidad **previa** (medida estrictamente ANTES del evento: un
denominador que contiene el movimiento que escala es un punto fijo sobre sí mismo, la familia de bug
que `_trend_delta` ya documenta), con el signo de lo que el evento afirma — **un `contradicts` se
confirma con una CAÍDA**. Ambas patas pasan por `nav_engine._eur_prices` antes de restarse: un
benchmark USD contra un vehículo EUR apuntaría el movimiento del EURUSD de la ventana como alfa del
sector — el defecto de v8.1 («la escala se cancela; la divisa no») un nivel más abajo. **Lo que ya
nombra:** `cat_20260603_copper_supply_deficit_2026`, fuerza 78, el evento confirmador de la tesis de
cobre, tiene z **−1,47** sobre el propio cobre y **−2,85** sobre grid — la cinta se movió en contra
1,5σ en los cinco días siguientes. Columna candidata, peso 0, y **jamás una puerta**: un evento no
confirmado se NOMBRA, nunca se degrada solo. Que el mercado vaya tarde es la premisa del mandato
entero; automatizar «el mercado tiene razón» sería invertirlo.

**Dos defectos que destapó la propia implementación, y valen más que las funciones.** (1) El mensaje
de bracket invertido era **código muerto**: un bracket vacío falla una comprobación de cota para
CUALQUIER score, así que el mensaje de cota siempre ganaba la carrera — y «score por debajo de su
anchor stronger_than» manda al autor a cambiar el SCORE cuando lo que está mal es qué anchor fue a
qué campo. La inversión se comprueba primero. (2) `ca_logit` se leía de `catalyst_detail`, que
retorna temprano en los dos caminos sin catalizadores — y uno de ellos, un estudio con
`active_catalyst_ids` vacío, es un cero **MEDIDO**, no ausente. La columna perdía exactamente la fila
cuyo cero es un hallazgo (`infrastructure_core`). Se deriva del CA final; un CA **imputado** sigue
dando None, porque ése sí no es una medición (v6 H2).

**Lo que NO se tocó, con causa.** El peso de CA en el compuesto (0,35), la puerta de convicción de
v10, y el `ca_conviction_tilt`: evidencia negativa sobre el score de catalizadores cambia **cómo se
calcula**, nunca **si participa** (doctrina del usuario, 2026-09-01). Y nada de reponderar el
compuesto sin IC delante — K1 sigue bloqueada por datos.

Schema `catalyst_event` **1.2 → 1.3**. Migración: los 19 documentos existentes **no se reescriben** y
siguen validando y puntuando — `schema_version` acepta `["1.2","1.3"]` y los campos nuevos son
LINTADOS, nunca requeridos por el scorer, así que un evento pre-1.3 se reporta como cardinal desnudo
en vez de caerse. Hoy el lint da **16/16 FAIL**, que es el lint funcionando: ni un solo evento
registrado lleva rúbrica ni bracket. El run ya grabado **no se re-persiste** (sus overrides se
firmaron contra esa tabla); las columnas aparecen en el próximo score run. 771 tests en verde (+36).

---

## v10.4 — La señal se medía en cuatro divisas; el libro se cobra en una (2026-09-01)

**Salió de «arregla todo de golpe».** v10.3 restauró la lectura de la divisa y dejó la
consecuencia abierta; esto la cierra. **15 de los 26 vehículos puntuados no cotizan en euros**
(10 USD, 4 GBp, 1 GBP). Dentro de UNA serie la escala se cancela en un retorno; **entre series
no**, y todo lo que ordena o mide compara series.

**La pieza que más importa no era el momentum, era el TARGET.** `calibration.forward_returns`
leía cierres nativos, así que el IC preguntaba *«¿el composite predice el retorno de un comprador
en dólares?»* cuando lo que el operador cobra es el retorno en euros. Y de ese `fwd` cuelgan el
rank IC que **gatea λ**, el scorecard de reglas contra la baseline HOLD, el tally del `ca_tilt` y
el precio a 21d de cada override. Convertir solo la señal habría dejado los dos lados midiendo
cosas distintas — peor que no tocar nada.

**Cuatro consumidores, una implementación.** La conversión no es nueva: `nav_engine` convierte
P&L desde el primer día. Se ha movido a `catalyx/data/prices.py`, que es la capa que ya posee la
caché **y** la divisa por ticker, de modo que la regla GBp×0.01 existe UNA vez —
`prices.to_eur` / `fx_series` / `read_eur` / `read_eur_with_basis` / `basis_by_ticker`.
`nav_engine._to_eur` y `_default_fx_fn` quedan como delegados finos: el contrato inyectable
`ccy_fn`/`fx_fn` del que dependen los tests de NAV no cambia. Consumidores migrados:
`calibration.forward_returns`, `covariance._prices`, `comomentum` (price_fn por defecto) y
`market_data.fetch_metrics` — este último no pasa por la caché, así que convierte su propia serie
con el FX de la caché.

**Lo medido, separando efectos en vez de sumarlos.**

| | nativo | EUR | Δ |
|---|---|---|---|
| ρ semanal, 26 vehículos del universo | 0,199 | **0,179** | **−0,020** |
| ρ semanal, libro modelo (6 nombres) | 0,3213 | 0,3238 | +0,0025 |
| vol del libro modelo | 18,63% | 18,40% | −0,23pp |
| ranking de momentum | — | — | **12 plazas, máx 2**, top-6 intacto |

Dos lecturas que no se pueden saltar. **(1) La correlación BAJA al convertir, no sube.** La
predicción intuitiva —«añadir un factor FX común eleva la correlación»— es **falsa aquí**: la
pata FX compensa parcialmente el co-movimiento de las acciones. Y baja donde importa: los 26
vehículos del universo son la muestra de la que se leyó la rodilla de `n_target`, no el libro de
6. **(2) Sobre el libro VIVO el efecto es inmaterial** (+0,0025), así que nada de la tabla de
riesgo de hoy se explica por la divisa. Las dos cosas son ciertas a la vez y decir solo una
sería propaganda.

**`n_target` NO se toca.** Un ρ 0,020 más bajo empuja hacia MÁS nombres, y `n_target: 6` es el
mando único del que derivan `max_position_pct` y los `conviction_tiers`. Mover el mando en el
mismo edit que cambia la base de medición es exactamente cómo se pierde la capacidad de saber
cuál de los dos cambios hizo qué. Se publica el número y se decide después, con la tabla delante
— la misma regla que el cap nocional contra el presupuesto de varianza.

**Una fila declara su base, y una fila que no pudo convertirse NO dice EUR.** Cada fila de
momentum lleva `return_basis`; `momentum_engine` publica `return_basis`, `return_basis_values` y
`basis_mixed`, y **avisa por stderr cuando el panel mezcla bases** — un ranking transversal solo
significa algo sobre UNA base. Es diagnóstico, nunca puerta: las filas siguen ordenando, solo
dejan de aparentar. Las particiones anteriores a la migración no traen el campo y se leen como
`"native"`, no como EUR: «no medido» y «medido en euros» son afirmaciones distintas, la misma
doctrina que v6 H2 con un estudio ausente. Por el mismo motivo, una columna cuyo FX no se pudo
traer **se queda nativa y lo dice**, en vez de desaparecer del frame — perder la columna
encogería en silencio la sección transversal sobre la que se rankea, que es peor.

**Y el confound de calendario, fijado con test.** El FX se reindexa **sobre las fechas de
cotización del propio instrumento**. Un índice unión mete días en los que cotizó la divisa y no
el fondo, así que «N filas atrás» abarca una ventana de calendario distinta: medido, ese
artefacto **por sí solo duplicaba el efecto aparente** sobre el ranking (26 plazas y máx 3, contra
las 12 y máx 2 reales). Es el mismo error de forma que el de `_load_shares_series` en v10.2 —
una serie alineada contra la clave equivocada.

782 tests en verde (+11).

---

## v10.3 — La divisa del vehículo se leía por defecto, no por lectura (2026-09-01)

**Salió de un hallazgo colateral de v10.2 y de una pregunta del usuario: «esto tiene fix simple?».**
La lectura sí. La consecuencia que destapa, no.

`fetch_metrics` tomaba la divisa de `hist.attrs.get("currency", "USD")` y en esta versión de
yfinance **`hist.attrs` viene siempre vacío** — comprobado en V9N.DE, XAIX.DE, SRVR, IH2O.L,
IGLN.L y PPFB.DE. O sea, las 26 filas de momentum del lake decían `USD` **por defecto, no por
lectura**. Y ésa es exactamente la comprobación en la que se apoyó v8.1 para cazar que `IH2O.L`
cotiza en GBp mientras el operador paga en EUR: llevaba muerta en silencio desde entonces.

**El arreglo:** `Ticker.history_metadata` trae `currency` en el MISMO response del chart — cero
llamadas de red extra — y distingue correctamente `GBp` de `GBP`, `EUR` y `USD`. Cuando no se
puede leer se escribe **`None`, nunca `"USD"`**: convertir «no medido» en un valor es el defecto
que ocultó éste, y es la misma doctrina que v6 H2 aplica a un estudio ausente. `hist.attrs` se
mantiene como fallback por si una versión futura de yfinance vuelve a poblarlo.

**Lo que la columna restaurada deja ver, medido: 15 de los 26 vehículos puntuados NO cotizan en
euros** — 11 EUR, 10 USD, 4 GBp, 1 GBP. Dentro de UNA serie la escala se cancela en un retorno
(por eso ningún número cambia con este edit, y hay test que lo fija); lo que no se cancela es el
FX ENTRE series, y el compuesto ordena 26 sectores medidos en cuatro divisas distintas. v10.1 ya
lo había medido en un caso: mismo fondo, mismo día, 1y **+28,24% (EUR, PPFB.DE)** contra **+24,62%
(USD, IGLN.L)**.

**Tamaño del sesgo, medido sobre los 26 vehículos vivos** (convirtiendo cada serie a EUR con
EURUSD/EURGBP reindexado SOBRE las fechas de cotización del propio ETF — un índice unión mete días
en los que cotizó el FX y no el fondo, y eso por sí solo duplicaba el efecto aparente: 26 plazas y
máximo 3 antes de alinear, **12 plazas y máximo 2** después): el top-6 no se mueve, y los cambios
reales son `gold_miners` 11→9 y `copper_miners` 9→11. Segundo orden comparado con el error de
identidad de vehículo de v10.2, que movió 7 plazas él solo — pero `gold_miners` está en cartera.

**Lo que NO se hace aquí, y por qué.** Convertir los retornos a EUR no es un edit lateral: cambia
la BASE de todas las series de momentum del lake, así que las filas de antes y después no son
comparables — la misma rotura de escala que el `composite` pre-schema-1.4, y que allí exigió nota
de migración y detección de escala. Necesita además serie FX cacheada por run y alineación de
calendario por ticker. Queda como **decisión abierta del usuario** con la tabla delante, no como
efecto colateral de arreglar una lectura. 735 tests en verde (+7).

---

## v10.2 — El vehículo medía otro sector, y el CMF se comía el dato que lo habría arreglado (2026-09-01)

**Salió de dos frases del usuario:** «el xaix de data centers no ha podido calcular bien el flow»
y «he buscado en revolut y tengo un v9n, son equivalentes?».

**No lo son, y la diferencia no es de matiz.** `XAIX.DE` es Xtrackers **AI & Big Data** ($8.556M):
70,1% tecnología, **0% real estate**, top-5 MSFT/AMZN/AAPL/NVDA/GOOGL — y Walmart y Bank of America
en el top-10. `V9N.DE` es Global X **Data Center REITs & Digital Infrastructure** ($342M): **53,4%
real estate**, top-5 Equinix 13,3 / Digital Realty 12,3 / American Tower 10,2 / Crown Castle 6,8 /
NextDC 5,2. **Solape en el top-10: cero.** El `sector_id` se llama `ai_infrastructure_data_centers`
y el vehículo que lo puntuaba no tenía un solo data center: era una cesta ancha de IA cuya beta ya
estaba en el libro dos veces, en `cloud_software_saas` (rank 1) y `semiconductors_design` (rank 4).
Identidad verificada contra yfinance (`longName`/`currency`/`exchange`) como exige el universo
desde el incidente `IQQR.DE`. V9N.DE pasa a tier-1; XAIX.DE **baja a tier-2 con la razón escrita**,
no se borra. Cadena de flujo `V9N.DE → DTCR → SRVR` (DTCR = hermano US exacto, mismo índice Global
X; SRVR = Pacer, mismo tema, y es el que tiene historia en el lake). AUM 342M supera el umbral de
200M pero por poco — verificar spread antes de dimensionar.

**Lo que cuesta el vehículo equivocado, medido:** contrafactual sobre `run_20260831_184616`
(baseline reproducido **exacto**, 26/26 ranks y composites). Momentum **67,3 → 36,5** — V9N a 3m
−13,9% contra −4,8% de XAIX, a 6m +12,1% contra +35,8%, a 52w-high −14,8% contra −5,9%: los REITs
de data center se comieron el golpe y la cesta de megacaps de IA no. Con el flujo real añadido,
**rank 2 → 9, composite 60,5 → 53,4** (z +0,701 → +0,224). Descompuesto: solo momentum → rank 8;
solo flujo real → rank 5. **El BUY de €1.500 aprobado para este sector se apoyaba en un rank que
lo ganó otro fondo.** Ningún `Movement` referencia el sector — la orden nunca se ejecutó
físicamente, así que no hay base de coste que tocar.

**El defecto que destapó, que vale más que el cambio de ticker.** El flujo del sector no fallaba
porque XAIX sea UCITS: la cadena `XAIX.DE → AIQ → BOTZ` **sí** resolvía shares (AIQ: 158.450.000
vía stockanalysis). Lo que faltaba era la lectura ANTERIOR — sin dos lecturas no hay delta —, así
que la resolución devolvía `kind: baseline` y caía al CMF. Y ahí, al entrar el CMF, `fetch_flow_data`
sobrescribía `flow_proxy_ticker` con el vehículo de OHLCV, y `flow_to_rows` buscaba entonces
`etfs["XAIX.DE"]` — que no existe — en vez de `etfs["AIQ"]`. **El baseline se tiraba a la basura en
cada run**, de modo que el run siguiente volvía a empezar de cero: CMF permanente por construcción.
La fila del lake lo confirmaba: `2026-08-31 | XAIX.DE | shares=NaN | volume_proxy`.

**No era un sector: eran seis de los ocho que estaban en CMF.** Comprobado en vivo — `AIQ` 158,45M,
`SLV` 545,85M, `GLD` 352,40M, `ICLN` 125,60M, `DAPP` 17,25M, `MOO` 12,30M, todos devolviendo un
conteo real que el run descartaba. Solo `eu_defense_prime_contractors` y `luxury_goods` carecen de
verdad de hermano US. Dicho de otro modo: el «13,1 puntos de error medio del CMF» que midió v6.9 se
estaba pagando en seis sectores que no lo necesitaban.

**El arreglo separa las dos preguntas que la fila mezclaba:** `flow_proxy_ticker` dice *de qué
vehículo se leyó la señal* (bajo CMF, el de OHLCV) y el nuevo `shares_ticker` dice *a quién
pertenece el conteo de acciones*. `flow_to_rows` busca el raw por `shares_ticker`, y
`_load_shares_series` lo añade a los campos con los que casa una historia — es el único de los tres
que es correcto en una fila CMF. La columna es aditiva (`union_by_name` en DuckDB, `pd.concat` en
pandas), así que las particiones viejas se leen igual. **El score sigue siendo el del CMF y sigue
marcado `volume_proxy`:** preservar el baseline no convierte una aproximación en una medición — la
medición llega al run SIGUIENTE, porque `_load_shares_series` exige la lectura previa
estrictamente anterior a hoy.

**Efecto inmediato, verificado end-to-end sin escribir al lake:** `ai_infrastructure_data_centers`
pasa a **flujo REAL hoy mismo** (`proxy_computed`, SRVR, −0,805% → 43,6) porque SRVR sí tiene
historia en el lake — el sector la tuvo hasta el 2026-07-05 y el cambio de vehículo de universo v2.0
la había roto. `gold_physical` y `silver_physical` siguen en CMF este run (correcto: aún no hay
lectura previa) pero ya persisten `shares_ticker=GLD/SLV` con el conteo, así que gradúan en el
siguiente. 728 tests en verde (+3).

**Hallazgo colateral, NO corregido aquí:** `market_data.fetch_metrics` lee la divisa de
`hist.attrs.get("currency", "USD")` y en esta versión de yfinance `hist.attrs` **viene siempre
vacío** — comprobado en V9N.DE, XAIX.DE y SRVR. O sea, las 26 filas de momentum del lake dicen
`USD` por defecto, no por lectura. Es exactamente la comprobación en la que se apoyó v8.1 para
cazar el problema GBp/EUR de `IH2O.L`, y está muerta en silencio. No se toca en este edit porque
afecta a toda la tabla de momentum y merece su propia entrada.

---

## v10.1 — UCITS no es lo que hace comprable a un vehículo; el KID de PRIIPs sí (2026-09-01)

**Salió de una frase del usuario:** «veo que oro no tiene asignado etf, en Revolut veo PPFB».

Las dos mitades eran ciertas y la segunda explica la primera. `gold_physical` y `silver_physical`
imprimían `—` en la columna ETF del ranking, y no era un fallo de display:
`portfolio.py:576` hace `df = df[df["primary_etf"].notna()]`, así que **los dos sectores estaban
excluidos de la construcción del libro modelo**, y `rebalance` los rechazaba con «no buyable UCITS
vehicle». `silver_physical` rankeaba **10º** este run — dentro del campo del que sale el top-N — y
no podía recibir un euro por una razón que nadie había enunciado.

**La causa es una sobrecorrección de la propia doctrina de comprabilidad.** El 2026-08-28
`primary_etf` caía al pool no-UCITS cuando un sector no tenía entrada UCITS, y así imprimió
`BUY biotech_drug_development €891` contra **IBB** — un ETF US que PRIIPs hace imposible para esta
cuenta. El arreglo fue exigir `ucits: true`. Y `ucits` no es el discriminante:
**lo que hace vendible un vehículo a un minorista español es el KID de PRIIPs, no el sello UCITS**.
Un fondo de un solo metal **no puede** ser UCITS — la diversificación UCITS lo prohíbe —, así que
todo el oro y la plata físicos de Europa son ETCs, llevan KID y cotizan en Xetra y LSE. El flag
juntaba «US, sin KID, ilegal aquí» con «nota europea perfectamente comprable», y el universo v2.0
ya no contiene **ni una sola** entrada en bolsa US: el filtro defendía de una amenaza que ya no
está en el fichero mientras tiraba 2 de los 26 sectores investables.

**El arreglo filtra por VENUE, que es la garantía que el parche del 08-28 buscaba de verdad.**
`snapshot_repo.PRIIPS_VENUES` = {LSE, XETRA, EURONEXT, SIX, BME, BORSA_ITALIANA}; una cotización US
falla el test **diga lo que diga** `ucits` o `instrument` — hay un test con un `IBB` que se declara
`ucits: true` para fijarlo. El fallback silencioso a un ticker no comprable sigue siendo peor que
`None`, y sigue prohibido.

**El vehículo:** `PPFB.DE` (iShares Physical Gold ETC, EUR, Xetra — verificado contra yfinance
`longName`/`currency`/`exchange`) pasa a tier-1 de `gold_physical` y a `chain[0]` de
`SECTOR_TICKERS`; `IGLN.L` baja a tier-2 sin borrarse. Es el mismo ETC que `IGLN.L`/`SGLN.L`, y es
**la línea que el buscador del broker devuelve** — misma regla que v8.1 (`IQQQ.DE` sobre `IH2O.L`,
`2B76.DE` sobre `RBOT.L`). La escala se cancela en un retorno; **la divisa no**: mismo fondo, mismo
día (2026-09-01), 1y **+28,24%** en EUR contra **+24,62%** en `IGLN.L` (USD), 6m −15,75% contra
−16,52%. `4GLD.DE` (Xetra-Gold, EUR) sigue tier-2 a propósito: es un bono al portador alemán con
fiscalidad distinta, no un ETC. `broker_access` sigue `assumed` — visto en el buscador, no operado.

**Lo que NO cambia:** el run `run_20260831_184616` no se re-persiste. Sus overrides se firmaron
contra esa tabla, y `primary_etf` se escribe en el snapshot (`snapshot_repo.py:345`), así que los
dos sectores entran en el **próximo** score run — que es donde el criterio debe aparecer, no
recortando por detrás una tabla ya decidida. Ninguno de los dos está en cartera; no se toca
ninguna base de coste. `silver_physical` no gana vehículo nuevo (`PHAG.L` ya era `chain[0]`), solo
deja de ser invisible; no se encontró línea Xetra en EUR de un ETC de plata al probar contra
yfinance.

**Cuatro tests nuevos en `test_config_feasibility.py`**, donde ya viven los invariantes de config:
todo sector investable resuelve vehículo · **el vehículo que se puntúa es el que se recomienda**
(`SECTOR_TICKERS[sid][0] == primary_etf(sid)`, que hoy se cumplía en 24 de 26 y fallaba justo en
los dos rotos — es el defecto de la v2.0 «puntuar COPX y comprar 4COP» un nivel más abajo) · una
cotización US nunca resuelve · un ETC europeo sí. 725 tests en verde (+16).

---

## v10.0 — El libro solo tenía una manera de ser pequeño: fallar (2026-09-01)

**Salió de dos frases del usuario:** «si bajo catalizadores o riesgo hay sectores que dejan de ser
interesantes deberíamos eliminarlos, no ir a por los n primeros» y «hay que incluir el riesgo y la
concentración como métricas para recomendar rebalanceos».

**El diagnóstico, que es lo que hay que quedarse.** Todo mecanismo capaz de encoger el libro
desembocaba en el mismo sitio — un *deployment shortfall* que la regla de persistencia reabría cada
review. No existía forma de decir «este sector dejó de merecer la pena», ni «cuatro nombres es el
número correcto este mes», ni «quiero caja hasta X» que sobreviviera un ciclo. Y **la mitad de lo
que el usuario pedía ya estaba construido**: los filtros pre-corte (`min_momentum: 40`,
`max_crowding: 85`) ya son absolutos, `deploy_ratio` ya modela caja con rampa de VIX, y
`sell_if_any.catalyst_status: [invalidated, dormant]` ya es una salida DIRECTA por catalizador
muerto, con precedencia máxima. Lo que faltaba eran cuatro piezas concretas, no un rediseño.

**P1 — la puerta de convicción, absoluta, al lado de la relativa.** `min_composite_z: 0.0` es
relativo por construcción: significa «mejor que la media de ESTE run», así que en un universo donde
todos los drivers se apagan sigue admitiendo a la mitad del campo y el libro sigue lleno porque el
libro siempre está lleno. Un z-score ordena; no opina sobre si lo que ordena merece poseerse.
`portfolio.conviction_gate()` lee `catalyst_alignment`, que SÍ es un nivel absoluto [0,100] y ya es
el mayor peso del compuesto (0,35). **El suelo 40 se lee de la distribución medida, no se elige:**
en `run_20260831_184616` los 26 valores de CA son bimodales — veinte entre 71,5 y 96,2, luego un
hueco de 25 puntos, luego 36,2 / 13,9 / 13,9 / 0,0, que son exactamente los sectores sin driver
estructural vivo. 40 cae en el hueco. **No mueve la tabla de hoy** (los cuatro excluidos rankean
17º o peor) y eso es el punto: enuncia el criterio sin recortar en silencio una tabla ya decidida.
Lo que para es el caso que aún no ha pasado — `eu_retail_banking`, momentum 86,5 con CA 13,9, a un
buen trimestre del techo rank-8 de `buy_if`, comprado por precio sin driver detrás. **Un CA
IMPUTADO (`ca_imputed`) no se juzga:** pasa y se reporta sin evaluar, porque convertir «no medido»
en «suspende» es la única inferencia que v5 y v6 dedicaron dos versiones a eliminar. **Un cero
MEDIDO sí se juzga** — `infrastructure_core` marca 0,0 con estudio detrás, y ese cero se observó.

**P2 — el shortfall se mide contra lo que el libro PUEDE sostener.** `deployable` dice cuánto
debería estar trabajando y calla sobre si existen NOMBRES elegibles para sostenerlo; con techo del
20% por posición, cinco nombres es el mínimo que absorbe un libro completo, cuatro topan en 80%,
tres en 60%. Un libro que las propias reglas cortan a cuatro se medía contra un objetivo
inalcanzable por construcción, y la regla de persistencia reabría esa brecha cada review — una
exigencia infactible, incumplida permanentemente, y por tanto ignorada con el tiempo. Es el mismo
fallo que v6 L2 encontró en el cap de catalizador correlacionado, llegando por el otro lado.
`absorbable_eur()` baja el listón a `min(deployable, Σ target)`. **Y no es un escondite para caja,
por tres razones que van en el código:** el techo es la aritmética del cap (`n × max_position_pct`,
nada discrecional), AMBOS números se siguen imprimiendo — `shortfall_vs_deployable_pp` nunca
desaparece de la página — y `cash_drag` sigue preciando CADA euro ocioso contra el benchmark y
contra el model book. La regla deja de exigir lo imposible y sigue cobrando por lo posible.

**P3 — la postura de caja: dicha UNA vez, fechada, preciada, y que CADUCA.** Había dos maneras de
estar infra-desplegado y solo una era una decisión. `cash_stance` deliberadamente NO es un tipo de
documento nuevo: es un override, en el override log, con autor y razón, preciado ~21 días de
mercado después contra el benchmark que declinó — porque lo que mantiene honesta una vía de escape
es que se le ponga precio, y esa maquinaria ya existía. Lo nuevo es solo que responde a una regla
de LIBRO en vez de a una fila, y que **expira** (90d): el silencio es lo que la regla de shortfall
existe para romper, así que una postura que no caducara reconstruiría el silencio que sustituye.
Autores máquina (`budget`, `ramp`, `unrecorded`) no pueden escribir una: automatización excusando
su propia salida no responde ante nadie. `shortfall_status` pasa a `breached: false` con
`breach_raw: true` — **la brecha no se borra**, se contesta, y se reabre sola en su fecha.
CLI: `rebalance cash-stance --reason ...` (sin `--reason` muestra la vigente).

**P6/P7 — el riesgo entra en la tabla, y la discrepancia con el nocional es el hallazgo.**
El párrafo de `correlated_catalyst_cap` terminaba desde v6.4 en «replacing the notional cap with a
risk budget is a decision for the user»; el usuario la tomó. `max_cluster_variance_pct: 35.0` NO
sustituye al cap nocional: ambos corren, ambos en `warn`, un ciclo, y dos runs de evidencia deciden
cuál sobrevive — el mismo orden que v6 L2, donde una medición se gana su enforcement y un cap nunca
se relaja sobre una sola tabla. **35 se elige contra el spread medido** (libro post-trade
2026-08-31): a 35 incumple exactamente UN cluster, `ai_capex`, el que todo review ya nombra,
mientras `biopharma` — que incumple el NOCIONAL a la mitad del riesgo que carga `gold` bajo él — no.
`cluster_risk_pass()` corre como post-pase sobre la tabla terminada, porque el riesgo de un cluster
es propiedad del libro POST-trade y ese libro no existe hasta que la primera pasada lo produce (la
misma razón por la que el presupuesto de operaciones y la rampa son post-pases). Bloquea ADD/BUY
hacia un cluster por encima de presupuesto y NOMBRA al miembro que más riesgo carga como candidato
a TRIM, dimensionado a primer orden. **Nunca toca un SELL o un REDUCE:** un límite de riesgo que
pudiera cancelar una RETIRADA de riesgo sería un límite discutiendo consigo mismo. **Un riesgo NO
MEDIDO (`over_risk: None`) jamás se lee como «dentro de presupuesto»** — la misma regla que
`cluster_risk_for` sigue al devolver None en vez de 0.

**Un defecto que la propia implementación destapó, y que vale más que la función:** el candidato a
TRIM salía de `members_by_risk`, que se mide sobre el libro POST-trade — así que un nombre que solo
se PROPONE comprar carga riesgo ahí sin existir todavía, y como el bloqueo acababa de declinar esa
misma compra, el presupuesto habría cancelado una compra y vendido el resultado en el mismo aliento.
El candidato exige ahora posición REALMENTE tenida (`actual_eur > 0`) y no haber sido bloqueado en
la misma pasada: una acción por fila por pasada.

**Lo que la tabla nueva dice hoy, que es por lo que esto no es cosmético.** `struct_ai_capex_
supercycle` incumple los dos (62,5% nocional, **89,2% de la varianza del libro**), pero
`struct_energy_transition_grid` **cumple el nocional al 15,0% y carga el 44,6% de la varianza** —
las dos reglas discrepan, y discrepan sobre un driver que el cap nocional declara limpio. Esa fila
es exactamente la evidencia que el usuario pidió. Dos clusters salen UNMEASURED y se reportan como
tal. Ninguna acción cambió: ambos enforcement son `warn`.

**Lo que se DECIDIÓ NO hacer, con causa.** El plan inicial incluía una pata «evidencia `blind` >N
días bloquea un BUY». Se descartó al leer `_data_age_by_sector`, cuyo docstring fija la doctrina
opuesta y por escrito: *«This QUALIFIES a row. It never votes: `decide_action` does not read it, by
design… stale data is a reason to RE-VERIFY, not to stop acting — turning a maintenance failure
into a trading prohibition is the conservative bias Phase C exists to fight.»* Revertir eso en
silencio dentro de un edit sobre otra cosa habría sido justo el tipo de cambio que este proyecto
registra para no repetir. Queda como decisión abierta del usuario.

695 tests en verde (+31), con dos invariantes nuevas de factibilidad: un libro LLENO debe poder
absorber la regla de despliegue (si no, la relajación de P2 dispararía siempre en vez de solo
cuando la puerta corta el libro), y el presupuesto de riesgo debe admitir una posición individual
(el fallo de v6 L2 en unidades de varianza).

**P8 — el score de catalizadores DIMENSIONA el libro, como prior declarado (`ca_conviction_tilt`).**
Salió de tres frases del usuario en el mismo hilo: «para el rebalanceo no se usa nuestro score de
catalizadores, y es el CORE de nuestro approach», «quiero darle un peso, aunque no lo tengamos
calculado… tiene que funcionar, si no no tenemos nada que medir», y la doctrina que lo gobierna:
«no vamos a apagar CA nunca; si va mal, cambiaremos cómo se calcula, pero nunca se apaga».

**El diagnóstico era correcto en el sentido que importa.** CA ya elegía los NOMBRES (peso 0,35 del
compuesto → rank → buy/sell; puerta absoluta P1; status binario → SELL), pero con λ=0 no ponía ni
un euro de diferencia entre ellos: el libro quedaba inverse-vol neutral, y el argumento estadístico
de esperar al IC tiene un fallo causal — **un tilt que nunca mueve euros no genera jamás la serie
que lo calibraría**, porque el contrafactual no se construye. Así que el delta entra como PRIOR
DECLARADO — el mismo estatus epistémico que el 0,35 de CA en el compuesto (v8 Q3: «declared, not
hidden») — y lo que lo mantiene honesto no es una puerta sino un LIBRO MAYOR.

**El mecanismo** (`portfolio.ca_tilt`, entre `skill_shrink` y `vol_tilt`): `w ← w·(1 + δ·z_CA)`,
z cross-sectional de `catalyst_alignment` entre los nombres SELECCIONADOS, winsor ±2,5; un CA
imputado va a z=0 (v6 H2: lo no medido se imputa al prior, nunca se inclina) y fuera de los
momentos. **δ=0,20 no se inventa: mapea el rango del winsor sobre la escalera de convicción YA
declarada** (`tier_multiples` [1.4, 1.0, 0.5]: +2σ→×1,4, 0→×1,0, −2,5σ→×0,5 — los tiers que
ningún código leía). Corre ANTES de `vol_tilt` (convicción primero, comparabilidad en riesgo
después) y antes del cap (un límite de riesgo manda sobre una preferencia de tamaño). Solo libros
composite: `momentum` y `equal` son baselines contrafactuales y se quedan limpios.

**El libro mayor que lo hace falsificable.** Cada build persiste el contrafactual δ=0 exacto
(`w_pre_ca_pct`, ambos libros recorren idéntico el tramo vol_tilt→cap→deadband) junto a `ca_z` y
`ca_tilt_mult`; `calibration.ca_tilt_edge` (CLI `--ca-tilt`) precia `Σ(w_post−w_pre)·fwd` por
ventana COMPLETA de 63d, un run por fecha de review, ventanas efectivas no solapadas — y
**descompone por sector y por driver** (crédito solapado, la regla de `exposure_eur`). La doctrina
está en el código: un tally negativo NOMBRA el catalizador cuyo cálculo desvió el sizing
(indicadores, decay, noisy-OR, frescura) — la evidencia entra por recalibrar CA, nunca por
apagarlo. `ca_tilt_delta_min: 0.05` hace `delta: 0` inescribible por construcción; mover δ es un
edit de umbral congelado, config + CHANGELOG con el tally delante.

**Efecto vivo, que es por lo que no es cosmético** (libro modelo sobre `run_20260831_184616`, sin
re-persistir el run ya decidido — sus overrides se registraron contra la tabla pre-tilt y un juicio
fechado no se reescribe): los CA 95–96 se amplifican (×1,06–1,12) y `cybersecurity_commercial`
(CA 79, z −2,23 en un cross-section apiñado en 94–96) cae ×0,55 → su target 17%→10,2% y su ADD de
la tabla anterior degrada a HOLD. La tabla de rebalance gana la columna `caZ` al lado de `rk` y la
línea `CA-TILT δ=…` bajo `TILT λ=…`: el prior que dimensiona cada fila, impreso donde mueve euros.
709 tests en verde (+14).

**P8b — el tilt toma su z del LOGIT, y la retro es la otra mitad del contrafactual.** Dos
peticiones del usuario el mismo día: «lo quiero funcionando YA, y luego mejoraremos los scores»
y «tener esa información es oro — ¿se puede calcular a posteriori con los runs?».

**El problema que arregla está MEDIDO; el arreglo NO está backtesteado, y se envía igual por
decisión explícita del usuario.** El noisy-OR está acotado en 100 y anclado al portador más
fuerte, así que un sector con varios catalizadores fuertes satura: en el universo del
2026-09-01 **nueve sectores caen entre 95,0 y 96,7** — 1,7 puntos contra σ_cross 28,5, o sea
**0,06σ**, menos resolución que el redondeo del propio agregador. Esa banda no es una curiosidad:
es donde el libro concentra, y es la población exacta que el tilt estandariza — el tilt estaba
leyendo diferencias que la escala había dejado de transportar. `ca_tilt_scale: "logit"` toma la z
de `log(p/(1−p))` del MISMO CA. Es la inversa del mapa saturante que creó el techo, y sobre los
nombres del libro vivo la banda del techo pasa de un span de z de 0,177 a 0,430 — **×2,4 de
resolución** donde se decide el tamaño. **La transformación es MONÓTONA**, así que re-espacia y
jamás reordena: la selección, la puerta de convicción y todos los ranks quedan intactos por
construcción, y solo cambia el espaciado entre multiplicadores (efecto en el libro vivo: máx
0,34pp, `copper_miners` +0,34 / `cloud` −0,19). `scale: "level"` reproduce el sizing de P8 exacto.
**Lo que NO arregla, dicho claro:** un techo comprimido puede significar que la escala está mal
O que los sectores son de verdad igual de fuertes. El logit asume lo primero y nada lo distingue
todavía — por eso el tally sigue siendo lo que decide. Una fila sin `ca_logit` (run pre-v11, o un
CA imputado, donde el logit es None por diseño) hace caer **toda la sección transversal** al
nivel, y se declara (`scale_used`, `scale_fallback`): una z necesita UNA escala, y mezclar dos
dentro del mismo z compara unidades que no son comparables.

**`ca_tilt_edge_retro` — el contrafactual mirando hacia atrás.** La pata viva solo puede empezar
a medir desde P8; los 13 runs ya grabados son la otra mitad, y son literalmente el libro «sin
CA». Así que se les aplica el δ de hoy usando el `catalyst_alignment` que **ese run congeló** en
su propio `sector_snapshot` — punto-en-tiempo, y el único hindsight es el retorno forward, que es
el que un backtest sí puede usar — se recapa y se precia `Σ(w_tilt − w_grabado)·fwd` sobre la
misma ventana que usa la pata viva. Juntas dan **una serie continua**: retro antes de P8, viva
después. Persiste en `validation/ca_tilt_retro` (re-ejecutable, sobreescribe por run).
**Dos decisiones que la implementación forzó:** (1) los runs son de desarrollo — hay ráfagas de
cinco en una tarde —, así que las fechas se colapsan con `_cycle_runs` del propio rebalance
(mismo `min_gap_days: 21`, se queda la lectura más reciente del clúster): una tarde de iteración
no puede contar como cinco observaciones de la misma decisión. (2) El cap del retro es
`max(cap_hoy, mayor línea del libro grabado)` — cobrarle al contrafactual un techo que la
baseline nunca enfrentó falsearía el resultado; un libro totalmente cap-bound sale como «sin
ventana», nunca como cero. **Lo que dice hoy:** +0,35pp en la única ventana cerrada de 63d;
−0,04pp de media en 3 ventanas de 21d (−0,43 / +0,12 / +0,20). Sin veredicto — es exactamente la
muestra que `calibration` etiquetaría `noise` —, pero ya es una serie que crece sola, y su mayor
lastre tiene nombre (`struct_energy_transition_grid`), que es lo que la doctrina pide: localizar
QUÉ catalizador recalibrar, nunca el interruptor. 787 tests en verde (+78 sobre v9).

---

## v9.0 — La regla decía DÓNDE debe estar el libro y nunca dijo A QUÉ VELOCIDAD (2026-09-01)

**Salió de dos frases del usuario:** «en rebalanceo hay algo mal, pone 773 hold y yo tengo 565 en
uspy» y «me gustaría que rebalanceo sea la tabla maestra de esta iteración; no meter los 10.000 de
golpe, ir escalando».

**Lo del 773 no era un defecto: era un TARGET de otro run.** `773,97` es el `target_eur` de
`cybersecurity_commercial` en `run_20260828_102925`; el `actual_eur` de esa misma fila es `564,57`
— exactamente los €565 que el usuario ve en el bróker. La columna «actual» ha sido correcta todo el
tiempo; lo que confunde es que la tabla contesta DOS preguntas a la vez (a dónde va el libro, y qué
se ejecuta hoy) y las imprime en columnas contiguas. Esa confusión es el defecto real, y es lo que
arregla el bloque **THIS ITERATION**: las filas que sobreviven a las dos escaseces, en orden de
ejecución, arriba del todo, con la fricción de cada una. Lo demás de la tabla es en qué se apoyan.

**La rampa de despliegue (`deployment.ramp.max_step_pp: 15.0`).** El `deploy_ratio` decía «el 75%
debería estar trabajando» y no decía nada sobre cuánto tarda en llegar, así que cada review
infra-desplegado pedía el hueco ENTERO de una vez: €4.868 en seis nombres a los precios de una sola
tarde — la mayor apuesta de timing que el sistema puede hacer, y la única que nadie eligió. La
rampa es la mitad que faltaba: el destino no cambia (`deployable`), la RUTA son 15 puntos de
capital total por review. Tres propiedades la separan de un escondite para el efectivo:

- **Limita el NETO** (`Σ trade_eur`), así que una rotación pura —vender un nombre y comprar otro—
  pasa sin tocarla. Raciona escalar EN, no la rotación.
- **Rellena por RANK y a tamaño COMPLETO**: un tramo compra un nombre a peso de convicción, no seis
  cuartos de posición. Un cuarto de posición no es una apuesta más pequeña, es una peor — el mismo
  hueco de operación y el mismo spread para un cuarto de la exposición.
- **No borra el incumplimiento.** El shortfall se sigue midiendo contra el `deployable` COMPLETO,
  el cash drag se sigue imprimiendo al lado, y cada fila encolada se registra (autor `ramp`) y se
  precia a 21 días como cualquier desviación. Si el marcador dice que la rampa costó dinero, el
  número que cambia es este. Los 15pp son un parámetro de AVERSIÓN, como `base`, no una derivación:
  a `n_target=6` y despliegue máximo 0,85 es un nombre a peso neutro por review, y llegar al 75%
  desde 26% cuesta ~4 reviews (~4 meses a la cadencia de 30-45d). Test de factibilidad incluido: el
  tramo debe poder colocar al menos una posición neutra completa.

**Tres defectos que el cambio destapó, y que valen más que la función nueva:**

1. **El presupuesto de operaciones racionaba huecos entre filas que la rampa iba a encolar.** Con
   el orden viejo (huecos antes que caja) seis BUYs consumían la asignación y **echaban de la tabla
   el TRIM de `pharma_large_cap`** — justo la fila que el usuario ya había aprobado. Ahora: CAJA
   primero (qué cabe en el tramo), HUECOS después (qué cabe en las 6 operaciones del review). Con
   el orden corregido el tramo pasa de 3 filas a 5, y la que entra es la venta.
2. **`cap_check` ignoraba las ventas que financian las compras.** Tenía un `if eur <= 0: continue`,
   así que una tabla que cierra `copper_miners` para comprar dentro del MISMO clúster se medía como
   si la venta no ocurriera. En vivo hoy la diferencia es la decisión entera:
   `struct_ai_capex_supercycle` leía **39,1% (incumple, €913 por encima)** contando el cobre que se
   está vendiendo, y **29,1% (cumple)** contándolo bien. El cap pregunta cuánto dinero se mueve si
   el driver se rompe, y después de la tabla ese dinero ya no está. La deducción se limita a la
   BASE de exposición: un ganador vendido por encima de coste no puede retirar más exposición de la
   que llegó a llevar.
3. **Re-correr el rebalance del mismo score run duplicaba los DEFER automáticos**, y un DEFER de
   máquina de un corte anterior sobrevivía aunque el corte nuevo concediera la fila — un registro
   que dice «diferido» sobre una fila que el usuario ejecuta, y que `override_edge` habría preciado
   como real. Ahora los DEFER de autor `budget`/`ramp` se **reconcilian** contra la tabla vigente
   (se retractan los que ya no aplican) y nunca se archiva un segundo veredicto sobre una fila que
   una PERSONA ya contestó. La autoría humana no se toca: una decisión de una persona no es de la
   máquina para retirarla.

**R2 — una salida completa no hace cola, la llame la regla como la llame.** Lo preguntó el usuario
en cuanto vio la rampa: «¿esto afecta a si quiero vender una posición completa aunque sea muy
grande?». La rampa no (toda fila negativa se concede: verificado con una venta de €9.000 bajo un
tramo de 1pp), y `SELL`/`REDUCE` ya estaban exentos del presupuesto de huecos. Pero **`TRIM` no lo
estaba**, y una salida completa llega etiquetada `TRIM` siempre que el target del modelo es 0% —
que es como sale del libro **todo** sector que el model book ha dejado de tener (hoy mismo, copper
y pharma). Con más de 6 filas que mueven dinero, un cierre total podía quedar detrás de una compra:
la asignación de huecos decidiendo **mantener riesgo**, que es justo lo contrario de la regla que
la lista de exentos codifica. `exempt_full_exits: true` + `is_full_exit()` (trade negativo, target
0%, y el importe cubre la posición) lo corrigen sin borrar el tier 2: un TRIM que rasca 4pp de un
sobrepeso contra un target VIVO sigue siendo rotación y sigue siendo lo primero que se queda sin
hueco. Una fila que no puede saber si cierra (sin target o sin valor de mercado) **no** reclama la
exención — conceder sobre dato ausente es como una guarda se convierte en puerta trasera.

El marcador `*` de la tabla pasa a significar «encolada por una escasez (hueco u ramp)». El
dashboard gana la tarjeta **this tranche** y el mismo bloque de orden de ejecución, y persiste
`ramp_state`/`budget_state` por fila más la ruta (`book_ramp_*`) para que un run releído mañana
diga por qué una fila estaba encolada. `review_report` añade `ramp` a los autores automáticos: la
línea RAMP ya nombra cada fila retenida, y un marcador que pide prosa por una decisión que nadie
tomó enseña a saltarse los marcadores. 667 tests en verde (+12).

**Efecto en la tabla viva:** el tramo de esta iteración son 5 filas —SELL `copper_miners` €1.051,
SELL `pharma_large_cap` €556, ADD `semiconductors_design` €374, BUY `ai_infrastructure_data_centers`
€1.500, BUY `cloud_software_saas` €913— que dejan el libro al 38,1% desplegado; `water_infrastructure`
(€1.499) y `robotics_automation` (€1.468) quedan encolados al siguiente review. Con el cap ya
contando la venta del cobre, las cinco juntas dejan `struct_ai_capex_supercycle` en **32,9%**
(incumple por €287) y sin el ADD de semis en **29,1%** — que es dónde está la decisión.

---

## v8.1 — El vehículo que sale en el buscador es el vehículo que se puntúa (2026-09-01)

**Salió de una pregunta del usuario, no de un test:** buscando `IH2O` en el bróker no aparece el
ETF — aparecen **HTO** (H2O America) y **GWRS** (Global Water Resources), dos *utilities* de agua
estadounidenses de **acción única**. El buscador matchea el texto «water», no el vehículo, y las dos
respuestas que ofrece son instrumentos de otra clase de riesgo. `RBOT` tampoco resuelve; el fondo
aparece como **2B76**.

La regla de la v2.0 del universo dice que `chain[0]` es el vehículo **realmente comprable**, porque
puntuar `COPX` y comprar `4COP.DE` mide un retorno que no obtienes. Un ticker que el bróker no
devuelve incumple esa regla exactamente igual: no es comprable *de hecho*, aunque lo sea sobre el
papel. Así que se promueve a tier-1 la línea con la que el vehículo SÍ aparece:

| sector | tier-1 antes | tier-1 ahora | mismo fondo |
|---|---|---|---|
| `water_infrastructure` | `IH2O.L` (LSE, GBp) | **`IQQQ.DE`** (Xetra, EUR) | sí — iShares Global Water UCITS, USD (Dist) |
| `robotics_automation` | `RBOT.L` (LSE, USD) | **`2B76.DE`** (Xetra, EUR) | sí — iShares Automation & Robotics UCITS, USD (Acc) |

Identidad verificada contra yfinance (`longName`/`currency`/`exchange`), que es lo que exige el
universo desde el incidente `IQQR.DE`. Las líneas viejas **no se borran**: bajan a tier-2 con la
razón escrita. Ninguna de las dos está en cartera (`data/movements/` no las menciona), así que el
cambio no toca ninguna base de coste.

**Lo que el cambio corrige además, y no es cosmético.** El comentario de `SECTOR_TICKERS` afirmaba
que una cotización en GBp «no da problema porque el momentum es un RETORNO y la escala se cancela».
La escala se cancela; **la divisa no**. `IH2O.L` medía el retorno de un comprador en GBP y el
operador paga en EUR — sobre el mismo fondo, el mismo día, la diferencia es real: 1y **−0,09%** en
GBp contra **+1,51%** en EUR, 6m **−6,41%** contra **−3,90%**. La promoción alinea la divisa de la
medición con la divisa en la que se paga, que es el mismo argumento que la doctrina v2.0, un nivel
más abajo. Comentario reescrito en `market_data.py`.

`SECTOR_FLOW_TICKERS` se actualiza solo en su primer elemento por coherencia; los proxies US
(`PHO`/`FIW`, `ROBO`/`BOTZ`) siguen haciendo el trabajo — las dos tablas siguen sin unificarse.
Ambas líneas nuevas traen ≥2 años de histórico en yfinance, así que el momentum 12-1 de v8 no
pierde cobertura. `broker_access` se queda en `assumed`: el usuario los ha visto en el buscador,
no los ha operado, y `verified` significa operado de hecho. 663 tests en verde.

## v6.9 — La dimensión de flujo medía otra cosa en 20 de 26 sectores (2026-08-31)

**La dimensión de flujo medía otra cosa en 20 de 26 sectores.** Salió porque el usuario preguntó por qué el flow imprimía un warning. Lo imprimía con razón: `stockanalysis.com`, la fuente PRIMARIA de shares outstanding, devolvía **47 errores y 0 aciertos** — su endpoint no oficial había empezado a dar 404 —, así que todo caía en cascada hasta CMF, un oscilador de precio+volumen que la propia nota del módulo etiqueta «⚠ not true flow». Veinte sectores puntuaban `flow_confirmation` (peso 0,15) con un indicador técnico. **El arreglo:** el dato sigue en la página, solo murió la ruta. Dos caminos la sustituyen, el más pequeño primero — el payload `__data.json` de SvelteKit (~49 KB, cuya clave `sharesOut` guarda un ÍNDICE al array aplanado y hay que desreferenciar: leída como valor da 27) y la página renderizada (~228 KB) como respaldo. **Revalidado contra el screener oficial de iShares** en 8 fondos solapados: −4,15%..+0,92%, dentro de la banda ~0,25–1,75% con la que esta fuente se aceptó en su día. Cobertura de 6 a **18 de 26** sectores con shares reales. **Lo que la reparación MIDIÓ, que es lo que hay que quedarse:** doce sectores pasaron de CMF a shares reales de golpe, así que el error del sustituto ya no se supone, se observa — **|error| medio 13,1 puntos sobre 100, máximo 25,6** —, y los dos errores mayores cayeron justo en los dos nombres sobre los que la tabla actuaba: `gold_miners` inflado (CMF 78,2 vs 52,6 real) y `cybersecurity_commercial` desinflado (28,8 vs 54,2). **Una hipótesis se comprobó y se DESCARTÓ antes de escribirla:** CMF no es momentum disfrazado — su correlación con la dimensión de momentum es +0,16, así que el fallo es inexactitud, no doble contabilidad. **Por eso CMF ahora se imputa, no se puntúa:** quedan 8 sectores sin proxy US limpio, y 13 puntos de error medio no son un proxy. Se tratan como v6 H2 ya trata un estudio ausente — imputados al prior (z=0), fuera de los momentos de la dimensión, y marcados como COLUMNA (`flow_imputed`, `~flow` en el digest), nunca como puerta. La rama específica de CA en `commensurate` se generalizó a un mapa `_IMPUTED_FLAG` en vez de duplicarse, con un test que fija que el camino de CA no ha regresado. **Dos defectos de ranking que destapó el re-scoring, de la misma forma que el #3 de v6.8:** `composite_z` se guardaba con `round(s, 3)` — y desde que v6.8 lo convirtió en la unidad sobre la que se ordena, ese redondeo es el mismo defecto un decimal más abajo (`water_infrastructure` y `semiconductors_design` empataban en +0,457 y los separaba el orden de la lista); ahora 6 decimales, así que un empate es un empate de verdad. Y el digest imprimía solo `comp` a 1 decimal mientras ordenaba por z, lo que hacía parecer mal ordenada una tabla correcta; ahora imprime `z=` al lado. **Efecto en la tabla viva, que es por lo que esto no es cosmético:** `cybersecurity_commercial` pasa de rank **10 a 5**, convirtiendo un **TRIM −€565 en un ADD +€727** — el trim se apoyaba en un peldaño cuya condición es «el modelo ya no lidera este nombre», y el modelo solo dejó de liderarlo porque una fuente rota le había desinflado el flujo 25 puntos. `semiconductors_design` 5→6 (ADD → HOLD), `ai_infrastructure_data_centers` 6→8 (se cae su BUY de €1.456), `gold_miners` 8→11. De ocho acciones a seis, y el incumplimiento del cap de `struct_ai_capex_supercycle` baja de **55,4% a 38,8%** del nocional (€881 por encima, no €2.540). 614 tests en verde (+2).

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

## v6.8 — Seis defectos que el review se encontró a sí mismo (2026-08-31)

> Rotada desde la tabla `Recent Changes` de CLAUDE.md, verbatim.

**Seis defectos que el review se encontró a sí mismo, y el que llevaba meses reescribiendo scores.** Salieron al correr un SEGUNDO `/catalyx-review` el mismo día — condición que varios necesitaban para hacerse visibles. Ningún threshold movido; todo son correcciones. **(1) La intensidad alimentaba su propia tendencia.** `write_back` escribía la fila de HOY en `intensity.history` y `_trend_delta` leía `scores[0]-scores[1]` — o sea, su propia estimación previa. El score era una iteración de punto fijo sobre sí mismo: `struct_cb_gold_accumulation` hizo **78,6 → 68,5 → 64,5** en dos runs con el mundo quieto, cada paso reportado como `↓ falling 1 period`. Compone, y en la única dirección que parece una tesis muriéndose. Arreglado en tres capas: `compute_intensity` excluye la fila con la fecha de hoy (es su propia estimación, no un periodo cerrado; una etiqueta explícita `--period 2026-Q3` no casa y se conserva), `write_back` REEMPLAZA la fila de ese periodo en vez de apilar otra, y `_trend_delta` deduplica por periodo al leer, lo que repara todo fichero ya escrito del modo viejo. **Nueve de trece catalizadores tenían periodos duplicados —hasta SEIS filas de `2026-Q2`—, así que esto llevaba meses malleyendo la pata de tendencia**, no solo hoy. Un test fija que tres write-backs seguidos dan salida idéntica. **(2) `--all --write-back` escribía los scores en los ficheros EQUIVOCADOS.** `compute_all()` salta `merged` y `role: macro_context` (13 resultados) pero el CLI los emparejaba con un `glob("*.yaml")` fresco (18 rutas): el score del oro acabó en `biopharma_patent_cliff_ma`, el de `ai_capex` en `commercial_space`, y los últimos cinco ficheros no se escribieron nunca. Los resultados ya llevan `_source_file`; ahora cada escritura se direcciona por él. Es el comando que la entrada de v6.7 manda ejecutar, así que el bug estaba armado esperando a quien siguiera las instrucciones. **(3) El rank publicado salía del compuesto REDONDEADO.** `composite` va a un decimal, así que cualquier par a menos de 0,05 era un empate resuelto por el orden del fichero de taxonomía — cuando v6/H1 ya había declarado `composite_z` la unidad comparable. En vivo hoy: `space_defense_satellite` (49,4, z −0,041) por encima de `nuclear_energy` (49,4, z −0,038) por pura suerte alfabética. `sector_scorer.rank_key()` elige la unidad y se usa en todos los sitios que ordenan, incluido el corte top-N de `portfolio` — que es donde el redondeo cuesta dinero. **(4) El ranking del work list no era el ranking grabado:** `--universe` pasaba un `--crowd` plano a todos, σ_cross de crowding = 0 y el lint de dimensión muerta lo cantaba, en la vista cuyo único trabajo es alimentar los estudios. **(5) Cinco sellos de frescura rebotaron, en los catalizadores recién verificados:** el enum de `catalyst_review` tenía tres valores mientras `catalyx-scan.md` documentaba cinco, y el docstring afirmaba que mapeaban «sin traducción». `strengthening` se rechazaba, así que tres drivers de posiciones abiertas seguían leyéndose stale el día que se re-verificaron. **(6) Dos líneas que mentían en el transcript:** `catalyst_lifecycle --apply` imprimía «dry run — use --apply» cuando no había transiciones, e `indicator_update batch` imprimía `Σ None` en la única línea que dice qué catalizador se movió. 612 tests en verde (+6).

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
