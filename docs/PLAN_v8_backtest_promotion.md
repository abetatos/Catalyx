# PLAN v8 — Backtest punto-en-tiempo: las candidatas se ganan el peso con historia, no con espera

> **ESTADO: EJECUTADO 2026-08-31 (v8.0, Fase P + Q1·Q2·Q3 aprobadas por el usuario).**
> Resultados en `validation/backtest_ic` y en el CHANGELOG §v8.0. Sucesor de
> `docs/PLAN_v7_signal_content.md` (ejecutado).
> v7 dejó 9 columnas candidatas con peso 0 esperando ~3 ventanas vivas de 63d (≈ mayo 2027).
> **El usuario ha decidido que esa espera no es aceptable** — y la salida correcta no es bajar
> el listón de evidencia sino AMPLIAR la muestra hacia atrás: las señales reconstruibles
> punto-en-tiempo se miden sobre 10-20 años de historia que ya existe. Es el TODO de
> "Backtesting harness (strict no-look-ahead)" de CLAUDE.md, ejecutado para la decisión que
> ahora lo necesita.
>
> **Lo que este plan cambia de doctrina, dicho sin rodeos:** la evidencia para el config edit
> pasa de "ventanas vivas" a "ventanas históricas sin look-ahead". Lo que NO cambia: el edit
> lo aprueba el usuario con la tabla delante (Fase Q), el backfill de la tabla viva sigue
> prohibido, y λ (el sizing) sigue esperando al IC VIVO — el backtest autoriza selección,
> nunca agresividad.

---

## 0. Diagnóstico

### 0.1 Qué es reconstruible punto-en-tiempo (medido 2026-08-31)

Profundidad de historia de los vehículos (yfinance, `period="max"`):

| UCITS | desde | Hermano US | desde |
|---|---|---|---|
| 4COP.DE | 2021-11 | COPX | **2010-04** |
| SEMI.L | 2021-08 | SOXX | **2001-07** |
| BTEC.L | 2017-10 | XBI | **2006-02** |
| IUES.L | 2015-11 | XLE | **1998-12** |
| SPGP.L | 2011-09 | GDX | **2006-05** |
| IGLN.L | 2011-04 | GLD | **2004-11** |
| IH2O.L | 2009-01 | PHO | **2005-12** |
| RBOT.L | 2016-09 | ROBO | 2013-10 |
| USPY.L | 2015-09 | CIBR | 2015-07 |
| WCLD.L | 2019-09 | WCLD | 2019-09 |

| Señal | ¿Reconstruible sin look-ahead? | Con qué |
|---|---|---|
| momentum 3m/6m (la actual) | **SÍ** | precios ≤ t |
| `momentum_12_1` | **SÍ** | precios ≤ t |
| `near_52w_high` | **SÍ** | precios ≤ t |
| `cot_crowding` | **SÍ** — historia CFTC desde 1986 | percentil 5y rolling en cada t |
| `crowding_comomentum` | **SÍ** (precios) — con caveat de agrupación (0.2) | residuos rolling 52w en cada t |
| `trends_crowding` | Parcial — 5y por fetch, ~4y utilizables tras burn-in | percentil expanding en cada t |
| `inst_sponsorship` (13F) | Posible — EDGAR EFTS filtra por fecha — pero lento | opcional (P3b) |
| `flow_resid` | **NO** — no hay historia de shares outstanding | queda en el carril vivo |
| `ca_unpriced` / CA | **NO** — los indicadores tienen 5-6 observaciones | queda en el carril vivo |
| valuation | **NO** — la serie empezó ayer (O3) | queda en el carril vivo |

**Consecuencia estructural:** el backtest puede recalibrar la familia MOMENTUM y la familia
CROWDING — exactamente las dos decisiones pendientes (K2, K4/N4). **No puede tocar el peso de
CA**: sin historia de intensidad, CA conserva su peso por prior, y eso se dice en la tabla
final en vez de esconderse.

### 0.2 Los caveats, escritos antes de medir

1. **Hermanos US = medir la SEÑAL del tema, no el libro comprable.** Misma doctrina que los
   flow proxies: la señal transversal de un tema global es vehículo-agnóstica; el retorno que
   obtienes no. El backtest calibra el ORDEN; el libro se opera en UCITS.
2. **Agrupación del comomentum:** los clusters (qué sector comparte driver con cuál) son el
   mapa de HOY. Aplicarlo a 2015 es un look-ahead suave en la agrupación (no en los datos).
   Se reporta y se acepta: la alternativa (reconstruir mapas históricos de catalizadores) no
   existe.
3. **Régimen:** 2012-2026 es mayormente un mercado alcista con dos shocks. Todo IC se reporta
   POR SUB-PERIODO (pre-2020 / 2020-2022 / 2023+) además del agregado; un IC que solo vive en
   un sub-periodo se dice.
4. **Inferencia con ventanas solapadas:** las mensuales solapan a horizonte 63d. El se se
   estima con bootstrap por bloques y se reporta n_eff de ventanas independientes, no el n
   crudo.
5. **El backtest es in-sample PARA SIEMPRE.** Una vez usado para elegir pesos, no puede
   validarlos. El scoreboard out-of-sample del nuevo composite son las ventanas VIVAS de
   `calibration` — que siguen acumulando y que dentro de unos meses dirán si el cambio ordenó.

---

## 1. Objetivos

1. IC por señal con 100+ ventanas mensuales (10+ años) donde la señal lo permita, hoy.
2. **Superposición medida**: matriz de correlación entre señales → pesos que descuentan
   redundancia en forma cerrada (Grinold–Kahn: `w ∝ Ω⁻¹ · IC`, Ω = correlación entre señales),
   shrunk hacia los pesos vigentes. Nada de búsqueda exhaustiva de pesos (data mining).
3. Una tabla de decisión para el usuario: pesos propuestos + IC + se + n_eff + sub-periodos.
4. Ejecutar el config edit que el usuario apruebe, con CHANGELOG y tests (Fase Q).

---

## 2. Fase P — El harness (`experiments/backtest_signals.py`)

**P1 — Panel builder mensual punto-en-tiempo.** Para cada fin de mes t desde ~2012: señales
computadas con datos ≤ t sobre el universo de hermanos US (+ UCITS donde ya existían), forward
return t→t+21d y t→t+63d del MISMO vehículo de la señal. Universo por disponibilidad con flag
(un sector entra cuando su vehículo tiene 13 meses de historia). Resultados a una tabla NUEVA
del lake (`validation/backtest_ic`) — **jamás a `sector_snapshot`**.

**P2 — Familia momentum, head-to-head.** `mom_3m6m` (la vigente) vs `momentum_12_1` vs
`near_52w_high`: IC medio, se por bloques, sub-periodos, y la correlación ENTRE ellas (si 12-1
y 52w-high son la misma señal con dos nombres, la tabla lo dirá antes de que pesen doble).

**P3 — Familia crowding, con signo de penalización.** `cot_crowding` (historia completa CFTC,
sleeve de 5 sectores), `crowding_comomentum` (rolling 52w), `trends_crowding` (4y utilizables).
¿Predice ALTO crowding BAJO retorno forward? Por señal y para el blend N4.
**P3b (opcional, si el tiempo de EDGAR lo permite):** `inst_sponsorship` con EFTS filtrado por
fecha, pasos trimestrales.

**P4 — Superposición y pesos propuestos.** Matriz de correlación completa (candidatas +
oficiales computables), n_eff de señales; solución Grinold–Kahn con shrinkage
`w_final = γ·w_GK + (1−γ)·w_actual`, γ = n_eff/(n_eff + prior) — el MISMO patrón de
credibilidad que `skill_lambda`; sensibilidad: perturbar cada IC ±1se y reportar la estabilidad
del reparto (unos pesos que bailan con 1se no están medidos). CA mantiene peso por prior,
declarado en la tabla.

## 3. Fase Q — El cambio (decisión del usuario, mismo día que vea la tabla)

**Q1 — Momentum spec.** Si 12-1 domina a 3m/6m fuera del ruido: sustituye el blend en
`momentum_period_weights` (frozen-threshold protocol: config edit + CHANGELOG).
**Q2 — Crowding.** Si el crowding medido domina la etiqueta: `crowding_measured` entra al
composite con fallback a etiqueta donde no hay medición (la infra N4 ya lo blenda).
**Q3 — Reparto del composite.** Los pesos shrunk de P4 sustituyen a 35/29/24/12. El z-space
absorbe el cambio de escalas sin tocar nada más.
**Q4 — Salvaguardas que NO se mueven:** λ sigue gated por IC VIVO (el backtest autoriza qué
ordena la selección, no cuánto dinero se aparta de neutral); el cap por catalizador intacto;
las ventanas vivas quedan como out-of-sample del nuevo reparto; tests de la Fase M intactos
(la protección pasa de "candidata sin peso" a "peso == config", que es lo que siempre protegía).

## 4. Rechazos, para no re-litigarlos

- **Backfill de candidatas en `sector_snapshot`** — look-ahead en la tabla que el calibration
  vivo lee. El backtest vive en `validation/backtest_ic`.
- **Armar λ o el after-tax gate con IC de backtest** — autorizan TRADES; esperan al vivo.
- **Grid-search de pesos sobre el backtest** — con 9 señales y 100 ventanas encuentras oro
  falso seguro; solo la forma cerrada GK + shrinkage.
- **Backtestear flow_resid / CA / valuation** — sin datos históricos; fabricarlos sería
  calibrar contra ficción.

## 5. Orden

```
P1 (panel) → P2 ∥ P3 → P4 (tabla de decisión) → usuario decide → Q1-Q4 + CHANGELOG
```

Todo P es ejecutable en una sesión (horas). Q es un edit el mismo día que la tabla exista.
