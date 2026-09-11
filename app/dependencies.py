from __future__ import annotations

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader

from app.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)


async def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    if api_key in settings.api_keys:
        return api_key
    raise HTTPException(status_code=403, detail="Invalid or missing API key")
