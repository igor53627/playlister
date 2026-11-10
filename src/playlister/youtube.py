"""Playlist reads and writes on top of a ytmusicapi client.

``add_playlist_items(..., duplicates=False)`` rejects the whole batch when any
id is already present, so duplicates are removed locally first. ``commit``
defaults to false: the same function previews the write.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from playlister.auth import AuthError, build_ytmusic, resolve_auth
from playlister.matcher import match_tracklist
from playlister.present import public_hit, public_track, write_succeeded
from playlister.redact import public_error

logger = logging.getLogger("playlister")

PRIVACY = {"PRIVATE", "PUBLIC", "UNLISTED"}
_ADD_CHUNK = 50

_factory: Callable[[], YouTubeMusic] | None = None


class YouTubeMusic:
    def __init__(self, client: Any, *, authenticated: bool) -> None:
        self._client = client
        self.authenticated = authenticated

    @classmethod
    def from_env(cls) -> YouTubeMusic:
        config = resolve_auth()
        client = build_ytmusic(config)
        return cls(client, authenticated=config.kind != "anonymous")

    def _require_auth(self) -> dict[str, Any] | None:
        if self.authenticated:
            return None
        return {
            "ok": False,
            "error": (
                "This action needs a local auth file. Set PLAYLISTER_AUTH_PATH "
                "to a ytmusicapi browser or OAuth file. Search and matching work "
                "without it; creating and editing playlists does not."
            ),
        }

    def search(self, query: str, filter: str = "songs", limit: int = 10) -> list[dict[str, Any]]:
        if filter not in {"songs", "videos"}:
            raise ValueError("filter must be 'songs' or 'videos'")
        limit = max(1, min(int(limit), 20))
        try:
            raw = self._client.search(query, filter=filter, limit=limit)
        except Exception as exc:
            logger.warning("search failed: %s", type(exc).__name__)
            raise
        return list(raw or [])

    def search_public(self, query: str, filter: str = "songs", limit: int = 10) -> dict[str, Any]:
        try:
            hits = self.search(query, filter=filter, limit=limit)
        except Exception as exc:
            return {"ok": False, "error": public_error(exc)}
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
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            logger.warning("match failed: %s", type(exc).__name__)
            return {"ok": False, "error": public_error(exc)}

    def list_playlists(self, limit: int = 25) -> dict[str, Any]:
        denied = self._require_auth()
        if denied:
            return denied
        try:
            raw = self._client.get_library_playlists(limit=limit if limit > 0 else None)
        except Exception as exc:
            return {"ok": False, "error": public_error(exc)}
        playlists = []
        for item in raw or []:
            if not isinstance(item, dict) or not item.get("playlistId"):
                continue
            playlists.append(
                {
                    "playlist_id": item.get("playlistId"),
                    "title": item.get("title"),
                    "count": item.get("count"),
                    "owned": bool(item.get("owned", False)),
                }
            )
        return {"ok": True, "playlists": playlists}

    def get_playlist(self, playlist_id: str, limit: int = 100) -> dict[str, Any]:
        try:
            raw = self._client.get_playlist(
                playlist_id,
                limit=None if limit <= 0 else limit,
                related=False,
                suggestions_limit=0,
            )
        except Exception as exc:
            return {"ok": False, "error": public_error(exc)}
        if not isinstance(raw, dict):
            return {"ok": False, "error": "Unexpected playlist response"}
        tracks = []
        for item in raw.get("tracks") or []:
            public = public_track(item)
            if public:
                tracks.append(public)
        author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
        return {
            "ok": True,
            "playlist_id": raw.get("id") or playlist_id,
            "title": raw.get("title"),
            "description": raw.get("description") or "",
            "privacy": raw.get("privacy"),
            "track_count": raw.get("trackCount"),
            "author": author.get("name") if isinstance(author, dict) else None,
            "tracks": tracks,
        }

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
            created = self._client.create_playlist(title, description, privacy_status=privacy)
        except Exception as exc:
            logger.info("create_playlist failed")
            return {"ok": False, "error": public_error(exc)}
        if not isinstance(created, str) or not created or created.startswith("{"):
            return {"ok": False, "error": "YouTube Music did not return a playlist id"}
        logger.info("created playlist privacy=%s", privacy)
        return {
            "ok": True,
            "commit": True,
            "playlist_id": created,
            "title": title,
            "privacy": privacy,
            "url": f"https://music.youtube.com/playlist?list={created}",
        }

    def add_tracks(
        self,
        playlist_id: str,
        video_ids: list[str],
        *,
        dedupe: bool = True,
        commit: bool = False,
    ) -> dict[str, Any]:
        playlist_id = playlist_id.strip()
        if not playlist_id:
            return {"ok": False, "error": "playlist_id is required"}
        cleaned: list[str] = []
        for video_id in video_ids:
            if isinstance(video_id, str) and video_id.strip():
                cleaned.append(video_id.strip())
        if not cleaned:
            return {"ok": False, "error": "video_ids is empty"}

        existing: set[str] = set()
        if dedupe:
            current = self.get_playlist(playlist_id, limit=0)
            if not current.get("ok"):
                return current
            existing = {track["video_id"] for track in current["tracks"]}

        would_add: list[str] = []
        skipped: list[str] = []
        seen: set[str] = set()
        for video_id in cleaned:
            if video_id in seen or (dedupe and video_id in existing):
                skipped.append(video_id)
                seen.add(video_id)
                continue
            would_add.append(video_id)
            seen.add(video_id)

        preview = {
            "ok": True,
            "commit": False,
            "playlist_id": playlist_id,
            "would_add": would_add,
            "skipped_duplicates": skipped,
            "note": "Nothing was added. Call again with commit=true to write.",
        }
        if not commit:
            return preview
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
        try:
            for start in range(0, len(would_add), _ADD_CHUNK):
                chunk = would_add[start : start + _ADD_CHUNK]
                result = self._client.add_playlist_items(playlist_id, chunk, duplicates=False)
                if not write_succeeded(result):
                    return {"ok": False, "error": "YouTube Music refused the add"}
        except Exception as exc:
            logger.info("add_playlist_tracks failed")
            return {"ok": False, "error": public_error(exc)}
        logger.info("added %s tracks (skipped %s duplicates)", len(would_add), len(skipped))
        return {
            "ok": True,
            "commit": True,
            "playlist_id": playlist_id,
            "added": would_add,
            "skipped_duplicates": skipped,
            "url": f"https://music.youtube.com/playlist?list={playlist_id}",
        }


def set_client_factory(factory: Callable[[], YouTubeMusic] | None) -> None:
    global _factory
    _factory = factory


def get_youtube() -> YouTubeMusic:
    if _factory is not None:
        return _factory()
    try:
        return YouTubeMusic.from_env()
    except AuthError as exc:
        raise AuthError(str(exc)) from None
