"""Commit helpers for adding and removing playlist tracks (OAuth Data API and browser)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from playlister.auth import AuthError
from playlister.data_api import DataAPIError
from playlister.present import (
    lost_add_response,
    lost_remove_response,
    partial_add,
    partial_remove,
    write_succeeded,
)
from playlister.redact import public_error

logger = logging.getLogger("playlister")

_ADD_CHUNK = 50


def format_error(exc: Exception) -> str:
    if isinstance(exc, AuthError | DataAPIError):
        return str(exc)
    return public_error(exc)


def _is_definitive_api_error(exc: Exception) -> bool:
    if isinstance(exc, AuthError):
        return True
    return isinstance(exc, DataAPIError) and not exc.ambiguous


def commit_add_data_api(
    data_api: Any,
    reread: Callable[[], dict[str, Any]],
    *,
    playlist_id: str,
    would_add: list[str],
    skipped: list[str],
) -> dict[str, Any]:
    """Insert items one by one via ``playlistItems.insert`` with partial/lost handling."""
    added: list[str] = []
    for video_id in would_add:
        try:
            data_api.insert_playlist_item(playlist_id, video_id)
        except Exception as exc:
            if _is_definitive_api_error(exc):
                logger.info("playlistItems.insert failed: %s", type(exc).__name__)
                return partial_add(playlist_id, added, would_add, skipped, error=str(exc))
            logger.info("playlistItems.insert uncertain failure")
            return lost_add_response(
                reread(),
                playlist_id=playlist_id,
                added=added,
                chunk=[video_id],
                would_add=would_add,
                skipped=skipped,
                error=format_error(exc),
            )
        added.append(video_id)
    logger.info("added %s tracks (skipped %s duplicates)", len(added), len(skipped))
    return {
        "ok": True,
        "commit": True,
        "playlist_id": playlist_id,
        "added": added,
        "skipped_duplicates": skipped,
        "url": f"https://music.youtube.com/playlist?list={playlist_id}",
    }


def commit_add_browser(
    client: Any,
    reread: Callable[[], dict[str, Any]],
    *,
    playlist_id: str,
    would_add: list[str],
    skipped: list[str],
) -> dict[str, Any]:
    """Insert items in chunks via browser-auth ytmusicapi."""
    added: list[str] = []
    for start in range(0, len(would_add), _ADD_CHUNK):
        chunk = would_add[start : start + _ADD_CHUNK]
        try:
            result = client.add_playlist_items(playlist_id, chunk, duplicates=False)
        except Exception as exc:
            logger.info("add_playlist_tracks failed")
            return lost_add_response(
                reread(),
                playlist_id=playlist_id,
                added=added,
                chunk=chunk,
                would_add=would_add,
                skipped=skipped,
                error=format_error(exc),
            )
        if not write_succeeded(result):
            return partial_add(playlist_id, added, would_add, skipped)
        added.extend(chunk)
    logger.info("added %s tracks (skipped %s duplicates)", len(would_add), len(skipped))
    return {
        "ok": True,
        "commit": True,
        "playlist_id": playlist_id,
        "added": would_add,
        "skipped_duplicates": skipped,
        "url": f"https://music.youtube.com/playlist?list={playlist_id}",
    }


def commit_remove_data_api(
    data_api: Any,
    reread: Callable[[], dict[str, Any]],
    *,
    playlist_id: str,
    to_remove: list[dict[str, str]],
    not_found: list[str],
) -> dict[str, Any]:
    """Delete playlist items one by one via ``playlistItems.delete``."""
    removed: list[str] = []
    for idx, item in enumerate(to_remove):
        try:
            data_api.delete_playlist_item(item["playlist_item_id"])
        except Exception as exc:
            if _is_definitive_api_error(exc):
                logger.info("playlistItems.delete failed: %s", type(exc).__name__)
                return partial_remove(
                    playlist_id,
                    removed,
                    [entry["video_id"] for entry in to_remove[idx:]],
                    not_found,
                    error=str(exc),
                )
            logger.info("playlistItems.delete uncertain failure")
            return lost_remove_response(
                reread(),
                playlist_id=playlist_id,
                removed=removed,
                in_flight=[item],
                to_remove=to_remove,
                not_found=not_found,
                error=format_error(exc),
            )
        removed.append(item["video_id"])
    return {
        "ok": True,
        "commit": True,
        "playlist_id": playlist_id,
        "removed": removed,
        "not_found": not_found,
        "url": f"https://music.youtube.com/playlist?list={playlist_id}",
    }


def commit_remove_browser(
    client: Any,
    reread: Callable[[], dict[str, Any]],
    *,
    playlist_id: str,
    to_remove: list[dict[str, str]],
    not_found: list[str],
) -> dict[str, Any]:
    """Delete playlist items via browser-auth ytmusicapi."""
    payload = [
        {"videoId": item["video_id"], "setVideoId": item["playlist_item_id"]} for item in to_remove
    ]
    try:
        result = client.remove_playlist_items(playlist_id, payload)
    except Exception as exc:
        logger.info("remove_playlist_tracks failed")
        return lost_remove_response(
            reread(),
            playlist_id=playlist_id,
            removed=[],
            in_flight=to_remove,
            to_remove=to_remove,
            not_found=not_found,
            error=format_error(exc),
        )
    if not write_succeeded(result):
        return partial_remove(
            playlist_id,
            [],
            [item["video_id"] for item in to_remove],
            not_found,
        )
    return {
        "ok": True,
        "commit": True,
        "playlist_id": playlist_id,
        "removed": [item["video_id"] for item in to_remove],
        "not_found": not_found,
        "url": f"https://music.youtube.com/playlist?list={playlist_id}",
    }
