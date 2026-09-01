"""Phase C — the cost of NOT acting — and B4, the rule scorecard (plan v4 §4, §3 B4).

v3 built everything that makes a bad ACTION visible: a rule table, banned words, an override log,
a suspension arithmetic. It built nothing that makes INACTION visible. Friction is printed to the
cent on every row; the cost of leaving €6,954 idle for 73 days was printed nowhere, an override
existed only if the narrator chose to write one, and a shortfall could survive review after review
as a line of text nobody had to answer.

Every test here pins one half of that asymmetry closed. The scorecard at the end closes the
matching one on the other side: overrides were scored from the day they were introduced, and the
rules they deviate FROM never were.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from catalyx.execution import rebalance as rb

_CFG = {"deployment": {"max_shortfall_pp": 10.0, "max_shortfall_runs": 2}}


# ── C1 — the shortfall is an action with a persistence rule ──────────────────

def test_shortfall_is_measured_in_points_of_total_capital():
    # A book at 30% against an 85% rule is 55pp short. Expressing it as a ratio of the target
    # ("35% of the way there") is the reading that makes an enormous gap sound like progress.
    assert rb.shortfall_pp(3_000, 8_500, 10_000) == 55.0
    assert rb.shortfall_pp(9_000, 8_500, 10_000) == -5.0          # over-deployed is negative
    assert rb.shortfall_pp(0, 0, 0) == 0.0                        # no capital, no verdict


def test_one_bad_run_is_not_a_breach_but_two_consecutive_ones_are():
    one = rb.shortfall_status([{"as_of": "2026-07-01", "shortfall_pp": 2.0},
                               {"as_of": "2026-08-01", "shortfall_pp": 40.0}], _CFG)
    assert one["runs_breached"] == 1 and one["breached"] is False
    two = rb.shortfall_status([{"as_of": "2026-07-01", "shortfall_pp": 40.0},
                               {"as_of": "2026-08-01", "shortfall_pp": 40.0}], _CFG)
    assert two["runs_breached"] == 2 and two["breached"] is True
    assert two["since"] == "2026-07-01", "the breach dates from where the streak STARTED"


def test_a_single_compliant_run_resets_the_streak():
    # Deploying, then drifting back, is not the same failure as never deploying — the rule is
    # about a shortfall nobody answers, not about a book that moves.
    st = rb.shortfall_status([{"as_of": "2026-06-01", "shortfall_pp": 40.0},
                              {"as_of": "2026-07-01", "shortfall_pp": 3.0},
                              {"as_of": "2026-08-01", "shortfall_pp": 40.0}], _CFG)
    assert st["runs_breached"] == 1 and st["breached"] is False


def test_an_empty_history_never_breaches():
    st = rb.shortfall_status([], _CFG)
    assert st["breached"] is False and st["runs_breached"] == 0


# ── C4 — the idle cash is priced in the same units as the friction ───────────

def test_cash_drag_prices_the_idle_capital_against_the_benchmark():
    d = rb.cash_drag(6_954.0, 3.03, "2026-06-16", 73)
    assert d["forgone_eur"] == pytest.approx(210.71, abs=0.01)
    assert "73d" in d["note"] and "forgone" in d["note"]


def test_an_unmeasurable_benchmark_leaves_the_cost_unstated_not_zero():
    # €0 forgone is a claim ("the cash cost nothing"); None is the truth ("we could not measure
    # it"). Printing the first because the price cache was cold is how inaction gets a free pass.
    d = rb.cash_drag(6_954.0, None, "2026-06-16", 73)
    assert d["forgone_eur"] is None
    assert "forgone" not in d["note"]


# ── C3 — the deviation nobody wrote down ─────────────────────────────────────

_PRIOR = [
    {"sector_id": "copper_miners", "rule_action": "SELL", "trade_eur": -1062.0,
     "run_id": "run_20260728_103246", "as_of": "2026-07-28"},
    {"sector_id": "pharma_large_cap", "rule_action": "ADD", "trade_eur": 456.0,
     "run_id": "run_20260728_103246", "as_of": "2026-07-28"},
    {"sector_id": "water_infrastructure", "rule_action": "HOLD", "trade_eur": 0.0,
     "run_id": "run_20260728_103246", "as_of": "2026-07-28"},
]


def test_a_row_with_no_movement_and_no_override_is_an_unrecorded_deviation():
    out = rb.unrecorded_deviations(_PRIOR, movements=[], overrides=[], since="2026-07-28",
                                   until="2026-08-28")
    assert [u["sector_id"] for u in out] == ["copper_miners", "pharma_large_cap"]


def test_a_hold_is_never_a_deviation():
    # Nothing was asked of it. Neither is RE-SCORE, which moves no money by design.
    rows = _PRIOR + [{"sector_id": "x", "rule_action": "RE-SCORE", "trade_eur": 0.0}]
    out = rb.unrecorded_deviations(rows, [], [], since="2026-07-28", until="2026-08-28")
    assert all(u["rule_action"] not in ("HOLD", "RE-SCORE") for u in out)


def test_an_executed_movement_clears_the_row():
    movs = [{"sector_id": "copper_miners", "executed_at": "2026-08-05T10:00:00"}]
    out = rb.unrecorded_deviations(_PRIOR, movs, [], since="2026-07-28", until="2026-08-28")
    assert [u["sector_id"] for u in out] == ["pharma_large_cap"]


def test_a_movement_outside_the_interval_does_not_clear_the_row():
    # A trade made BEFORE the run that recommended it is not compliance with that run.
    movs = [{"sector_id": "copper_miners", "executed_at": "2026-06-01"}]
    out = rb.unrecorded_deviations(_PRIOR, movs, [], since="2026-07-28", until="2026-08-28")
    assert [u["sector_id"] for u in out] == ["copper_miners", "pharma_large_cap"]


def test_a_logged_override_clears_the_row_and_makes_the_detector_idempotent():
    # This is also what stops `_log_unrecorded` from writing the same DEFER every run: once the
    # override exists, the deviation is recorded and no longer unrecorded.
    ovr = [{"sector_id": "copper_miners"}, {"sector_id": "pharma_large_cap"}]
    assert rb.unrecorded_deviations(_PRIOR, [], ovr, since="2026-07-28") == []


def test_unrecorded_is_an_allowed_override_author():
    # The machine must be able to write the deviation the narrator did not. If `unrecorded` were
    # not allowed, `_log_unrecorded` would swallow a ValueError per row and quiet would stay free.
    from catalyx.config import weights
    assert "unrecorded" in weights.rebalance_rules()["overrides"]["authors_allowed"]


# ── C5 — the language rule, enforced in the generator ────────────────────────

def _report_module():
    spec = importlib.util.spec_from_file_location("rr", Path("scripts/review_report.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_lint_catches_a_hedge_in_a_decision_section():
    rr = _report_module()
    found = rr.lint_prose("## Executive summary\nWe should monitor copper and revisit next cycle.\n")
    assert len(found) == 1 and found[0]["hedge"] in ("monitor", "revisit", "next cycle")


def test_analysis_sections_may_still_be_tentative():
    # The macro context is analysis, not a verdict. Policing hedges there would just teach the
    # narrator to write confidently about things it does not know.
    rr = _report_module()
    assert rr.lint_prose("## 0. Macro & geopolitical context\nA cautious read for now.\n") == []


def test_tables_and_generated_footnotes_are_not_linted():
    rr = _report_module()
    text = ("## 5. Overrides\n"
            "| sector | consider |\n"
            "> a generated note containing the word monitor\n")
    assert rr.lint_prose(text) == []


def test_the_generated_report_itself_passes_its_own_lint():
    # The generator does not get an exemption from the rule it enforces.
    rr = _report_module()
    assert rr.lint_prose(rr.build("2026-08-28")) == []


# ── F2 — the report cannot claim to be finished with its judgement half empty ─

def test_an_unanswered_marker_is_a_finding_and_an_answered_one_is_not():
    rr = _report_module()
    empty = "## 4. Open positions\n\n<!-- CLAUDE: one line of EVIDENCE per position. -->\n\n## 4b. Risk\n"
    assert [f["section"] for f in rr.lint_completeness(empty)] == ["4. Open positions"]

    # The marker STAYS in the file — it is the anchor for regenerating without losing the prose,
    # so a section is answered by writing under it, never by deleting it.
    answered = empty.replace("-->\n", "-->\ncopper: LME stocks checked 2026-08-30, still falling.\n")
    assert rr.lint_completeness(answered) == []


def test_a_freshly_generated_report_is_incomplete_by_construction():
    """The two lints are orthogonal and each must be able to fail alone: a report can be fully
    written and hedged, or unhedged only because nothing was written. v4.9's review passed
    `--check` clean with all five judgement sections blank."""
    rr = _report_module()
    text = rr.build("2026-08-28")
    assert rr.lint_prose(text) == [], "a blank report has no hedges — that is the whole problem"
    sections = [f["section"] for f in rr.lint_completeness(text)]
    # Every marker the generator emitted is unanswered — counted from the text, not hardcoded, so
    # the assertion is about the lint and not about how many conditions the book happens to trip.
    # A marker opens its own line; the header MENTIONS the syntax mid-sentence and is not one.
    emitted = sum(1 for ln in text.splitlines() if ln.lstrip().startswith("<!-- CLAUDE:"))
    assert len(sections) == emitted, sections
    assert any("Executive summary" in s for s in sections)
    assert any("Open positions" in s for s in sections)


def test_a_conditional_marker_tracks_its_condition_in_both_directions():
    """Overrides logged THIS run and cap breaches are conditional. Demanding prose where the
    honest answer is "nothing breached" is how a lint teaches people to write filler — but the
    marker must appear the moment there IS something to say, so the expectation is read off the
    book rather than frozen into the test."""
    from catalyx.store import movement_repo

    rr = _report_module()
    text = rr.build("2026-08-28")

    # The marker owes prose only for a deviation a PERSON chose. `unrecorded`, `budget` and
    # `ramp` DEFERs are the machinery working, and the expectation is read off the override log
    # rather than frozen here — this half of the test used to hardcode "no override is pending"
    # and started failing the day the book acquired one, which is the frozen expectation the
    # docstring warns about.
    from catalyx.execution import rebalance as rb
    deliberate = [p for p in (rb.score_overrides().get("pending") or [])
                  if str(p.get("author")) not in ("unrecorded", "budget", "ramp")]
    assert ("for each override logged THIS run" in text) is bool(deliberate)
    assert any("Overrides" in f["section"] for f in rr.lint_completeness(text)) is bool(deliberate)

    proposed = [{"sector_id": r.get("sector_id"), "trade_eur": r.get("trade_eur")}
                for r in rr._rebalance_rows() if r.get("rule_action") in ("BUY", "ADD")]
    breached = any(c["over"] for c in movement_repo.cap_check(proposed))
    assert ("cap and by how much" in text) is breached
    assert ("breaches the cap" in text) is breached


# ── B4 — the rule scorecard: the table audits itself too ─────────────────────
#
# `calibration` measures the ranking. `score_overrides` measures the deviations. Nothing measured
# the table, and that asymmetry made it unfalsifiable by construction: every human departure from
# the rules was priced, while the rules kept their authority by never being scored.

_SC_CFG = {"scorecard": {"horizon_days": 63, "min_n": 2, "min_effective_windows": 1}}


def _rows(action, returns, as_of="2026-01-01"):
    return [{"rule_action": action, "forward_return_pct": r, "as_of": as_of} for r in returns]


def test_a_sell_scores_well_when_the_vehicle_then_fell():
    # The raw forward return cannot be read as skill until it is signed by the direction the rule
    # moved money. A SELL into a −5% move is the rule being RIGHT, and an unsigned table would
    # print it as the worst row on the page.
    scored = _rows("HOLD", [0.0, 0.0]) + _rows("SELL", [-5.0, -5.0])
    sc = rb.decision_scorecard(scored, _SC_CFG)
    sell = next(r for r in sc["rows"] if r["action"] == "SELL")
    assert sell["vs_hold_pp"] == -5.0 and sell["rule_edge_pp"] == +5.0


def test_a_buy_scores_well_when_the_vehicle_then_rose():
    scored = _rows("HOLD", [0.0, 0.0]) + _rows("BUY", [4.0, 6.0])
    buy = next(r for r in rb.decision_scorecard(scored, _SC_CFG)["rows"] if r["action"] == "BUY")
    assert buy["rule_edge_pp"] == +5.0


def test_hold_is_the_baseline_not_a_verdict():
    # "Did the names go up" is beta and belongs to the deployment ratio. The only thing a rule
    # table can claim credit for is beating the book left alone.
    sc = rb.decision_scorecard(_rows("HOLD", [3.0, 3.0]) + _rows("BUY", [3.0, 3.0]), _SC_CFG)
    hold = next(r for r in sc["rows"] if r["action"] == "HOLD")
    buy = next(r for r in sc["rows"] if r["action"] == "BUY")
    assert hold["verdict"] == "baseline" and hold["rule_edge_pp"] is None
    assert buy["rule_edge_pp"] == 0.0, "matching the baseline is not skill, it is the baseline"


def test_re_score_moves_no_money_so_it_gets_no_verdict():
    sc = rb.decision_scorecard(_rows("HOLD", [0.0, 0.0]) + _rows("RE-SCORE", [9.0, 9.0]), _SC_CFG)
    row = next(r for r in sc["rows"] if r["action"] == "RE-SCORE")
    assert row["rule_edge_pp"] is None and row["verdict"] == "no money moved"


def test_overlapping_runs_are_one_observation():
    # Five runs inside one 63-day horizon are one window, exactly as `calibration.aggregate`
    # counts them. Reading a verdict off the row count is how a single regime gets mistaken for
    # a measured edge.
    scored = [{"rule_action": "BUY", "forward_return_pct": 5.0, "as_of": d}
              for d in ("2026-01-01", "2026-01-08", "2026-01-15")]
    assert rb.decision_scorecard(scored, _SC_CFG)["effective_windows"] == 1
    spread = [{"rule_action": "BUY", "forward_return_pct": 5.0, "as_of": d}
              for d in ("2026-01-01", "2026-06-01")]
    assert rb.decision_scorecard(spread, _SC_CFG)["effective_windows"] == 2


def test_a_thin_sample_refuses_to_produce_a_verdict():
    cfg = {"scorecard": {"horizon_days": 63, "min_n": 5, "min_effective_windows": 2}}
    sc = rb.decision_scorecard(_rows("HOLD", [0.0] * 5) + _rows("BUY", [5.0] * 5), cfg)
    buy = next(r for r in sc["rows"] if r["action"] == "BUY")
    assert "not scoreable yet" in buy["verdict"] and sc["scoreable"] is False


def test_an_edge_inside_two_standard_errors_is_noise():
    cfg = {"scorecard": {"horizon_days": 63, "min_n": 3, "min_effective_windows": 1}}
    scored = _rows("HOLD", [0.0, 0.0, 0.0]) + _rows("BUY", [-8.0, 0.0, 9.0])   # mean ≈ +0.3, se ≈ 5
    buy = next(r for r in rb.decision_scorecard(scored, cfg)["rows"] if r["action"] == "BUY")
    assert buy["verdict"] == "noise"


def test_the_scorecard_reads_in_rule_precedence():
    scored = _rows("BUY", [1.0, 1.0]) + _rows("SELL", [1.0, 1.0]) + _rows("HOLD", [1.0, 1.0])
    actions = [r["action"] for r in rb.decision_scorecard(scored, _SC_CFG)["rows"]]
    assert actions == ["SELL", "BUY", "HOLD"]


def test_an_empty_lake_produces_an_empty_scorecard_not_a_crash():
    sc = rb.decision_scorecard([], _SC_CFG)
    assert sc["rows"] == [] and sc["n_scored"] == 0


def test_an_open_window_is_pending_and_costs_no_price_fetch(tmp_path, monkeypatch):
    # For months the honest answer is "not yet"; paying for a download to print that would be a
    # fixed cost on a fixed non-answer. If this ever fetches, the call below raises.
    import pandas as pd

    from catalyx.store import lake

    run = "run_20260828_000000"
    lake.append_partition("rebalance", pd.DataFrame([
        {"run_id": run, "sector_id": "a", "etf": "AAA.L", "rule_action": "BUY",
         "trade_eur": 100.0, "as_of": "2026-08-28"}]),
        {"run_id": run}, overwrite=True, lake_dir=tmp_path)

    def _boom(*a, **k):
        raise AssertionError("a scorecard with no complete window must not fetch a price")

    res = rb.score_decisions(lake_dir=tmp_path, as_of="2026-08-31", price_fn=_boom)
    assert res["scored"] == [] and len(res["pending"]) == 1
    assert "window open" in res["pending"][0]["status"]
