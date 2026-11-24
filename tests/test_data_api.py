import json
import stat
from typing import Any

import pytest

from playlister.auth import AuthError
from playlister.data_api import DATA_API_BASE, DataAPIError, QuotaExceededError, YouTubeDataAPI
from playlister.oauth import OAUTH_TOKEN_URL, OAuthTokenManager


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = b"" if payload is None else json.dumps(payload).encode("utf-8")

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("empty")
        return self._payload


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": dict(params or {}),
                "json": json,
                "headers": dict(headers or {}),
            }
        )
        if not self.responses:
            raise AssertionError(f"Unexpected {method} {url}")
        return self.responses.pop(0)

    def post(self, url: str, data: dict[str, Any] | None = None, timeout: float = 30.0):
        self.calls.append({"method": "POST", "url": url, "data": dict(data or {})})
        if not self.responses:
            raise AssertionError(f"Unexpected POST {url}")
        return self.responses.pop(0)


def _make_api(
    tmp_path,
    responses: list[_FakeResponse],
    *,
    expires_at: int = 9_999,
    now: float = 1_000.0,
) -> tuple[YouTubeDataAPI, _FakeSession, list[float], Any]:
    token_path = tmp_path / "auth.json"
    token_path.write_text(
        json.dumps(
            {
                "access_token": "ya29.INIT_TOKEN",
                "refresh_token": "1//REFRESH_TOKEN",
                "expires_at": expires_at,
                "expires_in": 3600,
                "refresh_token_expires_in": 604800,
                "token_type": "Bearer",
            }
        ),
        encoding="utf-8",
    )
    token_path.chmod(0o600)
    session = _FakeSession(responses)
    sleeps: list[float] = []
    tokens = OAuthTokenManager(
        token_path,
        client_id="cid",
        client_secret="csec",
        session=session,  # type: ignore[arg-type]
        now=lambda: now,
    )
    api = YouTubeDataAPI(
        tokens,
        session=session,  # type: ignore[arg-type]
        sleep=sleeps.append,
    )
    return api, session, sleeps, token_path


def test_list_playlists_paginates_and_respects_limit(tmp_path):
    api, session, _, _ = _make_api(
        tmp_path,
        [
            _FakeResponse(
                200,
                {
                    "items": [
                        {
                            "id": "PL1",
                            "snippet": {"title": "First"},
                            "contentDetails": {"itemCount": 3},
                            "status": {"privacyStatus": "private"},
                        },
                        {
                            "id": "PL2",
                            "snippet": {"title": "Second"},
                            "contentDetails": {"itemCount": 7},
                            "status": {"privacyStatus": "unlisted"},
                        },
                    ],
                    "nextPageToken": "page2",
                },
            ),
            _FakeResponse(
                200,
                {
                    "items": [
                        {
                            "id": "PL3",
                            "snippet": {"title": "Third"},
                            "contentDetails": {"itemCount": 1},
                            "status": {"privacyStatus": "public"},
                        }
                    ]
                },
            ),
        ],
    )

    result = api.list_playlists(limit=0)
    assert [p["playlist_id"] for p in result] == ["PL1", "PL2", "PL3"]
    assert result[0] == {
        "playlist_id": "PL1",
        "title": "First",
        "count": 3,
        "owned": True,
        "privacy": "PRIVATE",
    }
    assert result[1]["privacy"] == "UNLISTED"
    assert session.calls[0]["url"] == f"{DATA_API_BASE}/playlists"
    assert session.calls[0]["params"]["mine"] == "true"
    assert session.calls[1]["params"]["pageToken"] == "page2"
    assert session.calls[0]["headers"]["Authorization"] == "Bearer ya29.INIT_TOKEN"


def test_get_playlist_paginates_and_returns_playlist_item_id_and_artist(tmp_path):
    api, session, _, _ = _make_api(
        tmp_path,
        [
            _FakeResponse(
                200,
                {
                    "items": [
                        {
                            "id": "PL1",
                            "snippet": {
                                "title": "Road",
                                "description": "driving songs",
                                "channelTitle": "Me",
                            },
                            "contentDetails": {"itemCount": 2},
                            "status": {"privacyStatus": "private"},
                        }
                    ]
                },
            ),
            _FakeResponse(
                200,
                {
                    "items": [
                        {
                            "id": "PLI_1",
                            "snippet": {
                                "title": "Wonderwall",
                                "videoOwnerChannelTitle": "Oasis - Topic",
                                "resourceId": {"videoId": "vid1"},
                            },
                            "contentDetails": {"videoId": "vid1"},
                        }
                    ],
                    "nextPageToken": "next",
                },
            ),
            _FakeResponse(
                200,
                {
                    "items": [
                        {
                            "id": "PLI_2",
                            "snippet": {
                                "title": "Группа крови",
                                "videoOwnerChannelTitle": "Кино",
                                "resourceId": {"videoId": "vid2"},
                            },
                            "contentDetails": {"videoId": "vid2"},
                        }
                    ]
                },
            ),
        ],
    )

    playlist = api.get_playlist("PL1", limit=0)
    assert playlist["ok"] is True
    assert playlist["playlist_id"] == "PL1"
    assert playlist["title"] == "Road"
    assert playlist["privacy"] == "PRIVATE"
    assert playlist["track_count"] == 2
    assert playlist["author"] == "Me"
    assert len(playlist["tracks"]) == 2
    assert playlist["tracks"][0]["video_id"] == "vid1"
    assert playlist["tracks"][0]["playlist_item_id"] == "PLI_1"
    assert playlist["tracks"][0]["artists"] == ["Oasis"]
    assert playlist["tracks"][0]["channel"] == "Oasis - Topic"
    assert playlist["tracks"][1]["playlist_item_id"] == "PLI_2"
    assert playlist["tracks"][1]["artists"] == ["Кино"]


def test_get_playlist_not_found_raises_404(tmp_path):
    api, _, _, _ = _make_api(tmp_path, [_FakeResponse(200, {"items": []})])
    with pytest.raises(DataAPIError) as caught:
        api.get_playlist("PLmissing")
    assert caught.value.status_code == 404


def test_create_playlist_sends_lowercase_privacy(tmp_path):
    api, session, _, _ = _make_api(tmp_path, [_FakeResponse(200, {"id": "PLcreated"})])
    created = api.create_playlist("Chill", "late night", "PRIVATE")
    assert created == "PLcreated"
    call = session.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == f"{DATA_API_BASE}/playlists"
    assert call["params"] == {"part": "snippet,status"}
    assert call["json"] == {
        "snippet": {"title": "Chill", "description": "late night"},
        "status": {"privacyStatus": "private"},
    }


def test_insert_playlist_item_retries_transient_409_and_503(tmp_path):
    api, session, sleeps, _ = _make_api(
        tmp_path,
        [
            _FakeResponse(409, {"error": {"code": 409, "message": "conflict"}}),
            _FakeResponse(503, {"error": {"code": 503, "message": "backend error"}}),
            _FakeResponse(200, {"id": "PLI_new"}),
        ],
    )
    res = api.insert_playlist_item("PL1", "vid1")
    assert res["id"] == "PLI_new"
    assert sleeps == [0.5, 1.0]
    assert len(session.calls) == 3
    assert session.calls[0]["json"] == {
        "snippet": {
            "playlistId": "PL1",
            "resourceId": {"kind": "youtube#video", "videoId": "vid1"},
        }
    }


def test_delete_playlist_item_and_rate_video_accept_204(tmp_path):
    api, session, _, _ = _make_api(
        tmp_path,
        [_FakeResponse(204, None), _FakeResponse(204, None)],
    )
    api.delete_playlist_item("PLI_1")
    api.rate_video("vid1", "like")

    assert session.calls[0]["method"] == "DELETE"
    assert session.calls[0]["url"] == f"{DATA_API_BASE}/playlistItems"
    assert session.calls[0]["params"] == {"id": "PLI_1"}

    assert session.calls[1]["method"] == "POST"
    assert session.calls[1]["url"] == f"{DATA_API_BASE}/videos/rate"
    assert session.calls[1]["params"] == {"id": "vid1", "rating": "like"}


def test_get_video_ratings_chunks_at_50(tmp_path):
    ids = [f"v{i}" for i in range(55)]
    api, session, _, _ = _make_api(
        tmp_path,
        [
            _FakeResponse(200, {"items": [{"videoId": vid, "rating": "like"} for vid in ids[:50]]}),
            _FakeResponse(200, {"items": [{"videoId": vid, "rating": "none"} for vid in ids[50:]]}),
        ],
    )
    ratings = api.get_video_ratings(ids)
    assert len(ratings) == 55
    assert ratings[0] == {"video_id": "v0", "rating": "like"}
    assert ratings[54] == {"video_id": "v54", "rating": "none"}
    assert len(session.calls) == 2
    assert len(session.calls[0]["params"]["id"].split(",")) == 50
    assert len(session.calls[1]["params"]["id"].split(",")) == 5


def test_401_triggers_token_refresh_and_retries_once(tmp_path):
    api, session, _, token_path = _make_api(
        tmp_path,
        [
            _FakeResponse(401, {"error": {"code": 401, "message": "Invalid Credentials"}}),
            _FakeResponse(
                200,
                {
                    "access_token": "ya29.REFRESHED_TOKEN",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            ),
            _FakeResponse(
                200,
                {
                    "items": [
                        {
                            "id": "PL1",
                            "snippet": {"title": "Mine"},
                            "contentDetails": {"itemCount": 0},
                            "status": {"privacyStatus": "private"},
                        }
                    ]
                },
            ),
        ],
    )

    playlists = api.list_playlists()
    assert len(playlists) == 1
    assert playlists[0]["playlist_id"] == "PL1"
    assert session.calls[0]["headers"]["Authorization"] == "Bearer ya29.INIT_TOKEN"
    assert session.calls[1]["url"] == OAUTH_TOKEN_URL
    assert session.calls[2]["headers"]["Authorization"] == "Bearer ya29.REFRESHED_TOKEN"
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
    saved = json.loads(token_path.read_text(encoding="utf-8"))
    assert saved["access_token"] == "ya29.REFRESHED_TOKEN"
    assert saved["refresh_token_expires_in"] == 604800


def test_double_401_raises_auth_error(tmp_path):
    api, _, _, _ = _make_api(
        tmp_path,
        [
            _FakeResponse(401, {"error": {"code": 401, "message": "Unauthorized"}}),
            _FakeResponse(
                200, {"access_token": "ya29.STILL_BAD", "expires_in": 3600, "token_type": "Bearer"}
            ),
            _FakeResponse(401, {"error": {"code": 401, "message": "Unauthorized"}}),
        ],
    )
    with pytest.raises(AuthError, match="rejected the OAuth token"):
        api.list_playlists()


def test_quota_exceeded_surfaces_clearly_and_does_not_retry(tmp_path):
    quota_payload = {
        "error": {
            "code": 403,
            "message": (
                "The request cannot be completed because you have exceeded your "
                '<a href="/youtube/v3/getting-started#quota">quota</a>.'
            ),
            "errors": [
                {
                    "domain": "youtube.quota",
                    "reason": "quotaExceeded",
                    "message": "quota exceeded",
                }
            ],
        }
    }
    api, session, sleeps, _ = _make_api(tmp_path, [_FakeResponse(403, quota_payload)])
    with pytest.raises(QuotaExceededError) as caught:
        api.insert_playlist_item("PL1", "vid1")
    message = str(caught.value)
    assert "quota exceeded" in message.lower()
    assert "10,000" in message
    assert "<a href" not in message
    assert sleeps == []
    assert len(session.calls) == 1


def test_data_api_exposes_no_search_method(tmp_path):
    api, _, _, _ = _make_api(tmp_path, [])
    assert not hasattr(api, "search")
