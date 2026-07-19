from . import ollama_client, store
from .config import settings
from .models import QueryResponse, Source

SYSTEM_PROMPT = (
    "You are a helpful assistant that answers questions using only the provided context. "
    "If the context does not contain the answer, say you don't know based on the available "
    "documents. Be concise and cite facts from the context."
)


def _build_prompt(question: str, contexts: list[str]) -> str:
    joined = "\n\n".join(f"[{i + 1}] {c}" for i, c in enumerate(contexts))
    return (
        f"Context:\n{joined}\n\n"
        f"Question: {question}\n\n"
        "Answer using only the context above."
    )


async def answer(question: str, top_k: int | None = None) -> QueryResponse:
    k = top_k or settings.top_k
    query_vector = await ollama_client.embed(question)

    collection = store.get_collection()
    results = collection.query(
        query_embeddings=[query_vector],
        n_results=k,
        include=["documents", "metadatas", "distances"],
    )

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    if not documents:
        return QueryResponse(
            answer="No documents have been ingested yet, so I can't answer that.",
            sources=[],
        )

    prompt = _build_prompt(question, documents)
    llm_answer = await ollama_client.chat(SYSTEM_PROMPT, prompt)

    sources: list[Source] = []
    for doc, meta, dist in zip(documents, metadatas, distances):
        preview = doc[:200] + ("..." if len(doc) > 200 else "")
        sources.append(
            Source(
                source=str(meta.get("source", "unknown")),
                chunk_index=int(meta.get("chunk_index", -1)),
                distance=float(dist) if dist is not None else None,
                preview=preview,
                chunk_type=meta.get("chunk_type"),
                page=int(meta["page"]) if meta.get("page") is not None else None,
                image_path=meta.get("image_path"),
                title=meta.get("title"),
                author=meta.get("author"),
                asset_class=meta.get("asset_class"),
                topics=meta.get("topics"),
                evidence_level=meta.get("evidence_level"),
            )
        )

    return QueryResponse(answer=llm_answer.strip(), sources=sources)
