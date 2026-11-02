<div align="center">

**English** · [Русский](README.ru.md)

# DW Bot

**Music, videos, and photo galleries delivered to Telegram**

![Python 3.13](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![aiogram 3.29](https://img.shields.io/badge/aiogram-3.29-2CA5E0?logo=telegram&logoColor=white)
![yt-dlp](https://img.shields.io/badge/yt--dlp-media-FF0000?logo=youtube&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)
![MIT License](https://img.shields.io/badge/License-MIT-green)

Send a song title or a link. Choose a track or video quality when prompted,
and get the file directly in your chat.
Built with aiogram, Spotify search, yt-dlp, and gallery-dl.

[Features](#features) · [Quick start](#quick-start) · [Usage](#usage) · [Configuration](#configuration)

</div>

---

## Features

| Feature | Description |
| --- | --- |
| 🔎 **Music search** | Find tracks through Spotify and choose from up to 10 results |
| 🎧 **MP3 audio** | Extract audio and add track metadata and cover art when available |
| 🎬 **YouTube quality selection** | Choose an available video resolution or audio only |
| 🖼️ **TikTok photo galleries** | Receive photos in media groups of up to 10 images |
| ⚡ **Inline sharing** | Send supported media into other chats with `@your_bot <URL>` |
| 🍪 **Optional YouTube cookies** | Retry failed extraction requests with an authenticated session |
| 🌐 **Separate proxies** | Configure media downloads and Telegram connections independently |
| 🐳 **Docker Compose** | Python, FFmpeg, ffprobe, and Deno included in the image |

Spotify provides search results and metadata. Audio for Spotify tracks is found
on YouTube by artist and title; the bot does not download audio from Spotify.

## Quick start

You need Docker Compose, a Telegram bot token from BotFather, and a Spotify
application's client ID and client secret. Spotify credentials are required
by the current configuration even if you only plan to download videos.

1. Copy [`.env.example`](.env.example) to `.env`.
2. Fill in the required credentials:

   ```dotenv
   BOT_TOKEN=your-telegram-bot-token
   SPOTIFY_CLIENT_ID=your-spotify-client-id
   SPOTIFY_CLIENT_SECRET=your-spotify-client-secret
   ```

3. Start the bot from the project root:

   ```bash
   docker compose up -d --build
   ```

Open the bot, send `/start`, then try `Daft Punk Get Lucky` or a supported link.

The bot uses long polling; no public webhook endpoint is needed.
Compose mounts `./data` at `/app/data` for optional cookie files.

## Usage

| Send to the bot | Result |
| --- | --- |
| A song title or artist and title | Spotify search results with track selection buttons |
| A Spotify track link | MP3 audio with Spotify metadata and cover art when available |
| A regular YouTube video link | Available resolutions and an audio-only option |
| A YouTube Shorts link | Video download |
| A YouTube Music link | Audio download |
| A TikTok video or photo-post link | Video or photo gallery; short links are resolved automatically |
| An Instagram or Pinterest video link | Video download, subject to extractor support and access |
| A VK / VK Video clip link | Clip download; supported paths begin with `/clip` or `/clips` |
| `/help` | Usage instructions in Telegram |

Support is limited to the sources handled by
[`SearchService`](app/services/search_service.py), even though yt-dlp itself
supports more sites. Spotify albums and playlists are not supported.
The bot's Telegram interface is currently in Russian.

### Inline mode

In BotFather, enable `/setinline` and set `/setinlinefeedback` to **100%**.
The bot needs selection feedback to replace its placeholder with the file.

In any chat, type:

```text
@your_bot https://www.youtube.com/shorts/VIDEO_ID
```

Select **«Скачать»**. The bot posts **«Готовлю файл...»**, prepares the media,
and replaces that message with the result.

- Inline mode accepts links, not song-title searches. Regular YouTube links
  require quality selection and should be sent directly to the bot.
- For a TikTok photo gallery, inline mode sends only the first image.
  Send the link directly to the bot to receive the full gallery.
- Without `INLINE_CACHE_CHAT_ID`, each user must first start a private chat
  with the bot. Media is uploaded there temporarily to obtain a Telegram file ID.
- With `INLINE_CACHE_CHAT_ID`, temporary uploads go to the specified chat
  or channel instead. Give the bot permission to send media there.
  The bot attempts to delete each temporary upload after use.

## Configuration

Settings are loaded from environment variables and `.env` by
[`app/core/config.py`](app/core/config.py).

| Variable | Required | Default | Purpose |
| --- | :---: | --- | --- |
| `BOT_TOKEN` | yes | — | Telegram bot token |
| `SPOTIFY_CLIENT_ID` | yes | — | Spotify application client ID |
| `SPOTIFY_CLIENT_SECRET` | yes | — | Spotify application client secret |
| `COOKIES_FILE` | no | — | Netscape cookie filename inside `data/` |
| `MEDIA_PROXY` | no | — | Proxy for Spotify, yt-dlp, gallery-dl, and cover downloads |
| `TELEGRAM_PROXY` | no | — | Proxy for Telegram Bot API connections |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` |
| `SEARCH_LIMIT` | no | `5` | Number of Spotify search results; 1–10 |
| `MAX_UPLOAD_SIZE_MB` | no | `49` | Maximum size of each media file; at least 1 MB |
| `INLINE_CACHE_CHAT_ID` | no | — | Chat or channel ID for temporary inline uploads |
| `IMAGE_TAG` | no | `latest` | Docker image tag used by Compose |

Leave unused proxies empty. `MEDIA_PROXY` does not apply to the initial
HTTP request that resolves a short TikTok link. `IMAGE_TAG` is a Compose
setting; the other variables configure the application.

### YouTube cookies

1. Place a Netscape-format cookie file at `data/youtube.cookies.txt`.
2. Set `COOKIES_FILE=youtube.cookies.txt` in `.env`.
3. Recreate the container to apply the environment change:

   ```bash
   docker compose up -d
   ```

The bot first tries extraction without cookies, then retries with them after
an extraction error. Leave `COOKIES_FILE` empty to disable this retry.
If a filename is configured but the file is missing, settings validation
prevents startup. Cookies do not guarantee access to restricted content.

## Local development

Use Python 3.13. Install FFmpeg and ffprobe for audio conversion, video merging,
and stream inspection, and Deno for yt-dlp's YouTube JavaScript challenges.
These tools must be available on `PATH`; the Docker image already includes them.

<details>
<summary>Linux / macOS</summary>

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
mkdir -p data
# Fill in .env before starting.
python main.py
```

</details>

<details>
<summary>Windows PowerShell</summary>

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
New-Item -ItemType Directory -Force data
# Fill in .env before starting.
.\.venv\Scripts\python.exe main.py
```

</details>

Run commands from the project root so `.env` is found.

## Operations and troubleshooting

```bash
# Follow bot logs
docker compose logs -f --tail=100 download-bot

# Rebuild after source or dependency changes
docker compose up -d --build

# Stop the bot
docker compose down
```

Loguru writes to stderr. `INFO` shows request and download progress;
`DEBUG` adds extraction and format details. Standard-library logs from
dependencies are forwarded at `WARNING` and above.

<details>
<summary>A download fails or Telegram rejects the file</summary>

Check the bot logs for the failing provider. For YouTube extraction errors,
check Deno availability, cookies, and connectivity. For TikTok videos,
the bot falls back from yt-dlp to gallery-dl after a download error.

For large YouTube videos, choose a lower resolution. Increasing
`MAX_UPLOAD_SIZE_MB` does not change Telegram's upload limits.
Private, deleted, or region-restricted content may remain unavailable.

</details>

<details>
<summary>An inline message stays at «Готовлю файл...»</summary>

Check that inline feedback is set to 100% and inspect the bot logs.
Start a private chat with the bot, or configure an accessible
`INLINE_CACHE_CHAT_ID`. Use the private chat for links that require
track or quality selection.

</details>

## Project structure

Telegram handlers receive requests and deliver files. Services parse input
and coordinate providers; providers talk to external platforms and process media.

```text
app/
├── bot/          # Telegram handlers, router, and selection keyboards
├── core/         # Settings and logging
├── errors/       # Application errors and user-facing messages
├── models/       # Input kinds, track metadata, and video formats
├── providers/    # Spotify, yt-dlp, gallery-dl, covers, and MP3 tags
└── services/     # Input routing, search, and download coordination

data/             # Optional cookies; mounted by Compose
main.py           # Application entry point
docker-compose.yml
Dockerfile
```

Downloaded files live in temporary directories and are removed when request
processing finishes. The bot does not maintain a permanent media library.

## Privacy and limitations

- Spotify search and YouTube matching may return a different recording,
  remix, or performance. Check the downloaded track.
- Platform changes may require dependency updates. The bot does not support
  arbitrary URLs, Spotify playlists, or Instagram photo galleries.
- Files must satisfy both the configured size limit and Telegram's limits.
- Download and share only content you have permission to use.

---

<div align="center">

Search · choose · download · share

[MIT License](LICENSE)

</div>
