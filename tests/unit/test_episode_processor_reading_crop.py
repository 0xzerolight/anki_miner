"""F-11 (R4, REPORT judge round 2): every word on a manga page gets its own block's crop."""

from __future__ import annotations

import collections
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from anki_miner.models.reading import ImageRef, ReadingUnit
from tests.unit.test_episode_processor_reading import (
    _IMG,
    _document,
    _make_anki_service,
    _make_processor,
    _parse_returning,
    _word,
)

PAGE = ImageRef(Path("/pages/page01.png"))


def _boxed(index: int, box: tuple[int, int, int, int] | None) -> ReadingUnit:
    return ReadingUnit(text=f"unit{index}", index=index, location_label="p.1", image_ref=PAGE, block_box=box)


def _fake_prepare(ref, dest_dir, archive_handles, *, block_box=None) -> Path:
    return Path(f"/tmp/{ref.source.stem}-{block_box}.jpg")


def _mine(config, units: list[ReadingUnit]):
    words = [_word(("犬", "猫", "鳥")[i], unit.index) for i, unit in enumerate(units)]
    counts = collections.Counter(word.lemma for word in words)
    parser = MagicMock()
    parser.parse_text_units.side_effect = _parse_returning(words, None, counts)
    anki = _make_anki_service()
    processor = _make_processor(config, subtitle_parser=parser, anki_service=anki)
    with patch(_IMG, side_effect=_fake_prepare) as prepare:
        processor.process_reading(_document(units))
    pictures = {card.media.screenshot_filename for card in anki.last_card_data}
    return prepare, pictures


def test_two_words_on_one_page_get_their_own_crops(test_config):
    config = replace(test_config, crop_manga_to_text=True)

    prepare, pictures = _mine(config, [_boxed(0, (10, 10, 50, 50)), _boxed(1, (60, 60, 90, 90))])

    assert prepare.call_count == 2
    assert sorted(call.kwargs["block_box"] for call in prepare.call_args_list) == [(10, 10, 50, 50), (60, 60, 90, 90)]
    assert pictures == {"page01-(10, 10, 50, 50).jpg", "page01-(60, 60, 90, 90).jpg"}


def test_one_block_shared_by_two_words_materializes_once(test_config):
    config = replace(test_config, crop_manga_to_text=True)

    prepare, pictures = _mine(config, [_boxed(0, (10, 10, 50, 50)), _boxed(1, (10, 10, 50, 50))])

    assert prepare.call_count == 1
    assert pictures == {"page01-(10, 10, 50, 50).jpg"}


def test_crop_off_shares_one_picture_per_page(test_config):
    config = replace(test_config, crop_manga_to_text=False)

    prepare, pictures = _mine(config, [_boxed(0, (10, 10, 50, 50)), _boxed(1, (60, 60, 90, 90))])

    assert prepare.call_count == 1
    assert prepare.call_args.kwargs.get("block_box") is None
    assert pictures == {"page01-None.jpg"}


def test_a_unit_without_a_box_keeps_the_full_page(test_config):
    config = replace(test_config, crop_manga_to_text=True)

    prepare, pictures = _mine(config, [_boxed(0, None)])

    assert prepare.call_args.kwargs.get("block_box") is None
    assert pictures == {"page01-None.jpg"}
