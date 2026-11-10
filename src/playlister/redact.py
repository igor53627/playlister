"""Scrub tokens out of anything that might be logged or returned to a client."""

from __future__ import annotations

import logging
import re

_SECRET = re.compile(
    r"(?i)\b(authorization|cookie|set-cookie|bearer|access_token|refresh_token|"
    r"client_secret|client_id|sapisid|hsid|ssid|sid|__secure-1psid|"
    r"__secure-3psid|visitor_data)\b\s*[:=]\s*\S+"
)
_BARE_TOKEN = re.compile(r"\bya29\.[A-Za-z0-9_\-\.]+")
_LONG_COOKIE = re.compile(r"(?i)(SAPISID|HSID|SSID|SID|LOGIN_INFO)=[^;\s]+")


def redact(text: str) -> str:
    cleaned = _SECRET.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    cleaned = _BARE_TOKEN.sub("[redacted]", cleaned)
    cleaned = _LONG_COOKIE.sub(lambda match: match.group(1) + "=[redacted]", cleaned)
    return cleaned


def public_error(exc: BaseException) -> str:
    message = redact(str(exc)).replace("\n", " ")
    lowered = message.casefold()
    if any(word in lowered for word in ("cookie", "authorization", "bearer", "token")):
        message = ""
    if len(message) > 240:
        message = message[:240] + "..."
    if message:
        return f"{type(exc).__name__}: {message}"
    return type(exc).__name__


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(arg) if isinstance(arg, str) else arg for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {
                key: redact(value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger("playlister")
    root.setLevel(level.upper())
    root.propagate = False
    if root.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    handler.addFilter(RedactFilter())
    root.addHandler(handler)
