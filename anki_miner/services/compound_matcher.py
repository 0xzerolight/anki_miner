"""Dictionary-attested compound matching (Yomitan longest-match principle).

Fragment fix (走り出した mined as 走り, 応急処置 split into 応急+処置): MeCab/
unidic short-unit tokens are mined per token, so multi-token dictionary words
never surface whole. Yomitan never fragments because the DICTIONARY defines
word boundaries — it generates candidates longest-first, deinflects, and ranks
matches by source length. This module adapts that to whole-line mining: scan
positions come from MeCab tokens, and deinflection is delegated to MeCab (the
span's tail token contributes its ``orthBase`` dictionary form).

Runs AFTER ``morphology.merge_compound_suffixes``. For spans of adjacent
tokens starting at a structurally contentful token, the longest span whose
candidate string is an exact offline-dictionary headword is merged into one
synthetic token; consumed tokens are skipped (greedy left-to-right, like
Yomitan's scan).

Lives outside ``morphology.py`` because the matcher is a stateful object — it
holds a mutable existence cache and an injected lookup dependency — unlike
morphology's pure stateless token helpers. Import direction:
``compound_matcher`` imports from ``morphology``; ``subtitle_parser`` imports
from both.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Callable

from anki_miner.services.morphology import (
    _FOLD_SUFFIX_PAIRS,
    SyntheticToken,
    TokenInclusionRule,
    extract_orth_base,
    extract_reading,
    iter_token_spans,
    mining_base,
    word_surface,
)
from anki_miner.utils.text_utils import katakana_to_hiragana

# Batch existence probe: returns the subset of the input strings that exist as
# exact dictionary headwords (DefinitionService.offline_terms_exist).
TermLookup = Callable[[list[str]], set[str]]

# Same batch shape, kept as a separate type name so parser wiring cannot
# accidentally substitute dictionary-form attestation for raw name membership.
NameLookup = Callable[[list[str]], set[str]]

# Single-headword attested-readings probe (the parser's memoized
# _attested_readings): an empty list when the dictionary records no reading.
ReadingsLookup = Callable[[str], list[str]]

# A dictionary headword joined across this particle must be speakable as the
# tokens' own readings. 彼の方 is a JMdict headword read あのかた, but 彼+の+方
# reads かれのほう, so the join names another word. Limited to の on measured
# evidence: checking every particle refused real joins (気に入る: UniDic reads
# 入ら as ハイラ), while across 874 merges on 12,453 Unnatural lines the の check
# refused none. Known cost: a token UniDic misreads (猫の額 as ねこのがく) loses
# its join. A word + particle span (one ending on an _ADVERBIAL_PARTICLES
# particle) is checked too: 外|に reads そとに, but JMdict's 外に is ほかに
# 'besides', and the join would stamp 外[ほか]に into the sentence. Such a span
# holds no verb or adjective (_adverbial_fits), so the misread verb tails
# above cannot arise in it.
_READING_CHECKED_PARTICLE = "の"

_HIRAGANA_RE = re.compile(r"[ぁ-ゖー]+")

# Voicing marks (゛゜ as combining characters after NFD). Readings are compared
# without them so rendaku inside a compound tail (トカゲのしっぽ切り: the line
# reads しっぽきり, the dictionary しっぽぎり) does not refuse a real join.
_VOICING_MARKS = frozenset("゙゚")


def _unvoiced(kana: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", kana) if ch not in _VOICING_MARKS)


# Span tails that conjugate: their candidate uses orthBase (dictionary form in
# the token's own orthography — unidic's lemma is kanji-canonical, し→為る,
# which is NOT what dictionaries store for kana idioms like 気がする).
# 助動詞 is deliberately NOT here: no legitimate compound ends in a bare
# auxiliary, and JMdict attests some aux-inclusive strings (やった) that must
# never become card fronts.
_INFLECTABLE_POS1 = frozenset({"動詞", "形容詞"})

# The ておく contraction (放っ|とい|て): UniDic tags とく 助動詞, but JMdict lists
# 放っとく as a verb, so a span may end on it and is looked up by its base とく.
_CONTRACTION_TAILS = frozenset({"とく"})


def _contraction_tail(token) -> bool:
    return _pos1(token) == "助動詞" and extract_orth_base(token) in _CONTRACTION_TAILS


def _potential_base(token) -> str:
    """A potential verb's base in its own spelling (おける → おく), or "".

    UniDic gives 放って|おけ|ない the potential base おける, which no dictionary
    joins; the swap is taken only when the swapped reading is the lemma's
    (オケル → オク), so 帰れる (lemma 返る) or a real ichidan verb never folds.
    """
    feature = getattr(token, "feature", None)
    orth_base = extract_orth_base(token)
    kana_base, l_form = getattr(feature, "kanaBase", None), getattr(feature, "lForm", None)
    if _pos1(token) != "動詞" or not isinstance(kana_base, str) or not isinstance(l_form, str):
        return ""
    for derived, base in _FOLD_SUFFIX_PAIRS:
        # The godan potential pairs only (ける → く), not the adjective or サ変 ones.
        if len(base) != 1 or not derived.endswith("る") or not orth_base.endswith(derived):
            continue
        swapped = katakana_to_hiragana(kana_base)[: -len(derived)] + base
        if orth_base[: -len(derived)] and swapped == katakana_to_hiragana(l_form):
            return orth_base[: -len(derived)] + base
    return ""


# A noun the line spells for the ear: a native word in katakana (ワレ, フリ, カネ)
# or a one-word noun with its vowel drawn out (手ぇ, 気い, 目ぇ). The dictionary
# lists the idiom under the plain spelling (我を忘れる, 手を出す), so a span
# also offers the join with such a token's lemma. Hiragana is left alone: the
# tagger's kanji for a kana word is a guess (かい is 甲斐, 貝 or 会). A katakana
# word must read as its lemma does, voicing aside (酒グセ is 酒癖): アンタ has the
# lemma 貴方, which is あなた, another word.
_KATAKANA_WORD_RE = re.compile(r"[ァ-ヺー]+")
_KANJI_RE = re.compile(r"[々一-鿿]")
_DRAWN_VOWELS = frozenset("ぁぃぅぇぉあいうえおー")


def _plain_spelling(token) -> str:
    """``token``'s surface, or its lemma when the surface is that noun written for the ear."""
    surface = getattr(token, "surface", None) or ""
    lemma = getattr(getattr(token, "feature", None), "lemma", None)
    if _pos1(token) not in ("名詞", "代名詞") or not isinstance(lemma, str) or not _KANJI_RE.search(lemma):
        return surface
    if surface[:-1] == lemma and surface[-1] in _DRAWN_VOWELS:
        return lemma
    lemma_reading = getattr(token.feature, "lForm", None)
    if (
        _KATAKANA_WORD_RE.fullmatch(surface)
        and isinstance(lemma_reading, str)
        and _unvoiced(surface) == _unvoiced(lemma_reading)
    ):
        return lemma
    return surface


# Speech drops the が of a noun + adjective idiom (耳早い, 頭痛い, 金ない). A noun
# right before an adjective also offers the dictionary's own spelling with the
# particle, after the plain join (仲いい is a headword of its own). Verbs stay
# out on measured evidence: 腹減った is 腹が減る, but as many bare noun + verb
# pairs are the two words the learner wants (the benchmark lost six cards and
# gained three junk ones for one card gained). So does the formal noun 事: 事ない
# is as often そんな事ない or 心配する事ない as the 事がない of 見た事がない.
_DROPPED_PARTICLE = "が"
_DROPPED_PARTICLE_REJECT_HEADS = frozenset({"事"})
# The few noun + verb idioms that are one word to the speaker, by the noun's plain
# spelling and the verb's base, with the particle speech drops (腹減った, 喉渇いた,
# 気いつけて). Every other bare noun + verb pair stays two words, as measured above.
_DROPPED_PARTICLE_VERBS: dict[tuple[str, str], str] = {
    ("腹", "減る"): "が",
    ("お腹", "減る"): "が",
    ("喉", "渇く"): "が",
    ("気", "つける"): "を",
}


def _dropped_particle_candidates(head, tail, prefix: str) -> Alternatives:
    """The kind-A candidate for a bare noun + adjective with が put back, or a listed noun + verb idiom."""
    if _pos1(head) != "名詞" or _pos2(head) != "普通名詞":
        return ()
    base = extract_orth_base(tail)
    if _pos1(tail) == "動詞":
        particle = _DROPPED_PARTICLE_VERBS.get((prefix, base))
        return ((prefix + particle + base, "A"),) if particle else ()
    if _pos1(tail) != "形容詞" or head.surface in _DROPPED_PARTICLE_REJECT_HEADS:
        return ()
    return ((prefix + _DROPPED_PARTICLE + base, "A"),) if base else ()


def _voiced_base(token) -> str:
    """A tail's base with the voiced first kana of its surface, or "" when they agree.

    元気|づけよう: the tagger gives the volitional the orthBase つける, and the
    entry is 元気づける.
    """
    surface = getattr(token, "surface", None) or ""
    base = extract_orth_base(token)
    if not surface or not base or surface[0] == base[0] or _unvoiced(surface[0]) != base[0]:
        return ""
    return surface[0] + base[1:]


# A span must END on a content token. Without this rule, JMdict's thousands of
# inflected-form headwords (気にするな, 気をつけて, ああ言った …) would match a
# raw surface join and ship an inflected card front.
_NON_CONTENT_POS1 = frozenset({"助詞", "助動詞", "記号", "補助記号", "空白"})

# The one exception: a word + particle that JMdict lists as an adverb, expression,
# pronoun or conjunction of its own (本当に, 誰も, 一人で, 今まで, 心から), as
# Yomitan's longest match reads it. は, が, を and の stay out (今日は is the
# greeting, 彼の a phrase), and so does a word something modifies: その時に and
# 行く時に are the noun 時 with its case particle, not the adverb 時に. A span
# with a verb or adjective in it is that word inflected (急いで), never the entry.
# と joins too (二度と 'never again', not 二度 'twice'), but never across an
# auxiliary (何だと is 何 + the copula + the quoting と) nor onto a lone pronoun:
# 何と is mostly 何となく, 何としても or a quoted 何と書かれて.
_ADVERBIAL_PARTICLES = frozenset({"に", "も", "で", "か", "から", "まで", "や", "でも", "しか", "と"})
_ADVERBIAL_TAGS = frozenset({"adv", "exp", "pn", "conj"})
# JMdict verb classes (v1, v5k, vk, vs-i, vz): an entry with none of them does not inflect.
_VERB_CLASS_TAGS = ("v1", "v5", "vk", "vs", "vz")


def _adverbial_end(token) -> bool:
    """Whether a word + particle span may end on ``token``.

    An _ADVERBIAL_PARTICLES particle (本当|に), or the copula's own に after a
    na-adjective (確か|に, まとも|に: UniDic tags it 助動詞 だ), which JMdict
    lists as the adverb 確かに.
    """
    if _pos1(token) == "助詞":
        return token.surface in _ADVERBIAL_PARTICLES
    return (
        _pos1(token) == "助動詞"
        and token.surface == "に"
        and getattr(getattr(token, "feature", None), "lemma", None) == "だ"
    )


# A word JMdict lists only inflected (計り知れない, 折り入って, 思いがけず,
# 心置きなく, 聞き捨てならない): UniDic gives the stem a base no dictionary has
# (計り知れる, 折り入る), and the end-on-content rule never lets a span reach the
# tail. When the head's own base is unattested, the line's surface through one
# of these tails (past なら) is offered too; an attested base (構う) keeps its
# own word, so 構わない never merges.
_FIXED_FORM_TAILS = frozenset({"て", "で", "ない", "ず", "ぬ", "なく"})
_FIXED_FORM_LINKS = frozenset({"なら"})
# The benefactive auxiliaries after て, in kana as they are written (``_benefactive_after_te``).
_BENEFACTIVE_TAILS = frozenset({"くれる", "もらう", "あげる"})

# A dictionary entry's tags, by headword (JMdict's adv, exp, n, …).
EntryTagsLookup = Callable[[list[str]], dict[str, set[str]]]

# Over-merge guards. Deliberately module constants, not config: the char cap is
# a safety bound and should not be a user footgun; the token cap bounds
# candidate generation. 16 chars (was 12; Yomitan's point-scan default is 10)
# admits the 2-token katakana tech compounds JMdict attests at 13-16 chars
# (アプリケーションプログラム) that 12 rejected. The junk classes 13-16 chars
# would otherwise admit are carried by the OTHER guards, fixture-proven: 13+
# char attested phrases/proverbs run >=6 tokens (お誕生日おめでとうございます is
# 7) and die on the 5-token cap even when attested; inflected-phrase headwords
# die on the end-on-content rule; everything else dies on attestation.
# Exact name matching uses 24 chars, the longest current bundled name-wordset
# entry; curated-resource membership is itself the boundary attestation.
# Verbs whose と quotes the 何か before it (``_other_words_here``).
_QUOTING_VERBS = frozenset({"思う", "言う", "聞く"})
_MAX_SPAN_CHARS = 16
_MAX_NAME_SPAN_CHARS = 24
_MAX_SPAN_TOKENS = 5

# One span may offer more than one lookup candidate, tried in order (first
# attested wins). Spans that offer a single candidate behave exactly as before.
Alternatives = tuple[tuple[str, str], ...]

# Existence-cache bound (positive AND negative results). Clear-on-cap keeps
# whole-corpus runs from growing without limit.
_EXIST_CACHE_CAP = 200_000


class CompoundSyntheticToken(SyntheticToken):
    """Matcher-produced merged token.

    Distinct subclass (not an instance attribute — the base declares
    ``__slots__``) so ``_emit_word`` can detect matcher output via
    ``getattr(token, "compound", False)`` and regenerate the emitted reading:
    the concatenated component kana is wrong for cross-particle compounds
    (気がする → キガシ) and ``TokenizedWord.reading`` reaches the curation
    dialog and TSV export.
    """

    __slots__ = ()
    compound = True


def _pos1(token) -> str | None:
    try:
        pos1 = token.feature.pos1
    except AttributeError:
        return None
    return str(pos1) if pos1 else None


def _pos2(token) -> str | None:
    try:
        pos2 = token.feature.pos2
    except AttributeError:
        return None
    return str(pos2) if pos2 else None


def _c_form(token) -> str:
    """Inflection form, or "" when absent.

    Synthetic tokens carry no ``cForm``, and fugashi maps unidic's ``*``
    placeholder to None — both must read as "no inflection form", never crash.
    """
    try:
        c_form = token.feature.cForm
    except AttributeError:
        return ""
    return str(c_form) if c_form and c_form != "*" else ""


def _modifies(token) -> bool:
    """Whether ``token`` modifies the noun after it.

    の, a pre-noun adjective (その), an adnominal form (行く), or a noun or
    suffix the noun continues (30分|後, 昨年|末).
    """
    pos1 = _pos1(token)
    if pos1 == "助詞":
        return bool(token.surface == "の")
    if pos1 in ("連体詞", "名詞", "接尾辞"):
        return True
    return pos1 in ("動詞", "形容詞", "助動詞") and _c_form(token).startswith("連体形")


def _tail_reading(token, kind: str) -> str:
    """The span tail's reading: its base-form kana for a deinflected (kind A) span."""
    if kind == "A":
        kana_base = getattr(getattr(token, "feature", None), "kanaBase", None)
        if isinstance(kana_base, str) and kana_base not in ("", "*"):
            return kana_base
    return extract_reading(token)


class CompoundDictionaryMatcher:
    """Greedy longest-match merger over one line's token stream.

    ``term_lookup`` is injected (no SQLite here); ``inclusion_rule`` is the
    same gate the parser mines with, applied to completed synthetic tokens.
    Candidate boundaries use structural content checks instead.
    """

    _default_max_span_chars = _MAX_SPAN_CHARS
    _fixed_forms = True
    # Whether a span also offers its spoken-idiom spellings (_plain_spelling,
    # _dropped_particle_candidates); a name span never does.
    _spoken_idioms = True

    def __init__(
        self,
        term_lookup: TermLookup,
        inclusion_rule: TokenInclusionRule,
        max_span_tokens: int = _MAX_SPAN_TOKENS,  # parameterized for tests only
        max_span_chars: int | None = None,
        force_include: Callable[[str], bool] | None = None,
        reading_lookup: ReadingsLookup | None = None,
        entry_tags: EntryTagsLookup | None = None,
    ) -> None:
        self._lookup = term_lookup
        self._rule = inclusion_rule
        # Tags of a word + particle headword (_ADVERBIAL_TAGS); None ⇒ no such span.
        self._entry_tags = entry_tags
        self._tags_cache: dict[str, set[str]] = {}
        # The run's whitelist probe (the parser's own): an attested span whose
        # synthetic the gate refuses still merges when its headword is
        # whitelisted and its tags are rescuable (R1); the parser then rescues it.
        self._force_include = force_include
        # Attested readings for _reading_fits; None ⇒ no check.
        self._readings = reading_lookup
        self._max_span = max(2, max_span_tokens)
        char_bound = self._default_max_span_chars if max_span_chars is None else max_span_chars
        self._max_span_chars = max(2, char_bound)
        self._exist_cache: dict[str, bool] = {}
        # This line's fixed-form spans -> their head's own base (_FIXED_FORM_TAILS).
        self._fixed_heads: dict[tuple[int, int], str] = {}

    def merge_line(self, text: str, tokens: list) -> list:
        """Return a new token list with dictionary-attested spans merged.

        Never mutates ``tokens`` or its elements (the parser's per-file line
        cache shares them). One batched ``term_lookup`` call per line covers
        every uncached candidate.
        """
        n = len(tokens)
        if n < 2:
            return tokens

        candidates = self._generate_candidates(text, tokens)
        if not candidates:
            return tokens
        self._resolve(candidates)

        merged: list = []
        i = 0
        while i < n:
            token = tokens[i]
            replacement = None
            consumed_end = i
            if self._can_start_at(tokens, i):
                # Longest span first — Yomitan ranks by source length.
                for j in range(min(i + self._max_span - 1, n - 1), i, -1):
                    alternatives = candidates.get((i, j))
                    if not alternatives:
                        continue
                    for candidate, kind in alternatives:
                        if not self._exist_cache.get(candidate):
                            continue
                        if self._other_words_here(tokens, i, j, candidate):
                            continue
                        if kind == "A" and self._inflects_pre_noun_word(tokens[j], candidate):
                            continue
                        if kind == "A" and self._benefactive_after_te(tokens, i, j):
                            continue
                        if kind == "F":
                            if self._exist_cache.get(self._fixed_heads[(i, j)]):
                                continue
                        elif not self._reading_fits(tokens[i : j + 1], candidate, kind) or (
                            not self._can_end(tokens[j])
                            and not (kind == "A" and _contraction_tail(tokens[j]))
                            and not self._adverbial_fits(tokens, i, j, candidate)
                        ):
                            continue
                        synthetic = self._build_synthetic(tokens[i : j + 1], candidate, kind)
                        if self._spoken_idioms and kind == "A":
                            self._keep_written_base(synthetic, tokens[i : j + 1], candidate)
                        # Never consume tokens for a word the gate would then drop.
                        if self._rule.should_include(synthetic) or self._whitelisted(synthetic, candidate):
                            replacement = synthetic
                            consumed_end = j
                            break
                    if replacement is not None:
                        break
            if replacement is not None:
                merged.append(replacement)
                i = consumed_end + 1
            else:
                merged.append(token)
                i += 1
        return merged

    @staticmethod
    def _other_words_here(tokens: list, i: int, j: int, headword: str) -> bool:
        """Whether an attested span is other words where it stands, by headword.

        こんな物がある 'there is such a thing': the entry 物がある 'to be (very) …'
        closes a clause (胸に迫る物がある), so after a pre-noun word or の its 物 is
        the plain noun. 何かと思えば, 何かと言うと: before a verb of thinking,
        saying or asking the と quotes 何か, and 何かと 'in various ways' is no
        word there.
        """
        if headword == "物がある":
            return i > 0 and (_pos1(tokens[i - 1]) == "連体詞" or tokens[i - 1].surface == "の")
        if headword == "何かと" and j + 1 < len(tokens):
            return getattr(getattr(tokens[j + 1], "feature", None), "lemma", None) in _QUOTING_VERBS
        return False

    def _inflects_pre_noun_word(self, tail, headword: str) -> bool:
        """Whether a deinflected span would inflect a word that only stands before a noun (そう言って).

        The dictionary lists そう言う, こう言う and どう言う as pre-noun words
        ('such', adj-pn) with no verb class: they have no て-form. With an
        inflected tail the line is the adverb and the verb (そう言って笑った, 'said
        so and laughed'); the base form before a noun stays joined (そう言う人).
        """
        if self._entry_tags is None or _pos1(tail) != "動詞" or tail.surface == extract_orth_base(tail):
            return False
        if headword not in self._tags_cache:
            if len(self._tags_cache) >= _EXIST_CACHE_CAP:
                self._tags_cache.clear()
            self._tags_cache[headword] = self._entry_tags([headword]).get(headword, set())
        tags = self._tags_cache[headword]
        return "adj-pn" in tags and not any(tag.startswith(_VERB_CLASS_TAGS) for tag in tags)

    @staticmethod
    def _benefactive_after_te(tokens: list, i: int, j: int) -> bool:
        """Whether a span ends on くれる / もらう / あげる right after a verb's て (出し|て|くれる, 見|て|もらう).

        The dictionary lists 出してくれる, 貸してあげる and 見てもらう (a spelling of
        診てもらう 'to see a doctor'), but in a line the tail is the benefactive
        auxiliary any verb takes (お金を出してくれた, 部長に見てもらう): the verb keeps
        the card, as 聞いてくれる cards 聞く. 診てもらう as the dictionary heads it
        stays joined.
        """
        if j - i < 2 or (_potential_base(tokens[j]) or extract_orth_base(tokens[j])) not in _BENEFACTIVE_TAILS:
            return False
        link = tokens[j - 1]
        if _pos2(link) != "接続助詞" or link.surface not in ("て", "で"):
            return False
        return bool(tokens[i].surface != "診")

    def _keep_written_base(self, synthetic, span: list, headword: str) -> None:
        """Note on a spoken-idiom join the base the line spells (腹減る for 腹が減る).

        The highlight follows a word's inflection by deinflecting the line's text
        to the token's base; 腹減って never reaches 腹が減る, so the bold would
        stop at the stem. The parser offers ``written_base`` as one more target
        (``_find_highlight_end``); the card front stays the headword.
        """
        prefix = "".join(token.surface for token in span[:-1])
        tail = span[-1]
        if all(
            headword != own for own, _kind in self._candidate_for_tail(prefix, tail, prefix + tail.surface, span[0])
        ):
            synthetic.feature.written_base = prefix + extract_orth_base(tail)

    def _adverbial_fits(self, tokens: list, start: int, end: int, headword: str) -> bool:
        """Whether a span ending on a particle is a word of its own here (本当に, not その時に)."""
        if self._entry_tags is None or (start > 0 and _modifies(tokens[start - 1])):
            return False
        # A verb's te-form is the verb (回収を急いでいます), a suffix or pre-noun
        # adjective heads a phrase, not a word (神様に, その下に), and so does a
        # nominalizing の (何なのか) or the grammar ように (毎日のように).
        if _pos1(tokens[start]) in ("接尾辞", "連体詞") or any(
            _pos1(token) in _INFLECTABLE_POS1 or _pos2(token) in ("準体助詞", "助動詞語幹")
            for token in tokens[start : end + 1]
        ):
            return False
        if tokens[end].surface == "と" and (
            (end - start == 1 and _pos1(tokens[start]) == "代名詞")
            or any(_pos1(token) == "助動詞" for token in tokens[start:end])
        ):
            return False
        if headword not in self._tags_cache:
            if len(self._tags_cache) >= _EXIST_CACHE_CAP:
                self._tags_cache.clear()
            self._tags_cache[headword] = self._entry_tags([headword]).get(headword, set())
        return bool(self._tags_cache[headword] & _ADVERBIAL_TAGS)

    def _can_end_span(self, token) -> bool:
        """Whether a span may end at ``token``: a content token, or an adverbial particle when tags are wired."""
        if self._can_end(token) or _contraction_tail(token):
            return True
        return self._entry_tags is not None and _adverbial_end(token)

    def _whitelisted(self, synthetic, headword: str) -> bool:
        """Whether the whitelist rescues a synthetic the inclusion gate refuses (R1)."""
        return self._force_include is not None and self._rule.rescuable(synthetic) and self._force_include(headword)

    def _reading_fits(self, span: list, headword: str, kind: str) -> bool:
        """Whether a join across の, or a word + particle span, can be read as its dictionary headword.

        True when no reading probe is wired, the span neither crosses の nor
        ends on an adverbial particle (本当|に), a token has
        no kana reading (OOV: its "reading" is its surface), or the dictionary
        records no reading for the headword. A kind-A span compares the tail's
        base reading (利い → キク), since the headword ends in the base form.
        Voicing marks are ignored on both sides, so rendaku still matches.
        """
        if self._readings is None:
            return True
        ends_on_particle = _adverbial_end(span[-1])
        if not ends_on_particle and not any(
            _pos1(token) == "助詞" and token.surface == _READING_CHECKED_PARTICLE for token in span
        ):
            return True
        spoken = katakana_to_hiragana("".join(extract_reading(t) for t in span[:-1]) + _tail_reading(span[-1], kind))
        if not _HIRAGANA_RE.fullmatch(spoken):
            return True
        readings = self._readings(headword)
        return not readings or _unvoiced(spoken) in {_unvoiced(katakana_to_hiragana(r)) for r in readings}

    def _can_start(self, token) -> bool:
        """Whether candidate spans may start at ``token``."""
        return self._can_end(token)

    def _can_start_at(self, tokens: list, i: int) -> bool:
        """``_can_start``, and not a suffix attached to the word before it.

        A suffix belongs to the word on its left: when the noun-suffix pass
        bails 義母|上|様 or 母上|殿, the leftover suffix and the address 様, 殿
        or さん must not join as a word of their own (上様 'the shogun', 上殿,
        長殿). Other suffix pairs keep their joins. After a space, or at the
        line start, a suffix-tagged token may still begin one (上様は茶室に).
        """
        if not self._can_start(tokens[i]):
            return False
        return not (
            0 < i < len(tokens) - 1
            and _pos1(tokens[i]) == "接尾辞"
            and _pos1(tokens[i + 1]) == "接尾辞"
            and tokens[i + 1].surface in ("様", "殿", "さん")
            and _pos1(tokens[i - 1]) in ("名詞", "接尾辞")
            and not getattr(tokens[i], "white_space", "")
        )

    @staticmethod
    def _can_end(token) -> bool:
        """Whether candidate spans may end at ``token``."""
        pos1 = _pos1(token)
        return pos1 is not None and pos1 not in _NON_CONTENT_POS1

    @staticmethod
    def _source_spans(text: str, tokens: list) -> dict[int, tuple[int, int]]:
        """Map token indexes to their cursor-aligned source spans."""
        spans: dict[int, tuple[int, int]] = {}
        next_index = 0
        for located, start, end in iter_token_spans(text, tokens):
            while next_index < len(tokens) and tokens[next_index] is not located:
                next_index += 1
            if next_index >= len(tokens):
                break
            spans[next_index] = (start, end)
            next_index += 1
        return spans

    def _generate_candidates(self, text: str, tokens: list) -> dict[tuple[int, int], Alternatives]:
        """Map ``(start, end)`` span -> candidate alternatives in preference order.

        kind "A" = deinflected tail (joined surfaces + tail orthBase);
        kind "B" = plain surface join. For an inflected tail the surface join is
        normally an inflected string, and matching it would ship inflected-
        headword card fronts like 気をつけて — so B is offered for an inflectable
        tail only in the one structurally safe case, see ``_candidate_for_tail``.
        """
        n = len(tokens)
        out: dict[tuple[int, int], Alternatives] = {}
        self._fixed_heads = {}
        source_spans = self._source_spans(text, tokens)
        surfaces = [word_surface(token) for token in tokens]
        for i in range(n - 1):
            start_span = source_spans.get(i)
            if start_span is None or not self._can_start_at(tokens, i):
                continue
            prefix = surfaces[i]
            plain = _plain_spelling(tokens[i]) if self._spoken_idioms else prefix
            source_end = start_span[1]
            for j in range(i + 1, min(i + self._max_span, n)):
                tail = tokens[j]
                tail_span = source_spans.get(j)
                # MeCab omits whitespace tokens. Candidate components still
                # must be adjacent in this occurrence; a same-spelled join
                # elsewhere in the line cannot license a merge here.
                if tail_span is None or tail_span[0] != source_end:
                    break
                joined = prefix + surfaces[j]
                if len(joined) > self._max_span_chars:
                    break
                # Span-end rule: non-content ends are not candidate endpoints,
                # but the span may still extend past them (気に|する|な: the
                # (0..2) span ending on する is reachable through the な).
                if self._can_end_span(tail):
                    out[(i, j)] = self._candidate_for_tail(prefix, tail, joined, tokens[i])
                    if self._spoken_idioms:
                        out[(i, j)] += self._spoken_idiom_candidates(plain, tokens, i, j, joined)
                head_base = self._fixed_form_head(tokens, i, j)
                if head_base is not None:
                    out[(i, j)] = out.get((i, j), ()) + ((joined, "F"),)
                    self._fixed_heads[(i, j)] = head_base
                prefix = joined
                if self._spoken_idioms:
                    plain += _plain_spelling(tail)
                source_end = tail_span[1]
        return out

    def _spoken_idiom_candidates(self, plain: str, tokens: list, i: int, j: int, joined: str) -> Alternatives:
        """The span's extra candidates: its plain spelling, and a dropped が put back.

        ``plain`` is the plain spelling of ``tokens[i..j-1]``. Offered after the
        span's own candidates, so a spelling the dictionary lists as written wins.
        """
        tail = tokens[j]
        plain_joined = plain + _plain_spelling(tail)
        extra: Alternatives = ()
        if plain_joined != joined:
            extra += self._candidate_for_tail(plain, tail, plain_joined, tokens[i])
        if j == i + 1:
            extra += _dropped_particle_candidates(tokens[i], tail, plain)
        return extra

    def _fixed_form_head(self, tokens: list, i: int, j: int) -> str | None:
        """The head's own base when ``tokens[i..j]`` is a word + a _FIXED_FORM_TAILS tail, else None."""
        head, tail = tokens[i], tokens[j]
        if not self._fixed_forms or j - i > 2 or _pos1(head) not in ("動詞", "名詞"):
            return None
        if tail.surface not in _FIXED_FORM_TAILS or any(t.surface not in _FIXED_FORM_LINKS for t in tokens[i + 1 : j]):
            return None
        if tail.surface in ("て", "で") and _pos2(tail) != "接続助詞":
            return None
        return extract_orth_base(head) if _pos1(head) == "動詞" else head.surface

    @staticmethod
    def _candidate_for_tail(prefix: str, tail, joined: str, head) -> Alternatives:
        """Lookup candidates for one span, in preference order (first attested wins).

        An inflectable tail normally yields ONLY the deinflected kind-A candidate:
        its raw surface join is an inflected string, and attesting that would ship
        気をつけて as a card front (the reason ``_INFLECTABLE_POS1`` exists).

        The one exception is a 接頭辞-headed span whose tail is in 連用形 — the
        standard honorific-nominalization pattern (ご存じ, お願い, お帰り). unidic
        tags 存じ as a 連用形 verb whose orthBase is the archaic 存ずる, so kind A
        builds ご存ずる, which no dictionary attests; 存じ then mines alone and the
        じる/ずる resolver ships 存じる. The surface join ご存じ IS an attested noun
        headword, so it is offered as a second alternative. This cannot leak
        inflected fronts: 接頭辞 is structurally incapable of being an inflected
        form, and the 連用形 gate blocks te-/past-form tails. Widening it past
        接頭辞 heads would re-open the 気をつけて hole.

        kind A stays FIRST so a span that attests both keeps its pre-fix front.

        A potential tail also offers its folded base (放って|おけ: おける → 放っておく),
        and the ておく contraction とく its base (放っ|とい → 放っとく).
        """
        if _contraction_tail(tail):
            return ((prefix + extract_orth_base(tail), "A"),)
        if _pos1(tail) not in _INFLECTABLE_POS1:
            return ((joined, "B"),)
        alternatives: Alternatives = ((prefix + extract_orth_base(tail), "A"),)
        folded = _potential_base(tail)
        if folded:
            alternatives += ((prefix + folded, "A"),)
        voiced = _voiced_base(tail)
        if voiced:
            alternatives += ((prefix + voiced, "A"),)
        # A classical 連体形 (名|高き, orthBase 高し) is looked up by its modern base
        # too: the dictionary lists 名高い.
        modern = mining_base(tail)
        if _pos1(tail) == "形容詞" and modern != extract_orth_base(tail):
            alternatives += ((prefix + modern, "A"),)
        if _pos1(head) == "接頭辞" and _c_form(tail).startswith("連用形"):
            alternatives += ((joined, "B"),)
        return alternatives

    def _resolve(self, candidates: dict[tuple[int, int], Alternatives]) -> None:
        """One batched lookup for all uncached candidate strings.

        ``_exist_cache`` is keyed by candidate STRING, so a span offering two
        alternatives is two independent entries resolved in the same batch.
        """
        current = {c for alternatives in candidates.values() for c, _kind in alternatives}
        current.update(self._fixed_heads.values())
        verdicts = {c: self._exist_cache[c] for c in current if c in self._exist_cache}
        unknown = {c for c in current if c not in verdicts}
        if not unknown:
            return
        hits = self._lookup(sorted(unknown))
        if len(self._exist_cache) + len(unknown) > _EXIST_CACHE_CAP:
            self._exist_cache.clear()
            # merge_line reads this cache after resolution; retain verdicts
            # required by the current line across the clear.
            self._exist_cache.update(verdicts)
        for candidate in unknown:
            self._exist_cache[candidate] = candidate in hits

    def _build_synthetic(self, span: list, headword: str, kind: str) -> CompoundSyntheticToken:
        """Assemble the merged token.

        surface = the joined span surfaces exactly as they appear in the text
        (locatable → correct offsets/bolding); lemma = the attested headword
        verbatim, so every lemma-keyed consumer (definitions, known words,
        frequency, pitch) sees the dictionary form.

        POS drives ``TokenizedWord.mined_form`` (lemma for 動詞/形容詞, surface
        otherwise): kind A inherits the tail's conjugating pos1 so the card
        front is the headword; its pos2 is pinned to 一般 — the real tails
        carry pos2=非自立可能, and inheriting that would make the merge's
        survival depend on the user's ``excluded_subtypes`` (the 非自立 vs
        非自立可能 trap) and silently drop compounds. Kind B inherits the
        uninflected tail's POS, which identifies the attested headword's type
        even when the first token has a different POS (動く+歩道). Surface
        equals the headword there, so mined_form is right either way. No new
        POS value is invented — a novel pos1 would silently break the ``pos in
        ("動詞", "形容詞")`` checks in word/pitch code.
        """
        surface = "".join(t.surface for t in span)
        kana = "".join(extract_reading(t) for t in span)
        if kind == "A":
            # A contraction tail (とく) is 助動詞; the attested headword is a verb.
            pos1 = "動詞" if _contraction_tail(span[-1]) else (_pos1(span[-1]) or "動詞")
            pos2 = "一般"
        else:
            # Inherit the tail's POS only when the tail is itself a content
            # word (動く歩道 → 歩道 keeps 名詞). A suffix tail (入院中, 可能性)
            # would smuggle 接尾辞 onto the synthetic and the inclusion gate
            # would reject the whole attested compound — those chains are
            # lexicalized nouns, so they take the nominal default instead.
            # A 接頭辞 + 連用形-verb kind B (ご存じ) lands here too and takes the
            # nominal default, which is exactly right: the attested headword is
            # a noun, and inheriting 動詞 would make mined_form the verb front.
            tail_pos1 = _pos1(span[-1])
            tail_pos2 = _pos2(span[-1])
            if tail_pos1 in ("名詞", "形状詞", "代名詞"):
                pos1 = tail_pos1
                pos2 = tail_pos2 if tail_pos2 and tail_pos2 != "*" else "普通名詞"
            else:
                pos1 = "名詞"
                pos2 = "普通名詞"
        return CompoundSyntheticToken(
            surface=surface,
            pos1=pos1,
            pos2=pos2,
            lemma=headword,
            kana=kana,
        )


# A name span never starts on a hiragana particle, auxiliary, pre-noun
# adjective or verb (その子, な子, てる女, いう子), nor ends on a hiragana
# particle or auxiliary (夢か, 山だ). Only hiragana tokens are checked: kanji
# tags are unreliable here (see _can_start), and 也 in 貴也 is tagged 助動詞.
_NAME_START_GRAMMAR = frozenset({"助詞", "助動詞", "連体詞", "動詞"})
_NAME_END_GRAMMAR = frozenset({"助詞", "助動詞"})

# 家の前, 山の上 and 坂の下 are place names in the wordsets, and in nearly every
# line 'in front of the house'. A name ending on の + a position word is refused;
# other names with an inner の (鞆の浦, 華厳の滝) still merge.
_NAME_POSITION_TAILS = frozenset({"前", "上", "下", "奥", "中", "横", "後", "裏", "外", "内", "隣", "側", "脇"})


def _hiragana_tagged(token, pos1s: frozenset[str]) -> bool:
    surface = getattr(token, "surface", None) or ""
    return bool(_HIRAGANA_RE.fullmatch(surface)) and _pos1(token) in pos1s


class NameSpanMatcher(CompoundDictionaryMatcher):
    """Merge exact name-wordset spans without trusting UniDic word forms.

    Reuses the dictionary matcher's parameterized candidate generation, batched
    lookup cache, greedy longest-first selection, and synthetic tokens. Name
    candidates differ at one load-bearing seam: every token contributes its
    raw surface. Deinflecting an adjective-misclassified tail would turn
    ``憂+太`` into ``憂太い`` and miss the actual name ``憂太``.

    A match is emitted as a nominal token so downstream mining uses the exact
    source spelling. Honorifics provide no special license; they merge only
    when the complete span itself exists in the injected name lookup.
    """

    _default_max_span_chars = _MAX_NAME_SPAN_CHARS
    _fixed_forms = False
    _spoken_idioms = False

    @staticmethod
    def _candidate_for_tail(prefix: str, tail, joined: str, head) -> Alternatives:
        if prefix.endswith("の") and tail.surface in _NAME_POSITION_TAILS:
            return ()
        return ((joined, "B"),)

    def _can_start_at(self, tokens: list, i: int) -> bool:
        return self._can_start(tokens[i])

    def _can_start(self, token) -> bool:
        # Exact raw-name attestation, not UniDic POS, licenses this boundary.
        # The combined-line probe can tag 狗 as 接尾辞 and 巻 as 固有名詞 even
        # though the same source phrase tags both 普通名詞; trusting either tag
        # here would recreate the fragment bug at a different POS boundary.
        # A hiragana grammar word is the exception (_NAME_START_GRAMMAR): the
        # wordsets hold その子, な子, てる子 and いう子, which glue a word to
        # a noun in その子が, きれいな子, 寝てる子 and ああいう子.
        surface = getattr(token, "surface", None)
        return bool(surface and surface.strip()) and not _hiragana_tagged(token, _NAME_START_GRAMMAR)

    @staticmethod
    def _can_end(token) -> bool:
        # 夢か, 山だ, 時や and 流され (流さ|れ) are names too; ending on a
        # hiragana particle or auxiliary they are a word and its grammar.
        surface = getattr(token, "surface", None)
        return bool(surface and surface.strip()) and not _hiragana_tagged(token, _NAME_END_GRAMMAR)

    def _build_synthetic(self, span: list, headword: str, kind: str) -> CompoundSyntheticToken:
        surface = "".join(token.surface for token in span)
        kana = "".join(extract_reading(token) for token in span)
        return CompoundSyntheticToken(
            surface=surface,
            pos1="名詞",
            pos2="普通名詞",
            lemma=headword,
            kana=kana,
        )
