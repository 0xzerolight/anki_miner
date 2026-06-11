"""Tests for the first-launch JMdict→SQLite migration hook."""

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from anki_miner.gui.controllers.background_tasks import _needs_jmdict_migration


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_needs_migration_when_xml_present_and_no_sqlite(tmp_path: Path):
    xml = tmp_path / "JMdict_e"
    xml.write_text("<JMdict></JMdict>", encoding="utf-8")
    dicts_root = tmp_path / "dicts"

    assert _needs_jmdict_migration(xml, dicts_root) is True


def test_no_migration_when_sqlite_already_exists(tmp_path: Path):
    xml = tmp_path / "JMdict_e"
    xml.write_text("<JMdict></JMdict>", encoding="utf-8")
    dicts_root = tmp_path / "dicts"
    (dicts_root / "jmdict-english").mkdir(parents=True)
    (dicts_root / "jmdict-english" / "index.sqlite").write_bytes(b"")

    assert _needs_jmdict_migration(xml, dicts_root) is False


def test_no_migration_when_xml_missing(tmp_path: Path):
    assert _needs_jmdict_migration(tmp_path / "missing", tmp_path / "dicts") is False
