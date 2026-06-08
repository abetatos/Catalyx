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

5. **Run the exit signal first (did you act with or against your own discipline?).** Before
   capturing anything, run the deterministic watcher so the close records whether you followed it:
   ```
   uv run python -m catalyx.scorer.exit_watcher --json --no-persist
   ```
   Note this position's `suggested_action` (hold/watch/reduce/exit). It becomes
   `outcome.signal_context.exit_watcher_action`; `followed_signal` = did the exit align with it
   (false = you overrode it — exited on a `hold`, or sat past a `reduce`/`exit`).

6. **Capture the experiment record (the `outcome` block — this is the point of the ledger).**
   Treat the close as a registered experiment and gather from the user:
   - **`exit_reason`** — free text, what actually drove the exit.
   - **`exit_note`** — their IN-THE-MOMENT behavioral reflection ("sold because the red Monday
     spooked me"). Ask for it plainly; it is the self-learning signal. By convention it is NEVER
     rewritten later — set `exit_note_at` to now. Later realizations go in `additional_notes[]`.
   - **`exit_trigger_type`** (optional) — a light tag for aggregation if one cleanly fits
     (`signal_stop`/`assumption_violated`/`regime_break`/`profit_take`/`rotation`/`discretionary`/
     `panic`/`other`); leave null if forcing a category would be false.
   - **`assumption_resolution[]`** — for each `assumptions[]` in the opening movement, ask: did it
     hold? → `validated` / `falsified` / `unresolved` (horizon too short is honest — use it).
   - **`catalyst_materialized`** (true/false/null) + note — did the attributed catalyst mechanism
     actually play out as described? (Distinct from whether the price moved your way.)

7. **Write the close movement** to `data/movements/mov_YYYYMMDD_<sector_id>_close.json`:
   - `action: "close"` (or `trim` for partial), `amount_eur` = proceeds, `qty`/`price`/`fees` of
     the sale.
   - `attribution[]`: carry the SAME catalysts/weights as the opening movement (so realized P&L
     attributes to the right catalysts in the ledger).
   - `trigger`: `profit_take` / `stop_hit` / `reconsideration` / `contradiction` as appropriate.
   - `outcome`: the captured fields from Step 6 (`exit_reason`, `exit_note`, `exit_note_at`,
     `exit_trigger_type`, `assumption_resolution`, `catalyst_materialized`,
     `signal_context.{exit_watcher_action,followed_signal}`). Leave `pnl`/`behavioral_flags`/
     `verdict` OUT — the engine computes them in Step 9.
   - Validate against `schemas/movement.json` (the hook auto-validates on save).

8. **Ingest** to refresh positions, the lake mirror, and the catalyst ledger:
   ```
   uv run python -m catalyx.store.movement_repo ingest --write-back
   ```

9. **Evaluate the experiment (P&L + verdict + behavioral flags).** This rebuilds the deleted
   right_reason_score on the Movement model — it computes realized gross + after-tax P&L (Spanish
   CGT), the right-thesis × right-reason VERDICT (skill / luck / variance / correct_invalidation),
   and the behavioral deviation flags, then merges them into the file's `outcome` block:
   ```
   uv run python -m catalyx.attribution.outcome evaluate <mov_id> --write-back
   ```

10. **Reflect (the self-learning step).** Surface the verdict + any `behavioral_flags` to the user:
    - `exited_intact_at_loss` / `discretionary_exit` → "sold with the thesis still holding — was
      this conviction or nerves?" `held_past_full_exit` → "you sat past your own full_exit stop."
      `overrode_signal` → "you went against the watcher."
    - If a flag fires, ASK for a one-line reflection and append it to `outcome.additional_notes[]`
      (`{at, note}`) — do NOT touch `exit_note`. Re-run Step 9 if you edited the file.

11. Confirm: realized P&L, tax due, net gain, the **verdict label**, updated book, and the catalyst
    ledger line (`lake_query ledger`). The experiment ledger view: `outcome report`.
