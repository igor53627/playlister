"""Google OAuth token management and device authorization flow.

Tokens are stored locally in JSON with mode 600 and written atomically.
Token values and client secrets are never logged.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import stat
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests

from playlister.auth import AuthError

logger = logging.getLogger("playlister")

OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
OAUTH_DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"

_EXPIRY_SKEW_SECONDS = 60
_DEFAULT_TIMEOUT = 30.0


def write_secret_json(path: Path, data: dict[str, Any]) -> None:
    """Atomically write JSON to ``path`` with mode 600 on POSIX."""
    parent = path.parent
    parent_existed = parent.exists()
    parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix" and not parent_existed:
        with contextlib.suppress(OSError):
            parent.chmod(0o700)

    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(parent))
    tmp_path = Path(tmp_name)
    try:
        if os.name == "posix":
            os.chmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        payload = json.dumps(data, indent=2) + "\n"
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise


def _parse_json_dict(response: requests.Response) -> dict[str, Any]:
    try:
        parsed = response.json()
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class OAuthTokenManager:
    """Manage a local Google OAuth token file with automatic refresh."""

    def __init__(
        self,
        path: Path,
        *,
        client_id: str,
        client_secret: str,
        session: requests.Session | None = None,
        now: Callable[[], float] | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._path = path
        self._client_id = client_id
        self._client_secret = client_secret
        self._session = session or requests.Session()
        self._now = now or time.time
        self._timeout = timeout
        self._token: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._token is not None:
            return self._token
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise AuthError("OAuth auth file is not valid JSON") from None
        except OSError:
            raise AuthError("OAuth auth file could not be read") from None
        if not isinstance(loaded, dict):
            raise AuthError("OAuth auth file must be a JSON object")
        self._token = dict(loaded)
        return self._token

    def _is_expired(self, token: dict[str, Any]) -> bool:
        if not token.get("access_token"):
            return True
        expires_at = token.get("expires_at")
        if expires_at is None:
            return False
        try:
            return self._now() >= float(expires_at) - _EXPIRY_SKEW_SECONDS
        except (TypeError, ValueError):
            return True

    def get_access_token(self, *, force_refresh: bool = False) -> str:
        """Return a valid access token, refreshing and persisting if needed."""
        token = self._load()
        if force_refresh or self._is_expired(token):
            token = self.refresh()
        access_token = token.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise AuthError(
                "OAuth auth file has no access_token. Run `playlister auth oauth` to authenticate."
            )
        return access_token

    def refresh(self) -> dict[str, Any]:
        """Refresh the access token using the stored refresh_token."""
        token = self._load()
        refresh_token = token.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise AuthError(
                "OAuth token is expired and has no refresh_token. "
                "Run `playlister auth oauth` to re-authenticate."
            )

        try:
            response = self._session.post(
                OAUTH_TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise AuthError(f"OAuth token refresh request failed ({type(exc).__name__})") from None

        body = _parse_json_dict(response)
        if response.status_code != 200:
            error_code = str(body.get("error") or "")
            if error_code == "invalid_grant":
                raise AuthError(
                    "OAuth refresh token was revoked or expired "
                    "(Google Testing-mode apps expire refresh tokens after 7 days). "
                    "Run `playlister auth oauth` to re-authenticate."
                )
            detail = error_code or f"HTTP {response.status_code}"
            raise AuthError(f"OAuth token refresh failed ({detail})")

        new_access = body.get("access_token")
        if not isinstance(new_access, str) or not new_access:
            raise AuthError("OAuth token refresh response did not include an access_token")

        # Preserve unknown extra fields and the existing refresh_token when omitted.
        updated = dict(token)
        updated.update(body)
        if not updated.get("refresh_token"):
            updated["refresh_token"] = refresh_token
        expires_in = updated.get("expires_in")
        if isinstance(expires_in, int | float):
            updated["expires_at"] = int(self._now()) + int(expires_in)

        try:
            write_secret_json(self._path, updated)
        except OSError:
            raise AuthError("Refreshed OAuth token could not be saved to disk") from None

        self._token = updated
        logger.info("refreshed OAuth access token")
        return updated


def run_device_flow(
    path: Path,
    *,
    client_id: str,
    client_secret: str,
    scope: str = YOUTUBE_SCOPE,
    session: requests.Session | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
    wall_now: Callable[[], float] = time.time,
    notify: Callable[[str], None] = print,
    timeout: float = _DEFAULT_TIMEOUT,
) -> Path:
    """Run Google's OAuth 2.0 device flow and save the token JSON with mode 600."""
    http = session or requests.Session()
    try:
        code_resp = http.post(
            OAUTH_DEVICE_CODE_URL,
            data={"client_id": client_id, "scope": scope},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise AuthError(f"Device authorization request failed ({type(exc).__name__})") from None

    code_body = _parse_json_dict(code_resp)
    if code_resp.status_code != 200:
        err = str(code_body.get("error") or f"HTTP {code_resp.status_code}")
        raise AuthError(f"Could not start Google device authorization ({err})")

    device_code = code_body.get("device_code")
    user_code = code_body.get("user_code")
    verification_url = code_body.get("verification_url") or code_body.get("verification_uri")
    if not device_code or not user_code or not verification_url:
        raise AuthError("Google device authorization response was missing required fields")

    interval = max(1, int(code_body.get("interval") or 5))
    expires_in = max(1, int(code_body.get("expires_in") or 1800))
    deadline = now() + expires_in

    notify(f"Open this URL in your browser:\n  {verification_url}\n")
    notify(f"Enter this code:\n  {user_code}\n")
    notify("Waiting for authorization...")

    while now() < deadline:
        remaining = deadline - now()
        if remaining <= 0:
            break
        sleep(min(interval, remaining))

        try:
            token_resp = http.post(
                OAUTH_TOKEN_URL,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "device_code": device_code,
                    "grant_type": DEVICE_GRANT_TYPE,
                },
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise AuthError(f"Token polling request failed ({type(exc).__name__})") from None

        token_body = _parse_json_dict(token_resp)
        if token_resp.status_code == 200 and token_body.get("access_token"):
            saved = dict(token_body)
            tok_expires = saved.get("expires_in")
            if isinstance(tok_expires, int | float):
                saved["expires_at"] = int(wall_now()) + int(tok_expires)
            try:
                write_secret_json(path, saved)
            except OSError:
                raise AuthError("OAuth token could not be saved to disk") from None
            return path

        err = str(token_body.get("error") or "")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        if err == "access_denied":
            raise AuthError("Authorization was denied.")
        if err == "expired_token":
            raise AuthError("Device code expired before authorization completed.")
        detail = err or f"HTTP {token_resp.status_code}"
        raise AuthError(f"Google device authorization failed ({detail})")

    raise AuthError("Device code expired before authorization completed.")
