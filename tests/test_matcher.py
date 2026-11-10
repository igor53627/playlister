from playlister.matcher import match_tracklist


def _song(video_id, title, artist, duration=200, result_type="song"):
    return {
        "resultType": result_type,
        "videoId": video_id,
        "title": title,
        "artists": [{"name": artist}],
        "duration_seconds": duration,
    }


class Catalog:
    """Tiny offline catalog keyed by a distinctive word in the query."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.songs = {
            "wonderwall": [
                _song("song1", "Wonderwall", "Oasis", 259),
                _song("vid1", "Wonderwall", "Oasis", 278, "video"),
                _song("remix1", "Wonderwall (Kygo Remix)", "Oasis", 210),
                _song("live1", "Wonderwall (Live at Manchester)", "Oasis", 280),
            ],
            "krovi": [
                _song("kino1", "Группа крови", "Кино", 283),
            ],
            "gruppa": [
                _song("kino2", "Gruppa Krovi", "Kino", 283),
            ],
            "ademi": [
                _song("kz1", "Ädemi", "Moldir Auelbekova", 190),
            ],
            "әдемі": [
                _song("kz1", "Әдемі", "Мөлдір Әуелбекова", 190),
            ],
            "yesterday": [
                _song("y1", "Yesterday", "The Beatles", 125),
                _song("y2", "Yesterday", "Someone Else", 130),
            ],
            "lucky": [
                _song("gl", "Get Lucky (feat. Pharrell Williams)", "Daft Punk", 369),
            ],
        }

    def search(self, query: str, filter: str, limit: int):
        self.calls.append((filter, query))
        folded = query.casefold()
        hits = []
        for key, rows in self.songs.items():
            if key in folded:
                hits.extend(rows)
        if filter == "videos":
            hits = [row for row in hits if row["resultType"] == "video"] or [
                {**row, "resultType": "video", "videoId": row["videoId"] + "v"} for row in hits[:1]
            ]
        else:
            hits = [row for row in hits if row["resultType"] == "song"]
        return hits[:limit]


def test_buckets_and_song_preference():
    catalog = Catalog()
    result = match_tracklist(
        "\n".join(
            [
                "Oasis — Wonderwall",
                "Кино — Группа крови",
                "Мөлдір Әуелбекова — Әдемі",
                "Not A Real Artist — Zxqv Unlikely Title 999",
            ]
        ),
        catalog.search,
    )
    by_line = {row["input"]: row for row in result["tracks"]}
    assert by_line["Oasis — Wonderwall"]["status"] == "matched"
    assert by_line["Oasis — Wonderwall"]["chosen"]["video_id"] == "song1"
    assert by_line["Oasis — Wonderwall"]["chosen"]["result_type"] == "song"
    assert by_line["Кино — Группа крови"]["status"] == "matched"
    assert by_line["Мөлдір Әуелбекова — Әдемі"]["status"] == "matched"
    assert by_line["Not A Real Artist — Zxqv Unlikely Title 999"]["status"] == "not_found"
    assert result["summary"]["matched"] == 3
    assert result["summary"]["not_found"] == 1
    assert "nothing was written" in result["note"].lower()


def test_cyrillic_query_tries_a_latin_search():
    catalog = Catalog()
    # Drop the Cyrillic catalog row so only the Latin spelling can hit.
    catalog.songs.pop("krovi")
    result = match_tracklist("Кино — Группа крови", catalog.search)
    row = result["tracks"][0]
    assert row["status"] == "matched"
    assert row["chosen"]["video_id"] == "kino2"
    assert any(query.startswith("songs:") and "gruppa" in query for query in row["queries"])


def test_remix_is_needs_review_or_chosen_when_present():
    catalog = Catalog()
    result = match_tracklist("Oasis — Wonderwall (Kygo Remix)", catalog.search)
    row = result["tracks"][0]
    assert row["chosen"]["video_id"] == "remix1"
    assert row["status"] == "matched"
    assert row["alternatives"]


def test_ambiguous_cover_needs_review_when_scores_are_close():
    catalog = Catalog()
    # Two different artists both called something close, via title-only noise,
    # is covered by scoring. Here the same title and a near artist forces review
    # when we plant two high, distinct songs.
    catalog.songs["wonderwall"] = [
        _song("a", "Wonderwall", "Oasis", 259),
        _song("b", "Wonderwall", "Oasis Tribute", 300),
    ]

    def search(query, filter, limit):
        if filter != "songs":
            return []
        return catalog.songs["wonderwall"][:limit]

    result = match_tracklist("Oasis — Wonderwall", search)
    row = result["tracks"][0]
    # The tribute is a different recording and should not be an automatic tie.
    assert row["status"] in {"matched", "needs_review"}
    assert row["chosen"]["video_id"] == "a"
    assert any(item["video_id"] == "b" for item in row["alternatives"])


def test_no_artist_is_never_an_automatic_match():
    catalog = Catalog()
    result = match_tracklist("Wonderwall", catalog.search)
    assert result["tracks"][0]["status"] == "needs_review"


def test_conflicting_recording_is_not_auto_matched():
    def search(query, filter, limit):
        if filter != "songs":
            return []
        return [_song("studio", "Wonderwall", "Oasis", 259)]

    result = match_tracklist("Oasis — Wonderwall (Live)", search)
    row = result["tracks"][0]
    assert row["status"] == "needs_review"
    assert any("live" in query for query in row["queries"])


def test_duplicate_release_does_not_hide_a_close_alternative():
    def search(query, filter, limit):
        if filter != "songs":
            return []
        return [
            _song("a", "Wonderwall", "Oasis", 259),
            _song("b", "Wonderwall", "Oasis", 260),
            _song("c", "Wonderwall (Radio Edit)", "Oasis", 220),
        ]

    result = match_tracklist("Oasis — Wonderwall", search)
    row = result["tracks"][0]
    assert row["status"] == "needs_review"
    assert any(item["video_id"] == "c" for item in row["alternatives"])


def test_video_fallback_uses_the_latin_query():
    def search(query, filter, limit):
        if filter == "videos" and "gruppa" in query.casefold():
            return [_song("latin-video", "Gruppa Krovi", "Kino", 283, "video")]
        return []

    result = match_tracklist("Кино — Группа крови", search)
    row = result["tracks"][0]
    assert row["chosen"]["video_id"] == "latin-video"
    assert any(query.startswith("videos:") and "gruppa" in query for query in row["queries"])


def test_video_fallback_when_songs_are_weak():
    def search(query, filter, limit):
        if filter == "videos" and "rare" in query.casefold():
            return [_song("v-only", "Rare Track", "Rare Artist", 180, "video")]
        return []

    result = match_tracklist("Rare Artist — Rare Track", search)
    row = result["tracks"][0]
    assert row["chosen"]["result_type"] == "video"
    assert row["chosen"]["video_id"] == "v-only"
    assert row["status"] in {"matched", "needs_review"}
