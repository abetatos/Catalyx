# PLAN v3 — Lean pipeline + rigorous rebalancing + anti-conservatism limits

> Status: PROPOSAL (2026-08-27). Nothing here is implemented yet. Each phase below is
> independently shippable and lists the exact files it touches.
>
> Three asks, in the user's words: (1) make the full pipeline cheaper in time and tokens and
> remove what is redundant or makes no sense; (2) replace the soft end-of-review metrics with a
> rigorous, metric-driven rebalancing view — the pipeline's ideal ETF composition vs the real
> book, side by side, with explicit sell / partial / add decisions and whether they pay after
> tax; (3) be "less AI": the LLM drifts conservative, so decisions must be rule-bound with hard
> limits the LLM can override only with a recorded, later-scored justification.

---

## 0. Diagnosis — where the tokens and the minutes go today

Measured on the repo as it is (`/catalyx-review scheduled`, 2026-07-28 run as reference).

### 0.1 Token budget of one scheduled review (estimate)

| Step | What it does | Cost driver | Est. tokens |
|---|---|---|---|
| 0/1 Scan | 14 C0 queries + 7 discovery + ~20 per-sector Pass-2 + 2 analyst-revision | ~40 WebSearches, each result 3–6k | 200–300k |
| 1.5 Lifecycle | Claude reads every catalyst, applies archive/dormant rules by hand | LLM doing deterministic work | 20–40k |
| 3 Studies | 8 studies × (6 searches + a 25 KB JSON) | 48 searches + 8×~12k output | 350–450k |
| 4 Dashboard | reads all structural YAML (147 KB) + events → writes `catalyst_dashboard_*.md` | pure re-read of Tier-1 files | 50–70k |
| 5 Heatmap | re-reads taxonomy (713 l) + weights (749 l) + etf_universe (759 l), then N per-sector `sector_scorer --crowd` re-runs, then a prose report with a "non-obvious finding" per top-5 | config re-read + redundant Bash + prose | 60–90k |
| 5b/5c post_run + score_run | deterministic, but `--all` scorers + 4×180-day backtests + rotation → verbose JSON into context | JSON dumps of 50 sectors | 30–50k |
| 6/7/8 Position reviews, exposure, tax | manual assumption checks per position via WebSearch | 4–8 searches | 40–60k |
| 8.5/9/12 Dashboard build, open-recs, gap review | mostly fine (user-facing) | — | 20–30k |
| Orchestration overhead | 551-line review skill + every sub-skill + CLAUDE.md (43 KB) loaded in main AND in every subagent | fixed | 60–100k |
| **Total** | | | **≈ 0.9–1.2 M tokens, ~45–60 WebSearches, 45–90 min wall** |

### 0.2 Things that are redundant or don't make sense (concrete, verified in code)

1. **A 25 KB sector study feeds the scorer exactly two fields.** `catalyst_scorer` reads only
   `active_catalyst_ids`; `snapshot_repo` reads only `narrative_maturity` (→ crowding). The other
   ~20 fields (`demand_drivers`, `supply_constraints`, `etf_analysis`, `historical_catalyst_performance`,
   `key_metrics_to_monitor`, `risks`, `narrative_notes`…) are prose that no code consumes and that
   `etf_universe.yaml` already holds (ETF analysis). ~45k tokens per study to produce a list and an enum.
2. **The heatmap 7-day freshness gate contradicts the movement-driven refresh policy.** Step 3 says
   "only refresh when the driver moved"; the heatmap skill says "any study > 7 days → BLOCK". In
   practice the gate is either violated every run or forces the expensive sweep. (Memory
   `study-refresh-criterion` already records the intended rule — the skill text doesn't.)
3. **Heatmap step 5 re-runs `sector_scorer <sid> --crowd N` per sector** to apply crowding from
   `narrative_maturity` — but `snapshot_repo.record` already does exactly that (`_crowding_for`).
   N redundant Bash calls per review, and the skill even re-reads three large config files the
   Python already owns.
4. **The catalyst dashboard report is dead weight.** `catalyst_dashboard_*.md` was last written
   2026-06-30 (skipped in the last two reviews in practice); the site dashboard + review §1
   already carry the same content. It re-reads 147 KB of YAML to format a table.
5. **The catalyst freshness gate is structurally unsatisfiable.** `exit_watcher` keys freshness on
   `status_last_reviewed`, but the ONLY write path is `/catalyx-update` per indicator. The scan's
   Pass-2 refresh (which is where each catalyst actually gets re-verified) is recommend-only and
   never touches the YAML. Result today: every held catalyst reads `very_stale` (83–87 days) even
   though the 07-28 review re-verified all of them. The discipline the user asked for on 08-04
   cannot be satisfied by the pipeline as wired.
6. **Lifecycle transitions (archive / dormant / invalidate / promote) are applied by the LLM**
   ("deterministic home is a future `catalyst_lifecycle.py`"). Pure rules, pure token waste,
   and drift-prone.
7. **yfinance is hit ~15 separate times per review** — `market_data`, `flow_data`, `dislocation`
   (twice: `--window` and `--anchor-sectors`), `entry_timing --all`, `exit_watcher`,
   `nav_engine` ×9 (4 backtest + 4 live + real), `technical_study`. No shared price cache. This
   is most of the wall-clock, it makes runs non-reproducible (a re-run an hour later sees different
   closes), and it breaks offline (today's `exit_watcher` run printed `pnl_pct_eur: None`).
8. **4 × 180-day backtest NAVs are rebuilt every run** although `track_record.yaml` states the
   backtest curve is "HYPOTHETICAL, never a track record". Only `live` + `real` matter.
9. **`--all` scorers over ~50 sectors** (`dislocation`, `entry_timing`) when the decisions only
   ever concern held sectors + top-15.
10. **Step 11 (watch-only triggers) is dead** — the taxonomy has no `watch_only` sectors.
11. **Three reports per review** (`catalyst_dashboard`, `heatmap`, `monthly_review`) + `heatmap_blocks/`
    with heavy overlap. One report is enough; the ranking table is a Python emit.
12. **Model portfolios recommend non-buyable vehicles.** `catalyx` book shows IHE / IBB / ARKG /
    BUG / CLOU / AIPO — US ETFs a Spanish retail account cannot buy (PRIIPs). `_primary_etf` prefers
    UCITS but falls back silently. A "target vs actual" comparison is impossible until each sector
    has a buyable primary vehicle.
13. **The heatmap's "⚠ PRE-CALIBRATION (0 closed positions)" banner will stay forever** because
    calibration was tied to closed positions. `snapshot_repo.validate_run` (rank IC, top-N spread)
    already exists and needs zero closes — it just isn't in the pipeline.
14. **CLAUDE.md (43 KB) is loaded by the main thread and by every subagent** (scan, N studies…).
    ~11k tokens × (1 + subagents) per review.
15. **`uv run python -m pytest` fails** (`No module named pytest`; `uv run pytest` can't resolve either
    from the current env). The "213 tests green" claim can't be reproduced from a fresh checkout — fix
    the dev-dependency group.

---

## 0.3 UPDATE 2026-08-28 — the universe was cut, and the first calibration ran

Two things changed after §0.1/§0.2 were written. Both move the plan.

### The universe shrank (done in a parallel session, `etf_universe.yaml` v2.0)

| | Before | Now |
|---|---|---|
| ETFs listed (non-buyable) | 96 (66) | **51 (0)** |
| Investable sectors | 53 | **26** |
| Sector studies per cycle | 53 | **26** |
| Catalysts to refresh | 18 | **12–13 active** (5 merged) |
| Indicators | 66 | **48 in active catalysts** |

**This completes §3.2** (buyable vehicle per sector) — the prerequisite for any "ideal vs ours"
table. Every listed vehicle is now UCITS/ETC and reachable from a Spanish retail account; the 66
US non-UCITS entries that could never be bought are gone, as are six dead tickers. Phase 2 no
longer has to do this.

It also **halves the remaining cost estimates** in §0.1: studies and per-sector scoring scale with
the sector count, so the ~350–450k study line becomes ~170–220k before the `core` split, and
~25–50k after it.

**Three bugs the cut exposed, now fixed:**
- `prices.universe_tickers()` parsed the OLD `{sector: {etfs: […]}}` shape and returned only
  benchmarks — silently, with no error. Regression-tested against the real file now.
- `freshness` audited merged/deactivated catalysts, manufacturing permanent stale rows for work
  nobody should ever do: **66 → 48 indicators audited, 57 → 40 stale**.
- `run_state`'s `TOP_N_RELEVANT = 15` meant "top quartile" at 53 sectors and "top 58%" at 26.
  Now adaptive (≈ a third, floor 5, cap 15 → **8** today), and the ranking is filtered to what is
  investable TODAY: 4 of the stored top-8 were sectors the cut removed, and the digest says so
  out loud instead of sending the review to study them.

### Calibration now exists — and the first reading needs care

`catalyx/scorer/calibration.py` (new) measures rank IC per dimension, persists to lake
`validation/calibration`, and runs inside `pre_run.sh`. It required no closed positions, only a run
old enough to have forward history — so **the "⚠ PRE-CALIBRATION (0 closed positions)" banner,
which could never have cleared, is replaced by a measured number** (§3.3 partly delivered).

First reading — 63-day equal-length windows, 26 investable sectors, buyable vehicles, IC shown
**as the composite uses it** (crowding is inverted there):

| dimension | mean IC | reading |
|---|---|---|
| composite | −0.12 | noise in every window |
| momentum | −0.27 | the only one to exceed the noise band, and only in 2 of 6 windows |
| catalyst_alignment | +0.09 | noise |
| flow_confirmation | −0.02 | noise |
| crowding (as used) | −0.21 | noise |

**Do not act on this yet, and note the correction to the first pass.** An earlier reading of the
same data gave composite −0.41 and momentum −0.44, and suggested "88% of the weight sits on
negative dimensions". That reading was inflated three ways, all now fixed: it used the OLD mixed
universe (mostly US ETFs this book cannot buy), it measured every run to a COMMON end date so the
windows nested inside each other, and it quoted `crowding_risk` raw instead of as-used (which
flips that dimension's sign). With equal-length windows on buyable vehicles the effect is roughly
a third the size and mostly inside the noise band.

The honest statement today: **no dimension shows reliable predictive power, and momentum is the
only one whose negative reading exceeds noise.** With n≈26, `se ≈ 0.20`, so the noise band is
±0.40 — and six runs weeks apart in one regime are **~1 independent observation, not 6**. That is
enough to justify watching momentum's weight (0.29) closely; it is not enough to change it.
`aggregate()` prints the effective-window count precisely so this cannot be over-read later.

**Consequence for Phase 2 §3.3:** `expected_edge_eur` must NOT be built on these ICs yet. Until
several independent windows accumulate, the rebalance engine should shrink the edge estimate
hard toward zero and print its `n` — a rebalance justified by a noise-grade IC would be exactly
the false precision the plan exists to remove.

---

## 1. Target — pipeline v3 numbers

| | Today | v3 target |
|---|---|---|
| WebSearches / scheduled review | 45–60 | **≤ 20** |
| Tokens / scheduled review | ~1M | **≤ 250k** |
| Wall-clock | 45–90 min | **≤ 15 min** (deterministic phases ≤ 3 min offline-capable) |
| Reports written | 3 + blocks | **1** (`review_<date>.md`) + JSON digests |
| LLM decisions that are actually rules | lifecycle, crowding, action verdicts | **0** — all in Python |
| Rebalance output | prose "Hold / consider trimming" | **table: target vs actual, € to trade, after-tax net edge, rule action, override slot** |

---

## 2. Phase 1 — Deterministic backbone (kills the waste; no LLM change yet)

### 2.1 Shared price cache — `catalyx/data/prices.py` (NEW)
- One table `market/prices` in the lake: `date × ticker` EUR-converted adjusted close + native close
  + FX, for the ETF universe + SPY + `^VIX` + FX pairs. `prices.refresh(as_of)` pulls ONCE per run
  (incremental — only missing dates).
- `nav_engine`, `dislocation`, `entry_timing`, `exit_watcher`, `technical_study`, `momentum_engine`
  get `price_fn = prices.read` by default (they are already injectable — tests prove it). yfinance
  becomes the cache's backend, not every module's.
- Wins: wall-clock (~10 network round-trips → 1), reproducibility (a run is pinned to a price date),
  offline runs, and the strict no-look-ahead source the backtest roadmap needs.

### 2.2 Catalyst lifecycle in Python — `catalyx/scorer/catalyst_lifecycle.py` (NEW)
- Implements Step 1.5b rules (event → archived / invalidated flag, structural → dormant / reactivate,
  event → structural promotion candidate) from `scoring_weights.yaml catalyst_lifecycle`.
- CLI `[--apply] [--json]`; `governance: auto` applies, `ask` prints the pending transitions for
  AskUserQuestion. Writes only `status` + a `lifecycle_log[]` entry. Invalidation stays a scan
  input (a reversal is evidence, not arithmetic) — the module consumes a `scan_deltas.json`.

### 2.3 Freshness write path — close the loop the 08-04 doctrine needs
- New cheap CLI: `catalyx.store.structural_catalyst_repo reviewed <id> --verdict intact|weakening|breaking --note "…"`
  → stamps `status_last_reviewed`, appends `review_log[]` (date, verdict, evidence one-liner).
  Same for events (`catalyst_repo reviewed`).
- The scan's Pass-2 refresh MUST call it for every catalyst it actually re-verified (it already
  produces the delta row — the stamp is the missing side effect). `exit_watcher.catalyst_freshness`
  then means what it says.
- Scan writes its deltas to `data/reports/scan_deltas_<date>.json` (machine-readable) so
  `catalyst_lifecycle` and `/catalyx-update --batch` consume them without re-reading prose.

### 2.4 `catalyx-update --batch` + scan → update in one hop  ✅ SHIPPED 2026-08-28
> `catalyx/store/indicator_update.py` (`set`/`batch`/`maturity`). Found a live bug while building
> it: the skill still told Claude to append the prior reading to the inline `value_history`, but
> schema 1.4 moved history to the lake and `intensity_engine` reads the lake first — so every
> hand-applied observation looked recorded and the empirical percentile never saw it. The batch
> path also enforces idempotence (a re-applied scan must not stack rows; the percentile weights by
> row count) and prints the deactivation conditions when a reading moves against the indicator's
> own `direction` or crosses `threshold_weak`.
- `/catalyx-update --batch data/reports/scan_deltas_<date>.json` applies all indicator updates
  from one file and recomputes every touched intensity once. Removes N conversational update calls.

### 2.5 Sector study split: `study_core` (machine) vs `study_deep` (prose, opt-in)
- Schema 1.x → add `study_type: core|full`. **Core** = the fields code reads or that a decision
  needs: `active_catalyst_ids`, `narrative_maturity` (+ one-line justification), `cycle_position`,
  `key_metrics_to_monitor[]` with current values, top-3 `risks`, `last_updated`. ≤ 3 KB, **2**
  WebSearches, ~8k tokens. A core refresh is what the review runs by default.
- **Full** (today's 25 KB dossier) becomes opt-in: `/catalyx-sector-study <id> --deep`, run when a
  sector is first added, when you're about to open, or quarterly.
- `etf_analysis[]` is removed from studies (it duplicates `etf_universe.yaml`, the single source).
- Heatmap freshness rule rewritten: **stale = driver moved since `last_updated`** (scan delta on any
  `active_catalyst_ids`) OR `> 45 d` (hard ceiling) — never "7 days" by itself. The gate warns and
  points at `study_core` refreshes; it does not block.

### 2.6 Kill list (delete, not "deprecate")  ✅ SHIPPED 2026-08-28
> Done: dashboard removed as a review step (digests instead); heatmap steps 2 (three config files,
> ~60–90k tokens/run) and 5 (per-sector `--crowd` re-runs that `snapshot_repo.record` already
> performs) removed; the per-top-5 prose block cut to one line + one finding for the book;
> `user_rank_multipliers` deleted; `heatmap_blocks/` + `llm_vs_pipeline_stability_*` moved to
> `experiments/` **with `snapshot_repo._BLOCKS_DIR` repointed** (moving them alone would have made
> `rationale_md` silently None); `post_run.sh` backtests dropped. **Step 11 was NOT deleted** as the
> plan proposed — with 27 sectors just retired to watch-only, a fired trigger is how one comes back.
> The sweep is what was killed: trigger checks are now findings-driven (~0 searches), not a
> 30-sector enumeration.
- `/catalyx-dashboard` as a review step + `docs/report_templates/catalyst_dashboard_template.md`.
  Replace with `structural_catalyst_repo summary --md` (already reads the YAML; add the alerts +
  next-review table there — pure formatting).
- Heatmap skill steps 2 (config re-read), 5 (per-sector `--crowd` re-runs), and the prose report.
  The heatmap becomes `lake_query ranking --md --top 15` embedded in the single review report.
  The "non-obvious finding" obligation moves to the review's executive summary (once, not per top-5).
- Review Step 11 (watch-only) — dead.
- `post_run.sh`: drop the 4 × `nav_engine model --backtest-days 180`; keep `live` + `real`.
  Backtest becomes `nav_engine model <id> --backtest` on demand.
- `score_run.sh`: `dislocation` + `entry_timing` take `--sectors` = held ∪ top-15 (add the flag;
  `--all` stays available).
- `data/reports/heatmap_blocks/`, `llm_vs_pipeline_stability_*.md` → move to `experiments/`.
- `user_rank_multipliers` (already deprecated one major version ago) → remove.

### 2.7 One pre-run script — `scripts/pre_run.sh` (NEW)
Deterministic, offline-capable, run FIRST (before any WebSearch):
```
prices.refresh → market_data → flow_data → freshness → exit_watcher --json
→ catalyst_lifecycle --json → movement_repo positions → lake_query ledger
→ emits data/reports/state_<date>.json (≤ 3k tokens): book P&L (EUR, FX-split), stale indicators,
  stale catalyst verdicts, exit-watcher actions, pending lifecycle transitions, held ∪ top-15 sector list
```
The review's first token spend is reading this digest — it tells the scan WHICH catalysts to
re-verify (held drivers + stale + top-15 drivers) instead of "every registered catalyst".

### 2.8 Slim the scan  ✅ SHIPPED 2026-08-28
> C0 14 → 6 (the four commodity and four macro queries returned overlapping result sets), discovery
> 7 → 3 (the pass is a net, not a census), and C2's fixed eight sector queries replaced by the
> `state_<date>.json` work list — `must_reverify` always, `should` budget permitting, `optional`
> never. Plus the missing output: the scan now writes `data/reports/scan_deltas_<date>.json`, the
> one file the three appliers read.
- C0: 14 → **6** queries (Fed+CPI, ECB/Europe, Trump/US policy, China, commodities in one
  copper/gold/oil query, geopolitics in one). Discovery: 7 → **3**. Pass-2 refresh: only the
  catalysts in `state_<date>.json` (held drivers, stale, top-15 drivers); the rest collapse to the
  "no change" line without a search. Analyst-revision: 2 (unchanged). Target ≤ 15 searches.

### 2.9 Fix the dev environment
- Add `[dependency-groups] dev = ["pytest", …]` to `pyproject.toml`; `uv sync --group dev`; CI runs
  `uv run pytest`. Tests for every new module above (price cache with an injected frame; lifecycle
  rules table-driven; `reviewed` stamp; `study_core` schema validation).

**Phase 1 exit criterion:** a scheduled review runs end-to-end with ≤ 20 searches and writes one
report; `exit_watcher` shows `fresh` for every catalyst re-verified in that run.

---

## 3. Phase 2 — Rigor: the rebalance engine and the metrics that justify a trade

> **STATUS 2026-08-28 — §3.1 · §3.2 · §3.3 SHIPPED.** `catalyx/execution/rebalance.py` +
> `rebalance_rules` in `scoring_weights.yaml` + rank-bucket calibration. Three design decisions
> were forced by running it against the real book, and all three are anti-conservatism fixes that
> the plan as written would have got wrong:
>
> 1. **The after-tax gate cannot bind on an unmeasured edge.** With ~1 independent window E[r]≈0,
>    so `net_edge = −(tax + spread)` and EVERY taxable sale fails — the gate would have silently
>    become a permanent ban on taking a profit. It now blocks only once calibration has
>    `min_windows_to_gate` (3) independent windows; below that it prints the cost and stands aside.
> 2. **The gate binds sales, not purchases.** A sale pays CGT now and irreversibly; a purchase out
>    of idle cash pays only the spread. Gating buys on a noise-grade edge would reimport the
>    conservatism through the back door.
> 3. **A stale catalyst verdict alone does not trim a winner.** `exit_watcher.reverify_required`
>    fires on staleness with no drawdown; halving a +13% position over a 60-day-old YAML is the
>    bias in a discipline costume. REDUCE now needs staleness **and** a drawdown tier — the
>    2026-08-04 doctrine as actually written.
>
> **§3.4 and §3.5 SHIPPED 2026-08-28 — Phase 2 is complete**, and so is the whole v3 plan.



### 3.1 `catalyx/execution/rebalance.py` (NEW) — "target vs actual, in €, after tax"

Inputs (all already in the lake or one call away):
- **Target**: the `catalyx` model book from the latest run (weights per sector) → mapped to the
  **buyable UCITS vehicle** (§3.2) → scaled from the €1000 notional to **deployable capital**
  (`total_capital_eur × deploy_ratio`, §4.2).
- **Actual**: real book marked to market in EUR (`nav_engine._eur_prices` via the price cache),
  cost basis, qty, plus cash = committed − cost.

Output per line (sector / vehicle):
| field | meaning |
|---|---|
| `target_pct`, `actual_pct`, `gap_pp` | weight gap in points of committed capital |
| `gap_eur` | € to trade to close the gap |
| `rule_action` | `BUY` / `ADD` / `HOLD` / `TRIM` / `SELL` — from the rule table (§4.1), not from judgment |
| `trade_eur` | the € the rule says to move (after deadband, min-ticket, cap) |
| `realized_gain_eur`, `tax_eur` | for a TRIM/SELL: `tax_engine` on the realized slice, YTD-aware |
| `cost_drag_eur` | tax + spread (from `etf_universe` spread bps) + fees |
| `expected_edge_eur` | see §3.3 — what the pipeline says the reallocation is worth over the horizon |
| `net_edge_eur` | `expected_edge − cost_drag`; **the trade prints only if > 0** — this is "¿renta vender?" |
| `override` | empty by default; Claude/user fills it ONLY to deviate (§4.3) |

Book-level: turnover %, active share vs model, tracking error vs model, cash after trades, catalyst
exposure after trades vs `correlated_catalyst_cap`, HHI (sector and catalyst).

CLI `uv run python -m catalyx.execution.rebalance [--strategy catalyx] [--json|--md]`; persists lake
table `portfolio/rebalance` by run_id so every recommendation is auditable later (did we follow it?
did it pay?).

### 3.2 Buyable vehicle per sector — `etf_universe.yaml`
- Add `primary_ucits` per sector (ticker on XETRA/LSE/Euronext, currency). `_primary_etf` returns
  it; the non-UCITS ticker moves to `us_reference` (used only as a price proxy when the UCITS has
  thin history). Sectors without a UCITS vehicle get `investable: false` in the taxonomy until one
  exists — a model book must be executable or it is not a target.
- This is the prerequisite for any "ideal composition vs ours" table; today 6 of the 10 `catalyx`
  holdings are not buyable.

### 3.3 Expected edge — make the score mean something in €
The composite is uncalibrated; the honest fix is to **measure its forward predictive power every
run**, not to wait for 50 closes:
- `snapshot_repo validate_run` already computes rank-IC and top-N-vs-rest spread for a past run.
  Wire it into `pre_run.sh` for every run older than 21 trading days → lake table
  `validation/calibration` (run_id, horizon, rank_ic, topN_spread, n).
- `expected_edge_eur(sector) = trade_eur × E[r_h | composite rank]`, with `E[r_h]` the empirical
  **rank-bucket forward return** from the calibration table (top-3 / 4-10 / rest), shrunk toward 0
  while `n` is small. Prints its `n` — "low confidence" is a number, not a vibe.
- Replace the eternal "PRE-CALIBRATION" banner with the live IC and its sample size.
- When the IC of a dimension is ≤ 0 over N ≥ 6 runs, the review flags the weight for revision
  (`scoring_weights.yaml` change is still a human commit).

### 3.4 Position & book metrics (Python, persisted, on the dashboard)  ✅ SHIPPED 2026-08-28
> `catalyx/execution/position_metrics.py` + lake `position_metrics` / `book_metrics`, last step
> before `rebalance` in `post_run.sh`. Two things the plan did not anticipate, both found by
> running it: (a) `portfolio_nav` stores **backtest, live and forward rows under one
> portfolio_id** — reading them sorted by date splices two curves and manufactures ±18% daily
> moves, which is where the first run's 95% "tracking error" came from; `_nav_series` now takes a
> mode. (b) Textbook **active share assumes both books sum to 100%** and ours do not (the real
> book is a % of total capital, the model is fully deployed), so it silently answers "how far
> apart are they" and not "how much of the model do we own". Both are reported, separately named.
> Live: model overlap **15.9%**, and `grid_infrastructure_utilities` shows a composite drift of
> **−37.4** — the model stopped believing that thesis long before the price said so.

Per position (lake `portfolio/position_metrics`, per run): EUR P&L split price/FX, drawdown from
peak, days held, return/vol since entry, **score drift** (`composite`/`rank` now vs
`score_context` at entry — a thesis-decay number), catalyst freshness, exit-watcher action.
Per catalyst: mark-to-market P&L (the ledger has only `invested_eur`).
Book: vol, beta to SPY, max DD, Sharpe (already in `build_site`), tracking error vs `catalyx`
model, active share, HHI, FX exposure %, deployed %.

### 3.5 Dashboard "Rebalance" tab  ✅ SHIPPED 2026-08-28
> Baked by `build_site.py` from lake `rebalance` + `position_metrics` + `book_metrics` +
> `override_log`; new `#/rebalance` route. English-only, recommend-only.

Two columns — **Pipeline target** vs **Real book** — with the `rule_action`, `trade_eur`,
`net_edge_eur` and the override slot per row, plus the book metrics strip. English-only, baked by
`build_site.py` from `portfolio/rebalance`.

---

## 4. Phase 3 — Anti-conservatism: hard limits, override log, rule scoring

> **STATUS 2026-08-28 — SHIPPED.** Thresholds frozen (the user delegated the call), override log
> scored and tallied by author, review language rules moved into the skill template. What the
> freeze changed from the drafts below, all of it in the anti-conservatism direction:
>
> | | draft | frozen | why |
> |---|---|---|---|
> | `deployment.base` | 0.60 | **0.70** | the ratio applies to capital ALREADY sized for risk in `track_record.yaml`; idle cash inside that envelope is an unchosen zero-return position, not prudence |
> | `deployment.floor` | 0.30 | **0.40** | binds only with nothing intact AND VIX > 30; it floors the TARGET and never forces a buy |
> | `profit_ladder` | 2 rungs | **1, rank-coupled** | the `rank_min: 0` rung trimmed a +50% winner the model still ranked #1 — the disposition effect with a threshold attached. Concentration is already bounded by `max_position_pct` + `trim_if` |
> | `sell_if_any.rank_out_of_top` | 12 | **10** | = `portfolios/catalyx.yaml max_positions`. 12 was chosen on a 53-sector universe; after the 08-27 cut to 26 it meant "sell the below-average half" |
>
> Live effect on the book: the rule now says deploy **70% → €7,000** against €3,046 actually at
> work — **€3,954 under-deployed**, up from €2,954 under the draft.
>
> §7.2 answered: **trailing rank is the default exit**, the ladder survives only rank-coupled.
> §7.3 answered: **both the user and Claude may override, both are scored**; `log_override`
> refuses `--author claude` once its tally is net-negative over ≥5 scored overrides. Making the
> user the sole author would not have removed the conservative deviation, only its record.
> §7.4 was answered by the 08-27 universe cut: no UCITS vehicle → not investable.

The observed failure: the LLM defaults to "Hold / watch / consider" and to leaving 70% cash. The fix
is not a prompt — it is moving every verdict into a rule table with numeric thresholds, and making
the LLM's only lever an *explicit, recorded, later-scored* override.

### 4.1 Rule table — `scoring_weights.yaml rebalance_rules` (single source of truth)
Draft thresholds (to be tuned by you, then frozen):
```yaml
rebalance_rules:
  deadband_pp: 2.0                 # |gap| below this → HOLD, never trade
  min_ticket_eur: 150              # smaller trades are noise + spread
  add_if: {rank_max: 5, gap_pp_min: 3, entry_timing_not: [falling], regime: intact}
  buy_if: {rank_max: 8, gap_pp_min: 4, maturity_not: [exhausted], regime: intact}
  trim_to_target_if: {overweight_pp_min: 4}
  profit_ladder:                    # partials are a RULE, not a mood
    - {gain_pct_min: 25, rank_min: 6, trim_fraction: 0.33}
    - {gain_pct_min: 50, trim_fraction: 0.33}
  sell_if_any:
    - {rank_out_of_top: 12, consecutive_runs: 2}
    - {regime: breaking}
    - {exit_watcher: exit}
    - {catalyst_status: [invalidated, dormant]}
  reduce_if_any:
    - {exit_watcher: reduce, catalyst_freshness: fresh}
  deployment_floor:                 # anti-cash-hoarding
    min_deployed_pct: 60           # when ≥ intact_top_sectors_min sectors are intact & rank ≤ 8
    intact_top_sectors_min: 5
    vix_pause_above: 30            # the only macro brake
  reverify_required_action: reduce_half   # STALE + drawdown-reduce → protective, same run, not "next cycle"
```
- `rebalance.py` evaluates these in a fixed precedence (SELL > REDUCE > TRIM > ADD/BUY > HOLD) and
  emits the action. No `watch`, no `consider`, no `monitor` exists in the output enum.
- `exit_watcher.watch` is mapped inside `rebalance` to `HOLD` or `REDUCE` using freshness + drawdown
  (the 08-04 doctrine); a `reverify_required` that is NOT resolved in the same review degrades to
  the protective action automatically.

### 4.2 Deployment ratio (replaces "cash by feel")
`deploy_ratio = clamp(base 0.6 + 0.05 × (n_intact_top8 − 5) − 0.2 × [VIX > vix_pause_above], 0.3, 1.0)`
Deterministic, printed with its inputs. Today's inputs (all top-10 intact, VIX normal) yield
≈ 0.85 → the rule would say the book is ~€5.5k under-deployed. That is the number the review must
confront, not "optional small add".

### 4.3 Override log — the only place the LLM (or you) can be conservative
- Any deviation from `rule_action` is written to lake `portfolio/override_log`
  (run_id, sector, rule_action, chosen_action, reason, author = user|claude).
- `pre_run.sh` scores past overrides once ≥ 21 trading days old: `override_edge_eur` = P&L of the
  chosen action − P&L of the rule action. The review prints the running tally
  (**overrides: N, net vs rule: ±€X**). If Claude's overrides lose money on aggregate, the review's
  rule is: Claude may no longer propose overrides, only the user.
- Step 6/9 of the review become: show the rebalance table → for each non-HOLD line AskUserQuestion
  **Execute (per rule) / Override (give reason) / Defer** — with "Defer" logged as an override too.

### 4.4 Language rules for the review (enforced by the report template, not by hope)
- Position rows: `rule_action` + `trade_eur` + `net_edge_eur`. Prose is limited to one line of
  evidence per row. Banned tokens in the action column: watch, monitor, consider, optional.
- The executive summary must state: deployed % vs deployment floor, number of rule actions, and
  the override tally.

---

## 5. Phase 4 — Context & maintenance hygiene

> **STATUS 2026-08-28 — SHIPPED. The cron was offered and DECLINED by the user** — the
> heartbeat stays a manual `bash scripts/pre_run.sh --check`. Do not re-propose scheduling it.
> CLAUDE.md **54.9 KB → 19.7 KB** (−64%; the module table moved verbatim to `docs/MODULES.md`,
> Recent Changes collapsed to 5 one-line rows pointing at CHANGELOG). The ≤15 KB target was not
> reached and deliberately so: what remains is rules that are load-bearing every run — broker
> reality, the one-driver rule, CGT, the AI-scoring rules — and a rule nobody opens is not a rule.
> Review skill **651 → 296 lines** (−55%). New `scripts/review_report.py` writes every
> deterministic section of the report from the lake, leaving Claude only the `<!-- CLAUDE: … -->`
> markers. `pre_run.sh --check` is the search-free weekly heartbeat: silent unless a rule action,
> a flagged position, a stale verdict, a pending transition, or a ±10% book move appears
> (exit 0 quiet / 10 attention) — run by hand, not on a schedule.
>
> Two things were deleted for being **actively wrong**, not merely verbose: the review's Step 1.5b
> spelled out the lifecycle transition rules for the LLM to apply, six weeks after
> `catalyst_lifecycle.py` took them over; and CLAUDE.md's "Data files state" block still described
> June ("theses/ ← vacío", one catalyst event). Stale documentation of a solved problem is worse
> than none — it invites re-solving it by hand.


- **CLAUDE.md 43 KB → ≤ 15 KB.** Move the module table to `docs/MODULES.md` (linked), keep
  "what/why/rules"; Recent Changes → 3 rows. Subagent prompts get a 2-line role brief, not the
  file. Saves ~8k tokens × (1 + #subagents) per review.
- **Review skill 551 → ~200 lines**: it orchestrates `pre_run.sh → scan-lite → update --batch →
  study_core (work list) → score_run.sh → post_run.sh (+ rebalance) → report → 2 AskUserQuestion
  steps`. Every rule that is now in Python is deleted from the skill text.
- **Report template**: one `review_<date>.md` generated by `scripts/review_report.py` from the JSON
  digests; Claude appends only the executive summary + non-obvious finding + override reasons.
- Adaptive cadence (`DESIGN_sell_signals.md §1b`) becomes a `CronCreate`/routine running
  `pre_run.sh` weekly: it only pings when a rule action, a stale verdict, or a ±10% move appears.
  Cheap because it is search-free.

---

## 6. Sequencing, effort, and what each phase buys

| Phase | Items | Effort | Payoff |
|---|---|---|---|
| 1 Backbone | price cache, lifecycle.py, `reviewed` stamp, `--batch`, study_core, kill list, pre_run.sh, scan-lite, pytest | ~3–4 days | −70% tokens, −75% wall, freshness gate works, reproducible runs |
| 2 Rigor | rebalance.py, UCITS vehicles, calibration table + expected edge, position/book metrics, Rebalance tab | ~4–5 days | the "target vs ours, € to trade, after-tax net edge" table; scores finally get a measured IC |
| 3 Limits | rule table, deployment ratio, override log + scoring, review language rules | ~2–3 days | removes LLM conservatism from the decision path; every deviation is recorded and scored |
| 4 Hygiene | CLAUDE.md, review skill rewrite, report generator, weekly cron | ~2 days | compounding per-session savings; discipline runs without a human remembering |

Phase 1 → 2 → 3 is the dependency order (rebalance needs the price cache and buyable vehicles;
the override log needs the rule engine). Phase 4 can interleave.

## 7. Open decisions — ALL ANSWERED 2026-08-28 (user delegated the call)
1. ~~The numeric thresholds in `rebalance_rules` (§4.1) and the deployment floor (§4.2).~~
   **FROZEN** — `rebalance_rules.frozen: "2026-08-28"`, four changes from the drafts, table in §4.
2. ~~`profit_ladder` on/off.~~ **Trailing rank is the default exit.** The ladder keeps exactly one
   rank-coupled rung (+25% AND rank ≥ 6); the rank-free rung is deleted. Pinned by
   `test_the_frozen_ladder_never_trims_a_position_the_model_still_leads`.
3. ~~Who may override.~~ **Both, both scored**, with an automatic suspension for Claude
   (`claude_suspended_if: {min_scored: 5, net_edge_eur_below: 0}`).
4. ~~Sectors with no UCITS vehicle.~~ **Non-investable** — done in the 08-27 universe cut
   (`sector_taxonomy.yaml` v2.0: investable 53 → 26, the rest watch-only with `retired_reason`).

Changing a frozen threshold later is a normal edit — `scoring_weights.yaml`, a line in
CHANGELOG.md saying why. What it must NOT be is a mid-review adjustment because a run produced an
uncomfortable number; that is the failure mode the freeze exists to make visible.
