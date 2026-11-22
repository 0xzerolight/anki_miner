# Derived from Yomitan (https://github.com/yomidevs/yomitan),
# ext/js/language/language-transformer.js and ext/js/language/language-transforms.js,
# commit e2ed450c2f11a591922822e77f008e70a87daf0c.
#
# Copyright (C) 2024-2026  Yomitan Authors
# Ported to Python in 2026.
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
from typing import Any, Callable, Iterable, Iterator, Mapping, NamedTuple, Sequence

from anki_miner.utils.text_utils import katakana_to_hiragana

# Rules-aware offline attestation probe. Each tuple preserves the terminal
# deinflection condition mask beside its candidate text; the returned set keeps
# only terms with at least one dictionary entry whose POS rules accept that mask.
TermRulesLookup = Callable[[list[tuple[str, int]]], set[str]]

# Batch offline commonness probe (``DefinitionService.offline_term_commonness``):
# maps each input string to whether a commonness-AWARE offline dict tags it common
# (jitendex 'popular'/'frequent'). ``None`` iff NO available offline provider is
# commonness-aware — the caller then keeps pre-commonness behavior byte-identical.
TermCommonLookup = Callable[[list[str]], dict[str, bool] | None]

_MAX_CONDITION_FLAGS = 32
_MAX_RESULTS = 4096
# Texts whose ``transform`` results one Deinflector keeps; the memo is emptied when full.
_TRANSFORM_MEMO_SIZE = 16384

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
        self._transform_memo: dict[str, tuple[TransformedText, ...]] = {}
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

        The rules never change after ``__init__``, so the results of a text are
        memoized (``_TRANSFORM_MEMO_SIZE``); each call gets its own list.
        """
        cached = self._transform_memo.get(source_text)
        if cached is None:
            if len(self._transform_memo) >= _TRANSFORM_MEMO_SIZE:
                self._transform_memo.clear()
            cached = self._transform_memo[source_text] = tuple(self._transform_uncached(source_text))
        return list(cached)

    def _transform_uncached(self, source_text: str) -> list[TransformedText]:
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
# 形状詞 and 接尾辞 carry the appearance そう and the nominal さ/がる (動き|そう,
# 温か|さ, 恥ずかし|がら|ない), which the table deinflects as -そう, -さ and -がる.
_INFLECTIONAL_TAIL_POS1 = frozenset({"助動詞", "助詞", "動詞", "形容詞", "形状詞", "接尾辞"})
# 食べさせられたくありませんでした has a supported 14-char tail after 食べ.
_WINDOW_CAP_CHARS = 14


def _is_pure_hiragana(text: str) -> bool:
    return bool(text) and all("ぁ" <= ch <= "ゟ" for ch in text)


def _str_or_none(value: object) -> str | None:
    # isinstance guard, NOT truthiness/getattr-default: MagicMock tokens
    # auto-vivify truthy attribute values.
    return value if isinstance(value, str) and value else None


class _ChainEnd(NamedTuple):
    """One place the chain after a mined token may end."""

    end: int
    # The text between the token and ``end`` as the table reads it.
    tail: str
    # The last tail token is the sentence-final particle よ, or an imperative that ends in it (とけよ).
    final_yo: bool = False
    # A tail token is the particle か.
    particle_ka: bool = False
    # The character right after ``end`` ("" at the end of the line).
    following: str = ""


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
    additional_target: str | None = None,
    chain_end: bool = False,
) -> tuple[int, tuple[str, ...]]:
    """Full-inflected-form end offset plus the accepted deinflection chain.

    Yomitan's ``originalTextLength`` mechanic adapted to a known lemma:
    extend the mined 動詞/形容詞 token's span over following raw tokens and
    accept the LONGEST candidate substring whose deinflection chain reaches
    the token's ``orthBase``/lemma under the cType condition mask, or an optional
    dictionary-attested ``additional_target`` under its compatible mask. A
    resolved modern 〜じる target uses its dictionary ``v1`` mask instead of the
    source token's サ変 mask. No valid chain (or any malformed input) ⇒
    ``(tok_end, ())`` — today's stem-only span with an empty chain.

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
    inflectional POS (助動詞/助詞/動詞/形容詞/形状詞/接尾辞 — a 名詞 like こと stops the
    window), and within ``tok_end + _WINDOW_CAP_CHARS`` chars.

    That end is what the card front is resolved from. ``chain_end`` asks for
    the end a reader sees instead (the highlight): the candidates come from
    ``_chain_end_candidates``, and ``_chain_end_gate`` may refuse an end the
    table accepts, in which case the next shorter one is tried.
    """
    feature = getattr(token, "feature", None)
    # すみません|でした: an apology the dictionary lists as an interjection has a
    # past like the verb it once was. The resolver takes that でした and nothing
    # else after such a word.
    polite_expression = (
        chain_end and getattr(feature, "pos1", None) == "感動詞" and text[tok_start:tok_end].endswith("ません")
    )
    if getattr(feature, "pos1", None) not in _EXTENDABLE_POS1 and not polite_expression:
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

    deinflector = get_highlight_deinflector()
    # The resolver asks the highlight table first: what that accepts the END
    # table accepts too, and the pass that found the front's end has memoized
    # its results for these texts.
    tables = (deinflector, get_chain_end_deinflector()) if chain_end else (deinflector,)
    mask = deinflector.mask_for_ctype(getattr(feature, "cType", None))
    additional_mask = mask
    if (
        additional_target is not None
        and additional_target.endswith("じる")
        and f"{additional_target[:-2]}ずる" in targets
    ):
        # UniDic tags the source stem サ変, but the dictionary-attested modern
        # front is ichidan. Keep the original targets on vs|vz; only the resolved
        # じる identity validates against v1.
        additional_mask = deinflector.condition_flags("v1")

    if chain_end:
        candidates = _chain_end_candidates(text, raw_tokens, tok_start, tok_end)
    else:
        candidates = [_ChainEnd(end, text[tok_end:end]) for end in _extension_candidate_ends(text, raw_tokens, tok_end)]
    if polite_expression:
        candidates = [end for end in candidates if end.tail == "でした" and end.end == tok_end + 3]
    # A stem written in katakana (ヤラ|れ|た, orthBase ヤル) inflects as its
    # hiragana spelling does, and the table's rules are hiragana.
    stem = text[tok_start:tok_end]
    kana_stem = katakana_to_hiragana(stem)
    kana_targets = {katakana_to_hiragana(target) for target in targets} if kana_stem != stem else set()
    floor = tok_end - tok_start
    for candidate in reversed(candidates):  # longest-first
        # A stretched tail (歩い|たぁ, 食べ|ねー) is the chain of its plain spelling.
        for spelling in _unstretched_forms(stem + candidate.tail, floor):
            chain = None
            for table in tables:
                chain = _reaching_chain(table, spelling, targets, mask, additional_target, additional_mask)
                if chain is None and kana_targets:
                    chain = _reaching_chain(table, kana_stem + spelling[floor:], kana_targets, mask, None, 0)
                if chain is not None:
                    break
            if chain is None:
                continue
            if chain_end and _chain_end_gate(candidate, chain, getattr(feature, "pos1", None)):
                break  # refused: the next shorter end is tried
            return candidate.end, chain
    return tok_end, ()


def _reaching_chain(
    deinflector: Deinflector,
    spelling: str,
    targets: set[str],
    mask: int,
    additional_target: str | None,
    additional_mask: int,
) -> tuple[str, ...] | None:
    """The transform chain by which ``spelling`` deinflects to a target, ``None`` when it reaches none."""
    for result in deinflector.transform(spelling):
        base_matches = result.text in targets and conditions_match_mask(result.conditions, mask)
        additional_matches = (
            additional_target is not None
            and result.text == additional_target
            and conditions_match_mask(result.conditions, additional_mask)
        )
        if base_matches or additional_matches:
            return tuple(frame[0] for frame in reversed(result.trace))
    return None


# Marks that lengthen the word written before them: a long mark, a small vowel
# (撃てぇ, 歩いたぁ), a wave dash after kana, and an emphatic っ that ends the word
# (写るーっ). They are part of that word's span, never a word of their own.
_STRETCH_MARKS = frozenset("ーぁぃぅぇぉァィゥェォ")
_WAVE_DASHES = frozenset("〜～")
_EMPHATIC_SOKUON = frozenset("っッ")
_E_ROW_KANA = frozenset("えけげせぜてでねへべぺめれ")


def _is_kana(ch: str) -> bool:
    return "ぁ" <= ch <= "ゟ" or "ァ" <= ch <= "ヿ"


def stretched_highlight_end(text: str, end: int) -> int:
    """``end`` moved past the stretch marks written right after a mined word.

    潜るー, 逃げ切ったぁーー and 撃てぇっ! are one word each to a reader, so the
    highlight covers the marks. A wave dash counts only after kana (10時〜12時 is
    a range), and a っ only when no kana follows it: 潜るって keeps the quoting
    って, and 潜るっす its copula.
    """
    if not 0 < end <= len(text):
        return end
    while end < len(text) and (text[end] in _STRETCH_MARKS or (text[end] in _WAVE_DASHES and _is_kana(text[end - 1]))):
        end += 1
    sokuon_end = end
    while sokuon_end < len(text) and text[sokuon_end] in _EMPHATIC_SOKUON:
        sokuon_end += 1
    if sokuon_end == len(text) or not _is_kana(text[sokuon_end]):
        return sokuon_end
    return end


def _unstretched_forms(surface: str, floor: int) -> Iterator[str]:
    """``surface``, then its spellings with the trailing stretch marks undone.

    The marks come off one at a time, never into the first ``floor`` characters
    (the mined token itself: 行っ keeps its っ). A long mark after an -え ending
    is first read as that vowel, since the table knows ねえ and てえ, not ねー.
    """
    yield surface
    form = surface
    while len(form) > max(floor, 1) and (
        form[-1] in _STRETCH_MARKS or form[-1] in _WAVE_DASHES or form[-1] in _EMPHATIC_SOKUON
    ):
        if form[-1] == "ー" and form[-2] in _E_ROW_KANA:
            yield form[:-1] + "え"
        form = form[:-1]
        yield form


def chain_spelling(surface: str, floor: int) -> str:
    """The spelling of a highlighted span that the deinflection table can undo.

    A span that ends in stretch marks (感じたぁー) deinflects as its plain
    spelling (感じた). An ending the table itself knows (感じねぇ) is kept, and
    so is a span with no such marks after its first ``floor`` characters.
    """
    if len(surface) <= max(floor, 1) or surface == next(reversed(list(_unstretched_forms(surface, floor)))):
        return surface
    deinflector = get_highlight_deinflector()
    form = surface
    for form in _unstretched_forms(surface, floor):
        if any(result.text != form for result in deinflector.transform(form)):
            return form
    return form


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


def _adjective_garu_verb(orth_base: str, candidates: Iterable[tuple[str, int]]) -> str | None:
    """The がる verb of the adjective *orth_base* when the span deinflects to it (強がって → 強がる), else ``None``."""
    if len(orth_base) < 2 or not orth_base.endswith("い"):
        return None
    verb = orth_base[:-1] + "がる"
    return verb if any(text == verb for text, _conditions in candidates) else None


def resolve_dictionary_form(
    inflected_surface: str,
    orth_base: str,
    term_rules_lookup: TermRulesLookup | None,
    term_common_lookup: TermCommonLookup | None = None,
    excluded: str = "",
    kana_lookup: Callable[[list[str]], dict[str, bool]] | None = None,
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
    * **The inflected surface competes only as a verb headword.** A
      deinflection never keeps the surface, and JMdict attests many inflected /
      stem strings (待った "matta!", 通じて, the noun 感じ) that would otherwise
      win the strictly-greater override and ship an inflected/stem card front.
      The surface is offered under the verb condition mask alone, so only a
      dictionary-form verb entry can win with it (知らせる, whose plain form
      has no deinflection left to reach it).
    * **Preserve each result's grammatical conditions through attestation.** A
      longer-prefix candidate is eligible only when a dictionary entry's stored
      POS rules accept its deinflection mask. This is the same gate Yomitan uses
      and prevents an attested adverb such as 決まって from beating the verb
      決まる. A rule-less row matches only a zero-condition spelling variant,
      not a non-zero deinflection hypothesis.
    * **Commonness-filter the override pool** (``term_common_lookup``, U11). Bare
      existence lets an archaic/rare LONGER-prefix deinflection outrank the
      orthBase and ship junk: 呼ばれる deinflects to both 呼ぶ AND the classical
      呼ばる (both JMdict-attested), and 呼ばる shares the longer prefix. When a
      commonness-aware offline dict is present, the pool is narrowed to the
      candidates it tags common (jitendex 'popular'/'frequent'), so 呼ばる (rare,
      untagged) drops out and 呼ぶ's own-prefix rule keeps the orthBase. An
      all-uncommon attested set (a rare-but-correct じる verb 詠じる/散じる whose
      only headword is untagged) falls back to the FULL attested set so its
      strictly-greater override still fires — the filter only prunes, never
      empties. ``term_common_lookup is None`` OR its returning ``None`` (no aware
      dict) leaves the pool = the full attested set: pre-commonness behavior,
      byte-identical.
    * **Override only on a STRICTLY GREATER common prefix** than ``orth_base``'s
      own — self-limiting so a verb whose orthBase is already the longest prefix
      (乞う, 彷徨った, 帰れる, 立った) is kept unchanged.

    * **The がる verb of the token's own adjective always competes** (強がって →
      強がる, not 強い). It is a derived verb with its own entry and meaning, not
      a rare twin, so the commonness filter does not prune it. A kana one
      (かわいがる) is only a reading of its entry (可愛がる), which the term probe
      cannot see: ``kana_lookup``, the term-or-reading probe, attests it.

    ``excluded`` is one candidate the caller knows is wrong for this token (the
    -aru verb 刺さる for the tagger's passive 刺さ|れ|た); it never competes.

    Safe degrade: ``term_rules_lookup is None`` (no rules-aware offline
    dictionary path) or an empty input returns ``orth_base`` unchanged — the fix
    never hard-depends on a dict.
    """
    if term_rules_lookup is None or not inflected_surface or not orth_base:
        return orth_base
    deinflector = get_highlight_deinflector()
    candidates = {
        (result.text, result.conditions)
        for result in deinflector.transform(inflected_surface)
        if result.text != inflected_surface and result.text != excluded
    }
    # The surface competes too, but only as a verb headword: 知らせる tokenizes
    # 知ら+せる (orthBase 知る) and has nothing left to deinflect, while 知らせた
    # reaches 知らせる through the past-tense rule. The verb mask keeps out
    # entries that are not verbs (待った is an interjection, 感じ a noun,
    # 変わらない an adjective); the commonness pool below then treats the
    # surface like any other candidate.
    candidates.add((inflected_surface, deinflector.condition_flags("v")))
    attested = term_rules_lookup(sorted(candidates))
    garu = _adjective_garu_verb(orth_base, candidates)
    kana_garu = garu is not None and garu not in attested and all("ぁ" <= ch <= "ゖ" for ch in garu)
    if garu is not None and kana_garu and kana_lookup is not None and kana_lookup([garu]).get(garu):
        attested = {*attested, garu}
    if not attested:
        return orth_base
    attested_sorted = sorted(attested)
    # Commonness filter: keep only common candidates in the override pool, but
    # never empty it (all-uncommon → the full attested set). None (no aware dict)
    # ⇒ the full attested set, pre-commonness behavior.
    common = term_common_lookup(attested_sorted) if term_common_lookup is not None else None
    if common is not None:
        pool: list[str] = [c for c in attested_sorted if common.get(c) or c == garu] or attested_sorted
    else:
        pool = attested_sorted
    # Rank: longest common prefix with the surface, then prefer the shorter
    # form, then lexicographic (a stable total order regardless of set order).
    winner = min(
        pool,
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
            tail_feature = getattr(tail_token, "feature", None)
            # An auxiliary written with a long mark (食べ|ねー) is still the chain;
            # a particle so written (待って|ねー) is not.
            if getattr(tail_feature, "pos1", None) == "助動詞":
                surface = surface.rstrip("ー")
            if not _is_pure_hiragana(surface):
                break
            if getattr(tail_feature, "pos1", None) not in _INFLECTIONAL_TAIL_POS1:
                break
            ends.append(end)
            prev_end = end
    except (AttributeError, TypeError):
        return []  # mock/duck-typed token stream: no extension
    return ends


# Auxiliaries the table lists in kanji too (-ほしい, -もらう, -過ぎる), by the
# kanji stem of their token: 教えて|欲しい, 信じて|貰え|なかった, 買い|過ぎ|た. Not
# 来る and 行く: after て the kanji is as often the motion verb itself (歩いて行ける
# 所), and three real words in forty were lost when they were let in. Not 上げる
# and 呉れる: 旗を持って上げた raises the flag.
_KANJI_AUXILIARY_STEMS = ("欲し", "貰", "過ぎ")


def _is_kanji_auxiliary(surface: str, pos1: object) -> bool:
    """Whether a tail token is one of ``_KANJI_AUXILIARY_STEMS`` with a kana ending."""
    if pos1 not in _EXTENDABLE_POS1:
        return False
    for stem in _KANJI_AUXILIARY_STEMS:
        if surface.startswith(stem):
            return surface == stem or _is_pure_hiragana(surface[len(stem) :])
    return False


# Words a quoting って leads to: 笑ったって|こと, 言ったって|話だ, 来たって|言う. The
# concessive たって has none of them after it (笑ったって無駄だ, 行ったっていい).
_QUOTATION_HEADS = frozenset({"こと", "事", "いう", "話", "わけ", "訳", "感じ", "の", "ん", "噂"})
_QUOTATION_VERB_STEMS = ("言", "思", "聞")


def _takes_quotation(text: str, following: tuple[Any, int, int] | None) -> bool:
    """Whether the token after a たって / だって is a word that takes a quotation."""
    if following is None:
        return False
    _token, start, end = following
    head = text[start:end]
    if head == "話" and text[end : end + 1] == "に":
        return False  # 泣いたって話にならない: the concessive before 話にならない
    return head in _QUOTATION_HEADS or head.startswith(_QUOTATION_VERB_STEMS)


_SENTENCE_END_FORMS = ("終止形", "連体形", "命令形")


def _opens_the_next_phrase(text: str, tail_token: Any, prev_end: int, start: int) -> bool:
    """Whether what the tagger calls an auxiliary after a space rather opens the next phrase.

    After て / で a spaced ない is as often the adjective 無い of the next clause
    (溶けて なくなる, 捜して なかった) as the negative of てる, and ます before
    another ます is the adverb ますます.
    """
    lemma = getattr(getattr(tail_token, "feature", None), "lemma", None)
    if lemma == "ない" and text[prev_end - 1 : prev_end] in ("て", "で"):
        return True
    return text[start : start + 4] == "ますます"


def _chain_end_candidates(text: str, raw_tokens: list, tok_start: int, tok_end: int) -> list[_ChainEnd]:
    """Ascending ends the chain-end resolver may take after ``tok_end``.

    The window of ``_extension_candidate_ends``, with what ``_chain_end_gate``
    needs to know about each end, and wider where the tagger's cut or the
    line's layout hides a chain: an auxiliary written in kanji, one space
    before an auxiliary, a lengthening あ, the negative ねえ after て, and an end
    inside a token (たって before a quotation, ちゃい before a particle, an
    imperative in よ). The table still has to accept every end.
    """
    from anki_miner.services.morphology import iter_token_spans

    # A chain goes on in hiragana, in a kanji auxiliary, in a lengthening ー or
    # after one space: before anything else (the line end, punctuation,
    # katakana) there is no end to list, and the line is not walked.
    after = text[tok_end : tok_end + 1]
    if not (
        _is_pure_hiragana(after) or after in (" ", "\u3000", "ー") or text.startswith(_KANJI_AUXILIARY_STEMS, tok_end)
    ):
        return []
    candidates: list[_ChainEnd] = []
    prev_end = tok_end
    tail = ""
    particle_ka = False
    spaced = False
    before_feature: Any = None  # of the token that ends where the next one should start
    try:
        # The spans are read one ahead and only as far as the chain goes.
        spans = iter_token_spans(text, raw_tokens)
        upcoming = next(spans, None)
        while upcoming is not None:
            tail_token, start, end = upcoming
            upcoming = next(spans, None)
            if end <= tok_end:
                before_feature = getattr(tail_token, "feature", None)
                continue
            if start != prev_end:
                if start < prev_end:
                    continue  # token overlapping the mined span; skip
                # One space before an auxiliary is how the line was laid out, not
                # the end of the word (帰り ましょう, 食べ ない); a stem in kana alone
                # is too easily another word (いえ ない), and after a form that can
                # end a sentence the space does end the word (違う とりゃあ！).
                if (
                    spaced
                    or text[prev_end:start] not in (" ", "\u3000")
                    or getattr(getattr(tail_token, "feature", None), "pos1", None) != "助動詞"
                    or all(_is_kana(ch) for ch in text[tok_start:tok_end])
                    or _opens_the_next_phrase(text, tail_token, prev_end, start)
                    or str(getattr(before_feature, "cForm", None)).startswith(_SENTENCE_END_FORMS)
                ):
                    break  # whitespace gap / non-adjacent tail
                spaced = True
            if end - tok_end > _WINDOW_CAP_CHARS:
                break
            surface = text[start:end]
            tail_feature = getattr(tail_token, "feature", None)
            pos1 = getattr(tail_feature, "pos1", None)
            # The token that touches this one on the right, if any.
            following = upcoming if upcoming is not None and upcoming[1] == end else None
            if not _is_pure_hiragana(surface.rstrip("ー") if pos1 == "助動詞" else surface) and not (
                _is_kanji_auxiliary(surface, pos1)
            ):
                break
            # The あ that lengthens a contracted conditional (見てりゃ|あ, 見りゃ|あ)
            # is a filler to the tagger.
            lengthened_ya = surface == "あ" and pos1 == "感動詞" and text[start - 2 : start] in ("りゃ", "きゃ")
            # ねえ after て / で with a particle or an auxiliary behind it is the
            # negative (聞い|て|ねえ|の), though the tagger may call it a noun there.
            negative_nee = (
                surface in ("ねえ", "ねぇ")
                and text[start - 1 : start] in ("て", "で")
                and following is not None
                and getattr(getattr(following[0], "feature", None), "pos1", None) in ("助詞", "助動詞")
            )
            if pos1 not in _INFLECTIONAL_TAIL_POS1 and not lengthened_ya and not negative_nee:
                break
            # 笑っ|たって|こと is 笑った and the quoting って: before a word that takes a
            # quotation the chain may end after the particle's first letter.
            if surface in ("たって", "だって") and pos1 == "助詞" and _takes_quotation(text, following):
                candidates.append(_ChainEnd(start + 1, tail + surface[0], False, particle_ka, surface[1]))
            # ちゃい before the particle や, け or か is ちゃ and いや / いけ / いか
            # (笑っちゃ|いやよ), not the stem of ちゃう: the chain ends after ちゃ.
            if (
                surface in ("ちゃい", "じゃい")
                and following is not None
                and text[following[1] : following[2]] in ("や", "け", "か")
            ):
                candidates.append(_ChainEnd(end - 1, tail + surface[:-1], False, particle_ka, "い"))
                break
            # An imperative in よ as ONE tail token (やめ|とけよ) may be the word and
            # the particle: the end before よ is a candidate too, and the gate
            # reads the whole token as it reads a separate よ.
            token_yo = (
                len(surface) > 1
                and surface.endswith("よ")
                and str(getattr(tail_feature, "cForm", None)).startswith("命令形")
            )
            if token_yo:
                candidates.append(_ChainEnd(end - 1, tail + surface[:-1], False, particle_ka, "よ"))
            tail += surface
            final_yo = token_yo or (surface == "よ" and getattr(tail_feature, "pos2", None) == "終助詞")
            particle_ka = particle_ka or (surface == "か" and pos1 == "助詞")
            candidates.append(_ChainEnd(end, tail, final_yo, particle_ka, text[end : end + 1]))
            prev_end = end
            before_feature = tail_feature
    except (AttributeError, TypeError):
        return []  # mock/duck-typed token stream: no extension
    return candidates


@lru_cache(maxsize=1)
def get_japanese_deinflector() -> Deinflector:
    """Process-wide ``Deinflector`` over the ported Yomitan Japanese table.

    Lazy import keeps the ~830-rule table off this module's import path;
    the build itself is a few milliseconds and happens once.
    """
    from anki_miner.services.japanese_transforms import CONDITIONS, TRANSFORMS

    return Deinflector(CONDITIONS, TRANSFORMS)


def _te_auxiliary_transforms() -> list[dict[str, Any]]:
    """て / で + the auxiliary verbs Yomitan's table has no transform for, back to the te-form.

    Modelled on the table's own ``-いる`` rules, with each auxiliary's own
    conjugation class as the condition, so the chain goes on past it:
    教えてもらえます is ``-て, -もらう, potential, -ます``. The same set as
    monosashi's deinflection supplements (its SPEC §4.1).
    """
    from anki_miner.services.japanese_transforms import suffix_inflection

    def te(auxiliaries: list[str], condition: str) -> list[dict[str, Any]]:
        return [suffix_inflection(t + aux, t, [condition], ["-て"]) for aux in auxiliaries for t in ("て", "で")]

    return [
        {"id": "-みる", "name": "-みる", "rules": te(["みる"], "v1")},
        {"id": "-くる", "name": "-くる", "rules": te(["くる", "来る"], "vk")},
        # Contracted いく too: てく, and てった / てってる whole, since the table
        # undoes った to く only for 行く itself. A bare てって would read the
        # quotative って after a te-form (待ってって) as the verb.
        {
            "id": "-いく",
            "name": "-いく",
            "rules": [*te(["いく", "行く", "ゆく", "く"], "v5"), *te(["った"], "-た"), *te(["ってる"], "v1p")],
        },
        {"id": "-ある", "name": "-ある", "rules": te(["ある"], "v5")},
        {"id": "-あげる", "name": "-あげる", "rules": te(["あげる", "上げる"], "v1")},
        {"id": "-くれる", "name": "-くれる", "rules": te(["くれる", "呉れる"], "v1")},
        {"id": "-もらう", "name": "-もらう", "rules": te(["もらう", "貰う"], "v5")},
        {"id": "-ほしい", "name": "-ほしい", "rules": te(["ほしい", "欲しい"], "adj-i")},
    ]


@lru_cache(maxsize=1)
def get_highlight_deinflector() -> Deinflector:
    """The Yomitan table plus て + auxiliary verbs, for a mined verb's span and front.

    A learner reads 教えてくれた as one verb, so its highlight runs to the end
    of the chain, as it already does through ている, てしまう and ておく, which
    the table has. The dictionary lookup ladder keeps the table alone.
    """
    from anki_miner.services.japanese_transforms import CONDITIONS, TRANSFORMS

    return Deinflector(CONDITIONS, [*TRANSFORMS, *_te_auxiliary_transforms()])


def _chain_end_supplements() -> list[dict[str, Any]]:
    """Forms inside a word's chain that the highlight table has no transform for.

    They serve the END of a highlight only and never a card front: with ん → る
    in the table a front is resolved from, 絡まん (絡む + ん) became 絡まる.

    Monosashi's deinflection supplements (its SPEC §4.1) that
    ``_te_auxiliary_transforms`` does not carry: 動いとん, おもろない, こよ / しよ and
    行きゃがる.

    Then forms of rough, Kansai and period speech the unit holds as well:
    恐れませぬ and 知りまへん as 恐れません, 言いたかない and 悪かない for くはない,
    住んどる as 住んでる, and the lengthened 見てりゃあ and なきゃあ.
    """
    from anki_miner.services.japanese_transforms import suffix_inflection

    yagaru = [("きゃがる", "く"), ("ぎゃがる", "ぐ"), ("しゃがる", "す"), ("ちゃがる", "つ")]
    yagaru += [("にゃがる", "ぬ"), ("びゃがる", "ぶ"), ("みゃがる", "む"), ("りゃがる", "る")]
    return [
        {
            "id": "-ん (kansai)",
            "name": "-ん (kansai)",
            "rules": [suffix_inflection("ん", "る", [], ["v5"]), suffix_inflection("すん", "する", [], ["vs"])],
        },
        {
            "id": "-ない (kansai)",
            "name": "-ない (kansai)",
            "rules": [suffix_inflection("ろない", "ろい", ["adj-i"], ["adj-i"])],
        },
        {
            "id": "volitional (short)",
            "name": "volitional (short)",
            "rules": [suffix_inflection("こよ", "くる", [], ["vk"]), suffix_inflection("しよ", "する", [], ["vs"])],
        },
        {
            "id": "-ゃがる",
            "name": "-ゃがる",
            "rules": [
                *(suffix_inflection(inflected, base, ["v5"], ["v5"]) for inflected, base in yagaru),
                suffix_inflection("しゃがる", "する", ["v5"], ["vs"]),
                suffix_inflection("きゃがる", "くる", ["v5"], ["vk"]),
            ],
        },
        {
            "id": "-ませぬ",
            "name": "-ませぬ",
            "rules": [
                suffix_inflection("ませぬ", "ます", ["-ません"], ["-ます"]),
                suffix_inflection("まへん", "ます", ["-ません"], ["-ます"]),
            ],
        },
        {"id": "-かない", "name": "-かない", "rules": [suffix_inflection("かない", "い", ["adj-i"], ["adj-i"])]},
        {"id": "-どる", "name": "-どる", "rules": [suffix_inflection("どる", "で", ["v5"], ["-て"])]},
        {
            "id": "-りゃあ",
            "name": "-りゃあ",
            "rules": [
                suffix_inflection("りゃあ", "れば", ["-ゃ"], ["-ば"]),
                suffix_inflection("きゃあ", "ければ", ["-ゃ"], ["-ば"]),
            ],
        },
    ]


@lru_cache(maxsize=1)
def get_chain_end_deinflector() -> Deinflector:
    """The highlight table plus ``_chain_end_supplements``: where a mined word's highlight ends, nothing else."""
    from anki_miner.services.japanese_transforms import CONDITIONS, TRANSFORMS

    return Deinflector(CONDITIONS, [*TRANSFORMS, *_te_auxiliary_transforms(), *_chain_end_supplements()])


# 出|なさ|れ|た is 出る and 為さる, though the table reads 出なさ as 出ない + さ: the
# next letter is the ending of なさる.
_NASARU_ENDINGS = frozenset("れるらりっい")
# After an ADJECTIVE's て the next verb is a word of its own (寒くて|いかん,
# 寂しくて|来た), unless a verb was made of the adjective first (寒がってくる).
_TE_AUXILIARY_IDS = frozenset({"-みる", "-くる", "-いく", "-ある", "-あげる", "-くれる", "-もらう", "-ほしい"})
_ADJECTIVE_VERB_MAKERS = frozenset({"-がる", "-すぎる", "-過ぎる"})
# What the table reads as an imperative in よ only by first building a verb in
# る that nobody said: 食べて|よ as 食べてる, 見とけ|よ and 言え|よ as a potential,
# 教えてくれ|よ as 教えてくれる. A verb's own よ form (捕まえよ, 会わせよ) is none of these.
_NO_YO_IMPERATIVE_AFTER = frozenset({"potential", "-いる", "-おく", "-くれる"})


def _chain_end_gate(candidate: _ChainEnd, chain: tuple[str, ...], pos1: object) -> bool:
    """Whether the resolver refuses an end the table accepts with ``chain`` for a ``pos1`` token.

    The table says what can be a chain; this says what is not one here:

    - A sentence-final よ is the particle, not the ending of an imperative,
      where that imperative would be the one of ``_NO_YO_IMPERATIVE_AFTER``.
    - かない for くはない has the tagger's たか or an adjective stem before it
      (言い|たか|ない, 悪|かない). Across the particle か the same letters are a
      past and a question (帰っ|た|か|ねえ), and for an ichidan verb only the
      tagger tells the two apart (食べ|た|か|ねえ): its reading stands.
    - A negative + さ before an ending of なさる (``_NASARU_ENDINGS``).
    - A て + auxiliary after an adjective's own て (``_TE_AUXILIARY_IDS``).
    """
    if candidate.particle_ka and "-かない" in chain:
        return True
    if chain[-2:] == ("negative", "-さ") and candidate.following in _NASARU_ENDINGS:
        return True
    if pos1 == "形容詞" and "-て" in chain:
        te = chain.index("-て")
        if not _ADJECTIVE_VERB_MAKERS.intersection(chain[:te]) and _TE_AUXILIARY_IDS.intersection(chain[te + 1 :]):
            return True
    return candidate.final_yo and len(chain) >= 2 and chain[-1] == "imperative" and chain[-2] in _NO_YO_IMPERATIVE_AFTER
