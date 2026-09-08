"""Human names for subtitle/audio language codes, from Qt rather than a table.

The Download tool's language pickers need "Japanese", not "ja". No ISO-639
table exists in this repo and hand-maintaining one is a standing liability, so
names come from :class:`QLocale`, which already ships the CLDR data Qt is built
against. An unrecognised code (yt-dlp's ``live_chat`` pseudo-language, a site's
private tag) resolves to itself, which is exactly what the picker should show.

Names are English (``QLocale.languageToString``), not localised into the app's
UI language: Qt's ``nativeLanguageName()`` folds the territory into the string
("American English" for ``en``), which reads worse than the plain English name
in a list the user scans for a code they half-remember.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from PyQt6.QtCore import QCoreApplication, QLocale

#: A bare language subtag or a code with one script/region subtag. Anything
#: else (a yt-dlp regex, an exclusion, "all") is an *expression*, not a list.
_PLAIN_CODE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,4})?$")

#: yt-dlp/YouTube's most common caption languages. The four mining languages
#: lead; the rest are ordered by rough caption prevalence. The picker sorts the
#: tail by display name at render time, so this order only fixes the head.
COMMON_SUBTITLE_LANGS: tuple[str, ...] = (
    "ja",
    "ko",
    "zh-Hans",
    "zh-Hant",
    "en",
    "es",
    "es-419",
    "pt",
    "pt-BR",
    "fr",
    "de",
    "it",
    "ru",
    "uk",
    "pl",
    "nl",
    "sv",
    "da",
    "fi",
    "cs",
    "el",
    "hu",
    "ro",
    "tr",
    "ar",
    "he",
    "fa",
    "hi",
    "bn",
    "ta",
    "th",
    "vi",
    "id",
    "ms",
    "tl",
)


#: YouTube's marker for the one auto caption made from the audio itself
#: (``ja-orig``); every other automatic_captions key is a machine translation.
_ORIG_SUFFIX = "-orig"


def language_display_name(code: str) -> str:
    """Return a human name for *code*, or *code* itself when unresolvable.

    ``"ja"`` -> ``"Japanese"``; ``"zh-Hans"`` -> ``"Chinese (Simplified Han)"``;
    ``"pt-BR"`` -> ``"Portuguese (Brazil)"``; ``"live_chat"`` -> ``"live_chat"``;
    ``"ja-orig"`` -> ``"Japanese (original)"``.

    The qualifier is appended only when the code actually carries a subtag, so
    bare ``"zh"`` stays ``"Chinese"`` rather than gaining the territory QLocale
    infers for it.
    """
    cleaned = code.strip()
    if not cleaned:
        return ""
    # Stripped first: "orig" is four letters, which the script rule below would
    # otherwise read as a script subtag and render "Japanese (Japanese)".
    original = cleaned.lower().endswith(_ORIG_SUFFIX) and len(cleaned) > len(_ORIG_SUFFIX)
    if original:
        cleaned = cleaned[: -len(_ORIG_SUFFIX)]
    locale = QLocale(cleaned.replace("-", "_"))
    if locale.language() == QLocale.Language.C:
        # QLocale's "no idea" answer; languageToString would render it "C".
        return code.strip()
    name = QLocale.languageToString(locale.language())
    qualifiers: list[str] = []
    subtags = cleaned.replace("_", "-").split("-")[1:]
    if subtags:
        # A four-letter subtag is a script (Hans/Hant/Latn); anything else is a
        # region (BR, 419, CN).
        qualifier = (
            QLocale.scriptToString(locale.script())
            if len(subtags[0]) == 4
            else QLocale.territoryToString(locale.territory())
        )
        if qualifier:
            qualifiers.append(qualifier)
    if original:
        qualifiers.append(QCoreApplication.translate("LanguageNames", "original"))
    if qualifiers:
        name = f"{name} ({', '.join(qualifiers)})"
    return name


def parse_lang_list(value: str) -> tuple[str, ...] | None:
    """Return *value* as a plain code tuple, or ``None`` if it is an expression.

    ``--sub-langs`` accepts far more than a comma list — regexes (``en.*``),
    exclusions (``-live_chat``) and the ``all`` wildcard. Those cannot be shown
    as checkboxes, so the picker routes them to its Advanced field instead. This
    function is the discriminator: a tuple means "the checkboxes can express
    it", ``None`` means "leave it to Advanced".
    """
    tokens = [token.strip() for token in value.split(",")]
    kept: list[str] = []
    for token in tokens:
        if not token:
            continue
        if token == "all" or not _PLAIN_CODE_RE.match(token):
            return None
        if token not in kept:
            kept.append(token)
    return tuple(kept)


def format_lang_list(codes: Sequence[str]) -> str:
    """Render *codes* as the ``--sub-langs`` value yt-dlp expects."""
    return ",".join(codes)
