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


def test_redact_strips_tokens_from_errors():
    text = redact("Authorization: Bearer ya29.SECRET stuff Cookie=SAPISID=abc")
    assert "ya29.SECRET" not in text
    assert "abc" not in text or "SAPISID=[redacted]" in text
    try:
        raise RuntimeError("failed access_token=ya29.SECRET for user")
    except RuntimeError as exc:
        public = public_error(exc)
    assert "ya29.SECRET" not in public
