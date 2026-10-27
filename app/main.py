from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import settings
from app.routers.download import router as download_router
from app.routers.telegram_bot import router as telegram_router
from app.services.bot_service import run_polling_worker, setup_webhook
from app.services.telegram import TelegramSender

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    bot_task = None
    if settings.telegram_bot_token:
        if settings.effective_public_url:
            webhook_url = f"{settings.effective_public_url}/api/v1/telegram/webhook"
            logger.info("Setting up Telegram webhook: %s", webhook_url)
            try:
                await setup_webhook(webhook_url)
            except Exception as e:
                logger.warning("Failed setting webhook on startup: %s", e)
        else:
            bot_task = asyncio.create_task(run_polling_worker())

    yield

    if bot_task:
        bot_task.cancel()
    await TelegramSender.close_userbot_client()


app = FastAPI(
    title="Universal Media Downloader & Personal Mau Saver Bot API",
    description="Download any media from any site. Just provide a URL.",
    version="2.0.0",
    lifespan=lifespan,
)


def _cors_allow_credentials() -> bool:
    return "*" not in settings.cors_origins


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=_cors_allow_credentials(),
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "X-Telegram-Sent",
        "X-Telegram-Link",
        "X-Telegram-Message-Id",
        "X-Telegram-Sender",
        "X-Telegram-Album",
        "X-Telegram-Media-Count",
    ],
)

app.include_router(download_router)
app.include_router(telegram_router)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/api/v1/config")
async def get_config():
    default_key = settings.api_keys[0] if settings.api_keys else "test-secret-key-123"
    return {"default_api_key": default_key}


@app.get("/playground")
async def playground():
    return FileResponse(
        path=str(__import__("pathlib").Path(__file__).parent / "static" / "playground.html"),
        media_type="text/html",
    )


@app.get("/")
async def root():
    return FileResponse(
        path=str(__import__("pathlib").Path(__file__).parent / "static" / "playground.html"),
        media_type="text/html",
    )
