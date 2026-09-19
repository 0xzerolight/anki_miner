"""S22: a content face the machine lacks falls back to the profile's bundled face.

The machine's font database is faked through ``_script_families``; the bundled
stand-in is the real Noto Sans JP the app already ships (no test font needed).
"""

from __future__ import annotations

import logging
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QFontDatabase  # noqa: E402

from anki_miner.gui.utils import fonts as fonts_module  # noqa: E402
from anki_miner.gui.utils.fonts import (  # noqa: E402
    BUNDLED_JAPANESE_FILE,
    reset_font_cache,
    resolve_content_families,
)

FAMILIES = ("Zzz Naskh", "Yyy Sans")


@pytest.fixture(autouse=True)
def _fresh_cache(qapp):
    reset_font_cache()
    yield
    reset_font_cache()


def _installed(monkeypatch, names):
    """Fake the faces the database lists for any script; returns the probe log."""
    probes: list[str] = []

    def fake(writing_system: str) -> list[str]:
        probes.append(writing_system)
        return list(names)

    monkeypatch.setattr(fonts_module, "_script_families", fake)
    return probes


def _registrations(monkeypatch, family):
    calls: list[str] = []

    def fake(filename: str) -> str | None:
        calls.append(filename)
        return family

    monkeypatch.setattr(fonts_module, "_register_bundled_face", fake)
    return calls


class TestNoProbe:
    def test_an_empty_writing_system_returns_the_families_untouched(self, monkeypatch):
        probes = _installed(monkeypatch, [])
        assert resolve_content_families(FAMILIES, "", BUNDLED_JAPANESE_FILE) is FAMILIES
        assert probes == []

    def test_without_an_application_nothing_is_probed(self, monkeypatch):
        probes = _installed(monkeypatch, [])
        monkeypatch.setattr(fonts_module, "_has_gui", lambda: False)
        assert resolve_content_families(FAMILIES, "Arabic", BUNDLED_JAPANESE_FILE) is FAMILIES
        assert probes == []


class TestInstalledFaceWins:
    def test_an_installed_listed_face_keeps_the_list_and_never_registers(self, monkeypatch):
        _installed(monkeypatch, ["Other Face", "Yyy Sans"])
        calls = _registrations(monkeypatch, "Should Not Load")
        assert resolve_content_families(FAMILIES, "Arabic", "Face-Regular.ttf") == FAMILIES
        assert calls == []


class TestBundledFallback:
    def test_the_bundled_face_is_registered_and_put_first(self, monkeypatch):
        _installed(monkeypatch, [])
        resolved = resolve_content_families(FAMILIES, "Arabic", BUNDLED_JAPANESE_FILE)
        assert resolved[1:] == FAMILIES
        assert resolved[0] in set(QFontDatabase.families())

    def test_a_registered_family_already_listed_is_not_repeated(self, monkeypatch):
        _installed(monkeypatch, [])
        _registrations(monkeypatch, "Yyy Sans")
        assert resolve_content_families(FAMILIES, "Arabic", "Face-Regular.ttf") == ("Yyy Sans", "Zzz Naskh")

    def test_resolution_is_cached_per_family_set(self, monkeypatch):
        probes = _installed(monkeypatch, [])
        calls = _registrations(monkeypatch, "Registered Face")
        first = resolve_content_families(FAMILIES, "Arabic", "Face-Regular.ttf")
        assert resolve_content_families(FAMILIES, "Arabic", "Face-Regular.ttf") is first
        assert (probes, calls) == (["Arabic"], ["Face-Regular.ttf"])
        resolve_content_families(("Other",), "Arabic", "Face-Regular.ttf")
        assert probes == ["Arabic", "Arabic"]

    def test_reset_font_cache_forgets_the_resolution(self, monkeypatch):
        probes = _installed(monkeypatch, [])
        _registrations(monkeypatch, "Registered Face")
        resolve_content_families(FAMILIES, "Arabic", "Face-Regular.ttf")
        reset_font_cache()
        resolve_content_families(FAMILIES, "Arabic", "Face-Regular.ttf")
        assert probes == ["Arabic", "Arabic"]


class TestNothingAvailable:
    @staticmethod
    def _messages(caplog) -> list[str]:
        return [record.getMessage() for record in caplog.records if record.name == fonts_module.logger.name]

    def test_a_missing_bundled_face_logs_exactly_one_line(self, monkeypatch, caplog):
        _installed(monkeypatch, [])
        with caplog.at_level(logging.WARNING, logger=fonts_module.logger.name):
            assert resolve_content_families(FAMILIES, "Arabic", "Missing-Regular.ttf") == FAMILIES
            resolve_content_families(FAMILIES, "Arabic", "Missing-Regular.ttf")
        assert self._messages(caplog) == [
            "No installed Arabic font among Zzz Naskh, Yyy Sans and no bundled face registered "
            "(Missing-Regular.ttf); mined text may render as boxes"
        ]

    def test_a_corrupt_bundled_face_logs_exactly_one_line(self, monkeypatch, tmp_path, caplog):
        (tmp_path / "fonts").mkdir()
        (tmp_path / "fonts" / "Bad-Regular.ttf").write_bytes(b"not a font")
        monkeypatch.setattr(fonts_module, "get_resource_dir", lambda: tmp_path)
        _installed(monkeypatch, [])
        with caplog.at_level(logging.WARNING, logger=fonts_module.logger.name):
            assert resolve_content_families(FAMILIES, "Hebrew", "Bad-Regular.ttf") == FAMILIES
        assert len(self._messages(caplog)) == 1

    def test_no_declared_face_says_so(self, monkeypatch, caplog):
        _installed(monkeypatch, [])
        with caplog.at_level(logging.WARNING, logger=fonts_module.logger.name):
            assert resolve_content_families(FAMILIES, "TraditionalChinese", "") == FAMILIES
        assert self._messages(caplog) == [
            "No installed TraditionalChinese font among Zzz Naskh, Yyy Sans and no bundled face registered "
            "(none declared); mined text may render as boxes"
        ]


@pytest.mark.parametrize("name", ["Arabic", "Hebrew", "Thai", "Vietnamese", "Latin", "TraditionalChinese"])
def test_the_consumer_writing_systems_probe_the_real_database(name):
    assert isinstance(fonts_module._script_families(name), list)
