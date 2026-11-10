# playlister

`playlister` is a local [MCP](https://modelcontextprotocol.io) server that turns a playlist written in chat into a real [YouTube Music](https://music.youtube.com) playlist.

Paste something messy:

```text
1. Oasis — Wonderwall (Remastered 2011)
Кино - Группа крови
Мөлдір Әуелбекова — Әдемі
Get Lucky by Daft Punk (feat. Pharrell Williams) 6:09
```

The assistant parses it, searches YouTube Music, and shows a confidence score for every row (`matched`, `needs_review`, `not_found`) before it creates or changes anything. You confirm the uncertain rows. Then it writes.

It exists because bulk transfer tools drop or mis-file tracks once titles pick up `feat.`, `remastered`, a year, or a Cyrillic spelling that does not match the Latin one on YouTube Music (Russian, and Kazakh letters ә ғ қ ң ө ұ ү һ і). It also exists because a playlist tool should run on your machine. Your browser headers and OAuth tokens stay in a file you control. They are not sent to an app author's server.

Matching is the product. The server normalizes case and punctuation, splits `feat.` / `ft.` / `featuring`, treats remaster tags and years as weak signals, and keeps live, radio edit, remix, and version tags (`acoustic`, `karaoke`, `nightcore`, …) as reasons to prefer one recording over another. It searches the original text and a Latin transliteration, scores title, artist, and duration, prefers songs, and falls back to videos. Confident rows can be added as-is. Close calls come back with alternatives for you to pick.

## Security model

- **Your process.** The default transport is stdio: the MCP client starts `playlister` and talks over stdin/stdout. Nothing listens on a port.
- **Optional HTTP is localhost only.** `playlister --transport http` binds `127.0.0.1`. There is no option to bind another interface.
- **No telemetry.** The project does not phone home. Network calls happen only when a tool runs, and only to YouTube Music and Google (catalog search, library reads and writes, and OAuth token refresh via `ytmusicapi`).
- **Auth stays on disk.** Set `PLAYLISTER_AUTH_PATH` to a `ytmusicapi` browser-headers file or OAuth file. When the server process starts, it sets that file to mode `600` if it can. `playlister --list-tools` does not open the file. Set `chmod 600` yourself before starting if you want the restriction even when the process cannot change it. The contents are never logged, never returned to the MCP client, and stripped from error text if a library exception echoes them.
- **Preview, then commit.** `create_playlist` and `add_playlist_tracks` do not write unless `commit` is true. New playlists default to `PRIVATE`.
- **Dedupe before add.** YouTube Music rejects an entire add when any id is already on the playlist. playlister reads the playlist and sends only the missing ids.

Do not commit the auth file. It is gitignored. Do not paste cookies or tokens into chat; the server never needs you to.

Search and `match_tracklist` work without auth (public catalog). Listing your library, creating a playlist, and adding tracks require the auth file.

## Install

Python 3.11 or newer.

From a checkout, for day-to-day use:

```bash
uv tool install --from . playlister
# or
pipx install .
```

From GitHub, once you do not have a checkout:

```bash
uvx --from git+https://github.com/igor53627/playlister playlister
pipx install "git+https://github.com/igor53627/playlister"
```

`uvx playlister` also works after the package is published on PyPI. Until then, use one of the forms above. A checkout is enough to develop:

```bash
uv sync --all-groups
uv run playlister --list-tools
```

`--list-tools` prints the tool names and exits. With no arguments the process waits on stdin for an MCP client. Silence is expected.

## Auth

Both formats are the ones [`ytmusicapi`](https://ytmusicapi.readthedocs.io/) already uses. Pick one.

### Browser headers

1. In a logged-in browser, open `https://music.youtube.com`.
2. Open developer tools → Network, filter for `browse`, and copy the request headers of an authenticated `POST` (Firefox: right click → Copy → Copy Request Headers. Chrome: copy the request-header block, or the header fields from "Copy as fetch").
3. Write the file somewhere outside the repo:

```bash
mkdir -p ~/.config/playlister
chmod 700 ~/.config/playlister
ytmusicapi browser --file ~/.config/playlister/auth.json
chmod 600 ~/.config/playlister/auth.json
```

If your `ytmusicapi` build has no `--file` flag, run `ytmusicapi browser`, then move the `browser.json` it created onto that path and `chmod 600` it.

The file looks like a small JSON object of headers (`Authorization`, `Cookie`, and a few others). Leave it like that. Sessions last until you log out of YouTube Music, often a long time.

### OAuth

OAuth needs your own YouTube Data API client (Google Cloud project, OAuth client id of type "TVs and Limited Input devices"). Then:

```bash
ytmusicapi oauth --file ~/.config/playlister/auth.json
chmod 600 ~/.config/playlister/auth.json
```

The MCP client environment also needs the same client id and secret, because `ytmusicapi` uses them to refresh the token:

```bash
PLAYLISTER_OAUTH_CLIENT_ID=...
PLAYLISTER_OAUTH_CLIENT_SECRET=...
```

Those two variables are only for the OAuth file. Browser auth ignores them.

### Environment

| Variable | Required | Purpose |
| --- | --- | --- |
| `PLAYLISTER_AUTH_PATH` | for library writes | Path to the browser or OAuth JSON. If unset, `~/.config/playlister/auth.json` is used when that file exists. |
| `PLAYLISTER_OAUTH_CLIENT_ID` | OAuth only | YouTube Data API client id. |
| `PLAYLISTER_OAUTH_CLIENT_SECRET` | OAuth only | YouTube Data API client secret. |
| `PLAYLISTER_BRAND_ACCOUNT` | no | Brand-account id, if you want requests sent as a YouTube brand account. |
| `PLAYLISTER_HTTP_PORT` | no | Port for `--transport http`. Default `8765`. Host is always `127.0.0.1`. |
| `PLAYLISTER_LOG_LEVEL` | no | Log level on stderr. Default `INFO`. Auth material is redacted. |

## Register with an MCP client

Point the client at the `playlister` executable and pass the auth path in the server environment. The examples use `uvx`. Replace the command with `playlister` if you installed it with pipx or `uv tool install`.

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows:

```json
{
  "mcpServers": {
    "playlister": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/igor53627/playlister", "playlister"],
      "env": {
        "PLAYLISTER_AUTH_PATH": "/home/you/.config/playlister/auth.json"
      }
    }
  }
}
```

Add `PLAYLISTER_OAUTH_CLIENT_ID` and `PLAYLISTER_OAUTH_CLIENT_SECRET` next to the auth path when the file is an OAuth token.

### Cursor

Cursor Settings → MCP → Add a new MCP server, transport **stdio**:

- Command: `uvx`
- Args: `--from` `git+https://github.com/igor53627/playlister` `playlister`
- Env: `PLAYLISTER_AUTH_PATH` = the absolute path of your auth file

The same block in `~/.cursor/mcp.json` (or the project `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "playlister": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/igor53627/playlister", "playlister"],
      "env": {
        "PLAYLISTER_AUTH_PATH": "/home/you/.config/playlister/auth.json"
      }
    }
  }
}
```

### Generic stdio

Any client that can launch a subprocess:

```json
{
  "command": "playlister",
  "args": [],
  "env": {
    "PLAYLISTER_AUTH_PATH": "/home/you/.config/playlister/auth.json"
  }
}
```

### Local HTTP

For a client that speaks streamable HTTP instead of stdio:

```bash
playlister --transport http --port 8765
```

The endpoint is `http://127.0.0.1:8765/mcp`. It is not reachable from another machine.

## Tools

| Tool | Writes? | What it does |
| --- | --- | --- |
| `parse_tracklist` | no | Splits a free-form list into artist, title, duration, and tags. No network. |
| `search_tracks` | no | Catalog search. `filter` is `songs` (default) or `videos`. |
| `match_tracklist` | no | Dry run. One row per input line: `matched`, `needs_review`, or `not_found`, with `confidence`, `chosen`, and `alternatives`. |
| `list_playlists` | no | Playlists in your library. Needs auth. |
| `get_playlist` | no | One playlist and its tracks. `limit` 0 reads all of them. |
| `create_playlist` | only if `commit` is true | Title, description, privacy (`PRIVATE` by default, or `UNLISTED` / `PUBLIC`). Without `commit`, returns what it would create. |
| `add_playlist_tracks` | only if `commit` is true | Adds video ids, always skipping ones already on the playlist or repeated in the request. Without `commit`, returns `would_add` and `skipped_duplicates`. A refused chunk reports `added` and `not_added`. A lost reply is re-checked against the playlist, or returned as `uncertain` when that read also fails. |

A normal chat looks like this:

1. `match_tracklist` on the pasted text.
2. The assistant shows `needs_review` and `not_found`, with alternatives, and you pick video ids or skip rows.
3. `create_playlist` with `commit` false, then again with `commit` true once you agree.
4. `add_playlist_tracks` with the confirmed video ids, preview first, then `commit` true.

`order` on the parse and match tools is `artist-title` (default) or `title-artist`. Lines written as `Title by Artist` are detected either way.

Thresholds, as a guide: a song at or above 0.84 with a clear gap over the next different recording is `matched`. From 0.62 up, or a close call, is `needs_review`. Below that is `not_found`. A line with no artist is never auto-matched. Videos need a higher score than songs before they count as `matched`.

## Example

Ask the assistant:

> Make a private YouTube Music playlist called "Kazakh and Kino" from this list, and check the ones you are not sure about before you add them.
>
> Кино — Группа крови
> Мөлдір Әуелбекова — Әдемі
> Oasis - Wonderwall (Remastered)
> Yesterday by The Beatles

It should call `match_tracklist`, show you any row that is not a clean match, and only then call `create_playlist` and `add_playlist_tracks` with `commit` true. The playlist URL comes back as `https://music.youtube.com/playlist?list=...`.

## Development

```bash
uv sync --all-groups
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Tests stub `ytmusicapi`. They do not open a network connection. CI runs the same lint and tests on Python 3.11 and 3.12.

The layout is a `src` package so the console script is `playlister`. Library code that does the matching (`playlister.parser`, `playlister.normalize`, `playlister.translit`, `playlister.matcher`) does not import the MCP SDK, which keeps the heuristics testable on their own.

## License

MIT. See [LICENSE](LICENSE).

## По-русски

playlister — локальный MCP-сервер. Вставляете в чат список треков (`Исполнитель — Название`, даже если он грязный: нумерация, `feat.`, remaster, «Title by Artist»), ассистент ищет совпадения на YouTube Music и только после вашего подтверждения создаёт плейлист.

Сопоставление как раз про те случаи, где сервисы переноса обычно ошибаются: суффиксы feat./remaster/live/remix и транслитерация кириллицы, включая казахские ә ғ қ ң ө ұ ү һ і. Сомнительные строки возвращаются как `needs_review` с альтернативами, а не добавляются молча.

Всё выполняется на вашей машине. Файл авторизации (`ytmusicapi`, браузерные заголовки или OAuth) лежит локально, права по возможности ставятся в `600`, в логи и в ответы ассистенту он не попадает. Своей телеметрии нет: сеть — только YouTube Music и Google. Новые плейлисты по умолчанию `PRIVATE`. Запись идёт через preview (`commit=false`), затем явное подтверждение (`commit=true`).

Установка, переменные и подключение к Claude Desktop / Cursor — в английской части выше. Кратко: `PLAYLISTER_AUTH_PATH` указывает на файл авторизации, клиент MCP запускает `playlister` по stdio.
