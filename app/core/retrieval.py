"""Busca híbrida: vetorial + lexical, fundidas por Reciprocal Rank Fusion.

Por que híbrida, e não só vetorial? Porque embeddings erram justamente onde o
usuário mais precisa de precisão: códigos de produto, nomes próprios, números
de artigo, siglas. "Art. 483" e "Art. 384" são quase idênticos no espaço
vetorial e completamente diferentes para quem perguntou. A busca lexical acerta
esses casos; a vetorial acerta paráfrase e sinônimo. Rodar as duas e fundir os
rankings entrega o melhor dos dois mundos.

A fusão usa RRF (Cormack et al., 2009): score = Σ 1/(k + rank). Ela combina
*posições*, não *scores*, então não é preciso normalizar escalas incomparáveis
(distância cosseno vs. ts_rank do Postgres) — que é onde a maioria das
implementações caseiras se perde.
"""

import logging
from dataclasses import dataclass

import numpy as np
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Chunk, Document
from app.providers.fake import tokenize

log = logging.getLogger(__name__)

RRF_K = 60  # constante do paper; amortece o peso das primeiras posições

# Teto de segurança do modo portátil (sem pgvector): o cálculo de similaridade
# acontece em memória, então limitamos quantos chunks são carregados por vez.
# Em Postgres com pgvector isso não se aplica — o índice HNSW resolve no banco.
MAX_IN_MEMORY_CHUNKS = 20_000


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    document_title: str
    ordinal: int
    text: str
    char_start: int
    char_end: int
    score: float
    vector_rank: int | None = None
    keyword_rank: int | None = None


def _base_filters(stmt, api_key_id: str, document_ids: list[str] | None):
    stmt = stmt.where(Chunk.api_key_id == api_key_id)
    if document_ids:
        stmt = stmt.where(Chunk.document_id.in_(document_ids))
    return stmt


async def vector_search(
    session: AsyncSession,
    *,
    api_key_id: str,
    query_vector: list[float],
    limit: int,
    document_ids: list[str] | None = None,
) -> list[str]:
    """IDs de chunk ordenados por similaridade de cosseno (mais similar primeiro)."""
    if settings.pgvector_enabled:
        return await _vector_search_pgvector(
            session,
            api_key_id=api_key_id,
            query_vector=query_vector,
            limit=limit,
            document_ids=document_ids,
        )
    return await _vector_search_numpy(
        session,
        api_key_id=api_key_id,
        query_vector=query_vector,
        limit=limit,
        document_ids=document_ids,
    )


async def _vector_search_pgvector(
    session: AsyncSession,
    *,
    api_key_id: str,
    query_vector: list[float],
    limit: int,
    document_ids: list[str] | None,
) -> list[str]:
    """Caminho de produção: o Postgres faz o ANN com índice HNSW."""
    distance = Chunk.embedding.cosine_distance(query_vector)
    stmt = select(Chunk.id).where(Chunk.embedding.isnot(None))
    stmt = _base_filters(stmt, api_key_id, document_ids)
    stmt = stmt.order_by(distance.asc()).limit(limit)
    rows = await session.execute(stmt)
    return [row[0] for row in rows.all()]


async def _vector_search_numpy(
    session: AsyncSession,
    *,
    api_key_id: str,
    query_vector: list[float],
    limit: int,
    document_ids: list[str] | None,
) -> list[str]:
    """Caminho portátil (SQLite / Postgres sem a extensão): cosseno em NumPy.

    Os vetores já são gravados normalizados, então o cosseno vira um único
    produto matriz-vetor — rápido o bastante para dezenas de milhares de chunks.
    """
    stmt = select(Chunk.id, Chunk.embedding).where(Chunk.embedding.isnot(None))
    stmt = _base_filters(stmt, api_key_id, document_ids).limit(MAX_IN_MEMORY_CHUNKS)
    rows = (await session.execute(stmt)).all()
    if not rows:
        return []

    ids = [row[0] for row in rows]
    matrix = np.asarray([row[1] for row in rows], dtype=np.float32)
    query = np.asarray(query_vector, dtype=np.float32)

    if matrix.ndim != 2 or matrix.shape[1] != query.shape[0]:
        log.warning(
            "dimensão de embedding inconsistente; ignorando busca vetorial",
            extra={"stored": matrix.shape, "query": query.shape},
        )
        return []

    scores = matrix @ query
    top = np.argsort(-scores)[:limit]
    return [ids[i] for i in top]


async def keyword_search(
    session: AsyncSession,
    *,
    api_key_id: str,
    query: str,
    limit: int,
    document_ids: list[str] | None = None,
) -> list[str]:
    """IDs de chunk ordenados por relevância lexical."""
    if settings.is_postgres:
        return await _keyword_search_postgres(
            session, api_key_id=api_key_id, query=query, limit=limit, document_ids=document_ids
        )
    return await _keyword_search_python(
        session, api_key_id=api_key_id, query=query, limit=limit, document_ids=document_ids
    )


async def _keyword_search_postgres(
    session: AsyncSession,
    *,
    api_key_id: str,
    query: str,
    limit: int,
    document_ids: list[str] | None,
) -> list[str]:
    """Full-text search nativo do Postgres, usando o índice GIN criado em init_db.

    Config 'simple' (sem stemming) para casar com o índice. Trocar para
    'portuguese' liga stemming e stopwords em PT — basta mudar nos dois lugares.
    """
    terms = tokenize(query)
    if not terms:
        return []

    tsquery = func.plainto_tsquery(text("'simple'"), " ".join(terms))
    tsvector = func.to_tsvector(text("'simple'"), Chunk.text)
    rank = func.ts_rank(tsvector, tsquery)

    stmt = select(Chunk.id).where(tsvector.op("@@")(tsquery))
    stmt = _base_filters(stmt, api_key_id, document_ids)
    stmt = stmt.order_by(rank.desc()).limit(limit)
    rows = await session.execute(stmt)
    return [row[0] for row in rows.all()]


async def _keyword_search_python(
    session: AsyncSession,
    *,
    api_key_id: str,
    query: str,
    limit: int,
    document_ids: list[str] | None,
) -> list[str]:
    """Fallback lexical para SQLite: TF-IDF simplificado em memória.

    SQLite tem FTS5, mas exige tabela virtual separada e sincronizada na mão.
    Como o SQLite aqui é só para dev e testes, o custo não se justifica.
    """
    terms = set(tokenize(query))
    if not terms:
        return []

    stmt = select(Chunk.id, Chunk.text)
    stmt = _base_filters(stmt, api_key_id, document_ids).limit(MAX_IN_MEMORY_CHUNKS)
    rows = (await session.execute(stmt)).all()
    if not rows:
        return []

    docs = [(row[0], tokenize(row[1])) for row in rows]
    total_docs = len(docs)

    doc_freq: dict[str, int] = {}
    for _, tokens in docs:
        for term in set(tokens) & terms:
            doc_freq[term] = doc_freq.get(term, 0) + 1

    scored: list[tuple[float, str]] = []
    for chunk_id, tokens in docs:
        if not tokens:
            continue
        score = 0.0
        for term in terms:
            tf = tokens.count(term)
            if tf == 0:
                continue
            idf = np.log(1 + total_docs / (1 + doc_freq.get(term, 0)))
            score += (tf / len(tokens)) * float(idf)
        if score > 0:
            scored.append((score, chunk_id))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [chunk_id for _, chunk_id in scored[:limit]]


def reciprocal_rank_fusion(
    rankings: dict[str, list[str]], *, k: int = RRF_K
) -> list[tuple[str, float, dict[str, int]]]:
    """Funde múltiplos rankings. Devolve (id, score, {estratégia: posição})."""
    scores: dict[str, float] = {}
    positions: dict[str, dict[str, int]] = {}

    for strategy, ids in rankings.items():
        for rank, item_id in enumerate(ids, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
            positions.setdefault(item_id, {})[strategy] = rank

    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return [(item_id, score, positions[item_id]) for item_id, score in ordered]


async def hybrid_search(
    session: AsyncSession,
    *,
    api_key_id: str,
    query: str,
    query_vector: list[float],
    top_k: int | None = None,
    document_ids: list[str] | None = None,
) -> list[RetrievedChunk]:
    """Roda as duas estratégias, funde com RRF e hidrata os chunks vencedores."""
    top_k = top_k or settings.retrieval_top_k
    pool = max(settings.candidate_pool, top_k)

    vector_ids = await vector_search(
        session,
        api_key_id=api_key_id,
        query_vector=query_vector,
        limit=pool,
        document_ids=document_ids,
    )
    keyword_ids = await keyword_search(
        session, api_key_id=api_key_id, query=query, limit=pool, document_ids=document_ids
    )

    fused = reciprocal_rank_fusion({"vector": vector_ids, "keyword": keyword_ids})[:top_k]
    if not fused:
        return []

    winner_ids = [item[0] for item in fused]
    stmt = (
        select(Chunk, Document.title)
        .join(Document, Document.id == Chunk.document_id)
        .where(Chunk.id.in_(winner_ids))
    )
    rows = (await session.execute(stmt)).all()
    by_id = {chunk.id: (chunk, title) for chunk, title in rows}

    results: list[RetrievedChunk] = []
    for chunk_id, score, positions in fused:
        entry = by_id.get(chunk_id)
        if entry is None:
            continue
        chunk, title = entry
        results.append(
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                document_title=title,
                ordinal=chunk.ordinal,
                text=chunk.text,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                score=round(score, 6),
                vector_rank=positions.get("vector"),
                keyword_rank=positions.get("keyword"),
            )
        )

    log.info(
        "retrieval concluído",
        extra={
            "vector_hits": len(vector_ids),
            "keyword_hits": len(keyword_ids),
            "returned": len(results),
        },
    )
    return results
