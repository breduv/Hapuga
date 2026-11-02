from pathlib import Path
from urllib.parse import urlparse

from loguru import logger

from app.errors.base import DownloadError
from app.models.search import TIKTOK_HOSTS
from app.providers.cover import CoverProvider
from app.providers.gallerydl import GallerydlProvider
from app.providers.spotify import SpotifyProvider
from app.providers.ytdlp import YtdlpProvider


class DownloadService:
    def __init__(
        self,
        spotify_provider: SpotifyProvider,
        ytdlp_provider: YtdlpProvider,
        cover_provaider: CoverProvider,
        gallerydl_provider: GallerydlProvider,
    ) -> None:
        self.spotify_provider = spotify_provider
        self.ytdlp_provider = ytdlp_provider
        self.cover_provaider = cover_provaider
        self.gallerydl_provider = gallerydl_provider

    async def download_media(
        self,
        media: dict[str, str],
        output_dir: Path,
    ) -> tuple[Path, Path | None]:
        value = media.get("audio")

        if value is not None:
            logger.info(
                "Audio download started metadata={}",
                "title" in media and "artist" in media,
            )
            audio_path, cover_path = await self.ytdlp_provider.download_audio(
                query_or_url=value,
                output_dir=output_dir,
            )

            title = media.get("title")
            artist = media.get("artist")

            if title is not None and artist is not None:
                await self.cover_provaider.set_mp3_metadata(
                    audio_path, title=title, artist=artist
                )

            cover_url = media.get("cover_url")

            if cover_url is not None:
                cover_path = await self.cover_provaider.download_cover(
                    cover_url, output_dir
                )

            if cover_path is not None:
                await self.cover_provaider.set_mp3_cover(audio_path, cover_path)

            logger.info("Audio download completed cover={}", cover_path is not None)
            return audio_path, cover_path

        logger.info("Video download started")
        video_url = media["video"]

        try:
            video_path = await self.ytdlp_provider.download_video(
                url=video_url,
                output_dir=output_dir,
            )
        except DownloadError as exc:
            if urlparse(video_url).hostname not in TIKTOK_HOSTS:
                raise

            logger.warning(
                "yt-dlp TikTok download failed, trying gallery-dl fallback details={}",
                exc.details,
            )
            video_path = await self.gallerydl_provider.download_video(
                url=video_url,
                output_dir=output_dir,
            )

        logger.info("Video download completed")
        return video_path, None

    async def download_photos(self, url: str, output_dir: Path) -> list[Path]:
        logger.info("Gallery download started")
        photo_paths = await self.gallerydl_provider.download_photos(
            url=url,
            output_dir=output_dir,
        )
        logger.info("Gallery download completed photos_count={}", len(photo_paths))
        return photo_paths

    async def download_on_spotify(
        self, track_id: str, output_dir: Path
    ) -> tuple[Path, Path | None]:
        logger.info("Spotify selection download started track_id={}", track_id)
        track = await self.spotify_provider.get_track(track_id)

        audio_path, cover_path = await self.ytdlp_provider.download_audio(
            query_or_url=f"{track.artist} - {track.title}",
            output_dir=output_dir,
        )

        await self.cover_provaider.set_mp3_metadata(
            audio_path, title=track.title, artist=track.artist
        )

        if track.cover_url is not None:
            cover_path = await self.cover_provaider.download_cover(
                track.cover_url, output_dir
            )

        if cover_path is not None:
            await self.cover_provaider.set_mp3_cover(audio_path, cover_path)

        logger.info(
            "Spotify selection download completed cover={}", cover_path is not None
        )
        return audio_path, cover_path

    async def download_on_youtube(
        self, url: str, format_id: str, output_dir: Path
    ) -> tuple[Path, Path | None]:
        logger.info("YouTube selection download started format_id={}", format_id)
        if format_id == "-1":
            audio_path, cover_path = await self.ytdlp_provider.download_audio(
                url, output_dir
            )

            if cover_path is not None:
                await self.cover_provaider.set_mp3_cover(audio_path, cover_path)

            logger.info(
                "YouTube audio download completed cover={}", cover_path is not None
            )
            return audio_path, cover_path

        video_path = await self.ytdlp_provider.download_video(
            url, output_dir, format_id
        )
        logger.info("YouTube video download completed format_id={}", format_id)
        return video_path, None
