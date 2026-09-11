from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

CLOUD_API = "https://api.telegram.org"
CLOUD_MAX_BYTES = 50 * 1024 * 1024
LOCAL_MAX_BYTES = 2000 * 1024 * 1024


class TelegramSender:
    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        local_api_url: str | None = None,
    ):
        self.token = token or settings.telegram_bot_token
        self.chat_id = chat_id or settings.telegram_chat_id
        self.local_api_url = (
            local_api_url
            if local_api_url is not None
            else settings.telegram_local_api_url
        )

    @property
    def api_base(self) -> str:
        return (self.local_api_url or CLOUD_API).rstrip("/")

    @property
    def uses_local_server(self) -> bool:
        return bool(self.local_api_url)

    def send_media(
        self,
        file_path: Path,
        filename: str,
        caption: str | None = None,
    ) -> dict:
        if not self.token or not self.chat_id:
            raise ValueError(
                "Telegram not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
            )

        size = file_path.stat().st_size
        if not self.uses_local_server and size > CLOUD_MAX_BYTES:
            raise ValueError(
                f"File is {size / (1024 * 1024):.1f}MB, but the Telegram cloud Bot API "
                "only accepts files up to 50MB. Set TELEGRAM_LOCAL_API_URL to a local "
                "Telegram Bot API server to send files up to 2000MB."
            )
        if self.uses_local_server and size > LOCAL_MAX_BYTES:
            raise ValueError(
                f"File is {size / (1024 * 1024):.1f}MB, exceeding the 2000MB local "
                "Bot API server limit."
            )

        mime, _ = mimetypes.guess_type(filename)
        mime = mime or "application/octet-stream"
        method, field = _method_for(mime)
        cap = (caption or "").strip()[:1024]

        url = f"{self.api_base}/bot{self.token}/{method}"
        payload: dict = {"chat_id": self.chat_id}
        if cap:
            payload["caption"] = cap

        with open(file_path, "rb") as f:
            files = {field: (filename, f, mime)}
            resp = httpx.post(url, data=payload, files=files, timeout=900)

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