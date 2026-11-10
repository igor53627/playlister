"""Shape ytmusicapi payloads into the fields an assistant should see.

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
        "duration": item.get("duration"),
        "duration_seconds": item.get("duration_seconds"),
        "result_type": item.get("resultType"),
    }


def public_track(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict) or not item.get("videoId"):
        return None
    return public_hit(item)
