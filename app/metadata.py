"""Document-level metadata validation and chunk context helpers."""

from __future__ import annotations

ALLOWED_METADATA_KEYS = frozenset(
    {
        "title",
        "author",
        "asset_class",
        "topics",
        "evidence_level",
        "acquisition",
    }
)

VALID_ASSET_CLASSES = frozenset({"fx", "equity", "futures", "general"})
VALID_EVIDENCE_LEVELS = frozenset({"principle", "heuristic", "empirical"})
VALID_ACQUISITIONS = frozenset({"owned", "purchase", "public_domain", "free_official"})


class MetadataError(ValueError):
    """Raised when ingest metadata is invalid."""


def normalize_topics(topics: str | list[str]) -> str:
    if isinstance(topics, list):
        return ",".join(str(t).strip() for t in topics if str(t).strip())
    return str(topics).strip()


def validate_metadata(metadata: dict[str, str] | None) -> dict[str, str]:
    if not metadata:
        return {}

    unknown = set(metadata) - ALLOWED_METADATA_KEYS
    if unknown:
        raise MetadataError(f"Unknown metadata keys: {sorted(unknown)}")

    normalized: dict[str, str] = {}
    for key, value in metadata.items():
        text = str(value).strip()
        if not text:
            continue
        normalized[key] = text

    if "asset_class" in normalized and normalized["asset_class"] not in VALID_ASSET_CLASSES:
        raise MetadataError(f"Invalid asset_class: {normalized['asset_class']}")

    if "evidence_level" in normalized and normalized["evidence_level"] not in VALID_EVIDENCE_LEVELS:
        raise MetadataError(f"Invalid evidence_level: {normalized['evidence_level']}")

    if "acquisition" in normalized and normalized["acquisition"] not in VALID_ACQUISITIONS:
        raise MetadataError(f"Invalid acquisition: {normalized['acquisition']}")

    if "topics" in normalized:
        normalized["topics"] = normalize_topics(normalized["topics"])

    return normalized


def build_chunk_metadata(
    source: str,
    chunk_type: str,
    chunk_index: int,
    doc_metadata: dict[str, str] | None = None,
    **extra: str | int,
) -> dict[str, str | int]:
    meta: dict[str, str | int] = {
        "chunk_type": chunk_type,
        "source": source,
        "chunk_index": chunk_index,
    }
    if doc_metadata:
        for key, value in doc_metadata.items():
            meta[key] = value
    meta.update(extra)
    return meta


def build_context_prefix(source: str, doc_metadata: dict[str, str] | None) -> str:
    if not doc_metadata:
        return f"[{source}]"

    parts = [source]
    if doc_metadata.get("author"):
        parts.append(doc_metadata["author"])
    if doc_metadata.get("asset_class"):
        parts.append(doc_metadata["asset_class"])
    if doc_metadata.get("topics"):
        parts.append(f"topics: {doc_metadata['topics']}")
    return "[" + " | ".join(parts) + "]"


def prefix_chunk_text(text: str, source: str, doc_metadata: dict[str, str] | None) -> str:
    prefix = build_context_prefix(source, doc_metadata)
    return f"{prefix}\n{text}"
