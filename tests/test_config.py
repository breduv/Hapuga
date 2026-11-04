from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core import config as config_module
from app.core.config import Settings, data_dir, get_settings

REQUIRED = {
    "bot_token": "123456:test-token",
    "spotify_client_id": "client-id",
    "spotify_client_secret": "client-secret",
}


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **(REQUIRED | overrides))


def test_required_settings_are_enforced() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_defaults_are_applied() -> None:
    settings = make_settings()

    assert settings.log_level == "INFO"
    assert settings.search_limit == 5
    assert settings.max_upload_size_mb == 49
    assert settings.media_proxy is None
    assert settings.telegram_proxy is None
    assert settings.inline_cache_chat_id is None
    assert settings.cookies_file is None


@pytest.mark.parametrize("field", ["media_proxy", "telegram_proxy"])
def test_blank_proxies_become_none(field: str) -> None:
    settings = make_settings(**{field: "   "})
    assert getattr(settings, field) is None


def test_blank_inline_cache_chat_id_becomes_none() -> None:
    settings = make_settings(inline_cache_chat_id="")
    assert settings.inline_cache_chat_id is None


def test_numeric_inline_cache_chat_id_is_parsed() -> None:
    settings = make_settings(inline_cache_chat_id="-1001234567890")
    assert settings.inline_cache_chat_id == -1001234567890


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("log_level", "TRACE"),
        ("search_limit", 0),
        ("search_limit", 11),
        ("max_upload_size_mb", 0),
    ],
)
def test_invalid_values_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{field: value})


def test_env_variables_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "999:env-token")
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "env-id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "env-secret")
    monkeypatch.setenv("SEARCH_LIMIT", "3")

    settings = Settings(_env_file=None)

    assert settings.bot_token.get_secret_value() == "999:env-token"
    assert settings.search_limit == 3


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "999:env-token")
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "env-id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "env-secret")

    assert get_settings() is get_settings()


def test_data_dir_points_at_the_repository_data_directory() -> None:
    assert data_dir().name == "data"
    assert data_dir().parent.name == "download-bot"


def test_blank_cookies_file_is_none() -> None:
    assert make_settings(cookies_file="  ").cookies_file is None


def test_cookies_file_must_exist_in_the_data_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(config_module, "data_dir", lambda: tmp_path)
    (tmp_path / "youtube.cookies.txt").write_text("# cookies", encoding="utf-8")

    settings = make_settings(cookies_file="youtube.cookies.txt")

    assert settings.cookies_file == str((tmp_path / "youtube.cookies.txt").resolve())


def test_cookies_file_path_components_are_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(config_module, "data_dir", lambda: tmp_path)

    with pytest.raises(ValidationError):
        make_settings(cookies_file="/etc/passwd")


def test_missing_cookies_file_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(config_module, "data_dir", lambda: tmp_path)

    with pytest.raises(ValidationError) as excinfo:
        make_settings(cookies_file="nope.cookies.txt")

    assert "does not exist" in str(excinfo.value)


def test_cookies_file_must_be_a_path_like_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(config_module, "data_dir", lambda: tmp_path)

    with pytest.raises(ValidationError):
        make_settings(cookies_file=42)
