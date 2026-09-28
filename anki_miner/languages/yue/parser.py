"""yue SubtitleParser factory (the profile's ``create_parser`` field).

The service is the shared ``SubtitleParserService`` -- nothing is subclassed.
The tokenizer arrives through ``tagger_provider.get_tagger(config.language)``.
The shared spaced factory fills the seams below; this one adds the ellipsis guard
and the split pass (``YueDecompoundPass``).

``script_gate`` is the one seam this factory closes that zh leaves open: in
sentence context the tagger gives Latin and digit runs ordinary tags (measured:
``Netflix`` NOUN, ``2024`` NOUN, ``8`` NOUN), so POS cannot exclude them and the
Han gate has to. zh does not need this because jieba tags Latin ``eng``/``x``,
outside its allowed set.

``compound_matching=False`` (S7) -- the matcher greedily joins adjacent tokens
against the installed dictionary, and Cantonese has no spaces to stop it. With
CC-CEDICT-Canto carrying 163,280 mostly written-Chinese headwords, leaving it on
turns the top risk of this language (written Chinese under Cantonese dialogue)
into a mining behaviour. th closes it for the same reason.
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING, Any

from anki_miner.utils.ja_normalize import is_cjk_ideograph

if TYPE_CHECKING:
    from collections.abc import Callable

    from anki_miner.languages.yue.tokenizer import Span
    from anki_miner.services.morphology import AttestLookup, FormLookup


def _splittable(token: Any) -> bool:
    """Two or more characters, one of them Han, and none of the three exclusions below."""
    surface = token.surface
    return (
        len(surface) > 1
        # Joined across a space (今 日): the lemma's pieces are not slices of the surface.
        and surface == token.feature.lemma
        # A name is not made of its characters.
        and token.feature.pos1 != "PROPN"
        # 三杯 -> 三 杯 would card the classifier.
        and unicodedata.numeric(surface[0], None) is None
        and any(is_cjk_ideograph(char) for char in surface)
    )


def _longest_match(word: str, attested: set[str]) -> list[str] | None:
    """``word`` cut left to right into its longest attested pieces; None when one is not attested."""
    pieces: list[str] = []
    start = 0
    while start < len(word):
        end = next((end for end in range(len(word), start, -1) if word[start:end] in attested), None)
        if end is None:
            return None
        pieces.append(word[start:end])
        start = end
    return pieces


class YueDecompoundPass:
    """``token_post_pass``: split a glued token that misses every dictionary into the words it is made of.

    The segmenter glues adverbs, aspect markers, particles and pronouns onto the
    word next to them (好忙, 瞓緊覺, 煮啦, 佢住). The glued token is no headword,
    so the word inside it never became a card. A splittable token that the probe
    misses is cut by longest match, and only when the probe attests EVERY piece.
    A separable verb-object compound (拍緊拖) comes apart into its literal pieces
    too; that is accepted.

    The re-segmented line is re-tagged in one ``pos_tag`` call through the
    lock-guarded tagger, so each piece is tagged in sentence context and
    ``YUE_TAG_OVERRIDES`` applies (a freed 緊 or 啦 is PART). Only the pieces
    take those tags: every token the pass did not split is returned as it came,
    with its first tag. Two probe calls per line at most: the suspects, then
    every inner substring of the misses.
    ``attest is None`` (no offline dictionary) makes the pass inert. The third
    argument (R36's form lookup) is ignored.
    """

    def __init__(self, tagger: Callable[..., list[Any]]) -> None:
        self._tagger = tagger

    def __call__(self, tokens: list[Any], attest: AttestLookup | None, forms: FormLookup | None) -> list[Any]:
        del forms
        if attest is None:
            return tokens
        suspects = list(dict.fromkeys(token.surface for token in tokens if _splittable(token)))
        if not suspects:
            return tokens
        misses = set(suspects) - attest(suspects)
        if not misses:
            return tokens
        inner = [word[i:j] for word in misses for i in range(len(word)) for j in range(i + 1, len(word) + 1)]
        attested = attest(list(dict.fromkeys(inner)))
        splits = {word: pieces for word in misses if (pieces := _longest_match(word, attested))}
        if not splits:
            return tokens
        cuts = [splits.get(token.surface) if _splittable(token) else None for token in tokens]
        # Offsets into the surfaces joined: a split surface equals its lemma, so
        # its pieces are consecutive slices of it.
        spans: list[Span] = []
        cursor = 0
        for token, pieces in zip(tokens, cuts, strict=True):
            if pieces is None:
                spans.append((token.feature.lemma, (cursor, cursor + len(token.surface))))
                cursor += len(token.surface)
                continue
            for piece in pieces:
                spans.append((piece, (cursor, cursor + len(piece))))
                cursor += len(piece)
        # One tagger token per span. Only the pieces take the new tags: re-tagged,
        # an untouched neighbour can change class (起床 VERB -> PART) and lose its card.
        retagged = iter(self._tagger("".join(token.surface for token in tokens), spans=spans))
        out: list[Any] = []
        for token, pieces in zip(tokens, cuts, strict=True):
            if pieces is None:
                next(retagged)
                out.append(token)
            else:
                out += [next(retagged) for _piece in pieces]
        return out


def create_parser(config: Any, **kwargs: Any) -> Any:
    """Build the Cantonese SubtitleParser for ``config``."""
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages.tagger_provider import get_tagger

    # Cantonese is as single-character-dense as Mandarin: see zh/parser.py.
    kwargs.setdefault("ellipsis_fragment_guard", False)
    # The cached tagger the parser itself tokenizes with.
    kwargs.setdefault("token_post_pass", YueDecompoundPass(get_tagger("yue")))
    return create_spaced_parser(config, **kwargs)
