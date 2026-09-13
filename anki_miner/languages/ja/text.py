"""JA display-text delegates.

``phrase_wrap_ja`` is Qt-free but lives under ``gui/utils``; the import is
function-local so ``anki_miner.languages`` carries NO import-time edge into
``anki_miner.gui`` (pinned by test_languages_package_carries_no_import_time_gui_edge).
"""

from __future__ import annotations


def ja_normalize(text: str) -> str:
    """The Japanese normaliser, exactly as the subtitle path composes it.

    ``normalize_for_tokenization`` then ``standardize_kanji_variants`` — the two
    steps ``clean_subtitle_text`` runs when no normaliser is injected. The ja
    parser injects none (its default IS this), so this is what a generic consumer
    of ``LanguageProfile.normalize`` gets for Japanese.
    """
    from anki_miner.utils.ja_normalize import normalize_for_tokenization, standardize_kanji_variants

    return standardize_kanji_variants(normalize_for_tokenization(text))


def ja_phrase_wrap(text: str) -> str:
    """Delegate to the existing BudouX phrase wrapper, byte-identically."""
    from anki_miner.gui.utils.phrase_wrap import phrase_wrap_ja

    return phrase_wrap_ja(text)
