---
title: Rank Changes & Validation
description: What entered/exited the top-N each run, and whether past rankings predicted returns.
---

```sql runs
select run_id, run_at, scoring_version, git_commit, sector_count, notes
from catalyx.score_run
order by run_at desc
```

```sql events
select
    sector_id,
    event_type,
    from_rank,
    to_rank,
    delta,
    run_id
from catalyx.rank_event
where sector_id is not null
order by run_id desc, abs(coalesce(delta, 99)) desc
```

```sql eventsCount
select count(*) as n from catalyx.rank_event where sector_id is not null
```

## Rank-change events

Derived automatically each run by diffing against the previous run: which sectors
**entered** or **exited** the top-N, and any move of ≥3 ranks.

{#if eventsCount[0].n > 0}
<DataTable data={events} rows=25>
    <Column id=sector_id title="Sector"/>
    <Column id=event_type title="Event"/>
    <Column id=from_rank title="From #"/>
    <Column id=to_rank title="To #"/>
    <Column id=delta title="Δ" contentType=delta/>
    <Column id=run_id title="Run"/>
</DataTable>
{:else}
<Alert status=info>

No rank-change events yet — these are derived by **diffing two runs**. The first run
(`{runs[0].run_id}`) has nothing to compare against. Events will populate from the next
recorded run onward.

</Alert>
{/if}

## All scoring runs

Each run is tagged with a `scoring_version` (a hash of `scoring_weights.yaml`) and the git
commit, so scores are only ever compared **within the same formula version**. A version change
means the numbers are not directly comparable across the boundary.

<DataTable data={runs} rows=all>
    <Column id=run_id title="Run"/>
    <Column id=run_at title="When" fmt="yyyy-mm-dd hh:mm"/>
    <Column id=scoring_version title="Formula"/>
    <Column id=git_commit title="Commit"/>
    <Column id=sector_count title="Sectors"/>
    <Column id=notes title="Notes"/>
</DataTable>

## Forward-return validation

Whether a past run's ranking actually predicted ETF returns is computed offline (it needs
live market data via yfinance, so it is not part of this static site):

```bash
uv run python -m catalyx.store.snapshot_repo validate
```

It reports two numbers per run:

- **rank IC** — Spearman correlation between each sector's composite and its forward ETF
  return. `> 0` means higher-scored sectors did better; this is the headline "were we right".
- **top-N minus rest** — mean forward return of the top-N sectors minus the rest: the
  tradable spread the ranking would have captured.

Meaningful only with **≥2 runs separated in time** — within a single day the forward window
is ~noise.
