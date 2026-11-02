import asyncio
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from aiohttp_socks import ProxyConnector
from loguru import logger
from mutagen._util import MutagenError
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3
from mutagen.id3._frames import APIC
from mutagen.id3._util import ID3NoHeaderError

from app.core.config import Settings
from app.errors.base import (
    DownloadError,
    ProviderTimeoutError,
    UnexpectedResponseError,
)


class CoverProvider:
    def __init__(self, settings: Settings) -> None:
        self.proxy = (
            settings.media_proxy.get_secret_value()
            if settings.media_proxy is not None
            else None
        )

    async def download_cover(
        self, url: str, output_dir: Path, filename: str = "cover.jpg"
    ) -> Path:
        cover_path = output_dir / filename
        timeout = aiohttp.ClientTimeout(total=10)
        connector = None
        request_proxy = self.proxy

        if self.proxy is not None:
            parsed_proxy = urlsplit(self.proxy)

            if parsed_proxy.scheme in ("socks4", "socks5", "socks5h"):
                request_proxy = None
                rdns = parsed_proxy.scheme == "socks5h"
                proxy_url = self.proxy

                if rdns:
                    proxy_url = urlunsplit(parsed_proxy._replace(scheme="socks5"))

                connector = ProxyConnector.from_url(proxy_url, rdns=rdns)

        logger.debug(
            "Cover download started filename={} proxy={}",
            filename,
            self.proxy is not None,
        )

        try:
            async with (
                aiohttp.ClientSession(timeout=timeout, connector=connector) as session,
                session.get(url, proxy=request_proxy) as response,
            ):
                if response.status != 200:
                    raise DownloadError(
                        f"cover request returned status {response.status}",
                        component="cover",
                        operation_name="download_cover",
                        public_message="Не удалось скачать обложку трека. Попробуй позже",
                    )

                content = await response.read()

            if not content:
                raise DownloadError(
                    "cover response is empty",
                    component="cover",
                    operation_name="download_cover",
                    public_message="Не удалось скачать обложку трека. Попробуй позже",
                )

            cover_path.write_bytes(content)

        except DownloadError:
            raise

        except TimeoutError as exc:
            raise ProviderTimeoutError(
                "cover download timed out",
                component="cover",
                operation_name="download_cover",
                public_message="Сервис обложек не ответил вовремя. Попробуй позже",
            ) from exc

        except aiohttp.ClientError as exc:
            raise DownloadError(
                "cover download request failed",
                component="cover",
                operation_name="download_cover",
                details=str(exc),
                public_message="Не удалось скачать обложку трека. Попробуй позже",
            ) from exc

        except OSError as exc:
            raise DownloadError(
                "failed to save cover",
                component="cover",
                operation_name="download_cover",
                details=str(exc),
                public_message="Не удалось скачать обложку трека. Попробуй позже",
            ) from exc

        logger.info("Cover downloaded size_bytes={}", len(content))
        return cover_path

    def _set_mp3_cover(self, mp3_path: Path, cover_path: Path) -> None:
        try:
            try:
                tags = ID3(mp3_path)
            except ID3NoHeaderError:
                tags = ID3()

            tags.delall("APIC")

            tags.add(
                APIC(
                    encoding=3,
                    mime=self._detect_mime(cover_path),
                    type=3,
                    desc="Cover",
                    data=cover_path.read_bytes(),
                )
            )

            tags.save(mp3_path, v2_version=3)

        except (MutagenError, OSError) as exc:
            raise DownloadError(
                "failed to embed cover into mp3",
                component="cover",
                operation_name="set_mp3_cover",
                details=str(exc),
                public_message="Не удалось добавить обложку к аудиофайлу",
            ) from exc

    def _detect_mime(self, path: Path) -> str:
        mime_by_suffix = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }

        mime = mime_by_suffix.get(path.suffix.lower())
        if mime is not None:
            return mime

        raise UnexpectedResponseError(
            f"unsupported cover image extension: {path.suffix}",
            component="cover",
            operation_name="set_mp3_cover",
            public_message="Формат обложки не поддерживается",
        )

    async def set_mp3_cover(self, mp3_path: Path, cover_path: Path) -> None:
        await asyncio.to_thread(
            self._set_mp3_cover,
            mp3_path,
            cover_path,
        )
        logger.debug("MP3 cover embedded")

    def _set_mp3_metadata(self, mp3_path: Path, *, title: str, artist: str) -> None:
        try:
            try:
                tags = EasyID3(mp3_path)
            except ID3NoHeaderError:
                tags = EasyID3()
                tags.save(mp3_path)

            tags["title"] = title
            tags["artist"] = artist

            tags.save(mp3_path)
        except (MutagenError, OSError) as exc:
            raise DownloadError(
                "failed to set mp3 metadata",
                component="cover",
                operation_name="set_mp3_metadata",
                details=str(exc),
                public_message="Не удалось добавить данные о треке в аудиофайл",
            ) from exc

    async def set_mp3_metadata(
        self, mp3_path: Path, *, title: str, artist: str
    ) -> None:
        await asyncio.to_thread(
            self._set_mp3_metadata, mp3_path, title=title, artist=artist
        )
        logger.debug("MP3 metadata written")
