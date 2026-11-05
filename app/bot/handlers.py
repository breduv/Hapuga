import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from urllib.parse import urlparse
from uuid import uuid4

from aiogram import Bot
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import (
    CallbackQuery,
    ChosenInlineResult,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputMediaAudio,
    InputMediaPhoto,
    InputMediaVideo,
    InputTextMessageContent,
    Message,
)
from aiogram.utils.chat_action import ChatActionSender
from loguru import logger

from app.bot.keyboard import build_search_results_keyboard
from app.errors.base import (
    AppError,
    InvalidCallbackDataError,
    UnexpectedResponseError,
)
from app.services.download_service import DownloadService
from app.services.search_service import SearchService


class BotHandlers:
    def __init__(
        self,
        search_service: SearchService,
        download_service: DownloadService,
        inline_cache_chat_id: int | None = None,
    ) -> None:
        self.search_service = search_service
        self.download_service = download_service
        self.inline_cache_chat_id = inline_cache_chat_id

    async def handle_start(self, msg: Message) -> None:
        text = (
            "Привет. Я скачиваю музыку, видео и фото по ссылкам\n\n"
            "Пришли мне название трека или ссылку, а я попробую вернуть готовый файл\n"
            "В любом чате можно использовать inline-режим: @loader_cat_bot <ссылка>\n\n"
            "Подробная инструкция: /help"
        )

        await msg.answer(text)

    async def handle_help(self, msg: Message) -> None:
        text = (
            "Что умеет бот:\n"
            "- ищет треки по названию и скачивает аудио;\n"
            "- скачивает треки по ссылке Spotify;\n"
            "- скачивает аудио из YouTube Music;\n"
            "- скачивает видео из YouTube Shorts, TikTok, Instagram, Pinterest и VK/VK Видео;\n"
            "- скачивает фотогалереи TikTok;\n"
            "- для обычных YouTube-ссылок показывает доступные форматы и вариант 'Только звук'.\n\n"
            "Как пользоваться в личке:\n"
            "1. Пришли название трека или ссылку.\n"
            "2. Если бот покажет варианты, выбери нужный кнопкой.\n"
            "3. Дождись сообщения с аудио, видео или фото.\n\n"
            "Как пользоваться в любом чате:\n"
            "1. Напиши @username_бота <ссылка>.\n"
            "2. Выбери результат 'Скачать'.\n"
            "3. Бот отправит 'Готовлю файл...' и заменит это сообщение на готовое медиа.\n\n"
            "Важно:\n"
            "- приватные, удаленные и недоступные по региону материалы могут не скачаться;\n"
            "- слишком большие файлы Telegram может не принять;\n"
            "- если inline-режим просит открыть бота в личке, напиши ему один раз напрямую и повтори запрос."
        )

        await msg.answer(text)

    async def handle_text(self, msg: Message) -> None:
        try:
            if (
                msg.text is None
                or not msg.from_user
                or msg.from_user.is_bot
                or msg.bot is None
            ):
                return

            logger.info(
                "Text request received user_id={} chat_id={} text_length={}",
                msg.from_user.id,
                msg.chat.id,
                len(msg.text),
            )

            response = await self.search_service.search(msg.text)

            if response.get("audio") is not None:
                logger.info("Audio request resolved user_id={}", msg.from_user.id)
                loading_msg = await msg.answer("Скачиваю...")
                try:
                    async with ChatActionSender(
                        bot=msg.bot,
                        chat_id=msg.chat.id,
                        message_thread_id=(
                            msg.message_thread_id if msg.is_topic_message else None
                        ),
                        action=ChatAction.UPLOAD_DOCUMENT,
                    ):
                        with TemporaryDirectory() as temp_dir:
                            (
                                file_path,
                                cover_path,
                            ) = await self.download_service.download_media(
                                response,
                                Path(temp_dir),
                            )

                            await msg.answer_audio(
                                audio=FSInputFile(file_path),
                                thumbnail=FSInputFile(cover_path)
                                if cover_path is not None
                                else None,
                            )
                            logger.info("Audio sent user_id={}", msg.from_user.id)
                finally:
                    await self._delete_message(loading_msg)

                return

            if response.get("video") is not None:
                logger.info("Video request resolved user_id={}", msg.from_user.id)
                loading_msg = await msg.answer("Скачиваю...")
                try:
                    async with ChatActionSender(
                        bot=msg.bot,
                        chat_id=msg.chat.id,
                        message_thread_id=(
                            msg.message_thread_id if msg.is_topic_message else None
                        ),
                        action=ChatAction.UPLOAD_VIDEO,
                    ):
                        with TemporaryDirectory() as temp_dir:
                            file_path, _ = await self.download_service.download_media(
                                response,
                                Path(temp_dir),
                            )

                            await msg.answer_video(
                                video=FSInputFile(file_path), supports_streaming=True
                            )
                            logger.info("Video sent user_id={}", msg.from_user.id)
                finally:
                    await self._delete_message(loading_msg)

                return

            if response.get("photo") is not None:
                logger.info("Photo request resolved user_id={}", msg.from_user.id)
                loading_msg = await msg.answer("Скачиваю...")
                try:
                    async with ChatActionSender(
                        bot=msg.bot,
                        chat_id=msg.chat.id,
                        message_thread_id=(
                            msg.message_thread_id if msg.is_topic_message else None
                        ),
                        action=ChatAction.UPLOAD_PHOTO,
                    ):
                        with TemporaryDirectory() as temp_dir:
                            photo_paths = await self.download_service.download_photos(
                                response["photo"],
                                Path(temp_dir),
                            )

                            await self._send_photos(msg, photo_paths)
                            logger.info(
                                "Gallery sent user_id={} photos_count={}",
                                msg.from_user.id,
                                len(photo_paths),
                            )
                finally:
                    await self._delete_message(loading_msg)

                return

            keyboard = build_search_results_keyboard(response)
            text = "Выбери вариант:"

            if any(key.startswith("sp:") for key in response):
                text = "Выбери трек:"
            elif any(key.startswith("yt:") for key in response):
                text = "Выбери формат:"

            await msg.answer(text, reply_markup=keyboard)
            logger.info(
                "Selection keyboard sent user_id={} options_count={}",
                msg.from_user.id,
                len(response),
            )

            return
        except AppError as exc:
            self._log_handled_error("text", exc)
            await self._send_error(msg, exc.public_message)
        except Exception:  # noqa: BLE001
            logger.exception("Unhandled request error context=text")
            await self._send_error(
                msg, "Произошла непредвиденная ошибка. Попробуй ещё раз позже"
            )

    async def handle_callback(self, callback: CallbackQuery) -> None:
        try:
            if callback.data is None:
                return

            if callback.data == "inline:pending":
                await callback.answer("Файл готовится...")
                return

            if not isinstance(callback.message, Message):
                raise InvalidCallbackDataError(
                    "Callback message is missing",
                    component="bot",
                    operation_name="handle_callback",
                    public_message="Не удалось найти сообщение",
                )

            msg = callback.message
            data = callback.data

            if msg.bot is None:
                return

            logger.info(
                "Callback received user_id={} chat_id={} callback_type={}",
                callback.from_user.id,
                msg.chat.id,
                data.partition(":")[0],
            )

            await callback.answer()

            if data.startswith("sp:"):
                track_id = data.removeprefix("sp:")
                if not track_id:
                    raise InvalidCallbackDataError(
                        "Spotify callback track id is empty",
                        component="bot",
                        operation_name="handle_callback",
                    )

                loading_msg = await msg.answer("Скачиваю...")
                try:
                    async with ChatActionSender(
                        bot=msg.bot,
                        chat_id=msg.chat.id,
                        message_thread_id=(
                            msg.message_thread_id if msg.is_topic_message else None
                        ),
                        action=ChatAction.UPLOAD_DOCUMENT,
                    ):
                        with TemporaryDirectory() as temp_dir:
                            (
                                file_path,
                                cover_path,
                            ) = await self.download_service.download_on_spotify(
                                track_id,
                                Path(temp_dir),
                            )

                            await msg.answer_audio(
                                audio=FSInputFile(file_path),
                                thumbnail=FSInputFile(cover_path)
                                if cover_path is not None
                                else None,
                            )
                            logger.info(
                                "Spotify audio sent user_id={}", callback.from_user.id
                            )
                finally:
                    await self._delete_message(loading_msg)

                return

            if data.startswith("yt:"):
                parts = data.split(":", maxsplit=2)
                if len(parts) != 3 or not parts[1] or not parts[2]:
                    raise InvalidCallbackDataError(
                        f"Malformed YouTube callback data: {data}",
                        component="bot",
                        operation_name="handle_callback",
                    )

                _, video_id, format_id = parts
                video_url = f"https://www.youtube.com/watch?v={video_id}"

                loading_msg = await msg.answer("Скачиваю...")
                try:
                    async with ChatActionSender(
                        bot=msg.bot,
                        chat_id=msg.chat.id,
                        message_thread_id=(
                            msg.message_thread_id if msg.is_topic_message else None
                        ),
                        action=(
                            ChatAction.UPLOAD_DOCUMENT
                            if format_id == "-1"
                            else ChatAction.UPLOAD_VIDEO
                        ),
                    ):
                        with TemporaryDirectory() as temp_dir:
                            (
                                file_path,
                                cover_path,
                            ) = await self.download_service.download_on_youtube(
                                video_url,
                                format_id,
                                Path(temp_dir),
                            )

                            if format_id == "-1":
                                await msg.answer_audio(
                                    audio=FSInputFile(file_path),
                                    thumbnail=FSInputFile(cover_path)
                                    if cover_path is not None
                                    else None,
                                )
                                logger.info(
                                    "YouTube audio sent user_id={}",
                                    callback.from_user.id,
                                )
                            else:
                                await msg.answer_video(
                                    video=FSInputFile(file_path),
                                    supports_streaming=True,
                                )
                                logger.info(
                                    "YouTube video sent user_id={} format_id={}",
                                    callback.from_user.id,
                                    format_id,
                                )
                finally:
                    await self._delete_message(loading_msg)

                return

            raise InvalidCallbackDataError(
                f"Unknown callback data: {data}",
                component="bot",
                operation_name="handle_callback",
            )
        except AppError as exc:
            self._log_handled_error("callback", exc)
            await self._send_callback_error(callback, exc.public_message)
        except Exception:  # noqa: BLE001
            logger.exception("Unhandled request error context=callback")
            await self._send_callback_error(
                callback,
                "Произошла непредвиденная ошибка. Попробуй отправить запрос ещё раз.",
            )

    async def handle_inline_query(self, inline_query: InlineQuery, bot: Bot) -> None:
        query = inline_query.query.strip()

        if not query or not (
            urlparse(query).scheme in ("http", "https")
            and urlparse(query).hostname is not None
        ):
            try:
                await bot.answer_inline_query(
                    inline_query_id=inline_query.id, results=[], cache_time=1
                )
            except TelegramAPIError:
                logger.opt(exception=True).warning(
                    "Failed to answer empty inline query"
                )
            return

        try:
            logger.info(
                "Inline request received user_id={} query_length={}",
                inline_query.from_user.id,
                len(query),
            )

            result = InlineQueryResultArticle(
                id=uuid4().hex,
                title="Скачать",
                description="Нажми, и бот подготовит файл",
                input_message_content=InputTextMessageContent(
                    message_text="Готовлю файл..."
                ),
                reply_markup=self._build_inline_pending_keyboard(),
            )

            await bot.answer_inline_query(
                inline_query_id=inline_query.id,
                results=[result],
            )

            logger.info(
                "Inline placeholder answered user_id={}",
                inline_query.from_user.id,
            )
        except TelegramAPIError:
            logger.exception("Telegram API error context=inline_query")
        except Exception:  # noqa: BLE001
            logger.exception("Unhandled request error context=inline_query")

    async def handle_chosen_inline_result(
        self,
        chosen_inline_result: ChosenInlineResult,
        bot: Bot,
    ) -> None:
        query = chosen_inline_result.query.strip()

        if not query:
            return

        if chosen_inline_result.inline_message_id is None:
            logger.warning(
                "Chosen inline result without inline_message_id user_id={}",
                chosen_inline_result.from_user.id,
            )
            return

        logger.info(
            "Chosen inline result received user_id={} result_id={} query_length={}",
            chosen_inline_result.from_user.id,
            chosen_inline_result.result_id,
            len(query),
        )

        asyncio.create_task(
            self._process_chosen_inline_result(bot, chosen_inline_result)
        )

    async def _process_chosen_inline_result(
        self,
        bot: Bot,
        chosen_inline_result: ChosenInlineResult,
    ) -> None:
        inline_message_id = chosen_inline_result.inline_message_id
        query = chosen_inline_result.query.strip()

        if inline_message_id is None:
            return

        try:
            started_at = monotonic()
            response = await self.search_service.search(query)
            search_finished_at = monotonic()
            with TemporaryDirectory() as temp_dir:
                logger.info(
                    "Inline media preparation started result_id={} search_duration_s={:.3f} "
                    "temp_dir_duration_s={:.3f}",
                    chosen_inline_result.result_id,
                    search_finished_at - started_at,
                    monotonic() - search_finished_at,
                )
                await self._edit_inline_result_media(
                    bot,
                    chosen_inline_result,
                    response,
                    Path(temp_dir),
                )

            logger.info(
                "Chosen inline result processed user_id={}",
                chosen_inline_result.from_user.id,
            )
        except TelegramForbiddenError:
            logger.opt(exception=True).warning(
                "Inline upload target is unavailable user_id={} cache_chat_id={}",
                chosen_inline_result.from_user.id,
                self.inline_cache_chat_id,
            )
            public_message = (
                "Сначала открой бота в личке, потом повтори inline-запрос"
                if self.inline_cache_chat_id is None
                else "Бот не может загрузить файл в служебный чат"
            )
            await self._edit_inline_error(bot, inline_message_id, public_message)
        except AppError as exc:
            self._log_handled_error("inline_query", exc)
            await self._edit_inline_error(bot, inline_message_id, exc.public_message)
        except TelegramAPIError:
            logger.exception("Telegram API error context=chosen_inline_result")
            await self._edit_inline_error(
                bot,
                inline_message_id,
                "Telegram не принял файл. Попробуй другую ссылку",
            )
        except Exception:  # noqa: BLE001
            logger.exception("Unhandled request error context=chosen_inline_result")
            await self._edit_inline_error(
                bot,
                inline_message_id,
                "Произошла непредвиденная ошибка. Попробуй ещё раз позже",
            )

    async def _edit_inline_result_media(
        self,
        bot: Bot,
        chosen_inline_result: ChosenInlineResult,
        response: dict[str, str],
        temp_dir: Path,
    ) -> None:
        inline_message_id = chosen_inline_result.inline_message_id

        if inline_message_id is None:
            return

        if response.get("audio") is not None:
            file_path, cover_path = await self.download_service.download_media(
                response, temp_dir
            )
            upload_msg = await bot.send_audio(
                chat_id=self.inline_cache_chat_id or chosen_inline_result.from_user.id,
                audio=FSInputFile(file_path),
                thumbnail=FSInputFile(cover_path) if cover_path is not None else None,
                title=response.get("title"),
                performer=response.get("artist"),
                disable_notification=True,
            )

            try:
                if upload_msg.audio is None:
                    raise UnexpectedResponseError(
                        "Telegram did not return uploaded audio",
                        component="telegram",
                        operation_name="upload_inline_audio",
                        public_message="Не удалось подготовить аудио для inline-отправки",
                    )

                await bot.edit_message_media(
                    inline_message_id=inline_message_id,
                    media=InputMediaAudio(
                        media=upload_msg.audio.file_id,
                        title=response.get("title"),
                        performer=response.get("artist"),
                    ),
                )
            finally:
                await self._delete_message(upload_msg)

            return

        if response.get("video") is not None:
            file_path, _ = await self.download_service.download_media(
                response, temp_dir
            )

            upload_msg = await bot.send_video(
                chat_id=self.inline_cache_chat_id or chosen_inline_result.from_user.id,
                video=FSInputFile(file_path),
                supports_streaming=True,
                disable_notification=True,
            )
            try:
                if upload_msg.video is None:
                    raise UnexpectedResponseError(
                        "Telegram did not return uploaded video",
                        component="telegram",
                        operation_name="upload_inline_video",
                        public_message="Не удалось подготовить видео для inline-отправки",
                    )

                await bot.edit_message_media(
                    inline_message_id=inline_message_id,
                    media=InputMediaVideo(
                        media=upload_msg.video.file_id,
                        supports_streaming=True,
                    ),
                )
            finally:
                await self._delete_message(upload_msg)

            return

        if response.get("photo") is not None:
            photo_paths = await self.download_service.download_photos(
                response["photo"],
                temp_dir,
            )

            upload_msg = await bot.send_photo(
                chat_id=self.inline_cache_chat_id or chosen_inline_result.from_user.id,
                photo=FSInputFile(photo_paths[0]),
                disable_notification=True,
            )
            try:
                if not upload_msg.photo:
                    raise UnexpectedResponseError(
                        "Telegram did not return uploaded photo",
                        component="telegram",
                        operation_name="upload_inline_photo",
                        public_message="Не удалось подготовить фото для inline-отправки",
                    )

                await bot.edit_message_media(
                    inline_message_id=inline_message_id,
                    media=InputMediaPhoto(
                        media=upload_msg.photo[-1].file_id,
                        caption=(
                            f"Фото 1/{len(photo_paths)}"
                            if len(photo_paths) > 1
                            else None
                        ),
                    ),
                )
            finally:
                await self._delete_message(upload_msg)

            return

        raise InvalidCallbackDataError(
            "Inline query resolved to unsupported selection results",
            component="bot",
            operation_name="handle_inline_query",
            public_message="Эта ссылка пока не поддерживается в inline-режиме",
        )

    @staticmethod
    async def _send_photos(msg: Message, photo_paths: list[Path]) -> None:
        max_group_size = 10

        for start in range(0, len(photo_paths), max_group_size):
            batch = photo_paths[start : start + max_group_size]
            logger.debug("Sending photo batch photos_count={}", len(batch))

            if len(batch) == 1:
                await msg.answer_photo(photo=FSInputFile(batch[0]))
                continue

            media = [
                InputMediaPhoto(media=FSInputFile(photo_path)) for photo_path in batch
            ]
            await msg.answer_media_group(media=media)  # pyright: ignore[reportArgumentType]

    @staticmethod
    def _log_handled_error(
        context: str,
        exc: AppError,
    ) -> None:
        logger.opt(exception=exc).warning(
            "Handled request error context={} code={} component={} operation_name={} message={} details={}",
            context,
            exc.code,
            exc.component,
            exc.operation_name,
            exc.message,
            exc.details,
        )

    @staticmethod
    async def _delete_message(msg: Message) -> None:
        try:
            await msg.delete()
        except Exception:  # noqa: BLE001
            logger.opt(exception=True).warning("Failed to delete message")

    @staticmethod
    async def _send_error(msg: Message, public_message: str) -> None:
        try:
            await msg.answer(public_message)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send error message to user")

    async def _send_callback_error(
        self, callback: CallbackQuery, public_message: str
    ) -> None:
        if isinstance(callback.message, Message):
            await self._send_error(callback.message, public_message)
            return

        try:
            await callback.answer(public_message, show_alert=True)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send callback error to user")

    @staticmethod
    def _build_inline_pending_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Готовлю...",
                        callback_data="inline:pending",
                    )
                ]
            ]
        )

    @staticmethod
    async def _edit_inline_error(
        bot: Bot,
        inline_message_id: str,
        public_message: str,
    ) -> None:
        try:
            await bot.edit_message_text(
                inline_message_id=inline_message_id,
                text=public_message,
            )
        except TelegramAPIError:
            logger.opt(exception=True).warning(
                "Failed to edit inline message with error"
            )
