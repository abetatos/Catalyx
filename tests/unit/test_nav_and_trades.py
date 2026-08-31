"""Unit tests for the NAV engine and movement repo (positions/ledger). Network-free."""
from __future__ import annotations

import json

import pandas as pd

from catalyx.execution import nav_engine as nav
from catalyx.store import movement_repo as mr


# ── NAV math ─────────────────────────────────────────────────────────────────

def _prices(data: dict[str, list[float]], dates: list[str]):
    return pd.DataFrame(data, index=pd.to_datetime(dates))


def test_holdings_nav_buy_and_hold():
    px = _prices({"AAA": [100, 110], "BBB": [100, 90]}, ["2026-06-05", "2026-06-06"])
    # 50/50: day0 = 100; day1 = 0.5*1.10 + 0.5*0.90 = 1.00 → NAV 100
    series = nav.holdings_nav([{"etf": "AAA", "weight_pct": 50}, {"etf": "BBB", "weight_pct": 50}], px)
    assert series[0]["nav"] == 100.0
    assert series[1]["nav"] == 100.0


def test_holdings_nav_gain():
    px = _prices({"AAA": [100, 120]}, ["2026-06-05", "2026-06-06"])
    series = nav.holdings_nav([{"etf": "AAA", "weight_pct": 100}], px)
    assert series[1]["nav"] == 120.0  # +20%


def test_holdings_nav_cash_when_underallocated():
    # 60% in AAA (+50%), 40% cash → day1 = 0.6*1.5 + 0.4 = 1.30 → 130
    px = _prices({"AAA": [100, 150]}, ["2026-06-05", "2026-06-06"])
    series = nav.holdings_nav([{"etf": "AAA", "weight_pct": 60}], px)
    assert series[1]["nav"] == 130.0


def test_holdings_nav_missing_etf_becomes_cash():
    px = _prices({"AAA": [100, 120]}, ["2026-06-05", "2026-06-06"])
    # BBB has no price column → its 50% weight is treated as flat cash
    series = nav.holdings_nav([{"etf": "AAA", "weight_pct": 50}, {"etf": "BBB", "weight_pct": 50}], px)
    assert series[1]["nav"] == 110.0  # 0.5*1.2 + 0.5*1.0


# ── compute_model_nav with injected prices + lake ────────────────────────────

def _seed_model_holding(tmp_path):
    from catalyx.store import lake
    df = pd.DataFrame([
        {"portfolio_id": "test", "run_id": "run_20260605_120000", "config_version": "cfg1",
         "rank_in_portfolio": 1, "sector_id": "a", "primary_etf": "AAA",
         "composite": 90, "momentum": 80, "crowding_risk": 50,
         "narrative_maturity": "emerging", "weight_pct": 100.0},
    ])
    lake.append_partition("portfolio_holding", df,
                          {"portfolio_id": "test", "run_id": "run_20260605_120000"}, lake_dir=tmp_path)


def test_compute_model_nav_persists_and_dates_from_run(tmp_path, monkeypatch):
    _seed_model_holding(tmp_path)
    # profile lookup will fail (no 'test' profile) → benchmark None, which is fine
    px = _prices({"AAA": [100, 110, 121]}, ["2026-06-05", "2026-06-06", "2026-06-07"])
    res = nav.compute_model_nav("test", price_fn=lambda t, s, e: px, lake_dir=tmp_path)
    assert res["start"] == "2026-06-05"          # derived from run_id
    assert res["points"] == 3
    assert res["last_nav"] == 121.0 and res["last_return_pct"] == 21.0
    shown = nav.show_nav("test", lake_dir=tmp_path)
    assert len(shown["series"]) == 3


# ── movement repo: positions + catalyst ledger ───────────────────────────────

def _write_mov(d, mid, etf, action, qty, amount_eur, sector_id="a",
               attribution=None, fees=0.0, executed_at="2026-06-05T00:00:00Z",
               reattribution=None):
    doc = {
        "$schema": "catalyx/schemas/movement.json", "id": mid, "schema_version": "1.0",
        "executed_at": executed_at, "action": action, "sector_id": sector_id,
        "vehicle": {"etf": etf, "currency": "EUR"}, "amount_eur": amount_eur,
        "qty": qty, "price": (amount_eur / qty if qty else None), "fees": fees,
        "attribution": attribution or [{"catalyst_id": "struct_x", "weight": 1.0}],
        "trigger": "new_catalyst", "conviction": "medium",
        "metadata": {"created_at": executed_at},
    }
    if reattribution:
        doc["reattribution"] = reattribution
        doc["schema_version"] = "1.3"
    (d / f"{mid}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_positions_net_from_movement_files(tmp_path):
    _write_mov(tmp_path, "mov_20260605_a_one", "COPX", "open", 10, 901.0, fees=1.0)
    _write_mov(tmp_path, "mov_20260606_a_two", "COPX", "add", 10, 1000.0)
    p = mr.positions(movements_dir=tmp_path)
    pos = p["holdings"][0]
    assert pos["etf"] == "COPX" and pos["qty"] == 20.0
    assert pos["invested_eur"] == 1901.0           # (901+1) + 1000
    assert pos["weight_pct"] == 100.0


def test_close_realizes_pnl_and_reduces_position(tmp_path):
    _write_mov(tmp_path, "mov_20260605_a_open", "GDX", "open", 10, 500.0)   # avg 50
    _write_mov(tmp_path, "mov_20260610_a_close", "GDX", "close", 4, 280.0)  # cost 200 → +80
    p = mr.positions(movements_dir=tmp_path)
    assert p["realized_eur"] == 80.0
    pos = p["holdings"][0]
    assert pos["qty"] == 6.0 and pos["invested_eur"] == 300.0


def test_catalyst_ledger_splits_credit_by_weight_but_exposes_the_whole_position(tmp_path):
    """Two questions, two numbers, and the cap needs the second one.

    P&L CREDIT splits by weight so one euro of return is not credited twice. RISK does not: if AI
    capex breaks, the whole €500 utilities position is at risk — nobody owns 30% of an ETF. Feeding
    the split number to `correlated_catalyst_cap` also inverts the incentive, since naming a second
    driver would then LOWER the position's weight on the first and buy headroom for free.
    """
    _write_mov(tmp_path, "mov_20260605_a_split", "IQQH", "open", 1, 500.0,
               attribution=[{"catalyst_id": "struct_grid", "weight": 0.7},
                            {"catalyst_id": "struct_ai", "weight": 0.3}])
    led = {e["catalyst_id"]: e for e in mr.catalyst_ledger(movements_dir=tmp_path)}
    assert led["struct_grid"]["invested_eur"] == 350.0
    assert led["struct_ai"]["invested_eur"] == 150.0
    assert led["struct_grid"]["exposure_eur"] == 500.0
    assert led["struct_ai"]["exposure_eur"] == 500.0


def test_declaring_a_second_driver_cannot_buy_cap_headroom(tmp_path):
    """The perverse incentive, pinned: re-attributing a position across two drivers must not
    shrink its exposure to the first. Weights move P&L credit; they never move risk."""
    one = [{"catalyst_id": "struct_ai", "weight": 1.0}]
    two = [{"catalyst_id": "struct_ai", "weight": 0.65},
           {"catalyst_id": "struct_grid", "weight": 0.35}]
    _write_mov(tmp_path, "mov_20260605_a_before", "COPA", "open", 1, 1000.0, attribution=one)
    before = {e["catalyst_id"]: e for e in mr.catalyst_ledger(movements_dir=tmp_path)}

    (tmp_path / "mov_20260605_a_before.json").unlink()
    _write_mov(tmp_path, "mov_20260605_a_after", "COPA", "open", 1, 1000.0, attribution=one,
               reattribution=[{"as_of": "2026-08-31", "attribution": two, "rationale": "grid too"}])
    after = {e["catalyst_id"]: e for e in mr.catalyst_ledger(movements_dir=tmp_path)}

    assert before["struct_ai"]["exposure_eur"] == after["struct_ai"]["exposure_eur"] == 1000.0
    assert after["struct_grid"]["exposure_eur"] == 1000.0      # the new driver carries it all too
    assert after["struct_ai"]["invested_eur"] == 650.0         # credit does move
    assert after["struct_ai"]["reattributed_sectors"] == ["a"]


def test_reattribution_is_present_tense_and_leaves_the_opening_record_alone(tmp_path):
    """`attribution[]` is the dated record of why the line was opened — the validation loop's
    input. `reattribution[]` answers what it is held for now. The ledger reads the latest entry;
    the frozen record stays readable on the file."""
    _write_mov(tmp_path, "mov_20260616_a_pharma", "IUHE", "open", 1, 500.0,
               attribution=[{"catalyst_id": "uncatalyzed", "weight": 1.0}],
               reattribution=[
                   {"as_of": "2026-07-01", "attribution": [{"catalyst_id": "struct_old",
                                                            "weight": 1.0}],
                    "rationale": "superseded"},
                   {"as_of": "2026-08-31", "attribution": [{"catalyst_id": "uncatalyzed",
                                                            "weight": 0.5},
                                                           {"catalyst_id": "struct_patent",
                                                            "weight": 0.5}],
                    "rationale": "shares the patent cliff with the biotech BUY"}])
    led = {e["catalyst_id"]: e for e in mr.catalyst_ledger(movements_dir=tmp_path)}
    assert "struct_old" not in led, "only the LATEST entry is in force"
    assert led["struct_patent"]["exposure_eur"] == 500.0
    assert led["uncatalyzed"]["exposure_eur"] == 500.0

    doc = json.loads((tmp_path / "mov_20260616_a_pharma.json").read_text(encoding="utf-8"))
    assert doc["attribution"] == [{"catalyst_id": "uncatalyzed", "weight": 1.0}]


# ── Real-book ledger: TWR / MWR (2026-08-28) ─────────────────────────────────
#
# The defect these pin: the real curve used to project TODAY's holdings backwards over the whole
# window. A book built in tranches then showed exposure it never had, and every risk metric
# downstream described a portfolio that was never held.

def _mov(mid, on, etf, action, qty, eur, price=None):
    return {"id": mid, "executed_at": f"{on}T10:00:00Z", "action": action, "qty": qty,
            "amount_eur": eur, "price": price, "vehicle": {"etf": etf, "currency": "EUR"}}


def test_daily_ledger_steps_qty_in_on_the_execution_date():
    idx = pd.to_datetime(["2026-06-01", "2026-06-02", "2026-06-03"])
    movs = [_mov("m1", "2026-06-02", "AAA", "open", 10.0, 1000.0)]
    qty, flow = nav.daily_ledger(movs, idx)
    assert list(qty["AAA"]) == [0.0, 10.0, 10.0]     # NOT held on day 1
    assert list(flow) == [0.0, 1000.0, 0.0]


def test_daily_ledger_sell_reduces_qty_and_flows_out():
    idx = pd.to_datetime(["2026-06-01", "2026-06-02"])
    movs = [_mov("m1", "2026-06-01", "AAA", "open", 10.0, 1000.0),
            _mov("m2", "2026-06-02", "AAA", "trim", 4.0, 420.0)]
    qty, flow = nav.daily_ledger(movs, idx)
    assert list(qty["AAA"]) == [10.0, 6.0]
    assert list(flow) == [1000.0, -420.0]


def test_daily_ledger_weekend_trade_lands_on_next_session():
    idx = pd.to_datetime(["2026-06-05", "2026-06-08"])          # Fri, Mon
    qty, flow = nav.daily_ledger([_mov("m1", "2026-06-06", "AAA", "open", 5.0, 500.0)], idx)
    assert list(qty["AAA"]) == [0.0, 5.0]
    assert list(flow) == [0.0, 500.0]


def test_twr_neutralizes_a_contribution():
    """A €1000 top-up into a flat book is not a +100% day."""
    idx = pd.to_datetime(["2026-06-01", "2026-06-02"])
    value = pd.Series([1000.0, 2000.0], index=idx)
    flow = pd.Series([1000.0, 1000.0], index=idx)
    out = nav.twr_series(value, flow)
    assert out[-1]["nav"] == 100.0                              # prices never moved


def test_twr_scores_the_first_day_against_the_price_paid():
    """Start-of-day convention: money invested today is marked at today's close."""
    idx = pd.to_datetime(["2026-06-01"])
    out = nav.twr_series(pd.Series([940.0], index=idx), pd.Series([1000.0], index=idx))
    assert out[0]["nav"] == 94.0                                # −6% really happened


def test_twr_compounds_across_a_contribution():
    idx = pd.to_datetime(["2026-06-01", "2026-06-02", "2026-06-03"])
    # d1: 1000 in, closes 1100 (+10%). d2: +1000 in, book worth 2100 (flat). d3: 2310 (+10%).
    value = pd.Series([1100.0, 2100.0, 2310.0], index=idx)
    flow = pd.Series([1000.0, 1000.0, 0.0], index=idx)
    out = nav.twr_series(value, flow)
    assert [p["nav"] for p in out] == [110.0, 110.0, 121.0]


def test_twr_ignores_days_before_the_book_exists():
    idx = pd.to_datetime(["2026-06-01", "2026-06-02"])
    out = nav.twr_series(pd.Series([0.0, 950.0], index=idx), pd.Series([0.0, 1000.0], index=idx))
    assert out[0]["nav"] == 100.0 and out[1]["nav"] == 95.0


def test_xirr_recovers_a_known_rate():
    import datetime as dt
    flows = [(dt.date(2026, 1, 1), -1000.0), (dt.date(2027, 1, 1), 1100.0)]
    assert abs(nav.xirr(flows) - 0.10) < 1e-3


def test_xirr_none_when_flows_do_not_bracket():
    import datetime as dt
    assert nav.xirr([(dt.date(2026, 1, 1), -100.0), (dt.date(2027, 1, 1), -100.0)]) is None


def test_mwr_beats_twr_when_money_arrives_before_the_gain():
    """The whole reason both numbers exist: timing of contributions changes one, not the other."""
    import datetime as dt
    # €1000 flat for a year, then €1000 more right before a +10% move on the whole book.
    flows = [(dt.date(2026, 1, 1), -1000.0), (dt.date(2026, 12, 1), -1000.0),
             (dt.date(2027, 1, 1), 2200.0)]
    assert nav.xirr(flows) > 0.10


def test_execution_price_check_flags_a_fill_logged_at_the_prior_close():
    px = _prices({"AAA": [60.57, 56.41]}, ["2026-06-04", "2026-06-05"])
    movs = [_mov("m1", "2026-06-05", "AAA", "open", 16.5, 1000.0, price=60.62)]
    w = nav.execution_price_checks(movs, px)
    assert len(w) == 1 and w[0]["matches_prior_close"] is True
    assert w[0]["drift_pct"] > 7


def test_execution_price_check_silent_when_the_fill_matches_the_close():
    px = _prices({"AAA": [60.57, 56.41]}, ["2026-06-04", "2026-06-05"])
    movs = [_mov("m1", "2026-06-05", "AAA", "open", 17.7, 1000.0, price=56.40)]
    assert nav.execution_price_checks(movs, px) == []


def test_daily_ledger_caps_an_oversized_sale_instead_of_going_short():
    idx = pd.to_datetime(["2026-06-01", "2026-06-02"])
    movs = [_mov("m1", "2026-06-01", "AAA", "open", 10.0, 1000.0),
            _mov("m2", "2026-06-02", "AAA", "close", 25.0, 2500.0)]     # hand-authored typo
    qty, _ = nav.daily_ledger(movs, idx)
    assert list(qty["AAA"]) == [10.0, 0.0]                              # never negative


def test_twr_holds_flat_while_the_book_is_empty_then_resumes():
    """Full exit → nothing held → re-entry. The empty stretch must not invent a return, and the
    re-entry must be scored against the price paid, not against the pre-exit NAV."""
    idx = pd.to_datetime(["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"])
    value = pd.Series([1100.0, 0.0, 0.0, 950.0], index=idx)
    flow = pd.Series([1000.0, -1100.0, 0.0, 1000.0], index=idx)
    out = [p["nav"] for p in nav.twr_series(value, flow)]
    assert out == [110.0, 110.0, 110.0, 104.5]                          # 110 × 0.95


def test_twr_partial_sale_is_not_a_loss():
    idx = pd.to_datetime(["2026-06-01", "2026-06-02"])
    value = pd.Series([1000.0, 500.0], index=idx)
    flow = pd.Series([1000.0, -500.0], index=idx)                       # sold half at the mark
    assert [p["nav"] for p in nav.twr_series(value, flow)] == [100.0, 100.0]


# ── A merged catalyst is ONE row, or the cap it feeds is decorative ──────────

def test_a_merged_catalyst_collapses_into_its_survivor(tmp_path, monkeypatch):
    """`attribution[].catalyst_id` is frozen at write time and a catalyst merged afterwards keeps
    its absorbed id there — as it must, since the lake indexes indicator history by that id. But
    the LEDGER is the input to `correlated_catalyst_cap`, and two rows for one economic driver is
    exactly the double count that cap exists to prevent.

    On the real book: `struct_copper_datacenter_demand` merged into `struct_ai_capex_supercycle`,
    and §6 published them as €1,000 + €650 — €1,350 of headroom where €350 existed, on the single
    largest exposure in the book.
    """
    from catalyx.store import structural_catalyst_repo as scr

    monkeypatch.setattr(scr, "merged_map", lambda: {"struct_copper_dc": "struct_ai_capex"})
    _write_mov(tmp_path, "mov_20260604_a_copper", "COPA", "open", 1, 1000.0,
               sector_id="copper_miners",
               attribution=[{"catalyst_id": "struct_copper_dc", "weight": 1.0}])
    _write_mov(tmp_path, "mov_20260608_a_semis", "SEMI", "open", 1, 500.0,
               sector_id="semiconductors_design",
               attribution=[{"catalyst_id": "struct_ai_capex", "weight": 1.0}])

    led = {e["catalyst_id"]: e for e in mr.catalyst_ledger(movements_dir=tmp_path)}
    assert "struct_copper_dc" not in led, "an absorbed id must not hold its own exposure row"
    assert led["struct_ai_capex"]["invested_eur"] == 1500.0
    # The collapse stays auditable — a reader must be able to tie the number to the files.
    assert led["struct_ai_capex"]["absorbed_ids"] == ["struct_copper_dc"]
    assert led["struct_ai_capex"]["sectors"] == ["copper_miners", "semiconductors_design"]

    # The raw record is still readable on demand: the movement files did not change, and the
    # validation loop scores what was actually written.
    raw = {e["catalyst_id"] for e in mr.catalyst_ledger(movements_dir=tmp_path,
                                                        resolve_merged=False)}
    assert raw == {"struct_copper_dc", "struct_ai_capex"}


def test_attribution_drift_names_the_gap_without_rewriting_the_record(tmp_path, monkeypatch):
    """`pharma_large_cap` was opened as a defensive line with `attribution: [uncatalyzed 1.0]`;
    its study now names structural drivers it shares with a biotech BUY on the same table. The cap
    cannot see an overlap filed under `uncatalyzed`.

    Naming it is the fix. The attribution is the dated record of WHY the line was opened and is
    what the validation loop scores — rewriting it here would destroy that.
    """
    from catalyx.execution import portfolio
    from catalyx.store import structural_catalyst_repo as scr

    monkeypatch.setattr(scr, "merged_map", lambda: {})
    monkeypatch.setattr(scr, "_load_all", lambda: [{"id": "struct_glp1"}, {"id": "struct_patent"}])
    monkeypatch.setattr(portfolio, "_sector_catalyst_map",
                        lambda: {"a_pharma": ["struct_glp1", "struct_patent", "cat_20260402_x"],
                                 "a_cyber": ["struct_cyber"]})
    _write_mov(tmp_path, "mov_20260616_a_pharma", "IUHE", "open", 1, 500.0,
               sector_id="a_pharma",
               attribution=[{"catalyst_id": "uncatalyzed", "weight": 1.0}])
    _write_mov(tmp_path, "mov_20260608_a_cyber", "USPY", "open", 1, 500.0,
               sector_id="a_cyber",
               attribution=[{"catalyst_id": "struct_cyber", "weight": 1.0}])

    drift = mr.attribution_drift(movements_dir=tmp_path)
    assert [d["sector_id"] for d in drift] == ["a_pharma"], "a fully attributed line is not drift"
    d = drift[0]
    assert d["uncatalyzed"] is True
    # Event catalysts are excluded: the cap is written per shared primary STRUCTURAL catalyst,
    # and listing every `cat_*` a study mentions would bury the cases that matter.
    assert d["unattributed"] == ["struct_glp1", "struct_patent"]

    # The file on disk is untouched.
    doc = json.loads((tmp_path / "mov_20260616_a_pharma.json").read_text(encoding="utf-8"))
    assert doc["attribution"] == [{"catalyst_id": "uncatalyzed", "weight": 1.0}]


def test_cap_check_prices_the_proposed_table_not_just_the_held_book(tmp_path, monkeypatch):
    """The cap says what a NEW position may take; the rebalance table is where new positions are
    proposed. Until these were joined, a table could route money into a bucket with no headroom
    left and still read as compliant — on the real book, €1,560 into a bucket holding €0.

    And the two action types resolve differently: a BUY has no attribution yet, so the sector
    study is the honest estimate; an ADD is held, so its recorded attribution governs — including
    a driver the review deliberately declined, which re-deriving from the study would overrule.
    """
    from catalyx.config import weights
    from catalyx.execution import portfolio
    from catalyx.store import structural_catalyst_repo as scr

    monkeypatch.setattr(scr, "merged_map", lambda: {})
    monkeypatch.setattr(scr, "_load_all",
                        lambda: [{"id": "struct_ai"}, {"id": "struct_glp1"}, {"id": "struct_pat"}])
    monkeypatch.setattr(portfolio, "_sector_catalyst_map",
                        lambda: {"a_semis": ["struct_ai"], "a_dc": ["struct_ai"],
                                 "a_pharma": ["struct_pat", "struct_glp1"]})
    monkeypatch.setattr(weights, "total_capital_eur", lambda: 10000.0)
    monkeypatch.setattr(weights, "correlated_catalyst_cap",
                        lambda: {"max_combined_pct": 20.0, "enforcement": "warn"})

    _write_mov(tmp_path, "mov_20260608_a_semis", "SEMI", "open", 1, 1800.0, sector_id="a_semis",
               attribution=[{"catalyst_id": "struct_ai", "weight": 1.0}])
    _write_mov(tmp_path, "mov_20260616_a_pharma", "IUHE", "open", 1, 500.0, sector_id="a_pharma",
               attribution=[{"catalyst_id": "uncatalyzed", "weight": 1.0}],
               reattribution=[{"as_of": "2026-08-31",
                               "attribution": [{"catalyst_id": "struct_pat", "weight": 1.0}],
                               "not_attributed": ["struct_glp1"],
                               "rationale": "not a GLP-1 vehicle"}])

    got = {c["catalyst_id"]: c for c in mr.cap_check(
        [{"sector_id": "a_dc", "trade_eur": 500.0},          # BUY — not held
         {"sector_id": "a_pharma", "trade_eur": 400.0}],     # ADD — held
        movements_dir=tmp_path)}

    ai = got["struct_ai"]
    assert (ai["current_eur"], ai["proposed_eur"], ai["post_eur"]) == (1800.0, 500.0, 2300.0)
    assert ai["over"] is True and ai["over_by_eur"] == 300.0
    assert got["struct_pat"]["post_eur"] == 900.0 and got["struct_pat"]["over"] is False
    assert "struct_glp1" not in got, "an ADD must not resurrect a driver the review declined"


def test_drift_closes_by_claiming_or_by_declining_in_writing(tmp_path, monkeypatch):
    """Both halves of the human answer close the row. A driver the review looked at and declined
    must stop reappearing — a check that re-raises an answered question trains its reader to skip
    the table, which is worse than not running it. The reason is on the file, not in a habit."""
    from catalyx.execution import portfolio
    from catalyx.store import structural_catalyst_repo as scr

    monkeypatch.setattr(scr, "merged_map", lambda: {})
    monkeypatch.setattr(scr, "_load_all", lambda: [{"id": "struct_glp1"}, {"id": "struct_patent"}])
    monkeypatch.setattr(portfolio, "_sector_catalyst_map",
                        lambda: {"a_pharma": ["struct_glp1", "struct_patent"]})
    _write_mov(tmp_path, "mov_20260616_a_pharma", "IUHE", "open", 1, 500.0, sector_id="a_pharma",
               attribution=[{"catalyst_id": "uncatalyzed", "weight": 1.0}],
               reattribution=[{"as_of": "2026-08-31",
                               "attribution": [{"catalyst_id": "uncatalyzed", "weight": 0.5},
                                               {"catalyst_id": "struct_patent", "weight": 0.5}],
                               "not_attributed": ["struct_glp1"],
                               "rationale": "not a GLP-1 vehicle"}])
    assert mr.attribution_drift(movements_dir=tmp_path) == []

    # Drop only the declination: the unclaimed driver comes straight back.
    doc = json.loads((tmp_path / "mov_20260616_a_pharma.json").read_text(encoding="utf-8"))
    doc["reattribution"][0].pop("not_attributed")
    (tmp_path / "mov_20260616_a_pharma.json").write_text(json.dumps(doc), encoding="utf-8")
    drift = mr.attribution_drift(movements_dir=tmp_path)
    assert [d["unattributed"] for d in drift] == [["struct_glp1"]]
    assert drift[0]["uncatalyzed"] is False, "half-attributed is no longer filed as uncatalyzed"
    assert drift[0]["reattributed_at"] == "2026-08-31"
