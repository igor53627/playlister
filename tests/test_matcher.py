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


def test_generic_remix_does_not_hide_a_different_mix():
    def search(query, filter, limit):
        if filter != "songs":
            return []
        return [
            _song("plain-remix", "The Long and Winding Road (Remix)", "The Beatles", 220),
            _song("dj-remix", "The Long and Winding Road (DJ Remix)", "The Beatles", 220),
        ]

    result = match_tracklist("The Beatles — The Long and Winding Road (Remix)", search)
    row = result["tracks"][0]
    assert row["status"] == "needs_review"
    ids = {row["chosen"]["video_id"]} | {item["video_id"] for item in row["alternatives"]}
    assert ids == {"plain-remix", "dj-remix"}


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


def _hit(video_id, title, artists, duration, album=None, result_type="song"):
    return {
        "resultType": result_type,
        "videoId": video_id,
        "title": title,
        "artists": [{"name": name} for name in artists],
        "duration_seconds": duration,
        "album": {"name": album} if album else None,
    }


def test_title_stem_is_not_auto_matched():
    def search(query, filter, limit):
        if filter != "songs":
            return []
        return [
            _hit("aidahar", "Айдахар", ["Ирина Кайратовна"], 186, "Айдахар"),
            _hit("arriva", "Arriva", ["Ирина Кайратовна"], 285, "13 ВЫПУСК"),
        ]

    row = match_tracklist("Ирина Кайратовна — Айда", search)["tracks"][0]
    assert row["status"] == "needs_review"
    assert row["chosen"]["title"] == "Айдахар"
    assert row["confidence"] < 0.84


def test_studio_song_wins_over_a_fan_or_concert_video():
    def search(query, filter, limit):
        song = _hit("studio", "Daididau", ["Dimash Qudaibergen"], 250, "Daididau")
        fan = _hit(
            "fan",
            "《Daididau》 Dimash y Alisher Karimov. Almaty 2013",
            ["Dimash Kudaibergen Perú Dears Fan Club Oficial"],
            279,
            result_type="video",
        )
        official = _hit(
            "mv",
            "Dimash Qudaibergen - The Love of Tired Swans (Official Music Video)",
            ["Dimash Qudaibergen"],
            338,
            result_type="video",
        )
        studio = _hit(
            "swans",
            "The Love of Tired Swans",
            ["Dimash Qudaibergen"],
            330,
            "The Love of Tired Swans",
        )
        if "daididau" in query.casefold():
            # The fan upload is in the song results on purpose, so a higher
            # raw video score cannot hide the studio track.
            return [fan, song] if filter == "songs" else [fan]
        if filter == "videos":
            return [official]
        return [studio]

    daididau = match_tracklist("Dimash Kudaibergen — Daididau", search)["tracks"][0]
    assert daididau["status"] == "matched"
    assert daididau["chosen"]["video_id"] == "studio"
    assert daididau["chosen"]["result_type"] == "song"

    swans = match_tracklist("Dimash Kudaibergen — Love of Tired Swans", search)["tracks"][0]
    assert swans["chosen"]["video_id"] == "swans"
    assert swans["chosen"]["result_type"] == "song"

    def videos_only(query, filter, limit):
        if filter != "videos":
            return []
        return [
            _hit(
                "fan",
                "《Daididau》 Dimash y Alisher Karimov. Almaty 2013",
                ["Dimash Kudaibergen Perú Dears Fan Club Oficial"],
                279,
                result_type="video",
            )
        ]

    fan_only = match_tracklist("Dimash Kudaibergen — Daididau", videos_only)["tracks"][0]
    assert fan_only["status"] == "needs_review"
    assert fan_only["chosen"]["result_type"] == "video"


def test_identical_copies_are_one_recording():
    def search(query, filter, limit):
        if filter != "songs":
            return []
        folded = query.casefold()
        if "vecherinka" in folded or "вечеринка" in folded:
            return [
                _hit("plain-first", "Вечеринка", ["Скриптонит"], 364, None),
                _hit("with-album", "Вечеринка", ["Скриптонит"], 364, "Дом с нормальными явлениями"),
                _hit("copy", "Вечеринка", ["Скриптонит"], 366, "Romantic Collection"),
                _hit("other", "Вечеринка (Другая версия)", ["Скриптонит"], 253, "Праздник"),
            ]
        return [
            _hit(
                "short",
                "Той жыры",
                ["Дос-Мукасан", "Мурат Кусаинов", "Мейрбек Молдабеков"],
                133,
                "Той жыры",
            ),
            _hit("long", "Той жыры", ["Дос - Мукасан"], 178, "Той жыры"),
            _hit("old", "Той жыры (old version)", ["Дос - Мукасан"], 175, "Той жыры (old version)"),
            _hit(
                "alt",
                "Той жыры (alternative version)",
                ["Дос - Мукасан"],
                198,
                "Той жыры (alternative version)",
            ),
        ]

    party = match_tracklist("Скриптонит — Вечеринка", search)["tracks"][0]
    assert party["status"] == "matched"
    # Same song and duration: the row that names an album beats the first copy.
    assert party["chosen"]["video_id"] == "with-album"

    wedding = match_tracklist("Dos-Mukasan — Той жыры", search)["tracks"][0]
    assert wedding["status"] == "matched"
    assert wedding["chosen"]["title"] == "Той жыры"
    assert wedding["chosen"]["result_type"] == "song"


def test_different_unidentified_versions_need_review():
    def search(query, filter, limit):
        if filter != "songs":
            return []
        return [
            _song(
                "ru",
                "Song (новая русская версия)",
                "Artist",
                200,
            )
        ]

    row = match_tracklist(
        "Artist — Song (original English language version)",
        search,
    )["tracks"][0]
    assert row["status"] == "needs_review"
    assert "version matches" not in row["chosen"]["reasons"]
    assert "version mismatch" in row["chosen"]["reasons"]


def test_exact_video_beats_a_conflicting_song_edit():
    def search(query, filter, limit):
        song = _hit("old", "Wonderwall (old version)", ["Oasis"], 400, "Old")
        video = _hit("exact", "Wonderwall", ["Oasis"], 259, result_type="video")
        if filter == "videos":
            return [video]
        return [song]

    row = match_tracklist("Oasis — Wonderwall 4:19", search)["tracks"][0]
    assert row["chosen"]["video_id"] == "exact"
    assert row["chosen"]["result_type"] == "video"


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
