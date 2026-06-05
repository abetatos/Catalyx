### 8. `semiconductors_equipment` — composite 69.5 (rank #8)

**Catalysts driving alignment (catalyst_alignment: 92.0):**

- `struct_ai_capex_supercycle` (intensity 89.9, dominant): Hyperscaler combined capex at $87B/quarter — well above the $60B strong threshold. TSMC guides $38–42B capex for 2026 (~70% flows to equipment), with Needham estimating $45B. AMAT Q2 2026 guide raised from >20% to >30% YoY growth — a mid-cycle upward revision, not a peak signal. ASML Q1 2026 reported €8.8B net sales (+13% YoY), full-year guidance raised to €36–40B. Backlog: €38.8B at end-2025 (12+ months of locked revenue); Q4 2025 bookings record €13.2B (€7.4B from EUV alone). Equipment revenues are confirming the demand signal with a 12–24 month lag from the original 2023 AI inflection — the lag is now materializing as revenue, not merely as order visibility.
- `struct_copper_datacenter_demand` (base intensity 83.9, confirmed by `cat_20260603_copper_supply_deficit_2026` strength 78, decayed score 77.22): Secondary alignment. Datacenter copper intensity (bus bars, cooling manifolds, power distribution) correlates with fab construction volumes; greenfield reshoring fabs (ESMC Dresden €10B+, CHIPS Act Ohio/Arizona) are the highest equipment-intensity event in the industry and carry the same structural copper demand driver. Additive signal, not the dominant one.

Dominant signal type: event + structural. `struct_ai_capex_supercycle` is the binding catalyst. `struct_copper_datacenter_demand` reinforces via greenfield fab construction but does not independently drive WFE spending.

---

**Non-obvious finding — what the market has NOT priced:**

The consensus trade is "ASML EUV monopoly + AI capex = buy semis equipment." That narrative is correct but already crowded (see crowding_risk below). The under-priced angle is the **oligopoly structure below ASML**.

ASML is the only EUV supplier, but every EUV tool shipment generates a mandatory toolset co-purchase that is *not* monopolized: deposition (AMAT CVD/ALD), etch (LRCX, TEL), metrology/inspection (KLAC, Onto Innovation). A single High-NA EUV system at ~$370M per unit requires approximately $600–900M in adjacent process tool spend to bring the surrounding wafer process to spec. This toolset co-dependency is a structural feature, not cyclical, and it means AMAT, LRCX, and KLAC grow *proportionally* with EUV shipment volumes — but trade at 25–35× forward P/E versus ASML's 35–40×, a discount that partially reflects the "they're not ASML" framing while ignoring the captive demand structure.

The second unpriceable: **High-NA EUV ramp optionality**. ASML plans 5–10 High-NA EXE:5200 deliveries in 2026 (vs <5 in 2025). TSMC delayed adoption to ~2029 (A10 node), but Intel Foundry is first mover, and each unit at $370M is 3–5× the revenue of a standard NXE:3800. The market has partially de-rated ASML on the TSMC delay — that de-rating does not reflect that Intel Foundry's unit demand alone represents $1.5–3.7B of incremental ASML High-NA revenue in 2026–2028, before TSMC volume commences. This is a mispriced optionality that does not appear in the buy-side consensus which focuses primarily on TSMC's cycle.

Third: **China re-entry optionality is not in the price, but China share collapse already is**. China fell to 19% of ASML Q1 2026 net system sales (from 36% in Q4 2025) as export controls bite; South Korea surged to 45%. The market has priced the China revenue loss. It has not priced the possibility of a US-China trade deal partial rollback of DUV restrictions — a tail event that would be immediately accretive to AMAT, LRCX, KLA (historically 25–30% of revenues from China) and represents pure upside from current depressed China assumptions.

---

**Best ETF for a Spanish investor:**

No pure-play semiconductor equipment ETF exists in UCITS or US-domiciled form as of 2026-06-05. The closest accessible UCITS vehicle is:

**`SEMI.L` — iShares MSCI Global Semiconductors UCITS ETF (LSE: SEMI)**
- TER: 0.35%
- AUM: ~$1.41B (LSE-listed share class; $4.25B across all exchange listings — adequate liquidity)
- UCITS: Yes — accessible for Spanish retail investor under PRIIPs/KID rules
- Spread: ~40bps on LSE (real-time bid/ask ~$7.58/$7.61 per HL/Yahoo Finance data 2026-06-05); materially wider than the 8bps stored in the YAML — flag for recalibration before position sizing. Use limit orders; avoid market orders.
- Equipment sub-weight: ~20–25% (ASML ~8–10%, AMAT, LRCX, KLAC, Onto Innovation combined). Remaining ~75% is foundry (TSMC ~15–18%), fabless design, and memory — correlated but not the pure equipment thesis.
- Replication: physical

**Thesis fit caveat:** `SEMI.L` is the best available UCITS proxy, not a pure vehicle. ~75% of the fund expresses adjacent semiconductor theses. If the specific equipment alpha thesis (picks-and-shovels vs foundry/design) is the target, the cleanest single-name expression for a Spanish investor is **ASML on Euronext Amsterdam** (EUR-denominated, no PRIIPS restriction on individual equities, EUV monopoly = direct equipment revenue). ASML is not an ETF but eliminates the thesis dilution problem.

Monitor **HANetf and Global X** product pipelines for a UCITS semiconductor equipment ETF launch (AMAT + LRCX + KLAC + ASML + ONTO + BESI targeting WFE exposure); such a launch would immediately become the preferred Tier 1 vehicle.

No AUM warning on `SEMI.L` ($1.41B well above $200M threshold). Spread warning applies — 40bps is above CATALYX tier-1 threshold.

---

**What would change the ranking:**

*Upward (composite > 75, ranking rise):*
- Crowding unwinding: `narrative_maturity` reverting from `crowded` toward `emerging` (requires analyst coverage reduction + ETF outflow confirmation) — currently penalizing composite by ~15–18 points.
- TSMC re-accelerates High-NA adoption timeline (pulls forward from 2029 to 2027–2028), triggering a fresh earnings-revision wave for ASML; or TSMC raises 2026 capex guidance above $45B.
- BIS partial rollback of DUV China export controls under a US-China trade framework — would add $8–12B to AMAT/LRCX/KLA addressable market without requiring any new demand catalyst.

*Downward (composite < 55, ranking drop):*
- Hyperscaler capex cut >15% in a single earnings quarter — the single highest-probability de-rating trigger; currently not visible (Q2 2026 guidance intact).
- TSMC issues capex guidance reduction (any cut to the $38–42B range propagates to equipment order cuts with a 1–2 quarter lag).
- `struct_ai_capex_supercycle` intensity falls below 75 (from current 89.9) — would reduce catalyst_alignment materially and pull composite below momentum-only baseline.
- SEMI WFE market guidance revised from $139B to below $120B (SEMI mid-year update due June–July 2026) — watch this as the first leading indicator of cycle softening.
- Momentum reversal: `semiconductors_equipment` momentum currently 87.5 (rank-based, cross-sectional). A sustained 20%+ drawdown in ASML/AMAT from current levels would compress momentum score below 50, collapsing the composite even if catalyst_alignment holds.
