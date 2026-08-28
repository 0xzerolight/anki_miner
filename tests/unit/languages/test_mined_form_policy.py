"""MinedFormPolicy dispatch — the parser emit site and the swap gate.

Task 1A.3. Two halves, and only one of them is observable from Japanese:

* The parser's emit site consults the injected policy and carries the answer
  onto ``TokenizedWord.mined_form_override``. With no policy injected the JA
  static (``models.word.select_mined_form``) runs verbatim, which is what the
  drift canary pins.
* ``WordFilterService._line_preserves_mined_form`` recomputes the candidate
  line's front. It must recompute with the SAME policy that produced the
  override — for ja both sides agree either way, so the ko test below is the
  only proof that half works.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from anki_miner.languages.registry import get_profile
from anki_miner.models import LineLemmas, TokenizedWord
from anki_miner.models.word import select_mined_form
from anki_miner.services.subtitle_parser import SubtitleParserService
from anki_miner.services.word_filter import WordFilterService


class _StubPolicy:
    """A MinedFormPolicy answering one fixed string, recording every call."""

    def __init__(self, answer: str = "X") -> None:
        self.answer = answer
        self.calls: list[tuple[str | None, str, str, str, str | None]] = []

    def mined_form(
        self,
        pos: str | None,
        orth_base: str,
        lemma: str,
        surface: str,
        pronunciation: str | None = None,
    ) -> str:
        self.calls.append((pos, orth_base, lemma, surface, pronunciation))
        return self.answer


def _make_token(surface, pos1, *, pos2=None, lemma=None, kana=None, orth_base=None, pron=""):
    """A fugashi-shaped mock token with every read attribute pinned.

    Same discipline as ``tests/unit/test_subtitle_parser._make_token``: an
    auto-created MagicMock attribute is truthy and would leak into mined_form.
    """
    token = MagicMock()
    token.surface = surface
    token.feature.pos1 = pos1
    token.feature.pos2 = pos2
    token.feature.lemma = lemma if lemma is not None else surface
    token.feature.kana = kana if kana is not None else surface
    token.feature.orthBase = orth_base if orth_base is not None else token.feature.lemma
    token.feature.lForm = None
    token.feature.kanaBase = None
    token.feature.cForm = None
    token.feature.pron = pron
    return token


def _parse_one(config, token, text, tmp_path, *, policy=None):
    """Parse a one-line, one-token subtitle file through the real emit path."""
    sub_file = tmp_path / "test.ass"
    sub_file.write_text("placeholder", encoding="utf-8")

    line = MagicMock()
    line.text = text
    line.start = 1000
    line.end = 3000
    subs = MagicMock()
    subs.__iter__ = MagicMock(return_value=iter([line]))

    tagger = MagicMock()
    tagger.return_value = [token]

    kwargs = {} if policy is None else {"mined_form_policy": policy}
    with (
        patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=subs),
        patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=tagger),
    ):
        service = SubtitleParserService(config, **kwargs)
        return service.parse_subtitle_file(sub_file)


def _word(**overrides) -> TokenizedWord:
    base = {
        "surface": "",
        "lemma": "",
        "reading": "",
        "sentence": "",
        "start_time": 0.0,
        "end_time": 1.0,
        "duration": 1.0,
    }
    base.update(overrides)
    return TokenizedWord(**base)


def _line(text: str, lemma: str, surface: str) -> LineLemmas:
    return LineLemmas(
        line_text=text,
        lemmas=frozenset({lemma}),
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
        lemma_spans=((lemma, surface, text.index(surface), text.index(surface) + len(surface), -1),),
    )


# --- (a) the default path is the JA static, verbatim -------------------------


@pytest.mark.parametrize(
    ("surface", "pos1", "lemma", "orth_base"),
    [
        ("破れ", "動詞", "破れる", "破れる"),  # verb: mines orthBase
        ("豪腕", "名詞", "剛腕", "剛腕"),  # noun: keeps surface (Issue #5)
    ],
)
def test_no_policy_matches_select_mined_form(test_config, tmp_path, surface, pos1, lemma, orth_base):
    token = _make_token(surface, pos1, lemma=lemma, orth_base=orth_base)
    words = _parse_one(test_config, token, surface, tmp_path)

    assert len(words) == 1
    expected = select_mined_form(pos1, orth_base, lemma, surface, pronunciation="")
    assert words[0].mined_form == expected


# --- (b) an injected policy is consulted, and its answer reaches the card ----


def test_injected_policy_answer_becomes_the_card_front(test_config, tmp_path):
    policy = _StubPolicy("X")
    token = _make_token("破れ", "動詞", lemma="破れる", orth_base="破れる")

    words = _parse_one(test_config, token, "破れ", tmp_path, policy=policy)

    # The identity resolver runs once per pass (sentence attestation, then the
    # emit), so only the argument tuple is pinned, not the call count.
    assert policy.calls
    assert set(policy.calls) == {("動詞", "破れる", "破れる", "破れ", "")}
    assert len(words) == 1
    assert words[0].mined_form_override == "X"
    # Not the surface, and not the JA static's answer either.
    assert words[0].mined_form == "X"


# --- (c) the ja profile's policy IS the static, on the Issue #19/#5 cases ----


@pytest.mark.parametrize(
    ("pos", "orth_base", "lemma", "surface"),
    [
        ("動詞", "破れる", "破れる", "破れ"),  # Issue #19: mine the dictionary form
        ("動詞", "殺る", "遣る", "殺っ"),  # Issue #5: keep the source orthography
        ("動詞", "賭ける", "掛ける", "賭け"),  # Issue #5: not the collapsed lemma
        ("名詞", "豪腕", "剛腕", "豪腕"),
    ],
)
def test_ja_profile_policy_delegates_to_select_mined_form(pos, orth_base, lemma, surface):
    policy = get_profile("ja").mined_form
    assert policy.mined_form(pos, orth_base, lemma, surface) == select_mined_form(pos, orth_base, lemma, surface)
    assert policy.mined_form(pos, orth_base, lemma, surface, "") == select_mined_form(
        pos, orth_base, lemma, surface, ""
    )


# --- (d) the kana-recovery probe stays on the static, deliberately ----------


def test_kana_recovery_probe_ignores_the_injected_policy(test_config, tmp_path):
    """``_probe_kana_recovery`` is a ja-only attestation probe (see its docstring)."""
    policy = _StubPolicy("X")
    probed: list[list[str]] = []

    def kana_attest_lookup(forms):
        probed.append(list(forms))
        return dict.fromkeys(forms, True)

    token = _make_token("きれい", "形状詞", lemma="きれい", orth_base="きれい")
    tagger = MagicMock()
    tagger.return_value = [token]
    with patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=tagger):
        service = SubtitleParserService(
            test_config,
            kana_attest_lookup=kana_attest_lookup,
            mined_form_policy=policy,
        )

    assert service._probe_kana_recovery(token, "形状詞", "きれい") is True
    # The static's answer was probed, and the policy was never asked.
    assert probed == [["きれい"]]
    assert policy.calls == []


# --- (e) a hand-built word keeps the JA property behaviour ------------------


def test_hand_built_word_without_override_uses_the_static():
    word = _word(surface="破れ", lemma="破れる", orth_base="破れる", pos="動詞")
    assert word.mined_form_override == ""
    assert word.mined_form == select_mined_form("動詞", "破れる", "破れる", "破れ")
    assert word.mined_form == "破れる"


def test_override_wins_over_the_static():
    word = _word(surface="먹었어요", lemma="먹다", orth_base="먹다", pos="VV", mined_form_override="먹다")
    assert word.mined_form == "먹다"
    # The static would have answered the inflected surface.
    assert select_mined_form("VV", "먹다", "먹다", "먹었어요") == "먹었어요"


# --- (f) ja identity across the swap gate -----------------------------------


def test_swap_gate_is_ja_identical_with_and_without_the_policy(test_config):
    """The ja profile's policy changes no verdict — same booleans either way."""
    word = _word(surface="方", lemma="方", orth_base="方", pos="名詞", mined_form_override="方")
    assert word.mined_form == "方"

    same_front = _line("この方が好き", "方", "方")
    other_front = _line("ホウを見る", "方", "ホウ")

    plain = WordFilterService(test_config)
    routed = WordFilterService(test_config, mined_form=get_profile("ja").mined_form)

    assert plain._line_preserves_mined_form(word, same_front) is True
    assert plain._line_preserves_mined_form(word, other_front) is False
    assert routed._line_preserves_mined_form(word, same_front) is True
    assert routed._line_preserves_mined_form(word, other_front) is False


# --- (g) the ko regression no ja test can observe ---------------------------


def test_ko_sentence_candidates_need_the_same_policy(test_config):
    """Recomputing with the JA table rejects EVERY line for a Korean verb."""
    line_index = [
        _line("밥을 먹었어요", "먹다", "먹었어요"),
        _line("지금 먹는다", "먹다", "먹는다"),
    ]

    routed = WordFilterService(test_config, mined_form=_StubPolicy("먹다"))
    word = _word(surface="먹었어요", lemma="먹다", orth_base="먹다", pos="VV", mined_form_override="먹다")
    routed.attach_sentence_candidates([word], line_index)
    assert [variant.surface for variant in word.sentence_candidates] == ["먹었어요", "먹는다"]

    # Without the policy the gate recomputes with the JA POS table, which falls
    # through to `return surface` for a Korean VV — never the override — so the
    # curator's sentence picker comes up empty.
    plain = WordFilterService(test_config)
    unrouted = _word(surface="먹었어요", lemma="먹다", orth_base="먹다", pos="VV", mined_form_override="먹다")
    plain.attach_sentence_candidates([unrouted], line_index)
    assert unrouted.sentence_candidates == []
