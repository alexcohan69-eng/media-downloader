from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    api_keys: list[str] = ["changeme"]
    cookie_browser: str = "chrome"
    cookie_file: str | None = None
    default_format: str = "best"
    max_file_size_mb: int = 2048
    temp_dir: str = "/tmp/media-downloader"
    host: str = "0.0.0.0"
    port: int = 8000
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_local_api_url: str | None = None
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_session_string: str | None = None
    cors_origins: list[str] = ["*"]
    public_url: str | None = None
    render_external_url: str | None = None
    telegram_webhook_secret: str | None = None

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @property
    def effective_public_url(self) -> str | None:
        url = self.public_url or self.render_external_url
        if url:
            return url.rstrip("/")
        return "https://media-downloader-tnew.onrender.com"

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def temp_path(self) -> Path:
        path = Path(self.temp_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()