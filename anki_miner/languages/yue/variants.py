"""Hong Kong character-variant pairs for the yue lookup ladder.

Seven single-character pairs whose two spellings mean the same word, applied
BIDIRECTIONALLY because the two catalogue dictionaries disagree about which
member they key. Measured over their real term banks (2026-09-20,
tests/fixtures/yue/hk_variants.jsonl): 裏面 is a headword in CC-Canto and not in
CC-CEDICT-Canto, 裡面 the other way round; the same split holds for 衞生/衛生
and 山峯/山峰. Across whole dictionaries CC-CEDICT-Canto keys almost entirely on
the non-HK member (裡 89 / 裏 5; 爲, 啓, 衞, 羣, 峯 zero against 為 258, 啟 46,
衛 138, 群 234, 峰 128) while CC-Canto holds both, so the rung matters most for
HK-spelled subtitles queried against CC-CEDICT-Canto.

The maps are whole-word character translations, so one candidate per direction:
a word mixing both conventions still resolves in one step.
"""

from __future__ import annotations

#: ``(Hong Kong spelling, standard spelling)``. First-party table.
HK_VARIANT_PAIRS: tuple[tuple[str, str], ...] = (
    ("裏", "裡"),
    ("着", "著"),
    ("爲", "為"),
    ("啓", "啟"),
    ("衞", "衛"),
    ("羣", "群"),
    ("峯", "峰"),
)

_TO_STANDARD = str.maketrans(dict(HK_VARIANT_PAIRS))
_TO_HONG_KONG = str.maketrans({standard: hk for hk, standard in HK_VARIANT_PAIRS})


def hk_variant_candidates(word: str) -> list[str]:
    """Both variant spellings of ``word``, minus ``word`` itself."""
    candidates = (word.translate(_TO_STANDARD), word.translate(_TO_HONG_KONG))
    return [candidate for candidate in dict.fromkeys(candidates) if candidate != word]
