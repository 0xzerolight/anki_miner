"""Duck-shape and span-location invariants the non-ja tokenizers depend on.

``LanguageToken`` is only useful if ``services/morphology.py`` treats it the way
the zh/ko parsers will need: never swept into the ja-only merge passes, always
locatable by ``iter_token_spans``, and probe-safe for the unidic-only feature
attributes. These pin that contract before Stage 2 writes against it.
"""

from __future__ import annotations

from anki_miner.languages.token import LanguageToken
from anki_miner.services.morphology import iter_token_spans


def test_is_not_a_synthetic_token():
    """morphology's isinstance gates are ja-only merge passes."""
    from anki_miner.services.morphology import SyntheticToken

    assert not isinstance(LanguageToken("가", "NNG"), SyntheticToken)


def test_iter_token_spans_locates_every_surface():
    text = "학생 이 공원 에서 달린다"
    tokens = [LanguageToken(s, "NNG") for s in ["학생", "이", "공원", "에서", "달린다"]]
    spans = list(iter_token_spans(text, tokens))
    assert len(spans) == len(tokens)
    assert [text[a:b] for _, a, b in spans] == [t.surface for t in tokens]


def test_absent_attributes_probe_to_defaults():
    t = LanguageToken("重", "n", pos2="nz", lemma="重")
    for name in (
        "orthBase",
        "pron",
        "cType",
        "cForm",
        "lForm",
        "kanaBase",
        "kana_attested",
        "kana_locked",
        "kana_special",
        "kana_overridden",
    ):
        assert getattr(t.feature, name, None) is None


def test_normalized_surface_would_be_dropped():
    """Guard rail: a re-spelled surface is unlocatable and silently lost."""
    assert list(iter_token_spans("学生", [LanguageToken("學生", "n")])) == []
