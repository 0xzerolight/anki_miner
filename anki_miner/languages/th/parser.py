"""th SubtitleParser factory (the profile's ``create_parser`` field).

The service is the shared ``SubtitleParserService`` -- nothing is subclassed.
The tokenizer arrives through ``tagger_provider.get_tagger(config.language)``.
The shared spaced factory fills every seam Thai needs; this one adds the split
pass (``ThaiDecompoundPass``).

``compound_matching=False`` (S7), as the zh and yue factories also pass: the
compound matcher greedily joins adjacent tokens against the
installed dictionary, and with no spaces to stop it a Thai line merges up to
five tokens into one attested-looking string. The spaced factory passes it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from anki_miner.languages.th.support import ThaiLookupStrategy
from anki_miner.languages.th.tokenizer import newmm_dictionary

if TYPE_CHECKING:
    from anki_miner.services.morphology import AttestLookup, FormLookup

_LADDER = ThaiLookupStrategy()


def _cuts(word: str) -> list[tuple[str, str]]:
    """Every cut of ``word`` into two words of newmm's dictionary, longest prefix first."""
    words = newmm_dictionary()
    return [
        (word[:end], word[end:]) for end in range(len(word) - 1, 0, -1) if word[:end] in words and word[end:] in words
    ]


class ThaiDecompoundPass:
    """``token_post_pass``: cut a mineable compound no dictionary defines into the two words it is made of.

    newmm keeps its longest match against ``thai_words()``, which holds compounds
    wty-th-en has no row for (ขับรถ, คิดถึงบ้าน, สถานีรถไฟ). Phase 2 then dropped
    the whole token for no definition although both halves are headwords: 16 of
    203 mined tokens over 75 everyday lines. Only a miss is touched -- a token
    neither attested itself nor through the lookup ladder, so จริงๆ (defined as
    จริง ๆ) stays whole -- and only a token that would be mined: an allowed POS,
    no tier, and not PROPN, because a name is not made of its syllables. The cut
    is at the longest attested prefix whose remainder is attested too; with no
    such cut the token stays. This is not plan D3's dictionary-headword Trie
    union, which re-cut every line and measured harmful: only misses change.

    Both parts must also be words of newmm's own dictionary. wty-th-en files
    every letter and vowel sign as a headword (เ, ษ, the combining ิ), and
    attestation alone cut ลิ into ล + ิ and เปน into เ + ปน: 440 of 2,202 cuts over
    4,560 real sentences.

    The re-segmented line is re-tagged in one ``pos_tag`` call through the
    lock-guarded tagger, so each part is tagged in sentence context, takes its
    tier, and then meets the ordinary POS and stopword gates. Two probe calls per
    line at most. ``attest is None`` (no offline dictionary) makes the pass
    inert. The third argument (R36's form lookup) is ignored.
    """

    def __init__(self, tagger: Callable[..., list[Any]], allowed_pos: Iterable[str]) -> None:
        self._tagger = tagger
        self._allowed_pos = frozenset(allowed_pos) - {"PROPN"}

    def _splittable(self, token: Any) -> bool:
        return len(token.surface) > 1 and token.feature.pos1 in self._allowed_pos and not token.feature.pos2

    def __call__(self, tokens: list[Any], attest: AttestLookup | None, forms: FormLookup | None) -> list[Any]:
        del forms
        if attest is None:
            return tokens
        suspects = list(dict.fromkeys(token.surface for token in tokens if self._splittable(token)))
        if not suspects:
            return tokens
        probes = {word: [word, *(term for term, _mask in _LADDER.candidates(word, word, None))] for word in suspects}
        found = attest(list(dict.fromkeys(term for terms in probes.values() for term in terms)))
        misses = [word for word in suspects if found.isdisjoint(probes[word])]
        if not misses:
            return tokens
        cuts = {word: _cuts(word) for word in misses}
        attested = attest(list(dict.fromkeys(part for word in misses for cut in cuts[word] for part in cut)))
        splits = {
            word: cut for word in misses if (cut := next((c for c in cuts[word] if attested.issuperset(c)), None))
        }
        if not splits:
            return tokens
        segments: list[str] = []
        for token in tokens:
            cut = splits.get(token.surface) if self._splittable(token) else None
            segments += cut or [token.surface]
        return self._tagger("".join(segments), segments=segments)


def create_parser(config: Any, **kwargs: Any) -> Any:
    """Build the Thai SubtitleParser for ``config``."""
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages.registry import get_profile
    from anki_miner.languages.tagger_provider import get_tagger

    # The cached tagger the parser itself tokenizes with, fetched when the pass
    # re-tags: a parser that only reads lines (create_line_parser) never loads it.
    kwargs.setdefault(
        "token_post_pass", ThaiDecompoundPass(lambda text, **kw: get_tagger("th")(text, **kw), config.allowed_pos)
    )
    # Bilingual cues put an English translation line under the native one, and the
    # flattened cue becomes the card's Sentence (ZH-046, KO-06): the script gate drops it.
    kwargs.setdefault("has_target_script", get_profile(config.language).script.contains_target_script)
    return create_spaced_parser(config, **kwargs)
