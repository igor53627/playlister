from playlister.youtube import YouTubeMusic


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


def test_failed_add_is_not_reported_as_success():
    fake = FakeYT()
    fake.add_playlist_items = lambda *args, **kwargs: {"status": "STATUS_FAILED"}  # type: ignore[method-assign]
    yt = YouTubeMusic(fake, authenticated=True)
    result = yt.add_tracks("PLexisting", ["new1"], commit=True)
    assert result["ok"] is False
    assert "refused" in result["error"]


def test_bad_privacy_is_rejected_without_a_write():
    fake = FakeYT()
    yt = YouTubeMusic(fake, authenticated=True)
    result = yt.create_playlist("X", privacy="FRIENDS", commit=True)
    assert result["ok"] is False
    assert fake.created == []
