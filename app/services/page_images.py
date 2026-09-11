from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import yt_dlp

from app.services.cookies import build_ydl_opts, is_cookie_error

logger = logging.getLogger(__name__)

TWITTER_MEDIA_RE = re.compile(r"https://pbs\.twimg\.com/media/[^\"\s\\]+")
OG_IMAGE_RE = re.compile(
    r'property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', re.IGNORECASE
)
TITLE_RE = re.compile(r"<title>([^<]+)</title>", re.IGNORECASE)
TWITTER_STATUS_RE = re.compile(r"(?:twitter\.com|x\.com)/[^/]+/status/(\d+)")


@dataclass
class PageImages:
    images: list[str] = field(default_factory=list)
    title: str | None = None
    media_type: str = "image"
    resolutions: list[tuple[int, int] | None] = field(default_factory=list)
    uploader: str | None = None
    views: int | None = None
    likes: int | None = None
    reposts: int | None = None
    comments: int | None = None


def _full_res_twitter_url(url: str) -> str:
    if "pbs.twimg.com/media/" in url and "name=" in url:
        base = url.split("&name=")[0]
        sep = "&" if "?" in base else "?"
        return base + sep + "name=orig"
    return url


def _fetch_text(url: str, opts: dict[str, Any]) -> str | None:
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            resp = ydl.urlopen(url)
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        if is_cookie_error(e) and "cookiesfrombrowser" in opts and "cookiefile" not in opts:
            retry_opts = {
                k: v
                for k, v in opts.items()
                if k not in ("cookiesfrombrowser", "cookiefile")
            }
            retry_opts.pop("format", None)
            retry_opts.pop("outtmpl", None)
            return _fetch_text(url, retry_opts)
        logger.warning("Could not fetch %s: %s", url, e)
        return None


def _base_fetch_opts(cookie_file: str | None) -> dict[str, Any]:
    opts = build_ydl_opts(cookie_file=cookie_file)
    opts.pop("format", None)
    opts.pop("outtmpl", None)
    opts.pop("skip_download", None)
    return opts


def resolve_twitter_media(url: str, cookie_file: str | None = None) -> PageImages | None:
    m = TWITTER_STATUS_RE.search(url)
    if not m:
        return None
    tweet_id = m.group(1)
    opts = _base_fetch_opts(cookie_file)

    for api in (
        f"https://api.fxtwitter.com/status/{tweet_id}",
        f"https://api.vxtwitter.com/Twitter/status/{tweet_id}",
    ):
        try:
            text = _fetch_text(api, opts)
            if not text:
                continue
            data = json.loads(text)
            tweet = data.get("tweet")
            if not tweet:
                continue

            media_all = (tweet.get("media") or {}).get("all") or []
            images: list[str] = []
            resolutions: list[tuple[int, int] | None] = []
            media_type = "image"
            for mm in media_all:
                mtype = mm.get("type") or ""
                if mtype == "video":
                    media_type = "video"
                elif mtype == "animated_gif":
                    media_type = "video"
                url_candidate = mm.get("url")
                if not url_candidate:
                    continue
                images.append(url_candidate)
                w, h = (
                    mm.get("width") or (mm.get("size") or {}).get("width"),
                    mm.get("height") or (mm.get("size") or {}).get("height"),
                )
                resolutions.append((w, h) if w and h else None)

            if not images:
                continue

            author = tweet.get("author") or {}
            return PageImages(
                images=images,
                title=tweet.get("text") or None,
                media_type=media_type,
                resolutions=resolutions,
                uploader=(author.get("name") or author.get("screen_name") or None),
                views=tweet.get("views"),
                likes=tweet.get("likes"),
                reposts=tweet.get("retweets"),
                comments=tweet.get("replies"),
            )
        except Exception as e:
            logger.warning("Twitter resolver %s failed: %s", api, e)

    return None


def extract_page_images(
    url: str, max_images: int = 10, cookie_file: str | None = None
) -> PageImages:
    opts = _base_fetch_opts(cookie_file)

    html = _fetch_text(url, opts)
    if not html:
        return PageImages()

    images: list[str] = []
    for match in re.finditer(TWITTER_MEDIA_RE, html):
        candidate = _full_res_twitter_url(match.group(0).replace("&amp;", "&"))
        if candidate not in images:
            images.append(candidate)
        if len(images) >= max_images:
            break

    og = OG_IMAGE_RE.search(html)
    if og:
        candidate = og.group(1).replace("&amp;", "&")
        if candidate not in images:
            images.append(candidate)
        if len(images) >= max_images:
            images = images[:max_images]

    title = None
    m = TITLE_RE.search(html)
    if m:
        title = m.group(1).strip()

    return PageImages(images=images, title=title)


def ext_from_image_url(url: str) -> str:
    m = re.search(r"[?&]format=([a-z0-9]+)", url, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    m = re.search(r"\.([a-z0-9]{3,4})(?:[?#]|$)", url, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    return "jpg"