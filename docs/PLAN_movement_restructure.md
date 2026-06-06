# PLAN — Restructuración: de *Thesis* a *Movement*

> Estado: **Fase 0 en curso** (2026-06-06). Cambio de eje conceptual aprobado por el usuario.
> Decisión pendiente: forma de la disciplina falsable (ver §7) → bloquea el schema `movement.json`.

---

## 0. Resumen en una frase

El catalizador deja de ser solo un *input* del scoring y pasa a ser la **unidad de cuenta del
track record**. La unidad operativa mínima ya no es una `Thesis` falsable pesada y obligatoria,
sino un **`Movement`**: un movimiento de capital (€X) atribuido a uno o varios catalizadores,
con un gatillo y una convicción. El `Catalyst` acumula P&L a lo largo del tiempo → sabemos
**qué catalizadores ganan**. Cada movimiento congela el estado del sistema en ese instante
(`score_context`) → habilita backtest honesto sin look-ahead.

```
ANTES:  Catalyst ──> Thesis (doc pesado, obligatorio) ──> Trade (referencia thesis)
                     validación: ClosedThesis + right_reason_score

AHORA:  Catalyst <──atribución── Movement (€X, fecha, convicción, gatillo, score_context)
                                      │
                                      └─> Position (neto, derivado, mark-to-market)
        El Catalyst acumula P&L  ──>  Catalyst Ledger = "qué catalizadores ganan"
```

## 1. Por qué este cambio

- El diferencial del proyecto es *"¿acerté, y por las razones correctas?"*. Esa pregunta se
  responde por **catalizador**, no por tesis. Atribuir el capital directamente al catalizador
  hace que el track record por catalizador sea un derivado, no un esfuerzo aparte.
- La `Thesis` obligatoria es ceremonia para un trader de momentum con revisión mensual. La
  ceremonia debe **escalar con la convicción**, no aplicarse a cada €100.
- `trade_logger` ya hace el ~80% de un `Movement` (qty/price/fees/EUR/divisa, linaje `run_id`,
  neto a posiciones + P&L realizado). El cambio es **re-apuntar la atribución `thesis_id` →
  `catalyst_ids[]`** y añadir variables. Es **evolución, no rewrite**.

## 2. El objeto `Movement`

```jsonc
{
  "id": "mov_20260606_copper_miners_dcdemand",
  "executed_at": "2026-06-06T10:00:00Z",
  "action": "open",            // open | add | trim | close
  "sector_id": "copper_miners",
  "vehicle": { "etf": "4COP", "isin": "IE0003Z9E2Y3", "currency": "USD" },
  "amount_eur": 100.0, "qty": 1.1, "price": 90.4, "fees": 1.0,

  // ── CAMBIO CLAVE: atribución directa al catalizador (no a una thesis) ──
  // weights suman 1.0; el ledger reparte el P&L del movement por weight (no doble-cuenta)
  "attribution": [
    { "catalyst_id": "struct_copper_datacenter_demand", "weight": 0.7 },
    { "catalyst_id": "cat_20260603_copper_supply_deficit_2026", "weight": 0.3 }
  ],

  // ── por qué te moviste y con cuánta convicción ──
  "trigger": "new_catalyst",   // new_catalyst | escalation | contradiction
                               // | reconsideration ("me lo pensé mejor")
                               // | rebalance | stop_hit | profit_take
  "conviction": "medium",      // small | medium | high

  // ── VARIABLE DE ORO PARA BACKTEST: foto point-in-time del sistema ──
  "score_context": {
    "run_id": "run_20260606_110720", "rank": 3,
    "composite": 84.1, "catalyst_alignment": 88.0, "momentum": 71.0,
    "flow": 55.0, "crowding": "low", "regime_state": "intact"
  },

  "rationale": null,           // §7 — forma pendiente de decisión (opciones 1/2/3)
  "run_id": "run_20260606_110720"
}
```

`action × trigger × conviction` cubre los tres casos del usuario:
- *"actualizar por un catalizador pequeño"* → `action: add, trigger: escalation, conviction: small`
- *"por uno más grande"* → `action: add, trigger: escalation, conviction: high`
- *"abrir porque me lo he pensado mejor"* → `action: open, trigger: reconsideration, conviction: medium`

**`score_context`** es la pieza nueva crítica: congela *qué sabía el sistema al mover*. Sin esto,
reconstruirlo después es look-ahead. Con esto: *"cuando moví sobre rank #3 / catalyst_alignment 88
/ regime intact, ¿pagó más a menudo que sobre #15 contested?"*.

## 3. Objetos derivados (no se escriben a mano)

| Objeto | Cómo se calcula | Responde a |
|---|---|---|
| **Position** | neto de movements por vehículo + mark-to-market (yfinance) | exposición viva, P&L no realizado, días en posición, MFE/MAE |
| **Catalyst Ledger** | P&L de cada movement × su `weight`, sumado por catalyst | **qué catalizadores han ganado** (hit rate, exposición, hold medio) |

## 4. Fases (cada una entrega un informe que funciona)

### Fase 0 — Spec + higiene *(sin cambio de comportamiento)*
- [x] Borrar la partición sentinela `run_20991231` del lake de dislocation.
- [x] Persistir este plan (`docs/PLAN_movement_restructure.md`).
- [x] Cerrar `schemas/movement.json` — **decisión §7 = opción 1** (risk_discipline opcional, chequeable).
- [x] Position + CatalystLedger definidos como derivados (vistas, no schema propio — ver §3).

### Fase 1 — `Movement` de primera clase *(evolucionar `trade_logger`)*
- `trade_logger` → `movement_logger`: añadir `attribution[]`, `trigger`, `conviction`, y captura
  automática de `score_context` (lee el último `score_run` del sector al ejecutar).
- Lake: tabla `movement` (vía `union_by_name`; los `portfolio_trade` viejos se leen atrás).
- Migrar las 2 tesis abiertas → movements de apertura atribuidos a sus catalizadores; el JSON de
  la tesis queda como `rationale` (según §7).
- Position view = `real_holdings` extendido con P&L no realizado + holding_days + atribución.

### Fase 2 — Catalyst Ledger + informe de métricas
- `catalyx/attribution/catalyst_ledger.py` → lake `catalyst_performance`: P&L (realizado +
  no realizado), hit rate, exposición, hold medio por catalizador.
- `return_decomposer` al cerrar (sector_beta vs catalyst vs idiosincrático) → lake `validation/`.
- Informe: win rate por catalizador / sector / `trigger` / `conviction` → `data/reports/`.
- Dashboard: sección **"Catalyst track record"** (reutiliza infra de Portfolios).

### Fase 3 — Contrafactual: "qué podríamos haber hecho"
- Por cada `score_run`, comparar movements reales vs top-N del heatmap (las carteras modelo ya
  son ese baseline). Informe de *regret*: el ranking dijo mover y no lo hiciste, o al revés.

### Fase 4 — Harness de backtest reutilizable *(no-look-ahead estricto)*
- Walk-forward sobre `score_run`: ¿predicen `catalyst_alignment`/`composite` el retorno forward
  de los movements? Valida los pesos del composite contra resultados **reales**, no sintéticos.

### Fase 5 — Rebalance simulator (consciente de coste **y** fiscalidad)
**Motivación:** `nav_engine.holdings_nav` mide el NAV de un único snapshot buy-and-hold, sin
fricción ni rebalanceo → alfa **bruto**, un techo teórico. El sistema real rebalanceará con cada
`score_run`; hay que medir cuánto de ese alfa **sobrevive a costes e impuestos** y si rebalancear
tan a menudo compensa.

**Perfil de coste del usuario (decidido):** Revolut Metal/Ultra (10+ trades gratis/mes), ETFs
UCITS en EUR. Consecuencias por defecto del modelo: comisión ≈ 0 casi siempre (cabe en franquicia;
0,25% solo en patas que excedan 10/mes); **FX = 0** (sin conversión EUR↔USD — ojo: cierto para el
*coste*, el retorno de un ETF USD-denominado como 4COP sigue llevando FX); el coste de transacción
dominante es el **spread bid/ask** de los ETFs sectoriales granulares (10–50 bps; aviso >25 bps);
el drag total dominante es **fiscal** (cada venta para rotar cristaliza CGT progresivo 19/21/23/27%
que el buy-and-hold diferiría y dejaría componiendo).

**Construir** `catalyx/execution/rebalance_simulator.py` (contrato `uv run python -m catalyx.*`,
network-free leyendo el lake):
- Recorrer la secuencia de `score_run`; **colapsar a una rejilla de cadencia** (1/día o 1/semana)
  ANTES de nada — el lake tiene re-runs intradía (3 el 06-05) que falsearían el turnover.
- En cada paso reconstruir el portfolio (`portfolio.build_model_holdings(run_id=...)` — ya es
  point-in-time en la selección) y calcular turnover vs holdings previos:
  `τ_t = Σ|w_new − w_old|/2`, nocional `= 2·τ_t·V`.
- **Precios point-in-time:** tomar precio de la partición de **momentum as-of cada run**, NO la
  última (la fuga de look-ahead conocida de `_etf_prices`).
- Descontar costes desde `config/execution_costs.yaml` (nuevo: `free_trades_per_month`,
  `commission_pct=0.0025`, `commission_min_eur=1`, `fx_pct`, `half_spread_bps` por defecto +
  override por ETF) y el CGT sobre la plusvalía realizada vía `tax_engine.compute_tax`.
- **Lotes FIFO propios:** `tax_engine` recibe una plusvalía agregada y usa coste-medio aguas
  arriba — el simulador debe llevar sus **propios lotes FIFO** para la ganancia por venta (España
  es FIFO; coste-medio ≠ FIFO). Modelar la **regla de los dos meses** (pérdida no deducible si se
  recompra el mismo ETF en <2 meses) — sin ella el harvesting de pérdidas queda sobreestimado.

**Entregable — 4 curvas (no 3) vs SPY:** rebalanceado-bruto / neto-coste / neto-coste-fiscal +
**buy-and-hold de referencia**. La pregunta que responde: *¿el neto-de-todo sigue batiendo al
buy-and-hold?* (¿sobrevive la rotación activa a la fricción frente a quedarse quieto?). Métricas:
turnover acumulado, € comisiones, € spread, € impuestos, nº trades, **effective drag %/año**, con
descomposición **auditable por-trade**.

**A/B de cadencia:** cada-run(dedup) vs banda-de-no-trade vs trimestral vs anual.

**Banda de no-trade — depende de Fase 4.** La regla "rotar solo si `alfa_esperado_rotación >
spread_round_trip + tax_adelantado`" compara un **Δscore adimensional** con **€**: necesita el
mapeo `Δscore → Δretorno_€` que produce el harness de Fase 4. **Secuencia:** entregar primero el
simulador como **A/B de cadencia puro**; añadir la banda cuando Fase 4 calibre el umbral en € (si
no, se calibra in-sample = overfit).

**Persistir** en tabla nueva del lake (append-only) → dashboard muestra bruto-vs-neto.
**Integración:** reusa `tax_engine`, `trade_logger` (fees), `portfolio.build_model_holdings`,
`nav_engine` (math NAV). Tests con `price_fn` sintético (sin red). Al construir: actualizar
CLAUDE.md (tabla de módulos + Recent Changes).

**Criterio de aceptación (revisado):** el comando emite las 4 curvas + el A/B y una descomposición
por-trade reproducible (comisión / spread / fiscal). El titular es el **desglose spread-vs-fiscal**
— NO un "gana la fiscal" prefijado (con comisión≈0 por franquicia, "fiscal > comisión" es trivial;
el contraste informativo es **fiscal vs spread**, y si (3) bate a (4)).

### Fase 6 — Feedback al scoring (ML sobre movement+catalyst)
- `prior_repo`: hit rate bayesiano por par catalyst-sector desde el ledger. Filas de
  entrenamiento **reales** (movements), no tesis hipotéticas. Offline, sin LLM.

## 5. Qué se reutiliza / degrada / muere

| | Componente |
|---|---|
| ✅ **Reutiliza** | `trade_logger`→`movement_logger`, `nav_engine`, `tax_engine`, `lake`/`lake_query`, sección Portfolios del dashboard, `score_run` history, carteras modelo (→ baseline del contrafactual) |
| ⬇️ **Degrada** | `Thesis` (doc obligatorio → `rationale` opcional); `ClosedThesis` → se pliega en `close` + `return_decomposer` |
| ❌ **Elimina** | la obligación de escribir una tesis completa antes de cualquier posición; partición sentinela del lake |

## 6. Trazabilidad con lo que pidió el usuario

| Petición | Dónde vive |
|---|---|
| "pongo €100 por este catalizador" | `Movement.amount_eur` + `attribution[]` + `conviction` |
| "catalizadores que han sido ganadores" | Fase 2 — `catalyst_ledger` |
| "más variables para backtest a futuro" | `score_context` point-in-time + MFE/MAE + holding_days + decomposición |
| "actualizar por pequeño/grande, o abrir por pensarlo mejor" | `action` × `trigger` × `conviction` |
| "informes de métricas a futuro" | Fase 2 (track record) + Fase 3 (contrafactual) |
| "evolución sostenida en el tiempo" | fases independientes; cada una entrega un informe y reutiliza infra |

## 7. DECISIÓN — disciplina falsable → **opción 1 (resuelta 2026-06-06)**

Las `assumptions` (lo que debe seguir siendo cierto) y `invalidation_conditions` (stop
pre-comprometido) de la `Thesis` son lo único valioso que se pierde al pasar a movements ligeros.
¿Dónde van?

| Opción | Dónde vive | El sistema puede… | Coste |
|---|---|---|---|
| **1. Opcional en la Position** *(recomendada)* | campos estructurados (`stop_price`, `assumption{source}`) en la posición viva | auto-vigilar y avisar (`invalidation_watcher`); alimenta `right_reason_score` | rellenar 2-3 campos en moves core |
| **2. Eliminar** | en ningún sitio | nada | pierdes stop automatizado + el input de "razón correcta" |
| **3. Bloque rationale rico** | documento opcional adjunto al movement | mostrarlo, pero es texto no chequeable | sigue siendo escribir mini-tesis a mano |

**Elegida: opción 1** (usuario, 2026-06-06) — convierte el stop en alerta automática y mantiene
vivo `right_reason_score` sobre datos reales, sin obligar a escribirlo en los moves pequeños.
Implementada en `schemas/movement.json` como el bloque opcional `risk_discipline`
(`invalidation[]` + `assumptions[]`, misma forma que la `Thesis` legacy → migración limpia).
