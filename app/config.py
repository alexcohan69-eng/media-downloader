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
    cors_origins: list[str] = ["*"]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def temp_path(self) -> Path:
        path = Path(self.temp_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()