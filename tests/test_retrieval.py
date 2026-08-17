"""Testes das partes puras do retrieval: fusão RRF e o embedding fake."""

import pytest

from app.core.retrieval import reciprocal_rank_fusion
from app.providers.fake import FakeEmbeddingProvider, tokenize


def test_rrf_premia_quem_aparece_nas_duas_listas():
    """O ponto central da busca híbrida: consenso entre estratégias vence.

    "b" e "c" aparecem nos dois rankings; "a" e "d" só em um. Mesmo "a" sendo
    o 1º colocado da busca vetorial, ele fica atrás de quem as duas estratégias
    concordam em recomendar — que é exatamente o comportamento desejado.
    """
    fused = reciprocal_rank_fusion(
        {
            "vector": ["a", "b", "c"],
            "keyword": ["c", "b", "d"],
        }
    )
    ranking = [item[0] for item in fused]
    assert set(ranking[:2]) == {"b", "c"}
    assert set(ranking[2:]) == {"a", "d"}


def test_rrf_1o_mais_3o_supera_2o_mais_2o():
    """Propriedade da curva 1/(k+rank): ela é convexa, então o ganho de subir
    para o 1º lugar vale mais do que a perda de cair para o 3º.

    Não é intuição — é o que separa RRF de uma média simples de posições, que
    daria empate aqui. Fixar isso em teste evita que alguém "simplifique" a
    fórmula depois sem perceber que mudou o ranking."""
    fused = reciprocal_rank_fusion(
        {
            # primeiro_terceiro: 1º aqui, 3º lá.  meio_meio: 2º nos dois.
            "vector": ["primeiro_terceiro", "meio_meio", "z"],
            "keyword": ["outro", "meio_meio", "primeiro_terceiro"],
        }
    )
    ranking = [item[0] for item in fused]
    assert ranking.index("primeiro_terceiro") < ranking.index("meio_meio")


def test_rrf_registra_a_posicao_em_cada_estrategia():
    fused = reciprocal_rank_fusion({"vector": ["x", "y"], "keyword": ["y"]})
    positions = {item[0]: item[2] for item in fused}
    assert positions["y"] == {"vector": 2, "keyword": 1}
    assert positions["x"] == {"vector": 1}


def test_rrf_com_uma_lista_preserva_a_ordem():
    fused = reciprocal_rank_fusion({"vector": ["a", "b", "c"]})
    assert [item[0] for item in fused] == ["a", "b", "c"]


def test_rrf_vazio():
    assert reciprocal_rank_fusion({"vector": [], "keyword": []}) == []


def test_scores_do_rrf_sao_decrescentes():
    fused = reciprocal_rank_fusion({"vector": ["a", "b", "c", "d"], "keyword": ["d", "a"]})
    scores = [item[1] for item in fused]
    assert scores == sorted(scores, reverse=True)


# ---------- embedding fake ----------


@pytest.fixture
def embedder() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider(dim=256)


async def test_embedding_e_deterministico(embedder):
    first = await embedder.embed(["texto de exemplo"])
    second = await embedder.embed(["texto de exemplo"])
    assert first.vectors == second.vectors


async def test_embedding_tem_a_dimensao_configurada(embedder):
    result = await embedder.embed(["a", "bb", "ccc"])
    assert len(result.vectors) == 3
    assert all(len(v) == 256 for v in result.vectors)


async def test_embedding_e_normalizado(embedder):
    vector = await embedder.embed_one("politica de ferias e reembolso da empresa")
    norm = sum(v * v for v in vector) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-6)


async def test_textos_parecidos_ficam_mais_proximos(embedder):
    ferias_a = await embedder.embed_one("politica de ferias remuneradas")
    ferias_b = await embedder.embed_one("regras sobre ferias remuneradas anuais")
    reembolso = await embedder.embed_one("reembolso de nota fiscal de viagem")

    def cosine(u, v):
        return sum(a * b for a, b in zip(u, v, strict=True))

    assert cosine(ferias_a, ferias_b) > cosine(ferias_a, reembolso)


async def test_texto_sem_termos_uteis_gera_vetor_zero(embedder):
    vector = await embedder.embed_one("a o de")  # só stopwords
    assert all(v == 0.0 for v in vector)


def test_tokenize_remove_stopwords_e_normaliza_caixa():
    assert tokenize("A Politica DE Ferias") == ["politica", "ferias"]
