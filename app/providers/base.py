"""Contratos dos providers de IA.

Todo o resto da aplicação depende só destas duas interfaces. Trocar OpenAI por
Anthropic, por um modelo local ou pelo provider fake dos testes não toca em
nenhuma linha das rotas, do retrieval ou do pipeline de ingestão.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            round(self.cost_usd + other.cost_usd, 8),
        )


@dataclass(slots=True)
class ChatResult:
    text: str
    usage: Usage = field(default_factory=Usage)


@dataclass(slots=True)
class EmbeddingResult:
    vectors: list[list[float]]
    usage: Usage = field(default_factory=Usage)


class EmbeddingProvider(ABC):
    """Transforma texto em vetores."""

    name: str
    dim: int

    @abstractmethod
    async def embed(self, texts: list[str]) -> EmbeddingResult: ...

    async def embed_one(self, text: str) -> list[float]:
        result = await self.embed([text])
        return result.vectors[0]


class ChatProvider(ABC):
    """Gera a resposta final a partir do contexto recuperado."""

    name: str

    @abstractmethod
    async def complete(
        self, system: str, user: str, *, max_tokens: int = 800
    ) -> ChatResult: ...


class ProviderError(RuntimeError):
    """Falha ao falar com o provider externo (rede, rate limit, 5xx)."""
