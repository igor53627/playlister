"""Official YouTube Data API v3 client for OAuth account operations.

Used only when auth kind is ``oauth``. Catalog search stays on unauthenticated
``ytmusicapi`` to avoid YouTube Data API search's 100-unit quota cost.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import Any

import requests

from playlister.auth import AuthError
from playlister.oauth import OAuthTokenManager
from playlister.redact import redact

logger = logging.getLogger("playlister")

DATA_API_BASE = "https://www.googleapis.com/youtube/v3"
_MAX_PAGE_SIZE = 50
_RATING_CHUNK = 50
_DEFAULT_TIMEOUT = 30.0
_TRANSIENT_STATUSES = {409, 500, 502, 503, 504}
_WRITE_BACKOFF_SECONDS = (0.5, 1.0, 2.0)
_QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}
_HTML_TAG = re.compile(r"<[^>]+>")


class DataAPIError(Exception):
    """Safe-to-display error from the YouTube Data API v3."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason


class QuotaExceededError(DataAPIError):
    """YouTube Data API v3 daily quota was exceeded."""


def _clean_topic_channel(name: str) -> str:
    if name.endswith(" - Topic"):
        return name[: -len(" - Topic")].strip()
    return name


def _parse_api_error(response: requests.Response) -> DataAPIError:
    status = response.status_code
    reason: str | None = None
    raw_message = ""
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            raw_message = str(err.get("message") or "")
            errors = err.get("errors")
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                raw_reason = errors[0].get("reason")
                if raw_reason:
                    reason = str(raw_reason)

    if reason in _QUOTA_REASONS or (status == 403 and "quota" in raw_message.casefold()):
        return QuotaExceededError(
            "YouTube Data API daily quota exceeded (10,000 units/day; "
            "insert/delete/rate cost 50 units, list/getRating cost 1). "
            "Quota resets at midnight Pacific Time.",
            status_code=status,
            reason=reason or "quotaExceeded",
        )

    cleaned = redact(_HTML_TAG.sub("", raw_message)).strip().replace("\n", " ")
    if len(cleaned) > 200:
        cleaned = cleaned[:200] + "..."
    label = f"HTTP {status}" + (f" ({reason})" if reason else "")
    prefix = f"YouTube Data API error {label}"
    message = f"{prefix}: {cleaned}" if cleaned else prefix
    return DataAPIError(message, status_code=status, reason=reason)


class YouTubeDataAPI:
    """Thin client for YouTube Data API v3 playlist and rating endpoints."""

    def __init__(
        self,
        token_manager: OAuthTokenManager,
        *,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._tokens = token_manager
        self._session = session or requests.Session()
        self._sleep = sleep
        self._timeout = timeout

    def _send(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        token: str,
    ) -> requests.Response:
        url = f"{DATA_API_BASE}/{endpoint.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        return self._session.request(
            method,
            url,
            params=params,
            json=json_body,
            headers=headers,
            timeout=self._timeout,
        )

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = self._tokens.get_access_token()
        response = self._send(method, endpoint, params=params, json_body=json_body, token=token)
        if response.status_code == 401:
            logger.info("YouTube Data API returned 401; refreshing OAuth token")
            token = self._tokens.get_access_token(force_refresh=True)
            response = self._send(method, endpoint, params=params, json_body=json_body, token=token)
            if response.status_code == 401:
                raise AuthError(
                    "YouTube Data API rejected the OAuth token after refresh. "
                    "Run `playlister auth oauth` to re-authenticate."
                )

        if 200 <= response.status_code < 300:
            if response.status_code == 204 or not response.content:
                return {}
            try:
                parsed = response.json()
            except ValueError:
                raise DataAPIError(
                    "YouTube Data API returned invalid JSON", status_code=response.status_code
                ) from None
            return parsed if isinstance(parsed, dict) else {}

        raise _parse_api_error(response)

    def list_playlists(self, limit: int = 25) -> list[dict[str, Any]]:
        """List authenticated user's playlists (``playlists.list mine=true``)."""
        playlists: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            if limit > 0:
                remaining = limit - len(playlists)
                if remaining <= 0:
                    break
                page_size = min(_MAX_PAGE_SIZE, remaining)
            else:
                page_size = _MAX_PAGE_SIZE

            params: dict[str, Any] = {
                "part": "snippet,contentDetails,status",
                "mine": "true",
                "maxResults": page_size,
            }
            if page_token:
                params["pageToken"] = page_token
            data = self._request("GET", "playlists", params=params)
            for item in data.get("items") or []:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
                details = (
                    item.get("contentDetails")
                    if isinstance(item.get("contentDetails"), dict)
                    else {}
                )
                status = item.get("status") if isinstance(item.get("status"), dict) else {}
                privacy = status.get("privacyStatus")
                privacy_upper = privacy.upper() if isinstance(privacy, str) and privacy else None
                playlists.append(
                    {
                        "playlist_id": item["id"],
                        "title": snippet.get("title"),
                        "count": details.get("itemCount"),
                        "owned": True,
                        "privacy": privacy_upper,
                    }
                )
                if limit > 0 and len(playlists) >= limit:
                    break
            page_token = data.get("nextPageToken")
            if not page_token or (limit > 0 and len(playlists) >= limit):
                break
        return playlists

    def get_playlist(self, playlist_id: str, limit: int = 100) -> dict[str, Any]:
        """Fetch playlist metadata and paginated items including ``playlist_item_id``."""
        meta = self._request(
            "GET",
            "playlists",
            params={"part": "snippet,contentDetails,status", "id": playlist_id},
        )
        items = meta.get("items") or []
        if not items or not isinstance(items[0], dict):
            raise DataAPIError(
                f"Playlist not found: {playlist_id}",
                status_code=404,
                reason="playlistNotFound",
            )
        raw_playlist = items[0]
        snippet = (
            raw_playlist.get("snippet") if isinstance(raw_playlist.get("snippet"), dict) else {}
        )
        details = (
            raw_playlist.get("contentDetails")
            if isinstance(raw_playlist.get("contentDetails"), dict)
            else {}
        )
        status = raw_playlist.get("status") if isinstance(raw_playlist.get("status"), dict) else {}
        privacy = status.get("privacyStatus")

        tracks: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            if limit > 0:
                remaining = limit - len(tracks)
                if remaining <= 0:
                    break
                page_size = min(_MAX_PAGE_SIZE, remaining)
            else:
                page_size = _MAX_PAGE_SIZE

            params: dict[str, Any] = {
                "part": "snippet,contentDetails",
                "playlistId": playlist_id,
                "maxResults": page_size,
            }
            if page_token:
                params["pageToken"] = page_token
            page = self._request("GET", "playlistItems", params=params)
            for item in page.get("items") or []:
                if not isinstance(item, dict):
                    continue
                item_snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
                item_details = (
                    item.get("contentDetails")
                    if isinstance(item.get("contentDetails"), dict)
                    else {}
                )
                resource_id = (
                    item_snippet.get("resourceId")
                    if isinstance(item_snippet.get("resourceId"), dict)
                    else {}
                )
                video_id = item_details.get("videoId") or resource_id.get("videoId")
                if not video_id:
                    continue
                channel = item_snippet.get("videoOwnerChannelTitle")
                artists = (
                    [_clean_topic_channel(channel)] if isinstance(channel, str) and channel else []
                )
                tracks.append(
                    {
                        "video_id": video_id,
                        "playlist_item_id": item.get("id"),
                        "title": item_snippet.get("title"),
                        "artists": artists,
                        "channel": channel if isinstance(channel, str) else None,
                        "album": None,
                        "duration": None,
                        "duration_seconds": None,
                        "result_type": None,
                    }
                )
                if limit > 0 and len(tracks) >= limit:
                    break
            page_token = page.get("nextPageToken")
            if not page_token or (limit > 0 and len(tracks) >= limit):
                break

        return {
            "ok": True,
            "playlist_id": raw_playlist.get("id") or playlist_id,
            "title": snippet.get("title"),
            "description": snippet.get("description") or "",
            "privacy": privacy.upper() if isinstance(privacy, str) and privacy else None,
            "track_count": details.get("itemCount"),
            "author": snippet.get("channelTitle"),
            "tracks": tracks,
        }

    def create_playlist(self, title: str, description: str = "", privacy: str = "PRIVATE") -> str:
        """Create a playlist via ``playlists.insert`` and return its id."""
        data = self._request(
            "POST",
            "playlists",
            params={"part": "snippet,status"},
            json_body={
                "snippet": {"title": title, "description": description},
                "status": {"privacyStatus": privacy.lower()},
            },
        )
        playlist_id = data.get("id")
        if not isinstance(playlist_id, str) or not playlist_id:
            raise DataAPIError("YouTube Data API did not return a playlist id")
        return playlist_id

    def insert_playlist_item(self, playlist_id: str, video_id: str) -> dict[str, Any]:
        """Insert one video into a playlist, retrying transient 409/5xx responses."""
        body = {
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        }
        delays = _WRITE_BACKOFF_SECONDS
        for attempt in range(len(delays) + 1):
            try:
                return self._request(
                    "POST",
                    "playlistItems",
                    params={"part": "snippet"},
                    json_body=body,
                )
            except QuotaExceededError:
                raise
            except DataAPIError as exc:
                if exc.status_code not in _TRANSIENT_STATUSES or attempt == len(delays):
                    raise
                logger.info("playlistItems.insert got HTTP %s; retrying", exc.status_code)
                self._sleep(delays[attempt])
        raise DataAPIError("playlistItems.insert failed")  # pragma: no cover

    def delete_playlist_item(self, playlist_item_id: str) -> None:
        """Delete one playlist item by ``playlistItemId``."""
        delays = _WRITE_BACKOFF_SECONDS
        for attempt in range(len(delays) + 1):
            try:
                self._request("DELETE", "playlistItems", params={"id": playlist_item_id})
                return
            except QuotaExceededError:
                raise
            except DataAPIError as exc:
                if exc.status_code not in _TRANSIENT_STATUSES or attempt == len(delays):
                    raise
                logger.info("playlistItems.delete got HTTP %s; retrying", exc.status_code)
                self._sleep(delays[attempt])

    def get_video_ratings(self, video_ids: list[str]) -> list[dict[str, str]]:
        """Call ``videos.getRating`` in chunks of up to 50 video ids."""
        ratings: list[dict[str, str]] = []
        for start in range(0, len(video_ids), _RATING_CHUNK):
            chunk = video_ids[start : start + _RATING_CHUNK]
            data = self._request("GET", "videos/getRating", params={"id": ",".join(chunk)})
            for item in data.get("items") or []:
                if not isinstance(item, dict) or not item.get("videoId"):
                    continue
                ratings.append(
                    {
                        "video_id": str(item["videoId"]),
                        "rating": str(item.get("rating") or "none"),
                    }
                )
        return ratings

    def rate_video(self, video_id: str, rating: str) -> None:
        """Call ``videos.rate`` with ``like``, ``dislike``, or ``none``."""
        self._request("POST", "videos/rate", params={"id": video_id, "rating": rating})
