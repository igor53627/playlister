from playlister.parser import parse_tracklist
from playlister.scoring import score_hit


def _track(line: str):
    return parse_tracklist(line)[0]


def _hit(**kwargs):
    base = {
        "resultType": "song",
        "videoId": "abc",
        "title": "Wonderwall",
        "artists": [{"name": "Oasis", "id": "1"}],
        "duration_seconds": 259,
        "album": {"name": "Morning Glory", "id": "a"},
    }
    base.update(kwargs)
    return base


def test_exact_song_scores_higher_than_a_different_artist():
    track = _track("Oasis — Wonderwall")
    good = score_hit(track, _hit())
    wrong = score_hit(
        track,
        _hit(videoId="other", artists=[{"name": "A Cover Band"}]),
    )
    assert good is not None and wrong is not None
    assert good.score >= 0.9
    assert wrong.score < 0.62


def test_remaster_request_still_accepts_the_original():
    track = _track("Oasis — Wonderwall (Remastered 2011)")
    original = score_hit(track, _hit())
    remaster = score_hit(track, _hit(videoId="rm", title="Wonderwall (Remastered)"))
    assert original is not None and remaster is not None
    assert original.score >= 0.84
    assert remaster.score >= original.score


def test_remix_names_match_regardless_of_case():
    track = _track("Oasis — Wonderwall (kygo remix)")
    remix = score_hit(track, _hit(videoId="ky", title="Wonderwall (Kygo Remix)"))
    plain = score_hit(track, _hit())
    assert remix is not None and plain is not None
    assert remix.score > plain.score
    assert "remix matches" in remix.reasons


def test_remix_and_live_prefer_the_tagged_recording():
    remix_query = _track("Oasis — Wonderwall (Kygo Remix)")
    plain = score_hit(remix_query, _hit())
    remix = score_hit(remix_query, _hit(videoId="ky", title="Wonderwall (Kygo Remix)"))
    assert plain is not None and remix is not None
    assert remix.score > plain.score
    assert plain.score < 0.84

    live_query = _track("Oasis — Wonderwall (Live)")
    studio = score_hit(live_query, _hit())
    live = score_hit(live_query, _hit(videoId="lv", title="Wonderwall (Live at Manchester)"))
    assert studio is not None and live is not None
    assert live.score > studio.score


def test_duration_breaks_a_tie():
    track = _track("Oasis — Wonderwall 4:19")
    close = score_hit(track, _hit(duration_seconds=259))
    far = score_hit(track, _hit(videoId="far", duration_seconds=500))
    assert close is not None and far is not None
    assert close.score > far.score
    assert far.score < close.score - 0.1


def test_cyrillic_query_scores_a_latin_catalog_entry():
    track = _track("Кино — Группа крови")
    hit = score_hit(
        track,
        _hit(title="Gruppa Krovi", artists=[{"name": "Kino"}], duration_seconds=283),
    )
    assert hit is not None
    assert hit.score >= 0.9

    kazakh = _track("Мөлдір Әуелбекова — Әдемі")
    kazakh_hit = score_hit(
        kazakh,
        _hit(
            videoId="kz",
            title="Ädemi",
            artists=[{"name": "Moldir Auelbekova"}],
            duration_seconds=180,
        ),
    )
    assert kazakh_hit is not None
    assert kazakh_hit.score >= 0.9


def test_video_is_penalized_against_an_equal_song():
    track = _track("Oasis — Wonderwall")
    song = score_hit(track, _hit())
    video = score_hit(track, _hit(videoId="vid", resultType="video"))
    assert song is not None and video is not None
    assert song.score > video.score
