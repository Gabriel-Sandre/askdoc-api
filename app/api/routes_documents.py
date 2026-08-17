"""Ingestão e gestão de documentos."""

import logging

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import rate_limited_key
from app.config import settings
from app.core.ingest import UnsupportedFileType, checksum, extract_text, run_ingestion
from app.db import get_session
from app.models import ApiKey, Chunk, Document, IngestJob
from app.schemas import (
    DocumentAccepted,
    DocumentDetail,
    DocumentIngestRequest,
    DocumentList,
    DocumentPublic,
    IngestJobPublic,
)

router = APIRouter(prefix="/v1/documents", tags=["documents"])
log = logging.getLogger(__name__)


async def _create_document(
    session: AsyncSession,
    *,
    api_key: ApiKey,
    title: str,
    content_type: str,
    body: str,
    raw: bytes,
) -> tuple[Document, IngestJob]:
    digest = checksum(raw)

    existing = (
        await session.execute(
            select(Document).where(
                Document.api_key_id == api_key.id, Document.checksum == digest
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        # idempotência: reenviar o mesmo arquivo não duplica nem re-embeda
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Documento idêntico já ingerido (id={existing.id}).",
        )

    if not body.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Não foi possível extrair texto deste arquivo.",
        )

    document = Document(
        api_key_id=api_key.id,
        title=title,
        content_type=content_type,
        char_count=len(body),
        checksum=digest,
    )
    session.add(document)
    await session.flush()

    job = IngestJob(document_id=document.id, status="pending")
    session.add(job)
    await session.commit()
    await session.refresh(document)
    await session.refresh(job)

    return document, job


@router.post("", response_model=DocumentAccepted, status_code=status.HTTP_202_ACCEPTED)
async def ingest_text(
    payload: DocumentIngestRequest,
    background: BackgroundTasks,
    api_key: ApiKey = Depends(rate_limited_key),
    session: AsyncSession = Depends(get_session),
) -> DocumentAccepted:
    """Ingere texto puro. Responde 202: o processamento roda em background."""
    raw = payload.content.encode("utf-8")
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Conteúdo excede {settings.max_upload_bytes} bytes.",
        )

    document, job = await _create_document(
        session,
        api_key=api_key,
        title=payload.title,
        content_type="text/plain",
        body=payload.content,
        raw=raw,
    )
    background.add_task(run_ingestion, document.id, payload.content)

    return DocumentAccepted(
        document=DocumentPublic.model_validate(document),
        job=IngestJobPublic.model_validate(job),
    )


@router.post("/upload", response_model=DocumentAccepted, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    api_key: ApiKey = Depends(rate_limited_key),
    session: AsyncSession = Depends(get_session),
) -> DocumentAccepted:
    """Upload de .txt, .md, .csv, .json ou .pdf."""
    raw = await file.read()
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Arquivo excede {settings.max_upload_bytes} bytes.",
        )

    filename = file.filename or "documento"
    try:
        body = extract_text(filename, file.content_type or "", raw)
    except UnsupportedFileType as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
        ) from exc
    except Exception as exc:  # noqa: BLE001 - PDF corrompido, encoding exótico...
        log.warning("falha ao extrair texto", extra={"filename": filename, "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Não foi possível ler o arquivo: {exc}",
        ) from exc

    document, job = await _create_document(
        session,
        api_key=api_key,
        title=filename,
        content_type=file.content_type or "application/octet-stream",
        body=body,
        raw=raw,
    )
    background.add_task(run_ingestion, document.id, body)

    return DocumentAccepted(
        document=DocumentPublic.model_validate(document),
        job=IngestJobPublic.model_validate(job),
    )


@router.get("", response_model=DocumentList)
async def list_documents(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    api_key: ApiKey = Depends(rate_limited_key),
    session: AsyncSession = Depends(get_session),
) -> DocumentList:
    total = (
        await session.execute(
            select(func.count(Document.id)).where(Document.api_key_id == api_key.id)
        )
    ).scalar_one()

    rows = (
        await session.execute(
            select(Document)
            .where(Document.api_key_id == api_key.id)
            .order_by(Document.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars()

    return DocumentList(
        items=[DocumentPublic.model_validate(doc) for doc in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


async def _owned_document(
    session: AsyncSession, document_id: str, api_key: ApiKey
) -> Document:
    document = await session.get(Document, document_id)
    # 404 (e não 403) quando o documento é de outra chave: não revelamos que ele existe
    if document is None or document.api_key_id != api_key.id:
        raise HTTPException(status_code=404, detail="Documento não encontrado.")
    return document


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: str,
    api_key: ApiKey = Depends(rate_limited_key),
    session: AsyncSession = Depends(get_session),
) -> DocumentDetail:
    """Detalhe do documento, incluindo o status do job de ingestão."""
    document = await _owned_document(session, document_id, api_key)

    job = (
        await session.execute(select(IngestJob).where(IngestJob.document_id == document.id))
    ).scalar_one_or_none()
    chunk_count = (
        await session.execute(
            select(func.count(Chunk.id)).where(Chunk.document_id == document.id)
        )
    ).scalar_one()

    return DocumentDetail(
        document=DocumentPublic.model_validate(document),
        job=IngestJobPublic.model_validate(job) if job else None,
        chunk_count=chunk_count,
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str,
    api_key: ApiKey = Depends(rate_limited_key),
    session: AsyncSession = Depends(get_session),
) -> None:
    document = await _owned_document(session, document_id, api_key)
    await session.delete(document)  # cascade remove chunks e job
    await session.commit()
