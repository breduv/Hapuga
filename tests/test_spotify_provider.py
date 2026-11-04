from __future__ import annotations

from typing import Any

import pytest
from requests.exceptions import Timeout as RequestsTimeout

from app.core.config import Settings
from app.errors.base import (
    EmptyResponseError,
    ProviderTimeoutError,
    TrackFetchError,
    UnexpectedResponseError,
)
from app.providers.spotify import SpotifyProvider


def build_provider(settings: Settings) -> SpotifyProvider:
    provider = SpotifyProvider(settings)
    return provider


def track_payload(
    *,
    track_id: str | None = "69kOkLUCkxIZYexIgSG8rq",
    name: str = "Get Lucky",
    artists: list[dict[str, Any]] | None = None,
    images: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": track_id,
        "name": name,
        "artists": artists if artists is not None else [{"name": "Daft Punk"}],
        "album": {"images": images if images is not None else []},
    }


class StubClient:
    def __init__(
        self,
        *,
        track: Any = None,
        search: Any = None,
        error: BaseException | None = None,
    ) -> None:
        self._track = track
        self._search = search
        self._error = error
        self.track_calls: list[str] = []
        self.search_calls: list[dict[str, Any]] = []

    def track(self, track_id: str) -> Any:
        self.track_calls.append(track_id)
        if self._error is not None:
            raise self._error
        return self._track

    def search(self, **kwargs: Any) -> Any:
        self.search_calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._search


def test_convert_track_joins_artists(settings: Settings) -> None:
    provider = build_provider(settings)
    item = track_payload(
        artists=[{"name": "Daft Punk"}, {"name": "Pharrell Williams"}]
    )

    track = provider._convert_track(item)

    assert track.artist == "Daft Punk, Pharrell Williams"
    assert track.title == "Get Lucky"
    assert track.cover_url is None


def test_convert_track_reads_the_first_cover(settings: Settings) -> None:
    provider = build_provider(settings)
    item = track_payload(
        images=[{"url": "https://i.scdn.co/image/big"}, {"url": "small"}]
    )

    assert provider._convert_track(item).cover_url == "https://i.scdn.co/image/big"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"id": None, "name": "Local", "artists": [{"name": "x"}], "album": {}},
        {"id": "1", "name": None, "artists": [{"name": "x"}], "album": {}},
        {"id": "1", "name": "x", "artists": "nope", "album": {}},
        {"id": "1", "name": "x", "artists": [{"name": "x"}], "album": "nope"},
        {"id": "1", "name": "x", "artists": [{"name": 1}], "album": {}},
    ],
)
def test_convert_track_rejects_unusable_payloads(
    settings: Settings, payload: dict[str, Any]
) -> None:
    provider = build_provider(settings)

    with pytest.raises(UnexpectedResponseError):
        provider._convert_track(payload)


def test_convert_track_rejects_invalid_images(settings: Settings) -> None:
    provider = build_provider(settings)

    with pytest.raises(UnexpectedResponseError):
        provider._convert_track(track_payload(images="nope"))


def test_convert_track_rejects_non_string_cover_url(settings: Settings) -> None:
    provider = build_provider(settings)

    with pytest.raises(UnexpectedResponseError):
        provider._convert_track(track_payload(images=[{"url": 42}]))


async def test_get_track_returns_track(settings: Settings) -> None:
    provider = build_provider(settings)
    provider.client = StubClient(  # type: ignore[assignment]
        track=track_payload(images=[{"url": "https://i.scdn.co/image/x"}])
    )

    track = await provider.get_track("spotify:track:69kOkLUCkxIZYexIgSG8rq")

    assert track.track_id == "69kOkLUCkxIZYexIgSG8rq"
    assert track.cover_url == "https://i.scdn.co/image/x"
    assert provider.client.track_calls == ["spotify:track:69kOkLUCkxIZYexIgSG8rq"]


async def test_get_track_reports_missing_track(settings: Settings) -> None:
    provider = build_provider(settings)
    provider.client = StubClient(track=None)  # type: ignore[assignment]

    with pytest.raises(EmptyResponseError):
        await provider.get_track("69kOkLUCkxIZYexIgSG8rq")


async def test_get_track_reports_unexpected_type(settings: Settings) -> None:
    provider = build_provider(settings)
    provider.client = StubClient(track=["nope"])  # type: ignore[assignment]

    with pytest.raises(UnexpectedResponseError):
        await provider.get_track("69kOkLUCkxIZYexIgSG8rq")


async def test_get_track_maps_timeouts(settings: Settings) -> None:
    provider = build_provider(settings)
    provider.client = StubClient(error=RequestsTimeout("slow"))  # type: ignore[assignment]

    with pytest.raises(ProviderTimeoutError):
        await provider.get_track("69kOkLUCkxIZYexIgSG8rq")


async def test_get_track_maps_client_errors(settings: Settings) -> None:
    provider = build_provider(settings)
    provider.client = StubClient(error=RuntimeError("boom"))  # type: ignore[assignment]

    with pytest.raises(TrackFetchError):
        await provider.get_track("69kOkLUCkxIZYexIgSG8rq")


async def test_search_skips_unusable_items(settings: Settings) -> None:
    provider = build_provider(settings)
    provider.client = StubClient(  # type: ignore[assignment]
        search={
            "tracks": {
                "items": [
                    None,
                    track_payload(track_id=None, name="Local file"),
                    track_payload(),
                    track_payload(name="One More Time"),
                ]
            }
        }
    )

    tracks = await provider.search_tracks("daft punk")

    assert [track.title for track in tracks] == ["Get Lucky", "One More Time"]
    assert provider.client.search_calls[0]["limit"] == settings.search_limit


async def test_search_without_usable_items_fails(settings: Settings) -> None:
    provider = build_provider(settings)
    provider.client = StubClient(  # type: ignore[assignment]
        search={"tracks": {"items": [None, track_payload(track_id=None)]}}
    )

    with pytest.raises(EmptyResponseError):
        await provider.search_tracks("daft punk")


async def test_search_reports_empty_results(settings: Settings) -> None:
    provider = build_provider(settings)
    provider.client = StubClient(search={"tracks": {"items": []}})  # type: ignore[assignment]

    with pytest.raises(EmptyResponseError):
        await provider.search_tracks("daft punk")


async def test_search_requires_track_items(settings: Settings) -> None:
    provider = build_provider(settings)
    provider.client = StubClient(search={"tracks": {}})  # type: ignore[assignment]

    with pytest.raises(UnexpectedResponseError):
        await provider.search_tracks("daft punk")


async def test_search_requires_tracks_payload(settings: Settings) -> None:
    provider = build_provider(settings)
    provider.client = StubClient(search={})  # type: ignore[assignment]

    with pytest.raises(UnexpectedResponseError):
        await provider.search_tracks("daft punk")


async def test_search_reports_empty_response(settings: Settings) -> None:
    provider = build_provider(settings)
    provider.client = StubClient(search=None)  # type: ignore[assignment]

    with pytest.raises(EmptyResponseError):
        await provider.search_tracks("daft punk")


async def test_search_maps_timeouts(settings: Settings) -> None:
    provider = build_provider(settings)
    provider.client = StubClient(error=RequestsTimeout("slow"))  # type: ignore[assignment]

    with pytest.raises(ProviderTimeoutError):
        await provider.search_tracks("daft punk")


async def test_search_maps_client_errors(settings: Settings) -> None:
    provider = build_provider(settings)
    provider.client = StubClient(error=RuntimeError("boom"))  # type: ignore[assignment]

    with pytest.raises(TrackFetchError):
        await provider.search_tracks("daft punk")
