"""Providers OpenAI (chat + embeddings) via HTTP puro.

Sem SDK de propósito: é uma dependência a menos, o contrato fica explícito e
dá para ver exatamente o que vai na requisição. Retry com backoff exponencial
cobre 429 e 5xx, que são as falhas que realmente acontecem em produção.
"""

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.providers.base import (
    ChatProvider,
    ChatResult,
    EmbeddingProvider,
    EmbeddingResult,
    ProviderError,
    Usage,
)

API_BASE = "https://api.openai.com/v1"

# USD por 1M de tokens. Valores indicativos — ajuste conforme a tabela vigente.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
}

_RETRY = dict(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
    retry=retry_if_exception_type((httpx.TransportError, ProviderError)),
    reraise=True,
)


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    in_price, out_price = PRICING.get(model, (0.0, 0.0))
    return round(
        (prompt_tokens * in_price + completion_tokens * out_price) / 1_000_000, 8
    )


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code == 429 or response.status_code >= 500:
        # transitório: vale retentar
        raise ProviderError(f"OpenAI {response.status_code}: {response.text[:200]}")
    if response.status_code >= 400:
        # 4xx de cliente (chave inválida, payload errado): retentar não ajuda
        raise ProviderError(f"OpenAI {response.status_code}: {response.text[:200]}") from None


class OpenAIEmbeddingProvider(EmbeddingProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str, dim: int, timeout: float = 30.0) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY é obrigatória para EMBEDDING_PROVIDER=openai")
        self.api_key = api_key
        self.model = model
        self.dim = dim
        self.timeout = timeout

    @retry(**_RETRY)
    async def embed(self, texts: list[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=[])
        payload = {"model": self.model, "input": texts, "dimensions": self.dim}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{API_BASE}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
        _raise_for_status(response)
        body = response.json()
        vectors = [item["embedding"] for item in sorted(body["data"], key=lambda d: d["index"])]
        prompt_tokens = body.get("usage", {}).get("prompt_tokens", 0)
        return EmbeddingResult(
            vectors=vectors,
            usage=Usage(
                prompt_tokens=prompt_tokens,
                cost_usd=estimate_cost(self.model, prompt_tokens, 0),
            ),
        )


class OpenAIChatProvider(ChatProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str, timeout: float = 60.0) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY é obrigatória para LLM_PROVIDER=openai")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @retry(**_RETRY)
    async def complete(
        self, system: str, user: str, *, max_tokens: int = 800
    ) -> ChatResult:
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0.1,  # RAG quer fidelidade ao contexto, não criatividade
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{API_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
        _raise_for_status(response)
        body = response.json()
        usage = body.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        return ChatResult(
            text=body["choices"][0]["message"]["content"],
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=estimate_cost(self.model, prompt_tokens, completion_tokens),
            ),
        )
