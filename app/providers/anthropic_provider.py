"""Provider de chat da Anthropic (Claude) via HTTP puro.

A Anthropic não expõe endpoint de embeddings, então o `EMBEDDING_PROVIDER`
continua sendo `openai` ou `fake` — é exatamente por isso que embedding e chat
são interfaces separadas em vez de um único "AIProvider".
"""

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.providers.base import ChatProvider, ChatResult, ProviderError, Usage

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

# USD por 1M de tokens. Valores indicativos — ajuste conforme a tabela vigente.
PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-opus-4-1": (15.00, 75.00),
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    in_price, out_price = PRICING.get(model, (0.0, 0.0))
    return round(
        (prompt_tokens * in_price + completion_tokens * out_price) / 1_000_000, 8
    )


class AnthropicChatProvider(ChatProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, timeout: float = 60.0) -> None:
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY é obrigatória para LLM_PROVIDER=anthropic")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        retry=retry_if_exception_type((httpx.TransportError, ProviderError)),
        reraise=True,
    )
    async def complete(
        self, system: str, user: str, *, max_tokens: int = 800
    ) -> ChatResult:
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0.1,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                API_URL,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": API_VERSION,
                    "content-type": "application/json",
                },
                json=payload,
            )
        if response.status_code >= 400:
            raise ProviderError(f"Anthropic {response.status_code}: {response.text[:200]}")

        body = response.json()
        usage = body.get("usage", {})
        prompt_tokens = usage.get("input_tokens", 0)
        completion_tokens = usage.get("output_tokens", 0)
        text = "".join(
            block.get("text", "")
            for block in body.get("content", [])
            if block.get("type") == "text"
        )
        return ChatResult(
            text=text,
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=estimate_cost(self.model, prompt_tokens, completion_tokens),
            ),
        )
