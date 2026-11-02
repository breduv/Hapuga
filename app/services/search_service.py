from urllib.parse import urlparse

import aiohttp
from loguru import logger

from app.errors.base import (
    EmptyQueryError,
    InvalidInputKindError,
    UnsupportedUrlError,
    UrlResolutionError,
)
from app.models.search import (
    INSTAGRAM_HOSTS,
    PINTEREST_HOSTS,
    SPOTIFY_HOSTS,
    TIKTOK_HOSTS,
    TIKTOK_SHORT_HOSTS,
    VK_HOSTS,
    YOUTUBE_HOSTS,
    YOUTUBE_MUSIC_HOSTS,
    InputKind,
    ParsedInput,
)
from app.providers.spotify import SpotifyProvider
from app.providers.ytdlp import YtdlpProvider


class SearchService:
    def __init__(
        self, spotify_provider: SpotifyProvider, ytdlp_provider: YtdlpProvider
    ) -> None:
        self.spotify_provider = spotify_provider
        self.ytdlp_provider = ytdlp_provider

    async def _parse_input(self, text: str) -> ParsedInput:
        value = text.strip()

        if not value:
            raise EmptyQueryError(
                "search query is empty",
                component="search",
                operation_name="parse_input",
            )

        parsed = urlparse(value)

        if parsed.scheme not in ("http", "https") and not parsed.netloc:
            return ParsedInput(InputKind.QUERY, value)

        host = parsed.hostname

        if host is None:
            raise UnsupportedUrlError(
                f"URL without host: {value}",
                component="search",
                operation_name="parse_input",
            )

        if host in YOUTUBE_MUSIC_HOSTS:
            return ParsedInput(InputKind.AUDIO, value)

        if host in SPOTIFY_HOSTS:
            return ParsedInput(InputKind.SPOTIFY, value)

        if host in YOUTUBE_HOSTS:
            if parsed.path.startswith("/shorts/"):
                return ParsedInput(InputKind.VIDEO, value)

            return ParsedInput(InputKind.YOUTUBE, value)

        if host in YOUTUBE_MUSIC_HOSTS:
            return ParsedInput(InputKind.AUDIO, value)

        if host in TIKTOK_HOSTS:
            resolved_value = value
            is_short_url = host in TIKTOK_SHORT_HOSTS or parsed.path.startswith("/t/")

            if is_short_url:
                resolved_value = await self._resolve_tiktok_short_url(value)
                parsed = urlparse(resolved_value)

                if parsed.hostname not in TIKTOK_HOSTS:
                    raise UrlResolutionError(
                        "TikTok short URL resolved to an unexpected host",
                        component="search",
                        operation_name="resolve_tiktok_url",
                    )

            if "/photo/" in parsed.path:
                if parsed.hostname == "m.tiktok.com":
                    parsed = parsed._replace(netloc="www.tiktok.com")
                    resolved_value = parsed.geturl()

                return ParsedInput(InputKind.PHOTO, resolved_value)

            if "/video/" in parsed.path:
                return ParsedInput(InputKind.VIDEO, resolved_value)

            raise UnsupportedUrlError(
                f"unsupported TikTok URL path: {parsed.path}",
                component="search",
                operation_name="parse_input",
            )

        if (
            host in PINTEREST_HOSTS
            or host in INSTAGRAM_HOSTS
            or host in VK_HOSTS
            and parsed.path.startswith(("/clip", "/clips"))
        ):
            return ParsedInput(InputKind.VIDEO, value)

        return ParsedInput(InputKind.UNSUPPORTED_URL, value)

    async def search(self, text: str) -> dict[str, str]:
        parsed_input = await self._parse_input(text)
        logger.debug("Search input parsed source={}", parsed_input.source)

        if parsed_input.source == InputKind.QUERY:
            results = await self.spotify_provider.search_tracks(parsed_input.query)
            logger.info("Spotify search completed results_count={}", len(results))
            return {
                f"sp:{track.track_id}": f"{track.artist} - {track.title}"
                for track in results
            }

        if parsed_input.source == InputKind.YOUTUBE:
            formats = await self.ytdlp_provider.get_video_formats(parsed_input.query)
            logger.info("YouTube formats loaded formats_count={}", len(formats))

            video_id = formats[0].video_id

            results = {
                f"yt:{video.video_id}:{video.format_id}": f"{video.height}p"
                for video in formats
            }
            results[f"yt:{video_id}:-1"] = "Только звук"

            return results

        if parsed_input.source == InputKind.SPOTIFY:
            track = await self.spotify_provider.get_track(parsed_input.query)
            logger.info("Spotify track resolved track_id={}", track.track_id)
            result = {
                "audio": f"{track.artist} - {track.title}",
                "title": track.title,
                "artist": track.artist,
            }

            cover_url = track.cover_url
            if cover_url:
                result["cover_url"] = cover_url
            return result

        if parsed_input.source == InputKind.AUDIO:
            return {"audio": parsed_input.query}

        if parsed_input.source == InputKind.VIDEO:
            return {"video": parsed_input.query}

        if parsed_input.source == InputKind.PHOTO:
            return {"photo": parsed_input.query}

        if parsed_input.source == InputKind.UNSUPPORTED_URL:
            raise UnsupportedUrlError(
                f"unsupported url: {parsed_input.query}",
                component="search",
                operation_name="search",
            )

        raise InvalidInputKindError(
            f"invalid input kind: {parsed_input.source}",
            component="search",
            operation_name="search",
        )

    async def _resolve_tiktok_short_url(self, url: str) -> str:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            )
        }
        timeout = aiohttp.ClientTimeout(total=10)
        host = urlparse(url).hostname
        logger.debug("TikTok URL resolution started host={}", host)

        try:
            async with (
                aiohttp.ClientSession(headers=headers, timeout=timeout) as session,
                session.get(url, allow_redirects=True) as response,
            ):
                response.raise_for_status()
                resolved_url = str(response.url)
                logger.debug(
                    "TikTok URL resolution completed status={} resolved_host={}",
                    response.status,
                    response.url.host,
                )
                return resolved_url
        except TimeoutError as exc:
            logger.warning("TikTok URL resolution timed out host={}", host)
            raise UrlResolutionError(
                "TikTok short URL resolution timed out",
                component="search",
                operation_name="resolve_tiktok_url",
            ) from exc
        except aiohttp.ClientError as exc:
            logger.warning(
                "TikTok URL resolution failed host={} error={}",
                host,
                exc.__class__.__name__,
            )
            raise UrlResolutionError(
                "TikTok short URL resolution request failed",
                component="search",
                operation_name="resolve_tiktok_url",
                details=str(exc),
            ) from exc
