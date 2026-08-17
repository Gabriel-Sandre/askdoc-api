"""Testes dos health checks e da documentação OpenAPI."""

from httpx import AsyncClient


async def test_health_nao_exige_autenticacao(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["vector_backend"] in {"pgvector", "numpy"}
    assert body["llm_provider"] == "fake"


async def test_readiness_checa_o_banco(client: AsyncClient):
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"ready": True, "database": "reachable"}


async def test_openapi_e_gerado(client: AsyncClient):
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    for route in ("/v1/query", "/v1/documents", "/v1/documents/upload", "/v1/keys"):
        assert route in paths


async def test_root_aponta_para_os_docs(client: AsyncClient):
    assert (await client.get("/")).json()["docs"] == "/docs"
