"""Flow-proxy logic — pure / network-free. Covers the FLOW_PROXY decoupling: the
data_quality provenance grading, the [0,100] score mapping, and that flow_to_rows
threads the proxy ticker through to the lake-row shape (including using the proxy
ticker — not the execution ticker — to look up the raw shares/nav data)."""
from __future__ import annotations

from catalyx.data import flow_data as fd


# ── data_quality provenance grading ──────────────────────────────────────────

def test_quality_estimated_when_no_flow_pct():
    # no week-over-week delta yet → neutral placeholder, regardless of proxy
    assert fd._flow_data_quality(None, False) == "estimated"
    assert fd._flow_data_quality(None, True) == "estimated"


def test_quality_computed_own_vehicle():
    # a flow_pct only ever exists on a direct basis (the gate guarantees it)
    assert fd._flow_data_quality(1.2, proxy_used=False) == "computed"


def test_quality_proxy_computed_when_proxy():
    assert fd._flow_data_quality(-0.8, proxy_used=True) == "proxy_computed"


# ── score mapping ─────────────────────────────────────────────────────────────

def test_flow_score_neutral_on_none():
    assert fd._flow_score(None) == 50.0


def test_flow_score_inflow_outflow_symmetry_and_clamp():
    assert fd._flow_score(0.0) == 50.0
    assert fd._flow_score(1.0) == 58.0       # 50 + 1*8
    assert fd._flow_score(-1.0) == 42.0
    assert fd._flow_score(99.0) == 90.0      # clamped high
    assert fd._flow_score(-99.0) == 10.0     # clamped low


# ── SECTOR_FLOW_TICKERS coverage + chain conventions ─────────────────────────

def _investable_sector_ids() -> set[str]:
    import yaml
    from pathlib import Path
    tax = Path(__file__).parents[2] / "catalyx" / "config" / "sector_taxonomy.yaml"
    return {
        s["id"] for s in yaml.safe_load(tax.read_text())["sectors"]
        if s.get("investable", False) and not s.get("watch_only", False)
    }


def _buyable_tickers() -> set[str]:
    import yaml
    from pathlib import Path
    uni = Path(__file__).parents[2] / "catalyx" / "config" / "etf_universe.yaml"
    return {
        e["ticker"] for lst in yaml.safe_load(uni.read_text())["etf_universe"].values()
        for e in lst
    }


def test_flow_chain_head_is_a_buyable_vehicle():
    """chain[0] se expone como SECTOR_TICKERS (\"primary tradeable ticker\").

    Antes de la v2.0 varios chain[0] eran ETFs US no-UCITS (COPX, GDX, XLE, IBB...):
    el alias afirmaba que eran operables cuando un retail en Espana no puede comprarlos.
    Este test impide que vuelva a colarse uno.
    """
    buyable = _buyable_tickers()
    offenders = {sid: chain[0] for sid, chain in fd.SECTOR_FLOW_TICKERS.items()
                 if chain[0] not in buyable}
    assert not offenders, f"chain[0] fuera de etf_universe.yaml: {offenders}"


def test_flow_chains_are_ordered_lists_with_us_fallbacks():
    m = fd.SECTOR_FLOW_TICKERS
    # Universo v2.0 (2026-08-27): la cobertura ya NO se mide por amplitud. El criterio
    # anterior (>= 45 sectores) premiaba justo el defecto que la v2.0 elimina — listar
    # sectores sin vehiculo comprable. El invariante correcto es la ALINEACION: un
    # sector tiene senal de flujo si y solo si es investable en la taxonomia.
    assert len(m) == len(_investable_sector_ids()), sorted(set(m) ^ set(_investable_sector_ids()))
    # every entry is a non-empty ordered list of ticker strings
    for sid, chain in m.items():
        assert isinstance(chain, list) and chain, sid
        assert all(isinstance(t, str) and t for t in chain), sid
    # UCITS-primary global themes carry a US sibling fallback for the signal
    assert fd.SECTOR_FLOW_TICKERS["gold_physical"][0] == "IGLN.L"
    assert "GLD" in fd.SECTOR_FLOW_TICKERS["gold_physical"]
    assert "SLV" in fd.SECTOR_FLOW_TICKERS["silver_physical"]
    assert "SOXX" in fd.SECTOR_FLOW_TICKERS["semiconductors_design"]
    # back-compat alias = chain head
    assert fd.SECTOR_TICKERS["copper_miners"] == fd.SECTOR_FLOW_TICKERS["copper_miners"][0]


def test_resolve_flow_signal_prefers_computable_then_baseline(monkeypatch):
    # synthetic fetcher: UCITS primary has no shares; first US fallback has a computable flow
    fakes = {
        "IGLN.L": {"ticker": "IGLN.L", "error": "no shares from any source", "shares_source": None},
        "GLD": {"ticker": "GLD", "shares_outstanding": 260e6, "shares_source": "stockanalysis",
                "flow_pct": 1.5},
    }
    monkeypatch.setattr(fd, "_fetch_flow_for_ticker", lambda tk, health, lookback_days=7: fakes.get(tk))
    tk, res, kind = fd._resolve_flow_signal(["IGLN.L", "GLD", "IAU"], fd.new_health())
    assert (tk, kind) == ("GLD", "flow")
    assert res["flow_pct"] == 1.5

    # when nothing computes but a later ticker has shares → 'baseline' (writes for next run)
    fakes2 = {
        "IGLN.L": {"ticker": "IGLN.L", "error": "no shares from any source", "shares_source": None},
        "GLD": {"ticker": "GLD", "shares_outstanding": 260e6, "shares_source": "stockanalysis",
                "flow_pct": None},
    }
    monkeypatch.setattr(fd, "_fetch_flow_for_ticker", lambda tk, health, lookback_days=7: fakes2.get(tk))
    tk, res, kind = fd._resolve_flow_signal(["IGLN.L", "GLD", "IAU"], fd.new_health())
    assert (tk, kind) == ("GLD", "baseline")


def test_trailing_window_flow_moving_average(monkeypatch):
    from datetime import date, timedelta
    today = date.today()
    s_old, s_yest = 100_000_000.0, 106_000_000.0   # +6M over 59 days = long-run daily avg
    monkeypatch.setattr(fd, "_load_shares_series",
                        lambda tk, lb: [(today - timedelta(days=60), s_old),
                                        (today - timedelta(days=1), s_yest)])
    shares_now = 110_000_000.0                       # +4M in one day = the spike
    out = fd._trailing_window_flow("X", shares_now, 90, 7)
    assert out["flow_window_days"] == 7
    assert out["flow_days_covered"] == 7
    # 6 days at the long-run daily avg + 1 day (today) at the spike, then ×7 / shares_now
    rate_a = (s_yest - s_old) / 59
    rate_b = (shares_now - s_yest) / 1
    expected = (rate_b + 6 * rate_a) / shares_now * 100   # = (avg_daily × 7)/shares × 100
    assert abs(out["flow_pct"] - round(expected, 3)) < 0.05


def test_trailing_window_flow_none_when_no_history(monkeypatch):
    monkeypatch.setattr(fd, "_load_shares_series", lambda tk, lb: [])
    assert fd._trailing_window_flow("X", 1e8, 90, 7) is None


def test_parse_human_num():
    assert fd._parse_human_num("60.50M") == 60_500_000
    assert fd._parse_human_num("$14.01B") == 14_010_000_000
    assert fd._parse_human_num("362,500,000") == 362_500_000
    assert fd._parse_human_num(None) is None
    assert fd._parse_human_num("n/a") is None


# ── flow_to_rows threads proxy provenance + reads raw from the proxy ticker ────

def test_flow_to_rows_uses_proxy_ticker_for_raw_lookup():
    snapshot = {
        "date": "2026-06-06", "generated_at": "2026-06-06", "source": "test",
        "sector_scores": {
            "gold_physical": {
                "ticker": "IGLN.L",           # execution vehicle (UCITS, no shares)
                "flow_proxy_ticker": "GLD",   # signal read from the sibling
                "flow_proxy_used": True,
                "flow_confirmation": 61.8,
                "flow_pct": 1.47,
                "implied_aum_m_usd": 150000.0,
                "data_quality": "proxy_computed",
                "inst_sponsorship_score": None,
                "inst_13f_filer_count": None,
                "inst_source": "not_available_ucits",
            },
        },
        # raw etfs are keyed by the SIGNAL ticker (GLD), not the execution ticker
        "etfs": {
            "GLD": {"shares_outstanding": 260_300_000, "nav_price": 400.7,
                    "shares_delta": 1_000_000, "flow_usd_1w": 400_700_000},
        },
    }
    rows = fd.flow_to_rows(snapshot)
    assert len(rows) == 1
    r = rows[0]
    assert r["ticker"] == "IGLN.L"           # execution vehicle preserved
    assert r["flow_proxy_ticker"] == "GLD"
    assert r["flow_proxy_used"] is True
    assert r["data_quality"] == "proxy_computed"
    # raw shares/nav must come from the proxy (GLD), not the empty IGLN.L
    assert r["shares_outstanding"] == 260_300_000
    assert r["nav_price"] == 400.7


def test_carry_forward_reuses_last_real_reading(monkeypatch):
    import pandas as pd
    from datetime import date, timedelta

    yest = (date.today() - timedelta(days=1)).isoformat()
    old = (date.today() - timedelta(days=30)).isoformat()
    df = pd.DataFrame([
        {"ticker": "COPX", "flow_proxy_ticker": "COPX", "date": yest, "flow_confirmation": 61.8,
         "flow_pct": 1.47, "data_quality": "computed"},
        {"ticker": "COPX", "flow_proxy_ticker": "COPX", "date": old, "flow_confirmation": 70.0,
         "flow_pct": 2.5, "data_quality": "computed"},
        # GLD only has a neutral placeholder → nothing real to carry
        {"ticker": "IGLN.L", "flow_proxy_ticker": "GLD", "date": yest, "flow_confirmation": 50.0,
         "flow_pct": None, "data_quality": "estimated"},
    ])
    monkeypatch.setattr("catalyx.store.lake.read_table", lambda _t: df)

    # keyed by TICKER: COPX finds the most recent real reading
    got = fd._carry_forward_flow("COPX")
    assert got is not None
    assert got["data_quality"] == "carried"
    assert got["flow_confirmation"] == 61.8         # most recent eligible row, not the old one
    assert got["carried_from"] == yest
    assert got["carried_quality"] == "computed"

    # a ticker whose only recent row is 'estimated' is not eligible for carry-forward
    assert fd._carry_forward_flow("GLD") is None


def test_carry_forward_respects_max_age(monkeypatch):
    import pandas as pd
    from datetime import date, timedelta

    old = (date.today() - timedelta(days=30)).isoformat()
    df = pd.DataFrame([
        {"ticker": "COPX", "flow_proxy_ticker": "COPX", "date": old, "flow_confirmation": 70.0,
         "flow_pct": 2.5, "data_quality": "computed"},
    ])
    monkeypatch.setattr("catalyx.store.lake.read_table", lambda _t: df)
    # 30 days old > 7-day window → stale, do not parrot it
    assert fd._carry_forward_flow("COPX") is None


def test_flow_to_rows_non_proxy_sector_defaults():
    snapshot = {
        "date": "2026-06-06", "generated_at": "2026-06-06", "source": "test",
        "sector_scores": {
            "copper_miners": {
                "ticker": "COPX", "flow_proxy_ticker": "COPX", "flow_proxy_used": False,
                "flow_confirmation": 61.8, "flow_pct": 1.47,
                "implied_aum_m_usd": 2100.0, "data_quality": "computed",
            },
        },
        "etfs": {"COPX": {"shares_outstanding": 50_000_000, "nav_price": 42.0}},
    }
    r = fd.flow_to_rows(snapshot)[0]
    assert r["flow_proxy_used"] is False
    assert r["flow_proxy_ticker"] == "COPX"
    assert r["shares_outstanding"] == 50_000_000
