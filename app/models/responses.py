from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class FormatInfo(BaseModel):
    format_id: str
    ext: str
    resolution: str | None = None
    filesize: int | None = None
    vcodec: str | None = None
    acodec: str | None = None
    fps: float | None = None
    tbr: float | None = None
    url: str | None = None


class MediaInfo(BaseModel):
    title: str | None = None
    type: str  # video, audio, image
    duration: float | None = None
    thumbnail: str | None = None
    uploader: str | None = None
    extractor: str | None = None
    webpage_url: str | None = None
    views: int | None = None
    likes: int | None = None
    reposts: int | None = None
    comments: int | None = None
    formats: list[FormatInfo] = []


class ErrorResponse(BaseModel):
    detail: str
    url: str | None = None
