# catalyx-heatmap

Generate the CATALYX Sector Heatmap — ranks **every investable sector** in the taxonomy
by composite score. Sectors with a sector study score on all dimensions (catalyst_alignment
+ crowding from the study); sectors without a study still appear on a **momentum baseline**
(catalyst_alignment falls back to 0, crowding to the default). No investable sector is
invisible — the goal is full-universe coverage every cycle.

## Steps

1. Read `CLAUDE.md` for scoring methodology and rules.

2. **Load the compact digests. Do NOT read the config files.**
   ```
   uv run python -m catalyx.store.structural_catalyst_repo summary
   uv run python -m catalyx.store.catalyst_repo summary
   uv run python -m catalyx.store.sector_study_repo core --all
   ```
   `core --all` is the study digest: maturity · trend · age · catalyst count per sector, the only
   study fields anything downstream consumes (`narrative_maturity` → crowding_risk,
   `active_catalyst_ids` → the catalyst→sector map). A full study is ~20 KB of RESEARCH — the
   reason those values are what they are — and it is opened by a human rewriting the study, or by
   `sector_study_repo core <sector_id>` when one sector needs its notes, never 26 at a time.
   `sector_taxonomy.yaml` (713 lines), `scoring_weights.yaml` (749) and `etf_universe.yaml` (759)
   used to be read in full here — ~60–90k tokens per run to reproduce facts the Python already
   owns and applies: `sector_scorer` reads the weights, `snapshot_repo` picks the primary ETF,
   the repos read the taxonomy. Open one ONLY to answer a specific question the digests cannot
   (e.g. "which UCITS vehicles exist for sector X?" → grep that sector in `etf_universe.yaml`).

3. **Sector study freshness check (quality gate, not a coverage gate).**

   The heatmap ranks ALL investable sectors (via `sector_scorer --universe`, step 4).
   A sector WITHOUT a study is not excluded — it ranks on its momentum baseline and is
   flagged `⚠ no study (momentum-only)` in the table.

   **A study is stale when its DRIVER moved, not when the calendar moved.** Two triggers:
   - **Driver moved** — the scan flagged strengthen / weaken / invalidation on any catalyst in
     that study's `active_catalyst_ids`, or a lifecycle transition touched one. This is the real
     trigger: a study whose drivers did not move is still correct however old it is.
   - **Hard ceiling: `last_updated` > 45 days.** Not a refresh schedule — a backstop against a
     study nobody has looked at in a season.

   ```
   uv run python -m catalyx.store.sector_study_repo stale --days 45
   uv run python -m catalyx.store.sector_study_repo core --all --json   # for the driver test
   ```
   The driver test needs each study's `active_catalyst_ids` against the scan's deltas — that is
   what `core --all --json` carries, and it is the whole reason to read it instead of the files.

   **This gate WARNS; it does not block.** The old rule ("any study > 7 days → ⛔ HEATMAP
   BLOCKED") was unsatisfiable in practice and directly contradicted the review's own
   movement-driven refresh policy: on 2026-08-28 all 26 studies were older than 7 days, so the
   rule demanded 26 full refreshes before a ranking could be produced at all — precisely the
   2M-token sweep the policy exists to avoid. A blocked heatmap also produces NO ranking, which
   is strictly worse than a ranking with a few sectors flagged.

   List the flagged sectors with their reason and carry them into the review's study work list:
   ```
   ⚠ studies to refresh (driver moved / > 45d):
     <sector_id>   last_updated: YYYY-MM-DD (N days)  ← driver <catalyst_id> flagged <delta>
   ```
   Then PROCEED. Mark each such sector `⚠ stale study` in the ranking table so the reader
   discounts its full-dimension score. Sectors with NO study are never blocked either — they
   rank on the momentum baseline, flagged `⚠ no study (momentum-only)`, and are listed in the
   GAPS section (step 9) as study candidates.

4. **Run Python scoring pipeline (one call per module).**

   Refresh market data if stale (>3 days old):
   ```bash
   uv run python -m catalyx.data.market_data        # momentum → lake (data/lake/market/momentum) + compat JSON
   uv run python -m catalyx.data.flow_data --write   # flow → lake (data/lake/market/flow) + compat JSON
   ```

   Both modules dual-write to the parquet lake (the Tier 2 source of truth, committed to git)
   and a compatibility snapshot JSON. `momentum_engine` reads the lake by default. See
   docs/PLAN_lake_dvc_serving.md.

   The flow snapshot computes week-over-week shares_outstanding delta (ETF creation/redemption).
   On first run it initialises to 50 (neutral) — `flow_pct_1w` becomes meaningful from the second run onward.

   Then run the scoring pipeline:
   ```bash
   # Composite scores for all sectors — auto-loads the latest momentum + flow snapshots;
   # catalyst_alignment derived from sector studies. ONE call: the digest carries the momentum
   # percentile per sector, so the separate `momentum_engine --json` pass was scoring the same
   # numbers twice and printing 12 KB to repeat a column. It is still there for the raw
   # 1m/3m/6m returns when a narrative needs them: `momentum_engine` (table) or `--json`.
   uv run python -m catalyx.scorer.sector_scorer --all --digest
   ```
   Need a single sector's full breakdown? `sector_scorer <sector_id> --json`. Never `--all --json`
   (100 KB, of which the ranking reads four fields).

   These outputs are the authoritative scores. Do NOT recompute catalyst_alignment, momentum, or flow manually.

5. **Record the run, then read the ranking back from it.**

   `sector_scorer --all` uses the default `crowding_risk` 35. Do NOT re-run it per sector with
   `--crowd N` to apply each study's `narrative_maturity`: `snapshot_repo record` already does
   exactly that (`_crowding_for`, using the same `crowding_from_maturity` map in
   `scoring_weights.yaml`). Those N extra Bash calls produced a table that merely AGREED with the
   run being recorded — and drifted from it whenever a study changed in between.

   The recorded run is the ranking. Step 11 records it; read it back with:
   ```bash
   uv run python -m catalyx.store.lake_query ranking --top-n 15
   ```
   Crowding source per sector is implicit: `narrative_maturity` present → from the study;
   absent → the 35 default. Flag the latter `⚠ default (35)` in the table.

6. For `watch_only: true` sectors: compute trigger progress (N triggers met / total triggers).
   Do not score — only show trigger status.

7. Rank all investable sectors by `composite` descending. Include a `regime` column
   (`regime_state` from `catalyst_scorer`: 🟢 intact / 🟡 contested / 🔴 breaking) so a sector under
   a live contradiction is visible in the main table — but remember `contested` is watch-only and
   does NOT change its score or weight. The composite (schema 1.2) has 4 dimensions —
   `catalyst_alignment×0.35 + momentum×0.29 + flow_confirmation×0.24 + (100−crowding_risk)×0.12`
   (`valuation_relative` was removed: it was a constant-50 placeholder and no price-derived metric
   earned its weight). Note which dimensions are Phase 0.5 defaults:
   - `flow_confirmation`: ⚠ default (50) — no ETF flow data yet
   - `crowding_risk`: 🟢 from study or ⚠ default (35)

8. For the top 5 sectors, write ONE line each: the driving catalyst ids + the buyable UCITS
   vehicle (flag AUM < $200M). Nothing else.

   The old obligation here was a full prose block per top-5 sector — five "non-obvious findings",
   five ETF analyses, five "what would change the ranking" paragraphs, every run. The ETF analysis
   duplicates `etf_universe.yaml`, and four of the five findings were never read by anyone. **The
   non-obvious finding is now written ONCE**, in the review's executive summary, for the book as a
   whole. If a sector genuinely needs a narrative block, write it by hand into
   `experiments/heatmap_blocks/<sector_id>.md` — `snapshot_repo` picks it up as `rationale_md`.

9. Flag any sector where `catalyst_alignment > 75` but where the composite is pulling it down due to weak momentum or high crowding — these are "strong catalyst, bad timing" sectors worth monitoring.

10. Write report to `data/reports/heatmap_YYYYMMDD.md` following `docs/report_templates/heatmap_template.md`.

11-12. **Persist the run + compute opportunity/regime facts — ONE call.** After the report is
    written, record the run, register the report, and emit the regime/dislocation/entry-timing facts
    in a single command (the record/register output goes to a log; the scorer JSON prints for you to
    consume in step 12d):
    ```bash
    bash scripts/score_run.sh "monthly heatmap" data/reports/heatmap_YYYYMMDD.md
    ```
    This is the shared script `/catalyx-review` Step 5c also runs — the two used to narrate the same
    six commands. It writes to the parquet lake (data/lake/scores/, committed to git) — the durable,
    only source of truth (there is no database). Interpret its scorer output per step 12 below.

    `record` writes one `sector_snapshot` per sector (scores + rank + primary ETF + `regime_state`
    + the per-sector narrative block as `rationale_md`), tags the run with the `scoring_version`
    (hash of scoring_weights.yaml), and derives `rank_event` rows vs the previous run (which sectors
    entered/exited the top-N, how far each moved). It uses the SAME composite as the heatmap
    (crowding from `narrative_maturity` via `crowding_from_maturity` in scoring_weights.yaml), so
    the lake and the report never diverge. To check whether past rankings predicted returns, run
    `uv run python -m catalyx.store.snapshot_repo validate` (needs ≥2 runs separated in time).

12. **Regime watch + Opportunities & Rotation + Entry timing (recommendations, NEVER auto-trades).**

    The `scripts/score_run.sh` call in step 11-12 already ran the four scorers below (AFTER `record`,
    so `regime_state` is in the lake) and printed their JSON. Read that output — do NOT re-run them.
    Python computes the facts; the escalation and buy/rotate calls are yours (the hybrid model). See
    `docs/DESIGN_catalyst_regime_discrimination.md`.

    **a. Regime watch** — from `catalyst_scorer --all` (regime_state, review_recommended, persistence)
    + `structural_monitor --all` (fundamentals health):
    - `intact` → nothing to do. `contested` → **WATCH only, do not touch weights.** A single
      `clustered_one_shock` development is noise (e.g. "two consecutive-day drops confirm nothing").
      Only when `review_recommended` is true (multiple DISPERSED developments) OR a structural is
      `degrading` → WebSearch the macro context and **you** decide whether it is a regime change.
      Python never auto-escalates off an event count.
    - Time-independent: the verdict is identical whether this review runs daily, weekly, or monthly.

    **b. Opportunities & Rotation** — from `dislocation --window 5` (persists the lake `dislocation`
    table → dashboard Opportunities tab):
    - **OPPORTUNITIES** — fell hard but `intact` + catalyst-confirmed, drop mostly CONTAGION (high
      `contagion_fraction`, small `idiosyncratic_pct`): "the tape sold it, the thesis didn't break."
      For each, WebSearch to confirm the idiosyncratic residual has **no hidden cause** before
      treating it as a panic dip — a large residual is a RED FLAG to investigate, not a buy.
    - **DIVERSIFIERS** — healthy sectors with LOW correlation to the stressed cluster: where to
      rotate so you are not re-buying the same correlated bet (fixes "illusory diversification").

    **c. Entry timing (the execution window — complementary to dislocation's *whether*)** — from
    `entry_timing --all` (persists the lake `entry_timing` table by run_id → dashboard Overview), for
    the top-ranked + opportunity sectors, the *when* to enter:
    - `micro_timing_state` + `suggested_verdict`: `falling` ⇒ `wait_stabilize` (knife not
      based), `overbought` ⇒ wait for a pullback, `basing` ⇒ `scale_in`, `neutral` ⇒ no objection.
    - **Event overhang** ⇒ `wait_event`: a discrete CatalystEvent with an `event_date` in the window
      (e.g. a peer mega-IPO whose flow could dump the read-across name). The module surfaces the
      fact; the adverse-vs-bullish call is yours (WebSearch the event). Reconcile with dislocation:
      a dip with intact fundamentals is a reason to *want* it, but don't deploy full size into
      unresolved tension — wait to base, scale in, or wait past the event.

    **d.** Write an **"Opportunities & Rotation"** section into the heatmap report:
    - Regime watch: `sector · regime_state · persistence note (n developments · span · clustered?) · your read`
    - Opportunities: `sector · drawdown% · contagion% vs idiosyncratic% · catalyst_alignment · VERDICT (buy-watch / investigate / pass)`
    - Diversifiers: `sector · composite · corr-to-stressed · note`
    - Entry timing: `sector · micro_timing_state · RSI/vol/5d% · event overhang? · suggested_verdict`
    Everything is a recommendation for the user — nothing here is an instruction to trade.

## Rules

- Never mention a sector without its `sector_id` in backticks.
- Never recommend an ETF without stating TER, AUM, UCITS status, and spread.
- The non-obvious finding section is mandatory for each top-5 sector. If the reason a sector ranks high is obvious, the analysis adds no value.
- If two adjacent sectors score similarly, explain the differentiation explicitly.
- **Calibration banner is mandatory — and it must carry the MEASURED number, not a promise.**
  The old banner said "weights unvalidated (0 closed positions)" and could never clear: it was
  tied to closing 50 positions, years away at this cadence. Calibration needs no closes — only a
  run old enough to have forward history. Read the current reading:
  ```bash
  uv run python -m catalyx.scorer.calibration --offline
  ```
  Put its headline at the top of the report and above the ranking table, in this form:
  `⚠ CALIBRATION: composite rank IC <X> over <N> sector(s), ~<E> independent window(s) — <verdict>.`
  Rules for reading it honestly:
  - Quote the **as-used** IC (the tool already negates `crowding_risk`, which enters the composite
    inverted). Never quote a raw correlation for that dimension.
  - An |IC| below `2 × se` is **noise** — say "indistinguishable from noise", never "the composite
    is inverted". With ~26 sectors, `se ≈ 0.20`, so anything inside ±0.40 says nothing.
  - Quote `effective_windows`, not the run count: runs weeks apart in one regime are ~one
    observation. "6 runs" is not 6 samples.
  - Calibration NEVER moves a weight by itself. Changing `scoring_weights.yaml` is a deliberate
    human commit, made on several independent windows — not on one quarter.
- **Regime / opportunity outputs (step 12) are RECOMMENDATIONS for human judgement, never auto-trades.** Python computes the facts (`regime_state`, the persistence dossier, the contagion-vs-idiosyncratic split, correlations); the escalation call (`contested` → regime change?) and the buy/rotate call are yours, made with WebSearch macro context. A `contested` sector keeps its full score and weight — it is a flag to watch, not an action. Only `breaking` (measured fundamental degradation) warrants a rotation recommendation.

## Output format

Follow `docs/report_templates/heatmap_template.md`.
Filename: `data/reports/heatmap_YYYYMMDD.md`.
After writing, print a ranking table (sector, catalyst_alignment, top ETF) as a quick-reference summary.
