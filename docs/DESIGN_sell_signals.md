# DESIGN — Sell signals: the exit side of the pipeline

> **Status:** design only — no code yet. Defines the four exit families, the signal schema,
> persistence, and the wiring into `/catalyx-review` + `/catalyx-close` before any build.
> **Motivated by:** the platform is asymmetric — the BUY stack is fully deterministic
> (`composite → dislocation → entry_timing → regime_state`), the SELL side is almost entirely
> LLM judgement (review Step 6 reads stops/assumptions by hand; `/catalyx-close` only *records*
> the exit). The `risk_discipline.invalidation[]` rules authored on every movement are **never
> read by any code**, and there is no `exit_timing` to mirror `entry_timing`.
> **Touches (planned):** new `catalyx/scorer/exit_watcher.py` + `catalyx/scorer/exit_timing.py`,
> reuse of `catalyst_scorer` (regime), `structural_monitor`, `entry_timing` (micro-tension math),
> `dislocation` (correlation), `tax_engine` (CGT), `movement_repo` (positions), `snapshot_repo`,
> `schemas/movement.json` (additive `risk_discipline` fields), `scoring_weights.yaml`
> (`exit_signals` config), `/catalyx-review` Step 6, `/catalyx-close`, the dashboard Positions page.

---

## 1. The problem

CATALYX answers four questions on the way IN — *which* sector (composite), *whether* it is cheap
(dislocation), *when* to enter (entry_timing), and *is the catalyst intact* (regime_state). On the
way OUT it answers almost none of them in code:

| Exit question | Today |
|---|---|
| Has my pre-committed risk line broken? | `risk_discipline.invalidation[]` is **written but unread** — no engine checks the stops. |
| Did the *reason* (catalyst) break? | `regime_state` / `structural_monitor` exist but are not crossed against open positions automatically. |
| Given I'm exiting, sell *now or wait*? | No `exit_timing`. The entry-timing math (RSI, stretch, vol, stabilization) is exit-relevant but not applied. |
| Should I take profit / is the edge spent? | Only manual judgement. No profit-target field, no exhaustion rule. |
| Should I rotate this into something better? | `dislocation` diversifiers exist for buying; nothing surfaces the *funding* sell. |
| What does exiting cost me after tax? | `tax_engine` exists but is only invoked *after* you've decided, inside `/catalyx-close`. |

The goal of this design: a deterministic **exit-signal layer**, symmetric to the buy stack,
reusing the facts the system already computes, under the same doctrine — *Python computes facts +
a suggested verdict; Claude judges; the system recommends, never auto-trades; it reacts to
persistence, not to the event.*

## 2. The principle — buy and sell are NOT symmetric in stance

A buy is optional: not buying costs nothing but opportunity. A sell concerns risk you **already
carry**, and one exit family — the **pre-committed stop** — is the single place the system has
license to be *more insistent than the buy side*, because the user committed in advance precisely
to remove in-the-moment emotion. So the stance ladder is asymmetric:

```
  pre-committed full_exit stop fires   → LOUDEST. Skill defaults the action to Exit. (you committed)
  reason broke (regime breaking)       → strong recommend, human confirms (the §regime doctrine)
  profit-take / exhaustion             → soft recommend (discretionary)
  rotation (better use of capital)     → softest (pure opportunity cost)
```

Everything still *recommends* — none auto-trades (same conclusion the regime A/B reached: acting on
noise costs edge). But the **default the skill offers** scales with how pre-committed and how
fundamental the trigger is. A discretionary signal defaults to Hold; a broken stop defaults to Exit.

## 3. The four families

Two of these answer *whether* to still own it; two answer *how/when* to leave. Mapped to existing
infra so we build bridges, not new engines.

### Family 1 — Invalidation (the *whether*): "the risk line / the reason broke"

The planned `invalidation_watcher`. For each open position (`movement_repo positions`) read its
opening movement's `risk_discipline` and evaluate, deterministically where possible:

- **Price/level stops** (`invalidation[].source == market_data`, with `stop_price_level` +
  `stop_price_ticker`): fetch the series, evaluate the comparator over the persistence window. →
  `fired` / `approaching` / `clear`.
- **Assumptions** (`assumptions[].current_status`): roll up `holding`/`weakening`/`violated`. A
  `violated` assumption on a `full_exit`-linked leg is an exit input.
- **Regime cross**: the position's attributed catalyst(s) (`attribution[]`) vs this run's
  `regime_state` (from `catalyst_scorer`) + `structural_monitor` health. `breaking` on a driving
  catalyst is the fundamental exit signal; `contested` is watch-only (per the regime doctrine).

**Machine-checkable vs judgement-required split.** Only `market_data` stops with a yfinance-resolvable
ticker can be evaluated in code. `earnings_data` / `macro_data` / `news_llm` conditions (e.g.
"2 of 3 hyperscalers cut capex >25%") are surfaced as **Claude-checks-with-WebSearch** items, not
auto-evaluated. The copper position alone has `COPPER_LME`, `LME_COPPER_INVENTORY`, `EURUSD` — of
which only EUR/USD is cleanly yfinance-resolvable today (`EURUSD=X`); copper has a futures proxy
(`HG=F`), inventory has no free feed. **This ticker-resolvability gap is the main limiter of
Family 1's automation** and is an open decision (§7).

**Persistence is intrinsic to stops.** Conditions are written as *"closes below $11,000 for 10
consecutive trading days"* — never one close. The check must honor the consecutive-day window
(time-independent: a stateless read of the price history, same answer at any cadence — the same
rule as the regime persistence layer). Free-text English is brittle to parse, so the clean fix is
**structured eval fields on the invalidation** (§6): `comparator`, `threshold`, `consecutive_days`,
keeping `condition` as the human description.

### Family 2 — Exit timing (the *when*): mirror of `entry_timing`

Once a discretionary exit/trim is decided, *now or wait?* Reuse `entry_timing`'s **pure math**
(`rsi`, `stretch_vs_ma`, `vol_ratio`, `pct_return`, `drawdown_from_local_high`) unchanged — it is
side-agnostic — but **invert the interpretation**:

| Micro state | Entry verdict (buy) | Exit verdict (sell) |
|---|---|---|
| `stretched` / overbought / blow-off | `wait_stabilize` (don't chase) | **`sell_into_strength`** (distribute into the rip) |
| `calm` | `enter_now` | `exit_at_leisure` (no timing objection) |
| `falling_unstable` (knife) | `wait_stabilize` | **`hold_dont_panic_sell`*** (don't dump into the knife) |
| `stabilizing` | `scale_in` | `exit_on_bounce` (let the bounce give you a better fill) |

`*` **overridden** by a Family-1 `full_exit` stop — see the arbitration rule (§5). Timing governs
only *discretionary* exits; a broken risk line is honored regardless of the tape.

Mirror of `is_stabilizing` → add `is_topping` (rolling over after a run-up: last N closes declining,
or lost the short MA after being above it) as the strength-exhaustion discriminator. Event overhang
is reused verbatim but the direction call flips: a near-term *bullish* event is a reason to **wait**
to sell (capture the pop); a *bearish* one a reason to sell **before**. Direction is Claude's, as in
entry_timing.

**Build decision:** sibling `exit_timing.py` that imports the pure functions from `entry_timing`
(keeps the two verdict philosophies cleanly separate, the way `dislocation`/`entry_timing` are
siblings) — not a `--side` flag bolted onto entry_timing.

### Family 3 — Profit-take / exhaustion (the edge is spent)

Distinct from timing (which is about the tape) — this is about the *thesis being consumed*:

- **Target reached** — needs an optional `profit_take[]` on `risk_discipline` (symmetric to
  `invalidation[]`; §6). Without it, Family 3 still works off the signals below.
- **Momentum exhaustion** — `momentum_score` in a top percentile **and** decelerating, **and**
  crowding `narrative_maturity ∈ {crowded, exhausted}` (CLAUDE.md: crowded/exhausted ⇒ less edge
  left). High-and-still-rising is not exhaustion; high-and-rolling-over with a crowded narrative is.
- **Conviction-tier drift** — the position appreciated to **well above its `conviction_tiers`
  ceiling** (12/8/4%) → trim back to tier is a risk action independent of the thesis.
- **Catalyst spent** — the driving *event* catalyst flipped to `archived` (decayed + priced-in, via
  the lifecycle gate). The *spike* reason is consumed; the *structural* floor may remain → trim the
  event-attributed portion, keep the structural portion.

Partly deterministic (percentile, label, weight-vs-tier, lifecycle status), partly Claude's call.

### Family 4 — Rotation (relative opportunity cost — the funding sell)

Sell not because it broke but because capital is better elsewhere. Reuses what the buy side already
computes, read in reverse:

- Held position **dropped out of the top-N rank** (its composite fell relative to the universe).
- `dislocation` surfaced a **higher-composite, lower-correlation** diversifier → "trim the redundant
  holding to fund the uncorrelated one" (directly the regime doc's Layer-3 "rotate to uncorrelated").
- `correlated_catalyst_cap` breach (Step 7) → trim the **most-redundant** of the correlated holdings.

Output is *paired*: `trim X → fund Y`, with the correlation between them shown (don't rotate into the
same bet). Softest stance — pure opportunity cost, always the human's call, and **tax-gated** (§4).

## 4. The tax dimension — the one thing the buy side has no mirror for

Selling in Spain **realizes** CGT; buying never does. Every exit recommendation must carry its
after-tax consequence, computed by `tax_engine` (single source of truth — never brackets by hand):

- **After-tax P&L of exiting now**: feed the position's unrealized gain + the YTD realized baseline
  (`movement_repo positions` → `realized_eur`) into `tax_engine --gain … --ytd-prior …` → show
  `net_gain`, marginal bracket, effective rate **on the recommendation**, not just at `/catalyx-close`.
- **Loss-harvesting note**: a position at a loss late in the calendar year can offset realized gains
  — surface it as a tax *reason to consider* exiting (or trimming) a broken position.
- **Spanish 2-month anti-application rule** (recompra): a loss is **not deductible** if the same /
  homogeneous security is repurchased within **2 months** (ETFs/equities). This directly constrains
  Family 4 rotation and loss-harvesting — flag it when a rotation would round-trip a recently-sold
  vehicle. (Distinct from a US 30-day wash sale; Spain = 2 months for listed, 1 year for unlisted.)
- **Bracket-aware sizing**: a partial trim that keeps realized YTD under the next bracket
  (€6k/€50k/€200k) may beat a full exit on after-tax terms — surface the trim that stays in-bracket.

The tax read is **advisory context on the signal**, never the trigger itself (don't hold a broken
thesis for tax reasons — but do let tax shape *how much* and *when* within the timing window).

## 5. Interaction rules (severity arbitration)

When multiple families fire on one position, resolve by stance ladder (§2), not by OR:

```
1. A Family-1 full_exit stop FIRED  → action = Exit. Overrides Family-2 timing entirely
                                       (you honor the pre-committed line even into a knife).
2. Family-1 review_and_reduce / regime breaking → action = Reduce; Family-2 timing sets WHEN
                                       (sell_into_strength / exit_on_bounce / don't-panic).
3. Family-3 exhaustion / Family-4 rotation (no Family-1) → action = Trim, fully timing- and
                                       tax-gated (discretionary — default Hold if timing is poor).
4. Nothing fired → Hold.
```

This mirrors entry_timing's "an upcoming overhang dominates the micro-state": the most
pre-committed / most fundamental trigger is the binding constraint; timing only modulates the
discretionary cases.

## 6. Schema & config changes (all additive)

**`schemas/movement.json` — `risk_discipline` (bump `schema_version`, mark additive, never delete):**

- Extend each `invalidation[]` item with optional **structured eval** fields so price stops are
  deterministically checkable (the `condition` text stays as the human description):
  ```
  "comparator":       "below" | "above",
  "threshold":         number,            // = stop_price_level for price stops
  "consecutive_days":  integer,           // the persistence window ("for 10 trading days")
  "eval_ticker":       string             // yfinance-resolvable; falls back to stop_price_ticker
  ```
- Add an optional **`profit_take[]`** array (symmetric to `invalidation[]`) for Family 3 targets:
  `{ id (pt_NN), target_level, target_ticker, comparator, action: trim|exit, size_pct, rationale }`.
- The watcher proposes flipping `invalidation[].triggered` / `triggered_at` — but writing that to a
  Tier-1 file is governed (`auto` apply + log, vs `ask` per transition), same as the lifecycle gate.
  Default `ask`: the watcher reports "would fire", the user/Claude confirms at `/catalyx-close`.

**`scoring_weights.yaml` — new `exit_signals` block (single source of truth, no magic numbers):**
exit-timing thresholds (reuse/extend the `entry_timing` set: `topping_down_closes`, `rsi_overbought`
for blow-off), exhaustion thresholds (`momentum_exhaustion_pctile`, `crowding_exit_levels`,
`conviction_drift_mult`), rotation (`rank_drop_floor`, `min_corr_gap`), tax (`bracket_edges`,
`recompra_days: 60`), and the stance-ladder defaults.

## 7. Where each piece lives (implementation surface)

| Piece | File | Type |
|---|---|---|
| Family 1 — read `risk_discipline`, evaluate price stops + assumptions + regime cross | new `catalyx/scorer/exit_watcher.py` | the unread→read bridge (planned `invalidation_watcher`) |
| Family 2 — exit-side micro-tension verdict | new `catalyx/scorer/exit_timing.py` (imports `entry_timing` pure math) | sibling module |
| Family 3 — exhaustion (momentum/crowding/tier/lifecycle) | `exit_watcher.py` (reads `sector_snapshot` + lifecycle) | additive |
| Family 4 — rotation pairs (rank drop + dislocation diversifier + cap) | `exit_watcher.py` (reuses `dislocation` correlation) | additive |
| Tax-aware after-exit P&L on each signal | `exit_watcher.py` → `tax_engine` | additive |
| Ticker resolution for non-yfinance stops (`COPPER_LME→HG=F`, inventory feed) | small map in `exit_watcher.py` / config | the automation limiter (§3) |
| Persist per-position exit signals | `snapshot_repo` → new lake table `exit_signal` (keyed by run_id) | additive table |
| Surface in the review | `/catalyx-review` Step 6 — replace the by-hand checks with the engine output, Claude interprets | skill edit |
| Pre-flight before recording a close | `/catalyx-close` — show exit-signal state + exit_timing verdict + after-tax P&L before writing | skill edit |
| Dashboard | Positions page — an "Exit watch" panel (mirror of the Opportunities/Timing surfaces) | `site/` + `build_site.py` |
| Schema additive fields | `schemas/movement.json` (§6) | additive, version bump |

**CLI shape (proposed):**
```
uv run python -m catalyx.scorer.exit_watcher [--all | <sector_id>] [--json]   # families 1/3/4 + tax
uv run python -m catalyx.scorer.exit_timing  --positions [--json]            # family 2
```
Both read the live book from `movement_repo`; only the `--all` review path persists the lake table
(a single-position ad-hoc run stays ephemeral — same rule as `entry_timing`).

## 8. Open decisions (resolve before building)

1. **One orchestrator vs two modules?** `exit_watcher` (1/3/4) + `exit_timing` (2), or a single
   `exit_signals` orchestrator that calls both? (Leaning: two modules + a thin orchestrator in the
   skill, mirroring how the review calls `catalyst_scorer`+`dislocation`+`entry_timing` separately.)
2. **`triggered` write-back governance** — `ask` (default, safe) vs `auto` for unambiguous price
   stops? A breached `full_exit` stop with structured eval fields is arguably safe to auto-flag.
3. **Add `profit_take[]` + structured `invalidation` eval fields now, or defer Family 3?** Family 1
   needs the eval fields to be deterministic; Family 3 needs `profit_take[]`. Could ship eval fields
   first (highest leverage), defer targets.
4. **Non-yfinance stop feeds.** Accept that copper-inventory / LME-spot stops stay Claude-checked
   (WebSearch), or invest in a data feed? Determines how much of Family 1 is truly automated.
5. **How hard does tax gate rotation?** Advisory note only, or actually reorder/suppress a Family-4
   recommendation that trips the 2-month recompra rule or pushes into a higher bracket?
6. **Stance on a fired `full_exit` stop** — keep "recommend, default Exit" (this doc), or make it
   the one genuine auto-action (the user pre-committed)? The regime A/B argues recommend; the
   pre-commitment argument argues act. Worth an explicit call.

## 9. Summary

- The sell side is the platform's biggest asymmetry: the buy stack is deterministic; exits are
  hand-judged, the authored stops are unread, and there is no `exit_timing`.
- Four families, all bridges over existing facts: **invalidation** (read the stops + regime),
  **exit timing** (mirror entry_timing, inverted), **exhaustion** (momentum/crowding/tier/lifecycle),
  **rotation** (rank drop + uncorrelated diversifier). Plus a **tax** dimension the buy side lacks.
- Stance is **asymmetric**: a pre-committed `full_exit` stop is the loudest signal and the only one
  whose default is Exit; everything else is softer and timing/tax-gated. The most pre-committed /
  fundamental trigger arbitrates (§5).
- Same doctrine as the buy side: Python computes facts + a suggested verdict; Claude judges
  (especially the non-market stops, via WebSearch); the system recommends, never auto-trades; it
  reacts to persistence (consecutive-day windows, time-independent), not to a single print.
- Schema changes are additive (structured `invalidation` eval fields + optional `profit_take[]`);
  config centralizes in `scoring_weights.yaml` `exit_signals`. Build order is gated on the six
  open decisions in §8 — most likely: structured eval fields + `exit_watcher` Family 1 first
  (closes the unread-stops gap), then `exit_timing`, then exhaustion/rotation.
```
