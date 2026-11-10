from playlister.normalize import extract_signals, primary_artists


def test_feat_remaster_and_year_leave_the_core_title():
    core, signals = extract_signals("Get Lucky (feat. Pharrell Williams) [Remastered 2011]")
    assert core == "Get Lucky"
    assert signals.featured == ["Pharrell Williams"]
    assert signals.remaster is True
    assert signals.remaster_year == 2011


def test_feat_abbreviations_and_inline_form():
    core, signals = extract_signals("Song ft. Someone")
    assert core == "Song"
    assert signals.featured == ["Someone"]
    core, signals = extract_signals("Song featuring Someone Else")
    assert core == "Song"
    assert "Someone Else" in signals.featured


def test_live_radio_and_remix_are_signals():
    core, signals = extract_signals("Wonderwall (Live at Manchester)")
    assert core == "Wonderwall"
    assert signals.live is True

    core, signals = extract_signals("Wonderwall (Radio Edit)")
    assert core == "Wonderwall"
    assert signals.radio_edit is True

    core, signals = extract_signals("Wonderwall (Kygo Remix)")
    assert core == "Wonderwall"
    assert signals.remix == "Kygo"


def test_version_tags_stay_available_as_signals():
    core, signals = extract_signals("Song (Acoustic Version)")
    assert core == "Song"
    assert signals.version == "acoustic"

    core, signals = extract_signals("Song (Nightcore)")
    assert signals.version == "nightcore"
    assert "nightcore" not in core.casefold()


def test_band_commas_are_not_split_but_ampersand_is():
    assert primary_artists("Tyler, The Creator") == ["Tyler, The Creator"]
    assert primary_artists("Earth, Wind & Fire") == ["Earth, Wind", "Fire"]
    assert primary_artists("Daft Punk feat. Pharrell") == ["Daft Punk"]


def test_whitespace_is_squashed():
    core, _signals = extract_signals("  Wonderwall   ")
    assert core == "Wonderwall"
