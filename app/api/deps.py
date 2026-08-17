"""Dependências compartilhadas pelas rotas."""

from fastapi import Depends, HTTPException, Response, status

from app.config import settings
from app.models import ApiKey
from app.rate_limit import SlidingWindowLimiter
from app.security import resolve_api_key

limiter = SlidingWindowLimiter(limit=settings.rate_limit_per_minute, window_seconds=60)


async def rate_limited_key(
    response: Response, api_key: ApiKey = Depends(resolve_api_key)
) -> ApiKey:
    """Autentica e aplica o rate limit na mesma dependency.

    Os headers `X-RateLimit-*` vão em toda resposta, não só no 429 — assim o
    cliente consegue se auto-regular antes de bater no limite.
    """
    state = limiter.hit(api_key.id)
    response.headers["X-RateLimit-Limit"] = str(state.limit)
    response.headers["X-RateLimit-Remaining"] = str(state.remaining)

    if not state.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Limite de {state.limit} requisições por minuto excedido.",
            headers={
                "Retry-After": str(state.retry_after),
                "X-RateLimit-Limit": str(state.limit),
                "X-RateLimit-Remaining": "0",
            },
        )

    return api_key
