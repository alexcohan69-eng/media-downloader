from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from app.dependencies import verify_api_key
from app.models.responses import ErrorResponse, MediaInfo
from app.services.downloader import MediaDownloader
from app.services.telegram import TelegramSender
from app.services.translator import detect_and_translate

router = APIRouter(prefix="/api/v1", dependencies=[Depends(verify_api_key)])


# ---------------------------------------------------------------------------
# Translation endpoint
# ---------------------------------------------------------------------------

class TranslateRequest(BaseModel):
    text: str


class TranslateResponse(BaseModel):
    original: str
    translated: str
    detected_lang: str | None
    was_translated: bool


@router.post(
    "/translate",
    response_model=TranslateResponse,
    summary="Detect language and translate to English (skips EN/BN)",
)
async def translate_text(body: TranslateRequest) -> TranslateResponse:
    translated, lang = detect_and_translate(body.text)
    return TranslateResponse(
        original=body.text,
        translated=translated,
        detected_lang=lang,
        was_translated=(translated != body.text),
    )


# ---------------------------------------------------------------------------
# Info endpoint
# ---------------------------------------------------------------------------

@router.get(
    "/info",
    response_model=MediaInfo,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def get_media_info(
    url: str = Query(..., description="URL of the media to get info for"),
    browser: str | None = Query(None, description="Cookie browser override"),
    cookies: str | None = Query(
        None, description="Path to a Netscape cookies.txt file on the server (for age-restricted/NSFW content)"
    ),
    title: str | None = Query(
        None, description="Custom title that replaces the real caption/title"
    ),
    auto_translate: bool = Query(
        True,
        description="If true, auto-translate title to English when not EN/BN",
    ),
) -> MediaInfo:
    downloader = MediaDownloader(cookie_browser=browser, cookie_file=cookies)
    try:
        info = downloader.get_info(url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if title:
        info.title = title
    elif auto_translate and info.title:
        translated, _ = detect_and_translate(info.title)
        info.title = translated

    return info


# ---------------------------------------------------------------------------
# Download endpoint
# ---------------------------------------------------------------------------

@router.get(
    "/download",
    responses={
        200: {"content": {"application/octet-stream": {}}},
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def download_media(
    url: str = Query(..., description="URL of the media to download"),
    format_str: str = Query(
        "best", description="Format selector (best, bestvideo+bestaudio, bestaudio)"
    ),
    audio_only: bool = Query(False, description="Download audio only"),
    browser: str | None = Query(None, description="Cookie browser override"),
    cookies: str | None = Query(
        None, description="Path to a Netscape cookies.txt file on the server (for age-restricted/NSFW content)"
    ),
    title: str | None = Query(
        None, description="Custom title that replaces the real caption/title"
    ),
    send_telegram: bool = Query(
        False, description="Send the downloaded media to a Telegram chat instead of streaming it back"
    ),
    use_userbot: bool | None = Query(
        None,
        description="Telegram sender: True to force userbot, False for bot API, None for auto (>50MB uses userbot)",
    ),
    auto_translate: bool = Query(
        True,
        description="If true, auto-translate caption to English when not EN/BN",
    ),
):
    downloader = MediaDownloader(cookie_browser=browser, cookie_file=cookies)
    try:
        result = downloader.download(
            url, format_str=format_str, audio_only=audio_only, title=title
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Resolve caption, optionally translate
    caption = _real_title(downloader, url, title)
    translated_caption = caption
    detected_lang: str | None = None
    sent: dict = {}

    if auto_translate and caption:
        translated_caption, detected_lang = detect_and_translate(caption)

    if send_telegram:
        sender = TelegramSender()
        try:
            sent = await sender.send_media(
                result.file_path,
                result.filename,
                caption=translated_caption,
                use_userbot=use_userbot,
            )
        except Exception as e:
            downloader._cleanup(result.tmp_path)
            raise HTTPException(status_code=400, detail=str(e))

    headers = {
        "Content-Disposition": f'attachment; filename="{result.filename}"',
        "Content-Length": str(result.file_size),
    }
    if send_telegram:
        headers["X-Telegram-Sent"] = "true"
        headers["X-Telegram-Link"] = sent["link"]
        headers["X-Telegram-Message-Id"] = str(sent["message_id"])
        headers["X-Telegram-Sender"] = sent.get("sender", "bot")

    if auto_translate and translated_caption and translated_caption != caption:
        headers["X-Translated-Caption"] = translated_caption
        headers["X-Detected-Lang"] = detected_lang or ""

    return StreamingResponse(
        content=result.stream,
        media_type=result.media_type,
        headers=headers,
        background=BackgroundTask(downloader._cleanup, result.tmp_path),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _real_title(
    downloader: MediaDownloader,
    url: str,
    title: str | None,
) -> str | None:
    if title:
        return title
    try:
        return downloader.get_info(url).title
    except Exception:
        return None
