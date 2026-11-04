from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import pytest
from gallery_dl import exception as gallery_exception

from app.core.config import Settings
from app.errors.base import DownloadError, EmptyResponseError, MediaTooLargeError
from app.providers import gallerydl as gallerydl_module
from app.providers.gallerydl import GallerydlProvider


def build_provider(settings: Settings) -> GallerydlProvider:
    return GallerydlProvider(settings)


@pytest.fixture
def gallery_job(monkeypatch: pytest.MonkeyPatch):
    state: dict[str, Any] = {"status": 0, "files": {}, "error": None, "urls": []}

    class FakeJob:
        def __init__(self, url: str) -> None:
            state["urls"].append(url)
            self.url = url

        def run(self) -> int:
            if state["error"] is not None:
                raise state["error"]
            for name, payload in state["files"].items():
                target = Path(name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            return int(state["status"])

    monkeypatch.setattr(gallerydl_module.job, "DownloadJob", FakeJob)
    monkeypatch.setattr(
        gallerydl_module.config,
        "apply",
        lambda options: contextlib.nullcontext(),
    )
    return state


def test_photos_returns_only_images(
    settings: Settings, gallery_job, tmp_path: Path
) -> None:
    provider = build_provider(settings)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    gallery_job["files"] = {
        str(output_dir / "photo-1.jpg"): b"a",
        str(output_dir / "photo-2.png"): b"b",
        str(output_dir / "video.mp4"): b"c",
        str(output_dir / "photo-3.webp.part"): b"d",
        str(output_dir / "notes.txt"): b"e",
    }

    files = provider._download_photos_sync("https://tiktok.com/@u/photo/1", output_dir)

    assert [path.name for path in files] == ["photo-1.jpg", "photo-2.png"]


def test_photos_without_images_fail(
    settings: Settings, gallery_job, tmp_path: Path
) -> None:
    provider = build_provider(settings)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    gallery_job["files"] = {str(output_dir / "video.mp4"): b"c"}

    with pytest.raises(EmptyResponseError):
        provider._download_photos_sync("https://tiktok.com/@u/photo/1", output_dir)


def test_empty_photo_fails(settings: Settings, gallery_job, tmp_path: Path) -> None:
    provider = build_provider(settings)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    gallery_job["files"] = {str(output_dir / "photo.jpg"): b""}

    with pytest.raises(EmptyResponseError):
        provider._download_photos_sync("https://tiktok.com/@u/photo/1", output_dir)


def test_oversized_photo_fails(
    settings: Settings, gallery_job, tmp_path: Path
) -> None:
    provider = build_provider(settings)
    provider.max_upload_size_bytes = 10
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    gallery_job["files"] = {str(output_dir / "photo.jpg"): b"x" * 11}

    with pytest.raises(MediaTooLargeError):
        provider._download_photos_sync("https://tiktok.com/@u/photo/1", output_dir)


def test_failed_gallery_job_raises(
    settings: Settings, gallery_job, tmp_path: Path
) -> None:
    provider = build_provider(settings)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    gallery_job["status"] = 4

    with pytest.raises(DownloadError):
        provider._download_photos_sync("https://tiktok.com/@u/photo/1", output_dir)


def test_missing_extractor_raises(
    settings: Settings, gallery_job, tmp_path: Path
) -> None:
    provider = build_provider(settings)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    gallery_job["error"] = gallery_exception.NoExtractorError("nope")

    with pytest.raises(DownloadError):
        provider._download_photos_sync("https://example.com/x", output_dir)


def test_video_picks_the_largest_file(
    settings: Settings, gallery_job, tmp_path: Path
) -> None:
    provider = build_provider(settings)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    gallery_job["files"] = {
        str(output_dir / "gallery-dl-1.mp4"): b"small",
        str(output_dir / "gallery-dl-2.webm"): b"much-larger-video",
        str(output_dir / "gallery-dl-3.jpg"): b"image",
    }

    path = provider._download_video_sync("https://tiktok.com/@u/video/1", output_dir)

    assert path.name == "gallery-dl-2.webm"


def test_video_without_files_fails(
    settings: Settings, gallery_job, tmp_path: Path
) -> None:
    provider = build_provider(settings)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    gallery_job["files"] = {str(output_dir / "gallery-dl-1.jpg"): b"image"}

    with pytest.raises(EmptyResponseError):
        provider._download_video_sync("https://tiktok.com/@u/video/1", output_dir)


def test_oversized_video_fails(
    settings: Settings, gallery_job, tmp_path: Path
) -> None:
    provider = build_provider(settings)
    provider.max_upload_size_bytes = 5
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    gallery_job["files"] = {str(output_dir / "gallery-dl-1.mp4"): b"x" * 6}

    with pytest.raises(MediaTooLargeError):
        provider._download_video_sync("https://tiktok.com/@u/video/1", output_dir)


async def test_download_photos_runs_in_a_thread(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = build_provider(settings)
    sentinel = [tmp_path / "a.jpg"]
    monkeypatch.setattr(provider, "_download_photos_sync", lambda url, out: sentinel)

    assert await provider.download_photos("url", tmp_path) == sentinel
