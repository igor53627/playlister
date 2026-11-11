import asyncio
import os
import sys

from playlister.server import TOOL_NAMES, search_tracks
from playlister.youtube import YouTubeMusic, clear_fresh_playlists, set_client_factory


class _Silent:
    def search(self, query, filter="songs", limit=10, ignore_spelling=False):
        return [
            {
                "resultType": "song",
                "videoId": "abc123",
                "title": "Wonderwall",
                "artists": [{"name": "Oasis"}],
                "duration_seconds": 259,
            }
        ][:limit]


def test_create_then_add_uses_a_fresh_id_across_clients():
    """create_playlist and add_playlist_tracks each build their own client."""
    from playlister.server import add_playlist_tracks, create_playlist

    class _SeparateCalls:
        def __init__(self) -> None:
            self.added: list[tuple[str, list[str]]] = []

        def create_playlist(
            self,
            title,
            description,
            privacy_status="PRIVATE",
            video_ids=None,
            source_playlist=None,
        ):
            return "PLfresh"

        def get_playlist(self, *args, **kwargs):
            raise KeyError(
                "Unable to find 'contents' using path "
                "['contents', 'twoColumnBrowseResultsRenderer', 'tabs', 0]"
            )

        def add_playlist_items(
            self, playlistId, videoIds=None, source_playlist=None, duplicates=False
        ):
            self.added.append((playlistId, list(videoIds or [])))
            return {"status": "STATUS_SUCCEEDED", "playlistEditResults": []}

    fake = _SeparateCalls()

    def client() -> YouTubeMusic:
        yt = YouTubeMusic(fake, authenticated=True)
        yt._sleep = lambda _delay: None
        return yt

    clear_fresh_playlists()
    set_client_factory(client)
    try:
        created = create_playlist("Road", commit=True)
        written = add_playlist_tracks(created["playlist_id"], ["new1", "new2"], commit=True)
    finally:
        set_client_factory(None)
        clear_fresh_playlists()
    assert created["ok"] is True
    assert written["ok"] is True
    assert written["added"] == ["new1", "new2"]
    assert fake.added == [("PLfresh", ["new1", "new2"])]


def test_tools_use_the_injected_client_and_do_not_write(monkeypatch):
    fake = _Silent()
    set_client_factory(lambda: YouTubeMusic(fake, authenticated=False))
    try:
        result = search_tracks("Oasis Wonderwall", limit=3)
    finally:
        set_client_factory(None)
    assert result["ok"] is True
    assert result["results"][0]["video_id"] == "abc123"


def test_list_tools_flag(capsys):
    from playlister.cli import main

    assert main(["--list-tools"]) == 0
    listed = capsys.readouterr().out.split()
    assert listed == list(TOOL_NAMES)


def test_stdio_server_lists_its_tools():
    async def _list() -> list[str]:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        env = {key: value for key, value in os.environ.items() if key != "PLAYLISTER_AUTH_PATH"}
        env["PLAYLISTER_LOG_LEVEL"] = "WARNING"
        server_params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "playlister"],
            env=env,
        )
        async with (
            stdio_client(server_params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
            return [tool.name for tool in listed.tools]

    names = asyncio.run(asyncio.wait_for(_list(), timeout=30))
    assert set(TOOL_NAMES) <= set(names)
    assert "match_tracklist" in names
    assert "add_playlist_tracks" in names
