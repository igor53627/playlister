from playlister.youtube import (
    YouTubeMusic,
    clear_fresh_playlists,
    is_fresh_playlist,
    remember_fresh_playlist,
)


class FakeYT:
    def __init__(self) -> None:
        self.playlists: dict[str, dict] = {
            "PLexisting": {
                "id": "PLexisting",
                "title": "Already",
                "description": "old",
                "privacy": "PRIVATE",
                "trackCount": 1,
                "author": {"name": "me"},
                "tracks": [
                    {
                        "videoId": "keep",
                        "title": "Keep",
                        "artists": [{"name": "A"}],
                        "duration": "3:00",
                        "duration_seconds": 180,
                        "resultType": "song",
                    }
                ],
            }
        }
        self.created: list[tuple] = []
        self.added: list[tuple] = []
        self.library = [
            {"playlistId": "PLexisting", "title": "Already", "count": "1", "owned": True},
        ]

    def search(self, query, filter="songs", limit=10, ignore_spelling=False):
        if filter == "songs":
            return [
                {
                    "resultType": "song",
                    "videoId": "song1",
                    "title": "Wonderwall",
                    "artists": [{"name": "Oasis"}],
                    "album": {"name": "Morning Glory"},
                    "duration": "4:19",
                    "duration_seconds": 259,
                }
            ][:limit]
        return []

    def get_library_playlists(self, limit=25):
        return self.library[: limit or None]

    def get_playlist(self, playlistId, limit=100, related=False, suggestions_limit=0):
        return self.playlists[playlistId]

    def create_playlist(
        self,
        title,
        description,
        privacy_status="PRIVATE",
        video_ids=None,
        source_playlist=None,
    ):
        self.created.append((title, description, privacy_status))
        playlist_id = "PLnew"
        self.playlists[playlist_id] = {
            "id": playlist_id,
            "title": title,
            "description": description,
            "privacy": privacy_status,
            "trackCount": 0,
            "author": {"name": "me"},
            "tracks": [],
        }
        return playlist_id

    def add_playlist_items(self, playlistId, videoIds=None, source_playlist=None, duplicates=False):
        existing = {track["videoId"] for track in self.playlists[playlistId]["tracks"]}
        if not duplicates and any(video_id in existing for video_id in videoIds or []):
            return {"error": "duplicates"}
        for video_id in videoIds or []:
            self.playlists[playlistId]["tracks"].append(
                {"videoId": video_id, "title": video_id, "artists": []}
            )
        self.added.append((playlistId, list(videoIds or []), duplicates))
        return {"status": "STATUS_SUCCEEDED", "playlistEditResults": []}


def test_create_and_add_preview_then_commit():
    fake = FakeYT()
    yt = YouTubeMusic(fake, authenticated=True)

    preview = yt.create_playlist("Road", "from chat", "private", commit=False)
    assert preview["commit"] is False
    assert preview["would_create"]["privacy"] == "PRIVATE"
    assert fake.created == []

    created = yt.create_playlist("Road", "from chat", commit=True)
    assert created["playlist_id"] == "PLnew"
    assert created["privacy"] == "PRIVATE"
    assert "music.youtube.com" in created["url"]
    assert len(fake.created) == 1

    planned = yt.add_tracks("PLexisting", ["keep", "new1", "new1", "new2"], commit=False)
    assert planned["commit"] is False
    assert planned["would_add"] == ["new1", "new2"]
    assert planned["skipped_duplicates"] == ["keep", "new1"]
    assert fake.added == []

    written = yt.add_tracks("PLexisting", ["keep", "new1", "new1", "new2"], commit=True)
    assert written["added"] == ["new1", "new2"]
    assert fake.added == [("PLexisting", ["new1", "new2"], False)]


def test_anonymous_client_can_search_but_not_write():
    fake = FakeYT()
    yt = YouTubeMusic(fake, authenticated=False)
    found = yt.search_public("Oasis Wonderwall")
    assert found["ok"] is True
    assert found["results"][0]["video_id"] == "song1"
    assert "feedback" not in found["results"][0]

    denied = yt.create_playlist("Nope", commit=True)
    assert denied["ok"] is False
    assert fake.created == []
    listed = yt.list_playlists()
    assert listed["ok"] is False


def test_match_is_a_dry_run():
    fake = FakeYT()
    yt = YouTubeMusic(fake, authenticated=True)
    result = yt.match("Oasis - Wonderwall")
    assert result["tracks"][0]["status"] == "matched"
    assert result["tracks"][0]["chosen"]["video_id"] == "song1"
    assert fake.created == []
    assert fake.added == []


def test_library_listing_and_playlist_items_drop_private_fields():
    fake = FakeYT()
    yt = YouTubeMusic(fake, authenticated=True)
    listed = yt.list_playlists()
    assert listed["playlists"][0]["playlist_id"] == "PLexisting"
    playlist = yt.get_playlist("PLexisting")
    assert playlist["tracks"][0]["video_id"] == "keep"
    assert "thumbnails" not in playlist["tracks"][0]


def test_lost_reply_is_reconciled_from_the_playlist():
    fake = FakeYT()

    def add(playlistId, videoIds=None, source_playlist=None, duplicates=False):
        for video_id in videoIds or []:
            fake.playlists[playlistId]["tracks"].append(
                {"videoId": video_id, "title": video_id, "artists": []}
            )
        raise TimeoutError("response lost")

    fake.add_playlist_items = add  # type: ignore[method-assign]
    yt = YouTubeMusic(fake, authenticated=True)
    result = yt.add_tracks("PLexisting", ["new1", "new2"], commit=True)
    assert result["ok"] is False
    assert result["added"] == ["new1", "new2"]
    assert result["not_added"] == []
    assert "uncertain" not in result


def test_lost_reply_stays_uncertain_when_reread_fails():
    fake = FakeYT()
    reads = {"n": 0}
    original = fake.get_playlist

    def add(playlistId, videoIds=None, source_playlist=None, duplicates=False):
        raise TimeoutError("response lost")

    def flaky_read(*args, **kwargs):
        reads["n"] += 1
        if reads["n"] > 1:
            raise TimeoutError("playlist read failed")
        return original(*args, **kwargs)

    fake.add_playlist_items = add  # type: ignore[method-assign]
    fake.get_playlist = flaky_read  # type: ignore[method-assign]
    yt = YouTubeMusic(fake, authenticated=True)
    result = yt.add_tracks("PLexisting", ["new1"], commit=True)
    assert result["added"] == []
    assert result["uncertain"] == ["new1"]
    assert result["not_added"] == []


def test_partial_add_reports_completed_chunks():
    fake = FakeYT()
    calls = {"n": 0}

    def add(playlistId, videoIds=None, source_playlist=None, duplicates=False):
        calls["n"] += 1
        if calls["n"] > 1:
            return {"status": "STATUS_FAILED"}
        fake.added.append((playlistId, list(videoIds or []), duplicates))
        return {"status": "STATUS_SUCCEEDED", "playlistEditResults": []}

    fake.add_playlist_items = add  # type: ignore[method-assign]
    yt = YouTubeMusic(fake, authenticated=True)
    ids = [f"v{index}" for index in range(51)]
    result = yt.add_tracks("PLexisting", ids, commit=True)
    assert result["ok"] is False
    assert result["added"] == ids[:50]
    assert result["not_added"] == [ids[50]]
    assert fake.added == [("PLexisting", ids[:50], False)]


def test_failed_add_is_not_reported_as_success():
    fake = FakeYT()
    fake.add_playlist_items = lambda *args, **kwargs: {"status": "STATUS_FAILED"}  # type: ignore[method-assign]
    yt = YouTubeMusic(fake, authenticated=True)
    result = yt.add_tracks("PLexisting", ["new1"], commit=True)
    assert result["ok"] is False
    assert "refused" in result["error"]


_BROWSE_MISSING = KeyError(
    "Unable to find 'contents' using path "
    "['contents', 'twoColumnBrowseResultsRenderer', 'tabs', 0, 'tabRenderer']"
)


def _no_wait(yt: YouTubeMusic) -> list[float]:
    delays: list[float] = []
    yt._sleep = delays.append
    return delays


def test_playlist_browse_is_retried_then_read():
    fake = FakeYT()
    calls = {"n": 0}
    original = fake.get_playlist

    def flaky(playlistId, limit=100, related=False, suggestions_limit=0):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _BROWSE_MISSING
        return original(playlistId, limit, related, suggestions_limit)

    fake.get_playlist = flaky  # type: ignore[method-assign]
    yt = YouTubeMusic(fake, authenticated=True)
    delays = _no_wait(yt)
    playlist = yt.get_playlist("PLexisting")
    assert playlist["ok"] is True
    assert playlist["tracks"][0]["video_id"] == "keep"
    assert "_browse_not_ready" not in playlist
    assert delays == [0.5, 1.0]
    assert calls["n"] == 3


def test_playlist_without_a_tracks_key_is_not_an_empty_playlist():
    fake = FakeYT()
    calls = {"n": 0}
    original = fake.get_playlist

    def flaky(playlistId, limit=100, related=False, suggestions_limit=0):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"id": playlistId, "title": "Already"}
        return original(playlistId, limit, related, suggestions_limit)

    fake.get_playlist = flaky  # type: ignore[method-assign]
    yt = YouTubeMusic(fake, authenticated=True)
    delays = _no_wait(yt)
    playlist = yt.get_playlist("PLexisting")
    assert playlist["ok"] is True
    assert playlist["tracks"][0]["video_id"] == "keep"
    assert delays == [0.5]
    assert calls["n"] == 2


def test_second_add_does_not_treat_a_written_playlist_as_empty():
    clear_fresh_playlists()
    fake = FakeYT()
    calls = {"n": 0}

    def unreadable(*args, **kwargs):
        raise _BROWSE_MISSING

    def add(playlistId, videoIds=None, source_playlist=None, duplicates=False):
        calls["n"] += 1
        if calls["n"] > 1:
            return {"status": "STATUS_FAILED"}
        return {"status": "STATUS_SUCCEEDED", "playlistEditResults": []}

    fake.get_playlist = unreadable  # type: ignore[method-assign]
    fake.add_playlist_items = add  # type: ignore[method-assign]
    yt = YouTubeMusic(fake, authenticated=True)
    _no_wait(yt)
    created = yt.create_playlist("Road", commit=True)
    playlist_id = created["playlist_id"]
    preview = yt.add_tracks(playlist_id, ["keep"], commit=False)
    assert preview["would_add"] == ["keep"]
    assert is_fresh_playlist(playlist_id) is True

    first = yt.add_tracks(playlist_id, ["keep"], commit=True)
    assert first["ok"] is True
    assert first["added"] == ["keep"]
    assert is_fresh_playlist(playlist_id) is False

    second = yt.add_tracks(playlist_id, ["keep", "new2"], commit=True)
    assert second["ok"] is False
    assert calls["n"] == 1
    clear_fresh_playlists()


def test_lost_add_reply_also_ends_the_empty_fallback():
    clear_fresh_playlists()
    fake = FakeYT()
    calls = {"n": 0}

    def unreadable(*args, **kwargs):
        raise _BROWSE_MISSING

    def add(playlistId, videoIds=None, source_playlist=None, duplicates=False):
        calls["n"] += 1
        raise TimeoutError("response lost")

    fake.get_playlist = unreadable  # type: ignore[method-assign]
    fake.add_playlist_items = add  # type: ignore[method-assign]
    yt = YouTubeMusic(fake, authenticated=True)
    _no_wait(yt)
    created = yt.create_playlist("Road", commit=True)
    first = yt.add_tracks(created["playlist_id"], ["keep"], commit=True)
    assert first["ok"] is False
    second = yt.add_tracks(created["playlist_id"], ["keep", "new2"], commit=True)
    assert second["ok"] is False
    assert calls["n"] == 1
    clear_fresh_playlists()


def test_fresh_playlist_mark_expires():
    clear_fresh_playlists()
    remember_fresh_playlist("PLnew", now=1_000.0)
    assert is_fresh_playlist("PLnew", now=1_059.0) is True
    assert is_fresh_playlist("PLnew", now=1_061.0) is False
    clear_fresh_playlists()


def test_empty_browse_payload_is_retried():
    fake = FakeYT()
    calls = {"n": 0}
    original = fake.get_playlist

    def flaky(playlistId, limit=100, related=False, suggestions_limit=0):
        calls["n"] += 1
        if calls["n"] == 1:
            return {}
        return original(playlistId, limit, related, suggestions_limit)

    fake.get_playlist = flaky  # type: ignore[method-assign]
    yt = YouTubeMusic(fake, authenticated=True)
    delays = _no_wait(yt)
    playlist = yt.get_playlist("PLexisting")
    assert playlist["ok"] is True
    assert delays == [0.5]
    assert calls["n"] == 2


def test_new_playlist_add_treats_an_unreadable_browse_as_empty():
    fake = FakeYT()
    adds: list[tuple] = []

    def unreadable(*args, **kwargs):
        raise _BROWSE_MISSING

    def add(playlistId, videoIds=None, source_playlist=None, duplicates=False):
        adds.append((playlistId, list(videoIds or []), duplicates))
        return {"status": "STATUS_SUCCEEDED", "playlistEditResults": []}

    fake.get_playlist = unreadable  # type: ignore[method-assign]
    fake.add_playlist_items = add  # type: ignore[method-assign]
    yt = YouTubeMusic(fake, authenticated=True)
    delays = _no_wait(yt)
    created = yt.create_playlist("Road", commit=True)
    still_missing = yt.get_playlist(created["playlist_id"])
    assert still_missing["ok"] is False
    assert "tracks" not in still_missing
    written = yt.add_tracks(created["playlist_id"], ["new1", "new2"], commit=True)
    assert written["ok"] is True
    assert written["added"] == ["new1", "new2"]
    assert adds == [(created["playlist_id"], ["new1", "new2"], False)]
    assert delays == [0.5, 1.0, 2.0, 4.0, 0.5, 1.0, 2.0, 4.0]


def test_older_playlist_browse_failure_does_not_add():
    fake = FakeYT()

    def unreadable(*args, **kwargs):
        raise _BROWSE_MISSING

    fake.get_playlist = unreadable  # type: ignore[method-assign]
    yt = YouTubeMusic(fake, authenticated=True)
    delays = _no_wait(yt)
    result = yt.add_tracks("PLexisting", ["new1"], commit=True)
    assert result["ok"] is False
    assert fake.added == []
    assert delays == [0.5, 1.0, 2.0, 4.0]
    assert "_browse_not_ready" not in result


def test_unrelated_playlist_errors_are_not_retried():
    fake = FakeYT()

    def boom(*args, **kwargs):
        raise TimeoutError("timed out")

    fake.get_playlist = boom  # type: ignore[method-assign]
    yt = YouTubeMusic(fake, authenticated=True)
    delays = _no_wait(yt)
    result = yt.get_playlist("PLexisting")
    assert result["ok"] is False
    assert delays == []

    def other_key(*args, **kwargs):
        raise KeyError("playlistId")

    fake.get_playlist = other_key  # type: ignore[method-assign]
    again = yt.get_playlist("PLexisting")
    assert again["ok"] is False
    assert delays == []


def test_bad_privacy_is_rejected_without_a_write():
    fake = FakeYT()
    yt = YouTubeMusic(fake, authenticated=True)
    result = yt.create_playlist("X", privacy="FRIENDS", commit=True)
    assert result["ok"] is False
    assert fake.created == []


class FakeDataAPI:
    def __init__(self) -> None:
        self.playlists: dict[str, dict] = {
            "PLoauth": {
                "ok": True,
                "playlist_id": "PLoauth",
                "title": "OAuth List",
                "description": "",
                "privacy": "PRIVATE",
                "track_count": 2,
                "author": "me",
                "tracks": [
                    {
                        "video_id": "keep",
                        "playlist_item_id": "PLI_keep",
                        "title": "Keep",
                        "artists": ["A"],
                        "channel": "A - Topic",
                        "album": None,
                        "duration": None,
                        "duration_seconds": None,
                        "result_type": None,
                    },
                    {
                        "video_id": "drop",
                        "playlist_item_id": "PLI_drop",
                        "title": "Drop",
                        "artists": ["B"],
                        "channel": "B - Topic",
                        "album": None,
                        "duration": None,
                        "duration_seconds": None,
                        "result_type": None,
                    },
                ],
            }
        }
        self.created: list[tuple[str, str, str]] = []
        self.inserted: list[tuple[str, str]] = []
        self.deleted: list[str] = []
        self.rated: list[tuple[str, str]] = []

    def list_playlists(self, limit: int = 25):
        return [
            {
                "playlist_id": pid,
                "title": p["title"],
                "count": p["track_count"],
                "owned": True,
                "privacy": p["privacy"],
            }
            for pid, p in list(self.playlists.items())[: limit or None]
        ]

    def get_playlist(self, playlist_id: str, limit: int = 100):
        from playlister.data_api import DataAPIError

        if playlist_id not in self.playlists:
            raise DataAPIError("Playlist not found", status_code=404, reason="playlistNotFound")
        p = self.playlists[playlist_id]
        tracks = p["tracks"][: limit or None]
        return {**p, "tracks": list(tracks)}

    def create_playlist(self, title: str, description: str = "", privacy: str = "PRIVATE") -> str:
        self.created.append((title, description, privacy))
        pid = "PLoauth_new"
        self.playlists[pid] = {
            "ok": True,
            "playlist_id": pid,
            "title": title,
            "description": description,
            "privacy": privacy,
            "track_count": 0,
            "author": "me",
            "tracks": [],
        }
        return pid

    def insert_playlist_item(self, playlist_id: str, video_id: str):
        self.inserted.append((playlist_id, video_id))
        item_id = f"PLI_{video_id}"
        self.playlists[playlist_id]["tracks"].append(
            {
                "video_id": video_id,
                "playlist_item_id": item_id,
                "title": video_id,
                "artists": [],
                "channel": None,
                "album": None,
                "duration": None,
                "duration_seconds": None,
                "result_type": None,
            }
        )
        return {"id": item_id}

    def delete_playlist_item(self, playlist_item_id: str) -> None:
        self.deleted.append(playlist_item_id)
        for p in self.playlists.values():
            p["tracks"] = [t for t in p["tracks"] if t["playlist_item_id"] != playlist_item_id]

    def get_video_ratings(self, video_ids: list[str]):
        return [
            {"video_id": vid, "rating": "like" if vid == "keep" else "none"} for vid in video_ids
        ]

    def rate_video(self, video_id: str, rating: str) -> None:
        self.rated.append((video_id, rating))


def test_oauth_hybrid_routes_account_ops_to_data_api_and_catalog_to_ytmusic():
    fake_yt = FakeYT()
    fake_yt.get_watch_playlist = lambda videoId, radio=False, limit=25: {  # type: ignore[attr-defined]
        "tracks": [
            {"videoId": videoId, "title": "Seed", "artists": [{"name": "Oasis"}], "length": "4:19"},
            {
                "videoId": "sim1",
                "title": "Don't Look Back In Anger",
                "artists": [{"name": "Oasis"}],
                "album": {"name": "Morning Glory"},
                "length": "4:48",
            },
            {
                "videoId": "sim1",
                "title": "Duplicate",
                "artists": [{"name": "Oasis"}],
                "length": "4:48",
            },
            {
                "videoId": "sim2",
                "title": "Mr. Brightside",
                "artists": [{"name": "The Killers"}],
                "length": "3:43",
            },
        ]
    }
    data_api = FakeDataAPI()
    yt = YouTubeMusic(fake_yt, authenticated=True, data_api=data_api)

    # Catalog stays on ytmusicapi
    found = yt.search_public("Oasis Wonderwall")
    assert found["ok"] is True
    assert found["results"][0]["video_id"] == "song1"

    similar = yt.get_similar_tracks("song1", limit=2)
    assert similar["ok"] is True
    assert similar["seed_video_id"] == "song1"
    assert [t["video_id"] for t in similar["tracks"]] == ["sim1", "sim2"]
    assert similar["tracks"][0]["duration"] == "4:48"

    # Account ops go through Data API
    listed = yt.list_playlists()
    assert listed["ok"] is True
    assert listed["playlists"][0]["playlist_id"] == "PLoauth"

    created = yt.create_playlist("New OAuth", "desc", commit=True)
    assert created["ok"] is True
    assert created["playlist_id"] == "PLoauth_new"
    assert data_api.created == [("New OAuth", "desc", "PRIVATE")]
    assert fake_yt.created == []

    preview_add = yt.add_tracks("PLoauth", ["keep", "new1", "new1"], commit=False)
    assert preview_add["would_add"] == ["new1"]
    assert preview_add["skipped_duplicates"] == ["keep", "new1"]
    assert data_api.inserted == []

    added = yt.add_tracks("PLoauth", ["keep", "new1"], commit=True)
    assert added["ok"] is True
    assert added["added"] == ["new1"]
    assert data_api.inserted == [("PLoauth", "new1")]
    assert fake_yt.added == []

    preview_rm = yt.remove_tracks("PLoauth", ["drop", "missing"], commit=False)
    assert preview_rm["commit"] is False
    assert preview_rm["would_remove"] == ["drop"]
    assert preview_rm["would_remove_items"] == [
        {"video_id": "drop", "playlist_item_id": "PLI_drop"}
    ]
    assert preview_rm["not_found"] == ["missing"]
    assert data_api.deleted == []

    removed = yt.remove_tracks("PLoauth", ["drop", "missing"], commit=True)
    assert removed["ok"] is True
    assert removed["removed"] == ["drop"]
    assert removed["not_found"] == ["missing"]
    assert data_api.deleted == ["PLI_drop"]

    ratings = yt.get_track_ratings(["keep", "new1"])
    assert ratings == {
        "ok": True,
        "ratings": [
            {"video_id": "keep", "rating": "like"},
            {"video_id": "new1", "rating": "none"},
        ],
    }

    rate_preview = yt.rate_track("new1", "like", commit=False)
    assert rate_preview["commit"] is False
    assert data_api.rated == []

    rated = yt.rate_track("new1", "LIKE", commit=True)
    assert rated == {"ok": True, "commit": True, "video_id": "new1", "rating": "like"}
    assert data_api.rated == [("new1", "like")]


def test_oauth_add_reports_quota_exceeded_partial_progress():
    from playlister.data_api import QuotaExceededError

    fake_yt = FakeYT()
    data_api = FakeDataAPI()
    calls = {"n": 0}
    original = data_api.insert_playlist_item

    def fail_second(playlist_id: str, video_id: str):
        calls["n"] += 1
        if calls["n"] == 2:
            raise QuotaExceededError("YouTube Data API daily quota exceeded (10,000 units/day)")
        return original(playlist_id, video_id)

    data_api.insert_playlist_item = fail_second  # type: ignore[method-assign]
    yt = YouTubeMusic(fake_yt, authenticated=True, data_api=data_api)
    result = yt.add_tracks("PLoauth", ["v1", "v2", "v3"], commit=True)
    assert result["ok"] is False
    assert "quota exceeded" in result["error"].lower()
    assert result["added"] == ["v1"]
    assert result["not_added"] == ["v2", "v3"]


def test_oauth_fresh_playlist_404_retried_and_falls_back_to_empty():
    clear_fresh_playlists()
    fake_yt = FakeYT()
    data_api = FakeDataAPI()
    orig_create = data_api.create_playlist

    def create_delayed(title: str, description: str = "", privacy: str = "PRIVATE") -> str:
        pid = orig_create(title, description, privacy)
        del data_api.playlists[pid]  # Simulate eventual consistency 404 on immediate read
        return pid

    data_api.create_playlist = create_delayed  # type: ignore[method-assign]
    data_api.insert_playlist_item = lambda pid, vid: {"id": f"PLI_{vid}"}  # type: ignore[method-assign]
    yt = YouTubeMusic(fake_yt, authenticated=True, data_api=data_api)
    delays = _no_wait(yt)

    created = yt.create_playlist("Fresh", commit=True)
    added = yt.add_tracks(created["playlist_id"], ["v1"], commit=True)
    assert added["ok"] is True
    assert added["added"] == ["v1"]
    assert delays == [0.5, 1.0, 2.0, 4.0]
    clear_fresh_playlists()


def test_browser_mode_remove_rate_and_ratings_guidance():
    fake = FakeYT()
    fake.playlists["PLexisting"]["tracks"][0]["setVideoId"] = "SET_keep"
    removed_calls: list[tuple] = []
    rated_calls: list[tuple] = []

    def remove_items(playlistId, videos):
        removed_calls.append((playlistId, videos))
        return "STATUS_SUCCEEDED"

    def rate_song(videoId, rating="INDIFFERENT"):
        rated_calls.append((videoId, rating))
        return {}

    fake.remove_playlist_items = remove_items  # type: ignore[attr-defined]
    fake.rate_song = rate_song  # type: ignore[attr-defined]
    yt = YouTubeMusic(fake, authenticated=True)

    playlist = yt.get_playlist("PLexisting")
    assert playlist["tracks"][0]["playlist_item_id"] == "SET_keep"

    removed = yt.remove_tracks("PLexisting", ["keep"], commit=True)
    assert removed["ok"] is True
    assert removed["removed"] == ["keep"]
    assert removed_calls == [("PLexisting", [{"videoId": "keep", "setVideoId": "SET_keep"}])]

    rated = yt.rate_track("keep", "none", commit=True)
    assert rated["ok"] is True
    assert rated_calls == [("keep", "INDIFFERENT")]

    ratings = yt.get_track_ratings(["keep"])
    assert ratings["ok"] is False
    assert "OAuth" in ratings["error"]


def test_anonymous_can_use_get_similar_tracks_but_not_ratings_or_writes():
    fake = FakeYT()
    fake.get_watch_playlist = lambda videoId, radio=False, limit=25: {  # type: ignore[attr-defined]
        "tracks": [
            {"videoId": videoId, "title": "Seed", "artists": [{"name": "A"}], "length": "3:00"},
            {"videoId": "rel1", "title": "Next", "artists": [{"name": "B"}], "length": "3:30"},
        ]
    }
    yt = YouTubeMusic(fake, authenticated=False)

    sim = yt.get_similar_tracks("seed1", limit=5)
    assert sim["ok"] is True
    assert [t["video_id"] for t in sim["tracks"]] == ["rel1"]

    assert yt.get_track_ratings(["seed1"])["ok"] is False
    assert yt.rate_track("seed1", "like", commit=True)["ok"] is False


def test_from_env_builds_hybrid_client_for_oauth(tmp_path, monkeypatch):
    import json

    import ytmusicapi

    auth = tmp_path / "oauth.json"
    auth.write_text(
        json.dumps(
            {
                "access_token": "ya29.TOK",
                "refresh_token": "1//REF",
                "refresh_token_expires_in": 604800,
                "token_type": "Bearer",
            }
        ),
        encoding="utf-8",
    )
    auth.chmod(0o600)
    monkeypatch.setenv("PLAYLISTER_AUTH_PATH", str(auth))
    monkeypatch.setenv("PLAYLISTER_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("PLAYLISTER_OAUTH_CLIENT_SECRET", "csec")

    yt_init_calls: list[tuple[tuple, dict]] = []

    class _StubYT:
        def __init__(self, *args, **kwargs):
            yt_init_calls.append((args, kwargs))

    monkeypatch.setattr(ytmusicapi, "YTMusic", _StubYT)

    client = YouTubeMusic.from_env()
    assert client.authenticated is True
    assert client._data_api is not None
    assert yt_init_calls == [((), {})]


def test_from_env_browser_mode_uses_unauthenticated_client_for_catalog_and_radio(
    tmp_path, monkeypatch
):
    import json

    import ytmusicapi

    auth = tmp_path / "browser.json"
    auth.write_text(json.dumps({"Cookie": "SAPISID=abc", "Authorization": "x"}), encoding="utf-8")
    auth.chmod(0o600)
    monkeypatch.setenv("PLAYLISTER_AUTH_PATH", str(auth))

    instances: list[tuple[tuple, dict]] = []

    class _StubYT:
        def __init__(self, *args, **kwargs):
            self.args = args
            instances.append((args, kwargs))

    monkeypatch.setattr(ytmusicapi, "YTMusic", _StubYT)

    client = YouTubeMusic.from_env()
    assert client.authenticated is True
    assert client._data_api is None
    assert client._client is not client._public_client
    assert client._client.args == (str(auth),)
    assert client._public_client.args == ()


def test_oauth_ambiguous_insert_error_reconciles_via_playlist_read():
    from playlister.data_api import DataAPIError

    fake_yt = FakeYT()
    data_api = FakeDataAPI()
    orig_insert = data_api.insert_playlist_item

    def apply_then_504(playlist_id: str, video_id: str):
        orig_insert(playlist_id, video_id)
        raise DataAPIError("HTTP 504", status_code=504, ambiguous=True)

    data_api.insert_playlist_item = apply_then_504  # type: ignore[method-assign]
    yt = YouTubeMusic(fake_yt, authenticated=True, data_api=data_api)
    result = yt.add_tracks("PLoauth", ["v1", "v2"], commit=True)
    assert result["ok"] is False
    assert result["added"] == ["v1"]
    assert result["not_added"] == ["v2"]
    assert "uncertain" not in result


def test_lost_remove_reply_reconciles_and_marks_uncertain_when_reread_fails():
    fake_yt = FakeYT()
    data_api = FakeDataAPI()
    orig_delete = data_api.delete_playlist_item

    def delete_then_timeout(playlist_item_id: str) -> None:
        orig_delete(playlist_item_id)
        raise TimeoutError("reply lost")

    data_api.delete_playlist_item = delete_then_timeout  # type: ignore[method-assign]
    yt = YouTubeMusic(fake_yt, authenticated=True, data_api=data_api)
    reconciled = yt.remove_tracks("PLoauth", ["keep", "drop"], commit=True)
    assert reconciled["ok"] is False
    assert reconciled["removed"] == ["keep"]
    assert reconciled["not_removed"] == ["drop"]
    assert "uncertain" not in reconciled

    # Now fail the re-read too so the in-flight item becomes uncertain
    data_api2 = FakeDataAPI()
    reads = {"n": 0}
    orig_get = data_api2.get_playlist

    def flaky_get(playlist_id: str, limit: int = 100):
        reads["n"] += 1
        if reads["n"] > 1:
            raise TimeoutError("reread failed")
        return orig_get(playlist_id, limit=limit)

    def timeout_delete(playlist_item_id: str) -> None:
        raise TimeoutError("reply lost")

    data_api2.get_playlist = flaky_get  # type: ignore[method-assign]
    data_api2.delete_playlist_item = timeout_delete  # type: ignore[method-assign]
    yt2 = YouTubeMusic(fake_yt, authenticated=True, data_api=data_api2)
    uncertain = yt2.remove_tracks("PLoauth", ["keep", "drop"], commit=True)
    assert uncertain["ok"] is False
    assert uncertain["removed"] == []
    assert uncertain["uncertain"] == ["keep"]
    assert uncertain["not_removed"] == ["drop"]


def test_browser_lost_remove_reply_marks_uncertain_when_reread_fails():
    fake = FakeYT()
    fake.playlists["PLexisting"]["tracks"][0]["setVideoId"] = "SET_keep"
    reads = {"n": 0}
    orig_get = fake.get_playlist

    def flaky_get(*args, **kwargs):
        reads["n"] += 1
        if reads["n"] > 1:
            raise TimeoutError("reread failed")
        return orig_get(*args, **kwargs)

    def boom_remove(*args, **kwargs):
        raise TimeoutError("reply lost")

    fake.get_playlist = flaky_get  # type: ignore[method-assign]
    fake.remove_playlist_items = boom_remove  # type: ignore[attr-defined]
    yt = YouTubeMusic(fake, authenticated=True)
    result = yt.remove_tracks("PLexisting", ["keep"], commit=True)
    assert result["ok"] is False
    assert result["removed"] == []
    assert result["uncertain"] == ["keep"]
    assert result["not_removed"] == []
