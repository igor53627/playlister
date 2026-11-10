"""Cyrillic-to-Latin folding for Russian and Kazakh track metadata.

The goal is a comparison key, not a scholarly romanization. Russian and
Kazakh letters collapse onto the spellings people actually type when they
transliterate a title by hand (``Gruppa krovi``, ``Moldir``, ``Ademi``).
"""

from __future__ import annotations

import re
import unicodedata

# Single-character map. Multi-letter digraphs are values. Kazakh-specific
# letters are folded toward the Latin letters those names are usually given
# online (ә->a, ғ->g, қ->k, ң->n, ө->o, ұ/ү->u, һ->h, і->i) so a Cyrillic
# query meets a Latin catalog entry.
_CYRILLIC = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "i",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sh",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
    # Kazakh
    "ә": "a",
    "ғ": "g",
    "қ": "k",
    "ң": "n",
    "ө": "o",
    "ұ": "u",
    "ү": "u",
    "һ": "h",
    "і": "i",
    # Other Cyrillic that shows up in titles
    "ґ": "g",
    "є": "e",
    "ї": "yi",
    "ў": "u",
    "ћ": "h",
    "ј": "j",
    "љ": "l",
    "њ": "n",
    "џ": "dz",
}

_LATIN_FOLD = str.maketrans(
    {
        "ä": "a",
        "á": "a",
        "à": "a",
        "â": "a",
        "ã": "a",
        "å": "a",
        "ā": "a",
        "ö": "o",
        "ó": "o",
        "ò": "o",
        "ô": "o",
        "õ": "o",
        "ō": "o",
        "ü": "u",
        "ú": "u",
        "ù": "u",
        "û": "u",
        "ū": "u",
        "ë": "e",
        "é": "e",
        "è": "e",
        "ê": "e",
        "ē": "e",
        "ï": "i",
        "í": "i",
        "ì": "i",
        "î": "i",
        "ī": "i",
        "ı": "i",
        "ñ": "n",
        "ń": "n",
        "ç": "s",
        "ş": "sh",
        "š": "sh",
        "ğ": "g",
        "ý": "y",
        "ÿ": "y",
        "đ": "d",
        "æ": "ae",
        "œ": "oe",
    }
)

_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")
# Drop punctuation, but keep letters we do not transliterate (Hangul, CJK,
# Arabic, Greek) so an exact non-Latin title can still match itself.
_NON_ALNUM = re.compile(r"[^\w]+", re.UNICODE)


def has_cyrillic(text: str) -> bool:
    return _CYRILLIC_RE.search(text) is not None


def to_latin(text: str) -> str:
    """Transliterate Cyrillic and fold Latin diacritics. Punctuation stays."""
    text = unicodedata.normalize("NFKC", text).casefold()
    out: list[str] = []
    for char in text:
        mapped = _CYRILLIC.get(char)
        if mapped is not None:
            out.append(mapped)
        else:
            out.append(char.translate(_LATIN_FOLD))
    return "".join(out)


def skeleton(text: str) -> str:
    """Loose phonetic key used for similarity, not for display.

    ``kh`` collapses to ``h`` and ``q`` to ``k`` so ``қ`` (mapped to ``k``)
    meets both ``k`` and ``q`` spellings. Digraphs produced by transliteration
    (``zh``, ``sh``, ``ch``, ``ts``, ``yu``, ``ya``) are kept: they separate
    different words better than a one-letter code would.
    """
    latin = to_latin(text).replace("kh", "h").replace("q", "k")
    latin = latin.replace("ß", "ss").replace("_", " ")
    cleaned = _NON_ALNUM.sub(" ", latin)
    return " ".join(cleaned.split())
