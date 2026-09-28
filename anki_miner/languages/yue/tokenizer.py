"""pycantonese-backed tokenizer producing fugashi-shaped duck tokens.

Emits ``LanguageToken`` (NOT ``morphology.SyntheticToken``): that class's
isinstance gates drive ja-only attested-reading and span-replacement merge
passes, and a Cantonese token caught by them would be swept into Japanese
morphology.

**The surface is the verbatim slice ``line[start:end]``, the lemma is the
engine's word.** ``segment(line, offsets=True)`` hands back both, and they differ
only when the segmenter joined across an interior space (``今 日好開心`` ->
slice ``今 日``, word ``今日``). Taking the word as the surface would be silently
destructive: ``morphology.iter_token_spans`` has to stitch a space-free surface
across the whitespace and then DROPS it (``morphology.py:487``), so the word
would vanish from the mined set with no error anywhere. The space-free spelling
still reaches the card front, because ``YueMinedFormPolicy`` prefers the lemma.

Cantonese is isolating: there is no inflection, so the lemma is the word as
segmented and ``kana`` is empty. ``pos1`` is the universal tag; ``pos2`` is
``"stopword"`` for a member of the engine's 104-word list and ``""`` otherwise --
a tier the POS editor can exclude, offered and off by default.

Latin, digit and punctuation tokens are emitted so the spans stay aligned with
the line. Their tags are not trustworthy (measured in sentence context:
``Netflix`` NOUN, ``2024`` NOUN, ``IQ題`` NOUN, and fullwidth Latin glues onto
the preceding particle as one ``嘅ＡＢＣ`` PART token), which is why the profile
excludes them with the Han script gate and never with POS. ``YUE_TAG_OVERRIDES``
corrects the Han words the model tags wrong in every context.

**Each run between punctuation marks is segmented on its own**, and each mark is
a token of its own. The segmenter strips punctuation only off a predicted word's
ends, so a whole-line segment emitted ``塞緊車，行告士打道`` as one word: a
dictionary miss that cost every word in it. The tags still come from ONE
``pos_tag`` call over the whole line, so each word keeps its sentence context.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence

from anki_miner.languages.token import LanguageToken
from anki_miner.languages.yue.overrides import YUE_TAG_OVERRIDES
from anki_miner.services.tagger import LockedTagger

#: ``(word, (start, end))`` into the line, the shape ``segment(offsets=True)`` returns.
Span = tuple[str, tuple[int, int]]


class YueTagger:
    """Callable with the fugashi ``Tagger`` surface the parser already consumes."""

    def __init__(self) -> None:
        import pycantonese

        self._segment = pycantonese.segment
        self._pos_tag = pycantonese.pos_tag
        # 104 words, materialised once. Binding the three names costs nothing:
        # the 34 MB segmenter and the 785 KB tagger load on their first call.
        self._stop_words = frozenset(pycantonese.stop_words())

    def __call__(self, text: str, spans: Sequence[Span] | None = None) -> list[LanguageToken]:
        """Tokenize ``text``, or tag ``spans``, a segmentation of it the caller already made.

        The parser's split pass (``yue/parser.py``) re-tags a re-segmented line
        through ``spans``, so the call stays under ``LockedTagger``'s lock.
        """
        if spans is None:
            spans = self._segment_runs(text)
        if not spans:
            return []
        words = [word for word, _offsets in spans]
        tagged = self._pos_tag(words)
        tokens: list[LanguageToken] = []
        for (word, (start, end)), (_word, tag) in zip(spans, tagged, strict=True):
            surface = text[start:end]
            if not surface:
                continue
            tokens.append(
                LanguageToken(
                    surface=surface,
                    pos1=YUE_TAG_OVERRIDES.get(word, tag),
                    pos2="stopword" if word in self._stop_words else "",
                    lemma=word,
                    kana="",
                )
            )
        return tokens

    def _segment_runs(self, text: str) -> list[Span]:
        """``segment`` over each maximal run between punctuation marks; each mark its own span."""
        spans: list[Span] = []
        run_start = 0
        for index, char in enumerate(text):
            if unicodedata.category(char).startswith("P"):
                spans += self._segment_run(text, run_start, index)
                spans.append((char, (index, index + 1)))
                run_start = index + 1
        return spans + self._segment_run(text, run_start, len(text))

    def _segment_run(self, text: str, start: int, end: int) -> list[Span]:
        return [(word, (start + s, start + e)) for word, (s, e) in self._segment(text[start:end], offsets=True)]

    def parse(self, text: str) -> list[LanguageToken]:
        """fugashi-compatible alias so ``LockedTagger.parse`` delegates cleanly."""
        return self(text)


def build_tagger() -> LockedTagger:
    """Build a lock-guarded Cantonese tokenizer.

    ``LockedTagger`` is reused verbatim from the ja stack: the segmenter and the
    tagger are lazily-loaded module-level singletons inside the Rust extension,
    which is the same hazard the ja lock already covers.
    """
    return LockedTagger(YueTagger())
