# CATALYX — Sector Heatmap
**Report type:** heatmap
**Period:** {{YYYY-MM-DD}}
**Generated:** {{datetime}}
**Structural catalysts:** {{N}} active · **Event catalysts:** {{N}} active

> Phase indicator: `Phase 0 — Partial` if momentum/flows missing · `Phase 1 — Full` when all 5 dimensions available.
> [S] = Structural catalyst · [E] = Event catalyst

---

## Investable Sectors — Ranked by {{primary_sort_dimension}}

| Rank | Sector | Cat. Align | Momentum | Flow | Valuation | Crowding | Composite | Tier-1 ETF |
|---|---|---|---|---|---|---|---|---|
| 1 | `{{sector_id}}` | {{score}} | {{score or —}} | {{score or —}} | {{score or —}} | {{score or —}} | {{composite or —}} | {{ticker}} |

---

## Top N — Detail

### {{rank}}. {{sector_label}} · `{{sector_id}}`
**Catalyst alignment: {{score}}/100** · Dominant: {{catalyst_list}}

**Why this rank:** {{2-3 sentences. Must explain what is non-obvious. If the reason is obvious, the analysis adds no value.}}

**Thesis in one line:** {{what has to be true for this sector to outperform}}

**ETF options:**

| Ticker | Exchange | CCY | TER | AUM ($M) | UCITS | Tier |
|---|---|---|---|---|---|---|---|

**Missing dimensions:** {{list what real-time data would add}}

---

## Crowding Flags

| Sector | Risk | Reason |
|---|---|---|

---

## Watch-Only — Trigger Progress

| Sector | Triggers met | Nearest trigger |
|---|---|---|

---

## Delta vs Prior Heatmap

| Sector | Change | Driver |
|---|---|---|

---

## Dimensions Missing for Full Score

| Dimension | Status | Unblocked by |
|---|---|---|
| `momentum` | ❌ / ✅ | yfinance (Phase 1) |
| `flow_confirmation` | ❌ / ✅ | iShares AUM endpoint (Phase 1) |
| `valuation_relative` | ❌ / ✅ | Sector P/E data (Phase 1) |
| `crowding_risk` | ⚠ qualitative / ✅ | COT data (Phase 1) |
| `composite_score` | ❌ / ✅ | Requires all 4 above |

---

*Template: [heatmap_template.md](heatmap_template.md)*
*Next heatmap: {{date}} or on new catalyst event*
