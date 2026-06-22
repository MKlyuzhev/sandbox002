import chromadb

from .config import settings

_client: chromadb.ClientAPI | None = None


def get_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
    return _client


def get_collection():
    """Return the persistent documents collection.

    Embeddings are supplied explicitly by the caller, so no embedding
    function is configured on the collection itself.
    """
    return get_client().get_or_create_collection(
        name=settings.chroma_collection,
        metadata={"hnsw:space": "cosine"},
    )


def count() -> int:
    try:
        return get_collection().count()
    except Exception:
        return 0
