from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.errors.base import DownloadError
from app.providers.cover import CoverProvider
from app.services.download_service import DownloadService


class StubYtdlp:
    def __init__(self, error: Exception | None = None, file_name: str = "media.mp4"):
        self.error = error
        self.file_name = file_name
        self.audio_calls: list[str] = []
        self.video_calls: list[tuple[str, str | None]] = []

    async def download_audio(self, query_or_url: str, output_dir: Path):
        self.audio_calls.append(query_or_url)
        path = output_dir / "audio.mp3"
        path.write_bytes(b"audio")
        return path, None

    async def download_video(
        self, url: str, output_dir: Path, format_id: str | None = None
    ):
        self.video_calls.append((url, format_id))
        if self.error is not None:
            raise self.error
        path = output_dir / self.file_name
        path.write_bytes(b"video")
        return path


class StubGallery:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.video_calls: list[str] = []
        self.photo_calls: list[str] = []

    async def download_video(self, url: str, output_dir: Path):
        self.video_calls.append(url)
        if self.error is not None:
            raise self.error
        path = output_dir / "gallery.mp4"
        path.write_bytes(b"video")
        return path

    async def download_photos(self, url: str, output_dir: Path):
        self.photo_calls.append(url)
        return [output_dir / "photo.jpg"]


class StubSpotify:
    def __init__(self, track: Any) -> None:
        self.track = track

    async def get_track(self, track_id: str):
        return self.track


class StubCover:
    def __init__(self, *, download_error: Exception | None = None, embed_error: Exception | None = None):
        self.download_error = download_error
        self.embed_error = embed_error
        self.metadata_calls: list[tuple[str, str]] = []
        self.download_calls: list[str] = []
        self.embed_calls: list[Path] = []

    async def set_mp3_metadata(self, path: Path, *, title: str, artist: str) -> None:
        self.metadata_calls.append((title, artist))

    async def download_cover(self, url: str, output_dir: Path, filename: str = "cover.jpg") -> Path:
        self.download_calls.append(url)
        if self.download_error is not None:
            raise self.download_error
        path = output_dir / "cover.jpg"
        path.write_bytes(b"cover")
        return path

    async def set_mp3_cover(self, path: Path, cover_path: Path) -> None:
        self.embed_calls.append(cover_path)
        if self.embed_error is not None:
            raise self.embed_error


def build_service(
    *,
    ytdlp: StubYtdlp | None = None,
    gallery: StubGallery | None = None,
    spotify: Any = None,
    cover: StubCover | None = None,
) -> DownloadService:
    return DownloadService(
        spotify_provider=spotify or StubSpotify(None),  # type: ignore[arg-type]
        ytdlp_provider=ytdlp or StubYtdlp(),  # type: ignore[arg-type]
        cover_provaider=cover or StubCover(),  # type: ignore[arg-type]
        gallerydl_provider=gallery or StubGallery(),  # type: ignore[arg-type]
    )


async def test_audio_download_tags_and_embeds_cover(
    settings: Settings, tmp_path: Path
) -> None:
    ytdlp = StubYtdlp()
    cover = StubCover()
    service = build_service(ytdlp=ytdlp, cover=cover)

    audio, cover_path = await service.download_media(
        {
            "audio": "daft punk get lucky",
            "title": "Get Lucky",
            "artist": "Daft Punk",
            "cover_url": "https://i.scdn.co/image/x",
        },
        tmp_path,
    )

    assert audio.name == "audio.mp3"
    assert cover_path is not None
    assert ytdlp.audio_calls == ["daft punk get lucky"]
    assert cover.metadata_calls == [("Get Lucky", "Daft Punk")]
    assert cover.download_calls == ["https://i.scdn.co/image/x"]
    assert len(cover.embed_calls) == 1


async def test_cover_failures_do_not_lose_the_download(
    settings: Settings, tmp_path: Path
) -> None:
    cover = StubCover(
        download_error=DownloadError(
            "no cover", component="cover", operation_name="download_cover"
        )
    )
    service = build_service(cover=cover)

    audio, cover_path = await service.download_media(
        {"audio": "track", "title": "Get Lucky", "artist": "Daft Punk", "cover_url": "x"},
        tmp_path,
    )

    assert audio.is_file()
    assert cover_path is None
    assert cover.embed_calls == []


async def test_cover_embedding_failures_do_not_lose_the_download(
    settings: Settings, tmp_path: Path
) -> None:
    cover = StubCover(
        embed_error=DownloadError(
            "no embed", component="cover", operation_name="set_mp3_cover"
        )
    )
    service = build_service(cover=cover)

    audio, cover_path = await service.download_media(
        {"audio": "track", "cover_url": "https://example.com/c.jpg"}, tmp_path
    )

    assert audio.is_file()
    assert cover_path is not None


async def test_video_download_returns_path(
    settings: Settings, tmp_path: Path
) -> None:
    ytdlp = StubYtdlp()
    service = build_service(ytdlp=ytdlp)

    video, cover = await service.download_media(
        {"video": "https://tiktok.com/@u/video/1"}, tmp_path
    )

    assert video.name == "media.mp4"
    assert cover is None
    assert ytdlp.video_calls == [("https://tiktok.com/@u/video/1", None)]


async def test_tiktok_failure_falls_back_to_gallery_dl(
    settings: Settings, tmp_path: Path
) -> None:
    ytdlp = StubYtdlp(error=DownloadError("boom", component="yt-dlp", operation_name="download_video"))
    gallery = StubGallery()
    service = build_service(ytdlp=ytdlp, gallery=gallery)

    video, _ = await service.download_media(
        {"video": "https://www.tiktok.com/@u/video/1"}, tmp_path
    )

    assert video.name == "gallery.mp4"
    assert gallery.video_calls == ["https://www.tiktok.com/@u/video/1"]


async def test_non_tiktok_failure_is_not_retried(
    settings: Settings, tmp_path: Path
) -> None:
    ytdlp = StubYtdlp(error=DownloadError("boom", component="yt-dlp", operation_name="download_video"))
    gallery = StubGallery()
    service = build_service(ytdlp=ytdlp, gallery=gallery)

    with pytest.raises(DownloadError):
        await service.download_media({"video": "https://vimeo.com/1"}, tmp_path)

    assert gallery.video_calls == []


async def test_spotify_download_uses_track_metadata(
    settings: Settings, tmp_path: Path
) -> None:
    track = _Track(title="Get Lucky", artist="Daft Punk", cover_url=None)
    ytdlp = StubYtdlp()
    cover = StubCover()
    service = build_service(ytdlp=ytdlp, spotify=StubSpotify(track), cover=cover)

    audio, cover_path = await service.download_on_spotify("69kOkLUCkxIZYexIgSG8rq", tmp_path)

    assert ytdlp.audio_calls == ["Daft Punk - Get Lucky"]
    assert cover.metadata_calls == [("Get Lucky", "Daft Punk")]
    assert cover_path is None


async def test_youtube_audio_selection(
    settings: Settings, tmp_path: Path
) -> None:
    ytdlp = StubYtdlp()
    service = build_service(ytdlp=ytdlp)

    audio, _ = await service.download_on_youtube(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "-1", tmp_path
    )

    assert audio.name == "audio.mp3"
    assert ytdlp.audio_calls == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]


async def test_youtube_video_selection(
    settings: Settings, tmp_path: Path
) -> None:
    ytdlp = StubYtdlp()
    service = build_service(ytdlp=ytdlp)

    video, cover = await service.download_on_youtube(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "137", tmp_path
    )

    assert video.name == "media.mp4"
    assert cover is None
    assert ytdlp.video_calls == [("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "137")]


async def test_gallery_download_returns_every_photo(
    settings: Settings, tmp_path: Path
) -> None:
    gallery = StubGallery()
    service = build_service(gallery=gallery)

    photos = await service.download_photos("https://tiktok.com/@u/photo/1", tmp_path)

    assert len(photos) == 1
    assert gallery.photo_calls == ["https://tiktok.com/@u/photo/1"]


class _Track:
    def __init__(self, *, title: str, artist: str, cover_url: str | None) -> None:
        self.title = title
        self.artist = artist
        self.cover_url = cover_url
