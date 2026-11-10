from playlister.translit import has_cyrillic, skeleton, to_latin


def test_russian_kino_matches_hand_transliteration():
    assert skeleton("Кино") == "kino"
    assert skeleton("Группа крови") == "gruppa krovi"
    assert skeleton("Gruppa Krovi") == skeleton("Группа крови")


def test_kazakh_letters_fold_to_common_latin():
    # ә ғ қ ң ө ұ ү һ і
    assert skeleton("әғқңөұүһі") == "agknouuhi"
    assert skeleton("Мөлдір Әуелбекова") == skeleton("Moldir Auelbekova")
    assert skeleton("Әдемі") == skeleton("Ädemi")
    assert skeleton("Қазақ") == skeleton("Qazaq")


def test_diacritics_and_detection():
    assert skeleton("Mötley Crüe") == "motley crue"
    assert has_cyrillic("Кино")
    assert not has_cyrillic("Kino")
    assert "zh" in to_latin("жук")
