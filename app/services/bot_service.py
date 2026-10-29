from __future__ import annotations

import asyncio
import datetime
import html
import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.services.downloader import MediaDownloader
from app.services.telegram import TelegramSender
from app.services.timestamp import update_image_timestamp
from app.services.translator import detect_and_translate

logger = logging.getLogger(__name__)

URL_PATTERN = re.compile(r"(https?://[^\s<>\"\'\)]+)", re.IGNORECASE)

BOT_USERNAME = "MissZeba_Auto_Save_Bot"


def get_start_message() -> tuple[str, dict]:
    text = (
        "👋 <b>Welcome to Zeba Saver!</b>\n\n"
        "Send me any media link from <b>TikTok, Instagram, YouTube, Twitter/X, Facebook, "
        "Reddit, Pinterest, SoundCloud</b> and I will download and send it to you instantly!\n\n"
        "✨ <b>Features:</b>\n"
        "• 🎬 <b>High Quality Video & Audio:</b> Full resolution downloads\n"
        "• 🖼️ <b>Carousels & Albums:</b> All photos/videos downloaded together\n"
        "• 📸 <b>Timestamp Editor:</b> Send any photo to update/add camera timestamp\n"
        "• 🌐 <b>Auto-Translation:</b> Foreign titles translated to English\n"
        "• ⚡ <b>Large Files:</b> Up to 2GB supported automatically\n"
        "• 👥 <b>Works in Groups:</b> Add me to any group chat!\n\n"
        "<i>Just drop a link or photo below to get started!</i>"
    )
    markup = {
        "inline_keyboard": [
            [
                {
                    "text": "➕ Add to Group",
                    "url": f"https://t.me/{BOT_USERNAME}?startgroup=true",
                }
            ],
            [
                {
                    "text": "🌐 Web Playground",
                    "url": "https://media-downloader-tnew.onrender.com/playground",
                }
            ],
        ]
    }
    return text, markup


def get_help_message() -> tuple[str, dict]:
    text = (
        "📖 <b>How to use Zeba Saver:</b>\n\n"
        "<b>Media Downloader:</b>\n"
        "• Paste any link from TikTok, Instagram, YouTube, X, Reddit, etc.\n"
        "• The bot will automatically download and deliver the media back to you!\n\n"
        "<b>Photo Timestamp Editor:</b>\n"
        "• Send any photo to update or add an authentic smartphone camera timestamp.\n"
        "• By default it uses current date & time, or write your custom date in the caption (e.g. <code>Oct 30, 2026, 02:00</code>).\n\n"
        "<b>In Telegram Groups:</b>\n"
        "• Add @MissZeba_Auto_Save_Bot to your group.\n"
        "• Whenever any member shares a link, the bot replies with the media directly."
    )
    markup = {
        "inline_keyboard": [
            [
                {
                    "text": "➕ Add to Group",
                    "url": f"https://t.me/{BOT_USERNAME}?startgroup=true",
                }
            ]
        ]
    }
    return text, markup


async def process_update(update: dict[str, Any]) -> None:
    sender = TelegramSender()

    # 1. Handle Callback Queries (e.g. Extract Audio button)
    if "callback_query" in update:
        cb = update["callback_query"]
        cb_id = cb.get("id")
        data = cb.get("data", "")
        msg = cb.get("message")
        chat_id = msg.get("chat", {}).get("id") if msg else None
        msg_id = msg.get("message_id") if msg else None

        if cb_id:
            await sender.answer_callback_query(cb_id, "🎵 Extracting audio...")

        if data.startswith("aud:") and chat_id:
            audio_url = data[4:]
            status_msg = await sender.send_message(
                chat_id, "⏳ <i>Extracting audio track (MP3)...</i>", reply_to_message_id=msg_id
            )
            status_id = status_msg.get("result", {}).get("message_id")
            await sender.send_chat_action(chat_id, "record_voice")

            downloader = MediaDownloader()
            result = None
            try:
                result = await asyncio.to_thread(downloader.download, audio_url, audio_only=True)
                caption = result.filename
                translated_caption, _ = detect_and_translate(caption)
                await sender.send_media(
                    file_path=result.file_path,
                    filename=result.filename,
                    caption=translated_caption,
                    target_chat_id=chat_id,
                    reply_to_message_id=msg_id,
                )
                if status_id:
                    await sender.delete_message(chat_id, status_id)
            except Exception as e:
                logger.error("Audio extraction failed: %s", e)
                if status_id:
                    await sender.edit_message_text(
                        chat_id, status_id, f"❌ <b>Audio extraction failed:</b> {html.escape(str(e))}"
                    )
            finally:
                if result and result.tmp_path:
                    downloader._cleanup(result.tmp_path)
        return

    # 2. Handle Messages
    message = update.get("message")
    if not message:
        return

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    chat_type = chat.get("type", "private")
    msg_id = message.get("message_id")
    text = message.get("text") or message.get("caption") or ""

    if not chat_id:
        return

    # Check if a photo or image document was sent -> Timestamp Editor
    is_photo = bool(message.get("photo"))
    is_image_doc = bool(message.get("document") and message.get("document", {}).get("mime_type", "").startswith("image/"))
    if is_photo or is_image_doc:
        await _handle_photo_timestamp(sender, chat_id, msg_id, message)
        return

    if not text:
        return

    text_clean = text.strip()

    # /start command
    if text_clean.startswith("/start"):
        start_text, start_markup = get_start_message()
        await sender.send_message(
            chat_id, start_text, reply_to_message_id=msg_id, reply_markup=start_markup
        )
        return

    # /help command
    if text_clean.startswith("/help"):
        help_text, help_markup = get_help_message()
        await sender.send_message(
            chat_id, help_text, reply_to_message_id=msg_id, reply_markup=help_markup
        )
        return

    # Check for URLs in message
    urls = URL_PATTERN.findall(text)
    if not urls:
        # If in private chat and not a command or URL, give friendly hint
        if chat_type == "private":
            await sender.send_message(
                chat_id,
                "💡 Please send a media link (TikTok, Instagram, YouTube, X, Reddit) or a photo to update its timestamp!",
                reply_to_message_id=msg_id,
            )
        return

    # Process extracted URL(s)
    for url in urls[:3]:  # Limit max 3 links per message to avoid spam
        await _download_and_reply(sender, chat_id, msg_id, url)


async def _handle_photo_timestamp(
    sender: TelegramSender,
    chat_id: int | str,
    reply_to_message_id: int,
    message: dict[str, Any],
) -> None:
    token = settings.telegram_bot_token
    if not token:
        return

    photo_list = message.get("photo")
    doc = message.get("document")
    if photo_list:
        file_id = photo_list[-1]["file_id"]
    elif doc and doc.get("mime_type", "").startswith("image/"):
        file_id = doc["file_id"]
    else:
        return

    await sender.send_chat_action(chat_id, "upload_photo")
    status_msg = await sender.send_message(
        chat_id, "🕒 <i>Updating camera timestamp…</i>", reply_to_message_id=reply_to_message_id
    )
    status_id = status_msg.get("result", {}).get("message_id")

    settings.temp_path.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(dir=settings.temp_path, prefix="ts_"))
    in_file = tmp_dir / "input.jpg"
    out_file = tmp_dir / "output.jpg"

    try:
        # 1. Download file from Telegram
        async with httpx.AsyncClient(timeout=40) as client:
            info_res = await client.get(f"https://api.telegram.org/bot{token}/getFile", params={"file_id": file_id})
            file_data = info_res.json()
            if not file_data.get("ok"):
                raise ValueError("Could not fetch file info from Telegram")
            file_path_tg = file_data["result"]["file_path"]
            dl_res = await client.get(f"https://api.telegram.org/file/bot{token}/{file_path_tg}")
            if dl_res.status_code != 200:
                raise ValueError("Could not download file from Telegram")
            in_file.write_bytes(dl_res.content)

        # 2. Extract custom text from caption if given
        caption = (message.get("caption") or "").strip()
        custom_text = caption if caption and not caption.startswith("/") else None

        # 3. Process image with update_image_timestamp
        result_path = await asyncio.to_thread(
            update_image_timestamp,
            image_path=in_file,
            output_path=out_file,
            custom_text=custom_text,
            position="bottom-left",
        )

        # 4. Reply with edited photo
        ts_shown = custom_text or datetime.datetime.now().strftime("%b %d, %Y, %H:%M")
        await sender.send_media(
            file_path=result_path,
            filename="timestamped.jpg",
            caption=f"✅ <b>Timestamp updated:</b> <code>{ts_shown}</code>",
            target_chat_id=chat_id,
            reply_to_message_id=reply_to_message_id,
        )

        if status_id:
            await sender.delete_message(chat_id, status_id)

    except Exception as e:
        logger.error("Timestamp processing error: %s", e)
        if status_id:
            await sender.edit_message_text(
                chat_id, status_id, f"❌ <b>Timestamp update failed:</b> {html.escape(str(e))}"
            )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def _download_and_reply(
    sender: TelegramSender,
    chat_id: int | str,
    reply_to_message_id: int,
    url: str,
) -> None:
    await sender.send_chat_action(chat_id, "upload_video")

    status_msg = await sender.send_message(
        chat_id, "⏳ <i>Processing media…</i>", reply_to_message_id=reply_to_message_id
    )
    status_id = status_msg.get("result", {}).get("message_id")

    downloader = MediaDownloader()
    result = None
    try:
        result = await asyncio.to_thread(downloader.download, url)
        await sender.send_chat_action(chat_id, "upload_video")

        # Resolve and translate title/caption
        raw_caption = result.title
        translated_caption = None
        if raw_caption:
            translated, detected_lang = detect_and_translate(raw_caption)
            if detected_lang and detected_lang not in ("en", "bn") and translated != raw_caption:
                translated_caption = f"{translated}\n\n🌐 <i>(Translated from {detected_lang.upper()})</i>"
            else:
                translated_caption = raw_caption

        # Inline button for extracting audio (if video)
        reply_markup = None
        if not result.is_album and result.media_type.startswith("video/"):
            reply_markup = {
                "inline_keyboard": [
                    [{"text": "🎵 Extract Audio (MP3)", "callback_data": f"aud:{url}"}]
                ]
            }

        item_paths = [item.file_path for item in result.items] if result.is_album else None

        await sender.send_media(
            file_path=result.file_path,
            filename=result.filename,
            caption=translated_caption,
            items=item_paths,
            target_chat_id=chat_id,
            reply_to_message_id=reply_to_message_id,
            reply_markup=reply_markup,
        )

        # Delete the temporary status message
        if status_id:
            await sender.delete_message(chat_id, status_id)

    except Exception as e:
        logger.error("Error downloading media for %s: %s", url, e)
        if status_id:
            err_text = f"❌ <b>Download failed:</b> {html.escape(str(e)[:200])}"
            await sender.edit_message_text(chat_id, status_id, err_text)
            # Auto-delete error message after 15s to keep chat clean
            await asyncio.sleep(15)
            await sender.delete_message(chat_id, status_id)
    finally:
        if result and result.tmp_path:
            downloader._cleanup(result.tmp_path)


# 3. Webhook Setup Helper
async def setup_webhook(webhook_url: str) -> bool:
    token = settings.telegram_bot_token
    if not token:
        return False
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{token}/setWebhook",
            json={
                "url": webhook_url,
                "allowed_updates": ["message", "callback_query"],
                "drop_pending_updates": True,
            },
        )
        data = resp.json()
        logger.info("setWebhook response: %s", data)
        return bool(data.get("ok"))


# 4. Long-Polling Background Worker (for local dev / environments without public webhook)
async def run_polling_worker() -> None:
    token = settings.telegram_bot_token
    if not token:
        logger.warning("No TELEGRAM_BOT_TOKEN set; telegram bot worker stopped")
        return

    # Delete any existing webhook before polling
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(f"https://api.telegram.org/bot{token}/deleteWebhook", json={"drop_pending_updates": False})

    offset = 0
    logger.info("Personal Mau Saver Telegram Bot polling started...")
    while True:
        try:
            async with httpx.AsyncClient(timeout=40) as client:
                resp = await client.get(
                    f"https://api.telegram.org/bot{token}/getUpdates",
                    params={"offset": offset, "timeout": 30, "allowed_updates": '["message","callback_query"]'},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for update in data.get("result", []):
                        offset = max(offset, update["update_id"] + 1)
                        asyncio.create_task(process_update(update))
                elif resp.status_code == 409:
                    logger.warning("Telegram polling conflict (webhook or another instance active). Waiting 10s...")
                    await asyncio.sleep(10)
                else:
                    await asyncio.sleep(2)
        except asyncio.CancelledError:
            logger.info("Telegram polling worker cancelled")
            break
        except Exception as e:
            logger.warning("Telegram polling error: %s", e)
            await asyncio.sleep(5)
