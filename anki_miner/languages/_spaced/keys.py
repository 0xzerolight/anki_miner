"""Dictionary key folding and the comparison fold for cased Latin languages.

Two DIFFERENT functions (R6/R7): ``CasefoldDictKeys.fold_term`` builds index
keys on both the import and the query side and never drops words;
``spaced_dedup_fold`` is the S3 comparison fold for known words, blacklists
and dedup, which additionally drops a leading article/infinitive marker so a
deck front ``to go`` meets the mined ``go``.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable

Fold = Callable[[str], str]


class CasefoldDictKeys:
    """DictKeyFolding: NFC + optional extra fold + casefold; Rule A then Rule A′.

    Symmetric by construction — the importer and the provider are handed the
    same profile's instance. ``extra_fold`` runs before ``casefold`` (ro's
    cedilla→comma-below map is the case it exists for). No Rule B: there is no
    kana script to reconcile.
    """

    def __init__(self, extra_fold: Fold | None = None) -> None:
        self._extra_fold = extra_fold

    def fold_term(self, s: str) -> str:
        text = unicodedata.normalize("NFC", s)
        if self._extra_fold is not None:
            text = self._extra_fold(text)
        return text.casefold()

    def fold_reading(self, s: str | None) -> str | None:
        return None if s is None else unicodedata.normalize("NFC", s)

    def homograph_keep_mask(self, word: str, rows: list[tuple[str, str]], lemma: str | None = None) -> list[bool]:
        """Rule A (term == word), else Rule A′ (term == lemma), each with the same-content carve-out."""
        for key in (word, lemma):
            if not key:
                continue
            exact = [term == key for term, _ in rows]
            if any(exact):
                contents = {content for (_, content), hit in zip(rows, exact, strict=True) if hit}
                return [hit or content in contents for (_, content), hit in zip(rows, exact, strict=True)]
        return [True] * len(rows)


def _strip_trailing_punctuation(text: str) -> str:
    end = len(text)
    while end and (text[end - 1].isspace() or unicodedata.category(text[end - 1]).startswith("P")):
        end -= 1
    return text[:end]


def _apostrophes(text: str) -> str:
    return text.replace("’", "'")


def spaced_dedup_fold(keys: CasefoldDictKeys, leading_words: frozenset[str] = frozenset()) -> Fold:
    """The S3 comparison fold: key fold, trailing punctuation off, leading table words dropped.

    Only a text of one to three whitespace tokens is touched. To a fixed point:
    a leading table word is dropped while more than one token remains (``to``
    alone stays ``to``), and a table entry ending in an apostrophe that is
    glued to the first token is stripped while some of the token remains (ca
    ``l'home`` → ``home``; ``'`` and ``’`` compare equal). Looping to a fixed
    point is what makes the fold idempotent — ``LanguageProfile.dedup_fold``
    must be, because folded keys are stored and folded again.
    """
    # Folded like the text they are compared with: casefold maps el's final sigma (ένας → ένασ).
    whole_words = frozenset(_apostrophes(keys.fold_term(word)) for word in leading_words)
    elided = tuple(sorted((w for w in whole_words if w.endswith("'")), key=lambda w: (-len(w), w)))

    def fold(text: str) -> str:
        folded = _strip_trailing_punctuation(keys.fold_term(text))
        tokens = folded.split()
        if not 1 <= len(tokens) <= 3:
            return folded.strip()
        changed = True
        while changed:
            changed = False
            if len(tokens) > 1 and _apostrophes(tokens[0]) in whole_words:
                tokens = tokens[1:]
                changed = True
                continue
            head = _apostrophes(tokens[0])
            for entry in elided:
                if head.startswith(entry) and len(head) > len(entry):
                    tokens[0] = tokens[0][len(entry) :]
                    changed = True
                    break
        return " ".join(tokens)

    return fold
