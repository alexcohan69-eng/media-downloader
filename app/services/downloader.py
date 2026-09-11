from __future__ import annotations

import logging
import mimetypes
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generator

import yt_dlp

from app.config import settings
from app.models.responses import FormatInfo, MediaInfo
from app.services.cookies import (
    build_ydl_opts,
    get_media_type,
    is_cookie_error,
    sanitize_formats,
)
from app.services.page_images import (
    PageImages,
    extract_page_images,
    ext_from_image_url,
    resolve_twitter_media,
)

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024


def _browser_cookies_only(opts: dict[str, Any]) -> bool:
    return "cookiesfrombrowser" in opts and "cookiefile" not in opts


@dataclass
class DownloadResult:
    stream: Generator[bytes, None, None]
    filename: str
    media_type: str
    file_size: int
    tmp_path: Path
    file_path: Path



class MediaDownloader:
    def __init__(self, cookie_browser: str | None = None, cookie_file: str | None = None):
        self.cookie_browser = cookie_browser
        self.cookie_file = cookie_file

    def _build_opts(self, **kwargs) -> dict[str, Any]:
        return build_ydl_opts(
            cookie_browser=self.cookie_browser,
            cookie_file=self.cookie_file,
            **kwargs,
        )

    def _extract(self, url: str, download: bool, opts: dict[str, Any]) -> dict[str, Any]:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=download)
                if info is None:
                    raise ValueError(f"Could not extract info for: {url}")
                return ydl.sanitize_info(info)
        except Exception as e:
            if is_cookie_error(e) and _browser_cookies_only(opts):
                logger.warning("Cookie extraction failed (%s), retrying without cookies", e)
                retry_opts = {
                    k: v for k, v in opts.items()
                    if k not in ("cookiesfrombrowser", "cookiefile")
                }
                with yt_dlp.YoutubeDL(retry_opts) as ydl:
                    info = ydl.extract_info(url, download=download)
                    if info is None:
                        raise ValueError(f"Could not extract info for: {url}")
                    return ydl.sanitize_info(info)
            raise

    def get_info(self, url: str) -> MediaInfo:
        opts = self._build_opts()
        opts["skip_download"] = True

        try:
            raw = self._extract(url, download=False, opts=opts)
        except Exception:
            page = self._page_images(url)
            if page.images:
                formats = [
                    FormatInfo(
                        format_id=f"media-{i}",
                        ext=ext_from_image_url(img),
                        resolution=(
                            f"{res[0]}x{res[1]}"
                            if (res := page.resolutions[i] if i < len(page.resolutions) else None)
                            else "original"
                        ),
                        url=img,
                    )
                    for i, img in enumerate(page.images)
                ]
                return MediaInfo(
                    title=page.title,
                    type=page.media_type,
                    thumbnail=page.images[0],
                    uploader=page.uploader,
                    extractor="page-image",
                    webpage_url=url,
                    views=page.views,
                    likes=page.likes,
                    reposts=page.reposts,
                    comments=page.comments,
                    formats=formats,
                )
            raise

        media_type = get_media_type(raw)
        formats = sanitize_formats(raw.get("formats", []))

        return MediaInfo(
            title=raw.get("title"),
            type=media_type,
            duration=raw.get("duration"),
            thumbnail=raw.get("thumbnail"),
            uploader=raw.get("uploader") or raw.get("channel"),
            extractor=raw.get("extractor"),
            webpage_url=raw.get("webpage_url"),
            views=raw.get("view_count"),
            likes=raw.get("like_count"),
            reposts=raw.get("repost_count"),
            comments=raw.get("comment_count"),
            formats=[FormatInfo(**f) for f in formats],
        )

    def download(
        self,
        url: str,
        format_str: str = "best",
        audio_only: bool = False,
        title: str | None = None,
    ) -> DownloadResult:
        tmp_path = self._create_tmp_path()
        outtmpl = str(tmp_path.with_suffix(".%(ext)s"))

        opts = self._build_opts(
            format_str=format_str,
            audio_only=audio_only,
            outtmpl=outtmpl,
        )

        try:
            fallback_page: PageImages | None = None
            try:
                info = self._extract(url, download=True, opts=opts)
                downloaded = self._find_downloaded_file(tmp_path, info)
            except Exception:
                page = self._page_images(url)
                if not page.images:
                    raise
                logger.info("Using page-image fallback for %s (%d images)", url, len(page.images))
                self._cleanup(tmp_path)
                fallback_page = page
                image_url = page.images[0]
                info = self._extract(image_url, download=True, opts=opts)
                downloaded = self._find_downloaded_file(tmp_path, info)

            if downloaded.suffix.lstrip(".") in ("", "unknown_video", "unknown_audio", "html"):
                correct_ext = ext_from_image_url(
                    (fallback_page.images[0] if fallback_page else url) or url
                )
                new_path = downloaded.with_suffix(f".{correct_ext}")
                os.replace(downloaded, new_path)
                downloaded = new_path

            if fallback_page and fallback_page.title:
                info = dict(info)
                info["title"] = fallback_page.title

            if title:
                info = dict(info)
                info["title"] = title

            file_size = downloaded.stat().st_size
            if file_size > settings.max_file_size_bytes:
                raise ValueError(
                    f"File too large: {file_size} bytes "
                    f"(max: {settings.max_file_size_bytes} bytes)"
                )

            ext = downloaded.suffix.lstrip(".") or "mp4"
            media_type = mimetypes.guess_type(downloaded.name)[0] or "application/octet-stream"

            title = info.get("title") or "download"
            safe_stem = "".join(
                c if (c.isalnum() and ord(c) < 128) or c in "._-" else "_" for c in title
            )[:200]
            if not any(ch.isalnum() for ch in safe_stem):
                tail = url.rstrip("/").split("?")[0].split("/")[-1]
                safe_stem = f"media_{re.sub(r'[^A-Za-z0-9._-]', '_', tail)}"
            filename = f"{safe_stem}.{ext}"

            return DownloadResult(
                stream=self._stream_and_cleanup(downloaded, tmp_path),
                filename=filename,
                media_type=media_type,
                file_size=file_size,
                tmp_path=tmp_path,
                file_path=downloaded,
            )

        except Exception as e:
            self._cleanup(tmp_path)
            logger.error("Download failed: %s", e)
            raise

    # --- page-image fallback helpers ---

    def _page_images(self, url: str) -> PageImages:
        resolved = resolve_twitter_media(url, cookie_file=self.cookie_file)
        if resolved and resolved.images:
            logger.info("Twitter resolver returned %d media url(s)", len(resolved.images))
            return resolved
        return extract_page_images(url, cookie_file=self.cookie_file)

    def _stream_and_cleanup(
        self, file_path: Path, tmp_path: Path
    ) -> Generator[bytes, None, None]:
        try:
            with open(file_path, "rb") as f:
                while chunk := f.read(CHUNK_SIZE):
                    yield chunk
        finally:
            self._cleanup(tmp_path)

    def _create_tmp_path(self) -> Path:
        tmp_dir = settings.temp_path
        tmp_dir.mkdir(parents=True, exist_ok=True)

        tmp_file = tempfile.NamedTemporaryFile(
            dir=tmp_dir, delete=False, suffix=".tmp"
        )
        tmp_path = Path(tmp_file.name)
        tmp_file.close()
        return tmp_path

    def _find_downloaded_file(
        self, base_path: Path, info: dict[str, Any]
    ) -> Path:
        ext = info.get("ext", "mp4")
        expected = base_path.with_suffix(f".{ext}")
        if expected.exists():
            return expected

        parent = base_path.parent
        stem = Path(base_path.name).stem
        for f in parent.iterdir():
            if f.name.startswith(stem) and f.suffix and f.suffix != ".tmp":
                return f

        raise ValueError(f"Downloaded file not found at {expected}")

    def _cleanup(self, tmp_path: Path) -> None:
        parent = tmp_path.parent
        stem = Path(tmp_path.name).stem
        try:
            for f in parent.iterdir():
                if f.name.startswith(stem):
                    try:
                        f.unlink()
                    except OSError:
                        pass
        except OSError:
            pass