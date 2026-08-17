"""Fábrica de providers: lê a config e devolve a implementação certa.

As instâncias ficam em cache porque são stateless e baratas de reusar. Os
testes sobrescrevem via `set_providers`, sem precisar mexer em variável de
ambiente nem em monkeypatch de import.
"""

from app.config import settings
from app.providers.base import (
    ChatProvider,
    ChatResult,
    EmbeddingProvider,
    EmbeddingResult,
    ProviderError,
    Usage,
)
from app.providers.fake import FakeChatProvider, FakeEmbeddingProvider

__all__ = [
    "ChatProvider",
    "ChatResult",
    "EmbeddingProvider",
    "EmbeddingResult",
    "ProviderError",
    "Usage",
    "get_chat_provider",
    "get_embedding_provider",
    "set_providers",
]

_embedding: EmbeddingProvider | None = None
_chat: ChatProvider | None = None


def _build_embedding_provider() -> EmbeddingProvider:
    kind = settings.embedding_provider.lower()
    if kind == "openai":
        from app.providers.openai_provider import OpenAIEmbeddingProvider

        return OpenAIEmbeddingProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_embedding_model,
            dim=settings.embedding_dim,
        )
    if kind == "fake":
        return FakeEmbeddingProvider(dim=settings.embedding_dim)
    raise ValueError(f"EMBEDDING_PROVIDER desconhecido: {kind!r} (use 'fake' ou 'openai')")


def _build_chat_provider() -> ChatProvider:
    kind = settings.llm_provider.lower()
    if kind == "openai":
        from app.providers.openai_provider import OpenAIChatProvider

        return OpenAIChatProvider(
            api_key=settings.openai_api_key, model=settings.openai_chat_model
        )
    if kind == "anthropic":
        from app.providers.anthropic_provider import AnthropicChatProvider

        return AnthropicChatProvider(
            api_key=settings.anthropic_api_key, model=settings.anthropic_chat_model
        )
    if kind == "fake":
        return FakeChatProvider()
    raise ValueError(
        f"LLM_PROVIDER desconhecido: {kind!r} (use 'fake', 'openai' ou 'anthropic')"
    )


def get_embedding_provider() -> EmbeddingProvider:
    global _embedding
    if _embedding is None:
        _embedding = _build_embedding_provider()
    return _embedding


def get_chat_provider() -> ChatProvider:
    global _chat
    if _chat is None:
        _chat = _build_chat_provider()
    return _chat


def set_providers(
    *, embedding: EmbeddingProvider | None = None, chat: ChatProvider | None = None
) -> None:
    """Injeção manual — usado pelos testes e por scripts de benchmark."""
    global _embedding, _chat
    if embedding is not None:
        _embedding = embedding
    if chat is not None:
        _chat = chat
