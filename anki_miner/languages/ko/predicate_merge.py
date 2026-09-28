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

The same pass, under the same two rules, joins nouns kiwi cuts into a stem and a
noun-forming suffix (손 + 님/XSN, 대학 + 생/XSN) or into two nouns (창 + 문): a run
of a noun and the nouns or XSN suffixes attached to it becomes ONE noun token
when the joined surface is an exact headword, the longest attested run first.
Without it the XSN is dropped as a token and the card front is the stem - 손
"hand" for 손님 "guest" - and 창문 "window" makes two cards. 사람들 and 친구들 are
no headwords, so the stem stays what the line mines. The noun gets no 다: XSN
is not a predicate suffix. A predicate pair keeps its head: 방청소했어요 mines
청소하다, not a noun run 방 + 청소.

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

#: The class a merged noun run takes; KoreanMinedForm mines an NN as its surface.
_NOUN_POS1 = "NN"

#: The noun-forming suffix a noun run may take besides another noun (-님, -생, -들).
_NOUN_SUFFIX_POS2 = "XSN"

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


#: ``id(token) -> (start, end)`` in the source line, from ``iter_token_spans``.
_Spans = dict[int, tuple[int, int]]


def _attached(spans: _Spans, left: Any, right: Any) -> bool:
    """Whether ``right`` starts exactly where ``left`` ends in the source line.

    Adjacency is checked against the SOURCE spans, not the token order: a merge
    across whitespace would produce a surface that ``iter_token_spans`` stitches
    and then drops, silently losing the word.
    """
    left_span = spans.get(id(left))
    right_span = spans.get(id(right))
    return left_span is not None and right_span is not None and left_span[1] == right_span[0]


class KoreanPredicateMerger:
    """Merge attached nominal + suffix runs into one predicate or noun token.

    Stateless: the attestation probe is a per-call parameter, and the parser
    owns the memoisation. Greedy left to right; a token consumed by one merge
    is never part of another. Predicate pairs are settled first, so a noun run
    never takes the head of an attested predicate.
    """

    def merge_line(self, text: str, tokens: list, attest: AttestLookup) -> list:
        """Return ``tokens`` with every attested predicate pair and noun run merged.

        Both kinds of candidate go to ``attest`` in one batched call. Returns
        the input list object unchanged when the line offers no structural
        candidate, so a line with nothing to merge costs one scan and no
        dictionary lookup.
        """
        spans = {id(token): (start, end) for token, start, end in iter_token_spans(text, tokens)}
        pairs = self._structural_pairs(tokens, spans)
        runs = self._noun_runs(tokens, spans)
        if not pairs and not runs:
            return tokens
        attested = attest(sorted({candidate for _, candidate, _ in pairs} | {candidate for _, _, candidate in runs}))

        # start index -> (stop index, lemma, pos1, pos2)
        merges: dict[int, tuple[int, str, str, str]] = {
            index: (index + 2, candidate, pos1, "") for index, candidate, pos1 in pairs if candidate in attested
        }
        consumed = {index for head in merges for index in (head, head + 1)}
        cursor = 0
        for start, stop, candidate in runs:
            if start < cursor or candidate not in attested or not consumed.isdisjoint(range(start, stop)):
                continue
            merges[start] = (stop, candidate, _NOUN_POS1, _feature(tokens[start], "pos2"))
            cursor = stop
        if not merges:
            return tokens

        out: list = []
        index = 0
        total = len(tokens)
        while index < total:
            merge = merges.get(index)
            if merge is None:
                out.append(tokens[index])
                index += 1
                continue
            stop, candidate, pos1, pos2 = merge
            out.append(
                LanguageToken(
                    surface="".join(token.surface for token in tokens[index:stop]),
                    pos1=pos1,
                    pos2=pos2,
                    lemma=candidate,
                    kana="",
                )
            )
            index = stop
        return out

    def _structural_pairs(self, tokens: list, spans: _Spans) -> list[tuple[int, str, str]]:
        """``(head index, candidate dictionary form, merged pos1)`` per eligible pair.

        kiwi does not in fact tag a whitespace-separated 하 as XSV, so the
        source-adjacency check is a guard against that guarantee changing, not
        a live case.
        """
        pairs: list[tuple[int, str, str]] = []
        index = 0
        total = len(tokens)
        while index < total - 1:
            head, tail = tokens[index], tokens[index + 1]
            pos1 = _SUFFIX_TO_PREDICATE.get(_feature(tail, "pos2"))
            if pos1 is None or not self._is_head(head) or not _attached(spans, head, tail):
                index += 1
                continue
            stem = (_feature(head, "lemma") or head.surface) + (_feature(tail, "lemma") or tail.surface)
            candidate = stem if stem.endswith("다") else stem + "다"
            pairs.append((index, candidate, pos1))
            index += 2
        return pairs

    def _noun_runs(self, tokens: list, spans: _Spans) -> list[tuple[int, int, str]]:
        """``(start, stop, joined surface)`` for every noun run of two or more tokens.

        A run starts at a noun that may head a predicate (a bound noun may not)
        and extends over the source-attached nouns and XSN suffixes after it.
        Sorted by start, longest first, which is the order ``merge_line`` takes
        the first attested one in. The candidate is the joined SOURCE surface:
        a noun has no stem to restore, and the surface is what a noun mines as.
        """
        runs: list[tuple[int, int, str]] = []
        total = len(tokens)
        for start in range(total - 1):
            if _feature(tokens[start], "pos1") != _NOUN_POS1 or not self._is_head(tokens[start]):
                continue
            stop = start + 1
            while (
                stop < total and self._is_noun_tail(tokens[stop]) and _attached(spans, tokens[stop - 1], tokens[stop])
            ):
                stop += 1
            surfaces = [token.surface for token in tokens[start:stop]]
            runs.extend((start, end, "".join(surfaces[: end - start])) for end in range(stop, start + 1, -1))
        return runs

    @staticmethod
    def _is_head(token: Any) -> bool:
        if _feature(token, "pos1") not in _HEAD_POS1:
            return False
        return _feature(token, "pos2") not in _HEAD_EXCLUDED_POS2

    @staticmethod
    def _is_noun_tail(token: Any) -> bool:
        return _feature(token, "pos1") == _NOUN_POS1 or _feature(token, "pos2") == _NOUN_SUFFIX_POS2
