# catalyx-thesis

Draft, review, or close a CATALYX thesis. The sub-command is passed as an argument.

Usage:
- `/catalyx-thesis draft <sector_id>` — create a new thesis draft
- `/catalyx-thesis review <thesis_id>` — review an open thesis against current data and news
- `/catalyx-thesis close <thesis_id>` — close a thesis and compute ClosedThesis with attribution

## Step 0 — Rebuild DB index (all sub-commands)

```
uv run python -c "from catalyx.store import init_all; init_all()"
```

---

## draft <sector_id>

1. Read config files:
   - `CLAUDE.md` — rules on thesis IDs, tax model, assumption measurability
   - `schemas/thesis.json` — every field must be populated or explicitly null with reason
   - `catalyx/config/sector_taxonomy.yaml` — sector entry for `<sector_id>`
   - `catalyx/config/etf_universe.yaml` — ETF options for `<sector_id>`
   - `catalyx/config/scoring_weights.yaml` — conviction tiers

   Load runtime data from DB:
   ```
   uv run python -m catalyx.store.sector_study_repo get study_<sector_id>
   uv run python -m catalyx.store.structural_catalyst_repo summary
   ```
   Also read the most recent `data/reports/heatmap_*.md` for sector's current catalyst_alignment ranking.

2. Identify the PRIMARY structural catalyst driving this sector. State why it is not yet priced in (what the market is missing). If you cannot state a specific mispricing, the thesis should not be drafted — flag this to the user.

2.5. **PORTFOLIO CORRELATION CHECK — mandatory before drafting.**
   Load open and draft theses from DB:
   ```
   uv run python -m catalyx.store.thesis_repo summary
   ```
   For each open/draft thesis:
   - List its `catalyst.catalyst_event_id` (primary structural catalyst)
   - List its `position_size_pct_portfolio`
   Compare against the new thesis's primary catalyst:
   - If any open thesis shares the same primary structural catalyst: FLAG as correlated.
   - Compute: `combined_allocation = existing_open_pct + proposed_new_pct`
   - If `combined_allocation > tier_ceiling` (Tier 2 = 8%, Tier 1 = 12%):
     - **REDUCE proposed size** so combined ≤ ceiling, OR
     - **BLOCK the thesis** if the user has not explicitly authorized exceeding the ceiling
   - Always include `metadata.correlation_check` in the thesis JSON:
     ```json
     "correlation_check": {
       "correlated_open_theses": ["<thesis_id>"],
       "shared_catalysts": ["<struct_id>"],
       "combined_allocation_pct": 0.XX,
       "combined_at_tier_ceiling": true/false,
       "correlation_note": "<explanation>"
     }
     ```
   - Do NOT draft a thesis where combined allocation exceeds ceiling without documenting the explicit override reason.

3. Draft the thesis JSON following `schemas/thesis.json` exactly. All required fields must be populated.

4. For `assumptions[]`: each assumption MUST be:
   - A binary pass/fail statement (not a degree)
   - Tied to a specific named data source
   - Have a specific falsification criterion (number + duration)
   - Use `monitoring_source` from the enum in the schema
   Reject any assumption that cannot be checked with a yes/no from a specific source.

5. For `invalidation_conditions[]`: each condition MUST be:
   - Measurable with a specific number (not "if the market deteriorates")
   - Actionable: either `full_exit` or `review_and_reduce`
   - Checkable from a named source

6. For `vehicle`: always choose the ETF with best AUM + spread for the sector. If no UCITS option has AUM > $200M, flag this explicitly and suggest a non-UCITS alternative.

7. For `tax`: set `ytd_realized_gains_eur_at_entry` to 0 as default and add a warning that the user must update this before entry. To preview the tax impact at any gain target, use:
   ```
   uv run python -m catalyx.execution.tax_engine --gain <expected_pnl_eur> --ytd-prior <ytd_eur>
   ```
   Do NOT apply brackets manually in the thesis JSON — actual tax is computed at close time by `tax_engine.py`.

8. Set `status: "draft"`. The user must change to `"open"` after reviewing.

9. Write to `data/theses/thesis_YYYYMMDD_<sector_id>_<keyword>.json`.

10. Import the new thesis into the DB so it appears in `thesis_repo summary` within this session:
    ```
    uv run python -m catalyx.store.thesis_repo import-file data/theses/<thesis_id>.json
    ```

11. After writing, present a structured critique prompt: list the 3 most debatable decisions in the thesis and ask the user to validate them before opening.

---

## review <thesis_id>

1. Load thesis from DB:
   ```
   uv run python -m catalyx.store.thesis_repo get <thesis_id>
   ```
2. Load referenced structural catalyst from DB:
   ```
   uv run python -m catalyx.store.structural_catalyst_repo get <catalyst_id>
   ```
3. For each assumption in `assumptions[]`:
   - Use WebSearch to find recent news/data relevant to that assumption
   - Assess: validated / monitoring / at_risk / invalidated
   - Cite specific evidence (date, source, data point)
4. For each `invalidation_conditions[]`:
   - Check if the condition has been breached
   - Use WebSearch for price/news data if needed
5. Compute days since entry (if `status: "open"` and trade logged).
6. Produce a review report in the following format:

```
## Thesis Review — <thesis_id> — <date>

### Assumption Status
| ID | Statement | Status | Evidence |
|---|---|---|---|

### Invalidation Check
| ID | Condition | Breached? | Current value |
|---|---|---|---|

### Overall Assessment
[one paragraph: is the thesis still valid? What has changed?]

### Recommended Action
[ ] Hold — all assumptions intact
[ ] Add — strong confirmation, size up to tier ceiling
[ ] Reduce — 1+ assumption at risk, de-risk position
[ ] Exit — invalidation condition breached or thesis fundamentally changed
```

7. Do NOT write to file — present in chat for user decision.

---

## close <thesis_id>

1. Load thesis from DB:
   ```
   uv run python -m catalyx.store.thesis_repo get <thesis_id>
   ```
2. Ask user for: exit_date, exit_price, close_reason.
3. Read entry data from the thesis file (entry_price, entry_date, shares, currency).
4. Compute P&L and tax:
   - gross_pnl_eur = (exit_price - entry_price) × shares × fx_rate_at_exit
   - Get the current YTD realized gains baseline (may differ from `ytd_realized_gains_eur_at_entry` if other theses closed since entry):
     ```
     uv run python -m catalyx.store.thesis_repo tax-snapshot
     ```
   - Use `realized_gains_eur` from tax-snapshot as `ytd_prior`. Then call the tax engine:
     ```
     uv run python -m catalyx.execution.tax_engine \
       --gain <gross_pnl_eur> \
       --ytd-prior <realized_gains_eur> \
       --json
     ```
   - Use the engine output directly for `tax_due`, `effective_rate`, and `net_gain` in the ClosedThesis JSON.
   - Do NOT recompute brackets manually — the engine is the single source of truth for tax.

5. For attribution: ask user for benchmark return and sector index return over the holding period (or use WebSearch to find them).

6. Score assumption_validation: for each assumption, ask user: `validated` / `invalidated` / `indeterminate`. Also ask: did the primary catalyst mechanism actually materialize as described (`catalyst_materialized: true/false`)?

7. Compute `right_reason_score` using the thesis scorer (do NOT estimate manually):
   - Write the ClosedThesis JSON with `assumption_validation[]`, `attribution`, and `catalyst_materialized` filled in
   - Then call:
     ```
     uv run python -m catalyx.attribution.thesis_scorer data/theses/closed_<thesis_id>.json --json
     ```
   - Use the `right_reason_score` from the output. Copy into the ClosedThesis JSON.

8. Assign `matrix_cell`:
   - profit + thesis_quality ≥ 7 → "confirmed"
   - profit + thesis_quality < 7 → "lucky"
   - loss + thesis_quality ≥ 7 → "bad_luck"
   - loss + thesis_quality < 7 → "avoided_future"
9. Write `ClosedThesis` to `data/theses/closed_<thesis_id>.json` following `schemas/closed_thesis.json`.
10. Update the original thesis file `status` to `"closed"`.
