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
5    /catalyx-heatmap            re-rank (+ score_run.sh: regime, dislocation, entry timing)
5b   scripts/post_run.sh         portfolios, NAV vs SPY, rotation, METRICS, REBALANCE table
5.5  dashboard                   build + serve locally — the user reads the BOOK before Step 6
6    open-position reviews       evidence per assumption; the ACTION comes from the table
7    catalyst exposure           combined per catalyst vs the cap
8    tax snapshot YTD
9    open recommendations        AskUserQuestion per candidate — three PRICED branches
12   taxonomy gap review         AskUserQuestion per proposal (watch triggers fold in here)
```

**Two steps were deleted, not shortened (plan v4 D-c).** Step 4 read the two catalyst summaries
that `/catalyx-scan` has already read in C1 — 4.5 KB to restate what step 0/1 carried forward.
Step 11 swept 31 non-investable sectors and its own instruction was to write "no watch trigger
surfaced"; a watch trigger firing IS an investability event, so it belongs in Step 12 and nowhere
else. And the dashboard moved from 8.5 to 5.5: the user reads the book, THEN the positions get
discussed — the evidence arrives before the argument, not after it.

**Why 1.5 gates scoring:** it used to run after the heatmap recorded the run, which baked stale and
spent catalysts into the recorded scores — sectors ranking top-10 on indicators 100–500 days old.
Prune first, then score.

## Execution model — the main thread is a thin orchestrator

Bulk WebSearch and many-file phases run in **subagents that return only the step's digest**; the
main conversation holds compact summaries and runs the two user-facing decisions (subagents cannot
ask the user). The subagent Writes its files directly — the file IS the registration.

**A subagent brief names one input and one output shape — never the pipeline order.** The order
is in this file and the state is in `run_<date>.json`; a brief that restates either is paying twice
for the same context, and the restatement is the part that grows every time a step is added.

| Step | Where | Input → output shape |
|---|---|---|
| 0/1 scan | **SUBAGENT** (`general-purpose`, `catalyx-scan.md`) | the work list in `state_<date>.json` → `scan_deltas_<date>.json` + C0 bullets |
| 3 studies | **SUBAGENTS**, one per few sectors, `run_in_background` | one sector id → the written study path, nothing else |
| 5/5b heatmap, portfolios, NAV, rebalance | **SUBAGENT** | `scan_deltas_<date>.json` → `run_<date>.json` + the rebalance table verbatim |
| 5c opportunities/regime | **SUBAGENT** | the lake → the four tables |
| 6 position reviews | **SUBAGENT** | `run_<date>.json` `positions[]` + `rebalance.actions[]` → one row per position |
| 1.5 · 2 · 7 · 8 | MAIN — one CLI each, small output | |
| **9 · 12** | **MAIN** — AskUserQuestion | |

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
uv run python -m catalyx.scorer.sector_scorer --universe --digest  # rank · composite · momentum · vehicle
uv run python -m catalyx.store.sector_study_repo stale --days 7
uv run python -m catalyx.store.sector_study_repo core --all   # maturity · trend · age · catalysts
uv run python -m catalyx.store.movement_repo positions
```
Report the work list, why each sector is on it, and the count skipped as unchanged. Fan the list out
to background subagents following `catalyx-sector-study.md`.

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
vehicle is two positions and only one was a thesis), drawdown from the position's own peak,
**score drift** vs the score the pipeline gave that sector on the day it was bought, and **risk
contribution** (capital share vs share of the book's volatility; they are routinely far apart, and
a NEGATIVE share means the position is lowering book vol — say so, it is the first thing that
would need defending before trimming it). Editing the
chain means editing `post_run.sh` — it is the single source of truth.

**The chain ends by writing `data/reports/run_<date>.json` — one file with every deterministic fact
this run produced** (book, attention, work list, scan deltas, ranking + rank moves, the NAV
comparison with `execution_alpha_pp`, book metrics, per-position metrics, the full rebalance table
with the cash row, catalyst exposure, the override tally). From here on, **read that file instead
of re-running a CLI or re-quoting a payload from earlier in this conversation.** It re-prints for
free:
```bash
uv run python -m catalyx.store.run_digest            # the ~25-line summary
```
Report each strategy's return, SPY's return and the `vs_benchmark_pct` DIFFERENCE (three numbers,
in points — a book at −0.96% against SPY +4.44% is 5.39pp **behind**), plus `execution_alpha_pp`:
the real book vs the model book it implements, computed only when both curves end on the same day.

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

### Step 5.5 — Dashboard (before the positions are discussed)

```bash
uv run python scripts/build_site.py
python -m http.server -d dist 8000        # background
```
Verify it answers 200, give the user **http://localhost:8000** and one line on what changed. This is
a LOCAL build; the public Pages dashboard only updates on push. It runs HERE, not before Step 9:
the user reads the book — the rebalance table, the risk contributions, the cost of not acting —
before the positions are argued about, so the evidence arrives ahead of the argument.

### Step 6 — Open position reviews

The per-position facts are already in `run_<date>.json` (`positions[]`: EUR P&L split, drawdown
from peak, days held, score/rank drift, catalyst freshness, regime, exit action). Read them there;
the two calls below are only for the movement lineage the digest does not carry.
```bash
uv run python -m catalyx.store.movement_repo positions
uv run python -m catalyx.store.lake_query ledger
```
For each open position read its movement in `data/movements/` and work its `risk_discipline`:
each `assumptions[]` → `holding` / `weakening` / `violated` with **specific evidence (date, source,
value)**; each `invalidation[]` → breached or not; then cross its catalysts against this run's
`regime_state`.

**The action does not come from your judgement — it comes from the rebalance table** that
`post_run.sh` just printed: `rule_action`, `trade_eur`, `tax_eur`, `breakeven_pct`, `gate_note`.
One row per position: sector · days open · assumptions (N/N holding) · catalyst regime ·
**`rule_action` + `trade_eur`** · one line of evidence.

Two blocks travel with that table and answer the two questions a rebalance actually raises:

- **SWAP LEDGER — "¿renta vender?"** Each rotation priced in observable euros (CGT + spread) and
  converted to a **breakeven**: the % the destination must outperform the source by over the
  horizon. Report the hurdle and the `EVIDENCE` line beside it. Do **not** convert the hurdle into
  a verdict — that is the judgement the user makes with their own view, and a `NONE`/`ADVERSE`
  evidence verdict is a fact to state, not a reason to soften the rule action into inaction.
  (`net_edge_eur` is still in the lake but no longer drives the table: it multiplied the trade by a
  rank-bucket mean one noisy window old and printed ±€1 beside a real tax bill.)
- **PARTIALS — "¿parciales?"** Distance to each rung per held line. Both rungs are shown because
  their units differ (pp of total capital above target vs % gain on the position). The ladder's
  rank leg fires once the model has **stopped** leading a name, so a rank-1 position failing it is
  the rule working, not a blocked position — never report it as a problem.

- **`CASH DRAG` and `SHORTFALL` — the other half of the ledger.** Every row prices its friction
  to the cent; these two price the alternative. Report the drag in euros beside the smallest
  friction blocking a trade, because that comparison is the whole point. If `SHORTFALL` prints,
  the persistence rule is breached and the review must **either execute the rows or log an
  override naming the shortfall itself** — carrying it to the next review is not one of the
  options.
- **`UNRECORDED DEVIATIONS` — what the last run asked for and never got.** Read from the
  movements on disk, not from any review's account of itself, and already logged as DEFERs
  authored `unrecorded`. State the count and what it cost; never re-argue the old rows.

- **The `TILT λ` line — why the targets are shaped the way they are.** λ is how much of the
  model's conviction tilt the ranking's measured IC has earned. At **λ=0** the targets are
  neutral in *risk* (inverse-vol), the model having selected the names but declining to size
  them. State it as the sizing regime, and state that **gross deployment is unchanged** — λ=0
  is not a reason to hold cash and never licences under-deploying against the deploy ratio.

- **Banned in the action column:** `watch`, `monitor`, `consider`, `optional`. A verdict that does
  not move money is `HOLD`, written once, with its reason. The one non-money action that is NOT a
  verdict is **`RE-SCORE`**: the sector was absent from too many recent runs to have a rank worth
  selling on. Report it as work to do, never soften it into a HOLD — "we do not know" is a state,
  and the fix is to score the sector, not to sit on the position without saying why.
- Your evidence can CONTRADICT the rule — that is what an override is for. Say so explicitly and
  record it, never by quietly softening the wording:
  ```bash
  uv run python -m catalyx.execution.rebalance override <sector_id> <chosen_action> \
    --reason "<the evidence the rule is missing>" --author claude --trade-eur <€ actually moved>
  ```
  `<chosen_action>` includes `DEFER`. **A "revisit next cycle" IS a deviation** — it is the form
  conservatism usually takes, and unlogged it is invisible. `--trade-eur 0` for a HOLD or a DEFER.
- **The rule table is audited too.** `rebalance scorecard` (also in the digest and report §5b)
  prices every recorded `rule_action` over a complete 63d window against the **HOLD baseline** —
  not "did the names go up" (that is beta) but "did acting beat leaving the book alone". The
  forward return is signed by the direction the rule moved money, so a positive **rule edge**
  always means the rule was right. Report it when it has a verdict; when it says "not scoreable
  yet", say that and move on. It is evidence for a config edit, **never** an edit — the same
  discipline the override tally has.
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

### Step 9 — Position-open recommendations

Candidates = ranked in the top-5 with no open position. Present a context block each — why it ranks
(flag parabolic momentum: a high rank is not an entry point) · crowding from `narrative_maturity` ·
entry-timing state and any overhang · the **buyable UCITS vehicle** (ticker, AUM; flag < $200M) ·
exposure fit vs the cap. Price the choice from the rebalance row before asking — target €, the
breakeven %, and the cap headroom are all on the table already:

```
biotech_drug_development · BTEC.L · rank 2 · target €973 (9.7%) · b/e 0.20% · cap headroom €227
```

Then **AskUserQuestion per candidate**, with three PRICED branches and no costless default:

| Option | What it means | What it writes |
|---|---|---|
| **Execute €<target>** | the rule's size | `/catalyx-open <sector_id>` |
| **Execute a smaller size — state it** | a partial deviation | `/catalyx-open` + an override for the difference |
| **Decline — state the evidence** | the rule is wrong here | an override, `--author user`, `--trade-eur 0` |

- "Wait" is **not** an option. It is the branch that is never wrong today and never right in the
  record: it writes nothing, so nothing scores it, so it costs nothing to choose forever. Every
  branch above produces a logged decision instead.
- On "Execute": hand off to `/catalyx-open <sector_id>`. This review never writes a movement.
- **Any answer short of the rule's size is an override — log it** with the user's own reason and
  `--author user`. Not to police the decision, to price it ~21 trading days later: a deferral that
  is never recorded cannot be wrong, which is exactly why it accumulates. If you forget, the NEXT
  run logs it for you as `unrecorded` — the tally is the same, only the reason is missing.

### Step 12 — Taxonomy gap review (and any watch trigger that fired)

**Watch-only sectors are handled here, and only on a finding.** Never sweep the 31 non-investable
sectors — a sweep reports "no change" 30 times. Raise one only when the scan's discovery pass
surfaced a theme that maps to it, when a finding directly addresses its `retired_reason` (a UCITS
vehicle finally launching, say), or when the user asks. A watch trigger firing IS an
investability event, which is what this step already decides; it was never a step of its own.

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
uv run python scripts/review_report.py --check   # LAST — lints the prose you just appended
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

`--check` is the last command of the review and it is not optional. It reads the committed file
and fails on hedges inside the sections where a decision is stated — `watch`, `monitor`,
`consider`, `revisit`, `next cycle`, `for now`. A verdict that does not move money is HOLD, said
once; "revisit next cycle" is a DEFER and belongs in the override log with an author and a reason,
not in a sentence. Fix the prose, do not soften the lint.

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
