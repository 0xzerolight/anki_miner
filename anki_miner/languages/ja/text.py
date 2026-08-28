"""JA display-text delegates.

``phrase_wrap_ja`` is Qt-free but lives under ``gui/utils``; the import is
function-local so ``anki_miner.languages`` carries NO import-time edge into
``anki_miner.gui`` (pinned by test_languages_package_carries_no_import_time_gui_edge).
"""

from __future__ import annotations


def ja_phrase_wrap(text: str) -> str:
    """Delegate to the existing BudouX phrase wrapper, byte-identically."""
    from anki_miner.gui.utils.phrase_wrap import phrase_wrap_ja

    return phrase_wrap_ja(text)
