"""Score one YouTube Music search hit against a parsed track."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from playlister.normalize import TrackSignals, extract_signals
from playlister.parser import ParsedTrack, parse_clock
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


@dataclass(frozen=True)
class ScoredCandidate:
    video_id: str
    title: str
    artists: tuple[str, ...]
    album: str | None
    duration_seconds: int | None
    result_type: str
    score: float
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "artists": list(self.artists),
            "album": self.album,
            "duration_seconds": self.duration_seconds,
            "result_type": self.result_type,
            "score": round(self.score, 3),
            "reasons": list(self.reasons),
        }


def _ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    seq = SequenceMatcher(None, left, right).ratio()
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    jaccard = 0.0
    if left_tokens and right_tokens:
        jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    contained = 0.0
    if left in right or right in left:
        shorter, longer = sorted((left, right), key=len)
        # A one-token containment ("one" inside "one more time") is weak.
        if " " in shorter or len(shorter) >= 8:
            contained = 0.9 + 0.1 * (len(shorter) / len(longer))
    return max(seq, (seq + jaccard) / 2, contained)


def name_similarity(left: str, right: str) -> float:
    return _ratio(skeleton(left), skeleton(right))


def artist_similarity(query: tuple[str, ...] | list[str], candidate: list[str]) -> float:
    query_names = [name for name in query if name]
    candidate_names = [name for name in candidate if name]
    if not query_names or not candidate_names:
        return 0.0
    per_query = []
    for wanted in query_names:
        per_query.append(max(name_similarity(wanted, got) for got in candidate_names))
    partial = sum(per_query) / len(per_query)
    joined = name_similarity(" ".join(query_names), " ".join(candidate_names))
    return max(partial, joined)


def _duration_delta(wanted: int | None, got: int | None) -> tuple[float, str | None]:
    if wanted is None or got is None:
        return 0.0, None
    diff = abs(wanted - got)
    if diff <= 3:
        return 0.05, f"duration within {diff}s"
    if diff <= 12:
        return 0.02, f"duration close ({diff}s)"
    if diff <= 30:
        return -0.06, f"duration differs by {diff}s"
    return -0.18, f"duration differs by {diff}s"


def _remix_compatible(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    if left == right or left == "remix" or right == "remix":
        return True
    return left in right or right in left


def _signal_delta(query: TrackSignals, candidate: TrackSignals) -> tuple[float, list[str]]:
    delta = 0.0
    reasons: list[str] = []

    if query.live != candidate.live:
        if candidate.live and not query.live:
            delta -= 0.16
            reasons.append("candidate is live")
        elif query.live and not candidate.live:
            delta -= 0.14
            reasons.append("candidate is not live")
    elif query.live and candidate.live:
        delta += 0.06
        reasons.append("live matches")

    if query.remix or candidate.remix:
        if _remix_compatible(query.remix, candidate.remix):
            delta += 0.08
            reasons.append("remix matches")
        elif candidate.remix and not query.remix:
            delta -= 0.24
            reasons.append("candidate is a remix")
        elif query.remix and not candidate.remix:
            delta -= 0.2
            reasons.append("candidate is not the remix")
        else:
            delta -= 0.16
            reasons.append("different remix")

    if query.radio_edit != candidate.radio_edit:
        delta -= 0.08
        reasons.append("radio edit mismatch")
    elif query.radio_edit and candidate.radio_edit:
        delta += 0.04
        reasons.append("radio edit matches")

    if query.version and candidate.version:
        if query.version == candidate.version:
            delta += 0.06
            reasons.append("version matches")
        else:
            delta -= 0.14
            reasons.append("version mismatch")
    elif candidate.version and not query.version:
        if candidate.version in _STRONG_VERSIONS:
            delta -= 0.2
            reasons.append(f"candidate is {candidate.version}")
        else:
            delta -= 0.05
            reasons.append(f"candidate version {candidate.version}")
    elif query.version and not candidate.version:
        delta -= 0.12
        reasons.append("candidate missing version tag")

    if query.remaster and candidate.remaster:
        delta += 0.03
        reasons.append("remaster matches")
    elif candidate.remaster and not query.remaster:
        delta -= 0.02
        reasons.append("candidate is a remaster")
    elif query.remaster and not candidate.remaster:
        # A remaster request still accepts the original recording.
        delta -= 0.02
        reasons.append("candidate is not tagged remaster")

    if query.featured and candidate.featured:
        wanted = {skeleton(name) for name in query.featured}
        got = {skeleton(name) for name in candidate.featured}
        if wanted & got:
            delta += 0.05
            reasons.append("featured artist matches")
        else:
            pairs = (
                name_similarity(left, right)
                for left in query.featured
                for right in candidate.featured
            )
            overlap = max(pairs, default=0.0)
            if overlap >= 0.8:
                delta += 0.04
                reasons.append("featured artist matches")
            else:
                delta -= 0.06
                reasons.append("featured artist differs")
    elif candidate.featured and not query.featured:
        delta -= 0.03
        reasons.append("candidate has a featured artist")

    return delta, reasons


def _artist_names(item: dict) -> list[str]:
    names: list[str] = []
    artists = item.get("artists")
    if isinstance(artists, list):
        for artist in artists:
            if isinstance(artist, dict) and artist.get("name"):
                names.append(str(artist["name"]))
            elif isinstance(artist, str):
                names.append(artist)
    elif isinstance(artists, str):
        names.append(artists)
    if not names and item.get("artist"):
        artist = item["artist"]
        if isinstance(artist, dict) and artist.get("name"):
            names.append(str(artist["name"]))
        elif isinstance(artist, str):
            names.append(artist)
    return names


def _duration_of(item: dict) -> int | None:
    seconds = item.get("duration_seconds")
    if isinstance(seconds, int):
        return seconds
    if isinstance(seconds, str) and seconds.isdigit():
        return int(seconds)
    text = item.get("duration")
    if isinstance(text, str):
        return parse_clock(text)
    return None


def _album_of(item: dict) -> str | None:
    album = item.get("album")
    if isinstance(album, dict) and album.get("name"):
        return str(album["name"])
    if isinstance(album, str):
        return album
    return None


def score_hit(track: ParsedTrack, item: dict) -> ScoredCandidate | None:
    result_type = item.get("resultType") or item.get("result_type")
    if result_type not in {"song", "video"}:
        return None
    video_id = item.get("videoId") or item.get("video_id")
    title = item.get("title")
    if not isinstance(video_id, str) or not video_id or not isinstance(title, str) or not title:
        return None

    core, signals = extract_signals(title)
    title_sim = name_similarity(track.title_core, core)
    artists = _artist_names(item)
    artist_sim = artist_similarity(track.artists, artists) if track.artists else 0.0

    if track.artists:
        base = 0.58 * title_sim + 0.42 * artist_sim
        if artist_sim < 0.5:
            # Same title, wrong person: do not let this look confident.
            base = min(base, 0.45 + 0.2 * artist_sim)
    else:
        base = min(0.9, title_sim)

    reasons = [f"title {title_sim:.2f}"]
    if track.artists:
        reasons.append(f"artist {artist_sim:.2f}")
    else:
        reasons.append("no artist in the request")

    signal_delta, signal_reasons = _signal_delta(track.signals, signals)
    duration_delta, duration_reason = _duration_delta(track.duration_seconds, _duration_of(item))
    reasons.extend(signal_reasons)
    if duration_reason:
        reasons.append(duration_reason)

    type_delta = 0.03 if result_type == "song" else -0.02
    reasons.append("song" if result_type == "song" else "video")

    # Official-topic audio is still a song. Lyric videos are a weaker fallback.
    if result_type == "video" and re.search(r"(?i)\blyrics?\b", title):
        type_delta -= 0.04
        reasons.append("lyrics video")

    score = min(1.0, max(0.0, base + signal_delta + duration_delta + type_delta))
    return ScoredCandidate(
        video_id=video_id,
        title=title,
        artists=tuple(artists),
        album=_album_of(item),
        duration_seconds=_duration_of(item),
        result_type=str(result_type),
        score=score,
        reasons=tuple(reasons),
    )
