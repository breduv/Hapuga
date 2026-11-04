from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app import doctor as doctor_module
from app.core.config import Settings
from app.doctor import (
    FAIL,
    OK,
    WARN,
    CheckResult,
    _check_binary,
    _check_data_dir,
    _check_proxy,
    _check_upload_limit,
    format_report,
    main,
    run_checks,
)


def make_settings(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        bot_token="123456:test-token",
        spotify_client_id="client-id",
        spotify_client_secret="client-secret",
        **overrides,
    )


def test_binary_check_reports_the_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_module.shutil, "which", lambda name: f"/usr/bin/{name}")

    result = _check_binary("ffmpeg", required=True)

    assert result.status == OK
    assert result.detail == "/usr/bin/ffmpeg"


def test_required_binary_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_module.shutil, "which", lambda name: None)

    assert _check_binary("ffprobe", required=True).status == FAIL


def test_optional_binary_is_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_module.shutil, "which", lambda name: None)

    assert _check_binary("deno", required=False).status == WARN


@pytest.mark.parametrize(
    ("proxy", "status"),
    [
        (None, OK),
        ("http://127.0.0.1:3128", OK),
        ("socks5h://127.0.0.1:1080", OK),
        ("ftp://127.0.0.1:21", FAIL),
        ("127.0.0.1:1080", FAIL),
    ],
)
def test_proxy_schemes(proxy: str | None, status: str) -> None:
    assert _check_proxy("media proxy", proxy).status == status


def test_upload_limit_above_telegram_limit_warns() -> None:
    assert _check_upload_limit(make_settings(max_upload_size_mb=50)).status == OK
    assert _check_upload_limit(make_settings(max_upload_size_mb=51)).status == WARN


def test_data_dir_missing_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(doctor_module, "data_dir", lambda: tmp_path / "missing")

    result = _check_data_dir()

    assert result.status == WARN
    assert "does not exist" in result.detail


def test_data_dir_is_probed_for_writability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(doctor_module, "data_dir", lambda: tmp_path)

    result = _check_data_dir()

    assert result.status == OK
    assert not (tmp_path / ".write-probe").exists()


def test_data_dir_write_failure_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(doctor_module, "data_dir", lambda: tmp_path)
    real_write = Path.write_text

    def fake_write(self: Path, *args: object, **kwargs: object) -> int:
        if self.parent == tmp_path:
            raise OSError("read-only file system")
        return real_write(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", fake_write)

    result = _check_data_dir()

    assert result.status == FAIL
    assert "not writable" in result.detail


def test_run_checks_reports_every_configured_item(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(doctor_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(doctor_module, "data_dir", lambda: tmp_path)
    (tmp_path / "youtube.cookies.txt").write_text("#", encoding="utf-8")

    settings = make_settings(
        media_proxy="socks5://127.0.0.1:1080",
        inline_cache_chat_id=-1001234567890,
    )
    settings.cookies_file = str(tmp_path / "youtube.cookies.txt")

    results = run_checks(settings)
    names = {result.name: result.status for result in results}

    assert names["ffmpeg"] == OK
    assert names["ffprobe"] == OK
    assert names["deno"] == OK
    assert names["media proxy"] == OK
    assert names["telegram proxy"] == OK
    assert names["cookies file"] == OK
    assert names["inline cache chat"] == OK
    assert all(result.status != FAIL for result in results)


def test_run_checks_warns_about_missing_inline_cache_chat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(doctor_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(doctor_module, "data_dir", lambda: tmp_path)

    results = {result.name: result for result in run_checks(make_settings())}

    assert results["inline cache chat"].status == WARN


def test_format_report_lists_checks_and_totals() -> None:
    report = format_report(
        [
            CheckResult("ffmpeg", OK, "/usr/bin/ffmpeg"),
            CheckResult("deno", WARN, "missing"),
            CheckResult("ffprobe", FAIL, "missing"),
        ]
    )

    assert "ffmpeg" in report
    assert "[WARN]" in report
    assert "[FAIL]" in report
    assert "3 checks, 1 failed, 1 warnings" in report


def test_main_returns_zero_when_everything_passes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(doctor_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(doctor_module, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(doctor_module, "get_settings", lambda: make_settings())

    assert main([]) == 0

    output = capsys.readouterr().out
    assert "Deployment checks" in output
    assert "0 failed" in output


def test_main_fails_on_missing_binaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(doctor_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(doctor_module, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(doctor_module, "get_settings", lambda: make_settings())

    assert main([]) == 1
    assert "[FAIL] ffmpeg" in capsys.readouterr().out


def test_main_reads_the_real_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom() -> Settings:
        # Raises ValidationError: the required settings are not provided.
        return Settings(_env_file=None)

    monkeypatch.setattr(doctor_module, "get_settings", boom)

    assert main([]) == 1
    output = capsys.readouterr().out
    assert "configuration" in output
    assert "bot_token" in output


def test_main_supports_json_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(doctor_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(doctor_module, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(doctor_module, "get_settings", lambda: make_settings())

    assert main(["--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["failed"] >= 1
    assert {result["name"] for result in payload["results"]} >= {"ffmpeg", "ffprobe"}


def test_main_rejects_unknown_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--nope"]) == 2
    assert "unknown arguments" in capsys.readouterr().err
