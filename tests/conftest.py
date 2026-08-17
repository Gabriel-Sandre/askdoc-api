"""Fixtures compartilhadas.

As variáveis de ambiente são definidas ANTES de qualquer import de `app`,
porque `app.config.settings` é um singleton montado no momento do import.
Essa ordem é a diferença entre a suíte rodar contra o SQLite temporário e
rodar contra o banco de desenvolvimento da pessoa.
"""

import os
import tempfile
from collections.abc import AsyncIterator

_TMP_DIR = tempfile.mkdtemp(prefix="askdoc-tests-")
_DB_PATH = os.path.join(_TMP_DIR, "test.db")

# Por padrão a suíte roda em SQLite (rápido, zero setup). O CI reexecuta tudo
# apontando TEST_DATABASE_URL para um Postgres com pgvector, para que o caminho
# de produção também fique coberto — SQL de full-text e o operador de distância
# do pgvector não existem no SQLite e passariam despercebidos.
_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", f"sqlite+aiosqlite:///{_DB_PATH}")
_USE_PGVECTOR = os.environ.get("TEST_USE_PGVECTOR", "false")

os.environ.update(
    {
        "DATABASE_URL": _DATABASE_URL,
        "USE_PGVECTOR": _USE_PGVECTOR,
        "LLM_PROVIDER": "fake",
        "EMBEDDING_PROVIDER": "fake",
        "EMBEDDING_DIM": "256",
        "ADMIN_TOKEN": "test-admin-token",
        "RATE_LIMIT_PER_MINUTE": "1000",
        "CHUNK_SIZE": "400",
        "CHUNK_OVERLAP": "80",
        "LOG_LEVEL": "WARNING",
    }
)

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.api.deps import limiter  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402

ADMIN_HEADERS = {"X-Admin-Token": "test-admin-token"}


@pytest.fixture(autouse=True)
async def fresh_database() -> AsyncIterator[None]:
    """Schema zerado a cada teste: nenhum teste depende da ordem de execução."""
    async with engine.begin() as conn:
        if settings.pgvector_enabled:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    limiter.reset()
    yield


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Cliente HTTP sem rede: fala com o app ASGI em memória.

    Não usamos `lifespan` aqui porque o schema já é criado pela fixture acima.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest.fixture
async def api_key(client: AsyncClient) -> dict[str, str]:
    """Cria uma API key e devolve o header pronto para uso."""
    response = await client.post(
        "/v1/keys", json={"name": "suite-de-testes"}, headers=ADMIN_HEADERS
    )
    assert response.status_code == 201, response.text
    return {"X-API-Key": response.json()["api_key"]}


@pytest.fixture
async def second_api_key(client: AsyncClient) -> dict[str, str]:
    """Segunda chave, para provar o isolamento entre inquilinos."""
    response = await client.post(
        "/v1/keys", json={"name": "outro-cliente"}, headers=ADMIN_HEADERS
    )
    assert response.status_code == 201, response.text
    return {"X-API-Key": response.json()["api_key"]}


@pytest.fixture
def session_factory():
    return SessionLocal


HANDBOOK = """
# Manual do Colaborador - TechCorp

## Férias
Todo colaborador tem direito a 30 dias de ferias remuneradas por ano.
O pedido de ferias deve ser feito com 45 dias de antecedencia pelo portal interno.
Ferias podem ser divididas em ate tres periodos, sendo um deles de no minimo 14 dias.

## Trabalho remoto
O modelo adotado e hibrido: tres dias presenciais e dois dias remotos por semana.
Terca-feira e quinta-feira sao os dias obrigatorios de presenca no escritorio.
Trabalho remoto internacional exige aprovacao previa do time de compliance.

## Reembolso
Despesas de viagem sao reembolsadas em ate quinze dias uteis apos o envio da nota fiscal.
O limite diario de alimentacao em viagem e de duzentos reais.
Notas fiscais devem ser enviadas pelo sistema Expensr em ate 30 dias da despesa.

## Equipamento
Cada colaborador recebe um notebook e um monitor externo no primeiro dia.
A garantia do equipamento fornecido pela empresa e de 24 meses.
Defeitos devem ser reportados ao time de TI pelo canal #suporte-ti.
"""


@pytest.fixture
def handbook_text() -> str:
    return HANDBOOK


async def ingest(client: AsyncClient, headers: dict[str, str], title: str, content: str) -> str:
    """Helper: ingere um documento e devolve o document_id já processado."""
    response = await client.post(
        "/v1/documents", json={"title": title, "content": content}, headers=headers
    )
    assert response.status_code == 202, response.text
    return response.json()["document"]["id"]
