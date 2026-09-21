"""zh part-of-speech defaults (jieba.posseg flags).

``allowed_pos`` matches on ``feature.pos1`` — the flag's first letter, the
coarse class — and ``excluded_subtypes`` on ``feature.pos2``, the full flag.
That is the same two-level shape unidic gives the ja defaults, so
``TokenInclusionRule`` is reused unchanged.

What stays out is jieba's function-word and fragment vocabulary: numerals (m),
classifiers (q), particles (u*), punctuation (x) and Latin runs (eng) never
reach ``ZH_ALLOWED_POS`` at all, and the bound-morpheme flags (ng/vg/ag/dg),
which mark pieces that are rarely independent words, are excluded subtypes.
Both rules have rows jieba files wrong — 一起 and 一点儿 are an adverb and a
quantity word it tags m, 喝 a free verb it tags vg — and ``overrides.py``
retags those words before the gate sees them, rather than admitting the class.
The name flags are narrower than the ja 固有名詞 mapping they came from: only
the transliterated and name-like variants (nrt, nrfg) and the organisation flag
(nt) are excluded.

Four rulings shape the defaults beyond that mapping:

* **ja parity.** unidic mines time nouns (名詞-普通名詞-副詞可能), place nouns
  and pronouns into Japanese cards today, so jieba's t (时间词), s (处所词),
  f (方位词), l (习用语) and r (代词) classes belong in the Chinese defaults for
  the same reason. Dropping them lost 今天, 家里, 里面, 有意思 and 他.
* **Over-include beats silent drop.** jieba's ``nz`` is a catch-all, not a
  proper-noun class: it fires on ordinary vocabulary (中文 is tagged nz), and an
  excluded subtype removes a word with no trace anywhere the user can see.
  ``ns`` and ``nr`` are the same story at a far higher price — jieba's own
  dictionary spends them on 太阳, 东西, 城市, 明白, 小姐, 新鲜 and 台风, and cuts
  喝咖啡 as one ``nr`` token so neither 喝 nor 咖啡 survives either; on a
  186-sentence corpus 16.1% of sentences lost a dictionary-attested word that
  way. Admitting the real names the two flags also carry is cheap: a word no
  offline dictionary lists never reaches a card (小明 and 王小明 are dropped a
  stage later), and known-words filtering retires an admitted 北京 after its
  first. Volume is what the frequency, known-words and i+1 filters downstream
  are for; a word the tagger never emitted cannot be recovered by any of them.
* **A Chinese preposition is a content word.** c (连词: 因为 所以 虽然 但是 如果)
  and p (介词: 在 给 跟 从 比 对 为了 除了 关于) are HSK1-3 vocabulary, not the
  Japanese-particle analogue the ja mapping assumed — and several of them were
  already mined whenever jieba happened to tag them as verbs instead.
* **b and z carry no grammar.** jieba files 区别词 (主要 所有 整个 唯一, and 高兴
  in its dictionary) and 状态词 (黑暗 悄悄 雪白) outside n/v/a/d on distributional
  grounds; to a learner they are adjectives and adverbs like any other. Matching
  on the first letter, z admits jieba's zg class too (很 您 车 穿 掉) — kept, since
  those are ordinary words a beginner meets before any of the 状态词.
"""

from __future__ import annotations

from collections.abc import Mapping

ZH_ALLOWED_POS: tuple[str, ...] = ("n", "v", "a", "d", "i", "t", "s", "f", "l", "r", "b", "z", "c", "p")

ZH_EXCLUDED_SUBTYPES: tuple[str, ...] = (
    "nrt",  # transliterated person name
    "nrfg",  # name-like fragment
    "nt",  # organisation
    "ng",  # bound noun morpheme
    "vg",  # bound verb morpheme
    "ag",  # bound adjective morpheme
    "dg",  # bound adverb morpheme
)

# Chinese label first (the names the tagger's own documentation uses), English
# gloss after. ``PosDefaults.labels`` has no consumer yet — the settings POS
# editor still shows raw tags.
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
    "b": "区别词 (distinguishing word)",
    "z": "状态词 (state word)",
    "c": "连词 (conjunction)",
    "p": "介词 (preposition)",
}
