# CATALYX — Monthly Review 2026-06-05

> Ciclo ejecutado tras la refactorización a **parquet lake**. Objetivo doble: (a) review mensual
> real, (b) **validar la maquinaria de runs/evolutivos** end-to-end y cazar errores de la migración.
> Runs registrados hoy: `run_20260605_081805` (baseline) → `run_20260605_115544` (sucio, flow-artefacto)
> → **`run_20260605_121805` (RUN LIMPIO autoritativo: decay-anchor corregido, writer lake-only sin SQLite)**.

⚠ **PRE-CALIBRATION:** pesos del composite sin validar (0 theses cerradas). Los scores indican
ordenación relativa, no niveles de convicción precisos.

---

## ★ Clean Run #3 — `run_20260605_121805` (autoritativo)

Tras corregir el decay-anchor y con el writer `snapshot_repo` ya **lake-only** (SQLite eliminado en
sesión paralela), se registró un tercer run limpio. **El evolutivo aisló el impacto del fix como se
predijo:** todos los sectores ligados a `struct_nato_rearmament` (vía el confirm del hormuz) cayeron
**catalyst_alignment 83.9 → 79.6 (−4.3)** — el hormuz dejó de sobre-contar (92 → 30.5). Defensa
demovida (`space_defense_satellite` #6→#8). Evolutivo `run2→run3`: `ai_infrastructure_data_centers`
#10→#7, `lng_natural_gas` #17→#28.

**Top-10 limpio (run #3):** space_commercial 74.0 · grid 72.8 · semis_design 70.8 · semis_memory 70.8 ·
copper 70.5 · cyber_commercial 70.2 · ai_infra 69.4 · space_defense 69.1 · cyber_defense 68.9 · semis_equip 68.7.

**Carteras (NAV 180d, run #3):** momentum +41.9% (+30.5pp) · equal_weight +35.9% (+24.6pp) ·
conviction +35.6% (+24.3pp) · low_crowding +29.8% (+18.4pp). Las 4 baten al SPY.

**Reframe de la "rancidez":** cyber y space se **crearon hoy** con valores de junio-2026; sus `last_date`
antiguos (2025) son la **fecha nativa de la serie** (informes anuales), no abandono. La auditoría de
frescura sobre-marca indicadores anuales etiquetados como `quarterly` → **bug de la lógica de auditoría**,
no datos rancios. Por eso el run limpio NO requirió refresco WebSearch de esos indicadores. (Acción 🟡:
arreglar la auditoría para usar la cadencia nativa del indicador / corregir los `check_frequency`.)

> Lo de abajo documenta el run #2 (sucio) y el proceso; se conserva para trazabilidad del evolutivo.

---

## Executive Summary

1. **El evolutivo (ranked de rank-moves) FUNCIONA end-to-end.** Segundo run registrado y diffeado
   contra el primero: `rank_event` pasó de 0 → 6 eventos. Los dos read-paths — SQLite cache
   (`snapshot_repo events`) y lake/DuckDB (`lake_query moves`) — devuelven **resultados idénticos**:
   el write-through a parquet y la lectura DuckDB están consistentes.

2. **[NO OBVIO] El movimiento de este evolutivo es 100% artefacto de la dimensión `flow`, no señal.**
   `catalyst_alignment` y `momentum` son **idénticos** entre los dos runs (mismo snapshot diario);
   el ranking se invirtió (`ai_infra` #1→#10, `grid` #7→#1) sólo porque el primer run se grabó con
   `flow_confirmation=50` (placeholder) y el segundo con flujos reales tras `flow_data --write`. Es
   un **hito de calidad de datos** (la dimensión flow se activó), no un deterioro fundamental.

3. **[BUG DE PROCESO, corregido] La frescura corría DESPUÉS del scoring.** El gate de
   stale-indicators + lifecycle era Step 10, posterior al run (Step 5). Resultado: el run rankeó
   `space_commercial` #2 y cybersecurity #6–#8 sobre catalizadores con indicadores de **124–520
   días**. Reordenado el skill: ahora **Step 1.5, antes del scoring**.

4. **[BUG, corregido] El decay se anclaba en `detected_at`, no en la fecha del evento.**
   `cat_20260228_hormuz_closure` (evento 28-feb, `priced_in=1.0`) puntuaba **decayed=92** como si
   tuviera 1 día. Corregido `catalyst_scorer` para anclar en la fecha de ocurrencia → ahora
   **decayed=30.5**, reflejando los 97 días reales. 82/82 tests verdes.

5. **Las 4 carteras modelo baten al SPY** en backtest 180d (momentum +30.5pp, conviction +27.4pp,
   equal_weight +24.6pp, low_crowding +11.5pp de alpha) — **con la salvedad** de sesgo look-ahead:
   el NAV backtestea las holdings ACTUALES hacia atrás (momentum selecciona ganadores recientes y
   luego los "mantiene" en el periodo en que ganaron). El test forward honesto es `snapshot_repo validate`.

---

## 0. Macro & Geopolitical Context (deltas vs datos del proyecto)

- **Fed**: 3.50–3.75%, en pausa por 3ª reunión (voto 8-4, 4 disidencias). FOMC 16-17 jun. Warsh sucede a Powell (15-may). Sesgo *hawkish*.
- **ECB**: tipos sin cambio (mar/abr); **mercado descuenta subida en junio** — divergencia al alza.
- **Oro**: ~$4.529 (2-jun); ATH $5.589 (28-ene). Polonia lidera compras CB (plan 700t). JPM target ~$6.000 cierre de año → **catalizador CB gold fuertemente activo**.
- **Defensa/NATO**: ReArm Europe €800B; Alemania +24% ($114B), Francia €68.5B (2.25% PIB); compromiso 5% PIB para 2035 → **rearme estructuralmente intacto**.
- **Ucrania**: alto el fuego de 3 días en mayo, intercambio de prisioneros; Putin "llegando a su fin", Trump "cada vez más cerca". **DELTA**: posible des-escalada — no invalida el rearme estructural, pero es señal de lifecycle para catalizadores de guerra.
- **Cobre**: dispersión fuerte — Citi *bullish* $15.000/t (1-jun) vs Goldman ~$10.710 H1; decisión de aranceles a fin de junio. JPM: demanda datacenter ~475kt en 2026.

---

## 1. Catalyst Updates

- Sin nuevos `CatalystEvent` por encima del umbral 55 no cubiertos ya (NATO, copper, gold, AI chips, Hormuz registrados).
- Decay re-anclado (ver §Bugs). Strengths decaídos recomputados:

| Catalyst | raw | decayed (nuevo ancla) | priced_in | nota |
|---|---|---|---|---|
| `cat_20260228_hormuz_closure` | 94 | **30.5** (era 92.4) | 1.00 | aproximándose a archivado (umbral decayed<20 ≈ mediados julio) |
| `cat_20260603_nato_defense_gdp` | 91 | 89.3 | 0.25 | activo, reciente |
| `cat_20260603_nato_hague_5pct_gdp` | 82 | 80.4 | 0.25 | activo |
| `cat_20260603_copper_supply_deficit_2026` | 78 | 76.5 | 0.50 | activo |
| `cat_20260601_us_ai_chip_export_controls` | 62 | 60.9 | 0.25 | activo |

## 2. Sector Studies Refreshed

**SKIP por rotación de frescura** — los ~40 studies en `data/sector_studies/` tienen `last_updated`
≤ 2 días (gate de 7 días). No se re-estudió ninguno este ciclo (ahorro ~46 WebSearches). Correcto
según el skill.

## 3 / 4. Heatmap — Nuevo Run `run_20260605_115544` (53 sectores)

| # | sector_id | composite | catalyst | momentum | flow | maturity | ETF |
|---|---|---|---|---|---|---|---|
| 1 | `grid_infrastructure_utilities` | 72.8 | 96.1 | 76.1 | 64.8 | mainstream | IQQH.DE |
| 2 | `space_commercial` | 72.6 | 82.0 | 92.0 | — | emerging | ROKT |
| 3 | `semiconductors_memory` | 71.7 | 89.9 | 98.9 | — | crowded | DRAM |
| 4 | `semiconductors_design` | 71.6 | 91.5 | 96.6 | — | crowded | SEMI.L |
| 5 | `copper_miners` | 70.5 | 96.0 | 69.3 | 61.8 | mainstream | COPA.L |
| 6 | `space_defense_satellite` | 70.3 | 84.0 | 80.7 | — | emerging | ROKT |
| 7 | `cybersecurity_defense` | 70.2 | 84.0 | 85.2 | — | mainstream | BUG |
| 8 | `cybersecurity_commercial` | 70.2 | 86.0 | 89.8 | — | mainstream | ISPY.L |
| 9 | `semiconductors_equipment` | 69.5 | 92.0 | 87.5 | — | crowded | ASML |
| 10 | `ai_infrastructure_data_centers` | 69.5 | 96.9 | 94.3 | 34.1 | crowded | AIPO |

> ⚠ **Fiabilidad del ranking degradada este ciclo:** top muy apelmazado (3.3 pts entre #1 y #10) →
> la dimensión `flow` (peso 0.15) hace oscilar el ranking con fuerza. Además, varios catalizadores
> que conducen el top tienen indicadores rancios (ver §Stale). El run sirve como **validación de
> maquinaria**; su orden cardinal NO es señal de convicción.

## 4b. EVOLUTIVOS — rank_event (verificación del objetivo)

`run_20260605_081805` → `run_20260605_115544`:

| sector_id | event | from → to | Δ | driver |
|---|---|---|---|---|
| `grid_infrastructure_utilities` | rank_up | #7 → #1 | +6 | flow 50→64.8 |
| `copper_miners` | rank_up | #10 → #5 | +5 | flow 50→61.8 |
| `ai_infrastructure_data_centers` | rank_down | #1 → #10 | −9 | flow 50→34.1 |
| `rare_earth_miners` | rank_up | #15 → #11 | +4 | flow |
| `eu_defense_prime_contractors` | rank_up | #25 → #22 | +3 | flow/mom |
| `us_defense_prime_contractors` | rank_down | #22 → #25 | −3 | flow/mom |

✅ Mecanismo OK · `rank_event` 0→6 · SQLite y DuckDB consistentes · `score_run`/`sector_snapshot` 2 particiones.

## 5. Carteras Modelo + NAV vs SPY (backtest 180d)

| Estrategia | Retorno | Alpha vs SPY | Posiciones |
|---|---|---|---|
| momentum | +41.9% | **+30.5pp** | 10 (semis/space/cyber) |
| conviction | +38.8% | +27.4pp | 10 |
| equal_weight | +35.9% | +24.6pp | 10 |
| low_crowding | +22.9% | +11.5pp | 10 (excluye crowded) |

Benchmark SPY consistente en las 4 (NAV final 111.74 ≈ +11.7%). **Caveat:** NAV = buy-and-hold de
holdings ACTUALES backtesteado hacia atrás → sesgo look-ahead/superviviente. Track-record forward real: `validate`.

## 6. Open Theses

| Thesis | Días | Catalyst align | Acción | Razón |
|---|---|---|---|---|
| `thesis_…_grid_infrastructure_utilities_bindingconstraint` | 2 | 96.1 (#1) | **HOLD** | Fed en pausa (no +100bps) favorece; transformer lead-times y capex hyperscaler intactos; mayor convicción del universo |
| `thesis_…_copper_miners_datacenter_alpha` | 2 | 96.0 (#5) | **HOLD** | Déficit de cobre activo (str 76.5); Citi $15k; riesgo: arancel fin de junio (binario). Vigilar inventario LME <200kt |

## 7. Portfolio Correlation

| Open theses | Catalyst primario | Combined % | Cap (20%) | Estado |
|---|---|---|---|---|
| grid + copper | distinto (grid=energy_transition_grid; copper=copper_datacenter_demand) — comparten *driver* AI capex | 4% + 4% = **8%** | 20% warn | ✅ OK, sin breach |

## 8. Tax Snapshot YTD (2026)

| Métrica | Valor |
|---|---|
| Theses cerradas | 0 |
| Ganancias realizadas | €0.00 |
| Impuesto pagado | €0.00 |
| Tramo marginal actual | 19% |

## 9. Thesis Draft Decisions

Candidatos top-5 sin thesis abierta: `space_commercial` (#2), `semiconductors_memory` (#3),
`semiconductors_design` (#4).

**Recomendación este ciclo: WAIT para todos.** El ranking está degradado (flow-artefacto + catalizadores
rancios). `space_commercial` lo conduce un catalizador con indicadores de 124–156 días; los semis están
`crowded` (crowding 75). No comprometer capital sobre un run marcado como no-fiable. Re-evaluar tras un
run limpio (indicadores refrescados).

## 10. Stale Indicators (Step 1.5 — auditoría)

| Catalyst | Indicadores stale (días) | Sectores afectados | Acción |
|---|---|---|---|
| `enterprise_cyber_spend_supercycle` | 96, **311, 489, 520, 520** | cybersecurity_* (#6–#8) | 🔴 refrescar (datos hasta de 2025) |
| `commercial_space_supercycle` | **124, 156, 156** | space_* (#2, #6) | 🔴 refrescar |
| `solar_lcoe_deployment` | 80, **156×3** | solar_energy | 🟡 refrescar |
| `nato_rearmament` | 96 (defense % GDP) | defense | 🟡 1 indicador |
| `energy_transition_grid` | 96 ×2 | grid (#1) | 🟡 |
| `copper_datacenter_demand` | 96 (DC copper est.) | copper (#5) | 🟡 |
| `ai_capex_supercycle` | 96 (DC power) | ai_infra | 🟡 |

## 11. Watch-Only Triggers

Sin cambios materiales detectados este ciclo (revisión ligera). Pendiente barrido WebSearch dedicado
en próximo ciclo limpio.

## 12. Taxonomy Gap Review

**0 propuestas pendientes** en DB (`catalyst_repo summary`). Nada que promover/rechazar/diferir.

---

## Bugs encontrados y corregidos (error-hunt de la migración parquet)

1. **Decay anclado en `detected_at`** (`catalyx/scorer/catalyst_scorer.py`) — eventos registrados
   tarde no decaían. Fix: nuevo `_anchor_date()` (precedencia `event_date` → fecha parseada del id
   `cat_YYYYMMDD_` → `detected_at`); `_decayed_strength` ahora tolera datetimes naive (asume UTC).
   Añadido `event_date` explícito al hormuz. **82/82 tests verdes.**

2. **Orden de pipeline** (`.claude/commands/catalyx-monthly-review.md`) — frescura+lifecycle movido
   de Step 10 (post-run) a **Step 1.5** (pre-scoring), para que el run puntúe sobre estado limpio.

## Hallazgos NO corregidos (decisión del usuario: documentar)

- **`run_20260605_115544` se grabó con el decay viejo y catalizadores rancios** → su ranking no es
  fiable como señal. Próximo run reflejará hormuz=30.5 y (si se refrescan) indicadores al día.
- **`IQQR.DE` (robotics_automation) figura *delisted* en yfinance** → revisar `etf_universe.yaml`,
  el NAV de equal_weight/low_crowding lo está descartando.
- **Evolutivo dominado por `flow`**: mientras flow esté mayormente en placeholder 50, los rank-moves
  reflejan activación de datos, no fundamentales. Madura desde el 2º snapshot de flow real.

## Pending Actions

- 🔴 **Refrescar indicadores rancios** de cyber (520d) y space (156d) — conducen el top y son de 2025.
- 🔴 **Re-correr el pipeline en orden nuevo** (frescura→scoring) para un run limpio con decay corregido,
  y comparar el evolutivo limpio vs este (sucio).
- 🟡 Revisar `IQQR.DE` en `etf_universe.yaml` (ticker delisted).
- 🟡 Considerar descontar/penalizar indicadores stale en `intensity_engine` (hoy se promedian a valor pleno).
- 🟢 Vigilar inventario LME (<200kt) y decisión de aranceles de cobre (fin de junio) para la thesis copper.
