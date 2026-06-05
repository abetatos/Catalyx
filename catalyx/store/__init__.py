"""Catalyx store package.

There is no database. Persistence lives in two tiers:

  Tier 1 (git, hand-edited)  : JSON/YAML documents in data/ and catalyx/config/.
                               The *_repo modules here read them directly.
  Tier 2 (git, parquet lake) : computed time-series in data/lake/, written and read
                               through catalyx.store.lake (+ lake_query for analytics).

Nothing to initialise — reading a document or a lake partition is the whole contract.
"""
from __future__ import annotations
