# catalyx-update

Record new observations on a structural catalyst's indicators, or move its narrative maturity.
Use after a data release (WGC, CFTC COT, IMF, LME, earnings) or when narrative saturation shifts.

**Python does the update.** Everything that used to be nine hand-executed steps — shift
`current_value → last_value`, stamp `last_date`, archive the prior reading, stamp
`status_last_reviewed`, recompute intensity — is `catalyx.store.indicator_update`. Do not edit
the YAML by hand: the manual path was writing history to the deprecated inline `value_history`
while `intensity_engine` reads the parquet lake, so observations looked recorded and the
empirical percentile never saw them.

Usage:
- `/catalyx-update <catalyst_id> <indicator_id> <value> [note]`
- `/catalyx-update <catalyst_id> narrative_maturity <level>` — `ignored|emerging|mainstream|crowded|exhausted`
- `/catalyx-update --batch <file.json>` — every observation from one scan, one recompute per catalyst

## Steps — indicator update

1. **Verify the number before recording it.** WebSearch the source if the value did not come from
   one (AI Scoring Rule 5: the YAML holds last month's data). State the source in `--source`.
2. Run:
   ```bash
   uv run python -m catalyx.store.indicator_update set <catalyst_id> <indicator_id> <value> \
     --note "<one line>" --source "<where it came from>"
   ```
   This writes the YAML, archives the prior reading to the lake, and recomputes intensity,
   printing `old → new` per indicator and the intensity Δ.
3. **Read the output for two things and report them:**
   - a `⚠` block — the reading moved against the indicator's `direction` or crossed
     `threshold_weak`, and the catalyst's deactivation conditions are printed underneath. Judge
     them: does this reading actually satisfy one? That call is yours, not the engine's.
   - a large intensity Δ (> 5 points) or a semaphore flip — say what drove it.
4. If the reading materially changes the catalyst's fundamental verdict, ALSO stamp it:
   `uv run python -m catalyx.store.catalyst_review stamp <catalyst_id> <verdict> --evidence "…"`
   (`weakening`/`breaking` require evidence). This is what the exit-watcher freshness gate reads.

## Steps — narrative_maturity

1. Read `catalyx/config/scoring_weights.yaml` `narrative_maturity_levels` and say in one sentence
   which anchored criteria the sector now meets. Never a number (AI Scoring Rule 2).
2. `uv run python -m catalyx.store.indicator_update maturity <catalyst_id> <level>`

## Steps — batch (from a scan)

`/catalyx-scan` writes `data/reports/scan_deltas_<date>.json`. One file, three consumers:

```bash
uv run python -m catalyx.store.indicator_update batch data/reports/scan_deltas_<date>.json
uv run python -m catalyx.store.catalyst_review   batch data/reports/scan_deltas_<date>.json
uv run python -m catalyx.scorer.catalyst_lifecycle --deltas data/reports/scan_deltas_<date>.json --apply
```

File shape — one entry per catalyst the scan actually looked at:
```json
[{"catalyst_id": "struct_nato_rearmament",
  "verdict": "intact",
  "evidence": "NATO Hague 5% target reaffirmed; RHM order book +19% YoY",
  "source": "Rheinmetall Q2 2026 report",
  "indicators": [{"id": "ind_02", "value": 0.19, "note": "Q2 print"}]}]
```
`verdict` ∈ `intact|strengthening|weakening|breaking|invalidated`. An entry with no `indicators`
is a re-verification only — it stamps freshness and records nothing numeric. Report the combined
digest: N observations applied, M catalysts recomputed, any `⚠`, any lifecycle transition.

## Rules

- Never compute `intensity.current_score`, `score` or `semaphore` by hand — all derived.
- Never hand-edit `current_value`/`last_value`/`last_date`/`value_history` in the YAML.
- Only `computation_method: "bootstrap"` allows a manual value, and only at file creation.
- The YAML is the source of truth; the repos read it directly. There is no DB to resync.
