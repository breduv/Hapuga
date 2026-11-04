"""Pre-flight checks for a deployment.

Run it before ``docker compose up`` or after changing ``.env``::

    python -m app doctor

Every check is offline and read-only, so it is safe to run inside the image.
Exit code is ``0`` when nothing failed, ``1`` when at least one check failed
(warnings alone keep the exit code at ``0``).
"""

from __future__ import annotations

import contextlib
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import ValidationError

from app.core.config import Settings, data_dir, get_settings

# Telegram's Bot API refuses uploads above 50 MB unless the bot runs on a
# local Bot API server, so anything higher only fails later, at upload time.
TELEGRAM_UPLOAD_LIMIT_MB = 50

SUPPORTED_PROXY_SCHEMES = frozenset(
    {"http", "https", "socks4", "socks5", "socks5h"}
)

OK = "ok"
WARN = "warn"
FAIL = "fail"

_STATUS_ICONS = {OK: "OK  ", WARN: "WARN", FAIL: "FAIL"}


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == FAIL


def _check_binary(name: str, *, required: bool) -> CheckResult:
    path = shutil.which(name)

    if path is not None:
        return CheckResult(name, OK, path)

    if required:
        return CheckResult(name, FAIL, "not found in PATH")

    return CheckResult(name, WARN, "not found in PATH (optional)")


def _check_data_dir() -> CheckResult:
    directory = data_dir()

    if not directory.is_dir():
        return CheckResult("data dir", WARN, f"{directory} does not exist yet")

    probe = directory / ".write-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
    except OSError as exc:
        return CheckResult("data dir", FAIL, f"{directory} is not writable: {exc}")
    finally:
        with contextlib.suppress(OSError):
            probe.unlink()

    return CheckResult("data dir", OK, str(directory))


def _check_proxy(name: str, proxy: str | None) -> CheckResult:
    if proxy is None:
        return CheckResult(name, OK, "not configured")

    scheme = proxy.split("://", 1)[0].lower() if "://" in proxy else ""

    if scheme not in SUPPORTED_PROXY_SCHEMES:
        return CheckResult(
            name,
            FAIL,
            f"unsupported proxy scheme {scheme or '(none)'!r}; "
            f"use one of {', '.join(sorted(SUPPORTED_PROXY_SCHEMES))}",
        )

    return CheckResult(name, OK, f"{scheme} proxy configured")


def _check_upload_limit(settings: Settings) -> CheckResult:
    limit = settings.max_upload_size_mb

    if limit > TELEGRAM_UPLOAD_LIMIT_MB:
        return CheckResult(
            "max upload size",
            WARN,
            f"{limit} MB exceeds the {TELEGRAM_UPLOAD_LIMIT_MB} MB Bot API limit",
        )

    return CheckResult("max upload size", OK, f"{limit} MB")


def run_checks(settings: Settings) -> list[CheckResult]:
    """Return every deployment check for a loaded configuration."""
    media_proxy = (
        settings.media_proxy.get_secret_value() if settings.media_proxy else None
    )
    telegram_proxy = (
        settings.telegram_proxy.get_secret_value()
        if settings.telegram_proxy
        else None
    )

    results = [
        CheckResult("bot token", OK, "configured"),
        CheckResult(
            "spotify credentials",
            OK,
            "configured",
        ),
        _check_binary("ffmpeg", required=True),
        _check_binary("ffprobe", required=True),
        _check_binary("deno", required=False),
        _check_data_dir(),
        _check_proxy("media proxy", media_proxy),
        _check_proxy("telegram proxy", telegram_proxy),
        _check_upload_limit(settings),
    ]

    if settings.cookies_file is None:
        results.append(
            CheckResult("cookies file", OK, "not configured (optional)")
        )
    else:
        cookies_path = Path(settings.cookies_file)
        if cookies_path.is_file():
            results.append(CheckResult("cookies file", OK, str(cookies_path)))
        else:
            results.append(
                CheckResult("cookies file", FAIL, f"{cookies_path} is missing")
            )

    if settings.inline_cache_chat_id is None:
        results.append(
            CheckResult(
                "inline cache chat",
                WARN,
                "INLINE_CACHE_CHAT_ID is unset; inline uploads use the user's chat",
            )
        )
    else:
        results.append(
            CheckResult(
                "inline cache chat", OK, str(settings.inline_cache_chat_id)
            )
        )

    return results


def format_report(results: list[CheckResult]) -> str:
    width = max(len(result.name) for result in results)
    lines = ["Deployment checks:"]

    for result in results:
        icon = _STATUS_ICONS.get(result.status, result.status.upper())
        lines.append(f"  [{icon}] {result.name.ljust(width)}  {result.detail}")

    failed = sum(1 for result in results if result.status == FAIL)
    warned = sum(1 for result in results if result.status == WARN)
    lines.append("")
    lines.append(
        f"{len(results)} checks, {failed} failed, {warned} warnings"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args

    unknown = [arg for arg in args if arg not in ("--json",)]
    if unknown:
        print(f"unknown arguments: {' '.join(unknown)}", file=sys.stderr)
        print("usage: python -m app doctor [--json]", file=sys.stderr)
        return 2

    try:
        settings = get_settings()
    except ValidationError as exc:
        missing = ", ".join(
            str(error["loc"][0]) for error in exc.errors() if error.get("loc")
        )
        results = [
            CheckResult(
                "configuration",
                FAIL,
                f"invalid or missing settings: {missing or exc}",
            )
        ]
        if as_json:
            print(json.dumps([asdict(result) for result in results], indent=2))
        else:
            print(format_report(results))
        return 1

    results = run_checks(settings)

    if as_json:
        print(
            json.dumps(
                {
                    "results": [asdict(result) for result in results],
                    "failed": sum(1 for r in results if r.status == FAIL),
                    "warnings": sum(1 for r in results if r.status == WARN),
                },
                indent=2,
            )
        )
    else:
        print(format_report(results))

    return 1 if any(result.failed for result in results) else 0
