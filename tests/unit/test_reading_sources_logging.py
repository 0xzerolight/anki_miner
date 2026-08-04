"""Operational logging contracts for reading-source parsing and detection."""

from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path
from typing import Callable

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.models.reading import ReadingDocument, ReadingSourceRef
from anki_miner.services.reading import (
    aozora_source,
    detector,
    epub_source,
    mokuro_source,
    subtitle_source,
    text_source,
)

_PRIVATE_TEXT = "固有の秘密文章です。"
_CONTAINER = (
    '<?xml version="1.0"?>\n'
    '<container version="1.0" '
    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
    '  <rootfiles><rootfile full-path="OEBPS/content.opf" '
    'media-type="application/oebps-package+xml"/></rootfiles>\n'
    "</container>\n"
)


def _write_epub(path: Path, *, drm: bool = False) -> None:
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>試験本</dc:title></metadata>\n"
        '  <manifest><item id="c1" href="ch1.xhtml" '
        'media-type="application/xhtml+xml"/></manifest>\n'
        '  <spine><itemref idref="c1"/></spine>\n'
        "</package>\n"
    )
    xhtml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        f"<body><p>{_PRIVATE_TEXT}</p></body></html>"
    )
    encryption = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
        'xmlns:enc="http://www.w3.org/2001/04/xmlenc#">\n'
        "  <enc:EncryptedData>\n"
        '    <enc:EncryptionMethod Algorithm="urn:vendor:drm"/>\n'
        '    <enc:CipherData><enc:CipherReference URI="OEBPS/ch1.xhtml"/></enc:CipherData>\n'
        "  </enc:EncryptedData>\n"
        "</encryption>\n"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", _CONTAINER)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/ch1.xhtml", xhtml)
        if drm:
            zf.writestr("META-INF/encryption.xml", encryption)


def _source_case(
    private_dir: Path, format_: str
) -> tuple[Callable[[ReadingSourceRef], ReadingDocument], ReadingSourceRef, str, str, str]:
    if format_ == "aozora":
        path = private_dir / "novel.txt"
        path.write_text(_PRIVATE_TEXT, encoding="utf-8")
        ref = ReadingSourceRef(kind="txt", path=path, title=path.stem)
        return aozora_source.load, ref, "Aozora parse:", aozora_source.__name__, path.name
    if format_ == "epub":
        path = private_dir / "novel.epub"
        _write_epub(path)
        ref = ReadingSourceRef(kind="epub", path=path, title=path.stem)
        return epub_source.load, ref, "EPUB parse:", epub_source.__name__, path.name
    if format_ == "mokuro":
        path = private_dir / "volume.mokuro"
        path.write_text(
            json.dumps(
                {
                    "version": "0.2.4",
                    "title": "試験漫画",
                    "title_uuid": "title-id",
                    "volume": "1",
                    "volume_uuid": "volume-id",
                    "pages": [{"img_path": "001.jpg", "blocks": [{"lines": [_PRIVATE_TEXT]}]}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        ref = ReadingSourceRef(kind="mokuro", path=path, title="試験漫画", volume="1")
        return mokuro_source.load, ref, "Mokuro parse:", mokuro_source.__name__, path.name
    if format_ == "subtitle":
        path = private_dir / "episode.srt"
        path.write_text(f"1\n00:00:01,000 --> 00:00:03,000\n{_PRIVATE_TEXT}\n", encoding="utf-8")
        ref = ReadingSourceRef(kind="subtitle", path=path, title=path.stem)
        return subtitle_source.load, ref, "Subtitle parse:", subtitle_source.__name__, path.name

    ref = ReadingSourceRef(kind="text", title="Text", text=_PRIVATE_TEXT)
    return text_source.load, ref, "Text parse:", text_source.__name__, "Text"


@pytest.mark.parametrize("format_", ["aozora", "epub", "mokuro", "subtitle", "text"])
def test_each_source_parser_logs_summary(format_: str, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    private_dir = tmp_path / "private-parent"
    private_dir.mkdir()
    load, ref, prefix, module_name, _file_name = _source_case(private_dir, format_)

    with caplog.at_level(logging.INFO, logger="anki_miner.services.reading"):
        doc = load(ref)

    record = next(record for record in caplog.records if record.getMessage().startswith(prefix))
    assert f"units={len(doc.units)}" in record.getMessage()
    assert record.levelno == logging.INFO
    assert record.name == module_name


@pytest.mark.parametrize("format_", ["aozora", "epub", "mokuro", "subtitle", "text"])
def test_source_summary_hides_parent_and_text(format_: str, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    private_dir = tmp_path / "private-parent"
    private_dir.mkdir()
    load, ref, prefix, _module_name, file_name = _source_case(private_dir, format_)

    with caplog.at_level(logging.INFO, logger="anki_miner.services.reading"):
        load(ref)

    record = next(record for record in caplog.records if record.getMessage().startswith(prefix))
    assert f"file={file_name}" in record.getMessage()
    assert all(str(private_dir) not in item.getMessage() for item in caplog.records)
    assert all(_PRIVATE_TEXT not in item.getMessage() for item in caplog.records)


def test_drm_rejection_logs_warning_reason(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "protected.epub"
    _write_epub(path, drm=True)
    ref = ReadingSourceRef(kind="epub", path=path, title=path.stem)

    with caplog.at_level(logging.WARNING, logger="anki_miner.services.reading"), pytest.raises(SetupError):
        epub_source.load(ref)

    record = next(record for record in caplog.records if record.getMessage().startswith("EPUB rejected:"))
    assert "reason=unsupported_encryption" in record.getMessage()
    assert record.levelno == logging.WARNING
    assert record.name == epub_source.__name__


def test_detector_logs_format_and_reason(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "novel.txt"
    path.write_text(_PRIVATE_TEXT, encoding="utf-8")

    with caplog.at_level(logging.INFO, logger="anki_miner.services.reading"):
        detector.detect(path)

    record = next(record for record in caplog.records if record.getMessage().startswith("Reading detect:"))
    assert "format=txt" in record.getMessage()
    assert "reason=extension" in record.getMessage()
    assert record.levelno == logging.INFO
    assert record.name == detector.__name__

    caplog.clear()
    title_dir = tmp_path / "manga"
    title_dir.mkdir()
    (title_dir / "volume.mokuro").write_text(
        json.dumps(
            {
                "version": "0.2.4",
                "title": "試験漫画",
                "title_uuid": "title-id",
                "volume": "1",
                "volume_uuid": "volume-id",
                "pages": [],
            }
        ),
        encoding="utf-8",
    )
    (title_dir / "broken.cbz").write_bytes(b"not a zip")

    with caplog.at_level(logging.INFO, logger="anki_miner.services.reading"):
        detector.detect(title_dir)

    record = next(record for record in caplog.records if record.getMessage().startswith("Reading detect:"))
    assert "skipped_archives=1" in record.getMessage()
    assert all(not item.getMessage().startswith("Reading detect degraded:") for item in caplog.records)


def test_detector_failure_logs_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "unknown.pdf"

    with caplog.at_level(logging.WARNING, logger="anki_miner.services.reading"), pytest.raises(SetupError):
        detector.detect(path)

    record = next(record for record in caplog.records if record.getMessage().startswith("Reading detect failed:"))
    assert "found=extension:.pdf" in record.getMessage()
    assert record.levelno == logging.WARNING
    assert record.name == detector.__name__
