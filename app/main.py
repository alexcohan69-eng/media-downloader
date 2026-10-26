from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import settings
from app.routers.download import router as download_router
from app.services.telegram import TelegramSender


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await TelegramSender.close_userbot_client()


app = FastAPI(
    title="Universal Media Downloader API",
    description="Download any media from any site. Just provide a URL.",
    version="1.0.0",
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
    ],
)

app.include_router(download_router)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


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
