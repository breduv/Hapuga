"""Shared fixtures.

The suite is fully offline: providers are replaced by stubs, and the only
external binary it touches is ``ffmpeg`` (skipped when absent), which the
application already requires.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import Settings, get_settings  # noqa: E402
from app.models.media import TrackInfo  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        bot_token="123456:test-token",
        spotify_client_id="client-id",
        spotify_client_secret="client-secret",
    )


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "media"
    directory.mkdir()
    return directory


@pytest.fixture
def mp3_file(tmp_path: Path) -> Path:
    """A real, empty-tagged mp3 that mutagen can write to."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required to build the audio fixture")

    path = tmp_path / "track.mp3"
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            "0.2",
            "-q:a",
            "9",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0 or not path.is_file():
        pytest.skip(f"could not build the audio fixture: {result.stderr.strip()}")

    return path


@pytest.fixture
def jpeg_bytes() -> bytes:
    return b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF" + b"\x00" * 32


@pytest.fixture
def png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def track() -> TrackInfo:
    return TrackInfo(
        title="Get Lucky",
        artist="Daft Punk",
        cover_url="https://i.scdn.co/image/cover.jpg",
        track_id="69kOkLUCkxIZYexIgSG8rq",
    )
