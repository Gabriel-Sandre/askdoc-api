"""Testes do ciclo de vida do documento: ingestão, consulta, isolamento, remoção."""

import io

from httpx import AsyncClient

from tests.conftest import ingest


async def test_ingestao_de_texto_responde_202_e_completa(
    client: AsyncClient, api_key, handbook_text
):
    response = await client.post(
        "/v1/documents",
        json={"title": "manual.md", "content": handbook_text},
        headers=api_key,
    )
    assert response.status_code == 202
    body = response.json()
    assert body["job"]["status"] in {"pending", "running", "completed"}

    detail = await client.get(f"/v1/documents/{body['document']['id']}", headers=api_key)
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["job"]["status"] == "completed"
    assert payload["job"]["error"] is None
    assert payload["chunk_count"] > 1
    assert payload["chunk_count"] == payload["job"]["chunks_created"]


async def test_documento_duplicado_retorna_409(client: AsyncClient, api_key, handbook_text):
    payload = {"title": "manual.md", "content": handbook_text}
    assert (await client.post("/v1/documents", json=payload, headers=api_key)).status_code == 202

    duplicate = await client.post("/v1/documents", json=payload, headers=api_key)
    assert duplicate.status_code == 409
    assert "já ingerido" in duplicate.json()["detail"]


async def test_mesmo_conteudo_em_chaves_diferentes_e_permitido(
    client: AsyncClient, api_key, second_api_key, handbook_text
):
    """O dedup é por dono, não global."""
    payload = {"title": "manual.md", "content": handbook_text}
    assert (await client.post("/v1/documents", json=payload, headers=api_key)).status_code == 202
    assert (
        await client.post("/v1/documents", json=payload, headers=second_api_key)
    ).status_code == 202


async def test_upload_de_arquivo_txt(client: AsyncClient, api_key, handbook_text):
    files = {"file": ("manual.txt", io.BytesIO(handbook_text.encode()), "text/plain")}
    response = await client.post("/v1/documents/upload", files=files, headers=api_key)
    assert response.status_code == 202
    assert response.json()["document"]["title"] == "manual.txt"

    detail = await client.get(
        f"/v1/documents/{response.json()['document']['id']}", headers=api_key
    )
    assert detail.json()["job"]["status"] == "completed"


async def test_upload_de_tipo_nao_suportado_retorna_415(client: AsyncClient, api_key):
    files = {"file": ("foto.png", io.BytesIO(b"\x89PNG\r\n\x1a\n"), "image/png")}
    response = await client.post("/v1/documents/upload", files=files, headers=api_key)
    assert response.status_code == 415


async def test_conteudo_vazio_e_rejeitado(client: AsyncClient, api_key):
    response = await client.post(
        "/v1/documents", json={"title": "vazio", "content": "   "}, headers=api_key
    )
    assert response.status_code == 422


async def test_titulo_obrigatorio(client: AsyncClient, api_key):
    response = await client.post(
        "/v1/documents", json={"title": "", "content": "algum texto"}, headers=api_key
    )
    assert response.status_code == 422


async def test_listagem_paginada(client: AsyncClient, api_key):
    for i in range(5):
        await ingest(client, api_key, f"doc-{i}", f"Conteudo unico do documento numero {i}.")

    page = await client.get("/v1/documents?limit=2&offset=0", headers=api_key)
    assert page.status_code == 200
    body = page.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2

    last = await client.get("/v1/documents?limit=2&offset=4", headers=api_key)
    assert len(last.json()["items"]) == 1


async def test_documento_de_outra_chave_retorna_404(
    client: AsyncClient, api_key, second_api_key, handbook_text
):
    """Isolamento entre inquilinos — 404 e não 403, para não revelar existência."""
    document_id = await ingest(client, api_key, "privado.md", handbook_text)

    assert (
        await client.get(f"/v1/documents/{document_id}", headers=second_api_key)
    ).status_code == 404
    assert (
        await client.delete(f"/v1/documents/{document_id}", headers=second_api_key)
    ).status_code == 404
    assert (await client.get("/v1/documents", headers=second_api_key)).json()["total"] == 0


async def test_remocao_apaga_chunks_em_cascata(
    client: AsyncClient, api_key, handbook_text, session_factory
):
    from sqlalchemy import func, select

    from app.models import Chunk

    document_id = await ingest(client, api_key, "descartavel.md", handbook_text)

    async with session_factory() as session:
        before = (
            await session.execute(
                select(func.count(Chunk.id)).where(Chunk.document_id == document_id)
            )
        ).scalar_one()
    assert before > 0

    assert (
        await client.delete(f"/v1/documents/{document_id}", headers=api_key)
    ).status_code == 204

    async with session_factory() as session:
        after = (
            await session.execute(
                select(func.count(Chunk.id)).where(Chunk.document_id == document_id)
            )
        ).scalar_one()
    assert after == 0

    assert (await client.get(f"/v1/documents/{document_id}", headers=api_key)).status_code == 404


async def test_chunks_guardam_offsets_validos(
    client: AsyncClient, api_key, handbook_text, session_factory
):
    """Mesma invariante do chunking, agora atravessando o banco."""
    from sqlalchemy import select

    from app.core.chunking import normalize
    from app.models import Chunk

    document_id = await ingest(client, api_key, "manual.md", handbook_text)
    normalized = normalize(handbook_text)

    async with session_factory() as session:
        chunks = (
            (
                await session.execute(
                    select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.ordinal)
                )
            )
            .scalars()
            .all()
        )

    assert chunks
    for chunk in chunks:
        assert normalized[chunk.char_start : chunk.char_end] == chunk.text
        assert chunk.embedding is not None
        assert len(chunk.embedding) == 256
