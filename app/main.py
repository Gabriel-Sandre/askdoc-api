"""Ponto de entrada da aplicação."""

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import routes_documents, routes_health, routes_keys, routes_query
from app.config import settings
from app.db import dispose_db, init_db
from app.logging_conf import request_id_ctx, setup_logging

log = logging.getLogger(__name__)

DESCRIPTION = """
API de perguntas e respostas sobre os seus próprios documentos (RAG).

**Fluxo:**
1. `POST /v1/keys` cria uma API key (precisa do header `X-Admin-Token`).
2. `POST /v1/documents` ou `/v1/documents/upload` envia o documento — responde 202.
3. `GET /v1/documents/{id}` acompanha o job de ingestão até `completed`.
4. `POST /v1/query` pergunta em linguagem natural e recebe resposta com citações.

Autenticação: header `X-API-Key` em todas as rotas de `/v1`, exceto `/v1/keys`.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging(settings.log_level)
    log.info(
        "subindo aplicação",
        extra={
            "postgres": settings.is_postgres,
            "pgvector": settings.pgvector_enabled,
            "llm_provider": settings.llm_provider,
        },
    )
    await init_db()
    yield
    await dispose_db()
    log.info("aplicação encerrada")


app = FastAPI(
    title="AskDoc API",
    version="0.1.0",
    description=DESCRIPTION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # em produção: lista explícita de domínios
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Atribui um request_id, mede a latência e loga toda requisição.

    O `X-Request-ID` volta no response para o cliente conseguir referenciar uma
    requisição específica ao reportar um problema.
    """
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    token = request_id_ctx.set(request_id)
    started = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        log.exception(
            "erro não tratado",
            extra={"path": request.url.path, "method": request.method},
        )
        raise
    finally:
        request_id_ctx.reset(token)

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time-ms"] = str(elapsed_ms)

    log.info(
        "requisição concluída",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": elapsed_ms,
        },
    )
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Nunca vaza stack trace para o cliente — só o request_id para correlacionar."""
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Erro interno.",
            "request_id": request_id_ctx.get(),
        },
    )


app.include_router(routes_health.router)
app.include_router(routes_keys.router)
app.include_router(routes_documents.router)
app.include_router(routes_query.router)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {"name": "AskDoc API", "version": "0.1.0", "docs": "/docs"}
