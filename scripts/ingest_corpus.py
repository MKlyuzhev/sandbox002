#!/usr/bin/env python3
"""Batch-ingest corpus documents from data/corpus/manifest.yaml."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "data" / "corpus" / "manifest.yaml"
DOCUMENTS_DIR = ROOT / "data" / "documents"
STATE_PATH = ROOT / "data" / "corpus" / ".ingest_state.json"
DEFAULT_BASE_URL = "http://localhost:8000"


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
        if isinstance(topics, list):
            meta["topics"] = ",".join(str(t) for t in topics)
        else:
            meta["topics"] = str(topics)
    return meta


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"ingested": {}}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def check_health(client: httpx.Client, base_url: str) -> None:
    r = client.get(f"{base_url}/health", timeout=30.0)
    r.raise_for_status()
    data = r.json()
    if not data.get("ollama"):
        print("Warning: Ollama is not reachable; ingest will likely fail.", file=sys.stderr)


def ingest_file(
    client: httpx.Client,
    base_url: str,
    file_path: Path,
    source_id: str,
    metadata: dict[str, str],
) -> dict:
    with file_path.open("rb") as f:
        files = {"file": (file_path.name, f)}
        data = {"source": source_id, "metadata": json.dumps(metadata)}
        r = client.post(f"{base_url}/ingest/file", files=files, data=data, timeout=14400.0)
    r.raise_for_status()
    return r.json()


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest corpus documents into the RAG server")
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--documents-dir", type=Path, default=DOCUMENTS_DIR)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--only", help="Comma-separated source_id list")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    defaults = manifest.get("defaults", {})
    documents = manifest.get("documents", [])
    only = {s.strip() for s in args.only.split(",")} if args.only else None
    state = load_state()

    pending: list[tuple[dict, Path]] = []
    missing: list[str] = []

    for entry in documents:
        if not entry.get("enabled", True):
            continue
        source_id = entry["source_id"]
        if only and source_id not in only:
            continue
        if args.skip_existing and source_id in state.get("ingested", {}):
            print(f"skip-existing: {source_id}")
            continue

        file_name = entry["file"]
        file_path = args.documents_dir / file_name
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

    print(f"Planned ingest: {len(pending)} document(s)")
    for entry, file_path in pending:
        meta = doc_metadata(entry, defaults)
        print(f"  - {entry['source_id']}: {file_path.name} ({file_path.stat().st_size // 1024} KB)")

    if args.dry_run:
        return 1 if missing else 0

    with httpx.Client() as client:
        check_health(client, args.base_url)
        for entry, file_path in pending:
            source_id = entry["source_id"]
            meta = doc_metadata(entry, defaults)
            print(f"Ingesting {source_id} ...", flush=True)
            try:
                result = ingest_file(client, args.base_url, file_path, source_id, meta)
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text
                print(f"Failed {source_id}: {exc} — {detail}", file=sys.stderr)
                return 1
            except httpx.HTTPError as exc:
                print(f"Failed {source_id}: {exc}", file=sys.stderr)
                return 1

            chunks = result.get("chunks_added", 0)
            figures = result.get("figures_added", 0)
            print(f"  done: {chunks} text chunks, {figures} figures")
            state.setdefault("ingested", {})[source_id] = {
                "chunks_added": chunks,
                "figures_added": figures,
                "file": entry["file"],
            }
            save_state(state)

    if missing:
        print(f"Completed with {len(missing)} missing file(s).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
