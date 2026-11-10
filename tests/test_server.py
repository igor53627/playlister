import asyncio
import os
import sys

from playlister.server import TOOL_NAMES, search_tracks
from playlister.youtube import YouTubeMusic, set_client_factory


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
