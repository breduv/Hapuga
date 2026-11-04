from urllib.parse import urlparse

import aiohttp
from loguru import logger

from app.core.urls import (
    classify_url,
    is_spotify_short_link,
    is_tiktok_short_link,
    normalize_host,
    spotify_track_reference,
)
from app.errors.base import (
    EmptyQueryError,
    InvalidInputKindError,
    UnsupportedUrlError,
    UrlResolutionError,
)
from app.models.search import SPOTIFY_HOSTS, TIKTOK_HOSTS, InputKind, ParsedInput
from app.providers.spotify import SpotifyProvider
from app.providers.ytdlp import YtdlpProvider

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

_NORMALIZED_TIKTOK_HOSTS = {normalize_host(value) for value in TIKTOK_HOSTS}
_NORMALIZED_SPOTIFY_HOSTS = {normalize_host(value) for value in SPOTIFY_HOSTS}


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

        if is_spotify_short_link(host):
            return await self._resolve_spotify_short_link(value)

        if is_tiktok_short_link(host, parsed.path):
            resolved_value = await self._resolve_redirect(
                value,
                component="tiktok",
                public_message=(
                    "Не удалось раскрыть короткую ссылку TikTok. "
                    "Пришли полную ссылку на публикацию"
                ),
            )
            parsed = urlparse(resolved_value)

            if normalize_host(parsed.hostname or "") not in _NORMALIZED_TIKTOK_HOSTS:
                raise UrlResolutionError(
                    "TikTok short URL resolved to an unexpected host",
                    component="search",
                    operation_name="resolve_tiktok_url",
                )

            value = resolved_value
            host = parsed.hostname or host

        kind = classify_url(host, parsed.path, parsed.query)

        if kind is None:
            raise UnsupportedUrlError(
                f"unsupported {normalize_host(host)} URL path: {parsed.path or '/'}",
                component="search",
                operation_name="parse_input",
                public_message=(
                    "Эта ссылка не похожа на отдельную публикацию. "
                    "Пришли ссылку на трек, видео или фотопост"
                ),
            )

        if kind is InputKind.PHOTO and (parsed.hostname or "").lower() == "m.tiktok.com":
            parsed = parsed._replace(netloc="www.tiktok.com")
            value = parsed.geturl()

        if kind is InputKind.SPOTIFY:
            reference = spotify_track_reference(parsed.geturl())

            if reference is None:
                raise UnsupportedUrlError(
                    "Spotify URL is not a single track",
                    component="search",
                    operation_name="parse_input",
                    public_message=(
                        "Ссылка на альбом или плейлист Spotify не поддерживается. "
                        "Пришли ссылку на трек"
                    ),
                )

            return ParsedInput(InputKind.SPOTIFY, reference)

        return ParsedInput(kind, value)

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

    async def _resolve_spotify_short_link(self, url: str) -> ParsedInput:
        """Resolve ``spotify.link`` and keep only the track it points at.

        ``spotipy`` parses ``open.spotify.com`` URLs itself but treats an
        unknown host as a raw track id, so a short link used to surface as a
        generic "could not fetch track" error.
        """
        resolved_value = await self._resolve_redirect(
            url,
            component="spotify",
            public_message=(
                "Не удалось раскрыть короткую ссылку Spotify. "
                "Пришли полную ссылку на трек"
            ),
        )

        if normalize_host(urlparse(resolved_value).hostname or "") not in (
            _NORMALIZED_SPOTIFY_HOSTS
        ):
            raise UrlResolutionError(
                "Spotify short URL resolved to an unexpected host",
                component="search",
                operation_name="resolve_spotify_url",
                public_message="Короткая ссылка ведёт не на Spotify. Пришли ссылку на трек",
            )

        reference = spotify_track_reference(resolved_value)

        if reference is None:
            raise UnsupportedUrlError(
                "Spotify short URL does not resolve to a single track",
                component="search",
                operation_name="resolve_spotify_url",
                public_message=(
                    "Короткая ссылка ведёт на альбом или плейлист. Пришли ссылку на трек"
                ),
            )

        return ParsedInput(InputKind.SPOTIFY, reference)

    async def _resolve_redirect(
        self,
        url: str,
        *,
        component: str,
        public_message: str,
    ) -> str:
        headers = {"User-Agent": _USER_AGENT}
        timeout = aiohttp.ClientTimeout(total=10)
        host = urlparse(url).hostname
        logger.debug("Short URL resolution started component={} host={}", component, host)

        try:
            async with (
                aiohttp.ClientSession(headers=headers, timeout=timeout) as session,
                session.get(url, allow_redirects=True) as response,
            ):
                response.raise_for_status()
                resolved_url = str(response.url)
                logger.debug(
                    "Short URL resolution completed component={} status={} resolved_host={}",
                    component,
                    response.status,
                    response.url.host,
                )
                return resolved_url
        except TimeoutError as exc:
            logger.warning(
                "URL resolution timed out component={} host={}", component, host
            )
            raise UrlResolutionError(
                f"{component} short URL resolution timed out",
                component="search",
                operation_name=f"resolve_{component}_url",
                public_message=public_message,
            ) from exc
        except aiohttp.ClientError as exc:
            logger.warning(
                "URL resolution failed component={} host={} error={}",
                component,
                host,
                exc.__class__.__name__,
            )
            raise UrlResolutionError(
                f"{component} short URL resolution request failed",
                component="search",
                operation_name=f"resolve_{component}_url",
                details=str(exc),
                public_message=public_message,
            ) from exc
        except OSError as exc:
            raise UrlResolutionError(
                f"{component} short URL resolution failed",
                component="search",
                operation_name=f"resolve_{component}_url",
                details=str(exc),
                public_message=public_message,
            ) from exc
