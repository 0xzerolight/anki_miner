"""A ko reading load splits on the ASCII period; ja and the default do not."""

from anki_miner.languages.registry import get_profile
from anki_miner.models.reading import ReadingSourceRef
from anki_miner.services.reading import detector

KO_TEXT = "안녕하세요. 반갑습니다."


def _units(rules):
    ref = ReadingSourceRef(kind="text", text=KO_TEXT)
    return [unit.text for unit in detector.load(ref, rules=rules).units]


def test_korean_rules_reach_the_text_source():
    assert _units(get_profile("ko").sentence_rules) == ["안녕하세요.", "반갑습니다."]


def test_the_default_and_the_ja_profile_agree():
    assert _units(None) == _units(get_profile("ja").sentence_rules) == [KO_TEXT]
