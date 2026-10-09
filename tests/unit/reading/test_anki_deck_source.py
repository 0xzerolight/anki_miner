"""Tests for mining an existing Anki deck (Issue #131): field helpers,
field auto-detection, and the per-note loader."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_miner.exceptions import AnkiConnectionError, OperationCancelled, SetupError
from anki_miner.languages.registry import get_profile
from anki_miner.models.reading import DeckFieldMap, ImageRef, ReadingSourceRef
from anki_miner.services.reading import anki_deck_source
from anki_miner.services.reading.anki_deck_source import (
    field_text,
    image_filename,
    sound_filename,
    suggest_field_map,
)

_JA = get_profile("ja").script.contains_target_script
_EN = get_profile("en").script.contains_target_script

_SUBS2SRS_FIELDS = ["SequenceMarker", "Audio", "Snapshot", "Video", "Expression", "Meaning"]


def _subs2srs(i: int) -> dict[str, str]:
    return {
        "SequenceMarker": f"ep01_{i:04d}",
        "Audio": f"[sound:Show_01_0.00.{i:02d}.000-0.00.{i + 2:02d}.000.mp3]",
        "Snapshot": f'<img src="Show_01_0.00.{i:02d}.000.jpg">',
        "Video": f"[sound:Show_01_{i}.mp4]",
        "Expression": "今日は<br>いい天気だね",
        "Meaning": "Nice weather today",
    }


# ---------------------------------------------------------------------------
# Field HTML helpers
# ---------------------------------------------------------------------------


def test_sound_filename_skips_video_clips():
    assert sound_filename("[sound:clip.mp4]") is None
    assert sound_filename("[sound:clip.mp4][sound:line.MP3]") == "line.MP3"
    assert sound_filename("[sound:asbplayer-rec.webm]") == "asbplayer-rec.webm"
    assert sound_filename("no sound here") is None


def test_image_filename_accepts_any_quoting():
    assert image_filename('<img src="a b.jpg">') == "a b.jpg"
    assert image_filename("<IMG class=x src='c.webp' />") == "c.webp"
    assert image_filename("<img src=d.png>") == "d.png"
    assert image_filename('<img src="a&amp;b.jpg">') == "a&b.jpg"
    assert image_filename("no image") is None


def test_field_text_keeps_line_breaks_and_drops_markup():
    raw = "<div><b>今日は</b>&nbsp;晴れ</div><div>Nice day[sound:x.mp3]</div>"
    assert field_text(raw) == "今日は 晴れ\nNice day"
    assert field_text("今日は<br>いい天気だね") == "今日は\nいい天気だね"


def test_field_text_drops_ruby_readings():
    assert field_text("<ruby>漢字<rp>(</rp><rt>かんじ</rt><rp>)</rp></ruby>だ") == "漢字だ"


# ---------------------------------------------------------------------------
# Field auto-detection
# ---------------------------------------------------------------------------


def test_subs2srs_layout_is_detected_for_japanese():
    fmap = suggest_field_map(_SUBS2SRS_FIELDS, [_subs2srs(i) for i in range(20)], contains_target_script=_JA)
    assert (fmap.sentence, fmap.audio, fmap.picture, fmap.translation) == (
        "Expression",
        "Audio",
        "Snapshot",
        "Meaning",
    )


def test_latin_mining_language_prefers_the_named_line_over_the_marker():
    samples = [{**_subs2srs(i), "Expression": "Hace buen tiempo"} for i in range(20)]
    fmap = suggest_field_map(_SUBS2SRS_FIELDS, samples, contains_target_script=_EN)
    assert fmap.sentence == "Expression"
    assert fmap.translation == "Meaning"


def test_sentence_named_field_beats_a_word_field_called_expression():
    samples = [{"Expression": "天気", "Sentence": "今日はいい天気だ", "Picture": '<img src="p.jpg">'}] * 5
    fmap = suggest_field_map(["Expression", "Sentence", "Picture"], samples, contains_target_script=_JA)
    assert fmap.sentence == "Sentence"
    assert fmap.picture == "Picture"


def test_a_webm_video_field_loses_to_the_audio_field_on_name():
    samples = [{"Video": "[sound:v.webm]", "Audio": "[sound:a.webm]", "Line": "猫だ"}] * 5
    fmap = suggest_field_map(["Video", "Audio", "Line"], samples, contains_target_script=_JA)
    assert fmap.audio == "Audio"


# The Core 2000 note type as shipped: field names and one note's values verbatim
# from a real export (ken-21-21/Japanese, data/raw/sample_decks.json, note
# "Core 2000 Step 01 - 001"); the same 18 names, in this order, are the "Core
# 2000" note type in blance714/StaticeApp's TestData.swift. Expression holds the
# sentence; Reading, Sentence-Kana and Sentence-Clozed are copies of it.
_CORE_2K_NOTE = {
    "Optimized-Voc-Index": "1",
    "Vocabulary-Kanji": "それ",
    "Vocabulary-Furigana": "それ",
    "Vocabulary-Kana": "それ",
    "Vocabulary-English": "that, that one",
    "Vocabulary-Audio": "[sound:8b0ee07c0864e07d96871e87f158ad96.mp3]",
    "Vocabulary-Pos": "Pronoun",
    "Caution": "",
    "Expression": "<b>それ</b>はとってもいい話だ。",
    "Reading": "<b>それ</b>はとってもいい 話[はなし]だ。",
    "Sentence-Kana": "<b>それ</b> は とっても いい はなし だ",
    "Sentence-English": "That's a really nice story.",
    "Sentence-Clozed": "<b>（　）</b>はとってもいい 話[はなし]だ。",
    "Sentence-Audio": "[sound:c951babc6302fbe6ee96898170363a6e.mp3]",
    "Notes": "Core 2000 Step 01 - 001",
    "Core-Index": "1",
    "Optimized-Sent-Index": "56",
    "Frequency": "37",
}


def test_core_2k_picks_expression_over_its_kana_and_cloze_copies():
    fmap = suggest_field_map(list(_CORE_2K_NOTE), [_CORE_2K_NOTE] * 5, contains_target_script=_JA)
    assert (fmap.sentence, fmap.audio, fmap.picture, fmap.translation) == (
        "Expression",
        "Sentence-Audio",
        "",
        "Sentence-English",
    )


def test_a_kana_only_sentence_field_ranks_below_a_plain_one():
    samples = [{"SentenceReading": "きょうはいいてんき", "Line": "今日はいい天気"}] * 5
    fmap = suggest_field_map(["SentenceReading", "Line"], samples, contains_target_script=_JA)
    assert fmap.sentence == "Line"


def test_a_fuller_video_field_still_loses_to_the_audio_field():
    # Every note has a video clip, one note lacks its audio: name, not
    # coverage, decides among fields that qualify.
    samples = [{"Video": "[sound:v.webm]", "Audio": "[sound:a.mp3]" if i else "", "Line": "猫だ"} for i in range(20)]
    fmap = suggest_field_map(["Video", "Audio", "Line"], samples, contains_target_script=_JA)
    assert fmap.audio == "Audio"


def test_fuller_word_audio_still_loses_to_sentence_audio():
    samples = [
        {
            "ExpressionAudio": "[sound:w.mp3]",
            "SentenceAudio": "[sound:s.mp3]" if i else "",
            "Sentence": "今日はいい天気だ",
        }
        for i in range(20)
    ]
    fmap = suggest_field_map(["ExpressionAudio", "SentenceAudio", "Sentence"], samples, contains_target_script=_JA)
    assert fmap.audio == "SentenceAudio"


def test_a_sentence_field_that_carries_the_clip_is_still_the_sentence():
    # The line and its [sound:] in one field: it is the audio pick AND the
    # sentence (field_text drops the sound ref from the line).
    samples = [{"Sentence": "猫だ[sound:a.mp3]", "Meaning": "It's a cat"}] * 5
    fmap = suggest_field_map(["Sentence", "Meaning"], samples, contains_target_script=_JA)
    assert (fmap.sentence, fmap.audio, fmap.translation) == ("Sentence", "Sentence", "Meaning")


def test_a_field_filled_on_under_half_the_notes_is_not_suggested():
    samples = [{"Line": "猫だ", "Audio": "[sound:a.mp3]" if i < 2 else ""} for i in range(5)]
    fmap = suggest_field_map(["Line", "Audio"], samples, contains_target_script=_JA)
    assert fmap.audio == ""
    assert fmap.sentence == "Line"


def test_no_qualifying_field_leaves_the_sentence_for_the_user():
    fmap = suggest_field_map(["Front"], [{"Front": "hello"}] * 5, contains_target_script=_JA)
    assert fmap.sentence == ""


def test_no_samples_suggests_nothing():
    fmap = suggest_field_map(["Expression", "Audio"], [], contains_target_script=_JA)
    assert (fmap.sentence, fmap.audio, fmap.picture, fmap.translation) == ("", "", "", "")


# ---------------------------------------------------------------------------
# load(): one unit per note
# ---------------------------------------------------------------------------


class FakeAnki:
    """Answers the three reads a deck load makes, like AnkiConnect does."""

    def __init__(self, notes: list[dict], media_dir: Path | None) -> None:
        self._by_id = {note["noteId"]: note for note in notes}
        self._media_dir = media_dir
        self.queries: list[str] = []

    def find_notes(self, query: str) -> list[int]:
        self.queries.append(query)
        return list(self._by_id)

    def notes_info(self, note_ids: list[int]) -> list[dict]:
        # In the order asked; a deleted note comes back as {}.
        return [self._by_id[i] if "fields" in self._by_id[i] else {} for i in note_ids]

    def media_dir_path(self) -> Path | None:
        return self._media_dir


def _note(nid: int, expression: str, audio: str = "", snapshot: str = "", meaning: str = "") -> dict:
    values = {"Expression": expression, "Audio": audio, "Snapshot": snapshot, "Meaning": meaning}
    return {
        "noteId": nid,
        "modelName": "subs2srs",
        "fields": {name: {"value": value, "order": i} for i, (name, value) in enumerate(values.items())},
    }


_FIELDS = DeckFieldMap(sentence="Expression", audio="Audio", picture="Snapshot", translation="Meaning")


def _ref(title: str = "D", fields: DeckFieldMap = _FIELDS) -> ReadingSourceRef:
    return ReadingSourceRef(kind="deck", title=title, deck_fields=fields)


def _media(tmp_path: Path, *names: str) -> Path:
    media = tmp_path / "collection.media"
    media.mkdir()
    for name in names:
        (media / name).write_bytes(b"x")
    return media


def test_each_note_becomes_a_unit_with_its_own_media(tmp_path):
    media = _media(tmp_path, "a.mp3", "a.jpg")
    anki = FakeAnki(
        [_note(2, "猫だ", "[sound:a.mp3]", '<img src="a.jpg">', "It's a <b>cat</b>"), _note(1, "犬だ")],
        media,
    )
    doc = anki_deck_source.load(_ref('My "Deck"'), anki)
    assert anki.queries == ['deck:"My \\"Deck\\""']
    assert [u.text for u in doc.units] == ["犬だ", "猫だ"]  # note-id (creation) order
    first, second = doc.units
    assert first.audio_ref is None and first.image_ref is None and first.translation == ""
    assert second.audio_ref == media / "a.mp3"
    assert second.image_ref == ImageRef(media / "a.jpg")
    assert second.translation == "It's a cat"
    assert (doc.kind, doc.title, doc.series, doc.episode) == ("deck", 'My "Deck"', 'My "Deck"', 'My "Deck"')
    assert [u.index for u in doc.units] == [0, 1]
    assert [u.location_label for u in doc.units] == ["#1", "#2"]
    assert doc.warnings == []


def test_missing_media_degrades_with_one_warning(tmp_path):
    anki = FakeAnki([_note(1, "猫だ", "[sound:gone.mp3]", '<img src="gone.jpg">')], _media(tmp_path))
    doc = anki_deck_source.load(_ref(), anki)
    assert doc.units[0].audio_ref is None and doc.units[0].image_ref is None
    assert len(doc.warnings) == 1 and doc.warnings[0].startswith("2 ")


def test_names_that_leave_the_media_folder_are_refused(tmp_path):
    media = _media(tmp_path)
    (tmp_path / "x.mp3").write_bytes(b"x")
    anki = FakeAnki(
        [
            _note(1, "猫だ", "[sound:../x.mp3]"),
            _note(2, "犬だ", "[sound:..\\x.mp3]"),
            _note(3, "鳥だ", "[sound:C:x.mp3]"),  # a Windows drive-relative join drops media_dir
        ],
        media,
    )
    doc = anki_deck_source.load(_ref(), anki)
    assert all(u.audio_ref is None for u in doc.units)


def test_unreadable_media_folder_mines_text_only(tmp_path):
    anki = FakeAnki([_note(1, "猫だ", "[sound:a.mp3]")], tmp_path / "not-here")
    doc = anki_deck_source.load(_ref(), anki)
    assert doc.units[0].text == "猫だ" and doc.units[0].audio_ref is None
    assert len(doc.warnings) == 1 and "not-here" in doc.warnings[0]


def test_an_anki_error_on_the_media_folder_degrades_instead_of_failing(tmp_path):
    anki = FakeAnki([_note(1, "猫だ", "[sound:a.mp3]")], tmp_path)
    anki.media_dir_path = MagicMock(side_effect=AnkiConnectionError("unsupported action"))  # type: ignore[method-assign]
    doc = anki_deck_source.load(_ref(), anki)
    assert doc.units[0].text == "猫だ"
    assert len(doc.warnings) == 1 and "None" not in doc.warnings[0]


def test_a_picked_audio_field_with_non_audio_clips_is_counted_not_silent(tmp_path):
    anki = FakeAnki([_note(1, "猫だ", "[sound:v.mp4]")], _media(tmp_path, "v.mp4"))
    doc = anki_deck_source.load(_ref(), anki)
    assert doc.units[0].audio_ref is None
    assert len(doc.warnings) == 1


def test_no_media_fields_never_asks_for_the_media_folder(tmp_path):
    anki = FakeAnki([_note(1, "猫だ", "[sound:a.mp3]")], None)
    anki.media_dir_path = MagicMock()  # type: ignore[method-assign]
    doc = anki_deck_source.load(_ref(fields=DeckFieldMap(sentence="Expression")), anki)
    anki.media_dir_path.assert_not_called()
    assert doc.warnings == [] and doc.units[0].audio_ref is None


def test_a_deck_with_no_sentences_fails_with_a_reason(tmp_path):
    anki = FakeAnki([_note(1, ""), {"noteId": 3}], tmp_path)  # empty line, deleted note
    with pytest.raises(SetupError, match="Expression"):
        anki_deck_source.load(_ref(), anki)


def test_notes_of_another_type_without_the_field_are_skipped(tmp_path):
    other = {"noteId": 5, "modelName": "Basic", "fields": {"Front": {"value": "猫", "order": 0}}}
    anki = FakeAnki([_note(1, "犬だ"), other], tmp_path)
    doc = anki_deck_source.load(_ref(), anki)
    assert [u.text for u in doc.units] == ["犬だ"]


def test_sentence_markup_is_cleaned_like_a_cue(tmp_path):
    anki = FakeAnki([_note(1, "<div>今日は</div><div>いい天気だね[sound:x.mp3]</div>")], tmp_path)
    doc = anki_deck_source.load(_ref(), anki)
    assert "<" not in doc.units[0].text and "sound" not in doc.units[0].text
    assert "今日は" in doc.units[0].text and "いい天気だね" in doc.units[0].text


def test_cancel_before_reading_raises(tmp_path):
    anki = FakeAnki([_note(1, "猫だ")], tmp_path)
    with pytest.raises(OperationCancelled):
        anki_deck_source.load(_ref(), anki, cancel_check=lambda: True)
