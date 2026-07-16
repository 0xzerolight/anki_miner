# Derived from Yomitan (https://github.com/yomidevs/yomitan),
# ext/js/language/language-transformer.js and ext/js/language/language-transforms.js,
# commit e2ed450c2f11a591922822e77f008e70a87daf0c.
#
# Copyright (C) 2024-2026  Yomitan Authors
# Copyright (C) 2026  anki_miner contributors (Python port)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Yomitan deinflection engine (Python port of ``LanguageTransformer``).

Faithful port of Yomitan's rule-driven deinflection BFS: each rule maps an
inflected suffix (or whole word) back toward dictionary form while chaining
grammatical-condition bitmasks (``conditions_match``), and per-path
``trace`` frames provide the cycle-detection termination exactly as
upstream. Deviations from upstream, both behavior-preserving:

- Suffix rules match via ``str.endswith`` instead of a ``RegExp`` — every
  upstream suffix is a literal kana string (asserted by the table's
  integrity test), so the regex machinery is unnecessary.
- Rules are bucketed by the final character of their inflected form in
  place of upstream's per-transform union-regex ``heuristic``; a rule can
  only match a text ending in that character, so the candidate rule set is
  identical.

``_MAX_RESULTS`` is a defensive backstop far above realistic BFS frontier
sizes (the longest real chains stay under a few hundred results); on
overflow the search stops expanding gracefully rather than raising.

Pure functions/objects, no I/O, no Qt. The Japanese rule table lives in
``japanese_transforms`` (same upstream commit).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Iterable, Mapping, Sequence

# Batch offline existence probe: the subset of the input strings attested as
# exact dictionary headwords (``DefinitionService.offline_terms_exist`` /
# ``compound_matcher.TermLookup``). Redeclared here (rather than imported) so
# this module stays free of the compound-matcher import edge.
TermLookup = Callable[[list[str]], set[str]]

_MAX_CONDITION_FLAGS = 32
_MAX_RESULTS = 4096

# Trace frame: (transform_id, rule_index, text-before-deinflection).
_TraceFrame = tuple[str, int, str]


@dataclass(frozen=True)
class Rule:
    """One deinflection rule (upstream ``suffixInflection``/``wholeWordInflection``)."""

    rule_type: str  # "suffix" | "wholeWord"
    inflected: str
    deinflected: str
    conditions_in: int
    conditions_out: int

    def matches(self, text: str) -> bool:
        if self.rule_type == "suffix":
            return text.endswith(self.inflected)
        return text == self.inflected

    def deinflect(self, text: str) -> str:
        if self.rule_type == "suffix":
            return text[: len(text) - len(self.inflected)] + self.deinflected
        return self.deinflected


@dataclass(frozen=True)
class TransformedText:
    """One BFS result: candidate dictionary form + grammatical conditions."""

    text: str
    conditions: int
    trace: tuple[_TraceFrame, ...]


def conditions_match(current_conditions: int, next_conditions: int) -> bool:
    """Upstream ``LanguageTransformer.conditionsMatch``: 0 is the wildcard."""
    return current_conditions == 0 or (current_conditions & next_conditions) != 0


def build_condition_flags(conditions: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    """Assign bit flags to condition types (port of ``_getConditionFlagsMap``).

    Leaf conditions (no ``subConditions``) get the next free bit; parent
    conditions OR their children's flags. Resolution iterates until fixed
    point; an unresolvable (cyclic) declaration raises, as upstream.
    """
    flags_map: dict[str, int] = {}
    next_flag_index = 0
    targets = list(conditions.items())
    while targets:
        deferred: list[tuple[str, Mapping[str, Any]]] = []
        for condition_type, condition in targets:
            sub_conditions = condition.get("subConditions")
            if sub_conditions is None:
                if next_flag_index >= _MAX_CONDITION_FLAGS:
                    raise ValueError("Maximum number of conditions was exceeded")
                flags = 1 << next_flag_index
                next_flag_index += 1
            else:
                resolved = _resolve_flags_strict(flags_map, sub_conditions)
                if resolved is None:
                    deferred.append((condition_type, condition))
                    continue
                flags = resolved
            flags_map[condition_type] = flags
        if len(deferred) == len(targets):
            # Cycle in subConditions declaration.
            raise ValueError("Maximum number of conditions was exceeded")
        targets = deferred
    return flags_map


def _resolve_flags_strict(flags_map: Mapping[str, int], condition_types: Iterable[str]) -> int | None:
    flags = 0
    for condition_type in condition_types:
        if condition_type not in flags_map:
            return None
        flags |= flags_map[condition_type]
    return flags


# unidic cType prefix → Yomitan dictionary-form condition name(s). The mined
# token's conjugation class gates which deinflection chains may claim it
# (e.g. った→う vs った→つ both exist; the lemma comparison disambiguates,
# the mask rejects cross-conjugation coincidences). A prefix may map to
# multiple names, ORed into one mask: unidic tags じる/ずる verbs (感じる,
# 信じる, 生じる) as サ行変格, but the transform rules that reach their 〜ずる
# orthBase carry vz, so サ行変格 must satisfy both vs and vz or the highlight
# stops at the stem (Bug J1).
_CTYPE_PREFIX_TO_CONDITION: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("五段", ("v5",)),
    ("上一段", ("v1",)),
    ("下一段", ("v1",)),
    ("サ行変格", ("vs", "vz")),
    ("カ行変格", ("vk",)),
    ("ザ行変格", ("vz",)),
    ("形容詞", ("adj-i",)),
)


class Deinflector:
    """Rule table + the ``transform`` BFS (port of ``LanguageTransformer``)."""

    def __init__(
        self,
        conditions: Mapping[str, Mapping[str, Any]],
        transforms: Sequence[Mapping[str, Any]],
    ) -> None:
        self._condition_flags = build_condition_flags(conditions)
        self._rules_by_last_char: dict[str, list[tuple[str, int, Rule]]] = {}
        self.transform_count = 0
        self.rule_count = 0
        for transform in transforms:
            transform_id = str(transform["id"])
            self.transform_count += 1
            for rule_index, raw_rule in enumerate(transform["rules"]):
                rule = Rule(
                    rule_type=str(raw_rule["type"]),
                    inflected=str(raw_rule["inflected"]),
                    deinflected=str(raw_rule["deinflected"]),
                    conditions_in=self._flags_strict(raw_rule["conditionsIn"], transform_id),
                    conditions_out=self._flags_strict(raw_rule["conditionsOut"], transform_id),
                )
                if not rule.inflected:
                    raise ValueError(f"Empty inflected form in transform {transform_id}")
                last_char = rule.inflected[-1]
                self._rules_by_last_char.setdefault(last_char, []).append((transform_id, rule_index, rule))
                self.rule_count += 1

    def _flags_strict(self, condition_types: Iterable[str], transform_id: str) -> int:
        flags = _resolve_flags_strict(self._condition_flags, condition_types)
        if flags is None:
            raise ValueError(f"Invalid conditions for transform {transform_id}")
        return flags

    def condition_flags(self, condition_type: str) -> int:
        """Bit flags for one condition name (0 when unknown)."""
        return self._condition_flags.get(condition_type, 0)

    def mask_for_ctype(self, ctype: object) -> int:
        """Condition mask for a unidic ``cType`` string; 0 = accept any.

        Guarded with ``isinstance`` rather than truthiness: MagicMock
        tokens auto-vivify truthy attribute values, so any non-``str``
        must be treated as absent.
        """
        if not isinstance(ctype, str):
            return 0
        for prefix, condition_names in _CTYPE_PREFIX_TO_CONDITION:
            if ctype.startswith(prefix):
                mask = 0
                for condition_name in condition_names:
                    mask |= self._condition_flags.get(condition_name, 0)
                return mask
        return 0

    def transform(self, source_text: str) -> list[TransformedText]:
        """All deinflection results for ``source_text`` (incl. the identity).

        Faithful port of upstream ``transform``: seed with conditions=0
        (wildcard), expand each result against every applicable rule,
        skip any (transform, rule, text) frame already on the path
        (``isCycle``). Result order differs from upstream (rule bucketing)
        but the result SET is identical; callers only test membership.
        """
        results = [TransformedText(source_text, 0, ())]
        index = 0
        while index < len(results):
            entry = results[index]
            index += 1
            text = entry.text
            if not text:
                continue
            if len(results) > _MAX_RESULTS:
                break
            for transform_id, rule_index, rule in self._rules_by_last_char.get(text[-1], ()):
                if not conditions_match(entry.conditions, rule.conditions_in):
                    continue
                if not rule.matches(text):
                    continue
                frame: _TraceFrame = (transform_id, rule_index, text)
                if frame in entry.trace:
                    continue  # cycle
                results.append(
                    TransformedText(
                        text=rule.deinflect(text),
                        conditions=rule.conditions_out,
                        trace=entry.trace + (frame,),
                    )
                )
        return results


# Window-stop bounds for find_highlight_end (candidate-COUNT bounds only;
# the deinflection validator is the correctness guarantee).
_EXTENDABLE_POS1 = ("動詞", "形容詞")
_INFLECTIONAL_TAIL_POS1 = frozenset({"助動詞", "助詞", "動詞", "形容詞"})
_WINDOW_CAP_CHARS = 13  # させられませんでした-class stacks are 10 kana; margin for …なかったです tails


def _is_pure_hiragana(text: str) -> bool:
    return bool(text) and all("ぁ" <= ch <= "ゟ" for ch in text)


def _str_or_none(value: object) -> str | None:
    # isinstance guard, NOT truthiness/getattr-default: MagicMock tokens
    # auto-vivify truthy attribute values.
    return value if isinstance(value, str) and value else None


def find_highlight_end(
    text: str,
    raw_tokens: list,
    tok_start: int,
    tok_end: int,
    token: Any,
) -> int:
    """End offset of the full inflected form starting at ``tok_start``.

    Thin end-only wrapper over :func:`find_highlight_end_with_trace`; see there
    for the full contract.
    """
    return find_highlight_end_with_trace(text, raw_tokens, tok_start, tok_end, token)[0]


def find_highlight_end_with_trace(
    text: str,
    raw_tokens: list,
    tok_start: int,
    tok_end: int,
    token: Any,
) -> tuple[int, tuple[str, ...]]:
    """Full-inflected-form end offset plus the accepted deinflection chain.

    Yomitan's ``originalTextLength`` mechanic adapted to a known lemma:
    extend the mined 動詞/形容詞 token's span over following raw tokens and
    accept the LONGEST candidate substring whose deinflection chain reaches
    the token's ``orthBase``/lemma under the cType condition mask. No valid
    chain (or any malformed input) ⇒ ``(tok_end, ())`` — today's stem-only span
    with an empty chain.

    The second element is the accepted result's transform-id chain in
    Yomitan *attachment order* (dictionary form outward): 食べませんでした →
    ``('-ます', 'negative', '-た')``. The BFS records frames surface-first
    (``entry.trace + (frame,)``); upstream ``_extendTrace`` prepends and
    ``translator.js`` maps ``trace → inflectionRules``, so the equivalent here
    is ``reversed(result.trace)`` (verified against
    ``japanese-transforms.test.js``). An empty tuple means no rightward
    extension was accepted (the surface token stands alone).

    Window stops (bounds only, never the correctness guarantee): a
    following raw token must be adjacent in ``text``, pure hiragana,
    inflectional POS (助動詞/助詞/動詞/形容詞 — a 名詞 like こと stops the
    window), and within ``tok_end + 13`` chars.
    """
    feature = getattr(token, "feature", None)
    if getattr(feature, "pos1", None) not in _EXTENDABLE_POS1:
        return tok_end, ()
    if not (0 <= tok_start < tok_end <= len(text)):
        return tok_end, ()

    targets = set()
    orth_base = _str_or_none(getattr(feature, "orthBase", None))
    if orth_base is not None:
        targets.add(orth_base)
    lemma = _str_or_none(_extract_lemma_safe(token))
    if lemma is not None:
        targets.add(lemma)
    if not targets:
        return tok_end, ()

    deinflector = get_japanese_deinflector()
    mask = deinflector.mask_for_ctype(getattr(feature, "cType", None))

    candidate_ends = _extension_candidate_ends(text, raw_tokens, tok_end)
    for candidate_end in reversed(candidate_ends):  # longest-first
        candidate = text[tok_start:candidate_end]
        for result in deinflector.transform(candidate):
            if result.text in targets and conditions_match_mask(result.conditions, mask):
                chain = tuple(frame[0] for frame in reversed(result.trace))
                return candidate_end, chain
    return tok_end, ()


def conditions_match_mask(result_conditions: int, target_mask: int) -> bool:
    """D2 acceptance gate: unknown cType (mask 0) accepts any chain; a known
    cType requires a result carrying that condition bit. No standalone
    ``result_conditions == 0`` acceptor — it would defeat the gate exactly
    in the terminal case."""
    return target_mask == 0 or (result_conditions & target_mask) != 0


def common_prefix_len(a: str, b: str) -> int:
    """Number of leading characters ``a`` and ``b`` share."""
    n = 0
    for ca, cb in zip(a, b, strict=False):  # stop at the shorter string
        if ca != cb:
            break
        n += 1
    return n


def resolve_dictionary_form(
    inflected_surface: str,
    orth_base: str,
    term_lookup: TermLookup | None,
) -> str:
    """Best modern JMdict-attested dictionary form for a 動詞/形容詞 card front.

    unidic tags the inflected stem of a じる/ずる verb (感じ, 論じ, 信じ) as
    サ行変格, so its ``orthBase`` is the archaic 〜ずる (感ずる) even though the
    modern, dictionary-attested headword is 〜じる (感じる). This deinflects the
    verb's actual inflected span and picks the modern headword by longest common
    prefix, falling back to ``orth_base`` whenever it cannot improve on it.

    Invariants (each hardened against an adversarial-review draft — do not relax):

    * **Candidates are the UNMASKED ``transform`` results.** The source token's
      cType mask is deliberately NOT applied: unidic tags 感じ サ変 (vs|vz) while
      the target 感じる carries v1, and ``v1 & (vs|vz) == 0`` — masking would
      delete the exact form the fix must pick. The cType mask stays only on the
      highlight-END computation (``find_highlight_end``), a separate concern.
    * **The inflected surface itself is discarded** from the candidate set. A
      deinflection never keeps the surface, and JMdict attests many inflected /
      stem strings (待った "matta!", 通じて, the noun 感じ) that would otherwise
      win the strictly-greater override and ship an inflected/stem card front.
    * **Gate on EXISTENCE, never ``entries.score``** — score is a uniform-0
      priority marker on the bundled jmdict-english, so a score gate would no-op
      the whole fix. ``term_lookup`` returns the attested subset (existence).
    * **Override only on a STRICTLY GREATER common prefix** than ``orth_base``'s
      own — self-limiting so a verb whose orthBase is already the longest prefix
      (乞う, 彷徨った, 帰れる, 立った) is kept unchanged.

    Safe degrade: ``term_lookup is None`` (no offline dictionary) or an empty
    input returns ``orth_base`` unchanged — the fix never hard-depends on a dict.
    """
    if term_lookup is None or not inflected_surface or not orth_base:
        return orth_base
    deinflector = get_japanese_deinflector()
    candidates = {result.text for result in deinflector.transform(inflected_surface)}
    candidates.discard(inflected_surface)
    if not candidates:
        return orth_base
    attested = term_lookup(sorted(candidates))
    if not attested:
        return orth_base
    # Rank: longest common prefix with the surface, then prefer the shorter
    # form, then lexicographic (a stable total order regardless of set order).
    winner = min(
        attested,
        key=lambda cand: (-common_prefix_len(cand, inflected_surface), len(cand), cand),
    )
    if common_prefix_len(winner, inflected_surface) > common_prefix_len(orth_base, inflected_surface):
        return winner
    return orth_base


# Ported from Yomitan (upstream e2ed450c2f11a591922822e77f008e70a87daf0c),
# ext/js/language/language-transformer.js
# ``LanguageTransformer.getConditionFlagsFromPartsOfSpeech`` + ``_getConditionFlags``.
# Maps a dictionary entry's stored ``rules`` string (term-bank ruleIdentifiers,
# e.g. "v5 vt" or "adj-i") to the OR of each name's condition-flag bitmask; an
# unknown name contributes 0, exactly as upstream. Used by the lookup-miss
# fallback's POS check (``translator.js`` ``_matchEntriesToDeinflections``): a
# deinflection hypothesis is kept for an entry only when its conditions match
# these flags. Behavior-preserving deviation: upstream restricts the lookup to
# the ``isDictionaryForm`` subset (``_partOfSpeechToConditionFlagsMap``); this
# reads the full condition-flag map. Term-bank ruleIdentifiers are always
# dictionary-form POS names, so the two agree on every real input, and any
# non-POS name still resolves to 0.
def condition_flags_from_rules(rules: str) -> int:
    """Bitmask for a space-separated ``entries.rules`` string; 0 when empty
    or when no name maps to a known condition."""
    deinflector = get_japanese_deinflector()
    flags = 0
    for name in rules.split():
        flags |= deinflector.condition_flags(name)
    return flags


def _extract_lemma_safe(token: Any) -> object:
    from anki_miner.services.morphology import extract_lemma

    try:
        return extract_lemma(token)
    except Exception:  # noqa: BLE001 — mock/duck-typed tokens degrade to no-extension
        return None


def _extension_candidate_ends(text: str, raw_tokens: list, tok_end: int) -> list[int]:
    """Ascending end offsets of contiguous inflectional-tail tokens after ``tok_end``."""
    from anki_miner.services.morphology import iter_token_spans

    ends: list[int] = []
    prev_end = tok_end
    try:
        for tail_token, start, end in iter_token_spans(text, raw_tokens):
            if end <= tok_end:
                continue
            if start != prev_end:
                if start > prev_end:
                    break  # whitespace gap / non-adjacent tail
                continue  # token overlapping the mined span; skip
            if end - tok_end > _WINDOW_CAP_CHARS:
                break
            surface = text[start:end]
            if not _is_pure_hiragana(surface):
                break
            tail_feature = getattr(tail_token, "feature", None)
            if getattr(tail_feature, "pos1", None) not in _INFLECTIONAL_TAIL_POS1:
                break
            ends.append(end)
            prev_end = end
    except (AttributeError, TypeError):
        return []  # mock/duck-typed token stream: no extension
    return ends


@lru_cache(maxsize=1)
def get_japanese_deinflector() -> Deinflector:
    """Process-wide ``Deinflector`` over the ported Yomitan Japanese table.

    Lazy import keeps the ~830-rule table off this module's import path;
    the build itself is a few milliseconds and happens once.
    """
    from anki_miner.services.japanese_transforms import CONDITIONS, TRANSFORMS

    return Deinflector(CONDITIONS, TRANSFORMS)
