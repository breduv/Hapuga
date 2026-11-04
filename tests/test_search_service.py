from __future__ import annotations

from typing import Any

import pytest

from app.errors.base import (
    EmptyQueryError,
    UnsupportedUrlError,
    UrlResolutionError,
)
from app.models.media import AvailableVideoFormat, TrackInfo
from app.models.search import InputKind
from app.services.search_service import SearchService


class StubSpotify:
    def __init__(
        self,
        tracks: list[TrackInfo] | None = None,
        track: TrackInfo | None = None,
    ) -> None:
        self.tracks = tracks or []
        self.track = track
        self.searched: list[str] = []
        self.requested: list[str] = []

    async def search_tracks(self, query: str) -> list[TrackInfo]:
        self.searched.append(query)
        return self.tracks

    async def get_track(self, track_id_or_url: str) -> TrackInfo:
        self.requested.append(track_id_or_url)
        assert self.track is not None, "stub has no track configured"
        return self.track


class StubYtdlp:
    def __init__(self, formats: list[AvailableVideoFormat] | None = None) -> None:
        self.formats = formats or []
        self.requested: list[str] = []

    async def get_video_formats(self, url: str) -> list[AvailableVideoFormat]:
        self.requested.append(url)
        return self.formats


def build_service(
    *,
    spotify: StubSpotify | None = None,
    ytdlp: StubYtdlp | None = None,
) -> SearchService:
    service = SearchService(
        spotify_provider=spotify or StubSpotify(),  # type: ignore[arg-type]
        ytdlp_provider=ytdlp or StubYtdlp(),  # type: ignore[arg-type]
    )
    return service


async def parse(text: str, **kwargs: Any):  # noqa: ANN201
    service = build_service(**kwargs)
    return await service._parse_input(text)


async def test_empty_query_is_rejected() -> None:
    with pytest.raises(EmptyQueryError):
        await parse("   ")


async def test_plain_text_is_a_search_query() -> None:
    parsed = await parse("  Daft Punk Get Lucky  ")
    assert parsed.source is InputKind.QUERY
    assert parsed.query == "Daft Punk Get Lucky"


async def test_scheme_less_text_stays_a_query() -> None:
    parsed = await parse("daft punk - get lucky")
    assert parsed.source is InputKind.QUERY


async def test_youtube_watch_link_selects_formats() -> None:
    parsed = await parse("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert parsed.source is InputKind.YOUTUBE
    assert parsed.query == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


async def test_youtube_short_is_a_direct_video() -> None:
    parsed = await parse("https://youtube.com/shorts/abc123")
    assert parsed.source is InputKind.VIDEO


async def test_youtube_music_is_audio() -> None:
    parsed = await parse("https://music.youtube.com/watch?v=dQw4w9WgXcQ")
    assert parsed.source is InputKind.AUDIO


async def test_spotify_url_becomes_a_track_uri() -> None:
    parsed = await parse("https://open.spotify.com/track/69kOkLUCkxIZYexIgSG8rq?si=x")
    assert parsed.source is InputKind.SPOTIFY
    assert parsed.query == "spotify:track:69kOkLUCkxIZYexIgSG8rq"


async def test_spotify_album_link_is_rejected_with_a_track_hint() -> None:
    with pytest.raises(UnsupportedUrlError) as excinfo:
        await parse("https://open.spotify.com/album/1ATL5GLyefJaxhQzSPVrLX")

    assert "альбом" in excinfo.value.public_message


async def test_tiktok_video_link() -> None:
    parsed = await parse("https://www.tiktok.com/@user/video/7311")
    assert parsed.source is InputKind.VIDEO


async def test_tiktok_photo_link() -> None:
    parsed = await parse("https://www.tiktok.com/@user/photo/7311")
    assert parsed.source is InputKind.PHOTO


async def test_tiktok_photo_link_on_mobile_host_is_rewritten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = await parse("https://m.tiktok.com/@user/photo/7311")
    assert parsed.source is InputKind.PHOTO
    assert parsed.query == "https://www.tiktok.com/@user/photo/7311"


async def test_tiktok_profile_is_not_a_media_link() -> None:
    with pytest.raises(UnsupportedUrlError):
        await parse("https://www.tiktok.com/@user")


async def test_tiktok_short_link_is_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(self, url: str, **kwargs: Any) -> str:
        assert url == "https://vm.tiktok.com/ZXY123/"
        return "https://www.tiktok.com/@user/video/7311"

    monkeypatch.setattr(SearchService, "_resolve_redirect", fake_resolve)
    parsed = await parse("https://vm.tiktok.com/ZXY123/")
    assert parsed.source is InputKind.VIDEO
    assert parsed.query == "https://www.tiktok.com/@user/video/7311"


async def test_tiktok_short_link_to_foreign_host_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_resolve(self, url: str, **kwargs: Any) -> str:
        return "https://evil.example.com/@user/video/7311"

    monkeypatch.setattr(SearchService, "_resolve_redirect", fake_resolve)

    with pytest.raises(UrlResolutionError):
        await parse("https://vm.tiktok.com/ZXY123/")


async def test_spotify_short_link_is_resolved_to_a_track(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_resolve(self, url: str, **kwargs: Any) -> str:
        return "https://open.spotify.com/intl-de/track/69kOkLUCkxIZYexIgSG8rq"

    monkeypatch.setattr(SearchService, "_resolve_redirect", fake_resolve)
    parsed = await parse("https://spotify.link/AbCdEf")
    assert parsed.source is InputKind.SPOTIFY
    assert parsed.query == "spotify:track:69kOkLUCkxIZYexIgSG8rq"


async def test_spotify_short_link_to_album_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_resolve(self, url: str, **kwargs: Any) -> str:
        return "https://open.spotify.com/album/1ATL5GLyefJaxhQzSPVrLX"

    monkeypatch.setattr(SearchService, "_resolve_redirect", fake_resolve)

    with pytest.raises(UnsupportedUrlError):
        await parse("https://spotify.link/AbCdEf")


async def test_spotify_short_link_to_foreign_host_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_resolve(self, url: str, **kwargs: Any) -> str:
        return "https://example.com/track/69kOkLUCkxIZYexIgSG8rq"

    monkeypatch.setattr(SearchService, "_resolve_redirect", fake_resolve)

    with pytest.raises(UrlResolutionError):
        await parse("https://spotify.link/AbCdEf")


async def test_instagram_post_is_a_video() -> None:
    parsed = await parse("https://www.instagram.com/reel/Cabc123/")
    assert parsed.source is InputKind.VIDEO


async def test_instagram_profile_is_rejected_before_download() -> None:
    with pytest.raises(UnsupportedUrlError) as excinfo:
        await parse("https://www.instagram.com/daftpunk/")

    assert "публикацию" in excinfo.value.public_message


async def test_pinterest_pin_is_a_video() -> None:
    parsed = await parse("https://www.pinterest.com/pin/123456/")
    assert parsed.source is InputKind.VIDEO


async def test_vk_video_link_is_supported() -> None:
    parsed = await parse("https://vk.com/video-220754053_456239018")
    assert parsed.source is InputKind.VIDEO


async def test_vk_clip_link_is_supported() -> None:
    parsed = await parse("https://vkvideo.ru/clip-220754053_456239018")
    assert parsed.source is InputKind.VIDEO


async def test_vk_wall_post_is_rejected() -> None:
    with pytest.raises(UnsupportedUrlError):
        await parse("https://vk.com/wall-220754053_1234")


async def test_unknown_host_is_rejected() -> None:
    with pytest.raises(UnsupportedUrlError):
        await parse("https://example.com/video/1")


async def test_search_returns_spotify_buttons() -> None:
    track = TrackInfo(
        title="Get Lucky",
        artist="Daft Punk",
        cover_url=None,
        track_id="69kOkLUCkxIZYexIgSG8rq",
    )
    spotify = StubSpotify(tracks=[track])
    service = build_service(spotify=spotify)

    results = await service.search("daft punk")

    assert results == {"sp:69kOkLUCkxIZYexIgSG8rq": "Daft Punk - Get Lucky"}
    assert spotify.searched == ["daft punk"]


async def test_search_returns_youtube_format_buttons() -> None:
    formats = [
        AvailableVideoFormat(video_id="vid", format_id="137", height=1080),
        AvailableVideoFormat(video_id="vid", format_id="136", height=720),
    ]
    ytdlp = StubYtdlp(formats=formats)
    service = build_service(ytdlp=ytdlp)

    results = await service.search("https://www.youtube.com/watch?v=vid")

    assert results == {
        "yt:vid:137": "1080p",
        "yt:vid:136": "720p",
        "yt:vid:-1": "Только звук",
    }
    assert ytdlp.requested == ["https://www.youtube.com/watch?v=vid"]


async def test_search_resolves_a_spotify_track() -> None:
    track = TrackInfo(
        title="Get Lucky",
        artist="Daft Punk",
        cover_url="https://i.scdn.co/image/cover.jpg",
        track_id="69kOkLUCkxIZYexIgSG8rq",
    )
    spotify = StubSpotify(track=track)
    service = build_service(spotify=spotify)

    results = await service.search("https://open.spotify.com/track/69kOkLUCkxIZYexIgSG8rq")

    assert results == {
        "audio": "Daft Punk - Get Lucky",
        "title": "Get Lucky",
        "artist": "Daft Punk",
        "cover_url": "https://i.scdn.co/image/cover.jpg",
    }
    assert spotify.requested == ["spotify:track:69kOkLUCkxIZYexIgSG8rq"]


async def test_search_without_cover_url_omits_the_key() -> None:
    track = TrackInfo(
        title="Get Lucky",
        artist="Daft Punk",
        cover_url=None,
        track_id="69kOkLUCkxIZYexIgSG8rq",
    )
    service = build_service(spotify=StubSpotify(track=track))

    results = await service.search("https://open.spotify.com/track/69kOkLUCkxIZYexIgSG8rq")

    assert "cover_url" not in results


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("https://www.tiktok.com/@user/video/7311", "video"),
        ("https://www.tiktok.com/@user/photo/7311", "photo"),
        ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", "audio"),
    ],
)
async def test_search_maps_direct_media_kinds(text: str, key: str) -> None:
    service = build_service()
    results = await service.search(text)
    assert results == {key: text}
