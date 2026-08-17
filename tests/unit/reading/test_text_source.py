"""Tests for the pasted-text source loader."""

from anki_miner.models.reading import ImageRef, ReadingSourceRef
from anki_miner.services.reading import text_source


def test_multi_paragraph_labels_and_indexes():
    doc = text_source.load(ReadingSourceRef(kind="text", title="Text", text="一段落目。\n\n二段落目。"))
    assert [u.location_label for u in doc.units] == ["¶1", "¶2"]
    assert [u.index for u in doc.units] == [0, 1]
    assert all(u.image_ref is None for u in doc.units)


def test_intra_line_sentence_split():
    doc = text_source.load(ReadingSourceRef(kind="text", title="Text", text="今日は晴れ。明日は雨。"))
    assert [u.text for u in doc.units] == ["今日は晴れ。", "明日は雨。"]
    assert [u.location_label for u in doc.units] == ["¶1", "¶1"]
    assert [u.index for u in doc.units] == [0, 1]


def test_full_width_indent_is_stripped():
    doc = text_source.load(ReadingSourceRef(kind="text", title="Text", text="　本文です。"))
    assert [u.text for u in doc.units] == ["本文です。"]


def test_identity_constants():
    doc = text_source.load(ReadingSourceRef(kind="text", title="Text", text="本文。"))
    assert doc.title == "Text"
    assert doc.series == "Text"
    assert doc.episode == "Text"
    assert doc.kind == "book"


def test_empty_and_whitespace_only_yield_no_units():
    for text in ("", "   \n\n  \t ", None):
        doc = text_source.load(ReadingSourceRef(kind="text", title="Text", text=text))
        assert doc.units == []
        assert doc.warnings == []


def test_crlf_normalized_to_paragraph_lines():
    # Guards against a later "simplify to str.splitlines()" regression: CRLF
    # must split physical lines...
    doc = text_source.load(ReadingSourceRef(kind="text", title="Text", text="あ\r\nい"))
    assert [u.text for u in doc.units] == ["あ", "い"]
    assert [u.location_label for u in doc.units] == ["¶1", "¶2"]


def test_exotic_line_breaks_are_not_paragraph_breaks():
    # ...while U+2028 / form-feed / NEL from PDF/web pastes must NOT — only
    # physical lines (\r\n / \r / \n) delimit paragraphs, like aozora.
    doc = text_source.load(ReadingSourceRef(kind="text", title="Text", text="あ い\fうえ"))
    assert len(doc.units) == 1
    assert [u.location_label for u in doc.units] == ["¶1"]


def test_image_root_becomes_one_shared_image_ref(tmp_path):
    picture = tmp_path / "cover.jpg"
    picture.write_bytes(b"not-really-a-jpeg")  # the loader never opens it
    ref = ReadingSourceRef(kind="text", title="Text", text="一文目。\n\n二文目。", image_root=picture)

    doc = text_source.load(ref)

    assert len(doc.units) == 2
    assert {u.image_ref for u in doc.units} == {ImageRef(picture)}
    # One identical frozen ref, so phase-3 materializes the image exactly once.
    assert doc.units[0].image_ref is doc.units[1].image_ref


def test_no_image_root_keeps_units_imageless():
    doc = text_source.load(ReadingSourceRef(kind="text", title="Text", text="一文目。"))
    assert doc.units[0].image_ref is None
