"""Which mining languages this build can actually offer.

``AVAILABLE_LANGUAGES`` is the declared vocabulary and lives in the package
``__init__`` (Stage 0); ``get_profile`` lives in ``languages.registry``, which
is the only import surface consumers use - ``languages/__init__.py`` stays
sealed because ``profile.py`` imports ``services.resource_catalog`` at module
level. A code is only offerable once ``get_profile`` builds, and a tokenizer
extra can be absent from any build, so the selector resolves every code and
drops what does not resolve rather than assuming. Labels read "native —
English", sorted by English name with Japanese first (C18);
:func:`mining_language_choices` adds the languages a pack download would
unlock (D12).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages.registry import get_profile, language_display_name

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MiningLanguageChoice:
    """One entry of the mining-language list (Settings and the setup wizard).

    Attributes:
        code: The mining-language code.
        label: "native — English" (the native name alone when both are equal).
        native_name: The profile's own name for the language.
        english_name: The English name; also what search matches.
        needs_download: True when the language can only be mined after its
            pack download (D12).
        download_mb: Size of that download (the pack plus any pack it needs).
    """

    code: str
    label: str
    native_name: str
    english_name: str
    needs_download: bool = False
    download_mb: int = 0


def _label(native: str, english: str) -> str:
    return native if not english or english == native else f"{native} — {english}"


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

    Sorted by English name with Japanese, the reference language, first.
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
            choices.append(MiningLanguageChoice(profile.code, _label(native, english), native, english))
            continue
        download_mb = _pack_download_mb(code)
        if download_mb is None:
            logger.info("Mining language %r is not available here: %s", code, reason)
            continue
        choices.append(MiningLanguageChoice(profile.code, _label(native, english), native, english, True, download_mb))
    choices.sort(key=lambda choice: (choice.code != "ja", choice.english_name.casefold()))
    return tuple(choices)


def available_mining_languages() -> tuple[tuple[str, str], ...]:
    """``(code, "native — English")`` for every language that can be mined right now."""
    return tuple((choice.code, choice.label) for choice in mining_language_choices() if not choice.needs_download)


def mining_language_display_name(code: str) -> str:
    """The native name of *code*, or the bare code where no profile builds.

    Separate from :func:`available_mining_languages`, which answers about what
    can be MINED: the pack rows and the download worker name a language the
    profile's own probe has ruled unavailable — that is precisely the language
    the pack exists to unlock — so the name has to resolve without the probe.
    """
    return language_display_name(code)


def mining_language_english_name(code: str) -> str:
    """The English name of *code*, or the bare code where no profile builds.

    For surfaces that must be findable without typing the native script: the
    pack rows render 한국어 / 中文 in every string they own, so settings search
    matched neither "Korean" nor "Chinese" until this fed the search index.
    """
    try:
        return get_profile(code).english_name or code.upper()
    except (LookupError, ValueError, ImportError):
        return code.upper()
