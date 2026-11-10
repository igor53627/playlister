"""MCP tools. Writes preview unless ``commit`` is true.

Tool functions live at module level so tests can call them with a fake client.
The server object is built on demand and speaks stdio unless the CLI selects
the localhost HTTP transport.
"""

from __future__ import annotations

from typing import Any, Literal

from playlister import __version__
from playlister.auth import AuthError
from playlister.parser import parse_tracklist
from playlister.redact import public_error
from playlister.youtube import get_youtube

INSTRUCTIONS = """
playlister turns a tracklist the user pasted in chat into a YouTube Music
playlist on the user's own account. It runs locally. Never ask the user to
paste cookies, headers, or OAuth tokens into the chat.

Use this order:
1. parse_tracklist, when you want to show how a messy list was read.
2. match_tracklist. This is a dry run and writes nothing. It returns each row
   as matched, needs_review, or not_found, with a confidence score, the chosen
   YouTube Music track, and alternatives.
3. Show needs_review and not_found rows to the user, including alternatives,
   and wait for them to confirm or pick a different video id.
4. create_playlist. The default call previews only. Privacy defaults to
   PRIVATE. Pass commit=true only after the user agrees to create it.
5. add_playlist_tracks with the confirmed video ids. The default call previews
   which ids are new. Pass commit=true only after the user agrees. Do not add
   needs_review rows unless the user explicitly accepted that video id.

Only video ids from matched rows (plus ids the user picked) belong in the
write. Search and matching work without auth; library reads and writes need
PLAYLISTER_AUTH_PATH on the machine running the server.
""".strip()

Order = Literal["artist-title", "title-artist"]
SearchFilter = Literal["songs", "videos"]
Privacy = Literal["PRIVATE", "UNLISTED", "PUBLIC"]


def _fail(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, (AuthError, ValueError)):
        return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": public_error(exc)}


def search_tracks(query: str, filter: SearchFilter = "songs", limit: int = 8) -> dict[str, Any]:
    """Search the public YouTube Music catalog.

    Prefer songs. Use filter="videos" only as a fallback. Returns video id,
    title, artists, album, and duration. Does not write anything.
    """
    try:
        return get_youtube().search_public(query, filter=filter, limit=limit)
    except Exception as exc:
        return _fail(exc)


def parse_tracklist_tool(text: str, order: Order = "artist-title") -> dict[str, Any]:
    """Parse a free-form tracklist. No network and no writes.

    Handles 'Artist — Title', 'Artist - Title', 'Artist: Title',
    'Title by Artist', numbering, bullets, and a trailing duration.
    order is artist-title (default) or title-artist. feat./remaster/live/remix
    tags are reported separately from the core title.
    """
    try:
        tracks = [track.as_dict() for track in parse_tracklist(text, order=order)]
    except Exception as exc:
        return _fail(exc)
    return {"ok": True, "count": len(tracks), "tracks": tracks}


def match_tracklist_tool(
    text: str,
    order: Order = "artist-title",
    search_limit: int = 8,
) -> dict[str, Any]:
    """Match a tracklist to YouTube Music. Dry run: this never writes.

    Each track is matched, needs_review, or not_found. confidence is 0 to 1.
    chosen is the best hit; alternatives are the next candidates. Songs are
    preferred over videos. Cyrillic (Russian and Kazakh) is transliterated
    and searched again. Show needs_review and not_found to the user before
    any create or add call.
    """
    try:
        return get_youtube().match(text, order=order, search_limit=search_limit)
    except Exception as exc:
        return _fail(exc)


def list_playlists(limit: int = 25) -> dict[str, Any]:
    """List playlists in the user's YouTube Music library. Requires auth.

    limit is how many to return. Does not modify the library.
    """
    try:
        return get_youtube().list_playlists(limit=limit)
    except Exception as exc:
        return _fail(exc)


def get_playlist(playlist_id: str, limit: int = 100) -> dict[str, Any]:
    """Return playlist metadata and tracks.

    limit is the maximum number of tracks. Pass 0 to read the whole playlist
    (used internally for dedupe). Does not modify the playlist.
    """
    try:
        return get_youtube().get_playlist(playlist_id, limit=limit)
    except Exception as exc:
        return _fail(exc)


def create_playlist(
    title: str,
    description: str = "",
    privacy: Privacy = "PRIVATE",
    commit: bool = False,
) -> dict[str, Any]:
    """Create a YouTube Music playlist. Preview unless commit is true.

    privacy is PRIVATE (default), UNLISTED, or PUBLIC. With commit false,
    nothing is created and the response describes what would be created.
    With commit true, the playlist is created empty and its id and URL are
    returned. Add tracks afterwards with add_playlist_tracks.
    """
    try:
        return get_youtube().create_playlist(title, description, privacy, commit=commit)
    except Exception as exc:
        return _fail(exc)


def add_playlist_tracks(
    playlist_id: str,
    video_ids: list[str],
    commit: bool = False,
) -> dict[str, Any]:
    """Add tracks to a playlist, skipping ids already on it.

    Preview unless commit is true. would_add is what a commit would send;
    skipped_duplicates were already on the playlist or repeated in video_ids.
    Pass only ids the user confirmed (matched rows, or a needs_review
    alternative they picked). Duplicates are always removed first: YouTube
    Music rejects the whole batch if any id is already present. If a later
    chunk fails, added lists the ids that were written and not_added lists
    the rest. If a reply is lost, the playlist is re-read; when that also
    fails, the in-flight chunk is returned as uncertain rather than not_added.
    A playlist created a moment ago may not be browsable yet. That read is
    retried for a few seconds. If it is still unreadable, dedupe treats the
    new playlist as empty. The add itself is not retried.
    """
    try:
        return get_youtube().add_tracks(playlist_id, video_ids, commit=commit)
    except Exception as exc:
        return _fail(exc)


TOOL_FUNCTIONS = (
    search_tracks,
    parse_tracklist_tool,
    match_tracklist_tool,
    list_playlists,
    get_playlist,
    create_playlist,
    add_playlist_tracks,
)

# Public tool names. parse/match keep the names a client should call; the
# Python functions have a suffix only to avoid clashing with the parser module.
TOOL_NAMES = (
    "search_tracks",
    "parse_tracklist",
    "match_tracklist",
    "list_playlists",
    "get_playlist",
    "create_playlist",
    "add_playlist_tracks",
)


def build_server():
    from mcp.server import MCPServer

    mcp = MCPServer(
        "playlister",
        instructions=INSTRUCTIONS,
        version=__version__,
        log_level="WARNING",
    )
    mcp.tool(name="parse_tracklist")(parse_tracklist_tool)
    mcp.tool(name="match_tracklist")(match_tracklist_tool)
    mcp.tool()(search_tracks)
    mcp.tool()(list_playlists)
    mcp.tool()(get_playlist)
    mcp.tool()(create_playlist)
    mcp.tool()(add_playlist_tracks)
    return mcp


def run_stdio() -> None:
    from playlister.auth import prepare_auth_file

    prepare_auth_file()
    build_server().run(transport="stdio")


def run_http(port: int) -> None:
    from playlister.auth import prepare_auth_file

    # Localhost only. There is no flag to bind another interface.
    prepare_auth_file()
    build_server().run(transport="streamable-http", host="127.0.0.1", port=port)
