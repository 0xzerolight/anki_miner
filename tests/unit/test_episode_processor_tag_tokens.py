"""F-03 in the pipeline: each item renders the tag string from its own source."""

from __future__ import annotations

import dataclasses
import time
from dataclasses import replace
from unittest.mock import MagicMock

from anki_miner.languages.registry import get_profile
from anki_miner.models import MediaData, ProcessingResult, TokenizedWord
from anki_miner.models.reading import ReadingDocument
from anki_miner.orchestration.episode_processor import (
    _document_tag_values,
    _episode_tag_values,
    _EpisodeContext,
)
from anki_miner.services.anki_note_builder import CARD_TAGS_KEY
from tests.conftest import build_processor


def test_episode_values_are_sanitized_like_the_source_label():
    assert _episode_tag_values("[SubsPlease] Frieren", "[SubsPlease] Frieren - 03 (1080p) [ABCD1234]") == {
        "series": "Frieren",
        "episode": "Frieren - 03",
        "volume": "",
    }


def test_a_part_that_sanitizes_to_nothing_is_kept_as_written():
    assert _episode_tag_values("[Oshi no Ko]", "01")["series"] == "[Oshi no Ko]"


def test_a_manga_volume_fills_volume():
    document = ReadingDocument(title="Frieren", kind="manga", series="Frieren", episode="Vol. 01")

    assert _document_tag_values(document) == {"series": "Frieren", "episode": "", "volume": "Vol. 01"}


def test_a_book_is_its_own_series():
    document = ReadingDocument(title="Kokoro", kind="book", series="Books", episode="Kokoro")

    assert _document_tag_values(document) == {"series": "Kokoro", "episode": "", "volume": ""}


def test_a_subtitle_file_reads_like_a_video():
    document = ReadingDocument(title="ep", kind="subtitle", series="Frieren", episode="[SubsPlease] Frieren - 03")

    assert _document_tag_values(document) == {"series": "Frieren", "episode": "Frieren - 03", "volume": ""}


def _word() -> TokenizedWord:
    return TokenizedWord(
        surface="電影",
        lemma="電影",
        reading="",
        sentence="電影。",
        start_time=1.0,
        end_time=3.0,
        duration=2.0,
        pos="名詞",
    )


def _phase5_payloads(config, ctx):
    """Drive _phase5_create on a non-ja profile (Review Focus 3) and return the payloads."""
    definition_service = MagicMock()
    definition_service.css_entries.return_value = []
    anki_service = MagicMock()
    anki_service.create_cards_batch.return_value = []
    profile = dataclasses.replace(get_profile("ja"), code="zh", render_hooks=())
    processor = build_processor(
        config, definition_service=definition_service, anki_service=anki_service, profile=profile
    )
    processor._phase5_create(ctx, [(_word(), MediaData())], ["film"], [None], [(None, None)], None)
    return anki_service.create_cards_batch.call_args.args[0]


def _ctx(**values: str) -> _EpisodeContext:
    return _EpisodeContext(
        start_time=time.time(),
        video_file_str="ep.mkv",
        subtitle_file_str="ep.ass",
        episode_name="ep",
        series_name="series",
        source_label="ep",
        tag_values=dict(values),
    )


def test_phase5_stamps_the_rendered_tags_on_every_payload(test_config):
    config = replace(test_config, anki_tags="mined am::{series}::{episode}")

    payloads = _phase5_payloads(config, _ctx(series="Frieren", episode="03", volume=""))

    assert payloads[0].extra_fields[CARD_TAGS_KEY] == "mined am::Frieren::03"


def test_a_tag_string_without_tokens_leaves_the_payload_as_it_was(test_config):
    config = replace(test_config, anki_tags="auto-mined")

    payloads = _phase5_payloads(config, _ctx(series="Frieren", episode="03", volume=""))

    assert CARD_TAGS_KEY not in (payloads[0].extra_fields or {})


def test_process_episode_hands_its_source_to_the_context(test_config, monkeypatch, tmp_path):
    processor = build_processor(test_config)
    seen: list[_EpisodeContext] = []

    def fake_pipeline(ctx, cancel_event, body):
        seen.append(ctx)
        return ProcessingResult(total_words_found=0, new_words_found=0, cards_created=0)

    monkeypatch.setattr(processor, "_run_pipeline", fake_pipeline)
    folder = tmp_path / "[SubsPlease] Frieren"
    processor.process_episode(folder / "[SubsPlease] Frieren - 03 (1080p).mkv", folder / "ep03.ass")

    assert seen[0].tag_values == {"series": "Frieren", "episode": "Frieren - 03", "volume": ""}


def test_process_reading_hands_its_document_to_the_context(test_config, monkeypatch):
    processor = build_processor(test_config)
    seen: list[_EpisodeContext] = []

    def fake_pipeline(ctx, cancel_event, body):
        seen.append(ctx)
        return ProcessingResult(total_words_found=0, new_words_found=0, cards_created=0)

    monkeypatch.setattr(processor, "_run_pipeline", fake_pipeline)
    processor.process_reading(ReadingDocument(title="Frieren", kind="manga", series="Frieren", episode="Vol. 02"))

    assert seen[0].tag_values == {"series": "Frieren", "episode": "", "volume": "Vol. 02"}
