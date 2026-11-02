import asyncio

from loguru import logger
from requests.exceptions import Timeout as RequestsTimeout
from spotipy.client import Spotify
from spotipy.oauth2 import SpotifyClientCredentials

from app.core.config import Settings
from app.errors.base import (
    EmptyResponseError,
    ProviderTimeoutError,
    TrackFetchError,
    UnexpectedResponseError,
)
from app.models.media import TrackInfo


class SpotifyProvider:
    def __init__(self, settings: Settings) -> None:
        self.client = Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=settings.spotify_client_id,
                client_secret=settings.spotify_client_secret.get_secret_value(),
                proxies={
                    "http": settings.media_proxy.get_secret_value()
                    if settings.media_proxy
                    else None,
                    "https": settings.media_proxy.get_secret_value()
                    if settings.media_proxy
                    else None,
                },
                requests_timeout=20,
            ),
            proxies={
                "http": settings.media_proxy.get_secret_value()
                if settings.media_proxy
                else None,
                "https": settings.media_proxy.get_secret_value()
                if settings.media_proxy
                else None,
            },
            requests_timeout=20,
            retries=3,
        )

        self.search_limit = settings.search_limit

    def _convert_track(self, item: dict) -> TrackInfo:

        album = item.get("album")
        artists = item.get("artists")
        title = item.get("name")
        track_id = item.get("id")

        if (
            not isinstance(album, dict)
            or not isinstance(artists, list)
            or not isinstance(title, str)
            or not isinstance(track_id, str)
        ):
            raise UnexpectedResponseError(
                "Spotify returned invalid track payload",
                component="spotify",
                operation_name="convert_track",
                public_message="Spotify вернул некорректные данные трека. Попробуй позже",
            )

        images = album.get("images")

        if images is None:
            images = []
        elif not isinstance(images, list):
            raise UnexpectedResponseError(
                "Spotify returned invalid track images",
                component="spotify",
                operation_name="convert_track",
                public_message="Spotify вернул некорректные данные трека. Попробуй позже",
            )

        artist_names: list[str] = []
        for artist in artists:
            if not isinstance(artist, dict) or not isinstance(artist.get("name"), str):
                raise UnexpectedResponseError(
                    "Spotify returned invalid track artists",
                    component="spotify",
                    operation_name="convert_track",
                    public_message="Spotify вернул некорректные данные трека. Попробуй позже",
                )

            artist_names.append(artist["name"])

        cover_url = None
        if images:
            image = images[0]

            if not isinstance(image, dict):
                raise UnexpectedResponseError(
                    "Spotify returned invalid track image",
                    component="spotify",
                    operation_name="convert_track",
                    public_message="Spotify вернул некорректные данные трека. Попробуй позже",
                )

            raw_cover_url = image.get("url")
            if raw_cover_url is not None and not isinstance(raw_cover_url, str):
                raise UnexpectedResponseError(
                    "Spotify returned invalid track cover URL",
                    component="spotify",
                    operation_name="convert_track",
                    public_message="Spotify вернул некорректные данные трека. Попробуй позже",
                )

            cover_url = raw_cover_url

        return TrackInfo(
            title=title,
            artist=", ".join(artist_names),
            cover_url=cover_url,
            track_id=track_id,
        )

    async def get_track(self, track_id_or_url: str) -> TrackInfo:
        logger.debug("Spotify get_track request started")
        try:
            item = await asyncio.to_thread(
                self.client.track,
                track_id_or_url,
            )
        except RequestsTimeout as exc:
            raise ProviderTimeoutError(
                "Spotify get track request timed out",
                component="spotify",
                operation_name="get_track",
                details=str(exc),
                public_message="Spotify не ответил вовремя. Попробуй получить трек позже",
            ) from exc
        except Exception as exc:
            raise TrackFetchError(
                f"Spotify get track failed for id or url: {track_id_or_url}",
                component="spotify",
                operation_name="get_track",
                details=str(exc),
                public_message=(
                    "Не смог получить трек из Spotify. "
                    "Проверь ссылку или попробуй позже"
                ),
            ) from exc

        if item is None:
            raise EmptyResponseError(
                f"Spotify returned no track for id or url: {track_id_or_url}",
                component="spotify",
                operation_name="get_track",
                public_message="Spotify вернул пустой ответ для этого трека. Попробуй позже",
            )

        if not isinstance(item, dict):
            raise UnexpectedResponseError(
                f"Spotify returned unexpected track response type: {type(item).__name__}",
                component="spotify",
                operation_name="get_track",
                public_message="Spotify вернул некорректные данные трека. Попробуй позже",
            )

        track = self._convert_track(item)
        logger.debug("Spotify get_track request completed track_id={}", track.track_id)
        return track

    async def search_tracks(self, query: str) -> list[TrackInfo]:
        logger.debug("Spotify search request started query_length={}", len(query))
        try:
            response = await asyncio.to_thread(
                self.client.search,
                q=query,
                type="track",
                limit=self.search_limit,
            )
        except RequestsTimeout as exc:
            raise ProviderTimeoutError(
                "Spotify search request timed out",
                component="spotify",
                operation_name="search_tracks",
                details=str(exc),
                public_message="Spotify не ответил вовремя. Попробуй повторить поиск позже",
            ) from exc
        except Exception as exc:
            raise TrackFetchError(
                f"Spotify search failed for query: {query}",
                component="spotify",
                operation_name="search_tracks",
                details=str(exc),
                public_message="Не смог выполнить поиск в Spotify. Попробуй позже",
            ) from exc

        if response is None:
            raise EmptyResponseError(
                f"Spotify search returned no response for query: {query}",
                component="spotify",
                operation_name="search_tracks",
                public_message="Spotify вернул пустой ответ. Попробуй позже",
            )

        if not isinstance(response, dict):
            raise UnexpectedResponseError(
                f"Spotify returned unexpected search response type: {type(response).__name__}",
                component="spotify",
                operation_name="search_tracks",
                public_message="Spotify вернул некорректные данные поиска. Попробуй позже",
            )

        tracks_payload = response.get("tracks")

        if not isinstance(tracks_payload, dict):
            raise UnexpectedResponseError(
                "Spotify returned search response without tracks payload",
                component="spotify",
                operation_name="search_tracks",
                public_message="Spotify вернул некорректные данные поиска. Попробуй позже",
            )

        items = tracks_payload.get("items")

        if not isinstance(items, list):
            raise UnexpectedResponseError(
                "Spotify returned search response without track items",
                component="spotify",
                operation_name="search_tracks",
                public_message="Spotify вернул некорректные данные поиска. Попробуй позже",
            )

        if not items:
            raise EmptyResponseError(
                f"Spotify search returned no tracks for query: {query}",
                component="spotify",
                operation_name="search_tracks",
                public_message="В Spotify ничего не найдено по этому запросу",
            )

        tracks = [self._convert_track(item) for item in items]
        logger.debug("Spotify search request completed results_count={}", len(tracks))
        return tracks
