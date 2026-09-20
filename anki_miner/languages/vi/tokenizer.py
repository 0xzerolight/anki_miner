"""Vietnamese tokenizer: underthesea word segmentation + the VLSP 2013 CRF POS model.

``build_tagger()`` is the name tagger_provider's generic non-ja branch resolves,
so Vietnamese needs no code there (the ko shape). ``LockedTagger`` serialises the
concurrent mining tabs on the one engine.

The engine reads a *tagging copy* (``tagging_text``): new-style tone placement,
because underthesea's segmentation dictionary spells the new style and joins
compounds only in it (plan decision 1), and a shouted cue lower-cased
(``_spaced.morphology.tagging_copy``). Both are length-preserving, so each engine
word is found in the copy by a running ``str.find`` and the surface is the SAME
slice of the real line: the stored sentence, the card front and every key stay
old style. A word that cannot be found is dropped, as ``iter_token_spans`` would
drop it.

POS comes from ``FastCRFSequenceTagger`` loaded from the v2.0 model's path inside
the package, not ``pos_tag(model="v2.0")``: in 9.5.0 that function raises
UnboundLocalError on every call after the first, and re-tokenises with
``use_token_normalize=True`` (plan decision 3; the ``[vi]`` extra is capped
<9.6 for the path). ``use_token_normalize=False`` keeps segmentation output a
set of verbatim substrings. The CRF sometimes glues a punctuation piece to a
word; ``regroup_punctuation`` splits it off before tagging.

The engine is imported inside ``_load_engine`` only: a machine without the
``[vi]`` extra or the pack fails there, where tagger_provider turns the
ImportError into its handled ValueError.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from anki_miner.languages._spaced.morphology import tagging_copy
from anki_miner.languages.token import LanguageToken
from anki_miner.languages.vi.keys import vi_fold_term
from anki_miner.languages.vi.pos import STOPWORD_TAG
from anki_miner.languages.vi.stopwords import VI_STOPWORDS
from anki_miner.languages.vi.tones import to_new_style
from anki_miner.services.tagger import LockedTagger

logger = logging.getLogger(__name__)

#: underthesea 9.5.0's v2.0 POS model, relative to ``underthesea/pipeline/pos_tag/``.
POS_MODEL_DIR = "models/pos_crf_vlsp2013_20230303"

#: One line (the tagging copy) -> its words, each a substring of that line.
Segment = Callable[[str], list[str]]
#: A line's words -> one bare VLSP tag per word.
Tag = Callable[[list[str]], list[str]]


def _has_word_character(piece: str) -> bool:
    return any(char.isalnum() for char in piece)


def regroup_punctuation(words: Sequence[str]) -> list[str]:
    """Split every letter- and digit-free piece out of a multi-syllable word."""
    out: list[str] = []
    for word in words:
        if " " not in word:
            out.append(word)
            continue
        run: list[str] = []
        for piece in word.split(" "):
            if _has_word_character(piece):
                run.append(piece)
                continue
            if run:
                out.append(" ".join(run))
                run = []
            if piece:
                out.append(piece)
        if run:
            out.append(" ".join(run))
    return out


def tagging_text(text: str) -> str:
    """The string the engine reads: new-style tones, lowered when shouted; always ``len(text)``."""
    copy = to_new_style(text)
    if len(copy) != len(text):  # input that was not NFC: tag it as it is
        copy = text
    return tagging_copy(copy)


def to_duck_tokens(words: Sequence[str], tags: Sequence[str], text: str, copy: str) -> list[LanguageToken]:
    """Locate each word in ``copy`` and emit the same slice of ``text`` as a fugashi-shaped token."""
    out: list[LanguageToken] = []
    cursor = 0
    for word, tag in zip(words, tags, strict=True):
        start = copy.find(word, cursor)
        if start < 0:
            logger.debug("Vietnamese word %r not found in its line; dropped", word)
            continue
        cursor = start + len(word)
        surface = text[start:cursor]
        lemma = vi_fold_term(surface)
        out.append(
            LanguageToken(
                surface=surface,
                pos1=tag,
                pos2=STOPWORD_TAG if lemma in VI_STOPWORDS else "",
                lemma=lemma,
                kana="",
            )
        )
    return out


class VietnameseTagger:
    """Callable with the fugashi tagger contract: ``tagger(text) -> list[LanguageToken]``."""

    def __init__(self, segment: Segment, tag: Tag) -> None:
        self._segment = segment
        self._tag = tag

    def __call__(self, text: str, **_: Any) -> list[LanguageToken]:
        copy = tagging_text(text)
        words = regroup_punctuation(self._segment(copy))
        tags = self._tag(words) if words else []
        return to_duck_tokens(words, tags, text, copy)

    def parse(self, text: str) -> list[LanguageToken]:
        """fugashi-compatible alias so ``LockedTagger.parse`` delegates cleanly."""
        return self(text)


def _load_engine() -> tuple[Segment, Tag]:
    """Import underthesea (pip extra or pack root) and load the v2.0 POS model once."""
    import underthesea.pipeline.pos_tag as pos_tag
    from underthesea import word_tokenize
    from underthesea.models.fast_crf_sequence_tagger import FastCRFSequenceTagger

    model = FastCRFSequenceTagger()
    model.load(str(Path(pos_tag.__file__).parent / POS_MODEL_DIR))

    def segment(text: str) -> list[str]:
        return list(word_tokenize(text, use_token_normalize=False))

    def tag(words: list[str]) -> list[str]:
        return [str(label).removeprefix("B-") for label in model.predict([[word] for word in words])]

    return segment, tag


def build_tagger() -> LockedTagger:
    """Build the lock-guarded Vietnamese tokenizer (tagger_provider's entry point)."""
    segment, tag = _load_engine()
    return LockedTagger(VietnameseTagger(segment, tag))
