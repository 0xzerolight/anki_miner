"""Generate the kana word list (anki_miner/resources/kana_words/ja.tsv) from JMdict_e.

Source:
  JMdict_e — Japanese-English Dictionary, maintained by EDRDG.
    Download: http://ftp.edrdg.org/pub/Nihongo/JMdict_e.gz
    License: Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)

A word is listed when JMdict gives its kana spelling a common priority tag and
unidic-lite tags it, inside a sentence, as one token of a listed class. The
tag is the one a carrier sentence gives it, since a word can tag differently
alone. A word JMdict writes in kanji (わたし for 私, じっと for 凝っと) also
needs a JMdict part of speech of that class, as its kana spelling alone tags as
anything (くすり as an adverb), and three morae, four for a noun, unless it is
named below: a short kana spelling is as often the tail of a longer kana word.
Its row is marked ``kanji``. The joins below are hand-picked; each must be a
JMdict spelling, or the build fails.

Usage:
    python scripts/build_kana_words.py --jmdict /tmp/JMdict_e

lxml is required (dev-only, not a runtime dependency).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from build_name_wordsets import COMMON_PRIORITY_TAGS, _lxml_parser
from lxml import etree

from anki_miner.services.tagger import get_shared_tagger

_USUALLY_KANA = "word usually written using kana alone"

#: The unidic pos1 tags whose kana words are listed. 動詞/形容詞/形状詞 stay out:
#: kana recovery already brings those back by attestation. A listed 感動詞 (はい,
#: ありがとう) overrides the parser's interjection skip.
WORD_POS1: tuple[str, ...] = ("副詞", "代名詞", "連体詞", "接続詞", "名詞", "感動詞")

#: The only two-kana nouns listed. A two-kana noun is as often a piece of a
#: longer word the tagger split (まね|た → ねた, や|つら, すり|ガラス) or a
#: particle or suffix (さえ, ほど, たち), so the short ones are named instead.
SHORT_NOUNS: frozenset[str] = frozenset(
    {"こと", "もの", "ため", "うち", "はず", "せい", "やつ", "まま", "ほか", "よそ", "そば", "だめ", "みな"}
    | {"ひと", "いぬ", "ねこ", "ごみ", "くそ"}
    | {"うそ", "ばか", "ただ", "ふう", "うえ", "いま", "けが", "むちゃ"}
    | {"くせ", "ほう", "まね", "ふり", "たび", "あと", "わけ", "とこ", "ころ"}
    | {"もん"}
)

#: The only two-kana spellings of kanji-written words listed outside the nouns.
SHORT_READINGS: frozenset[str] = frozenset({"きみ", "ぼく", "おれ", "かれ", "だれ", "ぜひ"})

#: The only kanji-written nouns of three morae listed by their kana spelling: those
#: commonly written in kana. A three-mora kana noun is otherwise as often the tail
#: of a longer kana word (いい|かげん, つ|くろう) or a given name (しずく).
SHORT_SPELLED_NOUNS: frozenset[str] = frozenset(
    {"とおり", "おなか", "ゆうべ", "かぎり", "におい", "しょうゆ", "けんか", "うわさ", "まぬけ", "あげく"}
    | {"おかげ", "ほんと", "ほんま", "ごはん", "あした", "しかた", "ふだん", "ことし"}
    | {"ようす", "しっぽ", "きげん", "ぜんぶ"}
)

#: Spoken kana spellings JMdict gives no priority tag (やっぱ for 矢っ張り, the
#: colloquial てめえ), listed as if it did: each is as common in dialogue as its
#: tagged form.
UNTAGGED_COMMON: frozenset[str] = frozenset(
    {"やっぱ", "とっても", "あくまで", "とっとと", "めっちゃ", "ちょい", "そこそこ", "またまた", "まんまと", "ばっちり"}
    | {"おかげ", "こないだ", "ほんと", "ほんま", "とことん", "ごっこ", "こだわり", "てめえ", "おめえ", "わたくし"}
    | {"ったく", "うむ", "おおきに", "てめぇ", "とやかく", "まんま"}
)

#: Tags a line gives a word that no carrier sentence does: あんな is 形状詞 in every
#: carrier but 連体詞 before a noun (あんなこと), as そんな and こんな are.
EXTRA_TAGS: dict[str, frozenset[str]] = {"あんな": frozenset({"連体詞"}), "めちゃくちゃ": frozenset({"名詞"})}

#: The JMdict part-of-speech text a kanji-written word needs for each tag.
JMDICT_POS: dict[str, tuple[str, ...]] = {
    "代名詞": ("pronoun",),
    "副詞": ("adverb",),
    "連体詞": ("pre-noun adjectival",),
    "接続詞": ("conjunction",),
    "名詞": ("noun (common)",),
    "感動詞": ("interjection",),
}

#: Sentences a candidate is tagged in; any single-token tag it gets counts.
CARRIERS: tuple[str, ...] = ("{}、それは違う。", "彼は{}来た。", "{}がある。", "それは{}だ。", "「{}」と言った。")

#: Rows left out, by (form, pos1) and reason.
EXCLUDED: dict[tuple[str, str], str] = {
    # Conjunction readings of particles: in a line the tagger gives these the
    # 接続詞 tag only where they open it, and the card would teach the particle.
    **dict.fromkeys((("から", "接続詞"), ("が", "接続詞"), ("けど", "接続詞"), ("けれど", "接続詞")), "particle"),
    **dict.fromkeys((("たら", "接続詞"), ("して", "接続詞"), ("じゃ", "接続詞")), "particle"),
    **dict.fromkeys((("つつ", "副詞"), ("たって", "副詞"), ("かと", "副詞")), "particle"),
    # Particles a carrier tags 名詞: the noun reading (鴨, 仮名) is rare in a line,
    # the particle constant.
    **dict.fromkeys((("かも", "名詞"), ("かな", "名詞"), ("とも", "名詞"), ("かね", "名詞")), "particle"),
    **dict.fromkeys((("なし", "名詞"), ("たか", "名詞"), ("しな", "名詞")), "particle"),
    # Requests after て (見てごらん, 待ってちょうだい).
    **dict.fromkeys((("ごらん", "名詞"), ("ちょうだい", "名詞")), "auxiliary"),
    # 嫌々 'reluctantly' in a line is mostly the interjection いやいや 'no, no'.
    ("いやいや", "名詞"): "interjection",
    # Suffixes, grammar nouns and nouns a carrier tags 副詞.
    **dict.fromkeys((("ころ", "副詞"), ("ごろ", "副詞"), ("ごと", "副詞")), "suffix"),
    ("よう", "副詞"): "grammar",
    **dict.fromkeys((("まま", "副詞"), ("ついで", "副詞"), ("ばか", "副詞"), ("がん", "副詞")), "noun"),
    **dict.fromkeys((("ばら", "副詞"), ("じゃり", "副詞")), "noun"),
    # Verb stems and interjections a carrier tags 副詞.
    **dict.fromkeys((("どく", "副詞"), ("むく", "副詞"), ("ぶつ", "副詞")), "verb"),
    **dict.fromkeys((("いや", "副詞"), ("よし", "副詞")), "interjection"),
    # Archaic, or not a word to learn.
    **dict.fromkeys((("たれ", "代名詞"), ("さる", "連体詞"), ("かく", "副詞"), ("ゆえ", "名詞")), "archaic"),
    **dict.fromkeys((("ちんちん", "副詞"), ("ちんちん", "名詞")), "vulgar"),
    ("きちがい", "名詞"): "slur",
    # 単に is as often the tail of a dialect past (待っちょったんに).
    ("たんに", "副詞"): "fragment",
    ("にゃー", "副詞"): "animal sound",
    # ええ is as often the Kansai いい (これでええ) as 'yes'; あら is mostly the
    # drawn-out あらぁ of a surprised line.
    **dict.fromkeys((("ええ", "感動詞"), ("あら", "感動詞")), "interjection"),
    # Fillers, hesitations and gasps: a sound, not a word to learn.
    **dict.fromkeys((("あの", "感動詞"), ("あのー", "感動詞"), ("その", "感動詞"), ("えっと", "感動詞")), "filler"),
    **dict.fromkeys((("えと", "感動詞"), ("うーん", "感動詞"), ("まー", "感動詞"), ("なー", "感動詞")), "filler"),
    **dict.fromkeys((("あっ", "感動詞"), ("あん", "感動詞"), ("あう", "感動詞"), ("あい", "感動詞")), "filler"),
    **dict.fromkeys((("ふん", "感動詞"), ("おす", "感動詞")), "filler"),
}

#: Words the tagger cuts apart: (headword, pos1, tokens, where). Kana
#: spans join in LexicalJoiner rather than the compound matcher, which attests
#: a span by its exact term and so misses words JMdict spells in kana only as a
#: reading (どういう, そのうち). The pos1 is the joined token's, so it decides the
#: card front like any token's. ``where`` is "" (anywhere), "start" (opening a line or
#: clause: a conjunction there, で|も inside one is the particles, 学校でも), "noun"
#: (after a noun) or "verb" (after a verb or an auxiliary). Left out: それ|に,
#: as a subtitle space before それに伴う reads as a clause break, and その|うち,
#: which is "of those" before の (そのうちの１本).
JOINS: tuple[tuple[str, str, str, str], ...] = (
    ("そのまま", "副詞", "その|まま", ""),
    ("このまま", "副詞", "この|まま", ""),
    ("そのうえ", "接続詞", "その|うえ", ""),
    ("このあいだ", "名詞", "この|あいだ", ""),
    ("どのくらい", "副詞", "どの|くらい", ""),
    ("どのぐらい", "副詞", "どの|ぐらい", ""),
    ("そういう", "連体詞", "そう|いう", ""),
    ("こういう", "連体詞", "こう|いう", ""),
    ("どういう", "連体詞", "どう|いう", ""),
    ("ああいう", "連体詞", "ああ|いう", ""),
    ("もしかして", "副詞", "もし|か|し|て", ""),
    ("もしかしたら", "副詞", "もし|か|し|たら", ""),
    ("もしかすると", "副詞", "もし|か|する|と", ""),
    ("どうも", "副詞", "どう|も", ""),
    ("ために", "名詞", "ため|に", ""),
    ("やけに", "副詞", "やけ|に", ""),
    ("かどうか", "副詞", "か|どう|か", ""),
    ("いつも", "副詞", "いつ|も", ""),
    ("いつか", "副詞", "いつ|か", ""),
    ("いつでも", "副詞", "いつ|で|も", ""),
    ("いつまでも", "副詞", "いつ|まで|も", ""),
    ("これから", "副詞", "これ|から", ""),
    ("どうして", "副詞", "どう|し|て", ""),
    ("どうしても", "副詞", "どう|し|て|も", ""),
    ("どうやって", "副詞", "どう|やっ|て", ""),
    ("そんなに", "副詞", "そんな|に", ""),
    ("こんなに", "副詞", "こんな|に", ""),
    ("どんなに", "副詞", "どんな|に", ""),
    # A pronoun's plural is its own JMdict word; alone, おれ would be cut from おれたち.
    ("わたしたち", "代名詞", "わたし|たち", ""),
    ("ぼくたち", "代名詞", "ぼく|たち", ""),
    ("おれたち", "代名詞", "おれ|たち", ""),
    ("おまえたち", "代名詞", "おまえ|たち", ""),
    ("きみたち", "代名詞", "きみ|たち", ""),
    ("あなたたち", "代名詞", "あなた|たち", ""),
    ("かれら", "代名詞", "かれ|ら", ""),
    ("おまえら", "代名詞", "おまえ|ら", ""),
    ("あいつら", "代名詞", "あいつ|ら", ""),
    ("こいつら", "代名詞", "こいつ|ら", ""),
    ("これら", "代名詞", "これ|ら", ""),
    ("それら", "代名詞", "それ|ら", ""),
    # Kinship words in kana: the tagger cuts お + ばあ + ちゃん, and JMdict has the
    # whole word only as the reading of a kanji spelling (お祖母ちゃん). The bare
    # root (ばあ, とう) is no row: alone it is as often another word (とうに).
    *(
        (prefix + root + suffix, "名詞", "|".join(filter(None, (prefix, root, suffix))), "")
        for root in ("ばあ", "じい", "かあ", "とう", "にい", "ねえ")
        for prefix in ("お", "")
        for suffix in ("ちゃん", "さん")
    ),
    *((root + suffix, "名詞", f"{root}|{suffix}", "") for root in ("おじ", "おば") for suffix in ("ちゃん", "さん")),
    # Apologies the tagger reads as the verb 済む and its negative.
    ("すみません", "感動詞", "すみ|ませ|ん", ""),
    ("すいません", "感動詞", "すい|ませ|ん", ""),
    ("いいかげん", "形状詞", "いい|かげん", ""),
    ("なぜか", "副詞", "なぜ|か", ""),
    ("すぐに", "副詞", "すぐ|に", ""),
    # Cut at a particle, an auxiliary or the adjective ない; the stem alone is no
    # word (とっく, もったい) or another one (あんな, いまだ).
    ("あんなに", "副詞", "あんな|に", ""),
    ("ちなみに", "接続詞", "ちなみ|に", ""),
    ("とっくに", "副詞", "とっく|に", ""),
    ("いまだに", "副詞", "いまだ|に", ""),
    ("くれぐれも", "副詞", "くれぐれ|も", ""),
    ("ひょっとして", "副詞", "ひょっと|し|て", ""),
    ("ひょっとしたら", "副詞", "ひょっと|し|たら", ""),
    ("ひょっとすると", "副詞", "ひょっと|する|と", ""),
    ("とんでもない", "形容詞", "とんでも|ない", ""),
    ("もったいない", "形容詞", "もったい|ない", ""),
    ("めんどくさい", "形容詞", "めんど|くさい", ""),
    ("しょうがない", "形容詞", "しょう|が|ない", ""),
    # Grammar expressions the tagger cuts at its particles. A token written =X
    # matches any token whose dictionary form is X, so the expression joins in
    # every inflection (こと|が|あっ|た is ことがある); a verb or adjective row
    # cards its headword, the others their surface. A noun row joins only after
    # a noun (猫という, 親として; not ダメだという 'says', 起こそうとして 'tries
    # to'), a verb row only after a verb (会ったことがある; not あんなことがあって
    # 'such a thing happened').
    ("という", "連体詞", "と|いう", "noun"),
    ("というのは", "接続詞", "と|いう|の|は", ""),
    ("として", "副詞", "と|し|て", "noun"),
    ("について", "副詞", "に|つい|て", ""),
    ("によって", "副詞", "に|よっ|て", ""),
    ("により", "副詞", "に|より", ""),
    ("による", "連体詞", "に|よる", ""),
    ("どのように", "副詞", "どの|よう|に", ""),
    ("このように", "副詞", "この|よう|に", ""),
    ("のように", "副詞", "の|よう|に", ""),
    ("ように", "副詞", "よう|に", ""),
    ("ような", "連体詞", "よう|な", ""),
    ("ようになる", "動詞", "よう|に|=なる", ""),
    ("ことがある", "動詞", "こと|が|=ある", "verb"),
    ("ことができる", "動詞", "こと|が|=できる", ""),
    ("ことになる", "動詞", "こと|に|=なる", ""),
    ("ことにする", "動詞", "こと|に|=する", ""),
    ("かもしれない", "形容詞", "か|も|=しれる", ""),
    ("でも", "接続詞", "で|も", "start"),
    ("だから", "接続詞", "だ|から", "start"),
    ("だって", "接続詞", "だ|って", "start"),
    ("だけど", "接続詞", "だ|けど", "start"),
    ("それで", "接続詞", "それ|で", "start"),
    ("それでも", "接続詞", "それ|で|も", "start"),
    ("それから", "接続詞", "それ|から", "start"),
    ("それとも", "接続詞", "それ|と|も", "start"),
    ("すると", "接続詞", "する|と", "start"),
    ("ところが", "接続詞", "ところ|が", "start"),
    ("ところで", "接続詞", "ところ|で", "start"),
)

_HEADER = """\
# Kana-written words Anki Miner mines by name (anki_miner/services/kana_words.py).
# Generated by scripts/build_kana_words.py from JMdict_e (EDRDG, CC BY-SA 4.0); do not edit.
# form<TAB>pos1[<TAB>kanji]              one token with this unidic pos1 (kanji: a kanji-written word's kana spelling)
# form<TAB>pos1<TAB>tokens[<TAB>where]   tokens LexicalJoiner joins (where: start, only opening a line or
#                                        clause; noun or verb, only after one; a last token =X: any
#                                        token whose dictionary form is X)
"""


def _is_hiragana(text: str) -> bool:
    return all("ぁ" <= ch <= "ゖ" or ch == "ー" for ch in text)


def _morae(text: str) -> int:
    """Kana counted as morae: a small ゃ, ゅ, ょ or vowel joins the kana before it (しょう is two)."""
    return sum(ch not in "ぁぃぅぇぉゃゅょゎ" for ch in text)


def _scan(jmdict: Path) -> tuple[set[str], dict[str, set[str]], set[str]]:
    """``(kana_written, kanji_readings, spellings)``.

    Common kana spellings of words JMdict writes in kana; common kana
    spellings of words it writes in kanji, with their JMdict parts of speech;
    and every JMdict spelling.
    """
    kana_written: set[str] = set()
    kanji_readings: dict[str, set[str]] = {}
    spellings: set[str] = set()
    root = etree.parse(str(jmdict), _lxml_parser()).getroot()
    for entry in root.iter("entry"):
        kebs = [k.findtext("keb") for k in entry.findall("k_ele")]
        spellings.update(k for k in kebs if k)
        in_kana = not kebs or any(m.text == _USUALLY_KANA for m in entry.iter("misc"))
        pos = {p.text or "" for p in entry.iter("pos")}
        for r_ele in entry.findall("r_ele"):
            reb = r_ele.findtext("reb") or ""
            spellings.add(reb)
            common = {p.text for p in r_ele.findall("re_pri")} & COMMON_PRIORITY_TAGS or reb in UNTAGGED_COMMON
            if not (common and len(reb) >= 2 and _is_hiragana(reb)):
                continue
            if in_kana:
                kana_written.add(reb)
            else:
                kanji_readings.setdefault(reb, set()).update(pos)
    return kana_written, kanji_readings, spellings


def _kanji_reading_fits(form: str, pos1: str, jmdict_pos: set[str]) -> bool:
    """A kanji-written word's kana spelling has a JMdict part of speech of its tag, and length."""
    if not any(p.startswith(prefix) for p in jmdict_pos for prefix in JMDICT_POS[pos1]):
        return False
    if pos1 == "名詞":
        return _morae(form) >= 4 or form in SHORT_SPELLED_NOUNS or form in SHORT_NOUNS
    return _morae(form) >= 3 or form in SHORT_READINGS


def _single_token_tags(word: str) -> set[str]:
    """The pos1 tags unidic-lite gives ``word`` as one whole token across the carriers."""
    tagger = get_shared_tagger()
    return {
        token.feature.pos1 for carrier in CARRIERS for token in tagger(carrier.format(word)) if token.surface == word
    }


def build(jmdict: Path) -> list[str]:
    kana_written, kanji_readings, spellings = _scan(jmdict)
    missing = [row[0] for row in JOINS if row[0] not in spellings]
    if missing:
        raise SystemExit(f"not JMdict spellings: {', '.join(missing)}")
    rows: dict[tuple[str, str], bool] = {}  # (form, pos1) -> a kanji-written word's spelling
    for form in kana_written | kanji_readings.keys():
        for pos1 in _single_token_tags(form) | EXTRA_TAGS.get(form, frozenset()):
            if pos1 not in WORD_POS1 or (form, pos1) in EXCLUDED:
                continue
            if form in kana_written and not (pos1 == "名詞" and len(form) == 2 and form not in SHORT_NOUNS):
                rows[form, pos1] = False
            elif form in kanji_readings and _kanji_reading_fits(form, pos1, kanji_readings[form]):
                rows[form, pos1] = True
    lines = [f"{form}\t{pos1}" + ("\tkanji" if kanji else "") for (form, pos1), kanji in sorted(rows.items())]
    lines += ["\t".join((form, pos1, tokens, *([where] if where else []))) for form, pos1, tokens, where in JOINS]
    return lines


_DEFAULT_OUT = Path(__file__).parent.parent / "anki_miner" / "resources" / "kana_words" / "ja.tsv"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the bundled kana word list from JMdict_e.")
    parser.add_argument("--jmdict", type=Path, required=True, help="Path to JMdict_e (uncompressed XML)")
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    args = parser.parse_args()
    lines = build(args.jmdict)
    args.out.write_text(_HEADER + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(lines)} rows -> {args.out}")


if __name__ == "__main__":
    main()
