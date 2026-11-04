from __future__ import annotations

import pytest

from app.core.urls import (
    classify_url,
    is_spotify_short_link,
    is_tiktok_short_link,
    normalize_host,
    spotify_track_reference,
)
from app.models.search import InputKind


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("EXAMPLE.com", "example.com"),
        ("www.example.com", "example.com"),
        ("m.example.com", "example.com"),
        ("example.com.", "example.com"),
        ("music.youtube.com", "music.youtube.com"),
        ("www.m.tiktok.com", "tiktok.com"),
    ],
)
def test_normalize_host(host: str, expected: str) -> None:
    assert normalize_host(host) == expected


@pytest.mark.parametrize(
    ("host", "path", "expected"),
    [
        ("www.youtube.com", "/watch", InputKind.YOUTUBE),
        ("youtube.com", "/watch", InputKind.YOUTUBE),
        ("m.youtube.com", "/watch", InputKind.YOUTUBE),
        ("youtu.be", "/dQw4w9WgXcQ", InputKind.YOUTUBE),
        ("www.youtube.com", "/shorts/abc123", InputKind.VIDEO),
        ("www.youtube.com", "/live/abc123", InputKind.VIDEO),
        ("music.youtube.com", "/watch", InputKind.AUDIO),
        ("www.tiktok.com", "/@user/video/123", InputKind.VIDEO),
        ("m.tiktok.com", "/@user/photo/123", InputKind.PHOTO),
        ("vm.tiktok.com", "/ZXY123", None),
        ("www.instagram.com", "/p/Cabc123/", InputKind.VIDEO),
        ("www.instagram.com", "/reel/Cabc123/", InputKind.VIDEO),
        ("www.instagram.com", "/reels/Cabc123/", InputKind.VIDEO),
        ("www.instagram.com", "/tv/Cabc123/", InputKind.VIDEO),
        ("www.pinterest.com", "/pin/123456/", InputKind.VIDEO),
        ("pin.it", "/AbCdEf", InputKind.VIDEO),
        ("vk.com", "/clip-123_456", InputKind.VIDEO),
        ("vk.com", "/clips-123_456", InputKind.VIDEO),
        ("vk.com", "/video-123_456", InputKind.VIDEO),
        ("vkvideo.ru", "/video-123_456", InputKind.VIDEO),
        ("open.spotify.com", "/track/69kOkLUCkxIZYexIgSG8rq", InputKind.SPOTIFY),
        ("spotify.link", "/AbCdEf", InputKind.SPOTIFY),
    ],
)
def test_classify_url_supported(host: str, path: str, expected: InputKind | None) -> None:
    assert classify_url(host, path) is expected


@pytest.mark.parametrize(
    ("host", "path"),
    [
        ("www.instagram.com", "/daftpunk/"),
        ("www.instagram.com", "/"),
        ("www.pinterest.com", "/ideas/"),
        ("vk.com", "/wall-123_456"),
        ("vk.com", "/"),
        ("www.tiktok.com", "/@user"),
        ("example.com", "/watch"),
    ],
)
def test_classify_url_rejects_non_media_paths(host: str, path: str) -> None:
    assert classify_url(host, path) is None


def test_classify_url_accepts_vk_query_clip_id() -> None:
    assert (
        classify_url("vk.com", "/video", "z=video-123_456")
        is InputKind.VIDEO
    )


def test_classify_url_ignores_query_when_path_is_not_media() -> None:
    assert classify_url("vk.com", "/feed", "z=video-123_456") is None


@pytest.mark.parametrize(
    ("host", "path", "expected"),
    [
        ("vm.tiktok.com", "/ZXY123", True),
        ("vt.tiktok.com", "/ZXY123", True),
        ("www.tiktok.com", "/t/ZXY123/", True),
        ("www.tiktok.com", "/@user/video/1", False),
        ("youtube.com", "/watch", False),
    ],
)
def test_is_tiktok_short_link(host: str, path: str, expected: bool) -> None:
    assert is_tiktok_short_link(host, path) is expected


@pytest.mark.parametrize(
    ("host", "expected"),
    [("spotify.link", True), ("SPOTIFY.link", True), ("open.spotify.com", False)],
)
def test_is_spotify_short_link(host: str, expected: bool) -> None:
    assert is_spotify_short_link(host) is expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://open.spotify.com/track/69kOkLUCkxIZYexIgSG8rq",
            "spotify:track:69kOkLUCkxIZYexIgSG8rq",
        ),
        (
            "https://open.spotify.com/intl-de/track/69kOkLUCkxIZYexIgSG8rq?si=abc",
            "spotify:track:69kOkLUCkxIZYexIgSG8rq",
        ),
        ("https://open.spotify.com/album/1ATL5GLyefJaxhQzSPVrLX", None),
        ("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M", None),
        ("https://open.spotify.com/", None),
        ("https://open.spotify.com/track/", None),
    ],
)
def test_spotify_track_reference(url: str, expected: str | None) -> None:
    assert spotify_track_reference(url) == expected
