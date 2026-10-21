"""2026-10-21 - position metrics describe the CURRENT holding, in the right currency, on the right history.

Found by auditing a real run, not by a test: `cybersecurity_commercial` showed "133 days held",
a +55 EUR price gain against a -132 EUR currency loss, and "the worst score drift in the book"
(-22). All three were measured against a lot that had been SOLD on 2026-09-01. The reopened line
(bought 2026-10-06, 13 days earlier) carried a price gain and a currency loss that never happened.
Two more faults rode with it: its fill `price` is the EUR figure on a USD line (documented in the
movement's `fill_note`), which the NAV check read as an -11% execution drift; and the risk table
estimated the covariance on the 96 days we have held the book, fell back to daily returns, and read
the book at 18.0% where its weekly history says 14.6%.

Run: uv run pytest tests/unit/test_position_episodes.py -q
"""
from __future__ import annotations

import pandas as pd

from catalyx.execution import nav_engine as nav
from catalyx.execution import position_metrics as pm
from catalyx.store import movement_repo as mr


def _m(mid, on, action, qty, eur, price, ccy="USD", sector="cyber", etf="USPY.L", ctx=None):
    m = {"id": mid, "executed_at": f"{on}T10:00:00Z", "action": action, "sector_id": sector,
         "qty": qty, "amount_eur": eur, "price": price,
         "vehicle": {"etf": etf, "currency": ccy}}
    if ctx is not None:
        m["score_context"] = ctx
    return m


# ── the current holding ──────────────────────────────────────────────────────

def test_a_reopened_line_is_a_new_holding():
    movs = [_m("a", "2026-06-08", "open", 14.0, 500.0, 40.92),
            _m("b", "2026-09-01", "close", 14.0, 540.0, 45.0),
            _m("c", "2026-10-06", "open", 18.0, 840.0, 51.07)]
    assert [m["id"] for m in mr.open_episodes(movs)["USPY.L"]] == ["c"]


def test_a_partial_trim_keeps_the_holding_and_a_full_close_ends_it():
    movs = [_m("a", "2026-06-08", "open", 10.0, 500.0, 50.0),
            _m("b", "2026-07-01", "trim", 4.0, 220.0, 55.0)]
    assert [m["id"] for m in mr.open_episodes(movs)["USPY.L"]] == ["a", "b"]
    assert mr.open_episodes(movs + [_m("c", "2026-07-02", "close", 6.0, 330.0, 55.0)]) == {}


def test_entry_facts_ignore_the_lot_that_was_sold():
    movs = [_m("a", "2026-06-08", "open", 14.0, 500.0, 40.92),
            _m("b", "2026-09-01", "close", 14.0, 540.0, 45.0),
            _m("c", "2026-10-06", "open", 18.0, 960.0, 53.0)]
    e = pm._entry_facts(movs)["USPY.L"]
    assert e["first_entry"] == "2026-10-06"
    assert e["entry_price_native"] == 53.0           # not the 40.92/53.0 blend of two holdings


def test_entry_context_is_the_score_the_current_holding_was_opened_under():
    movs = [_m("a", "2026-06-08", "open", 14.0, 500.0, 40.92, ctx={"composite": 77.1, "rank": 5}),
            _m("b", "2026-09-01", "close", 14.0, 540.0, 45.0, ctx={"composite": 54.0, "rank": 8}),
            _m("c", "2026-10-06", "open", 18.0, 960.0, 53.0, ctx={"composite": 55.0, "rank": 7})]
    assert pm._entry_context(movs) == {"cyber": {"composite": 55.0, "rank": 7}}


def test_a_reopen_that_cites_only_its_run_is_resolved_from_that_run(monkeypatch):
    monkeypatch.setattr(mr, "context_for_run",
                        lambda sid, run, **k: {"run_id": run, "composite": 55.2, "rank": 6})
    movs = [_m("a", "2026-06-08", "open", 14.0, 500.0, 40.92, ctx={"composite": 77.1, "rank": 5}),
            _m("b", "2026-09-01", "close", 14.0, 540.0, 45.0),
            _m("c", "2026-10-06", "open", 18.0, 960.0, 53.0, ctx={"run_id": "run_20261006"})]
    assert pm._entry_context(movs)["cyber"]["composite"] == 55.2


def test_a_reopen_with_no_score_at_all_stays_unknown_never_the_old_lots_score(monkeypatch):
    monkeypatch.setattr(mr, "context_for_run", lambda *a, **k: None)
    movs = [_m("a", "2026-06-08", "open", 14.0, 500.0, 40.92, ctx={"composite": 77.1, "rank": 5}),
            _m("b", "2026-09-01", "close", 14.0, 540.0, 45.0),
            _m("c", "2026-10-06", "open", 18.0, 960.0, 53.0)]
    assert pm._entry_context(movs) == {}


# ── an EUR price on a USD line ───────────────────────────────────────────────

def test_price_times_qty_reproducing_the_euro_amount_means_the_price_is_in_euros():
    eur_fill = _m("c", "2026-10-06", "open", 18.44937403, 840.0, 45.53)
    usd_fill = _m("a", "2026-06-08", "open", 13.99, 500.0, 40.92)          # 40.92 USD ~ 35.7 EUR
    assert mr.price_is_eur(eur_fill) is True
    assert mr.price_is_eur(usd_fill) is False


def test_an_euro_line_or_a_record_without_the_numbers_is_never_flagged():
    assert mr.price_is_eur(_m("a", "2026-06-08", "open", 10.0, 500.0, 50.0, ccy="EUR")) is False
    assert mr.price_is_eur({"vehicle": {"currency": "USD"}, "price": None, "qty": 1,
                            "amount_eur": 5}) is False


def test_an_eur_fill_is_not_compared_with_a_usd_close():
    native = pd.DataFrame({"USPY.L": [50.0, 51.25]}, index=pd.to_datetime(["2026-10-05", "2026-10-06"]))
    eur = pd.DataFrame({"USPY.L": [45.0, 45.60]}, index=native.index)
    fill = _m("c", "2026-10-06", "open", 18.44937403, 840.0, 45.53)
    # it used to be a -11% "execution drift" that never happened; with no EUR frame it is skipped
    assert nav.execution_price_checks([fill], native) == []
    # where the EUR frame exists it is compared with that, and passes
    assert nav.execution_price_checks([fill], native, eur_prices=eur) == []
    # a fill genuinely priced in USD is still checked against the USD close
    usd_fill = _m("a", "2026-10-06", "open", 10.0, 380.0, 44.0)         # 440 USD ~ 380 EUR
    assert nav.execution_price_checks([usd_fill], native)[0]["drift_pct"] < -10


def test_an_eur_fill_with_no_eur_frame_is_skipped_not_misjudged():
    native = pd.DataFrame({"USPY.L": [50.0, 51.25]}, index=pd.to_datetime(["2026-10-05", "2026-10-06"]))
    fill = _m("c", "2026-10-06", "open", 18.44937403, 840.0, 45.53)
    assert nav.execution_price_checks([fill], native, eur_prices=None) == []
    # the contract that matters: with an EUR frame that disagrees, the real drift IS flagged
    far = pd.DataFrame({"USPY.L": [60.0, 61.0]}, index=native.index)
    assert nav.execution_price_checks([fill], native, eur_prices=far)[0]["drift_pct"] < -20


def test_an_eur_priced_lot_enters_the_split_at_that_days_exchange_rate():
    idx = pd.to_datetime(["2026-10-05", "2026-10-06", "2026-10-07"])
    native = pd.DataFrame({"USPY.L": [50.0, 51.25, 49.0]}, index=idx)
    eur = pd.DataFrame({"USPY.L": [44.6, 45.6, 43.7]}, index=idx)        # EUR per native ~0.8898
    movs = [_m("c", "2026-10-06", "open", 18.44937403, 840.0, 45.53)]
    e = pm._entry_facts(movs)["USPY.L"]
    assert e["entry_price_native"] is None and e["eur_lots"]            # not guessable alone
    px = pm._entry_price_native(e, native, eur, "USPY.L")
    assert abs(px - 45.53 / (45.6 / 51.25)) < 1e-4                      # ~51.17 USD, not 45.53
    # the split now has an entry price that is in USD: price effect and currency effect are real
    split = pm.pnl_split(18.449, px, 49.0, 45.6 / 51.25, 43.7 / 49.0, -76.0)
    assert split["pnl_price_eur"] < 0 and abs(split["pnl_fx_eur"]) < 10


def test_without_the_frames_the_entry_price_is_unknown_not_invented():
    e = pm._entry_facts([_m("c", "2026-10-06", "open", 18.0, 840.0, 46.67)])["USPY.L"]
    assert pm._entry_price_native(e, None, None, "USPY.L") is None


def test_a_native_lot_and_an_eur_lot_average_in_the_same_currency():
    idx = pd.to_datetime(["2026-06-08", "2026-10-06"])
    native = pd.DataFrame({"USPY.L": [41.0, 51.25]}, index=idx)
    eur = pd.DataFrame({"USPY.L": [35.8, 45.6]}, index=idx)
    movs = [_m("a", "2026-06-08", "open", 10.0, 358.0, 41.0),             # USD price
            _m("c", "2026-10-06", "add", 10.0, 456.0, 45.6)]              # EUR price
    e = pm._entry_facts(movs)["USPY.L"]
    px = pm._entry_price_native(e, native, eur, "USPY.L")
    assert abs(px - (41.0 * 10 + 51.25 * 10) / 20) < 1e-3


# ── the history the covariance is read from ──────────────────────────────────

def _frame(n):
    return pd.DataFrame({"A": range(n), "B": range(n)}, index=pd.date_range("2026-01-01", periods=n))


def test_the_covariance_reads_the_longer_history_when_there_is_one():
    short, longer = _frame(96), _frame(342)
    assert len(pm._covariance_frame(short, ["A", "B"], "2026-10-19", long_fn=lambda c, a: longer)) == 342


def test_the_covariance_keeps_the_holding_window_when_the_long_read_fails_or_is_not_longer():
    short = _frame(96)

    def boom(c, a):
        raise OSError("no lake")
    assert pm._covariance_frame(short, ["A", "B"], "x", long_fn=boom) is short
    assert pm._covariance_frame(short, ["A", "B"], "x", long_fn=lambda c, a: _frame(50)) is short
    assert pm._covariance_frame(short, ["A", "B"], "x", long_fn=lambda c, a: None) is short


def test_injected_prices_keep_their_own_window():
    short = _frame(96)
    assert pm._covariance_frame(short, ["A", "B"], "x", price_fn=lambda *a: None) is short


# ── the model NAV: a closed venue is not a missing price ─────────────────────

def _holdings(tmp_path, rows):
    from catalyx.execution import portfolio as pf
    from catalyx.store import lake
    for rid in {r["run_id"] for r in rows}:
        part = [dict(r, portfolio_id="catalyx", rank_in_portfolio=i + 1)
                for i, r in enumerate(x for x in rows if x["run_id"] == rid)]
        lake.append_partition(pf._HOLDING_TABLE, pd.DataFrame(part),
                              {"portfolio_id": "catalyx", "run_id": rid},
                              overwrite=True, lake_dir=tmp_path)


def _live(tmp_path, frame):
    return nav.compute_live_nav("catalyx", inception="2026-08-28", as_of="2026-09-02",
                                price_fn=lambda t, s, e: frame, persist=False, lake_dir=tmp_path)


def test_a_segment_opening_on_a_bank_holiday_does_not_turn_that_line_into_cash(tmp_path):
    # 2026-08-31 was the UK summer bank holiday: the LSE line has no print on the day the second
    # segment opens. It used to be held as CASH for the whole segment (59.9% of the real book's
    # model on one day), so a -10% on it never reached the curve.
    idx = pd.to_datetime(["2026-08-28", "2026-08-31", "2026-09-01", "2026-09-02"])
    frame = pd.DataFrame({"A.L": [100.0, None, 90.0, 90.0], "B.DE": [100.0] * 4,
                          "SPY": [100.0] * 4}, index=idx)
    _holdings(tmp_path, [
        {"run_id": "run_20260828_100000", "primary_etf": "B.DE", "weight_pct": 100.0},
        {"run_id": "run_20260831_100000", "primary_etf": "A.L", "weight_pct": 50.0},
        {"run_id": "run_20260831_100000", "primary_etf": "B.DE", "weight_pct": 50.0}])
    r = _live(tmp_path, frame)
    assert abs(r["last_nav"] - 95.0) < 1e-6                 # 0.5 x (-10%) reaches the curve
    assert r["unpriced_weight_pct"] == 0.0 and r["unpriced_tickers"] == []


def test_a_vehicle_that_never_resolves_is_cash_and_the_curve_says_so(tmp_path):
    idx = pd.to_datetime(["2026-08-28", "2026-08-31", "2026-09-01", "2026-09-02"])
    frame = pd.DataFrame({"B.DE": [100.0, 100.0, 110.0, 110.0], "SPY": [100.0] * 4}, index=idx)
    _holdings(tmp_path, [
        {"run_id": "run_20260828_100000", "primary_etf": "B.DE", "weight_pct": 60.0},
        {"run_id": "run_20260828_100000", "primary_etf": "DEAD.DE", "weight_pct": 40.0}])
    r = _live(tmp_path, frame)
    assert r["unpriced_tickers"] == ["DEAD.DE"]
    assert abs(r["unpriced_weight_pct"] - 40.0) < 1e-6 and abs(r["unpriced_weight_peak_pct"] - 40.0) < 1e-6
    assert abs(r["last_nav"] - 106.0) < 1e-6                # 60% invested at +10%, 40% flat cash
    assert "DEAD.DE" in nav._unpriced_line(r) and "CASH" in nav._unpriced_line(r)


def test_a_fully_priced_curve_prints_no_warning():
    assert nav._unpriced_line({"unpriced_weight_pct": 0.0}) is None
    assert nav._unpriced_line({}) is None


def test_unpriced_weight_mirrors_the_rule_holdings_nav_applies():
    idx = pd.to_datetime(["2026-08-31", "2026-09-01"])
    px = pd.DataFrame({"A.L": [None, 90.0], "B.DE": [100.0, 100.0]}, index=idx)
    h = [{"primary_etf": "A.L", "weight_pct": 50.0}, {"primary_etf": "B.DE", "weight_pct": 50.0}]
    # on the raw slice the holiday line IS unpriced - which is why the caller forward-fills first
    assert nav.unpriced_weight(h, px) == (0.5, ["A.L"])
    assert nav.unpriced_weight(h, px.ffill()) == (0.5, ["A.L"])   # still NaN: nothing before it
    assert nav.unpriced_weight(h, pd.DataFrame({"A.L": [80.0, 90.0], "B.DE": [1.0, 1.0]},
                                               index=idx)) == (0.0, [])
