import asyncio
import json
import subprocess
from io import StringIO
from pathlib import Path
from time import monotonic
from typing import Any, cast

from loguru import logger
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError as YtdlpDownloadError

from app.core.config import Settings
from app.errors.base import (
    DownloadError,
    EmptyResponseError,
    MediaTooLargeError,
    UnexpectedResponseError,
)
from app.models.media import AvailableVideoFormat


class YtdlpProvider:
    def __init__(self, settings: Settings) -> None:
        self.base_options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": False,
            "noplaylist": True,
            "noprogress": True,
        }
        if settings.media_proxy:
            self.base_options["proxy"] = settings.media_proxy.get_secret_value()

        self.cookies_file = (
            Path(settings.cookies_file) if settings.cookies_file else None
        )

        self.max_upload_size_bytes = settings.max_upload_size_mb * 1024 * 1024

    def _download(
        self, target: str, options: dict[str, Any], operation: str
    ) -> dict[str, Any]:
        public_messages = {
            "download_audio": {
                "empty_response": (
                    "Не удалось получить информацию об аудио. Проверь ссылку или запрос"
                ),
                "download_error": "Не удалось скачать аудио. Попробуй другую ссылку или запрос",
                "unexpected_response": (
                    "Не удалось обработать данные аудио. "
                    "Попробуй другую ссылку или запрос"
                ),
                "empty_entries": "По этому запросу не удалось найти аудио",
            },
            "download_video": {
                "empty_response": "Не удалось получить информацию о видео. Проверь ссылку",
                "download_error": "Не удалось скачать видео. Проверь ссылку или попробуй позже",
                "unexpected_response": "Не удалось обработать данные видео. Попробуй другую ссылку",
                "empty_entries": "Не удалось получить информацию о видео. Проверь ссылку",
            },
        }[operation]

        logger.debug(
            "yt-dlp extraction started operation_name={} target_type={}",
            operation,
            "url" if target.startswith(("http://", "https://")) else "search",
        )
        cookies_file = self.cookies_file
        if cookies_file is None:
            attempts = (False,)
        elif operation == "download_audio" and target.startswith("ytsearch1:"):
            attempts = (True, False)
        else:
            attempts = (False, True)

        info = None
        for attempt, use_cookies in enumerate(attempts, start=1):
            started_at = monotonic()
            attempt_options = options
            try:
                if use_cookies and cookies_file is not None:
                    # yt-dlp saves its cookie jar on exit. Keep writes local to this attempt.
                    attempt_options = options | {
                        "cookiefile": StringIO(
                            cookies_file.read_text(encoding="utf-8")
                        ),
                    }

                with YoutubeDL(attempt_options) as ydl:  # pyright: ignore[reportArgumentType]
                    raw_info = ydl.extract_info(target, download=True)

                    if raw_info is None:
                        raise EmptyResponseError(
                            "yt-dlp returned empty response",
                            component="yt-dlp",
                            operation_name=operation,
                            public_message=public_messages["empty_response"],
                        )

                    info = ydl.sanitize_info(raw_info)
            except (YtdlpDownloadError, OSError, UnicodeError) as exc:
                if attempt == len(attempts):
                    raise DownloadError(
                        "yt-dlp failed to download target",
                        component="yt-dlp",
                        operation_name=operation,
                        details=str(exc),
                        public_message=public_messages["download_error"],
                    ) from exc

                logger.warning(
                    "yt-dlp download attempt failed, retrying operation_name={} "
                    "cookies={} details={}",
                    operation,
                    use_cookies,
                    str(exc),
                )
            else:
                break
            finally:
                logger.info(
                    "yt-dlp download attempt duration_s={:.3f} operation_name={} cookies={}",
                    monotonic() - started_at,
                    operation,
                    use_cookies,
                )

        if not isinstance(info, dict):
            raise UnexpectedResponseError(
                f"yt-dlp returned unexpected response type: {type(info).__name__}",
                component="yt-dlp",
                operation_name=operation,
                public_message=public_messages["unexpected_response"],
            )

        entries = info.get("entries")

        if entries is None:
            logger.debug("yt-dlp extraction completed operation_name={}", operation)
            return cast(dict[str, Any], info)

        if not isinstance(entries, list):
            raise UnexpectedResponseError(
                "yt-dlp returned invalid entries",
                component="yt-dlp",
                operation_name=operation,
                public_message=public_messages["unexpected_response"],
            )

        if len(entries) == 0:
            raise EmptyResponseError(
                "yt-dlp returned empty entries",
                component="yt-dlp",
                operation_name=operation,
                public_message=public_messages["empty_entries"],
            )

        entry = entries[0]

        if not isinstance(entry, dict):
            raise UnexpectedResponseError(
                "yt-dlp returned invalid entry",
                component="yt-dlp",
                operation_name=operation,
                public_message=public_messages["unexpected_response"],
            )

        logger.debug("yt-dlp extraction completed operation_name={}", operation)
        return cast(dict[str, Any], entry)

    def _download_audio_sync(
        self, query_or_url: str, output_dir: Path
    ) -> tuple[Path, Path | None]:
        options = self.base_options | {
            "format": "bestaudio/best[acodec!=none]",
            "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
            "writethumbnail": True,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                },
                {
                    "key": "FFmpegMetadata",
                },
                # {
                #     "key": "EmbedThumbnail",
                # },
            ],
        }

        target = (
            query_or_url
            if query_or_url.startswith(("http://", "https://"))
            else f"ytsearch1:{query_or_url}"
        )

        info = self._download(target, options, operation="download_audio")

        media_id = info.get("id")
        if media_id is None:
            raise UnexpectedResponseError(
                "yt-dlp returned info without media id",
                component="yt-dlp",
                operation_name="download_audio",
                public_message=(
                    "Не удалось обработать данные аудио. "
                    "Попробуй другую ссылку или запрос"
                ),
            )

        file_path = output_dir / (media_id + ".mp3")
        if not file_path.is_file():
            raise DownloadError(
                "downloaded file was not found",
                component="yt-dlp",
                operation_name="download_audio",
                public_message="Не удалось скачать аудио. Попробуй другую ссылку или запрос",
            )

        filesize = file_path.stat().st_size

        if filesize > self.max_upload_size_bytes:
            raise MediaTooLargeError(
                "downloaded media file is too large",
                component="yt-dlp",
                operation_name="download_audio",
                public_message="Аудиофайл слишком большой для отправки. Попробуй другой трек",
            )

        cover_path = None

        for suffix in (".jpg", ".jpeg", ".png", ".webp"):
            candidate = output_dir / f"{media_id}{suffix}"

            if candidate.is_file():
                cover_path = candidate
                break

        logger.info(
            "Audio file prepared media_id={} size_bytes={} cover={}",
            media_id,
            filesize,
            cover_path is not None,
        )
        return file_path, cover_path

    async def download_audio(
        self, query_or_url: str, output_dir: Path
    ) -> tuple[Path, Path | None]:
        return await asyncio.to_thread(
            self._download_audio_sync,
            query_or_url,
            output_dir,
        )

    @staticmethod
    def _probe_has_video_and_audio(file_path: Path) -> tuple[bool, bool]:
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "stream=codec_type",
                    "-of",
                    "json",
                    str(file_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise DownloadError(
                "ffprobe was not found",
                component="ffprobe",
                operation_name="probe_media",
                details=str(exc),
                public_message="Не удалось проверить скачанное видео. Попробуй позже",
            ) from exc

        if result.returncode != 0:
            return False, False

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False, False
        streams = data.get("streams", [])

        has_video = any(stream.get("codec_type") == "video" for stream in streams)
        has_audio = any(stream.get("codec_type") == "audio" for stream in streams)

        return has_video, has_audio

    def _get_used_format_ids(self, info: dict[str, Any]) -> list[str]:
        requested_formats = info.get("requested_formats")

        if isinstance(requested_formats, list):
            result: list[str] = []

            for item in requested_formats:
                if not isinstance(item, dict):
                    continue

                format_id = item.get("format_id")

                if isinstance(format_id, str):
                    result.append(format_id)

            return result

        format_id = info.get("format_id")

        if isinstance(format_id, str):
            return [part for part in format_id.split("+") if part]

        return []

    def _download_video_sync(
        self, url: str, format_id: str | None, output_dir: Path
    ) -> Path:
        banned_format_ids: set[str] = set()
        max_attempts = 3

        for attempt in range(1, max_attempts + 1):
            exclude = "".join(
                f"[format_id!={format_id}]" for format_id in banned_format_ids
            )

            if format_id is None and attempt == 1:
                format_selector = f"bestvideo*{exclude}+bestaudio{exclude}/best[vcodec!=none][acodec!=none]{exclude}"
            elif format_id is None:
                format_selector = (
                    f"best[vcodec^=h264][acodec!=none]{exclude}/"
                    f"best[vcodec^=avc1][acodec!=none]{exclude}/"
                    f"best[vcodec!=none][acodec!=none]{exclude}"
                )
            else:
                format_selector = (
                    f"{format_id}+bestaudio/{format_id}[vcodec!=none][acodec!=none]"
                )

            options = self.base_options | {
                "format": format_selector,
                "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
                "merge_output_format": "mp4",
                "recodevideo": "mp4",
            }

            info = self._download(url, options, operation="download_video")

            media_id = info.get("id")
            if media_id is None:
                raise UnexpectedResponseError(
                    "yt-dlp returned info without media id",
                    component="yt-dlp",
                    operation_name="download_video",
                    public_message="Не удалось обработать данные видео. Попробуй другую ссылку",
                )

            file_path = output_dir / (str(media_id) + ".mp4")
            if not file_path.is_file():
                raise DownloadError(
                    "downloaded file was not found",
                    component="yt-dlp",
                    operation_name="download_video",
                    public_message="Не удалось скачать видео. Проверь ссылку или попробуй позже",
                )

            has_video, has_audio = self._probe_has_video_and_audio(file_path)

            if has_video and has_audio:
                filesize = file_path.stat().st_size

                if filesize > self.max_upload_size_bytes:
                    raise MediaTooLargeError(
                        "downloaded media file is too large",
                        component="yt-dlp",
                        operation_name="download_video",
                        public_message=(
                            "Видеофайл слишком большой для отправки. Выбери более низкое качество"
                            if format_id is not None
                            else "Видеофайл слишком большой для отправки. Попробуй другую ссылку"
                        ),
                    )

                logger.info(
                    "Video file prepared media_id={} size_bytes={} format_id={}",
                    media_id,
                    filesize,
                    format_id or "auto",
                )
                return file_path

            used_format_ids = self._get_used_format_ids(info)

            if not used_format_ids:
                raise DownloadError(
                    "downloaded invalid media and yt-dlp did not report used format ids",
                    component="yt-dlp",
                    operation_name="download_video",
                    public_message="Не удалось скачать видео. Проверь ссылку или попробуй позже",
                )

            banned_format_ids.update(used_format_ids)

            logger.warning(
                "Downloaded invalid media, retrying media_id={} has_video={} has_audio={} used_formats={} attempt={}",
                media_id,
                has_video,
                has_audio,
                used_format_ids,
                attempt,
            )

            file_path.unlink(missing_ok=True)

            if format_id is not None:
                raise DownloadError(
                    "selected video format produced invalid media",
                    component="yt-dlp",
                    operation_name="download_video",
                    public_message="Не удалось скачать видео. Проверь ссылку или попробуй позже",
                )

        raise DownloadError(
            "failed to download valid video with audio",
            component="yt-dlp",
            operation_name="download_video",
            public_message="Не удалось скачать видео. Проверь ссылку или попробуй позже",
        )

    async def download_video(
        self, url: str, output_dir: Path, format_id: str | None = None
    ) -> Path:
        return await asyncio.to_thread(
            self._download_video_sync,
            url,
            format_id,
            output_dir,
        )

    def _get_video_formats_sync(self, url: str) -> list[AvailableVideoFormat]:
        options = self.base_options | {
            "skip_download": True,
        }

        try:
            with YoutubeDL(options) as ydl:  # pyright: ignore[reportArgumentType]
                raw_info = ydl.extract_info(url, download=False)

                if raw_info is None:
                    raise EmptyResponseError(
                        "yt-dlp returned empty response",
                        component="yt-dlp",
                        operation_name="get_video_formats",
                        public_message="Не удалось получить информацию о видео. Проверь ссылку",
                    )

                info = ydl.sanitize_info(raw_info)

        except YtdlpDownloadError as exc:
            if not self.cookies_file:
                raise DownloadError(
                    "yt-dlp failed to extract video formats",
                    component="yt-dlp",
                    operation_name="get_video_formats",
                    details=str(exc),
                    public_message=(
                        "Не удалось получить форматы видео. "
                        "Проверь ссылку или попробуй позже"
                    ),
                ) from exc

            logger.warning(
                "yt-dlp format extraction failed without cookies, retrying with cookies "
                "operation_name=get_video_formats details={}",
                str(exc),
            )

            try:
                retry_options = options | {
                    "cookiefile": StringIO(
                        self.cookies_file.read_text(encoding="utf-8")
                    ),
                }
                with YoutubeDL(retry_options) as ydl:  # pyright: ignore[reportArgumentType]
                    raw_info = ydl.extract_info(url, download=False)

                    if raw_info is None:
                        raise EmptyResponseError(
                            "yt-dlp returned empty video formats response with cookies",
                            component="yt-dlp",
                            operation_name="get_video_formats",
                            public_message="Не удалось получить информацию о видео. Проверь ссылку",
                        )

                    info = ydl.sanitize_info(raw_info)

            except (YtdlpDownloadError, OSError, UnicodeError) as retry_exc:
                raise DownloadError(
                    "yt-dlp failed to extract video formats with cookies",
                    component="yt-dlp",
                    operation_name="get_video_formats",
                    details=str(retry_exc),
                    public_message=(
                        "Не удалось получить форматы видео. "
                        "Проверь ссылку или попробуй позже"
                    ),
                ) from retry_exc

            logger.info(
                "yt-dlp format extraction succeeded with cookies "
                "operation_name=get_video_formats",
            )

        if not isinstance(info, dict):
            raise UnexpectedResponseError(
                f"yt-dlp returned unexpected response type: {type(info).__name__}",
                component="yt-dlp",
                operation_name="get_video_formats",
                public_message="Не удалось разобрать доступные форматы видео",
            )

        formats = info.get("formats")

        if not isinstance(formats, list):
            raise UnexpectedResponseError(
                "yt-dlp returned invalid formats",
                component="yt-dlp",
                operation_name="get_video_formats",
                public_message="Не удалось разобрать доступные форматы видео",
            )

        media_id = info.get("id")

        if media_id is None:
            raise UnexpectedResponseError(
                "yt-dlp returned info without media id",
                component="yt-dlp",
                operation_name="get_video_formats",
                public_message="Не удалось разобрать доступные форматы видео",
            )

        result: list[AvailableVideoFormat] = []
        seen_heights: set[int] = set()

        for item in formats:
            if not isinstance(item, dict):
                continue

            format_id = item.get("format_id")
            height = item.get("height")
            vcodec = item.get("vcodec")

            if not isinstance(format_id, str):
                continue

            if not isinstance(height, int):
                continue

            if vcodec in (None, "none"):
                continue

            if height in seen_heights:
                continue

            seen_heights.add(height)

            result.append(
                AvailableVideoFormat(
                    video_id=media_id,
                    format_id=format_id,
                    height=height,
                )
            )

        if not result:
            raise EmptyResponseError(
                "yt-dlp returned no available video formats",
                component="yt-dlp",
                operation_name="get_video_formats",
                public_message="У этого видео не нашлось доступных форматов",
            )

        logger.debug("Video formats extracted formats_count={}", len(result))
        return sorted(result, key=lambda item: item.height, reverse=True)

    async def get_video_formats(self, url: str) -> list[AvailableVideoFormat]:
        return await asyncio.to_thread(
            self._get_video_formats_sync,
            url,
        )
