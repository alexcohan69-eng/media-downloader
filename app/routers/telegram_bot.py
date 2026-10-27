from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Request

from app.config import settings
from app.services.bot_service import process_update, setup_webhook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/telegram", tags=["telegram"])


@router.post("/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receives incoming updates from Telegram Bot API.
    Returns 200 OK immediately and processes the update asynchronously.
    """
    try:
        update: dict[str, Any] = await request.json()
        background_tasks.add_task(process_update, update)
    except Exception as e:
        logger.error("Failed to parse telegram webhook update: %s", e)
    return {"ok": True}


@router.get("/status")
async def telegram_bot_status():
    """
    Returns current Telegram Bot and Webhook configuration status.
    """
    token = settings.telegram_bot_token
    if not token:
        return {"configured": False, "error": "TELEGRAM_BOT_TOKEN not configured"}

    async with httpx.AsyncClient(timeout=15) as client:
        bot_res = await client.get(f"https://api.telegram.org/bot{token}/getMe")
        webhook_res = await client.get(f"https://api.telegram.org/bot{token}/getWebhookInfo")

    return {
        "configured": True,
        "bot": bot_res.json().get("result", {}),
        "webhook": webhook_res.json().get("result", {}),
        "public_url": settings.effective_public_url,
    }


@router.post("/set-webhook")
async def trigger_set_webhook():
    """
    Manually activates the Telegram webhook to the effective public URL.
    """
    public_base = settings.effective_public_url
    if not public_base:
        return {"ok": False, "error": "No effective public URL available"}

    webhook_url = f"{public_base}/api/v1/telegram/webhook"
    success = await setup_webhook(webhook_url)
    return {"ok": success, "webhook_url": webhook_url}
