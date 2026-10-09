"""Which mining languages this build can actually offer.

``AVAILABLE_LANGUAGES`` is the declared vocabulary and lives in the package
``__init__`` (Stage 0); ``get_profile`` lives in ``languages.registry``, which
is the only import surface consumers use - ``languages/__init__.py`` stays
sealed because ``profile.py`` imports ``services.resource_catalog`` at module
level. A code is only offerable once ``get_profile`` builds, and a tokenizer
extra can be absent from any build, so the selector resolves every code and
drops what does not resolve rather than assuming. The list shows each
language by its native name alone, sorted by that name: Latin scripts first,
accents and case ignored, then Greek, Cyrillic, Hebrew, Arabic, Thai, Han and
Hangul (D2, 2026-10-08). :func:`mining_language_choices` adds the languages a
pack download would unlock (D12).
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass

from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages.registry import get_profile, language_display_name

logger = logging.getLogger(__name__)

#: Script groups in list order, named the way ``unicodedata.name`` starts for
#: that script's letters. A script not named here sorts last.
_SCRIPT_ORDER = ("LATIN", "GREEK", "CYRILLIC", "HEBREW", "ARABIC", "THAI", "CJK", "HANGUL")


def _sort_key(name: str) -> tuple[int, str]:
    """Where *name* sorts: its script's group, then A-Z ignoring accents and case.

    The group has to come first: NFKD splits 한 into Jamo (U+1112), which by
    code point would sort 한국어 before every Han name.
    """
    script = unicodedata.name(name[0], "").split(" ", 1)[0]
    group = _SCRIPT_ORDER.index(script) if script in _SCRIPT_ORDER else len(_SCRIPT_ORDER)
    folded = "".join(ch for ch in unicodedata.normalize("NFKD", name) if not unicodedata.combining(ch))
    return group, folded.casefold()


@dataclass(frozen=True)
class MiningLanguageChoice:
    """One entry of the mining-language list (Settings and the setup wizard).

    Attributes:
        code: The mining-language code.
        native_name: The profile's own name for the language; what the list shows.
        english_name: The English name. Not shown; settings search matches it.
        needs_download: True when the language can only be mined after its
            pack download (D12).
        download_mb: Size of that download (the pack plus any pack it needs).
    """

    code: str
    native_name: str
    english_name: str
    needs_download: bool = False
    download_mb: int = 0


def _pack_download_mb(code: str) -> int | None:
    """MB a pack download for ``code`` fetches, or None when no download can help here.

    None when the language ships no pack, no artifact resolves on this
    platform, or the pack is already installed (an installed pack that still
    leaves the language unavailable is a broken install, not a download).
    """
    from anki_miner.services.language_pack_installer import (  # noqa: PLC0415 - heavy, GUI-only
        combined_download_mb,
        is_installed,
        load_pack,
        pack_supported,
    )

    if load_pack(code) is None or not pack_supported(code) or is_installed(code):
        return None
    return combined_download_mb(code)


def mining_language_choices() -> tuple[MiningLanguageChoice, ...]:
    """Every language this build can mine now or after one pack download (C18, D12).

    Sorted by native name, Latin scripts first (D2).
    """
    choices: list[MiningLanguageChoice] = []
    for code in AVAILABLE_LANGUAGES:
        try:
            profile = get_profile(code)
        except (LookupError, ValueError, ImportError) as exc:
            # LookupError covers the registry miss (KeyError); ImportError covers
            # a build without that language's tokenizer extra.
            logger.info("Mining language %r is declared but not available here: %s", code, exc)
            continue
        native = profile.display_name
        english = profile.english_name or code.upper()
        # Building the profile proves nothing about the packages it needs at
        # parse time; the profile's own probe decides whether it can mine now.
        probe = profile.unavailable_reason
        reason = probe() if probe is not None else None
        if not reason:
            choices.append(MiningLanguageChoice(profile.code, native, english))
            continue
        download_mb = _pack_download_mb(code)
        if download_mb is None:
            logger.info("Mining language %r is not available here: %s", code, reason)
            continue
        choices.append(MiningLanguageChoice(profile.code, native, english, True, download_mb))
    choices.sort(key=lambda choice: _sort_key(choice.native_name))
    return tuple(choices)


def available_mining_languages() -> tuple[tuple[str, str], ...]:
    """``(code, native name)`` for every language that can be mined right now."""
    return tuple((choice.code, choice.native_name) for choice in mining_language_choices() if not choice.needs_download)


def mining_language_display_name(code: str) -> str:
    """The native name of *code*, or the bare code where no profile builds.

    Separate from :func:`available_mining_languages`, which answers about what
    can be MINED: the pack download worker names a language the profile's own
    probe has ruled unavailable — that is precisely the language the pack
    exists to unlock — so the name has to resolve without the probe.
    """
    return language_display_name(code)


def bidi_isolated(name: str) -> str:
    """Wrap a right-to-left ``name`` in Unicode isolates (FSI … PDI).

    Qt picks a plain label's direction from its first strong character, so an
    Arabic or Persian name leading an English sentence laid the whole line out
    right to left. A left-to-right name is returned unchanged.
    """
    if any(unicodedata.bidirectional(char) in ("R", "AL") for char in name):
        return f"⁨{name}⁩"
    return name
