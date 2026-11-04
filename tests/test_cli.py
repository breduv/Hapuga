from __future__ import annotations

import pytest

from app import cli as cli_module
from app.cli import USAGE, main


def test_help_is_printed(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--help"]) == 0
    assert "usage: python -m app" in capsys.readouterr().out


def test_help_aliases(capsys: pytest.CaptureFixture[str]) -> None:
    for alias in ("-h", "help"):
        assert main([alias]) == 0
        assert "doctor" in capsys.readouterr().out


def test_unknown_command_fails(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["serve"]) == 2
    assert "unknown command" in capsys.readouterr().err


def test_doctor_is_delegated(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[list[str]] = []

    def fake_doctor(argv: list[str] | None = None) -> int:
        received.append(list(argv or []))
        return 7

    monkeypatch.setattr(cli_module.doctor, "main", fake_doctor)

    assert main(["doctor", "--json"]) == 7
    assert received == [["--json"]]


def test_run_bot_handles_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    async def fake_run(app_settings: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "run_application", fake_run)

    assert cli_module.run_bot() == 0


def test_run_bot_reports_unhandled_errors(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    async def fake_run(app_settings: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "run_application", fake_run)

    assert cli_module.run_bot() == 1


def test_usage_lists_the_commands() -> None:
    assert "doctor" in USAGE
    assert "run the Telegram bot" in USAGE
