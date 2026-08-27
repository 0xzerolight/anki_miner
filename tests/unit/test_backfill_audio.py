from anki_miner.services.backfill_audio import word_audio_candidates


class _Feature:
    def __init__(self, kana: str) -> None:
        self.kana = kana


class _Token:
    def __init__(self, surface: str, kana: str) -> None:
        self.surface = surface
        self.feature = _Feature(kana)


def _tagger(mapping: dict[str, str]):
    """Duck-typed tagger: text -> one token whose kana feature is mapping[text]."""

    def parse(text: str):
        return [_Token(text, mapping.get(text, ""))]

    return parse


def test_plain_word_is_one_pair():
    assert word_audio_candidates("食べる", "たべる", "食べる", None) == [("食べる", "たべる")]


def test_blank_reading_yields_nothing():
    assert word_audio_candidates("食べる", "", "食べる", None) == []


def test_blank_mined_form_yields_nothing():
    assert word_audio_candidates("", "たべる", "", None) == []


def test_katakana_variant_is_added():
    assert word_audio_candidates("チップ", "ちっぷ", "チップ", None) == [
        ("チップ", "ちっぷ"),
        ("チップ", "チップ"),
    ]


def test_okurigana_only_lemma_gets_its_own_reading():
    result = word_audio_candidates("探し", "さがし", "探す", _tagger({"探す": "サガス"}))
    assert result == [("探し", "さがし"), ("探す", "さがす")]


def test_different_kanji_lemma_is_not_used():
    # 殺る -> 遣る is a UniDic canonicalization onto another homograph.
    result = word_audio_candidates("殺る", "やる", "遣る", _tagger({"遣る": "ヤル"}))
    assert result == [("殺る", "やる")]


def test_lemma_equal_to_mined_form_needs_no_tagger_call():
    calls: list[str] = []

    def tagger(text: str):
        calls.append(text)
        return [_Token(text, "タベル")]

    assert word_audio_candidates("食べる", "たべる", "食べる", tagger) == [("食べる", "たべる")]
    assert calls == []


def test_tagger_failure_degrades_to_the_mined_form_pair():
    def tagger(text: str):
        raise RuntimeError("tagger unavailable")

    assert word_audio_candidates("探し", "さがし", "探す", tagger) == [("探し", "さがし")]


def test_missing_lemma_falls_back_to_the_mined_form():
    # _resolve_context leaves lemma == mined_form for a multi-token expression.
    assert word_audio_candidates("食べる", "たべる", "", None) == [("食べる", "たべる")]
