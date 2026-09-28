"""Tests for mining an existing Anki deck (Issue #131): field helpers,
field auto-detection, and the per-note loader."""

from __future__ import annotations

from anki_miner.languages.registry import get_profile
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


def test_core_2k_layout_prefers_the_sentence_media_and_gloss():
    names = [
        "Vocabulary-Kanji",
        "Vocabulary-English",
        "Vocabulary-Audio",
        "Sentence-Kanji",
        "Sentence-English",
        "Sentence-Audio",
        "Sentence-Image",
    ]
    sample = {
        "Vocabulary-Kanji": "天気",
        "Vocabulary-English": "weather",
        "Vocabulary-Audio": "[sound:v.mp3]",
        "Sentence-Kanji": "今日はいい天気だ",
        "Sentence-English": "Nice weather today",
        "Sentence-Audio": "[sound:s.mp3]",
        "Sentence-Image": '<img src="s.jpg">',
    }
    fmap = suggest_field_map(names, [sample] * 5, contains_target_script=_JA)
    assert (fmap.sentence, fmap.audio, fmap.picture, fmap.translation) == (
        "Sentence-Kanji",
        "Sentence-Audio",
        "Sentence-Image",
        "Sentence-English",
    )


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
