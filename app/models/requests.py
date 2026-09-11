from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, HttpUrl


class DownloadRequest(BaseModel):
    url: HttpUrl
    format: str = "best"
    audio_only: bool = False


class InfoRequest(BaseModel):
    url: HttpUrl
