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
#: kana recovery already brings those back by attestation.
WORD_POS1: tuple[str, ...] = ("副詞", "代名詞", "連体詞", "接続詞", "名詞")

#: The only two-kana nouns listed. A two-kana noun is as often a piece of a
#: longer word the tagger split (まね|た → ねた, や|つら, すり|ガラス) or a
#: particle or suffix (さえ, ほど, たち), so the short ones are named instead.
SHORT_NOUNS: frozenset[str] = frozenset(
    {"こと", "もの", "ため", "うち", "はず", "せい", "やつ", "まま", "ほか", "よそ", "そば", "だめ", "みな"}
    | {"ひと", "いぬ", "ねこ", "ごみ", "くそ"}
)

#: The only two-kana spellings of kanji-written words listed outside the nouns.
SHORT_READINGS: frozenset[str] = frozenset({"きみ", "ぼく", "おれ", "かれ", "だれ"})

#: The only kanji-written nouns of three morae listed by their kana spelling: those
#: commonly written in kana. A three-mora kana noun is otherwise as often the tail
#: of a longer kana word (いい|かげん, つ|くろう) or a given name (しずく).
SHORT_SPELLED_NOUNS: frozenset[str] = frozenset(
    {"とおり", "おなか", "ゆうべ", "かぎり", "におい", "しょうゆ", "けんか", "うわさ", "まぬけ", "あげく"}
)

#: The JMdict part-of-speech text a kanji-written word needs for each tag.
JMDICT_POS: dict[str, tuple[str, ...]] = {
    "代名詞": ("pronoun",),
    "副詞": ("adverb",),
    "連体詞": ("pre-noun adjectival",),
    "接続詞": ("conjunction",),
    "名詞": ("noun (common)",),
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
}

#: Words the tagger cuts apart: (headword, pos1, tokens, line start only). Kana
#: spans join in LexicalJoiner rather than the compound matcher, which attests
#: a span by its exact term and so misses words JMdict spells in kana only as a
#: reading (どういう, そのうち). The pos1 is the joined token's, so it decides the
#: card front like any token's. A line-start row is a conjunction only where it opens a
#: line or clause: で|も inside one is the particles (学校でも). Left out: それ|に,
#: as a subtitle space before それに伴う reads as a clause break, and その|うち,
#: which is "of those" before の (そのうちの１本).
JOINS: tuple[tuple[str, str, str, bool], ...] = (
    ("そのまま", "副詞", "その|まま", False),
    ("このまま", "副詞", "この|まま", False),
    ("そのうえ", "接続詞", "その|うえ", False),
    ("このあいだ", "名詞", "この|あいだ", False),
    ("どのくらい", "副詞", "どの|くらい", False),
    ("どのぐらい", "副詞", "どの|ぐらい", False),
    ("そういう", "連体詞", "そう|いう", False),
    ("こういう", "連体詞", "こう|いう", False),
    ("どういう", "連体詞", "どう|いう", False),
    ("ああいう", "連体詞", "ああ|いう", False),
    ("もしかして", "副詞", "もし|か|し|て", False),
    ("もしかしたら", "副詞", "もし|か|し|たら", False),
    ("もしかすると", "副詞", "もし|か|する|と", False),
    ("どうも", "副詞", "どう|も", False),
    ("ために", "名詞", "ため|に", False),
    ("やけに", "副詞", "やけ|に", False),
    ("かどうか", "副詞", "か|どう|か", False),
    ("いつも", "副詞", "いつ|も", False),
    ("いつか", "副詞", "いつ|か", False),
    ("いつでも", "副詞", "いつ|で|も", False),
    ("いつまでも", "副詞", "いつ|まで|も", False),
    ("これから", "副詞", "これ|から", False),
    ("どうして", "副詞", "どう|し|て", False),
    ("どうしても", "副詞", "どう|し|て|も", False),
    ("どうやって", "副詞", "どう|やっ|て", False),
    ("そんなに", "副詞", "そんな|に", False),
    ("こんなに", "副詞", "こんな|に", False),
    ("どんなに", "副詞", "どんな|に", False),
    # A pronoun's plural is its own JMdict word; alone, おれ would be cut from おれたち.
    ("わたしたち", "代名詞", "わたし|たち", False),
    ("ぼくたち", "代名詞", "ぼく|たち", False),
    ("おれたち", "代名詞", "おれ|たち", False),
    ("おまえたち", "代名詞", "おまえ|たち", False),
    ("きみたち", "代名詞", "きみ|たち", False),
    ("あなたたち", "代名詞", "あなた|たち", False),
    ("かれら", "代名詞", "かれ|ら", False),
    ("おまえら", "代名詞", "おまえ|ら", False),
    ("あいつら", "代名詞", "あいつ|ら", False),
    ("こいつら", "代名詞", "こいつ|ら", False),
    ("これら", "代名詞", "これ|ら", False),
    ("それら", "代名詞", "それ|ら", False),
    ("いいかげん", "形状詞", "いい|かげん", False),
    ("なぜか", "副詞", "なぜ|か", False),
    ("すぐに", "副詞", "すぐ|に", False),
    ("でも", "接続詞", "で|も", True),
    ("だから", "接続詞", "だ|から", True),
    ("だって", "接続詞", "だ|って", True),
    ("だけど", "接続詞", "だ|けど", True),
    ("それで", "接続詞", "それ|で", True),
    ("それでも", "接続詞", "それ|で|も", True),
    ("それから", "接続詞", "それ|から", True),
    ("それとも", "接続詞", "それ|と|も", True),
    ("すると", "接続詞", "する|と", True),
    ("ところが", "接続詞", "ところ|が", True),
    ("ところで", "接続詞", "ところ|で", True),
)

_HEADER = """\
# Kana-written words Anki Miner mines by name (anki_miner/services/kana_words.py).
# Generated by scripts/build_kana_words.py from JMdict_e (EDRDG, CC BY-SA 4.0); do not edit.
# form<TAB>pos1[<TAB>kanji]              one token with this unidic pos1 (kanji: a kanji-written word's kana spelling)
# form<TAB>pos1<TAB>tokens[<TAB>start]   tokens LexicalJoiner joins (start: only opening a line or clause)
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
            common = {p.text for p in r_ele.findall("re_pri")} & COMMON_PRIORITY_TAGS
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
        for pos1 in _single_token_tags(form):
            if pos1 not in WORD_POS1 or (form, pos1) in EXCLUDED:
                continue
            if form in kana_written and not (pos1 == "名詞" and len(form) == 2 and form not in SHORT_NOUNS):
                rows[form, pos1] = False
            elif form in kanji_readings and _kanji_reading_fits(form, pos1, kanji_readings[form]):
                rows[form, pos1] = True
    lines = [f"{form}\t{pos1}" + ("\tkanji" if kanji else "") for (form, pos1), kanji in sorted(rows.items())]
    lines += ["\t".join((form, pos1, tokens, *(["start"] if start else []))) for form, pos1, tokens, start in JOINS]
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
