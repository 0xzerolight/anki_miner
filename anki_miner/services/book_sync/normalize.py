"""Fold text to the alphabet the aligner compares.

Applied identically to book sentences and Whisper segments, so the only
differences left are transcription errors. The folds are SubPlz's
(``ats/lang.py::Japanese.clean``) minus the ones that need a language switch:
kana folds are no-ops on Korean or Chinese text, so nothing here branches on
a language code.
"""

from __future__ import annotations

import unicodedata

from anki_miner.utils.text_utils import katakana_to_hiragana

_KANJI_DIGITS = str.maketrans("〇零一二三四五六七八九", "00123456789")
_HOMOPHONES = str.maketrans("はへを", "わえお")
_DROP = frozenset("ー々ゝゞヽヾ十百千万億")


def normalize_for_alignment(text: str) -> str:
    """Return *text* folded for alignment (see module docstring).

    Order: NFKC → katakana→hiragana (``utils.text_utils.katakana_to_hiragana``,
    ァ..ヶ) → kanji numerals→digits → Whisper homophones (は/へ/を) → casefold →
    keep letters and numbers only (also dropping prolongation/iteration marks
    and place-value kanji) → collapse adjacent repeats.
    """
    text = katakana_to_hiragana(unicodedata.normalize("NFKC", text))
    text = text.translate(_KANJI_DIGITS).translate(_HOMOPHONES).casefold()
    out: list[str] = []
    for ch in text:
        if ch in _DROP or unicodedata.category(ch)[0] not in ("L", "N"):
            continue
        if out and out[-1] == ch:
            continue
        out.append(ch)
    return "".join(out)
