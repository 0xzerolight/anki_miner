"""Pure token-level morphology shared by subtitle parsing.

Compound-merge passes, lemma/reading extraction and the POS/subtype
inclusion gate, relocated out of ``subtitle_parser`` so they are usable
without the file-parsing/caching service. Everything here operates on
fugashi-shaped tokens (``.surface``, ``.feature.{pos1,pos2,lemma,kana,orthBase}``)
and performs no I/O.

Import direction is one-way: ``subtitle_parser`` imports from this module;
this module must never import ``subtitle_parser``.
"""

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Iterator

from anki_miner.services.ja_normalize import is_cjk_ideograph

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


class SyntheticToken:
    """Duck-typed token replacement for merged compounds.

    Mimics fugashi token attribute access (.surface,
    .feature.{pos1,pos2,lemma,kana}). Subclassed by
    ``compound_matcher.CompoundSyntheticToken`` for dictionary-attested
    merges.
    """

    __slots__ = ("surface", "feature")

    def __init__(self, surface: str, pos1: str, pos2: str, lemma: str, kana: str):
        self.surface = surface
        self.feature = SimpleNamespace(pos1=pos1, pos2=pos2, lemma=lemma, kana=kana)


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
        lemma = word_token.surface

    # Clean lemma - strip unidic's English-gloss tail
    # (e.g. "スクランブル-scramble" -> "スクランブル") but leave Japanese
    # names like "メル-ビル" intact: only split when the tail is ASCII.
    if "-" in lemma:
        head, _, tail = lemma.partition("-")
        if tail.isascii():
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
        orth_base = None
    if not orth_base:
        return extract_lemma(word_token)
    return str(orth_base)


# Potential-verb paradigm (godan e-row + ら抜き) and adjective ク-form pairs:
# (derived orthBase suffix, base lemma suffix). Mirrors the potential rules in
# japanese_transforms.py (the "potential" transform); kept as data here because
# mining folds a HEADWORD (orthBase→lemma), not running the deinflection
# engine on text.
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
)


def mining_base(word_token) -> str:
    """orthBase for the card front, folded to lemma for derived sub-lemma entries.

    unidic gives potential verbs (保てる←保つ), ra-nuki forms (見れる←見る) and
    archaic i-adjective bases (良し←良い) their own orthBase while lemma points
    at the parent headword. Mining orthBase makes a 保てる card distinct from an
    existing 保つ card; folding to lemma dedupes them. Applies only to 動詞 /
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
    for derived, base in _FOLD_SUFFIX_PAIRS:
        if orth_base.endswith(derived) and len(orth_base) > len(derived) and orth_base[: -len(derived)] + base == lemma:
            return lemma
    return orth_base


def extract_reading(word_token) -> str:
    """Extract kana reading from word token.

    Args:
        word_token: MeCab word token

    Returns:
        Kana reading string
    """
    try:
        return str(word_token.feature.kana or word_token.surface)
    except AttributeError:
        return str(word_token.surface)


def iter_token_spans(text: str, tokens: list) -> Iterator[tuple[Any, int, int]]:
    """Yield ``(token, start, end)`` for each token locatable in ``text``.

    Locates each token's char span via ``str.find`` from a running
    cursor. MeCab silently drops whitespace from the token stream, so
    naive ``cursor += len(surface)`` walking drifts left by the count of
    preceding spaces and misaligns every downstream offset (bold
    wrapping, surface_start/end). Issue #20.

    Tokens whose surface is not find-able are dropped (defensive: should
    not happen for unmodified MeCab surfaces, but a merged compound whose
    components were whitespace-separated in the source concatenates to a
    space-free surface that is NOT find-able in ``text``). This locator
    is the single source of truth for that drop rule:
    ``parse_subtitle_file``, ``parse_subtitle_file_with_index`` AND
    ``count_lemmas`` must all route through it, or the count-vs-mine
    sets diverge and the Deck Builder preview over-promises (T-38).
    """
    cursor = 0
    for token in tokens:
        surface = token.surface
        idx = text.find(surface, cursor)
        if idx == -1:
            continue
        tok_end = idx + len(surface)
        cursor = tok_end
        yield token, idx, tok_end


def merge_compound_suffixes(tokens: list) -> list:
    """Run all compound-merge passes in dependency order.

    Order matters:
    1. _merge_prefix_compounds  — 接頭辞 + 名詞/形状詞 (e.g. 不+可能 → 不可能).
       Must run first so that downstream 名詞-suffix merge sees the
       synthetic 不可能 (pos1=名詞) as a valid head and chains correctly
       into 不可能性, 不可能的, etc.
    2. _merge_noun_suffixes     — 名詞 + 接尾辞(名詞的/形状詞的/副詞的)
       chains (e.g. 刑務+所 → 刑務所, 入院+中+的 → 入院中的).
    3. _merge_verb_nominalizers — 動詞(連用形) + 接尾辞(名詞的) where the
       suffix is a verb-stem nominalizer (方/手/様). Independent of (1)
       and (2) so order is irrelevant.
    """
    tokens = _merge_prefix_compounds(tokens)
    tokens = _merge_noun_suffixes(tokens)
    tokens = _merge_verb_nominalizers(tokens)
    return tokens


def _merge_noun_suffixes(tokens: list) -> list:
    """Merge 名詞 + 接尾辞(名詞的/形状詞的/副詞的) chains into a single token.

    Walks tokens left-to-right. When a 名詞 head is followed by one or
    more nominal-suffix tokens, both base and suffixes are consumed and
    replaced by a single _SyntheticToken whose surface is the concatenated
    form and whose lemma is reconstructed from each component's
    feature.lemma (falling back to surface when unidic emits "*"/None).
    Nouns rarely conjugate, so lemma usually equals surface, but morphemes
    like ~性 / ~中 / ~的 carry their own dictionary form and we preserve it.
    """
    merged: list = []
    i, n = 0, len(tokens)
    while i < n:
        head = tokens[i]
        try:
            head_pos1 = head.feature.pos1
        except AttributeError:
            merged.append(head)
            i += 1
            continue
        if head_pos1 == "名詞":
            j = i + 1
            chain: list = []
            while j < n:
                try:
                    p1 = tokens[j].feature.pos1
                    p2 = tokens[j].feature.pos2
                except AttributeError:
                    break
                if p1 == "接尾辞" and p2 in _NOMINAL_SUFFIX_POS2:
                    chain.append(tokens[j])
                    j += 1
                else:
                    break
            if chain:
                surf = head.surface + "".join(t.surface for t in chain)
                try:
                    head_kana = head.feature.kana or head.surface
                except AttributeError:
                    head_kana = head.surface
                suffix_kanas = []
                for t in chain:
                    try:
                        suffix_kanas.append(t.feature.kana or t.surface)
                    except AttributeError:
                        suffix_kanas.append(t.surface)
                kana = head_kana + "".join(suffix_kanas)
                try:
                    head_pos2 = head.feature.pos2 or "普通名詞"
                except AttributeError:
                    head_pos2 = "普通名詞"
                try:
                    head_lemma = extract_lemma(head)
                except AttributeError:
                    head_lemma = head.surface
                suffix_lemmas: list[str] = []
                for t in chain:
                    try:
                        suffix_lemmas.append(extract_lemma(t))
                    except AttributeError:
                        suffix_lemmas.append(t.surface)
                merged.append(
                    SyntheticToken(
                        surface=surf,
                        pos1="名詞",
                        pos2=head_pos2,
                        lemma=head_lemma + "".join(suffix_lemmas),
                        kana=kana,
                    )
                )
                i = j
                continue
        merged.append(head)
        i += 1
    return merged


def _merge_prefix_compounds(tokens: list) -> list:
    """Merge 接頭辞 + 名詞/形状詞 pairs into a single token.

    Only fires when the 接頭辞 surface is in _PREFIX_WHITELIST — this
    avoids over-merging on rare/unproductive prefixes (e.g. お+金).
    Empirically: 不+可能 → root is 形状詞, 無+関心 → root is 名詞, so
    both pos1 values are accepted as merge heads. The synthetic is
    emitted as pos1=名詞 (the compound is treated as a vocabulary unit,
    and 名詞 is what _merge_noun_suffixes expects as a head — this
    enables chaining like 不+可能+性 → 不可能 → 不可能性). pos2 inherits
    from the root, defaulting to 普通名詞 when unidic emits "*".
    """
    merged: list = []
    i, n = 0, len(tokens)
    while i < n:
        head = tokens[i]
        try:
            head_pos1 = head.feature.pos1
        except AttributeError:
            merged.append(head)
            i += 1
            continue
        if head_pos1 == "接頭辞" and head.surface in _PREFIX_WHITELIST and i + 1 < n:
            root = tokens[i + 1]
            try:
                root_pos1 = root.feature.pos1
                raw_root_pos2 = root.feature.pos2
            except AttributeError:
                merged.append(head)
                i += 1
                continue
            if root_pos1 in {"名詞", "形状詞"}:
                # Treat unidic's "*" placeholder as missing pos2.
                root_pos2 = raw_root_pos2 if raw_root_pos2 and raw_root_pos2 != "*" else "普通名詞"
                surf = head.surface + root.surface
                try:
                    head_kana = head.feature.kana or head.surface
                except AttributeError:
                    head_kana = head.surface
                try:
                    root_kana = root.feature.kana or root.surface
                except AttributeError:
                    root_kana = root.surface
                try:
                    head_lemma = extract_lemma(head)
                except AttributeError:
                    head_lemma = head.surface
                try:
                    root_lemma = extract_lemma(root)
                except AttributeError:
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


def _merge_verb_nominalizers(tokens: list) -> list:
    """Merge 動詞(連用形) + 接尾辞(名詞的) verb-stem nominalizers.

    Only fires when the suffix surface is in _VERB_NOMINALIZER_SUFFIXES
    ({方, 手, 様}). Crucially uses the verb's CONJUGATED surface
    (連用形, e.g. 言い/読み/生き) — NOT its lemma — so the merged form
    is 言い方 not 言う方. The synthetic is emitted as pos1=名詞,
    pos2=普通名詞 (the compound is nominalized).

    ``lemma`` is set to the merged surface (NOT head.lemma + suffix.lemma)
    because the dictionary entry IS 言い方 / 読み方 — using 言う + 方 would
    yield 言う方, which is not a headword and would miss dictionary lookups.
    """
    merged: list = []
    i, n = 0, len(tokens)
    while i < n:
        head = tokens[i]
        try:
            head_pos1 = head.feature.pos1
        except AttributeError:
            merged.append(head)
            i += 1
            continue
        if head_pos1 == "動詞" and i + 1 < n:
            suffix = tokens[i + 1]
            try:
                suf_pos1 = suffix.feature.pos1
                suf_pos2 = suffix.feature.pos2
            except AttributeError:
                merged.append(head)
                i += 1
                continue
            if suf_pos1 == "接尾辞" and suf_pos2 == "名詞的" and suffix.surface in _VERB_NOMINALIZER_SUFFIXES:
                surf = head.surface + suffix.surface
                try:
                    head_kana = head.feature.kana or head.surface
                except AttributeError:
                    head_kana = head.surface
                try:
                    suf_kana = suffix.feature.kana or suffix.surface
                except AttributeError:
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


@dataclass(frozen=True)
class TokenInclusionRule:
    """POS/subtype gate deciding which tokens count as mineable content words.

    Value object built from config (``allowed_pos`` / ``excluded_subtypes``)
    so the inclusion decision is usable without an ``AnkiMinerConfig``.
    """

    allowed_pos: frozenset[str]
    excluded_subtypes: frozenset[str]

    def should_include(self, word_token) -> bool:
        """Whether a token is a mineable content word.

        Applies the POS/subtype/script inclusion gate. Only surface forms
        containing kanji (or valid katakana loanwords) are mined; pure-hiragana
        content words are rejected because MeCab can't reliably tell a real kana
        word from a grammar fragment.

        Args:
            word_token: MeCab word token

        Returns:
            True if word should be included, False otherwise
        """
        surface = word_token.surface

        # Skip empty or whitespace-only tokens
        if not surface or not surface.strip():
            return False

        # Get part-of-speech tags
        try:
            pos1 = word_token.feature.pos1  # Main POS
            pos2 = word_token.feature.pos2  # Sub POS
        except AttributeError:
            return False

        # Skip particles, auxiliary verbs, symbols, punctuation
        if pos1 in ["助詞", "助動詞", "記号", "補助記号"]:
            return False

        # Skip interjections and fillers
        if pos1 in ["感動詞", "フィラー"]:
            return False

        # Check if it's a content word (noun, verb, adjective, adverb)
        if pos1 not in self.allowed_pos:
            return False

        # Check for excluded subtypes
        if pos2 and pos2 in self.excluded_subtypes:
            return False

        # Skip if no lemma available
        try:
            lemma = word_token.feature.lemma
            if not lemma:
                return False
        except AttributeError:
            return False

        # Check if word contains meaningful characters. Uses the shared ported
        # CJK_IDEOGRAPH_RANGES (Unified + Ext A-I + compat + astral) so kanji
        # outside the BMP Unified block (compat ideographs, astral Ext-B)
        # also count as kanji, not just U+4E00-U+9FFF.
        has_kanji = any(is_cjk_ideograph(c) for c in surface)
        is_katakana = all("\u30a0" <= c <= "\u30ff" or c in "ー・" for c in surface if c.strip())

        # For katakana-only words, apply stricter filtering
        if is_katakana and not has_kanji:
            # Skip onomatopoeia patterns
            stripped = surface.replace("ッ", "").replace("ー", "").replace("・", "")
            unique_chars = set(stripped)

            # If only 1-2 unique characters, likely onomatopoeia/mimetic word.
            # Gate on 副詞 (adverb) POS: mimetic/onomatopoeic words (ドキドキ,
            # ふわふわ) are tagged as adverbs; 2-char katakana NOUNS (ビル, バス,
            # ドア) are legitimate loanwords and must fall through to the ≥2-char
            # acceptance floor below.
            if pos1 == "副詞" and len(unique_chars) <= 2 and len(surface) <= 4:
                return False

            # If ends in small tsu and is short, likely sound effect
            if surface.endswith("ッ") and len(surface) <= 3:
                return False

            # Must be at least 2 chars to be valid katakana word
            return len(surface) >= 2

        # Words with kanji are included; pure hiragana (no kanji, not katakana)
        # is rejected — the pre-existing script gate.
        return has_kanji
