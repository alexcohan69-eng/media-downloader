from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

CLOUD_API = "https://api.telegram.org"
CLOUD_MAX_BYTES = 50 * 1024 * 1024  # 50MB
LOCAL_MAX_BYTES = 2000 * 1024 * 1024  # 2000MB
USERBOT_MAX_BYTES = 2000 * 1024 * 1024  # 2000MB (standard MTProto)


class TelegramSender:
    _userbot_client: Any = None
    _userbot_lock: asyncio.Lock | None = None

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        local_api_url: str | None = None,
        api_id: int | None = None,
        api_hash: str | None = None,
        session_string: str | None = None,
    ):
        self.token = token or settings.telegram_bot_token
        self.chat_id = chat_id or settings.telegram_chat_id
        self.local_api_url = (
            local_api_url
            if local_api_url is not None
            else settings.telegram_local_api_url
        )
        self.api_id = api_id or settings.telegram_api_id
        self.api_hash = api_hash or settings.telegram_api_hash
        self.session_string = session_string or settings.telegram_session_string

    @property
    def api_base(self) -> str:
        return (self.local_api_url or CLOUD_API).rstrip("/")

    @property
    def uses_local_server(self) -> bool:
        return bool(self.local_api_url)

    @property
    def can_use_bot(self) -> bool:
        return bool(self.token and self.chat_id)

    @property
    def can_use_userbot(self) -> bool:
        return bool(self.api_id and self.api_hash and self.session_string and self.chat_id)

    @classmethod
    async def get_userbot_client(
        cls,
        api_id: int,
        api_hash: str,
        session_string: str,
    ):
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        if cls._userbot_lock is None:
            cls._userbot_lock = asyncio.Lock()

        async with cls._userbot_lock:
            if cls._userbot_client is None or not cls._userbot_client.is_connected():
                client = TelegramClient(StringSession(session_string), api_id, api_hash)
                await client.connect()
                cls._userbot_client = client
            return cls._userbot_client

    @classmethod
    async def close_userbot_client(cls):
        if cls._userbot_client is not None:
            try:
                if cls._userbot_client.is_connected():
                    await cls._userbot_client.disconnect()
            except Exception as e:
                logger.warning("Error disconnecting userbot client: %s", e)
            finally:
                cls._userbot_client = None

    async def send_media(
        self,
        file_path: Path,
        filename: str,
        caption: str | None = None,
        use_userbot: bool | None = None,
        items: list[Path] | None = None,
    ) -> dict:
        # Multiple media / album handling
        if items and len(items) > 1:
            return await self._send_album(items, caption=caption, use_userbot=use_userbot)

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        size = file_path.stat().st_size

        # 1. Explicit userbot requested
        if use_userbot is True:
            if not self.can_use_userbot:
                raise ValueError(
                    "Telegram userbot requested, but credentials not configured "
                    "(set TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION_STRING, TELEGRAM_CHAT_ID)"
                )
            return await self._send_via_userbot(file_path, filename, caption)

        # 2. Explicit bot requested
        if use_userbot is False:
            if not self.can_use_bot:
                raise ValueError("Telegram Bot not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)")
            if not self.uses_local_server and size > CLOUD_MAX_BYTES:
                raise ValueError(
                    f"File is {size / (1024 * 1024):.1f}MB, exceeding the 50MB Bot API limit. "
                    "Use userbot or set use_userbot=True to send files over 50MB."
                )
            return await self._send_via_bot(file_path, filename, caption)

        # 3. Auto mode: File > 50MB automatically uses userbot
        if size > CLOUD_MAX_BYTES and not self.uses_local_server:
            if not self.can_use_userbot:
                raise ValueError(
                    f"File is {size / (1024 * 1024):.1f}MB (>50MB Bot API limit). "
                    "Telegram userbot credentials are required to send files over 50MB "
                    "(set TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION_STRING)."
                )
            logger.info("File size (%.1f MB > 50MB) auto-routed to userbot.", size / (1024 * 1024))
            return await self._send_via_userbot(file_path, filename, caption)

        # 4. Auto mode: File <= 50MB uses Bot API with userbot fallback
        if self.can_use_bot:
            try:
                return await self._send_via_bot(file_path, filename, caption)
            except Exception as e:
                if self.can_use_userbot:
                    logger.warning("Bot upload failed (%s). Falling back to userbot...", e)
                    return await self._send_via_userbot(file_path, filename, caption)
                raise
        elif self.can_use_userbot:
            return await self._send_via_userbot(file_path, filename, caption)
        else:
            raise ValueError(
                "Telegram not configured: configure TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID "
                "or userbot credentials (TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION_STRING)."
            )

    async def _send_album(
        self,
        items: list[Path],
        caption: str | None = None,
        use_userbot: bool | None = None,
    ) -> dict:
        total_size = sum(f.stat().st_size for f in items if f.exists())
        any_over_50mb = any(f.stat().st_size > CLOUD_MAX_BYTES for f in items if f.exists())

        if use_userbot is True:
            if not self.can_use_userbot:
                raise ValueError("Telegram userbot requested for album, but userbot credentials are not configured")
            return await self._send_album_via_userbot(items, caption)

        if use_userbot is False:
            if any_over_50mb or (not self.uses_local_server and total_size > CLOUD_MAX_BYTES):
                raise ValueError("Album files exceed 50MB Bot API limit. Use userbot for larger albums.")
            return await self._send_album_via_bot(items, caption)

        # Auto mode
        if any_over_50mb or (not self.uses_local_server and total_size > CLOUD_MAX_BYTES):
            if self.can_use_userbot:
                logger.info("Album size (%.1f MB) auto-routed to userbot.", total_size / (1024 * 1024))
                return await self._send_album_via_userbot(items, caption)
            raise ValueError(
                f"Album total size ({total_size / (1024 * 1024):.1f}MB) exceeds 50MB Bot API limit, "
                "and userbot credentials are not configured."
            )

        if self.can_use_bot:
            try:
                return await self._send_album_via_bot(items, caption)
            except Exception as e:
                if self.can_use_userbot:
                    logger.warning("Bot album send failed (%s). Falling back to userbot...", e)
                    return await self._send_album_via_userbot(items, caption)
                raise
        elif self.can_use_userbot:
            return await self._send_album_via_userbot(items, caption)
        else:
            raise ValueError("Telegram not configured for album sending.")

    async def _send_album_via_userbot(
        self,
        items: list[Path],
        caption: str | None = None,
    ) -> dict:
        total_size = sum(f.stat().st_size for f in items if f.exists())
        if total_size > USERBOT_MAX_BYTES:
            raise ValueError(f"Album total size ({total_size / (1024*1024):.1f}MB) exceeds userbot limit (2000MB)")

        if not self.api_id or not self.api_hash or not self.session_string:
            raise ValueError("Telegram userbot credentials incomplete: require api_id, api_hash, session_string")

        client = await self.get_userbot_client(
            int(self.api_id),
            str(self.api_hash),
            str(self.session_string),
        )

        target = self.chat_id
        if isinstance(target, str) and (target.isdigit() or (target.startswith("-") and target[1:].isdigit())):
            target = int(target)

        try:
            entity = await client.get_input_entity(target)
        except Exception:
            entity = await client.get_entity(target)

        cap = (caption or "").strip()[:1024]
        chunks = [items[i:i + 10] for i in range(0, len(items), 10)]
        all_message_ids: list[int] = []

        for idx, chunk in enumerate(chunks):
            chunk_caption = cap if idx == 0 else ""
            res = await client.send_file(
                entity,
                file=[str(f) for f in chunk],
                caption=chunk_caption,
            )
            if isinstance(res, list):
                all_message_ids.extend([m.id for m in res])
            else:
                all_message_ids.append(res.id)

        first_id = all_message_ids[0] if all_message_ids else 0
        return {
            "message_id": first_id,
            "message_ids": all_message_ids,
            "chat_id": self.chat_id,
            "link": self._chat_link(first_id),
            "sender": "userbot",
            "is_album": True,
            "media_count": len(items),
        }

    async def _send_album_via_bot(
        self,
        items: list[Path],
        caption: str | None = None,
    ) -> dict:
        total_size = sum(f.stat().st_size for f in items if f.exists())
        if not self.uses_local_server and total_size > CLOUD_MAX_BYTES:
            raise ValueError(f"Album total size ({total_size / (1024*1024):.1f}MB) exceeds 50MB Bot API limit")

        cap = (caption or "").strip()[:1024]
        chunks = [items[i:i + 10] for i in range(0, len(items), 10)]
        all_message_ids: list[int] = []

        async with httpx.AsyncClient(timeout=900) as client:
            for idx, chunk in enumerate(chunks):
                media = []
                file_handles = []
                files = {}
                try:
                    for i, file_path in enumerate(chunk):
                        mime, _ = mimetypes.guess_type(file_path.name)
                        mime = mime or "application/octet-stream"
                        mtype = (
                            "video"
                            if mime.startswith("video/")
                            else "photo"
                            if mime.startswith("image/")
                            else "document"
                        )
                        attach_name = f"file_{i}"
                        entry: dict[str, Any] = {
                            "type": mtype,
                            "media": f"attach://{attach_name}",
                        }
                        if i == 0 and idx == 0 and cap:
                            entry["caption"] = cap
                        media.append(entry)
                        fp = open(file_path, "rb")
                        file_handles.append(fp)
                        files[attach_name] = (file_path.name, fp, mime)

                    url = f"{self.api_base}/bot{self.token}/sendMediaGroup"
                    payload = {
                        "chat_id": self.chat_id,
                        "media": json.dumps(media),
                    }
                    resp = await client.post(url, data=payload, files=files)
                    data = resp.json()
                    if not data.get("ok"):
                        raise ValueError(f"Telegram Bot API sendMediaGroup failed: {data.get('description', resp.text)}")

                    chunk_ids = [m["message_id"] for m in data["result"]]
                    all_message_ids.extend(chunk_ids)
                finally:
                    for fp in file_handles:
                        try:
                            fp.close()
                        except Exception:
                            pass

        first_id = all_message_ids[0] if all_message_ids else 0
        return {
            "message_id": first_id,
            "message_ids": all_message_ids,
            "chat_id": self.chat_id,
            "link": self._chat_link(first_id),
            "sender": "bot",
            "is_album": True,
            "media_count": len(items),
        }

    async def _send_via_bot(
        self,
        file_path: Path,
        filename: str,
        caption: str | None = None,
    ) -> dict:
        size = file_path.stat().st_size
        if not self.uses_local_server and size > CLOUD_MAX_BYTES:
            raise ValueError(
                f"File is {size / (1024 * 1024):.1f}MB, but the Telegram cloud Bot API "
                "only accepts files up to 50MB."
            )
        if self.uses_local_server and size > LOCAL_MAX_BYTES:
            raise ValueError(
                f"File is {size / (1024 * 1024):.1f}MB, exceeding the 2000MB local Bot API server limit."
            )

        mime, _ = mimetypes.guess_type(filename)
        mime = mime or "application/octet-stream"
        method, field = _method_for(mime)
        cap = (caption or "").strip()[:1024]

        url = f"{self.api_base}/bot{self.token}/{method}"
        payload: dict = {"chat_id": self.chat_id}
        if cap:
            payload["caption"] = cap

        async with httpx.AsyncClient(timeout=900) as client:
            with open(file_path, "rb") as f:
                files = {field: (filename, f, mime)}
                resp = await client.post(url, data=payload, files=files)

        try:
            data = resp.json()
        except ValueError:
            raise ValueError(f"Telegram API bad response (HTTP {resp.status_code})")

        if not data.get("ok"):
            raise ValueError(f"Telegram send failed: {data.get('description', resp.text)}")

        message_id = data["result"]["message_id"]
        return {
            "message_id": message_id,
            "chat_id": self.chat_id,
            "link": self._chat_link(message_id),
            "sender": "bot",
            "is_album": False,
            "media_count": 1,
        }

    async def _send_via_userbot(
        self,
        file_path: Path,
        filename: str,
        caption: str | None = None,
    ) -> dict:
        size = file_path.stat().st_size
        if size > USERBOT_MAX_BYTES:
            raise ValueError(
                f"File is {size / (1024 * 1024):.1f}MB, exceeding Telegram userbot limit (2000MB)."
            )

        if not self.api_id or not self.api_hash or not self.session_string:
            raise ValueError(
                "Telegram userbot credentials incomplete: require api_id, api_hash, session_string"
            )

        client = await self.get_userbot_client(
            int(self.api_id),
            str(self.api_hash),
            str(self.session_string),
        )

        target = self.chat_id
        if isinstance(target, str) and (target.isdigit() or (target.startswith("-") and target[1:].isdigit())):
            target = int(target)

        try:
            entity = await client.get_input_entity(target)
        except Exception:
            entity = await client.get_entity(target)

        mime, _ = mimetypes.guess_type(filename)
        mime = mime or "application/octet-stream"
        cap = (caption or "").strip()[:1024]

        is_video = bool(mime and mime.startswith("video/"))
        is_audio = bool(mime and mime.startswith("audio/"))
        is_image = bool(mime and mime.startswith("image/"))

        msg = await client.send_file(
            entity,
            file=str(file_path),
            caption=cap,
            force_document=not (is_video or is_audio or is_image),
            supports_streaming=is_video,
        )

        message_id = msg.id
        return {
            "message_id": message_id,
            "chat_id": self.chat_id,
            "link": self._chat_link(message_id),
            "sender": "userbot",
            "is_album": False,
            "media_count": 1,
        }

    def _chat_link(self, message_id: int) -> str:
        cid = str(self.chat_id)
        if cid.startswith("-100"):
            public = cid[4:]
        else:
            public = cid.lstrip("-")
        return f"https://t.me/c/{public}/{message_id}"


def _method_for(mime: str) -> tuple[str, str]:
    if mime.startswith("image/"):
        return "sendPhoto", "photo"
    if mime.startswith("video/"):
        return "sendVideo", "video"
    if mime.startswith("audio/"):
        return "sendAudio", "audio"
    return "sendDocument", "document"
