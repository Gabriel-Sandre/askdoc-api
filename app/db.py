"""Engine, sessão e bootstrap do schema.

Suporta Postgres (produção) e SQLite (dev/testes) com o mesmo código.
A única diferença real está no tipo da coluna de embedding e nos índices
específicos de cada banco — ver `app/models.py` e `init_db`.
"""

import logging
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base

log = logging.getLogger(__name__)

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Dependency do FastAPI: uma sessão por requisição, sempre fechada."""
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Cria extensão, tabelas e índices. Idempotente."""
    async with engine.begin() as conn:
        if settings.pgvector_enabled:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

        await conn.run_sync(Base.metadata.create_all)

        if settings.is_postgres:
            # Índice de full-text search para o braço lexical da busca híbrida.
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_chunks_fts "
                    "ON chunks USING GIN (to_tsvector('simple', text))"
                )
            )
            if settings.pgvector_enabled:
                # HNSW: busca aproximada por vizinho mais próximo, distância cosseno.
                await conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw "
                        "ON chunks USING hnsw (embedding vector_cosine_ops)"
                    )
                )
    log.info(
        "schema pronto",
        extra={"postgres": settings.is_postgres, "pgvector": settings.pgvector_enabled},
    )


async def dispose_db() -> None:
    await engine.dispose()
