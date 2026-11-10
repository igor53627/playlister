"""Parse a free-form tracklist pasted from chat.

Accepted shapes, mixed in one blob:

- ``Artist — Title`` (em dash, en dash, or spaced hyphen)
- ``Artist: Title``
- ``Title by Artist``
- numbered or bulleted lines, with an optional ``m:ss`` duration
- a bare title when the line has no separator
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from playlister.normalize import (
    TrackSignals,
    extract_signals,
    featured_artists,
    merge_signals,
    primary_artists,
    squash,
)

_PREFIX = re.compile(
    r"^(?:"
    r"\d{1,3}[\.\)\]]\s+"
    r"|\d{1,3}\s+-\s+"
    r"|\[[0-9]{1,3}\]\s+"
    r"|[-*•·]\s+"
    r")"
)
_DURATION = re.compile(r"\s[\(\[]?(\d{1,2}:\d{2}(?::\d{2})?)[\)\]]?\s*$")
_DASH_SPLIT = re.compile(r"\s+[-–—|]\s+|\s+/\s+")
_BY_SPLIT = re.compile(r"\s+by\s+", re.IGNORECASE)
_HEADER = {
    "playlist",
    "tracklist",
    "tracks",
    "songs",
    "song",
    "list",
}
_SEPARATOR_LINE = re.compile(r"[-=_*~]{3,}")
_MAX_LINES = 300


@dataclass(frozen=True)
class ParsedTrack:
    line_number: int
    raw: str
    artist: str | None
    title: str
    duration_seconds: int | None
    artist_core: str | None
    title_core: str
    artists: tuple[str, ...]
    signals: TrackSignals

    def as_dict(self) -> dict[str, object]:
        return {
            "line_number": self.line_number,
            "raw": self.raw,
            "artist": self.artist,
            "title": self.title,
            "duration_seconds": self.duration_seconds,
            "artists": list(self.artists),
            **self.signals.as_dict(),
        }


def parse_clock(text: str) -> int | None:
    parts = text.split(":")
    if not all(part.isdigit() for part in parts):
        return None
    if len(parts) == 2:
        minutes, seconds = int(parts[0]), int(parts[1])
        if seconds >= 60:
            return None
        return minutes * 60 + seconds
    if len(parts) == 3:
        hours, minutes, seconds = (int(part) for part in parts)
        if minutes >= 60 or seconds >= 60:
            return None
        return hours * 3600 + minutes * 60 + seconds
    return None


def _pop_duration(line: str) -> tuple[str, int | None]:
    match = _DURATION.search(line)
    if not match or match.start() <= 0:
        return line, None
    seconds = parse_clock(match.group(1))
    if seconds is None:
        return line, None
    body = line[: match.start()].strip(" -–—(")
    if not body:
        return line, None
    return body, seconds


def _skip(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("#") or stripped.startswith("//"):
        return True
    if _SEPARATOR_LINE.fullmatch(stripped):
        return True
    return stripped.casefold() in _HEADER


def _split_artist_title(line: str, order: str) -> tuple[str | None, str]:
    # An explicit separator wins over the word "by", so "Ben E. King — Stand by Me"
    # stays artist / title. "Title by Artist" is only the fallback.
    pieces = [piece.strip() for piece in _DASH_SPLIT.split(line) if piece.strip()]
    if len(pieces) >= 2:
        left, right = pieces[0], " - ".join(pieces[1:])
        if order == "title-artist":
            return right, left
        return left, right

    by_parts = _BY_SPLIT.split(line)
    if len(by_parts) >= 2 and all(part.strip() for part in by_parts):
        title = " by ".join(part.strip() for part in by_parts[:-1])
        artist = by_parts[-1].strip()
        return artist, title

    if ":" in line and "://" not in line:
        left, right = (part.strip() for part in line.split(":", 1))
        if left and right:
            if order == "title-artist":
                return right, left
            return left, right

    return None, line


def parse_line(raw: str, line_number: int, order: str = "artist-title") -> ParsedTrack | None:
    if _skip(raw):
        return None
    line = squash(raw)
    line = _PREFIX.sub("", line).strip()
    if _skip(line):
        return None
    line, duration = _pop_duration(line)
    artist, title = _split_artist_title(line, order)
    title = squash(title)
    artist = squash(artist) if artist else None
    if not title and artist:
        title, artist = artist, None
    if not title:
        return None

    title_core, signals = extract_signals(title)
    if artist:
        artist_core, artist_signals = extract_signals(artist)
        merge_signals(signals, artist_signals)
        for name in featured_artists(artist):
            if name.casefold() not in {item.casefold() for item in signals.featured}:
                signals.featured.append(name)
        if artist_core and artist_core != artist:
            artist = artist_core
    else:
        artist_core = None

    artists = tuple(primary_artists(artist_core or artist))
    return ParsedTrack(
        line_number=line_number,
        raw=raw.strip(),
        artist=artist,
        title=title,
        duration_seconds=duration,
        artist_core=artist_core or None,
        title_core=title_core or title,
        artists=artists,
        signals=signals,
    )


def parse_tracklist(text: str, order: str = "artist-title") -> list[ParsedTrack]:
    if order not in {"artist-title", "title-artist"}:
        raise ValueError("order must be 'artist-title' or 'title-artist'")
    tracks: list[ParsedTrack] = []
    for index, raw in enumerate(text.splitlines(), start=1):
        parsed = parse_line(raw, index, order)
        if parsed is not None:
            tracks.append(parsed)
        if len(tracks) > _MAX_LINES:
            raise ValueError(f"tracklist is longer than {_MAX_LINES} tracks; split it into parts")
    return tracks
