from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from loguru import logger

from app.bot.router import setup_router
from app.core.config import Settings
from app.providers.cover import CoverProvider
from app.providers.gallerydl import GallerydlProvider
from app.providers.spotify import SpotifyProvider
from app.providers.ytdlp import YtdlpProvider
from app.services.download_service import DownloadService
from app.services.search_service import SearchService


async def run_application(settings: Settings) -> None:
    logger.info(
        "Initializing application search_limit={} max_upload_size_mb={} media_proxy={} telegram_proxy={} inline_cache_chat_id={}",
        settings.search_limit,
        settings.max_upload_size_mb,
        settings.media_proxy is not None,
        settings.telegram_proxy is not None,
        settings.inline_cache_chat_id,
    )

    session = AiohttpSession(
        proxy=settings.telegram_proxy.get_secret_value()
        if settings.telegram_proxy
        else None
    )
    bot = Bot(token=settings.bot_token.get_secret_value(), session=session)
    dispatcher = Dispatcher()

    spotify_provider = SpotifyProvider(settings)
    ytdlp_provider = YtdlpProvider(settings)
    cover_provaider = CoverProvider(settings)
    gallerydl_provider = GallerydlProvider(settings)

    search_service = SearchService(
        spotify_provider=spotify_provider,
        ytdlp_provider=ytdlp_provider,
    )
    download_service = DownloadService(
        spotify_provider=spotify_provider,
        ytdlp_provider=ytdlp_provider,
        cover_provaider=cover_provaider,
        gallerydl_provider=gallerydl_provider,
    )

    router = setup_router(
        search_service=search_service,
        download_service=download_service,
        inline_cache_chat_id=settings.inline_cache_chat_id,
    )
    dispatcher.include_router(router)

    try:
        # await bot.set_my_commands(...)
        logger.info("Starting Telegram bot")
        await dispatcher.start_polling(bot)
    finally:
        logger.info("Stopping Telegram bot")
        await bot.session.close()
        logger.info("Telegram bot stopped")
