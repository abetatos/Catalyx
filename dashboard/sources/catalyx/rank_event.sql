-- rank_event can be empty (the first run has nothing to diff against). Evidence's
-- DuckDB-wasm cache cannot read a zero-row parquet, so we guarantee one typed
-- sentinel row (all-null). Pages filter it out with `where sector_id is not null`.
select * from read_parquet('../data/history/rank_event.parquet')
union all by name
select
    cast(null as bigint)    as id,
    cast(null as varchar)   as run_id,
    cast(null as varchar)   as prev_run_id,
    cast(null as varchar)   as sector_id,
    cast(null as varchar)   as event_type,
    cast(null as bigint)    as from_rank,
    cast(null as bigint)    as to_rank,
    cast(null as bigint)    as delta,
    cast(null as bigint)    as top_n,
    cast(null as timestamp) as created_at
