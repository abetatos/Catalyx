"""v6 I4 — the sensitivity harness must be falsifiable before its table means anything.

Its whole failure mode is the FALSE NEGATIVE: a knob that misses its target prints exactly like
a constant that does not matter. Two real ones were found and fixed while building it (the
momentum periods, unpacked into other names at import; `sharpness`, overridden by the profile),
so the harness carries a control knob and these tests pin the machinery around it.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

_SPEC = importlib.util.spec_from_file_location(
    "sensitivity_weights",
    Path(__file__).resolve().parents[2] / "experiments" / "sensitivity_weights.py")
SW = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(SW)


# ── Rank agreement ───────────────────────────────────────────────────────────

def test_tau_is_one_for_an_identical_order():
    a = ["a", "b", "c", "d"]
    assert SW.kendall_tau(a, list(a)) == 1.0


def test_tau_is_minus_one_for_a_reversed_order():
    a = ["a", "b", "c", "d"]
    assert SW.kendall_tau(a, list(reversed(a))) == -1.0


def test_tau_falls_for_a_single_adjacent_swap():
    a = ["a", "b", "c", "d"]
    tau = SW.kendall_tau(a, ["b", "a", "c", "d"])
    assert 0.0 < tau < 1.0


def test_jaccard_measures_set_overlap_not_order():
    assert SW.jaccard(["a", "b"], ["b", "a"]) == 1.0
    assert SW.jaccard(["a", "b"], ["a", "c"]) == pytest.approx(1 / 3)
    assert SW.jaccard(["a"], ["b"]) == 0.0


# ── The knobs actually reach their targets ───────────────────────────────────

def test_every_ranking_knob_restores_what_it_patched():
    """A leaked patch would silently contaminate every row after it."""
    before = (SW.ME._WEIGHT_3M, SW.CS._REINFORCE_FACTOR, SW.CS._DEFAULT_HALFLIFE,
              SW.CS._EVENT_SUB_WEIGHT, dict(SW.IE._TREND_DELTAS))
    for knob in SW.RANK_KNOBS.values():
        with knob(0.5):
            pass
    after = (SW.ME._WEIGHT_3M, SW.CS._REINFORCE_FACTOR, SW.CS._DEFAULT_HALFLIFE,
             SW.CS._EVENT_SUB_WEIGHT, dict(SW.IE._TREND_DELTAS))
    assert before == after


def test_the_momentum_knob_reaches_the_value_the_engine_reads():
    """It patched `_MPW`, which is unpacked into _WEIGHT_* at import — so it reached nothing and
    reported both momentum periods as inert. That false negative is what this pins."""
    base = SW.ME._WEIGHT_3M
    with SW.RANK_KNOBS["momentum_period_weights.return_3m"](0.5):
        assert SW.ME._WEIGHT_3M == pytest.approx(base * 0.5)
    assert SW.ME._WEIGHT_3M == base


def test_the_sizing_knob_reaches_the_profile_that_overrides_the_global():
    """`build_model_holdings` reads `construction.sharpness` before the global, and catalyx.yaml
    declares it — so patching only the global accessor reached nothing."""
    from catalyx.execution import portfolio as PF

    with SW.SIZE_KNOBS and SW._weighting("sharpness", 0.5):
        assert PF.load_profile("catalyx")["construction"]["sharpness"] == pytest.approx(0.125)
    assert PF.load_profile("catalyx")["construction"]["sharpness"] == pytest.approx(0.25)


def test_the_unshrunk_mode_disables_the_skill_shrinkage():
    """λ=0 today, so a sizing constant can be decisive and read as inert. The second column
    measures it with the layer it feeds turned on."""
    from catalyx.execution import portfolio as PF

    with SW._weighting("sharpness", 1.0, unshrunk=True):
        assert PF.load_profile("catalyx")["construction"]["tilt_shrinkage"] is False


def test_the_control_knob_moves_every_score_and_reorders_nothing():
    """A sensitivity table nobody can falsify is decoration. `z_scale` is a monotone rescaling
    of the composite: it MUST move every score and change no position.

    One arithmetic exemption: a sector sitting AT the universe mean (composite ≈ 50, z ≈ 0)
    rescales by less than the 1dp display step — halving z_scale moves 50.10 to 50.05, both
    printed 50.1. That is rounding, not a broken knob, so near-50 rows may hold still.
    Surfaced live 2026-08-31 by gold_miners at 50.1."""
    base_rank, base_scores = SW._score()
    with SW.RANK_KNOBS["composite_scale.z_scale [control]"](0.5):
        rank, scores = SW._score()
    assert rank == base_rank
    unmoved = [s for s in base_scores if scores[s] == base_scores[s]]
    assert all(abs(base_scores[s] - 50.0) <= 0.15 for s in unmoved), unmoved
    assert len(unmoved) < len(base_scores) / 4


def test_the_rank_reads_composite_z_not_the_rounded_display_composite():
    """`composite` is rounded to 1dp, so ranking on it broke near-ties by list order. On the
    2026-08-31 universe that published space_defense_satellite (49.4, z −0.041) above
    nuclear_energy (49.4, z −0.038). The rank must read the unit v6/H1 declared comparable."""
    from catalyx.scorer.sector_scorer import rank_key

    rows = [{"sector_id": "a", "composite": 49.4, "composite_z": -0.041},
            {"sector_id": "b", "composite": 49.4, "composite_z": -0.038}]
    assert [r["sector_id"] for r in sorted(rows, key=rank_key(rows))] == ["b", "a"]

    # pre-v6 runs carry no composite_z, and a MIXED collection falls back too: comparing a z
    # against an absolute level ranks worse than a tie does.
    for legacy in ({"sector_id": "c", "composite": 60.0},
                   {"sector_id": "c", "composite": 60.0, "composite_z": None}):
        mixed = [*rows, legacy]
        assert sorted(mixed, key=rank_key(mixed))[0]["sector_id"] == "c"


def test_the_universe_cli_derives_crowding_the_way_the_recorded_run_does():
    """`--universe` passed one flat default to every sector, so crowding's σ_cross was 0 and the
    0.12-weighted dimension ranked nothing — in the view that feeds the study work list, while
    the recorded run derived it from narrative_maturity. Two different rankings."""
    from catalyx.scorer import sector_scorer as SS

    ids = SS._investable_sector_ids()
    derived = SS.universe_crowding(ids)
    assert set(derived) == set(ids)
    assert len(set(derived.values())) > 1, "crowding is constant across the universe again"

    # an explicit --crowd is still one number for everyone: that is what asking for one means
    assert set(SS.universe_crowding(ids, 35.0).values()) == {35.0}


def test_intensity_history_is_one_entry_per_period():
    """`write_back` appended a row every run, so a second review the same day left two
    `2026-08-31` entries and `_trend_delta` differenced them as two consecutive PERIODS —
    manufacturing a `falling` delta out of the pipeline running twice, which compounds on each
    re-run (gold went 78.6 → 68.5 → 64.5 on 2026-08-31 alone)."""
    from catalyx.scorer import intensity_engine as IE

    # the duplicate is the ONLY thing that could read as a fall: the real prior period is flat
    same_day = [{"period": "2026-08-31", "score": 68.5},
                {"period": "2026-08-31", "score": 78.6},
                {"period": "2026-07-28", "score": 68.4}]
    delta, label = IE._trend_delta(same_day)
    assert "falling" not in label, "a same-day re-run must not read as a period decline"
    assert delta == pytest.approx(float(IE._TREND_DELTAS["flat"]))

    # entries with no period label are distinct observations, not one collapsed period
    unlabelled = [{"score": 68.5}, {"score": 78.6}]
    assert "falling" in IE._trend_delta(unlabelled)[1]

    # a genuine period-over-period fall still reads as one
    real_fall = [{"period": "2026-08-31", "score": 68.5}, {"period": "2026-07-28", "score": 78.6}]
    assert "falling" in IE._trend_delta(real_fall)[1]


def test_no_structural_catalyst_carries_a_duplicate_history_period():
    """Nine of thirteen files did, up to six rows of `2026-Q2`. The dedupe on read repairs the
    reading; this keeps the files themselves from drifting back."""
    from collections import Counter
    from pathlib import Path

    import yaml as _yaml

    d = Path(__file__).parents[2] / "catalyx" / "config" / "structural_catalysts"
    for p in sorted(d.glob("*.yaml")):
        hist = (_yaml.safe_load(p.read_text(encoding="utf-8")).get("intensity") or {}).get("history") or []
        dupes = {k: v for k, v in Counter(str(e.get("period")) for e in hist).items() if v > 1}
        assert not dupes, f"{p.name} carries duplicate history periods: {dupes}"


def test_write_back_targets_the_file_each_result_was_computed_from():
    """`--all --write-back` zipped `compute_all()` (which SKIPS inactive/macro_context catalysts)
    against a fresh glob of every YAML. Different lengths → every result after the first skipped
    file landed in the wrong catalyst's file. On 2026-08-31 that wrote gold's score into
    biopharma_patent_cliff and left the last five files untouched."""
    from catalyx.scorer import intensity_engine as IE

    live = sorted(Path(IE._CATALYSTS_DIR).glob("*.yaml"))
    results = IE.compute_all()
    # the invariant the bug violated: results are addressed by their own source file, and there
    # are strictly fewer of them than there are files on disk (something IS being skipped)
    assert len(results) < len(live), "no catalyst is being skipped — this test proves nothing"
    for r in results:
        assert Path(r["_source_file"]).exists()
        got = yaml.safe_load(Path(r["_source_file"]).read_text(encoding="utf-8"))
        assert got["id"] == r["id"], "a result is addressed to a file describing another catalyst"


def test_intensity_is_idempotent_across_repeated_write_backs():
    """The score fed its own trend: `write_back` wrote today's row and `_trend_delta` then read
    it as the most-recent period, so recomputing moved the number again. Two reviews in one day
    took gold 78.6 → 68.5 → 64.5 on a world that had not moved. Compute must give the same
    answer whether or not write_back has already run today."""
    from catalyx.scorer import intensity_engine as IE

    path = Path(IE._CATALYSTS_DIR) / "cb_gold_accumulation.yaml"
    first = IE.compute_from_yaml(path)["computed_score"]

    cat = yaml.safe_load(path.read_text(encoding="utf-8"))
    from datetime import date as _date
    cat["intensity"]["history"].insert(
        0, {"period": _date.today().isoformat(), "score": first, "note": "as write_back writes it"})
    assert IE.compute_intensity(cat)["computed_score"] == pytest.approx(first)

    # a row from a PRIOR period is real history and still drives the trend
    cat["intensity"]["history"][0]["period"] = "2020-01-01"
    cat["intensity"]["history"][0]["score"] = first - 40
    assert IE.compute_intensity(cat)["computed_score"] != pytest.approx(first)


def test_a_cmf_flow_reading_is_imputed_not_scored():
    """`volume_proxy` is CMF — a price+volume oscillator `flow_data` itself labels "not true
    flow". Measured 2026-08-31 when the stockanalysis source was repaired and 12 sectors moved
    off CMF onto real share counts: mean |error| 13.1 points, max 25.6. So it is treated like a
    missing study — imputed to z=0, excluded from the dimension's moments, flagged as a column."""
    from catalyx.scorer import sector_scorer as SS

    rows = [{"sector_id": f"s{i}", "composite": 50.0,
             "score_breakdown": {"catalyst_alignment": 50.0, "momentum": 50.0 + i,
                                 "flow_confirmation": f, "crowding_risk": 50.0},
             "flow_imputed": imp}
            for i, (f, imp) in enumerate([(80.0, True), (20.0, True), (55.0, False),
                                          (45.0, False), (52.0, False), (48.0, False)])]
    SS.commensurate(rows)

    # the two CMF rows sit at the prior, whatever number CMF produced
    assert rows[0]["dimension_z"]["flow_confirmation"] == 0.0
    assert rows[1]["dimension_z"]["flow_confirmation"] == 0.0
    # ...and their 80/20 spread does not inflate the dimension's sigma: the measured rows,
    # which span only 45-55, still separate from each other
    assert rows[2]["dimension_z"]["flow_confirmation"] > rows[3]["dimension_z"]["flow_confirmation"]


def test_flow_imputation_did_not_change_how_catalyst_alignment_is_imputed():
    """The CA path was generalised, not replaced. A regression here would silently re-arm the
    v6 H2 bug (a missing study dragging the moments it is then measured against)."""
    from catalyx.scorer import sector_scorer as SS

    rows = [{"sector_id": f"s{i}", "composite": 50.0,
             "score_breakdown": {"catalyst_alignment": ca, "momentum": 50.0 + i,
                                 "flow_confirmation": 50.0, "crowding_risk": 50.0},
             "ca_imputed": imp}
            for i, (ca, imp) in enumerate([(0.0, True), (90.0, False), (70.0, False),
                                           (60.0, False), (50.0, False), (40.0, False)])]
    scale = SS.commensurate(rows)
    assert rows[0]["dimension_z"]["catalyst_alignment"] == 0.0
    assert scale["ca_imputed_n"] == 1
