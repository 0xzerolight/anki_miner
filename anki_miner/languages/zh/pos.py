"""zh part-of-speech defaults (jieba.posseg flags).

``allowed_pos`` matches on ``feature.pos1`` — the flag's first letter, the
coarse class — and ``excluded_subtypes`` on ``feature.pos2``, the full flag.
That is the same two-level shape unidic gives the ja defaults, so
``TokenInclusionRule`` is reused unchanged.

Exclusions mirror the ja intent rather than jieba's taxonomy: 固有名詞 becomes
the proper-noun flags (nr/ns/nt and jieba's nr variants), and unidic's 非自立
becomes the bound-morpheme flags (ng/vg/ag/dg), which mark fragments that are
never independent words. Numerals (m), classifiers (q), pronouns (r),
prepositions (p), particles (u*), conjunctions (c) and punctuation (x) need no
exclusion at all — their pos1 is outside ``ZH_ALLOWED_POS``.

Two rulings shape the defaults beyond that mapping:

* **ja parity.** unidic mines time nouns (名詞-普通名詞-副詞可能), place nouns
  and pronouns into Japanese cards today, so jieba's t (时间词), s (处所词),
  f (方位词), l (习用语) and r (代词) classes belong in the Chinese defaults for
  the same reason. Dropping them lost 今天, 家里, 里面, 有意思 and 他.
* **Over-include beats silent drop.** jieba's ``nz`` is a catch-all, not a
  proper-noun class: it fires on ordinary vocabulary (中文 is tagged nz), and an
  excluded subtype removes a word with no trace anywhere the user can see.
  Volume is what the frequency, known-words and i+1 filters downstream are for;
  a word the tagger never emitted cannot be recovered by any of them.
"""

from __future__ import annotations

from collections.abc import Mapping

ZH_ALLOWED_POS: tuple[str, ...] = ("n", "v", "a", "d", "i", "t", "s", "f", "l", "r")

ZH_EXCLUDED_SUBTYPES: tuple[str, ...] = (
    "nr",  # person name
    "nrt",  # transliterated person name
    "nrfg",  # name-like fragment
    "ns",  # place name
    "nt",  # organisation
    "ng",  # bound noun morpheme
    "vg",  # bound verb morpheme
    "ag",  # bound adjective morpheme
    "dg",  # bound adverb morpheme
)

# Shown beside each class in Settings -> Filtering. Chinese label first (the
# names the tagger's own documentation uses), English gloss after.
ZH_POS_LABELS: Mapping[str, str] = {
    "n": "名词 (noun)",
    "v": "动词 (verb)",
    "a": "形容词 (adjective)",
    "d": "副词 (adverb)",
    "i": "成语 (idiom)",
    "t": "时间词 (time word)",
    "s": "处所词 (place word)",
    "f": "方位词 (locative noun)",
    "l": "习用语 (set phrase)",
    "r": "代词 (pronoun)",
}
