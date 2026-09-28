"""S10: the subtitle regex trio follows the mining language."""

from __future__ import annotations

import dataclasses

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import LANGUAGE_SCOPED_FIELDS, switch_language
from anki_miner.services.subtitle_parser import compile_subtitle_regex_filter

TRIO = ("use_subtitle_regex_filter", "subtitle_regex_filter", "subtitle_regex_replacement")
BRACKETS = r"\[[^\]]*\]"


def _trio(config: AnkiMinerConfig) -> tuple[object, ...]:
    return tuple(getattr(config, name) for name in TRIO)


def test_the_trio_is_scoped():
    assert set(TRIO) <= set(LANGUAGE_SCOPED_FIELDS)


def test_ja_and_zh_first_visit_values_equal_the_dataclass_defaults():
    blank = _trio(AnkiMinerConfig())
    for code in ("ja", "zh"):
        assert tuple(get_profile(code).scoped_defaults[name] for name in TRIO) == blank


#: SDH cues as Korean and Thai CC tracks write them, and what the first-visit filter leaves.
SDH_CUES = {
    "ko": [
        ("[문 닫히는 소리]", ""),
        ("[웃음] 진짜 웃기다.", "진짜 웃기다."),
        ("그래 (한숨) 알았어.", "그래 알았어."),
        ("♪ 너를 사랑해 ♪", "너를 사랑해"),
        ("- 뭐야? - 몰라.", "뭐야? 몰라."),
    ],
    "th": [
        ("[เสียงดนตรี]", ""),
        ("[ถอนหายใจ] ไม่เป็นไร", "ไม่เป็นไร"),
        ("(หัวเราะ)", ""),
        ("♪ ดอกไม้บานในหัวใจ ♪", "ดอกไม้บานในหัวใจ"),
        ("- ไปไหน - ไม่รู้", "ไปไหน ไม่รู้"),
    ],
}


@pytest.mark.parametrize(
    ("code", "cue", "expected"), [(code, cue, expected) for code, cues in SDH_CUES.items() for cue, expected in cues]
)
def test_ko_and_th_first_visits_turn_the_sdh_filter_on(code, cue, expected):
    use, pattern, replacement = (get_profile(code).scoped_defaults[name] for name in TRIO)
    assert use is True and replacement == ""
    assert isinstance(pattern, str)
    assert " ".join(compile_subtitle_regex_filter(pattern, "").sub("", cue).split()) == expected


def test_ja_values_park_on_a_switch_and_come_back():
    ja = dataclasses.replace(AnkiMinerConfig(), use_subtitle_regex_filter=True, subtitle_regex_filter=BRACKETS)

    ko = switch_language(ja, "ko")
    assert _trio(ko) == tuple(get_profile("ko").scoped_defaults[name] for name in TRIO) != _trio(ja)
    assert _trio(switch_language(ko, "ja")) == (True, BRACKETS, "")


def _pre_stage_snapshot() -> dict[str, object]:
    """A snapshot parked before the trio was scoped: every scoped name but the trio."""
    return {name: getattr(AnkiMinerConfig(), name) for name in LANGUAGE_SCOPED_FIELDS if name not in TRIO}


def test_the_first_scoped_switch_gives_every_old_snapshot_the_shared_value():
    """D4: the trio was global, so at the first switch after it became scoped the
    live value is what EVERY parked language last used."""
    on_ko = AnkiMinerConfig(
        language="ko",
        language_stash={"ja": _pre_stage_snapshot(), "zh": _pre_stage_snapshot()},
        use_subtitle_regex_filter=True,
        subtitle_regex_filter=BRACKETS,
    )

    on_ja = switch_language(on_ko, "ja")
    assert _trio(on_ja) == (True, BRACKETS, "")

    # A later, language-specific change on ja must not reach zh: zh's old
    # snapshot was completed with the shared value at the first switch.
    on_zh = switch_language(dataclasses.replace(on_ja, subtitle_regex_filter=r"\(.*?\)"), "zh")
    assert _trio(on_zh) == (True, BRACKETS, "")
    assert _trio(switch_language(on_zh, "ko")) == (True, BRACKETS, "")


def test_after_the_first_scoped_switch_a_missing_key_takes_the_profile_default():
    """The test_switching.py contract: an absent key never takes the outgoing language's live value."""
    scoped_ja = {
        **_pre_stage_snapshot(),
        "use_subtitle_regex_filter": True,
        "subtitle_regex_filter": BRACKETS,
        "subtitle_regex_replacement": "",
    }
    on_ko = AnkiMinerConfig(
        language="ko",
        language_stash={"ja": scoped_ja, "zh": _pre_stage_snapshot()},
        use_subtitle_regex_filter=True,
        subtitle_regex_filter=r"\(.*?\)",
    )

    assert _trio(switch_language(on_ko, "zh")) == (False, "", "")


def test_a_first_visit_never_carries_the_live_value():
    on_ja = dataclasses.replace(AnkiMinerConfig(), use_subtitle_regex_filter=True, subtitle_regex_filter=BRACKETS)
    assert _trio(switch_language(on_ja, "zh")) == (False, "", "")
