#!/usr/bin/env python3
"""Ingest corpus documents by calling the ingest pipeline directly (no HTTP)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml

from app import epub_ingest, ingest, pdf_ingest
from app.metadata import validate_metadata

MANIFEST_PATH = ROOT / "data" / "corpus" / "manifest.yaml"
DOCUMENTS_DIR = ROOT / "data" / "documents"
STATE_PATH = ROOT / "data" / "corpus" / ".ingest_state.json"


def load_manifest(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def doc_metadata(entry: dict, defaults: dict) -> dict[str, str]:
    meta: dict[str, str] = {}
    for key in ("title", "author", "asset_class", "evidence_level", "acquisition"):
        value = entry.get(key) or defaults.get(key)
        if value:
            meta[key] = str(value)
    topics = entry.get("topics")
    if topics:
        meta["topics"] = ",".join(str(t) for t in topics) if isinstance(topics, list) else str(topics)
    return validate_metadata(meta)


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"ingested": {}}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


async def ingest_path(file_path: Path, source_id: str, metadata: dict[str, str]) -> tuple[int, int]:
    raw = file_path.read_bytes()
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        result = await asyncio.to_thread(pdf_ingest.process_pdf, raw, source_id)
        text_added = 0
        if result.full_text.strip():
            text_added = await ingest.ingest_text(result.full_text, source_id, metadata)
        figures_added = await ingest.ingest_figures(result.figures, source_id, metadata)
        return text_added, figures_added

    if suffix == ".epub":
        result = await asyncio.to_thread(epub_ingest.process_epub, raw, source_id)
        text_added = 0
        if result.full_text.strip():
            text_added = await ingest.ingest_text(result.full_text, source_id, metadata)
        figures_added = await ingest.ingest_figures(result.figures, source_id, metadata)
        return text_added, figures_added

    text = raw.decode("utf-8", errors="ignore")
    text_added = await ingest.ingest_text(text, source_id, metadata)
    return text_added, 0


async def run(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    defaults = manifest.get("defaults", {})
    only = {s.strip() for s in args.only.split(",")} if args.only else None
    state = load_state()

    pending: list[tuple[dict, Path]] = []
    missing: list[str] = []

    for entry in manifest.get("documents", []):
        if not entry.get("enabled", True):
            continue
        source_id = entry["source_id"]
        if only and source_id not in only:
            continue
        if args.skip_existing and source_id in state.get("ingested", {}):
            print(f"skip-existing: {source_id}")
            continue
        file_path = args.documents_dir / entry["file"]
        if not file_path.exists():
            missing.append(f"{source_id} -> {file_path}")
            continue
        pending.append((entry, file_path))

    if missing:
        print("Missing files:", file=sys.stderr)
        for line in missing:
            print(f"  - {line}", file=sys.stderr)

    if not pending:
        print("No documents to ingest.")
        return 1 if missing else 0

    for entry, file_path in pending:
        source_id = entry["source_id"]
        meta = doc_metadata(entry, defaults)
        print(f"Ingesting {source_id} ...", flush=True)
        try:
            chunks, figures = await ingest_path(file_path, source_id, meta)
        except Exception as exc:
            print(f"Failed {source_id}: {exc}", file=sys.stderr)
            return 1
        print(f"  done: {chunks} text chunks, {figures} figures")
        state.setdefault("ingested", {})[source_id] = {
            "chunks_added": chunks,
            "figures_added": figures,
            "file": entry["file"],
        }
        save_state(state)

    return 1 if missing else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Direct local corpus ingest")
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--documents-dir", type=Path, default=DOCUMENTS_DIR)
    parser.add_argument("--only", help="Comma-separated source_id list")
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
