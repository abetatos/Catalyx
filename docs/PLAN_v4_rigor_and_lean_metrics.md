# PLAN v4 — A closed target book, decisions priced in €, and half the pipeline cost

> Successor to `docs/PLAN_v3_lean_pipeline_rebalance.md`, which shipped in full (v3.1 → v3.5).
> v3 asked *"where do the tokens go and who decides the action?"* and answered both: the rule
> table, the deployment ratio, the override log, the pre-run/post-run chain.
>
> v4 asks the three questions the user asked on 2026-08-28, in their order:
>   1. **What is left in the pipeline that is redundant, or does not make sense?**
>   2. **The end-of-review metrics are not good enough.** Give me the ideal book and mine, side
>      by side, and tell me — with numbers — whether selling pays, and whether to sell partially.
>   3. **Be less AI.** An LLM defaults to whichever answer cannot be blamed. Force the limits.
>
> Everything in §0 was measured on this checkout on 2026-08-28, not estimated. Byte counts are
> `| wc -c` on the real stdout; portfolio numbers come from the committed lake.

---

## 0. Diagnosis

### 0.1 Where the tokens go now (post-v3)

v3 removed the searches. What is left is **raw JSON printed into a context window**. Measured:

| Command | Where it runs | stdout bytes | ≈ tokens |
|---|---|---|---|
| `sector_scorer --all` (no `--json`!) | heatmap step 4 | **97,275** | ~24k |
| `catalyst_scorer --all --json` | `score_run.sh` | **65,850** | ~16k |
| `entry_timing --all --json` | `score_run.sh` (unscoped fallback) | 12,700 | ~3k |
| `momentum_engine --json` | heatmap step 4 | 12,577 | ~3k |
| `dislocation --window 5 --json` | `score_run.sh` | 9,718 | ~2.4k |
| `sector_study_repo summary` | heatmap step 2 | 4,170 | ~1k |
| everything else (run_state, repos, exit_watcher, rebalance, position_metrics, ledger) | — | 15,300 | ~4k |
| **Total per review, before a single word of reasoning** | | **≈ 218 KB** | **≈ 53k** |

Two of those lines are the whole problem:

- **`sector_scorer` prints the full JSON even without `--json`.** `main()` renders the human table
  and then unconditionally prints `--- JSON output ---` plus `json.dumps(results, indent=2)`
  ([sector_scorer.py:298-300](catalyx/scorer/sector_scorer.py#L298-L300)). 97 KB, of which the
  heatmap uses ~8 columns × 26 rows — and then *reads the ranking back out of the lake anyway*
  (heatmap step 5). The JSON is written to the lake by `snapshot_repo record`. Nothing consumes
  the dump.
- **`catalyst_scorer --all --json` is 66 KB to deliver one column.** `score_run.sh` prints it so
  the review can read `regime_state` per sector — 26 values. The other 65.8 KB is per-catalyst
  event/decay breakdown that no downstream step reads.

**These two lines alone are ~40k tokens per run, ~19% of a v3 review, for zero information.**

### 0.2 Verified defects

Each of these was reproduced on this checkout. They are ordered by how much money the error touches.

---

**D1 — The benchmark comparison is printed with the wrong meaning, and the book is losing to SPY.**

`nav_engine` computes `vs_benchmark_pct = nav − benchmark_nav` (a *difference* in index points)
and then prints it labelled as the benchmark's own return
([nav_engine.py:834](catalyx/execution/nav_engine.py#L834)):

```
TWR (selection, vs benchmark) = -0.9555%   [SPY -5.3939%]
```

Read literally that says the book fell 0.96% while SPY fell 5.39% — a 4.4pp win. The lake says
the opposite:

```
date        nav      benchmark_nav   vs_benchmark_pct
2026-08-27  99.0445  104.4384        -5.3939
```

SPY went **+4.44%**; the book went **−0.96%**; the book is **5.39pp behind**. The misreading has
already propagated into prose — `CHANGELOG.md` v3.5 says *"TWR −0.96% (vs SPY −5.39%)"*, and
`review_report.py` §2 renders the same field under the header `vs bench pp` where it is correct,
directly above a real-book row whose CLI form says the reverse.

This is the single most consequential defect in the repo: **the number that answers "is any of
this working?" is currently displayed backwards in the place a human reads it.**

**A second defect hides inside the first.** `lake_query.portfolio_compare` takes the latest stored
row per portfolio and sorts by return — but the model NAVs are only rebuilt when `post_run.sh`
runs, so between runs they lag. On this checkout the model rows were stamped 2026-07-30 and the
real row 2026-08-27: the table put five curves side by side that stopped on different days, sorted
by a return measured over different windows. Rebuilding all five to the same date is one command
and changes the conclusion completely.

The full picture, all five curves to 2026-08-27, TWR:

| book | TWR | vs SPY (+4.44%) |
|---|---|---|
| momentum (model) | +3.89% | −0.55pp |
| catalyx (model, the flagship) | +3.88% | −0.56pp |
| low_crowding (model) | +2.90% | −1.54pp |
| equal_weight (model) | +2.61% | −1.83pp |
| **real (yours)** | **−0.96%** | **−5.39pp** |

**Execution alpha = −4.84pp.** The model books roughly tracked SPY. The real book lost 5.4pp to
SPY and 4.8pp to the model book it is supposed to implement — and note that TWR is measured on
*invested* capital, so this is not the cash drag (that is a separate €211, §4 C4). It is the
positions: the book holds two names the model dropped and has not opened five the model wants.

That number does not exist anywhere in the pipeline today, and it is the one that settles the
question v3 was built around. It says the rule table has been right and the deviations from it
have been expensive — which is an argument for **less** discretion, not more, and for closing the
gap between the target book and the real one rather than narrating it.

---

**D2 — The target book leaks 36% of its weight, so the anti-cash-hoarding rule is arithmetically
unreachable.**

`portfolio_holding` for run `20260728_103246` holds 10 names summing to exactly 100.0%.
`rebalance.build()` then drops the ones that are not investable today
([rebalance.py:414-420](catalyx/execution/rebalance.py#L414-L420)) — 4 names, **36.06% of the
model weight** — and computes `target_eur = weight_pct/100 × deployable` on the survivors. The
dropped weight is not redistributed. It evaporates.

Consequence, from today's real output:

```
RULE     deploy 70% → €7,000
targets  Σ target% = 44.7%  →  €4,476        ← the "ideal book" only asks for 45% of capital
ACTIONS  buys €2,250 · sells €1,454 → deployed after 38%
```

**Execute every single rule action, perfectly, and the book is at 38% against a rule that says
70%.** The deployment ratio — the module's flagship anti-conservatism device, whose docstring says
*"the book sitting 70% in cash was never a decision anybody made"* — cannot be satisfied by
following the table. The one place the design meant to force money to work is defeated by a
missing renormalization.

It gets worse as the ETF universe is pruned (in flight in a parallel session): every sector that
loses its buyable vehicle silently widens the leak.

---

**D3 — The rebalance table recommends vehicles that cannot be bought.**

Today's table proposes `BUY biotech_drug_development €891` against ticker **IBB**, and
`BUY ai_infrastructure_data_centers €567` against **AIPO**. Both are US non-UCITS — the exact
class `CLAUDE.md` calls regulatorily impossible for this account. Two independent causes:

1. The ETF on the row comes from `portfolio_holding.primary_etf`, frozen at the run that built
   the model book (2026-07-28, *before* the universe cut). The vehicle is never re-resolved at
   table time. `etf_universe.yaml` has had the right answers since 08-27: `BTEC.L` for biotech,
   `XAIX.DE` for AI infra.
2. `snapshot_repo._primary_etf` falls back silently: `pool = ucits or etfs`
   ([snapshot_repo.py:104](catalyx/store/snapshot_repo.py#L104)). No UCITS → return a US ticker
   rather than `None`. v3 §0.2 item 12 flagged this; it was never fixed, only routed around by
   marking whole sectors non-investable.

The sector-level `investable` filter is not the same guarantee as a buyable vehicle, and the
€-denominated action row is exactly where the difference costs something.

---

**D4 — The composite's measured skill is negative, and nothing in the pipeline reacts.**

`calibration` over 6 runs (63-day horizon, 26 sectors, vehicles remapped to what is buyable):

```
mean: composite=-0.12  momentum=-0.27  catalyst_alignment=+0.09  flow=-0.02  crowding=-0.21
mean top-5 spread: -5.21%
⚠ only ~1 non-overlapping window · |IC| < 2·se ≈ 0.40 in every window for 4 of 5 dimensions
```

Momentum — 29% of the composite — reads −0.35, −0.43, −0.47 in the three most recent windows.
That is not yet *evidence* (one effective window, se ≈ 0.20), but it is the only measurement that
exists, and the pipeline's response to it is: nothing. The composite still selects the book, the
softmax still sharpens the tilt (`sharpness: 0.25`), and the deployment rule still wants 70% of
capital pushed through that ranking.

The honest reading is not "stop investing". It is: **beta is worth owning, this specific tilt is
not yet demonstrated, and those are two different decisions that the pipeline currently fuses
into one.** §3 separates them.

---

**D5 — The after-tax gate is armed to fire backwards.**

`net_edge_gate` blocks a taxable sale whose expected edge does not cover CGT + spread, and stands
aside while `effective_windows < min_windows_to_gate (3)`. The bucket table it will use when it
arms:

```
top3: -0.056   mid: -0.003   rest: +0.779     (shrunk 0.14 toward zero)
```

The buckets are computed from the same ranking whose IC is negative, so `E[r|top3] < E[r|rest]`.
The moment the third independent window lands (~9 months from now, per the config comment), the
gate begins **blocking sales out of the bottom bucket and waving through sales out of the top** —
it will systematically invert the profit-taking rule, on a sample the module's own output labels
`noise`. The guard is a window *count*; it needs to be a significance test.

---

**D6 — Rank-based SELL fires on a rank that does not exist.**

Today's two SELL rows are `copper_miners` and `grid_infrastructure_utilities`, both with `rk = —`.
`rank_out_streak` counts `None` as "outside the top-10", and a sector's rank is `None` whenever it
is absent from that run's `sector_snapshot` — including when it was absent because the universe
changed shape. A missing rank is missing data, not a verdict.

> **CORRECTION (2026-08-28, on shipping B3).** The latent defect above is real. The evidence cited
> for it was not: those two rows are scored every run (copper #11, grid #14) and the `rk = —` in
> the table is the **model-book** rank, not the universe rank — blank for exactly the sectors the
> model dropped. No part of this book is currently decided by a missing value. The fix stands on
> its own merits; the "35% of the deployed book" figure does not.

---

**D7 — Report plumbing that renders empty or misleading columns.**

- §6 Catalyst exposure asks for `n_sectors` ([review_report.py:245](scripts/review_report.py#L245));
  `lake_query.catalyst_ledger()` returns `sectors` and `n_movements`. The column is `—` on every
  row, always. There is also **no % of capital and no comparison to
  `correlated_catalyst_cap.max_combined_pct`** — which is the only reason the section exists.
- §8 Overdue indicators asks for `check_frequency` / `overdue_by`; `freshness.overdue()` does not
  emit those keys. Two of five columns are blank on every row.
- §2 labels the benchmark column `vs bench pp` (correct) directly under a CLI that says the
  opposite (D1).

---

**D8 — Stale claims in the always-loaded context.** `CLAUDE.md`'s open-TODO list still names
*"v3 Phase 2 remainder: `portfolio/position_metrics` lake table + the dashboard Rebalance tab"*,
both shipped in v3.4. It is 19.7 KB loaded into the main thread **and every subagent**; a wrong
line there is paid for on every run and misdirects every agent that reads it.

---

**D9 — 27 sector studies (562 KB on disk) feed exactly two fields.** Known since v3 §0.2, and the
core/deep split fixed the *write* cost. The *read* cost is still there: the review's study step
reasons over full dossiers to produce `active_catalyst_ids` (a list) and `narrative_maturity` (an
enum). That is a `study_core` JSON of ~2 KB. The dossier is a human artifact and should be read
by a human, on demand, not by the pipeline every cycle.

---

## 1. Targets for v4

| Metric | Today | v4 target |
|---|---|---|
| Raw JSON into context per review | ≈ 218 KB / 53k tok | **≤ 40 KB / 10k tok** |
| Σ target% + cash% in the rebalance table | 44.7% + 69.5% = 114% (incoherent) | **exactly 100%, always** |
| Deployed after executing every rule action | 38% vs a 70% rule | **within 2pp of the rule** |
| Action rows naming a non-buyable vehicle | 2 of 6 | **0, enforced by a test** |
| "Does selling pay?" answered by | a shrunk E[r] that is statistically noise | **a breakeven the user can falsify** |
| Benchmark line | mislabelled, sign-inverted in prose | **SPY return AND the differential, both labelled** |
| Position sizing | composite-softmax, vol-blind (56% vol next to 19% vol at equal €) | **risk-budgeted, with risk contribution printed** |

---

## 2. Phase A — The closed book (this is the metrics ask)

The deliverable is one table that answers *"what should I hold, what do I hold, what do I do about
the difference, and does it pay?"* — closed, in €, after tax. Everything else in the review is
supporting evidence for this table.

### A1 — Normalize the target book; make cash an explicit row (fixes D2)  ✅ SHIPPED 2026-08-28

In `rebalance.build()`, after the investable filter, renormalize the surviving model weights so
they sum to 100% of the **deployable** amount:

```python
kept = sum(m["weight_pct"] for m in model)
if kept > 0:
    scale = 100.0 / kept                       # dropped weight is REDISTRIBUTED, never lost
    for m in model:
        m["weight_pct_effective"] = m["weight_pct"] * scale
```

Two guards, both necessary:

- **Cap the rescale.** If more than `max_dropped_pct` (proposal: **40%**) of the model book is
  unbuyable, the run does not silently concentrate the remainder into 6 names — it emits a
  `MODEL BOOK INCOMPLETE` row and falls back to the substitution rule below. A ranking whose top
  decile cannot be bought is a *scoring* problem, not a weighting problem.
- **Substitution before rescaling.** A dropped name's weight goes first to the next investable
  sector by composite that is not already in the book (`max_positions` is 10; the universe has
  26). Only the residual is rescaled across the incumbents. This keeps the book at its intended
  breadth instead of turning a universe cut into a concentration event.

Then re-cap with `water_fill` at `max_position_pct` (12% per `rebalance_rules`, distinct from the
profile's 16% — reconcile these; see D-3 in §6) and add the **CASH row**:

```
TARGET BOOK — pipeline, run 20260728_103246, €10,000 committed, deploy rule 70%
 #  sector                          vehicle    ideal%   ideal€    yours%   yours€      Δ€   action   trade€
 1  pharma_large_cap                IUHE.AS      14.6    1,459       5.6      560    +899   ADD       +899
 2  biotech_drug_development        BTEC.L       13.9    1,393       0.0        0  +1,393   BUY     +1,393
 3  semiconductors_design           SEMI.L       13.8    1,382       4.6      461    +921   ADD       +921
 …
 —  CASH (rule: 30.0%)                           30.0    3,000      69.5    6,954  -3,954   DEPLOY  -3,954
    TOTAL                                       100.0   10,000     100.0   10,000       0
```

**Test to pin it:** `abs(sum(target_pct) + cash_pct − 100) < 0.01` for every synthetic book,
including one where 90% of the model weight is unbuyable.

### A2 — Resolve the vehicle at table time (fixes D3)  ✅ SHIPPED 2026-08-28

Two changes, both small:

1. `rebalance.build()` re-resolves the vehicle from **today's** `etf_universe.yaml` instead of
   reading the frozen `portfolio_holding.primary_etf`. The stored ticker becomes
   `vehicle_at_run` (kept for audit, shown only when it differs).
2. `snapshot_repo._primary_etf` returns `None` when no UCITS entry exists, instead of falling
   back to a US ticker. The silent fallback is the root of D3 and it has already survived one
   plan cycle.

**Test:** no row whose `rule_action != HOLD` may carry a vehicle absent from `etf_universe.yaml`
or marked `ucits: false`. Same shape as `BANNED_ACTION_WORDS` — the rule is only real if something
checks it.

### A3 — The swap ledger: a breakeven, not a shrunk E[r] (*"¿renta vender?"*)  ✅ SHIPPED 2026-08-28 (v4.2)

`net_edge_eur` currently answers a question the data cannot support: it multiplies a trade by a
rank-bucket mean return that is one noisy window old, gets ±€1 on a €900 trade, and prints it
beside a real €12 tax bill. The number is not wrong, it is *unfalsifiable* — and per D5 it will
turn actively harmful when it arms.

Replace it with a **breakeven**, which needs no forecast and is checkable a month later:

```
SWAP  SELL copper_miners €1,062  →  BUY biotech_drug_development €891
  CGT on the realized slice   €12    (19% bracket, €62 gain, €0 YTD prior)
  spread, round trip          €4     (20 bps on €1,953 moved)
  ─────────────────────────────────
  total friction              €16    =  1.5% of the notional moved
  BREAKEVEN: biotech must outperform copper by +1.5% over the 63-day horizon.
  Evidence for that spread: rank-bucket top3−rest = −0.83pp over ~1 window (|IC| < 2·se) → NONE.
  The rule fires on rank-out (2 consecutive runs), not on an expected return. Friction is 1.5%;
  the thesis is that the rank signal is worth more than 1.5%. That claim is now testable.
```

Three properties this has and `net_edge_eur` does not:
- Every input is observable today (tax bracket, spread, notional). Nothing is estimated.
- The output is a **hurdle the user can accept or reject with their own view**, which is exactly
  the judgement a human should be making and the model should not.
- It is scored automatically 63 days later against the realized spread, feeding the same
  `override_log` machinery. The pipeline learns whether its rank signal clears its own friction.

`expected_edge` / `net_edge_eur` stay in the JSON and the lake (they are the input to A6's
calibration panel) but leave the decision table. `apply_gate` gates on the **breakeven vs a
significance-tested edge** (§3, B2) rather than on a point estimate.

> **Shipped.** `leg_friction` · `breakeven_pct` · `swap_ledger` · `rank_edge_evidence`; row column
> `net€` → `b/e%` in the CLI, the report §3b and the dashboard. Two corrections the estimate
> missed: CGT must be **pro-rated** across the legs one sale funds (charging each leg the full bill
> turned €20 of tax into €40 of phantom friction), and legs below `min_ticket_eur` are skipped —
> `size_trade` would refuse to print that order, so the ledger must not contain a rotation that
> cannot happen; the unpaired remainder lands in cash and the TOTAL line names it. The evidence
> line reports `NONE` on a thin sample and `ADVERSE` when top3 sits below rest, and is explicitly
> **not** a veto. `apply_gate` is unchanged pending B2 — it still steps aside below
> `min_windows_to_gate`, which remains correct.

### A4 — Risk-budgeted sizing, and risk contribution on every row  ✅ SHIPPED 2026-08-28 (v4.4)

Today `semiconductors_design` (vol **56%**) and `pharma_large_cap` (vol **19%**) are both €500
lines. They are not comparable positions: semis carries ~3× the risk per euro. The composite
decides *what* to own and *how much conviction*, and then the euro amount ignores the only input
that makes two euros comparable.

Add a vol-adjusted target with an explicit, bounded tilt:

```
w_i ∝ score_transform(composite_i) / σ_i^α          α ∈ [0, 1], proposal α = 0.5
```

`α = 0` is today's behaviour, `α = 1` is full inverse-vol. **0.5 is the recommended default**: it
halves the risk dispersion without turning the book into a low-vol fund, which would fight the
momentum mandate. Then `water_fill` at `max_position_pct` as now, so nothing about the cap or the
deadband changes.

Alongside it, print **risk contribution** per position, from the covariance of daily EUR returns
already available in `prices`:

```
RC_i = w_i · (Σw)_i / σ_p          Σ RC_i = 100%
```

Computed on today's book (daily EUR returns, 2026-06-05 → 2026-08-28, annualized):

```
etf         capital %   vol      risk contribution
4COP.DE        33.3%    43.2%    48.4%      ← a third of the money, half the risk
SEMI.L         16.7%    56.0%    28.4%
IQQH.DE        16.7%    30.5%    14.8%
USPY.L         16.7%    36.0%    12.2%
IUHE.AS        16.7%    17.8%    -3.7%      ← NEGATIVE: pharma is reducing book risk
                                 ─────
book vol 26.6%                    100.0%
```

Three things that table says and no current output does:

- **`copper_miners` (4COP.DE) carries 48% of the book's risk on 33% of its capital** — and it is
  the position the table wants to SELL on a rank that does not exist (D6). The largest single risk
  decision in the book is currently justified by a missing value.
- **`pharma_large_cap` has a negative risk contribution.** It is anticorrelated enough with the
  rest of the book to *lower* total volatility. The rule table wants to ADD to it, which is right —
  but for a reason nothing in the pipeline can currently articulate, and which would be the first
  thing to defend if the position ever came up for a trim.
- `effective N = 1/HHI = 4.3` on 5 positions is the honest concentration number, and it costs one
  division.

> **Shipped.** `position_metrics.risk_contribution` / `effective_n` and `portfolio.vol_tilt`
> (+ `_sector_vols`, cache-only). Measured on the rebuilt book the numbers came out at copper
> **52.9%** of risk on 34.9% of capital and pharma **−3.8%** — the plan's estimate stands, and the
> second bullet's reading of it needs the D6 correction above: the copper SELL fires on a real
> rank (#11), not a missing one. The tilt's effect on the model book is modest by design (largest
> move 3pp, nothing reordered): pharma +2.2, water +2.4, biotech +2.1, semis −3.0, space −2.4.
> A missing vol takes the MEDIAN of the ones present — never 0 (which divides into an infinite
> weight), never 1 (which reads as risk-free) — and `min_vol_pct: 5.0` does the same for a flat
> series.

### A5 — Partials, made explicit and tabulated  ✅ SHIPPED 2026-08-28 (v4.2)

The frozen ladder is one rung (`+25% AND rank ≥ 6 → trim 33%`) plus the overweight trim
(`≥ 4pp above target → trim back to target`). That is a sound *policy*; the problem is that the
review never shows the **distance to the next rung**, so a partial arrives as a surprise and a
human cannot plan around it. Add a `partials` block:

```
PARTIALS — distance to the next rung
  cybersecurity_commercial  +13.4%   rung at +25% & rank≥6   → needs +10.2% more, currently rank 6 ✓
  pharma_large_cap          +12.8%   rung at +25% & rank≥6   → needs +10.8% more, currently rank 1 ✗
  copper_miners             +6.2%    overweight rung: 10.6% actual vs 0.0% target → TRIM live
```

Also: **REDUCE currently means "halve"** (`reduce_fraction: 0.5`) and TRIM means "back to target"
or "33% of the line". Those three fractions are the entire partial-sale vocabulary and they are
never shown together. Print them in the table header so the reader knows the vocabulary before
reading the verdicts.

> **Shipped as `partial_rungs`, with one design change.** The plan's mock-up shows a single
> "next rung" per line; the first implementation did that and the overweight rung won every row,
> hiding the ladder completely — because the two rungs are measured in different units (points of
> TOTAL capital above target vs % gain ON the position) and a "nearest" across them is a category
> error. Both are now reported. The rank leg is phrased as what it means (`still a leader
> (rank 1 < 6)`, not `rank ✗`) since `rank_min` fires once the model has STOPPED leading a name,
> and a **missing** rank never satisfies it — the open B3 defect, which today lets two SELLs fire
> on `rk = —`.

### A6 — One measurement panel, with the benchmark stated correctly (fixes D1, D7)  ✅ SHIPPED 2026-08-28

Fix `nav_engine`'s print to name both numbers:

```
TWR (selection)  = -0.96%      SPY +4.44%      → vs benchmark -5.39pp
MWR (your money) = +7.16% ann.
vs cost (broker) = +1.53%
```

And add the number nobody computes today, which is the cheapest honest self-assessment in the
repo — **execution alpha**, the real book against the model book it is supposed to implement:

```
real -0.96%  ·  catalyx (model, live) -4.76%  →  execution alpha +3.80pp
```

Both lose to SPY; the human's deviations have been worth +3.8pp so far. That belongs in the
executive summary, not buried — it is the direct evidence on the question "should the rule table
or the human be deciding?", and right now it points the opposite way to the assumption baked into
v3.

Fix the two broken column mappings (D7) and give §6 the columns it exists for:

```
| catalyst | invested € | % of capital | cap % | headroom € | sectors |
```

### A7 — What lands where

| Change | File |
|---|---|
| Renormalize + substitute + CASH row + `max_dropped_pct` | `execution/rebalance.py`, `config/scoring_weights.yaml` |
| Vehicle re-resolution + `_primary_etf` returns None | `execution/rebalance.py`, `store/snapshot_repo.py` |
| Breakeven ledger; `net_edge_eur` out of the decision table | `execution/rebalance.py` |
| `α` vol-adjustment + risk contribution + effective N | `execution/portfolio.py`, `execution/position_metrics.py` |
| Partials block | `execution/rebalance.py` |
| Benchmark labels + execution alpha | `execution/nav_engine.py`, `store/lake_query.py` |
| Column fixes + exposure vs cap | `scripts/review_report.py` |
| Dashboard: closed book, RC bar, breakeven column | `scripts/build_site.py`, `site/app.js` |

---

## 3. Phase B — Honest edge: shrink the tilt, never the deployment

This is the rigorous answer to D4, and it is also the *anti*-conservative one. The conservative
response to "the model's IC is negative" is to hold cash. That is wrong twice: cash has a certain
negative real return, and one effective window is not evidence of anything.

**Separate the two decisions the pipeline currently fuses:**

| Decision | Justified by | v4 rule |
|---|---|---|
| **How much is at work** (beta) | equity risk premium — not this model's skill | `deploy_ratio`, unchanged, now actually reachable (A1) |
| **How the working capital is tilted** (alpha) | this model's *measured* rank IC | shrunk toward neutral by the measurement |

### B1 — λ: shrink the active tilt by measured skill  ✅ SHIPPED 2026-08-28 (v4.5)

```
w_final = w_neutral + λ · (w_model − w_neutral)

w_neutral = equal weight over the investable top-N of the same book
λ = clamp( IC_composite / IC_target , 0, 1 ) · n_eff/(n_eff + k)
```

With today's numbers (`IC = −0.12`, `n_eff ≈ 1`) → **λ ≈ 0**: the book becomes equal-weight over
the top-10 buyable sectors, fully deployed to the 70% rule. As independent windows accumulate and
IC turns positive, λ rises and the softmax conviction tilt returns — *earned*, not assumed.

This is strictly better than every alternative on the table:
- vs. today: stops paying concentration risk for an unmeasured signal.
- vs. holding cash: keeps the beta, which is the part with a positive prior.
- vs. abandoning the model: the model still selects the *universe* (which sectors), it just stops
  being trusted with the *sizing* until it has earned it. `catalyst_alignment` is the one
  dimension with a positive IC (+0.09, +0.36 in the newest window); λ lets that show up on its own.

A negative λ is **never** allowed to invert the book. An anti-signal on one window is noise, and
shorting your own ranking on n_eff=1 is a superstition with a minus sign.

> **Shipped** as `calibration.skill_lambda()` + `portfolio.skill_shrink()`, config
> `portfolio_weighting.tilt_shrinkage` (code default `False` = λ=1 = the pre-v4 book byte-for-byte).
> Measured today: composite IC **−0.050** over 3 complete windows but only **1 non-overlapping**
> one → **λ = 0.00**. Two corrections to this section as written: the IC is −0.050, not −0.12 (the
> figure above predated `composite_ic()` averaging complete windows only), and λ=0 does **not**
> make the book equal-weight — `vol_tilt` (A4) runs after the shrinkage, so the neutral book is
> neutral in *risk*, not in euros: top/bottom dispersion falls 2.58× → 1.82× rather than to 1.0×.
> Vol is a measurement, not a view, and it must keep applying when the view is withdrawn.
>
> Gross deployment is unchanged at every λ (both legs carry the same names at the same gross) and
> that is test-pinned, because "the model is unproven" quietly becoming "hold cash" is the exact
> conservatism the C-series exists to prevent. The regime haircut rides on the neutral leg, so a
> `contested` sector stays de-risked at λ=0. A `momentum`-weighted book shrinks by the momentum IC
> (−0.114), not the composite's — the shrink measures the column actually doing the ranking. λ is
> persisted (`portfolio_holding.tilt_lambda`, `rebalance.book_tilt_lambda`), so an old target book
> still says whether its dispersion was earned or assumed.

### B2 — Gate on significance, not on a window count (fixes D5)  ✅ SHIPPED 2026-08-28 (v4.3)

Replace `min_windows_to_gate: 3` with a joint condition:

```yaml
net_edge_gate:
  requires:
    min_independent_windows: 3
    min_abs_ic: 0.20            # roughly 1·se at n=26 — not "significant", but not noise-only
    ic_sign_must_be_positive: true   # a NEGATIVE IC disables the gate; it never inverts it
```

An unmeasured quantity must not become a veto (v3's insight, still right). A **measurably
inverted** quantity must not become a decision rule either — which is the new failure mode D5
identifies.

> **Shipped** as `calibration.composite_ic()` + `rebalance.gate_status()`. The measured composite
> IC is **−0.050 against se 0.200** over 3 complete windows, so all three conditions fail today and
> the gate names each one. The sign condition is asymmetric on purpose: a negative IC *disables*
> the gate, it never inverts it. Status is printed on the table, in report §3 and in the run
> digest, because a gate that does not fire is a decision too.

### B3 — A missing rank is missing data (fixes D6)  ✅ SHIPPED 2026-08-28 (v4.3)

`rank_out_streak` counts only runs where the sector was **scored and ranked outside the cut**. A
run in which the sector is absent from `sector_snapshot` breaks the streak and emits
`⚠ not scored in run X`. If a sector is absent from ≥2 of the last 4 runs, the row action becomes
`RE-SCORE` — a first-class, non-money action distinct from HOLD, because "we do not know" is a
real state and it currently masquerades as SELL on 35% of the book.

> **Shipped — and the claim in the last sentence was wrong about THIS book.** Neither of today's
> SELLs fires on a missing rank: `copper_miners` is scored #11 and `grid_infrastructure_utilities`
> #14 in every recent run. The `rk = —` that prompted the diagnosis is the **model-book** rank,
> which is blank for precisely the sectors the model dropped — i.e. blank on every row whose reason
> cites a rank. The defect in `rank_out_streak` (counting `None` as "outside the cut") is real and
> is fixed; the reading of these rows was not. The table now prevents the confusion: the column
> falls back to the universe rank as `~11`, and the reason names the number it fired on.
>
> **Found while fixing it:** `_rank_streaks` counted every `run_<date>_<time>` id, so two runs on
> the same afternoon were two "consecutive runs" and one day of iteration could manufacture a SELL.
> Only the last run per DATE counts now — which is what `rank_out_consecutive` always meant.

### B4 — Close the calibration loop on the decisions, not just the ranking  ✅ SHIPPED 2026-08-31 (v4.7)

`calibration` measures the ranking. It does not measure the **table**. Add
`rebalance.score_decisions()`, exactly parallel to `score_overrides()`: 63 trading days after each
recorded run, price every `rule_action` against holding, and tally by action type.

```
RULE SCORECARD (63d forward, 2 runs scored)
  action   n   mean fwd return   vs HOLD    verdict
  SELL     2         +2.1%        -2.1pp    the rule sold two winners
  ADD      2         -0.4%        -0.4pp    n too small
  BUY      2         +1.8%        +1.8pp    n too small
```

Overrides are already scored; the rules that overrides deviate *from* are not. Without this, the
override tally is a one-sided ledger — Claude's deviations are audited and the table's own record
is not. That asymmetry quietly makes the table unfalsifiable.

> **Shipped**, with three refinements the sketch above needed. **HOLD is the baseline, not a row**:
> "did the names go up" is beta and belongs to `deploy_ratio`; the only thing the table can claim
> credit for is beating the book left alone, so a BUY matching the HOLD mean scores 0.00pp.
> **The forward return is signed by the direction the rule moved money** — a SELL into a −5% move
> is the rule being right, and the sketch's unsigned "mean fwd return" column would have printed
> it as the worst row on the page. **A verdict needs n AND independent windows** (`min_n: 5`,
> `min_effective_windows: 2`), because five runs inside one 63-day horizon are one observation.
>
> Today: **nothing scoreable** — 24 recorded rows, no complete window. That is the honest output,
> and with no complete window the scorer does not fetch a price at all: for months the answer is
> "not yet", and paying for a download to print a fixed non-answer is a fixed cost on nothing.

---

## 4. Phase C — Anti-conservatism, tightened

v3 built the machinery (rule table, banned words, override log, suspension). The gaps left are the
places where inaction still has a costless exit.

### C1 — The deployment shortfall becomes an action row, not a footnote  ✅ SHIPPED 2026-08-29 (v4.6)

Today: `UNDER-deployed by €3,954` prints as a line of text nobody has to answer. After A1 the CASH
row carries an action (`DEPLOY −€3,954`) with a named destination:

```
CASH   30.0% ideal / 69.5% actual   DEPLOY €3,954 → the € is already allocated on the rows above;
       if you decline any of them, the decline is the override, not the cash.
```

**And a persistence rule:** if the book is more than `deployment.max_shortfall_pp` (proposal:
**10pp**) below the rule for `max_shortfall_runs` (proposal: **2**) consecutive recorded runs,
the review must either execute or log an override *naming the shortfall itself*, with the cash
drag priced (C4). A shortfall that survives two reviews without a written reason is the exact
failure mode the deployment ratio was built to make visible, and it currently survives silently:
the book has been at ~30% since 2026-06-16.

### C2 — Step 9 loses its costless default  ✅ SHIPPED 2026-08-29 (v4.6)

The current question is `Open now / Wait / Skip`. "Wait" is the option that is never wrong today
and never right in the record. Replace with a forced, priced choice:

```
biotech_drug_development · BTEC.L · rank 2 · ideal €1,393 (13.9%) · friction 0.2% · cap headroom €607
  [ Execute €1,393 ]  [ Execute a smaller size — state it ]  [ Decline — state the evidence ]
```

Every branch produces a logged decision. "Decline" writes an override with `--author user`, which
is what makes the deferral scoreable 21 days later. The current `Wait` writes nothing (the skill
*asks* Claude to log it — §Step 9 — but nothing enforces it, and the override log is empty after
three reviews with six non-HOLD rows).

### C3 — Detect the unlogged deviation instead of trusting the narrator  ✅ SHIPPED 2026-08-29 (v4.6)

An override is only logged if the LLM remembers to log it. Make it structural: at the **next**
run, `rebalance` compares the previous run's non-HOLD rows against the movements actually written
in the interval. Any row with no corresponding movement and no override becomes:

```
⚠ UNRECORDED DEVIATION — run 20260728 said SELL copper_miners €1,062. No movement, no override.
  Scored as a DEFER authored by `unrecorded` and tallied.
```

This closes the last hole: the cheapest way to be conservative today is to be quiet, and quiet is
currently free.

### C4 — Price the cash  ✅ SHIPPED 2026-08-29 (v4.6)

One line in the book strip, every run:

```
CASH DRAG  €6,954 idle since 2026-06-16 (73d) · SPY +3.03% over that window → €211 forgone
           (for scale: the friction that stops the copper→biotech swap is €16)
```

Not a reprimand — an entry in the same ledger as the €16 of friction that stops a swap. The
asymmetry the design keeps fighting is that friction is visible and inaction is not.

### C5 — Enforce the language in the generator, not in the prompt  ✅ SHIPPED 2026-08-29 (v4.6)

`BANNED_ACTION_WORDS` is enforced on `rebalance`'s own output. It is *not* enforced on the prose
Claude appends at the `<!-- CLAUDE: … -->` markers, which is where hedging actually lives. Add a
lint pass to `review_report.py --check`: scan the committed report for the banned words inside
action/recommendation sections and for unquantified hedges ("consider", "monitor", "keep an eye
on", "revisit next cycle") and fail with the offending lines. A "revisit next cycle" in prose is
a DEFER (`defer_is_an_override: true`) and must be a logged row, not a sentence.

> **Phase C shipped whole.** Measured on the first run: cash drag **€211** on €6,953 idle for 74d
> (against the smallest trade-blocking friction on the table, **€0.78**); shortfall **54.5pp** over
> 2 recorded review dates → breached; and **10 unrecorded deviations** — the whole of run
> 20260728's table, 2 SELLs + 3 ADDs + 5 BUYs, executed as nothing and recorded as nothing, now
> logged as DEFERs authored `unrecorded` and priced in ~21 trading days.
>
> Three implementation notes worth keeping. **The shortfall streak counts REVIEW DATES, not run
> ids**: a review can re-use an earlier score run, and the first cut excluded that run_id from the
> history — silently deleting the previous review from its own record, the exact quiet reset the
> rule exists to prevent. **Cash is idle from the last MOVEMENT, not the last run**: a review that
> recommends and is not executed must not restart the clock. **An unmeasurable benchmark leaves
> the drag `None`, never €0** — "the cash cost nothing" is a claim, and printing it because the
> price cache was cold hands inaction the free pass this phase removes.
>
> C5's lint is deliberately scoped to the sections where a decision is stated. Policing hedges in
> the macro-context sections would only teach the narrator to write confidently about what it does
> not know, which is the opposite failure and a worse one.

---

## 5. Phase D — Lighten (the token and time ask)

### D-a — Digest by default, `--json` on demand — ✅ SHIPPED 2026-08-28 (v4.1)

1. **`sector_scorer`: delete the unconditional JSON dump** ([sector_scorer.py:298-300](catalyx/scorer/sector_scorer.py#L298-L300)).
   The table is the human output; `--json` is the machine output. −97 KB.
2. **`catalyst_scorer --all`: add `--digest`** emitting one line per sector
   (`sector_id, catalyst_alignment, regime_state, n_confirm, n_contradict`) and use it in
   `score_run.sh`. −64 KB. Full JSON stays available per sector for the one case that needs it.
3. **`entry_timing` / `dislocation`: emit the digest by default**, full JSON behind `--json`.
   Both already persist to the lake; the review reads 4 fields from each. −18 KB.

Net: **≈ 218 KB → ≈ 39 KB**, no information lost — everything removed is either in the lake or
re-derivable by one scoped call.

> **Shipped, and better than estimated: ≈ 297 KB → ≈ 12 KB (≈ 74k tokens/run).** The estimate
> missed two calls. `intensity_engine` had the same unconditional dump (stripped, for consistency —
> it is not on the review's hot path). And the heatmap's separate `momentum_engine --json` pass
> (12.5 KB) re-scored the same snapshot to print a column `sector_scorer`'s composite already
> carries — removed from the skill, kept as a CLI for the raw 1m/3m/6m returns. The digests carry
> the buyable vehicle per sector, so a sector with no UCITS wrapper reads `— (not buyable)` where a
> decision would look for its ticker. Source-level test guards the four scorers against a
> reintroduced dump, since that regression fails nothing at runtime.

### D-b — One state file per run — ✅ SHIPPED 2026-08-28 (v4.1)

The review currently threads `state_<date>.json`, `scan_deltas_<date>.json`, three stdout digests
and the rebalance table through its context by hand. Have `post_run.sh` write a single
`data/reports/run_<date>.json` with the union of what the skill's steps consume, and have
`review_report.py` read it. The skill then says *"read run_<date>.json"* once instead of
carrying ~8 payloads across 12 steps. Cuts the orchestration re-statement, which is the part of
the review that grows with every step added.

> **Shipped as `catalyx/store/run_digest.py`**, called last in `post_run.sh`. Read-only over the
> lake and Tier-1 — no scorer, no fetch, nothing persisted but itself — so it assembles only what
> the run already computed. Missing sources degrade to `null` + a `missing[]` entry, never a
> traceback. `--write` writes the file; the default output is a ~25-line summary the review can
> re-print for free instead of re-running a CLI. **Deliberately NOT merged:** `review_report.py`
> keeps reading the lake directly rather than the digest, so the report can never quote a digest
> that went stale between the two calls.

### D-c — Steps that no longer earn their place  ✅ SHIPPED 2026-08-31 (v4.8)

| Step | Verdict |
|---|---|
| **4 — catalyst digests** (`structural_catalyst_repo summary` + `catalyst_repo summary`) | 4.5 KB, and both are already read by the scan in C1. **Delete from the review**; the scan carries them forward. |
| **5c — opportunities/regime** | Keep `dislocation` (cross-sectional, genuinely not derivable elsewhere). `structural_monitor --all` — **merge REJECTED 2026-08-28**: it is indexed by STRUCTURAL catalyst and carries `intensity`, the weak-indicator count and the drop against the degrade threshold; the digest is indexed by SECTOR. Only the regime label overlaps, and at 2 KB the rest is the cheapest fundamentals gate in the pipeline. Kept. |
| **11 — watch-only triggers** | v3 §0.2 called it dead; it is now 31 non-investable sectors and the skill's own instruction is to write "no watch trigger surfaced". **Delete the step**; fold into Step 12 (a watch trigger firing *is* a taxonomy/investability event). |
| **8.5 — local dashboard build** | Keep, but move **before** Step 6, not before Step 9: the user reads the book, then the positions get discussed. Costs nothing, changes the order in which the human sees evidence. |

### D-d — The study step reads a digest, not a dossier (D9)  ✅ SHIPPED 2026-08-31 (v4.8)

`sector_study_repo` gains `core <sector_id>` returning exactly the two consumed fields plus
`last_updated` and `narrative_notes` (~400 bytes vs ~20 KB). The review and the heatmap use it.
The full dossier is opened by a human, or by `/catalyx-sector-study --deep` when it is being
rewritten. 27 studies × 20 KB stops being a latent context bill.

### D-e — Context hygiene  ✅ SHIPPED 2026-08-31 (v4.8)

- Fix `CLAUDE.md`'s stale TODO block (D8) and add a one-line "measured state" row: deployed %,
  TWR vs SPY, effective calibration windows. Three numbers that stop every agent from having to
  rediscover them.
- The review skill's subagent briefs currently restate the pipeline order. After D-b they should
  name one input file and one output shape.

### D-f — Time, not tokens  ✅ SHIPPED 2026-08-31 (v4.8)

`pre_run.sh` fetches once and everything downstream reads the cache — that is already right. The
remaining wall-clock is `nav_engine live` × 4 strategies + `real`, run serially in `post_run.sh`.
They share the price frame; compute them in one pass over the cached frame. Also fix the dev
environment (v3 §2.9 item 15 — `uv run pytest` still needs the dependency group declared) so the
test suite is runnable from a fresh checkout without ceremony.

> **Shipped.** `live-all`: **10.4s → 1.9s**, identical numbers. The dev-environment defect was
> subtler than "the group is undeclared" (fixed in v3): with `CATALYX_PRICES_OFFLINE=1` exported,
> six tests that inject their own `fetch_fn` and touch no network **failed**. The obvious fix —
> let an injected fetcher override the offline switch — was REJECTED: a kill switch an argument
> can override is not a kill switch, and `test_offline_read_never_calls_the_backend` pins that
> strictness on purpose. The real defect is that the suite's result depended on the shell it was
> launched from, so `tests/conftest.py` clears the runtime switches per test. No production
> behaviour changed.
>
> D-d measured: **25,187 → 2,047 bytes** for one study. `age_days` is inside the digest because a
> stale study is worse than none, so the freshness cannot be one CLI call away from the value it
> discredits.

---

## 6. Open decisions

Per the standing instruction to decide rather than block, each carries a recommendation. Say the
word on any you want different; otherwise these are the values I will implement.

**D-1 — What happens to deployable capital the tilt cannot justify?**
With λ ≈ 0 (§B1), the book is equal-weight top-10 buyable sectors. An alternative is a **passive
core**: route the un-justified fraction `(1 − λ)` of deployable capital into a broad UCITS core
(a world/S&P UCITS accumulator) and run the tilt with the rest.
*Recommendation: **not yet.*** Equal-weight over 10 catalyst-selected sectors already captures
the beta, keeps the mandate (granular sectors, momentum/catalyst-driven) and keeps every position
measurable by the same machinery. Revisit if IC is still ≤ 0 after 3 independent windows — at that
point a core position is the honest conclusion, and it should be a deliberate decision, not a
default.

**D-2 — `α` (vol adjustment).** Recommendation **0.5**. Full inverse-vol (1.0) fights the momentum
mandate; 0 is today's vol-blind book.

**D-3 — `max_position_pct` is 12 in `rebalance_rules` and 16 in `portfolios/catalyx.yaml`.**
Recommendation: **12 wins**, and `portfolio.py` reads the rebalance value. Today's model book has
a 13.33% top weight that the rebalance cap would refuse — two ceilings that disagree means neither
binds.

**D-4 — Breakeven horizon.** Recommendation **63 days**, matching `calibration.DEFAULT_HORIZON_DAYS`,
so the breakeven and the measurement that later judges it use the same clock.

**D-5 — Does the rule scorecard (B4) get an automatic suspension like Claude's overrides?**
Recommendation: **no, not yet** — report it, do not arm it. A rule table that suspends itself on
two observations replaces one bias with a faster one. Revisit at ≥10 scored decisions.

---

## 7. Sequencing

> **✅ COMPLETE — 2026-08-31, v4.0 through v4.8. All ten steps shipped.**
>
> Two of this plan's own diagnoses were verified FALSE against the lake while implementing them,
> and both were corrected in place rather than quietly dropped: **execution alpha is negative**
> (the model books beat the real book — D1 had it backwards because it compared curves ending on
> different dates), and **neither current SELL fires on a missing rank** (D6's evidence was the
> MODEL-book rank column, blank by construction for the sectors the model dropped; the latent
> `rank_out_streak` defect was real and is fixed).
>
> One proposal was REJECTED with the reason recorded: merging `structural_monitor` into the run
> digest (it is indexed by structural catalyst and carries intensity + the weak-indicator count,
> not just the regime label the digest duplicates).
>
> What v4 leaves running needs TIME, not code: the calibration windows (1 independent, needs 3),
> the rule scorecard (no complete 63d window yet) and the override tally (10 pending, all
> `unrecorded`). Every one of them produces its first real verdict on its own clock.

| # | Phase | Contents | Effort | Buys |
|---|---|---|---|---|
| 1 ✅ | **A1–A2** | closed book, substitution, CASH row, vehicle resolution | ~0.5 day | the rebalance table becomes correct and executable; the deployment rule becomes reachable |
| 2 ✅ | **A6 + D7** | benchmark labels, execution alpha, report column fixes | ~0.3 day | the "is this working?" number stops being backwards |
| 3 ✅ | **D-a + D-b** | digest CLIs, one state file | ~0.5 day | measured **−96%** of the raw JSON bill (297 KB → 12 KB), compounding every run |
| 4 ✅ | **A3 + A5** | breakeven swap ledger, partials block | ~0.5 day | *"¿renta vender? ¿parciales?"* answered without a forecast |
| 5 ✅ | **B2 + B3** | significance gate, missing-rank handling | ~0.4 day | closes the two ways the table can fire on absent data |
| 6 ✅ | **A4** | vol-adjusted sizing + risk contribution | ~0.5 day | euros become comparable across positions |
| ~~7~~ ✅ | **B1** | λ tilt shrinkage | ~0.5 day | conviction becomes earned; deployment stays full |
| ~~8~~ ✅ | **C1–C5** | shortfall action, forced choice, unrecorded-deviation detection, cash drag, prose lint | ~0.7 day | inaction stops being free |
| ~~9~~ ✅ | **B4** | rule scorecard | ~0.4 day | the table becomes falsifiable, like the overrides already are |
| ~~10~~ ✅ | **D-c–D-f** | step deletions, study digest, context hygiene, dev env | ~0.5 day | the review gets shorter and the docs stop lying |

Order matters at the top: **1 and 2 are prerequisites for trusting anything else** — until the
target book closes and the benchmark reads correctly, every downstream metric is being compared
against a broken baseline. 3 is placed early because it pays on every subsequent run of the work
below it.

Steps 1–5 are the user's stated ask in full. 6–9 are the rigour that makes the ask hold up a year
from now. 10 is hygiene and can interleave anywhere.
