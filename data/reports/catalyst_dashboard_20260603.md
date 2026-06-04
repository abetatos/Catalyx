# CATALYX — Catalyst Dashboard
**Report type:** catalyst_dashboard
**Period:** 2026-06-03
**Generated:** 2026-06-03T00:00:00Z (v2 — updated after event catalyst registration + user_rank assignment)
**Event catalysts active:** 1 · **Structural catalysts active:** 5

> Ranked by `display_priority = intensity_score × user_rank_multiplier`.
> Indicators: 🟢 strong (above threshold_strong) · 🟡 monitoring · 🔴 alert (below threshold_weak)
> **Changes vs v1 (same day):** `cat_20260603_nato_defense_gdp` now registered · `struct_copper_datacenter_demand` promoted to user_rank 2 (priority 76→91.2) · Ranking #4/#5 swapped

---

## Structural Catalysts

### 1. Central bank systematic gold accumulation
`struct_cb_gold_accumulation` · `institutional_flow / central_bank_reserve_shift` · Onset: 2022-Q3 · Geography: GLOBAL, CHN, IND, TUR, POL, KAZ

**Intensity:** 84/100 · Trend: ↑ +5 vs Q1 · Display priority: **117.6** (84 × 1.40, user_rank 1)

```
Intensity history:
2025-Q2  80  ████████░░
2025-Q3  76  ███████░░░  Turkey slowed temporarily
2025-Q4  82  ████████░░  India joins top-5 buyers
2026-Q1  79  ███████░░░  China +34T, India +15T, Poland +10T
current  84  ████████░░  WGC Q2 data pending
```

**Thesis:** Los bancos centrales de economías emergentes están rotando reservas fuera del USD de forma sistemática y sostenida desde 2022 — la demanda soberana de oro es price-insensitive y no reversible rápidamente. No está modelada como base case en los modelos sell-side.

**Indicators**

| # | Indicator | Current | vs Prior | Status | Next check |
|---|---|---|---|---|---|
| ind_01 | WGC CB net purchases (T/quarter) | 290T | ↑ +22T | 🟢 | 2026-07-15 |
| ind_02 | IMF COFER non-USD reserve share | 0.582 | ↑ −0.009 (improving) | 🟡 | 2026-07-01 |
| ind_03 | Gold ETF AUM change (monthly) | +3.4% | ↑ +0.6pp | 🟢 | 2026-07-01 |

> **ind_02:** 0.582 está entre threshold_strong (0.58) y threshold_weak (0.62) — en zona amarilla por 0.002. La dirección es correcta (bajando = dedollarización avanzando). Vigilar Q3 COFER release (~octubre).

**Sectors directly impacted**

| Sector | Alignment rationale |
|---|---|
| `gold_physical` | Demanda soberana price-insensitive → suelo estructural al precio del oro |
| `gold_miners` | Apalancamiento operativo sobre precio del oro; misma señal con más beta |
| `silver_physical` | Correlación monetaria con oro; activación cuando ratio Au/Ag revierte |
| `royalty_streaming_metals` | Exposición a oro sin riesgo operacional de mina |

**Deactivation risk:** 🟢 LOW — CB purchases 290T vs 80T deactivation threshold. Margen amplio.

**User notes:** Confirmed by WGC quarterly data. CB demand: sovereign-driven, price-insensitive, not quickly reversible. Not in analyst base case — modeled as tail risk.

---

### 2. AI infrastructure capex supercycle
`struct_ai_capex_supercycle` · `technology_adoption / ai_inference_buildout` · Onset: 2023-Q1 · Geography: US, EU, APAC, GLOBAL

**Intensity:** 89/100 · Trend: ↑↑ consistente (→ flat en último período) · Display priority: **106.8** (89 × 1.20, user_rank 2)

```
Intensity history:
2025-Q3  82  ████████░░  Sovereign AI: UAE, Saudi, France
2025-Q4  85  ████████▓░  DeepSeek scare → quick recovery
2026-Q1  89  ████████▓░  MSFT $80B, GOOG $75B, META $63B, AMZN $100B+
current  89  ████████▓░  → flat
```

**Thesis:** Los hyperscalers están en un ciclo capex multi-año demand-inelástico — construir o perder ventaja competitiva. ~$350B guiado para 2026, creciendo 20-30% anual hasta 2028. La infraestructura física (data centers, grid, refrigeración, cobre) es el cuello de botella, no el modelo de IA en sí.

**Indicators**

| # | Indicator | Current | vs Prior | Status | Next check |
|---|---|---|---|---|---|
| ind_01 | Hyperscaler capex combinado ($B/quarter) | $87B | ↑ +$5B | 🟢 | 2026-07-30 |
| ind_02 | Nvidia DC revenue growth (YoY) | +22% | ↓ −6pp | 🟢 | 2026-08-28 |
| ind_03 | Global DC power demand growth IEA (YoY) | +28% | ↑ +4pp | 🟢 | ⚠️ OVERDUE (due 2026-06-01) |

> **ind_02:** Desaceleración +28%→+22% es normalización esperada desde base alta 2025. threshold_weak = 0% — distancia amplia. Vigilar caída bajo +15% (threshold_strong).
> **ind_03:** 2 días past due. IEA Electricity 2026 mid-year update aún no publicado. Valor actual (+28%) sigue siendo best estimate. Actualizar cuando IEA publique (previsión: 2026-06-15).

**Sectors directly impacted**

| Sector | Alignment rationale |
|---|---|
| `ai_infrastructure` | Beneficiario directo: construcción DC, cooling, racking, networking |
| `semiconductors_design` | GPU/ASIC/HBM demand — más priced-in, más crowded |
| `grid_infrastructure_utilities` | DCs consumen 30-50MW cada uno → demanda grid estructural |
| `nuclear_energy` | Baseload 24/7 para data centers (secondary) |
| `copper_miners` | Ver `struct_copper_datacenter_demand` (expresión commodity de este driver) |

**Deactivation risk:** 🟢 LOW — Necesitaría caída >30% capex guiado YoY durante 2 trimestres. Q1 2026 superó expectativas.

**User notes:** Catalizador más visible del mercado. Riesgo de estar priced-in en semiconductores de diseño. Focus en plays adyacentes less crowded: grid, refrigeración, networking, cobre.

---

### 3. NATO sustained multi-year rearmament cycle
`struct_nato_rearmament` · `fiscal_policy / defense_spending` · Onset: 2022-Q1 · Geography: EU, DEU, FRA, POL, GBR, ITA, SWE, FIN

**Intensity:** 88/100 · Trend: ↑↑ (78→82→88 over 3 periods) · Display priority: **105.6** (88 × 1.20, user_rank 2)

```
Intensity history:
2025-Q4  78  ███████░░░  Broad commitment sustained, ITA lagging
2026-Q1  82  ████████░░  DEU €30B supplementary, POL 4% GDP
2026-Q2  88  ████████▓░  NATO 3.5% formal commitment — EVENT CATALYST CONFIRMED
current  88  ████████▓░  → flat
```

**Thesis:** Los compromisos presupuestarios parlamentarios crean order books de 3-5 años para prime contractors europeos. La estructura política es resiliente a ciclos electorales. El NATO 3.5% GDP (hoy formalizado) es un acelerador del trend estructural iniciado en 2022.

**Indicators**

| # | Indicator | Current | vs Prior | Status | Next check |
|---|---|---|---|---|---|
| ind_01 | NATO avg defense spending (%GDP) | 2.34% | ↑ +0.10pp | 🟢 | ⚠️ OVERDUE (due 2026-06-01) |
| ind_02 | Order book growth RHM+LEO+ADS (YoY) | +19% | ↓ −3pp | 🟢 | 2026-08-15 |
| ind_03 | EU defense ETF AUM flows (monthly) | +4.1% | ↑ +0.7pp | 🟢 | 2026-07-01 |

> **ind_01:** Due for quarterly refresh (NDPP data, tipicamente publicado mid-June). 2.34% muy por encima de threshold_strong 2.20%. Actualizar en cuanto salga.
> **ind_02:** Ligera desaceleración (+22%→+19%) en order book growth — ruido trimestral. Absoluto sigue fuerte.
> **Linked event catalyst:** `cat_20260603_nato_defense_gdp` (strength: 91, detected today, 0% decay) CONFIRMS this structural. ⚠️ Current additive formula underscores this sector — see Alerts.

**Sectors directly impacted**

| Sector | Alignment rationale |
|---|---|
| `eu_defense_prime_contractors` | Primary: Rheinmetall, Leonardo, Airbus Defence, KNDS — order books multi-año |
| `space_defense_satellite` | Doctrina NATO: proliferación satélites militares |
| `drone_autonomous_systems` | Lección ucraniana: drones como vector de conflicto moderno |
| `cybersecurity` | Gasto defensa digital crece en paralelo al físico |

**Deactivation risk:** 🟢 LOW — Necesita ceasefire Rusia-Ucrania + revisión formal objetivos NATO, O bien 3+ miembros mayores recortando >15%. Nada en el horizonte visible.

**User notes:** Primera pata del trade ya ejecutada. Monitorizando segunda pata. El evento 3.5% refuerza order books — el structural es el floor, el event es el spike.

---

### 4. Copper demand from AI hyperscale data center buildout
`struct_copper_datacenter_demand` · `technology_adoption / ai_inference_buildout` · Onset: 2024-Q2 · Geography: US, EU, ARE, SAU, GLOBAL

**Intensity:** 76/100 · Trend: ↑ recuperando (68→76 vs Q4) · Display priority: **91.2** (76 × 1.20, user_rank 2) — **promoted from #5 to #4 after user_rank assignment**

```
Intensity history:
2025-Q2  60  ██████░░░░  Emerging thesis, not yet in consensus
2025-Q3  71  ███████░░░  First WoodMac/MS reports quantifying DC copper demand
2025-Q4  68  ██████░░░░  DeepSeek scare → recovered
2026-Q1  76  ███████░░░  $350B+ capex guide. Copper $10,200. LME stock: 98k T.
current  76  ███████░░░  → flat
```

**Thesis:** Los data centers hyperscale son una fuente nueva y subestimada de demanda de cobre — un DC de 100MW+ requiere 20,000-40,000T de cobre. La demanda estimada para 2028 equivale al ~7% de la producción minera global 2025. El mercado sigue valorando el cobre como "metal del EV" — el ángulo data center no está en los modelos consensus. Ventana de mispricing.

**Indicators**

| # | Indicator | Current | vs Prior | Status | Next check |
|---|---|---|---|---|---|
| ind_01 | Hyperscaler AI capex guide ($B/quarter) | $87B | ↑ +$5B | 🟢 | 2026-07-30 |
| ind_02 | LME copper spot (USD/tonne) | $10,200 | ↑ +$400 | 🟢 | 2026-07-01 |
| ind_03 | LME warehouse inventory (tonnes) | 98,000T | ↓ −14,000T | 🟢 | 2026-07-01 |
| ind_04 | DC copper demand estimate (T/year, WoodMac/CRU) | 1,200,000T | ↑ +200,000T | 🟢 | ⚠️ OVERDUE (due 2026-06-01) |

> **ind_03:** `lower_is_stronger` — 98k T muy por debajo de threshold_strong 150k T. Inventory draw acelerándose.
> **ind_04:** WoodMac/CRU mid-2026 update pending. 1.2M T/year (Mar 2026) sigue siendo best estimate.

**Sectors directly impacted**

| Sector | Alignment rationale |
|---|---|
| `copper_miners` | Primary equity expression — leveraged demand play, re-rating when DC angle enters consensus |
| `copper_physical` | Pure commodity expression for this catalyst |

**Linked structural:** `struct_ai_capex_supercycle` — esta es la expresión commodity del driver principal.

**Deactivation risk:** 🟢 LOW — Todos los indicadores en verde. Riesgo principal medium-term: arquitecturas DC migran a interconexiones ópticas a escala.

**User notes:** Alpha angle: market frames copper as EV metal. DC demand is additive and not in consensus. First to model it = re-rating event.

---

### 5. Grid infrastructure as the binding constraint of energy transition
`struct_energy_transition_grid` · `climate_transition / grid_electrification` · Onset: 2023-Q2 · Geography: US, EU, GLOBAL

**Intensity:** 82/100 · Trend: ↑ (79→82 vs Q4) · Display priority: **82.0** (82 × 1.00, user_rank 3) — **dropped from #4 to #5 after copper_datacenter user_rank update**

```
Intensity history:
2025-Q4  79  ███████░░░  US FERC permitting reform passed
2026-Q1  82  ████████░░  Lead times: 20M. EU €584B grid commitment through 2030.
current  82  ████████░░  → flat
```

**Thesis:** La transición energética y el buildout AI colisionan en el grid. Generación renovable se construye más rápido de lo que la red puede absorberla; data centers añaden carga más rápido de lo que crece la capacidad. Lead times de 10-15 años para infraestructura de transmisión. El cobre es el input físico crítico.

**Indicators**

| # | Indicator | Current | vs Prior | Status | Next check |
|---|---|---|---|---|---|
| ind_01 | Power transformer delivery lead times | 20 months | ↓ −2M | 🟢 | ⚠️ OVERDUE (due 2026-06-01) |
| ind_02 | EU grid investment announced (€B/year) | €96B | ↑ +€8B | 🟢 | ⚠️ OVERDUE (due 2026-06-01) |
| ind_03 | LME copper spot (USD/tonne) | $10,200 | ↑ +$400 | 🟢 | 2026-07-01 |

> **ind_01:** Ligera reducción (22M→20M) — podría indicar normalización gradual. Vigilar si cae bajo 12M (threshold_strong). Aún en verde profundo.
> **ind_01 + ind_02:** Ambos 2 días past due. WoodMac transformer survey y ENTSO-E investment report tipicamente publicados en junio. Actualizar antes de 2026-06-15.

**Sectors directly impacted**

| Sector | Alignment rationale |
|---|---|
| `grid_infrastructure_utilities` | Primary: transformadores, cables HVDC, gestión de red (GRID, INFR.L) |
| `copper_miners` | Primary: cables de transmisión >95% cobre |
| `nuclear_energy` | Secondary: baseload cuando la red no puede absorber variabilidad renovable |

**Deactivation risk:** 🟡 MODERATE — Lead times bajando gradualmente. Si caen bajo 12M dos trimestres consecutivos, cuello de botella cediendo. Aún a 8 meses de distancia del threshold.

**User notes:** Play menos crowded que el AI puro. La expresión commodity es cobre; la expresión equity regulada es utilities de grid.

---

## Event Catalysts

### 1. NATO 3.5% GDP defense spending commitment
`cat_20260603_nato_defense_gdp` · `fiscal_policy / defense_spending` · Detected: 2026-06-03

**Strength:** 91/100 · Days active: 0 · Remaining relevance: **91/100 (100%)** — half-life: 90d
**Display priority:** 109.2 (strength × 1.20, user_rank 2)

**Description:** NATO member states formally commit to a 3.5% GDP defense spending floor by 2028, replacing the prior 2% target. Binding commitment enforced via NATO Defence Planning Process (NDPP). DEU, FRA, POL, GBR already passed or announced supplementary budgets. Novelty score 72 — the 2% target was known; the step-change to 3.5% with binding timeline is the novel element.

**Sectors impacted:** `eu_defense_prime_contractors` (primary), `us_defense` (secondary), `cybersecurity` (secondary)

**Priced-in estimate:** 25%

**Linked structural:** `struct_nato_rearmament` — this event CONFIRMS the structural trend. Not independent new information — it accelerates existing procurement.

**Decay projection:**
```
Today   (0d):   91.0  ████████████████████  100%
+30d (Jul 03):  72.3  ████████████████░░░░   79%
+60d (Aug 02):  57.4  ████████████░░░░░░░░   63%
+90d (Sep 01):  45.5  █████████░░░░░░░░░░░   50%
+180d (Dec 01): 22.8  ████░░░░░░░░░░░░░░░░   25%
```

---

## Alerts

### 🔴 Critical
Ninguna.

---

### 🟡 Monitoring

**1. struct_cb_gold_accumulation / ind_02 — IMF COFER borderline amarillo**
Actual: 0.582 vs threshold_strong 0.58 — solo 0.002 sobre el umbral verde. Dirección correcta (−0.009 vs prior). Sin acción requerida; revisar en Q3 COFER release (~octubre 2026).

**2. [Design gap] Interacción structural × event NO CAPTURADA — cat_20260603 CONFIRMA struct_nato**
La fórmula actual trata los catalizadores como aditivos e independientes:
`catalyst_alignment = structural × 0.45 + event × 0.55`
Para `eu_defense_prime_contractors`: 88×0.45 + 91×0.55 = **89.6/100**
Pero este evento CONFIRMA y ACELERA el structural — no aporta información independiente. Una fórmula multiplicativa daría ~88 × 1.10 = **96.8** (con amplificador de confirmación). La fórmula actual subestima el sector. **Pendiente: TEST 8 para propuesta de fórmula revisada.**

**3. Cinco indicadores trimestrales overdue (2 días past due date)**
Todos debido el 2026-06-01. Reportes fuente aún no publicados. Sin preocupación de calidad de datos. Actualizar antes de 2026-06-15.
- `struct_ai_capex_supercycle` / ind_03 (IEA DC power demand, due 2026-06-01)
- `struct_nato_rearmament` / ind_01 (NATO avg %GDP, due 2026-06-01)
- `struct_energy_transition_grid` / ind_01 (transformer lead times, due 2026-06-01)
- `struct_energy_transition_grid` / ind_02 (EU grid investment, due 2026-06-01)
- `struct_copper_datacenter_demand` / ind_04 (DC copper demand estimate, due 2026-06-01)

**4. struct_energy_transition_grid / ind_01 — transformer lead times en tendencia descendente**
22M → 20M. Aún muy por encima de threshold_strong (12M). Vigilar si la tendencia continúa hacia 12M — señalaría que el cuello de botella está cediendo.

---

## Changes vs Prior Report (v1 → v2, same day)

| Catalyst | Change | Detail |
|---|---|---|
| `cat_20260603_nato_defense_gdp` | ADDED | Catalizador de evento registrado en data/catalysts/. Strength 91, 90d half-life. |
| `struct_copper_datacenter_demand` | PROMOTED | user_rank asignado: 2. Display priority 76.0 → **91.2**. Sube de #5 a #4 en ranking. |
| `struct_energy_transition_grid` | DEMOTED | Desplazado a #5 por copper_datacenter promotion. Sin cambio en intensity o datos. |
| Dashboard alert added | DESIGN GAP | Structural × event interaction not captured in formula — flagged for TEST 8. |

---

## Next Review Dates

| Catalyst | Indicator | Due | Source |
|---|---|---|---|
| ⚠️ `struct_ai_capex_supercycle` | ind_03 IEA DC power demand | OVERDUE | IEA Electricity 2026 mid-year |
| ⚠️ `struct_nato_rearmament` | ind_01 NATO avg %GDP | OVERDUE | NATO NDPP mid-year data |
| ⚠️ `struct_energy_transition_grid` | ind_01 Transformer lead times | OVERDUE | WoodMac / S&P Global Q2 |
| ⚠️ `struct_energy_transition_grid` | ind_02 EU grid investment | OVERDUE | ENTSO-E + EC report |
| ⚠️ `struct_copper_datacenter_demand` | ind_04 DC copper demand estimate | OVERDUE | WoodMac / CRU Group Q2 |
| `struct_cb_gold_accumulation` | ind_02 IMF COFER | 2026-07-01 | IMF COFER quarterly |
| `struct_cb_gold_accumulation` | ind_03 Gold ETF AUM | 2026-07-01 | iShares + WGC monthly |
| `struct_energy_transition_grid` | ind_03 Copper spot | 2026-07-01 | LME daily |
| `struct_copper_datacenter_demand` | ind_02 Copper spot | 2026-07-01 | LME daily |
| `struct_copper_datacenter_demand` | ind_03 LME inventory | 2026-07-01 | LME daily |
| `struct_nato_rearmament` | ind_03 EU defense ETF flows | 2026-07-01 | iShares + VanEck |
| `struct_cb_gold_accumulation` | ind_01 WGC CB purchases | 2026-07-15 | WGC Gold Demand Trends Q2 |
| `struct_ai_capex_supercycle` | ind_01 Hyperscaler capex guide | 2026-07-30 | Q2 2026 earnings season |
| `struct_copper_datacenter_demand` | ind_01 Hyperscaler capex guide | 2026-07-30 | Q2 2026 earnings season |
| `struct_nato_rearmament` | ind_02 Order book growth | 2026-08-15 | RHM, LEO, ADS Q2 earnings |
| `struct_ai_capex_supercycle` | ind_02 Nvidia DC revenue | 2026-08-28 | Nvidia Q2 FY2026 |

---

*Template: [catalyst_dashboard_template.md](../../docs/report_templates/catalyst_dashboard_template.md)*
*Próximo reporte completo: 2026-07-07 (primer lunes de julio) o tras actualización de indicador.*
*5 indicadores overdue — actualizar antes del 2026-06-15.*
