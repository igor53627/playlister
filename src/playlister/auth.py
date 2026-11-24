"""Load a local auth file (browser headers or Google OAuth). Never log secrets."""

from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path


class AuthError(Exception):
    """The auth file is missing or incomplete. The message is safe to show."""


@dataclass(frozen=True)
class AuthConfig:
    path: Path | None
    kind: str  # anonymous, browser, oauth
    oauth_client_id: str | None = field(default=None, repr=False)
    oauth_client_secret: str | None = field(default=None, repr=False)
    brand_account: str | None = field(default=None, repr=False)


def _is_file(path: Path) -> bool:
    """False when the path is missing or a parent directory cannot be entered.

    ``Path.is_file`` raises ``PermissionError`` on Python 3.11 in that case.
    Startup must not die on it.
    """
    try:
        return path.is_file()
    except OSError:
        return False


def default_auth_path() -> Path:
    """Target path for auth files: PLAYLISTER_AUTH_PATH or ~/.config/playlister/auth.json."""
    raw = os.environ.get("PLAYLISTER_AUTH_PATH")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".config" / "playlister" / "auth.json"


def auth_path_from_env() -> Path | None:
    raw = os.environ.get("PLAYLISTER_AUTH_PATH")
    if raw:
        return Path(raw).expanduser()
    default = Path.home() / ".config" / "playlister" / "auth.json"
    if _is_file(default):
        return default
    return None


def prepare_auth_file() -> None:
    """Set mode 600 on the auth file when the server starts.

    Missing files are left alone so search still works without auth. The file
    body is not read. A failure is logged and does not stop the server; the
    next authenticated tool call reports it.
    """
    try:
        path = auth_path_from_env()
        if path is None or not _is_file(path):
            return
        _tighten(path)
    except (AuthError, OSError) as exc:
        logging.getLogger("playlister").warning("%s", type(exc).__name__)


def _tighten(path: Path) -> None:
    if os.name != "posix":
        return
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            raise AuthError(
                "Auth file is readable by other users and could not be changed to mode 600"
            ) from None


def _is_oauth_payload(loaded: dict[str, object]) -> bool:
    """Distinguish Google OAuth token JSON from browser-header JSON."""
    if "refresh_token" in loaded:
        return True
    return "access_token" in loaded and any(
        key in loaded for key in ("token_type", "expires_at", "expires_in", "scope")
    )


def resolve_auth() -> AuthConfig:
    """Inspect the local auth file and environment without opening the network."""
    path = auth_path_from_env()
    brand = os.environ.get("PLAYLISTER_BRAND_ACCOUNT") or None
    if path is None:
        return AuthConfig(path=None, kind="anonymous", brand_account=brand)
    try:
        exists = path.is_file()
    except OSError:
        raise AuthError("Auth file could not be read") from None
    if not exists:
        raise AuthError(
            "PLAYLISTER_AUTH_PATH does not point at a file. "
            "Create it with `playlister auth oauth` or `ytmusicapi browser` "
            "(see the README) and chmod 600 it."
        )
    _tighten(path)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise AuthError("Auth file is not valid JSON") from None
    except OSError:
        raise AuthError("Auth file could not be read") from None
    if not isinstance(loaded, dict):
        raise AuthError("Auth file must be a JSON object")

    if _is_oauth_payload(loaded):
        client_id = os.environ.get("PLAYLISTER_OAUTH_CLIENT_ID")
        client_secret = os.environ.get("PLAYLISTER_OAUTH_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise AuthError(
                "OAuth auth file requires PLAYLISTER_OAUTH_CLIENT_ID and "
                "PLAYLISTER_OAUTH_CLIENT_SECRET in the environment"
            )
        return AuthConfig(
            path=path,
            kind="oauth",
            oauth_client_id=client_id,
            oauth_client_secret=client_secret,
            brand_account=brand,
        )
    return AuthConfig(path=path, kind="browser", brand_account=brand)


def build_ytmusic(config: AuthConfig):
    """Construct a ``YTMusic`` client.

    In OAuth mode, ytmusicapi is always unauthenticated (catalog search and
    radio only); account operations use the official YouTube Data API v3.
    """
    from ytmusicapi import YTMusic

    if config.kind == "browser" and config.path is not None:
        extra: dict[str, object] = {}
        if config.brand_account:
            extra["user"] = config.brand_account
        return YTMusic(str(config.path), **extra)
    return YTMusic()
