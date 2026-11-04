from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def data_dir() -> Path:
    """Directory mounted as ``/app/data`` in Docker (cookies files live here)."""
    return Path(__file__).resolve().parents[2] / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    bot_token: SecretStr

    spotify_client_id: str

    spotify_client_secret: SecretStr

    media_proxy: SecretStr | None = Field(default=None)

    telegram_proxy: SecretStr | None = Field(default=None)

    log_level: Literal[
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    ] = Field(default="INFO")

    cookies_file: str | None = Field(default=None)

    search_limit: int = Field(default=5, ge=1, le=10)

    max_upload_size_mb: int = Field(default=49, ge=1)

    inline_cache_chat_id: int | None = Field(default=None)

    @field_validator(
        "media_proxy", "telegram_proxy", "inline_cache_chat_id", mode="before"
    )
    @classmethod
    def empty_value_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("cookies_file", mode="before")
    @classmethod
    def validate_cookies_file(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None

        if not isinstance(value, (str, Path)):
            raise ValueError("Cookies path must be a file name")

        cookies_file = data_dir() / Path(value).name
        if not cookies_file.is_file():
            raise ValueError(
                f"Cookies file does not exist in data directory: {cookies_file}"
            )
        return str(cookies_file.resolve())


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
