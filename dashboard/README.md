# CATALYX Dashboard (Evidence.dev)

Static exploration site over the CATALYX **score-history** parquet files. It reads
`../data/history/*.parquet` (exported by `snapshot_repo`) via an in-memory DuckDB source —
no database server, no live API. The built site is fully static and deploys to GitHub Pages.

## Pages

| Page | What it shows |
|---|---|
| `/` (index) | Latest run: top-15 bar chart + full sortable ranking table + run metadata |
| `/sectors` | Pick any sector → composite & rank over time, dimension breakdown, latest narrative block |
| `/changes` | Rank-change events (entered/exited top-N), all runs with their `scoring_version`, validation notes |

## Data flow

```
heatmap run → snapshot_repo record   (writes score_run / sector_snapshot / rank_event / report to SQLite)
            → snapshot_repo export    (data/history/*.parquet)
            → this site reads the parquet at build time
```

Refresh the data before building:

```bash
# from the repo root
uv run python -m catalyx.store.snapshot_repo export
```

## Local development

```bash
cd dashboard
npm install          # first time only
npm run sources      # ingest the parquet into Evidence's cache
npm run dev          # http://localhost:3000
```

## Build (static)

```bash
npm run build        # → dashboard/build/
```

For GitHub Pages (project page at `https://abetatos.github.io/Catalyx/`) the build needs the
base path `/Catalyx`. The CI workflow (`.github/workflows/deploy-dashboard.yml`) handles this
automatically. To build locally with the base path:

```bash
npm run sources
npx evidence build --basePath /Catalyx
```

## Notes

- Time-series charts populate as runs accumulate; with a single run they show one point.
- `rank_event` and forward-return validation become meaningful from the **second** run onward.
- Forward-return validation (rank-IC, top-N spread) needs live market data, so it runs offline
  via `snapshot_repo validate` — not in this static site.
