# Universal Media Downloader

FastAPI + [yt-dlp](https://github.com/yt-dlp/yt-dlp) service that downloads media from almost any site (YouTube, X/Twitter, Instagram, TikTok, Reddit, etc.) and can push the result straight to a Telegram chat. Ships with a web playground at `/playground`.

## Features

- **One API key** gates every endpoint
- Metadata extraction (`/api/v1/info`) with views / likes / reposts / comments where the source provides them
- Two download modes:
  - **Stream/download** the file back to the caller
  - **Send to Telegram** — file is uploaded to a bot chat *and* streamed back with `X-Telegram-*` headers
- Custom `title` parameter overrides the real caption/title (also used as the Telegram caption)
- Cookie support for age-restricted / NSFW content (browser extraction or a `cookies.txt` file)
- Large-file Telegram sends via a local Telegram Bot API server (cloud API caps at 50 MB)
- Image resolution fallback for X/Twitter (fxtwitter / vxtwitter resolvers)

## Quick start

### Option A — Termux / plain Python

```bash
cd media-downloader
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # then fill in API_KEYS, Telegram, etc.
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Requires `ffmpeg` on the PATH for video/audio merging.

### Option B — Docker

```bash
cp .env.example .env
docker compose up -d --build
```

## Configuration (.env)

| Variable | Default | Description |
|---|---|---|
| `API_KEYS` | `["changeme"]` | JSON list of allowed API keys |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Server bind address |
| `MAX_FILE_SIZE_MB` | `2048` | Max download size |
| `TEMP_DIR` | `/tmp/media-downloader` | Scratch dir for downloads |
| `COOKIE_BROWSER` | `chrome` | Browser to extract cookies from |
| `COOKIE_FILE` | *(empty)* | Optional `cookies.txt` path (overrides browser) |
| `DEFAULT_FORMAT` | `best` | Default `format_str` |
| `TELEGRAM_BOT_TOKEN` | *(empty)* | Bot token from BotFather |
| `TELEGRAM_CHAT_ID` | *(empty)* | Chat/group/channel ID (e.g. `-1004374814379`) |
| `TELEGRAM_LOCAL_API_URL` | *(empty)* | Local Bot API server URL for >50 MB sends |

`.env` is git-ignored. Commit only `.env.example` with placeholders.

## API

All endpoints require the header `X-API-Key: <your-key>`.

### `GET /api/v1/info`

Extract metadata without downloading.

| Param | Type | Description |
|---|---|---|
| `url` | string (required) | URL of the media |
| `browser` | string | Cookie browser override (`chrome`, `firefox`, ...) |
| `cookies` | string | Path to a `cookies.txt` on the server |
| `title` | string | Custom title overriding the real caption |

**Response** (`MediaInfo`):

```json
{
  "title": "মাগি যখন পোদে ছেলের ধোন নিয়ে…",
  "type": "image",
  "duration": null,
  "thumbnail": "https://pbs.twimg.com/media/HPUSS...jpg",
  "uploader": "Hot Mommy",
  "extractor": "vxtwitter",
  "webpage_url": "https://x.com/old_marquis/status/2086594418707198138",
  "views": 508,
  "likes": 3,
  "reposts": 0,
  "comments": 0,
  "formats": [
    { "format_id": "orig", "ext": "jpg", "resolution": "1920x2560",
      "filesize": 816425, "vcodec": null, "acodec": null, "url": "https://pbs.twimg.com/media/...jpg?name=orig" }
  ]
}
```

### `GET /api/v1/download`

Download the media. With `send_telegram=false` it streams the binary back; with `send_telegram=true` it uploads to Telegram **and** still streams the file back.

| Param | Type | Description |
|---|---|---|
| `url` | string (required) | URL of the media |
| `format_str` | string | `best` (default), `bestvideo+bestaudio`, `bestaudio`, `bestvideo`, `worst` |
| `audio_only` | bool | Extract audio only |
| `browser` | string | Cookie browser override |
| `cookies` | string | Path to a `cookies.txt` on the server |
| `title` | string | Custom title; also used as the Telegram caption |
| `send_telegram` | bool | Send the file to Telegram instead of only streaming it |

**Response headers** (both modes):

- `Content-Type` — mime type (`image/jpeg`, `video/mp4`, `audio/m4a`, ...)
- `Content-Disposition` — `attachment; filename="<filename>"`
- `Content-Length`

**Extra headers** when `send_telegram=true`:

- `X-Telegram-Sent: true`
- `X-Telegram-Link: https://t.me/c/<chat>/<message_id>`
- `X-Telegram-Message-Id: <message_id>`

**Errors** return `400` with `{"detail": "..."}`.

## Using it from another project

The service is stateless over HTTP — the only thing another project needs is the base URL and an API key.

### cURL

```bash
# Metadata
curl -H "X-API-Key: YOUR_KEY" \
  "http://host:8000/api/v1/info?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3DdQw4w9WgXcQ"

# Download to a file
curl -H "X-API-Key: YOUR_KEY" \
  "http://host:8000/api/v1/download?url=<urlencoded>" -o media.mp4

# Download AND send to Telegram
curl -H "X-API-Key: YOUR_KEY" \
  "http://host:8000/api/v1/download?url=<urlencoded>&send_telegram=true" \
  -D headers.txt -o media.mp4
grep -i x-telegram headers.txt
```

### Python

```python
import httpx

API = "http://host:8000"
KEY = "YOUR_KEY"
H = {"X-API-Key": KEY}

# Info
info = httpx.get(f"{API}/api/v1/info", params={"url": url}, headers=H).json()
print(info["title"], info["type"], info["formats"][0]["filesize"])

# Download
with httpx.stream("GET", f"{API}/api/v1/download",
                  params={"url": url}, headers=H) as r:
    r.raise_for_status()
    with open(info["title"] + ".mp4", "wb") as f:
        for chunk in r.iter_bytes():
            f.write(chunk)

# Download + send to Telegram
with httpx.stream("GET", f"{API}/api/v1/download",
                  params={"url": url, "send_telegram": True, "title": "My caption"},
                  headers=H) as r:
    r.raise_for_status()
    sent = r.headers.get("X-Telegram-Sent") == "true"
    link = r.headers.get("X-Telegram-Link")
    print("sent:", sent, "| link:", link)
```

### Node.js / browser

```js
const res = await fetch(`${API}/api/v1/download?${new URLSearchParams({ url, send_telegram: 'true' })}`, {
  headers: { 'X-API-Key': KEY },
});
console.log(res.headers.get('X-Telegram-Link'));       // telegram message link
const blob = await res.blob();
const imgUrl = URL.createObjectURL(blob);              // preview in the page
```

### Embedding as a module (same repo / monorepo)

You can also import it directly into another FastAPI app:

```python
from fastapi import FastAPI
from app.main import app as media_downloader_app

app = FastAPI()
app.mount("/media", media_downloader_app)
```

Endpoints then live at `http://host/media/api/v1/info`, etc.

## Telegram integration

1. Create a bot with [@BotFather](https://t.me/BotFather), copy the **token**.
2. Add the bot to the target group/channel. For a **private group**, make the bot admin, then get the group ID (e.g. open `https://web.telegram.org`, or send a test message and read the update via `getUpdates`). Public channels use `@channelusername`.
3. Put values in `.env`:

```bash
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=-1004374814379
```

### Files larger than 50 MB

The Telegram **cloud Bot API** hard-caps uploaded files at 50 MB. To send up to ~2000 MB, run the [telegram-bot-api](https://core.telegram.org/bots/api) local server and point to it:

```bash
TELEGRAM_LOCAL_API_URL=http://127.0.0.1:8081
```

With no local URL configured, oversized files fail with a clear error telling you to set `TELEGRAM_LOCAL_API_URL`.

The media type selects the Telegram method automatically: images → `sendPhoto`, videos → `sendVideo`, audio → `sendAudio`, everything else → `sendDocument`. The caption is the resolved title (or your custom `title`) truncated to 1024 chars.

## Playground

Open `http://host:8000/playground`. The UI includes:

- URL + format/cookie/caption fields
- **Send to Telegram** checkbox — its state persists across downloads and page reloads until you uncheck it
- After a Telegram download the media preview is still shown, with a green "Sent to Telegram · message N · Open in Telegram" bar under it

API keys are accepted via the **API Key** field at the top (default `test-secret-key-123` matches `.env`).

## Troubleshooting

- **`ERROR: could not find chrome cookies database`** — expected when no browser profile exists; the server retries without cookies automatically.
- **`Telegram send failed: ... file must be non-empty`** — transient Telegram API issue; retry.
- **`File is NN.MB, but the Telegram cloud Bot API only accepts files up to 50MB`** — configure `TELEGRAM_LOCAL_API_URL`.
- **403/404 on Twitter links** — the extractor falls back to fxtwitter/vxtwitter; verify the tweet is public.