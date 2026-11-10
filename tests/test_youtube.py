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
