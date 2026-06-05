---
title: CATALYX — Sector Heatmap
description: Composite ranking of every investable sector, recorded each scoring run.
---

```sql latestRun
select run_id, run_at, scoring_version, git_commit, sector_count, notes
from catalyx.score_run
order by run_at desc
limit 1
```

```sql ranking
select
    rank,
    sector_id,
    composite,
    catalyst_alignment,
    momentum,
    flow_confirmation,
    valuation_relative,
    crowding_risk,
    narrative_maturity,
    primary_etf,
    etf_price
from catalyx.sector_snapshot
where run_id = (select run_id from catalyx.score_run order by run_at desc limit 1)
order by rank
```

```sql top15
select sector_id, composite, catalyst_alignment, momentum
from catalyx.sector_snapshot
where run_id = (select run_id from catalyx.score_run order by run_at desc limit 1)
order by rank
limit 15
```

<Alert status=warning>

**PRE-CALIBRATION** — composite weights (0.30 catalyst / 0.25 momentum / 0.20 flow / 0.15 valuation / 0.10 crowding) are unvalidated (0 closed theses). Scores indicate **relative ordering**, not precise conviction levels.

</Alert>

<Grid cols=3>
    <BigValue data={latestRun} value=sector_count title="Sectors Scored"/>
    <BigValue data={latestRun} value=run_at title="Last Run" fmt="yyyy-mm-dd hh:mm"/>
    <BigValue data={latestRun} value=scoring_version title="Scoring Version"/>
</Grid>

Run `{latestRun[0].run_id}` · git `{latestRun[0].git_commit}` · {latestRun[0].notes}

## Top 15 by composite

<BarChart
    data={top15}
    x=sector_id
    y=composite
    swapXY=true
    sort=false
    title="Composite score — top 15 sectors"
    labels=true
/>

## Full ranking

<DataTable data={ranking} rows=20 search=true>
    <Column id=rank title="#" align=center/>
    <Column id=sector_id title="Sector"/>
    <Column id=composite contentType=colorscale/>
    <Column id=catalyst_alignment title="Catalyst"/>
    <Column id=momentum/>
    <Column id=flow_confirmation title="Flow"/>
    <Column id=valuation_relative title="Valuation"/>
    <Column id=crowding_risk title="Crowding"/>
    <Column id=narrative_maturity title="Maturity"/>
    <Column id=primary_etf title="ETF"/>
</DataTable>

- **[Per-sector history →](/sectors)** — score evolution and the narrative block for any sector
- **[Rank changes & validation →](/changes)** — what entered/exited the top-N, and whether past rankings predicted returns
