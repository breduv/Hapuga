import asyncio
from pathlib import Path
from threading import Lock

from gallery_dl import config, exception, job
from loguru import logger

from app.core.config import Settings
from app.errors.base import (
    DownloadError,
    EmptyResponseError,
    MediaTooLargeError,
)


class GallerydlProvider:
    _config_lock = Lock()

    def __init__(self, settings: Settings) -> None:
        self.proxy = (
            settings.media_proxy.get_secret_value()
            if settings.media_proxy is not None
            else None
        )
        self.max_upload_size_bytes = settings.max_upload_size_mb * 1024 * 1024

    def _download_photos_sync(self, url: str, output_dir: Path) -> list[Path]:
        options: list[tuple[tuple[str, ...], str, object]] = [
            ((), "base-directory", str(output_dir)),
            ((), "directory", ()),
            (("extractor", "tiktok"), "photos", True),
            (("extractor", "tiktok"), "audio", False),
            (("extractor", "tiktok"), "videos", False),
            (("extractor", "tiktok"), "covers", False),
        ]

        if self.proxy is not None:
            options.append((("extractor", "tiktok"), "proxy", self.proxy))

        logger.debug(
            "Gallery-dl gallery download started proxy={}",
            self.proxy is not None,
        )

        try:
            with self._config_lock, config.apply(options):
                status = job.DownloadJob(url).run()
        except exception.NoExtractorError as exc:
            raise DownloadError(
                "gallery-dl has no extractor for URL",
                component="gallery-dl",
                operation_name="download_photos",
                details=str(exc),
                public_message="Не удалось обработать эту ссылку на фото TikTok",
            ) from exc
        except exception.GalleryDLException as exc:
            raise DownloadError(
                "gallery-dl failed to initialize download",
                component="gallery-dl",
                operation_name="download_photos",
                details=str(exc),
                public_message="Не удалось скачать фотографии из TikTok. Попробуй позже",
            ) from exc
        except OSError as exc:
            raise DownloadError(
                "gallery-dl failed to access output directory",
                component="gallery-dl",
                operation_name="download_photos",
                details=str(exc),
                public_message="Не удалось скачать фотографии из TikTok. Попробуй позже",
            ) from exc

        if status:
            raise DownloadError(
                f"gallery-dl download failed with status {status}",
                component="gallery-dl",
                operation_name="download_photos",
                public_message="Не удалось скачать фотографии из TikTok. Попробуй позже",
            )

        try:
            files = sorted(
                path
                for path in output_dir.rglob("*")
                if path.is_file() and not path.name.endswith(".part")
            )
        except OSError as exc:
            raise DownloadError(
                "failed to locate downloaded photo",
                component="gallery-dl",
                operation_name="download_photos",
                details=str(exc),
                public_message="Не удалось скачать фотографии из TikTok. Попробуй позже",
            ) from exc

        if not files:
            raise EmptyResponseError(
                "gallery-dl did not download a photo",
                component="gallery-dl",
                operation_name="download_photos",
                public_message="В публикации TikTok не нашлось доступных фотографий",
            )

        total_size_bytes = 0

        for photo_path in files:
            try:
                size_bytes = photo_path.stat().st_size
            except OSError as exc:
                raise DownloadError(
                    "failed to inspect downloaded photo",
                    component="gallery-dl",
                    operation_name="download_photos",
                    details=str(exc),
                    public_message="Не удалось скачать фотографии из TikTok. Попробуй позже",
                ) from exc

            if size_bytes == 0:
                raise EmptyResponseError(
                    "gallery-dl downloaded an empty photo",
                    component="gallery-dl",
                    operation_name="download_photos",
                    public_message="В публикации TikTok не нашлось доступных фотографий",
                )

            if size_bytes > self.max_upload_size_bytes:
                raise MediaTooLargeError(
                    "downloaded photo is too large",
                    component="gallery-dl",
                    operation_name="download_photos",
                    public_message="Одна из фотографий слишком большая для отправки",
                )

            total_size_bytes += size_bytes

        logger.info(
            "Gallery-dl gallery download completed photos_count={} total_size_bytes={}",
            len(files),
            total_size_bytes,
        )
        return files

    async def download_photos(self, url: str, output_dir: Path) -> list[Path]:
        return await asyncio.to_thread(
            self._download_photos_sync,
            url,
            output_dir,
        )

    def _download_video_sync(self, url: str, output_dir: Path) -> Path:
        options: list[tuple[tuple[str, ...], str, object]] = [
            ((), "base-directory", str(output_dir)),
            ((), "directory", ()),
            ((), "filename", "gallery-dl-{id}.{extension}"),
            (("extractor", "tiktok"), "photos", False),
            (("extractor", "tiktok"), "audio", False),
            (("extractor", "tiktok"), "videos", True),
            (("extractor", "tiktok"), "covers", False),
        ]

        if self.proxy is not None:
            options.append((("extractor", "tiktok"), "proxy", self.proxy))

        logger.debug(
            "Gallery-dl video fallback started proxy={}",
            self.proxy is not None,
        )

        try:
            with self._config_lock, config.apply(options):
                status = job.DownloadJob(url).run()
        except exception.NoExtractorError as exc:
            raise DownloadError(
                "gallery-dl has no extractor for TikTok video URL",
                component="gallery-dl",
                operation_name="download_video",
                details=str(exc),
                public_message="Не удалось обработать эту ссылку на видео TikTok",
            ) from exc
        except exception.GalleryDLException as exc:
            raise DownloadError(
                "gallery-dl failed to initialize video download",
                component="gallery-dl",
                operation_name="download_video",
                details=str(exc),
                public_message="Не удалось скачать видео из TikTok. Попробуй позже",
            ) from exc
        except OSError as exc:
            raise DownloadError(
                "gallery-dl failed to access video output directory",
                component="gallery-dl",
                operation_name="download_video",
                details=str(exc),
                public_message="Не удалось скачать видео из TikTok. Попробуй позже",
            ) from exc

        if status:
            raise DownloadError(
                f"gallery-dl video download failed with status {status}",
                component="gallery-dl",
                operation_name="download_video",
                public_message="Не удалось скачать видео из TikTok. Попробуй позже",
            )

        try:
            video_files = [
                path
                for path in output_dir.rglob("*")
                if path.is_file()
                and not path.name.endswith(".part")
                and path.name.startswith("gallery-dl-")
                and path.suffix.lower() in {".mp4", ".mkv", ".mov", ".webm"}
            ]
            video_path = max(video_files, key=lambda path: path.stat().st_size)
            size_bytes = video_path.stat().st_size
        except ValueError as exc:
            raise EmptyResponseError(
                "gallery-dl did not download a video",
                component="gallery-dl",
                operation_name="download_video",
                public_message="В публикации TikTok не нашлось доступного видео",
            ) from exc
        except OSError as exc:
            raise DownloadError(
                "failed to locate downloaded TikTok video",
                component="gallery-dl",
                operation_name="download_video",
                details=str(exc),
                public_message="Не удалось скачать видео из TikTok. Попробуй позже",
            ) from exc

        if size_bytes == 0:
            raise EmptyResponseError(
                "gallery-dl downloaded an empty video",
                component="gallery-dl",
                operation_name="download_video",
                public_message="В публикации TikTok не нашлось доступного видео",
            )

        if size_bytes > self.max_upload_size_bytes:
            raise MediaTooLargeError(
                "downloaded TikTok video is too large",
                component="gallery-dl",
                operation_name="download_video",
                public_message="Видеофайл слишком большой для отправки",
            )

        logger.info("Gallery-dl video fallback completed size_bytes={}", size_bytes)
        return video_path

    async def download_video(self, url: str, output_dir: Path) -> Path:
        return await asyncio.to_thread(self._download_video_sync, url, output_dir)
