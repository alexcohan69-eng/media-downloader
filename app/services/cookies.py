from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


def build_ydl_opts(
    *,
    format_str: str | None = None,
    audio_only: bool = False,
    cookie_browser: str | None = None,
    cookie_file: str | None = None,
    outtmpl: str | None = None,
) -> dict[str, Any]:
    browser = cookie_browser or settings.cookie_browser

    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
    }

    cookie_file = cookie_file or settings.cookie_file
    if cookie_file:
        if not Path(cookie_file).exists():
            raise ValueError(
                f"Cookie file not found: {cookie_file} "
                "(export as Netscape cookies.txt; e.g. with a browser extension)"
            )
        opts["cookiefile"] = cookie_file
    else:
        opts["cookiesfrombrowser"] = (browser,)

    if audio_only:
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "best",
            }
        ]
    else:
        opts["format"] = format_str or settings.default_format

    if outtmpl:
        opts["outtmpl"] = outtmpl

    return opts


def is_cookie_error(error: Exception) -> bool:
    msg = str(error).lower()
    return "cookie" in msg.lower()


def get_media_type(info: dict[str, Any]) -> str:
    if info.get("vcodec") and info["vcodec"] != "none":
        return "video"
    if info.get("acodec") and info["acodec"] != "none":
        return "audio"
    if info.get("ext") in ("jpg", "jpeg", "png", "gif", "webp", "svg", "bmp"):
        return "image"
    return "video"


def sanitize_formats(formats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized = []
    for f in formats:
        sanitized.append(
            {
                "format_id": f.get("format_id", ""),
                "ext": f.get("ext", ""),
                "resolution": f.get("resolution"),
                "filesize": f.get("filesize") or f.get("filesize_approx"),
                "vcodec": f.get("vcodec"),
                "acodec": f.get("acodec"),
                "fps": f.get("fps"),
                "tbr": f.get("tbr"),
            }
        )
    return sanitized