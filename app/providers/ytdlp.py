import asyncio
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
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

# yt-dlp ids are opaque, but they end up in file names: anything outside this
# alphabet (a path separator, ``..``) would write outside the output directory.
_MEDIA_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_AUDIO_SUFFIXES = (".mp3",)


@dataclass(frozen=True)
class MediaProbe:
    """What ffprobe found inside a downloaded file."""

    has_video: bool
    has_audio: bool


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

        self.cookies_file = settings.cookies_file

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
        try:
            with YoutubeDL(options) as ydl:  # pyright: ignore[reportArgumentType]
                raw_info = ydl.extract_info(target, download=True)

                if raw_info is None:
                    raise EmptyResponseError(
                        "yt-dlp returned empty response",
                        component="yt-dlp",
                        operation_name=operation,
                        public_message=public_messages["empty_response"],
                    )

                info = ydl.sanitize_info(raw_info)

        except YtdlpDownloadError as exc:
            if not self.cookies_file:
                raise DownloadError(
                    "yt-dlp failed to download target",
                    component="yt-dlp",
                    operation_name=operation,
                    details=str(exc),
                    public_message=public_messages["download_error"],
                ) from exc

            logger.warning(
                "yt-dlp extraction failed without cookies, retrying with cookies "
                "operation_name={} details={}",
                operation,
                str(exc),
            )

            retry_options = options | {
                "cookiefile": self.cookies_file,
            }

            try:
                with YoutubeDL(retry_options) as ydl:  # pyright: ignore[reportArgumentType]
                    raw_info = ydl.extract_info(target, download=True)

                    if raw_info is None:
                        raise EmptyResponseError(
                            "yt-dlp returned empty response with cookies",
                            component="yt-dlp",
                            operation_name=operation,
                            public_message=public_messages["empty_response"],
                        )

                    info = ydl.sanitize_info(raw_info)

            except YtdlpDownloadError as retry_exc:
                raise DownloadError(
                    "yt-dlp failed to download target with cookies",
                    component="yt-dlp",
                    operation_name=operation,
                    details=str(retry_exc),
                    public_message=public_messages["download_error"],
                ) from retry_exc

            logger.info(
                "yt-dlp extraction succeeded with cookies operation_name={}",
                operation,
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

        file_path = self._resolve_media_file(
            output_dir,
            media_id,
            _AUDIO_SUFFIXES,
            operation="download_audio",
            public_message="Не удалось скачать аудио. Попробуй другую ссылку или запрос",
        )

        filesize = self._ensure_uploadable(
            file_path,
            operation="download_audio",
            public_message="Аудиофайл слишком большой для отправки. Попробуй другой трек",
        )

        cover_path = None

        for suffix in (".jpg", ".jpeg", ".png", ".webp"):
            candidate = output_dir / f"{file_path.stem}{suffix}"

            if candidate.is_file():
                cover_path = candidate
                break

        logger.info(
            "Audio file prepared media_id={} file_name={} size_bytes={} cover={}",
            media_id,
            file_path.name,
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
    def _probe_media(file_path: Path) -> MediaProbe | None:
        """Inspect a downloaded file, or return ``None`` if the probe failed.

        A non-zero exit code or unparsable JSON means ffprobe could not inspect
        the container -- not that the container is invalid. Callers must keep
        the "no video stream" verdict separate from "no answer", because the
        retry loop deletes the file it rejects.
        """
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
            logger.warning(
                "ffprobe failed to inspect media path={} returncode={} stderr={}",
                file_path.name,
                result.returncode,
                result.stderr.strip()[:200],
            )
            return None

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            logger.warning(
                "ffprobe returned unparsable output path={}", file_path.name
            )
            return None

        if not isinstance(data, dict):
            return None

        streams = data.get("streams", [])

        if not isinstance(streams, list):
            return None

        has_video = any(
            isinstance(stream, dict) and stream.get("codec_type") == "video"
            for stream in streams
        )
        has_audio = any(
            isinstance(stream, dict) and stream.get("codec_type") == "audio"
            for stream in streams
        )

        return MediaProbe(has_video=has_video, has_audio=has_audio)

    @staticmethod
    def _resolve_media_file(
        output_dir: Path,
        media_id: object,
        suffixes: tuple[str, ...],
        *,
        operation: str,
        public_message: str,
    ) -> Path:
        """Locate the file yt-dlp wrote for ``media_id``.

        The id comes from a third-party extractor and is interpolated into a
        path, so it is validated before use, and any extension the
        post-processor chose is accepted instead of assuming one.
        """
        if not isinstance(media_id, str) or not _MEDIA_ID_PATTERN.match(media_id):
            raise UnexpectedResponseError(
                f"yt-dlp returned an unusable media id: {media_id!r}",
                component="yt-dlp",
                operation_name=operation,
                public_message=public_message,
            )

        for suffix in suffixes:
            candidate = output_dir / f"{media_id}{suffix}"
            if candidate.is_file():
                return candidate

        for candidate in sorted(output_dir.glob(f"{media_id}*")):
            if candidate.is_file() and candidate.suffix.lower() in suffixes:
                return candidate

        raise DownloadError(
            "downloaded file was not found",
            component="yt-dlp",
            operation_name=operation,
            details=f"expected one of {suffixes} for media id {media_id}",
            public_message=public_message,
        )

    def _ensure_uploadable(
        self,
        file_path: Path,
        *,
        operation: str,
        public_message: str,
    ) -> int:
        try:
            filesize = file_path.stat().st_size
        except OSError as exc:
            raise DownloadError(
                "failed to inspect downloaded file",
                component="yt-dlp",
                operation_name=operation,
                details=str(exc),
                public_message=public_message,
            ) from exc

        if filesize > self.max_upload_size_bytes:
            raise MediaTooLargeError(
                "downloaded media file is too large",
                component="yt-dlp",
                operation_name=operation,
                details=f"{filesize} bytes > {self.max_upload_size_bytes} bytes",
                public_message=public_message,
            )

        return filesize

    @staticmethod
    def _get_used_format_ids(info: dict[str, Any]) -> list[str]:
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

            file_path = self._resolve_media_file(
                output_dir,
                media_id,
                (".mp4",),
                operation="download_video",
                public_message=(
                    "Не удалось скачать видео. Проверь ссылку или попробуй позже"
                ),
            )

            probe = self._probe_media(file_path)

            if probe is None:
                # ffprobe could not read the container: keep the file rather
                # than deleting a download that yt-dlp reported as successful.
                filesize = self._ensure_uploadable(
                    file_path,
                    operation="download_video",
                    public_message=(
                        "Видеофайл слишком большой для отправки. Выбери более низкое качество"
                        if format_id is not None
                        else "Видеофайл слишком большой для отправки. Попробуй другую ссылку"
                    ),
                )
                logger.warning(
                    "Video file could not be probed, sending it anyway "
                    "media_id={} size_bytes={} format_id={}",
                    media_id,
                    filesize,
                    format_id or "auto",
                )
                return file_path

            if probe.has_video and probe.has_audio:
                filesize = self._ensure_uploadable(
                    file_path,
                    operation="download_video",
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
                probe.has_video,
                probe.has_audio,
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

            retry_options = options | {
                "cookiefile": self.cookies_file,
            }

            try:
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

            except YtdlpDownloadError as retry_exc:
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
