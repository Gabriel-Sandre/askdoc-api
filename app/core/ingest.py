"""Pipeline de ingestão, executado em background.

Ingerir um documento envolve chamada de rede (embeddings) e pode levar
segundos ou minutos. Fazer isso dentro do request handler seguraria a conexão
HTTP e daria timeout no cliente. Então o upload responde 202 na hora com um
`job_id`, e o processamento acontece depois — o cliente acompanha por polling
em GET /v1/documents/{id}.

Em produção real isso viraria uma fila de verdade (Celery, arq, SQS): a
BackgroundTask do FastAPI vive no processo da API e não sobrevive a um deploy
no meio do caminho. A fronteira aqui já está desenhada para essa troca — o
`run_ingestion` recebe só o `document_id` e abre a própria sessão, exatamente
como um worker faria.
"""

import hashlib
import io
import logging
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.chunking import chunk_text, normalize
from app.db import SessionLocal
from app.models import Chunk, Document, IngestJob
from app.providers import get_embedding_provider

log = logging.getLogger(__name__)

EMBED_BATCH_SIZE = 64
SUPPORTED_TEXT_TYPES = {"text/plain", "text/markdown", "text/csv", "application/json"}


class UnsupportedFileType(ValueError):
    pass


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_text(filename: str, content_type: str, data: bytes) -> str:
    """Converte o arquivo enviado em texto puro."""
    lowered = filename.lower()

    if lowered.endswith(".pdf") or content_type == "application/pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
        return normalize("\n\n".join(pages))

    if (
        content_type in SUPPORTED_TEXT_TYPES
        or content_type.startswith("text/")
        or lowered.endswith((".txt", ".md", ".markdown", ".csv", ".json"))
    ):
        return normalize(data.decode("utf-8", errors="replace"))

    raise UnsupportedFileType(
        f"Tipo não suportado: {content_type or filename}. Aceitos: .txt, .md, .csv, .json, .pdf"
    )


async def _embed_and_store(session: AsyncSession, document: Document, body: str) -> int:
    pieces = chunk_text(
        body, chunk_size=settings.chunk_size, overlap=settings.chunk_overlap
    )
    if not pieces:
        return 0

    # reingestão do mesmo documento: limpa os chunks antigos antes
    await session.execute(delete(Chunk).where(Chunk.document_id == document.id))

    embedder = get_embedding_provider()
    for start in range(0, len(pieces), EMBED_BATCH_SIZE):
        batch = pieces[start : start + EMBED_BATCH_SIZE]
        result = await embedder.embed([piece.text for piece in batch])
        for piece, vector in zip(batch, result.vectors, strict=True):
            session.add(
                Chunk(
                    document_id=document.id,
                    api_key_id=document.api_key_id,
                    ordinal=piece.ordinal,
                    text=piece.text,
                    char_start=piece.char_start,
                    char_end=piece.char_end,
                    embedding=vector,
                )
            )
        await session.flush()

    return len(pieces)


async def run_ingestion(document_id: str, body: str) -> None:
    """Processa um documento do começo ao fim. Nunca levanta exceção:
    qualquer falha é gravada no job para o cliente conseguir ver o motivo."""
    async with SessionLocal() as session:
        job = (
            await session.execute(
                select(IngestJob).where(IngestJob.document_id == document_id)
            )
        ).scalar_one_or_none()
        document = await session.get(Document, document_id)

        if job is None or document is None:
            log.error("job ou documento inexistente", extra={"document_id": document_id})
            return

        job.status = "running"
        await session.commit()

        try:
            created = await _embed_and_store(session, document, body)
            job.status = "completed"
            job.chunks_created = created
            job.error = None
            job.finished_at = datetime.now(UTC)
            await session.commit()
            log.info(
                "ingestão concluída",
                extra={"document_id": document_id, "chunks": created},
            )
        except Exception as exc:  # noqa: BLE001 - a falha precisa virar estado, não crash
            await session.rollback()
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"[:1000]
            job.finished_at = datetime.now(UTC)
            await session.commit()
            log.exception("ingestão falhou", extra={"document_id": document_id})
