import io

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from . import ingest, ollama_client, rag, store
from .config import settings
from .models import (
    HealthResponse,
    IngestResponse,
    IngestTextRequest,
    QueryRequest,
    QueryResponse,
)

app = FastAPI(
    title="Local RAG Server",
    description="Retrieval-augmented Q&A backed by Ollama and ChromaDB.",
    version="1.0.0",
)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    ollama_ok = await ollama_client.is_reachable()
    chunks = store.count()
    return HealthResponse(
        status="ok" if ollama_ok else "degraded",
        ollama=ollama_ok,
        chroma=True,
        llm_model=settings.ollama_llm_model,
        embed_model=settings.ollama_embed_model,
        document_chunks=chunks,
    )


@app.post("/ingest", response_model=IngestResponse)
async def ingest_text(req: IngestTextRequest) -> IngestResponse:
    try:
        added = await ingest.ingest_text(req.text, req.source)
    except ollama_client.OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return IngestResponse(source=req.source, chunks_added=added)


@app.post("/ingest/file", response_model=IngestResponse)
async def ingest_file(file: UploadFile = File(...), source: str = Form("")) -> IngestResponse:
    raw = await file.read()
    name = source or file.filename or "uploaded"
    filename = (file.filename or "").lower()

    if filename.endswith(".pdf"):
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(raw))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not read PDF: {exc}") from exc
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="ignore")

    if not text.strip():
        raise HTTPException(status_code=400, detail="No extractable text in file")

    try:
        added = await ingest.ingest_text(text, name)
    except ollama_client.OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return IngestResponse(source=name, chunks_added=added)


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest) -> QueryResponse:
    try:
        return await rag.answer(req.question, req.top_k)
    except ollama_client.OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/")
async def root() -> dict:
    return {"service": "Local RAG Server", "docs": "/docs", "health": "/health"}
