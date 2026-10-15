"""Tests for the PDF text-layer loader (TS-05, R18).

Every PDF is built in the test by ``_pdf_fixture.make_pdf``: real PDF bytes
with a text layer, so pypdf's own extraction runs end to end.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from anki_miner.exceptions import OperationCancelled, SetupError
from anki_miner.languages.registry import get_profile
from anki_miner.models.reading import ReadingSourceRef
from anki_miner.services.reading import pdf_source
from tests.unit.reading._pdf_fixture import make_pdf


def _ref(tmp_path: Path, pages: list[list[str]], name: str = "book.pdf") -> ReadingSourceRef:
    path = tmp_path / name
    path.write_bytes(make_pdf(pages))
    return ReadingSourceRef(kind="pdf", path=path, title=path.stem)


def test_pages_become_units_labelled_by_page(tmp_path: Path) -> None:
    ref = _ref(tmp_path, [["吾輩は猫である。名前は", "まだ無い。"], [], ["どこで生れたか。"]], name="neko.pdf")

    doc = pdf_source.load(ref, rules=get_profile("ja").sentence_rules)

    assert (doc.title, doc.kind, doc.series, doc.episode) == ("neko", "book", "Books", "neko")
    assert [(u.text, u.location_label, u.index) for u in doc.units] == [
        ("吾輩は猫である。", "p.1", 0),
        ("名前はまだ無い。", "p.1", 1),
        ("どこで生れたか。", "p.3", 2),
    ]
    assert all(u.image_ref is None for u in doc.units)


def test_a_space_delimited_language_rejoins_wrapped_and_hyphenated_lines(tmp_path: Path) -> None:
    """Review Focus 3: Spanish joins a wrap with a space and mends a hyphenated word."""
    ref = _ref(tmp_path, [["El gato duerme en la ca-", "sa. El perro come", "carne."]])

    doc = pdf_source.load(ref, rules=get_profile("es").sentence_rules)

    assert [u.text for u in doc.units] == ["El gato duerme en la casa.", "El perro come carne."]


def test_a_hyphen_before_a_capital_is_kept_without_a_space(tmp_path: Path) -> None:
    ref = _ref(tmp_path, [["Vive en Castilla-", "La Mancha desde hace años."]])

    doc = pdf_source.load(ref, rules=get_profile("es").sentence_rules)

    assert [u.text for u in doc.units] == ["Vive en Castilla-La Mancha desde hace años."]


def test_a_scanned_pdf_reports_no_text_layer(tmp_path: Path) -> None:
    ref = _ref(tmp_path, [[], []], name="scan.pdf")

    with pytest.raises(SetupError) as excinfo:
        pdf_source.load(ref)

    assert str(excinfo.value) == (
        "'scan.pdf' has no text layer (a scanned PDF?). " "Anki Miner reads the text inside a PDF, not its images."
    )


def test_a_file_that_is_not_a_pdf_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "fake.pdf"
    path.write_bytes(b"hello, not a pdf")

    with pytest.raises(SetupError, match=r"^'fake\.pdf' is not a readable PDF\.$"):
        pdf_source.load(ReadingSourceRef(kind="pdf", path=path, title="fake"))


def _encrypted(tmp_path: Path, user_password: str) -> ReadingSourceRef:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(make_pdf([["秘密の本です。"]]))))
    writer.encrypt(user_password=user_password, owner_password="owner", algorithm="RC4-128")
    path = tmp_path / "locked.pdf"
    with path.open("wb") as handle:
        writer.write(handle)
    return ReadingSourceRef(kind="pdf", path=path, title="locked")


def test_a_password_protected_pdf_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SetupError, match=r"^'locked\.pdf' is password-protected and cannot be mined\.$"):
        pdf_source.load(_encrypted(tmp_path, "secret"))


def test_an_owner_only_lock_still_mines(tmp_path: Path) -> None:
    doc = pdf_source.load(_encrypted(tmp_path, ""))

    assert [u.text for u in doc.units] == ["秘密の本です。"]


def test_cancel_stops_the_load(tmp_path: Path) -> None:
    with pytest.raises(OperationCancelled, match="Reading load cancelled"):
        pdf_source.load(_ref(tmp_path, [["一文目。"]]), cancel_check=lambda: True)
