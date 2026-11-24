"""Shape YouTube and ytmusicapi payloads into the fields an assistant should see.

Feedback tokens, cookies, and thumbnails stay out of tool results.
"""

from __future__ import annotations

from typing import Any


def write_succeeded(result: object) -> bool:
    """A successful write mentions SUCCEEDED. Errors come back as a raw body."""
    if isinstance(result, str):
        return "SUCCEEDED" in result
    if isinstance(result, dict):
        status = result.get("status")
        return isinstance(status, str) and "SUCCEEDED" in status
    return False


def public_hit(item: dict[str, Any]) -> dict[str, Any]:
    artists: list[str] = []
    for artist in item.get("artists") or []:
        if isinstance(artist, dict) and artist.get("name"):
            artists.append(str(artist["name"]))
        elif isinstance(artist, str):
            artists.append(artist)
    album = item.get("album")
    album_name = album.get("name") if isinstance(album, dict) else None
    return {
        "video_id": item.get("videoId"),
        "title": item.get("title"),
        "artists": artists,
        "album": album_name,
        "duration": item.get("duration") or item.get("length"),
        "duration_seconds": item.get("duration_seconds"),
        "result_type": item.get("resultType"),
    }


def partial_add(
    playlist_id: str,
    added: list[str],
    would_add: list[str],
    skipped: list[str],
    error: str = "YouTube Music refused the add",
) -> dict[str, Any]:
    return {
        "ok": False,
        "error": error,
        "playlist_id": playlist_id,
        "added": added,
        "not_added": would_add[len(added) :],
        "skipped_duplicates": skipped,
    }


def partial_remove(
    playlist_id: str,
    removed: list[str],
    not_removed: list[str],
    not_found: list[str],
    error: str = "YouTube Music refused the remove",
) -> dict[str, Any]:
    return {
        "ok": False,
        "error": error,
        "playlist_id": playlist_id,
        "removed": removed,
        "not_removed": not_removed,
        "not_found": not_found,
    }


def lost_add_response(
    refreshed: dict[str, Any],
    *,
    playlist_id: str,
    added: list[str],
    chunk: list[str],
    would_add: list[str],
    skipped: list[str],
    error: str,
) -> dict[str, Any]:
    """A sent chunk whose reply never came back.

    When the playlist can be re-read, ids now on it count as added. Otherwise
    the in-flight chunk is ``uncertain`` and unsent ids stay in ``not_added``.
    """
    if refreshed.get("ok"):
        present = {track["video_id"] for track in refreshed["tracks"]}
        return {
            "ok": False,
            "error": error,
            "playlist_id": playlist_id,
            "added": [video_id for video_id in would_add if video_id in present],
            "not_added": [video_id for video_id in would_add if video_id not in present],
            "skipped_duplicates": skipped,
        }
    return {
        "ok": False,
        "error": error,
        "playlist_id": playlist_id,
        "added": added,
        "uncertain": chunk,
        "not_added": would_add[len(added) + len(chunk) :],
        "skipped_duplicates": skipped,
    }


def public_track(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict) or not item.get("videoId"):
        return None
    track = public_hit(item)
    if item.get("setVideoId"):
        track["playlist_item_id"] = item["setVideoId"]
    return track


def present_playlist(raw: object, playlist_id: str) -> dict[str, Any]:
    """Turn a ytmusicapi playlist payload into the public tool shape."""
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
