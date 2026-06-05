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
