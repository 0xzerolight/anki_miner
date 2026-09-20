"""Statistical probe for a frequency source's *direction* (rank- vs occurrence-based).

Rank-based lists (JPDB, BCCWJ) number the *most* frequent word ``1`` — smaller is
more common. Occurrence-based lists store a raw count — *larger* is more common,
so filtering/sorting a card by ``max_frequency_rank`` silently inverts unless the
list is first re-ranked. Yomitan zips can *declare* ``frequencyMode``; plain CSVs
and undeclared zips cannot, so we fall back to the statistical probe below.

The heuristic is a paired-sign vote over two curated 10-term Japanese lists
(known-common vs known-rare). For every common/rare pair that both carry a value
it accumulates ``sign(common.max - rare.min) + sign(common.min - rare.max)``. A
positive total means the common terms hold the *larger* numbers (larger = more
frequent → occurrence-based / ``descending``); negative means the *smaller*
numbers (rank-based / ``ascending``); zero (e.g. no probe terms present) is
ambiguous → ``None``. The pairing makes it robust to partial coverage: a source
that only contains a few of the probe terms still votes with whatever it has.

Ported from Yomitan
``ext/js/pages/settings/sort-frequency-dictionary-controller.js``
(``SortFrequencyDictionaryController._getFrequencyOrder``, lines 154-236) at
upstream commit ``e2ed450``. Term lists and the paired-sign accumulation are
verbatim; the DB round-trip is replaced by a caller-supplied ``lookup``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence

from anki_miner.services.frequency.storage import FreqRow

# Declared ``frequencyMode`` values (Yomitan ``index.json``).
OCCURRENCE_BASED = "occurrence-based"
RANK_BASED = "rank-based"

# Probe verdicts (Yomitan sort orders): larger value = more common (occurrence)
# vs smaller value = more common (rank).
DESCENDING = "descending"
ASCENDING = "ascending"

# Curated probe term lists, keyed by source language. Verbatim from Yomitan's
# _getFrequencyOrder (moreCommonTerms / lessCommonTerms).
MORE_COMMON_TERMS: dict[str, list[str]] = {
    "ja": ["来る", "言う", "出る", "入る", "方", "男", "女", "今", "何", "時"],
    # Both script variants are listed in one table: a SUBTLEX-CH port is
    # simplified and a Sinica-style port is traditional, and a term the source
    # does not carry never votes (_min_max reports has_value False). One table
    # therefore serves both without a variant flag on the source.
    "zh": ["的", "是", "不", "我", "有", "人", "说", "說", "来", "來", "时候", "時候", "什么", "什麼", "知道"],
    # ko: NIKL 현대 국어 사용 빈도 조사 2 headwords, lemma+다 granularity (the
    # granularity languages/ko/morphology.py mines). Every term is in the top 31
    # of that survey once its homograph indices are merged.
    "ko": ["하다", "있다", "되다", "없다", "같다", "보다", "사람", "우리", "일", "말"],
    # Western 8 (Stage S, S16): lowercase surface forms as hermitdave/FrequencyWords'
    # OpenSubtitles 2018 *_50k.txt lists spell them (the default catalogue source,
    # imported before lemmatisation). Common terms sit in each list's top 25.
    # pt terms occur in both the pt and pt_br lists.
    "en": ["you", "i", "the", "to", "a", "it", "and", "that", "of", "is"],
    "de": ["ich", "sie", "das", "ist", "du", "nicht", "die", "es", "und", "der"],
    "fr": ["de", "je", "est", "pas", "le", "que", "la", "vous", "tu", "un"],
    "es": ["de", "que", "no", "a", "la", "el", "y", "es", "en", "lo"],
    "it": ["e", "non", "che", "di", "la", "il", "un", "a", "per", "è"],
    "pt": ["que", "o", "não", "de", "a", "é", "e", "um", "para", "eu"],
    "nl": ["ik", "je", "het", "de", "dat", "is", "een", "niet", "en", "van"],
    "ca": ["que", "no", "de", "la", "el", "a", "i", "és", "un", "per"],
    # nb: hermitdave's list lives in content/2018/no/ (R27) but the probe keys on the profile code.
    "nb": ["jeg", "det", "er", "du", "ikke", "en", "og", "i", "har", "vi"],
    # ro: the list spells most s/t words with the legacy cedilla and the probe matches raw terms,
    # so the common terms are the top of ro_50k.txt without an s/t letter.
    "ro": ["de", "nu", "să", "o", "în", "ce", "e", "că", "la", "a"],
    # el: the top ten of hermitdave's OpenSubtitles 2018 el_50k.txt, lowercase surface forms.
    "el": ["να", "το", "δεν", "είναι", "θα", "και", "μου", "με", "για", "την"],
    # fi: fi_50k.txt ranks 1-10 (lowercase surfaces, before lemmatisation).
    "fi": ["on", "ei", "ja", "se", "hän", "en", "mitä", "että", "ole", "olen"],
    # hu: the top 10 of hermitdave/FrequencyWords OpenSubtitles 2018 hu_50k.txt,
    # the default catalogue source, which is imported before lemmatisation.
    "hu": ["a", "nem", "az", "hogy", "és", "egy", "van", "ez", "is", "de"],
    # hr: the top ten of hermitdave's OpenSubtitles 2018 hr_50k.txt. The probe votes on raw surface forms,
    # before the catalogue row's lemmatise=True aggregates them.
    "hr": ["je", "da", "ne", "se", "i", "u", "to", "sam", "što", "na"],
    # sv: the top ten of hermitdave's OpenSubtitles 2018 sv_50k.txt, lowercase surface forms.
    "sv": ["jag", "det", "är", "du", "att", "inte", "en", "och", "har", "vi"],
    # pl: hermitdave pl_50k.txt ranks 1-10.
    "pl": ["nie", "to", "się", "w", "na", "i", "że", "z", "co", "jest"],
    # lt: the top ten of hermitdave's OpenSubtitles 2018 lt_50k.txt, lowercase surface forms.
    "lt": ["ir", "aš", "tai", "kad", "tu", "ne", "taip", "jis", "ką", "čia"],
    # da: da_50k.txt ranks 1-6 and 8-11 (lowercase surfaces, before lemmatisation).
    "da": ["jeg", "det", "er", "du", "ikke", "at", "en", "og", "har", "vi"],
    # tr: the top 10 of hermitdave/FrequencyWords OpenSubtitles 2018 tr_50k.txt,
    # the default catalogue source, which is imported before lemmatisation.
    "tr": ["bir", "bu", "ne", "ve", "için", "mi", "de", "o", "ben", "çok"],
    # id: hermitdave's OpenSubtitles 2018 id_50k.txt ranks 1-10 (lowercase surfaces).
    "id": ["aku", "kau", "yang", "tidak", "ini", "itu", "dan", "dia", "di", "akan"],
    # ru: hermitdave ru_50k.txt ranks 1-10.
    "ru": ["я", "не", "что", "в", "и", "ты", "это", "на", "с", "он"],
    # ar: hermitdave ar_50k.txt ranks 2-11 (rank 1 is the Arabic comma), raw surfaces before
    # lemmatisation. Escaped: a raw RTL run reorders on screen inside a source line.
    # laa, min, fii, an, haadhaa, 'alaa, maa, anaa, hal, wa
    "ar": [
        "\u0644\u0627",
        "\u0645\u0646",
        "\u0641\u064a",
        "\u0623\u0646",
        "\u0647\u0630\u0627",
        "\u0639\u0644\u0649",
        "\u0645\u0627",
        "\u0623\u0646\u0627",
        "\u0647\u0644",
        "\u0648",
    ],
    # th: Thai National Corpus ranks 1-10, verified against pythainlp.corpus.tnc
    # .word_freqs(). Thai is not on hermitdave, so the TNC list converted by
    # scripts/convert_tnc_thai_frequency.py is the catalogue source.
    "th": ["ที่", "การ", "เป็น", "ใน", "ของ", "มี", "จะ", "และ", "ไม่", "ได้"],
    # fa: fa_50k ranks 2-115 (va, dar, be, az, ke, in, ra, ba, ast, baraye).
    # Right-to-left data in a left-to-right list: the ranks are the check.
    "fa": ["و", "در", "به", "از", "که", "این", "را", "با", "است", "برای"],
    # sl: the top ten of hermitdave's OpenSubtitles 2018 sl_50k.txt. The probe votes on raw surface
    # forms, before the catalogue row's lemmatise=True aggregates them.
    "sl": ["je", "ne", "da", "se", "v", "sem", "to", "in", "si", "kaj"],
    # uk: hermitdave uk_50k.txt ranks 1-10. The list carries Russian rows from mixed subtitle
    # files (что, ты), which is what it actually contains and therefore what the probe votes with.
    "uk": ["я", "не", "в", "що", "на", "це", "ти", "что", "так", "у"],
}
LESS_COMMON_TERMS: dict[str, list[str]] = {
    "ja": ["行なう", "論じる", "過す", "行方", "人口", "猫", "犬", "滝", "理", "暁"],
    "zh": [
        "忐忑",
        "熠熠",
        "缱绻",
        "繾綣",
        "龃龉",
        "齟齬",
        "蹉跎",
        "阑珊",
        "闌珊",
        "斑驳",
        "斑駁",
        "踌躇",
        "躊躇",
        "惆怅",
        "惆悵",
    ],
    # ko: rare-but-real headwords, all present in that same survey with counts
    # of 3-23 against the common table's 9,225-76,984. 물레 and 갈무리 were
    # dropped from an earlier draft of this list: the survey carries only
    # 물레방아 and 갈무리하다, so neither term could ever have voted.
    "ko": ["노새", "자맥질", "여울", "두레박", "삿갓", "옹기", "맷돌", "나룻배", "멍석", "미나리"],
    # Western 8: rare-but-real nouns present in the same lists at ranks 7,700-50,000
    # (only pt_br puts two below 9,000: farol 7,706, esquilo 8,654).
    "en": [
        "hedgehog",
        "lighthouse",
        "thimble",
        "walrus",
        "anvil",
        "kiln",
        "sundial",
        "chisel",
        "beehive",
        "wheelbarrow",
    ],
    "de": [
        "igel",
        "leuchtturm",
        "fingerhut",
        "walross",
        "amboss",
        "laterne",
        "meißel",
        "dachs",
        "fernrohr",
        "schubkarre",
    ],
    "fr": ["hérisson", "phare", "morse", "enclume", "cadran", "lanterne", "ciseau", "blaireau", "brouette", "canoë"],
    "es": ["erizo", "faro", "dedal", "morsa", "yunque", "cincel", "tejón", "colmena", "carretilla", "canoa"],
    "it": [
        "riccio",
        "tricheco",
        "incudine",
        "lanterna",
        "scalpello",
        "telescopio",
        "alveare",
        "carriola",
        "canoa",
        "bussola",
    ],
    "pt": ["ouriço", "farol", "morsa", "bigorna", "texugo", "telescópio", "bússola", "colmeia", "canoa", "esquilo"],
    "nl": [
        "egel",
        "vuurtoren",
        "walrus",
        "aambeeld",
        "lantaarn",
        "beitel",
        "bijenkorf",
        "kruiwagen",
        "kano",
        "telescoop",
    ],
    "ca": ["didal", "enclusa", "llanterna", "teixó", "telescopi", "rusc", "sella", "canoa", "carretó", "esquirol"],
    "nb": [
        "pinnsvin",
        "fyrtårn",
        "hvalross",
        "lanterne",
        "meisel",
        "grevling",
        "kikkert",
        "bikube",
        "sparegris",
        "strykejern",
    ],
    "ro": ["arici", "felinar", "bursuc", "nicovală", "morsă", "roabă", "stup", "telescop", "canoe", "degetar"],
    "el": [
        "φανάρι",
        "πυξίδα",
        "μέλισσα",
        "λαγουδάκι",
        "τηλεσκόπιο",
        "κυψέλη",
        "κανό",
        "καλούπι",
        "καζάνι",
        "δρεπάνι",
    ],
    # fi: rare-but-real nouns at fi_50k ranks 20,133-41,138.
    "fi": ["majakka", "lyhty", "kompassi", "ankkuri", "lapio", "pöllö", "harppu", "kattila", "muurahainen", "kehto"],
    # hu: rare-but-real nouns present in that same list at ranks 12,858-48,944
    # (agglutination spreads a lemma over dozens of rows, so a rare noun still
    # has a row of its own).
    "hu": [
        "mókus",
        "bagoly",
        "pillangó",
        "denevér",
        "vödör",
        "kandalló",
        "sündisznó",
        "iránytű",
        "világítótorony",
        "seprű",
    ],
    # hr: concrete nouns that are in hr_50k.txt (ranks 15,130-47,081), so each one really votes.
    "hr": [
        "teleskop",
        "sidro",
        "svjetionik",
        "kanu",
        "košnica",
        "češalj",
        "lopata",
        "jazavac",
        "dvogled",
        "morž",
    ],
    # sv: rare-but-real nouns at ranks 14,000-45,500 in that same list. "städ" (anvil) is not
    # among them: it is also the imperative of "städa", so it would vote as a common word.
    "sv": [
        "kikare",
        "kastrull",
        "grävling",
        "lykta",
        "valross",
        "bikupa",
        "igelkott",
        "skottkärra",
        "strykjärn",
        "spargris",
    ],
    # pl: rare-but-real nouns at pl_50k ranks 13,588-47,397 (wiadro ... ul).
    "pl": ["wiadro", "młotek", "kompas", "wagon", "teleskop", "latarnia", "wiewiórka", "latarka", "kufel", "ul"],
    # lt: concrete nouns at lt_50k.txt ranks 16,797-41,353 (the rank is the second field's line number).
    "lt": [
        "žibintas",
        "barsukas",
        "teleskopas",
        "avilys",
        "kibiras",
        "šluota",
        "malūnas",
        "inkaras",
        "plaktukas",
        "sraigė",
    ],
    # da: rare-but-real nouns at da_50k ranks 16,139-49,304.
    "da": [
        "kano",
        "fyrtårn",
        "grævling",
        "pindsvin",
        "lanterne",
        "hvalros",
        "trillebør",
        "fingerbøl",
        "mejsel",
        "ambolt",
    ],
    # tr: rare-but-real nouns present in that same list at ranks 11,552-42,003
    # (agglutination spreads a lemma over many rows, the hu case).
    "tr": ["sincap", "baykuş", "kova", "şömine", "kirpi", "pusula", "süpürge", "çapa", "beşik", "sepet"],
    # id: id_50k ranks 3,164-27,336 (the spec C.5 list, verified present).
    "id": [
        "cendekiawan",
        "mercusuar",
        "landak",
        "kunang-kunang",
        "teropong",
        "gerhana",
        "kerajinan",
        "sekutu",
        "perpustakaan",
        "belalang",
    ],
    # ru: rare-but-real nouns at ru_50k ranks 14,055-28,695 (фонарь ... бочка).
    "ru": ["компас", "вагон", "телескоп", "фонарь", "белка", "фонарик", "кружка", "улей", "бочка", "якорь"],
    # ar: present in ar_50k.txt at ranks 16,328-44,701 - fan, swing, hammer, hose, shell, spider,
    # candle, tortoise, umbrella, bat; the spec's microscope/hedgehog/sickle/lantern are absent
    # from that list. Escaped for the same reason as the common table.
    "ar": [
        "\u0645\u0631\u0648\u062d\u0629",
        "\u0623\u0631\u062c\u0648\u062d\u0629",
        "\u0645\u0637\u0631\u0642\u0629",
        "\u062e\u0631\u0637\u0648\u0645",
        "\u0635\u062f\u0641",
        "\u0639\u0646\u0643\u0628\u0648\u062a",
        "\u0634\u0645\u0639\u0629",
        "\u0633\u0644\u062d\u0641\u0627\u0629",
        "\u0645\u0638\u0644\u0629",
        "\u062e\u0641\u0627\u0634",
    ],
    # th: concrete nouns present in that same TNC list at ranks 1,597-10,937.
    "th": ["กังหัน", "ผีเสื้อ", "ตะเกียง", "จักรยาน", "นกพิราบ", "ตั๊กแตน", "บันได", "ค้อน", "ภูเขาไฟ", "กระรอก"],
    # fa: fa_50k ranks 3,079-14,102 (fanus, senjab, qayeq, atashfeshan,
    # docharxe, kabutar, malax, nardeban, chakosh, parvane). The spec's
    # docharxe-savari is absent from fa_50k and carries a ZWNJ the fa fold
    # strips, so the bare noun stands in for it.
    "fa": [
        "فانوس",
        "سنجاب",
        "قایق",
        "آتشفشان",
        "دوچرخه",
        "کبوتر",
        "ملخ",
        "نردبان",
        "چکش",
        "پروانه",
    ],
    # sl: concrete nouns that are in sl_50k.txt (ranks 15,108-42,113), so each one really votes.
    "sl": [
        "sidro",
        "daljnogled",
        "teleskop",
        "svetilnik",
        "panj",
        "glavnik",
        "kanu",
        "lopata",
        "jazbec",
        "mrož",
    ],
    # uk: rare-but-real nouns at uk_50k ranks 17,205-44,521 (телескоп ... білка).
    "uk": [
        "телескоп",
        "вагон",
        "компас",
        "ліхтарик",
        "якір",
        "бочка",
        "цвях",
        "ліхтар",
        "кухоль",
        "білка",
    ],
}


def _sign(value: int) -> int:
    """``Math.sign`` for ints: -1, 0, or 1."""
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _terms_for(table: dict[str, list[str]], source_language: str) -> list[str]:
    """Probe terms for ``source_language``; aggregate all when it is unknown/blank.

    Mirrors Yomitan's ``dictionaryLang === ''`` branch, which pools every
    language's terms when the source declares no language.
    """
    if source_language and source_language in table:
        return list(table[source_language])
    pooled: list[str] = []
    for terms in table.values():
        pooled.extend(terms)
    return pooled


def terms_for_language(table: dict[str, list[str]], source_language: str) -> list[str]:
    """Probe terms belonging to ``source_language`` alone; empty when it has none.

    The strict counterpart of :func:`_terms_for`, and what an *import* must use.
    Pooling every language's terms — what ``_terms_for`` does for an unknown code,
    mirroring Yomitan — would let the Japanese list decide a Korean source's
    direction while ja is the only language with a table. A language with no
    table therefore votes with nothing at all, and the caller lands on
    :func:`resolve_is_occurrence`'s undetermined (rank-based) path.
    """
    return list(table.get(source_language, []))


def _min_max(values: Iterable[int]) -> tuple[bool, int, int]:
    """Return ``(has_value, min, max)`` over ``values`` (has_value False if empty)."""
    has_value = False
    min_value = 0
    max_value = 0
    for v in values:
        if not has_value:
            min_value = max_value = v
            has_value = True
        else:
            if v < min_value:
                min_value = v
            if v > max_value:
                max_value = v
    return has_value, min_value, max_value


def probe_direction(
    lookup: Callable[[str], Sequence[int]],
    source_language: str = "ja",
) -> str | None:
    """Vote on a source's direction from its numeric values.

    Args:
        lookup: Maps a probe term to the numeric values stored for it (empty if
            the term is absent). A term may carry several values (one per
            reading); min/max across them are used.
        source_language: Language of the source; selects the probe term lists.

    Returns:
        :data:`DESCENDING` (occurrence-based), :data:`ASCENDING` (rank-based), or
        ``None`` when the vote is a tie (typically no probe terms present).
    """
    more_terms = _terms_for(MORE_COMMON_TERMS, source_language)
    less_terms = _terms_for(LESS_COMMON_TERMS, source_language)

    more_details = [_min_max(lookup(term)) for term in more_terms]
    less_details = [_min_max(lookup(term)) for term in less_terms]

    result = 0
    for has1, min1, max1 in more_details:
        if not has1:
            continue
        for has2, min2, max2 in less_details:
            if not has2:
                continue
            result += _sign(max1 - min2) + _sign(min1 - max2)

    if result > 0:
        return DESCENDING
    if result < 0:
        return ASCENDING
    return None


def resolve_is_occurrence(
    declared_mode: str,
    term_values: Mapping[str, Sequence[int]],
    source_language: str = "ja",
) -> bool:
    """Decide whether a source is occurrence-based (higher value = more common).

    A declared ``frequencyMode`` is authoritative; the statistical probe runs
    only when the mode is undeclared (blank/unknown).

    Args:
        declared_mode: The source's declared ``frequencyMode`` (``""`` for CSVs
            and undeclared zips).
        term_values: Maps every stored term to its numeric values.
        source_language: Language passed through to :func:`probe_direction`.
    """
    if declared_mode == OCCURRENCE_BASED:
        return True
    if declared_mode == RANK_BASED:
        return False
    return probe_direction(lambda term: term_values.get(term, ()), source_language) == DESCENDING


def convert_to_ranks(rows: Iterable[FreqRow]) -> list[FreqRow]:
    """Re-rank occurrence-based rows: sort by value descending, assign ``1..n``.

    The incoming ``rank`` column holds the raw occurrence value; the largest
    value becomes rank 1. Ties break by ``(term, reading)`` for a deterministic,
    human-scannable order. ``display_value`` (the original figure, e.g. a count)
    is preserved unchanged.
    """
    ordered = sorted(rows, key=lambda r: (-r[2], r[0], r[1] or ""))
    return [(term, reading, new_rank, display) for new_rank, (term, reading, _value, display) in enumerate(ordered, 1)]
