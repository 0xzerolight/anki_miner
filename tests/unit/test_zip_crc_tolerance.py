"""Yomitan imports must survive archives whose recorded crc-32 is wrong.

Reported against Settings → Pitch Accent Sources: "Corrupt zip file: Bad CRC-32
for file 'term_meta_bank_1.json'". Extracting the same zip and re-zipping its
contents imported fine, which means the payload was intact and only the recorded
checksum lied. Yomitan reads such archives without complaint (JSZip defaults to
``checkCRC32: false``), so a dictionary published that way works everywhere in
that ecosystem and hard-failed only here.

The bypass is deliberately narrow: only a crc-32 mismatch is tolerated, and only
after the strict read has already failed. Data that is actually wrong still has
to be rejected downstream — that is what :class:`TestRealDamageStillRejected`
pins.
"""

from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.dictionary.importers.yomitan_importer import (
    import_yomitan_zip,
    read_yomitan_title,
)
from anki_miner.services.pitch_accent.source_importer import import_pitch_source
from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip
from tests.fixtures.pitch.build_yomitan_pitch_fixture import build_yomitan_pitch_zip
from tests.fixtures.zip_corruption import corrupt_member_crc, replace_member_payload


class TestPitchSourceImport:
    """The reported failure: a pitch zip with a bad checksum on a meta bank."""

    def test_bad_crc_meta_bank_imports(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_pitch_zip(tmp_path / "pitch.zip")
        baseline = import_pitch_source(zip_path, tmp_path / "clean")

        corrupt_member_crc(zip_path, "term_meta_bank_1.json")
        result = import_pitch_source(zip_path, tmp_path / "patched")

        assert result.entry_count == baseline.entry_count
        assert result.source_name == baseline.source_name

    def test_bad_crc_index_json_imports(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_pitch_zip(tmp_path / "pitch.zip", title="チェックサム辞典")
        corrupt_member_crc(zip_path, "index.json")

        result = import_pitch_source(zip_path, tmp_path / "dest")

        assert result.source_name == "チェックサム辞典"

    def test_mismatch_is_logged(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        zip_path = build_yomitan_pitch_zip(tmp_path / "pitch.zip")
        corrupt_member_crc(zip_path, "term_meta_bank_1.json")

        with caplog.at_level(logging.WARNING):
            import_pitch_source(zip_path, tmp_path / "dest")

        warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("checksum" in m and "term_meta_bank_1.json" in m for m in warnings), warnings

    def test_clean_zip_logs_no_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        zip_path = build_yomitan_pitch_zip(tmp_path / "pitch.zip")

        with caplog.at_level(logging.WARNING):
            import_pitch_source(zip_path, tmp_path / "dest")

        assert not [r for r in caplog.records if "checksum" in r.getMessage()]


class TestDictionaryImport:
    """Same tolerance for the term/definition importer and its metadata probe."""

    def test_bad_crc_term_bank_imports(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_zip(tmp_path / "dict.zip")
        baseline = import_yomitan_zip(zip_path, tmp_path / "clean")

        corrupt_member_crc(zip_path, "term_bank_1.json")
        result = import_yomitan_zip(zip_path, tmp_path / "patched")

        assert result.entry_count == baseline.entry_count

    def test_read_yomitan_title_tolerates_bad_crc(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_zip(tmp_path / "dict.zip")
        expected = read_yomitan_title(zip_path)

        corrupt_member_crc(zip_path, "index.json")

        assert read_yomitan_title(zip_path) == expected


class TestRealDamageStillRejected:
    """The bypass must not become "accept anything"."""

    def test_swapped_payload_fails_downstream(self, tmp_path: Path) -> None:
        """Valid deflate, wrong bytes: crc is the only structural tell, and we
        gave it up — the JSON parse has to be the one that says no."""
        zip_path = build_yomitan_pitch_zip(tmp_path / "pitch.zip")
        replace_member_payload(zip_path, "term_meta_bank_1.json", b"not json at all")

        with pytest.raises(SetupError, match="term_meta_bank_1.json"):
            import_pitch_source(zip_path, tmp_path / "dest")

    def test_swapped_index_payload_fails(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_pitch_zip(tmp_path / "pitch.zip")
        replace_member_payload(zip_path, "index.json", json.dumps({"title": "x", "format": 1}).encode())

        with pytest.raises(SetupError, match="unsupported Yomitan format version"):
            import_pitch_source(zip_path, tmp_path / "dest")

    def test_shredded_archive_still_rejected(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "pitch.zip"
        zip_path.write_bytes(b"PK\x03\x04 this is not a zip file")

        with pytest.raises(SetupError, match="Corrupt zip file|not found"):
            import_pitch_source(zip_path, tmp_path / "dest")

    def test_truncated_central_directory_still_rejected(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_pitch_zip(tmp_path / "pitch.zip")
        zip_path.write_bytes(zip_path.read_bytes()[:-32])

        with pytest.raises(SetupError, match="Corrupt zip file"):
            import_pitch_source(zip_path, tmp_path / "dest")


class TestCorruptionHelpers:
    """The mutations themselves must produce the archive shapes they claim."""

    def test_corrupt_member_crc_is_rejected_by_stock_zipfile(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_pitch_zip(tmp_path / "pitch.zip")
        corrupt_member_crc(zip_path, "term_meta_bank_1.json")

        with zipfile.ZipFile(zip_path) as zf, pytest.raises(zipfile.BadZipFile, match="Bad CRC-32"):
            zf.read("term_meta_bank_1.json")

    def test_corrupt_member_crc_leaves_bytes_intact(self, tmp_path: Path) -> None:
        zip_path = build_yomitan_pitch_zip(tmp_path / "pitch.zip")
        with zipfile.ZipFile(zip_path) as zf:
            before = {name: zf.read(name) for name in zf.namelist()}

        corrupt_member_crc(zip_path, "term_meta_bank_1.json")

        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                info = zf.getinfo(name)
                info.CRC = None  # type: ignore[assignment]  # read past the check we invalidated
                with zf.open(info) as fp:
                    assert fp.read() == before[name]
