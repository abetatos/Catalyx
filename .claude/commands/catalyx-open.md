# catalyx-open

Register a capital **movement** that opens, adds to, or trims a position — attributed to the
catalyst(s) that justify it. A movement is a Tier-1 JSON file in `data/movements/`; **writing
the file IS the registration**. This skill is INDEPENDENT of the review cycle — run it whenever
you act, driven by a catalyst (big or small) or a deliberate reconsideration.

Usage:
- `/catalyx-open <sector_id>` — interactive: gather the trade, attribute it, write the file.

> For closing or taking profit, use `/catalyx-close`. This skill only handles capital going IN
> (`open` / `add`) or a partial reduce that is NOT a P&L event you want taxed yet (`trim` — but a
> trim that realizes gains should go through `/catalyx-close`).

## Steps

1. **Read** the contract + config:
   - `schemas/movement.json` — every field; required fields must be populated or explicitly null.
   - `CLAUDE.md` — movement/catalyst ID rules, EUR rule, conviction tiers.
   - `catalyx/config/sector_taxonomy.yaml` — confirm `<sector_id>` is `investable: true` (watch-only sectors cannot be a movement target).
   - `catalyx/config/etf_universe.yaml` — ETF options for the sector.
   - `catalyx/config/scoring_weights.yaml` — `conviction_tiers` + `correlated_catalyst_cap`.

2. **Determine the action.** `open` (new position) / `add` (increase existing) / `trim` (reduce,
   non-taxable book-keeping). Check current state:
   ```
   uv run python -m catalyx.store.movement_repo positions
   ```

3. **Attribute to catalyst(s) — the core of the record.** Identify which catalyst(s) drive this
   move. Each must exist:
   ```
   uv run python -m catalyx.store.structural_catalyst_repo summary
   uv run python -m catalyx.store.catalyst_repo summary
   ```
   Build `attribution[]` with weights that sum to 1.0. If one catalyst dominates, use a single
   entry at 1.0. Only split when there are genuinely independent drivers (e.g. a grid position
   driven by both `struct_energy_transition_grid` and `struct_ai_capex_supercycle`).

4. **Set `trigger` and `conviction`.**
   - `trigger`: `new_catalyst` (acting on a catalyst) / `escalation` / `contradiction` /
     `reconsideration` ("me lo he pensado mejor", no new external signal) / `rebalance` /
     `stop_hit` / `profit_take`.
   - `conviction`: `small` / `medium` / `high` — pairs with `conviction_tiers` (12/8/4%).

5. **Correlation check (flexible cap).** Look at exposure already attributed to the same
   catalyst(s):
   ```
   uv run python -m catalyx.store.lake_query ledger
   ```
   Read `correlated_catalyst_cap` (`max_combined_pct`, default 0.20; `enforcement`, default
   "warn"). If this move pushes combined exposure to a shared catalyst over the cap: **WARN** the
   user (combined %, shared catalyst, breach amount) and record the override reason in
   `risk_discipline.note`. Only refuse if `enforcement == "block"`.

5.5. **Entry-timing gate (recommend-only — answers "¿es buen momento o espero un poco?").** This is
   the micro execution window, SEPARATE from the fundamental decision (the composite/dislocation
   already said you want the sector). Run:
   ```
   uv run python -m catalyx.scorer.entry_timing <sector_id>
   ```
   It returns, from yfinance (no LLM drift): a `micro_timing_state`
   (`calm` / `stretched` / `falling_unstable` / `stabilizing`), the facts behind it (RSI, stretch
   vs MA20, vol regime, 5d return, drawdown), the market backdrop (^VIX + SPY 5d), any near-term
   **event overhangs** (discrete CatalystEvents with an `event_date` inside the window — e.g. a peer
   mega-IPO whose flow could dump the read-across name), and a `suggested_verdict`
   (`enter_now` / `scale_in` / `wait_stabilize` / `wait_event`).
   - **The verdict is advisory, not a block.** Present it to the user with one line of reasoning.
   - **You make the direction call on overhangs.** The module surfaces the *fact* that a near-term
     event touches the sector; whether it is an adverse whale-dump risk or a bullish spike is YOUR
     judgement — WebSearch the event before deciding (e.g. SpaceX IPO date, lock-up, allocation).
   - **Reconcile with dislocation:** a correction with intact fundamentals is a reason to BUY
     (dislocation opportunity lens), but entry_timing says don't deploy full size into UNRESOLVED
     tension — `falling_unstable` → wait for it to base; `stabilizing` → `scale_in` (enter in
     tranches); `wait_event` → wait past the discrete event. Offer the user: enter now / scale in /
     wait, and let them decide (this can also justify a smaller `conviction`/`amount_eur` now with a
     planned add later).

6. **Vehicle.** Pick the ETF with best AUM + spread (UCITS preferred for the Spanish investor). If
   no UCITS option has AUM > $200M, flag it.

7. **Gather the fill** from the user: `amount_eur` (the full EUR put in), `qty`, `price`, `fees`
   (Revolut Metal/Ultra ≈ 0 within the monthly franchise), `executed_at`. **`amount_eur` is the
   authoritative number** — qty/price may be estimates to reconcile with the broker.

8. **`risk_discipline` (optional, recommended for core moves).** For a meaningful position add the
   machine-checkable block: `invalidation[]` (pre-committed stops — specific, measurable, with
   `stop_price_level`/`stop_price_ticker` where applicable) and `assumptions[]` (what must stay
   true — binary, with `monitoring_source` + `check_frequency`). A small satellite move may omit
   it. This is what lets a future `invalidation_watcher` auto-flag the position and feeds the
   right-reason scoring at close.

9. **Do NOT capture `score_context` by hand.** Leave it null/partial — the ingest step fills it
   point-in-time from the score_run as-of `executed_at` (no look-ahead).

10. **Write** the file to `data/movements/mov_YYYYMMDD_<sector_id>_<keyword>.json` following the
    schema. Validate:
    ```
    uv run python -c "import json,jsonschema; jsonschema.validate(json.load(open('data/movements/<file>.json',encoding='utf-8')), json.load(open('schemas/movement.json')))"
    ```

11. **Ingest** — backfill point-in-time `score_context`, refresh the lake mirror + catalyst ledger:
    ```
    uv run python -m catalyx.store.movement_repo ingest --write-back
    ```

12. Confirm to the user: the new position, its weight in the book, and the catalyst ledger line
    it feeds (`movement_repo positions` + `lake_query ledger`).
