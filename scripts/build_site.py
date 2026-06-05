"""Build the static GitHub-Pages dashboard (Fase F).

Bakes the parquet lake into a self-contained `dist/`: copies the static frontend
(site/*) + every lake parquet, and writes `manifest.json` mapping each table to its
partition files. The page (DuckDB-WASM) reads `manifest.json`, registers the parquet,
and queries them in the browser — no backend, no DVC pull (the lake is committed to git).

Run locally to preview:
    uv run python scripts/build_site.py
    python -m http.server -d dist 8000   # → http://localhost:8000

The GitHub Actions workflow (.github/workflows/pages.yml) runs this and deploys `dist/`.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import yaml

from catalyx.store import lake

_ROOT = Path(__file__).parents[1]
_SITE = _ROOT / "site"
_DIST = _ROOT / "dist"
_LAKE = _ROOT / "data" / "lake"
_STATIC = ("index.html", "app.js")

# Tier-1 documents (config/JSON) surfaced read-only in the dashboard alongside the lake.
_STRUCTURAL_CAT = _ROOT / "catalyx" / "config" / "structural_catalysts"
_EVENT_CAT = _ROOT / "data" / "catalysts"
_STUDIES = _ROOT / "data" / "sector_studies"
_THESES = _ROOT / "data" / "theses"


def _bake_docs(dist: Path) -> dict:
    """Bundle the Tier-1 documents into docs.json so the page can show the full picture:
    structural + event catalysts, sector studies, theses. Small (KB each) — one fetch."""
    docs: dict[str, list] = {"catalysts_structural": [], "catalysts_event": [],
                             "studies": [], "theses": []}
    for f in sorted(_STRUCTURAL_CAT.glob("*.yaml")):
        try:
            docs["catalysts_structural"].append(yaml.safe_load(f.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
    for src, key in ((_EVENT_CAT, "catalysts_event"), (_STUDIES, "studies"), (_THESES, "theses")):
        if src.exists():
            for f in sorted(src.glob("*.json")):
                try:
                    docs[key].append(json.loads(f.read_text(encoding="utf-8")))
                except Exception:  # noqa: BLE001
                    pass
    (dist / "docs.json").write_text(json.dumps(docs, ensure_ascii=False), encoding="utf-8")
    return {k: len(v) for k, v in docs.items()}


def build(dist: Path = _DIST) -> dict:
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(parents=True)

    for name in _STATIC:
        src = _SITE / name
        if src.exists():
            shutil.copy(src, dist / name)

    manifest: dict[str, list[str]] = {}
    total = 0
    for table in lake.TABLES:
        files = sorted(lake.table_dir(table).glob("*.parquet"))
        if not files:
            continue
        urls = []
        for fp in files:
            rel = fp.relative_to(_LAKE)
            dest = dist / "data" / "lake" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(fp, dest)
            urls.append(f"data/lake/{rel.as_posix()}")
            total += 1
        manifest[table] = urls

    (dist / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    docs = _bake_docs(dist)
    return {"tables": len(manifest), "parquet_files": total, "docs": docs, "dist": str(dist)}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    r = build()
    print(f"Built {r['dist']}: {r['parquet_files']} parquet across {r['tables']} tables")
    print(f"  docs.json: {r['docs']}")
    print("Preview: python -m http.server -d dist 8000  ->  http://localhost:8000")


if __name__ == "__main__":
    main()
