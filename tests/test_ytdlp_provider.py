from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from yt_dlp.utils import DownloadError as YtdlpDownloadError

from app.core.config import Settings
from app.errors.base import (
    DownloadError,
    EmptyResponseError,
    MediaTooLargeError,
    UnexpectedResponseError,
)
from app.providers import ytdlp as ytdlp_module
from app.providers.ytdlp import MediaProbe, YtdlpProvider


def build_provider(settings: Settings) -> YtdlpProvider:
    return YtdlpProvider(settings)


class FakeYoutubeDL:
    """Minimal stand-in for ``yt_dlp.YoutubeDL``.

    ``behaviour`` is called with ``(target, options)`` and returns the raw info
    dict; it may raise to simulate an extractor failure.
    """

    def __init__(
        self,
        options: dict[str, Any],
        behaviour: Any,
        log: list[dict[str, Any]],
    ) -> None:
        self.options = options
        self.behaviour = behaviour
        self.log = log

    def __enter__(self) -> FakeYoutubeDL:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def extract_info(self, target: str, download: bool = False) -> Any:
        self.log.append({"options": self.options, "target": target, "download": download})
        return self._next()

    def _next(self) -> Any:
        behaviour = self.behaviour
        if isinstance(behaviour, list):
            behaviour = behaviour.pop(0)
        if isinstance(behaviour, BaseException):
            raise behaviour
        if callable(behaviour):
            return behaviour(self.log[-1]["target"], self.options)
        return behaviour

    def sanitize_info(self, info: Any) -> Any:
        return info


@pytest.fixture
def ydl_calls(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, Any]] = []
    state: dict[str, Any] = {"behaviour": {}}

    def factory(options: dict[str, Any]) -> FakeYoutubeDL:
        return FakeYoutubeDL(options, state["behaviour"], calls)

    def set_behaviour(behaviour: Any) -> None:
        state["behaviour"] = behaviour

    monkeypatch.setattr(ytdlp_module, "YoutubeDL", factory)
    return SimpleNamespace(calls=calls, set_behaviour=set_behaviour)


def test_download_returns_single_info(settings: Settings, ydl_calls) -> None:
    provider = build_provider(settings)
    ydl_calls.set_behaviour({"id": "abc", "title": "Track"})

    info = provider._download("url", {"format": "best"}, operation="download_video")

    assert info == {"id": "abc", "title": "Track"}
    assert ydl_calls.calls[0]["target"] == "url"
    assert ydl_calls.calls[0]["download"] is True


def test_download_unwraps_playlist_entries(settings: Settings, ydl_calls) -> None:
    provider = build_provider(settings)
    ydl_calls.set_behaviour({"entries": [{"id": "first"}, {"id": "second"}]})

    info = provider._download("url", {}, operation="download_video")

    assert info == {"id": "first"}


def test_download_rejects_empty_entries(settings: Settings, ydl_calls) -> None:
    provider = build_provider(settings)
    ydl_calls.set_behaviour({"entries": []})

    with pytest.raises(EmptyResponseError):
        provider._download("url", {}, operation="download_video")


def test_download_rejects_invalid_entries(settings: Settings, ydl_calls) -> None:
    provider = build_provider(settings)
    ydl_calls.set_behaviour({"entries": "nope"})

    with pytest.raises(UnexpectedResponseError):
        provider._download("url", {}, operation="download_video")


def test_download_rejects_invalid_entry_type(settings: Settings, ydl_calls) -> None:
    provider = build_provider(settings)
    ydl_calls.set_behaviour({"entries": ["nope"]})

    with pytest.raises(UnexpectedResponseError):
        provider._download("url", {}, operation="download_video")


def test_download_rejects_empty_response(settings: Settings, ydl_calls) -> None:
    provider = build_provider(settings)
    ydl_calls.set_behaviour(None)

    with pytest.raises(EmptyResponseError):
        provider._download("url", {}, operation="download_audio")


def test_download_maps_extractor_failure(settings: Settings, ydl_calls) -> None:
    provider = build_provider(settings)
    ydl_calls.set_behaviour(YtdlpDownloadError("boom"))

    with pytest.raises(DownloadError) as excinfo:
        provider._download("url", {}, operation="download_video")

    assert "boom" in excinfo.value.details
    assert excinfo.value.operation_name == "download_video"


def test_download_retries_with_cookies(
    settings: Settings, ydl_calls, tmp_path: Path
) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("cookie", encoding="utf-8")
    settings.cookies_file = str(cookies)
    provider = build_provider(settings)

    ydl_calls.set_behaviour(
        [YtdlpDownloadError("bot check"), {"id": "abc"}]
    )

    info = provider._download("url", {"format": "best"}, operation="download_video")

    assert info == {"id": "abc"}
    assert len(ydl_calls.calls) == 2
    assert "cookiefile" not in ydl_calls.calls[0]["options"]
    assert ydl_calls.calls[1]["options"]["cookiefile"] == str(cookies)
    assert ydl_calls.calls[1]["options"]["format"] == "best"


def test_download_retry_failure_is_reported(
    settings: Settings, ydl_calls, tmp_path: Path
) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("cookie", encoding="utf-8")
    settings.cookies_file = str(cookies)
    provider = build_provider(settings)

    ydl_calls.set_behaviour(
        [YtdlpDownloadError("first"), YtdlpDownloadError("second")]
    )

    with pytest.raises(DownloadError) as excinfo:
        provider._download("url", {}, operation="download_video")

    assert "second" in excinfo.value.details


def test_resolve_media_file_prefers_the_expected_suffix(
    settings: Settings, output_dir: Path
) -> None:
    (output_dir / "abc.mp3").write_bytes(b"a")
    (output_dir / "abc.jpg").write_bytes(b"j")

    path = YtdlpProvider._resolve_media_file(
        output_dir,
        "abc",
        (".mp3",),
        operation="download_audio",
        public_message="nope",
    )

    assert path.name == "abc.mp3"


def test_resolve_media_file_accepts_any_extension_case(
    settings: Settings, output_dir: Path
) -> None:
    (output_dir / "abc.MP3").write_bytes(b"a")

    path = YtdlpProvider._resolve_media_file(
        output_dir,
        "abc",
        (".mp3",),
        operation="download_audio",
        public_message="nope",
    )

    assert path.name == "abc.MP3"


def test_resolve_media_file_rejects_path_traversal(
    settings: Settings, output_dir: Path
) -> None:
    with pytest.raises(UnexpectedResponseError):
        YtdlpProvider._resolve_media_file(
            output_dir,
            "../../etc/passwd",
            (".mp3",),
            operation="download_audio",
            public_message="nope",
        )


def test_resolve_media_file_rejects_non_string_id(
    settings: Settings, output_dir: Path
) -> None:
    with pytest.raises(UnexpectedResponseError):
        YtdlpProvider._resolve_media_file(
            output_dir,
            42,
            (".mp3",),
            operation="download_audio",
            public_message="nope",
        )


def test_resolve_media_file_reports_missing_output(
    settings: Settings, output_dir: Path
) -> None:
    with pytest.raises(DownloadError):
        YtdlpProvider._resolve_media_file(
            output_dir,
            "abc",
            (".mp3",),
            operation="download_audio",
            public_message="nope",
        )


def test_ensure_uploadable_accepts_small_files(
    settings: Settings, output_dir: Path
) -> None:
    provider = build_provider(settings)
    path = output_dir / "abc.mp3"
    path.write_bytes(b"x" * 10)
    provider.max_upload_size_bytes = 100

    assert provider._ensure_uploadable(
        path, operation="download_audio", public_message="nope"
    ) == 10


def test_ensure_uploadable_rejects_large_files(
    settings: Settings, output_dir: Path
) -> None:
    provider = build_provider(settings)
    path = output_dir / "abc.mp3"
    path.write_bytes(b"x" * 200)
    provider.max_upload_size_bytes = 100

    with pytest.raises(MediaTooLargeError):
        provider._ensure_uploadable(
            path, operation="download_audio", public_message="nope"
        )


def test_probe_media_reads_stream_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "video.mp4"
    path.write_bytes(b"x")

    payload = {
        "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
    }
    monkeypatch.setattr(
        ytdlp_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps(payload), stderr=""
        ),
    )

    assert YtdlpProvider._probe_media(path) == MediaProbe(True, True)


def test_probe_media_returns_none_on_probe_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "video.mp4"
    path.write_bytes(b"x")
    monkeypatch.setattr(
        ytdlp_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="moov atom not found"
        ),
    )

    assert YtdlpProvider._probe_media(path) is None


def test_probe_media_returns_none_on_unparsable_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "video.mp4"
    path.write_bytes(b"x")
    monkeypatch.setattr(
        ytdlp_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="not json", stderr=""
        ),
    )

    assert YtdlpProvider._probe_media(path) is None


def test_probe_media_requires_ffprobe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "video.mp4"
    path.write_bytes(b"x")

    def boom(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(ytdlp_module.subprocess, "run", boom)

    with pytest.raises(DownloadError):
        YtdlpProvider._probe_media(path)


def test_probe_media_on_a_real_audio_file(mp3_file: Path) -> None:
    probe = YtdlpProvider._probe_media(mp3_file)
    assert probe is not None
    assert probe.has_audio is True
    assert probe.has_video is False


def test_get_used_format_ids_prefers_requested_formats() -> None:
    info = {
        "requested_formats": [
            {"format_id": "137"},
            {"format_id": "140"},
            {"nope": True},
            "junk",
        ]
    }

    assert YtdlpProvider._get_used_format_ids(info) == ["137", "140"]


def test_get_used_format_ids_splits_merged_ids() -> None:
    assert YtdlpProvider._get_used_format_ids({"format_id": "137+140"}) == [
        "137",
        "140",
    ]


def test_get_used_format_ids_returns_empty_without_ids() -> None:
    assert YtdlpProvider._get_used_format_ids({}) == []


def test_download_audio_returns_file_and_cover(
    settings: Settings, ydl_calls, output_dir: Path
) -> None:
    provider = build_provider(settings)

    def behaviour(target: str, options: dict[str, Any]) -> dict[str, Any]:
        output_dir.joinpath("abc.mp3").write_bytes(b"audio")
        output_dir.joinpath("abc.jpg").write_bytes(b"cover")
        return {"id": "abc"}

    ydl_calls.set_behaviour(behaviour)

    audio_path, cover_path = provider._download_audio_sync("query", output_dir)

    assert audio_path.name == "abc.mp3"
    assert cover_path is not None
    assert cover_path.name == "abc.jpg"
    assert ydl_calls.calls[0]["target"] == "ytsearch1:query"


def test_download_audio_keeps_urls_as_targets(
    settings: Settings, ydl_calls, output_dir: Path
) -> None:
    provider = build_provider(settings)

    def behaviour(target: str, options: dict[str, Any]) -> dict[str, Any]:
        output_dir.joinpath("abc.mp3").write_bytes(b"audio")
        return {"id": "abc"}

    ydl_calls.set_behaviour(behaviour)
    provider._download_audio_sync("https://example.com/track", output_dir)

    assert ydl_calls.calls[0]["target"] == "https://example.com/track"


def test_download_audio_requires_an_id(settings: Settings, ydl_calls, output_dir: Path) -> None:
    provider = build_provider(settings)
    ydl_calls.set_behaviour({})

    with pytest.raises(UnexpectedResponseError):
        provider._download_audio_sync("query", output_dir)


def test_download_audio_reports_missing_output(
    settings: Settings, ydl_calls, output_dir: Path
) -> None:
    provider = build_provider(settings)
    ydl_calls.set_behaviour({"id": "abc"})

    with pytest.raises(DownloadError):
        provider._download_audio_sync("query", output_dir)


def test_download_video_retries_after_invalid_media(
    settings: Settings, output_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = build_provider(settings)
    probes = [
        MediaProbe(False, True),
        MediaProbe(True, True),
    ]

    def fake_download(target: str, options: dict[str, Any], operation: str) -> dict[str, Any]:
        output_dir.joinpath("abc.mp4").write_bytes(b"video")
        return {"id": "abc", "format_id": "18"}

    monkeypatch.setattr(provider, "_download", fake_download)
    monkeypatch.setattr(
        YtdlpProvider,
        "_probe_media",
        staticmethod(lambda path: probes.pop(0)),
    )

    video_path = provider._download_video_sync("url", None, output_dir)

    assert video_path.name == "abc.mp4"
    assert probes == []


def test_download_video_keeps_file_when_probe_cannot_read_it(
    settings: Settings, output_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = build_provider(settings)

    def fake_download(target: str, options: dict[str, Any], operation: str) -> dict[str, Any]:
        output_dir.joinpath("abc.mp4").write_bytes(b"video")
        return {"id": "abc"}

    monkeypatch.setattr(provider, "_download", fake_download)
    monkeypatch.setattr(YtdlpProvider, "_probe_media", staticmethod(lambda path: None))

    video_path = provider._download_video_sync("url", None, output_dir)

    assert video_path.is_file()


def test_download_video_fails_on_invalid_explicit_format(
    settings: Settings, output_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = build_provider(settings)

    def fake_download(target: str, options: dict[str, Any], operation: str) -> dict[str, Any]:
        output_dir.joinpath("abc.mp4").write_bytes(b"video")
        return {"id": "abc", "format_id": "18"}

    monkeypatch.setattr(provider, "_download", fake_download)
    monkeypatch.setattr(
        YtdlpProvider, "_probe_media", staticmethod(lambda path: MediaProbe(False, True))
    )

    with pytest.raises(DownloadError):
        provider._download_video_sync("url", "18", output_dir)

    assert not output_dir.joinpath("abc.mp4").exists()


def test_get_video_formats_filters_and_sorts(
    settings: Settings, ydl_calls
) -> None:
    provider = build_provider(settings)
    ydl_calls.set_behaviour(
        {
            "id": "vid",
            "formats": [
                {"format_id": "140", "height": None, "vcodec": "none"},
                {"format_id": "137", "height": 1080, "vcodec": "avc1"},
                {"format_id": "136", "height": 720, "vcodec": "avc1"},
                {"format_id": "137b", "height": 1080, "vcodec": "avc1"},
                {"height": 480, "vcodec": "avc1"},
                {"format_id": "18", "height": 360, "vcodec": "none"},
                {"format_id": "bad", "height": "360", "vcodec": "avc1"},
                "junk",
            ],
        }
    )

    formats = provider._get_video_formats_sync("https://youtu.be/vid")

    assert [item.height for item in formats] == [1080, 720]
    assert [item.format_id for item in formats] == ["137", "136"]
    assert all(item.video_id == "vid" for item in formats)


def test_get_video_formats_requires_formats(settings: Settings, ydl_calls) -> None:
    provider = build_provider(settings)
    ydl_calls.set_behaviour({"id": "vid", "formats": []})

    with pytest.raises(EmptyResponseError):
        provider._get_video_formats_sync("https://youtu.be/vid")


def test_get_video_formats_requires_video_streams(
    settings: Settings, ydl_calls
) -> None:
    provider = build_provider(settings)
    ydl_calls.set_behaviour(
        {"id": "vid", "formats": [{"format_id": "140", "height": 0, "vcodec": "none"}]}
    )

    with pytest.raises(EmptyResponseError):
        provider._get_video_formats_sync("https://youtu.be/vid")


async def test_download_audio_runs_in_a_thread(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, output_dir: Path
) -> None:
    provider = build_provider(settings)
    monkeypatch.setattr(
        provider,
        "_download_audio_sync",
        lambda query, out: (output_dir / "abc.mp3", None),
    )

    audio_path, cover_path = await provider.download_audio("q", output_dir)

    assert audio_path.name == "abc.mp3"
    assert cover_path is None
