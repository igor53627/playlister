import json
import logging
import stat
from typing import Any

import pytest
import requests

from playlister.auth import AuthError
from playlister.cli import main
from playlister.oauth import (
    OAUTH_DEVICE_CODE_URL,
    OAUTH_TOKEN_URL,
    YOUTUBE_SCOPE,
    OAuthTokenManager,
    run_device_flow,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = b"" if payload is None else json.dumps(payload).encode("utf-8")

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no body")
        return self._payload


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, data: dict[str, Any] | None = None, timeout: float = 30.0):
        self.calls.append((url, dict(data or {})))
        if not self.responses:
            raise AssertionError(f"Unexpected POST to {url}")
        return self.responses.pop(0)


def test_token_manager_tolerates_extra_fields_and_skips_refresh_when_fresh(tmp_path):
    token_path = tmp_path / "auth.json"
    token_path.write_text(
        json.dumps(
            {
                "access_token": "ya29.FRESH_ACCESS",
                "refresh_token": "1//REFRESH_VALUE",
                "expires_at": 2_000,
                "expires_in": 3600,
                "refresh_token_expires_in": 604800,
                "scope": YOUTUBE_SCOPE,
                "token_type": "Bearer",
                "extra_google_field": "kept",
            }
        ),
        encoding="utf-8",
    )
    token_path.chmod(0o600)
    session = _FakeSession([])
    manager = OAuthTokenManager(
        token_path,
        client_id="cid",
        client_secret="csecret",
        session=session,  # type: ignore[arg-type]
        now=lambda: 1_500.0,
    )

    assert manager.get_access_token() == "ya29.FRESH_ACCESS"
    assert session.calls == []


def test_token_manager_refreshes_expired_token_atomically_with_mode_600(tmp_path, caplog):
    token_path = tmp_path / "auth.json"
    token_path.write_text(
        json.dumps(
            {
                "access_token": "ya29.OLD_TOKEN",
                "refresh_token": "1//KEEP_THIS_REFRESH",
                "expires_at": 1_030,
                "expires_in": 3600,
                "refresh_token_expires_in": 604800,
                "scope": YOUTUBE_SCOPE,
                "token_type": "Bearer",
                "custom_meta": "still-here",
            }
        ),
        encoding="utf-8",
    )
    token_path.chmod(0o644)

    session = _FakeSession(
        [
            _FakeResponse(
                200,
                {
                    "access_token": "ya29.NEW_ACCESS_SECRET",
                    "expires_in": 3599,
                    "scope": YOUTUBE_SCOPE,
                    "token_type": "Bearer",
                },
            )
        ]
    )
    manager = OAuthTokenManager(
        token_path,
        client_id="my-client-id",
        client_secret="my-client-secret",
        session=session,  # type: ignore[arg-type]
        now=lambda: 1_000.0,  # within 60s skew of 1_030
    )

    with caplog.at_level(logging.DEBUG, logger="playlister"):
        token = manager.get_access_token()

    assert token == "ya29.NEW_ACCESS_SECRET"
    assert len(session.calls) == 1
    url, form = session.calls[0]
    assert url == OAUTH_TOKEN_URL
    assert form == {
        "client_id": "my-client-id",
        "client_secret": "my-client-secret",
        "refresh_token": "1//KEEP_THIS_REFRESH",
        "grant_type": "refresh_token",
    }

    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
    saved = json.loads(token_path.read_text(encoding="utf-8"))
    assert saved["access_token"] == "ya29.NEW_ACCESS_SECRET"
    assert saved["refresh_token"] == "1//KEEP_THIS_REFRESH"
    assert saved["refresh_token_expires_in"] == 604800
    assert saved["custom_meta"] == "still-here"
    assert saved["expires_at"] == 4_599

    # Never log tokens or client secret
    log_text = caplog.text
    assert "ya29.NEW_ACCESS_SECRET" not in log_text
    assert "1//KEEP_THIS_REFRESH" not in log_text
    assert "my-client-secret" not in log_text


def test_token_manager_surfaces_invalid_grant_without_leaking_secrets(tmp_path):
    token_path = tmp_path / "auth.json"
    token_path.write_text(
        json.dumps(
            {
                "access_token": "ya29.OLD",
                "refresh_token": "1//EXPIRED_REFRESH",
                "expires_at": 100,
                "token_type": "Bearer",
            }
        ),
        encoding="utf-8",
    )
    session = _FakeSession(
        [_FakeResponse(400, {"error": "invalid_grant", "error_description": "Token expired"})]
    )
    manager = OAuthTokenManager(
        token_path,
        client_id="cid",
        client_secret="super-secret",
        session=session,  # type: ignore[arg-type]
        now=lambda: 500.0,
    )

    with pytest.raises(AuthError) as caught:
        manager.get_access_token()
    msg = str(caught.value)
    assert "7 days" in msg
    assert "playlister auth oauth" in msg
    assert "1//EXPIRED_REFRESH" not in msg
    assert "super-secret" not in msg


def test_token_manager_missing_refresh_token_fails_cleanly(tmp_path):
    token_path = tmp_path / "auth.json"
    token_path.write_text(
        json.dumps({"access_token": "ya29.OLD", "expires_at": 100, "token_type": "Bearer"}),
        encoding="utf-8",
    )
    manager = OAuthTokenManager(
        token_path,
        client_id="cid",
        client_secret="sec",
        session=_FakeSession([]),  # type: ignore[arg-type]
        now=lambda: 500.0,
    )
    with pytest.raises(AuthError, match="no refresh_token"):
        manager.get_access_token()


def test_token_manager_network_failure_raises_auth_error(tmp_path):
    token_path = tmp_path / "auth.json"
    token_path.write_text(
        json.dumps(
            {
                "access_token": "ya29.OLD",
                "refresh_token": "1//REFRESH",
                "expires_at": 100,
                "token_type": "Bearer",
            }
        ),
        encoding="utf-8",
    )

    class _BoomSession:
        def post(self, *args, **kwargs):
            raise requests.ConnectionError("dns failed")

    manager = OAuthTokenManager(
        token_path,
        client_id="cid",
        client_secret="sec",
        session=_BoomSession(),  # type: ignore[arg-type]
        now=lambda: 500.0,
    )
    with pytest.raises(AuthError, match="ConnectionError"):
        manager.get_access_token()


def test_device_flow_polls_handles_pending_and_slow_down_and_saves_600(tmp_path):
    target = tmp_path / "nested" / "auth.json"
    session = _FakeSession(
        [
            _FakeResponse(
                200,
                {
                    "device_code": "dev-code-123",
                    "user_code": "WXYZ-1234",
                    "verification_url": "https://www.google.com/device",
                    "expires_in": 1800,
                    "interval": 5,
                },
            ),
            _FakeResponse(428, {"error": "authorization_pending"}),
            _FakeResponse(400, {"error": "slow_down"}),
            _FakeResponse(
                200,
                {
                    "access_token": "ya29.DEVICE_ACCESS",
                    "refresh_token": "1//DEVICE_REFRESH",
                    "expires_in": 3600,
                    "refresh_token_expires_in": 604799,
                    "scope": YOUTUBE_SCOPE,
                    "token_type": "Bearer",
                },
            ),
        ]
    )
    clock = {"t": 0.0}
    sleeps: list[float] = []
    messages: list[str] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["t"] += seconds

    saved_path = run_device_flow(
        target,
        client_id="cid",
        client_secret="csec",
        session=session,  # type: ignore[arg-type]
        sleep=fake_sleep,
        now=lambda: clock["t"],
        wall_now=lambda: 10_000.0,
        notify=messages.append,
    )

    assert saved_path == target
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert sleeps == [5, 5, 10]
    joined = "\n".join(messages)
    assert "https://www.google.com/device" in joined
    assert "WXYZ-1234" in joined
    assert "ya29.DEVICE_ACCESS" not in joined
    assert "1//DEVICE_REFRESH" not in joined

    assert session.calls[0][0] == OAUTH_DEVICE_CODE_URL
    assert session.calls[0][1] == {"client_id": "cid", "scope": YOUTUBE_SCOPE}
    assert session.calls[1][0] == OAUTH_TOKEN_URL

    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["access_token"] == "ya29.DEVICE_ACCESS"
    assert saved["refresh_token"] == "1//DEVICE_REFRESH"
    assert saved["refresh_token_expires_in"] == 604799
    assert saved["expires_at"] == 13_600


def test_device_flow_access_denied_and_expired(tmp_path):
    target = tmp_path / "auth.json"
    code_resp = _FakeResponse(
        200,
        {
            "device_code": "dev",
            "user_code": "ABCD-EFGH",
            "verification_url": "https://www.google.com/device",
            "expires_in": 60,
            "interval": 5,
        },
    )

    denied_session = _FakeSession([code_resp, _FakeResponse(403, {"error": "access_denied"})])
    with pytest.raises(AuthError, match="denied"):
        run_device_flow(
            target,
            client_id="cid",
            client_secret="sec",
            session=denied_session,  # type: ignore[arg-type]
            sleep=lambda _: None,
            now=lambda: 0.0,
            notify=lambda _: None,
        )
    assert not target.exists()

    expired_session = _FakeSession([code_resp, _FakeResponse(400, {"error": "expired_token"})])
    with pytest.raises(AuthError, match="expired"):
        run_device_flow(
            target,
            client_id="cid",
            client_secret="sec",
            session=expired_session,  # type: ignore[arg-type]
            sleep=lambda _: None,
            now=lambda: 0.0,
            notify=lambda _: None,
        )


def test_cli_auth_oauth_subcommand(tmp_path, monkeypatch, capsys):
    target = tmp_path / "oauth.json"
    monkeypatch.setenv("PLAYLISTER_OAUTH_CLIENT_ID", "cli-id")
    monkeypatch.setenv("PLAYLISTER_OAUTH_CLIENT_SECRET", "cli-secret")

    called: dict[str, Any] = {}

    def fake_device_flow(path, *, client_id, client_secret):
        called["path"] = path
        called["client_id"] = client_id
        called["client_secret"] = client_secret
        return path

    monkeypatch.setattr("playlister.cli.run_device_flow", fake_device_flow)
    rc = main(["auth", "oauth", "--file", str(target)])
    assert rc == 0
    assert called == {"path": target, "client_id": "cli-id", "client_secret": "cli-secret"}
    out = capsys.readouterr().out
    assert str(target) in out
    assert "cli-secret" not in out


def test_cli_auth_oauth_missing_env_in_non_tty_returns_error(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("PLAYLISTER_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("PLAYLISTER_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    rc = main(["auth", "oauth", "--file", str(tmp_path / "oauth.json")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "PLAYLISTER_OAUTH_CLIENT_ID" in err


def test_device_flow_converts_save_oserror_to_auth_error(tmp_path, monkeypatch):
    target = tmp_path / "auth.json"
    session = _FakeSession(
        [
            _FakeResponse(
                200,
                {
                    "device_code": "dev",
                    "user_code": "ABCD-EFGH",
                    "verification_url": "https://www.google.com/device",
                    "expires_in": 60,
                    "interval": 1,
                },
            ),
            _FakeResponse(
                200,
                {
                    "access_token": "ya29.TOK",
                    "refresh_token": "1//REF",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            ),
        ]
    )

    def fail_write(*args, **kwargs):
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr("playlister.oauth.write_secret_json", fail_write)
    with pytest.raises(AuthError, match="could not be saved"):
        run_device_flow(
            target,
            client_id="cid",
            client_secret="sec",
            session=session,  # type: ignore[arg-type]
            sleep=lambda _: None,
            now=lambda: 0.0,
            notify=lambda _: None,
        )
