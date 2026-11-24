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

## Why a hybrid backend

`ytmusicapi`'s OAuth mode is broken server-side by Google: authenticated calls to YouTube Music's internal API return HTTP 400 `Request contains an invalid argument` ([sigma67/ytmusicapi#813](https://github.com/sigma67/ytmusicapi/issues/813)). Browser-header auth still works, but sessions can expire within minutes or hours.

In **OAuth mode** (recommended), `playlister` splits requests across two backends:

- **Account operations** (`list_playlists`, `get_playlist`, `create_playlist`, `add_playlist_tracks`, `remove_playlist_tracks`, `get_track_ratings`, `rate_track`) go through the **official YouTube Data API v3** using your OAuth token, with automatic token refresh via `https://oauth2.googleapis.com/token`.
- **Catalog search, matching, and radio** (`search_tracks`, `match_tracklist`, `get_similar_tracks`) stay on an **unauthenticated `ytmusicapi`** instance. This preserves YouTube Music song/album search metadata and consumes zero YouTube Data API quota (playlister never calls Data API search).

**Browser-header mode** remains supported as before and uses `ytmusicapi` for account operations.

## Security model

- **Your process.** The default transport is stdio: the MCP client starts `playlister` and talks over stdin/stdout. Nothing listens on a port.
- **Optional HTTP is localhost only.** `playlister --transport http` binds `127.0.0.1`. There is no option to bind another interface.
- **No telemetry.** The project does not phone home. Network calls happen only when a tool runs, and only to YouTube Music and Google (anonymous catalog search/radio via `ytmusicapi`, official YouTube Data API v3 for OAuth account operations, and Google OAuth token refresh).
- **Auth stays on disk.** Set `PLAYLISTER_AUTH_PATH` (default `~/.config/playlister/auth.json`) to an OAuth token file or a `ytmusicapi` browser-headers file. When the server starts, it tightens that file to mode `600` if it can; OAuth refreshes persist atomically with mode `600`. `playlister --list-tools` does not open the file. The contents are never logged, never returned to the MCP client, and stripped from error text if a library exception echoes them.
- **Preview, then commit.** `create_playlist`, `add_playlist_tracks`, `remove_playlist_tracks`, and `rate_track` do not write unless `commit` is true. New playlists default to `PRIVATE`.
- **Dedupe before add.** playlister reads the playlist and sends only the missing video ids.

Do not commit the auth file. It is gitignored. Do not paste cookies or tokens into chat; the server never needs you to.

`parse_tracklist`, `search_tracks`, `match_tracklist`, and `get_similar_tracks` work without auth (public catalog). Listing your library, reading private playlists, creating/editing playlists, and reading/setting ratings require auth.

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

### OAuth (recommended — YouTube Data API v3)

OAuth uses your own Google Cloud OAuth client and the official YouTube Data API v3.

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create (or select) a project.
2. Enable **YouTube Data API v3** (APIs & Services → Library → search "YouTube Data API v3" → Enable).
3. Configure the **OAuth consent screen** (APIs & Services → OAuth consent screen):
   - Choose User Type **External** and fill in the required app name and contact email.
   - **Important (7-day token expiry):** While an External app stays in **Testing** mode, Google expires OAuth refresh tokens after **7 days** (you will get `invalid_grant` and have to re-auth every week). To avoid this, publish the consent screen to **In production** (APIs & Services → OAuth consent screen → **Publish App**). For personal use you do not need to submit for Google verification; you will simply click through Google's unverified-app warning once during sign-in. If you prefer to stay in Testing mode, add your Google account under Test users.
4. Create an OAuth client (APIs & Services → Credentials → Create Credentials → **OAuth client ID**):
   - Application type: **TVs and Limited Input devices**.
   - Copy the **Client ID** and **Client Secret**.
5. Run playlister's built-in device flow CLI (no `ytmusicapi oauth` needed):

```bash
export PLAYLISTER_OAUTH_CLIENT_ID="your-client-id"
export PLAYLISTER_OAUTH_CLIENT_SECRET="your-client-secret"
playlister auth oauth
```

It prints a verification URL and a short user code, polls Google until you approve access, and writes `~/.config/playlister/auth.json` atomically with mode `600` (pass `--file /path/to/auth.json` to choose another path).

6. Set `PLAYLISTER_OAUTH_CLIENT_ID` and `PLAYLISTER_OAUTH_CLIENT_SECRET` in your MCP client config so playlister can refresh expired access tokens automatically.

### Browser headers (alternative)

If you prefer not to create a Google Cloud project, browser-header auth still works via `ytmusicapi` (note that browser sessions can expire within minutes or hours, and `get_track_ratings` requires OAuth):

1. In a logged-in browser, open `https://music.youtube.com`.
2. Open developer tools → Network, filter for `browse`, and copy the request headers of an authenticated `POST`.
3. Write the file somewhere outside the repo:

```bash
mkdir -p ~/.config/playlister
chmod 700 ~/.config/playlister
ytmusicapi browser --file ~/.config/playlister/auth.json
chmod 600 ~/.config/playlister/auth.json
```

If your `ytmusicapi` build has no `--file` flag, run `ytmusicapi browser`, then move the `browser.json` it created onto that path and `chmod 600` it. Browser auth ignores `PLAYLISTER_OAUTH_CLIENT_ID` and `PLAYLISTER_OAUTH_CLIENT_SECRET`.

### Environment

| Variable | Required | Purpose |
| --- | --- | --- |
| `PLAYLISTER_AUTH_PATH` | for account ops | Path to the OAuth or browser JSON. If unset, `~/.config/playlister/auth.json` is used when that file exists. |
| `PLAYLISTER_OAUTH_CLIENT_ID` | OAuth only | Google Cloud OAuth client id (TVs and Limited Input devices). |
| `PLAYLISTER_OAUTH_CLIENT_SECRET` | OAuth only | Google Cloud OAuth client secret. |
| `PLAYLISTER_BRAND_ACCOUNT` | browser only | Brand-account id, if you want browser-auth requests sent as a YouTube brand account. |
| `PLAYLISTER_HTTP_PORT` | no | Port for `--transport http`. Default `8765`. Host is always `127.0.0.1`. |
| `PLAYLISTER_LOG_LEVEL` | no | Log level on stderr. Default `INFO`. Auth material is redacted. |

## Quota (OAuth / YouTube Data API v3)

Google's default YouTube Data API v3 quota is **10,000 units per day** (resets at midnight Pacific Time):

| Operation | Quota cost |
| --- | --- |
| `parse_tracklist`, `search_tracks`, `match_tracklist`, `get_similar_tracks` | **0** (local or anonymous `ytmusicapi`) |
| `list_playlists`, `get_playlist` | **1** per page (up to 50 items/page) |
| `get_track_ratings` | **1** per batch of up to 50 video ids |
| `create_playlist` | **50** |
| `add_playlist_tracks` | **50** per added video (preview costs only the playlist read) |
| `remove_playlist_tracks` | **50** per removed item (preview costs only the playlist read) |
| `rate_track` | **50** |

That allows roughly ~190–200 track additions or removals per day on the free default quota. Data API search (100 units/call) is never used. If you hit the limit, `playlister` surfaces a clear `quotaExceeded` error and reports any partial progress already completed.

## Register with an MCP client

Point the client at the `playlister` executable and pass the auth env vars. The examples use `uvx`. Replace the command with `playlister` if you installed it with pipx or `uv tool install`.

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows:

```json
{
  "mcpServers": {
    "playlister": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/igor53627/playlister", "playlister"],
      "env": {
        "PLAYLISTER_AUTH_PATH": "/home/you/.config/playlister/auth.json",
        "PLAYLISTER_OAUTH_CLIENT_ID": "your-client-id",
        "PLAYLISTER_OAUTH_CLIENT_SECRET": "your-client-secret"
      }
    }
  }
}
```

Omit `PLAYLISTER_OAUTH_CLIENT_ID` and `PLAYLISTER_OAUTH_CLIENT_SECRET` if using browser headers instead of OAuth.

### Cursor

Cursor Settings → MCP → Add a new MCP server, transport **stdio**:

- Command: `uvx`
- Args: `--from` `git+https://github.com/igor53627/playlister` `playlister`
- Env: `PLAYLISTER_AUTH_PATH`, `PLAYLISTER_OAUTH_CLIENT_ID`, `PLAYLISTER_OAUTH_CLIENT_SECRET`

The same block in `~/.cursor/mcp.json` (or the project `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "playlister": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/igor53627/playlister", "playlister"],
      "env": {
        "PLAYLISTER_AUTH_PATH": "/home/you/.config/playlister/auth.json",
        "PLAYLISTER_OAUTH_CLIENT_ID": "your-client-id",
        "PLAYLISTER_OAUTH_CLIENT_SECRET": "your-client-secret"
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
    "PLAYLISTER_AUTH_PATH": "/home/you/.config/playlister/auth.json",
    "PLAYLISTER_OAUTH_CLIENT_ID": "your-client-id",
    "PLAYLISTER_OAUTH_CLIENT_SECRET": "your-client-secret"
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
| `search_tracks` | no | Anonymous catalog search via `ytmusicapi`. `filter` is `songs` (default) or `videos`. |
| `match_tracklist` | no | Dry run. One row per input line: `matched`, `needs_review`, or `not_found`, with `confidence`, `chosen`, and `alternatives`. |
| `get_similar_tracks` | no | Radio / similar tracks for a seed `video_id` via anonymous `ytmusicapi` (`get_watch_playlist(radio=True)`). Works without auth and uses no Data API quota. |
| `list_playlists` | no | Playlists in your library (`playlists.list mine=true` in OAuth mode). Needs auth. |
| `get_playlist` | no | One playlist and its tracks, including `playlist_item_id` so items can be removed. `limit` 0 reads all of them. |
| `create_playlist` | only if `commit` is true | Title, description, privacy (`PRIVATE` by default, or `UNLISTED` / `PUBLIC`). Without `commit`, returns what it would create. |
| `add_playlist_tracks` | only if `commit` is true | Adds video ids, skipping ones already on the playlist or repeated in the request. Without `commit`, returns `would_add` and `skipped_duplicates`. Reports partial `added` / `not_added` (or `uncertain` on lost replies) if a write fails. |
| `remove_playlist_tracks` | only if `commit` is true | Removes tracks by `video_id` (resolved to `playlist_item_id`). Without `commit`, returns `would_remove`, `would_remove_items`, and `not_found`. |
| `get_track_ratings` | no | Returns `like` / `dislike` / `none` for video ids via Data API `videos.getRating` (batches up to 50 ids per call). Requires OAuth. |
| `rate_track` | only if `commit` is true | Sets a track rating (`like`, `dislike`, or `none`). Without `commit`, previews the change. |

A normal chat looks like this:

1. `match_tracklist` on the pasted text.
2. The assistant shows `needs_review` and `not_found`, with alternatives, and you pick video ids or skip rows.
3. `create_playlist` with `commit` false, then again with `commit` true once you agree.
4. `add_playlist_tracks` with the confirmed video ids, preview first, then `commit` true.

`order` on the parse and match tools is `artist-title` (default) or `title-artist`. Lines written as `Title by Artist` are detected either way.

Thresholds, as a guide: a song at or above 0.84 with a clear gap over the next different recording is `matched`. From 0.62 up, or a close call, is `needs_review`. Below that is `not_found`. A line with no artist is never auto-matched. A title that is only a prefix of a longer word (`Айда` versus `Айдахар`) is not a strong match. Videos need a higher score than songs, and a comparable studio song wins over a video of the same title. Fan, concert, and reaction uploads are penalized unless the request itself asks for them. Identical copies of one recording (same title and artist, about the same duration) are one candidate: the song with an album name is kept, and they do not count as a tie.

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

Tests stub HTTP and `ytmusicapi`. They do not open a network connection. CI runs the same lint and tests on Python 3.11 and 3.12.

The layout is a `src` package so the console script is `playlister`. Library code that does the matching (`playlister.parser`, `playlister.normalize`, `playlister.translit`, `playlister.matcher`) does not import the MCP SDK, which keeps the heuristics testable on their own.

## License

MIT. See [LICENSE](LICENSE).

## По-русски

playlister — локальный MCP-сервер. Вставляете в чат список треков (`Исполнитель — Название`, даже если он грязный: нумерация, `feat.`, remaster, «Title by Artist»), ассистент ищет совпадения на YouTube Music и только после вашего подтверждения создаёт или редактирует плейлист.

Сопоставление как раз про те случаи, где сервисы переноса обычно ошибаются: суффиксы feat./remaster/live/remix и транслитерация кириллицы, включая казахские ә ғ қ ң ө ұ ү һ і. Сомнительные строки возвращаются как `needs_review` с альтернативами, а не добавляются молча.

В режиме OAuth (рекомендуется) используется гибридная схема: поиск, сопоставление и радио (`get_similar_tracks`) работают через неавторизованный `ytmusicapi` без расхода квоты, а все операции с аккаунтом (плейлисты, добавление/удаление треков, оценки) — через официальный YouTube Data API v3, потому что OAuth-режим `ytmusicapi` сломан на стороне Google. Для настройки OAuth достаточно создать клиент «TVs and Limited Input devices» в Google Cloud (перевести consent screen в **In production**, чтобы refresh token не истекал через 7 дней) и выполнить `playlister auth oauth`. Авторизация через браузерные заголовки также сохранена.

Всё выполняется на вашей машине. Файл авторизации лежит локально с правами `600`, в логи и ответы ассистенту не попадает. Своей телеметрии нет. Новые плейлисты по умолчанию `PRIVATE`. Запись идёт через preview (`commit=false`), затем явное подтверждение (`commit=true`).
