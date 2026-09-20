"""The vi tokenizer: a new-style tagging copy, punctuation regrouping, verbatim spans (spec C.4).

The first half is engine-free (stub segmenter/tagger); the second hard-requires underthesea
(the [vi] extra is in the dev venv, PY311 and CI's `.[dev,languages]`), with the tagger built once
per module because tests/conftest.py clears the tagger cache per test.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from anki_miner.languages.vi.pos import VI_POS_LABELS
from anki_miner.languages.vi.script import vi_normalize
from anki_miner.languages.vi.tokenizer import (
    POS_MODEL_DIR,
    VietnameseTagger,
    build_tagger,
    regroup_punctuation,
    tagging_text,
    to_duck_tokens,
)

SAMPLE = Path(__file__).resolve().parents[2] / "fixtures" / "vi" / "opensubtitles2024_sample.txt"


@pytest.mark.parametrize(
    ("words", "regrouped"),
    [
        (["Mày", "? Tao", "đi"], ["Mày", "?", "Tao", "đi"]),
        (["♪ Directionless"], ["♪", "Directionless"]),
        (["-- 01:10:55"], ["--", "01:10:55"]),
        (["? ! Tao anh"], ["?", "!", "Tao anh"]),
        (["bác sĩ", ".", "Anh"], ["bác sĩ", ".", "Anh"]),
    ],
)
def test_a_letter_free_piece_glued_into_a_word_is_split_off(words, regrouped):
    assert regroup_punctuation(words) == regrouped


def test_the_tagging_copy_is_new_style_lowered_when_shouted_and_never_changes_length():
    assert tagging_text("Chúng ta cần hòa bình.") == "Chúng ta cần hoà bình."
    assert tagging_text("HÔM NAY TRỜI ĐẸP QUÁ.") == "hôm nay trời đẹp quá."
    decomposed = unicodedata.normalize("NFD", "hòa bình")  # the tone move would shorten it: kept as is
    assert tagging_text(decomposed) == decomposed


def test_surfaces_are_sliced_from_the_real_line_and_lemmas_folded():
    text = "Chúng ta cần hòa bình."
    copy = tagging_text(text)
    tokens = to_duck_tokens(["Chúng ta", "cần", "hoà bình", "."], ["P", "V", "A", "CH"], text, copy)
    assert [t.surface for t in tokens] == ["Chúng ta", "cần", "hòa bình", "."]
    assert [t.feature.lemma for t in tokens] == ["chúng ta", "cần", "hòa bình", "."]
    assert [t.feature.pos1 for t in tokens] == ["P", "V", "A", "CH"]
    assert [t.feature.pos2 for t in tokens] == ["stopword", "", "", ""]
    assert all(t.feature.kana == "" for t in tokens)


def test_a_word_missing_from_its_line_is_dropped_not_misplaced():
    tokens = to_duck_tokens(["cần", "xyz", "bình"], ["V", "N", "N"], "cần hòa bình", "cần hoà bình")
    assert [t.surface for t in tokens] == ["cần", "bình"]


def test_the_tagger_feeds_the_engine_the_copy_and_regroups_before_tagging():
    seen: dict[str, object] = {}

    def segment(text):
        seen["segment"] = text
        return ["mày", "? tao"]

    def tag(words):
        seen["tag"] = list(words)
        return ["P"] * len(words)

    tokens = VietnameseTagger(segment, tag)("MÀY? TAO")
    assert seen == {"segment": "mày? tao", "tag": ["mày", "?", "tao"]}
    assert [t.surface for t in tokens] == ["MÀY", "?", "TAO"]
    assert [t.surface for t in VietnameseTagger(segment, tag).parse("MÀY? TAO")] == ["MÀY", "?", "TAO"]


# --- real engine ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tagger():
    return build_tagger()


def _pairs(tagger, text):
    return [(t.surface, t.feature.pos1) for t in tagger(text)]


def test_the_smoke_sentence(tagger):
    assert _pairs(tagger, "Hôm nay trời đẹp quá.") == [
        ("Hôm nay", "N"),
        ("trời", "N"),
        ("đẹp", "A"),
        ("quá", "R"),
        (".", "CH"),
    ]


def test_an_old_style_cue_segments_like_the_dictionary_spelling(tagger):
    """Decision 1: fed old style, sức khỏe splits into sức + khỏe; the new-style copy keeps it one word."""
    first = tagger("Sức khỏe của mẹ tôi không tốt.")[0]
    assert (first.surface, first.feature.lemma, first.feature.pos1) == ("Sức khỏe", "sức khỏe", "N")


def test_a_shouted_line_is_tagged_lowered_and_keeps_its_capitals(tagger):
    tokens = tagger("HÔM NAY TRỜI ĐẸP QUÁ.")
    assert [t.surface for t in tokens] == ["HÔM NAY", "TRỜI", "ĐẸP", "QUÁ", "."]
    assert tokens[3].feature.pos1 == "R" and tokens[0].feature.lemma == "hôm nay"


def test_glued_punctuation_is_its_own_token(tagger):
    surfaces = [t.surface for t in tagger("Mày đi đâu đấy? Tao đang chờ mày mà!")]
    assert "?" in surfaces and "Tao" in surfaces and "? Tao" not in surfaces


def test_kinship_pronouns_and_the_classifier_con_carry_the_stopword_tier(tagger):
    tiers = {t.surface: (t.feature.pos1, t.feature.pos2) for t in tagger("Anh ơi, con mèo này đẹp quá!")}
    assert tiers["Anh"] == ("N", "stopword")
    assert tiers["con"] == ("Nc", "stopword")
    assert tiers["mèo"] == ("N", "")


def test_a_whole_personal_name_is_one_proper_noun(tagger):
    first = tagger("Trần Thị Bích Hằng là bác sĩ ở bệnh viện này.")[0]
    assert (first.surface, first.feature.pos1) == ("Trần Thị Bích Hằng", "Np")


def test_every_sample_cue_is_covered_by_verbatim_spans(tagger):
    """60 real OpenSubtitles 2024 cues: no token lost, every surface a slice of the normalised line."""
    for raw in SAMPLE.read_text(encoding="utf-8").splitlines():
        line = vi_normalize(raw)
        tokens = tagger(line)
        assert "".join(t.surface for t in tokens).replace(" ", "") == line.replace(" ", ""), line
        cursor = 0
        for token in tokens:
            cursor = line.index(token.surface, cursor) + len(token.surface)


def test_the_model_is_the_packaged_v2_crf_and_every_label_is_named():
    import underthesea.pipeline.pos_tag as pos_tag
    from underthesea.models.fast_crf_sequence_tagger import FastCRFSequenceTagger

    model_dir = Path(pos_tag.__file__).parent / POS_MODEL_DIR
    assert (model_dir / "models.bin").is_file()  # the pack's third sentinel
    model = FastCRFSequenceTagger()
    model.load(str(model_dir))
    labels = {label[2:] for label in model.estimator.labels()}
    assert len(labels) == 31 and labels <= set(VI_POS_LABELS)
