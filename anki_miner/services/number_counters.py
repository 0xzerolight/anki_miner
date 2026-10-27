"""Number + counter cards (3人 read よにん), for Settings → Word Filters → Mine numbers with counters.

unidic splits 3人 into the numeral 3 and the counter 人, and the parser drops the
numeral (数詞 is an excluded subtype), so the counter alone, or nothing, reaches a
card. With the setting on, ``merge_number_counters`` joins a numeral and the
counter after it into one token whose front is the line's own spelling (4人,
一個, ３本) and whose reading takes the counter's sound changes (よにん,
いっこ, さんぼん), which no per-token kana can give. A counter outside the
table, or a number the table cannot read (a decimal, a 1,000 with a comma, an
ordinal 第3回), leaves the line as it was.

The card's definition is the counter's own entry: ``counter_of`` names it for
the dictionary lookup ladder, as no dictionary lists 4人 itself.
"""

from __future__ import annotations

from typing import NamedTuple

from anki_miner.services.morphology import SyntheticToken, iter_token_spans
from anki_miner.utils.text_utils import hiragana_to_katakana

_DIGITS = {**{str(d): d for d in range(10)}, **{chr(0xFF10 + d): d for d in range(10)}}
_KANJI_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_KANJI_UNITS = {"十": 10, "百": 100, "千": 1000}
_KANJI_BIG = {"万": 10_000}
_MAX_NUMBER = 100_000_000
_DIGIT_SEPARATORS = frozenset({",", "，", ".", "．"})

_ONES = {1: "いち", 2: "に", 3: "さん", 4: "よん", 5: "ご", 6: "ろく", 7: "なな", 8: "はち", 9: "きゅう"}
_HUNDREDS = {
    1: "ひゃく",
    2: "にひゃく",
    3: "さんびゃく",
    4: "よんひゃく",
    5: "ごひゃく",
    6: "ろっぴゃく",
    7: "ななひゃく",
    8: "はっぴゃく",
    9: "きゅうひゃく",
}
_THOUSANDS = {
    1: "せん",
    2: "にせん",
    3: "さんぜん",
    4: "よんせん",
    5: "ごせん",
    6: "ろくせん",
    7: "ななせん",
    8: "はっせん",
    9: "きゅうせん",
}

# The kana a counter's first sound becomes after a geminated number (いっ+ほん →
# いっぽん) and after ん (さん+ほん → さんぼん).
_TO_P = str.maketrans("はひふへほ", "ぱぴぷぺぽ")
_TO_B = str.maketrans("はひふへほ", "ばびぶべぼ")
_TO_G = str.maketrans("かきくけこ", "がぎぐげご")


class _Counter(NamedTuple):
    reading: str
    #: Number endings that geminate before the counter (いち → いっ).
    geminate: frozenset[str] = frozenset()
    #: Sound the counter's first kana takes after a geminated number.
    after_geminate: dict[int, int] | None = None
    #: Endings after which the counter's first kana is voiced or half-voiced.
    after_n: dict[str, dict[int, int]] | None = None
    #: Ending replacements before the counter (よん → よ: よにん).
    endings: dict[str, str] | None = None
    #: Whole numbers with their own reading (1人 ひとり, 3日 みっか).
    exact: dict[int, str] | None = None
    #: The largest number the counter takes (a month, つ), None for any.
    limit: int | None = None


_GEMINATE_HK = frozenset({"いち", "ろく", "はち", "じゅう", "ひゃく"})
_GEMINATE_ST = frozenset({"いち", "はち", "じゅう"})
_H_AFTER_N = {"さん": _TO_B, "せん": _TO_B, "まん": _TO_B}

_COUNTERS: dict[str, _Counter] = {
    "本": _Counter("ほん", _GEMINATE_HK, _TO_P, _H_AFTER_N),
    "匹": _Counter("ひき", _GEMINATE_HK, _TO_P, _H_AFTER_N),
    "杯": _Counter("はい", _GEMINATE_HK, _TO_P, _H_AFTER_N),
    "分": _Counter("ふん", _GEMINATE_HK, _TO_P, {"さん": _TO_P, "よん": _TO_P, "せん": _TO_B, "まん": _TO_B}),
    "個": _Counter("こ", _GEMINATE_HK),
    "回": _Counter("かい", _GEMINATE_HK),
    "件": _Counter("けん", _GEMINATE_HK),
    "階": _Counter("かい", _GEMINATE_HK, None, {"さん": _TO_G}),
    "軒": _Counter("けん", _GEMINATE_HK, None, {"さん": _TO_G}),
    "か月": _Counter("かげつ", _GEMINATE_HK),
    "ヶ月": _Counter("かげつ", _GEMINATE_HK),
    "カ月": _Counter("かげつ", _GEMINATE_HK),
    "ヵ月": _Counter("かげつ", _GEMINATE_HK),
    "歳": _Counter("さい", _GEMINATE_ST, exact={20: "はたち"}),
    "才": _Counter("さい", _GEMINATE_ST, exact={20: "はたち"}),
    "冊": _Counter("さつ", _GEMINATE_ST),
    "週間": _Counter("しゅうかん", _GEMINATE_ST),
    "点": _Counter("てん", _GEMINATE_ST),
    "人": _Counter("にん", endings={"よん": "よ"}, exact={1: "ひとり", 2: "ふたり"}),
    "名": _Counter("めい"),
    "年": _Counter("ねん", endings={"よん": "よ"}),
    "年間": _Counter("ねんかん", endings={"よん": "よ"}),
    "円": _Counter("えん", endings={"よん": "よ"}),
    "時": _Counter("じ", endings={"よん": "よ", "なな": "しち", "きゅう": "く"}),
    "時間": _Counter("じかん", endings={"よん": "よ", "きゅう": "く"}),
    "月": _Counter("がつ", endings={"よん": "し", "なな": "しち", "きゅう": "く"}, limit=12),
    "日": _Counter(
        "にち",
        endings={"なな": "しち", "きゅう": "く"},
        exact={
            1: "いちにち",
            2: "ふつか",
            3: "みっか",
            4: "よっか",
            5: "いつか",
            6: "むいか",
            7: "なのか",
            8: "ようか",
            9: "ここのか",
            10: "とおか",
            14: "じゅうよっか",
            20: "はつか",
            24: "にじゅうよっか",
        },
    ),
    "枚": _Counter("まい"),
    "台": _Counter("だい"),
    "度": _Counter("ど"),
    "番": _Counter("ばん"),
    "秒": _Counter("びょう"),
    "倍": _Counter("ばい"),
    "号": _Counter("ごう"),
    "つ": _Counter(
        "",
        exact={
            1: "ひとつ",
            2: "ふたつ",
            3: "みっつ",
            4: "よっつ",
            5: "いつつ",
            6: "むっつ",
            7: "ななつ",
            8: "やっつ",
            9: "ここのつ",
            10: "とお",
        },
        limit=10,
    ),
}

#: unidic tags most counters 助数詞 or 助数詞可能; 人, 冊 and 軒 are 一般.
_COUNTER_POS3 = frozenset({"助数詞", "助数詞可能", "一般"})


def parse_number(text: str) -> int | None:
    """The value of a numeral written in digits (ASCII or full-width) or in kanji, else None."""
    if not text:
        return None
    if all(ch in _DIGITS for ch in text):
        return int("".join(str(_DIGITS[ch]) for ch in text))
    # Kanji, or digits with kanji units (10万, 3千): a digit run reads as one number.
    total = section = digit = 0
    seen = False
    for ch in text:
        value = _DIGITS.get(ch, _KANJI_DIGITS.get(ch))
        if value is not None:
            digit = digit * 10 + value if seen and digit else value
            seen = True
        elif ch in _KANJI_UNITS:
            section += (digit or 1) * _KANJI_UNITS[ch]
            digit, seen = 0, False
        elif ch in _KANJI_BIG:
            total += (section + digit or 1) * _KANJI_BIG[ch]
            section = digit = 0
            seen = False
        else:
            return None
    return total + section + digit


def _below_ten_thousand(n: int) -> list[tuple[int, str]]:
    """Reading parts of 1..9999 as (place, kana), largest place first, zero places left out."""
    parts = []
    for place, table in ((1000, _THOUSANDS), (100, _HUNDREDS)):
        if n // place:
            parts.append((place, table[n // place]))
        n %= place
    if n // 10:
        parts.append((10, ("" if n // 10 == 1 else _ONES[n // 10]) + "じゅう"))
    if n % 10:
        parts.append((1, _ONES[n % 10]))
    return parts


def _number_parts(n: int) -> list[tuple[int, str]]:
    """Reading parts of 1..99,999,999: the 万 part first, then the rest."""
    parts = []
    if n // 10_000:
        parts.append((10_000, "".join(kana for _, kana in _below_ten_thousand(n // 10_000)) + "まん"))
    return parts + _below_ten_thousand(n % 10_000)


def _ending(place: int, kana: str) -> str:
    """The word a reading part ends in, which decides the counter's sound change."""
    if place == 1:
        return kana
    return {10: "じゅう", 100: "ひゃく", 1000: "せん", 10_000: "まん"}[place]


def counter_reading(number: int, counter: str) -> str | None:
    """Hiragana reading of ``number`` + ``counter`` with its sound changes, or None outside the table."""
    rule = _COUNTERS.get(counter)
    if rule is None or not 0 < number < _MAX_NUMBER:
        return None
    if rule.exact and number in rule.exact:
        return rule.exact[number]
    if rule.limit is not None and number > rule.limit:
        return None
    parts = _number_parts(number)
    head = "".join(kana for _, kana in parts[:-1])
    place, last = parts[-1]
    ending = _ending(place, last)
    first, rest = rule.reading[:1], rule.reading[1:]
    if ending in rule.geminate:
        # いち → いっ, にじゅう → にじゅっ, さんびゃく → さんびゃっ.
        stem = last[:-1] + "っ"
        return head + stem + (first.translate(rule.after_geminate) if rule.after_geminate else first) + rest
    if rule.after_n and ending in rule.after_n:
        return head + last + first.translate(rule.after_n[ending]) + rest
    if rule.endings and place == 1 and ending in rule.endings:
        return head + rule.endings[ending] + rule.reading
    return head + last + rule.reading


def counter_of(front: str) -> str | None:
    """The counter a number + counter card front ends in (4人 → 人), for its definition, else None."""
    for counter in sorted(_COUNTERS, key=len, reverse=True):
        if front.endswith(counter) and parse_number(front[: -len(counter)]) is not None:
            return counter
    return None


def counter_entry(front: str) -> tuple[str, str] | None:
    """The counter of a number + counter front with the counter's own reading (4人 → 人, にん), else None.

    The reading picks the counter's entry among its homographs (人 is also ひと
    'person' and じん '-ian').
    """
    counter = counter_of(front)
    if counter is None:
        return None
    return counter, _COUNTERS[counter].reading or counter


def _is_numeral(token) -> bool:
    feature = getattr(token, "feature", None)
    return getattr(feature, "pos1", None) == "名詞" and getattr(feature, "pos2", None) == "数詞"


def _is_counter(token) -> bool:
    feature = getattr(token, "feature", None)
    return (
        token.surface in _COUNTERS
        and getattr(feature, "pos1", None) in ("名詞", "接尾辞")
        and getattr(feature, "pos3", None) in _COUNTER_POS3
    )


def merge_number_counters(text: str, tokens: list) -> list:
    """Join each numeral and the counter right after it into one number + counter token.

    Runs after the merge passes and the compound matcher: a longer attested
    word keeps its number (一生一度), and a pair the suffix pass already merged
    (一個, which keeps the numeral's 数詞 tag) is taken as it stands. The
    numeral may span several tokens (三百|五十, 10|万), each piece next to the
    last in the line, and a merge never starts inside one. The reading must be
    one the table gives, and a fraction (3分の1) is left alone. When the matcher
    took the number's tail with its counter (3|千円, 10|万人), the pieces join
    across it. The merged token
    carries its reading locked, so the dictionary pass keeps the counter's sound
    changes, and ``number_counter`` on its feature, which the parser's
    acceptance reads. A numeral right after 第 or after a digit separator
    (1,|500, 1.|5) continues a number and starts no merge.
    """
    spans: dict[int, tuple[int, int]] = {}
    index = 0
    for located, start, end in iter_token_spans(text, tokens):
        while index < len(tokens) and tokens[index] is not located:
            index += 1
        if index < len(tokens):
            spans[index] = (start, end)
            index += 1

    def touching(i: int) -> bool:
        return i in spans and i + 1 in spans and spans[i][1] == spans[i + 1][0]

    def fraction(counter: str, after: int) -> bool:
        """3分の1 is a third, not three minutes."""
        return counter == "分" and after < len(tokens) and tokens[after].surface == "の"

    def inside_number(i: int) -> bool:
        """Numeral i continues a number: 三百|五十, 1,|500 or 1.|5 split at the separator, 第|3回."""
        if i == 0 or not touching(i - 1):
            return False
        before = tokens[i - 1]
        if _is_numeral(before) or before.surface == "第":
            return True
        return before.surface in _DIGIT_SEPARATORS and i > 1 and _is_numeral(tokens[i - 2]) and touching(i - 2)

    out: list = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not _is_numeral(token) or inside_number(i):
            out.append(token)
            i += 1
            continue
        end = i + 1
        while end < len(tokens) and _is_numeral(tokens[end]) and touching(end - 1):
            end += 1
        numeral = "".join(t.surface for t in tokens[i:end])
        counter: str | None
        if end < len(tokens) and touching(end - 1) and _is_counter(tokens[end]):
            counter, consumed = tokens[end].surface, end + 1
        elif end < len(tokens) and touching(end - 1) and (counter := counter_of(tokens[end].surface)) is not None:
            # The matcher took the number's tail with its counter (3|千円, 10|万人).
            numeral += tokens[end].surface[: -len(counter)]
            consumed = end + 1
        else:
            # A pair the suffix pass merged keeps the numeral's tag (一個, 10|万人).
            counter = counter_of(numeral)
            if counter is not None:
                numeral = numeral[: -len(counter)]
            consumed = end
        number = parse_number(numeral) if counter is not None else None
        reading = counter_reading(number, counter) if number is not None and counter is not None else None
        if reading is None or counter is None or fraction(counter, consumed):
            out.extend(tokens[i:end])
            i = end
            continue
        merged = SyntheticToken(
            surface=numeral + counter,
            pos1="名詞",
            pos2="数詞",
            lemma=numeral + counter,
            kana=hiragana_to_katakana(reading),
            kana_locked=True,
        )
        merged.feature.number_counter = True
        # The table reading is attested: the sentence furigana and reading take
        # it too (replace_overridden_spans), not unidic's per-token さん|ぽん.
        merged.feature.kana_attested = True
        out.append(merged)
        i = consumed
    return out
