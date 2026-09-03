"""Unit tests for corpus retrieve + source filter (no network, no Chroma)."""

from __future__ import annotations

import asyncio
import inspect
import unittest
from unittest.mock import patch

from agent import retrieve
from app import rag_mcp


class _FakeCollection:
    def __init__(self, rows: list[tuple[str, dict, float]]) -> None:
        self.rows = rows
        self.query_kwargs: dict | None = None
        self.get_where: dict | None = None
        self.raise_on_where = False

    def query(self, **kwargs):
        self.query_kwargs = kwargs
        where = kwargs.get("where")
        if where and self.raise_on_where:
            raise RuntimeError("where not supported")
        src = (where or {}).get("source")
        selected = [
            row
            for row in self.rows
            if src is None or row[1].get("source") == src
        ]
        k = kwargs.get("n_results", 5)
        selected = selected[:k]
        return {
            "documents": [[r[0] for r in selected]],
            "metadatas": [[r[1] for r in selected]],
            "distances": [[r[2] for r in selected]],
        }

    def get(self, where=None, include=None):
        self.get_where = where
        clauses = (where or {}).get("$and") or []
        source = next((c.get("source") for c in clauses if "source" in c), None)
        idx = next(
            (c.get("chunk_index") for c in clauses if "chunk_index" in c), None
        )
        docs, metas = [], []
        for doc, meta, _dist in self.rows:
            if source is not None and meta.get("source") != source:
                continue
            if idx is not None and meta.get("chunk_index") != idx:
                continue
            docs.append(doc)
            metas.append(meta)
        return {"documents": docs, "metadatas": metas}


_ROWS = [
    ("lien fader text", {"source": "lien-fx", "chunk_index": 87, "chunk_type": "text", "title": "Lien"}, 0.1),
    ("lien fader figure", {"source": "lien-fx", "chunk_index": 87, "chunk_type": "figure", "title": "Lien"}, 0.2),
    ("murphy trend", {"source": "murphy-digital", "chunk_index": 1, "chunk_type": "text", "title": "Murphy"}, 0.05),
]


class TestSearchKnowledgeSource(unittest.TestCase):
    def test_filters_to_source(self) -> None:
        fake = _FakeCollection(_ROWS)

        async def _embed(_q: str) -> list[float]:
            return [0.0]

        with (
            patch.object(retrieve.store, "get_collection", return_value=fake),
            patch.object(retrieve.ollama_client, "embed", side_effect=_embed),
        ):
            hits = asyncio.run(
                retrieve.search_knowledge("fader", top_k=5, source="lien-fx")
            )
        self.assertEqual(fake.query_kwargs["where"], {"source": "lien-fx"})
        self.assertTrue(hits)
        self.assertTrue(all(h["source"] == "lien-fx" for h in hits))
        self.assertFalse(any(h["source"] == "murphy-digital" for h in hits))

    def test_post_filters_when_where_rejected(self) -> None:
        fake = _FakeCollection(_ROWS)
        fake.raise_on_where = True

        async def _embed(_q: str) -> list[float]:
            return [0.0]

        with (
            patch.object(retrieve.store, "get_collection", return_value=fake),
            patch.object(retrieve.ollama_client, "embed", side_effect=_embed),
        ):
            hits = asyncio.run(
                retrieve.search_knowledge("trend", top_k=5, source="lien-fx")
            )
        self.assertIsNone((fake.query_kwargs or {}).get("where"))
        self.assertTrue(hits)
        self.assertTrue(all(h["source"] == "lien-fx" for h in hits))

    def test_unfiltered_keeps_other_sources(self) -> None:
        fake = _FakeCollection(_ROWS)

        async def _embed(_q: str) -> list[float]:
            return [0.0]

        with (
            patch.object(retrieve.store, "get_collection", return_value=fake),
            patch.object(retrieve.ollama_client, "embed", side_effect=_embed),
        ):
            hits = asyncio.run(retrieve.search_knowledge("trend", top_k=5))
        sources = {h["source"] for h in hits}
        self.assertIn("murphy-digital", sources)
        self.assertIn("lien-fx", sources)


class TestGetSourceChunk(unittest.TestCase):
    def test_prefers_text_over_figure(self) -> None:
        fake = _FakeCollection(_ROWS)
        with patch.object(retrieve.store, "get_collection", return_value=fake):
            chunk = retrieve.get_source_chunk("lien-fx", 87)
        self.assertNotIn("error", chunk)
        self.assertEqual(chunk["chunk_type"], "text")
        self.assertEqual(chunk["text"], "lien fader text")

    def test_missing(self) -> None:
        fake = _FakeCollection(_ROWS)
        with patch.object(retrieve.store, "get_collection", return_value=fake):
            chunk = retrieve.get_source_chunk("lien-fx", 999)
        self.assertIn("error", chunk)


class TestMcpSignature(unittest.TestCase):
    def test_search_knowledge_accepts_source(self) -> None:
        params = inspect.signature(rag_mcp.search_knowledge).parameters
        self.assertIn("source", params)


if __name__ == "__main__":
    unittest.main()
