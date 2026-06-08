# CATALYX — Review 2026-06-08  (trigger: `scheduled`, first full run post-refactor)

> ⚠ PRE-CALIBRATION: composite weights unvalidated (0 closed positions). Scores indicate relative ordering, not precise conviction.
> Run recorded: `run_20260608_150929` (53 sectors, version 2b6fd794f5da), diffed vs `run_20260606_205930`.

## 0. Macro & Geopolitical Context
- **Rates / inflation — hawkish tilt.** Fed holds 3.50–3.75% (Jun 16–17 mtg, ~98% no-change), but **new Chair Kevin Warsh** (since May 15) inherits **April CPI 3.8% YoY — highest since May 2023** (energy-led); rate-*hike* odds rising. May CPI lands Jun 10. → confirms the "yields up → rotation out of long-duration tech" leg behind the AI-capex scare; mild headwind for rate-sensitive utilities (grid).
- **Copper (open position):** live COMEX **$6.37/lb ≈ $14,051/t** (verified — YAML $13,965/t is correct, *not* overstated). 2026 refined deficit doubling to ~330kt. BUT **LME inventory ~393k & rising, Comex record ~457k** → the near-term *supply-tightness* leg is breaking (demand thesis intact).
- **Gold:** ~$4,529/oz (+34% YoY); CB net buying resumed April (+17t; Poland +14t, China +8t); ~755t CB demand expected 2026.
- **NATO/defense:** 2026 allied spend >$1.5T (first time), Europe +14% to $864B → strongly supports `struct_nato_rearmament`.
- **Space (user-flagged):** SpaceX IPO imminent — S-1 filed May 20, pricing Jun 11 / Nasdaq listing Jun 12 as **SPCX**, raise up to ~$75B at ≥$1.8T (rumors $2T), up to 30% to retail; **Starlink 10.3M subs (doubled YoY)**. Advisers warn it could slip to 2027.
- **Tape:** risk-off — SPY −2.5%/5d, VIX 21.5 (+6). 26 of 44 timed sectors read `falling`.

## Executive Summary
1. **NON-OBVIOUS:** Copper's bull case has *inverted its driver*. Price is near all-time highs ($14k/t) and the structural DC-demand thesis is intact (catalyst_alignment 95.9, regime intact, hyperscaler capex *upgraded* to ~$700B), yet the **supply-tightness leg the position was partly sold on is gone** — LME inventories are rising through the inv_03 review stop (~393k > 350k). The thesis still holds, but for a *different reason* than at entry. Watch, don't reflexively sell on inventory.
2. **Space re-rated into the review by the user's instinct.** Refreshing `space_commercial` + registering the IPO catalyst moved it from ~#10 to **#5 (77.4)**. Timing is unambiguous: proxy UFO −16%/5d, −19% drawdown → falling knife + binary event in 4 days → `wait_event`.
3. **Regime: 7 `contested` (AI/semi/cloud), all noise** (`review_recommended=false`), **0 breaking**, all 9 structurals `healthy`. Both open positions' driving catalysts are `intact`.
4. **Model strategies all beat SPY** over trailing 180d (momentum +27pp, catalyx +24.6pp). Live track record still accruing (real book at inception, 1→2 points after today).
5. **Best diversifiers vs the copper/grid book = cybersecurity** (corr 0.06), but timing is poor right now (cyber_commercial overbought, cyber_defense falling).

## 1. Catalyst Updates
- **New event registered:** `cat_20260612_spacex_ipo_listing` (corporate_event / mega_ipo_listing, strength 80, `confirms` → `struct_commercial_space_supercycle`, event_date 2026-06-12). Closes a documented-but-missing gap (see Findings #1).
- No other event cleared the strength-55 threshold this cycle. Macro candidate noted but not registered: *Warsh + sticky 3.8% CPI + rising hike odds* (the AI-capex scare event already carries the rotation leg).

## 2. Sector Studies Refreshed
- `study_space_commercial` (Starlink 4M→10.3M, IPO specifics, UFO AUM $376M→$1B, NASA-ETF vehicle, slip-to-2027 risk). All other 52 studies fresh (<7d) → skipped by rotation.

## 3. Catalyst Dashboard
- Live dashboard rebuilt via `build_site.py` (57 parquet / 16 tables; 7 events incl. SpaceX, 53 studies). Supersedes the standalone markdown dashboard this cycle.

## 4. Sector Heatmap (top 10, recorded)
| # | Sector | Composite | Notable |
|---|---|---|---|
| 1 | `cybersecurity_commercial` | 83.8 | overbought timing |
| 2 | `semiconductors_design` | 81.3 | contested · enter_now |
| 3 | `semiconductors_equipment` | 80.3 | contested · enter_now |
| 4 | `cybersecurity_defense` | 78.2 | diversifier · falling |
| 5 | `space_commercial` | 77.4 | ↑ from study+IPO · wait_event |
| 6 | `semiconductors_foundry` | 76.7 | contested · enter_now |
| 7 | `copper_miners` | 76.1 | **OPEN** · intact |
| 8 | `semiconductors_memory` | 75.4 | contested |
| 9 | `grid_infrastructure_utilities` | 74.3 | **OPEN** · intact |
| 10 | `space_defense_satellite` | 73.5 | |

## 4b. Opportunities & Rotation (recommendations — not trades)
**Regime watch:** 7 contested (`cloud_software_saas`, `real_estate_data_centers`, `robotics_automation`, `semiconductors_design/equipment/foundry/memory`) — all `review_recommended=false` = noise from the lingering AI-capex scare. 0 breaking.

**Opportunities (dislocation):** cleanest dip = `ai_infrastructure_data_centers` (−5.1%, **near-pure contagion** idio −0.14, cat 96.7, intact). `space_commercial`/`space_defense` show as opportunities but are **majority idiosyncratic** (contagion_fraction 0.38) → *investigate, not buy* (the IPO run-up unwinding — a cause we understand).

**Diversifiers (vs held copper/grid):** `cybersecurity_commercial` (corr 0.06, comp 83.8) and `cybersecurity_defense` (corr 0.06, comp 78.2) — genuinely uncorrelated. Space corr 0.48 (less so).

**Entry timing:** 26 falling / 15 neutral / 2 overbought / 1 basing. `enter_now`: the 3 semis + `us_defense_prime_contractors` (relative strength). `wait_event`: `space_commercial` (IPO). `scale_in`: `eu_defense_prime_contractors` (basing).

## 5. Open Positions
| Sector | Days | Assumptions | Driving regime | Stops | Unrealized | Action |
|---|---|---|---|---|---|---|
| `copper_miners` (4COP.DE, €1000) | ~4 | 1 hold / **1 weakening (asm_02)** / 0 violated | intact | machine clear; **inv_03 inventory review MET** (393k>350k) | −6.9% (−€69) | **HOLD** — structural demand intact ($14k copper, $700B capex); the broken leg is *tightness*, secondary. Watch inv_03; don't add (falling). |
| `grid_infrastructure_utilities` (IQQH.DE, €500) | ~4 | 3 hold / 0 weakening | intact | all clear | −3.95% (−€20) | **HOLD** — intensity 95 (highest), 3/4 assumptions hold. Watch inv_03 (Fed +200bps) given hawkish tilt; mild rate headwind. |

## 6. Catalyst Exposure
| Catalyst | Invested € | Sectors | % of €1500 book |
|---|---|---|---|
| `struct_copper_datacenter_demand` | 1000 | copper_miners | 66.7% |
| `struct_energy_transition_grid` | 350 | grid | 23.3% |
| `struct_ai_capex_supercycle` | 150 | grid | 10.0% |

Note: copper's *parent* theme is `struct_ai_capex_supercycle` → true AI-capex-correlated exposure is higher than the 10% shown. Relevant to Step 9 cap checks, not a current breach (small absolute book). `max_combined_pct` 20% (warn).

## 7. Position Open Recommendations (recommend-only — open via `/catalyx-open`)
Top-5 with no open position: see AskUserQuestion. Short version:
- `cybersecurity_commercial` #1, best diversifier — **WAIT** (overbought RSI 68.9, ran up against the tape).
- `cybersecurity_defense` #4, diversifier corr 0.06 — **WAIT** (falling); strongest *fit* once it bases.
- `semiconductors_design/equipment` enter_now timing — **caution**: contested regime + crowded, correlated to the AI complex you're already exposed to via copper/grid.
- `space_commercial` #5 — **WAIT_EVENT** (IPO Jun 12; don't chase a −16%/5d knife into a binary event).

## 8. Tax Snapshot YTD
| Metric | Value |
|---|---|
| Realized gains YTD | €0 (no closing movements) |
| Tax paid YTD | €0 |
| Current marginal bracket | 19% (first bracket) |
| Unrealized (mark) | −€89 (copper −€69, grid −€20) — both harvestable if closed |

## 8b. Stale Indicators (Step 1.5 — refresh next cycle)
11 flagged (mostly quarterly @ 99d, just over the 95d limit). Deferred this run (no fresh prints available without dedicated searches; copper `ind_02` price *verified current*). HIGH-priority refresh targets: `struct_copper_datacenter_demand` ind_04 (DC-demand est), `struct_energy_transition_grid` ind_01/02, `struct_ai_capex_supercycle` ind_03, `struct_nato_rearmament` ind_01. Lower: solar, japan, cyber, commercial_space ind_01.

## 9. Watch-Only Triggers
Not deep-checked this cycle (WebSearch-expensive, low marginal value). Carry forward.

## 10. Taxonomy Gap Review
**0 pending proposals** (`data/taxonomy_proposals/` empty). Nothing to decide.

## Pending Actions
- 🔴 **Refresh the 11 stale indicators** before the next scoring run (esp. the 4 position-relevant ones above).
- 🔴 **Confirm SpaceX IPO** actually prices Jun 11–12 (vs slip to 2027); update `cat_20260612_spacex_ipo_listing` status/event_date accordingly.
- 🟡 **Copper inv_03:** decide Reduce vs Hold-and-watch on the inventory review-stop (currently recommend HOLD — demand-driven thesis, price strong).
- 🟡 Set `narrative_maturity` on `struct_ai_capex_supercycle` and `struct_nato_rearmament` (currently unset = `?` in repo summary).
- 🟢 If diversifying, cybersecurity is the fit — wait for cyber_defense to base / cyber_commercial to cool.

---
### Refactor bug/improvement findings — see chat summary.
