"""The two pos2 tiers Thai mining excludes by default (spec C.3).

Thai has no inflection and its taggers mis-tag spoken particles -- ``ค่ะ`` comes
back NOUN five times and PART eight times over 4,485 real dictionary example
sentences, ``คะ`` NOUN three times and PROPN twice, ``คุณ`` NOUN fifty-one times.
So the function-word gate is a LIST, keyed on the surface, applied after the
model has spoken.

``TH_STOPWORDS`` is first-party and deliberately NOT ``pythainlp.corpus
.thai_stopwords()``. That corpus is a search-engine stopword list: 355 of its
1,030 members carry a content part of speech in wty-th-en, and over those same
4,485 sentences the tagger calls ``มี`` a content word 243 times, ``ไป`` 219,
``ให้`` 166, ``พูด`` 32, ``คิด`` 25 and ``ช่วย`` 23. Excluding those by default
would silently delete ordinary learner vocabulary, and an excluded subtype
leaves no trace the user can see. What ships instead is the spec's curated
particle / pronoun / kin-title / interjection set, plus the particle CLUSTERS
newmm emits as one token (``นะคะ``, ``นะจ๊ะ``, ``ฮะ``, ``มั้ย`` -- a cluster never
reaches the entry for its parts), plus the 85 members of that corpus with no
content tag anywhere in wty. Union 125 terms, most frequent first. Regenerate
with the build script recorded in the plan rather than editing by hand.

There is deliberately **no name tier**. Subtracting ``thai_words()`` from the
name corpora is mandatory (2,197 names are ordinary words: ``น้ำ`` water,
``ใจ`` heart, ``รัก`` love) and empties the tier, because newmm's own dictionary
IS ``Trie(thai_words())`` -- what is left is by construction what newmm cannot
emit. Over 4,485 real sentences the surviving rule fires once. The gate the tier
was for already exists: a recognised name is tagged PROPN, and PROPN is outside
``TH_ALLOWED_POS``.
"""

from __future__ import annotations

#: Repetition, abbreviation and section marks. ``ๆ`` and ``ฯ`` are their own
#: newmm tokens; ``ฯลฯ`` (et cetera) is one token; the fongman, angkhankhu and
#: khomut are manuscript marks that survive OCR'd e-books. ``support._THAI_LETTERS``
#: and ``tokenizer._THAI_LETTERS`` both subtract these code points, so a mark is
#: never "Thai script" for the ingestion gate either.
TH_MARKS: frozenset[str] = frozenset(
    {
        "\N{THAI CHARACTER MAIYAMOK}",
        "\N{THAI CHARACTER PAIYANNOI}",
        "ฯลฯ",
        "\N{THAI CHARACTER FONGMAN}",
        "\N{THAI CHARACTER ANGKHANKHU}",
        "\N{THAI CHARACTER KHOMUT}",
    }
)

#: The single-character marks, as code points, for the two script gates.
TH_MARK_CODE_POINTS: frozenset[str] = frozenset(m for m in TH_MARKS if len(m) == 1)

#: Thai function words excluded from mining. First-party list (see the module
#: docstring): the spec's curated particle / pronoun / kin-title / interjection
#: set plus the members of PyThaiNLP's thai_stopwords() (CC0) that carry no
#: content part of speech in wty-th-en (Wiktionary, CC BY-SA 4.0). Most
#: frequent first, by TNC rank.
TH_STOPWORDS: frozenset[str] = frozenset(
    {
        "และ",
        "ก็",
        "หรือ",
        "นี้",
        "เขา",
        "นั้น",
        "เรา",
        "ผม",
        "เมื่อ",
        "ฉัน",
        "จึง",
        "เธอ",
        "คุณ",
        "มัน",
        "ถ้า",
        "อะไร",
        "นาย",
        "นะ",
        "ใคร",
        "ท่าน",
        "น่า",
        "พี่",
        "ครับ",
        "ตั้งแต่",
        "ก็ได้",
        "อย่างไร",
        "ล่ะ",
        "ค่ะ",
        "คะ",
        "ทำไม",
        "เนื่องจาก",
        "ตนเอง",
        "นั่น",
        "ทั้งหมด",
        "เหล่านี้",
        "แก",
        "น้อง",
        "นาง",
        "ไม่ว่า",
        "หนู",
        "น่ะ",
        "หลังจาก",
        "สิ",
        "เป็นต้น",
        "ทั้งนี้",
        "นอกจาก",
        "ยังไง",
        "อา",
        "ไง",
        "ณ",
        "ดิฉัน",
        "เหรอ",
        "จนถึง",
        "ข้าพเจ้า",
        "ก็ตาม",
        "พวกเขา",
        "มั้ย",
        "แม้แต่",
        "ป้า",
        "ภายใต้",
        "แม้ว่า",
        "นั่นเอง",
        "เถอะ",
        "ที่ไหน",
        "เนี่ย",
        "จากนั้น",
        "รึ",
        "แหละ",
        "ก็ดี",
        "กู",
        "ลุง",
        "ทั้งที่",
        "น้า",
        "ซิ",
        "เท่าไหร่",
        "วะ",
        "เพราะว่า",
        "เพราะฉะนั้น",
        "แต่ว่า",
        "จนกว่า",
        "เมื่อไหร่",
        "มึง",
        "ฮะ",
        "เท่าใด",
        "เอ่อ",
        "จ๊ะ",
        "ทว่า",
        "เฮ้ย",
        "เอ็ง",
        "อย่างเช่น",
        "อืม",
        "เผื่อ",
        "เถิด",
        "จ้ะ",
        "คุณครู",
        "นางสาว",
        "เมื่อไร",
        "กระนั้น",
        "ดั่ง",
        "โว้ย",
        "จ้า",
        "ที่แท้",
        "กระผม",
        "ว่ะ",
        "เว้ย",
        "ไฉน",
        "หรอ",
        "นู้น",
        "ครับผม",
        "ด้วยเหตุที่",
        "ตามที่",
        "ทุกคน",
        "ทุกสิ่ง",
        "ทุกอย่าง",
        "นะคะ",
        "นะจ๊ะ",
        "ผู้ใด",
        "พวกคุณ",
        "หรือยัง",
        "หรือเปล่า",
        "เพื่อว่า",
        "เพื่อให้",
        "แห่งใด",
        "แห่งไหน",
        "โอเค",
    }
)
