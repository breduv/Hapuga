"""Handler tests.

Telegram is faked at the bot boundary: ``FakeBot`` is an async callable that
receives the aiogram method object and answers with a duck-typed result, so the
handlers run unmodified without a network or a real bot token.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from aiogram.types import Chat, Message, User

from app.bot.handlers import BotHandlers
from app.errors.base import DownloadError, EmptyResponseError


class FakeBot:
    """Async callable that records aiogram methods and answers with stubs."""

    def __init__(self) -> None:
        self.id = 42
        self.calls: list[Any] = []

    async def send_chat_action(self, **kwargs: Any) -> bool:
        self.calls.append(("send_chat_action", kwargs))
        return True

    async def __call__(self, method: Any, **kwargs: Any) -> Any:
        name = type(method).__name__
        self.calls.append((name, method))
        return self.response_for(name)

    async def send_audio(self, **kwargs: Any) -> Any:
        self.calls.append(("SendAudio", _Simple(**kwargs)))
        return self.response_for("SendAudio")

    async def send_video(self, **kwargs: Any) -> Any:
        self.calls.append(("SendVideo", _Simple(**kwargs)))
        return self.response_for("SendVideo")

    async def send_photo(self, **kwargs: Any) -> Any:
        self.calls.append(("SendPhoto", _Simple(**kwargs)))
        return self.response_for("SendPhoto")

    async def edit_message_media(self, **kwargs: Any) -> bool:
        self.calls.append(("EditMessageMedia", _Simple(**kwargs)))
        return True

    async def edit_message_text(self, **kwargs: Any) -> bool:
        self.calls.append(("EditMessageText", _Simple(**kwargs)))
        return True

    async def answer_inline_query(self, **kwargs: Any) -> bool:
        self.calls.append(("AnswerInlineQuery", _Simple(**kwargs)))
        return True

    def response_for(self, name: str) -> Message:
        extra: dict[str, Any] = {}

        if name == "SendAudio":
            extra["audio"] = _Simple(file_id="file-audio", duration=1)
        elif name == "SendVideo":
            extra["video"] = _Simple(file_id="file-video", duration=1)
        elif name == "SendPhoto":
            extra["photo"] = [_Simple(file_id="file-photo")]

        message = Message.model_construct(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=100, type="private"),
            **extra,
        )
        return message.as_(self)  # type: ignore[arg-type]

    def methods(self, name: str) -> list[Any]:
        return [payload for method, payload in self.calls if method == name]


class _Simple:
    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeSearchService:
    def __init__(self, response: dict[str, str] | None = None, error: Exception | None = None) -> None:
        self.response = response or {}
        self.error = error
        self.queries: list[str] = []

    async def search(self, text: str) -> dict[str, str]:
        self.queries.append(text)
        if self.error is not None:
            raise self.error
        return self.response


class FakeDownloadService:
    def __init__(
        self,
        *,
        cover: bool = True,
        photos: int = 2,
        error: Exception | None = None,
    ) -> None:
        self.cover = cover
        self.photos = photos
        self.error = error
        self.media_calls: list[dict[str, Any]] = []
        self.spotify_calls: list[str] = []
        self.youtube_calls: list[tuple[str, str]] = []
        self.photo_calls: list[str] = []

    def _write(self, output_dir: Path, name: str) -> Path:
        path = output_dir / name
        path.write_bytes(b"payload")
        return path

    async def download_media(self, media: dict[str, str], output_dir: Path):
        self.media_calls.append(media)
        if self.error is not None:
            raise self.error
        suffix = "mp3" if "audio" in media else "mp4"
        cover = self._write(output_dir, f"cover.{'jpg' if self.cover else 'png'}")
        return self._write(output_dir, f"media.{suffix}"), cover

    async def download_photos(self, url: str, output_dir: Path) -> list[Path]:
        self.photo_calls.append(url)
        if self.error is not None:
            raise self.error
        return [
            self._write(output_dir, f"photo-{index}.jpg")
            for index in range(self.photos)
        ]

    async def download_on_spotify(self, track_id: str, output_dir: Path):
        self.spotify_calls.append(track_id)
        if self.error is not None:
            raise self.error
        return self._write(output_dir, "spotify.mp3"), None

    async def download_on_youtube(self, url: str, format_id: str, output_dir: Path):
        self.youtube_calls.append((url, format_id))
        if self.error is not None:
            raise self.error
        return self._write(output_dir, "youtube.mp4"), None


def build_handlers(
    *,
    search: FakeSearchService | None = None,
    download: FakeDownloadService | None = None,
    inline_cache_chat_id: int | None = None,
) -> tuple[BotHandlers, FakeSearchService, FakeDownloadService]:
    search_service = search or FakeSearchService()
    download_service = download or FakeDownloadService()
    handlers = BotHandlers(
        search_service=search_service,  # type: ignore[arg-type]
        download_service=download_service,  # type: ignore[arg-type]
        inline_cache_chat_id=inline_cache_chat_id,
    )
    return handlers, search_service, download_service


def build_message(bot: FakeBot, text: str | None = "daft punk") -> Message:
    message = Message.model_construct(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=100, type="private"),
        from_user=User(id=7, is_bot=False, first_name="Test"),
        text=text,
    )
    return message.as_(bot)  # type: ignore[arg-type]


def build_callback(bot: FakeBot, data: str, message: Message):
    class FakeCallback:
        def __init__(self) -> None:
            self.data = data
            self.message = message
            self.from_user = User(id=7, is_bot=False, first_name="Test")
            self.answers: list[Any] = []

        async def answer(self, text: str | None = None, **kwargs: Any) -> bool:
            self.answers.append((text, kwargs))
            return True

    return FakeCallback()


async def test_text_audio_response_sends_audio() -> None:
    bot = FakeBot()
    handlers, search, download = build_handlers(
        search=FakeSearchService({"audio": "daft punk get lucky"})
    )
    message = build_message(bot)

    await handlers.handle_text(message)

    assert search.queries == ["daft punk"]
    assert download.media_calls == [{"audio": "daft punk get lucky"}]
    sent = bot.methods("SendAudio")
    assert len(sent) == 1
    assert str(sent[0].audio.path).endswith(".mp3")


async def test_text_video_response_sends_video() -> None:
    bot = FakeBot()
    handlers, _, download = build_handlers(
        search=FakeSearchService({"video": "https://tiktok.com/@u/video/1"})
    )

    await handlers.handle_text(build_message(bot))

    assert download.media_calls == [{"video": "https://tiktok.com/@u/video/1"}]
    assert len(bot.methods("SendVideo")) == 1


async def test_text_photo_response_sends_gallery() -> None:
    bot = FakeBot()
    handlers, _, download = build_handlers(
        search=FakeSearchService({"photo": "https://tiktok.com/@u/photo/1"}),
        download=FakeDownloadService(photos=3),
    )

    await handlers.handle_text(build_message(bot))

    assert download.photo_calls == ["https://tiktok.com/@u/photo/1"]
    assert bot.methods("SendMediaGroup")


async def test_text_selection_sends_a_keyboard() -> None:
    bot = FakeBot()
    handlers, _, _ = build_handlers(
        search=FakeSearchService({"sp:abc": "Daft Punk - Get Lucky"})
    )

    await handlers.handle_text(build_message(bot))

    sent = bot.methods("SendMessage")
    assert len(sent) == 1
    assert sent[0].text == "Выбери трек:"
    assert sent[0].reply_markup is not None


async def test_text_ignores_messages_from_bots() -> None:
    bot = FakeBot()
    handlers, search, _ = build_handlers()
    message = Message.model_construct(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=100, type="private"),
        from_user=User(id=8, is_bot=True, first_name="Bot"),
        text="spam",
    ).as_(bot)  # type: ignore[arg-type]

    await handlers.handle_text(message)

    assert search.queries == []
    assert bot.calls == []


async def test_text_ignores_empty_messages() -> None:
    bot = FakeBot()
    handlers, search, _ = build_handlers()

    await handlers.handle_text(build_message(bot, text=None))

    assert search.queries == []


async def test_text_reports_app_errors() -> None:
    bot = FakeBot()
    handlers, _, _ = build_handlers(
        search=FakeSearchService(error=DownloadError("boom", component="x", operation_name="y"))
    )

    await handlers.handle_text(build_message(bot))

    sent = bot.methods("SendMessage")
    assert sent[0].text == DownloadError.public_message


async def test_text_reports_unexpected_errors() -> None:
    bot = FakeBot()
    handlers, _, _ = build_handlers(search=FakeSearchService(error=RuntimeError("boom")))

    await handlers.handle_text(build_message(bot))

    sent = bot.methods("SendMessage")
    assert "непредвиденная" in sent[0].text


async def test_text_deletes_the_loading_message() -> None:
    bot = FakeBot()
    handlers, _, _ = build_handlers(search=FakeSearchService({"audio": "track"}))

    await handlers.handle_text(build_message(bot))

    assert len(bot.methods("DeleteMessage")) == 1


async def test_callback_spotify_downloads_the_track() -> None:
    bot = FakeBot()
    handlers, _, download = build_handlers()
    callback = build_callback(bot, "sp:69kOkLUCkxIZYexIgSG8rq", build_message(bot))

    await handlers.handle_callback(callback)  # type: ignore[arg-type]

    assert download.spotify_calls == ["69kOkLUCkxIZYexIgSG8rq"]
    assert len(bot.methods("SendAudio")) == 1


async def test_callback_youtube_audio_option() -> None:
    bot = FakeBot()
    handlers, _, download = build_handlers()
    callback = build_callback(bot, "yt:dQw4w9WgXcQ:-1", build_message(bot))

    await handlers.handle_callback(callback)  # type: ignore[arg-type]

    assert download.youtube_calls == [("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "-1")]
    assert len(bot.methods("SendAudio")) == 1


async def test_callback_youtube_video_option() -> None:
    bot = FakeBot()
    handlers, _, download = build_handlers()
    callback = build_callback(bot, "yt:dQw4w9WgXcQ:137", build_message(bot))

    await handlers.handle_callback(callback)  # type: ignore[arg-type]

    assert download.youtube_calls == [("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "137")]
    assert len(bot.methods("SendVideo")) == 1


@pytest.mark.parametrize(
    "data",
    [
        "sp:",
        "sp:../../../etc/passwd",
        "yt:vid:bad/format",
        "yt:../../x:137",
        "unknown:data",
    ],
)
async def test_callback_rejects_malformed_payloads(data: str) -> None:
    bot = FakeBot()
    handlers, _, download = build_handlers()
    callback = build_callback(bot, data, build_message(bot))

    await handlers.handle_callback(callback)  # type: ignore[arg-type]

    assert download.spotify_calls == []
    assert download.youtube_calls == []
    assert bot.methods("SendMessage")


async def test_callback_pending_placeholder_is_acknowledged() -> None:
    bot = FakeBot()
    handlers, _, _ = build_handlers()
    callback = build_callback(bot, "inline:pending", build_message(bot))

    await handlers.handle_callback(callback)  # type: ignore[arg-type]

    assert callback.answers[0][0] == "Файл готовится..."


async def test_callback_without_message_reports_via_alert() -> None:
    bot = FakeBot()
    handlers, _, _ = build_handlers()

    class FakeCallback:
        data = "sp:abc"
        message = None
        from_user = User(id=7, is_bot=False, first_name="Test")

        def __init__(self) -> None:
            self.answers: list[Any] = []

        async def answer(self, text: str | None = None, **kwargs: Any) -> bool:
            self.answers.append((text, kwargs))
            return True

    callback = FakeCallback()
    await handlers.handle_callback(callback)  # type: ignore[arg-type]

    assert callback.answers
    assert callback.answers[0][1]["show_alert"] is True


async def test_send_photos_uses_a_single_message_for_one_photo() -> None:
    bot = FakeBot()
    message = build_message(bot)
    photos = [Path(f"/tmp/p{index}.jpg") for index in range(1)]

    await BotHandlers._send_photos(message, photos)

    assert len(bot.methods("SendPhoto")) == 1
    assert not bot.methods("SendMediaGroup")


async def test_send_photos_batches_ten_at_a_time() -> None:
    bot = FakeBot()
    message = build_message(bot)
    photos = [Path(f"/tmp/p{index}.jpg") for index in range(21)]

    await BotHandlers._send_photos(message, photos)

    groups = bot.methods("SendMediaGroup")
    assert [len(group.media) for group in groups] == [10, 10]
    # The trailing single photo is sent on its own: Telegram rejects a
    # one-item media group.
    assert len(bot.methods("SendPhoto")) == 1


async def test_send_photos_without_photos_does_nothing() -> None:
    bot = FakeBot()

    await BotHandlers._send_photos(build_message(bot), [])

    assert bot.calls == []


async def test_inline_choice_keeps_a_reference_to_its_task() -> None:
    bot = FakeBot()
    handlers, search, _ = build_handlers(search=FakeSearchService({"audio": "track"}))

    class FakeInline:
        query = "https://music.youtube.com/watch?v=dQw4w9WgXcQ"
        inline_message_id = "inline-1"
        result_id = "result-1"
        from_user = User(id=7, is_bot=False, first_name="Test")

    await handlers.handle_chosen_inline_result(FakeInline(), bot)  # type: ignore[arg-type]
    assert len(handlers._background_tasks) == 1

    await asyncio.gather(*handlers._background_tasks)

    assert search.queries == ["https://music.youtube.com/watch?v=dQw4w9WgXcQ"]
    assert handlers._background_tasks == set()


async def test_inline_choice_without_message_id_is_ignored() -> None:
    bot = FakeBot()
    handlers, search, _ = build_handlers()

    class FakeInline:
        query = "https://music.youtube.com/watch?v=dQw4w9WgXcQ"
        inline_message_id = None
        result_id = "result-1"
        from_user = User(id=7, is_bot=False, first_name="Test")

    await handlers.handle_chosen_inline_result(FakeInline(), bot)  # type: ignore[arg-type]

    assert handlers._background_tasks == set()
    assert search.queries == []


async def test_inline_empty_gallery_raises(settings, tmp_path: Path) -> None:
    bot = FakeBot()
    handlers, _, _ = build_handlers(download=FakeDownloadService(photos=0))

    class FakeInline:
        inline_message_id = "inline-1"
        from_user = User(id=7, is_bot=False, first_name="Test")

    with pytest.raises(EmptyResponseError):
        await handlers._edit_inline_result_media(
            bot,  # type: ignore[arg-type]
            FakeInline(),  # type: ignore[arg-type]
            {"photo": "https://tiktok.com/@u/photo/1"},
            tmp_path,
        )


async def test_inline_photo_reports_the_photo_count(tmp_path: Path) -> None:
    bot = FakeBot()
    handlers, _, _ = build_handlers(download=FakeDownloadService(photos=3))

    class FakeInline:
        inline_message_id = "inline-1"
        from_user = User(id=7, is_bot=False, first_name="Test")

    await handlers._edit_inline_result_media(
        bot,  # type: ignore[arg-type]
        FakeInline(),  # type: ignore[arg-type]
        {"photo": "https://tiktok.com/@u/photo/1"},
        tmp_path,
    )

    edits = bot.methods("EditMessageMedia")
    assert len(edits) == 1
    assert edits[0].media.caption == "Фото 1/3"


async def test_inline_unsupported_response_raises(tmp_path: Path) -> None:
    bot = FakeBot()
    handlers, _, _ = build_handlers()

    class FakeInline:
        inline_message_id = "inline-1"
        from_user = User(id=7, is_bot=False, first_name="Test")

    with pytest.raises(Exception):  # noqa: B017 - InvalidCallbackDataError
        await handlers._edit_inline_result_media(
            bot,  # type: ignore[arg-type]
            FakeInline(),  # type: ignore[arg-type]
            {"sp:abc": "Daft Punk - Get Lucky"},
            tmp_path,
        )
