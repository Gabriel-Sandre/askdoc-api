"""Health checks.

Dois endpoints separados de propósito, porque o Kubernetes trata os dois de
formas diferentes: `/health` (liveness) só diz se o processo está vivo e não
pode depender do banco — senão uma queda momentânea do Postgres faz o
orquestrador matar e reiniciar pods saudáveis, piorando o incidente.
`/health/ready` (readiness) checa o banco e tira a réplica do balanceador
enquanto a dependência estiver fora.
"""

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.schemas import HealthResponse

router = APIRouter(tags=["health"])

VERSION = "0.1.0"


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=VERSION,
        database="postgres" if settings.is_postgres else "sqlite",
        vector_backend="pgvector" if settings.pgvector_enabled else "numpy",
        llm_provider=settings.llm_provider,
        embedding_provider=settings.embedding_provider,
    )


@router.get("/health/ready")
async def readiness(response: Response) -> dict[str, object]:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"ready": False, "database": "unreachable", "error": str(exc)[:200]}

    return {"ready": True, "database": "reachable"}
