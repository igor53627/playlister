import pytest

from playlister.parser import parse_tracklist

SAMPLE = """
# weekend

1. Oasis — Wonderwall (Remastered 2011)
2) Queen - Bohemian Rhapsody 5:55
• Daft Punk – Get Lucky (feat. Pharrell Williams)
Кино - Группа крови
Мөлдір Әуелбекова — Әдемі
Yesterday by The Beatles
Wonderwall by Oasis (Live)

---
playlist
"""


def test_parses_messy_mixed_list():
    tracks = parse_tracklist(SAMPLE)
    assert [track.artist for track in tracks[:4]] == [
        "Oasis",
        "Queen",
        "Daft Punk",
        "Кино",
    ]
    assert tracks[0].title_core == "Wonderwall"
    assert tracks[0].signals.remaster is True
    assert tracks[1].duration_seconds == 5 * 60 + 55
    assert tracks[2].signals.featured == ["Pharrell Williams"]
    assert tracks[3].title_core == "Группа крови"
    assert tracks[4].artist == "Мөлдір Әуелбекова"
    assert tracks[4].title_core == "Әдемі"
    assert tracks[5].artist == "The Beatles"
    assert tracks[5].title == "Yesterday"
    assert tracks[6].signals.live is True
    assert tracks[6].artist == "Oasis"


def test_dash_inside_parentheses_does_not_split_a_by_line():
    tracks = parse_tracklist("Wonderwall (Live - Wembley) by Oasis")
    assert tracks[0].artist == "Oasis"
    assert tracks[0].title == "Wonderwall (Live - Wembley)"
    assert tracks[0].signals.live is True


def test_dash_wins_over_the_word_by():
    tracks = parse_tracklist("Ben E. King — Stand by Me")
    assert tracks[0].artist == "Ben E. King"
    assert tracks[0].title == "Stand by Me"


def test_comma_does_not_invent_an_artist():
    goodbye = parse_tracklist("Hello, Goodbye")
    assert goodbye[0].artist is None
    assert goodbye[0].title == "Hello, Goodbye"
    creator = parse_tracklist("Tyler, The Creator")
    assert creator[0].artist is None
    assert creator[0].title == "Tyler, The Creator"


def test_stand_by_me_splits_on_the_last_by():
    tracks = parse_tracklist("Stand by Me by Ben E. King")
    assert tracks[0].title == "Stand by Me"
    assert tracks[0].artist == "Ben E. King"


def test_title_artist_order_and_colon():
    tracks = parse_tracklist("Wonderwall - Oasis", order="title-artist")
    assert tracks[0].title == "Wonderwall"
    assert tracks[0].artist == "Oasis"
    colon = parse_tracklist("Artist: Title")
    assert colon[0].artist == "Artist"
    assert colon[0].title == "Title"


def test_bare_title_and_comments_are_handled():
    tracks = parse_tracklist("# comment\n\nWonderwall\n")
    assert len(tracks) == 1
    assert tracks[0].artist is None
    assert tracks[0].title == "Wonderwall"


def test_duration_in_parentheses_and_hour_clock():
    tracks = parse_tracklist("Artist - Title (3:45)")
    assert tracks[0].duration_seconds == 225
    long = parse_tracklist("Artist - Title 1:02:03")
    assert long[0].duration_seconds == 3723


def test_rejects_unknown_order_and_huge_lists():
    with pytest.raises(ValueError):
        parse_tracklist("a - b", order="random")
    blob = "\n".join(f"Artist - Title {index}" for index in range(301))
    with pytest.raises(ValueError):
        parse_tracklist(blob)
