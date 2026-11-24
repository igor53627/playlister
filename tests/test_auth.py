import json
import stat

import pytest

from playlister.auth import AuthError, resolve_auth
from playlister.redact import public_error, redact


def test_browser_file_is_tightened_and_not_echoed(tmp_path, monkeypatch):
    auth = tmp_path / "auth.json"
    secret = "ya29.SUPER_SECRET_TOKEN"
    auth.write_text(
        json.dumps(
            {
                "Authorization": f"Bearer {secret}",
                "Cookie": "SAPISID=super-secret-cookie; HSID=also-secret",
                "Accept": "*/*",
            }
        ),
        encoding="utf-8",
    )
    auth.chmod(0o644)
    monkeypatch.setenv("PLAYLISTER_AUTH_PATH", str(auth))
    monkeypatch.delenv("PLAYLISTER_OAUTH_CLIENT_ID", raising=False)

    config = resolve_auth()
    assert config.kind == "browser"
    assert stat.S_IMODE(auth.stat().st_mode) == 0o600
    blob = repr(config)
    assert secret not in blob
    assert "super-secret-cookie" not in blob
    assert config.oauth_client_secret is None


def test_oauth_requires_client_env_without_leaking_the_file(tmp_path, monkeypatch):
    auth = tmp_path / "oauth.json"
    refresh = "1//refresh-token-value-should-stay-local"
    auth.write_text(
        json.dumps(
            {
                "access_token": "ya29.ACCESS",
                "refresh_token": refresh,
                "token_type": "Bearer",
            }
        ),
        encoding="utf-8",
    )
    auth.chmod(0o600)
    monkeypatch.setenv("PLAYLISTER_AUTH_PATH", str(auth))
    monkeypatch.delenv("PLAYLISTER_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("PLAYLISTER_OAUTH_CLIENT_SECRET", raising=False)

    with pytest.raises(AuthError) as caught:
        resolve_auth()
    message = str(caught.value)
    assert "PLAYLISTER_OAUTH_CLIENT_ID" in message
    assert refresh not in message
    assert "ya29.ACCESS" not in message


def test_oauth_config_keeps_secrets_off_the_kind_only_path(tmp_path, monkeypatch):
    auth = tmp_path / "oauth.json"
    auth.write_text(
        json.dumps({"refresh_token": "r", "access_token": "a", "token_type": "Bearer"}),
        encoding="utf-8",
    )
    auth.chmod(0o600)
    monkeypatch.setenv("PLAYLISTER_AUTH_PATH", str(auth))
    monkeypatch.setenv("PLAYLISTER_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("PLAYLISTER_OAUTH_CLIENT_SECRET", "client-secret")
    config = resolve_auth()
    assert config.kind == "oauth"
    assert config.oauth_client_id == "client-id"
    assert "client-secret" not in repr(config)


def test_missing_file_and_anonymous_default(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYLISTER_AUTH_PATH", str(tmp_path / "missing.json"))
    with pytest.raises(AuthError) as caught:
        resolve_auth()
    assert "ytmusicapi" in str(caught.value)

    monkeypatch.delenv("PLAYLISTER_AUTH_PATH")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert resolve_auth().kind == "anonymous"


def test_prepare_auth_file_survives_an_unreadable_parent(monkeypatch):
    from pathlib import Path

    from playlister.auth import AuthError, prepare_auth_file, resolve_auth

    def denied(self):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "is_file", denied)
    monkeypatch.setenv("PLAYLISTER_AUTH_PATH", "/root/hidden/auth.json")
    prepare_auth_file()
    with pytest.raises(AuthError) as caught:
        resolve_auth()
    assert "could not be read" in str(caught.value)
    assert "hidden" not in str(caught.value)


def test_prepare_auth_file_tightens_without_reading_secrets(tmp_path, monkeypatch):
    from playlister.auth import prepare_auth_file

    auth = tmp_path / "auth.json"
    auth.write_text('{"Authorization": "Bearer ya29.SECRET"}', encoding="utf-8")
    auth.chmod(0o644)
    monkeypatch.setenv("PLAYLISTER_AUTH_PATH", str(auth))
    prepare_auth_file()
    assert stat.S_IMODE(auth.stat().st_mode) == 0o600


def test_redact_strips_tokens_from_errors():
    text = redact("Authorization: Bearer ya29.SECRET stuff Cookie=SAPISID=abc 1//REFRESH_XYZ")
    assert "ya29.SECRET" not in text
    assert "1//REFRESH_XYZ" not in text
    assert "abc" not in text or "SAPISID=[redacted]" in text
    try:
        raise RuntimeError("failed access_token=ya29.SECRET for user")
    except RuntimeError as exc:
        public = public_error(exc)
    assert "ya29.SECRET" not in public


def test_oauth_detection_with_refresh_token_expires_in_and_extra_fields(tmp_path, monkeypatch):
    auth = tmp_path / "oauth.json"
    auth.write_text(
        json.dumps(
            {
                "access_token": "ya29.ACCESS",
                "refresh_token": "1//REFRESH",
                "expires_at": 9999999999,
                "expires_in": 3599,
                "refresh_token_expires_in": 604799,
                "scope": "https://www.googleapis.com/auth/youtube",
                "token_type": "Bearer",
                "unknown_field": True,
            }
        ),
        encoding="utf-8",
    )
    auth.chmod(0o600)
    monkeypatch.setenv("PLAYLISTER_AUTH_PATH", str(auth))
    monkeypatch.setenv("PLAYLISTER_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("PLAYLISTER_OAUTH_CLIENT_SECRET", "csec")
    config = resolve_auth()
    assert config.kind == "oauth"


def test_build_ytmusic_is_unauthenticated_in_oauth_mode(tmp_path, monkeypatch):
    import ytmusicapi

    from playlister.auth import AuthConfig, build_ytmusic

    calls: list[tuple[tuple, dict]] = []

    class _StubYTMusic:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(ytmusicapi, "YTMusic", _StubYTMusic)

    oauth_path = tmp_path / "oauth.json"
    oauth_path.write_text("{}", encoding="utf-8")
    build_ytmusic(
        AuthConfig(
            path=oauth_path,
            kind="oauth",
            oauth_client_id="cid",
            oauth_client_secret="csec",
        )
    )
    assert calls[-1] == ((), {})

    browser_path = tmp_path / "browser.json"
    browser_path.write_text("{}", encoding="utf-8")
    build_ytmusic(AuthConfig(path=browser_path, kind="browser", brand_account="brand123"))
    assert calls[-1] == ((str(browser_path),), {"user": "brand123"})
