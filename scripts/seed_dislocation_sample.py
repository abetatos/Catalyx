"""Seed a SAMPLE dislocation partition so the dashboard 'Opportunities' tab can be tested
without a live yfinance run.

Writes ONE obviously-fake run (`run_20991231_120000` — year 2099, so it is clearly not real and
sorts as the "latest" run, which is what the tab shows). It exercises every UI state: clean panic
dips, "investigate residual" dips, diversifiers, and a `contested`/`breaking` regime watch.

⚠ This is sample data. It will SHADOW real dislocation runs (it sorts as max). When done testing:
    Remove-Item data/lake/analysis/dislocation/run_id=run_20991231_120000.parquet
then run the real engine:  uv run python -m catalyx.scorer.dislocation --window 5

    uv run python scripts/seed_dislocation_sample.py
"""
from datetime import datetime, timezone

import pandas as pd

from catalyx.store import lake

RUN = "run_20991231_120000"
_now = datetime.now(timezone.utc)
_common = dict(run_id=RUN, computed_at=_now, window_days=5, benchmark="SPY", market_window_pct=-2.5)


def _row(sector, etf, regime, ca, comp, draw, beta, contag, idio, frac, lens,
         opp=None, div=None, corr=None):
    return {**_common, "sector_id": sector, "primary_etf": etf, "regime_state": regime,
            "catalyst_alignment": ca, "composite": comp, "drawdown_pct": draw,
            "beta_to_market": beta, "contagion_explained_pct": contag, "idiosyncratic_pct": idio,
            "contagion_fraction": frac, "lens": lens, "opportunity_score": opp,
            "diversifier_score": div, "mean_corr_to_stressed": corr}


ROWS = [
    # OPPORTUNITIES (intact) — clean panic dips (≥70% contagion) and "investigate" (large residual)
    _row("ai_infrastructure_data_centers", "AIPO", "intact", 96.7, 69.4, -5.09, 1.97, -4.93, -0.15, 0.97, "opportunity", opp=4.9),
    _row("silver_miners", "SIL", "intact", 72.1, 60.0, -15.42, 2.81, -7.04, -8.37, 0.46, "opportunity", opp=11.1),
    _row("copper_miners", "COPA.L", "intact", 95.9, 70.5, -8.0, 1.5, -4.2, -3.8, 0.52, "opportunity", opp=7.7),
    _row("grid_infrastructure_utilities", "IQQH.DE", "intact", 96.1, 72.8, -6.19, 0.80, -2.0, -4.19, 0.32, "opportunity", opp=5.9),
    # DIVERSIFIERS (intact, low correlation to the stressed cluster)
    _row("cybersecurity_commercial", "ISPY.L", "intact", 85.8, 70.2, -3.1, 0.93, -2.33, -0.77, 0.75, "diversifier", div=56.9, corr=0.19),
    _row("gold_physical", "IGLN.L", "intact", 72.1, 62.0, -2.1, 0.80, -2.0, -0.1, 0.95, "diversifier", div=48.4, corr=0.22),
    _row("royalty_streaming_metals", "MRGR", "intact", 70.0, 56.1, -1.2, 0.60, -1.5, 0.3, 1.0, "diversifier", div=42.1, corr=0.25),
    # REGIME WATCH (non-intact) — contested pure-plays + one breaking (to exercise the red state)
    _row("semiconductors_memory", "DRAM", "contested", 75.9, 67.5, -3.1, 2.0, -5.0, 1.9, 0.0, "neither"),
    _row("semiconductors_design", "SEMI.L", "contested", 79.5, 68.0, -2.66, 1.9, -4.7, 2.04, 0.0, "neither"),
    _row("semiconductors_equipment", "ASML", "breaking", 66.0, 66.0, -7.0, 2.0, -5.0, -2.0, 0.71, "neither"),
]


def main() -> None:
    fp = lake.append_partition("dislocation", pd.DataFrame(ROWS), {"run_id": RUN}, overwrite=True)
    print(f"seeded {len(ROWS)} SAMPLE dislocation rows as {RUN} -> {fp}")
    print("rebuild the site to preview:  uv run python scripts/build_site.py")
    print("remove when done:  data/lake/analysis/dislocation/run_id=run_20991231_120000.parquet")


if __name__ == "__main__":
    main()
