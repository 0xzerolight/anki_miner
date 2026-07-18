"""Pure token-level morphology shared by subtitle parsing.

Compound-merge passes, lemma/reading extraction and the POS/subtype
inclusion gate, relocated out of ``subtitle_parser`` so they are usable
without the file-parsing/caching service. Everything here operates on
fugashi-shaped tokens (``.surface``, ``.feature.{pos1,pos2,lemma,kana,orthBase}``)
and performs no I/O.

Import direction is one-way: ``subtitle_parser`` imports from this module;
this module must never import ``subtitle_parser``.
"""

from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Iterator

from anki_miner.utils.ja_normalize import is_cjk_ideograph
from anki_miner.utils.text_utils import hiragana_to_katakana, katakana_to_hiragana

# Batch attested-readings probe (DefinitionService.offline_term_readings):
# term -> readings, best-first, hiragana-folded. See attest_merged_readings.
ReadingLookup = Callable[[list[str]], dict[str, list[str]]]

# Batch offline existence probe (DefinitionService.offline_terms_exist): a list
# of candidate surfaces -> the attested SUBSET. Injected into the compound-merge
# gate (merge_compound_suffixes) so morphology stays SQLite-free — the same
# dependency-injection pattern as ReadingLookup / attest_merged_readings. When
# None, the merge passes run UNGATED (output byte-identical to the pre-gate
# behavior); when present, the noun-suffix and prefix passes bail any synthetic
# compound the dictionary does not attest.
AttestLookup = Callable[[list[str]], set[str]]

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
# reading. Unlike the context-licensed kinship table above, each listed spelling
# reads its wrong value in EVERY context — no correct-reading token exists to
# protect — so the remap is unconditional. Applied in ``_emit_word``'s Expression
# fields (both the mined==surface branch and the headword-derived else-branch).
# Evidence probed on the shipping unidic-lite dictionary (2026-07):
#   (一日, ついたち) → いちにち: UniDic emits ツイタチ for the merged 一日 token in
#       every context (incl. ２４時間の一日); the calendar-date sense loss is
#       documented and accepted (standalone 一日 tokenizes as 一+日, unaffected).
#   (仏, ふつ) → ほとけ: UniDic emits フツ (the 仏=France abbreviation reading)
#       universally; no "leave ほとけ alone" token exists — France-sense loss accepted.
#   (マズい, まじい) → まずい: the katakana-ズ spelling misreads as マジイ; the 漢字
#       form 不味い reads correctly and is untouched.
#   (込む, ごむ) → こむ: the isolated 込む verb misreads as the loanword ゴム
#       (rubber); compounds (飲み込む→のみこむ) read correctly and are untouched.
_READING_OVERRIDES: dict[tuple[str, str], str] = {
    ("一日", "ついたち"): "いちにち",
    ("仏", "ふつ"): "ほとけ",
    ("マズい", "まじい"): "まずい",
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
            special = None
        if special is None:
            out.append(tok)
            continue
        try:
            pos1 = tok.feature.pos1
            pos2 = tok.feature.pos2
            lemma = extract_lemma(tok)
        except AttributeError:
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

    # Strip unidic's disambiguator tail: an English gloss
    # ("スクランブル-scramble", "ロック-rock（音楽）" — the fullwidth parens
    # defeat a plain isascii() check, "メリーゴーランド-merry-go-round" — the
    # gloss itself is hyphenated, hence splitting on the FIRST hyphen) or the
    # token's own POS name ("君-代名詞"). Decorated lemmas miss every
    # lemma-keyed lookup (frequency/pitch/offline-definition existence), which
    # key on the clean headword. Japanese name segments (メル-ビル) have
    # neither an ASCII letter nor a POS-name tail and are kept intact.
    if "-" in lemma:
        head, _, tail = lemma.partition("-")
        pos1 = getattr(getattr(word_token, "feature", None), "pos1", None)
        if head and tail and (any(c.isascii() and c.isalpha() for c in tail) or tail == pos1):
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

    unidic gives potential verbs (保てる←保つ), ra-nuki forms (見れる←見る),
    archaic i-adjective bases (良し←良い) and classical 連体形 ク-stems
    (美しき: orthBase 美し←美しい) their own orthBase while lemma points at the
    parent headword. Mining orthBase makes a 保てる card distinct from an
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


def merge_compound_suffixes(tokens: list, attest: AttestLookup | None = None) -> list:
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

    ``attest`` gates passes 1 and 2: a synthetic prefix/noun-suffix compound is
    minted only when the dictionary attests its surface (or, for the noun-suffix
    pass, it is a curated kinship compound — 兄ちゃん — whose reading must be
    preserved even though no dictionary attests it). An unattested candidate
    bails the WHOLE greedy chain to its bare components, letting the downstream
    dictionary matcher recover the longest attested sub-span (入院中的 → 入院中).
    Pass 3 is NEVER gated — its {方,手,様} whitelist is productive, near-zero
    junk. ``attest=None`` (the default that keeps every existing direct caller
    byte-identical) leaves all three passes ungated.
    """
    tokens = _merge_prefix_compounds(tokens, attest)
    tokens = _merge_noun_suffixes(tokens, attest)
    tokens = _merge_verb_nominalizers(tokens)
    return tokens


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
    (``feature.kana_special``, d848257) are skipped — the table outranks the
    dictionary. Real UniDic tokens are never touched (polyphonic 方/中 keep
    their contextual reading). Flags land on ``token.feature`` (a
    ``SimpleNamespace`` — ``SyntheticToken`` declares ``__slots__``):
    ``kana_attested`` on cases 1-3 (``_emit_word`` trusts the token kana), and
    ``kana_overridden`` on cases 2-3 only (the sentence display path merges
    only spans whose rendering was actually wrong; see
    ``replace_overridden_spans``). Mutates the synthetic tokens in place —
    they are per-line objects created by the merge passes above. Returns
    ``tokens`` unchanged (and issues NO lookup) when ``reading_lookup`` is
    ``None`` or the line produced no synthetics.
    """
    if reading_lookup is None:
        return tokens
    synthetics = [t for t in tokens if isinstance(t, SyntheticToken) and not getattr(t.feature, "kana_special", False)]
    if not synthetics:
        return tokens
    attested_map = reading_lookup(sorted({t.surface for t in synthetics}))
    for tok in synthetics:
        attested = attested_map.get(tok.surface)
        if not attested:
            continue
        concat = katakana_to_hiragana(tok.feature.kana or "")
        folded = [katakana_to_hiragana(r) for r in attested]
        if concat in folded:
            tok.feature.kana_attested = True
            continue
        if len(attested) == 1:
            chosen = folded[0]
        else:
            chosen = min(folded, key=lambda r: (_edit_distance(r, concat), folded.index(r)))
        tok.feature.kana = hiragana_to_katakana(chosen)
        tok.feature.kana_attested = True
        tok.feature.kana_overridden = True
    return tokens


def replace_overridden_spans(text: str, raw_tokens: list, merged_tokens: list) -> list:
    """Carry attested-overridden compound readings into the sentence stream.

    Sentence furigana/reading/bold are generated from the RAW token stream, so
    a corrected kana on a merged token never reaches them on its own. This pass
    aligns each merged token back to its consecutive raw-token run (the merged
    stream is a grouping of the raw stream — walk both, matching surface
    concatenation) and, ONLY for spans whose reading attestation actually
    overrode the kana (``feature.kana_overridden``), replaces the run with one
    ``SyntheticToken`` carrying the attested kana. Kept-as-attested compounds
    (何人, 副作用) keep today's per-morpheme rendering. The concatenated stream
    text is byte-identical, so downstream ``str.find`` cursoring and
    bold-offset math stay valid — with one guard: a replacement is skipped when
    the merged surface was stitched across source whitespace (MeCab drops it),
    because the single-token surface would then not be locatable in the line
    text; the raw run is kept instead (bail-keep). Any alignment mismatch
    returns ``raw_tokens`` untouched.
    """
    if not any(isinstance(m, SyntheticToken) and getattr(m.feature, "kana_overridden", False) for m in merged_tokens):
        return raw_tokens
    out: list = []
    ri, rn = 0, len(raw_tokens)
    for m in merged_tokens:
        if ri < rn and raw_tokens[ri] is m:
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
        if getattr(m.feature, "kana_overridden", False) and m.surface in text:
            # ``m.surface in text`` = the whitespace-stitch guard: a merge
            # across a source space is not locatable as one token in the line.
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
        return []
    chain: list = []
    j = i + 1
    n = len(tokens)
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
    return chain


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
    concatenated surface is a dictionary headword OR it is a curated kinship
    compound (``special_head`` — 兄ちゃん/父さま, whose reading the dictionary
    does not attest but which we must keep). Otherwise the WHOLE greedy chain
    bails to its components: the head is emitted alone and the suffix tokens
    re-enter the loop as bare tokens (a following dictionary matcher can then
    recover the longest attested sub-span). One batched probe per line covers
    every candidate. ``attest=None`` mints unconditionally (pre-gate behavior).

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
            # Attested-or-bail: unattested, non-kinship chains fragment back to
            # their components. Kinship carve-out (special_head not None) mints
            # even when unattested.
            if attested is not None and surf not in attested and special_head is None:
                merged.append(head)
                i += 1
                continue
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
            head_kana_final = special_head if special_head is not None else head_kana
            kana = head_kana_final + "".join(suffix_kanas)
            kana_special = special_head is not None
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
    """One batched attestation probe of every 接頭辞+名詞/形状詞 compound surface.

    Greedy walk mirroring ``_merge_prefix_compounds``, so the probed set is
    exactly what the merge loop weighs; bail-invariant (a bailed prefix
    re-exposes only its 名詞/形状詞 root, which never starts a 接頭辞 compound).
    Returns the attested SUBSET; no probe when the line has no candidate.
    """
    surfaces: set[str] = set()
    i, n = 0, len(tokens)
    while i < n:
        head = tokens[i]
        try:
            head_pos1 = head.feature.pos1
        except AttributeError:
            i += 1
            continue
        if head_pos1 == "接頭辞" and head.surface in _PREFIX_WHITELIST and i + 1 < n:
            root = tokens[i + 1]
            try:
                root_pos1 = root.feature.pos1
            except AttributeError:
                i += 1
                continue
            if root_pos1 in {"名詞", "形状詞"}:
                surfaces.add(head.surface + root.surface)
                i += 2
                continue
        i += 1
    if not surfaces:
        return set()
    return attest(sorted(surfaces))


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
    only when its surface is a dictionary headword. Otherwise it bails — the
    接頭辞 head is emitted alone (the inclusion gate drops it later, 接頭辞 ∉
    allowed_pos) and the root re-enters the loop to be mined on its own (超反応
    → 反応). One batched probe per line. ``attest=None`` mints unconditionally
    (pre-gate behavior). This pass cannot move to the matcher — the matcher's
    span-start requires a mineable POS and 接頭辞 is not one, which would give
    不可能 → 可能.
    """
    attested = _attested_prefix_surfaces(tokens, attest) if attest is not None else None
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
                surf = head.surface + root.surface
                # Attested-or-bail: an unattested prefix compound bails — append
                # the 接頭辞 (dropped later by the inclusion gate) and let the
                # root re-enter and be mined on its own.
                if attested is not None and surf not in attested:
                    merged.append(head)
                    i += 1
                    continue
                # Treat unidic's "*" placeholder as missing pos2.
                root_pos2 = raw_root_pos2 if raw_root_pos2 and raw_root_pos2 != "*" else "普通名詞"
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
                return False
            # Short katakana ending in small tsu → likely sound effect.
            if surface.endswith("ッ") and len(surface) <= 3:
                return False

        return True

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
        # Every non-script content check lives in content_gate_ok (single source
        # of truth, reused by the kana-recovery seam); this method layers only
        # the script gate on top — no check is duplicated here.
        if not self.content_gate_ok(word_token):
            return False

        surface = word_token.surface
        feature = word_token.feature
        pos1 = feature.pos1
        has_kanji = any(is_cjk_ideograph(c) for c in surface)
        is_katakana = all("\u30a0" <= c <= "\u30ff" or c in "ー・" for c in surface if c.strip())

        # Katakana-only words: onomatopoeia already rejected by content_gate_ok,
        # so accept any remaining ≥2-char loanword (ビル, コンピューター).
        if is_katakana and not has_kanji:
            return len(surface) >= 2

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
                return True

        # Words with kanji are included; pure hiragana (no kanji, not katakana)
        # is rejected — the pre-existing script gate.
        return has_kanji
