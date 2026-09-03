"""Korean noun/root + predicate-suffix merging (공부 + 하 -> 공부하다).

kiwipiepy analyses a 하다-predicate as two tokens: the nominal that carries the
meaning (공부/NNG, 깨끗/XR) and the predicate-forming suffix that makes it a verb
or adjective (하/XSV, 하/XSA, 되/XSV, 롭/XSA-I). XS is not in KO_ALLOWED_POS - it
shares a class with the noun-forming XSN (-님, -들) and a bare 하 is not a word -
so without this pass the suffix is dropped and the card front is the bare
nominal: 공부 for a sentence meaning "is studying", and 깨끗 (a BOUND ROOT that
no dictionary lists) for 깨끗한.

This pass merges the pair into one predicate token whose lemma is the dictionary
form, so ``KoreanMinedForm`` - which returns the lemma for VV/VA - puts 공부하다
on the card. Everything downstream keys on that: the curation dialog's mined-form
column, the known-words and Anki-duplicate checks, the definition lookup.

Two rules keep it honest:

* **The suffix must be attached in the source.** kiwi tags the 하 of 공부하고 as
  XSV and the free-standing 하 of "공부를 했어요" (or "공부 하고") as VV, so the
  structural gate alone distinguishes "the subtitle wrote the verb" from "the
  subtitle wrote a noun and the verb 하다". Bare 공부 in 공부 시간 is left alone -
  it is a real dictionary noun and it is what the speaker said.
* **The merged form must be an exact dictionary headword.** The probe is injected
  (``attest``), so this module stays SQLite-free, and the parser's memoised
  ``offline_terms_exist`` answers each distinct string once per run. With no
  offline dictionary wired the pass never runs at all and output is
  byte-identical to the pre-merge behaviour.

The candidate is built generically as ``head lemma + suffix lemma + 다`` - no
suffix table. That is correct for every suffix kiwi emits here (하 -> 공부하다,
되 -> 시작되다, 시키 -> 교육시키다, 스럽 -> 사랑스럽다, 롭 -> 자유롭다), because
the LEMMA carries the regular stem while the SOURCE SLICE carries the contracted
or irregular spelling (자유 + 롭 -> 자유롭다, surface 자유로운).

The output token is a ``LanguageToken``, never a ``SyntheticToken``: the two
``isinstance(t, SyntheticToken)`` gates in ``services/morphology.py`` drive
Japanese-only attested-reading and span-replacement passes that a Korean token
must never enter.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from anki_miner.languages.token import LanguageToken
from anki_miner.services.morphology import iter_token_spans

#: Batch exact-headword existence probe (``DefinitionService.offline_terms_exist``).
AttestLookup = Callable[[list[str]], set[str]]

#: Predicate-forming suffix tags -> the Sejong class the merged token takes.
#: XSN (noun-forming: -님, -들) is absent on purpose: 선생님 is not a predicate.
#: Both targets are in KO_ALLOWED_POS and in PREDICATE_TAGS, so the merged token
#: is mineable and mines as its lemma.
_SUFFIX_TO_PREDICATE: dict[str, str] = {"XSV": "VV", "XSA": "VA"}

#: Classes a predicate suffix may attach to: nouns and bound roots. XR is the
#: important one - a bound root is not a word by itself, so merging is the only
#: way 깨끗한 ever produces a card.
_HEAD_POS1 = frozenset({"NN", "XR"})

#: Bound nouns (것/수/개/명) are grammar scaffolding, excluded as heads for the
#: same reason KO_EXCLUDED_SUBTYPES excludes them from mining.
_HEAD_EXCLUDED_POS2 = frozenset({"NNB"})


def _feature(token: Any, name: str) -> str:
    value = getattr(token.feature, name, "")
    return str(value) if value else ""


class KoreanPredicateMerger:
    """Merge attached nominal + predicate-suffix pairs into one predicate token.

    Stateless: the attestation probe is a per-call parameter, and the parser
    owns the memoisation. Greedy left to right; a token consumed as a tail can
    never also start a pair.
    """

    def merge_line(self, text: str, tokens: list, attest: AttestLookup) -> list:
        """Return ``tokens`` with every attested nominal+suffix pair merged.

        Returns the input list object unchanged when the line offers no
        structural candidate, so a line with nothing to merge costs one scan
        and no dictionary lookup.
        """
        pairs = self._structural_pairs(text, tokens)
        if not pairs:
            return tokens
        attested = attest(sorted({candidate for _, candidate, _ in pairs}))
        firing = {index: (candidate, pos1) for index, candidate, pos1 in pairs if candidate in attested}
        if not firing:
            return tokens

        out: list = []
        index = 0
        total = len(tokens)
        while index < total:
            fired = firing.get(index)
            if fired is None:
                out.append(tokens[index])
                index += 1
                continue
            candidate, pos1 = fired
            head, tail = tokens[index], tokens[index + 1]
            out.append(
                LanguageToken(
                    surface=head.surface + tail.surface,
                    pos1=pos1,
                    pos2="",
                    lemma=candidate,
                    kana="",
                )
            )
            index += 2
        return out

    def _structural_pairs(self, text: str, tokens: list) -> list[tuple[int, str, str]]:
        """``(head index, candidate dictionary form, merged pos1)`` per eligible pair.

        Adjacency is checked against the SOURCE spans, not the token order: a
        pair separated by whitespace would produce a surface that
        ``iter_token_spans`` stitches and then drops, silently losing the word.
        kiwi does not in fact tag a whitespace-separated 하 as XSV, so this is a
        guard against that guarantee changing, not a live case.
        """
        spans = {id(token): (start, end) for token, start, end in iter_token_spans(text, tokens)}
        pairs: list[tuple[int, str, str]] = []
        index = 0
        total = len(tokens)
        while index < total - 1:
            head, tail = tokens[index], tokens[index + 1]
            pos1 = _SUFFIX_TO_PREDICATE.get(_feature(tail, "pos2"))
            if pos1 is None or not self._is_head(head):
                index += 1
                continue
            head_span = spans.get(id(head))
            tail_span = spans.get(id(tail))
            if head_span is None or tail_span is None or head_span[1] != tail_span[0]:
                index += 1
                continue
            stem = (_feature(head, "lemma") or head.surface) + (_feature(tail, "lemma") or tail.surface)
            candidate = stem if stem.endswith("다") else stem + "다"
            pairs.append((index, candidate, pos1))
            index += 2
        return pairs

    @staticmethod
    def _is_head(token: Any) -> bool:
        if _feature(token, "pos1") not in _HEAD_POS1:
            return False
        return _feature(token, "pos2") not in _HEAD_EXCLUDED_POS2
