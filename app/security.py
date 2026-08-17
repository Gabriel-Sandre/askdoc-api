"""Autenticação por API key.

O banco guarda apenas o SHA-256 da chave, nunca o valor em claro — se o dump
do banco vazar, as chaves continuam inutilizáveis. O usuário vê a chave uma
única vez, no momento da criação.

O prefixo (`ad_live_a1b2c3d4`) é armazenado separadamente e serve para duas
coisas: indexar a busca sem varrer a tabela inteira, e permitir que a pessoa
identifique qual chave é qual no painel sem nunca revelar o segredo.
"""

import hashlib
import secrets

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import ApiKey

KEY_PREFIX = "ad_live_"
PREFIX_LENGTH = 12  # "ad_live_" + 4 caracteres


def generate_api_key() -> tuple[str, str, str]:
    """Devolve (chave_em_claro, prefixo, hash). A chave em claro não é persistida."""
    raw = f"{KEY_PREFIX}{secrets.token_urlsafe(32)}"
    return raw, raw[:PREFIX_LENGTH], hash_api_key(raw)


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def resolve_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    session: AsyncSession = Depends(get_session),
) -> ApiKey:
    """Dependency: valida a chave e devolve o registro. 401 em qualquer falha."""
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Header X-API-Key ausente.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    key_hash = hash_api_key(x_api_key)
    result = await session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
    api_key = result.scalar_one_or_none()

    # mensagem idêntica para chave inexistente e revogada: não entregamos ao
    # atacante a informação de que a chave um dia existiu
    if api_key is None or not api_key.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key inválida ou revogada.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return api_key


async def require_admin(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    """Protege a criação e revogação de chaves.

    `compare_digest` evita timing attack: comparação de string comum retorna
    mais rápido quando o primeiro caractere já difere, e isso vaza informação.
    """
    if not x_admin_token or not secrets.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin token inválido."
        )
