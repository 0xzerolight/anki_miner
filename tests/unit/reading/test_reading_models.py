"""Contract tests for the reading-tab data models."""

import dataclasses
from pathlib import Path

import pytest

from anki_miner.services.reading.models import (
    ImageRef,
    ReadingDocument,
    ReadingSourceRef,
    ReadingUnit,
)


def test_imageref_dir_shape_defaults_entry_none():
    ref = ImageRef(Path("pages/001.jpg"))
    assert ref.source == Path("pages/001.jpg")
    assert ref.entry is None


def test_imageref_archive_shape_positional():
    ref = ImageRef(Path("vol.cbz"), "001.jpg")
    assert ref.source == Path("vol.cbz")
    assert ref.entry == "001.jpg"


def test_imageref_is_frozen():
    ref = ImageRef(Path("a.jpg"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.source = Path("b.jpg")  # type: ignore[misc]


def test_imageref_hashable_and_dict_key():
    dir_ref = ImageRef(Path("a.jpg"))
    arch_ref = ImageRef(Path("a.jpg"), "a.jpg")
    mapping = {dir_ref: 1, arch_ref: 2}
    # The two shapes are distinct keys despite the same source path.
    assert len(mapping) == 2
    assert dir_ref != arch_ref
    assert mapping[ImageRef(Path("a.jpg"))] == 1
    assert mapping[ImageRef(Path("a.jpg"), "a.jpg")] == 2


def test_imageref_usable_in_set_dedup():
    refs = [
        ImageRef(Path("p.jpg")),
        ImageRef(Path("p.jpg")),
        ImageRef(Path("vol.zip"), "p.jpg"),
    ]
    assert len(set(refs)) == 2


def test_reading_unit_is_frozen():
    unit = ReadingUnit(text="本文", index=3, location_label="p.42")
    assert unit.image_ref is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        unit.text = "changed"  # type: ignore[misc]


def test_reading_unit_carries_image_ref():
    ref = ImageRef(Path("vol.cbz"), "003.jpg")
    unit = ReadingUnit(text="", index=2, location_label="p.3", image_ref=ref)
    assert unit.image_ref is ref


def test_reading_unit_block_box_defaults_none():
    # Novels/txt (and pre-box constructions) never pass block_box.
    unit = ReadingUnit(text="本文", index=0, location_label="ch.1")
    assert unit.block_box is None


def test_reading_unit_carries_block_box():
    unit = ReadingUnit(text="", index=1, location_label="p.2", block_box=(1, 2, 3, 4))
    assert unit.block_box == (1, 2, 3, 4)


def test_reading_source_ref_is_frozen():
    ref = ReadingSourceRef(
        kind="mokuro",
        path=Path("v1.mokuro"),
        image_root=Path("v1.cbz"),
        title="Show",
        volume="1",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.title = "x"  # type: ignore[misc]


def test_reading_source_ref_book_shape():
    ref = ReadingSourceRef(
        kind="epub",
        path=Path("novel.epub"),
        image_root=None,
        title="novel",
        volume=None,
    )
    assert ref.image_root is None
    assert ref.volume is None


def test_reading_document_is_mutable():
    doc = ReadingDocument(title="T", kind="book", series="Books", episode="T")
    assert doc.units == []
    assert doc.warnings == []
    doc.units.append(ReadingUnit(text="a", index=0, location_label="¶1"))
    doc.warnings.append("text-only volume")
    assert len(doc.units) == 1
    assert doc.warnings == ["text-only volume"]
    # Distinct documents do not share the mutable default lists.
    other = ReadingDocument(title="U", kind="manga", series="S", episode="1")
    assert other.units == []
