"""Host and path rules that decide what a URL points at.

Kept separate from :class:`app.services.search_service.SearchService` so the
routing table is a pure function: it never touches the network and can be
exercised directly in tests.
"""

from __future__ import annotations

from urllib.parse import urlparse

from app.models.search import (
    INSTAGRAM_HOSTS,
    PINTEREST_HOSTS,
    SPOTIFY_HOSTS,
    TIKTOK_HOSTS,
    VK_HOSTS,
    YOUTUBE_MUSIC_HOSTS,
    InputKind,
)


def normalize_host(host: str) -> str:
    """Lower-case a host and drop any ``www.``/``m.`` prefixes.

    ``vm.tiktok.com`` is left alone (it is not the mobile prefix), while
    ``www.m.tiktok.com`` collapses to ``tiktok.com``.
    """
    value = host.strip().lower().rstrip(".")

    prefixes = ("www.", "m.")
    stripped = True

    while stripped:
        stripped = False
        for prefix in prefixes:
            if value.startswith(prefix):
                candidate = value[len(prefix) :]
                # Only strip the prefix when a registrable name is left behind.
                if "." in candidate:
                    value = candidate
                    stripped = True
                    break

    return value


def _path_starts_with(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path.startswith(prefix) for prefix in prefixes)


# Paths that point at a single post rather than at a profile or a feed.
INSTAGRAM_MEDIA_PREFIXES = ("/p/", "/reel/", "/reels/", "/tv/")
PINTEREST_MEDIA_PREFIXES = ("/pin/",)
VK_MEDIA_PREFIXES = ("/clip", "/video", "/videos", "/video_ext")
YOUTUBE_SHORT_PREFIXES = ("/shorts/", "/live/")
YOUTUBE_MUSIC_PATH_HINTS = ("/watch", "/playlist", "/shorts/")


def classify_url(host: str, path: str, query: str = "") -> InputKind | None:
    """Map a host + path pair onto an :class:`InputKind`.

    ``None`` means "the host is known but this path is not a media item" -- the
    caller turns that into an ``UnsupportedUrlError`` instead of handing a
    profile page to yt-dlp and reporting a download failure for it.
    """
    normalized = normalize_host(host)
    haystack = f"{path}?{query}" if query else path

    if normalized in {normalize_host(value) for value in YOUTUBE_MUSIC_HOSTS}:
        return InputKind.AUDIO

    if normalized == "youtu.be":
        return InputKind.YOUTUBE

    if normalized == normalize_host("youtube.com"):
        if _path_starts_with(path, YOUTUBE_SHORT_PREFIXES):
            return InputKind.VIDEO
        return InputKind.YOUTUBE

    if normalized in {normalize_host(value) for value in TIKTOK_HOSTS}:
        if "/photo/" in path:
            return InputKind.PHOTO
        if "/video/" in path:
            return InputKind.VIDEO
        return None

    if normalized in {normalize_host(value) for value in INSTAGRAM_HOSTS}:
        if _path_starts_with(path, INSTAGRAM_MEDIA_PREFIXES):
            return InputKind.VIDEO
        return None

    if normalized in {normalize_host(value) for value in PINTEREST_HOSTS}:
        if normalized == "pin.it":
            return InputKind.VIDEO
        if _path_starts_with(path, PINTEREST_MEDIA_PREFIXES):
            return InputKind.VIDEO
        return None

    if normalized in {normalize_host(value) for value in VK_HOSTS}:
        # ``vk.com/video?z=video-1_2`` keeps the clip id in the query string.
        if _path_starts_with(haystack, VK_MEDIA_PREFIXES):
            return InputKind.VIDEO
        return None

    if normalized in {normalize_host(value) for value in SPOTIFY_HOSTS}:
        return InputKind.SPOTIFY

    return None


def is_tiktok_short_link(host: str, path: str) -> bool:
    """True for ``vm.tiktok.com``/``vt.tiktok.com`` and ``tiktok.com/t/`` links."""
    original = host.strip().lower().rstrip(".")
    if original in {value.lower() for value in ("vm.tiktok.com", "vt.tiktok.com")}:
        return True
    return normalize_host(host) == "tiktok.com" and path.startswith("/t/")


def is_spotify_short_link(host: str) -> bool:
    """True for ``spotify.link`` short links, which spotipy cannot resolve."""
    return normalize_host(host) == normalize_host("spotify.link")


def spotify_track_reference(resolved_url: str) -> str | None:
    """Return a ``spotify:track:<id>`` URI for a resolved Spotify URL.

    ``spotipy`` silently forwards anything it cannot parse as a raw base-62 id,
    which turns a resolved landing page into a confusing 404, so the caller is
    given an explicit answer instead.
    """
    parsed = urlparse(resolved_url)
    segments = [segment for segment in parsed.path.split("/") if segment]

    if segments and segments[0].startswith("intl-"):
        segments = segments[1:]

    if len(segments) >= 2 and segments[0] == "track":
        candidate = segments[1].split("?")[0]
        if candidate and candidate.isalnum():
            return f"spotify:track:{candidate}"
    return None
