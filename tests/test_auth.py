"""Testes de autenticação, autorização e rate limiting."""

from httpx import AsyncClient

from app.api.deps import limiter
from app.security import hash_api_key
from tests.conftest import ADMIN_HEADERS


async def test_sem_api_key_retorna_401(client: AsyncClient):
    response = await client.get("/v1/documents")
    assert response.status_code == 401
    assert "X-API-Key" in response.json()["detail"]


async def test_api_key_invalida_retorna_401(client: AsyncClient):
    response = await client.get("/v1/documents", headers={"X-API-Key": "ad_live_naoexiste"})
    assert response.status_code == 401


async def test_criar_key_exige_admin_token(client: AsyncClient):
    response = await client.post("/v1/keys", json={"name": "sem-permissao"})
    assert response.status_code == 403


async def test_admin_token_errado_retorna_403(client: AsyncClient):
    response = await client.post(
        "/v1/keys", json={"name": "x"}, headers={"X-Admin-Token": "chute"}
    )
    assert response.status_code == 403


async def test_chave_em_claro_nao_e_persistida(client: AsyncClient, session_factory):
    """A chave só existe em claro na resposta da criação."""
    from sqlalchemy import select

    from app.models import ApiKey

    created = (
        await client.post("/v1/keys", json={"name": "auditoria"}, headers=ADMIN_HEADERS)
    ).json()
    raw = created["api_key"]

    async with session_factory() as session:
        stored = (await session.execute(select(ApiKey))).scalars().all()

    assert len(stored) == 1
    assert stored[0].key_hash == hash_api_key(raw)
    assert raw not in stored[0].key_hash
    assert stored[0].key_prefix == raw[:12]


async def test_key_revogada_para_de_funcionar(client: AsyncClient):
    created = (
        await client.post("/v1/keys", json={"name": "temporaria"}, headers=ADMIN_HEADERS)
    ).json()
    headers = {"X-API-Key": created["api_key"]}

    assert (await client.get("/v1/documents", headers=headers)).status_code == 200

    revoked = await client.delete(f"/v1/keys/{created['id']}", headers=ADMIN_HEADERS)
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None

    assert (await client.get("/v1/documents", headers=headers)).status_code == 401


async def test_revogar_key_inexistente_retorna_404(client: AsyncClient):
    response = await client.delete("/v1/keys/naoexiste", headers=ADMIN_HEADERS)
    assert response.status_code == 404


async def test_listagem_de_keys_nunca_expoe_o_hash(client: AsyncClient, api_key):
    response = await client.get("/v1/keys", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    body = response.text
    assert "key_hash" not in body
    assert "key_prefix" in body


async def test_rate_limit_devolve_429_e_retry_after(client: AsyncClient, api_key):
    limiter.limit = 3
    limiter.reset()
    try:
        for _ in range(3):
            assert (await client.get("/v1/documents", headers=api_key)).status_code == 200

        blocked = await client.get("/v1/documents", headers=api_key)
        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) >= 1
        assert blocked.headers["X-RateLimit-Remaining"] == "0"
    finally:
        limiter.limit = 1000
        limiter.reset()


async def test_rate_limit_e_isolado_por_chave(client: AsyncClient, api_key, second_api_key):
    """A chave A estourar o limite não pode afetar a chave B."""
    limiter.limit = 2
    limiter.reset()
    try:
        for _ in range(2):
            await client.get("/v1/documents", headers=api_key)
        assert (await client.get("/v1/documents", headers=api_key)).status_code == 429
        assert (await client.get("/v1/documents", headers=second_api_key)).status_code == 200
    finally:
        limiter.limit = 1000
        limiter.reset()


async def test_headers_de_rate_limit_em_resposta_bem_sucedida(client: AsyncClient, api_key):
    response = await client.get("/v1/documents", headers=api_key)
    assert response.headers["X-RateLimit-Limit"] == "1000"
    assert int(response.headers["X-RateLimit-Remaining"]) < 1000


async def test_request_id_volta_no_header(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    assert "X-Response-Time-ms" in response.headers


async def test_request_id_do_cliente_e_respeitado(client: AsyncClient):
    response = await client.get("/health", headers={"X-Request-ID": "rastreio-123"})
    assert response.headers["X-Request-ID"] == "rastreio-123"
