# catalyx-review

The ANALYTICAL cycle: facts → scan → apply → studies → score → rebalance → decisions. It
**recommends and never trades** — opening and closing are `/catalyx-open` and `/catalyx-close`,
run separately, whenever the user decides.

Usage:
- `/catalyx-review` or `/catalyx-review scheduled` — the full periodic review.
- `/catalyx-review event:<catalyst_id>` — a catalyst fired and you want to react now. Run only
  Step 0 · a **lightweight refresh of that one catalyst** (its keyword, strengthen/weaken/
  invalidation — NOT the full scan) · Step 2 for its indicators · Step 3 for the sectors it drives
  · Steps 5–5c · Step 6 for positions attributed to it. Skip 11–12 unless the event surfaces a gap.
  Say at the top of the report which trigger ran and why each skipped step was skipped.
- `/catalyx-review scheduled full-studies` — opt-in full-universe study sweep (quarterly at most).

## Pipeline order — mandatory

Each step produces what the next needs. `pre_run.sh` before any search; studies before the heatmap;
the heatmap's recorded run before portfolios; `post_run.sh` (which ends in **rebalance**) before any
position decision.

```
0    scripts/pre_run.sh          facts, work list, override tally — BEFORE any search
0/1  /catalyx-scan               C0 + discovery + per-catalyst refresh → scan_deltas_<date>.json
1.5  freshness gate              overdue indicators + lifecycle transitions, BEFORE scoring
2    apply the deltas            3 commands, one file
3    sector studies              movement-driven work list, fanned out to subagents
4    catalyst digests            two CLI summaries
5    /catalyx-heatmap            re-rank (+ score_run.sh: regime, dislocation, entry timing)
5b   scripts/post_run.sh         portfolios, NAV vs SPY, rotation, METRICS, REBALANCE table
6    open-position reviews       evidence per assumption; the ACTION comes from the table
7    catalyst exposure           combined per catalyst vs the cap
8    tax snapshot YTD
8.5  dashboard                   build + serve locally — the user looks BEFORE Step 9
9    open recommendations        AskUserQuestion per candidate
11   watch-only triggers         findings-driven, never a sweep
12   taxonomy gap review         AskUserQuestion per proposal
```

**Why 1.5 gates scoring:** it used to run after the heatmap recorded the run, which baked stale and
spent catalysts into the recorded scores — sectors ranking top-10 on indicators 100–500 days old.
Prune first, then score.

## Execution model — the main thread is a thin orchestrator

Bulk WebSearch and many-file phases run in **subagents that return only the step's digest**; the
main conversation holds compact summaries and runs the two user-facing decisions (subagents cannot
ask the user). The subagent Writes its files directly — the file IS the registration.

| Step | Where |
|---|---|
| 0/1 scan | **SUBAGENT** (`general-purpose`, follows `catalyx-scan.md`) → C0 bullets + refresh deltas + new gaps |
| 3 studies | **SUBAGENTS**, one per few sectors, `run_in_background` → return only the written path |
| 4/5/5b digests, heatmap, portfolios, NAV, rebalance | **SUBAGENT** → ranking digest + vs-SPY line + rebalance table |
| 5c opportunities/regime | **SUBAGENT** → the four tables |
| 6 position reviews | **SUBAGENT** → one row per position |
| 0 · 1.5 · 2 · 7 · 8 · 11 | MAIN — one CLI each, small output |
| **9 · 12** | **MAIN** — AskUserQuestion |

---

## Steps

### Step 0 — Facts before questions

```bash
bash scripts/pre_run.sh          # add --offline to serve the warm price cache
```
Writes `data/reports/state_<date>.json`: book P&L, exit-watcher actions, stale indicators and
verdicts, pending lifecycle transitions, the tiered work list (`must` / `should` / `optional`), and
the **override tally**. Read the tally now, not after Step 6 — the review has to know what its own
last deviation cost before it proposes another one. Everything downstream is scoped to the work
list: a review that opens with searches discovers its own book's problems last.

### Step 0/1 — Scan (macro front door)

Delegate to a subagent following `.claude/commands/catalyx-scan.md`. It returns the C0 digest, one
delta per catalyst **on the work list**, and any new gaps/events, and writes
`data/reports/scan_deltas_<date>.json`. Budget ≈ 6 C0 + 3 discovery + one per `must_reverify` + 2
analyst-revision ≈ **15 searches**. Catalysts not on the work list collapse to one "no change" line
and are **not stamped** — freshness must reflect what was actually checked.

Never read `sector_taxonomy.yaml` during the discovery pass: reading it first biases the search
toward known sectors, and finding what the taxonomy misses is the entire point of the pass.

### Step 1.5 — Freshness & lifecycle gate

```bash
uv run python -m catalyx.scorer.freshness --json          # overdue indicators → Step 2 targets
uv run python -m catalyx.scorer.catalyst_lifecycle        # dry run; --apply happens in Step 2
```
Thresholds are per-indicator **native cadence** (`check_frequency` is the single source of truth;
a `⚠mislabel` row means the YAML is wrong — fix the YAML). Every transition rule — archive a spent
event, dormant a weak structural, promote a repeated event — lives in `catalyst_lifecycle.py`. Do
not re-derive them here; read its output and report it. Reversals come only from scan evidence,
never inferred.

`last_date` is the date the value was **observed**, not the date it was entered. A fresh value with
a stale `last_date` is the main false-positive source.

### Step 2 — Apply the scan, in three commands

One file, three consumers. No per-indicator conversational calls, no hand-edited YAML, one intensity
recompute per touched catalyst:
```bash
D=data/reports/scan_deltas_$(date +%Y%m%d).json
uv run python -m catalyx.store.indicator_update batch "$D"                 # values + intensity
uv run python -m catalyx.store.catalyst_review   batch "$D"                # status_last_reviewed
uv run python -m catalyx.scorer.catalyst_lifecycle --deltas "$D" --apply   # status transitions
```
Report: N observations · M catalysts recomputed (any intensity Δ > 5) · any `⚠` deactivation notice
· any lifecycle transition. **The second command is what makes the exit-watcher freshness gate
work** — before it existed a review could re-verify the whole book and every catalyst still read
`very_stale` the next day.

### Step 3 — Sector studies (prerequisite for the heatmap)

**Movement-driven, not a sweep.** A deep study costs ≈ 45–50k tokens / 6 searches, and most of a
full sweep re-derives an unchanged file. A sector without a fresh study still ranks on its momentum
baseline, so nothing is missed by leaving it. Study a sector this cycle only if:
1. **it holds an open position** — the live book's theses stay current, always;
2. **its driver moved** — the scan flagged its catalyst strengthen/weaken/invalidation, or surfaced
   a new event or gap touching it;
3. **it is an entry candidate AND stale** — top-N by momentum or prior alignment, study missing or
   > 7 days old (these are the sectors Step 9 could name);
4. **it has never been studied** and is a plausible candidate.

The 7-day gate is a FLOOR, not a trigger — never re-study a sector just because 7 days passed if its
driver did not move. A STALE study is worse than none: it injects confident, wrong full-dimension
scores.

```bash
uv run python -m catalyx.scorer.sector_scorer --universe --json   # investable ids + momentum rank
uv run python -m catalyx.store.sector_study_repo stale --days 7
uv run python -m catalyx.store.movement_repo positions
```
Report the work list, why each sector is on it, and the count skipped as unchanged. Fan the list out
to background subagents following `catalyx-sector-study.md`.

### Step 4 — Catalyst state

```bash
uv run python -m catalyx.store.structural_catalyst_repo summary
uv run python -m catalyx.store.catalyst_repo summary
```
There is no separate dashboard report — `/catalyx-dashboard` still exists on demand but is not a
step of the review.

### Step 5 / 5b — Score, then rebalance

Follow `catalyx-heatmap.md` (it runs `scripts/score_run.sh`, records the run, and emits the regime /
dislocation / entry-timing facts). Then, once `sector_snapshot` is in the lake:
```bash
bash scripts/post_run.sh
```
Portfolios → NAV live vs SPY per strategy → real-book NAV → rotation anchored to held sectors →
**position & book metrics** → **rebalance**. The verbose parts go to a log; the NAV digest, the
metrics table and the whole rebalance table print to stdout. The metrics come first because they
explain the rows rebalance is about to act on — the EUR P&L split into price vs FX (a non-EUR
vehicle is two positions and only one was a thesis), drawdown from the position's own peak, and
**score drift** vs the score the pipeline gave that sector on the day it was bought. Report each strategy's `vs_benchmark_pct` and the rotation anchor. Editing the chain means
editing `post_run.sh` — it is the single source of truth.

### Step 5c — Opportunities & rotation

Step 5 already produced these; **read that output, do not re-run the scorers.** Facts, not trades:
- **Regime.** `contested` is a watch flag that changes no weight. Escalate only on
  `review_recommended` (dispersed developments) or a `degrading` structural — then WebSearch the
  macro context and decide. Two consecutive down days confirm nothing.
- **Opportunities.** Fell hard, still `intact` + catalyst-confirmed, drop mostly CONTAGION (low
  `idiosyncratic_pct`). WebSearch each to rule out a hidden cause behind the residual first.
- **Diversifiers.** Healthy, low correlation to the stressed cluster — where to rotate without
  re-buying the same bet.
- **Entry timing** (the *when*, complementary to dislocation's *whether*). Flag a high-ranked sector
  with `falling` (knife not based), `overbought`, or an event overhang. The module states the fact;
  the adverse-vs-bullish read on an overhang is yours.

### Step 6 — Open position reviews

```bash
uv run python -m catalyx.store.movement_repo positions
uv run python -m catalyx.store.lake_query ledger
```
For each open position read its movement in `data/movements/` and work its `risk_discipline`:
each `assumptions[]` → `holding` / `weakening` / `violated` with **specific evidence (date, source,
value)**; each `invalidation[]` → breached or not; then cross its catalysts against this run's
`regime_state`.

**The action does not come from your judgement — it comes from the rebalance table** that
`post_run.sh` just printed: `rule_action`, `trade_eur`, `tax_eur`, `net_edge_eur`, `gate_note`.
One row per position: sector · days open · assumptions (N/N holding) · catalyst regime ·
**`rule_action` + `trade_eur`** · one line of evidence.

- **Banned in the action column:** `watch`, `monitor`, `consider`, `optional`. A verdict that does
  not move money is `HOLD`, written once, with its reason.
- Your evidence can CONTRADICT the rule — that is what an override is for. Say so explicitly and
  record it, never by quietly softening the wording:
  ```bash
  uv run python -m catalyx.execution.rebalance override <sector_id> <chosen_action> \
    --reason "<the evidence the rule is missing>" --author claude --trade-eur <€ actually moved>
  ```
  `<chosen_action>` includes `DEFER`. **A "revisit next cycle" IS a deviation** — it is the form
  conservatism usually takes, and unlogged it is invisible. `--trade-eur 0` for a HOLD or a DEFER.
- Overrides are priced ~21 trading days later against the action they replaced. If Claude's tally
  goes net-negative over ≥5 scored overrides, `log_override` refuses `--author claude` and only the
  user may override. That suspension is arithmetic — do not argue with it.
- **Recommend only.** Any actual add/reduce/exit is executed by the user, never written here.

### Step 7 — Catalyst exposure

From the ledger: `invested_eur` and the sectors carrying each catalyst. For every candidate new
position compute `combined_exposure_pct = existing + proposed` and compare to
`correlated_catalyst_cap.max_combined_pct` (default 20%). A breach is ⚠ OVER-CAP — a **flexible**
warning requiring an explicit `correlation_note`, not a block (unless `enforcement: "block"`).
This check informs Step 9: never recommend a new position without it.

### Step 8 — Tax snapshot YTD

```bash
uv run python -m catalyx.store.movement_repo positions    # realized_eur = YTD realized
uv run python -m catalyx.execution.tax_engine --gain <projected_unrealized> --ytd-prior <realized> --json
```
Realized gains, tax paid YTD, marginal bracket, projected full-year if open positions closed at
mark. No closing movement yet → state YTD realized = 0.

### Step 8.5 — Dashboard (before Step 9)

```bash
uv run python scripts/build_site.py
python -m http.server -d dist 8000        # background
```
Verify it answers 200, give the user **http://localhost:8000** and one line on what changed. This is
a LOCAL build; the public Pages dashboard only updates on push. Let the user look before you ask.

### Step 9 — Position-open recommendations

Candidates = ranked in the top-5 with no open position. Present a context block each — why it ranks
(flag parabolic momentum: a high rank is not an entry point) · crowding from `narrative_maturity` ·
entry-timing state and any overhang · the **buyable UCITS vehicle** (ticker, AUM; flag < $200M) ·
exposure fit vs the cap · a one-line recommendation. Then **AskUserQuestion per candidate**:
Open now / Wait / Skip.

- On "Open now": hand off to `/catalyx-open <sector_id>`. This review never writes a movement.
- **If the rebalance table said BUY/ADD and the answer is Wait or Skip, that is an override — log
  it** with the user's own reason and `--author user`. Not to police the decision, to price it
  later: a deferral that is never recorded cannot be wrong, which is exactly why it accumulates.

### Step 11 — Watch-only triggers (findings-driven)

Do **not** search every watch-only sector — there are ~30 and a sweep reports "no change" 29 times.
Check a sector's `watch_triggers` only when the scan's discovery pass surfaced a theme that maps to
it, a scan finding directly addresses its `retired_reason` (e.g. a UCITS vehicle finally launches),
or the user asks. Otherwise write one line: `no watch trigger surfaced by this scan`.

### Step 12 — Taxonomy gap review

Update each proposal mechanically (detected again → `signal_count`++, append `evidence[]`, update
`last_seen`, `status: accumulating`; not detected → leave it and note "not seen this cycle").

Then for EACH pending proposal (`proposed` / `accumulating`) present a context block — thesis in one
line · why now (cite THIS cycle's evidence) · ETF coverage (pure-play or proxies) · relation to
existing sectors and whether it is genuinely distinct under the granularity principle (Gold ≠ gold
miners; if it is a slice of an existing sector, say so) · strength/novelty anchored against an
existing catalyst · risk or reason to wait (liquidity, single-issuer ETF, `signal_count` < 3) ·
recommendation — and **AskUserQuestion: Promote / Reject / Defer**. Never present the table
read-only, never decide automatically. `sector_taxonomy.yaml` is written only after "Promote".

---

## Output

```bash
uv run python scripts/review_report.py        # → data/reports/review_<date>.md
```
The generator writes every deterministic section from the lake — ranking, portfolios/NAV, the
rebalance table, open positions, overrides, exposure, tax, overdue indicators — so **do not retype
those numbers**: a transcription that silently disagrees with the lake is the one error a review
cannot absorb. Append prose ONLY at the `<!-- CLAUDE: … -->` markers: the macro context, the
executive summary, the evidence line per position, override reasons, the Step 9 context blocks, and
the Step 12 blocks.

The executive summary's first line is the `SUMMARY` row from the rebalance output, **verbatim**
(deployed % vs rule and floor · N rule actions · override tally). It must contain at least one
NON-OBVIOUS finding; if everything really is unchanged, say that explicitly.

Then print to chat: "Review complete. Key findings: [3 bullets]. Full report:
`data/reports/review_<date>.md`."

## Rules

- **Never trust a stored value before searching.** Project data is a month stale; the delta between
  the YAML and today is often the review's most important finding.
- **Never assign `intensity.current_score`, `score` or `semaphore` by hand** — all derived, all
  recomputed by `indicator_update`.
- **Stale indicators are not optional to flag** — they are data-quality faults that corrupt
  everything downstream.
- **The review recommends; the user executes.** Every step above is recommend-only.
- **Cash is a decision with a cost.** Report the deployment line every run, even when the answer is
  uncomfortable — especially then.
