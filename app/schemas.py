"""Schemas Pydantic: o contrato público da API.

Separar schema de modelo ORM não é cerimônia — é o que impede um `key_hash` de
vazar num response por acidente quando alguém adiciona uma coluna nova.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: str
    vector_backend: Literal["pgvector", "numpy"]
    llm_provider: str
    embedding_provider: str


# ---------- API keys ----------


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120, examples=["app-mobile"])


class ApiKeyCreated(BaseModel):
    id: str
    name: str
    api_key: str = Field(description="Mostrada apenas uma vez. Guarde agora.")
    key_prefix: str
    created_at: datetime


class ApiKeyPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    key_prefix: str
    created_at: datetime
    revoked_at: datetime | None


# ---------- Documentos ----------


class DocumentIngestRequest(BaseModel):
    """Ingestão de texto puro, sem upload de arquivo."""

    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)


class IngestJobPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: Literal["pending", "running", "completed", "failed"]
    chunks_created: int
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class DocumentPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    content_type: str
    char_count: int
    created_at: datetime


class DocumentAccepted(BaseModel):
    document: DocumentPublic
    job: IngestJobPublic


class DocumentDetail(BaseModel):
    document: DocumentPublic
    job: IngestJobPublic | None
    chunk_count: int


class DocumentList(BaseModel):
    items: list[DocumentPublic]
    total: int
    limit: int
    offset: int


# ---------- Consulta ----------


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000, examples=["Qual é o prazo de garantia?"])
    top_k: int | None = Field(default=None, ge=1, le=20)
    document_ids: list[str] | None = Field(
        default=None, description="Restringe a busca a documentos específicos."
    )
    include_chunks: bool = Field(
        default=False, description="Devolve os trechos recuperados, para debug do retrieval."
    )


class CitationPublic(BaseModel):
    marker: int
    chunk_id: str
    document_id: str
    document_title: str
    char_start: int
    char_end: int
    score: float
    excerpt: str


class RetrievedChunkPublic(BaseModel):
    chunk_id: str
    document_id: str
    ordinal: int
    text: str
    score: float
    vector_rank: int | None
    keyword_rank: int | None


class UsagePublic(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


class QueryResponse(BaseModel):
    answer: str
    grounded: bool = Field(
        description="False quando a resposta não pôde ser ancorada em nenhuma fonte."
    )
    citations: list[CitationPublic]
    chunks: list[RetrievedChunkPublic] | None = None
    usage: UsagePublic
    latency_ms: int


class ErrorResponse(BaseModel):
    detail: str
