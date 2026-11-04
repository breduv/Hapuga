from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3

from app.core.config import Settings
from app.errors.base import DownloadError, UnexpectedResponseError
from app.providers import cover as cover_module
from app.providers.cover import CoverProvider


def build_provider(settings: Settings) -> CoverProvider:
    return CoverProvider(settings)


@pytest.fixture
def fake_http(monkeypatch: pytest.MonkeyPatch):
    state: dict[str, Any] = {"status": 200, "body": b"", "requests": []}

    class FakeContent:
        async def read(self, limit: int) -> bytes:
            return state["body"][:limit]

    class FakeResponse:
        def __init__(self) -> None:
            self.status = state["status"]
            self.content = FakeContent()

        async def __aenter__(self) -> FakeResponse:
            return self

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

    class FakeSession:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

        def get(self, url: str, **kwargs: Any) -> FakeResponse:
            state["requests"].append({"url": url, "kwargs": kwargs})
            return FakeResponse()

    monkeypatch.setattr(cover_module.aiohttp, "ClientSession", FakeSession)
    return state


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"\xff\xd8\xff\xe0rest", ".jpg"),
        (b"\x89PNG\r\n\x1a\nrest", ".png"),
        (b"RIFF\x00\x00\x00\x00WEBPrest", ".webp"),
        (b"<html>not an image</html>", None),
        (b"GIF89a", None),
        (b"", None),
    ],
)
def test_sniff_image_suffix(payload: bytes, expected: str | None) -> None:
    assert CoverProvider.sniff_image_suffix(payload) == expected


async def test_download_cover_writes_jpeg(
    settings: Settings, fake_http, output_dir: Path, jpeg_bytes: bytes
) -> None:
    provider = build_provider(settings)
    fake_http["body"] = jpeg_bytes

    path = await provider.download_cover("https://i.scdn.co/image/x", output_dir)

    assert path.name == "cover.jpg"
    assert path.read_bytes() == jpeg_bytes
    assert fake_http["requests"][0]["url"] == "https://i.scdn.co/image/x"


async def test_download_cover_uses_the_real_extension(
    settings: Settings, fake_http, output_dir: Path, png_bytes: bytes
) -> None:
    provider = build_provider(settings)
    fake_http["body"] = png_bytes

    path = await provider.download_cover("https://example.com/cover", output_dir)

    assert path.name == "cover.png"


async def test_download_cover_rejects_non_images(
    settings: Settings, fake_http, output_dir: Path
) -> None:
    provider = build_provider(settings)
    fake_http["body"] = b"<html>cloudflare</html>"

    with pytest.raises(DownloadError) as excinfo:
        await provider.download_cover("https://example.com/cover", output_dir)

    assert "image" in excinfo.value.message
    assert not (output_dir / "cover.jpg").exists()


async def test_download_cover_rejects_empty_payloads(
    settings: Settings, fake_http, output_dir: Path
) -> None:
    provider = build_provider(settings)
    fake_http["body"] = b""

    with pytest.raises(DownloadError):
        await provider.download_cover("https://example.com/cover", output_dir)


async def test_download_cover_rejects_bad_status(
    settings: Settings, fake_http, output_dir: Path
) -> None:
    provider = build_provider(settings)
    fake_http["status"] = 404
    fake_http["body"] = b"nope"

    with pytest.raises(DownloadError):
        await provider.download_cover("https://example.com/cover", output_dir)


async def test_download_cover_rejects_oversized_payloads(
    settings: Settings,
    fake_http,
    output_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = build_provider(settings)
    monkeypatch.setattr(CoverProvider, "MAX_COVER_BYTES", 4)
    fake_http["body"] = b"\xff\xd8\xff\xe0" + b"x" * 8

    with pytest.raises(DownloadError) as excinfo:
        await provider.download_cover("https://example.com/cover", output_dir)

    assert "exceeds" in excinfo.value.message


def test_detect_mime_maps_suffixes() -> None:
    provider = CoverProvider.__new__(CoverProvider)

    assert provider._detect_mime(Path("a.jpg")) == "image/jpeg"
    assert provider._detect_mime(Path("a.JPEG")) == "image/jpeg"
    assert provider._detect_mime(Path("a.png")) == "image/png"
    assert provider._detect_mime(Path("a.webp")) == "image/webp"

    with pytest.raises(UnexpectedResponseError):
        provider._detect_mime(Path("a.gif"))


async def test_set_mp3_metadata_writes_tags(
    settings: Settings, mp3_file: Path
) -> None:
    provider = build_provider(settings)

    await provider.set_mp3_metadata(mp3_file, title="Get Lucky", artist="Daft Punk")

    tags = EasyID3(mp3_file)
    assert tags["title"] == ["Get Lucky"]
    assert tags["artist"] == ["Daft Punk"]


async def test_set_mp3_cover_embeds_artwork(
    settings: Settings, mp3_file: Path, tmp_path: Path, jpeg_bytes: bytes
) -> None:
    provider = build_provider(settings)
    cover_path = tmp_path / "cover.jpg"
    cover_path.write_bytes(jpeg_bytes)

    await provider.set_mp3_cover(mp3_file, cover_path)

    tags = ID3(mp3_file)
    pictures = tags.getall("APIC")
    assert len(pictures) == 1
    assert pictures[0].mime == "image/jpeg"
    assert pictures[0].data == jpeg_bytes


async def test_set_mp3_cover_replaces_existing_artwork(
    settings: Settings, mp3_file: Path, tmp_path: Path, jpeg_bytes: bytes
) -> None:
    provider = build_provider(settings)
    first = tmp_path / "first.jpg"
    first.write_bytes(jpeg_bytes)
    second = tmp_path / "second.png"
    second.write_bytes(b"\x89PNG\r\n\x1a\n" + b"y" * 16)

    await provider.set_mp3_cover(mp3_file, first)
    await provider.set_mp3_cover(mp3_file, second)

    pictures = ID3(mp3_file).getall("APIC")
    assert len(pictures) == 1
    assert pictures[0].mime == "image/png"
