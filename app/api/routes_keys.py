"""Administração de API keys. Protegido pelo header X-Admin-Token."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import ApiKey
from app.schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyPublic
from app.security import generate_api_key, require_admin

router = APIRouter(prefix="/v1/keys", tags=["api-keys"], dependencies=[Depends(require_admin)])


@router.post("", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_key(
    payload: ApiKeyCreate, session: AsyncSession = Depends(get_session)
) -> ApiKeyCreated:
    raw, prefix, key_hash = generate_api_key()
    api_key = ApiKey(name=payload.name, key_prefix=prefix, key_hash=key_hash)
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)

    return ApiKeyCreated(
        id=api_key.id,
        name=api_key.name,
        api_key=raw,  # única vez que o valor em claro sai da aplicação
        key_prefix=api_key.key_prefix,
        created_at=api_key.created_at,
    )


@router.get("", response_model=list[ApiKeyPublic])
async def list_keys(session: AsyncSession = Depends(get_session)) -> list[ApiKey]:
    result = await session.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))
    return list(result.scalars().all())


@router.delete("/{key_id}", response_model=ApiKeyPublic)
async def revoke_key(key_id: str, session: AsyncSession = Depends(get_session)) -> ApiKey:
    api_key = await session.get(ApiKey, key_id)
    if api_key is None:
        raise HTTPException(status_code=404, detail="API key não encontrada.")

    # revogação lógica: preserva o histórico de quem ingeriu o quê
    if api_key.revoked_at is None:
        api_key.revoked_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(api_key)

    return api_key
