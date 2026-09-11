from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.dependencies import verify_api_key
from app.models.responses import ErrorResponse, MediaInfo
from app.services.downloader import MediaDownloader
from app.services.telegram import TelegramSender

router = APIRouter(prefix="/api/v1", dependencies=[Depends(verify_api_key)])


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
) -> MediaInfo:
    downloader = MediaDownloader(cookie_browser=browser, cookie_file=cookies)
    try:
        info = downloader.get_info(url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    if title:
        info.title = title
    return info


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
):
    downloader = MediaDownloader(cookie_browser=browser, cookie_file=cookies)
    try:
        result = downloader.download(
            url, format_str=format_str, audio_only=audio_only, title=title
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if send_telegram:
        sender = TelegramSender()
        try:
            sent = sender.send_media(
                result.file_path,
                result.filename,
                caption=_real_title(downloader, url, title),
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

    return StreamingResponse(
        content=result.stream,
        media_type=result.media_type,
        headers=headers,
        background=BackgroundTask(downloader._cleanup, result.tmp_path),
    )


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