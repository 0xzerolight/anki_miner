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
    | {"もん", "もと", "じき"}
    | {"とき"}
)

#: The only two-kana spellings of kanji-written words listed outside the nouns.
SHORT_READINGS: frozenset[str] = frozenset({"きみ", "ぼく", "おれ", "かれ", "だれ", "ぜひ", "われ", "なん", "うぬ"})

#: The only kanji-written nouns of three morae listed by their kana spelling: those
#: commonly written in kana. A three-mora kana noun is otherwise as often the tail
#: of a longer kana word (いい|かげん, つ|くろう) or a given name (しずく).
SHORT_SPELLED_NOUNS: frozenset[str] = frozenset(
    {"とおり", "おなか", "ゆうべ", "かぎり", "におい", "しょうゆ", "けんか", "うわさ", "まぬけ", "あげく"}
    | {"おかげ", "ほんと", "ほんま", "ごはん", "あした", "しかた", "ふだん", "ことし"}
    | {"ようす", "しっぽ", "きげん", "ぜんぶ"}
    | {"ないしょ", "ひきょう", "いっちょ", "ちゅうちょ"}
    # Not からだ, ことば or ありか: in a line each is as often two words (来た|から|だ,
    # the dialect object こと|ば, 何でも|あり|か).
    | {"ちから", "いのち", "かたち", "ふたり", "せがれ", "じじい", "ばばあ", "おかみ"}
    | {"とどめ", "せりふ", "あるじ", "おなご", "だんご", "ささい", "うかつ"}
    | {"きのう", "しだい", "もよう"}
    # Archaic first- and second-person pronouns of period drama (わらわ is 妾; the tagger files it as a noun).
    | {"わらわ"}
)

#: Spoken kana spellings JMdict gives no priority tag (やっぱ for 矢っ張り, the
#: colloquial てめえ), listed as if it did: each is as common in dialogue as its
#: tagged form.
UNTAGGED_COMMON: frozenset[str] = frozenset(
    {"やっぱ", "とっても", "あくまで", "とっとと", "めっちゃ", "ちょい", "そこそこ", "またまた", "まんまと", "ばっちり"}
    | {"おかげ", "こないだ", "ほんと", "ほんま", "とことん", "ごっこ", "こだわり", "てめえ", "おめえ", "わたくし"}
    | {"ったく", "うむ", "おおきに", "てめぇ", "とやかく", "まんま"}
    | {"まぁ", "そりゃ", "そりゃあ", "こりゃ", "ありゃ", "なんぼ", "ちょいと", "どいつ"}
    | {
        "ねぇ",
        "こら",
        "よっしゃ",
        "よーし",
        "いざ",
        "おう",
        "やあ",
        "おーい",
        "ほな",
        "へえ",
        "へぇ",
        "さぁ",
        "ありがと",
    }
    # Sino-Japanese nouns subtitles write in kana. The parser recovers a long kana
    # spelling without a priority tag by rule only when the word is native
    # (SubtitleParserService._is_long_kana_spelling), so these are named.
    | {"ちきしょう", "いっちょ", "ないしょ", "ひきょう", "べっぴん", "じきじき", "まんじゅう", "こんにゃく"}
    | {"ちゅうちょ", "しょんべん", "ようかん", "そうめん", "もうろく", "たくあん", "けいれん", "ろうぜき"}
    # Pronouns of period, rural and rough speech (JMdict marks them archaic or
    # dialect): the everyday words of period drama, fantasy and yakuza films, and
    # none is the homograph of an everyday word, as the archaic rows left out below are.
    # Left out: うぬ (as often the growl うぬぬ). おら is listed; the parser refuses the
    # shout (おら！ おらよ) by what follows it.
    | {"そなた", "そち", "おぬし", "それがし", "おいら", "あっし", "わて", "あたい", "あたくし"}
    | {"こやつ", "あやつ", "こちとら"}
    # と-adverbs under four kana or drawn out, which the parser's length rule leaves to the
    # list. Not はっと or かっと: as often は|っと (戸締まりはっと) and ず|かっと.
    | {"ぞっと", "じーっと", "そーっと"}
    # Pre-noun adjectivals of formal and spoken lines. Not わが or かの: two kana are
    # as often a piece (the dialect わがんねえ, a name かのまた).
    | {"いかなる", "とある", "さらなる", "ひょんな", "おっきな", "ちっちゃな", "なんたる"}
    # Short spoken and period adverbs. The parser refuses ちと where it is glued to
    # the word before it (俺っ|ちと, 俺ん|ちと).
    | {"あんま", "ちっと", "ちと", "なんせ", "しばし"}
    | {"おら"}
    # The interjection ('hey, come on'), beside the adverb 追い追い the tagger always reads:
    # the parser retags the one that opens a line before a pause.
    | {"おいおい"}
    # Doubled adverbs without a priority tag. The parser's long-adverb rule refuses every exact
    # pair, as the commonest are cries (ちょちょ, あらあら, おうおう), so the words are named.
    # Not ちょいちょい (as often the cry ちょいちょいちょい) or しゃあしゃあ (the tail of いけしゃあしゃあ).
    | {"ごちゃごちゃ", "ただただ", "ぼちぼち", "ついつい", "のこのこ", "もたもた", "よくよく"}
    | {"ゆくゆく", "じゃんじゃん", "もろもろ", "ぬけぬけ", "むざむざ", "おめおめ", "ちょこちょこ", "うじうじ"}
    | {"ほとほと", "ちまちま", "ぐんぐん", "うかうか", "じわじわ", "うじゃうじゃ", "ぐだぐだ", "すくすく"}
    | {"おちおち", "もやもや", "ぬくぬく", "ぼつぼつ", "まずまず", "ばたばた", "へらへら", "めそめそ"}
    | {"いちゃいちゃ", "ひやひや", "うだうだ", "うすうす", "やすやす"}
    | {
        "せがれ",
        "じじい",
        "ばばあ",
        "おかみ",
        "とどめ",
        "せりふ",
        "あるじ",
        "おなご",
        "だんご",
        "ささい",
        "うかつ",
    }
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
    # The request after て (見てごらん). ちょうだい is listed: it is also the noun
    # (お茶をちょうだい), and the parser refuses it after て itself.
    ("ごらん", "名詞"): "auxiliary",
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

#: Words the tagger cuts apart: (headword, pos1, tokens, where[, "grammar"]). Kana
#: spans join in LexicalJoiner rather than the compound matcher, which attests
#: a span by its exact term and so misses words JMdict spells in kana only as a
#: reading (どういう, そのうち). The pos1 is the joined token's, so it decides the
#: card front like any token's. ``where`` is "" (anywhere), "start" (opening a line or
#: clause: a conjunction there, で|も inside one is the particles, 学校でも), "noun"
#: (after a noun), "verb" (after a verb or an auxiliary) or "alone" (opening a clause and
#: followed by a pause: そう|か is the reply そうか。 and the adverb in そうかもね). A "grammar" row is a
#: grammar point (んだ, ください), joined only while the user's Settings → Word
#: Filters → Mine grammar points is on. Left out: それ|に,
#: as a subtitle space before それに伴う reads as a clause break, and その|うち,
#: which is "of those" before の (そのうちの１本).
JOINS: tuple[tuple[str, str, str, str] | tuple[str, str, str, str, str], ...] = (
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
    ("とんでもない", "形容詞", "とんでも|=ない", ""),
    ("もったいない", "形容詞", "もったい|=ない", ""),
    ("めんどくさい", "形容詞", "めんど|くさい", ""),
    ("しょうがない", "形容詞", "しょう|が|=ない", ""),
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
    # Spoken words cut after a clipped pronoun or stem (なん, そっ, どっ, ぴん): the
    # piece alone is no word, or another one. なんか joins only where it opens a
    # clause ('somehow'); after a noun or て it is the particle (照れてなんかない).
    ("なんか", "副詞", "なん|か", "start"),
    ("なんで", "副詞", "なん|で", ""),
    ("なんでも", "副詞", "なん|で|も", ""),
    ("なんでもない", "形容詞", "なん|で|も|=ない", ""),
    ("なんとか", "副詞", "なん|と|か", ""),
    ("なんだか", "副詞", "なん|だ|か", ""),
    ("なんにも", "副詞", "なんに|も", ""),
    ("そっか", "感動詞", "そっ|か", ""),
    ("どっか", "代名詞", "どっ|か", ""),
    ("うちら", "代名詞", "うち|ら", ""),
    ("ぴんと", "副詞", "ぴん|と", ""),
    # Adverbs the tagger cuts at に. Their kana stem alone is turned away, as the
    # adverb is a dictionary word of its own (まとも|に, ほんと|に).
    ("まともに", "副詞", "まとも|に", ""),
    ("めったに", "副詞", "めった|に", ""),
    ("はるかに", "副詞", "はるか|に", ""),
    ("むやみに", "副詞", "むやみ|に", ""),
    ("わずかに", "副詞", "わずか|に", ""),
    ("たしかに", "副詞", "たしか|に", ""),
    ("にわかに", "副詞", "にわか|に", ""),
    ("あまりに", "副詞", "あまり|に", ""),
    ("あまりにも", "副詞", "あまり|に|も", ""),
    ("ほんとに", "副詞", "ほんと|に", ""),
    ("いっぺんに", "副詞", "いっぺん|に", ""),
    ("いっしょに", "副詞", "いっしょ|に", ""),
    ("べつに", "副詞", "べつ|に", ""),
    ("かってに", "副詞", "かって|に", ""),
    ("とっさに", "副詞", "とっさ|に", ""),
    ("じきに", "副詞", "じき|に", ""),
    ("もうじき", "副詞", "もう|じき", ""),
    ("そのかわり", "接続詞", "その|かわり", ""),
    # Greetings and set phrases the tagger cuts at the polite prefix or ending;
    # the piece between is another word (やすみ, かえる) or none (めでとう). いただきます
    # only opens a clause: after て or a verb stem it is the humble auxiliary.
    ("おやすみ", "感動詞", "お|やすみ", ""),
    ("おやすみなさい", "感動詞", "お|やすみ|なさい", ""),
    ("おかえり", "感動詞", "お|かえり", ""),
    ("おかえりなさい", "感動詞", "お|かえり|なさい", ""),
    ("おめでとう", "感動詞", "お|めでとう", ""),
    ("ごちそうさま", "感動詞", "ご|ちそう|さま", ""),
    ("おつかれさま", "感動詞", "お|つかれ|さま", ""),
    ("ごめんなさい", "感動詞", "ごめん|なさい", ""),
    ("いただきます", "感動詞", "いただき|ます", "start"),
    ("かしこまりました", "感動詞", "かしこまり|まし|た", ""),
    ("すまない", "形容詞", "すま|=ない", ""),
    ("すまん", "感動詞", "すま|ん", ""),
    ("はいはい", "感動詞", "はい|はい", ""),
    # Words cut at the polite prefix, whose kana tail alone is turned away
    # (ご|ちそう, お|しまい), and でかした, cut as a verb's past.
    ("ごちそう", "名詞", "ご|ちそう", ""),
    ("おしまい", "名詞", "お|しまい", ""),
    ("おわび", "名詞", "お|わび", ""),
    ("おしゃべり", "名詞", "お|しゃべり", ""),
    ("おせっかい", "名詞", "お|せっかい", ""),
    ("おかわり", "名詞", "お|かわり", ""),
    ("おつかれ", "感動詞", "お|つかれ", ""),
    ("でかした", "感動詞", "でかし|た", ""),
    # Adjectives in ない cut at a particle: joined in every inflection, or their
    # ない is mined alone (どうしようもなかった).
    ("どうしようもない", "形容詞", "どう|しよう|も|=ない", ""),
    ("しようがない", "形容詞", "しよう|が|=ない", ""),
    ("しかたがない", "形容詞", "しかた|が|=ない", ""),
    # Spoken words cut after a clipped demonstrative (こっ, そん, どん, ほん) and
    # このへん, whose へん alone the tagger reads as 変.
    ("このへん", "名詞", "この|へん", ""),
    ("そのへん", "名詞", "その|へん", ""),
    ("どのへん", "名詞", "どの|へん", ""),
    ("こっから", "副詞", "こっ|から", ""),
    ("そっから", "副詞", "そっ|から", ""),
    ("どっから", "副詞", "どっ|から", ""),
    ("どんだけ", "副詞", "どん|だけ", ""),
    ("そんだけ", "副詞", "そん|だけ", ""),
    ("こんだけ", "副詞", "こん|だけ", ""),
    ("あんだけ", "副詞", "あん|だけ", ""),
    ("そんで", "接続詞", "そん|で", "start"),
    ("そんなら", "接続詞", "そん|なら", "start"),
    ("ほんなら", "接続詞", "ほん|なら", "start"),
    # Sentence grammar JMdict lists as expressions and the tagger files as
    # auxiliaries and particles. A row of one token is the token itself, mined
    # by name. A "clause" row joins after whatever can end a clause (行くんだ,
    # 高いんです), not after the adnominal な (好きなんだ) nor where the ん is a
    # verb's own contraction (やってんだ): LexicalJoiner._ends_clause. の|です
    # after a noun is the possessive (私のです), hence "verb". A "copula" row
    # refuses a verb before it: there the で or じゃ is the verb's te-form
    # (読んではない, 読んじゃない), already in the verb's highlight.
    ("んだ", "感動詞", "ん|だ", "clause", "grammar"),
    ("んです", "感動詞", "ん|です", "clause", "grammar"),
    ("のです", "感動詞", "の|です", "verb", "grammar"),
    ("のだ", "感動詞", "の|だ", "verb", "grammar"),
    ("ですか", "感動詞", "です|か", "", "grammar"),
    ("だろう", "感動詞", "だろう", "", "grammar"),
    ("でしょう", "感動詞", "でしょう", "", "grammar"),
    ("だろ", "感動詞", "だろ", "", "grammar"),
    ("でしょ", "感動詞", "でしょ", "", "grammar"),
    ("ください", "感動詞", "ください", "", "grammar"),
    ("じゃない", "形容詞", "じゃ|=ない", "copula", "grammar"),
    ("ではない", "形容詞", "で|は|=ない", "copula", "grammar"),
    ("っていうか", "接続詞", "って|いう|か", "", "grammar"),
    # After the adnominal な the expression is なんだ, なんです (好きなんだ, 歌手なんです).
    ("なんだ", "感動詞", "な|ん|だ", "", "grammar"),
    ("なんです", "感動詞", "な|ん|です", "", "grammar"),
    # Conjunctions and adverbs the tagger cuts at an auxiliary or a particle.
    # The copula rows only open a clause (だったら帰る; 丈夫だったら is the
    # copula's own conditional), and っていう follows a noun as という does.
    # Left out: と|し|たら, which after a volitional is "tried to" (渡そうとしたら).
    ("だったら", "接続詞", "だっ|たら", "start"),
    ("だが", "接続詞", "だ|が", "start"),
    ("ですが", "接続詞", "です|が", "start"),
    ("ですから", "接続詞", "です|から", "start"),
    ("それより", "接続詞", "それ|より", "start"),
    ("そしたら", "接続詞", "そ|し|たら", ""),
    ("そうやって", "副詞", "そう|やっ|て", ""),
    ("こうやって", "副詞", "こう|やっ|て", ""),
    ("くせに", "副詞", "くせ|に", ""),
    ("っていう", "連体詞", "って|いう", "noun"),
    # K1120: spoken words the tagger cuts (や|だ, あか|ん; ほっ|とく was carded ほる 'dig').
    ("やだ", "感動詞", "や|だ", ""),
    ("あかん", "感動詞", "あか|ん", ""),
    ("あかん", "感動詞", "あ|かん", ""),
    ("すんません", "感動詞", "すん|ませ|ん", ""),
    ("すんまへん", "感動詞", "すん|まへ|ん", ""),
    ("ほっとく", "動詞", "ほっ|=とく", ""),
    ("ほっておく", "動詞", "ほっ|て|=おく", ""),
    ("ほっておく", "動詞", "ほっ|て|=おける", ""),
    # K1120: a kana verb + its own negative the dictionary lists as a word (わかん|ない, いら|ん);
    # つまら|ん, くだら|ん and たまら|ん are listed too, and never 詰まる / 下る / 堪る.
    ("ふざけんな", "感動詞", "ふざけん|な", ""),
    ("わからん", "感動詞", "わから|ん", ""),
    ("いらん", "感動詞", "いら|ん", ""),
    ("かまわない", "形容詞", "かまわ|=ない", ""),
    ("つまらん", "感動詞", "つまら|ん", ""),
    ("つまらぬ", "感動詞", "つまら|ぬ", ""),
    ("くだらん", "感動詞", "くだら|ん", ""),
    ("くだらぬ", "感動詞", "くだら|ぬ", ""),
    ("たまらん", "感動詞", "たまら|ん", ""),
    # K1120: the obligation unit after a conditional (行かなければ|ならない, 見ては|いけない). Never
    # なければならない as one word: the verb chain keeps the conditional.
    ("ならない", "形容詞", "なら|=ない", "conditional", "grammar"),
    ("いけない", "形容詞", "いけ|=ない", "conditional", "grammar"),
    ("いけません", "感動詞", "いけ|ませ|ん", "conditional", "grammar"),
    ("いかん", "感動詞", "いか|ん", "conditional", "grammar"),
    ("ならん", "感動詞", "なら|ん", "conditional", "grammar"),
    ("なりません", "感動詞", "なり|ませ|ん", "conditional", "grammar"),
    # K1120: kana expressions the tagger cuts (そう|いえ|ば was carded いう, そう|いや the adverb いや).
    ("そういえば", "接続詞", "そう|いえ|ば", "start"),
    ("そういや", "接続詞", "そう|いや", "start"),
    ("なんとなく", "副詞", "なん|と|なく", ""),
    ("もしも", "副詞", "もし|も", ""),
    ("どうにか", "副詞", "どう|に|か", ""),
    ("わがはい", "代名詞", "わが|はい", ""),
    # K1120: set greetings (よう|こそ was carded よい, いっ|てらっしゃい carded いく).
    ("はじめまして", "感動詞", "はじめ|まし|て", ""),
    ("いらっしゃいませ", "感動詞", "いらっしゃい|ませ", ""),
    ("ようこそ", "感動詞", "よう|こそ", ""),
    ("いってらっしゃい", "感動詞", "いっ|てらっしゃい", ""),
    ("どういたしまして", "感動詞", "どう|いたし|まし|て", ""),
    ("おじゃま", "名詞", "お|じゃま", ""),
    # K1120: clause-opening それでは / それじゃ (carded それで and それ); not before ない (それじゃない).
    ("それでは", "接続詞", "それ|で|は", "start"),
    ("それじゃ", "接続詞", "それ|じゃ", "start"),
    ("それじゃあ", "接続詞", "それ|じゃあ", "start"),
    # K1120b: words the tagger cuts after お/ご that the dictionary lists whole (お|いで, お|すすめ,
    # ご|きげん|よう). おいで after a verb's て is the auxiliary (見ておいで): the joiner passes it over.
    ("おいで", "名詞", "お|いで", ""),
    ("おいで", "動詞", "お|いでよ", ""),
    ("おすすめ", "名詞", "お|すすめ", ""),
    ("おなじみ", "名詞", "お|なじみ", ""),
    ("おめでたい", "形容詞", "お|=めでたい", ""),
    ("ごきげんよう", "感動詞", "ご|きげん|よう", ""),
    ("ごきげん", "名詞", "ご|きげん", ""),
    ("ごもっとも", "形状詞", "ご|もっとも", ""),
    ("おつり", "名詞", "お|つり", ""),
    ("おそろい", "名詞", "お|そろい", ""),
    ("おまかせ", "名詞", "お|まかせ", ""),
    ("おねがい", "名詞", "お|ねがい", ""),
    ("おじき", "名詞", "お|じき", ""),
    ("ごくろうさま", "感動詞", "ご|くろう|さま", ""),
    ("ごくろう", "名詞", "ご|くろう", ""),
    ("おたがいさま", "名詞", "お|たがい|さま", ""),
    ("おたがい", "名詞", "お|たがい", ""),
    ("おせわ", "名詞", "お|せわ", ""),
    ("おだいじに", "感動詞", "お|だいじ|に", ""),
    ("おみまい", "名詞", "お|みまい", ""),
    ("おいわい", "名詞", "お|いわい", ""),
    ("おしおき", "名詞", "お|しおき", ""),
    ("おしらせ", "名詞", "お|しらせ", ""),
    ("おすわり", "名詞", "お|すわり", ""),
    ("おまじない", "名詞", "お|まじない", ""),
    ("おこづかい", "名詞", "お|こづかい", ""),
    ("おむかえ", "名詞", "お|むかえ", ""),
    ("おわかれ", "名詞", "お|わかれ", ""),
    ("おでかけ", "名詞", "お|でかけ", ""),
    ("おことわり", "名詞", "お|ことわり", ""),
    ("おあずけ", "名詞", "お|あずけ", ""),
    ("ごほうび", "名詞", "ご|ほうび", ""),
    ("おまいり", "名詞", "お|まいり", ""),
    # K1120b: negatives that are words of their own: すま|ぬ is the apology 済まぬ, never 済む (the tagger
    # keeps すまない and すまねえ whole), and おもわ|ぬ is 思わぬ 'unexpected'.
    ("すまぬ", "感動詞", "すま|ぬ", ""),
    ("おもわぬ", "連体詞", "おもわ|ぬ", ""),
    # K1120: the spoken contraction わかん|ない is the listed わかんない, not わかる.
    ("わかんない", "形容詞", "わかん|=ない", ""),
    # K1121 EXPR: a word and its function tail that JMdict lists as one entry (あと|で, それ|まで). どこ|か is
    # passed over before a verb of knowing or asking (lexical_joiner._embedded_question).
    ("あとで", "副詞", "あと|で", ""),
    ("おかげで", "副詞", "おかげ|で", ""),
    ("それまで", "副詞", "それ|まで", ""),
    ("これまで", "副詞", "これ|まで", ""),
    ("あれだけ", "副詞", "あれ|だけ", ""),
    ("これだけ", "副詞", "これ|だけ", ""),
    ("それだけ", "副詞", "それ|だけ", ""),
    ("どれだけ", "副詞", "どれ|だけ", ""),
    ("どれほど", "副詞", "どれ|ほど", ""),
    ("これほど", "副詞", "これ|ほど", ""),
    ("それほど", "副詞", "それ|ほど", ""),
    ("あれほど", "副詞", "あれ|ほど", ""),
    ("いつだって", "副詞", "いつ|だ|って", ""),
    ("まずは", "副詞", "まず|は", "start"),
    ("そのとおり", "名詞", "その|とおり", ""),
    ("もうすぐ", "副詞", "もう|すぐ", ""),
    ("もうちょっと", "副詞", "もう|ちょっと", ""),
    ("うまいこと", "副詞", "うまい|こと", ""),
    ("そんなふうに", "副詞", "そんな|ふう|に", ""),
    ("こんなふうに", "副詞", "こんな|ふう|に", ""),
    ("いつものように", "副詞", "いつ|も|の|よう|に", ""),
    ("どこか", "代名詞", "どこ|か", ""),
    # K1121 EXPR: replies that stand alone between pauses (そうか。 そうです、… いいよ。) and questions that
    # open a clause. なんなの is 副詞 so that its kanji spelling 何なの passes the run's allowed_pos.
    ("そうか", "感動詞", "そう|か", "alone"),
    ("そうです", "感動詞", "そう|です", "alone"),
    ("そうですね", "感動詞", "そう|です|ね", "alone"),
    ("そうだ", "感動詞", "そう|だ", "alone"),
    ("そうだね", "感動詞", "そう|だ|ね", "alone"),
    ("いいよ", "感動詞", "いい|よ", "alone"),
    ("なんなの", "副詞", "なん|な|の", "start"),
    ("どうしたの", "感動詞", "どう|し|た|の", "start"),
    ("どうしたん", "感動詞", "どう|し|た|ん", "alone"),
    ("どうしよう", "感動詞", "どう|しよう", ""),
    # K1121 EXPR: particle-headed expressions after a noun, and ほうがいい after a verb. に|とっ|て is passed
    # over before a verb, where it is the て-form of とる (lexical_joiner._verb_te_form).
    ("にとって", "副詞", "に|とっ|て", "noun"),
    ("に対する", "連体詞", "に|対する", "noun"),
    ("に対して", "副詞", "に|対し|て", "noun"),
    ("に対し", "副詞", "に|対し", "noun"),
    ("と共に", "副詞", "と|共に", "noun"),
    ("とともに", "副詞", "と|とも|に", "noun"),
    ("といえば", "副詞", "と|いえ|ば", "noun"),
    ("のせいで", "副詞", "の|せい|で", "noun"),
    ("にしても", "副詞", "に|し|て|も", "noun"),
    ("にしては", "副詞", "に|し|て|は", "noun"),
    ("としたことが", "副詞", "と|し|た|こと|が", "noun"),
    ("に限らず", "副詞", "に|限ら|ず", "noun"),
    ("ほうがいい", "形容詞", "ほう|が|=いい", "verb"),
    # K1121 EXPR: clause openers and fixed adverbs. こうして before a て-auxiliary and そうしたら before いい are
    # the verb (lexical_joiner._te_form_in_use); そう|いっ|た joins only before a noun (_adnominal_only);
    # って|こと|は only at the head of a line or after punctuation (_quotes_what_precedes). No そうして row:
    # half of its lines are the request 'do so' (そうして。 そうしてやってください).
    ("それにしても", "接続詞", "それ|に|し|て|も", "start"),
    ("そうすると", "接続詞", "そう|する|と", "start"),
    ("そうしたら", "接続詞", "そう|し|たら", "start"),
    ("こうして", "副詞", "こう|し|て", "start"),
    ("なので", "接続詞", "な|の|で", "start"),
    ("そうでなければ", "接続詞", "そう|で|なけれ|ば", "start"),
    ("ってことは", "接続詞", "って|こと|は", "start"),
    ("そういった", "連体詞", "そう|いっ|た", ""),
    ("どう考えても", "副詞", "どう|考え|て|も", ""),
    ("それはそれで", "副詞", "それ|は|それ|で", ""),
    ("どうだっていい", "形容詞", "どう|だっ|て|=いい", ""),
    ("わけのわからない", "形容詞", "わけ|の|わから|=ない", ""),
    # K1121 EXPR: verbs the tagger cuts into two verbs: あり|え is ありえる (never ある + える), い|なく|なる is
    # いなくなる 'disappear'.
    ("ありえない", "形容詞", "あり|え|=ない", ""),
    ("ありえる", "動詞", "あり|=える", ""),
    ("いなくなる", "動詞", "い|なく|=なる", ""),
    # K1121 EXPR: grammar points. みたい is one token the tagger files as an auxiliary stem (猫みたいだ); the verb
    # 見たい is み|たい and never matches.
    ("みたい", "形状詞", "みたい", "", "grammar"),
    ("じゃないか", "感動詞", "じゃ|ない|か", "copula", "grammar"),
    ("でしょうか", "感動詞", "でしょう|か", "", "grammar"),
    # K1121 EXPR: greetings JMdict lists whole. The two with a kanji token are 副詞 so that they pass the run's
    # allowed_pos (a join that is not pure kana is no list word).
    ("ありがとうございます", "感動詞", "ありがとう|ござい|ます", ""),
    ("ありがとうございました", "感動詞", "ありがとう|ござい|まし|た", ""),
    ("おはようございます", "感動詞", "おはよう|ござい|ます", ""),
    ("よろしくお願いします", "副詞", "よろしく|お願い|し|ます", ""),
    ("ご覧ください", "副詞", "ご覧|ください", ""),
    # K1121 LIST: grammar points of one or two tokens. らしい and やろ are the row only as the tagger's
    # auxiliary (the suffix of 春らしい and the volitional やろ of やる are other tokens:
    # lexical_joiner._not_the_auxiliary); じゃ|ね only after a noun and before a pause (学生じゃね？; the
    # leave-taking じゃね opens its line); うる only after a verb (起こりうる; 本をうる is 売る). The kanji
    # 如き / 如く are 副詞 so that they pass the run's allowed_pos. しかる|べき is the entry しかるべき
    # (no grammar point: under the べき row its しかる would be carded alone).
    ("らしい", "形容詞", "=らしい", "", "grammar"),
    ("ごとき", "感動詞", "ごとき", "", "grammar"),
    ("ごとく", "感動詞", "ごとく", "", "grammar"),
    ("如き", "副詞", "如き", "", "grammar"),
    ("如く", "副詞", "如く", "", "grammar"),
    ("べき", "感動詞", "べき", "", "grammar"),
    ("すべき", "感動詞", "す|べき", "", "grammar"),
    ("やろ", "感動詞", "やろ", "", "grammar"),
    ("やない", "形容詞", "や|=ない", "copula", "grammar"),
    ("じゃね", "感動詞", "じゃ|ね", "noun", "grammar"),
    ("うる", "動詞", "うる", "verb", "grammar"),
    ("しかるべき", "連体詞", "しかる|べき", ""),
    # K1121 LIST: やらしい is one adjective (the tagger cuts や + the auxiliary らしい).
    ("やらしい", "形容詞", "や|=らしい", ""),
    # K1121 LIST: the entry どうした before the explanatory ん / の + a copula (どうした + んだ, どうした + のだ). The
    # longer どうしたん / どうしたの are the card only where the clause ends on them: a row that does not fit gives
    # its tokens to the next one down (LexicalJoiner._kana_row), and どう|し|た|の is passed over before だ / です
    # (lexical_joiner._no_of_noda).
    ("どうした", "感動詞", "どう|し|た", "start"),
    # K1121 LIST: entries whose kana verb (する, くる, すぎる) was carded alone. ちょっと|し|た only before a noun
    # (_adnominal_only); に|つれ|て not before くる / いく (家につれてくる: _verb_te_form); しだい|に only opening a
    # clause (気持ちしだいになる is the suffix 次第 + に). No やってくる row: a third of its lines are やる + くる
    # (昔からやってきた, 代わりにやってきました).
    ("ちょっとした", "連体詞", "ちょっと|し|た", ""),
    ("につれて", "副詞", "に|つれ|て", ""),
    ("にすぎない", "形容詞", "に|すぎ|=ない", ""),
    ("にすぎない", "形容詞", "に|すぎ|ませ|ん", ""),
    ("ときたら", "副詞", "と|き|たら", "noun"),
    ("しだいに", "副詞", "しだい|に", "start"),
    ("さすれば", "接続詞", "さ|すれ|ば", "start"),
    # K1121 LIST: expressions that were still missed. かい|が|ある and しか|ない only after a verb (待ったかいがあった,
    # 行くしかない; 金しかない is 'only money'), まで|に after a noun, それ|と only opening a clause before a pause
    # (それとこれ is それ + と), だっ|たり|し|て only where the sentence stops (lexical_joiner._runs_on). あり|えへん
    # is the Kansai negative of ありえる, which the tagger cuts あり + an interjection.
    ("かいがある", "動詞", "かい|が|=ある", "verb"),
    ("それすら", "副詞", "それ|すら", ""),
    ("しかない", "形容詞", "しか|=ない", "verb"),
    ("しかない", "形容詞", "しか|あり|ませ|ん", "verb"),
    ("までに", "副詞", "まで|に", "noun"),
    ("おめでとうございます", "感動詞", "お|めでとう|ござい|ます", ""),
    ("お誕生日おめでとうございます", "副詞", "お誕生日おめでとう|ござい|ます", ""),
    ("そんなん", "代名詞", "そんな|ん", ""),
    ("だったりして", "感動詞", "だっ|たり|し|て", ""),
    ("やいなや", "副詞", "や|いな|や", "verb"),
    ("ざるをえない", "形容詞", "ざる|を|え|=ない", ""),
    ("ざるを得ない", "形容詞", "ざる|を|得|=ない", ""),
    ("せざるをえない", "形容詞", "せ|ざる|を|え|=ない", ""),
    ("せざるを得ない", "形容詞", "せ|ざる|を|得|=ない", ""),
    ("それと", "接続詞", "それ|と", "alone"),
    ("よって", "接続詞", "よっ|て", "start"),
    ("ありえる", "動詞", "あり|えへん", ""),
    # K1121 LIST: 役立た|ず is the noun 役立たず where a noun stands (この役立たずが, 役立たずだ:
    # lexical_joiner._noun_place); before a clause it is the verb negated.
    ("役立たず", "名詞", "役立た|ず", ""),
    # K1121 LIST: words the tagger cuts after the kana なん. なん|と is the adverb before what it exclaims at or
    # before a verb of saying (なんと美しい, なんと言えばいい); before another verb or a pronoun the と is 'with'
    # (何と戦う, 何と何: lexical_joiner._with_what). なん|て is passed over before a kana いう: なんていうか is no
    # entry of the dictionaries, and its いう would be carded alone.
    ("なんと", "副詞", "なん|と", ""),
    ("なんとも", "副詞", "なん|と|も", ""),
    ("なんとしても", "副詞", "なん|と|し|て|も", ""),
    ("なんという", "連体詞", "なん|と|いう", ""),
    ("なんというか", "副詞", "なん|と|いう|か", ""),
    ("なんて", "副詞", "なん|て", ""),
    ("なんてこった", "感動詞", "なん|てこっ|た", ""),
    # K1121 LIST: listed expressions on なる, so that the adjective + なる reading does not card their pieces:
    # よく|なる is よくなる 'get better'; そう|に|なる right after a verb's stem is そうになる 'almost do' (泣きそうになった).
    ("よくなる", "動詞", "よく|=なる", ""),
    ("そうになる", "動詞", "そう|に|=なる", "verb"),
    # K1121 LIST: 目|に|あっ where the tagger reads the kana verb as ある (ひどい目にあった): the entry 目にあう, only
    # with a modifier before 目 (lexical_joiner._bare_me; 目にあるもの is 'what is in the eye').
    ("目にあう", "動詞", "目|に|=ある", ""),
    # K1121 LIST: more words on the kana なん, so that the word row なん does not card their first piece.
    ("なんらか", "副詞", "なん|ら|か", ""),
    ("なんにせよ", "副詞", "なん|に|せよ", ""),
    ("なんだったら", "副詞", "なん|だっ|たら", "start"),
    ("なんも", "副詞", "なん|も", ""),
    # K1121 LIST: お / ご + a kana noun that the dictionary lists whole by its reading (ご|あいさつ is ご挨拶). The noun
    # alone is turned away as a piece of that longer word, so without the row the line has no card. Each row was
    # checked both ways: the tagger cuts it so, and the joined kana is an entry's reading.
    ("おかね", "名詞", "お|かね", ""),
    ("おみず", "名詞", "お|みず", ""),
    ("おちゃ", "名詞", "お|ちゃ", ""),
    ("おさけ", "名詞", "お|さけ", ""),
    ("おふろ", "名詞", "お|ふろ", ""),
    ("おはなし", "名詞", "お|はなし", ""),
    ("おなまえ", "名詞", "お|なまえ", ""),
    ("おからだ", "名詞", "お|からだ", ""),
    ("おうち", "名詞", "お|うち", ""),
    ("おしごと", "名詞", "お|しごと", ""),
    ("おてつだい", "名詞", "お|てつだい", ""),
    ("おべんとう", "名詞", "お|べんとう", ""),
    ("おみやげ", "名詞", "お|みやげ", ""),
    ("おまつり", "名詞", "お|まつり", ""),
    ("おいのり", "名詞", "お|いのり", ""),
    ("おとも", "名詞", "お|とも", ""),
    ("おつかい", "名詞", "お|つかい", ""),
    ("おひる", "名詞", "お|ひる", ""),
    ("おしろ", "名詞", "お|しろ", ""),
    ("おてら", "名詞", "お|てら", ""),
    ("おまもり", "名詞", "お|まもり", ""),
    ("おさら", "名詞", "お|さら", ""),
    ("おにく", "名詞", "お|にく", ""),
    ("おはな", "名詞", "お|はな", ""),
    ("おつき", "名詞", "お|つき", ""),
    ("おかお", "名詞", "お|かお", ""),
    ("おくち", "名詞", "お|くち", ""),
    ("おへそ", "名詞", "お|へそ", ""),
    ("おへや", "名詞", "お|へや", ""),
    ("おいえ", "名詞", "お|いえ", ""),
    ("おくに", "名詞", "お|くに", ""),
    ("おやま", "名詞", "お|やま", ""),
    ("おそうじ", "名詞", "お|そうじ", ""),
    ("おつとめ", "名詞", "お|つとめ", ""),
    ("おやくそく", "名詞", "お|やくそく", ""),
    ("おでんわ", "名詞", "お|でんわ", ""),
    ("おことば", "名詞", "お|ことば", ""),
    ("おしり", "名詞", "お|しり", ""),
    ("ごあいさつ", "名詞", "ご|あいさつ", ""),
    ("ごしゅじん", "名詞", "ご|しゅじん", ""),
    ("ごめいわく", "名詞", "ご|めいわく", ""),
    ("ごしんぱい", "名詞", "ご|しんぱい", ""),
    ("ごあんない", "名詞", "ご|あんない", ""),
    ("ごれんらく", "名詞", "ご|れんらく", ""),
    ("ごきょうだい", "名詞", "ご|きょうだい", ""),
    ("ごきょうりょく", "名詞", "ご|きょうりょく", ""),
    ("ごせいこう", "名詞", "ご|せいこう", ""),
    ("ごいっしょ", "名詞", "ご|いっしょ", ""),
    ("ごぞんじ", "名詞", "ご|ぞんじ", ""),
    ("ごじしん", "名詞", "ご|じしん", ""),
    # K1121 INTJ: interjections the tagger cuts apart. An "interjection" row is joined only while the parser
    # mines interjections (its switch, off by default): あの|ね opening a clause, しまっ|た and ん|ん alone.
    ("あのね", "感動詞", "あの|ね", "start", "interjection"),
    ("しまった", "感動詞", "しまっ|た", "alone", "interjection"),
    ("んん", "感動詞", "ん|ん", "alone", "interjection"),
    # K1121 ROWS: a word + a particle that is another word where it stands. それ|に, そこ|で and それ|も are the
    # conjunction opening a clause before a pause (それに、…); a space is enough for それ|に unless a verb follows
    # it (それに 気づいた), never for そこ|で and それ|も (そこで 着替えてて, それも いい: lexical_joiner._needs_a_comma).
    # よく|も not before ない / 悪く (_good_too), どちら|か not before an embedded question (_embedded_question),
    # なに|かしら not where its clause ends (あれは何かしら: _wonders_what), いい|から opening a line or after
    # punctuation (_quotes_what_precedes), もの|か where the sentence stops after a verb that is no past (_thing_done).
    ("それに", "接続詞", "それ|に", "alone"),
    ("そこで", "接続詞", "そこ|で", "alone"),
    ("それも", "接続詞", "それ|も", "alone"),
    ("それなのに", "接続詞", "それ|な|の|に", "start"),
    ("それどころか", "接続詞", "それ|どころ|か", "start"),
    ("それどころ", "副詞", "それ|どころ", ""),
    ("よくも", "副詞", "よく|も", ""),
    ("よくぞ", "副詞", "よく|ぞ", ""),
    ("どちらか", "名詞", "どちら|か", ""),
    ("どっちか", "名詞", "どっち|か", ""),
    ("どちらかというと", "副詞", "どちら|か|と|いう|と", ""),
    ("どっちかというと", "副詞", "どっち|か|と|いう|と", ""),
    ("なにかしら", "副詞", "なに|かしら", ""),
    ("どこでも", "副詞", "どこ|で|も", ""),
    ("さきほど", "名詞", "さき|ほど", ""),
    ("いいから", "副詞", "いい|から", "start"),
    ("ものか", "副詞", "もの|か", "verb", "grammar"),
    # K1121 ROWS: entries that start on a particle. と|する right after a volitional is とする 'try to; be about to'
    # (逃げようとした; after a noun or a plain form it stays out: lexical_joiner._no_volitional_before); と|いっ|て|も
    # opening a clause is the conjunction といっても.
    ("とする", "動詞", "と|=する", "verb", "grammar"),
    ("といっても", "接続詞", "と|いっ|て|も", "start"),
    # K1121 ROWS: よう + the copula after a predicate is the grammar point ようだ 'it seems' (降らないようだ,
    # 元気なようで); after の it is 'like' and stays out (lexical_joiner._no_predicate_before). よう|な and よう|に
    # keep their own rows above.
    ("ようだ", "形容詞", "よう|=だ", "", "grammar"),
    ("ようです", "形容詞", "よう|=です", "", "grammar"),
    # K1121 ROWS: conjunctions the tagger files as the copula or as particles: なら, な|の|に and で|は heading a line
    # or following punctuation, before a pause (なら、私も行く; 学生 なら is the copula: lexical_joiner._quotes_what_precedes).
    ("なら", "接続詞", "なら", "alone"),
    ("なのに", "接続詞", "な|の|に", "alone"),
    ("では", "接続詞", "で|は", "alone"),
    # けど, じゃ and んで where the tagger says 接続詞 (lexical_joiner._not_the_conjunction) and they open a clause; じゃ
    # also needs a pause after it (じゃなーい and じゃあさ are other words). As word rows they were carded inside a
    # clause too (来たとじゃ, 君とかじゃ, な|んで), where the tagger's 接続詞 is wrong.
    ("けど", "接続詞", "けど", "start"),
    ("じゃ", "接続詞", "じゃ", "alone"),
    ("んで", "接続詞", "んで", "start"),
    # K1121 ROWS: more interjection entries of two tokens: a repeat (うん|うん) or a word + a final particle (いい|ね),
    # each a reply of its own, opening a clause before a pause; あの|さ opens a clause as あの|ね does.
    ("なんだ", "感動詞", "なん|だ", "alone", "interjection"),
    ("なんだと", "感動詞", "なん|だ|と", "alone", "interjection"),
    ("もういい", "感動詞", "もう|いい", "alone", "interjection"),
    ("いえいえ", "感動詞", "いえ|いえ", "alone", "interjection"),
    ("そうね", "感動詞", "そう|ね", "alone", "interjection"),
    ("うんうん", "感動詞", "うん|うん", "alone", "interjection"),
    ("いいぞ", "感動詞", "いい|ぞ", "alone", "interjection"),
    ("いいね", "感動詞", "いい|ね", "alone", "interjection"),
    ("こちらこそ", "感動詞", "こちら|こそ", "alone", "interjection"),
    ("まあね", "感動詞", "まあ|ね", "alone", "interjection"),
    ("よしよし", "感動詞", "よし|よし", "alone", "interjection"),
    ("またね", "感動詞", "また|ね", "alone", "interjection"),
    ("おいおい", "感動詞", "おい|おい", "alone", "interjection"),
    ("ねえねえ", "感動詞", "ねえ|ねえ", "alone", "interjection"),
    ("やれやれ", "感動詞", "やれ|やれ", "alone", "interjection"),
    ("はいよ", "感動詞", "はい|よ", "alone", "interjection"),
    ("じゃあね", "感動詞", "じゃあ|ね", "alone", "interjection"),
    ("あのさ", "感動詞", "あの|さ", "start", "interjection"),
    # K1121 ROWS: the colloquial という / って言う, one token the tagger files as an auxiliary (何っちゅうことや,
    # 行くっつってた). A verb row, so that the card front is the dictionary form in every inflection.
    ("っちゅう", "動詞", "=っちゅう", "", "grammar"),
    ("っつう", "動詞", "=っつう", "", "grammar"),
)

_HEADER = """\
# Kana-written words Anki Miner mines by name (anki_miner/services/kana_words.py).
# Generated by scripts/build_kana_words.py from JMdict_e (EDRDG, CC BY-SA 4.0); do not edit.
# form<TAB>pos1[<TAB>kanji]              one token with this unidic pos1 (kanji: a kanji-written word's kana spelling)
# form<TAB>pos1<TAB>tokens[<TAB>where]   tokens LexicalJoiner joins (where: start, only opening a line or
#                                        clause; noun or verb, only after one; alone, only opening a
#                                        clause and before a pause; a last token =X: any token whose
#                                        dictionary form is X). A token also matches a kanji-written
#                                        token by its reading when the dictionary lists that spelling.
# form<TAB>pos1<TAB>tokens<TAB>where<TAB>grammar
#                                        a grammar point (where may be empty), joined only while
#                                        Mine grammar points is on
# form<TAB>pos1<TAB>tokens<TAB>where<TAB>interjection
#                                        an interjection, joined only while the parser mines interjections
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
    lines += [
        "\t".join((form, pos1, tokens, *([where] if where or grammar else []), *grammar))
        for form, pos1, tokens, where, *grammar in JOINS
    ]
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
