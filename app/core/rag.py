"""Orquestração do RAG: recuperar → montar contexto → gerar → citar.

Duas decisões que definem a qualidade do resultado:

1. **Orçamento de contexto.** Chunks entram no prompt até estourar o limite de
   tokens, na ordem do ranking. Jogar tudo dentro custa caro e piora a resposta
   ("lost in the middle": modelos prestam menos atenção no meio de contextos
   longos).
2. **Citação verificável.** O prompt exige marcadores [1], [2]... e o código
   devolve só as fontes que o modelo realmente citou, com o offset exato no
   documento original. Resposta de RAG sem citação rastreável é indistinguível
   de alucinação.
"""

import logging
import re
import time
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.chunking import estimate_tokens
from app.core.retrieval import RetrievedChunk, hybrid_search
from app.providers import Usage, get_chat_provider, get_embedding_provider

log = logging.getLogger(__name__)

MAX_CONTEXT_TOKENS = 3_000
_CITATION_RE = re.compile(r"\[(\d{1,2})\]")

NOT_FOUND_ANSWER = "Não encontrei essa informação nos documentos fornecidos."

SYSTEM_PROMPT = f"""Você é um assistente que responde exclusivamente com base
nos trechos de documentos fornecidos.

Regras obrigatórias:
1. Use APENAS a informação presente nos trechos. Não complete com
   conhecimento próprio.
2. Cite a fonte de cada afirmação com o marcador correspondente, no
   formato [1], [2].
3. Se os trechos não contiverem a resposta, diga exatamente:
   "{NOT_FOUND_ANSWER}" Não tente adivinhar.
4. Responda no mesmo idioma da pergunta, de forma direta e objetiva.
"""


@dataclass(slots=True)
class Citation:
    marker: int
    chunk_id: str
    document_id: str
    document_title: str
    char_start: int
    char_end: int
    score: float
    excerpt: str


@dataclass(slots=True)
class AnswerResult:
    answer: str
    citations: list[Citation] = field(default_factory=list)
    retrieved: list[RetrievedChunk] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    grounded: bool = True


def build_context(
    chunks: list[RetrievedChunk], *, max_tokens: int = MAX_CONTEXT_TOKENS
) -> tuple[str, list[RetrievedChunk]]:
    """Monta o bloco de contexto respeitando o orçamento de tokens.

    Devolve o texto e a lista de chunks que de fato entraram — só esses podem
    virar citação.
    """
    parts: list[str] = []
    used: list[RetrievedChunk] = []
    budget = max_tokens

    for marker, chunk in enumerate(chunks, start=1):
        block = f"[{marker}] (documento: {chunk.document_title})\n{chunk.text}"
        cost = estimate_tokens(block)
        if cost > budget and used:
            break
        budget -= cost
        parts.append(block)
        used.append(chunk)

    return "\n\n".join(parts), used


def extract_citations(answer: str, used: list[RetrievedChunk]) -> list[Citation]:
    """Lê os marcadores [n] da resposta e devolve as fontes correspondentes.

    Marcadores fora do intervalo são descartados em silêncio — é o caso de um
    modelo inventando [7] quando só existiam 5 trechos.
    """
    seen: set[int] = set()
    citations: list[Citation] = []

    for match in _CITATION_RE.finditer(answer):
        marker = int(match.group(1))
        if marker in seen or not 1 <= marker <= len(used):
            continue
        seen.add(marker)
        chunk = used[marker - 1]
        citations.append(
            Citation(
                marker=marker,
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                document_title=chunk.document_title,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                score=chunk.score,
                excerpt=chunk.text[:280],
            )
        )

    return sorted(citations, key=lambda c: c.marker)


async def answer_question(
    session: AsyncSession,
    *,
    api_key_id: str,
    question: str,
    top_k: int | None = None,
    document_ids: list[str] | None = None,
) -> AnswerResult:
    started = time.perf_counter()

    embedder = get_embedding_provider()
    embedding_result = await embedder.embed([question])
    query_vector = embedding_result.vectors[0]

    retrieved = await hybrid_search(
        session,
        api_key_id=api_key_id,
        query=question,
        query_vector=query_vector,
        top_k=top_k,
        document_ids=document_ids,
    )

    if not retrieved:
        return AnswerResult(
            answer=NOT_FOUND_ANSWER,
            usage=embedding_result.usage,
            latency_ms=int((time.perf_counter() - started) * 1000),
            grounded=False,
        )

    context, used = build_context(retrieved)
    user_prompt = f"TRECHOS:\n{context}\n\nPERGUNTA: {question}"

    chat = get_chat_provider()
    completion = await chat.complete(SYSTEM_PROMPT, user_prompt)

    citations = extract_citations(completion.text, used)
    latency_ms = int((time.perf_counter() - started) * 1000)

    log.info(
        "resposta gerada",
        extra={
            "chunks_retrieved": len(retrieved),
            "chunks_used": len(used),
            "citations": len(citations),
            "latency_ms": latency_ms,
        },
    )

    return AnswerResult(
        answer=completion.text,
        citations=citations,
        retrieved=used,
        usage=embedding_result.usage + completion.usage,
        latency_ms=latency_ms,
        grounded=bool(citations),
    )
