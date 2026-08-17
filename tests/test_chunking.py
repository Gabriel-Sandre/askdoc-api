"""Testes do chunking.

O que importa aqui é a invariante que sustenta as citações: para todo chunk,
`texto_original[char_start:char_end] == chunk.text`. Se isso quebrar, a API
continua respondendo — só passa a citar o trecho errado, que é pior.
"""

import pytest

from app.core.chunking import chunk_text, estimate_tokens, normalize


def test_texto_vazio_nao_gera_chunk():
    assert chunk_text("") == []
    assert chunk_text("   \n\n  \t ") == []


def test_texto_curto_vira_um_chunk_unico():
    chunks = chunk_text("Uma frase curta e direta.", chunk_size=400)
    assert len(chunks) == 1
    assert chunks[0].text == "Uma frase curta e direta."
    assert chunks[0].char_start == 0


def test_offsets_apontam_para_o_texto_normalizado():
    """A invariante que garante citação correta."""
    source = " ".join(f"Sentenca numero {i} com algum conteudo textual." for i in range(80))
    normalized = normalize(source)

    for chunk in chunk_text(source, chunk_size=300, overlap=50):
        assert normalized[chunk.char_start : chunk.char_end] == chunk.text


def test_chunks_respeitam_o_tamanho_maximo():
    source = "palavra " * 2000
    for chunk in chunk_text(source, chunk_size=500, overlap=100):
        assert chunk.length <= 500


def test_ordinais_sao_sequenciais_e_sem_buraco():
    source = "Frase de teste com tamanho razoavel. " * 100
    chunks = chunk_text(source, chunk_size=300, overlap=60)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_sobreposicao_existe_entre_chunks_consecutivos():
    source = "Conteudo relevante para testar sobreposicao. " * 60
    chunks = chunk_text(source, chunk_size=400, overlap=120)
    assert len(chunks) > 1
    for previous, current in zip(chunks, chunks[1:], strict=False):
        assert current.char_start < previous.char_end, "os chunks deveriam se sobrepor"


def test_corte_prefere_fronteira_de_paragrafo():
    source = "Primeiro paragrafo com bastante conteudo.\n\n" + "Segundo paragrafo. " * 40
    chunks = chunk_text(source, chunk_size=120, overlap=20)
    # o primeiro corte não deve partir a palavra "conteudo" ao meio
    assert not chunks[0].text.endswith("conte")


def test_normalize_colapsa_espacos_e_quebras():
    assert normalize("a\r\n\r\n\r\n\r\nb") == "a\n\nb"
    assert normalize("muitos     espacos") == "muitos espacos"
    assert normalize("  bordas  ") == "bordas"


@pytest.mark.parametrize("chunk_size,overlap", [(0, 0), (-1, 0), (100, 100), (100, 150), (100, -1)])
def test_parametros_invalidos_sao_rejeitados(chunk_size, overlap):
    with pytest.raises(ValueError):
        chunk_text("qualquer texto", chunk_size=chunk_size, overlap=overlap)


def test_progresso_garantido_sem_overlap():
    """overlap=0 não pode causar laço infinito nem chunk repetido."""
    chunks = chunk_text("x" * 1000, chunk_size=100, overlap=0)
    assert len(chunks) == 10
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == 1000


def test_estimate_tokens_nunca_zera():
    assert estimate_tokens("") == 1
    assert estimate_tokens("a" * 400) == 100
