"""Grammar-cut words joined back after tagging (思わず mined as 思う).

unidic cuts a few lexicalized words at their grammar: 思わず is 思わ + ず, 実は is
実 + は, くだらない is くだら + ない. The compound matcher cannot rejoin them,
because a span may not end on a particle or an auxiliary (the rule that keeps
inflected headwords such as 気をつけて off card fronts). So the card names the
wrong word (思う 'think' for 思わず 'involuntarily', 実 'fruit' for 実は
'actually'), or, for the kana adjectives, there is no card at all.

A short curated table, not a rule: the general pattern (noun + に) would also
turn その時に into 時に 'by the way'. Each row is a JMdict entry whose meaning
differs from its first token's, chosen by hand against the real tagger.

Runs after the compound matcher, so a longer attested span over the same tokens
keeps priority. Every join is attested against the run's offline dictionaries
(term or reading: くだらない is a reading of 下らない), so a dictionary without
the entry keeps the split words and their cards. Lives outside ``morphology.py``
for the same reason ``masu_stem_nominalizer.py`` does: it holds an injected
lookup and a cache.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Callable, NamedTuple

from anki_miner.services.compound_matcher import CompoundDictionaryMatcher, CompoundSyntheticToken, _pos1, _pos2
from anki_miner.services.kana_words import KanaJoin, kana_words
from anki_miner.services.morphology import extract_lemma, extract_orth_base, extract_reading, word_surface
from anki_miner.utils.text_utils import is_kana_only, katakana_to_hiragana

# Batch term-OR-reading probe (DefinitionService.has_offline_definitions).
AttestLookup = Callable[[list[str]], dict[str, bool]]

# Adverbs and conjunctions the tagger cuts at a particle or at 未然形 + ず, as
# exact token surfaces; the card front is the joined surfaces. Left out on
# purpose because the split words are as common as the joined one: 時に (その時に
# 'at that time'), 先に and 前に (place + に), 残らず (何も残らず 'nothing was
# left'), 本当に (本当 already teaches it).
_FIXED_JOINS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("実", "は"),
        ("常", "に"),
        ("一緒", "に"),
        ("一気", "に"),
        ("互い", "に"),
        ("仮", "に"),
        ("確か", "に"),
        ("別", "に"),
        ("共", "に"),
        ("次第", "に"),
        ("徐々", "に"),
        ("実", "に"),
        ("同時", "に"),
        ("急", "に"),
        ("一斉", "に"),
        ("一向", "に"),
        ("非常", "に"),
        ("未だ", "に"),
        ("要する", "に"),
        ("因み", "に"),
        ("故", "に"),
        ("何", "か"),
        ("誰", "か"),
        ("何故", "か"),
        ("何", "と", "か"),
        ("何", "だ", "か"),
        ("思わ", "ず"),
        ("絶え", "ず"),
        ("相", "変わら", "ず"),
    }
)

# Verb orthBase in 未然形 + the negative auxiliary ない (なかっ, なく, ねえ), mapped
# to the i-adjective the pair spells. No 下る/詰まる rows: JMdict marks 下らない
# and 詰まらない usually-kana, so a kanji spelling is the verb (パイプが詰まらない,
# 千人を下らない). Kana たまる/つまる also spell 'does not accumulate/clog'
# (お金がたまらない); accepted, as the adjectives are far more common.
_NEGATIVE_ADJECTIVES: dict[str, str] = {
    "くだる": "くだらない",
    "つまる": "つまらない",
    "たまる": "たまらない",
    "堪る": "堪らない",
    "思いがける": "思いがけない",
    "否める": "否めない",
}

# What follows the て of どう|し|て when it is the て-form of する, by dictionary form
# in kana: the past, the polite ending and the て-auxiliaries (``_te_form_in_use``).
_TE_FOLLOWERS = frozenset({"た", "ます", "いる", "いい", "くれる", "ほしい", "もらう", "あげる", "おく", "みる"})
# The same after こう|し|て, plus requests and favours (こうしてください 'do it like this', こうしてもらえると).
_TE_ADVERBS = frozenset({"どうして", "こうして"})
_TE_REQUESTS = frozenset({"ください", "くださる", "ちょうだい", "やる", "もらえる", "いただく", "いただける"})
# What makes the で before it a copula: で|ある, で|ござい|ます (``_copula_after``).
_COPULA_VERBS = frozenset({"ある", "ござる"})
# Verbs that take an embedded question, by lemma (``_embedded_question``).
# Rows of a question word + か that are the question itself before one of those verbs (``_embedded_question``).
_QUESTION_ROWS = frozenset({"どこか", "どちらか", "どっちか"})
# Conjunctions of a pronoun + a particle, and the ones a mere space after them does not make (``_needs_a_comma``).
_PRONOUN_CONJUNCTIONS = frozenset({"それに", "そこで", "それも"})
# 何|かしら as written or read: 'something or other' only where its clause goes on (``_wonders_what``).
_SOMETHING_ROWS = frozenset({"なにかしら", "何かしら"})
# "start" rows that a mere space does not open (``_quotes_what_precedes``).
_AFTER_PUNCTUATION = frozenset({"ってことは", "いいから", "なら", "なのに", "では"})
# The rows of よう + the copula, and what they follow as the grammar point 'it seems' (``_no_predicate_before``).
_SEEMING_ROWS = frozenset({"ようだ", "ようです"})
_PREDICATE_POS1 = frozenset({"動詞", "助動詞", "形容詞"})
_QUESTION_VERBS = frozenset(
    {"分かる", "知る", "覚える", "忘れる", "聞く", "教える", "言う", "思う", "考える", "調べる"}
)
# One-token grammar rows that are the word only as the tagger's auxiliary (``_not_the_auxiliary``).
_AUXILIARY_ROWS = frozenset({"らしい", "やろ"})
# One-token rows that are the word only as the tagger's conjunction (``_not_the_conjunction``).
_CONJUNCTION_ROWS = frozenset({"けど", "じゃ", "んで"})
# Conjunctions that are a particle or the copula anywhere else: carded only while the run allows 接続詞,
# as when the tagger's own tag decided (the other kana rows join whatever the run allows).
_ALLOWED_POS_ROWS = _CONJUNCTION_ROWS | {"なら", "なのに", "では"}
# Rows that are the word only where the line or the sentence stops after them (``_runs_on``).
_STOP_AFTER = frozenset({"じゃね", "だったりして", "ものか"})
# Joined rows that end a predicate: a row right after one still follows an auxiliary (``_joined_pos1``).
_PREDICATE_ROWS = _AUXILIARY_ROWS | {"どうした"}
# Pre-noun rows whose tokens are an adverb + a verb's past anywhere else (``_adnominal_only``).
_ADNOMINAL_ROWS = frozenset({"そういった", "ちょっとした"})
# What makes に|つれ|て the verb 連れて: a verb of coming or going right after it (``_verb_te_form``).
_MOTION_VERBS = frozenset({"くる", "来る", "いく", "行く", "帰る"})
# Rows of a verb + ず that are a noun where a noun stands, and what follows that noun (``_noun_place``).
_NEGATIVE_NOUNS = frozenset({"役立たず"})
_NOUN_FOLLOWERS = frozenset({"が", "の", "め", "だ", "な", "は", "を", "と", "ども", "です", "じゃ"})
# Verbs of saying, by lemma: before one なん|と is 'what', before any other verb 'with what' (``_with_what``).
_SAYING_VERBS = frozenset({"言う", "申す", "呼ぶ", "仰る"})
# Verbs that take a quote, by lemma: before one なん|て is 何 + the quoting て, not the adverb (``_with_what``).
_QUOTING_VERBS = _SAYING_VERBS | {"書く", "読む"}
# The reply rows that are also the hearsay そうだ after a plain form (``_hearsay``).
_HEARSAY_ROWS = frozenset({"そうだ", "そうです"})
# "noun" rows that also open a clause before the noun they name (``_opens_before_a_noun``).
_NAMING_ROWS = frozenset({"という"})
# Nouns of people: after a clause, という before one is the verb, 'people who say …' (``_opens_before_a_noun``).
_PEOPLE = frozenset({"人", "者", "奴", "やつ", "方", "人間", "連中", "人々", "人たち"})
# The formal nouns a joined entry opens on (わけ|で|は|ない, はず|が|ない, こと|は|ない): ``_opens_before_a_noun``.
_JOINED_NOUN_HEADS = ("わけ", "はず", "こと", "もの")
_GRAMMAR_POS1 = frozenset({"助詞", "助動詞"})
_MAX_FIXED_TOKENS = max(len(key) for key in _FIXED_JOINS)
_MAX_KANA_TOKENS = max(len(key) for key in (*kana_words().joins, *kana_words().base_joins))
_CLAUSE_BREAK_POS1 = frozenset({"補助記号", "記号"})
_NOUN_POS1 = frozenset({"名詞", "代名詞", "接尾辞"})
_VERB_POS1 = frozenset({"動詞", "助動詞"})
# What ends a conditional before the obligation unit (行かなければ|ならない,
# 見ちゃ|いけない, 行ったら|いけません, 行かなきゃ|いけない). ては and ないと take two
# tokens (``_after_conditional``).
_CONDITIONAL_PARTICLES = frozenset({"ば", "ちゃ", "じゃ", "ちゃあ", "じゃあ"})
_CONDITIONAL_AUXILIARIES = frozenset({"たら", "だら", "なきゃ", "なけりゃ"})

# 思わず read as a negated verb, not the adverb: followed by に (思わずに
# 'without thinking') or after とは/とも (そうとは思わず 'not thinking so'). A bare
# と is left out: UniDic tags the conditional と (見ると思わず笑った) the same as the
# quoting one, so a bare 「…」と思わず quote still joins. Accepted. 絶えず and
# 相変わらず are never a negated verb, so the check is for 思わず alone.
_QUOTATIONS = (("と", "は"), ("と", "も"))


class _Join(NamedTuple):
    start: int
    end: int  # inclusive
    headword: str
    pos1: str
    # A kana word list row (services/kana_words.py): joined whatever the run's
    # allowed_pos, as the list names its words; line_start rows only where they
    # open a line or clause.
    listed: bool = False
    line_start: bool = False
    # A kana row's "noun" or "verb": joined only right after one; "alone": only before a pause.
    after: str = ""


def _negated_verb_context(tokens: list, start: int, end: int) -> bool:
    """Whether a 思わず span sits where the verb is negated rather than adverbial."""
    if end + 1 < len(tokens) and tokens[end + 1].surface == "に":
        return True
    before = tuple(token.surface for token in tokens[max(0, start - 2) : start])
    return any(before[-len(quote) :] == quote for quote in _QUOTATIONS)


def _fixed_join(tokens: list, i: int) -> _Join | None:
    """The longest fixed table entry starting at ``tokens[i]``, ending on grammar."""
    for size in range(min(_MAX_FIXED_TOKENS, len(tokens) - i), 1, -1):
        span = tokens[i : i + size]
        key = tuple(token.surface for token in span)
        if key not in _FIXED_JOINS or _pos1(span[-1]) not in _GRAMMAR_POS1:
            continue
        if key == ("思わ", "ず") and _negated_verb_context(tokens, i, i + size - 1):
            return None
        return _Join(i, i + size - 1, "".join(key), "副詞")
    return None


def _negative_adjective(tokens: list, i: int) -> _Join | None:
    """A table verb in 未然形 followed by the negative auxiliary ない."""
    verb, aux = tokens[i], tokens[i + 1]
    c_form = getattr(getattr(verb, "feature", None), "cForm", None)
    if _pos1(verb) != "動詞" or not isinstance(c_form, str) or not c_form.startswith("未然形"):
        return None
    headword = _NEGATIVE_ADJECTIVES.get(extract_orth_base(verb))
    if headword is None or _pos1(aux) != "助動詞" or extract_orth_base(aux) != "ない":
        return None
    return _Join(i, i + 1, headword, "形容詞")


@lru_cache(maxsize=1)
def _row_starts() -> tuple[frozenset[str], frozenset[str]]:
    """What a list row can start on: the first surface of every row, and the dictionary form of a one-token base row.

    A token that is neither starts no row, and ``_kana_join`` need not try any size on it.
    """
    listed = kana_words()
    return (
        frozenset(key[0] for key in listed.joins) | frozenset(key[0] for key in listed.base_joins if len(key) > 1),
        frozenset(key[0] for key in listed.base_joins if len(key) == 1),
    )


def _kana_join(
    tokens: list,
    i: int,
    grammar_points: bool,
    keys: list[str] | None = None,
    written_ok: Callable[[str], bool] | None = None,
    below: int | None = None,
    interjections: bool = False,
) -> _Join | None:
    """The longest kana word list join starting at ``tokens[i]``: by surfaces, or ending on a dictionary form.

    ``below`` asks for a join of fewer tokens than that: the next row down, when
    the longer one does not fit where it stands (``LexicalJoiner._kana_row``).

    A row of one token (だろう, ください) is no join but the same kind of word: a
    grammar expression the tagger files as an auxiliary, named by the list.
    Without ``grammar_points`` a grammar row is passed over as if unlisted, so a
    shorter row under it still joins (っていう in っていうか). An interjection
    row (あのね, しまった) is passed over the same way without ``interjections``.

    With ``keys`` (``_row_key`` of every token) and ``written_ok`` a row of two
    or more tokens also matches kanji-written tokens by their reading
    (``_read_join``); the join's headword is then the span as written. A join
    that is not pure kana is no list word (``listed``): the kana list overrides
    the run's ``allowed_pos`` because the script gate drops kana, and a
    kanji-written word passes that gate like any other.
    """
    listed = kana_words()
    read = keys is not None and written_ok is not None and keys[i] in listed.heads
    surfaces_first, bases_alone = _row_starts()
    if not read and tokens[i].surface not in surfaces_first and extract_orth_base(tokens[i]) not in bases_alone:
        return None
    for size in range(min(_MAX_KANA_TOKENS, len(tokens) - i, (below or _MAX_KANA_TOKENS + 1) - 1), 0, -1):
        span = tokens[i : i + size]
        surfaces = tuple(token.surface for token in span)
        row = listed.joins.get(surfaces) or listed.base_joins.get((*surfaces[:-1], extract_orth_base(span[-1])))
        headword = row.headword if row is not None else ""
        if row is None and read and size > 1 and keys is not None and written_ok is not None:
            row, headword = _read_join(span, surfaces, tuple(keys[i : i + size]), written_ok)
            # An interjection row is kana only: a kanji-written 何だ is turned away as an interjection
            # downstream, and the join would only take the card of 何.
            if row is not None and (row.interjection or _copula_after(surfaces[-1], tokens, i + size)):
                row = None
        if (
            row is not None
            and (grammar_points or not row.grammar)
            and (interjections or not row.interjection)
            and not _cut_from_negative(tokens, i + size - 1, row.line_start)
            and not _te_form_in_use(row.headword, tokens, i + size)
            and not _te_form_goes_on(row.headword, tokens, i + size)
            and not _embedded_question(row.headword, tokens, i + size)
            and not _verb_te_form(row.headword, tokens, i + size)
            and not _adnominal_only(row.headword, tokens, i + size)
            and not (row.grammar and _ka_of_kamo(surfaces[-1], tokens, i + size))
            and not _auxiliary_after_te(row.headword, tokens, i)
            and not _not_the_auxiliary(row.headword, span)
            and not _not_the_conjunction(row.headword, span)
            and not _other_tte(row.headword, span)
            and not _runs_on(row.headword, tokens, i + size)
            and not _misread_ja_nai(row.headword, tokens, i)
            and not _no_of_noda(row.headword, tokens, i + size)
            and not _inside_longer_entry(row.headword, tokens, i + size)
            and not _copula_de_negated(row.headword, tokens, i + size)
            and not _volitional_you(tokens, i)
            and not _with_what(row.headword, tokens, i + size)
            and not _bare_me(row.headword, tokens, i)
            and not _allowed_now(row.headword, tokens, i)
            and not _thing_wanted(row.headword, tokens, i)
            and not _good_too(row.headword, tokens, i + size)
            and not _thing_done(row.headword, tokens, i)
            and not _no_volitional_before(row.headword, tokens, i)
            and not _no_predicate_before(row.headword, tokens, i)
        ):
            return _Join(
                i,
                i + size - 1,
                headword,
                row.pos1,
                listed=is_kana_only("".join(surfaces)),
                line_start=row.line_start,
                after=row.after,
            )
    return None


def _row_key(token) -> str:
    """What a join row's token is matched against: the surface, or the kana reading of a token not written in kana."""
    surface = word_surface(token)
    return surface if is_kana_only(surface) else katakana_to_hiragana(extract_reading(token))


def _read_join(
    span: list, surfaces: tuple[str, ...], keys: tuple[str, ...], written_ok: Callable[[str], bool]
) -> tuple[KanaJoin | None, str]:
    """The row ``span`` matches through the readings of its kanji-written tokens, with the span as written.

    なん|と|なく is also 何|と|なく, かって|に also 勝手|に. A reading fits a
    homophone as well (時期|に reads じきに, この|変 reads このへん), so the match
    counts only when the dictionary lists the span as written (何となく is a
    term, 時期に is not); that spelling is the join's headword. A row ending on
    a dictionary form is written with it (方|が|よかっ is 方がいい), and only when
    its headword is its own tokens: か|も|=しれる names かもしれない, which the
    tokens of かも知れ do not spell.

    A verb written in kanji is never matched: there the line means the verb,
    where the kana spelling is the fixed word. 「…」と言う is 'says' and という
    'called', 任務に就いて 'on duty' and について 'about', 思わぬ 'do not think'
    and おもわぬ 'unexpected', 済まない 'not over yet' and すまない the apology
    (in the corpus replay every sampled 言う row was the verb).
    """
    if keys == surfaces:
        return None, ""
    if any(key != surface and _pos1(token) == "動詞" for token, surface, key in zip(span, surfaces, keys, strict=True)):
        return None, ""
    listed = kana_words()
    row = listed.joins.get(keys)
    written = "".join(word_surface(token) for token in span)
    if row is None:
        base = (*keys[:-1], extract_orth_base(span[-1]))
        row = listed.base_joins.get(base)
        if row is None or row.headword != "".join(base):
            return None, ""
        written = "".join(surfaces[:-1]) + base[-1]
    return (row, written) if written_ok(written) else (None, "")


def _cut_from_negative(tokens: list, end: int, clause_opener: bool) -> bool:
    """Whether a row's last token belongs to the negative after it, so the row is not the word here.

    いらっしゃい|ませ|ん: the greeting ends on the imperative ませ; before ん the
    same tokens are the honorific verb negated (先生はいらっしゃいませんか).
    それ|じゃ|ない, それで|は|なく: after a clause opener, じゃ / は before ない is the
    negative copula ('not that'), not the connective それじゃ / それでは.
    """
    if end + 1 >= len(tokens):
        return False
    last, after = tokens[end].surface, tokens[end + 1]
    if last == "ませ":
        return bool(after.surface == "ん")
    return clause_opener and last in ("は", "じゃ", "じゃあ") and extract_orth_base(after) == "ない"


def _copula_after(last: str, tokens: list, after: int) -> bool:
    """Whether the last で of a row matched by reading belongs to the copula after it (である, でございます).

    何であるか is 何 + である 'what it is', not なんで 'why'; 所であり is no ところで.
    Kana rows are left alone: なんであるの is 'why is it here' as often as not.
    """
    return last == "で" and after < len(tokens) and extract_orth_base(tokens[after]) in _COPULA_VERBS


def _embedded_question(headword: str, tokens: list, after: int) -> bool:
    """Whether どこ|か is どこ + the question か, not the word どこか 'somewhere'.

    Before a verb of knowing, asking or saying the か closes an embedded
    question: どこかわからない 'do not know where', どこか知らない？ 'do you know
    where?'. So it does where the line or sentence ends on it: 出口はどこか
    'where is the exit?', and before a sentence-final particle: どこかな,
    どこかね 'where, I wonder'. The row is passed over and どこ keeps its card.
    どちら|か and どっち|か are the same (出口がどちらか分からない 'do not know which way the exit is').
    """
    if headword not in _QUESTION_ROWS:
        return False
    if after >= len(tokens):
        return True
    following = tokens[after]
    if _pos1(following) in _CLAUSE_BREAK_POS1 or _pos2(following) == "終助詞":
        return True
    return extract_lemma(following) in _QUESTION_VERBS


def _ka_of_kamo(last: str, tokens: list, after: int) -> bool:
    """Whether the last か of a grammar row opens かも: じゃない|か|も|しれ|ない is じゃない + かもしれない.

    The row is passed over, so the shorter じゃない under it joins and か|も|しれ
    stays whole for its own row.
    """
    return last == "か" and after < len(tokens) and tokens[after].surface == "も"


def _adnominal_only(headword: str, tokens: list, after: int) -> bool:
    """Whether そう|いっ|た is 'said so' here, not the pre-noun そういった 'that kind of'.

    The pre-noun word stands before its noun (そういった話); anywhere else the
    same tokens are そう + 言った (彼がそういったんだ). ちょっと|し|た is the same:
    ちょっとした問題 'a minor problem' against ちょっとしたんだ 'did a little'.
    """
    return headword in _ADNOMINAL_ROWS and not (after < len(tokens) and _pos1(tokens[after]) in _NOUN_POS1)


def _copula_de_negated(headword: str, tokens: list, after: int) -> bool:
    """Whether the で of の|せい|で is the copula before は / も: 誰のせいでもない, 君のせいではない, 僕のせいでもある.

    The row is passed over: せい keeps its card and the negative its own.
    """
    if headword != "のせいで" or after + 1 >= len(tokens) or tokens[after].surface not in ("は", "も"):
        return False
    return extract_orth_base(tokens[after + 1]) in ("ない", "ある")


def _volitional_you(tokens: list, i: int) -> bool:
    """Whether a row opens on the よう of a volitional (考え|よう|に|も), which is the verb's own ending.

    The noun よう of ように / ような follows a verb's dictionary form (考えるように).
    Right after a 連用形 or 未然形 the tagger still files よう as that noun, but it
    is the ichidan volitional, and the verb's highlight already covers it. Not
    after いい: the tagger reads the adjective of いいように as 言い.
    """
    if tokens[i].surface != "よう" or i == 0 or _pos1(tokens[i - 1]) != "動詞" or tokens[i - 1].surface == "いい":
        return False
    c_form = getattr(getattr(tokens[i - 1], "feature", None), "cForm", None)
    return isinstance(c_form, str) and c_form.startswith(("連用形", "未然形"))


def _noun_place(candidate: _Join, tokens: list, spans: dict[int, tuple[int, int]]) -> bool:
    """Whether 役立た|ず stands where a noun does, so it is 役立たず 'good-for-nothing' and not the verb negated.

    After a pre-noun word (この役立たず), before a pause, or before a particle or
    the copula (役立たずが, 役立たずだ). 何も役立たず終わった goes on as a clause.
    """
    if candidate.start > 0 and _pos1(tokens[candidate.start - 1]) == "連体詞":
        return True
    return _stands_alone(tokens, spans, candidate.end) or tokens[candidate.end + 1].surface in _NOUN_FOLLOWERS


def _with_what(headword: str, tokens: list, after: int) -> bool:
    """Whether なん|と is 'with what' (何と戦う, 何と何), not the adverb なんと 'what; how …!'.

    The adverb stands before what it exclaims at (なんと美しい) or before a verb
    of saying (なんと言えばいい). The row is passed over before any other verb
    and before a pronoun. なん|て is passed over before a verb that takes a quote:
    there it is 何 + the quoting て (何て言った 'said what', 何て読むの 'how is it
    read'), and なんていうか 'how to put it' is one expression. The adverb stands
    before what it exclaims at (なんて寒い朝だ).
    """
    if headword not in ("なんと", "なんて") or after >= len(tokens):
        return False
    following = tokens[after]
    if headword == "なんて":
        return _pos1(following) == "動詞" and extract_lemma(following) in _QUOTING_VERBS
    if _pos1(following) == "代名詞":
        return True
    return _pos1(following) == "動詞" and extract_lemma(following) not in _SAYING_VERBS


def _allowed_now(headword: str, tokens: list, i: int) -> bool:
    """Whether よく|なる follows も, where it is 〜てもいい + なる and not よくなる 'get better'.

    どうでもよくなった 'stopped caring', 出なくてもよくなる 'no longer have to go out'.
    """
    return headword == "よくなる" and i > 0 and tokens[i - 1].surface.endswith("も")


def _bare_me(headword: str, tokens: list, i: int) -> bool:
    """Whether 目|に|ある has no modifier before 目, where it is 'in the eye' and not 目にあう 'go through'.

    The entry is said of an experience: ひどい目にあった, こんな目にあうなんて.
    """
    return headword == "目にあう" and not (i > 0 and _pos1(tokens[i - 1]) in ("形容詞", "連体詞"))


def _thing_wanted(headword: str, tokens: list, i: int) -> bool:
    """Whether こと|が|ある follows たい, where it is こと + ある and not ことがある 'there are times when'.

    The entry follows a verb's dictionary form or its past (転ぶことがある,
    灯台に登ったことがある). After たい / たかった the こと is the thing wanted
    (店長に伝えたいことがある 'there is something I want to tell the manager'): the row is
    passed over, and こと and ある keep their own cards.
    """
    if headword != "ことがある" or i == 0:
        return False
    before = tokens[i - 2] if i > 1 and tokens[i - 1].surface == "た" else tokens[i - 1]
    return _pos1(before) == "助動詞" and extract_lemma(before) == "たい"


def _good_too(headword: str, tokens: list, after: int) -> bool:
    """Whether よく|も is 良く + も before ない or 悪く (よくもない 'not good either', よくも悪くも).

    The adverb よくも reproaches what follows it (よくも皿を割ったな 'how dare you break the plate').
    """
    return headword == "よくも" and after < len(tokens) and extract_orth_base(tokens[after]) in ("ない", "悪い")


def _thing_done(headword: str, tokens: list, i: int) -> bool:
    """Whether もの|か follows a past, where もの is the thing done (誰が置いたものか 'who left this, I wonder').

    The rhetorical ものか follows what will not happen (二度と行くものか 'as if I would go again').
    """
    return headword == "ものか" and i > 0 and extract_lemma(tokens[i - 1]) == "た"


def _no_volitional_before(headword: str, tokens: list, i: int) -> bool:
    """Whether と|する follows no volitional, where it is not とする 'try to; be about to' (逃げようとした).

    After a noun it is 'make it; regard as' (目標とする) and after a plain form
    'supposing' (行くとすると): the row is passed over.
    """
    if headword != "とする":
        return False
    if i == 0:
        return True
    before = tokens[i - 1]
    c_form = getattr(getattr(before, "feature", None), "cForm", None)
    if isinstance(c_form, str) and c_form.startswith("意志推量形"):
        return False
    return not (_pos1(before) == "助動詞" and extract_lemma(before) in ("う", "よう"))


def _no_predicate_before(headword: str, tokens: list, i: int) -> bool:
    """Whether よう|だ follows no predicate, where it is not the grammar point ようだ 'it seems'.

    The grammar point follows a verb, an adjective, an auxiliary or the adnominal
    な (降らないようだ, 寒いようだ, 元気なようで). After の it is 'like' (夢のようだ),
    which stays out.
    """
    return headword in _SEEMING_ROWS and not (i > 0 and _pos1(tokens[i - 1]) in _PREDICATE_POS1)


def _no_of_noda(headword: str, tokens: list, after: int) -> bool:
    """Whether the の of どう|し|た|の opens のだ / のです: どうしたのだ is どうした + のだ, not the question どうしたの.

    The row is passed over, so the shorter どう|し|た joins and の|だ stays whole for its own row.
    なん|な|の before a copula is the same: なんなのだ is 何 + なのだ, and なん keeps its own card.
    """
    if headword not in ("どうしたの", "なんなの") or after >= len(tokens):
        return False
    following = tokens[after]
    return _pos1(following) == "助動詞" and extract_lemma(following) in ("だ", "です")


def _inside_longer_entry(headword: str, tokens: list, after: int) -> bool:
    """Whether どう|し|た opens the entry どうしたものか 'what should I do', not the question どうした.

    The row is passed over before もの / もん + か, and どう keeps its own card.
    """
    if headword != "どうした" or after + 1 >= len(tokens):
        return False
    return tokens[after].surface in ("もの", "もん") and tokens[after + 1].surface == "か"


def _verb_te_form(headword: str, tokens: list, after: int) -> bool:
    """Whether に|とっ|て is に + the て-form of とる (取って, 撮って), not にとって 'for; to'.

    A verb right after it takes that て: 写真にとって送った 'took a photo and sent
    it', ここにとっておいた 'kept it here'. The expression is followed by what it
    qualifies (彼にとって大事だ) or by は / も. に|つれ|て before a verb of coming
    or going is 連れて (家につれてくる), not につれて 'as; in proportion to'.
    """
    if after >= len(tokens) or _pos1(tokens[after]) != "動詞":
        return False
    return headword == "にとって" or (headword == "につれて" and extract_orth_base(tokens[after]) in _MOTION_VERBS)


def _te_form_in_use(headword: str, tokens: list, after: int) -> bool:
    """Whether どう|し|て is the て-form of する here, not the adverb どうして 'why'.

    A past, a polite ending or a て-auxiliary right after it inflects the verb:
    どうしてた 'how were you doing', どうしていいか 'what to do', どうしてくれる
    'what will you do about it'. The row is passed over and どう keeps its card.
    やる is left out: どうしてやらないの is 'why' as often as not. こう|し|て is the
    same, and there やる and the requests count too: こうしている 'doing this',
    こうしてください 'do it like this', against こうして出会った 'this is how we met'.
    """
    if after >= len(tokens):
        return False
    follower = extract_orth_base(tokens[after])
    if headword == "そうしたら":  # そうしたらいい 'you should do so', not the conjunction 'and then'
        return follower in ("いい", "よい")
    if headword not in _TE_ADVERBS:
        return False
    return follower in _TE_FOLLOWERS or (headword != "どうして" and follower in _TE_REQUESTS)


def _te_form_goes_on(headword: str, tokens: list, after: int) -> bool:
    """Whether よっ|て is the て-form of 寄る or 酔う here, not the conjunction よって 'therefore'.

    An auxiliary, a て-auxiliary, a verb of coming or going or ない right after it
    goes on from that て: よってくる 'come over', よってたかって, よってない 'not drunk'.
    Any other verb is the clause the conjunction opens (よって買えない).
    """
    if headword != "よって" or after >= len(tokens):
        return False
    follower = extract_orth_base(tokens[after])
    return _pos1(tokens[after]) == "助動詞" or follower == "ない" or follower in _TE_FOLLOWERS | _MOTION_VERBS


def _auxiliary_after_te(headword: str, tokens: list, start: int) -> bool:
    """Whether お|いで follows a verb's て, where it is an auxiliary and not the word おいで 'come'.

    見ておいで 'go and look', 起きておいでですか 'are you up': after て it stands in for
    きなさい / いらっしゃる, as くる and いる do there, and the verb keeps the line's card.
    The grammar row みたい is the same: after て it is みる + たい, which the tagger
    now and then files as the auxiliary stem (なってみたい 'want to try being').
    """
    if headword not in ("おいで", "みたい") or start == 0:
        return False
    before = tokens[start - 1]
    return _pos2(before) == "接続助詞" and before.surface in ("て", "で")


def _not_the_auxiliary(headword: str, span: list) -> bool:
    """Whether a one-token row of ``_AUXILIARY_ROWS`` met another token with its kana.

    雨らしい is the auxiliary 'it seems'; 春らしい is the suffix '-like', which the
    compound matcher joins where the dictionary lists the word (男らしい). 知らんやろ
    is the Kansai だろう; the verb やろ is やる's clipped volitional.
    """
    return headword in _AUXILIARY_ROWS and _pos1(span[0]) != "助動詞"


def _not_the_conjunction(headword: str, span: list) -> bool:
    """Whether a one-token row of ``_CONJUNCTION_ROWS`` met the particle with its kana.

    けど、もう遅い opens on the conjunction; in 行くけど and after a subtitle space
    (行きたい けど) the tagger says particle, and the row is passed over.
    """
    return headword in _CONJUNCTION_ROWS and _pos1(span[0]) != "接続詞"


def _other_tte(headword: str, span: list) -> bool:
    """Whether the tagger's っつう is written って or ってえ, where the card would not show the word.

    行くっつってた and そういう話っつうか spell it; ぜってえ and 痛ってえ are 絶対 and
    痛い drawn out, and a plain って is the particle a learner already has. A bare
    っつ is as often the middle of another word (ひとっつも, a nickname).
    """
    surface = span[0].surface
    return headword == "っつう" and (not surface.startswith("っつ") or surface == "っつ")


def _runs_on(headword: str, tokens: list, after: int) -> bool:
    """Whether a word follows じゃ|ね, where it is じゃ + the particle ね and not じゃね 'isn't it'.

    学生じゃね？ and 終了じゃね stop there. そんなことじゃね 無理だ goes on after a
    mere space ('with that, you know, …'), as does じゃねとか.
    """
    return headword in _STOP_AFTER and after < len(tokens) and _pos1(tokens[after]) not in _CLAUSE_BREAK_POS1


def _misread_ja_nai(headword: str, tokens: list, start: int) -> bool:
    """Whether や|ない is the tail of a じゃない written with a large や (もう夏じやない), not the Kansai やない."""
    return headword == "やない" and start > 0 and tokens[start - 1].surface.endswith(("じ", "ゃ", "や"))


def _joined_pos1(previous: _Join | None) -> str | None:
    """The POS a row sees in the join right before it: a joined predicate still ends on an auxiliary.

    らしい|のです and どうした|のだ: the "verb" row のです follows the joined らしい as it followed the token.
    """
    if previous is None:
        return None
    return "助動詞" if previous.headword in _PREDICATE_ROWS else previous.pos1


def _hearsay(candidate: _Join, tokens: list) -> bool:
    """Whether そう|だ / そう|です is the hearsay そうだ after a plain form (雨が降るそうだ 'they say it will rain').

    The tagger files that そう apart (そう-伝聞) from the reply's adverb and from
    the そう of 泣きそうだ. The same entry lists the reply and the hearsay; this
    one is a grammar point, joined only while Mine grammar points is on.
    """
    return (
        candidate.headword in _HEARSAY_ROWS
        and candidate.start > 0
        and extract_lemma(tokens[candidate.start]) == "そう-伝聞"
        and not _stem_before_sou(tokens, candidate.start)
    )


def _stem_before_sou(tokens: list, i: int) -> bool:
    """Whether the そう at ``tokens[i]`` follows a stem, where it is 'looks like' and not the hearsay.

    The hearsay follows a plain form (降るそうだ, 降ったそうだ, 会いたいそうだ).
    After the stem of たい or of an adjective it is the そう of appearance
    (帰りたそうだ 'looks like he wants to go home'). The tagger files that た as a
    past now and then: after a godan 連用形 that has a sound change it is no past
    (聞きた; the past is 聞いた), so it is the stem of たい.
    """
    before = tokens[i - 1]
    c_form = getattr(getattr(before, "feature", None), "cForm", None)
    if isinstance(c_form, str) and c_form.startswith("語幹"):
        return True
    if before.surface != "た" or i < 2 or _pos1(tokens[i - 2]) != "動詞":
        return False
    verb = getattr(tokens[i - 2], "feature", None)
    c_type = getattr(verb, "cType", None)
    return (
        isinstance(c_type, str)
        and c_type.startswith("五段")
        and not c_type.startswith("五段-サ行")
        and getattr(verb, "cForm", None) == "連用形-一般"
    )


def _opens_before_a_noun(candidate: _Join, tokens: list, spans: dict[int, tuple[int, int]]) -> bool:
    """Whether と|いう, after no noun, stands right before the noun it names.

    優勝したという知らせ, 彼などという人, and a line that a subtitle breaks there
    (…/という話だ): the same という 'called; that says', not the verb 言う.
    Before anything else the verb keeps the card (来ないという 'they say he will
    not come'), and so it does inside a line before a noun of people: 行くなという人
    is 'people who say do not go'. An entry the matcher joined is no noun
    token, but one that opens on a formal noun is named the same way
    (できないという|わけではない).
    """
    after = candidate.end + 1
    if candidate.headword not in _NAMING_ROWS or after >= len(tokens):
        return False
    if _pos1(tokens[after]) not in _NOUN_POS1 and not (
        getattr(tokens[after], "compound", False) is True and tokens[after].surface.startswith(_JOINED_NOUN_HEADS)
    ):
        return False
    return _opens_clause(tokens, spans, candidate.start) or word_surface(tokens[after]) not in _PEOPLE


def _opens_clause(tokens: list, spans: dict[int, tuple[int, int]], i: int) -> bool:
    """Whether ``tokens[i]`` opens its line or clause: first, after punctuation, or after a space."""
    if i == 0:
        return True
    if _pos1(tokens[i - 1]) in _CLAUSE_BREAK_POS1:
        return True
    left, right = spans.get(i - 1), spans.get(i)
    return left is None or right is None or left[1] != right[0]


def _stands_alone(tokens: list, spans: dict[int, tuple[int, int]], end: int) -> bool:
    """Whether a pause follows ``tokens[end]``: the line end, punctuation, or a space.

    With the clause opening before it, this is where an "alone" row is a reply
    of its own (そうか。 そうですか、… いいよ！) and not the start of something
    longer (そうかもね, そうだよ, いいよね).
    """
    if end + 1 >= len(tokens) or _pos1(tokens[end + 1]) in _CLAUSE_BREAK_POS1:
        return True
    left, right = spans.get(end), spans.get(end + 1)
    return left is None or right is None or left[1] != right[0]


def _permits_after_te(candidate: _Join, tokens: list) -> bool:
    """Whether an "alone" row that opens on いい stands after a verb's て-form: 休んで いいよ is 〜ていい 'you may'.

    The space is a subtitle break inside the frame, so いい is the predicate
    and its particle no reply of its own: after て / で, and after ても / でも.
    """
    i = candidate.start
    if tokens[i].surface != "いい" or i == 0:
        return False
    te = i - 2 if tokens[i - 1].surface == "も" and i >= 2 else i - 1
    return tokens[te].surface in ("て", "で") and _pos1(tokens[te]) == "助詞"


def _quotes_what_precedes(headword: str, tokens: list, i: int) -> bool:
    """Whether って|こと|は after a mere space still quotes the words before it, so it opens nothing.

    …どれだけ大事か ってことは is 'the fact of how important …': the space is a
    subtitle break inside one sentence. The conjunction ってことは 'which means'
    heads its line or follows punctuation. いい|から is the same: after a space
    it is 'because it is good' (天気が いいから), and いいから 'never mind, just …'
    heads its line, follows punctuation or is said twice (いいから いいから こっちに来て).
    なら, な|の|に and で|は after a space are the copula and the particles
    (学生 なら, 東京 では); the conjunctions head a line or follow punctuation.
    """
    return headword in _AFTER_PUNCTUATION and i > 0 and _pos1(tokens[i - 1]) not in _CLAUSE_BREAK_POS1


def _needs_a_comma(candidate: _Join, tokens: list) -> bool:
    """Whether a pronoun + particle row has only a space after it where a space does not make it the conjunction.

    そこで、… and それも、… are the conjunctions; そこで 着替えてて 'get changed there' and
    それも いい 'that is fine too' are the pronoun and its particle, as a subtitle
    space also falls inside a clause. それに after a space is 'besides' unless a
    verb comes right after it (それに 気づいた 'noticed that'). None of them opens
    a clause right after は, が or を (私は それに 触れた), and そこで？ or そこで」
    ends its sentence on 'there'.
    """
    if candidate.headword not in _PRONOUN_CONJUNCTIONS:
        return False
    before = tokens[candidate.start - 1] if candidate.start else None
    if before is not None and _pos1(before) == "助詞" and before.surface in ("は", "が", "を"):
        return True
    after = candidate.end + 1
    if after >= len(tokens):
        return candidate.headword != "それに"
    following = tokens[after]
    if _pos1(following) in _CLAUSE_BREAK_POS1:
        return candidate.headword != "それに" and _pos2(following) in ("句点", "括弧閉")
    return candidate.headword != "それに" or _pos1(following) == "動詞"


def _wonders_what(candidate: _Join, tokens: list, spans: dict[int, tuple[int, int]]) -> bool:
    """Whether 何|かしら ends its clause, where it is 何 + the particle かしら ('I wonder what').

    何かしら 'something or other' goes on into what it qualifies (何かしら裏がある).
    """
    if candidate.headword not in _SOMETHING_ROWS:
        return False
    return _stands_alone(tokens, spans, candidate.end) or _pos2(tokens[candidate.end + 1]) == "終助詞"


def _follows(tokens: list, i: int, after: str, joined_pos1: str | None = None) -> bool:
    """Whether ``tokens[i]`` comes right after the kind of word a kana row asks for ("" asks for none).

    A noun row also follows a closing quote (「本」という): what is quoted is named.
    ``joined_pos1`` is the POS of a join accepted in the same pass that ends on
    ``tokens[i - 1]``: the joined word is what precedes (こう|いう is the 連体詞
    こういう, not the verb いう; よう|に|なる is a verb). A verb row refuses the
    auxiliary だ: the adnominal な of a na-adjective (嫌なこと) ends no verb. It is
    told by lemma: the voiced past だ (読んだ) shares orthBase だ but its lemma is た.
    A "clause" row (んだ, んです) follows whatever can end a clause (``_ends_clause``).
    A "copula" row (じゃない, ではない) refuses a verb: there the で or じゃ is the
    verb's te-form (読んではない, 読んじゃない), which its highlight already covers.
    A "conditional" row (ならない, いけない) follows a conditional (``_after_conditional``).
    """
    if not after:
        return True
    if after == "clause":
        return i == 0 or joined_pos1 is not None or _ends_clause(tokens[i - 1])
    if after == "copula":
        return i == 0 or (joined_pos1 or _pos1(tokens[i - 1])) != "動詞"
    if after == "conditional":
        return _after_conditional(tokens, i)
    if i == 0:
        return False
    before = tokens[i - 1]
    pos1 = joined_pos1 or _pos1(before)
    if after == "noun":
        return pos1 in _NOUN_POS1 or (joined_pos1 is None and _pos2(before) == "括弧閉")
    if joined_pos1 is None and pos1 == "助動詞" and extract_lemma(before) == "だ":
        return False
    return pos1 in _VERB_POS1


def _after_conditional(tokens: list, i: int) -> bool:
    """Whether ``tokens[i]`` comes right after a conditional, where ならない / いけない is the obligation unit.

    行かなければ|ならない, 見ては|いけない, 行かないと|いけない, 食べちゃ|いかん: the unit
    is what follows the conditional, and the verb chain before it keeps its own
    card (a chain wins over an expression that starts inside it, so
    なければならない is never one word). Anywhere else the same tokens are the
    plain verb (医者にならない, 今日はいけない). と counts only after a negative
    (ないと): 見るといけない is as often 'in case someone sees'.
    """
    if i == 0:
        return False
    before = tokens[i - 1]
    pos1 = _pos1(before)
    if pos1 == "助動詞":
        return before.surface in _CONDITIONAL_AUXILIARIES
    if pos1 != "助詞":
        return False
    earlier = tokens[i - 2] if i > 1 else None
    if _pos2(before) == "接続助詞":
        if before.surface == "と":
            return earlier is not None and extract_orth_base(earlier) == "ない"
        return before.surface in _CONDITIONAL_PARTICLES
    # ては / では: the topic は right after a verb's て.
    return (
        before.surface == "は"
        and earlier is not None
        and _pos2(earlier) == "接続助詞"
        and earlier.surface in ("て", "で")
    )


def _ends_clause(before) -> bool:
    """Whether the explanatory ん of a "clause" row (んだ, んです) can follow ``before``.

    Not the adnominal な (好きなんだ: the benchmark cards no んだ there), and not
    a word the ん itself inflects: after て or a 連用形 the ん is the verb's own
    contraction (やってんだ is やっている + のだ: the tagger marks that て
    連体形-省略; くれんだ is くれる + のだ), which the verb's highlight already
    covers, so a card there would overlap it.
    """
    pos1 = _pos1(before)
    if pos1 == "助詞":
        return not (_pos2(before) == "接続助詞" and before.surface in ("て", "で"))
    if pos1 not in ("動詞", "助動詞", "形容詞"):
        return True
    c_form = getattr(getattr(before, "feature", None), "cForm", None)
    if before.surface == "な" or not isinstance(c_form, str) or c_form.endswith("省略"):
        return False
    return c_form.startswith(("終止形", "連体形"))


def _contiguous(spans: dict[int, tuple[int, int]], start: int, end: int) -> bool:
    """Whether tokens ``start..end`` sit side by side in the source (no space between)."""
    for k in range(start, end):
        left, right = spans.get(k), spans.get(k + 1)
        if left is None or right is None or left[1] != right[0]:
            return False
    return True


class LexicalJoiner:
    """Join the table's grammar-cut words back into one token per line.

    ``allowed_pos`` is the run's inclusion rule's: a join whose POS the user
    turned off is skipped, so the split words keep their cards.
    ``grammar_points`` is the user's Mine grammar points: off, the list's
    grammar rows (んだ, ください) are not joined.
    """

    def __init__(
        self,
        attest_lookup: AttestLookup,
        allowed_pos: frozenset[str],
        *,
        grammar_points: bool,
        interjections: bool = False,
    ) -> None:
        self._lookup = attest_lookup
        self._allowed_pos = allowed_pos
        self._grammar_points = grammar_points
        # The parser's interjection switch: on, the list's interjection rows are joined. Read at
        # every line, so it can be set after construction.
        self._interjections = interjections
        self._attested: dict[str, bool] = {}

    def join_line(self, text: str, tokens: list) -> list:
        """Return ``tokens`` with table words joined.

        Never mutates ``tokens`` or its elements (the parser's per-file line
        cache shares them) and returns the input object when nothing joins.
        One batched lookup per line covers every headword not seen before.
        """
        spans: dict[int, tuple[int, int]] = {}
        kept: list[_Join] = []

        def fits(candidate: _Join) -> bool:
            if not (candidate.listed or candidate.pos1 in self._allowed_pos):
                return False
            if candidate.headword in _ALLOWED_POS_ROWS and candidate.pos1 not in self._allowed_pos:
                return False
            if not spans:
                spans.update(CompoundDictionaryMatcher._source_spans(text, tokens))
            if not _contiguous(spans, candidate.start, candidate.end):
                return False
            if self._grammar_points and _hearsay(candidate, tokens):
                kept.append(candidate)
                return True
            if candidate.line_start and not _opens_clause(tokens, spans, candidate.start):
                return False
            repeated = bool(kept) and kept[-1].end == candidate.start - 1 and kept[-1].headword == candidate.headword
            if _quotes_what_precedes(candidate.headword, tokens, candidate.start) and not repeated:
                return False
            if candidate.headword in _NEGATIVE_NOUNS and not _noun_place(candidate, tokens, spans):
                return False
            if _wonders_what(candidate, tokens, spans) or _needs_a_comma(candidate, tokens):
                return False
            previous = kept[-1] if kept and kept[-1].end == candidate.start - 1 else None
            if candidate.after == "alone":
                if not _stands_alone(tokens, spans, candidate.end) or _permits_after_te(candidate, tokens):
                    return False
            elif not _follows(
                tokens, candidate.start, candidate.after, _joined_pos1(previous)
            ) and not _opens_before_a_noun(candidate, tokens, spans):
                return False
            kept.append(candidate)
            return True

        joins = self._find(tokens, fits)
        if not joins:
            return tokens
        unknown = sorted({join.headword for join in joins} - self._attested.keys())
        if unknown:
            hits = self._lookup(unknown)
            for headword in unknown:
                self._attested[headword] = bool(hits.get(headword))
        by_start = {join.start: join for join in joins if self._attested[join.headword]}
        if not by_start:
            return tokens
        out: list = []
        i = 0
        while i < len(tokens):
            join = by_start.get(i)
            if join is None:
                out.append(tokens[i])
                i += 1
                continue
            span = tokens[join.start : join.end + 1]
            out.append(
                CompoundSyntheticToken(
                    surface="".join(token.surface for token in span),
                    pos1=join.pos1,
                    pos2="一般",
                    lemma=join.headword,
                    kana="".join(extract_reading(token) for token in span),
                )
            )
            i = join.end + 1
        return out

    def _written(self, span: str) -> bool:
        """Whether the dictionary lists ``span`` as written: the gate of a row matched by reading (``_read_join``)."""
        hit = self._attested.get(span)
        if hit is None:
            hit = self._attested[span] = bool(self._lookup([span]).get(span))
        return hit

    def _find(self, tokens: list, fits: Callable[[_Join], bool] = lambda join: True) -> list[_Join]:
        """Greedy left-to-right table matches, the longest fixed entry first.

        A match that does not fit where it stands (``fits``) takes no tokens:
        の|だ after a space is no のだ, and the だ|から behind it still joins.
        """
        found: list[_Join] = []
        i = 0
        last = len(tokens) - 1
        keys = [_row_key(token) for token in tokens]
        while i <= last:
            join = (i < last and (_fixed_join(tokens, i) or _negative_adjective(tokens, i))) or None
            if join is None:
                join = self._kana_row(tokens, i, keys, fits)
            elif not fits(join):
                join = None
            if join is None:
                i += 1
            else:
                found.append(join)
                i = join.end + 1
        return found

    def _kana_row(self, tokens: list, i: int, keys: list[str], fits: Callable[[_Join], bool]) -> _Join | None:
        """The longest kana row at ``tokens[i]`` that fits where it stands.

        A longer row that does not fit gives way to the next one down: どう|し|た|ん
        is the reply only before a pause, so before だ the row どう|し|た joins and
        ん|だ stays for its own row (どうした + んだ). So does a row the run's
        dictionaries lack: without おめでとうございます the row お|めでとう still joins.
        """
        below: int | None = None
        while True:
            join = _kana_join(
                tokens, i, self._grammar_points, keys, self._written, below, interjections=self._interjections
            )
            if join is None or (self._written(join.headword) and fits(join)):
                return join
            below = join.end - i + 1
