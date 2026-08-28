"""Contract tests for schemas/sector_study.json (v1.3 — the core/full refresh split).

Schema validation for studies was effectively DEAD before 1.3: 16 of 26 real files failed against
1.2 and nothing checked it, so the "cheap refresh" contract could rot the same way. These tests
run the real schema against the real files, and pin the core-vs-full split that the token budget
of a review now depends on.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

_ROOT = Path(__file__).parents[2]
_SCHEMA_PATH = _ROOT / "schemas" / "sector_study.json"
_STUDY_DIR = _ROOT / "data" / "sector_studies"


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _core_study(**over) -> dict:
    """A minimal `core` study: only the fields a decision or a scorer actually reads."""
    doc = {
        "$schema": "catalyx/schemas/sector_study.json",
        "id": "study_copper_miners",
        "schema_version": "1.3",
        "sector_id": "copper_miners",
        "sector_label": "Copper miners",
        "created_at": "2026-01-01",
        "last_updated": "2026-08-27",
        "study_type": "core",
        "taxonomy": {"parent_sector": "materials",
                     "differentiation_note": "Copper miners ≠ copper price: equity beta, grade "
                                             "decline and capex discipline drive them."},
        "risks": ["Chile permitting", "China demand", "Grade decline"],
        "active_catalyst_ids": ["struct_copper_datacenter_demand"],
        "narrative_maturity": "mainstream",
        "narrative_notes": "Broker coverage broad since Q2.",
        "key_metrics_to_monitor": [{"metric": "LME copper", "source": "LME", "current": 13871}],
    }
    doc.update(over)
    return doc


# ── The core/full split ──────────────────────────────────────────────────────

def test_core_study_validates_without_the_expensive_blocks(schema):
    # This IS the token saving: a refresh that needs neither demand_drivers nor etf_analysis.
    jsonschema.validate(_core_study(), schema)


def test_core_study_stays_small(schema):
    assert len(json.dumps(_core_study())) < 3000        # ~25 KB for a full dossier


def test_a_study_claiming_full_must_carry_the_deep_blocks(schema):
    # Otherwise "full" becomes a label anyone can put on a cheap study.
    with pytest.raises(jsonschema.ValidationError, match="demand_drivers"):
        jsonschema.validate(_core_study(study_type="full"), schema)


def test_the_two_fields_the_scorers_actually_read_are_expressible_in_core(schema):
    doc = _core_study()
    jsonschema.validate(doc, schema)
    assert doc["active_catalyst_ids"]      # catalyst_scorer.compute_catalyst_alignment
    assert doc["narrative_maturity"]       # snapshot_repo._crowding_for


# ── Back-compatibility (Schema Change Protocol: never break stored data) ─────

def test_both_schema_versions_read_back(schema):
    jsonschema.validate(_core_study(schema_version="1.2", study_type="summary"), schema)
    jsonschema.validate(_core_study(schema_version="1.3"), schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(_core_study(schema_version="9.9"), schema)


def test_every_real_study_on_disk_validates(schema):
    failures = []
    for fp in sorted(_STUDY_DIR.glob("study_*.json")):
        doc = json.loads(fp.read_text(encoding="utf-8"))
        errs = list(jsonschema.Draft7Validator(schema).iter_errors(doc))
        if errs:
            failures.append(f"{fp.name}: {errs[0].message[:80]}")
    assert not failures, "studies failing validation:\n  " + "\n  ".join(failures)


# ── The two pre-existing bugs that had killed validation ────────────────────

def test_the_repo_wide_schema_pointer_is_allowed(schema):
    # movement.json already declared `$schema`; sector_study did not, so every study carrying the
    # convention was invalid under additionalProperties:false.
    jsonschema.validate(_core_study(), schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(_core_study(some_unknown_field="x"), schema)   # still strict


def test_underscore_annotations_do_not_invalidate_a_study(schema):
    jsonschema.validate(_core_study(_universe_v2_note="reviewed in the v2 ETF sweep"), schema)


def test_deprecated_etf_block_accepts_an_honest_null(schema):
    # An unknown TER is null, never a fabricated number (CLAUDE.md: no false precision).
    doc = _core_study(study_type="full", demand_drivers=["Data-center copper intensity"],
                      etf_analysis=[{"ticker": "4COP.DE", "exchange": "XETRA", "currency": "EUR",
                                     "ter": None, "aum_m_usd": None, "replication": None,
                                     "recommendation_tier": 1}])
    jsonschema.validate(doc, schema)


def test_etf_analysis_is_marked_deprecated(schema):
    # etf_universe.yaml is the single source; the flag is what stops new studies re-duplicating it.
    assert schema["properties"]["etf_analysis"].get("deprecated") is True
