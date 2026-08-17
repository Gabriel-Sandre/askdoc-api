"""Endpoint de pergunta e resposta sobre os documentos ingeridos."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import rate_limited_key
from app.core.rag import answer_question
from app.db import get_session
from app.models import ApiKey, QueryLog
from app.providers.base import ProviderError
from app.schemas import (
    CitationPublic,
    QueryRequest,
    QueryResponse,
    RetrievedChunkPublic,
    UsagePublic,
)

router = APIRouter(prefix="/v1", tags=["query"])
log = logging.getLogger(__name__)


@router.post("/query", response_model=QueryResponse)
async def query(
    payload: QueryRequest,
    api_key: ApiKey = Depends(rate_limited_key),
    session: AsyncSession = Depends(get_session),
) -> QueryResponse:
    try:
        result = await answer_question(
            session,
            api_key_id=api_key.id,
            question=payload.question,
            top_k=payload.top_k,
            document_ids=payload.document_ids,
        )
    except ProviderError as exc:
        # falha do provider externo é 502, não 500: o erro não é nosso
        log.error("provider indisponível", extra={"error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Provider de IA indisponível no momento. Tente novamente.",
        ) from exc

    session.add(
        QueryLog(
            api_key_id=api_key.id,
            question=payload.question,
            answer=result.answer,
            chunk_ids=[c.chunk_id for c in result.retrieved],
            latency_ms=result.latency_ms,
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            cost_usd=result.usage.cost_usd,
        )
    )
    await session.commit()

    return QueryResponse(
        answer=result.answer,
        grounded=result.grounded,
        citations=[
            CitationPublic(
                marker=c.marker,
                chunk_id=c.chunk_id,
                document_id=c.document_id,
                document_title=c.document_title,
                char_start=c.char_start,
                char_end=c.char_end,
                score=c.score,
                excerpt=c.excerpt,
            )
            for c in result.citations
        ],
        chunks=(
            [
                RetrievedChunkPublic(
                    chunk_id=c.chunk_id,
                    document_id=c.document_id,
                    ordinal=c.ordinal,
                    text=c.text,
                    score=c.score,
                    vector_rank=c.vector_rank,
                    keyword_rank=c.keyword_rank,
                )
                for c in result.retrieved
            ]
            if payload.include_chunks
            else None
        ),
        usage=UsagePublic(
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            cost_usd=result.usage.cost_usd,
        ),
        latency_ms=result.latency_ms,
    )


@router.get("/usage")
async def usage_summary(
    days: int = Query(default=7, ge=1, le=90),
    api_key: ApiKey = Depends(rate_limited_key),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Consumo agregado da chave.

    Cada resposta grava tokens e custo estimado; sem isso, a primeira fatura
    inesperada da OpenAI vira uma investigação forense.
    """
    from datetime import UTC, datetime, timedelta

    since = datetime.now(UTC) - timedelta(days=days)
    row = (
        await session.execute(
            select(
                func.count(QueryLog.id),
                func.coalesce(func.sum(QueryLog.prompt_tokens), 0),
                func.coalesce(func.sum(QueryLog.completion_tokens), 0),
                func.coalesce(func.sum(QueryLog.cost_usd), 0.0),
                func.coalesce(func.avg(QueryLog.latency_ms), 0.0),
            ).where(QueryLog.api_key_id == api_key.id, QueryLog.created_at >= since)
        )
    ).one()

    return {
        "period_days": days,
        "queries": row[0],
        "prompt_tokens": int(row[1]),
        "completion_tokens": int(row[2]),
        "estimated_cost_usd": round(float(row[3]), 6),
        "avg_latency_ms": round(float(row[4]), 1),
    }
