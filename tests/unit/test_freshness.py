"""Tests for the indicator freshness audit (catalyx.scorer.freshness).

Core regression: an ANNUAL-cadence indicator must NOT be flagged stale just because its last
data point is ~6 months old — the bug that over-flagged Gartner/IBM-X-Force/BloombergNEF
indicators (sourced annually, mislabeled `quarterly`) at the 95-day quarterly threshold.
"""
from datetime import date

import pytest

from catalyx.scorer import freshness


def _write_catalyst(dir_path, cid, indicators):
    lines = [f"id: {cid}", "indicators:"]
    for ind in indicators:
        lines.append(f"- id: {ind['id']}")
        lines.append(f"  name: {ind.get('name', ind['id'])}")
        lines.append(f"  check_frequency: {ind['check_frequency']}")
        if ind.get("last_date") is not None:
            lines.append(f"  last_date: \"{ind['last_date']}\"")
    (dir_path / f"{cid}.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture()
def patched_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(freshness, "_STRUCTURAL_DIR", tmp_path)
    return tmp_path


AS_OF = date(2026, 6, 5)


def test_annual_indicator_not_stale_at_124_days(patched_dir):
    # IBM X-Force style: annual report, last value 2026-02-01 → 124 days old, still fresh.
    _write_catalyst(patched_dir, "struct_x", [
        {"id": "ind_01", "check_frequency": "annual", "last_date": "2026-02-01"},
    ])
    rows = freshness.audit_indicators(AS_OF)
    assert rows[0]["stale"] is False
    assert rows[0]["reason"] == "fresh"
    assert rows[0]["threshold_days"] == 400


def test_quarterly_indicator_stale_past_95_days(patched_dir):
    _write_catalyst(patched_dir, "struct_x", [
        {"id": "ind_01", "check_frequency": "quarterly", "last_date": "2026-03-01"},  # 96d
    ])
    rows = freshness.audit_indicators(AS_OF)
    assert rows[0]["stale"] is True
    assert rows[0]["reason"] == "overdue"


def test_annual_indicator_stale_past_400_days(patched_dir):
    _write_catalyst(patched_dir, "struct_x", [
        {"id": "ind_01", "check_frequency": "annual", "last_date": "2024-01-01"},  # ~886d
    ])
    assert freshness.audit_indicators(AS_OF)[0]["stale"] is True


def test_missing_last_date_is_stale(patched_dir):
    _write_catalyst(patched_dir, "struct_x", [
        {"id": "ind_01", "check_frequency": "monthly", "last_date": None},
    ])
    row = freshness.audit_indicators(AS_OF)[0]
    assert row["stale"] is True
    assert row["reason"] == "no_last_date"


def test_unrecognized_cadence_falls_back_to_monthly_and_flags_mislabel(patched_dir):
    _write_catalyst(patched_dir, "struct_x", [
        {"id": "ind_01", "check_frequency": "fortnightly", "last_date": "2026-06-01"},
    ])
    row = freshness.audit_indicators(AS_OF)[0]
    assert row["cadence"] == "monthly"  # default fallback
    assert row["cadence_mislabeled"] is True


def test_overdue_returns_only_stale(patched_dir):
    _write_catalyst(patched_dir, "struct_x", [
        {"id": "ind_fresh", "check_frequency": "annual", "last_date": "2026-02-01"},
        {"id": "ind_stale", "check_frequency": "quarterly", "last_date": "2026-01-01"},
    ])
    overdue = freshness.overdue(AS_OF)
    assert {r["indicator_id"] for r in overdue} == {"ind_stale"}


def test_merged_and_deactivated_catalysts_are_not_audited(tmp_path, monkeypatch):
    """A merged catalyst's indicators will never be refreshed again, so auditing them
    manufactures permanent 'stale' rows that inflate the review's work list with dead work.
    (Real case: 18 of 66 audited indicators after the 2026-08-27 universe cut.)"""
    import yaml

    from catalyx.scorer import freshness as fr

    d = tmp_path / "structural"
    d.mkdir()
    (d / "live.yaml").write_text(yaml.safe_dump({
        "id": "struct_live", "status": "active",
        "indicators": [{"id": "ind_01", "check_frequency": "monthly", "last_date": "2020-01-01"}],
    }), encoding="utf-8")
    (d / "gone.yaml").write_text(yaml.safe_dump({
        "id": "struct_gone", "status": "merged",
        "indicators": [{"id": "ind_01", "check_frequency": "monthly", "last_date": "2020-01-01"}],
    }), encoding="utf-8")
    monkeypatch.setattr(fr, "_STRUCTURAL_DIR", d)

    ids = {r["catalyst_id"] for r in fr.audit_indicators()}
    assert ids == {"struct_live"}
    assert {r["catalyst_id"] for r in fr.overdue()} == {"struct_live"}
    # …but the dead state is still inspectable on demand.
    assert {r["catalyst_id"] for r in fr.audit_indicators(include_inactive=True)} == \
        {"struct_live", "struct_gone"}


# ── E1 — a spending row must be able to carry the age of its own evidence ────

def _catalysts(tmp_path, monkeypatch, spec: dict):
    """{catalyst_id: [(indicator_id, cadence, last_date), …]} → a temp structural dir."""
    import yaml

    from catalyx.scorer import freshness as fr

    d = tmp_path / "structural"
    d.mkdir()
    for cid, inds in spec.items():
        (d / f"{cid}.yaml").write_text(yaml.safe_dump({
            "id": cid, "status": "active",
            "indicators": [{"id": i, "check_frequency": c, "last_date": l} for i, c, l in inds],
        }), encoding="utf-8")
    monkeypatch.setattr(fr, "_STRUCTURAL_DIR", d)
    return fr


def test_blind_is_a_harder_state_than_stale_and_needs_twice_the_cadence(tmp_path, monkeypatch):
    """One missed quarter is a late reading. Two is a catalyst whose intensity is describing a
    world nobody has looked at — and `struct_china_luxury_recovery` was funding a €1,020 BUY at
    240 days over a quarterly cadence."""
    from datetime import date

    fr = _catalysts(tmp_path, monkeypatch, {
        "c_fresh": [("i1", "monthly", "2026-08-20")],
        "c_stale": [("i1", "quarterly", "2026-05-01")],    # 122d, over 95 but under 2×95
        "c_blind": [("i1", "quarterly", "2025-09-30")],    # 335d, far past 2×95
    })
    got = fr.by_catalyst(as_of=date(2026, 8, 31))
    assert got["c_fresh"]["status"] == "fresh"
    assert got["c_stale"]["status"] == "stale"
    assert got["c_blind"]["status"] == "blind"
    # The number is days OVER the cadence, not days since the reading — the same convention §8
    # uses, so "240d" means "240 days later than it was due", not "240 days old".
    assert got["c_blind"]["label"] == "blind (240d)"          # 335d since − 95d quarterly
    assert got["c_stale"]["label"] == "stale (27d)"           # 122d since − 95d quarterly


def test_a_never_observed_indicator_outranks_any_finite_lateness(tmp_path, monkeypatch):
    """A missing `last_date` is the most blind state there is. Sorting it as zero days over would
    let the catalyst nobody ever measured report `fresh`."""
    from datetime import date

    fr = _catalysts(tmp_path, monkeypatch, {
        "c": [("late", "monthly", "2026-07-01"), ("never", "monthly", None)],
    })
    got = fr.by_catalyst(as_of=date(2026, 8, 31))["c"]
    assert got["status"] == "blind" and got["worst_indicator_id"] == "never"
    assert "never observed" in got["label"]
    assert got["n_stale"] == 2 and got["n_indicators"] == 2
