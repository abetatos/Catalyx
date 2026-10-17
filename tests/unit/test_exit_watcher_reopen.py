"""A re-opened sector must not inherit the stops of the lot that was already sold."""
from catalyx.scorer import exit_watcher


def _mov(mid, action, etf, at, stops=(), attr=()):
    return {"id": mid, "action": action, "vehicle": {"etf": etf}, "executed_at": at,
            "risk_discipline": {"invalidation": [{"id": s} for s in stops], "assumptions": []},
            "attribution": [{"catalyst_id": c} for c in attr]}


def test_a_closed_lots_stops_do_not_reach_the_reopened_position():
    """REGRESSION (2026-09-02). grid_infrastructure_utilities: opened 2026-06-04 at 11.27, closed
    2026-08-20, re-opened 2026-09-02 at 8.523. The June stop (threshold 11.27−20% = 9.20) fired
    against 8.591 with 13 consecutive breaching days — of a position that had not existed for any
    of them — and the table answered REDUCE on a line bought two hours earlier and up 0.8%."""
    movs = [_mov("m1", "open", "IQQH.DE", "2026-06-04T00:00:00Z", ("inv_04",), ("cat_old",)),
            _mov("m2", "close", "IQQH.DE", "2026-08-20T00:00:00Z"),
            _mov("m3", "open", "IQQH.DE", "2026-09-02T07:52:00Z", ("inv_01",), ("cat_new",))]
    inv, _asm, attr = exit_watcher._risk_for_etf("IQQH.DE", movs)
    assert [i["id"] for i in inv] == ["inv_01"]
    assert [a["catalyst_id"] for a in attr] == ["cat_new"], "attribution was duplicated too"


def test_a_trim_does_not_reset_the_lot():
    """A trim is partial: the position and its thesis survive it, so its stops must survive too."""
    movs = [_mov("m1", "open", "X.L", "2026-06-04T00:00:00Z", ("inv_01",)),
            _mov("m2", "trim", "X.L", "2026-07-01T00:00:00Z"),
            _mov("m3", "add", "X.L", "2026-08-01T00:00:00Z", ("inv_02",))]
    inv, _a, _t = exit_watcher._risk_for_etf("X.L", movs)
    assert {i["id"] for i in inv} == {"inv_01", "inv_02"}


def test_adds_after_the_reopen_still_contribute():
    movs = [_mov("m1", "open", "X.L", "2026-01-01T00:00:00Z", ("dead",)),
            _mov("m2", "close", "X.L", "2026-02-01T00:00:00Z"),
            _mov("m3", "open", "X.L", "2026-03-01T00:00:00Z", ("live_a",)),
            _mov("m4", "add", "X.L", "2026-04-01T00:00:00Z", ("live_b",))]
    inv, _a, _t = exit_watcher._risk_for_etf("X.L", movs)
    assert {i["id"] for i in inv} == {"live_a", "live_b"}


def test_a_missing_timestamp_keeps_the_stop_rather_than_dropping_it():
    """Fails toward KEEPING the discipline: a mechanism that discards a live stop on a data gap is
    a mechanism that cancels a sell, and those must fail toward the sale."""
    movs = [_mov("m1", "close", "X.L", "2026-02-01T00:00:00Z"),
            _mov("m2", "open", "X.L", None, ("undated",))]
    inv, _a, _t = exit_watcher._risk_for_etf("X.L", movs)
    assert [i["id"] for i in inv] == ["undated"]
