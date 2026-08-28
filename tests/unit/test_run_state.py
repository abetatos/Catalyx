"""Unit tests for the run-state digest (catalyx.store.run_state).

The digest is what a review reads INSTEAD of opening with ~40 searches, so the two properties
that matter are: (1) the work-list tiers put money-at-risk first and let the tail be skipped
without a search, and (2) one unavailable input never takes the whole digest down — a digest
that crashes offline sends the review straight back to searching blind.
"""
from __future__ import annotations

from catalyx.store import run_state


# ── Work-list tiers ──────────────────────────────────────────────────────────

def test_driving_catalysts_are_must_even_when_their_verdict_is_fresh():
    # Freshness is a floor, not a substitute for checking what you actually hold.
    must, should, optional = run_state.tier_work_list(
        driving=["struct_copper", "struct_grid"], stale_by_id={}, sector_drivers=set())
    assert [m["catalyst_id"] for m in must] == ["struct_copper", "struct_grid"]
    assert all(m["why"] == "drives an open position" for m in must)
    assert should == [] and optional == []


def test_stale_driver_of_a_relevant_sector_is_should_not_optional():
    stale = {"struct_ai": {"freshness": "very_stale"}, "struct_far": {"freshness": "stale"}}
    must, should, optional = run_state.tier_work_list(
        driving=[], stale_by_id=stale, sector_drivers={"struct_ai"})
    assert [s["catalyst_id"] for s in should] == ["struct_ai"]
    assert "very_stale" in should[0]["why"]
    assert optional == ["struct_far"]        # nothing this cycle hangs on it → no search
    assert must == []


def test_a_catalyst_never_appears_in_two_tiers():
    stale = {"struct_copper": {"freshness": "very_stale"}, "struct_ai": {"freshness": "stale"}}
    must, should, optional = run_state.tier_work_list(
        driving=["struct_copper"], stale_by_id=stale, sector_drivers={"struct_copper", "struct_ai"})
    ids = [m["catalyst_id"] for m in must] + [s["catalyst_id"] for s in should] + optional
    assert len(ids) == len(set(ids))
    assert [m["catalyst_id"] for m in must] == ["struct_copper"]     # held beats stale
    assert [s["catalyst_id"] for s in should] == ["struct_ai"]


def test_every_stale_catalyst_lands_in_exactly_one_tier():
    # Nothing may fall off the list silently — "we did not look" must stay visible.
    stale = {f"c{i}": {"freshness": "stale"} for i in range(6)}
    must, should, optional = run_state.tier_work_list(
        driving=["c0"], stale_by_id=stale, sector_drivers={"c1", "c2"})
    covered = {m["catalyst_id"] for m in must} | {s["catalyst_id"] for s in should} | set(optional)
    assert covered == set(stale)


# ── Resilience ───────────────────────────────────────────────────────────────

def test_safe_swallows_a_failing_input_and_keeps_its_shape():
    def boom():
        raise RuntimeError("no lake yet")

    assert run_state._safe(boom, []) == []                       # list-shaped default survives
    assert "error" in run_state._safe(boom, {})                  # dict-shaped default carries why
    assert run_state._safe(lambda: [1, 2], []) == [1, 2]


def test_drivers_for_ignores_unknown_and_unparseable_studies(tmp_path, monkeypatch):
    studies = tmp_path / "data" / "sector_studies"
    studies.mkdir(parents=True)
    (studies / "study_copper_miners.json").write_text(
        '{"active_catalyst_ids": ["struct_copper", "struct_ai"]}', encoding="utf-8")
    (studies / "study_broken.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(run_state, "_REPO_ROOT", tmp_path)

    got = run_state._drivers_for(["copper_miners", "broken", "never_studied"])
    assert got == {"struct_copper", "struct_ai"}                 # bad/missing files are skipped


# ── Render ───────────────────────────────────────────────────────────────────

def _digest(**over) -> dict:
    base = {
        "as_of": "2026-08-27",
        "book": {"n_positions": 1, "invested_eur": 500.0, "unrealized_eur": -108.0,
                 "unrealized_pct": -21.7, "realized_ytd_eur": 0.0, "held_sectors": ["grid"]},
        "positions": [{"sector_id": "grid", "etf": "IQQH.DE", "action": "reduce",
                       "unrealized_pct": -21.7, "unrealized_eur": -108.0,
                       "drawdown_tier": "reduce", "reverify_required": True,
                       "catalyst_freshness": "very_stale", "regime_state": "intact"}],
        "attention": {"positions_needing_action": ["grid"], "stale_verdicts": 2,
                      "stale_indicators": 5, "pending_lifecycle": 1},
        "lifecycle_transitions": [{"catalyst_id": "cat_x", "from": "active", "to": "archived",
                                   "reason": "spent"}],
        "work_list": {"must_reverify": [{"catalyst_id": "struct_grid", "why": "drives an open position"}],
                      "should_reverify": [], "optional_reverify": ["struct_z"],
                      "sectors_decision_relevant": ["grid"]},
    }
    base.update(over)
    return base


def test_render_surfaces_the_action_and_the_reverify_flag():
    text = run_state.render(_digest())
    assert "reduce" in text and "-21.7%" in text
    assert "⚠" in text                                   # the re-verify flag must be visible
    assert "act on: grid" in text
    assert "cat_x active → archived" in text
    assert "must=1" in text and "optional=1" in text


def test_render_handles_a_position_with_no_mark():
    d = _digest()
    d["positions"][0]["unrealized_pct"] = None
    d["book"]["unrealized_pct"] = None
    text = run_state.render(d)
    assert "n/a" in text                                 # honest gap, not a crash or a fake 0


# ── Universe-size adaptivity + investable filter ─────────────────────────────

def test_relevant_top_n_scales_with_the_universe():
    # A fixed 15 meant "top quartile" at 53 sectors and "top 58%" at 26 — two different filters
    # wearing the same number.
    assert run_state.relevant_top_n(53) == 15        # capped
    assert run_state.relevant_top_n(26) == 8         # ≈ a third
    assert run_state.relevant_top_n(9) == 5          # floored — a tiny universe still offers choice
    assert run_state.relevant_top_n(0) == 5


def test_investable_sectors_excludes_watch_only(tmp_path, monkeypatch):
    import yaml

    cfg = tmp_path / "catalyx" / "config"
    cfg.mkdir(parents=True)
    (cfg / "sector_taxonomy.yaml").write_text(yaml.safe_dump({"sectors": [
        {"id": "copper_miners", "investable": True},
        {"id": "fusion_energy", "investable": True, "watch_only": True},
        {"id": "dead_sector", "investable": False},
    ]}), encoding="utf-8")
    monkeypatch.setattr(run_state, "_REPO_ROOT", tmp_path)
    assert run_state.investable_sectors() == {"copper_miners"}


def test_missing_taxonomy_yields_no_filter_rather_than_an_empty_work_list(tmp_path, monkeypatch):
    # Failing open matters: an empty investable set must not silently blank the work list.
    monkeypatch.setattr(run_state, "_REPO_ROOT", tmp_path)
    assert run_state.investable_sectors() == set()


# ── Merged catalysts must not reach the work list ────────────────────────────

def test_merged_drivers_resolve_to_the_catalyst_that_is_actually_scored(tmp_path, monkeypatch):
    # A movement keeps the id it was opened against. After a merge that id is `status: merged`
    # and compute_all() skips it — putting it on MUST spends a search on a dead catalyst.
    import yaml

    from catalyx.store import structural_catalyst_repo as scr

    d = tmp_path / "structural_catalysts"
    d.mkdir()
    (d / "copper.yaml").write_text(yaml.safe_dump(
        {"id": "struct_copper", "status": "merged", "merged_into": "struct_aicapex"}),
        encoding="utf-8")
    (d / "aicapex.yaml").write_text(yaml.safe_dump(
        {"id": "struct_aicapex", "status": "active"}), encoding="utf-8")
    monkeypatch.setattr(scr, "_YAML_DIR", d)

    assert scr.resolve("struct_copper") == "struct_aicapex"
    assert scr.resolve("struct_never_heard_of_it") == "struct_never_heard_of_it"
    # De-duplicates: two positions on merged siblings become ONE search, not two.
    assert scr.resolve_all(["struct_copper", "struct_aicapex"]) == ["struct_aicapex"]


def test_a_merge_cycle_stops_instead_of_hanging(tmp_path, monkeypatch):
    import yaml

    from catalyx.store import structural_catalyst_repo as scr

    d = tmp_path / "structural_catalysts"
    d.mkdir()
    (d / "a.yaml").write_text(yaml.safe_dump(
        {"id": "struct_a", "status": "merged", "merged_into": "struct_b"}), encoding="utf-8")
    (d / "b.yaml").write_text(yaml.safe_dump(
        {"id": "struct_b", "status": "merged", "merged_into": "struct_a"}), encoding="utf-8")
    monkeypatch.setattr(scr, "_YAML_DIR", d)
    assert scr.resolve("struct_a") in ("struct_a", "struct_b")   # terminates, value unimportant
