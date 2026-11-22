"""Pure token-level morphology shared by subtitle parsing.

Compound-merge passes, lemma/reading extraction and the POS/subtype
inclusion gate, relocated out of ``subtitle_parser`` so they are usable
without the file-parsing/caching service. Everything here operates on
fugashi-shaped tokens (``.surface``, ``.feature.{pos1,pos2,lemma,kana,orthBase}``)
and performs no I/O.

Import direction is one-way: ``subtitle_parser`` imports from this module;
this module must never import ``subtitle_parser``.
"""

import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache
from types import SimpleNamespace
from typing import Any, Iterator, NamedTuple

from anki_miner.languages.token import LanguageToken
from anki_miner.utils.ja_normalize import is_cjk_ideograph
from anki_miner.utils.text_utils import hiragana_to_katakana, is_kana_only, is_plain_kana, katakana_to_hiragana

# Batch attested-readings probe (DefinitionService.offline_term_readings):
# term -> readings, best-first, hiragana-folded. See attest_merged_readings.
ReadingLookup = Callable[[list[str]], dict[str, list[str]]]


# Token attribute guards. A fugashi token whose ``.feature`` lacks a field is a
# normal OOV shape, so a per-site log line would write one record per token and
# bury the run. A token of the WRONG KIND, though -- a jieba tag object, a test
# double built from SimpleNamespace, a language pack whose engine loaded a
# different token class -- makes EVERY guard below fire and silently empties the
# parse, which is exactly the "No words found" report that has no other trace.
# Counting by guard name and emitting the counts once with the parse receipt
# (``SubtitleParser._log_parse_probe_timing``) separates the two. Increments are
# plain ``Counter`` bumps: a racing parse can lose one, and a lost count in a
# diagnostic costs nothing worth a lock on this hot a path.
_ATTRIBUTE_GUARDS: Counter[str] = Counter()


def _guard(name: str) -> None:
    """Account one ``except AttributeError`` guard firing, by guard name."""
    _ATTRIBUTE_GUARDS[name] += 1


def drain_attribute_guard_counts() -> list[str]:
    """Return ``name:count`` for every guard that fired, busiest first, and reset.

    Draining rather than reading keeps the counts scoped to one parse, so a
    receipt reports the file it belongs to instead of the process lifetime.
    """
    counts = _ATTRIBUTE_GUARDS.most_common()
    _ATTRIBUTE_GUARDS.clear()
    return [f"{name}:{count}" for name, count in counts]


@dataclass(frozen=True)
class AttestedReadingResolution:
    """Result of comparing one derived reading with exact-headword readings."""

    reading: str | None
    ambiguous: bool = False


def resolve_attested_reading(
    derived_reading: str,
    attested_readings: list[str],
) -> AttestedReadingResolution:
    """Keep an attested reading or replace it only from a unique dictionary row.

    Readings are hiragana-folded and deduplicated before applying the policy.
    A multi-reading mismatch is deliberately unresolved: bulk mining has no
    semantic selection step, so choosing by dictionary order or string distance
    would silently stamp an arbitrary homograph reading onto the card.
    """
    derived = katakana_to_hiragana(derived_reading)
    folded = list(dict.fromkeys(katakana_to_hiragana(reading) for reading in attested_readings))
    if not folded:
        return AttestedReadingResolution(None)
    if derived in folded:
        return AttestedReadingResolution(derived)
    if len(folded) == 1:
        return AttestedReadingResolution(folded[0])
    return AttestedReadingResolution(None, ambiguous=True)


# Batch offline existence probe (DefinitionService.offline_terms_exist): a list
# of candidate surfaces -> the attested SUBSET. Injected into the compound-merge
# gate (merge_compound_suffixes) so morphology stays SQLite-free — the same
# dependency-injection pattern as ReadingLookup / attest_merged_readings. When
# None, the merge passes run UNGATED (output byte-identical to the pre-gate
# behavior); when present, the noun-suffix and prefix passes bail any synthetic
# compound the dictionary does not attest. One deliberate exception: the prefix
# pass may mint a TEMPORARY unattested prefix synthetic when the prefix+root
# plus its immediate nominal-suffix run IS attested (e.g. 不可能性 without
# 不可能) — the final chain is still validated and minted by the noun-suffix
# pass, so nothing unattested survives to the output.
AttestLookup = Callable[[list[str]], set[str]]

# Folded term -> (content, tags) rows from the enabled offline chain (spec R36).
# Only the type lives here: the he stage adds the parser's ``form_lookup`` seam
# that builds one; every other stage passes None in its place.
FormLookup = Callable[[list[str]], dict[str, list[tuple[str, str]]]]

# A language's parser-side token pass (spec §4.3 item 2(b)): the raw tagger
# tokens, the parser's memoised attestation probe (None without a dictionary)
# and a form lookup (None until R36 lands), returning the tokens the line mines.
TokenPostPass = Callable[[list[Any], AttestLookup | None, FormLookup | None], list[Any]]

_NOMINAL_SUFFIX_POS2 = {"名詞的", "形状詞的", "副詞的"}


# Whitelist of 接頭辞 surfaces that productively form compounds with
# 名詞/形状詞 roots. Used by _merge_prefix_compounds to avoid false positives
# from rare/unproductive 接頭辞 entries in unidic.
_PREFIX_WHITELIST = frozenset({"無", "不", "非", "反", "超", "未", "新", "旧", "全", "半", "副", "元", "再", "最"})

# 接尾辞(名詞的) surfaces that nominalize a preceding 動詞 連用形 stem
# (e.g. 言い+方 → 言い方). Restricted to a small productive set; 者/事/物
# etc. are not included because they tokenize differently and would
# over-merge.
_VERB_NOMINALIZER_SUFFIXES = frozenset({"方", "手", "様"})


# Honorific-kinship special readings. UniDic tokenizes お兄ちゃん as
# お(接頭辞) + 兄(名詞, kana=アニ) + ちゃん(接尾辞・名詞的), so the noun-suffix
# merge concatenates the *isolated* head kana (アニ) with the suffix — yielding
# あにちゃん instead of the contextual にいちゃん. The head only takes the special
# reading when immediately followed by one of the licensing kinship honorifics;
# standalone 兄 keeps アニ. Katakana to match feature.kana. Licensing set
# deliberately EXCLUDES 上/君/貴/親 (兄上=あにうえ, 兄貴=あにき, 父親=ちちおや keep the
# plain reading — those suffixes are not honorific address forms).
_HONORIFIC_SUFFIXES = frozenset({"ちゃん", "さん", "さま", "様"})
_KINSHIP_HEAD_READINGS: dict[str, tuple[str, frozenset[str]]] = {
    "兄": ("ニイ", _HONORIFIC_SUFFIXES),  # お兄ちゃん にい (probe: 兄+ちゃん → アニチャン)
    "姉": ("ネエ", _HONORIFIC_SUFFIXES),  # お姉ちゃん ねえ (probe: 姉+ちゃん → アネチャン)
    "父": ("トウ", _HONORIFIC_SUFFIXES),  # お父さん とう (probe: 父+さん → チチサン)
    "母": ("カア", _HONORIFIC_SUFFIXES),  # お母さん かあ (probe: 母+さん → ハハサン)
}
# Probe-confirmed NON-members (already correct, must NOT be added): 娘さん=むすめさん,
# 息子さん=むすこさん, おじさん=おじさん, じいちゃん=じいちゃん, 婆ちゃん=ばあちゃん.


def resolve_special_reading(head_surface: str, next_surface: str | None) -> str | None:
    """Corrected katakana head reading for a kinship head licensed by its suffix.

    Returns the special katakana reading of ``head_surface`` (兄/姉/父/母) when it
    is immediately followed by a licensing honorific suffix (``next_surface`` in
    ちゃん/さん/さま/様); otherwise ``None`` (the caller keeps the UniDic reading).
    Pure and data-driven — the single choke point shared by the compound-merge
    pass (Expression/audio/frequency) and ``apply_special_readings`` (sentence
    furigana/reading), so every layer agrees on にい/ねえ/とう/かあ.
    """
    entry = _KINSHIP_HEAD_READINGS.get(head_surface)
    if entry is None or next_surface is None:
        return None
    special_kana, licensing = entry
    return special_kana if next_surface in licensing else None


# Curated per-spelling reading corrections for unidic-lite misreadings. Keyed by
# (card-front spelling, hiragana-folded UniDic reading) → corrected hiragana
# reading; unconditional, applied in ``_emit_word``'s Expression fields. One row
# is left. Every other reading the tagger gets wrong on a token of its own (私,
# 明日, 日本, 仏, 蛙, 一日, 言う, 不味い) is a row of ``_READING_CONTEXT``, corrected
# on the raw stream, so a merged spelling (私たち, 日本語, そう言う) inherits it
# and the sentence ruby agrees with the card.
#   (込む, ごむ) → こむ: the isolated 込む verb misreads as the loanword ゴム
#       (rubber); compounds (飲み込む→のみこむ) read correctly and are untouched.
_READING_OVERRIDES: dict[tuple[str, str], str] = {
    ("込む", "ごむ"): "こむ",
}


def resolve_reading_override(spelling: str, derived_reading: str) -> str | None:
    """Corrected hiragana reading for a spelling unidic-lite misreads, else ``None``.

    Looks up ``(spelling, derived_reading)`` — the card-front spelling paired with
    its hiragana-folded UniDic reading — in the curated ``_READING_OVERRIDES``
    table and returns the corrected hiragana reading on a hit, ``None`` otherwise
    (the caller keeps the UniDic reading). Pure, context-free and dictionary-free:
    every listed spelling reads the same wrong value in every context, so no
    licensing suffix is consulted. A SEPARATE sibling of the kinship
    ``resolve_special_reading`` above — that returns context-licensed katakana;
    this returns an unconditional hiragana correction.
    """
    return _READING_OVERRIDES.get((spelling, derived_reading))


def apply_special_readings(tokens: list) -> list:
    """Return ``tokens`` with kinship-head kana overridden per the special table.

    Scans adjacent RAW tokens (never merged): where a head is licensed by the
    next token's surface, the head is replaced by a ``SyntheticToken`` carrying
    the special katakana reading. Surfaces are left unchanged, so downstream
    ``str.find`` cursoring and bold-offset math (wrap_target_furigana_from_tokens)
    stay byte-identical. All other tokens pass through by identity. Used for the
    Sentence furigana/reading/bold path, where 兄 is still an isolated token
    (the Expression path is corrected upstream in ``_merge_noun_suffixes``).
    """
    if not tokens:
        return tokens
    out: list = []
    n = len(tokens)
    for i, tok in enumerate(tokens):
        next_surface = tokens[i + 1].surface if i + 1 < n else None
        try:
            special = resolve_special_reading(tok.surface, next_surface)
        except AttributeError:
            _guard("apply_special_readings#1")
            special = None
        if special is None:
            out.append(tok)
            continue
        try:
            pos1 = tok.feature.pos1
            pos2 = tok.feature.pos2
            lemma = extract_lemma(tok)
        except AttributeError:
            _guard("apply_special_readings#2")
            out.append(tok)
            continue
        out.append(
            SyntheticToken(
                surface=tok.surface,
                pos1=pos1,
                pos2=pos2,
                lemma=lemma,
                kana=special,
            )
        )
    return out


class SyntheticToken:
    """Duck-typed token replacement for merged compounds.

    Mimics fugashi token attribute access (.surface,
    .feature.{pos1,pos2,lemma,kana}). Subclassed by
    ``compound_matcher.CompoundSyntheticToken`` for dictionary-attested
    merges.
    """

    __slots__ = ("surface", "feature")

    def __init__(self, surface: str, pos1: str, pos2: str, lemma: str, kana: str, *, kana_locked: bool = False):
        self.surface = surface
        self.feature = SimpleNamespace(pos1=pos1, pos2=pos2, lemma=lemma, kana=kana, kana_locked=kana_locked)


# Back-compat alias for the pre-rename private name.
_SyntheticToken = SyntheticToken


def extract_lemma(word_token) -> str:
    """Extract lemma (dictionary form) from word token.

    Args:
        word_token: MeCab word token

    Returns:
        Lemma string
    """
    try:
        lemma = word_token.feature.lemma or word_token.surface
    except AttributeError:
        _guard("extract_lemma")
        lemma = word_token.surface

    # Strip unidic's disambiguator tail: an English gloss
    # ("スクランブル-scramble", "ロック-rock（音楽）" — the fullwidth parens
    # defeat a plain isascii() check, "メリーゴーランド-merry-go-round" — the
    # gloss itself is hyphenated, hence splitting on the FIRST hyphen) or a
    # POS-name tail. The tail is a POS decorator when it EQUALS the coarse pos1
    # ("君-代名詞") or ENDS WITH it ("引く-他動詞", "落ちる-自動詞" — unidic tags
    # transitivity with the fine 他動詞/自動詞 while pos1 is the coarse 動詞).
    # Decorated lemmas miss every lemma-fallback lookup (frequency/pitch/offline
    # definition existence) AND block mining_base folds keyed on a clean headword
    # (引ける→引く). Japanese name segments (メル-ビル) end with neither an ASCII
    # letter nor pos1 and are kept intact. A duck token from a non-ja tokenizer
    # carries its FINAL lemma: "well-known", "kupu-kupu", "e-mail" are words, not
    # UniDic disambiguators, so the strip is UniDic-only (spec S6).
    if "-" in lemma and not isinstance(word_token, LanguageToken):
        head, _, tail = lemma.partition("-")
        pos1 = getattr(getattr(word_token, "feature", None), "pos1", None)
        is_pos_tail = bool(pos1) and (tail == pos1 or tail.endswith(pos1))
        if head and tail and (any(c.isascii() and c.isalpha() for c in tail) or is_pos_tail):
            lemma = head

    return str(lemma)


def extract_orth_base(word_token) -> str:
    """Extract the dictionary form in the token's own orthography.

    UniDic's ``lemma`` is the canonical headword and silently normalizes
    orthographic kanji variants (乞う→請う, 喰らう→食らう); ``orthBase``
    keeps the spelling the source text used (乞わ→乞う), which is what the
    card Expression must show. Yomitan behaves the same way: it deinflects
    the raw sentence string and never consults a normalized lemma.

    Falls back to ``extract_lemma`` when the field is missing (synthetic
    ``_SyntheticToken`` features have no ``orthBase`` attribute) or falsy
    (fugashi maps unidic's ``*`` placeholder to ``None`` on OOV tokens);
    the fallback inherits extract_lemma's surface fallback and ASCII-gloss
    stripping. No gloss stripping on the orthBase branch — the English
    gloss tail rides on the lemma/lForm fields only.
    """
    try:
        orth_base = word_token.feature.orthBase
    except AttributeError:
        _guard("extract_orth_base")
        orth_base = None
    if not orth_base:
        return extract_lemma(word_token)
    return str(orth_base)


# Potential-verb paradigm (godan e-row + ら抜き), adjective ク-form pairs and the
# one-kanji サ変 alternants (愛す/愛せる←愛する): (derived orthBase suffix, base
# lemma suffix). Mirrors the potential rules in japanese_transforms.py (the
# "potential" transform); kept as data here because mining folds a HEADWORD
# (orthBase→lemma), not running the deinflection engine on text.
_FOLD_SUFFIX_PAIRS = (
    ("える", "う"),
    ("ける", "く"),
    ("げる", "ぐ"),
    ("せる", "す"),
    ("てる", "つ"),
    ("ねる", "ぬ"),
    ("べる", "ぶ"),
    ("める", "む"),
    ("れる", "る"),
    ("し", "い"),
    ("す", "する"),
    ("せる", "する"),
)


def mining_base(word_token) -> str:
    """orthBase for the card front, folded to lemma for derived sub-lemma entries.

    unidic gives potential verbs (保てる←保つ), ra-nuki forms (見れる←見る),
    archaic i-adjective bases (良し←良い), classical 連体形 ク-stems
    (美しき: orthBase 美し←美しい) and the godan/potential forms of one-kanji サ変
    verbs (愛される/愛すべき: 愛す, 愛せない: 愛せる ← 愛する) their own orthBase
    while lemma points at the parent headword. Mining orthBase makes a 保てる card
    distinct from an existing 保つ card; folding to lemma dedupes them. Applies only to 動詞 /
    形容詞 — the only POS whose mined_form reads orth_base (select_mined_form).

    Trigger: the lemma reading (lForm) and orthBase reading (kanaBase) diverge,
    hiragana-folded. NOTE this is strictly "readings diverge", not "is a
    conjugated derivative" — polyphonic entries like 言う (イウ vs ユウ) also
    fire, harmlessly, because lemma and orthBase are the same string.

    Guard: fold only when the lemma is exactly the orthBase with its derived
    suffix swapped for the paradigm base suffix (``_FOLD_SUFFIX_PAIRS``).
    Everything outside the conjugating suffix must match the lemma
    byte-for-byte, so unidic lemma canonicalization can never leak into the
    card front: kanji swaps (帰れる→lemma 返る, 出逢える→出会う), okurigana
    variants (表せる→表わす, 行なえる→行う) and modern→archaic じる/ずる
    (信じる→信ずる) all keep their source orthBase — the same
    variant-preservation contract as Issues #19/#5 (乞う not 請う, readings
    equal, never triggers the fold at all).

    Ichidan potential/passive 〜られる never reaches this code: MeCab
    tokenizes 食べられる as 食べ + られる auxiliary, so Yomitan's
    potential-vs-passive ambiguity does not exist in this pipeline.

    Missing/'*'/non-string readings (synthetic compound tokens, OOV) never
    fold. The isinstance(str) checks are load-bearing: MagicMock-based token
    fakes auto-create truthy attribute objects.
    """
    orth_base = extract_orth_base(word_token)
    feature = getattr(word_token, "feature", None)
    if getattr(feature, "pos1", None) not in ("動詞", "形容詞"):
        return orth_base
    l_form = getattr(feature, "lForm", None)
    kana_base = getattr(feature, "kanaBase", None)
    if not isinstance(l_form, str) or not isinstance(kana_base, str):
        return orth_base
    if l_form in ("", "*") or kana_base in ("", "*"):
        return orth_base
    from anki_miner.utils.text_utils import katakana_to_hiragana

    if katakana_to_hiragana(l_form) == katakana_to_hiragana(kana_base):
        return orth_base
    lemma = extract_lemma(word_token)
    if not lemma or not orth_base:
        return orth_base
    # Classical 形容詞 連体形 ク-stem (美しき: orthBase 美し, lemma 美しい): unidic gives
    # the ク-stem its own orthBase while lemma is the full い-form. Fold to lemma so a
    # 美しき card dedups against 美しい. 形容詞-only and append-only (lemma == stem + い)
    # — the stem is byte-identical to the lemma minus its final い, so no kanji/
    # okurigana variant can leak (unlike the swap pairs below). 良し-class ク-forms
    # carry orthBase 良し and fold via the ('し','い') swap pair instead.
    if getattr(feature, "pos1", None) == "形容詞" and orth_base + "い" == lemma:
        return lemma
    for derived, base in _FOLD_SUFFIX_PAIRS:
        if orth_base.endswith(derived) and len(orth_base) > len(derived) and orth_base[: -len(derived)] + base == lemma:
            return lemma
    return orth_base


def source_spelling_fold(orth_base: str, l_form: str, kana_base: str) -> str | None:
    """The base verb of a potential in the line's own spelling, when the readings prove it.

    ``mining_base`` folds a potential only when the tagger's lemma is its
    orthBase with the suffix swapped byte for byte. UniDic often spells that
    lemma otherwise (帰れる → 返る, やり直せる → 遣り直す), so the fold is
    built here on the source stem instead (帰る, やり直す), and only when the
    same swap on the orthBase reading gives the lemma reading (カエレル →
    カエル). The caller decides whether to use it (attestation).
    """
    reading = katakana_to_hiragana(kana_base)
    lemma_reading = katakana_to_hiragana(l_form)
    for derived, base in _FOLD_SUFFIX_PAIRS:
        if not (orth_base.endswith(derived) and len(orth_base) > len(derived) and reading.endswith(derived)):
            continue
        if reading[: -len(derived)] + base == lemma_reading:
            return orth_base[: -len(derived)] + base
    return None


# Godan 未然形 a-row kana → the dictionary-form u-row kana (行か → 行く).
_A_ROW_TO_U_ROW = {
    "か": "く",
    "が": "ぐ",
    "さ": "す",
    "た": "つ",
    "な": "ぬ",
    "ば": "ぶ",
    "ま": "む",
    "ら": "る",
    "わ": "う",
}
# Ichidan stem endings: the e/i-row kana before a short causative さす (食べさす).
_ICHIDAN_STEM_KANA = frozenset("えけげせぜてでねべぺめれいきぎしじちにびみり")


def short_causative_bases(orth_base: str) -> list[str]:
    """Base verbs a short causative headword can come from, in the line's spelling.

    UniDic gives the short causative its own -す lexeme (行かさ|れ|た: 行かす,
    食べさす), which a dictionary rarely lists. 行かす → 行く; 食べさす → 食べる,
    only when a kana e/i-row stem proves the ichidan reading (出さす stays 出す).
    The caller keeps the -す front when it is attested and uses the first
    attested base otherwise.
    """
    bases: list[str] = []
    if not orth_base.endswith("す") or len(orth_base) < 3:
        return bases
    if orth_base.endswith("さす") and orth_base[-3] in _ICHIDAN_STEM_KANA:
        bases.append(orth_base[:-2] + "る")
    u_row = _A_ROW_TO_U_ROW.get(orth_base[-2])
    if u_row is not None:
        bases.append(orth_base[:-2] + u_row)
    return bases


def extract_reading(word_token) -> str:
    """Extract kana reading from word token.

    Args:
        word_token: MeCab word token

    Returns:
        Kana reading string
    """
    try:
        surface = word_token.surface
        if is_plain_kana(surface):
            # A surface written only in kana is its own reading. unidic-lite's
            # ``kana`` is not always the surface's kana (ズルい → ズリイ, かんぞう →
            # カンソウ, ていあん → エイン), and that value would become the card's
            # Reading, the audio identity pair and the sentence reading.
            return hiragana_to_katakana(surface)
        return str(word_token.feature.kana or surface)
    except AttributeError:
        _guard("extract_reading")
        return str(word_token.surface)


def iter_token_spans(text: str, tokens: list) -> Iterator[tuple[Any, int, int]]:
    """Yield ``(token, start, end)`` for each token locatable in ``text``.

    Locates each token's char span via ``str.find`` from a running
    cursor. MeCab silently drops whitespace from the token stream, so
    naive ``cursor += len(surface)`` walking drifts left by the count of
    preceding spaces and misaligns every downstream offset (bold
    wrapping, surface_start/end). Issue #20.

    Tokens whose surface is not find-able are dropped (defensive: should
    not happen for unmodified MeCab surfaces). A merged compound whose
    components were whitespace-separated in the source is also dropped, but
    its whitespace-stitched source run is consumed first; otherwise a later
    identical contiguous surface could be stolen by ``str.find``. This locator
    is the single source of truth for that drop-and-consume rule:
    ``parse_subtitle_file``, ``parse_subtitle_file_with_index`` AND
    ``count_lemmas`` must all route through it, or the count-vs-mine
    sets diverge and a reported occurrence count over-promises (T-38).
    """
    cursor = 0
    for token in tokens:
        surface = token.surface
        if not surface or any(char.isspace() for char in surface):
            idx = text.find(surface, cursor)
            if idx == -1:
                continue
            tok_end = idx + len(surface)
            cursor = tok_end
            yield token, idx, tok_end
            continue

        idx = -1
        tok_end = -1
        stitched = False
        search_from = cursor
        while search_from < len(text):
            candidate = text.find(surface[0], search_from)
            if candidate == -1:
                break
            source_pos = candidate
            surface_pos = 0
            saw_whitespace = False
            while source_pos < len(text) and surface_pos < len(surface):
                if text[source_pos] == surface[surface_pos]:
                    source_pos += 1
                    surface_pos += 1
                elif text[source_pos].isspace():
                    source_pos += 1
                    saw_whitespace = True
                else:
                    break
            if surface_pos == len(surface):
                idx = candidate
                tok_end = source_pos
                stitched = saw_whitespace
                break
            search_from = candidate + 1
        if idx == -1:
            continue
        cursor = tok_end
        if stitched:
            continue
        yield token, idx, tok_end


def merge_compound_suffixes(
    tokens: list, attest: AttestLookup | None = None, form_lookup: FormLookup | None = None
) -> list:
    """Run all compound-merge passes in dependency order.

    Order matters:
    0. retag_mistagged_tokens  — corrects a tag the next token disproves
       (あの filler before a noun → 連体詞, 今 prefix before a space → 名詞),
       so every pass below sees it; with ``form_lookup``, an unknown katakana
       noun the dictionary lists (スマホ) also gets its surface as lemma.
    1. _merge_prefix_compounds  — 接頭辞 + 名詞/形状詞 (e.g. 不+可能 → 不可能).
       Must run first so that downstream 名詞-suffix merge sees the
       synthetic 不可能 (pos1=名詞) as a valid head and chains correctly
       into 不可能性, 不可能的, etc.
    2. _merge_noun_suffixes     — 名詞 + 接尾辞(名詞的/形状詞的/副詞的)
       chains (e.g. 刑務+所 → 刑務所, 入院+中+的 → 入院中的).
    3. _merge_verb_nominalizers — 動詞(連用形) + 接尾辞(名詞的) where the
       suffix is a verb-stem nominalizer (方/手/様). Independent of (1)
       and (2) so order is irrelevant.

    ``attest`` gates passes 1 and 2: a prefix synthetic is minted when the
    dictionary attests either its surface or the complete immediate noun-suffix
    chain that needs it as a temporary head; the noun-suffix pass remains the
    authority that validates and mints that final chain. Otherwise a synthetic
    is minted only when the dictionary attests its surface (or, for the
    noun-suffix pass, it is a curated kinship compound — 兄ちゃん — whose reading
    must be preserved even though no dictionary attests it). An unattested
    candidate bails the WHOLE greedy chain to its bare components, letting the
    downstream dictionary matcher recover the longest attested sub-span
    (入院中的 → 入院中). Pass 3 mints only an attested compound: an unattested
    戦い方 gets no definition and is dropped, costing the line its verb.
    ``attest=None`` (the default that keeps every existing direct caller
    byte-identical) leaves all three passes ungated.
    """
    tokens = retag_mistagged_tokens(tokens, form_lookup)
    tokens = _merge_prefix_compounds(tokens, attest)
    tokens = _merge_noun_suffixes(tokens, attest)
    tokens = _merge_verb_nominalizers(tokens, attest)
    return tokens


#: What 抱く takes when it reads いだく ('to harbour'): a feeling, a hope, a doubt,
#: followed by its particle. Tuned on the dev shows: nouns ending in 意 (好意,
#: 殺意), 感, 心, 念, 望, 情, 志, 想, 夢 or 欲, the listed words, and the kana-final
#: 恨み, 思い, 憎しみ. Everything else is だく ('to hold in the arms').
_IDAKU_OBJECT = re.compile(
    "(?:[意感心念望情志想夢欲]|疑問|不満|期待|興味|恐怖|不審|覚悟|意識|屈辱|意思|虚無|憎悪|嫌悪|情熱|義憤|欲求"
    "|不信|不安|印象|疑い|恨み|怒り|悩み|願い|望み|恐れ|憧れ|思い|想い|しみ|気持ち)(?:を|も|は|すら|さえ)$"
)
_IDAKU_KANA_FIELDS = ("kana", "kanaBase", "lForm", "pron", "pronBase", "form", "formBase")


def reread_daku_tokens(tokens: list) -> list:
    """Read 抱く as だく where its object is not a feeling (彼女を抱いた, 抱かれたい).

    unidic-lite reads every 抱く イダク, the literary 'to harbour', which is
    right only before an abstract object (夢を抱く, 不安を抱いている:
    ``_IDAKU_OBJECT``); most lines mean 'to hold in the arms'. On the raw
    stream, so the card and the sentence ruby agree. A token of the verb
    written 抱… whose reading starts イダ loses that イ in every reading field;
    抱きしめる and 抱っこ are their own words and never match.
    """
    out: list = []
    before = ""
    for token in tokens:
        surface = token.surface if isinstance(token.surface, str) else ""
        feature = getattr(token, "feature", None)
        context = before.rstrip()
        white_space = getattr(token, "white_space", "")
        before += (white_space if isinstance(white_space, str) else "") + surface
        if (
            not surface.startswith("抱")
            or getattr(feature, "pos1", None) != "動詞"
            or getattr(feature, "lemma", None) != "抱く"
            or not hasattr(feature, "_replace")
            or _IDAKU_OBJECT.search(context)
        ):
            out.append(token)
            continue
        fields = {
            name: value[1:]
            for name in _IDAKU_KANA_FIELDS
            if isinstance(value := getattr(feature, name, None), str) and value.startswith("イダ")
        }
        out.append(RetaggedToken(token, fields) if fields else token)
    return out


#: 表 as 'table' when it stands alone: after a demonstrative (この表をもとに) or
#: before a verb of making one (表を作った, 表にまとめる).
_HYOU_BEFORE = re.compile("(?:この|その|あの|どの|下の|上の|次の)$")
_HYOU_AFTER = re.compile("(?:を|に)(?:作|書|まと|もと|埋|記入)")
_OMOTE_KANA_FIELDS = ("kana", "kanaBase", "lForm", "pron", "pronBase", "form", "formBase")


def reread_omote_tokens(tokens: list) -> list:
    """Read a 表 that stands alone as おもて (表へ出ろ, 表で待ってる, 表と裏).

    unidic-lite reads every noun 表 ヒョウ, 'table', which is right as the tail
    of a compound (計画表, メンバー表: a noun, suffix or prefix token directly
    before it) and in the few lines about a table (``_HYOU_BEFORE``,
    ``_HYOU_AFTER``). On the dev shows 85 in 100 stand alone, and about 94 in
    100 of those mean the front, the outside or the public face. 回表 is the
    top of an inning and reads おもて too. On the raw stream, so the card and
    the sentence ruby agree.
    """
    out: list = []
    before = ""
    for i, token in enumerate(tokens):
        surface = token.surface if isinstance(token.surface, str) else ""
        feature = getattr(token, "feature", None)
        white_space = getattr(token, "white_space", "")
        spaced = isinstance(white_space, str) and bool(white_space)
        context = before
        before += (white_space if isinstance(white_space, str) else "") + surface
        if surface != "表" or getattr(feature, "pos1", None) != "名詞" or getattr(feature, "kana", None) != "ヒョウ":
            out.append(token)
            continue
        previous = tokens[i - 1] if i else None
        attached = (
            previous is not None
            and not spaced
            and getattr(getattr(previous, "feature", None), "pos1", None) in ("名詞", "接尾辞", "接頭辞")
            and previous.surface != "回"
        )
        after = "".join(t.surface for t in tokens[i + 1 : i + 3] if isinstance(t.surface, str))
        if attached or _HYOU_BEFORE.search(context) or _HYOU_AFTER.match(after) or not hasattr(feature, "_replace"):
            out.append(token)
            continue
        out.append(RetaggedToken(token, dict.fromkeys(_OMOTE_KANA_FIELDS, "オモテ")))
    return out


class _ReadingRow(NamedTuple):
    """One row of ``_READING_CONTEXT``: a reading the tagger gave and what it becomes.

    ``word`` is the token surface, ``pos1`` the part of speech the tagger must
    have given, ``old`` the katakana it gave (a prefix of ``feature.kana``) and
    ``new`` what replaces that prefix; with ``base`` set, ``word`` is instead the
    dictionary form of a verb or adjective (the tagger lemma, else the form as
    written) and the token must start with its first character. A row whose
    ``new`` equals ``old`` keeps
    the tagger's reading. Every test a row names must hold. ``before`` is
    searched in the line up to the token, trailing spaces dropped; ``after`` is
    matched at the start of the line after it, so a subtitle space there fails
    it; ``prev`` and ``nxt`` name the class of the token attached directly
    before and after (its part of speech; 数詞 for a numeral before, 助数詞 for a
    counter after); ``line`` is searched anywhere in the line.
    """

    word: str
    pos1: tuple[str, ...]
    old: str
    new: str
    before: re.Pattern[str] | None
    after: re.Pattern[str] | None
    prev: tuple[str, ...]
    nxt: tuple[str, ...]
    line: re.Pattern[str] | None
    base: bool


def _reading_row(
    word: str,
    pos1: tuple[str, ...],
    old: str,
    new: str,
    *,
    before: str = "",
    after: str = "",
    prev: tuple[str, ...] = (),
    nxt: tuple[str, ...] = (),
    line: str = "",
    base: bool = False,
) -> _ReadingRow:
    """A ``_READING_CONTEXT`` row with its patterns compiled."""
    return _ReadingRow(
        word,
        pos1,
        old,
        new,
        re.compile(before) if before else None,
        re.compile(after) if after else None,
        prev,
        nxt,
        re.compile(line) if line else None,
        base,
    )


_WHAT_POS = ("代名詞", "名詞")
_NOUN_POS = ("名詞",)
_CONTEXT_KANA_FIELDS = ("kana", "kanaBase", "lForm", "pron", "pronBase", "form", "formBase")

#: Readings the line decides. Rows of one word are tried in order and the first
#: whose tests all hold decides. Every row is one hand-written statement about
#: Japanese; none is computed from subtitle files.
_READING_CONTEXT: tuple[_ReadingRow, ...] = (
    # 何: the tagger says なん wherever it stands. It is なん before the copula and
    # the particles で, と, の, て, って, じゃ, や (何だ, 何ですか, 何の話, 何て, 何なの,
    # 何なら), before ら and before a counter (何回, 何人); なに everywhere else (何を,
    # 何が, 何か, 何も, 何に, 何よ, a verb, the line end). の must be the next TOKEN,
    # the particle: in 何のんきな it starts another word. The other kana are read
    # off the text, because the tagger cuts 何やかんや and 何なりと in odd places.
    # 何と with a second question word or a partner's verb is なにと (何と何, 何と戦う).
    _reading_row(
        "何", _WHAT_POS, "ナン", "ナニ", after="と(?:何|誰|だれ|どれ|どっち|どちら|戦|闘|比べ|一緒|交換|間違|勘違|関係)"
    ),
    _reading_row("何", _WHAT_POS, "ナン", "ナン", nxt=("助詞",), after="の"),
    _reading_row(
        "何",
        _WHAT_POS,
        "ナン",
        "ナン",
        after="(?:だ|で|と|て|って|な[のんらり]|じゃ|ら(?![しれ])|や(?![っりるれろら])|っ?[すス](?:か|よ|ね)|ちゅう|つう|っつ)",
    ),
    _reading_row(
        "何",
        _WHAT_POS,
        "ナン",
        "ナン",
        after="(?:[度回時人日年月分秒歳才個本枚匹階番倍割件円曜十百千万億遍等]|べん|ぺん|どき|[かヶカヵ箇]月)",
    ),
    _reading_row("何", _WHAT_POS, "ナン", "ナン", nxt=("助数詞",)),
    _reading_row("何", _WHAT_POS, "ナン", "ナニ"),
    # 私: the tagger says わたくし wherever it stands; a card takes わたし, as the
    # ワタシ fold in models/word.py does. The humble 私ども and 私め keep わたくし,
    # and so does a humble or ladylike line (ございます, 申します, 参ります, 存じます,
    # …ですわ, ごきげんよう; the greetings ありがとうございます, おはようございます and
    # おめでとうございます are everybody's).
    _reading_row("私", ("代名詞",), "ワタクシ", "ワタクシ", after="(?:ども|共)"),
    _reading_row("私", ("代名詞",), "ワタクシ", "ワタクシ", after="め", nxt=("接尾辞",)),
    _reading_row(
        "私",
        ("代名詞",),
        "ワタクシ",
        "ワタクシ",
        line="(?<!ありがとう)(?<!おはよう)(?<!めでとう)ございま|申しま|申し上げ|参りま|致しま|いたしま|存じま|ですわ|ますわ(?!け)|ませんわ|ましたわ|ましてよ|くださいまし(?!た)|あそばせ|ごきげんよう",
    ),
    _reading_row("私", ("代名詞",), "ワタクシ", "ワタシ"),
    # 明日: the tagger says the formal あす; a card takes the everyday あした.
    _reading_row("明日", _NOUN_POS, "アス", "アシタ"),
    # 日本: the tagger says にっぽん; 日本語, 日本人 and 日本一 follow the token.
    # 大日本 keeps にっぽん.
    _reading_row("日本", _NOUN_POS, "ニッポン", "ニッポン", before="大$"),
    _reading_row("日本", _NOUN_POS, "ニッポン", "ニホン"),
    # 仏: the tagger says ふつ (France) for the Buddha.
    _reading_row("仏", _NOUN_POS, "フツ", "ホトケ"),
    # 蛙: the tagger says the poetic かわず, which is right in the proverb 井の中の蛙.
    _reading_row("蛙", _NOUN_POS, "カワズ", "カワズ", before="井の中の$"),
    _reading_row("蛙", _NOUN_POS, "カワズ", "カエル"),
    # 後 the noun is あと (この後、 その後、 後三年かかる). ご is the suffix of ３日後,
    # which is no noun token; the tagger says ご for the noun before a pause.
    _reading_row("後", _NOUN_POS, "ゴ", "アト"),
    # 他: た in その他 and directly before a kanji or katakana noun (他チーム). The
    # tagger also says た after a subtitle space (それとも 他に), where it is ほか.
    _reading_row("他", _NOUN_POS, "タ", "タ", before="その$"),
    _reading_row("他", _NOUN_POS, "タ", "タ", after="[一-鿿ァ-ヺ]"),
    _reading_row("他", _NOUN_POS, "タ", "ホカ"),
    # 時 the noun is とき; じ stands on a numeral or a noun (３時, 緊急時) and
    # inside a longer word. The tagger also says じ before a subtitle space
    # (込める時 気をつけろ).
    _reading_row("時", _NOUN_POS, "ジ", "ジ", prev=("名詞", "数詞", "接尾辞", "接頭辞", "記号", "代名詞")),
    _reading_row("時", _NOUN_POS, "ジ", "ジ", before="[0-9０-９〇一二三四五六七八九十]$"),
    _reading_row("時", _NOUN_POS, "ジ", "ジ", after="[一-鿿]"),
    _reading_row("時", _NOUN_POS, "ジ", "トキ"),
    # 通り: どおり directly after a noun that is no numeral (命令通り, 銀座通り) and
    # after いつも / まで; とおり after a verb, の and その (言った通り, その通り), after
    # a numeral (二通り) and for the street by itself.
    _reading_row("通り", _NOUN_POS, "トオリ", "ドオリ", prev=("名詞",)),
    _reading_row("通り", _NOUN_POS, "トオリ", "ドオリ", before="(?:いつも|まで)$"),
    # 頃: ごろ directly after a time word (３時頃, 来週頃); ころ after の, a verb, an
    # adjective and あの.
    _reading_row("頃", _NOUN_POS, "コロ", "ゴロ", prev=("名詞", "数詞", "接尾辞", "代名詞")),
    # 度 directly after a verb is たび (描く度に).
    _reading_row("度", _NOUN_POS, "ド", "タビ", prev=("動詞", "助動詞")),
    # お家 is おうち, the home (お家に帰ろう); the clan of a period piece keeps おいえ
    # (お家騒動, お家のため, お家を守る, お家断絶, and any line in a samurai's words).
    _reading_row(
        "家",
        _NOUN_POS,
        "イエ",
        "イエ",
        before="お$",
        prev=("接頭辞",),
        after="(?:の(?:ため|為|不為|一大事|大事|恥|名|名誉|存続|存亡|再興|安泰)|を(?:守|潰|継|絶や|再興)|が(?:潰|絶|滅)"
        "|ご?(?:断絶|再興|取り潰し|騒動|存続|安泰|大事)|第一)",
    ),
    _reading_row(
        "家",
        _NOUN_POS,
        "イエ",
        "イエ",
        before="お$",
        prev=("接頭辞",),
        line="わし|拙者|殿|藩|家老|家臣|ござ[るり]|奉公|主君",
    ),
    _reading_row("家", _NOUN_POS, "イエ", "ウチ", before="お$", prev=("接頭辞",)),
    # 術: すべ, a way to do it, directly after a verb and before が, は, も, を or
    # なし (知る術がない, 為す術もなく, 勝つ術を探す); it stays じゅつ, a technique,
    # after a verb of casting one (使う術, かける術), after ある and before に
    # (煽る術にはまる). After の the tagger says すべ, where it is じゅつ (忍びの術),
    # but not after ための.
    _reading_row(
        "術",
        _NOUN_POS,
        "ジュツ",
        "ジュツ",
        before="(?:使|かけ|掛け|覚え|編み出|放|操|施|唱え|扱|封じ|破|解|習|会得|発動|仕掛け)[ぁ-ん]*$",
    ),
    _reading_row("術", _NOUN_POS, "ジュツ", "ジュツ", before="ある$"),
    _reading_row(
        "術", _NOUN_POS, "ジュツ", "スベ", prev=("動詞",), after="(?:が|は|も|すら|さえ|を|な[しいく]|無[しいく])"
    ),
    _reading_row("術", _NOUN_POS, "スベ", "ジュツ", before="(?<!ため)(?<!為)の$"),
    # 敵: かたき, the one to take revenge on, after a family member or comrade + の
    # (親の敵) and before を取る / を討つ; てき elsewhere (敵を取り囲む, 敵を討ち取る,
    # and a kana うつ, which may be 撃つ).
    _reading_row(
        "敵",
        _NOUN_POS,
        "テキ",
        "カタキ",
        before="(?:親|父|母|兄|弟|姉|妹|夫|妻|友|仲間|師匠|主君|両親|家族|父上|母上|兄上|姉上|父さん|母さん|兄さん|姉さん|息子|娘)の$",
    ),
    _reading_row("敵", _NOUN_POS, "テキ", "カタキ", after="を(?:取[っるれろら]|討(?!ち取)|とっ|とる|とれ)"),
    # 宴 alone is うたげ; えん in 宴もたけなわ.
    _reading_row("宴", _NOUN_POS, "エン", "エン", after="も?\\s*(?:たけなわ|酣)"),
    _reading_row("宴", _NOUN_POS, "エン", "ウタゲ"),
    # 下: もと, under an authority or a condition, after such a noun + の and
    # before に, で or a pause (法の下に, 指導の下で); した is the place below.
    _reading_row(
        "下",
        _NOUN_POS,
        "シタ",
        "モト",
        before="(?:名|法|旗|指揮|指導|監督|管理|支配|庇護|保護|命令|許可|条件|名目|約束|契約|監視|体制|名義|合意|了解|旗印|大義|統治|指示|管轄|加護|導き)の$",
        after="(?:に|で|、|$)",
    ),
    # 空く: すく, to get empty, of the stomach (お腹が空いた); あく elsewhere (席が空いた).
    _reading_row("空く", ("動詞",), "ア", "ス", base=True, before="(?:お腹|腹|おなか|小腹)(?:が|も|を|は|の)?$"),
    # 開く: あく of what opens by itself (穴, ドア, 窓, 鍵, 店, with が or no
    # particle; 目, 口, 幕 as words of their own with が); ひらく elsewhere (本を開いた,
    # 口を開くな, 傷口が開いた) and always in the passive (開かれる).
    _reading_row("開く", ("動詞",), "ヒラ", "ヒラ", base=True, after="れ"),
    _reading_row(
        "開く",
        ("動詞",),
        "ヒラ",
        "ア",
        base=True,
        before="(?:(?:穴|ドア|戸|窓|鍵|蓋|ふた|フタ|店|瓶|ビン|襖|引き出し|シャッター|ロック|錠)(?:が|の|も|は)?|(?<![一-鿿])(?:目|口|幕)が)$",
    ),
    # 一日, １日: the tagger says ついたち, the first of the month. That is right
    # directly after a month (六月一日, 毎月１日); anywhere else it is one day.
    _reading_row("一日", _NOUN_POS, "ツイタチ", "ツイタチ", before="月$"),
    _reading_row("一日", _NOUN_POS, "ツイタチ", "イチニチ"),
    _reading_row("１日", _NOUN_POS, "ツイタチ", "ツイタチ", before="月$"),
    _reading_row("１日", _NOUN_POS, "ツイタチ", "イチニチ"),
    # 言う: the tagger says ゆう after そう and for the headword read alone; a
    # kanji 言う never reads ゆう on a card.
    _reading_row("言う", ("動詞",), "ユウ", "イウ", base=True),
    # 不味い: the tagger says まじい for the plain form.
    _reading_row("不味い", ("形容詞",), "マジイ", "マズイ", base=True),
)
_READING_CONTEXT_BY_SURFACE: dict[str, tuple[_ReadingRow, ...]] = {
    word: tuple(row for row in _READING_CONTEXT if row.word == word)
    for word in {row.word for row in _READING_CONTEXT if not row.base}
}
_READING_CONTEXT_BY_BASE: dict[str, tuple[_ReadingRow, ...]] = {
    word: tuple(row for row in _READING_CONTEXT if row.word == word)
    for word in {row.word for row in _READING_CONTEXT if row.base}
}
_READING_CONTEXT_HEADS = frozenset(row.word[0] for row in _READING_CONTEXT)


def _reading_rows(surface: str, feature: Any) -> tuple[_ReadingRow, ...] | None:
    """The table rows of a token: by its surface, else by its dictionary form."""
    rows = _READING_CONTEXT_BY_SURFACE.get(surface)
    if rows is not None:
        return rows
    for base in (getattr(feature, "lemma", None), getattr(feature, "orthBase", None)):
        if isinstance(base, str) and base[:1] == surface[:1] and base in _READING_CONTEXT_BY_BASE:
            return _READING_CONTEXT_BY_BASE[base]
    return None


def _context_class(token: Any, counter: bool) -> str:
    """What a row's ``prev`` / ``nxt`` test calls *token*: 数詞, 助数詞, else its part of speech."""
    feature = getattr(token, "feature", None)
    pos2 = getattr(feature, "pos2", None)
    if counter:
        if any(isinstance(pos, str) and "助数詞" in pos for pos in (pos2, getattr(feature, "pos3", None))):
            return "助数詞"
    elif pos2 == "数詞":
        return "数詞"
    pos1 = getattr(feature, "pos1", None)
    return pos1 if isinstance(pos1, str) else ""


def reread_in_context(tokens: list) -> list:
    """Read each token the way its line asks, by ``_READING_CONTEXT``.

    unidic-lite reads some words one way wherever they stand (何 is ナン in 何を
    and in 何だ alike). A row names the word, the reading the tagger gave and
    the tests on the line under which that reading changes or stays. On the raw
    stream and before the merges: a merge joins its tokens' kana, so the card,
    a joined headword and the sentence ruby agree. Only the kana fields change,
    on a ``RetaggedToken``; a token no row changes passes by identity, and a
    line without a table word is returned as the same list.
    """
    if not any(isinstance(token.surface, str) and token.surface[:1] in _READING_CONTEXT_HEADS for token in tokens):
        return tokens
    spaces = [space if isinstance(space := getattr(token, "white_space", ""), str) else "" for token in tokens]
    surfaces = [token.surface if isinstance(token.surface, str) else "" for token in tokens]
    out: list = []
    before = ""
    for i, token in enumerate(tokens):
        context = before + spaces[i]
        before = context + surfaces[i]
        feature = getattr(token, "feature", None)
        kana = getattr(feature, "kana", None)
        rows = _reading_rows(surfaces[i], feature) if surfaces[i][:1] in _READING_CONTEXT_HEADS else None
        if rows is None or not isinstance(kana, str) or not hasattr(feature, "_replace"):
            out.append(token)
            continue
        pos1 = getattr(feature, "pos1", None)
        after = "".join(space + surface for space, surface in zip(spaces[i + 1 :], surfaces[i + 1 :], strict=True))
        prev = _context_class(tokens[i - 1], False) if i and not spaces[i] else ""
        nxt = _context_class(tokens[i + 1], True) if i + 1 < len(tokens) and not spaces[i + 1] else ""
        row = next(
            (
                candidate
                for candidate in rows
                if pos1 in candidate.pos1
                and kana.startswith(candidate.old)
                and (not candidate.prev or prev in candidate.prev)
                and (not candidate.nxt or nxt in candidate.nxt)
                and (candidate.before is None or candidate.before.search(context.rstrip()))
                and (candidate.after is None or candidate.after.match(after))
                and (candidate.line is None or candidate.line.search(before + after))
            ),
            None,
        )
        if row is None or row.new == row.old:
            out.append(token)
            continue
        if not row.base and kana == row.old:
            # A whole word: every kana field takes the reading, as in
            # ``reread_omote_tokens`` (``pron`` writes トオリ as トーリ, no prefix of it).
            fields = {name: row.new for name in _CONTEXT_KANA_FIELDS if isinstance(getattr(feature, name, None), str)}
        else:
            fields = {
                name: row.new + value[len(row.old) :]
                for name in _CONTEXT_KANA_FIELDS
                if isinstance(value := getattr(feature, name, None), str) and value.startswith(row.old)
            }
        out.append(RetaggedToken(token, fields))
    return out


def _edit_distance(a: str, b: str) -> int:
    """Plain Levenshtein distance (readings are short; O(len*len) is fine)."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def attest_merged_readings(tokens: list, reading_lookup: ReadingLookup | None) -> list:
    """Override merged-compound kana with the dictionary's attested reading.

    Every merge pass builds a compound's kana by concatenating per-token UniDic
    kana, which loses rendaku and on/kun junction effects (バカ+力 → バカリョク
    where the dictionary attests ばかぢから; 体+じゅう → タイジュウ vs
    からだじゅう — 2026-07 card audit F2: 20/729 cards shipped such readings).
    This pass runs after the merges and asks the enabled offline dictionaries
    for each SYNTHETIC token's surface (surface-keyed on purpose: an inflected
    matcher span like 手っ取り早く is not a headword and is correctly skipped;
    lemma-keyed lookup would poison such spans with citation-form readings):

    1. concatenated kana already attested → keep it (contextual MeCab signal
       wins; only ``kana_attested`` is stamped);
    2. exactly one attested reading → take it;
    3. several → take the one closest to the concatenation by edit distance
       (ties: dictionary score order), so context still steers 四人 to よにん
       rather than the top entry しにん.

    Tokens whose kana came from the curated kinship table
    (``feature.kana_special``, d848257) or from a context-locked 1:1
    replacement (``feature.kana_locked`` — e.g. ``masu_stem_nominalizer``'s
    nominalization, whose kana is unidic's context-disambiguated reading, not
    a re-tokenization) are skipped — both outrank the dictionary. Real UniDic
    tokens are never touched (polyphonic 方/中 keep their contextual reading).
    Flags land on ``token.feature`` (a
    ``SimpleNamespace`` — ``SyntheticToken`` declares ``__slots__``):
    ``kana_attested`` on cases 1-3 (``_emit_word`` trusts the token kana), and
    ``kana_overridden`` on cases 2-3 only. The sentence display path carries
    every ``kana_attested`` synthetic so dictionary-backed compound grouping is
    consistent; see ``replace_overridden_spans``. Mutates the synthetic tokens in place —
    they are per-line objects created by the merge passes above. Returns
    ``tokens`` unchanged (and issues NO lookup) when ``reading_lookup`` is
    ``None`` or the line produced no synthetics.
    """
    if reading_lookup is None:
        return tokens
    synthetics = [
        t
        for t in tokens
        if isinstance(t, SyntheticToken)
        and not getattr(t.feature, "kana_special", False)
        and not getattr(t.feature, "kana_locked", False)
    ]
    if not synthetics:
        return tokens
    attested_map = reading_lookup(sorted({t.surface for t in synthetics}))
    for tok in synthetics:
        attested = attested_map.get(tok.surface)
        if not attested:
            continue
        concat = katakana_to_hiragana(tok.feature.kana or "")
        resolution = resolve_attested_reading(concat, attested)
        chosen = resolution.reading
        if resolution.ambiguous:
            # Merged-token concatenation is a stronger contextual signal than a
            # real token's collapsed lexical reading, so preserve this existing
            # compound-only tie-break. Single real tokens never enter this path.
            folded = list(dict.fromkeys(katakana_to_hiragana(r) for r in attested))
            chosen = min(folded, key=lambda r: _edit_distance(r, concat))
        if chosen is None:
            continue
        # The curated table outranks an attested concatenation of a misread head
        # (私たち: ワタクシ+タチ is a listed reading, but not the one a card wants).
        chosen = resolve_reading_override(tok.surface, chosen) or chosen
        if chosen == concat:
            tok.feature.kana_attested = True
            continue
        tok.feature.kana = hiragana_to_katakana(chosen)
        tok.feature.kana_attested = True
        tok.feature.kana_overridden = True
    return tokens


def replace_overridden_spans(text: str, raw_tokens: list, merged_tokens: list) -> list:
    """Carry dictionary-attested compound tokens into the sentence stream.

    Sentence furigana/reading/bold are generated from the RAW token stream, so
    a merged token's dictionary-backed grouping and kana never reach them on
    their own. This pass
    aligns each merged token back to its consecutive raw-token run (the merged
    stream is a grouping of the raw stream — walk both, matching surface
    concatenation) and, for every dictionary-attested synthetic
    (``feature.kana_attested``), replaces the run with one ``SyntheticToken``
    carrying the attested kana. This keeps unchanged readings such as 二級
    grouped like corrected readings such as 一級. The concatenated stream text
    is byte-identical, so downstream ``str.find`` cursoring and bold-offset math
    stay valid — with one guard: a replacement is skipped when its exact raw
    occurrence was stitched across source whitespace (MeCab drops it), because
    the single-token surface would then not be locatable at that occurrence;
    the raw run is kept instead (bail-keep). Any alignment mismatch returns
    ``raw_tokens`` untouched.
    """
    if not any(isinstance(m, SyntheticToken) and getattr(m.feature, "kana_attested", False) for m in merged_tokens):
        return raw_tokens
    out: list = []
    ri, rn = 0, len(raw_tokens)
    source_cursor = 0
    for m in merged_tokens:
        if ri < rn and raw_tokens[ri] is m:
            idx = text.find(m.surface, source_cursor)
            if idx == -1:
                return raw_tokens
            source_cursor = idx + len(m.surface)
            out.append(m)
            ri += 1
            continue
        acc, j = "", ri
        while j < rn and len(acc) < len(m.surface):
            acc += raw_tokens[j].surface
            j += 1
        if acc != m.surface:
            return raw_tokens
        run = raw_tokens[ri:j]
        ri = j
        run_start = source_cursor
        for index, raw in enumerate(run):
            idx = text.find(raw.surface, source_cursor)
            if idx == -1:
                return raw_tokens
            if index == 0:
                run_start = idx
            source_cursor = idx + len(raw.surface)
        source_run = text[run_start:source_cursor]
        if getattr(m.feature, "kana_attested", False) and source_run == m.surface:
            # Exact-occurrence guard: a later contiguous duplicate cannot make
            # an earlier whitespace-stitched run appear locatable.
            out.append(
                SyntheticToken(
                    surface=m.surface,
                    pos1=m.feature.pos1,
                    pos2=m.feature.pos2,
                    lemma=m.feature.lemma,
                    kana=m.feature.kana,
                )
            )
        else:
            out.extend(run)
    if ri != rn:
        return raw_tokens
    return out


def _nominal_suffix_run(tokens: list, start: int) -> list:
    """Consecutive nominal-suffix tokens starting at ``start``."""
    chain: list = []
    n = len(tokens)
    while start < n:
        try:
            p1 = tokens[start].feature.pos1
            p2 = tokens[start].feature.pos2
        except AttributeError:
            _guard("_nominal_suffix_run")
            break
        if p1 != "接尾辞" or p2 not in _NOMINAL_SUFFIX_POS2:
            break
        chain.append(tokens[start])
        start += 1
    return chain


def _nominal_suffix_chain(tokens: list, i: int) -> list:
    """Run of nominal-suffix tokens immediately following a 名詞 head at ``i``.

    Returns the (possibly empty) list of consecutive 接尾辞(名詞的/形状詞的/副詞的)
    tokens after ``tokens[i]``; empty when ``tokens[i]`` is not a 名詞 head
    (missing feature or wrong pos1) or no nominal suffix follows. Shared by
    ``_merge_noun_suffixes``' candidate pre-scan and its merge loop so the two
    can never disagree on which chains exist.
    """
    try:
        if tokens[i].feature.pos1 != "名詞":
            return []
    except AttributeError:
        _guard("_nominal_suffix_chain")
        return []
    return _nominal_suffix_run(tokens, i + 1)


def _attested_noun_suffix_surfaces(tokens: list, attest: AttestLookup) -> set[str]:
    """One batched attestation probe of every noun-suffix compound on the line.

    Greedy left-to-right, mirroring ``_merge_noun_suffixes``' walk, so the probed
    set is exactly the candidate compounds the merge loop will weigh. The set is
    bail-invariant: bailing a candidate only re-exposes its 接尾辞 tokens, which
    never start a new chain (a chain begins only at a 名詞 head). Returns the
    attested SUBSET; issues NO probe when the line has no candidate.
    """
    surfaces: set[str] = set()
    i, n = 0, len(tokens)
    while i < n:
        chain = _nominal_suffix_chain(tokens, i)
        if chain:
            surfaces.add(tokens[i].surface + "".join(t.surface for t in chain))
            i += 1 + len(chain)
        else:
            i += 1
    if not surfaces:
        return set()
    return attest(sorted(surfaces))


def _merge_noun_suffixes(tokens: list, attest: AttestLookup | None = None) -> list:
    """Merge 名詞 + 接尾辞(名詞的/形状詞的/副詞的) chains into a single token.

    Walks tokens left-to-right. When a 名詞 head is followed by one or
    more nominal-suffix tokens, both base and suffixes are consumed and
    replaced by a single _SyntheticToken whose surface is the concatenated
    form and whose lemma is reconstructed from each component's
    feature.lemma (falling back to surface when unidic emits "*"/None).
    Nouns rarely conjugate, so lemma usually equals surface, but morphemes
    like ~性 / ~中 / ~的 carry their own dictionary form and we preserve it.

    Attested-or-bail gate (``attest`` not None): a chain is minted only when its
    concatenated surface is a dictionary headword. On a full-surface miss, a
    curated kinship compound (``special_head`` — 兄ちゃん/父さま, whose reading
    the dictionary does not attest but which we must keep) mints only through
    its first licensing suffix; later suffixes re-enter the loop as bare tokens.
    Every other miss bails the WHOLE greedy chain to its components: the head is
    emitted alone and all suffix tokens re-enter the loop (a following dictionary
    matcher can then recover the longest attested sub-span). One batched probe
    per line covers every candidate. ``attest=None`` mints unconditionally
    (pre-gate behavior).

    The non-kinship branch overlaps CompoundDictionaryMatcher (which also
    recovers attested 名詞+接尾辞 spans) but is retained deliberately: the
    matcher's 12-char/5-token span caps and lemma derivation differ, so
    reducing this pass to kinship-only would change minted output. Kinship is
    the behavior only this pass owns — the matcher has no reading override.
    """
    attested = _attested_noun_suffix_surfaces(tokens, attest) if attest is not None else None
    merged: list = []
    i, n = 0, len(tokens)
    while i < n:
        head = tokens[i]
        chain = _nominal_suffix_chain(tokens, i)
        if chain:
            surf = head.surface + "".join(t.surface for t in chain)
            # Honorific-kinship override: 兄+ちゃん must read ニイチャン, not the
            # concatenated isolated-head アニチャン (see _KINSHIP_HEAD_READINGS).
            # Licensed by the first suffix in the chain (the adjacent honorific).
            special_head = resolve_special_reading(head.surface, chain[0].surface)
            # Attested-or-bail: ordinary misses fragment back to their
            # components. The kinship carve-out covers only the head plus its
            # adjacent licensing suffix; any later suffix must re-enter bare.
            if attested is not None and surf not in attested:
                if special_head is None:
                    merged.append(head)
                    i += 1
                    continue
                chain = chain[:1]
                surf = head.surface + chain[0].surface
            try:
                head_kana = head.feature.kana or head.surface
            except AttributeError:
                _guard("_merge_noun_suffixes#1")
                head_kana = head.surface
            suffix_kanas = []
            for t in chain:
                try:
                    suffix_kanas.append(t.feature.kana or t.surface)
                except AttributeError:
                    _guard("_merge_noun_suffixes#2")
                    suffix_kanas.append(t.surface)
            head_kana_final = special_head if special_head is not None else head_kana
            kana = head_kana_final + "".join(suffix_kanas)
            kana_special = special_head is not None
            try:
                head_pos2 = head.feature.pos2 or "普通名詞"
            except AttributeError:
                _guard("_merge_noun_suffixes#3")
                head_pos2 = "普通名詞"
            # An attested merge is a dictionary word, not the name its head is
            # (日本+人 → 日本人), so the proper-noun gate must not drop it.
            if head_pos2 == "固有名詞" and attested is not None and surf in attested:
                head_pos2 = "普通名詞"
            try:
                head_lemma = extract_lemma(head)
            except AttributeError:
                _guard("_merge_noun_suffixes#4")
                head_lemma = head.surface
            suffix_lemmas: list[str] = []
            for t in chain:
                try:
                    suffix_lemmas.append(extract_lemma(t))
                except AttributeError:
                    _guard("_merge_noun_suffixes#5")
                    suffix_lemmas.append(t.surface)
            synthetic = SyntheticToken(
                surface=surf,
                pos1="名詞",
                pos2=head_pos2,
                lemma=head_lemma + "".join(suffix_lemmas),
                kana=kana,
            )
            if kana_special:
                # Curated kinship reading outranks dictionary attestation:
                # attest_merged_readings must not replace にいちゃん with a
                # dictionary variant (あんちゃん). Flag lives on the feature
                # namespace (SyntheticToken declares __slots__).
                synthetic.feature.kana_special = True
            merged.append(synthetic)
            i += 1 + len(chain)
            continue
        merged.append(head)
        i += 1
    return merged


def _attested_prefix_surfaces(tokens: list, attest: AttestLookup) -> set[str]:
    """Batch prefix intermediates and their complete immediate suffix chains.

    Greedy walk mirroring ``_merge_prefix_compounds``, so the probed set is
    exactly what the merge loop weighs. A final chain hit licenses the temporary
    prefix synthetic that ``_merge_noun_suffixes`` needs as its 名詞 head.
    Returns the attested SUBSET; no probe when the line has no candidate.
    """
    surfaces: set[str] = set()
    i, n = 0, len(tokens)
    while i < n:
        head = tokens[i]
        try:
            head_pos1 = head.feature.pos1
        except AttributeError:
            _guard("_attested_prefix_surfaces#1")
            i += 1
            continue
        if head_pos1 == "接頭辞" and head.surface in _PREFIX_WHITELIST and i + 1 < n:
            root = tokens[i + 1]
            try:
                root_pos1 = root.feature.pos1
            except AttributeError:
                _guard("_attested_prefix_surfaces#2")
                i += 1
                continue
            if root_pos1 in {"名詞", "形状詞"}:
                surf = head.surface + root.surface
                surfaces.add(surf)
                suffix_run = _nominal_suffix_run(tokens, i + 2)
                if suffix_run:
                    surfaces.add(surf + "".join(t.surface for t in suffix_run))
                i += 2
                continue
        i += 1
    if not surfaces:
        return set()
    return attest(sorted(surfaces))


class RetaggedToken:
    """A tagger token whose part of speech ``retag_mistagged_tokens`` corrected.

    Every feature the replacement does not name stays the tagger's own, so the
    token mines exactly as the tagger's other reading of the same word would.
    """

    __slots__ = ("surface", "feature", "white_space")

    def __init__(self, token, fields: dict[str, str], surface: str | None = None):
        self.surface = token.surface if surface is None else surface
        self.feature = token.feature._replace(**fields)
        self.white_space = getattr(token, "white_space", "")


#: The pre-noun adjectives unidic-lite also reads as the homograph filler, with
#: the fields its 連体詞 reading carries (あの時 tags 連体詞, あの女 the filler).
_FILLER_PRENOUNS: dict[str, dict[str, str]] = {
    "あの": {"pos1": "連体詞", "pos2": "*", "lemma": "彼の"},
    "その": {"pos1": "連体詞", "pos2": "*", "lemma": "其の"},
}


#: The nouns unidic-lite also reads as a one-kanji prefix (今 コン, 次 ジ), with
#: the fields its noun reading carries (今は, 次は tag 名詞).
_PREFIX_NOUNS: dict[str, dict[str, str]] = {
    "今": {
        "pos1": "名詞",
        "pos2": "普通名詞",
        "pos3": "副詞可能",
        **dict.fromkeys(("lForm", "pron", "pronBase", "kana", "kanaBase", "form", "formBase"), "イマ"),
        "goshu": "和",
    },
    "次": {
        "pos1": "名詞",
        "pos2": "普通名詞",
        "pos3": "一般",
        **dict.fromkeys(("lForm", "pron", "pronBase", "kana", "kanaBase", "form", "formBase"), "ツギ"),
        "goshu": "和",
    },
}


#: Two tagger tokens cut at the wrong place, by their (surface, lemma), and the
#: two (surface, fields) they are re-cut into. 自分ち 'my place' (ち from うち)
#: has no path in unidic-lite, which reads 自 + 分ち (わかち 'division').
#: 殺人犯って is read 殺人 + 犯っ (犯る, vulgar やる) + て: it is the crime suffix
#: 犯 (ハン, the tag 殺人犯は gets) and the quoting って, so the noun-suffix merge
#: makes 殺人犯 as it does before は. Only right after a noun stem
#: (``_MISSPLIT_STEMS``): 彼女を犯って is the verb.
_MISSPLITS: dict[tuple[tuple[str, str | None], ...], tuple[tuple[str, dict[str, str]], ...]] = {
    (("犯っ", "遣る"), ("て", "て")): (
        (
            "犯",
            {
                "pos1": "接尾辞",
                "pos2": "名詞的",
                "pos3": "一般",
                "cType": "*",
                "cForm": "*",
                "goshu": "漢",
                **dict.fromkeys(("lemma", "orth", "orthBase"), "犯"),
                **dict.fromkeys(("lForm", "pron", "pronBase", "kana", "kanaBase", "form", "formBase"), "ハン"),
            },
        ),
        (
            "って",
            {
                "pos2": "副助詞",
                **dict.fromkeys(("lemma", "orth", "orthBase"), "って"),
                **dict.fromkeys(("lForm", "pron", "pronBase", "kana", "kanaBase", "form", "formBase"), "ッテ"),
            },
        ),
    ),
    (("自", "自"), ("分ち", "分かち")): (
        (
            "自分",
            {
                "pos3": "一般",
                **dict.fromkeys(("lemma", "orth", "orthBase"), "自分"),
                **dict.fromkeys(("lForm", "pron", "pronBase", "kana", "kanaBase", "form", "formBase"), "ジブン"),
            },
        ),
        ("ち", {**dict.fromkeys(("lemma", "orth", "orthBase"), "ち"), **dict.fromkeys(("lForm", "kana"), "チ")}),
    ),
    # てらんない (ていられない): unidic-lite reads the らん as an adverb, which ends
    # the verb's chain at て and leaves the ない after it standing as a word.
    (("て", "て"), ("らん", "らん")): (("て", {}), ("らん", {"pos1": "助動詞"})),
    (("で", "て"), ("らん", "らん")): (("で", {}), ("らん", {"pos1": "助動詞"})),
}


def _noun_tte_resplit(
    noun: str, kana: str, pos3: str = "一般", lemma: str | None = None
) -> tuple[tuple[str, dict[str, str]], ...]:
    """The re-cut of a one-kanji noun + って the tagger read as a verb's て form (恋っ|て → 恋|って)."""
    kana_fields = ("lForm", "pron", "pronBase", "kana", "kanaBase", "form", "formBase")
    return (
        (
            noun,
            {
                "pos1": "名詞",
                "pos2": "普通名詞",
                "pos3": pos3,
                "cType": "*",
                "cForm": "*",
                **dict.fromkeys(("orth", "orthBase"), noun),
                "lemma": lemma or noun,
                **dict.fromkeys(kana_fields, kana),
            },
        ),
        (
            "って",
            {
                "pos2": "副助詞",
                **dict.fromkeys(("lemma", "orth", "orthBase"), "って"),
                **dict.fromkeys(kana_fields, "ッテ"),
            },
        ),
    )


# 恋って, 番って and 敵って before the quoting って: unidic-lite reads the 促音便 of 恋う
# (whose て form is 恋うて), 番う 'to mate' and 敵う (written 叶って when it is meant).
# The cost is the rare 番っている and 敵っている, which now card the noun.
# The rows after them are nouns it reads as a verb spelled without its okurigana
# (味わう, 逆らう, 曲がる) or as an old verb (司る, 隣る, 因る, 減る, 凍る).
_MISSPLITS.update(
    {
        (("恋っ", "恋う"), ("て", "て")): _noun_tte_resplit("恋", "コイ", "サ変可能"),
        (("番っ", "番う"), ("て", "て")): _noun_tte_resplit("番", "バン"),
        (("敵っ", "敵う"), ("て", "て")): _noun_tte_resplit("敵", "テキ"),
        (("味っ", "味わう"), ("て", "て")): _noun_tte_resplit("味", "アジ"),
        (("逆っ", "逆らう"), ("て", "て")): _noun_tte_resplit("逆", "ギャク", "形状詞可能"),
        (("曲っ", "曲がる"), ("て", "て")): _noun_tte_resplit("曲", "キョク"),
        (("主っ", "司る"), ("て", "て")): _noun_tte_resplit("主", "ヌシ"),
        (("隣っ", "隣る"), ("て", "て")): _noun_tte_resplit("隣", "トナリ", lemma="隣り"),
        (("縁っ", "因る"), ("て", "て")): _noun_tte_resplit("縁", "エン"),
        (("空っ", "減る"), ("て", "て")): _noun_tte_resplit("空", "ソラ"),
        (("氷っ", "凍る"), ("て", "て")): _noun_tte_resplit("氷", "コオリ"),
    }
)


#: The pairs of ``_MISSPLITS`` that are re-cut only right after (no space) a
#: token of one of these parts of speech, the stem their first piece suffixes.
_MISSPLIT_STEMS: dict[tuple[tuple[str, str | None], ...], frozenset[str]] = {
    (("犯っ", "遣る"), ("て", "て")): frozenset({"名詞", "形状詞"}),
}


def _unknown_katakana_noun(token) -> bool:
    """A katakana word the tagger does not know: a 名詞 with no lemma (スマホ, コスパ)."""
    feature = getattr(token, "feature", None)
    surface = getattr(token, "surface", "")
    return (
        getattr(feature, "pos1", None) == "名詞"
        and not getattr(feature, "lemma", None)
        and isinstance(surface, str)
        and len(surface) >= 3
        and all("ァ" <= ch <= "ヺ" or ch == "ー" for ch in surface)
    )


#: Entry tags of a sound or a cry, which JMdict often also tags ``n``
#: (ピーポー 'nee-naw', オラオラ).
_SOUND_TAGS = frozenset({"int", "on-mim"})


@lru_cache(maxsize=1)
def _missplit_surfaces() -> frozenset[tuple[str, str]]:
    """The surface pairs of ``_MISSPLITS``: a pair outside it needs no lemma lookup.

    Built on first use, after the module has loaded, so an entry added anywhere in
    the module is in it; code that changes the table later calls ``cache_clear()``.
    """
    return frozenset((a[0], b[0]) for a, b in _MISSPLITS)


def _lemma_or_none(token) -> str | None:
    lemma = getattr(getattr(token, "feature", None), "lemma", None)
    return lemma if isinstance(lemma, str) else None


def _dictionary_nouns(surfaces: set[str], form_lookup: FormLookup) -> set[str]:
    """The surfaces the dictionary has a noun entry for (tag ``n``) and no sound or cry sense, in one probe."""
    rows = form_lookup(sorted(surfaces))
    nouns: set[str] = set()
    for term, found in rows.items():
        tags = {tag for _content, row_tags in found for tag in row_tags.split()}
        if "n" in tags and not tags & _SOUND_TAGS:
            nouns.add(term)
    return nouns


def retag_mistagged_tokens(tokens: list, form_lookup: FormLookup | None = None) -> list:
    """Correct a tagger part of speech the next token disproves, or fill in a lemma it lacks.

    Fugashi tokens carry the space before them as ``white_space``; a token
    without one counts as unspaced.

    - あの/その tagged as the filler directly before a noun, with no space or
      mark between, are the pre-noun adjective (あの女, あの橋): a filler is
      followed by a pause or a particle (あの…, あの 山田さん, あのね). A pronoun
      is left out on purpose: あの私 is "um, I".
    - 今/次 tagged as a prefix with a space after them are the nouns 'now' and
      'next' (今 駅にいる): the tagger skips the subtitle space and reads 今 駅
      as it reads 今大会. A prefix is never spaced from its noun, so the
      unspaced prefix (今大会, 次年度) stays one.
    - A katakana word the tagger does not know (スマホ) is a 名詞 with no lemma,
      which no inclusion check takes. With a dictionary that has a noun entry
      for it, its surface becomes its lemma. The noun entry is required, and a
      sound or cry sense refused, because the dictionary also lists katakana
      interjections and sound words (ワーオ, ピーポー), some of them as nouns.
    - Two tokens cut at the wrong place (``_MISSPLITS``: 自|分ち) are re-cut
      (自分|ち); the pair's surfaces join to the same text.
    - An interjection read as another word before a space (おいおい as the
      adverb 追い追い, ほら as a stem of 放る) is the interjection
      (``_spaced_interjection_fields``).
    - A clause-final いい read as the stem of 言う (どうだっていい) is the
      adjective 良い (``_clause_final_ii_fields``).
    - か + なっ glued into かなっ (適う) after なんと, 何と or どうに is cut in two
      (``_glued_ka_naru``).
    - A kana verb filed under a homophone's lemma (おごる under 驕る) takes
      the lemma of the verb it is (``_kana_homophone_fields``).
    - 若 read as the stem of 若い where a pause or a case particle follows is the
      address 'young master', the noun 若 (``_address_waka_fields``).
    """
    unknown = {token.surface for token in tokens if _unknown_katakana_noun(token)}
    nouns = _dictionary_nouns(unknown, form_lookup) if unknown and form_lookup is not None else set()
    out: list = []
    resplit: tuple[tuple[str, dict[str, str]], ...] | None = None
    missplit_surfaces = _missplit_surfaces()
    for i, token in enumerate(tokens):
        if resplit is not None:
            out.append(RetaggedToken(token, resplit[1][1], resplit[1][0]))
            resplit = None
            continue
        pos1 = getattr(getattr(token, "feature", None), "pos1", None)
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        white_space = getattr(nxt, "white_space", "")
        spaced = isinstance(white_space, str) and bool(white_space)
        if nxt is not None and not spaced and (token.surface, nxt.surface) in missplit_surfaces:
            pair = ((token.surface, _lemma_or_none(token)), (nxt.surface, _lemma_or_none(nxt)))
            resplit = _MISSPLITS.get(pair)
            stems = _MISSPLIT_STEMS.get(pair)
            if stems is not None and not (
                i > 0
                and not getattr(token, "white_space", "")
                and getattr(getattr(tokens[i - 1], "feature", None), "pos1", None) in stems
            ):
                resplit = None
            if resplit is not None:
                out.append(RetaggedToken(token, resplit[0][1], resplit[0][0]))
                continue
        fields: dict[str, str] | None = None
        if pos1 == "感動詞" and nxt is not None and not spaced:
            if getattr(getattr(nxt, "feature", None), "pos1", None) == "名詞":
                fields = _FILLER_PRENOUNS.get(token.surface)
        elif pos1 == "接頭辞" and spaced:
            fields = _PREFIX_NOUNS.get(token.surface)
        elif token.surface in nouns and _unknown_katakana_noun(token):
            fields = dict.fromkeys(("lemma", "orthBase", "kana"), token.surface)
        if fields is None and (pieces := _glued_ka_naru(tokens, i)) is not None:
            out.extend(pieces)
            continue
        if fields is None:
            fields = (
                _spaced_interjection_fields(tokens, i)
                or _clause_final_ii_fields(tokens, i)
                or _kana_homophone_fields(tokens, i)
                or _address_waka_fields(tokens, i)
            )
        out.append(token if fields is None else RetaggedToken(token, fields))
    return out


#: Interjections unidic-lite reads as another word where a space follows them,
#: by (surface, lemma), with the interjection's own lemma: おいおい ('hey, come
#: on') is to it always the adverb 追い追い 'gradually', and ほら before a spaced
#: ねえ or ない the stem of 放る (ほら ねえ).
_SPACED_INTERJECTIONS: dict[tuple[str, str | None], str] = {
    ("おいおい", "追い追い"): "おいおい",
    ("ほら", "放る"): "ほら",
}


@lru_cache(maxsize=1)
def _spaced_interjection_surfaces() -> frozenset[str]:
    """The surfaces of ``_SPACED_INTERJECTIONS``, built on first use (``_missplit_surfaces``)."""
    return frozenset(surface for surface, _lemma in _SPACED_INTERJECTIONS)


_PAUSE_POS1: frozenset[str] = frozenset({"補助記号", "感動詞", "フィラー"})


def _before_pause(tokens: list, i: int) -> bool:
    """Whether a space, a mark or the line end follows token ``i``."""
    nxt = tokens[i + 1] if i + 1 < len(tokens) else None
    return (
        nxt is None
        or bool(getattr(nxt, "white_space", ""))
        or getattr(getattr(nxt, "feature", None), "pos1", None) == "補助記号"
    )


def _spaced_interjection_fields(tokens: list, i: int) -> dict[str, str] | None:
    """The 感動詞 fields of an interjection the tagger read as another word (``_SPACED_INTERJECTIONS``), or None.

    Only where a space, a mark or the line end follows: a verb stem is never
    spaced from its ending (ほらん), and an adverb runs on into its clause. An
    adverb must also open the line or follow a pause, as the real 追い追い
    stands mid-clause (それは おいおい 考えます).
    """
    token = tokens[i]
    if token.surface not in _spaced_interjection_surfaces():
        return None
    feature = getattr(token, "feature", None)
    lemma = _SPACED_INTERJECTIONS.get((token.surface, _lemma_or_none(token)))
    if lemma is None:
        return None
    if not _before_pause(tokens, i):
        return None
    before = getattr(getattr(tokens[i - 1], "feature", None), "pos1", None) if i else None
    if getattr(feature, "pos1", None) == "副詞" and before not in (None, *_PAUSE_POS1):
        return None
    fields = {"pos1": "感動詞", "pos2": "一般", "cType": "*", "cForm": "*", "lemma": lemma, "orthBase": token.surface}
    kana = getattr(feature, "kana", None)
    if isinstance(kana, str):
        fields.update(dict.fromkeys(("lForm", "kanaBase", "formBase", "pronBase"), kana))
    return fields


#: What follows 若 the address: a case or topic particle, or the copula. Not か:
#: 若か is the Kyushu 若い (若か男).
_WAKA_FOLLOWERS: frozenset[str] = frozenset(
    {"は", "が", "を", "の", "に", "も", "と", "へ", "や", "って", "だ", "です"}
)
#: Marks that continue an adjective's stem instead of ending the word: the cry (若っ, 若ー) and a trailing-off 若….
_STEM_RUN_ON_MARKS = frozenset("っッーぇェ〜～…‥")


def _address_waka_fields(tokens: list, i: int) -> dict[str, str] | None:
    """The noun fields of 若 used as an address ('young master'), or None.

    unidic-lite reads a 若 that a pause follows (若、お待ちを) as the stem of 若い,
    which was carded 若い; elsewhere it reads the same address as the noun 若
    (若！, 若を頼む), which is the card. The adjective's stem runs on into its
    ending (若すぎる, 若そう, the cry 若っ), so before a space, a mark that ends
    the word, the line end, a case or topic particle or the copula 若 is the
    address and takes the noun's fields. A noun after it (若旦那, 若先生) is left
    to the tagger.
    """
    token = tokens[i]
    feature = getattr(token, "feature", None)
    if token.surface != "若" or getattr(feature, "pos1", None) != "形容詞" or _lemma_or_none(token) != "若い":
        return None
    nxt = tokens[i + 1] if i + 1 < len(tokens) else None
    if nxt is not None and not getattr(nxt, "white_space", ""):
        nxt_pos1 = getattr(getattr(nxt, "feature", None), "pos1", None)
        if nxt.surface[:1] in _STEM_RUN_ON_MARKS:
            return None
        if nxt_pos1 != "補助記号" and not (nxt_pos1 in ("助詞", "助動詞") and nxt.surface in _WAKA_FOLLOWERS):
            return None
    fields = {"pos1": "名詞", "pos2": "普通名詞", "pos3": "一般", "pos4": "*", "cType": "*", "cForm": "*"}
    fields.update(dict.fromkeys(("lemma", "orthBase"), "若"))
    fields.update(dict.fromkeys(("lForm", "kanaBase", "formBase", "pronBase"), "ワカ"))
    return fields


#: Kana verbs unidic-lite files under a homophone's lemma, by (orthBase, lemma),
#: with the lemma of the verb the kana spelling nearly always is: おごる is 奢る
#: 'to treat' (not 驕る 'to be haughty'), つるむ 連む 'to hang around with' (not
#: 交尾む 'to copulate'), かなう 叶う 'to come true' (not 適う 'to suit'). A
#: hiragana card takes the definition of the tagger's lemma, so the wrong lemma
#: is the wrong card.
_KANA_HOMOPHONE_LEMMAS: dict[tuple[str | None, str | None], str] = {
    ("おごる", "驕る"): "奢る",
    ("つるむ", "交尾む"): "連む",
    ("かなう", "適う"): "叶う",
}


@lru_cache(maxsize=1)
def _kana_homophone_orths() -> frozenset[str | None]:
    """The orthBases of ``_KANA_HOMOPHONE_LEMMAS``, built on first use (``_missplit_surfaces``)."""
    return frozenset(orth_base for orth_base, _lemma in _KANA_HOMOPHONE_LEMMAS)


def _kana_homophone_fields(tokens: list, i: int) -> dict[str, str] | None:
    """The corrected lemma of a kana verb the tagger files under its homophone (``_KANA_HOMOPHONE_LEMMAS``), or None.

    かなう right after に keeps 適う, the verb of 理にかなう and 目的にかなう.
    """
    token = tokens[i]
    orth_base = getattr(getattr(token, "feature", None), "orthBase", None)
    if orth_base not in _kana_homophone_orths():
        return None
    lemma = _KANA_HOMOPHONE_LEMMAS.get((orth_base, _lemma_or_none(token)))
    if lemma is None or (lemma == "叶う" and i and tokens[i - 1].surface == "に"):
        return None
    return {"lemma": lemma}


#: The fields unidic-lite gives the particle か and the stem なっ of 成る, the two
#: words it glues into かなっ (適う 'to come true') in なんとかなった.
_KA_PARTICLE: dict[str, str] = {
    "pos1": "助詞",
    "pos2": "副助詞",
    "cType": "*",
    "cForm": "*",
    **dict.fromkeys(("lemma", "orth", "orthBase"), "か"),
    **dict.fromkeys(("lForm", "pron", "pronBase", "kana", "kanaBase", "form", "formBase"), "カ"),
}
_NARU_STEM: dict[str, str] = {
    "pos1": "動詞",
    "pos2": "非自立可能",
    "cType": "五段-ラ行",
    "cForm": "連用形-促音便",
    "lemma": "成る",
    "orth": "なっ",
    "orthBase": "なる",
    **dict.fromkeys(("pron", "kana", "form"), "ナッ"),
    **dict.fromkeys(("lForm", "pronBase", "kanaBase", "formBase"), "ナル"),
}


def _glued_ka_naru(tokens: list, i: int) -> tuple[RetaggedToken, RetaggedToken] | None:
    """The か and なっ of a かなっ the tagger glued (なんとかなった, どうにかなって), or None.

    Only right after なん/何/どう + と/に, where no real かなう stands (夢がかなった,
    理にかなった keep the verb). The pieces join to the same text, and なんとか
    can then join as the adverb it is.
    """
    token = tokens[i]
    if i < 2 or token.surface != "かなっ" or _lemma_or_none(token) != "適う":
        return None
    if tokens[i - 1].surface not in ("と", "に") or tokens[i - 2].surface not in ("なん", "何", "どう"):
        return None
    if getattr(token, "white_space", "") or getattr(tokens[i - 1], "white_space", ""):
        return None
    stem = RetaggedToken(token, _NARU_STEM, "なっ")
    stem.white_space = ""
    return RetaggedToken(token, _KA_PARTICLE, "か"), stem


#: The fields unidic-lite gives the adjective いい (良い 'good, fine').
_ADJECTIVE_II: dict[str, str] = {
    "pos1": "形容詞",
    "pos2": "非自立可能",
    "cType": "形容詞",
    "cForm": "終止形-一般",
    "lForm": "ヨイ",
    "lemma": "良い",
    "orthBase": "いい",
    "pronBase": "イー",
    "kanaBase": "イイ",
    "formBase": "イイ",
}


def _clause_final_ii_fields(tokens: list, i: int) -> dict[str, str] | None:
    """The 良い fields of a clause-final いい the tagger read as the stem of 言う (どうだっていい, 礼はいい、…), or None.

    The stem of 言う runs on into its ending (いいます, いいながら, いい出す);
    before a space, a mark or the line end いい is the adjective. After a noun
    + と it stays the verb, the pattern AといいBといい ('A as well as B'). The
    literary そういい、彼は去った is read as the adjective too, and subtitles
    rarely have it.
    """
    token = tokens[i]
    if token.surface != "いい" or _lemma_or_none(token) != "言う" or not _before_pause(tokens, i):
        return None
    before = getattr(getattr(tokens[i - 2], "feature", None), "pos1", None) if i >= 2 else None
    if before in ("名詞", "代名詞", "接尾辞") and tokens[i - 1].surface == "と":
        return None
    return _ADJECTIVE_II


#: Adjectives spoken with an -ee ending (やばい → やべー) that unidic-lite has no
#: path for and cuts into particles, sounds and stray nouns (や|べ|ー, で|けー,
#: かっけ 'beriberi' |ー): the stem as written, then the dictionary form in kana,
#: the lemma the tagger gives that form, and its reading. The tagger's own
#: readings of other such forms (すげー, おもしれー, うるせー) need no entry.
_CONTRACTED_ADJECTIVES: dict[str, tuple[str, str, str]] = {
    "やべ": ("やばい", "やばい", "ヤバイ"),
    "やっべ": ("やばい", "やばい", "ヤバイ"),
    "でけ": ("でかい", "でかい", "デカイ"),
    "でっけ": ("でっかい", "でかい", "デッカイ"),
    "こえ": ("こわい", "怖い", "コワイ"),
    "たけ": ("たかい", "高い", "タカイ"),
    "うぜ": ("うざい", "うざい", "ウザイ"),
    "うめ": ("うまい", "旨い", "ウマイ"),
    "かっけ": ("かっこいい", "かっこいい", "カッコイイ"),
    "めんどくせ": ("めんどくさい", "面倒臭い", "メンドクサイ"),
    "あったけ": ("あったかい", "暖かい", "アッタカイ"),
    "うっせ": ("うるさい", "煩い", "ウルサイ"),
}
_CONTRACTED_ENDING_MARKS = frozenset("ーえぇェ")
_CONTRACTED_MAX_TOKENS = 6


def _contracted_adjective(surface: str) -> tuple[str, str, str] | None:
    """The ``_CONTRACTED_ADJECTIVES`` row *surface* spells: a stem plus at least one ending mark."""
    stem = surface
    while stem and stem[-1] in _CONTRACTED_ENDING_MARKS:
        stem = stem[:-1]  # こえー is こえ + ー: an え may belong to the stem
        if stem in _CONTRACTED_ADJECTIVES:
            return _CONTRACTED_ADJECTIVES[stem]
    return None


def rejoin_contracted_adjectives(tokens: list) -> list:
    """Join the pieces the tagger cuts a contracted -ee adjective into (``_CONTRACTED_ADJECTIVES``).

    やべー arrives as や|べ|ー and でけー as で|けー: no piece is a word, so the
    adjective was never mined. A run of unspaced tokens whose surfaces join to
    a listed stem plus its ending marks (ー, え, ぇ) becomes one 形容詞 token
    with the fields the tagger gives すげー: the kana dictionary form as its
    orthBase, the listed lemma and reading. The longest such run from a token
    wins (やべーー). A stem inside a longer token never matches (行こう|ぜー has
    no う token), and a run the tagger already reads as one token is not a run.
    """
    out: list = []
    i = 0
    while i < len(tokens):
        joined = tokens[i].surface if isinstance(tokens[i].surface, str) else ""
        match: tuple[int, str, tuple[str, str, str]] | None = None
        for j in range(i + 1, min(i + _CONTRACTED_MAX_TOKENS, len(tokens))):
            surface = tokens[j].surface
            white_space = getattr(tokens[j], "white_space", "")
            if not isinstance(surface, str) or (isinstance(white_space, str) and white_space):
                break
            joined += surface
            row = _contracted_adjective(joined)
            if row is not None:
                match = (j, joined, row)
        if match is None or not hasattr(getattr(tokens[i], "feature", None), "_replace"):
            out.append(tokens[i])
            i += 1
            continue
        end, joined, (base, lemma, reading) = match
        fields = {
            "pos1": "形容詞",
            "pos2": "一般",
            "pos3": "*",
            "pos4": "*",
            "cType": "形容詞",
            "cForm": "終止形-一般",
            "lemma": lemma,
            "orth": joined,
            "orthBase": base,
            "goshu": "和",
            **dict.fromkeys(("lForm", "pronBase", "kanaBase", "formBase"), reading),
            **dict.fromkeys(("pron", "kana", "form"), hiragana_to_katakana(joined)),
        }
        out.append(RetaggedToken(tokens[i], fields, joined))
        i = end + 1
    return out


#: The mark that ends an adjective stem said as a cry (熱っ, 痛ッ), and the one
#: its -ee contraction is written with on the kanji (痛ぇ, 遅ぇよ).
_STEM_CRY_MARKS = frozenset("っッ")
_STEM_CONTRACTION_MARKS = frozenset("ぇェ")
#: What the tagger calls the kanji of a stem it does not read as one: 熱 名詞,
#: 痛 接尾辞, 高 接頭辞, 遅 記号, 早 副詞, 酷 形状詞.
_STEM_CRY_POS1 = frozenset({"名詞", "接尾辞", "接頭辞", "記号", "副詞", "形状詞"})


def _pause_at(tokens: list, i: int) -> bool:
    """Whether the line ends or pauses at ``tokens[i]``: no token, a space before it, or a mark."""
    if i >= len(tokens):
        return True
    white_space = getattr(tokens[i], "white_space", "")
    if isinstance(white_space, str) and white_space:
        return True
    return getattr(getattr(tokens[i], "feature", None), "pos1", None) in ("補助記号", "空白")


def _stem_cry_kanji(tokens: list, i: int) -> str | None:
    """The kanji of ``tokens[i]`` when the token is shaped like a stem cry, else ``None``.

    Either a one-kanji token directly before っ and a pause (熱|っ！) or before
    ぇ (痛|ぇ), or a verb the tagger makes of kanji + っ that nothing continues
    (早っ！ as 早る): a real 促音便 is followed by て or た.
    """
    token = tokens[i]
    surface = token.surface
    feature = getattr(token, "feature", None)
    if not isinstance(surface, str) or not hasattr(feature, "_replace"):
        return None
    if not surface or not "\u4e00" <= surface[0] <= "\u9fff":
        return None
    pos1 = getattr(feature, "pos1", None)
    if len(surface) == 2 and surface[1] in _STEM_CRY_MARKS:
        c_form = getattr(feature, "cForm", None)
        cut_verb = pos1 == "動詞" and isinstance(c_form, str) and c_form.startswith("連用形-促音便")
        return surface[0] if cut_verb and _pause_at(tokens, i + 1) else None
    if len(surface) != 1 or pos1 not in _STEM_CRY_POS1 or i + 1 >= len(tokens):
        return None
    mark = tokens[i + 1]
    white_space = getattr(mark, "white_space", "")
    if isinstance(white_space, str) and white_space:
        return None
    if mark.surface in _STEM_CONTRACTION_MARKS:
        return surface
    return surface if mark.surface in _STEM_CRY_MARKS and _pause_at(tokens, i + 2) else None


def _stem_adjective(kanji: str, tag: Callable[[str], Iterable[Any]], form_lookup: FormLookup) -> Any | None:
    """The tagger's token for kanji + い when the dictionary lists that as an i-adjective, else ``None``.

    The tagger alone is too generous (楽い, 空い, 大い are adjectives to it), and
    the dictionary alone gives no fields, so both must agree.
    """
    word = kanji + "い"
    found = list(tag(word))
    if len(found) != 1:
        return None
    feature = found[0].feature
    if getattr(feature, "pos1", None) != "形容詞" or getattr(feature, "orthBase", None) != word:
        return None
    reading = getattr(feature, "lForm", None)
    if not isinstance(reading, str) or len(reading) < 2:
        return None
    tags = {tag_name for _content, row_tags in form_lookup([word]).get(word, ()) for tag_name in row_tags.split()}
    return found[0] if "adj-i" in tags else None


def rejoin_adjective_stem_cries(
    tokens: list, tag: Callable[[str], Iterable[Any]], form_lookup: FormLookup | None
) -> list:
    """Read the kanji of an adjective cry as the adjective's stem (熱っ → 熱い, 痛ぇ → 痛い).

    The tagger knows 怖っ as the stem of 怖い followed by the mark っ, but reads
    熱っ as the noun 熱 (ねつ), 痛っ as a suffix, 高っ as a prefix and 早っ as the
    verb 早る, so the card was a one-kanji noun or a rare verb. A token shaped
    like such a cry (``_stem_cry_kanji``) whose kanji + い is a dictionary
    i-adjective takes the fields the tagger gives that adjective, in the stem
    form it gives 怖: the token keeps its surface, so nothing is re-cut. 何っ,
    嘘っ, 嫌っ and 違っ stay as they are (no such adjective), and so does a mark
    inside a word (全っ然). ``tag`` is the tagger; no dictionary, no change.
    """
    if form_lookup is None:
        return tokens
    cries = {i: kanji for i in range(len(tokens)) if (kanji := _stem_cry_kanji(tokens, i)) is not None}
    if not cries:
        return tokens
    for token in tokens:
        # fugashi parses a feature on first use, from memory the next tagger call reuses.
        getattr(token, "feature", None)
    out: list = []
    for i, token in enumerate(tokens):
        adjective = _stem_adjective(cries[i], tag, form_lookup) if i in cries else None
        if adjective is None:
            out.append(token)
            continue
        reading = adjective.feature.lForm
        stem = reading[:-1] + hiragana_to_katakana(token.surface[1:])
        fields = {
            "cForm": "語幹-一般",
            "orth": token.surface,
            **dict.fromkeys(("pronBase", "kanaBase", "formBase"), reading),
            **dict.fromkeys(("pron", "kana", "form"), stem),
        }
        retagged = RetaggedToken(adjective, fields, token.surface)
        retagged.white_space = getattr(token, "white_space", "")
        out.append(retagged)
    return out


def _merge_prefix_compounds(tokens: list, attest: AttestLookup | None = None) -> list:
    """Merge 接頭辞 + 名詞/形状詞 pairs into a single token.

    Only fires when the 接頭辞 surface is in _PREFIX_WHITELIST — this
    avoids over-merging on rare/unproductive prefixes (e.g. お+金).
    Empirically: 不+可能 → root is 形状詞, 無+関心 → root is 名詞, so
    both pos1 values are accepted as merge heads. The synthetic is
    emitted as pos1=名詞 (the compound is treated as a vocabulary unit,
    and 名詞 is what _merge_noun_suffixes expects as a head — this
    enables chaining like 不+可能+性 → 不可能 → 不可能性). pos2 inherits
    from the root, defaulting to 普通名詞 when unidic emits "*".

    Attested-or-bail gate (``attest`` not None): the prefix synthetic is minted
    when either its surface or its complete immediate noun-suffix chain is a
    dictionary headword. A final-chain hit licenses only the temporary prefix
    synthetic; ``_merge_noun_suffixes`` still validates and mints the final
    chain. Otherwise it bails — the 接頭辞 head is emitted alone (the inclusion
    gate drops it later, 接頭辞 ∉ allowed_pos) and the root re-enters the loop to
    be mined on its own (超反応 → 反応). One batched probe per line.
    ``attest=None`` mints unconditionally (pre-gate behavior). This pass cannot
    move to the matcher — the matcher's span-start requires a mineable POS and
    接頭辞 is not one, which would give 不可能 → 可能.
    """
    attested = _attested_prefix_surfaces(tokens, attest) if attest is not None else None
    merged: list = []
    i, n = 0, len(tokens)
    while i < n:
        head = tokens[i]
        try:
            head_pos1 = head.feature.pos1
        except AttributeError:
            _guard("_merge_prefix_compounds#1")
            merged.append(head)
            i += 1
            continue
        if head_pos1 == "接頭辞" and head.surface in _PREFIX_WHITELIST and i + 1 < n:
            root = tokens[i + 1]
            try:
                root_pos1 = root.feature.pos1
                raw_root_pos2 = root.feature.pos2
            except AttributeError:
                _guard("_merge_prefix_compounds#2")
                merged.append(head)
                i += 1
                continue
            if root_pos1 in {"名詞", "形状詞"}:
                surf = head.surface + root.surface
                if attested is not None:
                    suffix_run = _nominal_suffix_run(tokens, i + 2)
                    final_surf = surf + "".join(t.surface for t in suffix_run)
                    # The full chain may license this temporary synthetic; the
                    # noun-suffix pass still owns the final mint-or-bail gate.
                    if surf not in attested and final_surf not in attested:
                        merged.append(head)
                        i += 1
                        continue
                # Treat unidic's "*" placeholder as missing pos2.
                root_pos2 = raw_root_pos2 if raw_root_pos2 and raw_root_pos2 != "*" else "普通名詞"
                try:
                    head_kana = head.feature.kana or head.surface
                except AttributeError:
                    _guard("_merge_prefix_compounds#3")
                    head_kana = head.surface
                try:
                    root_kana = root.feature.kana or root.surface
                except AttributeError:
                    _guard("_merge_prefix_compounds#4")
                    root_kana = root.surface
                try:
                    head_lemma = extract_lemma(head)
                except AttributeError:
                    _guard("_merge_prefix_compounds#5")
                    head_lemma = head.surface
                try:
                    root_lemma = extract_lemma(root)
                except AttributeError:
                    _guard("_merge_prefix_compounds#6")
                    root_lemma = root.surface
                merged.append(
                    SyntheticToken(
                        surface=surf,
                        pos1="名詞",
                        pos2=root_pos2,
                        lemma=head_lemma + root_lemma,
                        kana=head_kana + root_kana,
                    )
                )
                i += 2
                continue
        merged.append(head)
        i += 1
    return merged


def _verb_nominalizer_suffix(tokens: list, i: int) -> bool:
    """Whether ``tokens[i]`` is a 連用形 verb and ``tokens[i + 1]`` a {方, 手, 様} nominalizer."""
    if i + 1 >= len(tokens):
        return False
    head, suffix = tokens[i], tokens[i + 1]
    try:
        head_pos1 = head.feature.pos1
        head_c_form = head.feature.cForm
        suf_pos1 = suffix.feature.pos1
        suf_pos2 = suffix.feature.pos2
    except AttributeError:
        return False
    return (
        head_pos1 == "動詞"
        and isinstance(head_c_form, str)
        and head_c_form.startswith("連用形")
        and suf_pos1 == "接尾辞"
        and suf_pos2 == "名詞的"
        and suffix.surface in _VERB_NOMINALIZER_SUFFIXES
    )


def _merge_verb_nominalizers(tokens: list, attest: AttestLookup | None = None) -> list:
    """Merge 動詞(連用形) + 接尾辞(名詞的) verb-stem nominalizers.

    Only fires when the suffix surface is in _VERB_NOMINALIZER_SUFFIXES
    ({方, 手, 様}). Crucially uses the verb's CONJUGATED surface
    (連用形, e.g. 言い/読み/生き) — NOT its lemma — so the merged form
    is 言い方 not 言う方. The synthetic is emitted as pos1=名詞,
    pos2=普通名詞 (the compound is nominalized).

    ``lemma`` is set to the merged surface (NOT head.lemma + suffix.lemma)
    because the dictionary entry IS 言い方 / 読み方 — using 言う + 方 would
    yield 言う方, which is not a headword and would miss dictionary lookups.

    With ``attest`` the merge needs a dictionary entry (言い方): an unattested
    戦い方 has no definition and is dropped before it reaches a card, so the
    merge would only cost the line its verb. Left apart, 戦い mines as 戦う and
    方 falls to the suffix reject. いい方 keeps the merge: it is mostly 良い方
    'the better one', and apart it would mine the tagger's 言う.
    """
    attested: set[str] | None = None
    if attest is not None:
        candidates = {
            tokens[i].surface + tokens[i + 1].surface
            for i in range(len(tokens))
            if _verb_nominalizer_suffix(tokens, i) and tokens[i].surface != "いい"
        }
        attested = attest(sorted(candidates)) if candidates else set()
    merged: list = []
    i, n = 0, len(tokens)
    while i < n:
        head = tokens[i]
        try:
            head_pos1 = head.feature.pos1
            head_c_form = head.feature.cForm
        except AttributeError:
            _guard("_merge_verb_nominalizers#1")
            merged.append(head)
            i += 1
            continue
        if head_pos1 == "動詞" and isinstance(head_c_form, str) and head_c_form.startswith("連用形") and i + 1 < n:
            suffix = tokens[i + 1]
            try:
                suf_pos1 = suffix.feature.pos1
                suf_pos2 = suffix.feature.pos2
            except AttributeError:
                _guard("_merge_verb_nominalizers#2")
                merged.append(head)
                i += 1
                continue
            if (
                suf_pos1 == "接尾辞"
                and suf_pos2 == "名詞的"
                and suffix.surface in _VERB_NOMINALIZER_SUFFIXES
                and (attested is None or head.surface == "いい" or head.surface + suffix.surface in attested)
            ):
                surf = head.surface + suffix.surface
                try:
                    head_kana = head.feature.kana or head.surface
                except AttributeError:
                    _guard("_merge_verb_nominalizers#3")
                    head_kana = head.surface
                try:
                    suf_kana = suffix.feature.kana or suffix.surface
                except AttributeError:
                    _guard("_merge_verb_nominalizers#4")
                    suf_kana = suffix.surface
                merged.append(
                    SyntheticToken(
                        surface=surf,
                        pos1="名詞",
                        pos2="普通名詞",
                        lemma=surf,
                        kana=head_kana + suf_kana,
                    )
                )
                i += 2
                continue
        merged.append(head)
        i += 1
    return merged


def _is_run_identity_kana(char: str) -> bool:
    """Whether ``char`` counts toward a repeated-kana run.

    Hiragana or katakana, EXCLUDING the long-vowel mark ー and the sokuon っ/ッ:
    those legitimately repeat in stylized text (ーーー) and geminate runs, so they
    must not trip the reject.
    """
    if char in ("ー", "っ", "ッ"):
        return False
    return ("ぁ" <= char <= "ゖ") or ("ァ" <= char <= "ヺ")


def _has_repeated_kana_run(surface: str) -> bool:
    """True when ``surface`` holds ≥3 consecutive identical run-identity kana.

    Laughter/scream debris (どおおおお → the おおおっ token, merged シシシ) that
    unidic mis-tags as a content word or the kana-recovery seam would re-admit. ー
    and っ/ッ are excluded from the identity alphabet (see _is_run_identity_kana).
    """
    run = 1
    for i in range(1, len(surface)):
        if surface[i] == surface[i - 1]:
            run += 1
        else:
            run = 1
        if run >= 3 and _is_run_identity_kana(surface[i]):
            return True
    return False


#: Particles, auxiliary verbs, symbols, punctuation: never content, never
#: rescued by a whitelist entry (R1).
_JA_FUNCTION_POS = frozenset({"助詞", "助動詞", "記号", "補助記号"})

#: Which check of :class:`TokenInclusionRule` turned a token away (``rejection``).
#: STRUCTURE is the floor no setting or whitelist entry lowers; the rest are preferences.
REJECT_STRUCTURE = "structure"
REJECT_WORD_TYPE = "word_type"  # interjection/filler, pos1 not allowed, excluded pos2
REJECT_SOUND_EFFECT = "sound_effect"  # the katakana onomatopoeia rejections
REJECT_KANA_ONLY = "kana_only"  # final script decision on a kana-only surface
REJECT_SCRIPT = "script"  # final script decision on any other surface


@dataclass(frozen=True)
class TokenInclusionRule:
    """POS/subtype gate deciding which tokens count as mineable content words.

    Value object built from config (``allowed_pos`` / ``excluded_subtypes``)
    so the inclusion decision is usable without an ``AnkiMinerConfig``.
    """

    allowed_pos: frozenset[str]
    excluded_subtypes: frozenset[str]
    #: Replaces the FINAL script decision of should_include for non-JA
    #: languages: a pure-hangul word carries no kanji, so the ja ladder would
    #: mine nothing for Korean. None (the default) keeps the ja path exactly as
    #: it was - content_gate_ok, the katakana/loanword branches and the has_kanji
    #: fallback all unchanged.
    script_gate: Callable[[str], bool] | None = None
    #: Tags a whitelisted card front may rescue from a rejection (R1): the
    #: profile's ``PosDefaults.rescuable_tags``. Empty rescues nothing.
    rescuable_tags: frozenset[str] = frozenset()

    def structural_ok(self, word_token) -> bool:
        """Whether a token is a real, lemma-bearing, non-function word.

        The floor no setting and no whitelist entry can lower: empty or
        whitespace surfaces, repeated-kana runs, tokens without POS or lemma,
        and the particle/auxiliary/symbol/punctuation classes. Pure and I/O-free.
        """
        surface = word_token.surface
        if not surface or not surface.strip():
            return False
        # Reject ≥3 consecutive identical kana: laughter/scream runs (どおおおお →
        # the おおおっ token, merged シシシ) unidic mis-tags as content words or the
        # kana-recovery seam would re-admit. Placed here (the single gate both
        # should_include and the recovery probe route through) so include-path,
        # kana recovery and count/mine parity are covered at once; ー and っ/ッ are
        # excluded so ーーー stylistics and geminate runs survive.
        if _has_repeated_kana_run(surface):
            return False
        try:
            pos1 = word_token.feature.pos1
        except AttributeError:
            _guard("content_gate_ok#1")
            return False
        if pos1 in _JA_FUNCTION_POS:
            return False
        try:
            return bool(word_token.feature.lemma)
        except AttributeError:
            _guard("content_gate_ok#2")
            return False

    def rescuable(self, word_token) -> bool:
        """Whether a whitelist entry may override this token's rejection (R1).

        A fail-safe allowlist: pos1 must be a tag the profile lists as
        rescuable, and an excluded pos2 is overridden only when it is listed
        too (固有名詞 is, 数詞 is not). ``structural_ok`` is never overridden.
        """
        if not self.rescuable_tags or not self.structural_ok(word_token):
            return False
        feature = word_token.feature
        if feature.pos1 not in self.rescuable_tags:
            return False
        pos2 = getattr(feature, "pos2", None)
        return not (pos2 and pos2 in self.excluded_subtypes and pos2 not in self.rescuable_tags)

    def content_gate_ok(self, word_token) -> bool:
        """Content-word gate WITHOUT the final pure-hiragana script decision.

        Everything ``should_include`` checks — empty/whitespace, POS-attribute
        presence, particle/aux/symbol/interjection skip, ``allowed_pos``
        membership, excluded ``pos2`` subtype, non-empty lemma, and the
        katakana-onomatopoeia REJECTIONS — EXCEPT the ``has_kanji`` script gate
        (and the katakana ≥2-char / mixed-loanword ACCEPTANCE, which are script
        decisions ``should_include`` applies once this returns ``True``).

        Pure and I/O-free. Single source of truth for "is this a real content
        word", reused by two callers: ``should_include`` (which layers the
        script gate on top) and the parser's kana-recovery seam
        (``subtitle_parser._recover_kana_content_word``), which needs the
        content decision for a pure-hiragana token WITHOUT the script gate that
        would otherwise drop it — the dictionary-attestation probe lives at the
        parser layer to keep this module pure.

        Args:
            word_token: MeCab word token

        Returns:
            True if the token clears every non-script content check.
        """
        return self.content_gate_rejection(word_token) is None

    def content_gate_rejection(self, word_token) -> str | None:
        """Which non-script content check turns ``word_token`` away, or None when all pass.

        The single body behind :meth:`content_gate_ok` and :meth:`rejection`:
        every check of the gate in its order, naming the gate instead of
        returning False. Pure and I/O-free.
        """
        # Structure first (empty, repeated kana, no POS/lemma, function classes);
        # the checks below are the preferences a whitelist entry may override.
        if not self.structural_ok(word_token):
            return REJECT_STRUCTURE
        surface = word_token.surface

        # Get part-of-speech tags
        try:
            pos1 = word_token.feature.pos1  # Main POS
            pos2 = word_token.feature.pos2  # Sub POS
        except AttributeError:
            _guard("content_gate_ok#1")
            return REJECT_STRUCTURE

        # Skip interjections and fillers
        if pos1 in ["感動詞", "フィラー"]:
            return REJECT_WORD_TYPE

        # Check if it's a content word (noun, verb, adjective, adverb)
        if pos1 not in self.allowed_pos:
            return REJECT_WORD_TYPE

        # Check for excluded subtypes
        if pos2 and pos2 in self.excluded_subtypes:
            return REJECT_WORD_TYPE

        # Katakana-onomatopoeia REJECTIONS (the ≥2-char katakana ACCEPTANCE is a
        # script decision applied by should_include, not here). has_kanji uses
        # the shared ported CJK_IDEOGRAPH_RANGES (Unified + Ext A-I + compat +
        # astral) so kanji outside the BMP Unified block also count.
        has_kanji = any(is_cjk_ideograph(c) for c in surface)
        is_katakana = all("\u30a0" <= c <= "\u30ff" or c in "ー・" for c in surface if c.strip())
        if is_katakana and not has_kanji:
            stripped = surface.replace("ッ", "").replace("ー", "").replace("・", "")
            unique_chars = set(stripped)
            # 1-2 unique chars → likely onomatopoeia/mimetic. Gate on 副詞
            # (adverb) POS: mimetic words (ドキドキ, ふわふわ) are adverbs;
            # 2-char katakana NOUNS (ビル, バス, ドア) are loanwords and must
            # fall through to should_include's ≥2-char acceptance floor.
            if pos1 == "副詞" and len(unique_chars) <= 2 and len(surface) <= 4:
                return REJECT_SOUND_EFFECT
            # Short katakana ending in small tsu → likely sound effect.
            if surface.endswith("ッ") and len(surface) <= 3:
                return REJECT_SOUND_EFFECT

        return None

    def should_include(self, word_token) -> bool:
        """Whether a token is a mineable content word.

        Applies the POS/subtype/script inclusion gate. Only surface forms
        containing kanji (or valid katakana loanwords) are mined; pure-hiragana
        content words are rejected because MeCab can't reliably tell a real kana
        word from a grammar fragment. (The parser layer recovers a curated,
        dictionary-attested slice of those at ``_should_include_word``; this
        rule stays script-only and pure.)

        Args:
            word_token: MeCab word token

        Returns:
            True if word should be included, False otherwise
        """
        return self.rejection(word_token) is None

    def rejection(self, word_token) -> str | None:
        """Which check :meth:`should_include` fails on, or None when it includes the token.

        :meth:`content_gate_rejection` plus the final script decision. The
        parser's ``_mine_token`` calls this directly, so naming the gate costs
        the hot path nothing over :meth:`should_include`.
        """
        # Every non-script content check lives in content_gate_rejection (single
        # source of truth, reused by the kana-recovery seam); this method layers
        # only the script gate on top — no check is duplicated here.
        reason = self.content_gate_rejection(word_token)
        if reason is not None:
            return reason

        surface = word_token.surface
        if self.script_gate is not None:
            return None if self.script_gate(surface) else REJECT_SCRIPT

        feature = word_token.feature
        pos1 = feature.pos1
        has_kanji = any(is_cjk_ideograph(c) for c in surface)
        is_katakana = all("\u30a0" <= c <= "\u30ff" or c in "ー・" for c in surface if c.strip())

        # Katakana-only words: onomatopoeia already rejected by the content gate,
        # so accept any remaining ≥2-char loanword (ビル, コンピューター).
        if is_katakana and not has_kanji:
            return None if len(surface) >= 2 else REJECT_KANA_ONLY

        # Mixed katakana+hiragana loanword verbs/adjectives (サボる, ググる,
        # ディスる, ヤバい): has_kanji is False and is_katakana is False because
        # the hiragana okurigana breaks the all-katakana test, so the script
        # gate below would drop them. Accept when the dictionary form carries
        # katakana — 動詞/形容詞 only, never pure-hiragana tokens (dropped here
        # by design; recovered at the parser seam) or other POS.
        if pos1 in ("動詞", "形容詞"):
            orth_base = getattr(feature, "orthBase", None)
            dict_form = orth_base if isinstance(orth_base, str) and orth_base else feature.lemma
            if any("゠" <= c <= "ヿ" for c in dict_form):
                return None

        # Words with kanji are included; pure hiragana (no kanji, not katakana)
        # is rejected — the pre-existing script gate.
        if has_kanji:
            return None
        return REJECT_KANA_ONLY if is_kana_only(surface) else REJECT_SCRIPT
