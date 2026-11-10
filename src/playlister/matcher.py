"""Turn a tracklist into matched / needs_review / not_found, without writing.

Search is injected so tests stay offline. Production passes ``YTMusic.search``.
Songs are queried first. Videos are a fallback when no song is already a
confident match, and a song wins ties against a video of the same recording.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from playlister.normalize import TrackSignals, extract_signals
from playlister.parser import ParsedTrack, parse_tracklist
from playlister.scoring import (
    ScoredCandidate,
    artist_similarity,
    name_similarity,
    remix_compatible,
    score_hit,
)
from playlister.translit import skeleton

_STRONG_VERSIONS = {
    "acoustic",
    "instrumental",
    "piano",
    "karaoke",
    "cover",
    "tribute",
    "nightcore",
    "slowed",
    "reverb",
    "sped up",
    "8-bit",
}

SearchFn = Callable[[str, str, int], list[dict[str, Any]]]

MATCH_THRESHOLD = 0.84
REVIEW_THRESHOLD = 0.62
MARGIN = 0.07
VIDEO_MATCH_THRESHOLD = 0.9
SONG_PREFERENCE = 0.05


def queries_for(track: ParsedTrack) -> list[str]:
    """A short list of catalog queries, most specific first."""
    artist = track.artist_core or track.artist or ""
    title = track.title_core or track.title
    primary = " ".join(part for part in (artist, title) if part).strip()
    queries: list[str] = []

    def add(query: str) -> None:
        cleaned = " ".join(query.split())
        if cleaned and cleaned.casefold() not in {item.casefold() for item in queries}:
            queries.append(cleaned)

    tag = _requested_tag(track.signals)
    if tag:
        add(f"{primary} {tag}")
    add(primary)
    folded = skeleton(primary)
    if folded and folded != primary.casefold():
        add(folded)
    if track.signals.featured:
        add(f"{primary} {track.signals.featured[0]}")
    if track.signals.remix and track.signals.remix != "remix":
        add(f"{primary} {track.signals.remix}")
    if artist and title:
        # Title-only catches a transliteration of the artist we did not guess,
        # while scoring still rejects a different artist.
        add(title)
        folded_title = skeleton(title)
        if folded_title != title.casefold():
            add(folded_title)
    return queries[:5]


def _requested_tag(signals: TrackSignals) -> str | None:
    if signals.remix:
        return "remix" if signals.remix == "remix" else signals.remix
    if signals.version:
        return signals.version
    if signals.live:
        return "live"
    if signals.radio_edit:
        return "radio edit"
    return None


def blocks_auto_match(query: TrackSignals, title: str) -> bool:
    """Conflicting live, radio, remix, or version tags stay in needs_review.

    A remaster tag is not a conflict. It is usually the same song.
    """
    _, candidate = extract_signals(title)
    if query.live != candidate.live or query.radio_edit != candidate.radio_edit:
        return True
    if (query.remix or candidate.remix) and not remix_compatible(query.remix, candidate.remix):
        return True
    query_strong = query.version in _STRONG_VERSIONS
    candidate_strong = candidate.version in _STRONG_VERSIONS
    if (query_strong or candidate_strong) and query.version != candidate.version:
        return True
    return bool(query.version) and not candidate.version


def _same_remix(left: str | None, right: str | None) -> bool:
    """Identity of two hits. A bare "Remix" is not the same mix as "DJ Remix".

    Request scoring still treats a generic remix ask as compatible with a named
    one. That wildcard must not hide the other mix from review.
    """
    if not left and not right:
        return True
    if not left or not right:
        return False
    a, b = left.casefold(), right.casefold()
    if a == "remix" or b == "remix":
        return a == b
    return a == b or a in b or b in a


def _durations_compatible(left: int | None, right: int | None) -> bool:
    """True when two copies look like the same edit.

    Catalog rows for one song often disagree by half a minute. A gap past
    about 50 seconds is a different edit and stays in the comparison.
    """
    if left is None or right is None:
        return True
    diff = abs(left - right)
    if diff <= 12:
        return True
    short = min(left, right) or 1
    return diff <= 50 and diff <= short * 0.4


def _same_recording(left: ScoredCandidate, right: ScoredCandidate) -> bool:
    _, left_signals = extract_signals(left.title)
    _, right_signals = extract_signals(right.title)
    live_differs = left_signals.live != right_signals.live
    radio_differs = left_signals.radio_edit != right_signals.radio_edit
    if live_differs or radio_differs:
        return False
    if not _same_remix(left_signals.remix, right_signals.remix):
        return False
    if (left_signals.version or None) != (right_signals.version or None):
        return False
    if name_similarity(left.title, right.title) < 0.92:
        return False
    if artist_similarity(left.artists, list(right.artists)) < 0.8:
        return False
    return _durations_compatible(left.duration_seconds, right.duration_seconds)


def _comparable_song(item: ScoredCandidate, signals: TrackSignals | None) -> bool:
    """A studio row of the same title and artist, not a different recording."""
    if item.result_type != "song" or item.score < REVIEW_THRESHOLD:
        return False
    if item.title_similarity < 0.88 or item.artist_similarity < 0.8:
        return False
    return signals is None or not blocks_auto_match(signals, item.title)


def _prefer_song(
    ranked: list[ScoredCandidate], signals: TrackSignals | None = None
) -> ScoredCandidate:
    best = ranked[0]
    if best.result_type != "video":
        return best
    songs = [item for item in ranked if _comparable_song(item, signals)]
    if songs:
        return songs[0]
    floor = best.score - SONG_PREFERENCE
    close = [item for item in ranked if item.result_type == "song" and item.score >= floor]
    if close:
        return close[0]
    return best


def classify(
    ranked: list[ScoredCandidate],
    *,
    had_artist: bool,
    signals: TrackSignals | None = None,
) -> tuple[str, ScoredCandidate | None]:
    if not ranked:
        return "not_found", None
    chosen = _prefer_song(ranked, signals)
    others = [item for item in ranked if item.video_id != chosen.video_id]
    runner = next((item for item in others if not _same_recording(chosen, item)), None)
    margin = chosen.score - (runner.score if runner else 0.0)
    ambiguous = runner is not None and margin < MARGIN

    if not had_artist:
        # A title with no artist is not safe to write automatically.
        if chosen.score >= REVIEW_THRESHOLD:
            return "needs_review", chosen
        return "not_found", chosen

    if chosen.result_type == "video":
        if chosen.score >= VIDEO_MATCH_THRESHOLD and not ambiguous:
            status = "matched"
        elif chosen.score >= REVIEW_THRESHOLD:
            status = "needs_review"
        else:
            status = "not_found"
    elif chosen.score >= MATCH_THRESHOLD and not ambiguous:
        status = "matched"
    elif chosen.score >= REVIEW_THRESHOLD:
        status = "needs_review"
    else:
        status = "not_found"
    if status == "matched" and signals is not None and blocks_auto_match(signals, chosen.title):
        status = "needs_review"
    return status, chosen


def _absorb(
    found: dict[str, ScoredCandidate], track: ParsedTrack, hits: list[dict[str, Any]]
) -> None:
    for hit in hits:
        scored = score_hit(track, hit)
        if scored is None:
            continue
        current = found.get(scored.video_id)
        if current is None or scored.score > current.score:
            found[scored.video_id] = scored


def _best_song(found: dict[str, ScoredCandidate], track: ParsedTrack) -> float:
    """Best song that does not conflict with the requested recording tags."""
    scores = [
        item.score
        for item in found.values()
        if item.result_type == "song" and not blocks_auto_match(track.signals, item.title)
    ]
    return max(scores, default=0.0)


def _video_queries(queries: list[str]) -> list[str]:
    """Original spelling, plus the folded Latin query when it differs."""
    if not queries:
        return []
    picked = [queries[0]]
    seen = {queries[0].casefold()}
    for query in queries[1:]:
        if skeleton(query) == query.casefold() and query.casefold() not in seen:
            picked.append(query)
            break
    return picked


def match_track(track: ParsedTrack, search: SearchFn, *, search_limit: int = 8) -> dict[str, Any]:
    found: dict[str, ScoredCandidate] = {}
    tried: list[str] = []
    queries = queries_for(track)
    song_queries = queries
    # The title-only fallback is last and only used when the specific queries
    # did not already find a strong song. That keeps common titles from
    # flooding the candidate list.
    title_key = (track.title_core or "").casefold()
    specific = [query for query in song_queries if query.casefold() != title_key]
    fallback = [query for query in song_queries if query not in specific]
    ordered = specific or fallback

    for query in ordered:
        tried.append(f"songs:{query}")
        _absorb(found, track, search(query, "songs", search_limit))
        if _best_song(found, track) >= 0.92:
            break
    else:
        if specific and fallback and _best_song(found, track) < MATCH_THRESHOLD:
            for query in fallback:
                tried.append(f"songs:{query}")
                _absorb(found, track, search(query, "songs", search_limit))

    if _best_song(found, track) < MATCH_THRESHOLD:
        for video_query in _video_queries(ordered):
            tried.append(f"videos:{video_query}")
            _absorb(found, track, search(video_query, "videos", min(search_limit, 6)))

    # Equal scores are one recording's copies: song, then a row that names an
    # album, then whichever hit was returned first.
    ranked = sorted(
        found.values(),
        key=lambda item: (item.score, item.result_type == "song", bool(item.album)),
        reverse=True,
    )
    status, chosen = classify(ranked, had_artist=bool(track.artists), signals=track.signals)
    alternatives = [
        item.as_dict() for item in ranked if chosen is None or item.video_id != chosen.video_id
    ][:3]
    return {
        "line_number": track.line_number,
        "input": track.raw,
        "parsed": track.as_dict(),
        "status": status,
        "confidence": round(chosen.score, 3) if chosen else 0.0,
        "chosen": chosen.as_dict() if chosen else None,
        "alternatives": alternatives,
        "queries": tried,
    }


def match_tracklist(
    text: str,
    search: SearchFn,
    *,
    order: str = "artist-title",
    search_limit: int = 8,
) -> dict[str, Any]:
    tracks = parse_tracklist(text, order=order)
    rows = [match_track(track, search, search_limit=search_limit) for track in tracks]
    summary = {
        "matched": sum(row["status"] == "matched" for row in rows),
        "needs_review": sum(row["status"] == "needs_review" for row in rows),
        "not_found": sum(row["status"] == "not_found" for row in rows),
        "total": len(rows),
    }
    return {
        "summary": summary,
        "tracks": rows,
        "note": (
            "Dry run only: nothing was written. "
            "Show needs_review and not_found to the user before creating a playlist. "
            "Pass confirmed video ids to add_playlist_tracks."
        ),
    }
