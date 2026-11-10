"""Title and artist normalization.

Parenthetical noise (feat., remaster, live, radio edit, remix, version) is
removed from the comparison core and kept as signals. Remix and version tags
change which recording we should pick, so they are scored later rather than
thrown away. Remaster years are weak: they are usually the same song.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_PAREN = re.compile(r"\s*[\(\[]([^\)\]]+)[\)\]]")
# The dot in "feat." / "ft." is not a word character, so a trailing \b would
# fail to match. Require whitespace after the keyword instead.
_FEAT_WORD = r"(?:feat\.?|ft\.?|featuring)"
_FEAT_GROUP = re.compile(rf"(?i)^{_FEAT_WORD}\s+(.+)$")
_FEAT_INLINE = re.compile(rf"(?i)\s+(?:[-–—]\s*)?\b{_FEAT_WORD}\s+(.+)$")
_RADIO = re.compile(r"(?i)\bradio\s+(?:edit|mix|version)\b")
_VERSION_WORD = re.compile(r"(?i)\b(?:version|версия|нұсқа)\b")
_REMASTER = re.compile(r"(?i)remaster")
_LIVE_WORD = re.compile(r"(?i)\blive\b")
_LIVE_PHRASE = re.compile(r"(?i)\blive\s+(?:at|in|from|version|session)\b.*$")
_REMIX = re.compile(r"(?i)\b(?:remix|rmx)\b")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_YEAR_ONLY = re.compile(r"(?i)^\s*(?:19|20)\d{2}\s*$")
_EXPLICIT = re.compile(r"(?i)^\s*(?:explicit|clean)\s*$")
_NAME_SPLIT = re.compile(r"\s*(?:,|&|/|\band\b)\s*", re.IGNORECASE)
_TRAILING_TAG = re.compile(
    r"(?i)\s+[-–—]?\s*(?:"
    r"remaster(?:ed)?(?:\s+(?:19|20)\d{2})?"
    r"|radio\s+edit"
    r"|remix"
    r")\s*$"
)

# Order matters: longer phrases first.
_VERSIONS: tuple[tuple[str, str], ...] = (
    ("album version", "album"),
    ("single version", "single"),
    ("sped up", "sped up"),
    ("8-bit", "8-bit"),
    ("acoustic", "acoustic"),
    ("instrumental", "instrumental"),
    ("piano", "piano"),
    ("karaoke", "karaoke"),
    ("tribute", "tribute"),
    ("nightcore", "nightcore"),
    ("slowed", "slowed"),
    ("reverb", "reverb"),
    ("extended", "extended"),
    ("deluxe", "deluxe"),
    ("bonus", "bonus"),
    ("cover", "cover"),
    ("mono", "mono"),
)

# A bare "version" / "edit" is a weak version signal, not a strong one.
_STRONG_VERSIONS = {
    "acoustic",
    "instrumental",
    "piano",
    "karaoke",
    "cover",
    "tribute",
    "nightcore",
    "slowed",
    "reverb",
    "sped up",
    "8-bit",
}


@dataclass
class TrackSignals:
    featured: list[str] = field(default_factory=list)
    remaster: bool = False
    remaster_year: int | None = None
    live: bool = False
    radio_edit: bool = False
    remix: str | None = None
    version: str | None = None
    year: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "featured": list(self.featured),
            "remaster": self.remaster,
            "remaster_year": self.remaster_year,
            "live": self.live,
            "radio_edit": self.radio_edit,
            "remix": self.remix,
            "version": self.version,
            "year": self.year,
        }


def squash(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("’", "'").replace("`", "'")
    text = text.strip().strip("\"'`“”«»")
    return " ".join(text.split())


def split_names(text: str) -> list[str]:
    """Split a featured-artist list on commas, ampersands, and slashes."""
    text = _REMIX.sub(" ", text)
    text = re.sub(r"(?i)\b(?:edit|version|mix)\b", " ", text)
    parts = [squash(part) for part in _NAME_SPLIT.split(text)]
    return [part for part in parts if part]


def primary_artists(artist: str | None) -> list[str]:
    """Split a display artist into comparable names.

    Commas stay inside the name (``Tyler, The Creator``, ``Earth, Wind & Fire``
    is split on ``&`` only). ``feat.`` is not a primary artist.
    """
    if not artist:
        return []
    without_feat = _FEAT_INLINE.sub(" ", artist)
    parts = re.split(r"\s+(?:&|x)\s+", without_feat, flags=re.IGNORECASE)
    return [squash(part) for part in parts if squash(part)]


def featured_artists(artist: str | None) -> list[str]:
    """Names after feat., ft., or featuring on an artist credit."""
    if not artist:
        return []
    match = _FEAT_INLINE.search(artist)
    if not match:
        return []
    return split_names(match.group(1))


def _generic_version_label(inner: str) -> str:
    """Words left after a version keyword, so two edits do not share one label.

    A bare ``(version)`` stays the sentinel ``version``. That sentinel never
    counts as equal to another copy of itself: the words that would identify
    the edit were not there.
    """
    residual = squash(_VERSION_WORD.sub(" ", inner).strip(" -–—"))
    if not residual:
        return "version"
    return residual.casefold()


def versions_equal(left: str | None, right: str | None) -> bool:
    """True when both labels name the same identified edit.

    Missing labels and the unidentified sentinel are not a match.
    """
    if not left or not right or left == "version" or right == "version":
        return False
    return left == right


def known_version(name: str | None) -> bool:
    """True for a label produced by ``_VERSIONS``, such as ``album`` or ``acoustic``."""
    return name in {label for _, label in _VERSIONS}


def _apply_group(inner: str, signals: TrackSignals) -> bool:
    """Record feat., radio, remix, and version tags found inside one bracket group."""
    feat = _FEAT_GROUP.match(inner.strip())
    if feat:
        signals.featured.extend(split_names(feat.group(1)))
        return True

    applied = False
    if _RADIO.search(inner):
        signals.radio_edit = True
        applied = True
    if _REMASTER.search(inner):
        signals.remaster = True
        year = _YEAR.search(inner)
        if year:
            signals.remaster_year = int(year.group(0))
        applied = True
    if _LIVE_WORD.search(inner):
        signals.live = True
        applied = True
    remix = _REMIX.search(inner)
    if remix:
        name = _REMIX.sub(" ", inner)
        name = re.sub(r"(?i)\b(?:edit|version|mix|radio)\b", " ", name)
        name = squash(name.strip(" -–—"))
        signals.remix = name or "remix"
        applied = True
    version = _version_name(inner)
    # Radio edits and named phrases (_RADIO, _VERSIONS) win. Anything else
    # keeps its own words: "original English language" is not "новая русская".
    if version is None and not signals.radio_edit and _VERSION_WORD.search(inner):
        version = _generic_version_label(inner)
    if version:
        signals.version = signals.version or version
        applied = True
    if _EXPLICIT.match(inner):
        applied = True
    if _YEAR_ONLY.match(inner):
        signals.year = int(inner.strip())
        applied = True
    return applied


def _version_name(text: str) -> str | None:
    """Return a known version name, or None when the phrase is not in ``_VERSIONS``."""
    for phrase, name in _VERSIONS:
        if re.search(rf"(?i)\b{re.escape(phrase)}\b", text):
            return name
    return None


def merge_signals(base: TrackSignals, extra: TrackSignals) -> TrackSignals:
    """Fold tags that were parsed off the artist into the title's signals."""
    for name in extra.featured:
        if name.casefold() not in {item.casefold() for item in base.featured}:
            base.featured.append(name)
    base.remaster = base.remaster or extra.remaster
    base.remaster_year = base.remaster_year or extra.remaster_year
    base.live = base.live or extra.live
    base.radio_edit = base.radio_edit or extra.radio_edit
    base.remix = base.remix or extra.remix
    base.version = base.version or extra.version
    base.year = base.year or extra.year
    return base


def extract_signals(title: str) -> tuple[str, TrackSignals]:
    """Return ``(core_title, signals)`` for a raw title or leftover phrase."""
    signals = TrackSignals()
    core = squash(title)

    def _replace(match: re.Match[str]) -> str:
        """Drop a bracket group that is only a tag; keep other bracket text."""
        inner = match.group(1).strip()
        if _apply_group(inner, signals):
            return " "
        # Unknown parentheses stay as words so a real subtitle can still match,
        # but the brackets themselves do not.
        return f" {inner} "

    core = _PAREN.sub(_replace, core)
    feat = _FEAT_INLINE.search(core)
    if feat:
        signals.featured.extend(split_names(feat.group(1)))
        core = core[: feat.start()]

    live_phrase = _LIVE_PHRASE.search(core)
    if live_phrase and live_phrase.start() > 0:
        signals.live = True
        core = core[: live_phrase.start()]

    trailing = _TRAILING_TAG.search(core)
    if trailing and trailing.start() > 0:
        _apply_group(trailing.group(0), signals)
        core = core[: trailing.start()]

    # Only a suffix ("Wonderwall Acoustic", "Song - Acoustic Version").
    # Words inside the title stay put: "Piano Man", "The Piano Guys", "Cover Me".
    phrases = sorted(
        (phrase for phrase, name in _VERSIONS if name in _STRONG_VERSIONS),
        key=len,
        reverse=True,
    )
    version_tail = re.search(
        rf"(?i)\s+(?:[-–—|]\s+)?({'|'.join(re.escape(phrase) for phrase in phrases)})"
        rf"(?:\s+version)?\s*$",
        core,
    )
    if version_tail and version_tail.start() > 0:
        version = _version_name(version_tail.group(1))
        window = core[version_tail.start() :]
        ordinary_word = version in {"piano", "cover", "tribute"}
        marked = bool(re.search(r"[-–—|]", window)) or "version" in window.casefold()
        if version and not (ordinary_word and not marked):
            signals.version = signals.version or version
            core = core[: version_tail.start()]

    core = squash(core.strip(" -–—|"))
    if not core:
        core = squash(_PAREN.sub(" ", title))
    # A featured list can pick up empties or the same name twice.
    seen: list[str] = []
    for name in signals.featured:
        if name and name.casefold() not in {item.casefold() for item in seen}:
            seen.append(name)
    signals.featured = seen
    return core, signals
