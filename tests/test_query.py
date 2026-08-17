"""Testes do endpoint de consulta e da lógica de citação."""

from httpx import AsyncClient

from app.core.rag import build_context, extract_citations
from app.core.retrieval import RetrievedChunk
from tests.conftest import ingest


def _chunk(chunk_id: str, text: str = "conteudo de exemplo do documento") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="doc-1",
        document_title="manual.md",
        ordinal=0,
        text=text,
        char_start=0,
        char_end=len(text),
        score=0.5,
    )


# ---------- unidades puras ----------


def test_build_context_numera_os_trechos_a_partir_de_1():
    context, used = build_context([_chunk("a"), _chunk("b")])
    assert "[1]" in context and "[2]" in context
    assert len(used) == 2


def test_build_context_respeita_o_orcamento_de_tokens():
    chunks = [_chunk(str(i), "palavra " * 500) for i in range(10)]
    _, used = build_context(chunks, max_tokens=300)
    assert 0 < len(used) < 10


def test_build_context_sempre_inclui_ao_menos_um_chunk():
    """Mesmo um chunk sozinho maior que o orçamento entra — senão não há resposta."""
    _, used = build_context([_chunk("gigante", "palavra " * 5000)], max_tokens=10)
    assert len(used) == 1


def test_extract_citations_le_os_marcadores_da_resposta():
    used = [_chunk("a"), _chunk("b"), _chunk("c")]
    citations = extract_citations("Segundo [1] e tambem [3], a resposta e essa.", used)
    assert [c.marker for c in citations] == [1, 3]
    assert citations[0].chunk_id == "a"
    assert citations[1].chunk_id == "c"


def test_extract_citations_ignora_marcador_fora_do_intervalo():
    """Modelo alucinando uma fonte [9] que não existe não pode virar citação."""
    citations = extract_citations("De acordo com [9], sim.", [_chunk("a")])
    assert citations == []


def test_extract_citations_deduplica():
    citations = extract_citations("[1] diz isso, e [1] tambem diz aquilo.", [_chunk("a")])
    assert len(citations) == 1


def test_extract_citations_sem_marcador():
    assert extract_citations("Resposta sem nenhuma fonte.", [_chunk("a")]) == []


# ---------- integração ----------


async def test_query_sem_documentos_nao_e_grounded(client: AsyncClient, api_key):
    response = await client.post(
        "/v1/query", json={"question": "Qual e a politica de ferias?"}, headers=api_key
    )
    assert response.status_code == 200
    body = response.json()
    assert body["grounded"] is False
    assert body["citations"] == []
    assert "Não encontrei" in body["answer"]


async def test_query_recupera_o_trecho_certo(client: AsyncClient, api_key, handbook_text):
    await ingest(client, api_key, "manual.md", handbook_text)

    response = await client.post(
        "/v1/query",
        json={"question": "Quantos dias de ferias remuneradas por ano?", "include_chunks": True},
        headers=api_key,
    )
    assert response.status_code == 200
    body = response.json()

    recuperado = " ".join(chunk["text"] for chunk in body["chunks"]).lower()
    assert "30 dias de ferias" in recuperado
    assert body["latency_ms"] >= 0


async def test_resposta_offline_vem_citada_e_grounded(
    client: AsyncClient, api_key, handbook_text
):
    """O provider offline precisa emitir os marcadores [n] do contexto.

    Sem isso o demo do README — que é o primeiro contato de quem clona o
    repositório — responderia sempre com `grounded=false` e citação vazia,
    escondendo justamente a funcionalidade central da API.
    """
    await ingest(client, api_key, "manual.md", handbook_text)

    body = (
        await client.post(
            "/v1/query",
            json={"question": "Quantos dias de ferias remuneradas por ano?"},
            headers=api_key,
        )
    ).json()

    assert body["grounded"] is True
    assert body["citations"], "a resposta offline deveria citar ao menos um trecho"

    for citation in body["citations"]:
        assert citation["char_end"] > citation["char_start"]
        assert citation["excerpt"]


async def test_busca_hibrida_usa_as_duas_estrategias(
    client: AsyncClient, api_key, handbook_text
):
    """Os chunks vencedores devem trazer a posição em cada ranking."""
    await ingest(client, api_key, "manual.md", handbook_text)

    body = (
        await client.post(
            "/v1/query",
            json={"question": "Qual o limite diario de alimentacao?", "include_chunks": True},
            headers=api_key,
        )
    ).json()

    chunks = body["chunks"]
    assert chunks
    assert any(c["vector_rank"] is not None for c in chunks)
    assert any(c["keyword_rank"] is not None for c in chunks)


async def test_lexical_sobrevive_a_palavra_ausente_no_documento(
    client: AsyncClient, api_key, handbook_text
):
    """Os termos da pergunta precisam ser combinados por OR, não por AND.

    "girafa" não existe no manual. Com AND (o que `plainto_tsquery` faz por
    padrão) a consulta inteira não casaria com nada e o braço lexical devolveria
    vazio — degradando a busca híbrida para vetorial pura sem nenhum aviso.
    """
    await ingest(client, api_key, "manual.md", handbook_text)

    body = (
        await client.post(
            "/v1/query",
            json={
                "question": "girafa limite diario de alimentacao",
                "include_chunks": True,
            },
            headers=api_key,
        )
    ).json()

    chunks = body["chunks"]
    assert chunks
    assert any(
        c["keyword_rank"] is not None for c in chunks
    ), "a busca lexical não pode zerar por causa de uma palavra ausente"


async def test_filtro_por_document_ids(client: AsyncClient, api_key, handbook_text):
    manual_id = await ingest(client, api_key, "manual.md", handbook_text)
    outro_id = await ingest(
        client,
        api_key,
        "cardapio.md",
        "O restaurante serve feijoada as quartas. A sobremesa do dia e pudim de leite.",
    )

    body = (
        await client.post(
            "/v1/query",
            json={
                "question": "Quantos dias de ferias?",
                "document_ids": [outro_id],
                "include_chunks": True,
            },
            headers=api_key,
        )
    ).json()

    for chunk in body["chunks"] or []:
        assert chunk["document_id"] == outro_id
        assert chunk["document_id"] != manual_id


async def test_query_nao_enxerga_documento_de_outra_chave(
    client: AsyncClient, api_key, second_api_key, handbook_text
):
    await ingest(client, api_key, "manual.md", handbook_text)

    body = (
        await client.post(
            "/v1/query",
            json={"question": "Quantos dias de ferias?", "include_chunks": True},
            headers=second_api_key,
        )
    ).json()

    assert body["grounded"] is False
    assert not body["chunks"]


async def test_top_k_limita_o_numero_de_trechos(client: AsyncClient, api_key, handbook_text):
    await ingest(client, api_key, "manual.md", handbook_text)

    body = (
        await client.post(
            "/v1/query",
            json={
                "question": "Como funciona o trabalho remoto?",
                "top_k": 2,
                "include_chunks": True,
            },
            headers=api_key,
        )
    ).json()

    assert len(body["chunks"]) <= 2


async def test_chunks_sao_omitidos_por_padrao(client: AsyncClient, api_key, handbook_text):
    await ingest(client, api_key, "manual.md", handbook_text)
    body = (
        await client.post(
            "/v1/query", json={"question": "Como funciona o reembolso?"}, headers=api_key
        )
    ).json()
    assert body["chunks"] is None


async def test_pergunta_muito_curta_e_rejeitada(client: AsyncClient, api_key):
    response = await client.post("/v1/query", json={"question": "oi"}, headers=api_key)
    assert response.status_code == 422


async def test_consulta_e_registrada_no_endpoint_de_uso(
    client: AsyncClient, api_key, handbook_text
):
    await ingest(client, api_key, "manual.md", handbook_text)
    for _ in range(3):
        await client.post(
            "/v1/query", json={"question": "Qual a garantia do equipamento?"}, headers=api_key
        )

    usage = await client.get("/v1/usage?days=1", headers=api_key)
    assert usage.status_code == 200
    body = usage.json()
    assert body["queries"] == 3
    assert body["prompt_tokens"] > 0
    assert body["avg_latency_ms"] >= 0


async def test_uso_e_isolado_por_chave(
    client: AsyncClient, api_key, second_api_key, handbook_text
):
    await ingest(client, api_key, "manual.md", handbook_text)
    await client.post("/v1/query", json={"question": "Qual a garantia?"}, headers=api_key)

    assert (await client.get("/v1/usage", headers=second_api_key)).json()["queries"] == 0
