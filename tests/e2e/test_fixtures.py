"""Tests for the E2E harness fixtures: video, subtitle, and offline dictionary.

These run in the DEFAULT suite. Anything that actually invokes ffmpeg/ffprobe is
gated behind a skip-if-the-binary-is-missing guard so a binary-less CI box does
not fail; the committed-asset checks fall back to a structural MP4 probe when
ffprobe is unavailable, and the tokenizer test skips if fugashi/MeCab cannot be
imported. On this dev box ffmpeg + fugashi are present, so they run for real.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.e2e import fixtures_dictionary, fixtures_media, fixtures_subtitle


def _ffmpeg_available() -> bool:
    """True if the resolved ffmpeg executable is invokable."""
    return shutil.which(fixtures_media._ffmpeg()) is not None


def _ffprobe_available() -> bool:
    """True if the resolved ffprobe executable is invokable."""
    return shutil.which(fixtures_media._ffprobe()) is not None


# --------------------------------------------------------------------------- #
# Video
# --------------------------------------------------------------------------- #


def test_committed_video_asset_exists_and_nontrivial() -> None:
    """The committed clip exists and is a non-trivial, valid MP4.

    If ffprobe is available, assert BOTH a video and an audio stream. Otherwise
    fall back to a structural check (the MP4 ``ftyp`` box at bytes 4-8) so the
    test still runs without any binaries.
    """
    path = fixtures_media.TEST_VIDEO_PATH
    assert path.exists(), f"committed clip missing: {path}"
    assert path.stat().st_size > 1024, "committed clip is implausibly small"

    if _ffprobe_available():
        has_video, has_audio = fixtures_media._probe_streams(path)
        assert has_video, "clip is missing a video stream"
        assert has_audio, "clip is missing an audio stream"
    else:
        # Minimal ISO-BMFF sanity: bytes 4-8 spell the 'ftyp' box type.
        with path.open("rb") as fh:
            header = fh.read(12)
        assert header[4:8] == b"ftyp", "committed clip is not a valid MP4 (no ftyp box)"


def test_generate_test_video_idempotent(tmp_path: Path) -> None:
    """generate_test_video produces a two-stream clip and skips on re-run."""
    if not (_ffmpeg_available() and _ffprobe_available()):
        pytest.skip("ffmpeg/ffprobe not available")

    dest = tmp_path / "clip.mp4"
    fixtures_media.generate_test_video(dest)
    assert dest.exists()
    assert fixtures_media._probe_streams(dest) == (True, True)

    # Idempotent: a second call must not rewrite the already-valid file.
    mtime_before = dest.stat().st_mtime_ns
    fixtures_media.generate_test_video(dest)
    assert dest.stat().st_mtime_ns == mtime_before


def test_get_test_video_returns_committed_path() -> None:
    """get_test_video returns the committed asset (already present)."""
    assert fixtures_media.get_test_video() == fixtures_media.TEST_VIDEO_PATH
    assert fixtures_media.TEST_VIDEO_PATH.exists()


# --------------------------------------------------------------------------- #
# Subtitle
# --------------------------------------------------------------------------- #


def test_committed_subtitle_asset_exists() -> None:
    """The committed subtitle asset exists and is non-empty."""
    path = fixtures_subtitle.TEST_SRT_PATH
    assert path.exists(), f"committed subtitle missing: {path}"
    assert path.stat().st_size > 0


def test_write_test_srt_has_srt_structure(tmp_path: Path) -> None:
    """write_test_srt emits SRT structure (one arrow-timing line per cue, in-window)."""
    srt = fixtures_subtitle.write_test_srt(tmp_path / "e2e.srt")
    text = srt.read_text(encoding="utf-8")
    # One numbered block per line, each with an SRT arrow timing.
    assert text.count(" --> ") == len(fixtures_subtitle.SUBTITLE_LINES)
    for start, end, body in fixtures_subtitle.SUBTITLE_LINES:
        assert body in text
        # Every cue must fall inside the clip's [0, 10] s window.
        assert 0.0 <= start <= end <= 10.0


def test_subtitle_yields_expected_lemmas(tmp_path: Path) -> None:
    """Parsing the SRT with the real tokenizer yields exactly EXPECTED_LEMMAS.

    This is the guard that keeps EXPECTED_LEMMAS honest: it re-derives the
    lemma set from the live SubtitleParserService and asserts equality. Skips if
    fugashi/MeCab cannot be imported (it is present where the unit suite runs).
    """
    pytest.importorskip("fugashi")
    from anki_miner.config import AnkiMinerConfig
    from anki_miner.services import SubtitleParserService

    srt = fixtures_subtitle.write_test_srt(tmp_path / "e2e.srt")
    parser = SubtitleParserService(AnkiMinerConfig())
    words = parser.parse_subtitle_file(srt)

    lemmas = tuple(w.lemma for w in words)
    assert lemmas == fixtures_subtitle.EXPECTED_LEMMAS
    # Every expected lemma has a reading entry the dict fixture can seed from.
    assert set(fixtures_subtitle.LEMMA_READINGS) == set(fixtures_subtitle.EXPECTED_LEMMAS)
    # Reading VALUES must also match: a MeCab/unidic change that altered a lemma's
    # reading would otherwise silently rot LEMMA_READINGS (and the seeded dict).
    assert {w.lemma: w.lemma_reading for w in words} == fixtures_subtitle.LEMMA_READINGS


# --------------------------------------------------------------------------- #
# Dictionary
# --------------------------------------------------------------------------- #


def test_seed_offline_dict_is_discoverable(tmp_path: Path) -> None:
    """The seeded dict loads via DictionaryRegistry with a valid schema."""
    from anki_miner.services.dictionary.registry import DictionaryRegistry

    db_path = fixtures_dictionary.seed_offline_dict(tmp_path)
    assert db_path.exists()

    registry = DictionaryRegistry(tmp_path)
    registry.load()

    meta = registry.get(fixtures_dictionary.DEFAULT_DICT_ID)
    assert meta is not None, "seeded dict was not discovered by the registry"
    assert meta.schema_ok, "seeded dict reports an invalid schema_version"
    assert meta.entry_count == len(fixtures_subtitle.EXPECTED_LEMMAS)
    assert meta.entry_count > 0
    assert meta.format == "yomitan"


def test_seeded_dict_covers_every_expected_lemma(tmp_path: Path) -> None:
    """Every expected lemma resolves to a definition through the offline chain.

    Builds the indexed provider chain over the seeded dict (no Jisho) and
    confirms each lemma's gloss surfaces — i.e. the harness can mine offline.
    """
    from dataclasses import replace

    from anki_miner.config import AnkiMinerConfig, ChainEntry
    from anki_miner.services.definition_service import DefinitionService
    from anki_miner.services.dictionary.registry import DictionaryRegistry

    fixtures_dictionary.seed_offline_dict(tmp_path)

    config = replace(
        AnkiMinerConfig(),
        dicts_root=tmp_path,
        dictionary_chain=(ChainEntry(kind="indexed", dict_id=fixtures_dictionary.DEFAULT_DICT_ID, enabled=True),),
    )
    registry = DictionaryRegistry(tmp_path)
    registry.load()
    providers = registry.build_provider_chain(config)
    service = DefinitionService(config, providers=providers)

    lemmas = list(fixtures_subtitle.EXPECTED_LEMMAS)
    definitions = service.get_definitions_batch(lemmas)
    assert len(definitions) == len(lemmas)
    for lemma, definition in zip(lemmas, definitions, strict=True):
        assert definition is not None, f"no offline definition for {lemma!r}"
        assert fixtures_dictionary.GLOSSES[lemma] in definition
