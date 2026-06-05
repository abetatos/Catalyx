---
title: Sector History
description: Score evolution and the latest narrative block for any sector.
---

```sql sectors_list
select sector_id, max(rank) as last_rank
from catalyx.sector_snapshot
group by sector_id
order by last_rank
```

<Dropdown data={sectors_list} name=sector value=sector_id defaultValue="ai_infrastructure_data_centers"/>

```sql sector_hist
select
    snapshot_at,
    run_id,
    rank,
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
where sector_id = '${inputs.sector.value}'
order by snapshot_at
```

```sql sectorLatest
select rank, composite, catalyst_alignment, momentum, flow_confirmation,
       valuation_relative, crowding_risk, narrative_maturity, primary_etf,
       etf_price, rationale_md as rationale, snapshot_at
from catalyx.sector_snapshot
where sector_id = '${inputs.sector.value}'
order by snapshot_at desc
limit 1
```

# {inputs.sector.value}

<Grid cols=4>
    <BigValue data={sectorLatest} value=rank title="Current Rank"/>
    <BigValue data={sectorLatest} value=composite title="Composite"/>
    <BigValue data={sectorLatest} value=primary_etf title="Primary ETF"/>
    <BigValue data={sectorLatest} value=narrative_maturity title="Maturity"/>
</Grid>

## Composite & rank over time

<LineChart
    data={sector_hist}
    x=snapshot_at
    y=composite
    title="Composite over time"
    yMin=0
    yMax=100
    markers=true
/>

<LineChart
    data={sector_hist}
    x=snapshot_at
    y=rank
    title="Rank over time (lower = better)"
    reverseY=true
    markers=true
/>

## Dimension breakdown (latest run)

```sql sector_dims
select dim, score from (
    select 'catalyst_alignment' as dim, catalyst_alignment as score from catalyx.sector_snapshot where sector_id = '${inputs.sector.value}' order by snapshot_at desc limit 1
) union all
select dim, score from (
    select 'momentum' as dim, momentum as score from catalyx.sector_snapshot where sector_id = '${inputs.sector.value}' order by snapshot_at desc limit 1
) union all
select dim, score from (
    select 'flow_confirmation' as dim, flow_confirmation as score from catalyx.sector_snapshot where sector_id = '${inputs.sector.value}' order by snapshot_at desc limit 1
) union all
select dim, score from (
    select 'valuation_relative' as dim, valuation_relative as score from catalyx.sector_snapshot where sector_id = '${inputs.sector.value}' order by snapshot_at desc limit 1
) union all
select dim, score from (
    select 'crowding_risk' as dim, crowding_risk as score from catalyx.sector_snapshot where sector_id = '${inputs.sector.value}' order by snapshot_at desc limit 1
)
```

<BarChart data={sector_dims} x=dim y=score swapXY=true sort=false labels=true yMax=100/>

## Narrative (latest)

{#if sectorLatest[0].rationale}
<Details title="Sector narrative — latest run" open=true>

{sectorLatest[0].rationale}

</Details>
{:else}
<Alert status=info>No narrative block stored for this sector — only top-N sectors get a written block each run.</Alert>
{/if}

## Full history table

<DataTable data={sector_hist} rows=all>
    <Column id=snapshot_at title="Run" fmt="yyyy-mm-dd"/>
    <Column id=rank title="#"/>
    <Column id=composite/>
    <Column id=catalyst_alignment title="Catalyst"/>
    <Column id=momentum/>
    <Column id=flow_confirmation title="Flow"/>
    <Column id=valuation_relative title="Valuation"/>
    <Column id=crowding_risk title="Crowding"/>
</DataTable>
