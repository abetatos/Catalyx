# catalyx-close

Close (or partially close for profit/stop) a position. Records a `close` movement, realizes P&L
in EUR, and computes Spanish CGT. INDEPENDENT of the review cycle — run it whenever you exit.

Usage:
- `/catalyx-close <sector_id|etf>` — close the open position in that sector/vehicle.

## Steps

1. **Identify the position** and its cost basis:
   ```
   uv run python -m catalyx.store.movement_repo positions
   ```
   Note `qty`, `avg_cost` (EUR), `invested_eur`, and the opening movement's `attribution[]` +
   `risk_discipline` (read the opening file in `data/movements/`).

2. **Gather the exit** from the user: `executed_at`, exit `price`, `qty` to sell (full or
   partial), `fees`, and `close_reason`. Compute proceeds in EUR (`amount_eur`). For a
   non-EUR vehicle, convert at the exit-date FX (EUR rule: P&L always in EUR).

3. **Realized P&L (EUR):** `gross_pnl_eur = proceeds_eur − fees − (avg_cost × qty_sold)`.
   (Average-cost basis today; FIFO lots are a Fase-5 refinement — see the restructure plan.)

4. **Spanish CGT.** Get the YTD realized baseline (prior realized gains this calendar year):
   ```
   uv run python -m catalyx.store.movement_repo positions   # realized_eur = YTD realized so far
   ```
   Then call the engine (single source of truth — never apply brackets by hand):
   ```
   uv run python -m catalyx.execution.tax_engine --gain <gross_pnl_eur> --ytd-prior <realized_eur> --json
   ```
   Use its `tax_due`, `effective_rate`, `net_gain` directly.

5. **Score the assumptions at close (right-reason input).** For each `assumptions[]` in the
   opening movement's `risk_discipline`, ask the user: did it hold? (`holding`/`violated`/
   `indeterminate`). Ask whether the attributed catalyst mechanism actually materialized as
   described. Record these on the close movement.

   > Full attribution decomposition (`return_decomposer` → sector_beta vs catalyst vs
   > idiosyncratic, and `right_reason_score`) is **Fase 2** — note it as pending; do not estimate
   > it by hand now.

6. **Write the close movement** to `data/movements/mov_YYYYMMDD_<sector_id>_close.json`:
   - `action: "close"` (or `trim` for partial), `amount_eur` = proceeds, `qty`/`price`/`fees` of
     the sale.
   - `attribution[]`: carry the SAME catalysts/weights as the opening movement (so realized P&L
     attributes to the right catalysts in the ledger).
   - `trigger`: `profit_take` / `stop_hit` / `reconsideration` / `contradiction` as appropriate.
   - `metadata`: record `gross_pnl_eur`, `tax_due`, `net_gain`, `close_reason`, the assumption
     outcomes, and `catalyst_materialized: true/false`.
   - Validate against `schemas/movement.json`.

7. **Ingest** to refresh positions, the lake mirror, and the catalyst ledger:
   ```
   uv run python -m catalyx.store.movement_repo ingest --write-back
   ```

8. Confirm: realized P&L, tax due, net gain, updated book, and the catalyst ledger line
   (`lake_query ledger`) — this is the row that builds the "which catalysts won" track record.
