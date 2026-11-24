"""Hybrid backend: unauthenticated ytmusicapi for catalog, Data API v3 for OAuth.

Browser-header auth continues to use ytmusicapi for account operations.
``commit`` defaults to false on mutating calls so they preview before writing.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from playlister.auth import AuthError, build_public_ytmusic, build_ytmusic, resolve_auth
from playlister.data_api import DataAPIError, YouTubeDataAPI
from playlister.matcher import match_tracklist
from playlister.oauth import OAuthTokenManager
from playlister.playlist_ops import (
    commit_add_browser,
    commit_add_data_api,
    commit_remove_browser,
    commit_remove_data_api,
    format_error,
)
from playlister.present import present_playlist, public_hit

logger = logging.getLogger("playlister")

PRIVACY = {"PRIVATE", "PUBLIC", "UNLISTED"}
RATINGS = {"like", "dislike", "none"}
_BROWSER_RATING_MAP = {"like": "LIKE", "dislike": "DISLIKE", "none": "INDIFFERENT"}
_BROWSE_BACKOFF_SECONDS = (0.5, 1.0, 2.0, 4.0)
_BROWSE_NOT_READY = "_browse_not_ready"

_FRESH_TTL_SECONDS = 60.0
_fresh_until: dict[str, float] = {}
_factory: Callable[[], YouTubeMusic] | None = None


def remember_fresh_playlist(playlist_id: str, *, now: float | None = None) -> None:
    """Remember a playlist id created here, for about a minute."""
    stamp = time.monotonic() if now is None else now
    _fresh_until[playlist_id] = stamp + _FRESH_TTL_SECONDS


def is_fresh_playlist(playlist_id: str, *, now: float | None = None) -> bool:
    """True while that id is still inside the just-created window."""
    deadline = _fresh_until.get(playlist_id)
    if deadline is None:
        return False
    stamp = time.monotonic() if now is None else now
    if stamp > deadline:
        _fresh_until.pop(playlist_id, None)
        return False
    return True


def clear_fresh_playlists() -> None:
    """Drop the just-created cache. Tests call this between cases."""
    _fresh_until.clear()


def forget_fresh_playlist(playlist_id: str) -> None:
    """Drop the empty-browse fallback once a write to this playlist was attempted."""
    _fresh_until.pop(playlist_id, None)


def _browse_not_ready(exc: BaseException) -> bool:
    if not isinstance(exc, KeyError | IndexError):
        return False
    text = str(exc)
    return "Unable to find" in text and (
        "contents" in text or "twoColumnBrowseResultsRenderer" in text
    )


def _structure_missing(raw: object) -> bool:
    return isinstance(raw, dict) and "tracks" not in raw


def _empty_playlist(playlist_id: str) -> dict[str, Any]:
    return {
        "ok": True,
        "playlist_id": playlist_id,
        "title": None,
        "description": "",
        "privacy": None,
        "track_count": 0,
        "author": None,
        "tracks": [],
    }


def _clean_ids(video_ids: list[str]) -> list[str]:
    return [vid.strip() for vid in video_ids if isinstance(vid, str) and vid.strip()]


class YouTubeMusic:
    def __init__(
        self,
        client: Any,
        *,
        authenticated: bool,
        data_api: Any | None = None,
        public_client: Any | None = None,
    ) -> None:
        self._client = client
        self._public_client = public_client if public_client is not None else client
        self.authenticated = authenticated
        self._data_api = data_api
        self._sleep = time.sleep

    @classmethod
    def from_env(cls) -> YouTubeMusic:
        config = resolve_auth()
        client = build_ytmusic(config)
        public_client = build_public_ytmusic() if config.kind == "browser" else client
        data_api: YouTubeDataAPI | None = None
        if config.kind == "oauth" and config.path is not None:
            tokens = OAuthTokenManager(
                config.path,
                client_id=config.oauth_client_id or "",
                client_secret=config.oauth_client_secret or "",
            )
            data_api = YouTubeDataAPI(tokens)
        return cls(
            client,
            authenticated=config.kind != "anonymous",
            data_api=data_api,
            public_client=public_client,
        )

    def _require_auth(self) -> dict[str, Any] | None:
        if self.authenticated:
            return None
        return {
            "ok": False,
            "error": (
                "This action needs a local auth file. Run `playlister auth oauth` "
                "(recommended) or set PLAYLISTER_AUTH_PATH to a browser/OAuth file. "
                "Search, matching, and radio work without auth; account operations do not."
            ),
        }

    # --- Catalog (always unauthenticated ytmusicapi via _public_client) ---

    def search(self, query: str, filter: str = "songs", limit: int = 10) -> list[dict[str, Any]]:
        if filter not in {"songs", "videos"}:
            raise ValueError("filter must be 'songs' or 'videos'")
        limit = max(1, min(int(limit), 20))
        try:
            raw = self._public_client.search(query, filter=filter, limit=limit)
        except Exception as exc:
            logger.warning("search failed: %s", type(exc).__name__)
            raise
        return list(raw or [])

    def search_public(self, query: str, filter: str = "songs", limit: int = 10) -> dict[str, Any]:
        try:
            hits = self.search(query, filter=filter, limit=limit)
        except Exception as exc:
            if type(exc) is ValueError:
                return {"ok": False, "error": str(exc)}
            return {"ok": False, "error": format_error(exc)}
        return {"ok": True, "results": [public_hit(hit) for hit in hits]}

    def match(
        self, text: str, order: str = "artist-title", search_limit: int = 8
    ) -> dict[str, Any]:
        try:
            return match_tracklist(
                text,
                self.search,
                order=order,
                search_limit=max(1, min(int(search_limit), 15)),
            )
        except Exception as exc:
            if type(exc) is ValueError:
                return {"ok": False, "error": str(exc)}
            logger.warning("match failed: %s", type(exc).__name__)
            return {"ok": False, "error": format_error(exc)}

    def get_similar_tracks(self, video_id: str, limit: int = 20) -> dict[str, Any]:
        seed = video_id.strip()
        if not seed:
            return {"ok": False, "error": "video_id is required"}
        clamped = max(1, min(int(limit), 50))
        try:
            raw = self._public_client.get_watch_playlist(
                videoId=seed, radio=True, limit=clamped + 1
            )
        except Exception as exc:
            logger.warning("get_similar_tracks failed: %s", type(exc).__name__)
            return {"ok": False, "error": format_error(exc)}
        items = raw.get("tracks") if isinstance(raw, dict) else []
        tracks: list[dict[str, Any]] = []
        seen: set[str] = {seed}
        for item in items or []:
            if not isinstance(item, dict):
                continue
            vid = item.get("videoId")
            if not vid or vid in seen:
                continue
            seen.add(vid)
            tracks.append(public_hit(item))
            if len(tracks) >= clamped:
                break
        return {"ok": True, "seed_video_id": seed, "tracks": tracks}

    # --- Account operations (YouTube Data API v3 in OAuth mode, ytmusicapi in browser mode) ---

    def list_playlists(self, limit: int = 25) -> dict[str, Any]:
        denied = self._require_auth()
        if denied:
            return denied
        if self._data_api is not None:
            try:
                return {"ok": True, "playlists": self._data_api.list_playlists(limit=limit)}
            except Exception as exc:
                return {"ok": False, "error": format_error(exc)}
        try:
            raw = self._client.get_library_playlists(limit=limit if limit > 0 else None)
        except Exception as exc:
            return {"ok": False, "error": format_error(exc)}
        playlists = [
            {
                "playlist_id": item.get("playlistId"),
                "title": item.get("title"),
                "count": item.get("count"),
                "owned": bool(item.get("owned", False)),
            }
            for item in (raw or [])
            if isinstance(item, dict) and item.get("playlistId")
        ]
        return {"ok": True, "playlists": playlists}

    def get_playlist(self, playlist_id: str, limit: int = 100) -> dict[str, Any]:
        loaded = self._load_playlist(playlist_id, limit)
        loaded.pop(_BROWSE_NOT_READY, None)
        return loaded

    def _load_playlist(self, playlist_id: str, limit: int) -> dict[str, Any]:
        if self._data_api is not None:
            return self._load_playlist_data_api(playlist_id, limit)
        delays = _BROWSE_BACKOFF_SECONDS
        last_error = "Unexpected playlist response"
        for attempt in range(len(delays) + 1):
            try:
                raw = self._client.get_playlist(
                    playlist_id,
                    limit=None if limit <= 0 else limit,
                    related=False,
                    suggestions_limit=0,
                )
            except Exception as exc:
                if not _browse_not_ready(exc):
                    return {"ok": False, "error": format_error(exc)}
                last_error = format_error(exc)
            else:
                if not _structure_missing(raw):
                    return present_playlist(raw, playlist_id)
                last_error = "Unexpected playlist response"
            if attempt == len(delays):
                break
            logger.info("playlist browse not ready; retrying")
            self._sleep(delays[attempt])
        return {"ok": False, "error": last_error, _BROWSE_NOT_READY: True}

    def _load_playlist_data_api(self, playlist_id: str, limit: int) -> dict[str, Any]:
        delays = _BROWSE_BACKOFF_SECONDS if is_fresh_playlist(playlist_id) else ()
        last_error = "Playlist not found"
        for attempt in range(len(delays) + 1):
            try:
                return self._data_api.get_playlist(playlist_id, limit=limit)
            except DataAPIError as exc:
                if exc.status_code != 404 or not delays:
                    return {"ok": False, "error": format_error(exc)}
                last_error = str(exc)
            except Exception as exc:
                return {"ok": False, "error": format_error(exc)}
            if attempt == len(delays):
                break
            logger.info("new playlist not yet visible in Data API; retrying")
            self._sleep(delays[attempt])
        return {"ok": False, "error": last_error, _BROWSE_NOT_READY: True}

    def create_playlist(
        self,
        title: str,
        description: str = "",
        privacy: str = "PRIVATE",
        *,
        commit: bool = False,
    ) -> dict[str, Any]:
        privacy = (privacy or "PRIVATE").upper()
        if privacy not in PRIVACY:
            return {"ok": False, "error": "privacy must be PRIVATE, UNLISTED, or PUBLIC"}
        title = title.strip()
        if not title:
            return {"ok": False, "error": "title is required"}
        if "<" in title or ">" in title:
            return {"ok": False, "error": "title cannot contain < or >"}
        plan = {"title": title, "description": description, "privacy": privacy}
        if not commit:
            return {
                "ok": True,
                "commit": False,
                "would_create": plan,
                "note": "No playlist was created. Call again with commit=true to write it.",
            }
        denied = self._require_auth()
        if denied:
            return denied
        try:
            if self._data_api is not None:
                created = self._data_api.create_playlist(title, description, privacy)
            else:
                created = self._client.create_playlist(title, description, privacy_status=privacy)
        except Exception as exc:
            logger.info("create_playlist failed")
            return {"ok": False, "error": format_error(exc)}
        if not isinstance(created, str) or not created or created.startswith("{"):
            return {"ok": False, "error": "YouTube Music did not return a playlist id"}
        logger.info("created playlist privacy=%s", privacy)
        remember_fresh_playlist(created)
        return {
            "ok": True,
            "commit": True,
            "playlist_id": created,
            "title": title,
            "privacy": privacy,
            "url": f"https://music.youtube.com/playlist?list={created}",
        }

    def add_tracks(
        self, playlist_id: str, video_ids: list[str], *, commit: bool = False
    ) -> dict[str, Any]:
        playlist_id = playlist_id.strip()
        if not playlist_id:
            return {"ok": False, "error": "playlist_id is required"}
        cleaned = _clean_ids(video_ids)
        if not cleaned:
            return {"ok": False, "error": "video_ids is empty"}

        current = self._load_playlist(playlist_id, limit=0)
        if not current.get("ok"):
            if current.get(_BROWSE_NOT_READY) and is_fresh_playlist(playlist_id):
                current = _empty_playlist(playlist_id)
            else:
                current.pop(_BROWSE_NOT_READY, None)
                return current
        existing = {track["video_id"] for track in current["tracks"]}

        would_add: list[str] = []
        skipped: list[str] = []
        seen: set[str] = set()
        for video_id in cleaned:
            if video_id in seen or video_id in existing:
                skipped.append(video_id)
            else:
                would_add.append(video_id)
            seen.add(video_id)

        if not commit:
            return {
                "ok": True,
                "commit": False,
                "playlist_id": playlist_id,
                "would_add": would_add,
                "skipped_duplicates": skipped,
                "note": "Nothing was added. Call again with commit=true to write.",
            }
        denied = self._require_auth()
        if denied:
            return denied
        if not would_add:
            return {
                "ok": True,
                "commit": True,
                "playlist_id": playlist_id,
                "added": [],
                "skipped_duplicates": skipped,
            }
        forget_fresh_playlist(playlist_id)
        reread = lambda: self.get_playlist(playlist_id, limit=0)  # noqa: E731
        if self._data_api is not None:
            return commit_add_data_api(
                self._data_api,
                reread,
                playlist_id=playlist_id,
                would_add=would_add,
                skipped=skipped,
            )
        return commit_add_browser(
            self._client, reread, playlist_id=playlist_id, would_add=would_add, skipped=skipped
        )

    def remove_tracks(
        self, playlist_id: str, video_ids: list[str], *, commit: bool = False
    ) -> dict[str, Any]:
        playlist_id = playlist_id.strip()
        if not playlist_id:
            return {"ok": False, "error": "playlist_id is required"}
        cleaned = list(dict.fromkeys(_clean_ids(video_ids)))
        if not cleaned:
            return {"ok": False, "error": "video_ids is empty"}

        current = self.get_playlist(playlist_id, limit=0)
        if not current.get("ok"):
            return current

        requested = set(cleaned)
        to_remove: list[dict[str, str]] = []
        found_ids: set[str] = set()
        for track in current["tracks"]:
            vid = track.get("video_id")
            if vid not in requested:
                continue
            found_ids.add(vid)
            item_id = track.get("playlist_item_id")
            if not item_id:
                return {
                    "ok": False,
                    "error": (
                        "Cannot remove tracks: playlist item id is missing "
                        "(do you own this playlist?)"
                    ),
                }
            to_remove.append({"video_id": vid, "playlist_item_id": str(item_id)})

        not_found = [vid for vid in cleaned if vid not in found_ids]
        if not commit:
            return {
                "ok": True,
                "commit": False,
                "playlist_id": playlist_id,
                "would_remove": [item["video_id"] for item in to_remove],
                "would_remove_items": to_remove,
                "not_found": not_found,
                "note": "Nothing was removed. Call again with commit=true to write.",
            }
        denied = self._require_auth()
        if denied:
            return denied
        if not to_remove:
            return {
                "ok": True,
                "commit": True,
                "playlist_id": playlist_id,
                "removed": [],
                "not_found": not_found,
            }
        reread = lambda: self.get_playlist(playlist_id, limit=0)  # noqa: E731
        if self._data_api is not None:
            return commit_remove_data_api(
                self._data_api,
                reread,
                playlist_id=playlist_id,
                to_remove=to_remove,
                not_found=not_found,
            )
        return commit_remove_browser(
            self._client,
            reread,
            playlist_id=playlist_id,
            to_remove=to_remove,
            not_found=not_found,
        )

    def get_track_ratings(self, video_ids: list[str]) -> dict[str, Any]:
        cleaned = list(dict.fromkeys(_clean_ids(video_ids)))
        if not cleaned:
            return {"ok": False, "error": "video_ids is empty"}
        denied = self._require_auth()
        if denied:
            return denied
        if self._data_api is None:
            return {
                "ok": False,
                "error": (
                    "get_track_ratings requires OAuth authentication (YouTube Data API v3). "
                    "Run `playlister auth oauth` to set up OAuth."
                ),
            }
        try:
            ratings = self._data_api.get_video_ratings(cleaned)
        except Exception as exc:
            return {"ok": False, "error": format_error(exc)}
        return {"ok": True, "ratings": ratings}

    def rate_track(self, video_id: str, rating: str, *, commit: bool = False) -> dict[str, Any]:
        vid = video_id.strip()
        if not vid:
            return {"ok": False, "error": "video_id is required"}
        normalized = (rating or "").strip().lower()
        if normalized not in RATINGS:
            return {"ok": False, "error": "rating must be 'like', 'dislike', or 'none'"}
        if not commit:
            return {
                "ok": True,
                "commit": False,
                "would_rate": {"video_id": vid, "rating": normalized},
                "note": "No rating was changed. Call again with commit=true to write.",
            }
        denied = self._require_auth()
        if denied:
            return denied
        try:
            if self._data_api is not None:
                self._data_api.rate_video(vid, normalized)
            else:
                self._client.rate_song(vid, rating=_BROWSER_RATING_MAP[normalized])
        except Exception as exc:
            logger.info("rate_track failed")
            return {"ok": False, "error": format_error(exc)}
        return {"ok": True, "commit": True, "video_id": vid, "rating": normalized}


def set_client_factory(factory: Callable[[], YouTubeMusic] | None) -> None:
    global _factory
    _factory = factory


def get_youtube() -> YouTubeMusic:
    """Return the injected client, or one built from the local auth file."""
    if _factory is not None:
        return _factory()
    try:
        return YouTubeMusic.from_env()
    except AuthError as exc:
        raise AuthError(str(exc)) from None
