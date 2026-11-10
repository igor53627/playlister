"""Turn a tracklist into matched / needs_review / not_found, without writing.

Search is injected so tests stay offline. Production passes ``YTMusic.search``.
Songs are queried first. Videos are a fallback when no song is already a
confident match, and a song wins ties against a video of the same recording.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from playlister.normalize import extract_signals
from playlister.parser import ParsedTrack, parse_tracklist
from playlister.scoring import ScoredCandidate, artist_similarity, name_similarity, score_hit
from playlister.translit import skeleton

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


def _same_recording(left: ScoredCandidate, right: ScoredCandidate) -> bool:
    _, left_signals = extract_signals(left.title)
    _, right_signals = extract_signals(right.title)
    live_differs = left_signals.live != right_signals.live
    radio_differs = left_signals.radio_edit != right_signals.radio_edit
    if live_differs or radio_differs:
        return False
    if (left_signals.remix or None) != (right_signals.remix or None):
        left_remix = left_signals.remix or ""
        right_remix = right_signals.remix or ""
        shares_name = left_remix in right_remix or right_remix in left_remix
        if not (left_remix and right_remix and shares_name):
            return False
    if (left_signals.version or None) != (right_signals.version or None):
        return False
    if name_similarity(left.title, right.title) < 0.92:
        return False
    if artist_similarity(left.artists, list(right.artists)) < 0.8:
        return False
    if left.duration_seconds is None or right.duration_seconds is None:
        return True
    return abs(left.duration_seconds - right.duration_seconds) <= 8


def _prefer_song(ranked: list[ScoredCandidate]) -> ScoredCandidate:
    best = ranked[0]
    floor = best.score - SONG_PREFERENCE
    songs = [item for item in ranked if item.result_type == "song" and item.score >= floor]
    if best.result_type == "video" and songs:
        return songs[0]
    return best


def classify(
    ranked: list[ScoredCandidate], *, had_artist: bool
) -> tuple[str, ScoredCandidate | None]:
    if not ranked:
        return "not_found", None
    chosen = _prefer_song(ranked)
    others = [item for item in ranked if item.video_id != chosen.video_id]
    runner = others[0] if others else None
    margin = chosen.score - (runner.score if runner else 0.0)
    ambiguous = runner is not None and margin < MARGIN and not _same_recording(chosen, runner)

    if not had_artist:
        # A title with no artist is not safe to write automatically.
        if chosen.score >= REVIEW_THRESHOLD:
            return "needs_review", chosen
        return "not_found", chosen

    if chosen.result_type == "video":
        if chosen.score >= VIDEO_MATCH_THRESHOLD and not ambiguous:
            return "matched", chosen
        if chosen.score >= REVIEW_THRESHOLD:
            return "needs_review", chosen
        return "not_found", chosen

    if chosen.score >= MATCH_THRESHOLD and not ambiguous:
        return "matched", chosen
    if chosen.score >= REVIEW_THRESHOLD:
        return "needs_review", chosen
    return "not_found", chosen


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


def _best_song(found: dict[str, ScoredCandidate]) -> float:
    songs = [item.score for item in found.values() if item.result_type == "song"]
    return max(songs, default=0.0)


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
        if _best_song(found) >= 0.92:
            break
    else:
        if specific and fallback and _best_song(found) < MATCH_THRESHOLD:
            for query in fallback:
                tried.append(f"songs:{query}")
                _absorb(found, track, search(query, "songs", search_limit))

    if _best_song(found) < MATCH_THRESHOLD and ordered:
        video_query = ordered[0]
        tried.append(f"videos:{video_query}")
        _absorb(found, track, search(video_query, "videos", min(search_limit, 6)))

    ranked = sorted(found.values(), key=lambda item: item.score, reverse=True)
    status, chosen = classify(ranked, had_artist=bool(track.artists))
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
