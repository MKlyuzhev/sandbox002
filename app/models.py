from pydantic import BaseModel, Field


class IngestTextRequest(BaseModel):
    text: str = Field(..., description="Raw text to ingest")
    source: str = Field("inline", description="Identifier for the source document")
    metadata: dict[str, str] | None = Field(
        None,
        description="Document-level metadata (title, author, asset_class, topics, evidence_level, acquisition)",
    )


class IngestResponse(BaseModel):
    source: str
    chunks_added: int
    figures_added: int = 0


class QueryRequest(BaseModel):
    question: str = Field(..., description="User question to answer from the corpus")
    top_k: int | None = Field(None, description="Override default number of chunks to retrieve")


class Source(BaseModel):
    source: str
    chunk_index: int
    distance: float | None = None
    preview: str
    chunk_type: str | None = None
    page: int | None = None
    image_path: str | None = None
    title: str | None = None
    author: str | None = None
    asset_class: str | None = None
    topics: str | None = None
    evidence_level: str | None = None


class QueryResponse(BaseModel):
    answer: str
    sources: list[Source]


class HealthResponse(BaseModel):
    status: str
    ollama: bool
    chroma: bool
    llm_model: str
    embed_model: str
    document_chunks: int
