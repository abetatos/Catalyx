# CATALYX — Scheduled Review 2026-06-12

**Trigger:** `scheduled` (full periodic review). Run id: `run_20260612_151007` (diffed vs `run_20260608_150929`).

## 0. Macro & Geopolitical Context

- 🔴 **Iran war / Strait of Hormuz energy shock is the dominant macro force.** US–Iran ceasefire holds (Apr 7–8) but brinkmanship over Hormuz transit continues; IEA calls it the "largest oil supply disruption in history." Brent ~$80–82.
- 🔴 **US CPI (May) = 4.2% YoY**, highest since Apr-2023 (3.8%→4.2%), energy +23.5% YoY drove >60% of the print. Core a tamer 2.9% (monthly +0.2%, below est). Stagflationary mix: energy-pushed headline, soft core.
- 🟡 **Fed on hold at 3.50–3.75%** (99% implied for the Jun 16–17 meeting). **Powell out, Warsh in.** First 2026 cut being pushed out; the `gold_physical` study flags a May NFP upside shock (172k vs 85k) lifting *rate-hike* odds — a real-rate headwind.
- 🔴 **Gold −25% from its Jan-28 ATH** ($4,080 vs $5,589), lowest since Nov-2025. **But the structural buying thesis is intact** — CBs net +315T in Q2 (Poland, PBoC, Uzbekistan). The *price* corrected on real rates/dollar; CB *demand* did not. (Important: this hits gold-sector momentum, NOT `struct_cb_gold_accumulation` intensity, which held at 72.1.)
- 🟢 **AI capex confirmed $600–725B for 2026** (+36% YoY, ~75% = $450B direct AI infra): Amazon ~$200B, Google $175–185B, MSFT $110–120B, Meta $125–145B. FCF strain emerging (Amazon FCF→negative). The 06-05 "peak scare" was a Broadcom *supplier* miss, not hyperscaler cuts.
- 🟢 **NATO 5%-by-2035 rearmament intact** (3.5% core + 1.5% resilience); Europe defense +14% to $864B in 2025, Germany +24%. Russia–Ukraine grinding (slight Russian territorial losses last 4 wks; 61–62% on both sides now favor negotiations → a peace-overhang tail risk for defense).
- 🟢 **Copper** ~$6.4/lb (LME), −4.3% on the month on US–Iran peace optimism, +33.9% YoY; Jefferies 491kt/yr deficit through 2030 — structural thesis intact.

**Deltas vs 06-08 review:** energy-driven CPI re-acceleration and the gold drawdown are the new facts; the AI-capex "scare" is confirmed as a supplier (not demand) event; space supercycle has surged in the ranking.

## Executive Summary

1. **Space sectors took the top of the heatmap** — `space_commercial` #5→**#1** (80.5), `space_defense_satellite` #10→**#4**, `ai_infrastructure` #11→**#5**. The commercial-space structural carries the highest intensity in the book (88.7) and is **uncorrelated** (corr ~0.38) to the AI/semis cluster that's under pressure — it is simultaneously the top-ranked sector AND the top rotation diversifier.
2. **NON-OBVIOUS:** Grid's −10.7% 5d drop is **mostly idiosyncratic, not contagion** (idio −8.2 vs contagion −2.5). That residual is the **rate-sensitivity** the grid study warned about (Warsh-era hike odds), *not* a thesis break — fundamentals actually **strengthened** (transformer lead times 87→128 weeks; 40% of AI datacenter projects now power-constrained). So the dip is a multiple compression on a strengthening thesis → entry-timing says `wait_stabilize` (falling knife), not exit.
3. **The real book is winning the selloff: +0.92% since 06-05 inception while SPY fell −2.55% over the 5d window.** The diversifier construction (cyber +10.9%, low correlation confirmed) is doing exactly its job; the two AI-cluster experiments (semis −14.7%, copper −9.9%) carry the drawdown.
4. **All invalidation stops are clear across every open position** — including the deterministic price stops and the Claude-checked fundamental stops (no hyperscaler capex cuts, no Fed +200bps, no export-control broad ban). **No exits warranted.** `semiconductors_design` regime is `contested` (the AI scare touches its own thesis) — watch, not breaking.
5. All 4 model strategies beat SPY over 180d (momentum +40.2%, catalyx +39.5%, equal +34.7%, low_crowding +23.3%).

## 1. Catalyst Updates

- **No new CatalystEvents above threshold this cycle.** Every live macro theme is already registered: `cat_20260228_hormuz_closure` (energy shock, str 94), `cat_20260603_nato_defense_gdp`/`_hague_5pct_gdp`, `cat_20260603_copper_supply_deficit_2026`, `cat_20260605_ai_capex_peak_scare`, `cat_20260601_us_ai_chip_export_controls`, `cat_20260612_spacex_ipo_listing` (today).
- **Intensities recomputed from indicators (Rule 1, written back):** ai_capex 82.9 (↓4.0), cb_gold 72.1 (flat — buying intact despite price), commercial_space 88.7 (↑2.0, highest), copper 71.9 (↓4.0), energy_grid 92.9, cyber 85.8, nato 70.7, japan_carry 68.1, solar 68.1.
- **Lifecycle (governance: auto): no transitions.** No event meets archive criteria (copper event decayed 78→72.4, far above the 20 floor); no structural is dormant (lowest 68.1 vs <40 threshold); all 9 structurals read **healthy** (none degrading).

## 2. Sector Studies Refreshed

7 stale studies (>7d) refreshed via 4 parallel subagents; the other 46 freshness-skipped (studied 06-08). Key thesis updates:

| Sector | Update |
|---|---|
| copper_miners | Thesis intact — FCX record margins ($3.87/lb spread), COPX AUM tripled to $7.27B; headwinds = Goldman 2026 surplus call + diesel cost inflation; COPX −17% from high. |
| gold_physical | Regime shift — CB buying intact (244t Q1, PBoC accelerating) but **rate-cycle headwind now dominant** near-term driver of the −27% drawdown. |
| gold_miners | Crowded (GDX +108% YoY at peak); operating leverage cut both ways (−34% from peak); **AISC guidance raised** on diesel shock (Newmont $1,680, Barrick $1,760–1,950); Q2 AISC actuals (July) = falsification test. |
| grid_infrastructure_utilities | Thesis **strengthened** — transformer lead times 87→128 wks, 40% of AI-DC projects power-constrained, FERC June rulemaking; rate-sensitivity + materials inflation are the risks; narrative now crowded. |
| ai_infrastructure_data_centers | Bifurcated mid-cycle: GPU layer decelerating (Broadcom scare, Nvidia DC +22% & falling), **physical infra (transformers/cooling/construction) 2–3 yrs of revenue ahead**; SpaceX IPO = near-term flow-rotation risk. |
| semiconductors_memory | Mid-to-late expansion → **Q3-Q4 2026 price peak** per TrendForce; HBM3E +20%, all 2026 HBM sold out, DRAM ETF +90% since April = late-cycle crowding; exit discipline now the dominant requirement. |
| eu_defense_prime_contractors | Mid-expansion; Rheinmetall backlog €64B→€73B but Q1 rev miss (~€360M, 64% FY in H2 = binary); crowded (~29x P/E); ETF corrected to **EUDF.DE**; peace-talk tail risk. |

## 3. Catalyst Dashboard

Intensities current (Section 1). Highest-intensity structurals: energy_grid 92.9, commercial_space 88.7, cyber 85.8, ai_capex 82.9.

## 4. Sector Heatmap — Top 12

| # | Sector | Comp | Mom | Catalyst | Crowd | Maturity | Regime |
|---|---|---|---|---|---|---|---|
| 1 | space_commercial | 80.5 | 83.0 | 88.7 | 25 | emerging | intact |
| 2 | semiconductors_design | 80.3 | 98.9 | 77.3 | 75 | crowded | contested |
| 3 | semiconductors_equipment | 80.0 | 96.6 | 78.2 | 75 | crowded | contested |
| 4 | space_defense_satellite | 79.5 | 92.0 | 78.3 | 25 | emerging | intact |
| 5 | ai_infrastructure_data_centers | 77.6 | 94.3 | 96.8 | 75 | crowded | intact |
| 6 | copper_miners | 76.8 | 67.0 | 94.2 | 55 | mainstream | intact |
| 7 | semiconductors_memory | 76.3 | 89.8 | 73.3 | 75 | crowded | contested |
| 8 | semiconductors_foundry | 75.6 | 87.5 | 73.3 | 75 | crowded | — |
| 9 | cybersecurity_commercial | 74.0 | 85.2 | 85.8 | 55 | mainstream | intact |
| 10 | cybersecurity_defense | 69.4 | 78.4 | 78.3 | 55 | mainstream | intact |
| 11 | grid_infrastructure_utilities | 69.5 | — | — | — | crowded | intact |
| 13 | grid (post-rank) | — | — | — | — | — | — |

**Biggest movers vs 06-08:** space_commercial +4 (→#1), space_defense +6, ai_infra +6 (entered top-N), cybersecurity_commercial −8 (→#9), gold_physical −6, grid −4 (exited top-10), uranium_miners −7.

## 4b. Opportunities & Rotation (recommendations — not trades)

**Regime watch** (non-intact)

| Sector | Regime | Read |
|---|---|---|
| semiconductors_design / _equipment / _memory | contested | AI-capex scare touches their own thesis; single clustered shock = noise. Watch, no action. Capex was *raised*, so the contradicts is a supplier event. |
| robotics_automation, real_estate_data_centers | contested | Spillover from the AI/rate selloff; not fundamental. |

**Opportunities** (fell hard · intact · catalyst-confirmed · contagion-driven, SPY 5d −2.55%)

| Sector | Draw% | Contagion/Idio | catAlign | Read |
|---|---|---|---|---|
| ai_infrastructure_data_centers | −7.5 | −5.4 / −2.0 | 96.8 | Cleanest panic dip (mostly contagion, β 2.12, intact). Physical-infra layer has runway. |
| copper_miners (held) | −4.1 | −2.8 / −1.2 | 94.2 | Mostly contagion; thesis intact — already held. |
| grid (held) | −10.7 | −2.5 / **−8.2** | 94.2 | **Mostly idiosyncratic = rate sensitivity, not panic.** Fundamentals strengthened; wait to base. |

**Diversifiers** (anchored to the real book — healthy · low corr to held cluster)

| Sector | Comp | Corr | Note |
|---|---|---|---|
| cybersecurity_defense (BUG) | 69.4 | 0.26 | Low corr; sibling of the held cyber winner. |
| space_commercial (ROKT) | 80.5 | 0.38 | **#1 ranked + low corr + emerging** — best diversifying add. |
| space_defense_satellite (ROKT) | 79.5 | 0.38 | NATO-adjacent, low corr. |
| royalty_streaming_metals (MRGR) | 50.6 | 0.01 | Near-zero corr but lower quality. |

**Entry timing** (execution window)

| Sector | State | RSI / vol / 5d% | Overhang | Verdict |
|---|---|---|---|---|
| copper_miners | basing | 48 / 1.29 / −7.8 | — | scale_in |
| grid | **falling** | 39 / 1.09 / −10.7 | — | wait_stabilize |
| cybersecurity_commercial | basing | 57 / 1.44 / −7.1 | — | scale_in |
| semiconductors_design | basing | 59 / 1.12 / −4.3 | — | scale_in |
| semiconductors_equipment | **overbought** | 69 / 1.33 / +8.1 | — | wait_stabilize |
| space_commercial | basing | 50 / 1.54 / −4.1 | **SpaceX IPO @06-12** | wait_event |
| ai_infrastructure | basing | 57 / 1.59 / −4.4 | **SpaceX IPO @06-12** | wait_event |

## 5. Open Positions

| Sector | ETF | Days | P&L | Assumptions | Regime | Action |
|---|---|---|---|---|---|---|
| copper_miners | 4COP.DE | 7 | −9.9% | 1✓ / 1 weak (Goldman surplus) | intact | **HOLD** — stops clear, deficit thesis intact; basing → could scale_in but the surplus-call weakening argues hold first |
| grid_infrastructure_utilities | IQQH.DE | 7 | −10.6% | 3✓ | intact | **HOLD** — fundamentals strengthened; drop is rate-driven multiple compression; wait_stabilize before adding |
| cybersecurity_commercial | USPY.L | 4 | +10.9% | 2✓ | intact | **HOLD** — diversifier thesis validated (low corr confirmed); let the winner run |
| semiconductors_design | SEMI.L | 4 | −14.7% | 2✓ | **contested** | **HOLD** — price stop clear; AI scare is supplier-side not demand; July Q2 hyperscaler earnings is the binary test |

No invalidation breached anywhere. Every "Claude-check" stop resolves clear against Step 0 evidence.

## 6. Catalyst Exposure (committed basis, €10,000)

| Catalyst | Invested € | Sectors | % committed | Status |
|---|---|---|---|---|
| struct_copper_datacenter_demand | 1,000 | copper_miners | 10.0% | OK |
| struct_ai_capex_supercycle | 650 | grid, semiconductors_design | 6.5% | OK |
| struct_enterprise_cyber_spend | 500 | cybersecurity_commercial | 5.0% | OK |
| struct_energy_transition_grid | 350 | grid | 3.5% | OK |

All under the 20% correlated-catalyst cap. ~€2,500 invested / €10,000 committed → 25% deployed, €7,500 dry powder.

## 7. Position Open Recommendations

Top-5 with no open position → candidates:

- **space_commercial (#1, comp 80.5)** — dominant: highest structural intensity (88.7), *emerging* (edge left, not crowded), AND the top rotation diversifier (corr 0.38 to the book), on a **new uncorrelated catalyst** (no existing exposure → full room). Caveat: entry-timing `wait_event` — the SpaceX IPO lists today; flow could rotate / reset comps. **Recommendation: Open a first scale-in tranche, timed around the SpaceX IPO.**
- **space_defense_satellite (#4, 79.5)** — same catalyst family, emerging, low corr; NATO-adjacent. Secondary diversifying add.
- **semiconductors_equipment (#3, 80.0)** — crowded, **contested**, ASML **overbought** (+8%, wait_stabilize), AND shares the AI-capex catalyst already held via semis_design. **Recommendation: Skip/Wait** (overbought + correlated + contested).
- **ai_infrastructure (#5, 77.6)** — cleanest contagion dip, intact, but adds to the already-held AI-capex bet and `wait_event` (SpaceX). Defer.

## 8. Tax Snapshot YTD

| Metric | Value |
|---|---|
| Realized gains | €0 |
| Tax paid | €0 |
| Marginal bracket | n/a (no realizations) |
| If all open positions closed at mark | net negative (small harvestable losses on copper/semis/grid; cyber +€34 gross) → effectively €0 CGT |

## 8b. Stale Indicators (data-quality — refresh next cycle)

11 indicators overdue (mostly quarterly prints 103d old, 8d over limit, awaiting Q2 data): ai_capex ind_03, commercial_space ind_01, copper ind_04, energy_grid ind_01/02, cyber ind_02, japan_carry ind_01/04, nato ind_01, solar ind_02/03. None materially corrupts this run (theses unchanged vs Step 0); Q2 prints land over the coming weeks.

## 9. Watch-Only Triggers

| Sector | Change |
|---|---|
| quantum_computing | No change — no trigger surfaced this cycle |
| nuclear_fusion | No change |
| brain_computer_interface | No change |
| advanced_materials_metamaterials | No change |

(This cycle's macro was Iran/energy, gold, AI-capex, NATO — none touches the frontier watch themes.)

## 10. Taxonomy Gap Review

**0 pending proposals.** Nothing to review.

## Pending Actions

- 🟡 **MEDIUM** — Decide on **space_commercial** entry (scale-in, timed around the SpaceX IPO). It's the #1 rank + top diversifier on a new uncorrelated catalyst. → `/catalyx-open space_commercial`
- 🟡 **MEDIUM** — Watch `semiconductors_design` (contested, −14.7%): the July Q2 hyperscaler earnings is the binary confirm/contradict on the AI-capex thesis. No action now.
- 🟢 LOW — `grid`: do not add until it bases (rate-driven `falling`); thesis intact/strengthening.
- 🟢 LOW — Refresh the 11 stale quarterly indicators as Q2 prints land.
- 🟢 LOW — gold_miners Q2 AISC actuals (July) = falsification test on the rising-cost concern.
