"""Every Tier-1 document must validate against its own schema (v12 B13).

CLAUDE.md's schema-change protocol says "check every existing JSON in `data/` using it", and
nothing enforced it. It had already failed: `catalyst_event.json` declares
`additionalProperties: false`, and the pipeline had been writing five fields the schema never
declared — `affected_sectors` (which CLAUDE.md itself cites as the answer to "one catalyst = one
driver"), `review_log`, `status_last_reviewed`, `lifecycle_log`, `invalidation_reason`. Result:
11 of 19 event documents were invalid against their own schema while the CHANGELOG recorded
"los 19 documentos siguen validando".

Run: uv run pytest tests/unit/test_schema_documents_validate.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

_ROOT = Path(__file__).parents[2]
_PAIRS = [
    ("catalyst_event", "data/catalysts"),
    ("sector_study", "data/sector_studies"),
    ("movement", "data/movements"),
    ("taxonomy_gap_proposal", "data/taxonomy_proposals"),
]


def _cases() -> list[tuple[str, Path]]:
    out = []
    for schema_name, folder in _PAIRS:
        if not (_ROOT / "schemas" / f"{schema_name}.json").exists():
            continue
        for doc in sorted((_ROOT / folder).glob("*.json")):
            out.append((schema_name, doc))
    return out


@pytest.mark.parametrize("schema_name,doc", _cases(),
                         ids=lambda v: v.stem if isinstance(v, Path) else str(v))
def test_document_validates_against_its_schema(schema_name: str, doc: Path):
    schema = json.loads((_ROOT / "schemas" / f"{schema_name}.json").read_text(encoding="utf-8"))
    jsonschema.validate(json.loads(doc.read_text(encoding="utf-8")), schema)
