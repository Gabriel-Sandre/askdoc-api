"""Provider offline: sem chave de API, sem custo, determinístico.

Não é um brinquedo jogado no projeto para "fazer os testes passarem" — ele
resolve dois problemas reais:

1. **CI sem segredo.** A suíte de testes roda inteira no GitHub Actions sem
   nenhuma API key, e o resultado é reprodutível.
2. **Demo instantânea.** Qualquer pessoa clona o repositório, sobe a API e
   vê o fluxo completo funcionando sem gastar um centavo.

O embedding usa o *hashing trick* (bag-of-words projetado num vetor de tamanho
fixo, normalizado em L2). Não captura sinônimos como um modelo treinado, mas
captura sobreposição de termos — o suficiente para o retrieval se comportar de
forma coerente e para os testes afirmarem algo de verdade.
"""

import hashlib
import math
import re
from collections import Counter

from app.providers.base import ChatProvider, ChatResult, EmbeddingProvider, EmbeddingResult, Usage

_WORD_RE = re.compile(r"\w+", re.UNICODE)
_SENT_RE = re.compile(r"(?<=[.!?])\s+")
# cabeçalho de bloco do contexto montado em core/rag.py: "[1] (documento: x)"
_CONTEXT_MARKER_RE = re.compile(r"^\[(\d{1,2})\]\s*\(documento:")

# stopwords PT/EN mais comuns: sem isso, "de/a/o/the" dominam a similaridade
_STOPWORDS = {
    "a", "o", "e", "de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas",
    "um", "uma", "para", "por", "com", "que", "se", "ao", "as", "os", "the", "of",
    "and", "to", "in", "is", "it", "for", "on", "at", "an", "be", "this",
}


def tokenize(text: str) -> list[str]:
    return [t for t in _WORD_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


class FakeEmbeddingProvider(EmbeddingProvider):
    name = "fake"

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def _vector(self, text: str) -> list[float]:
        counts = Counter(tokenize(text))
        vec = [0.0] * self.dim
        for term, tf in counts.items():
            digest = hashlib.blake2b(term.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            # tf sublinear: a 10ª ocorrência de um termo vale menos que a 2ª
            vec[index] += sign * (1.0 + math.log(tf))

        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            # texto vazio ou só stopwords: vetor válido, porém sem direção útil
            return [0.0] * self.dim
        return [v / norm for v in vec]

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        return EmbeddingResult(vectors=[self._vector(t) for t in texts])


class FakeChatProvider(ChatProvider):
    """Responde de forma extrativa: escolhe as frases do contexto que mais
    cobrem os termos da pergunta. Sem alucinação, porque nada é gerado.

    As frases saem com o marcador [n] do bloco de onde vieram — sem isso o
    modo offline responderia sempre com `grounded=false` e citação vazia,
    escondendo justamente a funcionalidade central da API.
    """

    name = "fake"

    async def complete(
        self, system: str, user: str, *, max_tokens: int = 800
    ) -> ChatResult:
        question, context = self._split_prompt(user)
        q_terms = set(tokenize(question))

        scored: list[tuple[float, str, int | None]] = []
        marker: int | None = None

        for line in context.splitlines():
            stripped = line.strip()

            header = _CONTEXT_MARKER_RE.match(stripped)
            if header:
                marker = int(header.group(1))
                continue

            if len(stripped) < 25:
                continue
            for sentence in _SENT_RE.split(stripped):
                sentence = sentence.strip()
                if len(sentence) < 25:
                    continue
                terms = set(tokenize(sentence))
                if not terms:
                    continue
                overlap = len(q_terms & terms) / (len(q_terms) or 1)
                if overlap > 0:
                    scored.append((overlap, sentence, marker))

        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            return ChatResult(
                text="Não encontrei essa informação nos documentos fornecidos.",
                usage=Usage(prompt_tokens=len(user) // 4),
            )

        best = [
            f"{sentence} [{marker}]" if marker else sentence
            for _, sentence, marker in scored[:3]
        ]
        answer = " ".join(best)
        return ChatResult(
            text=answer,
            usage=Usage(prompt_tokens=len(user) // 4, completion_tokens=len(answer) // 4),
        )

    @staticmethod
    def _split_prompt(user: str) -> tuple[str, str]:
        marker = "PERGUNTA:"
        if marker in user:
            context, question = user.rsplit(marker, 1)
            return question, context
        return user, user
