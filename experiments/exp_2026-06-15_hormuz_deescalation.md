# Experiment — 2026-06-15 Iran/Hormuz De-escalation (pre-open prediction)

> **Logged on:** 2026-06-15, **markets CLOSED** (genuine no-look-ahead prediction) · **Author:** pre-open forecast
> **Catalysts:** `cat_20260614_hormuz_deescalation_deal` (new) · reverses `cat_20260228_hormuz_closure`
> **Falsifiable:** every directional call below is checked against the next open + the days after. Recorded BEFORE prices move.

## The event (researched, not invented)

On **2026-06-14** the US and Iran announced an **initial deal to end the war and reopen the Strait of Hormuz**:

- **MoU to be signed 2026-06-19** in Switzerland; **60-day ceasefire** for a fuller agreement (lift the naval blockade, dismantle Iran's nuclear program, remove enriched uranium). Trump-admin officials put **~80% odds** on signing.
- **Market reaction (Friday / overnight futures):** **Brent −4.7% to $83.25**, **WTI −5.1% to $80.53** — lowest since 2026-03-10. Oil **~−6% on the week** but still **+20% above pre-conflict** levels.
- **Context:** Hormuz has been effectively closed since 2026-02-28 (−95% crude / −99% LNG transit per WTO). Normalization is **gradual even after signing** — mine-clearing, restart of idled production, facility repairs. CBA: flows only need to reach **60–70%** of pre-war to restore oversupply expectations.

Sources: [Reuters/Investing](https://www.investing.com/news/commodities-news/oil-slips-over-4-after-us-iran-reach-peace-deal-reopen-strait-of-hormuz-4741118) · [Britannica 2026 Iran war](https://www.britannica.com/event/2026-Iran-war) · [2026 Strait of Hormuz crisis (Wikipedia)](https://en.wikipedia.org/wiki/2026_Strait_of_Hormuz_crisis) · [House of Commons Library](https://commonslibrary.parliament.uk/research-briefings/cbp-10636/)

## The core thesis being tested

A de-escalation is the **opposite-sign mirror** of the Feb-28 closure. The closure was: oil/LNG/tankers **UP**, broad market **risk-off**. So the reversal should be: **oil/LNG/tankers DOWN, broad market risk-ON**, driven by (a) the geopolitical risk premium fading and (b) lower energy costs → lower inflation impulse → friendlier rate path for long-duration assets.

**Key nuance that makes it falsifiable, not a coin-flip:** the deal is **largely telegraphed** (`consensus_surprise=0.3`, weeks of talks, 80% odds) and oil already moved ~−6% on the week. So `is_priced_in_estimate=0.50`. The *incremental* move at the open should be **moderate, not a gap-and-crash** — and the energy *short* side has more juice left than the risk-on *long* side, because the war premium in oil is concrete and measurable while the equity risk-on is diffuse.

---

## PREDICTION (logged pre-open)

Direction + conviction per CATALYX investable sector. Conviction = how confident in the **sign**, not the magnitude.

### Expected to RISE — "las subidas en bolsa" (risk-on / oil-consumers)

| Sector | ETF | Dir | Conviction | Why |
|---|---|---|---|---|
| Broad equities (benchmark) | SPY / Europe | ▲ | **High** | Geopolitical risk premium fades; this is the cleanest read. |
| `consumer_india_em` | INDA, NDIA.L | ▲▲ | **High** | India is a major oil **importer** — cheaper crude is a direct terms-of-trade + margin tailwind. Highest-conviction *single* long. |
| `luxury_goods` | LUXE.PA, GLUX.SW | ▲ | Medium-High | Risk-on + EU/China consumer relief; energy-cost-sensitive demand. |
| `semiconductors_design` | SOXX, SMH, SEMI.L | ▲ | Medium | Long-duration growth re-rates on a friendlier inflation/rate path; also rebounding from the 06-05 AI scare. |
| `ai_infrastructure_data_centers` | AIPO, WTAI, BOTZ | ▲ | Medium | Same duration logic; high beta to a risk-on tape. |
| EU energy-intensive industrials/autos | (broad) | ▲ | Medium | Europe was hit hardest by the energy shock; biggest relief on reversal. |

### Expected to FALL — war-premium unwind (the short side; more conviction on magnitude)

| Sector | ETF | Dir | Conviction | Why |
|---|---|---|---|---|
| `oil_majors_integrated` | XLE, IUES.L | ▼▼ | **High** | War premium unwinds with crude −5%. |
| `oil_services_equipment` | OIH | ▼▼ | **High** | Highest oil-beta; falls more than majors. |
| `lng_natural_gas` | FCG, LNGA.L | ▼▼▼ | **Highest** | The study names Hormuz reopening as THE reversion risk: TTF/JKM premium (~+€20/MWh, +83% JKM) collapses + LNG-carrier glut as Cape rerouting ends. **The cleanest short.** |
| Tanker / shipping freight | (BWET, gap-proposal sector) | ▼▼▼ | **Highest** | Rerouting premium evaporates; BWET surged +1,331% on the closure → biggest mean-reversion. |
| `gold_physical` / `gold_miners` | IGLN.L / GDX | ▼ | Medium | Safe-haven bid fades (already −25% from ATH, so limited). |
| `eu_defense_prime_contractors` | EUDF.L, DFEN.DE | ≈/▼ | **Low** | Mild war-premium give-back, BUT `struct_nato_rearmament` is a multi-year structural a ceasefire does NOT reverse → expect resilience, not a real drop. |

### Explicit NON-predictions (intellectual honesty)
- **Magnitude at the open is small-to-moderate**, not a crash — 50% is already priced and the deal is telegraphed.
- **Energy could even bounce intraday** if traders "sell the rumor, buy the signing" or if mine-clearing/restart delays remind the market normalization is months away. The *sign over the next 1–2 weeks* is the real test, not tick-by-tick at the bell.

---

## What was changed in the repo (the "scores update")

1. **`cat_20260228_hormuz_closure.json`** — `strength_score` kept at **94** (historical impact is not rewritten); decay already fades it (residual `catalyst_alignment` ≈ 27 today). Notes document the reversal; `status` stays `active` because the strait is still physically closed; flips to `invalidated` when transit restoration is confirmed.
2. **`cat_20260614_hormuz_deescalation_deal.json`** (new) — the macro driver of the reversed (bearish-oil / risk-on) side. `relation_to_structural=contradicts`, `strength=68`, `is_priced_in=0.50`, `decay_halflife=30`.
3. **Scoring note:** `catalyst_scorer` does **not** aggregate a "direct" contradicts event (no oil-supply structural to dampen). So energy `catalyst_alignment` falls via the **natural decay** of the closure event (→ ~27 now, → 0 on `invalidated`), not via a hand-edited number. LNG verified at `catalyst_alignment=27.2 [intact]`.

## How this gets graded (no look-ahead)
When markets have traded, append a **Findings** section: for each row, sign correct? rank the conviction calls by hit-rate. Headline metric = **directional accuracy on the RISE list** (the user's question) + whether the **FALL list fell more than the RISE list rose** (the asymmetry thesis). The closed loop is the point — this file is the hypothesis, the open is the result.
