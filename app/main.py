import asyncio
import json

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from . import epub_ingest, ingest, ollama_client, pdf_ingest, rag, store
from .config import settings
from .metadata import MetadataError, validate_metadata
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


def _parse_metadata_json(raw: str | None) -> dict[str, str] | None:
    if not raw or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid metadata JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=422, detail="metadata must be a JSON object")
    return validate_metadata({str(k): str(v) for k, v in parsed.items()})


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
        metadata = validate_metadata(req.metadata)
        added = await ingest.ingest_text(req.text, req.source, metadata)
    except MetadataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ollama_client.OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return IngestResponse(source=req.source, chunks_added=added)


@app.post("/ingest/file", response_model=IngestResponse)
async def ingest_file(
    file: UploadFile = File(...),
    source: str = Form(""),
    metadata: str = Form(""),
) -> IngestResponse:
    raw = await file.read()
    name = source or file.filename or "uploaded"
    filename = (file.filename or "").lower()

    try:
        doc_metadata = _parse_metadata_json(metadata)
    except MetadataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if filename.endswith(".pdf"):
        try:
            result = await asyncio.to_thread(pdf_ingest.process_pdf, raw, name)
        except pdf_ingest.PdfIngestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        text_added = 0
        if result.full_text.strip():
            try:
                text_added = await ingest.ingest_text(result.full_text, name, doc_metadata)
            except ollama_client.OllamaError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc

        try:
            figures_added = await ingest.ingest_figures(result.figures, name, doc_metadata)
        except ollama_client.OllamaError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        if text_added == 0 and figures_added == 0:
            raise HTTPException(status_code=400, detail="No extractable text or figures in file")

        return IngestResponse(
            source=name,
            chunks_added=text_added,
            figures_added=figures_added,
        )

    if filename.endswith(".epub"):
        try:
            result = await asyncio.to_thread(epub_ingest.process_epub, raw, name)
        except epub_ingest.EpubIngestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        text_added = 0
        if result.full_text.strip():
            try:
                text_added = await ingest.ingest_text(result.full_text, name, doc_metadata)
            except ollama_client.OllamaError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc

        try:
            figures_added = await ingest.ingest_figures(result.figures, name, doc_metadata)
        except ollama_client.OllamaError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        if text_added == 0 and figures_added == 0:
            raise HTTPException(status_code=400, detail="No extractable text or figures in file")

        return IngestResponse(
            source=name,
            chunks_added=text_added,
            figures_added=figures_added,
        )

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="ignore")

    if not text.strip():
        raise HTTPException(status_code=400, detail="No extractable text in file")

    try:
        added = await ingest.ingest_text(text, name, doc_metadata)
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
