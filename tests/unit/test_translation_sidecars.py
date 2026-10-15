"""F-04: translation sidecars are the video's own subtitles tagged for a language the learner reads."""

from __future__ import annotations

from pathlib import Path

from anki_miner.languages.registry import get_profile, subtitle_language
from anki_miner.utils.audio_track_detector import TrackTitle
from anki_miner.utils.file_pairing import find_translation_sidecars

EN = get_profile("en").audio_track_codes
DE = get_profile("de").audio_track_codes


def _touch(folder: Path, *names: str) -> None:
    for name in names:
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("1\n00:00:01,000 --> 00:00:02,000\nx\n", encoding="utf-8")


def _found(video: Path, mining: str, *preferred: frozenset[str]) -> list[tuple[str, int, TrackTitle]]:
    return [
        (s.path.name, s.preference, s.title)
        for s in find_translation_sidecars(video, subtitle_language(mining), preferred)
    ]


def test_an_english_sidecar_named_for_the_video_is_found(tmp_path):
    _touch(tmp_path, "ep01.mkv", "ep01.ja.srt", "ep01.en.srt")
    assert _found(tmp_path / "ep01.mkv", "ja", EN) == [("ep01.en.srt", 0, TrackTitle.PLAIN)]


def test_untagged_and_mining_language_sidecars_are_never_translations(tmp_path):
    _touch(tmp_path, "ep01.mkv", "ep01.srt", "ep01.ja.ass")
    assert _found(tmp_path / "ep01.mkv", "ja", EN) == []


def test_a_language_the_learner_does_not_read_is_left_out(tmp_path):
    _touch(tmp_path, "ep01.mkv", "ep01.fr.srt")
    assert _found(tmp_path / "ep01.mkv", "ja", EN) == []


def test_name_flags_set_the_title_rank(tmp_path):
    _touch(tmp_path, "ep01.mkv", "ep01.en.forced.srt", "ep01.en.sdh.srt")
    titles = {name: title for name, _preference, title in _found(tmp_path / "ep01.mkv", "ja", EN)}
    assert titles == {"ep01.en.forced.srt": TrackTitle.PARTIAL, "ep01.en.sdh.srt": TrackTitle.ANNOTATED}


def test_bracketed_and_spelled_out_tags_are_read(tmp_path):
    _touch(tmp_path, "ep01.mkv", "ep01_track4_[eng].ass", "ep01.English.srt")
    assert sorted(_found(tmp_path / "ep01.mkv", "ja", EN)) == [
        ("ep01.English.srt", 0, TrackTitle.PLAIN),
        ("ep01_track4_[eng].ass", 0, TrackTitle.PLAIN),
    ]


def test_another_episodes_subtitle_is_not_taken(tmp_path):
    # Only names built on the video's own stem count: no episode-number guessing for translations.
    _touch(tmp_path, "ep01.mkv", "ep02.mkv", "ep02.en.srt", "Show - 01 [eng].srt")
    assert _found(tmp_path / "ep01.mkv", "ja", EN) == []


def test_subs_folder_per_video_layout(tmp_path):
    _touch(tmp_path, "ep01.mkv", "Subs/ep01/2_English.srt", "Subs/ep01/3_Japanese.srt")
    assert _found(tmp_path / "ep01.mkv", "ja", EN) == [("2_English.srt", 0, TrackTitle.PLAIN)]


def test_an_unreadable_folder_finds_nothing(tmp_path):
    assert _found(tmp_path / "missing" / "ep01.mkv", "ja", EN) == []


def test_spanish_mining_reads_german_and_english_sidecars(tmp_path):
    # Review Focus 3: a non-Japanese mining language; German interface first, English second.
    _touch(tmp_path, "cap01.mkv", "cap01.es.srt", "cap01.de.srt", "cap01.en.srt")
    assert sorted(_found(tmp_path / "cap01.mkv", "es", DE, EN)) == [
        ("cap01.de.srt", 0, TrackTitle.PLAIN),
        ("cap01.en.srt", 1, TrackTitle.PLAIN),
    ]
