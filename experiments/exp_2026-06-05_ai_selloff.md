# Experiment — 2026-06-05 AI/Semiconductor Selloff

> **Run on:** 2026-06-06 · **Author:** pipeline stress-test · **Catalyst:** `cat_20260605_ai_capex_peak_scare`
> **Runs compared:** BEFORE `run_20260605_121805` → AFTER `run_20260606_110720`

## The event (researched, not invented)

On **2026-06-05** US equities sold off hard:

- **S&P 500 −2.64%** (closed 7,383.74), **Nasdaq −4.18%** — worst day since April 2025, ~$1T of cap wiped.
- **Trigger:** Broadcom failed to raise its AI-chip outlook → broke the "AI guidance ratchets up every quarter" narrative → dragged the whole semiconductor + memory complex (info-tech −2.9%).
- **Compounding macro leg:** a much stronger-than-expected May jobs report (172k vs ~88k) pushed the 10Y above 4.5% on Fed-tightening fears → **rotation OUT of long-duration tech INTO defensives** (staples, healthcare, utilities).

Sources: [TheStreet](https://www.thestreet.com/stock-market-today/stock-market-today-dow-jones-sp-500-nasdaq-updates-june-05-2026) · [CNBC](https://www.cnbc.com/2026/06/04/stock-market-today-live-updates.html) · [Bloomberg](https://www.bloomberg.com/news/articles/2026-06-05/s-p-500-s-record-win-streak-in-danger-as-ai-selloff-continues)

**Why this is the right resilience test:** the event **contradicts our single largest structural thesis**, `struct_ai_capex_supercycle` (intensity 86.9, the most-weighted catalyst across the book), and it hits the exact cluster our portfolios are concentrated in (~43% of the momentum book is semiconductors + AI).

## Hypothesis

1. Adding a `contradicts` catalyst on `struct_ai_capex_supercycle` should **lower** the catalyst_alignment of AI-exposed sectors and **demote** them in the ranking.
2. The portfolios should **rotate away** from the contradicted sectors.
3. The strategies should show **differentiated resilience** to the S&P drop.

## Method

1. **WebSearch** for the real driver (above).
2. Wrote `data/catalysts/cat_20260605_ai_capex_peak_scare.json`:
   - `relation_to_structural: "contradicts"` → `struct_ai_capex_supercycle`
   - `strength_score: 76`, `decay_halflife_days: 30` (sentiment/positioning shock, **not** a structural deactivation — Broadcom missed the *expectation* of an upgrade; actual hyperscaler capex was not cut, so the structural's `deactivation_conditions` are not met).
   - `is_priced_in_estimate: 0.50` (one-day drop priced the sentiment; "is AI capex peaking?" is unresolved).
3. `snapshot_repo record` → AFTER run `run_20260606_110720` (53 sectors).
4. `portfolio build-all` on the new run; compared holdings vs BEFORE.
5. `nav_engine model <p> --backtest-days 5` for all 4 strategies over the selloff window (06-01 → 06-06) vs **SPY**.

## Findings

### 1. Mechanical stability — `STABLE` ✅

Pipeline ingested the catalyst cleanly: 53 sectors re-scored, **3 rank-change events**, no crash, no cascade, no NaNs. The diff vs the prior run was contained and explainable.

```
rank_down  semiconductors_design   #3→#8  (Δ-5)
rank_down  semiconductors_memory   #4→#9  (Δ-5)
rank_down  semiconductors_foundry  #11→#15 (Δ-4)
```

### 2. Scoring propagation — correct but **heterogeneous**

The contradict applies `penalty = 15 × (decayed/100)` to the structural's modified score:
`struct_ai_capex_supercycle` 86.9 → **75.88** (−11.0) at decayed strength 73.5.

| Sector | catalyst_alignment before → after | composite before → after | why |
|---|---|---|---|
| `semiconductors_memory` | 86.9 → **75.9** (−11.0) | 70.8 → **67.5** (−3.3) | **pure-play** — only `struct_ai_capex_supercycle`, takes the full hit |
| `semiconductors_design` | ~88.9 → ~79 | 70.8 → **68.0** (−2.8) | pure-play |
| `semiconductors_equipment` | 89.4 → ~78 | 68.7 → **66.0** (−2.7) | pure-play |
| `ai_infrastructure_data_centers` | 96.8 → **96.7** (−0.1) | 69.4 → **69.4** (0.0) | **multi-catalyst** — noisy-OR anchored by `struct_energy_transition_grid` (95.0) and `struct_copper_datacenter_demand` |

> **Key structural finding:** the max-anchored noisy-OR aggregation makes a sector backed by
> several confirming catalysts **nearly immune** to a single contradicting one. The contradict
> knocked the AI structural down 11 points, but `ai_infrastructure_data_centers` barely moved
> because its alignment floor is set by the grid catalyst. **Resilience here doubles as
> insensitivity** — exactly the case where you'd want the system to *listen* to bad news.

### 3. Portfolio response — the momentum book is `BLIND` ⚠️

| Strategy | Ranked/weighted by | Change after catalyst |
|---|---|---|
| **momentum** | momentum | **IDENTICAL** — same 10 holdings, same weights (DRAM 11.3%, SEMI.L 11.04%, AIPO 10.78% …) |
| conviction | composite | minor: dropped `robotics_automation`, added `rare_earth_miners`; semis demoted but retained |
| equal_weight | composite | minor reshuffle, semis demoted within the 12 |
| low_crowding | composite | barely moved — **already** insulated (its `max_crowding` filter excludes the crowded semis entirely) |

> **The book most exposed to the selloff (momentum, ~43% semis) did not rebalance at all.**
> A `contradicts` catalyst cannot touch a momentum-weighted position, because momentum is a
> price signal and the catalyst only moves `catalyst_alignment`. By design, a bearish
> structural signal is invisible to the momentum strategy.

### 4. Market resilience — all four strategies `FRAGILE` to broad risk-off

NAV backtest of current holdings over the selloff window (2026-06-01 → 06-06), vs SPY (≈ **−2.77%**, matching the documented S&P −2.64%):

| Strategy | Return | vs SPY | Note |
|---|---|---|---|
| `low_crowding` | **−5.19%** | **+/−2.42** worse… i.e. lagged | *marginally best, but `IQQR.DE` failed to download → ~10% silently held as cash, flattering the loss* |
| `momentum` | **−6.22%** | −2.77 | tech tilt → tracks Nasdaq, not S&P |
| `equal_weight` | **−6.28%** | −2.77 | |
| `conviction` | **−6.44%** | −2.77 | most concentrated in the hit cluster |

> **All four underperform the index by ~2.4–2.8 points, and differ from each other by <1.3
> points.** They are long the same AI/cyclical/momentum cluster, so the "four strategies" give
> **illusory diversification** in a risk-off. The defensives that led the tape that day
> (staples, healthcare, utilities) are in *none* of the books — the momentum tilt is
> structurally anti-defensive and therefore underperforms in exactly this regime.

### 5. Data layer — `FRAGILE` (the weakest link)

Measuring the 1-day P&L from the snapshots was **not reliably possible**:

- The "06-05" momentum snapshot is **78% stale**: only **14 of 64** tickers carry `last_date = 2026-06-05` (33 are 06-04, 17 are 06-03). The momentum scores feeding the event-day run **mostly do not see the crash yet.**
- The universe **expanded** between snapshots (28 → 64 tickers); key holdings `DRAM`, `AIPO`, `ROKT`, `ASML` only appeared on 06-05 → **no prior price → their drop is uncomputable** from the snapshots. Only 29.5% of the momentum book had a price in both days.
- `IQQR.DE` is delisted in yfinance → silently dropped to cash by `holdings_nav`, distorting the `low_crowding` NAV with no warning surfaced to the ranking.

## Resilience scorecard

| Layer | Verdict | Evidence |
|---|---|---|
| Catalyst ingestion / scoring run | `STABLE` | 53 sectors, 3 contained rank events, no failure |
| Catalyst propagation (pure-plays) | `STABLE` | semis correctly demoted (−2.7 to −3.3 composite) |
| Catalyst propagation (multi-catalyst) | `FRAGILE` | noisy-OR absorbs the contradict; `ai_infrastructure` ~unchanged |
| Momentum strategy | `BLIND` | zero rebalance to a contradicting structural |
| Composite strategies | `STABLE` (weak) | rotate, but only modestly; same cluster remains |
| Cross-strategy diversification | `FRAGILE` | all 4 within 1.3pts, all −2.8 vs SPY |
| Momentum data freshness | `FRAGILE` | 78% stale on the event day |
| NAV / price plumbing | `FRAGILE` | silent cash on failed download; inconsistent universe |

**Bottom line:** the **deterministic scoring core is resilient** — it took a shock that
contradicts its biggest thesis and stayed stable, contained, and directionally correct. But
the **strategy as deployed is not yet resilient to a market-wide risk-off**, for three
reasons: the momentum book ignores the bad-news catalyst, the aggregation masks it for
multi-catalyst sectors, and the data feeding momentum lags the event by 1–2 days.

## Recommendations — keep the strategy intact, add the missing guards

Ordered by leverage. None of these change the conversational interface; all are additive
deterministic modules behind the existing `uv run python -m catalyx.*` contract.

1. **Risk-off regime overlay (highest leverage).** When a high-strength `contradicts` catalyst
   with broad geography (`GLOBAL`/multi-region) fires, scale **gross exposure** down and lift a
   cash buffer across *all* strategies — momentum included. This is the guard that would have
   trimmed the book *before* the lagged momentum data caught up. (New `risk_overlay` step in
   `record_run` / portfolio build.)
2. **Let `contradicts` reach the momentum strategy.** Add a *catalyst veto*: cap (or zero) the
   weight on any sector whose dominant structural was just contradicted above a strength
   threshold, even when the strategy ranks by momentum. Momentum stays the selector; the veto
   is a risk gate on top.

   > **But first, define how the pipeline tells noise from a regime change** — a `contradicts`
   > event may be a one-off (decays, veto auto-unwinds) or the first tremor of a structural break
   > (permanent rotation). The veto alone cannot discriminate; the discrimination must come from
   > the structural's own indicators. Design: [docs/DESIGN_catalyst_regime_discrimination.md](../docs/DESIGN_catalyst_regime_discrimination.md)
   > (3-state model `intact`/`contested`/`breaking`; this selloff validates as `contested`).
3. **Make the noisy-OR penalty-aware.** Today `contradicts` can only lower one structural's
   modified score and is then ignored by the max-anchor. Add a "worst-case drag" term so a
   strong contradict can pull the *aggregate* down — otherwise multi-catalyst sectors are
   structurally deaf to bad news (see `ai_infrastructure_data_centers`: 96.8 → 96.7).
4. **Freshness gate before a run.** Mirror the 7-day sector-study gate: **refuse or flag** a
   scoring run when >X% of the momentum snapshot is older than 1 trading day. The 06-05 run
   scored on 78%-stale prices and nobody was warned.
5. **Cross-strategy correlation cap.** Surface the combined exposure across the 4 books to the
   same primary structural / ETF cluster. "Four strategies, one bet" should be a flagged state,
   not a silent one.
6. **Fail loud on price gaps.** `holdings_nav` should *report* dropped tickers (`IQQR.DE`) and
   stabilize the snapshot universe so a 1-day P&L is computable, instead of silently converting
   a position to cash.

## Follow-up — regime-state prototype (built + verified)

The discrimination design ([docs/DESIGN_catalyst_regime_discrimination.md](../docs/DESIGN_catalyst_regime_discrimination.md))
is now prototyped: `catalyx/thesis/structural_monitor.py` (the fundamentals gate) +
`regime_state` emitted per sector by `catalyst_scorer` and persisted in the lake
`sector_snapshot`. Re-running this experiment's scenario through it (run `run_20260606_114437`,
53 sectors):

| State | n | Sectors |
|---|---|---|
| `intact` | 46 | — |
| `contested` | **7** | semiconductors_design / memory / equipment / foundry, robotics_automation, cloud_software_saas, real_estate_data_centers |
| `breaking` | **0** | — |

**Verdict: the selloff classifies as `contested`, not `breaking`** — the contradict reaches only
the AI-capex pure-plays (all reversible), multi-catalyst sectors (`ai_infrastructure`, `copper`)
stay `intact`, and nothing rotates permanently because `structural_monitor --all` shows all 8
structurals healthy (0 weak indicators, max intensity drop −3.0 ≪ −15 threshold). The pipeline now
*treats the one-off event as noise by construction*. The signal is additive — `catalyst_alignment`,
the composite, and `scoring_version` are unchanged; 88 tests green.

**Action layer — built, measured, and turned OFF by default.** The `contested`→haircut veto was
implemented in `portfolio.py` and A/B-tested on this scenario (momentum, vs SPY):

| Variant on `contested` | Selloff drawdown gap | 180d edge |
|---|---|---|
| flag-only (no action) | −3.46 | +26.93 |
| haircut → redistribute | −3.27 (**+0.19**) | +25.46 (**−1.47**) |
| haircut → cash (gross-down) | −2.30 (+1.16) | +19.97 (**−6.96**) |

Redistribution barely helps (the freed weight goes to other names in the *same* correlated
momentum cluster, which also fell); cash helps more but costs real edge. Acting on a one-off,
possibly-reverting event is poor risk/reward **and** off-objective (this is a monthly/conviction
book, not an active trader). **Decision:** the overlay defaults to **flag-only** — `regime_state`
is surfaced for the human review but does not move weights; the haircut/exclude actions are opt-in
(`risk_overlay:` in the profile YAML). The converged design: *the system recommends, it does not
trade; it reacts to persistence, not the event; and it rotates toward uncorrelated sectors.* Next
build: persistence (Layer 2) + correlation-aware recommendation (Layer 3) — see the design doc §8.5.
The penalty-aware aggregation (rec #3) stays parked (touches the core formula, unneeded for this goal).

## Reproduce

```bash
# state was captured at:  BEFORE run_20260605_121805  /  AFTER run_20260606_110720
uv run python -m catalyx.scorer.catalyst_scorer semiconductors_memory          # see the -11 contradict
uv run python -m catalyx.store.snapshot_repo events --run-id run_20260606_110720
uv run python -m catalyx.execution.portfolio show momentum                     # identical to before
uv run python -m catalyx.execution.nav_engine model momentum --backtest-days 5 # -6.22% vs SPY -2.77%

# regime-state prototype
uv run python -m catalyx.thesis.structural_monitor --all                       # all 8 healthy
uv run python -m catalyx.scorer.catalyst_scorer semiconductors_memory          # [contested]
uv run python -m catalyx.store.lake_query sql "SELECT regime_state, count(*) FROM sector_snapshot WHERE run_id='run_20260606_114437' GROUP BY regime_state"
```
