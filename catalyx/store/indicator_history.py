"""Indicator value-history store — externalized from the structural-catalyst YAMLs
into the parquet lake (Tier 2). See docs/PLAN_lake_dvc_serving.md (Fase C).

Why: `value_history[]` used to live inline in every catalyst YAML, so each new observation
bloated a hand-edited config file with a mutable time series and noisy git diffs. The
history is observed/computed data, not config — it belongs in the lake. The YAML field is
now deprecated (kept one version as a read fallback); `intensity_engine` reads here first.

Table `indicator_history` is partitioned by catalyst_id (one file per catalyst holds every
indicator's observations). Columns: catalyst_id, indicator_id, date, value, source.
"""
from __future__ import annotations

from pathlib import Path

from catalyx.store import lake

TABLE = "indicator_history"


def history_for(catalyst_id: str, lake_dir: Path | None = None) -> dict[str, list[dict]]:
    """{indicator_id: [{date, value}, ...]} for one catalyst, or {} if the lake has none."""
    import pandas as pd

    df = lake.read_table(TABLE, lake_dir=lake_dir)
    if df.empty or "catalyst_id" not in df.columns:
        return {}
    df = df[df["catalyst_id"] == catalyst_id]
    out: dict[str, list[dict]] = {}
    for ind_id, group in df.groupby("indicator_id"):
        points = []
        for _, r in group.iterrows():
            v = r["value"]
            if isinstance(v, float) and pd.isna(v):
                continue
            points.append({"date": r.get("date"), "value": float(v)})
        out[str(ind_id)] = points
    return out


def write_catalyst(catalyst_id: str, indicator_histories: dict[str, list[dict]],
                   source: str | None = None, lake_dir: Path | None = None) -> int:
    """Overwrite the partition for `catalyst_id` with the full set of indicator histories.

    `indicator_histories`: {indicator_id: [{date, value[, source]}, ...]}. Returns rows written.
    """
    import pandas as pd

    rows = [
        {
            "catalyst_id": catalyst_id, "indicator_id": ind_id,
            "date": p.get("date"), "value": p.get("value"),
            "source": p.get("source", source),
        }
        for ind_id, points in indicator_histories.items()
        for p in points
        if p.get("value") is not None
    ]
    if not rows:
        return 0
    lake.append_partition(TABLE, pd.DataFrame(rows), {"catalyst_id": catalyst_id},
                          overwrite=True, lake_dir=lake_dir)
    return len(rows)


def append_observation(catalyst_id: str, indicator_id: str, date: str, value: float,
                       source: str | None = None, lake_dir: Path | None = None) -> int:
    """Append one observation to a catalyst's history, preserving existing rows.

    Rewrites the catalyst partition (read existing + add). Returns the new total row count.
    """
    import pandas as pd

    df = lake.read_table(TABLE, lake_dir=lake_dir)
    if not df.empty and "catalyst_id" in df.columns:
        existing = df[df["catalyst_id"] == catalyst_id]
    else:
        existing = pd.DataFrame()
    new = pd.DataFrame([{
        "catalyst_id": catalyst_id, "indicator_id": indicator_id,
        "date": date, "value": value, "source": source,
    }])
    combined = pd.concat([existing, new], ignore_index=True)
    lake.append_partition(TABLE, combined, {"catalyst_id": catalyst_id},
                          overwrite=True, lake_dir=lake_dir)
    return len(combined)
