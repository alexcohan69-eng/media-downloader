from __future__ import annotations

import logging
import mimetypes
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator

import httpx
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
class DownloadedItem:
    file_path: Path
    filename: str
    media_type: str
    file_size: int


@dataclass
class DownloadResult:
    stream: Generator[bytes, None, None]
    filename: str
    media_type: str
    file_size: int
    tmp_path: Path
    file_path: Path
    title: str | None = None
    items: list[DownloadedItem] = field(default_factory=list)
    is_album: bool = False


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
                is_alb = len(page.images) > 1
                return MediaInfo(
                    title=page.title,
                    type="album" if is_alb else page.media_type,
                    thumbnail=page.images[0],
                    uploader=page.uploader,
                    extractor="page-image",
                    webpage_url=url,
                    views=page.views,
                    likes=page.likes,
                    reposts=page.reposts,
                    comments=page.comments,
                    formats=formats,
                    is_album=is_alb,
                    media_count=len(page.images),
                )
            raise

        # Check if yt-dlp extracted playlist / multi-item gallery
        entries = raw.get("entries")
        if entries:
            entries_list = [e for e in entries if e]
            if len(entries_list) > 1:
                formats = []
                for e in entries_list:
                    entry_formats = sanitize_formats(e.get("formats", []))
                    formats.extend([FormatInfo(**f) for f in entry_formats[:2]])
                return MediaInfo(
                    title=raw.get("title") or (entries_list[0].get("title") if entries_list else None),
                    type="album",
                    duration=raw.get("duration"),
                    thumbnail=raw.get("thumbnail") or (entries_list[0].get("thumbnail") if entries_list else None),
                    uploader=raw.get("uploader") or raw.get("channel"),
                    extractor=raw.get("extractor"),
                    webpage_url=raw.get("webpage_url") or url,
                    views=raw.get("view_count"),
                    likes=raw.get("like_count"),
                    reposts=raw.get("repost_count"),
                    comments=raw.get("comment_count"),
                    formats=formats,
                    is_album=True,
                    media_count=len(entries_list),
                )

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
            is_album=False,
            media_count=1,
        )

    def download(
        self,
        url: str,
        format_str: str = "best",
        audio_only: bool = False,
        title: str | None = None,
    ) -> DownloadResult:
        settings.temp_path.mkdir(parents=True, exist_ok=True)
        batch_dir = Path(tempfile.mkdtemp(dir=settings.temp_path, prefix="dl_batch_"))
        outtmpl = str(batch_dir / "%(playlist_index&{:02d}_|)s%(title).80B.%(ext)s")

        opts = self._build_opts(
            format_str=format_str,
            audio_only=audio_only,
            outtmpl=outtmpl,
        )

        try:
            info: dict[str, Any] = {}
            fallback_page: PageImages | None = None
            try:
                info = self._extract(url, download=True, opts=opts)
            except Exception:
                page = self._page_images(url)
                if not page or not page.images:
                    raise
                logger.info("Using page-image fallback for %s (%d media items)", url, len(page.images))
                fallback_page = page
                with httpx.Client(timeout=60, follow_redirects=True) as client:
                    for idx, img_url in enumerate(page.images):
                        ext = ext_from_image_url(img_url)
                        prefix = f"{idx+1:02d}_" if len(page.images) > 1 else ""
                        item_file = batch_dir / f"{prefix}media_{idx+1}.{ext}"
                        try:
                            resp = client.get(img_url)
                            if resp.status_code == 200 and resp.content:
                                item_file.write_bytes(resp.content)
                        except Exception as e:
                            logger.warning("Could not download media item %s: %s", img_url, e)

            # Discover all valid downloaded files
            downloaded_files = sorted(
                [
                    f for f in batch_dir.iterdir()
                    if f.is_file()
                    and not f.name.endswith(".tmp")
                    and not f.name.endswith(".part")
                    and not f.name.endswith(".ytdl")
                ]
            )

            if not downloaded_files:
                raise ValueError(f"No media files were downloaded for: {url}")

            # Check individual extensions and fix unknown extensions
            for f in downloaded_files:
                if f.suffix.lstrip(".") in ("", "unknown_video", "unknown_audio", "html"):
                    correct_ext = ext_from_image_url(url)
                    new_path = f.with_suffix(f".{correct_ext}")
                    os.replace(f, new_path)

            downloaded_files = sorted(
                [
                    f for f in batch_dir.iterdir()
                    if f.is_file()
                    and not f.name.endswith(".tmp")
                    and not f.name.endswith(".part")
                ]
            )

            resolved_title = (
                title
                or (fallback_page.title if fallback_page else None)
                or info.get("title")
                or info.get("description")
                or "download"
            )
            raw_title = resolved_title if resolved_title != "download" else None
            safe_stem = "".join(
                c if (c.isalnum() and ord(c) < 128) or c in "._-" else "_" for c in resolved_title
            )[:200].strip("._")
            if not any(ch.isalnum() for ch in safe_stem):
                tail = url.rstrip("/").split("?")[0].split("/")[-1]
                safe_stem = f"media_{re.sub(r'[^A-Za-z0-9._-]', '_', tail)}"

            # 1. Single Media Item
            if len(downloaded_files) == 1:
                single_file = downloaded_files[0]
                file_size = single_file.stat().st_size
                if file_size > settings.max_file_size_bytes:
                    raise ValueError(f"File too large: {file_size} bytes (max: {settings.max_file_size_bytes})")

                ext = single_file.suffix.lstrip(".") or "mp4"
                media_type = mimetypes.guess_type(single_file.name)[0] or "application/octet-stream"
                final_filename = f"{safe_stem}.{ext}"

                items = [
                    DownloadedItem(
                        file_path=single_file,
                        filename=final_filename,
                        media_type=media_type,
                        file_size=file_size,
                    )
                ]

                return DownloadResult(
                    stream=self._stream_and_cleanup(single_file, batch_dir),
                    filename=final_filename,
                    media_type=media_type,
                    file_size=file_size,
                    tmp_path=batch_dir,
                    file_path=single_file,
                    title=raw_title,
                    items=items,
                    is_album=False,
                )

            # 2. Album / Multiple Media Items
            total_size = sum(f.stat().st_size for f in downloaded_files)
            if total_size > settings.max_file_size_bytes:
                raise ValueError(
                    f"Album total size ({total_size / (1024*1024):.1f}MB) exceeds limit "
                    f"({settings.max_file_size_mb}MB)"
                )

            items = []
            for f in downloaded_files:
                mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                items.append(
                    DownloadedItem(
                        file_path=f,
                        filename=f.name,
                        media_type=mime,
                        file_size=f.stat().st_size,
                    )
                )

            # Create ZIP bundle for album downloads
            zip_filename = f"{safe_stem}_album.zip"
            zip_path = batch_dir.parent / f"{batch_dir.name}.zip"
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for f in downloaded_files:
                    zf.write(f, arcname=f.name)

            zip_size = zip_path.stat().st_size
            return DownloadResult(
                stream=self._stream_and_cleanup(zip_path, batch_dir),
                filename=zip_filename,
                media_type="application/zip",
                file_size=zip_size,
                tmp_path=batch_dir,
                file_path=zip_path,
                title=raw_title,
                items=items,
                is_album=True,
            )

        except Exception as e:
            self._cleanup(batch_dir)
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
        self, stream_file: Path, batch_dir: Path
    ) -> Generator[bytes, None, None]:
        try:
            with open(stream_file, "rb") as f:
                while chunk := f.read(CHUNK_SIZE):
                    yield chunk
        finally:
            self._cleanup(batch_dir)
            if stream_file != batch_dir and stream_file.exists():
                try:
                    stream_file.unlink(missing_ok=True)
                except Exception:
                    pass

    def _cleanup(self, path: Path | None) -> None:
        if path is None:
            return
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.is_file():
                path.unlink(missing_ok=True)
            zip_candidate = path.parent / f"{path.name}.zip"
            if zip_candidate.exists():
                zip_candidate.unlink(missing_ok=True)
        except Exception:
            pass
