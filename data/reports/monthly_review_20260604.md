# CATALYX — Monthly Review 2026-06-04

## 0. Macro & Geopolitical Context

- **Iran war / Strait of Hormuz crisis is the dominant macro driver.** US-Israel air war on Iran since 2026-02-28 (Khamenei assassinated); Hormuz largely blocked by IRGC mines/attacks. Brent ~$120, WTI ~$107. Analysts drawing 1970s oil-shock parallels. Only ~3 laden VLCCs transited Hormuz in the last 7 days vs ~16 normal.
- **Fed:** on hold at 3.50–3.75% (June 16–17 FOMC); <10% odds of any cut this year. Energy-driven inflation keeps it hawkish-by-default. Unemployment ~4.4%.
- **ECB:** expected to **HIKE** +25bps on June 11 → 2.25% deposit rate, with another priced by year-end. War-driven energy inflation (2026 HICP ~2.6%). **Delta vs stored data:** project YAMLs assumed benign rates; a hiking ECB is a fresh headwind for rate-sensitive grid utilities (mitigated — see grid thesis asm_04 references the Fed, not ECB).
- **Copper:** LME spot ~$13,965/t (Jun 2), 3M ~$13,825 (Jun 4). **LME warehouse stock 393,400t (May 20) with net inflows** — *not* the ~98kt multi-year-low the YAML claimed. Chile weakest April output in 23 years (supply story); S&P analysts call copper "overextended."
- **Gold:** ~$4,529/oz. WGC: ~755t CB purchases expected 2026; 95% of CBs (record) expect reserves to grow. ETF/miner flows soft despite high spot.
- **Hyperscaler capex:** $725B guided for 2026 (+77% YoY), ~$450B AI-specific. **Power, not chips, is the binding constraint** — Microsoft disclosed an $80B Azure backlog unfulfillable due to power.
- **Russia/Ukraine:** ceasefire unlikely (≈9.5% by Jun 30); diplomacy stalled.

**Flagged indicator discrepancies:** copper LME inventory 98kt → **393kt** (corrected this cycle); hyperscaler capex $700B → $725B (minor).

## Executive Summary
- 🔴 **Copper thesis assumption invalidated.** `thesis_…copper_miners_datacenter_alpha` asm_02 requires LME inventory < 200kt (4-wk avg); actual is 393kt and rising. The near-term supply-tightness pillar is broken. **Recommendation: Reduce.**
- 🟢 **Grid infrastructure is now the #1 structural catalyst (95.0)** and all 4 grid-thesis assumptions validate. The "power is the binding constraint" macro (MSFT $80B power-bound backlog) is the cleanest confirmation in the book. **Recommendation: Hold (Add candidate, correlation-permitting).**
- 🟡 **NON-OBVIOUS:** the copper inventory correction does double duty — it both invalidates copper asm_02 *and* resolves a portfolio over-concentration. The two open theses (grid 4% + copper 6% = 10%) jointly ride the AI→power→copper complex and breach the Tier-2 8% combined ceiling. Trimming copper to ~3–4% respects the broken assumption **and** brings the complex back under 8%.
- 🟡 **EU defense: right thesis, wrong tape.** Catalyst alignment 84 (NATO 5% Hague, DEU €117B) but momentum collapsed to 8.8 and ETF flows −5% in June. Strength without participation.
- 🟢 Gold catalyst is a *floor* (CB demand 76.1), not a spike — miner/ETF momentum is weak (14.7/2.9) despite $4,529 spot.

## 1. Catalyst Updates
- **Structural intensity recomputed** (intensity_engine v1.5, de-compression + corrected copper inventory):

| Catalyst | Prior | New | Δ | Trend |
|---|---|---|---|---|
| `struct_energy_transition_grid` | 95.0 | **95.0** | — | ↑↑ |
| `struct_ai_capex_supercycle` | 94.9 | **89.9** | −5.0 | → |
| `struct_copper_datacenter_demand` | 95.0 | **83.9** | **−11.1** | ↑↑ (ind_03 🔴) |
| `struct_cb_gold_accumulation` | 81.1 | **76.1** | −5.0 | ↓ |
| `struct_nato_rearmament` | 82.7 | **74.7** | −8.0 | ↓ |

- **No new CatalystEvent JSON created.** The Iran/Hormuz shock is already captured (`cat_20260228_hormuz_closure`, strength 94, priced_in 1.0). Pass-1 Discovery surfaced the VLCC tanker spike (feeds the existing — already-rejected — tanker gap) and the HBM/memory cycle (existing `semiconductors_memory` gap). No theme above strength 55 is uncovered.

## 2. Sector Studies Refreshed
All 6 existing studies are 1–2 days old (≤ 7-day freshness gate) → no mandatory re-run this cycle.
⚠️ `study_copper_miners` (2026-06-03) should be annotated for the LME-inventory correction at next refresh — its supply-tightness framing is now stale.

## 3. Catalyst Dashboard
→ `data/reports/catalyst_dashboard_20260604.md`. Headline change: grid overtakes AI capex as #1 structural; copper de-rated on inventory.

## 4. Sector Heatmap
→ `data/reports/heatmap_20260604.md` (refreshed, supersedes earlier same-day file).

| Rank | Sector | Composite | CA | Momentum |
|---|---|---|---|---|
| 1 | ai_infrastructure_data_centers | 75.9 | 96.9 | 91.2 |
| 2 | grid_infrastructure_utilities | 71.2 | 96.1 | 73.5 |
| 3 | copper_miners | 69.7 | 96.0 | 67.6 |
| 4 | eu_defense_prime_contractors | 51.4 | 84.0 | 8.8 |
| 5 | gold_miners | 50.5 | 76.1 | 14.7 |
| 6 | gold_physical | 47.6 | 76.1 | 2.9 |

Order vs prior month unchanged in the top 3; spread compressed.

## 5. Open Theses
| Thesis | Days open | Assumptions (ok/total) | Recommended action |
|---|---|---|---|
| `…grid_infrastructure_utilities_bindingconstraint` | 1 | 4/4 (asm_01 backlog ✓, asm_02 lead-times 20mo ✓, asm_03 capex $181B/q ✓, asm_04 Fed flat ✓) | **Hold** (Add candidate if correlation freed) |
| `…copper_miners_datacenter_alpha` | 1 | 2/4 (asm_01 capex ✓, asm_03 AISC ✓ at $13,965/t; **asm_02 inventory ✗ 393kt**; asm_04 China PMI unverified) | **Reduce** (cut 6%→~3–4%) |

## 6. Portfolio Correlation
| Open theses | Shared exposure | Combined % | Status |
|---|---|---|---|
| grid (4%) + copper (6%) | AI capex → power/copper complex; copper lists `energy_transition_grid` as a secondary structural; both anchored by grid catalyst (95.0) | **10%** | 🔴 **EXCEEDS Tier-2 8% ceiling** |

Primaries differ (grid: `energy_transition_grid`; copper: `copper_datacenter_demand`) but the two are highly collinear. Trimming copper to ~3–4% brings the complex to ≤8%.

## 7. Thesis Draft Decisions
- **`ai_infrastructure_data_centers` (#1, 75.9, no open thesis)** is the natural next draft, but it shares `struct_ai_capex_supercycle` with both existing theses → a new position compounds AI-complex concentration. **BLOCKED by correlation** until copper is reduced. Re-evaluate after the copper trim.
- `eu_defense` (#4): catalyst-strong but momentum 8.8 — **not ready** (await price participation). Defer.
- `gold_miners` (#5): weak momentum. Defer.

## 8. Tax Snapshot YTD
| Metric | Value |
|---|---|
| Closed theses (2026) | 0 |
| Realized gains | €0.00 |
| Tax paid | €0.00 |
| Current marginal bracket | 19% (first bracket, no realized gains) |
| Projected YTD if open positions close at mark | n/a — both theses opened this cycle, no mark history |

## 9. Stale Indicators
| Catalyst | Indicator | Last updated | Freq | Status |
|---|---|---|---|---|
| ai_capex / grid / nato / copper | IEA DC power (ind_03), transformer lead-times (ind_01), EU grid (ind_02), NATO %GDP (ind_01), DC copper demand (ind_04) | 2026-03-01 | quarterly | ⚠️ ~95 days — **due now** (refresh at next quarterly: WGC/earnings/IEA Q2 prints) |
| All monthly indicators | spot/flow/inventory | 2026-06-01 to 06-04 | monthly | ✓ current |

## 10. Watch-Only Triggers
| Sector | Triggers | Change |
|---|---|---|
| quantum_computing | >1M logical qubits / >$5B procurement | No change |
| nuclear_fusion | commercial net energy gain / >$2B fusion IPO | No change (energy-security narrative noted, trigger not met) |
| brain_computer_interface | FDA full approval / >$5B IPO | No change |
| advanced_materials_metamaterials | — | No change |

## 11. Taxonomy Gap Review
| Gap ID | Theme | Signals | First→Last | ETF | Status | Action |
|---|---|---|---|---|---|---|
| `gap_20260604_semiconductors_memory` | DRAM/HBM/NAND memory IDMs | 1 | 06-04→06-04 | **DRAM** (pure-play; inferred ticker 'MEMU' was wrong) | **PROMOTED** | User chose Promote (over default Defer). New sector `semiconductors_memory` added to taxonomy (v1.1→**v1.2**); ETFs DRAM/SMH/SEMI.L/EWY added to etf_universe; sector study written; gap status → promoted, promoted_date 2026-06-04. |
| `gap_20260604_tanker_shipping_freight` | Crude tanker freight (VLCC) | 1 | 06-04→06-04 | BWET | **rejected** | For record only — event-driven (Hormuz), reversible, 142% short interest. VLCC rates hit all-time records this cycle but remain a tactical event play, not a structural sector. |

**Promotion note:** `semiconductors_memory` is now investable. ✅ Sector study written (`study_semiconductors_memory.json`) — cycle position **mid-to-late expansion** (fundamentals tightening hard, but SK Hynix +230%/Micron +226% YTD, narrative **crowded/80**). Pure-play **DRAM** (Roundhill Memory ETF, $13.88B AUM, fastest ETF launch ever) is **US/non-UCITS → not buyable by EU retail under PRIIPs**; the least-bad UCITS route is **SEMI.L** (global semis, diluted memory weight). Remaining follow-ups: (1) consider a dedicated `struct_hbm_memory_cycle` structural catalyst vs. the current link to `struct_ai_capex_supercycle`; (2) thesis is **not yet draftable cleanly** for a EUR investor without single-stock (Micron ADR) or broker-specific non-UCITS access — and it would compound AI-complex correlation, so defer any draft until copper is trimmed.

## Pending Actions
- 🔴 **HIGH — Reduce copper position** (`…copper_miners_datacenter_alpha`) from 6% to ~3–4%: asm_02 invalidated AND resolves the 10%→≤8% correlation breach.
- ✅ **DONE — `semiconductors_memory` gap promoted** (user decision). Sector + ETFs added, taxonomy bumped to v1.2. Follow-up: sector study + structural-catalyst linkage next cycle.
- 🟡 **MEDIUM — Fix sector→structural linkage bug:** `eu_defense_prime_contractors` lists event IDs (`cat_20260603_nato_defense_gdp`, `cat_20260603_nato_hague_5pct_gdp`) in its structural slot → "YAML not found" (graceful, score unaffected). Should reference only `struct_*` IDs.
- 🟡 **MEDIUM — Re-evaluate AI-infra draft** after copper trim (currently blocked by correlation).
- 🟡 **MEDIUM — Annotate `study_copper_miners`** for the inventory correction at next refresh.
- 🟢 **LOW — Quarterly indicators due** (~95 days): refresh IEA DC power, transformer lead-times, EU grid, NATO %GDP, DC copper demand at Q2 prints.
- 🟢 **LOW — Resolve AI-capex ind_01 unit inconsistency:** `ai_capex_supercycle` ind_01 = 87 vs `copper_datacenter_demand` ind_01 = 175 for the same combined-capex series (both saturate 🟢, no score impact).
